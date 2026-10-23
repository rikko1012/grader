"""Scout Sports API service - its OWN process (and, in production, its own Render web service), so no esports request
thread ever executes sports work (owner brief 2; foundation measured +51% esports p99 with sports in-process).

    python -m sports.service [--host 0.0.0.0] [--port $PORT]

Serves sports/web.py's ROUTES. Every route needs a verified st1 token (sports/authtoken.py) minted by the esports
server after ITS server-side paid-member check; the token arrives as "Authorization: Bearer ..." (browser, CORS) or from
the esports gateway (proxy mode). The only unauthenticated path is GET /api/sports/health (no data). Admin ops:
GET /api/sports/admin/ops (token role admin, or X-Admin-Token == ADMIN_TOKEN).

Resource limits (environment, defaults):
  SPORTS_SERVICE_THREADS   32    concurrent requests; beyond that a request waits SPORTS_SERVICE_QUEUE_MS (200 ms)
                                 for a slot, then gets an immediate 503 sports_busy (never an unbounded thread pile)
  SPORTS_REQUEST_BUDGET_S  4     hard per-query budget (SQLite progress handler) and single-flight wait
  SPORTS_DB_POOL           4     read-only store connections at once (web.store_ro)
  SPORTS_BODY_MAX          65536 POST body cap (refused unread above it)
  SPORTS_ALLOWED_ORIGINS   ""    comma list of browser origins allowed by CORS (e.g. https://cs2-scout.onrender.com);
                                 a request whose Origin is not listed is refused 403 before any work
  SPORTS_SERVICE_MAX_MB    900   RSS ceiling: above it the service stops accepting work (503) and exits for a restart
                                 after in-flight requests finish (Render restarts it)
Worker: with SPORTS_INGEST on, this process supervises ONE ingestion child (python -m sports.worker loop ...) with its
own memory cap, restarting it with jittered exponential backoff; the child writes the store, this process only reads.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import authtoken, flags, web

VERSION = "svc-1"


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


THREADS = max(2, _int("SPORTS_SERVICE_THREADS", "32"))
QUEUE_S = _int("SPORTS_SERVICE_QUEUE_MS", "200") / 1000.0
BODY_MAX = _int("SPORTS_BODY_MAX", "65536")
MAX_MB = _int("SPORTS_SERVICE_MAX_MB", "900")
STATS = {"requests": 0, "errors_5xx": 0, "rejected_busy": 0, "unauthorized": 0, "forbidden_origin": 0,
         "inflight": 0, "peak_inflight": 0, "ms_total": 0.0, "started": time.time()}
_SLOCK = threading.Lock()
_STATE = {"draining": False}
WORKER = {"enabled": False, "pid": None, "starts": 0, "last_exit": None, "last_start": None, "next_start": None}
BUSY_RESPONSE = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: 23\r\n"
                 b"Retry-After: 2\r\nConnection: close\r\n\r\n{\"error\":\"sports_busy\"}")


def rss_mb():
    try:
        import resource                                 # POSIX (Render)
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * resource.getpagesize() / 1e6
    except Exception:                                   # noqa: BLE001
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("a", wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in
                        ("PeakWS", "WS", "q1", "q2", "q3", "q4", "pf", "ppf")]
        k32, ps = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        m = PMC()
        m.cb = ctypes.sizeof(PMC)
        ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(m), m.cb)
        return m.WS / 1e6
    except Exception:                                   # noqa: BLE001
        return None


def allowed_origins():
    return {o.strip().rstrip("/") for o in os.environ.get("SPORTS_ALLOWED_ORIGINS", "").split(",") if o.strip()}


# ----------------------------------------------------------------------------------------------- feed freshness
_OPS = {"t": 0.0, "v": None}


def feed_state():
    """Per platform: newest offer sighting (last_seen_at / observed_at) and its age; store size. Cached 60 s."""
    if _OPS["v"] is not None and time.time() - _OPS["t"] < 60:
        return _OPS["v"]
    out = {"store_mb": None, "platforms": {}}
    try:
        p = web.store.db_path()
        out["store_mb"] = round(sum(f.stat().st_size for f in p.parent.glob(p.name + "*")) / 1e6, 1)
        with web.store_ro(budget_s=10) as con:
            rows = con.execute("SELECT platform, max(coalesce(last_seen_at, observed_at)) FROM records "
                               "WHERE kind = 'offer' GROUP BY platform").fetchall()
        now = time.time()
        for plat, last in rows:
            age = None
            try:
                from datetime import datetime
                age = round((now - datetime.fromisoformat(str(last).replace("Z", "+00:00")).timestamp()) / 60, 1)
            except Exception:                           # noqa: BLE001
                pass
            out["platforms"][plat] = {"last_seen": last, "age_min": age}
    except FileNotFoundError:
        out["store"] = "not_created"
    except Exception as e:                              # noqa: BLE001
        out["error"] = type(e).__name__
    _OPS.update(t=time.time(), v=out)
    return out


def ops():
    with _SLOCK:
        s = dict(STATS)
    s["avg_ms"] = round(s.pop("ms_total") / max(1, s["requests"]), 1)
    s["uptime_s"] = round(time.time() - s.pop("started"))
    return {"version": VERSION, "service": s, "threads_max": THREADS, "rss_mb": round(rss_mb() or 0, 1),
            "rss_max_mb": MAX_MB, "draining": _STATE["draining"], "threads": threading.active_count(),
            "db_pool": web.pool_stats(), "cache": web.cache_stats(), "flags": flags.state(), "worker": dict(WORKER),
            "token_configured": authtoken.configured(), "feeds": feed_state()}


# ------------------------------------------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"                       # keep-alive (browsers, the gateway pool)
    server_version = "scout-sports"
    sys_version = ""

    def log_message(self, fmt, *a):                     # no access log lines (tokens never logged)
        pass

    def _cors(self):
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if not origin:
            return {}, True
        if origin in allowed_origins():
            return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Headers": "Authorization, Content-Type",
                    "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Max-Age": "600"}, True
        return {}, False

    def _send(self, code, body, ctype="application/json", headers=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, default=str).encode("utf-8")
        with _SLOCK:
            STATS["requests"] += 1
            STATS["ms_total"] += (time.time() - self._t0) * 1000
            if code >= 500:
                STATS["errors_5xx"] += 1
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if _STATE["draining"]:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            self.close_connection = True

    def do_OPTIONS(self):
        self._t0 = time.time()
        cors, ok = self._cors()
        if not ok:
            with _SLOCK:
                STATS["forbidden_origin"] += 1
            return self._send(403, {"error": "origin_not_allowed"})
        return self._send(204, b"", "text/plain", cors)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def _serve(self, method):
        self._t0 = time.time()
        u = urlparse(self.path)
        cors, ok = self._cors()
        if method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > BODY_MAX:
                self.close_connection = True
                return self._send(413 if n > BODY_MAX else 400, {"error": "body_too_large" if n > 0 else "bad_length"},
                                  headers=cors)
        if not ok:
            self.close_connection = True
            with _SLOCK:
                STATS["forbidden_origin"] += 1
            return self._send(403, {"error": "origin_not_allowed"})
        if u.path == "/api/sports/health":
            return self._send(200, {"ok": not _STATE["draining"], "version": VERSION,
                                    "store_ready": web.store.db_path().exists()}, headers=cors)
        claims, why = None, "missing"
        tok = authtoken.bearer(self.headers.get("Authorization"))
        if tok:
            try:
                claims = authtoken.verify(tok)
            except authtoken.TokenError as e:
                why = e.reason
        admin_header = self.headers.get("X-Admin-Token")
        ops_admin = bool(admin_header and os.environ.get("ADMIN_TOKEN")
                         and __import__("hmac").compare_digest(admin_header, os.environ["ADMIN_TOKEN"]))
        if u.path == "/api/sports/admin/ops":
            if ops_admin or (claims and claims["role"] == "admin"):
                return self._send(200, ops(), headers=cors)
            return self._send(403, {"error": "admin_only"}, headers=cors)
        if claims is None:
            with _SLOCK:
                STATS["unauthorized"] += 1
            if method == "POST":
                self.close_connection = True
            code = 503 if why == "not_configured" else 401
            return self._send(code, {"error": "sports_auth_not_configured" if code == 503 else "members_only",
                                     "why": why}, headers=cors)
        if not flags.on("SPORTS_API"):
            return self._send(404, {"error": "not found"}, headers=cors)
        body = b""
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        req = web.Request(path=u.path, query=q, method=method, body=body, member=claims["sub"], role=claims["role"])
        try:
            code, data, ctype = web.dispatch(req)
        except Exception as e:                          # noqa: BLE001 - one route's failure is that route's 503
            sys.stderr.write(f"  sports route {u.path} failed: {type(e).__name__}: {str(e)[:200]}\n")
            code, data, ctype = 503, {"error": "sports_unavailable"}, "application/json"
        return self._send(code, data, ctype, cors)


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with a HARD concurrency cap: at most THREADS requests run; a connection that cannot get a
    slot within QUEUE_S gets a canned 503 on the accept thread and is closed (no unbounded thread / memory growth)."""
    daemon_threads = True
    request_queue_size = 1024
    allow_reuse_address = True

    def __init__(self, addr, handler=Handler, threads=None):
        super().__init__(addr, handler)
        self.slots = threading.BoundedSemaphore(threads or THREADS)

    def process_request(self, request, client_address):
        if _STATE["draining"] or not self.slots.acquire(timeout=QUEUE_S):
            with _SLOCK:
                STATS["rejected_busy"] += 1
            try:
                request.sendall(BUSY_RESPONSE)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        with _SLOCK:
            STATS["inflight"] += 1
            STATS["peak_inflight"] = max(STATS["peak_inflight"], STATS["inflight"])
        try:
            request.settimeout(30)                      # an idle kept-alive / slow client frees its slot
        except OSError:
            pass
        t = threading.Thread(target=self._run, args=(request, client_address), daemon=True)
        t.start()

    def _run(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:                               # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            with _SLOCK:
                STATS["inflight"] -= 1
            self.slots.release()

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if not isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.timeout)):
            sys.stderr.write(f"  sports service error: {type(exc).__name__}: {str(exc)[:200]}\n")


