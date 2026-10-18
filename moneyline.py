#!/usr/bin/env python3
"""
moneyline.py - CS2 SERIES-WINNER market baseline from Polymarket and Kalshi (+ forward snapshots).

Nothing here touches grading.  The prop grader keeps reading polymarket.match_odds(); this module
is a separate, read-only view of the two exchanges for a market baseline and its forward history.

Sources (independent - one failing never blocks the other or the prop grader)
  polymarket_ml.py  Gamma listing + CLOB books   (one shared refresh, background thread)
  kalshi.py         KXCS2GAME events + orderbooks (one shared refresh, background thread)
  Each refresher: every 60 s, 10 s request timeouts, on failure exponential backoff 120 s, 240 s,
  480 s, capped at 10 min; the last good data is kept and marked stale with its age.
  Member requests never call upstream - board() reads the in-memory result.

Implied probability for canonical team A (team order from matchid.canonical_match)
  Polymarket: midpoint of team A's outcome-token best bid / best ask.  The two outcomes are
              complementary shares of ONE market, so there is no vig to remove and none is removed.
  Kalshi:     midpoint of team A's OWN "<A> wins" YES contract.  Team B's YES midpoint is recorded
              next to it with the coherence gap |pA_yesA + pB_yesB - 1| (flagged > 0.05).  The two are
              never averaged.  (NO of A is A's complement, not B's YES.)
  A last trade is labelled "last trade (not a quote)" and is never used as a probability.

A quote is USABLE only when all hold:
  current     - fetched by the latest successful refresh (<= 180 s old) and the market is open /
                accepting orders
  two-sided   - a best bid and a best ask
  spread      - ask - bid <= 0.10
  depth       - >= 5 shares/contracts at the best bid AND best ask, and >= 100 within 5c of each
                (best levels only, when the full book is unavailable: >= 100 at each best level;
                no sizes at all -> never usable)
  not started - decided once per MATCH, not per listing: no listing of the match (either platform) is marked
                live / ended and the earliest start any of them (or HLTV's schedule) states is in the future.
                Kalshi states the ORIGINALLY scheduled start and has no live flag, so a match that starts early is
                caught by Polymarket's live flag for Kalshi's block too (never an in-play Kalshi baseline).
  win price   - not a best-of-2 (a BO2 can end in a draw; Polymarket resolves a tie 50-50, so its midpoint is
                P(A) + P(draw)/2, not P(A wins)).  Such a match keeps its prices on display, labelled as a price,
                and never has a baseline or a shadow-log row.

FROZEN BASELINE RULE  (ml-baseline-1, returned with every match as baseline["rule"])
  1. Only a match linked to one canonical match id can have a baseline.
  2. Both platforms usable -> the one with the smaller bid/ask spread; tie -> the larger best-level
     depth (best-bid size + best-ask size); tie -> Polymarket.
  3. One platform usable -> that platform.
  4. None usable -> no baseline: {"baseline": None, "label": "Market probability unavailable"}.
     Never 50%.
  Disagreement is flagged when both are usable and |pA_poly - pA_kalshi| >= 0.05.

History (CS2_DATA_DIR/.cache/ml_snapshots.db, rows immutable)
  ml_quotes     every quote (usable AND unusable) when it CHANGES (bid / ask / sizes / depth / status / usability /
                link) and otherwise once per HEARTBEAT (10 min) - an unchanged quote is not re-written every minute;
                unique on (platform, market_id, outcome_id, received_ts) - re-inserting is a no-op.  Above
                SNAPSHOT_MAX_MB the store stops growing (snapshot_error on admin) instead of filling the data disk.
  ml_forecasts  shadow log of the baseline per linked match, model_version "ml-baseline-1", written
                when the baseline (platform / probability / availability) changes or every 30 min.
                Next to every baseline row the frozen SHADOW candidate (ml_model.py, model_version
                "ml-candidate-1", NOT validated, never a stake) is logged with the same logged_ts.
                2026-10-13: the 0U side's Thunderpick-first market input (ml_sides "ml-sides-2": Thunderpick's
                eligible price, else this baseline) is logged too, on its own change / 30-min rule; every row
                written since carries baseline_source + odds_policy so the evaluation separates the policies.

Thunderpick (primary bookmaker source, 2026-10-13) - tp_block(): its block per linked match for the tab and the side,
  read from thunderpick.POLLER memory; never part of ml-baseline-1 (which stays exactly as frozen).  None = no event;
  a reader failure is the never-eligible "read_error" block (no ml-sides-2 row is written on that pass).

Public:
  start()  idempotent - one background refresher per source
  status() per source health
  board()  list of matches (see _assemble for the shape)
  match(team_a, team_b, start_iso, bo=None, event=None)  one match oriented to the canonical team A,
           or the unavailable stub (never 50%)
"""
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import kalshi
import polymarket_ml

MODEL_VERSION = "ml-baseline-1"
INTERVAL = 60            # seconds between refreshes per source
MAX_BACKOFF = 600        # 10 min cap
STALE_AFTER = 180        # a source's data older than this is not current
MAX_SPREAD = 0.10
MIN_BEST = 5             # shares / contracts at the best bid AND best ask (Polymarket min order = 5)
MIN_DEPTH_5C = 100       # shares / contracts within 5c of the best bid AND of the best ask
DISAGREE = 0.05
COHERENCE = 0.05
FORECAST_EVERY = 1800    # shadow log: also re-log an unchanged baseline every 30 min
HEARTBEAT = 600          # ml_quotes: an unchanged quote is re-written at most every 10 min
SNAPSHOT_MAX_MB = float(os.environ.get("ML_SNAPSHOT_MAX_MB") or 500)   # size cap of ml_snapshots.db (+ WAL)
GROUP_TTL = 3600         # another platform's started / best-of-2 signal is trusted for 1 h after its refresh
CANON_TTL = 600          # re-ask matchid at most every 10 min per listing
BOARD_TTL = 5            # board() recomputed at most every 5 s (single flight)
DROP_STARTED_AFTER = 12 * 3600   # started more than 12 h ago -> off the board
PLATFORMS = ("polymarket", "kalshi")
UNAVAILABLE = "Market probability unavailable"
TIE_REASON = "best-of-2: a draw is possible, so the price is not a series-win probability"
OTHER_LIVE = "match started (another platform marks it live)"

BASELINE_RULE = (
    "ml-baseline-1: linked matches only. Both platforms usable -> smaller bid/ask spread; tie -> larger "
    "best-level depth (best-bid size + best-ask size); tie -> Polymarket. One usable -> it. None usable -> "
    "no baseline ('Market probability unavailable'), never 50%. Usable = current (latest refresh, <= 180 s, "
    "market open) AND two-sided AND spread <= 0.10 AND depth (>= 5 at each best level and >= 100 within 5c of "
    "each side) AND not started (one decision per match: any platform live, or the earliest stated start passed) AND "
    "not a best-of-2 (a draw is possible - the price is not a win probability). "
    "Polymarket = midpoint of team A's outcome token (complements of one market, no de-vig); Kalshi = "
    "midpoint of team A's own YES contract. Disagreement flagged at |pA_poly - pA_kalshi| >= 0.05.")


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------- canonical match ids

_FB_DROP = {"team", "esports", "esport", "gaming", "club", "the"}   # NOT academy / junior / fe / nxt


def _fallback_key(name):
    words = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).split()
    core = [w for w in words if w not in _FB_DROP]
    return " ".join(core or words)


