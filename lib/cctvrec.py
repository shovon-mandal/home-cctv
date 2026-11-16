"""Recording layout helpers shared by the recorder, uploader, cleaner and web server.

<rec>/<cam>/<YYYY-MM-DD>/
    HH-MM-SS.mp4      continuous 1-minute segment (start time, local); on the SSD, finished
                      minutes are later joined into longer files (bin/cctv-merge), whose real
                      length is kept in .merged.json (same folder)
    HH-MM-SS.jpg      thumbnail of that segment
    m-HH-MM-SS.jpg    snapshot taken when motion started (from Motion)
    events.jsonl      {"e": "start"|"end"|"pic", "t": epoch, ["f": file]}
    low/HH-MM-SS.mp4  "smart" mode: 24/7 low-resolution copy (sub stream); the full-quality
                      segments above are then kept only around motion
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

SEG_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})\.mp4$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SEGMENT_SECONDS = int(os.environ.get("CCTV_SEGMENT_SECONDS", 60))
LOW = "low"
MERGE_INDEX = ".merged.json"     # {"HH-MM-SS.mp4": seconds} for joined (longer) files


def merged_index(folder):
    try:
        with open(os.path.join(folder, MERGE_INDEX), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_merged_index(folder, idx):
    tmp = os.path.join(folder, MERGE_INDEX + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f)
    os.replace(tmp, os.path.join(folder, MERGE_INDEX))


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
    d = os.path.join(cam_dir(rec, cam), datetime.fromtimestamp(now).strftime("%Y-%m-%d"))
    ev = read_events(d, now)
    return bool(ev) and ev[-1][1] is None


def segments(daydir, date, now=None, low=False):
    """List segments with start/end epochs. A segment's end is the next one's start
    (capped at SEGMENT_SECONDS+5), or its mtime for the newest one.
    low=True lists the low-resolution copies in <day>/low/."""
    now = now or time.time()
    if low:
        daydir = os.path.join(daydir, LOW)
    try:
        names = sorted(n for n in os.listdir(daydir) if SEG_RE.match(n))
    except OSError:
        return []
    idx = merged_index(daydir)
    segs = []
    for n in names:
        p = os.path.join(daydir, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        segs.append({"name": n, "path": p, "start": seg_epoch(date, n), "size": st.st_size, "mtime": st.st_mtime,
                     "low": low, "rel": f"{LOW}/{n}" if low else n, "merged": n in idx})
    for i, s in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else None
        if s["merged"]:                          # joined file: its length is known
            end = s["start"] + float(idx[s["name"]])
        elif nxt:
            end = min(nxt, s["start"] + SEGMENT_SECONDS + 5)
        else:
            end = max(s["start"] + 1, min(s["mtime"], now))
        s["end"] = end
        s["done"] = s["merged"] or nxt is not None or now - s["mtime"] > 30
    return segs


def merged_segments(daydir, date, now=None):
    """What to play: full-quality segments, and the low copy wherever no full one exists.
    A low file that is only partly covered (joined files are long) is cut into pieces:
    "fstart" is the start time of the file itself, "start"/"end" the part to play."""
    main = segments(daydir, date, now)
    for s in main:
        s["fstart"] = s["start"]
    cover = sorted((s["start"], s["end"]) for s in main)
    out = list(main)
    for s in segments(daydir, date, now, low=True):
        a = s["start"]
        for c0, c1 in cover:
            if c1 <= a or c0 >= s["end"]:
                continue
            if c0 - a >= 2:
                out.append(dict(s, start=a, end=c0, fstart=s["start"]))
            a = max(a, c1)
        if s["end"] - a >= 2:
            out.append(dict(s, start=a, end=s["end"], fstart=s["start"]))
    return sorted(out, key=lambda s: s["start"])


def overlaps(a0, a1, intervals):
    return any(a0 < (e if e is not None else time.time()) and a1 > s for s, e in intervals)


def motion_intervals(rec, cam, days=2, pre=5, post=5, now=None):
    """Motion intervals (padded) for the last `days` days, as [(start, end_or_None)]."""
    now = now or time.time()
    out = []
    for i in range(days, -1, -1):
        date = (datetime.fromtimestamp(now) - timedelta(days=i)).strftime("%Y-%m-%d")
        for s, e, _ in read_events(os.path.join(cam_dir(rec, cam), date), now):
            out.append((s - pre, (e + post) if e is not None else None))
    return out


# ---- camera folders: on disk (and on Drive) each camera's folder carries its NAME, e.g.
# "Imou Ranger 2 Pro/2026-11-16/..."; code keeps using the id (cam1). Old "cam1" folders still work.
_names = {"mtime": None, "map": {}}


def safe_folder(name):
    n = re.sub(r"[^\w \-().,&+]+", " ", name or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", n).strip(" .") or None


def folder_names():
    """{cam_id: folder name} from cameras.conf (cached until the file changes)."""
    try:
        import cctvconf
        path = cctvconf.CAMS_PATH
        mt = os.path.getmtime(path)
        if mt != _names["mtime"]:
            out = {}
            for c in cctvconf.load_cameras():
                n = safe_folder(c.get("name")) or c["id"]
                out[c["id"]] = n if n not in out.values() else f"{n} ({c['id']})"
            _names.update(mtime=mt, map=out)
    except Exception:  # noqa: BLE001  (no config / unreadable: fall back to ids)
        pass
    return _names["map"]


def cam_dir(rec, cam):
    """Folder of camera `cam` on storage `rec` (falls back to a legacy cam1-style folder)."""
    name = folder_names().get(cam, cam)
    p = os.path.join(rec, name)
    if name != cam and not os.path.isdir(p) and os.path.isdir(os.path.join(rec, cam)):
        return os.path.join(rec, cam)
    return p


def cams_in(rec):
    """Camera ids that have a folder on this storage."""
    try:
        present = set(d for d in os.listdir(rec) if os.path.isdir(os.path.join(rec, d)))
    except OSError:
        return []
    ids = {cid for cid, n in folder_names().items() if n in present or cid in present}
    ids |= {d for d in present if re.match(r"^cam\d+$", d)}      # removed cameras' old folders
    return sorted(ids)


def dates_of(rec, cam):
    try:
        return sorted((d for d in os.listdir(cam_dir(rec, cam)) if DATE_RE.match(d)), reverse=True)
    except OSError:
        return []


def all_segments(rec, now=None):
    """Every segment on this storage: dicts with cam/date/start/end/path/size."""
    out = []
    for cam in cams_in(rec):
        for date in dates_of(rec, cam):
            for low in (False, True):
                for s in segments(os.path.join(cam_dir(rec, cam), date), date, now, low):
                    s["cam"], s["date"] = cam, date
                    out.append(s)
    return out
