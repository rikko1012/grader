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
  round swing        -> from the hltv-scraper database via hltv.py when configured (HLTV_DB_DSN)
                        and the player has 10+ HLTV maps in the window; otherwise 0, flagged
  Sniping (Gate A)   -> AWP detection from body-shot kills per body hit
                        (one-shot AWP kills): >= 0.30 AWPer, <= 0.22 rifler
  Trading/Firepower  -> not available: support flag skipped
  Rating 3.0         -> BO3.gg rating / 6.0 (average pro = 1.00, like HLTV)
  IGL                -> igl.csv (player,team) - BO3.gg doesn't record it
"""
import argparse
import collections
import copy
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

from bo3 import BO3
import kvstore
from faceit_resolve import Resolver
from grader import CONFIG as GRADER_CONFIG, grade_leg
import pick_verdict
from polymarket import match_odds, name_match

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
REGIONAL_MAX_SERIES = 8    # regional series: at most this many vs same-nationality teams (was 4)
REGIONAL_MAX_LOOKUPS = 200  # distinct past opponents checked (team pages are cached on disk 12 h). Was 40: a tier-3
                            # player meets 100+ teams a year, so H4SAN4TOR's Swedish opponent Metizport (49th) was never checked
REGIONAL_DAYS = 365         # regional series: the last 12 months, like the exact head-to-head (owner 2026-10-06)
REGIONAL_MIN_COUNTRY = 3    # a non-European country tag with fewer series than this is filled with same-REGION series
COUNTRY_ONLY_REGIONS = {"Europe"}   # owner 2026-10-12: "keep europe to country flags" - never a Europe region tag or fill-in


# ---------------------------------------------------------------- helpers
def log(msg):
    print(msg, file=sys.stderr)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "player"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def cache_get(key, ttl_hours):
    if ttl_hours <= 0:
        return None
    got = kvstore.get("misc", hashlib.sha1(key.encode()).hexdigest())
    return got[1] if got and time.time() - got[0] < ttl_hours * 3600 else None


def cache_put(key, value):
    kvstore.put("misc", hashlib.sha1(key.encode()).hexdigest(), value)   # packed cache, never fatal


def prune_misc(keep_days=7):
    """FACEIT/odds answers: drop anything older than keep_days; old-format answer files and temp
    files. (Other files in .cache - CS API checks, Liquipedia, warm list - are left alone.)"""
    freed, now = kvstore.prune("misc", keep_days), time.time()
    if CACHE_DIR.exists():
        for p in CACHE_DIR.iterdir():
            try:
                if p.is_file() and (p.suffix == ".tmp" or (p.name.endswith(".json.gz")
                                                          and now - p.stat().st_mtime > keep_days * 86400)):
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
        if 0 < (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0) < 13:
            continue          # a forfeit / technical win, not a played map (as map_rows; 2026-10-13 audit: window stats kept it)
        me = next((r for r in ps if (r.get("steam_profile") or {}).get("player_id") == pid), None)
        if me:
            rows.append({"number": g.get("number") or 9, "map": g.get("map_name"), "game_id": g.get("id"),
                         "rounds": (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0),
                         "kills": me.get("kills") or 0, "deaths": me.get("death") or 0,
                         "assists": me.get("assists") or 0, "damage": me.get("damage") or 0,
                         "headshots": me.get("headshots") or 0,
                         "opponent": me.get("enemy_clan_name"),
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
        return None, ("AWP: unknown (no data)" if not body_hits else f"AWP: unknown (only {body_hits} body-shot kills on record)")
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
        played = (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0)
        if 0 < played < 13:
            # a map can't end under 13 rounds (MR12): a forfeit / technical win (BO3.gg 'done' 1-0, 2026-10-11 audit:
            # foggers' de_anubis vs Ground Zero, 1 round) - not a played map, kept out of every total and KPR
            continue
        rows.append({
            "game_id": g["id"], "match_id": mt["id"], "match": mt.get("slug"),
            "date": (g.get("begin_at") or mt.get("start_date") or "")[:10],
            "start": mt.get("start_date"),        # the series' scheduled start (forecast cutoff, 2026-10-13)
            "number": g.get("number"), "map": g.get("map_name"),
            "team_id": my_team, "team": (teams.get(my_team) or {}).get("name") or me.get("clan_name"),
            "team_slug": (teams.get(my_team) or {}).get("slug"),
            "opponent_id": opp_id, "opponent": (teams.get(opp_id) or {}).get("name") or me.get("enemy_clan_name"),
            "kills": me.get("kills"), "headshots": me.get("headshots"), "deaths": me.get("death"),
            "kast": me.get("kast"), "rounds": (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0),
            "won": bool(me.get("win")), "steam_id": (me.get("steam_profile") or {}).get("steam_id_64"),
            "lineup": [((r.get("steam_profile") or {}).get("player") or {}).get("slug")
                       for r in ps if (r.get("team_clan") or {}).get("team_id") == my_team],
            "opp_lineup": [((r.get("steam_profile") or {}).get("player") or {}).get("slug")
                           for r in ps if (r.get("team_clan") or {}).get("team_id") == opp_id],
        })
    return rows


def recent_form(rows, window_kpr, line=None, stat="kills", need=2):
    """Last 10 matches, Maps 1-2 only: KPR (for the recency blend) and each match's Maps 1-2
    total vs the line. need=1: Map 1 lines - every match's Map 1, BO1s included."""
    series = {}
    for r in rows:
        series.setdefault(r["match_id"], []).append(r)
    out_series = []
    for mid, maps in series.items():
        maps.sort(key=lambda m: m["number"] or 9)
        if len(maps) < need:
            continue  # BO1s and forfeits have no Map 2
        m12 = maps[:need]
        if any(m[stat] is None for m in m12):
            continue  # HLTV series whose headshots the scraper hasn't read yet
        out_series.append({"date": m12[0]["date"], "opponent": m12[0]["opponent"],
                           "source": "HLTV" if m12[0].get("source") == "hltv" else None,
                           "maps": [m["map"] for m in m12], "values": [m[stat] for m in m12],
                           "total": sum(m[stat] for m in m12), "rounds": sum(m["rounds"] for m in m12),
                           "result": ({2: "W", 1: "split", 0: "L"} if need == 2 else {1: "W", 0: "L"})[sum(m["won"] for m in m12)]})
    out_series.sort(key=lambda s: s["date"], reverse=True)
    kr = [r for r in rows if r["rounds"] and r["kills"] is not None and len(series[r["match_id"]]) >= need]
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
        # a total exactly on a whole line is a PUSH (no action) - never an Under (fixed 2026-10-08)
        overs = sum(1 for s in out_series if s["total"] > line)
        pushes = sum(1 for s in out_series if s["total"] == line)
        decided = len(out_series) - pushes
        out.update(line=line, overs=overs, pushes=pushes, decided=decided,
                   hit_rate=round(overs / decided, 3) if decided else None)
    return out


def _pick_namesake(b, exact, team_hint):
    """Several players share the nickname: the one on the team the line lists (Underdog), else his
    latest HLTV team (HLTV first - the official record), else the one whose team plays soon, else
    None (first search hit)."""
    teams = {}
    for h in exact[:4]:
        try:
            teams[h["slug"]] = ((b.player(h["slug"]).get("team") or {}).get("name") or "")
        except Exception:
            teams[h["slug"]] = ""
    if not team_hint:
        try:
            import hltv
            team_hint = hltv.team_of(exact[0].get("nickname"))
        except Exception:
            team_hint = None
    if team_hint:
        for sl, t in teams.items():
            # exact name variants / aliases only (2026-10-13 audit: a team CONTAINING the hint was taken - 'Spirit' picked the
            # Spirit Academy player, 'OG' the Dogs Gaming one)
            if t and _same_team(team_hint, t):
                return sl
    try:
        playing = {norm(x) for m in _slate_raw(b, 72) for x in ((m.get("team1") or {}).get("name"), (m.get("team2") or {}).get("name")) if x}
        for sl, t in teams.items():
            if t and norm(t) in playing:
                return sl
    except Exception:
        pass
    return None


def run_player(b, name, args):
    hits = b.search_players(name, 10).get("results") or []
    if not hits and re.sub(r"[\W_]+$|^[\W_]+", "", name) != name:   # Underdog's "m0NESY-"
        name = re.sub(r"[\W_]+$|^[\W_]+", "", name)
        hits = b.search_players(name, 10).get("results") or []
    if not hits:                                   # typo ("monesy"): the real nick from HLTV's player list
        try:
            import hltv
            real = hltv.canonical_nick(name)
        except Exception:
            real = None
        if real and norm(real) != norm(name):
            name = real
            hits = b.search_players(name, 10).get("results") or []
    if args.bo3_slug:
        slug = args.bo3_slug
    elif hits:
        exact = [h for h in hits if norm(h.get("nickname")) == norm(name)]
        slug = (exact or hits)[0]["slug"]
        if len(exact) > 1:                         # two pros with this name (JBa): pick by team
            slug = _pick_namesake(b, exact, getattr(args, "team_hint", None)) or slug
    else:
        raise LookupError(f"no pro player called '{name}' - check the spelling")
    p = b.player(slug)
    team = p.get("team") or {}
    joined = datetime.fromisoformat(p["joined_team_at"][:10]).date() if p.get("joined_team_at") else None
    out = {"player": {"id": p["id"], "slug": slug, "nick": p.get("nickname")},
           "current_team": team.get("name"), "team_slug": team.get("slug"), "team_id": team.get("id"),
           # the BO3.gg PROFILE team as read (2026-10-13): current_team can be rewritten in run() (a move / his new team's
           # roster lists him); the pick log stores this as profile_team next to the team he plays tonight's match for
           "profile_team": team.get("name"),
           "joined_team": joined.isoformat() if joined else None}

    # Gate C - roster
    roster = []
    if team.get("slug"):
        td = {}
        # tslug, never `slug` (2026-10-14 audit: the loop overwrote the PLAYER's slug, so window_stats below read the team
        # slug's stats - the AWP hit-group test ran on the wrong data)
        for tslug in dict.fromkeys(x for x in (team["slug"], norm(team.get("name"))) if x):
            try:                                   # BO3.gg sometimes renames a team's page (mousesports -> mouz)
                td = b.team(tslug)
                break
            except Exception:
                continue
        roster = [x for x in td.get("players") or [] if not x.get("is_coach")]
    out["roster"] = [x.get("nickname") for x in roster]
    # BO3.gg marks benched players with status 2 (2026-10-09: Aurora's page lists MAJ3R benched) - display only
    out["benched"] = [x.get("nickname") for x in roster if x.get("status") == 2]
    in_roster = any(x.get("id") == p["id"] for x in roster)
    listed = args.team
    if listed:
        # canonical identity, never a substring (2026-10-13 audit: 'Mine Academy' passed for 'Mine')
        out["on_listed_team"] = bool(team.get("name")) and (_same_team(listed, team.get("name")) or
                                                             norm(listed) == norm(team.get("slug")))
        out["transfer_check"] = "OK" if out["on_listed_team"] else f"MISMATCH - BO3.gg shows {team.get('name')}, not {listed}"

    matches = b.player_matches(p["id"]).get("results") or []
    last = next((m.get("start_date") for m in matches if m.get("status") == "finished"), None)
    out["days_since_last_map"] = (date.today() - date.fromisoformat(last[:10])).days if last else None

    # stats behind the projection: Maps 1-2 only, from each map's scoreboard
    overall, games = waterfall(b, matches, p["id"], joined)
    # v7.5 step 2: KPR and HS% = 0.50 x 3m + 0.30 x 6m + 0.20 x 12m (Maps 1-2), dropping any window
    # with <10 Maps 1-2. Walk-forward (900 cases): miss kills 6.07 -> 6.01, HS 3.76 -> 3.69.
    windows, wrows_by = {}, {}
    year = (date.today() - timedelta(days=365)).isoformat()
    menv = match_envs(b, [m for m in matches if (m.get("start_date") or "")[:10] >= year])
    game_env = {g["id"]: menv.get(m["id"]) for m in matches for g in (m.get("games") or []) if g.get("id")}
    out["_match_env"] = menv
    for label, (wdays, _) in FORM_BLEND.items():
        wrows = player_rows(window_games(b, matches, wdays, BLEND_MAX_MAPS), p["id"])
        for r in wrows:
            r["env"] = game_env.get(r.get("game_id"))
        wrows_by[label] = wrows
        if label == "12m":
            out["_opp_rows"] = [(r["opponent"], r["kills"], r["rounds"], r["env"]) for r in wrows if r["rounds"] and r["opponent"]]
        a = agg([r for r in wrows if r["number"] <= 2])
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
    out["overall_env"] = {env: _blend_windows({lb: [r for r in rs if r["env"] == env] for lb, rs in wrows_by.items()})
                          for env in ("lan", "online")}
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
    # active teammates only: benched players (status 2) never rank his role (2026-10-13 audit: 5-6 'teammates')
    mates = [x for x in roster if x.get("id") != p["id"] and x.get("status") != 2]
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
    # his last 10 matches include BO1s / forfeits: the last-10 reaches back into older BO3.gg matches (12 months) for the
    # series those leave out - read here, where the BO3.gg client is, only when the last 10 fall short (2026-10-11 audit)
    n_ok = sum(1 for mid in {r["match_id"] for r in recent_rows} if sum(1 for r in recent_rows if r["match_id"] == mid) >= 2)
    out["_older_maps"] = []
    if n_ok < RECENT_MATCHES:
        cut = (date.today() - timedelta(days=365)).isoformat()
        more = [mt for mt in matches if mt.get("id") not in seen and mt.get("status") == "finished"
                and (mt.get("start_date") or "")[:10] >= cut][:2 * (RECENT_MATCHES - n_ok) + 2]
        try:
            out["_older_maps"] = map_rows(b, p["id"], more, lambda mt, g: (g.get("number") or 9) <= 2) if more else []
        except Exception:
            out["_older_maps"] = []
    out["_rf_kpr"] = overall.get("kpr")          # kept so a new line only redoes the cheap part
    out["recent_form"] = recent_form(recent_rows, overall.get("kpr"), args.line,
                                     "headshots" if getattr(args, "prop", "kills") == "hs" else "kills")
    out["steam_id"] = next((r["steam_id"] for r in recent_rows if r["steam_id"]), None)

    # stand-in / roster confirmation: his team's ACTIVE roster vs the lineup of his latest map for that team
    # Being missing from his own team's roster page is not "stand-in" (sixth men, 5-man pages,
    # a profile BO3.gg hasn't updated). Stand-in is decided in run(), once we know who he plays for.
    out["on_profile_roster"] = in_roster
    out["is_standin"] = False
    out["roster_confirmed"], out["lineup_check"] = _lineup_check(roster if team.get("slug") else None, p["id"],
                                                                 _lineup_for(recent_rows, team.get("id")))
    if recent_rows and recent_rows[0]["team_id"] != team.get("id"):
        out["roster_note"] = f"Last map was played for {recent_rows[0]['team']}, not {team.get('name')}"
    out["_matches"] = matches
    return out


_REGIONS = {"mtime": None, "data": {}}
GROUPED_REGIONS = {"CIS", "Oceania", "South America", "North America"}   # owner 2026-10-11: NRG (US/CA players) is an NA team
_NAT = {}                   # team slug -> (time, tag): rosters change rarely, 30-min memory
_NAT_TTL = 1800


def load_regions():
    """regions.csv: country_code -> region the owner groups it with (e.g. RU/UA/KZ).
    Read once and kept in memory (re-read only if the file changes)."""
    try:
        mt = REGIONS_CSV.stat().st_mtime
    except OSError:
        return {}
    if _REGIONS["mtime"] != mt:
        # Owner rule 2026-09-29: regional series follow the opponent's COUNTRY (a Polish core ->
        # only Polish teams), except these regions, which stay grouped as one.
        with REGIONS_CSV.open(encoding="utf-8-sig", newline="") as f:
            _REGIONS["data"] = {r["country_code"].strip().upper(): r["region"].strip()
                                for r in csv.DictReader(f)
                                if r.get("country_code") and (r.get("region") or "").strip() in GROUPED_REGIONS}
        _REGIONS["mtime"] = mt
    return _REGIONS["data"]


