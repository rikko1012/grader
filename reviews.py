"""reviews.py - member reviews (/reviews): one review per paid Whop member, optional photos, every rating published.

Rules (FTC consumer-review rule; owner 2026-11-15): every review is posted straight away, 1-3 stars included. A review is
hidden only for spam, slurs/abuse or personal info - the reason is recorded (mod_log) and the same rule applies to every
rating. The average and the star breakdown are computed from every published review. One review per member, editable, the
edit date shown. No incentives. user_id is stored only to tie a review to its author and is never returned by any API.

Storage: reviews.db (its own SQLite file, WAL) and review_photos/ in workspace_api.data_dir(). Reads never create the
database: with no review written yet every read answers an empty page. Photos are re-encoded server-side (Pillow, imported
on the first upload only): EXIF/GPS and every other metadata dropped, <= 1600 px full + <= 480 px thumbnail, one decode
at a time. File names are a hash of (member, bytes), so two members' identical photos never share a file.

Switches, read per request: REVIEWS_PAGE=0 -> every public reviews route 404 (owner tools still work);
REVIEWS_WRITE=0 -> page readable, every member write refused; REVIEWS_PHOTOS=0 -> photo upload refused, upload UI hidden.

Nothing here grades, reads a forecast or touches the model.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------- limits
STARS = (1, 2, 3, 4, 5)
BODY_MIN, BODY_MAX, TITLE_MAX, REPLY_MAX, NOTE_MAX = 10, 1500, 80, 1500, 300
MAX_PHOTOS = 3
PAGE_SIZE = 20
MAX_PAGE = 500
JSON_MAX = 8 * 1024                     # every reviews JSON body (a 1,500-char review in UTF-8 is far below this)
PHOTO_MAX = 8 * 1024 * 1024             # raw upload cap (the page sends a <= 1600 px JPEG, normally < 1 MB)
FULL_PX, THUMB_PX, QUALITY = 1600, 480, 82
MAX_PIXELS = 24_000_000                 # JPEG: refused above this before decoding (draft() then decodes at a reduced scale)
MAX_PIXELS_OTHER = 10_000_000           # PNG / WEBP decode at full size (RGBA 4 bytes/px): a tighter cap keeps one decode < ~40 MB
FORMATS = ("JPEG", "PNG", "WEBP")
UPLOAD_TYPES = ("image/jpeg", "image/png", "image/webp", "application/octet-stream")
PHOTOS_CAP_BYTES = 300 * 1024 * 1024    # review_photos/ total
MIN_FREE_BYTES = 800 * 1024 * 1024      # the /data disk keeps this much free
ORPHAN_S = 24 * 3600                    # an uploaded photo never attached to a review is removed after this
UPLOADS_PER_HOUR = 10
WRITES_PER_HOUR = 30
UPLOAD_SLOTS = 2                        # uploads whose body may be in memory at once
HIDE_REASONS = {"spam": "Spam", "abuse": "Slurs or abuse", "personal_info": "Personal info"}
SORTS = {"new": "created_at DESC, id DESC", "high": "stars DESC, created_at DESC, id DESC",
         "low": "stars ASC, created_at DESC, id DESC"}
OWNER_NAME = "1Time (owner)"
PHOTO_RE = re.compile(r"^/reviews/p/([0-9a-f]{32})(_t)?\.jpg$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
NEXT_PAGES = {"reviews": "/reviews"}    # /auth/login?next=<key>: a fixed allow-list, never a raw URL
NEXT_COOKIE = "cs2s_next"

GEN = [0]                               # bumped on every write: part of every cache key, so a write is seen at once
_WRITE = threading.Lock()
_DECODE = threading.Lock()
_SLOTS = threading.BoundedSemaphore(UPLOAD_SLOTS)
_RATE, _RATE_LOCK = {}, threading.Lock()
_BLOCKED = {"gen": None, "ids": frozenset()}
_SWEEP = {"at": 0.0}
_INIT = set()


class Refused(Exception):
    """A request the rules refuse: (HTTP status, plain message for the member)."""
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status, self.msg = status, msg


def _off(flag):
    return os.environ.get(flag, "").strip().lower() in ("0", "false", "no", "off")


def page_on():
    return not _off("REVIEWS_PAGE")


def write_on():
    return page_on() and not _off("REVIEWS_WRITE")


def photos_on():
    return write_on() and not _off("REVIEWS_PHOTOS")


# ---------------------------------------------------------------- storage
def data_dir():
    import workspace_api
    return Path(workspace_api.data_dir())


def db_path():
    return data_dir() / "reviews.db"


def photo_dir():
    return data_dir() / "review_photos"


SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  name_style TEXT NOT NULL DEFAULT 'short',
  stars INTEGER NOT NULL CHECK(stars BETWEEN 1 AND 5),
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  photos TEXT NOT NULL DEFAULT '[]',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  hidden INTEGER NOT NULL DEFAULT 0,
  hidden_reason TEXT,
  hidden_note TEXT,
  hidden_at INTEGER,
  owner_reply TEXT,
  owner_reply_at INTEGER);
CREATE INDEX IF NOT EXISTS reviews_pub ON reviews(hidden, created_at);
CREATE TABLE IF NOT EXISTS photos(
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS photos_user ON photos(user_id);
CREATE TABLE IF NOT EXISTS mod_log(
  ts INTEGER NOT NULL, review_id INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT, note TEXT, stars INTEGER);
"""


