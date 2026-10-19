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
from datetime import datetime, timedelta, timezone

from . import rules, settle, store
from .names import ROLE_LABEL

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


def factor_fit():
    from .. import factor_study
    try:
        mt = factor_study.FIT_FILE.stat().st_mtime
    except OSError:
        return None
    if _FF["mtime"] != mt:
        try:
            _FF["fit"], _FF["mtime"] = json.loads(factor_study.FIT_FILE.read_text(encoding="utf-8")), mt
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
        game_rows = self.db.query("SELECT DISTINCT game_key, match_key, game_number, played_at, team, team_id, opponent, win "
                                  "FROM lol_player_games WHERE played_at IS NOT NULL AND played_at >= ?", (self.since,))
        md = {}
        for r in game_rows:
            d = _when(r["played_at"]).date().toordinal()
            md[r["match_key"]] = min(md.get(r["match_key"], d), d)
        self.md = md
        self.elo = elo.from_rows(game_rows, lambda r: md[r["match_key"]])
        self.label = {r["team_id"]: r["team"] for r in game_rows}
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
        a, b = self.label.get(tid), self.label.get(opp)
        return self.elo.p_win(a, b, day) if a and b else None

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


def forecast_one(con, h, offer, link, now=None, market=None):
    """-> forecast record dict (not saved) or {"skip": reason}. market(team, opponent, start) -> the match-winner price at
    this moment (lol/odds.match_odds) or None: saved with the record for forward evaluation only (A7), never an input."""
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
    pin, tin, oin = h.inputs(pid, tid, opp, day)
    role = ROLE_IN.get(link.get("role")) or h.role(pid, day)
    if not role:
        return {"skip": "player role unknown"}
    p_win = h.p_win(tid, opp, day)
    league = h.league(pid, day)
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
        feats = factor_index(h).features(pid, tid, opp, role, day, league, p_win, s["tournament"] or s["tournament_page"], ff["consts"])
        mult, contrib = factors.adjust(feats, fs)
        if fs:
            pj = dict(pj, version="lol-1f", lambda_lol1=pj["lambda"], factor_mult=mult)
            pj["lambda"] *= mult
            pj["lambda_game"] *= mult
            pj["sd"] = math.sqrt(pj["lambda"] + pj["lambda"] ** 2 / pj["nb_size"])
        fpanel = (feats, contrib, mult, fs)
    flags = []
    bo = s["best_of"]
    if FORMAT.get(scope) and bo and bo != FORMAT[scope]:
        flags.append("format_scope_mismatch")
        if scope == "123" and bo == 3 and p_win is not None:       # A2: a mixture over 2 or 3 games
            p3 = 2 * p_win * (1 - p_win)
            lg = pj["lambda_game"]
            o3, u3, q3 = m.probs(3 * lg, m.R[(stat, "123")], offer["line"])
            o2, u2, q2 = m.probs(2 * lg, m.R[(stat, "12")], offer["line"])
            po, pu, push = p3 * o3 + (1 - p3) * o2, p3 * u3 + (1 - p3) * u2, p3 * q3 + (1 - p3) * q2
        else:
            po, pu, push = m.probs(pj["lambda"], pj["nb_size"], offer["line"])
    else:
        po, pu, push = m.probs(pj["lambda"], pj["nb_size"], offer["line"])
    if pj["data_quality"] != "ok":
        flags.append(pj["data_quality"])
    if not oin:
        flags.append("opponent has no stored games (opponent factor = league average)")
    side = "over" if po >= pu else "under"
    offered = side_offered(offer.get("platform"), offer.get("sides"), side)
    p_side = po if side == "over" else pu
    grade, units_policy = grade_and_units(p_side)
    lo, hi = m.interval(pj["lambda"], pj["nb_size"], 0.8)
    med = _nb_median(m, pj["lambda"], pj["nb_size"])
    rec = dict(sport=store.SPORT, platform=offer["platform"], market_key=offer["market_key"], line_id=offer.get("line_id"),
               quote_event_id=offer.get("_event_id"), quote_observed_at=offer.get("_observed_at"), stat=stat, scope=scope,
               market_type="single", odds_type=offer.get("odds_type"), line=offer["line"], sides=offer.get("sides"),
               sid=link["sid"], pid=pid, tid=tid, opp_tid=opp, role=role, best_of=bo, series_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
               model_version=pj.get("version") or m.VERSION, fit_sha=fit_sha(), form=pj.get("form"), **{"lambda": round(pj["lambda"], 4)},
               nb_size=pj["nb_size"], p_over=round(po, 4), p_under=round(pu, 4), p_push=round(push, 4), model_side=side,
               side_offered=None if offered is None else int(bool(offered)), grade=grade,
               grade_label=(f"Lean {side.title()}" if grade == "Lean" else grade), units_policy=units_policy, units=0.0,
               stake_restriction=LOL_STAKE_RESTRICTION if units_policy > 0 else None,
               inputs={k: (round(v, 4) if isinstance(v, float) else v) for k, v in pj.items()
                       if k in ("lambda_game", "player_rate", "team_factor", "opp_factor", "strength_factor", "p_win",
                                "history_games", "history_weight", "data_quality", "role_prior", "form")} | {
                   "league": league, "team_games": len(tin), "opponent_games": len(oin), "history_last_game": h.last_game, "history_since": h.since, "data_version": _data_version(con),
                   "mean": round(pj["lambda"], 3), "median": med, "range80": [lo, hi], "provisional": _provisional(m),
                   "drivers": _drivers(pj), "unknowns": ["lineup changes after the forecast", "draft and champions",
                                                          "sides", "the tournament patch effect (not modelled)"]},
               flags=flags)
    j = {"kills": 1, "deaths": 2, "assists": 3}[stat]
    last = [r[j] for r in pin if r[j] is not None][-10:]
    rec["inputs"]["recent"] = {"games": len(last), "per_game": round(sum(last) / len(last), 2) if last else None}
    from . import prices
    rec["inputs"]["market"] = prices.classify(offer["platform"], offer)
    rec["inputs"]["team_win"] = team_win(p_win, offer, start, market)
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
    if rec["side_offered"] != 1:
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
        st = con.execute("SELECT e.line, e.sides, s.open FROM market_state s JOIN market_events e ON e.id = s.last_event_id "
                         "WHERE s.platform = ? AND s.market_key = ?", (r["platform"], r["market_key"])).fetchone()
        if not st or not st["open"]:
            why.append(f"leg {r['id']}: line no longer offered")
        elif st["line"] != r["line"]:
            why.append(f"leg {r['id']}: line moved {r['line']} -> {st['line']}")
        elif side_offered(r["platform"], json.loads(st["sides"] or "{}"), r["model_side"]) is not True:
            why.append(f"leg {r['id']}: {r['model_side']} no longer offered")
        if _when(r["series_start"]) and _when(r["series_start"]) <= now:
            why.append(f"leg {r['id']}: series started")
        if not r["slip_eligible"]:
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
    return store.sha({k: rec.get(k) for k in ("quote_event_id", "line", "sides", "lambda", "nb_size", "p_over", "p_under",
                                              "fit_sha", "flags", "series_start")})


