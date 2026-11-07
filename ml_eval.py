#!/usr/bin/env python3
"""
ml_eval.py - offline evaluation of CS2 series-winner market prices against HLTV results.

Research tool only: nothing here is imported by the server, the prop grader or polymarket.match_odds().

  1. Inventory   closed Polymarket CS2 events (gamma, series 10310) with a series-winner market, each mapped
                 to exactly one HLTV match (exact team names / listed aliases, same two teams, date +-1 day),
                 the HLTV series winner (maps won, cross-checked with HLTV's series row), and a pre-match
                 price = the LAST point of Polymarket's recorded price history (CLOB /prices-history on the
                 outcome-0 token) at or before scheduled start - 30 min and no older than 6 h before that.
                 Kalshi (KXCS2GAME, series winner only) the same way from its official 1-minute candlesticks
                 (YES bid/ask at the close of each minute).
  2. Baseline    market price vs a fixed 50%: log-loss, Brier, calibration, match-grouped bootstrap CIs,
                 by probability range, LAN/online, tier proxy (Polymarket volume tertiles).
  3. Features    chronological split 60/20/20 (dev/validation/test, by match start). One feature at a time
                 as a regularised correction  logit(p) = logit(p_mkt) + b*x  (b fitted with L2 on dev, the L2
                 strength chosen by time-ordered expanding-window cross-validation INSIDE dev - validation is
                 used only for the promotion gain and its CI). Promoted only if validation log-loss improves with
                 a match-grouped 95% CI above zero. A non-final run writes the freeze (pinned dev/val/test match
                 ids, promoted features, coefficients, code tag, sha256) once; --final reloads it, rebuilds the
                 split from the pinned ids, aborts on any sha mismatch and scores the test set ONCE (a second
                 --final needs --rescore-test and is reported as a rescore). Test rows are never written to the
                 repo-root results file.
  4. Forward     ml_snapshots.db (written by the live moneyline collector, when it exists): per match and
                 cohort, the latest snapshot at or before start - 30 min (earliest start any source states),
                 scored when it was available then and is no older than 6 h before that cutoff (otherwise a
                 counted exclusion); best-of-2 excluded explicitly. forward_compare() pairs the frozen shadow
                 candidate (ml_model.py, "ml-candidate-1") with the baseline on the same matches and applies the
                 pre-registered PROMOTION_RULE.

Prices are Polymarket's recorded price history (mid/last-trade series), not executable quotes we captured.

Use:  python ml_eval.py --cache DIR [--report ML_EVAL.md] [--json ml_eval_results.json]
Needs HLTV_DB_DSN (read-only HLTV database) for the HLTV side.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

UA = {"User-Agent": "cs2-scout/1.0"}
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
CS2_SERIES = 10310
KALSHI_SERIES = "KXCS2GAME"          # series winner only (KXCS2MAP = map winner, never mixed in)

CUTOFF_MIN = 30                      # price must be at or before scheduled start - 30 min
MAX_AGE_H = 6                        # ... and no older than 6 h before that cutoff
FIDELITY = 10                        # minutes per Polymarket history point
POST_START_H = 2                     # history requested up to start + 2 h (only to classify "post-start only")
MAX_EVENTS = 3000
RATE = 5.0                           # requests per second, all upstreams together
RESOLVED_EPS = 0.01                  # a "pre-match" price at <=1% / >=99% means the start time is wrong
KALSHI_MAX_SPREAD = 0.10             # wider YES bid/ask than this at the cutoff = no usable Kalshi quote

SPLIT = (0.6, 0.2, 0.2)
LAMBDAS = (0.0, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0)
BOOT = 2000
SEED = 7
EDGE_THRESHOLDS = (3, 4, 5, 6)       # percentage points
MIN_BETS_CI = 20                     # fewer bets than this: no ROI "confidence interval" (a resample of 1 bet is not one)
CV_FOLDS = 4                         # expanding-window folds inside development for the L2 strength
CODE_TAG = "ml-eval-2"               # part of the frozen hash: a code change to the staged tests needs a new freeze
BO2_EXCLUSION = "best-of-2 (a draw is possible - the price is not a series-win probability)"
COST = 0.01                          # 1 cent added to the recorded price when "buying"

# Feature constants - fixed before any test was run (no tuning on the data)
ROSTER_DAYS = 60
RATING_K = 20                        # player rating shrunk toward 1.0 with k = 20 maps
MAPPOOL_DAYS = 180
MAPPOOL_TOP = 4
MAPPOOL_K = 10                       # pooled win rate on the top-4 maps shrunk toward 0.5, k = 10 maps
FORM_N = 10
FORM_DAYS = 365
FORM_K = 5                           # last-10 series win rate shrunk toward 0.5, k = 5 series
H2H_DAYS = 365
H2H_KEEP = 3                         # both teams kept >= 3 of today's 5 players
H2H_K = 3

# PRE-REGISTERED PROMOTION RULE (2026-10-12, before any forward forecast of the candidate existed). The frozen shadow model
# (ml_model.py, ml_model_frozen.json) becomes a member-visible model probability WITH stakes only when ALL hold on the
# forward cohort (matches after the freeze, its forecast = the last one logged >= 30 min before start, paired with the
# ml-baseline-1 forecast of the same match): n >= 300 matches, log-loss gain vs the baseline > 0 with the match-grouped
# 95% bootstrap CI (2000 resamples) excluding 0, calibration slope in [0.8, 1.2]. Until then the probability may be shown
# only labelled "candidate - not validated", never with a stake. Changing this constant needs a new model version.
PROMOTION_RULE = {
    "model_version": "ml-candidate-1",
    "baseline_version": "ml-baseline-1",
    "registered": "2026-10-12",
    "min_forward_matches": 300,
    "gain": "mean(log-loss baseline - log-loss model) > 0 with the 95% CI lower bound > 0",
    "ci": "match-grouped percentile bootstrap, 2000 resamples, seed 7",
    "calibration_slope_range": [0.8, 1.2],
    "calibration_slope": "logistic regression of the result on logit(model probability), point estimate",
    "until_promoted": "model probability may be shown only labelled 'candidate - not validated'; never a stake",
}

# ACTIVATION POLICY (owner review 2026-10-12): PROMOTION_RULE above is a CHECKPOINT that triggers a review, never an automatic
# switch. Nothing in code ever turns on a moneyline stake (server.ml_model_state: available False, probability None;
# ml_model.predict: stake None, validated False). Stakes need ALL of: the checkpoint met on the frozen forward cohort; a
# positive return at the prices that were actually available (the recorded ASK of the side bet, plus fees) with the
# match-grouped 95% CI above 0 over the same cohort; calibration within range in every probability band with >= 30
# matches; the owner's explicit approval; and a new model version (re-frozen). An unsuccessful or inconclusive candidate
# stays in shadow.
ACTIVATION_POLICY = {
    "automatic": False,
    "checkpoint": "PROMOTION_RULE (n >= 300 forward matches, log-loss gain CI > 0, calibration slope 0.8-1.2) starts a review only",
    "requires": ["checkpoint met on the frozen forward cohort",
                 "return at the recorded ask (+ fees) of the side bet > 0 with the match-grouped 95% CI lower bound > 0",
                 "calibration in range in every probability band with >= 30 matches",
                 "explicit owner approval", "a new, re-frozen model version"],
    "otherwise": "shadow - model probability labelled 'candidate - not validated', never a stake",
    "review_plan": "REVIEW_PLAN (fixed looks, tolerances, minimum evidence) - the only way a candidate reaches owner review",
}

# REVIEW PLAN (owner review 2026-10-12: "make activation criteria statistically workable"). Fixed BEFORE any forward
# result exists; changing it makes a new plan version and restarts the count. Three scheduled looks - nothing in
# between is a decision, and a look's result is recorded once (ml_reviews.json) and never recomputed with more data.
#   * Looks at 300 / 600 / 1000 paired forward matches (candidate and baseline both scorable, one row per HLTV match,
#     matches starting after the freeze). A look runs the first time forward_eval sees the count at or past it.
#   * Error spending (O'Brien-Fleming, 3 looks, overall two-sided alpha 0.05): the gain and return intervals use
#     99.95% / 98.6% / 95.5% match-grouped bootstrap intervals at looks 1 / 2 / 3, so three looks together keep the
#     false-promotion risk at about 5% instead of the ~11% that three plain 95% looks would give.
#   * Minimum evidence at a look: forward matches from >= 4 distinct calendar weeks, no single week holding > 40% of
#     them (one hot week of one tournament is not enough).
#   * Calibration tolerances (all must hold): slope point estimate in [0.8, 1.2] (the frozen rule) AND its 95%
#     interval inside [0.6, 1.4]; calibration-in-the-large: 95% interval of mean(result) - mean(probability) inside
#     [-0.06, +0.06]; per favourite band (0.50-0.60, 0.60-0.70, 0.70-0.80, 0.80-1.00) with >= 50 matches: the gap
#     |observed - predicted| minus 1.96 x its standard error must be <= 0.05 (a band is "miscalibrated" only when the
#     data show a gap of more than 5 points); at least 2 bands must have >= 50 matches.
#   * Return at prices that were really available: side bought when model probability - recorded ASK of that side
#     >= 0.03, price + fees (Kalshi 0.07 x p x (1 - p) per contract, Polymarket 0), 1 unit per bet; needs >= 100 bets
#     and the look's interval for ROI above 0. No recorded ask at the cutoff = no bet (counted).
#   * Futility: at look 2, a gain point estimate <= 0 ends the candidate (stays shadow, a new candidate needs a new
#     freeze and a new plan count).
#   * Passing a look = "eligible for owner review" only (ACTIVATION_POLICY): owner approval and a re-frozen version
#     are still required; nothing in code turns on a stake.
REVIEW_PLAN = {
    "version": "review-plan-1",
    "registered": "2026-10-12",
    "looks": [300, 600, 1000],
    "ci_levels": [0.9995, 0.986, 0.955],
    "error_spending": "O'Brien-Fleming, 3 looks, overall two-sided alpha 0.05",
    "min_weeks": 4,
    "max_week_share": 0.40,
    "slope_point": [0.8, 1.2],
    "slope_ci": [0.6, 1.4],
    "citl_ci": [-0.06, 0.06],
    "bands": [[0.5, 0.6], [0.6, 0.7], [0.7, 0.8], [0.8, 1.0001]],
    "band_min_n": 50,
    "band_tolerance": 0.05,
    "min_testable_bands": 2,
    "edge_vs_ask": 0.03,
    "fees": {"kalshi": "0.07 * p * (1 - p) per contract", "polymarket": "0"},
    "min_bets": 100,
    "futility_look": 2,
}

# Polymarket outcome names that differ from the HLTV team name beyond the "Team"/"Esports" affixes.
# Exact, hand-checked aliases only (never fuzzy - academies and "Junior" sides are different teams).
ALIASES = {
    "NIP": "Ninjas in Pyjamas",
    "Keyd": "Keyd Stars",
    "Aurora Young Blood": "Aurora Young Blud",          # HLTV's spelling
    "FC Famalicão Esports": "Famalicão",
}


_ALIAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team_aliases.json")
_FILE_ALIASES = {}


def _file_aliases():
    """{normalised platform name: HLTV team id} from team_aliases.json (maintained, each entry with its proof)."""
    if _ALIAS_FILE not in _FILE_ALIASES:
        out = {}
        try:
            with open(_ALIAS_FILE, encoding="utf-8") as fh:
                for a in json.load(fh).get("aliases") or []:
                    if a.get("name") and a.get("team_id") is not None:
                        out[_norm(a["name"])] = int(a["team_id"])
        except (OSError, ValueError):
            pass
        _FILE_ALIASES[_ALIAS_FILE] = out
    return _FILE_ALIASES[_ALIAS_FILE]


# ---------------------------------------------------------------------------------------------------- utils
def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


_AFFIX = re.compile(r"^(team)\s+|\s+(esports|e-sports|esport|gaming|club|team)$", re.I)


def name_variants(name):
    """Exact normalised name first, then without a leading 'Team' / trailing 'Esports' etc. (same rule as
    hltv.team_name_variants). Exact matches only - never fuzzy."""
    out, cur = [], str(name or "").strip()
    while cur and _norm(cur) not in out:
        out.append(_norm(cur))
        nxt = _AFFIX.sub("", cur).strip()
        if nxt == cur:
            break
        cur = nxt
    return [v for v in out if len(v) >= 2]


def parse_ts(s):
    """Polymarket/Kalshi time string -> unix seconds (UTC), or None."""
    if not s:
        return None
    s = str(s).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"([+-]\d\d)$", r"\1:00", s)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def utc_date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date()


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(z):
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def logloss1(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def wilson(k, n, z=1.96):
    if not n:
        return (None, None)
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 4), round(c + h, 4))


# ---------------------------------------------------------------------------------------------------- http
class Fetcher:
    """Polite cached GET: one shared rate limit (RATE/s), UA header, bounded timeouts, retries on 429/5xx.
    Every answer (including 404) is cached on disk, so a rerun makes no upstream requests."""

    def __init__(self, cache_dir, rate=RATE, session=None, offline=False):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.min_gap = 1.0 / rate
        self.offline = offline
        self._last = 0.0
        self._lock = threading.Lock()
        self.requests = 0
        self.hits = 0
        if session is None and not offline:
            import requests
            session = requests.Session()
        self.s = session

    def _path(self, url, params):
        key = url + "?" + json.dumps(params or {}, sort_keys=True)
        h = hashlib.sha1(key.encode()).hexdigest()
        return os.path.join(self.dir, h[:2], h + ".json")

    def get_json(self, url, params=None):
        path = self._path(url, params)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                self.hits += 1
                return rec["body"]
            except (OSError, ValueError, KeyError):
                pass
        if self.offline:
            raise RuntimeError(f"offline and not cached: {url}")
        body = self._fetch(url, params)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"url": url, "params": params, "at": time.time(), "body": body}, f)
        os.replace(tmp, path)
        return body

    def _fetch(self, url, params):
        delay = 1.0
        for attempt in range(5):
            with self._lock:
                wait = self._last + self.min_gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            self.requests += 1
            try:
                r = self.s.get(url, params=params, headers=UA, timeout=(10, 30))
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 4:
                    r.raise_for_status()
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        return None


# ---------------------------------------------------------------------------------------------------- Polymarket
def fetch_closed_events(f, max_events=MAX_EVENTS):
    """Closed CS2 events, newest first (by id), via gamma's keyset pagination (plain offsets stop at 2000).
    Stops after max_events; returns (events, more_exist)."""
    events, cursor = [], None
    while len(events) < max_events:
        params = {"series_id": CS2_SERIES, "closed": "true", "limit": 100, "order": "id", "ascending": "false"}
        if cursor:
            params["after_cursor"] = cursor
        body = f.get_json(f"{GAMMA}/events/keyset", params) or {}
        batch = body.get("events") or []
        events += batch
        cursor = body.get("next_cursor")
        if not batch or not cursor:
            break
    seen, out = set(), []
    for ev in events:
        if ev.get("id") not in seen:
            seen.add(ev.get("id"))
            out.append(ev)
    return out[:max_events], bool(cursor) and len(out) >= max_events


_NOT_SERIES = re.compile(r"\b(map|game)\s*\d|handicap|total|o/u|over|under|kills|pistol|first", re.I)


def series_market(ev):
    """(market, how) for the event's series-winner market, or (None, reason).
    Newer events tag it sportsMarketType 'moneyline' (map winners are 'child_moneyline' - never used).
    The oldest events have no type: accepted only when the event has exactly one 2-outcome market whose
    question is the plain 'Counter-Strike: A vs B' title with team-name outcomes."""
    mkts = ev.get("markets") or []
    ml = [m for m in mkts if m.get("sportsMarketType") == "moneyline"]
    if len(ml) == 1:
        return ml[0], "moneyline"
    if len(ml) > 1:
        return None, "several moneyline markets"
    if any(m.get("sportsMarketType") for m in mkts):
        return None, "no series-winner market"
    if len(mkts) == 1:
        m = mkts[0]
        q = str(m.get("question") or "")
        try:
            outs = json.loads(m.get("outcomes") or "[]")
        except ValueError:
            outs = []
        if (q.startswith("Counter-Strike:") and " vs" in q and not _NOT_SERIES.search(q)
                and len(outs) == 2 and not {o.lower() for o in outs} & {"yes", "no", "over", "under"}):
            return m, "untyped single market"
    return None, "no series-winner market"


def market_winner_idx(m):
    """Index of the outcome Polymarket resolved to (final outcomePrices 1/0), else None."""
    try:
        pr = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except (ValueError, TypeError):
        return None
    if len(pr) == 2 and sorted(pr) == [0.0, 1.0]:
        return pr.index(1.0)
    return None


def prematch_point(history, start_ts, cutoff_min=CUTOFF_MIN, max_age_h=MAX_AGE_H):
    """Last history point at or before start - cutoff_min, no older than max_age_h before that cutoff.
    history: [{"t": unix, "p": price}]. Returns (point, None) or (None, exclusion reason)."""
    cutoff = start_ts - cutoff_min * 60
    pts = sorted((h for h in history or [] if h.get("t") is not None and h.get("p") is not None),
                 key=lambda h: h["t"])
    if not pts:
        return None, "no price history"
    before = [h for h in pts if h["t"] <= cutoff]
    if not before:
        if all(h["t"] >= start_ts for h in pts):
            return None, "post-start points only"
        return None, "no price before cutoff"
    last = before[-1]
    if last["t"] < cutoff - max_age_h * 3600:
        return None, "no price before cutoff"          # newest pre-cutoff point is older than 6 h
    p = float(last["p"])
    if p <= RESOLVED_EPS or p >= 1 - RESOLVED_EPS:
        return None, "price already resolved before cutoff (start time wrong)"
    return {"t": int(last["t"]), "p": p}, None


def price_history(f, token, start_ts):
    cutoff = start_ts - CUTOFF_MIN * 60
    body = f.get_json(f"{CLOB}/prices-history", {
        "market": token, "startTs": int(cutoff - MAX_AGE_H * 3600), "endTs": int(start_ts + POST_START_H * 3600),
        "fidelity": FIDELITY})
    return (body or {}).get("history") or []


# ---------------------------------------------------------------------------------------------------- Kalshi
_K_SCHED = re.compile(r"originally scheduled for ([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) at (\d{1,2}):(\d\d) ([AP]M) (EDT|EST)")
_MON = {m: i + 1 for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def kalshi_start(rules):
    """Scheduled start from rules_primary ('... originally scheduled for Oct 11, 2026 at 3:30 AM EDT')."""
    m = _K_SCHED.search(str(rules or ""))
    if not m:
        return None
    mon, day, yr, hh, mm, ap, tz = m.groups()
    h = int(hh) % 12 + (12 if ap == "PM" else 0)
    off = -4 if tz == "EDT" else -5
    d = datetime(int(yr), _MON[mon], int(day), h, int(mm), tzinfo=timezone(timedelta(hours=off)))
    return d.timestamp()


def fetch_kalshi_markets(f, max_events=MAX_EVENTS):
    """Settled KXCS2GAME markets: live endpoint plus the historical endpoint (markets settled before
    Kalshi's historical cutoff move there)."""
    out = []
    for path, hist in (("/markets", False), ("/historical/markets", True)):
        cursor = None
        while True:
            p = {"series_ticker": KALSHI_SERIES, "limit": 1000}
            if not hist:
                p["status"] = "settled"
            if cursor:
                p["cursor"] = cursor
            body = f.get_json(KALSHI + path, p) or {}
            for m in body.get("markets") or []:
                m["_historical"] = hist
                out.append(m)
            cursor = body.get("cursor")
            if not cursor or not body.get("markets"):
                break
    seen, uniq = set(), []
    for m in out:
        if m.get("ticker") in seen:
            continue
        seen.add(m.get("ticker"))
        uniq.append(m)
    events = defaultdict(list)
    for m in uniq:
        events[m.get("event_ticker")].append(m)
    keys = sorted(events, key=lambda e: max(str(m.get("close_time") or "") for m in events[e]), reverse=True)
    return {k: sorted(events[k], key=lambda m: m.get("ticker")) for k in keys[:max_events]}, len(keys)


def kalshi_candles(f, m, start_ts):
    cutoff = start_ts - CUTOFF_MIN * 60
    params = {"start_ts": int(cutoff - MAX_AGE_H * 3600), "end_ts": int(start_ts + POST_START_H * 3600),
              "period_interval": 1}
    path = (f"/historical/markets/{m['ticker']}/candlesticks" if m.get("_historical")
            else f"/series/{KALSHI_SERIES}/markets/{m['ticker']}/candlesticks")
    body = f.get_json(KALSHI + path, params) or {}
    return body.get("candlesticks") or []


def kalshi_prematch(candles, start_ts):
    """YES mid at the close of the last 1-minute candle ending at or before the cutoff (two-sided book,
    spread <= 10c, no older than 6 h). Returns (point, None) or (None, reason)."""
    cutoff = start_ts - CUTOFF_MIN * 60
    pts = []
    for c in candles:
        t = c.get("end_period_ts")
        try:
            bid = float((c.get("yes_bid") or {}).get("close_dollars"))
            ask = float((c.get("yes_ask") or {}).get("close_dollars"))
        except (TypeError, ValueError):
            continue
        if t is not None:
            pts.append((t, bid, ask))
    pts.sort()
    if not pts:
        return None, "no price history"
    before = [x for x in pts if x[0] <= cutoff]
    if not before:
        return None, ("post-start points only" if all(x[0] >= start_ts for x in pts) else "no price before cutoff")
    t, bid, ask = before[-1]
    if t < cutoff - MAX_AGE_H * 3600:
        return None, "no price before cutoff"
    if bid <= 0 or ask >= 1 or ask - bid > KALSHI_MAX_SPREAD:
        return None, "no two-sided quote (spread <= 10c) at cutoff"
    p = (bid + ask) / 2
    if p <= RESOLVED_EPS or p >= 1 - RESOLVED_EPS:
        return None, "price already resolved before cutoff (start time wrong)"
    return {"t": int(t), "p": p, "bid": bid, "ask": ask}, None


# ---------------------------------------------------------------------------------------------------- HLTV
class Hltv:
    """In-memory copy of the HLTV tables the evaluation needs (read once, read-only)."""

    def __init__(self):
        self.teams = {}                       # teamid -> name
        self.exact = defaultdict(set)         # normalised exact name -> teamids
        self.stripped = defaultdict(set)      # affix-stripped name -> teamids
        self.matches = {}                     # matchid -> dict
        self.by_date = defaultdict(list)      # date -> [matchid]
        self.player_rows = defaultdict(list)  # playerid -> [(date, rating)] sorted
        self.team_player_rows = defaultdict(list)   # teamid -> [(date, matchid, playerid)] sorted
        self.lineups = defaultdict(set)       # (matchid, teamid) -> playerids
        self.team_maps = defaultdict(list)    # teamid -> [(date, mapname, won)] sorted
        self.team_series = defaultdict(list)  # teamid -> [(date, matchid, oppid, won)] sorted
        self.rank_dates = []
        self.last_date = date.min
        self.first_date = None                # set by load_hltv_recent: matches before it were NOT loaded

    @classmethod
    def load(cls, con):
        h = cls()
        cur = con.cursor()
        cur.execute("SET statement_timeout = 180000")    # bulk read; the pool resets it on return
        cur.execute("SELECT teamid, name FROM teams")
        for tid, name in cur.fetchall():
            h.add_team(tid, name)
        cur.execute("SELECT matchid, date, event, lan, coalesce(forfeited_maps, 0), source FROM match_overview")
        for mid, d, ev, lan, ff, src in cur.fetchall():
            h.matches[mid] = {"matchid": mid, "date": d, "event": ev, "lan": lan, "forfeited": ff, "source": src,
                              "series": {}, "maps_won": defaultdict(int), "teams": set()}
        cur.execute("""SELECT own.matchid, own.teamid, own.mapid, own.score, opp.score, mp.name
                       FROM matches own
                       JOIN matches opp ON opp.matchid = own.matchid AND opp.teamid <> own.teamid
                                       AND opp.mapid = own.mapid AND opp.sideid = 0
                       LEFT JOIN maps mp ON mp.mapid = own.mapid
                       WHERE own.sideid = 0""")
        map_rows = cur.fetchall()
        cur.execute("""SELECT ps.matchid, ps.teamid, ps.playerid, ps.rating FROM player_stats ps
                       WHERE ps.mapid <> 0 AND ps.sideid = 0""")
        prow = cur.fetchall()
        try:
            cur.execute("SELECT DISTINCT date FROM team_rankings ORDER BY date")
            h.rank_dates = [r[0] for r in cur.fetchall()]
        except Exception:
            con.rollback()
        h.build(map_rows, prow)
        return h

    def add_team(self, tid, name):
        self.teams[tid] = name
        v = name_variants(name)
        if v:
            self.exact[v[0]].add(tid)
            for x in v[1:]:
                self.stripped[x].add(tid)

    def build(self, map_rows, player_rows):
        """map_rows: (matchid, teamid, mapid, own score, opp score, map name), mapid 0 = series row.
        player_rows: (matchid, teamid, playerid, rating) per played map."""
        for mid, tid, mapid, own, opp, mname in map_rows:
            m = self.matches.get(mid)
            if m is None:
                continue
            m["teams"].add(tid)
            if mapid == 0:
                m["series"][tid] = own
            else:
                if own is not None and opp is not None and own > opp:
                    m["maps_won"][tid] += 1
                if own is not None and opp is not None:
                    self.team_maps[tid].append((m["date"], _norm(mname), own > opp))
        self.last_date = max((m["date"] for m in self.matches.values()), default=date.min)
        for mid, m in self.matches.items():
            self.by_date[m["date"]].append(mid)
            w = self.winner(mid)
            if w is not None and len(m["teams"]) == 2:
                a, b = sorted(m["teams"])
                self.team_series[a].append((m["date"], mid, b, w == a))
                self.team_series[b].append((m["date"], mid, a, w == b))
        for mid, tid, pid, rating in player_rows:
            m = self.matches.get(mid)
            if m is None:
                continue
            self.team_player_rows[tid].append((m["date"], mid, pid))
            self.lineups[(mid, tid)].add(pid)
            if rating is not None:
                self.player_rows[pid].append((m["date"], float(rating)))
        for d in (self.player_rows, self.team_player_rows, self.team_maps, self.team_series):
            for k in d:
                d[k].sort()

    # ---- mapping
    def team_ids(self, name):
        """HLTV team ids for a Polymarket/Kalshi team name: exact normalised name (or a listed alias - ALIASES here
        or a proven entry of team_aliases.json); only if nothing matches exactly, the name with
        'Team'/'Esports'/'Gaming'/'Club' dropped on either side."""
        tid = _file_aliases().get(_norm(name))
        if tid is not None and tid in self.teams:
            return {tid}
        name = ALIASES.get(name, name)
        v = name_variants(name)
        if not v:
            return set()
        if self.exact.get(v[0]):
            return set(self.exact[v[0]])
        ids = set()
        for x in v:
            ids |= self.exact.get(x, set()) | self.stripped.get(x, set())
        return ids

    def winner(self, mid):
        """Series winner from maps won, cross-checked against HLTV's series row; None if missing/inconsistent."""
        m = self.matches[mid]
        if len(m["teams"]) != 2:
            return None
        a, b = sorted(m["teams"])
        wa, wb = m["maps_won"].get(a, 0), m["maps_won"].get(b, 0)
        if wa == wb:
            return None
        w = a if wa > wb else b
        s = m["series"]
        if a in s and b in s and s[a] is not None and s[b] is not None and s[a] != s[b]:
            if (s[a] > s[b]) != (w == a):
                return None
        return w

    def find_match(self, ids_a, ids_b, day):
        """Unique HLTV match within +-1 day whose two teams are one from ids_a and one from ids_b.
        Returns (matchid, team_a_id, team_b_id, reason)."""
        hits = []
        for dd in (-1, 0, 1):
            for mid in self.by_date.get(day + timedelta(days=dd), []):
                t = self.matches[mid]["teams"]
                if len(t) != 2:
                    continue
                x, y = sorted(t)
                if x in ids_a and y in ids_b and x != y:
                    hits.append((mid, x, y))
                elif y in ids_a and x in ids_b and x != y:
                    hits.append((mid, y, x))
        if not hits:
            return None, None, None, "no HLTV match (same teams, +-1 day)"
        if len({h[0] for h in hits}) > 1 or len(hits) > 1:
            return None, None, None, "ambiguous HLTV match"
        return hits[0][0], hits[0][1], hits[0][2], None

    def pair_near(self, ids_a, ids_b, day, days=7):
        """Diagnostic: does HLTV have ANY match of these two teams within +-days?"""
        for dd in range(-days, days + 1):
            for mid in self.by_date.get(day + timedelta(days=dd), []):
                t = self.matches[mid]["teams"]
                if t & ids_a and t & ids_b:
                    return True
        return False

    # ---- features (rows strictly before the match date)
    def roster(self, tid, day, days=ROSTER_DAYS):
        rows = self.team_player_rows.get(tid, [])
        lo = bisect.bisect_left(rows, (day - timedelta(days=days),))
        hi = bisect.bisect_left(rows, (day,))
        last, n = {}, Counter()
        for d, mid, pid in rows[lo:hi]:
            n[pid] += 1
            last[pid] = max(last.get(pid, d), d)
        return sorted(n, key=lambda p: (last[p], n[p], -p), reverse=True)[:5]

    def player_rating(self, pid, day, days=ROSTER_DAYS, k=RATING_K):
        rows = self.player_rows.get(pid, [])
        lo = bisect.bisect_left(rows, (day - timedelta(days=days),))
        hi = bisect.bisect_left(rows, (day,))
        vals = [r for _, r in rows[lo:hi]]
        return (sum(vals) + k * 1.0) / (len(vals) + k), len(vals)

    def f_rating(self, ta, tb, day):
        out = []
        for t in (ta, tb):
            r = self.roster(t, day)
            if not r:
                return None
            out.append(sum(self.player_rating(p, day)[0] for p in r) / len(r))
        return out[0] - out[1]

    def f_mappool(self, ta, tb, day):
        out = []
        for t in (ta, tb):
            rows = self.team_maps.get(t, [])
            lo = bisect.bisect_left(rows, (day - timedelta(days=MAPPOOL_DAYS),))
            hi = bisect.bisect_left(rows, (day,))
            cnt, won = Counter(), Counter()
            for _, mp, w in rows[lo:hi]:
                cnt[mp] += 1
                won[mp] += bool(w)
            if not cnt:
                return None
            top = [mp for mp, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:MAPPOOL_TOP]]
            n, w = sum(cnt[m] for m in top), sum(won[m] for m in top)
            out.append((w + 0.5 * MAPPOOL_K) / (n + MAPPOOL_K))
        return out[0] - out[1]

    def f_form(self, ta, tb, day):
        out = []
        for t in (ta, tb):
            rows = self.team_series.get(t, [])
            lo = bisect.bisect_left(rows, (day - timedelta(days=FORM_DAYS),))
            hi = bisect.bisect_left(rows, (day,))
            last = rows[lo:hi][-FORM_N:]
            if not last:
                return None
            w = sum(1 for r in last if r[3])
            out.append((w + 0.5 * FORM_K) / (len(last) + FORM_K))
        return out[0] - out[1]

    def f_h2h(self, ta, tb, day):
        ra, rb = set(self.roster(ta, day)), set(self.roster(tb, day))
        rows = self.team_series.get(ta, [])
        lo = bisect.bisect_left(rows, (day - timedelta(days=H2H_DAYS),))
        hi = bisect.bisect_left(rows, (day,))
        wa = wb = 0
        for _, mid, opp, won in rows[lo:hi]:
            if opp != tb:
                continue
            if (len(self.lineups.get((mid, ta), set()) & ra) >= H2H_KEEP
                    and len(self.lineups.get((mid, tb), set()) & rb) >= H2H_KEEP):
                wa += bool(won)
                wb += not won
        return (wa - wb) / (wa + wb + H2H_K), wa + wb


