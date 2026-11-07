"""LoL data center (backend only, not released): source revisions, paced/paged ingestion, internal ids with renames and
quarantine, game order from schedule ids, exact-scope settlement by platform rules, the append-only quote archive and
schedule-constrained offer matching. Every team, player and number here is a TEST FIXTURE (no network)."""
import json

import pytest

from lol.center import build, cargo as lp, ingest, markets, match, rules, settle, store


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


def put(con, tbl, rows, at="2026-10-01T00:00:00Z"):
    spec = ingest.WINDOWED.get(tbl) or ingest.REFERENCE.get(tbl)
    tcol = spec.get("tcol")
    return store.put_rows(con, tbl, rows, spec["key"], (lambda r: r.get(tcol)) if tcol else None, None, at)


# ---------------------------------------------------------------- storage
def test_revisions_kept_and_gone_never_deleted(con):
    row = {"GameId": "G1", "DateTime_UTC": "2026-10-01 10:00:00", "Team1Kills": "10"}
    assert put(con, "ScoreboardGames", [row])["new"] == 1
    assert put(con, "ScoreboardGames", [dict(row)], at="2026-10-02T00:00:00Z")["same"] == 1
    assert put(con, "ScoreboardGames", [dict(row, Team1Kills="11")], at="2026-10-03T00:00:00Z")["changed"] == 1
    revs = con.execute("SELECT rev, json_extract(data, '$.Team1Kills') FROM lp_rows WHERE skey = 'G1' ORDER BY rev").fetchall()
    assert [tuple(r) for r in revs] == [(1, "10"), (2, "11")]           # the earlier revision is kept
    n = store.mark_gone(con, "ScoreboardGames", "2026-10-01 00:00:00", "2026-10-15 00:00:00", "2026-10-04T00:00:00Z", "2026-10-04T00:00:01Z")
    assert n == 1 and store.current_rows(con, "ScoreboardGames") == []
    assert con.execute("SELECT COUNT(*) FROM lp_rows WHERE skey = 'G1'").fetchone()[0] == 2   # nothing deleted
    put(con, "ScoreboardGames", [dict(row, Team1Kills="11")], at="2026-10-05T00:00:00Z")
    assert len(store.current_rows(con, "ScoreboardGames")) == 1          # reappeared -> a new revision, present again


def test_internal_ids_are_ours_and_sport_scoped(con):
    a = store.entity(con, "team", "leaguepedia:page", "T1")
    b = store.entity(con, "team", "leaguepedia:page", "T1 Esports Academy")
    assert a.startswith("lol:team:") and a != b and store.entity(con, "team", "leaguepedia:page", "T1") == a


# ---------------------------------------------------------------- client + paging
def test_pacer_backs_off_on_rate_limit_then_succeeds(monkeypatch):
    calls = []
    answers = [json.dumps({"error": {"code": "ratelimited"}}).encode(), json.dumps({"cargoquery": [{"title": {"A B": "1", "A B__precision": "0"}}]}).encode()]

    def fake(params, data=None, timeout=60):
        calls.append(params)
        return answers.pop(0)
    monkeypatch.setattr(lp, "_raw", fake)
    monkeypatch.setattr(lp.time, "sleep", lambda s: None)
    monkeypatch.setattr(lp, "PACER", lp.Pacer(base=1, ceiling=8))
    monkeypatch.setattr(lp, "_OPENER", {"o": object(), "logged_in": None})
    rows, raw, params = lp.cargo("T=T", ["T.A_B=A_B"], "T.A_B")
    assert rows == [{"A_B": "1"}] and len(calls) == 2 and lp.PACER.stats["ratelimited"] == 1
    assert params["fields"] == "T.A_B=A_B" and params["order_by"] == "T.A_B" and params["limit"] == "500"
    with pytest.raises(ValueError):
        lp.cargo("T=T", ["T.A=A"], "")                                  # deterministic order is mandatory


def test_sweep_pages_until_short_page_and_marks_gone_only_when_complete(con, monkeypatch):
    pages = {0: [{"GameId": f"G{i}", "DateTime_UTC": "2026-10-02 00:00:00"} for i in range(500)],
             500: [{"GameId": "G500", "DateTime_UTC": "2026-10-02 00:00:00"}]}
    seen = []

    def fake(tables, fields, order, where=None, join_on=None, offset=0, **k):
        seen.append(offset)
        return pages[offset], b"{}", {"offset": offset}
    monkeypatch.setattr(lp, "cargo", fake)
    put(con, "ScoreboardGames", [{"GameId": "OLD", "DateTime_UTC": "2026-10-03 00:00:00"}], at="2026-01-01T00:00:00Z")
    t = ingest.sweep(con, "ScoreboardGames", "2026-10-01 00:00:00", "2026-10-15 00:00:00")
    assert seen == [0, 500] and t["rows"] == 501 and t["gone"] == 1
    assert con.execute("SELECT COUNT(*) FROM raw_snapshots").fetchone()[0] == 2   # every page kept

    def broken(*a, **k):
        raise lp.RateLimited("x")
    monkeypatch.setattr(lp, "cargo", broken)
    before = con.execute("SELECT COUNT(*) FROM lp_current WHERE gone_at IS NULL").fetchone()[0]
    ok, agg = ingest.run_unit(con, ("window", ("ScoreboardGames", "2026-10-01 00:00:00", "2026-10-15 00:00:00")))
    assert not ok and con.execute("SELECT COUNT(*) FROM lp_current WHERE gone_at IS NULL").fetchone()[0] == before


