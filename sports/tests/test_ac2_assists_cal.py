"""ac-2 (NBA_ASSISTS_CAL2): NBA assists displayed-chance calibration - zero intercept (side never flips), sums to one,
NBA assists only, cal-mi-1 wins when ON, flag 0 = the previous (raw) behaviour exactly, no look-ahead in the
gradecheck walk-forward, artifact validation."""
import json

import pytest

from sports import board_view
from sports.model import ac2, cal


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.delenv(ac2.FLAG, raising=False)
    monkeypatch.delenv("SPORTS_CAL_MI", raising=False)
    monkeypatch.delenv("SPORTS_CHANCE_CAL", raising=False)
    ac2.reset_cache()
    cal.reset_cache()
    yield
    ac2.reset_cache()
    cal.reset_cache()


def _cal_mi_weights(on, key="nba:assists"):
    params = {key: {"on": on, "M": [0.1, 0.5, 0.4], "S": [0.05, 0.7]}}
    obj = {"version": cal.VERSION, "params": params}
    obj["sha"] = cal._sha(params)
    p = cal.weights_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    cal.reset_cache()
    return obj


def test_committed_artifact_is_valid_and_default_on():
    obj, src = ac2.load(force=True)
    assert src == "default" and obj["version"] == "ac-2" and ac2.B_MIN <= obj["params"]["b"] < 1.0
    assert ac2.enabled()


def test_adjust_shrinks_keeps_side_sums_to_one_and_keeps_raw():
    obj, _ = ac2.load(force=True)
    b = obj["params"]["b"]
    for po, pu, pp in ((0.30, 0.66, 0.04), (0.72, 0.28, 0.0), (0.51, 0.49, 0.0), (0.02, 0.98, 0.0)):
        a = ac2.adjust("nba", "assists", po, pu, pp)
        assert abs(a["po"] + a["pu"] + a["pp"] - 1) < 1e-5 and a["pp"] == pytest.approx(pp)
        assert a["raw"] == [po, pu, pp] and a["v"] == f"ac-2:{obj['sha']}" and a["mode"] == "S"
        assert (a["po"] > a["pu"]) == (po > pu)                      # zero intercept: the side never changes
        ps = po / (po + pu)
        assert abs(a["po"] / (a["po"] + a["pu"]) - 0.5) <= abs(ps - 0.5) + 1e-9
        assert a["po"] / (1 - pp) == pytest.approx(ac2._sig(b * ac2._logit(ps)), abs=1e-5)


def test_only_nba_assists():
    for lg, st in (("nba", "points"), ("nba", "rebounds"), ("nba", "threes"), ("nfl", "assists"), ("nfl", "rec")):
        assert ac2.adjust(lg, st, 0.3, 0.7, 0.0) is None
    assert ac2.adjust("nba", "assists", None, 0.7, 0.0) is None


def test_flag_off_restores_the_previous_display_exactly(monkeypatch):
    monkeypatch.setenv(ac2.FLAG, "0")
    ac2.reset_cache()
    assert ac2.adjust("nba", "assists", 0.3, 0.7, 0.0) is None
    assert board_view.calibrated("nba", "assists", 0.3, 0.7, 0.0, "sm-1", None) is None   # raw chance shown
    monkeypatch.setenv(ac2.FLAG, "1")
    ac2.reset_cache()
    assert board_view.calibrated("nba", "assists", 0.3, 0.7, 0.0, "sm-1", None)["v"].startswith("ac-2:")


def test_board_order_cal_mi_wins_when_on_and_ac2_only_when_off():
    _cal_mi_weights(on=True)
    ca = board_view.calibrated("nba", "assists", 0.3, 0.7, 0.0, "sm-1", None)
    assert ca["v"].startswith("cal-mi-1:")
    _cal_mi_weights(on=False)
    ca = board_view.calibrated("nba", "assists", 0.3, 0.7, 0.0, "sm-1", None)
    assert ca["v"].startswith("ac-2:")
    # other NBA stats are untouched by ac-2 (raw when their cal-mi market is off / missing)
    assert board_view.calibrated("nba", "points", 0.3, 0.7, 0.0, "sm-1", None) is None
    # market-only / other model views are never calibrated
    assert board_view.calibrated("nba", "assists", 0.3, 0.7, 0.0, "mi-1", None) is None


