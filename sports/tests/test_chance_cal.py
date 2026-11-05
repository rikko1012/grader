"""pc-1 (REGISTER A10, owner decision 2026-11-05): the printed NBA / NFL chance is the chronologically fitted,
held-out-tested calibrated chance - default on, env "0" off, raw kept beside it, letters from the calibrated chance,
a league that failed its held-out test keeps its letters held, no look-ahead in the fit or the daily walk-forward."""
import json
from pathlib import Path

import pytest

from sports import board_view, containment, snapshots
from sports.model import cal, chance_cal, grade_policy
from sports.slips import pool

COMMITTED = Path(chance_cal.__file__).resolve().parent / "artifacts" / "chance_cal_pc1.json"


def _obj(nba=("platt", [-0.05, 0.3], "contain-rule"), nfl=("platt", [-0.08, 0.13], "held"), stats=None):
    lgs = {}
    for lg, v in (("nba", nba), ("nfl", nfl)):
        if v:
            lgs[lg] = {"spec": v[0], "theta": v[1], "stats": (stats or {}).get(lg, {}), "letters": v[2]}
    o = {"version": chance_cal.VERSION, "leagues": lgs}
    o["sha"] = chance_cal._sha(chance_cal._serving(lgs))
    return o


def _write(obj):
    p = chance_cal.weights_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    chance_cal.reset_cache()
    containment.reset_cache()
    return p


# ---------------------------------------------------------------------------------------------- committed artifact
def test_committed_artifact_is_valid_default_on_and_env_zero_turns_it_off(monkeypatch):
    obj = chance_cal.validate(json.loads(COMMITTED.read_text(encoding="utf-8")))
    assert set(obj["leagues"]) == {"nba", "nfl"}
    for lg, p in obj["leagues"].items():
        ho = p["held_out"]
        assert ho["fit_window"][1] < ho["test_window"][0]          # chronological: fit strictly before the test
        assert p["letters"] == ("contain-rule" if ho["pass"] else "held")
    monkeypatch.setattr(chance_cal, "DEFAULT_PATH", COMMITTED)
    chance_cal.reset_cache()
    got, src = chance_cal.load(force=True)
    assert src == "default" and got["sha"] == obj["sha"]           # no env needed: on by default
    a = chance_cal.adjust("nfl", "rec_yds", 0.80, 0.20, 0.0)
    assert a["mode"] == "P" and a["v"] == f"pc-1:{obj['sha']}" and a["po"] < 0.80
    monkeypatch.setenv("SPORTS_CHANCE_CAL", "0")
    chance_cal.reset_cache()
    assert chance_cal.load(force=True) == (None, None) and chance_cal.adjust("nfl", "rec_yds", 0.8, 0.2, 0) is None
    assert not chance_cal.letters_held("nba")


def test_served_parameters_have_no_shift_so_the_side_never_flips():
    """Coordinator review 2026-11-05: intercept 0 unless a shift clearly beats it on held-out log loss; with no shift
    the calibrated side is always the model's side (no flip against the projected mean from calibration alone)."""
    obj = json.loads(COMMITTED.read_text(encoding="utf-8"))
    rep = json.loads((Path(chance_cal.__file__).resolve().parents[1] / "research" /
                      "pc1_heldout_2026-11-05.json").read_text(encoding="utf-8"))
    for lg, p in obj["leagues"].items():
        v = rep["leagues"][lg]["intercept_variants"]
        assert set(v) == set(chance_cal.ICPT_MODES) and v["zero"]["side_flips_vs_raw"] == 0
        if p["intercept"] == "zero":
            assert p["theta"][0] == 0 and all(t[0] == 0 for t in p["stats"].values())
        else:                                                      # a shift is served only under ICPT_RULE
            assert v[p["intercept"]]["log_loss_minus_intercept0"][2] < 0
        for po in [i / 100 for i in range(1, 100)]:
            a = chance_cal.adjust(lg, "x", po, 1 - po, 0.0, weights=obj)
            if p["intercept"] == "zero" and po != 0.5:
                assert (a["po"] > 0.5) == (po > 0.5)
    bad = _obj(nba=("platt", [0.1, 0.3], "contain-rule"))
    bad["leagues"]["nba"]["intercept"] = "zero"
    with pytest.raises(ValueError):
        chance_cal.validate(bad)


