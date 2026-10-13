#!/usr/bin/env python3
"""
thunderpick.py - Thunderpick CS2 odds through the partner odds API (owner, 2026-10-13: "Thunderpick is over Polymarket and
Kalshi odds-wise - these are real odds").  Read-only towards the provider; nothing here grades.

API (partner integration guide, 2026-10-13)
  GET https://aggr.mightykiwi.xyz/api/partners-odds/esport/cs2      header  api-key: <THUNDERPICK_API_KEY>
  200 -> JSON array of events (an empty [] is a normal empty feed); 401/403 key; 404 path; 429 rate limit; 5xx upstream.
  Rate limit: 10 requests / minute / source IP / endpoint path, fixed window, no queue.
  Event: name (main market, "typically 1X2"), team1, team2, team1Odds, team2Odds, drawOdds (number | null | absent),
         matchUrl, startTime "yyyy-MM-dd HH:mm:ss" (timezone NOT documented), competition, markets[]
  Market: name, selections {label: decimal odds}, baseLine (str | null), isMainLine (bool | null)
  The guide documents no quote-update time, no live / suspended flag, no stable event or market id and no tournament id.
  MEASURED, not provider-confirmed: the CS2 matches arrive on esport/csgo (esport/cs2 answers []); startTime fits UTC+0
  against HLTV starts; the event-level main market is labelled "Match Winner" with per-map markets labelled separately.

Ingestion (members never call upstream) - every request goes through request() (the gateway):
  - ONE atomic reservation (SQLite BEGIN IMMEDIATE on the persistent disk) checks and takes, for ALL Thunderpick traffic
    (poller, admin probe, cs2 and csgo): no cooldown running, no other request in flight (a lease, expires after
    LEASE_S), >= MIN_GAP_S since the last request sent, SHARED_PER_MIN requests in any 60 s. Shared by the threads and
    processes of one host; Render runs one instance on the disk and its deploys were serial in the request log - two
    hosts could not share it.
  - request_log is append-only: a 'started' row before the request is sent, a 'completed' row after (same attempt_id),
    a 'refused' row for every refusal - time, path, caller, instance, pid, commit, status, duration, sanitized headers
    incl. request ids, a key-redacted body snippet of errors. Never the key.
  - persisted cooldown: 429 -> Retry-After when supplied, else 60 s x 2^(n-1) x (0.8..1.2 jitter) capped at 30 min
    (jitter BEFORE the cap), n = the 429s of the last R429_WINDOW_S - a persisted, decaying count that a success (an
    empty 200, an admin probe) does NOT reset; 5xx / timeouts / network -> 30 s x 2^(n-1); 401/403/404 -> 30 min
  - adaptive automatic polling per endpoint (cs2 and csgo): 60 s; slower after repeated EMPTY answers (120 / 180 / 300 s)
    and while 429s persist (120 / 240 / 300 s by the 429s of the last 30 min - it relaxes only as they age out of the
    window, i.e. after a sustained clean period); persisted schedule, cooldown and authentication stop
    (THUNDERPICK_AUTOPOLL=0 turns it off)
  - bounded timeouts (5 s connect, 10 s read); request() never raises and never retries
  - status reads (status / state / public_state / admin_view / market_types / policy / board_block / projection_odds)
    never call upstream and write nothing; probe(name) is the one explicit admin action that sends a request

Eligibility (owner 2026-10-13: conservative) - a quote feeds projections ONLY when its availability is "current":
  its event is in its endpoint's latest SUCCESSFUL answer, that answer is nonempty and <= QUOTE_MAX_AGE_S old (age from
  that answer's retrieval time), the market type is enabled (market_types), the series format allowed (bo1 / bo3 /
  bo5), two valid odds, no draw price, the policy active and the canonical start (earliest of the HLTV start, the
  caller's start and the converted Thunderpick start) in the future. Otherwise `availability` says why:
    endpoint_empty        the endpoint's latest successful answer listed no events (earlier quotes: display / history)
    not_in_latest_answer  the event is absent from its endpoint's latest successful nonempty answer
    stale                 the latest successful answer is older than QUOTE_MAX_AGE_S (failures / cooldown - a failed
                          request neither extends nor voids anything by itself)
    started | scope_unconfirmed | draw_listed | format_excluded (BO2 / unknown) | invalid_odds | policy_inactive |
    unlinked | ambiguous
  Earlier quotes stay for display with their OWN retrieval time and snapshot hash - never renewed.
  retrieved_at is OUR time (the request's send time - the answer is at least that old); the feed publishes no quote
  time, so source_updated_at is always None.

History (CS2_DATA_DIR/.cache/thunderpick.db; raw_snapshots, events, request_log, market_evidence, policy_log append-only)
  raw_snapshots  a status row for every attempt (status, retrieval time, body sha256, n_events); the gzip body of a
                 CHANGED answer at most once per BODY_EVERY_S per path (and the first after start)
  events         normalized event rows (teams, odds, draw, start, ids, tournament, game) of every CHANGED answer - a
                 restart rebuilds the last answer from them when its body was not stored. No per-market rows (they
                 grew the store ~38 MB/h). Above SNAPSHOT_MAX_MB only status rows; status warns above 80%.

Probability (a TWO-outcome market; feeds projections only under the policy - Match Winner, label rule)
  qA = 1/dA, qB = 1/dB, margin = qA + qB - 1, pA = qA/(qA + qB), pB = qB/(qA + qB)
  = a proportional margin-adjusted market probability; raw odds and the quoted margin are always kept next to it.
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
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE = "https://aggr.mightykiwi.xyz"
PATH = "/api/partners-odds/esport/cs2"          # the CS2 feed (the guide's csgo example is a different identifier)
# The only paths this module ever calls (fixed, approved). 2026-10-13 the cs2 path answered 200 with an empty list while
# CS2 matches were scheduled; csgo carries them (measured behaviour - not a provider statement, provider question 1).
PATHS = {"cs2": PATH, "csgo": "/api/partners-odds/esport/csgo"}
_NAME_OF = {v: k for k, v in PATHS.items()}
SOURCE = "thunderpick"
TIMEOUT = (5, 10)
BACKOFF_MAX = 300
CONFIG_RETRY_S = 600
RATE_WINDOW_S = 60
BODY_EVERY_S = 600                              # a changed body is stored (gzip) at most every 10 min per path
SNAPSHOT_MAX_MB = float(os.environ.get("THUNDERPICK_SNAPSHOT_MAX_MB") or 2000)   # polling-history budget (all segments)
SEGMENT_MAX_MB = float(os.environ.get("THUNDERPICK_SEGMENT_MAX_MB") or 200)       # one history segment file
STORE_WARN_SHARE = 0.8
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


def _ts(s):
    """ISO time (or epoch seconds) -> epoch seconds; a naive ISO time is read as UTC; None when absent / unreadable."""
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        d = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _commit():
    return (os.environ.get("RENDER_GIT_COMMIT") or "")[:12] or None


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
_MATCH_PATH = re.compile(r"^/match/(\d+)/?$")


def safe_url(url):
    """The match link ONLY in the form https://thunderpick.io|www.thunderpick.io/match/<digits> (query and fragment
    dropped, no credentials, default port); anything else -> None."""
    try:
        u = urlparse(str(url or ""))
        port = u.port
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if u.scheme != "https" or host not in APPROVED_HOSTS or u.username or u.password or port not in (None, 443):
        return None
    m = _MATCH_PATH.match(u.path or "")
    return f"https://{host}/match/{m.group(1)}" if m else None


def url_id(url):
    """A source-side id read from the match URL (a SOURCE identifier only - never canonical identity)."""
    m = _ID_IN_URL.search(urlparse(str(url or "")).path or "")
    return m.group(1) if m else None


def source_event_id(fixture_id, uid):
    """The namespaced source event id: 'thunderpick:fx:<fixtureId>' (the feed's own id, undocumented, on ~60% of events),
    else 'thunderpick:url:<number in matchUrl>', else None. A SOURCE identifier only - never canonical identity."""
    if fixture_id not in (None, ""):
        return f"{SOURCE}:fx:{fixture_id}"
    return f"{SOURCE}:url:{uid}" if uid else None


def _namespaced(sid):
    """History rows written before 2026-10-13 WS1 hold 'fx:<id>' / '<url number>' -> the namespaced form."""
    s = str(sid or "")
    if not s or s.startswith(f"{SOURCE}:"):
        return s or None
    return f"{SOURCE}:{s}" if s.startswith("fx:") else (f"{SOURCE}:url:{s}" if s.isdigit() else s)


def _legacy(sid):
    s = str(sid or "")
    if s.startswith(f"{SOURCE}:fx:"):
        return s[len(SOURCE) + 1:]
    if s.startswith(f"{SOURCE}:url:"):
        return s[len(SOURCE) + 5:]
    return s


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
        url = safe_url(ev.get("matchUrl"))      # not an approved /match/<id> link: no link shown (the event still links -
                                                  # a provider URL change must not silently drop coverage)
        mk = []
        for m in ev.get("markets") or []:
            if not isinstance(m, dict):
                continue
            sel = m.get("selections") if isinstance(m.get("selections"), dict) else {}
            mk.append({"name": str(m.get("name") or ""), "base_line": m.get("baseLine"), "is_main_line": m.get("isMainLine"),
                       "selections": {str(k): (odds(v), v) for k, v in sel.items()},
                       "invalid_selections": sum(1 for v in sel.values() if odds(v) is None)})
        fx = ev.get("fixtureId")
        fixture_id = None if fx in (None, "") else str(fx)
        uid = url_id(ev.get("matchUrl")) if url else None          # an id only from an approved match link
        out.append({
            "index": i, "source": SOURCE, "retrieved_at": retrieved_at,
            # the feed's own fixtureId when present (undocumented, seen 2026-10-13 on 37 of 61 events); else the number in
            # the match URL - namespaced, a SOURCE identifier only, never canonical identity
            "source_event_id": source_event_id(fixture_id, uid), "fixture_id": fixture_id,
            "url_id": uid, "tournament": ev.get("tournament"), "game": ev.get("game"), "url": url,
            "main_market": str(ev.get("name") or ""), "team1": t1, "team2": t2,
            "team1_odds": d1, "team2_odds": d2, "draw_odds": dd, "draw_present": draw_present,
            "start_raw": ev.get("startTime"), "start_naive": start.isoformat() if start else None, "start_tz": "unconfirmed",
            "competition": ev.get("competition"), "markets": mk,
            "two_way": two_way(d1, d2) if not draw_present and not probs else None,
            "problem": "; ".join(probs) or None,
        })
    return out


_ODDS_PROBLEMS = {"invalid or missing team odds", "invalid draw odds"}


def _ekey(e):
    """An event's identity within the feed: its namespaced source id, else (team1, team2, startTime)."""
    return e.get("source_event_id") or ("noid", e.get("team1"), e.get("team2"), e.get("start_raw"))


def _skey(e):
    k = _ekey(e)
    return k if isinstance(k, str) else "|".join(str(x) for x in k)


def inventory(events):
    """What the real feed contains - field names, main-market names, ALL market names (no truncation: one answer is
    bounded by the feed), lines, draws, hosts, duplicates."""
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
            "competitions": dict(comps.most_common()), "market_names": dict(names.most_common()),
            "market_names_total": len(names), "market_samples": samples, "markets_with_baseLine": lines,
            "markets_marked_main_line": mainline, "events_with_draw_odds": draws, "unparseable_start_times": starts_bad,
            "match_url_hosts": dict(hosts), "duplicate_events": sum(c - 1 for c in pairs.values() if c > 1)}


# ---------------------------------------------------------------- history (one SQLite file, schema ensured once per path)
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
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TABLE IF NOT EXISTS budget_slots (ts REAL NOT NULL, path TEXT, caller TEXT, instance TEXT, pid INTEGER);
CREATE INDEX IF NOT EXISTS budget_ts ON budget_slots (ts);
CREATE TABLE IF NOT EXISTS gw_state (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS request_log (id INTEGER PRIMARY KEY AUTOINCREMENT, sent_at TEXT NOT NULL, path TEXT,
  caller TEXT, instance TEXT, pid INTEGER, git_commit TEXT, http_status INTEGER, outcome TEXT, duration_ms INTEGER,
  events INTEGER, headers TEXT, body_snippet TEXT, cooldown_until TEXT);
CREATE TRIGGER IF NOT EXISTS reqlog_no_update BEFORE UPDATE ON request_log BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS reqlog_no_delete BEFORE DELETE ON request_log BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TABLE IF NOT EXISTS market_evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, level TEXT,
  label TEXT, entry_sha TEXT, entry TEXT);
CREATE INDEX IF NOT EXISTS market_evidence_label ON market_evidence (level, label, id);
CREATE TRIGGER IF NOT EXISTS mev_no_update BEFORE UPDATE ON market_evidence BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS mev_no_delete BEFORE DELETE ON market_evidence BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TABLE IF NOT EXISTS policy_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, version TEXT,
  active INTEGER, reasons TEXT, evidence TEXT, git_commit TEXT);
