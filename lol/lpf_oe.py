"""lpf-1 second year of history from Oracle's Elixir (research / shadow only; nothing live imports it).

Owner approved the download 2026-11-05. The Oracle's Elixir yearly match-data CSVs (one row per player per game plus two team
rows per game) are converted into the same shape as production's lol_games / lol_player_games (the hist_games /
hist_player_games tables of the local read-only copy) and written to a separate SQLite file. lpf_data.Store(db, extra=[that
file]) appends them as HISTORY ONLY:
  - only OE games that do not match a production game and are played before the production window (2025-11-03) are written,
    so every production row, target, settlement and the live reconstruction stay exactly as before;
  - targets() never makes a target from an OE row (graded count and the evaluation set are unchanged);
  - LiveBaseline ignores OE rows (live never had them, so the reconstruction of live stays exact);
  - the candidate's features (player / team / league / patch arrays, both Elo ratings, the running role prior) use them.

Identity: OE player / team ids are mapped to production ids by majority vote over the games both sources hold (matched on the
UTC time within 48 h and the set of ten champions; the per-player kills / deaths / assists are compared and reported). An id
never seen in the overlap keeps an "oe:" id of its own. OE league codes are mapped to production's league names the same way.
Series are rebuilt from consecutive games of the same two teams in the same league (game number restarting = new series).

The CSVs are untrusted data: they are only parsed with the csv module; nothing in them is executed.

  python -I -c "import sys; sys.path.insert(0, r'<worktree>'); from lol import lpf_oe; lpf_oe.main()" \
      --db <lol_full.db> --out <lol_oe_hist.db> <2025.csv> <2026.csv>
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

WINDOW_START = "2025-11-03T00:00:00Z"        # first day of the production history (lol_full.db manifest)
MATCH_SECONDS = 2 * 86400
ROLE = {"top": "top", "jng": "jungle", "mid": "mid", "bot": "bot", "sup": "support"}
CHAMP_ALIAS = {"monkeyking": "wukong", "nunu": "nunuwillump", "renata": "renataglasc"}
PCOLS = ("_row", "game_key", "player_key", "player", "player_id", "team", "team_id", "opponent", "opponent_id", "side", "role",
         "champion", "kills", "deaths", "assists", "cs", "gold", "team_kills", "opp_kills", "win", "duration_s", "match_key",
         "game_number", "best_of", "played_at", "league", "source", "damage", "vision")
GCOLS = ("_row", "game_key", "source", "match_key", "game_number", "best_of", "played_at", "league", "tournament", "blue_team",
         "red_team", "blue_team_id", "red_team_id", "blue_kills", "red_kills", "winner", "winner_basis", "duration_s", "patch",
         "fetched_at")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def champ(s):
    n = norm(s)
    return CHAMP_ALIAS.get(n, n)


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except ValueError:
        return None


def _float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def oe_time(s):
    return datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def iso_time(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def patch_of(p, year):
    """OE writes 2025 patches as 15.xx (Riot's 25.xx); production stores '25.21'. Two decimals."""
    f = _float(p)
    if f is None:
        return None
    major, minor = int(f), int(round((f - int(f)) * 100))
    if major < 20 and year and int(year) >= 2025:
        major += 10
    return f"{major}.{minor:02d}"


def read_oe(paths):
    """{gameid: {"players": [row...], "teams": [row...]}} from the OE CSVs (csv parsing only)."""
    games = defaultdict(lambda: {"players": [], "teams": []})
    for p in paths:
        with open(p, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g = games[r["gameid"]]
                (g["teams"] if r["position"] == "team" else g["players"]).append(r)
    return dict(games)


def oe_player_key(r):
    return r["playerid"] or ("name:" + norm(r["playername"]) + "|" + (r["teamid"] or norm(r["teamname"])))


def oe_team_key(r):
    return r["teamid"] or ("name:" + norm(r["teamname"]))


def match_overlap(oe, con):
    """Match OE games to production games: same set of ten champions, closest UTC time within 48 h.
    Returns ({gameid: game_key}, local player rows by game_key, report)."""
    loc = defaultdict(list)
    for r in con.execute("SELECT game_key, champion, kills, deaths, assists, played_at, team_id, player_id, game_number, league "
                         "FROM hist_player_games"):
        loc[r[0]].append(r)
    idx = defaultdict(list)
    for gk, rs in loc.items():
        idx[frozenset(champ(r[1]) for r in rs)].append(gk)
    w0 = iso_time(WINDOW_START)
    m, rep = {}, Counter()
    hours = Counter()
    unmatched_in_window = Counter()
    for gid, g in oe.items():
        if not g["players"]:
            continue
        t = oe_time(g["players"][0]["date"])
        inwin = t >= w0
        rep["oe_games_in_window" if inwin else "oe_games_before_window"] += 1
        key = frozenset(champ(r["champion"]) for r in g["players"])
        cands = [k for k in idx.get(key, ()) if abs((iso_time(loc[k][0][5]) - t).total_seconds()) < MATCH_SECONDS]
        if not cands:
            if inwin:
                unmatched_in_window[g["players"][0]["league"]] += 1
            continue
        k = min(cands, key=lambda k: abs((iso_time(loc[k][0][5]) - t).total_seconds()))
        m[gid] = k
        rep["matched_in_window" if inwin else "matched_before_window"] += 1
        dh = abs((iso_time(loc[k][0][5]) - t).total_seconds()) / 3600
        hours["<=1h" if dh <= 1 else "<=6h" if dh <= 6 else "<=48h"] += 1
        lc = {champ(r[1]): r for r in loc[k]}
        for r in g["players"]:
            l = lc.get(champ(r["champion"]))
            same = l is not None and (_int(r["kills"]), _int(r["deaths"]), _int(r["assists"])) == (l[2], l[3], l[4])
            rep["player_kda_equal" if same else "player_kda_differ"] += 1
        rep["game_number_equal" if _int(g["players"][0]["game"]) == loc[k][0][8] else "game_number_differ"] += 1
    rep["local_games"] = len(loc)
    rep["local_games_matched"] = len(set(m.values()))
    rep["local_only_games"] = len(loc) - len(set(m.values()))
    return m, loc, {"counts": dict(rep), "time_gap": dict(hours), "oe_unmatched_in_window_by_league": dict(unmatched_in_window)}


def id_maps(oe, matched, loc, con):
    """OE player / team / league id -> production id by majority vote over the matched games."""
    pv, tv, lv = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    glg = dict(con.execute("SELECT game_key, league FROM hist_games"))
    for gid, gk in matched.items():
        lc = {champ(r[1]): r for r in loc[gk]}
        for r in oe[gid]["players"]:
            l = lc.get(champ(r["champion"]))
            if l is None:
                continue
            pv[oe_player_key(r)][l[7]] += 1
            tv[oe_team_key(r)][l[6]] += 1
        lv[oe[gid]["players"][0]["league"]][glg.get(gk)] += 1
    pick = lambda v: {k: c.most_common(1)[0][0] for k, c in v.items()}  # noqa: E731
    ambiguous = {"players": sum(1 for c in pv.values() if len(c) > 1), "teams": sum(1 for c in tv.values() if len(c) > 1)}
    return pick(pv), pick(tv), pick(lv), ambiguous


def series_keys(games):
    """[(gameid, time, league, team pair, game no.)] -> {gameid: series key}. A new series starts when game 1 comes again,
    the game number does not increase, or more than 12 h pass since the previous game of the same pair."""
    by_pair = defaultdict(list)
    for gid, t, lg, pair, gn in games:
        by_pair[(lg, pair)].append((t, gn or 1, gid))
    out = {}
    for _, v in by_pair.items():
        v.sort()
        cur, last_t, last_gn = None, None, 0
        for t, gn, gid in v:
            if cur is None or gn <= last_gn or gn == 1 or (t - last_t).total_seconds() > 12 * 3600:
                cur = "oe:series:" + hashlib.sha1(gid.encode()).hexdigest()[:12]
            out[gid] = cur
            last_t, last_gn = t, gn
    return out


def convert(oe, matched, pmap, tmap, lmap, cut=WINDOW_START):
    """OE games that are not in production and are played before `cut` -> (game rows, player rows) in production's shape."""
    cut_t = iso_time(cut)
    keep = []
    for gid, g in oe.items():
        if gid in matched or len(g["players"]) != 10 or len(g["teams"]) != 2:
            continue
        t = oe_time(g["players"][0]["date"])
        if t >= cut_t:
            continue
        if any(_int(r["kills"]) is None or _int(r["deaths"]) is None or _int(r["assists"]) is None for r in g["players"]):
            continue
        keep.append((gid, t))
    tid = lambda r: tmap.get(oe_team_key(r)) or "oe:team:" + oe_team_key(r).split(":")[-1]  # noqa: E731
    sk = series_keys([(gid, t, oe[gid]["players"][0]["league"], frozenset(tid(r) for r in oe[gid]["teams"]),
                       _int(oe[gid]["players"][0]["game"])) for gid, t in keep])
    grows, prows = [], []
    for gid, t in sorted(keep, key=lambda x: x[1]):
        g = oe[gid]
        p0 = g["players"][0]
        when = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        lg = lmap.get(p0["league"]) or p0["league"]
        teams = {r["side"].lower(): r for r in g["teams"]}
        if set(teams) != {"blue", "red"}:
            continue
        tk = {s: _int(r["kills"]) for s, r in teams.items()}
        if tk["blue"] is None or tk["red"] is None:
            continue
        res = {s: _int(r["result"]) for s, r in teams.items()}
        gk = "oe:game:" + gid
        dur = _float(p0["gamelength"])
        tour = f"{lg} {p0['year']} {p0['split']}".strip() + (" Playoffs" if p0.get("playoffs") == "1" else "")
        grows.append({"game_key": gk, "source": "oe", "match_key": sk[gid], "game_number": _int(p0["game"]) or 1, "best_of": None,
                      "played_at": when, "league": lg, "tournament": tour, "blue_team": teams["blue"]["teamname"],
                      "red_team": teams["red"]["teamname"], "blue_team_id": tid(teams["blue"]), "red_team_id": tid(teams["red"]),
                      "blue_kills": tk["blue"], "red_kills": tk["red"],
                      "winner": (teams["blue"]["teamname"] if res["blue"] == 1 else teams["red"]["teamname"] if res["red"] == 1 else None),
                      "winner_basis": "oe", "duration_s": dur, "patch": patch_of(p0["patch"], p0["year"]), "fetched_at": None})
        for r in g["players"]:
            side = r["side"].lower()
            other = "red" if side == "blue" else "blue"
            pk = oe_player_key(r)
            prows.append({"game_key": gk, "player_key": norm(r["playername"]), "player": r["playername"],
                          "player_id": pmap.get(pk) or "oe:player:" + pk.split(":")[-1],
                          "team": r["teamname"], "team_id": tid(r), "opponent": teams[other]["teamname"],
                          "opponent_id": tid(teams[other]), "side": side, "role": ROLE.get(r["position"]), "champion": r["champion"],
                          "kills": _int(r["kills"]), "deaths": _int(r["deaths"]), "assists": _int(r["assists"]),
                          "cs": _int(r.get("total cs")), "gold": _int(r.get("totalgold")), "team_kills": tk[side],
                          "opp_kills": tk[other], "win": res[side], "duration_s": dur, "match_key": sk[gid],
                          "game_number": _int(r["game"]) or 1, "best_of": None, "played_at": when, "league": lg, "source": "oe",
                          "damage": _int(r.get("damagetochampions")), "vision": _int(r.get("visionscore"))})
    return grows, prows


def write(out, grows, prows):
    if os.path.exists(out):
        os.remove(out)
    con = sqlite3.connect(out)
    con.execute(f"CREATE TABLE hist_games ({', '.join(GCOLS)})")
    con.execute(f"CREATE TABLE hist_player_games ({', '.join(PCOLS)})")
    for t, cols, rows in (("hist_games", GCOLS, grows), ("hist_player_games", PCOLS, prows)):
        con.executemany(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[i + 1] + [r[c] for c in cols[1:]] for i, r in enumerate(rows)])
    con.commit()
    con.close()


def build(db, csvs, out, log=print):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    oe = read_oe(csvs)
    matched, loc, rep = match_overlap(oe, con)
    pmap, tmap, lmap, amb = id_maps(oe, matched, loc, con)
    grows, prows = convert(oe, matched, pmap, tmap, lmap)
    write(out, grows, prows)
    mapped_p = sum(1 for r in prows if not str(r["player_id"]).startswith("oe:"))
    mapped_t = sum(1 for r in prows if not str(r["team_id"]).startswith("oe:"))
    rep.update({"written_games": len(grows), "written_player_rows": len(prows),
                "written_series": len({g["match_key"] for g in grows}),
                "first_played_at": min((g["played_at"] for g in grows), default=None),
                "last_played_at": max((g["played_at"] for g in grows), default=None),
                "player_rows_with_production_player_id": mapped_p, "player_rows_with_production_team_id": mapped_t,
                "id_map_sizes": {"players": len(pmap), "teams": len(tmap), "leagues": len(lmap)},
                "ambiguous_ids": amb, "league_map": lmap})
    log(json.dumps(rep, indent=1, default=str))
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("csvs", nargs="+")
    a = ap.parse_args(argv if argv is not None else sys.argv[1:])
    rep = build(a.db, a.csvs, a.out)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, default=str)
    return rep


if __name__ == "__main__":
    main()
