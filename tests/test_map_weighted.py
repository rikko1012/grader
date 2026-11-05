"""CS2_MAP_WEIGHTED / mw-1 (map_weighted.py, MODEL_VERSIONS.md): the vm-2 map weighting as the live CS2 projection behind a
flag that defaults to 0. Flag off: nothing is wrapped, the frozen model tag is untouched and every grade is byte-identical.
Flag on: the projection is vm-2's map-weighted one (exact arithmetic checked), under its own model tag; every fallback keeps
the current projection with its reason logged; snapshots must be known at or before the forecast time and before the start;
an HLTV database failure is the existing transient error, never a different grade. Fixture: GenOne vs MAYBE, HLTV match
2400124 (tests/test_vm2_map_chances.py); the grader inputs are the saved sample forecast (TEST FIXTURE)."""
import copy
import datetime as dt
import json
import math
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import cs2_scout
import grader
import hltv
import hltv_mapchances as hmc
import map_weighted as mw
import picklog
import vm2
from tests import test_vm2_map_chances as tv
from tests.test_audit13_grader import _base, _hs

ROOT = Path(__file__).resolve().parent.parent
NOW = tv.NOW
CAP, START = tv.CAP, tv.START
FROZEN_TAG = "3007c59b74"
HASHED = ("grader.py", "sim.py", "cs2_scout.py", "hltv.py", "perf.py", "perf_live.py", "scope.py", "hltv_store.py",
          "market_odds.py", "thunderpick.py", "pick_verdict.py", "bo3.py", "roles.py", "polymarket.py", "oddspapi.py",
          "cm1.py", "cm2.py", "cm2_model.json")


@pytest.fixture
def db(monkeypatch):
    fake = tv.FakeDB(mc=tv.mc_row())
    monkeypatch.setattr(hltv, "dsn", lambda: "postgresql://test")
    monkeypatch.setattr(hltv, "_connect", lambda: fake)
    monkeypatch.setattr(hmc, "_MC", {})
    return fake


@pytest.fixture(autouse=True)
def off_after():
    yield
    mw.deactivate()


def res_(slug="hltv-2400124", start=START, opp="MAYBE", team="GenOne", pid=11893):
    return {"next_match": {"slug": slug, "start": start, "opponent": opp}, "player_block": {"playing_for": team},
            "hltv": {"found": bool(pid), "hltv_id": pid}}


def d_kills(line=29.5):
    return _base(line=line)


def per_map_expected():
    a0 = 1783 / 102
    avg = {"ancient": a0, "dust2": 429 / 25, "anubis": 237 / 15, "cache": 221 / 12, "nuke": 234 / 13, "inferno": 202 / 13, "mirage": 301 / 17}
    w = {"ancient": 68, "dust2": 68, "anubis": 56, "cache": 51, "nuke": 50, "inferno": 7, "mirage": 1}
    return sum(w[k] * avg[k] for k in w) / sum(w.values()), a0


# ---------------------------------------------------------------- weighted vs unweighted
def test_weighted_projection_is_vm2s_exact_formula_and_moves_the_grade(db):
    d = d_kills()
    before = copy.deepcopy(d)
    rec = mw.apply(d, res_(), now=NOW)
    per, a0 = per_map_expected()
    assert rec["used"] is True and rec["rule"] == "mw-1" and rec["vm2_version"] == "vm-2-37ec7435b0"
    assert d["perf"]["lambda"] == pytest.approx(2 * per, abs=1e-4)               # sum(chance x a_m) / sum(chance) x 2 maps
    c = before["perf"]["sd"] / math.sqrt(before["perf"]["lambda"])
    assert d["perf"]["sd"] == pytest.approx(c * math.sqrt(d["perf"]["lambda"]), rel=1e-12)   # the live spread, rescaled
    assert d["perf"]["lambda_kills"] == d["perf"]["lambda"] and d["perf"]["version"] == "perf-2"
    assert rec["unweighted"]["lambda"] == before["perf"]["lambda"] and rec["weighted"]["lambda"] == d["perf"]["lambda"]
    assert rec["snapshot"]["chances"]["Dust 2"] == 68 and rec["snapshot"]["captured_at"] == dt.datetime.fromisoformat(CAP).isoformat()
    assert rec["map_factor"] == pytest.approx(per / a0, abs=1e-6) and rec["player"]["per_map"]["Ancient"]["fallback"] == "overall"
    # everything except the projection is the same input
    assert {k: v for k, v in d.items() if k not in ("perf", "map_weighted")} == {k: v for k, v in before.items() if k != "perf"}
    gw, gu = grader.grade_leg(d), grader.grade_leg(before)
    assert gw["lambda_final"] == round(d["perf"]["lambda"], 2) and gu["lambda_final"] == round(before["perf"]["lambda"], 2)
    assert gw["p_more"] != gu["p_more"] and gw["projection_source"] == gu["projection_source"] == "perf-2"


