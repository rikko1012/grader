"""Player headshots and team logos for the v3 cards (owner 2026-11-20: "Add player and team pictures to cards also").

Display only, behind SCOUT_UI_V3 (server.py answers /img/* only when _v3_on(); off = the unknown-route answer).

How a picture reaches a card:
  1. img_index.py reads a sport's CURRENT board snapshot (the same bytes members already get) and registers, for each exact id on
     it, the upstream URL(s) it may come from: ESPN athlete / team id (NBA, NFL, soccer, tennis), BO3.gg player / team id (CS2),
     the PrizePicks player id (soccer, tennis). Never a name match, never a URL from a request.
  2. One background thread (the warmer) fetches each registered key once, re-encodes it small (WEBP, 160 px headshot / 128 px
     logo, Pillow) and writes it atomically under .cache/img. Paced per host, https only, exact host allowlist, no redirects,
     byte and pixel caps, short timeouts. Misses are remembered (404: 6 h, other failures: 10 min).
  3. /img/<p|t>/<sport>/<token>.webp serves ONLY what is already on disk (memory LRU in front). A member request never waits on
     an upstream: a miss is a fast 404 (60 s) and the card keeps its monogram.
  4. /img/index/<sport>.json lists the pictures that are READY for the board's players, so a card only asks for files that exist.

Bounded: at most MAX_FILES files / MAX_BYTES on disk (oldest dropped first), MEM_BYTES in memory; refreshed after REFRESH_S.
"""
import hashlib
import io
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

ALLOWED_HOSTS = frozenset(("a.espncdn.com", "files.bo3.gg", "static.prizepicks.com"))
# known "no real picture" art: a URL carrying one of these is treated as no image (the sampler's list, 2026-11-20)
PLACEHOLDER_MARKS = ("placeholder.png", "player_avatar.png", "default_headshot", "/images/teams/", "jersey")
SPORTS = ("cs2", "nba", "nfl", "soccer", "tennis")
KINDS = ("p", "t")                         # p = player headshot, t = team logo
SIZE = {"p": 160, "t": 128}                # output box (2x the largest card avatar / badge)
TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
ROUTE = re.compile(r"^/img/(p|t)/(cs2|nba|nfl|soccer|tennis)/([a-z0-9][a-z0-9_-]{0,47})\.webp$")
INDEX_ROUTE = re.compile(r"^/img/index/(cs2|nba|nfl|soccer|tennis)\.json$")

MAX_FETCH_BYTES = 4_000_000                # upstream answer cap (BO3.gg team art is ~60 KB, ESPN headshots ~260 KB)
# decoded-size cap, checked from the header before any pixel is decoded (every format; see the JPEG note below). 2048 x 2048
# fits; the largest real art sampled 2026-11-20 is a 1319 x 1681 BO3.gg team logo. A 4.2 M pixel RGBA decode is ~17 MB, so one
# big upstream file can no longer push the warmer to ~290 MB RSS (review 2026-11-20; production memory sits at its limit).
MAX_PIXELS = 4_200_000
# a JPEG may be bigger on the wire ONLY when it is a single-scan baseline file: libjpeg then decodes it strip by strip at 1/2 ..
# 1/8 scale (measured 2026-11-20: 48 M pixels baseline +2.8 MB). Progressive and multi-scan JPEGs allocate coefficient buffers for
# the FULL original size whatever the draft scale (+48 MB at 16 M pixels 4:2:0, +97 MB at 4:4:4, +544 MB at 177 M pixels), so they
# get the normal cap, checked from the header before any decode.
MAX_JPEG_BASELINE_PIXELS = 16_000_000
MAX_OUT_BYTES = 120_000                    # a re-encoded file over this is refused (a 160 px WEBP is ~5-12 KB)
MAX_FILES = 20_000
MAX_BYTES = 200_000_000
MEM_BYTES = 6_000_000
REFRESH_S = 7 * 86400                      # a stored picture is fetched again after a week (kept and served meanwhile)
MISS_S = 6 * 3600                          # upstream said 404 / not an image: not asked again for 6 h
FAIL_S = 600                               # timeout / 5xx / network: not asked again for 10 min
HOST_GAP_S = 0.35                          # polite pacing per upstream host
TIMEOUT = (4, 8)                           # connect, read seconds
QUEUE_MAX = 5000
UA = "1timeScout-images/1.0"


