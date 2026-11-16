"""Find cameras on the local network (stdlib only).

scan(subnet="192.168.1") -> [{"ip": "192.168.1.20", "ports": [554], "onvif": True, "name": "..."}, ...]

Two methods, combined:
  - ONVIF WS-Discovery: a multicast "who is there?" that ONVIF cameras answer
    (does not cross Docker's network on Windows/macOS, so we also do...)
  - a quick TCP check of the usual RTSP ports on every address of the /24 subnet.
"""
import concurrent.futures
import re
import socket
import uuid

RTSP_PORTS = (554, 8554, 10554, 88)

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<e:Header><w:MessageID>uuid:{id}</w:MessageID>
<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>
<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>"""


def local_subnet():
    """'192.168.1' for the interface that has the default route ('' if unknown)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))     # no packet is sent
        ip = s.getsockname()[0]
        s.close()
        return ip.rsplit(".", 1)[0]
    except OSError:
        return ""


def ws_discovery(timeout=2.5):
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(timeout)
        s.sendto(PROBE.format(id=uuid.uuid4()).encode(), ("239.255.255.250", 3702))
        while True:
            data, (ip, _) = s.recvfrom(65535)
            text = data.decode("utf-8", "replace")
            scopes = " ".join(re.findall(r"<[^>]*Scopes[^>]*>([^<]*)<", text))
            name = ""
            for key in ("name", "hardware"):
                m = re.search(r"onvif://www\.onvif\.org/" + key + r"/(\S+)", scopes)
                if m:
                    name = (name + " " + m.group(1).replace("%20", " ")).strip()
            xaddr = re.search(r"<[^>]*XAddrs[^>]*>\s*(\S+)", text)
            port = None
            if xaddr:
                m = re.match(r"https?://[^/:]+:(\d+)", xaddr.group(1))
                port = int(m.group(1)) if m else 80
            found[ip] = {"name": name, "onvif_port": port}
    except OSError:
        pass
    return found


def _open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan(subnet=None, timeout=0.6):
    subnet = (subnet or local_subnet()).strip().rstrip(".")
    if subnet.count(".") == 3:                    # "192.168.1.0" or "192.168.1.0/24"
        subnet = subnet.split("/")[0].rsplit(".", 1)[0]
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$", subnet or ""):
        raise ValueError("Enter the network as three numbers, e.g. 192.168.1")
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        disc = ex.submit(ws_discovery)
        jobs = {ex.submit(_open, f"{subnet}.{i}", p, timeout): (f"{subnet}.{i}", p)
                for i in range(1, 255) for p in RTSP_PORTS}
        hits = {}
        for fut, (ip, p) in jobs.items():
            if fut.result():
                hits.setdefault(ip, []).append(p)
        onvif = disc.result()
    out = []
    for ip in sorted(set(hits) | set(onvif), key=lambda x: tuple(int(n) for n in x.split("."))):
        o = onvif.get(ip, {})
        out.append({"ip": ip, "ports": sorted(hits.get(ip, [])), "onvif": ip in onvif,
                    "onvif_port": o.get("onvif_port"), "name": o.get("name", "")})
    return {"subnet": subnet, "cameras": out}
