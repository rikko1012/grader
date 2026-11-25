"""The esports-process side of Prop Grader Sports - the ONLY sports code an esports request thread runs (owner brief 2).

server.py's single flag-guarded hook calls gateway.handle_get(...) for /sports, /sports/<asset> and /api/sports/*.
Standard library + sports/authtoken.py only: importing this module does not import the sports store, models or
adapters, and nothing here touches the sports database.

SPORTS_API_MODE (read per request)
  service    (default, chosen 2026-10-23 - see sports/RUNBOOK.md "Architecture")
             The sports API runs in its own process / Render service (sports/service.py). The esports server only:
               GET /api/sports/token          paid member (server-side Handler.allowed) -> st1 token + api_base
               GET /sports, /sports/<asset>   static page bytes (memory-cached, no data)
               GET /api/sports/admin/gateway  admin: these counters
             Every other /api/sports/* path answers 404 {"error":"not_here","api_base":...} without sports work.
  proxy      The esports server forwards GET /api/sports/* to SPORTS_UPSTREAM (a private sports service) with a
             hard budget: at most SPORTS_PROXY_MAX_INFLIGHT (16) at once, SPORTS_PROXY_QUEUE_MS (200 ms) to get a
             slot or 503 sports_busy, SPORTS_PROXY_TIMEOUT_S (2.5 s) per request, a LIFO keep-alive pool of the same
             size, a 4 MB response cap and a circuit breaker (5 consecutive failures -> 5 s of immediate 503s).
  inprocess  Legacy foundation path (sports/web.py inside the esports process). Dev / tests only - measured
             +51% esports p99 at 700 members. Never set in production.

Environment: SPORTS_TOKEN_SECRET (shared with the sports service), SPORTS_API_BASE (public origin of the sports
service, e.g. https://scout-sports-api.onrender.com; "" = same origin), SPORTS_UPSTREAM (host:port, proxy mode).
"""
from __future__ import annotations

import http.client
import json
import mimetypes
import os
import queue
import threading
import time
from pathlib import Path

from . import authtoken

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
ASSET_DIR = WEB_DIR / "sports"
ASSET_TYPES = {".css", ".js", ".svg", ".png", ".webp", ".woff2", ".json", ".html", ".ico"}
ASSET_MAX = 2_000_000
_FIXED_TYPES = {".js": "application/javascript", ".css": "text/css", ".json": "application/json", ".html": "text/html"}
RESP_MAX = 4_000_000
PLACEHOLDER = (b"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
               b"<title>Prop Grader Sports</title><body style='font-family:system-ui;margin:2rem'><h1>Prop Grader Sports</h1>"
               b"<p>Sports is being built and is not available yet.</p></body>")
MODES = ("service", "proxy", "inprocess")
PAGE_ALIASES = {"results": "results.html"}          # /sports/results: admin only since 2026-11-09 (members -> /sports#/slate)


def mode():
    m = os.environ.get("SPORTS_API_MODE", "service").strip().lower()
    return m if m in MODES else "service"


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


def _float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


STATS = {"token_minted": 0, "token_refused": 0, "proxied": 0, "proxy_busy": 0, "proxy_errors": 0,
         "proxy_circuit_open": 0, "proxy_inflight": 0, "proxy_peak_inflight": 0, "not_here": 0, "pages": 0}
_SLOCK = threading.Lock()


def _bump(k, n=1):
    with _SLOCK:
        STATS[k] = STATS.get(k, 0) + n


def _j(code, obj, headers=None):
    return code, json.dumps(obj).encode("utf-8"), "application/json", dict(headers or {})


# ------------------------------------------------------------------------------------------------ static pages
_PAGES = {}
_PLOCK = threading.Lock()


def _static(rel):
    """Page / asset bytes from web/, cached in memory (re-read when the file's mtime changes). No data, no auth."""
    if rel == "":
        f = WEB_DIR / "sports.html"
        ctype = "text/html; charset=utf-8"
    else:
        if ".." in rel or rel.startswith(("/", "\\")) or "\\" in rel:
            return None
        f = (ASSET_DIR / rel)
        try:
            f.resolve().relative_to(ASSET_DIR.resolve())
        except ValueError:
            return None
        if f.suffix.lower() not in ASSET_TYPES:
            return None
        ctype = mimetypes.types_map.get(f.suffix.lower(), "application/octet-stream")
        if f.suffix.lower() == ".woff2":
            ctype = "font/woff2"
        elif f.suffix.lower() in _FIXED_TYPES:          # never the host's registry (Windows can map .js to text/plain,
            ctype = _FIXED_TYPES[f.suffix.lower()]      # which nosniff browsers refuse to run)
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
    try:
        st = f.stat()
    except OSError:
        return (PLACEHOLDER, "text/html; charset=utf-8") if rel == "" else None
    if st.st_size > ASSET_MAX:
        return None
    with _PLOCK:
        hit = _PAGES.get(str(f))
        if hit and hit[0] == st.st_mtime_ns:
            return hit[1], hit[2]
    data = f.read_bytes()
    with _PLOCK:
        if len(_PAGES) > 200:
            _PAGES.clear()
        _PAGES[str(f)] = (st.st_mtime_ns, data, ctype)
    return data, ctype