def test_weighted_equals_the_vm2_shadow_arm_for_the_same_inputs(db):
    d = d_kills()
    snap = hmc.map_chances("hltv:2400124", START)
    pk = hmc.player_map_kills(11893, dt.date.fromisoformat(START[:10]), 180)
    ent = vm2.shadow_entry(copy.deepcopy(d), snap, pk)
    mw.apply(d, res_(), now=NOW)
    assert d["perf"]["lambda"] == ent["vm-2"]["lam"] and d["map_weighted"]["per_map_kills"] == ent["vm-2"]["per_map_kills"]


def test_uniform_map_kills_give_the_pooled_projection_not_the_live_one(db):
    db.pk = [(m, 10, 170) for m, _, _ in tv.CHANCES]
    d = d_kills()
    mw.apply(d, res_(), now=NOW)
    assert d["perf"]["lambda"] == pytest.approx(34.0) and d["map_weighted"]["map_factor"] == 1.0


def test_hs_line_weights_the_kills_and_keeps_hs_per_kill_and_kills_spread(db):
    d = _hs(_base())
    before = copy.deepcopy(d)
    rec = mw.apply(d, res_(), now=NOW)
    per, _ = per_map_expected()
    h = before["perf"]["hs_per_kill"]
    assert rec["used"] and d["perf"]["lambda_kills"] == pytest.approx(2 * per, abs=1e-4)
    assert d["perf"]["lambda"] == pytest.approx(d["perf"]["lambda_kills"] * h, rel=1e-12) and d["perf"]["hs_per_kill"] == h
    assert d["perf"]["spread_c_kills"] == before["perf"]["spread_c_kills"]
    assert grader.grade_leg(d)["p_more"] != grader.grade_leg(before)["p_more"]


def test_map1_uses_the_likeliest_veto_map(db):
    d = _base(scope="1", line=14.5)
    d["perf"] = dict(d["perf"], **{"lambda": 14.7, "lambda_kills": 14.7, "sd": 5.6})
    rec = mw.apply(d, res_(), now=NOW)
    assert rec["used"] and rec["map1"]["map"] == "Dust 2" and d["perf"]["lambda"] == pytest.approx(429 / 25, abs=1e-4)