def _connect(create=False):
    """A connection per operation (closed by the caller). create=False and no database yet -> None (reads never create it)."""
    p = db_path()
    if not create and not p.exists():
        return None
    if create:
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    key = str(p)
    if key not in _INIT:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        _INIT.add(key)
    return con


def _bump():
    GEN[0] += 1


def _now():
    return int(time.time())


def _iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z") if ts else None


# ---------------------------------------------------------------- text
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


def clean_line(s, cap):
    s = _CTRL.sub(" ", str(s or "").replace("\n", " ").replace("\r", " ").replace("\t", " "))
    return re.sub(r"\s+", " ", s).strip()[:cap]


def clean_body(s):
    s = str(s or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    s = _CTRL.sub("", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def names(whop_name):
    """-> {'short', 'full'} from the Whop session name. short = first name + last initial ("Sam K."); a one-word handle stays
    as it is; empty or email-like -> "Prop Grader member"."""
    n = clean_line(whop_name, 60)
    if not n or "@" in n:
        return {"short": "Prop Grader member", "full": "Prop Grader member"}
    parts = n.split(" ")
    short = f"{parts[0][:30]} {parts[-1][0].upper()}." if len(parts) >= 2 else parts[0][:30]
    return {"short": short, "full": n}


# ---------------------------------------------------------------- photos
def _photo_files(pid):
    d = photo_dir()
    return d / f"{pid}.jpg", d / f"{pid}_t.jpg"


def photo_urls(pid):
    return {"id": pid, "thumb": f"/reviews/p/{pid}_t.jpg", "full": f"/reviews/p/{pid}.jpg"}


def process_image(data):
    """Raw upload bytes -> (full JPEG bytes, thumbnail JPEG bytes). Refuses anything that is not a JPEG / PNG / WEBP (by
    content, never the name), or larger than the pixel cap (checked from the header, before decoding). The output carries no
    metadata at all (no EXIF, no GPS, no ICC). One decode at a time."""
    import warnings
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)   # the w*h check below refuses these itself
    if not _DECODE.acquire(timeout=20):
        raise Refused(503, "Photo uploads are busy - try again in a moment.")
    img = None
    try:
        try:
            img = Image.open(BytesIO(data), formats=list(FORMATS))
        except Image.DecompressionBombError:            # the header alone names far more pixels than the cap
            raise Refused(413, "That photo is too large - pick a smaller one.")
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
            raise Refused(415, "That file isn't a photo we can use - send a JPG, PNG or WEBP.")
        if img.format not in FORMATS:
            raise Refused(415, "That file isn't a photo we can use - send a JPG, PNG or WEBP.")
        w, h = img.size
        if w < 1 or h < 1 or w * h > (MAX_PIXELS if img.format == "JPEG" else MAX_PIXELS_OTHER):
            raise Refused(413, "That photo is too large - pick a smaller one.")
        try:
            if img.format == "JPEG":
                img.draft("RGB", (FULL_PX, FULL_PX))
            out = ImageOps.exif_transpose(img)
            if out is None:
                out = img
            if out.mode in ("RGBA", "LA") or (out.mode == "P" and "transparency" in out.info):
                rgba = out.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (12, 16, 22))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                out = bg
            else:
                out = out.convert("RGB")
            out.thumbnail((FULL_PX, FULL_PX), Image.LANCZOS)
            out.info = {}
            full = BytesIO()
            out.save(full, "JPEG", quality=QUALITY, optimize=True, progressive=True)
            thumb_img = out.copy()
            thumb_img.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
            thumb_img.info = {}
            thumb = BytesIO()
            thumb_img.save(thumb, "JPEG", quality=QUALITY, optimize=True)
            thumb_img.close()
            out.close()
        except Refused:
            raise
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise Refused(415, "That photo couldn't be read - try another one.")
        return full.getvalue(), thumb.getvalue()
    finally:
        if img is not None:
            img.close()
        _DECODE.release()


