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


# ---------------------------------------------------------------------------
# Picture settings: night mode (IR cut filter), HDR/WDR, brightness, main-stream resolution
# ---------------------------------------------------------------------------
NS["timg"] = "http://www.onvif.org/ver20/imaging/wsdl"
TT = "http://www.onvif.org/ver10/schema"
TRT = "http://www.onvif.org/ver10/media/wsdl"
_svc_cache = {}


def _services(host, user, password, port=None):
    """{'media': url, 'imaging': url or None}, cached per camera."""
    key = (host, user, password, port)
    if key in _svc_cache:
        return _svc_cache[key]
    for p in ([int(port)] if port else DEFAULT_PORTS):
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
        fix = lambda u: f"http://{host}:{p}{urlparse(u.strip()).path}"
        m = root.find(".//tt:Media/tt:XAddr", NS)
        i = root.find(".//tt:Imaging/tt:XAddr", NS)
        _svc_cache[key] = {"media": fix(m.text) if m is not None and m.text else dev,
                           "imaging": fix(i.text) if i is not None and i.text else None}
        return _svc_cache[key]
    raise OnvifError("ONVIF not reachable (is ONVIF on in the camera app?) || ONVIF পাওয়া যায়নি (camera app-এ ONVIF চালু আছে?)")


def _main_profile(media, user, password):
    """The profile with the biggest resolution -> (video_source_token, encoder_config_element)."""
    root = _call(media, '<GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>', user, password)
    best, area = None, -1
    for pr in root.findall(".//trt:Profiles", NS):
        w = int(pr.findtext(".//tt:VideoEncoderConfiguration/tt:Resolution/tt:Width", "0", NS) or 0)
        h = int(pr.findtext(".//tt:VideoEncoderConfiguration/tt:Resolution/tt:Height", "0", NS) or 0)
        if w * h > area:
            best, area = pr, w * h
    if best is None:
        raise OnvifError("No video profile || কোনো video profile নেই")
    src = best.find(".//tt:VideoSourceConfiguration/tt:SourceToken", NS)
    enc = best.find(".//tt:VideoEncoderConfiguration", NS)
    return (src.text.strip() if src is not None and src.text else None), enc


def _num(el, path):
    try:
        return float(el.findtext(path, "", NS))
    except (TypeError, ValueError):
        return None


def get_picture(host, user, password, port=None):
    svc = _services(host, user, password, port)
    src, enc = _main_profile(svc["media"], user, password)
    res = {"imaging": False, "encoder": None}
    # ---- encoder (main stream)
    if enc is not None:
        e = {"token": enc.get("token"), "encoding": (enc.findtext("tt:Encoding", "", NS) or "").upper(),
             "width": int(_num(enc, "tt:Resolution/tt:Width") or 0), "height": int(_num(enc, "tt:Resolution/tt:Height") or 0),
             "bitrate": int(_num(enc, "tt:RateControl/tt:BitrateLimit") or 0),
             "fps": int(_num(enc, "tt:RateControl/tt:FrameRateLimit") or 0), "resolutions": [], "bitrate_max": None}
        try:
            o = _call(svc["media"], '<GetVideoEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">'
                      f"<ConfigurationToken>{_esc(e['token'])}</ConfigurationToken></GetVideoEncoderConfigurationOptions>",
                      user, password)
            seen = set()
            for r in o.findall(".//tt:H264/tt:ResolutionsAvailable", NS):
                wh = (int(_num(r, "tt:Width") or 0), int(_num(r, "tt:Height") or 0))
                if all(wh) and wh not in seen:
                    seen.add(wh)
            e["resolutions"] = sorted(seen, key=lambda x: -x[0] * x[1])
            br = o.find(".//tt:Extension/tt:H264/tt:BitrateRange", NS)
            if br is not None:
                e["bitrate_max"] = int(_num(br, "tt:Max") or 0) or None
        except (OnvifError, OSError, ET.ParseError):
            pass
        # only plain H.264 configs can be changed safely over ONVIF media v1
        e["editable"] = e["encoding"] == "H264" and bool(e["resolutions"])
        res["encoder"] = e
    # ---- imaging
    if svc["imaging"] and src:
        try:
            s = _call(svc["imaging"], f'<GetImagingSettings xmlns="{NS["timg"]}"><VideoSourceToken>{_esc(src)}</VideoSourceToken></GetImagingSettings>', user, password)
            o = _call(svc["imaging"], f'<GetOptions xmlns="{NS["timg"]}"><VideoSourceToken>{_esc(src)}</VideoSourceToken></GetOptions>', user, password)
            st = s.find(".//timg:ImagingSettings", NS)
            op = o.find(".//timg:ImagingOptions", NS)
            if st is not None:
                res["imaging"] = True
                res["ir"] = st.findtext("tt:IrCutFilter", "", NS) or None
                res["wdr"] = st.findtext("tt:WideDynamicRange/tt:Mode", "", NS) or None
                res["brightness"] = _num(st, "tt:Brightness")
                res["saturation"] = _num(st, "tt:ColorSaturation")
                if op is not None:
                    res["ir_modes"] = [x.text for x in op.findall("tt:IrCutFilterModes", NS) if x.text]
                    res["wdr_modes"] = [x.text for x in op.findall("tt:WideDynamicRange/tt:Mode", NS) if x.text]
                    res["brightness_range"] = [_num(op, "tt:Brightness/tt:Min"), _num(op, "tt:Brightness/tt:Max")]
                    res["saturation_range"] = [_num(op, "tt:ColorSaturation/tt:Min"), _num(op, "tt:ColorSaturation/tt:Max")]
        except (OnvifError, OSError, ET.ParseError):
            pass
    return res