def test_windows_fixed_and_newest_first():
    from datetime import date
    w = ingest.windows(date(2026, 10, 16), days=30)
    assert w[0][0] > w[-1][0] and all(lo < hi for lo, hi in w)
    assert ingest.windows(date(2026, 10, 16), days=30)[1:] == ingest.windows(date(2026, 10, 17), days=31)[1:]


# ---------------------------------------------------------------- canonical build fixtures
TEAMS = [{"OverviewPage": "Alpha Esports", "Name": "Alpha Esports", "Short": "ALP"},
         {"OverviewPage": "Alpha Esports Academy", "Name": "Alpha Esports Academy", "Short": "ALPA"},
         {"OverviewPage": "Beta Gaming", "Name": "Beta Gaming", "Short": "BET"}]
ROLES = ["Top", "Jungle", "Mid", "Bot", "Support"]


def roster(team):
    return [f"{team[:4]}{r}" for r in ROLES]


def game_rows(mid, n, t1, t2, when, kills1=(1, 2, 3, 4, 0), kills2=(0, 1, 1, 2, 0), winner=1, link_override=None):
    gid = f"{mid}_{n}"
    sg = {"GameId": gid, "MatchId": mid, "OverviewPage": "Test League 2026", "Team1": t1, "Team2": t2, "DateTime_UTC": when,
          "Team1Kills": str(sum(kills1)), "Team2Kills": str(sum(kills2)), "Winner": str(winner), "N_GameInMatch": str(n),
          "Patch": "26.20", "Gamelength_Number": "31.5"}
    sp = []
    for side, team, ks in ((1, t1, kills1), (2, t2, kills2)):
        for i, (name, k) in enumerate(zip(roster(team), ks)):
            link = (link_override or {}).get(name, name)
            sp.append({"GameId": gid, "MatchId": mid, "OverviewPage": "Test League 2026", "Link": link, "Name": name, "Team": team,
                       "TeamVs": t2 if team == t1 else t1, "Role": ROLES[i], "Side": str(side), "Kills": str(k), "Deaths": "1",
                       "Assists": "2", "TeamKills": str(sum(ks)), "CS": "200", "DateTime_UTC": when,
                       "UniqueLine": f"{gid}_{side}_{i}", "PlayerWin": "Yes" if (side == winner) else "No"})
    return sg, sp


def load_world(con, extra_games=(), best_of="3", score=(2, 0)):
    put(con, "Teams", TEAMS)
    put(con, "Tournaments", [{"OverviewPage": "Test League 2026", "Name": "Test League 2026", "League": "Test League", "Region": "Test"}])
    redirects = [{"AllName": n, "OverviewPage": n} for t in ("Alpha Esports", "Beta Gaming") for n in roster(t)]
    put(con, "PlayerRedirects", redirects)
    put(con, "Players", [{"OverviewPage": r["OverviewPage"], "ID": r["AllName"]} for r in redirects])
    for n in {r["AllName"] for r in redirects}:
        con.execute("INSERT OR IGNORE INTO ref_state (kind, name, checked_at) VALUES ('PlayerRedirects', ?, 'x')", (n,))
    put(con, "MatchSchedule", [{"MatchId": "M1", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "Team1Score": str(score[0]), "Team2Score": str(score[1]), "Winner": "1", "BestOf": best_of,
                                "DateTime_UTC": "2026-10-10 09:00:00"}])
    # schedule game order; the scoreboard rows arrive in REVERSE order (API order must not matter)
    put(con, "MatchScheduleGame", [{"GameId": "M1_1", "MatchId": "M1", "N_GameInMatch": "1", "Match_DateTime_UTC": "2026-10-10 09:00:00"},
                                   {"GameId": "M1_2", "MatchId": "M1", "N_GameInMatch": "2", "Match_DateTime_UTC": "2026-10-10 09:00:00"}])
    g1 = game_rows("M1", 1, "Alpha Esports", "Beta Gaming", "2026-10-10 09:05:00")
    g2 = game_rows("M1", 2, "Beta Gaming", "Alpha Esports", "2026-10-10 09:50:00", kills1=(0, 1, 2, 1, 0), kills2=(3, 1, 4, 2, 1), winner=2)
    for sg, sp in [g2, g1] + list(extra_games):
        put(con, "ScoreboardGames", [sg])
        put(con, "ScoreboardPlayers", sp)
    con.commit()


