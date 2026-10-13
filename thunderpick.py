#!/usr/bin/env python3
"""
thunderpick.py - Thunderpick CS2 odds through the partner odds API (owner, 2026-10-13: "Thunderpick is over Polymarket and
Kalshi odds-wise - these are real odds").  Read-only; nothing here touches grading.

API (partner integration guide, 2026-10-13)
  GET https://aggr.mightykiwi.xyz/api/partners-odds/esport/cs2      header  api-key: <THUNDERPICK_API_KEY>
  200 -> JSON array of events (an empty [] is a normal empty feed); 401/403 key; 404 path; 429 rate limit; 5xx upstream.
  Rate limit: 10 requests / minute / source IP / endpoint path, fixed window, no queue.
  Event: name (main market, "typically 1X2"), team1, team2, team1Odds, team2Odds, drawOdds (number | null | absent),
         matchUrl, startTime "yyyy-MM-dd HH:mm:ss" (timezone NOT documented), competition, markets[]
  Market: name, selections {label: decimal odds}, baseLine (str | null), isMainLine (bool | null)
  The guide documents no quote-update time, no live / suspended flag, no stable event or market id and no tournament id.

Ingestion (one coordinated poller per server process - members never call upstream)
  - every POLL_S (15 s); a shared RequestBudget allows at most BUDGET_PER_MIN (8) requests in any 60 s, retries included
  - bounded timeouts (5 s connect, 10 s read); 429 -> wait for the fixed window to reset (60 s) then back off; 5xx /
    timeouts / connection errors -> 30 s, 60 s, ... capped at 300 s; 401/403/404 -> 10 min (configuration problem)
  - an empty 200 is an empty feed (replaces the data, current); a failure keeps the last good data, marked STALE, and
    stale data is never used as a current comparison
  - retrieved_at is OUR fetch time - it is never presented as the source's quote time (the API has none)

History (CS2_DATA_DIR/.cache/thunderpick.db, append-only)
  raw_snapshots  every attempt (status, retrieved time, body sha256); the gzip body whenever it changed or every
                 HEARTBEAT_S; normalized events / markets for every changed body.  New snapshots never create past
                 pre-match coverage.

Probability (a confirmed TWO-outcome series-winner market only - see MAIN_MARKET_CONFIRMED)
  qA = 1/dA, qB = 1/dB, margin = qA + qB - 1, pA = qA/(qA + qB), pB = qB/(qA + qB)
  = a proportional margin-adjusted market estimate; raw odds and the quoted margin are always kept next to it.
  A draw price present -> never normalized over two teams (three-way market, kept apart).
"""
import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE = "https://aggr.mightykiwi.xyz"
PATH = "/api/partners-odds/esport/cs2"          # the CS2 feed (the guide's csgo example is a different identifier)
# The only paths this module ever calls (fixed, approved). "csgo" is read only by the admin coverage check - 2026-10-13 the
# cs2 path answered 200 with an empty list while CS2 matches were scheduled; the guide's example uses csgo.
PATHS = {"cs2": PATH, "csgo": "/api/partners-odds/esport/csgo"}
SOURCE = "thunderpick"
POLL_S = 15
BUDGET_PER_MIN = 8                              # documented limit 10 / min / IP / path - keep 2 in reserve
TIMEOUT = (5, 10)
BACKOFF_MAX = 300
CONFIG_RETRY_S = 600
RATE_WINDOW_S = 60
STALE_AFTER = 90                                # data older than this (or after a failed refresh) is stale
HEARTBEAT_S = 600                               # an unchanged body is stored again at most every 10 min
SNAPSHOT_MAX_MB = float(os.environ.get("THUNDERPICK_SNAPSHOT_MAX_MB") or 300)
APPROVED_HOSTS = {"thunderpick.io", "www.thunderpick.io"}
# Set to the exact main-market name(s) only after a real response shows they mean "series winner, two outcomes, no
# draw" - until then no probability is computed and nothing reaches members (2026-10-13: not yet confirmed).
MAIN_MARKET_CONFIRMED = frozenset()


