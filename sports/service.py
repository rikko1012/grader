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
Worker side: this process runs the SCHEDULE (schedule()) as CHILD processes, never in a request thread: long-running
children (ingestion loop, assistant runner, lab runner; restarted with jittered exponential backoff) and periodic jobs
(snapshot prebuild, slip spool ingest, slip settlement; each with a timeout). Children write the store / their own
databases; this process only reads.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
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

from . import authtoken, flags, service_life, web

VERSION = "svc-1"


def _int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return int(default)


THREADS = max(2, _int("SPORTS_SERVICE_THREADS", "16"))     # Starter (512 MB / 0.5 CPU)
QUEUE_S = _int("SPORTS_SERVICE_QUEUE_MS", "200") / 1000.0
BODY_MAX = _int("SPORTS_BODY_MAX", "65536")
from . import scheduler as _plan                        # noqa: E402 - plan size only (no job is started here)
# service drain-and-restart ceiling: Starter 160 MB, Standard (>= 1.5 GB, 2026-10-25) 400 MB; children <= budget
MAX_MB = _int("SPORTS_SERVICE_MAX_MB", "400" if _plan.standard() else "160")
STATS = {"requests": 0, "errors_5xx": 0, "rejected_busy": 0, "unauthorized": 0, "forbidden_origin": 0,
         "health_bypass": 0, "inflight": 0, "peak_inflight": 0, "ms_total": 0.0, "started": time.time()}
_SLOCK = threading.Lock()
_STATE = {"draining": False}
_PATHS = {}                                             # thread id -> path being served (named in the exit record)
TRIM_FRAC = 0.8                                         # malloc_trim when RSS passes this share of MAX_MB (<= 1/min)
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


# ------------------------------------------------------------------------------- security headers (switches, off = none)
# Same contract as server.py csp_headers (duplicated on purpose: this process imports no root module): SCOUT_CSP unset / "0"
# = no header, "report" = Content-Security-Policy-Report-Only, "1" = enforced; frame-ancestors from SCOUT_FRAME_ANCESTORS
# (Whop frames the member pages, so never an X-Frame-Options header); SCOUT_HSTS=1 adds Strict-Transport-Security. This
# service answers JSON to the member page on another origin, so the default policy is the strict API shape.
CSP_MODES = {"1": "Content-Security-Policy", "true": "Content-Security-Policy", "on": "Content-Security-Policy",
             "yes": "Content-Security-Policy", "report": "Content-Security-Policy-Report-Only"}
CSP_FRAME_ANCESTORS_DEFAULT = "'self' https://whop.com https://*.whop.com"
CSP_POLICY_DEFAULT = "default-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"


def _csp_list(value, default=""):
    items = (value or "").replace(",", " ").split()
    return " ".join(items) if items else default


def csp_headers(env=None):
    """-> [(header, value), ...] the SCOUT_CSP / SCOUT_HSTS switches ask for; [] with both off."""
    env = os.environ if env is None else env
    out = []
    name = CSP_MODES.get((env.get("SCOUT_CSP") or "").strip().lower())
    if name:
        policy = _csp_list(env.get("SCOUT_CSP_POLICY")) or CSP_POLICY_DEFAULT
        if "frame-ancestors" not in policy:
            policy += "; frame-ancestors " + _csp_list(env.get("SCOUT_FRAME_ANCESTORS"), CSP_FRAME_ANCESTORS_DEFAULT)
        report = _csp_list(env.get("SCOUT_CSP_REPORT_URI"))
        if report and "report-uri" not in policy:
            policy += "; report-uri " + report
        out.append((name, policy))
    if (env.get("SCOUT_HSTS") or "").strip().lower() in ("1", "true", "on", "yes"):
        age = (env.get("SCOUT_HSTS_MAX_AGE") or "").strip()
        out.append(("Strict-Transport-Security", "max-age=" + (age if age.isdigit() else "31536000")))
    return out


