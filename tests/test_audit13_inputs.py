"""2026-10-13 audit, grading data inputs: regression tests (each one failed before its fix). No network: every upstream call
(BO3.gg, the HLTV database, Polymarket, CS API) is stubbed."""
import argparse
import asyncio
import copy
import threading
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bo3
import cs2_scout
import hltv
import hltv_store
import kvstore
import perf_live
import roles


def _clock(monkeypatch):
    """The wall clock, movable forward: off[0] seconds are added to time.time() everywhere."""
    real, off = time.time, [0.0]
    monkeypatch.setattr(time, "time", lambda: real() + off[0])
    return off


class _Con:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def close(self):
        pass


class _Cur:
    connection = SimpleNamespace(rollback=lambda: None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.q, self.p = sql, params or {}

    def fetchall(self):
        return []

    def fetchone(self):
        return None


def _base_pb(**k):
    pb = {"player": {"id": 7, "slug": "tester", "nick": "tester"}, "current_team": "Mine", "team_slug": "mine", "team_id": 1,
          "overall": {"maps_played": 40, "kpr": 0.70, "hs_pct": 50.0, "window": "last_3m", "days": 90, "dpr": 0.65, "rating": 1.0,
                      "form_windows": {"3m": {"maps": 40, "kpr": 0.70, "hs_pct": 50.0}, "6m": {"maps": 60, "kpr": 0.70, "hs_pct": 50.0},
                                       "12m": {"maps": 80, "kpr": 0.70, "hs_pct": 50.0}}},
          "overall_env": {"lan": {"form_windows": {}, "kpr": 0.90, "hs_pct": 40.0, "maps": 15},
                          "online": {"form_windows": {}, "kpr": 0.65, "hs_pct": 52.0, "maps": 65}},
          "recent_maps": [], "_older_maps": [], "_match_env": {}, "per_map": [], "map_table": {},
          "teammate_ratings": {"a": 1.0, "b": 0.9, "c": 0.95, "d": 1.05}, "teammates": ["a", "b", "c", "d"],
          "is_igl": False, "sniping_equiv": None, "awp_note": "", "is_standin": False, "roster_confirmed": True,
          "on_profile_roster": True, "kast": 70.0, "trade_pr": None, "_rf_kpr": 0.70, "days_since_last_map": 3,
          "recent_form": {}, "_opp_rows": []}
    pb.update(k)
    return pb


def _args(**k):
    a = argparse.Namespace(prop="kills", line=40.5, line_type="standard", scope="12", team_ml=None, opp_ml=None, opponent="Foe",
                           light=True, web=True)
    for n, v in k.items():
        setattr(a, n, v)
    return a


@pytest.fixture
def offline(monkeypatch):
    """No HLTV database, no CS API: finish_line runs on the stubbed inputs only."""
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    import csapi
    monkeypatch.setattr(csapi, "player_check_async", lambda *a, **k: {})


def _series(mid, day, opp, maps, kills, hltv_row=False, team="X"):
    out = []
    for n, ((mp, rounds, won), k) in enumerate(zip(maps, kills), 1):
        r = {"match_id": mid, "number": n, "date": day, "opponent": opp, "team": team, "map": mp, "rounds": rounds, "won": won,
             "kills": k, "headshots": k // 2}
        if hltv_row:
            r.update(source="hltv", hltv_match=int(str(mid).split("-")[1]), env="lan")
        out.append(r)
    return out


@pytest.fixture(autouse=True)
def tables_not_loaded(monkeypatch):
    """matchid's team tables are not loaded unless a test says so (_aliases) - whatever another test module left behind."""
    import matchid
    monkeypatch.setattr(matchid, "ready", lambda: False)


def _aliases(monkeypatch, table):
    """matchid's canonical team keys for these spellings (normalised name -> 'team:<id>'), as if its tables were loaded."""
    import matchid
    monkeypatch.setattr(matchid, "ready", lambda: True)
    monkeypatch.setattr(matchid, "team_key", lambda n: table.get(hltv._norm(n)))


# ---------------------------------------------------------------- C1: one series, one total (HLTV / BO3.gg twins)
def test_c1_spellings_merge_only_through_a_verified_alias(monkeypatch):
    d = (date.today() - timedelta(days=3)).isoformat()
    maps = [("de_nuke", 24, False), ("de_mirage", 22, False)]
    b3 = _series(555, d, "FaZe Clan", maps, (14, 13))
    hl = _series("hltv-999", d, "FaZe", maps, (15, 13), hltv_row=True)
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [dict(r) for r in hl])
    pb = {"recent_maps": b3, "_older_maps": [], "player": {"nick": "x"}}
    form = lambda: cs2_scout.recent_form(cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365), 0.7, 26.5)
    assert form()["matches"] == 2              # the same scoreline under two names proves nothing: not merged
    _aliases(monkeypatch, {"faze": "team:6667", "fazeclan": "team:6667", "x": "team:1"})     # the verified alias
    rf = form()
    assert rf["matches"] == 1 and rf["series"][0]["total"] == 28 and rf["series"][0]["source"] == "HLTV"
    assert (rf["overs"], rf["decided"]) == (1, 1)
    h = cs2_scout.h2h(cs2_scout._hltv_h2h({}, [dict(r) for r in b3], "FaZe Clan"), "FaZe Clan", 26.5)
    assert [s["total"] for s in h["series"]] == [28]                       # one meeting, HLTV's numbers


def test_c1_identical_rematches_are_never_guessed(monkeypatch):
    d = (date.today() - timedelta(days=3)).isoformat()
    same = [("de_nuke", 24, True), ("de_mirage", 26, True)]                # two meetings, the same scores
    b3 = _series(1, d, "Rival", same, (20, 21)) + _series(2, d, "Rival", same, (11, 12))
    hl = _series("hltv-1", d, "Rival", same, (20, 22), hltv_row=True) + _series("hltv-2", d, "Rival", same, (11, 13), hltv_row=True)
    pb = {"recent_maps": b3, "_older_maps": [], "player": {"nick": "x"}}
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [dict(r) for r in hl])
    rf = cs2_scout.recent_form(cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365), 0.7, 26.5)
    assert sorted(s["total"] for s in rf["series"]) == [24, 42] and {s["source"] for s in rf["series"]} == {"HLTV"}
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [dict(r) for r in hl[:2]])
    rf = cs2_scout.recent_form(cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365), 0.7, 26.5)
    assert sorted(s["total"] for s in rf["series"]) == [23, 41, 42]        # HLTV has one: which BO3.gg one? not guessed


def test_c1_orientation_and_teams_must_both_match(monkeypatch):
    d = (date.today() - timedelta(days=3)).isoformat()
    maps = [("de_nuke", 24, True), ("de_mirage", 22, False)]
    # BO3.gg lists his team second (home / away swapped): map_rows orients the row to his team
    class B:
        def game_players_many(self, ids):
            pass

        def game_players(self, gid):
            return [{"steam_profile": {"player_id": 5, "player": {"slug": "me"}}, "team_clan": {"team_id": 2}, "kills": 17,
                     "headshots": 8, "win": gid == 11}]
    mt = {"id": 9, "status": "finished", "start_date": d + "T15:00:00Z", "team1_id": 1, "team2_id": 2,
          "team1": {"name": "Rival"}, "team2": {"name": "X"},
          "games": [{"id": 11, "number": 1, "state": "done", "map_name": "de_nuke", "winner_clan_score": 13, "loser_clan_score": 11},
                    {"id": 12, "number": 2, "state": "done", "map_name": "de_mirage", "winner_clan_score": 13, "loser_clan_score": 9}]}
    b3 = cs2_scout.map_rows(B(), 5, [mt], lambda m, g: True)
    assert {(r["team"], r["opponent"]) for r in b3} == {("X", "Rival")}
    hl = _series("hltv-3", d, "Rival", maps, (18, 17), hltv_row=True)
    assert cs2_scout._twins({"hltv-3": hl}, {9: b3}) == {9: "hltv-3"}
    flipped = [dict(r, team="Rival", opponent="X") for r in hl]            # his side the other way round: not the same
    assert cs2_scout._twins({"hltv-3": flipped}, {9: b3}) == {}
    other_day = [dict(r, date=(date.today() - timedelta(days=4)).isoformat()) for r in hl]
    assert cs2_scout._twins({"hltv-3": other_day}, {9: b3}) == {}
    swapped = [dict(r, number=3 - r["number"]) for r in hl]                 # maps in another order: not proven
    assert cs2_scout._twins({"hltv-3": swapped}, {9: b3}) == {}