# ---------------------------------------------------------------- clean fallback (current projection, reason logged)
@pytest.mark.parametrize("case,expect", [
    ("no_chances", "no HLTV map chances for this match"),
    ("no_simulation", "HLTV showed no map simulation"),
    ("stale", "stale"),
    ("late", "after the match start"),
    ("no_opponent", "team unknown"),
    ("hashed_match", "team unknown (the match has no HLTV id)"),
    ("no_player", "no HLTV player id"),
    ("legacy", "live projection not perf-2"),
    ("thin", "fewer than 10 HLTV maps"),
    ("bad_sum", "add up to"),
])
def test_fallback_keeps_the_current_projection_and_logs_why(db, case, expect):
    d, res, now = d_kills(), res_(), NOW
    if case == "no_chances":
        db.mc = (None,) * 5
    elif case == "no_simulation":
        db.mc = tv.mc_row(chances=None, found=False, vetoes=False)
    elif case == "stale":
        db.mc = tv.mc_row(cap=(NOW - dt.timedelta(minutes=mw.STALE_MIN + 30)).isoformat())
    elif case == "late":                         # HLTV re-read the match after the start: nothing pre-start is left
        db.mc = tv.mc_row(cap=(NOW - dt.timedelta(minutes=20)).isoformat())
        res = res_(start=(NOW - dt.timedelta(minutes=30)).isoformat())
        now = NOW - dt.timedelta(minutes=10)
    elif case == "no_opponent":
        res = res_(opp=None)
    elif case == "hashed_match":
        res = res_(slug="some-bo3-slug")
        res["next_match"]["hltv_match"] = None
    elif case == "no_player":
        res = res_(pid=None)
    elif case == "legacy":
        d = dict(d, perf={"error": "player not found in HLTV (TEST FIXTURE)"})
    elif case == "thin":
        db.pk = [("Anubis", 9, 150)]
    elif case == "bad_sum":
        db.mc = tv.mc_row(chances=tv.CHANCES[:4])
    if case == "hashed_match":
        import veto_serve
        assert veto_serve.match_id_of(res) is None or not str(veto_serve.match_id_of(res)).startswith("hltv:")
    ref = copy.deepcopy(d)
    rec = mw.apply(d, res, now=now)
    assert rec["used"] is False and expect in rec["reason"], rec
    assert d["perf"] == ref["perf"]                                              # the current projection, untouched
    assert {k: v for k, v in d.items() if k != "map_weighted"} == ref
    assert json.dumps(grader.grade_leg(d), sort_keys=True, default=str) == json.dumps(grader.grade_leg(ref), sort_keys=True, default=str)


def test_database_failure_is_transient_never_a_different_grade(db):
    db.fail = TimeoutError("timed out")
    d = d_kills()
    rec = mw.apply(d, res_(), now=NOW)
    assert rec["used"] is False and rec["transient"] is True and d["perf"]["transient"] is True
    assert "HLTV database unreachable" in d["perf"]["error"]


def test_a_crash_in_the_weighting_keeps_the_grade(db, monkeypatch):
    monkeypatch.setattr(mw, "apply", lambda *a, **k: 1 / 0)
    base_gi = lambda pb, args, odds=None, hh=None: d_kills()   # noqa: E731
    monkeypatch.setattr(cs2_scout, "grader_inputs", base_gi)
    mw.activate(force=True)
    d = cs2_scout.grader_inputs({}, SimpleNamespace())
    assert d["map_weighted"] == {"rule": "mw-1", "used": False, "reason": "map weighting failed (ZeroDivisionError)"}
    assert d["perf"] == d_kills()["perf"]


# ---------------------------------------------------------------- known_at <= forecast_at, before the start
def test_snapshot_captured_after_the_forecast_time_is_not_used(db):
    d = d_kills()
    rec = mw.apply(d, res_(), now=dt.datetime.fromisoformat(CAP) - dt.timedelta(minutes=10))
    assert rec["used"] is False and rec["reason"] == "HLTV map chances captured after the forecast time"
    assert d["perf"] == d_kills()["perf"]
    rec = mw.apply(d_kills(), res_(), now=dt.datetime.fromisoformat(CAP))           # known exactly at the forecast time: used
    assert rec["used"] is True


def test_check_snapshot_rules_directly():
    snap = dict(hmc.parse_map_chances(tv.mc_row(), 2400124), captured_at=CAP, start_used=START)
    cap = dt.datetime.fromisoformat(CAP)
    assert mw.check_snapshot(snap, cap + dt.timedelta(minutes=1))[1] is None
    assert "after the forecast" in mw.check_snapshot(snap, cap - dt.timedelta(seconds=1))[1]
    assert "at or after the match start" in mw.check_snapshot(snap, START)[1]
    assert "stale" in mw.check_snapshot(snap, cap + dt.timedelta(minutes=mw.STALE_MIN + 1))[1]
    late_veto = copy.deepcopy(snap)
    late_veto["vetoes"][0]["updated_at"] = (cap + dt.timedelta(hours=2)).isoformat()
    kept, why = mw.check_snapshot(late_veto, cap + dt.timedelta(minutes=5))
    assert why is None and [v["rank"] for v in kept["vetoes"]] == [2]          # a veto known only later is dropped
    assert mw.check_snapshot({"ok": False, "reason": "x"}, cap)[1] == "x"


