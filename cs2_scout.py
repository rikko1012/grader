#!/usr/bin/env python3
"""
cs2_scout.py - pulls everything grader.py (CS2 v7.2) needs, fully automatic.

Stats:   BO3.gg via the cs2api wrapper (bo3.py) - no browser, no scraping of HLTV.
Odds:    Polymarket (polymarket.py).
FACEIT:  official Data API v4 (needs FACEIT_API_KEY); account found by Steam ID.

Commands:
  player  - roster check, stats waterfall (3m -> 6m -> 12m -> all-time on team),
            map filter, AWPer check, role rank, KAST/DPR, last-5 trend
  h2h     - Maps 1-2 series vs an opponent, hit rate vs a line, v7.2 verdict
  faceit  - the player's FACEIT account + recent matches
  odds    - team win odds from Polymarket (name = team, --opponent optional)
  slate   - CS2 matches in the next 36 hours (for the members' match list)
  full    - everything above, plus the finished grade when --line is given

HLTV-only numbers BO3.gg doesn't have, and what replaces them (approved 2026-09-27):
  round swing        -> not available: 0 (no swing adjustment), flagged
  Sniping (Gate A)   -> AWP detection from body-shot kills per body hit
                        (one-shot AWP kills): >= 0.30 AWPer, <= 0.22 rifler
  Trading/Firepower  -> not available: support flag skipped
  Rating 3.0         -> BO3.gg rating / 6.0 (average pro = 1.00, like HLTV)
  IGL                -> igl.csv (player,team) - BO3.gg doesn't record it
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from bo3 import BO3, _read as bo3_read, _write as bo3_write
from faceit_resolve import Resolver
from grader import CONFIG as GRADER_CONFIG, grade_leg
from polymarket import match_odds

BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or BASE)   # persistent storage on the host
CACHE_DIR = DATA_DIR / ".cache"
OUT_DIR = DATA_DIR / "output"
IGL_CSV = BASE / "igl.csv"
REGIONS_CSV = BASE / "regions.csv"
FACEIT_API = "https://open.faceit.com/data/v4"

WINDOWS = [("last_3m", 90), ("last_6m", 180), ("last_12m", 365)]
MIN_MAPS = 10
MAX_WINDOW_MAPS = 80        # maps (all numbers) read per window, newest first
BLEND_MAX_MAPS = 250        # form blend reads full 3/6/12-month windows (as tested)
FORM_BLEND = {"3m": (90, 0.50), "6m": (180, 0.30), "12m": (365, 0.20)}   # v7.5 step 2
RECENT_MATCHES = 10         # recent form: last 10 matches, Maps 1-2 only
STANDIN_DAYS = 14           # teams he played for in the last 14 days count as "his" for the next match
AWP_RATIO, RIFLE_RATIO = 0.30, 0.22   # body-shot kills per body hit
AWP_MIN_HITS = 150          # need this many chest+stomach hits to judge
AWP_HS_MAX, AWP_HS_KPR = 40.0, 0.70   # or: HS% under 40 with KPR 0.70+ = AWPer
NATIONALITY_MIN = 3         # 3+ players of one country = the team is from that country
REGIONAL_MAX_SERIES = 4    # regional H2H extension: at most this many same-nationality series
REGIONAL_MAX_LOOKUPS = 40   # team pages checked while looking for them (polite to BO3.gg)


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, file=sys.stderr)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "player"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def cache_get(key, ttl_hours):
    p = CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".json.gz")
    try:
        if ttl_hours > 0 and time.time() - p.stat().st_mtime < ttl_hours * 3600:
            return bo3_read(p)
    except OSError:
        pass
    return None


def cache_put(key, value):
    bo3_write(CACHE_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".json.gz"), value)   # gzip, atomic, never fatal


def prune_misc(keep_days=7):
    """FACEIT/odds answers: delete anything older than keep_days, old-format files, temp files."""
    freed, now = 0, time.time()
    if CACHE_DIR.exists():
        for p in CACHE_DIR.iterdir():
            try:
                if p.is_file() and (p.suffix in (".tmp", ".json") or now - p.stat().st_mtime > keep_days * 86400):
                    freed += p.stat().st_size
                    p.unlink()
            except OSError:
                continue
    return freed


def load_igl():
    if not IGL_CSV.exists():
        IGL_CSV.write_text("player,team\n", encoding="utf-8")
    with IGL_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {(norm(r.get("player", "")), norm(r.get("team", ""))) for r in csv.DictReader(f) if r.get("player")}


def is_igl(nick, team):
    rows = load_igl()
    return any(p == norm(nick) and (not t or t == norm(team)) for p, t in rows)


# ---------------------------------------------------------------- BO3 stats
def window_stats(b, slug, days):
    """One date window: overall numbers, per-map numbers, HS%, AWP signal, rating."""
    s = b.player_stats(slug, days)
    g = s.get("general_stats") or {}
    rounds = g.get("rounds_count") or 0
    kills, deaths = g.get("kills_sum"), g.get("deaths_sum")
    ms = s.get("map_stats") or []
    n = sum(m.get("maps_count") or 0 for m in ms)
    rating = sum((m.get("avg_player_rating") or 0) * (m.get("maps_count") or 0) for m in ms) / n if n else None
    acc = {a.get("hit_group"): a for a in (s.get("accuracy_stats") or [])}
    acc_kills = sum(a.get("kills_sum") or 0 for a in acc.values())
    head = (acc.get("Head") or {}).get("kills_sum") or 0
    body = [acc.get(k) or {} for k in ("Chest", "Stomach")]
    body_hits = sum(x.get("hits_sum") or 0 for x in body)
    body_kills = sum(x.get("kills_sum") or 0 for x in body)
    return {
        "days": days,
        "maps_played": g.get("games_count"),
        "rounds": rounds,
        "kpr": round(kills / rounds, 3) if kills is not None and rounds else None,
        "dpr": round(deaths / rounds, 3) if deaths is not None and rounds else None,
        "apr": round(g["assists_sum"] / rounds, 3) if g.get("assists_sum") is not None and rounds else None,
        "adr": round(g["damage_sum"] / rounds, 1) if g.get("damage_sum") and rounds else None,
        "hs_pct": round(100 * head / acc_kills, 1) if acc_kills else None,
        "rating": None,   # HLTV-style, filled in once KAST for the window is known (hltv_rating)
        "rating_bo3": round(rating, 2) if rating else None,
        "awp_ratio": round(body_kills / body_hits, 3) if body_hits else None,
        "body_hits": body_hits,
        "per_map": {m["map_name"]: {"maps_played": m.get("maps_count"), "kpr": round(m["avg_kills"], 3)
                                    if m.get("avg_kills") is not None else None}
                    for m in ms if m.get("map_name")},
    }


# Calibrated 2026-09-28 against HLTV Rating 3.0 (last 3 months, no filters) for 125 players.
# Left-out accuracy: avg miss 0.023, 115/125 within 0.05, stars (1.20+) within 0.010.
# [intercept, approx, kpr, dpr, apr, adr/100, kast/100]
RATING_FIT = [0.2732, 0.3859, -0.0415, -0.2516, -0.2413, 0.7113, 0.1152]
RATING_BLEND = (1.20, 1.30)   # public formula is best for stars: blend into it across this range


def hltv_rating(s, kast):
    """HLTV-style rating from raw stats. HLTV's Rating 3.0 formula is private, so: the public
    Rating 2.0 approximation, calibrated against real HLTV numbers (the approximation alone
    overrates tier-2 players; the calibration alone squeezes the stars - blended, both hold)."""
    kpr, dpr, apr, adr = s.get("kpr"), s.get("dpr"), s.get("apr"), s.get("adr")
    if None in (kpr, dpr, apr, adr, kast):
        return None
    impact = 2.13 * kpr + 0.42 * apr - 0.41
    approx = 0.0073 * kast + 0.3591 * kpr - 0.5329 * dpr + 0.2372 * impact + 0.0032 * adr + 0.1587
    w = RATING_FIT
    fit = w[0] + w[1] * approx + w[2] * kpr + w[3] * dpr + w[4] * apr + w[5] * adr / 100 + w[6] * kast / 100
    lo, hi = RATING_BLEND
    t = min(max((approx - lo) / (hi - lo), 0.0), 1.0)
    return round(fit + t * (approx - fit), 3)


def window_games(b, matches, days, cap=None):
    """Finished maps in the window with their scoreboards, newest first (MAX_WINDOW_MAPS max).
    One scoreboard covers all 10 players, so teammates come for free."""
    since = (date.today() - timedelta(days=days)).isoformat()
    cap = cap or MAX_WINDOW_MAPS
    ids = []
    for mt in matches:
        if mt.get("status") != "finished" or (mt.get("start_date") or "")[:10] < since:
            continue
        ids += [g["id"] for g in mt.get("games") or [] if g.get("state") == "done"]
    b.game_players_many(ids[:cap])          # parallel download of anything not saved yet
    out = []
    for mt in matches:
        if mt.get("status") != "finished" or (mt.get("start_date") or "")[:10] < since:
            continue
        for g in sorted(mt.get("games") or [], key=lambda g: g.get("number") or 0):
            if g.get("state") != "done":
                continue
            if len(out) >= cap:
                return out
            out.append((g, b.game_players(g["id"])))
    return out


def player_rows(games, pid):
    """One row per map this player played in the given games."""
    rows = []
    for g, ps in games:
        me = next((r for r in ps if (r.get("steam_profile") or {}).get("player_id") == pid), None)
        if me:
            rows.append({"number": g.get("number") or 9, "map": g.get("map_name"),
                         "rounds": (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0),
                         "kills": me.get("kills") or 0, "deaths": me.get("death") or 0,
                         "assists": me.get("assists") or 0, "damage": me.get("damage") or 0,
                         "headshots": me.get("headshots") or 0,
                         "kast": 100 * me["kast"] if me.get("kast") is not None else None})
    return rows


def agg(rows):
    """Per-round stats over a set of maps."""
    r = sum(x["rounds"] for x in rows)
    k = sum(x["kills"] for x in rows)
    kast = [x["kast"] for x in rows if x["kast"] is not None]
    if not r:
        return {"maps_played": len(rows), "kpr": None, "dpr": None, "apr": None, "adr": None,
                "hs_pct": None, "kast": None, "rounds": 0}
    return {"maps_played": len(rows), "rounds": r,
            "kpr": round(k / r, 3), "dpr": round(sum(x["deaths"] for x in rows) / r, 3),
            "apr": round(sum(x["assists"] for x in rows) / r, 3),
            "adr": round(sum(x["damage"] for x in rows) / r, 1),
            "hs_pct": round(100 * sum(x["headshots"] for x in rows) / k, 1) if k else None,
            "kast": round(sum(kast) / len(kast), 1) if kast else None}


def rating_of(rows):
    a = agg(rows)
    return hltv_rating(a, a["kast"]), a["maps_played"]


def chain(joined):
    """3m -> 6m -> 12m -> all-time on current team (only if he joined over a year ago)."""
    steps = list(WINDOWS)
    if joined:
        days_on_team = (date.today() - joined).days + 1
        if days_on_team > 365:
            steps.append(("all_time_on_team", days_on_team))
    return steps


def waterfall(b, matches, pid, joined):
    """3m -> 6m -> 12m -> all-time on team until 10+ MAPS 1-2 (the prop maps). Returns the
    Maps 1-2 stats and the window's games (all maps, for the rating)."""
    s, games = None, []
    for label, days in chain(joined):
        games = window_games(b, matches, days)
        s = dict(agg([r for r in player_rows(games, pid) if r["number"] <= 2]), window=label, days=days)
        if s["maps_played"] >= MIN_MAPS:
            s["flags"] = ["All-time on current team used (+/-8% variance flag)"] if label == "all_time_on_team" else []
            return s, games
    s["flags"] = ([f"Only {s['maps_played']} Map 1-2s even at the widest window - widen SD"]
                  if s["maps_played"] else ["No BO3.gg map data for this player"])
    return s, games