# ---------------------------------------------------------------------------------------------------- inventory
EXCLUSIONS = ("unresolved team mapping", "no HLTV match (same teams, +-1 day)", "ambiguous HLTV match", "forfeit",
              "no HLTV result", "no price history", "no price before cutoff", "post-start points only",
              "price already resolved before cutoff (start time wrong)",
              "duplicate market for the same HLTV match (kept highest volume)")


def build_inventory(f, hl, max_events=MAX_EVENTS, log=None):
    """Polymarket closed events -> eligible observations + exclusion counts (applied in this order: market,
    team mapping, HLTV match, forfeit, HLTV result, resolution cross-check, price)."""
    events, more = fetch_closed_events(f, max_events)
    excl, how_counts, unresolved = Counter({k: 0 for k in EXCLUSIONS}), Counter(), Counter()
    excl["Polymarket resolution disagrees with HLTV winner"] = 0
    near = Counter()
    obs = []
    for i, ev in enumerate(events):
        if log and i % 200 == 0:
            log(f"  polymarket {i}/{len(events)}  requests={f.requests} cache_hits={f.hits}")
        m, how = series_market(ev)
        if m is None:
            excl[how] += 1
            continue
        how_counts[how] += 1
        if re.search(r"\(\s*BO\s*2\s*\)", str(m.get("question") or ev.get("title") or ""), re.I):
            excl[BO2_EXCLUSION] += 1
            continue
        try:
            outs = json.loads(m.get("outcomes") or "[]")
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except ValueError:
            outs, toks = [], []
        if len(outs) != 2 or len(toks) != 2:
            excl["market without two outcomes/tokens"] += 1
            continue
        start = parse_ts(m.get("gameStartTime"))
        if start is None:
            excl["no scheduled start"] += 1
            continue
        ids_a, ids_b = hl.team_ids(outs[0]), hl.team_ids(outs[1])
        if not ids_a or not ids_b:
            excl["unresolved team mapping"] += 1
            unresolved.update(o for o, ids in ((outs[0], ids_a), (outs[1], ids_b)) if not ids)
            continue
        mid, ta, tb, why = hl.find_match(ids_a, ids_b, utc_date(start))
        if why:
            excl[why] += 1
            if why.startswith("no HLTV match"):
                near["pair has an HLTV match within +-7 days"] += hl.pair_near(ids_a, ids_b, utc_date(start))
                near["start on/after the newest HLTV date"] += utc_date(start) >= hl.last_date
            continue
        hm = hl.matches[mid]
        if hm["forfeited"]:
            excl["forfeit"] += 1
            continue
        w = hl.winner(mid)
        if w is None:
            excl["no HLTV result"] += 1
            continue
        y = 1 if w == ta else 0
        pm_w = market_winner_idx(m)
        if pm_w is not None and (pm_w == 0) != bool(y):
            excl["Polymarket resolution disagrees with HLTV winner"] += 1
            continue
        hist = price_history(f, toks[0], start)
        pt, why = prematch_point(hist, start)
        if why:
            excl[why] += 1
            continue
        obs.append({
            "source": "polymarket", "event_id": ev.get("id"), "market_id": m.get("id"),
            "question": m.get("question"), "how": how, "team_a": outs[0], "team_b": outs[1],
            "start": int(start), "price_t": pt["t"], "p": pt["p"], "y": y, "pm_resolved": pm_w is not None,
            "volume": float(m.get("volumeNum") or ev.get("volume") or 0), "hltv_match": mid,
            "hltv_a": ta, "hltv_b": tb, "date": hm["date"].isoformat(), "lan": hm["lan"], "event": hm["event"],
        })
    obs, dup = dedupe(obs)
    if dup:
        excl["duplicate market for the same HLTV match (kept highest volume)"] += dup
    return {"events": len(events), "more_events_exist": more,
            "oldest_event_end": min((str(e.get("endDate") or "") for e in events), default=None),
            "newest_event_end": max((str(e.get("endDate") or "") for e in events), default=None),
            "series_markets": dict(how_counts), "eligible": obs, "no_match_diagnostic": dict(near),
            "excluded": dict(excl), "unresolved_names": unresolved.most_common(40)}


