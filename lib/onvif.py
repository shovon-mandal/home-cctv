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


def _call(url, body, user, password, timeout=4):
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
    last = "camera পাওয়া যায়নি (ONVIF চালু আছে তো?)"
    for port in ports:
        try:
            media = _media_url(host, port, user, password)
            root = _call(media, '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>', user, password)
        except OnvifError as e:
            if str(e) == "auth":
                last = "ONVIF পাওয়া গেছে কিন্তু username/password ভুল"
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
        last = f"port {port}-এ ONVIF আছে, কিন্তু কোনো stream দেয়নি"
    raise OnvifError(last)