def test_build_order_from_schedule_and_checks(con):
    load_world(con)
    out = build.build(con)
    assert out["games"] == 2 and out["player_games"] == 20 and not out["checks"].get("games_kills_mismatch")
    gs = con.execute("SELECT game_n, lp_game_id FROM c_games ORDER BY game_n").fetchall()
    assert [tuple(g) for g in gs] == [(1, "M1_1"), (2, "M1_2")]
    s = con.execute("SELECT * FROM c_series").fetchone()
    assert s["sid"].startswith("lol:series:") and s["best_of"] == 3 and s["n_games"] == 2 and s["status"] == "complete"
    again = build.build(con)                                           # repeatable: same ids
    assert again["games"] == 2 and con.execute("SELECT sid FROM c_series").fetchone()[0] == s["sid"]


def test_kills_mismatch_and_order_conflict_flagged(con):
    load_world(con)
    sg, sp = game_rows("M1", 2, "Beta Gaming", "Alpha Esports", "2026-10-10 09:50:00", kills1=(0, 1, 2, 1, 0), kills2=(3, 1, 4, 2, 1), winner=2)
    put(con, "ScoreboardGames", [dict(sg, Team1Kills="9", N_GameInMatch="3")], at="2026-10-11T00:00:00Z")
    out = build.build(con)
    assert out["checks"]["lp_games_team_total_differs"] == 1 and out["checks"]["order_conflict"] == 1
    # the raw team total (9) is kept; the canonical team kills are the player-kill sum (no whole-game exclusion)
    g = con.execute("SELECT team1_kills, team1_kills_raw, team_kills_status FROM c_games WHERE lp_game_id = 'M1_2'").fetchone()
    assert tuple(g) == (4, 9, "raw_total_differs_from_player_sum")
    assert con.execute("SELECT COUNT(*) FROM c_player_games WHERE gid = (SELECT gid FROM c_games WHERE lp_game_id = 'M1_2')").fetchone()[0] == 10
    # bug sweep 2026-11-06 #19: the coverage column counts the flag the build writes (it read 0 for every competition)
    from lol.center import coverage
    cov = coverage.compute(con)
    assert cov["game_flags"].get("team_total_differs_from_player_sum") == 1
    assert sum((d.get("kills_mismatch") or 0) for d in cov["by_competition"].values()) == 1


def test_rename_keeps_player_id_and_ambiguous_link_is_quarantined(con):
    load_world(con)
    build.build(con)
    old = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    # the player is renamed upstream: page "AlphMid" becomes "Nova"; the old name now redirects
    put(con, "PlayerRedirects", [{"AllName": "AlphMid", "OverviewPage": "Nova"}, {"AllName": "Nova", "OverviewPage": "Nova"}], at="2026-10-12T00:00:00Z")
    con.execute("UPDATE lp_current SET gone_at = 'x' WHERE tbl = 'PlayerRedirects' AND skey = 'AlphMid|AlphMid'")
    sg, sp = game_rows("M2", 1, "Alpha Esports", "Beta Gaming", "2026-10-12 09:05:00", link_override={"AlphMid": "Nova"})
    put(con, "ScoreboardGames", [sg]); put(con, "ScoreboardPlayers", sp)
    build.build(con)
    assert store.find_entity(con, "player", "leaguepedia:page", "Nova") == old
    assert con.execute("SELECT COUNT(DISTINCT pid) FROM c_player_games WHERE name_shown = 'AlphMid'").fetchone()[0] == 1
    # a name that redirects to two different pages is never guessed
    put(con, "PlayerRedirects", [{"AllName": "Twin", "OverviewPage": "Twin (A)"}, {"AllName": "Twin", "OverviewPage": "Twin (B)"}], at="2026-10-13T00:00:00Z")
    sg, sp = game_rows("M3", 1, "Alpha Esports", "Beta Gaming", "2026-10-13 09:05:00", link_override={"AlphTop": "Twin"})
    put(con, "ScoreboardGames", [sg]); put(con, "ScoreboardPlayers", sp)
    out = build.build(con)
    assert out["checks"].get("player_unresolved_ambiguous") == 1
    assert con.execute("SELECT COUNT(*) FROM quarantine WHERE kind = 'player' AND reason = 'redirect_ambiguous'").fetchone()[0] == 1


# ---------------------------------------------------------------- settlement
def _ids(con):
    pid = store.find_entity(con, "player", "leaguepedia:page", "AlphMid")
    sid = con.execute("SELECT sid FROM c_series").fetchone()[0]
    return pid, sid