_ALL_REGIONS = {"mtime": None, "data": {}}


def country_regions():
    """regions.csv: every country_code -> its region (Europe, Asia, Africa, North America, South America, Oceania, CIS, ...)."""
    try:
        mt = REGIONS_CSV.stat().st_mtime
    except OSError:
        return {}
    if _ALL_REGIONS["mtime"] != mt:
        with REGIONS_CSV.open(encoding="utf-8-sig", newline="") as f:
            _ALL_REGIONS["data"] = {r["country_code"].strip().upper(): r["region"].strip() for r in csv.DictReader(f)
                                    if r.get("country_code") and (r.get("region") or "").strip() not in ("", "Other")}
        _ALL_REGIONS["mtime"] = mt
    return _ALL_REGIONS["data"]


def nationality(b, team_slug):
    """Gate C nationality tag (memory-cached 30 min per team)."""
    hit = _NAT.get(team_slug)
    if hit and time.time() - hit[0] < _NAT_TTL:
        return hit[1]
    tag = _nationality(b, team_slug)
    if tag is not None:
        if len(_NAT) > 5000:
            _NAT.clear()
        _NAT[team_slug] = (time.time(), tag)
    return tag


def _nationality(b, team_slug):
    """Gate C nationality tag from the CURRENT roster (benched players don't count). Owner rules: 3+ players from one
    country = that country (2026-09-29: "a Polish core -> only Polish teams"), countries in a grouped region (CIS, Oceania,
    South America, North America) share the region; 2026-10-12: "3 players from the same region = that team's region" -
    a mixed roster with 3+ players from one region (e.g. PL / RO / ES / FI / TR) is that region (Europe), where it used to
    get no tag at all - except Europe, which stays on country flags (owner 2026-10-12: a mixed European roster takes
    HLTV's country flag, never a "Europe" tag). HLTV's single team flag only when the roster can't decide. Every tag carries its region, so
    regional_rows can fall back from a country to its region. Nothing is inferred from a player with no country."""
    try:
        td = b.team(team_slug)
        roster = [p for p in (td.get("players") or []) if not p.get("is_coach") and p.get("status") != 2]
    except Exception:
        return None
    grouped, regmap = load_regions(), country_regions()
    counts, names, codes, rcounts = {}, {}, [], {}
    for p in roster:
        c = p.get("country") or {}
        code = (c.get("code") or "").upper()
        codes.append(code or "?")
        if code:
            key = grouped.get(code, code)          # grouped countries share one key
            counts[key] = counts.get(key, 0) + 1
            names[key] = key if code in grouped else c.get("name")
            if regmap.get(code):
                rcounts[regmap[code]] = rcounts.get(regmap[code], 0) + 1
    top = max(counts.items(), key=lambda kv: kv[1]) if counts else (None, 0)
    if top[1] >= NATIONALITY_MIN:
        region = top[0] if top[0] in GROUPED_REGIONS else regmap.get(top[0])
        return {"id": top[0], "label": names[top[0]], "players": top[1], "of": len(roster), "roster": codes, "source": "roster",
                "kind": "group" if top[0] in GROUPED_REGIONS else "country", "region": region}
    rtop = max(((k, v) for k, v in rcounts.items() if k not in COUNTRY_ONLY_REGIONS), key=lambda kv: kv[1], default=(None, 0))
    if rtop[1] >= NATIONALITY_MIN:
        return {"id": "region:" + rtop[0], "label": rtop[0], "players": rtop[1], "of": len(roster), "roster": codes,
                "source": "roster (region)", "kind": "region", "region": rtop[0]}
    try:                                       # roster can't decide: HLTV's team flag (hltv-scraper teams.country)
        import hltv
        hc = hltv.team_country(td.get("name"))
    except Exception:
        hc = None
    if hc and (hc[0] or hc[1]):
        code, cname = (hc[0] or "").upper(), hc[1] or ""
        if len(code) == 2 and code not in ("EU", "WW", "UN"):
            key = grouped.get(code, code)
            region = key if key in GROUPED_REGIONS else regmap.get(code)
            return {"id": key, "label": key if code in grouped else (cname or code), "source": "HLTV flag", "roster": codes,
                    "kind": "group" if key in GROUPED_REGIONS else "country", "region": region}
        if cname and "cis" in cname.lower() and grouped.get("RU"):
            return {"id": grouped["RU"], "label": grouped["RU"], "source": "HLTV flag", "roster": codes, "kind": "group", "region": grouped["RU"]}
        return {"id": None, "label": f"international team ({cname or code})", "source": "HLTV flag", "roster": codes}
    return {"id": None, "label": f"mixed roster ({'/'.join(codes)})", "roster": codes}


def team_tag(b, team_slug, lookups):
    """A past opponent's tag, looked up once per regional search (REGIONAL_MAX_LOOKUPS distinct teams)."""
    if not team_slug:
        return None
    if team_slug not in lookups["seen"]:
        if lookups["n"] >= REGIONAL_MAX_LOOKUPS:
            lookups["capped"] = True
            return None
        lookups["n"] += 1
        lookups["seen"][team_slug] = nationality(b, team_slug)
    return lookups["seen"][team_slug]


def same_tag(b, team_slug, tag, lookups, level="tag"):
    """level "tag": the same country / grouped region / region as tonight's opponent; level "region": the same region."""
    t = team_tag(b, team_slug, lookups)
    if not t:
        return False
    if level == "region" or tag.get("kind") == "region":
        return bool(t.get("region")) and t.get("region") == tag.get("region")
    return t.get("id") is not None and t["id"] == tag["id"]


def regional_rows(b, pb, opp_id, tag):
    """Maps 1-2 of the player's latest series (last REGIONAL_DAYS) vs OTHER teams sharing the opponent's tag.
    A country tag (e.g. Poland) with fewer than REGIONAL_MIN_COUNTRY such series is filled with same-REGION series (Europe),
    marked regional="region": shown as context, never a grade input (grader_inputs keeps only regional=True rows).
    A region tag (mixed roster) gives region rows only. Returns rows; the search stats are left in pb["_regional_stats"]."""
    lookups, cut = {"n": 0, "seen": {}, "capped": False}, (date.today() - timedelta(days=REGIONAL_DAYS)).isoformat()
    fin = [mt for mt in pb["_matches"] if mt.get("status") == "finished" and (mt.get("start_date") or "")[:10] >= cut
           and opp_id not in (mt.get("team1_id"), mt.get("team2_id"))]

    def collect(level, mark, limit, skip):
        rows, series = [], 0
        for mt in fin:
            if series >= limit:
                break
            if mt.get("id") in skip:
                continue
            if not any(same_tag(b, (mt.get(s_) or {}).get("slug"), tag, lookups, level) for s_ in ("team1", "team2")):
                continue
            got = [r for r in map_rows(b, pb["player"]["id"], [mt], lambda m, g: (g.get("number") or 9) <= 2)
                   if same_tag(b, next(((mt.get(s_) or {}).get("slug") for s_ in ("team1", "team2")
                                        if mt.get(s_ + "_id") == r["opponent_id"]), None), tag, lookups, level)]
            if len(got) >= 2:
                rows += [dict(r, regional=mark) for r in got]
                skip.add(mt.get("id"))
                series += 1
        return rows, series

    skip = set()
    if tag.get("kind") == "region":
        rows, n_region = collect("region", "region", REGIONAL_MAX_SERIES, skip)
        n_tag = 0
    else:
        rows, n_tag = collect("tag", True, REGIONAL_MAX_SERIES, skip)
        n_region = 0
        if (n_tag < REGIONAL_MIN_COUNTRY and tag.get("kind") == "country" and tag.get("region")
                and tag["region"] not in COUNTRY_ONLY_REGIONS):
            more, n_region = collect("region", "region", REGIONAL_MAX_SERIES - n_tag, skip)
            rows += more
    pb["_regional_stats"] = {"tag_series": n_tag, "region_series": n_region, "teams_checked": lookups["n"],
                             "capped": lookups["capped"], "matches_in_window": len(fin)}
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


def event_pool(b, nxt):
    """This event's map pool, VERIFIED: the 7 maps of the latest finished BO3 veto either team played at the same
    event (BO3.gg). None when there is none yet (first BO3 of the event, BO1 events) - callers fall back, labelled."""
    ev = (nxt or {}).get("event_slug")
    if not ev:
        return None
    best = None
    for tid in ((nxt or {}).get("team1_id"), (nxt or {}).get("team2_id")):
        if not tid:
            continue
        try:
            ms = b.team_recent(tid, limit=30).get("results") or []
        except Exception:
            continue
        for m in ms:
            if ((m.get("tournament") or {}).get("slug") != ev or m.get("status") != "finished"
                    or (best and (m.get("start_date") or "") <= best[0])):
                continue
            try:
                d = b.match_veto(m["slug"])
            except Exception:
                continue
            vs = d.get("match_maps") or []
            names = {v.get("map_name") or (v.get("maps") or {}).get("map_name") for v in vs} - {None}
            if d.get("bo_type") == 3 and len(names) == 7:
                best = (m.get("start_date") or "", names, m["slug"])
    if not best:
        return None
    import hltv
    return {"keys": sorted(hltv.map_key(x) for x in best[1]), "from_match": best[2]}


def predict_maps(b, team1_id, team2_id, before=None, pool_keys=None):
    """Probability of each map being Map 1 and Map 2, from both teams' veto habits."""
    p1, p2 = veto_profile(b, team1_id, before), veto_profile(b, team2_id, before)
    pool = p1["pool"] | p2["pool"]
    pool_source = "veto history"
    try:
        import hltv
        if pool_keys:                                    # this event's verified pool
            pool = {m for m in pool if hltv.map_key(m) in set(pool_keys)}
            pool_source = "event veto"
        elif not before:                                 # live fallback: INFERRED from recent play (labelled)
            act = hltv.active_map_pool()
            if act:
                pool = {m for m in pool if hltv.map_key(m) in act}
                pool_source = "inferred (maps played in the last 30 days)"
    except Exception:
        pass
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
    return {"map1": top(slot1), "map2": top(slot2), "vetoes_used": {"team1": p1["n"], "team2": p2["n"]}, "pool_source": pool_source}


def _hltv_map_prediction(team, opponent, pool_keys=None):
    """Likely Map 1 / Map 2 from both teams' HLTV vetoes (hltv.predict_maps); None if unknown."""
    if not team or not opponent:
        return None
    try:
        import hltv
        pr = hltv.predict_maps(team, opponent, date.today().isoformat(), pool_keys=pool_keys)
    except Exception:
        return None
    if not pr:
        return None
    fix = lambda d: {("de_" + k): round(v, 3) for k, v in d.items() if v >= 0.005}
    return {"map1": fix(pr["map1"]), "map2": fix(pr["map2"]), "source": "HLTV vetoes", "pool_source": pr.get("pool_source"),
            "vetoes_used": {"team1": pr["history"][0], "team2": pr["history"][1]}}


def _joined(pb):
    return date.fromisoformat(pb["joined_team"]) if pb.get("joined_team") else None


def upcoming_matches(b, team_id):
    """Every match the team has on BO3.gg that has NOT started and has a known opponent, earliest
    first. A team can play 2+ times in a day - the member picks which one the line is for."""
    now = datetime.now(timezone.utc).isoformat()
    # BO3.gg's per-team search misses some matches (e.g. a 2nd match the same day), so the full
    # 3-day schedule is merged in
    rows = {m.get("slug"): m for m in (b.team_upcoming(team_id).get("results") or [])}
    try:
        for m in _slate_raw(b, 72):
            if team_id in (m.get("team1_id"), m.get("team2_id")):
                rows.setdefault(m.get("slug"), m)
    except Exception:
        pass
    ms = [m for m in rows.values()
          if (m.get("team1") or {}).get("name") and (m.get("team2") or {}).get("name")
          and m.get("status") == "upcoming" and (m.get("start_date") or "") > now[:19]]
    return [_match_info(m, team_id) for m in sorted(ms, key=lambda m: m.get("start_date") or "9999")]


def next_match(b, team_id, want=None):
    """The team's next match that has NOT started (or the one with slug `want`): opponent + vetoed
    maps in order. A match in progress is skipped - its props are locked."""
    ms = upcoming_matches(b, team_id)
    return next((m for m in ms if want and m["slug"] == want), ms[0] if ms else None)


def _match_info(m, team_id):
    opp = m["team2"] if m.get("team1_id") == team_id else m["team1"]
    mine = m["team1"] if m.get("team1_id") == team_id else m["team2"]
    return {"start": m.get("start_date"), "slug": m.get("slug"), "opponent": opp.get("name"), "bo": m.get("bo_type"),
            "team1_id": m.get("team1_id"), "team2_id": m.get("team2_id"),
            "opponent_id": opp.get("id"), "opponent_slug": opp.get("slug"), "team_slug": (mine or {}).get("slug"),
            "event": (m.get("tournament") or {}).get("name"), "event_slug": (m.get("tournament") or {}).get("slug"),
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
    rows = map_rows(b, player_block["player"]["id"], player_block["_matches"], vs)
    # the opponent must be the OTHER side: a series where he played FOR that team (ArtFr0st on
    # Spirit in 2023) is not a meeting with it
    rows = [r for r in rows if (r.get("opponent_id") == opp_id if opp_id else norm(r.get("opponent")) == q)]
    cut = (date.today() - timedelta(days=H2H_DAYS)).isoformat()
    return [r for r in rows if (r.get("date") or "") >= cut]


def active_roster(players):
    """Tonight's opponent lineup for the same-roster test: players only, benched (BO3.gg status 2) left out - the owner rule
    'benched players are ignored' (2026-10-11 audit: Johnny Speeds' benched trio made a 0-overlap meeting 'same roster')."""
    return [x.get("slug") for x in (players or []) if not x.get("is_coach") and x.get("status") != 2]


def _lineup_for(rows, team_id):
    """The lineup (player slugs) of his newest Maps 1-2 map for team_id in rows (newest first); None when there is none."""
    r = next((r for r in rows or [] if team_id is not None and r.get("team_id") == team_id and r.get("lineup")), None)
    return list(r["lineup"]) if r else None


def _lineup_check(players, pid, lineup):
    """Gate C, 'Roster: Confirmed' (2026-10-13 audit: benched players and coaches counted). From the team's ACTIVE roster
    page (benched - status 2 - and coaches never count) and the lineup of his latest map for that team:
    True = he is on it, it names 5+ active players and 4+ of that lineup are among them; False = known not to be so (he is
    benched / not on a 5-player page, or the lineup has changed); None = unknown (no roster page with 5 active players, or
    no lineup of a map he played for the team) - never 'confirmed', never 'not confirmed' for missing information.
    A team roster is not the starting lineup of the graded match: no source publishes that before the match
    (starting_lineup None). Returns (confirmed, detail)."""
    listed = [x for x in players or [] if not x.get("is_coach")]
    active = [x for x in listed if x.get("status") != 2]
    slugs = {x.get("slug") for x in active if x.get("slug")}
    me = next((x for x in listed if x.get("id") == pid), None)
    known = len(active) >= 5
    seen = set(lineup or ()) - {None}
    overlap = len(seen & slugs) if seen and known else None
    if me is not None and me.get("status") == 2:
        ok = False                                   # benched on the page: never confirmed
    elif not known:
        ok = None
    elif me is None:
        ok = False
    else:
        ok = None if overlap is None else overlap >= 4
    return ok, {"team_roster": len(active) if players is not None else None, "on_team_roster": me is not None and me.get("status") != 2,
                "last_lineup_overlap": overlap, "starting_lineup": None}


H2H_DAYS = 365      # head-to-head: last 12 months only (owner 2026-10-06: older meetings are too far back)
SAME_ROSTER = 4   # past meeting counts only if the opponent fielded 4+ of today's players (HLTV year test)


def h2h(rows, opponent, line=None, stat="kills", opp_roster=None):
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
                       "regional": bool(m12[0].get("regional")),
                       "regional_level": ("region" if m12[0].get("regional") == "region" else "tag") if m12[0].get("regional") else None,
                       "same_roster": (None if not opp_roster or not m12[0].get("opp_lineup") else
                                       len(set(m12[0]["opp_lineup"]) & set(opp_roster)) >= SAME_ROSTER),
                       "roster_overlap": (len(set(m12[0].get("opp_lineup") or []) & set(opp_roster))
                                          if opp_roster and m12[0].get("opp_lineup") else None)})
    series.sort(key=lambda s: s["date"], reverse=True)
    reg = sum(1 for s in series if s["regional"])
    out = {"opponent_query": opponent, "stat": stat, "maps": 2 * len(series), "series": series,
           "exact_maps": 2 * (len(series) - reg), "regional_maps": 2 * reg,
           "single_map_series_skipped": skipped}
    if line is None or not series:
        return out
    dec = [s for s in series if s["total"] != line]             # pushes (total == whole line) are no action
    hits = [s["total"] > line for s in dec]
    rate = sum(hits) / len(hits) if hits else None
    in_w = [s["total"] > line for s in dec if s["result"] == "W"]
    in_l = [s["total"] > line for s in dec if s["result"] == "L"]
    out.update(line=line, hit_rate=round(rate, 3) if rate is not None else None, pushes=len(series) - len(dec),
               hits_in_wins=f"{sum(in_w)}/{len(in_w)}", hits_in_losses=f"{sum(in_l)}/{len(in_l)}",
               clears_in_wins=bool(in_w) and all(in_w), clears_in_losses=bool(in_l) and all(in_l))
    return out