def _fallback_canonical(team_a, team_b, start_iso, bo=None, event=None, scope="series"):
    """Used only when matchid.py is not present: exact names after case / punctuation and
    'Team' / 'Esports' / 'Gaming' are dropped.  Never fuzzy - 'Vitality Academy' != 'Vitality'."""
    ka, kb = _fallback_key(team_a), _fallback_key(team_b)
    if not ka or not kb or ka == kb:
        return {"match_id": None, "team_keys": sorted([ka, kb]), "flipped": False, "status": "unlinked",
                "reason": "team names missing or identical"}
    keys = sorted([ka, kb])
    st = _parse(start_iso)
    if st is None:
        return {"match_id": None, "team_keys": keys, "flipped": keys[0] != ka, "status": "unlinked",
                "reason": "no start time"}
    return {"match_id": f"fb:{keys[0]}~{keys[1]}@{st.strftime('%Y-%m-%dT%H')}", "team_keys": keys,
            "flipped": keys[0] != ka, "status": "fallback", "reason": "matchid.py not available - name key"}


def canonical(team_a, team_b, start_iso, bo=None, event=None, scope="series", mint=True):
    """mint=False: lookup only - never issues a hashed match id (matchid.canonical_match)."""
    try:
        import matchid
    except ImportError:
        return _fallback_canonical(team_a, team_b, start_iso, bo, event, scope)
    try:
        r = (matchid.canonical_match(team_a, team_b, start_iso, bo=bo, event=event, scope=scope) if mint else
             matchid.canonical_match(team_a, team_b, start_iso, bo=bo, event=event, scope=scope, mint=False))
        return dict(r) if isinstance(r, dict) else {"match_id": None, "status": "error", "reason": "bad reply"}
    except Exception as e:          # noqa: BLE001 - linking failing never blocks the board
        return {"match_id": None, "team_keys": None, "flipped": False, "status": "error",
                "reason": f"canonical_match failed: {type(e).__name__}"}


_BAD_STATUS = {"ambiguous", "unlinked", "unknown", "error", "none", "conflict", "not_found", "notfound",
               "unmatched", "no_match"}


def _linked(c):
    return bool(c.get("match_id")) and str(c.get("status") or "").lower() not in _BAD_STATUS


def _record_links(listings):
    """Tell matchid which platform market belongs to which match (its idempotent link table).
    Never raises - linking is bookkeeping, the quotes do not depend on it."""
    try:
        import matchid
        link = matchid.link
    except (ImportError, AttributeError):
        return
    for L in listings:
        c = L.get("canon") or {}
        if not L.get("linked") or not c.get("team_keys") or L.get("idx_a") is None:
            continue
        ka, kb = c["team_keys"]
        own = [ka, kb] if L["idx_a"] == 0 else [kb, ka]          # team key of teams[0], teams[1]
        try:
            if L["platform"] == "polymarket":
                q0 = L["quotes"][0]
                link("polymarket", L.get("event_id"), q0["market_id"], c["match_id"], {"0": own[0], "1": own[1]})
            else:
                for i, q in enumerate(L.get("quotes") or []):
                    link("kalshi", L.get("event_id"), q["market_id"], c["match_id"], {"yes": own[i]})
        except Exception:       # noqa: BLE001
            continue


# ---------------------------------------------------------------- quote evaluation

def evaluate(q, now, source_current, live=False, source_note=None, started=None, started_note=None, blocker=None):
    """(current, usable, reason, prob, spread) for one normalized quote at time `now`.
    started: the match-level decision (see _match_state) - None = this quote's own start / live flag only.
    blocker: a reason that makes the price unusable whatever the book looks like (best-of-2)."""
    bid, ask = q.get("bid"), q.get("ask")
    two_sided = bid is not None and ask is not None and ask >= bid
    spread = round(ask - bid, 4) if two_sided else None
    prob = round((bid + ask) / 2, 4) if two_sided else None
    current = bool(source_current and q.get("market_open"))
    if started is None:
        st = _parse(q.get("start"))
        started = bool(live) or (st is not None and st <= now)
    reasons = []
    if not source_current:
        reasons.append(source_note or "not from the latest refresh")
    elif not q.get("market_open"):
        reasons.append(f"market not open ({q.get('status')})")
    if started:
        reasons.append(started_note or "match started - in-play price, not pre-match")
    if blocker:
        reasons.append(blocker)
    if not two_sided:
        reasons.append("one-sided or empty book (no bid/ask pair)")
    elif spread > MAX_SPREAD + 1e-9:
        reasons.append(f"spread {spread:.2f} > {MAX_SPREAD:.2f}")
    if two_sided:
        why = depth_problem(q)
        if why:
            reasons.append(why)
    usable = current and not reasons
    return current, usable, ("; ".join(reasons) or None), prob, spread


_BO2_TEXT = re.compile(r"\(\s*BO\s*2\s*\)|\bBO2\b|\bbest[- ]of[- ](?:2|two)\b", re.I)
_DRAW_TEXT = re.compile(r"\b(?:tie|tied|ties|draw|drawn|draws)\b", re.I)


def tie_possible(L):
    """True when the listing's series can end in a draw: best-of-2 by the listing's BO, by HLTV's format for the linked
    match, or by its text.  Polymarket's rules ALWAYS say a canceled match or a tie resolves 50-50 (boilerplate on every
    BO1 / BO3 too), so for Polymarket only an explicit BO2 counts; Kalshi's series rules never mention a draw for a
    match that can't have one, so a tie / draw outcome in Kalshi's text counts."""
    if L.get("bo") == 2 or (L.get("canon") or {}).get("hltv_bo") == 2:
        return True
    text = " ".join(str(x or "") for x in (L.get("rules"), L.get("event_title"), L.get("title")))
    if _BO2_TEXT.search(text):
        return True
    return L.get("platform") == "kalshi" and bool(_DRAW_TEXT.search(str(L.get("rules") or "")))


def _match_state(listings, now):
    """One 'started' / 'draw possible' decision for ALL listings of one match (both platforms, duplicates too):
    started = any listing marked live / ended, or the earliest start any of them - or HLTV's schedule for the linked
    match - states is at or before `now`.  Returns (started, started_note, tie, earliest_start_iso)."""
    live = [L for L in listings if L.get("live")]
    starts = []
    for L in listings:
        for s in (L.get("start"), (L.get("canon") or {}).get("hltv_start") if L.get("linked") else None):
            d = _parse(s)
            if d is not None:
                starts.append(d)
    first = min(starts) if starts else None
    started = bool(live) or (first is not None and first <= now)
    note = "match started - in-play price, not pre-match" if started else None
    return started, note, any(tie_possible(L) for L in listings), (_iso(first) if first else None)


def _started_note(L, listings, now):
    """Why THIS listing's price counts as in-play when the match-level decision says started."""
    own = _parse(L.get("start"))
    if L.get("live") or (own is not None and own <= now):
        return "match started - in-play price, not pre-match"
    if any(x.get("live") for x in listings if x is not L):
        return OTHER_LIVE + " - in-play price, not pre-match"
    return "match started (the earliest start a platform or HLTV states has passed) - in-play price, not pre-match"


# Cross-platform signal for the snapshot log: each source's refresh records, per LINKED match id, whether its own listing
# is live / its start / best-of-2.  _snapshot_rows (which runs inside ONE source's refresh) reads the other platform's
# record, so a Kalshi row of a match Polymarket already marks live is stored as unusable with no prob_a.  A source that
# fails simply stops refreshing its record (it expires after GROUP_TTL) - it never blocks the other source.
_GROUP = {}                 # match_id -> {platform: {"at", "live", "start", "tie"}}
_GROUP_LOCK = threading.Lock()


