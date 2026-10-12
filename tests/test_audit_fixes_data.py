"""Regression tests for the 2026-10-11 confirmed-findings batch: data / matching defects (one test per defect)."""
import argparse
import time
from datetime import date, datetime, timedelta, timezone

import cs2_scout
import comparison
import grader
import hltv
import perf_live
import server


# ---------------------------------------------------------------- 3 / 18. the ladder speaks the card's verdict
def test_ladder_entries_follow_the_verdict_gates():
    gi = {"prop": "hs", "line": 12.5, "line_type": "standard", "maps_played": 30, "kpr": 0.75, "hs_pct": 40.0,
          "sniping": 100, "team_ml": -150, "opp_ml": 130, "market_quality": "HARD", "roster_confirmed": True,
          "on_listed_team": True}
    res = {"grader_inputs": gi, "grade": grader.grade_leg(dict(gi))}
    lad = cs2_scout._ladder(res, {}, argparse.Namespace(line=12.5))
    assert lad and all(x["grade"] == "LEAN" and x["units"] == 0 for x in lad)       # AWPer on HS: 0U at every line
    v = server.verdict(res["grade"])
    step0 = next(x for x in lad if x["line"] == 12.5)
    assert (step0["side"], step0["grade"], step0["units"]) == (v["side"], v["grade"], v["units"])


def test_ladder_scout_lines_are_never_letters():
    gi = {"prop": "kills", "line": 20.5, "line_type": "standard", "maps_played": 30, "kpr": 0.80, "team_ml": -150,
          "opp_ml": 130, "market_quality": "HARD", "roster_confirmed": True, "on_listed_team": True, "scout": True}
    res = {"grader_inputs": gi, "grade": grader.grade_leg(dict(gi))}
    lad = cs2_scout._ladder(res, {}, argparse.Namespace(line=20.5))
    assert lad and all(x["state"] == "SCOUT" and x["units"] == 0 for x in lad)


# ---------------------------------------------------------------- 2 / 7. match selection (run): book typos + new teams
class _Stop(BaseException):
    pass


def _run_select(monkeypatch, pb, options, hl, member_opp=None, start=None):
    """cs2_scout.run up to the moment the match is chosen; returns (res, pb)."""
    monkeypatch.setattr(cs2_scout, "run_player", lambda b, name, args: pb)
    monkeypatch.setattr(cs2_scout, "upcoming_matches", lambda b, tid: list(options) if tid == pb.get("team_id") else [])
    monkeypatch.setattr(cs2_scout, "_hltv_schedule",
                        lambda names, opp=None: [m for m in hl if not opp or cs2_scout.name_match(opp, m["opponent"])])
    monkeypatch.setattr(cs2_scout, "match_odds", lambda *a, **k: {"found": False})

    def stop(*a, **k):
        raise _Stop()
    monkeypatch.setattr(cs2_scout, "_event_type", stop)
    monkeypatch.setattr(cs2_scout, "event_pool", stop)
    monkeypatch.setattr(cs2_scout, "h2h_rows", stop)              # no match: the run goes on to H2H - stop there

    import pandascore
    monkeypatch.setattr(pandascore, "upcoming", lambda *a, **k: [])
    monkeypatch.setattr(cs2_scout, "opponent_defense", lambda *a, **k: None)

    class B:
        background = False

        def team(self, slug):
            return {"players": []}

        def team_recent(self, *a, **k):
            return {"results": []}
    args = argparse.Namespace(cmd="full", name=pb["player"]["nick"], member_opponent=member_opp, member_start=start,
                              opponent=member_opp, match=None, maps=None, line=20.5, prop="kills", scope="12")
    res = {}
    try:
        cs2_scout.run(args, res, B(), 12)
    except _Stop:
        pass
    return res, pb