def dedupe(obs):
    best = {}
    for o in obs:
        k = o["hltv_match"]
        if k not in best or o.get("volume", 0) > best[k].get("volume", 0):
            best[k] = o
    keep = sorted(best.values(), key=lambda o: (o["start"], o["hltv_match"]))
    return keep, len(obs) - len(keep)


def build_kalshi_inventory(f, hl, max_events=MAX_EVENTS, log=None):
    events, listed = fetch_kalshi_markets(f, max_events)
    excl = Counter({k: 0 for k in EXCLUSIONS})
    excl["Kalshi resolution disagrees with HLTV winner"] = 0
    excl["no two-sided quote (spread <= 10c) at cutoff"] = 0
    near = Counter()
    obs, unresolved = [], Counter()
    for i, (et, ms) in enumerate(events.items()):
        if log and i % 200 == 0:
            log(f"  kalshi {i}/{len(events)}  requests={f.requests} cache_hits={f.hits}")
        if len(ms) != 2:
            excl["event without exactly two team markets"] += 1
            continue
        a, b = ms
        if re.search(r"\b(?:tie|tied|draw|drawn)\b|\bBO2\b|\bbest[- ]of[- ](?:2|two)\b", str(a.get("rules_primary") or ""), re.I):
            excl[BO2_EXCLUSION] += 1
            continue
        start = kalshi_start(a.get("rules_primary"))
        if start is None:
            excl["no scheduled start"] += 1
            continue
        ids_a, ids_b = hl.team_ids(a.get("yes_sub_title")), hl.team_ids(b.get("yes_sub_title"))
        if not ids_a or not ids_b:
            excl["unresolved team mapping"] += 1
            for m, ids in ((a, ids_a), (b, ids_b)):
                if not ids:
                    unresolved[m.get("yes_sub_title")] += 1
            continue
        mid, ta, tb, why = hl.find_match(ids_a, ids_b, utc_date(start))
        if why:
            excl[why] += 1
            if why.startswith("no HLTV match"):
                near["pair has an HLTV match within +-7 days"] += hl.pair_near(ids_a, ids_b, utc_date(start))
                near["start on/after the newest HLTV date"] += utc_date(start) >= hl.last_date
            continue
        hm = hl.matches[mid]
        if hm["forfeited"]:
            excl["forfeit"] += 1
            continue
        w = hl.winner(mid)
        if w is None:
            excl["no HLTV result"] += 1
            continue
        y = 1 if w == ta else 0
        res = str(a.get("result") or "").lower()
        if res in ("yes", "no") and (res == "yes") != bool(y):
            excl["Kalshi resolution disagrees with HLTV winner"] += 1
            continue
        pt, why = kalshi_prematch(kalshi_candles(f, a, start), start)
        if why:
            excl[why] += 1
            continue
        vol = sum(float(m.get("volume_fp") or m.get("volume") or 0) for m in ms)
        obs.append({"source": "kalshi", "event_ticker": et, "market": a.get("ticker"),
                    "team_a": a.get("yes_sub_title"), "team_b": b.get("yes_sub_title"), "start": int(start),
                    "price_t": pt["t"], "p": pt["p"], "bid": pt["bid"], "ask": pt["ask"], "y": y,
                    "volume": vol, "hltv_match": mid, "hltv_a": ta, "hltv_b": tb, "date": hm["date"].isoformat(),
                    "lan": hm["lan"], "event": hm["event"]})
    obs, dup = dedupe(obs)
    if dup:
        excl["duplicate market for the same HLTV match (kept highest volume)"] += dup
    return {"events": len(events), "events_listed": listed, "more_events_exist": listed > len(events),
            "eligible": obs, "no_match_diagnostic": dict(near), "excluded": dict(excl), "unresolved_names": unresolved.most_common(40)}


# ---------------------------------------------------------------------------------------------------- scoring
def _rng(seed=SEED):
    import random
    return random.Random(seed)


def group_bootstrap(groups, stat, B=BOOT, seed=SEED, level=0.95):
    """Match-grouped bootstrap: resample groups (matches) with replacement. groups: list of row-lists.
    stat(rows) -> float. Returns (lo, hi) percentile interval at `level` (95% by default)."""
    if not groups:
        return (None, None)
    r = _rng(seed)
    n = len(groups)
    vals = []
    for _ in range(B):
        rows = []
        for _ in range(n):
            rows.extend(groups[r.randrange(n)])
        v = stat(rows)
        if v is not None:
            vals.append(v)
    if not vals:
        return (None, None)
    vals.sort()
    a = (1 - level) / 2
    return (round(vals[int(a * (len(vals) - 1))], 5), round(vals[int((1 - a) * (len(vals) - 1))], 5))


def _groups(rows, key="hltv_match"):
    g = defaultdict(list)
    for r in rows:
        g[r.get(key)].append(r)
    return list(g.values())


def metrics(rows, pkey="p", boot=True):
    """Log-loss / Brier of the market price vs a fixed 50%, with match-grouped bootstrap CIs."""
    n = len(rows)
    if not n:
        return {"n": 0}

    def ll(rs, k=pkey):
        return sum(logloss1(r[k], r["y"]) for r in rs) / len(rs) if rs else None

    def br(rs, k=pkey):
        return sum((r[k] - r["y"]) ** 2 for r in rs) / len(rs) if rs else None

    coin = sum(1 for r in rows if abs(r[pkey] - 0.5) < 1e-12)
    out = {"n": n, "matches": len({r.get("hltv_match") for r in rows}),
           "logloss": round(ll(rows), 5), "brier": round(br(rows), 5),
           "logloss_50": math.log(2), "brier_50": 0.25,
           # a 50/50 price has no favourite: counted apart, never as a team-A favourite
           "fav_won": sum(1 for r in rows if abs(r[pkey] - 0.5) >= 1e-12 and (r[pkey] > 0.5) == bool(r["y"])),
           "n_fav": n - coin, "coin_flips": coin,
           "mean_p": round(sum(r[pkey] for r in rows) / n, 4), "rate_a": round(sum(r["y"] for r in rows) / n, 4)}
    if boot:
        g = _groups(rows)
        out["logloss_ci"] = group_bootstrap(g, ll)
        out["brier_ci"] = group_bootstrap(g, br)
        out["logloss_gain_vs_50_ci"] = group_bootstrap(g, lambda rs: math.log(2) - ll(rs))
        out["brier_gain_vs_50_ci"] = group_bootstrap(g, lambda rs: 0.25 - br(rs))
    out["logloss_gain_vs_50"] = round(math.log(2) - out["logloss"], 5)
    out["brier_gain_vs_50"] = round(0.25 - out["brier"], 5)
    return out