def test_bo3_sweep_settles_on_the_games_played(con):
    load_world(con)
    build.build(con)
    pid, sid = _ids(con)
    for platform in ("underdog", "prizepicks"):
        r = settle.settle(con, platform, "kills", "maps_1_2_3", pid, sid, 5.5)
        # AlphMid: 3 kills in game 1 + 4 in game 2 (as Team2) = 7; game 3 never played -> games 1 + 2 (owner 2026-10-17)
        assert r["status"] == "graded" and r["actual"] == 7 and r["game_numbers"] == [1, 2] and r["over_hit"] == 1
        assert any("settled on the games played" in x for x in r["reasons"])


def test_bo5_full_scope_grades_and_push_and_dnp(con):
    load_world(con, best_of="5", score=(3, 0),
               extra_games=[game_rows("M1", 3, "Alpha Esports", "Beta Gaming", "2026-10-10 10:40:00", kills1=(1, 1, 1, 1, 1))])
    put(con, "MatchScheduleGame", [{"GameId": "M1_3", "MatchId": "M1", "N_GameInMatch": "3", "Match_DateTime_UTC": "2026-10-10 09:00:00"}])
    build.build(con)
    pid, sid = _ids(con)
    r = settle.settle(con, "underdog", "kills", "maps_1_2_3", pid, sid, 7.5)
    assert r["status"] == "graded" and r["actual"] == 8 and r["over_hit"] == 1 and r["game_numbers"] == [1, 2, 3]
    assert settle.settle(con, "prizepicks", "kills", "maps_1_2_3", pid, sid, 8.0)["status"] == "push"
    fp = settle.settle(con, "underdog", "fantasy_points", "maps_1_2_3", pid, sid, 30.5)
    assert fp["actual"] == round(3 * 8 + 2 * 6 - 3 + 0.02 * 600, 2)
    ghost = store.entity(con, "player", "leaguepedia:page", "Nobody")
    assert settle.settle(con, "underdog", "kills", "maps_1_2_3", ghost, sid, 1.5)["status"] == "dnp"


def test_forfeit_voids_on_underdog_and_settlement_versions(con):
    load_world(con)
    put(con, "MatchScheduleGame", [{"GameId": "M1_2", "MatchId": "M1", "N_GameInMatch": "2", "FF": "1",
                                    "Match_DateTime_UTC": "2026-10-10 09:00:00"}], at="2026-10-11T00:00:00Z")
    build.build(con)
    pid, sid = _ids(con)
    assert settle.settle(con, "underdog", "kills", "maps_1_2_3", pid, sid, 5.5)["status"] == "void"
    assert settle.settle(con, "prizepicks", "kills", "maps_1_2_3", pid, sid, 5.5)["status"] == "rule_unverified"
    v1 = settle.record(con, "u1", "underdog", "kills", "maps_1_2_3", pid, sid, 5.5)
    assert v1["version"] == 1 and settle.record(con, "u1", "underdog", "kills", "maps_1_2_3", pid, sid, 5.5).get("unchanged")
    # an upstream correction (forfeit flag removed) -> a NEW version superseding the first; version 1 is kept
    put(con, "MatchScheduleGame", [{"GameId": "M1_2", "MatchId": "M1", "N_GameInMatch": "2", "Match_DateTime_UTC": "2026-10-10 09:00:00"}],
        at="2026-10-12T00:00:00Z")
    build.build(con)
    v2 = settle.record(con, "u1", "underdog", "kills", "maps_1_2_3", pid, sid, 5.5)
    rows = con.execute("SELECT version, status, supersedes FROM lol_settlements WHERE unit_key = 'u1' ORDER BY version").fetchall()
    assert v2["version"] == 2 and [r[1] for r in rows] == ["void", "graded"] and rows[1][2] is not None


def test_rules_have_sources_and_verified_flags():
    for p, spec in rules.RULES.items():
        assert spec["source"] and spec["read_at"]
        assert all(isinstance(v["verified"], bool) and v["handling"] for v in spec["rules"].values())
    assert rules.unverified("underdog") == ["remake"]
    assert rules.unverified("prizepicks") == ["combined", "forfeit", "remake"]
    assert rules.rule("prizepicks", "unplayed_game_in_scope")["basis"].startswith("owner")
    assert rules.rule("underdog", "player_active")["verified"] is True


# ---------------------------------------------------------------- quote archive
UD = {"appearances": [{"id": "a1", "match_id": 9, "player_id": "p1", "team_id": "tA"}],
      "games": [{"id": 9, "home_team_id": "tB", "away_team_id": "tA", "full_team_names_title": "Alpha Esports @ Beta Gaming",
                 "short_title": "ALP @ BET", "scheduled_at": "2026-10-20T09:00:00Z", "title": "Alpha Esports vs. Beta Gaming"}],
      "players": [{"id": "p1", "first_name": "", "last_name": "AlphMid", "position_name": "Gamer"}],
      "over_under_lines": [{"id": "L1", "over_under_id": "OU1", "line_type": "balanced", "stat_value": "6.5", "status": "active",
                            "updated_at": "2026-10-19T00:00:00Z",
                            "over_under": {"appearance_stat": {"appearance_id": "a1", "stat": "kills_on_maps_1_2_3", "display_stat": "Kills on Maps 1+2+3"}},
                            "options": [{"choice": "higher", "status": "active", "payout_multiplier": "1.1"}]}]}