# ------------------------------------------------------------------------------- shared encoded bodies (gzip + ETag)
GZIP_MIN = 1024
ENC_MAX_ENTRIES = 32
ENC_MAX_BYTES = _int("SPORTS_GZIP_CACHE_MB", "12") * 1_000_000     # Starter: 12 MB of gzip copies
_ENC = {}                                               # etag -> gzip bytes (insertion order = age)
_ENC_FLIGHT = {}
_ENC_LOCK = threading.Lock()
ENC_STATS = {"gzip_built": 0, "gzip_served": 0, "identity_served": 0, "not_modified": 0, "bytes_out": 0}


def etag_of(body):
    return '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'


def etag_matches(header, etag):
    if not header:
        return False
    gz = etag[:-1] + '-gz"'
    for t in header.split(","):
        t = t.strip()
        if t.startswith("W/"):
            t = t[2:]
        if t in (etag, gz, "*"):
            return True
    return False


def gzipped(etag, body):
    """The gzip copy of a body, made ONCE per ETag (single-flight: simultaneous misses wait for one compression)."""
    with _ENC_LOCK:
        hit = _ENC.get(etag)
        if hit is not None:
            return hit
        ev = _ENC_FLIGHT.get(etag)
        leader = ev is None
        if leader:
            ev = _ENC_FLIGHT[etag] = threading.Event()
    if not leader:
        ev.wait(5)
        with _ENC_LOCK:
            hit = _ENC.get(etag)
        return hit if hit is not None else gzip.compress(body, 6)
    try:
        gz = gzip.compress(body, 6)
        with _ENC_LOCK:
            ENC_STATS["gzip_built"] += 1
            _ENC[etag] = gz
            while len(_ENC) > ENC_MAX_ENTRIES or sum(len(v) for v in _ENC.values()) > ENC_MAX_BYTES:
                _ENC.pop(next(iter(_ENC)))
        return gz
    finally:
        with _ENC_LOCK:
            _ENC_FLIGHT.pop(etag, None)
        ev.set()


def accepts_gzip(header):
    for part in (header or "").lower().split(","):
        name, _, q = part.strip().partition(";")
        if name.strip() in ("gzip", "*") and q.strip() not in ("q=0", "q=0.0", "q=0.00", "q=0.000"):
            return True
    return False


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
            rows = con.execute(web.store.FEED_SIGHTINGS_SQL).fetchall()
        now = time.time()
        for plat, last in rows:
            age = None
            try:
                from datetime import datetime
                age = round((now - datetime.fromisoformat(str(last).replace("Z", "+00:00")).timestamp()) / 60, 1)
            except Exception:                           # noqa: BLE001
                pass
            out["platforms"][plat] = {"last_seen": last, "age_min": age}
        with web.store_ro(budget_s=10) as con:
            from . import archive
            out["archive"] = archive.stats(con)
    except FileNotFoundError:
        out["store"] = "not_created"
    except Exception as e:                              # noqa: BLE001
        out["error"] = type(e).__name__
    try:                                                # how far the latest archive pass got (a killed one included)
        from . import archive
        last = archive.last_pass()
        if last is not None:
            out.setdefault("archive", {})["last_pass"] = last
    except Exception:                                   # noqa: BLE001
        pass
    try:                                                # the disk the Starter plan pays for (alerts at 80 / 90 %)
        import shutil
        from . import config
        du = shutil.disk_usage(config.DATA_DIR)
        out["disk"] = {"total_mb": round(du.total / 1e6), "used_mb": round(du.used / 1e6),
                       "used_pct": round(100.0 * du.used / du.total, 1)}
        areas = {}
        for f in config.DATA_DIR.iterdir():
            n = sum(x.stat().st_size for x in f.rglob("*") if x.is_file()) if f.is_dir() else f.stat().st_size
            key = f.name.split(".")[0] if f.is_file() else f.name
            areas[key] = areas.get(key, 0) + n
        out["data_mb"] = {k: round(v / 1e6, 1) for k, v in sorted(areas.items(), key=lambda kv: -kv[1])}
    except (OSError, ValueError):
        pass
    _OPS.update(t=time.time(), v=out)
    return out