def _pb(**k):
    pb = {"player": {"nick": "ayuki", "id": 7}, "team_id": 11, "current_team": "Nuclear TigeRES", "team_slug": "nt",
          "recent_maps": [{"date": date.today().isoformat(), "team_id": 11, "team": "Nuclear TigeRES", "team_slug": "nt"}],
          "on_profile_roster": True, "roster_confirmed": True, "_matches": []}
    pb.update(k)
    return pb


def test_misspelled_book_opponent_resolves_by_start(monkeypatch):
    opt = {"start": "2026-10-12T14:00:00Z", "opponent": "XI Esport", "slug": "nt-vs-xi", "event": "E", "event_slug": "e",
           "maps": [], "team_slug": "nt"}
    res, pb = _run_select(monkeypatch, _pb(), [opt], [], member_opp="Xl", start="2026-10-12T14:00:00Z")
    assert res["next_match"]["opponent"] == "XI Esport" and pb["scout"] is False         # graded, not SCOUT
    res, pb = _run_select(monkeypatch, _pb(), [opt], [], member_opp="Xl", start="2026-10-14T14:00:00Z")
    assert "next_match" not in res and pb["scout"] is True                               # another time: never guessed
    assert cs2_scout._by_start([opt, dict(opt, slug="b")], "2026-10-12T14:10:00Z", lambda o: o["start"]) == []   # 2 fit


def test_fallback_match_for_another_team_runs_the_roster_rules(monkeypatch):
    hl = [{"team": "Nordic Partners", "opponent": "ex-RUSTEC", "start": datetime(2026, 10, 12, 14, tzinfo=timezone.utc),
           "format": "bo3", "event": "PGL Bucharest 2027 Open Qualifier 1", "url": "/matches/2399451/x"}]
    newmap = [{"date": date.today().isoformat(), "team_id": 31, "team": "Nordic Partners Gaming ", "team_slug": "npg"}]
    pb = _pb(player={"nick": "Fajr", "id": 9}, current_team="LPH Gaming", team_id=21, recent_maps=newmap)
    res, pb = _run_select(monkeypatch, pb, [], hl)
    assert res["next_match"]["opponent"] == "ex-RUSTEC"
    assert pb["standin_for"] and "Nordic Partners" in pb["standin_for"]               # his profile team still lists him
    pb2 = _pb(player={"nick": "Fajr", "id": 9}, current_team="LPH Gaming", team_id=21, on_profile_roster=False,
              recent_maps=newmap)
    res, pb2 = _run_select(monkeypatch, pb2, [], hl)
    assert pb2["roster_confirmed"] is False and "Nordic Partners" in pb2["team_note"] and not pb2.get("standin_for")
    pb3 = _pb(player={"nick": "x", "id": 9}, current_team="Nordic Partners Gaming", team_id=21, recent_maps=[])
    res, pb3 = _run_select(monkeypatch, pb3, [], hl)
    assert pb3["roster_confirmed"] is True and not pb3.get("standin_for")              # same team (name variant): no change


# ---------------------------------------------------------------- 5. HLTV ids with no maps -> BO3.gg fallback
def test_hltv_ids_without_maps_use_the_bo3_fallback(monkeypatch):
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: {"version": "perf-2", "lambda": 26.87, "sd": 7.0,
                                                                  "hltv_player_ids": [1], "player_maps": 0, "newest_map_day": None,
                                                                  "data_quality": "no history - pro average only"})
    monkeypatch.setattr(perf_live, "opp_maps", lambda opp, since: (5, [(739000, 70, 100)] * 30))
    rows = [{"date": f"2026-09-{d:02d}", "kills": 20, "rounds": 24, "headshots": 10, "match_id": d} for d in range(1, 29)]
    pb = {"player": {"nick": "x"}, "current_team": "T", "recent_maps": rows}
    a = argparse.Namespace(prop="kills", opponent="O", match_start=None)
    out = cs2_scout._perf_input_once(pb, a, "12")
    assert out["version"].endswith("-bo3") and out["hltv_player_ids"] == [1] and "420 days" in out["hltv_error"]
    pb["recent_maps"] = []
    out = cs2_scout._perf_input_once(pb, a, "12")
    assert "lambda" not in out and out["error"]                                    # never a pro-average projection


