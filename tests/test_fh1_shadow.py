"""fh-1 full-history shadow (owner 2026-10-15): live and fh-1 through the same production projection code, differing only in
history eligibility; the live arm = the exact live N0 (n0-r4) rows; identical history = identical outputs; both predictions
saved before the match with their inputs, revisions and times; exact replay from saved inputs; scored after settlement.
Every row, id, name and kill count below is a LABELLED TEST FIXTURE (the grader inputs are one saved production input)."""
import copy
import json
import sqlite3
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import cs2_scout
import grader
import hltv
import learning as L
import perf
import perf_live
import perf_shadow as S
import picklog
from pick_verdict import verdict

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))["inputs"]
START = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
DAY = START.date().toordinal()
N0_IDS = {1, 2, 3, 4, 5}          # TEST: match ids inside N0


def dd(k):
    return date.fromordinal(DAY - k)


# (date, kills, rounds, hs, lan, players on the map, match id) - TEST FIXTURE
PLAYER = [(dd(3), 20, 24, 10, False, 10, 1), (dd(10), 18, 22, 9, True, 10, 2), (dd(20), 25, 26, 12, False, 10, 3),
          (dd(25), 15, 20, 7, False, 9, 4),          # substitution map: in neither arm
          (dd(40), 22, 24, 0, False, 10, 5),         # 0 HS on 12+ kills: HS flag off in both arms
          (dd(5), 30, 24, 14, False, 10, 101), (dd(12), 28, 26, 13, False, 10, 102)]   # outside N0: fh-1 only
OPP = [(dd(4), 90, 24, 1), (dd(15), 100, 26, 3), (dd(6), 80, 22, 103), (dd(30), 95, 24, 104)]


class Cur:
    def __init__(self, db):
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, p=None):
        since = date.fromisoformat(p["since"])
        if "pm.n, ps.matchid" in sql:
            self.rows = [r for r in self.db["player"] if r[0] >= since]
        elif "SUM(ps.k)" in sql:
            self.rows = [r for r in self.db["opp"] if r[0] >= since]
        else:
            raise AssertionError("unexpected query")
        self.db["calls"] += 1

    def fetchall(self):
        return list(self.rows)


class Con:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return Cur(self.db)

    def close(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    db = {"player": list(PLAYER), "opp": list(OPP), "calls": 0}
    monkeypatch.setattr(hltv, "dsn", lambda: "test")
    monkeypatch.setattr(hltv, "_connect", lambda: Con(db))
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [777] if nick == "tester" else [])
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: 555 if name == "Opp" else None)
    n0 = {"pre": frozenset(N0_IDS), "late_in": frozenset(), "sha": "testsha", "counts": {}, "rule": perf_live.INPUT_RULE, "snapshots_used": []}
    monkeypatch.setattr(perf_live, "n0", lambda: n0)
    monkeypatch.setattr(perf_live, "input_revision", lambda: "N0/n0-r4:TEST")
    monkeypatch.setattr(perf_live, "round_shares", lambda *a: {})
    monkeypatch.setattr(perf_live, "_CACHE", {})
    monkeypatch.setattr(perf_live, "_SERVED", {})
    monkeypatch.setattr(S, "_RAW", {})
    monkeypatch.setattr(S, "_FAIL", {})
    return db


BO3_MAPS = [{"date": dd(k).isoformat(), "kills": 17 + k % 5, "rounds": 23, "headshots": 8, "env": "online", "match_id": 900 + k}
            for k in (2, 9, 16)]


def live_res(prop="kills", line=None, nick="tester", opp="Opp", tonight="online"):
    """A board grade exactly as production makes it: cs2_scout._perf_input_once -> grader.grade_leg (TEST FIXTURE player)."""
    pb = {"player": {"nick": nick, "slug": nick, "id": 1}, "playing_for": "Team", "current_team": "Team", "_env": {"tonight": tonight},
          "recent_maps": BO3_MAPS, "_match_env": {}, "overall": {"maps_played": 40}}
    args = types.SimpleNamespace(opponent=opp, match_start=START.isoformat(), prop=prop)
    pf = cs2_scout._perf_input_once(pb, args, "12")
    gi = dict(copy.deepcopy(SAMPLE), perf=pf, prop=prop, scope="12", line=line if line is not None else float(int(pf["lambda"])) + 0.5)
    g = grader.grade_leg(copy.deepcopy(gi))
    res = {"grade": g, "grader_inputs": gi, "player_block": pb,
           "next_match": {"slug": "test-match", "opponent": opp, "start": START.isoformat(), "maps": []}}
    return res, {"start": START.isoformat(), "opponent": opp, "scope": "12"}


def rows6(raw, ids):
    return [[r[0].toordinal(), r[1], r[2], r[3], bool(r[4]), not (r[3] == 0 and r[1] >= 12)] for r in raw if r[5] == 10 and r[6] in ids]


