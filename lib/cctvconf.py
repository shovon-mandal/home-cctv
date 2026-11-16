"""Shared config loader for the CCTV system.

/etc/cctv/cctv.conf      general settings (edited by hand)
/etc/cctv/cameras.conf   cameras (edited from the dashboard or by hand)

INI format, no interpolation so passwords containing % work.
"""
import configparser
import os
import re
from urllib.parse import quote

CONF_PATH = os.environ.get("CCTV_CONF", "/etc/cctv/cctv.conf")
CAMS_PATH = os.environ.get("CCTV_CAMS", os.path.join(os.path.dirname(CONF_PATH), "cameras.conf"))

DEFAULTS = {
    "site_name": "Home CCTV",
    "web_port": "8085",
    "sd_buffer_gb": "7",
    "sd_min_free_gb": "2",
    "ssd_min_free_percent": "5",
    "ssd_max_gb": "200",
    "ssd_auto": "yes",
    "drive_enabled": "yes",
    "drive_remote": "gdrive:CCTV",
    "drive_backup_when_ssd": "yes",
    "drive_keep_days": "5",
    "drive_max_gb": "12",
    "drive_bwlimit": "0",
    "motion_width": "640",
    "motion_height": "360",
    "event_gap": "20",
    "pre_capture_sec": "3",
    "post_capture_sec": "5",
    "max_clip_minutes": "3",
    "motion_only_keep_minutes": "10",
    "merge_minutes": "0",           # join finished 1-minute files into files this long (0 = off)
    "auto_update": "yes",
    "cpu_max_mhz": "auto",          # auto: Tinker Board at most 1416 MHz (steady power); 0 = no limit           # install new versions from GitHub by itself (no: only check)
    "merge_ssd_only": "no",         # yes: join only on the SSD, keep 1-minute files on the SD card
}