def _root():
    v = (os.environ.get("SCOUT_IMG_DIR") or "").strip()
    if v:
        return Path(v)
    try:
        import kvstore
        return Path(kvstore.DATA_DIR) / ".cache" / "img"
    except Exception:                                  # noqa: BLE001
        return Path(__file__).resolve().parent / ".cache" / "img"


# ---------------------------------------------------------------------------------------------------------------- upstream URLs
def safe_url(url):
    """-> the https URL on an allowed host, percent-encoded (PrizePicks 'manual/<Name>.png' paths carry spaces and accents), or
    None. No credentials, no port, no other scheme, never a placeholder."""
    if not isinstance(url, str) or not url or len(url) > 600:
        return None
    u = url.strip()
    if u.startswith("http://") and urlsplit(u).hostname in ALLOWED_HOSTS:
        u = "https://" + u[7:]
    try:
        s = urlsplit(u)
    except ValueError:
        return None
    if s.scheme != "https" or s.hostname not in ALLOWED_HOSTS or s.port not in (None, 443) or s.username or s.password:
        return None
    if s.netloc.lower() != s.hostname:                 # no userinfo / odd authority forms
        return None
    low = u.lower()
    if any(m in low for m in PLACEHOLDER_MARKS):
        return None
    path = quote(s.path, safe="/%-._~")
    query = quote(s.query, safe="=&%-._~/")
    return urlunsplit(("https", s.hostname, path, query, ""))


def espn_headshot(league, athlete_id):
    return f"https://a.espncdn.com/i/headshots/{league}/players/full/{int(athlete_id)}.png" if str(athlete_id).isdigit() else None


def espn_logo(league, team):
    t = str(team or "").lower()
    if league == "soccer":
        return f"https://a.espncdn.com/i/teamlogos/soccer/500/{int(t)}.png" if t.isdigit() else None
    return f"https://a.espncdn.com/i/teamlogos/{league}/500/{t}.png" if re.fullmatch(r"[a-z]{2,4}", t) else None


def shared_urls(pairs):
    """{url: number of distinct ids using it} over (id, url) pairs: art shared by two or more ids is team / jersey art, not a
    headshot (PrizePicks CS2: 89 players on 18 URLs)."""
    seen = {}
    for pid, url in pairs:
        if url:
            seen.setdefault(url, set()).add(pid)
    return {u: len(ids) for u, ids in seen.items()}


# ---------------------------------------------------------------------------------------------------------------- state
_LOCK = threading.Lock()
_SRC = {}                                  # (kind, sport, token) -> tuple of candidate upstream URLs (registered from boards)
_MISS = {}                                 # key -> retry-after epoch
_READY = {}                                # key -> (etag, mtime, size): on disk
_MEM = OrderedDict()                       # key -> (bytes, etag)
_MEM_SIZE = [0]
_QUEUE = []                                # FIFO of keys
_QUEUED = set()
_HOST_AT = {}
_VERSION = [0]                             # bumps when a picture becomes ready (img_index rebuilds its lists)
_PUBLISHED = [0, 0.0]                      # the version img_index sees, and when it was last moved (version(): coalesced)
VERSION_GAP_S = 30                         # while the warmer is working through its queue, the lists move at most this often
_WARMER = [None]
_SCANNED = [False]
STATS = {"fetched": 0, "stored": 0, "missed": 0, "failed": 0, "served": 0, "served_304": 0, "not_ready": 0, "refused": 0}


def key_ok(kind, sport, token):
    return kind in KINDS and sport in SPORTS and isinstance(token, str) and bool(TOKEN_RE.match(token))


def path_of(key):
    kind, sport, token = key
    return _root() / kind / sport / (token + ".webp")