def test_c1_two_meetings_never_merge(monkeypatch):
    d = (date.today() - timedelta(days=3)).isoformat()
    d0 = (date.today() - timedelta(days=4)).isoformat()
    first = [("de_anubis", 36, True), ("de_nuke", 19, True)]
    second = [("de_anubis", 19, False), ("de_cache", 24, True)]
    # upper-bracket final and grand final vs the same team on one day; HLTV has only the second one so far
    b3 = _series(1, d, "The Last Resort", first, (20, 15)) + _series(2, d, "The Last Resort", second, (12, 18))
    hl = _series("hltv-7", d, "The Last Resort", second, (13, 18), hltv_row=True)
    # a rematch a day apart: each source has only one of them
    b3 += _series(3, d0, "Yawara", [("de_anubis", 21, True), ("de_nuke", 17, True)], (22, 9))
    hl += _series("hltv-8", d, "Yawara", [("de_anubis", 19, False), ("de_mirage", 24, False)], (11, 12), hltv_row=True)
    # an academy the same day: another team
    b3 += _series(4, d, "Spirit Academy", [("de_dust2", 24, True), ("de_train", 20, True)], (25, 25))
    hl += _series("hltv-9", d, "Spirit", [("de_inferno", 22, False), ("de_ancient", 23, False)], (5, 5), hltv_row=True)
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [dict(r) for r in hl])
    pb = {"recent_maps": b3, "_older_maps": [], "player": {"nick": "x"}}
    rf = cs2_scout.recent_form(cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365), 0.7, 26.5)
    assert sorted(s["total"] for s in rf["series"]) == [10, 23, 31, 31, 35, 50]
    h = cs2_scout.h2h(cs2_scout._hltv_h2h({}, [r for r in b3 if r["match_id"] in (1, 2)], "The Last Resort"), "The Last Resort")
    assert sorted(s["total"] for s in h["series"]) == [31, 35]            # HLTV's 13+18 on the second meeting only


# ---------------------------------------------------------------- C2: an empty BO3.gg scoreboard is never kept for good
def test_c2_empty_scoreboard_is_asked_again(monkeypatch, tmp_path):
    monkeypatch.setattr(kvstore, "DB_PATH", tmp_path / "cache.db")
    monkeypatch.setattr(kvstore, "_local", threading.local())
    monkeypatch.setattr(bo3, "_MEM", {})
    monkeypatch.setattr(bo3, "_TMEM", {})
    monkeypatch.setattr(bo3, "_next_slot", [0.0])
    off = _clock(monkeypatch)
    published, calls = {"v": False}, []
    full = [{"steam_profile": {"player_id": i, "player": {"slug": f"p{i}"}}, "team_clan": {"team_id": 1 + i // 5}, "kills": 15}
            for i in range(10)]

    async def raw(endpoint, params=None):
        calls.append(endpoint)
        return copy.deepcopy(full) if published["v"] else []      # map 'done', players_stats not published yet
    b = bo3.BO3(ttl_hours=12, background=True)
    b._raw = raw
    try:
        assert b.game_players(4242) == [] and len(calls) == 1
        assert b.game_players(4242) == [] and len(calls) == 1         # asked again later, not on every read
        published["v"] = True
        off[0] += 31 * 60
        assert len(b.game_players(4242)) == 10 and len(calls) == 2    # stats published: read, not the empty answer
        b2 = bo3.BO3(ttl_hours=12, background=True)
        b2._raw = raw
        try:
            off[0] += 2 * 86400
            assert len(b2.game_players(4242)) == 10 and len(calls) == 2   # a full scoreboard is kept for good
            bo3._MEM.clear()
            assert len(b2.game_players(4242)) == 10 and len(calls) == 2   # ... on disk too
        finally:
            b2.close()
    finally:
        b.close()


# ---------------------------------------------------------------- C3: tonight's LAN / online label is the graded match's
def _sched_db(monkeypatch, sched):
    class Cur(_Cur):
        def _rows(self):
            return [r for r in sched if hltv._norm(r[2]) in self.p["t"] or hltv._norm(r[3]) in self.p["t"]]

        def fetchall(self):
            if "GROUP BY mo.lan" in self.q:
                return [(True, 300, 480, 20), (False, 900, 1440, 60)]
            if "FROM upcoming_matches" in self.q:
                return self._rows()
            return []

        def fetchone(self):
            if "FROM upcoming_matches" in self.q:
                rows = self._rows()
                return rows[0] if rows else None
            if "FROM events" in self.q:
                e = self.p["e"]
                return (True,) if "biglan" in e else (False,) if "onlinecup" in e else None
            return None
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_CACHE", {})
    monkeypatch.setattr(hltv, "_connect", lambda: _Con(Cur()))
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [1])
    monkeypatch.setattr(hltv, "_team_id", lambda cur, name: None)
    for name in ("_prop_maps", "_eco", "_last5", "_h2h", "_ranking", "_rank_band", "_backup"):
        monkeypatch.setattr(hltv, name, lambda *a, **k: None)


def test_c3_tonight_comes_from_the_graded_match(monkeypatch):
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=20)
    # his own team's previous match is still live at a LAN; tonight he stands in for another team in an online cup
    sched = [(now - timedelta(hours=2), True, "Mine", "Other", "Big LAN Finals 2026", "bo3", "u1"),
             (start, False, "Stand Team", "Foe", "Online Cup 12", "bo3", "u2")]
    _sched_db(monkeypatch, sched)
    pb = {"player": {"nick": "tester"}, "current_team": "Mine", "playing_for": "Stand Team", "recent_maps": [], "per_map": []}
    args = SimpleNamespace(opponent="Foe", match_start=start.isoformat())
    ctx = cs2_scout._hltv_context(pb, args)
    assert ctx["environment"]["tonight"] == "online" and ctx["environment"]["event"] == "Online Cup 12"
    res = {"hltv_context": ctx, "next_match": {"start": start.isoformat(), "opponent": "Foe", "event": "Online Cup #12",
                                               "event_type": "online"}}
    assert cs2_scout._environment(res, pb)["tonight"] == "online"