def test_live_arm_is_the_exact_live_selection_and_fh1_adds_only_what_n0_excludes(fake):
    res, r = live_res()
    assert res["grade"]["projection_source"] == "perf-2"
    rec, bodies = S.build(res, r)
    assert rec["n0_check"] == dict(rec["n0_check"], exact=True, published_reproduced=True, player_read_consistent=True, opp_read_consistent=True)
    snap = S.unpack(bodies[rec["inputs_sha"]][1])
    assert snap["player"]["live"] == rows6(PLAYER, N0_IDS)                         # the live cache rows, in their order
    assert sorted(snap["player"]["extra"]) == sorted(rows6(PLAYER, {101, 102}))    # only the rows N0 excludes
    opp_full = [[x[0].toordinal(), x[1], x[2]] for x in OPP if x[3] in N0_IDS] + snap["opp"]["extra"]
    assert sorted(opp_full) == sorted([x[0].toordinal(), x[1], x[2]] for x in OPP)
    full = perf.project(rows6(PLAYER, N0_IDS) + snap["player"]["extra"], opp_full, False, DAY, "12", "kills")
    assert rec["fh1"]["lambda_perf"] == full["lambda"] and rec["fh1"]["sd"] == full["sd"]
    assert rec["live"]["lambda_perf"] == res["grader_inputs"]["perf"]["lambda"] and rec["live"]["p_more"] == res["grade"]["p_more"]
    assert rec["adds"] == {"player_maps": 2, "opp_maps": 2} and rec["identical_history"] is False
    assert rec["fh1"]["lambda"] != rec["live"]["lambda"]
    assert rec["history"]["live"].startswith("N0/n0-r4") and rec["published"]["proj_version"] == "perf-2"
    assert rec["read_at"]["player"] and rec["read_at"]["live_player"] and rec["computed_at"]


def test_identical_history_gives_identical_outputs(fake):
    fake["player"] = [x for x in PLAYER if x[6] in N0_IDS]
    fake["opp"] = [x for x in OPP if x[3] in N0_IDS]
    for prop in ("kills", "hs"):
        perf_live._CACHE.clear(), S._RAW.clear()
        res, r = live_res(prop=prop)
        rec, _ = S.build(res, r)
        assert rec["identical_history"] is True and rec["n0_check"]["exact"]
        assert rec["live"] == rec["fh1"]                                            # every field, exactly


def test_unknown_history_environment_replays_in_both_arms(fake):
    fake["player"] = [tuple(list(x[:4]) + [None] + list(x[5:])) for x in PLAYER]
    res, row = live_res()
    rec, bodies = S.build(res, row)
    assert rec["n0_check"]["exact"]
    snap = S.unpack(bodies[rec["inputs_sha"]][1])
    assert snap["row_policy"] == perf_live.ROW_POLICY
    assert all(x[4] is None for x in snap["player"]["live"] + snap["player"]["extra"])
    replay = S.replay(snap, res["grader_inputs"])
    assert replay["live"] == rec["live"] and replay["fh1"] == rec["fh1"]


def test_project_arm_on_any_snapshot_without_extras_is_the_live_arm():
    import random
    rnd = random.Random(3)
    for _ in range(200):
        pm = [[DAY - rnd.randint(1, 400), rnd.randint(5, 35), rnd.randint(16, 30), rnd.choice([None, rnd.randint(0, 15)]),
               rnd.random() < 0.3, rnd.random() < 0.95] for _ in range(rnd.randint(0, 40))]
        om = [[DAY - rnd.randint(1, 400), rnd.randint(50, 120), rnd.randint(16, 30)] for _ in range(rnd.randint(0, 30))]
        snap = {"day": DAY, "lan": rnd.choice([None, True, False]), "scope": rnd.choice(["1", "12", "123"]), "prop": rnd.choice(["kills", "hs"]),
                "opponent": "Opp", "player": {"live_id": [1], "live": pm, "id": [1], "extra": []},
                "opp": {"live_id": 2, "live": om, "id": 2, "extra": []}, "bo3_rows": []}
        a, b = S.project_arm("live", snap), S.project_arm("fh1", snap)
        assert a == b


def test_replay_from_saved_inputs_is_exact_and_detects_a_changed_input(fake):
    res, r = live_res(prop="hs")
    rec, bodies = S.build(res, r)
    snap, gi = S.unpack(bodies[rec["inputs_sha"]][1]), S.unpack(bodies[rec["gi_sha"]][1])
    assert S.check_record(json.loads(json.dumps(rec)), snap, gi) == (True, [])
    bad = copy.deepcopy(snap)
    bad["player"]["extra"][0][1] += 5
    ok, why = S.check_record(rec, bad, gi)
    assert not ok and any("fh1." in w for w in why) and "saved input hash differs" in why