def url_path(key, etag=None):
    kind, sport, token = key
    return f"/img/{kind}/{sport}/{token}.webp" + (f"?v={etag[:10]}" if etag else "")


def register(key, urls):
    """Remember where this exact id's picture may come from (first good URL wins) and queue it when it is not on disk yet."""
    if not key_ok(*key):
        return False
    good = tuple(dict.fromkeys(u for u in (safe_url(x) for x in urls) if u))
    if not good:
        return False
    with _LOCK:
        _SRC[key] = good
    if ready(key) is None or _stale(key):
        enqueue(key)
    return True


def ready(key):
    """-> (etag, mtime, size) when the picture is on disk, else None. A cold process learns about files from the disk once."""
    with _LOCK:
        hit = _READY.get(key)
    if hit is not None:
        return hit
    p = path_of(key)
    try:
        st = p.stat()
    except OSError:
        return None
    if not 0 < st.st_size <= MAX_OUT_BYTES:
        return None
    try:
        data = p.read_bytes()
    except OSError:
        return None
    etag = hashlib.sha1(data).hexdigest()[:20]
    hit = (etag, st.st_mtime, st.st_size)
    with _LOCK:
        _READY[key] = hit
    _mem_put(key, data, etag)
    return hit


def _stale(key):
    hit = _READY.get(key)
    return bool(hit) and time.time() - hit[1] > REFRESH_S


def enqueue(key):
    with _LOCK:
        if key in _QUEUED or len(_QUEUE) >= QUEUE_MAX:
            return False
        until = _MISS.get(key)
        if until and until > time.time():
            return False
        _QUEUE.append(key)
        _QUEUED.add(key)
    start_warmer()
    return True


def _mem_put(key, data, etag):
    with _LOCK:
        old = _MEM.pop(key, None)
        if old:
            _MEM_SIZE[0] -= len(old[0])
        _MEM[key] = (data, etag)
        _MEM_SIZE[0] += len(data)
        while _MEM_SIZE[0] > MEM_BYTES and _MEM:
            _, (d, _e) = _MEM.popitem(last=False)
            _MEM_SIZE[0] -= len(d)


def read(key):
    """-> (bytes, etag) of a stored picture, or None. Disk read only on a memory miss; never an upstream call."""
    with _LOCK:
        hit = _MEM.get(key)
        if hit:
            _MEM.move_to_end(key)
            return hit
    meta = ready(key)
    if meta is None:
        return None
    try:
        data = path_of(key).read_bytes()
    except OSError:
        with _LOCK:
            _READY.pop(key, None)
        return None
    etag = hashlib.sha1(data).hexdigest()[:20]
    _mem_put(key, data, etag)
    return data, etag


def bump():
    """A picture (or BO3.gg roster data) changed: img_index rebuilds its lists on a later read (see version())."""
    with _LOCK:
        _VERSION[0] += 1


def _warming():
    t = _WARMER[0]
    return os.environ.get("SCOUT_IMG_WARM", "1") != "0" and t is not None and t.is_alive()


def version():
    """The picture-set version img_index rebuilds on, COALESCED (review 2026-11-20: one bump per stored picture rebuilt every
    list every MIN_REBUILD_S through a cold warm-up). A change is seen at once when the warmer is idle (queue empty, or no warmer:
    tests store pictures themselves), at once after a quiet VERSION_GAP_S, and otherwise at most every VERSION_GAP_S - so the
    last picture of a batch is always seen (the queue is empty by then)."""
    with _LOCK:
        v = _VERSION[0]
        if v != _PUBLISHED[0]:
            now = time.time()
            if not (_QUEUE and _warming()) or now - _PUBLISHED[1] >= VERSION_GAP_S:
                _PUBLISHED[0], _PUBLISHED[1] = v, now
        return _PUBLISHED[0]


# ---------------------------------------------------------------------------------------------------------------- fetch + encode
class Refused(Exception):
    """Not a picture we keep (404, wrong type, too big, too small, a redirect): remembered as a miss for MISS_S."""


