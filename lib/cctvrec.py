"""Recording layout helpers shared by the recorder, uploader, cleaner and web server.

<rec>/<cam>/<YYYY-MM-DD>/
    HH-MM-SS.mp4      continuous 1-minute segment (start time, local)
    HH-MM-SS.jpg      thumbnail of that segment
    m-HH-MM-SS.jpg    snapshot taken when motion started (from Motion)
    events.jsonl      {"e": "start"|"end"|"pic", "t": epoch, ["f": file]}
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

SEG_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\.mp4$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEGMENT_SECONDS = int(os.environ.get("CCTV_SEGMENT_SECONDS", 60))


def seg_epoch(date, name):
    m = SEG_RE.match(name)
    if not m:
        return None
    return time.mktime(time.strptime(f"{date} {m.group(1)}:{m.group(2)}:{m.group(3)}", "%Y-%m-%d %H:%M:%S"))


def midnight(date):
    return time.mktime(time.strptime(date, "%Y-%m-%d"))


def read_events(daydir, now=None):
    """Return [(start, end, pic)] epochs; an open event ends at `now`."""
    now = now or time.time()
    out, cur = [], None
    try:
        with open(os.path.join(daydir, "events.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("e") == "start":
                    if cur:                      # previous event never got an "end" (restart/crash)
                        cur[1] = max(cur[0], e["t"])
                        out.append(cur)
                    cur = [e["t"], None, None]
                elif e.get("e") == "end" and cur:
                    cur[1] = max(cur[0], e["t"])
                    out.append(cur)
                    cur = None
                elif e.get("e") == "pic":
                    tgt = cur or (out[-1] if out else None)
                    if tgt and not tgt[2]:
                        tgt[2] = e.get("f")
    except OSError:
        pass
    if cur:
        # open event: still going, unless it is from long ago (crash) -> cap at 10 min
        cur[1] = None if now - cur[0] < 600 else cur[0] + 600
        out.append(cur)
    return [tuple(x) for x in out]


def motion_active(rec, cam, now=None):
    now = now or time.time()
    d = os.path.join(rec, cam, datetime.fromtimestamp(now).strftime("%Y-%m-%d"))
    ev = read_events(d, now)
    return bool(ev) and ev[-1][1] is None


def segments(daydir, date, now=None):
    """List segments with start/end epochs. A segment's end is the next one's start
    (capped at SEGMENT_SECONDS+5), or its mtime for the newest one."""
    now = now or time.time()
    try:
        names = sorted(n for n in os.listdir(daydir) if SEG_RE.match(n))
    except OSError:
        return []
    segs = []
    for n in names:
        p = os.path.join(daydir, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        segs.append({"name": n, "path": p, "start": seg_epoch(date, n), "size": st.st_size, "mtime": st.st_mtime})
    for i, s in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else None
        end = min(nxt, s["start"] + SEGMENT_SECONDS + 5) if nxt else max(s["start"] + 1, min(s["mtime"], now))
        s["end"] = end
        s["done"] = nxt is not None or now - s["mtime"] > 30
    return segs


def overlaps(a0, a1, intervals):
    return any(a0 < (e if e is not None else time.time()) and a1 > s for s, e in intervals)


def motion_intervals(rec, cam, days=2, pre=5, post=5, now=None):
    """Motion intervals (padded) for the last `days` days, as [(start, end_or_None)]."""
    now = now or time.time()
    out = []
    for i in range(days, -1, -1):
        date = (datetime.fromtimestamp(now) - timedelta(days=i)).strftime("%Y-%m-%d")
        for s, e, _ in read_events(os.path.join(rec, cam, date), now):
            out.append((s - pre, (e + post) if e is not None else None))
    return out


def cams_in(rec):
    try:
        return sorted(d for d in os.listdir(rec) if re.match(r"^cam\d+$", d) and os.path.isdir(os.path.join(rec, d)))
    except OSError:
        return []


def dates_of(rec, cam):
    try:
        return sorted((d for d in os.listdir(os.path.join(rec, cam)) if DATE_RE.match(d)), reverse=True)
    except OSError:
        return []


def all_segments(rec, now=None):
    """Every segment on this storage: dicts with cam/date/start/end/path/size."""
    out = []
    for cam in cams_in(rec):
        for date in dates_of(rec, cam):
            for s in segments(os.path.join(rec, cam, date), date, now):
                s["cam"], s["date"] = cam, date
                out.append(s)
    return out