# ---------------------------------------------------------------- 6. qualifier titles + team variants on HLTV's schedule
def test_qualifier_title_is_not_the_main_events_lan(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setitem(hltv._CACHE, ("events_all",), (time.time(), [("PGL Bucharest 2027", True),
                                                                      ("PGL Bucharest 2027 Open Qualifier 1", False),
                                                                      ("PGL Masters Bucharest 2026", True),
                                                                      ("Roman Imperium Cup IX", True)]))
    assert hltv.event_env_from_title("Nordic Partners Gaming  vs ex-RUSTEC (BO3) - PGL Bucharest Open Qualifier #1 Playoffs") == "online"
    assert hltv.event_env_from_title("X vs Y (BO3) - PGL Masters Bucharest 2026 Europe Closed Qualifier") == "online"
    assert hltv.event_env_from_title("A vs B (BO3) - PGL Bucharest 2027 Playoffs") == "lan"
    assert hltv.event_env_from_title("Rebels vs NAVI Junior (BO1) - Roman Imperium Cup Group B") == "lan"


def test_next_match_uses_team_name_variants():
    class Cur:
        def execute(self, sql, params):
            self.p = params

        def fetchone(self):
            return ((None, False, "Nordic Partners", "ex-RUSTEC", "PGL Bucharest 2027 Open Qualifier 1", "bo3", "u")
                    if "nordicpartners" in self.p["t"] else None)
    nm = hltv._next_match(Cur(), "Nordic Partners Gaming ")
    assert nm and nm["opponent"] == "ex-RUSTEC"


# ---------------------------------------------------------------- 9. settlement: exact team names, never an academy
def test_settlement_never_matches_an_academy(monkeypatch):
    rows = [{"hltv_match": 2398822, "opponent": "Inner Circle Academy", "number": n, "kills": 12 * n, "headshots": 5,
             "date": "2026-09-30", "map": "de_x", "rounds": 24, "won": True} for n in (1, 2)]
    monkeypatch.setattr(hltv, "dsn", lambda: "x")

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Con:
        def cursor(self):
            return Cur()

        def close(self):
            pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, n, t: [1])
    monkeypatch.setattr(__import__("picklog"), "_team_confirmed", lambda cur, ids, team: True)
    monkeypatch.setattr(hltv, "_series_rows", lambda *a, **k: list(rows))
    assert hltv.settle_by_day("Joey", "Sangal", "2026-09-30", "Inner Circle") == {"found": False}
    assert hltv.settle_by_day("Joey", "Sangal", "2026-09-30", "Inner Circle Academy")["found"]
    assert not hltv.same_team("NAVI", "NAVI Junior") and hltv.same_team("Chicken Coop Esports", "Chicken Coop")


# ---------------------------------------------------------------- 10. H2H / regional rows carry HLTV's numbers
def test_h2h_rows_use_hltv_numbers_and_hltv_only_meetings(monkeypatch):
    d1 = (date.today() - timedelta(days=30)).isoformat()
    d2 = (date.today() - timedelta(days=60)).isoformat()
    bo3 = [{"match_id": 77, "number": n, "date": d1, "opponent": "Galorys", "kills": v, "headshots": 5, "map": "de_a",
            "won": True, "rounds": 24, "opp_lineup": ["a", "b"], "regional": False, "team": "X"} for n, v in ((1, 14), (2, 9))]
    hl = [{"match_id": "hltv-1", "hltv_match": 1, "number": n, "date": d1, "opponent": "Galorys", "kills": v, "headshots": 5,
           "map": "de_a", "won": True, "rounds": 24, "team": "X", "source": "hltv"} for n, v in ((1, 14), (2, 10))]
    hl += [{"match_id": "hltv-2", "hltv_match": 2, "number": n, "date": d2, "opponent": "Galorys", "kills": 10 + n,
            "headshots": 4, "map": "de_b", "won": False, "rounds": 22, "team": "X", "source": "hltv"} for n in (1, 2)]
    hl += [{"match_id": "hltv-3", "hltv_match": 3, "number": n, "date": d2, "opponent": "Galorys Academy", "kills": 30,
            "headshots": 4, "map": "de_b", "won": False, "rounds": 22, "team": "X", "source": "hltv"} for n in (1, 2)]
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: hl)
    out = cs2_scout._hltv_h2h({}, bo3, "Galorys")
    h = cs2_scout.h2h(out, "Galorys", 20.5)
    assert {s["date"]: s["total"] for s in h["series"]} == {d1: 24, d2: 23}   # HLTV's 14+10, + the HLTV-only meeting, no academy