CREATE TRIGGER IF NOT EXISTS plog_no_update BEFORE UPDATE ON policy_log BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER IF NOT EXISTS plog_no_delete BEFORE DELETE ON policy_log BEGIN SELECT RAISE(ABORT, 'append-only'); END;
"""
# columns added after the first release (ALTER TABLE when an older file lacks them)
_ADDED = {"raw_snapshots": (("api_path", "TEXT"),),
          "events": (("tournament", "TEXT"), ("game", "TEXT"), ("fixture_id", "TEXT"), ("url_id", "TEXT"),
                     ("draw_present", "INTEGER"), ("api_path", "TEXT")),
          "request_log": (("phase", "TEXT"), ("attempt_id", "TEXT"))}
_INDEXES = """
CREATE INDEX IF NOT EXISTS events_sid ON events (source_event_id);
CREATE INDEX IF NOT EXISTS raw_path ON raw_snapshots (api_path, id);
CREATE INDEX IF NOT EXISTS raw_sha ON raw_snapshots (body_sha256);
"""
_READY = set()                                  # db paths whose schema this process has ensured


def _connect(path=None):
    """A connection to the history file. The schema (tables, added columns, indexes, triggers) is ensured once per path
    per process - later connections (status reads included) execute no DDL at all."""
    p = Path(path or db_path())
    k = str(p)
    if k in _READY and p.exists():
        return sqlite3.connect(k, timeout=10)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(k, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(_SCHEMA)
    for t, cols in _ADDED.items():
        have = {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
        for c, typ in cols:
            if c not in have:
                con.execute(f"ALTER TABLE {t} ADD COLUMN {c} {typ}")
    con.executescript(_INDEXES)
    con.commit()
    _READY.add(k)
    return con


def _rebuild(rows, retrieved_at):
    """Normalized `events` rows of one snapshot -> normalized events (markets are not stored; the event-level fields
    are). Draw presence and stored problems are kept, so an event never becomes two-way by being rebuilt."""
    raw, probs = [], []
    for r in rows:
        (sid, t1, t2, mm, o1, o2, dd, start, comp, url, problem, tour, game, fx, uid, dp) = r
        e = {"name": mm, "team1": t1, "team2": t2, "team1Odds": o1, "team2Odds": o2, "matchUrl": url, "startTime": start,
             "competition": comp, "tournament": tour, "game": game, "markets": []}
        if dp or dd is not None or "draw odds" in (problem or ""):
            e["drawOdds"] = dd if dd is not None else "invalid"
        if fx not in (None, ""):
            e["fixtureId"] = fx
        raw.append(e)
        probs.append((problem, _namespaced(sid), uid))
    norm = normalize(raw, retrieved_at)
    for e, (problem, sid, uid) in zip(norm, probs):
        both = [x for x in (e.get("problem"), problem) if x]
        e["problem"] = "; ".join(dict.fromkeys("; ".join(both).split("; "))) if both else None
        if e.get("problem"):
            e["two_way"] = None
        if sid:                                 # the stored id is the identity (legacy 'fx:' rows had no fixture_id)
            e["source_event_id"] = sid
            if sid.startswith(f"{SOURCE}:fx:") and not e.get("fixture_id"):
                e["fixture_id"] = sid[len(SOURCE) + 4:]
        e["url_id"] = e.get("url_id") or uid
    return norm


# ---------------------------------------------------------------- polling history: bounded segments (2026-10-13)
# The polling history (raw_snapshots / events) is written to SEGMENT files under .cache/thunderpick_history/, a new one
# whenever the current reaches SEGMENT_MAX_MB; whole OLDEST segments are deleted when all segments together pass
# SNAPSHOT_MAX_MB. Inside a segment rows stay append-only (triggers). Gateway state, the request audit, the market
# evidence and the policy log stay in thunderpick.db and are never rotated or pruned (the request budget and cooldowns
# survive). Pruning never removes replay evidence: every forecast saves the selected quote itself (odds, teams, start,
# source ids, retrieval time, snapshot sha256) in its grader inputs (odds_provenance) - the raw polling history is
# supporting detail. Readers search the segments newest first, then the pre-segment history in thunderpick.db.


def history_dir():
    return data_dir() / ".cache" / "thunderpick_history"


def _file_mb(f):
    f = Path(f)
    return sum(x.stat().st_size for x in (f, Path(str(f) + "-wal")) if x.exists()) / 1e6


def _segment_files():
    d = history_dir()
    return sorted(d.glob("history-*.db")) if d.exists() else []          # oldest first (names sort by time)


def _history_sources(path=None):
    """Files to read the polling history from, newest first: an explicit path alone (tests / tools), else the segments
    then the pre-segment history in thunderpick.db."""
    if path is not None:
        return [Path(path)]
    legacy = db_path()
    return list(reversed(_segment_files())) + ([legacy] if legacy.exists() else [])


def _prune_segments(keep):
    """Delete whole oldest segments (never `keep`, the one being written) while all segments pass SNAPSHOT_MAX_MB."""
    segs = _segment_files()
    total = sum(_file_mb(s) for s in segs)
    deleted = []
    for s in segs:
        if total <= SNAPSHOT_MAX_MB or s == keep:
            break
        mb = _file_mb(s)
        try:
            for x in (s, Path(str(s) + "-wal"), Path(str(s) + "-shm")):
                if x.exists():
                    x.unlink()
            _READY.discard(str(s))
            total -= mb
            deleted.append(s.name)
        except OSError:
            break                                            # in use: tried again on the next rotation
    return deleted


def _current_segment(now_ts=None):
    """The segment to append to: the newest while it is under SEGMENT_MAX_MB, else a new one (then old ones pruned)."""
    segs = _segment_files()
    if segs and _file_mb(segs[-1]) < SEGMENT_MAX_MB:
        return segs[-1]
    now_ts = time.time() if now_ts is None else now_ts
    d = history_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(now_ts)) + f"{int((now_ts % 1) * 1e6):06d}"
    seg = d / f"history-{stamp}.db"
    _prune_segments(keep=seg)
    return seg


def _seg_latest(api_path, f):
    """One history file: (latest successful answer, latest NONEMPTY answer) as (retrieved_at, n_events, sha) or None."""
    with _DB_LOCK:
        con = _connect(f)
        try:
            last = con.execute("SELECT retrieved_at, n_events, body_sha256 FROM raw_snapshots WHERE api_path = ? AND ok = 1 "
                               "ORDER BY id DESC LIMIT 1", (api_path,)).fetchone()
            full = con.execute("SELECT retrieved_at, n_events, body_sha256 FROM raw_snapshots WHERE api_path = ? AND ok = 1 "
                               "AND n_events > 0 AND body_sha256 IS NOT NULL ORDER BY id DESC LIMIT 1", (api_path,)).fetchone()
        finally:
            con.close()
    return (tuple(last) if last else None), (tuple(full) if full else None)


def _seg_content(sha, retrieved_at, f):
    """The answer with this sha256 in one history file: its gzip body, else its normalized event rows - or None."""
    with _DB_LOCK:
        con = _connect(f)
        try:
            b = con.execute("SELECT body_gz FROM raw_snapshots WHERE body_sha256 = ? AND body_gz IS NOT NULL "
                            "ORDER BY id DESC LIMIT 1", (sha,)).fetchone()
            rows = None
            if not b:
                sid = con.execute("SELECT MAX(s.id) FROM raw_snapshots s WHERE s.body_sha256 = ? AND EXISTS "
                                  "(SELECT 1 FROM events x WHERE x.snapshot_id = s.id)", (sha,)).fetchone()[0]
                if sid is not None:
                    rows = con.execute("SELECT source_event_id, team1, team2, main_market, team1_odds, team2_odds, "
                                       "draw_odds, start_raw, competition, url, problem, tournament, game, fixture_id, "
                                       "url_id, draw_present FROM events WHERE snapshot_id = ? ORDER BY idx",
                                       (sid,)).fetchall()
        finally:
            con.close()
    if b:
        return {"body": gzip.decompress(b[0]), "events": None}
    if rows:
        return {"body": None, "events": _rebuild(rows, retrieved_at)}
    return None


def last_full_snapshot(api_path, path=None):
    """The latest successful NON-EMPTY answer of one endpoint in the history (all segments, newest first), for a restart:
    {"retrieved_at", "sha", "body" (bytes, found by hash in ANY segment) | None, "events" (rebuilt from its normalized
    rows when no body was stored) | None, "later_empty_at" (a LATER successful empty answer of this path, or None),
    "latest_sha"}. retrieved_at is that answer's own retrieval time (an unchanged body seen again counts as seen again -
    the time it was really retrieved). None when absent or when neither its body nor its rows were stored anywhere."""
    srcs = _history_sources(path)
    last = full = None
    for f in srcs:
        l_, f_ = _seg_latest(api_path, f)
        last = last or l_
        full = full or f_
        if last and full:
            break
    if not full:
        return None
    content = None
    for f in srcs:                                       # the body / rows of that answer, wherever they were stored
        content = _seg_content(full[2], full[0], f)
        if content:
            break
    if not content:
        return None
    later_empty = None
    if last and not (last[1] or 0):
        lt, ft = _ts(last[0]), _ts(full[0])
        if lt is not None and ft is not None and lt > ft:
            later_empty = last[0]
    return {"retrieved_at": full[0], "sha": full[2], "body": content["body"],
            "events": content["events"],
            "later_empty_at": later_empty, "latest_sha": last[2] if last else full[2]}


def first_seen_history(sids, path=None):
    """{namespaced source_event_id: earliest retrieval time in the events history} (legacy ids included)."""
    want = {}
    for s in sids:
        if isinstance(s, str) and s:
            want[s] = s
            want[_legacy(s)] = s
    if not want:
        return {}
    out = {}
    keys = list(want)
    for f in _history_sources(path):
        with _DB_LOCK:
            con = _connect(f)
            try:
                for i in range(0, len(keys), 400):
                    part = keys[i:i + 400]
                    for sid, first in con.execute(f"SELECT source_event_id, MIN(retrieved_at) FROM events WHERE "
                                                  f"source_event_id IN ({','.join('?' * len(part))}) GROUP BY source_event_id",
                                                  part):
                        k = want[sid]
                        t = _ts(first)
                        if t is not None and (k not in out or t < out[k]):
                            out[k] = t
            finally:
                con.close()
    return out


def _store_mb(path=None):
    """Size of the polling history: an explicit file, else all segments (thunderpick.db - gateway state and the
    pre-segment history - is reported separately and never pruned)."""
    if path is not None:
        return _file_mb(path)
    return sum(_file_mb(s) for s in _segment_files())


def store_info(path=None):
    mb = _store_mb(path)
    share = mb / SNAPSHOT_MAX_MB if SNAPSHOT_MAX_MB else 1.0
    segs = _segment_files() if path is None else []
    return {"path": str(Path(path) if path is not None else history_dir()), "mb": round(mb, 2), "max_mb": SNAPSHOT_MAX_MB,
            "segments": len(segs), "segment_max_mb": SEGMENT_MAX_MB, "used_share": round(share, 3),
            "oldest_segment": segs[0].name if segs else None,
            "gateway_file_mb": round(_file_mb(db_path()), 2) if path is None and db_path().exists() else None,
            "warning": (f"polling history at {share:.0%} of {SNAPSHOT_MAX_MB:.0f} MB - the oldest segments are being pruned "
                        "(forecasts keep their own quotes)") if share > STORE_WARN_SHARE else None,
            "policy": f"a status row per attempt; event rows for every changed answer; the gzip body of a changed answer at "
                      f"most every {BODY_EVERY_S}s per path (and the first after start); no per-market rows; segments of "
                      f"{SEGMENT_MAX_MB:.0f} MB, oldest pruned past {SNAPSHOT_MAX_MB:.0f} MB in total"}


def record(ok, retrieved_at, status=None, error_kind=None, body=None, normalized=None, last_sha=None, last_body_at=0.0,
           now_ts=None, path=None, api_path=PATH):
    """Append one attempt to the current history segment (or `path`): a status row always; for a CHANGED body its
    normalized event rows and - at most once per BODY_EVERY_S per path, and the first after start (last_body_at 0) - the
    gzip body. If pruning cannot keep the history under SNAPSHOT_MAX_MB, only the status row. Returns (sha, body_stored)."""
    now_ts = time.time() if now_ts is None else now_ts
    target = Path(path) if path is not None else _current_segment(now_ts)
    sha = hashlib.sha256(body).hexdigest() if body is not None else None
    changed = sha is not None and sha != last_sha
    room = changed and _store_mb(path) < SNAPSHOT_MAX_MB
    keep = room and (not last_body_at or now_ts - last_body_at >= BODY_EVERY_S)
    gz = gzip.compress(body) if keep else None           # compressed before taking the process-wide lock
    with _DB_LOCK:
        con = _connect(target)
        try:
            cur = con.execute("INSERT INTO raw_snapshots (retrieved_at, ok, http_status, error_kind, body_sha256, body_gz, "
                              "n_events, changed, api_path) VALUES (?,?,?,?,?,?,?,?,?)",
                              (retrieved_at, int(ok), status, error_kind, sha, gz,
                               len(normalized) if normalized is not None else None, int(changed), api_path))
            sid = cur.lastrowid
            if room and normalized:
                con.executemany(
                    "INSERT INTO events (snapshot_id, retrieved_at, idx, source_event_id, team1, team2, main_market, team1_odds, "
                    "team2_odds, draw_odds, start_raw, competition, url, problem, tournament, game, fixture_id, url_id, "
                    "draw_present, api_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(sid, retrieved_at, e["index"], e.get("source_event_id"), e.get("team1"), e.get("team2"),
                      e.get("main_market"), e.get("team1_odds"), e.get("team2_odds"), e.get("draw_odds"), e.get("start_raw"),
                      None if e.get("competition") is None else str(e.get("competition")), e.get("url"), e.get("problem"),
                      None if e.get("tournament") is None else str(e.get("tournament")),
                      None if e.get("game") is None else str(e.get("game")), e.get("fixture_id"), e.get("url_id"),
                      int(bool(e.get("draw_present"))), api_path) for e in normalized])
            con.commit()
        finally:
            con.close()
    return sha, keep


# ---------------------------------------------------------------- the gateway (EVERY Thunderpick request goes through it)
# Owner, 2026-10-13 (429s at ~2-4 requests/min): one request budget for ALL Thunderpick traffic - poller, admin probes,
# cs2 and csgo; spacing and ONE request in flight enforced inside the same atomic reservation; a cooldown and a 429
# memory that survive restarts; an append-only audit of every attempt (started / completed / refused).
SHARED_PER_MIN = int(os.environ.get("THUNDERPICK_SHARED_PER_MIN") or 4)     # all paths together, until the limit is known
COOLDOWN_BASE_S, COOLDOWN_MAX_S = 60, 1800
FAIL_COOLDOWN_S = 30
LEASE_S = 60                    # an in-flight lease expires by itself (a killed request never blocks the gateway longer)
R429_WINDOW_S = 1800            # the 429 memory: 429s of the last 30 min (persisted; a success never clears it)


def instance_id():
    import socket
    return os.environ.get("RENDER_INSTANCE_ID") or socket.gethostname()


def _gw(path=None):
    return _connect(path)


def _state(con, k, default=None):
    r = con.execute("SELECT v FROM gw_state WHERE k = ?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


_GW_WRITES = [0]                # gw_state writes by this process (invalidates the policy() read cache)


def _set_state(con, k, v):
    con.execute("INSERT OR REPLACE INTO gw_state (k, v) VALUES (?, ?)", (k, json.dumps(v)))
    _GW_WRITES[0] += 1


def _recent_429(con, now):
    return [t for t in (_state(con, "r429", []) or []) if 0 <= now - t <= R429_WINDOW_S]


def cooldown(now=None, path=None):
    """The persisted cooldown {"until", "reason", "n429", "nfail", "recent_429"} (until = 0 when none). Read-only."""
    now = time.time() if now is None else now
    with _DB_LOCK:
        con = _gw(path)
        try:
            c = _state(con, "cooldown", {"until": 0, "reason": None, "n429": 0, "nfail": 0})
            c["recent_429"] = len(_recent_429(con, now))
        finally:
            con.close()
    c["active"] = c.get("until", 0) > now
    c["remaining_s"] = round(max(0.0, c.get("until", 0) - now), 1)
    return c


def _log_row(con, phase, sent_at, api_path, caller, status=None, outcome=None, ms=None, events=None, headers=None,
             body=None, until=None, attempt_id=None):
    con.execute("INSERT INTO request_log (sent_at, path, caller, instance, pid, git_commit, http_status, outcome, duration_ms, "
                "events, headers, body_snippet, cooldown_until, phase, attempt_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sent_at, api_path, caller, instance_id(), os.getpid(), _commit() or "", status, outcome, ms, events,
                 json.dumps(headers or {}), (body or "")[:300], _iso(until) if until else None, phase, attempt_id))


def _refusal(con, now):
    """Why a request may not be sent now (None = it may): cooldown, a request in flight, spacing, the shared budget."""
    c = _state(con, "cooldown", {"until": 0}) or {"until": 0}
    if c.get("until", 0) > now:
        return f"cooldown until {_iso(c['until'])} ({c.get('reason')})"
    ls = _state(con, "lease", None)
    if ls and ls.get("until", 0) > now:
        return f"another request in flight ({ls.get('caller')} {ls.get('path')} since {_iso(ls.get('since'))})"
    last = con.execute("SELECT MAX(ts) FROM budget_slots").fetchone()[0]
    if last is not None and now - last < MIN_GAP_S:
        return f"spacing: the last request was {max(0.0, now - last):.0f}s ago (minimum {MIN_GAP_S}s)"
    con.execute("DELETE FROM budget_slots WHERE ts < ?", (now - RATE_WINDOW_S * 10,))
    n = con.execute("SELECT COUNT(*) FROM budget_slots WHERE ts > ?", (now - RATE_WINDOW_S,)).fetchone()[0]
    if n >= SHARED_PER_MIN:
        return f"shared budget full ({n} of {SHARED_PER_MIN} in the last {RATE_WINDOW_S}s)"
    return None


def _reserve(api_path, caller, now, db=None):
    """ONE transaction (BEGIN IMMEDIATE): refuse during a cooldown, while another request is in flight, within MIN_GAP_S
    of the last request sent or when the shared budget is full - the refusal appended to request_log - else take a
    budget slot and the in-flight lease and append the 'started' row. Returns (attempt_id, None) or (None, reason)."""
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                why = _refusal(con, now)
                if why:
                    _log_row(con, "refused", _iso(now), api_path, caller, outcome=f"refused: {why}"[:200])
                    con.execute("COMMIT")
                    return None, why
                aid = uuid.uuid4().hex[:16]
                con.execute("INSERT INTO budget_slots VALUES (?,?,?,?,?)", (now, api_path, caller, instance_id(), os.getpid()))
                _set_state(con, "lease", {"attempt_id": aid, "since": now, "until": now + LEASE_S, "path": api_path,
                                          "caller": caller, "instance": instance_id(), "pid": os.getpid()})
                _log_row(con, "started", _iso(now), api_path, caller, outcome="started", attempt_id=aid)
                con.execute("COMMIT")
                return aid, None
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()


def _log_refusal(api_path, caller, now, why, db=None):
    """A refusal decided before the reservation (no key, path) - appended too; best effort, never raises."""
    try:
        with _DB_LOCK:
            con = _gw(db)
            try:
                _log_row(con, "refused", _iso(now), api_path, caller, outcome=f"refused: {why}"[:200])
                con.commit()
            finally:
                con.close()
    except Exception:          # noqa: BLE001
        pass


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


def _cooldown_update(con, kind, headers, now, rnd):
    """429 -> Retry-After, else 60 s x 2^(n-1) x jitter capped at 30 min (n = 429s of the last R429_WINDOW_S, this one
    included); 5xx / timeout / network -> 30 s x 2^(n-1) x jitter; auth / path -> 30 min; success -> no cooldown and no
    failure streak - the 429 memory is NOT cleared by a success (an empty 200 or a probe included)."""
    c = _state(con, "cooldown", {"until": 0, "reason": None, "n429": 0, "nfail": 0}) or {}
    hist = _recent_429(con, now)
    if kind is None:
        c.update(until=0, reason=None, nfail=0)
    elif kind == "rate_limited":
        hist.append(now)
        ra = _retry_after_s(headers)
        wait = ra if ra is not None else min(COOLDOWN_BASE_S * 2 ** min(len(hist) - 1, 20) * (0.8 + 0.4 * rnd()), COOLDOWN_MAX_S)
        c.update(until=now + wait, reason=f"HTTP 429 ({'Retry-After' if ra is not None else 'backoff'} {wait:.0f}s)")
    elif kind in ("auth", "path", "config"):
        c.update(until=now + COOLDOWN_MAX_S, reason=f"{kind} - configuration problem")
    else:
        c["nfail"] = c.get("nfail", 0) + 1
        wait = min(FAIL_COOLDOWN_S * 2 ** min(c["nfail"] - 1, 20) * (0.8 + 0.4 * rnd()), COOLDOWN_MAX_S)
        c.update(until=now + wait, reason=f"{kind} (backoff {wait:.0f}s)")
    c["n429"] = len(hist)
    _set_state(con, "r429", hist[-50:])
    _set_state(con, "cooldown", c)
    return c


def _complete(aid, kind, headers, done_at, sent_at, api_path, caller, status, outcome, ms, events, hdr, body, db=None,
              rnd=None):
    """ONE transaction after the answer: the cooldown / 429 memory, the lease released (only when it is still ours), the
    'completed' audit row. Returns the cooldown."""
    import random
    rnd = rnd or random.random
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.execute("BEGIN IMMEDIATE")
            try:
                c = _cooldown_update(con, kind, headers, done_at, rnd)
                ls = _state(con, "lease", None)
                if ls and ls.get("attempt_id") == aid:
                    con.execute("DELETE FROM gw_state WHERE k = 'lease'")
                _log_row(con, "completed", sent_at, api_path, caller, status, outcome, ms, events, hdr, body,
                         c.get("until") if kind else None, aid)
                con.execute("COMMIT")
                return c
            except BaseException:
                con.execute("ROLLBACK")
                raise
        finally:
            con.close()


def request(name="cs2", caller="poller", fetch_fn=None, clock=time.time, db=None, rnd=None):
    """THE way to call Thunderpick: reservation (cooldown, lease, spacing, shared budget, 'started' row) -> one request ->
    completion (cooldown / 429 memory, lease release, 'completed' row). Returns {"ok", "refused"?, "events"?, "status"?,
    "error"?, "data"?, "body"?, "sent_ts", "n429"}. Never raises, never retries. A failure to write the completion never
    turns a successful answer into an error (the lease then expires by itself after LEASE_S)."""
    try:
        api_path = PATHS.get(name)
        if api_path is None:
            _log_refusal(str(name)[:40], caller, clock(), "path not allow-listed", db)
            return {"ok": False, "refused": "path not allow-listed"}
        now = clock()
        if not enabled():
            _log_refusal(api_path, caller, now, "no key configured", db)
            return {"ok": False, "refused": "no key configured"}
        try:
            aid, why = _reserve(api_path, caller, now, db)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "refused": f"gateway unavailable ({type(e).__name__})"}
        if why:
            return {"ok": False, "refused": why}
        t0 = time.time()
        kind, status, ev, events, outcome = None, None, {}, None, "ok"
        try:
            got = (fetch_fn or (lambda: fetch(path=api_path)))()
            data, status, body = got[:3]
            hdr = (got[3] if len(got) > 3 else {}) or {}
            out = {"ok": True, "data": data, "status": status, "body": body, "events": len(data), "headers": hdr.get("headers")}
            events, ev = len(data), {"headers": hdr.get("headers"), "body": ""}
        except FetchError as e:
            kind, status, ev, outcome = e.kind, e.status, e.evidence or {}, e.kind
            out = {"ok": False, "error": str(e), "kind": e.kind, "status": e.status, "evidence": ev}
        except Exception as e:      # noqa: BLE001
            kind, outcome = "error", f"error {type(e).__name__}"
            out = {"ok": False, "error": type(e).__name__, "kind": "error"}
        out["sent_ts"] = now
        try:
            c = _complete(aid, kind, ev.get("headers"), clock(), _iso(now), api_path, caller, status, outcome,
                          int((time.time() - t0) * 1000), events, ev.get("headers"), ev.get("body"), db, rnd)
            out["n429"] = c.get("n429", 0)
            if kind:
                out["cooldown"] = c
        except Exception as e:      # noqa: BLE001 - the answer stands; the audit gap is reported
            out["audit_error"] = type(e).__name__
        return out
    except Exception as e:          # noqa: BLE001
        return {"ok": False, "refused": f"gateway error ({type(e).__name__})"}


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
# "sched:<name>"): 60 s while it returns markets; slower after repeated EMPTY answers (120 / 180 / 300 s) and while 429s
# persist (120 / 240 / 300 s by the 429s of the last 30 min). A 429, a 5xx, a timeout or a network error sets the
# gateway's persisted cooldown (all endpoints wait). Repeated authentication failures stop all requests and raise ONE
# alert until the key changes. These are conservative starting settings, not the provider's confirmed limits.
BASE_INTERVAL_S = 60
EMPTY_STEPS = ((5, 60), (10, 120), (15, 180))       # empty streak below N -> interval; beyond the last -> MAX_INTERVAL_S
RL_STEPS = (60, 120, 240, 300)
CARRY_MAX_INTERVAL_S = 150                           # an endpoint returning events: never slower (quotes live 180 s)                      # 429s in the last R429_WINDOW_S: 0, 1, 2, 3+ -> interval
MAX_INTERVAL_S = 300
MIN_GAP_S = 20
QUOTE_MAX_AGE_S = int(2.5 * BASE_INTERVAL_S + 30)   # 180 s: a quote older than this is never current
RESTORE_MAX_AGE_S = 3 * 3600                        # a restart restores the last answer for DISPLAY up to 3 h old
DROPPED_KEEP_S = 6 * 3600                           # events missing from a newer answer stay visible ("last seen")
SEEN_KEEP_S = 2 * 86400
TICK_S = 5
AUTH_BLOCK_AFTER = 3


def autopoll():
    return os.environ.get("THUNDERPICK_AUTOPOLL", "1") != "0"


def interval_for(empty_streak, n429=0):
    """The endpoint's polling interval: the slower of the empty-answer step and the 429 step."""
    e = MAX_INTERVAL_S
    for n, s in EMPTY_STEPS:
        if empty_streak < n:
            e = s
            break
    return max(e, RL_STEPS[min(max(int(n429 or 0), 0), len(RL_STEPS) - 1)])