def run(con, db=None, now=None, market=None):
    """One forecast cycle over every open linked offer. Appends changed forecasts. Returns counts. market: see forecast_one."""
    counts = {}
    q = ("SELECT l.platform, l.market_key, l.sid, l.tid, l.opp_tid, l.pid, l.role, e.id AS event_id, e.observed_at, e.data "
         "FROM market_links l JOIN market_state s ON s.platform = l.platform AND s.market_key = l.market_key "
         "JOIN market_events e ON e.id = s.last_event_id WHERE l.status = 'matched' AND s.open = 1")
    todo = con.execute(q).fetchall()
    ids = lambda col: {store.canonical_id(con, r[col]) for r in todo}
    h = History(db, ids("pid"), ids("tid") | ids("opp_tid"), now=now)
    h.fit, h.sim_states = fit_json(), {}
    h.factor_pids, h.factor_teams = ids("pid"), ids("tid") | ids("opp_tid")
    for r in todo:
        offer = json.loads(r["data"])
        offer.update(platform=r["platform"], _event_id=r["event_id"], _observed_at=r["observed_at"])
        rec = forecast_one(con, h, offer, dict(r), now, market)
        if "skip" in rec:
            counts["skip: " + rec["skip"]] = counts.get("skip: " + rec["skip"], 0) + 1
            continue
        sha = _sha(rec)
        last = con.execute("SELECT state_sha FROM lol_forecasts WHERE platform = ? AND market_key = ? ORDER BY id DESC LIMIT 1",
                           (rec["platform"], rec["market_key"])).fetchone()
        if last and last[0] == sha:
            counts["unchanged"] = counts.get("unchanged", 0) + 1
            continue
        cols = ("sport", "platform", "market_key", "line_id", "quote_event_id", "quote_observed_at", "stat", "scope", "market_type",
                "odds_type", "line", "sides", "sid", "pid", "tid", "opp_tid", "role", "best_of", "series_start", "model_version",
                "fit_sha", "form", "lambda", "nb_size", "p_over", "p_under", "p_push", "model_side", "side_offered", "inputs",
                "flags", "slip_eligible", "slip_reasons", "grade", "grade_label", "units_policy", "units", "stake_restriction")
        vals = [json.dumps(rec[c], default=str) if c in ("sides", "inputs", "flags", "slip_reasons") else rec[c] for c in cols]
        con.execute(f"INSERT INTO lol_forecasts (forecast_at, state_sha, {', '.join(cols)}) VALUES (?, ?, {', '.join('?' * len(cols))})",
                    [store.now_iso(), sha] + vals)
        counts["saved"] = counts.get("saved", 0) + 1
    con.commit()
    if db is None:
        h.db.close()
    return counts