def test_c3_another_match_never_labels_tonight(monkeypatch):
    now = datetime.now(timezone.utc)
    start = now + timedelta(hours=20)
    # the team's next HLTV match is vs someone else (LAN), and its meeting with tonight's opponent is two days later
    sched = [(now + timedelta(hours=3), False, "Mine", "Other", "Big LAN Finals 2026", "bo3", "u1"),
             (start + timedelta(days=2), False, "Mine", "Foe", "Big LAN Finals 2026", "bo3", "u3")]
    _sched_db(monkeypatch, sched)
    pb = {"player": {"nick": "tester"}, "current_team": "Mine", "recent_maps": [], "per_map": []}
    ctx = cs2_scout._hltv_context(pb, SimpleNamespace(opponent="Foe", match_start=start.isoformat()))
    assert not ctx.get("transient") and ctx["environment"]["tonight"] is None and ctx["environment"]["lan"]["maps"] == 20
    res = {"hltv_context": ctx, "next_match": {"start": start.isoformat(), "opponent": "Foe", "event": "Online Cup #12",
                                               "event_type": "online"}}
    env = cs2_scout._environment(res, pb)
    assert env["tonight"] == "online" and env["source"] == "BO3.gg event type"


def test_c3_blip_keeps_tonight_transient(monkeypatch, offline):
    """Unchanged rule: HLTV's own label lost to a blip makes the grade transient - BO3.gg's event type never stands in."""
    monkeypatch.setattr(cs2_scout, "_hltv_context", lambda pb, args, fresh=False: {"found": True, "transient": True,
                                                                                    "environment": None})
    import hltv as h
    monkeypatch.setattr(h, "allow_probe", lambda: False)
    pb = _base_pb()
    res = {"player_block": pb, "next_match": {"start": "2026-10-20T15:00:00Z", "opponent": "Foe", "bo": 3, "event": "Cup",
                                              "event_type": "online", "maps": []}}
    cs2_scout.finish_line(res, _args(line=30.5))
    assert pb["_env_transient"] and res["grader_inputs"]["perf"]["transient"]


# ---------------------------------------------------------------- C4: last 10 in tonight's environment reaches a year back
@pytest.mark.parametrize("split", [True, False])           # with / without a BO3.gg LAN / online split to blend
def test_c4_environment_last10_reaches_back_a_year(monkeypatch, offline, split):
    today = date.today()
    hl = []

    def series(i, d, env, kills):
        return [{"match_id": f"hltv-{i}", "hltv_match": i, "date": d.isoformat(), "number": n, "map": "de_nuke", "kills": kills,
                 "headshots": 10, "rounds": 24, "won": True, "opponent": f"Team{i}", "team": "Mine", "source": "hltv", "env": env,
                 "opponent_id": i} for n in (1, 2)]
    for i in range(45):                                   # a busy online season: 45 series in ~100 days
        hl += series(i, today - timedelta(days=2 + 2 * i), "online", 18)
    for j in range(6):                                    # his 6 LAN series, 5-8 months ago
        hl += series(100 + j, today - timedelta(days=150 + 20 * j), "lan", 25)
    monkeypatch.setattr(hltv, "recent_series", lambda nick, team=None, since=None: [dict(r) for r in hl if r["date"] >= since])
    pb = _base_pb() if split else _base_pb(overall_env={"lan": {"kpr": None, "maps": 0}, "online": {"kpr": None, "maps": 0}})
    res = {"player_block": pb, "next_match": {"start": (today + timedelta(days=1)).isoformat() + "T15:00:00Z", "opponent": "Foe",
                                              "bo": 3, "event": "Big LAN", "event_type": "lan", "maps": []}}
    cs2_scout.finish_line(res, _args())
    rf = pb["recent_form"]
    assert rf["env"] == "lan" and rf["matches"] == 6 and (rf["overs"], rf["decided"]) == (6, 6)
    assert pb["_env"]["form_from"] == "lan" and res["grader_inputs"]["recent"] == {"n": 6, "overs": 6}


# ---------------------------------------------------------------- C5: namesakes - exact team names, never a substring
def test_c5_namesake_is_picked_by_exact_team_name(monkeypatch):
    prof = {"kye-academy": {"team": {"name": "Spirit Academy"}}, "kye": {"team": {"name": "Team Spirit"}},
            "jba-og": {"team": {"name": "Dogs Gaming"}}, "jba": {"team": {"name": "OG"}}}
    b = SimpleNamespace(player=lambda slug: prof[slug])
    monkeypatch.setattr(cs2_scout, "_slate_raw", lambda b, h=36: [])
    kye = [{"slug": "kye-academy", "nickname": "kye"}, {"slug": "kye", "nickname": "kye"}]
    jba = [{"slug": "jba-og", "nickname": "JBa"}, {"slug": "jba", "nickname": "JBa"}]
    assert cs2_scout._pick_namesake(b, kye, "Spirit") == "kye"
    assert cs2_scout._pick_namesake(b, kye, "Spirit Academy") == "kye-academy"
    assert cs2_scout._pick_namesake(b, jba, "OG") == "jba"
    assert cs2_scout._pick_namesake(b, jba, "Spir") is None                 # no prefix guesses either


def test_c5_awp_role_never_takes_the_namesakes_team(monkeypatch):
    rows = [dict(team="Spirit", awp_share=0.42, kills=600, end="2026-10-12", roles=[], team_match=False, wiki=None, updated=None,
                 igl=False, awper=None),
            dict(team="Spirit Academy", awp_share=0.03, kills=450, end="2026-10-12", roles=[], team_match=False, wiki=None,
                 updated=None, igl=False, awper=None)]
    monkeypatch.setattr(roles, "_HLTV", {"at": time.time(), "rows": {"kye": rows}})
    assert roles.awp_role("kye", "Spirit Academy")[0] is False             # the academy rifler, not the main team's AWPer
    assert roles.awp_role("kye", "Team Spirit")[0] is True
    assert not roles._team_ok("OG", ["Dogs Gaming"]) and not roles._team_ok("Spirit Academy", ["Spirit"])
    assert roles._team_ok("Imperial Esports", ["Imperial"]) and roles._team_ok("ex-Imperial", ["x"])


# ---------------------------------------------------------------- C6: the LAN / online blend runs once per grade
def test_c6_environment_blend_is_idempotent():
    pb = _base_pb(_env={"tonight": "lan"})
    cs2_scout._apply_env(pb)
    o = pb["overall"]
    assert (o["kpr"], o["hs_pct"], o["kpr_all_envs"]) == (0.8, 45.0, 0.7)
    hit = copy.deepcopy(pb)                                # a cached context: finish_line again
    hit["_env"] = {"tonight": "lan"}
    cs2_scout._apply_env(hit)
    assert (hit["overall"]["kpr"], hit["overall"]["hs_pct"], hit["overall"]["kpr_all_envs"], hit["_rf_kpr"]) == (0.8, 45.0, 0.7, 0.8)
    other = copy.deepcopy(hit)                             # tonight unknown on the regrade: his all-environment numbers
    other["_env"] = {"tonight": None}
    cs2_scout._apply_env(other)
    assert (other["overall"]["kpr"], other["overall"]["hs_pct"], other["_rf_kpr"]) == (0.7, 50.0, 0.7)
    assert "kpr_all_envs" not in other["overall"]
    full = _base_pb(_env={"tonight": "lan"})              # 30+ LAN maps: his LAN numbers alone, LAN windows on the panel
    full["overall_env"]["lan"].update(maps=30, form_windows={"3m": {"maps": 30, "kpr": 0.9, "hs_pct": 40.0}})
    cs2_scout._apply_env(full)
    assert (full["overall"]["kpr"], full["overall"]["form_windows_env"]) == (0.9, "lan")
    again = copy.deepcopy(full)                            # the same context, tonight's match online
    again["_env"] = {"tonight": "online"}
    cs2_scout._apply_env(again)
    o = again["overall"]
    assert (o["kpr"], o["hs_pct"], o["kpr_all_envs"], o["hs_all_envs"], o["form_windows_env"]) == (0.65, 52.0, 0.7, 50.0, "online")
    assert o["form_windows"] == {}
    back = copy.deepcopy(full)
    back["_env"] = {"tonight": None}
    cs2_scout._apply_env(back)
    assert back["overall"]["form_windows"] == _base_pb()["overall"]["form_windows"] and "form_windows_env" not in back["overall"]