def _key_fp():
    k = _key()
    return hashlib.sha256(k.encode()).hexdigest()[:8] if k else None


_SCHED0 = {"next_at": 0, "empty_streak": 0, "interval_s": BASE_INTERVAL_S, "requests": 0, "ok": 0, "empties": 0,
           "nonempty": 0, "last_attempt": None, "last_success": None, "last_outcome": None, "last_nonempty_at": None,
           "events_last": None}


def _sched(name, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            return dict(_SCHED0, **(_state(con, f"sched:{name}", {}) or {}))
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


def _get_many(keys, db=None):
    """Several gw_state keys in ONE connection (policy() runs on every grade)."""
    with _DB_LOCK:
        con = _gw(db)
        try:
            rows = dict(con.execute(f"SELECT k, v FROM gw_state WHERE k IN ({','.join('?' * len(keys))})", tuple(keys)).fetchall())
        finally:
            con.close()
    return {k: (json.loads(rows[k]) if k in rows else None) for k in keys}


_AUTH0 = {"blocked": False, "failures": 0, "since": None, "key_fp": None}


def auth_block(db=None):
    """The authentication stop {"blocked", "since", "failures", "key_fp"} - READ-ONLY: a stop set under another key no
    longer applies (and its failures no longer count); the next answer persists the cleared state."""
    b = _get("auth", dict(_AUTH0), db) or dict(_AUTH0)
    if b.get("key_fp") and b.get("key_fp") != _key_fp():
        return dict(_AUTH0, previous_key_stop=bool(b.get("blocked")))
    return b


def _gate_view(db=None):
    """Read-only: (cooldown until, lease until, last request time) for the scheduler."""
    with _DB_LOCK:
        con = _gw(db)
        try:
            c = _state(con, "cooldown", {"until": 0}) or {"until": 0}
            ls = _state(con, "lease", None) or {}
            last = con.execute("SELECT MAX(ts) FROM budget_slots").fetchone()[0] or 0.0
        finally:
            con.close()
    return c.get("until", 0), ls.get("until", 0), last


def _endpoint(api_path):
    return _NAME_OF.get(api_path, "cs2")


class Poller:
    def __init__(self, fetch_fns=None, clock=time.time, db=None, store=True, canonical_fn=None, board_fn=None):
        self.fetch_fns = fetch_fns or {}            # tests: {"cs2": fn, "csgo": fn}
        self.clock, self.db, self.store = clock, db, store
        self.canonical_fn, self.board_fn = canonical_fn, board_fn
        self.lock = threading.Lock()
        self.data = {}           # name -> the endpoint's latest NONEMPTY successful answer {"events", "raw", "at", "sha", "by_key"}
        self.answer = {}         # name -> the endpoint's latest SUCCESSFUL answer {"at", "empty", "sha", "ids"}
        self.seen = {}           # event key -> {"first_seen", "last_observed"} (retrieval times of answers listing it)
        self.matched = {}        # match_id -> matched record (the event as of the last linking)
        self.dropped = {}        # match_id -> record whose event is missing from a newer nonempty answer (display only)
        self.ambiguous = set()   # match ids two Thunderpick events link to
        self.last_sha, self.last_body_at = {}, {}
        self.store_error = None
        self.last_refused = None
        self.alert = None
        self._relink = False                         # restored quotes waiting for the canonical matcher
        self._labels = None                          # markets[] label registry (lazy): label -> observation
        self._sched_mem = {}                         # name -> this poller's last written schedule (hot-path reads)
        self._written = None                         # (level, label) -> {"cls", "full", "at"} last market_evidence row
        self._stop = threading.Event()
        self._thread = None

    # -- one request
    def poll_once(self, name="cs2", caller="poller"):
        if name not in PATHS:
            self.last_refused = "path not allow-listed"
            _log_refusal(str(name)[:40], caller, self.clock(), self.last_refused, self.db)
            return None
        if auth_block(self.db).get("blocked"):
            self.last_refused = "authentication blocked - check the Thunderpick key"
            _log_refusal(PATHS[name], caller, self.clock(), self.last_refused, self.db)
            return None
        r = request(name, caller, self.fetch_fns.get(name), self.clock, self.db)
        if r.get("refused"):
            self.last_refused = r["refused"]
            return None
        self.last_refused = None
        now = r.get("sent_ts", self.clock())
        sch = _sched(name, self.db)
        sch["requests"] += 1
        sch["last_attempt"] = _iso(now)
        if not r["ok"]:
            sch["last_outcome"] = r.get("kind")
            sch["interval_s"] = interval_for(sch.get("empty_streak", 0), r.get("n429", 0))
            cd = r.get("cooldown") or {}
            sch["next_at"] = max(cd.get("until", 0), now + sch["interval_s"])
            if r.get("kind") == "auth":
                a = auth_block(self.db)                 # failures under the current key only
                a.pop("previous_key_stop", None)
                a["failures"] = a.get("failures", 0) + 1
                a["key_fp"] = _key_fp()
                if a["failures"] >= AUTH_BLOCK_AFTER and not a.get("blocked"):
                    a.update(blocked=True, since=_iso(now))
                    self.alert = (f"Thunderpick authentication failed {a['failures']} times in a row (HTTP {r.get('status')}) - "
                                  "requests stopped; update the key on Render (THUNDER_PICK_API) to resume")
                    print(f"[thunderpick] ALERT: {self.alert}", flush=True)
                _put("auth", a, self.db)
            _put(f"sched:{name}", sch, self.db)
            self._sched_mem[name] = dict(sch)
            self._record(False, _iso(now), r.get("status"), r.get("kind"), api_path=PATHS[name], name=name)
            return False
        if (_get("auth", None, self.db) or _AUTH0) != _AUTH0:
            _put("auth", dict(_AUTH0), self.db)
        data = r["data"]
        norm = normalize(data, _iso(now))
        sha = hashlib.sha256(r["body"]).hexdigest() if r.get("body") is not None else None
        for e in norm:
            e["api_path"] = PATHS[name]
            e["snapshot_sha256"] = sha
        empty = len(data) == 0
        sch.update(ok=sch["ok"] + 1, last_success=_iso(now), last_outcome="empty" if empty else "events", events_last=len(data))
        if empty:
            sch["empties"] += 1
            sch["empty_streak"] = sch.get("empty_streak", 0) + 1
        else:
            sch["nonempty"] += 1
            sch["empty_streak"] = 0
            sch["last_nonempty_at"] = _iso(now)
        sch["interval_s"] = interval_for(sch["empty_streak"], r.get("n429", 0))
        if not empty:   # the endpoint carrying quotes is polled before they expire (review 2026-10-13: 240 / 300 s under
            sch["interval_s"] = min(sch["interval_s"], CARRY_MAX_INTERVAL_S)   # 429s left them stale every cycle)
        sch["next_at"] = now + sch["interval_s"]
        _put(f"sched:{name}", sch, self.db)
        self._sched_mem[name] = dict(sch)
        by_key = {_ekey(e): e for e in norm}
        with self.lock:
            prev_sha = (self.answer.get(name) or {}).get("sha")
            # the endpoint's latest successful answer - an empty one makes every earlier quote of this endpoint
            # display-only at once (owner 2026-10-13: no holding through empty answers)
            self.answer[name] = {"at": now, "empty": empty, "sha": sha, "ids": frozenset(by_key)}
            if not empty:
                self.data[name] = {"events": norm, "raw": data, "at": now, "sha": sha, "by_key": by_key}
                for k in by_key:
                    s = self.seen.get(k)
                    self.seen[k] = {"first_seen": s["first_seen"] if s else now, "last_observed": now}
                if len(self.seen) > 4000:
                    self.seen = {k: v for k, v in self.seen.items() if now - v["last_observed"] <= SEEN_KEEP_S}
        self._record(True, _iso(now), r["status"], None, r["body"], norm, api_path=PATHS[name], name=name)
        if not empty:
            if sha != prev_sha:
                try:
                    self._update_registry(norm, now)
                except Exception as e:  # noqa: BLE001 - the registry never stops the feed
                    self.store_error = f"registry: {type(e).__name__}: {str(e)[:120]}"
            try:
                self.refresh_matches(now)
            except Exception as e:      # noqa: BLE001 - matching never stops the feed
                self.store_error = f"matching: {type(e).__name__}: {str(e)[:120]}"
        return True

    def _record(self, ok, retrieved, status, kind, body=None, norm=None, api_path=PATH, name="cs2"):
        if not self.store:
            return
        try:
            sha, kept = record(ok, retrieved, status, kind, body, norm, self.last_sha.get(name), self.last_body_at.get(name, 0.0),
                               self.clock(), path=self.db, api_path=api_path)
            if sha:
                self.last_sha[name] = sha
            if kept:
                self.last_body_at[name] = self.clock()
            self.store_error = None
        except Exception as e:      # noqa: BLE001
            self.store_error = f"{type(e).__name__}: {str(e)[:120]}"

    # -- scheduler
    def due(self, now=None):
        """The endpoint to poll now (or None): due by its own schedule, outside the cooldown, no request in flight,
        spaced MIN_GAP_S (the gateway enforces the same rules - this only avoids refused attempts). Read-only."""
        now = self.clock() if now is None else now
        if not autopoll() or not enabled() or auth_block(self.db).get("blocked"):
            return None
        until, lease, last = _gate_view(self.db)
        if until > now or lease > now or now - last < MIN_GAP_S:
            return None
        cands = [(s["next_at"], n) for n in PATHS for s in [_sched(n, self.db)] if s["next_at"] <= now]
        return min(cands)[1] if cands else None

    def tick(self):
        name = self.due()
        return self.poll_once(name) if name else None

    def restore(self, now=None):
        """After a restart: each endpoint's last nonempty answer from the history with its OWN retrieval time and hash
        (the body found by hash, else rebuilt from its normalized event rows) - never renewed. A LATER successful empty
        answer of that path makes the restored quotes display-only (endpoint_empty); eligibility is the usual rule, so
        a restored answer older than QUOTE_MAX_AGE_S is display-only (stale). Up to RESTORE_MAX_AGE_S old.
        Returns the restored endpoint names."""
        now = self.clock() if now is None else now
        got = []
        try:
            _registry_main(self.db, seed=True)          # the enabling rule's history (seeded once from the events rows)
        except Exception as e:          # noqa: BLE001
            self.store_error = f"registry seed: {type(e).__name__}: {str(e)[:120]}"
        for name, api_path in PATHS.items():
            if name in self.data or name in self.answer:
                continue
            try:
                s = last_full_snapshot(api_path, self.db)
                if not s:
                    continue
                at = _ts(s["retrieved_at"])
                if at is None or now - at > RESTORE_MAX_AGE_S:
                    continue
                if s["body"] is not None:
                    raw = json.loads(s["body"])
                    norm = normalize(raw, s["retrieved_at"])
                else:
                    raw, norm = None, s["events"] or []
                if not norm:
                    continue
                for e in norm:
                    e["api_path"] = api_path
                    e["snapshot_sha256"] = s["sha"]
                by_key = {_ekey(e): e for e in norm}
                first = first_seen_history([k for k in by_key if isinstance(k, str)], self.db)
            except Exception as e:      # noqa: BLE001 - a damaged history never stops the feed
                self.store_error = f"restore: {type(e).__name__}: {str(e)[:120]}"
                continue
            later = _ts(s["later_empty_at"])
            with self.lock:
                self.data[name] = {"events": norm, "raw": raw, "at": at, "sha": s["sha"], "by_key": by_key,
                                   "restored": True, "rebuilt": raw is None}
                self.answer[name] = ({"at": later, "empty": True, "sha": s["latest_sha"], "ids": frozenset(), "restored": True}
                                     if later is not None else
                                     {"at": at, "empty": False, "sha": s["sha"], "ids": frozenset(by_key), "restored": True})
                for k in by_key:
                    self.seen[k] = {"first_seen": min(first.get(k, at), at), "last_observed": at}
            self.last_sha[name] = s["latest_sha"]
            got.append(name)
        if got:
            self._relink = True
            self._try_relink(now)
        return got

    def _try_relink(self, now=None):
        try:
            self.refresh_matches(now, restored=True)
        except Exception as e:          # noqa: BLE001
            self.store_error = f"matching: {type(e).__name__}: {str(e)[:120]}"
        _, evs, disp = self._linkable()
        if self.matched or not (evs or disp):        # linked, or nothing left to link
            self._relink = False

    def _loop(self):
        try:
            self.restore()
        except Exception as e:          # noqa: BLE001
            self.store_error = f"restore: {type(e).__name__}: {str(e)[:120]}"
        while not self._stop.is_set():
            try:
                if self._relink:
                    self._try_relink()
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

    # -- freshness / eligibility (pure reads)
    def sch(self, name):
        """The endpoint's schedule for the hot read paths (every grade, every Moneylines card): this poller's own last
        write, else the persisted one (read once). The admin status and the scheduler read the persisted state."""
        s = self._sched_mem.get(name)
        if s is None:
            s = self._sched_mem[name] = _sched(name, self.db)
        return s

    def endpoint_eligible(self, name, now=None):
        """The endpoint's latest successful answer is nonempty and <= QUOTE_MAX_AGE_S old (its quotes may be current)."""
        now = self.clock() if now is None else now
        with self.lock:
            a, d = self.answer.get(name), self.data.get(name)
        return bool(a and not a["empty"] and d and d["at"] == a["at"] and now - a["at"] <= QUOTE_MAX_AGE_S)

    def current(self, name, now=None):
        """Eligible quotes, or an empty latest answer that is still recent (its next poll not overdue)."""
        now = self.clock() if now is None else now
        if self.endpoint_eligible(name, now):
            return True
        with self.lock:
            a = self.answer.get(name)
        if not a or not a["empty"]:
            return False
        sch = self.sch(name)
        return now - a["at"] <= 2.5 * max(sch.get("interval_s", BASE_INTERVAL_S), BASE_INTERVAL_S) + 30

    def events(self, now=None):
        """Current, valid events: those of every endpoint whose latest successful answer is nonempty and fresh
        (deduplicated by the source event id; cs2 first)."""
        out, seen = [], set()
        for name in ("cs2", "csgo"):
            if not self.endpoint_eligible(name, now):
                continue
            for e in self.data[name]["events"]:
                k = _ekey(e)
                if k in seen:
                    continue
                seen.add(k)
                out.append(e)
        return out

    def _linkable(self):
        """(live endpoint names, their events, display-only events): `live` = the endpoints whose latest successful
        answer is nonempty (fresh or not - linking is display; the eligibility rule decides use), newest answer first;
        display-only = the last nonempty answer of an endpoint whose latest answer is EMPTY (e.g. restored after a
        later empty answer). Deduplicated, live first."""
        with self.lock:
            names = sorted((n for n in PATHS if self.data.get(n)), key=lambda n: -self.data[n]["at"])
            live = [n for n in names if self.answer.get(n) and not self.answer[n]["empty"]]
            evs, disp, seen = [], [], set()
            for n in live + [n for n in names if n not in live]:
                for e in self.data[n]["events"]:
                    k = _ekey(e)
                    if k not in seen:
                        seen.add(k)
                        (evs if n in live else disp).append(e)
        return live, evs, disp

    def feed_state(self, rec, now, dropped=False):
        """(availability from the feed alone, the event's latest observation in its endpoint's last nonempty answer -
        for display, None when absent -, the endpoint's latest successful answer). Pure read."""
        e0 = rec["event"]
        name = _endpoint(e0.get("api_path"))
        k = _ekey(e0)
        with self.lock:
            a, d = self.answer.get(name), self.data.get(name)
        cur = (d or {}).get("by_key", {}).get(k)
        if cur is not None and (cur.get("team1"), cur.get("team2")) != (e0.get("team1"), e0.get("team2")):
            cur = None                                  # the same id now names other teams: not this event
        if dropped:
            return "not_in_latest_answer", cur, a
        if not a:
            return "stale", cur, a
        if a["empty"]:
            return "endpoint_empty", cur, a
        if k not in a["ids"] or cur is None or d["at"] != a["at"]:
            return "not_in_latest_answer", cur, a
        if now - a["at"] > QUOTE_MAX_AGE_S:
            return "stale", cur, a
        return "current", cur, a

    def evaluate(self, rec, now, start_iso=None, bo=None, hltv_start=None, hltv_bo=None, pol=None, dropped=False):
        """THE eligibility decision for one matched record (projection_odds and board_block both use it). Pure read.
        current only when: in its endpoint's latest successful nonempty answer <= QUOTE_MAX_AGE_S old, not started
        (canonical start = earliest of HLTV / caller / converted Thunderpick start), no draw price, two valid odds,
        market type enabled, series format allowed, policy active."""
        pol = pol if pol is not None else policy(self.db)
        feed, cur, ans = self.feed_state(rec, now, dropped)
        e = cur or rec["event"]
        k = _ekey(e)
        with self.lock:
            seen = dict(self.seen.get(k) or {})
        # canonical start: the earliest known start - a later Thunderpick start never lets an in-play price through
        tz = pol.get("timezone") or {}
        tp_start = None
        naive = parse_start(e.get("start_raw"))
        if naive is not None and tz.get("resolved") and tz.get("offset_h") is not None:
            tp_start = naive.replace(tzinfo=timezone.utc).timestamp() - float(tz["offset_h"]) * 3600
        hs = _ts(hltv_start) or _ts(rec.get("hltv_start"))
        cs = _ts(start_iso)
        ms = _ts(rec.get("start_utc"))
        known = [x for x in (hs, cs, tp_start, ms) if x is not None]
        canon = min(known) if known else None
        started = canon is None or canon <= now
        # series format: HLTV's (canonical link) first, else the caller's; both known and different -> excluded
        fh = _fmt(hltv_bo if hltv_bo is not None else rec.get("hltv_bo"))
        fc = _fmt(bo)
        fmt = fh or fc
        conflict = bool(fh and fc and fh != fc)
        fmts = pol["formats"] if pol.get("formats") is not None else list(ENABLED_FORMATS)
        o = _orient(e, rec.get("flipped"))
        mkt = e.get("main_market")
        enabled_m = mkt in (pol.get("enabled_markets") or [])
        if started:
            st, why = "started", ("match started - no pre-match price" if canon is not None else
                                  "start time unknown - treated as started (no pre-match check possible)")
        elif feed != "current":
            st, why = feed, {"endpoint_empty": "Thunderpick's latest answer listed no CS2 markets - earlier quote display only",
                             "not_in_latest_answer": "event not in Thunderpick's latest answer",
                             "stale": "Thunderpick quote stale - no successful answer within "
                                      f"{QUOTE_MAX_AGE_S}s"}[feed]
        elif e.get("draw_present"):
            st, why = "draw_listed", "draw price listed - not a two-way series market"
        elif not o["two_way"]:
            st, why = "invalid_odds", "invalid odds"
        elif not enabled_m:
            st, why = "scope_unconfirmed", f"market '{mkt}' not enabled as the series winner (market_types)"
        elif conflict or fmt not in fmts:
            st, why = "format_excluded", (f"series format conflict (HLTV {fh}, caller {fc})" if conflict else
                                          f"series format {fmt or 'unknown'} excluded (enabled: {', '.join(fmts) or 'none'})")
        elif mkt not in (pol.get("series_markets") or []) or not pol.get("active"):
            st, why = "policy_inactive", "Thunderpick policy inactive: " + ("; ".join(pol.get("reasons") or []) or "sanity gate")
        else:
            st, why = "current", None
        return {"availability": st, "reason": why, "event": e, "endpoint": _endpoint(e.get("api_path")), "answer": ans,
                "seen": seen, "canonical_start": canon, "start_sources": {"hltv": hs, "caller": cs, "thunderpick_utc": tp_start,
                                                                          "matched": ms},
                "started": started, "series_format": fmt, "format_basis": "HLTV" if fh else ("caller" if fc else None),
                "oriented": o, "enabled_market": enabled_m, "tz": tz}

    # -- matching + evidence
    def refresh_matches(self, now=None, restored=False):
        """Link the events of the endpoints whose latest answer is nonempty to canonical matches. Records of an
        endpoint whose latest answer is EMPTY stay (display only: endpoint_empty); a record whose event is missing from
        its endpoint's newer nonempty answer moves to `dropped` (not_in_latest_answer, display); two events on one match
        -> ambiguous, both excluded. Timezone evidence keeps accruing after resolution. Events whose only problem is
        their odds still link (shown as invalid_odds, never priced); other problems (teams, start) never link."""
        now = self.clock() if now is None else now
        live, evs, disp = self._linkable()
        ok = lambda e: not e.get("problem") or set(e["problem"].split("; ")) <= _ODDS_PROBLEMS     # noqa: E731
        tz = tz_state(self.db)
        off = tz.get("offset_h") if tz.get("resolved") else None
        ev_off = _get("tz_evidence", {}, self.db) or {}
        before = dict(ev_off)

        def link(events, evidence):
            recs, amb = {}, set()
            for e in events:
                if not ok(e):
                    continue
                m = match_event(e, off, self.canonical_fn)
                mid = m.get("match_id")
                if not mid:
                    continue
                if evidence and m.get("offset_h") is not None:
                    ev_off.pop(mid, None)
                    ev_off[mid] = m["offset_h"]
                if mid in recs or mid in amb:          # two Thunderpick events -> one match: ambiguous, drop both
                    recs.pop(mid, None)
                    amb.add(mid)
                    continue
                recs[mid] = dict(m, event=e)
            return recs, amb
        recs, amb = link(evs, True)
        shown, _ = link(disp, False)                   # display-only events never compete with live ones
        if ev_off != before:
            _put("tz_evidence", dict(list(ev_off.items())[-300:]), self.db)
        resolve_tz(self.db)
        with self.lock:
            old, new = self.matched, dict(recs)
            for mid, r in shown.items():
                if mid not in new and mid not in amb:
                    new[mid] = r
            for mid, r in old.items():
                if mid in new or mid in amb:
                    continue
                n = _endpoint(r["event"].get("api_path"))
                a = self.answer.get(n)
                if n not in live:                       # its endpoint's latest answer is empty: keep for display
                    new[mid] = r
                elif a and _ekey(r["event"]) not in a["ids"]:
                    self.dropped[mid] = dict(r, dropped_at=now)
            for mid in list(new) + list(amb):
                self.dropped.pop(mid, None)
            self.dropped = {k: v for k, v in self.dropped.items()
                            if now - (self.seen.get(_ekey(v["event"])) or {}).get("last_observed", v["dropped_at"]) <= DROPPED_KEEP_S}
            self.matched, self.ambiguous = new, amb
        if self.board_fn and not restored:
            cur = {k: v for k, v in new.items() if self.feed_state(v, now)[0] == "current"}
            update_series_evidence(cur, self.board_fn(), self.db, now)
        policy(self.db, record_events=True)
        return self.matched

    def _update_registry(self, norm, now):
        """Observed market labels -> the market-type registry. Event-level main markets: distinct events, draws and
        outcome counts in gw_state 'market_main' (what the enabling rule reads, persisted). Every label's entry is
        appended to market_evidence (frozen JSON) when it first appears or its classification changes; the main
        market's evidence snapshot at most once per REGISTRY_EVERY_S."""
        main, sids = _registry_main(self.db, seed=True)
        changed = False
        obs, known = {}, {}
        for e in norm:
            if not isinstance(e, dict) or "main_market" not in e:
                continue
            lab, sk = e["main_market"], _skey(e)
            m = main.setdefault(lab, {"events": 0, "draw_events": 0, "outcomes": [], "first_seen": _iso(now), "last_seen": None})
            s = sids.setdefault(lab, {"sids": [], "draw_sids": []})
            if lab not in known:
                known[lab] = (set(s["sids"]), set(s["draw_sids"]))
            ks, kd = known[lab]
            if sk not in ks:
                ks.add(sk)
                s["sids"].append(sk)
                m["events"] += 1
                changed = True
            oc = 3 if e.get("draw_present") else 2
            if oc not in m["outcomes"]:
                m["outcomes"] = sorted(m["outcomes"] + [oc])
                changed = True
            if e.get("draw_present") and sk not in kd:
                kd.add(sk)
                s["draw_sids"].append(sk)
                m["draw_events"] += 1
                changed = True
            m["last_seen"] = _iso(now)
            obs[(MAIN_LEVEL, lab)] = None
            for mk in e.get("markets") or []:
                o = obs.setdefault((MARKET_LEVEL, mk["name"]), {"outcomes": set(), "draw": False})
                o["outcomes"].add(len(mk["selections"]))
                o["draw"] = o["draw"] or any(str(s).strip().lower() in ("draw", "x", "tie") for s in mk["selections"])
        if changed or now - (_ts((_get("market_main_at", None, self.db) or {}).get("at")) or 0) >= REGISTRY_EVERY_S:
            for s in sids.values():
                s["sids"], s["draw_sids"] = s["sids"][-5000:], s["draw_sids"][-5000:]
            _put("market_main", main, self.db)
            _put("market_main_sids", sids, self.db)
            _put("market_main_at", {"at": _iso(now)}, self.db)
        if self._written is None:
            self._written, self._labels = _registry_written(self.db, now)
        est = series_established(self.db)
        rows = []
        for (level, label), o in obs.items():
            if level == MAIN_LEVEL:
                entry = _main_entry(label, main[label], est)
            else:
                L = self._labels.setdefault(label, {"outcomes": set(), "draw": False, "first_seen": _iso(now)})
                L["outcomes"] |= o["outcomes"]
                L["draw"] = L["draw"] or o["draw"]
                L["last_seen"] = _iso(now)
                entry = _market_entry(label, L)
            cls, full = _entry_shas(entry)
            w = self._written.get((level, label))
            if w is None or w["cls"] != cls or (level == MAIN_LEVEL and w["full"] != full and now - w["at"] >= REGISTRY_EVERY_S):
                rows.append((_iso(now), level, label, full, json.dumps(entry, sort_keys=True, default=str)))
                self._written[(level, label)] = {"cls": cls, "full": full, "at": now}
        if rows:
            with _DB_LOCK:
                con = _gw(self.db)
                try:
                    con.executemany("INSERT INTO market_evidence (at, level, label, entry_sha, entry) VALUES (?,?,?,?,?)", rows)
                    con.commit()
                finally:
                    con.close()
        if len(self._labels) > 20000:                    # bounded memory: labels not seen for a week are forgotten
            cut = now - 7 * 86400
            self._labels = {k: v for k, v in self._labels.items() if (_ts(v.get("last_seen")) or now) >= cut}

    def status(self):
        now = self.clock()
        sch = {}
        for n in PATHS:
            s = _sched(n, self.db)
            with self.lock:
                a, d = self.answer.get(n), self.data.get(n)
            s.update(next_in_s=round(max(0.0, s["next_at"] - now), 1),
                     next_attempt=_iso(s["next_at"]) if s.get("next_at") else "now",
                     last_request_at=s.get("last_attempt"), last_request_outcome=s.get("last_outcome"),
                     last_success_at=s.get("last_success"), latest_answer_at=_iso(a["at"]) if a else None,
                     latest_answer_empty=(bool(a["empty"]) if a else None),
                     serving_snapshot_at=_iso(d["at"]) if d else None, eligible_now=self.endpoint_eligible(n, now),
                     current=self.current(n, now), restored_after_restart=bool(d and d.get("restored")),
                     rebuilt_from_event_rows=bool(d and d.get("rebuilt")))
            sch[n] = s
        out = {"source": SOURCE, "enabled": enabled(), "key": key_diagnostic(), "autopoll": autopoll(),
               "running": bool(self._thread and self._thread.is_alive()), "endpoints": sch,
               "cooldown": cooldown(now, self.db), "auth": auth_block(self.db), "alert": self.alert,
               "last_refused": self.last_refused, "store_error": self.store_error,
               "budget": f"{SHARED_PER_MIN} requests / {RATE_WINDOW_S}s for ALL Thunderpick paths and callers together; "
                         f">= {MIN_GAP_S}s apart and one in flight (enforced in the gateway); per endpoint {BASE_INTERVAL_S}s, "
                         f"slowing to {MAX_INTERVAL_S}s on empty feeds and while 429s persist",
               "quote_rule": f"current only when in the endpoint's latest successful nonempty answer <= {QUOTE_MAX_AGE_S}s old",
               "matched": len(self.matched), "dropped": len(self.dropped), "ambiguous": len(self.ambiguous),
               "instance": instance_id(), "pid": os.getpid(), "store": store_info(self.db)}
        out["state"] = self.state(now)
        out["blocker"] = blocker(self, now)
        return out

    def state(self, now=None):
        now = self.clock() if now is None else now
        if not enabled():
            return "not_configured"
        if auth_block(self.db).get("blocked"):
            return "auth_failed"
        if self.events(now):            # current quotes are served even while a cooldown runs
            return "ok"
        c = cooldown(now, self.db)
        if c["active"] and (c.get("reason") or "").startswith("HTTP 429"):
            return "rate_limited"
        if any(self.current(n, now) for n in PATHS):
            return "empty"
        if c["active"]:
            return "unavailable"
        if not autopoll():
            return "paused"
        return "unavailable" if self.answer else "starting"

    def carrying_endpoint(self):
        """The endpoint that carries the CS2 markets: the one with the most recent nonempty answer (None if none yet)."""
        best = None
        for n in PATHS:
            t = _ts(self.sch(n).get("last_nonempty_at"))
            with self.lock:
                d = self.data.get(n)
            t = max(x for x in (t, d["at"] if d else None, 0) if x is not None)
            if t and (best is None or t > best[0]):
                best = (t, n)
        return best[1] if best else None


def blocker(p, now):
    """The one current reason Thunderpick cannot feed projections yet (None when the policy is active)."""
    st = p.state(now)
    if st in ("not_configured", "auth_failed", "rate_limited", "unavailable", "paused", "starting"):
        return {"not_configured": "no key", "auth_failed": "authentication failed - update the key",
                "rate_limited": "rate-limited by the provider (cooldown running)", "unavailable": "feed not answering",
                "paused": "automatic polling off", "starting": "first requests pending"}[st]
    if st == "empty":
        return "the latest answers are an empty feed (no CS2 markets; earlier quotes display only)"
    pol = policy(p.db)
    return None if pol["active"] else "; ".join(pol["reasons"])


# ---------------------------------------------------------------- canonical matching
TZ_SCAN_H = range(-12, 15)
TZ_MIN_MATCHES, TZ_MIN_SHARE = 10, 0.9
TZ_TOLERANCE_H = 0.5        # un-resolve check: evidence within +-30 min of the resolved offset agrees (schedule drift)


def _canon(team_a, team_b, start_iso, fn=None):
    if fn is not None:
        return fn(team_a, team_b, start_iso)
    import moneyline
    return moneyline.canonical(team_a, team_b, start_iso, scope="series")


def _offset_from(naive, hs):
    if not hs:
        return None
    try:
        hdt = datetime.fromisoformat(str(hs).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None
    return round((naive - hdt).total_seconds() / 3600 * 4) / 4


def match_event(e, offset_h=None, canonical_fn=None):
    """One Thunderpick event -> the canonical match (the id Polymarket / Kalshi / props / settlement use).
    Timezone resolved: start (naive) - offset = UTC, the canonical +-3 h window; the linked HLTV start still yields
    offset evidence (the inference keeps being checked). Unresolved: the start is scanned over UTC-12..+14 and the event
    matches only when exactly ONE HLTV match of these two teams fits; its HLTV start gives the offset evidence.
    Ambiguous / unlinked -> no match. The HLTV start and series format of the link are kept (canonical start, format)."""
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
                "tz": "resolved", "start_utc": st.isoformat(), "hltv_match": c.get("hltv_match"),
                "offset_h": _offset_from(naive, c.get("hltv_start")) if c.get("source") == "hltv" else None,
                "hltv_start": c.get("hltv_start"), "hltv_bo": c.get("hltv_bo"), "link_source": c.get("source")}
    found = {}
    for h in TZ_SCAN_H:
        st = (naive - timedelta(hours=h)).replace(tzinfo=timezone.utc)
        c = _canon(t1, t2, st.strftime("%Y-%m-%dT%H:%M:%SZ"), canonical_fn)
        if c.get("status") == "linked" and c.get("source") == "hltv" and c.get("match_id"):
            found.setdefault(c["match_id"], c)
    if len(found) != 1:
        return {"reason": "no HLTV match of these teams near this start" if not found else "ambiguous: several HLTV matches"}
    mid, c = next(iter(found.items()))
    hs = c.get("hltv_start")
    return {"match_id": mid, "flipped": bool(c.get("flipped")), "method": "unique HLTV match of the two teams (timezone unconfirmed)",
            "tz": "unconfirmed", "start_utc": hs, "offset_h": _offset_from(naive, hs), "hltv_match": c.get("hltv_match"),
            "hltv_start": hs, "hltv_bo": c.get("hltv_bo"), "link_source": c.get("source")}


def tz_state(db=None):
    return _get("tz", {"resolved": False}, db) or {"resolved": False}


def _tz_summary(ev, tz):
    vals = [v for v in (ev or {}).values() if isinstance(v, (int, float))]
    if not vals:
        return {"matches": 0}
    mode, n = Counter(vals).most_common(1)[0]
    ref = tz.get("offset_h") if tz.get("resolved") and tz.get("offset_h") is not None else mode
    return {"matches": len(vals), "modal_offset_h": mode, "share_at_offset": round(sum(1 for v in vals if v == ref) / len(vals), 3),
            "share_within_tolerance": round(sum(1 for v in vals if abs(v - ref) <= TZ_TOLERANCE_H) / len(vals), 3),
            "offset_h": ref}


def tz_basis(tz, summary=None):
    """Plain words: how the startTime timezone is known. Never 'confirmed' - the provider has not stated it."""
    if not tz.get("resolved"):
        return "not established yet (no offset inferred from matched fixtures); not provider-confirmed"
    s = summary or {}
    n = s.get("matches") or tz.get("matches") or 0
    share = s.get("share_at_offset", tz.get("share") or 0)
    return f"inferred from {n} matched fixtures ({share:.0%} at UTC{float(tz.get('offset_h') or 0):+g}); not provider-confirmed"


def resolve_tz(db=None):
    """Timezone of startTime from evidence: >= TZ_MIN_MATCHES distinct matches, >= TZ_MIN_SHARE at the modal offset.
    Evidence keeps accruing after resolution; when fewer than 80% of >= 20 matches sit within TZ_TOLERANCE_H of the
    resolved offset it un-resolves (the policy then stops using Thunderpick)."""
    ev = [v for v in (_get("tz_evidence", {}, db) or {}).values() if isinstance(v, (int, float))]
    cur = tz_state(db)
    if not ev:
        return cur
    mode, n = Counter(ev).most_common(1)[0]
    share = n / len(ev)
    if not cur.get("resolved") and len(ev) >= TZ_MIN_MATCHES and share >= TZ_MIN_SHARE:
        cur = {"resolved": True, "offset_h": mode, "matches": len(ev), "share": round(share, 3), "resolved_at": _iso(time.time()),
               "meaning": f"startTime = UTC{mode:+g} (inferred)"}
        _put("tz", cur, db)
    elif cur.get("resolved") and len(ev) >= 20:
        ok = sum(1 for v in ev if abs(v - float(cur.get("offset_h") or 0)) <= TZ_TOLERANCE_H) / len(ev)
        if ok < 0.8:
            cur = {"resolved": False, "reason": f"offsets disagree ({ok:.0%} of {len(ev)} within {TZ_TOLERANCE_H:g} h of "
                                                f"UTC{float(cur.get('offset_h') or 0):+g})", "unresolved_at": _iso(time.time())}
            _put("tz", cur, db)
        else:
            cur = dict(cur, evidence_matches=len(ev), modal_offset_h=mode, modal_share=round(share, 3))
    else:
        cur = dict(cur, evidence_matches=len(ev), modal_offset_h=mode, modal_share=round(share, 3))
    return cur


# ---------------------------------------------------------------- market-type registry
# Owner, 2026-10-13: what a market MEANS comes from its label and level, recorded per observed label; price agreement with
# exchange prices is supporting evidence and a sanity gate only. Nothing here is provider-confirmed (no provider
# statement yet). Enabled in this release: ONLY the event-level "Match Winner" market as the series winner.
MAIN_LEVEL, MARKET_LEVEL = "event-level main market", "markets[] entry"
ENABLED_LABEL = "match winner"
ENABLED_FORMATS = ("bo1", "bo3", "bo5")
REGISTRY_EVERY_S = 3600
MATCH_WINNER_BASIS = ("explicit label 'Match Winner' (event-level main market) with per-map markets separately labelled "
                      "'Map N Winner' / '1X2 - Map N' in the same events; exactly two outcomes; no draw price on any "
                      "observed event; not provider-confirmed")
_MAP_N = re.compile(r"\bmap\s*(\d+)\b", re.I)


def _fmt(bo):
    m = re.search(r"\d+", str(bo if bo is not None else ""))
    return f"bo{int(m.group())}" if m and int(m.group()) in (1, 2, 3, 5, 7) else None


def _overtime(label):
    low = str(label or "").lower()
    if "incl. overtime" in low or "including overtime" in low or "incl overtime" in low:
        return "included (the label says 'Incl. Overtime')"
    if "excl. overtime" in low or "excluding overtime" in low or "excl overtime" in low:
        return "excluded (the label says 'Excl. Overtime')"
    return "n/a (the label states nothing about overtime)"


def _main_enabled(label, m):
    return (str(label or "").strip().lower() == ENABLED_LABEL and (m or {}).get("events", 0) > 0
            and not (m or {}).get("draw_events") and (m or {}).get("outcomes") == [2])


def _main_entry(label, m, est):
    on = str(label or "").strip().lower() == ENABLED_LABEL
    en = _main_enabled(label, m)
    ev = (est or {}).get(label) or {}
    return {"level": MAIN_LEVEL, "label": label,
            "scope": "series winner" if on else "unclassified (event-level label other than 'Match Winner')",
            "outcomes": list(m.get("outcomes") or []),
            "draws_possible": bool(m.get("draw_events")) or str(label or "").strip().lower().startswith("1x2"),
            "draw_events": m.get("draw_events", 0), "overtime": _overtime(label),
            "series_formats_enabled": list(ENABLED_FORMATS) if en else [],
            "series_formats_excluded": ["bo2", "unknown"] if en else [],
            "bo1_note": "BO1: the series winner IS the map-1 winner (same outcome)" if on else None,
            "enabled": en,
            "basis": (MATCH_WINNER_BASIS if en else
                      f"label 'Match Winner' but a draw price was observed on {m.get('draw_events')} events - not enabled" if on else
                      "not enabled: only the event-level 'Match Winner' label is used for projections"),
            "provider_confirmed": False, "provider_reference": None, "events_seen": m.get("events", 0),
            "first_seen": m.get("first_seen"), "last_seen": m.get("last_seen"),
            "supporting_evidence": {"role": "price agreement with exchange prices of the same matches (team A view) - "
                                            "supporting evidence and a sanity gate only",
                                    "pooled": {k: ev.get(k) for k in ("matches", "mae", "corr", "established")} if ev else None,
                                    "by_format": ev.get("by_format") if ev else None}}


def _market_entry(label, L):
    mm = _MAP_N.search(str(label or ""))
    draws = bool(L.get("draw")) or str(label or "").strip().lower().startswith("1x2")
    return {"level": MARKET_LEVEL, "label": label,
            "scope": f"map {mm.group(1)}" if mm else "match-level or unclassified (no map number in the label)",
            "outcomes": sorted(L.get("outcomes") or []), "draws_possible": draws, "overtime": _overtime(label),
            "series_formats_enabled": [], "enabled": False,
            "basis": "not used for projections in this release (per-map and other markets are never mixed in)",
            "provider_confirmed": False, "provider_reference": None,
            "first_seen": L.get("first_seen"), "last_seen": L.get("last_seen")}


_VOLATILE = ("first_seen", "last_seen", "events_seen", "draw_events", "supporting_evidence")


def _entry_shas(entry):
    cls = {k: v for k, v in entry.items() if k not in _VOLATILE}
    full = {k: v for k, v in entry.items() if k not in ("first_seen", "last_seen")}
    h = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]   # noqa: E731
    return h(cls), h(full)