def map_waterfall(b, matches, pid, joined, map_code):
    """This map as Map 1 or 2: first window with 10+ such maps, else the widest one."""
    best = None
    for label, days in chain(joined):
        rows = [r for r in player_rows(window_games(b, matches, days), pid)
                if r["number"] <= 2 and r["map"] == map_code]
        if rows:
            a = agg(rows)
            best = {"map": map_code, "window": label, "maps_played": a["maps_played"], "kpr": a["kpr"],
                    "hs_pct": a["hs_pct"]}
            if a["maps_played"] >= MIN_MAPS:
                break
    return best or {"map": map_code, "maps_played": 0, "kpr": None, "window": None}


def awp_status(ratio, body_hits):
    if ratio is None or body_hits < AWP_MIN_HITS:
        return None, f"AWPer status unknown (only {body_hits} body hits on record)"
    if ratio >= AWP_RATIO:
        return 100, f"AWPer (body-shot kill rate {ratio:.2f} >= {AWP_RATIO})"
    if ratio <= RIFLE_RATIO:
        return 0, f"Rifler (body-shot kill rate {ratio:.2f})"
    return None, f"AWPer status unclear (body-shot kill rate {ratio:.2f}, between {RIFLE_RATIO} and {AWP_RATIO})"


def map_rows(b, player_id, matches, want_games):
    """Per-map rows for this player from the given matches (newest first). The games are picked
    in one pass (want_games may count matches), downloaded together, then read."""
    chosen = []
    for mt in matches:
        if mt.get("status") != "finished":
            continue
        for g in sorted(mt.get("games") or [], key=lambda g: g.get("number") or 0):
            if g.get("state") == "done" and want_games(mt, g):
                chosen.append((mt, g))
    b.game_players_many([g["id"] for _, g in chosen])
    rows = []
    for mt, g in chosen:
        teams = {mt.get("team1_id"): mt.get("team1") or {}, mt.get("team2_id"): mt.get("team2") or {}}
        ps = b.game_players(g["id"])
        me = next((r for r in ps if (r.get("steam_profile") or {}).get("player_id") == player_id), None)
        if not me:
            continue
        my_team = (me.get("team_clan") or {}).get("team_id")
        opp_id = next((t for t in teams if t != my_team), None)
        rows.append({
            "game_id": g["id"], "match_id": mt["id"], "match": mt.get("slug"),
            "date": (g.get("begin_at") or mt.get("start_date") or "")[:10],
            "number": g.get("number"), "map": g.get("map_name"),
            "team_id": my_team, "team": (teams.get(my_team) or {}).get("name") or me.get("clan_name"),
            "opponent_id": opp_id, "opponent": (teams.get(opp_id) or {}).get("name") or me.get("enemy_clan_name"),
            "kills": me.get("kills"), "headshots": me.get("headshots"), "deaths": me.get("death"),
            "kast": me.get("kast"), "rounds": (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0),
            "won": bool(me.get("win")), "steam_id": (me.get("steam_profile") or {}).get("steam_id_64"),
            "lineup": [((r.get("steam_profile") or {}).get("player") or {}).get("slug")
                       for r in ps if (r.get("team_clan") or {}).get("team_id") == my_team],
        })
    return rows


