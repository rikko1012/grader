"""Per-game numbers that only play-by-play holds. Box-score stats are NOT recomputed here: points, rebounds, targets,
carries and the rest are read from the box score, once (sports_game_players / sports_game_teams). What play-by-play adds:

Basketball, per player and game
  rim_fga / rim_fgm            shots at the rim (layups, dunks, tips, putbacks, or 4 ft and closer)
  short_mid_fga / _fgm         other 2s from 5-14 ft;  long_mid_fga / _fgm  2s from 15 ft out
  corner3_fga / _fgm           3s from the corners
  pullup_fga / _fgm            off-the-dribble jumpers (ESPN shot types "Pullup", "Step Back")
  spotup_fga / _fgm            plain "Jump Shot" attempts - the catch-and-shoot proxy (ESPN has no touch tracking)
  assisted_fgm                 made shots credited with an assist
  on_s, on_pts_for, on_pts_against   seconds on court and the score while on (from substitutions; on/off)
  oreb_live_misses_on,         live (reboundable) misses while on court, own team / opponent - a PROXY for rebound
  dreb_live_misses_on          chances: ESPN has no tracking, so a miss nobody could reach still counts
Football, per player and game (roles from ESPN's play participants; plays wiped out by a penalty are skipped)
  tgt_deep, tgt_short, tgt_left, tgt_middle, tgt_right, tgt_rz (inside the 20), tgt_i10, tgt_air_yds_completed,
  tgt_success, tgt_explosive (20+), tgt_epa;  rush_rz, rush_i5, rush_left / middle / right, rush_success,
  rush_explosive (10+), rush_epa;  QB: dropbacks, scrambles, sacks_taken, att_deep, att_short, att_left / middle / right,
  cmp_deep, air_yds_completed, db_success, db_epa
Football, per team and game (offense; the defense's numbers are its opponent's offense)
  plays, dropbacks, rushes, neutral_plays, neutral_dropbacks, neutral_clock_s (pace), success, epa, explosive,
  rz_drives, rz_td_drives, drives

Success: 40% of the yards to go on 1st down, 60% on 2nd, all of it on 3rd / 4th (or a touchdown). EPA uses our own
expected-points table fitted on stored ESPN play-by-play (ep_nfl.json; fit_ep()). Neutral: home win probability
between 20% and 80% and not in the last 2 minutes of a half.
"""
import json
import math
import re
from collections import defaultdict
from pathlib import Path

EP_FILE = Path(__file__).resolve().parent / "ep_nfl.json"
RIM_TYPES = ("Layup", "Dunk", "Tip", "Alley Oop", "Finger Roll", "Putback")
_FEET = re.compile(r"(\d+)-foot")
_DEPTH = re.compile(r"pass (?:incomplete )?(short|deep) (left|middle|right)")
_RUSH_DIR = re.compile(r"\b(left end|left tackle|left guard|up the middle|right guard|right tackle|right end)\b")
PASS_TYPES = {"Pass Reception", "Pass Incompletion", "Passing Touchdown", "Pass Interception Return",
              "Interception Return Touchdown", "Sack", "Sack Opp Fumble Recovery", "Pass Fumble"}
RUSH_TYPES = {"Rush", "Rushing Touchdown", "Fumble Recovery (Own)", "Fumble Recovery (Opponent)", "Fumble Return Touchdown"}
TD_POINTS = 6.95                      # a touchdown plus the expected try


