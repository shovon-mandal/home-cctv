#!/usr/bin/env python3
"""CCTV web dashboard server (stdlib only).

- Serves the dashboard (index.html)
- Proxies live-view websockets to the local go2rtc (which only listens on
  127.0.0.1, so camera passwords and the go2rtc admin API never leave the box)
- Lists and streams recordings (with HTTP Range for seeking)
"""
import json
import mimetypes
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, "/opt/cctv/lib")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import cctvconf  # noqa: E402
import cctvusers  # noqa: E402
import onvif  # noqa: E402
import cctvrec  # noqa: E402
import cctvscan  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.environ.get("CCTV_GEN_DIR", "/etc/cctv/generated")
RUN = os.environ.get("CCTV_RUN", "/run/cctv")
REC = os.environ.get("CCTV_REC", "/srv/cctv/rec")
GO2RTC = os.environ.get("GO2RTC_ADDR", "127.0.0.1:1984")

SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\-]+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

general, _ = cctvconf.load()
COOKIE = "cctv_session"
_fails = {}            # ip -> [timestamps] for login rate limiting
_user_fails = {}       # username -> [timestamps] of failed logins coming from the internet (Funnel)
APP_URL = general.get("app_url") or "https://github.com/shovon-mandal/home-cctv/releases/download/app-latest/HomeCCTV.apk"
DOCKER = os.environ.get("CCTV_SUPERVISOR") == "1"


def site_name():
    try:
        return cctvconf.load()[0].get("site_name") or "Home CCTV"
    except Exception:  # noqa: BLE001
        return "Home CCTV"


def mask_url(url):
    """rtsp://user:secret@host/... -> rtsp://user:***@host/... (for showing in the dashboard)."""
    return re.sub(r"^((?:ffmpeg:)?[a-z0-9+]+://[^:/@\s]*:)[^@/\s]*@", r"\1***@", url or "", flags=re.I)


def unmask_url(new, old):
    """Put the stored password back when the dashboard sends a masked URL."""
    if "***@" not in (new or ""):
        return new
    m = re.match(r"^(?:ffmpeg:)?[a-z0-9+]+://[^:/@\s]*:([^@/\s]*)@", old or "", re.I)
    return new.replace("***@", (m.group(1) if m else "") + "@", 1)


# ---------------- where recordings can be watched from ----------------
SD_BUF = os.environ.get("CCTV_SD_BUFFER", "/srv/cctv/sd-buffer")
SSD_DIR = os.environ.get("CCTV_SSD_DIR", "/mnt/cctv-ssd/CCTV")
DRIVE_CACHE = os.environ.get("CCTV_DRIVE_CACHE", "/srv/cctv/drive-cache")
DRIVE_CACHE_MAX = 2 * 1024 ** 3          # clips played from Drive are kept here (oldest removed)
_drive_ls, _drive_lock = {}, threading.Lock()
EXPORT_SEM = threading.Semaphore(1)      # one range download at a time (the board's disk is slow)


def local_sources():
    """[(id, folder)]: the SSD if mounted, the SD card buffer (Docker: the recordings folder)."""
    out = []
    if os.path.isdir(SSD_DIR) and os.path.ismount(os.path.dirname(SSD_DIR)):
        out.append(("ssd", os.path.realpath(SSD_DIR)))
    if os.path.isdir(SD_BUF):
        out.append(("sd", os.path.realpath(SD_BUF)))
    cur = os.path.realpath(REC)
    if not any(r == cur for _, r in out):
        out.append(("sd" if not any(i == "sd" for i, _ in out) else "rec", cur))
    return out


def default_source():
    """Where recording goes now (SSD when plugged in, else the SD card)."""
    cur = os.path.realpath(REC)
    srcs = local_sources()
    return next((i for i, r in srcs if r == cur), srcs[0][0])


def source_root(src):
    return dict(local_sources()).get(src)


def drive_ready():
    try:
        return "token" in open(RCLONE_CONF).read()
    except OSError:
        return False


def drive_path(cam, date=None, name=None):
    remote = cctvconf.load()[0].get("drive_remote", "gdrive:CCTV").rstrip("/")
    return "/".join([remote, cctvrec.folder_names().get(cam, cam)] + [x for x in (date, name) if x])


def drive_ls(path, dirs=False):
    """rclone listing of a Drive folder, cached for a minute."""
    key = (path, dirs)
    hit = _drive_ls.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    r = subprocess.run(["rclone", "--config", RCLONE_CONF, "lsjson", path, "--dirs-only" if dirs else "--files-only",
                        "--no-mimetype"], capture_output=True, text=True, timeout=45)
    if r.returncode != 0 and "not found" not in (r.stderr or "").lower():
        raise ValueError("Google Drive did not answer, try again")
    try:
        items = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except ValueError:
        items = []
    _drive_ls[key] = (time.time(), items)
    return items


_fails_lock = threading.Lock()