def recent_form(rows, window_kpr, line=None, stat="kills"):
    """Last 10 matches, Maps 1-2 only: KPR (for the recency blend) and each match's Maps 1-2
    total vs the line."""
    series = {}
    for r in rows:
        series.setdefault(r["match_id"], []).append(r)
    out_series = []
    for mid, maps in series.items():
        maps.sort(key=lambda m: m["number"] or 9)
        if len(maps) < 2:
            continue  # BO1s and forfeits have no Map 2
        m12 = maps[:2]
        out_series.append({"date": m12[0]["date"], "opponent": m12[0]["opponent"],
                           "maps": [m["map"] for m in m12], "values": [m[stat] for m in m12],
                           "total": sum(m[stat] for m in m12), "rounds": sum(m["rounds"] for m in m12),
                           "result": {2: "W", 1: "split", 0: "L"}[sum(m["won"] for m in m12)]})
    out_series.sort(key=lambda s: s["date"], reverse=True)
    kr = [r for r in rows if r["rounds"] and len(series[r["match_id"]]) >= 2]
    if not kr or not window_kpr:
        return {"matches": len(out_series), "series": out_series}
    kpr = sum(r["kills"] for r in kr) / sum(r["rounds"] for r in kr)
    trend = (kpr - window_kpr) / window_kpr * 100
    totals = sorted(s["total"] for s in out_series)
    med = (totals[(len(totals) - 1) // 2] + totals[len(totals) // 2]) / 2 if totals else None
    out = {"matches": len(out_series), "series": out_series, "kpr_recent": round(kpr, 3), "median_total": med,
           "trend_pct_vs_window": round(trend, 1), "avg_total": round(sum(s["total"] for s in out_series)
                                                                        / len(out_series), 1) if out_series else None}
    if abs(trend) >= 15:
        out["note"] = "Trending hard vs window - 70/30 blend applies"
    if line is not None and out_series:
        overs = sum(1 for s in out_series if s["total"] > line)
        out.update(line=line, overs=overs, hit_rate=round(overs / len(out_series), 3))
    return out


def run_player(b, name, args):
    hits = b.search_players(name, 10).get("results") or []
    if args.bo3_slug:
        slug = args.bo3_slug
    elif hits:
        exact = [h for h in hits if norm(h.get("nickname")) == norm(name)]
        slug = (exact or hits)[0]["slug"]
    else:
        raise LookupError(f"no pro player called '{name}' on BO3.gg - check the spelling")
    p = b.player(slug)
    team = p.get("team") or {}
    joined = datetime.fromisoformat(p["joined_team_at"][:10]).date() if p.get("joined_team_at") else None
    out = {"player": {"id": p["id"], "slug": slug, "nick": p.get("nickname")},
           "current_team": team.get("name"), "team_slug": team.get("slug"), "team_id": team.get("id"),
           "joined_team": joined.isoformat() if joined else None}

    # Gate C - roster
    roster = []
    if team.get("slug"):
        td = b.team(team["slug"])
        roster = [x for x in td.get("players") or [] if not x.get("is_coach")]
    out["roster"] = [x.get("nickname") for x in roster]
    in_roster = any(x.get("id") == p["id"] for x in roster)
    listed = args.team
    if listed:
        out["on_listed_team"] = bool(team.get("name")) and (norm(listed) in norm(team.get("name")) or
                                                             norm(team.get("name")) in norm(listed) or
                                                             norm(listed) == norm(team.get("slug")))
        out["transfer_check"] = "OK" if out["on_listed_team"] else f"MISMATCH - BO3.gg shows {team.get('name')}, not {listed}"

    matches = b.player_matches(p["id"]).get("results") or []
    last = next((m.get("start_date") for m in matches if m.get("status") == "finished"), None)
    out["days_since_last_map"] = (date.today() - date.fromisoformat(last[:10])).days if last else None

    # stats behind the projection: Maps 1-2 only, from each map's scoreboard
    overall, games = waterfall(b, matches, p["id"], joined)
    # v7.5 step 2: KPR and HS% = 0.50 x 3m + 0.30 x 6m + 0.20 x 12m (Maps 1-2), dropping any window
    # with <10 Maps 1-2. Walk-forward (900 cases): miss kills 6.07 -> 6.01, HS 3.76 -> 3.69.
    windows = {}
    for label, (wdays, _) in FORM_BLEND.items():
        a = agg([r for r in player_rows(window_games(b, matches, wdays, BLEND_MAX_MAPS), p["id"]) if r["number"] <= 2])
        windows[label] = {"maps": a["maps_played"], "kpr": a["kpr"], "hs_pct": a["hs_pct"]}
    use = {k: v for k, v in windows.items() if v["maps"] >= MIN_MAPS and v["kpr"]}
    overall["form_windows"] = windows
    if use:
        s_ = sum(FORM_BLEND[k][1] for k in use)
        overall["kpr_single_window"], overall["hs_single_window"] = overall["kpr"], overall["hs_pct"]
        overall["kpr"] = round(sum(FORM_BLEND[k][1] * v["kpr"] for k, v in use.items()) / s_, 3)
        hs_use = {k: v for k, v in use.items() if v["hs_pct"] is not None}
        if hs_use:
            overall["hs_pct"] = round(sum(FORM_BLEND[k][1] * v["hs_pct"] for k, v in hs_use.items())
                                      / sum(FORM_BLEND[k][1] for k in hs_use), 1)
        overall["form_blend"] = "+".join(k for k in FORM_BLEND if k in use)
    out["overall"] = overall
    days = overall["days"]
    out["kast"] = overall["kast"]
    # 4a (tested 2026-09-28, weight 0): his share of his team's Maps 1-2 kills in the window
    mk = tk = 0
    for g, ps in games:
        if (g.get("number") or 9) > 2:
            continue
        me = next((r for r in ps if (r.get("steam_profile") or {}).get("player_id") == p["id"]), None)
        if me:
            tid = (me.get("team_clan") or {}).get("team_id")
            mk += me.get("kills") or 0
            tk += sum(r.get("kills") or 0 for r in ps if (r.get("team_clan") or {}).get("team_id") == tid)
    out["team_kill_share"] = round(mk / tk, 3) if tk else None
    # 4b (tested 2026-09-28, weight 0): opening kills per round on Maps 1-2 in the window
    fk = fd = rr_ = trk = 0
    for g, ps in games:
        if (g.get("number") or 9) > 2:
            continue
        me = next((r for r in ps if (r.get("steam_profile") or {}).get("player_id") == p["id"]), None)
        if me:
            fk += me.get("first_kills") or 0
            fd += me.get("first_death") or 0
            trk += me.get("trade_kills") or 0
            rr_ += (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0)
    out["opening"] = {"kills_per_round": round(fk / rr_, 3) if rr_ else None,
                      "duel_win": round(fk / (fk + fd), 3) if fk + fd else None}
    out["trade_pr"] = round(trk / rr_, 4) if rr_ else None   # 4c: in the grade for kills props

    # his Maps 1-2 numbers on every map in the window (used when the maps are predicted)
    by_map = {}
    for r in player_rows(games, p["id"]):
        if r["number"] <= 2:
            by_map.setdefault(r["map"], []).append(r)
    out["map_table"] = {m: {k: v for k, v in agg(rs).items() if k in ("kpr", "hs_pct", "maps_played")}
                        for m, rs in by_map.items()}

    maps = [x.strip().lower() for x in (args.maps or "").split(",") if x.strip()]
    out["per_map"] = [map_waterfall(b, matches, p["id"], joined, m if m.startswith("de_") else f"de_{m}")
                      for m in maps]

    # AWP check uses BO3.gg's hit-group totals for the same window (all maps - a role, not a prop stat)
    acc = window_stats(b, slug, days)
    snip, awp_note = awp_status(acc.get("awp_ratio"), acc.get("body_hits") or 0)
    # second AWP test (owner's rule): low headshot share with a high kill rate = AWPer
    hs, kpr = overall.get("hs_pct"), overall.get("kpr")
    if snip != 100 and hs is not None and kpr is not None and hs < AWP_HS_MAX and kpr >= AWP_HS_KPR:
        snip, awp_note = 100, f"AWPer (Maps 1-2 HS% {hs} < {AWP_HS_MAX} with KPR {kpr})"
    out["sniping_equiv"], out["awp_note"] = snip, awp_note

    # rating (role rank): ALL maps in the window, like HLTV - him and every teammate, same maps
    overall["rating"], overall["rating_maps"] = rating_of(player_rows(games, p["id"]))
    mates = [x for x in roster if x.get("id") != p["id"]]
    out["teammate_ratings"], out["teammate_rating_maps"] = {}, {}
    for x in mates:
        out["teammate_ratings"][x["nickname"]], out["teammate_rating_maps"][x["nickname"]] = \
            rating_of(player_rows(games, x["id"]))
    out["teammates"] = [x["nickname"] for x in mates]
    out["is_igl"] = is_igl(p.get("nickname"), team.get("name"))

    # recent form: last 10 matches, Maps 1-2 only
    seen = []

    def recent(mt, g):
        if mt["id"] not in seen:
            if len(seen) >= RECENT_MATCHES:
                return False
            seen.append(mt["id"])
        return (g.get("number") or 9) <= 2
    recent_rows = map_rows(b, p["id"], matches, recent)
    out["recent_maps"] = recent_rows
    out["recent_form"] = recent_form(recent_rows, overall.get("kpr"), args.line,
                                     "headshots" if getattr(args, "prop", "kills") == "hs" else "kills")
    out["steam_id"] = next((r["steam_id"] for r in recent_rows if r["steam_id"]), None)

    # stand-in / roster confirmation from the latest lineup
    last_lineup = set(recent_rows[0]["lineup"]) if recent_rows else set()
    roster_slugs = {x.get("slug") for x in roster}
    out["is_standin"] = bool(roster) and not in_roster
    out["roster_confirmed"] = in_roster and len(roster) >= 5 and len(last_lineup & roster_slugs) >= 4
    if recent_rows and recent_rows[0]["team_id"] != team.get("id"):
        out["roster_note"] = f"Last map was played for {recent_rows[0]['team']}, not {team.get('name')}"
    out["_matches"] = matches
    return out


def load_regions():
    """regions.csv: country_code -> region the owner groups it with (e.g. RU/UA/KZ)."""
    if not REGIONS_CSV.exists():
        return {}
    with REGIONS_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {r["country_code"].strip().upper(): r["region"].strip()
                for r in csv.DictReader(f) if r.get("country_code") and r.get("region")}


def nationality(b, team_slug):
    """Gate C nationality tag, from the CURRENT roster only: 3+ players from one country, or
    from one region in regions.csv, = the team is from there. Mixed rosters get no tag (rule
    doesn't apply). Nothing is inferred - a player with no country on record doesn't count."""
    try:
        roster = [p for p in (b.team(team_slug).get("players") or []) if not p.get("is_coach")]
    except Exception:
        return None
    regions = load_regions()
    counts, names, codes = {}, {}, []
    for p in roster:
        c = p.get("country") or {}
        code = (c.get("code") or "").upper()
        codes.append(code or "?")
        if code:
            key = regions.get(code, code)          # grouped countries share one key
            counts[key] = counts.get(key, 0) + 1
            names[key] = key if code in regions else c.get("name")
    top = max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0)
    if top[1] >= NATIONALITY_MIN:
        return {"id": top[0], "label": names[top[0]], "players": top[1], "of": len(roster), "roster": codes}
    return {"id": None, "label": f"mixed roster ({'/'.join(codes)})", "roster": codes}


def same_tag(b, team_slug, tag, lookups):
    if not team_slug or lookups["n"] >= REGIONAL_MAX_LOOKUPS:
        return False
    if team_slug not in lookups["seen"]:
        lookups["n"] += 1
        lookups["seen"][team_slug] = nationality(b, team_slug)
    t = lookups["seen"][team_slug]
    return bool(t) and t["id"] is not None and t["id"] == tag["id"]


def regional_rows(b, pb, opp_id, tag):
    """Maps 1-2 of the player's latest series vs OTHER teams sharing the opponent's tag."""
    lookups, rows, series = {"n": 0, "seen": {}}, [], 0
    for mt in pb["_matches"]:
        if series >= REGIONAL_MAX_SERIES:
            break
        if mt.get("status") != "finished" or opp_id in (mt.get("team1_id"), mt.get("team2_id")):
            continue
        if not any(same_tag(b, (mt.get(s) or {}).get("slug"), tag, lookups) for s in ("team1", "team2")):
            continue
        got = [r for r in map_rows(b, pb["player"]["id"], [mt], lambda m, g: (g.get("number") or 9) <= 2)
               if same_tag(b, next(((mt.get(s) or {}).get("slug") for s in ("team1", "team2")
                                    if mt.get(s + "_id") == r["opponent_id"]), None), tag, lookups)]
        if len(got) >= 2:
            rows += [dict(r, regional=True) for r in got]
            series += 1
    return rows


# ---------------------------------------------------------------- map prediction (before the veto)
# Checked on 21 real BO3 vetoes: order is always ban-ban-pick-pick-ban-ban-decider, Map 1 is the
# first pick, Map 2 the second, and the team that bans first also picks first - on BO3.gg that is
# the second-listed team (19 of 21).
VETO_MATCHES = 15          # each team's last 15 BO3 vetoes
VETO_DAYS = 150
P_TEAM2_STARTS = 0.9       # measured 19/21
VETO_SMOOTH = 0.5          # add-half smoothing so an unseen map is unlikely, not impossible


def veto_profile(b, team_id, before=None):
    """How often a team picks / first-bans each map in its recent BO3s (optionally only
    matches before a date - used to test predictions honestly)."""
    ref = date.fromisoformat(before) if before else date.today()
    since = (ref - timedelta(days=VETO_DAYS)).isoformat()
    picks, bans, pool, n = {}, {}, set(), 0
    recent_ms = b.team_recent(team_id, limit=60 if before else 30).get("results") or []
    b.match_veto_many([m["slug"] for m in recent_ms
                       if (m.get("start_date") or "")[:10] >= since and (not before or m["start_date"][:10] < before)
                       ][:VETO_MATCHES * 2])
    for m in recent_ms:
        start = (m.get("start_date") or "")[:10]
        if before and start >= before:
            continue
        if n >= VETO_MATCHES or start < since:
            break
        try:
            d = b.match_veto(m["slug"])
        except Exception:
            continue
        vs = sorted(d.get("match_maps") or [], key=lambda v: v.get("order") or 0)
        if d.get("bo_type") != 3 or len(vs) < 7:
            continue
        n += 1
        for v in vs:
            name = v.get("map_name") or (v.get("maps") or {}).get("map_name")   # slim or full format
            if not name:
                continue
            pool.add(name)
            if v.get("team_id") != team_id:
                continue
            if v.get("choice_type") == 1:
                picks[name] = picks.get(name, 0) + 1
            elif v.get("choice_type") == 2 and (v.get("order") or 9) <= 2:
                bans[name] = bans.get(name, 0) + 1
    return {"n": n, "picks": picks, "bans": bans, "pool": pool}


PERMABAN = 0.70           # a team that first-bans a map 70%+ of the time (5+ vetoes) removes it


def _permabanned(team, m):
    return team["n"] >= 5 and team["bans"].get(m, 0) / team["n"] >= PERMABAN


def _pick_probs(me, opp, pool, exclude=None):
    """P(this team picks each map): its pick habit, minus maps it or the opponent tends to ban.
    A map either team (near-)always bans first is treated as gone."""
    k = len(pool)
    w = {}
    for m in pool:
        if m == exclude or _permabanned(me, m) or _permabanned(opp, m):
            continue
        pick = (me["picks"].get(m, 0) + VETO_SMOOTH) / (me["n"] + k * VETO_SMOOTH)
        my_ban = (me["bans"].get(m, 0) + VETO_SMOOTH) / (me["n"] + k * VETO_SMOOTH)
        opp_ban = (opp["bans"].get(m, 0) + VETO_SMOOTH) / (opp["n"] + k * VETO_SMOOTH)
        w[m] = pick * (1 - my_ban) * (1 - opp_ban)
    s = sum(w.values()) or 1
    return {m: v / s for m, v in w.items()}


def predict_maps(b, team1_id, team2_id, before=None):
    """Probability of each map being Map 1 and Map 2, from both teams' veto habits."""
    p1, p2 = veto_profile(b, team1_id, before), veto_profile(b, team2_id, before)
    pool = p1["pool"] | p2["pool"]
    if not pool or (p1["n"] + p2["n"]) < 3:
        return None
    slot1, slot2 = {}, {}
    for starter, other, ps in ((p2, p1, P_TEAM2_STARTS), (p1, p2, 1 - P_TEAM2_STARTS)):
        first = _pick_probs(starter, other, pool)
        for m, pm in first.items():
            slot1[m] = slot1.get(m, 0) + ps * pm
            for m2, pm2 in _pick_probs(other, starter, pool, exclude=m).items():
                slot2[m2] = slot2.get(m2, 0) + ps * pm * pm2
    top = lambda d: dict(sorted(((m, round(p, 3)) for m, p in d.items() if p >= 0.005),
                                key=lambda x: -x[1]))
    return {"map1": top(slot1), "map2": top(slot2), "vetoes_used": {"team1": p1["n"], "team2": p2["n"]}}


def _joined(pb):
    return date.fromisoformat(pb["joined_team"]) if pb.get("joined_team") else None


def next_match(b, team_id):
    """The team's next match on BO3.gg that has NOT started: opponent + vetoed maps in order.
    A match in progress is skipped - its props are locked, and the grade is for the next one."""
    now = datetime.now(timezone.utc).isoformat()
    ms = [m for m in (b.team_upcoming(team_id).get("results") or [])
          if (m.get("team1") or {}).get("name") and (m.get("team2") or {}).get("name")
          and m.get("status") == "upcoming" and (m.get("start_date") or "") > now[:19]]
    if not ms:
        return None
    m = min(ms, key=lambda m: m.get("start_date") or "9999")
    opp = m["team2"] if m.get("team1_id") == team_id else m["team1"]
    return {"start": m.get("start_date"), "slug": m.get("slug"), "opponent": opp.get("name"),
            "team1_id": m.get("team1_id"), "team2_id": m.get("team2_id"),
            "opponent_id": opp.get("id"), "opponent_slug": opp.get("slug"), "event": (m.get("tournament") or {}).get("name"),
            "maps": [g["map_name"] for g in sorted(m.get("games") or [], key=lambda g: g.get("number") or 0)
                     if g.get("map_name")]}


def h2h_rows(b, player_block, opp_query, opp_id=None):
    """Maps 1-2 of every series vs the opponent in the player's match history."""
    q = norm(opp_query)

    def vs(mt, g):
        if (g.get("number") or 9) > 2:
            return False
        for side in ("team1", "team2"):
            t = mt.get(side) or {}
            if opp_id and t.get("id") == opp_id:
                return True
            if not opp_id and q and (q == norm(t.get("name")) or q == norm(t.get("slug"))):
                return True
        return False
    return map_rows(b, player_block["player"]["id"], player_block["_matches"], vs)


def h2h(rows, opponent, line=None, stat="kills"):
    groups = {}
    for r in rows:
        groups.setdefault(r["match_id"], []).append(r)
    series, skipped = [], 0
    for mid, maps in groups.items():
        maps.sort(key=lambda m: m["number"])  # map order within the series
        if len(maps) < 2:
            skipped += 1
            continue
        m12 = maps[:2]
        wins = [m["won"] for m in m12]
        series.append({"date": m12[0]["date"], "team": m12[0]["team"], "opponent": m12[0]["opponent"],
                       "maps": [m["map"] for m in m12], "values": [m[stat] for m in m12],
                       "total": sum(m[stat] for m in m12),
                       "result": {2: "W", 1: "split", 0: "L"}[sum(wins)],
                       "regional": bool(m12[0].get("regional"))})
    series.sort(key=lambda s: s["date"], reverse=True)
    reg = sum(1 for s in series if s["regional"])
    out = {"opponent_query": opponent, "stat": stat, "maps": 2 * len(series), "series": series,
           "exact_maps": 2 * (len(series) - reg), "regional_maps": 2 * reg,
           "single_map_series_skipped": skipped}
    if line is None or not series:
        return out
    hits = [s["total"] > line for s in series]
    rate = sum(hits) / len(hits)
    in_w = [s["total"] > line for s in series if s["result"] == "W"]
    in_l = [s["total"] > line for s in series if s["result"] == "L"]
    out.update(line=line, hit_rate=round(rate, 3),
               hits_in_wins=f"{sum(in_w)}/{len(in_w)}", hits_in_losses=f"{sum(in_l)}/{len(in_l)}",
               clears_in_wins=bool(in_w) and all(in_w), clears_in_losses=bool(in_l) and all(in_l))
    return out


HARD_LIQUIDITY = 1000   # USD


def market_quality(odds):
    """HARD: liquid Polymarket book priced off the bid/ask midpoint. SOFT: thin or wide book.
    NONE: no pre-match market (win chance falls back to 50/50)."""
    if not odds or not odds.get("found") or odds.get("team_ml") is None:
        return "NONE"
    return "HARD" if (odds.get("liquidity") or 0) >= HARD_LIQUIDITY and odds.get("source") == "midpoint" else "SOFT"


def opponent_defense(b, opp_id, maps=15):
    """SHADOW ONLY (logged, not in the projection yet): kills per round the opponent allowed per
    player on its last Maps 1-2, and how many of those maps it won. Tested against real results in
    Phase 10 before it is ever allowed to move a grade."""
    if not opp_id:
        return None
    kills = rounds = won = n = 0
    for m in b.team_recent(opp_id).get("results") or []:
        for g in sorted(m.get("games") or [], key=lambda g: g.get("number") or 0):
            if n >= maps or g.get("state") != "done" or (g.get("number") or 9) > 2:
                continue
            ps = b.game_players(g["id"])
            them = [r for r in ps if (r.get("team_clan") or {}).get("team_id") != opp_id]
            if len(them) < 5:
                continue
            r_ = (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0)
            kills += sum(r.get("kills") or 0 for r in them)
            rounds += r_ * len(them)
            won += any(r.get("win") for r in ps if (r.get("team_clan") or {}).get("team_id") == opp_id)
            n += 1
        if n >= maps:
            break
    if not rounds:
        return None
    return {"kpr_allowed": round(kills / rounds, 3), "maps": n, "map_win_rate": round(won / n, 2)}


def grader_inputs(pb, args, odds=None, hh=None):
    """Map everything into the keys grade_leg() documents."""
    o = pb["overall"]
    d = {
        "prop": args.prop, "line": args.line, "line_type": args.line_type,
        "maps_played": o.get("maps_played"), "kpr": o.get("kpr"), "hs_pct": o.get("hs_pct"),
        "kpr_last5": (pb.get("recent_form") or {}).get("kpr_recent"),  # last 10 matches, Maps 1-2
        "round_swing": None,  # HLTV-only - no swing adjustment
        "per_map": [{"map": m["map"], "kpr": m.get("kpr"), "hs_pct": m.get("hs_pct"), "maps_played": m.get("maps_played")}
                    for m in pb["per_map"]],
        "map_probs": ([pb["map_prediction"]["map1"], pb["map_prediction"]["map2"]]
                      if pb.get("map_prediction") and not pb["per_map"] else None),
        "map_stats": pb.get("map_table"),
        "rating": o.get("rating"), "teammate_ratings": list(pb["teammate_ratings"].values()),
        "is_igl": pb["is_igl"], "sniping": pb["sniping_equiv"],
        "firepower": None, "trading": None, "opening": None,
        "dpr": o.get("dpr"), "kast": pb.get("kast"), "adr": o.get("adr"),
        "market_quality": market_quality(odds), "days_since_last_map": pb.get("days_since_last_map"),
        "map_win_probs": [((odds or {}).get("maps") or {}).get(k, {}).get("team_prob")
                          if ((odds or {}).get("maps") or {}).get(k) else None for k in ("map1", "map2")]
                         if (odds or {}).get("found") else None,
        "recent_avg": (pb.get("recent_form") or {}).get("avg_total"),
        "recent_n": (pb.get("recent_form") or {}).get("matches"),
        "trade_pr": pb.get("trade_pr"),
        "scout": bool(pb.get("scout")), "standin_for": pb.get("standin_for"),
        "recent": ({"n": rf["matches"], "overs": rf["overs"]}
                   if (rf := pb.get("recent_form") or {}).get("overs") is not None else None),
        "on_listed_team": pb.get("on_listed_team"), "is_standin": pb["is_standin"],
        "roster_confirmed": pb["roster_confirmed"],
    }
    if odds and odds.get("found") and odds.get("team_ml") is not None:
        d["team_ml"], d["opp_ml"] = odds["team_ml"], odds["opp_ml"]
    if args.team_ml is not None:
        d["team_ml"] = args.team_ml
    if args.opp_ml is not None:
        d["opp_ml"] = args.opp_ml
    if hh and hh.get("series"):
        # exact-opponent series only (regional stand-ins are shown with weight 0)
        today = date.today()
        d["h2h_series"] = [{"age_days": (today - date.fromisoformat(x["date"])).days, "total": x["total"]}
                           for x in hh["series"] if not x.get("regional") and x.get("date")]
    return d


# ---------------------------------------------------------------- slate
def slate(b, hours=36):
    now = datetime.now(timezone.utc)
    params = {
        "scope": "widget-matches", "page[offset]": 0, "page[limit]": 100, "sort": "start_date",
        "filter[matches.status][in]": "upcoming,current",
        "filter[matches.start_date][gt]": now.strftime("%Y-%m-%d %H:%M"),
        "filter[matches.start_date][lt]": (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M"),
        "filter[matches.discipline_id][eq]": 1, "with": "teams,tournament,games",
    }
    data = b._run(b.cs2._api._make_request("/matches", params), ttl=0.5)
    out = []
    for m in data.get("results") or []:
        t1, t2 = m.get("team1") or {}, m.get("team2") or {}
        if not t1.get("name") or not t2.get("name"):
            continue
        out.append({"start": m.get("start_date"), "slug": m.get("slug"), "bo": m.get("bo_type"),
                    "tier": m.get("tier"), "event": (m.get("tournament") or {}).get("name"),
                    "team1": t1.get("name"), "team1_slug": t1.get("slug"),
                    "team2": t2.get("name"), "team2_slug": t2.get("slug"),
                    "maps": [g.get("map_name") for g in sorted(m.get("games") or [], key=lambda g: g.get("number") or 0)
                             if g.get("map_name")]})
    return out


# ---------------------------------------------------------------- FACEIT
class FaceitClient:
    def __init__(self, key, ttl_hours=12):
        if not key:
            raise SystemExit("Set FACEIT_API_KEY (get one at developers.faceit.com) or pass --faceit-key.")
        self.key, self.ttl = key, ttl_hours

    def _get(self, path, params=None):
        ck = f"faceit:{path}:{json.dumps(params, sort_keys=True)}"
        data = cache_get(ck, self.ttl)
        if data is not None:
            return data
        r = requests.get(FACEIT_API + path, params=params, timeout=20,
                         headers={"Authorization": f"Bearer {self.key}"})
        if r.status_code == 404:
            raise LookupError(f"FACEIT: not found ({path})")
        r.raise_for_status()
        data = r.json()
        cache_put(ck, data)
        return data

    def summary(self, nickname, n=20, player_id=None):
        p = (self._get(f"/players/{player_id}") if player_id else
             self._get("/players", {"nickname": nickname, "game": "cs2"}))
        pid = p["player_id"]
        cs2 = (p.get("games") or {}).get("cs2", {})
        recent = self._get(f"/players/{pid}/games/cs2/stats", {"limit": n}).get("items", [])
        matches = []
        for it in recent:
            s = it.get("stats", {})

            def f(*keys):
                for k in keys:
                    try:
                        return float(s[k])
                    except (KeyError, TypeError, ValueError):
                        pass
                return None
            kills, kr = f("Kills"), f("K/R Ratio")
            rounds = f("Rounds") or (round(kills / kr) if kills and kr else None)
            matches.append({"map": s.get("Map"), "result": s.get("Result"), "kills": kills,
                            "hs_pct": f("Headshots %", "HS %"), "rounds": rounds})
        k = [m for m in matches if m["kills"] is not None]
        with_r = [m for m in k if m["rounds"]]
        hs = [m["hs_pct"] for m in matches if m["hs_pct"] is not None]
        lifetime = self._get(f"/players/{pid}/stats/cs2").get("lifetime", {})
        return {
            "nickname": p.get("nickname"), "player_id": pid,
            "elo": cs2.get("faceit_elo"), "level": cs2.get("skill_level"),
            "recent_n": len(matches),
            "avg_kills": round(sum(m["kills"] for m in k) / len(k), 2) if k else None,
            "avg_hs_pct": round(sum(hs) / len(hs), 1) if hs else None,
            "kpr": round(sum(m["kills"] for m in with_r) / sum(m["rounds"] for m in with_r), 3) if with_r else None,
            "matches": matches,
            "lifetime": {kk: lifetime.get(kk) for kk in
                         ("Matches", "Average Headshots %", "Average K/D Ratio", "Win Rate %")},
        }


# ---------------------------------------------------------------- output
def print_summary(res):
    p = res.get("player_block")
    if p:
        o = p["overall"]
        print(f"\n== {p['player']['nick']} (BO3.gg {p['player']['slug']}) ==")
        print(f"Team: {p.get('current_team')}  |  Transfer check: {p.get('transfer_check', 'n/a')}  |  "
              f"roster confirmed: {p['roster_confirmed']}  stand-in: {p['is_standin']}")
        print(f"Roster: {', '.join(p['roster'])}")
        if p.get("roster_note"):
            print(f"  ! {p['roster_note']}")
        print(f"Window: {o.get('window')}  Maps 1-2: {o.get('maps_played')}  KPR={o.get('kpr')}  DPR={o.get('dpr')}  "
              f"HS%={o.get('hs_pct')}  ADR={o.get('adr')}  KAST={p.get('kast')}  |  "
              f"rating={o.get('rating')} (all {o.get('rating_maps')} maps)")
        for m in p["per_map"]:
            print(f"  {m['map']}: {m.get('window')} maps={m.get('maps_played')} KPR={m.get('kpr')}")
        for fl in o.get("flags", []):
            print(f"  ! {fl}")
        print(f"Gate A: {p['awp_note']}")
        print(f"Gate B: {'IGL (from igl.csv)' if p['is_igl'] else 'not in igl.csv - treated as non-IGL'}")
        tm = ", ".join(f"{k} {v}" for k, v in p["teammate_ratings"].items())
        print(f"Teammate ratings: {tm}")
        rf = p.get("recent_form") or {}
        if rf.get("kpr_recent") is not None:
            print(f"Last {rf['matches']} matches (Maps 1-2): KPR {rf['kpr_recent']} "
                  f"({rf['trend_pct_vs_window']:+}% vs window), avg {rf['avg_total']}"
                  + (f", over {rf['line']} in {rf['overs']}/{rf['matches']}" if "overs" in rf else "")
                  + (f" - {rf['note']}" if rf.get('note') else ""))
            for s in rf["series"]:
                print(f"    {s['date']}  v {s['opponent']:18} {s['values']} = {s['total']}  [{s['result']}]"
                      + (f"  {'OVER' if s['total'] > rf['line'] else 'under'}" if "line" in rf else ""))
    nm = res.get("next_match")
    if nm:
        print(f"\nNext match (auto): vs {nm['opponent']}  {nm['start'][:16]}  {nm['event']}  "
              f"maps: {', '.join(nm['maps']) if nm['maps'] else 'not announced yet'}")
    hh = res.get("h2h")
    if hh:
        nat = (res.get("opponent_nationality") or {}).get("label", "unknown")
        print(f"\n== H2H vs {hh['opponent_query']} ({nat}): {hh['exact_maps']} exact + "
              f"{hh['regional_maps']} regional maps ({hh['stat']}) ==")
        if res.get("regional_note"):
            print(f"  {res['regional_note']}")
        for s in hh["series"]:
            print(f"  {s['date']}  {s['team']} v {s['opponent']}  {s['maps']}  {s['values']} = {s['total']}  "
                  f"[{s['result']}]" + ("  (regional)" if s["regional"] else ""))
        if "hit_rate" in hh:
            print(f"Line {hh['line']}: hit {hh['hit_rate']:.0%}  wins {hh['hits_in_wins']}  losses {hh['hits_in_losses']}")
    od = res.get("odds")
    if od:
        if od["found"]:
            print(f"\n== Polymarket: {od['title']} ({od['start']}) ==")
            print(f"{od['team']} {od.get('team_prob', 0):.1%} ({od.get('team_ml')})  vs  "
                  f"{od['opponent']} {od.get('opp_prob', 0):.1%} ({od.get('opp_ml')})  "
                  f"| liquidity ${od['liquidity']:,}")
            for k, v in od["maps"].items():
                if v:
                    print(f"  {k}: {od['team']} {v['team_prob']:.1%}")
            for fl in od["flags"]:
                print(f"  ! {fl}")
        else:
            print(f"\n== Polymarket: {od['note']} ==")
    fi = res.get("faceit_identity")
    if fi:
        print(f"\n== FACEIT account: {fi.get('nickname') or 'NOT FOUND'}  [{fi['confidence'].upper()}] ==")
        for e in fi.get("evidence", []):
            print(f"  - {e}")
        for a in fi.get("alternatives", []):
            print(f"  alt: {a['nickname']} (lvl {a['level']}, {a['elo']} elo) [{a['confidence']}] {'; '.join(a['evidence'])}")
    fc = res.get("faceit")
    if fc:
        print(f"== FACEIT {fc['nickname']} (lvl {fc['level']}, {fc['elo']} elo) - last {fc['recent_n']}: "
              f"avg kills {fc['avg_kills']}  KPR {fc['kpr']}  HS% {fc['avg_hs_pct']}")
    g = res.get("grade")
    if g:
        print(f"\n== GRADE: {g['grade']}  ({g['status']})  {g['prop']} {g['line']} {g['line_type']} ==")
        if g.get("reason"):
            print(f"  {g['reason']}")
        if "p_more" in g:
            print(f"  Less side: {g['grade_less']}")
            print(f"  P(More) {g['p_more']:.1%}  P(Less) {g['p_less']:.1%}  units {g['units']}  "
                  f"lambda {g['lambda_final']} (base {g['lambda_base']})  SD {g['sd']}")
            print(f"  role: {g['detail']['role']}  |  p_win {g['detail']['p_win']}  |  {g['h2h']}  |  "
                  f"edge vs PrizePicks {g['market']['edge']:+.3f}")
        for fl in g.get("flags", []):
            print(f"  ! {fl}")
    for m in res.get("slate") or []:
        print(f"{m['start'][:16]}  {m['team1']} vs {m['team2']}  (BO{m['bo']}, {m['event']})"
              + (f"  maps: {', '.join(m['maps'])}" if m["maps"] else ""))


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("name", nargs="?", default="", help="Player name (or team name for 'odds')")
    common.add_argument("--bo3-slug", help="BO3.gg player slug if search picks the wrong player")
    common.add_argument("--team", help="Team listed on the prop, for the transfer check")
    common.add_argument("--maps", help="Prop maps, e.g. mirage,inferno")
    common.add_argument("--opponent", help="Opponent team for H2H and odds")
    common.add_argument("--line", type=float, help="Maps 1-2 line")
    common.add_argument("--prop", choices=["kills", "hs"], default="kills")
    common.add_argument("--line-type", choices=["standard", "goblin", "demon"], default="standard")
    common.add_argument("--team-ml", type=float, help="Override Polymarket: team moneyline")
    common.add_argument("--opp-ml", type=float, help="Override Polymarket: opponent moneyline")
    common.add_argument("--faceit", help="FACEIT nickname, if you already know it (skips the account search)")
    common.add_argument("--faceit-key", default=os.environ.get("FACEIT_API_KEY"))
    common.add_argument("--n", type=int, default=20, help="FACEIT recent matches")
    common.add_argument("--refresh", action="store_true", help="Ignore cache")
    common.add_argument("--ttl", type=float, default=12, help="Cache hours (default 12)")

    ap = argparse.ArgumentParser(description="BO3.gg + Polymarket + FACEIT data for CS2 prop grading")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("player", "h2h", "faceit", "odds", "slate", "full"):
        sub.add_parser(c, parents=[common])
    args = ap.parse_args()
    if args.cmd != "slate" and not args.name:
        ap.error("a player (or team) name is required")
    ttl = 0 if args.refresh else args.ttl

    res = {"generated": date.today().isoformat(), "command": args.cmd}
    b = BO3(ttl_hours=ttl) if args.cmd in ("player", "h2h", "faceit", "slate", "full") else None
    try:
        run(args, res, b, ttl)
    except LookupError as e:
        res["error"] = str(e)
        print(f"Not found: {e}")
    finally:
        if b:
            b.close()

    if res.get("player_block"):
        res["player_block"].pop("_matches", None)
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"{slugify(args.name or 'slate')}_{args.cmd}_{date.today().isoformat()}.json"
    out.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    if "error" in res:
        sys.exit(2)
    print_summary(res)
    print(f"\nSaved: {out}")


def grade(name, line, prop="kills", line_type="standard", slug=None, faceit_key=None, ttl=12,
          background=False):
    """One-call entry point for the web interface: everything 'full' does, returned as a dict.
    background=True (pre-loading) gives way to member look-ups."""
    args = argparse.Namespace(
        cmd="full", name=name, bo3_slug=slug, team=None, maps=None, opponent=None, line=line,
        prop=prop, line_type=line_type, team_ml=None, opp_ml=None, faceit=None,
        faceit_key=faceit_key or os.environ.get("FACEIT_API_KEY"), n=20, refresh=False, ttl=ttl)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": "grade"}
    b = BO3(ttl_hours=ttl, background=background)
    try:
        run(args, res, b, ttl)
    except LookupError as e:
        res["error"] = str(e)
    finally:
        b.close()
    if res.get("player_block"):
        res["player_block"].pop("_matches", None)
    return res


def run(args, res, b, ttl):
    if args.cmd == "slate":
        res["slate"] = slate(b)
    pb = None
    if args.cmd in ("player", "h2h", "faceit", "full"):
        pb = run_player(b, args.name, args)
        if args.cmd != "faceit":
            res["player_block"] = pb
    if args.cmd in ("h2h", "full") and not args.opponent and pb and pb.get("team_id"):
        # every team he has played for in the last 14 days (stand-ins, loans, recent moves) -
        # grade against whichever of them plays next
        cutoff = (date.today() - timedelta(days=STANDIN_DAYS)).isoformat()
        teams = {pb["team_id"]: pb.get("current_team")}
        for r in pb.get("recent_maps") or []:
            if r["date"] >= cutoff and r["team_id"] and r["team_id"] not in teams:
                teams[r["team_id"]] = r["team"]
        options = []
        for tid, tname in teams.items():
            nm_ = next_match(b, tid)
            if nm_:
                options.append((nm_["start"] or "9999", tid, tname, nm_))
        nxt = None
        if options:
            _, tid, tname, nxt = min(options, key=lambda o: o[0])
            pb["playing_for"], pb["playing_for_id"] = tname, tid
            if tid != pb["team_id"]:
                pb["standin_for"] = tname
        pb["scout"] = nxt is None          # no upcoming match -> SCOUT card, no grade/units
        if nxt:
            if b.background:   # shadow feature: computed by the pre-loader, never on a member's clock
                try:
                    res["opponent_defense"] = opponent_defense(b, nxt.get("opponent_id"))
                except Exception:
                    res["opponent_defense"] = None
            res["next_match"] = nxt
            args.opponent = nxt["opponent"]
            if not args.maps and nxt["maps"]:
                args.maps = ",".join(nxt["maps"][:2])
                pb["per_map"] = [map_waterfall(b, pb["_matches"], pb["player"]["id"], _joined(pb), m)
                                 for m in nxt["maps"][:2]]
            elif not args.maps and nxt.get("team1_id") and nxt.get("team2_id"):
                # veto not done yet: predict Maps 1-2 from both teams' veto habits
                try:
                    pb["map_prediction"] = predict_maps(b, nxt["team1_id"], nxt["team2_id"])
                except Exception:
                    pb["map_prediction"] = None
    if args.cmd == "h2h" or (args.cmd == "full" and args.opponent):
        if not args.opponent:
            raise SystemExit("h2h needs --opponent")
        nm = res.get("next_match") or {}
        opp_id, opp_slug = nm.get("opponent_id"), nm.get("opponent_slug")
        try:
            hits = [] if opp_id else b._run(b.cs2.search_teams(args.opponent, 5)).get("results") or []
            exact = [t for t in hits if norm(t.get("name")) == norm(args.opponent)
                     or norm(t.get("slug")) == norm(args.opponent)]
            pick = (exact or hits[:1] or [{}])[0]
            opp_id, opp_slug = opp_id or pick.get("id"), opp_slug or pick.get("slug")
        except Exception:
            pass
        stat = "headshots" if args.prop == "hs" else "kills"
        rows = h2h_rows(b, pb, args.opponent, opp_id)
        # Gate C nationality tag + Phase 7 regional extension
        tag = nationality(b, opp_slug) if opp_slug else None
        res["opponent_nationality"] = tag
        # one exact series (h2h_min_maps) counts on its own; extend only when there's none
        need = GRADER_CONFIG["h2h_min_maps"]
        exact_maps = 2 * len({r["match_id"] for r in rows if (r["number"] or 9) <= 2})
        if exact_maps < need:
            if not pb["roster_confirmed"]:
                res["regional_note"] = "Regional extension disabled - roster not confirmed"
            elif not tag or tag["id"] is None:
                res["regional_note"] = (f"Regional extension doesn't apply - opponent is a "
                                        f"{(tag or {}).get('label', 'roster with no country data')}")
            else:
                reg = regional_rows(b, pb, opp_id, tag)
                rows += reg
                n = len({r["match_id"] for r in reg})
                res["regional_note"] = (f"Regional extension: {n} series vs other {tag['label']} teams" if n else
                                        f"Regional extension: no recent series vs other {tag['label']} teams found")
        res["h2h"] = h2h(rows, args.opponent, args.line, stat)
    if args.cmd == "odds" or (args.cmd == "full" and (args.team or pb)):
        team = args.name if args.cmd == "odds" else (args.team or pb.get("playing_for") or pb.get("current_team"))
        res["odds"] = match_odds(team, args.opponent)  # always live, no cache
    if args.cmd == "full" and args.faceit_key:
        try:
            _faceit(args, res, pb, ttl)
        except Exception as e:          # FACEIT is display-only: it can never break a grade
            res["faceit_error"] = str(e)[:200]
    elif args.cmd == "faceit":
        _faceit(args, res, pb, ttl)
    if args.cmd == "full" and args.line is not None:
        res["grader_inputs"] = grader_inputs(pb, args, res.get("odds"), res.get("h2h"))
        res["grade"] = grade_leg(res["grader_inputs"])
        try:   # 20,000-run match simulation - display only (weight 0, tested vs the curve)
            from sim import simulate
            res["simulation"] = simulate(res["grader_inputs"])
        except Exception as e:
            res["simulation"] = None
            res["simulation_error"] = str(e)[:200]


def _faceit(args, res, pb, ttl):
    fc = FaceitClient(args.faceit_key, ttl)
    if args.faceit:
        ident = {"nickname": args.faceit, "player_id": None, "confidence": "manual",
                 "evidence": ["given with --faceit"], "alternatives": [], "show_to_members": True}
    else:
        ident = Resolver(fc).resolve(pb["player"]["nick"], pb.get("current_team"), pb["teammates"],
                                     use_saved=not args.refresh, steam_id=pb.get("steam_id"))
    res["faceit_identity"] = ident
    if ident.get("nickname"):
        res["faceit"] = fc.summary(ident["nickname"], args.n, ident.get("player_id"))


if __name__ == "__main__":
    main()