def test_intercept_zero_fit_keeps_no_shift_even_on_a_one_sided_sample():
    rows = _rows(per_day=400)
    for r in rows:
        r["hit"] = 0.0 if r["ps"] < 0.7 else r["hit"]              # an "Under night": Overs mostly lose
    assert chance_cal.fit(rows, "platt", 100.0, "zero")["theta"][0] == 0
    assert chance_cal.fit(rows, "platt", None, "free")["theta"][0] < -0.3
    g = chance_cal.fit(rows, "platt", None, "games")["theta"][0]
    assert chance_cal.fit(rows, "platt", None, "free")["theta"][0] < g < 0   # shrunk by game count


def test_committed_report_matches_the_artifact():
    rep = json.loads((Path(chance_cal.__file__).resolve().parents[1] / "research" /
                      "pc1_heldout_2026-11-05.json").read_text(encoding="utf-8"))
    obj = json.loads(COMMITTED.read_text(encoding="utf-8"))
    for lg, p in obj["leagues"].items():
        r = rep["leagues"][lg]
        assert r["verdict"]["pass"] == p["held_out"]["pass"]
        assert r["served"]["theta"] == p["theta"] and r["n_test"] == p["held_out"]["n_test"]


# ---------------------------------------------------------------------------------------------- serving maths
def test_adjust_sums_to_one_keeps_push_and_raw_and_is_monotone():
    w = _obj()
    prev = None
    for po in (0.02, 0.2, 0.45, 0.5, 0.55, 0.8, 0.98):
        pp = 0.04
        pu = 1 - pp - po if po < 1 - pp else 0.0
        a = chance_cal.adjust("nba", "points", po, pu, pp, weights=w)
        assert abs(a["po"] + a["pu"] + a["pp"] - 1) < 1e-5 and a["pp"] == pytest.approx(pp)
        assert a["raw"] == [round(po, 6), round(pu, 6), pp]
        if prev is not None:
            assert a["po"] >= prev
        prev = a["po"]
    a = chance_cal.adjust("nba", "points", 0.8, 0.2, 0.0, weights=w)
    q = chance_cal._sig(-0.05 + 0.3 * chance_cal._logit(0.8))
    assert a["po"] == pytest.approx(q, abs=1e-6) and a["po"] < 0.6        # a raw 80% prints well under 60%
    assert chance_cal.adjust("cs2", "kills", 0.8, 0.2, 0.0, weights=w) is None   # not an NBA / NFL league


def test_split_spec_and_per_stat_parameters():
    w = _obj(nba=("split", [0.0, 0.5, 0.1], "contain-rule"), stats={"nba": {"threes": [0.0, 1.0, 1.0]}})
    over = chance_cal.adjust("nba", "points", 0.8, 0.2, 0.0, weights=w)["po"]
    under = chance_cal.adjust("nba", "points", 0.2, 0.8, 0.0, weights=w)["pu"]
    assert over == pytest.approx(chance_cal._sig(0.5 * chance_cal._logit(0.8)), abs=1e-6)
    assert under == pytest.approx(1 - chance_cal._sig(0.1 * chance_cal._logit(0.2)), abs=1e-6) and under < over
    assert chance_cal.adjust("nba", "threes", 0.8, 0.2, 0.0, weights=w)["po"] == pytest.approx(0.8, abs=1e-6)


