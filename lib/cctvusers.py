"""User accounts (admin / viewer) and signed login sessions.

users.json:
{ "users": { "name": { "hash": "pbkdf2$...", "role": "admin|viewer",
                       "cameras": ["cam1"] | "*", "recordings": true, "ver": 1 } } }
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

USERS_PATH = os.environ.get("CCTV_USERS", "/etc/cctv/auth/users.json")
SECRET_PATH = os.environ.get("CCTV_SECRET", "/etc/cctv/auth/secret")
ITER = 60000          # pbkdf2 rounds: ~0.3 s on a Tinker Board
SESSION_DAYS = 30
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,32}$")

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}


# ---------------- passwords ----------------
def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, ITER)
    return f"pbkdf2${ITER}${salt.hex()}${dk.hex()}"


def check_password(pw, stored):
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, AttributeError):
        return False


# ---------------- storage ----------------
def load():
    with _lock:
        try:
            m = os.stat(USERS_PATH).st_mtime_ns
        except FileNotFoundError:
            return {"users": {}}
        if _cache["mtime"] != m:
            with open(USERS_PATH, encoding="utf-8") as f:
                _cache["data"] = json.load(f)
            _cache["mtime"] = m
        return json.loads(json.dumps(_cache["data"]))   # deep copy


def save(data):
    with _lock:
        tmp = USERS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, USERS_PATH)
        _cache["mtime"] = None


def set_user(data, name, password=None, role="viewer", cameras="*", recordings=True, ptz=None):
    if not NAME_RE.match(name):
        raise ValueError("Username: 2-32 characters, only a-z 0-9 _ . - || username: 2-32 অক্ষর, শুধু a-z 0-9 _ . -")
    if role not in ("admin", "viewer"):
        raise ValueError("role must be admin or viewer")
    u = data["users"].get(name)
    if u is None:
        if not password:
            raise ValueError("A new user needs a password || নতুন user-এর password লাগবে")
        u = {"ver": 0}
    if password:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters || password কমপক্ষে ৮ অক্ষর")
        u["hash"] = hash_password(password)
        u["ver"] = u.get("ver", 0) + 1          # logs out old sessions
    u["role"] = role
    u["cameras"] = "*" if role == "admin" or cameras in ("*", None) else sorted(set(cameras))
    u["recordings"] = True if role == "admin" else bool(recordings)
    if ptz is not None or "ptz" not in u:
        u["ptz"] = True if role == "admin" else bool(ptz)
    if role == "admin":
        u["ptz"] = True
    data["users"][name] = u
    if not any(x["role"] == "admin" for x in data["users"].values()):
        raise ValueError("There must be at least one admin || কমপক্ষে একজন admin থাকতে হবে")
    return data


def delete_user(data, name):
    if name not in data["users"]:
        raise ValueError("No such user || user নেই")
    rest = {k: v for k, v in data["users"].items() if k != name}
    if not any(x["role"] == "admin" for x in rest.values()):
        raise ValueError("The last admin cannot be deleted || শেষ admin মোছা যাবে না")
    data["users"] = rest
    return data


def public_view(name, u):
    return {"name": name, "role": u["role"], "cameras": u.get("cameras", "*"),
            "recordings": u.get("recordings", True),
            "ptz": u["role"] == "admin" or u.get("ptz", False), "lang": u.get("lang", "en")}


def set_lang(data, name, lang):
    if lang not in ("en", "bn"):
        raise ValueError("lang must be en or bn")
    data["users"][name]["lang"] = lang
    return data


# ---------------- sessions ----------------
def _secret():
    try:
        with open(SECRET_PATH, "rb") as f:
            s = f.read()
            if len(s) >= 32:
                return s
    except FileNotFoundError:
        pass
    s = secrets.token_bytes(32)
    with open(SECRET_PATH, "wb") as f:
        f.write(s)
    os.chmod(SECRET_PATH, 0o600)
    return s


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(name, u):
    body = _b64(json.dumps({"u": name, "v": u.get("ver", 0),
                            "exp": int(time.time()) + SESSION_DAYS * 86400}).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def check_token(token):
    """Returns (name, user) or None."""
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            return None
        d = json.loads(_unb64(body))
        if d["exp"] < time.time():
            return None
        u = load()["users"].get(d["u"])
        if not u or u.get("ver", 0) != d["v"]:
            return None
        return d["u"], u
    except (ValueError, KeyError, TypeError):
        return None


def can_see(u, cam_id):
    cams = u.get("cameras", "*")
    return cams == "*" or cam_id in cams
