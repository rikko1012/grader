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

Ingestion (members never call upstream) - every request goes through request() (the gateway):
  - one budget for ALL Thunderpick traffic (poller, admin probes, cs2 and csgo): SHARED_PER_MIN requests in any 60 s,
    kept in SQLite on the persistent disk, so threads, processes and overlapping deployments share it
  - a persisted cooldown: 429 -> Retry-After when supplied, else 60 s x 2^(n-1) with +-20% jitter (cap 30 min); 5xx /
    timeouts / network -> 30 s x 2^(n-1) with jitter; 401/403/404 -> 30 min; no automatic retries inside a request
  - adaptive automatic polling per endpoint (cs2 and csgo): 60 s, slowing to 300 s on repeated empty feeds; persisted
    schedule, cooldown and authentication stop (THUNDERPICK_AUTOPOLL=0 turns it off)
  - every request actually sent is an append-only request_log row (time, path, caller, instance, pid, commit, status,
    sanitized headers incl. request ids, a key-redacted body snippet of errors) - never the key
  - bounded timeouts (5 s connect, 10 s read)
  - an empty 200 is an empty feed (replaces the data, current); a failure keeps the last good data, marked STALE, and
    stale data is never used as a current comparison
  - retrieved_at is OUR fetch time - it is never presented as the source's quote time (the API has none)

History (CS2_DATA_DIR/.cache/thunderpick.db, append-only)
  raw_snapshots  every attempt (status, retrieved time, body sha256); the gzip body whenever it changed or every
                 HEARTBEAT_S; normalized events / markets for every changed body.  New snapshots never create past
                 pre-match coverage.

Probability (a TWO-outcome market; used for projections only once policy() establishes it as the series winner)
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
TIMEOUT = (5, 10)
BACKOFF_MAX = 300
CONFIG_RETRY_S = 600
RATE_WINDOW_S = 60
HEARTBEAT_S = 600                               # an unchanged body is stored again at most every 10 min
SNAPSHOT_MAX_MB = float(os.environ.get("THUNDERPICK_SNAPSHOT_MAX_MB") or 300)
APPROVED_HOSTS = {"thunderpick.io", "www.thunderpick.io"}


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


class FetchError(Exception):
    def __init__(self, kind, status=None, msg="", evidence=None):
        super().__init__(f"{kind}{f' HTTP {status}' if status else ''}{f': {msg}' if msg else ''}")
        self.kind, self.status, self.evidence = kind, status, evidence


_EVIDENCE_HEADERS = ("retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "ratelimit",
                     "ratelimit-policy", "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "server", "cf-ray",
                     "content-type", "date", "x-request-id", "request-id", "x-correlation-id", "x-amzn-requestid",
                     "x-trace-id", "cf-cache-status", "age")


def _evidence(r, key):
    """Rate-limit / server headers and a short body snippet of a non-200 answer, with the key redacted."""
    hdr = {}
    try:
        hdr = {k.lower(): str(v)[:120] for k, v in (r.headers or {}).items() if k.lower() in _EVIDENCE_HEADERS}
    except Exception:          # noqa: BLE001
        pass
    try:
        body = (r.content or b"")[:300].decode("utf-8", "replace")
    except Exception:          # noqa: BLE001
        body = ""
    if key:
        body = body.replace(key, "[redacted]")
        hdr = {k: v.replace(key, "[redacted]") for k, v in hdr.items()}
    return {"headers": hdr, "body": body}


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
    ev = _evidence(r, key)
    if st == 200:
        ev["body"] = ""                                 # a 200 body is stored as the snapshot, not in the audit
    if st == 429:
        raise FetchError("rate_limited", st, evidence=ev)
    if st in (401, 403):
        raise FetchError("auth", st, evidence=ev)
    if st == 404:
        raise FetchError("path", st, evidence=ev)
    if st >= 500:
        raise FetchError("upstream", st, evidence=ev)
    if st != 200:
        raise FetchError("http", st, evidence=ev)
    body = r.content
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FetchError("bad_json", st) from None
    if not isinstance(data, list):
        raise FetchError("bad_shape", st, "body is not a JSON array")
    return data, st, body, ev


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
            # the feed's own fixtureId when present (undocumented, seen 2026-10-13 on 37 of 61 events); else the number in
            # the match URL - a SOURCE identifier only, never canonical identity
            "source_event_id": (f"fx:{ev['fixtureId']}" if ev.get("fixtureId") not in (None, "") else url_id(ev.get("matchUrl"))),
            "url_id": url_id(ev.get("matchUrl")), "tournament": ev.get("tournament"), "game": ev.get("game"), "url": url,
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