# ---------------------------------------------------------------- 11. benched players are not tonight's roster
def test_opponent_roster_ignores_benched_and_coaches():
    players = [{"slug": "bsover"}, {"slug": "draken"}, {"slug": "lekr0", "status": 2}, {"slug": "coach", "is_coach": True}]
    assert cs2_scout.active_roster(players) == ["bsover", "draken"]
    rows = [{"match_id": 1, "number": n, "date": "2026-05-15", "team": "X", "opponent": "Johnny Speeds", "map": "m",
             "kills": 10, "won": True, "opp_lineup": ["heap", "jocab", "lekr0", "nawwk", "sapec"]} for n in (1, 2)]
    tonight = [{"slug": x} for x in ("bsover", "draken", "hampus", "rack", "svedjehed")] + \
              [{"slug": x, "status": 2} for x in ("lekr0", "nawwk", "heap")]
    s = cs2_scout.h2h(rows, "Johnny Speeds", opp_roster=cs2_scout.active_roster(tonight))
    assert s["series"][0]["same_roster"] is False and s["series"][0]["roster_overlap"] == 0


# ---------------------------------------------------------------- 11b. last-10 reaches older BO3.gg series
def test_last10_reaches_older_bo3_series(monkeypatch):
    day = lambda n: (date.today() - timedelta(days=n)).isoformat()
    recent = [{"match_id": i, "number": 1, "date": day(i), "opponent": "o", "kills": 10, "headshots": 4, "rounds": 13, "won": True}
              for i in range(1, 8)]                                           # BO1s: no Maps 1-2 series
    older = [{"match_id": 100 + i, "number": n, "date": day(100 + i), "opponent": "o", "kills": 15, "headshots": 4, "rounds": 24,
              "won": True} for i in range(12) for n in (1, 2)]
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [])
    pb = {"recent_maps": recent, "_older_maps": older, "player": {"nick": "x"}}
    rows = cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365)
    ok = cs2_scout._first_series(cs2_scout._usable_series(rows, "kills", 2), cs2_scout.RECENT_MATCHES)
    assert len({r["match_id"] for r in ok}) == cs2_scout.RECENT_MATCHES
    assert cs2_scout._form_rows(pb, "kills") == recent                         # the plain last-10 read is unchanged


# ---------------------------------------------------------------- 12. forfeited maps never count
def test_map_rows_skip_a_forfeited_map():
    class B:
        def game_players_many(self, ids):
            pass

        def game_players(self, gid):
            return [{"steam_profile": {"player_id": 5, "player": {"slug": "f"}}, "team_clan": {"team_id": 1},
                     "kills": 6 if gid == 2 else 17, "headshots": 4, "win": True}]
    mt = {"id": 9, "status": "finished", "team1_id": 1, "team2_id": 2, "team1": {"name": "A"}, "team2": {"name": "B"},
          "games": [{"id": 1, "number": 1, "state": "done", "map_name": "de_ancient", "winner_clan_score": 13, "loser_clan_score": 7},
                    {"id": 2, "number": 2, "state": "done", "map_name": "de_anubis", "winner_clan_score": 1, "loser_clan_score": 0}]}
    rows = cs2_scout.map_rows(B(), 5, [mt], lambda m, g: True)
    assert [r["number"] for r in rows] == [1]                         # map 2 (1 round) is a forfeit
    assert cs2_scout.recent_form(rows, 0.8, 20.5)["matches"] == 0      # so no Maps 1-2 result