def _registry_main(db=None, seed=False):
    """(gw_state 'market_main' {label: {events, draw_events, outcomes, first_seen, last_seen}} - the summary policy()
    reads -, 'market_main_sids' {label: {sids, draw_sids}} - the distinct events behind the counts). When absent and
    seed=True (the poller, never a status read): seeded ONCE from the events history - 'no draw price on any observed
    event' covers every stored observation, not only those since this process started."""
    st = _get_many(("market_main", "market_main_sids"), db)
    if st["market_main"] is not None or not seed:
        return st["market_main"] or {}, st["market_main_sids"] or {}
    m, sids = {}, {}
    with _DB_LOCK:
        con = _gw(db)
        try:
            rows = con.execute(
                "SELECT main_market, COALESCE(source_event_id, team1 || '|' || team2 || '|' || start_raw), "
                "MAX(CASE WHEN draw_odds IS NOT NULL OR draw_present = 1 OR problem LIKE '%draw odds%' THEN 1 ELSE 0 END), "
                "MIN(retrieved_at), MAX(retrieved_at) FROM events WHERE main_market IS NOT NULL "
                "GROUP BY main_market, COALESCE(source_event_id, team1 || '|' || team2 || '|' || start_raw)").fetchall()
        finally:
            con.close()
    for lab, sid, draw, first, last in rows:
        e = m.setdefault(lab, {"events": 0, "draw_events": 0, "outcomes": [], "first_seen": first, "last_seen": last,
                               "seeded_from_history": True})
        s = sids.setdefault(lab, {"sids": [], "draw_sids": []})
        sk = _namespaced(sid) or sid
        e["events"] += 1
        s["sids"].append(sk)
        oc = 3 if draw else 2
        if oc not in e["outcomes"]:
            e["outcomes"] = sorted(e["outcomes"] + [oc])
        if draw:
            e["draw_events"] += 1
            s["draw_sids"].append(sk)
        e["first_seen"] = min(e["first_seen"] or first, first)
        e["last_seen"] = max(e["last_seen"] or last, last)
    for s in sids.values():
        s["sids"], s["draw_sids"] = s["sids"][-5000:], s["draw_sids"][-5000:]
    _put("market_main", m, db)
    _put("market_main_sids", sids, db)
    return m, sids


