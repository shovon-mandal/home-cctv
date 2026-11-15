"""Tiny ONVIF client: ask a camera for its RTSP stream paths (stdlib only).

find_streams(host, user, password, ports=None) ->
    {"onvif_port": 8899, "streams": [{"name": "...", "width": 2560, "height": 1440,
                                      "uri": "rtsp://...", "path": "/...", "port": 554}, ...]}
"""
import base64
import hashlib
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlparse

DEFAULT_PORTS = [80, 8000, 8080, 8899, 2020, 5000, 6688, 10080]

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}


class OnvifError(Exception):
    pass


def _security(user, password):
    if not user:
        return ""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    return (
        '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        "<UsernameToken>"
        f"<Username>{_esc(user)}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        f'<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        f'<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>'
        "</UsernameToken></Security>"
    )


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _call(url, body, user, password, timeout=3):
    env = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{_security(user, password)}</s:Header>"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    )
    req = urllib.request.Request(url, data=env.encode(), method="POST",
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return ET.fromstring(r.read())
    except urllib.error.HTTPError as e:
        data = e.read()
        if b"NotAuthorized" in data or e.code in (400, 401):
            raise OnvifError("auth") from None
        raise OnvifError(f"HTTP {e.code}") from None


def _media_url(host, port, user, password):
    url = f"http://{host}:{port}/onvif/device_service"
    root = _call(url, '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl">'
                      "<Category>Media</Category></GetCapabilities>", user, password)
    x = root.find(".//tt:Media/tt:XAddr", NS)
    if x is None or not x.text:
        return url  # many cameras serve media on the device URL too
    # cameras behind NAT sometimes report a wrong host; keep theirs path, our host
    p = urlparse(x.text.strip())
    return f"http://{host}:{port}{p.path or '/onvif/device_service'}"


def find_streams(host, user, password, ports=None):
    ports = [int(p) for p in (ports or DEFAULT_PORTS)]
    last = "Camera not found (is ONVIF on?) || camera পাওয়া যায়নি (ONVIF চালু আছে তো?)"
    for port in ports:
        try:
            media = _media_url(host, port, user, password)
            root = _call(media, '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>', user, password)
        except OnvifError as e:
            if str(e) == "auth":
                last = "ONVIF found but username/password is wrong || ONVIF পাওয়া গেছে কিন্তু username/password ভুল"
                break
            last = f"port {port}: {e}"
            continue
        except (OSError, ET.ParseError):
            continue
        streams = []
        for prof in root.findall(".//trt:Profiles", NS):
            token = prof.get("token")
            name = (prof.findtext("tt:Name", "", NS) or token or "").strip()
            w = prof.findtext(".//tt:VideoEncoderConfiguration/tt:Resolution/tt:Width", "", NS)
            h = prof.findtext(".//tt:VideoEncoderConfiguration/tt:Resolution/tt:Height", "", NS)
            enc = prof.findtext(".//tt:VideoEncoderConfiguration/tt:Encoding", "", NS)
            body = ('<GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">'
                    '<StreamSetup><Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
                    '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol></Transport>'
                    f"</StreamSetup><ProfileToken>{_esc(token or '')}</ProfileToken></GetStreamUri>")
            try:
                r = _call(media, body, user, password)
            except (OnvifError, OSError, ET.ParseError):
                continue
            uri = (r.findtext(".//tt:Uri", "", NS) or "").strip()
            if not uri:
                continue
            u = urlparse(uri)
            path = u.path + (f"?{u.query}" if u.query else "")
            streams.append({"name": name, "encoding": enc, "width": int(w or 0), "height": int(h or 0),
                            "uri": re.sub(r"//[^@/]*@", "//", uri), "path": path, "port": u.port or 554})
        if streams:
            streams.sort(key=lambda s: s["width"] * s["height"], reverse=True)
            return {"onvif_port": port, "streams": streams}
        last = f"ONVIF on port {port} returned no streams || port {port}-এ ONVIF আছে, কিন্তু কোনো stream দেয়নি"
    raise OnvifError(last)


# ---------------------------------------------------------------- PTZ
_ptz_cache = {}


def _ptz_target(host, user, password, port=None):
    """(ptz_url, profile_token), cached per camera."""
    key = (host, user, password, port)
    if key in _ptz_cache:
        return _ptz_cache[key]
    ports = [int(port)] if port else DEFAULT_PORTS
    last = "PTZ not found || PTZ পাওয়া যায়নি"
    for p in ports:
        dev = f"http://{host}:{p}/onvif/device_service"
        try:
            root = _call(dev, '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl">'
                              "<Category>All</Category></GetCapabilities>", user, password, timeout=3)
        except OnvifError as e:
            if str(e) == "auth":
                raise OnvifError("ONVIF username/password wrong || ONVIF username/password ভুল") from None
            continue
        except (OSError, ET.ParseError):
            continue
        ptz = root.find(".//tt:PTZ/tt:XAddr", NS)
        media = root.find(".//tt:Media/tt:XAddr", NS)
        if ptz is None or not ptz.text:
            last = "This camera does not support PTZ over ONVIF || এই camera ONVIF দিয়ে ঘোরানো যায় না"
            break
        fix = lambda u: f"http://{host}:{p}{urlparse(u.strip()).path}"
        media_url = fix(media.text) if media is not None and media.text else dev
        prof = _call(media_url, '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>', user, password)
        tok = None
        for pr in prof.findall(".//trt:Profiles", NS):
            if pr.find(".//tt:PTZConfiguration", NS) is not None:
                tok = pr.get("token")
                break
        if tok is None:
            first = prof.find(".//trt:Profiles", NS)
            tok = first.get("token") if first is not None else None
        if not tok:
            break
        _ptz_cache[key] = (fix(ptz.text), tok)
        return _ptz_cache[key]
    raise OnvifError(last)


def ptz_move(host, user, password, x=0.0, y=0.0, zoom=0.0, port=None):
    url, tok = _ptz_target(host, user, password, port)
    zoom_xml = f'<Zoom x="{zoom:.2f}" xmlns="http://www.onvif.org/ver10/schema"/>' if zoom else ""
    body = ('<ContinuousMove xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
            f"<ProfileToken>{_esc(tok)}</ProfileToken><Velocity>"
            f'<PanTilt x="{x:.2f}" y="{y:.2f}" xmlns="http://www.onvif.org/ver10/schema"/>{zoom_xml}'
            "</Velocity><Timeout>PT3S</Timeout></ContinuousMove>")
    _call(url, body, user, password, timeout=4)


def ptz_stop(host, user, password, port=None):
    url, tok = _ptz_target(host, user, password, port)
    body = ('<Stop xmlns="http://www.onvif.org/ver20/ptz/wsdl">'
            f"<ProfileToken>{_esc(tok)}</ProfileToken><PanTilt>true</PanTilt><Zoom>true</Zoom></Stop>")
    _call(url, body, user, password, timeout=4)