# ---------------------------------------------------------------- C7: an outage serves the same window's stored answer
def test_c7_outage_after_midnight_serves_the_same_window(monkeypatch):
    off = _clock(monkeypatch)
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_CACHE", {})
    down = {"v": False}

    def connect():
        if down["v"]:
            raise hltv.Unavailable("could not connect to server")
        return _Con(_Cur())
    monkeypatch.setattr(hltv, "_connect", connect)
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [42])
    today = datetime.now(timezone.utc).date()

    def series_rows(cur, ids, since, *a, **k):
        out = []
        for i in range(70):
            d = (today - timedelta(days=5 * i)).isoformat()
            if d >= since:
                out += [{"match_id": f"hltv-{i}", "date": d, "number": n, "kills": 20, "headshots": 10, "rounds": 24} for n in (1, 2)]
        return out
    monkeypatch.setattr(hltv, "_series_rows", series_rows)
    n = lambda rows: len({r["match_id"] for r in rows})
    year = hltv.recent_series("tester", "Mine", (today - timedelta(days=366)).isoformat())     # last-10 / H2H read
    stake = hltv.recent_series("tester", "Mine", (today - timedelta(days=90)).isoformat())     # stake check's 90-day read
    assert n(year) == 70 and n(stake) == 19
    off[0] += 86400                                        # after UTC midnight the window starts move a day; database down
    hltv._CACHE.clear()
    down["v"] = True
    tomorrow = today + timedelta(days=1)
    assert n(hltv.recent_series("tester", "Mine", (tomorrow - timedelta(days=366)).isoformat())) == 70
    assert n(hltv.recent_series("tester", "Mine", (tomorrow - timedelta(days=90)).isoformat())) == 19
    hltv_store.save(("p", "x", "t", "2025-08-17"), 1)      # other keys keep their date-free loose key (perf player maps)
    assert hltv_store.load(("p", "x", "t", "2025-08-19")) == (pytest.approx(time.time(), abs=5), 1)


# ---------------------------------------------------------------- C8: one failed HLTV read never sticks for 30 minutes
def test_c8_failed_hltv_read_is_tried_again(monkeypatch):
    off = _clock(monkeypatch)
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_CACHE", {})
    down = {"v": True}

    def connect():
        if down["v"]:
            raise hltv.Unavailable("statement timeout")
        return _Con(_Cur())
    monkeypatch.setattr(hltv, "_connect", connect)
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [42])
    d = lambda i: (date.today() - timedelta(days=3 + 3 * i)).isoformat()
    good = [{"match_id": f"hltv-{i}", "hltv_match": i, "date": d(i), "number": n, "map": "de_nuke", "kills": 20, "headshots": 10,
             "rounds": 25, "won": True, "opponent": f"T{i}", "team": "Mine", "source": "hltv", "env": "online"}
            for i in range(8) for n in (1, 2)]
    monkeypatch.setattr(hltv, "_series_rows", lambda cur, ids, since, *a, **k: [dict(r) for r in good if r["date"] >= since])
    pb = {"player": {"nick": "hltvonly"}, "current_team": "Mine", "recent_maps": [], "_older_maps": [],
          "overall": {"maps_played": 0, "kpr": None, "hs_pct": None, "window": "last_12m", "days": 365, "form_windows": {}}}
    cs2_scout._hltv_only_overall(pb)                       # the HLTV read fails (one statement timeout)
    assert pb["overall"]["kpr"] is None and not pb.get("_hltv_overall_tried")
    down["v"] = False
    off[0] += hltv.FAIL_TTL + 1
    cached = copy.deepcopy(pb)                             # the next grade on the cached context
    cs2_scout._hltv_only_overall(cached)
    assert cached["overall"]["kpr"] == 0.8 and cached["overall"]["maps_played"] == 16 and cached["_hltv_overall_tried"]


# ---------------------------------------------------------------- C9: opponents' VRS ranks by exact team names
def test_c9_rank_band_by_exact_names(monkeypatch):
    monkeypatch.setattr(hltv, "team_ranks", lambda: {"spirit": 3, "mouz": 5, "fnatic": 14, "og": 12, "vitality": 1})
    pb = {"_opp_rank": 6, "_env": {}, "_opp_rows": [
        ("Spirit Academy", 14, 24, "online"), ("MOUZ NXT", 13, 24, "online"), ("Fnatic Rising", 12, 22, "online"),
        ("Dogs Esports", 11, 21, "online"), ("Prodigy", 10, 21, "online"), ("Team Vitality", 20, 24, "online"),
        ("Random Five", 22, 24, "online")]}
    rb = cs2_scout._bo3_rank_band(pb)
    assert rb["teams"] == ["Team Vitality"] and rb["maps"] == 1


# ---------------------------------------------------------------- C15: the projection's opponent through the alias table
def test_c15_opponent_alias_resolves_for_the_projection(monkeypatch):
    import matchid
    monkeypatch.setattr(perf_live, "_CACHE", {})
    monkeypatch.setattr(hltv, "dsn", lambda: "x")

    class Cur(_Cur):
        def fetchall(self):
            return [(date(2026, 9, 1), 70, 24)] if "FROM matches t" in self.q else []
    monkeypatch.setattr(hltv, "_connect", lambda: _Con(Cur()))
    monkeypatch.setattr(matchid, "ready", lambda: True)
    monkeypatch.setattr(matchid, "team_key", lambda n: "team:4411" if hltv._norm(n) == "nip" else None)
    tid, rows = perf_live.opp_maps("NIP", "2025-08-01")
    assert tid == 4411 and rows == [(date(2026, 9, 1).toordinal(), 70, 24)]
    assert perf_live.opp_maps("Nobody", "2025-08-01") == (None, [])

    def down():
        raise hltv.Unavailable("down")
    monkeypatch.setattr(hltv, "_connect", down)
    with pytest.raises(hltv.Unavailable):                  # a blip is never 'opponent not in HLTV'
        perf_live.opp_maps("Somebody Else", "2025-08-01")


# ---------------------------------------------------------------- A12: benched players never rank his role
class _FakeBO3:
    background = False

    def __init__(self, team_pages, matches_by_player, scoreboards, search, profiles):
        self.team_pages, self.mbp, self.sb, self.search, self.profiles = team_pages, matches_by_player, scoreboards, search, profiles

    def close(self):
        pass

    def search_players(self, q, n=10):
        return {"results": self.search.get(q.lower(), [])}

    def player(self, slug):
        return self.profiles[slug]

    def team(self, slug):
        if slug not in self.team_pages:
            raise LookupError(slug)
        return self.team_pages[slug]

    def player_matches(self, pid, pages=3, ttl=None):
        return {"results": self.mbp.get(pid, [])}

    def game_players_many(self, ids):
        return {}

    def game_players(self, gid, finished=True):
        return self.sb.get(gid, [])


def _sb_row(pid, slug, team_id, kills):
    return {"steam_profile": {"player_id": pid, "steam_id_64": None, "player": {"slug": slug}}, "team_clan": {"team_id": team_id},
            "kills": kills, "death": 15, "assists": 4, "damage": kills * 95, "headshots": kills // 2, "kast": 0.75, "win": True,
            "enemy_clan_name": "Opp", "first_kills": 2, "first_death": 2, "trade_kills": 1}