# ---------------------------------------------------------------- the gateway (EVERY Thunderpick request goes through it)
# Owner, 2026-10-13 (429s at ~4 requests/min): one request budget for ALL Thunderpick traffic - poller, admin probes,
# retries, cs2 and csgo - shared across threads, processes and overlapping deployments (SQLite on the persistent disk);
# a cooldown that survives restarts (Retry-After when supplied, else growing backoff with jitter); automatic polling OFF
# during diagnosis (THUNDERPICK_AUTOPOLL=1 turns it on); and an audit row for every request actually sent.
SHARED_PER_MIN = int(os.environ.get("THUNDERPICK_SHARED_PER_MIN") or 4)     # all paths together, until the limit is known
COOLDOWN_BASE_S, COOLDOWN_MAX_S = 60, 1800
FAIL_COOLDOWN_S = 30
_GW_SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_slots (ts REAL NOT NULL, path TEXT, caller TEXT, instance TEXT, pid INTEGER);
CREATE INDEX IF NOT EXISTS budget_ts ON budget_slots (ts);
CREATE TABLE IF NOT EXISTS gw_state (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS request_log (id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at TEXT NOT NULL, path TEXT,
  caller TEXT, instance TEXT, pid INTEGER, git_commit TEXT, http_status INTEGER, outcome TEXT, duration_ms INTEGER,
  events INTEGER, headers TEXT, body_snippet TEXT, cooldown_until TEXT);
CREATE TRIGGER IF NOT EXISTS reqlog_no_update BEFORE UPDATE ON request_log BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS reqlog_no_delete BEFORE DELETE ON request_log BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""


def instance_id():
    import socket
    return os.environ.get("RENDER_INSTANCE_ID") or socket.gethostname()


def _gw(path=None):
    con = _connect(path)
    con.executescript(_GW_SCHEMA)
    return con


def _state(con, k, default=None):
    r = con.execute("SELECT v FROM gw_state WHERE k = ?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def _set_state(con, k, v):
    con.execute("INSERT OR REPLACE INTO gw_state (k, v) VALUES (?, ?)", (k, json.dumps(v)))


def cooldown(now=None, path=None):
    """The persisted cooldown {"until", "reason", "n429", "nfail"} (until = 0 when none)."""
    now = time.time() if now is None else now
    with _DB_LOCK:
        con = _gw(path)
        try:
            c = _state(con, "cooldown", {"until": 0, "reason": None, "n429": 0, "nfail": 0})
        finally:
            con.close()
    c["active"] = c.get("until", 0) > now
    c["remaining_s"] = round(max(0.0, c.get("until", 0) - now), 1)
    return c


def _reserve(api_path, caller, now, db=None):
    """Atomically: refuse during a cooldown, else take one slot of the shared budget (all paths together)."""
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.execute("BEGIN IMMEDIATE")
            c = _state(con, "cooldown", {"until": 0})
            if c.get("until", 0) > now:
                con.execute("ROLLBACK")
                return f"cooldown until {_iso(c['until'])} ({c.get('reason')})"
            con.execute("DELETE FROM budget_slots WHERE ts < ?", (now - RATE_WINDOW_S * 10,))
            n = con.execute("SELECT COUNT(*) FROM budget_slots WHERE ts > ?", (now - RATE_WINDOW_S,)).fetchone()[0]
            if n >= SHARED_PER_MIN:
                con.execute("ROLLBACK")
                return f"shared budget full ({n} of {SHARED_PER_MIN} in the last {RATE_WINDOW_S}s)"
            con.execute("INSERT INTO budget_slots VALUES (?,?,?,?,?)", (now, api_path, caller, instance_id(), os.getpid()))
            con.execute("COMMIT")
            return None
        finally:
            con.close()


def _retry_after_s(headers):
    v = (headers or {}).get("retry-after")
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        return max(0.0, parsedate_to_datetime(v).timestamp() - time.time())
    except Exception:          # noqa: BLE001
        return None


def _after(kind, headers, now, db=None, rnd=None):
    """Persist the cooldown after an answer: 429 -> Retry-After, else 60 s x 2^(n-1) +-20% jitter (cap 30 min);
    5xx / timeout / network -> 30 s x 2^(n-1) +-20%; auth / path -> 30 min; success -> clear."""
    import random
    rnd = rnd or random.random
    with _DB_LOCK:
        con = _gw(db)
        try:
            c = _state(con, "cooldown", {"until": 0, "reason": None, "n429": 0, "nfail": 0})
            if kind is None:
                c = {"until": 0, "reason": None, "n429": 0, "nfail": 0}
            elif kind == "rate_limited":
                c["n429"] = c.get("n429", 0) + 1
                ra = _retry_after_s(headers)
                wait = ra if ra is not None else min(COOLDOWN_BASE_S * 2 ** (c["n429"] - 1), COOLDOWN_MAX_S) * (0.8 + 0.4 * rnd())
                c.update(until=now + wait, reason=f"HTTP 429 ({'Retry-After' if ra is not None else 'backoff'} {wait:.0f}s)")
            elif kind in ("auth", "path", "config"):
                c.update(until=now + COOLDOWN_MAX_S, reason=f"{kind} - configuration problem")
            else:
                c["nfail"] = c.get("nfail", 0) + 1
                wait = min(FAIL_COOLDOWN_S * 2 ** (c["nfail"] - 1), COOLDOWN_MAX_S) * (0.8 + 0.4 * rnd())
                c.update(until=now + wait, reason=f"{kind} (backoff {wait:.0f}s)")
            _set_state(con, "cooldown", c)
            con.commit()
            return c
        finally:
            con.close()


def _log(sent_at, api_path, caller, status, outcome, ms, events, headers, body, until, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.execute("INSERT INTO request_log (sent_at, path, caller, instance, pid, git_commit, http_status, outcome, "
                        "duration_ms, events, headers, body_snippet, cooldown_until) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sent_at, api_path, caller, instance_id(), os.getpid(), (os.environ.get("RENDER_GIT_COMMIT") or "")[:12],
                         status, outcome, ms, events, json.dumps(headers or {}), (body or "")[:300], _iso(until) if until else None))
            con.commit()
        finally:
            con.close()


def request(name="cs2", caller="poller", fetch_fn=None, clock=time.time, db=None):
    """THE way to call Thunderpick: cooldown -> shared budget -> one request -> audit row -> cooldown update.
    Returns {"ok", "refused"?, "events"?, "status"?, "error"?, "data"?, "body"?}. Never raises, never retries."""
    if name not in PATHS:
        return {"ok": False, "refused": "path not allow-listed"}
    api_path = PATHS[name]
    if not enabled():
        return {"ok": False, "refused": "no key configured"}
    now = clock()
    why = _reserve(api_path, caller, now, db)
    if why:
        return {"ok": False, "refused": why}
    t0 = time.time()
    try:
        got = (fetch_fn or (lambda: fetch(path=api_path)))()
        data, status, body = got[:3]
        hdr = (got[3] if len(got) > 3 else {}) or {}
        c = _after(None, None, clock(), db)
        _log(_iso(now), api_path, caller, status, "ok", int((time.time() - t0) * 1000), len(data), hdr.get("headers"), "", None, db)
        return {"ok": True, "data": data, "status": status, "body": body, "events": len(data), "headers": hdr.get("headers")}
    except FetchError as e:
        ev = e.evidence or {}
        c = _after(e.kind, ev.get("headers"), clock(), db)
        _log(_iso(now), api_path, caller, e.status, e.kind, int((time.time() - t0) * 1000), None, ev.get("headers"),
             ev.get("body"), c.get("until"), db)
        return {"ok": False, "error": str(e), "kind": e.kind, "status": e.status, "evidence": ev, "cooldown": c}
    except Exception as e:      # noqa: BLE001
        c = _after("error", None, clock(), db)
        _log(_iso(now), api_path, caller, None, f"error {type(e).__name__}", int((time.time() - t0) * 1000), None, None, "",
             c.get("until"), db)
        return {"ok": False, "error": type(e).__name__, "kind": "error", "cooldown": c}


def request_log(limit=60, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT * FROM request_log ORDER BY id DESC LIMIT ?", (limit,))]
        finally:
            con.close()
    for r in rows:
        try:
            r["headers"] = json.loads(r["headers"] or "{}")
        except ValueError:
            pass
    return rows


# ---------------------------------------------------------------- the poller: adaptive, both endpoints, durable state
# Owner, 2026-10-13: automatic, conservative, durable. Each endpoint has its own persisted schedule (gw_state
# "sched:<name>"): 60 s while it returns markets; after repeated EMPTY answers it slows to 120 / 180 / 300 s. A 429, a
# 5xx, a timeout or a network error sets the gateway's persisted cooldown (all endpoints wait). Repeated authentication
# failures stop all requests and raise ONE alert until the key changes. Requests are spaced >= MIN_GAP_S apart.
# These are conservative starting settings, not the provider's confirmed limits.
BASE_INTERVAL_S = 60
EMPTY_STEPS = ((5, 60), (10, 120), (15, 180))       # empty streak below N -> interval; beyond the last -> MAX_INTERVAL_S
MAX_INTERVAL_S = 300
MIN_GAP_S = 20
TICK_S = 5
AUTH_BLOCK_AFTER = 3


def autopoll():
    return os.environ.get("THUNDERPICK_AUTOPOLL", "1") != "0"


def interval_for(empty_streak):
    for n, s in EMPTY_STEPS:
        if empty_streak < n:
            return s
    return MAX_INTERVAL_S


def _key_fp():
    k = _key()
    return hashlib.sha256(k.encode()).hexdigest()[:8] if k else None


def _sched(name, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            return _state(con, f"sched:{name}", {"next_at": 0, "empty_streak": 0, "interval_s": BASE_INTERVAL_S,
                                                 "requests": 0, "ok": 0, "empties": 0, "nonempty": 0,
                                                 "last_attempt": None, "last_success": None, "last_outcome": None,
                                                 "last_nonempty_at": None, "events_last": None})
        finally:
            con.close()


def _put(k, v, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            _set_state(con, k, v)
            con.commit()
        finally:
            con.close()


def _get(k, default=None, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            return _state(con, k, default)
        finally:
            con.close()


def auth_block(db=None):
    """The persisted authentication stop: {"blocked", "since", "failures", "key_fp"}; cleared when the key changes."""
    b = _get("auth", {"blocked": False, "failures": 0, "since": None, "key_fp": None}, db)
    if b.get("blocked") and b.get("key_fp") != _key_fp():
        b = {"blocked": False, "failures": 0, "since": None, "key_fp": None}
        _put("auth", b, db)
    return b


def _last_sent(db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            r = con.execute("SELECT MAX(ts) FROM budget_slots").fetchone()
            return r[0] or 0.0
        finally:
            con.close()


class Poller:
    def __init__(self, fetch_fns=None, clock=time.time, db=None, store=True, canonical_fn=None, board_fn=None):
        self.fetch_fns = fetch_fns or {}            # tests: {"cs2": fn, "csgo": fn}
        self.clock, self.db, self.store = clock, db, store
        self.canonical_fn, self.board_fn = canonical_fn, board_fn
        self.lock = threading.Lock()
        self.data = {}                               # name -> {"events" (normalized), "raw", "at", "empty"}
        self.matched = {}                            # match_id -> matched record (latest)
        self.last_sha, self.last_body_at = {}, {}
        self.store_error = None
        self.last_refused = None
        self.alert = None
        self._stop = threading.Event()
        self._thread = None

    # -- one request
    def poll_once(self, name="cs2", caller="poller"):
        now = self.clock()
        if auth_block(self.db).get("blocked"):
            self.last_refused = "authentication blocked - check the Thunderpick key"
            return None
        r = request(name, caller, self.fetch_fns.get(name), self.clock, self.db)
        if r.get("refused"):
            self.last_refused = r["refused"]
            return None
        self.last_refused = None
        sch = _sched(name, self.db)
        sch["requests"] += 1
        sch["last_attempt"] = _iso(now)
        if not r["ok"]:
            sch["last_outcome"] = r.get("kind")
            cd = r.get("cooldown") or {}
            sch["next_at"] = max(cd.get("until", 0), now + sch.get("interval_s", BASE_INTERVAL_S))
            if r.get("kind") == "auth":
                a = _get("auth", {"blocked": False, "failures": 0}, self.db)
                a["failures"] = a.get("failures", 0) + 1
                if a["failures"] >= AUTH_BLOCK_AFTER and not a.get("blocked"):
                    a.update(blocked=True, since=_iso(now), key_fp=_key_fp())
                    self.alert = (f"Thunderpick authentication failed {a['failures']} times in a row (HTTP {r.get('status')}) - "
                                  "requests stopped; update the key on Render (THUNDER_PICK_API) to resume")
                    print(f"[thunderpick] ALERT: {self.alert}", flush=True)
                _put("auth", a, self.db)
            _put(f"sched:{name}", sch, self.db)
            self._record(False, _iso(now), r.get("status"), r.get("kind"), api_path=PATHS[name], name=name)
            return False
        _put("auth", {"blocked": False, "failures": 0, "since": None, "key_fp": None}, self.db)
        data = r["data"]
        norm = normalize(data, _iso(now))
        for e in norm:
            e["api_path"] = PATHS[name]
        empty = len(data) == 0
        sch.update(ok=sch["ok"] + 1, last_success=_iso(now), last_outcome="empty" if empty else "events", events_last=len(data))
        if empty:
            sch["empties"] += 1
            sch["empty_streak"] = sch.get("empty_streak", 0) + 1
        else:
            sch["nonempty"] += 1
            sch["empty_streak"] = 0
            sch["last_nonempty_at"] = _iso(now)
        sch["interval_s"] = interval_for(sch["empty_streak"])
        sch["next_at"] = now + sch["interval_s"]
        _put(f"sched:{name}", sch, self.db)
        with self.lock:
            self.data[name] = {"events": norm, "raw": data, "at": now, "empty": empty}
        self._record(True, _iso(now), r["status"], None, r["body"], norm, api_path=PATHS[name], name=name)
        if not empty:
            try:
                self.refresh_matches()
            except Exception as e:      # noqa: BLE001 - matching never stops the feed
                self.store_error = f"matching: {type(e).__name__}: {str(e)[:120]}"
        return True

    def _record(self, ok, retrieved, status, kind, body=None, norm=None, api_path=PATH, name="cs2"):
        if not self.store:
            return
        try:
            sha, kept = record(ok, retrieved, status, kind, body, norm, self.last_sha.get(name), self.last_body_at.get(name, 0.0),
                               self.clock(), path=None, api_path=api_path)
            if sha:
                self.last_sha[name] = sha
            if kept:
                self.last_body_at[name] = self.clock()
            self.store_error = None
        except Exception as e:      # noqa: BLE001
            self.store_error = f"{type(e).__name__}: {str(e)[:120]}"

    # -- scheduler
    def due(self, now=None):
        """The endpoint to poll now (or None): due by its own schedule, outside the cooldown, spaced MIN_GAP_S."""
        now = self.clock() if now is None else now
        if not autopoll() or not enabled() or auth_block(self.db).get("blocked"):
            return None
        if cooldown(now, self.db)["active"] or now - _last_sent(self.db) < MIN_GAP_S:
            return None
        cands = [(s["next_at"], n) for n in PATHS for s in [_sched(n, self.db)] if s["next_at"] <= now]
        return min(cands)[1] if cands else None

    def tick(self):
        name = self.due()
        return self.poll_once(name) if name else None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:      # noqa: BLE001 - the background service never dies
                self.store_error = f"tick: {type(e).__name__}: {str(e)[:120]}"
            self._stop.wait(TICK_S)

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

    # -- freshness
    def current(self, name, now=None):
        now = self.clock() if now is None else now
        d = self.data.get(name)
        if not d:
            return False
        sch = _sched(name, self.db)
        limit = 2.5 * max(sch.get("interval_s", BASE_INTERVAL_S), BASE_INTERVAL_S) + 30
        return now - d["at"] <= limit and not cooldown(now, self.db)["active"]

    def events(self, now=None):
        """Current, valid events of every endpoint (deduplicated by the source event id; cs2 first)."""
        out, seen = [], set()
        for name in ("cs2", "csgo"):
            if not self.current(name, now):
                continue
            for e in self.data[name]["events"]:
                k = e.get("source_event_id") or (e.get("team1"), e.get("team2"), e.get("start_raw"))
                if k in seen:
                    continue
                seen.add(k)
                out.append(e)
        return out

    # -- matching + evidence
    def refresh_matches(self, now=None):
        now = self.clock() if now is None else now
        evs = [e for e in self.events(now) if not e.get("problem")]
        tz = tz_state(self.db)
        recs = {}
        ev_off = _get("tz_evidence", {}, self.db)
        for e in evs:
            m = match_event(e, tz.get("offset_h") if tz.get("resolved") else None, self.canonical_fn)
            if not m.get("match_id"):
                continue
            if m.get("offset_h") is not None:
                ev_off[m["match_id"]] = m["offset_h"]
            if m["match_id"] in recs:              # two Thunderpick events -> one match: ambiguous, drop both
                recs[m["match_id"]] = {"ambiguous": True}
                continue
            recs[m["match_id"]] = dict(m, event=e)
        _put("tz_evidence", dict(list(ev_off.items())[-300:]), self.db)
        resolve_tz(self.db)
        with self.lock:
            self.matched = {k: v for k, v in recs.items() if not v.get("ambiguous")}
        if self.board_fn:
            update_series_evidence(self.matched, self.board_fn(), self.db)
        policy(self.db, record_events=True)
        return self.matched

    def status(self):
        now = self.clock()
        sch = {n: dict(_sched(n, self.db), next_in_s=round(max(0.0, _sched(n, self.db)["next_at"] - now), 1)) for n in PATHS}
        for n, s in sch.items():
            s["next_attempt"] = _iso(s["next_at"]) if s.get("next_at") else "now"
            s["current"] = self.current(n, now)
        out = {"source": SOURCE, "enabled": enabled(), "key": key_diagnostic(), "autopoll": autopoll(),
               "running": bool(self._thread and self._thread.is_alive()), "endpoints": sch,
               "cooldown": cooldown(now, self.db), "auth": auth_block(self.db), "alert": self.alert,
               "last_refused": self.last_refused, "store_error": self.store_error,
               "budget": f"{SHARED_PER_MIN} requests / {RATE_WINDOW_S}s for ALL Thunderpick paths and callers together; "
                         f"requests >= {MIN_GAP_S}s apart; per endpoint {BASE_INTERVAL_S}s, slowing to {MAX_INTERVAL_S}s on empty feeds",
               "matched": len(self.matched), "instance": instance_id(), "pid": os.getpid()}
        out["state"] = self.state(now)
        out["blocker"] = blocker(self, now)
        return out

    def state(self, now=None):
        now = self.clock() if now is None else now
        if not enabled():
            return "not_configured"
        if auth_block(self.db).get("blocked"):
            return "auth_failed"
        c = cooldown(now, self.db)
        if c["active"] and (c.get("reason") or "").startswith("HTTP 429"):
            return "rate_limited"
        if any(self.current(n, now) for n in PATHS):
            return "ok" if self.events(now) else "empty"
        if c["active"]:
            return "unavailable"
        return "starting" if autopoll() else "paused"


def blocker(p, now):
    """The one current reason Thunderpick cannot feed projections yet (None when the policy is active)."""
    st = p.state(now)
    if st in ("not_configured", "auth_failed", "rate_limited", "unavailable", "paused", "starting"):
        return {"not_configured": "no key", "auth_failed": "authentication failed - update the key",
                "rate_limited": "rate-limited by the provider (cooldown running)", "unavailable": "feed not answering",
                "paused": "automatic polling off", "starting": "first requests pending"}[st]
    if st == "empty":
        return "both endpoints answer with an empty feed (no CS2 markets for this partner account)"
    pol = policy(p.db)
    return None if pol["active"] else "; ".join(pol["reasons"])


# ---------------------------------------------------------------- canonical matching
TZ_SCAN_H = range(-12, 15)
TZ_MIN_MATCHES, TZ_MIN_SHARE = 10, 0.9


def _canon(team_a, team_b, start_iso, fn=None):
    if fn is not None:
        return fn(team_a, team_b, start_iso)
    import moneyline
    return moneyline.canonical(team_a, team_b, start_iso, scope="series")


def match_event(e, offset_h=None, canonical_fn=None):
    """One Thunderpick event -> the canonical match (the id Polymarket / Kalshi / props / settlement use).
    Timezone resolved: start (naive) - offset = UTC, the canonical +-3 h window. Unresolved: the start is scanned over
    UTC-12..+14 and the event matches only when exactly ONE HLTV match of these two teams fits; its HLTV start gives the
    offset evidence. Ambiguous / unlinked -> no match."""
    from datetime import timedelta
    naive = parse_start(e.get("start_raw"))
    if naive is None:
        return {"reason": "no start time"}
    t1, t2 = e.get("team1"), e.get("team2")
    if offset_h is not None:
        st = (naive - timedelta(hours=offset_h)).replace(tzinfo=timezone.utc)
        c = _canon(t1, t2, st.strftime("%Y-%m-%dT%H:%M:%SZ"), canonical_fn)
        if (c.get("status") != "linked") or not c.get("match_id"):
            return {"reason": c.get("reason") or c.get("status") or "unlinked"}
        return {"match_id": c["match_id"], "flipped": bool(c.get("flipped")), "method": f"canonical, start UTC{-offset_h:+g}",
                "tz": "resolved", "start_utc": st.isoformat(), "offset_h": None, "hltv_match": c.get("hltv_match")}
    found = {}
    for h in TZ_SCAN_H:
        st = (naive - timedelta(hours=h)).replace(tzinfo=timezone.utc)
        c = _canon(t1, t2, st.strftime("%Y-%m-%dT%H:%M:%SZ"), canonical_fn)
        if c.get("status") == "linked" and c.get("source") == "hltv" and c.get("match_id"):
            found.setdefault(c["match_id"], c)
    if len(found) != 1:
        return {"reason": "no HLTV match of these teams near this start" if not found else "ambiguous: several HLTV matches"}
    mid, c = next(iter(found.items()))
    off = None
    hs = c.get("hltv_start")
    if hs:
        try:
            hdt = datetime.fromisoformat(str(hs).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
            off = round((naive - hdt).total_seconds() / 3600 * 4) / 4
        except ValueError:
            off = None
    return {"match_id": mid, "flipped": bool(c.get("flipped")), "method": "unique HLTV match of the two teams (timezone unconfirmed)",
            "tz": "unconfirmed", "start_utc": hs, "offset_h": off, "hltv_match": c.get("hltv_match")}


def tz_state(db=None):
    return _get("tz", {"resolved": False}, db)


def resolve_tz(db=None):
    """Timezone of startTime from evidence: >= TZ_MIN_MATCHES distinct matches, >= TZ_MIN_SHARE at the modal offset.
    Later disagreement (< 80% over >= 20 matches) un-resolves it."""
    ev = [v for v in _get("tz_evidence", {}, db).values() if v is not None]
    cur = tz_state(db)
    if not ev:
        return cur
    mode, n = Counter(ev).most_common(1)[0]
    share = n / len(ev)
    if not cur.get("resolved") and len(ev) >= TZ_MIN_MATCHES and share >= TZ_MIN_SHARE:
        cur = {"resolved": True, "offset_h": mode, "matches": len(ev), "share": round(share, 3), "resolved_at": _iso(time.time()),
               "meaning": f"startTime = UTC{mode:+g}"}
        _put("tz", cur, db)
    elif cur.get("resolved") and len(ev) >= 20 and share < 0.8:
        cur = {"resolved": False, "reason": f"offsets disagree (modal {mode:+g} only {share:.0%} of {len(ev)})"}
        _put("tz", cur, db)
    else:
        cur = dict(cur, evidence_matches=len(ev), modal_offset_h=mode, modal_share=round(share, 3))
    return cur


# ---------------------------------------------------------------- market scope evidence + the policy gate
POLICY_VERSION = "tp-first-1"
ON_ACTIVATE = None          # set by the server: returns the old-vs-new comparison report (market_odds.compare)
SERIES_MIN_MATCHES, SERIES_MAX_MAE, SERIES_MIN_CORR = 15, 0.06, 0.85


def oriented(rec):
    """Team-A (canonical) view of a matched event: odds, raw implied, margin-adjusted probabilities."""
    e = rec["event"]
    da, db_ = (e["team2_odds"], e["team1_odds"]) if rec.get("flipped") else (e["team1_odds"], e["team2_odds"])
    tw = two_way(da, db_)
    return {"odds_a": da, "odds_b": db_, "two_way": tw}


def update_series_evidence(matched, board, db=None):
    """Thunderpick main-market probability vs the usable exchange price of the SAME canonical match (team A view)."""
    ev = _get("series_evidence", {}, db)
    for m in board or []:
        rec = matched.get(m.get("match_id"))
        if not rec or rec["event"].get("draw_present"):
            continue
        ex = None
        for pl in ("polymarket", "kalshi"):
            b = (m.get("platforms") or {}).get(pl) or {}
            if b.get("usable") and b.get("prob_a") is not None:
                ex = b["prob_a"]
                break
        o = oriented(rec)
        if ex is None or not o["two_way"]:
            continue
        ev[m["match_id"]] = {"market": rec["event"].get("main_market"), "tp": o["two_way"]["p_a"], "ex": ex}
    _put("series_evidence", dict(list(ev.items())[-400:]), db)
    return ev


def series_established(db=None):
    """{market name: evidence} for main markets whose meaning (two-way SERIES winner, correct orientation) is established."""
    ev = _get("series_evidence", {}, db)
    by = {}
    for v in ev.values():
        by.setdefault(v["market"], []).append((v["tp"], v["ex"]))
    out = {}
    for name, pairs in by.items():
        n = len(pairs)
        mae = sum(abs(a - b) for a, b in pairs) / n
        ma, mb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
        va, vb = sum((a - ma) ** 2 for a, _ in pairs), sum((b - mb) ** 2 for _, b in pairs)
        corr = sum((a - ma) * (b - mb) for a, b in pairs) / (va * vb) ** 0.5 if va > 0 and vb > 0 else 0.0
        ok = n >= SERIES_MIN_MATCHES and mae <= SERIES_MAX_MAE and corr >= SERIES_MIN_CORR
        out[name] = {"matches": n, "mae": round(mae, 4), "corr": round(corr, 3), "established": ok}
    return out


def policy(db=None, record_events=False):
    """The Thunderpick-first projection policy gate. Active only when: the startTime timezone is resolved, a main market
    is established as the two-way series winner (orientation checked against exchange prices), and THUNDERPICK_POLICY
    is not "0". Activation and deactivation are recorded once each with their evidence."""
    tz = tz_state(db)
    est = series_established(db)
    names = sorted(n for n, v in est.items() if v["established"])
    reasons = []
    if os.environ.get("THUNDERPICK_POLICY", "1") == "0":
        reasons.append("policy switched off (THUNDERPICK_POLICY=0)")
    if not tz.get("resolved"):
        reasons.append("startTime timezone not yet established from matched events")
    if not names:
        reasons.append("series-winner meaning of the main market not yet established against exchange prices"
                       + (f" ({', '.join(f'{k}: {v['matches']} matches, MAE {v['mae']}, corr {v['corr']}' for k, v in est.items())})" if est else ""))
    active = not reasons
    out = {"version": POLICY_VERSION, "active": active, "series_markets": names, "timezone": tz, "evidence": est,
           "reasons": reasons}
    if record_events:
        prev = _get("policy_state", {"active": False}, db)
        if prev.get("active") != active:
            hist = _get("policy_events", [], db)
            hist.append({"at": _iso(time.time()), "active": active, "version": POLICY_VERSION, "series_markets": names,
                         "timezone": tz, "evidence": est, "reasons": reasons})
            _put("policy_events", hist[-50:], db)
            _put("policy_state", {"active": active, "since": _iso(time.time())}, db)
            print(f"[thunderpick] projection policy {POLICY_VERSION} {'ACTIVE' if active else 'inactive'}: {reasons or names}", flush=True)
            if active and ON_ACTIVATE:
                try:                    # the old-source vs Thunderpick-source comparison, saved with the activation
                    _put("compare_on_activation", {"at": _iso(time.time()), "version": POLICY_VERSION,
                                                   "report": ON_ACTIVATE()}, db)
                except Exception as e:  # noqa: BLE001
                    _put("compare_on_activation", {"at": _iso(time.time()), "error": f"{type(e).__name__}: {str(e)[:160]}"}, db)
    return out


# ---------------------------------------------------------------- readers: projections, the Moneylines tab
def projection_odds(team, opponent, start_iso, now=None, poller=None, canonical_fn=None):
    """Thunderpick's series-winner odds for `team` vs `opponent` in the polymarket.match_odds shape, or
    {"found": False, "reason": ...}. Only when the policy is active, the match links exactly (canonical id + orientation),
    the quote is current and pre-match, two-way with valid odds and no draw. Never a 50% stand-in."""
    p = poller or POLLER
    now_ts = p.clock() if now is None else now
    pol = policy(p.db)
    if not pol["active"]:
        return {"found": False, "reason": "Thunderpick policy inactive: " + "; ".join(pol["reasons"])}
    if not team or not opponent:
        return {"found": False, "reason": "team or opponent unknown"}
    c = _canon(team, opponent, start_iso, canonical_fn or p.canonical_fn)
    if c.get("status") != "linked" or not c.get("match_id"):
        return {"found": False, "reason": f"match not linked ({c.get('reason') or c.get('status')})"}
    with p.lock:
        rec = p.matched.get(c["match_id"])
    if not rec:
        return {"found": False, "reason": "no Thunderpick event for this match"}
    e = rec["event"]
    name = next((n for n in PATHS if PATHS[n] == e.get("api_path")), "cs2")
    if not p.current(name, now_ts):
        return {"found": False, "reason": "Thunderpick quote stale"}
    if e.get("main_market") not in pol["series_markets"]:
        return {"found": False, "reason": f"market '{e.get('main_market')}' not established as the series winner"}
    if e.get("draw_present"):
        return {"found": False, "reason": "draw price listed - not a two-way series market"}
    st = rec.get("start_utc")
    try:
        if st and datetime.fromisoformat(str(st).replace("Z", "+00:00")).timestamp() <= now_ts:
            return {"found": False, "reason": "match started - no pre-match price"}
    except ValueError:
        return {"found": False, "reason": "start time unreadable"}
    o = oriented(rec)
    if not o["two_way"]:
        return {"found": False, "reason": "invalid odds"}
    team_is_a = not bool(c.get("flipped"))
    pa = o["two_way"]["p_a"]
    p_team = pa if team_is_a else 1 - pa
    from polymarket import prob_to_american
    retrieved = p.data[name]["at"]
    prov = {"source": SOURCE, "policy": POLICY_VERSION, "market": e.get("main_market"), "scope": "series winner",
            "match_id": c["match_id"], "source_event_id": e.get("source_event_id"), "api_path": e.get("api_path"),
            "odds_team": o["odds_a"] if team_is_a else o["odds_b"], "odds_opp": o["odds_b"] if team_is_a else o["odds_a"],
            "implied_team_raw": round(1 / (o["odds_a"] if team_is_a else o["odds_b"]), 6), "margin": o["two_way"]["margin"],
            "p_team": round(p_team, 6), "method": o["two_way"]["method"], "retrieved_at": _iso(retrieved),
            "source_updated_at": None, "source_updated_note": "the feed publishes no quote update time",
            "snapshot_sha256": p.last_sha.get(name), "match_url": e.get("url"), "timezone": pol["timezone"].get("meaning")}
    return {"found": True, "team": team, "opponent": opponent, "start": st, "url": e.get("url"),
            "title": f"{e.get('team1')} vs {e.get('team2')}", "book": SOURCE, "team_prob": round(p_team, 4),
            "opp_prob": round(1 - p_team, 4), "team_ml": prob_to_american(p_team), "opp_ml": prob_to_american(1 - p_team),
            "maps": {}, "flags": ["Win chance from Thunderpick odds (proportional margin-adjusted); per-map chances derived "
                                  "from the series price - no map prices mixed in from another source"],
            "provenance": prov}


def board_block(match_id, now=None, poller=None):
    """Thunderpick's block for one Moneylines card (team A = canonical team A), or None when it has no event."""
    p = poller or POLLER
    now_ts = p.clock() if now is None else now
    with p.lock:
        rec = p.matched.get(match_id)
    if not rec:
        return None
    e = rec["event"]
    name = next((n for n in PATHS if PATHS[n] == e.get("api_path")), "cs2")
    pol = policy(p.db)
    o = oriented(rec)
    cur = p.current(name, now_ts)
    scope_ok = e.get("main_market") in pol["series_markets"] and not e.get("draw_present")
    tw = o["two_way"]
    blk = {"source": SOURCE, "market": e.get("main_market"), "scope_confirmed": scope_ok,
           "scope": "series winner" if scope_ok else "main market - series scope not yet confirmed",
           "odds_a": o["odds_a"], "odds_b": o["odds_b"], "draw_odds": e.get("draw_odds"),
           "implied_a_raw": round(1 / o["odds_a"], 4) if o["odds_a"] else None,
           "implied_b_raw": round(1 / o["odds_b"], 4) if o["odds_b"] else None,
           "p_a": tw["p_a"] if tw else None, "p_b": tw["p_b"] if tw else None, "margin": tw["margin"] if tw else None,
           "method": "proportional margin-adjusted market estimate", "retrieved_at": _iso(p.data[name]["at"]),
           "source_updated_at": None, "current": cur, "status": "current" if cur else "stale",
           "usable": bool(cur and scope_ok and tw), "url": e.get("url"), "match_method": rec.get("method"),
           "timezone": "confirmed" if rec.get("tz") == "resolved" else "unconfirmed",
           "note": "listed bookmaker odds - no stake limits, depth or execution confirmation in the feed"}
    return blk


POLLER = Poller()


def start():
    if POLLER.board_fn is None:
        try:
            import moneyline
            POLLER.board_fn = moneyline.board
        except Exception:      # noqa: BLE001
            pass
    return POLLER.start()


PUBLIC_LABEL = {"ok": "Thunderpick odds current", "empty": "Thunderpick: no CS2 markets in the feed right now",
                "rate_limited": "Thunderpick unavailable: rate-limited by the provider",
                "unavailable": "Thunderpick unavailable: the feed is not answering",
                "auth_failed": "Thunderpick unavailable: connection problem",
                "starting": "Thunderpick: connecting", "paused": "Thunderpick: paused",
                "not_configured": "Thunderpick not connected"}


def public_state():
    st = POLLER.state()
    last = max((d["at"] for d in POLLER.data.values()), default=None)
    return {"source": SOURCE, "state": st, "label": PUBLIC_LABEL.get(st, st), "retrieved_at": _iso(last),
            "time_basis": "our retrieval time", "policy_active": policy(POLLER.db)["active"]}


def admin_view(samples=3, probe=None):
    """Admin: status (per endpoint: last attempt / success / next attempt / blocker), the request audit, the policy gate
    with its evidence, inventories per endpoint, a few raw events. probe = cs2 | csgo -> ONE request via the gateway."""
    probe_out = None
    if probe in PATHS:
        r = POLLER.poll_once(probe, caller="admin_probe")
        probe_out = {"path": PATHS[probe], "result": {True: "ok", False: "failed", None: "refused"}[r],
                     "refused": POLLER.last_refused}
    inv = {}
    for n in PATHS:
        d = POLLER.data.get(n)
        inv[n] = {"retrieved_at": _iso(d["at"]) if d else None, "inventory": inventory(d["raw"]) if d else None,
                  "sample_events": (d["raw"][:samples] if d else [])}
    return {"status": POLLER.status(), "probe": probe_out, "policy": policy(POLLER.db),
            "policy_events": _get("policy_events", [], POLLER.db), "tz_evidence_matches": len(_get("tz_evidence", {}, POLLER.db)),
            "matched": [{"match_id": k, "method": v.get("method"), "tz": v.get("tz"), "teams": [v["event"]["team1"], v["event"]["team2"]],
                         "flipped": v.get("flipped"), "market": v["event"].get("main_market")} for k, v in list(POLLER.matched.items())[:40]],
            "endpoints": inv, "request_log": request_log(60), "public_state": public_state(),
            "compare_on_activation": _get("compare_on_activation", None, POLLER.db),
            "store": {"path": str(db_path()), "mb": round(_store_mb(), 2)}}
