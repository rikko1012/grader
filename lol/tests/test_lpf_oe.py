"""lol/lpf_oe.py (Oracle's Elixir second year, history only): overlap matching and id mapping, only pre-window unmatched games
written, and Store(extra=...) leaves targets and the live reconstruction unchanged while the candidate's features see it."""
import csv
import datetime as dt
import sqlite3

import pytest

from lol import lpf, lpf_data as D, lpf_oe as O
from lol.tests.test_lpf import _db

HEAD = ["gameid", "datacompleteness", "url", "league", "year", "split", "playoffs", "date", "game", "patch", "participantid", "side",
        "position", "playername", "playerid", "teamname", "teamid", "champion", "gamelength", "result", "kills", "deaths",
        "assists", "teamkills", "teamdeaths", "damagetochampions", "visionscore", "totalgold", "total cs"]
POS = ("top", "jng", "mid", "bot", "sup")


def _prod(tmp_path):
    p = _db(tmp_path / "prod.db")
    con = sqlite3.connect(p)
    con.execute("UPDATE hist_player_games SET champion = team || '_' || champion")     # ten distinct champions per game
    con.commit()
    first = con.execute("SELECT MIN(played_at) FROM hist_games").fetchone()[0]
    g1 = con.execute("SELECT player_id, team, champion, kills, deaths, assists, team_kills, opp_kills, win FROM hist_player_games "
                     "WHERE game_key = 'g1'").fetchall()
    con.close()
    return str(p), first, g1


def _oe_game(gid, when, gn, teams, champs, pids, kills=None, league="LA"):
    rows = []
    for side, (tname, tid) in zip(("Blue", "Red"), teams):
        tk = sum((kills or {}).get((tname, i), 1) for i in range(5))
        for i, pos in enumerate(POS):
            rows.append({"gameid": gid, "datacompleteness": "complete", "league": league, "year": "2025", "split": "Spring",
                         "playoffs": "0", "date": when, "game": gn, "patch": "15.05", "side": side, "position": pos,
                         "playername": pids[(tname, i)], "playerid": "oe:player:" + pids[(tname, i)], "teamname": tname,
                         "teamid": "oe:team:" + tid, "champion": champs[(tname, i)], "gamelength": "1800",
                         "result": "1" if side == "Blue" else "0", "kills": (kills or {}).get((tname, i), 1), "deaths": 1,
                         "assists": 2, "teamkills": tk, "teamdeaths": 5, "damagetochampions": 10000, "visionscore": 20,
                         "totalgold": 9000, "total cs": 200})
        rows.append({"gameid": gid, "datacompleteness": "complete", "league": league, "year": "2025", "split": "Spring",
                     "playoffs": "0", "date": when, "game": gn, "patch": "15.05", "side": side, "position": "team",
                     "teamname": tname, "teamid": "oe:team:" + tid, "result": "1" if side == "Blue" else "0", "kills": tk})
    return rows


def _csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, HEAD)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in HEAD})
    return str(path)