def _registry_written(db, now):
    """The last market_evidence row of every label (its shas) + the markets[] labels seen in the last 7 days."""
    written, labels = {}, {}
    with _DB_LOCK:
        con = _gw(db)
        try:
            rows = con.execute("SELECT m.at, m.level, m.label, m.entry FROM market_evidence m JOIN "
                               "(SELECT MAX(id) id FROM market_evidence GROUP BY level, label) x ON m.id = x.id").fetchall()
        finally:
            con.close()
    for at, level, label, entry in rows:
        try:
            e = json.loads(entry)
        except ValueError:
            continue
        cls, full = _entry_shas(e)
        written[(level, label)] = {"cls": cls, "full": full, "at": _ts(at) or 0}
        if level == MARKET_LEVEL and now - (_ts(e.get("last_seen")) or _ts(at) or 0) <= 7 * 86400:
            labels[label] = {"outcomes": set(e.get("outcomes") or []), "draw": bool(e.get("draws_possible"))
                             and not str(label).lower().startswith("1x2"), "first_seen": e.get("first_seen"),
                             "last_seen": e.get("last_seen")}
    return written, labels


def market_types(db=None):
    """The market-type registry (READ-ONLY): every observed market label with its level (event-level main market vs a
    markets[] entry), scope (series / map N), outcome counts, draws possible, overtime wording, enabled series formats,
    enabled, basis, provider_confirmed (False for all - no provider statement yet) and, for the main market, the price
    agreement with exchange prices by series format with dates (supporting evidence and a sanity gate only)."""
    with _DB_LOCK:
        con = _gw(db)
        try:
            rows = con.execute("SELECT m.at, m.level, m.label, m.entry FROM market_evidence m JOIN "
                               "(SELECT MAX(id) id FROM market_evidence GROUP BY level, label) x ON m.id = x.id").fetchall()
            n_rows = con.execute("SELECT COUNT(*) FROM market_evidence").fetchone()[0]
        finally:
            con.close()
    st = _get_many(("market_main", "series_evidence"), db)
    est = _series_stats(st["series_evidence"] or {})
    entries = {}
    for at, level, label, entry in rows:
        try:
            entries[(level, label)] = dict(json.loads(entry), recorded_at=at)
        except ValueError:
            continue
    for label, m in (st["market_main"] or {}).items():         # live numbers for the main markets
        entries[(MAIN_LEVEL, label)] = dict(_main_entry(label, m, est),
                                            recorded_at=(entries.get((MAIN_LEVEL, label)) or {}).get("recorded_at"))
    lst = sorted(entries.values(), key=lambda e: (e["level"] != MAIN_LEVEL, not e.get("enabled"), str(e.get("label"))))
    return {"rule": "enabled only: event-level 'Match Winner' (series winner) - " + MATCH_WINNER_BASIS,
            "enabled": [e["label"] for e in lst if e.get("enabled")], "formats_enabled": list(ENABLED_FORMATS),
            "provider_confirmed": False, "labels": len(lst), "evidence_rows": n_rows, "entries": lst}