def test_a12_role_rank_ignores_benched_players(monkeypatch):
    monkeypatch.setitem(cs2_scout.EVENT_TYPES, "slug:ev", "online")
    monkeypatch.setattr(cs2_scout, "window_stats", lambda b, slug, days: {})
    active = [(7, "tester"), (11, "a"), (12, "b"), (13, "c"), (14, "d")]
    kills = {7: 21, 15: 26, 11: 17, 12: 17, 13: 16, 14: 16}
    ms, sb = [], {}
    for i in range(12):
        d = (date.today() - timedelta(days=1 + 3 * i)).isoformat()
        games = []
        for num in (1, 2):
            gid = 1000 * (i + 1) + num
            games.append({"id": gid, "number": num, "map_name": "de_mirage", "state": "done", "winner_clan_score": 13,
                          "loser_clan_score": 10})
            players = active + ([(15, "starbench")] if i >= 4 else [])       # benched 3 weeks ago
            sb[gid] = [_sb_row(pid, sl, 1, kills[pid]) for pid, sl in players[:5] + players[6:]] + \
                      [_sb_row(900 + j, f"o{j}", 2, 15) for j in range(5)]
        ms.append({"id": 50 + i, "slug": f"m{i}", "status": "finished", "start_date": d + "T12:00:00Z", "team1_id": 1, "team2_id": 2,
                   "team1": {"id": 1, "name": "Mine", "slug": "mine"}, "team2": {"id": 2, "name": "Opp", "slug": "opp"},
                   "tournament": {"slug": "ev", "name": "Ev"}, "games": games, "bo_type": 3})
    team_page = {"players": [{"id": pid, "slug": sl, "nickname": sl, "status": 1} for pid, sl in active]
                 + [{"id": 15, "slug": "starbench", "nickname": "starbench", "status": 2},
                    {"id": 99, "slug": "coachy", "nickname": "coachy", "is_coach": True}]}
    prof = {"tester": {"id": 7, "nickname": "tester", "team": {"name": "Mine", "slug": "mine", "id": 1}, "joined_team_at": "2025-01-01"}}
    b = _FakeBO3({"mine": team_page}, {7: ms}, sb, {"tester": [{"slug": "tester", "nickname": "tester"}]}, prof)
    pb = cs2_scout.run_player(b, "tester", argparse.Namespace(bo3_slug=None, team=None, maps=None, line=None, prop="kills",
                                                               team_hint=None))
    assert pb["benched"] == ["starbench"]                  # still shown as benched
    assert sorted(pb["teammates"]) == ["a", "b", "c", "d"] and sorted(pb["teammate_ratings"]) == ["a", "b", "c", "d"]


# ---------------------------------------------------------------- A13: a Polymarket-found match keeps its format
def test_a13_polymarket_found_match_keeps_its_format(monkeypatch):
    start = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pb = {"player": {"id": 7, "nick": "tester", "slug": "tester"}, "team_id": 1, "current_team": "Mine", "team_slug": None,
          "recent_maps": [], "on_profile_roster": True, "roster_confirmed": True, "is_standin": False, "_matches": [], "per_map": []}
    monkeypatch.setattr(cs2_scout, "run_player", lambda b, name, args: pb)
    monkeypatch.setattr(cs2_scout, "upcoming_matches", lambda b, tid: [])
    monkeypatch.setattr(cs2_scout, "_hltv_schedule", lambda names, opp=None: [])
    monkeypatch.setattr(cs2_scout, "_hltv_map_prediction", lambda *a, **k: None)
    monkeypatch.setattr(cs2_scout, "match_odds", lambda team, opp=None: {
        "found": True, "team": "Mine", "opponent": "Foe", "title": "Mine vs Foe (BO5) - Grand Final", "start": start})
    import csapi
    import market_odds
    monkeypatch.setattr(market_odds, "select", lambda *a, **k: {"found": False, "provenance": {}})
    monkeypatch.setattr(csapi, "player_check_async", lambda *a, **k: {})

    def offline(*a, **k):
        raise RuntimeError("offline")
    b = SimpleNamespace(background=False, _run=offline, cs2=SimpleNamespace(search_teams=lambda *a: None),
                        game_players_many=lambda ids: {})
    args = argparse.Namespace(cmd="full", name="tester", opponent=None, member_opponent=None, match=None, line=None, team=None,
                              maps=None, faceit_key=None, prop="kills", member_start=None)
    res = {}
    cs2_scout.run(args, res, b, 12)
    assert res["next_match"]["bo"] == 5 and args.match_bo == 5


# ---------------------------------------------------------------- minor: a forfeited map is not a played map
def test_player_rows_skip_a_forfeited_map():
    me = [{"steam_profile": {"player_id": 5}, "kills": 17, "death": 10, "headshots": 8, "kast": 0.7}]
    g1 = {"id": 1, "number": 1, "map_name": "de_ancient", "winner_clan_score": 13, "loser_clan_score": 7}
    g2 = {"id": 2, "number": 2, "map_name": "de_anubis", "winner_clan_score": 1, "loser_clan_score": 0}
    assert [r["number"] for r in cs2_scout.player_rows([(g1, me), (g2, me)], 5)] == [1]


def test_maps123_record_never_takes_an_academy_for_its_main_team():
    import records
    d = (date.today() - timedelta(days=5)).isoformat()
    m3 = [("de_nuke", 24, True), ("de_mirage", 22, False), ("de_inferno", 26, True)]
    b3 = [dict(r, number=n) for n, r in enumerate(_series(1, d, "Spirit Academy", m3, (20, 18, 22)), 1)]
    hl = _series("hltv-5", d, "Spirit", m3, (10, 11, 12), hltv_row=True)
    rec = records.maps123_record({"player": {"nick": "x"}, "recent_maps": b3}, 40.5, hltv_rows=hl)
    assert sorted(s["total"] for s in rec["series"]) == [33, 60]


# ---------------------------------------------------------------- C12 (re-check) and E1 (odds blip on the grade)
def _fake_run(odds):
    def run(args, res, b, ttl):
        res["player_block"] = _base_pb()
        res["next_match"] = {"start": "2026-10-20T15:00:00Z", "slug": "x", "opponent": "Foe", "bo": 3, "event": "Some Cup",
                             "event_type": "online", "maps": [], "opponent_id": None, "opponent_slug": None}
        res["odds"] = copy.deepcopy(odds)
        args.match_start, args.match_bo, args.opponent = res["next_match"]["start"], 3, "Foe"
        cs2_scout.finish_line(res, args)
    return run


def test_c12_every_regrade_rechecks_the_odds(monkeypatch, offline):
    import market_odds
    calls = []
    monkeypatch.setattr(market_odds, "recheck", lambda od, **k: calls.append(1) or (od, {"result": "unchanged"}))
    monkeypatch.setattr(cs2_scout, "_CONTEXT", {})
    monkeypatch.setattr(cs2_scout, "BO3", lambda **k: SimpleNamespace(close=lambda: None, background=k.get("background")))
    monkeypatch.setattr(cs2_scout, "run", _fake_run({"found": False, "provenance": {"query": {"team": "Mine"}}}))
    cs2_scout.grade("tester", 30.5, background=True)       # board: builds the context (re-checked once inside run)
    cs2_scout.grade("tester", 30.5, background=True)       # board: cache hit
    cs2_scout.grade("tester", 30.5)                        # member: cache hit
    assert len(calls) == 3


