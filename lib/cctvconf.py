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
}

# Ready-made RTSP paths per brand. "meari" has no known fixed path: use the
# dashboard's "ONVIF থেকে খুঁজুন" button (or Onvifer) to fill it in.
PRESETS = {
    "imou":   {"label": "Imou / Dahua", "port": "554", "user": "admin",
               "main_path": "/cam/realmonitor?channel=1&subtype=0",
               "sub_path": "/cam/realmonitor?channel=1&subtype=1",
               "hint": "Password = the Safety Code on the camera sticker || Password = camera-র নিচের sticker-এর Safety Code", "ptz": "yes"},
    "meari":  {"label": "Meari / CloudEdge", "port": "554", "user": "admin",
               "main_path": "", "sub_path": "",
               "hint": "Enter IP and password, then press 'Find via ONVIF' || IP আর password দিয়ে 'ONVIF থেকে খুঁজুন' চাপুন, path নিজে বসে যাবে"},
    "v380":   {"label": "V380", "port": "554", "user": "admin",
               "main_path": "/live/ch00_0", "sub_path": "/live/ch00_1",
               "hint": "Turn on ONVIF in the V380 app; password = device password || V380 app-এ ONVIF চালু করুন; password = device password"},
    "tapo":   {"label": "TP-Link Tapo", "port": "554", "user": "",
               "main_path": "/stream1", "sub_path": "/stream2",
               "hint": "Tapo app → Advanced → Camera Account user/password || Tapo app → Advanced → Camera Account-এর user/password"},
    "custom": {"label": "Other / Custom", "port": "554", "user": "admin",
               "main_path": "", "sub_path": "",
               "hint": "Enter the path you found with Onvifer or VLC || Onvifer বা VLC দিয়ে পাওয়া path বসান"},
}

CAM_FIELDS = ["name", "type", "host", "port", "user", "password", "main_path", "sub_path",
              "onvif_port", "mode", "record_stream", "threshold", "audio", "ptz", "enabled"]

CAM_DEFAULTS = {
    "name": "", "type": "custom", "host": "", "port": "554", "user": "admin", "password": "",
    "main_path": "", "sub_path": "", "onvif_port": "",
    "main": "", "sub": "",        # optional full rtsp:// URLs (override host/path fields)
    "enabled": "yes",
    "mode": "continuous",         # continuous (24/7) | motion (keep only motion parts) | off
    "ptz": "no",                  # camera can pan/tilt (ONVIF)
    "audio": "yes",               # sound in live view and recordings
    "threshold": "400",
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
    path = c.get(f"{which}_path") or (c.get("main_path") if which == "sub" else "")
    if not c.get("host") or not path:
        return ""
    if not path.startswith("/"):
        path = "/" + path
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
    c.update({k: str(v).strip() for k, v in raw.items()})
    c["id"] = cid
    c["name"] = c["name"] or cid
    c["enabled"] = _yes(c["enabled"])
    c["ptz"] = _yes(c["ptz"])
    c["audio"] = _yes(c["audio"])
    c["mode"] = c["mode"].lower()
    if c["mode"] not in ("motion", "continuous", "off"):
        raise ValueError(f"[{cid}] mode must be motion, continuous or off")
    c["record_stream"] = c["record_stream"].lower()
    if c["record_stream"] not in ("main", "sub"):
        raise ValueError(f"[{cid}] record_stream must be main or sub")
    if c["host"] and not HOST_RE.match(c["host"]):
        raise ValueError(f"[{cid}] Invalid IP/host || IP/host ঠিক নেই")
    if c["port"] and not c["port"].isdigit():
        raise ValueError(f"[{cid}] Port must be a number || port শুধু সংখ্যা হবে")
    try:
        c["threshold"] = str(max(1, int(float(c["threshold"]))))
    except ValueError:
        raise ValueError(f"[{cid}] Sensitivity must be a number || threshold সংখ্যা হবে") from None
    c["main_url"] = rtsp_url(c, "main")
    c["sub_url"] = rtsp_url(c, "sub") or c["main_url"]
    if c["enabled"] and not c["main_url"]:
        raise ValueError(f"[{cid}] IP and main stream path are required || IP আর main path দরকার")
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