def test_bo3_fallback_live_and_hltv_full_history(fake):
    fake["player"] = [x for x in PLAYER if x[6] not in N0_IDS]          # the player is known only from outside N0
    res, r = live_res()
    assert res["grade"]["projection_source"] == "perf-2-bo3"
    rec, bodies = S.build(res, r)
    assert rec["n0_check"]["exact"] and rec["published"]["proj_version"] == "perf-2-bo3"
    assert rec["live"]["proj_version"] == "perf-2-bo3" and rec["fh1"]["proj_version"] == "perf-2"
    assert rec["group"] == "thin history / lower tier" and rec["depth"].startswith("BO3.gg")
    snap, gi = S.unpack(bodies[rec["inputs_sha"]][1]), S.unpack(bodies[rec["gi_sha"]][1])
    assert snap["bo3_rows"] and S.check_record(rec, snap, gi)[0]


def test_a_live_cache_refreshed_after_the_grade_is_flagged_not_hidden(fake):
    res, r = live_res()
    fake["player"].append((dd(1), 40, 24, 20, False, 10, 4))          # a new N0 map lands, the live cache refreshes
    perf_live._CACHE.clear()
    cs2_scout._perf_input_once(res["player_block"], types.SimpleNamespace(opponent="Opp", match_start=START.isoformat(), prop="kills"), "12")
    rec, _ = S.build(res, r)
    assert rec["n0_check"]["published_reproduced"] is False and rec["n0_check"]["exact"] is False


def test_attach_never_raises_and_skips_without_a_record(fake, monkeypatch):
    res, r = live_res()
    monkeypatch.setattr(S, "_player_raw", lambda *a: (_ for _ in ()).throw(hltv.Unavailable("TEST outage")))
    before = S.STATS["error"]
    assert S.attach(res, r) is None and "_fh1" not in res and S.STATS["error"] == before + 1
    res2, r2 = live_res()
    res2["grader_inputs"]["perf"] = {"error": "HLTV database unreachable right now", "transient": True}
    assert S.attach(res2, r2) is None and "_fh1" not in res2


@pytest.fixture
def pdb(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: "matches=1;max_id=1;max_date=2026-10-15")
    return tmp_path / "picks.db"


def _board_res(res):
    res = dict(res)
    res["player_block"] = dict(res["player_block"], player={"nick": "tester", "slug": "tester", "id": 1})
    res["next_match"] = dict(res["next_match"], start=START.isoformat())
    res["book"] = {"book": "underdog", "market_id": "TEST-1", "market": "m", "odds_type": "standard"}
    return res


def test_pick_log_saves_records_beside_unchanged_live_rows(fake, pdb):
    res, r = live_res()
    res = _board_res(res)
    pid = picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))          # without fh-1
    con = sqlite3.connect(pdb)
    con.row_factory = sqlite3.Row
    base = [dict(x) for x in con.execute("SELECT line, pick, p_pick, p_more, grade, units, lambda FROM forecasts WHERE pick_id = ?", (pid,))]
    S.attach(res, r)
    picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))                 # the same forecast + its record
    picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))                 # unchanged: nothing new
    assert [dict(x) for x in con.execute("SELECT line, pick, p_pick, p_more, grade, units, lambda FROM forecasts WHERE pick_id = ?", (pid,))] == base
    sh = con.execute("SELECT * FROM shadow_forecasts WHERE pick_id = ?", (pid,)).fetchall()
    assert len(sh) == 1 and sh[0]["n0_exact"] == 1 and sh[0]["ch_p_more"] == json.loads(sh[0]["rec"])["fh1"]["p_more"]
    assert sh[0]["forecast_id"] == con.execute("SELECT MAX(id) FROM forecasts WHERE pick_id = ?", (pid,)).fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM shadow_inputs").fetchone()[0] == 2
    fake["player"].append((dd(2), 35, 24, 15, False, 10, 105))       # a new lower-tier map: fh-1 changes, live does not
    S._RAW.clear()
    S.attach(res, r)
    picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))
    assert con.execute("SELECT COUNT(*) FROM shadow_forecasts WHERE pick_id = ?", (pid,)).fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM forecasts WHERE pick_id = ?", (pid,)).fetchone()[0] == len(base)
    rc = picklog.shadow_replay_check()
    assert rc["rows_checked"] == 2 and rc["mismatches"] == 0
    pg = picklog.shadow_page(limit=1)
    assert len(pg["rows"]) == 1 and not pg["done"] and pg["rows"][0]["rec"]["challenger"] == "fh-1"


