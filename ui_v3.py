"""v3 interface ("precision instrument", owner-approved 2026-11-19/20), behind a server-side flag that is OFF by default.

Display only: nothing here reads grades, APIs or member data. server.py asks two questions per request and, when the answer is no,
sends exactly what it sent before (same bytes, same cache key, same unknown-route answer):

  ui_v3_default()          v3 for everyone unless SCOUT_UI_V3=0 (read per request, so a Render env change applies without a deploy).
  preview_ok(cookie)       the owner's preview cookie (set by /preview/v3 for the owner only; an HMAC of ADMIN_TOKEN, HttpOnly),
                           so a member can neither forge nor copy-paste a value that turns it on. No ADMIN_TOKEN = no preview.

page(body, name) adds the v3 head (theme before paint, one stylesheet) and one deferred script to a page's bytes; the page files on
disk are never edited (web/UI_V3.md). Bundles concatenate web/v3/<part> files in a fixed order, so page groups edit only their own
files and never this module or server.py. Memoised per body object / file versions with a single-flight lock (700 members on a cold
page build it once; send_shared keys its ETag + gzip by body identity)."""
import hashlib
import hmac
import os
import re
import threading
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
V3 = WEB / "v3"
COOKIE = "scout_v3"
PREVIEW = "/preview/v3"                      # owner only: ?off=1 clears; /preview/v3/landing = the signed-out page in v3

# Bundles: served at /v3/<name>. Order matters (tokens, then components, then the shell, then the page groups).
BUNDLES = {
    "member.css": ("tokens.css", "components.css", "shell.css", "board.css", "slips.css", "picks.css", "player.css", "account.css"),
    "member.js": ("shell.js", "board.js", "slips.js", "picks.js", "player.js", "account.js"),
    "record.css": ("tokens.css", "components.css", "shell.css", "record.css"),
    "record.js": ("shell.js", "record.js"),
    "landing.css": ("tokens.css", "components.css", "shell.css", "landing.css"),
    "landing.js": ("shell.js", "landing.js"),
}
CTYPE = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
# Pages: name -> (bundle stem, default sport accent). The names are the ones server.py passes (upgrade_page's names + two public pages).
PAGES = {"cs2": ("member", "cs2"), "lol": ("member", "lol"), "sports": ("member", "nba"), "sports-results": ("member", "nba"),
         "record": ("record", "cs2"), "landing": ("landing", "cs2")}
ART = frozenset(("dust2", "mirage", "inferno", "nuke", "ancient", "anubis", "train", "overpass", "cache", "nba", "nba-home", "nfl", "soccer",
                 "tennis-hard", "tennis-clay", "tennis-grass", "tennis-indoor", "rift-blue", "rift-red"))
ROUTE = re.compile(r"^/v3/(?:(member|record|landing)\.(css|js)|art/([a-z0-9-]{1,24})\.svg)$")
NEXT_SWITCH = b"const ck=document.cookie,NEXT="          # the UI_NEXT head script (server.upgrade_page): v3 pages never take the redesign

_LOCK = threading.Lock()
_FILES = {}                                  # path -> (mtime, bytes)
_BUNDLE = {}                                 # name -> (mtimes, bytes)
_PAGE = {}                                   # (id(body), name) -> (body, out)


def ui_v3_default():
    # ON for everyone since Nov 20 2026 (owner: v3 live). SCOUT_UI_V3=0 (Render env, read per request, no deploy) restores
    # today's classic pages for everyone at once.
    return os.environ.get("SCOUT_UI_V3", "1").strip().lower() not in ("0", "false", "off", "no")


def preview_value(admin_token):
    """The owner's preview cookie value: HMAC-SHA256(ADMIN_TOKEN, purpose), never the token itself. None without a token."""
    if not admin_token:
        return None
    return hmac.new(admin_token.encode("utf-8"), b"scout-ui-v3-preview-1", hashlib.sha256).hexdigest()[:40]


def preview_ok(cookie_value, admin_token):
    want = preview_value(admin_token)
    return bool(want and cookie_value) and hmac.compare_digest(cookie_value.encode("utf-8", "replace"), want.encode("utf-8"))