# Ready-made RTSP settings per brand (shown in the dashboard's brand list, in this order).
# A path may contain {user} and {password} for cameras that want them in the URL.
# "onvif" = no fixed path: the dashboard's "Find stream paths via ONVIF" fills it in.
# "url"   = paste complete stream URLs (rtsp://, rtsps://, rtmp://, http://, or a go2rtc source).
# More details and "how to turn RTSP on" for each brand: docs/CAMERAS.md
PRESETS = {
    "onvif":     {"label": "Any ONVIF camera (auto-detect)", "port": "554", "user": "admin",
                  "main_path": "", "sub_path": "",
                  "hint": "Enter IP, username and password, then press 'Find stream paths via ONVIF'. "
                          "Works with most cameras that have ONVIF turned on."},
    "hikvision": {"label": "Hikvision / HiWatch / Annke", "port": "554", "user": "admin",
                  "main_path": "/Streaming/Channels/101", "sub_path": "/Streaming/Channels/102",
                  "hint": "Password = the one set when the camera was activated."},
    "ezviz":     {"label": "EZVIZ", "port": "554", "user": "admin",
                  "main_path": "/h264/ch1/main/av_stream", "sub_path": "/h264/ch1/sub/av_stream",
                  "hint": "Password = the 6-letter verification code on the camera sticker. In the EZVIZ app turn on "
                          "RTSP (Settings > LAN Live View / Local Service Settings) and turn off Image Encryption. "
                          "If Test fails, try /Streaming/Channels/101 or ONVIF."},
    "dahua":     {"label": "Dahua / Amcrest / Lorex", "port": "554", "user": "admin",
                  "main_path": "/cam/realmonitor?channel=1&subtype=0",
                  "sub_path": "/cam/realmonitor?channel=1&subtype=1",
                  "hint": "Username and password of the camera's web page / app."},
    "imou":      {"label": "Imou", "port": "554", "user": "admin",
                  "main_path": "/cam/realmonitor?channel=1&subtype=0",
                  "sub_path": "/cam/realmonitor?channel=1&subtype=1",
                  "hint": "Password = the Safety Code on the camera sticker. In the Imou app turn off "
                          "Video Encryption (and turn on ONVIF/RTSP if the setting exists).", "ptz": "yes"},
    "reolink":   {"label": "Reolink", "port": "554", "user": "admin",
                  "main_path": "/h264Preview_01_main", "sub_path": "/h264Preview_01_sub",
                  "hint": "Turn on RTSP in the Reolink app/client (Network > Advanced > Server/Port settings). "
                          "H.265 models: /h265Preview_01_main. Battery cameras and the E1 have no RTSP."},
    "tapo":      {"label": "TP-Link Tapo", "port": "554", "user": "", "onvif_port": "2020",
                  "main_path": "/stream1", "sub_path": "/stream2",
                  "hint": "Tapo app > camera > Settings > Advanced Settings > Camera Account: create a "
                          "username/password and use them here (not your TP-Link login)."},
    "vigi":      {"label": "TP-Link VIGI / Omada", "port": "554", "user": "admin",
                  "main_path": "/stream1", "sub_path": "/stream2",
                  "hint": "Username and password of the camera's web page."},
    "uniview":   {"label": "Uniview (UNV)", "port": "554", "user": "admin",
                  "main_path": "/media/video1", "sub_path": "/media/video2",
                  "hint": "Username and password of the camera's web page."},
    "axis":      {"label": "Axis", "port": "554", "user": "root",
                  "main_path": "/axis-media/media.amp", "sub_path": "/axis-media/media.amp?resolution=640x360",
                  "hint": "Username and password of the camera's web page."},
    "foscam":    {"label": "Foscam", "port": "554", "user": "admin",
                  "main_path": "/videoMain", "sub_path": "/videoSub",
                  "hint": "Older models use RTSP port 88."},
    "eufy":      {"label": "eufy (models with RTSP)", "port": "554", "user": "",
                  "main_path": "/live0", "sub_path": "",
                  "hint": "eufy app > camera Settings > Storage > NAS (RTSP): turn it on and set a username/password. "
                          "Not every model has this."},
    "meari":     {"label": "Meari / CloudEdge", "port": "8554", "user": "admin",
                  "main_path": "/Streaming/Channels/101", "sub_path": "",
                  "hint": "Turn on ONVIF in the app (Settings > Onvif Settings) and set its password. "
                          "Then press 'Find stream paths via ONVIF' to fill in both streams."},
    "v380":      {"label": "V380", "port": "554", "user": "admin",
                  "main_path": "/live/ch00_0", "sub_path": "/live/ch00_1",
                  "hint": "Turn on ONVIF in the V380 app; password = device password."},
    "yoosee":    {"label": "Yoosee", "port": "554", "user": "admin", "onvif_port": "5000",
                  "main_path": "/onvif1", "sub_path": "/onvif2",
                  "hint": "Set the RTSP/NVR password in the Yoosee app. If Test fails, the camera may only "
                          "stream over UDP: use 'Full stream URL' with ffmpeg:rtsp://..."},
    "xmeye":     {"label": "ICSee / XMEye (Xiongmai)", "port": "554", "user": "admin", "onvif_port": "8899",
                  "main_path": "/user={user}&password={password}&channel=1&stream=0.sdp?real_stream",
                  "sub_path": "/user={user}&password={password}&channel=1&stream=1.sdp?real_stream",
                  "hint": "Default user admin with an empty password (set one in the app)."},
    "vstarcam":  {"label": "Vstarcam / Eye4", "port": "10554", "user": "admin",
                  "main_path": "/tcp/av0_0", "sub_path": "/tcp/av0_1",
                  "hint": "Password = device password set in the Eye4 app."},
    "wansview":  {"label": "Wansview", "port": "554", "user": "admin",
                  "main_path": "/live/ch0", "sub_path": "/live/ch1",
                  "hint": "Turn on RTSP in the app and set its password."},
    "url":       {"label": "Full stream URL (any camera, NVR, UniFi, ...)", "port": "", "user": "",
                  "main_path": "", "sub_path": "",
                  "hint": "Paste complete URLs, e.g. rtsp://user:pass@192.168.1.20:554/stream1 . "
                          "Also rtsps://, rtmp://, http:// (MJPEG) and go2rtc sources like ffmpeg:rtsp://..."},
    "custom":    {"label": "Other (enter the path yourself)", "port": "554", "user": "admin",
                  "main_path": "", "sub_path": "",
                  "hint": "Look up your model in docs/CAMERAS.md, or find the path with ONVIF Device Manager / Onvifer."},
}
URL_SCHEMES = ("rtsp://", "rtsps://", "rtspx://", "rtmp://", "rtmps://", "http://", "https://", "ffmpeg:")