KEY_NAMES = ("THUNDERPICK_API_KEY", "THUNDER_PICK_API")     # the second is the name used on Render (2026-10-13)


def _key():
    for n in KEY_NAMES:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return None


def enabled():
    return _key() is not None and os.environ.get("THUNDERPICK", "1") != "0"


def key_diagnostic():
    """Which related environment variable NAMES exist (never values) - to find a key stored under another name."""
    names = sorted(k for k in os.environ if any(w in k.upper() for w in ("THUNDER", "PARTNER", "KIWI", "TP_")))
    return {"expected": " or ".join(KEY_NAMES), "present": _key() is not None, "related_names": names,
            "disabled_by_flag": os.environ.get("THUNDERPICK") == "0"}


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def data_dir():
    return Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)


def db_path():
    return data_dir() / ".cache" / "thunderpick.db"


# ---------------------------------------------------------------- request budget (shared by every call to PATH)
class RequestBudget:
    """At most `limit` requests in any rolling `window` seconds - first tries and retries alike."""

    def __init__(self, limit=BUDGET_PER_MIN, window=RATE_WINDOW_S):
        self.limit, self.window = limit, window
        self.sent = deque()
        self.lock = threading.Lock()

    def take(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            while self.sent and now - self.sent[0] >= self.window:
                self.sent.popleft()
            if len(self.sent) >= self.limit:
                return False
            self.sent.append(now)
            return True

    def wait_s(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if len(self.sent) < self.limit:
                return 0.0
            return max(0.0, self.window - (now - self.sent[0]))


class FetchError(Exception):
    def __init__(self, kind, status=None, msg=""):
        super().__init__(f"{kind}{f' HTTP {status}' if status else ''}{f': {msg}' if msg else ''}")
        self.kind, self.status = kind, status


def fetch(session=None, key=None, path=PATH):
    """One request to an allow-listed path. Returns (events list, http status, body bytes). Never logs or returns the key."""
    if path not in PATHS.values():
        raise FetchError("config", msg="path not allow-listed")
    key = key or _key()
    if not key:
        raise FetchError("config", msg="THUNDERPICK_API_KEY not set")
    get = (session or requests).get
    try:
        r = get(BASE + path, headers={"api-key": key, "Accept": "application/json", "User-Agent": "cs2-scout/1.0"},
                timeout=TIMEOUT)
    except requests.Timeout:
        raise FetchError("timeout") from None
    except requests.RequestException as e:
        raise FetchError("network", msg=type(e).__name__) from None
    st = r.status_code
    if st == 429:
        raise FetchError("rate_limited", st)
    if st in (401, 403):
        raise FetchError("auth", st)
    if st == 404:
        raise FetchError("path", st)
    if st >= 500:
        raise FetchError("upstream", st)
    if st != 200:
        raise FetchError("http", st)
    body = r.content
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FetchError("bad_json", st) from None
    if not isinstance(data, list):
        raise FetchError("bad_shape", st, "body is not a JSON array")
    return data, st, body


# ---------------------------------------------------------------- validation / normalization
_ID_IN_URL = re.compile(r"/(?:match|event|matches)/(?:[^/]*?-)?(\d{4,})")


def safe_url(url):
    """The match link only when it is https on an approved Thunderpick host; else None."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return None
    if u.scheme != "https" or (u.hostname or "").lower() not in APPROVED_HOSTS or u.username or u.password:
        return None
    return u.geturl()


def url_id(url):
    """A source-side id read from the match URL (a SOURCE identifier only - never canonical identity)."""
    m = _ID_IN_URL.search(urlparse(str(url or "")).path or "")
    return m.group(1) if m else None


def odds(x):
    """Decimal odds > 1.0 as float, else None (0, 1, negatives, strings, NaN are invalid)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and 1.0 < v < 1000.0 else None


def parse_start(s):
    """'yyyy-MM-dd HH:mm:ss' -> naive datetime (the timezone is NOT documented - never assumed here)."""
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def two_way(d_a, d_b):
    """Proportional margin-adjusted estimate for a confirmed two-outcome market: {qA, qB, margin, pA, pB} or None."""
    a, b = odds(d_a), odds(d_b)
    if a is None or b is None:
        return None
    qa, qb = 1.0 / a, 1.0 / b
    s = qa + qb
    return {"q_a": round(qa, 6), "q_b": round(qb, 6), "margin": round(s - 1.0, 6),
            "p_a": round(qa / s, 6), "p_b": round(qb / s, 6),
            "method": "proportional margin-adjusted market estimate"}


def normalize(events, retrieved_at):
    """Raw events -> list of normalized events (problems recorded, nothing invented)."""
    out = []
    for i, ev in enumerate(events if isinstance(events, list) else []):
        if not isinstance(ev, dict):
            out.append({"index": i, "problem": "event is not an object"})
            continue
        probs = []
        t1, t2 = str(ev.get("team1") or "").strip(), str(ev.get("team2") or "").strip()
        if not t1 or not t2:
            probs.append("missing team name")
        elif t1.lower() == t2.lower():
            probs.append("same team twice")
        d1, d2 = odds(ev.get("team1Odds")), odds(ev.get("team2Odds"))
        if d1 is None or d2 is None:
            probs.append("invalid or missing team odds")
        draw_present = "drawOdds" in ev and ev.get("drawOdds") is not None
        dd = odds(ev.get("drawOdds")) if draw_present else None
        if draw_present and dd is None:
            probs.append("invalid draw odds")
        start = parse_start(ev.get("startTime"))
        if start is None:
            probs.append("missing or unparseable startTime")
        url = safe_url(ev.get("matchUrl"))
        if ev.get("matchUrl") and url is None:
            probs.append("matchUrl not on an approved Thunderpick https host")
        mk = []
        for m in ev.get("markets") or []:
            if not isinstance(m, dict):
                continue
            sel = m.get("selections") if isinstance(m.get("selections"), dict) else {}
            mk.append({"name": str(m.get("name") or ""), "base_line": m.get("baseLine"), "is_main_line": m.get("isMainLine"),
                       "selections": {str(k): (odds(v), v) for k, v in sel.items()},
                       "invalid_selections": sum(1 for v in sel.values() if odds(v) is None)})
        out.append({
            "index": i, "source": SOURCE, "retrieved_at": retrieved_at,
            "source_event_id": url_id(ev.get("matchUrl")), "url": url,
            "main_market": str(ev.get("name") or ""), "team1": t1, "team2": t2,
            "team1_odds": d1, "team2_odds": d2, "draw_odds": dd, "draw_present": draw_present,
            "start_raw": ev.get("startTime"), "start_naive": start.isoformat() if start else None, "start_tz": "unconfirmed",
            "competition": ev.get("competition"), "markets": mk,
            "two_way": two_way(d1, d2) if not draw_present and not probs else None,
            "problem": "; ".join(probs) or None,
        })
    return out


def inventory(events):
    """What the real feed contains - field names, main-market names, market names, lines, draws, hosts, duplicates."""
    ev_keys, mk_keys, mains, comps, names, hosts = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    draws = lines = mainline = starts_bad = 0
    pairs = Counter()
    samples = {}
    for ev in events if isinstance(events, list) else []:
        if not isinstance(ev, dict):
            continue
        ev_keys.update(ev.keys())
        mains[str(ev.get("name"))] += 1
        comps[str(ev.get("competition"))] += 1
        if ev.get("drawOdds") is not None:
            draws += 1
        if parse_start(ev.get("startTime")) is None:
            starts_bad += 1
        try:
            hosts[(urlparse(str(ev.get("matchUrl") or "")).hostname or "none").lower()] += 1
        except ValueError:
            hosts["invalid"] += 1
        pairs[(str(ev.get("team1")).lower(), str(ev.get("team2")).lower(), str(ev.get("startTime")))] += 1
        for m in ev.get("markets") or []:
            if not isinstance(m, dict):
                continue
            mk_keys.update(m.keys())
            n = str(m.get("name"))
            names[n] += 1
            lines += m.get("baseLine") is not None
            mainline += bool(m.get("isMainLine"))
            samples.setdefault(n, {"selections": list((m.get("selections") or {}).keys())[:6], "baseLine": m.get("baseLine"),
                                   "isMainLine": m.get("isMainLine"), "event": f"{ev.get('team1')} vs {ev.get('team2')}"})
    return {"events": sum(1 for e in events if isinstance(e, dict)) if isinstance(events, list) else 0,
            "event_fields": dict(ev_keys), "market_fields": dict(mk_keys), "main_market_names": dict(mains),
            "competitions": dict(comps.most_common(40)), "market_names": dict(names.most_common(200)),
            "market_samples": samples, "markets_with_baseLine": lines, "markets_marked_main_line": mainline,
            "events_with_draw_odds": draws, "unparseable_start_times": starts_bad, "match_url_hosts": dict(hosts),
            "duplicate_events": sum(c - 1 for c in pairs.values() if c > 1)}


# ---------------------------------------------------------------- history
_DB_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, retrieved_at TEXT NOT NULL, ok INTEGER,
  http_status INTEGER, error_kind TEXT, body_sha256 TEXT, body_gz BLOB, n_events INTEGER, changed INTEGER);
CREATE TABLE IF NOT EXISTS events (snapshot_id INTEGER, retrieved_at TEXT, idx INTEGER, source_event_id TEXT, team1 TEXT,
  team2 TEXT, main_market TEXT, team1_odds REAL, team2_odds REAL, draw_odds REAL, start_raw TEXT, competition TEXT,
  url TEXT, problem TEXT, PRIMARY KEY (snapshot_id, idx));
CREATE TABLE IF NOT EXISTS markets (snapshot_id INTEGER, idx INTEGER, m INTEGER, name TEXT, base_line TEXT,
  is_main_line INTEGER, selections TEXT, PRIMARY KEY (snapshot_id, idx, m));
CREATE TRIGGER IF NOT EXISTS raw_no_update BEFORE UPDATE ON raw_snapshots BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_delete BEFORE DELETE ON raw_snapshots BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def _connect(path=None):
    p = Path(path or db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    try:
        con.execute("ALTER TABLE raw_snapshots ADD COLUMN api_path TEXT")
    except sqlite3.OperationalError:
        pass
    return con


def _store_mb(path=None):
    p = Path(path or db_path())
    return sum(f.stat().st_size for f in (p, Path(str(p) + "-wal")) if f.exists()) / 1e6


def record(ok, retrieved_at, status=None, error_kind=None, body=None, normalized=None, last_sha=None, last_body_at=0.0,
           now_ts=None, path=None, api_path=PATH):
    """Append one attempt. The body (gzip) + normalized rows are stored when the body changed or HEARTBEAT_S passed.
    Returns (sha, stored_body). Above SNAPSHOT_MAX_MB only the status row is written."""
    now_ts = time.time() if now_ts is None else now_ts
    sha = hashlib.sha256(body).hexdigest() if body is not None else None
    changed = sha is not None and sha != last_sha
    keep = body is not None and (changed or now_ts - last_body_at >= HEARTBEAT_S) and _store_mb(path) < SNAPSHOT_MAX_MB
    with _DB_LOCK:
        con = _connect(path)
        try:
            cur = con.execute("INSERT INTO raw_snapshots (retrieved_at, ok, http_status, error_kind, body_sha256, body_gz, "
                              "n_events, changed, api_path) VALUES (?,?,?,?,?,?,?,?,?)",
                              (retrieved_at, int(ok), status, error_kind, sha, gzip.compress(body) if keep else None,
                               len(normalized) if normalized is not None else None, int(changed), api_path))
            sid = cur.lastrowid
            if keep and normalized:
                for e in normalized:
                    con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (sid, retrieved_at, e["index"], e.get("source_event_id"), e.get("team1"), e.get("team2"),
                                 e.get("main_market"), e.get("team1_odds"), e.get("team2_odds"), e.get("draw_odds"),
                                 e.get("start_raw"), e.get("competition"), e.get("url"), e.get("problem")))
                    for j, m in enumerate(e.get("markets") or []):
                        con.execute("INSERT INTO markets VALUES (?,?,?,?,?,?,?)",
                                    (sid, e["index"], j, m["name"], None if m["base_line"] is None else str(m["base_line"]),
                                     None if m["is_main_line"] is None else int(bool(m["is_main_line"])),
                                     json.dumps({k: v[1] for k, v in m["selections"].items()})))
            con.commit()
        finally:
            con.close()
    return sha, keep


# ---------------------------------------------------------------- the poller
class Poller:
    def __init__(self, fetch_fn=None, budget=None, poll_s=POLL_S, store=True, clock=time.time, sleep=None):
        self.fetch_fn = fetch_fn or fetch
        self.budget = budget or RequestBudget()
        self.poll_s, self.store, self.clock = poll_s, store, clock
        self.lock = threading.Lock()
        self.events, self.raw = [], []
        self.last_success = self.last_attempt = None
        self.last_ok, self.last_error, self.last_kind, self.failures = False, None, None, 0
        self.next_delay = poll_s
        self.empty = False
        self.attempts = self.successes = self.rate_limited = 0
        self.last_sha, self.last_body_at = None, 0.0
        self.store_error = None
        self._stop = threading.Event()
        self._thread = None

    def _delay_after(self, kind):
        if kind == "rate_limited":
            return min(RATE_WINDOW_S * (2 ** max(0, self.failures - 1)), BACKOFF_MAX)
        if kind in ("auth", "path", "config"):
            return CONFIG_RETRY_S
        return min(30 * (2 ** max(0, self.failures - 1)), BACKOFF_MAX)

    def poll_once(self):
        """One attempt inside the shared budget. Success replaces the data (an empty list is a valid empty feed);
        failure keeps the last good data (stale) and backs off."""
        now = self.clock()
        if not self.budget.take(now):
            self.next_delay = max(self.poll_s, self.budget.wait_s(now))
            return None
        self.last_attempt, self.attempts = now, self.attempts + 1
        retrieved = _iso(now)
        try:
            data, status, body = self.fetch_fn()
        except FetchError as e:
            with self.lock:
                self.last_ok, self.failures = False, self.failures + 1
                self.last_error, self.last_kind = str(e), e.kind
                self.rate_limited += e.kind == "rate_limited"
                self.next_delay = self._delay_after(e.kind)
            self._record(False, retrieved, e.status, e.kind)
            return False
        except Exception as e:      # noqa: BLE001 - never kill the poller
            with self.lock:
                self.last_ok, self.failures = False, self.failures + 1
                self.last_error, self.last_kind = f"{type(e).__name__}", "error"
                self.next_delay = self._delay_after("error")
            self._record(False, retrieved, None, "error")
            return False
        norm = normalize(data, retrieved)
        with self.lock:
            self.events, self.raw = norm, data
            self.empty = len(data) == 0
            self.last_success, self.last_ok, self.last_error, self.last_kind = now, True, None, None
            self.failures, self.successes, self.next_delay = 0, self.successes + 1, self.poll_s
        self._record(True, retrieved, status, None, body, norm)
        return True

    def _record(self, ok, retrieved, status, kind, body=None, norm=None):
        if not self.store:
            return
        try:
            sha, kept = record(ok, retrieved, status, kind, body, norm, self.last_sha, self.last_body_at, self.clock())
            if sha:
                self.last_sha = sha
            if kept:
                self.last_body_at = self.clock()
            self.store_error = None
        except Exception as e:      # noqa: BLE001 - a disk problem never stops the odds
            self.store_error = f"{type(e).__name__}: {str(e)[:120]}"

    def is_current(self, now=None):
        now = self.clock() if now is None else now
        return bool(self.last_ok and self.last_success and now - self.last_success <= STALE_AFTER)

    def snapshot(self):
        """(events, freshness) for readers - members never trigger an upstream call."""
        now = self.clock()
        with self.lock:
            age = None if self.last_success is None else round(now - self.last_success, 1)
            return list(self.events), {"source": SOURCE, "current": self.is_current(now), "stale": not self.is_current(now),
                                       "retrieved_at": _iso(self.last_success), "age_s": age, "empty_feed": self.empty,
                                       "time_basis": "our retrieval time (the API publishes no quote timestamp)"}

    def status(self):
        now = self.clock()
        with self.lock:
            return {"source": SOURCE, "enabled": enabled(), "key": key_diagnostic(), "running": bool(self._thread and self._thread.is_alive()),
                    "current": self.is_current(now), "last_success": _iso(self.last_success),
                    "last_attempt": _iso(self.last_attempt), "age_s": None if self.last_success is None else round(now - self.last_success, 1),
                    "last_error": self.last_error, "error_kind": self.last_kind, "consecutive_failures": self.failures,
                    "next_poll_s": round(self.next_delay, 1), "attempts": self.attempts, "successes": self.successes,
                    "rate_limited": self.rate_limited, "events": len(self.events), "empty_feed": self.empty,
                    "budget": f"{self.budget.limit} requests / {self.budget.window}s (documented limit 10 / min / IP / path)",
                    "store_error": self.store_error, "main_market_confirmed": sorted(MAIN_MARKET_CONFIRMED)}

    def _loop(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.next_delay)

    def start(self):
        if not enabled():
            return False
        with self.lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="thunderpick", daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()


POLLER = Poller()


def start():
    return POLLER.start()


_COVERAGE_BUDGETS = {k: RequestBudget() for k in PATHS if k != "cs2"}     # per path, like the provider's limit
COVERAGE = {}


def coverage_probe(name, fetch_fn=None, clock=time.time):
    """Admin: ONE request to another allow-listed path (e.g. csgo) inside that path's own budget; the raw body is
    recorded with its path. Returns a small summary + inventory (never the key)."""
    if name not in _COVERAGE_BUDGETS:
        return {"path": name, "result": "not allow-listed"}
    now = clock()
    if not _COVERAGE_BUDGETS[name].take(now):
        return {"path": PATHS[name], "result": "skipped: request budget in use"}
    retrieved = _iso(now)
    try:
        data, status, body = (fetch_fn or (lambda: fetch(path=PATHS[name])))()
    except FetchError as e:
        try:
            record(False, retrieved, e.status, e.kind, api_path=PATHS[name])
        except Exception:      # noqa: BLE001
            pass
        out = {"path": PATHS[name], "retrieved_at": retrieved, "result": "failed", "error": str(e)}
        COVERAGE[name] = out
        return out
    norm = normalize(data, retrieved)
    try:
        record(True, retrieved, status, None, body, norm, api_path=PATHS[name])
    except Exception:          # noqa: BLE001
        pass
    out = {"path": PATHS[name], "retrieved_at": retrieved, "result": "ok", "http_status": status, "events": len(data),
           "inventory": inventory(data), "sample_events": data[:3]}
    COVERAGE[name] = out
    return out


def admin_view(samples=3):
    """Admin only: status + the inventory of the latest real response + a few raw events (no key, ever)."""
    with POLLER.lock:
        raw = list(POLLER.raw)
    return {"status": POLLER.status(), "inventory": inventory(raw), "sample_events": raw[:samples],
            "normalized_problems": Counter(e.get("problem") for e in POLLER.events if e.get("problem")).most_common(10),
            "store": {"path": str(db_path()), "mb": round(_store_mb(), 2)}, "coverage_checks": COVERAGE}