# ---------------------------------------------------------------- price-agreement evidence + the policy gate
POLICY_VERSION = "tp-first-2"
ON_ACTIVATE = None          # set by the server: returns the old-vs-new comparison report (market_odds.compare)
SERIES_MIN_MATCHES, SERIES_MAX_MAE, SERIES_MIN_CORR = 15, 0.06, 0.85
POLICY_RULE = ("active when: an enabled market type (event-level 'Match Winner', label rule) + the startTime timezone "
               "inferred from matched fixtures + the sanity gate (price agreement with exchange prices of the same "
               f"matches, bo2 excluded: >= {SERIES_MIN_MATCHES} matches, MAE <= {SERIES_MAX_MAE}, corr >= {SERIES_MIN_CORR}; "
               "a series format whose own evidence fails it is excluded) + THUNDERPICK_POLICY not 0")


def _orient(e, flipped):
    """Team-A (canonical) view of an event: odds, raw implied, margin-adjusted probabilities."""
    da, db_ = (e.get("team2_odds"), e.get("team1_odds")) if flipped else (e.get("team1_odds"), e.get("team2_odds"))
    return {"odds_a": da, "odds_b": db_, "two_way": None if e.get("draw_present") else two_way(da, db_)}


def oriented(rec):
    """Team-A (canonical) view of a matched event: odds, raw implied, margin-adjusted probabilities."""
    return _orient(rec["event"], rec.get("flipped"))