def test_recheck_lists_hltv_voids_for_review(monkeypatch):
    import picklog

    class Con:
        def execute(self, *a):
            class R:
                def fetchall(self_):
                    return [{"id": 1, "player": "foggers", "prop": "hs", "line": 9.5, "pick": "More", "actual": 10, "hit": 1,
                             "status": "settled", "scope": "12"}]
            return R()

        def close(self):
            pass
    monkeypatch.setattr(picklog, "_db", lambda: Con())
    monkeypatch.setattr(picklog, "_col", lambda r, k: r.get(k))
    monkeypatch.setattr(picklog, "_hltv_totals", lambda r: ("void", None, None, None))
    out = picklog.recheck_with_hltv()
    assert out and out[0]["review"] and out[0]["id"] == 1


# ---------------------------------------------------------------- 13. an opponent id without maps is not 'known'
def test_opponent_without_maps_is_average_and_labelled(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(perf_live, "player_maps", lambda n, t, s: ([1], [(739000 - i, 20, 24, 10, False, True) for i in range(40)]))
    monkeypatch.setattr(perf_live, "opp_maps", lambda o, s: (12164, []))
    out = perf_live.projection("Crisp", "Chicken Coop", "Zomblers", False, day=739100)
    assert out["opp_known"] is False and "opponent has no HLTV maps - average opponent" in out["data_quality"]


# ---------------------------------------------------------------- 14. HLTV numbers: team-name variants, no false mismatch
def test_player_numbers_team_variants(monkeypatch):
    today = date.today()

    def row(pid, team, d):
        return (pid, "Drop", team, today - timedelta(days=d), None, "total", 1.0, 0.1, 80.0, 70.0, 20, 15, "hltv")
    rows = [row(22794, "Chicken Coop", d) for d in range(1, 30)] + [row(19750, "BESTIA", d) for d in range(1, 60)]

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a):
            pass

        def fetchall(self):
            return rows

    class Con:
        def cursor(self):
            return Cur()

        def close(self):
            pass
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    out = hltv._player_numbers("Drop", "Chicken Coop Esports", None)
    assert out["hltv_id"] == 22794 and "team_mismatch" not in out             # not the namesake with more maps


# ---------------------------------------------------------------- 8 / 31. stale HLTV history is flagged (context only)
def test_stale_hltv_history_is_flagged_not_regraded():
    g = {"projection_source": "perf-2", "flags": [], "p_more": 0.3, "units": 0}
    pf = {"version": "perf-2", "newest_map_day": date(2026, 3, 16).toordinal()}
    pb = {"recent_maps": [{"date": f"2026-09-{d:02d}", "match_id": d} for d in (1, 5, 9)], "hltv": {"team": "Ground Zero"},
          "current_team": "Rooster"}
    cs2_scout._hltv_staleness(g, pf, pb)
    assert g["hltv_ends"] == "2026-03-16" and g["flags"][0].startswith("HLTV STALE: HLTV history ends 2026-03-16 (for Ground Zero)")
    assert (g["p_more"], g["units"]) == (0.3, 0)                               # context only: the grade is untouched
    assert comparison.source_label(g) == "HLTV (ends 2026-03-16)"
    assert comparison.data_status(g, {"state": "GRADE"})[0] == "verify"
    assert server._public_flag(g["flags"][0]).startswith("HLTV history ends 2026-03-16")
    assert server.has_warning(g)
    fresh = {"projection_source": "perf-2", "flags": []}
    cs2_scout._hltv_staleness(fresh, {"version": "perf-2", "newest_map_day": date(2026, 9, 8).toordinal()}, pb)
    assert not fresh["flags"]                                                  # 1 newer match, 1 day apart: nothing