def test_validation_rejects_bad_files_and_falls_back_to_default(monkeypatch, tmp_path):
    good = _obj()
    for bad in ({**good, "sha": "0" * 16}, {**good, "version": "pc-0"},
                {**good, "leagues": {"nba": {"spec": "platt", "theta": [0, 99], "stats": {}, "letters": "held"}}},
                {**good, "leagues": {"nba": {"spec": "iso", "theta": [0, 1], "stats": {}, "letters": "held"}}},
                {**good, "leagues": {"nba": {"spec": "platt", "theta": [0, 1], "stats": {}, "letters": "on"}}}):
        with pytest.raises(ValueError):
            chance_cal.validate(bad)
    d = tmp_path / "default.json"
    d.write_text(json.dumps(good), encoding="utf-8")
    monkeypatch.setattr(chance_cal, "DEFAULT_PATH", d)
    _write({**good, "sha": "bad"})
    assert chance_cal.load(force=True)[1] == "default"
    _write(_obj(nba=("platt", [0.0, 0.5], "held")))
    assert chance_cal.load(force=True)[1] == "file"


# ---------------------------------------------------------------------------------------------- wiring
def _fc(fid, po, pu, mu, model="sm-1", line=24.5):
    return {"forecast_id": fid, "model_version": model, "kind": "scout" if model == "sm-1" else "market",
            "p_over": po, "p_under": pu, "p_push": 0.0, "projection_mean": mu, "line": line,
            "forecast_at": "2026-10-24T12:00:00Z"}


def _row(quotes, league="nba", stat="points"):
    return {"key": f"{league}:espn:9|p1|{stat}|full_game", "event_id": f"{league}:espn:9", "player_id": "p1",
            "player": "P", "stat": stat, "quotes": quotes, "kalshi": [], "alternates": [], "market": None}


def _q(rid="r1", fid="f1", line=24.5):
    return {"platform": "underdog", "record_id": rid, "line": line, "forecast_id": fid, "variant": "standard",
            "last_seen_at": "T", "source_updated_at": None, "gradable": True, "sides": []}


def test_pc1_supersedes_cal_mi_and_off_switch_restores_it(monkeypatch):
    params = {"nba:points": {"on": True, "M": [0.0, 1.0, 0.0], "S": [0.3, 1.0]}}
    cobj = {"version": cal.VERSION, "params": params, "sha": cal._sha(params)}
    cp = cal.weights_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(cobj), encoding="utf-8")
    cal.reset_cache()
    _write(_obj())
    a = board_view.calibrated("nba", "points", 0.7, 0.3, 0.0, "sm-1", None)
    assert a["mode"] == "P" and a["v"].startswith("pc-1:")
    assert board_view.calibrated("nba", "points", 0.7, 0.3, 0.0, "mi-1", None) is None   # only the fitted model
    monkeypatch.setenv("SPORTS_CHANCE_CAL", "0")
    chance_cal.reset_cache()
    b = board_view.calibrated("nba", "points", 0.7, 0.3, 0.0, "sm-1", None)
    assert b["mode"] == "S" and b["v"].startswith("cal-mi-1:")
    cal.reset_cache()


def test_board_quote_prints_calibrated_chance_and_letter_comes_from_it(tmp_path, monkeypatch):
    _write(_obj(nba=("platt", [0.0, 0.25], "contain-rule")))
    fcs = {"f1": _fc("f1", 0.80, 0.20, 28.0)}
    q = _q()
    row = _row([q])
    view = {"lines": {"24.5": [7, 3, 0, 10, 4, 1, 0, 5]}, "played": 30}
    snapshots.guard_row("nba", row, fcs, view, "2026-11-05T12:00:00Z")
    ca = q["cal"]
    want = chance_cal._sig(0.25 * chance_cal._logit(0.8))
    assert ca["po"] == pytest.approx(want, abs=1e-6) and ca["raw"][:2] == [0.8, 0.2] and ca["f"] == "f1"
    assert fcs["f1"]["p_over"] == 0.80                                 # the forecast record keeps the raw chance
    assert q["x"]["ln"] == "over" and q["x"]["pl"] == pytest.approx(round(ca["po"], 4))
    assert q["x"]["gr"] == grade_policy.band(ca["po"]) and q["x"]["gr"] != grade_policy.band(0.80)
    cq = snapshots.compact_quote(q)
    assert cq["ca"]["po"] == ca["po"]
    s = snapshots.write_cal_index(tmp_path / "snaps" / "nba", {"league": "nba"}, [{"quotes": [q], "kalshi_near": []}])
    assert s["pc1"]["quotes"] == 1 and s["pc1"]["letters"] == "contain-rule"
    from sports.model import shown                                    # what members saw is stored with the raw
    import sqlite3
    con = sqlite3.connect(shown.path())
    po, raw_o, v = con.execute("SELECT p_over, raw_over, cal_version FROM shown WHERE forecast_id='f1'").fetchone()
    con.close()
    assert po == ca["po"] and raw_o == 0.8 and v.startswith("pc-1:")
    assert pool.cal_by_record([{"markets": [{"q": [cq]}]}])["r1"]["po"] == ca["po"]   # slips read the same number


