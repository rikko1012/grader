"""
Pre-match LoL forecasts - ONE record format, saved before the series starts, read by the board, the card, the match
table and the slip check (one displayed forecast everywhere). Settled later by the platform's rules.

For every archived offer that is linked to a canonical series and player (match.py), still open and before its series'
start, single-player, stat kills / assists / deaths and scope Map 1 / Maps 1-2 / Maps 1-3:
  inputs  the canonical history (lol/history.py, written by export.py) - the player's games by data-center id, the team's
          and the opponent's games by team id - all on days BEFORE the match day; the Elo win chance from the same rows
  model   lol/model.py project() with the TRAIN fit lol/model_fit.json (the form the A1 selection chose per stat);
          probabilities from its negative binomial; no fit = no forecast (said so), never a guess
  checks  format vs scope (Maps 1-2 = best-of-3, Maps 1-3 = best-of-5; a mismatch is flagged and kept out of slips);
          whether the model's preferred side is offered on that line
A new record is appended whenever the offer, the inputs or the fit change (lol_forecasts is append-only); the latest one
made before the series' start is the pre-match forecast. No grade and no stake are attached: the model has not been
validated at real lines (lol/EVALUATION.md); probabilities are model estimates.
"""
import hashlib
import math
import json
import os
import re
from datetime import datetime, timedelta, timezone

from . import rules, settle, store
from . import role_display

SCOPE = {"maps_1": "1", "maps_1_2": "12", "maps_1_2_3": "123"}
# the site's published grade bands and unit policy (the same numbers members see on CS2: A+ 80%, A 74%, A- 68%, B+ 63%,
# B 57%, B- 55%; units 3 / 2 / 1.5 / 1 / 0.5 / 0.5) - copied as published values, not imported (lol never imports CS2)
BANDS = ((0.80, "A+", 3.0), (0.74, "A", 2.0), (0.68, "A-", 1.5), (0.63, "B+", 1.0), (0.57, "B", 0.5), (0.55, "B-", 0.5))
LOL_STAKE_RESTRICTION = ("0U - LoL projections are not yet validated at real offered lines (needs 300 settled player-markets "
                         "across 60+ series, lol/EVALUATION.md); the grade is the model's, the stake is held at 0")


def grade_and_units(p):
    """Published bands on the chosen side's probability -> (grade, units by the published policy). Under 55%: Lean."""
    for cut, g, u in BANDS:
        if p >= cut:
            return g, u
    return "Lean", 0.0
SCOPE_LABEL = {"1": "Map 1", "12": "Maps 1-2", "123": "Maps 1-3"}
FORMAT = {"12": 3, "123": 5}
STATS = ("kills", "assists", "deaths")
ROLE_IN = {"Top": "top", "Jungle": "jungle", "Mid": "mid", "Bot": "bot", "Support": "support"}


def _model():
    from .. import model
    return model


# lol-1f (lol/EVALUATION.md A7 / A7.1, 2026-10-19): the owner's factor list, tested as increments over lol-1; only the stats
# whose combined factor model passed the registered rule carry a fit (kills, assists - deaths stays lol-1)
_FF = {"fit": None, "mtime": None, "index": None, "index_key": None}


FACTOR_FIT_FILE = "factor_fit.json"     # lol/factor_fit.json - read directly: the live worker never imports the study (scipy)


def factor_fit():
    from .. import config
    path = config.PKG_DIR / FACTOR_FIT_FILE
    try:
        mt = path.stat().st_mtime
    except OSError:
        return None
    if _FF["mtime"] != mt:
        try:
            _FF["fit"], _FF["mtime"] = json.loads(path.read_text(encoding="utf-8")), mt
        except (OSError, ValueError):
            _FF["fit"] = None
    return _FF["fit"]


def factor_index(h):
    """The A7 feature index for this cycle: every team's game results / lengths / kills (pace, opponent strength) and team
    damage totals, plus the full histories of the players on the teams of the open lines only (memory: never every
    player's history). The same features as the study (tested in lol/tests/test_factors_a7.py). Rebuilt only when the
    history or the set of teams changes."""
    from .. import factors
    teams_ = sorted(set(getattr(h, "factor_teams", ()) or ()) - {None})
    key = (h.since[:10], h.last_game, tuple(teams_), tuple(sorted(set(getattr(h, "factor_pids", ()) or ()) - {None})))
    if _FF["index_key"] != key:
        _FF["index"] = None
        q = h.db.query
        team_rows = q("SELECT DISTINCT game_key, match_key, game_number, team_id, team_kills, opp_kills, duration_s, win "
                      "FROM lol_player_games WHERE played_at IS NOT NULL AND played_at >= ?", (h.since,))
        team_dmg = {(r["game_key"], r["team_id"]): r["d"] or 0 for r in
                    q("SELECT game_key, team_id, SUM(damage) AS d FROM lol_player_games WHERE played_at >= ? GROUP BY game_key, team_id", (h.since,))}
        pids = set(getattr(h, "factor_pids", ()) or ())
        for i in range(0, len(teams_), 200):
            ch = teams_[i:i + 200]
            pids |= {r["player_id"] for r in q(f"SELECT DISTINCT player_id FROM lol_player_games WHERE team_id IN ({','.join('?' * len(ch))}) "
                                               "AND played_at >= ?", ch + [h.since])}
        rows, pl = [], sorted(pids - {None})
        cols = ("game_key, match_key, game_number, played_at, player_id, team, team_id, opponent, opponent_id, role, champion, kills, "
                "deaths, assists, team_kills, opp_kills, duration_s, damage, cs, vision, win, league")
        for i in range(0, len(pl), 200):
            ch = pl[i:i + 200]
            rows += q(f"SELECT {cols} FROM lol_player_games WHERE player_id IN ({','.join('?' * len(ch))}) AND played_at >= ?", ch + [h.since])
        games = {g["game_key"]: g for g in q("SELECT game_key, patch, tournament, league, played_at FROM lol_games WHERE played_at >= ?", (h.since,))}
        _FF["index"] = factors.Index(rows, games, match_day=h.md, team_rows=team_rows, team_dmg=team_dmg)
        _FF["index_key"] = key
    return _FF["index"]


# every factor of the owner's 2026-10-19 list that is not (or cannot be) in a grade, and why
NOT_PRE_MATCH = ("the draft: champions picked, composition and counter-picks (known only after the draft)",
                 "side for games 2+ (picked by the loser of the previous game)",
                 "gold / XP / CS difference at 15 minutes (not in the data yet)",
                 "patch strength of a champion (no champion table yet)", "travel, time zones and coaching (no source)")


def factor_panel(stat, feats, contrib, mult, fit_stat, team_win_):
    """What the card shows: every measured factor, its value and its effect on this projection (0 when it is not in the
    grade), the market win chance being logged, and what is not knowable before the match."""
    from .. import factors
    rows = []
    for k in factors.FEATS:
        eff = contrib.get(k)
        rows.append({"factor": factors.LABEL.get(k, k), "key": k, "value": round(feats.get(k, 0.0), 4),
                     "effect_pct": None if eff is None else round((math.exp(eff) - 1) * 100, 2)})
    rows.sort(key=lambda r: -abs(r["effect_pct"] or 0))
    return {"model": "lol-1f" if fit_stat else "lol-1", "multiplier": round(mult, 4),
            "status": ("in the grade (A7 test passed for this stat)" if fit_stat else
                       "measured, weight 0 (the A7 test did not pass for this stat)"),
            "factors": rows, "team_win": team_win_, "not_pre_match": list(NOT_PRE_MATCH)}