ALERT_RULES = {                                         # sports/RUNBOOK.md section 9 - thresholds in one place
    "feed_stale_min": float(os.environ.get("SPORTS_ALERT_FEED_STALE_MIN", "45")),     # pick'em feeds: 10-min cadence
    "kalshi_stale_min": float(os.environ.get("SPORTS_ALERT_KALSHI_STALE_MIN", "150")),  # Kalshi: hourly (Starter)
    "store_warn_mb": float(os.environ.get("SPORTS_ALERT_STORE_MB", "6000")),
    "rss_warn_frac": 0.8,
}


def alerts(o):
    """-> [{"level": "page"|"warn", "code", "detail"}] from an ops() snapshot. Pure function (tested)."""
    out = []
    ingest_on = o["flags"].get("SPORTS_INGEST")
    sched = o.get("scheduler") or {}
    jobs = sched.get("jobs") or {}
    daily_state = o.get("grades_daily") or {}
    if (jobs.get("grades-daily") or {}).get("enabled"):
        if daily_state.get("last_error"):
            out.append({"level": "page", "code": "daily_grading_incomplete", "detail": daily_state["last_error"]})
        if daily_state.get("publish_pending"):
            out.append({"level": "warn", "code": "daily_report_unpublished", "detail": {
                "attempt_day": daily_state.get("attempt_day")}})
    if o.get("scheduler_alive") is False:
        out.append({"level": "page", "code": "scheduler_down", "detail": "the job scheduler thread is not running"})
    # the collector: "ingest" (one job) or one "ingest-<league>" job per league (game-day cadence, scheduler.py)
    for jname in sorted(n for n in jobs if n == "ingest" or (n.startswith("ingest-") and n != "ingest-kalshi")):
        ing = jobs.get(jname) or {}
        if not (ingest_on and ing.get("enabled")):
            continue
        last_ok = ing.get("last_ok_at")
        every = max(ing.get("every_s") or 900, 900)     # a 5-minute pre-tip cadence does not tighten the page
        if last_ok is None and (sched.get("uptime_s") or 0) > 3 * every:
            out.append({"level": "page", "code": "worker_down", "detail": {"job": jname, "last_exit": ing.get("last_exit")}})
        elif last_ok is not None and time.time() - last_ok > 3 * every:
            out.append({"level": "page", "code": "worker_down", "detail": {"job": jname, "last_ok_age_s":
                                                                           round(time.time() - last_ok)}})
    feeds = o.get("feeds") or {}
    if ingest_on:
        if feeds.get("error") or feeds.get("store") == "not_created":
            out.append({"level": "warn", "code": "feed_status_unavailable",
                        "detail": {"error": feeds.get("error") or feeds.get("store")}})
        for plat, f in (feeds.get("platforms") or {}).items():
            lim = ALERT_RULES["kalshi_stale_min"] if plat.startswith("kalshi") else ALERT_RULES["feed_stale_min"]
            if f.get("age_min") is None or f["age_min"] > lim:
                out.append({"level": "warn", "code": "feed_stale", "detail": {"platform": plat, **f, "limit_min": lim}})
    # the same measure the worker pauses on: the whole data directory against store_cap_mb (2026-11-05: the alert read
    # canonical.db alone, so ingestion paused for ~14 h with no store_full alert)
    mb = sum(v for v in (feeds.get("data_mb") or {}).values() if isinstance(v, (int, float))) or feeds.get("store_mb")
    try:
        from .worker import store_cap_mb
        stop_mb = store_cap_mb(float(os.environ.get("SPORTS_STORE_MAX_MB", "1500")))[0]   # read now, as before
    except Exception:                                   # noqa: BLE001 - the alert never breaks ops
        stop_mb = float(os.environ.get("SPORTS_STORE_MAX_MB", "1500"))
    if mb is not None and mb >= stop_mb:
        out.append({"level": "page", "code": "store_full_ingest_paused", "detail": {"store_mb": mb, "max_mb": stop_mb}})
    elif mb is not None and mb >= min(ALERT_RULES["store_warn_mb"], 0.8 * stop_mb):
        out.append({"level": "warn", "code": "store_large", "detail": {"store_mb": mb, "max_mb": stop_mb}})
    disk = feeds.get("disk") or {}
    if disk.get("used_pct") is not None and disk["used_pct"] >= 90:
        out.append({"level": "page", "code": "disk_90", "detail": disk})
    elif disk.get("used_pct") is not None and disk["used_pct"] >= 80:
        out.append({"level": "warn", "code": "disk_80", "detail": disk})
    if o.get("rss_mb") and o["rss_mb"] > ALERT_RULES["rss_warn_frac"] * o["rss_max_mb"]:
        out.append({"level": "warn", "code": "service_memory_high", "detail": {"rss_mb": o["rss_mb"]}})
    svc = o["service"]
    if svc["requests"] and (svc["rejected_busy"] + svc["errors_5xx"]) / svc["requests"] > 0.01:
        out.append({"level": "warn", "code": "service_shedding", "detail": {k: svc[k] for k in
                                                                         ("requests", "rejected_busy", "errors_5xx")}})
    pool = o["db_pool"]
    # Keep lifetime counters for diagnosis, but a recovered incident must stop paging the dashboard.
    recent_error = (pool.get("last_error_at") is not None and time.time() - pool["last_error_at"] <= 300)
    legacy_error = "last_error_at" not in pool and (pool.get("waits_timed_out") or pool.get("queries_interrupted"))
    if recent_error or legacy_error:
        out.append({"level": "warn", "code": "store_slow", "detail": o["db_pool"]})
    for name, j in jobs.items():
        if not j.get("enabled"):
            continue
        # These stages used to stay quiet during the long retry backoff: unlike
        # collectors they had no success-age check. A healthy HTTP server alone
        # does not mean new forecasts or settlements are being produced.
        if name in {"model-forward", "books", "settle", "settle-post", "picks-settle", "slips-settle", "grades-daily"}:
            every = j.get("every_s")
            if every:
                limit = max(3 * every, every + (j.get("timeout_s") or 0), 1800)
                last_ok = j.get("last_ok_at")
                age = time.time() - last_ok if last_ok is not None else (sched.get("uptime_s") or 0)
                if age > limit:
                    out.append({"level": "page", "code": "grading_job_stale", "detail": {
                        "job": name, "success_age_s": round(age), "limit_s": limit,
                        "last_exit": j.get("last_exit"), "next_start": j.get("next_start")}})
        if j.get("failures_in_a_row", 0) >= 3:
            out.append({"level": "warn", "code": "job_failing", "detail": {"job": name, "last_exit": j.get("last_exit")}})
        code = str((j.get("last_exit") or {}).get("code"))
        if code.startswith("killed_over_cap") and j.get("failures_in_a_row", 0) >= 2:
            out.append({"level": "page", "code": "job_over_cap", "detail": {"job": name, "cap_mb": j.get("cap_mb"),
                                                                           "last_exit": j.get("last_exit")}})
        qs = j.get("queued_since")
        if qs and time.time() - qs > max(1800, 2 * (j.get("every_s") or 0)):
            out.append({"level": "warn", "code": "job_starved", "detail": {"job": name, "waiting_s": round(time.time() - qs)}})
    if o["flags"].get("SPORTS_API"):
        for name, sn in ((o.get("prebuilt") or {}).get("snapshots") or {}).items():
            if sn.get("age_s") is None or sn["age_s"] > 3 * sn["max_age_s"]:
                out.append({"level": "warn", "code": "snapshot_stale", "detail": {"snapshot": name, **sn}})
    if not o.get("token_configured"):
        out.append({"level": "page", "code": "token_secret_missing", "detail": "SPORTS_TOKEN_SECRET unset or short"})
    return out