def update_series_evidence(matched, board, db=None, now=None):
    """Thunderpick main-market probability vs the usable exchange price of the SAME canonical match (team A view), with
    the series format (HLTV's, else the board's) and the date. Latest pair per match; the last 400 matches."""
    now = time.time() if now is None else now
    ev = _get("series_evidence", {}, db) or {}
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
        ev.pop(m["match_id"], None)
        ev[m["match_id"]] = {"market": rec["event"].get("main_market"), "tp": o["two_way"]["p_a"], "ex": ex,
                             "bo": _fmt(rec.get("hltv_bo")) or _fmt(m.get("bo")), "at": _iso(now)}
    _put("series_evidence", dict(list(ev.items())[-400:]), db)
    return ev


def _stats(pairs):
    n = len(pairs)
    if not n:
        return {"n": 0, "mae": None, "corr": None}
    mae = sum(abs(a - b) for a, b in pairs) / n
    ma, mb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    va, vb = sum((a - ma) ** 2 for a, _ in pairs), sum((b - mb) ** 2 for _, b in pairs)
    corr = sum((a - ma) * (b - mb) for a, b in pairs) / (va * vb) ** 0.5 if va > 0 and vb > 0 else 0.0
    return {"n": n, "mae": round(mae, 4), "corr": round(corr, 3)}


def _passes(s):
    return bool(s and s["n"] >= SERIES_MIN_MATCHES and s["mae"] is not None and s["mae"] <= SERIES_MAX_MAE
                and s["corr"] >= SERIES_MIN_CORR)


def _series_stats(ev):
    by = {}
    for v in (ev or {}).values():
        if isinstance(v, dict) and v.get("market") is not None:
            by.setdefault(v["market"], []).append(v)
    out = {}
    for name, vs in by.items():
        pooled = _stats([(v["tp"], v["ex"]) for v in vs if (v.get("bo") or "unknown") != "bo2"])
        fm = {}
        for f in ("bo1", "bo3", "bo5", "bo2", "unknown"):
            sel = [v for v in vs if (v.get("bo") or "unknown") == f]
            if sel:
                ats = [v["at"] for v in sel if v.get("at")]
                fm[f] = dict(_stats([(v["tp"], v["ex"]) for v in sel]), first=min(ats) if ats else None,
                             last=max(ats) if ats else None)
        out[name] = {"matches": pooled["n"], "mae": pooled["mae"], "corr": pooled["corr"], "established": _passes(pooled),
                     "by_format": fm, "meaning": _meaning_test(vs)}
    return out


MEANING_MIN_BO3, MEANING_MARGIN = 15, 0.01


def _bo3_map_chance(p):
    lo, hi = 0.0, 1.0
    for _ in range(60):
        q = (lo + hi) / 2
        lo, hi = (q, hi) if q * q * (3 - 2 * q) < p else (lo, q)
    return (lo + hi) / 2


def _meaning_test(vs):
    """What the label means, tested instead of assumed (2026-10-13 owner: a market name is not proof): on BO3 matches a
    SERIES-winner price should match the exchange series price, while a MAP-1-winner price would sit at the per-map chance
    q (p = q^2(3-2q)), i.e. nearer 50%. Mean absolute error of Thunderpick's price under each reading; 'series' is
    supported when it fits better by MEANING_MARGIN on >= MEANING_MIN_BO3 BO3 matches. BO1 cannot tell them apart (the
    series is map 1). Inferred from prices - not a provider statement."""
    bo3 = [(v["tp"], v["ex"]) for v in vs if v.get("bo") == "bo3" and v.get("tp") is not None and v.get("ex") is not None]
    n = len(bo3)
    if not n:
        return {"bo3_matches": 0, "series_mae": None, "map1_mae": None, "supported": None,
                "note": "no BO3 match priced on both - the series vs map-1 reading cannot be told apart yet"}
    s = sum(abs(a - b) for a, b in bo3) / n
    m = sum(abs(a - _bo3_map_chance(b)) for a, b in bo3) / n
    sup = "series" if n >= MEANING_MIN_BO3 and s + MEANING_MARGIN <= m else ("map 1" if n >= MEANING_MIN_BO3 and m + MEANING_MARGIN <= s else None)
    return {"bo3_matches": n, "series_mae": round(s, 4), "map1_mae": round(m, 4), "supported": sup,
            "basis": "inferred from prices on BO3 matches (series vs map-1 reading); not provider-confirmed"}


def series_established(db=None):
    """{market name: pooled price agreement (bo2 excluded) + "established" (= the sanity gate) + by_format}."""
    return _series_stats(_get("series_evidence", {}, db) or {})


_POLICY_CACHE = {}
POLICY_CACHE_S = 5


def policy(db=None, record_events=False):
    """The Thunderpick-first projection policy gate (tp-first-2). Active only when an enabled market type exists (label
    rule, market_types), the startTime timezone is inferred, the sanity gate passes and THUNDERPICK_POLICY is not "0".
    A read unless record_events: then an activation / deactivation (or a new policy version) is appended to policy_log
    with the full evidence snapshot. Runs on every grade / Moneylines card: the result is reused for POLICY_CACHE_S
    while this process wrote nothing to gw_state (the only writer of these keys)."""
    ck = (str(Path(db or db_path())), os.environ.get("THUNDERPICK_POLICY", "1"))
    w0 = _GW_WRITES[0]
    hit = _POLICY_CACHE.get(ck)
    if not record_events and hit and hit[0] == w0 and time.time() - hit[1] < POLICY_CACHE_S:
        return hit[2]
    st = _get_many(("tz", "tz_evidence", "series_evidence", "market_main"), db)
    tz = st["tz"] or {"resolved": False}
    tzs = _tz_summary(st["tz_evidence"] or {}, tz)
    main = st["market_main"] or {}
    est = _series_stats(st["series_evidence"] or {})
    enabled_m = sorted(lab for lab, m in main.items() if _main_enabled(lab, m))
    passed = [lab for lab in enabled_m if (est.get(lab) or {}).get("established")
              and ((est.get(lab) or {}).get("meaning") or {}).get("supported") == "series"]
    failed_fmt = sorted({f for lab in enabled_m for f, s in ((est.get(lab) or {}).get("by_format") or {}).items()
                         if f in ENABLED_FORMATS and s["n"] >= SERIES_MIN_MATCHES and not _passes(s)})
    formats = [f for f in ENABLED_FORMATS if f not in failed_fmt]
    reasons = []
    if os.environ.get("THUNDERPICK_POLICY", "1") == "0":
        reasons.append("policy switched off (THUNDERPICK_POLICY=0)")
    if not tz.get("resolved"):
        reasons.append("startTime timezone not yet inferred from matched fixtures")
    if not enabled_m:
        reasons.append("no enabled market type observed (event-level 'Match Winner' with two outcomes and no draw price)")
    elif not passed and any((est.get(k) or {}).get("established") for k in enabled_m):
        reasons.append("series meaning not established from prices: " + ", ".join(
            f"{k}: BO3 n {((est.get(k) or {}).get('meaning') or {}).get('bo3_matches')}, series MAE "
            f"{((est.get(k) or {}).get('meaning') or {}).get('series_mae')} vs map-1 MAE "
            f"{((est.get(k) or {}).get('meaning') or {}).get('map1_mae')}" for k in enabled_m))
    elif not passed:
        reasons.append("sanity gate not passed - price agreement with exchange prices: " +
                       ", ".join(f"{k}: {(est.get(k) or {}).get('matches', 0)} matches, MAE {(est.get(k) or {}).get('mae')}, "
                                 f"corr {(est.get(k) or {}).get('corr')}" for k in enabled_m))
    if not formats:
        reasons.append("every series format failed its own price-agreement check")
    active = not reasons
    out = {"version": POLICY_VERSION, "active": active, "series_markets": passed if active else [],
           "enabled_markets": enabled_m, "formats": formats, "formats_failed_sanity": failed_fmt,
           "timezone": dict(tz, basis=tz_basis(tz, tzs)), "timezone_basis": tz_basis(tz, tzs),
           "timezone_evidence": tzs, "timezone_provider_confirmed": False,
           "evidence": est, "reasons": reasons, "rule": POLICY_RULE}
    _POLICY_CACHE[ck] = (w0, time.time(), out)
    if record_events:
        _record_policy(out, main, est, tzs, db)
    return out


def _record_policy(out, main, est, tzs, db=None):
    prev = _get("policy_state", {"active": False}, db) or {"active": False}
    sig = [out.get("formats"), out.get("enabled_markets"), out.get("series_markets")]
    if prev.get("active") == out["active"] and prev.get("version") == POLICY_VERSION and prev.get("sig", sig) == sig:
        return
    now = time.time()
    snap = {"market_registry": {lab: _main_entry(lab, m, est) for lab, m in main.items()},
            "timezone": out["timezone"], "timezone_evidence": tzs, "sanity": est, "formats": out["formats"],
            "formats_failed_sanity": out["formats_failed_sanity"], "rule": POLICY_RULE, "code_commit": _commit(),
            "previous": prev}
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.execute("INSERT INTO policy_log (at, version, active, reasons, evidence, git_commit) VALUES (?,?,?,?,?,?)",
                        (_iso(now), POLICY_VERSION, int(out["active"]), json.dumps(out["reasons"]),
                         json.dumps(snap, sort_keys=True, default=str), _commit()))
            _set_state(con, "policy_state", {"active": out["active"], "version": POLICY_VERSION, "since": _iso(now), "sig": sig})
            con.commit()
        finally:
            con.close()
    print(f"[thunderpick] projection policy {POLICY_VERSION} {'ACTIVE' if out['active'] else 'inactive'}: "
          f"{out['reasons'] or out['series_markets']}", flush=True)
    if out["active"] and ON_ACTIVATE:
        try:                    # the old-source vs Thunderpick-source comparison, saved with the activation
            _put("compare_on_activation", {"at": _iso(now), "version": POLICY_VERSION, "report": ON_ACTIVATE()}, db)
        except Exception as e:  # noqa: BLE001
            _put("compare_on_activation", {"at": _iso(now), "error": f"{type(e).__name__}: {str(e)[:160]}"}, db)


def policy_log(limit=50, db=None):
    with _DB_LOCK:
        con = _gw(db)
        try:
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute("SELECT * FROM policy_log ORDER BY id DESC LIMIT ?", (limit,))]
        finally:
            con.close()
    for r in rows:
        for k in ("reasons", "evidence"):
            try:
                r[k] = json.loads(r[k] or "null")
            except ValueError:
                pass
    return rows