def _note_group(platform, listings, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    with _GROUP_LOCK:
        for mid in [m for m, v in _GROUP.items() if all(now_ts - x["at"] > GROUP_TTL for x in v.values())]:
            del _GROUP[mid]
        for L in listings:
            mid = (L.get("canon") or {}).get("match_id") if L.get("linked") else None
            if not mid:
                continue
            c = L.get("canon") or {}
            prev = _GROUP.setdefault(mid, {}).get(platform)
            rec = {"at": now_ts, "live": bool(L.get("live")), "start": L.get("start"), "tie": tie_possible(L),
                   "hltv_start": c.get("hltv_start")}
            if prev and prev["at"] == now_ts:          # two listings of one platform on one match: either one counts
                rec = {"at": now_ts, "live": prev["live"] or rec["live"], "tie": prev["tie"] or rec["tie"],
                       "start": min([s for s in (prev["start"], rec["start"]) if s] or [None]),
                       "hltv_start": rec["hltv_start"] or prev.get("hltv_start")}
            _GROUP[mid][platform] = rec


def _other_platforms(mid, platform, now_ts=None):
    """The fresh records of the OTHER platforms for one linked match id (list of dicts)."""
    now_ts = time.time() if now_ts is None else now_ts
    with _GROUP_LOCK:
        return [dict(v) for p, v in (_GROUP.get(mid) or {}).items() if p != platform and now_ts - v["at"] <= GROUP_TTL]


def depth_problem(q):
    """None when the book is deep enough: >= MIN_BEST at the best bid and best ask AND >= MIN_DEPTH_5C
    within 5c of each best level.  Without a full book (best levels only) each best level must hold
    MIN_DEPTH_5C by itself; with no sizes at all the quote is never usable."""
    bs, as_ = q.get("bid_size"), q.get("ask_size")
    if bs is None or as_ is None:
        return "no depth data (order book sizes unavailable)"
    if min(bs, as_) < MIN_BEST:
        return f"thin book ({min(bs, as_):g} < {MIN_BEST} at best level)"
    d = q.get("depth") or {}
    b5, a5 = d.get("bid_5c"), d.get("ask_5c")
    if b5 is None or a5 is None:
        b5, a5 = bs, as_
        where = "at best level (full book unavailable)"
    else:
        where = "within 5c"
    if min(b5, a5) < MIN_DEPTH_5C:
        return f"thin book ({min(b5, a5):g} < {MIN_DEPTH_5C} {where})"
    return None


def best_depth(q):
    return (q.get("bid_size") or 0) + (q.get("ask_size") or 0)


# ---------------------------------------------------------------- snapshot store

def db_path():
    base = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent)
    return base / ".cache" / "ml_snapshots.db"


_DB_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ml_quotes (
  platform TEXT NOT NULL, market_id TEXT NOT NULL, outcome_id TEXT NOT NULL, received_ts TEXT NOT NULL,
  match_id TEXT, link_status TEXT, event_id TEXT, team_name TEXT, team_side TEXT,
  bid REAL, ask REAL, bid_size REAL, ask_size REAL, depth TEXT, prob REAL, last_trade REAL,
  source_ts TEXT, status TEXT, current INTEGER, usable INTEGER, unusable_reason TEXT,
  start TEXT, bo INTEGER, event_title TEXT,
  source TEXT, scope TEXT, team_a TEXT, team_b TEXT, prob_a REAL, captured_at TEXT,
  PRIMARY KEY (platform, market_id, outcome_id, received_ts));