def settle_all(con):
    """Settle every pre-match forecast whose series is complete (the latest forecast made before the series start, per
    platform / market / line). Versioned: an upstream correction becomes a new settlement version."""
    out = {}
    rows = con.execute("SELECT f.* FROM lol_forecasts f JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM lol_forecasts "
                       "WHERE forecast_at < series_start GROUP BY platform, market_key, line) z ON z.id = f.id "
                       "JOIN c_series s ON s.sid = f.sid WHERE s.status = 'complete'").fetchall()
    for f in rows:
        unit = f"{f['platform']}|{f['market_key']}|{f['line']}"
        res = settle.record(con, unit, f["platform"], f["stat"], "maps_" + "_".join(f["scope"]), store.canonical_id(con, f["pid"]),
                            f["sid"], f["line"])
        out[res["status"]] = out.get(res["status"], 0) + 1
    return out


def board(con, now=None):
    """The admin board: every open LoL offer with its latest forecast (or why there is none), the match tables (identical
    markets side by side) and each forecast's card data. Also the settled forecasts of the last 3 days."""
    now = now or datetime.now(timezone.utc)
    label = {r[0]: r[1] for r in con.execute("SELECT eid, label FROM entities")}
    lab = lambda e: label.get(store.canonical_id(con, e)) if e else None
    series = {r["sid"]: dict(r) for r in con.execute("SELECT * FROM c_series")}
    rows = []
    q = ("SELECT s.platform, s.market_key, s.last_seen AS checked_at, e.id AS event_id, e.observed_at, e.player, e.team, e.opponent, e.start_time, e.stat, e.scope, "
         "e.stat_label, e.market_type, e.odds_type, e.line, e.sides, l.status AS link_status, l.reason AS link_reason, l.sid, l.pid, "
         "l.tid, l.opp_tid, l.role FROM market_state s JOIN market_events e ON e.id = s.last_event_id LEFT JOIN market_links l ON "
         "l.platform = s.platform AND l.market_key = s.market_key WHERE s.open = 1 ORDER BY e.start_time, e.player")
    for r in con.execute(q).fetchall():
        r = dict(r)
        f = con.execute("SELECT * FROM lol_forecasts WHERE platform = ? AND market_key = ? ORDER BY id DESC LIMIT 1",
                        (r["platform"], r["market_key"])).fetchone()
        f = dict(f) if f else None
        s = series.get(r.get("sid")) or {}
        rows.append({
            "platform": r["platform"], "market_key": r["market_key"], "player": r["player"], "team": lab(r.get("tid")) or r["team"],
            "role": ROLE_LABEL.get(r.get("role")) if r.get("role") else None, "opponent": lab(r.get("opp_tid")) or r["opponent"],
            "competition": s.get("tournament") or (s.get("tournament_page") or "").replace("LE:", ""), "best_of": s.get("best_of"),
            "series_start": s.get("scheduled_utc") or r["start_time"], "stat": r["stat"], "scope": SCOPE_LABEL.get(SCOPE.get(r["scope"] or ""), r["stat_label"]),
            "stat_label": r["stat_label"], "market_type": r["market_type"], "odds_type": r["odds_type"], "line": r["line"],
            "sides": json.loads(r["sides"] or "{}"), "quote_seen_at": r["observed_at"], "quote_checked_at": r["checked_at"],
            "link": {"status": r.get("link_status"), "reason": r.get("link_reason"), "sid": r.get("sid"), "pid": r.get("pid")},
            "forecast": None if not f else {
                "id": f["id"], "at": f["forecast_at"], "line": f["line"], "projection": f["lambda"], "p_over": f["p_over"],
                "p_under": f["p_under"], "p_push": f["p_push"], "model_side": f["model_side"], "side_offered": f["side_offered"],
                "model": f"{f['model_version']} ({f['form']}, fit {f['fit_sha']})", "inputs": json.loads(f["inputs"] or "{}"),
                "flags": json.loads(f["flags"] or "[]"), "slip_eligible": bool(f["slip_eligible"]),
                "slip_reasons": json.loads(f["slip_reasons"] or "[]"), "current": f["line"] == r["line"],
                "grade": f["grade_label"], "units": f["units"], "units_policy": f["units_policy"],
                "stake_restriction": f["stake_restriction"]},
            "grade": (f or {}).get("grade_label"), "stake": (f or {}).get("units")})
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
                 "model_side": (x["forecast"] or {}).get("model_side"), "grade": (x["forecast"] or {}).get("grade"),
                 "current": (x["forecast"] or {}).get("current"),
                 "market": ((x["forecast"] or {}).get("inputs") or {}).get("market")})
    settled = [dict(r) for r in con.execute("SELECT * FROM lol_settlements WHERE settled_at >= ? ORDER BY id DESC LIMIT 500",
                                            (store.now_iso(now.timestamp() - 3 * 86400),))]
    feeds = {r[0]: r[1] for r in con.execute("SELECT platform, MAX(polled_at) FROM market_polls WHERE ok = 1 GROUP BY platform")}
    return {"at": store.now_iso(), "released_to_members": False, "feeds": feeds,
            "grades": "model grades from the published bands; stakes held at 0U until validated at real lines",
            "rows": rows, "match_tables": list(tables.values()), "settled_recent": settled,
            "counts": {"offers": len(rows), "with_forecast": sum(1 for x in rows if x["forecast"]),
                       "linked": sum(1 for x in rows if x["link"]["status"] == "matched")}}