def test_convert_writes_only_unmatched_pre_window_games_with_mapped_ids(tmp_path):
    prod, first, g1 = _prod(tmp_path)
    t1 = O.iso_time(first)
    teams = [("a1", "A1"), ("a2", "A2")]
    pids = {(t, i): f"{t}x{i}" for t, _ in teams for i in range(5)}
    # the overlap game = production g1 (same champions, 20 min later); two earlier OE-only games of a1 v a2
    ch_g1 = {}
    for r in g1:
        ch_g1[(r[1], ["top", "jungle", "mid", "bot", "support"].index(sql_role(prod, r[0])))] = r[2]
    rows = _oe_game("OVL", (t1 + dt.timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S"), "1", teams, ch_g1, pids)
    old = t1 - dt.timedelta(days=30)
    other = {(t, i): f"z{t}{i}" for t, _ in teams for i in range(5)}
    rows += _oe_game("OLD1", old.strftime("%Y-%m-%d %H:%M:%S"), "1", teams, other, pids, kills={("a1", 0): 7})
    rows += _oe_game("OLD2", (old + dt.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"), "2", teams, other, pids)
    path = _csv(tmp_path / "oe.csv", rows)
    con = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
    oe = O.read_oe([path])
    matched, loc, rep = O.match_overlap(oe, con)
    assert matched == {"OVL": "g1"} and rep["counts"]["player_kda_differ"] > 0      # stats differ in the fixture: reported
    pmap, tmap, lmap, _ = O.id_maps(oe, matched, loc, con)
    assert tmap["oe:team:A1"] == "a1" and pmap["oe:player:a1x0"].startswith("p:a1") and lmap["LA"] == "LA"
    grows, prows = O.convert(oe, matched, pmap, tmap, lmap, cut=first)
    assert {g["game_key"] for g in grows} == {"oe:game:OLD1", "oe:game:OLD2"}
    assert len({g["match_key"] for g in grows}) == 1                                    # games 1 and 2 = one series
    assert all(p["played_at"] < first and p["source"] == "oe" for p in prows)
    assert all(p["team_id"] in ("a1", "a2") and not p["player_id"].startswith("oe:") for p in prows)
    assert {p["role"] for p in prows} == {"top", "jungle", "mid", "bot", "support"}
    assert grows[0]["patch"] == "25.05"
    out = tmp_path / "oe_hist.db"
    O.write(str(out), grows, prows)

    a, b = D.Store(prod), D.Store(prod, extra=[str(out)])
    assert b.extra_rows == 20
    ta, tb = D.targets(a), D.targets(b)
    assert [(t["match"], t["pid"]) for t in ta] == [(t["match"], t["pid"]) for t in tb]      # graded set unchanged
    Ba, Bb = D.LiveBaseline(a, log=lambda *x: None), D.LiveBaseline(b, log=lambda *x: None)
    for t, u in list(zip(ta, tb))[:40]:
        assert Ba.project(t)[0] == Bb.project(u)[0]                                      # live reconstruction unchanged
    Fa, Fb = lpf.Features(a, log=lambda *x: None), lpf.Features(b, log=lambda *x: None)
    t = ta[0]
    fa, fb = Fa.target(t), Fb.target(tb[0])
    assert any((fa[k] != fb[k]) and not (fa[k] != fa[k] and fb[k] != fb[k]) for k in fa)   # the candidate sees the extra year


def sql_role(prod, pid):
    con = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
    r = con.execute("SELECT role FROM hist_player_games WHERE player_id = ? LIMIT 1", (pid,)).fetchone()[0]
    con.close()
    return r


def test_store_rejects_extra_history_that_repeats_a_production_game(tmp_path):
    prod, _, _ = _prod(tmp_path)
    x = tmp_path / "x.db"
    con = sqlite3.connect(prod)
    g = con.execute("SELECT * FROM hist_games WHERE game_key = 'g1'").fetchone()
    con.close()
    O.write(str(x), [dict(zip(O.GCOLS, g))], [])
    with pytest.raises(ValueError):
        D.Store(prod, extra=[str(x)])


def test_series_keys_restart_on_game_one_and_long_gaps():
    t = dt.datetime(2025, 3, 1, 10, tzinfo=dt.timezone.utc)
    h = dt.timedelta(hours=1)
    pair = frozenset({"a", "b"})
    k = O.series_keys([("g1", t, "L", pair, 1), ("g2", t + h, "L", pair, 2), ("g3", t + 2 * h, "L", pair, 1),
                       ("g4", t + 30 * h, "L", pair, 2), ("g5", t + 3 * h, "M", pair, 2)])
    assert k["g1"] == k["g2"] != k["g3"] and k["g4"] != k["g3"] and k["g5"] not in (k["g1"], k["g3"], k["g4"])


def test_patch_and_champion_normalisation():
    assert O.patch_of("15.01", "2025") == "25.01" and O.patch_of("26.1", "2026") == "26.10" and O.patch_of("", "2025") is None
    assert O.champ("MonkeyKing") == O.champ("Wukong") and O.champ("Nunu & Willump") == "nunuwillump"