def _j(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


# ------------------------------------------------------------------------------------------------------ basketball

def shot_zone(p):
    """-> 'rim' | 'short_mid' | 'long_mid' | 'corner3' | 'above3' | None (not a field-goal attempt)."""
    t = p.get("type") or ""
    if not p.get("shooting") or t.startswith("Free Throw") or (p.get("points_attempted") or 0) not in (2, 3):
        return None
    m = _FEET.search(p.get("text") or "")
    feet = int(m.group(1)) if m else None
    x, y = p.get("x"), p.get("y")
    if feet is None and x is not None and y is not None:
        feet = math.hypot(x - 25, y - 1)
    if p["points_attempted"] == 3:
        return "corner3" if x is not None and y is not None and abs(x - 25) >= 21 and y <= 9 else "above3"
    if any(k in t for k in RIM_TYPES) or (feet is not None and feet <= 4):
        return "rim"
    return "long_mid" if feet is not None and feet >= 15 else "short_mid"


def _reboundable(p):
    t = p.get("type") or ""
    if p.get("scoring"):
        return False
    if t.startswith("Free Throw"):
        m = re.search(r"(\d) of (\d)", t)
        return bool(m) and m.group(1) == m.group(2)          # only the last free throw's miss is live
    return bool(p.get("shooting"))


def basketball_game(plays, players, home_id, away_id, period_s=720, ot_s=300):
    """plays (one game, in order) + box players -> {athlete_id: {stat: n}}. period_s / ot_s: period lengths (NBA 12 / 5
    minutes; WNBA 10 / 5) - each period's clock starts there."""
    team_of = {p["athlete_id"]: p["team_id"] for p in players}
    starters = defaultdict(list)
    for p in players:
        if p.get("starter"):
            starters[p["team_id"]].append(p["athlete_id"])
    out = defaultdict(lambda: defaultdict(float))
    # shots
    for p in plays:
        z = shot_zone(p)
        ath = _j(p.get("athletes"))
        if not z or not ath:
            continue
        shooter, made, t = ath[0]["id"], bool(p.get("scoring")), p.get("type") or ""
        if z != "above3":
            out[shooter][f"{z}_fga"] += 1
            out[shooter][f"{z}_fgm"] += made
        if "Pullup" in t or "Step Back" in t:
            out[shooter]["pullup_fga"] += 1
            out[shooter]["pullup_fgm"] += made
        elif t == "Jump Shot":
            out[shooter]["spotup_fga"] += 1
            out[shooter]["spotup_fgm"] += made
        if made and "assists)" in (p.get("text") or ""):
            out[shooter]["assisted_fgm"] += 1
    # who is on court: box starters, then substitutions ("A enters the game for B": participants [A, B]); a period's
    # first five = whoever is subbed out before being subbed in, or acts before any substitution of his own
    on = {home_id: set(starters[home_id]), away_id: set(starters[away_id])}
    by_period = defaultdict(list)
    for p in plays:
        by_period[p.get("period") or 0].append(p)
    for per in sorted(by_period):
        ps = by_period[per]
        if per > 1:
            first = {home_id: [], away_id: []}
            late = set()
            for p in ps:
                ath = [a["id"] for a in _j(p.get("athletes"))]
                if p.get("type") == "Substitution" and len(ath) >= 2:
                    if ath[1] not in late and ath[1] not in first.get(team_of.get(ath[1]), []):
                        first.setdefault(team_of.get(ath[1]), []).append(ath[1])
                    late.add(ath[0])
                else:
                    for a in ath:
                        tm = team_of.get(a)
                        if tm in first and a not in late and a not in first[tm]:
                            first[tm].append(a)
            for tm in (home_id, away_id):
                five = first[tm][:5]
                for a in on[tm]:                             # fill from the previous lineup when the log is short
                    if len(five) >= 5:
                        break
                    if a not in five and a not in late:
                        five.append(a)
                on[tm] = set(five)
        prev_clock = float(period_s if per <= 4 else ot_s)
        for p in ps:
            c = p.get("clock_s")
            if prev_clock is not None and c is not None and prev_clock >= c:
                for tm in (home_id, away_id):
                    for a in on[tm]:
                        out[a]["on_s"] += prev_clock - c
            if c is not None:
                prev_clock = c
            tm = p.get("team_id")
            if p.get("scoring") and p.get("score_value") and tm in on:
                other = away_id if tm == home_id else home_id
                for a in on[tm]:
                    out[a]["on_pts_for"] += p["score_value"]
                for a in on[other]:
                    out[a]["on_pts_against"] += p["score_value"]
            if _reboundable(p) and tm in on:
                other = away_id if tm == home_id else home_id
                for a in on[tm]:
                    out[a]["oreb_live_misses_on"] += 1
                for a in on[other]:
                    out[a]["dreb_live_misses_on"] += 1
            if p.get("type") == "Substitution":
                ath = [a["id"] for a in _j(p.get("athletes"))]
                if len(ath) >= 2 and team_of.get(ath[0]) in on:
                    s = on[team_of[ath[0]]]
                    s.discard(ath[1])
                    s.add(ath[0])
    return {a: dict(v) for a, v in out.items()}


# -------------------------------------------------------------------------------------------------------- football

def load_ep():
    try:
        return json.loads(EP_FILE.read_text())
    except (OSError, ValueError):
        return None


def _dist_b(d):
    return "1" if d <= 1 else "2" if d == 2 else "3" if d == 3 else "4-6" if d <= 6 else "7-10" if d <= 10 \
        else "11-15" if d <= 15 else "16+"


def _yte_b(y):
    return str(min(9, max(0, (int(y) - 1) // 10)))


def ep_value(ep, down, distance, yte):
    """Expected points of a 1st-4th down state for the team with the ball (None without a fit)."""
    if not ep or not down or distance is None or yte is None or not (1 <= yte <= 99):
        return None
    k = f"{int(down)}|{_dist_b(distance)}|{_yte_b(yte)}"
    v = ep["cells"].get(k)
    return v if v is not None else ep["by_down_yte"].get(f"{int(down)}|{_yte_b(yte)}")


def _state(p):
    return bool(p.get("down")) and p.get("yte") is not None and 1 <= p["yte"] <= 99


def _live(p):
    """A snap that counts: a pass or run play that was not wiped out by a penalty."""
    t = p.get("type") or ""
    if t == "Penalty" or "No Play" in (p.get("text") or "") or "NO PLAY" in (p.get("text") or ""):
        return False
    return (t in PASS_TYPES or t in RUSH_TYPES) and p.get("down") and p.get("yte") is not None


def success(p):
    y, d, dist = p.get("yards") or 0, p.get("down"), p.get("distance") or 0
    if p.get("scoring") and "Touchdown" in (p.get("type") or "") and not p.get("turnover"):
        return True
    if p.get("turnover"):
        return False
    need = 0.4 * dist if d == 1 else 0.6 * dist if d == 2 else dist
    return y >= need


def epa(ep, p, nxt):
    """EP after the play minus EP before, for the offense. A touchdown counts TD_POINTS, a field goal 3, a safety -2; a
    change of possession counts minus the opponent's EP at its next snap."""
    before = ep_value(ep, p.get("down"), p.get("distance"), p.get("yte"))
    if before is None:
        return None
    t, off = p.get("type") or "", p.get("offense_id")
    if p.get("scoring") and "Touchdown" in t:
        return (-TD_POINTS if p.get("turnover") or "Return" in t else TD_POINTS) - before
    if "Safety" in t or p.get("scoring_type") == "safety":
        return -2 - before
    if nxt is None or nxt.get("period") != p.get("period") and (p.get("period") or 0) in (2, 4):
        return None                                       # half / game over: no next state to value
    after = ep_value(ep, nxt.get("down"), nxt.get("distance"), nxt.get("yte"))
    if after is None:
        return None
    return (after if nxt.get("offense_id") == off else -after) - before


def football_game(plays, home_id, away_id, ep=None):
    """plays (one game, in order) -> ({athlete_id: {stat: n}}, {team_id: {stat: n}})."""
    snaps = [p for p in plays if _live(p)]
    # the next STATE after a snap is the next play that starts from a down - a snap, a punt, a field-goal try or a
    # penalty - so a drive that ends in a kick still credits the snaps before it with the 4th-down state they reached
    states = [p for p in plays if _state(p)]
    nxt_of = {}
    for a, b in zip(states, states[1:]):
        nxt_of[id(a)] = b
    pl = defaultdict(lambda: defaultdict(float))
    tm = defaultdict(lambda: defaultdict(float))
    prev = {}
    for p in snaps:
        off = p.get("offense_id") or p.get("team_id")
        t, text = p.get("type") or "", p.get("text") or ""
        roles = defaultdict(list)
        for a in _j(p.get("athletes")):
            roles[a["role"]].append(a["id"])
        ok = success(p)
        e = epa(ep, p, nxt_of.get(id(p)))
        y = p.get("yards") or 0
        wp = p.get("home_wp")
        neutral = wp is not None and 0.2 <= (wp if off == home_id else 1 - wp) <= 0.8 and not (
            (p.get("period") in (2, 4)) and (p.get("clock_s") or 0) <= 120)
        is_pass = t in PASS_TYPES or " pass " in f" {text} " or "scrambles" in text
        T = tm[off]
        T["plays"] += 1
        T["success"] += ok
        T["epa"] += e or 0
        T["epa_n"] += e is not None
        T["dropbacks" if is_pass else "rushes"] += 1
        T["explosive"] += (y >= 20) if is_pass else (y >= 10)
        if neutral:
            T["neutral_plays"] += 1
            T["neutral_dropbacks"] += is_pass
            last = prev.get(off)
            if last and last.get("drive_id") == p.get("drive_id") and last.get("period") == p.get("period") and \
                    last.get("clock_s") is not None and p.get("clock_s") is not None and last["clock_s"] >= p["clock_s"]:
                T["neutral_clock_s"] += last["clock_s"] - p["clock_s"]
                T["neutral_gaps"] += 1
        prev[off] = p
        if is_pass:
            qb = (roles.get("passer") or roles.get("rusher") or [None])[0]
            if qb:
                Q = pl[qb]
                Q["dropbacks"] += 1
                Q["db_success"] += ok
                Q["db_epa"] += e or 0
                Q["scrambles"] += "scrambles" in text
                Q["sacks_taken"] += t.startswith("Sack")
            m = _DEPTH.search(text)
            rec = (roles.get("receiver") or [None])[0]
            if m and qb:
                Q = pl[qb]
                Q[f"att_{m.group(1)}"] += 1
                Q[f"att_{m.group(2)}"] += 1
                if t in ("Pass Reception", "Passing Touchdown"):
                    Q[f"cmp_{m.group(1)}"] += 1
                    Q["air_yds_completed"] += y - (p.get("yac") or 0)
            if rec and m:
                R = pl[rec]
                R[f"tgt_{m.group(1)}"] += 1
                R[f"tgt_{m.group(2)}"] += 1
                R["tgt_rz"] += p["yte"] <= 20
                R["tgt_i10"] += p["yte"] <= 10
                R["tgt_success"] += ok
                R["tgt_explosive"] += y >= 20
                R["tgt_epa"] += e or 0
                if t in ("Pass Reception", "Passing Touchdown"):
                    R["tgt_air_yds_completed"] += y - (p.get("yac") or 0)
        else:
            r = (roles.get("rusher") or [None])[0]
            if r and "kneels" not in text:
                R = pl[r]
                R["rush_rz"] += p["yte"] <= 20
                R["rush_i5"] += p["yte"] <= 5
                d = _RUSH_DIR.search(text)
                if d:
                    side = "middle" if "middle" in d.group(1) or "guard" in d.group(1) else d.group(1).split()[0]
                    R[f"rush_{side}"] += 1
                R["rush_success"] += ok
                R["rush_explosive"] += y >= 10
                R["rush_epa"] += e or 0
    drives = defaultdict(lambda: {"team": None, "min_yte": 100, "td": False})
    for p in plays:
        if not p.get("drive_id") or not p.get("offense_id"):
            continue
        d = drives[p["drive_id"]]
        d["team"] = d["team"] or p["offense_id"]
        if p.get("yte") is not None and p["offense_id"] == d["team"]:
            d["min_yte"] = min(d["min_yte"], p["yte"])
        if p.get("scoring") and "Touchdown" in (p.get("type") or "") and p["offense_id"] == d["team"] \
                and not p.get("turnover"):
            d["td"] = True
    for d in drives.values():
        if d["team"]:
            tm[d["team"]]["drives"] += 1
            tm[d["team"]]["rz_drives"] += d["min_yte"] <= 20
            tm[d["team"]]["rz_td_drives"] += d["min_yte"] <= 20 and d["td"]
    return {a: dict(v) for a, v in pl.items()}, {t: dict(v) for t, v in tm.items()}


def fit_ep(play_rows, min_n=30):
    """Expected points by down x distance bucket x 10-yard bucket, from stored plays (every play that starts from a down,
    punts and field-goal tries included, so 4th-down states carry the kick): the value of a state is the next score in
    the same half (+TD_POINTS / +3 / -2 for the team with the ball, negative when the other team scores; 0 if
    none). Thin cells fall back to down x yards bucket."""
    games = defaultdict(list)
    for p in play_rows:
        games[(p["league"], p["event_id"])].append(p)
    cells, coarse = defaultdict(list), defaultdict(list)
    for ps in games.values():
        ps.sort(key=lambda p: p["seq"])
        nxt_score = None                                   # (points, team, half) of the next score, scanning backwards
        for p in reversed(ps):
            half = 1 if (p.get("period") or 1) <= 2 else 2
            t = p.get("type") or ""
            if p.get("scoring"):
                pts = TD_POINTS if "Touchdown" in t else 3 if "Field Goal" in t else 2 if "Safety" in t else None
                if pts:
                    scorer = p.get("offense_id")
                    if p.get("turnover") or "Return" in t or "Safety" in t:
                        scorer = p.get("defense_id")
                    nxt_score = (pts, scorer, half)
            if not _state(p):
                continue
            v = 0.0
            if nxt_score and nxt_score[2] == half:
                v = nxt_score[0] if nxt_score[1] == p.get("offense_id") else -nxt_score[0]
            cells[f"{int(p['down'])}|{_dist_b(p['distance'] or 0)}|{_yte_b(p['yte'])}"].append(v)
            coarse[f"{int(p['down'])}|{_yte_b(p['yte'])}"].append(v)
    return {"cells": {k: sum(v) / len(v) for k, v in cells.items() if len(v) >= min_n},
            "by_down_yte": {k: sum(v) / len(v) for k, v in coarse.items()},
            "n_plays": sum(len(v) for v in coarse.values()), "n_games": len(games)}