HARD_LIQUIDITY = 1000   # USD


def market_quality(odds):
    """HARD: liquid Polymarket book priced off the bid/ask midpoint. SOFT: thin or wide book.
    NONE: no pre-match market (win chance falls back to 50/50)."""
    if not odds or not odds.get("found") or odds.get("team_ml") is None:
        return "NONE"
    if odds.get("book"):                 # OddsPapi: Pinnacle / exchange = sharp; others soft
        return "HARD" if odds["book"] in ("pinnacle", "betfair-ex") else "SOFT"
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


def _hltv_swing(pb):
    try:
        import hltv
        return hltv.grade_swing(pb.get("hltv"))
    except Exception:
        return None


def _attach_hltv(pb):
    """HLTV numbers from the hltv-scraper database (hltv.py). Context, plus the round swing."""
    if not pb:
        return   # looked up every grade: hltv.py caches 15 min and short-circuits when the DB is down
    try:
        import hltv
        last = (pb.get("recent_maps") or [{}])[0].get("date")
        pb["hltv"] = hltv.player_numbers((pb.get("player") or {}).get("nick"), pb.get("current_team"), last)
    except Exception as e:   # HLTV data is optional: it can never break a grade
        import hltv
        pb["hltv"] = {"found": False, "error": hltv.err_text(e), "transient": hltv.is_transient(e)}


from comparison import JOIN_START_MIN    # same match: scheduled starts within 45 min (one rule site-wide)


def _same_team(a, b):
    """The same team by canonical identity (hltv.same_team, 2026-10-13 audit): the HLTV team ids when both names resolve -
    exact name, verified alias (team_aliases.json), org affix - else exact name variants ('Nordic Partners Gaming' =
    'Nordic Partners'). Never fuzzy: an academy is another team, 'MOUZ' is not 'MOUZ NXT'."""
    if not a or not b:
        return False
    try:
        import hltv
        return hltv.same_team(a, b)
    except Exception:
        return norm(a) == norm(b)


def _by_start(items, start, start_of):
    """The items (upcoming matches) whose start is within JOIN_START_MIN of the book's start time - a book line names its
    match by opponent AND start, so a misspelled opponent still finds the one match at that time. [] without a start, or
    when several different matches fit (never guess between them)."""
    st = _iso(start)
    if not st:
        return []
    if st.tzinfo is None:
        st = st.replace(tzinfo=timezone.utc)
    out = []
    for it in items:
        s0 = start_of(it)
        s0 = s0 if isinstance(s0, datetime) else _iso(s0)
        if s0 is None:
            continue
        if s0.tzinfo is None:
            s0 = s0.replace(tzinfo=timezone.utc)
        if abs((s0 - st).total_seconds()) <= JOIN_START_MIN * 60:
            out.append(it)
    return out if len(out) == 1 else []


def _hltv_schedule(team_names, member_opp=None):
    """His teams' upcoming matches on HLTV's schedule (hltv.py); [] on any problem. Teams by canonical identity / verified
    aliases, never a name containing another (2026-10-13 audit: the book's 'MOUZ' took HLTV's MOUZ NXT match)."""
    try:
        import hltv
        hl = hltv.upcoming_for(team_names, lambda a, b: 3 if _same_team(a, b) else 0)
        return [m for m in hl if not member_opp or _same_team(member_opp, m["opponent"])]
    except Exception:
        return []


def _bo_of(fmt):
    """A schedule source's match format as BO3.gg's bo_type (int): 'bo3' / 'BO5' / 3 -> 3 / 5; None when unknown."""
    m = re.search(r"(\d+)", str(fmt or ""))
    n = int(m.group(1)) if m else None
    return n if n in (1, 2, 3, 5, 7) else None


def _iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _hltv_check_bo3(chosen, options, h):
    """Compare BO3.gg's next match with HLTV's. Returns (chosen or None, heads-up note or None).

    None for `chosen` means: BO3.gg is missing his real next match - HLTV lists an earlier one
    (90+ minutes before BO3.gg's) against an opponent BO3.gg has no match with at all. The caller
    then grades HLTV's match. Same opponent at a different time only adds a note."""
    bo3 = chosen[3]
    start = _iso(bo3.get("start"))
    if start and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    when = h["start"].strftime("%a %H:%M UTC")
    if _same_team(h["opponent"], bo3.get("opponent")):          # canonical identity (2026-10-13 audit: was 'contains')
        if start and abs(h["start"] - start) > timedelta(minutes=30):
            return chosen, (f"HLTV has this match at {when} "
                            f"(BO3.gg: {start.strftime('%a %H:%M UTC')}) - check the start time")
        return chosen, None
    on_bo3 = any(_same_team(h["opponent"], o[3].get("opponent")) for o in options)
    if start and h["start"] < start - timedelta(minutes=90) and not on_bo3:
        return None, (f"BO3.gg is missing his team's next match: HLTV lists {h['team']} vs {h['opponent']} "
                      f"at {when}, before BO3.gg's match vs {bo3.get('opponent')} - graded the HLTV match. "
                      "Maps unknown until the veto: all-maps numbers used.")
    return chosen, None


def _hltv_context(pb, args, fresh=False):
    """Everything else the HLTV scraper has for this grade (hltv.context). Display only: nothing
    here reaches grade_leg(), and any failure just leaves the section out."""
    try:
        import hltv
        maps = [m.get("map") for m in pb.get("per_map") or [] if m.get("map")]
        if not maps and pb.get("map_prediction"):   # no maps given: the most likely Map 1 and Map 2
            mp = pb["map_prediction"]
            maps = [next(iter(mp.get(k) or {}), None) for k in ("map1", "map2")]
            maps = [m for i, m in enumerate(maps) if m and m not in maps[:i]]
        last = (pb.get("recent_maps") or [{}])[0].get("date")
        # tonight's LAN / online label from THIS match on HLTV's schedule (its opponent and start) and the team he plays it
        # for (2026-10-13 audit: it came from his own team's next / live match - another match for a stand-in)
        return hltv.context((pb.get("player") or {}).get("nick"), pb.get("current_team"),
                            getattr(args, "opponent", None), maps, last, fresh=fresh,
                            start=getattr(args, "match_start", None), playing_for=pb.get("playing_for"))
    except Exception as e:
        import hltv
        return {"found": False, "error": hltv.err_text(e), "transient": _transient_hltv_error(e)}


def _form_trend(o):
    """3-month vs 12-month kills per round (+0.08 = 8% hotter lately), None without both windows."""
    fw = o.get("form_windows") or {}
    k3, k12 = (fw.get("3m") or {}).get("kpr"), (fw.get("12m") or {}).get("kpr")
    return round(k3 / k12 - 1, 3) if k3 and k12 else None


def _a_an(w):
    return ("an " if str(w)[:1].lower() in "aeiou" else "a ") + str(w)


def match_day(start):
    """The match's UTC date as a day ordinal - the 'day' the performance model was tested with (history strictly
    before the match day, weights relative to it). Today when the start is unknown or already past.
    2026-10-10 fix: live used today's date, so maps played earlier today were left out until midnight UTC and every
    projection shifted at 00:00 UTC with no new information."""
    today = datetime.now(timezone.utc).date().toordinal()
    try:
        d = datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()
    except (TypeError, ValueError):
        return today
    return max(d, today)


def _ckey_name(s):
    """Cache-key form of a book's team name: 'Imperial Esports' (Underdog), 'Imperial' (PrizePicks) and a member's 'imperial'
    share ONE player context, so both boards and the card grade from the same data snapshot (2026-10-11 audit: three contexts
    refreshed minutes apart could differ). Only org suffixes go; academy / female / NXT squads stay distinct."""
    k = norm(s or "")
    k2 = re.sub(r"^team", "", k) if re.match(r"(?i)team\s", str(s or "").strip()) else k   # 'Team Spirit' yes, 'TeamOne' no
    k2 = re.sub(r"(esports?|gaming|clan|club|team)$", "", k2)
    return k2 or k


def _transient_hltv_error(e):
    """A database that is unreachable / timing out (not 'player not found'): the grade must not quietly switch models."""
    import hltv
    return hltv.is_transient(e)


PERF_RETRY_WAIT = 1.0


def _perf_input(pb, args):
    """perf.project() on live HLTV data for this player / opponent / tonight's environment (None when unavailable).
    2026-10-11: an HLTV database blip is retried once (PERF_RETRY_WAIT s) and then reported as transient - the board keeps
    its last HLTV grade and the card says the database is unreachable, instead of a different projection from the old pipeline."""
    sc = getattr(args, "scope", "12") or "12"
    if sc not in ("1", "12", "123"):
        return None
    if pb.get("_env_transient"):                   # tonight's LAN / online flag could not be read (finish_line): the model would
        return {"error": "HLTV database unreachable right now (tonight's environment unknown)", "transient": True}   # silently project without it
    for attempt in (1, 2):
        try:
            return _perf_input_once(pb, args, sc)
        except Exception as e:
            if not _transient_hltv_error(e):
                return {"error": f"performance model failed: {str(e)[:120]}"}
            if attempt == 1:
                import hltv
                if hltv.allow_probe():             # this caller may probe (one at a time); the others report at once, no sleep
                    time.sleep(PERF_RETRY_WAIT)
                    continue
            return {"error": f"HLTV database unreachable right now (retried once): {type(e).__name__}", "transient": True}


def _perf_input_once(pb, args, sc):
    if True:
        import perf_live
        tonight = (pb.get("_env") or {}).get("tonight")
        day = match_day(getattr(args, "match_start", None))
        out = perf_live.projection((pb.get("player") or {}).get("nick"), pb.get("playing_for") or pb.get("current_team"),
                                   getattr(args, "opponent", None), None if tonight is None else tonight == "lan",
                                   sc, "hs" if args.prop == "hs" else "kills", day)
        no_hltv = bool(out.get("error") and "not found in HLTV" in out["error"])
        if not out.get("error") and (out.get("player_maps") == 0 or ("newest_map_day" in out and out["newest_map_day"] is None)):
            # HLTV knows the player but has no usable map before the match day (all older than 420 days, or only
            # substitution maps): that is no HLTV history too - not a pro-average projection (2026-10-11 audit)
            no_hltv = True
            out = {"error": f"player {(pb.get('player') or {}).get('nick')!r} has no HLTV maps in the last 420 days",
                   "hltv_player_ids": out.get("hltv_player_ids")}
        if no_hltv:
            # perf-2-bo3 (live, provisional): the same model on his BO3.gg maps when HLTV has no history
            me = pb.get("_match_env") or {}
            rows = [(date.fromisoformat(r["date"]).toordinal(), r["kills"], r["rounds"], r.get("headshots"),
                     (r.get("env") or me.get(r.get("match_id"))) == "lan",
                     not (r.get("headshots") == 0 and (r["kills"] or 0) >= 12))
                    for r in pb.get("recent_maps") or [] if r.get("kills") is not None and r.get("rounds") and r.get("date")]
            if rows:
                import perf
                since = (date.today() - timedelta(days=420)).isoformat()
                opp = getattr(args, "opponent", None)
                tid, om = perf_live.opp_maps(opp, since) if opp else (None, [])
                lan_ = None if tonight is None else tonight == "lan"
                pr_ = "hs" if args.prop == "hs" else "kills"
                bo3 = perf.project(rows, om, lan_, day, sc, pr_, version=perf.VERSION + "-bo3")
                bo3.update(source="BO3.gg maps (HLTV has no history)", player_maps=len(rows),
                           hltv_opp_team=tid, hltv_error=out["error"], hltv_player_ids=out.get("hltv_player_ids"),
                           opp_maps=len(om), opp_known=bool(om),
                           g7=perf_live.g7_fields(perf.project(rows, om, lan_, day, sc, pr_,
                                                              perf.PARAMS_PERF1, perf.SPREAD_PERF1, "perf-1-bo3")))
                if not om:
                    dq = bo3.get("data_quality")
                    bo3["data_quality"] = ((dq + "; ") if dq not in (None, "ok") else "") + ("opponent not in HLTV" if not tid else "opponent has no HLTV maps") + " - average opponent"
                return bo3
        return out                         # {"error": ...} when unavailable - the grader says so instead of silently falling back


WINDOW_DAYS = {"last_3m": 90, "last_6m": 180, "last_12m": 365}


def hltv_first_gates(pb, d):
    """The stake checks read HLTV first; BO3.gg is the backup (owner 2026-10-10: "bo3 should be a backup plan").
    - AWPer on a headshot line: HLTV weapon stats (AWP share of kills, 90 days), then Liquipedia roles - the same answer
      the board shows - and BO3.gg's body-shot rate only when neither knows him.
    - Days since his last official map: the most recent of HLTV and BO3.gg (either can lag the other).
    - Sample size: his Maps 1-2 count in the same window, the larger of HLTV and BO3.gg (BO3.gg misses events HLTV has).
    Each source used is recorded in d["gate_sources"]."""
    nick, team = (pb.get("player") or {}).get("nick"), pb.get("playing_for") or pb.get("current_team")
    src = d.setdefault("gate_sources", {})
    try:
        import roles
        roles.refresh_hltv()                        # 30-minute cache; never raises
        awp, why = roles.awp_role(nick, team)
        if awp is not None:
            d["sniping"] = 100 if awp else 0
            pb["awp_note"] = ("AWPer" if awp else "Rifler") + f" ({why})"
            src["awp"] = why
    except Exception:
        pass
    src.setdefault("awp", "BO3.gg body-shot rate" if d.get("sniping") is not None else "unknown")
    pf = d.get("perf") if isinstance(d.get("perf"), dict) else {}
    if pf.get("newest_map_day"):
        days = datetime.now(timezone.utc).date().toordinal() - int(pf["newest_map_day"])
        b3 = d.get("days_since_last_map")
        d["days_since_last_map"] = days if b3 is None else min(days, b3)
        pb["days_since_last_map"] = d["days_since_last_map"]
        src["rest"] = "HLTV" if b3 is None or days <= b3 else "BO3.gg (more recent than HLTV's data)"
    try:
        import hltv
        win = WINDOW_DAYS.get((pb.get("overall") or {}).get("window"), 365)
        since = (datetime.now(timezone.utc).date() - timedelta(days=win)).isoformat()
        n = len(hltv.recent_series(nick, team, since)) if nick else 0      # Maps 1-2 rows (both maps known)
        if n and n > (d.get("maps_played") or 0):
            d["maps_played"] = n
            src["sample"] = f"HLTV ({n} Maps 1-2 in the window)"
        if nick and hltv.lookup_failed(("series", hltv._norm(nick), hltv._norm(team), since)):
            d["hltv_transient"] = "his HLTV series could not be read"
    except Exception:
        pass
    # the last-10 / head-to-head HLTV read (12 months) and the HLTV-only overall read: a failed read with no good copy is a
    # blip, never a quiet switch to BO3.gg's rows (2026-10-13 audit) - a good, EMPTY answer (no HLTV history) is not
    if (pb.get("hltv_series") or {}).get("state") == "failed":
        d["hltv_transient"] = "his HLTV series (last 10 / head-to-head) could not be read"
    if pb.get("_hltv_overall_tried") is False:
        d["hltv_transient"] = "his HLTV maps could not be read"
    if (pb.get("hltv") or {}).get("transient"):
        d["hltv_transient"] = "his HLTV numbers could not be read"
    src.setdefault("sample", "BO3.gg")
    return d