def test_archive_is_append_only(con):
    raw = json.dumps(UD).encode()
    assert markets.poll(con, "underdog", fetcher=lambda p: (200, raw))["events"] == 1
    assert markets.poll(con, "underdog", fetcher=lambda p: (200, raw))["events"] == 0          # unchanged: heartbeat only
    b = json.loads(raw)
    b["over_under_lines"][0]["options"].append({"choice": "lower", "status": "active", "payout_multiplier": "0.9"})
    assert markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(b).encode()))["events"] == 1   # a side added
    b["over_under_lines"][0]["stat_value"] = "7.5"
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(b).encode()))
    empty = json.dumps(dict(UD, over_under_lines=[])).encode()
    markets.poll(con, "underdog", fetcher=lambda p: (200, empty))
    markets.poll(con, "underdog", fetcher=lambda p: (500, b"down"))
    ev = [tuple(r) for r in con.execute("SELECT event, line, sides FROM market_events ORDER BY id")]
    assert [e[0] for e in ev] == ["open", "change", "change", "removed"] and [e[1] for e in ev[:3]] == [6.5, 6.5, 7.5]
    assert set(json.loads(ev[0][2])) == {"higher"} and set(json.loads(ev[1][2])) == {"higher", "lower"}
    polls = con.execute("SELECT ok FROM market_polls ORDER BY id").fetchall()
    assert [p[0] for p in polls] == [1, 1, 1, 1, 1, 0]


def test_empty_board_needs_two_polls_before_removal(con):
    lines = [dict(UD["over_under_lines"][0], id=f"L{i}", over_under_id=f"OU{i}") for i in range(5)]
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(dict(UD, over_under_lines=lines)).encode()))
    empty = json.dumps(dict(UD, over_under_lines=[])).encode()
    markets.poll(con, "underdog", fetcher=lambda p: (200, empty))
    assert con.execute("SELECT COUNT(*) FROM market_events WHERE event = 'removed'").fetchone()[0] == 0
    markets.poll(con, "underdog", fetcher=lambda p: (200, empty))
    assert con.execute("SELECT COUNT(*) FROM market_events WHERE event = 'removed'").fetchone()[0] == 5


def test_pp_stat_scope_and_sides():
    assert markets.pp_stat("MAPS 1-3 Kills") == ("kills", "maps_1_2_3", False)
    assert markets.pp_stat("MAPS 1-3 Kills (Combo)") == ("kills", "maps_1_2_3", True)
    assert markets.ud_stat("period_1_2_3_fantasy_points", "") == ("fantasy_points", "maps_1_2_3")
    assert markets.ud_stat("assists_on_maps_1_2_3", "") == ("assists", "maps_1_2_3")
    assert markets.ud_stat("first_blood", "")[1] is None                                       # unmapped: archived only
    assert markets.pp_sides([], "demon", True)["lower"]["offered"] is None


# ---------------------------------------------------------------- offer -> canonical
def test_offer_links_through_schedule_not_name_alone(con):
    load_world(con)
    put(con, "MatchSchedule", [{"MatchId": "M9", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "BestOf": "3", "DateTime_UTC": "2026-10-20 09:00:00"},
                               {"MatchId": "M8", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports Academy", "Team2": "Beta Gaming",
                                "BestOf": "3", "DateTime_UTC": "2026-10-25 09:00:00"}], at="2026-10-15T00:00:00Z")
    build.build(con)
    o = markets.parse_underdog(UD)[0]
    o["platform"] = "underdog"
    r = match.link(con, o)
    assert r["status"] == "matched" and r["pid"] == store.find_entity(con, "player", "leaguepedia:page", "AlphMid") and r["role"] == "Mid"
    assert r["sid"] == store.find_entity(con, "series", "leaguepedia:match", "M9")
    # an academy-vs-Beta series five days later is a different team id and outside the window: never matched to it
    o2 = dict(o, start_time="2026-10-25T09:00:00Z", team="Alpha Esports Academy", extra=dict(o["extra"], team_abbr="ALPA"))
    r2 = match.link(con, o2)
    assert r2["status"] == "unmatched" and r2["reason"] == "player_not_on_team"
    # a rematch inside the window -> quarantined, not guessed
    put(con, "MatchSchedule", [{"MatchId": "M10", "OverviewPage": "Test League 2026", "Team1": "Beta Gaming", "Team2": "Alpha Esports",
                                "BestOf": "3", "DateTime_UTC": "2026-10-20 15:00:00"}], at="2026-10-15T01:00:00Z")
    build.build(con)
    assert match.link(con, o)["reason"] == "series_ambiguous"


