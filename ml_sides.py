#!/usr/bin/env python3
"""
ml_sides.py - a SIDE for every CS2 moneyline (series winner), with the HLTV figures shown next to it.

Owner 2026-10-12: "correct the moneylines grading - pick a side for each moneyline with reasoning and HLTV data to back
that side".  Shadow only: every pick carries stake_units 0 unless a candidate demonstrates an improvement on a chronological test.

What decides the side (FROZEN rule, MODEL_VERSION "ml-sides-1")
  1. Usable market baseline (moneyline.board() match with a linked match_id and baseline.prob_a not None):
       side = the market favourite (prob_a > 0.5 -> team A, < 0.5 -> team B), prob = the baseline probability of that
       side, kind "market", label "Lean <team>".  prob_a exactly 0.5: the side the HLTV composite (rule 2) favours,
       and the label says the market is level.  This is the validated baseline (ML_EVAL.md: market log-loss 0.607 vs
       0.693 for 50% on the 234-match test set) - it is NOT presented as an edge on that platform's own price.
  2. No usable market price (unlinked, started, best-of-2, thin / one-sided book, no market):
       side = sign of the frozen HLTV composite  z = sum over the AVAILABLE signals of x / sd, where x are ml_eval.py's
       features (a) rating, (b) map pool, (c) form, (e) head-to-head - computed by ml_eval's own Hltv methods - and sd is
       each feature's development RMS from the frozen ml_eval run (SCALE below).  Ties (z == 0, or no two-team
       signal because one team has no HLTV data) -> the higher shrunk lineup rating, a team with no HLTV maps in the
       60-day window counting as 1.00 (exactly what ml_eval's k = 20 shrinkage gives zero maps); still level with
       some signal -> canonical team A, said so.  kind "hltv-only", prob None (never a made-up probability, never
       50%), label "Lean <team> (HLTV only - untested, no market price)".
  3. No usable market price and no HLTV lineup or signal for EITHER team: side None, label
       "No HLTV data and no market price - no side" (the only case without a side; the reasons say why).
  Every pick: stake_units 0.  hltv_view = how the four HLTV signals line up with the side: "agrees" (a majority of the
  available signals back it), "disagrees" (a majority favour the other team), "mixed" otherwise, "no data" (none).
  The signals have weight 0 for the market side: none of them demonstrated an improvement over the market in ml_eval's chronological test
  (ML_EVAL.md section 3: nothing promoted).  LAN / online record and the current VRS rank are shown, never counted.

Thunderpick-first market input (MODEL_VERSION_TP "ml-sides-2", 2026-10-13 - owner: Thunderpick is the primary odds
source; the Moneylines tab showed Thunderpick's price as primary while the side still said "no market price")
  Used when the caller passes Thunderpick's block (pick(..., tp=block or None), sides_for_board(..., tp_blocks=[...])).
  Rule 1's "usable market baseline" becomes: Thunderpick's ELIGIBLE price first (moneyline.tp_block: in Thunderpick's
  latest answer, availability "current", pre-match, linked match, not best-of-2, valid two-way odds) -> side = Thunderpick's favourite
  by its proportional margin-adjusted probability; otherwise the ml-baseline-1 exchange baseline exactly as in rule 1;
  otherwise rules 2 / 3 unchanged (the "no usable market price" reason then names Thunderpick's state too).  The HLTV
  composite, ties, the level-50% case and every stake (0U) are the frozen rules above.  Without a tp argument the
  side, label, reasons and basis are exactly ml-sides-1.  Every pick says baseline_source + odds_policy (metadata
  only); moneyline.log_forecasts logs the
  ml-sides-2 market input (ml_forecasts, model_version "ml-sides-2") so the evaluation separates the two policies.
  Thunderpick has not been evaluated as a moneyline baseline yet - the basis says so; it is never called validated.

HLTV evidence (evidence(); one per team pair and cutoff day)
  cutoff day = the earlier of today (UTC) and the match's UTC start date; every window uses rows dated STRICTLY BEFORE
  it (HLTV dates matches by calendar day only - the same rule ml_eval uses, so nothing after "now" or the start leaks in).
  Per team: expected starters (ml_eval roster: the 5 players with the most recent maps for the team in the 60 days
  before the cutoff, ties -> more maps), each player's HLTV rating over those 60 days with map counts, raw and shrunk
  toward 1.0 with k = 20 maps (feature (a)); last 10 series (within 365 days) W-L with dates / opponents and the shrunk
  rate (feature (c)); map pool: the 4 most-played maps of the last 120 days with W-L (display) plus feature (b) (top-4
  maps over 180 days, shrunk with k = 10); LAN / online series record over 12 months; current VRS rank (latest
  team_rankings snapshot, labelled "current rank").  Head-to-head over 365 days counting only meetings where both teams
  had >= 3 of today's starters (feature (e)).
  Teams keyed "name:<...>" (HLTV schedule only) or unresolved have no HLTV data: their pieces are None with a reason.

Load / failure behaviour
  One bulk read for every team on the board (one pooled connection, ~9 indexed queries, ~3 s for 120 teams), at most one
  read at a time, at most MAX_MATCHES matches per refresh.  Evidence is cached per (team pair, cutoff day) for
  EVIDENCE_TTL (15 min) in memory and saved to hltv_store; a failed read keeps serving the previous copy (memory, else
  the stored copy on disk, labelled as such) and is retried after FAIL_TTL; with no copy at all the pieces are None with
  the reason.  Nothing here raises to a caller.
  sides_for_board() never touches the database unless block=True: member requests read memory only and, when something
  is missing or older than 15 min, wake the single background refresh (start() also runs it every REFRESH_EVERY s).

Public
  evidence(team_a_key, team_b_key, start_iso, lan=None)   evidence dict oriented to (a, b) - does a DB read on a miss
  pick(match, evidence, tp=...)                             the side for one moneyline.board() match (pure function)
  sides_for_board(board_matches, block=False, tp_blocks=None)  list aligned with board_matches (memory only by default)
  market_input(match, tp) / forecast_row(match, tp, at)     the ml-sides-2 market input and its ml_forecasts row
  refresh(board_matches) / start(board_fn) / status()
"""
import json
import math
import threading
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import hltv
import hltv_store
import ml_eval as M

MODEL_VERSION = "ml-sides-1"
EVIDENCE_TTL = 15 * 60          # one computation per team pair and day per 15 min
FAIL_TTL = 30                   # a failed read is retried after this long (the previous copy is served meanwhile)
MAX_MATCHES = 80                # evidence computed for at most this many board matches per refresh
REFRESH_EVERY = 60              # start(): background pass interval (only stale pairs are read)
LOAD_WAIT = 20                  # evidence(): seconds to wait for a running read before serving what is cached
LOAD_BUDGET_S = 60              # a bulk read stops issuing queries after this long (counts as a failure)
QUERY_TIMEOUT_MS = 20000        # background read only: per-query timeout (the pool's member default is 5 s; hltv.py resets
                                # a connection whose timeout was SET before it goes back to the pool)
STORE_KEY = "ml_sides_ev"

# ml_eval.py feature definitions (frozen before its test ran) - imported, never redefined
ROSTER_DAYS, RATING_K = M.ROSTER_DAYS, M.RATING_K
MAPPOOL_DAYS, MAPPOOL_TOP, MAPPOOL_K = M.MAPPOOL_DAYS, M.MAPPOOL_TOP, M.MAPPOOL_K
FORM_N, FORM_DAYS, FORM_K = M.FORM_N, M.FORM_DAYS, M.FORM_K
H2H_DAYS, H2H_KEEP, H2H_K = M.H2H_DAYS, M.H2H_KEEP, M.H2H_K
RATING_PRIOR = 1.0              # ml_eval's rating shrink target: a lineup with zero HLTV maps in 60 days rates exactly this
MAP_DISPLAY_DAYS = 120          # display only: the 4 most-played maps of the last 120 days
LAN_DAYS = 365                  # display only: LAN / online series record