def _cli_odds(args, odds):
    """CLI only (--team-ml / --opp-ml, the owner's local tool): the typed prices REPLACE the market input as a whole - one
    object for the moneylines, the exact probability, quality (the existing bookmaker rule: a typed price is SOFT), map
    prices (none) and provenance. Before 2026-10-13 the typed moneylines sat on top of the selected source's provenance,
    quality and map prices. Web grades (args.web) never use it. Idempotent: an object it made passes through (finish_line
    stores it as res["odds"] so the summary prints the input the grade used; grader_inputs calls it again)."""
    tm, om = getattr(args, "team_ml", None), getattr(args, "opp_ml", None)
    if getattr(args, "web", False) or (tm is None and om is None):
        return odds
    if ((odds or {}).get("provenance") or {}).get("selected") == "manual":
        return odds
    from grader import american_to_prob
    from polymarket import prob_to_american
    if tm is not None and om is not None:
        a, b = american_to_prob(tm), american_to_prob(om)
        p = a / (a + b)
    else:
        p = american_to_prob(tm) if tm is not None else 1 - american_to_prob(om)
    tm = tm if tm is not None else prob_to_american(p)
    om = om if om is not None else prob_to_american(1 - p)
    q = ((odds or {}).get("provenance") or {}).get("query") or {}
    return {"found": True, "team_ml": tm, "opp_ml": om, "team_prob": round(p, 6), "opp_prob": round(1 - p, 6),
            "team": (odds or {}).get("team") or q.get("team"), "opponent": (odds or {}).get("opponent") or q.get("opponent"),
            "title": "moneylines typed on the command line", "start": (odds or {}).get("start") or q.get("start"),
            "book": "manual", "maps": {}, "flags": ["Win chance from moneylines typed on the command line"],
            "provenance": {"selected": "manual", "source": "manual (CLI --team-ml / --opp-ml)", "p_team": round(p, 6),
                           "odds_policy": "cli-override", "mapping_rev": None,
                           "replaced": copy.deepcopy((odds or {}).get("provenance"))}}


def _p_team_market(odds):
    """The exact market probability the odds selection produced for his team (None when there is no market)."""
    try:
        import market_odds
        return market_odds.p_team(odds)
    except Exception:
        return None


def grader_inputs(pb, args, odds=None, hh=None):
    """Map everything into the keys grade_leg() documents."""
    o = pb["overall"]
    odds = _cli_odds(args, odds)
    pv_ = (odds or {}).get("provenance") or {}
    d = {
        "prop": args.prop, "line": args.line, "line_type": args.line_type,
        # the sides the PLATFORM FEED offers for this exact line (prizepicks.directions; None = not known: the grader's
        # line-type rule) - 2026-10-14 owner: no blanket goblin / demon 'More only'
        "less_available": getattr(args, "less_available", None),
        "scope": getattr(args, "scope", "12") or "12", "bo": getattr(args, "match_bo", None),
        "maps_played": o.get("maps_played"), "kpr": o.get("kpr"), "hs_pct": o.get("hs_pct"),
        "sample_maps": max((w.get("maps") or 0 for w in (o.get("form_windows") or {}).values()), default=None),
        "kpr_last5": (pb.get("recent_form") or {}).get("kpr_recent"),  # last 10 matches, Maps 1-2
        "form_trend": _form_trend(o),
        "environment": pb.get("_env"),
        "round_swing": _hltv_swing(pb),  # HLTV via hltv.py when available, else None (no swing adjustment)
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
        # which source supplied the win probability (saved with the forecast; the grader itself never reads it) - a COPY:
        # the odds object the grade used stays exactly as selected
        "odds_provenance": copy.deepcopy(pv_) if pv_ else None,
        # 2026-10-13: the exact selected probability (no American-odds round trip; None = no market, never 50%), the odds
        # policy that selected it and the alias / matching revision it was linked under (recorded with every forecast)
        "p_team_market": _p_team_market(odds),
        "odds_policy": pv_.get("odds_policy"), "mapping_rev": pv_.get("mapping_rev") or _mapping_rev(),
        "map_win_probs": [((odds or {}).get("maps") or {}).get(k, {}).get("team_prob")
                          if ((odds or {}).get("maps") or {}).get(k) else None for k in ("map1", "map2")]
                         if (odds or {}).get("found") else None,
        "recent_avg": (pb.get("recent_form") or {}).get("avg_total"),
        "recent_median": (pb.get("recent_form") or {}).get("median_total"),
        "perf": _perf_input(pb, args),         # performance model (perf.py) on live HLTV data - Maps 1-2 / Map 1
        "recent_n": (pb.get("recent_form") or {}).get("matches"),
        # which HLTV answer the last 10 / head-to-head used: read now, the last good copy (memory / stored) and its age, a
        # good empty answer (no HLTV history: BO3.gg rows) or a failure (transient) - recorded, the grader never reads it
        "hltv_series": copy.deepcopy(pb.get("hltv_series")),
        "trade_pr": pb.get("trade_pr"),
        "opp_rank": pb.get("_opp_rank"),          # opponent's VRS rank (HLTV) - elite-opponent drop
        "rank_band": pb.get("_rank_band"),        # his HLTV rate vs teams ranked within 10 of the opponent
        "scout": bool(pb.get("scout")), "standin_for": pb.get("standin_for"),
        "recent": ({"n": rf.get("decided", rf["matches"]), "overs": rf["overs"]}       # pushes left out of n
                   if (rf := pb.get("recent_form") or {}).get("overs") is not None else None),
        "on_listed_team": pb.get("on_listed_team"), "is_standin": pb["is_standin"],
        "roster_confirmed": pb["roster_confirmed"],
    }
    hltv_first_gates(pb, d)
    if d.get("hltv_transient") and isinstance(d.get("perf"), dict) and not d["perf"].get("error"):
        # the projection came through but a stake input did not (2026-10-11 audit): one transient grade, never a quietly
        # different unit size next to the board's
        d["perf"] = {"error": f"HLTV database unreachable right now ({d['hltv_transient']})", "transient": True}
    if odds and odds.get("found") and odds.get("team_ml") is not None:
        d["team_ml"], d["opp_ml"] = odds["team_ml"], odds["opp_ml"]
    if hh and hh.get("series"):
        # exact-opponent series only (regional stand-ins are shown with weight 0)
        today = date.today()
        d["h2h_series"] = [{"age_days": (today - date.fromisoformat(x["date"])).days, "total": x["total"]}
                           for x in hh["series"] if not x.get("regional") and x.get("date")
                           and x.get("same_roster") is not False]
        # region-level fill-ins (2026-10-12) are context only: the tested regional definition is country / grouped region
        d["regional_series"] = [{"age_days": (today - date.fromisoformat(x["date"])).days, "total": x["total"]}
                                for x in hh["series"] if x.get("regional") and x.get("regional_level") != "region" and x.get("date")]
    return d


# ---------------------------------------------------------------- slate
def slate(b, hours=36):
    out = []
    for m in _slate_raw(b, hours):
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


def _slate_raw(b, hours=36):
    """Every CS2 match starting in the next `hours` (raw BO3.gg rows, cached 30 min). Times are
    rounded to the hour so every grade in the same half hour shares one cached schedule."""
    now = datetime.now(timezone.utc)
    out = []
    for page in range(4):
        params = {
            "scope": "widget-matches", "page[offset]": 100 * page, "page[limit]": 100, "sort": "start_date",
            "filter[matches.status][in]": "upcoming,current",
            "filter[matches.start_date][gt]": now.strftime("%Y-%m-%d %H:00"),
            "filter[matches.start_date][lt]": (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:00"),
            "filter[matches.discipline_id][eq]": 1, "with": "teams,tournament,games",
        }
        batch = b._run(b.cs2._api._make_request("/matches", params), ttl=0.5).get("results") or []
        out += batch
        if len(batch) < 100:
            break
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
        # the source that was actually selected (2026-10-13: always said 'Polymarket' and read od['liquidity'], which a
        # Thunderpick / OddsPapi price has not - KeyError)
        pv = od.get("provenance") or {}
        try:
            import market_odds
            src = market_odds.source_label(od) or "Odds"
        except Exception:
            src = "Odds"
        if od.get("found"):
            print(f"\n== {src}: {od.get('title')} ({od.get('start')}) ==")
            liq = od.get("liquidity")
            print(f"{od.get('team')} {od.get('team_prob') or 0:.1%} ({od.get('team_ml')})  vs  "
                  f"{od.get('opponent')} {od.get('opp_prob') or 0:.1%} ({od.get('opp_ml')})"
                  + (f"  | liquidity ${liq:,}" if isinstance(liq, (int, float)) else ""))
            for k, v in (od.get("maps") or {}).items():
                if v:
                    print(f"  {k}: {od.get('team')} {v['team_prob']:.1%}")
            for fl in od.get("flags") or []:
                print(f"  ! {fl}")
        else:
            print(f"\n== No market: {od.get('note') or pv.get('fallback_reason') or 'not found'} ==")
            if pv.get("fallback_reason"):
                print(f"  Thunderpick: {pv['fallback_reason']}")
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
          background=False, match=None, opponent=None, light=False, scope="12", team_hint=None, match_start=None,
          less_available=None):
    """One-call entry point for the web interface: everything 'full' does, returned as a dict.
    background=True (pre-loading) gives way to member look-ups."""
    args = argparse.Namespace(
        cmd="full", name=name, bo3_slug=slug, team=None, maps=None, opponent=None, line=line,
        prop=prop, line_type=line_type, team_ml=None, opp_ml=None, faceit=None, less_available=less_available,
        faceit_key=faceit_key or os.environ.get("FACEIT_API_KEY"), n=20, refresh=False, ttl=ttl,
        match=match, member_opponent=(opponent or "").strip() or None, light=light,
        scope=scope if scope in ("12", "123", "1") else "12", team_hint=team_hint,
        member_start=(str(match_start).strip() or None) if match_start and opponent else None,
        web=True)                                  # web grade: the CLI --team-ml / --opp-ml override never applies
    args.opponent = args.member_opponent
    # Shared context (stats, match, maps, odds, H2H series, FACEIT, CS API) is the same for every
    # line and prop of a player: built once, reused for CONTEXT_TTL seconds - a new line only
    # redoes finish_line(). Keeps hundreds of members on the same slate cheap.
    ckey = _context_key(name, slug, match, args.member_opponent, team_hint, args.faceit_key, args.member_start)
    hit = _CONTEXT.get(ckey)
    t0 = time.time()
    age = time.time() - hit[0] if hit else None
    if hit and CONTEXT_TTL <= age < CONTEXT_STALE and not background:   # members only - the board never triggers one
        _refresh_context(ckey, args, ttl)     # this grade uses the saved data; fresh data is built meanwhile
    if hit and age < CONTEXT_STALE:
        res = copy.deepcopy(hit[1])
        _tick(res, "copy", t0)
        res["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not background and not res.get("csapi"):       # context built by the board: add the CS API check
            try:
                import csapi
                pb_ = res.get("player_block") or {}
                res["csapi"] = csapi.player_check_async((pb_.get("player") or {}).get("nick"), pb_.get("current_team"),
                                                        (pb_.get("recent_maps") or [{}])[0].get("date"))
            except Exception:
                pass
        if (res.get("csapi") or {}).get("pending"):      # background check may have finished since
            try:
                import csapi
                pb_ = res.get("player_block") or {}
                res["csapi"] = csapi.player_check_async((pb_.get("player") or {}).get("nick"), pb_.get("current_team"),
                                                        (pb_.get("recent_maps") or [{}])[0].get("date"))
            except Exception:
                pass
        res["team_hint"] = team_hint                     # the platform's team label for THIS line (pick log fallback)
        if not res.get("error"):
            finish_line(res, args)
        _timing_done(res, name, "hit", background, t0)
        return res
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": "grade", "team_hint": team_hint}
    b = BO3(ttl_hours=ttl, background=background)
    try:
        run(args, res, b, ttl)
    except LookupError as e:
        res["error"] = str(e)
    finally:
        b.close()
    if res.get("player_block"):
        res["player_block"].pop("_matches", None)
    if len(_CONTEXT) > 3000:
        _CONTEXT.clear()
    _store_context(ckey, res)
    _timing_done(res, name, "miss", background, t0)
    return res


def _mapping_rev():
    """The alias / matching revision (mapping.revision(): team_aliases.json + comparison.VERIFIED_TEAM_ALIASES +
    matchid.MATCHING_RULES_VERSION). None when unreadable (never blocks a grade)."""
    try:
        import mapping
        return mapping.revision()
    except Exception:
        return None


def _context_key(name, slug, match, member_opp, team_hint, faceit_key, member_start):
    """The shared-context key: shared by member grades and both boards (same data snapshot). The mapping revision is part
    of it (2026-10-13): a context whose odds were linked under an older alias / matching state is never reused - the
    next grade re-evaluates the line under the new mapping."""
    return ((name or "").strip().lower(), (slug or "").lower(), match, _ckey_name(member_opp), _ckey_name(team_hint),
            bool(faceit_key), (member_start or "")[:16], _mapping_rev())


def _context_tainted(res):
    """Built while the HLTV database blipped (context transient, or the HLTV schedule unreadable so an HLTV-only match may
    have been missed): such a context is rebuilt within a minute instead of serving every grade for 30 minutes."""
    try:
        import hltv
        return bool((res.get("hltv_context") or {}).get("transient") or hltv._STATE.get("schedule_failed"))
    except Exception:
        return False


def _store_context(ckey, res):
    ts = time.time() - (CONTEXT_STALE - 60) if _context_tainted(res) else time.time()
    _CONTEXT[ckey] = (ts, copy.deepcopy(res))


TIMINGS = collections.deque(maxlen=300)   # recent grades' step times (admin /health) - finding slow steps


def _tick(res, step, t0):
    res.setdefault("_t", {})[step] = round(time.time() - t0, 2)


def _timing_done(res, name, kind, background, t0):
    t = res.pop("_t", {}) or {}
    TIMINGS.append({"at": round(time.time()), "player": name, "kind": kind, "board": bool(background),
                    "total": round(time.time() - t0, 2), **t})


import membudget as _membudget  # noqa: E402
# grade contexts (deep copies) bounded by bytes as well as count (2026-10-18 Render out-of-memory fix); a miss recomputes
_CONTEXT = _membudget.BudgetLRU("contexts", _membudget.env_mb("CONTEXT_MEM_MB", 100), max_items=3000)
CONTEXT_TTL = 600          # older than this: rebuilt in the background (the grade doesn't wait)
CONTEXT_STALE = 30 * 60    # older than this: rebuilt before grading
_REFRESHING, _REFRESH_LOCK = set(), threading.Lock()
_REFRESH_SLOTS = threading.BoundedSemaphore(2)


def _refresh_context(ckey, args, ttl):
    """Rebuild one player's shared context in the background (one rebuild per player at a time).
    Members kept waiting 10-40 s behind a rebuild every 2 minutes under load before this."""
    if not _REFRESH_SLOTS.acquire(blocking=False):   # both refreshers busy: skip (no thread pile-up)
        return
    with _REFRESH_LOCK:
        if ckey in _REFRESHING:
            _REFRESH_SLOTS.release()
            return
        _REFRESHING.add(ckey)
    a = copy.copy(args)
    a.light = True

    def work():
        try:
            if True:                            # slot already held (released below)
                res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "command": "grade"}
                b = BO3(ttl_hours=ttl, background=True)
                try:
                    run(a, res, b, ttl)
                finally:
                    b.close()
                if res.get("player_block"):
                    res["player_block"].pop("_matches", None)
                if not res.get("error"):
                    _store_context(ckey, res)
        except Exception:
            pass                                # keep the saved context; the next grade tries again
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(ckey)
            _REFRESH_SLOTS.release()
    threading.Thread(target=work, daemon=True, name="ctx-refresh").start()