# ---------------------------------------------------------------- LoL Esports source (no login)
def le_world(con, wins=(3, 1), slots=5, done=True):
    teams = [{"id": "RA", "name": "Red Arrows", "code": "RA", "game_wins": wins[0]}, {"id": "BB", "name": "Blue Bears", "code": "BB", "game_wins": wins[1]}]
    n_played = sum(wins)
    games = [{"number": n, "id": f"g{n}", "state": ("completed" if n <= n_played else "unneeded") if done else "unstarted",
              "sides": {"RA": "blue", "BB": "red"}} for n in range(1, slots + 1)]
    put_le = lambda tbl, rows, key: store.put_rows(con, tbl, rows, key, lambda r: "2026-10-20 18:00:00", None, "2026-10-21T00:00:00Z")
    put_le("LE:Match", [{"match_id": "M7", "league_slug": "worlds", "league": "Worlds", "start": "2026-10-20T18:00:00Z", "best_of": 5,
                         "teams": teams, "games": games}], lambda r: r["match_id"])
    if not done:
        return
    for n in range(1, n_played + 1):
        winner_side = "blue" if n != 2 else "red"           # RA wins games 1, 3, 4; BB wins game 2 (3-1)
        bk, rk = (10, 4) if winner_side == "blue" else (5, 12)
        # rows arrive in REVERSE game order: the schedule number decides the order
        put_le("LE:Game", [{"game_id": f"g{n}", "match_id": "M7", "number": n, "start": f"2026-10-20T{17 + n}:05:00Z", "duration_s": 1900,
                            "patch": "16.20.826.3891", "blue_team_id": "RA", "red_team_id": "BB", "blue_kills": bk, "red_kills": rk,
                            "winner_side": winner_side, "winner_basis": "inferred from inhibitors"}], lambda r: r["game_id"])
        rows = []
        for side, team, k in (("blue", "RA", bk), ("red", "BB", rk)):
            ks = [k - 4, 1, 1, 1, 1] if k >= 4 else [k, 0, 0, 0, 0]
            for i, rl in enumerate(["top", "jungle", "mid", "bot", "support"]):
                rows.append({"game_id": f"g{n}", "match_id": "M7", "number": n, "player_id": f"{team}{i}", "name": f"{team}p{i}",
                             "team_id": team, "side": side, "role": rl, "kills": ks[i], "deaths": 1, "assists": 3, "cs": 250})
        put_le("LE:PlayerGame", rows, lambda r: f"{r['game_id']}|{r['player_id']}")
    con.commit()


def test_lolesports_build_ids_order_winners_patch(con):
    le_world(con)
    out = build.build(con)
    assert out["lolesports"]["series"] == 1 and out["lolesports"]["games"] == 4 and out["player_games"] == 40
    s = con.execute("SELECT * FROM c_series").fetchone()
    assert s["source"] == "lolesports" and s["status"] == "complete" and s["best_of"] == 5 and s["winner"] == 1
    gs = con.execute("SELECT game_n, patch, winner_tid, flags FROM c_games ORDER BY game_n").fetchall()
    ra = store.find_entity(con, "team", "lolesports:team", "RA")
    assert [g[0] for g in gs] == [1, 2, 3, 4] and gs[0][1] == "26.20"
    # 3-1: only the last game's winner follows with certainty from the official score; the frame-inferred winners of
    # games 1-3 add up to 3-1 but are NOT certified (two wrong labels can still add up)
    assert [g[2] == ra for g in gs] == [False, False, False, True] and [g[2] for g in gs][:3] == [None, None, None]
    st = [r[0] for r in con.execute("SELECT winner_status FROM c_games ORDER BY game_n")]
    inf = [r[0] == ra for r in con.execute("SELECT winner_inferred_tid FROM c_games ORDER BY game_n")]
    assert st == ["inferred_unverified"] * 3 + ["derived_last_game"] and inf == [True, False, True, True]
    assert not any("team_total_differs" in g[3] for g in gs)
    pid = store.find_entity(con, "player", "lolesports:player", "RA2")
    r = settle.settle(con, "underdog", "kills", "maps_1_2_3", pid, s["sid"], 2.5)
    assert r["status"] == "graded" and r["actual"] == 3 and r["game_numbers"] == [1, 2, 3]