# ---------------------------------------------------------------- 25. outage wording on the card
def test_outage_concern_names_the_outage():
    g = {"flags": ["OLD PROJECTION: the performance model is unavailable here (HLTV database unreachable right now (timeout)) "
                   "- graded on the old pipeline"], "lambda_final": 20.0, "line": 20.5, "p_more": 0.45, "p_less": 0.55,
         "dist": {"kind": "normal", "lam": 20.0, "sd": 6.0}, "sd": 6.0}
    pnl = comparison.panel({"grade": g, "verdict": {"side": "Less", "p": 0.55}}, [])
    texts = [x["text"] for x in pnl["evidence_against"] if x["basis"] == "data"]
    assert texts and "unreachable" in texts[0] and not any("no HLTV data" in t for t in texts)


# ---------------------------------------------------------------- 37. the model's spread never leaves the server
def test_comparison_panel_has_no_spread():
    g = {"flags": [], "lambda_final": 20.0, "line": 20.5, "p_more": 0.45, "p_less": 0.55,
         "dist": {"kind": "normal", "lam": 20.0, "sd": 6.0}, "sd": 6.0}
    pnl = comparison.panel({"grade": g, "verdict": {"side": "Less", "p": 0.55}}, [])
    assert "sd" not in pnl["ours"] and all("sd" not in x for x in pnl["internal"])


# ---------------------------------------------------------------- 26. match players: grading lines, BO3.gg projections
def test_match_players_show_pending_lines_and_board_projections(monkeypatch):
    import underdog
    start = "2026-10-12T04:00:00Z"
    graded = [{"nick": "foggers", "player": "foggers", "team": "Rooster", "team_name": "Rooster", "opponent": "LFO 8", "prop": "kills",
               "scope": "12", "line": 30.5, "side": "Less", "p": 0.72, "grade": "A-", "units": 0.5, "proj": 26.1, "start": start,
               "source": "HLTV", "state": "GRADE", "book": "underdog"},
              {"nick": "chelleos", "player": "chelleos", "team": "Rooster", "team_name": "Rooster", "opponent": "LFO 8", "prop": "hs",
               "scope": "12", "line": 12.5, "side": "More", "p": 0.56, "grade": "LEAN", "units": 0.0, "proj": 13.4, "start": start,
               "source": "BO3.gg", "state": "GRADE", "book": "prizepicks"}]
    monkeypatch.setattr(server, "board_payload", lambda book="underdog": {"rows": [r for r in graded if r["book"] == book]})
    monkeypatch.setattr(underdog, "_BOARD", {"at": 1, "rows": [], "error": None})
    monkeypatch.setattr(underdog, "board", lambda wait=False: {"at": 1, "error": None, "rows": [
        {"player": "damyo", "team": "Rooster", "opponent": "LFO 8", "prop": "kills", "scope": "12", "line": 31.5, "start": start}]})

    class B:
        def team(self, slug):
            return {"players": [{"nickname": n} for n in ("foggers", "damyo", "chelleos")]} if slug == "rooster" else {"players": []}

        def close(self):
            pass
    monkeypatch.setattr(server, "BO3", B)
    monkeypatch.setattr(perf_live, "projection", lambda nick, *a, **k: {"lambda": 27.6, "data_quality": "ok", "player_maps": 30}
                        if nick == "damyo" else {"lambda": 26.9, "player_maps": 0, "data_quality": "no history - pro average only"})
    t = next(x for x in server.match_compare("rooster", "lfo", "Rooster", "LFO 8", start, "12")["teams"] if x["team"] == "Rooster")
    p = {e["nick"]: e for e in t["players"]}
    assert p["damyo"]["kills"]["line"] == 31.5 and p["damyo"]["kills"]["pending"] and p["damyo"]["kills"]["proj"] == 27.6
    assert p["chelleos"]["hs"]["proj"] == 13.4 and p["chelleos"]["hs"]["source"] == "BO3.gg"   # the board's BO3.gg projection
    assert p["chelleos"]["kills"]["proj"] is None and p["chelleos"]["kills"]["note"] == "no HLTV history"   # never a pro average