def test_snapshot_updated_after_the_start_is_never_used(db):
    db.mc = tv.mc_row(cap=(NOW - dt.timedelta(minutes=5)).isoformat())
    res = res_(start=(NOW - dt.timedelta(minutes=10)).isoformat())
    rec = mw.apply(d_kills(), res, now=NOW - dt.timedelta(minutes=20))
    assert rec["used"] is False and "after the match start" in rec["reason"]


# ---------------------------------------------------------------- flag off = byte-identical, flag on = new tag
def test_flag_defaults_off_and_server_import_wraps_nothing(monkeypatch):
    monkeypatch.delenv("CM2_MAP_WEIGHTED", raising=False)
    monkeypatch.delenv(mw.FLAG, raising=False)
    import server  # noqa: F401
    assert mw.enabled() is False and mw.activate() is False and mw.active() is False
    assert cs2_scout.finish_line.__module__ == "cs2_scout" and cs2_scout.grader_inputs.__module__ == "cs2_scout"
    assert picklog.MODEL_VERSION == picklog._model_version()
    for v in ("0", "", "true", "yes", " 2"):
        assert mw.enabled({mw.FLAG: v}) is False
    assert mw.enabled({mw.FLAG: "1"}) is True


def test_flag_off_grades_are_byte_identical(db, monkeypatch):
    monkeypatch.delenv(mw.FLAG, raising=False)
    calls = []
    orig_gi = lambda pb, args, odds=None, hh=None: (calls.append(1), d_kills())[1]   # noqa: E731
    monkeypatch.setattr(cs2_scout, "grader_inputs", orig_gi)
    assert mw.activate() is False
    assert cs2_scout.grader_inputs is orig_gi
    d = cs2_scout.grader_inputs({}, SimpleNamespace())
    assert "map_weighted" not in d and json.dumps(d, sort_keys=True, default=str) == json.dumps(d_kills(), sort_keys=True, default=str)
    g = grader.grade_leg(d)
    assert json.dumps(g, sort_keys=True, default=str) == json.dumps(grader.grade_leg(d_kills()), sort_keys=True, default=str)
    assert not db.calls                                                           # flag off reads nothing on the grade path
    assert picklog.MODEL_VERSION == picklog._model_version()


def test_flag_on_new_tag_and_off_restores_the_frozen_one(db, monkeypatch):
    base = picklog.MODEL_VERSION
    monkeypatch.setenv(mw.FLAG, "1")
    assert mw.activate() is True and mw.active()
    on = picklog.MODEL_VERSION
    assert on != base and on == mw.tag_on(base) and len(on) == 10
    assert picklog._model_version() == base                                     # the frozen hash itself never moves
    assert mw.activate() is True and picklog.MODEL_VERSION == on                # idempotent
    mw.deactivate()
    assert picklog.MODEL_VERSION == base and cs2_scout.finish_line.__module__ == "cs2_scout"
    assert mw.tag_on(base) != mw.tag_on("other") and mw.tag_on(base) == on      # deterministic


def test_flag_on_wiring_runs_the_weighting_inside_the_real_grade_call_chain(db, monkeypatch):
    # the real finish_line calls grader_inputs by its module global, and grade() calls finish_line by its module global
    assert "grader_inputs" in cs2_scout.finish_line.__code__.co_names
    assert "finish_line" in cs2_scout.grade.__code__.co_names and "finish_line" in cs2_scout.run.__code__.co_names
    seen = {}

    def fake_finish(res, args):                  # stands in for the real one: same global lookup of grader_inputs
        res["grader_inputs"] = cs2_scout.grader_inputs(res["player_block"], args)
        res["grade"] = grader.grade_leg(res["grader_inputs"])
    monkeypatch.setattr(cs2_scout, "finish_line", fake_finish)
    monkeypatch.setattr(cs2_scout, "grader_inputs", lambda pb, args, odds=None, hh=None: d_kills())
    mw.activate(force=True)
    res = res_()
    cs2_scout.finish_line(res, SimpleNamespace(opponent="MAYBE", match_start=START))
    seen["used"] = res["grader_inputs"]["map_weighted"]["used"]
    per, _ = per_map_expected()
    assert seen["used"] is True and res["grade"]["lambda_final"] == round(2 * per, 2)
    assert getattr(mw._CTX, "res", None) is None                               # the context never leaks to the next grade