def test_lolesports_unscheduled_series_and_vs_title_link(con):
    le_world(con)                                                        # M7 played 2026-10-20: RA / BB players have games
    nxt = {"match_id": "M8", "league_slug": "worlds", "league": "Worlds", "start": "2026-10-22T18:00:00Z", "best_of": 5,
           "teams": [{"id": "RA", "name": "Red Arrows", "code": "RA", "game_wins": 0}, {"id": "BB", "name": "Blue Bears", "code": "BB", "game_wins": 0}],
           "games": [{"number": 1, "id": "h1", "state": "unstarted", "sides": {}}]}
    store.put_rows(con, "LE:Match", [nxt], lambda r: r["match_id"], lambda r: "2026-10-22 18:00:00", None, "2026-10-21T00:00:00Z")
    build.build(con)
    assert con.execute("SELECT status FROM c_series WHERE source_match_id = 'M8'").fetchone()[0] == "scheduled"
    # an Underdog line whose game title is "A vs B" (no away/home): the player decides the team
    raw = dict(UD, games=[dict(UD["games"][0], full_team_names_title="Red Arrows vs Blue Bears", short_title="TBD-2 vs TBD-1",
                               title="Red Arrows vs Blue Bears", scheduled_at="2026-10-22T18:00:00Z")],
               players=[dict(UD["players"][0], last_name="RAp2")])
    o = markets.parse_underdog(raw)[0]
    assert o["team"] is None and o["extra"]["matchup_names"] == ["Red Arrows", "Blue Bears"]
    o["platform"] = "underdog"
    r = match.link(con, o)
    assert r["status"] == "matched" and r["tid"] == store.find_entity(con, "team", "lolesports:team", "RA")
    assert r["sid"] == store.find_entity(con, "series", "lolesports:match", "M8") and r["role"] == "Mid"
    # only games that ended before the series' start count: the same offer for M7 itself finds no earlier roster
    early = dict(o, start_time="2026-10-20T18:00:00Z")
    assert match.link(con, early)["reason"] in ("player_not_on_team", "series_ambiguous")


def test_wrong_merge_of_two_leaguepedia_pages_is_split_back(con):
    """Repair: a merge made before the guard (one LoL Esports id paired with two Leaguepedia players) is reversed."""
    a = store.entity(con, "player", "leaguepedia:page", "Alpha")
    le = store.entity(con, "player", "lolesports", "le-1")
    store.merge(con, a, le, "player identified in a series both sources hold")
    b = store.entity(con, "player", "leaguepedia:page", "Beta")
    store.link_source(con, b, "player", "lolesports", "le-2", "x")
    store.merge(con, a, b, "player identified in a series both sources hold")     # the old bug: two pages on one id
    assert store.canonical_id(con, b) == a
    assert build.split_wrong_merges(con, "2026-10-17T06:00:00Z") == 1
    assert store.canonical_id(con, b) == b and store.find_entity(con, "player", "leaguepedia:page", "Beta") == b
    # LoL Esports keys are not moved by the split: they go where their corroborated shared games put them (link_overlaps)
    assert store.find_entity(con, "player", "lolesports", "le-2") == a
    assert store.find_entity(con, "player", "leaguepedia:page", "Alpha") == a
    assert store.find_entity(con, "player", "lolesports", "le-1") == a              # the earlier, correct merge stays
    q = con.execute("SELECT reason FROM quarantine WHERE skey LIKE 'split:%'").fetchall()
    assert [r[0] for r in q] == ["wrong_merge_split"]
    assert build.split_wrong_merges(con, "2026-10-17T06:10:00Z") == 0


def test_split_is_stable_for_the_aiming_lazyfeel_case(con):
    """2026-10-17 production loop: E was created from Aiming's LoL Esports key; the pages Aiming (id X1) and LazyFeel (id
    X2) and LazyFeel's LoL Esports key were all merged into E, so every note named the same merges. The split keeps the
    page named like E, returns LazyFeel's page to the id created for it, and a second pass changes nothing."""
    e = store.entity(con, "player", "lolesports:player", "le-aiming", label="Aiming")
    lz = store.entity(con, "player", "lolesports:player", "le-lazyfeel", label="LazyFeel")
    x1 = store.entity(con, "player", "leaguepedia:page", "Aiming")
    x2 = store.entity(con, "player", "leaguepedia:page", "LazyFeel")
    for drop in (x2, x1, lz):
        store.merge(con, e, drop, "player identified in a series both sources hold")
    assert build.split_wrong_merges(con, "2026-10-17T21:00:00Z") == 1
    assert store.find_entity(con, "player", "leaguepedia:page", "Aiming") == e
    assert store.find_entity(con, "player", "leaguepedia:page", "LazyFeel") == x2 and store.canonical_id(con, x2) == x2
    assert build.split_wrong_merges(con, "2026-10-17T21:10:00Z") == 0
    # the evidence then moves LazyFeel's key to his page; the id first created for that key follows it
    new = store.detach_source(con, "player", "lolesports:player", "le-lazyfeel", "corroborated games", at="2026-10-17T21:11:00Z")
    build._attach(con, "player", "lolesports:player", "le-lazyfeel", x2, "2026-10-17T21:11:00Z")
    assert store.canonical_id(con, store.find_entity(con, "player", "lolesports:player", "le-lazyfeel")) == x2
    assert store.canonical_id(con, lz) == x2 and store.canonical_id(con, new) == x2
    assert build.split_wrong_merges(con, "2026-10-17T21:20:00Z") == 0


