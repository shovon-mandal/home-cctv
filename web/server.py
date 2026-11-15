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
import threading
import time
import urllib.error
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, "/opt/cctv/lib")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import cctvconf  # noqa: E402
import cctvusers  # noqa: E402
import onvif  # noqa: E402
import cctvrec  # noqa: E402

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
    cmd = ["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-timeout", "6000000",
           "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height", "-of", "json", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return {"ok": False, "error": "ffprobe is not installed (sudo apt install ffmpeg) || ffprobe install নেই (sudo apt install ffmpeg)"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timed out: camera not answering (check IP/port) || সময় শেষ: camera সাড়া দিচ্ছে না (IP/port দেখুন)"}
    try:
        st = json.loads(r.stdout or "{}").get("streams", [])
    except ValueError:
        st = []
    if r.returncode == 0 and st:
        return {"ok": True, "codec": st[0].get("codec_name"), "width": st[0].get("width"), "height": st[0].get("height")}
    err = (r.stderr or "").strip().splitlines()
    msg = err[-1] if err else "No stream found || stream পাওয়া যায়নি"
    msg = re.sub(r"rtsp://\S+: ", "", msg)                   # drop the URL (it contains the password)
    msg = re.sub(r"//[^@/\s]+@", "//***@", msg)
    if "401" in msg or "Unauthorized" in msg:
        msg = "Wrong username/password || username/password ভুল"
    elif "404" in msg or "Not Found" in msg:
        msg = "Wrong path (camera does not know this stream) || path ভুল (camera এই stream চেনে না)"
    elif "Connection refused" in msg:
        msg = "Port closed (is RTSP/ONVIF on?) || port বন্ধ (RTSP/ONVIF চালু আছে তো?)"
    elif "Invalid data found" in msg:
        msg = ("Camera answers but the video can't be read. On Imou turn off 'Video/Stream Encryption' in the app || "
               "camera সাড়া দিচ্ছে কিন্তু video পড়া যাচ্ছে না। Imou হলে app-এ 'Video Encryption' বন্ধ করুন")
    elif "No route" in msg or "timed out" in msg.lower() or "Network is unreachable" in msg:
        msg = "Camera not reachable (is the IP right?) || camera পাওয়া যাচ্ছে না (IP ঠিক আছে তো?)"
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
    raise ValueError("Could not get the Google login link || Google login link পাওয়া যায়নি")


def drive_finish(pasted):
    import base64
    import urllib.request
    from urllib.parse import urlencode
    q = parse_qs(urlparse(pasted.strip()).query)
    code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    if not code:
        raise ValueError("The link has no 'code='. After Allow, copy the whole address bar link || Link-এ 'code=' নেই। Allow চাপার পরে address bar-এর পুরো link copy করুন।")
    p = _drive["proc"]
    if not p or p.poll() is not None or state != _drive["state"]:
        raise ValueError("Timed out. Press 'Connect with Google' again || সময় শেষ হয়ে গেছে। আবার 'Google দিয়ে connect' চাপুন।")
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
            raise ValueError("Google did not give a token (link expired or no internet). Press 'Connect with Google' again || Google থেকে token নেওয়া যায়নি। আবার 'Google দিয়ে connect' চাপুন।")
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
    res = {"configured": configured, "remote": general.get("drive_remote"), "max_gb": general.get("drive_max_gb")}
    if configured and check:
        base = ["rclone", "--config", RCLONE_CONF]
        r = subprocess.run(base + ["mkdir", general.get("drive_remote", "gdrive:CCTV")], capture_output=True, text=True, timeout=60)
        res["ok"] = r.returncode == 0
        if r.returncode != 0:
            res["error"] = (r.stderr or "").strip().splitlines()[-1:] or ["unknown"]
        a = subprocess.run(base + ["about", "gdrive:", "--json"], capture_output=True, text=True, timeout=60)
        try:
            res["about"] = json.loads(a.stdout)
        except ValueError:
            pass
    return res


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
            self.send_error_json(401, "Please log in || login দরকার")
            return None
        if admin and who[1]["role"] != "admin":
            self.send_error_json(403, "Admins only || শুধু admin পারবে")
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
                return self.status()
            if p == "/api/storage":
                return self.send_json({"storage": read_json(os.path.join(RUN, "storage.json"), {}),
                                       "eject": read_json(os.path.join(RUN, "eject.json"), {})})
            if p == "/api/recordings":
                return self.recordings(q, user)
            if p.startswith("/rec/"):
                return self.serve_recording(unquote(p[5:]), user)
            if p == "/api/admin/cameras":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only || শুধু admin পারবে")
                return self.admin_cameras()
            if p == "/api/admin/update":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only || শুধু admin পারবে")
                d = read_json(os.path.join(RUN, "update.json"), {})
                try:
                    d["public_url"] = open("/etc/cctv/public_url").read().strip()
                except OSError:
                    d["public_url"] = ""
                return self.send_json(d)
            if p == "/api/admin/drive":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only || শুধু admin পারবে")
                return self.send_json(drive_status(check="check" in q))
            if p == "/api/admin/apply":
                return self.send_json(read_json(os.path.join(RUN, "apply.json"), {}))
            if p == "/api/users":
                if user["role"] != "admin":
                    return self.send_error_json(403, "Admins only || শুধু admin পারবে")
                data = cctvusers.load()
                return self.send_json([cctvusers.public_view(n, x) for n, x in sorted(data["users"].items())])
            self.send_error_json(404, "not found")
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_HEAD = do_GET


    def do_POST(self):
        p = urlparse(self.path).path
        try:
            body = self.read_body()
        except PermissionError:
            return self.send_error_json(403, "bad request origin")
        except (ValueError, TypeError):
            return self.send_error_json(400, "bad json")
        try:
            if p == "/api/login":
                return self.login(body)
            if p == "/api/logout":
                return self.send_json({"ok": True}, headers=[("Set-Cookie", f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")])
            if p == "/api/password":                     # any user: change own password
                who = self.require()
                if not who:
                    return
                name, u = who
                if not cctvusers.check_password(str(body.get("old", "")), u.get("hash", "")):
                    return self.send_error_json(400, "Current password is wrong || পুরোনো password ভুল")
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
                                   cams, bool(body.get("recordings", True)), bool(body.get("ptz", False)))
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
            if p == "/api/admin/cameras/save":
                return self.cam_save(body)
            if p == "/api/admin/cameras/delete":
                return self.cam_delete(body)
            if p == "/api/admin/cameras/test":
                return self.cam_test(body)
            if p == "/api/admin/cameras/onvif":
                return self.cam_onvif(body)
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
            return self.send_json({"error": self.tr("Please log in || login দরকার"), "needs_setup": not data["users"]}, 401)
        return self.send_json(cctvusers.public_view(*who))

    def login(self, body):
        ip = self.client_ip()
        now = time.time()
        with _fails_lock:
            recent = [t for t in _fails.get(ip, []) if now - t < 600]
            _fails[ip] = recent
            if len(recent) >= 5:
                return self.send_error_json(429, "Too many attempts, try again in 10 minutes || অনেকবার ভুল হয়েছে, ১০ মিনিট পর চেষ্টা করুন")
        name = str(body.get("user", "")).strip()
        u = cctvusers.load()["users"].get(name)
        if not u or not cctvusers.check_password(str(body.get("pass", "")), u.get("hash", "")):
            with _fails_lock:
                _fails.setdefault(ip, []).append(now)
            time.sleep(1)
            return self.send_error_json(401, "Wrong username or password || username বা password ভুল")
        with _fails_lock:
            _fails.pop(ip, None)
        self.send_json(cctvusers.public_view(name, u), headers=[("Set-Cookie", self.cookie(cctvusers.make_token(name, u)))])

    def delete_recording(self, body):
        cam, date, fname = (str(body.get(k, "")) for k in ("cam", "date", "name"))
        if not (SAFE_NAME.match(cam) and DATE_RE.match(date) and SAFE_NAME.match(fname)):
            return self.send_error_json(400, "bad path")
        root = self.rec_root()
        fp = os.path.realpath(os.path.join(root, cam, date, fname))
        if not fp.startswith(root + os.sep) or not os.path.isfile(fp):
            return self.send_error_json(404, "not found")
        os.remove(fp)
        self.send_json({"ok": True})


    # ---------- camera management (admin) ----------
    def admin_cameras(self):
        cams = []
        for c in cctvconf.load_cameras():
            d = {k: c.get(k, "") for k in cctvconf.CAM_FIELDS if k != "password"}
            d.update(id=c["id"], has_password=bool(c.get("password")),
                     custom_url=bool(c.get("main")), enabled=c["enabled"], ptz=c["ptz"])
            d["recording"] = self.rec_status(c)
            cams.append(d)
        presets = {k: {kk: vv for kk, vv in v.items()} for k, v in cctvconf.PRESETS.items()}
        self.send_json({"cameras": cams, "presets": presets})

    def rec_status(self, c):
        if not c["enabled"] or c["mode"] == "off":
            return {"state": "off"}
        st = read_json(os.path.join(RUN, "recorder.json"), {}).get(c["id"], {})
        date = time.strftime("%Y-%m-%d")
        segs = cctvrec.segments(os.path.join(self.rec_root(), c["id"], date), date)
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
        # dashboard edits always use host/path fields
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
            return self.send_error_json(404, "No such camera || camera নেই")
        if not cid:
            used = {int(c["id"][3:]) for c in cams}
            n = next(i for i in range(1, 100) if i not in used)
            cid = f"cam{n}"
        raw = self._cam_from_body(body, by_id.get(cid))
        if not raw.get("name"):
            raise ValueError("Give the camera a name || camera-র নাম দিন")
        c = cctvconf.normalize_cam(cid, raw)            # validates
        new = [x for x in cams if x["id"] != cid] + [dict(raw, id=cid)]
        cctvconf.save_cameras(new)
        self._request_apply()
        self.send_json({"ok": True, "id": cid, "name": c["name"], "requested": time.time()})

    def cam_delete(self, body):
        cams, by_id = self._cams_by_id()
        cid = str(body.get("id", ""))
        if cid not in by_id:
            return self.send_error_json(404, "No such camera || camera নেই")
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
            return self.send_error_json(400, "Invalid IP || IP ঠিক নেই")
        user = str(body.get("user", old.get("user", "")))
        pw = str(body.get("password") or old.get("password", ""))
        ports = [int(body["onvif_port"])] if str(body.get("onvif_port", "")).isdigit() else None
        try:
            res = onvif.find_streams(host, user, pw, ports)
        except onvif.OnvifError as e:
            return self.send_error_json(400, str(e))
        self.send_json(res)

    # ---------- PTZ ----------
    def ptz(self, body, user):
        if not (user["role"] == "admin" or user.get("ptz")):
            return self.send_error_json(403, "You are not allowed to move cameras || camera ঘোরানোর অনুমতি নেই")
        cid = str(body.get("cam", ""))
        if not cctvusers.can_see(user, cid):
            return self.send_error_json(403, "No permission || অনুমতি নেই")
        c = {x["id"]: x for x in cctvconf.load_cameras()}.get(cid)
        if not c or not c["ptz"]:
            return self.send_error_json(400, "PTZ is off for this camera || এই camera-য় PTZ বন্ধ")
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
            return self.send_error_json(502, "Camera did not answer || camera সাড়া দেয়নি")
        self.send_json({"ok": True})

    # ---------- timeline ----------
    def timeline(self, q, user):
        if not user.get("recordings"):
            return self.send_error_json(403, "No permission for recordings || recordings দেখার অনুমতি নেই")
        cam = (q.get("cam") or [""])[0]
        date = (q.get("date") or [""])[0]
        if not cctvusers.can_see(user, cam) or cam not in {c["id"] for c in cameras()}:
            return self.send_error_json(403, "No permission || অনুমতি নেই")
        root = self.rec_root()
        days = cctvrec.dates_of(root, cam)
        if not DATE_RE.match(date):
            date = days[0] if days else time.strftime("%Y-%m-%d")
        d = os.path.join(root, cam, date)
        now = time.time()
        base = f"/rec/{cam}/{date}/"
        segs = []
        for s in cctvrec.segments(d, date, now):
            jpg = s["name"][:-4] + ".jpg"
            has = os.path.exists(os.path.join(d, jpg)) and os.path.getsize(os.path.join(d, jpg)) > 0
            segs.append({"start": s["start"], "end": s["end"], "size": s["size"], "done": s["done"],
                         "url": base + s["name"], "thumb": base + jpg if has else None})
        evs = [{"start": a, "end": b, "live": b is None, "pic": base + pic if pic else None}
               for a, b, pic in cctvrec.read_events(d, now)]
        self.send_json({"cam": cam, "date": date, "day_start": cctvrec.midnight(date),
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
    def status(self):
        storage = read_json(os.path.join(RUN, "storage.json"), {})
        upload = read_json(os.path.join(RUN, "upload.json"), {})
        try:
            load1 = os.getloadavg()[0]
        except OSError:
            load1 = None
        self.send_json({
            "storage": storage,
            "upload": upload,
            "load": load1,
            "cpus": os.cpu_count(),
            "time": int(time.time()),
        })

    # ---------- recordings ----------
    def rec_root(self):
        return os.path.realpath(REC)

    def recordings(self, q, user):
        if not user.get("recordings"):
            return self.send_error_json(403, "No permission for recordings || recordings দেখার অনুমতি নেই")
        root = self.rec_root()
        cam = (q.get("cam") or [""])[0]
        date = (q.get("date") or [""])[0]
        names = {c["id"]: c["name"] for c in self.my_cameras(user)}
        if not cam:
            out = {}
            for cid in names:
                d = os.path.join(root, cid)
                dates = sorted([x for x in os.listdir(d) if DATE_RE.match(x)], reverse=True) if os.path.isdir(d) else []
                out[cid] = dates
            return self.send_json(out)
        if cam not in names or not DATE_RE.match(date):
            return self.send_error_json(400, "bad cam/date")
        d = os.path.join(root, cam, date)
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

    def serve_recording(self, rel, user):
        parts = rel.split("/")
        if len(parts) != 3 or not all(SAFE_NAME.match(x) for x in parts):
            return self.send_error_json(400, "bad path")
        if not user.get("recordings") or not cctvusers.can_see(user, parts[0]):
            return self.send_error_json(403, "No permission || অনুমতি নেই")
        root = self.rec_root()
        fp = os.path.realpath(os.path.join(root, *parts))
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