def test_model_hash_files_untouched_and_frozen_tag():
    out = subprocess.run(["git", "diff", "--quiet", "origin/main", "--", *HASHED], cwd=ROOT)
    if out.returncode not in (0, 1):
        pytest.skip("origin/main not available")
    assert out.returncode == 0                                                   # no hashed file edited on this branch
    import hashlib
    h = hashlib.sha1()
    for f in HASHED:
        blob = subprocess.run(["git", "show", f"HEAD:{f}"], cwd=ROOT, capture_output=True)
        if blob.returncode != 0:
            pytest.skip("git blobs not available")
        h.update(blob.stdout)
    assert h.hexdigest()[:10] == FROZEN_TAG                                      # as Render builds it from the committed blobs
    assert not set(mw.TAG_FILES) & set(HASHED)


# ---------------------------------------------------------------- forecast record + display
def test_the_forecast_record_carries_the_chances_or_the_reason(db):
    import zlib
    d = d_kills()
    mw.apply(d, res_(), now=NOW)
    blob = json.loads(zlib.decompress(zlib.compress(json.dumps(d, default=str).encode())))   # what picklog saves (forecasts.inputs)
    assert blob["map_weighted"]["used"] and blob["map_weighted"]["snapshot"]["chances"]["Ancient"] == 68
    d2 = d_kills()
    mw.apply(d2, res_(pid=None), now=NOW)
    assert d2["map_weighted"]["reason"] == "no HLTV player id" and "snapshot" not in d2["map_weighted"]


def test_card_labels_used_and_shadow(db):
    d = d_kills()
    mw.apply(d, res_(), now=NOW)
    res = dict(res_(), grader_inputs=d, grade=grader.grade_leg(d))
    u = mw.card_status(res)
    assert u["used"] and u["label"] == "HLTV map chances used" and u["at"] == dt.datetime.fromisoformat(CAP).isoformat()
    assert u["projection"] == round(res["grade"]["lambda_final"], 2) and u["unweighted"] == round(d_kills()["perf"]["lambda"], 2)
    off = dict(res_(), grader_inputs=d_kills(), grade=grader.grade_leg(d_kills()))
    s = mw.card_status(off)
    per, _ = per_map_expected()
    assert s["used"] is False and s["label"] == "Shadow only – not in this grade"
    assert s["shadow"] == pytest.approx(round(2 * per, 2)) and s["projection"] == round(off["grade"]["lambda_final"], 2)
    assert mw.card_status(dict(off, hltv_outage={"rows": []})) is None          # a kept board grade: no label


def test_server_card_block_and_hub_block(db, monkeypatch):
    import server
    import workspace_api
    off = dict(res_(), grader_inputs=d_kills(), grade=grader.grade_leg(d_kills()))
    b = server.card_map_chances(off)
    assert b["weight"] == 0 and b["grade_use"]["label"].startswith("Shadow only") and b["grade_use"]["shadow"] is not None
    d = d_kills()
    mw.apply(d, res_(), now=NOW)
    b = server.card_map_chances(dict(res_(), grader_inputs=d, grade=grader.grade_leg(d)))
    assert b["weight"] == 1 and b["note"] is None and b["grade_use"]["used"] and b["grade_use"]["at"]
    sh = {"vm2": {"eligible": True, "vm-2": {"lam": 33.123}}}
    h = workspace_api._map_chances({"match_id": "hltv:2400124"}, {"gi": d_kills(), "shadow": sh, "lambda": 29.42})
    assert h["grade_use"] == {"used": False, "label": "Shadow only – not in this grade", "projection": 29.42, "shadow": 33.12}
    h = workspace_api._map_chances({"match_id": "hltv:2400124"}, {"gi": d, "shadow": sh, "lambda": 30.1})
    assert h["grade_use"]["used"] and h["grade_use"]["label"] == "HLTV map chances used" and h["weight"] == 1
    assert "grade_use" not in workspace_api._map_chances({"match_id": "hltv:2400124"})        # no saved forecast: block as before


