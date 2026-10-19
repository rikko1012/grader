"""The A7 / A7.1 pre-match factor features - ONE implementation shared by the study (lol/factor_study.py) and the live
forecast (lol/center/forecast.py), so the features tested are the features used.

Index(rows, games) arranges the history; features(...) computes the A7 table for one (player, team, opponent, role, day):
only games on days BEFORE the match day; lineups = what each team fielded in its last game before the day (A7.1);
reference averages (per role, league game length, kill pace) are frozen from F-TRAIN (CONSTS, saved with the fit) so a
live forecast never re-estimates them. Missing data = 0 plus an indicator, never a favourable guess."""
import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

HL = 60.0
GROUPS = {
    "pace": ("pace_len", "pace_ckpm"),
    "script": ("script_stomp",),
    "matchup": ("lane_opp_deaths", "lane_opp_kills", "lane_missing"),
    "form": ("form_k", "form_a", "form_d", "sample_opp_wr"),
    "individual": ("kp", "dmg_share", "dpm", "cspm", "vspm", "death_share", "kill_share"),
    "team": ("team_form", "playoffs", "lg_lck", "lg_lpl", "lg_lec", "lg_americas"),
    "patch": ("patch_age", "patch_share", "patch_missing"),
    "situational": ("rest", "roster", "champ_pool"),
}
FEATS = [f for g in GROUPS.values() for f in g] + ["no_history"]
LABEL = {"pace_len": "expected game length (both teams)", "pace_ckpm": "combined kill pace (both teams)",
         "script_stomp": "expected stomp (team strength gap)", "lane_opp_deaths": "lane opponent's deaths per game",
         "lane_opp_kills": "lane opponent's kills per game", "lane_missing": "lane opponent unknown",
         "form_k": "kills last 5 vs his average", "form_a": "assists last 5 vs his average", "form_d": "deaths last 5 vs his average",
         "sample_opp_wr": "strength of the opponents in his last 10", "kp": "kill participation", "dmg_share": "damage share",
         "dpm": "damage per minute", "cspm": "CS per minute", "vspm": "vision per minute", "death_share": "death share",
         "kill_share": "kill share", "team_form": "team form (last 10)", "playoffs": "playoffs",
         "lg_lck": "LCK", "lg_lpl": "LPL", "lg_lec": "LEC", "lg_americas": "Americas leagues", "patch_age": "days since the patch",
         "patch_share": "his games on the current patch", "patch_missing": "patch unknown", "rest": "days since the team's last game",
         "roster": "roster continuity", "champ_pool": "champion pool depth", "no_history": "no earlier games"}