def test_sub_threshold_rows_stay_lean_not_no_bet():
    _write(_obj(nba=("platt", [0.0, 0.1], "contain-rule")))
    fcs = {"f1": _fc("f1", 0.70, 0.30, 27.0)}
    q = _q()
    snapshots.guard_row("nba", _row([q]), fcs, {"lines": {"24.5": [6, 4, 0, 10, 3, 2, 0, 5]}, "played": 30}, "T")
    assert q["cal"]["po"] < grade_policy.BE_LOW
    assert q["x"]["ln"] == "over" and q["x"]["gr"] == "Lean"
    assert grade_policy.apply(q["cal"]["po"], q["cal"]["pu"], ("over", "under"), "forward-validated",
                              stake_gate_passed=True)["label"] == "Lean Over"


def test_failed_held_out_league_keeps_letters_held_but_prints_calibrated_chance():
    _write(_obj(nba=("platt", [0.0, 0.5], "contain-rule"), nfl=("platt", [0.0, 0.5], "held")))
    assert containment.contained("nfl", "rec_yds") and not containment.contained("nba", "points")
    assert containment.snapshot()["pc1_held"] == {"nba": False, "nfl": True}
    fcs = {"f1": _fc("f1", 0.80, 0.20, 40.0, line=30.5)}
    q = _q(line=30.5)
    snapshots.guard_row("nfl", _row([q], "nfl", "rec_yds"), fcs, None, "T")
    assert q["x"]["b"] == "gate" and "gr" not in q["x"]
    assert q["cal"]["po"] == pytest.approx(chance_cal._sig(0.5 * chance_cal._logit(0.8)), abs=1e-6)


def test_pc1_never_releases_a_contained_league(monkeypatch):
    _write(_obj(nba=("platt", [0.0, 0.5], "contain-rule")))
    monkeypatch.setenv("SPORTS_CONTAIN_NBA", "1")
    containment.reset_cache()
    assert containment.contained("nba", "points")


def test_tracked_pick_source_names_the_calibration():
    import inspect
    from sports import picks
    assert 'str(ca.get("v") or "cal-mi-1").split(":")[0]' in inspect.getsource(picks)


# ---------------------------------------------------------------------------------------------- fitting: no leakage
def _rows(n_days=8, per_day=200, seed=3, slope=0.3, test_flip=False, from_day=6):
    import random
    rng = random.Random(seed)
    out = []
    for d in range(n_days):
        day = f"2026-10-{10 + d:02d}"
        for k in range(per_day):
            ps = rng.uniform(0.15, 0.85)
            true = chance_cal._sig(slope * chance_cal._logit(ps))
            hit = 1.0 if rng.random() < true else 0.0
            if test_flip and d >= from_day:
                hit = 1.0 - hit
            out.append({"league": "nba", "stat": ("points", "assists")[k % 2], "ps": ps, "hit": hit, "day": day,
                        "event": f"g{d}-{k % 5}", "forecast_at": day + "T20:00:00Z",
                        "settled_at": day + "T23:59:00Z"})
    return out