def fit_json():
    """The saved fit as written (selection per stat, and lol-2's parameters when it was selected)."""
    m = _model()
    try:
        return json.loads(m.FIT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fit_sha():
    m = _model()
    try:
        return hashlib.sha256(m.FIT_FILE.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def _when(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


# The history window the model was evaluated with (lol/EVALUATION.md A1: 8 months, 2026-02-16..10-16). Scout keeps
# every source record it receives (the full-history collector reaches back to 2025-01-01); live projections and Elo
# use the games of the last HISTORY_DAYS days, so more stored history never changes the evaluated model's inputs.
HISTORY_DAYS = int(os.environ.get("LOL_FORECAST_HISTORY_DAYS", "245"))


class History:
    """The canonical history for one forecast cycle: the rows of the players and teams on the open lines only, plus one
    compact row per game and team for the Elo (memory stays small on the server). Only games of the last HISTORY_DAYS
    days (the evaluated window)."""

    def __init__(self, db=None, pids=(), tids=(), now=None):
        from .. import elo, history
        self.db = db or history.DB(dsn="")
        now = now or datetime.now(timezone.utc)
        self.since = (now - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        game_rows = self.db.query("SELECT DISTINCT game_key, match_key, game_number, played_at, team, team_id, opponent, opponent_id, "
                                  "win FROM lol_player_games WHERE played_at IS NOT NULL AND played_at >= ?", (self.since,))
        md = {}
        for r in game_rows:
            d = _when(r["played_at"]).date().toordinal()
            md[r["match_key"]] = min(md.get(r["match_key"], d), d)
        self.md = md
        # keyed by the data center's team id, exactly as the evaluated / fitted Elo (backtest.Data, evaluate.lol2_samples):
        # one rating per canonical team (no name merges), and a team without earlier games is at BASE (audit 44)
        self.elo = elo.from_rows([dict(r, team=r.get("team_id") or r["team"], opponent=r.get("opponent_id") or r["opponent"])
                                  for r in game_rows], lambda r: md[r["match_key"]])
        self.last_game = max((r["played_at"] for r in game_rows), default=None)
        self.by_player, self.by_team = {}, {}
        pids, tids = sorted(set(pids) - {None}), sorted(set(tids) - {None})
        for i in range(0, len(pids), 200):
            chunk = pids[i:i + 200]
            for r in self.db.query(f"SELECT * FROM lol_player_games WHERE player_id IN ({','.join('?' * len(chunk))}) "
                                   "AND played_at >= ?", chunk + [self.since]):
                r["_day"] = md.get(r["match_key"])
                if r["_day"] is not None:
                    self.by_player.setdefault(r["player_id"], []).append(r)
        for i in range(0, len(tids), 200):
            chunk = tids[i:i + 200]
            seen = set()
            for r in self.db.query("SELECT DISTINCT game_key, match_key, team_id, team_kills, opp_kills FROM lol_player_games "
                                   f"WHERE team_id IN ({','.join('?' * len(chunk))}) AND played_at >= ?", chunk + [self.since]):
                k = (r["game_key"], r["team_id"])
                if k in seen or md.get(r["match_key"]) is None:
                    continue
                seen.add(k)
                r["_day"] = md[r["match_key"]]
                self.by_team.setdefault(r["team_id"], []).append(r)
        for v in list(self.by_player.values()) + list(self.by_team.values()):
            v.sort(key=lambda r: r["_day"])

    def inputs(self, pid, tid, opp, day):
        pin = [(r["_day"], r["kills"], r["deaths"], r["assists"], r["team_kills"], r["opp_kills"], r.get("win"))
               for r in self.by_player.get(pid, []) if r["_day"] < day and None not in (r["kills"], r["team_kills"], r["opp_kills"])]
        tin = [(r["_day"], r["team_kills"], r["opp_kills"]) for r in self.by_team.get(tid, [])
               if r["_day"] < day and r["team_kills"] is not None and r["opp_kills"] is not None]
        oin = [(r["_day"], r["team_kills"], r["opp_kills"]) for r in self.by_team.get(opp, [])
               if r["_day"] < day and r["team_kills"] is not None and r["opp_kills"] is not None]
        return pin, tin, oin

    def role(self, pid, day):
        rs = [r["role"] for r in self.by_player.get(pid, []) if r["_day"] < day and r.get("role")][-10:]
        return max(set(rs), key=rs.count) if rs else None

    def league(self, pid, day):
        rs = [r["league"] for r in self.by_player.get(pid, []) if r["_day"] < day][-5:]
        return rs[-1] if rs else None

    def p_win(self, tid, opp, day):
        return self.elo.p_win(tid, opp, day) if tid and opp else None

    league_of = None

    def series_league(self, con, s):
        """(league, tournament) of the series being forecast - the mapping the export gives that series' games, i.e. the
        league / tournament the A7 study's target carried (audit 15); (None, None) when the series has no page."""
        if not (s["tournament_page"] or ""):
            return None, None
        if self.league_of is None:
            from . import export
            self.league_of = export._league(con)
        return self.league_of(s)

    sim_states = None
    _all = None

    def all_rows(self):
        """Every history row (only when lol-2 is the selected model for some stat)."""
        if self._all is None:
            self._all = self.db.query("SELECT * FROM lol_player_games WHERE played_at IS NOT NULL AND played_at >= ?", (self.since,))
        return self._all


def side_offered(platform, sides, side):
    """Whether the feed offers `side` ('over' / 'under') on this line: True / False / None (not confirmed)."""
    key = "higher" if side == "over" else "lower"
    s = (sides or {}).get(key)
    if platform == "underdog":
        return bool(s) and (s.get("status") in (None, "active"))
    if s is None:
        return False
    return s.get("offered")


ONE_SIDED_LABEL = {"over": "Over only", "under": "Under only"}
ONE_SIDED_REASON = "the line offers one side only (Scout grades lines that offer both sides)"


def one_sided(platform, sides):
    """Owner 2026-11-06 (LoL os-1, as sports cap-2; scope "Underdog only"): "over" / "under" when an UNDERDOG line lists
    one option only, else None. A listed side whose status is suspended right now is still listed, so a two-sided line
    keeps its grade. PrizePicks lines (standard, goblin, demon) are never one-sided here: they keep today's grading,
    record and slips. Such a line is shown with its line and price and never graded, staked, put on a slip, priced or
    counted in the record."""
    if isinstance(sides, str):
        try:
            sides = json.loads(sides or "{}")
        except ValueError:
            sides = {}
    sides = sides if isinstance(sides, dict) else {}
    if platform == "underdog":
        listed = [k for k in ("higher", "lower") if sides.get(k)]
        return ("over" if listed[0] == "higher" else "under") if len(listed) == 1 else None
    return None                 # PrizePicks: owner 2026-11-06 "Underdog only" - unchanged


def shown(f):
    """THE chance members see for one saved forecast record (dict) - every LoL surface reads it here: board.json rows and
    match tables (member board, cards, compare, copy-pick and slip checks), slips, the slip line check, learning.
    The calibrated chance (lc-1, lol/EVALUATION.md A14) when the record carries one and LOL_CAL is not 0; else the raw
    chance. Side, offered-side check and grade (published BANDS, unchanged) follow from that chance."""
    from .. import cal
    raw = (f.get("p_over"), f.get("p_under"), f.get("p_push") or 0.0)
    v = f.get("cal_version")
    if v and cal.version_enabled(v) and f.get("cal_p_over") is not None and f.get("cal_p_under") is not None:
        po, pu, pp = f["cal_p_over"], f["cal_p_under"], f.get("cal_p_push") or 0.0
    else:
        (po, pu, pp), v = raw, None
    po, pu = float(po or 0.0), float(pu or 0.0)
    side = "over" if po >= pu else "under"
    if v and str(v).startswith("ec-1") and abs(po - pu) < 1e-3:
        # ec-1 never flips the model's side: a calibrated chance at its 50% floor stays on the side the raw chance picked
        side = f["model_side"] if f.get("model_side") in ("over", "under") else (
            "over" if float(raw[0] or 0) >= float(raw[1] or 0) else "under")
    if v is None and not f.get("cal_version") and f.get("model_side") in ("over", "under") and abs(po - pu) < 1e-3:
        # A15 audit: the record stores the chance rounded to 4 places; at an exact tie after rounding the side chosen
        # on the unrounded chance at save time is the side (record 1703: saved "Lean Under", re-read as "Lean Over")
        side = f["model_side"]
    sides = f.get("sides")
    if isinstance(sides, str):
        try:
            sides = json.loads(sides or "{}")
        except ValueError:
            sides = {}
    off = side_offered(f.get("platform"), sides, side)
    p_side = po if side == "over" else pu
    grade, units_policy = grade_and_units(p_side)
    rside = "over" if float(raw[0] or 0) >= float(raw[1] or 0) else "under"
    rgrade = grade_and_units(float((raw[0] if rside == "over" else raw[1]) or 0))[0]
    if (v is None and not f.get("cal_version") and f.get("grade") and f.get("model_side") == side
            and f.get("units_policy") is not None):
        # an uncalibrated record shows the grade it was saved with (graded on the unrounded chance)
        grade, units_policy = f["grade"], f["units_policy"]
    if f.get("model_side") == rside and f.get("grade") and not f.get("cal_version"):
        rgrade = f["grade"]
    one = one_sided(f.get("platform"), sides)
    label = f"Lean {side.title()}" if grade == "Lean" else grade
    if one:                     # os-1: shown with its line and price, never graded (the saved chances stay in the record row)
        grade, label, units_policy = None, ONE_SIDED_LABEL[one], 0.0
    return {"p_over": po, "p_under": pu, "p_push": float(pp), "side": side, "p_side": p_side,
            "side_offered": None if off is None else int(bool(off)), "grade": grade,
            "grade_label": label, "units_policy": units_policy, "cal": v, "one_sided": one,
            "raw": {"p_over": raw[0], "p_under": raw[1], "p_push": raw[2], "side": rside, "grade": rgrade}}


def _ec1_status():
    """ec-1 (gradecore/chance_cal.py) as the LoL board reports it: switch, artifact tag, LoL letters live / held."""
    try:
        from gradecore import chance_cal
        obj = chance_cal.load()
        d = ((obj or {}).get("games") or {}).get("lol") or {}
        return {"enabled": chance_cal.enabled(), "tag": chance_cal.tag(obj), "letters": d.get("letters"), "why": d.get("why")}
    except Exception as e:                               # noqa: BLE001 - the board never fails on this block
        return {"error": f"{type(e).__name__}"}


# lav-1 availability hold (owner 2026-11-09: "every projection accounts for ... whether a player is playing or not"). Not a
# projection factor: it decides whether a line is shown with a side / letter at all; the projection of a player who plays
# is unchanged. Held = his team fielded a full lineup after his last game for that team, without him (games before the
# match day only, A7.1 last_lineup). Missing data never holds (no stored game of his for the team, an incomplete lineup,
# his last game for the team older than LINEUP_RECENT_DAYS = an identity / coverage gap). A read failure keeps the line's
# previous hold (transient rule) and never creates a new one.
LINEUP_HOLD = "Not in the team's current lineup"
LINEUP_HOLD_VERSION = "lav-1"
LINEUP_RECENT_DAYS = 120
AVAIL_META = "avail_holds"
# lnk-1: a saved forecast is shown only while its line is still linked to that player and series. A definite roster /
# schedule answer from the linker hides it; a link that failed for missing data keeps it (transient rule).
LINK_LOST = {"player_not_on_team", "player_ambiguous", "series_ambiguous", "no_scheduled_series"}
LINK_LOST_TEXT = "Line no longer linked to this player's series"
LINK_CHANGED_TEXT = "Line now linked to another player or series"


def lineup_state(h, pid, tid, day):
    """-> ("held", info) | ("ok", None) | ("unknown", None). unknown = the lineup data could not be read this cycle."""
    if not pid or not tid:
        return "ok", None
    try:
        idx = factor_index(h)
        team = [x for x in idx.tm.get(tid, ()) if x[0] < day]
        lu = idx.last_lineup(tid, day)
        mine = [r["_day"] for r in (getattr(h, "by_player", None) or {}).get(pid, ())
                if r.get("team_id") == tid and r.get("_day") is not None and r["_day"] < day]
    except Exception:          # noqa: BLE001 - a read failure: unknown (the previous hold, if any, stands)
        return "unknown", None
    if not team or len(lu) < 5 or pid in lu.values() or not mine:
        return "ok", None
    his, team_last = max(mine), team[-1][0]
    if his >= team_last or day - his > LINEUP_RECENT_DAYS:
        return "ok", None
    from datetime import date as _d
    return "held", {"his_last_game": _d.fromordinal(his).isoformat(), "team_last_game": _d.fromordinal(team_last).isoformat(),
                    "team_games_since": sum(1 for x in team if x[0] > his)}


def avail_holds(con):
    """{"platform|market_key": hold} from the last completed forecast cycle (lav-1)."""
    try:
        return (store.get_meta(con, AVAIL_META) or {}).get("rows") or {}
    except Exception:          # noqa: BLE001
        return {}


def display_hold(con, r, f, holds):
    """Why a saved forecast must not be shown for this open line (None = show it). r: board row with link_status,
    link_reason, sid, pid; f: the latest saved forecast (dict) or None. Display only - no record is changed."""
    h = holds.get(f"{r.get('platform')}|{r.get('market_key')}")
    if h:
        return {"reason": LINEUP_HOLD, "v": LINEUP_HOLD_VERSION, "his_last_game": h.get("his_last_game"),
                "team_last_game": h.get("team_last_game")}
    if not f:
        return None
    st, why = r.get("link_status"), r.get("link_reason")
    if st and st != "matched":
        return {"reason": LINK_LOST_TEXT, "v": "lnk-1", "link_reason": why} if why in LINK_LOST else None
    if st == "matched":
        canon = lambda x: store.canonical_id(con, x) if x else x
        if (r.get("sid") and f.get("sid") and r["sid"] != f["sid"]) or (
                r.get("pid") and f.get("pid") and canon(r["pid"]) != canon(f["pid"])):
            return {"reason": LINK_CHANGED_TEXT, "v": "lnk-1"}
    return None


def forecast_one(con, h, offer, link, now=None, market=None):
    """-> forecast record dict (not saved) or {"skip": reason}. market(team, opponent, start) -> the match-winner price at
    this moment (lol/odds.match_odds) or None: saved with the record for forward evaluation only (A7), never an input.
    A player his team no longer fields (lav-1) -> {"skip": ..., "hold": {...}}: nothing new is saved and the board hides
    the line's saved forecast (before the lpf-1 block, so a held line never keeps an old lpf-1 grade on the board)."""
    m = _model()
    now = now or datetime.now(timezone.utc)
    stat, scope = offer.get("stat"), SCOPE.get(offer.get("scope") or "")
    if offer.get("market_type") != "single":
        return {"skip": "combined market (archived only)"}
    if stat not in STATS or not scope:
        return {"skip": f"market not modelled ({offer.get('stat_label')})"}
    s = con.execute("SELECT * FROM c_series WHERE sid = ?", (link["sid"],)).fetchone()
    if not s:
        return {"skip": "series not in the data center"}
    start = _when(s["scheduled_utc"] or s["first_game_utc"] or offer.get("start_time"))
    if start is None or start <= now:
        return {"skip": "series started - no new pre-match forecast"}
    if m.FIT is None or m.PARAMS is None:
        return {"skip": "no fitted model (lol/model_fit.json missing)"}
    day = start.date().toordinal()
    pid, tid, opp = store.canonical_id(con, link["pid"]), store.canonical_id(con, link["tid"]), store.canonical_id(con, link["opp_tid"])
    st_, info_ = lineup_state(h, pid, tid, day)
    prev_ = (getattr(h, "avail_prev", None) or {}).get(f"{offer.get('platform')}|{offer.get('market_key')}")
    if st_ == "unknown" and prev_:
        st_, info_ = "held", dict(prev_, kept=True)          # a read failure never lifts a hold
    if st_ == "held":
        return {"skip": "held: not in the team's current lineup",
                "hold": dict(info_ or {}, reason=LINEUP_HOLD, v=LINEUP_HOLD_VERSION, pid=pid, tid=tid, sid=link["sid"])}
    pin, tin, oin = h.inputs(pid, tid, opp, day)
    role = ROLE_IN.get(link.get("role")) or h.role(pid, day)
    if not role:
        return {"skip": "player role unknown"}
    p_win = h.p_win(tid, opp, day)
    league, tour = h.series_league(con, s)
    league = league or h.league(pid, day)          # the player's last league only when the series has none
    fj = getattr(h, "fit", None) or {}
    if (fj.get("selection") or {}).get(stat) == "lol-2" and fj.get("sim"):
        pj = _sim_projection(h, fj["sim"], pid, tid, opp, role, day, scope, stat, p_win, offer)
        if pj is None:
            return {"skip": "lol-2: a team's lineup is not known from earlier games"}
    else:
        pj = m.project(pin, tin, oin, role, day, scope, stat, p_win=p_win, league=league)
    fpanel = None
    ff = factor_fit()
    if ff and pj.get("form") in ("share", "rate"):
        from .. import factors
        fs = (ff.get("stats") or {}).get(stat)
        feats = factor_index(h).features(pid, tid, opp, role, day, league, p_win, tour or s["tournament"] or s["tournament_page"], ff["consts"])
        mult, contrib = factors.adjust(feats, fs)
        if fs:
            pj = dict(pj, version="lol-1f", lambda_lol1=pj["lambda"], factor_mult=mult)
            pj["lambda"] *= mult
            pj["lambda_game"] *= mult
            pj["sd"] = math.sqrt(pj["lambda"] + pj["lambda"] ** 2 / pj["nb_size"])
        fpanel = (feats, contrib, mult, fs)
    flags = []
    bo = s["best_of"]
    mix = None                  # A2 mixture parts [(weight, lambda, size)] when the line's chance comes from one
    if FORMAT.get(scope) and bo and bo != FORMAT[scope]:
        flags.append("format_scope_mismatch")
        if scope == "123" and bo == 3 and p_win is not None:       # A2: a mixture over 2 or 3 games
            p3 = 2 * p_win * (1 - p_win)
            lg = pj["lambda_game"]
            o3, u3, q3 = m.probs(3 * lg, m.R[(stat, "123")], offer["line"])
            o2, u2, q2 = m.probs(2 * lg, m.R[(stat, "12")], offer["line"])
            po, pu, push = p3 * o3 + (1 - p3) * o2, p3 * u3 + (1 - p3) * u2, p3 * q3 + (1 - p3) * q2
            mix = [(p3, 3 * lg, m.R[(stat, "123")]), (1 - p3, 2 * lg, m.R[(stat, "12")])]
        else:
            po, pu, push = m.probs(pj["lambda"], pj["nb_size"], offer["line"])
    else:
        po, pu, push = m.probs(pj["lambda"], pj["nb_size"], offer["line"])
    if pj["data_quality"] != "ok":
        flags.append(pj["data_quality"])
    if not oin:
        flags.append("opponent has no stored games (opponent factor = league average)")
    if mix:
        # audit 42: the projection, median and 80% range shown with the chance are those of the same 2-or-3-game
        # distribution (mean lambda_game x (2 + p3)); the 3-game total stays in the inputs
        lo, med, hi = _mix_quantiles(m, mix, (0.1, 0.5, 0.9))
        lam_shown = sum(w * lam for w, lam, _ in mix)
    else:
        lo, hi = m.interval(pj["lambda"], pj["nb_size"], 0.8)
        med = _nb_median(m, pj["lambda"], pj["nb_size"])
        lam_shown = pj["lambda"]
    # lpf-1 (live for kills + assists since 2026-11-09, owner "yes turn lpf on"; LOL_LPF1=0 = rollback to lol-1f; answers from
    # ONE short-lived child process, lol/lpf1_client.py): the full-history distribution started from the live per-game
    # projection above replaces the chance, the projection, the median and the 80% range for the stats it covers. The book
    # line is never an input. No lpf-1 answer this cycle (child failed / timed out / busy / artifact missing): when the
    # line's last saved forecast is an lpf-1 one at the same line it stands (a transient failure never changes a grade); a line with no
    # lpf-1 forecast yet gets the live lol-1f forecast above (labelled lol-1f) - never an error, never a hidden line.
    lp = getattr(h, "lpf1", None)
    lpf1_inputs = None
    r1 = None
    if lp is not None and stat in lp["stats"]:
        if lp.get("proj") is not None:
            t = lp["proj"].target(link["sid"], pid, tid, opp, role, day, league, tour or s["tournament"],
                                  bo or {"1": 1, "12": 3, "123": 5}[scope], s["tournament_page"])
            try:
                r1 = lp["proj"].project(t, stat, scope, pj["lambda_game"], offer["line"])
            except Exception:      # noqa: BLE001 - pending (pre-pass) / no answer this cycle
                r1 = None
        if r1 is None:
            if lp.get("prepass"):
                return {"skip": "lpf-1 request collected"}
            if _last_model(con, offer) == (lp["version"], offer["line"]):
                return {"skip": "lpf-1 answer unavailable this cycle (the last saved lpf-1 forecast stands)"}
    if r1 is not None:
        po, pu, push = r1["p_over"], r1["p_under"], r1["p_push"]
        lpf1_inputs = {"version": lp["version"], "artifact": lp["sha"], "cutoff": r1.get("cutoff", lp.get("cutoff")), "lambda_lol1f": round(lam_shown, 4),
                       "lambda_game_lol1f": round(pj["lambda_game"], 4), "p_win_series_model": round(r1["p_win"], 4),
                       "p_decider": round(r1["p_decider"], 4), "patch_assumed": r1.get("patch_assumed", t.get("patch"))}
        mix = None
        lam_shown, med, (lo, hi) = r1["mean"], r1["median"], r1["range80"]
        pj = dict(pj, version=lp["version"])
    so1 = None
    if r1 is None and pj.get("version") == "lol-1f" and stat in SO1_STATS:
        offs = so1_state(con, h)
        if offs is None and so1_on() and _last_model(con, offer) == ("lol-1f+" + SO1_VERSION, offer["line"]):
            return {"skip": "so-1 offsets unavailable this cycle (the last saved forecast stands)"}
        ev = so1_event(s["tournament_page"])
        c, npm = (offs or {}).get((ev, stat, scope), (0.0, 0))
        if c:
            k = math.exp(c)
            so1 = {"version": SO1_VERSION, "c": round(c, 4), "multiplier": round(k, 4), "player_markets": npm, "event": ev,
                   "lambda_before": round(lam_shown, 4)}
            if mix:
                mix = [(w, lam * k, sz) for w, lam, sz in mix]
                parts = [(w, m.probs(lam, sz, offer["line"])) for w, lam, sz in mix]
                po, pu, push = (sum(w * x[i] for w, x in parts) for i in range(3))
                lo, med, hi = _mix_quantiles(m, mix, (0.1, 0.5, 0.9))
                lam_shown = sum(w * lam for w, lam, _ in mix)
            else:
                lam2 = pj["lambda"] * k
                po, pu, push = m.probs(lam2, pj["nb_size"], offer["line"])
                lo, hi = m.interval(lam2, pj["nb_size"], 0.8)
                med = _nb_median(m, lam2, pj["nb_size"])
                lam_shown = lam2
            pj = dict(pj, version="lol-1f+" + SO1_VERSION, lambda_so1_before=pj["lambda"], **{"lambda": pj["lambda"] * k},
                      lambda_game=pj["lambda_game"] * k)
    # lc-1 (lol/EVALUATION.md A14): the chance members see, calibrated against the offered line - computed ONCE here, at
    # save time; the record keeps the raw chance (p_over / p_under / p_push) and adds the calibrated one + its version.
    # Side, grade, Lean label, offered-side check and slip reasons follow the shown chance (shown() below).
    from .. import cal
    po_x, pu_x = po, pu                    # unrounded: the raw grade is exactly as before lc-1
    po, pu, push = round(po, 4), round(pu, 4), round(push, 4)
    ca = cal.adjust({"stat": stat, "platform": offer["platform"], "line": offer["line"], "lambda": round(lam_shown, 4),
                     "nb_size": pj["nb_size"], "sides": offer.get("sides"), "odds_type": offer.get("odds_type"), "scope": scope,
                     "p_over": po, "p_under": pu, "p_push": push, "model_side": "over" if po_x >= pu_x else "under"})
    sh = shown({"platform": offer["platform"], "sides": offer.get("sides"), "p_over": po_x, "p_under": pu_x, "p_push": push,
                "model_side": "over" if po_x >= pu_x else "under",
                "cal_version": ca and ca["v"], "cal_p_over": ca and ca["po"], "cal_p_under": ca and ca["pu"],
                "cal_p_push": ca and ca["pp"]})
    side, offered, grade, units_policy = sh["side"], sh["side_offered"], sh["grade"], sh["units_policy"]
    rec = dict(sport=store.SPORT, platform=offer["platform"], market_key=offer["market_key"], line_id=offer.get("line_id"),
               quote_event_id=offer.get("_event_id"), quote_observed_at=offer.get("_observed_at"), stat=stat, scope=scope,
               market_type="single", odds_type=offer.get("odds_type"), line=offer["line"], sides=offer.get("sides"),
               sid=link["sid"], pid=pid, tid=tid, opp_tid=opp, role=role, best_of=bo, series_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
               model_version=pj.get("version") or m.VERSION, fit_sha=fit_sha(), form=pj.get("form"), **{"lambda": round(lam_shown, 4)},
               nb_size=pj["nb_size"], p_over=po, p_under=pu, p_push=push, model_side=side, side_offered=offered, grade=grade,
               grade_label=sh["grade_label"], units_policy=units_policy, units=0.0,
               cal_version=ca and ca["v"], cal_p_over=ca and ca["po"], cal_p_under=ca and ca["pu"], cal_p_push=ca and ca["pp"],
               stake_restriction=LOL_STAKE_RESTRICTION if units_policy > 0 else None,
               inputs={k: (round(v, 4) if isinstance(v, float) else v) for k, v in pj.items()
                       if k in ("lambda_game", "player_rate", "team_factor", "opp_factor", "strength_factor", "p_win",
                                "history_games", "history_weight", "data_quality", "role_prior", "form")} | {
                   "league": league, "team_games": len(tin), "opponent_games": len(oin), "history_last_game": h.last_game, "history_since": h.since, "data_version": _data_version(con),
                   "mean": round(lam_shown, 3), "median": med, "range80": [lo, hi], "provisional": _provisional(m),
                   "drivers": _drivers(pj), "unknowns": ["lineup changes after the forecast", "draft and champions",
                                                          "sides", "the tournament patch effect (not modelled)"]},
               flags=flags)
    j = {"kills": 1, "deaths": 2, "assists": 3}[stat]
    last = [r[j] for r in pin if r[j] is not None][-10:]
    rec["inputs"]["recent"] = {"games": len(last), "per_game": round(sum(last) / len(last), 2) if last else None}
    from . import prices
    rec["inputs"]["market"] = prices.classify(offer["platform"], offer)
    rec["inputs"]["team_win"] = team_win(p_win, offer, start, market)
    if ca:
        rec["inputs"]["calibration"] = {"version": ca["v"], "weights": ca["src"], "raw_side": sh["raw"]["side"],
                                        "raw_grade": sh["raw"]["grade"],
                                        "register": "gradecore/chance_cal.py (ec-1)" if str(ca["v"]).startswith("ec-1") else cal.REGISTER}
    if mix:
        rec["inputs"]["lambda_3_games"] = round(pj["lambda"], 4)
        rec["inputs"]["p_3_games"] = round(mix[0][0], 4)
    if lpf1_inputs:
        rec["inputs"]["lpf1"] = lpf1_inputs
    if so1:
        rec["inputs"]["scope_offset"] = so1
    if fpanel:
        rec["inputs"]["factors"] = factor_panel(stat, *fpanel, rec["inputs"]["team_win"])
        if pj.get("lambda_lol1") is not None:
            rec["inputs"]["lambda_lol1"] = round(pj["lambda_lol1"], 4)
    elig, why = slip_eligibility(rec)
    rec["slip_eligible"], rec["slip_reasons"] = int(elig), why
    return rec


def _sim_projection(h, params, pid, tid, opp, role, day, scope, stat, p_win, offer):
    """lol-2: simulate the whole match (both lineups as last fielded; the posted player in their role's slot) and sum
    the scope's games; the total as a negative binomial with the simulation's mean and variance."""
    from .. import sim
    if day not in h.sim_states:
        h.sim_states[day] = sim.state_until(h.all_rows(), params["hl"], day)
    ctx = sim.context(h.sim_states[day], params, tid, opp, p_win, day, posted={"pid": pid, "team": tid, "role": role})
    if ctx is None:
        return None
    n = {"1": 1, "12": 2, "123": 3}[scope]
    import zlib
    tot = sim.simulate(params, ctx, n, sims=4000, seed=zlib.crc32(f"{offer.get('market_key')}|{day}".encode()))
    x = tot[(pid, stat)]
    mean, var = float(x.mean()), float(x.var())
    size = mean * mean / (var - mean) if var > mean + 1e-9 else 1e6
    lp = [q for q in ctx["lineup_a"] if q["pid"] == pid]
    return {"version": "lol-2", "form": "lol-2 simulation", "lambda": mean, "lambda_game": mean / n, "nb_size": size,
            "player_rate": (lp[0]["kill_share"] if lp else None), "team_factor": None, "opp_factor": 1.0,
            "strength_factor": None, "p_win": p_win, "history_games": int(round((h.sim_states[day].p[pid].at(day, params["hl"])[0])))
            if pid in h.sim_states[day].p else 0, "history_weight": None,
            "data_quality": "ok", "role_prior": None,
            "sim": {"team_pace": [round(ctx["team_a"]["pace"], 3), round(ctx["team_b"]["pace"], 3)],
                    "duration_logs": [round(ctx["team_a"]["logdur"], 3), round(ctx["team_b"]["logdur"], 3)],
                    "sd": round(float(x.std()), 3)}}


def _nb_median(m, lam, size):
    c, k = 0.0, 0
    while k < 1000:
        c += m.nb_pmf(k, lam, size)
        if c >= 0.5:
            return k
        k += 1
    return k


def _mix_quantiles(m, mix, qs):
    """Quantiles (smallest k with cdf >= q) of a mixture of negative binomials [(weight, lambda, size)]."""
    out, c, k, i = [], 0.0, 0, 0
    while i < len(qs) and k <= 10000:
        c += sum(w * m.nb_pmf(k, lam, size) for w, lam, size in mix)
        while i < len(qs) and c >= qs[i]:
            out.append(k)
            i += 1
        k += 1
    return out + [k] * (len(qs) - len(out))


def _provisional(m):
    meta = (m.FIT_META or {}) if hasattr(m, "FIT_META") else {}
    return {"selection": (m.FIT[3] or {}).get("selection") if m.FIT else None, "basis": meta.get("train"),
            "note": "selected by the single final comparison (lol/EVALUATION.md A5, 2026-10-17)"}


def _drivers(pj):
    """The measured components of the projection (only what the fitted model actually uses)."""
    if pj.get("form") == "lol-2 simulation":
        s = pj.get("sim") or {}
        return [f"whole-match simulation: both teams' kill pace {s.get('team_pace')}, game length, win chance "
                f"{(pj.get('p_win') or 0.5):.0%}, the player's share of team kills {pj.get('player_rate') or 0:.2f}",
                f"simulated spread of the total: sd {s.get('sd')}"]
    if pj.get("form") == "baseline":
        return [f"player's per-game average shrunk toward the role ({pj.get('history_games')} earlier games)"]
    out = [f"player {'share of team output' if pj.get('form') == 'share' else 'per-game rate'} "
           f"{pj.get('player_rate'):.2f} ({pj.get('history_games')} earlier games)"]
    if pj.get("team_factor") is not None:
        out.append(f"team pace factor {pj['team_factor']:.2f}")
    out.append(f"opponent factor {pj.get('opp_factor'):.2f}")
    if pj.get("strength_factor") not in (None, 1.0):
        out.append(f"match-strength factor {pj['strength_factor']:.2f} (win chance {pj.get('p_win') or 0:.0%})")
    return out


def slip_eligibility(rec):
    """Can this forecast be a leg of an automatic slip? Every reason it cannot is listed."""
    why = []
    if rec["stat"] not in ("kills", "assists"):
        why.append(f"{rec['stat']} not offered as a slip leg")
    for name in ("scope", "player_active", "unplayed_game_in_scope", "push"):
        if not rules.rule(rec["platform"], name)["verified"]:
            why.append(f"{rec['platform']} rule not confirmed: {name}")
    if "format_scope_mismatch" in rec["flags"]:
        why.append("line scope does not match the series format")
    if one_sided(rec["platform"], rec.get("sides")):
        why.append(ONE_SIDED_REASON)
    elif rec["side_offered"] != 1:
        why.append("the model's side is not offered on this line" if rec["side_offered"] == 0 else "offered sides not confirmed by the feed")
    if any(f.startswith("no history") for f in rec["flags"]):
        why.append("no stored history for the player")
    if rec.get("units", 0) <= 0:
        why.append("stake 0U (LoL projections not yet validated at real lines)")
    return (not why), why


def slip_check(con, legs, now=None):
    """A proposed slip (forecast ids) -> eligible / reasons. Rules: one sport and one platform; each leg's exact line
    and offered side re-checked against the archive's latest state; no two legs from the same series (no dependence
    model for same-match LoL props); no player twice; every leg individually eligible."""
    now = now or datetime.now(timezone.utc)
    rows = [dict(r) for r in con.execute(f"SELECT * FROM lol_forecasts WHERE id IN ({','.join('?' * len(legs))})", legs)]
    why = []
    if len(rows) != len(legs):
        why.append("unknown forecast id")
    if len({r["platform"] for r in rows}) > 1:
        why.append("legs from more than one platform")
    if len({r["sid"] for r in rows}) < len(rows):
        why.append("two legs from the same series (no dependence model)")
    if len({r["pid"] for r in rows}) < len(rows):
        why.append("the same player twice")
    for r in rows:
        st = con.execute("SELECT e.line, e.sides, s.open, s.last_seen FROM market_state s JOIN market_events e ON e.id = s.last_event_id "
                         "WHERE s.platform = ? AND s.market_key = ?", (r["platform"], r["market_key"])).fetchone()
        if not st or not st["open"]:
            why.append(f"leg {r['id']}: line no longer offered")
        elif not _when(st["last_seen"]) or not -60 <= (now - _when(st["last_seen"])).total_seconds() <= 900:
            why.append(f"leg {r['id']}: line check missing, invalid or older than 15 minutes")
        elif st["line"] != r["line"]:
            why.append(f"leg {r['id']}: line moved {r['line']} -> {st['line']}")
        elif side_offered(r["platform"], json.loads(st["sides"] or "{}"), shown(r)["side"]) is not True:
            why.append(f"leg {r['id']}: {shown(r)['side']} no longer offered")
        if not _when(r["series_start"]):
            why.append(f"leg {r['id']}: series start missing")
        elif _when(r["series_start"]) <= now:
            why.append(f"leg {r['id']}: series started")
        if one_sided(r["platform"], r.get("sides")):
            why.append(f"leg {r['id']}: {ONE_SIDED_REASON}")
        elif not r["slip_eligible"]:
            why.append(f"leg {r['id']}: not eligible ({'; '.join(json.loads(r['slip_reasons'] or '[]'))})")
    return {"eligible": not why, "reasons": why, "legs": len(rows)}


def team_win(p_win, offer, start, market):
    """The team's pre-match win chance two ways (A7, 2026-10-19 owner: "including team win odds"): the Elo chance the model
    uses, and the match-winner market's price at this moment when one is open (Polymarket's two-outcome midpoint carries no
    bookmaker margin, so it is the market's win chance as is). Measurement only - judged forward before it may enter a grade."""
    out = {"elo": None if p_win is None else round(p_win, 4), "market": None}
    if market is None or not offer.get("team"):
        return out
    try:
        o = market(offer.get("team"), offer.get("opponent"), start.isoformat()) or {}
    except Exception as e:      # noqa: BLE001 - a price lookup never blocks a forecast
        out["market"] = {"error": type(e).__name__}
        return out
    if not o.get("found"):
        out["market"] = {"found": False}
        return out
    out["market"] = {"found": True, "p": o.get("team_prob"), "price_source": o.get("source"), "liquidity": o.get("liquidity"),
                     "flags": o.get("flags"), "market_id": o.get("market_id"), "retrieved_at": o.get("retrieved_at"),
                     "source": "polymarket"}
    return out


def _data_version(con):
    """The history export the forecast's inputs came from (time and size of the last export)."""
    e = store.get_meta(con, "last_export") or {}
    return {"export_at": e.get("at") or e.get("exported_at"), "games": e.get("games"), "player_rows": e.get("player_rows")}


def _sha(rec):
    keys = ("quote_event_id", "line", "sides", "lambda", "nb_size", "p_over", "p_under", "fit_sha", "flags", "series_start")
    if rec.get("cal_version"):          # lc-1: a calibration change saves a new record; uncalibrated records hash as before
        keys += ("cal_version", "cal_p_over")
    return store.sha({k: rec.get(k) for k in keys})


def _last_model(con, offer):
    """(model_version, line) of the offer's last saved forecast (None when it has none)."""
    try:
        r = con.execute("SELECT model_version, line FROM lol_forecasts WHERE platform = ? AND market_key = ? ORDER BY id DESC LIMIT 1",
                        (offer.get("platform"), offer.get("market_key"))).fetchone()
    except Exception:          # noqa: BLE001
        return None
    return (r[0], float(r[1]) if r[1] is not None else None) if r else None


# so-1 (LoL round 2, 2026-11-21, research/lol_r2_1121): a stat x scope offset on the lol-1f projection, re-estimated from
# the settled lines of the SAME event. Pre-registered T4b (walk-forward at real offered lines, playable_current): 54.9% ->
# 57.4%, +2.47 pts, day CI [+0.74, +4.51], series CI [+0.54, +4.35], days under 53% 9 -> 6 of 18 (passed gate b; Holm p
# 0.051 so not gate a). The event-scoped form shipped here was checked after registration (r2_t4b_scoped.py V2: lol-1f rows
# only, pool = same event settled before the forecast): +1.75 pts [+0.10, +3.45] series, 9 -> 7 bad days, Over share
# 54.5 -> 50.8. Letters-only universes show no gain. It FAILED on the lpf-1 replay (T4a, Under skew), so it applies ONLY
# when the shown forecast is lol-1f (LOL_LPF1=0 rollback, or no lpf-1 answer for a new line); lpf-1 output and its lol-1f
# inputs are never touched. A new event starts at no offset until SO1_MIN_PM of its player-markets settle per cell.
# Rollback LOL_SCOPE_OFFSET=0 (read every cycle).
SO1_VERSION = "so-1"
SO1_MIN_PM = 30
SO1_STATS = ("kills", "assists")
_SO1_LAST = {"offsets": None}           # last good offsets: a failed read keeps them, a failure is never cached


def so1_on():
    return os.environ.get("LOL_SCOPE_OFFSET", "1").strip() != "0"


def so1_event(tournament_page):
    """The event a series belongs to: its tournament page without the stage ('2026 Season World Championship/Main Event'
    and '.../Play-In' -> '2026 Season World Championship'); None without a page."""
    p = (tournament_page or "").strip()
    if not p:
        return None
    return p.rsplit("/", 1)[0] if "/" in p else p


SO1_SQL = (
    "SELECT f.platform, f.market_key, f.line, s.sid, s.pid, f.stat, f.scope, f.model_version, f.lambda, s.actual, cs.tournament_page, "
    "json_extract(f.inputs, '$.lpf1.lambda_lol1f') AS lam_lpf, json_extract(f.inputs, '$.scope_offset.lambda_before') AS lam_so1 "
    "FROM lol_forecasts f JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts "
    "WHERE forecast_at < series_start AND stat IN ('kills', 'assists') GROUP BY platform, market_key, line) z ON z.id = f.id "
    "JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements GROUP BY unit_key) lv ON "
    "lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
    "JOIN lol_settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v "
    "JOIN c_series cs ON cs.sid = s.sid "
    "WHERE s.status = 'graded' AND s.actual IS NOT NULL")


def so1_offsets(con):
    """{(event, stat, scope): (c, player_markets)}: c = log(sum actual / sum lol-1f projection) over the event's settled
    player-markets (the first line of each settlement series/player/stat/scope by unit key; the latest pre-match forecast of
    that line; its latest settlement, graded), when the cell has >= SO1_MIN_PM player-markets, else 0. The projection is
    the lol-1f one, never an offset one: the saved lambda of lol-1 / lol-1f rows (so-1 rows: the lambda before the offset),
    lpf-1 rows' inputs.lpf1.lambda_lol1f."""
    rows = [dict(x) for x in con.execute(SO1_SQL).fetchall()]
    cells, seen = {}, set()
    for r in sorted(rows, key=lambda r: f"{r['platform']}|{r['market_key']}|{r['line']}"):
        ev = so1_event(r["tournament_page"])
        pm = (r["sid"], r["pid"], r["stat"], str(r["scope"]))
        if ev is None or pm in seen:
            continue
        mv = str(r["model_version"] or "")
        if mv.startswith("lpf-1"):
            lam = r["lam_lpf"]
        elif mv.startswith("lol-1f") or mv == "lol-1":
            lam = r["lam_so1"] if r["lam_so1"] is not None else r["lambda"]
        else:
            lam = None
        if not isinstance(lam, (int, float)) or lam <= 0:
            continue
        seen.add(pm)
        c = cells.setdefault((ev, r["stat"], str(r["scope"])), [0.0, 0.0, 0])
        c[0] += float(r["actual"])
        c[1] += float(lam)
        c[2] += 1
    return {k: (math.log(v[0] / v[1]) if v[2] >= SO1_MIN_PM and v[0] > 0 and v[1] > 0 else 0.0, v[2]) for k, v in cells.items()}


def so1_state(con, h):
    """This cycle's so-1 offsets (computed once per history object); a read failure keeps the last good offsets. None =
    no offsets available (flag off, or a failure before any good read)."""
    if not so1_on():
        return None
    got = getattr(h, "_so1", None)
    if got is not None:
        return got
    try:
        got = so1_offsets(con)
        _SO1_LAST["offsets"] = got
    except Exception:          # noqa: BLE001 - a transient read failure never changes a grade
        got = _SO1_LAST["offsets"]
        if got is None:
            return None        # nothing good yet: the failure is not cached (the next cycle reads again)
    try:
        h._so1 = got
    except AttributeError:
        pass
    return got


def lpf1_on():
    """lpf-1 is live by default (owner 2026-11-09); LOL_LPF1=0 is the rollback switch (back to lol-1f)."""
    return os.environ.get("LOL_LPF1", "1").strip() != "0"


def lpf1_state(con, h, todo=(), now=None, market=None):
    """None when LOL_LPF1=0 (rollback: nothing about the forecast changes; the lpf-1 modules are never imported).
    On (the default since 2026-11-09): the lpf-1 answers for this cycle's lines, computed OUTSIDE this process (lol/lpf1_client.py: one short-lived child
    that builds the projector from the history database, answers, writes a small JSON and exits - the worker's memory does
    not grow). A pre-pass over the offers collects the lines lpf-1 must answer; answers are reused while the history version
    and the artifact are unchanged, so a quiet cycle starts no child. No answer (child failed / timed out / artifact
    missing / another child running) = a line whose last saved forecast is lpf-1 keeps it; any other line gets the live
    lol-1f forecast (forecast_one)."""
    if not lpf1_on():
        return None
    from .. import lpf1_client as C
    st = {"proj": None, "stats": C.live_stats(), "version": C.VERSION, "sha": C.artifact_sha(), "cutoff": None}
    if st["sha"] is None:
        st["error"] = "artifact missing"
        return st
    ver = (h.last_game, len(h.md), st["sha"])
    proj = C.RequestProjector(dict(C.answers_for(ver)))
    st["proj"] = proj
    pre = {"proj": proj, "stats": st["stats"], "version": st["version"], "sha": st["sha"], "cutoff": None, "prepass": True}
    h.lpf1 = pre
    for r in todo:
        try:
            offer = json.loads(r["data"])
            if offer.get("stat") not in st["stats"]:
                continue
            ln = offer.get("line")
            if not isinstance(ln, (int, float)) or isinstance(ln, bool) or not math.isfinite(ln):
                continue
            offer.update(platform=r["platform"], _event_id=r["event_id"], _observed_at=r["observed_at"])
            forecast_one(con, h, offer, dict(r), now, market)
        except Exception:      # noqa: BLE001 - a bad offer is counted by the main loop
            pass
    if proj.requests:
        ans, info = C.run_child(proj.requests, C.history_path(h.db), store.db_path())
        st["child"] = info
        C.remember(ver, ans)
        proj.answers.update(ans)                # this cycle: failures stay None (skip), never cached
        if info.get("error"):
            st["error"] = f"child: {info['error']}"[:300]
        proj.requests = {}
    return st


def run(con, db=None, now=None, market=None):
    """One forecast cycle over every open linked offer. Appends changed forecasts. Returns counts. market: see forecast_one."""
    counts = {}
    q = ("SELECT l.platform, l.market_key, l.sid, l.tid, l.opp_tid, l.pid, l.role, e.id AS event_id, e.observed_at, e.data "
         "FROM market_links l JOIN market_state s ON s.platform = l.platform AND s.market_key = l.market_key "
         "JOIN market_events e ON e.id = s.last_event_id WHERE l.status = 'matched' AND s.open = 1")
    todo = con.execute(q).fetchall()
    ids = lambda col: {store.canonical_id(con, r[col]) for r in todo}
    h = History(db, ids("pid"), ids("tid") | ids("opp_tid"), now=now)
    news = []
    try:
        h.fit, h.sim_states = fit_json(), {}
        h.factor_pids, h.factor_teams = ids("pid"), ids("tid") | ids("opp_tid")
        h.avail_prev = avail_holds(con)          # lav-1: the last cycle's holds (a read failure keeps a line's hold)
        holds = {}
        try:
            h.lpf1 = lpf1_state(con, h, todo, now, market) if todo else None
        except Exception as e:      # noqa: BLE001 - never stops the cycle: no lpf-1 answers this cycle (fallback rule in forecast_one)
            from .. import lpf1_client as C
            h.lpf1 = {"proj": None, "stats": C.live_stats(), "version": C.VERSION, "sha": None, "cutoff": None,
                      "error": f"{type(e).__name__}: {str(e)[:200]}"}
        if h.lpf1 and h.lpf1.get("error"):
            counts["lpf1_error"] = h.lpf1["error"]
        if h.lpf1 and h.lpf1.get("child"):
            counts["lpf1_child"] = h.lpf1["child"]
        for r in todo:
            # audit 12: one bad offer (a feed row without a usable line, an error in its inputs) is counted and skipped;
            # it never stops the other offers, the commit, settlement or the board
            try:
                offer = json.loads(r["data"])
                offer.update(platform=r["platform"], _event_id=r["event_id"], _observed_at=r["observed_at"])
                ln = offer.get("line")
                if not isinstance(ln, (int, float)) or isinstance(ln, bool) or not math.isfinite(ln):
                    counts["skip: no numeric line"] = counts.get("skip: no numeric line", 0) + 1
                    continue
                rec = forecast_one(con, h, offer, dict(r), now, market)
                if "skip" in rec:
                    counts["skip: " + rec["skip"]] = counts.get("skip: " + rec["skip"], 0) + 1
                    if rec.get("hold"):
                        holds[f"{r['platform']}|{r['market_key']}"] = rec["hold"]
                    continue
                sha = _sha(rec)
                last = con.execute("SELECT state_sha, sid, pid FROM lol_forecasts WHERE platform = ? AND market_key = ? ORDER BY id DESC LIMIT 1",
                                   (rec["platform"], rec["market_key"])).fetchone()
                # lnk-1: the same numbers under a new series / player link are saved again (append-only), so the saved
                # forecast always carries the line's current link (board display check, settlement)
                if last and last[0] == sha and last[1] == rec["sid"] and (
                        store.canonical_id(con, last[2]) if last[2] else last[2]) == rec["pid"]:
                    counts["unchanged"] = counts.get("unchanged", 0) + 1
                    continue
                cols = ("sport", "platform", "market_key", "line_id", "quote_event_id", "quote_observed_at", "stat", "scope", "market_type",
                        "odds_type", "line", "sides", "sid", "pid", "tid", "opp_tid", "role", "best_of", "series_start", "model_version",
                        "fit_sha", "form", "lambda", "nb_size", "p_over", "p_under", "p_push", "model_side", "side_offered", "inputs",
                        "flags", "slip_eligible", "slip_reasons", "grade", "grade_label", "units_policy", "units", "stake_restriction",
                        "cal_version", "cal_p_over", "cal_p_under", "cal_p_push")
                vals = [json.dumps(rec[c], default=str) if c in ("sides", "inputs", "flags", "slip_reasons") else rec[c] for c in cols]
                saved_at = store.now_iso()
                cursor = con.execute(f"INSERT INTO lol_forecasts (forecast_at, state_sha, {', '.join(cols)}) VALUES (?, ?, {', '.join('?' * len(cols))})",
                            [saved_at, sha] + vals)
                if os.getenv('SCOUT_GRADECORE_SHADOW', '0') == '1':
                    from gradecore.capture import capture
                    capture(con, 'lol', rec, cursor.lastrowid, saved_at)
                counts["saved"] = counts.get("saved", 0) + 1
                news.append((saved_at, rec))
            except Exception as e:      # noqa: BLE001
                kk = f"{r['platform']}|{r['market_key']}"
                if kk in h.avail_prev:              # a failed offer keeps its hold (transient rule)
                    holds[kk] = h.avail_prev[kk]
                k = f"error: {type(e).__name__}"
                counts[k] = counts.get(k, 0) + 1
                errs = counts.setdefault("_errors", [])
                if len(errs) < 5:
                    errs.append({"market": f"{r['platform']}|{r['market_key']}", "error": f"{type(e).__name__}: {str(e)[:200]}"})
        store.set_meta(con, AVAIL_META, {"at": store.now_iso(), "v": LINEUP_HOLD_VERSION, "rows": holds})
        if holds:
            counts["held"] = len(holds)
        con.commit()
        counts.update(archive_news(news))
    finally:
        if db is None:
            h.db.close()
    return counts


def news_path():
    return os.environ.get("PREGAME_NEWS_LOL_DB") or str(store.db_path().parent / "pregame_news.db")


def archive_news(saved):
    """pgn-1 (pregame_news/): next to every saved forecast, the posted player's team + role and the lineup each team
    fielded in its last game before the match day (the lineup the projection assumes) - change-only, append-only, observed
    at the forecast's save time. Research input only; a failure never touches the forecasts (already committed)."""
    if not saved:
        return {}
    try:
        import pregame_news as core
        if not core.enabled():
            return {}
        idx = _FF.get("index")
        con = core.connect(news_path())
        n = 0
        try:
            for at, rec in saved:
                ev, start = rec.get("sid") or "", rec.get("series_start")
                at_z = (at or "")[:19] + "Z" if at else None
                r = core.observe(con, "lol", "lol", "player_role", "series", {(rec["pid"], ev): {
                    "team": rec.get("tid"), "opponent": rec.get("opp_tid"), "role": rec.get("role"),
                    "best_of": rec.get("best_of")}}, observed_at=at_z, source="lol data center", start_at=start, touch=False)
                n += r.get("new", 0) + r.get("changed", 0)
                if idx is not None and start:
                    day = datetime.fromisoformat(start.replace("Z", "+00:00")).date().toordinal()
                    for team in (rec.get("tid"), rec.get("opp_tid")):
                        if team:
                            lu = idx.last_lineup(team, day)
                            r = core.observe(con, "lol", "lol", "lineup_assumed", "series", {(team, ev): {
                                "lineup": dict(sorted(lu.items())) if lu else None, "basis": "last game fielded"}},
                                observed_at=at_z, source="lol data center", start_at=start, touch=False)
                            n += r.get("new", 0) + r.get("changed", 0)
        finally:
            con.close()
        return {"news_rows": n} if n else {}
    except Exception as e:      # noqa: BLE001
        return {"news_error": f"{type(e).__name__}: {str(e)[:120]}"}


def settle_all(con):
    """Settle every pre-match forecast whose series is complete (the latest forecast made before the series start, per
    platform / market / line). Versioned: an upstream correction becomes a new settlement version."""
    out = {}
    # audit 13: a settlement changes only when the canonical tables (a build) or the settlement rules / code change, so
    # the full pass runs once per such change; in between only units without a recorded settlement are tried
    key = store.sha([store.get_meta(con, "built_at"), rules.RULES, _settle_code()])
    full = store.get_meta(con, "settle_pass") != key
    # latest status per settled unit; the team check (link_mismatch) applies to new units and to units already
    # link_mismatch (so the owner-approved clean-up of the old DNPs is never undone by a later full pass)
    latest = {u: st for u, st in con.execute("SELECT x.unit_key, x.status FROM lol_settlements x JOIN (SELECT unit_key, "
                                              "MAX(version) AS v FROM lol_settlements GROUP BY unit_key) z ON z.unit_key = x.unit_key "
                                              "AND z.v = x.version")}
    done = set() if full else set(latest)
    rows = con.execute("SELECT f.* FROM lol_forecasts f JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts "
                       "WHERE forecast_at < series_start GROUP BY platform, market_key, line) z ON z.id = f.id "
                       "JOIN c_series s ON s.sid = f.sid WHERE s.status = 'complete'").fetchall()
    for f in rows:
        unit = f"{f['platform']}|{f['market_key']}|{f['line']}"
        if unit in done:
            out["unchanged"] = out.get("unchanged", 0) + 1
            continue
        # the team check (link_mismatch, 2026-11-09): units settled from now on; a unit that already has another status
        # keeps its rule (the 17 old DNP rows change only through the owner-approved clean-up, fix_dnp_links)
        res = settle.record(con, unit, f["platform"], f["stat"], "maps_" + "_".join(f["scope"]), store.canonical_id(con, f["pid"]),
                            f["sid"], f["line"], tid=f["tid"] if latest.get(unit, "link_mismatch") == "link_mismatch" else None)
        out[res["status"]] = out.get(res["status"], 0) + 1
    if full:
        store.set_meta(con, "settle_pass", key)
        con.commit()
    return out


def _settle_code():
    try:
        return hashlib.sha256(open(settle.__file__, "rb").read()).hexdigest()[:16]
    except OSError:
        return None


_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _ud_ids(r):
    """Underdog's ids that open this line in Underdog (display only, never a forecast input): the market id from the
    market key ("ud:<over_under_id>|<line_type>", a uuid or nothing), the match id (digits) and match type. Offers seen
    since 2026-10-29 carry the match type in their event; an offer open before then whose state never changed has no new
    event, so a missing type with a known match id is Underdog's "Game" (every LoL appearance seen; review 2026-10-29)."""
    k = str(r.get("market_key") or "")
    ou = k[3:].split("|")[0].lower() if k.startswith("ud:") else ""
    m = str(r.get("match_source_id") or "")
    mid = m if m.isdigit() and len(m) <= 12 else None
    mt = r.get("ud_match_type") if isinstance(r.get("ud_match_type"), str) and r.get("ud_match_type") else None
    return {"ud_over_under_id": ou if _UUID.fullmatch(ou) else None, "ud_match_id": mid,
            "ud_match_type": mt if mt is not None else ("Game" if mid else None)}


def board(con, now=None):
    """The admin board: every open LoL offer with its latest forecast (or why there is none), the match tables (identical
    markets side by side) and each forecast's card data. Also the settled forecasts of the last 3 days."""
    now = now or datetime.now(timezone.utc)
    label = {r[0]: r[1] for r in con.execute("SELECT eid, label FROM entities")}
    lab = lambda e: label.get(store.canonical_id(con, e)) if e else None
    series = {r["sid"]: dict(r) for r in con.execute("SELECT * FROM c_series")}
    holds = avail_holds(con)
    rows = []
    q = ("SELECT s.platform, s.market_key, s.last_seen AS checked_at, e.id AS event_id, e.observed_at, e.player, e.team, e.opponent, e.start_time, e.stat, e.scope, "
         "e.stat_label, e.market_type, e.odds_type, e.line, e.line_id, e.match_source_id, json_extract(e.data, '$.extra.match_type') AS ud_match_type, "
         "e.sides, l.status AS link_status, l.reason AS link_reason, l.sid, l.pid, "
         "l.tid, l.opp_tid, l.role FROM market_state s JOIN market_events e ON e.id = s.last_event_id LEFT JOIN market_links l ON "
         "l.platform = s.platform AND l.market_key = s.market_key WHERE s.open = 1 ORDER BY e.start_time, e.player")
    for r in con.execute(q).fetchall():
        r = dict(r)
        f = con.execute("SELECT * FROM lol_forecasts WHERE platform = ? AND market_key = ? ORDER BY id DESC LIMIT 1",
                        (r["platform"], r["market_key"])).fetchone()
        f = dict(f) if f else None
        hold = display_hold(con, r, f, holds)      # lav-1 / lnk-1: the line is shown with no forecast (no side or letter)
        if hold:
            f = None
        sh = shown(f) if f else {}
        s = series.get(r.get("sid")) or {}
        rows.append({
            "platform": r["platform"], "market_key": r["market_key"], "player": r["player"], "team": lab(r.get("tid")) or r["team"],
            # role tag (display only, owner 2026-11-07): the linked role, else the latest forecast's (game data); Bot reads "ADC"
            "role": role_display.label(r.get("role"), (f or {}).get("role") if r.get("pid") else None), "opponent": lab(r.get("opp_tid")) or r["opponent"],
            "competition": s.get("tournament") or (s.get("tournament_page") or "").replace("LE:", ""), "best_of": s.get("best_of"),
            "series_start": s.get("scheduled_utc") or r["start_time"], "stat": r["stat"], "scope": SCOPE_LABEL.get(SCOPE.get(r["scope"] or ""), r["stat_label"]),
            "stat_label": r["stat_label"], "market_type": r["market_type"], "odds_type": r["odds_type"], "line": r["line"],
            "line_id": r["line_id"],          # the platform's own id of the CURRENT offer (PrizePicks: the projection id)
            **(_ud_ids(r) if r["platform"] == "underdog" else {}),
            "sides": json.loads(r["sides"] or "{}"), "quote_seen_at": r["observed_at"], "quote_checked_at": r["checked_at"],
            "link": {"status": r.get("link_status"), "reason": r.get("link_reason"), "sid": r.get("sid"), "pid": r.get("pid")},
            "forecast": None if not f else {
                # the shown chance (lc-1 when on, else raw) - side, grade and offered check follow it; raw kept for admin
                "id": f["id"], "at": f["forecast_at"], "line": f["line"], "projection": f["lambda"],
                # the saved NB spread (display: the workspace reads another book's line from this same distribution)
                "nb_size": f["nb_size"] if "nb_size" in f.keys() else None, "p_over": sh["p_over"],
                "p_under": sh["p_under"], "p_push": sh["p_push"], "model_side": sh["side"], "side_offered": sh["side_offered"],
                "model": f"{f['model_version']} ({f['form']}, fit {f['fit_sha']})", "inputs": json.loads(f["inputs"] or "{}"),
                "flags": json.loads(f["flags"] or "[]"), "slip_eligible": bool(f["slip_eligible"]),
                "slip_reasons": json.loads(f["slip_reasons"] or "[]"), "current": f["line"] == r["line"],
                "grade": sh["grade_label"], "units": f["units"], "units_policy": sh["units_policy"],
                "stake_restriction": LOL_STAKE_RESTRICTION if sh["units_policy"] > 0 else None,
                "calibration": sh["cal"], "raw": sh["raw"], "one_sided": sh["one_sided"]},
            "grade": sh.get("grade_label"), "stake": (f or {}).get("units"), "hold": hold})
    tables = {}
    for x in rows:
        if x["link"]["sid"]:
            t = tables.setdefault(x["link"]["sid"], {"sid": x["link"]["sid"], "teams": None, "competition": x["competition"],
                                                      "best_of": x["best_of"], "start": x["series_start"], "markets": {}})
            key = f"{x['link']['pid'] or x['player']}|{x['stat']}|{x['scope']}|{x['market_type']}"    # exact: same player id
            t["markets"].setdefault(key, {"player": x["player"], "team": x["team"], "role": x["role"], "stat": x["stat"],
                                          "scope": x["scope"], "market_type": x["market_type"], "lines": []})["lines"].append(
                {"platform": x["platform"], "odds_type": x["odds_type"], "line": x["line"], "sides": x["sides"],
                 "checked_at": x["quote_checked_at"], "projection": (x["forecast"] or {}).get("projection"),
                 "p_over": (x["forecast"] or {}).get("p_over"), "p_under": (x["forecast"] or {}).get("p_under"),
                 "p_push": (x["forecast"] or {}).get("p_push"),
                 "model_side": (x["forecast"] or {}).get("model_side"), "grade": (x["forecast"] or {}).get("grade"),
                 "one_sided": (x["forecast"] or {}).get("one_sided"),
                 "current": (x["forecast"] or {}).get("current"),
                 "market": ((x["forecast"] or {}).get("inputs") or {}).get("market")})
    settled = [dict(r) for r in con.execute("SELECT * FROM lol_settlements WHERE settled_at >= ? ORDER BY id DESC LIMIT 500",
                                            (store.now_iso(now.timestamp() - 3 * 86400),))]
    feeds = {r[0]: r[1] for r in con.execute("SELECT platform, MAX(polled_at) FROM market_polls WHERE ok = 1 GROUP BY platform")}
    from .. import cal
    cw, csrc = cal.load()
    return {"at": store.now_iso(), "released_to_members": False, "feeds": feeds,
            "calibration": {"version": cal.VERSION, "enabled": cal.enabled(), "weights": csrc, "sha": (cw or {}).get("sha"),
                            "on": sorted(k for k, p in ((cw or {}).get("params") or {}).items() if p.get("on")),
                            "register": cal.REGISTER, "ec1": _ec1_status()},
            "grades": "model grades from the published bands; stakes held at 0U until validated at real lines",
            "rows": rows, "match_tables": list(tables.values()), "settled_recent": settled,
            "counts": {"offers": len(rows), "with_forecast": sum(1 for x in rows if x["forecast"]),
                       "held": sum(1 for x in rows if x["hold"]),
                       "linked": sum(1 for x in rows if x["link"]["status"] == "matched")}}