CAM_FIELDS = ["name", "type", "host", "port", "user", "password", "main_path", "sub_path",
              "onvif_port", "mode", "record_stream", "threshold", "audio", "ptz", "enabled"]

CAM_DEFAULTS = {
    "name": "", "type": "custom", "host": "", "port": "554", "user": "admin", "password": "",
    "main_path": "", "sub_path": "", "onvif_port": "",
    "main": "", "sub": "",        # optional full rtsp:// URLs (override host/path fields)
    "enabled": "yes",
    "mode": "smart",              # smart (24/7 low + full quality at motion) | continuous (24/7 full) | motion | off
    "ptz": "no",                  # camera can pan/tilt (ONVIF)
    "audio": "yes",               # sound in live view and recordings
    "threshold": "250",
    "record_stream": "main",      # main = full quality; sub = low quality (remote cams)
}

CAM_ID_RE = re.compile(r"^cam[0-9]{1,2}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")


def _yes(v):
    return str(v).strip().lower() in ("1", "yes", "true", "on")


def yes(v):
    return _yes(v)


def _parser():
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str.lower
    return cp


def rtsp_url(c, which):
    """Build the rtsp:// URL for 'main' or 'sub'."""
    if c.get(which):
        return c[which]
    if c.get("type") == "url":
        return c.get("main", "") if which == "sub" else ""
    path = c.get(f"{which}_path") or (c.get("main_path") if which == "sub" else "")
    if not c.get("host") or not path:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    path = path.replace("{user}", quote(c.get("user", ""), safe="")).replace(
        "{password}", quote(c.get("password", ""), safe=""))
    auth = ""
    if c.get("user"):
        auth = quote(c["user"], safe="")
        if c.get("password"):
            auth += ":" + quote(c["password"], safe="")
        auth += "@"
    port = f":{c['port']}" if c.get("port") else ""
    return f"rtsp://{auth}{c['host']}{port}{path}"


def normalize_cam(cid, raw):
    c = dict(CAM_DEFAULTS)
    c.update({k: str(v).strip() for k, v in raw.items()
              if str(v).strip() or k not in ("mode", "record_stream", "port", "threshold", "enabled")})
    c["id"] = cid
    c["name"] = c["name"] or cid
    c["enabled"] = _yes(c["enabled"])
    c["ptz"] = _yes(c["ptz"])
    c["audio"] = _yes(c["audio"])
    c["mode"] = c["mode"].lower()
    if c["mode"] not in ("smart", "motion", "continuous", "off"):
        raise ValueError(f"[{cid}] mode must be smart, motion, continuous or off")
    c["record_stream"] = c["record_stream"].lower()
    if c["record_stream"] not in ("main", "sub"):
        raise ValueError(f"[{cid}] record_stream must be main or sub")
    if c["host"] and not HOST_RE.match(c["host"]):
        raise ValueError(f"[{cid}] Invalid IP/host")
    if c["port"] and not c["port"].isdigit():
        raise ValueError(f"[{cid}] Port must be a number")
    try:
        c["threshold"] = str(max(1, int(float(c["threshold"]))))
    except ValueError:
        raise ValueError(f"[{cid}] Sensitivity must be a number") from None
    for which in ("main", "sub"):
        if c.get(which) and not c[which].lower().startswith(URL_SCHEMES):
            raise ValueError(f"[{cid}] Stream URL must start with rtsp://, rtsps://, rtmp://, http:// or ffmpeg:")
    c["main_url"] = rtsp_url(c, "main")
    c["sub_url"] = rtsp_url(c, "sub") or c["main_url"]
    if c["enabled"] and not c["main_url"]:
        raise ValueError(f"[{cid}] " + ("The main stream URL is required" if c["type"] == "url"
                                        else "IP and main stream path are required"))
    return c