def test_fit_recovers_a_shrinking_slope():
    m = chance_cal.fit(_rows(per_day=1500), "platt", None)
    assert 0.15 < m["theta"][1] < 0.45 and abs(m["theta"][0]) < 0.1


def test_held_out_labels_never_change_the_tested_fit_or_the_spec_choice():
    a, ka = chance_cal.evaluate_league(_rows(), "2026-10-16")
    b, kb = chance_cal.evaluate_league(_rows(test_flip=True), "2026-10-16")
    assert ka == kb and a["fitted_on_fit_window"] == b["fitted_on_fit_window"]
    assert a["spec_choice"] == b["spec_choice"] and a["n_test"] == 400
    assert a["calibrated"]["scores"]["log_loss"] != b["calibrated"]["scores"]["log_loss"]
    assert a["spec_choice"]["inner_eval_days"][-1] < "2026-10-16"


def test_report_has_bins_with_intervals_per_side():
    rep, _ = chance_cal.evaluate_league(_rows(per_day=400), "2026-10-16")
    rel = rep["raw"]["reliability"]
    for s in ("all", "over", "under"):
        assert rel[s]["n"] > 0 and rel[s]["bins"]
        for b in rel[s]["bins"]:
            assert b["ci"][0] <= b["hit"] <= b["ci"][1] and b["bin"].count("-") == 1
    assert rel["over"]["n"] + rel["under"]["n"] == rel["all"]["n"]
    assert set(rep["verdict"]["checks"]) >= {"log_loss_better", "in_the_large", "over_side", "under_side"}
    assert rep["raw"]["scores"]["brier"] > 0 and "log_loss" in rep["calibrated"]["scores"]


def test_build_marks_failing_league_held(monkeypatch):
    rows = _rows()

    def export(rows, flip_test):
        out = []
        for r in rows:
            hit = r["hit"]
            if flip_test and r["day"] >= "2026-10-16":
                hit = 1.0 - hit                                   # the model is anti-signal in the held-out window
            side = "over" if r["ps"] >= 0.5 else "under"
            p = r["ps"] if side == "over" else 1 - r["ps"]
            win = (hit == 1.0) == (side == "over")
            out.append({"sport": "nba", "market": r["stat"], "side": side, "p": p, "status": "win" if win else "loss",
                        "match_key": r["event"], "forecast_at": r["forecast_at"], "start_at": r["day"] + "T23:00:00Z",
                        "settled_at": r["settled_at"]})
        return {"rows": out}
    obj, rep = chance_cal.build({"nba": export(_rows(slope=1.0, per_day=400), True)}, "2026-10-16")
    assert obj["leagues"]["nba"]["letters"] == "held" and not rep["leagues"]["nba"]["verdict"]["pass"]


# ---------------------------------------------------------------------------------------------- daily grade check
def test_gradecheck_scores_the_walk_forward_pc1_chance_without_look_ahead(tmp_path):
    from sports.tests.test_gradecheck import World, _run
    w = World(tmp_path)
    for g in range(4):                                                 # day 1: 320 props (fit material)
        for k in range(80):
            po = 0.75 if k % 2 else 0.30
            over = (k % 4 != 0) if po > 0.5 else (k % 3 == 0)
            w.prop(f"nba:espn:d1g{g}", po, 1 - po, mean=12 if po > 0.5 else 9, value=14 if over else 9,
                   start="2026-10-23T23:00:00Z")
    for k in range(30):                                                # day 2: scored with day-1 parameters only
        w.prop("nba:espn:d2g0", 0.75, 0.25, mean=12, value=14 if k % 2 else 9, start="2026-10-25T23:00:00Z")
    w.close()
    _write(_obj())
    r = _run(w)
    assert r["counts"]["chance_pc1"] == 30 and r["cal_mi"]["pc1"]["game_days_fitted"] == 1
    assert r["cal_mi"]["pc1"]["rows_with_walk_forward_chance"] == 30
    chance_cal.weights_path().unlink()
    chance_cal.reset_cache()
    assert _run(w)["counts"]["chance_pc1"] == 0                        # pc-1 off: the previous rule