def day_of(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()


def league_flags(league):
    s = str(league or "").lower()
    return {"lg_lck": "lck" in s and "cl" not in s and "challengers" not in s, "lg_lpl": "lpl" in s,
            "lg_lec": s.startswith("lec") or "emea championship" in s,
            "lg_americas": any(x in s for x in ("lcs", "lta", "cblol", "lla"))}


def _pid(r):
    return r.get("pid") or r.get("player_id") or r.get("player_key")


def _tid(r, k="team"):
    return r.get(f"{k}_id") or r.get("opponent_id" if k == "opponent" else "team_id") or r.get(k)


class Index:
    """rows: player-game dicts (lol_player_games columns); games: {game_key: {patch, tournament, league, played_at}}."""

    def __init__(self, rows, games, match_day=None, team_rows=None, team_dmg=None):
        """team_rows (optional): one row per (game, team) for EVERY team (game_key, match_key, game_number, team_id,
        team_kills, opp_kills, duration_s, win) - the live index passes it with player rows of the teams on open lines only,
        so the server never holds every player's history; team_dmg: {(game_key, team_id): damage} likewise."""
        md = dict(match_day or {})
        if not md:
            for r in rows:
                if r.get("played_at"):
                    d = day_of(r["played_at"])
                    md[r["match_key"]] = min(md.get(r["match_key"], d), d)
        self.md = md
        tdmg, lineup = defaultdict(float), defaultdict(dict)
        for r in rows:
            if r.get("match_key") not in md:
                continue
            tk = _tid(r)
            if team_dmg is None:
                tdmg[(r["game_key"], tk)] += r.get("damage") or 0
            lineup[(r["game_key"], tk)][r.get("role")] = _pid(r)
        if team_dmg is not None:
            tdmg = defaultdict(float, team_dmg)
        self.pl, self.tm = defaultdict(list), defaultdict(list)
        seen = set()
        for r in rows:
            day = md.get(r.get("match_key"))
            if day is None or r.get("kills") is None or r.get("team_kills") is None or r.get("opp_kills") is None:
                continue
            tk, opp = _tid(r), r.get("opponent_id") or r.get("opponent")
            dur = (r.get("duration_s") or 0) / 60.0 or None
            g = games.get(r["game_key"]) or {}
            gn = int(r.get("game_number") or 1)
            self.pl[_pid(r)].append((day, r["kills"], r["deaths"], r["assists"], r["team_kills"], r["opp_kills"], dur,
                                     r.get("damage"), tdmg[(r["game_key"], tk)] or None, r.get("cs"), r.get("vision"),
                                     r.get("win"), opp, r.get("champion"), g.get("patch"), r["game_key"], gn))
            if team_rows is None and (r["game_key"], tk) not in seen:
                seen.add((r["game_key"], tk))
                self.tm[tk].append((day, r["team_kills"], r["opp_kills"], dur, r.get("win"), r["game_key"], gn))
        for r in team_rows or ():
            day = md.get(r.get("match_key"))
            if day is None or r.get("team_kills") is None or r.get("opp_kills") is None or (r["game_key"], r["team_id"]) in seen:
                continue
            seen.add((r["game_key"], r["team_id"]))
            self.tm[r["team_id"]].append((day, r["team_kills"], r["opp_kills"], (r.get("duration_s") or 0) / 60.0 or None,
                                          r.get("win"), r["game_key"], int(r.get("game_number") or 1)))
        for v in self.pl.values():
            v.sort(key=lambda x: (x[0], x[16]))
        for v in self.tm.values():
            v.sort(key=lambda x: (x[0], x[6]))
        self.lineup = lineup
        self.patch_first, self.league_games = {}, defaultdict(list)
        for gk, g in games.items():
            if not g.get("played_at") or not g.get("patch"):
                continue
            d = day_of(g["played_at"])
            self.patch_first[g["patch"]] = min(self.patch_first.get(g["patch"], d), d)
            self.league_games[g.get("league")].append((d, g["patch"]))
        for v in self.league_games.values():
            v.sort()
        self._twr, self._feat = {}, {}

    # ---- frozen reference averages (computed once on F-TRAIN by the study, saved with the fit)
    def consts(self, d_from, d_to):
        durs, ck = [], []
        for tk, games in self.tm.items():
            for x in games:
                if d_from <= x[0] < d_to and x[3]:
                    durs.append(x[3])
                    ck.append((x[1] + x[2]) / x[3])
        return {"len_avg": float(np.mean(durs)), "ckpm_avg": float(np.mean(ck))}

    def team_wr(self, team, day):
        k = (team, day)
        if k not in self._twr:
            g = [x for x in self.tm.get(team, ()) if x[0] < day and x[4] is not None]
            w = np.array([math.exp(-(day - x[0]) / HL) for x in g])
            self._twr[k] = float((np.dot(w, [x[4] for x in g]) + 5 * 0.5) / (w.sum() + 5)) if len(g) else 0.5
        return self._twr[k]

    def last_lineup(self, team, day):
        """A7.1: the lineup the team fielded in its last game before the day ({role: pid}), or {}."""
        g = [x for x in self.tm.get(team, ()) if x[0] < day]
        return dict(self.lineup.get((g[-1][5], team)) or {}) if g else {}

    def features(self, pid, team, opp, role, day, league, p_win, tournament, consts):
        key = (pid, team, opp, role, day, league, p_win, tournament)
        if key not in self._feat:
            self._feat[key] = self._features(pid, team, opp, role, day, league, p_win, tournament, consts)
        return dict(self._feat[key])

    def _features(self, pid, team, opp, role, day, league, p_win, tournament, consts):
        ra = (consts.get("role") or {}).get(role) or {}
        f = dict.fromkeys(FEATS, 0.0)
        prev = [x for x in self.pl.get(pid, ()) if x[0] < day]
        if not prev:
            f["no_history"] = 1.0
        w = np.array([math.exp(-(day - x[0]) / HL) for x in prev])

        def wmean(num, den, prior, K=5.0):
            if not prior:
                return None
            nn = dd = 0.0
            for wi, x in zip(w, prev):
                a, b = num(x), den(x)
                if a is None or b is None or not b:
                    continue
                nn += wi * a
                dd += wi * b
            return (nn + K * prior) / (dd + K)

        lg = lambda v, base: math.log(v / base) if v and base else 0.0
        f["kp"] = lg(wmean(lambda x: x[1] + x[3], lambda x: x[4], ra.get("kp")), ra.get("kp"))
        f["kill_share"] = lg(wmean(lambda x: x[1], lambda x: x[4], ra.get("kill_share")), ra.get("kill_share"))
        f["death_share"] = lg(wmean(lambda x: x[2], lambda x: x[5], ra.get("death_share")), ra.get("death_share"))
        f["dmg_share"] = lg(wmean(lambda x: x[7], lambda x: x[8], ra.get("dmg_share")), ra.get("dmg_share"))
        for k, c in (("dpm", 7), ("cspm", 9), ("vspm", 10)):
            f[k] = lg(wmean(lambda x, c=c: x[c], lambda x: x[6], ra.get(k), K=150.0), ra.get(k))
        for key, j, stat in (("form_k", 1, "kills"), ("form_a", 3, "assists"), ("form_d", 2, "deaths")):
            if prev:
                rate = (float(np.dot(w, [x[j] for x in prev])) + 5 * ra.get(stat, 1)) / (w.sum() + 5)
                l5 = [x[j] for x in prev[-5:]]
                f[key] = math.log(((sum(l5) + 3 * rate) / (len(l5) + 3)) / rate) if rate else 0.0
        if prev:
            f["sample_opp_wr"] = float(np.mean([self.team_wr(x[12], day) for x in prev[-10:]])) - 0.5
            pool = {x[13] for x in prev[-20:] if x[13]}
            f["champ_pool"] = len(pool) / max(1, min(20, len(prev)))
        L, C = consts["len_avg"], consts["ckpm_avg"]

        def tpace(t):
            g = [x for x in self.tm.get(t, ()) if x[0] < day and x[3]]
            if not g:
                return L, C
            ww = np.array([math.exp(-(day - x[0]) / HL) for x in g])
            return ((np.dot(ww, [x[3] for x in g]) + 5 * L) / (ww.sum() + 5),
                    (np.dot(ww, [(x[1] + x[2]) / x[3] for x in g]) + 5 * C) / (ww.sum() + 5))
        (l1, c1), (l2, c2) = tpace(team), tpace(opp)
        f["pace_len"] = math.log(((l1 + l2) / 2) / L)
        f["pace_ckpm"] = math.log(((c1 + c2) / 2) / C)
        f["script_stomp"] = 4 * ((p_win if p_win is not None else 0.5) - 0.5) ** 2
        tg = [x for x in self.tm.get(team, ()) if x[0] < day and x[4] is not None]
        if tg:
            f["team_form"] = float(np.mean([x[4] for x in tg[-10:]])) - self.team_wr(team, day)
            f["rest"] = math.log1p(min(30, day - max(x[0] for x in tg)))
            cur = set(self.last_lineup(team, day).values())
            f["roster"] = float(np.mean([cur <= set(self.lineup[(x[5], team)].values()) for x in tg[-6:-1]])) if cur and len(tg) > 1 else 0.0
        f["playoffs"] = 1.0 if "playoff" in str(tournament or "").lower() else 0.0
        f.update({k: float(v) for k, v in league_flags(league).items()})
        lgs = [p for dd, p in self.league_games.get(league, ()) if dd < day]
        if lgs:
            cur = lgs[-1]
            f["patch_age"] = math.log1p(max(0, day - self.patch_first.get(cur, day)))
            f["patch_share"] = float(np.mean([x[14] == cur for x in prev[-10:]])) if prev else 0.0
        else:
            f["patch_missing"] = 1.0
        opp_pid = self.last_lineup(opp, day).get(role)
        op = [x for x in self.pl.get(opp_pid, ()) if x[0] < day] if opp_pid else []
        if op:
            ow = np.array([math.exp(-(day - x[0]) / HL) for x in op])
            od = (np.dot(ow, [x[2] for x in op]) + 5 * ra.get("deaths", 3)) / (ow.sum() + 5)
            ok = (np.dot(ow, [x[1] for x in op]) + 5 * ra.get("kills", 3)) / (ow.sum() + 5)
            f["lane_opp_deaths"] = math.log(od / ra.get("deaths", 3))
            f["lane_opp_kills"] = math.log(ok / ra.get("kills", 3))
        else:
            f["lane_missing"] = 1.0
        return f


def role_consts(rows, match_day, d_from, d_to):
    """Per-role reference averages on F-TRAIN rows (kills / deaths / assists, KP, kill / death / damage share, DPM, CS / min,
    vision / min)."""
    acc = defaultdict(lambda: defaultdict(list))
    tdmg = defaultdict(float)
    sel = [r for r in rows if d_from <= match_day.get(r.get("match_key"), -1) < d_to and r.get("kills") is not None
           and r.get("team_kills") is not None and r.get("opp_kills") is not None]
    for r in sel:
        tdmg[(r["game_key"], _tid(r))] += r.get("damage") or 0
    for r in sel:
        ro, dur = r.get("role"), (r.get("duration_s") or 0) / 60
        if r["team_kills"]:
            acc[ro]["kp"].append((r["kills"] + r["assists"]) / r["team_kills"])
            acc[ro]["kill_share"].append(r["kills"] / r["team_kills"])
        if r["opp_kills"]:
            acc[ro]["death_share"].append(r["deaths"] / r["opp_kills"])
        if dur:
            for k, v in (("dpm", r.get("damage")), ("cspm", r.get("cs")), ("vspm", r.get("vision"))):
                if v is not None:
                    acc[ro][k].append(v / dur)
        t = tdmg[(r["game_key"], _tid(r))]
        if t and r.get("damage") is not None:
            acc[ro]["dmg_share"].append(r["damage"] / t)
        for k in ("kills", "deaths", "assists"):
            acc[ro][k].append(r[k])
    return {ro: {k: float(np.mean(v)) for k, v in m.items()} for ro, m in acc.items() if ro}


def adjust(feats, fit_stat):
    """exp(beta . z) and each feature's contribution for one stat's saved fit ({beta, mean, sd}); 1.0 when no fit."""
    if not fit_stat:
        return 1.0, {}
    contrib = {}
    eta = 0.0
    for k, b in fit_stat["beta"].items():
        z = (feats.get(k, 0.0) - fit_stat["mean"][k]) / fit_stat["sd"][k]
        contrib[k] = b * z
        eta += b * z
    return math.exp(eta), contrib