_SCHED = {"obj": None, "thread": None, "started": None}


def scheduler_state():
    from . import scheduler
    st = scheduler.state()
    st["uptime_s"] = round(time.time() - _SCHED["started"]) if _SCHED["started"] else None
    return st


def prebuilt_state():
    try:
        from . import prebuilt
        return prebuilt.state()
    except Exception as e:                              # noqa: BLE001
        return {"error": type(e).__name__}


def board_builds():
    """league -> the last finished board build: seconds, per-stage seconds, counts (admin ops only)."""
    from . import snapshots
    out = {}
    for lg in os.environ.get("SPORTS_LEAGUES", "nba nfl").split():
        try:
            m = json.loads((snapshots.snap_root() / lg / "manifest.json").read_text(encoding="utf-8"))
            out[lg] = {k: m.get(k) for k in ("built_at", "build_s", "build_stage_s", "counts")}
        except (OSError, ValueError):
            out[lg] = None
    return out


def settle_stages(name="settle_stages.json"):
    """The last finished settle run: seconds since its start at the end of each stage (admin ops only).
    settle_post_stages.json: the settle-post job (results ledger + scorecards), written after each stage."""
    from . import config
    try:
        return json.loads((config.DATA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sheets_stages():
    """The sheets job's steps: this run (finished false after a kill names the step) and the last finished run."""
    from . import config
    try:
        return json.loads((config.DATA_DIR / "sheets_stages.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def expansion_learning_status():
    """Small worker-written reports only. Never starts fitting or reads a DB."""
    from pathlib import Path
    from . import config
    root = Path(os.environ.get('SCOUT_EXPANSION_DATA_DIR') or config.DATA_DIR / 'expansion')
    out = {}
    for sport in ('soccer','tennis'):
        enabled = os.environ.get(f'SCOUT_{sport.upper()}_ENABLED') == '1'
        row = {'enabled':enabled,'report':None}
        if enabled:
            try:
                path = root / sport / 'learning-status.json'
                if path.stat().st_size <= 65536:
                    row['report'] = json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):
                pass
        out[sport] = row
    return out


def ops():
    with _SLOCK:
        s = dict(STATS)
    s["avg_ms"] = round(s.pop("ms_total") / max(1, s["requests"]), 1)
    s["uptime_s"] = round(time.time() - s.pop("started"))
    return {"version": VERSION, "service": s, "threads_max": THREADS, "rss_mb": round(rss_mb() or 0, 1),
            "rss_max_mb": MAX_MB, "draining": _STATE["draining"], "threads": threading.active_count(),
            "db_pool": web.pool_stats(), "cache": web.cache_stats(), "flags": flags.state(), "scheduler": scheduler_state(),
            "scheduler_alive": bool(_SCHED["thread"] and _SCHED["thread"].is_alive()) if _SCHED["thread"] else None,
            "prebuilt": prebuilt_state(),
            "board_builds": board_builds(),
            "settle_stages": settle_stages(), "settle_post_stages": settle_stages("settle_post_stages.json"),
            "sheets_stages": sheets_stages(),
            "grades_daily": settle_stages("model/daily_state.json"),
            "expansion_learning": expansion_learning_status(),
            "token_configured": authtoken.configured(), "feeds": feed_state(),
            "encoding": dict(ENC_STATS, cached_bodies=len(_ENC)),
            "restarts": service_life.recent(10), "cgroup": service_life.cgroup()}


def ops_with_alerts():
    o = ops()
    o["alerts"] = alerts(o)
    return o


# ------------------------------------------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"                       # keep-alive (browsers, the gateway pool)
    one_shot = False                                    # True: the reserved health path (Server.process_request)
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
        headers = dict(headers or {})
        cache = "no-store"
        if self.command == "GET" and code == 200 and len(body) >= 256 and getattr(self, "_member_ok", False):
            # member answers (snapshot-backed, they change every ~45 s): revalidate with ETag -> 304, gzip once per body
            etag = etag_of(body)
            cache = "private, no-cache"
            headers["Vary"] = "Accept-Encoding" + (", Origin" if "Vary" in headers else "")
            if etag_matches(self.headers.get("If-None-Match"), etag):
                with _ENC_LOCK:
                    ENC_STATS["not_modified"] += 1
                headers["ETag"] = etag
                return self._send_raw(304, b"", ctype, headers, cache)
            if len(body) >= GZIP_MIN and accepts_gzip(self.headers.get("Accept-Encoding")):
                body = gzipped(etag, body)
                headers["Content-Encoding"] = "gzip"
                headers["ETag"] = etag[:-1] + '-gz"'
                with _ENC_LOCK:
                    ENC_STATS["gzip_served"] += 1
            else:
                headers["ETag"] = etag
                with _ENC_LOCK:
                    ENC_STATS["identity_served"] += 1
        return self._send_raw(code, body, ctype, headers, cache)

    def _send_raw(self, code, body, ctype, headers, cache):
        with _ENC_LOCK:
            ENC_STATS["bytes_out"] += len(body)
        with _SLOCK:
            STATS["requests"] += 1
            STATS["ms_total"] += (time.time() - self._t0) * 1000
            if code >= 500:
                STATS["errors_5xx"] += 1
        try:
            self.send_response(code)
            if code != 304:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in csp_headers():                  # SCOUT_CSP / SCOUT_HSTS switches: off = nothing added here
                self.send_header(k, v)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if _STATE["draining"]:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD" and code != 304:
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
        _PATHS[threading.get_ident()] = "GET " + self.path.split("?")[0][:120]
        try:
            self._serve("GET")
        finally:
            _PATHS.pop(threading.get_ident(), None)

    def do_POST(self):
        _PATHS[threading.get_ident()] = "POST " + self.path.split("?")[0][:120]
        try:
            self._serve("POST")
        finally:
            _PATHS.pop(threading.get_ident(), None)

    def _serve(self, method):
        self._t0 = time.time()
        self._member_ok = False
        u = urlparse(self.path)
        if self.one_shot:                               # no request slot is held: health only, one answer, then close
            self.close_connection = True
            if method != "GET" or u.path != "/api/sports/health":
                return self._send(503, {"error": "sports_busy"})
        cors, ok = self._cors()
        body = b""
        if method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > BODY_MAX:
                self.close_connection = True
                return self._send(413 if n > BODY_MAX else 400, {"error": "body_too_large" if n > 0 else "bad_length"},
                                  headers=cors)
            # bug sweep 2026-11-06 #11: drain the body before ANY early answer (health, admin/ops 403, flag off) - unread
            # bytes on a kept-alive connection were parsed as the next request (desync / request smuggling). A chunked
            # body is never parsed here, so that connection is closed after the answer.
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
            try:
                body = self.rfile.read(n) if n else b""
            except (OSError, socket.timeout):
                self.close_connection = True
                return
            if len(body) != n:                          # client sent less than it said: nothing left to trust
                self.close_connection = True
                return
        if not ok:
            self.close_connection = True
            with _SLOCK:
                STATS["forbidden_origin"] += 1
            return self._send(403, {"error": "origin_not_allowed"})
        if u.path == "/api/sports/health":
            return self._send(200, {"ok": not _STATE["draining"], "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:40], "version": VERSION,
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
                         and __import__("hmac").compare_digest(admin_header.encode("utf-8", "replace"),
                                                               os.environ["ADMIN_TOKEN"].encode("utf-8")))
        if u.path == "/api/sports/admin/ops":
            if ops_admin or (claims and claims["role"] == "admin"):
                return self._send(200, ops_with_alerts(), headers=cors)
            return self._send(403, {"error": "admin_only"}, headers=cors)
        if claims is None and web.public_open(u.path, method):     # PUBLIC_WHEN flag on: no token needed
            claims = {"sub": None, "role": None}
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
        q ={k: v[0] for k, v in parse_qs(u.query).items()}
        req = web.Request(path=u.path, query=q, method=method, body=body, member=claims["sub"], role=claims["role"])
        self._member_ok = True
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
        # The platform's health check (render healthCheckPath) must not depend on request capacity: slow reads or
        # idle keep-alive connections holding every slot answered it with the canned 503, which reads as an
        # unhealthy instance. Two reserved slots serve that one route, one answer per connection.
        self.health_slots = threading.BoundedSemaphore(2)
        self._health_handler = type("HealthHandler", (handler,), {"one_shot": True})

    @staticmethod
    def _is_health(request):
        try:
            request.settimeout(0.05)                    # the accept thread waits at most this long for the first bytes
            return request.recv(64, socket.MSG_PEEK).startswith(b"GET /api/sports/health")
        except (OSError, ValueError):
            return False

    def process_request(self, request, client_address):
        if _STATE["draining"] or not self.slots.acquire(timeout=QUEUE_S):
            if not _STATE["draining"] and self._is_health(request) and self.health_slots.acquire(blocking=False):
                with _SLOCK:
                    STATS["health_bypass"] += 1
                try:
                    request.settimeout(5)
                    threading.Thread(target=self._run_health, args=(request, client_address), daemon=True).start()
                except (OSError, RuntimeError):
                    self.health_slots.release()
                    self.shutdown_request(request)
                return
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
        try:
            threading.Thread(target=self._run, args=(request, client_address), daemon=True).start()
        except RuntimeError:                            # no thread could start: give the slot back (it leaked before)
            with _SLOCK:
                STATS["inflight"] -= 1
            self.slots.release()
            self.shutdown_request(request)

    def _run_health(self, request, client_address):
        try:
            self._health_handler(request, client_address, self)
        except Exception:                               # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self.health_slots.release()

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
def _exit_detail(mb):
    """What the service held when the watchdog fired (every part optional; never raises)."""
    d = {"rss_mb": round(mb or 0, 1), "max_mb": MAX_MB, "inflight": STATS.get("inflight"),
         "serving": sorted(_PATHS.values())[:40]}
    for name, fn in (("web_cache", web.cache_stats), ("encoded_bodies", lambda: len(_ENC)),
                     ("encoded_bytes", lambda: sum(len(v) for v in list(_ENC.values()))),
                     ("children", lambda: {k: v.get("rss_mb") for k, v in (scheduler_state().get("jobs") or {}).items()
                                           if v.get("pid")})):
        try:
            d[name] = fn()
        except Exception:                               # noqa: BLE001
            d[name] = None
    try:
        from . import expansion_board
        d["expansion_prepared"] = len(expansion_board._PREPARED)
    except Exception:                                   # noqa: BLE001
        pass
    return d


def _record_exit(reason, mb=None, wait_s=3.0):
    """Write the exit record from a daemon thread and wait at most wait_s: a lock held by a request thread or a slow
    disk never stops the process from exiting."""
    t = threading.Thread(target=lambda: service_life.exit_record(reason, **_exit_detail(mb if mb is not None else rss_mb())),
                         daemon=True, name="sports-exit-record")
    t.start()
    t.join(wait_s)


def _on_sigterm(signum, frame):
    """Render stops (deploys, restarts it starts) arrive as SIGTERM: record them so the next boot can tell them from an
    OOM kill / crash ("unclean"), then terminate exactly as before."""
    import signal
    _STATE["draining"] = True
    _record_exit("sigterm", wait_s=2.0)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGTERM)


def _watchdog(srv, every=5.0):
    last_trim = 0.0
    while True:
        time.sleep(every)
        mb = rss_mb()
        service_life.sample(mb)
        if mb and mb > TRIM_FRAC * MAX_MB and time.time() - last_trim >= 60:
            last_trim = time.time()                     # freed heap back to the OS before deciding to restart
            if service_life.trim():
                mb = rss_mb()
        if mb and mb > MAX_MB and not _STATE["draining"]:
            sys.stderr.write(f"sports service rss {mb:.0f} MB > {MAX_MB} MB: draining for a restart\n")
            _STATE["draining"] = True                   # stop taking work first; the record can never delay the exit
            _record_exit("rss_over_max", mb)
            deadline = time.time() + 20
            while STATS["inflight"] > 0 and time.time() < deadline:
                time.sleep(0.2)
            os._exit(3)                                 # Render restarts the service


# --------------------------------------------------------------------------------------- ingestion supervisor
def backoff(attempt, base=30.0, cap=900.0):
    return min(cap, base * (2 ** attempt)) * (0.5 + random.random() / 2)


def schedule():
    """The worker-side job table (sports/scheduler.py default_jobs: cadence, memory cap, class, flag)."""
    from . import scheduler
    return scheduler.default_jobs()


def supervise(stop=None, jobs=None):
    """Start the ONE scheduler thread (memory-reservation queue over every job whose flag is on). -> Scheduler."""
    from . import scheduler
    from . import config
    # recent run times persist across deploys (the drain fill-in rule needs them at once; review 2026-11-09)
    sch = scheduler.Scheduler(jobs=jobs, runs_path=config.DATA_DIR / "scheduler_runs.json")
    if stop is not None:
        sch.stop_ev = stop
    t = threading.Thread(target=sch.run, daemon=True, name="sports-scheduler")
    t.start()
    _SCHED.update(obj=sch, thread=t, started=time.time())
    return sch


def serve(host="0.0.0.0", port=None):
    port = int(port or os.environ.get("PORT", "10000"))
    srv = Server((host, port))
    boot = service_life.boot(VERSION)                   # names the previous exit (or "unclean") on disk + in ops
    if boot and boot.get("previous_exit") == "unclean":
        sys.stderr.write("sports service: previous run ended without an exit record (OOM kill / crash / host restart)\n")
    try:
        import signal
        signal.signal(signal.SIGTERM, _on_sigterm)
    except (ValueError, AttributeError, OSError):          # not the main thread / platform without it: no record
        pass
    threading.Thread(target=_watchdog, args=(srv,), daemon=True, name="sports-watchdog").start()
    supervise()
    print(f"scout sports service {VERSION} on {host}:{srv.server_address[1]} (threads {THREADS}, db pool "
          f"{web.DB_POOL}, token {'set' if authtoken.configured() else 'NOT SET - every request refused'})",
          flush=True)
    return srv


def check(url):
    """On-call / cron check: python -m sports.service --check https://scout-sports-api.onrender.com
    (needs ADMIN_TOKEN in the environment). Prints the alerts; exit 2 on a page, 1 on a warning, 0 when clean."""
    import urllib.request
    req = urllib.request.Request(url.rstrip("/") + "/api/sports/admin/ops",
                                 headers={"X-Admin-Token": os.environ.get("ADMIN_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            o = json.loads(r.read())
    except Exception as e:                              # noqa: BLE001
        print(json.dumps({"level": "page", "code": "service_unreachable", "detail": f"{type(e).__name__}: {e}"}))
        return 2
    for a in o.get("alerts", []):
        print(json.dumps(a, default=str))
    levels = {a["level"] for a in o.get("alerts", [])}
    return 2 if "page" in levels else 1 if levels else 0


# owner 2026-11-05: these display-only switches are ON unless Render says otherwise ("0"/"off"); set when the service runs (its
# scheduled jobs inherit them), never on import, so tests keep their explicit off checks. SCOUT_BOARD_H2H is read by the sheets job.
DEFAULT_ON = ("SPORTS_CONTEXT_RECONCILE", "SPORTS_MARKET_FALLBACK", "SCOUT_BOARD_H2H")


def apply_default_flags(env=os.environ):
    for k in DEFAULT_ON:
        env.setdefault(k, "1")


def main(argv=None):
    apply_default_flags()
    p = argparse.ArgumentParser(prog="python -m sports.service")
    p.add_argument("--host", default=os.environ.get("SPORTS_SERVICE_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--check", metavar="URL", help="print the service's alerts and exit (0 clean, 1 warn, 2 page)")
    p.add_argument("cmd", nargs="*", help="snapshots [name ...]: build the prebuilt snapshots now and exit")
    a = p.parse_args(argv)
    if a.check:
        raise SystemExit(check(a.check))
    if a.cmd and a.cmd[0] == "snapshots":
        from . import prebuilt
        rep = prebuilt.build(a.cmd[1:] or None)
        print(json.dumps(rep))
        raise SystemExit(0 if all(r.get("ok") for r in rep.values()) else 1)
    srv = serve(a.host, a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