def _http_get(url):
    """-> bytes. Exact-host https GET, no redirects, streamed with a byte cap. Raises Refused for a definite no, other
    exceptions for transient trouble."""
    import requests
    host = urlsplit(url).hostname
    if host not in ALLOWED_HOSTS:
        raise Refused("host")
    r = requests.get(url, timeout=TIMEOUT, allow_redirects=False, stream=True, headers={"User-Agent": UA, "Accept": "image/*"})
    try:
        if r.status_code in (404, 410, 403, 400) or 300 <= r.status_code < 400:
            raise Refused(str(r.status_code))
        if r.status_code != 200:
            raise IOError(f"status {r.status_code}")
        n = r.headers.get("Content-Length")
        if n and n.isdigit() and int(n) > MAX_FETCH_BYTES:
            raise Refused("size")
        buf = bytearray()
        for chunk in r.iter_content(65536):
            buf += chunk
            if len(buf) > MAX_FETCH_BYTES:
                raise Refused("size")
        return bytes(buf)
    finally:
        r.close()


_SOF_BASELINE = (0xC0, 0xC1)                   # baseline / extended sequential Huffman; every other SOF is refused the bigger cap


def _jpeg_single_scan_baseline(raw, im):
    """True only for a sequential (SOF0/SOF1) JPEG with exactly one scan covering every frame component: the only kind libjpeg decodes at reduced scale without a
    full-size coefficient buffer. Walks the marker segments to the first SOS, then counts further SOS markers in the rest (entropy
    data never holds an unstuffed 0xFF 0xDA); anything unexpected answers False (the normal cap applies)."""
    if im.info.get("progressive") or im.info.get("progression"):
        return False
    n, i, sof, nf = len(raw), 2, None, None
    if raw[:2] != b"\xff\xd8":
        return False
    while i + 4 <= n:
        if raw[i] != 0xFF:
            return False
        m = raw[i + 1]
        if m == 0xFF:                                  # fill byte
            i += 1
            continue
        if m in (0x01,) or 0xD0 <= m <= 0xD7:          # standalone markers
            i += 2
            continue
        seg = int.from_bytes(raw[i + 2:i + 4], "big")
        if seg < 2:
            return False
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if sof is not None:
                return False
            sof, nf = m, (raw[i + 9] if i + 9 < n else None)
        if m == 0xDA:
            ns = raw[i + 4] if i + 4 < n else None     # a scan with fewer components than the frame = more scans follow (buffered)
            return sof in _SOF_BASELINE and nf is not None and ns == nf and raw.find(b"\xff\xda", i + 2 + seg) < 0
        i += 2 + seg
    return False


def encode(raw, kind):
    """Upstream bytes -> a small WEBP (bytes). Players: a square from the top centre (faces sit high in ESPN / BO3.gg art),
    160 px. Logos: the whole mark centred on a transparent 128 px square. Raises Refused for anything that is not a picture."""
    from PIL import Image
    box = SIZE[kind]
    try:
        # Image.MAX_IMAGE_PIXELS is process-wide (reviews.py sets its own per upload): never changed here; the cap below is ours
        im = Image.open(io.BytesIO(raw))
        if im.format not in ("PNG", "WEBP", "JPEG", "GIF"):
            raise Refused("format")
        w, h = im.size                                 # header size: nothing decoded yet
        if w < 24 or h < 24:
            raise Refused("dims")
        cap = MAX_PIXELS
        if im.format == "JPEG" and _jpeg_single_scan_baseline(raw, im):
            cap = MAX_JPEG_BASELINE_PIXELS
        if w * h > cap:
            raise Refused("dims")                      # refused from the header, every format, before any decode
        if im.format == "JPEG":
            im.draft("RGB", (box * 2, box * 2))       # decode at 1/2 .. 1/8 scale (never below 2x the output box)
            w, h = im.size
            if w * h > MAX_PIXELS:
                raise Refused("dims")
        im.load()
    except Refused:
        raise
    except Exception as e:                             # noqa: BLE001 - truncated / not an image / bomb
        raise Refused(type(e).__name__) from e
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA")
    f = min(w, h) // (box * 2)
    if f >= 2:                                         # shrink before the RGBA copy and the crop (box-average, then LANCZOS)
        im = im.reduce(f)
        w, h = im.size
    im = im.convert("RGBA")
    if kind == "p":
        # portrait art (BO3.gg 354x455) and landscape art (ESPN 600x436): a top-centre square; square art (PrizePicks) whole
        # (measured on real art 2026-11-20: 80% of the short side keeps head and shoulders, the face reads at 32 px)
        side = int(min(w, h) * (0.8 if h >= w * 1.05 or w >= h * 1.05 else 1.0))
        left, top = (w - side) // 2, 0
        im = im.crop((left, top, left + side, top + side)).resize((box, box), Image.LANCZOS)
    else:
        im = logo_disc(im, box)
    out = io.BytesIO()
    im.save(out, "WEBP", quality=84, method=4)
    data = out.getvalue()
    if not data or len(data) > MAX_OUT_BYTES:
        raise Refused("encoded size")
    return data