def _file(path):
    mt = path.stat().st_mtime
    hit = _FILES.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    data = path.read_bytes()
    _FILES[path] = (mt, data)
    return data


def route(path):
    """-> (bytes, content type, cache key) for a fixed v3 asset path, or None (the caller's unknown-route answer).
    Fixed names only: a request path never reaches the filesystem except as one of these names."""
    m = ROUTE.match(path)
    if not m:
        return None
    if m.group(3):
        name = m.group(3)
        if name not in ART:
            return None
        return _file(V3 / "art" / (name + ".svg")), CTYPE[".svg"], ("v3", "art", name)
    name = f"{m.group(1)}.{m.group(2)}"
    return bundle(name), CTYPE["." + m.group(2)], ("v3", name)


def bundle(name):
    parts = BUNDLES[name]
    mtimes = tuple((V3 / p).stat().st_mtime for p in parts)
    hit = _BUNDLE.get(name)
    if hit and hit[0] == mtimes:
        return hit[1]
    with _LOCK:                              # single flight
        hit = _BUNDLE.get(name)
        if hit and hit[0] == mtimes:
            return hit[1]
        sep = b"\n;\n" if name.endswith(".js") else b"\n"
        out = sep.join(b"/* ---- web/v3/" + p.encode() + b" */\n" + _file(V3 / p) for p in parts)
        _BUNDLE[name] = (mtimes, out)
        return out


def head(name):
    stem, sport = PAGES[name]
    return (b'<script>(function(){var r=document.documentElement,t=null;window.scoutV3=true;'
            b"try{t=localStorage.getItem('scout-theme');}catch(e){}if(t==='light'||t==='dark')r.dataset.theme=t;})();</script>"
            b'<link rel="stylesheet" href="/v3/' + stem.encode() + b'.css">'), sport, stem


def sports_on():
    """The sports the signed-out pages name (landing copy, its sport count): the four always on, plus soccer / tennis when their
    expansion switch is on (the same SCOUT_<SPORT>_ENABLED the sports service reads). Display only."""
    out = ["cs2", "lol", "nba", "nfl"]
    for sp in ("soccer", "tennis"):
        if os.environ.get(f"SCOUT_{sp.upper()}_ENABLED") == "1":
            out.append(sp)
    return " ".join(out)


def page(body, name):
    """The v3 copy of a page's bytes (already upgraded or not). Memoised per body OBJECT, as upgrade_page is (and per sport list on
    the landing page, two env reads)."""
    sp = sports_on() if name == "landing" else ""
    key = (id(body), name, sp)
    hit = _PAGE.get(key)
    if hit is not None and hit[0] is body:
        return hit[1]
    with _LOCK:
        hit = _PAGE.get(key)
        if hit is not None and hit[0] is body:
            return hit[1]
        out = _build(body, name)
        if sp:                               # the landing's sport list, on the <html> mark _build added
            out = out.replace(b'<html data-ui-v3="1"', b'<html data-v3-sports="' + sp.encode() + b'" data-ui-v3="1"', 1)
        if len(_PAGE) > 24:                  # a handful of pages x variants; old versions drop when a file changes
            _PAGE.clear()
        _PAGE[key] = (body, out)
        return out


def _build(body, name):
    i, j = body.rfind(b"</head>"), body.rfind(b"</body>")
    if not 0 <= i < j:                       # a page without its markers goes out as it is
        return body
    h, sport, stem = head(name)
    if b"family=Outfit" not in body:         # Outfit 400 / 500 / 600 only (the record page loads no Outfit of its own)
        h = b'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600&display=swap">' + h
    tail = b'<script src="/v3/' + stem.encode() + b'.js" defer></script>'
    out = body[:i] + h + body[i:j] + tail + body[j:]
    out = out.replace(NEXT_SWITCH, b"const ck=document.cookie,NEXT=false,_v3next=", 1)
    mark = b'<html data-ui-v3="1" data-v3-page="' + name.encode() + b'" data-sc-sport="' + sport.encode() + b'"'
    k = out.find(b"<html")
    if k >= 0 and out[k + 5:k + 6] in (b" ", b">"):
        out = out[:k] + mark + out[k + 5:]
    return out