class _FakeLibc:
    def __init__(self, mallopt_ok=1):
        self.calls, self.ok = [], mallopt_ok

    def mallopt(self, param, value):
        self.calls.append(("mallopt", param, value))
        return self.ok

    def malloc_trim(self, pad):
        self.calls.append(("malloc_trim", pad))
        return 1


class _Threads:
    """Records threads in start order instead of running them."""
    def __init__(self, log):
        self.log = log

    def __call__(self, target=None, args=(), daemon=None, name=None):
        log = self.log

        class T:
            def start(self):
                log.append(name)
        return T()


def test_worker_caps_arenas_and_trims_like_the_web_process(monkeypatch):
    """2026-10-30: the worker is its own process, so the web process's allocator tuning never reached it and its resident
    memory ratcheted up between deploys. It caps glibc's arenas itself and trims freed memory back to the OS."""
    import ctypes
    from lol.center import worker
    libc, started = _FakeLibc(), []
    monkeypatch.setattr(ctypes, "CDLL", lambda name: libc)
    monkeypatch.setattr(worker.threading, "Thread", _Threads(started))
    monkeypatch.setattr(worker, "ALLOC", {"arena_max": None, "trims": 0, "last_trim_freed_mb": None, "platform": None})
    assert worker._tune_allocator(arenas=2) is True
    assert libc.calls == [("mallopt", -8, 2)] and worker.ALLOC["arena_max"] == 2 and started == ["lol-malloc-trim"]
    seq = iter([400.0, 310.0])
    monkeypatch.setattr(worker, "_rss", lambda: next(seq))
    worker._trim_once(libc)
    assert libc.calls[-1] == ("malloc_trim", 0) and worker.ALLOC["trims"] == 1 and worker.ALLOC["last_trim_freed_mb"] == 90.0
    monkeypatch.setattr(ctypes, "CDLL", lambda name: (_ for _ in ()).throw(OSError("no libc")))   # Windows / musl
    assert worker._tune_allocator() is False and worker.ALLOC["platform"] == "no glibc - not tuned"


def test_worker_is_first_in_line_for_the_oom_killer(monkeypatch, tmp_path):
    """2026-10-31: the container's memory peaked at 1,884 of 2,147 MB. If it runs out, the kernel must end the worker
    (restarted by server.py), never the web process members are on."""
    from lol.center import worker
    monkeypatch.setattr(worker, "ALLOC", {"arena_max": None, "trims": 0, "last_trim_freed_mb": None, "platform": None})
    adj = tmp_path / "oom_score_adj"
    adj.write_text("0")
    assert worker._prefer_oom_kill(path=str(adj)) is True
    assert adj.read_text() == "900" and worker.ALLOC["oom_score_adj"] == 900
    assert worker._prefer_oom_kill(path=str(tmp_path / "missing" / "oom_score_adj")) is False   # no /proc (Windows)


def test_worker_tunes_the_allocator_before_any_thread_starts(monkeypatch, tmp_path):
    """mallopt's arena cap only binds arenas created after it, so it must run before the worker's threads exist."""
    from lol.center import worker
    order = []
    monkeypatch.setattr(worker, "OUT", tmp_path)
    monkeypatch.setattr(worker, "_tune_allocator", lambda: order.append("tune"))
    monkeypatch.setattr(worker, "_prefer_oom_kill", lambda: order.append("oom"))
    monkeypatch.setattr(worker.threading, "Thread", _Threads(order))
    monkeypatch.setattr(worker, "write_status", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    for k in ("LOL_ARCHIVE", "LOL_LP", "LOL_LE"):
        monkeypatch.setenv(k, "1")
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert order[:2] == ["tune", "oom"] and len(order) > 2 and "tune" not in order[2:]


def test_worker_stage_memory_is_recorded_per_step(monkeypatch):
    """2026-10-18 memory: each worker step records its start / peak / end resident memory (TEST values)."""
    from lol.center import worker
    seq = iter([100.0, 180.0, 120.0])
    monkeypatch.setattr(worker, "_rss", lambda: next(seq))
    monkeypatch.setattr(worker, "MEM", {"stages": {}, "active": {}, "unattributed": {"peak_mb": 0.0}})
    with worker.stage("export"):
        worker.MEM["active"]["export"]["peak"] = max(worker.MEM["active"]["export"]["peak"], worker._rss())   # one sample
    st = worker.MEM["stages"]["export"]
    assert st["runs"] == 1 and st["peak_mb"] == 180.0 and st["last"]["start_mb"] == 100.0 and st["last"]["end_mb"] == 120.0
    assert worker.MEM["active"] == {}
    monkeypatch.setattr(worker, "_rss", lambda: None)                  # no /proc (Windows): runs counted, no memory
    with worker.stage("board"):
        pass
    assert worker.MEM["stages"]["board"]["runs"] == 1 and worker.MEM["stages"]["board"]["last"]["peak_mb"] is None