def calibration(rows, pkey="p", bins=10):
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        rs = [r for r in rows if lo <= r[pkey] < hi or (b == bins - 1 and r[pkey] == 1)]
        k = sum(r["y"] for r in rs)
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(rs),
                    "mean_p": round(sum(r[pkey] for r in rs) / len(rs), 4) if rs else None,
                    "observed": round(k / len(rs), 4) if rs else None, "ci95": wilson(k, len(rs))})
    return out


def by_fav_range(rows, pkey="p"):
    out = []
    coin = [r for r in rows if abs(r[pkey] - 0.5) < 1e-12]
    rows = [r for r in rows if abs(r[pkey] - 0.5) >= 1e-12]       # a coin flip has no favourite (reported apart)
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)):
        rs = [r for r in rows if lo <= max(r[pkey], 1 - r[pkey]) < hi]
        k = sum(1 for r in rs if (r[pkey] > 0.5) == bool(r["y"]))
        m = metrics(rs, pkey, boot=True) if rs else {"n": 0}
        out.append({"fav_price": f"{lo:.1f}-{min(hi, 1):.1f}", "n": len(rs),
                    "fav_mean_p": round(sum(max(r[pkey], 1 - r[pkey]) for r in rs) / len(rs), 4) if rs else None,
                    "fav_won": k, "fav_win_rate": round(k / len(rs), 4) if rs else None, "ci95": wilson(k, len(rs)),
                    "logloss": m.get("logloss"), "logloss_ci": m.get("logloss_ci"), "brier": m.get("brier")})
    out.append({"fav_price": "coin-flip (p = 0.5)", "n": len(coin), "coin_flip": True, "team_a_won": sum(r["y"] for r in coin)})
    return out


def segments(rows, pkey="p", unit="$"):
    out = {"lan": {}, "tier": {}}
    for lab, cond in (("LAN", lambda r: r.get("lan") is True), ("online", lambda r: r.get("lan") is False),
                      ("unknown", lambda r: r.get("lan") is None)):
        rs = [r for r in rows if cond(r)]
        out["lan"][lab] = metrics(rs, pkey) if rs else {"n": 0}
    vols = sorted(r.get("volume", 0) for r in rows)
    if len(vols) >= 3:
        t1, t2 = vols[len(vols) // 3], vols[2 * len(vols) // 3]
        u = (lambda v: f"${v:,.0f}") if unit == "$" else (lambda v: f"{v:,.0f} {unit}")
        for lab, cond in ((f"low volume (< {u(t1)})", lambda r: r.get("volume", 0) < t1),
                          (f"mid volume ({u(t1)} - {u(t2)})", lambda r: t1 <= r.get("volume", 0) < t2),
                          (f"high volume (>= {u(t2)})", lambda r: r.get("volume", 0) >= t2)):
            rs = [r for r in rows if cond(r)]
            out["tier"][lab] = metrics(rs, pkey) if rs else {"n": 0}
    return out


def score_baseline(rows, pkey="p", unit="$"):
    return {"overall": metrics(rows, pkey), "calibration": calibration(rows, pkey),
            "by_favourite_price": by_fav_range(rows, pkey), **segments(rows, pkey, unit)}


# ---------------------------------------------------------------------------------------------------- splits + features
def chrono_split(rows, frac=SPLIT):
    """Oldest 60% / next 20% / newest 20% by match start; every observation of a match in one split."""
    by_match = defaultdict(list)
    for r in rows:
        by_match[r["hltv_match"]].append(r)
    order = sorted(by_match, key=lambda m: (min(r["start"] for r in by_match[m]), m))
    n = len(order)
    a, b = int(round(frac[0] * n)), int(round((frac[0] + frac[1]) * n))
    parts = (order[:a], order[a:b], order[b:])
    return tuple([r for m in part for r in by_match[m]] for part in parts)


def fit_b(rows, xkey, lam, base="base_logit"):
    """argmin_b sum logloss(sigmoid(base + b*x)) + lam*b^2 (Newton, 1-D, convex)."""
    b = 0.0
    for _ in range(50):
        g, h = 2 * lam * b, 2 * lam
        for r in rows:
            x = r[xkey]
            p = sigmoid(r[base] + b * x)
            g += (p - r["y"]) * x
            h += p * (1 - p) * x * x
        if h <= 0:
            break
        step = g / h
        b -= step
        if abs(step) < 1e-10:
            break
    return b


def mean_ll(rows, b, xkey, base="base_logit"):
    return sum(logloss1(sigmoid(r[base] + b * r[xkey]), r["y"]) for r in rows) / len(rows)


def _cv_folds(dev, k=CV_FOLDS):
    """Expanding-window folds inside development, by match start (every row of a match in one chunk):
    [(train rows, held-out rows)] - fold i trains on chunks 0..i-1 and scores chunk i."""
    by = defaultdict(list)
    for r in dev:
        by[r.get("hltv_match")].append(r)
    order = sorted(by, key=lambda m: (min(r["start"] for r in by[m]), str(m)))
    if len(order) < k + 1:
        return []
    cuts = [round(i * len(order) / (k + 1)) for i in range(k + 2)]
    chunks = [[r for m in order[cuts[i]:cuts[i + 1]] for r in by[m]] for i in range(k + 1)]
    return [([r for c in chunks[:i] for r in c], chunks[i]) for i in range(1, k + 1) if chunks[i]]


def choose_lambda(dev, xkey, base="base_logit"):
    """The L2 strength with the lowest expanding-window CV log-loss inside development (ties -> the stronger penalty).
    Validation is never looked at here."""
    folds = _cv_folds(dev)
    scores = []
    for lam in LAMBDAS:
        tot = n = 0
        for train, held in folds:
            b = fit_b(train, xkey, lam, base)
            tot += mean_ll(held, b, xkey, base) * len(held)
            n += len(held)
        scores.append({"lambda": lam, "cv_logloss": round(tot / n, 5) if n else None})
    if not folds:
        return max(LAMBDAS), scores            # too few matches to cross-validate: the strongest penalty
    best = min(scores, key=lambda g: (g["cv_logloss"], -g["lambda"]))
    return best["lambda"], scores


def test_feature(dev, val, xkey, base="base_logit"):
    """L2 strength by time-ordered CV inside dev, b fitted on all of dev with it, then the validation gain and its
    match-grouped bootstrap CI (validation decides promotion only - it never chose the strength)."""
    xs = [r[xkey] for r in dev]
    sd = math.sqrt(sum(x * x for x in xs) / len(xs)) if xs else 0.0
    if not sd:
        return {"status": "feature is zero everywhere on development"}
    for r in dev + val:
        r["_x"] = r[xkey] / sd
    lam, cv = choose_lambda(dev, "_x", base)
    grid = []
    for g in cv:
        bb = fit_b(dev, "_x", g["lambda"], base)
        grid.append({"lambda": g["lambda"], "b": round(bb, 5), "cv_logloss": g["cv_logloss"],
                     "dev_logloss": round(mean_ll(dev, bb, "_x", base), 5)})
    best = next(g for g in grid if g["lambda"] == lam)
    b = best["b"]
    best["val_logloss"] = round(mean_ll(val, b, "_x", base), 5)
    base_ll = sum(logloss1(sigmoid(r[base]), r["y"]) for r in val) / len(val)
    gain = base_ll - best["val_logloss"]

    def stat(rs):
        return sum(logloss1(sigmoid(r[base]), r["y"]) - logloss1(sigmoid(r[base] + b * r["_x"]), r["y"])
                   for r in rs) / len(rs)
    ci = group_bootstrap(_groups(val), stat)
    for r in dev + val:
        r[xkey + "_p"] = sigmoid(r[base] + b * r["_x"])
    promoted = ci[0] is not None and ci[0] > 0
    return {"status": "tested", "x_scale_sd": round(sd, 5), "grid": grid, "chosen_lambda": best["lambda"],
            "lambda_chosen_by": f"expanding-window CV inside development ({CV_FOLDS} folds)",
            "b_per_sd": b, "val_base_logloss": round(base_ll, 5), "val_model_logloss": best["val_logloss"],
            "val_gain": round(gain, 5), "val_gain_ci": ci, "promoted": promoted,
            "dev_n": len(dev), "val_n": len(val),
            "x_nonzero_dev": sum(1 for r in dev if r[xkey]), "x_nonzero_val": sum(1 for r in val if r[xkey])}


def edge_returns(rows, pkey_model, pkey_mkt="p", thresholds=EDGE_THRESHOLDS, cost=COST):
    """Hypothetical returns at RECORDED price-history prices (+ cost) when model - market >= threshold.
    1 unit staked per bet; never executable fills."""
    out = []
    for th in thresholds:
        bets = []
        for r in rows:
            pm, pmk = r[pkey_model], r[pkey_mkt]
            for side, edge, price, won in ((0, pm - pmk, pmk, r["y"] == 1), (1, pmk - pm, 1 - pmk, r["y"] == 0)):
                if edge * 100 >= th - 1e-9:
                    c = price + cost
                    if c < 1:
                        bets.append({"hltv_match": r["hltv_match"], "profit": (1 / c if won else 0) - 1, "won": won,
                                     "price": price})
        n = len(bets)
        prof = sum(b["profit"] for b in bets)
        ci = (group_bootstrap(_groups(bets), lambda bs: sum(b["profit"] for b in bs) / len(bs))
              if n >= MIN_BETS_CI else (None, None))          # under MIN_BETS_CI bets the report prints n/a
        out.append({"threshold_pts": th, "bets": n, "won": sum(b["won"] for b in bets),
                    "avg_price": round(sum(b["price"] for b in bets) / n, 4) if n else None,
                    "units_staked": n, "net_units": round(prof, 3), "roi": round(prof / n, 4) if n else None,
                    "roi_ci95": ci})
    return out


FEATURES = {
    "a_rating": "Shrunk HLTV rating differential of expected starting rosters",
    "b_mappool": "Map-pool win rate (top-4 maps, 180 d, shrunk) differential",
    "c_form": "Recent series form (last 10 series, shrunk) differential",
    "d_lan_x_rating": "Feature (a) x LAN flag (tested only if (a) is promoted)",
    "e_h2h": "Roster-aware head-to-head (365 d, both teams kept >= 3 of today's 5), shrunk",
}


def add_features(rows, hl):
    miss = Counter()
    for r in rows:
        # HLTV rows carry a date, no time: when HLTV dates the match a day after the UTC start, rows dated on the UTC
        # start day may be series played after the price cutoff - so the cutoff day is the EARLIER of the two dates
        d = date.fromisoformat(r["date"])
        if r.get("start") is not None:
            d = min(d, utc_date(r["start"]))
        r["feature_day"] = d.isoformat()
        ta, tb = r["hltv_a"], r["hltv_b"]
        r["base_logit"] = logit(r["p"])
        a = hl.f_rating(ta, tb, d)
        miss["a_rating"] += a is None
        r["a_rating"] = a or 0.0
        bm = hl.f_mappool(ta, tb, d)
        miss["b_mappool"] += bm is None
        r["b_mappool"] = bm or 0.0
        c = hl.f_form(ta, tb, d)
        miss["c_form"] += c is None
        r["c_form"] = c or 0.0
        e, n = hl.f_h2h(ta, tb, d)
        miss["e_h2h"] += n == 0
        r["e_h2h"] = e
        r["d_lan_x_rating"] = r["a_rating"] * (1.0 if r.get("lan") is True else 0.0)
    return dict(miss)


class FreezeError(RuntimeError):
    """The freeze discipline refused this run (see staged_tests)."""


def _freeze_core(dev, val, test, features, promoted):
    """What the freeze pins (and hashes): the exact dev / val / test match ids, the promoted features, their
    coefficients and the code tag. Nothing time-dependent - a --final run recomputes it and must get the same sha."""
    def ids(rs):
        first = {}
        for r in rs:
            first[r["hltv_match"]] = min(first.get(r["hltv_match"], r["start"]), r["start"])
        return sorted(first, key=lambda m: (first[m], str(m)))
    return {"version": CODE_TAG,
            "split": {"development": ids(dev), "validation": ids(val), "test": ids(test)},
            "promoted": sorted(promoted),
            "coefficients": {k: {"b_per_sd": features[k]["b_per_sd"], "x_scale_sd": features[k]["x_scale_sd"]}
                             for k in sorted(promoted)}}


def _sha(core):
    return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


def _scored_path(freeze_path):
    return str(freeze_path) + ".scored.json"


def staged_tests(rows, hl, freeze_path=None, final=False, refreeze=False, rescore=False):
    """Chronological split, one-feature-at-a-time tests on dev/val, freeze, then test-set scoring of the baseline and
    promoted features only.

    Freeze discipline:
      non-final  writes the freeze (pinned dev/val/test match ids, promoted, coefficients, CODE_TAG, sha256) to
                 freeze_path - only when none exists; an existing freeze is replaced only with refreeze=True, and the
                 replacement records it (refrozen, previous sha).
      final      needs that freeze (FreezeError otherwise), rebuilds the split from the PINNED match ids (not today's
                 alias-dependent eligibility), recomputes promoted + coefficients and aborts unless the sha matches.
                 The freeze is never rewritten. Each scoring is appended to <freeze_path>.scored.json; a second
                 --final is refused unless rescore=True, and the result says how many times the test set was scored.
    Every row gets r["split"] (development / validation / test)."""
    missing = add_features(rows, hl)
    fz = None
    dropped = 0
    if final:
        if not freeze_path or not os.path.exists(freeze_path):
            raise FreezeError("--final needs the freeze written by an earlier non-final run (none found at "
                              f"{freeze_path}) - run without --final first")
        with open(freeze_path, encoding="utf-8") as fh:
            fz = json.load(fh)
        if _sha(fz.get("core")) != fz.get("sha256"):
            raise FreezeError("the freeze file does not match its own sha256 - refusing to score the test set")
        pinned = fz["core"]["split"]
        by = defaultdict(list)
        for r in rows:
            by[r["hltv_match"]].append(r)
        want = {m for part in pinned.values() for m in part}
        lost = [m for m in want if m not in by]
        if lost:
            raise FreezeError(f"{len(lost)} pinned matches are no longer eligible (e.g. {lost[:5]}) - the split "
                              "can't be rebuilt from the freeze; refreeze (non-final run with --refreeze) first")
        dropped = sum(len(v) for m, v in by.items() if m not in want)
        dev, val, test = ([r for m in pinned[k] for r in by[m]] for k in ("development", "validation", "test"))
    else:
        if freeze_path and os.path.exists(freeze_path) and not refreeze:
            raise FreezeError(f"a freeze already exists at {freeze_path} - a new one would move the frozen split; pass "
                              "--refreeze to replace it (recorded in the freeze and the results)")
        dev, val, test = chrono_split(rows)
    for part, lab in ((dev, "development"), (val, "validation"), (test, "test")):
        for r in part:
            r["split"] = lab
    rng = lambda rs: {"n": len(rs), "from": min(r["start"] for r in rs) if rs else None,
                      "to": max(r["start"] for r in rs) if rs else None}
    out = {"split": {"development": rng(dev), "validation": rng(val), "test": rng(test)},
           "features_missing_as_zero": missing, "features": {}}
    if dropped:
        out["rows_outside_the_frozen_split"] = dropped
    for fk in ("a_rating", "b_mappool", "c_form", "e_h2h"):
        out["features"][fk] = {"label": FEATURES[fk], **test_feature(dev, val, fk)}
        if out["features"][fk].get("status") == "tested":
            out["features"][fk]["val_returns"] = edge_returns(val, fk + "_p")
    a = out["features"]["a_rating"]
    if a.get("promoted"):
        for r in dev + val + test:
            r["base_a"] = r["base_logit"] + a["b_per_sd"] * r["a_rating"] / a["x_scale_sd"]
        res = test_feature(dev, val, "d_lan_x_rating", base="base_a")
        res["note"] = "tested on top of the promoted (a) correction"
        out["features"]["d_lan_x_rating"] = {"label": FEATURES["d_lan_x_rating"], **res}
    else:
        out["features"]["d_lan_x_rating"] = {"label": FEATURES["d_lan_x_rating"],
                                             "status": "not tested - (a) was not promoted"}
    out["development_baseline"] = metrics(dev)
    out["validation_baseline"] = metrics(val)
    promoted = [k for k, v in out["features"].items() if v.get("promoted")]
    core = _freeze_core(dev, val, test, out["features"], promoted)
    sha = _sha(core)
    if final:
        if sha != fz["sha256"]:
            raise FreezeError("the recomputed split / promoted features / coefficients differ from the freeze "
                              f"({sha[:12]} vs frozen {fz['sha256'][:12]}) - refusing to score the test set")
        out["freeze"] = {"sha256": sha, "promoted": promoted, "frozen_at": fz.get("frozen_at"), "test_n": len(test),
                         "refrozen": bool(fz.get("refrozen")), "verified_at_final": True}
    else:
        frozen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        prev = None
        if freeze_path and os.path.exists(freeze_path):
            try:
                with open(freeze_path, encoding="utf-8") as fh:
                    prev = json.load(fh).get("sha256")
            except (OSError, ValueError):
                prev = "unreadable"
        rec = {"core": core, "sha256": sha, "frozen_at": frozen_at, "refrozen": prev is not None, "previous_sha256": prev}
        if freeze_path:
            with open(freeze_path, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, sort_keys=True)
        out["freeze"] = {"sha256": sha, "promoted": promoted, "frozen_at": frozen_at, "test_n": len(test),
                         "refrozen": prev is not None, "previous_sha256": prev, "written": bool(freeze_path)}
    if not final:
        out["test"] = None                 # development/validation only; rerun with --final to score the test set
        for r in rows:
            r.pop("_x", None)
        return out
    # ---- test set: scored after the freeze was verified; the scoring itself is recorded before any number exists
    sp = _scored_path(freeze_path)
    log = []
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as fh:
            log = json.load(fh)
        if log and not rescore:
            raise FreezeError(f"the test set was already scored {len(log)} time(s) (first {log[0].get('test_scored_at')}) "
                              "- pass --rescore-test to score it again (the report then says it is a rescore)")
    log.append({"test_scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sha256": sha,
                "rescore": bool(log)})
    with open(sp, "w", encoding="utf-8") as fh:
        json.dump(log, fh)
    out["test"] = {"baseline": metrics(test), "scored_count": len(log), "rescore": len(log) > 1,
                   "scored_at": log[-1]["test_scored_at"]}
    for k in promoted:
        v = out["features"][k]
        base = "base_a" if k == "d_lan_x_rating" else "base_logit"
        for r in test:
            r[k + "_p"] = sigmoid(r[base] + v["b_per_sd"] * r[k] / v["x_scale_sd"])
        trs = [dict(r, p=r[k + "_p"]) for r in test]
        out["test"][k] = metrics(trs)
        out["test"][k]["gain_vs_market_ci"] = group_bootstrap(
            _groups(test), lambda rs: sum(logloss1(sigmoid(r[base]), r["y"]) - logloss1(r[k + "_p"], r["y"])
                                          for r in rs) / len(rs))
    for r in rows:
        r.pop("_x", None)
    return out


# ---------------------------------------------------------------------------------------------------- forward cohort
_COLS = {
    "team_a": ("team_a", "team1", "home", "home_team"),
    "team_b": ("team_b", "team2", "away", "away_team"),
    "p": ("prob_a", "p_a", "team_a_prob", "price_a", "mid_a"),
    "start": ("start_ts", "start", "start_time", "game_start", "scheduled_start", "match_start"),
    "at": ("captured_at", "logged_ts", "snapshot_at", "taken_at", "fetched_at", "received_ts"),
    "source": ("source", "venue", "provider"),
    "scope": ("scope", "market_scope"),
    "usable": ("usable", "available"),
    "match_id": ("match_id",),
    "model_version": ("model_version",),
    "bo": ("bo", "best_of"),
    "side": ("team_side",),
}
FORWARD_EXCLUSIONS = ("unavailable at cutoff", f"stale snapshot (> {MAX_AGE_H} h before cutoff)", BO2_EXCLUSION)


def _pick(cols, names):
    low = {c.lower(): c for c in cols}
    return next((low[n] for n in names if n in low), None)


def _ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e11 else 1)
    try:
        return float(v)
    except ValueError:
        return parse_ts(v)