DISC_LIGHT = (243, 245, 249, 255)
DISC_DARK = (27, 34, 48, 255)


def logo_disc(im, box):
    """A team mark on its own round disc, so it reads in dark AND light mode: a light disc for dark or coloured marks, a dark
    disc for white / very light marks (BO3.gg and lolesports logos are often white on transparent). Outside the circle is
    transparent. The mark is trimmed to its visible pixels and fitted to ~64% of the disc."""
    from PIL import Image, ImageDraw, ImageStat
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    alpha = im.getchannel("A")
    rgb = im.convert("RGB")
    try:
        mean = ImageStat.Stat(rgb, mask=alpha.point(lambda a: 255 if a > 128 else 0)).mean
        lum = (0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]) / 255
    except ZeroDivisionError:
        lum = 0.0
    disc = DISC_DARK if lum > 0.78 else DISC_LIGHT
    scale = 4                                          # draw the circle large, then shrink: smooth edge
    big = Image.new("RGBA", (box * scale, box * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).ellipse((0, 0, box * scale - 1, box * scale - 1), fill=disc)
    canvas = big.resize((box, box), Image.LANCZOS)
    inner = int(box * 0.64)
    im = im.copy()
    im.thumbnail((inner, inner), Image.LANCZOS)
    canvas.alpha_composite(im, ((box - im.width) // 2, (box - im.height) // 2))
    return canvas


def _write(key, data):
    p = path_of(key)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        for i in range(3):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:                    # Windows: a reader has the old file open
                if i == 2:
                    raise
                time.sleep(0.05)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    etag = hashlib.sha1(data).hexdigest()[:20]
    with _LOCK:
        _READY[key] = (etag, time.time(), len(data))
    bump()
    _mem_put(key, data, etag)
    return True


def _pace(url):
    host = urlsplit(url).hostname
    with _LOCK:
        wait = _HOST_AT.get(host, 0) + HOST_GAP_S - time.time()
        _HOST_AT[host] = max(time.time(), _HOST_AT.get(host, 0) + HOST_GAP_S)
    if wait > 0:
        time.sleep(wait)


def fetch_one(key, get=None):
    """Fetch, encode and store one registered key (the warmer's step; tests call it directly). -> 'stored' | 'miss' | 'fail'."""
    get = get or _http_get
    with _LOCK:
        urls = _SRC.get(key) or ()
    if not urls:
        return "miss"
    transient = False
    for url in urls:
        _pace(url)
        try:
            raw = get(url)
            STATS["fetched"] += 1
            data = encode(raw, key[0])
        except Refused:
            continue
        except Exception:                              # noqa: BLE001 - timeout / 5xx / network
            transient = True
            continue
        if _write(key, data):
            STATS["stored"] += 1
            with _LOCK:
                _MISS.pop(key, None)
            return "stored"
        transient = True
    with _LOCK:
        _MISS[key] = time.time() + (FAIL_S if transient else MISS_S)
    STATS["failed" if transient else "missed"] += 1
    return "fail" if transient else "miss"


def missing(key):
    with _LOCK:
        until = _MISS.get(key)
    return bool(until and until > time.time())


# ---------------------------------------------------------------------------------------------------------------- warmer
def _next():
    with _LOCK:
        while _QUEUE:
            k = _QUEUE.pop(0)
            _QUEUED.discard(k)
            return k
    return None


def _loop():
    last_prune = last_scan = 0.0
    while True:
        try:
            now = time.time()
            if now - last_scan > 600:
                last_scan = now
                try:
                    import img_index
                    img_index.bo3_scan()
                except Exception:                      # noqa: BLE001 - a scan failure only means fewer CS2 pictures
                    pass
            k = _next()
            if k is None:
                if now - last_prune > 3600:
                    last_prune = now
                    prune()
                time.sleep(1.0)
                continue
            if ready(k) is not None and not _stale(k):
                continue
            fetch_one(k)
        except Exception:                              # noqa: BLE001 - the warmer never dies
            time.sleep(2.0)


def start_warmer():
    if os.environ.get("SCOUT_IMG_WARM", "1") == "0":
        return
    with _LOCK:
        t = _WARMER[0]
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=_loop, name="img-warmer", daemon=True)
        _WARMER[0] = t
    t.start()


def prune(max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Drop the oldest files until both bounds hold (and stray .tmp files). -> number removed."""
    root = _root()
    files = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.endswith(".tmp"):
                try:
                    if time.time() - p.stat().st_mtime > 600:
                        p.unlink()
                except OSError:
                    pass
                continue
            if p.suffix == ".webp":
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, p))
    except OSError:
        return 0
    files.sort()
    total, n, removed = sum(f[1] for f in files), len(files), 0
    for mt, size, p in files:
        if n <= max_files and total <= max_bytes:
            break
        try:
            p.unlink()
        except OSError:
            continue
        total -= size
        n -= 1
        removed += 1
        rel = p.relative_to(root).parts
        if len(rel) == 3:
            key = (rel[0], rel[1], rel[2][:-5])
            with _LOCK:
                _READY.pop(key, None)
                old = _MEM.pop(key, None)
                if old:
                    _MEM_SIZE[0] -= len(old[0])
    return removed


def status():
    with _LOCK:
        return {"registered": len(_SRC), "ready": len(_READY), "queued": len(_QUEUE), "missing": len(_MISS),
                "mem_bytes": _MEM_SIZE[0], **STATS}


def reset():
    """Tests: forget everything held in memory (disk files stay)."""
    with _LOCK:
        _SRC.clear(); _MISS.clear(); _READY.clear(); _MEM.clear(); _QUEUE.clear(); _QUEUED.clear(); _HOST_AT.clear()
        _MEM_SIZE[0] = 0
        _PUBLISHED[0], _PUBLISHED[1] = _VERSION[0], 0.0


# ---------------------------------------------------------------------------------------------------------------- HTTP answer
def answer(path, if_none_match=None):
    """-> (status, body bytes, content type, headers) for /img/<kind>/<sport>/<token>.webp, or None when the path is not one
    (the caller's unknown-route answer). Disk / memory only."""
    m = ROUTE.match(path)
    if not m:
        return None
    key = (m.group(1), m.group(2), m.group(3))
    got = read(key)
    if got is None:
        STATS["not_ready"] += 1
        return 404, b"not ready", "text/plain; charset=utf-8", {"Cache-Control": "public, max-age=60"}
    data, etag = got
    tag = f'"{etag}"'
    h = {"Cache-Control": "public, max-age=2592000, immutable", "ETag": tag}
    if if_none_match and tag in [x.strip().removeprefix("W/") for x in if_none_match.split(",")]:
        STATS["served_304"] += 1
        return 304, b"", "image/webp", h
    STATS["served"] += 1
    return 200, data, "image/webp", h