# ---------------------------------------------------------------- readers: projections, the Moneylines tab
def quote_age(e, now_ts):
    """Seconds since the snapshot this event came from was retrieved (None when unreadable)."""
    t = _ts(e.get("retrieved_at"))
    return None if t is None else now_ts - t


def _quote_current(e, now_ts):
    a = quote_age(e, now_ts)
    return a is not None and a <= QUOTE_MAX_AGE_S


def _hm(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M UTC") if ts else "unknown time"


def availability_label(v, now):
    """Plain words for one availability state (Moneylines / admin)."""
    st = v["availability"]
    last = (v.get("seen") or {}).get("last_observed") or _ts(v["event"].get("retrieved_at"))
    seen = f"Last seen {_hm(last)}"
    a = v.get("answer") or {}
    if st == "current":
        return f"Current - in Thunderpick's latest answer (retrieved {_hm(a.get('at'))})"
    if st == "endpoint_empty":
        return f"{seen} - Thunderpick's latest answer listed no CS2 markets; not confirmed now"
    if st == "not_in_latest_answer":
        return f"{seen} - not in Thunderpick's latest answer; not confirmed now"
    if st == "stale":
        mins = int((now - a["at"]) // 60) if a.get("at") else None
        return f"{seen} - no successful Thunderpick answer for {mins} min; not confirmed now" if mins is not None else \
            f"{seen} - no successful Thunderpick answer since; not confirmed now"
    if st == "started":
        return f"Match started - a pre-match price is no longer usable ({seen.lower()})"
    if st == "draw_listed":
        return "A draw price is listed - not a two-outcome series market"
    if st == "invalid_odds":
        return "Invalid odds in the feed"
    if st == "scope_unconfirmed":
        return f"Market '{v['event'].get('main_market')}' is not enabled as the series winner"
    if st == "format_excluded":
        return f"Series format {v.get('series_format') or 'unknown'} - excluded (draw possible or format unknown)"
    if st == "policy_inactive":
        return "Thunderpick policy inactive - prices shown for reference only"
    return st


def _nf(reason, availability=None):
    return {"found": False, "reason": reason, "availability": availability}


def projection_odds(team, opponent, start_iso, now=None, poller=None, canonical_fn=None, bo=None):
    """Thunderpick's series-winner probability for `team` vs `opponent` in the polymarket.match_odds shape, or
    {"found": False, "reason", "availability"}. Only when evaluate() says "current" (see the module doc). Exact
    probability (6 dp); American odds for display only. Never a 50% stand-in. A pure read: no poller state changes."""
    p = poller or POLLER
    now_ts = p.clock() if now is None else now
    pol = policy(p.db)
    if not pol["active"]:
        return _nf("Thunderpick policy inactive: " + "; ".join(pol["reasons"]), "policy_inactive")
    if not team or not opponent:
        return _nf("team or opponent unknown", "unlinked")
    c = _canon(team, opponent, start_iso, canonical_fn or p.canonical_fn)
    if c.get("status") != "linked" or not c.get("match_id"):
        return _nf(f"match not linked ({c.get('reason') or c.get('status')})",
                   "ambiguous" if c.get("status") == "ambiguous" else "unlinked")
    mid = c["match_id"]
    with p.lock:
        rec, gone, amb = p.matched.get(mid), p.dropped.get(mid), mid in p.ambiguous
    if not rec:
        if amb:
            return _nf("two Thunderpick events link to this match - ambiguous, excluded", "ambiguous")
        if gone:
            return _nf("event not in Thunderpick's latest answer", "not_in_latest_answer")
        return _nf("no Thunderpick event for this match")
    v = p.evaluate(rec, now_ts, start_iso=start_iso, bo=bo, hltv_start=c.get("hltv_start"), hltv_bo=c.get("hltv_bo"), pol=pol)
    if v["availability"] != "current":
        return _nf(v["reason"], v["availability"])
    e, o, a = v["event"], v["oriented"], v["answer"]
    team_is_a = not bool(c.get("flipped"))
    pa = o["two_way"]["p_a"]
    p_team = round(pa if team_is_a else 1 - pa, 6)
    p_opp = round(1 - p_team, 6)
    odds_t, odds_o = (o["odds_a"], o["odds_b"]) if team_is_a else (o["odds_b"], o["odds_a"])
    sch = p.sch(v["endpoint"])
    tzb = pol["timezone_basis"]
    from polymarket import prob_to_american
    prov = {"source": SOURCE, "policy": POLICY_VERSION, "market": e.get("main_market"), "market_id": MAIN_LEVEL,
            "scope": "series winner", "scope_basis": MATCH_WINNER_BASIS, "scope_provider_confirmed": False,
            "series_format": v["series_format"], "series_format_basis": v["format_basis"],
            "bo1_note": "BO1: the series winner is the map-1 winner" if v["series_format"] == "bo1" else None,
            "draws_possible": False, "match_id": mid, "source_event_id": e.get("source_event_id"),
            "fixture_id": e.get("fixture_id"), "url_id": e.get("url_id"), "tournament": e.get("tournament"),
            "competition": e.get("competition"), "api_path": e.get("api_path"),
            "source_team1": e.get("team1"), "source_team2": e.get("team2"), "team_is_a": team_is_a,
            "flipped": bool(rec.get("flipped")),            # True: Thunderpick lists the canonical team B first
            "odds_team": odds_t, "odds_opp": odds_o, "implied_team_raw": round(1 / odds_t, 6),
            "implied_opp_raw": round(1 / odds_o, 6), "margin": o["two_way"]["margin"], "p_team": p_team, "p_opp": p_opp,
            "method": "proportional margin-adjusted market probability", "retrieved_at": e.get("retrieved_at"),
            "quote_age_s": round(now_ts - a["at"], 1), "event_first_seen": _iso(v["seen"].get("first_seen")),
            "event_last_observed": _iso(v["seen"].get("last_observed")),
            "endpoint_last_success_at": sch.get("last_success"), "endpoint_last_nonempty_at": sch.get("last_nonempty_at"),
            "availability": "current", "source_updated_at": None,
            "source_updated_note": "the feed publishes no quote update time - retrieved_at is our request time",
            "snapshot_sha256": e.get("snapshot_sha256"), "start_raw": e.get("start_raw"),
            "start_utc_from_source": _iso(v["start_sources"]["thunderpick_utc"]),
            "canonical_start": _iso(v["canonical_start"]), "timezone": (pol["timezone"] or {}).get("meaning"),
            "timezone_basis": tzb, "timezone_provider_confirmed": False, "match_url": e.get("url")}
    return {"found": True, "team": team, "opponent": opponent, "start": _iso(v["canonical_start"]), "url": e.get("url"),
            "title": f"{e.get('team1')} vs {e.get('team2')}", "book": SOURCE, "team_prob": p_team,
            "opp_prob": p_opp, "team_ml": prob_to_american(p_team), "opp_ml": prob_to_american(p_opp),
            "maps": {}, "flags": ["Win chance from Thunderpick odds (proportional margin-adjusted); per-map chances derived "
                                  "from the series price - no map prices mixed in from another source"],
            "provenance": prov}


def board_block(match_id, now=None, poller=None, start_iso=None, bo=None):
    """Thunderpick's block for one Moneylines card (team A = canonical team A), or None when Thunderpick never listed
    it. Same eligibility as projection_odds (evaluate): usable / eligible / freshly_confirmed only when "current"."""
    p = poller or POLLER
    now_ts = p.clock() if now is None else now
    with p.lock:
        rec = p.matched.get(match_id)
        gone = None if rec else p.dropped.get(match_id)
    if not rec and not gone:
        return None
    pol = policy(p.db)
    v = p.evaluate(rec or gone, now_ts, start_iso=start_iso, bo=bo, pol=pol, dropped=bool(gone))
    e, o, a = v["event"], v["oriented"], v["answer"] or {}
    tw = o["two_way"]
    cur = v["availability"] == "current"
    sch = p.sch(v["endpoint"])
    blk = {"source": SOURCE, "market": e.get("main_market"), "scope_confirmed": v["enabled_market"], "scope_basis": "label rule + price tests (not provider-confirmed)",
           "scope": "series winner" if v["enabled_market"] else "main market - not enabled as the series winner",
           "scope_basis": MATCH_WINNER_BASIS if v["enabled_market"] else None, "scope_provider_confirmed": False,
           "series_format": v["series_format"],
           "odds_a": o["odds_a"], "odds_b": o["odds_b"], "draw_odds": e.get("draw_odds"),
           "implied_a_raw": round(1 / o["odds_a"], 4) if o["odds_a"] else None,
           "implied_b_raw": round(1 / o["odds_b"], 4) if o["odds_b"] else None,
           "p_a": tw["p_a"] if tw else None, "p_b": tw["p_b"] if tw else None, "margin": tw["margin"] if tw else None,
           "method": "proportional margin-adjusted market probability", "retrieved_at": e.get("retrieved_at"),
           "source_updated_at": None, "quote_time": None, "current": cur, "status": v["availability"],
           "availability": v["availability"], "availability_label": availability_label(v, now_ts), "reason": v["reason"],
           "eligible": cur, "freshly_confirmed": cur, "usable": cur, "started": v["started"],
           "canonical_start": _iso(v["canonical_start"]),
           "event_first_seen": _iso(v["seen"].get("first_seen")), "event_last_observed": _iso(v["seen"].get("last_observed")),
           "endpoint_last_success_at": sch.get("last_success"), "latest_answer_at": _iso(a.get("at")),
           "url": e.get("url"), "match_method": (rec or gone).get("method"),
           "timezone": "inferred" if (pol.get("timezone") or {}).get("resolved") else "unconfirmed",
           "timezone_basis": pol["timezone_basis"], "timezone_provider_confirmed": False,
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


def probe(name, poller=None):
    """The explicit admin action: ONE request to an allow-listed endpoint through the gateway - the same cooldown,
    lease, spacing and shared budget as the poller; its answer is that endpoint's latest answer. Status reads never
    call this."""
    p = poller or POLLER
    if name not in PATHS:
        return {"path": None, "result": "refused", "refused": "path not allow-listed"}
    r = p.poll_once(name, caller="admin_probe")
    s = _sched(name, p.db)
    return {"path": PATHS[name], "result": {True: "ok", False: "failed", None: "refused"}[r],
            "refused": p.last_refused if r is None else None, "outcome": s.get("last_outcome") if r is not None else None,
            "events": s.get("events_last") if r else None, "at": _iso(p.clock())}


PUBLIC_LABEL = {"ok": "Thunderpick odds current", "empty": "Thunderpick: no CS2 markets in the feed right now",
                "rate_limited": "Thunderpick unavailable: rate-limited by the provider",
                "unavailable": "Thunderpick unavailable: the feed is not answering",
                "auth_failed": "Thunderpick unavailable: connection problem",
                "starting": "Thunderpick: connecting", "paused": "Thunderpick: paused",
                "not_configured": "Thunderpick not connected"}


def public_state(poller=None):
    """The member-facing state. quotes_confirmed_at = the retrieval time of the answer currently serving CURRENT quotes
    (None when none); last_success_at / last_nonempty_at are those of the endpoint carrying CS2 (never a max that
    mixes in an always-empty endpoint)."""
    p = poller or POLLER
    now = p.clock()
    st = p.state(now)
    ep = p.carrying_endpoint()
    s = p.sch(ep) if ep else {}
    with p.lock:
        d = p.data.get(ep) if ep else None
    q = _iso(d["at"]) if d and p.endpoint_eligible(ep, now) else None
    return {"source": SOURCE, "state": st, "label": PUBLIC_LABEL.get(st, st), "quotes_confirmed_at": q, "retrieved_at": q,
            "last_success_at": s.get("last_success"), "last_nonempty_at": s.get("last_nonempty_at"), "endpoint": ep,
            "time_basis": "our retrieval time (when our request was sent) - the feed publishes no quote time",
            "policy_active": policy(p.db)["active"]}


def admin_view(samples=3, probe=None):
    """Admin (READ-ONLY - never sends a request; the probe is the separate explicit action probe(name)): status (per
    endpoint: last request / success / nonempty answer, latest answer empty, serving snapshot), the request audit, the
    policy gate with its evidence and log, the market-type registry, matched / dropped / ambiguous, inventories."""
    p = POLLER
    inv = {}
    for n in PATHS:
        with p.lock:
            d, a = p.data.get(n), p.answer.get(n)
        raw = (d or {}).get("raw")
        inv[n] = {"retrieved_at": _iso(d["at"]) if d else None, "latest_answer_at": _iso(a["at"]) if a else None,
                  "latest_answer_empty": bool(a["empty"]) if a else None,
                  "inventory": inventory(raw) if raw is not None else None,
                  "rebuilt_from_event_rows": bool(d and d.get("rebuilt")), "sample_events": (raw[:samples] if raw else [])}
    with p.lock:
        matched, dropped, amb = dict(p.matched), dict(p.dropped), sorted(p.ambiguous)

    def row(k, v):
        return {"match_id": k, "method": v.get("method"), "tz": v.get("tz"), "teams": [v["event"]["team1"], v["event"]["team2"]],
                "flipped": v.get("flipped"), "market": v["event"].get("main_market"),
                "source_event_id": v["event"].get("source_event_id"), "hltv_bo": v.get("hltv_bo")}
    plog = policy_log(50, p.db)
    return {"status": p.status(),
            "probe": ({"note": "status reads never send requests - the probe is the explicit admin action thunderpick.probe()"}
                      if probe else None),
            "policy": policy(p.db), "policy_log": plog, "policy_events": plog, "market_types": market_types(p.db),
            "tz_evidence_matches": len(_get("tz_evidence", {}, p.db) or {}),
            "matched": [row(k, v) for k, v in matched.items()], "dropped": [row(k, v) for k, v in dropped.items()],
            "ambiguous": amb, "endpoints": inv, "request_log": request_log(60, p.db), "public_state": public_state(p),
            "compare_on_activation": _get("compare_on_activation", None, p.db), "store": store_info(p.db)}