def load_cameras(path=None):
    path = path or CAMS_PATH
    cams = []
    cp = _parser()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cp.read_file(f)
    # legacy: cameras inside cctv.conf
    if os.path.exists(CONF_PATH):
        legacy = _parser()
        with open(CONF_PATH, encoding="utf-8") as f:
            legacy.read_file(f)
        for sec in legacy.sections():
            if CAM_ID_RE.match(sec) and not cp.has_section(sec):
                cp.add_section(sec)
                for k, v in legacy.items(sec):
                    cp.set(sec, k, v)
    for sec in cp.sections():
        if not CAM_ID_RE.match(sec):
            raise ValueError(f"Camera section must be named cam1..cam99, got [{sec}]")
        cams.append(normalize_cam(sec, dict(cp.items(sec))))
    cams.sort(key=lambda c: int(c["id"][3:]))
    return cams


def save_cameras(cams, path=None):
    """cams: list of dicts with CAM_FIELDS (+ optional main/sub). Atomic write."""
    path = path or CAMS_PATH
    lines = [
        "# Cameras. Edit from the dashboard: Settings → Cameras.",
        "# After editing by hand run: sudo cctv-apply",
        "",
    ]
    for c in sorted(cams, key=lambda c: int(c["id"][3:])):
        lines.append(f"[{c['id']}]")
        for k in CAM_FIELDS + ["main", "sub"]:
            v = c.get(k, "")
            if isinstance(v, bool):
                v = "yes" if v else "no"
            v = str(v).replace("\n", " ").strip()
            if k in ("main", "sub", "onvif_port") and not v:
                continue
            lines.append(f"{k} = {v}")
        lines.append("")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.chmod(tmp, 0o640)
    fix_owner(tmp)
    os.replace(tmp, path)


def fix_owner(path):
    """Files written by root (cctv-apply, updates) must stay readable by the cctv services."""
    if os.geteuid() != 0:
        return
    try:
        import grp
        import pwd
        os.chown(path, pwd.getpwnam("cctv").pw_uid, grp.getgrnam("cctv").gr_gid)
    except (KeyError, OSError):
        pass


def set_general(updates, path=None):
    """Change `key = value` lines in [general], keeping comments and order; adds missing keys."""
    path = path or CONF_PATH
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out, section, done = [], None, set()
    gen_end = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("[") and s.endswith("]"):
            if section == "general":
                gen_end = len(out)
            section = s[1:-1].strip().lower()
        elif section == "general" and "=" in s and not s.startswith(("#", ";")):
            k = s.split("=", 1)[0].strip().lower()
            if k in updates:
                ln = f"{k} = {updates[k]}"
                done.add(k)
        out.append(ln)
    if section == "general" and gen_end is None:
        gen_end = len(out)
    missing = [f"{k} = {v}" for k, v in updates.items() if k not in done]
    if missing:
        if gen_end is None:
            out = ["[general]"] + missing + [""] + out
        else:
            while gen_end > 0 and out[gen_end - 1].strip() == "":
                gen_end -= 1
            out[gen_end:gen_end] = missing
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    os.chmod(tmp, 0o640)
    fix_owner(tmp)
    os.replace(tmp, path)


def load(path=None):
    path = path or CONF_PATH
    cp = _parser()
    with open(path, encoding="utf-8") as f:
        cp.read_file(f)
    general = dict(DEFAULTS)
    if cp.has_section("general"):
        general.update({k: v.strip() for k, v in cp.items("general")})
    return general, load_cameras()
