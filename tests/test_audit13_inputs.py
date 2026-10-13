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


# ---------------------------------------------------------------- C1: one series, one total (HLTV / BO3.gg twins)
def test_c1_same_series_spelled_differently_counts_once(monkeypatch):
    d = (date.today() - timedelta(days=3)).isoformat()
    maps = [("de_nuke", 24, False), ("de_mirage", 22, False)]
    b3 = _series(555, d, "FaZe Clan", maps, (14, 13))
    hl = _series("hltv-999", d, "FaZe", maps, (15, 13), hltv_row=True)
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: [dict(r) for r in hl])
    pb = {"recent_maps": b3, "_older_maps": [], "player": {"nick": "x"}}
    rf = cs2_scout.recent_form(cs2_scout._form_rows(pb, "kills", keep_n=40, back_days=365), 0.7, 26.5)
    assert rf["matches"] == 1 and rf["series"][0]["total"] == 28 and rf["series"][0]["source"] == "HLTV"
    assert (rf["overs"], rf["decided"]) == (1, 1)
    h = cs2_scout.h2h(cs2_scout._hltv_h2h({}, [dict(r) for r in b3], "FaZe Clan"), "FaZe Clan", 26.5)
    assert [s["total"] for s in h["series"]] == [28]                       # one meeting, HLTV's numbers


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