# Development RMS of each feature in the frozen ml_eval run (ml_eval_results.json staged.features.<f>.x_scale_sd,
# freeze sha256 c4edd047...).  The composite divides each feature by it - the same scaling ml_eval's tests used.
SCALE = {"rating": 0.05016, "map_pool": 0.10247, "form": 0.14117, "h2h": 0.17449}
SIGNALS = ("rating", "map_pool", "form", "h2h")
ML_EVAL_FEATURE = {"rating": "a_rating", "map_pool": "b_mappool", "form": "c_form", "h2h": "e_h2h"}

BASIS_MARKET = ("Side = market favourite (validated baseline). HLTV figures are context - none of the tested candidates "
                "demonstrated an improvement over the market price (ML_EVAL.md).")
BASIS_HLTV = ("Side = sign of the frozen HLTV composite (ml_eval features a rating, b map pool, c form, e head-to-head, "
              "each divided by its development SD; ties -> higher shrunk lineup rating). Untested as a side-picker - "
              "none of the tested candidates demonstrated an improvement over the market price (ML_EVAL.md). "
              "No market price, no stake.")
BASIS_NONE = "No usable market price and no comparable HLTV data for these two teams - no side."
NO_SIDE_LABEL = "No HLTV data and no market price - no side"
PENDING_LABEL = "HLTV evidence loading - no market price; the side appears once it is read"

MODEL_VERSION_TP = "ml-sides-2"        # Thunderpick-first market input (see the module docstring)
ODDS_POLICY_EXCHANGE = "exchange-only"
ODDS_POLICY_TP = "thunderpick-first"
BASIS_MARKET_TP = ("Side = Thunderpick's favourite (bookmaker odds, margin removed proportionally) - the primary odds "
                   "source since 2026-10-13. Not yet evaluated as a moneyline baseline: its forecasts are logged "
                   "(ml-sides-2) so it can be. HLTV figures are context.")
TP_PRICE = "Thunderpick odds, margin removed proportionally"
RULE_TP = ("ml-sides-2 market input: Thunderpick's eligible series price (in its latest answer, <= 75 min, Match Winner = "
           "series winner, two-way, no draw, pre-match, linked match) -> its proportional margin-adjusted probability "
           "for team A; else the ml-baseline-1 exchange baseline; else none (never 50%). Shadow - 0 units.")
_RULE1 = object()                      # pick(tp=...) default: no Thunderpick input -> exactly ml-sides-1


TP_READ_ERROR = "read_error"           # moneyline.tp_block's marker when Thunderpick could not be read (not "no event")
TP_FORMAT_WHY = "best-of-2 - a draw is possible, so a two-way price is not a series-winner probability"


def _draw_format(match):
    """A best-of-2 board match (a draw is possible): never a Thunderpick side input, whatever the block says - the same
    exclusion ml-sides-1 gets from its baseline (2026-10-13 review: checked here too, not only by thunderpick.py)."""
    return bool((match or {}).get("tie_possible")) or (match or {}).get("bo") == 2


def tp_eligible(tp, match):
    """Is `tp` (moneyline.tp_block of this match) an eligible pre-match Thunderpick series price for the side?"""
    if not isinstance(tp, dict) or not tp or not (match or {}).get("match_id"):
        return False
    if (match or {}).get("started") or tp.get("started") or _draw_format(match):
        return False
    if tp.get("availability") not in (None, "current") or not tp.get("eligible", tp.get("usable")):
        return False
    try:
        pa = float(tp.get("p_a"))
    except (TypeError, ValueError):
        return False
    return 0 < pa < 1


def tp_why(tp, match):
    """Why Thunderpick gives no side input for this match, in a few words (None when it does)."""
    if not (match or {}).get("match_id"):
        return "not tied to one HLTV match"
    if not tp:
        return "no Thunderpick event linked to this match"
    if tp_eligible(tp, match):
        return None
    if (match or {}).get("started") or tp.get("started"):
        return "match started - no pre-match price"
    if _draw_format(match):
        return TP_FORMAT_WHY
    return str(tp.get("availability_label") or tp.get("availability") or "price not usable")[:160]


def market_input(match, tp=_RULE1, tp_policy=None):
    """The market input of the side for one board match: {"model_version", "odds_policy", "prob_a" (team A, or None),
    "source" ("thunderpick" / "polymarket" / "kalshi" / None), "exchange": {"platform", "prob_a"}, "tp_why"}.
    tp=_RULE1 (default): ml-sides-1 - the exchange baseline only.  tp=block or None: ml-sides-2 - Thunderpick's eligible
    price first.  Pure."""
    base = (match or {}).get("baseline") or {}
    linked = bool((match or {}).get("match_id"))
    ex_pa = base.get("prob_a") if linked else None
    ex = {"platform": base.get("platform") if ex_pa is not None else None, "prob_a": ex_pa}
    ex_ver = base.get("model_version") or "ml-baseline-1"
    if tp is _RULE1:
        return {"model_version": MODEL_VERSION, "odds_policy": f"{ODDS_POLICY_EXCHANGE}|{ex_ver}", "prob_a": ex_pa,
                "source": ex["platform"], "exchange": ex, "tp_why": None}
    pol = f"{ODDS_POLICY_TP}|{(tp or {}).get('policy') or tp_policy or 'thunderpick'}|{ex_ver}"
    if tp_eligible(tp, match):
        return {"model_version": MODEL_VERSION_TP, "odds_policy": pol, "prob_a": float(tp["p_a"]),
                "source": "thunderpick", "exchange": ex, "tp_why": None}
    return {"model_version": MODEL_VERSION_TP, "odds_policy": pol, "prob_a": ex_pa, "source": ex["platform"],
            "exchange": ex, "tp_why": tp_why(tp, match)}

_LOCK = threading.Lock()        # guards _EV / _STATE
_LOAD = threading.Lock()        # at most one database read at a time (one pooled connection)
_EV = {}                        # canonical key -> {"at", "ev", "ok", "retry_at"}
_STATE = {"busy": False, "thread": None, "last_refresh": None, "last_ok": None, "last_error": None, "last_secs": None,
          "loads": 0, "load_failures": 0, "pairs_loaded": 0, "loop": None, "loop_error": None}


# ---------------------------------------------------------------- small helpers
def _utcnow():
    return datetime.now(timezone.utc)