def run(args, res, b, ttl):
    if args.cmd == "slate":
        res["slate"] = slate(b)
    pb = None
    if args.cmd in ("player", "h2h", "faceit", "full"):
        pb = run_player(b, args.name, args)
        if args.cmd != "faceit":
            res["player_block"] = pb
    member_opp = getattr(args, "member_opponent", None)   # typed on the form (PrizePicks card)
    if args.cmd in ("h2h", "full") and (not args.opponent or member_opp) and pb:
        # every team he has played for in the last 14 days (stand-ins, loans, recent moves) -
        # grade against whichever of them plays next.
        # No team on BO3.gg (between teams, e.g. ct0m): NEVER guess from an old team. Order:
        # Liquipedia's current team -> old teams only if the member typed an opponent they play ->
        # Polymarket by the typed opponent -> otherwise SCOUT (asks for the opponent).
        no_team = not pb.get("team_id")
        teams = {}
        if no_team:
            try:
                import liquipedia
                lp = liquipedia.player_team(pb["player"]["nick"])
            except Exception:
                lp = None
            if lp and lp.get("team"):
                res["liquipedia"] = lp
                try:
                    hits = b._run(b.cs2.search_teams(lp["team"], 5)).get("results") or []
                    t = next((t for t in hits if norm(t.get("name")) == norm(lp["team"])), None)
                except Exception:
                    t = None
                teams[t["id"] if t else f"lp:{lp['team']}"] = lp["team"]
                pb["team_source"] = "Liquipedia"
        else:
            teams[pb["team_id"]] = pb.get("current_team")
        today_utc = datetime.now(timezone.utc).date()          # same answer on every server clock
        if not no_team or member_opp:
            cutoff = (today_utc - timedelta(days=45 if no_team else STANDIN_DAYS)).isoformat()
            for r in pb.get("recent_maps") or []:
                if r["date"] >= cutoff and r["team_id"] and r["team_id"] not in teams:
                    teams[r["team_id"]] = r["team"]
        rm0 = (pb.get("recent_maps") or [None])[0]
        if (not no_team and rm0 and rm0.get("team_id") and rm0["team_id"] not in teams
                and rm0["date"] >= (today_utc - timedelta(days=60)).isoformat()):
            teams[rm0["team_id"]] = rm0["team"]    # the team of his latest map is always a candidate
        # Only teams he actually plays for now: the team of his most recent map, teams whose
        # current roster lists him, or his profile team if it lists him. A team he stood in for
        # earlier, or an old team he has left, is dropped (it used to make false "stand-ins").
        pid = pb["player"]["id"]
        rm = pb.get("recent_maps") or []
        newest = rm[0]["team_id"] if rm else None
        slugs = {}
        for r in rm:
            slugs.setdefault(r["team_id"], r.get("team_slug"))
        if pb.get("team_id"):
            slugs.setdefault(pb["team_id"], pb.get("team_slug"))

        def lists_him(tid):
            try:
                return any(x.get("id") == pid for x in (b.team(slugs[tid]).get("players") or [])) if slugs.get(tid) else False
            except Exception:
                return False

        def played_their_last(tid):
            """Was he in that team's own latest match? (team data updates faster than his match list)"""
            try:
                last = next((m for m in b.team_recent(tid, limit=5).get("results") or []
                             if m.get("status") == "finished"), None)
                g = sorted((last or {}).get("games") or [], key=lambda g: g.get("number") or 0)
                return bool(g) and any((r.get("steam_profile") or {}).get("player_id") == pid
                                       for r in b.game_players(g[0]["id"]))
            except Exception:
                return True                       # can't check: keep the team rather than guess
        if not member_opp:
            teams = {tid: tn for tid, tn in teams.items()
                     if not isinstance(tid, int) or tid == newest or lists_him(tid)
                     or (tid == pb.get("team_id") and pb.get("on_profile_roster"))
                     or played_their_last(tid)}
        def team_players(tid):
            try:
                return (b.team(slugs[tid]).get("players") or []) if tid is not None and slugs.get(tid) else None
            except Exception:
                return None                           # page unreadable: unknown, never 'not confirmed'

        def team_change(tid, tname):
            """He plays tonight's match for tname, not his BO3.gg profile team: is it his team now, a stand-in or a move?
            tid None = a team BO3.gg has no id for (match found on HLTV / Polymarket / PandaScore). Roster confirmation is
            the graded match's team's: its active roster vs his latest lineup for it (2026-10-13 audit: 'listed + his last
            map was for them' confirmed with benched players, and a team with no page was 'not confirmed', not unknown)."""
            if tid is not None and lists_him(tid):
                # that team's roster lists him: it IS his team - BO3.gg's profile is behind
                pb["team_note"] = f"BO3.gg profile still shows {pb.get('current_team')}; {tname}'s roster lists him"
                pb["current_team"], pb["team_id"] = tname, tid
            elif pb.get("on_profile_roster"):
                pb["standin_for"] = tname                     # real stand-in: his own team still lists him
            else:
                # left his profile team, not on the new roster page yet: a move, not a stand-in
                pb["team_note"] = (f"Now playing for {tname} (not on its BO3.gg roster page yet); "
                                   f"profile still shows {pb.get('current_team')}")
                pb["current_team"] = tname
                if tid is not None:
                    pb["team_id"] = tid
            pb["roster_confirmed"], pb["lineup_check"] = _lineup_check(team_players(tid), pid, _lineup_for(rm, tid))

        options = []
        for tid, tname in teams.items():
            if not isinstance(tid, int):
                continue                      # Liquipedia-only team: no BO3.gg id to search
            for nm_ in upcoming_matches(b, tid):
                options.append((nm_["start"] or "9999", tid, tname, nm_))
        options.sort(key=lambda o: o[0])
        if member_opp:   # the member named the opponent: only matches against them
            # by canonical identity / verified aliases (2026-10-13 audit: 'contains' - the book's 'MOUZ' took a MOUZ NXT match)
            named = [o for o in options if _same_team(member_opp, o[3]["opponent"])]
            if len(named) > 1:       # two meetings with the same team (rematch): the one at the book's start, when it says
                named = _by_start(named, getattr(args, "member_start", None), lambda o: o[3].get("start")) or named
            # the book's opponent can be misspelled (PrizePicks 'Xl' for XI, 2026-10-11): with the book's start time, his
            # team's match starting within JOIN_START_MIN of it is that match (the opponent string only breaks ties)
            options = named or _by_start(options, getattr(args, "member_start", None), lambda o: o[3].get("start"))
        # two+ matches in a day: grade the one the member picked, else the earliest; list the rest
        res["match_options"] = [{"slug": o[3]["slug"], "opponent": o[3]["opponent"], "start": o[3]["start"],
                                 "team": o[2], "event": o[3]["event"]} for o in options[:6]]
        want = getattr(args, "match", None)
        chosen = next((o for o in options if want and o[3]["slug"] == want), options[0] if options else None)
        nxt = None
        # HLTV's schedule (hltv-scraper database) is the most complete: it fills in matches BO3.gg
        # doesn't have, and corrects BO3.gg when it's missing his team's real next match.
        hl = _hltv_schedule([n for n in teams.values() if n], member_opp)
        if member_opp and len(hl) > 1:          # a rematch on HLTV's schedule: the one at the book's start, when it says
            hl = _by_start(hl, getattr(args, "member_start", None), lambda m: m.get("start")) or hl
        if member_opp and not hl and getattr(args, "member_start", None):     # misspelled book opponent (see above)
            hl = _by_start(_hltv_schedule([n for n in teams.values() if n]), args.member_start, lambda m: m.get("start"))
        if chosen and hl and not want and not member_opp:
            chosen, note = _hltv_check_bo3(chosen, options, hl[0])
            if note:
                res["schedule_correction" if chosen is None else "schedule_heads_up"] = note
        if not chosen:
            pm, src = None, "Polymarket"
            if hl:
                # HLTV first: a local database read - instant, free, and the most accurate schedule
                m = hl[0]
                mine = next((n for n in teams.values() if n and _same_team(n, m["team"])), m["team"])
                pm, src = {"team": mine, "opponent": m["opponent"],
                           "start": m["start"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "title": f"{mine} vs {m['opponent']}{' (' + m['format'].upper() + ')' if m['format'] else ''}"
                                    f"{' - ' + m['event'] if m['event'] else ''}",
                           "bo": _bo_of(m.get("format")),     # HLTV's format ('bo3') -> 3: the odds selection's format
                           "hltv_match": (re.search(r"/matches/(\d+)", m.get("url") or "") or [None, None])[1]}, "HLTV"
            # BO3.gg doesn't have the match (smaller events, e.g. CIS LAN): use Polymarket's
            # pre-match listing - by his team's exact name, or by the opponent the member typed
            for tname in ([n for n in teams.values() if n] if not member_opp and not pm else []):
                o = match_odds(tname)
                if o.get("found") and norm(o["team"]) == norm(tname):
                    pm = o
                    break
            if member_opp and not pm:
                for tname in [n for n in teams.values() if n] + [None]:
                    o = match_odds(tname, member_opp)
                    if o.get("found"):
                        pm = o
                        break
            if not pm and not b.background:   # limited free quota: members' grades only, after Polymarket
                # PandaScore's free schedule covers events BO3.gg misses (e.g. CIS LAN Championship)
                try:
                    import pandascore
                    cand = []
                    for tname in [n for n in teams.values() if n]:
                        cand += [m for m in pandascore.upcoming(tname)
                                 if not member_opp or _same_team(member_opp, m["opponent"])]
                    if not cand and member_opp:
                        # none of his known teams plays them: the typed opponent's next match -
                        # the other side is his team
                        cand = [dict(m, team=m["opponent"], opponent=m["team"])
                                for m in pandascore.upcoming(member_opp)]
                    if cand:
                        m = min(cand, key=lambda m: m["start"] or "9999")
                        pm, src = {"team": m["team"], "opponent": m["opponent"], "start": m["start"], "bo": _bo_of(m.get("bo")),
                                   "title": f"{m['team']} vs {m['opponent']} (BO{m.get('bo') or '?'}) - {m['event']}"}, "PandaScore"
                except Exception:
                    pass
            if pm:
                pm = dict(pm, team=str(pm.get("team") or "").strip(), opponent=str(pm.get("opponent") or "").strip())
                # match format: None when the source has none; a Polymarket market names it in its question ('A vs B (BO5) -
                # ...') - 2026-10-13 audit: a Polymarket-found BO5 was graded as a BO3
                bo_ = pm.get("bo") or _bo_of((re.search(r"\(BO(\d)\)", str(pm.get("title") or ""), re.I) or [None, None])[1])
                nxt = {"start": pm.get("start"), "slug": None, "opponent": pm["opponent"],
                       "event": pm.get("title"), "maps": [], "source": src.lower(),
                       "opponent_id": None, "opponent_slug": None, "bo": bo_,
                       "hltv_match": pm.get("hltv_match")}   # logged + settled from HLTV (picklog)
                pb["playing_for"] = pm["team"]
                if no_team:
                    pb["current_team"] = pm["team"]
                elif not _same_team(pm["team"], pb.get("current_team")):
                    # a match for another team than his BO3.gg profile team: the same stand-in / roster rules as a BO3.gg
                    # match (2026-10-11 audit: Fajr was graded for Nordic Partners with LPH's roster, 'Roster: Confirmed'
                    # and no stand-in cap, because these rules ran only for BO3.gg matches)
                    tid_ = next((t for t, n in teams.items() if isinstance(t, int) and n and _same_team(n, pm["team"])), None)
                    team_change(tid_, pm["team"])
                res["match_note"] = res.get("schedule_correction") or (
                    f"Match not on BO3.gg - found on {src}. Maps unknown until the veto: all-maps numbers used.")
        if chosen:
            _, tid, tname, nxt = chosen
            pb["playing_for"], pb["playing_for_id"] = tname, tid
            if no_team:
                pb["current_team"], pb["team_id"] = tname, tid     # the team playing the named match
            elif tid != pb["team_id"]:
                slugs.setdefault(tid, nxt.get("team_slug"))
                team_change(tid, tname)
        pb["scout"] = nxt is None          # no upcoming match -> SCOUT card, no grade/units
        if nxt:
            if b.background:   # shadow feature: computed by the pre-loader, never on a member's clock
                try:
                    res["opponent_defense"] = opponent_defense(b, nxt.get("opponent_id"))
                except Exception:
                    res["opponent_defense"] = None
            res["next_match"] = nxt
            args.match_start = nxt.get("start")          # the performance model's day = the match day
            # match format (BO3 / BO5; HLTV / PandaScore matches too since 2026-10-13): the map-3 note, and the odds
            # selection's format (market_odds.select bo= - Thunderpick's series market is eligible per format)
            args.match_bo = nxt.get("bo")
            if nxt.get("event_slug") and not nxt.get("event_type"):
                nxt["event_type"] = _event_type(b, nxt["event_slug"])     # "lan" / "online" (BO3.gg)
            args.opponent = nxt["opponent"]
            if not args.maps and nxt["maps"]:
                args.maps = ",".join(nxt["maps"][:2])
                pb["per_map"] = [map_waterfall(b, pb["_matches"], pb["player"]["id"], _joined(pb), m)
                                 for m in nxt["maps"][:2]]
            elif not args.maps:
                # veto not done yet: likely Maps 1-2 - HLTV's real veto history first (Map 1 right
                # 30% / top-2 50% on 191 past BO3s vs 16% for BO3.gg's), then BO3.gg's
                try:
                    ep = event_pool(b, nxt)
                except Exception:
                    ep = None
                pk = ep["keys"] if ep else None
                pb["map_prediction"] = _hltv_map_prediction(pb.get("playing_for") or pb.get("current_team"),
                                                            nxt.get("opponent"), pk)
                if not pb["map_prediction"] and nxt.get("team1_id") and nxt.get("team2_id"):
                    try:
                        pb["map_prediction"] = predict_maps(b, nxt["team1_id"], nxt["team2_id"], pool_keys=pk)
                    except Exception:
                        pb["map_prediction"] = None
    if args.cmd == "h2h" or (args.cmd == "full" and args.opponent):
        if not args.opponent:
            raise SystemExit("h2h needs --opponent")
        nm = res.get("next_match") or {}
        opp_id, opp_slug = nm.get("opponent_id"), nm.get("opponent_slug")
        try:
            hits = [] if opp_id else b._run(b.cs2.search_teams(args.opponent, 5)).get("results") or []
            exact = [t for t in hits if _same_team(t.get("name"), args.opponent)
                     or norm(t.get("slug")) == norm(args.opponent)]
            pick = (exact or [{}])[0]          # never the search's first hit (2026-10-13 audit: 'MOUZ' could be MOUZ NXT)
            opp_id, opp_slug = opp_id or pick.get("id"), opp_slug or pick.get("slug")
        except Exception:
            pass
        stat = "headshots" if args.prop == "hs" else "kills"
        rows = h2h_rows(b, pb, args.opponent, opp_id)
        try:                                   # tonight's opponent roster, to keep only same-roster meetings
            res["_opp_roster"] = active_roster(b.team(opp_slug).get("players")) if opp_slug else None
        except Exception:
            res["_opp_roster"] = None
        # Gate C nationality tag + Phase 7 regional extension
        tag = nationality(b, opp_slug) if opp_slug else None
        res["opponent_nationality"] = tag
        # regional series are ALWAYS added (owner rule 2026-09-28), next to any exact H2H -
        # shown separately and weighted by their own tested weight
        if True:
            if not pb["roster_confirmed"]:
                res["regional_note"] = ("Regional extension disabled - roster not confirmed" if pb["roster_confirmed"] is False
                                        else "Regional extension disabled - his lineup is unknown")
            elif not tag or tag["id"] is None:
                res["regional_note"] = (f"Regional extension doesn't apply - opponent is "
                                        f"{_a_an((tag or {}).get('label', 'roster with no country data'))}")
            else:
                reg = regional_rows(b, pb, opp_id, tag)
                rows += reg
                st = pb.pop("_regional_stats", {}) or {}
                res["_regional_meta"] = {"label": tag["label"], "region": tag.get("region") or tag["label"], "kind": tag.get("kind"),
                                         "capped": st.get("capped"), "checked": st.get("teams_checked")}
                n_t, n_r = st.get("tag_series", 0), st.get("region_series", 0)
                region = tag.get("region") or tag["label"]
                ctx = " (context - not in the projection)"
                if tag.get("kind") == "region":
                    res["regional_note"] = (f"Regional extension: {n_r} series vs other {region} teams{ctx}" if n_r else
                                            f"Regional extension: no series vs other {region} teams in the last 12 months")
                elif n_r:
                    res["regional_note"] = (f"Regional extension: {n_t} series vs other {tag['label']} teams"
                                            f" + {n_r} vs other {region} teams{ctx}")
                else:
                    res["regional_note"] = (f"Regional extension: {n_t} series vs other {tag['label']} teams" if n_t else
                                            f"Regional extension: no series vs other {tag['label']} teams in the last 12 months")
                if st.get("capped"):
                    res["regional_note"] += f" - searched the latest {st.get('teams_checked')} opponents"
        res["h2h"] = h2h(rows, args.opponent, args.line, stat, res.get("_opp_roster"))
        res["_h2h_rows"], res["_h2h_opp"] = rows, args.opponent
    if args.cmd == "odds" or (args.cmd == "full" and (args.team or pb)):
        team = args.name if args.cmd == "odds" else (args.team or pb.get("playing_for") or pb.get("current_team"))
        if args.cmd == "full":
            # ONE odds policy per grade (market_odds.select, odds-source-2, 2026-10-13): Thunderpick first when its quote
            # is eligible, else the existing Polymarket rule, else OddsPapi (a live request only on a member's own context
            # build - network=False on the board / background: cache-only), else no market - the reasons are kept in
            # provenance. The match format goes with it (bo). The returned object is never mutated (finish_line re-checks
            # it at every regrade and swaps in a NEW object when the selection changed).
            import market_odds
            res["odds"] = market_odds.select(team, args.opponent, (res.get("next_match") or {}).get("start"),
                                             bo=getattr(args, "match_bo", None), network=not b.background)
        else:
            res["odds"] = match_odds(team, args.opponent)  # always live, no cache
    if args.cmd == "full" and pb and not b.background:
        # CS API (top teams): second-opinion roster, VRS rank, rating/swing context, staleness
        try:
            import csapi
            last = (pb.get("recent_maps") or [{}])[0].get("date")
            res["csapi"] = csapi.player_check_async(pb["player"]["nick"], pb.get("current_team"), last)
        except Exception:
            res["csapi"] = None
    if args.cmd == "full" and args.faceit_key:
        try:
            _faceit(args, res, pb, ttl)
        except Exception as e:          # FACEIT is display-only: it can never break a grade
            res["faceit_error"] = str(e)[:200]
    elif args.cmd == "faceit":
        _faceit(args, res, pb, ttl)
    if args.cmd == "full" and args.line is not None:
        finish_line(res, args)


def _hltv_since(pb, back_days=None):
    """The start date of his HLTV series read (the last-10 and H2H share one cached read)."""
    rows = pb.get("recent_maps") or []
    since = (min(r["date"] for r in rows) if rows else (date.today() - timedelta(days=120)).isoformat())
    if back_days:                                   # reach further back on HLTV (last 10 in one environment)
        since = min(since, (date.today() - timedelta(days=back_days)).isoformat())
    return (date.fromisoformat(since) - timedelta(days=1)).isoformat()


def _hltv_series(pb, back_days=None):
    try:
        import hltv
        return hltv.recent_series((pb.get("player") or {}).get("nick"), pb.get("current_team"), _hltv_since(pb, back_days))
    except Exception:
        return []


def _hltv_series_status(pb, back_days=None):
    """How the _hltv_series read was answered (hltv.read_status) and what that means for the grade - "source": read now from
    HLTV, HLTV's last good copy of the SAME query (memory / stored, with its age) during an outage, a good EMPTY answer (no
    HLTV series for him: BO3.gg rows, labelled) or a failure with no copy (state "failed": the grade is transient).
    None when HLTV is not configured / was not read."""
    try:
        import hltv
        nick = (pb.get("player") or {}).get("nick")
        st = hltv.read_status(("series", hltv._norm(nick), hltv._norm(pb.get("current_team")), _hltv_since(pb, back_days))) \
            if nick and hltv.dsn() else None
    except Exception:
        return None
    if not st:
        return None
    if st["state"] == "failed":
        st["source"] = "none - HLTV unreachable and no stored copy (transient)"
    else:
        st["source"] = {"live": "HLTV", "memory copy": "HLTV, last good copy (database unreachable)",
                        "stored copy": "HLTV, stored copy (database unreachable)"}[st["state"]] + \
            ("" if st.get("n") else ": no HLTV series for him - BO3.gg rows")
    return st


def _before_match(rows, start, now=None):
    """Forecast cutoff (2026-10-13 audit): only series strictly before the graded match - dated before its UTC day, or on
    that day and started before it (a BO3.gg row's start; an HLTV row has a date only: before a match that has not
    started yet). No start known: unchanged."""
    st = _iso(start) if start else None
    if st is None:
        return rows
    st = st if st.tzinfo else st.replace(tzinfo=timezone.utc)
    day, now = st.date().isoformat(), now or datetime.now(timezone.utc)
    by = {}
    for r in rows:
        by.setdefault(r["match_id"], []).append(r)
    keep = set()
    for mid, maps in by.items():
        d = min((m.get("date") or "")[:10] for m in maps)
        s = next((_iso(m["start"]) for m in maps if m.get("start")), None)
        if s is not None and s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        if d < day or (d == day and (s < st if s is not None else now < st)):
            keep.add(mid)
    return [r for r in rows if r["match_id"] in keep]


def _hltv_series_failed(pb, back_days=None):
    """The _hltv_series read failed (database unreachable, no earlier good answer) - its [] is not 'no HLTV series'."""
    try:
        import hltv
        nick = (pb.get("player") or {}).get("nick")
        return bool(nick) and hltv.lookup_failed(("series", hltv._norm(nick), hltv._norm(pb.get("current_team")),
                                                  _hltv_since(pb, back_days)))
    except Exception:
        return False


def _older_bo3(pb, rows, back_days):
    """rows + his BO3.gg Maps 1-2 rows from matches older than his last 10 (pb['_older_maps'], read with the player block)
    inside back_days."""
    have = {r["match_id"] for r in rows}
    cut = (date.today() - timedelta(days=back_days)).isoformat()
    return rows + [r for r in pb.get("_older_maps") or [] if r["match_id"] not in have and (r.get("date") or "") >= cut]


def _series_detail(maps):
    """A series' Maps 1-2 in order: ((map, rounds, won), (map, rounds, won)) - None unless both maps are fully known."""
    m = {r.get("number"): r for r in maps}
    out = []
    for n in (1, 2):
        r = m.get(n)
        if not r or not r.get("map") or not r.get("rounds") or r.get("won") is None:
            return None
        out.append((norm(str(r["map"]).removeprefix("de_")), r["rounds"], bool(r["won"])))
    return tuple(out)


def _days(maps):
    """The UTC days a series is dated on: its map 1 day and (BO3.gg) its scheduled start's day."""
    return {d for d in ((maps[0].get("date") or "")[:10], (maps[0].get("start") or "")[:10]) if d}


def _same_series(h, x):
    """True only when an HLTV series and a BO3.gg series (their map rows) are provably one series. 2026-10-13 audit: a loose
    name match counted 'FaZe' / 'FaZe Clan' twice and merged a team's two meetings with one opponent in a day - and names
    or a scoreline alone prove nothing. Required, all of: BOTH teams the same by canonical identity (HLTV team ids /
    verified aliases, else exact name variants - _same_team, never fuzzy), the same day (HLTV dates a series, it has no
    start time: its date is the BO3.gg series' map 1 day or scheduled day) and the same Maps 1-2 in order (map, rounds,
    result)."""
    hd = (h[0].get("date") or "")[:10]
    if not hd or hd not in _days(x):
        return False
    dh = _series_detail(h)
    if dh is None or dh != _series_detail(x):
        return False
    return _same_team(h[0].get("team"), x[0].get("team")) and _same_team(h[0].get("opponent"), x[0].get("opponent"))


def _clean_repeats(rows, quarantine):
    """BO3.gg map rows without repeated-map records (2026-10-14 audit: 2 BO3.gg series listed one map twice and stayed
    duplicates). The same provider game listed twice (same game id) is one row; a series that lists one map name for two
    different games is QUARANTINED - a map can't be played twice in a series, and which record is the real one isn't
    knowable - its rows are left out (quarantine[match id] = why; HLTV's record of that series, if any, counts instead).
    Rows without a game id are compared by their complete record (number, map, rounds, result, kills, headshots, deaths)."""
    seen, by = set(), {}
    for r in rows:
        k = (r.get("match_id"), r.get("game_id") if r.get("game_id") is not None else
             (r.get("number"), r.get("map"), r.get("rounds"), r.get("won"), r.get("kills"), r.get("headshots"), r.get("deaths")))
        if k in seen:
            continue
        seen.add(k)
        by.setdefault(r.get("match_id"), []).append(r)
    out = []
    for mid, maps in by.items():
        names = [norm(str(m["map"]).removeprefix("de_")) for m in maps if m.get("map")]
        if len(names) != len(set(names)):
            quarantine[mid] = "BO3.gg lists one map twice with different records"
            continue
        out += maps
    return out


def _map_kills(maps):
    m = {r.get("number"): r.get("kills") for r in maps}
    k = (m.get(1), m.get(2))
    return k if None not in k else None


def _twins(hl_by, rows_by, quarantine=None):
    """{BO3.gg match id: HLTV match id} for the series both sources provably have ({match id: map rows} each; _same_series).
    Ambiguous = not merged: look-alike series (rematches with identical scores) are paired only when every HLTV series of
    the group matches every BO3.gg series of it and the counts agree - any pairing then gives the same series and numbers.
    Otherwise (2026-10-14 audit) the complete map records decide: a BO3.gg series pairs with the HLTV series whose Maps 1-2
    kills are identical when that is unique both ways; the group's other BO3.gg look-alikes are QUARANTINED
    (quarantine[match id] = why) - never counted next to the HLTV series they may duplicate, never guessed."""
    by_day = {}
    for xm, xr in rows_by.items():
        for d in _days(xr):
            by_day.setdefault(d, set()).add(xm)
    edges, hs_of = {}, {}
    for hm, hr in hl_by.items():
        for xm in by_day.get((hr[0].get("date") or "")[:10], ()):
            if _same_series(hr, rows_by[xm]):
                edges.setdefault(hm, set()).add(xm)
                hs_of.setdefault(xm, set()).add(hm)
    out, done = {}, set()
    for hm in edges:
        if hm in done:
            continue
        hs, xs, todo = set(), set(), [hm]
        while todo:                                      # the group of series connected to this one
            a = todo.pop()
            if a in hs:
                continue
            hs.add(a)
            for xm in edges[a]:
                xs.add(xm)
                todo += [b for b in hs_of[xm] if b not in hs]
        done |= hs
        if len(hs) == len(xs) and all(edges[a] == xs for a in hs):
            out.update(zip(sorted(xs, key=str), sorted(hs, key=str)))
            continue
        kp = [(a, x) for a in hs for x in edges[a] if _map_kills(hl_by[a]) is not None
              and _map_kills(hl_by[a]) == _map_kills(rows_by[x])]
        for a, x in kp:
            if sum(1 for b, _ in kp if b == a) == 1 and sum(1 for _, y in kp if y == x) == 1:
                out[x] = a
        if quarantine is not None:
            # a duplicate needs an HLTV series to duplicate: a leftover whose every HLTV look-alike is already paired (on its
            # complete record) with another BO3.gg series is a different provider series - a genuine rematch HLTV lacks - and
            # stays; one that may still be an unpaired HLTV series' twin is quarantined (2026-10-14 harness finding)
            paired = {out[y] for y in xs if y in out}
            for x in xs - set(out):
                if any(a not in paired for a in hs_of.get(x, ())):
                    quarantine[x] = "look-alike of an HLTV series (same day, teams and Maps 1-2) - not provably the same or another series"
    return out


def _hltv_h2h(pb, rows, opponent):
    """Head-to-head / regional rows with HLTV's numbers wherever HLTV has the series (the last-10 does the same - one
    series, one total on the card), plus meetings with tonight's opponent only HLTV has (exact name variants, last
    H2H_DAYS). BO3.gg's lineup / regional fields stay (the same-roster test reads them). 2026-10-11 audit."""
    q = {}
    rows = _clean_repeats(rows, q)
    hl = _hltv_series(pb, 365)
    if not hl:
        if q:
            pb.setdefault("quarantined_series", {}).update(q)
        return rows
    by_match = {}
    for r in hl:
        by_match.setdefault(r["match_id"], []).append(r)
    out, used = [], set()
    groups = {}
    for r in rows:
        groups.setdefault(r["match_id"], []).append(r)
    pair = _twins(by_match, groups, q)
    if q:
        pb.setdefault("quarantined_series", {}).update(q)
    for mid, maps in groups.items():
        if mid in q:
            continue                                     # quarantined: HLTV's record of the series (if any) counts instead
        twin = pair.get(mid)
        if twin is None:
            out += maps
            continue
        used.add(twin)
        hnum = {h["number"]: h for h in by_match[twin]}
        for r in maps:
            h = hnum.get(r.get("number"))
            if h is None:
                out.append(r)
                continue
            out.append(dict(r, map=h["map"], won=h["won"], rounds=h["rounds"], source="hltv", hltv_match=h.get("hltv_match"),
                            kills=h["kills"] if h["kills"] is not None else r.get("kills"),
                            headshots=h["headshots"] if h["headshots"] is not None else r.get("headshots")))
    if opponent:
        import hltv
        cut = (date.today() - timedelta(days=H2H_DAYS)).isoformat()
        for m, hr in by_match.items():
            if m in used or hr[0]["date"] < cut or not hltv.same_team(opponent, hr[0]["opponent"]):
                continue
            if any(x["match_id"] == m for x in out):
                continue
            out += [dict(h, regional=False) for h in hr]                     # a meeting only HLTV has
    return out


def _form_rows(pb, stat="kills", keep_n=RECENT_MATCHES, back_days=None):
    """His recent Maps 1-2 rows, newest first, RECENT_MATCHES series in total: BO3.gg's, with HLTV's
    numbers swapped in for every match HLTV also has (HLTV is the official record - BO3.gg's quick
    scoreboards and map order are sometimes off), plus series only HLTV has (events BO3.gg doesn't
    cover, e.g. CIS LANs). Kept out of pb["recent_maps"]: team / stand-in checks use BO3.gg's ids."""
    rows = list(pb.get("recent_maps") or [])
    hl = _hltv_series(pb, back_days)
    if back_days and len({r["match_id"] for r in _usable_series(rows, stat, 2)}) < keep_n:
        # BO3.gg's last 10 matches include BO1s and other-setting series: reach back into his older BO3.gg matches
        # (already loaded for H2H) for the same window as HLTV (2026-10-11 audit: xureba showed 7 of 10)
        rows = _older_bo3(pb, rows, back_days)
    q = {}
    rows = _clean_repeats(rows, q)                       # repeated-map records out (2026-10-14 audit)
    if not hl:
        if q:
            pb.setdefault("quarantined_series", {}).update(q)
        return rows
    by_match, rows_by = {}, {}
    for r in hl:
        by_match.setdefault(r["match_id"], []).append(r)
    for r in rows:
        rows_by.setdefault(r["match_id"], []).append(r)
    twin_of = {h: x for x, h in _twins(by_match, rows_by, q).items()}   # one BO3.gg series per HLTV series
    if q:
        pb.setdefault("quarantined_series", {}).update(q)
        rows = [x for x in rows if x["match_id"] not in q]   # ambiguous look-alikes: HLTV's series counts, never twice
    extra = []
    for mid, hrows in by_match.items():
        usable = all(r[stat] is not None for r in hrows)
        if mid in twin_of:
            if usable:                                   # HLTV's numbers replace BO3.gg's for this match
                rows = [x for x in rows if x["match_id"] != twin_of[mid]]
                extra += hrows
        elif usable or stat == "kills":
            extra += hrows                               # a match only HLTV has
    if not extra:
        return rows
    newest = max(r["date"] for r in extra)
    if not rows or newest > max(r["date"] for r in rows):
        pb["days_since_last_map"] = (date.today() - date.fromisoformat(newest)).days
    merged = sorted(extra + rows, key=lambda r: (r["date"], str(r["match_id"])), reverse=True)
    keep = []
    for r in merged:
        if r["match_id"] not in keep:
            keep.append(r["match_id"])
    keep = set(keep[:keep_n])
    return [r for r in merged if r["match_id"] in keep]


def _bo3_rank_band(pb, band=10):
    """His BO3.gg maps of the last 12 months vs teams ranked (HLTV VRS) within `band` of tonight's
    opponent - BO3.gg reaches back further than the HLTV database."""
    orank, rows = pb.get("_opp_rank"), pb.get("_opp_rows") or []
    tonight = (pb.get("_env") or {}).get("stats_from")
    if tonight:
        er = [x for x in rows if len(x) > 3 and x[3] == tonight]
        rows = er if len(er) >= 10 else rows
    rows = [x[:3] for x in rows]
    if not orank or not rows:
        return None
    try:
        import hltv
        ranks = hltv.team_ranks()
    except Exception:
        return None
    if not ranks:
        return None
    cache = {}

    def rank_of(name):
        if name not in cache:
            # exact name variants only (2026-10-13 audit: by substring Spirit Academy / MOUZ NXT / 'Dogs Esports' took the
            # main team's or OG's rank)
            cache[name] = next((ranks[v] for v in hltv.team_name_variants(name) if v in ranks), None)
        return cache[name]
    k_all = sum(k for _, k, r in rows)
    r_all = sum(r for _, k, r in rows)
    sim = [(o, k, r) for o, k, r in rows if rank_of(o) is not None and abs(rank_of(o) - orank) <= band]
    r_b = sum(r for _, k, r in sim)
    if not r_all or not r_b:
        return {"opp_rank": orank, "band": band, "maps": 0, "source": "BO3.gg 12 months"}
    kpr, kpr_all = sum(k for _, k, r in sim) / r_b, k_all / r_all
    return {"opp_rank": orank, "band": band, "maps": len(sim), "kpr": round(kpr, 3), "kpr_all": round(kpr_all, 3),
            "ratio": round(kpr / kpr_all, 3), "teams": sorted({o for o, _, _ in sim})[:8], "source": "BO3.gg 12 months"}


def _best_rank_band(pb, hltv_band):
    bo3_band = _bo3_rank_band(pb)
    cands = [x for x in (bo3_band, hltv_band) if x]
    return max(cands, key=lambda x: x.get("maps") or 0) if cands else None


def _hltv_only_overall(pb):
    """BO3.gg has no maps for him (teams it doesn't cover, e.g. ShindeN): his rates come from his
    HLTV Maps 1-2 of the last 10 series instead, so the line still gets a real grade and a side."""
    o = pb.get("overall") or {}
    if o.get("maps_played") or pb.get("_hltv_overall_tried"):
        return
    rows = _form_rows(pb, "kills")
    # tried = read: a failed HLTV read (a blip, no good copy) is tried again on the next grade of the cached context
    # (2026-10-13 audit: marked tried before the read, one timeout left the line with no KPR for 10-30 minutes)
    pb["_hltv_overall_tried"] = not _hltv_series_failed(pb)
    by = {}
    for r in rows:
        by.setdefault(r["match_id"], []).append(r)
    m12 = [m for ms in by.values() if len(ms) >= 2 for m in sorted(ms, key=lambda m: m["number"] or 9)[:2]]
    m12 = [m for m in m12 if m.get("rounds") and m.get("kills") is not None]
    if not m12:
        return
    k, r = sum(m["kills"] for m in m12), sum(m["rounds"] for m in m12)
    hs_rows = [m for m in m12 if m.get("headshots") is not None]
    hs_k = sum(m["kills"] for m in hs_rows)
    hs_pct = round(100 * sum(m["headshots"] for m in hs_rows) / hs_k, 1) if hs_k else None
    o.update(maps_played=len(m12), kpr=round(k / r, 3), source="HLTV (BO3.gg has no maps for him)")
    if hs_pct is not None:
        o["hs_pct"] = hs_pct
    fw = o.setdefault("form_windows", {})
    fw["3m"] = {"maps": len(m12), "kpr": round(k / r, 3), "hs_pct": hs_pct}
    pb["overall"] = o
    pb["_rf_kpr"] = round(k / r, 3)


def _hltv_staleness(g, pf, pb):
    """HLTV projection whose history ends well before his BO3.gg matches (2026-10-11 audit: damyo / foggers got A- Unders
    from HLTV maps for their old teams that end months / weeks before 10 recent BO3.gg series, and nothing said so).
    Context only - the projection and the stake are unchanged (switching the source is the owner's call and needs a
    chronological test); the grade gets a member-visible flag, the board a 'verify' data status and the source label
    the date HLTV's history ends."""
    if not isinstance(pf, dict) or pf.get("error") or str(pf.get("version") or "").endswith("-bo3"):
        return
    nd = pf.get("newest_map_day")
    if not nd or not str(g.get("projection_source") or "").startswith("perf"):
        return
    import hltv
    ends = date.fromordinal(int(nd))
    newer = sorted({r.get("match_id") or r.get("date") for r in pb.get("recent_maps") or []
                    if r.get("date") and r["date"][:10] > ends.isoformat()})
    last = max((r["date"][:10] for r in pb.get("recent_maps") or [] if r.get("date")), default=None)
    if not last or len(newer) < 2 or (date.fromisoformat(last) - ends).days <= hltv.STALE_DAYS:
        return
    team = (pb.get("hltv") or {}).get("team")
    tail = f" (for {team})" if team and not _same_team(team, pb.get("playing_for") or pb.get("current_team")) else ""
    g["hltv_ends"] = ends.isoformat()
    g.setdefault("flags", []).append(
        f"HLTV STALE: HLTV history ends {ends.isoformat()}{tail} - BO3.gg has {len(newer)} newer matches (to {last}) "
        f"that the projection doesn't include - extra caution")


def _ladder(res, pb, args):
    """The same grade at nearby lines (exact: the full grader at each line, his record re-counted)."""
    gi = res.get("grader_inputs") or {}
    if "p_more" not in (res.get("grade") or {}):
        return None
    totals = [s["total"] for s in ((pb.get("recent_form") or {}).get("series") or []) if s.get("total") is not None]
    out = []
    for step in (-2, -1, 0, 1, 2):
        ln = args.line + step
        if ln <= 0:
            continue
        d = dict(gi, line=ln)
        if d.get("recent") and totals:
            d["recent"] = {"n": sum(t != ln for t in totals), "overs": sum(t > ln for t in totals)}   # pushes out
        try:
            g = res["grade"] if step == 0 else grade_leg(d)
        except Exception:
            continue
        if "p_more" not in g:
            continue
        # each line through the member verdict (2026-10-11 audit: the chips printed the raw letter - 'Over 70.7% · A-' on a
        # 0-unit AWPer headshot pick the card calls Lean Over): gates, Lean, goblin/demon Under and the tie-break all match
        v = pick_verdict.verdict(g)
        out.append({"line": ln, "side": v["side"], "p": v.get("p"), "grade": v.get("grade"), "units": v.get("units") or 0.0,
                    "state": v.get("state")})
    return out


EVENT_TYPES_FILE = DATA_DIR / ".cache" / "event_types.json"


def _load_event_types():
    try:
        return json.loads(EVENT_TYPES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


EVENT_TYPES = _load_event_types()   # normalized BO3.gg tournament name -> "lan" / "online" (kept on the volume)


def _blend_windows(rows_by):
    """Same 3m / 6m / 12m blend as the overall numbers, from a subset of his maps (Maps 1-2)."""
    wins = {}
    for label, rs in rows_by.items():
        a = agg([r for r in rs if r["number"] <= 2])
        wins[label] = {"maps": a["maps_played"], "kpr": a["kpr"], "hs_pct": a["hs_pct"]}
    use = {k: v for k, v in wins.items() if v["maps"] and v["kpr"]}
    if not use:
        return {"form_windows": wins, "kpr": None, "hs_pct": None, "maps": 0}
    s_ = sum(FORM_BLEND[k][1] for k in use)
    hs_use = {k: v for k, v in use.items() if v["hs_pct"] is not None}
    return {"form_windows": wins, "maps": wins.get("12m", {}).get("maps") or 0,
            "kpr": round(sum(FORM_BLEND[k][1] * v["kpr"] for k, v in use.items()) / s_, 3),
            "hs_pct": round(sum(FORM_BLEND[k][1] * v["hs_pct"] for k, v in hs_use.items())
                            / sum(FORM_BLEND[k][1] for k in hs_use), 1) if hs_use else None}


ENV_FULL_MAPS = 30   # LAN / online stats used alone from this many Maps 1-2 in that environment (12 months)


def match_envs(b, matches):
    """{BO3.gg match id: "lan" / "online"} from each match's tournament type (looked up once per event)."""
    out, new = {}, False
    for mt in matches:
        slug = (mt.get("tournament") or {}).get("slug")
        if not slug:
            continue
        key = "slug:" + slug
        if key not in EVENT_TYPES:
            et = _event_type(b, slug)
            if et is None:                            # lookup failed: asked again next time (2026-10-11 audit)
                continue
            EVENT_TYPES[key] = et
            new = True
        if EVENT_TYPES[key]:
            out[mt["id"]] = EVENT_TYPES[key]
    if new:
        try:
            EVENT_TYPES_FILE.parent.mkdir(parents=True, exist_ok=True)
            EVENT_TYPES_FILE.write_text(json.dumps(EVENT_TYPES), encoding="utf-8")
        except Exception:
            pass
    return out


def _apply_env(pb):
    """Owner 2026-10-07: a LAN match is graded on his LAN stats, an online match on his online stats
    (H2H excepted). Alone from ENV_FULL_MAPS maps there; with fewer, blended toward all his maps by
    sample so 3 LAN maps can't swing a grade."""
    env = pb.get("_env") or {}
    tonight = env.get("tonight")
    oe = (pb.get("overall_env") or {}).get(tonight) if tonight else None
    o = pb.get("overall") or {}
    if "kpr_all_envs" in o:
        # a reused context holds the blended numbers already: blend again from his all-environment ones (2026-10-13 audit:
        # every cached grade re-blended the blend - KPR 0.80 -> 0.85 - and kpr_all_envs became the blended value)
        o["kpr"], o["hs_pct"] = o.pop("kpr_all_envs"), o.pop("hs_all_envs", o.get("hs_pct"))
        if "form_windows_all_envs" in o:
            o["form_windows"] = o.pop("form_windows_all_envs")
            o.pop("form_windows_env", None)
        pb["_rf_kpr"] = o["kpr"]
    if not oe or not oe.get("kpr") or not o.get("kpr"):
        return None
    w = min(1.0, (oe.get("maps") or 0) / ENV_FULL_MAPS)
    if w <= 0:
        return None
    o["kpr_all_envs"], o["hs_all_envs"] = o["kpr"], o.get("hs_pct")
    o["kpr"] = round(w * oe["kpr"] + (1 - w) * o["kpr"], 3)
    if oe.get("hs_pct") is not None and o.get("hs_pct") is not None:
        o["hs_pct"] = round(w * oe["hs_pct"] + (1 - w) * o["hs_pct"], 1)
    if w >= 1:
        o["form_windows_all_envs"] = o.get("form_windows")
        o["form_windows"] = oe["form_windows"]
        o["form_windows_env"] = tonight            # the stats panel says so ('LAN maps only') - display only
    pb["_rf_kpr"] = o["kpr"]
    env.update(stats_from=tonight, stats_maps=oe.get("maps"), stats_weight=round(w, 2), ratio=None)
    return tonight


def _usable_series(rows, stat, need):
    """Rows of series that count for a record: `need` maps played and the stat recorded on them."""
    by = {}
    for r in rows:
        by.setdefault(r["match_id"], []).append(r)
    ok = set()
    for mid, maps in by.items():
        maps = sorted(maps, key=lambda m: m["number"] or 9)
        if len(maps) >= need and all(m.get(stat) is not None for m in maps[:need]):
            ok.add(mid)
    return [r for r in rows if r["match_id"] in ok]


def _first_series(rows, n):
    """The rows of the n newest series (rows newest first)."""
    keep = []
    for r in rows:
        if r["match_id"] not in keep:
            keep.append(r["match_id"])
    keep = set(keep[:n])
    return [r for r in rows if r["match_id"] in keep]


def _env_rows(rows, pb, tonight):
    """His rows from tonight's environment (BO3.gg rows by tournament type, HLTV rows by its flag)."""
    me = pb.get("_match_env") or {}
    return [r for r in rows if (r.get("env") or me.get(r.get("match_id"))) == tonight]


def _label_env(rf, deep, listed, pb, env, stat, need):
    """Display only (never changes which series are used): which environment the last-10 list is from (env = tonight's
    LAN / online when it was filtered to it, else None) and the newest series of the OTHER environment it left out -
    those newer than the oldest listed series ("env_skipped", up to 3, newest first, Maps 1-2 totals of the same stat;
    "env_skipped_n" = how many in all)."""
    rf["env"], rf["env_skipped"], rf["env_skipped_n"] = env, [], 0
    if not env or not listed:
        return rf
    ok = _usable_series(deep, stat, need)
    keep = {r["match_id"] for r in _env_rows(ok, pb, env)}
    oldest = min(r["date"] for r in listed)
    other = [r for r in ok if r["match_id"] not in keep and r["date"] > oldest]
    me = pb.get("_match_env") or {}
    by = {}
    for r in other:
        by.setdefault(r["match_id"], []).append(r)
    skipped = []
    for mid, maps in by.items():
        maps = sorted(maps, key=lambda m: m["number"] or 9)[:need]
        skipped.append({"date": maps[0]["date"], "opponent": maps[0]["opponent"], "total": sum(m[stat] for m in maps),
                        "env": maps[0].get("env") or me.get(mid)})
    skipped.sort(key=lambda x: x["date"], reverse=True)
    rf["env_skipped"], rf["env_skipped_n"] = skipped[:3], len(skipped)
    return rf


def months12_avg(pb, stat="kills", today=None):
    """His Maps 1-2 average (same stat) over every official series of the last 12 months with both maps and the stat
    recorded: (average, series). Plain average - display only."""
    rows = _form_rows(pb, stat, keep_n=10 ** 6, back_days=365)   # the same HLTV read as the last-10 (cached)
    since = ((today or date.today()) - timedelta(days=365)).isoformat()
    rows = _usable_series([r for r in rows if (r.get("date") or "") >= since], stat, 2)
    by = {}
    for r in rows:
        by.setdefault(r["match_id"], []).append(r)
    tots = [sum(m[stat] for m in sorted(ms, key=lambda m: m["number"] or 9)[:2]) for ms in by.values()]
    return (round(sum(tots) / len(tots), 1) if tots else None), len(tots)


def _form_gap(res, pb, args, stat):
    """Plain numbers behind "Projection differs from his last-10 average" (Maps 1-2 lines): his last-10 average and the
    environment it is from, the projection, his 12-month Maps 1-2 average, his rate vs teams ranked like tonight's
    opponent (context, as shown in the HLTV panel) and the opponent. Inputs and outputs only - no weights."""
    g = res.get("grade") or {}
    if (getattr(args, "scope", "12") or "12") != "12" or g.get("lambda_final") is None:
        return None
    rf = pb.get("recent_form") or {}
    m12, n12 = months12_avg(pb, stat) if pb.get("recent_maps") is not None else (None, 0)
    rb = pb.get("_rank_band") or {}
    return {"last10_avg": rf.get("avg_total"), "last10_n": rf.get("matches"), "last10_env": rf.get("env"),
            "projection": round(g["lambda_final"], 1),
            "gap": round(g["lambda_final"] - rf["avg_total"], 1) if rf.get("avg_total") is not None else None,
            "months12_avg": m12, "months12_n": n12, "stat": stat,
            "rank_band": ({"opp_rank": rb.get("opp_rank"), "kpr_ratio": rb.get("ratio"), "maps": rb.get("maps")}
                          if rb.get("opp_rank") is not None else None),
            "opponent": (res.get("next_match") or {}).get("opponent") or getattr(args, "opponent", None)}


def _event_type(b, slug):
    """BO3.gg's tournament type: "lan" / "online" / None (cached with the API answers)."""
    try:
        t = b._run(b.cs2._api._make_request("/tournaments/" + slug, {}))
        et = (t.get("event_type") or "").lower()
        if et in ("lan", "online") and t.get("name"):
            if EVENT_TYPES.get(norm(t["name"])) != et:   # learned: matches listed elsewhere ("... - Roman Imperium Cup Group B")
                EVENT_TYPES[norm(t["name"])] = et
                try:
                    EVENT_TYPES_FILE.parent.mkdir(parents=True, exist_ok=True)
                    EVENT_TYPES_FILE.write_text(json.dumps(EVENT_TYPES), encoding="utf-8")
                except Exception:
                    pass
        return et if et in ("lan", "online") else ""    # "" = BO3.gg answered without a type
    except Exception:
        return None                                   # unknown because the lookup failed - never remembered as 'no type'


def _environment(res, pb):
    """Tonight LAN or online: HLTV first (matches already played at the event / event name), then
    BO3.gg's tournament type. With his LAN / online split, the ratio for tonight's environment."""
    env = dict((res.get("hltv_context") or {}).get("environment") or {})
    nm = res.get("next_match") or {}
    et = nm.get("event_type")
    if not et and nm.get("event"):              # HLTV's events list first (title contains the event name)
        try:
            import hltv
            et = hltv.event_env_from_title(nm["event"])
        except Exception:
            et = None
    if not et and nm.get("event"):              # listed by HLTV / PandaScore: a known event inside the title
        title = norm(nm["event"])
        core = lambda k: re.sub(r"(season)?([0-9]+|[ivx]+)$", "", k)     # "romanimperiumcupix" -> "romanimperiumcup"
        et = next((v for k, v in sorted(EVENT_TYPES.items(), key=lambda kv: -len(kv[0]))
                   if len(core(k)) >= 8 and core(k) in title), None)
    if not env.get("tonight") and et:
        env["tonight"], env["source"] = et, "BO3.gg event type"
        side = env.get(et)
        tot = [v for k, v in env.items() if k in ("lan", "online") and isinstance(v, dict)]
        maps = sum(v["maps"] for v in tot)
        if side and maps:
            allk = sum(v["kpr"] * v["maps"] for v in tot) / maps
            env["ratio"], env["ratio_maps"] = round(side["kpr"] / allk, 3), side["maps"]
    return env or None


def regional_note_text(meta, series):
    """The card's regional note, counted from the series the card actually lists (after the LAN / online filter)."""
    n_t = sum(1 for x in series if x.get("regional") and x.get("regional_level") != "region")
    n_r = sum(1 for x in series if x.get("regional_level") == "region")
    ctx = " (context - not in the projection)"
    if meta.get("kind") == "region":
        note = (f"Regional extension: {n_r} series vs other {meta['region']} teams{ctx}" if n_r else
                f"Regional extension: no series vs other {meta['region']} teams in the last 12 months")
    elif n_r:
        note = f"Regional extension: {n_t} series vs other {meta['label']} teams + {n_r} vs other {meta['region']} teams{ctx}"
    else:
        note = (f"Regional extension: {n_t} series vs other {meta['label']} teams" if n_t else
                f"Regional extension: no series vs other {meta['label']} teams in the last 12 months")
    if meta.get("capped"):
        note += f" - searched the latest {meta.get('checked')} opponents"
    return note


def _regional_note(res):
    meta = res.get("_regional_meta")
    if meta and isinstance(res.get("h2h"), dict):
        res["regional_note"] = regional_note_text(meta, res["h2h"].get("series") or [])


def _revalidated_odds(od):
    """The context's odds re-checked NOW (market_odds.recheck, 2026-10-13) -> (odds, check): a shared context is reused up
    to 30 minutes but a Thunderpick quote is eligible for 180 s, so the freshness / empty-answer / started checks run at
    every regrade, not only when the context was built. No network here (board, background and members alike: Polymarket
    on the list in memory, OddsPapi cache-only). Unchanged -> the same object; changed -> a NEW object (the cached one is
    never mutated); a failure while re-checking keeps the context's odds. check: when the re-check ran, what it found and
    the selected quote's latest age (a re-confirmed quote keeps its own retrieval time - this records the confirmation);
    None when the odds were not made by the selector."""
    if not od or not isinstance((od.get("provenance") or {}).get("query"), dict):
        return od, None                              # not made by market_odds.select (CLI 'odds' command / old context)
    try:
        import market_odds
        return market_odds.recheck(od, network=False, quality=market_quality)
    except Exception as e:
        return od, {"checked_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                    "result": f"failed ({type(e).__name__}): the context's odds kept"}


def finish_line(res, args):
    """The only part of a grade that depends on the line / prop: last-10 vs the line, H2H hit
    rates, grader inputs, the grade, the simulation. Everything else is shared context."""
    pb = res.get("player_block") or {}
    stat = "headshots" if args.prop == "hs" else "kills"
    t0 = time.time()
    _attach_hltv(pb)
    res["hltv"] = pb.get("hltv")
    _tick(res, "hltv_player", t0)
    if not getattr(args, "opponent", None):            # reused context: the opponent comes from the match
        args.opponent = (res.get("next_match") or {}).get("opponent")
    # reused context (2026-10-11 fix): the match day and format come from the cached match too - a cache hit used to
    # grade with today's date (not the match day the model is tested with) and no match format
    nm_ = res.get("next_match") or {}
    if not getattr(args, "match_start", None) and nm_.get("start"):
        args.match_start = nm_["start"]
    if not getattr(args, "match_bo", None) and nm_.get("bo"):
        args.match_bo = nm_["bo"]
    if nm_.get("opponent"):                            # the match's own opponent name on every path: a cache hit used to keep
        args.opponent = nm_["opponent"]                # the book's string, so the HLTV opponent lookup could differ (2026-10-11 audit)
    res["hltv_context"] = _hltv_context(pb, args)
    if (res["hltv_context"] or {}).get("transient"):   # HLTV blip while reading the context: one retry (if this caller may probe)
        import hltv
        if hltv.allow_probe():
            time.sleep(PERF_RETRY_WAIT)
            res["hltv_context"] = _hltv_context(pb, args, fresh=True)
    _tick(res, "hltv_context", t0)
    pb["_opp_rank"] = ((res["hltv_context"] or {}).get("opp_ranking") or {}).get("rank")
    pb["_env"] = _environment(res, pb)       # LAN / online split + tonight's environment
    # tonight's LAN / online flag feeds the projection: unknown because the database was unreachable (not because no one
    # labels the event) makes the grade transient - never a silently different perf-2 number (2026-10-11 audit)
    ctx_ = res["hltv_context"] or {}
    pb["_env_transient"] = bool(ctx_.get("transient") and not ctx_.get("environment"))   # HLTV's own label missing because of the blip
    _apply_env(pb)                           # LAN match -> LAN stats, online match -> online stats
    # the last 10 and the regional series follow tonight's environment whenever it is known - also when his BO3.gg maps have
    # no LAN / online split to blend (2026-10-13 audit: an HLTV-only player's last 10 was never from tonight's environment)
    tonight = (pb.get("_env") or {}).get("tonight")
    if pb.get("recent_maps") is not None:
        _hltv_only_overall(pb)
        # every series of the last 12 months (the same cached reads) strictly before the graded match: tonight's environment
        # is picked from all of them, then cut to 10 (2026-10-13 audit: only the newest 40 were kept - 45 online series hid
        # his 6 LAN series 5-8 months back)
        deep = _before_match(_form_rows(pb, stat, keep_n=10 ** 6, back_days=365), getattr(args, "match_start", None))

        def last10(need):
            # his last 10 USABLE series (Maps 1-2 both played and the stat recorded; need=1: Map 1), in
            # tonight's environment when he has 5+ there - from deeper HLTV history, not only what is inside his
            # last 10 overall (that left 5 - f1ku 2026-10-07; BO1s / unread headshots left 9 - FL1T)
            ok = _usable_series(deep, stat, need)
            rows = _first_series(ok, RECENT_MATCHES)
            if tonight:
                er = _first_series(_env_rows(ok, pb, tonight), RECENT_MATCHES)
                if len({r["match_id"] for r in er}) >= 5:
                    return er, True
            return rows, False
        rows, env_form = last10(2)
        if env_form:
            pb["_env"]["form_from"] = tonight
        pb["recent_form"] = recent_form(rows, pb.get("_rf_kpr"), args.line, stat)   # Maps 1-2: feeds the grade
        _label_env(pb["recent_form"], deep, rows, pb, tonight if env_form else None, stat, 2)
        if getattr(args, "scope", "12") == "1":          # display only: every match's Map 1, BO1s included
            rows1, env1 = last10(1)
            pb["recent_form_map1"] = recent_form(rows1, pb.get("_rf_kpr"), args.line, stat, need=1)
            _label_env(pb["recent_form_map1"], deep, rows1, pb, tonight if env1 else None, stat, 1)
    _tick(res, "form", t0)
    if res.get("_h2h_rows") is not None:
        hrows = list(res["_h2h_rows"])
        if tonight:   # H2H keeps every meeting; the same-region series only count from tonight's environment
            hrows = [r for r in hrows if not r.get("regional") or r in _env_rows([r], pb, tonight)]
        try:
            hrows = _hltv_h2h(pb, hrows, res.get("_h2h_opp"))
        except Exception:
            pass
        res["h2h"] = h2h(hrows, res.get("_h2h_opp"), args.line, stat, res.get("_opp_roster"))
        _regional_note(res)
    _tick(res, "h2h", t0)
    pb["hltv_series"] = _hltv_series_status(pb, 365)   # source + freshness of the last-10 / H2H HLTV read (failed = transient)
    pb["_rank_band"] = _best_rank_band(pb, (res["hltv_context"] or {}).get("rank_band"))
    _tick(res, "rank_band", t0)
    res["odds"], res["odds_check"] = _revalidated_odds(res.get("odds"))
    # CLI --team-ml / --opp-ml (never web grades): the typed prices ARE the market input, so the summary prints what the
    # grade used (2026-10-13 review: it printed the selected source's price next to a grade made from the typed ones)
    res["odds"] = _cli_odds(args, res["odds"])
    _tick(res, "odds_check", t0)
    res["grader_inputs"] = grader_inputs(pb, args, res.get("odds"), res.get("h2h"))
    # the re-check itself, saved with the forecast (the inputs blob) - when the market input was last confirmed and what
    # the re-check found; the grader never reads it
    res["grader_inputs"]["odds_check"] = copy.deepcopy(res["odds_check"])
    res["grade"] = grade_leg(res["grader_inputs"])
    od_ = res.get("odds") or {}
    if od_.get("transient") is True:
        # Thunderpick has the match but no current quote AND no eligible fallback price (E1 narrowed by E2, owner
        # 2026-10-15: a found fallback grades normally - market_odds) - the board keeps its last grade, the card says so;
        # never set on any other grade. odds_transient_source names what was used (none here), never Thunderpick
        res["grade"]["odds_transient"] = True
        res["grade"]["odds_transient_reason"] = (od_.get("provenance") or {}).get("fallback_reason")
        used = (od_.get("provenance") or {}).get("selected") if od_.get("found") else None
        res["grade"]["odds_transient_source"] = None if str(used or "").lower().startswith("thunderpick") else used
    # 2026-10-14 audit: the grader reads MIXED input revisions - each component is labelled (never "the old history" for
    # the whole grade): only the performance model is pinned to N0 (perf_live); records / stake checks read the full tables
    pf_ = res["grader_inputs"].get("perf") if isinstance(res["grader_inputs"].get("perf"), dict) else {}
    v_ = str(pf_.get("version") or "")
    try:
        import perf_live
        n0_rev = perf_live.input_revision()
    except Exception:
        n0_rev = None
    res["grade"]["input_revisions"] = {
        "performance_model": ((pf_.get("hltv_data") or {}).get("input_revision") or n0_rev if v_.startswith("perf-2") and not v_.endswith("-bo3")
                              else f"player: BO3.gg maps (no HLTV history); opponent: {n0_rev}" if v_.endswith("-bo3")
                              else "not used (old pipeline)"),
        "last10_h2h_records": "full HLTV tables (repaired history incl. the 2026-10-13 backfill) + BO3.gg",
        "stake_checks": "full HLTV tables (maps played, last map date, AWP share) + BO3.gg",
        "settlement_display": "full HLTV tables"}
    try:                                     # display only (weight 0): HLTV history that stops well before BO3.gg's
        _hltv_staleness(res["grade"], res["grader_inputs"].get("perf"), pb)
    except Exception:
        pass
    try:                                     # display only: why the projection can sit far from his last-10 average
        res["form_gap"] = _form_gap(res, pb, args, stat)
    except Exception:
        res["form_gap"] = None
    res["ladder"] = _ladder(res, pb, args)
    _tick(res, "grade", t0)
    try:   # 20,000-run match simulation built on the grade (same projection and spread) - how it plays out
        from sim import simulate_grade
        res["simulation"] = None if getattr(args, "light", False) else simulate_grade(res["grade"])
        _tick(res, "sim", t0)
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