def test_e1_odds_blip_is_marked_on_the_grade(monkeypatch, offline):
    import market_odds
    blip = {"found": False, "transient": True,
            "provenance": {"query": {"team": "Mine"}, "blip": True, "fallback_reason": "Thunderpick has the match but no current quote"}}
    monkeypatch.setattr(market_odds, "recheck", lambda od, **k: (copy.deepcopy(blip), {"result": "changed"}))
    res = {}
    _fake_run({"found": False, "provenance": {"query": {"team": "Mine"}}})(_args(line=30.5), res, None, 12)
    assert res["grade"]["odds_transient"] is True
    assert res["grade"]["odds_transient_reason"] == "Thunderpick has the match but no current quote"
    monkeypatch.setattr(market_odds, "recheck", lambda od, **k: (od, {"result": "unchanged"}))
    res2 = {}
    _fake_run({"found": False, "provenance": {"query": {"team": "Mine"}, "fallback_reason": "no Thunderpick event"}})(
        _args(line=30.5), res2, None, 12)
    assert "odds_transient" not in res2["grade"] and "odds_transient_reason" not in res2["grade"]


def test_e1_marker_names_the_source_actually_used(monkeypatch, offline):
    import market_odds
    pm = {"found": True, "team_ml": -150, "opp_ml": 130, "team_prob": 0.6, "opp_prob": 0.4, "maps": {}, "transient": True,
          "provenance": {"query": {"team": "Mine"}, "selected": "polymarket", "blip": True, "p_team": 0.6,
                         "fallback_reason": "Thunderpick: event listed, no current quote"}}
    monkeypatch.setattr(market_odds, "recheck", lambda od, **k: (copy.deepcopy(pm), {"result": "changed"}))
    res = {}
    _fake_run({"found": False, "provenance": {"query": {"team": "Mine"}}})(_args(line=30.5), res, None, 12)
    g, gi = res["grade"], res["grader_inputs"]
    assert g["odds_transient"] is True and g["odds_transient_source"] == "polymarket"
    assert gi["odds_provenance"]["selected"] == "polymarket"               # the input stays the fallback's, labelled so
    assert "thunderpick" not in str(g.get("odds_transient_source")).lower()


# ---------------------------------------------------------------- spec 1: teams by canonical identity / verified aliases
def test_team_identity_is_canonical(monkeypatch):
    assert not hltv.same_team("MOUZ", "MOUZ NXT") and hltv.same_team("Team Vitality", "Vitality")    # tables not loaded
    assert not hltv.same_team("NIP", "Ninjas in Pyjamas")                   # no alias available: not assumed
    _aliases(monkeypatch, {"nip": "team:4411", "ninjasinpyjamas": "team:4411", "novaesports": "team:1", "novagaming": "team:2",
                           "mouz": "team:4494", "mouznxt": "team:11176"})
    assert hltv.same_team("NIP", "Ninjas in Pyjamas")                      # a verified alias: the same team
    assert not hltv.same_team("Nova Esports", "Nova Gaming")                # a shared variant, two HLTV teams: never
    assert not hltv.same_team("MOUZ", "MOUZ NXT") and not cs2_scout._same_team("MOUZ", "MOUZ NXT")


def test_canonical_team_id_prefers_a_verified_alias(monkeypatch):
    class Cur(_Cur):
        def fetchone(self):
            return {"apogee": (777,), "betclic": (12916,)}.get(self.p[0]) if isinstance(self.p, tuple) else None
    _aliases(monkeypatch, {"apogeeesports": "team:12916"})
    assert hltv.canonical_team_id(Cur(), "Apogee Esports") == 12916       # the alias, not the team called 'Apogee'
    assert hltv.canonical_team_id(Cur(), "Betclic") == 12916 and hltv.canonical_team_id(Cur(), "Nobody") is None


def _run_stubs(monkeypatch, pb, upcoming):
    monkeypatch.setattr(cs2_scout, "run_player", lambda b, name, args: pb)
    monkeypatch.setattr(cs2_scout, "_hltv_map_prediction", lambda *a, **k: None)
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    import csapi
    import market_odds
    monkeypatch.setattr(market_odds, "select", lambda *a, **k: {"found": False, "provenance": {}})
    monkeypatch.setattr(csapi, "player_check_async", lambda *a, **k: {})

    def offline(*a, **k):
        raise RuntimeError("offline")
    return SimpleNamespace(background=False, _run=offline, cs2=SimpleNamespace(search_teams=lambda *a: None),
                           game_players_many=lambda ids: {}, team_upcoming=lambda tid: {"results": upcoming})


def _raw_match(slug, h, home, away, home_id, away_id):
    st = (datetime.now(timezone.utc) + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"slug": slug, "status": "upcoming", "start_date": st, "team1_id": home_id, "team2_id": away_id, "bo_type": 3,
            "team1": {"id": home_id, "name": home, "slug": home.lower()}, "team2": {"id": away_id, "name": away, "slug": away.lower()},
            "tournament": {"name": "Cup", "slug": None}, "games": []}


def _graded(monkeypatch, upcoming, member_opp, member_start=None):
    monkeypatch.setattr(cs2_scout, "_slate_raw", lambda b, h=36: [])
    pb = {"player": {"id": 7, "nick": "tester", "slug": "tester"}, "team_id": 1, "current_team": "Mine", "team_slug": None,
          "recent_maps": [], "on_profile_roster": True, "roster_confirmed": True, "is_standin": False, "_matches": [], "per_map": []}
    b = _run_stubs(monkeypatch, pb, upcoming)
    args = argparse.Namespace(cmd="full", name="tester", opponent=member_opp, member_opponent=member_opp, match=None, line=None,
                              team=None, maps=None, faceit_key=None, prop="kills", member_start=member_start)
    res = {}
    cs2_scout.run(args, res, b, 12)
    return res, pb


def test_book_opponent_by_canonical_identity(monkeypatch):
    # MOUZ NXT plays first; the book line is for MOUZ - and his team is listed second (home / away swapped)
    up = [_raw_match("m-nxt", 3, "MOUZ NXT", "Mine", 50, 1), _raw_match("m-mouz", 9, "MOUZ", "Mine", 51, 1)]
    res, pb = _graded(monkeypatch, up, "MOUZ")
    assert res["next_match"]["opponent"] == "MOUZ" and [o["opponent"] for o in res["match_options"]] == ["MOUZ"]
    assert pb["playing_for"] == "Mine" and res["next_match"]["opponent_id"] == 51       # oriented to his team
    _aliases(monkeypatch, {"nip": "team:4411", "ninjasinpyjamas": "team:4411", "mine": "team:1"})
    res, pb = _graded(monkeypatch, [_raw_match("m-nip", 5, "Mine", "Ninjas in Pyjamas", 1, 60)], "NIP")
    assert res["next_match"]["opponent"] == "Ninjas in Pyjamas"           # the book's alias spelling: the same team


def test_two_meetings_with_one_team_never_confused(monkeypatch):
    up = [_raw_match("group", 4, "Mine", "Rival", 1, 70), _raw_match("final", 30, "Rival", "Mine", 70, 1)]
    final_start = up[1]["start_date"]
    res, _ = _graded(monkeypatch, up, "Rival", member_start=final_start)
    assert res["next_match"]["slug"] == "final"                            # the meeting at the book's start
    res, _ = _graded(monkeypatch, up, "Rival", member_start=up[0]["start_date"])
    assert res["next_match"]["slug"] == "group"