def _forward_tables(con):
    out = []
    for t in [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
        cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
        c = {k: _pick(cols, v) for k, v in _COLS.items()}
        if all(c[k] for k in ("team_a", "team_b", "p", "start", "at")):
            out.append((t, c))
    return out


def _earliest_starts(con, tables=None):
    """{match_id: earliest start (epoch s) any forward table states} - the ONE 'earliest stated start' both forward_rows
    and asks_at_cutoff cut at (another source's earlier start counts, e.g. ml_forecasts' HLTV start)."""
    earliest = {}
    for t, c in (_forward_tables(con) if tables is None else tables):
        if c["match_id"]:
            for mid, st in con.execute(f'SELECT "{c["match_id"]}", MIN("{c["start"]}") FROM "{t}" '
                                       f'WHERE "{c["match_id"]}" IS NOT NULL GROUP BY "{c["match_id"]}"'):
                st = _ts(st)
                if st is not None:
                    earliest[mid] = min(earliest.get(mid, st), st)
    return earliest


def earliest_starts(db_path):
    """_earliest_starts for a snapshot DB path ({} when it cannot be read)."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return {}
    try:
        return _earliest_starts(con)
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def forward_rows(db_path, excluded=None):
    """Per (cohort, match): the LATEST snapshot at or before the cutoff (start - CUTOFF_MIN), chosen BEFORE looking at
    its availability - so a baseline that became unavailable before the cutoff is never replaced by an older available
    one. The cutoff uses the earliest start any row of the same match_id states (another source's earlier start counts).
    That snapshot is scored only when it was available (usable / available and 0 < p < 1) and is no older than
    MAX_AGE_H before the cutoff - the same bound as the historical evaluation; otherwise the match is a counted
    exclusion in `excluded` (a Counter / dict: "<cohort>: <reason>"), and best-of-2 matches are excluded explicitly.

    moneyline.py writes CS2_DATA_DIR/.cache/ml_snapshots.db:
      ml_forecasts  the frozen baseline per linked match (model_version, prob_a, available, logged_ts) ->
                    cohort "baseline <model_version>"
      ml_quotes     every changed quote (+ a 10-min heartbeat); team A's own row of a linked match carries prob_a ->
                    one cohort per platform (team B's Kalshi YES rows and unlinked rows are not probability rows)
    Any other table with team A / team B / probability-of-A / start / capture-time columns is read the same way."""
    if not db_path or not os.path.exists(db_path):
        return None, f"{db_path} not found (the live collector has not written snapshots here yet)"
    excl = Counter() if excluded is None else excluded
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        tables = _forward_tables(con)
        if not tables:
            return None, f"no table in {db_path} has team/probability/start/capture-time columns"
        earliest = _earliest_starts(con, tables)
        best = {}
        order = ("team_a", "team_b", "p", "start", "at", "source", "scope", "usable", "match_id", "model_version", "bo", "side")
        for t, c in tables:
            sel = [f'"{c[k]}"' if c[k] else "NULL" for k in order]
            for ta, tb, p, st, at, src, scope, ok, mid, mv, bo, side in con.execute(f'SELECT {", ".join(sel)} FROM "{t}"'):
                st, at = _ts(st), _ts(at)
                if None in (ta, tb, st, at):
                    continue
                if scope is not None and str(scope).lower() not in ("series", "match", "game", "moneyline"):
                    continue                                       # map-winner rows are never series rows
                if str(side or "").lower() == "b" or (c["match_id"] and not mid):
                    continue                                       # team B's own contract / an unlinked quote row
                if p is None and (ok is None or ok):
                    continue                                       # not a probability-of-A row at all
                st = min(st, earliest.get(mid, st)) if mid else st
                cutoff = st - CUTOFF_MIN * 60
                if at > cutoff:
                    continue
                cohort = ((f"baseline {mv}" if str(mv).startswith("ml-baseline") else f"model {mv}") if mv
                          else str(src or t))
                key = (cohort, mid or (_norm(ta), _norm(tb), int(st)))
                if key not in best or at > best[key]["at"]:
                    try:
                        pf = float(p) if p is not None else None
                    except (TypeError, ValueError):
                        pf = None
                    best[key] = {"source": cohort, "team_a": ta, "team_b": tb, "p": pf, "start": int(st), "at": at,
                                 "match_id": mid, "ok": (ok is None or bool(ok)) and pf is not None and 0 < pf < 1,
                                 "bo": bo, "cutoff": cutoff}
        out = []
        for r in best.values():
            why = None
            try:
                bo2 = r["bo"] is not None and int(r["bo"]) == 2
            except (TypeError, ValueError):
                bo2 = False
            if bo2:
                why = BO2_EXCLUSION
            elif not r["ok"]:
                why = "unavailable at cutoff"
            elif r["at"] < r["cutoff"] - MAX_AGE_H * 3600:
                why = f"stale snapshot (> {MAX_AGE_H} h before cutoff)"
            if why:
                excl[f"{r['source']}: {why}"] += 1
                continue
            out.append({k: r[k] for k in ("source", "team_a", "team_b", "p", "start", "at", "match_id")})
        return out, None
    finally:
        con.close()


def forward_span(db_path):
    """(oldest start of a scorable forward row, [HLTV match ids those rows link to]) - what the server's light HLTV copy
    must cover so no forward match falls out of the loaded window."""
    rows, _ = forward_rows(db_path)
    if not rows:
        return None, []
    ids = sorted({int(str(r["match_id"])[5:]) for r in rows
                  if str(r.get("match_id") or "").startswith("hltv:") and str(r["match_id"])[5:].isdigit()})
    return min(r["start"] for r in rows), ids


def _forward_match(hl, r):
    """(hltv matchid, HLTV id of team A, reason). A 'hltv:<id>' canonical match id is used directly (team A
    oriented by exact name); otherwise the same two teams within +-1 day. A match dated before the loaded HLTV window
    (load_hltv_recent) is 'outside loaded HLTV window', never 'no HLTV match'."""
    ids_a, ids_b = hl.team_ids(r["team_a"]), hl.team_ids(r["team_b"])
    mid = str(r.get("match_id") or "")
    first = getattr(hl, "first_date", None)
    outside = bool(first and utc_date(r["start"]) + timedelta(days=1) < first)
    if mid.startswith("hltv:") and mid[5:].isdigit() and int(mid[5:]) in hl.matches:
        m = int(mid[5:])
        teams = hl.matches[m]["teams"]
        a = (ids_a & teams) or ((teams - ids_b) if ids_b & teams else set())
        if len(a) == 1:
            return m, next(iter(a)), None
        return None, None, "team A not identifiable in the linked HLTV match"
    if outside:
        return None, None, "outside loaded HLTV window"
    if not ids_a or not ids_b:
        return None, None, "unresolved team mapping"
    m, ta, tb, why = hl.find_match(ids_a, ids_b, utc_date(r["start"]))
    return m, ta, why


def forward_eval(db_path, hl):
    excl = Counter()
    rows, why = forward_rows(db_path, excl)
    if rows is None:
        return {"status": why, "n": 0}
    out_rows = []
    for r in rows:
        mid, ta, why = _forward_match(hl, r)
        if why:
            excl[f"{r['source']}: {why}"] += 1
            continue
        if hl.matches[mid]["forfeited"]:
            excl[f"{r['source']}: forfeit"] += 1
            continue
        win = hl.winner(mid)
        if win is None:
            excl[f"{r['source']}: no HLTV result yet"] += 1
            continue
        out_rows.append(dict(r, hltv_match=mid, y=1 if win == ta else 0, lan=hl.matches[mid]["lan"], volume=0))
    res = {"status": "ok", "snapshots_usable": len(rows), "excluded": dict(excl), "by_source": {},
           "hltv_window_from": hl.first_date.isoformat() if getattr(hl, "first_date", None) else None}
    for src in sorted({r["source"] for r in out_rows}):
        rs = [r for r in out_rows if r["source"] == src]
        res["by_source"][src] = {"overall": metrics(rs), "calibration": calibration(rs)}
    res["n"] = len(out_rows)
    res["candidate"] = forward_compare(out_rows, since_ts=candidate_frozen_ts(),
                                       reviews_path=os.path.join(os.path.dirname(os.path.abspath(db_path)), "ml_reviews.json"),
                                       asks=asks_at_cutoff(db_path))
    return res


def asks_at_cutoff(db_path):
    """{match_id: {ask_a, ask_a_platform, ask_b, ask_b_platform}}: per side the lowest recorded ASK across platforms in
    the last usable quote at or before the cutoff (earliest stated start - CUTOFF_MIN), no older than MAX_AGE_H before
    it. The prices a member could actually have paid - REVIEW_PLAN's return check uses only these.
    The earliest stated start is forward_rows' own (_earliest_starts over every table, incl. ml_forecasts' HLTV start)
    combined with the a/b rows' own starts, so the ask and the forecast it pairs with are cut at the same moment
    (bug sweep 2026-11-06 #21)."""
    out = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return out
    # bug sweep 2026-11-06 #2: two streamed cursor passes, never .fetchall() - this runs inside the web process that
    # serves members, and ~1M quote rows were ~480 MB as a list. Memory is bounded by `earliest` (one entry per match)
    # and `last` (one per match x platform x side). One read transaction so both passes see the same rows.
    where = "FROM ml_quotes WHERE match_id IS NOT NULL AND team_side IN ('a', 'b')"
    earliest, last = {}, {}
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='ml_quotes'").fetchone():
            return out
        con.execute("BEGIN")
        seen = set()
        for mid, st in con.execute("SELECT DISTINCT match_id, start " + where):
            seen.add(mid)
            st = _ts(st)
            if st is not None:
                earliest[mid] = min(earliest.get(mid, st), st)
        # bug sweep 2026-11-06 #21: the same earliest stated start forward_rows cuts at (every forward table)
        fwd = _earliest_starts(con)
        for mid in seen:
            if mid in fwd:
                earliest[mid] = min(earliest.get(mid, fwd[mid]), fwd[mid])
        del seen, fwd
        for mid, plat, side, ask, rec, ok in con.execute(
                "SELECT match_id, platform, team_side, ask, received_ts, usable " + where):
            rec = _ts(rec)
            if mid not in earliest or rec is None:
                continue
            cutoff = earliest[mid] - CUTOFF_MIN * 60
            if rec > cutoff or rec < cutoff - MAX_AGE_H * 3600:
                continue
            k = (mid, plat, side)
            if k not in last or rec > last[k][0]:
                last[k] = (rec, ask if ok and ask is not None and 0 < ask < 1 else None)
    finally:
        con.close()
    for (mid, plat, side), (_rec, ask) in last.items():
        if ask is None:
            continue
        d = out.setdefault(mid, {})
        if d.get(f"ask_{side}") is None or ask < d[f"ask_{side}"]:
            d[f"ask_{side}"], d[f"ask_{side}_platform"] = ask, plat
    return out


def candidate_frozen_ts(path=None):
    """When the candidate was frozen (ml_model_frozen.json payload.frozen_at), or None."""
    p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_model_frozen.json")
    try:
        with open(p, encoding="utf-8") as fh:
            return parse_ts(json.load(fh)["payload"]["frozen_at"])
    except Exception:
        return None


def calib_slope(rows, pkey="p"):
    """(intercept, slope) of the logistic regression of y on logit(p) - pure Python Newton steps."""
    xs = [logit(min(max(r[pkey], 1e-6), 1 - 1e-6)) for r in rows]
    ys = [r["y"] for r in rows]
    a, b = 0.0, 1.0
    for _ in range(50):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            q = sigmoid(a + b * x)
            w = q * (1 - q)
            g0 += q - y
            g1 += (q - y) * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        h00 += 1e-9
        h11 += 1e-9
        det = h00 * h11 - h01 * h01
        if det <= 0:
            return None, None
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a, b = a - da, b - db
        if max(abs(da), abs(db)) < 1e-10:
            break
    return round(a, 4), round(b, 4)


def forward_compare(out_rows, rule=None, since_ts=None, boot=BOOT, reviews_path=None, asks=None):
    """The frozen candidate vs the frozen baseline on the SAME forward matches (each side's forecast = its last row at or
    before start - 30 min, from forward_rows), and the pre-registered promotion decision. Matches starting before the
    freeze (since_ts) are not counted. Unpaired matches are counted, never scored."""
    rule = rule or PROMOTION_RULE
    cand, base = f"model {rule['model_version']}", f"baseline {rule['baseline_version']}"
    by = defaultdict(dict)
    for r in out_rows:
        if r["source"] in (cand, base) and (since_ts is None or r["start"] >= since_ts):
            by[r["hltv_match"]][r["source"]] = r
    pairs, only = [], Counter()
    for mid, d in by.items():
        if cand in d and base in d:
            pairs.append(dict({"hltv_match": mid, "y": d[base]["y"], "p": d[cand]["p"], "pb": d[base]["p"],
                               "start": d[base]["start"]}, **((asks or {}).get(d[base].get("match_id")) or {})))
        else:
            only[f"only {next(iter(d))}"] += 1
    out = {"rule": rule, "model": cand, "baseline": base, "n": len(pairs), "unpaired": dict(only),
           "since": datetime.fromtimestamp(since_ts, timezone.utc).isoformat(timespec="seconds") if since_ts else None}
    if not pairs:
        out.update(promote=False, decision=f"not measurable yet: 0 of {rule['min_forward_matches']} forward matches",
                   review=review_progress([], reviews_path))
        return out

    def ll(rs, k):
        return sum(logloss1(r[k], r["y"]) for r in rs) / len(rs)

    def gain(rs):
        return ll(rs, "pb") - ll(rs, "p")

    g = _groups(pairs)
    out.update(ll_model=round(ll(pairs, "p"), 5), ll_baseline=round(ll(pairs, "pb"), 5), gain=round(gain(pairs), 5),
               gain_ci=list(group_bootstrap(g, gain, B=boot)),
               brier_model=round(sum((r["p"] - r["y"]) ** 2 for r in pairs) / len(pairs), 5),
               brier_baseline=round(sum((r["pb"] - r["y"]) ** 2 for r in pairs) / len(pairs), 5))
    out["calibration_intercept"], out["calibration_slope"] = calib_slope(pairs)
    out["calibration_slope_baseline"] = calib_slope(pairs, "pb")[1]
    months = defaultdict(list)
    for r in pairs:
        months[datetime.fromtimestamp(r["start"], timezone.utc).strftime("%Y-%m")].append(r)
    out["by_month"] = {m: {"n": len(rs), "gain": round(gain(rs), 5)} for m, rs in sorted(months.items())}
    lo = out["gain_ci"][0]
    slope = out["calibration_slope"]
    lo_s, hi_s = rule["calibration_slope_range"]
    checks = {"n_ge_min": len(pairs) >= rule["min_forward_matches"],
              "gain_positive_ci_excludes_0": out["gain"] > 0 and lo is not None and lo > 0,
              "calibration_slope_in_range": slope is not None and lo_s <= slope <= hi_s}
    out["checks"] = checks
    out["promote"] = all(checks.values())
    out["decision"] = ("PROMOTE: all pre-registered checks pass" if out["promote"] else
                       "keep in shadow (" + ", ".join(k for k, v in checks.items() if not v) + " not met) - "
                       "probability only as 'candidate - not validated', no stake")
    if out["promote"]:
        out["decision"] = "checkpoint met - a scheduled REVIEW_PLAN look decides; never an automatic stake"
    out["review"] = review_progress(pairs, reviews_path, boot=boot)
    return out


def _kalshi_fee(p):
    return 0.07 * p * (1 - p)


def ask_returns(pairs, edge=None, level=0.95, boot=BOOT):
    """REVIEW_PLAN return check: 1 unit on the side whose model probability beats that side's recorded ASK at the cutoff
    by >= edge, paid at ask + fee (Kalshi 0.07 p (1-p) per contract, Polymarket 0). Pairs carry ask_a / ask_b and
    ask_a_platform / ask_b_platform (None = no ask at the cutoff -> no bet, counted)."""
    edge = REVIEW_PLAN["edge_vs_ask"] if edge is None else edge
    bets, no_ask = [], 0
    for r in pairs:
        aa, ab = r.get("ask_a"), r.get("ask_b")
        if aa is None and ab is None:
            no_ask += 1
            continue
        pick = None
        if aa is not None and 0 < aa < 1 and r["p"] - aa >= edge:
            pick = (aa, r.get("ask_a_platform"), r["y"] == 1)
        elif ab is not None and 0 < ab < 1 and (1 - r["p"]) - ab >= edge:
            pick = (ab, r.get("ask_b_platform"), r["y"] == 0)
        if pick is None:
            continue
        price, plat, won = pick
        cost = price + (_kalshi_fee(price) if plat == "kalshi" else 0.0)
        bets.append({"hltv_match": r["hltv_match"], "net": (1 - cost) / cost if won else -1.0, "won": won})
    out = {"bets": len(bets), "no_ask": no_ask, "won": sum(1 for b in bets if b["won"])}
    if bets:
        roi = lambda rs: sum(b["net"] for b in rs) / len(rs)
        out.update(net_units=round(sum(b["net"] for b in bets), 3), roi=round(roi(bets), 4),
                   roi_ci=list(group_bootstrap(_groups(bets), roi, B=boot, level=level)), level=level)
    return out


def scheduled_review(pairs, look, plan=None, boot=BOOT):
    """One REVIEW_PLAN look on `pairs` (the first looks[look-1] paired forward matches by start). Returns every check,
    'eligible_for_owner_review' (never a stake by itself) and 'futility' (look 2, gain <= 0 ends the candidate)."""
    plan = plan or REVIEW_PLAN
    level = plan["ci_levels"][look - 1]
    g = _groups(pairs)
    ll = lambda rs, k: sum(logloss1(r[k], r["y"]) for r in rs) / len(rs)
    gain = lambda rs: ll(rs, "pb") - ll(rs, "p")
    gp = round(gain(pairs), 5)
    gci = list(group_bootstrap(g, gain, B=boot, level=level))
    slope = calib_slope(pairs)[1]
    # the slope interval refits a logistic regression per resample: 400 resamples keep a 1,000-match look under a minute
    s_ci = list(group_bootstrap(g, lambda rs: calib_slope(rs)[1], B=min(boot, 400)))
    citl = lambda rs: sum(r["y"] for r in rs) / len(rs) - sum(r["p"] for r in rs) / len(rs)
    c_ci = list(group_bootstrap(g, citl, B=boot))
    bands, testable, bands_ok = [], 0, True
    for lo, hi in plan["bands"]:
        rs = [r for r in pairs if lo <= max(r["p"], 1 - r["p"]) < hi]
        b = {"band": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(rs)}
        if rs:
            pred = sum(max(r["p"], 1 - r["p"]) for r in rs) / len(rs)
            obs = sum(1 for r in rs if (r["p"] >= 0.5) == (r["y"] == 1)) / len(rs)
            se = math.sqrt(max(obs * (1 - obs), 1e-9) / len(rs))
            b.update(predicted=round(pred, 4), observed=round(obs, 4), gap=round(obs - pred, 4))
            if len(rs) >= plan["band_min_n"]:
                testable += 1
                b["ok"] = abs(obs - pred) - 1.96 * se <= plan["band_tolerance"]
                bands_ok = bands_ok and b["ok"]
        bands.append(b)
    weeks = Counter(datetime.fromtimestamp(r["start"], timezone.utc).strftime("%G-W%V") for r in pairs)
    ret = ask_returns(pairs, level=level, boot=boot)
    checks = {
        "gain_ci_above_0": gci[0] is not None and gci[0] > 0,
        "slope_point_in_range": slope is not None and plan["slope_point"][0] <= slope <= plan["slope_point"][1],
        "slope_ci_inside_tolerance": None not in s_ci and plan["slope_ci"][0] <= s_ci[0] and s_ci[1] <= plan["slope_ci"][1],
        "calibration_in_the_large": None not in c_ci and plan["citl_ci"][0] <= c_ci[0] and c_ci[1] <= plan["citl_ci"][1],
        "bands_within_tolerance": bands_ok and testable >= plan["min_testable_bands"],
        "min_weeks": len(weeks) >= plan["min_weeks"],
        "no_dominant_week": bool(weeks) and max(weeks.values()) / len(pairs) <= plan["max_week_share"],
        "return_at_ask_ci_above_0": ret["bets"] >= plan["min_bets"] and (ret.get("roi_ci") or [None])[0] is not None
                                    and ret["roi_ci"][0] > 0,
    }
    return {"plan": plan["version"], "look": look, "n": len(pairs), "ci_level": level, "gain": gp, "gain_ci": gci,
            "calibration_slope": slope, "calibration_slope_ci": s_ci, "calibration_in_the_large_ci": c_ci,
            "bands": bands, "weeks": len(weeks), "returns": ret, "checks": checks,
            "eligible_for_owner_review": all(checks.values()),
            "futility": look == plan["futility_look"] and gp <= 0,
            "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def review_progress(pairs, reviews_path=None, plan=None, boot=BOOT):
    """Run each REVIEW_PLAN look ONCE, the first time the paired count reaches it, on exactly the first looks[i] matches
    by start; the result is stored in reviews_path and never recomputed (no repeated peeking with more data)."""
    plan = plan or REVIEW_PLAN
    done = {}
    if reviews_path and os.path.exists(reviews_path):
        try:
            with open(reviews_path, encoding="utf-8") as fh:
                done = {k: v for k, v in json.load(fh).items() if v.get("plan") == plan["version"]}
        except (OSError, ValueError):
            done = {}
    ordered = sorted(pairs, key=lambda r: (r["start"], str(r["hltv_match"])))
    ended = any(v.get("futility") for v in done.values())
    changed = False
    for i, n_look in enumerate(plan["looks"], 1):
        if ended or str(i) in done or len(ordered) < n_look:
            continue
        done[str(i)] = scheduled_review(ordered[:n_look], i, plan, boot=boot)
        changed = True
        ended = ended or done[str(i)]["futility"]
    if changed and reviews_path:
        tmp = reviews_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(done, fh, indent=1, default=str)
        os.replace(tmp, reviews_path)
    nxt = next((n for i, n in enumerate(plan["looks"], 1) if str(i) not in done), None)
    return {"plan": plan["version"], "paired": len(pairs), "looks_done": done, "ended_by_futility": ended,
            "next_look_at": None if ended else nxt,
            "eligible_for_owner_review": any(v.get("eligible_for_owner_review") for v in done.values()) and not ended,
            "note": "between looks only counts are shown; a look's result is final for that look"}


# ---------------------------------------------------------------------------------------------------- report
def _fmt_ci(ci, pct=False):
    if not ci or ci[0] is None:
        return "n/a"
    return f"[{ci[0]:+.4f}, {ci[1]:+.4f}]" if not pct else f"[{ci[0]*100:+.1f}%, {ci[1]*100:+.1f}%]"


def _when(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d") if ts else "n/a"


def render_report(res):
    L = []
    w = L.append
    w("# ML_EVAL - series-winner market prices vs HLTV results")
    w("")
    w(f"Generated {res['generated_at']} by `ml_eval.py` (research only - no grader, prop grade or member "
      "output changed). Every number below comes with its count.")
    w("")
    w("**What the prices are.** Polymarket numbers are *Polymarket's recorded price history (mid/last-trade "
      "series), not executable quotes we captured*: CLOB `/prices-history` on the outcome-0 token, "
      f"fidelity {FIDELITY} min, last point at or before scheduled start - {CUTOFF_MIN} min and no older than "
      f"{MAX_AGE_H} h before that cutoff. Returns computed from them are price-history returns, not fills.")
    w("")
    w("## 1. Inventory")
    for key, title in (("polymarket", "Polymarket (gamma series 10310, closed=true)"),
                       ("kalshi", "Kalshi (KXCS2GAME, settled; 1-minute candlesticks)")):
        inv = res.get(key)
        if not inv:
            continue
        w(f"### {title}")
        if inv.get("error"):
            w(f"Not measured: {inv['error']}")
            w("")
            continue
        if key == "kalshi":
            w(f"- Settled events listed: {inv.get('events_listed')}; evaluated: **{inv['events']}** (newest by close "
              f"time{', capped at ' + str(inv['events']) if inv.get('more_events_exist') else ''})")
        else:
            w(f"- Events evaluated: **{inv['events']}**" + (
                f" (newest first by id; listing stopped at the {inv['events']}-event cap, older events exist; "
                f"event end dates in the evaluated listing {str(inv.get('oldest_event_end'))[:10]} to "
                f"{str(inv.get('newest_event_end'))[:10]})" if inv.get("more_events_exist") else ""))
        if inv.get("series_markets"):
            w(f"- Series-winner market identified: {', '.join(f'{k} {v}' for k, v in inv['series_markets'].items())}")
        w(f"- **Eligible observations: {inv['n_eligible']}** (one per HLTV match)")
        if inv.get("span"):
            w(f"- Span of eligible matches: {inv['span'][0]} to {inv['span'][1]}")
        w("")
        w("| Exclusion | Count |")
        w("|---|---:|")
        for k, v in sorted(inv["excluded"].items(), key=lambda kv: -kv[1]):
            w(f"| {k} | {v} |")
        w("")
        nd = inv.get("no_match_diagnostic") or {}
        nm = inv["excluded"].get("no HLTV match (same teams, +-1 day)", 0)
        if nm:
            w(f"Of the {nm} 'no HLTV match' exclusions (both names resolved): "
              f"{nd.get('pair has an HLTV match within +-7 days', 0)} have the same pair on HLTV within +-7 days "
              f"(possible date mismatch), {nd.get('start on/after the newest HLTV date', 0)} start on/after the newest "
              "HLTV date; the rest are matches the HLTV database does not contain.")
            w("")
        if inv.get("unresolved_names"):
            w("Most frequent unresolved team names (no exact HLTV name; the HLTV database only holds "
              f"{res.get('hltv_teams', '?')} teams): " +
              ", ".join(f"{n} ({c})" for n, c in inv["unresolved_names"][:20]))
            w("")
        if key == "kalshi" and inv.get("note"):
            w(inv["note"])
            w("")
    w("## 2. Baseline: market price vs a fixed 50%")
    for key in ("polymarket", "kalshi"):
        b = (res.get("baseline") or {}).get(key)
        if not b:
            continue
        o = b["overall"]
        w(f"### {key.title()} (n = {o['n']} matches)")
        if not o.get("n"):
            w("No eligible observations.")
            continue
        w("")
        w("| Metric | Market | Fixed 50% | Gain vs 50% (95% match-bootstrap CI) |")
        w("|---|---:|---:|---|")
        w(f"| Log-loss | {o['logloss']:.4f} {_fmt_ci(o['logloss_ci'])} | {math.log(2):.4f} | "
          f"{o['logloss_gain_vs_50']:+.4f} {_fmt_ci(o['logloss_gain_vs_50_ci'])} |")
        w(f"| Brier | {o['brier']:.4f} {_fmt_ci(o['brier_ci'])} | 0.2500 | "
          f"{o['brier_gain_vs_50']:+.4f} {_fmt_ci(o['brier_gain_vs_50_ci'])} |")
        nf = o.get("n_fav", o["n"])
        w(f"| Favourite won | {o['fav_won']}/{nf} ({(o['fav_won'] / nf if nf else 0):.1%})"
          + (f"; {o['coin_flips']} coin flips (p = 0.5) left out" if o.get("coin_flips") else "")
          + ("" if "n_fav" in o else " (results from before the coin-flip rule: p = 0.5 counted as a team-A favourite)")
          + " | | |")
        w("")
        w("Calibration (" + ("outcome-0 price" if key == "polymarket" else "team A = first market of the event by "
                                                                           "ticker, YES mid") +
          "; observed = share of team-A wins, Wilson 95% interval):")
        w("")
        w("| Price bin | n | Mean price | Observed | 95% CI |")
        w("|---|---:|---:|---:|---|")
        for c in b["calibration"]:
            if c["n"]:
                w(f"| {c['bin']} | {c['n']} | {c['mean_p']:.3f} | {c['observed']:.3f} | "
                  f"{c['ci95'][0]:.3f}-{c['ci95'][1]:.3f} |")
        w("")
        w("By favourite price range:")
        w("")
        w("| Favourite price | n | Mean fav price | Fav won | Fav win rate (95% CI) | Log-loss (CI) | Brier |")
        w("|---|---:|---:|---:|---|---|---:|")
        for c in b["by_favourite_price"]:
            if c.get("coin_flip"):
                if c["n"]:
                    w(f"| {c['fav_price']} | {c['n']} | 0.500 | - (no favourite; team A won {c['team_a_won']}) | - | - | - |")
                continue
            if c["n"]:
                w(f"| {c['fav_price']} | {c['n']} | {c['fav_mean_p']:.3f} | {c['fav_won']} | "
                  f"{c['fav_win_rate']:.3f} ({c['ci95'][0]:.3f}-{c['ci95'][1]:.3f}) | "
                  f"{c['logloss']:.4f} {_fmt_ci(c['logloss_ci'])} | {c['brier']:.4f} |")
        w("")
        for seg, title in (("lan", "LAN / online (HLTV match_overview.lan)"),
                           ("tier", "Tier proxy (Polymarket volume tertiles)" if key == "polymarket"
                            else "Tier proxy (Kalshi volume tertiles)")):
            w(f"{title}:")
            w("")
            w("| Segment | n | Log-loss (CI) | 50% log-loss | Gain vs 50% (CI) | Brier |")
            w("|---|---:|---|---:|---|---:|")
            for lab, m in b[seg].items():
                if m.get("n"):
                    w(f"| {lab} | {m['n']} | {m['logloss']:.4f} {_fmt_ci(m['logloss_ci'])} | {math.log(2):.4f} | "
                      f"{m['logloss_gain_vs_50']:+.4f} {_fmt_ci(m['logloss_gain_vs_50_ci'])} | {m['brier']:.4f} |")
                else:
                    w(f"| {lab} | 0 | - | - | - | - |")
            w("")
    ov = res.get("overlap")
    if ov:
        w(f"### Same matches on both venues (n = {ov['n']})")
        if ov["n"]:
            w(f"Polymarket log-loss {ov['polymarket']['logloss']:.4f}, Kalshi {ov['kalshi']['logloss']:.4f}; "
              f"difference (Kalshi - Polymarket) {ov['diff']:+.4f} {_fmt_ci(ov['diff_ci'])}; mean absolute price "
              f"gap {ov['mean_abs_gap']:.3f}.")
        w("")
    st = res.get("staged")
    w("## 3. Staged feature tests (Polymarket set)")
    if not st:
        w("Not run.")
    else:
        sp = st["split"]
        w(f"Chronological split by match start (one observation per match): development n = "
          f"{sp['development']['n']} ({_when(sp['development']['from'])} to {_when(sp['development']['to'])}), "
          f"validation n = {sp['validation']['n']} ({_when(sp['validation']['from'])} to "
          f"{_when(sp['validation']['to'])}), final test n = {sp['test']['n']} ({_when(sp['test']['from'])} to "
          f"{_when(sp['test']['to'])}).")
        w("")
        # results written by the earlier code (no "lambda_chosen_by") are described as they were made
        legacy = not any("lambda_chosen_by" in v for v in st["features"].values() if v.get("status") == "tested")
        if legacy:
            method = (f"b fitted on development with L2 strength from {list(LAMBDAS)}, the strength chosen on "
                      "validation log-loss (the SAME validation rows then decide promotion - an optimistic selection; "
                      "the current code cross-validates the strength inside development). "
                      "Promotion rule: validation log-loss gain with a match-grouped 95% bootstrap CI excluding zero. "
                      "Features used HLTV rows dated strictly before the HLTV match date (the current code uses the "
                      "earlier of that date and the UTC start date). ")
        else:
            method = (f"b fitted on development with L2 strength from {list(LAMBDAS)}, the strength chosen by "
                      f"expanding-window cross-validation inside development ({CV_FOLDS} time-ordered folds) - "
                      "validation never chooses it. "
                      "Promotion rule: validation log-loss gain with a match-grouped 95% bootstrap CI excluding zero. "
                      "All features use HLTV rows dated strictly before the earlier of the HLTV match date and the UTC "
                      "date of the scheduled start (HLTV rows carry no time, so same-day results are never used). ")
        w("Each feature alone: `logit(p) = logit(p_mkt) + b*x` (x scaled by its development RMS, no intercept), "
          + method + "Missing feature = 0 (no correction): "
          + ", ".join(f"{k} {v}" for k, v in st["features_missing_as_zero"].items())
          + f" of {sum(sp[k]['n'] for k in sp)} matches.")
        w("")
        w(f"| Feature | {'Chosen L2' if legacy else 'L2 (dev CV)'} | b per SD | Val log-loss market -> model | "
          "Val gain (95% CI) | Promoted |")
        w("|---|---:|---:|---|---|---|")
        for k, v in st["features"].items():
            if v.get("status") != "tested":
                w(f"| ({k[0]}) {v['label']} | - | - | - | {v.get('status')} | no |")
                continue
            w(f"| ({k[0]}) {v['label']} | {v['chosen_lambda']:g} | {v['b_per_sd']:+.4f} | "
              f"{v['val_base_logloss']:.4f} -> {v['val_model_logloss']:.4f} | {v['val_gain']:+.5f} "
              f"{_fmt_ci(v['val_gain_ci'])} | {'**yes**' if v['promoted'] else 'no'} |")
        w("")
        w(f"Development baseline log-loss {st['development_baseline']['logloss']:.4f} (n = "
          f"{st['development_baseline']['n']}); validation baseline {st['validation_baseline']['logloss']:.4f} "
          f"(n = {st['validation_baseline']['n']}).")
        w("")
        w("Hypothetical validation returns at recorded Polymarket prices + 1 cent, 1 unit per bet, side bought when "
          "the feature-corrected probability exceeds the recorded price by the threshold. **Price-history "
          "returns, not executable fills.**")
        w("")
        w(f"| Feature | Edge >= | Bets | Won | Avg price | Net units | ROI (95% CI; n/a under {MIN_BETS_CI} bets) |")
        w("|---|---:|---:|---:|---:|---:|---|")
        for k, v in st["features"].items():
            for e in v.get("val_returns") or []:
                ci = e["roi_ci95"] if e["bets"] >= MIN_BETS_CI else None     # a handful of bets has no interval
                roi = f"{e['roi']:+.1%} {_fmt_ci(ci, True)}" if e["bets"] else "-"
                ap = f"{e['avg_price']:.3f}" if e["bets"] else "-"
                w(f"| ({k[0]}) | {e['threshold_pts']} pts | {e['bets']} | {e['won']} | {ap} | "
                  f"{e['net_units']:+.2f} | {roi} |")
        w("")
        fr = st["freeze"]
        w(f"**Freeze** before the test set was scored: {fr['frozen_at']}, sha256 `{fr['sha256'][:16]}...` "
          f"(pinned dev / validation / test match ids, promoted features, coefficients, code tag). "
          f"Promoted: {', '.join(fr['promoted']) or 'none'}."
          + (f" This freeze REPLACED an earlier one (sha256 `{str(fr.get('previous_sha256'))[:16]}...`)." if fr.get("refrozen") else ""))
        w("")
        t = (st.get("test") or {}).get("baseline")
        if not t:
            w("**Final test: not scored in this run** (run with `--final` once, after the freeze).")
        else:
            sc = (st.get("test") or {}).get("scored_count")
            how = ("touched once, after the freeze" if sc == 1 else
                   f"scored {sc} times - this is rescore #{sc}" if sc else
                   "scored before the one-time guard existed - the number of scorings was not recorded")
            w(f"**Final test ({how}):** market baseline log-loss {t['logloss']:.4f} "
              f"{_fmt_ci(t['logloss_ci'])} vs {math.log(2):.4f} at 50% (gain {t['logloss_gain_vs_50']:+.4f} "
              f"{_fmt_ci(t['logloss_gain_vs_50_ci'])}), Brier {t['brier']:.4f} (gain {t['brier_gain_vs_50']:+.4f} "
              f"{_fmt_ci(t['brier_gain_vs_50_ci'])}), favourite won {t['fav_won']}/{t.get('n_fav', t['n'])}, "
              f"n = {t['n']} matches.")
            for k in fr["promoted"]:
                m = st["test"][k]
                w(f"- ({k[0]}) test log-loss {m['logloss']:.4f}, gain vs market {_fmt_ci(m['gain_vs_market_ci'])}, "
                  f"n = {m['n']}.")
            if not fr["promoted"]:
                w("")
                w("No feature was promoted, so no feature has a test-set number (by design).")
        w("")
    w("## 4. Forward cohort (ml_snapshots.db)")
    fw = res.get("forward") or {}
    if fw.get("status") != "ok":
        w(f"Not measurable yet: {fw.get('status')}. The cohort starts accumulating once the live collector writes "
          "snapshots; rerun `python ml_eval.py --forward-only` to score it.")
    else:
        w(f"Scorable snapshots (per source and match: the latest at or before start - {CUTOFF_MIN} min, available then "
          f"and no older than {MAX_AGE_H} h before that cutoff): {fw['snapshots_usable']}; "
          f"scored against HLTV: {fw['n']}. Excluded: " +
          (", ".join(f"{k} {v}" for k, v in fw["excluded"].items()) or "none") + ".")
        for src, b in fw["by_source"].items():
            o = b["overall"]
            w(f"- {src}: n = {o['n']}, log-loss {o['logloss']:.4f} {_fmt_ci(o.get('logloss_ci'))}, Brier "
              f"{o['brier']:.4f}, gain vs 50% {o['logloss_gain_vs_50']:+.4f} {_fmt_ci(o.get('logloss_gain_vs_50_ci'))}.")
        c = fw.get("candidate") or {}
        if c:
            if c.get("n"):
                w(f"- Candidate {c['model']} vs {c['baseline']} on the same {c['n']} forward matches: log-loss "
                  f"{c['ll_model']:.4f} vs {c['ll_baseline']:.4f}, gain {c['gain']:+.5f} {_fmt_ci(c.get('gain_ci'))}, "
                  f"calibration slope {c['calibration_slope']}. Decision: {c['decision']}.")
            else:
                w(f"- Candidate: {c.get('decision')}.")
    w("")
    for note in res.get("notes", []):
        w(f"- {note}")
    w("")
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------------- main
def _load_dsn_file():
    if os.environ.get("HLTV_DB_DSN"):
        return
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.render-db")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if line.startswith("DATABASE_URL="):
                os.environ["HLTV_DB_DSN"] = line.split("=", 1)[1].strip().strip('"')


def load_hltv():
    _load_dsn_file()
    import hltv
    if not hltv.dsn():
        raise RuntimeError("HLTV_DB_DSN not set")
    con = hltv._connect()
    try:
        return Hltv.load(con)
    finally:
        con.close()


def load_hltv_recent(days=60, since=None, matchids=None):
    """A light HLTV copy for the forward cohort only (server /admin/ml_eval): teams + matches dated on/after `since`
    (default: the last `days`) PLUS every listed HLTV match id, with their map scores - no player rows, no features.
    Enough for team_ids / winner / _forward_match. h.first_date records the window (older = 'outside loaded window')."""
    import hltv
    since = since or (datetime.now(timezone.utc).date() - timedelta(days=int(days)))
    con = hltv._connect()
    try:
        h = Hltv()
        h.first_date = since
        cur = con.cursor()
        cur.execute("SET statement_timeout = 60000")
        cur.execute("SELECT teamid, name FROM teams")
        for tid, name in cur.fetchall():
            h.add_team(tid, name)
        cur.execute("SELECT matchid, date, event, lan, coalesce(forfeited_maps, 0), source FROM match_overview "
                    "WHERE date >= %s OR matchid = ANY(%s)", (since, [int(m) for m in (matchids or [])]))
        for mid, d, ev, lan, ff, src in cur.fetchall():
            h.matches[mid] = {"matchid": mid, "date": d, "event": ev, "lan": lan, "forfeited": ff, "source": src,
                              "series": {}, "maps_won": defaultdict(int), "teams": set()}
        ids = list(h.matches)
        map_rows = []
        if ids:
            cur.execute("""SELECT own.matchid, own.teamid, own.mapid, own.score, opp.score, mp.name
                           FROM matches own
                           JOIN matches opp ON opp.matchid = own.matchid AND opp.teamid <> own.teamid
                                           AND opp.mapid = own.mapid AND opp.sideid = 0
                           LEFT JOIN maps mp ON mp.mapid = own.mapid
                           WHERE own.sideid = 0 AND own.matchid = ANY(%s)""", (ids,))
            map_rows = cur.fetchall()
        h.build(map_rows, [])
        return h
    finally:
        con.close()


def _strip(rows):
    keep = ("source", "event_id", "market_id", "event_ticker", "market", "question", "team_a", "team_b", "start",
            "price_t", "p", "bid", "ask", "y", "volume", "hltv_match", "date", "lan", "event", "split", "feature_day",
            "a_rating", "b_mappool", "c_form", "e_h2h")
    return [{k: r[k] for k in keep if k in r} for r in rows]


def results_rows(rows, final=False):
    """(rows for the repo-root results file, test-split rows). The results file NEVER carries the test split's labels,
    prices or features - a non-final run iterating on dev / val would otherwise have every test outcome to hand; only
    a --final run writes the test rows, to a separate file next to the freeze (cache dir, not the repo)."""
    keep = [r for r in rows if r.get("split") != "test"]
    test = [r for r in rows if r.get("split") == "test"]
    return _strip(keep), (_strip(test) if final else [])


def write_results(res, rows, json_path, final=False, test_rows_path=None):
    res = dict(res)
    if "staged" in res:
        res["polymarket_rows"], test = results_rows(rows, final)
        res["polymarket_rows_excluded"] = "test split never written here" + (
            f" ({len(test)} test rows in {os.path.basename(test_rows_path)}, cache dir)" if test and test_rows_path else "")
        if test and test_rows_path:
            with open(test_rows_path, "w", encoding="utf-8") as fh:
                json.dump(test, fh, default=str)
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1, default=str)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--cache", default=os.environ.get("ML_EVAL_CACHE") or os.path.join(
        os.environ.get("TEMP", "/tmp"), "cs2_scout_ml_cache"))
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--report", default=os.path.join(here, "ML_EVAL.md"))
    ap.add_argument("--json", default=os.path.join(here, "ml_eval_results.json"))
    ap.add_argument("--snapshots", default=os.path.join(os.environ.get("CS2_DATA_DIR") or here, ".cache",
                                                        "ml_snapshots.db"))
    ap.add_argument("--max-events", type=int, default=MAX_EVENTS)
    ap.add_argument("--no-kalshi", action="store_true")
    ap.add_argument("--forward-only", action="store_true")
    ap.add_argument("--offline", action="store_true", help="use cached downloads only")
    ap.add_argument("--final", action="store_true",
                    help="after the freeze, score the final test set (do this ONCE; without it only dev/validation)")
    ap.add_argument("--refreeze", action="store_true",
                    help="replace an existing freeze (non-final run only; recorded in the freeze and the results)")
    ap.add_argument("--rescore-test", action="store_true",
                    help="score the test set again although it was scored before (reported as a rescore)")
    a = ap.parse_args(argv)
    if a.final and a.refreeze:
        ap.error("--refreeze is for a non-final run (a --final run never rewrites the freeze)")

    def log(s):
        print(s, file=sys.stderr, flush=True)

    t0 = time.time()
    hl = load_hltv()
    log(f"HLTV loaded: {len(hl.matches)} matches, {len(hl.teams)} teams ({time.time()-t0:.0f}s)")
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hltv_teams": len(hl.teams),
           "hltv_matches": len(hl.matches),
           "hltv_span": [min(m["date"] for m in hl.matches.values()).isoformat(),
                         max(m["date"] for m in hl.matches.values()).isoformat()],
           "params": {"cutoff_min": CUTOFF_MIN, "max_age_h": MAX_AGE_H, "fidelity_min": FIDELITY, "split": SPLIT,
                      "lambdas": LAMBDAS, "bootstrap": BOOT, "seed": SEED, "edge_thresholds": EDGE_THRESHOLDS,
                      "cost": COST}, "notes": []}
    res["forward"] = forward_eval(a.snapshots, hl)
    if a.forward_only:
        print(json.dumps(res["forward"], indent=2, default=str))
        return res
    f = Fetcher(os.path.join(a.cache, "http"), offline=a.offline)
    inv = build_inventory(f, hl, a.max_events, log)
    rows = inv.pop("eligible")
    inv["n_eligible"] = len(rows)
    inv["span"] = [rows[0]["date"], rows[-1]["date"]] if rows else None
    res["polymarket"] = inv
    res["baseline"] = {"polymarket": score_baseline(rows)}
    krows = []
    if not a.no_kalshi:
        try:
            kinv = build_kalshi_inventory(f, hl, a.max_events, log)
            krows = kinv.pop("eligible")
            kinv["n_eligible"] = len(krows)
            kinv["span"] = [krows[0]["date"], krows[-1]["date"]] if krows else None
            kinv["note"] = ("Kalshi history: the official candlesticks endpoint returns 1-minute YES bid/ask "
                            "closes before the match, so a pre-match quote history exists. Mid = (bid + ask)/2 of the "
                            f"last candle ending at or before the cutoff, two-sided book with spread <= "
                            f"{KALSHI_MAX_SPREAD:.2f} required; team A = first market of the event by ticker. "
                            "Scheduled start parsed from rules_primary (EDT/EST).")
            res["kalshi"] = kinv
            res["baseline"]["kalshi"] = score_baseline(krows, unit="contracts")
        except Exception as e:
            res["kalshi"] = {"error": f"{type(e).__name__}: {e}"}
    if rows and krows:
        km = {r["hltv_match"]: r for r in krows}
        pairs = []
        for r in rows:
            k = km.get(r["hltv_match"])
            if not k:
                continue
            pk = k["p"] if k["hltv_a"] == r["hltv_a"] else 1 - k["p"]
            pairs.append({"hltv_match": r["hltv_match"], "y": r["y"], "pp": r["p"], "pk": pk})
        if pairs:
            def d(rs):
                return sum(logloss1(x["pk"], x["y"]) - logloss1(x["pp"], x["y"]) for x in rs) / len(rs)
            res["overlap"] = {"n": len(pairs), "polymarket": metrics([dict(x, p=x["pp"]) for x in pairs], boot=False),
                              "kalshi": metrics([dict(x, p=x["pk"]) for x in pairs], boot=False),
                              "diff": round(d(pairs), 5), "diff_ci": group_bootstrap(_groups(pairs), d),
                              "mean_abs_gap": round(sum(abs(x["pp"] - x["pk"]) for x in pairs) / len(pairs), 4)}
        else:
            res["overlap"] = {"n": 0}
    if len(rows) >= 50:
        os.makedirs(a.cache, exist_ok=True)
        try:
            res["staged"] = staged_tests(rows, hl, freeze_path=os.path.join(a.cache, "ml_eval_freeze.json"),
                                         final=a.final, refreeze=a.refreeze, rescore=a.rescore_test)
        except FreezeError as e:
            raise SystemExit(f"ml_eval: {e}")
    res["notes"] += [
        "HLTV team_rankings holds a single snapshot date "
        f"({', '.join(str(d) for d in hl.rank_dates[-3:]) or 'none'}), so point-in-time opponent ranks do not exist; "
        "using today's ranks for past matches would leak the future, so feature (c) is NOT rank-adjusted.",
        f"HLTV database covers {res['hltv_span'][0]} to {res['hltv_span'][1]} with {len(hl.matches)} matches and "
        f"{len(hl.teams)} teams; Polymarket matches between teams outside it are excluded as unresolved/no match.",
        "HLTV match dates are calendar dates only, so the +-1 day window is applied to the UTC date of the "
        "scheduled start; two meetings of the same teams inside it are excluded as ambiguous.",
        "Winner = more maps won on HLTV's per-map rows, cross-checked with HLTV's series row; forfeits "
        "(match_overview.forfeited_maps > 0) excluded. A market whose own resolution disagrees with HLTV is "
        "excluded as a likely mismatch.",
        "Feature constants (60-day roster window, k = 20 rating shrinkage, top-4 maps over 180 days with k = 10, "
        "last 10 series within 365 days with k = 5, head-to-head 365 days with k = 3) were fixed before any test ran; "
        "the only fitted quantities are b and the L2 strength, both on development (strength by expanding-window CV).",
        "Hypothetical returns use each tested feature's correction even when it was not promoted; they are on the "
        "validation set (which no longer chooses the L2 strength - that is cross-validated inside development - but "
        "they are still one sample), they are price-history returns at recorded prices + 1 cent, not executable fills, "
        f"and a threshold with fewer than {MIN_BETS_CI} bets gets no ROI interval.",
        "Best-of-2 markets are excluded explicitly (a draw is possible; Polymarket resolves a tie 50-50, so the price "
        "is not a series-win probability).",
        "Expected roster (a, e) = the 5 players with the most recent maps for the team in the 60 days before the "
        "match (ties: more maps). Player rating = mean HLTV rating over his maps in those 60 days, shrunk to 1.0 "
        "with k = 20 maps.",
    ]
    res = write_results(res, rows, a.json, final=a.final,
                        test_rows_path=os.path.join(a.cache, "ml_eval_test_rows.json"))
    with open(a.report, "w", encoding="utf-8") as fh:
        fh.write(render_report(res))
    log(f"done in {time.time()-t0:.0f}s: polymarket eligible {len(rows)}, kalshi eligible {len(krows)}, "
        f"requests {f.requests}, cache hits {f.hits}")
    return res


if __name__ == "__main__":
    main()