CREATE INDEX IF NOT EXISTS ml_quotes_match ON ml_quotes(match_id, received_ts);
CREATE INDEX IF NOT EXISTS ml_quotes_received ON ml_quotes(received_ts);
CREATE TRIGGER IF NOT EXISTS ml_quotes_no_update BEFORE UPDATE ON ml_quotes
  BEGIN SELECT RAISE(ABORT, 'ml_quotes rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ml_quotes_no_delete BEFORE DELETE ON ml_quotes
  BEGIN SELECT RAISE(ABORT, 'ml_quotes rows are immutable'); END;
CREATE TABLE IF NOT EXISTS ml_forecasts (
  match_id TEXT NOT NULL, model_version TEXT NOT NULL, logged_ts TEXT NOT NULL,
  platform TEXT, prob_a REAL, team_a TEXT, team_b TEXT, start TEXT, available INTEGER,
  disagreement INTEGER, reason TEXT, rule TEXT,
  PRIMARY KEY (match_id, model_version, logged_ts));
CREATE TRIGGER IF NOT EXISTS ml_forecasts_no_update BEFORE UPDATE ON ml_forecasts
  BEGIN SELECT RAISE(ABORT, 'ml_forecasts rows are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ml_forecasts_no_delete BEFORE DELETE ON ml_forecasts
  BEGIN SELECT RAISE(ABORT, 'ml_forecasts rows are immutable'); END;
"""
# 2026-10-13: which market input a forecast row used and under which odds policy, so the evaluation separates the
# exchange-only baseline (ml-baseline-1) from the Thunderpick-first side input (ml_sides "ml-sides-2"). Added columns
# only (rows stay immutable; older rows read NULL).
_FC_EXTRA_COLS = (("baseline_source", "TEXT"), ("odds_policy", "TEXT"))


def _connect():
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except sqlite3.DatabaseError:
        pass
    con.executescript(_SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(ml_forecasts)")}
    for col, typ in _FC_EXTRA_COLS:
        if col not in have:
            try:
                con.execute(f"ALTER TABLE ml_forecasts ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:          # another connection added it a moment ago
                pass
    return con


def write_snapshots(rows, force=False):
    """Append quote rows that CHANGED since the last stored row of the same quote (platform, market, outcome) - or whose
    last stored row is HEARTBEAT seconds older (by received_ts).  A row already stored (same platform / market / outcome
    / received_ts) is ignored.  force=True writes every row (still idempotent).  Returns the number of new rows.
    Raises SnapshotCapReached when the store is over SNAPSHOT_MAX_MB (the caller reports it; nothing is written)."""
    if not rows:
        return 0
    cols = ["platform", "market_id", "outcome_id", "received_ts", "match_id", "link_status", "event_id",
            "team_name", "team_side", "bid", "ask", "bid_size", "ask_size", "depth", "prob", "last_trade",
            "source_ts", "status", "current", "usable", "unusable_reason", "start", "bo", "event_title",
            "source", "scope", "team_a", "team_b", "prob_a", "captured_at"]
    with _DB_LOCK:
        mb = _store_mb()
        if mb > SNAPSHOT_MAX_MB:
            raise SnapshotCapReached(f"ml_snapshots.db is {mb:.0f} MB (cap {SNAPSHOT_MAX_MB:.0f} MB) - "
                                     "quote snapshots paused; archive or raise ML_SNAPSHOT_MAX_MB")
        con = _connect()
        try:
            last = _last_written(con)
            keep = []
            for r in rows:
                k = (r.get("platform"), str(r.get("market_id")), str(r.get("outcome_id")))
                sig = tuple(r.get(c) for c in _SIG_COLS)
                at = _parse(r.get("received_ts"))
                prev = last.get(k)
                if (force or prev is None or prev[0] != sig or at is None or prev[1] is None
                        or (at - prev[1]).total_seconds() >= HEARTBEAT):
                    keep.append((k, sig, at, r))
            if not keep:
                return 0
            before = con.total_changes
            con.executemany(f"INSERT OR IGNORE INTO ml_quotes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [tuple(r.get(c) for c in cols) for _, _, _, r in keep])
            con.commit()
            for k, sig, at, _ in keep:
                prev = last.get(k)
                if prev is None or prev[1] is None or (at is not None and at >= prev[1]):
                    last[k] = (sig, at)
            return con.total_changes - before
        finally:
            con.close()


class SnapshotCapReached(RuntimeError):
    pass


# the columns whose change makes a new ml_quotes row (everything a reader of the history uses; not received / source
# timestamps or the last trade, which change every refresh without changing the quote)
# 2026-10-12: sizes and the 5c depth ladder are stored with every row but no longer start a new row on their own - they
# changed on ~47% of quotes every minute (~90 MB/day); a quote's price, state or usability changing still writes at once, and
# HEARTBEAT re-writes an unchanged quote every 10 min, so the depth history keeps a 10-minute resolution.
_SIG_COLS = ("match_id", "link_status", "bid", "ask", "status", "current", "usable",
             "unusable_reason", "prob", "prob_a", "start", "team_side")
_LAST = {"path": None, "map": {}}          # last stored signature per quote, for the db at _LAST["path"]


def _last_written(con):
    """{(platform, market_id, outcome_id): (signature, received_at)} of the newest stored row per quote - rebuilt from
    the db once per process (and per db path), so a restart does not write a full extra copy of every quote."""
    p = str(db_path())
    if _LAST["path"] != p:
        m = {}
        sel = ", ".join(f"q.{c}" for c in _SIG_COLS)
        for row in con.execute(f"SELECT q.platform, q.market_id, q.outcome_id, q.received_ts, {sel} FROM ml_quotes q JOIN "
                               "(SELECT platform, market_id, outcome_id, MAX(received_ts) AS mx FROM ml_quotes "
                               " GROUP BY platform, market_id, outcome_id) z ON q.platform = z.platform AND "
                               "q.market_id = z.market_id AND q.outcome_id = z.outcome_id AND q.received_ts = z.mx"):
            m[(row[0], row[1], row[2])] = (tuple(row[4:]), _parse(row[3]))
        _LAST.update(path=p, map=m)
    return _LAST["map"]


def _store_mb():
    p = db_path()
    total = 0
    for f in (p, p.with_name(p.name + "-wal")):
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total / 1e6


def _snapshot_rows(listings, now, platform=None):
    """ml_quotes rows for one source's listings.  'Started' and 'best-of-2' are decided per MATCH: a linked listing also
    reads the other platform's record (_note_group), so a match another platform marks live is stored unusable with no
    prob_a ("match started (another platform marks it live)"), and a best-of-2 never carries prob_a."""
    rows = []
    now_ts = time.time()
    for L in listings:
        c = L.get("canon") or {}
        ia = L.get("idx_a")
        t = L.get("teams") or []
        names = (t[ia], t[1 - ia]) if ia is not None and len(t) == 2 else (None, None)
        started, note, tie, _ = _match_state([L], now)
        if L.get("linked") and c.get("match_id"):
            for o in _other_platforms(c["match_id"], platform or L.get("platform"), now_ts):
                st = _parse(o.get("start"))
                if not started and (o.get("live") or (st is not None and st <= now)):
                    started, note = True, OTHER_LIVE if o.get("live") else "match started - in-play price, not pre-match"
                tie = tie or bool(o.get("tie"))
        for i, q in enumerate(L.get("quotes") or []):
            cur, use, why, prob, _ = evaluate(q, now, True, L.get("live"), started=started, started_note=note,
                                              blocker=TIE_REASON if tie else None)
            lt = q.get("last_trade") or {}
            rows.append({
                "platform": q["platform"], "market_id": str(q.get("market_id")), "outcome_id": str(q.get("outcome_id")),
                "received_ts": q.get("received_ts"), "match_id": c.get("match_id") if L.get("linked") else None,
                "link_status": c.get("status"), "event_id": q.get("event_id"), "team_name": q.get("team_name"),
                "team_side": (None if ia is None else ("a" if i == ia else "b")),
                "bid": q.get("bid"), "ask": q.get("ask"), "bid_size": q.get("bid_size"), "ask_size": q.get("ask_size"),
                "depth": json.dumps(q.get("depth"), sort_keys=True), "prob": prob, "last_trade": lt.get("price"),
                "source_ts": q.get("source_ts"), "status": q.get("status"), "current": int(cur), "usable": int(use),
                "unusable_reason": why, "start": q.get("start"), "bo": 2 if tie and q.get("bo") is None else q.get("bo"),
                "event_title": q.get("event_title"),
                # ml_eval.py forward-cohort columns: team A's own row carries prob_a (Kalshi team B's YES row
                # does not - it is B's contract, not A's complement); a started match or a best-of-2 never does
                "source": q["platform"], "scope": "series", "team_a": names[0], "team_b": names[1],
                "prob_a": prob if (ia is not None and i == ia and L.get("linked") and not started and not tie) else None,
                "captured_at": q.get("received_ts"),
            })
    return rows


# ---------------------------------------------------------------- one source = one refresher

_HTTP_CODE = re.compile(r"\b([45]\d\d)\s+(?:Client|Server)\s+Error|\bHTTP\s*([45]\d\d)\b|\bstatus(?:[ _]code)?\W{0,3}([45]\d\d)\b"
                        r"|^\s*([45]\d\d)\s*$", re.I)


def member_error(text):
    """A refresh failure in member words (2026-10-13 audit A4: members saw upstream hosts, ports and URLs): the exception
    type and a generic cause - timeout / HTTP <code> / connection failed / failed. Never a host, port, URL, driver message
    or key; the full text stays in status()['last_error'] (admin)."""
    s = str(text or "")
    if not s:
        return s
    m = re.match(r"\s*([A-Za-z_][\w.]*)\s*:", s)
    name = m.group(1).rsplit(".", 1)[-1] if m else None
    code = _HTTP_CODE.search(s)
    if re.search(r"timed? ?out|timeout", s, re.I):
        cause = "timeout"
    elif code:
        cause = "HTTP " + next(g for g in code.groups() if g)
    elif re.search(r"connect|resolve|unreachable|refused|max retries|getaddrinfo|reset by peer", s, re.I):
        cause = "connection failed"
    else:
        cause = "failed"
    return f"{name}: {cause}" if name else cause


class Source:
    def __init__(self, name, fetch_fn, normalize_fn, interval=INTERVAL, max_backoff=MAX_BACKOFF,
                 canonical_fn=None, snapshot=True):
        self.name = name
        self.fetch_fn, self.normalize_fn = fetch_fn, normalize_fn
        self.interval, self.max_backoff = interval, max_backoff
        self.canonical_fn = canonical_fn
        self.snapshot = snapshot
        self.lock = threading.Lock()
        self.listings = []            # last good data
        self.last_success = None      # epoch of the last good refresh
        self.last_attempt = None
        self.last_ok = False          # latest attempt succeeded
        self.last_error = None
        self.failures = 0
        self.next_delay = interval
        self.refreshes = 0
        self.version = 0
        self.snapshot_error = None
        self.rows_written = 0
        self._canon_cache = {}
        self._thread = None
        self._stop = threading.Event()
        self.on_refresh = None
        self.link_markets = canonical_fn is None     # tests with injected ids never write match_links.db

    def backoff(self, failures):
        if failures <= 0:
            return self.interval
        return min(self.interval * (2 ** failures), self.max_backoff)

    def _canon(self, L, now_ts):
        t = L.get("teams") or []
        if len(t) != 2 or L.get("problem"):
            return {"match_id": None, "team_keys": None, "flipped": False, "status": "unlinked",
                    "reason": L.get("problem") or "not a two-team market"}
        key = (L.get("event_id"), t[0], t[1], L.get("start"))
        hit = self._canon_cache.get(key)
        if hit and now_ts - hit[0] < CANON_TTL:
            return hit[1]
        fn = self.canonical_fn or canonical
        c = fn(t[0], t[1], L.get("start"), bo=L.get("bo"), event=L.get("event_title"), scope="series")
        self._canon_cache[key] = (now_ts, c)
        return c

    def refresh_once(self):
        """One fetch.  Success replaces the data; failure keeps the last good data and backs off."""
        t0 = time.time()
        self.last_attempt = t0
        try:
            raw = self.fetch_fn()
            listings = self.normalize_fn(raw)
            now = _utcnow()
            for L in listings:
                c = self._canon(L, t0)
                L["canon"] = c
                L["linked"] = _linked(c)
                L["idx_a"] = (1 if c.get("flipped") else 0) if len(L.get("teams") or []) == 2 else None
            if self.link_markets:
                _record_links(listings)
            try:
                _note_group(self.name, listings)        # the other source's snapshot rows read this (started / BO2)
            except Exception:       # noqa: BLE001 - bookkeeping never fails a refresh
                pass
            with self.lock:
                self.listings = listings
                self.last_success = time.time()
                self.last_ok, self.last_error = True, None
                self.failures = 0
                self.next_delay = self.interval
                self.refreshes += 1
                self.version += 1
        except Exception as e:          # noqa: BLE001 - keep last good data, back off
            with self.lock:
                self.last_ok = False
                self.failures += 1
                self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                self.next_delay = self.backoff(self.failures)
                if getattr(e, "retry_after", None):          # the source asked us to wait (e.g. Kalshi Retry-After)
                    self.next_delay = max(self.next_delay, float(e.retry_after))
                self.version += 1
            return False
        if self.snapshot:
            try:
                self.rows_written += write_snapshots(_snapshot_rows(listings, now, self.name))
                self.snapshot_error = None
            except Exception as e:      # noqa: BLE001 - a disk problem never stops the quotes
                self.snapshot_error = f"{type(e).__name__}: {str(e)[:200]}"
        return True

    def is_current(self, now_ts=None):
        now_ts = time.time() if now_ts is None else now_ts
        return bool(self.last_ok and self.last_success and now_ts - self.last_success <= STALE_AFTER)

    def note(self, now_ts=None):
        """The source's state in member words (it reaches the platform blocks' reasons): the failure's cause only
        (member_error) - the full error text is status()['last_error'] (admin)."""
        now_ts = time.time() if now_ts is None else now_ts
        err = member_error(self.last_error)
        if self.last_success is None:
            return f"{self.name} has not loaded yet" + (f" ({err})" if err else "")
        age = int(now_ts - self.last_success)
        if not self.last_ok:
            return f"stale: {self.name} refresh failing ({err}); last good data {age}s old"
        if age > STALE_AFTER:
            return f"stale: last good data {age}s old"
        return None

    def status(self):
        now_ts = time.time()
        with self.lock:
            n_q = sum(len(L.get("quotes") or []) for L in self.listings)
            age = None if self.last_success is None else round(now_ts - self.last_success, 1)
            return {
                "source": self.name, "ok": self.is_current(now_ts), "stale": not self.is_current(now_ts),
                "age_s": age, "last_success": None if self.last_success is None else
                _iso(datetime.fromtimestamp(self.last_success, timezone.utc)),
                "last_attempt": None if self.last_attempt is None else
                _iso(datetime.fromtimestamp(self.last_attempt, timezone.utc)),
                "last_error": self.last_error, "consecutive_failures": self.failures,
                "next_refresh_s": self.next_delay, "refreshes": self.refreshes,
                "listings": len(self.listings), "quotes": n_q, "snapshot_rows_written": self.rows_written,
                "snapshot_error": self.snapshot_error, "running": bool(self._thread and self._thread.is_alive()),
                "note": self.note(now_ts),
            }

    def _loop(self):
        while not self._stop.is_set():
            self.refresh_once()
            if self.on_refresh and not self._stop.is_set():
                try:
                    self.on_refresh(self)
                except Exception:       # noqa: BLE001 - the shadow log never kills the refresher
                    pass
            self._stop.wait(self.next_delay)

    def start(self):
        with self.lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name=f"moneyline-{self.name}", daemon=True)
            self._thread.start()
            return True

    def stop(self, join=None):
        self._stop.set()
        th = self._thread
        if join and th and th.is_alive() and th is not threading.current_thread():
            th.join(join)


SOURCES = {
    "polymarket": Source("polymarket", polymarket_ml.fetch, polymarket_ml.normalize),
    "kalshi": Source("kalshi", kalshi.fetch, kalshi.normalize),
}
_START_LOCK = threading.Lock()


def start(sources=None):
    """Start one background refresher per source (idempotent).  Returns the names started now."""
    srcs = SOURCES if sources is None else sources
    started = []
    with _START_LOCK:
        for name, s in srcs.items():
            s.on_refresh = lambda _s, _srcs=srcs: log_forecasts(_srcs)
            if s.start():
                started.append(name)
    return started


def stop(sources=None, join=None):
    """Stop the refreshers; join=<seconds> waits for an in-flight refresh to finish."""
    for s in (SOURCES if sources is None else sources).values():
        s.stop(join)


def status(sources=None):
    return {name: s.status() for name, s in (SOURCES if sources is None else sources).items()}


# ---------------------------------------------------------------- board

def _platform_block(L, src, now, now_ts, started=None, started_note=None, tie=False):
    """Team-A view of one platform's listing for a match.  started / tie = the match-level decisions (_match_state);
    None = this listing's own start / live flag only.
    fresh       = the source's latest refresh succeeded and is <= STALE_AFTER old (data freshness);
    market_open = the platform says the market is open / accepting orders;
    current     = fresh AND market_open (as before - it gates usable, the baseline and the ml_quotes log)."""
    ia = L["idx_a"]
    qs = L.get("quotes") or []
    src_cur = src.is_current(now_ts)
    note = src.note(now_ts)
    blocker = TIE_REASON if tie else None
    if ia is None or len(qs) != 2:
        return {"available": False, "prob_a": None, "usable": False, "current": False, "fresh": src_cur,
                "market_open": False, "price_only": bool(tie),
                "reason": L.get("problem") or "not a two-team market", "url": L.get("url"),
                "status": None, "quotes": qs}
    qa, qb = qs[ia], qs[1 - ia]
    cur, use, why, prob, spread = evaluate(qa, now, src_cur, L.get("live"), note, started=started,
                                           started_note=started_note, blocker=blocker)
    lt = qa.get("last_trade")
    blk = {
        "available": True, "prob_a": prob, "bid": qa.get("bid"), "ask": qa.get("ask"), "spread": spread,
        "bid_size": qa.get("bid_size"), "ask_size": qa.get("ask_size"), "depth": qa.get("depth"),
        "best_depth": best_depth(qa), "status": qa.get("status"), "current": cur, "usable": use, "reason": why,
        "fresh": src_cur, "market_open": bool(qa.get("market_open")),
        "price_only": bool(tie),       # best-of-2: the midpoint is a PRICE (a draw pays 50-50), not a win probability
        "source_ts": qa.get("source_ts"), "received_ts": qa.get("received_ts"), "url": L.get("url"),
        "event_id": L.get("event_id"), "market_id": qa.get("market_id"), "outcome_id": qa.get("outcome_id"),
        "team_a_name": qa.get("team_name"), "team_b_name": qb.get("team_name"),
        "last_trade": lt, "rules": L.get("rules"),
        "quotes": [qa, qb],        # raw normalized quotes (team A first), each with its platform raw
    }
    if L["platform"] == "kalshi":
        _, use_b, why_b, prob_b, _ = evaluate(qb, now, src_cur, L.get("live"), note, started=started,
                                              started_note=started_note, blocker=blocker)
        gap = round(abs(prob + prob_b - 1), 4) if prob is not None and prob_b is not None else None
        blk.update({"prob_b_yes": prob_b, "prob_b_usable": use_b, "coherence_gap": gap,
                    "coherence_flag": gap is not None and gap > COHERENCE + 1e-9,
                    "prob_note": "team A's own YES midpoint; team B's YES shown separately, never averaged"})
    else:
        blk["prob_note"] = "midpoint of team A's outcome token (two outcomes = complements of one market, no de-vig)"
    if tie:
        blk["prob_note"] = "price only - " + TIE_REASON
    return blk


def choose_baseline(poly, kal, linked=True, link_reason=None):
    """The frozen rule (see module docstring).  poly / kal = platform blocks or None."""
    def out(platform, prob, reason):
        return {"baseline": prob, "platform": platform, "prob_a": prob, "rule": BASELINE_RULE, "reason": reason,
                "label": (f"{platform.capitalize()} midpoint" if platform else UNAVAILABLE),
                "model_version": MODEL_VERSION}
    if not linked:
        return out(None, None, f"Not linked to one canonical match ({link_reason or 'unlinked'}) - never used as baseline")
    pu = bool(poly and poly.get("usable"))
    ku = bool(kal and kal.get("usable"))
    if pu and ku:
        ps, ks = round(poly["spread"], 4), round(kal["spread"], 4)
        if ps != ks:
            w = "polymarket" if ps < ks else "kalshi"
            return out(w, (poly if w == "polymarket" else kal)["prob_a"],
                       f"both usable; {w} has the smaller spread ({min(ps, ks):.2f} vs {max(ps, ks):.2f})")
        pd, kd = poly.get("best_depth") or 0, kal.get("best_depth") or 0
        if pd != kd:
            w = "polymarket" if pd > kd else "kalshi"
            return out(w, (poly if w == "polymarket" else kal)["prob_a"],
                       f"both usable, equal spread {ps:.2f}; {w} has more best-level depth ({max(pd, kd):g} vs {min(pd, kd):g})")
        return out("polymarket", poly["prob_a"], f"both usable, equal spread {ps:.2f} and depth {pd:g}; tie -> Polymarket")
    if pu:
        return out("polymarket", poly["prob_a"], "only Polymarket usable (Kalshi: " +
                   ((kal or {}).get("reason") or "no market") + ")")
    if ku:
        return out("kalshi", kal["prob_a"], "only Kalshi usable (Polymarket: " +
                   ((poly or {}).get("reason") or "no market") + ")")
    return out(None, None, "no usable quote - Polymarket: " + ((poly or {}).get("reason") or "no market") +
               "; Kalshi: " + ((kal or {}).get("reason") or "no market"))


def _merge_unanchored(groups, order):
    """Ids that are not HLTV match ids (matchid's hashed 'cs2:' ids, the name-key 'fb:' fallback) depend on the start
    each platform states; Kalshi keeps the ORIGINAL start while Polymarket's moves, so one match could get two ids.
    Safety net (matchid anchors hashed ids itself): the same team keys, starts within 3 h and DISJOINT platforms are one
    match.  HLTV ids are never merged."""
    keys = [k for k in order if k[0] == "id" and str(k[1]).split(":")[0] in ("fb", "cs2")]
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            if k1 not in groups or k2 not in groups:
                continue
            g1, g2 = groups[k1], groups[k2]
            if set(g1["listings"]) & set(g2["listings"]):
                continue
            L1, L2 = next(iter(g1["listings"].values())), next(iter(g2["listings"].values()))
            s1, s2 = _parse(L1.get("start")), _parse(L2.get("start"))
            if (L1["canon"].get("team_keys") and L1["canon"].get("team_keys") == L2["canon"].get("team_keys") and s1 and s2
                    and abs((s1 - s2).total_seconds()) <= 3 * 3600):
                g1["listings"].update(g2["listings"])
                g1["dupes"].extend(g2["dupes"])
                del groups[k2]
                order.remove(k2)


def _assemble(sources, now=None):
    now = now or _utcnow()          # decides "started"
    now_ts = time.time()            # decides whether a source's data is current (wall clock)
    groups, order = {}, []
    for pname in PLATFORMS:
        src = sources.get(pname)
        if not src:
            continue
        with src.lock:
            listings = list(src.listings)
        for L in listings:
            st = _parse(L.get("start"))
            if st is not None and (now - st).total_seconds() > DROP_STARTED_AFTER:
                continue
            c = L.get("canon") or {}
            if L.get("linked"):
                key = ("id", c["match_id"])
            else:
                key = ("solo", pname, L.get("event_id"))
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"listings": {}, "dupes": []}
                order.append(key)
            if pname in g["listings"]:
                g["dupes"].append(L)
            else:
                g["listings"][pname] = L
    _merge_unanchored(groups, order)
    out = []
    for key in order:
        g = groups[key]
        linked = key[0] == "id" and not g["dupes"]
        link_reason = None
        if key[0] == "id" and g["dupes"]:
            link_reason = "ambiguous - more than one market on one platform maps to this match"
        any_L = g["listings"].get("polymarket") or g["listings"].get("kalshi")
        c = any_L.get("canon") or {}
        if key[0] == "solo":
            link_reason = c.get("reason") or c.get("status") or "unlinked"
        # ONE started / best-of-2 decision for every block of this match (Kalshi has no live flag and keeps the
        # originally scheduled start: Polymarket's live flag or the earliest start must stop Kalshi's in-play price too)
        members = list(g["listings"].values()) + g["dupes"]
        started, _, tie, first = _match_state(members, now)
        blocks = {p: (_platform_block(g["listings"][p], sources[p], now, now_ts, started,
                                      _started_note(g["listings"][p], members, now) if started else None, tie)
                      if p in g["listings"] else None)
                  for p in PLATFORMS}
        ia = any_L.get("idx_a")
        teams = any_L.get("teams") or []
        keys = c.get("team_keys") if linked else None
        name_a = teams[ia] if ia is not None and len(teams) == 2 else (teams[0] if teams else None)
        name_b = teams[1 - ia] if ia is not None and len(teams) == 2 else (teams[1] if len(teams) > 1 else None)
        disp = {p: (b.get("team_a_name"), b.get("team_b_name")) for p, b in blocks.items() if b and b.get("available")}
        base = choose_baseline(blocks["polymarket"], blocks["kalshi"], linked, link_reason)
        if tie and linked:
            base["reason"] = TIE_REASON + " (each platform's price is shown, never as a baseline)"
        pb, kb = blocks["polymarket"], blocks["kalshi"]
        dis = bool(pb and kb and pb.get("usable") and kb.get("usable")
                   and abs(pb["prob_a"] - kb["prob_a"]) >= DISAGREE - 1e-9)
        Lp = g["listings"].get("polymarket")
        bo = (Lp or {}).get("bo") or any_L.get("bo") or c.get("hltv_bo")
        out.append({
            "match_id": c.get("match_id") if linked else None,
            "link_status": c.get("status") if linked else "unlinked",
            "link_reason": link_reason if not linked else c.get("reason"),
            "teams": {"a": {"key": keys[0] if keys else None, "name": name_a},
                      "b": {"key": keys[1] if keys else None, "name": name_b},
                      "names_by_platform": {p: {"a": v[0], "b": v[1]} for p, v in disp.items()}},
            "event": (Lp or any_L).get("event_title"),
            "start": first or (Lp or any_L).get("start"),     # the EARLIEST stated start (a started match leaves Upcoming)
            "started": started,
            "tie_possible": tie,
            "bo": 2 if tie and not bo else bo,
            "scope": "series",
            "platforms": blocks,
            "baseline": base,
            "disagreement": dis,
            "disagreement_gap": (round(abs(pb["prob_a"] - kb["prob_a"]), 4)
                                 if pb and kb and pb.get("prob_a") is not None and kb.get("prob_a") is not None else None),
        })
    out.sort(key=lambda m: (m["start"] or "9999", str(m["match_id"] or "")))
    return out


_BOARD = {"at": 0.0, "key": None, "data": None}
_BOARD_LOCK = threading.Lock()


def board(sources=None, now=None):
    """All CS2 series-winner matches on either platform (cached 5 s, one rebuild at a time)."""
    srcs = SOURCES if sources is None else sources
    if sources is not None or now is not None:
        return _assemble(srcs, now)
    key = tuple(s.version for s in srcs.values())
    t = time.time()
    if _BOARD["data"] is not None and _BOARD["key"] == key and t - _BOARD["at"] < BOARD_TTL:
        return _BOARD["data"]
    with _BOARD_LOCK:
        if _BOARD["data"] is not None and _BOARD["key"] == key and time.time() - _BOARD["at"] < BOARD_TTL:
            return _BOARD["data"]
        data = _assemble(srcs)
        _BOARD.update(at=time.time(), key=key, data=data)
        return data


def unavailable(reason="no market found on Polymarket or Kalshi"):
    return {"baseline": None, "platform": None, "prob_a": None, "rule": BASELINE_RULE, "reason": reason,
            "label": UNAVAILABLE, "model_version": MODEL_VERSION}


_MATCH_CANON = {}       # member lookups never hit matchid more than once per 10 min per match


def match(team_a, team_b, start_iso, bo=None, event=None, sources=None, now=None):
    """The board entry for one match (oriented to canonical team A) plus `query_team_is_a`.
    No market -> {"match_id": .., "baseline": unavailable(..)} - never a 50% default."""
    ck = (team_a, team_b, start_iso, bo, event)
    hit = _MATCH_CANON.get(ck)
    if hit and time.time() - hit[0] < CANON_TTL:
        c = hit[1]
    else:
        c = canonical(team_a, team_b, start_iso, bo=bo, event=event, scope="series")
        if len(_MATCH_CANON) > 5000:
            _MATCH_CANON.clear()
        _MATCH_CANON[ck] = (time.time(), c)
    if not _linked(c):
        return {"match_id": None, "link_reason": c.get("reason") or c.get("status"),
                "baseline": unavailable("match not linked to one canonical match id")}
    rows = board(sources, now)
    hit = next((m for m in rows if m["match_id"] == c["match_id"]), None)
    if hit is None and str(c["match_id"]).startswith("fb:"):
        st = _parse(start_iso)
        hit = next((m for m in rows if m["match_id"] and m["teams"]["a"]["key"] == c["team_keys"][0]
                    and m["teams"]["b"]["key"] == c["team_keys"][1] and st and _parse(m["start"])
                    and abs((_parse(m["start"]) - st).total_seconds()) <= 3 * 3600), None)
    if hit is None:
        return {"match_id": c["match_id"], "query_team_is_a": not c.get("flipped"),
                "baseline": unavailable()}
    return dict(hit, query_team_is_a=not c.get("flipped"))


# ---------------------------------------------------------------- Thunderpick on the Moneylines tab (2026-10-13)
# Thunderpick is the primary bookmaker source (owner 2026-10-13); Polymarket and Kalshi stay attributed comparisons and
# ml-baseline-1 above stays exactly as frozen.  tp_block() is the one reader the tab, its counts and the 0U side
# (ml_sides "ml-sides-2") share, so the card, the filter and the side can never disagree about a price.  Memory only
# (thunderpick.POLLER) - never an upstream call.  Conservative: eligible only when thunderpick itself says so
# (availability "current") AND neither Thunderpick nor this board match marks the match started (the feed has no live
# flag, so a started match's odds may be in-play).
TP_STATE_LABEL = {
    "current": "Current - listed in Thunderpick's latest answer",
    "endpoint_empty": "Thunderpick's latest answer listed no CS2 markets - not confirmed now",
    "not_in_latest_answer": "Not in Thunderpick's latest answer - not confirmed now",
    "stale": "Not current - no Thunderpick answer within its 75 min quote life",
    "started": "Match started - the feed has no live flag, so these odds may be in-play; not used",
    "scope_unconfirmed": "Series scope of this market not established - not used",
    "draw_listed": "A draw price is listed - not a two-way series market",
    "format_excluded": "Format excluded (best-of-2 or unknown - a draw is possible)",
    "invalid_odds": "Odds missing or invalid",
    "policy_inactive": "Not used yet - the Thunderpick-first policy is waiting for its evidence (what the market settles on is tested on BO3 prices)",
    "unlinked": "Not linked to one match",
    "ambiguous": "Two Thunderpick events link to this match - not used",
    "read_error": "Could not be read just now - not used",          # this module's own: the reader failed (not "no event")
}
TP_READ_ERROR = "read_error"
TP_NOTE = ("Listed bookmaker odds. The feed gives no stake limits or execution confirmation - a listed price is not a "
           "guarantee for any stake.")
TP_SCOPE_LABEL = ("Match Winner - read as the series winner because, on BO3 matches, its prices fit the exchange series "
                  "prices better than a map-1 reading (price test; not provider-confirmed)")
TP_SCOPE_UNCONFIRMED = "main market - series scope not established"
_TP_URL = re.compile(r"^https://(?:www\.)?thunderpick\.io/match/\d+$")


def tp_url(url):
    """The match page link, only https://(www.)thunderpick.io/match/<digits>, query and fragment dropped - else None."""
    if not url or not isinstance(url, str):
        return None
    u = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return u if _TP_URL.match(u) else None


def tp_policy():
    """Thunderpick's policy version (thunderpick.POLICY_VERSION), None when the module is unavailable."""
    try:
        import thunderpick
        return getattr(thunderpick, "POLICY_VERSION", None)
    except Exception:          # noqa: BLE001
        return None


_TP_POL = {"fn": None, "at": 0.0, "active": False}
_TP_POL_LOCK = threading.Lock()


def _tp_policy_active(T):
    """thunderpick.policy()['active'] for the older module (its board_block marks a quote usable without the policy
    gate, so THUNDERPICK_POLICY=0 did not stop it - 2026-10-13 review).  Cached 5 s (one store read per board build, not
    one per card); unreadable = inactive."""
    fn = getattr(T, "policy", None)
    if fn is None:
        return False
    t = time.time()
    with _TP_POL_LOCK:
        if _TP_POL["fn"] is fn and t - _TP_POL["at"] < 5:
            return _TP_POL["active"]
    try:
        active = bool((fn(getattr(getattr(T, "POLLER", None), "db", None)) or {}).get("active"))
    except Exception:          # noqa: BLE001
        active = False
    with _TP_POL_LOCK:
        _TP_POL.update(fn=fn, at=t, active=active)
    return active


def _tp_legacy_state(b, T=None, bo=None):
    """availability for a board_block without one (older thunderpick.py): from the policy gate, the format, current /
    scope / odds - conservative (an unknown format is excluded: a draw may be possible)."""
    if b.get("started"):
        return "started"
    if T is not None and not _tp_policy_active(T):
        return "policy_inactive"
    if bo not in (1, 3, 5):
        return "format_excluded"
    if b.get("current") is False:
        return "stale"
    if b.get("draw_odds") is not None:
        return "draw_listed"
    if b.get("scope_confirmed") is False:
        return "scope_unconfirmed"
    if b.get("p_a") is None:
        return "invalid_odds"
    return "current" if b.get("usable") else "stale"


def _tp_takes_start(fn):
    """Does this board_block accept the release contract's start_iso / bo?  Decided from its signature - never by
    catching a TypeError, which would also swallow one raised INSIDE a new board_block."""
    import inspect
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return True
    return ("start_iso" in ps and "bo" in ps) or any(p.kind == p.VAR_KEYWORD for p in ps.values())


def _tp_read_error(m, now, T=None):
    """The block for a Thunderpick READ failure: never eligible, said so - not None, which means "no event" downstream
    (2026-10-13 review: a locked store or a reader bug told members "no event linked" and wrote an immutable ml-sides-2
    row with that false reason; ml_sides.forecast_row writes no row for this marker)."""
    st = _parse((m or {}).get("start"))
    return {"source": "thunderpick", "availability": TP_READ_ERROR, "availability_label": TP_STATE_LABEL[TP_READ_ERROR],
            "eligible": False, "usable": False, "freshly_confirmed": False, "current": False,
            "started": bool((m or {}).get("started")) or bool(st and st <= now), "p_a": None, "p_b": None,
            "odds_a": None, "odds_b": None, "retrieved_at": None, "url": None, "note": TP_NOTE, "quote_time": None,
            "source_updated_at": None, "policy": getattr(T, "POLICY_VERSION", None) if T else None,
            "max_age_s": getattr(T, "QUOTE_MAX_AGE_S", 180) if T else 180}


def tp_block(m, now=None):
    """Thunderpick's block for one board match (team A = the board's canonical team A), or None (unlinked, or
    Thunderpick has no event for it).  A reader failure (module import, store, a board_block bug) is NOT None: it is the
    never-eligible read_error block.  Adds: availability / availability_label (thunderpick's own when it gives them),
    eligible (== usable), started, policy, max_age_s, the member note / scope label, a checked match URL and quote_time
    None (the feed publishes none).  Independently conservative: a started match or a best-of-2 board match (a draw is
    possible) is never eligible whatever thunderpick says; an older thunderpick.py also needs its policy gate active
    and a known best-of-1 / 3 / 5 format.  Never raises."""
    mid = (m or {}).get("match_id")
    if not mid:
        return None
    now = now or _utcnow()
    T = None
    try:
        import thunderpick as T
        fn = T.board_block
        b = fn(mid, start_iso=m.get("start"), bo=m.get("bo")) if _tp_takes_start(fn) else fn(mid)
    except Exception:                                  # noqa: BLE001 - Thunderpick never breaks the tab
        return _tp_read_error(m, now, T)
    if not b:
        return None
    if not isinstance(b, dict):
        return _tp_read_error(m, now, T)
    b = dict(b)
    st = _parse((m or {}).get("start"))
    started = bool(b.get("started")) or bool((m or {}).get("started")) or bool(st and st <= now)
    given = b.get("availability")
    try:
        bo = int((m or {}).get("bo")) if (m or {}).get("bo") is not None else None
    except (TypeError, ValueError):
        bo = None
    draw_fmt = bool((m or {}).get("tie_possible")) or bo == 2
    avail = ("started" if started else "format_excluded" if draw_fmt
             else (given or _tp_legacy_state(b, T, bo)))
    label = b.get("availability_label") if given and avail == given and b.get("availability_label") else \
        TP_STATE_LABEL.get(avail, str(avail).replace("_", " "))
    try:
        pa = float(b.get("p_a")) if b.get("p_a") is not None else None
    except (TypeError, ValueError):
        pa = None
    elig = bool(b.get("eligible", b.get("usable"))) and avail == "current" and pa is not None and 0 < pa < 1
    scope_ok = b.get("scope_confirmed", True) is not False
    b.update(availability=avail, availability_label=label, eligible=elig, usable=elig, freshly_confirmed=elig,
             started=started, policy=b.get("policy") or getattr(T, "POLICY_VERSION", None),
             max_age_s=b.get("max_age_s") or getattr(T, "QUOTE_MAX_AGE_S", 180), url=tp_url(b.get("url")),
             note=TP_NOTE, quote_time=None, source_updated_at=None,
             scope_label=(TP_SCOPE_LABEL.replace("Match Winner", str(b.get("market") or "Match Winner"))
                          if scope_ok else TP_SCOPE_UNCONFIRMED))
    return b


# ---------------------------------------------------------------- shadow forecast log

_FC_LAST = {}           # match_id -> (logged_epoch, platform, prob, available)
_FC_LAST_TP = {}        # match_id -> (logged_epoch, baseline_source, prob, available) of its ml-sides-2 row
_FC_LOCK = threading.Lock()
EXCHANGE_POLICY = "exchange-only|" + MODEL_VERSION     # odds_policy recorded on the ml-baseline-1 rows


def log_forecasts(sources=None, now=None):
    """Record the baseline per linked match: when it changes, or every 30 min (+ the shadow candidate's row next to each
    one).  Next to them, on its own change / 30-min rule, the Thunderpick-first market input of the 0U side
    (ml_sides "ml-sides-2": Thunderpick's eligible price, else this baseline) with baseline_source + odds_policy, so the
    evaluation keeps the two policies apart.  Returns the number of baseline rows written."""
    srcs = SOURCES if sources is None else sources
    now = now or _utcnow()
    rows, extra, tp_rows = [], [], []
    with _FC_LOCK:
        pol = tp_policy()
        for m in _assemble(srcs, now):
            mid = m["match_id"]
            if not mid or m.get("tie_possible"):
                continue                # unlinked, or a best-of-2 (its price is not a series-win probability)
            b = m["baseline"]
            sig = (b["platform"], None if b["prob_a"] is None else round(b["prob_a"], 4), b["prob_a"] is not None)
            last = _FC_LAST.get(mid)
            if not (last and last[1:] == sig and now.timestamp() - last[0] < FORECAST_EVERY):
                rows.append((mid, MODEL_VERSION, _iso(now), b["platform"], b["prob_a"], m["teams"]["a"]["name"],
                             m["teams"]["b"]["name"], m["start"], int(b["prob_a"] is not None), int(m["disagreement"]),
                             b["reason"], b["rule"], b["platform"], EXCHANGE_POLICY))
                _FC_LAST[mid] = (now.timestamp(),) + sig
                cand = _candidate_row(m, _iso(now))
                if cand:                # ml-candidate-1 reads this same baseline price: its source + policy recorded
                    extra.append(tuple(cand[:12]) + (cand[3], EXCHANGE_POLICY))
            r = _sides_tp_row(m, _iso(now), pol, now)
            if r:
                sig2 = (r[12], None if r[4] is None else round(r[4], 4), bool(r[8]))
                last2 = _FC_LAST_TP.get(mid)
                if not (last2 and last2[1:] == sig2 and now.timestamp() - last2[0] < FORECAST_EVERY):
                    tp_rows.append(r)
                    _FC_LAST_TP[mid] = (now.timestamp(),) + sig2
        if rows or tp_rows:
            with _DB_LOCK:
                con = _connect()
                try:
                    # every new row (baseline, candidate, ml-sides-2) carries baseline_source + odds_policy
                    con.executemany("INSERT OR IGNORE INTO ml_forecasts (match_id, model_version, logged_ts, platform,"
                                    " prob_a, team_a, team_b, start, available, disagreement, reason, rule,"
                                    " baseline_source, odds_policy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    rows + extra + tp_rows)
                    con.commit()
                finally:
                    con.close()
    return len(rows)


def _sides_tp_row(m, logged_iso, policy=None, now=None):
    """The ml-sides-2 row (ml_sides.forecast_row: _SCHEMA column order + baseline_source, odds_policy) for one linked
    board match, or None.  A missing / broken Thunderpick or ml_sides module never stops the baseline log, and a
    Thunderpick read error writes no ml-sides-2 row this pass (tp_block's read_error marker; the row's last state and
    its 30-min clock are kept, so the next readable pass decides)."""
    try:
        import ml_sides
        return ml_sides.forecast_row(m, tp_block(m, now), logged_iso, tp_policy=policy)
    except Exception:          # noqa: BLE001
        return None


def _candidate_row(m, logged_iso):
    """The shadow model's row (ml_model.py, "ml-candidate-1", NOT validated) logged next to the baseline's, with the same
    logged_ts.  A missing / broken model never stops the baseline log."""
    try:
        import ml_model
        return ml_model.forecast_row(m, logged_iso)
    except Exception:
        return None


if __name__ == "__main__":
    for s in SOURCES.values():
        s.refresh_once()
    print(json.dumps(status(), indent=1))
    b = board()
    print(len(b), "matches")