def storage_ok():
    """False when review photos already use PHOTOS_CAP_BYTES or the data disk has under MIN_FREE_BYTES free."""
    used = 0
    con = _connect()
    if con is not None:
        try:
            used = con.execute("SELECT COALESCE(SUM(bytes),0) FROM photos").fetchone()[0]
        finally:
            con.close()
    if used >= PHOTOS_CAP_BYTES:
        return False
    try:
        d = data_dir()
        d.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(str(d)).free >= MIN_FREE_BYTES
    except OSError:
        return False


def _write_file(path, data):
    tmp = path.with_suffix(f".tmp{threading.get_ident()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _unlink_photo(pid):
    for f in _photo_files(pid):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def save_photo(user_id, data):
    """Re-encode + store one upload for this member -> photo dict (id + urls)."""
    full, thumb = process_image(data)
    pid = hashlib.sha256(user_id.encode("utf-8") + b"\0" + full).hexdigest()[:32]
    d = photo_dir()
    d.mkdir(parents=True, exist_ok=True)
    f_full, f_thumb = _photo_files(pid)
    with _WRITE:
        _write_file(f_full, full)
        _write_file(f_thumb, thumb)
        con = _connect(create=True)
        try:
            with con:
                con.execute("INSERT OR IGNORE INTO photos(id,user_id,bytes,created_at) VALUES(?,?,?,?)",
                            (pid, user_id, len(full) + len(thumb), _now()))
        finally:
            con.close()
        _bump()
    return photo_urls(pid)


def sweep_orphans(force=False):
    """Photos uploaded but never attached to the uploader's review within ORPHAN_S are removed (at most once an hour)."""
    if not force and time.time() - _SWEEP["at"] < 3600:
        return 0
    _SWEEP["at"] = time.time()
    con = _connect()
    if con is None:
        return 0
    gone = []
    with _WRITE:
        try:
            attached = set()
            for r in con.execute("SELECT photos FROM reviews"):
                attached.update(_photo_list(r["photos"]))
            for r in con.execute("SELECT id FROM photos WHERE created_at < ?", (_now() - ORPHAN_S,)):
                if r["id"] not in attached:
                    gone.append(r["id"])
            with con:
                con.executemany("DELETE FROM photos WHERE id=?", [(g,) for g in gone])
        finally:
            con.close()
        for g in gone:
            _unlink_photo(g)
        if gone:
            _bump()
    return len(gone)


def _photo_list(raw):
    try:
        v = json.loads(raw or "[]")
        return [p for p in v if isinstance(p, str) and ID_RE.match(p)] if isinstance(v, list) else []
    except ValueError:
        return []


def blocked_photos():
    """Photo ids of hidden reviews (never served while hidden). Recomputed once per write generation."""
    if _BLOCKED["gen"] == (GEN[0], str(db_path())):
        return _BLOCKED["ids"]
    ids = set()
    con = _connect()
    if con is not None:
        try:
            for r in con.execute("SELECT photos FROM reviews WHERE hidden=1"):
                ids.update(_photo_list(r["photos"]))
        finally:
            con.close()
    _BLOCKED.update(gen=(GEN[0], str(db_path())), ids=frozenset(ids))
    return _BLOCKED["ids"]


def photo_bytes(pid, thumb):
    """The stored JPEG for a photo id, or None (unknown, removed, or on a hidden review)."""
    if not ID_RE.match(pid or "") or pid in blocked_photos():
        return None
    f = _photo_files(pid)[1 if thumb else 0]
    try:
        return f.read_bytes()
    except OSError:
        return None


# ---------------------------------------------------------------- reviews
def _public(r):
    edited = r["updated_at"] if r["updated_at"] and r["updated_at"] > r["created_at"] else None
    out = {"id": r["id"], "name": r["display_name"], "stars": r["stars"], "title": r["title"] or "", "body": r["body"],
           "photos": [photo_urls(p) for p in _photo_list(r["photos"])], "created": _iso(r["created_at"]),
           "edited": _iso(edited), "verified": True,
           "reply": {"name": OWNER_NAME, "text": r["owner_reply"], "at": _iso(r["owner_reply_at"])} if r["owner_reply"] else None}
    return out


def summary(con=None):
    """{count, average, breakdown {'5'..'1'}} over EVERY published review."""
    own = con is None
    con = con or _connect()
    br = {str(s): 0 for s in (5, 4, 3, 2, 1)}
    if con is None:
        return {"count": 0, "average": None, "breakdown": br}
    try:
        for r in con.execute("SELECT stars, COUNT(*) n FROM reviews WHERE hidden=0 GROUP BY stars"):
            br[str(r["stars"])] = r["n"]
    finally:
        if own:
            con.close()
    n = sum(br.values())
    avg = round(sum(int(k) * v for k, v in br.items()) / n, 1) if n else None
    return {"count": n, "average": avg, "breakdown": br}


def public_page(sort="new", page=0):
    con = _connect()
    if con is None:
        return {"summary": summary(None), "reviews": [], "sort": sort, "page": page, "more": False}
    try:
        s = summary(con)
        rows = con.execute(f"SELECT * FROM reviews WHERE hidden=0 ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
                           (PAGE_SIZE + 1, page * PAGE_SIZE)).fetchall()
    finally:
        con.close()
    return {"summary": s, "reviews": [_public(r) for r in rows[:PAGE_SIZE]], "sort": sort, "page": page,
            "more": len(rows) > PAGE_SIZE}


def own_review(user_id):
    con = _connect()
    if con is None:
        return None
    try:
        r = con.execute("SELECT * FROM reviews WHERE user_id=?", (user_id,)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    out = _public(r)
    out["name_style"] = r["name_style"]
    out["hidden"] = bool(r["hidden"])
    if r["hidden"]:
        out["hidden_reason"] = HIDE_REASONS.get(r["hidden_reason"], "")
    return out


def own_photo_ids(con, user_id):
    return {r["id"] for r in con.execute("SELECT id FROM photos WHERE user_id=?", (user_id,))}


def validate(d):
    """The member's JSON -> cleaned fields, or Refused(400)."""
    if not isinstance(d, dict):
        raise Refused(400, "Bad request.")
    stars = d.get("stars")
    if isinstance(stars, bool) or not isinstance(stars, int) or stars not in STARS:
        raise Refused(400, "Pick 1 to 5 stars.")
    title = d.get("title") or ""
    body = d.get("body")
    if not isinstance(title, str) or not isinstance(body, str):
        raise Refused(400, "Bad request.")
    title = clean_line(title, 10_000)
    if len(title) > TITLE_MAX:
        raise Refused(400, f"Keep the title to {TITLE_MAX} characters.")
    body = clean_body(body)
    if len(body) < BODY_MIN:
        raise Refused(400, f"Write at least {BODY_MIN} characters.")
    if len(body) > BODY_MAX:
        raise Refused(400, f"Keep the review to {BODY_MAX} characters.")
    style = d.get("name_style") or "short"
    if style not in ("short", "full"):
        raise Refused(400, "Bad request.")
    photos = d.get("photos") or []
    if not isinstance(photos, list) or len(photos) > MAX_PHOTOS or not all(isinstance(p, str) and ID_RE.match(p) for p in photos):
        raise Refused(400, f"Up to {MAX_PHOTOS} photos.")
    if len(set(photos)) != len(photos):
        raise Refused(400, "The same photo is attached twice.")
    return {"stars": stars, "title": title, "body": body, "name_style": style, "photos": photos}


def upsert(user_id, whop_name, d):
    """Create or update the member's one review. -> (own review dict, created: bool)."""
    f = validate(d)
    display = names(whop_name)[f["name_style"]]
    now = _now()
    with _WRITE:
        con = _connect(create=True)
        try:
            mine = own_photo_ids(con, user_id)
            if any(p not in mine or not _photo_files(p)[0].exists() for p in f["photos"]):
                raise Refused(400, "A photo is missing - add it again.")
            old = con.execute("SELECT id, photos, stars, title, body, name_style FROM reviews WHERE user_id=?", (user_id,)).fetchone()
            with con:
                if old is None:
                    con.execute("INSERT INTO reviews(user_id,display_name,name_style,stars,title,body,photos,created_at,updated_at)"
                                " VALUES(?,?,?,?,?,?,?,?,?)", (user_id, display, f["name_style"], f["stars"], f["title"], f["body"],
                                                                json.dumps(f["photos"]), now, now))
                else:
                    same = (old["stars"], old["title"], old["body"], old["name_style"], _photo_list(old["photos"])) == \
                           (f["stars"], f["title"], f["body"], f["name_style"], f["photos"])
                    con.execute("UPDATE reviews SET display_name=?, name_style=?, stars=?, title=?, body=?, photos=?,"
                                " updated_at=CASE WHEN ? THEN updated_at ELSE ? END WHERE user_id=?",
                                (display, f["name_style"], f["stars"], f["title"], f["body"], json.dumps(f["photos"]),
                                 1 if same else 0, now, user_id))
                    dropped = [p for p in _photo_list(old["photos"]) if p not in f["photos"]]
                    con.executemany("DELETE FROM photos WHERE id=? AND user_id=?", [(p, user_id) for p in dropped])
                    for p in dropped:
                        _unlink_photo(p)
        finally:
            con.close()
        _bump()
    return own_review(user_id), old is None


def delete_own(user_id):
    """The member deletes their review (and its photos). -> True if there was one."""
    with _WRITE:
        con = _connect()
        if con is None:
            return False
        try:
            r = con.execute("SELECT photos FROM reviews WHERE user_id=?", (user_id,)).fetchone()
            if not r:
                return False
            pics = _photo_list(r["photos"])
            with con:
                con.execute("DELETE FROM reviews WHERE user_id=?", (user_id,))
                con.executemany("DELETE FROM photos WHERE id=? AND user_id=?", [(p, user_id) for p in pics])
        finally:
            con.close()
        for p in pics:
            _unlink_photo(p)
        _bump()
    return True


def delete_own_photo(user_id, pid):
    """The member removes one of their own photos (attached or not). -> True if it was theirs."""
    if not ID_RE.match(pid or ""):
        return False
    with _WRITE:
        con = _connect()
        if con is None:
            return False
        try:
            if not con.execute("SELECT 1 FROM photos WHERE id=? AND user_id=?", (pid, user_id)).fetchone():
                return False
            r = con.execute("SELECT photos FROM reviews WHERE user_id=?", (user_id,)).fetchone()
            with con:
                con.execute("DELETE FROM photos WHERE id=? AND user_id=?", (pid, user_id))
                if r and pid in _photo_list(r["photos"]):
                    con.execute("UPDATE reviews SET photos=? WHERE user_id=?",
                                (json.dumps([p for p in _photo_list(r["photos"]) if p != pid]), user_id))
        finally:
            con.close()
        _unlink_photo(pid)
        _bump()
    return True


# ---------------------------------------------------------------- owner tools
def admin_list():
    con = _connect()
    if con is None:
        return {"summary": summary(None), "reviews": [], "log": []}
    try:
        rows = con.execute("SELECT * FROM reviews ORDER BY created_at DESC, id DESC").fetchall()
        log = con.execute("SELECT * FROM mod_log ORDER BY ts DESC LIMIT 200").fetchall()
        s = summary(con)
    finally:
        con.close()
    out = []
    for r in rows:
        x = _public(r)
        x.update(hidden=bool(r["hidden"]), hidden_reason=r["hidden_reason"], hidden_note=r["hidden_note"],
                 hidden_at=_iso(r["hidden_at"]), name_style=r["name_style"])
        out.append(x)
    return {"summary": s, "reviews": out, "reasons": HIDE_REASONS,
            "log": [{"at": _iso(l["ts"]), "review": l["review_id"], "action": l["action"], "reason": l["reason"],
                     "note": l["note"], "stars": l["stars"]} for l in log]}


def admin_action(d):
    """Owner JSON {action: reply|hide|unhide|remove_photo, id, ...} -> {"ok": True}."""
    if not isinstance(d, dict):
        raise Refused(400, "Bad request.")
    action, rid = d.get("action"), d.get("id")
    if isinstance(rid, bool) or not isinstance(rid, int):
        raise Refused(400, "Bad review id.")
    now = _now()
    with _WRITE:
        con = _connect()
        if con is None:
            raise Refused(404, "No such review.")
        try:
            r = con.execute("SELECT * FROM reviews WHERE id=?", (rid,)).fetchone()
            if not r:
                raise Refused(404, "No such review.")
            gone = []
            with con:
                if action == "reply":
                    text = clean_body(d.get("text") if isinstance(d.get("text"), str) else "")
                    if len(text) > REPLY_MAX:
                        raise Refused(400, f"Keep the reply to {REPLY_MAX} characters.")
                    con.execute("UPDATE reviews SET owner_reply=?, owner_reply_at=? WHERE id=?",
                                (text or None, now if text else None, rid))
                    con.execute("INSERT INTO mod_log VALUES(?,?,?,?,?,?)", (now, rid, "reply" if text else "reply_removed", None, None, r["stars"]))
                elif action == "hide":
                    reason = d.get("reason")
                    if reason not in HIDE_REASONS:
                        raise Refused(400, "Choose a reason: spam, slurs/abuse or personal info.")
                    note = clean_line(d.get("note") if isinstance(d.get("note"), str) else "", NOTE_MAX)
                    con.execute("UPDATE reviews SET hidden=1, hidden_reason=?, hidden_note=?, hidden_at=? WHERE id=?",
                                (reason, note, now, rid))
                    con.execute("INSERT INTO mod_log VALUES(?,?,?,?,?,?)", (now, rid, "hide", reason, note, r["stars"]))
                elif action == "unhide":
                    con.execute("UPDATE reviews SET hidden=0, hidden_reason=NULL, hidden_note=NULL, hidden_at=NULL WHERE id=?", (rid,))
                    con.execute("INSERT INTO mod_log VALUES(?,?,?,?,?,?)", (now, rid, "unhide", None, None, r["stars"]))
                elif action == "remove_photo":
                    pid = d.get("photo")
                    pics = _photo_list(r["photos"])
                    if not isinstance(pid, str) or pid not in pics:
                        raise Refused(404, "No such photo on this review.")
                    reason = d.get("reason") if d.get("reason") in HIDE_REASONS else None
                    con.execute("UPDATE reviews SET photos=? WHERE id=?", (json.dumps([p for p in pics if p != pid]), rid))
                    con.execute("DELETE FROM photos WHERE id=?", (pid,))
                    con.execute("INSERT INTO mod_log VALUES(?,?,?,?,?,?)", (now, rid, "remove_photo", reason, pid[:8], r["stars"]))
                    gone.append(pid)
                else:
                    raise Refused(400, "Unknown action.")
        finally:
            con.close()
        for p in gone:
            _unlink_photo(p)
        _bump()
    return {"ok": True}


# ---------------------------------------------------------------- rate limits
def rate_ok(kind, user_id, limit, window=3600):
    now = time.time()
    k = (kind, user_id)
    with _RATE_LOCK:
        if len(_RATE) > 50000:
            _RATE.clear()
        hits = [t for t in _RATE.get(k, []) if now - t < window]
        if len(hits) >= limit:
            _RATE[k] = hits
            return False
        hits.append(now)
        _RATE[k] = hits
        return True


# ---------------------------------------------------------------- HTTP (called from server.Handler)
def same_origin(h):
    """CSRF defence for member writes (the session cookie is SameSite=Lax): Origin, else Referer, must name this host.
    Neither header -> refused."""
    hosts = {x.strip().lower() for x in (h.headers.get("Host"), h.headers.get("X-Forwarded-Host")) if x}
    try:
        import whop_auth
        pub = urlparse(whop_auth.public_url()).netloc.lower()
        if pub:
            hosts.add(pub)
    except Exception:
        pass
    for hdr in ("Origin", "Referer"):
        v = h.headers.get(hdr)
        if v:
            return urlparse(v).netloc.lower() in hosts
    return False


def _writer(h):
    """(user_id, whop name) of a signed-in PAID member, else None. allowed() alone is not enough: the load-test header and a
    local run with sign-in off pass it with no member to write as."""
    if not h.allowed():
        return None
    sess = h.member() or {}
    return (sess["user"], sess.get("name") or "") if sess.get("user") else None


def _refuse(h, status, body):
    """Answer without reading the request body: the connection is closed so the unread bytes never reach the next request."""
    h.close_connection = True
    return h.send(status, body, headers={"Connection": "close"})


def _members_only(h):
    import whop_auth
    return _refuse(h, 401, {"error": "members_only", "signed_in": bool(h.member()), "upgrade": whop_auth.upgrade_url()})


def cached_summary(srv):
    """(key, status, body) of the shared summary answer (/api/reviews/summary): built at most once per 5 min per write
    generation (single flight), so the landing page can read it on every visit without touching SQLite."""
    key = ("reviews_summary", GEN[0], str(db_path()))
    st, body = srv.shared(key, lambda: (200, json.dumps({"summary": summary()}).encode()), ttl=300)
    return key, st, body


def handles_get(path):
    return path in ("/reviews", "/reviews.js", "/api/reviews", "/api/reviews/me", "/api/reviews/summary",
                    "/admin/reviews", "/admin/reviews.json") or path.startswith("/reviews/p/")


def handle_get(h, srv, path, q):
    if path in ("/admin/reviews", "/admin/reviews.json"):
        if path == "/admin/reviews":
            if not h.authed(q.get("token")):                 # the owner's link (?token=) or the header
                return h.send(403, b"Forbidden", "text/plain")
            return h.send(200, srv.web_file("reviews-admin.html"), "text/html; charset=utf-8")
        if not h.authed(None):                              # scripts: the X-Admin-Token header only, never a URL token
            return h.send(403, {"error": "forbidden"})
        return h.send(200, admin_list())
    if not page_on():
        return h.send(404, {"error": "not found"})
    if path in ("/reviews", "/reviews.js"):
        name, ctype = ("reviews.html", "text/html; charset=utf-8") if path == "/reviews" else ("reviews.js", "text/javascript; charset=utf-8")
        return h.send_shared(("reviews_asset", name), 200, srv.web_file(name), ctype, keep=600)
    if path.startswith("/reviews/p/"):
        m = PHOTO_RE.match(path)
        data = photo_bytes(m.group(1), bool(m.group(2))) if m else None
        if data is None:
            return h.send(404, {"error": "not found"})
        return h.send(200, data, "image/jpeg", {"Cache-Control": "public, max-age=31536000, immutable"})
    if path == "/api/reviews/me":
        return h.send(200, me(h))
    if path == "/api/reviews/summary":
        key, st, body = cached_summary(srv)
        return h.send_shared(key, st, body, keep=300)
    sort = q.get("sort") or "new"
    try:
        page = int(q.get("page") or 0)
    except ValueError:
        page = -1
    if sort not in SORTS or not 0 <= page <= MAX_PAGE:
        return h.send(400, {"error": "bad sort or page"})
    key = ("reviews_page", GEN[0], str(db_path()), sort, page)
    st, body = srv.shared(key, lambda: (200, json.dumps(public_page(sort, page)).encode()), ttl=300)
    return h.send_shared(key, st, body, keep=300)


def me(h):
    """The signed-in visitor's own state (never cached across visitors)."""
    import whop_auth
    sess = h.member() if whop_auth.enabled() else None
    w = _writer(h)
    out = {"signed_in": bool(sess), "member": bool(w), "write_on": write_on(), "photos_on": photos_on(),
           "can_write": bool(w) and write_on(), "max_photos": MAX_PHOTOS, "body_min": BODY_MIN, "body_max": BODY_MAX,
           "title_max": TITLE_MAX, "sign_in": "/auth/login?next=reviews", "join": whop_auth.upgrade_url(), "review": None}
    if w:
        out["names"] = names(w[1])
        out["review"] = own_review(w[0])
    return out


def handles_post(path):
    return path in ("/api/reviews", "/api/reviews/delete", "/api/reviews/photo", "/api/reviews/photo/delete",
                    "/admin/reviews/action")


def _read_json(h, n):
    if n > JSON_MAX:
        return None, _refuse(h, 413, {"error": f"body over {JSON_MAX} bytes"})
    if not (h.headers.get("Content-Type") or "").lower().startswith("application/json"):
        return None, _refuse(h, 415, {"error": "send JSON"})
    raw = h.rfile.read(n) if n > 0 else b""
    try:
        return json.loads(raw.decode("utf-8") or "{}", parse_constant=_no_constant), None
    except (ValueError, UnicodeDecodeError):
        return None, h.send(400, {"error": "bad JSON"})


def _no_constant(_):
    raise ValueError("NaN / Infinity")


def handle_post(h, srv, path, n):
    """Every refusal here answers before reading the body (and closes the connection)."""
    if path == "/admin/reviews/action":
        if not h.authed(None):                              # header token only
            return _refuse(h, 403, {"error": "forbidden"})
        d, sent = _read_json(h, n)
        if sent is not None:
            return sent
        try:
            return h.send(200, admin_action(d))
        except Refused as e:
            return h.send(e.status, {"error": e.msg})
    if not page_on():
        return _refuse(h, 404, {"error": "not found"})
    if not write_on():
        return _refuse(h, 403, {"error": "reviews_closed", "message": "Reviews are closed for new posts right now."})
    w = _writer(h)
    if not w:
        return _members_only(h)
    if not same_origin(h):
        return _refuse(h, 403, {"error": "cross_origin"})
    user, name = w
    if path == "/api/reviews/photo":
        return _upload(h, user, n)
    if not rate_ok("write", user, WRITES_PER_HOUR):
        return _refuse(h, 429, {"error": "rate_limited", "message": "Too many changes - try again later."})
    d, sent = _read_json(h, n)
    if sent is not None:
        return sent
    try:
        if path == "/api/reviews":
            review, created = upsert(user, name, d)
            sweep_orphans()
            return h.send(201 if created else 200, {"ok": True, "review": review})
        if path == "/api/reviews/delete":
            return h.send(200, {"ok": True, "deleted": delete_own(user)})
        if path == "/api/reviews/photo/delete":
            pid = d.get("id") if isinstance(d, dict) else None
            ok = delete_own_photo(user, pid if isinstance(pid, str) else "")
            return h.send(200 if ok else 404, {"ok": ok})
    except Refused as e:
        return h.send(e.status, {"error": e.msg})
    return h.send(404, {"error": "not found"})


def _upload(h, user, n):
    if not photos_on():
        return _refuse(h, 403, {"error": "photos_closed", "message": "Photo uploads are off right now."})
    if n <= 0:
        return _refuse(h, 400, {"error": "empty upload"})
    if n > PHOTO_MAX:
        return _refuse(h, 413, {"error": "too_large", "message": "That photo is too large - pick a smaller one."})
    ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in UPLOAD_TYPES:
        return _refuse(h, 415, {"error": "bad_type", "message": "Send a JPG, PNG or WEBP photo."})
    if not storage_ok():
        return _refuse(h, 507, {"error": "storage_full", "message": "Photo uploads are paused right now - post without a photo."})
    if not rate_ok("upload", user, UPLOADS_PER_HOUR):
        return _refuse(h, 429, {"error": "rate_limited", "message": f"Up to {UPLOADS_PER_HOUR} photo uploads an hour - try again later."})
    if not _SLOTS.acquire(blocking=False):
        return _refuse(h, 503, {"error": "busy", "message": "Photo uploads are busy - try again in a moment."})
    try:
        data = h.rfile.read(n)
        if len(data) != n:
            h.close_connection = True
            return h.send(400, {"error": "incomplete upload"})
        try:
            photo = save_photo(user, data)
        except Refused as e:
            return h.send(e.status, {"error": "refused", "message": e.msg})
        finally:
            data = None
        sweep_orphans()
        return h.send(201, {"ok": True, "photo": photo})
    finally:
        _SLOTS.release()