# ------------------------------------------------------------------------------------------- owner-first access
def allow_id(user) -> str:
    """SPORTS_UI=owner allow-list id of a Whop user (same formula as server.sports_allow_id; tested equal)."""
    import hashlib
    return "sa_" + hashlib.sha256(f"scout-sports-allow|{user}".encode("utf-8")).hexdigest()[:24]


def _whoami(member, allowed, admin):
    """GET /api/sports/whoami?token=<ADMIN_TOKEN> (SPORTS_UI=owner only; server.py routes it here only for the admin
    token): the allow id of the Whop session in THIS browser, to put in SPORTS_UI_ALLOW. Never the Whop id itself."""
    if not admin():
        return _j(403, {"error": "admin_only"})
    user = member()
    return _j(200, {"signed_in": bool(user), "paid": bool(allowed()), "allow_id": allow_id(user) if user else None,
                    "set": "SPORTS_UI_ALLOW=<allow_id> on cs2-scout (comma-separate several)"},
              {"Cache-Control": "no-store"})


# ------------------------------------------------------------------------------------------------------- tokens
def api_base():
    return os.environ.get("SPORTS_API_BASE", "").strip().rstrip("/") if mode() == "service" else ""


def _token(member, allowed, admin, headers):
    if (headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
        _bump("token_refused")
        return _j(403, {"error": "cross_site"})
    if not allowed():
        _bump("token_refused")
        return _j(401, {"error": "members_only", "api_base": api_base()})    # public origin (Results page)
    is_admin = bool(admin())
    user = member() or ("admin" if is_admin else None) or "local"
    try:
        t = authtoken.mint(str(user), "admin" if is_admin else "member")
    except authtoken.TokenError:
        return _j(503, {"error": "sports_auth_not_configured"})
    _bump("token_minted")
    return _j(200, {"token": t["token"], "exp": t["exp"], "ttl_s": t["ttl_s"], "api_base": api_base(),
                    # PP_OPEN_LINK owner switch (default off): the page draws "Open in PrizePicks" / "Open Underdog"
                    **({"pp_open_link": True} if os.environ.get("PP_OPEN_LINK", "").strip() == "1" else {})},
              {"Cache-Control": "no-store"})


# -------------------------------------------------------------------------------------------------------- proxy
class _Proxy:
    def __init__(self):
        self.lock = threading.Lock()
        self.slots = None
        self.size = None
        self.idle = queue.LifoQueue()
        self.fails = 0
        self.open_until = 0.0
        self.tokens = {}                                # member -> (exp, token): one mint per member per life

    def configure(self):
        size = max(1, _int("SPORTS_PROXY_MAX_INFLIGHT", "16"))
        with self.lock:
            if self.size != size:
                self.size, self.slots = size, threading.BoundedSemaphore(size)
        return self.slots

    def token_for(self, user, role):
        now = time.time()
        k = (user, role)
        with self.lock:
            hit = self.tokens.get(k)
            if hit and hit[0] - 60 > now:
                return hit[1]
        t = authtoken.mint(str(user), role)
        with self.lock:
            if len(self.tokens) > 5000:
                self.tokens.clear()
            self.tokens[k] = (t["exp"], t["token"])
        return t["token"]

    def _conn(self, upstream, timeout):
        try:
            c = self.idle.get_nowait()
            if c.host_port == upstream:
                c.timeout = timeout
                if c.sock is not None:
                    c.sock.settimeout(timeout)
                return c, True
            c.close()
        except queue.Empty:
            pass
        host, _, port = upstream.rpartition(":")
        c = http.client.HTTPConnection(host or "127.0.0.1", int(port or 80), timeout=timeout)
        c.host_port = upstream
        return c, False

    def forward(self, path_qs, token):
        upstream = os.environ.get("SPORTS_UPSTREAM", "").strip()
        if not upstream:
            return _j(503, {"error": "sports_upstream_not_configured"})
        now = time.monotonic()
        if now < self.open_until:
            _bump("proxy_circuit_open")
            return _j(503, {"error": "sports_unavailable"}, {"Retry-After": "5"})
        slots = self.configure()
        if not slots.acquire(timeout=_float("SPORTS_PROXY_QUEUE_MS", "200") / 1000.0):
            _bump("proxy_busy")
            return _j(503, {"error": "sports_busy"}, {"Retry-After": "2"})
        with _SLOCK:
            STATS["proxy_inflight"] += 1
            STATS["proxy_peak_inflight"] = max(STATS["proxy_peak_inflight"], STATS["proxy_inflight"])
        timeout = _float("SPORTS_PROXY_TIMEOUT_S", "2.5")
        try:
            for attempt in (0, 1):                      # one retry only for a stale kept-alive connection
                c, reused = self._conn(upstream, timeout)
                try:
                    c.request("GET", path_qs, headers={"Authorization": f"Bearer {token}", "X-Sports-Via": "gateway"})
                    r = c.getresponse()
                    body = r.read(RESP_MAX + 1)
                    if len(body) > RESP_MAX:
                        c.close()
                        raise OSError("sports response over the cap")
                    ctype = r.getheader("Content-Type") or "application/json"
                    if r.will_close:
                        c.close()
                    else:
                        self.idle.put(c)
                    with self.lock:
                        self.fails = 0 if r.status < 500 else self.fails
                    _bump("proxied")
                    return r.status, body, ctype, {}
                except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                    c.close()
                    if reused and attempt == 0:
                        continue
                    raise
        except Exception as e:                          # noqa: BLE001 - timeout / refused / reset
            _bump("proxy_errors")
            with self.lock:
                self.fails += 1
                if self.fails >= 5:
                    self.open_until = time.monotonic() + 5.0
                    self.fails = 0
            return _j(503, {"error": "sports_unavailable", "why": type(e).__name__})
        finally:
            with _SLOCK:
                STATS["proxy_inflight"] -= 1
            slots.release()


_PROXY = _Proxy()


def stats():
    with _SLOCK:
        out = dict(STATS)
    out.update({"mode": mode(), "token_configured": authtoken.configured(), "api_base": api_base(),
                "proxy_pool_idle": _PROXY.idle.qsize(), "proxy_max_inflight": _PROXY.size,
                "proxy_circuit_open": time.monotonic() < _PROXY.open_until})
    return out


def inprocess_member(user):
    """inprocess mode: the member's pseudonymous id (the same subject a st1 token carries when a secret is set), so one
    member's per-member rows are never another's. None without a signed-in member."""
    if not user:
        return None
    try:
        return authtoken.subject(str(user))
    except authtoken.TokenError:                        # no secret (local dev): still one id per member, never shared
        import hashlib
        return "m_" + hashlib.sha256(f"inprocess|{user}".encode("utf-8")).hexdigest()[:20]


# ------------------------------------------------------------------------------------------------------ the hook
def handle_get(path, raw_query, query, member, allowed, admin, headers):
    """-> (status, body_bytes, content_type, headers). Called by server.py's hook only when the path's flag is on
    (SPORTS_UI for /sports and /sports/<asset>, SPORTS_API for /api/sports/*)."""
    if path == "/sports" or path.startswith("/sports/"):
        if path == '/sports/expansion.html':
            sport = 'tennis' if query.get('sport') == 'tennis' else 'soccer'
            return 302, b'', 'text/html; charset=utf-8', {'Location': '/#scout/board?sport=' + sport,
                                                       'Cache-Control': 'private, no-store'}
        rel = "" if path == "/sports" else path[len("/sports/"):]
        rel = PAGE_ALIASES.get(rel, rel)
        if rel == "results.html" and not admin():   # owner 2026-11-09: no member Results page; the admin token still opens it
            return 302, b"", "text/html; charset=utf-8", {"Location": "/sports#/slate"}
        got = _static(rel)
        if got is None:
            return _j(404, {"error": "not found"})
        _bump("pages")
        return 200, got[0], got[1], {"Cache-Control": "private, no-cache"}
    if path == "/api/sports/token":
        return _token(member, allowed, admin, headers)
    if path == "/api/sports/whoami":
        return _whoami(member, allowed, admin)
    if path == "/api/sports/admin/gateway":
        if not admin():
            return _j(403, {"error": "admin_only"})
        return _j(200, stats())
    m = mode()
    if m == "service":
        _bump("not_here")
        return _j(404, {"error": "not_here", "api_base": api_base()})
    if m == "proxy":
        if not allowed():
            return _j(401, {"error": "members_only"})
        is_admin = bool(admin())
        try:
            tok = _PROXY.token_for(member() or ("admin" if is_admin else "local"), "admin" if is_admin else "member")
        except authtoken.TokenError:
            return _j(503, {"error": "sports_auth_not_configured"})
        from urllib.parse import urlencode
        fwd = urlencode({k: v for k, v in query.items() if k != "token"})   # the esports admin token never travels
        return _PROXY.forward(path + (f"?{fwd}" if fwd else ""), tok)
    from . import web                                   # inprocess (dev / tests only)
    code, body, ctype = web.handle(path, query, allowed=allowed, admin=admin, member=inprocess_member(member()))
    return code, body, ctype, {}


if __name__ == "__main__":                              # python -m sports.gateway allow-id <whop user id>
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "allow-id":
        print(allow_id(sys.argv[2]))
    else:
        print("usage: python -m sports.gateway allow-id <whop user id>")
        sys.exit(2)
