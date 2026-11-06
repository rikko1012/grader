#!/usr/bin/env python3
"""
server.py - the web interface for cs2_scout.

    python server.py            then open http://localhost:8000

GET /                   the page (web/index.html); with Whop sign-in on and no valid session cookie: web/landing.html
                        (also /lol and /sports when released)
GET /api/search?q=      player suggestions from BO3.gg
GET /api/grade?player=&line=&prop=kills|hs&line_type=standard|goblin|demon[&slug=][&match=][&opponent=]
                        the full grade + supporting data

One grade runs at a time (keeps requests to BO3.gg polite). Standard library only.
"""
import collections
import copy
import gzip
import hashlib
import json
import math
import re
import secrets
import shutil
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import html as htmllib
from urllib.parse import parse_qs, quote, urlparse

import time
from datetime import datetime, timedelta, timezone

import cs2_scout
from pick_verdict import verdict, stake_cap_reasons, _CAP_WORDS, _CAP_RE, CAP_WORD_BOOKMAKER   # one verdict for the card, board and ladder
import hltv
import picklog
import platform_rules
import pplink
import slips
import slip_batches
import comparison
import prizepicks
import records
import roles
import underdog
import grade_display
import line_chance
import perf_shadow
import whop_auth
import bo3
from bo3 import BO3, DATA_DIR, prune, storage_report
import letter_containment
letter_containment.configure(DATA_DIR)              # lc-1: CS2 / LoL letter containment state (display only)
import esports_cal
esports_cal.configure(DATA_DIR)                     # ec-1: printed chance = calibrated chance (SCOUT_CAL_CHANCE, display layer)

from polymarket import match_odds, upcoming_events
import map_weighted
# CS2_MAP_WEIGHTED (default 0, MODEL_VERSIONS.md mw-1): off = nothing is wrapped and the model tag stays the frozen one;
# "1" = the live CS2 projection uses vm-2's map weighting (fallback to the current projection, reason logged) under its own tag
map_weighted.activate()

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
PORT = int(os.environ.get("PORT", 8000))
# on a host (Railway sets PORT) listen on all interfaces; locally only this computer
HOST = os.environ.get("HOST") or ("0.0.0.0" if "PORT" in os.environ else "127.0.0.1")
# pre-load upcoming matches in the background: on by default on the host, off on a PC
PRELOAD = os.environ.get("PRELOAD", "1" if "PORT" in os.environ else "0") == "1"
PRELOAD_HOURS = 36          # matches starting within this window
PRELOAD_EVERY = 30 * 60     # seconds between passes
REFRESH_AFTER = 6 * 3600    # re-load a player at most every 6 hours
# ---------------------------------------------------------------- load handling (700 at once)
# Grades run side by side (a single global lock once froze every member for ~60 s behind one
# cold look-up). Three layers:
# 1. identical requests share ONE computation, and its answer is reused for RESULT_TTL seconds
#    (a slate drop = hundreds of members grading the same props)
# 2. players already loaded run in the fast lane; players never seen get a few cold slots of
#    their own, so cold look-ups can never fill the fast lane
# 3. the same player is only assembled by one thread at a time (no duplicate BO3.gg fetches)
RESULT_TTL = 60
FAST_SLOTS = threading.BoundedSemaphore(6)    # CPU-bound (one Python core): more threads only thrash
COLD_SLOTS = threading.BoundedSemaphore(3)    # mostly waiting on BO3.gg (4 req/s shared)
WARM_FOR = 6 * 3600
_WARM = {}                                   # player key -> last successful grade time
WARM_FILE = DATA_DIR / ".cache" / "warm_players.json"   # survives restarts/deploys (volume)
_WARM_SAVED = [0.0]


def _load_warm():
    try:
        now = time.time()
        _WARM.update({k: v for k, v in json.loads(WARM_FILE.read_text("utf-8")).items() if now - v < WARM_FOR})
    except Exception:
        pass


def mark_warm(*keys):
    now = time.time()
    for k in keys:
        if k:
            _WARM[k.strip().lower()] = now
    if now - _WARM_SAVED[0] > 30:                # write at most every 30 s
        _WARM_SAVED[0] = now
        try:
            WARM_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = WARM_FILE.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps({k: v for k, v in list(_WARM.items()) if now - v < WARM_FOR}), "utf-8")
            os.replace(tmp, WARM_FILE)
        except Exception:
            pass


_load_warm()
WARM_START = {"done": 0, "total": 0, "finished": False}


def warm_start():
    """After a restart/deploy, memory is empty and each player's first grade re-reads ~500 files
    (4-6 s). Reload the most recently used players in the background, newest first, so members
    don't pay that. Uses background priority: members' own look-ups always go first."""
    keys = sorted(_WARM, key=_WARM.get, reverse=True)[:400]
    WARM_START["total"] = len(keys)
    seen = set()
    for k in keys:
        try:
            res = cs2_scout.grade(k, 20.5, faceit_key=None, background=True)
            slug = ((res.get("player_block") or {}).get("player") or {}).get("slug")
            if slug in seen:
                continue
            seen.add(slug)
        except Exception:
            pass
        WARM_START["done"] += 1
    WARM_START["finished"] = True


_RESULTS, _INFLIGHT, _RG = {}, {}, threading.Lock()
_SEARCH = {}                                  # query -> (time, body, n_results, rows)
_PLAYER_LOCKS, _PLAYER_LOCKS_GUARD = {}, threading.Lock()
RATE_LIMIT = int(os.environ.get("RATE_LIMIT", 60))   # grades per minute per IP (phone carriers share IPs)
_HITS, _HITS_GUARD = {}, threading.Lock()


_STORE = {"at": 0.0, "data": {}, "lock": threading.Lock()}


def storage_cached():
    """Disk usage for /health - scanning the cache folder is slow, so at most once a minute and
    never by two requests at once (400 parallel /health calls used to jam the server)."""
    if time.time() - _STORE["at"] > 60 and _STORE["lock"].acquire(blocking=False):
        try:
            _STORE["data"] = storage_report()
            _STORE["at"] = time.time()
        finally:
            _STORE["lock"].release()
    return _STORE["data"]


def rate_ok(ip):
    now = time.time()
    with _HITS_GUARD:
        if len(_HITS) > 50000:
            _HITS.clear()
        hits = [t for t in _HITS.get(ip, []) if now - t < 60]
        ok = len(hits) < RATE_LIMIT
        if ok:
            hits.append(now)
        _HITS[ip] = hits
        return ok


def player_lock(name):
    key = (name or "").strip().lower()
    with _PLAYER_LOCKS_GUARD:
        if len(_PLAYER_LOCKS) > 5000:
            _PLAYER_LOCKS.clear()
        return _PLAYER_LOCKS.setdefault(key, threading.Lock())


RESULTS_MAX = 20000
RESULTS_MAX_BYTES = int(float(os.environ.get("RESULTS_MEM_MB", "100")) * 1e6)   # 2026-10-18: bodies bounded by bytes too
_RES_BYTES = [0, 0.0]                                 # approx bytes of cached bodies, last re-count time
_SWEPT = [0.0]


def _store_result(key, status, body, ttl, keep):
    """(caller holds _RG) Keep one answer; expired answers leave on insert (2026-10-13 audit: they were evicted by count only,
    so up to 20,000 old grade bodies stayed in memory) - a sweep at most every 5 s, never more than RESULTS_MAX kept."""
    now = time.time()
    if now - _SWEPT[0] > 5:
        _SWEPT[0] = now
        for k in [k for k, v in _RESULTS.items() if len(v) > 3 and v[3] <= now]:
            del _RESULTS[k]
    size = lambda v: len(v[2]) if len(v) > 2 and isinstance(v[2], (bytes, str)) else 2000
    if now - _RES_BYTES[1] > 5:                       # re-count at most every 5 s (other code also removes answers)
        _RES_BYTES[0], _RES_BYTES[1] = sum(size(v) for v in _RESULTS.values()), now
    while _RESULTS and (len(_RESULTS) >= RESULTS_MAX or _RES_BYTES[0] > RESULTS_MAX_BYTES):
        _RES_BYTES[0] -= size(_RESULTS.pop(next(iter(_RESULTS))))   # the oldest go (recomputed if asked again)
    _RESULTS[key] = (now - max(0, ttl - keep), status, body, now + keep)
    _RES_BYTES[0] += size(_RESULTS[key])


FAIL_KEEP = 2.0         # a failed computation is handed to everyone asking within this many seconds - never longer


def _raise_or_return(failed):
    if isinstance(failed, BaseException):
        raise failed
    return failed


def shared(key, compute, ttl=RESULT_TTL):
    """Run compute() once per key: callers arriving while it runs wait for that answer, callers
    within `ttl` seconds after get it instantly. compute() returns (status, body_bytes).
    A failure (compute raised, or a status other than 200 / 404) goes to the callers that waited for it and to callers
    within FAIL_KEEP s - they don't each run it again (2026-10-13 audit: 8 waiters waited 1..8 s, and a member crowd
    multiplied the requests to a failing upstream) - and is never kept as a result beyond that."""
    now = time.time()
    with _RG:
        hit = _RESULTS.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], hit[2]
        fail = _RESULTS.get(("!fail", key))            # kept next to the answers (same store, same sweep, same limit)
        failed = fail[1] if fail and now < fail[3] else None
        if failed is None:
            ev = _INFLIGHT.get(key)
            leader = ev is None
            if leader:
                ev = _INFLIGHT[key] = threading.Event()
    if failed is not None:
        return _raise_or_return(failed)
    if not leader:
        if not ev.wait(900):
            return shared(key, compute, ttl)            # still running after 15 min: wait for it again
        failed = getattr(ev, "failed", None)            # the leader's failure, handed to everyone who waited for it
        if failed is not None:
            return _raise_or_return(failed)
        with _RG:
            hit = _RESULTS.get(key)
        if hit:
            return hit[1], hit[2]
        return shared(key, compute, ttl)
    failed = None
    try:
        out = compute()
        status, body = out[0], out[1]
        keep = out[2] if len(out) > 2 and out[2] else ttl    # a result may ask to be reused for less time (outage grades)
        if status in (200, 404):
            with _RG:
                _store_result(key, status, body, ttl, keep)
        else:
            failed = (status, body)
        return status, body
    except BaseException as e:
        failed = e
        raise
    finally:
        with _RG:
            _INFLIGHT.pop(key, None)
            if failed is not None:                      # bounds retries of a failing upstream to one per key per FAIL_KEEP s
                _store_result(("!fail", key), failed, None, FAIL_KEEP, FAIL_KEEP)
            else:
                _RESULTS.pop(("!fail", key), None)
        ev.failed = failed
        ev.set()


# ---- member responses: gzip + ETag made ONCE per shared body (2026-10-21 QoL item 5). The board, the LoL board, the
# moneylines and the pages went uncompressed with no-store to every member; now each distinct body is hashed and
# compressed once (700 members asking for the same body share that work), the compressed copy is kept in the result store
# (it counts toward RESULTS_MAX_BYTES and leaves with the same sweep), and a member whose browser already holds the body
# gets "304 Not Modified" with no body. Bodies are never changed - only how they travel.
GZIP_MIN = 1024                                       # smaller bodies go as they are
MEMBER_CACHE = "private, no-cache"                    # the browser keeps a private copy and must re-check it every time
_ENC_LOCKS, _ENC_GUARD = {}, threading.Lock()

# Content-Security-Policy behind SCOUT_CSP (unset / "0" = no header at all, "report" = Content-Security-Policy-Report-Only,
# "1" = enforced), read per response so a Render env change applies without a deploy (audit 2026-10-26 P2: CSP with explicit
# frame-ancestors for Whop). The pages carry inline <script> / <style> and style attributes, Google Fonts, ESPN images and,
# in SPORTS_API_MODE=service, the sports API origin, so the policy keeps those. Whop frames the member pages: they stay
# frameable through frame-ancestors (SCOUT_FRAME_ANCESTORS, a space / comma list; default below) - never an X-Frame-Options
# header. SCOUT_HSTS=1 adds Strict-Transport-Security (Render terminates TLS at its edge and does not add the header for
# the app). Bodies never change; flags off = the exact headers of before.
CSP_MODES = {"1": "Content-Security-Policy", "true": "Content-Security-Policy", "on": "Content-Security-Policy",
             "yes": "Content-Security-Policy", "report": "Content-Security-Policy-Report-Only"}
CSP_FRAME_ANCESTORS_DEFAULT = "'self' https://whop.com https://*.whop.com"
CSP_POLICY_DEFAULT = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                      "font-src 'self' data: https://fonts.gstatic.com; img-src 'self' data: https:; "
                      "object-src 'none'; base-uri 'self'; form-action 'self'")


def _csp_list(value, default=""):
    """A space / comma separated env value as one space-joined header token list (newlines can never reach a header)."""
    items = (value or "").replace(",", " ").split()
    return " ".join(items) if items else default


def csp_headers(env=None):
    """-> [(header, value), ...] the SCOUT_CSP / SCOUT_HSTS switches ask for; [] with both off."""
    env = os.environ if env is None else env
    out = []
    name = CSP_MODES.get((env.get("SCOUT_CSP") or "").strip().lower())
    if name:
        custom = _csp_list(env.get("SCOUT_CSP_POLICY"))      # the owner's own policy is used as written
        policy = custom or CSP_POLICY_DEFAULT
        if not custom:                                       # default policy: the browser talks to self + the sports API
            connect = ["'self'"]
            api = (env.get("SPORTS_API_BASE") or "").strip().rstrip("/")
            if api.startswith("https://"):
                connect.append(api)
            extra = _csp_list(env.get("SCOUT_CSP_CONNECT"))
            if extra:
                connect.append(extra)
            policy += "; connect-src " + " ".join(connect)
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


def encoded(key, body, keep=60):
    """(etag, gzip bytes or None) for `body` - computed once per body object under `key` (single flight: callers arriving
    while it is compressed wait for that copy). A different body under the same key (the next board build) replaces it."""
    ek = ("!enc", key)
    with _RG:
        hit = _RESULTS.get(ek)
        if hit and hit[1][0] is body:
            return hit[1][1], hit[2] or None
    with _ENC_GUARD:
        if len(_ENC_LOCKS) > 1000:                    # keys are a small fixed set; never grows without bound
            _ENC_LOCKS.clear()
        lk = _ENC_LOCKS.setdefault(key, threading.Lock())
    with lk:
        with _RG:
            hit = _RESULTS.get(ek)
            if hit and hit[1][0] is body:
                return hit[1][1], hit[2] or None
        etag = '"' + hashlib.blake2b(body, digest_size=12).hexdigest() + '"'
        gz = gzip.compress(body, 6, mtime=0) if len(body) >= GZIP_MIN else b""
        with _RG:
            _store_result(ek, (body, etag), gz, keep, keep)
    return etag, gz or None


def accepts_gzip(header):
    """True when Accept-Encoding allows gzip (q=0 refuses it)."""
    for part in (header or "").lower().split(","):
        name, _, params = part.strip().partition(";")
        if name.strip() in ("gzip", "*"):
            q = re.search(r"q\s*=\s*([0-9.]+)", params)
            try:
                return not q or float(q.group(1)) > 0
            except ValueError:
                return False
    return False


def etag_matches(header, etag):
    """If-None-Match holds this body's tag (plain or gzip copy; a proxy may have weakened it to W/"...")."""
    if not header:
        return False
    for t in header.split(","):
        t = t.strip()
        if t == "*":
            return True
        if t.startswith("W/"):
            t = t[2:]
        if t.endswith('-gz"'):
            t = t[:-4] + '"'
        if t == etag:
            return True
    return False


PLAUSIBLE_MAX = {"kills": {"1": 50, "12": 90, "123": 130}, "hs": {"1": 35, "12": 60, "123": 90}}   # line sanity (see /api/grade)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")   # set on Railway; no token = admin page disabled

PRELOAD_STATUS = {"enabled": PRELOAD, "players_ready": 0, "last_pass": None, "current": None, "errors": 0}


STORAGE = {"last_cleanup": None, "freed_mb": 0.0}


# ---------------------------------------------------------------- Underdog board (graded in the background)
BOARD = {}              # Underdog line id -> board row + its grade summary
BOARD_WORKERS = 3       # lines graded side by side (one at a time left most of the board ungraded for hours)
BOARD_EVERY = 15 * 60   # re-grade a line at most this often (members' grades are cached anyway)
BOARD_TTL = 5           # /api/board: one answer per platform shared by every member for 5 s
HLTV_RETRY = 90         # a line graded while the HLTV database was unreachable is tried again this soon

FEED_GRADE_MAX_S = 2 * 3600   # audit 2026-11-05 B1: a platform whose last good read is older than this is not graded as current (its
                              # lines keep their last grade; the workspace hides them as stale); a feed blip shorter than this changes nothing


def feed_age_s(fb, now=None):
    """Seconds since this platform feed's last good read ({"at": epoch or ISO}), or None when it has never been read."""
    at = (fb or {}).get("at")
    if isinstance(at, str):
        try:
            t = datetime.fromisoformat(at.replace("Z", "+00:00"))
            at = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()
        except ValueError:
            return None
    if not isinstance(at, (int, float)) or at <= 0:
        return None
    return max(0.0, (time.time() if now is None else now) - float(at))


def feed_current(fb, now=None):
    """True while this feed's last good read is recent enough to grade its lines as current."""
    a = feed_age_s(fb, now)
    return a is not None and a <= FEED_GRADE_MAX_S


def _hltv_status(public=False):
    try:
        import hltv
        if public:
            return hltv.public_status()
        out = hltv.status()
        try:      # 2026-10-13: the performance model's pinned input revision (N0), its counts and last load error
            import perf_live
            out = dict(out, perf_input=perf_live.input_status())
        except Exception as e:  # noqa: BLE001
            out = dict(out, perf_input={"error": type(e).__name__})
        return out
    except Exception as e:
        return {"error": str(e)[:80]}


def hltv_required(env=None):
    """The member deployment (REQUIRE_WHOP set, like allowed()): there the HLTV database is part of the service - a missing
    DSN is an outage, never a reason to grade the whole board on the old pipeline. Locally (no REQUIRE_WHOP) it is not."""
    return bool((os.environ if env is None else env).get("REQUIRE_WHOP"))


def perf_transient(g):
    """True when this grade fell back to the old pipeline only because the HLTV database was unreachable (cs2_scout._perf_input
    marks that in the grade's projection_source; grader output itself is unchanged). 2026-10-13 audit: on the member
    deployment a missing database ('HLTV database not configured') counts too - it silently regraded the board on the old
    pipeline and logged it as live."""
    ps = str((g or {}).get("projection_source") or "")
    return "HLTV database unreachable" in ps or ("HLTV database not configured" in ps and hltv_required())


def odds_transient(g):
    """True when Thunderpick HAS an event for this match but no current quote (stale / cooldown / reader error) and the grade
    used the fallback odds (market_odds marks the selection transient; the grade carries g['odds_transient']). Owner
    2026-10-13: an odds blip is treated like an HLTV blip - the board keeps the line's last grade, nothing is logged."""
    return bool((g or {}).get("odds_transient"))


def grade_transient(g):
    """A grade the board never stores and the pick log never records as a live forecast: HLTV or odds blip."""
    return perf_transient(g) or odds_transient(g)


def _transient_causes(g):
    return [c for c, on in (("hltv", perf_transient(g)), ("odds", odds_transient(g))) if on]


HLTV_MAX_RETRIES = 20   # a line kept this often (~30 min) while its model stays 'unreachable' drops to a hidden placeholder on the
                        # regular cycle (a slow query, not an outage) - an old-pipeline grade still never goes on the board for it


def outage_placeholder(old, r, now=None, g=None):
    """A line whose grade could not use the HLTV database (or Thunderpick's quote - g carries the cause) and that has no
    earlier grade of this line to keep: not shown (no side), tried again in HLTV_RETRY s; after HLTV_MAX_RETRIES it stays
    hidden but on the regular BOARD_EVERY cycle (a slow query, not an outage - still never an old-pipeline grade on the board)."""
    old = old or {}
    n = (old.get("hltv_retries") or 0) + 1
    now = now or time.time()
    odds = g is not None and odds_transient(g) and not perf_transient(g)
    why = ("Thunderpick lists this match but has no current quote" + (f" ({_member_text(g.get('odds_transient_reason'))})"
                                                                      if g.get("odds_transient_reason") else "") if odds
           else "HLTV database unreachable")
    return {"at": now - BOARD_EVERY + HLTV_RETRY if n <= HLTV_MAX_RETRIES else now, "state": "RETRY", "reason": why + " - graded again shortly",
            "graded_line": r.get("line"), "hltv_retries": n, "hltv_retry": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "retry_cause": "odds" if odds else "hltv"}


def side_opened(g, r):
    """The feed now offers the Under this board grade was made without (audit 55): the grade (Lean 0U 'only Over is
    offered') is for another set of sides than the card's, so it is regraded at once and never shown or kept meanwhile.
    Only the Under is a grading input (less_available); a side that was withdrawn is labelled by side_unavailable."""
    gs = (g or {}).get("graded_sides") or {}
    return "under" in gs and gs["under"] is not True and (r or {}).get("under_available") is True


def keep_last_hltv_grade(old, g, r=None, now=None):
    """The HLTV database was unreachable for this grade (g['perf_transient']) - or Thunderpick had no current quote
    (odds_transient): the board keeps the last grade of the line instead of showing an old-pipeline / fallback-odds grade
    next to the other grades of the same player (2026-10-11 saadzin: Under 30.5 on the Underdog board, Over 29.5 on
    PrizePicks and in Match players, ten minutes apart). Returns the row to keep (due again in HLTV_RETRY seconds) or None
    when the new grade should be stored."""
    if not grade_transient(g) or not old or not old.get("grade"):
        return None
    if r is not None and old.get("graded_line") is not None and float(old["graded_line"]) != float(r.get("line") or -1):
        return None                                   # the line moved: the old grade is for another line
    if r is not None and side_opened(old, r):
        return None                                   # graded without the Under the feed now offers: another grade
    if (old.get("hltv_retries") or 0) >= HLTV_MAX_RETRIES:
        return None
    now = now or time.time()
    return dict(old, at=now - BOARD_EVERY + HLTV_RETRY, hltv_retry=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                hltv_retries=(old.get("hltv_retries") or 0) + 1,
                retry_cause="odds" if odds_transient(g) and not perf_transient(g) else "hltv")
BOARD_FILE = DATA_DIR / ".cache" / "board.json"


def _grader_version():
    """Changes whenever the grading formula (grader.py) changes: saved board grades from an older
    formula are dropped on startup instead of being shown."""
    import hashlib
    # 2026-10-10: the full model version (every file that moves a grade, picklog.MODEL_VERSION) - the old hash covered
    # grader.py + perf.py only, so a cs2_scout.py / perf_live.py fix kept the old grades until each line was 15 min old
    return picklog.MODEL_VERSION


_BOARD_SAVE = threading.Lock()


def _board_save():
    with _BOARD_SAVE:
        _board_save_locked()


def _board_save_locked():
    try:
        tmp = BOARD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": _grader_version(), "rows": BOARD}, default=str), encoding="utf-8")
        os.replace(tmp, BOARD_FILE)
    except Exception:
        pass


def _board_load():
    try:
        saved = json.loads(BOARD_FILE.read_text(encoding="utf-8"))
        rows = saved.get("rows") or {}
        now = datetime.now(timezone.utc)
        for k in [k for k, v in rows.items() if _started(v.get("match_start"), now)]:
            del rows[k]                               # its match started while the server was down: never shown again
        for v in rows.values():
            # grading changed: keep showing the last grades (never an empty board) but regrade every line made by
            # another model version first thing - "at" 0 puts them at the front of the queue
            if saved.get("version") != _grader_version() or v.get("graded_model") != picklog.MODEL_VERSION:
                v["at"] = 0
                if str(v.get("source") or "").startswith("older model"):
                    # an old-pipeline grade from another version (an outage artefact before 2026-10-11) is not shown again:
                    # it waits hidden and is regraded first
                    v.pop("side", None)
                    v["state"] = "RETRY"
        BOARD.update(rows)
    except Exception:
        pass


def _started(start, now=None):
    t = comparison._ts(start)
    return bool(t and t.tzinfo and t <= (now or datetime.now(timezone.utc)))


def _feed_known(fb, book):
    """This pass's read of one platform's feed is real data (rows, or an answered empty board) - not a failed / not yet
    loaded read, which must never drop the board's kept grades."""
    if fb.get("rows"):
        return True
    if book == "prizepicks":
        return fb.get("state") in ("ok", "empty")
    return not fb.get("error") and bool(fb.get("at"))


def prune_board(feeds, now=None):
    """2026-10-13 audit: BOARD was never pruned (only HLTV-retry rows were) and past 5,000 entries the whole board - current
    lines and kept HLTV grades included - was cleared, so the Prop Board showed 0 lines. Now each pass drops exactly the
    entries whose line left its platform's feed or whose match started; a platform whose read failed keeps its entries.
    feeds = {book: that pass's underdog.board() / prizepicks.board()}. Returns how many were dropped."""
    now_ = now or datetime.now(timezone.utc)
    live, known = set(), set()
    for book, fb in feeds.items():
        if _feed_known(fb or {}, book):
            known.add(book)
        # each platform's own listed start locks its own line, compared as times (2026-10-18: as text, a start written with
        # another UTC offset compared wrong - the 2026-10-13 audit fixed the same in _before_start)
        live |= {r["id"] for r in (fb or {}).get("rows") or [] if not _started(r.get("start"), now_)}
    drop = [k for k in list(BOARD) if k not in live and ("prizepicks" if str(k).startswith("pp:") else "underdog") in known]
    for k in drop:
        BOARD.pop(k, None)
    return len(drop)


def _board_l10(rf, r):
    """Last-10 numbers in the line's own maps: Map 1 lines use his map-1 values, Maps 1-3 his maps 1-3 totals of series
    where map 3 was played (records.py), Maps 1-2 as before."""
    sc = r.get("scope") or "12"
    if (rf.get("matches") or 0) < 3:                 # 1-2 matches is not a record (one OT map read as "avg 39")
        return {"l10_over": None, "l10_n": None, "l10_avg": None}
    if sc in ("12", "123"):
        return {"l10_over": rf.get("overs"), "l10_n": rf.get("decided", rf.get("matches")), "l10_push": rf.get("pushes") or 0,
                "l10_avg": rf.get("avg_total")}
    if sc == "1":                                    # recent_form_map1: one value per match (BO1s included)
        v = [s["values"][0] for s in (rf.get("series") or []) if s.get("values") and s["values"][0] is not None]
        return {"l10_over": sum(x > r["line"] for x in v) if v else None, "l10_n": sum(x != r["line"] for x in v) or None,
                "l10_push": sum(x == r["line"] for x in v), "l10_avg": round(sum(v) / len(v), 1) if v else None}
    return {"l10_over": None, "l10_n": None, "l10_avg": None}


def ud_id_map(rows=None):
    """Underdog player id -> our BO3.gg player id, learnt from board grades (the board grade resolved that exact
    Underdog line to one player). A Underdog id seen with two different players is dropped (ambiguous)."""
    out, bad = {}, set()
    for r in rows if rows is not None else underdog.board()["rows"]:
        g = BOARD.get(r.get("id")) or {}
        u, p = r.get("ud_player_id"), g.get("player_id")
        if u is None or p is None or u in bad:
            continue
        if out.get(u, p) != p:
            bad.add(u); out.pop(u, None); continue
        out[u] = p
    return out


def _slug_mid(slug):
    """The canonical id an HLTV-fallback match slug ("hltv-2399657") names ("hltv:2399657"); None for any other slug."""
    m = re.fullmatch(r"hltv-(\d+)", str(slug or ""))
    return f"hltv:{m.group(1)}" if m else None


def _settle_slug(nm):
    """The match key the pick log settles under (same rule as picklog.pick_row): BO3.gg's slug, else hltv-<HLTV match id>."""
    return nm.get("slug") or (f"hltv-{nm['hltv_match']}" if nm.get("hltv_match") else None)


def same_match(r, slug, st, mid=None):
    """A board row is for the graded match: its match id decides when both sides know one (2026-10-15 owner - a platform's
    listed start can differ from the graded match's by hours: Underdog listed WBT vs ENCE at 14:00, BO3.gg 12:00; a
    rematch has another id, so it never matches); otherwise the listed start within the join window.
    2026-10-18 audit: one match can carry two slugs - the HLTV fallback's "hltv-2399657" until BO3.gg lists it, then BO3.gg's
    "gamerlegion-vs-ex-rustec-16-10-2026" (GamerLegion vs ex-RUSTEC, Oct 16: lines graded on each side of the switch were
    different matches here, so a card missed its board row's same-match stake). Two different slugs are the same match only
    when both resolve to the SAME canonical HLTV match id (the fallback slug names it; a board row carries matchid's id;
    mid = the card's) - never by names or a time window."""
    if slug and r.get("match_slug"):
        if r["match_slug"] == slug:
            return True
        a, b = _slug_mid(slug) or mid, _slug_mid(r["match_slug"]) or r.get("match_id")
        return bool(a and b and str(a).startswith("hltv:") and a == b)
    rs = comparison._ts(r.get("start"))
    return bool(st and rs and abs((rs - st).total_seconds()) <= comparison.JOIN_START_MIN * 60)


def match_compare(team_slug, opp_slug, team, opponent, start, scope="12", book="underdog", match_slug=None):
    """Match player projection comparison (2026-10-09): every player of both teams in this match - OUR projection for
    kills and headshots, the graded Underdog line for the same match and maps when one exists (side, chance, grade,
    stake, freshness, data status), otherwise the projection alone and 'no available line' (no pick invented).
    Team totals only when the whole roster has a projection; never any probability arithmetic across players."""
    from datetime import datetime as _dt
    import perf_live
    st = comparison._ts(start)
    # one platform's graded lines (2026-10-10: PrizePicks like Underdog); the table shows each player's STANDARD line -
    # PrizePicks goblin / demon markets stay on the board, never mixed into this table. A promo line is a standard Over /
    # Under line graded as standard (2026-10-13 audit: it was left out here and re-added from the feed as 'grading...')
    rows = [r for r in letter_containment.cs2_rows(board_payload(book)["rows"])          # lc-1: held letters read Lean, 0U
            if (r.get("line_type") or r.get("odds_type") or "standard") in ("standard", "promo")]
    same = [r for r in rows if (r.get("scope") or "12") == scope and same_match(r, match_slug, st)
            and ({comparison._team_same(r.get("team_name") or r.get("team"), team), comparison._team_same(r.get("team_name") or r.get("team"), opponent)} & {True})]
    # the platform's own listed start(s) of this match (from its graded rows): feed lines carry no match id
    starts = [x for x in {st, *(comparison._ts(r.get("start")) for r in same)} if x]
    b = BO3()
    benched = {}                                    # team -> benched nicks (lower): never in a lineup or a team total
    try:
        rosters = {}
        for slug, name in ((team_slug, team), (opp_slug, opponent)):
            try:
                td = b.team(slug) if slug else {}
                # active players only: BO3.gg status 2 = benched (not in the lineup)
                rosters[name] = [x.get("nickname") for x in (td.get("players") or [])
                                 if not x.get("is_coach") and x.get("nickname") and x.get("status") != 2]
                benched[name] = {x["nickname"].lower() for x in (td.get("players") or [])
                                 if not x.get("is_coach") and x.get("nickname") and x.get("status") == 2}
            except Exception:
                rosters[name] = []
    finally:
        b.close()
    def side_of(r):
        if comparison._team_same(r.get("team_name") or r.get("team"), team):
            return team
        return opponent
    players = {}                                    # (team, nick lower) -> entry
    for name, nicks in rosters.items():
        for n in nicks:
            players[(name, n.lower())] = {"nick": n, "team": name, "kills": None, "hs": None}
    for r in same:
        nk = (r.get("nick") or r["player"])
        # a benched player with a book line (2026-10-13 audit: he was put back into the lineup and the team total, labelled
        # 'not on roster page'): his line is shown, labelled benched, and never counted in a lineup or a total
        bn = nk.lower() in benched.get(side_of(r), ())
        e = players.setdefault((side_of(r), nk.lower()), dict({"nick": nk, "team": side_of(r), "kills": None, "hs": None},
                                                              **({"benched": True} if bn else {"not_on_roster_page": True})))
        if r.get("odds_type") == "promo" and (e.get(r["prop"]) or {}).get("line") is not None:
            continue                                # the player's standard line is the table's line; a promo line never replaces it
        e[r["prop"]] = {"proj": r.get("proj"), "line": r["line"], "side": r.get("side"), "p": r.get("p"), "grade": r.get("grade"),
                        "units": r.get("units"), "units_full": r.get("units_full"), "graded_at": r.get("graded_at"), "dstat": r.get("dstat"),
                        "source": r.get("source"), "state": r.get("state"), "ud_team": r.get("team"), "ud_opponent": r.get("opponent"),
                        "reason": r.get("reason"), "kept": bool(r.get("hltv_retry")), "retry_cause": r.get("retry_cause"),
                        "unavailable": r.get("unavailable"), "model_grade": r.get("model_grade"),   # display gd-1
                        "stake_reason": r.get("stake_reason"), "available": r.get("available"),
                        # night/consistency 2026-10-23: the row's push mass (the chance's denominator is shown the same way
                        # as on the board / card) and its identity (model version, match id, row id, cap words)
                        "model_outcomes": r.get("model_outcomes"), "graded_model": r.get("graded_model"),
                        "match_id": r.get("match_id"), "row_id": r.get("id"), "cap_n": r.get("cap_n"),
                        "cap_reason": r.get("cap_reason"), "player_id": r.get("player_id")}
    # offered but not graded yet (2026-10-11 audit: damyo's 31.5 showed 'No available line' while it was grading)
    try:                                            # the feed as already read (never a blocking first read here)
        feed = (prizepicks.board()["rows"] if book == "prizepicks" else
                underdog.board()["rows"] if (underdog._BOARD.get("at") or underdog._BOARD.get("tried")) else [])
    except Exception:
        feed = []
    for r in feed:
        if (r.get("line_type") or r.get("odds_type") or "standard") not in ("standard", "promo") or (r.get("scope") or "12") != scope:
            continue
        fs = comparison._ts(r.get("start"))
        if not (fs and any(abs((fs - x).total_seconds()) <= comparison.JOIN_START_MIN * 60 for x in starts)):
            continue
        if not ({comparison._team_same(r.get("team"), team), comparison._team_same(r.get("team"), opponent)} & {True}):
            continue
        tm_ = team if comparison._team_same(r.get("team"), team) else opponent
        e = players.get((tm_, str(r.get("player") or "").lower()))
        if e is not None and e.get(r.get("prop")) is None:
            e[r["prop"]] = {"proj": None, "line": r.get("line"), "pending": True}
    # graded on the other platform (a BO3.gg-history projection the board made): its projection, not 'no projection'
    others = {}
    try:                                            # both platforms' graded rows (this answer is shared per match for 120 s)
        both = _both_boards()
    except Exception:
        both = _BS_CACHE[1] or []
    for r in both:
        if (r.get("scope") or "12") == scope and r.get("proj") is not None and same_match(r, match_slug, st):
            others.setdefault((str(r.get("nick") or r.get("player") or "").lower(), r.get("prop")), r)
    # same day and LAN / online setting as the graded rows (2026-10-10 audit: these used today's date and no LAN /
    # online blend, so a no-line projection was not comparable with the graded ones next to it)
    import cs2_scout
    day = cs2_scout.match_day(start)
    tn = next((r.get("tonight") for r in same if r.get("tonight")), None)
    lan = None if tn is None else tn == "lan"
    for (tname, _), e in players.items():          # projections for props without a graded line
        for prop in ("kills", "hs"):
            if e[prop] is None or e[prop].get("pending"):
                pend = (e[prop] or {}).get("line") if e[prop] else None
                o = others.get((e["nick"].lower(), prop))
                if o is not None and o.get("source") and o.get("source") != "HLTV":
                    pr = {"lambda": o["proj"], "source": o["source"]}            # the board's own (BO3.gg) projection
                else:
                    try:
                        pr = perf_live.projection(e["nick"], tname, opponent if tname == team else team, lan, scope, prop, day)
                    except Exception as ex:
                        pr = {"error": "HLTV database unreachable right now" if cs2_scout._transient_hltv_error(ex) else "failed"}
                    if pr and not pr.get("error") and pr.get("player_maps") == 0:
                        pr = {"error": "no HLTV maps in the last 420 days"}       # a pro-average number is no projection
                if pr and not pr.get("error") and pr.get("lambda") is not None:
                    e[prop] = {"proj": round(pr["lambda"], 2), "line": pend, "no_line": pend is None, "pending": pend is not None,
                               "source": pr.get("source") or "HLTV", "dq": pr.get("data_quality")}
                else:
                    e[prop] = {"proj": None, "line": pend, "no_line": pend is None, "pending": pend is not None,
                               "note": "HLTV database unreachable right now" if "unreachable" in str((pr or {}).get("error"))
                               else "no HLTV history"}
    out = []
    for tname in (team, opponent):
        shown = [e for (t, _), e in players.items() if t == tname]
        ps = [e for e in shown if not e.get("benched")]      # lineups and totals: active players only
        # team totals need the 5 who actually play: Underdog's lineup when it lists exactly 5, else a roster page of
        # exactly 5; otherwise no total (a 6-man roster page would inflate it)
        lined = [e for e in ps if any((e[p] or {}).get("line") is not None for p in ("kills", "hs"))]
        five = lined if len(lined) == 5 else ps if len(ps) == 5 else None
        basis = (("PrizePicks" if book == "prizepicks" else "Underdog") + " lineup" if five is lined and len(lined) == 5
                 else "roster page" if five else None)
        covered = sum(1 for e in (five or ps) if all((e[p] or {}).get("proj") is not None for p in ("kills", "hs")))
        full = bool(five) and covered == 5
        summ = {prop: (round(sum((e[prop] or {}).get("proj") or 0 for e in five), 1) if full else None) for prop in ("kills", "hs")}
        out.append({"team": tname, "players": shown, "summary": dict(summ, complete=full, covered=covered, roster=len(ps), basis=basis,
                                                                     lineup=[e["nick"] for e in five] if five else None),
                    "exposure": team_exposure(shown)})
    return {"match": {"team": team, "opponent": opponent, "start": start, "scope": scope}, "teams": out,
            "at": _dt.now(timezone.utc).isoformat(timespec="seconds")}


def team_exposure(players):
    """Shared exposure of one team's graded lines in a match (both props, every side): only picks with a stake count -
    a 0-unit Lean is not exposure (2026-10-11: 'same player both props' listed Leans). Leans are counted apart."""
    picks, leans, both = [], 0, []
    for e in players:
        staked = {}
        for prop in ("kills", "hs"):
            c = e.get(prop) or {}
            if c.get("line") is None or not c.get("side"):
                continue
            if (c.get("units") or 0) > 0:
                picks.append(c)
                staked[prop] = "Over" if c["side"] == "More" else "Under"
            else:
                leans += 1
        if len(staked) == 2:
            both.append({"nick": e.get("nick"), "kills": staked["kills"], "hs": staked["hs"]})
    over = sum(1 for c in picks if c["side"] == "More")
    return {"picks": len(picks), "over": over, "under": len(picks) - over,
            "units": round(sum(c.get("units") or 0 for c in picks), 2), "leans": leans, "both_props": both}


def _card_match_id(res):
    """The canonical match id of a card's graded match (the same lookup as a board row's), or None (identity only)."""
    pb, nm = res.get("player_block") or {}, res.get("next_match") or {}
    if _slug_mid(nm.get("slug")):
        return _slug_mid(nm.get("slug"))
    return _row_match_id({"team_name": pb.get("playing_for") or pb.get("current_team"), "opp_name": nm.get("opponent"),
                          "start": nm.get("start"), "bo": nm.get("bo"), "event": nm.get("event")})


def card_identity(res, v, prop=None, scope=None, line=None, book=None):
    """The card verdict with the identity a board row carries (night/consistency 2026-10-23: the card sent no model version
    or match id, so a card could not be shown to be the same grade as its board row / slip leg): graded_model = the
    version that made THIS grade (a live card: the running model; an outage card keeps the board row's own); match_id =
    the board row's own id when a board lists exactly this offer (same platform, player, stat, maps, line and match -
    same_match), else the canonical id of the graded match (a platform's listed start can be hours from BO3.gg's, and a
    match with no HLTV id hashes its start, so the card's own lookup could name another id than the row's). A new dict;
    identity only, never a grade input. No network: the board snapshot is the 5 s single-flight copy."""
    if not isinstance(v, dict):
        return v
    out = dict(v)
    if not out.get("kept_from_board"):
        out["graded_model"] = picklog.MODEL_VERSION if out.get("side") else None
    mid, hit = None, None
    try:
        mid = _card_match_id(res)
    except Exception:
        pass
    try:
        if prop and line is not None:
            nick = (((res.get("player_block") or {}).get("player") or {}).get("nick") or "").lower()
            nm = res.get("next_match") or {}
            st, slug = comparison._ts(nm.get("start")), nm.get("slug")
            books = (book,) if isinstance(book, str) else tuple(book or ("underdog", "prizepicks"))
            rows = _bs_rows() if nick and (st or slug) else []
            for bk in books:
                hit = next((r for r in rows if (r.get("book") or "underdog") == bk and r.get("match_id")
                            and str(r.get("nick") or r.get("player") or "").lower() == nick and r.get("prop") == prop
                            and (r.get("scope") or "12") == (scope or "12") and float(r.get("line") or -1) == float(line)
                            and same_match(r, slug, st, mid)), None)
                if hit:
                    mid = hit["match_id"]
                    break
    except Exception:
        hit = None
    out["match_id"] = mid
    try:                                    # the slip lock rule (slip_builder.lock_time): the earlier of the exact board
        from slip_builder import lock_iso  # row's platform start (when a board lists this offer) and the graded match's
        out["lock_at"] = lock_iso({"start": (hit or {}).get("start"), "match_start": (res.get("next_match") or {}).get("start")})
    except Exception:
        out["lock_at"] = None
    return out


def card_veto(res):
    """The card's map-veto panel (veto_serve: veto-1, display only - never a grade input): the answer the background thread
    stored for this match, or None (the match is then queued for the background; nothing is computed here)."""
    try:
        import veto_serve
        return veto_serve.for_card(res)
    except Exception:
        return None


def card_map_chances(res):
    """The card's "HLTV map chances" block (mapchances: display only, weight 0 - never a grade input): hltv_mapchances'
    cached answer for the match (one query per match per 3 minutes, single flight, shared by every member; the card waits at most
    0.5 s for a refresh). None = no block. grade_use (map_weighted): "HLTV map chances used" + capture time when the flag is on
    and they fed this grade, otherwise "Shadow only - not in this grade" with vm-2's projection beside the live one."""
    try:
        import mapchances
        block = mapchances.for_card(res)
    except Exception:
        return None
    try:
        use = map_weighted.card_status(res)
        if use and use.get("used") and block is None:
            block = map_weighted.block_from_record((res.get("grader_inputs") or {}).get("map_weighted"))
        if block is not None and use:
            block = dict(block, grade_use=use)
            if use.get("used"):
                block.update(weight=1, note=None)
    except Exception:
        pass
    return block


def veto_body(match_id):
    """/api/veto: the stored panel of one canonical match id (memory only), shared by every member for VETO_TTL s."""
    try:
        import veto_serve
        return json.dumps({"match_id": match_id, "veto": veto_serve.for_match(match_id)}, default=str).encode()
    except Exception:
        return json.dumps({"match_id": match_id, "veto": None}).encode()


VETO_TTL = 30
_RQ = {"t": 0.0, "n": 0, "busy": False}
BUSY_RPS = 20              # more requests a second than this = members are being served: background veto work waits


def members_busy():
    """True while the server answers more than BUSY_RPS requests a second (sampled once a second; no lock, no I/O): the
    veto background (veto_serve.BUSY) steps aside so a member crowd never queues behind its CPU work."""
    now = time.time()
    if now - _RQ["t"] >= 1.0:
        n = STATS["requests"]
        _RQ["busy"] = _RQ["t"] > 0 and (n - _RQ["n"]) / (now - _RQ["t"]) > BUSY_RPS
        _RQ.update(t=now, n=n)
    return _RQ["busy"]


def _row_match_id(row):
    """matchid.match_id_for_row, or None while matchid's tables are still loading (a member request never waits for the
    first HLTV read - the boot thread does it) or if linking fails. Identity only: no grade reads it."""
    try:
        import matchid
        return matchid.match_id_for_row(row) if matchid.ready() else None
    except Exception:
        return None


def _iso_any(t):
    if t in (None, "", 0):
        return None
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    return str(t)


def feed_meta(book, b=None):
    """When OUR server read the platform's feed into its cache (observed_at) - distinct from the provider's own times -
    and the feed's state. b: a board() answer already in hand (the board pass's own snapshot)."""
    try:
        b = b if b is not None else (prizepicks.board() if book == "prizepicks" else underdog.board())
    except Exception:
        return None
    if book == "prizepicks":
        return {"book": book, "observed_at": _iso_any(b.get("ingested_at")), "provider_read_at": _iso_any(b.get("at")),
                "feed_state": b.get("state")}
    return {"book": book, "observed_at": _iso_any(b.get("at")), "provider_read_at": None,
            "feed_state": "failed" if b.get("error") else ("ok" if b.get("at") else "loading")}


def quote_record(book, r, meta):
    """One platform's offer exactly as read: ids, line, market type, the sides offered, prices / payouts, the provider's
    timestamps and when our cache read it (pl-2 anchor inputs, 2026-10-15)."""
    if book == "prizepicks":
        mt = r.get("odds_type") or "standard"
        over, under = r.get("over_available"), r.get("under_available")
    else:
        mt = "alt" if r.get("alt") else "standard"
        over = None if r.get("higher") is None else True
        under = None if r.get("lower") is None else True
    return {"book": book, "source_id": r.get("id"), "market_id": r.get("market_id"), "line": r.get("line"), "market_type": mt,
            "over_offered": over, "under_offered": under, "price_over": r.get("price_higher"), "price_under": r.get("price_lower"),
            "payout_over": r.get("higher"), "payout_under": r.get("lower"), "source_updated_at": r.get("updated_at"),
            "source_seen_at": r.get("seen_at"), **{k: (meta or {}).get(k) for k in ("observed_at", "provider_read_at", "feed_state")}}


def other_anchor(book, player_id, slug, st, prop, scope):
    """The OTHER platform's offer for exactly this player (BO3.gg id from its own board grade) x canonical match x stat x
    maps, from its feed cache at this moment. -> (quote record | None, reason | None). Unknown identity = no anchor."""
    ob = "underdog" if book == "prizepicks" else "prizepicks"
    try:
        b = prizepicks.board() if ob == "prizepicks" else underdog.board()
    except Exception:
        return None, "other platform feed unreadable"
    meta = feed_meta(ob, b)
    if not player_id:
        return None, "player id unknown"
    cand = []
    for x in b.get("rows") or []:
        if x.get("prop") != prop or (x.get("scope") or "12") != scope:
            continue
        gb = BOARD.get(x.get("id")) or {}
        if gb.get("player_id") != player_id or not same_match({"match_slug": gb.get("match_slug"), "start": x.get("start")}, slug, st):
            continue
        cand.append(x)
    if not cand:
        return None, "no identified line of the other platform for this player / match / stat / maps in its current read"
    if ob == "underdog":
        std = [x for x in cand if not x.get("alt")]
    else:
        std = [x for x in cand if (x.get("odds_type") or "standard") in ("standard", "promo")]
    if not std:
        return None, "the other platform offers only adjusted / alternate lines here"
    plain = [x for x in std if (x.get("odds_type") or "standard") == "standard"]
    pick = plain if plain else std
    if len(pick) > 1:
        return None, f"ambiguous: {len(pick)} standard lines on the other platform"
    return dict(quote_record(ob, pick[0], meta), matched_on="player_id + canonical match + stat + maps"), None


def board_at_iso(book):
    """The fetch time of a platform's board as ISO UTC (None when unknown) - recorded with each board forecast's quote."""
    try:
        b = prizepicks.board() if book == "prizepicks" else underdog.board()
        at = b.get("at")
        return datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds") if isinstance(at, (int, float)) and at else (at or None)
    except Exception:
        return None

_OPS = {"at": 0.0, "out": None}
OPS_TTL = 60


def _age_s(t, now):
    ts = comparison._ts(t) if not isinstance(t, (int, float)) else (datetime.fromtimestamp(t, timezone.utc) if t else None)
    return round((now - ts).total_seconds()) if ts else None


def _check(state, **kw):
    return dict(kw, state=state)


MEM_WARMUP_MIN = 20


def ops_checks(now=None):
    """Operational checks for /health (2026-10-18 release brief, section 5) - each reads the real input state, never a
    provider request: platform feed ages and failures, odds sources' upstream ages (a cache read never counts as a new
    quote), the HLTV database, the grading backlog, container memory and its growth, and settlement lag. Cached OPS_TTL s.
    state: ok / warn / fail (the thresholds are listed with each value)."""
    t = time.time()
    if now is None and _OPS["out"] is not None and t - _OPS["at"] < OPS_TTL:
        return _OPS["out"]
    now = now or datetime.now(timezone.utc)
    out = {}
    try:
        ub = underdog._BOARD
        a = _age_s(ub.get("at"), now) if ub.get("at") else None
        out["feed_underdog"] = _check("fail" if a is None or a > 1800 else "warn" if a > 600 or ub.get("error") else "ok",
                                      age_s=a, warn_s=600, fail_s=1800, error=member_source_note(ub.get("error")), lines=len(ub.get("rows") or []))
    except Exception as e:
        out["feed_underdog"] = _check("fail", error=type(e).__name__)
    try:
        pb = prizepicks.board()
        a = _age_s(pb.get("at"), now) if pb.get("at") else None
        out["feed_prizepicks"] = _check("fail" if a is None or a > 1800 else "warn" if a > 600 or pb.get("state") not in ("ok", "empty") else "ok",
                                        age_s=a, warn_s=600, fail_s=1800, feed_state=pb.get("state"), lines=len(pb.get("rows") or []))
    except Exception as e:
        out["feed_prizepicks"] = _check("fail", error=type(e).__name__)
    try:                                   # upstream retrieval time of the quotes being served (not our cache read time)
        import thunderpick
        ps = thunderpick.public_state() or {}
        a = _age_s(ps.get("retrieved_at"), now)
        out["odds_thunderpick"] = _check("warn" if a is None or a > 3600 else "ok", upstream_age_s=a, warn_s=3600,
                                         policy_limit_s=4500, state_label=ps.get("label"), last_success_at=ps.get("last_success_at"))
    except Exception as e:
        out["odds_thunderpick"] = _check("warn", error=type(e).__name__)
    try:
        import moneyline
        for name, st in moneyline.status().items():
            out[f"odds_{name}"] = _check("ok" if st.get("ok") else "warn" if (st.get("consecutive_failures") or 0) < 5 else "fail",
                                         age_s=st.get("age_s"), consecutive_failures=st.get("consecutive_failures"))
    except Exception as e:
        out["odds_exchanges"] = _check("warn", error=type(e).__name__)
    try:                                   # moneyline quote store (ml_store alerts: memory only, thresholds listed)
        import ml_store
        a = ml_store.alerts(now_ts=now.timestamp())
        out["ml_store"] = _check("warn" if a["state"] == "alert" else "ok", alert_state=a["state"], on=a["on"],
                                 thresholds=a["thresholds"])
    except Exception as e:
        out["ml_store"] = _check("warn", error=type(e).__name__)
    try:
        hs = hltv.public_status()
        rev = picklog.data_revision() or ""
        md = re.search(r"max_date=(\d{4}-\d{2}-\d{2})", rev)
        days = (now.date() - datetime.strptime(md.group(1), "%Y-%m-%d").date()).days if md else None
        out["hltv_db"] = _check("fail" if hs.get("breaker_on") or not hs.get("configured") else "warn" if days is None or days > 2 else "ok",
                                breaker_on=hs.get("breaker_on"), latest_match_day_age_d=days, warn_d=2)
    except Exception as e:
        out["hltv_db"] = _check("warn", error=type(e).__name__)
    try:                                   # listed, not-started lines without a current grade, and the oldest grade shown
        rows = [r for bk in ("underdog", "prizepicks") for r in ((prizepicks.board() if bk == "prizepicks" else underdog.board()).get("rows") or [])
                if not _started(r.get("start"), now)]
        graded = [BOARD.get(r["id"]) for r in rows if (BOARD.get(r["id"]) or {}).get("side")]
        ages = [_age_s(g.get("graded_at"), now) for g in graded if g.get("graded_at")]
        share = round(1 - len(graded) / len(rows), 3) if rows else 0.0
        oldest = max([a for a in ages if a is not None], default=None)
        out["grading_backlog"] = _check("warn" if share > 0.3 or (oldest or 0) > 2700 else "ok", listed=len(rows), graded=len(graded),
                                        ungraded_share=share, warn_share=0.3, oldest_grade_age_s=oldest, warn_age_s=2700)
    except Exception as e:
        out["grading_backlog"] = _check("warn", error=type(e).__name__)
    try:
        import membudget
        m = membudget.process_memory()
        lim = m.get("container_limit_mb") or 0
        # growth over the last hour of one-minute samples, leaving out the first MEM_WARMUP_MIN after the process started:
        # that is the caches filling to their budgets (2026-10-18 soak: ~600 MB in the first minutes after every deploy read
        # as 943 MB/h "growth"); real growth after it still warns
        allh = [h for h in list(membudget.HISTORY) if h.get("anon") is not None]
        hist = allh[MEM_WARMUP_MIN:][-60:]
        growth = round((hist[-1]["anon"] - hist[0]["anon"]) * 60 / max(len(hist) - 1, 1), 1) if len(hist) >= 10 else None
        anon_share = round(m["container_anon_mb"] / lim, 3) if lim and m.get("container_anon_mb") else None
        kills = (m.get("container_events") or {}).get("oom_kill")
        out["memory"] = _check("fail" if kills else "warn" if (anon_share or 0) > 0.8 or (growth or 0) > 150 else "ok",
                               total_mb=m.get("container_mb"), anon_mb=m.get("container_anon_mb"), limit_mb=lim or None,
                               anon_share=anon_share, warn_share=0.8, anon_growth_mb_per_h=growth, warn_growth=150,
                               growth_window_min=len(hist), warming_up=len(allh) < MEM_WARMUP_MIN + 10,
                               limit_hits=(m.get("container_events") or {}).get("max"), oom_kills=kills)
    except Exception as e:
        out["memory"] = _check("warn", error=type(e).__name__)
    try:                                   # picks still open 8 h after their listed start (a BO3 is over well before)
        out["settlement_lag"] = dict(_check("ok"), **picklog.settlement_lag(now))
        out["settlement_lag"]["state"] = "warn" if out["settlement_lag"].get("open_after_8h") else "ok"
    except Exception as e:
        out["settlement_lag"] = _check("warn", error=type(e).__name__)
    rank = {"ok": 0, "warn": 1, "fail": 2}
    res = {"state": max((c.get("state", "warn") for c in out.values()), key=lambda x: rank.get(x, 1)), "at": now.isoformat(timespec="seconds"),
           "checks": out}
    _OPS.update(at=t, out=res)
    return res


def studio_history_payload(batches, now=None):
    """Leg results and slip outcomes for member_batches() (pure apart from the pick-log read)."""
    import slip_builder
    now = now or datetime.now(timezone.utc)
    legs = [l for b in batches for ss in (b.get("slips") or {}).values() for s_ in ss for l in s_.get("legs") or []]
    res = slip_batches.leg_result_from_picks(picklog.rows_for_matches({l.get("match_slug") for l in legs}))
    out = []
    for b in batches:
        slips = []
        for ss in (b.get("slips") or {}).values():
            for s_ in ss:
                rs = [res(l) if l.get("prop") != "moneyline" else None for l in s_.get("legs") or []]
                # the ONE lock rule (slip_builder.lock_time: listed start, graded match start, saved lock - 2026-10-26
                # audit: only the listed start counted, so a slip locked at the graded start read 'not started')
                started = any((lt_ := slip_builder.lock_time(l)) is not None and lt_ <= now for l in s_.get("legs") or [])
                outcome = ("lost" if "miss" in rs else "won" if rs and all(r == "hit" for r in rs)
                           else "reduced" if rs and all(r in ("hit", "push", "void") for r in rs) else "in play" if started else "not started")
                slips.append(dict(s_, leg_results=rs, started=started, outcome=outcome))
        out.append({"batch_id": b["batch_id"], "created_at": b.get("created_at"), "closed_at": b.get("closed_at"),
                    "builder_version": b.get("builder_version"), "slips": slips})
    return {"at": now.isoformat(timespec="seconds"), "batches": out}


def content_book_page():
    """The live content book: today's verified drafts on both platforms (posts.drafts - saved forecast + line offered now),
    the board's own facts (other platform's line, opening line) and the Results tab's current figures."""
    import content_book
    import posts
    drafts, rows, skipped = [], [], 0
    for bk in ("prizepicks", "underdog"):
        try:
            b = board_payload(bk)["rows"]
            rows += b
            d = posts.drafts(b, bk, picklog.DB, min_grade="B-")
            drafts += d["drafts"]
            skipped += len(d["skipped"])
        except Exception:
            traceback.print_exc()
    drafts.sort(key=lambda d: (posts.LETTERS.index(d["grade"]), -(d["chance"] or 0)))
    res = None
    try:
        r = results_payload() or {}
        pk = r.get("picks") or {}
        if pk.get("picks"):
            res = (f"{pk['picks']} picks in {pk.get('matches')} matches: printed {pk['printed'] * 100:.1f}% average chance, "
                   f"won {pk['observed'] * 100:.1f}% (a push counts as won), as of {r.get('as_of')}")
    except Exception:
        res = None
    return content_book.page(content_book.live_scripts(drafts, rows), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                             skipped, res)


_MAP1_STATUS = [0.0, None]                            # (time, results_view.map1_status) - admin diagnostics, 5 min


def _p1_export():
    # Member reads must not run learning.connect(): it initializes/migrates the
    # schema and can wait on the learning worker's write transaction.
    import sqlite3
    import learning
    path = Path(learning.DB_PATH)
    if not path.exists():
        return {}
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    con.row_factory = sqlite3.Row
    try:
        # A first learning run may not have committed its schema/export yet.
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
            return {}
        return learning._meta(con, "p1_units") or {}
    finally:
        con.close()


def _p1_export_printed():
    """The P1 export at the chance members were shown: rows logged after ec-1 went live at their calibrated chance
    (esports_cal.forward_export), earlier rows as logged. The record, its letters and the EV gate read this; the slip
    ranking table (fitted on the model's own chance) reads _p1_export()."""
    return esports_cal.forward_export(_p1_export())


def results_payload():
    """Member Results (results_view.member_results) from the learning cycle's stored P1 export. Map 1 stays off it until
    Map 1 has results_view.MAP1_RESULTS_MIN_STAKED settled staked units (owner 2026-10-25); the count is cached here too."""
    import results_view
    ex = _p1_export()
    _MAP1_STATUS[:] = [time.time(), results_view.map1_status(ex)]
    return results_view.member_results(esports_cal.forward_export(ex))


def map_chance_check_status():
    """Admin diagnostics: HLTV's last pre-start map chances vs the maps played, hit rate per match (mapchances, cached
    5 minutes; never a grade input)."""
    try:
        import mapchances
        return mapchances.report_cached()
    except Exception:
        return None


def closing_lines_status():
    """Admin diagnostics (SCOUT_CLOSING_LINES): pick-log lines vs the closing lines saved at lock, CLV in line points by group
    and platform (closing_lines.clv_report, cached 5 minutes, bounded reads; never a grade input)."""
    try:
        import closing_lines
        return closing_lines.report_cached(picklog._read_db)
    except Exception:
        return None


def map1_results_status():
    """Admin diagnostics: Map 1's settled staked P1 units vs MAP1_RESULTS_MIN_STAKED (cached 5 minutes)."""
    import results_view
    if _MAP1_STATUS[1] is None or time.time() - _MAP1_STATUS[0] > 300:
        try:
            _MAP1_STATUS[:] = [time.time(), results_view.map1_status(_p1_export())]
        except Exception as e:
            return {"staked_settled": None, "threshold": results_view.MAP1_RESULTS_MIN_STAKED, "error": str(e)[:200]}
    return dict(_MAP1_STATUS[1])


def board_payload(book="underdog"):
    """The graded board for one platform. book="prizepicks": PrizePicks' lines (prizepicks.py), each graded at its own
    line and market type; its feed state (ok / stale / failed / empty) travels with it. Default Underdog, unchanged."""
    b = prizepicks.board() if book == "prizepicks" else underdog.board()
    now = datetime.now(timezone.utc)
    rows = []
    moved = [] if os.environ.get("SCOUT_CLOSING_LINES") == "1" else None
    from slip_builder import lock_time, lock_iso
    for r in b["rows"]:
        if _started(r.get("start"), now):
            continue                                  # started on its own platform's listed start (compared as times)
        g = BOARD.get(r["id"]) or {}
        lk = lock_time({"start": r.get("start"), "match_start": g.get("match_start")})
        if lk is not None and lk <= now:
            continue                                  # the slip lock rule: the earlier of that start and the graded match's
        if not g.get("side"):
            continue                                  # not graded yet: never shown
        if g.get("graded_line") is not None and (r.get("line") is None or float(g["graded_line"]) != float(r["line"])):
            _closing_moved(moved, r, g, book)
            continue                                  # the line moved: graded afresh within seconds, never shown with the old grade
        if side_opened(g, r):
            _closing_moved(moved, r, g, book)
            continue                                  # graded before the Under was offered: regraded first, never shown meanwhile
        rows.append(dict(r, **{k: g.get(k) for k in ("grade", "side", "p", "units", "proj", "reason", "graded_at", "model_outcomes",
                                                     "nick", "state", "edge", "l10_over", "l10_n", "l10_push", "l10_avg",
                                                     "hs_pct", "p_win", "market", "team_name", "opp_name", "match_slug", "settle_slug",
                                                     "graded_line", "graded_model", "player_id", "event", "bo", "maps_known",
                                                     "dstat", "source", "support", "concern", "tonight", "hltv_retry",
                                                     "retry_cause", "cap_grade", "warn", "graded_sides",
                                                     "model_grade", "stake_reason", "available", "display_version",
                                                     "match_start")}))
        rows[-1]["scope"] = r.get("scope") or "12"
        rows[-1]["lock_at"] = lock_iso(rows[-1])      # one lock time for board, card, slip, Studio and the server recheck
        x = rows[-1]
        if x.get("model_grade") is None and x.get("side"):     # a row graded before display gd-1 (until its next regrade)
            x.update(grade_display.annotate_saved(x))
        nick = x.get("nick") or x.get("player") or ""
        # roles (slip builder + display only; True / False / None = unknown, with the source)
        x["awp"], x["awp_src"] = awp_role(nick, g, x.get("hs_pct"), x.get("team_name") or x.get("team"))
        # confirmed = explicit role data (HLTV weapon stats, Liquipedia); measured = BO3.gg body-shot rate;
        # inferred = known list / HS% - an inferred role is never treated as confirmed (slips)
        src = (x["awp_src"] or "").lower()
        x["awp_conf"] = (None if x["awp"] is None else "confirmed" if src.startswith(("hltv", "liquipedia"))
                         else "measured" if src.startswith("bo3") else "inferred")
        x["igl"], x["igl_src"] = roles.igl_role(nick, x.get("team_name") or x.get("team"))
    for x in rows:
        x["book"] = x.get("book") or "underdog"
        x["match_id"] = _row_match_id(x)         # canonical match id (matchid.py): the same on every platform; None = unlinked
        x["unavailable"] = side_unavailable(x)   # the feed no longer offers this side: shown as such, never actionable
    # ec-1 (gradecore/chance_cal.py): the printed chance, letter and stake from the calibrated chance - display only, after the
    # pick log; BOARD and every logged forecast keep the model's own (p_raw / grade_raw / units_raw ride along)
    rows = esports_cal.cs2_rows(rows)
    _cap_same_result(rows)                    # within this platform: a slip is one platform's entry
    for x in rows:                            # why a stake is capped + warning marker (display only, sorting clean picks first)
        cg = x.pop("cap_grade", None)
        cg = _legacy_cap_words(x.get("reason")) if cg is None else cg
        cut = x.get("cap_n") if (x.get("units_full") or 0) > (x.get("units") or 0) else None   # the cap cut THIS row (0.5U floor)
        x["cap_reason"] = cap_reason_text(cg if (x.get("units") or 0) > 0 else [], cut)
        x["flagged"] = bool((x.get("dstat") or ["ok"])[0] in ("verify", "limited") or x.pop("warn", False))
    if moved is not None:
        _CL_MOVED[book] = moved               # closing lines only (never in the payload): the feed's line while a regrade is due
    # feed errors / sources reach members in words only - no host, port, driver or URL text (2026-10-13 audit)
    out = {"book": book, "updated": b["at"], "error": member_source_note(b["error"]), "rows": rows, "total": len(b["rows"]),
           "version": picklog.MODEL_VERSION}
    if book == "prizepicks":
        out["feed"] = {k: b.get(k) for k in ("state", "at", "checked_at", "lines", "ingested_at")}
        # where this read came from, as an explicit flag (2026-10-13 audit: the page tested the source text for "direct",
        # which the fallback's own text contains - "direct read failed" - and dropped "via the scraper's copy")
        src = b.get("source")
        out["feed"].update(error=member_source_note(b.get("error")), via_scraper=bool(src) and src != prizepicks.DIRECT_SOURCE,
                           source=None if not src else "PrizePicks (direct)" if src == prizepicks.DIRECT_SOURCE
                           else "PrizePicks via the scraper's copy")
    return out


KNOWN_AWP = {"zywoo", "m0nesy", "sh1ro", "broky", "w0nderful", "torzsi", "woro2k", "hallzerk", "s1mple", "device", "jame",
             "molodoy", "910", "ultimate"}
AWP_BOARD_HS = 35          # 3-month HS% under this = an AWPer for the slip builder (riflers sit around 45-60%)
RIFLE_BOARD_HS = 42        # and 42%+ with BO3.gg not calling him an AWPer = a rifler


def awp_role(nick, g, hs_pct, team=None):
    """(True / False / None, source). HLTV's real AWP kill share first (weapon stats, last 90 days),
    then Liquipedia's role listing, BO3.gg's body-shot kill rate, the known list, then HS%."""
    lp, src = roles.awp_role(nick, team)            # HLTV AWP share, then Liquipedia (roles.py)
    # a stand-in / just-moved player's role rows carry his own team: retry with it, as the card's gate does
    # (cs2_scout.hltv_first_gates, 2026-10-21 audit no 24) so the chip and the HS stake rule agree
    for own in dict.fromkeys(t for t in (g.get("own_teams") or ()) if t and t != team):
        if lp is not None:
            break
        lp, src = roles.awp_role(nick, own)
    if lp is not None:
        return lp, src
    snip = g.get("snip")
    if snip == 100:
        return True, "BO3.gg: " + (g.get("awp_note") or "AWPer")
    if (nick or "").lower() in KNOWN_AWP:
        return True, "known AWPer list"
    if hs_pct is not None and hs_pct < AWP_BOARD_HS:
        return True, f"HS% {hs_pct:.0f} (3 months) - AWPer range"
    if snip == 0:
        return False, "BO3.gg: " + (g.get("awp_note") or "rifler")
    if hs_pct is not None and hs_pct >= RIFLE_BOARD_HS:
        return False, f"HS% {hs_pct:.0f} (3 months) - rifler range"
    return None, "unknown"

SAME_RESULT_CAP = 3.0   # units (on the 0.5-3U scale), all bets that need the same team to do well / badly in one match


SIDE_GONE = "Unavailable—side no longer offered"      # a kept (stale) grade whose side the feed dropped
SIDE_NOT_OFFERED = "Unavailable—side not offered"     # a current grade leaning to a side the feed does not offer


def side_unavailable(r):
    """The member-facing label when the platform feed does not offer the row's side for this exact line (2026-10-15 owner:
    a retained PrizePicks Under that is no longer offered must say so - 0U alone does not explain availability), else
    None. The row keeps its grade for evaluation; it is excluded from recommendations, copying and slips."""
    side = r.get("side")
    k = "under" if side == "Less" else "over" if side == "More" else None
    if k is None or r.get(f"{k}_available") is not False:
        return None
    gs = r.get("graded_sides")
    if isinstance(gs, dict):                  # offered when graded, dropped since: no longer offered
        return SIDE_GONE if gs.get(k) is not False else SIDE_NOT_OFFERED
    return SIDE_GONE if r.get("hltv_retry") else SIDE_NOT_OFFERED   # a grade from before graded_sides was recorded


def _cap_same_result(rows):
    """Bets on several players of one team, same side, in one match are one bet on that match's result
    (an upset or an overtime sinks them all). Their units together are capped at SAME_RESULT_CAP.
    Real lines Oct 2-6: total +15.75U on 236U staked -> +13.85U on 151U, worst day -6.8U -> -0.4U.
    The cap really holds (2026-10-11 fix: rounding to the nearest quarter and the 0.5U floor pushed groups to 3.25-4.5U):
    stakes scale to the cap in quarter units, then the largest stakes give back 0.25U at a time (lowest chance first)
    until the group fits; stakes never go under 0.5U, so when even 0.5U each is too much only the highest-chance
    picks keep 0.5U and the rest become 0-unit Leans ('same-match cap')."""
    groups = {}
    for r in rows:
        if (r.get("units") or 0) > 0 and not r.get("unavailable"):   # a side no longer offered is no current bet
            groups.setdefault((r.get("matchup"), r.get("start"), r.get("team"), r.get("side")), []).append(r)
    q = 0.25
    for lst in groups.values():
        tot = sum(r["units"] for r in lst)
        if tot <= SAME_RESULT_CAP + 1e-9:
            continue
        f = SAME_RESULT_CAP / tot
        for r in lst:
            r["units_full"] = r["units"]
            r["units"] = max(0.5, round(r["units"] * f / q) * q)        # quarter units, never below 0.5U
            r["cap_n"] = len(lst)
        # highest chance first; ties by name so the same board always gives the same stakes
        order = sorted(lst, key=lambda r: (-(r.get("p") or 0), str(r.get("nick") or r.get("player") or "")))
        keep = int(SAME_RESULT_CAP // 0.5)
        for r in order[keep:]:                 # even 0.5U each is over the cap: the lowest-chance picks drop to 0U
            r["units"] = 0.0
            r["grade"] = "LEAN"                # the verdict's legacy grade (logged forecasts / slips unchanged)
            r["reason"] = f"0 units: same-match cap ({len(lst)} bets on this team) - {r.get('reason') or ''}".rstrip(" -")
            if r.get("model_grade") in grade_display.LETTERS:      # display gd-1: the grade the chance earns stays
                r["stake_reason"] = f"same-match exposure cap ({len(lst)} bets on this team)"
        while sum(x["units"] for x in lst) > SAME_RESULT_CAP + 1e-9:
            big = [x for x in order[::-1] if x["units"] > 0.5]       # lowest chance first among the largest stakes
            if not big:
                break
            top = max(x["units"] for x in big)
            next(x for x in big if x["units"] == top)["units"] -= q


def cap_reason_text(reasons, cap_n=None):
    """'stand-in; same-match cap (4 bets on this team)' - None when nothing capped the stake."""
    parts = list(reasons or [])
    if cap_n:
        parts.append(f"same-match cap ({cap_n} bets on this team)")
    return "; ".join(parts) or None


_WARN_START = ("HLTV database unreachable", "HLTV database not connected", "Grading resumes", "Projection from the older model",
               "Projection from BO3.gg history", "Provisional", "FORM FIGHT", "TAPE FIGHT", "No upcoming match")


def has_warning(g):
    """Any member-visible warning flag on this grade (not the kills-cap note, the last-10 display note, a per-map note or
    a plain 'last official map N days ago')."""
    for f in (g or {}).get("flags") or []:
        if f.startswith("SOFT BOOK") and "bookmaker price" in f:
            continue    # a bookmaker quote (Thunderpick / OddsPapi book) is a STAKE RULE (1.5U cap, named by cap_grade), not a
                        # data-quality problem - it no longer marks the line "flagged / open data issue" (2026-10-13)
        if f.startswith(("SOFT BOOK", "NO MARKET")):  # a thin exchange book / no qualified market: still a warning
            return True
        if f.startswith(("Gate C: roster", "VETO UNCERTAINTY")) or (f.startswith("OLD PROJECTION") and "HLTV database unreachable" in f):
            # 2026-10-16: no label on the page, still an open data issue
            return True                                              # (Best plays order, the flagged filter)
        t = _public_flag(f)
        if t and ("caution" in t or "unit size limited" in t or t.startswith(_WARN_START)):
            return True
    return False


def _legacy_cap_words(reason):
    """A board row graded before cap_grade existed: the cap named in its verdict reason ('stake capped: RUST')."""
    m = re.search(r"stake capped: (.+)$", str(reason or ""))
    if not m:
        return []
    w = next((w for k, w in _CAP_WORDS if m.group(1).startswith(k)), None)
    if w:
        return [w]                                   # old wording: the internal flag name
    words = {w for _, w in _CAP_WORDS} | {CAP_WORD_BOOKMAKER}
    return [x for x in m.group(1).split("; ") if x in words]


def stake_caps():
    """The stake limits the card's pills quote, read from the grader itself (2026-10-10 audit: the card said max 0.5U
    for a stand-in and max 1U for a thin market while the grader allowed 1U and 1.5U)."""
    from grader import CONFIG
    return {"standin": 1.0, "market": CONFIG["soft_market_cap"], "tape": CONFIG["tape_fight_cap"], "thin": 1.5,
            "same_result": SAME_RESULT_CAP, "kills_chance": CONFIG["p_cap_kills"]}


_BS_CACHE = [0.0, []]
_BS_LOCK = threading.Lock()


def _both_boards():
    """Both platforms' graded rows (a PrizePicks card gets the same-match cap its board applied - 2026-10-11 audit)."""
    rows = list(board_payload()["rows"])
    try:
        rows += board_payload("prizepicks")["rows"]
    except Exception:
        pass
    return rows


def _bs_rows():
    """Both boards' rows at most 5 s old (no board rebuild per member grade). Single-flight: one request rebuilds, the rest
    keep the previous rows (a 700-member rush must not rebuild the board 700 times at once); only the very first build
    makes others wait."""
    if time.time() - _BS_CACHE[0] > 5:
        if _BS_LOCK.acquire(blocking=not _BS_CACHE[0]):
            try:
                if time.time() - _BS_CACHE[0] > 5:
                    _BS_CACHE[:] = [time.time(), _both_boards()]
            finally:
                _BS_LOCK.release()
    return _BS_CACHE[1]


def feed_less_available(name, prop, scope, line, line_type="standard", book=None, start=None):
    """The platform feed's own Under availability for this exact line - the board grades with its row's (2026-10-14: a
    PrizePicks line the feed limits to Over has no Under stake), so a card for the same line must too: one stake per line.
    Platform order as board_stake (the card's own, else Underdog first). None = no platform lists it or the feed doesn't
    say (the grader's line-type rule)."""
    n_ = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())
    st = comparison._ts(start) if start else None
    types = ("standard", "promo") if line_type == "standard" else (line_type,)
    for bk in ((book,) if book else ("underdog", "prizepicks")):
        try:
            rows = (prizepicks.board() if bk == "prizepicks" else underdog.board()).get("rows") or []
        except Exception:
            continue
        hits = [r for r in rows if n_(r.get("player")) == n_(name) and r.get("prop") == prop and (r.get("scope") or "12") == scope
                and float(r.get("line") or -1) == float(line) and (r.get("odds_type") or "standard") in types
                and (not st or not r.get("start") or not comparison._ts(r["start"])
                     or abs((comparison._ts(r["start"]) - st).total_seconds()) <= comparison.JOIN_START_MIN * 60)]
        if hits:
            vals = {r.get("under_available") for r in hits}
            return vals.pop() if len(vals) == 1 else None
    return None


LOCKED_NOTE = "Match started / locked"


def slip_lock(check, *starts, now=None):
    """A comparison.line_check answer under the ONE slip lock rule (slip_builder.lock_time): the earliest of the leg's
    own start, the graded match's start, the leg's saved lock and the platform's CURRENT listed start of the matched rows
    (a match rescheduled earlier). Past it, a verified / changed line is 'unavailable' - 'Match started / locked' - never
    a copyable line. Adds lock_at (the client keeps it on the leg). Identity is untouched."""
    from slip_builder import _ts
    now = now or datetime.now(timezone.utc)
    ts = [t for t in (_ts(x) for x in list(starts) + [r.get("start") for r in (check or {}).get("rows") or []]) if t is not None]
    out = dict(check or {})
    lk = min(ts) if ts else None
    out["lock_at"] = lk.isoformat(timespec="seconds") if lk else None
    if lk is not None and lk <= now and out.get("status") in ("verified", "changed", "stale"):
        out.update(status="unavailable", note=LOCKED_NOTE, rows=[])
        out.pop("new_line", None)
    return out


SLIP_RETRY = "Could not check the lines right now - try again"


def _slip_side(v):
    v = str(v or "")
    return "Less" if v in ("Less", "Under", "lower", "under") else "More" if v in ("More", "Over", "higher", "over") else None


_SLIP_IDX = {}                                        # book -> ((feed at, rows), {normalized player: [rows]})


def _slip_feed(bk):
    """The platform's cached feed (no network I/O) + its rows by normalized player name, built once per feed refresh and
    shared by every member's check (no lock: a race only builds the same index twice)."""
    b = prizepicks.board() if bk == "prizepicks" else underdog.board()
    rows = b.get("rows") or []
    # the feed's own read identity (2026-10-26 audit: the key held id(rows[0]), but prizepicks.board() hands out fresh row
    # copies on every call, so the PrizePicks index was rebuilt on every member check): last good read, when Scout took it
    # in, the feed state (it sets every row's "stale"), the error and the row count; Underdog hands out its own row objects,
    # so their identity is a valid part of its key (a replaced board is rebuilt even within one "at")
    key = (b.get("at"), b.get("ingested_at"), b.get("state"), b.get("error"), len(rows),
           id(rows[0]) if rows and bk != "prizepicks" else None)
    c = _SLIP_IDX.get(bk)
    if c is None or c[0] != key:
        idx = {}
        for r in rows:
            idx.setdefault(comparison._norm(r.get("player")), []).append(r)
        c = _SLIP_IDX[bk] = (key, idx)
    return b, c[1]


def slip_leg_check(leg, feeds=None, now=None, hit=None):
    """The ONE server rule for a slip leg against the platform feed NOW (2026-10-18 owner: a side that is not offered or
    not confirmed is never accepted - also from a slip saved earlier; closeout2 copyval: this answer decides whether the
    page may write the slip to the clipboard at all). -> None when the exact line is listed with this side offered and
    the match has not started, else {"code", "reason"} with a plain member-facing reason.
    The lock (slip_builder.lock_time rule): the earliest of the leg's own start / graded start / saved lock, the platform's
    CURRENT start of the matched rows, and the graded match start the server itself holds for the leg's row and for the
    matched rows (BOARD - not only the page's word). feeds: {book: board()} shared by one request's legs (cached boards:
    no network I/O, no lock held while checking)."""
    from slip_builder import _ts
    now = now or datetime.now(timezone.utc)
    side, book = _slip_side(leg.get("side")), leg.get("book")
    if side is None:
        return {"code": "side", "reason": "Side not offered"}
    try:
        line = float(leg.get("line"))
    except (TypeError, ValueError):
        return {"code": "gone", "reason": "Line not on the platform now"}
    g0 = BOARD.get(str(leg.get("row_id"))) if leg.get("row_id") else None
    own = [t for t in (_ts(x) for x in (leg.get("start"), leg.get("match_start"), leg.get("lock_at"),
                                         (g0 or {}).get("match_start"))) if t is not None]
    if own and min(own) <= now:
        return {"code": "locked", "reason": "Match started"}
    feeds = {} if feeds is None else feeds
    lt = leg.get("line_type") or "standard"
    found = []
    for bk in ((book,) if book in ("underdog", "prizepicks") else ("underdog", "prizepicks")):
        try:
            if bk not in feeds:
                feeds[bk] = _slip_feed(bk)
            fb, idx = feeds[bk]
            # only this player's rows (line_check matches by exact normalized name here: no provider-id map) - the
            # same answer as the whole feed without scanning ~2,000 rows per leg (a feed error reads "try again" either
            # way: line_check's failed and stale both map to SLIP_RETRY below)
            sub = dict(fb, rows=idx.get(comparison._norm(leg.get("nick")), []))
            check = comparison.line_check(sub, leg.get("nick"), leg.get("prop"), leg.get("scope") or "12", line,
                                          team=leg.get("team"), start=leg.get("start"), opponent=leg.get("opponent"),
                                          book=bk, odds_type=lt, side=side, now=now)
        except Exception:
            found.append((3, "retry", SLIP_RETRY))
            continue
        graded = [(BOARD.get(str(r.get("id"))) or {}).get("match_start") for r in check.get("rows") or [] if r.get("id")]
        check = slip_lock(check, leg.get("start"), leg.get("match_start"), leg.get("lock_at"), *graded, now=now)
        st, note = check.get("status"), check.get("note") or ""
        if st == "verified":
            if hit is not None:                   # PP_OPEN_LINK: the feed rows this verified leg matched (their ids)
                same = [r for r in check.get("rows") or [] if r.get("line") == line
                        and ((not r.get("alt")) if _lt_norm(lt) == "standard" else r.get("odds_type") == lt)]
                hit.update(book=bk, rows=[{"id": r.get("id"), "odds_type": r.get("odds_type"), "line": r.get("line")} for r in same])
            return None
        if note == LOCKED_NOTE:
            found.append((0, "locked", "Match started"))
        elif st == "changed" and check.get("new_line") is not None:
            nl = float(check["new_line"])
            found.append((1, "moved", f"Line moved to {int(nl) if nl.is_integer() else nl}"))
        elif st == "unavailable" and check.get("rows"):
            found.append((2, "side", "Side not offered"))
        elif st in ("failed", "stale") and note.startswith("Regrade"):
            found.append((4, "regrade", "Regrade this pick with its team and match start"))
        elif st in ("failed", "stale"):
            found.append((3, "retry", SLIP_RETRY))          # feed down / not fresh: never confirmed, never copied
        else:                                               # no line for it / two players fit
            found.append((5, "gone", "Line not on the platform now"))
    if not found:
        return {"code": "gone", "reason": "Line not on the platform now"}
    _, code, reason = min(found)
    return {"code": code, "reason": reason}


def _leg_label(leg):
    side = _slip_side(leg.get("side"))
    return f"{leg.get('nick')} {'Over' if side == 'More' else 'Under' if side == 'Less' else ''} {leg.get('line')}".replace("  ", " ")


def slip_leg_problem(leg, feeds=None, now=None):
    """slip_leg_check as one line: '<nick> <Over/Under> <line>: <reason>' (None = the leg is up)."""
    c = slip_leg_check(leg, feeds, now)
    return None if c is None else f"{_leg_label(leg)}: {c['reason']}"


def _pp_leg(leg, hit):
    """One verified PrizePicks leg -> its link leg, from the feed row(s) the check matched: the leg's own row id when it is
    one of them, else exactly one projection, else a standard (not promo) one when exactly one; two that still fit = no
    link (never a guess)."""
    rows = [r for r in (hit or {}).get("rows") or [] if pplink.projection_id(r.get("id"))]
    if (hit or {}).get("book") != "prizepicks" or not rows:
        return None
    ids = {pplink.projection_id(r["id"]) for r in rows}
    own = pplink.projection_id(leg.get("row_id"))
    if own and own in ids:                             # the row the member picked (e.g. a promo at a standard's line)
        rows = [r for r in rows if pplink.projection_id(r["id"]) == own]
    elif len(ids) > 1:
        rows = [r for r in rows if (r.get("odds_type") or "standard") == "standard"]
        if len({pplink.projection_id(r["id"]) for r in rows}) != 1:
            return None
    r = rows[0]
    return {"projection_id": pplink.projection_id(r["id"]), "side": _slip_side(leg.get("side")), "line": r.get("line"),
            "odds_type": r.get("odds_type") or leg.get("line_type") or "standard", "book": "prizepicks"}


def studio_open_link(rec, feed):
    """PP_OPEN_LINK, a Slip Studio slip that verify_before_copy just passed: PrizePicks -> {"pp_url"} (each leg's row id is
    the projection id - verify matched it to the current graded row - and must still be a row of the current feed) or
    {"pp_reason"}; Underdog -> {"open_url": the pick'em home} (no pre-fill). No I/O: the feed is the one verify read."""
    book = rec.get("book") or "underdog"
    if book == "underdog":
        # each leg's own line (2026-10-29): its row id must still be a row of the feed verify read, at the same line
        live = {str(r.get("id")): r for r in (feed or {}).get("rows") or [] if r.get("id")}
        lk = []
        for l in rec.get("legs") or []:
            r = live.get(str(l.get("row_id")))
            ok = r is not None and (l.get("book") or "underdog") == "underdog" and r.get("line") == l.get("line")
            lk.append(ud_row_link(l.get("row_id")) if ok else None)
        return dict({"open_url": pplink.UD_HOME}, **ud_legs_open(lk))
    if book != "prizepicks":
        return {}
    legs = rec.get("legs") or []
    live = {str(r.get("id")): r for r in (feed or {}).get("rows") or [] if str(r.get("id") or "").startswith("pp:")}
    pl = []
    for l in legs:
        r = live.get(str(l.get("row_id")))
        if r is None or (l.get("book") or "underdog") != "prizepicks":
            return {"pp_reason": "PrizePicks' id for a pick on this slip is not known - copy the slip instead"}
        pl.append({"row_id": l.get("row_id"), "side": l.get("side"), "line": r.get("line"),
                   "odds_type": r.get("odds_type") or l.get("market_type"), "book": "prizepicks"})
    url, why = pplink.pp_link(pl)
    return {"pp_url": url} if url else {"pp_reason": why}


def ud_row_link(row_id):
    """PP_OPEN_LINK: Underdog's link to the market screen of one CURRENT Underdog line (its line id = the board row id),
    from the ids the last board read carried (underdog.ud_ids) - None when that line is not on the board now."""
    t = underdog.ud_ids(row_id)
    return pplink.ud_link(*t) if t else None


def _ud_leg(leg, hit):
    """One verified Underdog leg -> its line's link: the leg's own row (line id) when it is one the check matched, else
    exactly one matched row; two that still fit = no link (never a guess)."""
    if (hit or {}).get("book") != "underdog":
        return None
    ids = [str(r.get("id")) for r in hit.get("rows") or [] if r.get("id")]
    own = str(leg.get("row_id") or "")
    rid = own if own and own in ids else ids[0] if len(set(ids)) == 1 else None
    return ud_row_link(rid) if rid else None


def ud_legs_open(links):
    """[link or None per leg] -> {"legs_open": links} when at least one leg has one, else {} (the answer stays as it was)."""
    return {"legs_open": list(links)} if any(links) else {}


def with_ud_links(payload):
    """PP_OPEN_LINK: the Underdog board answer with each row's "ud_app_link" (rows are copied - the shared board, the
    shadow logs and Studio records never carry it)."""
    rows = []
    for r in payload.get("rows") or []:
        lk = ud_row_link(r.get("id")) if (r.get("book") or "underdog") == "underdog" else None
        rows.append(dict(r, ud_app_link=lk) if lk else r)
    return dict(payload, rows=rows)


def _ud_live():
    """The current Underdog feed rows by line id (cached board: no network I/O)."""
    return {str(r.get("id")): r for r in (underdog.board().get("rows") or []) if r.get("id")}


def tail_visible(rec, tail, live, now=None):
    """PP_OPEN_LINK owner tail link of one Studio slip -> the url members may open, or None: an Underdog slip, the link
    attached to exactly these lines (signature), still a valid share link, no leg's match started (the one lock rule), and
    every leg still on Underdog's board at its line with its side offered."""
    import ud_tails
    from slip_builder import lock_time
    if not rec or not tail or (rec.get("book") or "underdog") != "underdog" or (rec.get("status") or "live") != "live":
        return None
    if tail.get("sig") != ud_tails.leg_sig(rec) or pplink.tail_share_id(tail.get("url"))[0] != tail.get("url"):
        return None
    now = now or datetime.now(timezone.utc)
    legs = rec.get("legs") or []
    if not legs:
        return None
    for l in legs:
        r = live.get(str(l.get("row_id")))
        if r is None or r.get("line") != l.get("line") or _started(r.get("start"), now):
            return None
        lk = lock_time({"start": l.get("start"), "match_start": l.get("match_start")})
        lk2 = lock_time({"start": r.get("start"), "match_start": (BOARD.get(str(r.get("id"))) or {}).get("match_start")})
        if any(x is not None and x <= now for x in (lk, lk2)):
            return None
        if (r.get("over_available") if l.get("side") == "More" else r.get("under_available")) is False:
            return None
    return tail["url"]


def studio_tail_annotate(b):
    """PP_OPEN_LINK: a Studio batch answer with "tail_url" on each slip whose owner tail link is still showable."""
    if not PP_OPEN_LINK or not b:
        return b
    import ud_tails
    tails = ud_tails.all_tails()
    mine = [s_ for xs in (b.get("slips") or {}).values() for s_ in xs or [] if s_.get("slip_id") in tails]
    if mine:
        live = _ud_live()
        for s_ in mine:
            u_ = tail_visible(s_, tails[s_["slip_id"]], live)
            if u_:
                s_["tail_url"] = u_
    return b


_TAIL_LEG = ("nick", "player", "side", "line", "prop", "scope", "market_type", "grade", "p", "team", "opponent", "start",
             "match_start", "lock_at", "row_id", "book")


def studio_tails_payload():
    """Members: the owner's Underdog slips with a showable tail link (legs as stored, display fields only)."""
    import slip_builder
    import ud_tails
    tails, out = ud_tails.all_tails(), []
    live = _ud_live() if tails else {}
    for sid_, t in sorted(tails.items(), key=lambda kv: kv[1].get("at") or "", reverse=True):
        rec = slip_builder.get_slip(sid_)
        if not rec or not rec.get("batch_id"):
            continue
        dead = slip_builder.slip_status(rec["batch_id"], sid_)
        u_ = None if dead else tail_visible(rec, t, live)
        if u_:
            out.append({"slip_id": sid_, "book": "underdog", "n": len(rec.get("legs") or []), "all_hit": rec.get("all_hit"),
                        "legs": [{k: l.get(k) for k in _TAIL_LEG} for l in rec.get("legs") or []], "tail_url": u_})
    return {"slips": out}


def admin_ud_tail(form):
    """POST /admin/ud_tail (admin token): slip_id + url attaches an Underdog share link; clear=1 removes it."""
    import slip_builder
    import ud_tails
    sid_ = str(form.get("slip_id") or "").strip()
    if not _SLIP_RE.match(sid_):
        return 400, {"error": "slip_id must be a stored Studio slip id"}
    if form.get("clear") == "1":
        return 200, {"ok": True, "removed": ud_tails.detach(sid_)}
    rec = slip_builder.get_slip(sid_)
    if not rec:
        return 404, {"error": "unknown slip"}
    ent, why = ud_tails.attach(rec, form.get("url"))
    if not ent:
        return 400, {"error": why}
    return 200, {"ok": True, "slip_id": sid_, "share_id": ent["share_id"], "url": ent["url"],
                 "showing": bool(tail_visible(dict(rec, status="live"), ent, _ud_live()))}


def admin_ud_tails_page(token):
    import html as _h
    import ud_tails
    rows = "".join(f"<tr><td>{_h.escape(k)}</td><td>{_h.escape(v.get('share_id') or '')}</td><td>{_h.escape(v.get('at') or '')}</td></tr>"
                   for k, v in sorted(ud_tails.all_tails().items()))
    t = _h.escape(token or "", quote=True)
    leg_txt = lambda l: f"{l.get('nick') or l.get('player')} {'Higher' if l.get('side') == 'More' else 'Lower'} {l.get('line')}"
    try:                                           # the newest Underdog Studio slips, so the owner can pick the id
        import slip_builder
        with slip_builder._db() as con:
            rec_ = [json.loads(r[0]) for r in con.execute("SELECT record FROM slips ORDER BY created_at DESC LIMIT 40")]
        recent = "".join(f"<tr><td><code>{_h.escape(r_.get('slip_id') or '')}</code></td>"
                         f"<td>{_h.escape(' / '.join(leg_txt(l) for l in r_.get('legs') or []))}</td></tr>"
                         for r_ in rec_ if (r_.get("book") or "underdog") == "underdog")
    except Exception:
        recent = ""
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Underdog tail links</title>"
            "<body style='font-family:system-ui;max-width:760px;margin:20px auto;padding:0 16px'><h2>Underdog tail links</h2>"
            "<p>Paste the share link of the Underdog entry you made for a Slip Studio slip. Members see &ldquo;Tail on Underdog&rdquo; on it "
            "until any leg's match starts or a line on it changes.</p>"
            f"<form method=post action='/admin/ud_tail'><input type=hidden name=token value='{t}'>"
            "<p><label>Slip id <input name=slip_id required style='width:100%'></label></p>"
            f"<p><label>Underdog share link <input name=url type=url maxlength={pplink.TAIL_MAX} style='width:100%'></label></p>"
            "<p><label><input type=checkbox name=clear value=1> Remove the link from this slip</label></p><button>Save</button></form>"
            f"<h3>Attached</h3><table border=1 cellpadding=4><tr><th>Slip</th><th>Share id</th><th>Attached</th></tr>{rows}</table>"
            f"<h3>Newest Underdog Studio slips</h3><table border=1 cellpadding=4><tr><th>Slip id</th><th>Legs</th></tr>{recent}</table></body>")


def slip_line_advantages(legs):
    """Read-only easier-line suggestions, from cached CS2 boards. No grade writes,
    provider requests, implicit platform switch, or relaxation of copy checks."""
    try:
        import line_advantage_live
        rows = _bs_rows()
        wanted = {(l.get("book"), l.get("row_id")) for l in legs
                  if isinstance(l.get("book"), str) and isinstance(l.get("row_id"), str)}
        identities = {(r.get("player_id"), r.get("match_id"), r.get("prop"), r.get("scope"))
                      for r in rows if (r.get("book"), r.get("id")) in wanted}
        if not identities:
            return []
        selected = [r for r in rows if (r.get("player_id"), r.get("match_id"), r.get("prop"), r.get("scope")) in identities]
        feeds = {bk: _slip_feed(bk)[0] for bk in ("underdog", "prizepicks")}
        return line_advantage_live.recommendations(legs, selected, feeds, datetime.now(timezone.utc))
    except Exception:
        return []                           # missing comparison never means "best line"


def slip_check(d, link=False):
    """Every rule a copied slip must pass on the server BEFORE the page writes it to the clipboard (closeout2 copyval,
    owner: validation first, the clipboard only on the server's OK): each leg (slip_leg_check incl. the lock rule), one
    platform per slip, at least 2 picks, and the platform's published entry rules (platform_rules.entry_check).
    -> {"ok", "legs": [member-facing lines], "problems": [{leg, nick, code, reason}]}.
    Bounded: <= 6 legs, each platform's cached feed read once per request, no lock held, no network I/O."""
    legs = d["legs"]
    feeds, out, hits = {}, [], []
    for i, l in enumerate(legs):
        hits.append({})
        c = slip_leg_check(l, feeds, hit=hits[-1]) if link else slip_leg_check(l, feeds)
        if c:
            out.append(dict(c, leg=i, nick=l.get("nick"), text=f"{_leg_label(l)}: {c['reason']}"))
    books_ = {str(l.get("book")) for l in legs if l.get("book")}     # a crafted non-string book: a refusal, never a 500
    if len(books_) > 1 or (books_ and d.get("book") in ("underdog", "prizepicks") and books_ != {d.get("book")}):
        names = sorted({"prizepicks": "PrizePicks", "underdog": "Underdog"}.get(str(b), str(b)) for b in books_ | (
            {d.get("book")} if d.get("book") in ("underdog", "prizepicks") else set()))
        out.append({"leg": None, "code": "platforms", "text": "One platform per slip - these picks are on " + " and ".join(names)})
    if len(legs) < 2:
        out.append({"leg": None, "code": "size", "text": "Entries need at least 2 picks"})
    if not out:                                        # 2026-10-23: the platform's published entry rules (platform_rules.py)
        ec = platform_rules.entry_check(legs, d.get("book") if d.get("book") in ("underdog", "prizepicks") else None)
        if ec.get("eligible") is False:
            for r in ec.get("refusals") or []:
                out.append({"leg": None, "code": "entry",
                            "text": "Entries need players from at least 2 different teams" if "different teams" in r else r})
    for x in out:
        x.setdefault("reason", x["text"])
    res = {"ok": not out, "legs": [x["text"] for x in out], "problems": out}
    if link and res["ok"] and d.get("book") == "underdog" and all(l.get("book") == "underdog" for l in legs):
        res["ud_legs"] = [_ud_leg(l, h) for l, h in zip(legs, hits)]      # each line's own Underdog link (or None)
    if link and res["ok"]:
        # PP_OPEN_LINK: the PrizePicks lineup link of THIS checked slip (the ids of the feed rows each leg verified against)
        if d.get("book") != "prizepicks" or any(l.get("book") != "prizepicks" for l in legs):
            res["pp_reason"] = "Only PrizePicks slips open in PrizePicks"
        else:
            pl = [_pp_leg(l, h) for l, h in zip(legs, hits)]
            if any(x is None for x in pl):
                res["pp_reason"] = "PrizePicks' id for a pick on this slip is not known - copy the slip instead"
            else:
                res["pp_url"], why = pplink.pp_link(pl)
                if not res["pp_url"]:
                    res.pop("pp_url")
                    res["pp_reason"] = why
    return res


def board_stake(res, prop, scope, line, line_type="standard", book=None):
    """The same line on the board with its stake cut by the same-match cap: the card shows that stake too (one stake
    per line on the whole site). Only when the board row is this exact pick (side, line, maps, match) and was cut from
    exactly this card's stake - otherwise the card's own stake stands. Returns the verdict (a new dict when capped).
    book: the card's platform; a tuple of platforms (a typed card / Slate line / share link with no platform - 2026-10-13
    audit: those always used Underdog's cap, so a PrizePicks-only line showed another stake than its board) = the first
    platform whose board lists this exact pick decides, Underdog first."""
    v = res.get("verdict") or {}
    if not v.get("side") or not v.get("units"):
        return v
    nick = ((res.get("player_block") or {}).get("player") or {}).get("nick") or ""
    nm = res.get("next_match") or {}
    st, slug = comparison._ts(nm.get("start")), nm.get("slug")
    mid = _card_match_id(res)
    rows = _bs_rows()
    book = book or ("prizepicks" if line_type in ("goblin", "demon") else "underdog")   # the card's own platform
    for bk in ((book,) if isinstance(book, str) else tuple(book)):
        exact = [r for r in rows if (r.get("book") or "underdog") == bk
                 and str(r.get("nick") or r.get("player") or "").lower() == nick.lower() and r.get("prop") == prop
                 and (r.get("scope") or "12") == scope and float(r.get("line") or -1) == float(line) and r.get("side") == v["side"]
                 and (st or slug) and same_match(r, slug, st, mid)]
        for r in exact:
            if r.get("cap_n") and r.get("units_full") == v["units"] and r["units"] < r["units_full"]:
                out = dict(v, units=r["units"], units_full=r["units_full"], cap_n=r["cap_n"],
                           cap_team=r.get("team_name") or r.get("team"))
                if not r["units"]:              # the cap left no room for this pick: a 0-unit Lean, as on the board
                    out.update(grade="LEAN", reason=r.get("reason") or v.get("reason"))
                    if r.get("stake_reason"):           # display gd-1: the model grade stays, the cap is named
                        out.update(stake_reason=r["stake_reason"])
                return out
        if exact:
            return v                            # this platform lists the pick uncut: its stake stands
    return v


def _lt_norm(x):
    """A line's market type for matching: PrizePicks goblin / demon are their own markets; standard and promo lines grade
    as standard."""
    return x if x in ("goblin", "demon") else "standard"


def kept_board_row(res, prop, scope, line, book=None, line_type="standard", why=None):
    """The Prop Board's saved grade for exactly this pick, or None: same player, match (the match id when both sides know
    it - the platform's listed start can differ from the graded match's start by hours, 2026-10-14 post-deploy check: WBT vs
    ENCE listed 14:00, BO3.gg 12:00; otherwise the start within the join window), platform, stat, maps, line and market
    type, graded at this line, and its side still offered by the platform's feed. Platforms in order (the card's own, else
    Underdog first)."""
    nick = (((res.get("player_block") or {}).get("player") or {}).get("nick") or "").lower()
    nm = res.get("next_match") or {}
    st, slug = comparison._ts(nm.get("start")), nm.get("slug")
    if not nick or not (st or slug):
        return None
    mid = _card_match_id(res)
    try:
        rows_ = _bs_rows()
    except Exception:
        rows_ = _BS_CACHE[1]
    for bk in ((book,) if isinstance(book, str) else tuple(book or ("underdog", "prizepicks"))):
        for r in rows_:
            if not ((r.get("book") or "underdog") == bk and str(r.get("nick") or r.get("player") or "").lower() == nick
                    and r.get("prop") == prop and (r.get("scope") or "12") == scope and float(r.get("line") or -1) == float(line)
                    and _lt_norm(r.get("line_type") or r.get("odds_type")) == _lt_norm(line_type)
                    and r.get("side") and r.get("grade")):
                continue
            try:
                if r.get("graded_line") is None or float(r["graded_line"]) != float(line):
                    continue                      # graded at another line
            except (TypeError, ValueError):
                continue
            if not same_match(r, slug, st, mid):
                continue                          # another match (the id decides, whatever the listed start)
            if (r["side"] == "Less" and r.get("under_available") is False) or (r["side"] == "More" and r.get("over_available") is False):
                if why is not None:               # the feed no longer offers this side: say so (never "no saved grade")
                    why.append(SIDE_GONE)
                continue
            return r
    return None


def _outage_cause(g):
    cs_ = _transient_causes(g or {})
    return ("HLTV database unreachable right now" if "hltv" in cs_ or not cs_ else
            "Thunderpick lists this match but has no current quote")


def outage_verdict(res, prop, scope, line, v, book=None, line_type="standard"):
    """During an outage (HLTV unreachable, a Thunderpick blip, the performance model's input revision unavailable) the
    card has no grade of its own - its temporary numbers are no forecast (2026-10-14 owner: never a temporary projection or
    a made-up chance presented as a new prediction, never a stake on it). It shows the Prop Board's saved grade only when
    that is for exactly this pick (kept_board_row), with the time it was graded and marked stale; otherwise a
    data-unavailable verdict: no side, chance, grade or stake."""
    if not isinstance(v, dict):
        return v
    why = []
    r = kept_board_row(res, prop, scope, line, book, line_type, why=why)
    if r is not None:
        ds_ = r.get("dstat")
        when = r.get("graded_at") or "an earlier regrade"
        return {"side": r["side"], "p": r.get("p"), "grade": r["grade"], "units": r.get("units") or 0.0,
                "model_grade": r.get("model_grade"), "stake_reason": r.get("stake_reason"), "available": r.get("available"),
                "units_full": r.get("units_full"), "cap_n": r.get("cap_n"), "cap_team": r.get("team_name") or r.get("team"),
                "state": r.get("state"), "dstat": ds_,
                # the board row's own cap reason + warning marker (board_payload already turned cap_grade / warn into
                # them and popped both - audit 52/57: reading the popped keys dropped them on the outage card)
                "cap_reason": ((r.get("cap_reason") if "cap_reason" in r else cap_reason_text(r.get("cap_grade") or [], r.get("cap_n")))
                               if (r.get("units") or 0) > 0 else None),
                "flagged": (bool(r.get("flagged")) if "flagged" in r
                            else bool((ds_ or [None])[0] in ("verify", "limited") or r.get("warn"))),
                "kept_from_board": True, "stale": True, "unavailable": False, "temporary": False,
                "graded_at": r.get("graded_at"), "kept_book": r.get("book") or "underdog",
                "graded_model": r.get("graded_model"),     # the saved grade's own model version (night/consistency)
                "kept_cause": r.get("retry_cause") if r.get("hltv_retry") else None,
                "reason": f"{_outage_cause(res.get('grade'))} - this is the Prop Board's saved grade of this exact pick from "
                          f"{when} (stale, not a new forecast); regraded when live data is back"}
    if why:                                       # the saved grade is for a side the platform no longer offers
        return {"side": None, "p": None, "grade": None, "units": 0.0, "state": "UNAVAILABLE", "unavailable": True,
                "kept_from_board": False, "stale": False, "temporary": False, "side_unavailable": SIDE_GONE,
                "reason": f"{SIDE_GONE} - the Prop Board's saved grade of this pick is for a side the platform no longer "
                          f"offers; {_outage_cause(res.get('grade'))}, so there is no current grade"}
    return {"side": None, "p": None, "grade": None, "units": 0.0, "state": "UNAVAILABLE", "unavailable": True,
            "kept_from_board": False, "stale": False, "temporary": False,
            "reason": f"{_outage_cause(res.get('grade'))} - no grade for this line until live data is back. The Prop Board has "
                      "no saved grade of this exact pick (player, match, platform, stat, maps, line and market type) to show"}


# what an outage card keeps of its own grade: the line it is for and why it has no grade - never the temporary numbers
_OUTAGE_GRADE_KEYS = ("line", "prop", "scope", "line_type", "flags", "projection_source", "perf_transient", "odds_transient",
                      "odds_transient_reason", "odds_transient_source", "input_revisions")


def withhold_temporary(res):
    """An outage card never shows its temporary projection: the chances, projection, ladder, simulation and line
    comparison all come from the fallback grade, so they are removed (the verdict is outage_verdict's)."""
    g = res.get("grade") or {}
    res["grade"] = dict({k: g[k] for k in _OUTAGE_GRADE_KEYS if k in g}, status="DATA UNAVAILABLE", withheld=True,
                        reason=_outage_cause(g))
    res["ladder"] = []
    res["simulation"] = None
    res["comparison"] = {"error": "no line comparison while live data is unavailable"}
    fg = res.get("form_gap")                  # audit 56: its projection / gap are the temporary grade's lambda
    if isinstance(fg, dict):
        res["form_gap"] = {k: v for k, v in fg.items() if k not in ("projection", "gap")}
    return res


def withhold_shadow(res):
    """sr-bo3-2: a member card for a BO3.gg-route (shadow) grade shows no projection, chance, letter, stake, ladder,
    simulation or line comparison - the line is hidden; the full grade was already logged. Returns res."""
    g = res.get("grade") or {}
    res["grade"] = dict({k: g[k] for k in _OUTAGE_GRADE_KEYS if k in g}, status="NO PROJECTION", withheld=True, shadow=True,
                        reason=grade_display.BO3_SHADOW_REASON)
    res["verdict"] = grade_display.shadow_verdict()
    res["ladder"] = []
    res["simulation"] = None
    res["comparison"] = {"error": "no line comparison: this line has no shown projection"}
    fg = res.get("form_gap")
    if isinstance(fg, dict):
        res["form_gap"] = {k: v for k, v in fg.items() if k not in ("projection", "gap")}
    return res


def outage_info(res, causes):
    """The card's outage notice: the cause(s), the odds note for a Thunderpick blip, and the saved board grade it shows
    (only the exact pick - never another line's grade)."""
    v = res.get("verdict") or {}
    rows = ([{"book": v.get("kept_book"), "line": (res.get("grade") or {}).get("line"), "side": v.get("side"),
              "grade": v.get("grade"), "p": v.get("p"), "units": v.get("units"), "graded_at": v.get("graded_at"),
              "stale": True}] if v.get("kept_from_board") else [])
    return {"rows": rows, "causes": causes, "odds_note": odds_blip_note(res) if "odds" in causes else None,
            "unavailable": bool(v.get("unavailable"))}


# ---------------------------------------------------------------- research tools: moneylines + slip studio (no grading)
# Nothing here feeds or changes a grade. Every member request reads shared in-memory state: moneyline.py keeps one
# background refresher per exchange, the slip studio builds from a graded-board snapshot rebuilt at most every few
# seconds by one thread, and no member request calls an upstream API.
ML_TTL = 30                       # /api/moneylines: one response shared by every member for 30 s


def studio_flag(env=None):
    """The member slip studio is ON (owner 2026-10-12: "rebuild slip selection around portfolio diversity" ... "get it ready",
    after being told the old builder was off pending settled batches). SLIP_STUDIO=0 is the kill switch. The panel itself says
    the diversified builder is still being validated on settled batches."""
    return (os.environ if env is None else env).get("SLIP_STUDIO", "1") != "0"


PP_OPEN_LINK = pplink.enabled()   # "Open in PrizePicks" / "Open Underdog" on checked slips - OFF unless PP_OPEN_LINK=1 (owner app test)
SLIP_STUDIO = studio_flag()       # on by default (owner 2026-10-12); SLIP_STUDIO=0 turns the endpoints + panel off
STUDIO_BUILDS_PER_MIN = 20        # Build presses per member per minute (a retry with the same idem key is free)
STUDIO_EVENTS_PER_MIN = 40        # verify + event calls per member per minute (its own bucket - never eats build quota)
STUDIO_BODY_MAX = 16 * 1024
POST_MAX = 20000                  # every other POST body (slip log <= 20,000 bytes; the admin closing-line form far less)
SLIP_LOG_PER_MIN = 10             # /api/slip_log entries per member per minute
MC_PER_MIN = 30                   # /api/match_compare requests per member per minute (audit 20; a card asks once per maps / platform)
SEARCH_BO3_PER_MIN = 60           # /api/search lookups that go to BO3.gg (HLTV index down, query not cached) per member a minute
MC_TEAM_MAX = 80                  # longest team / opponent name /api/match_compare accepts
MC_START_AHEAD_D = 21             # latest match start it accepts (days ahead; a past start already projects as today)
STUDIO_CAP_TTL = 5                # /api/slip_studio/capacity: one answer per settings shared by every member for 5 s
SHADOW_LABEL = "Shadow - not a staking recommendation"
MODEL_NOT_VALIDATED = "Model: not validated yet - market baseline shown, no recommendation"
_ML_BLOCK_KEYS = ("available", "prob_a", "bid", "ask", "spread", "bid_size", "ask_size", "depth", "best_depth", "status",
                  "current", "fresh", "market_open", "price_only", "usable", "reason", "source_ts", "received_ts",
                  "team_a_name", "team_b_name", "last_trade", "prob_b_yes", "prob_b_usable", "coherence_gap", "coherence_flag")
RESEARCH = {"matchid_ready": False, "moneyline_started": [], "errors": []}


# ------------------------------------------------------------ LoL data center (lol/center) - backend only, not released
# The server never imports lol (the lol package's isolation rule, lol/tests/test_lol_isolation.py): it starts the worker
# process and reads the files it writes, plus the quote archive read-only. No member page shows any of it.
LOL_CENTER = Path(DATA_DIR) / "lol" / "center"
LOL_SUP = {"proc": None, "starts": 0, "started_at": None, "last_exit": None, "disabled": None}


_LOL_PUB = {"at": 0.0, "on": False}


def lol_public():
    """The owner's release switch for the member LoL page (default off): LOL_PUBLIC=1, or the flag file the admin release
    action writes on the data disk (checked at most every 15 s)."""
    if os.environ.get("LOL_PUBLIC", "0") == "1":
        return True
    if time.time() - _LOL_PUB["at"] > 15:
        _LOL_PUB.update(at=time.time(), on=(LOL_CENTER / "public.flag").exists())
    return _LOL_PUB["on"]
_LOL_MEMBER = {"mtime": None, "body": None, "lock": threading.Lock()}   # mtime = (board.json mtime, lc-1 state)
LOL_STAKE_NOTE = "0U: LoL stakes start once LoL picks have a settled record at real lines. The grade is the model's grade for this line."


def lol_member_board():
    """The member view of the LoL board (bytes, cached until the worker rewrites board.json): only lines with a saved
    pre-match forecast; no internal ids, admin flags or slip internals. Read-only file access - never imports lol."""
    path = LOL_CENTER / "board.json"
    try:
        mt = (path.stat().st_mtime, letter_containment.key("lol"))     # a containment change rebuilds once
    except OSError:
        return None
    if _LOL_MEMBER["mtime"] == mt and _LOL_MEMBER["body"] is not None:
        return _LOL_MEMBER["body"]
    with _LOL_MEMBER["lock"]:                                   # single flight: concurrent members wait for one rebuild
        if _LOL_MEMBER["mtime"] == mt and _LOL_MEMBER["body"] is not None:
            return _LOL_MEMBER["body"]
        return _lol_member_rebuild(path, mt)


LOL_PLATFORM = {"underdog": "Underdog", "prizepicks": "PrizePicks"}
LOL_STAT = {"kills": "Kills", "assists": "Assists", "deaths": "Deaths", "fantasy_points": "Fantasy points"}
LOL_SCOPE_GAMES = {"Map 1": "game 1 only", "Maps 1-2": "games 1 and 2 added", "Maps 1-3": "games 1, 2 and 3 added"}


def _lol_line_type(platform, odds_type, market):
    """(short label, sentence) for the kind of line - definite facts from the feed only."""
    kind = (market or {}).get("kind")
    if kind == "promotional" or odds_type == "promo":
        return "Promo", "PrizePicks promotional line"
    if platform == "prizepicks" and odds_type == "goblin":
        return "Goblin", "PrizePicks Goblin: an alternate line set below the standard line, with a lower payout"
    if platform == "prizepicks" and odds_type == "demon":
        return "Demon", "PrizePicks Demon: an alternate line set above the standard line, with a higher payout"
    if platform == "underdog" and odds_type not in (None, "balanced"):
        return "Adjusted", f"Underdog {odds_type} line"
    return "Standard", "Standard line"


def _lol_sides(platform, sides):
    """[{side, offered, payout}] - payout is Underdog's per-pick payout modifier, never odds; a side the feed does not
    list as available is not offered."""
    out = []
    for key, name in (("higher", "Over"), ("lower", "Under")):
        s = (sides or {}).get(key) or {}
        if platform == "underdog":
            on = bool(s) and (s.get("status") in (None, "active"))
            out.append({"side": name, "offered": on, "payout": s.get("payout_multiplier") if on else None})
        else:
            out.append({"side": name, "offered": s.get("offered") is True, "payout": None})
    return out


def _lol_key(platform, market_key):
    """An opaque, stable key for one offered line (the internal market key is not shown to members)."""
    return hashlib.sha1(f"{platform}|{market_key}".encode()).hexdigest()[:16]


LOL_SLIP_REASON = {"stake 0U": "LoL stakes are 0U until LoL picks have a settled record at real lines - no slip is suggested until then",
                   "the model's side is not offered": "the pick's side is not offered on that line",
                   "the line offers one side only": "lines that offer one side only are not graded: Scout grades lines that offer both sides",
                   "offered sides not": "the feed does not list the pick's side as offered",
                   "rule not": "the platform's settlement rule for that prop is not on file",
                   "not offered as a slip leg": "that stat is not used in slips",
                   "line scope does not match": "the line's maps do not match the series format",
                   "no stored history": "no stored history for the player"}


def _lol_member_slips(sm):
    """Recommended LoL slips for members (legs by line key), or why there are none - from the worker's summary."""
    out = {}
    for pl, d in (sm.get("platforms") or {}).items():
        reasons = []
        for r, n in (d.get("blocked_by") or {}).items():
            text = next((v for k, v in LOL_SLIP_REASON.items() if r.startswith(k) or k in r), None)
            if text and text not in [x["text"] for x in reasons]:
                reasons.append({"text": text, "picks": n})
        n = d.get("upcoming_series", 0)
        if n == 0:
            reasons.append({"text": "no LoL match with lines is still to start", "picks": 0})
        elif n == 1:
            reasons.append({"text": "only one match with lines is still to start, and legs from the same match are never combined "
                                    "(there is no model of how their results move together)", "picks": d.get("upcoming_forecasts", 0)})
        out[LOL_PLATFORM.get(pl, pl)] = {
            "recommended": [{"size": sl.get("size"), "legs": [{"key": _lol_key(pl, lg.get("market_key")), "side": lg.get("side"),
                                                              "line": lg.get("line")} for lg in sl.get("legs") or []],
                             # no entry payout to members (owner rule: no payout / returns figures) - the chance stays
                             "model_chance_all_hit": sl.get("model_chance_all_hit")}
                            for sl in d.get("recommended") or []],
            "why_none": reasons, "upcoming_picks": d.get("upcoming_forecasts", 0)}
    return out


LOL_ONE_SIDED = {"over": "Over only", "under": "Under only"}


def _lol_one_sided(platform, sides):
    """lol/center/forecast.one_sided, read here without importing lol (owner 2026-11-06, os-1): "Over only" / "Under only"
    when the book offers that side only, else None. Underdog: one option listed. PrizePicks: exactly one side offered (a
    goblin / demon line with no allowed sides sent is More only). Such a line shows its line and price, never a grade."""
    sides = sides if isinstance(sides, dict) else {}
    if platform == "underdog":
        listed = [k for k in ("higher", "lower") if sides.get(k)]
        return LOL_ONE_SIDED["over" if listed[0] == "higher" else "under"] if len(listed) == 1 else None
    o, u = ((sides.get("higher") or {}).get("offered") is True), ((sides.get("lower") or {}).get("offered") is True)
    return LOL_ONE_SIDED["over"] if o and not u else (LOL_ONE_SIDED["under"] if u and not o else None)


def lol_one_sided_label(platform, sides, saved=None):
    """The label of a one-sided LoL line: the forecast's own (saved sides) or the current offer's - either one is enough."""
    return LOL_ONE_SIDED.get(saved) or _lol_one_sided(platform, sides)


LOL_ONE_SIDED_NOTE = "{}: Scout grades lines that offer both sides."


def _lol_side_offered(platform, sides, model_side):
    name = {"over": "Over", "under": "Under"}.get(model_side)
    return any(x["side"] == name and x["offered"] for x in _lol_sides(platform, sides))


def _lol_member_rebuild(path, mt):
    try:
        b = json.loads(path.read_text())
    except (OSError, ValueError):
        return _LOL_MEMBER["body"]
    rows, label = [], {}
    for x in b.get("rows") or []:                               # one match label per series, from both teams' names
        sid = (x.get("link") or {}).get("sid")
        if sid and x.get("team") and x.get("opponent"):
            label.setdefault(sid, " vs ".join(sorted([x["team"], x["opponent"]])))
    for x in b.get("rows") or []:
        f = x.get("forecast")
        if not f or not f.get("current") or x.get("market_type") != "single":
            continue
        inp = f.get("inputs") or {}
        side = "Over" if f.get("model_side") == "over" else "Under"
        sides = _lol_sides(x.get("platform"), x.get("sides"))
        pick_offered = next(s["offered"] for s in sides if s["side"] == side)
        lt, lt_note = _lol_line_type(x.get("platform"), x.get("odds_type"), inp.get("market"))
        rec = inp.get("recent") or {}
        one = lol_one_sided_label(x.get("platform"), x.get("sides"), f.get("one_sided"))
        rows.append({"key": _lol_key(x.get("platform"), x.get("market_key")),
                     "platform": x.get("platform"), "platform_label": LOL_PLATFORM.get(x.get("platform"), x.get("platform")),
                     "player": x.get("player"), "team": x.get("team"), "role": x.get("role"), "opponent": x.get("opponent"),
                     "event": x.get("competition"), "best_of": x.get("best_of"), "start": x.get("series_start"),
                     "match": label.get((x.get("link") or {}).get("sid")) or " vs ".join(sorted(filter(None, [x.get("team"), x.get("opponent")]))),
                     "stat": x.get("stat"), "stat_name": LOL_STAT.get(x.get("stat"), x.get("stat")), "scope": x.get("scope"),
                     "scope_games": LOL_SCOPE_GAMES.get(x.get("scope")), "line_type": lt, "line_type_note": lt_note,
                     "line": x.get("line"), "sides": sides, "projection": f.get("projection"), "median": inp.get("median"),
                     "range80": inp.get("range80"), "recent_games": rec.get("games"), "recent_per_game": rec.get("per_game"),
                     "p_over": f.get("p_over"), "p_under": f.get("p_under"), "p_push": f.get("p_push"),
                     "pick_side": side, "pick": f"{side} {x.get('line')}", "pick_offered": pick_offered,
                     # a side the line does not offer is the model's direction, never a pick: no letter grade
                     "grade": f.get("grade") if pick_offered else None, "units": 0.0, "stake_reason": LOL_STAKE_NOTE,
                     "line_seen": x.get("quote_seen_at"), "line_checked": x.get("quote_checked_at") or x.get("quote_seen_at"),
                     "forecast_at": f.get("at"), "drivers": inp.get("drivers") or [], "not_modelled": inp.get("unknowns") or [],
                     "model_version": str(f.get("model") or "").split(" ")[0] or None, "factors": inp.get("factors"), "team_win": inp.get("team_win")})
        if one:         # os-1: the line and its price only - no chance, projection, pick, letter or stake
            side_one = "Over" if one == LOL_ONE_SIDED["over"] else "Under"
            rows[-1].update(one_sided=one, pick_side=side_one, pick=f"{side_one} {x.get('line')}", pick_offered=False, grade=None,
                            units=None, stake_reason=LOL_ONE_SIDED_NOTE.format(one), p_over=None, p_under=None, p_push=None,
                            projection=None, median=None, range80=None, drivers=[], factors=None, model_version=None,
                            forecast_at=None)
        if PP_OPEN_LINK and x.get("platform") == "prizepicks":     # the PrizePicks projection id of the CURRENT offer (lineup link)
            rows[-1]["pp_projection_id"] = pplink.projection_id(x.get("line_id"))
        if PP_OPEN_LINK and x.get("platform") == "underdog":       # Underdog's link to this line's market screen (2026-10-29)
            lk_ = pplink.ud_link(x.get("ud_over_under_id"), x.get("ud_match_id"), x.get("ud_match_type"))
            if lk_:
                rows[-1]["ud_app_link"] = lk_
    tables = []
    for t in b.get("match_tables") or []:
        mk = [m for m in (t.get("markets") or {}).values() if m.get("market_type") == "single"]
        if not mk:
            continue
        teams = sorted({m.get("team") for m in mk if m.get("team")})
        markets = []
        for m in mk:
            lines = []
            for ln in m.get("lines") or []:
                lt, _ = _lol_line_type(ln.get("platform"), ln.get("odds_type"), ln.get("market"))
                one = lol_one_sided_label(ln.get("platform"), ln.get("sides"), ln.get("one_sided"))
                fc = ln.get("projection") is not None and ln.get("current") is not False and not one
                lines.append({"platform": LOL_PLATFORM.get(ln.get("platform"), ln.get("platform")), "line_type": lt, "line": ln.get("line"),
                              "sides": _lol_sides(ln.get("platform"), ln.get("sides")), "checked": ln.get("checked_at"),
                              "projection": ln.get("projection") if fc else None, "p_over": ln.get("p_over") if fc else None,
                              "p_under": ln.get("p_under") if fc else None,
                              "p_push": ln.get("p_push") if fc else None,
                              "grade": ln.get("grade") if fc and _lol_side_offered(ln.get("platform"), ln.get("sides"), ln.get("model_side")) else None,
                              "pick_side": ({"over": "Over", "under": "Under"}.get(ln.get("model_side")) if fc else None),
                              **({"one_sided": one} if one else {})})
            lines.sort(key=lambda z: (z["line"] if z["line"] is not None else 1e9, z["platform"]))
            markets.append({"player": m.get("player"), "team": m.get("team"), "role": m.get("role"), "stat": m.get("stat"),
                            "stat_name": LOL_STAT.get(m.get("stat"), m.get("stat")), "scope": m.get("scope"), "lines": lines})
        markets.sort(key=lambda z: (z["team"] or "", z["player"] or "", z["stat_name"] or ""))
        tables.append({"teams": label.get(t.get("sid")) or " vs ".join(teams), "event": t.get("competition"), "best_of": t.get("best_of"),
                       "start": t.get("start"), "markets": markets})
    tables.sort(key=lambda z: z["start"] or "")
    feeds = {LOL_PLATFORM.get(k, k): v for k, v in (b.get("feeds") or {}).items()}
    doc = {"at": b.get("at"), "feeds": feeds, "rows": rows, "match_tables": tables, "stake_note": LOL_STAKE_NOTE,
           "slips": _lol_member_slips(b.get("slips_member") or {})}
    body = json.dumps(letter_containment.lol_member_doc(doc)).encode()     # lc-1: held letters read Lean (display only)
    _LOL_MEMBER.update(mtime=mt, body=body)
    return body


def lol_center_supervise():
    """Keep one LoL data-center worker running (python -m lol.center.worker, low priority); restart it with backoff
    (1 min doubling to 30 min; reset after an hour up). LOL_DATA=0 starts nothing."""
    import subprocess
    if os.environ.get("LOL_DATA", "1") == "0":
        LOL_SUP["disabled"] = "LOL_DATA=0"
        return
    time.sleep(int(os.environ.get("LOL_START_DELAY", "90")))
    backoff = 60
    while True:
        cmd = [sys.executable, "-m", "lol.center.worker"]
        if os.name == "posix" and shutil.which("nice"):
            cmd = ["nice", "-n", "15"] + cmd
        t0, log = time.time(), LOL_CENTER / "worker.log"
        try:
            LOL_CENTER.mkdir(parents=True, exist_ok=True)
            if log.exists() and log.stat().st_size > 5_000_000:
                log.replace(log.with_suffix(".log.1"))
            with open(log, "ab") as fh:
                LOL_SUP["proc"] = subprocess.Popen(cmd, cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=fh,
                                                   creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) if os.name == "nt" else 0)
                LOL_SUP["starts"] += 1
                LOL_SUP["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                LOL_SUP["proc"].wait()
            LOL_SUP["last_exit"] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "code": LOL_SUP["proc"].returncode,
                                    "log_tail": log.read_bytes()[-600:].decode("utf-8", "replace") if log.exists() else ""}
        except Exception as e:
            LOL_SUP["last_exit"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        backoff = 60 if time.time() - t0 > 3600 else min(1800, backoff * 2)
        time.sleep(backoff)


def lol_center_admin(path, q):
    """Admin-only LoL views: the worker's files, and the quote archive (read-only, bounded pages)."""
    def read(name):
        try:
            return json.loads((LOL_CENTER / name).read_text())
        except (OSError, ValueError):
            return None
    if path == "/admin/lol/release":
        # the owner's release action (admin token): on=1 writes the flag on the data disk, on=0 removes it
        flag = LOL_CENTER / "public.flag"
        if q.get("on") == "1":
            LOL_CENTER.mkdir(parents=True, exist_ok=True)
            flag.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        elif q.get("on") == "0" and flag.exists():
            flag.unlink()
        _LOL_PUB["at"] = 0.0
        return {"public": lol_public(), "flag": flag.exists(), "env": os.environ.get("LOL_PUBLIC", "0") == "1"}
    if path == "/admin/lol/rebuild":
        # admin: ask the worker to rebuild + export from the stored source records at its next loop (no data changes)
        LOL_CENTER.mkdir(parents=True, exist_ok=True)
        (LOL_CENTER / "rebuild.flag").write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return {"requested": True}
    if path == "/admin/lol.json":
        p = LOL_SUP.get("proc")
        # names only (never values) of credential-like settings, to check a bot login was entered under the expected names
        names = sorted(k for k in os.environ if any(w in k.upper() for w in ("LEAGUEPEDIA", "LIQUIPEDIA", "FANDOM", "LOL_")))
        return {"released_to_members": lol_public(), "credential_env_names_present": names,
                "worker": {"running": bool(p and p.poll() is None), "starts": LOL_SUP["starts"],
                                                         "started_at": LOL_SUP["started_at"], "last_exit": LOL_SUP["last_exit"],
                                                         "disabled": LOL_SUP["disabled"]}, "status": read("status.json")}
    if path in ("/admin/lol/coverage.json", "/admin/lol/quarantine.json", "/admin/lol/links.json", "/admin/lol/board.json",
                "/admin/lol/learning.json", "/admin/lol/slips.json"):
        return read(path.rsplit("/", 1)[1]) or {"note": "not written yet"}
    if path == "/admin/lol/query.json":
        return lol_admin_query(q.get("db") or "center", q.get("sql") or "")
    # paged, read-only exports: the quote archive, the saved forecasts and settlements (the data center's database) and
    # the one LoL history (DATA_DIR/lol/history.db) - bounded pages, oldest first
    pages = {"/admin/lol/quotes.json": (LOL_CENTER / "lol.db", "market_events", "id",
                                        "id, sport, platform, market_key, event, observed_at, source_updated_at, line_id, player, team, "
                                        "opponent, match_source_id, start_time, stat_label, stat, scope, market_type, odds_type, line, "
                                        "sides, status, raw_id"),
             "/admin/lol/forecasts.json": (LOL_CENTER / "lol.db", "lol_forecasts", "id", "*"),
             "/admin/lol/settlements.json": (LOL_CENTER / "lol.db", "lol_settlements", "id", "*"),
             "/admin/lol/crosscheck.json": (LOL_CENTER / "lol.db", "crosscheck", "rowid", "rowid AS _row, *"),
             "/admin/lol/history.json": (Path(DATA_DIR) / "lol" / "history.db",
                                         "lol_games" if q.get("table") == "games" else "lol_player_games", "rowid", "rowid AS _row, *")}
    if path in pages:
        import sqlite3
        db, table, key, cols = pages[path]
        num = lambda k, d: int(q[k]) if (q.get(k) or "").isdigit() else d
        after, limit = num("after", 0), min(num("limit", 500), 5000)
        if not db.exists():
            return {"rows": [], "note": "not written yet"}
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            plat = q.get("platform") if table != "lol_player_games" and table != "lol_games" else None
            sql = f"SELECT {cols} FROM {table} WHERE {key} > ?" + (" AND platform = ?" if plat else "") + f" ORDER BY {key} LIMIT ?"
            rows = [dict(r) for r in con.execute(sql, (after, plat, limit) if plat else (after, limit))]
        except sqlite3.OperationalError as e:
            return {"rows": [], "note": f"not available yet ({str(e)[:80]})"}
        finally:
            con.close()
        if table == "market_events":
            for r in rows:
                r["sides"] = json.loads(r["sides"]) if r.get("sides") else None
        last = rows[-1].get("_row", rows[-1].get("id")) if rows else None
        return {"after": after, "rows": rows, "next_after": last if len(rows) == limit else None}
    return {"error": "unknown LoL view"}


_MEMPROF = {"base": None, "started": None}


def memprof(action):
    """Admin memory profiler (2026-10-18 out-of-memory hunt): action=start begins tracemalloc and takes a baseline;
    report = the 30 code lines whose retained memory grew most since the baseline + object counts by type;
    stop = ends tracing (its own overhead goes with it). Read-only for the app."""
    import gc
    import tracemalloc
    from collections import Counter
    if action == "start":
        if not tracemalloc.is_tracing():
            tracemalloc.start(8)
        _MEMPROF.update(base=tracemalloc.take_snapshot(), started=time.time())
        return {"tracing": True, "memory": _memory_report()}
    if action == "history":                       # the server's own minute samples (last 24 h) - read-only
        import membudget
        return {"memory": _memory_report(), "history": list(membudget.HISTORY)}
    if action == "exceptions":
        return {"memory": _memory_report(), **_scan_exceptions()}
    if action == "containers":
        return {"memory": _memory_report(), "containers": _scan_containers()}
    if action == "stop":
        tracemalloc.stop()
        _MEMPROF.update(base=None, started=None)
        return {"tracing": False}
    out = {"memory": _memory_report(), "tracing": tracemalloc.is_tracing()}
    if tracemalloc.is_tracing() and _MEMPROF["base"] is not None:
        snap = tracemalloc.take_snapshot()
        diff = snap.compare_to(_MEMPROF["base"], "traceback")
        out["since_s"] = round(time.time() - _MEMPROF["started"])
        out["traced_mb"] = round(tracemalloc.get_traced_memory()[0] / 1e6, 1)
        out["growth"] = [{"mb": round(d.size_diff / 1e6, 2), "count": d.count_diff,
                          "where": [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in d.traceback][:8]}
                         for d in diff[:30]]
    out["objects"] = Counter(type(o).__name__ for o in gc.get_objects()).most_common(20)
    return out


def _scan_exceptions():
    """Live futures and exception objects that keep tracebacks (and with them every frame's locals) alive: counts, the
    raising code lines, and what holds them."""
    import asyncio
    import concurrent.futures
    import gc
    from collections import Counter
    objs = gc.get_objects()
    futs = [o for o in objs if isinstance(o, (concurrent.futures.Future, asyncio.Future))]
    excs = [o for o in objs if isinstance(o, BaseException) and o.__traceback__ is not None]
    where, kinds = Counter(), Counter()
    for e in excs:
        tb = e.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        where[f"{tb.tb_frame.f_code.co_filename.rsplit('/', 1)[-1]}:{tb.tb_lineno}"] += 1
        kinds[f"{type(e).__name__}: {str(e)[:80]}"] += 1
    holders = Counter()
    for e in excs[:15]:
        for r in gc.get_referrers(e):
            if r is not objs and r is not excs:
                holders[type(r).__name__ + (f" {r.__class__.__module__}" if hasattr(r, "__class__") else "")] += 1
    fstate = Counter((type(f).__module__ + "." + type(f).__name__, getattr(f, "_state", "?"),
                      bool(getattr(f, "_exception", None))) for f in futs)
    return {"futures": [[*k, n] for k, n in fstate.most_common(10)], "exceptions_with_traceback": len(excs),
            "raised_at": where.most_common(10), "kinds": kinds.most_common(10), "held_by": holders.most_common(10)}


def _scan_containers(top=30):
    """Module-level containers of this app's own modules: entries and an estimated size (JSON length of a sample of
    items x entries x the measured Python/JSON ratio). Cheap - finds retained datasets without tracing."""
    import random
    import membudget
    base = str(BASE)
    out = []
    for mname, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", "") or ""
        if not f.startswith(base) or "site-packages" in f:
            continue
        cands = []
        for gname, v in list(vars(mod).items()):
            if gname.startswith("__"):
                continue
            cands.append((gname, v))
            d = getattr(v, "__dict__", None)              # one level into module-level objects (sources, clients, stores)
            if isinstance(d, dict) and not isinstance(v, type) and type(v).__name__ not in ("module", "function"):
                cands += [(f"{gname}.{a}", x) for a, x in list(d.items())]
            if isinstance(v, dict) and len(v) < 50:          # e.g. moneyline.SOURCES = {name: Source}
                for k2, x in list(v.items()):
                    d2 = getattr(x, "__dict__", None)
                    if isinstance(d2, dict) and not isinstance(x, type):
                        cands += [(f"{gname}[{k2}].{a}", y) for a, y in list(d2.items())]
        for gname, v in cands:
            if isinstance(v, membudget.BudgetLRU):
                st = v.stats()
                out.append({"name": f"{mname}.{gname}", "entries": st["entries"], "est_mb": st["mb"], "kind": "BudgetLRU"})
                continue
            if not isinstance(v, (dict, list, set, tuple)) or isinstance(v, type) or len(v) < 50:
                continue
            try:
                items = list(v.items()) if isinstance(v, dict) else list(v)
                sample = random.sample(items, min(40, len(items)))
                per = sum(membudget.json_cost(x) for x in sample) / max(len(sample), 1)
                out.append({"name": f"{mname}.{gname}", "entries": len(v), "est_mb": round(per * len(v) / 1e6, 1),
                            "kind": type(v).__name__})
            except Exception as e:
                out.append({"name": f"{mname}.{gname}", "entries": len(v), "error": type(e).__name__})
    return sorted(out, key=lambda r: -(r.get("est_mb") or 0))[:top]


def _memory_report():
    """The whole container as Render counts it (cgroup), this web process, the LoL worker, and the bounded caches."""
    import membudget
    out = membudget.process_memory()
    proc = LOL_SUP.get("proc")
    if proc is not None and proc.poll() is None:
        out["lol_worker_rss_mb"] = membudget.rss_of(proc.pid)
    out["allocator"] = dict(membudget.ALLOC)
    out["processes_mb"] = membudget.processes()
    out["sampled_peaks"] = dict(membudget.PEAK)            # highest minute samples since this process started
    out["history_minutes"] = len(membudget.HISTORY)       # /admin/memprof.json?action=history
    out["caches"] = {"bo3_finished": bo3._MEM.stats(), "bo3_expiring": bo3._TMEM.stats(), "contexts": cs2_scout._CONTEXT.stats(),
                     "results": {"entries": len(_RESULTS), "mb": round(_RES_BYTES[0] / 1e6, 1),
                                 "budget_mb": round(RESULTS_MAX_BYTES / 1e6)}}
    return out


def lol_admin_query(which, sql):
    """Admin audit view: one read-only SELECT on the LoL data center (center) or history database. Opened read-only, and
    an authorizer allows only reads (no ATTACH, PRAGMA writes or any change); 5000 rows, 15 s."""
    import sqlite3
    db = {"center": LOL_CENTER / "lol.db", "history": Path(DATA_DIR) / "lol" / "history.db"}.get(which)
    if db is None or not db.exists():
        return {"error": "unknown or missing database"}
    if not sql.lstrip().lower().startswith(("select", "with")):
        return {"error": "SELECT only"}
    ok = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.set_authorizer(lambda action, *a: sqlite3.SQLITE_OK if action in ok else sqlite3.SQLITE_DENY)
    t0 = time.time()
    con.set_progress_handler(lambda: 1 if time.time() - t0 > 15 else 0, 20000)
    try:
        cur = con.execute(sql)
        rows = [dict(r) for r in cur.fetchmany(5000)]
        return {"rows": rows, "truncated": len(rows) == 5000, "seconds": round(time.time() - t0, 2)}
    except sqlite3.Error as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    finally:
        con.close()


def start_research():
    """Boot (its own thread, after the grading threads): load matchid's tables, then start the moneyline refreshers.
    Each step in its own try - a failure here never touches grading, /health or the other step."""
    try:
        import matchid
        RESEARCH["matchid_ready"] = matchid.warm()
    except Exception as e:
        RESEARCH["errors"].append(f"matchid: {type(e).__name__}: {str(e)[:160]}")
    try:                                   # map veto (veto_serve, veto-1): DISPLAY + LOGGING only - its own background thread
        import veto_serve                  # builds the table in a child process, predicts board matches once per input
        veto_serve.BUSY = members_busy     # change; members only read the stored answer; it steps aside for a crowd
        RESEARCH["veto_started"] = veto_serve.start()
    except Exception as e:
        RESEARCH["errors"].append(f"veto: {type(e).__name__}: {str(e)[:160]}")
    try:                                   # Thunderpick partner odds: one poller, shared request budget (admin-only until
        import thunderpick                 # adaptive poller, matching, evidence-gated Thunderpick-first policy
        import market_odds
        thunderpick.ON_ACTIVATE = lambda: market_odds.compare(_tp_compare_rows())
        RESEARCH["thunderpick_started"] = thunderpick.start()
    except Exception as e:
        RESEARCH["errors"].append(f"thunderpick: {type(e).__name__}: {str(e)[:160]}")
    if os.environ.get("MONEYLINES", "1") != "1":       # MONEYLINES=0: refreshers off (the tab then shows "unavailable")
        return
    try:
        import moneyline
        RESEARCH["moneyline_started"] = moneyline.start()
        try:                                   # 0U side + HLTV reasoning per moneyline (ml_sides): one bulk read per minute
            import ml_sides
            ml_sides.start(moneyline.board)
            RESEARCH["sides_started"] = True
        except Exception as e:
            RESEARCH.setdefault("errors", []).append(f"ml_sides: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:
        RESEARCH["errors"].append(f"moneyline: {type(e).__name__}: {str(e)[:160]}")


_MODEL_STATE = {}


def validated_features(d):
    """Features that passed BOTH stages: promoted on development / validation (freeze.promoted) AND a frozen test-set
    result whose gain-vs-market CI lower bound is above zero. A validation-only promotion (no --final run yet, or a test
    CI touching zero) is not 'validated'."""
    st = (d or {}).get("staged") or {}
    test = st.get("test") or {}
    out = []
    for k in ((st.get("freeze") or {}).get("promoted") or []):
        ci = (test.get(k) or {}).get("gain_vs_market_ci") if isinstance(test.get(k), dict) else None
        if ci and ci[0] is not None and ci[0] > 0:
            out.append(k)
    return out


def ml_model_state():
    """Is there a walk-forward validated moneyline feature? Read from ml_eval_results.json once. No live model
    exists yet, so members get the market baseline and no model probability either way."""
    if not _MODEL_STATE:
        validated, val_promoted, at = [], [], None
        try:
            with open(BASE / "ml_eval_results.json", encoding="utf-8") as f:
                d = json.load(f)
            val_promoted = list((((d.get("staged") or {}).get("freeze") or {}).get("promoted")) or [])
            validated = validated_features(d)
            at = d.get("generated_at")
        except Exception:
            pass
        _MODEL_STATE.update(promoted=validated, val_promoted=val_promoted, evaluated_at=at, available=False,
                            probability=None,
                            label=MODEL_NOT_VALIDATED if not validated else
                            "Model: validated features exist but no live model is deployed - market baseline shown, no recommendation")
    return dict(_MODEL_STATE)


def ml_sides_for(board, tp_blocks=None):
    """The picked side per board match (memory only - the side picker refreshes in the background). tp_blocks:
    Thunderpick's block per match (moneyline.tp_block) -> the side's Thunderpick-first input (ml_sides "ml-sides-2")."""
    import ml_sides
    import moneyline
    if tp_blocks is None:
        return ml_sides.sides_for_board(board, detail=False)
    return ml_sides.sides_for_board(board, detail=False, tp_blocks=tp_blocks, tp_policy=moneyline.tp_policy())


# what the Moneylines tab says about Thunderpick's rule (members; the exchanges' own frozen rule is moneyline.BASELINE_RULE)
TP_RULE = ("Thunderpick (primary bookmaker source): a price is used only when the match is in Thunderpick's latest "
           "successful answer, that answer is at most {age} s old (75 min since 2026-10-16; our retrieval time - the feed publishes no quote "
           "time), the market is the event-level 'Match Winner' (read as the series winner from its label - two "
           "outcomes, no draw price; not provider-confirmed), the format is best-of-1, 3 or 5, and the match has not "
           "started (the feed has no live flag). Decimal odds d -> raw implied chance 1/d (the two add up to more than "
           "100% by the bookmaker margin) -> market-implied probability = each raw implied chance divided by their sum "
           "(proportional margin removal). Listed odds only: the feed gives no stake limits or execution confirmation.")
_TP_STATE_KEYS = ("source", "state", "label", "quotes_confirmed_at", "last_success_at", "last_nonempty_at", "time_basis",
                  "policy_active")


def member_source_note(note):
    """A Polymarket / Kalshi status note for members: no exception text, upstream messages or URLs - just what it means
    (2026-10-13 audit: members saw 'HTTPError: 429 Client Error: Too Many Requests for url: https://api...')."""
    if not note:
        return note
    s = str(note)

    def words(err):
        e = str(err or "")
        code = re.search(r"\b([45]\d\d)\b", e)
        if (code and code.group(1) == "429") or "Too Many Requests" in e:
            return "rate-limited by the provider"
        if code and code.group(1).startswith("5"):
            return "the provider returned an error"
        if re.search(r"timed? ?out|Timeout", e, re.I):
            return "the request timed out"
        if re.search(r"Connection|resolve|unreachable", e, re.I):
            return "connection problem"
        return f"HTTP {code.group(1)}" if code else "refresh failed"
    m = re.match(r"^(stale: \S+ refresh failing \()(.*)(\); last good data \d+s old)$", s, re.S)
    if m:
        return m.group(1) + words(m.group(2)) + m.group(3)
    m = re.match(r"^(\S+ has not loaded yet \()(.*)(\))$", s, re.S)
    if m:
        return m.group(1) + words(m.group(2)) + m.group(3)
    if re.search(r"https?://|Error|Exception|Traceback", s):
        return words(s)
    return s[:200]


_RAW_RE = re.compile(r"https?://|Traceback|host=|port=|\b\w+(Error|Exception)\b")


def _member_reason(s):
    """A moneyline block / baseline reason for members (2026-10-13 audit): a source note inside it ('stale: kalshi refresh
    failing (<raw error>); last good data 40s old') gets member_source_note's words; any other raw error text the same."""
    if not s or not _RAW_RE.search(str(s)):
        return s
    s = re.sub(r"stale: \S+ refresh failing \(.*?\); last good data \d+s old", lambda m: member_source_note(m.group(0)), str(s))
    return member_source_note(s) if _RAW_RE.search(s) else s


def _member_text(s, n=160):
    """Free text from a provider / reader shown to members: no URLs; anything that reads like an exception or a connection
    string becomes member_source_note's words."""
    s = re.sub(r"https?://\S+", "", str(s or "")).strip()
    if not s:
        return None
    return member_source_note(s) if _RAW_RE.search(s) else s[:n]


def moneylines_payload():
    """The member view of moneyline.board(): slimmed (no raw platform payloads / rules text), team A first.
    Thunderpick first (2026-10-13): one block per linked match (moneyline.tp_block - memory only), the SAME blocks feed
    the 0U side (ml-sides-2), the card, the usable-price filter and its count."""
    import moneyline
    st = moneyline.status()
    board = moneyline.board()
    tp_blocks = [moneyline.tp_block(e) if e.get("match_id") else None for e in board]
    try:
        sides = ml_sides_for(board, tp_blocks)
    except Exception:
        sides = [None] * len(board)
    tp_by_mid = {e["match_id"]: b for e, b in zip(board, tp_blocks) if e.get("match_id")}
    out = []
    for e, side in zip(board, sides):
        pl = {}
        for p, b in (e.get("platforms") or {}).items():
            if not b:
                pl[p] = None
                continue
            blk = {k: b.get(k) for k in _ML_BLOCK_KEYS}
            blk["url"] = b.get("url") if p == "polymarket" else None      # Kalshi's page URL form is unverified - not linked
            blk["reason"] = _member_reason(blk.get("reason"))
            pl[p] = blk
        base = dict(e.get("baseline") or {})
        base["reason"] = _member_reason(base.get("reason"))
        out.append({"match_id": e.get("match_id"), "link_status": e.get("link_status"), "link_reason": e.get("link_reason"),
                    "teams": {"a": (e.get("teams") or {}).get("a", {}).get("name"), "b": (e.get("teams") or {}).get("b", {}).get("name"),
                              "names_by_platform": dict((e.get("teams") or {}).get("names_by_platform") or {})},
                    "event": e.get("event"), "start": e.get("start"), "started": bool(e.get("started")),
                    "tie_possible": bool(e.get("tie_possible")), "bo": e.get("bo"), "scope": "series",
                    "platforms": pl,
                    "baseline": {k: base.get(k) for k in ("prob_a", "platform", "label", "reason", "model_version")},
                    "disagreement": bool(e.get("disagreement")), "disagreement_gap": e.get("disagreement_gap"),
                    "side": side})
    out = _merge_unlinked(out)
    for m in out:                           # Thunderpick first (2026-10-13): its block per linked match, team A view
        tb = tp_by_mid.get(m["match_id"]) if m.get("match_id") else None
        # a reader error is not "no event" (2026-10-13 review): the card says it could not be read; the side already fell
        # back with that same reason (the block is never eligible) and the forecast log skips that pass
        err = bool(tb and tb.get("availability") == moneyline.TP_READ_ERROR)
        m["thunderpick"] = None if err else tb
        # members can tell "no Thunderpick event" from "not looked up" (an unlinked card is never matched to Thunderpick)
        m["thunderpick_note"] = (None if m["thunderpick"] else
                                 f"Thunderpick: {moneyline.TP_STATE_LABEL[moneyline.TP_READ_ERROR].lower()}" if err else
                                 "Thunderpick: no event linked to this match" if m.get("match_id") else
                                 "Thunderpick: not checked - this card is not tied to one HLTV match")
    out.extend(_tp_only_matches({m.get("match_id") for m in out if m.get("match_id")}))
    tps = _thunderpick_state()
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches": out,
            "sources": {k: {x: (member_source_note(v.get(x)) if x == "note" else v.get(x))
                            for x in ("ok", "stale", "age_s", "last_success", "note", "listings")} for k, v in st.items()},
            "rule": moneyline.BASELINE_RULE, "model_version": moneyline.MODEL_VERSION,
            "thunderpick_rule": TP_RULE.format(age=tps.get("max_age_s") or 180),
            "model": {k: v for k, v in ml_model_state().items() if k != "val_promoted"},
            "recommendation": SHADOW_LABEL, "scope": "series", "thunderpick": tps}


def _tp_only_matches(seen_ids, now=None):
    """Matches Thunderpick lists that no exchange lists (2026-10-13 owner: Thunderpick is the primary bookmaker source on
    Moneylines): one card per CORRECTLY MATCHED Thunderpick event (canonical HLTV id, never ambiguous) that is not already
    on the board - team A = the canonical team A, its block from the same reader as every other card. No exchange
    platforms, no baseline and no 0U side (the side rules are defined on exchange prices); started matches left out."""
    try:
        import thunderpick, moneyline
        with thunderpick.POLLER.lock:
            recs = dict(thunderpick.POLLER.matched)
    except Exception:
        return []
    out = []
    for mid, rec in recs.items():
        if not mid or mid in seen_ids or not str(mid).startswith("hltv:") or rec.get("ambiguous"):
            continue
        e = rec.get("event") or {}
        start = rec.get("hltv_start") or rec.get("start_utc")
        bo = rec.get("hltv_bo")
        blk = moneyline.tp_block({"match_id": mid, "start": start, "bo": bo}, now)
        if not blk or blk.get("availability") in (moneyline.TP_READ_ERROR, "started") or blk.get("started"):
            continue
        a, b = (e.get("team2"), e.get("team1")) if rec.get("flipped") else (e.get("team1"), e.get("team2"))
        out.append({"match_id": mid, "link_status": "linked", "link_reason": "Thunderpick event matched to the HLTV match",
                    "teams": {"a": a, "b": b, "names_by_platform": {"thunderpick": {"a": a, "b": b}}},
                    "event": e.get("tournament") or e.get("competition"), "start": start, "started": False,
                    "tie_possible": bo == 2, "bo": bo, "scope": "series", "platforms": {"polymarket": None, "kalshi": None},
                    "baseline": {"prob_a": None, "platform": None, "label": None,
                                 "reason": "no exchange lists this match - Thunderpick only", "model_version": None},
                    "disagreement": False, "disagreement_gap": None, "side": None, "thunderpick_only": True,
                    "thunderpick": blk, "thunderpick_note": None})
    return out


def _tp_compare_rows(last=400):
    """Recent forecasts with their saved grader inputs + the pick's team / opponent / start (for market_odds.compare)."""
    fc = picklog.export_forecasts(inputs=True, last=last)
    picks = {p["id"]: p for p in picklog.export()}
    rows = []
    for f in fc:
        p = picks.get(f.get("pick_id"))
        if not p or not f.get("inputs"):
            continue
        rows.append({"forecast_id": f["id"], "inputs": f["inputs"], "team": p.get("team"), "opponent": p.get("opponent"),
                     "start": p.get("match_start"), "player": p.get("player")})
    return rows


def _tp_unmatched():
    """Admin coverage view: (1) current Thunderpick events that link to no canonical match, with the reason; (2) the
    open board matches whose projections fall back from Thunderpick, with the reason. Read-only, no upstream call."""
    import thunderpick as T
    p = T.POLLER
    tz = T.tz_state(p.db)
    off = tz.get("offset_h") if tz.get("resolved") else None
    with p.lock:
        matched = set(p.matched)
    evs, events_out = p.events(), []
    for e in evs:
        row = {"teams": [e.get("team1"), e.get("team2")], "start": e.get("start_raw"), "tournament": e.get("tournament"),
               "source_event_id": e.get("source_event_id")}
        if e.get("problem"):
            events_out.append(dict(row, why=f"invalid event: {e['problem']}"))
            continue
        m = T.match_event(e, off, p.canonical_fn)
        if m.get("match_id") in matched:
            continue
        events_out.append(dict(row, match_id=m.get("match_id"),
                               why=m.get("reason") or "ambiguous: two Thunderpick events link to this match"))
    # the board side comes from what the grader actually used (each open pick's latest saved provenance), not from the
    # pick's team label: picks.team was written from the player's BO3.gg PROFILE team (pb.current_team), which can differ
    # from the team he plays for in this match. ALLE (2026-10-13): BO3.gg profile 'UNO MILLE' (a separate HLTV team,
    # 11555 - his former team), while PrizePicks / Underdog list him under 'ex-Keyd Stars' and the grader graded him for
    # ex-Keyd Stars vs Galorys (playing_for) - so the label said 'UNO MILLE' for a match priced as ex-Keyd Stars.
    latest = {}
    for f in picklog.export_forecasts(inputs=True, last=4000):          # newest first
        latest.setdefault(f.get("pick_id"), f)
    by = {}
    for k in picklog.export():
        if k.get("status") != "open" or not k.get("team"):
            continue
        pv = ((latest.get(k["id"]) or {}).get("inputs") or {}).get("odds_provenance") or {}
        m = by.setdefault((k["team"], k.get("opponent"), k.get("match_start")), {"sources": {}, "why": {}})
        src = pv.get("selected") or ("no market" if pv else "no saved provenance")
        m["sources"][src] = m["sources"].get(src, 0) + 1
        if pv.get("fallback_reason"):
            m["why"][pv["fallback_reason"]] = m["why"].get(pv["fallback_reason"], 0) + 1
    board = [{"team": t, "opponent": o, "start": s, "latest_sources": v["sources"], "fallback_reasons": v["why"]}
             for (t, o, s), v in by.items() if "thunderpick" not in v["sources"]]
    return {"events": len(evs), "matched": len(matched), "unmatched_events": events_out,
            "open_matches": len(by), "open_matches_without_thunderpick": board,
            "basis": "board side = each open pick's latest saved forecast provenance (what the grader used)"}


_TP_ENDPOINT_KEYS = ("last_request_at", "last_request_outcome", "last_success_at", "last_nonempty_at", "latest_answer_empty",
                     "serving_snapshot_at", "held_through_empty_at", "restored_after_restart", "next_attempt", "current")


def tp_admin_view(samples=3):
    """Admin, read-only (never an upstream request): thunderpick.admin_view (status, request audit, policy, inventories)
    + a per-endpoint summary of the freshness fields (last request / outcome / success / nonempty answer / serving
    snapshot), the market-type registry (thunderpick.market_types) and exactly what members are told."""
    import thunderpick as T
    v = dict(T.admin_view(samples) or {})
    eps = ((v.get("status") or {}).get("endpoints") or {})
    v["endpoint_summary"] = {n: {k: e.get(k) for k in _TP_ENDPOINT_KEYS if k in e} for n, e in eps.items() if isinstance(e, dict)}
    if "market_types" not in v and hasattr(T, "market_types"):
        try:
            v["market_types"] = T.market_types()
        except Exception as e:      # noqa: BLE001 - the view still answers
            v["market_types"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    v["member_public_state"] = _thunderpick_state()
    v["probe_route"] = "/admin/thunderpick_probe?path=cs2|csgo - one request through the gateway (this view never sends one)"
    return v


def tp_probe(name):
    """The explicit admin probe: thunderpick.probe(name) (gateway budget, cooldown and in-flight lease shared with the
    poller); an older thunderpick.py without probe() -> the same single gateway request through admin_view's probe."""
    import thunderpick as T
    fn = getattr(T, "probe", None)
    out = fn(name) if fn is not None else (T.admin_view(0, probe=name) or {}).get("probe")
    return {"path": name, "result": out, "public_state": _thunderpick_state()}


def _tp_redact(obj):
    """Belt and braces for the Thunderpick admin views: the API key never leaves the server, whatever a view returns."""
    s = json.dumps(obj, default=str)
    try:
        import thunderpick
        k = thunderpick._key()
    except Exception:
        k = None
    if k and len(k) >= 6 and k in s:
        s = s.replace(k, "[redacted]")
    return s.encode()


def _thunderpick_state():
    """Thunderpick's honest state for the Moneylines tab (rate-limited / unavailable / setting up / empty / current) -
    its prices are not shown until the CS2 market is confirmed, and Polymarket / Kalshi are never relabelled as it.
    Only the member keys: quotes_confirmed_at = the retrieval time of the snapshot serving current quotes (never a max
    over an endpoint that answered empty - that overstated freshness), plus max_age_s (the quote limit)."""
    try:
        import thunderpick
        ps = thunderpick.public_state() or {}
        out = {k: ps.get(k) for k in _TP_STATE_KEYS if k in ps}
        out.setdefault("source", "thunderpick")
        out["max_age_s"] = getattr(thunderpick, "QUOTE_MAX_AGE_S", 180)
        if ps.get("state") == "ok" and ps.get("policy_active") is False:   # feed fine, prices not used yet - say so
            out["label"] = "Thunderpick odds retrieved - not used yet (its market's meaning is still being tested on BO3 prices)"
        return out
    except Exception as e:
        return {"source": "thunderpick", "state": "unavailable", "label": f"Thunderpick unavailable ({type(e).__name__})"}


def _norm_team():
    try:
        from matchid import norm
        return norm
    except Exception:
        return lambda x: re.sub(r"[^a-z0-9]+", "", str(x or "").lower())


def _oriented_prob(blk, name_a, name_b, norm):
    """A block's probability for the card's team A, read by the block's OWN team_a_name (an unlinked match may list
    the teams the other way round on the other exchange). None when the block's team can't be placed exactly."""
    if not blk or blk.get("prob_a") is None:
        return None
    own = norm(blk.get("team_a_name"))
    if own and own == norm(name_a):
        return blk["prob_a"]
    if own and own == norm(name_b):
        return round(1 - blk["prob_a"], 4)
    return None


def _merge_unlinked(rows):
    """Display only: an unlinked match listed on BOTH exchanges arrives as two one-platform entries (moneyline.py never
    joins what it can't link). Show it once when the two entries name exactly the same two teams (normalised: case,
    accents, punctuation - never fuzzy) and start within 3 h. It stays unlinked: no match id, never a baseline. Each
    platform block keeps its own team_a_name, so a reversed team order is still read correctly.
    The merged card (merged_unlinked) compares the two prices only after orienting both to the card's team A by each
    block's own team_a_name, and only when both quotes are usable; otherwise disagreement_gap stays None (the page then
    says the prices were not compared - never 'agree')."""
    norm = _norm_team()
    keyed, out = {}, []
    for m in rows:
        plats = [p for p, b in m["platforms"].items() if b]
        if m["match_id"] or len(plats) != 1:
            out.append(m)
            continue
        names = frozenset((norm(m["teams"]["a"]), norm(m["teams"]["b"])))
        st = comparison._ts(m.get("start"))
        twin = next((x for x in keyed.get(names, []) if not x["platforms"].get(plats[0]) and st and comparison._ts(x.get("start"))
                     and abs((comparison._ts(x["start"]) - st).total_seconds()) <= 3 * 3600), None)
        if twin is None or len(names) != 2:
            keyed.setdefault(names, []).append(m)
            out.append(m)
            continue
        twin["platforms"][plats[0]] = m["platforms"][plats[0]]
        twin["teams"]["names_by_platform"].update(m["teams"]["names_by_platform"])
        # the agreement fields were computed when the twin had ONE platform: never carry them over
        twin.update(merged_unlinked=True, disagreement=False, disagreement_gap=None)
        blks = [b for b in twin["platforms"].values() if b]
        if len(blks) == 2 and all(b.get("usable") for b in blks):
            ps = [_oriented_prob(b, twin["teams"]["a"], twin["teams"]["b"], norm) for b in blks]
            if None not in ps:
                gap = round(abs(ps[0] - ps[1]), 4)
                twin.update(disagreement_gap=gap, disagreement=gap >= moneyline_disagree() - 1e-9)
    return out


def moneyline_disagree():
    try:
        import moneyline
        return moneyline.DISAGREE
    except Exception:
        return 0.05


_GB = {}                                                      # book -> (built_at, board_payload)
_GB_LOCKS = {"underdog": threading.Lock(), "prizepicks": threading.Lock()}


def graded_board(book, max_age=5.0):
    """board_payload(book), rebuilt by ONE thread at most every `max_age` seconds; everyone else gets that copy (700
    Build presses = one board rebuild, not 700)."""
    hit = _GB.get(book)
    if hit and time.time() - hit[0] <= max_age:
        return hit[1]
    with _GB_LOCKS[book]:
        hit = _GB.get(book)
        if not hit or time.time() - hit[0] > max_age:
            hit = _GB[book] = (time.time(), board_payload(book))
            _closing_observe(book, hit[1])
    return hit[1]


_CL_MOVED = {}                                                # book -> graded rows whose feed line moved (latest board build)
_CL_ID_KEYS = ("nick", "team_name", "opp_name", "match_slug", "settle_slug", "match_start")


def _closing_moved(moved, r, g, book):
    """SCOUT_CLOSING_LINES only: a graded row hidden while its moved line is regraded still reports the platform's CURRENT
    line to the closing-line store (a late move is never saved as the old line). Never shown, never graded."""
    if moved is None:
        return
    try:
        from slip_builder import lock_iso
        x = dict(r, **{k: g.get(k) for k in _CL_ID_KEYS})
        x["book"] = x.get("book") or book
        x["scope"] = r.get("scope") or "12"
        x["match_id"] = _row_match_id(x)
        x["lock_at"] = lock_iso(x)
        moved.append(x)
    except Exception:
        pass


def _closing_observe(book, payload):
    """SCOUT_CLOSING_LINES (owner flag, default off): each fresh board copy's pre-lock lines; the last one before lock is saved
    once (closing_lines.py, append-only). Display / diagnostics data only, never a grade input; never breaks the board."""
    if os.environ.get("SCOUT_CLOSING_LINES") != "1":
        return
    try:
        import closing_lines
        closing_lines.observe(book, list((payload or {}).get("rows") or []) + list(_CL_MOVED.get(book) or []))
    except Exception:
        pass


_SETTLED = [0.0, None]


def studio_track_record():
    """The studio's honest note, each count under its OWN builder tag (cached 5 min):
    studio_settled = settled batches of the studio builder (slip_builder.VERSION) - 0 until a studio settler exists
    (slip_batches only ever settles its own shadow builder); shadow_settled = slip_batches' shadow builder
    (slip_batches.VERSION, a different algorithm) - never presented as the studio's record."""
    if _SETTLED[1] is None or time.time() - _SETTLED[0] > 300:
        import slip_builder
        rec = {"builder": slip_builder.VERSION, "studio_settled": 0,
               "shadow_builder": getattr(slip_batches, "VERSION", None), "shadow_settled": 0}
        try:
            res = slip_batches.results()
            rec["shadow_settled"] = len(res)
            rec["studio_settled"] = sum(1 for r in res if isinstance(r, dict) and r.get("version") == slip_builder.VERSION)
        except Exception:
            pass
        _SETTLED[:] = [time.time(), rec]
    return dict(_SETTLED[1])


def settled_batches():
    """Settled batches of the STUDIO builder (studio-1) only - see studio_track_record."""
    return studio_track_record()["studio_settled"]


_STUDIO_HITS, _STUDIO_EV_HITS, _STUDIO_GUARD = {}, {}, threading.Lock()
_SLIP_LOG_HITS = {}                                   # /api/slip_log: its own per-member bucket (studio_rate_ok)
_MC_HITS = {}                                         # /api/match_compare: its own per-member bucket (audit 20)
_SEARCH_BO3_HITS = {}                                 # /api/search BO3.gg fallback: its own per-member bucket (audit 2026-10-26)


def match_compare_problem(q):
    """Why a /api/match_compare query is refused, or None (audit 20: every value is part of the cache key and of the
    projection keys, so a member looping start dates or names made fresh HLTV queries and cache entries each time)."""
    if (q.get("maps") or "12") not in ("1", "12", "123"):
        return "bad maps"
    if any(len(q.get(k) or "") > MC_TEAM_MAX for k in ("team", "opponent", "team_slug", "opp_slug")):
        return "name too long"
    st = comparison._ts(q.get("start"))
    if st is None:
        return None                                   # no usable start: projected as today (cs2_scout.match_day)
    if st.tzinfo is None:
        st = st.replace(tzinfo=timezone.utc)
    if st > datetime.now(timezone.utc) + timedelta(days=MC_START_AHEAD_D):   # each future day = new projection keys
        return "start out of range"
    return None


def studio_rate_ok(member, idem=None, bucket=None, limit=None):
    """Per-member sliding 60 s window. A request repeating an idem key already counted in the window is free (a retry of
    the same press - studioCall re-sends 502/503/504 and the 409/404 path re-sends once); a new key is counted only when
    it passes. bucket / limit: the verify + event bucket (_STUDIO_EV_HITS, STUDIO_EVENTS_PER_MIN) is separate."""
    d = _STUDIO_HITS if bucket is None else bucket
    limit = STUDIO_BUILDS_PER_MIN if limit is None else limit
    now = time.time()
    with _STUDIO_GUARD:
        if len(d) > 50000:
            d.clear()
        hits = [(t, k) for t, k in d.get(member, []) if now - t < 60]
        if idem is not None and any(k == idem for _, k in hits):
            d[member] = hits
            return True
        ok = len(hits) < limit
        if ok:
            hits.append((now, idem))
        d[member] = hits
        return ok


def _no_json_constant(c):
    raise ValueError(f"{c} is not allowed")


_IDEM_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,64}$")
_BATCH_RE = re.compile(r"^[0-9a-f]{12}-\d{1,9}$")
_SLIP_RE = re.compile(r"^[0-9a-f]{12}-\d{1,9}/b\d{1,9}/s\d{1,4}$")
_SETTING_KEYS = {"book", "side", "stat", "scope", "market", "sizes", "count", "counts", "overlap", "max_uses", "fill",
                 "must_include"}
_ROW_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")    # a board row id (Underdog line uuid, "pp:<line id>")


def studio_settings(s):
    """Member settings -> slip_builder settings, bounded. ValueError (-> 400) on anything outside the studio's choices."""
    if not isinstance(s, dict) or set(s) - _SETTING_KEYS:
        raise ValueError("settings must be an object with: " + ", ".join(sorted(_SETTING_KEYS)))
    for k in ("book", "side", "stat", "scope", "market", "overlap", "fill"):
        if k in s and not isinstance(s[k], str):
            raise ValueError(f"{k} must be text")
    if str(s.get("market") or "standard").lower() == "moneyline":
        raise ValueError("moneylines are not built into slips (shadow only)")
    sizes = s.get("sizes", [2, 3, 4])
    if not isinstance(sizes, list) or not 1 <= len(sizes) <= 3:
        raise ValueError("sizes must be a list of 2, 3 and/or 4")
    if not all(isinstance(n, int) and not isinstance(n, bool) and n in (2, 3, 4) for n in sizes):
        raise ValueError("sizes must be a list of 2, 3 and/or 4")          # floats, Infinity, NaN, bools: 400, never 500
    for k in ("count",):
        if k in s and not (isinstance(s[k], int) and not isinstance(s[k], bool) and 1 <= s[k] <= 10):
            raise ValueError("count must be 1-10")
    if "counts" in s:
        c = s["counts"]
        if not isinstance(c, dict) or len(c) > 3 or not all(str(k) in ("2", "3", "4") and isinstance(v, int)
                                                            and not isinstance(v, bool) and 1 <= v <= 10 for k, v in c.items()):
            raise ValueError("counts must map 2/3/4 to 1-10")
    if "max_uses" in s:
        m = s["max_uses"]
        if not isinstance(m, dict) or set(m) - {"player", "match"} or not all(
                isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 3 for v in m.values()):
            raise ValueError("max_uses must be {player, match} with values 1-3")
    if "must_include" in s:                              # "build around": up to 2 board row ids (U7 shared contract)
        m = s["must_include"]
        if not isinstance(m, list) or len(m) > 2 or not all(isinstance(x, str) and _ROW_ID_RE.match(x) for x in m):
            raise ValueError("must_include must be a list of up to 2 board row ids")
    import slip_builder
    return slip_builder.normalize_settings(s)


_SNAPC = {"at": 0.0, "data": None}


def _snapshot_counts():
    """ml_snapshots.db counts (read-only connection, cached 10 min). Never a full scan of ml_quotes: the total is its
    largest rowid (rows are never deleted, so it equals COUNT(*)), per-platform figures cover the last 24 h through the
    received_ts index; ml_forecasts is small (one row per match per change / 30 min)."""
    if _SNAPC["data"] is not None and time.time() - _SNAPC["at"] < 600:
        return _SNAPC["data"]
    import sqlite3
    import moneyline
    p = moneyline.db_path()
    if not p.exists():
        out = {"exists": False}
    else:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
            total = con.execute("SELECT MAX(rowid) FROM ml_quotes").fetchone()[0] or 0
            quotes = {pl: {"rows_24h": n, "usable_24h": int(u or 0), "linked_matches_24h": m, "last_received": last}
                      for pl, n, u, m, last in con.execute(
                          "SELECT platform, COUNT(*), SUM(usable), COUNT(DISTINCT match_id), MAX(received_ts) FROM ml_quotes "
                          "WHERE received_ts >= ? GROUP BY platform", (since,))}
            n, mids, avail, last = con.execute("SELECT COUNT(*), COUNT(DISTINCT match_id), SUM(available), MAX(logged_ts) "
                                               "FROM ml_forecasts").fetchone()
            out = {"exists": True, "size_mb": round(moneyline._store_mb(), 2), "cap_mb": moneyline.SNAPSHOT_MAX_MB,
                   "quote_rows_total": total, "quotes": quotes,
                   "forecasts": {"rows": n, "matches": mids, "available": int(avail or 0), "last_logged": last}}
        finally:
            con.close()
    _SNAPC.update(at=time.time(), data=out)
    return out


def moneylines_status():
    """Admin: each source's health, the snapshot store, and how well markets link to canonical matches."""
    out = {"research": RESEARCH, "model": ml_model_state()}
    try:
        import moneyline
        out["sources"] = moneyline.status()
        b = moneyline.board()
        both = [m for m in b if m["platforms"].get("polymarket") and m["platforms"].get("kalshi")]
        out["links"] = {
            "matches": len(b), "linked": sum(1 for m in b if m["match_id"]),
            "unlinked": sum(1 for m in b if not m["match_id"]),
            "by_id_source": dict(collections.Counter(str(m["match_id"]).split(":")[0] for m in b if m["match_id"])),
            "on_both_platforms": len(both),
            "usable": {p: sum(1 for m in b if (m["platforms"].get(p) or {}).get("usable")) for p in moneyline.PLATFORMS},
            "usable_on_both": sum(1 for m in both if m["platforms"]["polymarket"].get("usable") and m["platforms"]["kalshi"].get("usable")),
            "baselines": dict(collections.Counter((m["baseline"] or {}).get("platform") or "unavailable" for m in b)),
            "disagreements": sum(1 for m in b if m["disagreement"]),
            "coherence_flags": sum(1 for m in b if (m["platforms"].get("kalshi") or {}).get("coherence_flag")),
            "unlinked_examples": [f"{m['teams']['a']['name']} vs {m['teams']['b']['name']}: {m.get('link_reason')}"
                                  for m in b if not m["match_id"]][:15]}
    except Exception as e:
        out["error"] = f"moneyline: {type(e).__name__}: {str(e)[:200]}"
    try:
        out["snapshots"] = _snapshot_counts()
    except Exception as e:
        out["snapshots"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        import matchid
        out["matchid"] = {"ready": matchid.ready(), "sources": matchid.status(), "store": matchid.link_stats()}
    except Exception as e:
        out["matchid"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return out


_MLE = {"at": 0.0, "forward": None, "error": None, "busy": False, "took_s": None}
_MLE_LOCK = threading.Lock()
_MLE_SUMMARY = {}


def _ml_eval_forward():
    t0 = time.time()
    try:
        import ml_eval
        import moneyline
        path = str(moneyline.db_path())
        # light copy (teams + results, no player rows) covering EVERY snapshot match: from the oldest snapshot start
        # (- 2 days) plus each linked hltv:<id>, so the cumulative cohort never shrinks to a rolling 60-day window
        oldest, ids = ml_eval.forward_span(path)
        since = (datetime.fromtimestamp(oldest, timezone.utc).date() - timedelta(days=2)) if oldest else None
        hl = ml_eval.load_hltv_recent(60, since=since, matchids=ids)
        _MLE.update(forward=ml_eval.forward_eval(path, hl), error=None)
    except Exception as e:
        _MLE["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        _MLE.update(at=time.time(), busy=False, took_s=round(time.time() - t0, 1))


def ml_eval_admin(refresh=False):
    """Admin: the frozen historical evaluation (ml_eval_results.json without its per-match rows) plus the forward cohort
    from the live snapshots, recomputed in a background thread at most every 30 min (or on ?refresh=1)."""
    if not _MLE_SUMMARY:
        try:
            with open(BASE / "ml_eval_results.json", encoding="utf-8") as f:
                d = json.load(f)
            _MLE_SUMMARY.update({k: v for k, v in d.items() if k != "polymarket_rows"},
                                polymarket_rows_n=len(d.get("polymarket_rows") or []))
        except Exception as e:
            _MLE_SUMMARY["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    with _MLE_LOCK:
        if not _MLE["busy"] and (refresh or time.time() - _MLE["at"] > 1800):
            _MLE["busy"] = True
            threading.Thread(target=_ml_eval_forward, daemon=True, name="ml-eval-forward").start()
    return {"results": _MLE_SUMMARY, "model": ml_model_state(),
            "forward": {"computing": _MLE["busy"], "computed_at": datetime.fromtimestamp(_MLE["at"], timezone.utc).isoformat(timespec="seconds")
                        if _MLE["at"] else None, "took_s": _MLE["took_s"], "error": _MLE["error"], "result": _MLE["forward"]}}


REQ_TIMES = collections.deque(maxlen=300)
SEARCH_TIMES = collections.deque(maxlen=3000)   # server-side seconds per search: request received -> response sent
MEMBER_GRADES = [0]     # member grades running right now (the board steps aside while any are)
_MG_LOCK = threading.Lock()
_BOARD_BUSY = threading.Lock()   # while members are grading, only one board grade runs at a time


def members_idle(max_wait=45):
    """Wait until no member grade is running (at most `max_wait` - then one board grade goes
    through, so the board never freezes during a rush). Quiet site: no wait at all."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        if MEMBER_GRADES[0] <= 0:
            return
        time.sleep(2)


BOARD_RESCAN_S = 60                # board pass: re-read the feeds for moved / new lines this often (audit 21)


def current_feed_row(r):
    """The feed's current row with r's id (in memory, no network), tagged with that feed's fetch time and read meta - the
    snapshot this grade is actually made from (audit 21: the quote said the current fetch time for an older line). None
    when the line is no longer listed."""
    bk = r.get("book") or "underdog"
    try:
        b = prizepicks.board() if bk == "prizepicks" else underdog.board()
    except Exception:
        return r                                      # the feed cache is unreadable: grade the pass's own snapshot
    cur = next((x for x in b["rows"] if x.get("id") == r.get("id")), None)
    if cur is None:
        return None
    at = b.get("at")
    return dict(cur, _board_at=(datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds")
                                if isinstance(at, (int, float)) and at else (at or None)),
                _feed_meta=feed_meta(bk, b))


_SLIP_SETTLED = [0.0]
_DIAG = {}
_DIAG_LOCK = threading.Lock()       # one diagnostics rebuild at a time (a second admin tab waits, never builds twice)


def board_gradable(feeds, now_ts=None):
    """The board loop's per-row gate (audit 2026-11-05 B1): r -> may this row be (re)graded now. A feed with no good
    read for FEED_GRADE_MAX_S (or none at all) has its lines skipped, so they keep their last BOARD grade (transient
    failures never change grades); the other feed keeps grading. Underdog alternates stay ungraded."""
    current = {bk_: feed_current(fb_, now_ts) for bk_, fb_ in feeds.items()}

    def gradable(r, now_):
        return ((not r["alt"] or r.get("book") == "prizepicks") and (not r.get("start") or r["start"] > now_)
                and current.get(r.get("book") or "underdog", True))
    return gradable


def board_forever():
    """Grade every Underdog CS2 line in the background (gives way to members), so the Board tab is
    instant. Each graded line is also logged (test=2: not a member's pick) so it settles and the
    grader can be measured on a large, unbiased set of real lines."""
    _board_load()                                     # last grades show right away after a restart
    time.sleep(20)
    while True:
        try:
            # both platforms' lines, each graded at its own line and market type with the same pipeline (same
            # projection for the same player / match / stat / maps and model version; only the line differs)
            feeds = {"underdog": underdog.board(wait=True), "prizepicks": prizepicks.board(wait=True)}
            if os.environ.get("SCOUT_CLOSING_LINES") == "1":  # closing lines are captured with no member online too
                for bk_ in ("underdog", "prizepicks"):
                    try:
                        graded_board(bk_)
                    except Exception:
                        pass
            pass_meta = {bk_: feed_meta(bk_, fb_) for bk_, fb_ in feeds.items()}     # pl-2 anchors: the graded snapshot
            rows = feeds["underdog"]["rows"] + feeds["prizepicks"]["rows"]
            now = datetime.now(timezone.utc).isoformat()
            # Underdog alternates stay ungraded (as before); PrizePicks goblin / demon are their own markets and are graded
            # a line that moved since its grade is regraded at once (its grade was for another line - 2026-10-11 audit)
            # audit 55: so is a line whose platform now offers the Under it was graded without
            moved = lambda r: (((BOARD.get(r["id"]) or {}).get("graded_line") is not None
                                and float(BOARD[r["id"]]["graded_line"]) != float(r.get("line") or -1))
                               or side_opened(BOARD.get(r["id"]), r))
            # audit 2026-11-05 B1: lines of a feed with no good read for FEED_GRADE_MAX_S are not graded as current (last grade kept)
            gradable = board_gradable(feeds)
            todo = [r for r in rows if gradable(r, now)
                    and (time.time() - (BOARD.get(r["id"]) or {}).get("at", 0) > BOARD_EVERY or moved(r))]
            # lines from an older model version first, then soonest matches; one match's lines and each player's kills /
            # headshots back to back (the second grade of a player is served from his warm data)
            stale = lambda r: (BOARD.get(r["id"]) or {}).get("graded_model") != picklog.MODEL_VERSION
            todo.sort(key=lambda r: (not moved(r), not stale(r), r.get("start") or "9", str(r.get("matchup") or ""),
                                     str(r.get("player") or "").lower(), r.get("prop") or ""))

            def one(r):
                members_idle()                            # members first, but the board never stalls
                # audit 21: the row as the feed has it NOW (the pass's snapshot can be minutes or hours old while members
                # grade): graded at its current line / sides / start, skipped when it left the feed or its match started
                r = current_feed_row(r)
                if r is None or not gradable(r, datetime.now(timezone.utc).isoformat()):
                    return
                # PrizePicks goblin / demon are their own markets (their sides: the feed's, prizepicks.directions); promo and
                # standard grade as standard
                lt = r.get("odds_type") if r.get("book") == "prizepicks" and r.get("odds_type") in ("goblin", "demon") else "standard"
                try:
                    if MEMBER_GRADES[0] > 0:              # members still busy: one board grade at a time
                        with _BOARD_BUSY:
                            res = cs2_scout.grade(r["player"], r["line"], r["prop"], lt, scope=r.get("scope") or "12", team_hint=r.get("team"), opponent=r.get("opponent") or None,
                                                  match_start=r.get("start"), faceit_key=faceit_key(), background=True, light=True,
                                                  less_available=r.get("under_available"))
                    else:
                        res = cs2_scout.grade(r["player"], r["line"], r["prop"], lt, scope=r.get("scope") or "12", team_hint=r.get("team"), opponent=r.get("opponent") or None, faceit_key=faceit_key(),
                                              match_start=r.get("start"), background=True, light=True,
                                              less_available=r.get("under_available"))
                    if res.get("error"):
                        old = BOARD.get(r["id"]) or {}
                        if old.get("side") and (old.get("nf_retries") or 0) < 3:   # a graded line is not dropped on one failed
                            BOARD[r["id"]] = dict(old, at=time.time() - BOARD_EVERY + HLTV_RETRY, nf_retries=(old.get("nf_retries") or 0) + 1)
                            return                                                 # context build (BO3.gg blip): tried again soon
                        BOARD[r["id"]] = {"at": time.time(), "state": "NOT FOUND", "reason": res["error"]}
                        return
                    v = verdict(res.get("grade"))
                    g = res.get("grade") or {}
                    kept = keep_last_hltv_grade(BOARD.get(r["id"]), g, r)
                    if kept:
                        BOARD[r["id"]] = kept
                        return
                    if grade_transient(g):                # nothing to keep: the line waits (hidden) and is tried again soon -
                        BOARD[r["id"]] = outage_placeholder(BOARD.get(r["id"]), r, g=g)   # never an old-pipeline / fallback-odds grade for a transient reason
                        return
                    pb = res.get("player_block") or {}
                    if (r.get("scope") or "12") == "123":   # display only: his Maps 1-3 record (never a grade input)
                        try:
                            pb["recent_form_123"] = records.maps123_record(pb, r["line"], "headshots" if r["prop"] == "hs" else "kills")
                        except Exception:
                            traceback.print_exc()
                    rf = comparison.record_for_scope(pb, r.get("scope"))   # the line's own maps - never Maps 1-2 for Map 1
                    fw = ((pb.get("overall") or {}).get("form_windows") or {}).get("3m") or {}
                    BOARD[r["id"]] = {"at": time.time(), "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                      "player_id": (pb.get("player") or {}).get("id"),     # BO3.gg id: Underdog -> player map
                                      "match_start": (res.get("next_match") or {}).get("start"),   # comparison audit
                                      # match headers + compact rows (UI 2026-10-09)
                                      "event": (res.get("next_match") or {}).get("event"), "bo": (res.get("next_match") or {}).get("bo"),
                                      "maps_known": ((res.get("next_match") or {}).get("maps") or [])[:3],
                                      "dstat": comparison.data_status(res.get("grade") or {}, v),
                                      "source": comparison.source_label(res.get("grade") or {}),
                                      # why the stake is below what its chance earns + any member warning (display only)
                                      "cap_grade": stake_cap_reasons(g, v, source=odds_selected(res.get("odds"))), "warn": has_warning(g),
                                      "graded_line": r["line"], "graded_model": picklog.MODEL_VERSION,   # slips: exact line + model
                                      "grade": v.get("grade"), "side": v.get("side"), "p": v.get("p"),
                                      "model_outcomes": copy.deepcopy(g.get("probs_raw")),
                                      # the exact distribution behind this grade (grader.py "dist", display only, server side: never
                                      # in a member payload - protect_recipe): the workspace reads another book's line from it
                                      # (line_chance.cs2_at), never a new model or a shrink
                                      "dist": line_chance.compact_dist(g.get("dist")),
                                      # performance-model projection: no line-dependent gap-trust shift or margin, so another
                                      # line is a plain read of the same distribution (audit follow-up B1, line_chance.cs2_at)
                                      "perf_on": not str(g.get("projection_source") or "legacy").startswith("legacy"),
                                      "units": v.get("units"), "proj": g.get("lambda_final"), "state": v.get("state"),
                                      "reason": v.get("reason"), "nick": (pb.get("player") or {}).get("nick"),
                                      # projection minus the line (the cut the grade uses since the 2026-10-07 continuity fix)
                                      "edge": round(g["lambda_final"] - r["line"], 2) if g.get("lambda_final") is not None else None,
                                      **_board_l10(rf, r),
                                      "hs_pct": fw.get("hs_pct"), "p_win": (g.get("detail") or {}).get("p_win"),
                                      "snip": pb.get("sniping_equiv"), "awp_note": pb.get("awp_note"),
                                      "own_teams": [t for t in (pb.get("profile_team"), pb.get("current_team")) if t],
                                      "market": g.get("market_quality"),
                                      "team_name": pb.get("playing_for") or pb.get("current_team"),
                                      "opp_name": (res.get("next_match") or {}).get("opponent"),
                                      "match_slug": (res.get("next_match") or {}).get("slug"),   # exact match id (settling slips)
                                      # the pick log's match key (picklog.pick_row: a match only HLTV has is logged as
                                      # hltv-<HLTV match id>). 2026-10-21 bug: Studio legs on such matches carried slug None
                                      # and never settled. Kept apart from match_slug, whose None still means "match by the
                                      # start time" for the card stake / anchor lookups (unchanged).
                                      "settle_slug": _settle_slug(res.get("next_match") or {}),
                                      "tonight": (pb.get("_env") or {}).get("tonight"),   # LAN / online (match table projections)
                                      "book": r.get("book") or "underdog", "line_type": lt,
                                      # the sides the feed offered when this grade was made (display: "no longer offered")
                                      "graded_sides": {"under": r.get("under_available"), "over": r.get("over_available")},
                                      # display gd-1 (owner 2026-10-15): model grade / stake reason / availability, separate
                                      **grade_display.annotate(g, v)}
                    res["verdict"] = v
                    try:                                  # immutable comparison snapshot (changes only) + the row's evidence
                        ub = underdog.board()
                        pnl = comparison.panel(res, ub["rows"] + prizepicks.board()["rows"], board_at=ub.get("at"), id_map=ud_id_map(ub["rows"]))
                        if not grade_transient(g):            # an outage grade is no evidence of a changed forecast
                            comparison.snapshot(res, pnl, picklog.MODEL_VERSION)
                        if pnl:
                            BOARD[r["id"]].update(support=(pnl["supports"] or [None])[0], concern=pnl["concern"])
                    except Exception:
                        traceback.print_exc()
                    # the platform and the provider's market id travel with the logged forecast (one row per platform)
                    res["book"] = {"book": r.get("book") or "underdog", "market_id": r.get("market_id") or r.get("id"),
                                   "market": r.get("market"), "odds_type": lt,
                                   # 2026-10-23: the platform's listed start for this line (picklog schedule_obs - the
                                   # original scheduled start and any re-listing; the reschedule rule's evidence)
                                   "start": r.get("start"),
                                   # 2026-10-15 measurement only (never a grade input): the quote this forecast was graded
                                   # on - per-side prices / payouts, the feed's own market type (PrizePicks 'promo' = a
                                   # boosted standard line), the sides offered, the provider's update time and the board's
                                   # fetch time - so a line's use as a market anchor can be checked against its pricing
                                   "quote": {"price_over": r.get("price_higher"), "price_under": r.get("price_lower"),
                                             "payout_over": r.get("higher"), "payout_under": r.get("lower"),
                                             "feed_odds_type": r.get("odds_type") or ("alt" if r.get("alt") else "standard"),
                                             "over_available": r.get("over_available"), "under_available": r.get("under_available"),
                                             "updated_at": r.get("updated_at"), "board_at": r.get("_board_at") or board_at_iso(r.get("book") or "underdog")}}
                    try:                                  # pl-2 anchor inputs (shadow measurement only), atomically now
                        bk1 = r.get("book") or "underdog"
                        nm1 = res.get("next_match") or {}
                        oq, oy = other_anchor(bk1, (pb.get("player") or {}).get("id"), nm1.get("slug"), comparison._ts(nm1.get("start")),
                                              r["prop"], r.get("scope") or "12")
                        res["book"]["quote"]["anchors"] = {"target": quote_record(bk1, r, r.get("_feed_meta") or pass_meta.get(bk1)), "other": oq, "other_reason": oy,
                                                           "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                    except Exception:
                        traceback.print_exc()
                    if not grade_transient(g):                # never logged as a forecast of the live model (calibration cohorts)
                        perf_shadow.attach(res, r)            # fh-1 shadow (full history), logged beside it - never a live input
                        try:
                            picklog.log_async(res, v, test=2)
                        except Exception:
                            traceback.print_exc()
                    if grade_display.bo3_shadow(g):
                        # sr-bo3-2: a BO3.gg-route projection is logged exactly as above but never shown - the stored row has
                        # no side, so the board, slips, compare table and content book skip the line (admin sees it as SHADOW)
                        BOARD[r["id"]] = grade_display.shadow_board_row(r, g, v, time.time(), BOARD[r["id"]].get("graded_at"))
                except Exception:
                    traceback.print_exc()
                if len(BOARD) % 10 == 0:
                    _board_save()

            # audit 21: a live queue, not a frozen list - every BOARD_RESCAN_S the feeds (in memory) are re-read and lines
            # that moved, opened the Under or were first posted since the pass began go to the front, so they are not
            # hidden until the whole pass ends (each line at most 3 times a pass)
            q_, lk_, seen_ = collections.deque(todo), threading.Lock(), {}
            queued_, scan_ = {r["id"] for r in todo}, [time.time()]

            def next_row():
                with lk_:
                    if time.time() - scan_[0] >= BOARD_RESCAN_S:
                        scan_[0] = time.time()
                        now_ = datetime.now(timezone.utc).isoformat()
                        try:
                            cur = underdog.board()["rows"] + prizepicks.board()["rows"]
                        except Exception:
                            cur = []
                        # only a line whose line / Under differs from what this pass already tried (a failed regrade
                        # is not retried in a loop - the next pass does that)
                        front = [r for r in cur if r["id"] not in queued_ and gradable(r, now_) and len(seen_.get(r["id"], ())) < 3
                                 and (r.get("line"), r.get("under_available")) not in seen_.get(r["id"], ())
                                 and (r["id"] not in BOARD or moved(r))]
                        for r in reversed(front):
                            q_.appendleft(r)
                            queued_.add(r["id"])
                    if not q_:
                        return None
                    r = q_.popleft()
                    queued_.discard(r["id"])
                    seen_.setdefault(r["id"], set()).add((r.get("line"), r.get("under_available")))
                    return r

            def work(_):
                while True:
                    r = next_row()
                    if r is None:
                        return
                    one(r)

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(BOARD_WORKERS, thread_name_prefix="board") as ex:
                list(ex.map(work, range(BOARD_WORKERS)))
            # a line that left the feed / whose match started is dropped (no endless 10 s retry pause, no unbounded board)
            prune_board(feeds)
            _board_save()
            try:                                      # roles: the HLTV scraper's tables (30 min), then Liquipedia
                roles.refresh_hltv()
            except Exception:
                traceback.print_exc()
            try:
                roles.refresh([((BOARD.get(r["id"]) or {}).get("nick") or r["player"],
                                (BOARD.get(r["id"]) or {}).get("team_name") or r.get("team")) for r in rows])
            except Exception:
                traceback.print_exc()
            try:                                      # rebuilt builder, shadow only: fixed hourly batches (slip_batches.py)
                slip_batches.maybe_batch(board_payload()["rows"], picklog.MODEL_VERSION)
            except Exception:
                traceback.print_exc()
            try:                                      # shadow batches: settle finished ones (each once, durable) every 30 min
                if time.time() - _SLIP_SETTLED[0] > 1800:
                    _SLIP_SETTLED[0] = time.time()
                    slip_batches.settle_all(slip_batches.leg_result_from_picks(picklog.settlement_rows()))
            except Exception:
                traceback.print_exc()
            try:                                      # the builder's own record: each new recommended slip, once
                slips.log_recommended(board_payload()["rows"], picklog.MODEL_VERSION)
            except Exception:
                traceback.print_exc()
        except Exception:
            traceback.print_exc()
        # lines waiting on the HLTV database (kept grades / placeholders) are tried again within seconds, not after a full pass
        time.sleep(10 if any(v.get("hltv_retry") for v in list(BOARD.values())) else 60)


def cleanup():
    """Keep the volume from filling: slim/compress old files, drop stale ones."""
    try:
        freed = prune() + cs2_scout.prune_misc()
        try:
            import hltv_store
            hltv_store.prune()                   # stored HLTV answers older than 14 days
        except Exception:
            pass
        STORAGE["freed_mb"] = round(STORAGE["freed_mb"] + freed / 1e6, 1)
        STORAGE["last_cleanup"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        traceback.print_exc()


def preload_forever():
    """Grade every rostered player of every upcoming match once, so members' look-ups hit
    saved data. Matches with a Polymarket market first (those get prop lines), then by start."""
    warmed = {}   # slug -> time loaded
    while True:
        cleanup()
        try:
            b = BO3(background=True)
            try:
                slate = cs2_scout.slate(b, PRELOAD_HOURS)
                try:
                    events = upcoming_events()
                    for m in slate:
                        m["pm"] = match_odds(m["team1"], m["team2"], events).get("found", False)
                except Exception:
                    for m in slate:
                        m["pm"] = False
                slate.sort(key=lambda m: (not m["pm"], m["start"] or ""))
                queue = []
                for m in slate:
                    for side, other in (("team1", "team2"), ("team2", "team1")):
                        try:
                            roster = [p for p in (b.team(m[side + "_slug"]).get("players") or [])
                                      if not p.get("is_coach")]
                        except Exception:
                            continue
                        queue += [(p["nickname"], p["slug"]) for p in roster]
            finally:
                b.close()
            try:
                sb = BO3(background=True)
                try:
                    PRELOAD_STATUS["settled_last_pass"] = picklog.settle(sb, cs2_scout.map_rows)
                finally:
                    sb.close()
            except Exception:
                traceback.print_exc()
            for nick, slug in queue:
                if time.time() - warmed.get(slug, 0) < REFRESH_AFTER:
                    continue
                PRELOAD_STATUS["current"] = nick
                try:
                    cs2_scout.grade(nick, 20.5, slug=slug, faceit_key=None, background=True)
                    warmed[slug] = time.time()
                    mark_warm(slug, nick)
                except Exception:
                    PRELOAD_STATUS["errors"] += 1
                PRELOAD_STATUS["players_ready"] = sum(1 for t in warmed.values() if time.time() - t < REFRESH_AFTER * 2)
            PRELOAD_STATUS["current"] = None
            PRELOAD_STATUS["last_pass"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            traceback.print_exc()
            PRELOAD_STATUS["errors"] += 1
        time.sleep(PRELOAD_EVERY)


def faceit_key():
    """FACEIT_API_KEY from this process, else from the Windows user environment (setx)."""
    key = os.environ.get("FACEIT_API_KEY")
    if key or sys.platform != "win32":
        return key
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, "FACEIT_API_KEY")[0]
    except OSError:
        return None


def public(res):
    """What members see: drop admin-only details."""
    fi = res.pop("faceit_identity", None) or {}
    if not fi.get("show_to_members"):
        res.pop("faceit", None)
    res.pop("grader_inputs", None)
    res.pop("_h2h_rows", None)
    res.pop("_h2h_opp", None)
    res.pop("_regional_meta", None)
    (res.get("player_block") or {}).pop("_rf_kpr", None)
    (res.get("player_block") or {}).pop("_older_maps", None)
    (res.get("player_block") or {}).pop("_hltv_overall_tried", None)
    res.pop("_opp_roster", None)
    (res.get("player_block") or {}).pop("_env", None)
    for k in ("_match_env", "overall_env"):
        (res.get("player_block") or {}).pop(k, None)
    for k in ("_opp_rows", "_opp_rank"):
        (res.get("player_block") or {}).pop(k, None)
    rb = (res.get("player_block") or {}).pop("_rank_band", None)
    if rb and isinstance(res.get("hltv_context"), dict):
        # the one the grade used - on a copy: the context dict is hltv.context's cached answer, shared with later grades
        # (2026-10-13 audit: BO3.gg's band replaced the cached HLTV band)
        res["hltv_context"] = dict(res["hltv_context"], rank_band=rb)
    g_ = res.get("grade")
    if isinstance(g_, dict) and g_.get("odds_transient_reason"):
        g_["odds_transient_reason"] = _member_text(g_["odds_transient_reason"])   # reader text in words only
    res.pop("opponent_defense", None)      # shadow feature - admin log only until it is proven
    pb = res.get("player_block") or {}
    for r in pb.get("recent_maps") or []:
        r.pop("steam_id", None)
        r.pop("lineup", None)
    pb.pop("steam_id", None)
    try:                                   # display only: HS% of each last-10 series (owner 2026-10-20)
        series_hs(pb, (res.get("grade") or {}).get("prop"))
    except Exception:
        traceback.print_exc()
    try:                                   # the card's odds lines, from the full grade (before the recipe is stripped)
        res["odds_info"] = card_odds_info(res)
    except Exception:
        traceback.print_exc()
        res["odds_info"] = None
    if isinstance(res.get("odds"), dict):  # a member copy: the selection's audit fields (feed path, snapshot hash,
        od = dict(res["odds"])             # signature, query, ids) stay in the admin forecast log
        if isinstance(od.get("provenance"), dict):
            od["provenance"] = {k: od["provenance"][k] for k in _PROV_KEEP if k in od["provenance"]}
        res["odds"] = od
    protect_recipe(res)
    return res


# ---------------------------------------------------------------- keep the recipe private
# Members see inputs (stats, form, H2H, match, odds) and outputs (projection, chance, grade,
# units). The recipe - weights, constants, spread, multipliers, internal factors - never leaves
# the server (the page AND the raw API response), so the grader can't be copied from it.
KEEP_DETAIL = {"p_win", "p_map1", "p_map2", "maps_used", "role", "rank"}
_FLAG_RULES = (   # (starts with, member-friendly text or None = not shown)
    # owner 2026-10-16: no unsure labels on the member site - an outage / unconfirmed note is left out, never reworded
    ("OLD PROJECTION: the performance model is unavailable here (HLTV database unreachable", None),
    ("OLD PROJECTION", "Projection from the older model - the newer performance model has no HLTV data for this player or matchup"),
    ("CAP:", "Chance capped at 78% on kills props (big kill totals swing more than the model's spread)"),
    ("PROVISIONAL:", "Projection from BO3.gg history - HLTV has no history for this player"),
    ("Gate C: roster", None),
    ("Gate D: only", "Small sample of recent official maps - extra caution, unit size limited"),
    ("Thin data", "Small sample of recent official maps - extra caution, unit size limited"),
    ("Gate A", None), ("Heavy favourite", None), ("Underdog HS", None), ("Role unconfirmed", None),
    ("Support profile", None), ("Stack capped", None), ("Kills props beat", None),
    ("No moneyline", None), ("Only one moneyline", None),
    ("SOFT BOOK", "Thin betting market - unit size limited"),
    ("NO MARKET", "No betting market for this match - unit size limited"),
    ("STAND-IN", "Stand-in - playing for another team, unit size limited"),
    ("VETO UNCERTAINTY", None),
    ("FORM FIGHT", "FORM FIGHT - his recent record at this line disagrees - no units"),
    ("TAPE FIGHT", "TAPE FIGHT - his recent record at this line disagrees - small stake only"),
    ("0 units: SCOUT", "No upcoming match found - scouting view only"),
    ("0 units", None),
)
HLTV_NOT_CONNECTED = "Grading resumes in a few minutes - the Prop Board keeps each line's latest grade meanwhile"
HLTV_NOT_CONFIGURED = "Projection from the older model - the HLTV database is not configured on this server"
_REASONS = (("Gate A", "AWPer on a headshot prop"), ("Gate D", "No recent official maps to grade"),
            ("Gate C", "Not on this team's roster"), ("Missing", "Not enough data to grade"))


# ---------------------------------------------------------------- the odds a card's grade used (2026-10-13)
# market_odds.select picks ONE source per grade (Thunderpick first, else Polymarket, else the OddsPapi fallback) and
# records it in odds.provenance. Members are told which one, what market / scope it is, why Thunderpick was not used,
# and how the odds act on THIS grade: on the performance model only the stake rule (market-quality cap) and the context
# win chance; on the older model the projection itself.
SOFT_TEXT_BOOKMAKER = "Bookmaker price - stake capped at {cap}U under the market-quality rule"
SOFT_TEXT_THIN = "Thin betting market - unit size limited"
_PROV_KEEP = ("selected", "source", "selection", "policy", "odds_policy", "market", "scope", "scope_basis",
              "scope_provider_confirmed", "series_format", "draws_possible", "team_is_a", "odds_team", "odds_opp",
              "implied_team_raw", "implied_opp_raw", "margin", "p_team", "p_opp", "method", "retrieved_at", "quote_age_s",
              "availability", "source_updated_at", "source_updated_note", "timezone_basis", "timezone_provider_confirmed",
              "match_url", "fallback_reason", "book")


def odds_selected(odds):
    """'thunderpick' / 'polymarket' / 'oddspapi' / None: the source that supplied THIS grade's win chance."""
    if not isinstance(odds, dict) or not odds.get("found"):
        return None
    sel = (odds.get("provenance") or {}).get("selected")
    if sel in ("thunderpick", "polymarket", "oddspapi"):
        return sel
    if odds.get("book") == "thunderpick":
        return "thunderpick"
    return "oddspapi" if odds.get("book") else "polymarket"


def _soft_text(f, src=None, book=None, cap=None):
    """The member text of a SOFT BOOK flag: a bookmaker price (Thunderpick / an OddsPapi book) is not a thin market."""
    cap = cap if cap is not None else _soft_cap()
    if src == "thunderpick" or (src is None and re.search(r"thunderpick", f, re.I)):
        return SOFT_TEXT_BOOKMAKER.format(cap=f"{cap:g}")
    if src == "oddspapi" or (src is None and re.search(r"bookmaker|oddspapi", f, re.I)):
        return (f"Bookmaker price ({book}) - stake capped at {cap:g}U under the market-quality rule" if book else
                SOFT_TEXT_BOOKMAKER.format(cap=f"{cap:g}"))
    return SOFT_TEXT_THIN


def _soft_cap():
    try:
        from grader import CONFIG
        return float(CONFIG["soft_market_cap"])
    except Exception:
        return 1.5


def _is_perf(g):
    return str((g or {}).get("projection_source") or "").startswith("perf") and ((g or {}).get("scope") or "12") in ("12", "1", "123")


_MAP_FORMULA = {3: "series chance p = q^2 (3 - 2q) for a per-map chance q",
                5: "series chance p = q^3 (10 - 15q + 6q^2) for a per-map chance q"}


def _map_basis_words(basis, bo=None):
    """How the grade's per-map chances came from the series price, in words. basis = detail.map_prob_basis: the grader's
    dict (grader.map_win_chance: series_win_chance, format 'bo1' / 'bo3' / 'bo5' / 'unknown - BO3 assumed', formula,
    assumption, kind) - read by its FORMAT field (2026-10-13 review: reading the dict as text matched its 'assumption'
    key and told every best-of-3 card 'format not known'); a plain string from an older record; None = a grade from
    before the grader recorded it (the best-of-3 inversion it always used, whatever the match format)."""
    if isinstance(basis, dict):
        fmt = str(basis.get("format") or "").strip().lower()
        formula = re.sub(r"^BO\d:\s*", "", str(basis.get("formula") or "")).strip()
        if fmt == "bo1":
            return "best-of-1: the map chance is the series chance"
        if fmt in ("bo3", "bo5"):
            return f"best-of-{fmt[2]} formula: " + (_MAP_FORMULA[int(fmt[2])] if not formula else
                                                    f"series chance {formula} for a per-map chance q")
        if fmt.startswith("unknown") or not fmt:
            return "format not known - best-of-3 formula assumed: " + _MAP_FORMULA[3]
        return f"{fmt} - " + (formula or "model-derived")[:120]
    b = str(basis or "").lower()
    if b:                                           # a plain-text basis: the format it names ('unknown' before 'bo3')
        if "unknown" in b:
            return "format not known - best-of-3 formula assumed: " + _MAP_FORMULA[3]
        if "bo1" in b or "best-of-1" in b:
            return "best-of-1: the map chance is the series chance"
        if "bo5" in b or "best-of-5" in b:
            return "best-of-5 formula: " + _MAP_FORMULA[5]
        if "bo3" in b or "best-of-3" in b:
            return "best-of-3 formula: " + _MAP_FORMULA[3]
        return str(basis)[:120]
    return ("best-of-3 formula applied to a best-of-1 match (an approximation)" if bo == 1 else
            "best-of-5 match read with the best-of-3 formula (an approximation)" if bo == 5 else
            "best-of-3 formula: " + _MAP_FORMULA[3])


def _map_basis_kind(basis, maps=None):
    """Where THIS grade's two map chances came from: 'derived' (both model-derived from the series price), 'source' (both
    from the selected source's own map markets) or 'partial' (one from its map market, the other derived). The grader's
    record decides when it has one (map_prob_basis.kind); else the odds' own map prices."""
    k = str((basis or {}).get("kind") or "").lower() if isinstance(basis, dict) else ""
    if k:
        if k.startswith("map winner prices"):
            return "source"
        if "the other model-derived" in k:
            return "partial"
        return "derived"
    maps = maps or {}
    n = sum(1 for x in ("map1", "map2") if maps.get(x))
    return "source" if n == 2 else "partial" if n == 1 else "derived"


def card_odds_info(res):
    """The card's odds lines (built from the full grade, before the recipe is stripped): source used, market + scope,
    why Thunderpick was not used, how odds act on this grade, the market-quality stake rule in this source's words,
    the retrieval time, the derived-maps note and the labels for the Match section / win-chance tile / footer."""
    od = res.get("odds") if isinstance(res.get("odds"), dict) else None
    g = res.get("grade") or {}
    if od is None or "p_more" not in g:
        return None
    pv = od.get("provenance") or {}
    src = odds_selected(od)
    perf = _is_perf(g)
    mq = g.get("market_quality")
    cap = _soft_cap()
    nm = res.get("next_match") or {}
    fb = str(pv.get("fallback_reason") or "").strip()
    fb = re.sub(r"https?://\S+", "", fb)[:160] or None
    out = {"source": src, "perf": perf, "market_quality": mq}
    if src == "thunderpick":
        label, market = "Thunderpick", pv.get("market") or "Match Winner"
        scope = pv.get("scope") or "series winner"
        out["scope_note"] = f"{market} - {scope}"      # owner 2026-10-16: no hedge wording (the basis stays in THUNDERPICK.md)
        out["price_note"] = "Bookmaker odds, margin removed proportionally"
        out["retrieved_at"] = pv.get("retrieved_at")
        out["time_note"] = "retrieved by us - the feed publishes no quote time"
        out["prices"] = {k: pv.get(k) for k in ("odds_team", "odds_opp", "implied_team_raw", "implied_opp_raw", "margin",
                                                  "p_team", "p_opp")}
    elif src == "oddspapi":
        book = od.get("book") or pv.get("book")
        label, market, scope = f"OddsPapi ({book})" if book else "OddsPapi", "match winner", "series winner"
        out["price_note"] = f"Bookmaker odds ({book})" if book else "Bookmaker odds"
    elif src == "polymarket":
        label, market, scope = "Polymarket", "moneyline market", "series winner"
        out["price_note"] = "Exchange price (bid/ask midpoint, or last trade when the spread is wide)"
    else:
        label = market = scope = None
    out["label"] = label
    if src:
        out["line"] = f"Odds source used: {label} ({market}, {scope})"
        out["fallback"] = None                          # owner 2026-10-16: no "not used / not usable" notes on the card
        out["match_label"] = f"Win chance ({label}{', margin removed' if src == 'thunderpick' else ''})"
        out["footer"] = f"{od.get('book')} via OddsPapi" if src == "oddspapi" and od.get("book") else label
    else:
        out["line"] = "Odds source used: none - no betting market found for this match"
        out["fallback"] = None
        out["match_label"] = "Win chance"
        out["footer"] = None
    if perf:
        out["use"] = ("How odds are used here: stake rule only (market-quality cap) and context win chance - the "
                      "projection does not use odds")
    else:
        out["use"] = ("How odds are used here: the projection uses the win chance"
                      + ("" if src else " (no market: the older model grades the match as even)")
                      + "; the market-quality stake rule also applies")
    if mq == "SOFT":
        out["stake_rule"] = _soft_text("", src, od.get("book"), cap) if src in ("thunderpick", "oddspapi") else SOFT_TEXT_THIN
        out["stake_pill"] = (f"Bookmaker price · max {cap:g}U" if src in ("thunderpick", "oddspapi")
                             else f"Thin betting market · max {cap:g}U")
    elif mq in ("NONE", "NO MARKET"):
        out["stake_rule"] = "No betting market for this match - unit size limited"
        out["stake_pill"] = f"No betting market · max {cap:g}U"
    else:
        out["stake_rule"] = out["stake_pill"] = None
    if not src:
        out["match_note"] = ("No betting market found for this match - stake capped at "
                             f"{cap:g}U; {'the projection does not use odds' if perf else 'the older model grades the match as even'}")
        out["tile_note"] = "no market – stake capped" if perf else "no market – graded as even"
    else:
        out["match_note"] = None
        out["tile_note"] = (f"{label}{' (margin removed)' if src == 'thunderpick' else ''} · context"
                            f"{' + stake cap' if mq not in (None, 'HARD') else ''}; not in the projection"
                            if perf else f"{label} · in the projection")
    # the price ACTUALLY used - Thunderpick or a fallback - with our retrieval time and its age when graded, and whether
    # the selected odds changed this grade (2026-10-14 owner: a source label alone is not projection integration)
    if src:
        out["retrieved_at"] = out.get("retrieved_at") or pv.get("retrieved_at")
        out["quote_age_s"] = pv.get("quote_age_s")
        if src != "thunderpick" and not out.get("time_note"):
            out["time_note"] = f"our retrieval time of {label}'s prices"
    out["market_effect"] = (g.get("detail") or {}).get("market_effect")
    maps = od.get("maps") or {}
    mpb = (g.get("detail") or {}).get("map_prob_basis")
    kind = _map_basis_kind(mpb, maps)
    feeds = (" They feed the match simulation's map scenarios"
             + (" and the Maps 1–3 'map 3 played' chance - not the projection" if perf
                else " and the older model's projection") + ".")
    if src and kind != "source":
        basis = _map_basis_words(mpb, nm.get("bo"))
        how = basis if basis.startswith("best-of-1:") else f"{basis}; maps assumed equal and independent"
        if kind == "partial":                       # one map from the source's own market, the other derived
            have = "Map 1" if maps.get("map1") else "Map 2" if maps.get("map2") else "One map"
            out["maps_note"] = (f"{have} win chance from {label}'s own map market; the other map's chance is model-derived "
                                f"from the {label} series price ({how}).{feeds}")
        else:
            out["maps_note"] = (f"Map win chances are model-derived from the {label} series price ({how}) - {label} "
                                f"supplies no map prices.{feeds}")
    elif src:
        out["maps_note"] = f"Map win chances from {label}'s own map markets.{feeds}"
    else:
        out["maps_note"] = None
    return out


def odds_blip_note(res):
    """The card's notice for an odds blip (owner 2026-10-13: handled like an HLTV blip): the real condition - Thunderpick
    lists this match but has no current quote - and what the card's odds actually are, never presented as current
    Thunderpick odds."""
    g = res.get("grade") or {}
    why = _member_text(g.get("odds_transient_reason"))
    od = res.get("odds") if isinstance(res.get("odds"), dict) else {}
    src, book = odds_selected(od), od.get("book")
    fb = "Polymarket" if src == "polymarket" else (f"{book} via OddsPapi" if book else "OddsPapi") if src == "oddspapi" else None
    used = (f"this card's odds come from {fb} (the fallback source), not from Thunderpick" if fb else
            "the Thunderpick price on this card is not a current quote" if src == "thunderpick" else
            "this card has no betting-market odds")
    return f"Thunderpick lists this match but has no current quote{f' ({why})' if why else ''} - {used}."


def _public_flag(f, src=None, book=None):
    if f.startswith("SOFT BOOK"):                   # the market-quality cap, in the selected source's own words
        return _soft_text(f, src, book)
    if f.startswith("OLD PROJECTION") and "HLTV database not configured" in f:
        # the real cause (2026-10-13 audit: it read 'no HLTV data for this player or matchup')
        return HLTV_NOT_CONNECTED if hltv_required() else HLTV_NOT_CONFIGURED
    if f.startswith("RUST") or f.startswith("Note: last official map"):
        days = next((w for w in f.split() if w.isdigit()), None)
        return (f"Last official map {days} days ago" + (" - extra caution" if f.startswith("RUST") else "")) if days else None
    if f.startswith("TAPE:"):
        return "Projection differs from his last-10 average"
    if f.startswith("HLTV STALE: "):                # its text is written for members (cs2_scout._hltv_staleness)
        return f[len("HLTV STALE: "):]
    if ": no Maps 1-2 on this map" in f:
        return f.split(" - ")[0]
    for start, text in _FLAG_RULES:
        if f.startswith(start):
            return text
    return None                                     # anything not listed stays private


def _hs_norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def series_hs(pb, prop):
    """Each last-10 series' headshots / kills on the maps the table shows (owner 2026-10-20: HS% on the card) - display
    only, never a grade input. The maps come from the same rows as the table: HLTV's series (the cached read the grade
    just made), else his BO3.gg maps; a series is filled only when the matched maps reproduce the table's own numbers
    (same date, opponent and per-map values) and every map has a real headshot count (0 with 12+ kills = missing)."""
    import cs2_scout
    stat = "headshots" if prop == "hs" else "kills"
    try:
        hl = cs2_scout._hltv_series(pb, 365)
    except Exception:
        hl = []
    pool = {}
    for m in list(hl or []) + list(pb.get("recent_maps") or []):
        pool.setdefault((str(m.get("date"))[:10], _hs_norm(m.get("opponent"))), []).append(m)
    for key in ("recent_form", "recent_form_map1", "recent_form_123"):
        rf = pb.get(key)
        for s in (rf or {}).get("series") or [] if isinstance(rf, dict) else []:
            n = len(s.get("values") or [])
            cands = {}
            for m in pool.get((str(s.get("date"))[:10], _hs_norm(s.get("opponent"))), []):
                cands.setdefault((m.get("source"), m.get("match_id")), []).append(m)
            for ms in cands.values():
                ms = sorted(ms, key=lambda m: m.get("number") or 9)[:n]
                if len(ms) != n or [m.get(stat) for m in ms] != list(s["values"]):
                    continue
                if any(m.get("headshots") is None or not m.get("kills") or (m["headshots"] == 0 and m["kills"] >= 12) for m in ms):
                    continue
                k, h = sum(m["kills"] for m in ms), sum(m["headshots"] for m in ms)
                s["hs"], s["hs_kills"], s["hs_pct"] = h, k, round(100 * h / k, 1)
                break


def protect_recipe(res):
    g = res.get("grade")
    sim0 = res.get("simulation")
    if isinstance(g, dict) and isinstance(sim0, dict) and sim0.get("anchored") and "rounds" not in sim0:
        try:                                        # display only (sim_rounds.py): the same runs grouped by map length,
            import sim_rounds                       # read from the full grade before its detail is trimmed below
            res["simulation"] = dict(sim0, rounds=sim_rounds.round_view(g))
        except Exception:
            traceback.print_exc()
    if isinstance(g, dict):
        g = res["grade"] = dict(g)                  # the member copy: the full grade object itself is left as it was
        flags = []
        src, book = odds_selected(res.get("odds")), (res.get("odds") or {}).get("book")
        for f in g.get("flags") or []:
            if ": no Maps 1-2 on this map" in f and _is_perf(g):
                continue                            # the performance model takes no map input: a stale note there
            t = _public_flag(f, src, book)
            if t and t not in flags:
                flags.append(t)
        g["flags"] = flags
        g["detail"] = {k: v for k, v in (g.get("detail") or {}).items() if k in KEEP_DETAIL}
        # dist / online_adj carry the spread (sd; HS: spread constant c, hs_per_kill) and the online weights (audit 58);
        # the chart uses dist_view, computed from the full grade before public()
        for k in ("lambda_base", "sd", "value", "market", "model_grade", "p_more_model", "h2h", "regional", "dist", "online_adj"):
            g.pop(k, None)
        if g.get("reason"):
            g["reason"] = next((t for k, t in _REASONS if g["reason"].startswith(k)), "Not graded")
    sim = res.get("simulation")
    if isinstance(sim, dict):
        res["simulation"] = {k: sim.get(k) for k in ("p_more", "p_push", "mean", "p10", "p50", "p90", "scenarios", "scenarios_note", "anchored", "rounds")}
    res.pop("simulation_error", None)
    o = (res.get("player_block") or {}).get("overall") or {}
    for k in ("kpr", "hs_pct", "form_blend", "kpr_single_window", "hs_single_window"):
        o.pop(k, None)                              # blended numbers + their weights stay private


def memory_mb():
    """Resident memory of this process (Linux/Railway); None elsewhere."""
    try:
        with open("/proc/self/status") as f:
            return round(int(next(l for l in f if l.startswith("VmRSS")).split()[1]) / 1024)
    except Exception:
        return None


_PAGE = {"mtime": None, "data": b""}
_WEBFILES = {}                                          # name -> {"mtime", "data"} for the other member pages
_FILE_LOCK = threading.Lock()


def page():
    """index.html from memory (re-read only when the file changes)."""
    f = WEB / "index.html"
    mt = f.stat().st_mtime
    if _PAGE["mtime"] != mt:
        with _FILE_LOCK:                                # single flight: 700 cold requests share ONE bytes object (follow-up
            if _PAGE["mtime"] != mt:                    # 2026-11-06: each racing read made its own copy, build and gzip)
                _PAGE["data"], _PAGE["mtime"] = f.read_bytes(), mt
    return _PAGE["data"]


_SPORTS_NAV_AFTER = (b'<a id="sportlol" href="/lol">LoL</a></nav>',                      # index.html header
                     b'<a href="/lol" class="on" aria-current="page">LoL</a></nav>')       # lol.html header
_SPORTS_NAV = b'<nav class="sportsw" aria-label="Sports"><a href="/sports">NFL / NBA</a></nav>'
_SPORTS_NAV_MEMO = {}                                   # id(page bytes) -> (page bytes, page with the link)


def sports_allow_id(user):
    """The allow-list id of a Whop user for SPORTS_UI=owner: "sa_" + sha256("scout-sports-allow|" + user)[:24]. A hash,
    so no Whop id is ever written to git or env; independent of SPORTS_TOKEN_SECRET (survives a rotation). The same
    formula is sports.gateway.allow_id (tested equal)."""
    import hashlib
    return "sa_" + hashlib.sha256(f"scout-sports-allow|{user}".encode("utf-8")).hexdigest()[:24]


def sports_nav(body):
    """The ONE Sports link on the esports pages, added only when SPORTS_UI is on (Handler._sports_on). Pure bytes edit
    after the game switcher - no sports import; with the flag off the page bytes are untouched
    (sports/tests/test_isolation_server.py). A page without the marker is returned unchanged.
    Memoised per page OBJECT: send_shared / encoded() cache the ETag + gzip by body identity, so a fresh copy per
    request would recompress the page every time (measured 2026-10-23: esports p95 5.6 s at 700 members without it)."""
    hit = _SPORTS_NAV_MEMO.get(id(body))
    if hit is not None and hit[0] is body:
        return hit[1]
    out = body
    for marker in _SPORTS_NAV_AFTER:
        if marker in body:
            out = body.replace(marker, marker + _SPORTS_NAV, 1)
            break
    if len(_SPORTS_NAV_MEMO) > 8:                        # two pages; old versions dropped when a file changes
        _SPORTS_NAV_MEMO.clear()
    _SPORTS_NAV_MEMO[id(body)] = (body, out)
    return out


def web_file(name):
    """A member page from memory (re-read only when the file changes) - lol.html was read from disk on every request."""
    f = WEB / name
    mt = f.stat().st_mtime
    c = _WEBFILES.get(name)
    if not c or c["mtime"] != mt:
        with _FILE_LOCK:                                # single flight, as page()
            c = _WEBFILES.get(name)
            if not c or c["mtime"] != mt:
                c = _WEBFILES[name] = {"mtime": mt, "data": f.read_bytes()}
    return c["data"]


_LANDING = {"raw": None, "url": None, "data": b""}
LANDING_PATHS = ("/", "/index.html", "/lol", "/sports", "/sports/")


def landing_page():
    """The signed-out landing page (web/landing.html) with the Join on Whop link filled in. ONE bytes object per (file
    version, join url): send_shared caches the ETag + gzip by body identity (see sports_nav)."""
    raw, url = web_file("landing.html"), whop_auth.upgrade_url()
    if _LANDING["raw"] is not raw or _LANDING["url"] != url:
        _LANDING.update(raw=raw, url=url, data=raw.replace(b"{{JOIN_URL}}", htmllib.escape(url, quote=True).encode("utf-8")))
    return _LANDING["data"]


def landing_route(path):
    """Paths whose signed-out visitor gets the landing page: the CS2 page always, /lol only when it is released
    (otherwise unchanged), /sports only when SPORTS_UI is set at all (otherwise the unknown-route answer, unchanged)."""
    if path in ("/", "/index.html"):
        return True
    if path == "/lol":
        return lol_public()
    if path in ("/sports", "/sports/"):
        return os.environ.get("SPORTS_UI", "").strip().lower() in ("1", "true", "yes", "on", "owner")
    return False


# Research workspace preview, gated by Handler._workspace_on. Fixed names only - web_file has no path guard, so a request
# path never reaches it. Its API lives in workspace_api.py, imported on the first allowed request: it reads saved grades
# and offers and never grades, logs a forecast or changes a stake.
WORKSPACE_ROUTES = {"/preview/workspace": ("workspace.html", "text/html; charset=utf-8"),
                    "/preview/workspace/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
                    "/preview/workspace/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
                    "/preview/workspace/workspace-data.js": ("workspace-data.js", "text/javascript; charset=utf-8")}
WORKSPACE_API = "/preview/workspace/api/"

# Upgraded member pages, gated by Handler._upgrade_on (SCOUT_UPGRADE, default off). The pages stay at their own routes and the
# files on disk are never edited: the gate adds one stylesheet and one script to the bytes it sends. Off, nothing below runs and
# the page bytes are the file's (tests/test_upgrade_gate.py). The API is the workspace's, on its own prefix and its own gate.
UPGRADE_ROUTES = {"/upgrade/upgrade.css": ("upgrade.css", "text/css; charset=utf-8"),
                  "/upgrade/hub.js": ("upgrade-hub.js", "text/javascript; charset=utf-8"),
                  "/upgrade/hub.css": ("upgrade-hub.css", "text/css; charset=utf-8"),
                  "/upgrade/upgrade.js": ("upgrade.js", "text/javascript; charset=utf-8")}
UPGRADE_API = "/api/upgrade/"
_UPGRADE_MEMO = {}                                      # (id(page bytes), name, tokens) -> (page bytes, upgraded page)
_UPGRADE_LOCK = threading.Lock()
UI_TOKENS_ROUTE = "/upgrade/tokens.css"                 # SCOUT_UI_TOKENS=1 only; off = the unknown-route answer
PICKEM_EV_ROUTE = "/upgrade/ev.js"                      # SCOUT_PICKEM_EV=1 only (the +EV board's page script); off = unknown route


def ui_tokens_on():
    """SCOUT_UI_TOKENS (default off): the hub on the classic 1Time tokens via its own stylesheet. Off, upgrade_page and hub.css are
    byte for byte what they were."""
    return os.environ.get("SCOUT_UI_TOKENS", "").strip() == "1"


def classic_lazy_on():
    """audit 2026-11-05: on the upgraded CS2 page the classic page is built only when it is shown (?view=classic, a classic route such
    as #matches, or the research board unavailable). Until then it waits in a <template>: not in the page, not read by screen readers,
    its scripts not run. SCOUT_CLASSIC_LAZY=0 sends it built at once, as before."""
    return os.environ.get("SCOUT_CLASSIC_LAZY", "").strip() != "0"


# Builds the template's classic page in place: scripts are recreated so they run, in page order, once every node is in. The research
# board's routes keep it unbuilt; upgrade.js / hub.js call scoutClassicMount() when the board is not shown after all. The classic
# sign-in lock (body.locked, lifted by the classic script) waits with it, so its rules never hide the board's own header.
CLASSIC_MOUNT = br"""<script>(function(){
var t=document.getElementById('scout-classic');
window.scoutClassicMount=function(){
if(!t||!t.parentNode)return false;
var b=document.body;if(b.dataset.classicLocked){b.classList.add('locked');delete b.dataset.classicLocked;}
var f=document.importNode(t.content,true),list=f.querySelectorAll('script');
for(var i=0;i<list.length;i++){var o=list[i],n=document.createElement('script');for(var a=0;a<o.attributes.length;a++)n.setAttribute(o.attributes[a].name,o.attributes[a].value);n.text=o.text;o.parentNode.replaceChild(n,o);}
var p=t.parentNode;p.insertBefore(f,t);p.removeChild(t);t=null;
document.dispatchEvent(new Event('scout-classic-mount'));
return true;};
var h=location.hash;
if(window.scoutClassic||!(!h||h==='#all-sports'||/^#(?:board(?:\/|$)|scout\/|\/(?:board|games|slips|results|record|tools|picks|alerts|ev)(?:[?\/]|$))/.test(h)))window.scoutClassicMount();
else if(document.body.classList.contains('locked')){document.body.classList.remove('locked');document.body.dataset.classicLocked='1';}
})();</script>"""


def upgrade_page(body, name):
    """The upgraded copy of a member page. Memoised per page OBJECT, as sports_nav is: send_shared caches the ETag + gzip by body
    identity, so a fresh copy per request would recompress the page every time."""
    tokens, lazy = ui_tokens_on(), name == "cs2" and classic_lazy_on()
    hit = _UPGRADE_MEMO.get((id(body), name, tokens, lazy))
    if hit is not None and hit[0] is body:
        return hit[1]
    with _UPGRADE_LOCK:                                 # single flight (follow-up 2026-11-06: 700 members on a cold page built it
        hit = _UPGRADE_MEMO.get((id(body), name, tokens, lazy))      # 3 times, each copy ~0.5 MB and its own gzip)
        if hit is not None and hit[0] is body:
            return hit[1]
        return _upgrade_build(body, name, tokens, lazy)


def _upgrade_build(body, name, tokens, lazy):
    # Runs in the head, before the original board can paint. Classic keeps its
    # tools and renderers; a failed workspace load always releases the page.
    head = br'''<style>
html.scout-boot body{visibility:hidden!important}
html.scout-boot{background:#101215}
html.scout-boot::after{content:"Loading your workspace...";position:fixed;inset:0;display:grid;place-items:center;color:#b3becb;font:500 16px system-ui}
</style><script>
(function(){
const root=document.documentElement,q=new URLSearchParams(location.search);
window.scoutClassic=q.get('view')==='classic'||q.has('slip')||q.has('p')||q.get('bv')==='1';
window.scoutBootDone=function(){root.classList.remove('scout-boot');clearTimeout(window.scoutBootTimer);};
const h=location.hash;
if(!window.scoutClassic&&root.dataset.upgrade!=='sports-results'&&(!h||h==='#all-sports'||/^#(?:board(?:\/|$)|scout\/|\/(?:board|games|slips|results|tools|picks|alerts))/.test(h))){
root.classList.add('scout-boot');window.scoutBootTimer=setTimeout(window.scoutBootDone,8000);
}
window.scoutStylesReady=Promise.all(['/upgrade/upgrade.css','/upgrade/hub.css'].map(href=>new Promise(resolve=>{
const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.onload=link.onerror=resolve;document.head.appendChild(link);
})));
})();
</script>'''
    tail = f'<script src="/upgrade/upgrade.js" data-page="{name}"></script>'.encode()
    mark = f'<html data-upgrade="{name}" '
    if tokens:                                          # flag on only: the token sheet joins the two hub sheets, nothing else moves
        head = head.replace(b"html.scout-boot{background:#101215}", b"html.scout-boot{background:#080808}", 1)
        head = head.replace(b"['/upgrade/upgrade.css','/upgrade/hub.css']", b"['/upgrade/upgrade.css','/upgrade/hub.css','" + UI_TOKENS_ROUTE.encode() + b"']", 1)
        mark += 'data-ui-tokens="1" '
    i, j = body.rfind(b"</head>"), body.rfind(b"</body>")
    out = body
    if 0 <= i < j:                                      # a page without its markers goes out as it is
        k = body.find(b"<body", i)
        k = body.find(b">", k) + 1 if 0 <= k < j else 0
        if lazy and 0 < k <= j:                         # the classic CS2 page waits in a template until it is shown
            out = body[:i] + head + body[i:k] + b'<template id="scout-classic">' + body[k:j] + b"</template>" + CLASSIC_MOUNT + tail + body[j:]
        else:
            out = body[:i] + head + body[i:j] + tail + body[j:]
        out = out.replace(b"<html ", mark.encode(), 1)
    if len(_UPGRADE_MEMO) > 16:                         # a handful of pages; old versions drop when a file changes
        _UPGRADE_MEMO.clear()
    _UPGRADE_MEMO[(id(body), name, tokens, lazy)] = (body, out)
    return out


STATS = {"requests": 0, "ms_total": 0.0, "since": time.time(), "errors_5xx": 0}
LAST_MEMBER = [0.0]     # last member request on /api/* (the board worker steps aside while members are active)
_BUSY_RE = re.compile(r"cannot connect|connection|timed? ?out|timeout|getaddrinfo|temporar|unreachable|\b50[234]\b", re.I)


def failure_body(e, path=""):
    """(status, body) for an unexpected failure: members get a plain message with a short reference, the details go to the
    server log under that reference (2026-10-13 audit: 'Something went wrong: API request to https://api.bo3.gg/... failed:
    Cannot connect to host api.bo3.gg:443 ...' reached the card). An upstream that could not be reached -> 503, which the
    page retries by itself. One failure handed to many waiting requests (shared) is logged once, under one reference."""
    ref = getattr(e, "scout_ref", None)
    if ref is None:
        ref = secrets.token_hex(3)
        try:
            e.scout_ref = ref
        except Exception:
            pass
        sys.stderr.write(f"  error {ref} on {path}: {type(e).__name__}: {str(e)[:300]}\n")
        traceback.print_exc()
    busy = hltv.is_transient(e) or bool(_BUSY_RE.search(str(e)))
    return (503 if busy else 500), {"error": ("Couldn't reach a data source right now - please try again in a moment" if busy
                                              else "Something went wrong - please try again") + f" (ref {ref})"}


def token_eq(given, secret):
    """Constant-time token check on bytes (audit 54: compare_digest on str raises TypeError for any non-ASCII value, so
    a stray header or ?token= gave a 500 / a dropped POST instead of a refusal)."""
    import hmac
    return hmac.compare_digest(str(given or "").encode("utf-8", "surrogateescape"), str(secret).encode("utf-8", "surrogateescape"))


def qint(q, key, default, lo=None, hi=None):
    """An integer query parameter for the admin views: a missing or malformed value is the default (2026-10-26 audit:
    ?n=abc / ?days=abc gave a 500), clamped to [lo, hi] so no parameter can ask for an unbounded answer."""
    try:
        v = int(str(q.get(key) if q.get(key) not in (None, "") else default).strip())
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def qdates(q, max_days=31):
    """(from, to) dates of an admin export window, or (None, error) - missing / malformed / reversed / too long windows
    are a 400 with the reason, never a 500 (2026-10-26 audit: no ?from= raised TypeError inside fromisoformat)."""
    from datetime import date as _d
    try:
        d0, d1 = _d.fromisoformat(str(q.get("from") or "")), _d.fromisoformat(str(q.get("to") or ""))
    except ValueError:
        return None, "from and to must be dates (YYYY-MM-DD)"
    if d1 < d0:
        return None, "to is before from"
    if (d1 - d0).days > max_days:
        return None, f"max {max_days} days per call"
    return (d0, d1), None


# the client (or the proxy) went away mid-response, or a write timed out: nothing more can be sent on that connection -
# never counted or logged as a server failure, never a second response (audit 53)
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)



from health_snapshot import HealthSnapshot
_HEALTH_SNAPSHOT = HealthSnapshot()


def _health_diagnostics():
    # These diagnostics can wait on SQLite, Postgres or a directory scan.
    # Render liveness must not wait for them. Freshness is explicit in the response.
    return {"ok": True, "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:40], "faceit_key": bool(faceit_key()), "preload": PRELOAD_STATUS, "warm_start": WARM_START,
                                       "whop_signin": {"on": whop_auth.enabled(), "recent_errors": whop_auth.LAST_ERRORS[-3:]},
                                       "memory_mb": memory_mb(), "threads": threading.active_count(),
                                       "hltv": _hltv_status(public=True),
                                       "memory": _memory_report(),
                                       "caches": {"results": len(_RESULTS), "contexts": len(cs2_scout._CONTEXT),
                                                  "scoreboards": len(bo3._MEM), "expiring": len(bo3._TMEM)},
                                       "traffic": {"requests": STATS["requests"], "errors_5xx": STATS["errors_5xx"],
                                                   "avg_server_ms": round(STATS["ms_total"] / max(STATS["requests"], 1), 1),
                                                   "since": datetime.fromtimestamp(STATS["since"], timezone.utc).isoformat(timespec="seconds")},
                                       "storage": dict(storage_cached(), **STORAGE), "ops": ops_checks()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"      # keep-alive: browsers/Railway's proxy reuse connections
    timeout = 30                       # idle keep-alive connections are closed after 30 s

    def log_message(self, fmt, *a):
        pass                           # no line per request (700 members = thousands of log lines)

    def log_error(self, fmt, *a):
        sys.stderr.write("  " + fmt % a + "\n")

    def send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        STATS["requests"] += 1
        STATS["ms_total"] += (time.time() - getattr(self, "_t0", time.time())) * 1000
        if self.path.startswith("/api/search"):        # server-side seconds: request received -> response sent
            SEARCH_TIMES.append(round(time.time() - getattr(self, "_t0", time.time()), 3))
        if code >= 500:
            STATS["errors_5xx"] += 1
        self._sent = True                              # a response has started: a later failure never sends a second one
        try:
            self._write_response(code, data, ctype, headers)
        except CLIENT_GONE:
            self.close_connection = True
            STATS["client_gone"] = STATS.get("client_gone", 0) + 1

    def _write_response(self, code, data, ctype, headers):
        headers = dict(headers or {})
        self.send_response(code)
        if code != 304:                                # a 304 carries no body (and no length of one)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))   # no-store unless a member route says so
        # audit 2026-10-26: no type sniffing; the admin ?token= never travels in a Referer to another site. No frame
        # header on purpose: Whop shows the member pages inside its app frame.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in csp_headers():                     # SCOUT_CSP / SCOUT_HSTS switches: off = nothing added here
            self.send_header(k, v)
        for k, v in headers.items():
            self.send_header(k, v)
        renew, self._renew = getattr(self, "_renew", None), None
        if renew:                                      # Whop just confirmed access: the cookie carries that check (B3)
            self.send_header("Set-Cookie", f"{whop_auth.COOKIE}={renew[0]}; Max-Age={renew[1]}; Path=/; HttpOnly; Secure; SameSite=Lax")
        self.end_headers()
        if code != 304:
            self.wfile.write(data)

    def send_shared(self, key, status, body, ctype="application/json", headers=None, keep=60):
        """A shared member body (board, LoL board, moneylines, the pages) with its once-made ETag and gzip copy:
        If-None-Match on the same body -> 304, Accept-Encoding gzip -> the compressed copy, Cache-Control private, no-cache.
        Anything that is not a 200 body goes through send() unchanged (no-store)."""
        if status != 200 or not isinstance(body, bytes):
            return self.send(status, body, ctype, headers)
        etag, gz = encoded(key, body, keep)
        use_gz = gz is not None and accepts_gzip(self.headers.get("Accept-Encoding"))
        h = {"Cache-Control": MEMBER_CACHE, "Vary": "Accept-Encoding", "ETag": etag[:-1] + '-gz"' if use_gz else etag}
        h.update(headers or {})
        if etag_matches(self.headers.get("If-None-Match"), etag):
            STATS["not_modified"] = STATS.get("not_modified", 0) + 1
            return self.send(304, b"", ctype, h)
        if use_gz:
            h["Content-Encoding"] = "gzip"
        return self.send(200, gz if use_gz else body, ctype, h)

    def cookie(self, name):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return None

    def member(self):
        """The signed-in Whop member's session (or None) - read once per request. When this read re-checked access with
        Whop and got a clear answer, the response re-issues the cookie with that check (2026-10-13 audit: the check time in
        the cookie was never refreshed, so after a restart every request of a member signed in 6+ h ago asked Whop again)."""
        if not whop_auth.enabled():
            return None
        if getattr(self, "_sess", False) is False:
            s = whop_auth.session(self.cookie(whop_auth.COOKIE))
            self._renew = (s or {}).pop("renew", None)
            self._sess = s
        return self._sess

    def signed_out(self):
        """Signed-out landing (2026-10-24): Whop sign-in is on and the request carries no valid session cookie (never
        signed in, signed out, expired). Decided from the signed cookie alone - no Whop call, no cookie written. A
        signed-in visitor (member or not) and the owner's load tests get the pages exactly as before."""
        if not whop_auth.enabled() or self.load_test():
            return False
        return not whop_auth.cookie_valid(self.cookie(whop_auth.COOKIE))

    def allowed(self):
        """Grading/search/board: paid Whop members (and the owner's load tests) only. On Railway the data
        stays locked if the Whop settings are ever missing - it never opens up to everyone."""
        if not whop_auth.enabled():
            return not (os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_ENVIRONMENT_NAME")
                        or os.environ.get("REQUIRE_WHOP") or os.environ.get("RENDER"))   # Render sets RENDER=true itself
        if self.load_test():
            return True
        sess = self.member()
        return bool(sess and sess["ok"])

    def redirect(self, location, cookies=()):
        STATS["requests"] += 1
        self.send_response(302)
        self.send_header("Location", location)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        self._t0 = time.time()
        self._sess, self._renew = False, None           # per request (a kept-alive connection reuses this handler)
        self._sent = False
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ('/record','/record.js','/api/record','/api/record/slips'):
                if os.environ.get('SCOUT_PUBLIC_RECORD') != '1' or (u.path == '/api/record/slips' and os.environ.get('SCOUT_PUBLIC_RECORD_SLIPS') != '1'):
                    return self.send(404, {'error':'not found'})
                if u.path == '/api/record/slips':
                    sport=q.get('sport','cs2')
                    if sport not in ('cs2','lol','nba','nfl'):
                        return self.send(400,{'error':'unsupported sport'})
                    def slips_read():                   # one bounded read-only query per window for all four sports
                        import public_record, workspace_api
                        try:return 200,json.dumps(public_record.read_slips(workspace_api.data_dir()/'workspace.db'),allow_nan=False).encode()
                        except Exception:return 503,b'{"error":"slips_unavailable"}'
                    def slips_sport():
                        st,body=shared(('public_record_slips',),slips_read,ttl=300)
                        return (200,json.dumps({'sport':sport,**json.loads(body)[sport]}).encode()) if st==200 else (st,body)
                    status,body=shared(('public_record_slips',sport),slips_sport,ttl=300)
                    return self.send_shared(('public_record_slips',sport),status,body,keep=300)
                if u.path != '/api/record':
                    name,ctype=('record.html','text/html; charset=utf-8') if u.path=='/record' else ('record.js','text/javascript; charset=utf-8')
                    return self.send_shared(('public_record_asset',name),200,web_file(name),ctype,keep=600)
                sport=q.get('sport','cs2')
                if sport not in ('cs2','lol','nba','nfl'):
                    return self.send(400,{'error':'unsupported sport'})
                def record_read():
                    import public_record
                    try:return 200,json.dumps(public_record.read(sys.modules[__name__],sport),allow_nan=False).encode()
                    except Exception:return 503,b'{"error":"record_unavailable"}'
                key=('public_record',sport,os.environ.get('SCOUT_RECORD_STATUS')=='1',os.environ.get('SCOUT_PUBLIC_RECORD_SLIPS')=='1',
                     os.environ.get('SCOUT_RECORD_RELIABILITY')=='1')
                status,body=shared(key,record_read,ttl=300)
                return self.send_shared(key,status,body,keep=300)
            if u.path == "/scout-ui.css":
                return self.send_shared(("asset", "scout-ui"), 200, web_file("scout-ui.css"), "text/css; charset=utf-8", keep=600)
            if u.path == "/alerts/unsubscribe":
                return self.send(*unsubscribe_page(q.get("m"), q.get("t")))
            if u.path == "/health":
                return self.send(200, {**_HEALTH_SNAPSHOT.read(_health_diagnostics), "ok": True,
                                       "commit": os.environ.get("RENDER_GIT_COMMIT", "")[:40]})
            if u.path in LANDING_PATHS and landing_route(u.path) and self.signed_out():     # signed-out visitors only
                return self.send_shared(("page", "landing"), 200, landing_page(), "text/html; charset=utf-8", keep=600)
            if (u.path == "/sports" or u.path.startswith(("/sports/", "/api/sports/"))) and self._sports_on(u.path, q):
                return self._sports_route(u, q)          # the ONE sports hook: flags off = this line does nothing
            if u.path in WORKSPACE_ROUTES and self._workspace_on(q):    # not allowed = the unknown-route answer below
                name, ctype = WORKSPACE_ROUTES[u.path]
                return self.send_shared(("workspace", name), 200, web_file(name), ctype, keep=600)
            if u.path.startswith(WORKSPACE_API) and self._workspace_on(q):
                return self._workspace_api("GET", u.path, q)
            if u.path in UPGRADE_ROUTES and self._upgrade_on(q):        # not allowed = the unknown-route answer below
                name, ctype = UPGRADE_ROUTES[u.path]
                return self.send_shared(("upgrade", name), 200, web_file(name), ctype, keep=600)
            if u.path == UI_TOKENS_ROUTE and ui_tokens_on() and self._upgrade_on(q):    # SCOUT_UI_TOKENS only
                return self.send_shared(("upgrade", "upgrade-tokens.css"), 200, web_file("upgrade-tokens.css"), "text/css; charset=utf-8", keep=600)
            if u.path == PICKEM_EV_ROUTE and os.environ.get("SCOUT_PICKEM_EV", "").strip() == "1" and self._upgrade_on(q):
                return self.send_shared(("upgrade", "upgrade-ev.js"), 200, web_file("upgrade-ev.js"), "text/javascript; charset=utf-8", keep=600)
            if u.path.startswith(UPGRADE_API) and self._upgrade_on(q):
                return self._workspace_api("GET", u.path, q, prefix=UPGRADE_API)
            if u.path in ("/", "/index.html"):
                if self._sports_on("/sports"):                          # sports nav link: SPORTS_UI only (else unchanged)
                    if self._upgrade_on(q):
                        return self.send_shared(("page", "index", "sports", "upgrade"), 200, upgrade_page(sports_nav(page()), "cs2"), "text/html; charset=utf-8", keep=600)
                    return self.send_shared(("page", "index", "sports"), 200, sports_nav(page()), "text/html; charset=utf-8", keep=600)
                if self._upgrade_on(q):
                    return self.send_shared(("page", "index", "upgrade"), 200, upgrade_page(page(), "cs2"), "text/html; charset=utf-8", keep=600)
                return self.send_shared(("page", "index"), 200, page(), "text/html; charset=utf-8", keep=600)
            if u.path == "/lol" and lol_public():                       # the member LoL page (owner's release switch)
                if self._sports_on("/sports"):
                    if self._upgrade_on(q):
                        return self.send_shared(("page", "lol", "sports", "upgrade"), 200, upgrade_page(sports_nav(web_file("lol.html")), "lol"), "text/html; charset=utf-8", keep=600)
                    return self.send_shared(("page", "lol", "sports"), 200, sports_nav(web_file("lol.html")), "text/html; charset=utf-8", keep=600)
                if self._upgrade_on(q):
                    return self.send_shared(("page", "lol", "upgrade"), 200, upgrade_page(web_file("lol.html"), "lol"), "text/html; charset=utf-8", keep=600)
                return self.send_shared(("page", "lol"), 200, web_file("lol.html"), "text/html; charset=utf-8", keep=600)
            if u.path == "/api/lol/board" and lol_public():
                if not self.allowed():
                    sess = self.member()
                    return self.send(401, {"error": "members_only", "signed_in": bool(sess), "upgrade": whop_auth.upgrade_url()})
                body = lol_member_board()
                return self.send_shared(("lolboard",), 200, body, keep=300) if body else self.send(503, {"error": "lol board not ready"})
            # ---- Sign in with Whop (paid product only). Off until the WHOP_* variables are set.
            if u.path == "/auth/me":
                sess = self.member()
                return self.send(200, {"required": whop_auth.enabled(), "signed_in": bool(sess),
                                       "member": bool(sess and sess["ok"]), "name": (sess or {}).get("name"),
                                       "upgrade": whop_auth.upgrade_url(), "slip_studio": SLIP_STUDIO, "lol": lol_public(),
                                       **({"pp_open_link": True} if PP_OPEN_LINK else {})})
            if u.path == "/auth/login" and whop_auth.enabled():
                url, pkce = whop_auth.start_login()
                return self.redirect(url, [f"{whop_auth.PKCE_COOKIE}={pkce}; Max-Age=900; Path=/; HttpOnly; Secure; SameSite=Lax"])
            if u.path == "/auth/callback" and whop_auth.enabled():
                value, msg = whop_auth.finish_login(q.get("code", ""), q.get("state", ""),
                                                     self.cookie(whop_auth.PKCE_COOKIE))
                if not value:
                    return self.redirect("/?signin_error=" + quote(msg))
                return self.redirect("/", [f"{whop_auth.COOKIE}={value}; Max-Age={whop_auth.SESSION_DAYS * 86400}; "
                                           f"Path=/; HttpOnly; Secure; SameSite=Lax",
                                           f"{whop_auth.PKCE_COOKIE}=; Max-Age=0; Path=/"])
            if u.path == "/auth/logout":
                return self.redirect("/", [f"{whop_auth.COOKIE}=; Max-Age=0; Path=/"])
            if u.path in ("/api/search", "/api/grade", "/api/board", "/api/line_check", "/api/match_compare", "/api/moneylines", "/api/veto",
                          "/api/slip_studio/batch", "/api/slip_studio/capacity", "/api/slip_studio/history", "/api/slip_studio/tails",
                          "/api/results") and not self.allowed():
                sess = self.member()
                return self.send(401, {"error": "members_only", "signed_in": bool(sess),
                                       "upgrade": whop_auth.upgrade_url()})
            if u.path == "/api/search":
                return self.search(q.get("q", "").strip())
            if u.path.startswith("/api/") and u.path not in ("/api/board", "/api/moneylines", "/api/slip_studio/batch",
                                                             "/api/slip_studio/capacity", "/api/slip_studio/history",
                                                             "/api/slip_studio/tails", "/api/results"):
                LAST_MEMBER[0] = time.time()      # research reads are cheap: they don't make the board worker step aside
            if u.path == "/api/moneylines":
                return self.moneylines()
            if u.path == "/api/slip_studio/batch":
                return self.studio_batch(q)
            if u.path == "/api/slip_studio/history":
                return self.studio_history()
            if u.path == "/api/slip_studio/capacity":
                return self.studio_capacity(q)
            if u.path == "/api/slip_studio/tails":       # PP_OPEN_LINK: the owner's Underdog slips members can tail
                if not (PP_OPEN_LINK and SLIP_STUDIO):
                    return self.send(404, {"error": "not found"})
                if not self.studio_member():
                    return self.send(401, {"error": "members_only"})
                st_, body_ = shared(("ud_tails",), lambda: (200, json.dumps(studio_tails_payload(), default=str).encode()), ttl=5)
                return self.send_shared(("ud_tails",), st_, body_, keep=5)
            if u.path == "/api/veto":                    # map veto panel of one match (Matchroom): a stored answer, no compute
                mid_ = (q.get("match") or "")[:40]
                if not re.fullmatch(r"hltv:\d{1,9}", mid_):
                    return self.send(400, {"error": "match must be an HLTV match id (hltv:<id>)"})
                st_, body_ = shared(("veto", mid_), lambda: (200, veto_body(mid_)), ttl=VETO_TTL)
                return self.send_shared(("veto", mid_), st_, body_)
            if u.path == "/api/match_compare":
                bad_ = match_compare_problem(q)
                if bad_:
                    return self.send(400, {"error": bad_})
                if not self.load_test() and not studio_rate_ok(self.rate_key(), bucket=_MC_HITS, limit=MC_PER_MIN):
                    return self.send(429, {"error": f"Too many requests - limit is {MC_PER_MIN} a minute"})
                bk_ = "prizepicks" if q.get("book") == "prizepicks" else "underdog"
                # the team names are part of the key (2026-10-13 audit: matches found on HLTV / Polymarket / PandaScore send
                # no BO3.gg slugs, so two matches with the same start shared one answer - another match's table)
                k_ = ("mcmp", q.get("team_slug"), q.get("opp_slug"), comparison._norm(q.get("team")), comparison._norm(q.get("opponent")),
                      q.get("start"), q.get("maps") or "12", bk_, (q.get("match") or "")[:120])

                def _mc():
                    return 200, json.dumps(dict(match_compare(q.get("team_slug"), q.get("opp_slug"), q.get("team", ""), q.get("opponent", ""),
                                                              q.get("start"), q.get("maps") or "12", bk_, (q.get("match") or "")[:120] or None),
                                                book=bk_), default=str).encode()
                st_, body_ = shared(k_, _mc, ttl=120)
                return self.send_shared(k_, st_, body_, keep=120)     # gzip + ETag/304 once per shared body (audit 2026-10-26)
            if u.path == "/api/line_check":
                # before a pick goes on a slip: the line Underdog shows NOW for this player / stat / maps
                book = "prizepicks" if q.get("book") == "prizepicks" else "underdog"
                try:
                    ub = prizepicks.board() if book == "prizepicks" else underdog.board()
                    lc_ = comparison.line_check(ub, q.get("player", ""), q.get("prop", "kills"), q.get("maps", "12"),
                                                q.get("line"), q.get("team"), q.get("start"), pid=q.get("pid") or None,
                                                id_map=ud_id_map(ub["rows"]) if book == "underdog" else {},
                                                book=book, odds_type=q.get("lt") or "standard",
                                                side=q.get("side") or None, opponent=q.get("opponent") or None)
                    # the slip lock rule (earlier of the platform's current start and the graded match's)
                    checked = slip_lock(lc_, q.get("start"), (q.get("ms") or "")[:40] or None, (q.get("lk") or "")[:40] or None)
                    offer_id = q.get("rid")
                    if checked.get("status") == "verified" and not offer_id:
                        # Single-player cards have no saved board row id. Resolve
                        # only the unique exact offer the existing check verified;
                        # the adapter still joins its server-owned canonical IDs.
                        ids = {str(r["id"]) for r in checked.get("rows", [])
                               if r.get("id") and r.get("line") == float(q["line"])
                               and r.get("side_offered") is True and not r.get("alt")
                               and (r.get("odds_type") or "standard") == "standard"}
                        if len(ids) == 1:
                            offer_id = ids.pop()
                    if checked.get("status") == "verified" and offer_id:
                        checked["line_advantages"] = slip_line_advantages([{
                            "row_id": offer_id, "book": book, "nick": q.get("player"),
                            "prop": q.get("prop", "kills"), "scope": q.get("maps", "12"),
                            "line": float(q["line"]), "side": q.get("side"), "player_id": q.get("pid")}])
                    return self.send(200, checked)
                except Exception:
                    return self.send(200, comparison.line_check(None, "", "", "", failed=True, book=book))
            if u.path == "/api/grade":
                with _MG_LOCK:
                    MEMBER_GRADES[0] += 1
                try:
                    return self.grade(q)
                finally:
                    with _MG_LOCK:
                        MEMBER_GRADES[0] -= 1
            if u.path == "/api/results":
                # the stored, immutable P1 scorecard export (learning cycle), summarised for members - shared by every
                # member for 5 minutes; never recomputes a forecast
                st_, body_ = shared(("results",), lambda: (200, json.dumps(results_payload(), default=str).encode()), ttl=300)
                return self.send_shared(("results",), st_, body_, keep=300)
            if u.path == "/api/board":
                # one build + encoding shared by every member for BOARD_TTL s (2026-10-13 load test: 700 members at once
                # each rebuilt and encoded the whole board; the local test server refused half the connections meanwhile)
                bk_ = "prizepicks" if q.get("book") == "prizepicks" else "underdog"
                # lc-1 (letter_containment): a held market shows Lean, 0U and the banner - display only, BOARD untouched
                st_, body_ = shared(("board", bk_), lambda: (200, json.dumps(letter_containment.cs2_board(
                    with_ud_links(board_payload(bk_)) if PP_OPEN_LINK and bk_ == "underdog" else board_payload(bk_)),
                    default=str).encode()), ttl=BOARD_TTL)
                return self.send_shared(("board", bk_), st_, body_)
            if u.path == "/admin":
                return self.admin(q)
            if u.path == "/admin/thunderpick.json" and self.authed(q.get("token")):
                # admin only, READ-ONLY (2026-10-13: a GET of this view never sends an upstream request - the old
                # ?probe= moved to /admin/thunderpick_probe): status, request audit, market registry, inventory, never
                # the key
                if q.get("compare") == "1":     # old source vs Thunderpick on identical saved inputs (no upstream call)
                    import market_odds
                    return self.send(200, _tp_redact(market_odds.compare(_tp_compare_rows())))
                if q.get("unmatched") == "1":   # coverage: current events that feed nothing, and why (no upstream call)
                    return self.send(200, _tp_redact(_tp_unmatched()))
                return self.send(200, _tp_redact(tp_admin_view(min(int(q["samples"]), 20) if (q.get("samples") or "").isdigit() else 3)))
            if u.path == "/admin/thunderpick_probe" and self.authed(q.get("token")):
                # the explicit admin action: ONE controlled request through thunderpick's gateway (its persisted cooldown,
                # single in-flight lease and the one shared budget for all Thunderpick traffic decide whether it is sent)
                if q.get("path") not in ("cs2", "csgo"):
                    return self.send(400, {"error": "path must be cs2 or csgo"})
                return self.send(200, _tp_redact(tp_probe(q["path"])))
            if u.path == "/admin/moneylines_status" and self.authed(q.get("token")):
                try:
                    import ml_sides
                    q["_sides"] = ml_sides.status()
                except Exception as e:
                    q["_sides"] = {"error": str(e)[:80]}
                return self.send(200, moneylines_status())
            if u.path == "/admin/ml_store.json" and self.authed(q.get("token")):
                import ml_store                           # read-only: store state + alerts, or the bounded unified lookup
                return self.send(*ml_store.admin_lookup(q))
            if u.path == "/admin/ml_eval" and self.authed(q.get("token")):
                return self.send(200, ml_eval_admin(q.get("refresh") == "1"))
            if u.path == "/admin/ud_tails" and PP_OPEN_LINK and self.authed(q.get("token")):
                return self.send(200, admin_ud_tails_page(q.get("token")).encode(), "text/html; charset=utf-8",
                                 {"Cache-Control": "no-store"})
            if u.path == "/admin/slips.json" and self.authed(q.get("token")):
                return self.send(200, {"builder": slips.BUILDER_VERSION, "data_dir": str(DATA_DIR), "hltv_roles": roles.hltv_status(),
                                       "recommended": slips.read(slips.RECOMMENDED), "loaded": slips.read(slips.LOADED)})
            if u.path == "/admin/slip_batches.json" and self.authed(q.get("token")):
                return self.send(200, {"version": slip_batches.VERSION, "batches": slip_batches.read()})
            if u.path == "/admin/diagnostics.json" and self.authed(q.get("token")):
                import diagnostics                        # admin-only diagnostic dashboard (flags, never causes)
                # bounded pages, never the whole forecast history with inputs in memory (2026-10-21 audit 0)
                asked = time.time()
                with _DIAG_LOCK:
                    stale = not _DIAG.get("at") or time.time() - _DIAG["at"] > 600 or (q.get("fresh") and _DIAG["at"] <= asked)
                    if stale:
                        _DIAG.update(data=diagnostics.build_from_log(picklog, slip_batches.read()), at=time.time())
                extra = {"closing_lines": closing_lines_status()} if os.environ.get("SCOUT_CLOSING_LINES") == "1" else {}
                return self.send(200, dict(_DIAG["data"], map1_results=map1_results_status(), map_chance_check=map_chance_check_status(),
                                           letter_containment=letter_containment.snapshot(), chance_calibration=esports_cal.status(), **extra))
            if u.path in ("/content-book", "/content-book.md"):
                # the video creator's content book (owner 2026-10-19): the 1,000-idea library + today's real lines, built
                # on every load from verified saved forecasts (posts.drafts). Admin token, or CONTENT_BOOK_KEY - a separate
                # read-only key for this page only (never the admin token). Unknown key = 404 (the page is not advertised).
                import hmac
                ck = os.environ.get("CONTENT_BOOK_KEY") or ""
                if not (self.authed(q.get("token")) or (ck and token_eq(q.get("key"), ck))):
                    return self.send(404, {"error": "not found"})
                import content_book
                if u.path.endswith(".md"):
                    return self.send(200, content_book.markdown().encode(), "text/markdown; charset=utf-8",
                                     {"Content-Disposition": 'attachment; filename="1time-content-book.md"'})
                st_, body_ = shared(("content_book",), lambda: (200, content_book_page().encode()), ttl=60)
                return self.send(st_, body_, "text/html; charset=utf-8")
            if u.path == "/admin/diagnostics" and self.authed(q.get("token")):
                return self.send(200, (WEB / "diagnostics.html").read_bytes(), "text/html; charset=utf-8")
            if u.path in ("/admin/coverage", "/admin/coverage.json") and self.authed(q.get("token")):
                import admin_coverage                     # SCOUT_ADMIN_COVERAGE (default off): health of every source
                if not admin_coverage.enabled():
                    return self.send(404, {"error": "not found"})
                st_, body_ = admin_coverage.shared(sys.modules[__name__])
                if u.path.endswith(".json"):
                    return self.send(st_, body_, "application/json", {"Cache-Control": "no-store"})
                return self.send(200, admin_coverage.page(json.loads(body_)).encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            if u.path in ("/admin/alerts_dryrun", "/admin/alerts_dryrun.json") and self.authed(q.get("token")):
                import admin_coverage, workspace_alerts, workspace_api   # read-only: nothing is written or sent
                doc = workspace_alerts.preview(workspace_api.data_dir(), workspace_api.store().get_state,
                                               workspace_alerts.live_rows(sys.modules[__name__]))
                if u.path.endswith(".json"):
                    return self.send(200, doc, headers={"Cache-Control": "no-store"})
                return self.send(200, admin_coverage.dryrun_page(doc).encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            if u.path == "/admin/memprof.json" and self.authed(q.get("token")):
                return self.send(200, memprof(q.get("action") or "report"))
            if u.path == "/admin/lol" and self.authed(q.get("token")):    # admin LoL board / match tables / cards (not released)
                return self.send(200, (WEB / "lol_admin.html").read_bytes(), "text/html; charset=utf-8")
            if (u.path == "/admin/lol.json" or u.path.startswith("/admin/lol/")) and self.authed(q.get("token")):
                return self.send(200, lol_center_admin(u.path, q))     # LoL data center (backend only, not released)
            if (u.path in ("/admin/learning", "/admin/learning.json") or u.path.startswith("/admin/learning/")) and self.authed(q.get("token")):
                import learning                           # continuous measurement dashboard (shadow; never changes a grade)
                return self.send(*learning.http(u.path, q, WEB))
            if u.path == "/admin/comparison_audit" and self.authed(q.get("token")):
                # ONE cohort at ONE timestamp: every Underdog offer now, and for every graded board line whether the
                # comparison finds its own offer (by id / name) or why not
                ub = underdog.board(); rows = ub["rows"]; idm = ud_id_map(rows)
                out = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "board_fetched": ub.get("at"),
                       "underdog_offers": len(rows), "standard": sum(not r["alt"] for r in rows), "alternate": sum(bool(r["alt"]) for r in rows),
                       "graded_lines": 0, "missing_player_id": 0, "missing_match": 0, "matched_by_id": 0, "matched_by_name": 0,
                       "not_matched": 0, "rejected_reasons": {}, "examples": []}
                for r in rows:
                    gb = BOARD.get(r["id"]) or {}
                    if r["alt"] or not gb.get("side"):
                        continue
                    out["graded_lines"] += 1
                    if not gb.get("player_id"):
                        out["missing_player_id"] += 1
                    if not gb.get("match_start"):
                        out["missing_match"] += 1
                    res_ = {"grade": {"prop": r["prop"], "scope": r.get("scope") or "12"},
                            "player_block": {"player": {"nick": gb.get("nick") or r["player"], "id": gb.get("player_id")}, "playing_for": gb.get("team_name")},
                            "next_match": {"start": gb.get("match_start"), "opponent": gb.get("opp_name")}}
                    offers, rej = comparison.match_offers(res_, rows, id_map=idm)
                    own = next((o for o in offers if o["id"] == r["id"]), None)
                    if own:
                        out["matched_by_" + own["identity"]] += 1
                    else:
                        out["not_matched"] += 1
                        why = [x[1] for x in rej if x[0] == r["line"]] or ["no offer found (name / id)"]
                        k = why[0].split(" (")[0]
                        out["rejected_reasons"][k] = out["rejected_reasons"].get(k, 0) + 1
                        if len(out["examples"]) < 15:
                            out["examples"].append({"player": r["player"], "line": r["line"], "prop": r["prop"], "why": why[0]})
                return self.send(200, out)
            if u.path == "/admin/comparisons.json" and self.authed(q.get("token")):
                # streamed: only the last n lines are ever held (2026-10-26 audit: the whole file was read per call and
                # ?last= had no ceiling)
                n = qint(q, "last", 500, 1, 5000)
                tail, total = collections.deque(maxlen=n), 0
                try:
                    with open(comparison.snapshot_path(), encoding="utf-8") as f:
                        for x in f:
                            total += 1
                            tail.append(x)
                except OSError:
                    pass
                return self.send(200, {"version": comparison.VERSION, "total": total, "sources": comparison.SOURCES,
                                       "snapshots": [json.loads(x) for x in tail if x.strip()]})
            if u.path == "/admin/slip_batch_results.json" and self.authed(q.get("token")):
                new = slip_batches.settle_all(slip_batches.leg_result_from_picks(picklog.settlement_rows()))
                return self.send(200, {"version": slip_batches.VERSION, "new": len(new), "results": slip_batches.results(),
                                       "payout_note": "standard 3x/6x/10x and x Underdog multipliers are ASSUMPTIONS, not real entries"})
            if u.path == "/admin/forecasts.json" and self.authed(q.get("token")):
                # capped at one page's worth (2026-10-21 audit 0: no ?last= loaded the whole history, with inputs too);
                # the full history is /admin/forecasts_page.json
                cap = picklog.PAGE_MAX[bool(q.get("inputs"))]
                last = min(int(q["last"]), cap) if (q.get("last") or "").isdigit() and int(q["last"]) > 0 else cap
                return self.send(200, {"forecasts": picklog.export_forecasts(inputs=bool(q.get("inputs")), last=last),
                                       "last": last, "capped_at": cap, "full_history": "/admin/forecasts_page.json"})
            if u.path == "/admin/forecasts_page.json" and self.authed(q.get("token")):
                # bounded, cursor-paginated export (the unbounded one above restarted production on 2026-10-14)
                num = lambda k: int(q[k]) if (q.get(k) or "").isdigit() else None
                return self.send(200, picklog.forecasts_page(after_id=num("after") or 0, cutoff_id=num("cutoff"),
                                                             limit=num("limit"), inputs=q.get("inputs") == "1" and q.get('compact')!='1', compact=q.get('compact')=='1'))
            if u.path == "/admin/rule_pending.json" and self.authed(q.get("token")):
                # 2026-10-23 closeout: open picks held pending by an unpublished platform rule (bounded page, read-only)
                num = lambda k: int(q[k]) if (q.get(k) or "").isdigit() else None
                return self.send(200, picklog.rule_pending_page(after_id=num("after") or 0, limit=num("limit")))
            if u.path == "/admin/post_drafts.json" and self.authed(q.get("token")):
                # publication drafts built ONLY from saved board forecasts of lines offered now (posts.py, owner 2026-10-16)
                import posts
                bk_ = "prizepicks" if q.get("book") == "prizepicks" else "underdog"
                out = posts.drafts(board_payload(bk_)["rows"], bk_, picklog.DB, min_grade=q.get("min") or "B-")
                try:                                            # the grader's own record at real offered lines (P1)
                    import learning
                    pr_ = ((learning.dashboard().get("scorecard") or {}).get("product") or {})
                    out["grader_record_at_real_lines"] = {"recommended": pr_.get("recommended"), "units": pr_.get("units"),
                                                          "matches": pr_.get("matches")}
                except Exception:
                    out["grader_record_at_real_lines"] = None
                return self.send(200, out)
            if u.path == "/admin/fh1.json" and self.authed(q.get("token")):
                # fh-1 shadow (perf_shadow): status + a bounded page of its records; replay=1 re-runs a page from saved inputs
                num = lambda k: int(q[k]) if (q.get(k) or "").isdigit() else None
                if q.get("replay") == "1":
                    return self.send(200, picklog.shadow_replay_check(after_id=num("after") or 0, limit=num("limit") or 100))
                return self.send(200, {"status": perf_shadow.status(),
                                       "page": picklog.shadow_page(after_id=num("after") or 0, cutoff_id=num("cutoff"),
                                                                   limit=num("limit"), inputs=q.get("inputs") == "1")})
            if u.path == "/admin/picks.json" and self.authed(q.get("token")):
                # bounded keyset pages (2026-10-26 audit: the whole pick log, factors included, in one response); page
                # with ?after=<next_after>&cutoff=<cutoff_id> until done
                num = lambda k: int(q[k]) if (q.get(k) or "").isdigit() else None
                return self.send(200, picklog.picks_page(after_id=num("after") or 0, cutoff_id=num("cutoff"), limit=num("limit")))
            if u.path == "/admin/mark_tests" and self.authed(q.get("token")):
                ids = [int(x) for x in (q.get("ids") or "").split(",") if x.strip().isdigit()]
                picklog.mark_tests(ids)
                return self.send(200, {"marked": len(ids), "pushes_fixed": picklog.resettle_pushes()})
            if u.path == "/admin/whop_check" and self.authed(q.get("token")):
                # read-only health of the member gate for NEW and existing members (no names, emails or tokens returned)
                import requests as _rq
                H_ = {"Authorization": f"Bearer {os.environ.get('WHOP_API_KEY', '')}"}
                out = {"enabled": whop_auth.enabled(), "client_id_set": bool(os.environ.get("WHOP_CLIENT_ID")),
                       "api_key_set": bool(os.environ.get("WHOP_API_KEY")), "secret_set": bool(os.environ.get("WHOP_CLIENT_SECRET")),
                       "session_secret_set": bool(os.environ.get("SESSION_SECRET")), "products": whop_auth.products(),
                       "paid_plans": sorted(whop_auth.paid_plans()), "callback": whop_auth.public_url() + "/auth/callback",
                       "join_url": whop_auth.upgrade_url(), "recent_errors": whop_auth.LAST_ERRORS[-5:], "plans": {}}
                try:                                    # the user_id filter must really filter
                    r = _rq.get(whop_auth.MEMBERSHIPS, params={"user_id": "user_doesnotexist0000", "first": 5}, headers=H_, timeout=20)
                    out["fake_user_rows"] = len(r.json().get("data") or []) if r.status_code == 200 else f"HTTP {r.status_code}"
                except Exception as e:
                    out["fake_user_rows"] = type(e).__name__
                try:
                    j = _rq.get(whop_auth.JOIN_URL, timeout=20, allow_redirects=True)
                    out["join_url_status"] = j.status_code
                except Exception as e:
                    out["join_url_status"] = type(e).__name__
                sample_user = None
                for plan in sorted(whop_auth.paid_plans()):
                    st = {}
                    try:
                        r = _rq.get(whop_auth.MEMBERSHIPS, params={"plan_id": plan, "first": qint(q, "n", 10, 1, 100)}, headers=H_, timeout=20)
                        if r.status_code != 200:
                            out["plans"][plan] = f"HTTP {r.status_code}: {whop_auth._why(r)}"; continue
                        for m in r.json().get("data") or []:
                            uid = whop_auth._row_user(m)
                            sample_user = sample_user or uid
                            live = m.get("status") in whop_auth.LIVE_STATUSES or (plan == whop_auth.LIFETIME_PLAN and m.get("status") == "completed")
                            allowed = whop_auth.has_access(uid, force=True) if uid else None
                            k = f"{m.get('status')}: {'allowed' if allowed else 'denied'}"
                            st[k] = st.get(k, 0) + 1
                            if live and not allowed:
                                st["LIVE BUT DENIED"] = st.get("LIVE BUT DENIED", 0) + 1
                    except Exception as e:
                        st = {"error": type(e).__name__}
                    out["plans"][plan] = st
                if sample_user:                         # a real member's lookup returns only that member's rows
                    try:
                        r = _rq.get(whop_auth.MEMBERSHIPS, params={"user_id": sample_user, "first": 50}, headers=H_, timeout=20)
                        rows = r.json().get("data") or []
                        out["one_user_rows"] = len(rows)
                        out["one_user_other_rows"] = sum(1 for m in rows if whop_auth._row_user(m) != sample_user)
                    except Exception as e:
                        out["one_user_rows"] = type(e).__name__
                return self.send(200, out)
            if u.path == "/admin/whop_plan" and self.authed(q.get("token")) and whop_auth.enabled():
                # private: members on one plan and whether the grader lets each in (Whop read-only)
                import requests as _rq
                r = _rq.get(whop_auth.MEMBERSHIPS, timeout=20,
                            params={"plan_id": q.get("plan", ""), "first": qint(q, "n", 15, 1, 100)},
                            headers={"Authorization": f"Bearer {os.environ.get('WHOP_API_KEY', '')}"})
                if r.status_code != 200:
                    return self.send(200, {"whop_status": r.status_code, "whop_error": whop_auth._why(r)})
                rows = []
                for m in r.json().get("data") or []:
                    uid = m.get("user_id")
                    rows.append({"membership": m.get("id"), "status": m.get("status"), "plan": m.get("plan_id"),
                                 "product": m.get("product_id"),
                                 "grader_allows": whop_auth.has_access(uid, force=True) if uid else None})
                return self.send(200, {"plan": q.get("plan"), "memberships": rows})
            if u.path == "/admin/pp_history" and self.authed(q.get("token")):
                # every PrizePicks line Scout observed: openings, moves, provider-id changes, removals (prizepicks.py)
                ev = prizepicks.history(q.get("market") or None, qint(q, "limit", 500, 1, 5000))
                b = prizepicks.board()
                return self.send(200, {"feed": {k: b.get(k) for k in ("state", "at", "checked_at", "lines", "error", "ingested_at", "source")},
                                       "ingests": prizepicks.ingests(), "events": ev,
                                       "counts": dict(collections.Counter(e.get("event") for e in ev))})
            if u.path == "/admin/namesakes" and self.authed(q.get("token")):
                # read-only: every board player with 2+ HLTV players of the same name - which one the projection uses and why
                import hltv
                rows_ = board_payload()["rows"] + board_payload("prizepicks")["rows"]
                seen_, out = set(), []
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        for r in rows_:
                            nick, team = r.get("nick") or r.get("player"), r.get("team_name") or r.get("team")
                            k = (hltv._norm(nick), hltv._norm(team))
                            if k in seen_:
                                continue
                            seen_.add(k)
                            cur.execute("""SELECT ps.playerid, string_agg(DISTINCT t.name, ', '), count(*), max(mo.date)
                                           FROM players p JOIN player_stats ps USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                                           JOIN match_overview mo ON mo.matchid = ps.matchid
                                           WHERE lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = %s GROUP BY ps.playerid""",
                                        (hltv._norm(nick),))
                            cands = cur.fetchall()
                            if len(cands) < 2:
                                continue
                            chosen = hltv._player_ids(cur, nick, team)
                            variants = hltv.team_name_variants(team)
                            out.append({"player": nick, "team": team, "chosen": chosen[0] if chosen else None,
                                        "team_matched": any(hltv._norm(x) in variants for c in cands for x in str(c[1]).split(", ") if c[0] == (chosen[0] if chosen else None)),
                                        "candidates": [{"id": c[0], "teams": c[1], "rows": c[2], "last": str(c[3])} for c in cands]})
                finally:
                    con.close()
                return self.send(200, {"board_players_with_namesakes": len(out), "guessed_without_team_match": sum(1 for o in out if not o["team_matched"]), "rows": out})
            if u.path == "/admin/hltv_status" and self.authed(q.get("token")):
                return self.send(200, _hltv_status())      # database host (name only), breaker, last error, pool
            if u.path == "/admin/prop_feed" and self.authed(q.get("token")):
                # read-only look at the scraper's props tables (PrizePicks receiver): status, volume, freshness, samples
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        def rows(sql, args=None):
                            cur.execute(sql, args)
                            cols = [c[0] for c in cur.description]
                            return [dict(zip(cols, [str(v) if v is not None else None for v in r])) for r in cur.fetchall()]
                        out = {"status": rows("SELECT * FROM prop_status ORDER BY book"),
                               "counts": rows("""SELECT book, maps, market, odds_type, count(*) n, min(start_time) first_start, max(start_time) last_start,
                                                 min(updated_at) oldest_update, max(updated_at) newest_update, sum((moves > 0)::int) moved,
                                                 sum((higher IS NOT NULL)::int) with_higher
                                          FROM prop_lines GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"""),
                               "upcoming": rows("""SELECT book, count(*) n, sum((updated_at >= now() - interval '90 minutes')::int) seen_90min
                                            FROM prop_lines WHERE start_time > now() GROUP BY 1"""),
                               "sample": rows("""SELECT * FROM prop_lines WHERE book = %s AND start_time > now() ORDER BY start_time, player LIMIT %s""",
                                              (q.get("book") or "prizepicks", qint(q, "n", 12, 1, 500))),
                               "grades_columns": rows("""SELECT column_name, data_type FROM information_schema.columns
                                                  WHERE table_name = 'prop_grades' ORDER BY ordinal_position"""),
                               "grades_sample": rows("SELECT * FROM prop_grades ORDER BY 1 DESC LIMIT 3")}
                        return self.send(200, out)
                finally:
                    con.close()
            if u.path == "/admin/hltv_schema" and self.authed(q.get("token")):
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("""SELECT table_name, string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position)
                                       FROM information_schema.columns WHERE table_schema = 'public'
                                       GROUP BY table_name ORDER BY table_name""")
                        return self.send(200, {"tables": {t: c for t, c in cur.fetchall()}})
                finally:
                    con.close()
            if u.path == "/admin/disk" and self.authed(q.get("token")):
                # private: what takes space on the volume (bytes vs blocks actually allocated)
                rows = {}
                for p in Path(DATA_DIR).rglob("*"):
                    try:
                        if p.is_file():
                            st = p.stat()
                            top = p.relative_to(DATA_DIR).parts
                            k = "/".join(top[:2]) if len(top) > 2 else top[0]
                            r = rows.setdefault(k, [0, 0, 0])
                            r[0] += 1; r[1] += st.st_size; r[2] += getattr(st, "st_blocks", 0) * 512
                    except OSError:
                        pass
                du = shutil.disk_usage(DATA_DIR)
                return self.send(200, {"dir": str(DATA_DIR), "total_mb": round(du.total / 1e6), "used_mb": round(du.used / 1e6),
                                       "parts": sorted(({"path": k, "files": a, "mb": round(b / 1e6, 1), "on_disk_mb": round(c / 1e6, 1)}
                                                        for k, (a, b, c) in rows.items()), key=lambda x: -x["on_disk_mb"])[:40]})
            if u.path == "/admin/hltv_match" and self.authed(q.get("token")):
                # private, read-only: a team's HLTV matches on a date + the stored raw page (inspection)
                import gzip as _gz, hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("""SELECT DISTINCT mo.matchid, mo.event, mo.date FROM match_overview mo
                                       JOIN matches m USING (matchid) JOIN teams t ON t.teamid = m.teamid
                                       WHERE lower(t.name) = lower(%s) AND mo.date = %s ORDER BY mo.matchid""",
                                    (q.get("team", ""), q.get("date", "")))
                        rows = [{"matchid": a, "event": b, "date": str(c)} for a, b, c in cur.fetchall()]
                        for r in rows:
                            cur.execute("SELECT url, html_gz FROM raw_pages WHERE matchid = %s", (r["matchid"],))
                            pg = cur.fetchall()
                            r["pages"] = [{"url": u_, "html": _gz.decompress(bytes(h)).decode("utf-8", "replace")
                                           if q.get("html") else None, "bytes": len(h or b"")} for u_, h in pg]
                        return self.send(200, {"matches": rows})
                finally:
                    con.close()
            if u.path == "/admin/recheck_hltv" and self.authed(q.get("token")):
                changed = picklog.recheck_with_hltv()
                return self.send(200, {"changed": sum(1 for x in changed if not x.get("review")),
                                       "review": sum(1 for x in changed if x.get("review")), "rows": changed})
            if u.path == "/admin/ud_moves" and self.authed(q.get("token")):
                import sqlite3
                db = BOARD_FILE.parent / "ud_moves.db"
                if not db.exists():
                    return self.send(200, {"rows": []})
                # bounded pages (2026-10-26 audit: ?since= with no end returned the whole table): rows in insertion order,
                # page on with ?after=<next_after>; done = nothing more after this page
                lim_, aft_ = qint(q, "limit", 5000, 1, 5000), qint(q, "after", 0, 0)
                con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
                try:
                    got = con.execute("SELECT rowid, * FROM moves WHERE at >= ? AND rowid > ? ORDER BY rowid LIMIT ?",
                                      (q.get("since", ""), aft_, lim_)).fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": [list(r[1:]) for r in got], "next_after": got[-1][0] if got else aft_,
                                       "done": len(got) < lim_, "limit": lim_})
            if u.path == "/admin/hltv_env_rows" and self.authed(q.get("token")):
                # private analysis: every player-map since `since` with LAN / online (match_overview.lan)
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT p.name, t.name, mo.date::text, mo.lan, mo.event, mo.matchid, ps.k, own.score + opp.score
                                       FROM player_stats ps JOIN players p USING (playerid) JOIN teams t ON t.teamid = ps.teamid
                                       JOIN match_overview mo ON mo.matchid = ps.matchid
                                       JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                                       JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                                       JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                                       WHERE ps.sideid = 0 AND mo.date >= %s""", (q.get("since", "2025-09-01"),))
                        rows = cur.fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": rows})
            if u.path == "/admin/hltv_hs_rows" and self.authed(q.get("token")):
                # private analysis: every player-map with kills, headshots, rounds and the map (played maps only)
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT mo.date::text, ps.playerid, mp.name, ps.k, h.hs, own.score + opp.score, mo.matchid
                                       FROM player_stats ps JOIN match_overview mo ON mo.matchid = ps.matchid
                                       JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                                       JOIN maps mp ON mp.mapid = ps.mapid
                                       JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
                                       JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                                       JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                                       WHERE ps.sideid = 0 AND h.hs IS NOT NULL AND mo.date >= %s""", (q.get("since", "2025-09-01"),))
                        rows = cur.fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": rows})
            if u.path == "/admin/hltv_team_maps" and self.authed(q.get("token")):
                # private analysis: every map since `since` - date, event, number, map, both teams + scores
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 60000")
                        cur.execute("""SELECT mo.matchid, mo.date::text, mo.event, mm.number, mp.name, t1.name, a.score,
                                              t2.name, b.score
                                       FROM match_maps mm JOIN match_overview mo USING (matchid) JOIN maps mp ON mp.mapid = mm.mapid
                                       JOIN matches a ON a.matchid = mm.matchid AND a.mapid = mm.mapid AND a.sideid = 0
                                       JOIN matches b ON b.matchid = mm.matchid AND b.mapid = mm.mapid AND b.sideid = 0
                                                      AND b.teamid > a.teamid
                                       JOIN teams t1 ON t1.teamid = a.teamid JOIN teams t2 ON t2.teamid = b.teamid
                                       WHERE mo.date >= %s ORDER BY mo.date, mo.matchid, mm.number""",
                                    (q.get("since", "2025-09-01"),))
                        rows = cur.fetchall()
                finally:
                    con.close()
                return self.send(200, {"rows": rows})
            if u.path == "/admin/hltv_rank_rows" and self.authed(q.get("token")):
                # private analysis: every player-map (total side) with both teams' VRS rank at the time
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 60000")
                        cur.execute("""
                            SELECT mo.matchid, mo.date::text, ps.playerid, ps.teamid, opp.teamid, ps.mapid, ps.k,
                                   own.score, opp.score, mm.number,
                                   (SELECT rank FROM team_rankings tr WHERE tr.teamid = ps.teamid ORDER BY abs(tr.date - mo.date) LIMIT 1),
                                   (SELECT rank FROM team_rankings tr WHERE tr.teamid = opp.teamid ORDER BY abs(tr.date - mo.date) LIMIT 1)
                            FROM player_stats ps
                            JOIN match_overview mo USING (matchid)
                            JOIN matches own ON own.matchid = ps.matchid AND own.teamid = ps.teamid AND own.mapid = ps.mapid AND own.sideid = 0
                            JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                            LEFT JOIN match_maps mm ON mm.matchid = ps.matchid AND mm.mapid = ps.mapid
                            WHERE ps.sideid = 0 AND ps.mapid <> 0 AND mo.date >= current_date - %s""", (qint(q, "days", 120, 1, 3650),))
                        rows = cur.fetchall()
                    return self.send(200, {"rows": rows})
                finally:
                    con.close()
            if u.path == "/admin/hltv_player_opps" and self.authed(q.get("token")):
                # private: a player's HLTV opponents (12 months) with their latest rank
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("""SELECT p.playerid, p.name, count(*) FROM players p JOIN player_stats ps USING (playerid)
                                       WHERE lower(regexp_replace(p.name, '[^a-zA-Z0-9]', '', 'g')) = %s GROUP BY 1, 2""",
                                    (hltv._norm(q.get("nick")),))
                        cands = cur.fetchall()
                        ids = hltv._player_ids(cur, q.get("nick"), q.get("team"))
                        cur.execute("""WITH ranks AS (SELECT DISTINCT ON (teamid) teamid, rank FROM team_rankings ORDER BY teamid, date DESC)
                                       SELECT t2.name, rk.rank, count(*), min(mo.date)::text, max(mo.date)::text
                                       FROM player_stats ps JOIN match_overview mo USING (matchid)
                                       JOIN matches opp ON opp.matchid = ps.matchid AND opp.teamid <> ps.teamid AND opp.mapid = ps.mapid AND opp.sideid = 0
                                       JOIN teams t2 ON t2.teamid = opp.teamid LEFT JOIN ranks rk ON rk.teamid = opp.teamid
                                       WHERE ps.playerid = ANY(%s) AND ps.sideid = 0 AND ps.mapid <> 0
                                       GROUP BY 1, 2 ORDER BY 2 NULLS LAST""", (ids,))
                        opps = cur.fetchall()
                        cur.execute("SELECT min(date)::text, max(date)::text, count(*) FROM match_overview")
                        span = cur.fetchone()
                    return self.send(200, {"candidates": cands, "ids_used": ids, "opponents": opps, "db_span": span})
                finally:
                    con.close()
            if u.path == "/admin/hltv_status" and self.authed(q.get("token")):
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        out = {}
                        for lab, sql in (
                            ("matches_45d", "SELECT count(*) FROM match_overview WHERE date >= current_date - 45"),
                            ("matches_7d", "SELECT count(*) FROM match_overview WHERE date >= current_date - 7"),
                            ("maps_45d", "SELECT count(*) FROM match_maps mm JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 45"),
                            ("maps_with_hs_45d", "SELECT count(DISTINCT h.mapstatsid) FROM map_headshots h JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 45"),
                            ("maps_with_hs_7d", "SELECT count(DISTINCT h.mapstatsid) FROM map_headshots h JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 7"),
                            ("maps_7d", "SELECT count(*) FROM match_maps mm JOIN match_overview mo USING (matchid) WHERE mo.date >= current_date - 7"),
                            ("hs_failures", "SELECT count(*) FROM mapstats_status"),
                            ("teams", "SELECT count(*) FROM teams"),
                            ("newest_match", "SELECT max(date)::text FROM match_overview")):
                            try:
                                cur.execute(sql); out[lab] = cur.fetchone()[0]
                            except Exception as e:
                                con.rollback() if not con.autocommit else None
                                out[lab] = f"error: {str(e)[:80]}"
                        cur.execute("SELECT last_error, count(*) FROM mapstats_status GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
                        out["hs_failure_reasons"] = cur.fetchall()
                        return self.send(200, out)
                finally:
                    con.close()
            if u.path == "/admin/veto.json" and self.authed(q.get("token")):
                # veto-1 serving: status | records (paged, <= 200) | score (prospective, cached 10 min) | store | rebuild
                import veto_serve
                return self.send(200, veto_serve.admin(q))
            if u.path == "/admin/veto_backtest" and self.authed(q.get("token")):
                import hltv
                return self.send(200, hltv.veto_backtest(qint(q, "days", 30, 1, 365), qint(q, "limit", 250, 1, 2000)))
            if u.path == "/admin/hltv_maps" and self.authed(q.get("token")):
                import hltv
                items = json.loads(q.get("items") or "[]")[:80]      # [[nick, team, day], ...]
                out = []
                for n, t, dday in items:
                    try:
                        out.append({"nick": n, "team": t, "day": dday, "maps": hltv.match_maps_detail(n, t, dday)})
                    except Exception as e:
                        out.append({"nick": n, "team": t, "day": dday, "error": str(e)[:200]})
                return self.send(200, {"rows": out})
            if u.path == "/admin/fix_scope_settlements" and self.authed(q.get("token")):
                # 2026-10-08 incident: dry run by default; apply=1 corrects and logs old + new values
                # scopes=12 (Maps 1-2) is DRY RUN ONLY: a read-only replay of the new shared settlement path
                sc = tuple((q.get("scopes") or "1,123").split(","))
                return self.send(200, picklog.fix_scope_settlements(apply=q.get("apply") == "1" and "12" not in sc, scopes=sc,
                                                                    test=int(q["test"]) if (q.get("test") or "").isdigit() else None))
            if u.path == "/admin/settlement_audit" and self.authed(q.get("token")):
                # 2026-10-12 identity fix: re-derive EVERY settled pick (Maps 1-2, Map 1, Maps 1-3) through the canonical-id
                # settlement path. Dry run unless apply=1; corrections append old + new values to settlement_corrections.jsonl
                # 2026-10-12 owner review: until=<settled_at ISO> / ids=1,2,3 freeze the cohort; picks HLTV can't verify
                # are checked against BO3.gg's full scoreboard; summary.line = "N wrong among V verified; U unresolved"
                sb = BO3(background=True)
                try:
                    return self.send(200, picklog.fix_scope_settlements(
                        apply=q.get("apply") == "1", scopes=("12", "1", "123"),
                        test=int(q["test"]) if (q.get("test") or "").isdigit() else None, until=q.get("until") or None,
                        ids=[i for i in (q.get("ids") or "").split(",") if i.strip().isdigit()] or None,
                        bo3=sb, map_rows=cs2_scout.map_rows))
                finally:
                    sb.close()
            if u.path == "/admin/perf_check" and self.authed(q.get("token")):
                # performance model on LIVE data for given players (to prove it equals the training export)
                import perf_live
                from datetime import date as _d
                items = json.loads(q.get("items") or "[]")[:40]   # [[nick, team, opponent, lan, scope, prop, day], ...]
                out = []
                for nick, team, opp, lan, sc, prop, day in items:
                    try:
                        out.append(perf_live.projection(nick, team, opp, lan, sc, prop, _d.fromisoformat(day).toordinal()))
                    except Exception as e:
                        out.append({"error": str(e)[:200]})
                return self.send(200, {"rows": out})
            if u.path == "/admin/hltv_export" and self.authed(q.get("token")):
                # performance-model dataset (2026-10-08 rebuild): one row per player per map, totals side, with the
                # map's rounds / result, map order, LAN flag, event, both teams; date window <= 31 days per call
                import hltv
                win_, bad_ = qdates(q)
                if bad_:
                    return self.send(400, {"error": bad_})
                d0, d1 = win_
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT mo.matchid, mo.date, mo.event, mo.lan, mm.number, mp.name, ps.playerid, ps.teamid, opp.teamid,
                                              ps.k, ps.d, ps.ek, ps.ed, ps.adr, ps.eadr, ps.kast, ps.ekast, ps.rating, ps.roundswing, h.hs,
                                              own.score, opp.score
                                       FROM match_maps mm
                                       JOIN match_overview mo USING (matchid)
                                       JOIN maps mp ON mp.mapid = mm.mapid
                                       JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
                                       JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
                                       JOIN matches opp ON opp.matchid = mm.matchid AND opp.teamid <> ps.teamid AND opp.mapid = mm.mapid AND opp.sideid = 0
                                       LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
                                       WHERE mo.date >= %s AND mo.date <= %s
                                       ORDER BY mo.date, mo.matchid, mm.number""", (d0, d1))
                        cols = ["match", "date", "event", "lan", "map_no", "map", "player", "team", "opp", "k", "d", "ek", "ed",
                                "adr", "eadr", "kast", "ekast", "rating", "swing", "hs", "own", "opp_score"]
                        rows = [[str(v) if k == "date" else v for k, v in zip(cols, r)] for r in cur.fetchall()]
                        cur.execute("SELECT playerid, name FROM players")
                        names = dict(cur.fetchall())
                        cur.execute("SELECT teamid, name, country FROM teams")
                        teams = {t: [n, c] for t, n, c in cur.fetchall()}
                        return self.send(200, {"cols": cols, "rows": rows, "players": names, "teams": teams})
                finally:
                    con.close()
            if u.path == "/admin/hltv_sides" and self.authed(q.get("token")):
                # COLLECTION ONLY (2026-10-09): per-side player rows (sideid 1/2 as stored by the scraper) and every
                # per-side score row, for the CT/T factor test; date window <= 31 days per call; never used in grading
                import hltv
                win_, bad_ = qdates(q)
                if bad_:
                    return self.send(400, {"error": bad_})
                d0, d1 = win_
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 120000")
                        cur.execute("""SELECT mo.matchid, mo.date, mm.number, ps.playerid, ps.teamid, ps.sideid, ps.k, ps.d, ps.adr, ps.kast, ps.rating
                                       FROM match_maps mm JOIN match_overview mo USING (matchid)
                                       JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid IN (1, 2)
                                       WHERE mo.date >= %s AND mo.date <= %s ORDER BY mo.date, mo.matchid, mm.number""", (d0, d1))
                        prow = [[str(v) if i == 1 else v for i, v in enumerate(r)] for r in cur.fetchall()]
                        cur.execute("""SELECT m.matchid, mm.number, m.teamid, m.sideid, m.score
                                       FROM matches m JOIN match_maps mm ON mm.matchid = m.matchid AND mm.mapid = m.mapid
                                       JOIN match_overview mo ON mo.matchid = m.matchid
                                       WHERE mo.date >= %s AND mo.date <= %s""", (d0, d1))
                        srow = cur.fetchall()
                    return self.send(200, {"player_cols": ["match", "date", "map_no", "player", "team", "sideid", "k", "d", "adr", "kast", "rating"],
                                           "player_rows": prow, "score_cols": ["match", "map_no", "team", "sideid", "score"], "score_rows": srow})
                finally:
                    con.close()
            if u.path == "/admin/role_snapshots" and self.authed(q.get("token")):
                import roles as _roles
                try:
                    with open(_roles.SNAP, encoding="utf-8") as f:
                        lines = f.readlines()
                except OSError:
                    lines = []
                days = [json.loads(x)["day"] for x in lines if x.strip()]
                if q.get("day"):
                    return self.send(200, next((json.loads(x) for x in lines if x.strip() and json.loads(x)["day"] == q["day"]), {}))
                return self.send(200, {"days": days})
            if u.path == "/admin/hltv_ranks" and self.authed(q.get("token")):
                import hltv
                con = hltv._connect()
                try:
                    with con.cursor() as cur:
                        cur.execute("SET statement_timeout = 60000")
                        cur.execute("SELECT date, teamid, rank, points FROM team_rankings ORDER BY date")
                        return self.send(200, {"rows": [[str(d), t, r, p] for d, t, r, p in cur.fetchall()]})
                finally:
                    con.close()
            if u.path == "/admin/hltv_history" and self.authed(q.get("token")):
                import hltv
                items = json.loads(q.get("items") or "[]")[:60]      # [[nick, cutoff, team], ...]
                return self.send(200, {"rows": [dict(hltv.history(n, c, t), nick=n, cutoff=c) for n, c, t in items]})
            if u.path == "/admin/reopen" and self.authed(q.get("token")):
                ids = [int(x) for x in (q.get("ids") or "").split(",") if x.strip().isdigit()]
                picklog.reopen(ids)
                return self.send(200, {"reopened": len(ids)})
            if u.path == "/admin/settle_now" and self.authed(q.get("token")):
                sb = BO3(background=True)
                try:
                    n = picklog.settle(sb, cs2_scout.map_rows)
                finally:
                    sb.close()
                return self.send(200, {"settled": n})
            if u.path == "/admin/timings" and self.authed(q.get("token")):
                return self.send(200, {"member_grades_now": MEMBER_GRADES[0], "board_size": len(BOARD),
                                       "threads": [t.name for t in threading.enumerate()],
                                       "requests": list(REQ_TIMES)[-qint(q, "n", 60, 1):],
                                       "search_wait": list(SEARCH_TIMES)[-qint(q, "s", 1, 1):] if q.get("s") else [],
                                       "recent": list(cs2_scout.TIMINGS)[-qint(q, "n", 60, 1):]})
            if u.path == "/admin/probe" and self.authed(q.get("token")):
                # private: raw shape of the backup data sources (keys never included)
                import oddspapi
                return self.send(200, {"oddspapi": oddspapi.probe()})
            if u.path == "/admin/oddspapi_markets" and self.authed(q.get("token")):
                import oddspapi                           # read-only source inventory (2026-10-09)
                return self.send(200, oddspapi.markets_inventory(qint(q, "n", 3, 1, 20)))
            return self.send(404, {"error": "not found"})
        except CLIENT_GONE:
            self.close_connection = True                # the client left: not a server failure, nothing more to send
        except Exception as e:
            if self._sent:                              # part of a response is already out: a second one would corrupt it
                failure_body(e, u.path)                 # (logged once, under a reference)
                self.close_connection = True
                return
            return self.send(*failure_body(e, u.path))

    def search(self, q):
        if len(q) < 2:
            return self.send(200, {"results": []})
        ql = q.lower()
        # typing: a longer query can be answered from a shorter one's complete list (<8 hits)
        # HLTV first (in memory); BO3.gg only if it's down. 2026-10-09: one scan per distinct query per index load -
        # identical queries arriving together share it (a 700-member burst used to scan the whole index ~2,100 times)
        try:
            k_ = ("hlsearch", hltv._INDEX.get("at"), hltv._norm(q))

            def _hl():
                r_ = hltv.search_players(q, 8)
                return (200, json.dumps({"results": r_}).encode()) if r_ is not None else (503, b"")
            st_, body_ = shared(k_, _hl, ttl=3600)
            if st_ == 200:
                return self.send(200, body_)
        except Exception:
            pass
        hit = _SEARCH.get(ql)
        if hit and time.time() - hit[0] < 3600:
            return self.send(200, hit[1])
        for k in range(len(ql) - 1, 1, -1):              # typing: a shorter query's complete list
            prev = _SEARCH.get(ql[:k])
            if prev and time.time() - prev[0] < 3600 and prev[2] < 8:
                keep = [h for h in prev[3] if ql in (h["nick"] or "").lower() or ql in (h["name"] or "").lower()]
                body = json.dumps({"results": keep}).encode()
                _SEARCH[ql] = (time.time(), body, len(keep), keep)
                return self.send(200, body)

        def compute():
            b = BO3()
            try:
                hits = b.search_players(q, 8).get("results") or []
            finally:
                b.close()
            return 200, json.dumps({"results": [
                {"slug": h["slug"], "nick": h.get("nickname"),
                 "name": " ".join(x for x in (h.get("first_name"), h.get("last_name")) if x),
                 "country": (h.get("country") or {}).get("code"), "image": h.get("image_url")}
                for h in hits]}).encode()
        # audit 2026-10-26: only the BO3.gg fallback costs an upstream call per new query - one member typing or looping
        # distinct queries while the HLTV index is down must not spend the shared BO3.gg allowance. Cached queries are free.
        with _RG:
            cached_ = _RESULTS.get(("search", ql))
        if (not (cached_ and time.time() - cached_[0] < 3600) and not self.load_test()
                and not studio_rate_ok(self.rate_key(), bucket=_SEARCH_BO3_HITS, limit=SEARCH_BO3_PER_MIN)):
            return self.send(429, {"error": f"Too many searches - limit is {SEARCH_BO3_PER_MIN} a minute while search is on "
                                            "its backup source. Wait a moment.", "results": []})
        status, body = shared(("search", ql), compute, ttl=3600)
        if status == 200:
            rows = json.loads(body)["results"]
            if len(_SEARCH) > 20000:
                _SEARCH.clear()
            _SEARCH[ql] = (time.time(), body, len(rows), rows)
        return self.send(status, body)

    def client_ip(self):
        # the hop our proxy appended (Render appends the connecting address last): the first hop is whatever the client
        # sent, so a made-up X-Forwarded-For got a fresh rate-limit bucket per request (2026-10-13 audit)
        fwd = self.headers.get("X-Forwarded-For") or ""
        return fwd.split(",")[-1].strip() or self.client_address[0]

    def rate_key(self):
        """Who a rate limit counts: the signed-in Whop member (an id no header can change), else the client address."""
        sess = self.member()
        return f"whop:{sess['user']}" if sess and sess.get("user") else "ip:" + self.client_ip()

    # ------------------------------------------------------------ research tools (moneylines + slip studio)
    def moneylines(self):
        """GET /api/moneylines: one shared response for everyone, rebuilt at most every ML_TTL seconds (single flight)."""
        def compute():
            return 200, json.dumps(moneylines_payload(), default=str).encode()
        try:
            st_, body_ = shared(("moneylines",), compute, ttl=ML_TTL)
        except Exception as e:
            sys.stderr.write(f"  moneylines: {type(e).__name__}: {str(e)[:200]}\n")
            return self.send(503, {"error": "moneylines_unavailable", "label": "Market probability unavailable"})
        # the shared body is up to ML_TTL old: X-Served-At = the server's clock as THIS copy leaves, so the page measures a
        # Thunderpick quote's age from serve time, not from the cached build time (2026-10-13 review: a 179 s quote built
        # 29 s before the fetch still read as usable at a true 208 s). Per response, the body bytes stay shared.
        return self.send_shared(("moneylines",), st_, body_, headers={"X-Served-At": datetime.now(timezone.utc).isoformat(timespec="milliseconds")})

    def studio_member(self):
        """Whose slip batches these are: the signed-in Whop member (user id), a load test (fixed id, optional X-Test-Member
        suffix so a load test can act as many members), or - only where Whop sign-in is off and access is open (local) -
        the client address. None = nobody."""
        if self.load_test():
            sfx = re.sub(r"[^A-Za-z0-9_-]", "", str(self.headers.get("X-Test-Member") or ""))[:32]
            return "load-test" + (":" + sfx if sfx else "")
        if whop_auth.enabled():
            sess = self.member()
            return f"whop:{sess['user']}" if sess and sess.get("ok") and sess.get("user") else None
        return ("anon:" + self.client_ip()) if self.allowed() else None

    def studio_batch(self, q):
        """GET /api/slip_studio/batch[?id=]: the member's batch (default: the open one) with all its slips and exposure."""
        if not SLIP_STUDIO:
            return self.send(404, {"error": "slip_studio_off"})
        sid = self.studio_member()
        if not sid:
            return self.send(401, {"error": "members_only"})
        import slip_builder
        bid = q.get("id") or ""
        if bid and not _BATCH_RE.match(bid):
            return self.send(400, {"error": "bad batch id"})
        cur = slip_builder.current_batch(sid)
        if bid and bid != cur and not slip_builder.owns(sid, bid):
            return self.send(404, {"error": "unknown batch"})
        b = slip_builder.get_batch(bid or cur)
        if b is None:
            return self.send(404, {"error": "unknown batch"})
        return self.send(200, dict(letter_containment.cs2_slip_records(studio_tail_annotate(b)), current=(b["batch_id"] == cur), settled_batches=settled_batches(),
                                   track_record=studio_track_record(), builder_version=slip_builder.VERSION))

    def studio_history(self):
        """GET /api/slip_studio/history: the member's open batch and recent earlier ones, each slip with its legs'
        results from the pick log (hit / miss / push / void / waiting - the shadow builder's settlement rule) and the
        slip's outcome: won (every leg hit), lost (any leg missed), reduced (no leg lost, a push or void - the platform's
        rules decide the entry), live or waiting. Records are shown as stored; no payout is assumed."""
        if not SLIP_STUDIO:
            return self.send(404, {"error": "slip_studio_off"})
        sid = self.studio_member()
        if not sid:
            return self.send(401, {"error": "members_only"})
        import slip_builder
        return self.send(200, letter_containment.cs2_slip_records(studio_history_payload(slip_builder.member_batches(sid))))

    def studio_capacity(self, q):
        """GET /api/slip_studio/capacity?book=&side=&stat=&scope=[&market=]: eligible lines / matches and the most slips of each
        size they support with no repeats. Members only (like the batch); built from the shared graded-board snapshot and
        shared for STUDIO_CAP_TTL s per settings - no upstream call and no board rebuild per request."""
        if not SLIP_STUDIO:
            return self.send(404, {"error": "slip_studio_off"})
        if not self.studio_member():
            return self.send(401, {"error": "members_only"})
        raw = {k: q[k] for k in ("book", "side", "stat", "scope", "market") if q.get(k)}
        try:
            st = studio_settings(raw)
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        import slip_builder
        key = ("studio_cap",) + tuple(st[k] for k in ("book", "side", "stat", "scope", "market"))

        def compute():
            gb = letter_containment.cs2_board(graded_board(st["book"]))      # lc-1: a held leg has no rating
            out = slip_builder.capacity(gb.get("rows") or [], st, model=gb.get("version"))
            out["board"] = {"book": st["book"], "updated": gb.get("updated"), "graded_rows": len(gb.get("rows") or [])}
            return 200, json.dumps(out, default=str).encode()
        st_, body_ = shared(key, compute, ttl=STUDIO_CAP_TTL)
        return self.send(st_, body_)

    def studio_post(self, path, n):
        """POST /api/slip_studio/build | verify | event (JSON bodies, members only, every field bounded)."""
        if n > STUDIO_BODY_MAX:
            self.close_connection = True              # the unread body would corrupt a kept-alive connection
            return self.send(413, {"error": f"body over {STUDIO_BODY_MAX} bytes"})
        body = self.rfile.read(n) if n > 0 else b""
        if not SLIP_STUDIO or path not in ("/api/slip_studio/build", "/api/slip_studio/verify", "/api/slip_studio/event"):
            return self.send(404, {"error": "not found"})
        if not self.allowed():
            return self.send(401, {"error": "members_only", "signed_in": bool(self.member()), "upgrade": whop_auth.upgrade_url()})
        sid = self.studio_member()
        if not sid:
            return self.send(401, {"error": "members_only"})
        try:
            d = json.loads(body or b"{}", parse_constant=_no_json_constant)      # NaN / Infinity: 400, never 500
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            return self.send(400, {"error": "body must be a JSON object"})
        idem = d.get("idem")
        if idem is not None and (not isinstance(idem, str) or not _IDEM_RE.match(idem)):
            return self.send(400, {"error": "idem must be 8-64 characters of A-Z a-z 0-9 _ . : -"})
        import slip_builder
        try:
            if path == "/api/slip_studio/build":
                return self.studio_build(slip_builder, sid, d, idem)
            if path == "/api/slip_studio/verify":
                return self.studio_verify(slip_builder, sid, d, idem)
            return self.studio_event(slip_builder, sid, d, idem)
        except Exception as e:
            traceback.print_exc()
            return self.send(500, {"error": f"slip studio failed ({type(e).__name__}) - try again"})

    def studio_build(self, sb, sid, d, idem):
        extra = set(d) - {"settings", "batch_id", "new_batch", "idem", "replace"}
        if extra:
            return self.send(400, {"error": "unknown field(s): " + ", ".join(sorted(extra))})
        if not idem:
            return self.send(400, {"error": "idem (the request id) is required"})
        try:
            st = studio_settings(d.get("settings") if d.get("settings") is not None else {})
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        nb, bid, rep = d.get("new_batch", False), d.get("batch_id"), d.get("replace")
        if not isinstance(nb, bool):
            return self.send(400, {"error": "new_batch must be true or false"})
        if bid is not None and (not isinstance(bid, str) or not _BATCH_RE.match(bid)):
            return self.send(400, {"error": "bad batch id"})
        if rep is not None and (not isinstance(rep, str) or not _SLIP_RE.match(rep) or nb):
            return self.send(400, {"error": "replace must be a stored slip id of this batch (not with new_batch)"})
        if not self.load_test() and not studio_rate_ok(sid, idem):
            return self.send(429, {"error": f"Too many builds - limit is {STUDIO_BUILDS_PER_MIN} a minute. Wait a moment."})
        if rep:
            old = sb.get_slip(rep)
            if not old or not old.get("batch_id") or not sb.owns(sid, old["batch_id"]) or (bid and bid != old["batch_id"]):
                return self.send(404, {"error": "unknown slip"})
            batch = old["batch_id"]
            st = dict(st, book=(old.get("settings") or {}).get("book") or old.get("book") or st["book"])
        elif nb:
            batch = sb.new_batch(sid, key="nb:" + idem)
        elif bid:
            if not sb.owns(sid, bid):
                return self.send(404, {"error": "unknown batch"})
            batch = bid
        else:
            batch = sb.current_batch(sid)
        gb = letter_containment.cs2_board(graded_board(st["book"]))          # lc-1: a held leg has no rating
        try:
            res = sb.build(gb["rows"], st, batch=batch, key=idem, model=gb.get("version"), replace=rep)
        except ValueError as e:
            if "closed" in str(e):
                return self.send(409, {"error": str(e), "current_batch": sb.current_batch(sid)})
            return self.send(400, {"error": str(e)})
        return self.send(200, dict(res, settled_batches=settled_batches(), track_record=studio_track_record(),
                                   board={"book": st["book"], "updated": gb.get("updated"), "graded_rows": len(gb.get("rows") or []),
                                          "feed_state": (gb.get("feed") or {}).get("state"), "error": gb.get("error")}))

    def _studio_slip(self, sb, sid, d):
        sl = d.get("slip")
        slip_id = sl.get("slip_id") if isinstance(sl, dict) else sl if sl is not None else d.get("slip_id")
        if not isinstance(slip_id, str) or not _SLIP_RE.match(slip_id):
            return None, self.send(400, {"error": "slip must be a stored slip id (preview slips can't be verified)"})
        rec = sb.get_slip(slip_id)
        if not rec or not rec.get("batch_id") or not sb.owns(sid, rec["batch_id"]):
            return None, self.send(404, {"error": "unknown slip"})
        return rec, None

    def _studio_events_ok(self, sid, idem):
        """verify + event: their own per-member bucket (a loop on them can't grow slip_studio.db or eat build quota)."""
        return self.load_test() or studio_rate_ok(sid, idem, bucket=_STUDIO_EV_HITS, limit=STUDIO_EVENTS_PER_MIN)

    def studio_verify(self, sb, sid, d, idem):
        """Before copying: the platform's board is refreshed (graded snapshot <= 2 s old, the shared feed), then every leg is
        checked. Any change -> action 'rebuild' with what changed; the old chance is never carried to a new line."""
        if set(d) - {"slip", "slip_id", "idem"}:
            return self.send(400, {"error": "unknown field(s)"})
        if not self._studio_events_ok(sid, idem):
            return self.send(429, {"error": f"Too many checks - limit is {STUDIO_EVENTS_PER_MIN} a minute. Wait a moment."})
        rec, err = self._studio_slip(sb, sid, d)
        if rec is None:
            return err
        book = rec.get("book") or "underdog"
        dead = sb.slip_status(rec["batch_id"], rec["slip_id"])
        if dead:
            # 2026-10-21: a replaced / changed / dismissed slip no longer counts toward the batch (exposure, usage) - it
            # got ok + copy_text again when its lines happened to be unchanged. Never copied: a changed or dismissed one
            # is rebuilt (replace), a replaced one points at its replacement.
            why = {"replaced": "this slip was replaced - copy its replacement",
                   "changed": "a line on this slip changed since it was built - it is rebuilt from the current board",
                   "dismissed": "this slip was dismissed - build again for a current one"}[dead["status"]]
            return self.send(200, {"ok": False, "action": "replaced" if dead["status"] == "replaced" else "rebuild",
                                   "status": dead["status"], "replaced_by": dead.get("replaced_by"), "changes": [why],
                                   "legs": [], "slip_id": rec["slip_id"], "batch_id": rec["batch_id"], "book": book,
                                   "copy_text": None})
        feed = prizepicks.board() if book == "prizepicks" else underdog.board()
        graded = letter_containment.cs2_board(graded_board(book, max_age=2))   # lc-1: a held grade reads Lean
        v = sb.verify_before_copy(rec, {book: {"feed": feed, "graded": graded}}, model=graded.get("version"))
        # one stored row per request id - or per slip per 30 s without one (the answer is still computed every time)
        key = "v:" + (idem or f"{rec['slip_id']}:{int(time.time() // 30)}")
        try:
            sb.record_event(rec["batch_id"], rec["slip_id"], "verify_ok" if v["ok"] else "verify_changed",
                            key=key, detail={"changes": v["changes"][:10]})
        except ValueError:
            pass
        out = dict(v, slip_id=rec["slip_id"], batch_id=rec["batch_id"], book=book,
                   copy_text=sb.copy_text(rec) if v["ok"] else None)
        if v["ok"]:
            out["line_advantages"] = slip_line_advantages(rec.get("legs") or [])
        if PP_OPEN_LINK and v["ok"]:
            out.update(studio_open_link(rec, feed))
        return self.send(200, out)

    def studio_event(self, sb, sid, d, idem):
        if set(d) - {"batch_id", "slip_id", "kind", "idem"}:
            return self.send(400, {"error": "unknown field(s)"})
        kind, bid = d.get("kind"), d.get("batch_id")
        if kind not in sb.EVENT_KINDS:
            return self.send(400, {"error": "kind must be one of " + ", ".join(sb.EVENT_KINDS)})
        if not isinstance(bid, str) or not _BATCH_RE.match(bid):
            return self.send(400, {"error": "bad batch id"})
        if not isinstance(d.get("slip_id"), str) or not _SLIP_RE.match(d["slip_id"]):
            return self.send(400, {"error": "bad slip id"})
        if not self._studio_events_ok(sid, idem):
            return self.send(429, {"error": f"Too many requests - limit is {STUDIO_EVENTS_PER_MIN} a minute. Wait a moment."})
        if not sb.owns(sid, bid):
            return self.send(404, {"error": "unknown batch"})
        key = idem or f"e:{d['slip_id']}:{kind}:{int(time.time() // 30)}"       # no request id: one row per 30 s
        try:
            new = sb.record_event(bid, d["slip_id"], kind, key=key)
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        return self.send(200, {"ok": True, "duplicate": not new})


    def grade(self, q):
        load_test = self.load_test()                     # owner's own tests
        if not load_test and not rate_ok(self.rate_key()):
            return self.send(429, {"error": f"Too many grades - limit is {RATE_LIMIT} a minute. "
                                            f"Wait a moment and try again."})
        name = q.get("player", "").strip()
        try:
            line = float(q.get("line", ""))
        except ValueError:
            return self.send(400, {"error": "Enter the line as a number, e.g. 27.5"})
        if not name:
            return self.send(400, {"error": "Enter a player name"})
        prop = q.get("prop", "kills") if q.get("prop") in ("kills", "hs") else "kills"
        lt = q.get("line_type", "standard") if q.get("line_type") in ("standard", "goblin", "demon") else "standard"
        scope = q.get("maps") if q.get("maps") in ("12", "123", "1") else "12"
        # 2026-10-09 audit: 79 member lines were impossible (e.g. a 1675 headshot line); the old pipeline then pulled its
        # projection toward the line (846 HS). Generous plausible ranges - anything outside is a typo, not a prop.
        hi_ = PLAUSIBLE_MAX[prop][scope]
        if not (0 < line <= hi_):
            return self.send(400, {"error": f"{line} doesn't look like a real {'headshots' if prop == 'hs' else 'kills'} line for "
                                            f"{ {'1': 'Map 1', '12': 'Maps 1-2', '123': 'Maps 1-3'}[scope]} (expected between 0 and {hi_}). Check the number."})
        slug, match = q.get("slug") or None, q.get("match") or None
        opp = (q.get("opponent") or "")[:60].strip() or None
        team = (q.get("team") or "")[:60].strip() or None     # board click: the team the board graded him for (namesakes)
        mstart = (q.get("start") or "")[:32].strip() or None   # board click: the line's start (a misspelled book opponent)
        if mstart and not re.fullmatch(r"[0-9T:.+\-Z ]{10,32}", mstart):
            mstart = None
        pkey = (slug or name).lower()
        # the card's platform (board / match-players clicks send it): its stake cap is its own board's; no platform (typed
        # card, Slate, a share link without one) = the board that lists this exact pick (board_stake, Underdog first)
        book = ("prizepicks" if q.get("book") == "prizepicks" or lt in ("goblin", "demon") else
                "underdog" if q.get("book") == "underdog" else None)
        less_av = feed_less_available(name, prop, scope, line, lt, book, mstart)   # the feed's sides, as the board grades
        key = (pkey, line, prop, lt, match, (opp or "").lower(), scope, (team or "").lower(), book, (mstart or "")[:16] if opp else "",
               less_av)

        t_in = time.time()

        def compute():
            tm = {"queued": round(time.time() - t_in, 2)}
            t0 = time.time()
            with player_lock(pkey):                     # one assembly per player at a time
                tm["player_lock"] = round(time.time() - t0, 2)
                warm = time.time() - _WARM.get(pkey, 0) < WARM_FOR
                t0 = time.time()
                with (FAST_SLOTS if warm else COLD_SLOTS):
                    tm["slot"], tm["warm"] = round(time.time() - t0, 2), warm
                    t0 = time.time()
                    res = cs2_scout.grade(name, line, prop, lt, slug=slug, faceit_key=faceit_key(), scope=scope,
                                          match=match, opponent=opp, team_hint=team, match_start=mstart, less_available=less_av)
                    tm["grade"] = round(time.time() - t0, 2)
            t0 = time.time()
            if res.get("error"):
                body_ = {"error": res["error"][0].upper() + res["error"][1:]}
                if "check the spelling" in res["error"]:    # "Did you mean ...?" - real nicks from the HLTV index (in memory)
                    try:
                        import hltv
                        body_["suggestions"] = hltv.suggest_nicks(name, 5)
                    except Exception:
                        body_["suggestions"] = []
                return 404, json.dumps(body_).encode()
            pb_ = res.get("player_block") or {}
            mark_warm(pkey, name, (pb_.get("player") or {}).get("slug"), (pb_.get("player") or {}).get("nick"))
            if scope == "123":                           # display only: his Maps 1-3 record (never a grade input)
                try:
                    pb_["recent_form_123"] = records.maps123_record(pb_, line, "headshots" if prop == "hs" else "kills")
                except Exception:
                    traceback.print_exc()
            res["verdict"] = verdict(res.get("grade"))
            res["caps"] = stake_caps()
            outage = grade_transient(res.get("grade") or {})        # HLTV or Thunderpick blip (owner 2026-10-13)
            try:
                res["underdog"] = underdog.line_for((pb_.get("player") or {}).get("nick") or name, prop)
                if PP_OPEN_LINK and res["underdog"] and ud_row_link(res["underdog"].get("id")):   # a copy: never the board's row
                    res["underdog"] = dict(res["underdog"], ud_app_link=ud_row_link(res["underdog"].get("id")))
            except Exception:
                res["underdog"] = None
            try:                                         # comparison panel: context only, never changes the grade
                tc = time.time()
                ub = underdog.board()
                res["comparison"] = comparison.panel(res, ub["rows"] + prizepicks.board()["rows"], board_at=ub.get("at"), id_map=ud_id_map(ub["rows"]))
                tm["comparison"] = round(time.time() - tc, 3)
            except Exception:
                res["comparison"] = {"error": "comparison unavailable"}
            tm["underdog"] = round(time.time() - t0, 2)
            t0 = time.time()
            if not outage:                               # an outage grade is never logged as a live forecast (2026-10-13 audit:
                try:                                     # the board path skipped it, member card lookups did not)
                    picklog.log_async(res, res["verdict"], test=load_test)   # written in the background
                except Exception:
                    traceback.print_exc()
            tm["log"] = round(time.time() - t0, 2)
            if not outage and grade_display.bo3_shadow(res.get("grade")):
                withhold_shadow(res)                     # sr-bo3-2: logged above, hidden here (no chance / letter / side / stake)
            try:                                         # the card's distribution chart (display only, dist_view.py)
                import dist_view
                res["dist_view"] = None if outage else dist_view.view(res.get("grade"))
            except Exception:
                res["dist_view"] = None
            try:                                         # ec-1: after the pick log - the card prints the calibrated chance,
                res = esports_cal.cs2_card(res)          # letter and stake (new dicts; the logged row keeps the model's own)
            except Exception:
                traceback.print_exc()
            try:                                         # after logging: the log keeps the grader's own stake
                res["verdict"] = board_stake(res, prop, scope, line, lt, book or ("underdog", "prizepicks"))
                vv = res["verdict"]
                if isinstance(vv, dict) and vv.get("side"):          # the board row's cap words, on the card too (new dict:
                    g_ = res.get("grade") or {}                      # the logged verdict is being written in the background)
                    own = dict(vv, units=vv.get("units_full") or vv.get("units"))
                    # dstat: the board's own data badge, sent with the card (2026-10-13 audit: thin HLTV history read
                    # "Limited history" on the board and "Data complete" on the card for the same line)
                    ds_ = comparison.data_status(g_, vv)
                    res["verdict"] = dict(vv, cap_reason=cap_reason_text(stake_cap_reasons(g_, own, source=odds_selected(res.get("odds"))) if (vv.get("units") or 0) > 0 else [],
                                                                         vv.get("cap_n")),
                                          dstat=ds_, flagged=bool(ds_[0] in ("verify", "limited") or has_warning(g_)))
                    if vv.get("side") == "Less" and less_av is False:   # the feed offers no Under for this exact line: shown
                        res["verdict"]["side_unavailable"] = SIDE_NOT_OFFERED   # as such, never a pick (grade logged as is)
                vv = res["verdict"]
                if isinstance(vv, dict) and vv.get("side"):          # display gd-1: grade / stake reason / availability
                    keep_sr = vv.get("stake_reason")
                    res["verdict"] = vv = dict(vv, **grade_display.annotate(res.get("grade") or {}, vv,
                                                                            dict(vv, unavailable=vv.get("side_unavailable"))))
                    if keep_sr:
                        vv["stake_reason"] = keep_sr
                if isinstance(vv, dict):                 # identity of this grade (night/consistency 2026-10-23): the model
                    vv = res["verdict"] = card_identity(res, vv, prop, scope, line, book)   # version + match id, as a board row
                for x in res.get("ladder") or []:        # the ladder's own line = the card's verdict (same-match cap included)
                    if isinstance(vv, dict) and vv.get("side") and float(x.get("line") or -1) == float(line):
                        x.update(side=vv["side"], p=vv.get("p"), grade=vv.get("grade"), units=vv.get("units") or 0.0,
                                 state=vv.get("state"), model_grade=vv.get("model_grade"), stake_reason=vv.get("stake_reason"),
                                 available=vv.get("available"))
                    else:
                        x.update(grade_display.annotate_item(x))
            except Exception:
                traceback.print_exc()
            if outage:                                   # no grade of its own: the board's saved grade of this exact pick
                cs_ = _transient_causes(res.get("grade") or {})     # (stale) or data unavailable; temporary numbers withheld
                try:
                    res["verdict"] = card_identity(res, outage_verdict(res, prop, scope, line, res["verdict"], book or ("underdog", "prizepicks"), lt),
                                                   prop, scope, line, book)
                    res["hltv_outage"] = outage_info(res, cs_)
                except Exception:                        # fail closed: never the temporary grade or its stake
                    traceback.print_exc()
                    res["verdict"] = {"side": None, "p": None, "grade": None, "units": 0.0, "state": "UNAVAILABLE",
                                      "unavailable": True, "kept_from_board": False, "stale": False, "temporary": False,
                                      "reason": "Live data unavailable - no grade for this line until it is back"}
                    res["hltv_outage"] = {"rows": [], "causes": cs_, "odds_note": None, "unavailable": True}
                withhold_temporary(res)
            res["veto"] = card_veto(res)                 # map veto panel: the background's stored answer (display only)
            res["map_chances"] = card_map_chances(res)   # HLTV map chances (display only, weight 0; after the pick log)
            res = letter_containment.cs2_card(res)       # lc-1: after the pick log - a held market reads Lean, 0U (display only)
            t0 = time.time()
            body = json.dumps(public(res), default=str).encode()
            tm["json"] = round(time.time() - t0, 2)
            REQ_TIMES.append(dict(tm, player=name, at=round(time.time()), total=round(time.time() - t_in, 2)))
            return 200, body, (5 if outage else None)    # an outage grade is reused for 5 s, not 60

        status, body = shared(key, compute)
        # a card is one shared body per key (700 members on one card share it): gzip + ETag made once, 304 on revisit
        # (audit 2026-10-26: cards of ~60 KB went uncompressed with no-store). Body bytes unchanged; non-200 via send().
        # The compressed copy is kept 5 s (an outage card is reused for 5 s only - nothing of it may outlive that).
        return self.send_shared(("card",) + key, status, body, keep=5)

    # ------------------------------------------------------------ Phase 10 admin (private)
    def load_test(self):
        """The owner's load-test / audit header - constant-time compare like the admin token."""
        import hmac
        return bool(ADMIN_TOKEN) and token_eq(self.headers.get("X-Load-Test"), ADMIN_TOKEN)

    def _sports_on(self, path, q=None):
        """Sports (sports/, NFL/NBA) is behind SPORTS_UI (/sports, /sports/<asset>) and SPORTS_API (/api/sports/*), both
        OFF by default. Read from the environment here, so with the flags off nothing from sports is ever imported.
        SPORTS_UI=owner (staged access, 2026-10-24): the page, its assets, the nav link and /api/sports/token are served
        ONLY to a paid member whose allow id is listed in SPORTS_UI_ALLOW (comma-separated, sports_allow_id); everyone
        else gets exactly the flags-off answer (the request falls through to the esports routes). The other
        /api/sports/* paths keep following SPORTS_API. /api/sports/whoami answers only the owner's admin token (to read
        the allow id of the signed-in session) - for anyone else it is an unknown route too."""
        page = path == "/sports" or path.startswith("/sports/")
        if os.environ.get("SPORTS_UI", "").strip().lower() == "owner":
            if page or path == "/api/sports/token":
                return self._sports_owner()
            if path == "/api/sports/whoami":
                return self.authed((q or {}).get("token"))
        flag = "SPORTS_UI" if page else "SPORTS_API"
        return os.environ.get(flag, "").strip().lower() in ("1", "true", "yes", "on")

    def _sports_owner(self):
        """SPORTS_UI=owner: the existing server-side paid check FIRST, then the member's allow id against the list.
        No list = nobody (fails closed). Load-test / anonymous requests have no member, so they never qualify."""
        allow = {x.strip().lower() for x in os.environ.get("SPORTS_UI_ALLOW", "").split(",") if x.strip()}
        if not allow or not self.allowed():
            return False
        user = (self.member() or {}).get("user")
        return bool(user) and sports_allow_id(user) in allow

    def _workspace_on(self, q):
        """The research workspace preview: SCOUT_WORKSPACE_PREVIEW on (default off) for anyone who already passes the paid
        check - sign-in off on a local run passes it, a signed-out visitor on the live site never does - or the owner: the
        admin token (?token= or X-Admin-Token) or the SPORTS_UI_ALLOW allow list. The page's scripts and styles carry no
        token, so with ?token= they are authorised from the page URL in their same-origin Referer."""
        if os.environ.get("SCOUT_WORKSPACE_PREVIEW", "").strip().lower() in ("1", "true", "yes", "on") and self.allowed():
            return True
        token = q.get("token")
        ref = urlparse(self.headers.get("Referer") or "")
        if not token and ref.path == "/preview/workspace":
            token = (parse_qs(ref.query).get("token") or [None])[0]
        return self.authed(token) or self._sports_owner()

    def _workspace_api(self, method, path, q, n=0, prefix=WORKSPACE_API):
        """The one workspace API hook. This module is passed in: `import server` from there would load a second copy."""
        import workspace_api
        return workspace_api.handle(self, sys.modules[__name__], method, path, q, n, prefix)

    def _upgrade_on(self, q):
        """The upgraded member pages. SCOUT_UPGRADE unset or on (owner 2026-11-03: "make it live"): every member who passes the paid
        check. SCOUT_UPGRADE=owner: the owner only, by the admin token (?token=, X-Admin-Token, or a same-origin Referer from a page opened with it, since the page's scripts
        and styles carry no token) or the SPORTS_UI_ALLOW allow list. SCOUT_UPGRADE=off: nobody."""
        flag = os.environ.get("SCOUT_UPGRADE", "").strip().lower()
        if flag in ("0", "false", "no", "off"):
            return False
        if flag in ("", "1", "true", "yes", "on") and self.allowed():
            return True
        token = q.get("token")
        if not token and self.headers.get("Referer"):
            ref = urlparse(self.headers.get("Referer"))
            if ref.path in ("/", "/index.html", "/lol", "/sports", "/sports/", "/sports/results"):
                token = (parse_qs(ref.query).get("token") or [None])[0]
        if self.authed(token):
            return True
        allow = {x.strip().lower() for x in os.environ.get("SPORTS_UI_ALLOW", "").split(",") if x.strip()}
        user = whop_auth.cookie_user(self.cookie(whop_auth.COOKIE)) if allow else None      # read from the signed cookie: a member off the
        return bool(user) and sports_allow_id(user) in allow and self._sports_owner()       # list never reaches Whop on a page load

    def _sports_route(self, u, q):
        """Sports answers through sports/gateway.py (standard library only). In the default SPORTS_API_MODE=service the
        sports API runs in its own process / Render service: this thread only mints a member token after the server-side
        paid check, serves the static page, or answers 404 - it never runs sports queries. Any sports failure is a 503
        for that sports request only (sports/tests/test_isolation_server.py)."""
        try:
            from sports import gateway
            code, body, ctype, hdrs = gateway.handle_get(
                u.path, u.query, q, member=lambda: (self.member() or {}).get("user"), allowed=self.allowed,
                admin=lambda: self.authed(q.get("token")), headers=self.headers)
        except Exception as e:                          # noqa: BLE001 - sports must not break esports
            sys.stderr.write(f"  sports route failed: {type(e).__name__}: {str(e)[:200]}\n")
            return self.send(503, {"error": "sports_unavailable"})
        if code == 200 and u.path in ("/sports", "/sports/", "/sports/results") and self._upgrade_on(q):
            body = upgrade_page(body, "sports-results" if u.path == "/sports/results" else "sports")
        return self.send(code, body, ctype, hdrs)

    def authed(self, token):
        # the admin link's ?token= still works; scripts send the X-Admin-Token header instead, so the token stays out
        # of URLs and logs (2026-10-10). Constant-time compare.
        import hmac
        tok = token or self.headers.get("X-Admin-Token") or ""
        return bool(ADMIN_TOKEN) and token_eq(tok, ADMIN_TOKEN)

    def do_POST(self):
        """Same failure handling as do_GET (audit 54: an exception here dropped the connection with no response)."""
        self._sent = False
        try:
            return self._do_post()
        except CLIENT_GONE:
            self.close_connection = True
        except Exception as e:
            self.close_connection = True                # an unread body would corrupt a kept-alive connection
            if self._sent:
                failure_body(e, urlparse(self.path).path)
                return
            return self.send(*failure_body(e, urlparse(self.path).path))

    def _do_post(self):
        u = urlparse(self.path)
        self._sess, self._renew = False, None           # per request (a kept-alive connection reuses this handler)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:
            self.close_connection = True
            return self.send(400, {"error": "bad Content-Length"})
        if u.path.startswith("/api/slip_studio/"):
            return self.studio_post(u.path, n)
        if u.path == "/alerts/unsubscribe":
            if n > 512:
                self.close_connection = True
                return self.send(413, {"error": "request too large"})
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
            return self.send(*unsubscribe_page(form.get("m"), form.get("t"), confirm=True))
        # 2026-10-13 audit: the body was read before any check, as long as the client said (a 10 GB Content-Length was
        # read into memory). Every body here is small: over POST_MAX it is refused unread, members-only before reading.
        # Saved lineup batches contain up to twelve slips. All other routes keep
        # their existing small-body limit; state still authenticates before read.
        post_limit = 128 * 1024 if u.path in (WORKSPACE_API + "state", UPGRADE_API + "state") else POST_MAX
        if n > post_limit:
            self.close_connection = True              # the unread body would corrupt a kept-alive connection
            return self.send(413, {"error": f"body over {post_limit} bytes"})
        if u.path.startswith(WORKSPACE_API) and self._workspace_on({k: v[0] for k, v in parse_qs(u.query).items()}):
            return self._workspace_api("POST", u.path, {k: v[0] for k, v in parse_qs(u.query).items()}, n)
        if u.path.startswith(UPGRADE_API) and self._upgrade_on({k: v[0] for k, v in parse_qs(u.query).items()}):
            return self._workspace_api("POST", u.path, {k: v[0] for k, v in parse_qs(u.query).items()}, n, prefix=UPGRADE_API)
        if u.path == "/api/slip_log":
            # closeout2 copyval: the page's Copy slip waits for THIS answer and writes the clipboard only on {"ok": true}
            # (owner: validate first - a refused, failed or timed-out check leaves the clipboard untouched). One call
            # validates (slip_check: the server's own rules) and logs the copy (slips.py) - only a valid slip is logged.
            if not self.allowed():
                self.close_connection = True
                return self.send(401, {"error": "members_only"})
            body = self.rfile.read(n)
            # one member copying slips by hand never needs more; a loop can't grow slips_loaded.jsonl without limit
            if not self.load_test() and not studio_rate_ok(self.rate_key(), bucket=_SLIP_LOG_HITS, limit=SLIP_LOG_PER_MIN):
                return self.send(429, {"error": f"Too many copies - limit is {SLIP_LOG_PER_MIN} a minute. Wait a moment and try again."})
            try:
                d = json.loads(body)
                assert isinstance(d, dict) and isinstance(d.get("legs"), list) and 1 <= len(d["legs"]) <= 6
                assert all(isinstance(l, dict) for l in d["legs"]) and len(body) <= 20000
            except Exception:
                return self.send(400, {"error": "bad slip"})
            # PP_OPEN_LINK: "open": "prizepicks" asks for the lineup link of this checked slip (built only after the same
            # check); with the flag off the field is ignored and the answer is exactly the copy answer
            want = d.get("open") if PP_OPEN_LINK and d.get("open") in ("prizepicks", "underdog") else None
            # an Underdog slip (copy or open, flag on) also gets each line's own Underdog link after the same check
            udl = PP_OPEN_LINK and d.get("book") == "underdog"
            v = slip_check(d, link=want == "prizepicks" or udl)
            if not v["ok"]:                            # rejected on the server: not copied, nothing logged
                return self.send(409, {"ok": False, "error": "slip_has_unavailable_legs", "legs": v["legs"], "problems": v["problems"]})
            extra = {}
            if want == "prizepicks":
                if not v.get("pp_url"):                # a valid slip without a lineup link: nothing opened, nothing logged
                    return self.send(409, {"ok": False, "error": "no_prizepicks_link", "legs": [v.get("pp_reason") or "This slip can't be opened in PrizePicks"],
                                           "problems": []})
                extra = {"pp_url": v["pp_url"]}
            elif want == "underdog":
                if d.get("book") != "underdog":
                    return self.send(409, {"ok": False, "error": "not_underdog", "legs": ["Only Underdog slips open Underdog"], "problems": []})
                extra = {"open_url": pplink.UD_HOME}
            if udl:
                extra.update(ud_legs_open(v.get("ud_legs") or []))
            try:
                new = slips.log_loaded(d)
            except ValueError:
                return self.send(400, {"error": "bad slip"})
            except Exception:                          # the log file failed: the slip itself is valid - still copied
                return self.send(200, {"ok": True, "logged": False, **extra})
            return self.send(200, {"ok": True, "duplicate": not new, **extra})
        if u.path == "/admin/ud_tail":                # PP_OPEN_LINK owner tail link (admin token only)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace")).items()}
            if not PP_OPEN_LINK or not self.authed(form.get("token")):
                return self.send(403, {"error": "forbidden"})
            return self.send(*admin_ud_tail(form))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        if u.path != "/admin/closing" or not self.authed(form.get("token")):
            return self.send(403, {"error": "forbidden"})
        try:
            picklog.set_closing(int(form["id"]), float(form["closing"]))
        except (KeyError, ValueError):
            pass
        self.send_response(303)
        self.send_header("Location", "/admin?token=" + form.get("token", ""))
        self.send_header("Content-Length", "0")      # keep-alive needs an explicit length
        self.end_headers()

    def admin(self, q):
        if not self.authed(q.get("token")):
            return self.send(403, b"Forbidden", "text/plain")
        r = picklog.report()
        e = htmllib.escape
        tok = e(q.get("token") or "")                  # signed in by the X-Admin-Token header alone: no token in the URL (was a 500)

        def table(rows, cols):
            head = "".join(f"<th>{e(c)}</th>" for c in cols)
            body = "".join("<tr>" + "".join(f"<td>{e(str(x.get(c, '')))}</td>" for c in cols) + "</tr>" for x in rows)
            return f"<table><tr>{head}</tr>{body}</table>"

        def pct(rows):
            return [dict(x, printed=f"{x['printed']:.1%}", actual=f"{x['actual']:.1%}") for x in rows]

        def s(v):                                      # any stored value as escaped text; NULL = empty (2026-10-26 audit:
            return "" if v is None else e(str(v))      # a pick with no chance / line type / grade made the whole page a 500)

        def pc(v):
            return f"{v:.0%}" if isinstance(v, (int, float)) and math.isfinite(v) else ""

        picks = []
        for x in r["rows"]:
            x = collections.defaultdict(lambda: None, x)
            form = (f"<form method=post action=/admin/closing><input type=hidden name=token value='{tok}'>"
                    f"<input type=hidden name=id value={s(x['id'])}><input name=closing size=4 "
                    f"value='{s(x['closing_line'])}'><button>save</button></form>")
            picks.append(f"<tr><td>{s(x['id'])}</td><td>{s((x['logged_at'] or '')[:16])}</td><td>{s(x['player'])}</td>"
                         f"<td>{s(x['opponent'])}</td><td>{s(x['prop'])} {s(x['line'])} {s(x['line_type'])}</td>"
                         f"<td><b>{s(x['pick'])}</b> {s(x['grade'])} {pc(x['p_pick'])} {s(x['units'])}U</td>"
                         f"<td>{s(x['lambda'])}</td><td>{s(x['market'])}</td><td>{s(x['rec_hit'])}</td>"
                         f"<td>{s(x['status'])}</td><td>{s(x['actual'])}</td>"
                         f"<td>{'' if x['hit'] is None else ('HIT' if x['hit'] else 'miss')}</td>"
                         f"<td>{form}</td><td>{s(x['clv'])}</td><td>{s(x['requests'])}</td></tr>")
        page = f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Pick log</title><style>body{{font:14px system-ui;background:#0b0e13;color:#e8ecf3;margin:16px}}
table{{border-collapse:collapse;margin:8px 0 20px}}td,th{{border:1px solid #252e3f;padding:4px 8px;text-align:left}}
th{{color:#8b95a8}}input{{background:#1a2130;color:#e8ecf3;border:1px solid #252e3f}}.k{{color:#8b95a8}}
.scroll{{overflow-x:auto}}</style>
<h2>Phase 10 - real lines</h2>
<p><span class=k>Logged</span> {r['total']} · <span class=k>open</span> {r['open']} · <span class=k>settled</span> {r['settled']}
 · <span class=k>void</span> {r['void']} · <span class=k>hit rate</span> {'' if r['hit_rate_all'] is None else f"{r['hit_rate_all']:.1%}"}
 · <span class=k>CLV avg</span> {r['clv_avg']} ({r['clv_n']} lines, {'' if r['clv_positive'] is None else f"{r['clv_positive']:.0%} positive"})
 · <span class=k>units net (flat ±1, not payout-adjusted)</span> {r['units_net_flat']}</p>
<p>Hit rate uses {r.get('scored', r['settled'])} records with a known outcome. Calibration tables use {r.get('calibration_scored', r['settled'])} records with both an outcome and a valid saved probability. Missing outcomes: {r.get('missing_outcome', 0)}; missing/invalid probabilities: {r.get('missing_probability', 0)}. All records remain in the log.</p>
<h3>Printed % vs actual (settled)</h3>{table(pct(r['by_printed_pct']), ['bucket', 'n', 'printed', 'actual'])}
<h3>By units</h3>{table(pct(r['by_units']), ['bucket', 'n', 'printed', 'actual'])}
<h3>Picks (newest first) - enter Underdog's closing line for CLV</h3><div class=scroll><table>
<tr><th>#</th><th>logged</th><th>player</th><th>vs</th><th>line</th><th>pick</th><th>λ</th><th>market</th><th>last10</th>
<th>status</th><th>actual</th><th>result</th><th>closing</th><th>CLV</th><th>asks</th></tr>{''.join(picks)}</table></div>"""
        return self.send(200, page.encode(), "text/html; charset=utf-8")


def unsubscribe_page(member, token, confirm=False):
    """One-tap alert unsubscribe from a Whop message (workspace_alerts.unsub_url). GET shows a button (link previews and prefetch
    never unsubscribe anyone); POST with the same signed token turns delivery off. A bad or missing token changes nothing."""
    import workspace_alerts, workspace_api
    esc = htmllib.escape
    shell = ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             "<title>Scout alerts</title><style>body{background:#000;color:#eee;font:16px/1.5 system-ui,sans-serif;margin:0;padding:24px;max-width:520px}"
             "button{min-height:44px;padding:0 18px;border-radius:8px;border:1px solid #f5c842;background:#f5c842;color:#000;font-weight:600}"
             "p{color:#bbb}</style></head><body><h1>Scout alerts</h1>{}</body></html>")
    if not workspace_alerts.unsub_ok(member, token):
        return 400, shell.replace("{}", "<p>This link is not valid. Turn alerts off in Scout under Alerts.</p>").encode(), "text/html; charset=utf-8"
    if not confirm:
        body = (f"<p>Stop background alerts for this account? Live highlight on the research screen is not affected.</p>"
                f"<form method=post action='/alerts/unsubscribe'><input type=hidden name=m value='{esc(member, quote=True)}'>"
                f"<input type=hidden name=t value='{esc(token, quote=True)}'><button type=submit>Stop alerts</button></form>")
        return 200, shell.replace("{}", body).encode(), "text/html; charset=utf-8", {"Cache-Control": "no-store"}
    workspace_alerts.subscription(workspace_api.data_dir(), member, False)
    return 200, shell.replace("{}", "<p>Done. Background alerts are off for this account. You can turn them back on in Scout under Alerts.</p>").encode(), \
        "text/html; charset=utf-8", {"Cache-Control": "no-store"}


# owner 2026-11-05 ("why do i have to turn them on in render"): the remaining-brief surfaces passed tests, review and the 700-member
# load test, so the running server turns them ON unless Render says otherwise ("0"/"off" = off). Set only when the process runs as
# the server, never on import, so tests keep their explicit flag-off checks. SCOUT_UI_TOKENS (gold design) stays off: owner keeps the blue.
DEFAULT_ON = ("SCOUT_BOARD_H2H", "SCOUT_GENIE", "SCOUT_SLIP_HEADER", "SCOUT_PUBLIC_RECORD", "SCOUT_PUBLIC_RECORD_SLIPS",
              "SCOUT_RECORD_RELIABILITY", "SCOUT_RECORD_STATUS", "SCOUT_CLOSING_LINES", "SCOUT_ALERT_DEEPLINK",
              # Phase 4, no keys needed (owner: "why do i have to turn them on in render")
              "SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE", "SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_MARKET_TOOLS",
              "SCOUT_GENIE_PERSONA", "SCOUT_ADMIN_COVERAGE",
              # owner 2026-11-05 "Turn everything on": competitor-spec board fixes, line history, +EV board, slip header v2.
              # SCOUT_PARITY_PREFS = Kelly sizing as a % of bankroll (display only, never a grader stake).
              "SCOUT_PARITY_EDGE", "SCOUT_PARITY_EV_BAND", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_HIT_SORT",
              "SCOUT_PARITY_PLAYS", "SCOUT_PARITY_PREFS", "SCOUT_PARITY_REPORT", "SCOUT_LINE_HISTORY", "SCOUT_PICKEM_EV",
              "SCOUT_SLIP_HEADER_V2")
# NOT default-on (owner 2026-11-05, "dont turn on letter containment"): SCOUT_LETTER_CONTAINMENT stays OFF, so CS2 / LoL letters
# show exactly as before. CS2_MAP_WEIGHTED stays OFF (shadow only); SCOUT_CAL_CHANCE / SPORTS_CHANCE_CAL stay OFF.
# SCOUT_BOOK_DABBLE (owner 2026-11-05, "simply code it we are partnered"): api.dabble.com answered 200 without credentials, so it is
# on like Sleeper. Betr (401 + location check), ParlayPlay (Cloudflare 403) and Boom (app only) are blocked in book_providers.json and
# have no flag.
# ec-1 calibrated printed chance (SCOUT_CAL_CHANCE) is OFF by default again: owner 2026-11-05 after go-live, "yeah you've
# ruined the grader". Render SCOUT_CAL_CHANCE=1 turns it back on; the layer and its artifact stay in the code.


def apply_default_flags(env=os.environ):
    for k in DEFAULT_ON:
        env.setdefault(k, "1")


if __name__ == "__main__":
    apply_default_flags()
    __import__("membudget").tune_allocator()          # before any thread starts (2026-10-18 out-of-memory fix)
    threading.Thread(target=warm_start, daemon=True, name="warm-start").start()
    threading.Thread(target=board_forever, daemon=True, name="underdog-board").start()
    def _pack_cache():   # one-time: old per-file cache -> one packed file (same data, ~1/3 the disk)
        try:
            import kvstore
            n = kvstore.migrate()
            STORAGE["migrated_files"] = n
        except Exception:
            traceback.print_exc()
    threading.Thread(target=_pack_cache, daemon=True, name="pack-cache").start()
    if PRELOAD:
        threading.Thread(target=preload_forever, daemon=True, name="preload").start()   # cleans up first
    else:
        threading.Thread(target=cleanup, daemon=True, name="cleanup").start()
    # research tools last (matchid tables, then the moneyline refreshers): their own thread, failures isolated
    threading.Thread(target=start_research, daemon=True, name="research").start()
    # continuous learning (learning.py): measures settled matches every 15 min - shadow only, never touches grading
    threading.Thread(target=lambda: __import__("learning").forever(), daemon=True, name="learning").start()
    # LoL data center: one supervised low-priority child process (line archive + Leaguepedia); not released to members
    threading.Thread(target=lol_center_supervise, daemon=True, name="lol-center").start()
    import workspace_alerts
    workspace_alerts.start(sys.modules[__name__])
    # lc-1: the CS2 / LoL letter containment check, its own thread (reads the stored P1 export and the LoL worker's
    # learning.json every 6 h; requests only read the cached state)
    letter_containment.start(sys.modules[__name__], DATA_DIR)
    # pgn-1 pre-game news archive (pregame_news/cs2_picks.py): its own thread reads the pick log read-only every 10 min
    # and archives the roster facts each forecast saw - research only, nothing on a request path (PREGAME_NEWS=0 = off)
    try:
        __import__("pregame_news.cs2_picks").cs2_picks.start(Path(picklog.DB).parent)
    except Exception:
        traceback.print_exc()
    try:                                              # ec-1: when this artifact first went live (the record maps forecasts
        esports_cal.mark_live(DATA_DIR)               # logged after it at the calibrated chance; earlier ones stay as logged)
    except Exception:
        traceback.print_exc()
    try:                                              # extra book providers' one poller (no-op with every SCOUT_BOOK_* off)
        import book_providers, workspace_api
        book_providers.start(workspace_api.data_dir())
    except Exception:
        traceback.print_exc()
    try:
        hltv.search_players("a")                      # loads the HLTV player-search index in the background
    except Exception:
        pass
    ThreadingHTTPServer.request_queue_size = 2048     # accept hundreds of connections at once
    ThreadingHTTPServer.daemon_threads = True
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"cs2_scout running on {HOST}:{PORT} - open http://localhost:{PORT}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