def test_schedule_and_heads_up_never_take_another_squad(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(hltv, "upcoming_for", lambda names, matcher, hours=72: [{"opponent": "MOUZ NXT"}, {"opponent": "MOUZ"},
                                                                                 {"opponent": "Team MOUZ"}])
    assert [m["opponent"] for m in cs2_scout._hltv_schedule(["Mine"], "MOUZ")] == ["MOUZ", "Team MOUZ"]
    chosen = ("x", 1, "Mine", {"start": (now + timedelta(hours=5)).isoformat(), "opponent": "MOUZ"})
    h = {"start": now + timedelta(hours=1), "team": "Mine", "opponent": "MOUZ NXT"}
    assert cs2_scout._hltv_check_bo3(chosen, [chosen], h)[0] is None        # HLTV's earlier match vs another squad: graded


# ---------------------------------------------------------------- spec 2: roster confirmation from the active lineup
def _bo3_for(lineup_of, team_page, n=12):
    ms, sb = [], {}
    for i in range(n):
        d = (date.today() - timedelta(days=1 + 3 * i)).isoformat()
        games = []
        for num in (1, 2):
            gid = 1000 * (i + 1) + num
            games.append({"id": gid, "number": num, "map_name": "de_mirage", "state": "done", "winner_clan_score": 13,
                          "loser_clan_score": 10})
            sb[gid] = [_sb_row(pid, sl, 1, 18) for pid, sl in lineup_of(i)] + [_sb_row(900 + j, f"o{j}", 2, 15) for j in range(5)]
        ms.append({"id": 50 + i, "slug": f"m{i}", "status": "finished", "start_date": d + "T12:00:00Z", "team1_id": 1, "team2_id": 2,
                   "team1": {"id": 1, "name": "Mine", "slug": "mine"}, "team2": {"id": 2, "name": "Opp", "slug": "opp"},
                   "tournament": {"slug": "ev", "name": "Ev"}, "games": games, "bo_type": 3})
    prof = {"tester": {"id": 7, "nickname": "tester", "team": {"name": "Mine", "slug": "mine", "id": 1}, "joined_team_at": "2025-01-01"}}
    return _FakeBO3({"mine": team_page}, {7: ms}, sb, {"tester": [{"slug": "tester", "nickname": "tester"}]}, prof)


def _pargs(**k):
    return argparse.Namespace(**dict(dict(bo3_slug=None, team=None, maps=None, line=None, prop="kills", team_hint=None), **k))


ACTIVE = [(7, "tester"), (11, "a"), (12, "b"), (13, "c"), (14, "d")]


def _page(extra=()):
    return {"players": [{"id": pid, "slug": sl, "nickname": sl, "status": 1} for pid, sl in ACTIVE] + list(extra)}


def test_lineup_check_cases():
    bench = [{"id": 15, "slug": "x", "status": 2}, {"id": 16, "slug": "y", "status": 2}, {"id": 99, "slug": "coachy", "is_coach": True}]
    page = _page(bench)["players"]
    usual = ["tester", "a", "b", "c", "d"]
    assert cs2_scout._lineup_check(page, 7, usual)[0] is True
    assert cs2_scout._lineup_check(page, 7, ["tester", "a", "x", "y", "d"])[0] is False      # two of them benched since
    assert cs2_scout._lineup_check(page, 7, ["tester", "a", "b", "coachy", "x"])[0] is False # coach / benched never count
    four = [p for p in page if p["slug"] != "d"]                           # 4 active + 2 benched + a coach on the page
    assert cs2_scout._lineup_check(four, 7, usual)[0] is None              # the roster isn't known: unknown, not confirmed
    assert cs2_scout._lineup_check(page, 7, None)[0] is None               # no map of his for the team: unknown
    assert cs2_scout._lineup_check(None, 7, usual)[0] is None              # no page: unknown
    assert cs2_scout._lineup_check([dict(p, status=2) if p["id"] == 7 else p for p in page], 7, usual)[0] is False   # benched
    others = [p for p in page if p["id"] != 7] + [{"id": 17, "slug": "e", "status": 1}]
    assert cs2_scout._lineup_check(others, 7, usual)[0] is False           # five active players known, he isn't one
    ok, detail = cs2_scout._lineup_check(page, 7, usual)
    assert detail == {"team_roster": 5, "on_team_roster": True, "last_lineup_overlap": 5, "starting_lineup": None}


def test_roster_confirmation_ignores_benched_players(monkeypatch):
    monkeypatch.setitem(cs2_scout.EVENT_TYPES, "slug:ev", "online")
    monkeypatch.setattr(cs2_scout, "window_stats", lambda b, slug, days: {})
    page = _page([{"id": 15, "slug": "x", "nickname": "x", "status": 2}, {"id": 16, "slug": "y", "nickname": "y", "status": 2},
                  {"id": 99, "slug": "coachy", "nickname": "coachy", "is_coach": True}])
    last = lambda i: [(7, "tester"), (11, "a"), (15, "x"), (16, "y"), (14, "d")] if i == 0 else ACTIVE
    assert cs2_scout.run_player(_bo3_for(last, page), "tester", _pargs())["roster_confirmed"] is False
    assert cs2_scout.run_player(_bo3_for(lambda i: ACTIVE, page), "tester", _pargs())["roster_confirmed"] is True
    short = {"players": [p for p in page["players"] if p["slug"] != "d"]}  # 4 active players listed: unknown
    assert cs2_scout.run_player(_bo3_for(lambda i: ACTIVE, short), "tester", _pargs())["roster_confirmed"] is None


def test_listed_team_is_exact(monkeypatch):
    monkeypatch.setitem(cs2_scout.EVENT_TYPES, "slug:ev", "online")
    monkeypatch.setattr(cs2_scout, "window_stats", lambda b, slug, days: {})
    b = _bo3_for(lambda i: ACTIVE, _page())
    assert cs2_scout.run_player(b, "tester", _pargs(team="Mine Academy"))["on_listed_team"] is False
    assert cs2_scout.run_player(b, "tester", _pargs(team="Team Mine"))["on_listed_team"] is True
    assert cs2_scout.run_player(b, "tester", _pargs(team="mine"))["on_listed_team"] is True


# ---------------------------------------------------------------- spec 3: a failed HLTV read is never a BO3.gg grade
@pytest.mark.parametrize("case", ["empty", "timeout", "incomplete", "down", "stored"])
def test_failed_hltv_series_read_is_transient_empty_is_not(monkeypatch, case):
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_CACHE", {})
    monkeypatch.setattr(hltv, "_META", {})
    cut = (date.today() - timedelta(days=200)).isoformat()

    class Cur(_Cur):
        def fetchall(self):
            if case == "incomplete" and self.since < cut:
                raise hltv.Unavailable("server closed the connection unexpectedly")      # cut off mid-read
            return []
    monkeypatch.setattr(hltv, "_connect", lambda: _Con(Cur()))
    monkeypatch.setattr(hltv, "_player_ids", lambda c, nick, team: [] if case == "empty" else [42])
    good = [{"match_id": "hltv-1", "hltv_match": 1, "date": (date.today() - timedelta(days=9)).isoformat(), "number": n,
             "map": "de_nuke", "kills": 25, "headshots": 9, "rounds": 24, "won": True, "opponent": "Z", "team": "Mine",
             "source": "hltv", "env": "online"} for n in (1, 2)]

    def series_rows(c, ids, since, *a, **k):
        c.since = since
        if case == "timeout" and since < cut:              # the 365-day read times out; the stake check's 90-day read answers
            raise hltv.Unavailable("canceling statement due to statement timeout")
        c.fetchall()
        return []
    monkeypatch.setattr(hltv, "_series_rows", series_rows)
    monkeypatch.setattr(hltv, "player_numbers", lambda *a, **k: {"found": False, "note": "x"})
    monkeypatch.setattr(roles, "refresh_hltv", lambda: None)
    monkeypatch.setattr(roles, "awp_role", lambda n, t: (None, None))
    monkeypatch.setattr(cs2_scout, "_hltv_context", lambda pb, args, fresh=False: {"found": True, "environment": None})
    monkeypatch.setattr(cs2_scout, "_perf_input_once", lambda pb, args, sc: {"version": "perf-2", "lambda": 31.0})
    seen = {}
    monkeypatch.setattr(cs2_scout, "grade_leg", lambda d: seen.update(d) or {"prop": d["prop"], "line": d["line"]})
    d0 = lambda i: (date.today() - timedelta(days=2 + 3 * i)).isoformat()
    bo3_rows = [{"match_id": 50 + i, "number": n, "date": d0(i), "opponent": f"T{i}", "team": "Mine", "team_id": 1, "map": "de_nuke",
                 "kills": 20, "headshots": 9, "rounds": 24, "won": True} for i in range(10) for n in (1, 2)]
    pb = _base_pb(recent_maps=bo3_rows)
    key = ("series", "tester", "mine", cs2_scout._hltv_since(pb, 365))
    if case in ("down", "stored"):                         # the database is unreachable for every read
        monkeypatch.setattr(hltv, "_connect", lambda: (_ for _ in ()).throw(hltv.Unavailable("could not connect")))
    if case == "stored":                                   # good reads saved earlier (the last-10 one and the stake check's)
        hltv_store.save(key, [dict(r) for r in good])
        hltv_store.save(("series", "tester", "mine", (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()), [])
    res = {"player_block": pb, "next_match": {"start": "2026-10-20T15:00:00Z", "opponent": "Foe", "bo": 3, "event": "Cup",
                                              "event_type": "online", "maps": []}}
    cs2_scout.finish_line(res, _args(line=40.5))
    st = seen["hltv_series"]
    if case == "empty":                                    # a good, EMPTY answer: BO3.gg's rows as before, labelled
        assert st["state"] == "live" and st["n"] == 0 and "no HLTV series for him - BO3.gg rows" in st["source"]
        assert "hltv_transient" not in seen and seen["perf"] == {"version": "perf-2", "lambda": 31.0}
        assert pb["recent_form"]["matches"] == 10
    elif case == "stored":                                 # the outage: the stored copy of the SAME query, with its age
        assert st["state"] == "stored copy" and st["n"] == 2 and st["age_min"] is not None and "stored copy" in st["source"]
        assert "hltv_transient" not in seen and any(s["source"] == "HLTV" for s in pb["recent_form"]["series"])
    else:                                                  # timed out / cut off / down: transient, never BO3.gg standing in
        assert st["state"] == "failed" and "transient" in st["source"]
        assert "could not be read" in seen["hltv_transient"] and seen["perf"]["transient"] is True
        assert hltv.lookup_failed(key) and hltv._CACHE[key][1] == []          # nothing partial kept as an answer
        if case != "down":                                 # only the 12-month read failed: this check alone catches it
            assert "last 10" in seen["hltv_transient"]


# ---------------------------------------------------------------- C6: cold and warm grades are identical
def test_c6_cold_and_warm_grades_are_identical(monkeypatch, offline):
    import json
    import market_odds
    monkeypatch.setattr(market_odds, "recheck", lambda od, **k: (od, {"result": "unchanged"}))
    monkeypatch.setattr(cs2_scout, "_CONTEXT", {})
    monkeypatch.setattr(cs2_scout, "BO3", lambda **k: SimpleNamespace(close=lambda: None, background=k.get("background")))

    def run(args, res, b, ttl):
        _fake_run({"found": False, "provenance": {"query": {"team": "Mine"}}})(args, res, b, ttl)
        res["player_block"]["_env"] = None
    monkeypatch.setattr(cs2_scout, "run", run)
    real_env = cs2_scout._environment
    monkeypatch.setattr(cs2_scout, "_environment", lambda res, pb: dict(real_env(res, pb) or {}, tonight="lan"))
    key = lambda g: (g.get("lambda_final"), g.get("p_more"), g.get("grade"), g.get("units"), g.get("side"))
    cold = cs2_scout.grade("tester", 30.5)
    (ckey, (at, stored)), = cs2_scout._CONTEXT.items()
    before = json.dumps(stored, sort_keys=True, default=str)
    warm = [cs2_scout.grade("tester", 30.5) for _ in range(5)]
    assert {key(r["grade"]) for r in warm} == {key(cold["grade"])} and cold["grade"].get("p_more") is not None
    assert {r["grader_inputs"]["kpr"] for r in warm} == {cold["grader_inputs"]["kpr"]} == {0.8}
    assert json.dumps(cs2_scout._CONTEXT[ckey][1], sort_keys=True, default=str) == before     # nothing accumulates


# ---------------------------------------------------------------- C4: forecast cutoff
def test_c4_last10_only_from_series_before_the_graded_match():
    st = datetime(2026, 10, 20, 15, tzinfo=timezone.utc)
    rows = []
    for mid, day, start in (("a", "2026-10-19", None), ("b", "2026-10-20", "2026-10-20T11:00:00Z"),
                            ("c", "2026-10-20", "2026-10-20T18:00:00Z"), ("d", "2026-10-21", None), ("e", "2026-10-20", None)):
        rows += [{"match_id": mid, "date": day, "start": start, "number": n} for n in (1, 2)]
    before = lambda now: sorted({r["match_id"] for r in cs2_scout._before_match(rows, st.isoformat(), now)})
    assert before(st - timedelta(hours=1)) == ["a", "b", "e"]              # same-day HLTV row (date only): before an unstarted match
    assert before(st + timedelta(hours=5)) == ["a", "b"]                   # the match has started: that day's undated series out
    assert cs2_scout._before_match(rows, None) == rows


# ---------------------------------------------------------------- C8: retries bounded and shared under load
def test_c8_concurrent_failing_reads_hit_the_database_once(monkeypatch):
    off = _clock(monkeypatch)
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_CACHE", {})
    calls, gate = [], threading.Event()

    def connect():
        calls.append(1)
        gate.wait(0.3)                                     # a slow failure: everyone piles up behind it
        raise hltv.Unavailable("statement timeout")
    monkeypatch.setattr(hltv, "_connect", connect)
    out, errs = [], []

    def one(fn):
        try:
            out.append(fn())
        except Exception as e:
            errs.append(e)

    def wave(fn, n=200):
        ths = [threading.Thread(target=one, args=(fn,)) for _ in range(n)]
        [t.start() for t in ths]
        [t.join() for t in ths]
    wave(lambda: hltv.recent_series("tester", "Mine", "2025-10-12"))
    assert len(calls) == 1 and not errs and out == [[]] * 200
    wave(lambda: hltv.context("tester", "Mine", "Foe", ()))
    assert len(calls) == 2 and all(o.get("transient") for o in out[200:])
    off[0] += hltv.FAIL_TTL + 1                            # one retry per key per FAIL_TTL, shared by everyone
    wave(lambda: hltv.recent_series("tester", "Mine", "2025-10-12"))
    assert len(calls) == 3
    # the performance model's reads too: one database call, the failure shared, no stored copy -> transient for all
    monkeypatch.setattr(perf_live, "_CACHE", {})
    out.clear()
    wave(lambda: perf_live.player_maps("tester", "Mine", "2025-08-01"))
    assert len(calls) == 4 and len(errs) == 200 and all(hltv.is_transient(e) for e in errs)