def _parse(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def cutoff_day(start_iso, now=None):
    """Rows dated strictly before this day are used: the earlier of today (UTC) and the start's UTC date."""
    today = (now or _utcnow()).astimezone(timezone.utc).date()
    st = _parse(start_iso)
    return min(today, st.astimezone(timezone.utc).date()) if st else today


def hltv_id(key):
    """'team:<id>' -> int; 'name:<...>' / None / anything else -> None."""
    if isinstance(key, str) and key.startswith("team:"):
        try:
            return int(key.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _ckey(ka, kb, day):
    a, b = str(ka or "?"), str(kb or "?")
    return (a, b, day.isoformat()) if a <= b else (b, a, day.isoformat())


def _window(rows, day, days):
    """ml_eval's window: rows (date, ...) sorted, dated in [day - days, day)."""
    import bisect
    lo = bisect.bisect_left(rows, (day - timedelta(days=days),))
    hi = bisect.bisect_left(rows, (day,))
    return rows[lo:hi]


def _map_title(n):
    return {"dust2": "Dust2", "": "Unknown map"}.get(n, str(n).title())


def _pct(x):
    return f"{x * 100:.0f}%"


def _pair(x, y, digits=2, pct=False):
    """Two numbers at display precision - one more digit when they would print the same (never '1.07 vs 1.07 -
    favours ...')."""
    for dg in (digits, digits + 1, digits + 2):
        if pct:
            a, b = f"{x * 100:.{max(dg - 2, 0)}f}%", f"{y * 100:.{max(dg - 2, 0)}f}%"
        else:
            a, b = f"{x:.{dg}f}", f"{y:.{dg}f}"
        if a != b or abs(x - y) < 1e-12:
            return a, b
    return a, b


# ---------------------------------------------------------------- database (one bulk read for many teams)
SQL = {
    # must START with "SET statement_timeout" so hltv.py marks the connection and resets it to 5 s when handed back
    "timeout": "SET statement_timeout = %(ms)s /*ml_sides:timeout*/",
    "overview": """/*ml_sides:overview*/
        SELECT mo.matchid, mo.date, mo.event, mo.lan, coalesce(mo.forfeited_maps, 0), mo.source
        FROM match_overview mo
        WHERE mo.date >= %(lo)s AND mo.date < %(hi)s
          AND EXISTS (SELECT 1 FROM matches m WHERE m.matchid = mo.matchid AND m.teamid = ANY(%(teams)s))""",
    "maps": """/*ml_sides:maps*/
        SELECT own.matchid, own.teamid, own.mapid, own.score, opp.score, mp.name
        FROM matches own
        JOIN matches opp ON opp.matchid = own.matchid AND opp.teamid <> own.teamid
                        AND opp.mapid = own.mapid AND opp.sideid = 0
        LEFT JOIN maps mp ON mp.mapid = own.mapid
        WHERE own.sideid = 0 AND own.matchid = ANY(%(ids)s)""",
    "team_players": """/*ml_sides:team_players*/
        SELECT ps.matchid, ps.teamid, ps.playerid, ps.mapid, ps.rating FROM player_stats ps
        WHERE ps.matchid = ANY(%(ids)s) AND ps.teamid = ANY(%(teams)s) AND ps.mapid <> 0 AND ps.sideid = 0""",
    "window": """/*ml_sides:window*/
        SELECT matchid, date, event, lan, coalesce(forfeited_maps, 0), source FROM match_overview
        WHERE date >= %(lo)s AND date < %(hi)s""",
    "starters": """/*ml_sides:starters*/
        SELECT ps.matchid, ps.teamid, ps.playerid, ps.mapid, ps.rating FROM player_stats ps
        WHERE ps.matchid = ANY(%(ids)s) AND ps.playerid = ANY(%(pids)s) AND ps.mapid <> 0 AND ps.sideid = 0""",
    "lineups": """/*ml_sides:lineups*/
        SELECT ps.matchid, ps.teamid, ps.playerid, ps.mapid, ps.rating FROM player_stats ps
        WHERE ps.matchid = ANY(%(ids)s) AND ps.teamid = ANY(%(teams)s) AND ps.mapid <> 0 AND ps.sideid = 0""",
    "teams": "/*ml_sides:teams*/ SELECT teamid, name FROM teams WHERE teamid = ANY(%(teams)s)",
    "players": "/*ml_sides:players*/ SELECT playerid, name FROM players WHERE playerid = ANY(%(pids)s)",
    "ranks": """/*ml_sides:ranks*/
        SELECT DISTINCT ON (teamid) teamid, rank, points, date FROM team_rankings
        WHERE teamid = ANY(%(teams)s) ORDER BY teamid, date DESC""",
}


class _Budget(RuntimeError):
    pass


def _match_entry(mid, d, ev, lan, ff, src):
    from collections import defaultdict
    return {"matchid": mid, "date": d, "event": ev, "lan": lan, "forfeited": ff, "source": src,
            "series": {}, "maps_won": defaultdict(int), "teams": set()}


def _as_date(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return date.fromisoformat(str(d)[:10])


def load_rows(cur, team_ids, specs, deadline=None):
    """Every HLTV row the evidence of `specs` [(team_a_id, team_b_id, cutoff_day)] needs, for all teams at once.
    Windows: [min cutoff - 365 d, max cutoff) for results / maps, [min cutoff - 60 d, max cutoff) for players.
    Returns a dict of raw rows; the per-pair windows (strictly before each pair's own cutoff) are applied in Python."""
    def q(name, params):
        if deadline is not None and time.time() > deadline:
            raise _Budget(f"HLTV read stopped after {LOAD_BUDGET_S} s")
        cur.execute(SQL[name], params)
        return cur.fetchall()
    teams = sorted({int(t) for t in team_ids})
    cur.execute(SQL["timeout"], {"ms": int(QUERY_TIMEOUT_MS)})
    days = [d for _, _, d in specs]
    hi = max(days)
    lo = min(days) - timedelta(days=max(FORM_DAYS, H2H_DAYS, MAPPOOL_DAYS, LAN_DAYS, MAP_DISPLAY_DAYS))
    lo60 = min(days) - timedelta(days=ROSTER_DAYS)
    overview = [(r[0], _as_date(r[1])) + tuple(r[2:]) for r in q("overview", {"lo": lo, "hi": hi, "teams": teams})]
    ids = [r[0] for r in overview]
    maps = q("maps", {"ids": ids}) if ids else []
    ids60 = [r[0] for r in overview if r[1] >= lo60]
    team_players = q("team_players", {"ids": ids60, "teams": teams}) if ids60 else []
    # expected starters per (team, cutoff) - ml_eval's roster() on these rows
    tmp = M.Hltv()
    for r in overview:
        tmp.matches[r[0]] = _match_entry(*r)
    tmp.build([], [(m, t, p, rt) for m, t, p, _, rt in team_players])
    starters = set()
    for a, b, d in specs:
        for t in (a, b):
            if t is not None:
                starters.update(tmp.roster(t, d))
    window = [(r[0], _as_date(r[1])) + tuple(r[2:]) for r in q("window", {"lo": lo60, "hi": hi})]
    starter_rows = (q("starters", {"ids": [r[0] for r in window], "pids": sorted(starters)})
                    if starters and window else [])
    # head-to-head meetings of the requested pairs (lineups of every meeting, also those older than 60 days)
    pairs = {frozenset((a, b)) for a, b, _ in specs if a is not None and b is not None}
    by_match = {}
    for mid, tid, mapid, *_ in maps:
        by_match.setdefault(mid, set()).add(tid)
    meet = sorted(mid for mid, ts in by_match.items() if frozenset(ts) in pairs)
    lineups = q("lineups", {"ids": meet, "teams": teams}) if meet else []
    names = dict(q("teams", {"teams": sorted(set(teams) | {t for ts in by_match.values() for t in ts})}))
    pnames = dict(q("players", {"pids": sorted(starters)})) if starters else {}
    ranks = {r[0]: {"rank": r[1], "points": r[2], "as_of": _as_date(r[3]).isoformat()} for r in q("ranks", {"teams": teams})}
    return {"overview": overview, "window": window, "maps": maps, "team_players": team_players,
            "starter_rows": starter_rows, "lineups": lineups, "names": names, "pnames": pnames, "ranks": ranks}


def build_hltv(rows):
    """ml_eval.Hltv built from load_rows() output - the same object ml_eval's tests ran on, restricted to these teams.
    Player rows are de-duplicated on (match, team, player, map) - the table's own key - before build()."""
    h = M.Hltv()
    for r in rows["overview"]:
        h.matches[r[0]] = _match_entry(*r)
    for r in rows["window"]:
        if r[0] not in h.matches:
            h.matches[r[0]] = _match_entry(*r)
    seen = {}
    for part in ("team_players", "starter_rows", "lineups"):
        for mid, tid, pid, mapid, rating in rows[part]:
            seen.setdefault((mid, tid, pid, mapid), (mid, tid, pid, rating))
    h.build(list(rows["maps"]), list(seen.values()))
    for tid, name in rows["names"].items():
        h.teams[tid] = name
    return h


# ---------------------------------------------------------------- evidence from one Hltv
def _no_team(key, name=None, why=None):
    if why is None:
        why = ("HLTV lists this team only on its schedule - no results yet" if str(key or "").startswith("name:")
               else "team not resolved to an HLTV team")
    return {"key": key, "hltv_id": None, "hltv_name": None, "name": name, "available": False, "reason": why,
            "starters": None, "lineup_rating": None, "form": None, "map_pool": None, "lan_online": None, "rank": None,
            "reasons": {"all": why}}


def _team_block(h, tid, key, day, pnames, ranks):
    reasons = {}
    roster = h.roster(tid, day)
    players = []
    for pid in roster:
        shrunk, n = h.player_rating(pid, day)                    # ml_eval feature (a), per player
        vals = [r for _, r in _window(h.player_rows.get(pid, []), day, ROSTER_DAYS)]
        team_maps = sum(1 for d, mid, p in _window(h.team_player_rows.get(tid, []), day, ROSTER_DAYS) if p == pid)
        players.append({"id": pid, "name": pnames.get(pid) or str(pid), "maps": n, "maps_for_team": team_maps,
                        "rating_raw": round(sum(vals) / len(vals), 3) if vals else None,
                        "rating_shrunk": round(shrunk, 4)})
    if roster:
        raw = [p["rating_raw"] for p in players if p["rating_raw"] is not None]
        shrunk_val = sum(h.player_rating(p, day)[0] for p in roster) / len(roster)   # exactly f_rating's per-team value
        lineup = {"shrunk": shrunk_val, "raw": (sum(raw) / len(raw)) if raw else None,
                  "maps": sum(p["maps"] for p in players), "players": len(roster),
                  "window_days": ROSTER_DAYS, "k": RATING_K}
    else:
        lineup = None
        reasons["starters"] = f"no HLTV maps for this team in the {ROSTER_DAYS} days before {day.isoformat()}"
    # form: last 10 series within 365 days (ml_eval feature (c))
    series = _window(h.team_series.get(tid, []), day, FORM_DAYS)
    last = series[-FORM_N:]
    if last:
        w = sum(1 for r in last if r[3])
        lst = []
        for d, mid, opp, won in reversed(last):
            m = h.matches.get(mid) or {}
            mw = m.get("maps_won") or {}
            lst.append({"date": d.isoformat(), "opponent": h.teams.get(opp) or str(opp), "opponent_id": opp,
                        "won": bool(won), "maps": f"{mw.get(tid, 0)}-{mw.get(opp, 0)}", "event": m.get("event"),
                        "lan": m.get("lan"), "hltv_match": mid})
        form = {"wins": w, "losses": len(last) - w, "n": len(last), "rate_shrunk": (w + 0.5 * FORM_K) / (len(last) + FORM_K),
                "series": lst, "window": f"last {FORM_N} series within {FORM_DAYS} days"}
    else:
        form = None
        reasons["form"] = f"no finished series in the {FORM_DAYS} days before {day.isoformat()}"
    # map pool: display (120 days, top 4 by maps played) + feature (b) (180 days, shrunk, ml_eval's definition)
    maps_disp = _window(h.team_maps.get(tid, []), day, MAP_DISPLAY_DAYS)
    cnt, won = Counter(), Counter()
    for _, mp, wn in maps_disp:
        cnt[mp] += 1
        won[mp] += bool(wn)
    top = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:MAPPOOL_TOP]
    top_disp = [{"map": _map_title(mp), "played": n, "won": won[mp], "win_rate": round(won[mp] / n, 4)} for mp, n in top]
    maps_feat = _window(h.team_maps.get(tid, []), day, MAPPOOL_DAYS)
    cf, wf = Counter(), Counter()
    for _, mp, wn in maps_feat:
        cf[mp] += 1
        wf[mp] += bool(wn)
    if cf:
        topf = [mp for mp, _ in sorted(cf.items(), key=lambda kv: (-kv[1], kv[0]))[:MAPPOOL_TOP]]
        n, wv = sum(cf[m] for m in topf), sum(wf[m] for m in topf)
        pool = {"top_maps_120d": top_disp, "feature_maps_180d": [_map_title(m) for m in topf], "feature_played": n,
                "feature_won": wv, "feature_rate_shrunk": (wv + 0.5 * MAPPOOL_K) / (n + MAPPOOL_K),
                "window": f"display: {MAP_DISPLAY_DAYS} days; signal: top {MAPPOOL_TOP} maps over {MAPPOOL_DAYS} days, "
                          f"shrunk toward 50% with k = {MAPPOOL_K}"}
    else:
        pool = None
        reasons["map_pool"] = f"no maps in the {MAPPOOL_DAYS} days before {day.isoformat()}"
    # LAN / online series record, 12 months (display only)
    rec = {"lan": [0, 0], "online": [0, 0], "unknown": [0, 0]}
    for d, mid, opp, wn in _window(h.team_series.get(tid, []), day, LAN_DAYS):
        lan = (h.matches.get(mid) or {}).get("lan")
        k = "unknown" if lan is None else ("lan" if lan else "online")
        rec[k][0 if wn else 1] += 1
    lan_online = {k: {"wins": v[0], "losses": v[1]} for k, v in rec.items()}
    lan_online["window_days"] = LAN_DAYS
    if not any(v[0] + v[1] for v in rec.values()):
        lan_online = None
        reasons["lan_online"] = f"no finished series in the {LAN_DAYS} days before {day.isoformat()}"
    rk = ranks.get(tid)
    rank = dict(rk, label="current rank") if rk else None
    if rank is None:
        reasons["rank"] = "not in HLTV's VRS ranking snapshot"
    return {"key": key, "hltv_id": tid, "hltv_name": h.teams.get(tid), "available": bool(roster or last or cf),
            "starters": players or None, "lineup_rating": lineup, "form": form, "map_pool": pool,
            "lan_online": lan_online, "rank": rank, "reasons": reasons}


def _h2h(h, ta, tb, day):
    """Head-to-head in 365 days counting only meetings where BOTH teams had >= 3 of today's starters (feature (e))."""
    ra, rb = set(h.roster(ta, day)), set(h.roster(tb, day))
    meetings, wa, wb = [], 0, 0
    for d, mid, opp, won in _window(h.team_series.get(ta, []), day, H2H_DAYS):
        if opp != tb:
            continue
        keep_a = len(h.lineups.get((mid, ta), set()) & ra)
        keep_b = len(h.lineups.get((mid, tb), set()) & rb)
        counted = keep_a >= H2H_KEEP and keep_b >= H2H_KEEP
        if counted:
            wa += bool(won)
            wb += not won
        mw = (h.matches.get(mid) or {}).get("maps_won") or {}
        meetings.append({"date": d.isoformat(), "winner": f"team:{ta if won else tb}", "maps_a": mw.get(ta, 0),
                         "maps_b": mw.get(tb, 0), "counted": counted, "kept_a": keep_a, "kept_b": keep_b,
                         "event": (h.matches.get(mid) or {}).get("event"), "hltv_match": mid})
    meetings.reverse()
    return {"a_wins": wa, "b_wins": wb, "n": wa + wb, "meetings": meetings, "window_days": H2H_DAYS,
            "rule": f"meetings where both teams had >= {H2H_KEEP} of today's 5 starters"}


def evidence_from(h, ka, kb, day, pnames=None, ranks=None, names=None):
    """Evidence for (ka, kb) at `day` from an ml_eval.Hltv (pure; no database).  Signals are ml_eval's own features."""
    pnames, ranks, names = pnames or {}, ranks or {}, names or {}
    ta, tb = hltv_id(ka), hltv_id(kb)
    blocks = {}
    for side, k, t in (("a", ka, ta), ("b", kb, tb)):
        blocks[side] = _team_block(h, t, k, day, pnames, ranks) if t is not None else _no_team(k, names.get(side))
    sig = {}
    if ta is not None and tb is not None:
        feats = {"rating": h.f_rating(ta, tb, day), "map_pool": h.f_mappool(ta, tb, day), "form": h.f_form(ta, tb, day)}
        e, n = h.f_h2h(ta, tb, day)
        feats["h2h"] = e if n else None
        per = {"rating": lambda b: (b["lineup_rating"] or {}).get("shrunk"),
               "map_pool": lambda b: (b["map_pool"] or {}).get("feature_rate_shrunk"),
               "form": lambda b: (b["form"] or {}).get("rate_shrunk")}
        for s in SIGNALS:
            x = feats[s]
            if x is None:
                sig[s] = None
                continue
            sig[s] = {"diff": x, "z": x / SCALE[s], "scale": SCALE[s], "feature": ML_EVAL_FEATURE[s],
                      "a": per[s](blocks["a"]) if s in per else None, "b": per[s](blocks["b"]) if s in per else None}
        h2h = _h2h(h, ta, tb, day)
    else:
        sig = {s: None for s in SIGNALS}
        h2h = None
    return {"model_version": MODEL_VERSION, "cutoff_day": day.isoformat(),
            "rule": "rows dated strictly before cutoff_day (the earlier of today UTC and the start date)",
            "teams": blocks, "head_to_head": h2h, "signals": sig, "ok": True, "source": "hltv", "computed_at": None}


def empty_evidence(ka, kb, day, why, names=None):
    names = names or {}
    ev = {"model_version": MODEL_VERSION, "cutoff_day": day.isoformat() if day else None,
          "rule": "rows dated strictly before cutoff_day (the earlier of today UTC and the start date)",
          "teams": {"a": _no_team(ka, names.get("a"), why if hltv_id(ka) is not None else None),
                    "b": _no_team(kb, names.get("b"), why if hltv_id(kb) is not None else None)},
          "head_to_head": None, "signals": {s: None for s in SIGNALS}, "ok": False, "source": None,
          "computed_at": None, "error": why}
    return ev


def orient(ev, ka, kb):
    """The evidence with team A = ka (a copy; signals negated when the stored order was the other way round)."""
    if ev is None:
        return None
    a = ev["teams"]["a"].get("key")
    if str(a or "?") == str(ka or "?") or str(ev["teams"]["b"].get("key") or "?") != str(ka or "?"):
        return dict(ev)
    out = dict(ev)
    out["teams"] = {"a": ev["teams"]["b"], "b": ev["teams"]["a"]}
    sig = {}
    for s, v in (ev.get("signals") or {}).items():
        sig[s] = None if v is None else dict(v, diff=-v["diff"], z=-v["z"], a=v.get("b"), b=v.get("a"))
    out["signals"] = sig
    hh = ev.get("head_to_head")
    if hh:
        out["head_to_head"] = dict(hh, a_wins=hh["b_wins"], b_wins=hh["a_wins"],
                                   meetings=[dict(m, maps_a=m["maps_b"], maps_b=m["maps_a"], kept_a=m["kept_b"],
                                                  kept_b=m["kept_a"]) for m in hh["meetings"]])
    return out


# ---------------------------------------------------------------- cache + loads
def _fresh(entry, now_ts):
    return bool(entry) and (now_ts - entry["at"] < EVIDENCE_TTL or now_ts < entry.get("retry_at", 0))


def _store_key(key):
    return (STORE_KEY,) + tuple(key)


def _prefill_stored(keys):
    """Keys with nothing in memory: the last good copy from disk (labelled), so a restart shows it at once."""
    for key in keys:
        with _LOCK:
            if key in _EV:
                continue
        stored = hltv_store.load(_store_key(key))
        if stored is not None:
            at, ev = stored
            ev = dict(ev, source="stored copy", stored_at=datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds"))
            with _LOCK:
                _EV.setdefault(key, {"at": 0.0, "ev": ev, "ok": False, "retry_at": 0.0})


def _compute(specs, now_ts=None):
    """Read and store evidence for canonical specs [(key, ka, kb, day)].  Never raises.  Returns (ok, error)."""
    now_ts = time.time() if now_ts is None else now_ts
    live = [s for s in specs if hltv_id(s[1]) is not None or hltv_id(s[2]) is not None]
    for key, ka, kb, day in specs:                   # neither team on HLTV: nothing to read
        if (key, ka, kb, day) not in live:
            ev = empty_evidence(ka, kb, day, None)
            ev.update(ok=True, source="no HLTV team ids",
                      computed_at=datetime.fromtimestamp(now_ts, timezone.utc).isoformat(timespec="seconds"))
            with _LOCK:
                _EV[key] = {"at": now_ts, "ev": ev, "ok": True, "retry_at": 0.0}
    if not live:
        return True, None
    _prefill_stored([s[0] for s in live])
    t0 = time.time()
    err = None
    try:
        if not hltv.dsn():
            raise hltv.Unavailable("HLTV database not configured")
        ids = {t for _, ka, kb, _ in live for t in (hltv_id(ka), hltv_id(kb)) if t is not None}
        con = hltv._connect()
        try:
            cur = con.cursor()
            rows = load_rows(cur, ids, [(hltv_id(ka), hltv_id(kb), day) for _, ka, kb, day in live],
                             deadline=t0 + LOAD_BUDGET_S)
        finally:
            con.close()
        h = build_hltv(rows)
        at_iso = datetime.fromtimestamp(time.time(), timezone.utc).isoformat(timespec="seconds")
        for key, ka, kb, day in live:
            ev = evidence_from(h, ka, kb, day, rows["pnames"], rows["ranks"])
            ev.update(computed_at=at_iso, source="hltv")
            with _LOCK:
                _EV[key] = {"at": time.time(), "ev": ev, "ok": True, "retry_at": 0.0}
            hltv_store.save(_store_key(key), ev)
        _STATE.update(loads=_STATE["loads"] + 1, pairs_loaded=_STATE["pairs_loaded"] + len(live))
    except Exception as e:                          # noqa: BLE001 - an outage serves the previous copy, never raises
        err = hltv.err_text(e) if not isinstance(e, _Budget) else str(e)
        _STATE.update(load_failures=_STATE["load_failures"] + 1)
        retry = time.time() + FAIL_TTL
        for key, ka, kb, day in live:
            with _LOCK:
                prev = _EV.get(key)
                if prev is not None:
                    ev = prev["ev"]
                    if prev["ok"] and ev.get("source") == "hltv":
                        ev = dict(ev, source="previous copy (HLTV read failed)", read_error=err)
                    _EV[key] = dict(prev, ev=ev, retry_at=retry)
                else:
                    _EV[key] = {"at": 0.0, "ev": empty_evidence(ka, kb, day, f"HLTV unavailable ({err})"),
                                "ok": False, "retry_at": retry}
    _STATE.update(last_secs=round(time.time() - t0, 2), last_refresh=_utcnow().isoformat(timespec="seconds"),
                  last_ok=err is None, last_error=err)
    return err is None, err


def evidence(team_a_key, team_b_key, start_iso, lan=None, now=None, names=None):
    """HLTV evidence for one pair, oriented to (team_a_key, team_b_key).  Cached per (pair, cutoff day) for 15 min; a
    miss reads the database (not for member request paths - use sides_for_board).  Never raises."""
    day = None
    try:
        day = cutoff_day(start_iso, now)
        key = _ckey(team_a_key, team_b_key, day)
        a, b = (team_a_key, team_b_key) if str(team_a_key or "?") <= str(team_b_key or "?") else (team_b_key, team_a_key)
        with _LOCK:
            entry = _EV.get(key)
        if not _fresh(entry, time.time()):
            if _LOAD.acquire(timeout=LOAD_WAIT):
                try:
                    with _LOCK:
                        entry = _EV.get(key)
                    if not _fresh(entry, time.time()):
                        _compute([(key, a, b, day)])
                finally:
                    _LOAD.release()
            with _LOCK:
                entry = _EV.get(key)
        if entry is None:
            ev = empty_evidence(team_a_key, team_b_key, day, "HLTV read busy - nothing cached yet", names)
        else:
            ev = orient(entry["ev"], team_a_key, team_b_key)
        ev = dict(ev, lan=lan)
        return _with_names(ev, names)
    except Exception as e:                          # noqa: BLE001
        return dict(empty_evidence(team_a_key, team_b_key, day, f"evidence failed: {type(e).__name__}", names), lan=lan)


def _with_names(ev, names):
    if not names:
        return ev
    ev = dict(ev, teams=dict(ev["teams"]))
    for s in ("a", "b"):
        if names.get(s):
            ev["teams"][s] = dict(ev["teams"][s], name=names[s])
    return ev


# ---------------------------------------------------------------- the pick (pure)
def _count_view(sig, side):
    """(view, counts) of the four signals relative to side 'a' / 'b'."""
    f = ag = lv = 0
    for s in SIGNALS:
        v = (sig or {}).get(s)
        if v is None:
            continue
        d = v["diff"] if side == "a" else -v["diff"]
        if d > 1e-12:
            f += 1
        elif d < -1e-12:
            ag += 1
        else:
            lv += 1
    n = f + ag + lv
    if n == 0:
        view = "no data"
    elif f * 2 > n:
        view = "agrees"
    elif ag * 2 > n:
        view = "disagrees"
    else:
        view = "mixed"
    return view, {"for": f, "against": ag, "level": lv, "available": n}


def _lineup(ev, t):
    return (((ev or {}).get("teams") or {}).get(t) or {}).get("lineup_rating") or {}


def composite(sig, ev=None):
    """(side 'a'/'b'/None, z, how): sign of the sum of the available scaled signals; tie (z == 0, or no two-team signal)
    -> the higher shrunk lineup rating, where a team with no HLTV maps in the 60-day window counts as RATING_PRIOR (what
    ml_eval's k = 20 shrinkage gives a lineup with zero maps); still level -> None (the caller decides).
    Frozen rule - see the module docstring.  ev (oriented like sig) is only read for that one-sided tie-break."""
    avail = [s for s in SIGNALS if (sig or {}).get(s) is not None]
    z = sum(sig[s]["z"] for s in avail) if avail else None
    if z is not None and z > 1e-12:
        return "a", z, "composite"
    if z is not None and z < -1e-12:
        return "b", z, "composite"
    r = (sig or {}).get("rating")
    if r is not None:
        if abs(r["diff"]) > 1e-12:
            return ("a" if r["diff"] > 0 else "b"), z, "composite level - higher shrunk lineup rating"
        return None, z, "composite level and equal lineup rating"
    ra, rb = _lineup(ev, "a").get("shrunk"), _lineup(ev, "b").get("shrunk")
    if (ra is None) != (rb is None):
        va, vb = (ra, RATING_PRIOR) if ra is not None else (RATING_PRIOR, rb)
        if abs(va - vb) > 1e-12:
            return ("a" if va > vb else "b"), z, "one-sided"
    if z is None:
        return None, None, "no comparable HLTV signal"
    return None, z, "composite level and no lineup rating to break the tie"


def _lines(ev, side, names):
    """Plain-word HLTV lines, side team first, each tagged as context."""
    if ev is None:
        return ["HLTV evidence not loaded yet"]
    s, o = side, ("b" if side == "a" else "a")
    S, O = names[s], names[o]
    sig = ev.get("signals") or {}
    tb = ev.get("teams") or {}
    out = []

    def direction(key):
        v = sig.get(key)
        d = v["diff"] if s == "a" else -v["diff"]
        return f"backs {S}" if d > 1e-12 else (f"favours {O}" if d < -1e-12 else "level")

    def blk(t, part):
        return (tb.get(t) or {}).get(part)

    if sig.get("rating") and blk(s, "lineup_rating") and blk(o, "lineup_rating"):
        ls, lo = blk(s, "lineup_rating"), blk(o, "lineup_rating")
        xs, xo = _pair(ls["shrunk"], lo["shrunk"])
        out.append(f"Lineup rating: {S} {xs} vs {O} {xo} ({ROSTER_DAYS} days, 5 starters, "
                   f"{ls['maps']} vs {lo['maps']} player-maps) - {direction('rating')} (context)")
    if sig.get("form") and blk(s, "form") and blk(o, "form"):
        fs, fo = blk(s, "form"), blk(o, "form")
        out.append(f"Form: {S} {fs['wins']}-{fs['losses']} vs {O} {fo['wins']}-{fo['losses']} (last {FORM_N} series) "
                   f"- {direction('form')} (context)")
    if sig.get("map_pool") and blk(s, "map_pool") and blk(o, "map_pool"):
        ps, po = blk(s, "map_pool"), blk(o, "map_pool")
        xs, xo = _pair(ps["feature_rate_shrunk"], po["feature_rate_shrunk"], pct=True)
        out.append(f"Map pool: {S} {xs} vs {O} {xo} on their "
                   f"top-{MAPPOOL_TOP} maps ({MAPPOOL_DAYS} days, {ps['feature_won']}-{ps['feature_played'] - ps['feature_won']} "
                   f"vs {po['feature_won']}-{po['feature_played'] - po['feature_won']}) - {direction('map_pool')} (context)")
    hh = ev.get("head_to_head")
    if hh is not None and (sig.get("h2h") is not None or not hh.get("n")):
        ws, wo = (hh["a_wins"], hh["b_wins"]) if s == "a" else (hh["b_wins"], hh["a_wins"])
        if hh["n"]:
            out.append(f"Head-to-head (same rosters, {H2H_DAYS} days): {S} {ws}-{wo} - {direction('h2h')} (context)")
        else:
            other = len(hh["meetings"])
            out.append(f"Head-to-head: no meeting in {H2H_DAYS} days with >= {H2H_KEEP} of today's starters on both sides"
                       + (f" ({other} older-roster meeting{'s' if other != 1 else ''} not counted)" if other else "")
                       + " (context)")
    lo_s, lo_o = (tb.get(s) or {}).get("lan_online"), (tb.get(o) or {}).get("lan_online")
    if lo_s or lo_o:
        def rec(x, k):
            return f"{x[k]['wins']}-{x[k]['losses']}" if x else "-"
        lan = ev.get("lan")
        order = ("lan", "online") if lan is not False else ("online", "lan")
        parts = [f"{'LAN' if k == 'lan' else 'online'} {S} {rec(lo_s, k)} vs {O} {rec(lo_o, k)}" for k in order]
        tonight = "" if lan is None else f"; tonight is {'LAN' if lan else 'online'}"
        out.append(f"Record (12 months): " + ", ".join(parts) + f"{tonight} (context, not counted)")
    rs, ro = (tb.get(s) or {}).get("rank"), (tb.get(o) or {}).get("rank")
    if rs or ro:
        asof = (rs or ro)["as_of"]
        out.append(f"Current VRS rank: {S} {('#' + str(rs['rank'])) if rs else 'unranked'} vs "
                   f"{O} {('#' + str(ro['rank'])) if ro else 'unranked'} (as of {asof}; context, not counted)")
    for t, nm in ((s, S), (o, O)):
        blk = tb.get(t) or {}
        if not blk.get("hltv_id"):
            out.append(f"No HLTV data for {nm}: {blk.get('reason') or 'not on HLTV'}")
        elif not blk.get("available"):
            out.append(f"No recent HLTV results for {nm}")
    if ev.get("source") not in (None, "hltv", "no HLTV team ids"):
        when = ev.get("stored_at") or ev.get("computed_at")
        out.append(f"HLTV figures from the {ev['source']}" + (f" ({when})" if when else ""))
    return out


def _names(match):
    t = (match or {}).get("teams") or {}
    a, b = t.get("a") or {}, t.get("b") or {}
    if isinstance(a, str):
        a = {"name": a}
    if isinstance(b, str):
        b = {"name": b}
    return {"a": a.get("name") or "Team A", "b": b.get("name") or "Team B"}, a.get("key"), b.get("key")


def pick(match, evidence, note=None, pending=True, tp=_RULE1, tp_policy=None):
    """The side for one moneyline.board() match.  evidence: evidence() output (any team order - it is re-oriented by
    team key), or None when not loaded.  tp: Thunderpick's block for the match (moneyline.tp_block; None = no
    Thunderpick event) -> the ml-sides-2 market input; left out -> exactly ml-sides-1.  Pure: no I/O.
    stake_units is always 0."""
    names, ka, kb = _names(match)
    ev = evidence
    if ev is not None:
        ea, eb = ev["teams"]["a"].get("key"), ev["teams"]["b"].get("key")
        if ka is None and kb is None:
            ka, kb = ea, eb
        ev = orient(ev, ka, kb)
    sig = (ev or {}).get("signals") or {}
    base = (match or {}).get("baseline") or {}
    mi = market_input(match, tp, tp_policy)
    pa = mi["prob_a"]
    from_tp = mi["source"] == "thunderpick"
    market = pa is not None and bool((match or {}).get("match_id"))
    out = {"model_version": mi["model_version"], "match_id": (match or {}).get("match_id"), "stake_units": 0,
           "started": bool((match or {}).get("started")),
           "side": None, "side_key": None, "side_name": None, "kind": None, "prob": None, "label": None,
           "reasons": [], "hltv_view": "no data", "hltv_counts": None, "basis": None, "composite": None,
           "market": None, "evidence_source": (ev or {}).get("source"), "cutoff_day": (ev or {}).get("cutoff_day"),
           "baseline_source": mi["source"] if market else None, "odds_policy": mi["odds_policy"]}
    hside, z, how = composite(sig, ev)
    out["composite"] = {"side": hside, "z": None if z is None else round(z, 4), "how": how,
                        "signals": {s: (None if sig.get(s) is None else round(sig[s]["z"], 4)) for s in SIGNALS},
                        "weights": "sign of the sum (frozen); weight 0 for the market side"}
    if market:
        pa = float(pa)
        plat = str(base.get("platform") or "market").capitalize()
        price = TP_PRICE if from_tp else f"{plat} midpoint"
        if abs(pa - 0.5) < 1e-9:
            side = hside or "a"
            prob = 0.5
            if hside:
                out["label"] = f"Lean {names[side]} (market level at 50.0% - side from HLTV evidence)"
                first = (f"Market level: {names['a']} 50.0% / {names['b']} 50.0% ({price}) - side from HLTV "
                         f"evidence ({how})")
            else:
                out["label"] = f"Lean {names[side]} (market level at 50.0%, HLTV level or unavailable - team A shown)"
                first = (f"Market level: {names['a']} 50.0% / {names['b']} 50.0% ({price}) - HLTV evidence "
                         f"level or unavailable ({how}), so canonical team A is shown; neither team is favoured")
        else:
            side = "a" if pa > 0.5 else "b"
            prob = pa if side == "a" else 1 - pa
            out["label"] = f"Lean {names[side]}"
            first = f"Market favourite: {names[side]} {prob * 100:.1f}% ({price})"
        out.update(kind="market", side=side, prob=round(prob, 4), basis=BASIS_MARKET_TP if from_tp else BASIS_MARKET,
                   market={"platform": "thunderpick" if from_tp else base.get("platform"), "prob_a": pa,
                           "side_prob": round(prob, 4),
                           "reason": (tp or {}).get("availability_label") if from_tp else base.get("reason")})
        lead = [first]
        ex = mi["exchange"]
        if from_tp and ex["prob_a"] is not None:       # the exchange price next to it, attributed (context, not the side)
            xs = ex["prob_a"] if side == "a" else 1 - ex["prob_a"]
            lead.append(f"Exchange comparison: {names[side]} {xs * 100:.1f}% "
                        f"({str(ex['platform'] or 'exchange').capitalize()} midpoint) - context")
        out["reasons"] = lead + _lines(ev, side, names)
        if ev is None:
            out["reasons"][-1] = "HLTV evidence not loaded yet - " + (note or "it is read in the background")
    else:
        why = base.get("reason") or base.get("label") or "no market found on Polymarket or Kalshi"
        if not (match or {}).get("match_id"):
            why = "match not linked to one canonical match id" + (f" ({match.get('link_reason')})"
                                                                    if (match or {}).get("link_reason") else "")
        nomkt = f"No usable market price ({why[:200]})"
        if mi["tp_why"] and (match or {}).get("match_id"):      # ml-sides-2: Thunderpick was looked at first
            nomkt = f"No usable market price (Thunderpick: {mi['tp_why']}; exchanges: {why[:200]})"
        if (match or {}).get("started"):
            nomkt = ("Match started - in-play prices are never used; the pre-match market favourite is in the shadow "
                     "log (ml_forecasts). " + nomkt)
        if ev is None:
            if pending:
                out.update(kind="pending", label=PENDING_LABEL, basis=BASIS_NONE,
                           reasons=[note or "HLTV evidence not loaded yet", nomkt])
            else:
                out.update(kind="none", label=NO_SIDE_LABEL, basis=BASIS_NONE,
                           reasons=[f"No side: {note or 'HLTV evidence not read'} and no usable market price", nomkt])
            return out
        side = hside
        if side is None and not any(sig.get(s) for s in SIGNALS):
            missing = [names[t] for t in ("a", "b") if not (ev["teams"][t].get("hltv_id") and ev["teams"][t].get("available"))]
            if not ev.get("ok") and ev.get("error"):
                why_none = f"{ev['error']} and nothing stored"
            else:
                why_none = ("no HLTV data for " + " or ".join(missing)) if missing else "no comparable HLTV signal"
            out.update(kind="none", label=NO_SIDE_LABEL, basis=BASIS_NONE,
                       reasons=[f"No side: {why_none} and no usable market price", nomkt] + _lines(ev, "a", names))
            return out
        if side is None:
            side = "a"
            how = f"{how} - canonical team A shown (no favourite)"
        out.update(kind="hltv-only", side=side, prob=None, basis=BASIS_HLTV,
                   label=f"Lean {names[side]} (HLTV only - untested, no market price)")
        if how == "one-sided":
            o = "b" if side == "a" else "a"
            known = side if _lineup(ev, side).get("shrunk") is not None else o
            unk = "b" if known == "a" else "a"
            first = (f"Only {names[known]} has HLTV data: lineup rating {names[known]} "
                     f"{_lineup(ev, known)['shrunk']:.2f} vs {names[unk]} {RATING_PRIOR:.2f} (no HLTV maps in "
                     f"{ROSTER_DAYS} days = the k = {RATING_K} prior) - tie-break of the frozen composite, untested")
        else:
            zz = z if side == "a" else -z
            first = f"HLTV composite favours {names[side]} (scaled sum {zz:+.2f}; {how}; untested)"
        out["reasons"] = [first] + _lines(ev, side, names) + [nomkt]
    out["side_name"] = names[side]
    out["side_key"] = ka if side == "a" else kb
    view, counts = _count_view(sig, side)
    out["hltv_view"], out["hltv_counts"] = view, counts
    return out


# ---------------------------------------------------------------- board
def _keys_for(match):
    """(team A key, team B key) for a board match: the board's own keys, else (unlinked) matchid's in-memory lookup."""
    names, ka, kb = _names(match)
    if ka and kb:
        return ka, kb
    try:
        import matchid
        if matchid.ready():
            ka = ka or matchid.team_key(names["a"])
            kb = kb or matchid.team_key(names["b"])
    except Exception:          # noqa: BLE001 - identity lookup never breaks the board
        pass
    return ka, kb


def _selected(board_matches, max_matches):
    """Indexes that get evidence: not-started matches first (board order), then started ones, at most max_matches."""
    idx = sorted(range(len(board_matches)), key=lambda i: (bool((board_matches[i] or {}).get("started")), i))
    return set(idx[:max_matches])


def _specs(board_matches, now, max_matches):
    out, seen = [], set()
    for i in sorted(_selected(board_matches, max_matches)):
        m = board_matches[i]
        ka, kb = _keys_for(m)
        day = cutoff_day(m.get("start"), now)
        key = _ckey(ka, kb, day)
        if key in seen:
            continue
        seen.add(key)
        a, b = (ka, kb) if str(ka or "?") <= str(kb or "?") else (kb, ka)
        out.append((key, a, b, day))
    return out


def refresh(board_matches, now=None, max_matches=MAX_MATCHES, wait=True):
    """Read evidence for every selected match whose copy is missing or older than 15 min - ONE bulk read.  Never raises.
    Returns {"pairs", "stale", "ok", "error", "secs"}."""
    t0 = time.time()
    try:
        specs = _specs(list(board_matches or []), now, max_matches)
        now_ts = time.time()
        with _LOCK:
            stale = [s for s in specs if not _fresh(_EV.get(s[0]), now_ts)]
        if not stale:
            return {"pairs": len(specs), "stale": 0, "ok": True, "error": None, "secs": 0.0}
        if not _LOAD.acquire(timeout=LOAD_WAIT if wait else 0):
            return {"pairs": len(specs), "stale": len(stale), "ok": False, "error": "another read is running", "secs": 0.0}
        try:
            with _LOCK:
                stale = [s for s in stale if not _fresh(_EV.get(s[0]), time.time())]
            ok, err = _compute(stale) if stale else (True, None)
        finally:
            _LOAD.release()
        return {"pairs": len(specs), "stale": len(stale), "ok": ok, "error": err, "secs": round(time.time() - t0, 2)}
    except Exception as e:      # noqa: BLE001
        return {"pairs": 0, "stale": 0, "ok": False, "error": f"{type(e).__name__}", "secs": round(time.time() - t0, 2)}


def _kick(board_matches, now=None):
    """Wake ONE background refresh (no-op while one runs).  Member requests never wait on it."""
    with _LOCK:
        if _STATE["busy"]:
            return False
        _STATE["busy"] = True
    rows = list(board_matches)

    def run():
        try:
            refresh(rows, now)
        finally:
            with _LOCK:
                _STATE["busy"] = False
    th = threading.Thread(target=run, name="ml-sides-refresh", daemon=True)
    _STATE["thread"] = th
    th.start()
    return True


def sides_for_board(board_matches, now=None, block=False, max_matches=MAX_MATCHES, detail=True, tp_blocks=None,
                    tp_policy=None):
    """One pick per board match, aligned with board_matches (moneyline.board() rows: teams.a/b.key + name, baseline,
    match_id).  block=False (member paths): memory only - missing or expired evidence wakes the single background
    refresh and the pick uses the previous / stored copy meanwhile.  block=True: one bulk read first (scripts, start()).
    detail=True attaches the oriented evidence to each pick under "evidence".  tp_blocks: Thunderpick's block per match
    (aligned; None = no event) -> every pick uses the ml-sides-2 market input; left out -> ml-sides-1.  Never raises."""
    rows = list(board_matches or [])
    if block:
        refresh(rows, now, max_matches)
    sel = _selected(rows, max_matches)
    now_ts = time.time()
    out, stale = [], False
    for i, m in enumerate(rows):
        try:
            names, _, _ = _names(m)
            ka, kb = _keys_for(m)
            tpk = {} if tp_blocks is None else {"tp": tp_blocks[i] if i < len(tp_blocks) else None,
                                                "tp_policy": tp_policy}
            if i not in sel:
                p = pick(m, None, note=f"HLTV evidence is read for the first {max_matches} matches only", pending=False,
                         **tpk)
                p["evidence"] = None
                out.append(p)
                continue
            day = cutoff_day(m.get("start"), now)
            key = _ckey(ka, kb, day)
            with _LOCK:
                entry = _EV.get(key)
            if not _fresh(entry, now_ts):
                stale = True
            ev = None
            if entry is not None:
                ev = _with_names(orient(entry["ev"], ka, kb), names)
                ev = dict(ev, lan=m.get("lan"))
            mm = m
            if not ((m.get("teams") or {}).get("a") or {}).get("key") and (ka or kb):
                mm = dict(m, teams={"a": {"key": ka, "name": names["a"]}, "b": {"key": kb, "name": names["b"]}})
            p = pick(mm, ev, **tpk)
            p["evidence"] = ev if detail else None
            out.append(p)
        except Exception as e:  # noqa: BLE001 - one bad row never breaks the board
            out.append({"model_version": MODEL_VERSION, "match_id": (m or {}).get("match_id"), "stake_units": 0,
                        "side": None, "side_key": None, "side_name": None, "kind": "error", "prob": None,
                        "label": "Side unavailable (internal error)", "reasons": [f"{type(e).__name__}"],
                        "hltv_view": "no data", "basis": None, "evidence": None})
    if stale and not block:
        try:
            _kick(rows, now)
        except Exception:       # noqa: BLE001
            pass
    return out


def forecast_row(m, tp, logged_iso, tp_policy=None):
    """The ml_forecasts row of the ml-sides-2 market input for one linked board match (moneyline._SCHEMA column order
    + baseline_source, odds_policy), or None for an unlinked / best-of-2 match, or when Thunderpick could not be READ
    this pass (a reader error is not "no event": an immutable row would record a false reason and flip the source -
    the next pass that reads it decides).  prob_a = Thunderpick's eligible probability for team A, else the
    ml-baseline-1 baseline, else None (available 0 - never 50%).  Pure."""
    if not (m or {}).get("match_id") or _draw_format(m):
        return None
    if isinstance(tp, dict) and tp.get("availability") == TP_READ_ERROR:
        return None
    mi = market_input(m, tp, tp_policy)
    t = (m.get("teams") or {})
    name = lambda s: (t.get(s) or {}).get("name") if isinstance(t.get(s), dict) else t.get(s)
    tpd = None
    if tp:
        tpd = {k: tp.get(k) for k in ("availability", "eligible", "p_a", "odds_a", "odds_b", "margin", "retrieved_at",
                                      "event_last_observed", "market", "policy")}
    why = {"baseline_source": mi["source"], "odds_policy": mi["odds_policy"], "thunderpick": tpd,
           "thunderpick_not_used": mi["tp_why"], "exchange": mi["exchange"]}
    return (m["match_id"], MODEL_VERSION_TP, logged_iso, mi["source"], mi["prob_a"], name("a"), name("b"), m.get("start"),
            int(mi["prob_a"] is not None), int(bool(m.get("disagreement"))), json.dumps(why, sort_keys=True, default=str),
            RULE_TP, mi["source"], mi["odds_policy"])


def start(board_fn, interval=REFRESH_EVERY):
    """Background loop (idempotent): every `interval` s, board_fn() -> refresh() (only stale pairs are read)."""
    with _LOCK:
        th = _STATE.get("loop")
        if th is not None and th.is_alive():
            return False

    def loop():
        while True:
            try:
                refresh(board_fn())
                _STATE["loop_error"] = None
            except Exception as e:      # noqa: BLE001
                _STATE["loop_error"] = f"{type(e).__name__}: {str(e)[:160]}"
            time.sleep(interval)
    th = threading.Thread(target=loop, name="ml-sides-loop", daemon=True)
    with _LOCK:
        _STATE["loop"] = th
    th.start()
    return True


def status():
    with _LOCK:
        n = len(_EV)
        ok = sum(1 for e in _EV.values() if e["ok"])
        fresh = sum(1 for e in _EV.values() if _fresh(e, time.time()))
        st = {k: v for k, v in _STATE.items() if k not in ("thread", "loop")}
    st.update(cached_pairs=n, cached_ok=ok, cached_fresh=fresh, model_version=MODEL_VERSION,
              model_version_tp=MODEL_VERSION_TP,
              loop_running=bool(_STATE.get("loop") and _STATE["loop"].is_alive()))
    return st


def _reset():
    """Tests: forget the in-memory cache and state (stored copies on disk stay)."""
    with _LOCK:
        _EV.clear()
        _STATE.update(busy=False, thread=None, last_refresh=None, last_ok=None, last_error=None, last_secs=None,
                      loads=0, load_failures=0, pairs_loaded=0)