def test_saved_forecast_read_returns_inputs_shadow_and_projection(tmp_path, monkeypatch):
    import sqlite3
    import zlib
    import workspace_api
    p = tmp_path / "p.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE forecasts (id INTEGER PRIMARY KEY, inputs BLOB, shadow TEXT, lambda REAL)")
    con.execute("INSERT INTO forecasts VALUES (7, ?, ?, 29.4)", (zlib.compress(json.dumps({"kpr": 0.7}).encode()), json.dumps({"vm2": {"eligible": False}})))
    con.commit()
    con.close()

    def rd():
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        return c
    srv = SimpleNamespace(picklog=SimpleNamespace(_read_db=rd))
    s = workspace_api._saved(srv, {"id": 7})
    assert s == {"lambda": 29.4, "gi": {"kpr": 0.7}, "shadow": {"vm2": {"eligible": False}}}
    assert workspace_api._context(srv, {"id": 7}, s)["rows"][0]["key"] == "kpr"
    assert workspace_api._saved(srv, {}) == {}


def test_pages_show_both_labels():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    hub = (ROOT / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    for src in (html, hub):
        body = src[src.index("function mapChancesUse"):]
        body = body[:body.index("\n}\n") if src is html else body.index("\n  }\n")]
        assert "u.used" in body and "captured" in body and "u.shadow" in body and "live projection" in body
        assert not any(h in body.lower() for h in tv.HEDGE)


# ---------------------------------------------------------------- 700 at once
def test_700_concurrent_flag_on_grades_share_one_read_each(db):
    db.delay = 0.2
    outs, errs = [], []

    def one():
        try:
            d = d_kills()
            outs.append(mw.apply(d, res_(), now=NOW)["used"])
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ths = [threading.Thread(target=one) for _ in range(700)]
    t0 = time.time()
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert not errs and outs == [True] * 700 and time.time() - t0 < 15
    assert len(db.calls) == 2                                                    # one map chances read + one player read


def test_700_cards_never_wait_on_a_slow_player_read(db):
    hmc.map_chances_snapshot("hltv:2400124")                                     # the card's display read is warm
    db.delay = 2.0
    off = dict(res_(), grader_inputs=d_kills(), grade=grader.grade_leg(d_kills()))
    outs = []
    ths = [threading.Thread(target=lambda: outs.append(mw.card_status(off))) for _ in range(700)]
    t0 = time.time()
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert time.time() - t0 < 2.0 + 2.0 and len(outs) == 700
    assert all(o["label"].startswith("Shadow only") for o in outs) and len(db.calls) == 2    # 1 display + 1 background player read
    for th in threading.enumerate():                                             # let the background read finish inside this test
        if th.name == "hltv-player-map-kills":
            th.join(5)
    assert mw.card_status(off)["shadow"] is not None                            # ... and the next card has the shadow number


# ---------------------------------------------------------------- per-cell switch (the registered rule promotes per cell)
def test_cells_env_limits_the_weighting_and_is_part_of_the_tag(db, monkeypatch):
    assert mw.cells({}) == mw.ALL_CELLS and len(mw.ALL_CELLS) == 6
    assert mw.cells({mw.CELLS_ENV: "kills:12, hs:12, bogus"}) == ("kills:12", "hs:12")
    rec = mw.apply(d_kills(), res_(), now=NOW, cell_set=("hs:12",))
    assert rec["used"] is False and rec["reason"] == "cell kills 12 not weighted (CS2_MAP_WEIGHTED_CELLS)"
    assert mw.apply(d_kills(), res_(), now=NOW, cell_set=("kills:12",))["used"] is True
    base = picklog.MODEL_VERSION
    assert mw.tag_on(base, ("kills:12",)) != mw.tag_on(base) and mw.tag_on(base, mw.ALL_CELLS) == mw.tag_on(base)
    monkeypatch.setenv(mw.FLAG, "1")
    monkeypatch.setenv(mw.CELLS_ENV, "kills:12")
    mw.activate()
    assert picklog.MODEL_VERSION == mw.tag_on(base, ("kills:12",))
    mw.deactivate()
    assert picklog.MODEL_VERSION == base