def test_invalid_artifact_is_refused(tmp_path, monkeypatch):
    bad = {"version": "ac-2", "params": {"b": 1.7}}
    bad["sha"] = ac2._sha(bad["params"])
    p = tmp_path / "ac2.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(ac2, "ART_PATH", p)
    ac2.reset_cache()
    assert ac2.load(force=True) == (None, None)
    assert board_view.calibrated("nba", "assists", 0.3, 0.7, 0.0, "sm-1", None) is None
    good = {"version": "ac-2", "params": {"b": 0.5}}
    good["sha"] = "0" * 16                                   # sha mismatch
    p.write_text(json.dumps(good), encoding="utf-8")
    ac2.reset_cache()
    assert ac2.load(force=True) == (None, None)


def test_fit_b_recovers_a_shrunk_slope():
    import random
    rng = random.Random(7)
    rows = []
    for _ in range(4000):
        ps = rng.uniform(0.2, 0.8)
        q = ac2._sig(0.45 * ac2._logit(ps))
        rows.append({"ps": ps, "hit": 1.0 if rng.random() < q else 0.0})
    b = ac2.fit_b(rows, lam=0.0)
    assert 0.3 < b < 0.6
    assert ac2.fit_b(rows[:10]) is None                       # too few rows


def test_walk_q_uses_only_props_settled_before_the_day():
    rows = []
    for day, n in (("2026-11-01", 80), ("2026-11-02", 80), ("2026-11-03", 5)):
        for i in range(n):
            ps = 0.7 if i % 2 else 0.3
            win = (i % 4) in (0, 1)                             # half of the props hit Over -> slope well below 1
            rows.append({"fid": f"{day}-{i}", "league": "nba", "stat": "assists", "ps": ps, "line": 4.5,
                         "value": 6 if win else 3, "status": "win" if win else "loss",
                         "forecast_at": f"{day}T20:00:00Z", "settled_at": f"{day}T23:59:00Z"})
    rows.append({"fid": "pts", "league": "nba", "stat": "points", "ps": 0.7, "line": 20.5, "value": 30,
                 "status": "win", "forecast_at": "2026-11-03T20:00:00Z", "settled_at": "2026-11-03T23:59:00Z"})
    out = ac2.walk_q(rows, cal.game_day)
    assert not any(f.startswith("2026-11-01") for f in out)          # no earlier settled props -> raw
    assert any(f.startswith("2026-11-02") for f in out)              # fitted on 11-01 only (80 rows)
    assert "pts" not in out
    # 11-02 is fitted on 11-01 alone: changing 11-02 / 11-03 outcomes never changes 11-02's chance
    rows2 = [dict(r, value=9, status="win") if r["forecast_at"] >= "2026-11-02" and r["stat"] == "assists" else r
             for r in rows]
    out2 = ac2.walk_q(rows2, cal.game_day)
    assert all(out[f] == out2[f] for f in out if f.startswith("2026-11-02"))
    assert all(abs(q - 0.5) < 0.2 for q in out.values())


def test_gradecheck_scores_assists_at_the_ac2_chance(monkeypatch):
    from sports.model import gradecheck
    rows = []
    for day in ("2026-11-01", "2026-11-02"):
        for i in range(80):
            po = 0.7 if i % 2 else 0.3
            win = (i % 4) in (0, 1)
            rows.append({"fid": f"{day}-{i}", "league": "nba", "stat": "assists", "event": f"e-{day}", "po": po,
                         "pu": 1 - po, "pp": 0.0, "ps": po, "line": 4.5, "value": 6 if win else 3,
                         "status": "win" if win else "loss", "mean": 6.0 if po > 0.5 else 3.0,
                         "forecast_at": f"{day}T20:00:00Z", "settled_at": f"{day}T23:59:00Z"})
    q, info = gradecheck.ac2_walk_q(rows)
    assert info["enabled"] and info["rows_with_walk_forward_chance"] == 80
    gradecheck.score_rows(rows, q, labels={f: "ac-2" for f in q})
    assert {r["chance"] for r in rows if r["forecast_at"] >= "2026-11-02"} == {"ac-2"}
    assert {r["chance"] for r in rows if r["forecast_at"] < "2026-11-02"} == {"raw"}
    monkeypatch.setenv(ac2.FLAG, "0")
    assert gradecheck.ac2_walk_q(rows) == ({}, {"version": "ac-2", "enabled": False})


def test_points_noletter_owner_option_default_off(monkeypatch):
    monkeypatch.delenv("NBA_POINTS_NOLETTER", raising=False)
    assert not board_view.points_noletter("nba", "points")
    monkeypatch.setenv("NBA_POINTS_NOLETTER", "1")
    assert board_view.points_noletter("nba", "points")
    assert not board_view.points_noletter("nba", "assists") and not board_view.points_noletter("nfl", "points")