def cameras():
    try:
        with open(os.path.join(GEN, "cameras.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def probe(url):
    """Check an RTSP URL with ffprobe. Returns {ok, codec, width, height} or {ok: False, error}."""
    if url.startswith("ffmpeg:"):                    # go2rtc source: let FFmpeg pick the transport
        url, net = url[7:].split("#", 1)[0], []
    else:
        url = url.split("#", 1)[0]
        net = ["-rtsp_transport", "tcp"] if url.lower().startswith(("rtsp://", "rtsps://")) else []
    if url.lower().startswith("rtspx://"):
        url = "rtsps://" + url[8:]
    cmd = ["ffprobe", "-v", "error", *net, "-timeout", "6000000",
           "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "json", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return {"ok": False, "error": "ffprobe is not installed (install FFmpeg)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timed out: camera not answering (check IP/port)"}
    try:
        st = json.loads(r.stdout or "{}").get("streams", [])
    except ValueError:
        st = []
    if r.returncode == 0 and st:
        return {"ok": True, "codec": st[0].get("codec_name"), "width": st[0].get("width"), "height": st[0].get("height")}
    err = (r.stderr or "").strip().splitlines()
    msg = err[-1] if err else "No stream found"
    msg = re.sub(r"rtsp://\S+: ", "", msg)                   # drop the URL (it contains the password)
    msg = re.sub(r"//[^@/\s]+@", "//***@", msg)
    if "401" in msg or "Unauthorized" in msg:
        msg = "Wrong username/password"
    elif "404" in msg or "Not Found" in msg:
        msg = "Wrong path (camera does not know this stream)"
    elif "Connection refused" in msg:
        msg = "Port closed (is RTSP/ONVIF on?)"
    elif "Invalid data found" in msg:
        msg = ("Camera answers but the video can't be read. On Imou/Dahua/Ezviz turn off "
               "'Video/Stream Encryption' in the camera app")
    elif "No route" in msg or "timed out" in msg.lower() or "Network is unreachable" in msg:
        msg = "Camera not reachable (is the IP right?)"
    return {"ok": False, "error": msg[:200]}


# ---------------- Google Drive connect (rclone authorize, no API keys) ----------------
RCLONE_CONF = os.environ.get("RCLONE_CONF", "/etc/cctv/rclone.conf")
_drive = {"proc": None, "out": [], "state": None, "lock": threading.Lock()}
SCOPE_BLOB = "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"     # base64url({"scope":"drive.file"}): only files rclone creates


def drive_start():
    import urllib.request
    with _drive["lock"]:
        p = _drive["proc"]
        if p and p.poll() is None:
            p.kill()
        _drive["out"] = []
        p = subprocess.Popen(["rclone", "authorize", "drive", SCOPE_BLOB, "--auth-no-open-browser"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=dict(os.environ, HOME=os.environ.get("HOME", "/tmp")))
        _drive["proc"] = p

    def reader():
        for line in p.stdout:
            _drive["out"].append(line)
    threading.Thread(target=reader, daemon=True).start()

    local = None
    for _ in range(100):
        m = re.search(r"http://127\.0\.0\.1:53682/auth\?state=([\w-]+)", "".join(_drive["out"]))
        if m:
            local, _drive["state"] = m.group(0), m.group(1)
            break
        if p.poll() is not None:
            break
        time.sleep(0.1)
    if not local:
        raise ValueError("rclone did not start: " + "".join(_drive["out"])[-200:])

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        urllib.request.build_opener(NoRedirect).open(local, timeout=5)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        if loc and loc.startswith("https://accounts.google.com/"):
            return loc
    raise ValueError("Could not get the Google login link")


def drive_finish(pasted):
    import base64
    import urllib.request
    from urllib.parse import urlencode
    q = parse_qs(urlparse(pasted.strip()).query)
    code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    if not code:
        raise ValueError("The link has no 'code='. After Allow, copy the whole link from the address bar")
    p = _drive["proc"]
    if not p or p.poll() is not None or state != _drive["state"]:
        raise ValueError("Timed out. Press 'Connect with Google' again")
    params = {"state": state, "code": code}
    if q.get("scope"):
        params["scope"] = q["scope"][0]
    try:
        urllib.request.urlopen("http://127.0.0.1:53682/?" + urlencode(params), timeout=20).read()
    except Exception:  # noqa: BLE001  (rclone answers then exits)
        pass
    for _ in range(200):
        if p.poll() is not None:
            break
        time.sleep(0.1)
    out = "".join(_drive["out"])
    m = re.search(r"--->\s*(.+?)\s*<---End paste", out, re.S)
    if not m:
        if "failed to get token" in out:
            raise ValueError("Google did not give a token (link expired or no internet). Press 'Connect with Google' again")
        raise ValueError("No token received: " + out.strip().splitlines()[-1][-200:])
    blob = m.group(1).strip()
    token = blob
    if not blob.startswith("{"):
        d = json.loads(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
        token = d.get("token", "")
    json.loads(token)                       # must be valid JSON
    tmp = RCLONE_CONF + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"[gdrive]\ntype = drive\nscope = drive.file\ntoken = {token}\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, RCLONE_CONF)
    return drive_status(check=True)


def drive_status(check=False):
    configured = os.path.exists(RCLONE_CONF) and "token" in open(RCLONE_CONF).read()
    g, _ = cctvconf.load()
    res = {"configured": configured, "remote": g.get("drive_remote"), "max_gb": g.get("drive_max_gb")}
    if configured and check:
        base = ["rclone", "--config", RCLONE_CONF]
        r = subprocess.run(base + ["mkdir", g.get("drive_remote", "gdrive:CCTV")], capture_output=True, text=True, timeout=60)
        res["ok"] = r.returncode == 0
        if r.returncode != 0:
            res["error"] = (r.stderr or "").strip().splitlines()[-1:] or ["unknown"]
        a = subprocess.run(base + ["about", "gdrive:", "--json"], capture_output=True, text=True, timeout=60)
        try:
            res["about"] = json.loads(a.stdout)
        except ValueError:
            pass
    return res


SD_BUF = os.environ.get("CCTV_SD_BUF", "/srv/cctv/sd-buffer")
GB = 1024 ** 3


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def cpu_temp():
    """Hottest thermal zone in °C (the board's CPU), or None (e.g. inside a VM)."""
    import glob
    best = None
    for f in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            v = int(open(f).read().strip()) / 1000
        except (OSError, ValueError):
            continue
        if 0 < v < 150 and (best is None or v > best):
            best = v
    return round(best, 1) if best is not None else None


def limits_info():
    g, _ = cctvconf.load()
    st = os.statvfs(SD_BUF if os.path.isdir(SD_BUF) else "/")
    total, avail = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    used_by_buf = _dir_size(SD_BUF) if os.path.isdir(SD_BUF) else 0
    keep_free = float(g["sd_min_free_gb"])
    max_buf = max(1.0, (avail + used_by_buf) / GB - keep_free)       # what the buffer could grow to
    return {"sd_buffer_gb": float(g["sd_buffer_gb"]), "ssd_max_gb": float(g["ssd_max_gb"]),
            "drive_max_gb": float(g["drive_max_gb"]), "drive_keep_days": float(g["drive_keep_days"]),
            "sd_total_gb": round(total / GB, 1), "sd_free_gb": round(avail / GB, 1),
            "sd_buffer_used_gb": round(used_by_buf / GB, 2), "sd_buffer_max_gb": round(max_buf, 1),
            "sd_keep_free_gb": keep_free}


def limits_save(body):
    info = limits_info()
    rules = {  # key: (min, max)
        "sd_buffer_gb": (1, info["sd_buffer_max_gb"]),
        "ssd_max_gb": (1, 100000),
        "drive_max_gb": (0.5, 100000),
        "drive_keep_days": (1, 365),
    }
    upd = {}
    for k, (lo, hi) in rules.items():
        if k not in body or body[k] in ("", None):
            continue
        try:
            v = float(body[k])
        except (TypeError, ValueError):
            raise ValueError(f"{k}: not a number") from None
        if not lo <= v <= hi:
            raise ValueError(f"{k} must be between {lo:g} and {hi:g}")
        upd[k] = f"{v:g}"
    if upd:
        cctvconf.set_general(upd)
    return limits_info()


def share_info(host_header=""):
    """Addresses a new user needs: home Wi-Fi URL, internet URL, app download."""
    lan = ""
    if DOCKER:          # inside Docker our own IP is the container's: use the address the admin typed
        h = host_header.split(":")[0]
        if h and h not in ("localhost", "127.0.0.1", "[::1]"):
            return {"lan_url": f"http://{host_header}", "public_url": _public_url(), "app_url": APP_URL}
        return {"lan_url": "", "public_url": _public_url(), "app_url": APP_URL}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))        # no packet is sent; just picks the LAN interface
        lan = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    port = general.get("web_port", "8085")
    return {"lan_url": f"http://{lan}:{port}" if lan else "", "public_url": _public_url(), "app_url": APP_URL}


def _public_url():
    """Internet address, if one is set up (cctv-funnel writes it; or put any URL in this file)."""
    try:
        return open(os.path.join(os.path.dirname(cctvconf.CONF_PATH), "public_url")).read().strip()
    except OSError:
        return ""


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (dst, src):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = "cctv"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("CCTV_WEB_DEBUG"):
            super().log_message(fmt, *args)

    # ---------- helpers ----------
    def current_user(self):
        """(name, user) from the session cookie, or None."""
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and v:
                return cctvusers.check_token(v)
        return None

    def require(self, admin=False):
        who = self.current_user()
        if not who:
            self.send_error_json(401, "Please log in")
            return None
        if admin and who[1]["role"] != "admin":
            self.send_error_json(403, "Admins only")
            return None
        return who

    def my_cameras(self, u):
        return [c for c in cameras() if cctvusers.can_see(u, c["id"])]

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 16384:
            raise ValueError("too large")
        if self.headers.get("X-Requested-With") != "cctv":   # blocks cross-site form posts
            raise PermissionError
        return json.loads(self.rfile.read(n) or b"{}")

    def end_headers(self):
        # no framing (clickjacking), no MIME sniffing, no leaking URLs to other sites
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def same_origin(self):
        """Requests from another website (a browser sends its Origin) are refused."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    def send_json(self, obj, code=200, headers=()):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def lang(self):
        lg = self.headers.get("X-Lang", "")
        return lg if lg in ("en", "bn") else "en"

    def tr(self, msg):
        msg = re.sub(r"^\[cam\d+\] ", "", str(msg))
        parts = msg.split(" || ", 1)
        return parts[0] if len(parts) == 1 else (parts[1] if self.lang() == "bn" else parts[0])

    def send_error_json(self, code, msg):
        self.send_json({"error": self.tr(msg)}, code)

    # ---------- routing ----------
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                return self.serve_file(os.path.join(HERE, "index.html"), "text/html; charset=utf-8")
            if p == "/lang/bn.js":
                return self.serve_file(os.path.join(HERE, "lang", "bn.js"), "text/javascript; charset=utf-8")
            if p == "/icon.svg":
                return self.serve_file(os.path.join(HERE, "icon.svg"), "image/svg+xml")
            if p == "/manifest.webmanifest":
                return self.serve_file(os.path.join(HERE, "manifest.webmanifest"), "application/manifest+json")
            if p == "/api/me":
                return self.me()
            who = self.require()
            if not who:
                return
            name, user = who
            if p == "/api/ws" and not self.same_origin():
                return self.send_error_json(403, "bad origin")
            if p == "/api/ws":
                return self.ws_proxy(q, user)
            if p == "/api/cameras":
                return self.send_json(self.my_cameras(user))
            if p == "/api/motion":
                root = self.rec_root()
                return self.send_json({"live": [c["id"] for c in self.my_cameras(user)
                                                if cctvrec.motion_active(root, c["id"])]})
            if p == "/api/timeline":
                return self.timeline(q, user)
            if p == "/api/status":
                return self.status(user)
            if p == "/api/storage":
                if not cctvusers.has_perm(user, "status"):
                    return self.send_error_json(403, "No permission")
                return self.send_json({"storage": read_json(os.path.join(RUN, "storage.json"), {}),
                                       "eject": read_json(os.path.join(RUN, "eject.json"), {})})
            if p == "/api/recordings":
                return self.recordings(q, user)
            if p.startswith("/rec/"):
                return self.serve_recording(unquote(p[5:]), user, (q.get("src") or [None])[0])
            if p.startswith("/drive/"):
                return self.serve_drive(unquote(p[7:]), user)
            if p == "/api/export":
                return self.export_range(q, user)
            if p == "/api/admin/cameras":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                return self.admin_cameras()
            if p == "/api/admin/update":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                d = read_json(os.path.join(RUN, "update.json"), {})
                d["public_url"] = _public_url()
                d["auto_update"] = cctvconf.yes(cctvconf.load()[0].get("auto_update", "yes"))
                return self.send_json(d)
            if p == "/api/admin/drive":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                return self.send_json(drive_status(check="check" in q))
            if p == "/api/admin/share":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                return self.send_json(share_info(self.headers.get("Host", "")))
            if p == "/api/admin/picture":
                cam = (q.get("cam") or [""])[0]
                if not (cctvusers.has_perm(user, "camset") and cctvusers.can_see(user, cam)):
                    return self.send_error_json(403, "No permission")
                return self.picture(q, None)
            if p == "/api/admin/limits":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                return self.send_json(limits_info())
            if p == "/api/admin/scan-subnet":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                return self.send_json({"subnet": "" if DOCKER else cctvscan.local_subnet(), "docker": DOCKER})
            if p == "/api/admin/apply":
                return self.send_json(read_json(os.path.join(RUN, "apply.json"), {}))
            if p == "/api/users":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only")
                data = cctvusers.load()
                return self.send_json([cctvusers.public_view(n, x) for n, x in sorted(data["users"].items())])
            self.send_error_json(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_HEAD = do_GET


    def do_POST(self):
        p = urlparse(self.path).path
        if not self.same_origin():
            return self.send_error_json(403, "bad request origin")
        try:
            body = self.read_body()
        except PermissionError:
            return self.send_error_json(403, "bad request origin")
        except (ValueError, TypeError):
            return self.send_error_json(400, "bad json")
        try:
            if p == "/api/login":
                return self.login(body)
            if p == "/api/setup":
                return self.first_admin(body)
            if p == "/api/logout":
                return self.send_json({"ok": True}, headers=[("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")])
            if p == "/api/password":                     # any user: change own password
                who = self.require()
                if not who:
                    return
                name, u = who
                if not cctvusers.check_password(str(body.get("old", "")), u.get("hash", "")):
                    return self.send_error_json(400, "Current password is wrong")
                data = cctvusers.load()
                cctvusers.set_user(data, name, str(body.get("new", "")), u["role"], u.get("cameras"), u.get("recordings"), u.get("ptz"))
                cctvusers.save(data)
                tok = cctvusers.make_token(name, data["users"][name])
                return self.send_json({"ok": True}, headers=[("Set-Cookie", self.cookie(tok))])
            if p == "/api/prefs":
                who = self.require()
                if not who:
                    return
                data = cctvusers.load()
                cctvusers.set_lang(data, who[0], str(body.get("lang", "en")))
                cctvusers.save(data)
                return self.send_json({"ok": True})
            if p == "/api/ptz":
                who = self.require()
                if not who:
                    return
                return self.ptz(body, who[1])
            if p in ("/api/admin/picture", "/api/admin/cameras/motion", "/api/recordings/delete"):
                who = self.require()                    # viewers may have been given these
                if not who:
                    return
                key = "delete" if p.endswith("/delete") else "camset"
                cam = str(body.get("cam") or body.get("id") or "")
                if not (cctvusers.has_perm(who[1], key) and cctvusers.can_see(who[1], cam)):
                    return self.send_error_json(403, "No permission")
                if p == "/api/admin/picture":
                    return self.picture(None, body)
                if p == "/api/admin/cameras/motion":
                    return self.cam_motion(body)
                return self.delete_recording(body)
            who = self.require(admin=True)
            if not who:
                return
            if p == "/api/users/save":
                data = cctvusers.load()
                name = str(body.get("name", "")).strip()
                cams = body.get("cameras", "*")
                if cams != "*":
                    valid = {c["id"] for c in cameras()}
                    cams = [c for c in cams if c in valid]
                cctvusers.set_user(data, name, body.get("password") or None, body.get("role", "viewer"),
                                   cams, bool(body.get("recordings", True)), bool(body.get("ptz", False)),
                                   {k: bool(body.get(k)) for k in cctvusers.EXTRA_PERMS})
                cctvusers.save(data)
                return self.send_json({"ok": True})
            if p == "/api/users/delete":
                data = cctvusers.load()
                cctvusers.delete_user(data, str(body.get("name", "")))
                cctvusers.save(data)
                return self.send_json({"ok": True})
            if p in ("/api/admin/ssd/eject", "/api/admin/ssd/attach"):
                name = "eject-request" if p.endswith("eject") else "attach-request"
                with open(os.path.join(RUN, name), "w") as f:            # watched by cctv-ssd-*.path
                    f.write(str(time.time()))
                return self.send_json({"ok": True, "requested": time.time()})
            if p == "/api/admin/update":
                with open(os.path.join(RUN, "update-request"), "w") as f:   # watched by cctv-update.path
                    f.write(str(time.time()))
                return self.send_json({"ok": True, "requested": time.time()})
            if p == "/api/admin/drive/start":
                return self.send_json({"url": drive_start()})
            if p == "/api/admin/drive/finish":
                return self.send_json(drive_finish(str(body.get("url", ""))))
            if p == "/api/admin/drive/disconnect":
                if os.path.exists(RCLONE_CONF):
                    open(RCLONE_CONF, "w").close()
                return self.send_json(drive_status())
            if p == "/api/admin/limits":
                return self.send_json(limits_save(body))
            if p == "/api/admin/picture":
                return self.picture(None, body)
            if p == "/api/admin/cameras/save":
                return self.cam_save(body)
            if p == "/api/admin/cameras/motion":
                return self.cam_motion(body)
            if p == "/api/admin/cameras/delete":
                return self.cam_delete(body)
            if p == "/api/admin/cameras/test":
                return self.cam_test(body)
            if p == "/api/admin/cameras/onvif":
                return self.cam_onvif(body)
            if p == "/api/admin/cameras/scan":
                return self.send_json(cctvscan.scan(str(body.get("subnet") or "") or None))
            if p == "/api/admin/autoupdate":
                on = bool(body.get("on"))
                cctvconf.set_general({"auto_update": "yes" if on else "no"})
                return self.send_json({"ok": True, "auto_update": on})
            if p == "/api/admin/site":
                name = re.sub(r"\s+", " ", str(body.get("site_name", ""))).strip()[:40]
                if not name:
                    raise ValueError("Enter a name")
                cctvconf.set_general({"site_name": name})
                return self.send_json({"ok": True, "site_name": name})
            if p == "/api/recordings/delete":
                return self.delete_recording(body)
            self.send_error_json(404, "not found")
        except ValueError as e:
            self.send_error_json(400, str(e))

    def via_proxy(self):
        """True when the request came through Tailscale Funnel/serve (a local reverse proxy)."""
        return self.client_address[0] in ("127.0.0.1", "::1") and bool(self.headers.get("X-Forwarded-For"))

    def client_ip(self):
        if self.via_proxy():
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def cookie(self, tok):
        secure = "; Secure" if self.via_proxy() or self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"{COOKIE}={tok}; Path=/; Max-Age={cctvusers.SESSION_DAYS * 86400}; HttpOnly; SameSite=Strict{secure}"

    def me(self):
        data = cctvusers.load()
        who = self.current_user()
        if not who:
            return self.send_json({"error": "Please log in", "needs_setup": not data["users"],
                                   "site_name": site_name()}, 401)
        return self.send_json(dict(cctvusers.public_view(*who), site_name=site_name(), docker=DOCKER))

    def first_admin(self, body):
        """First start: create the first admin account from the dashboard (only while there is none)."""
        import ipaddress
        data = cctvusers.load()
        if data["users"]:
            return self.send_error_json(403, "An account already exists. Please log in.")
        try:
            local = ipaddress.ip_address(self.client_address[0]).is_private and not self.via_proxy()
        except ValueError:
            local = False
        if not local:
            return self.send_error_json(403, "Create the first account from your home network")
        name = str(body.get("user", "")).strip()
        cctvusers.set_user(data, name, str(body.get("pass", "")), "admin", "*", True, True)
        cctvusers.save(data)
        u = data["users"][name]
        self.send_json(cctvusers.public_view(name, u), headers=[("Set-Cookie", self.cookie(cctvusers.make_token(name, u)))])

    def login(self, body):
        ip = self.client_ip()
        now = time.time()
        with _fails_lock:
            recent = [t for t in _fails.get(ip, []) if now - t < 600]
            _fails[ip] = recent
            if len(recent) >= 5:
                return self.send_error_json(429, "Too many attempts, try again in 10 minutes")
        name = str(body.get("user", "")).strip()
        remote = self.via_proxy()               # came from the internet (Tailscale Funnel)
        if remote:
            with _fails_lock:
                uf = [t for t in _user_fails.get(name, []) if now - t < 900]
                _user_fails[name] = uf
                if len(uf) >= 10:              # someone is guessing this account: block it from outside
                    return self.send_error_json(429, "This account is locked from the internet for 15 minutes (too many wrong passwords). It still works at home.")
        u = cctvusers.load()["users"].get(name)
        if not u or not cctvusers.check_password(str(body.get("pass", "")), u.get("hash", "")):
            with _fails_lock:
                _fails.setdefault(ip, []).append(now)
                if remote:
                    _user_fails.setdefault(name, []).append(now)
            time.sleep(1)
            return self.send_error_json(401, "Wrong username or password")
        with _fails_lock:
            _fails.pop(ip, None)
        self.send_json(cctvusers.public_view(name, u), headers=[("Set-Cookie", self.cookie(cctvusers.make_token(name, u)))])

    def delete_recording(self, body):
        cam, date, fname = (str(body.get(k, "")) for k in ("cam", "date", "name"))
        sub, _, base = fname.rpartition("/")
        if not (SAFE_NAME.match(cam) and DATE_RE.match(date) and SAFE_NAME.match(base) and sub in ("", cctvrec.LOW)):
            return self.send_error_json(400, "bad path")
        if body.get("src") == "drive":
            return self.send_error_json(400, "Clips on Google Drive are removed by the Drive clean-up")
        root = self.rec_root(body.get("src") or None)
        folder = os.path.join(cctvrec.cam_dir(root, cam), date, *([sub] if sub else []))
        fp = os.path.realpath(os.path.join(folder, base))
        if not fp.startswith(root + os.sep) or not os.path.isfile(fp):
            return self.send_error_json(404, "not found")
        os.remove(fp)
        if base.endswith(".mp4"):
            try:
                os.remove(fp[:-4] + ".jpg")
            except OSError:
                pass
            idx = cctvrec.merged_index(folder)
            if idx.pop(base, None) is not None:
                cctvrec.save_merged_index(folder, idx)
        self.send_json({"ok": True})


    # ---------- camera management (admin) ----------
    def admin_cameras(self):
        cams = []
        for c in cctvconf.load_cameras():
            d = {k: c.get(k, "") for k in cctvconf.CAM_FIELDS if k != "password"}
            d.update(id=c["id"], has_password=bool(c.get("password")),
                     custom_url=bool(c.get("main")), enabled=c["enabled"], ptz=c["ptz"],
                     main=mask_url(c.get("main", "")), sub=mask_url(c.get("sub", "")))
            d["recording"] = self.rec_status(c)
            cams.append(d)
        presets = {k: {kk: vv for kk, vv in v.items()} for k, v in cctvconf.PRESETS.items()}
        self.send_json({"cameras": cams, "presets": presets})

    def rec_status(self, c):
        if not c["enabled"] or c["mode"] == "off":
            return {"state": "off"}
        st = read_json(os.path.join(RUN, "recorder.json"), {}).get(c["id"], {})
        date = time.strftime("%Y-%m-%d")
        segs = cctvrec.segments(os.path.join(cctvrec.cam_dir(self.rec_root(), c["id"]), date), date)
        age = time.time() - segs[-1]["mtime"] if segs else None
        state = "ok" if age is not None and age < 90 else ("starting" if st.get("recording") else "error")
        return {"state": state, "error": st.get("error", ""), "codec": st.get("codec"), "audio": st.get("audio"),
                "last_write_age": int(age) if age is not None else None}

    def _cam_from_body(self, body, existing):
        """Merge the form into the existing camera; empty password = keep the old one."""
        raw = {k: v for k, v in (existing or {}).items() if k in cctvconf.CAM_FIELDS + ["main", "sub"]}
        for k in cctvconf.CAM_FIELDS:
            if k in body and k != "password":
                v = body[k]
                if k == "ptz":
                    v = bool(v)
                raw[k] = ("yes" if v else "no") if isinstance(v, bool) else str(v).strip()
        if body.get("password"):
            raw["password"] = str(body["password"])
        if body.get("clear_password"):
            raw["password"] = ""
        if raw.get("type") == "url":
            # complete URLs; host/user/password are taken from it for ONVIF (PTZ, picture settings)
            from urllib.parse import unquote as uq
            for k in ("main", "sub"):
                raw[k] = unmask_url(str(body.get(k, "")).strip(), (existing or {}).get(k, ""))
            if not raw["sub"]:
                raw.pop("sub")
            u = urlparse(raw["main"].split("#", 1)[0].removeprefix("ffmpeg:"))
            raw.update(host=u.hostname or "", port=str(u.port or ""), user=uq(u.username or ""),
                       password=uq(u.password or ""), main_path="", sub_path="")
        else:
            # brand presets use host/path fields
            raw.pop("main", None)
            raw.pop("sub", None)
        return raw

    def _cams_by_id(self):
        cams = cctvconf.load_cameras()
        return cams, {c["id"]: c for c in cams}

    def _request_apply(self):
        with open(os.path.join(RUN, "apply-request"), "w") as f:   # watched by cctv-apply.path
            f.write(str(time.time()))

    def cam_save(self, body):
        cams, by_id = self._cams_by_id()
        cid = str(body.get("id") or "")
        if cid and cid not in by_id:
            return self.send_error_json(404, "No such camera")
        if not cid:
            used = {int(c["id"][3:]) for c in cams}
            n = next(i for i in range(1, 100) if i not in used)
            cid = f"cam{n}"
        raw = self._cam_from_body(body, by_id.get(cid))
        if not raw.get("name"):
            raise ValueError("Give the camera a name")
        c = cctvconf.normalize_cam(cid, raw)            # validates
        new = [x for x in cams if x["id"] != cid] + [dict(raw, id=cid)]
        cctvconf.save_cameras(new)
        self._request_apply()
        self.send_json({"ok": True, "id": cid, "name": c["name"], "requested": time.time()})

    def cam_motion(self, body):
        """Change only the motion threshold of one camera."""
        cams, by_id = self._cams_by_id()
        cid = str(body.get("id") or "")
        if cid not in by_id:
            return self.send_error_json(404, "No such camera")
        try:
            thr = int(body.get("threshold"))
        except (TypeError, ValueError):
            thr = 0
        if not 20 <= thr <= 50000:
            raise ValueError("Sensitivity out of range")
        cctvconf.save_cameras([dict(x, threshold=str(thr)) if x["id"] == cid else x for x in cams])
        self._request_apply()
        self.send_json({"ok": True, "requested": time.time()})

    def cam_delete(self, body):
        cams, by_id = self._cams_by_id()
        cid = str(body.get("id", ""))
        if cid not in by_id:
            return self.send_error_json(404, "No such camera")
        cctvconf.save_cameras([c for c in cams if c["id"] != cid])
        self._request_apply()
        self.send_json({"ok": True, "requested": time.time()})

    def cam_test(self, body):
        _, by_id = self._cams_by_id()
        raw = self._cam_from_body(body, by_id.get(str(body.get("id") or "")))
        try:
            c = cctvconf.normalize_cam("cam99", dict(raw, enabled="yes"))
        except ValueError as e:
            return self.send_error_json(400, str(e).replace("[cam99] ", ""))
        out = {}
        for which in ("main", "sub"):
            url = c[f"{which}_url"]
            if which == "sub" and url == c["main_url"]:
                out["sub"] = dict(out["main"], same=True)
                break
            out[which] = probe(url)
            if out[which].get("error"):
                out[which]["error"] = self.tr(out[which]["error"])
        self.send_json(out)

    def cam_onvif(self, body):
        _, by_id = self._cams_by_id()
        old = by_id.get(str(body.get("id") or "")) or {}
        host = str(body.get("host", "")).strip()
        if not cctvconf.HOST_RE.match(host):
            return self.send_error_json(400, "Invalid IP")
        user = str(body.get("user", old.get("user", "")))
        pw = str(body.get("password") or old.get("password", ""))
        ports = [int(body["onvif_port"])] if str(body.get("onvif_port", "")).isdigit() else None
        try:
            res = onvif.find_streams(host, user, pw, ports)
        except onvif.OnvifError as e:
            return self.send_error_json(400, str(e))
        self.send_json(res)

    # ---------- picture settings (night mode, HDR, resolution) ----------
    def picture(self, q, body):
        cid = str((q.get("cam") or [""])[0] if q is not None else body.get("cam", ""))
        c = {x["id"]: x for x in cctvconf.load_cameras()}.get(cid)
        if not c:
            return self.send_error_json(404, "No such camera")
        port = c.get("onvif_port") or None
        try:
            if body is None:
                return self.send_json(onvif.get_picture(c["host"], c["user"], c["password"], port))
            num = lambda k: float(body[k]) if body.get(k) not in (None, "") else None
            wdr = body.get("wdr") if body.get("wdr") in ("ON", "OFF") else None
            w = h = None
            if body.get("resolution"):
                w, h = (int(v) for v in str(body["resolution"]).lower().split("x"))
            br = int(body["bitrate"]) if str(body.get("bitrate", "")).isdigit() else None
            onvif.set_picture(c["host"], c["user"], c["password"], port, wdr=wdr,
                              brightness=num("brightness"), saturation=num("saturation"), width=w, height=h, bitrate=br)
            return self.send_json(onvif.get_picture(c["host"], c["user"], c["password"], port))
        except onvif.OnvifError as e:
            return self.send_error_json(400, str(e))
        except (OSError, ValueError):
            return self.send_error_json(502, "Camera did not answer")

    # ---------- PTZ ----------
    def ptz(self, body, user):
        if not (user["role"] == "admin" or user.get("ptz")):
            return self.send_error_json(403, "You are not allowed to move cameras")
        cid = str(body.get("cam", ""))
        if not cctvusers.can_see(user, cid):
            return self.send_error_json(403, "No permission")
        c = {x["id"]: x for x in cctvconf.load_cameras()}.get(cid)
        if not c or not c["ptz"]:
            return self.send_error_json(400, "PTZ is off for this camera")
        port = c.get("onvif_port") or None
        try:
            if body.get("stop"):
                onvif.ptz_stop(c["host"], c["user"], c["password"], port)
            else:
                clamp = lambda v: max(-1.0, min(1.0, float(v or 0)))
                onvif.ptz_move(c["host"], c["user"], c["password"],
                               clamp(body.get("x")), clamp(body.get("y")), clamp(body.get("z")), port)
        except onvif.OnvifError as e:
            return self.send_error_json(400, str(e))
        except OSError:
            return self.send_error_json(502, "Camera did not answer")
        self.send_json({"ok": True})

    # ---------- timeline ----------
    def timeline(self, q, user):
        if not user.get("recordings"):
            return self.send_error_json(403, "No permission for recordings")
        cam = (q.get("cam") or [""])[0]
        date = (q.get("date") or [""])[0]
        if not cctvusers.can_see(user, cam) or cam not in {c["id"] for c in cameras()}:
            return self.send_error_json(403, "No permission")
        sources = [i for i, _ in local_sources()] + (["drive"] if drive_ready() else [])
        src = (q.get("src") or [""])[0]
        if src not in sources:
            src = default_source()
        now = time.time()
        local = self.rec_root(None if src == "drive" else src)
        if src == "drive":
            days = sorted((x["Name"] for x in drive_ls(drive_path(cam), dirs=True) if DATE_RE.match(x.get("Name", ""))),
                          reverse=True)
        else:
            days = cctvrec.dates_of(local, cam)
        if not DATE_RE.match(date):
            date = days[0] if days else time.strftime("%Y-%m-%d")
        d = os.path.join(cctvrec.cam_dir(local, cam), date)
        base, qs = f"/rec/{cam}/{date}/", f"?src={src}"
        segs = []
        if src == "drive":
            items = [x for x in drive_ls(drive_path(cam, date)) if cctvrec.SEG_RE.match(x.get("Name", ""))]
            items.sort(key=lambda x: x["Name"])
            starts = [cctvrec.seg_epoch(date, x["Name"]) for x in items]
            for i, (x, st) in enumerate(zip(items, starts)):
                end = min(starts[i + 1], st + 60) if i + 1 < len(starts) else st + 60
                jpg = os.path.join(d, x["Name"][:-4] + ".jpg")       # picture of that minute, if still here
                has = os.path.exists(jpg) and os.path.getsize(jpg) > 0
                segs.append({"start": st, "end": end, "fstart": st, "size": x.get("Size", 0), "done": True, "low": False,
                             "url": f"/drive/{cam}/{date}/{x['Name']}",
                             "thumb": base + x["Name"][:-4] + ".jpg?src=" + default_source() if has else None})
        else:
            for s_ in cctvrec.merged_segments(d, date, now):
                jpg = s_["rel"][:-4] + ".jpg"
                has = os.path.exists(os.path.join(d, jpg)) and os.path.getsize(os.path.join(d, jpg)) > 0
                segs.append({"start": s_["start"], "end": s_["end"], "fstart": s_["fstart"], "size": s_["size"],
                             "done": s_["done"], "low": s_["low"], "url": base + s_["rel"] + qs,
                             "thumb": base + jpg + qs if has else None})
        pq = qs if src != "drive" else f"?src={default_source()}"
        evs = [{"start": a, "end": b, "live": b is None, "pic": base + pic + pq if pic else None}
               for a, b, pic in cctvrec.read_events(d, now)]
        self.send_json({"cam": cam, "date": date, "day_start": cctvrec.midnight(date), "src": src, "sources": sources,
                        "dates": days, "segments": segs, "events": evs, "now": now})

    # ---------- live view: websocket proxy to go2rtc ----------
    def ws_proxy(self, q, user):
        src = (q.get("src") or [""])[0]
        mine = self.my_cameras(user)
        allowed = {c["id"] for c in mine} | {c["id"] + "_sub" for c in mine}
        if src not in allowed:
            return self.send_error_json(403, "unknown camera")
        if self.headers.get("Upgrade", "").lower() != "websocket":
            return self.send_error_json(400, "websocket only")
        host, port = GO2RTC.rsplit(":", 1)
        try:
            backend = socket.create_connection((host, int(port)), timeout=5)
        except OSError:
            return self.send_error_json(502, "go2rtc not running")
        backend.settimeout(None)
        lines = [f"GET /api/ws?src={src} HTTP/1.1", f"Host: {GO2RTC}"]
        for k, v in self.headers.items():
            if k.lower() in ("host", "origin", "authorization", "cookie"):
                continue
            lines.append(f"{k}: {v}")
        backend.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        client = self.connection
        client.settimeout(None)
        self.wfile.flush()
        t = threading.Thread(target=pipe, args=(client, backend), daemon=True)
        t.start()
        pipe(backend, client)
        t.join(timeout=2)
        backend.close()
        self.close_connection = True

    # ---------- status ----------
    def status(self, user):
        if not cctvusers.has_perm(user, "status"):         # plain viewers: no system details
            return self.send_json({"docker": DOCKER, "time": int(time.time()), "limited": True})
        storage = read_json(os.path.join(RUN, "storage.json"), {})
        upload = read_json(os.path.join(RUN, "upload.json"), {})
        upload["usage"] = read_json(os.path.join(RUN, "drive-usage.json"), None)
        try:
            load1 = os.getloadavg()[0]
        except OSError:
            load1 = None
        self.send_json({
            "storage": storage,
            "upload": upload,
            "load": load1,
            "temp": cpu_temp(),
            "cpus": os.cpu_count(),
            "docker": DOCKER,
            "time": int(time.time()),
        })

    # ---------- recordings ----------
    def rec_root(self, src=None):
        return (source_root(src) if src else None) or os.path.realpath(REC)

    def recordings(self, q, user):
        if not user.get("recordings"):
            return self.send_error_json(403, "No permission for recordings")
        root = self.rec_root()
        cam = (q.get("cam") or [""])[0]
        date = (q.get("date") or [""])[0]
        names = {c["id"]: c["name"] for c in self.my_cameras(user)}
        if not cam:
            out = {}
            for cid in names:
                d = cctvrec.cam_dir(root, cid)
                dates = sorted([x for x in os.listdir(d) if DATE_RE.match(x)], reverse=True) if os.path.isdir(d) else []
                out[cid] = dates
            return self.send_json(out)
        if cam not in names or not DATE_RE.match(date):
            return self.send_error_json(400, "bad cam/date")
        d = os.path.join(cctvrec.cam_dir(root, cam), date)
        files = []
        if os.path.isdir(d):
            for name in os.listdir(d):
                fp = os.path.join(d, name)
                if not os.path.isfile(fp) or name.startswith("."):
                    continue
                st = os.stat(fp)
                files.append({"name": name, "size": st.st_size, "mtime": int(st.st_mtime),
                              "url": f"/rec/{cam}/{date}/{name}"})
        files.sort(key=lambda f: f["name"], reverse=True)
        self.send_json(files)

    def export_range(self, q, user):
        """One video for a time range: the 1-minute files joined on the fly (no re-encoding)."""
        if not user.get("recordings") or not cctvusers.has_perm(user, "download"):
            return self.send_error_json(403, "No permission to download")
        cam, date = (q.get("cam") or [""])[0], (q.get("date") or [""])[0]
        if not cctvusers.can_see(user, cam) or cam not in {c["id"] for c in cameras()} or not DATE_RE.match(date):
            return self.send_error_json(403, "No permission")
        def hm(v):
            mm = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", v or "")
            if not mm:
                raise ValueError("Pick a start and end time")
            return int(mm.group(1)) * 3600 + int(mm.group(2)) * 60 + int(mm.group(3) or 0)
        day0 = cctvrec.midnight(date)
        a, b = day0 + hm((q.get("from") or [""])[0]), day0 + hm((q.get("to") or [""])[0])
        if b <= a:
            b += 86400 if b < a else 0
        if b <= a:
            raise ValueError("The end time must be after the start time")
        src = (q.get("src") or [""])[0]
        root = self.rec_root(None if src == "drive" else src or None)
        d = os.path.join(cctvrec.cam_dir(root, cam), date)
        now = time.time()

        def pick(low):
            out = []
            for s_ in cctvrec.segments(d, date, now, low=low):
                if s_["end"] > a and s_["start"] < b:
                    out.append(s_)
            cov = sum(min(x["end"], b) - max(x["start"], a) for x in out)
            return out, cov
        hd, hd_cov = pick(False)
        lo, lo_cov = pick(True)
        want = (q.get("q") or ["auto"])[0]
        if want == "low" and lo:
            segs = lo
        elif want == "hd" and hd:
            segs = hd
        else:                                    # full quality when it covers the range, else the 24h copy
            segs = hd if hd and (hd_cov >= 0.97 * (b - a) or hd_cov >= lo_cov) else lo
        if not segs:
            return self.send_error_json(404, "No recording in this time range")
        if not EXPORT_SEM.acquire(blocking=False):
            return self.send_error_json(429, "Another download is being prepared, try again in a minute")
        lst = None
        try:
            fd, lst = tempfile.mkstemp(prefix="cctv-export-", suffix=".txt")
            with os.fdopen(fd, "w") as f:
                for s_ in segs:
                    f.write("file '%s'\n" % s_["path"].replace("'", "'\\''"))
                    if a > s_["start"] + 0.5:
                        f.write(f"inpoint {a - s_['start']:.2f}\n")
                    if b < s_["end"] - 0.5:
                        f.write(f"outpoint {b - s_['start']:.2f}\n")
            codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
                                    "-of", "csv=p=0", segs[0]["path"]], capture_output=True, text=True, timeout=20).stdout.strip()
            cmd = ["nice", "-n", "5", "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                   "-map", "0:v:0", "-map", "0:a?", "-c", "copy", *(["-tag:v", "hvc1"] if codec == "hevc" else []),
                   "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            cname = next((c["name"] for c in cameras() if c["id"] == cam), cam)
            fname = f"{cname} {date} {time.strftime('%H-%M', time.localtime(a))} to {time.strftime('%H-%M', time.localtime(b))}.mp4"
            ascii_name = re.sub(r"[^A-Za-z0-9 ._-]", "_", fname)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname)}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")           # length is not known in advance
            self.end_headers()
            self.close_connection = True
            try:
                while True:
                    chunk = proc.stdout.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.kill()
                proc.wait()
        finally:
            EXPORT_SEM.release()
            if lst:
                try:
                    os.remove(lst)
                except OSError:
                    pass

    def serve_drive(self, rel, user):
        """Play a clip that is only on Google Drive: fetched once into a local cache, then served."""
        parts = rel.split("/")
        if len(parts) != 3 or not all(SAFE_NAME.match(x) for x in parts) or not DATE_RE.match(parts[1]):
            return self.send_error_json(400, "bad path")
        if not user.get("recordings") or not cctvusers.can_see(user, parts[0]):
            return self.send_error_json(403, "No permission")
        if not cctvrec.SEG_RE.match(parts[2]) or not drive_ready():
            return self.send_error_json(404, "not found")
        fp = os.path.join(DRIVE_CACHE, *parts)
        with _drive_lock:
            if not os.path.isfile(fp):
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                tmp = fp + ".part"
                r = subprocess.run(["rclone", "--config", RCLONE_CONF, "copyto", drive_path(*parts), tmp],
                                   capture_output=True, text=True, timeout=300)
                if r.returncode != 0 or not os.path.isfile(tmp):
                    return self.send_error_json(502, "Could not get the clip from Google Drive")
                os.replace(tmp, fp)
                self._prune_drive_cache()
        self.serve_file(fp, "video/mp4", ranged=True)

    @staticmethod
    def _prune_drive_cache():
        files = []
        for dp, _, fs in os.walk(DRIVE_CACHE):
            for n in fs:
                p = os.path.join(dp, n)
                try:
                    st = os.stat(p)
                    files.append((st.st_mtime, st.st_size, p))
                except OSError:
                    pass
        total = sum(f[1] for f in files)
        for _, size, p in sorted(files):
            if total <= DRIVE_CACHE_MAX:
                break
            try:
                os.remove(p)
                total -= size
            except OSError:
                pass

    def serve_recording(self, rel, user, src=None):
        parts = rel.split("/")
        if len(parts) == 4 and parts[2] == cctvrec.LOW:      # cam/date/low/file
            pass
        elif len(parts) != 3:
            return self.send_error_json(400, "bad path")
        if not all(SAFE_NAME.match(x) for x in parts):
            return self.send_error_json(400, "bad path")
        if not user.get("recordings") or not cctvusers.can_see(user, parts[0]):
            return self.send_error_json(403, "No permission")
        root = self.rec_root(src)
        fp = os.path.realpath(os.path.join(cctvrec.cam_dir(root, parts[0]), *parts[1:]))
        if not fp.startswith(root + os.sep) or not os.path.isfile(fp):
            return self.send_error_json(404, "not found")
        ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        self.serve_file(fp, ctype, ranged=True)

    def serve_file(self, fp, ctype, ranged=False):
        try:
            f = open(fp, "rb")
        except OSError:
            return self.send_error_json(404, "not found")
        with f:
            size = os.fstat(f.fileno()).st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range") if ranged else None
            m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
            if m and size:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(HTTPStatus.OK)
            length = end - start + 1 if size else 0
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            if ranged:
                self.send_header("Accept-Ranges", "bytes")
            else:
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if self.command == "HEAD" or not length:
                return
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(262144, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def main():
    port = int(os.environ.get("CCTV_WEB_PORT", general.get("web_port", 8085)))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"CCTV dashboard on http://0.0.0.0:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
