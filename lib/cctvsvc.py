"""Start/stop/restart the CCTV services, wherever the system runs.

- Native Linux install: the services are systemd units (cctv-go2rtc, cctv-motion, ...).
- Docker: there is no systemd; bin/cctv-supervisor runs the services and picks up
  requests from small files in <run>/ctl/ (set CCTV_SUPERVISOR=1).
"""
import json
import os
import subprocess
import time

RUN = os.environ.get("CCTV_RUN", "/run/cctv")
SUPERVISED = os.environ.get("CCTV_SUPERVISOR") == "1"
CTL = os.path.join(RUN, "ctl")


def _unit(name):
    """'cctv-motion' or 'motion' -> 'motion' (supervisor name)."""
    return name[5:] if name.startswith("cctv-") else name


def control(action, *units):
    """action: start | stop | restart"""
    if not units:
        return
    if SUPERVISED:
        os.makedirs(CTL, exist_ok=True)
        for u in units:
            path = os.path.join(CTL, f"{_unit(u)}.{action}")
            with open(path + ".tmp", "w") as f:
                f.write(str(time.time()))
            os.replace(path + ".tmp", path)
        return
    subprocess.call(["systemctl", action, *units])


def is_active(unit):
    if SUPERVISED:
        try:
            with open(os.path.join(RUN, "services.json")) as f:
                return json.load(f).get(_unit(unit)) == "active"
        except (OSError, ValueError):
            return False
    return subprocess.call(["systemctl", "is-active", "--quiet", unit]) == 0