def set_picture(host, user, password, port=None, ir=None, wdr=None, brightness=None, saturation=None,
                width=None, height=None, bitrate=None):
    svc = _services(host, user, password, port)
    src, enc = _main_profile(svc["media"], user, password)
    if any(v is not None for v in (ir, wdr, brightness, saturation)):
        if not (svc["imaging"] and src):
            raise OnvifError("This camera has no ONVIF picture settings || এই camera-র ONVIF picture setting নেই")
        parts = []                                  # schema order matters
        if brightness is not None:
            parts.append(f'<Brightness xmlns="{TT}">{float(brightness):g}</Brightness>')
        if saturation is not None:
            parts.append(f'<ColorSaturation xmlns="{TT}">{float(saturation):g}</ColorSaturation>')
        if ir:
            parts.append(f'<IrCutFilter xmlns="{TT}">{_esc(ir)}</IrCutFilter>')
        if wdr:
            parts.append(f'<WideDynamicRange xmlns="{TT}"><Mode>{_esc(wdr)}</Mode></WideDynamicRange>')
        _call(svc["imaging"], f'<SetImagingSettings xmlns="{NS["timg"]}"><VideoSourceToken>{_esc(src)}</VideoSourceToken>'
              f"<ImagingSettings>{''.join(parts)}</ImagingSettings><ForcePersistence>true</ForcePersistence></SetImagingSettings>",
              user, password, timeout=5)
    if width or bitrate:
        if enc is None:
            raise OnvifError("No encoder settings || encoder setting নেই")
        tok = enc.get("token")
        g = _call(svc["media"], '<GetVideoEncoderConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">'
                  f"<ConfigurationToken>{_esc(tok)}</ConfigurationToken></GetVideoEncoderConfiguration>", user, password)
        cfg = g.find(".//trt:Configuration", NS)
        if cfg is None or (cfg.findtext("tt:Encoding", "", NS) or "").upper() != "H264":
            raise OnvifError("Resolution of this camera can only be changed in its own app || এই camera-র resolution শুধু তার নিজের app-এ বদলানো যায়")
        if width and height:
            cfg.find("tt:Resolution/tt:Width", NS).text = str(int(width))
            cfg.find("tt:Resolution/tt:Height", NS).text = str(int(height))
        b = cfg.find("tt:RateControl/tt:BitrateLimit", NS)
        if bitrate and b is not None:
            b.text = str(int(bitrate))
        cfg.tag = f"{{{TRT}}}Configuration"
        xml = ET.tostring(cfg, encoding="unicode")
        _call(svc["media"], f'<SetVideoEncoderConfiguration xmlns="{TRT}">{xml}<ForcePersistence>true</ForcePersistence>'
              "</SetVideoEncoderConfiguration>", user, password, timeout=8)