# ------------------------------------------------------------------------------------------- memory watchdog
def _watchdog(srv, every=5.0):
    while True:
        time.sleep(every)
        mb = rss_mb()
        if mb and mb > MAX_MB and not _STATE["draining"]:
            sys.stderr.write(f"sports service rss {mb:.0f} MB > {MAX_MB} MB: draining for a restart\n")
            _STATE["draining"] = True
            deadline = time.time() + 20
            while STATS["inflight"] > 0 and time.time() < deadline:
                time.sleep(0.2)
            os._exit(3)                                 # Render restarts the service


# --------------------------------------------------------------------------------------- ingestion supervisor
def backoff(attempt, base=30.0, cap=900.0):
    return min(cap, base * (2 ** attempt)) * (0.5 + random.random() / 2)


def supervise_worker(argv=None, stop=None):
    """Run the ingestion loop as a child process (own memory cap SPORTS_WORKER_MAX_MB) and restart it when it exits.
    Only with SPORTS_INGEST on; flipping the flag off and restarting the service stops ingestion."""
    if not flags.on("SPORTS_INGEST"):
        return
    leagues = os.environ.get("SPORTS_LEAGUES", "nba nfl").split()
    argv = argv or [sys.executable, "-m", "sports.worker", "loop", *leagues,
                    "--every", os.environ.get("SPORTS_INGEST_EVERY_MIN", "10"),
                    "--kalshi-every", os.environ.get("SPORTS_KALSHI_EVERY_MIN", "30")]
    WORKER["enabled"] = True
    attempt = 0
    while not (stop and stop.is_set()):
        WORKER.update(last_start=time.time(), starts=WORKER["starts"] + 1, next_start=None)
        t0 = time.time()
        try:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL)
            WORKER["pid"] = p.pid
            rc = p.wait()
        except OSError as e:
            rc = f"spawn failed: {e}"
        WORKER.update(pid=None, last_exit={"code": rc, "at": time.time(), "ran_s": round(time.time() - t0)})
        attempt = 0 if time.time() - t0 > 600 else attempt + 1
        wait = backoff(attempt)
        WORKER["next_start"] = time.time() + wait
        if stop and stop.wait(wait):
            break
        if not stop:
            time.sleep(wait)


def serve(host="0.0.0.0", port=None):
    port = int(port or os.environ.get("PORT", "10000"))
    srv = Server((host, port))
    threading.Thread(target=_watchdog, args=(srv,), daemon=True, name="sports-watchdog").start()
    threading.Thread(target=supervise_worker, daemon=True, name="sports-worker-supervisor").start()
    print(f"scout sports service {VERSION} on {host}:{srv.server_address[1]} (threads {THREADS}, db pool "
          f"{web.DB_POOL}, token {'set' if authtoken.configured() else 'NOT SET - every request refused'})",
          flush=True)
    return srv


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.service")
    p.add_argument("--host", default=os.environ.get("SPORTS_SERVICE_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=None)
    a = p.parse_args(argv)
    srv = serve(a.host, a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