def test_a_shadow_arm_change_appends_a_record_while_the_live_rows_stay_identical(fake, pdb):
    """2026-10-18 audit: cm-1 becoming available / moving with its tables is saved even when the live grade is unchanged."""
    res, r = live_res()
    res = _board_res(res)
    S.attach(res, r)
    res["_fh1"]["rec"]["cm1"] = {"version": "cm-1", "ineligible": "cm-1 tables not built yet"}         # TEST: before tables
    pid = picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))
    con = sqlite3.connect(pdb)
    con.row_factory = sqlite3.Row
    live = lambda: [dict(x) for x in con.execute("SELECT * FROM forecasts WHERE pick_id = ?", (pid,))]
    pick = lambda: dict(con.execute("SELECT * FROM picks WHERE id = ?", (pid,)).fetchone())
    base_f, base_p = live(), pick()
    res["_fh1"]["rec"]["cm1"] = {"version": "cm-1", "p_more": 0.61, "lambda": 31.0}                   # TEST: tables built
    picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))
    picklog.write_row(picklog.pick_row(res, verdict(res["grade"]), test=2))                            # unchanged: nothing new
    sh = con.execute("SELECT rec FROM shadow_forecasts WHERE pick_id = ? ORDER BY id", (pid,)).fetchall()
    assert [json.loads(x["rec"])["cm1"].get("p_more") for x in sh] == [None, 0.61]
    assert live() == base_f
    counters = ("requests", "regrades")                       # board-write counters, incremented on every pre-match write
    assert {k: v for k, v in pick().items() if k not in counters} == {k: v for k, v in base_p.items() if k not in counters}

def test_learning_scores_the_latest_record_before_the_cutoff_after_it_passes(tmp_path):
    con = L.connect(tmp_path / "l.db")
    st = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
    freeze = st - timedelta(hours=10)
    L._ensure_full_history(con, freeze)
    L._ensure_full_history(con, freeze + timedelta(hours=1))                 # once
    ch = con.execute("SELECT * FROM challengers WHERE family = 'full-history-1'").fetchone()
    assert ch["id"] == f"fh-001-{L._sha(L.FULL_HISTORY)[:8]}" and ch["status"] == "shadow" and ch["frozen_at"] == L._iso(freeze)
    t0 = st - timedelta(hours=3)
    con.execute("INSERT INTO forecast_copy (forecast_id, pick_id, logged_at, match_start, line, side, p_more, lambda, source, book, line_type, prop, scope) "
                "VALUES (1, 7, ?, ?, 29.5, 'More', 0.55, 30.4, 'board', 'underdog', 'standard', 'kills', '12')", (L._iso(t0), L._iso(st)))

    def rec(i, at, pf, pl):
        r = {"challenger": "fh-1", "live": {"p_more": pl, "lambda": 30.4, "projection_source": "perf-2"},
             "fh1": {"p_more": pf, "lambda": 31.2, "projection_source": "perf-2"}, "n0_check": {"exact": True},
             "identical_history": False, "group": "thin history / lower tier", "depth": "< 150 rounds", "adds": {"player_maps": 3}}
        return {"id": i, "pick_id": 7, "forecast_id": 1, "logged_at": L._iso(at), "match_start": L._iso(st), "line": 29.5, "prop": "kills",
                "scope": "12", "live_p_more": pl, "ch_p_more": pf, "live_lambda": 30.4, "ch_lambda": 31.2, "n0_exact": 1, "identical": 0,
                "grp": r["group"], "inputs_sha": "a", "gi_sha": "b", "rec": json.dumps(r)}
    L._copy_fh1(con, [rec(1, t0, 0.61, 0.55), rec(2, st - timedelta(minutes=10), 0.70, 0.55)], freeze)   # 2: after the cut-off
    assert L._fh1_shadow(con, ch, st - timedelta(minutes=25)) == 0                                   # cut-off + 5 min: wait
    assert L._fh1_shadow(con, ch, st - timedelta(minutes=15)) == 1
    s = con.execute("SELECT * FROM shadow WHERE challenger_id = ?", (ch["id"],)).fetchone()
    assert (s["p_over_ch"], s["p_over_base"], s["p_over_logged"], s["lam_ch"]) == (0.61, 0.55, 0.55, 31.2)
    us = [{"forecast_id": 1, "logged": L._iso(t0), "start": L._iso(st), "match": "m1", "over": 1, "actual": 33.0, "line": 29.5,
           "prop": "kills", "scope": "12", "p": 0.55}]
    ex = L._fh1_extras(con, ch, us)["fh1"]
    assert ex["units"] == 1 and ex["matches"] == 1 and ex["all"]["log_loss_gain"] > 0
    assert ex["by_group"][0]["group"] == "thin history / lower tier"
    assert ex["all"]["projection"]["kills"]["mae"] == {"live": 2.6, "fh-1": 1.8}
