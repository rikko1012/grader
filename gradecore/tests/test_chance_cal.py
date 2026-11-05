"""ec-1 (gradecore/chance_cal.py): calibrated printed chance, fitted on older settled forecasts, tested on later ones."""
import json
import math
import random

import pytest

from gradecore import calibrate, chance_cal as cc


def _rows(n, start_day=1, days=20, game="cs2", stat="kills", seed=1, hit_of=lambda p: 0.5):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        d = start_day + i % days
        p = 0.5 + rng.random() * 0.3
        side = "over" if i % 2 else "under"
        out.append({"game": game, "stat": stat, "side": side, "p": p, "win": float(rng.random() < hit_of(p)),
                    "match": f"m{d}-{i % 7}", "t": f"2026-10-{d:02d}T12:00:00+00:00"})
    return out


def test_the_committed_artifact_is_valid_and_covers_both_games():
    obj = cc.load(force=True)
    assert obj is not None and obj["version"] == cc.VERSION and set(obj["games"]) == {"cs2", "lol"}
    for g, d in obj["games"].items():
        assert d["letters"] in ("live", "held") and d["verdict"] in ("pass", "fail")
        assert (d["letters"] == "live") == (d["verdict"] == "pass")
        for side in cc.SIDES:
            assert f"*|{side}" in d["tables"]
        assert d["test"]["from"] and d["test"]["summary"]["n"] > 0


def test_tampering_or_a_falling_table_is_rejected():
    obj = json.loads(cc.PATH.read_text(encoding="utf-8"))
    bad = json.loads(json.dumps(obj))
    bad["games"]["cs2"]["letters"] = "live" if obj["games"]["cs2"]["letters"] == "held" else "held"
    with pytest.raises(ValueError):
        cc.validate(bad)
    bad = json.loads(json.dumps(obj))
    bad["games"]["cs2"]["tables"]["*|over"] = {"kind": "isotonic", "x": [0.5, 0.6], "y": [0.6, 0.5]}     # falling
    bad["sha"] = cc._sha(bad["games"])
    with pytest.raises(ValueError):
        cc.validate(bad)
    bad["games"]["cs2"]["tables"]["*|over"] = {"kind": "scale", "a": 1.3}                              # sharpening
    bad["sha"] = cc._sha(bad["games"])
    with pytest.raises(ValueError):
        cc.validate(bad)


def test_off_means_no_calibrated_chance(monkeypatch):
    monkeypatch.delenv(cc.FLAG, raising=False)
    assert not cc.enabled() and cc.side_chance("cs2", "kills", "over", 0.7) is None and cc.pair("lol", "kills", 0.7) is None
    assert not cc.held("lol")
    monkeypatch.setenv(cc.FLAG, "0")
    assert not cc.enabled() and cc.side_chance("cs2", "kills", "over", 0.7) is None


def test_pure_python_apply_matches_the_numpy_one():
    rng = random.Random(3)
    p = [0.5 + rng.random() * 0.4 for _ in range(1500)]
    y = [float(rng.random() < 0.45 + 0.3 * (x - 0.5)) for x in p]
    for f in (calibrate.fit(p, y, "k", None, 300), calibrate.fit(p[:500], y[:500], "k", None, 300)):
        assert f["kind"] in ("isotonic", "platt")
        for x in (0.3, 0.5, 0.55, 0.61, 0.73, 0.9, 0.99):
            assert abs(cc.apply_fit(f, x) - calibrate.apply(f, x)) < 1e-9


def test_side_chance_never_below_the_floor_and_never_falls_as_the_chance_rises(monkeypatch):
    monkeypatch.setenv(cc.FLAG, "1")
    for game, stats in (("cs2", ("kills", "hs")), ("lol", ("kills", "assists", "deaths"))):
        for stat in stats:
            for side in cc.SIDES:
                prev = 0
                for i in range(50, 100):
                    c = cc.side_chance(game, stat, side, i / 100)
                    assert cc.FLOOR <= c <= cc.CEIL and c >= prev - 1e-12
                    prev = c
                o, u = cc.pair(game, stat, 0.7, None)
                assert abs(o + u - 1) < 1e-9 and o >= 0.5                       # the model's side is kept


def test_held_follows_the_artifact_and_the_switch(monkeypatch):
    obj = cc.load(force=True)
    monkeypatch.setenv(cc.FLAG, "1")
    for g in cc.GAMES:
        assert cc.held(g) == (obj["games"][g]["letters"] == "held")


def test_walk_forward_fits_only_on_matches_settled_before_the_day():
    """Every row on or after the test day is a win: if any of them leaked into a fit, the fitted tables would move."""
    old = _rows(1200, 1, 20, seed=5)
    new = [dict(r, win=1.0, t=f"2026-10-{25 + i % 3:02d}T12:00:00+00:00", match=f"new{i}") for i, r in enumerate(_rows(300, seed=6))]
    scored = cc.walk_forward(old + new, "2026-10-25")
    assert {r["t"][:10] for r, _, _ in scored} == {"2026-10-25", "2026-10-26", "2026-10-27"}
    base = cc.fit_tables(old)
    for r, c, k in scored:
        if r["t"].startswith("2026-10-25"):                                # day 1: nothing new is known yet
            assert abs(c - max(cc.FLOOR, cc.apply_fit(base[k], r["p"]))) < 1e-12
    late = [x for x in old + new if x["t"] < "2026-10-26T12:00:00"]
    # a row 6 h before the cut is still out (its match may not have settled): 2026-10-25T12:00 + 6 h < 2026-10-26 00:00
    assert all(cc._ts(x["t"]) < cc._ts("2026-10-26T00:00:00+00:00") for x in late if x["t"].startswith("2026-10-25"))


def test_verdict_is_ct3s_calibration_rule():
    def summ(n, stated, hit, n_side=(200, 200), gaps=(0.0, 0.0), ll=(0.69, 0.68)):
        return {"all": {"calibrated": {"n": n, "stated": stated, "hit": hit, "gap": hit - stated, "log_loss": ll[1]},
                        "raw": {"log_loss": ll[0]}},
                "sides": {s: {"calibrated": {"n": k, "stated": 0.52, "hit": 0.52 + g, "gap": g}}
                          for s, k, g in zip(cc.SIDES, n_side, gaps)}}
    assert cc.verdict(summ(400, 0.52, 0.53))[0] == "pass"
    assert cc.verdict(summ(299, 0.52, 0.53))[0] == "fail"                    # too few held-out forecasts
    assert cc.verdict(summ(400, 0.52, 0.56))[0] == "fail"                    # overall gap
    assert cc.verdict(summ(400, 0.52, 0.53, gaps=(0.0, -0.03)))[0] == "fail"  # one side off: opposite errors never cancel
    assert cc.verdict(summ(400, 0.52, 0.53, n_side=(50, 350)))[0] == "fail"   # a side under 60
    assert cc.verdict(summ(400, 0.52, 0.53, ll=(0.68, 0.69)))[0] == "fail"   # worse than the raw chance


def test_build_refits_on_everything_and_takes_the_held_out_verdict():
    rows = _rows(3000, 1, 28, hit_of=lambda p: 0.52)
    obj, ev = cc.build({"cs2": rows}, {"cs2": "test"})
    assert obj["games"]["cs2"]["letters"] == ("live" if ev["cs2"]["verdict"] == "pass" else "held")
    assert obj["games"]["cs2"]["variant"] == ev["cs2"]["served"]
    assert obj["games"]["cs2"]["tables"] == cc.fit_tables(rows, variant=ev["cs2"]["served"])
    assert set(ev["cs2"]["variants"]) == {"zero", "shrunk", "free", "raw"}
    s = ev["cs2"]["summary"]
    # an overconfident model (52% hit whatever it says) is pulled down: calibrated stated close to the hit rate
    assert abs(s["all"]["calibrated"]["stated"] - s["all"]["calibrated"]["hit"]) < abs(s["all"]["raw"]["stated"] - s["all"]["raw"]["hit"])
    assert s["summary"]["log_loss_cal"] <= s["summary"]["log_loss_raw"]


def test_intercept_zero_is_served_unless_a_shift_wins_with_a_clustered_interval_below_zero():
    """Coordinator rule 2026-11-05 (the NFL intercept flipped 641 sides to Under): slope-only shrink toward 50%, 0 flips."""
    # a model that is right about direction but overconfident, and an Under-heavy level shift in the outcomes
    rows = _rows(4000, 1, 28, hit_of=lambda p: 0.5 + 0.4 * (p - 0.5))
    t = cc.fit_tables(rows, variant="zero")
    assert all(f["kind"] == "scale" and 0 <= f["a"] <= 1 for f in t.values())
    for f in t.values():
        for p in (0.5, 0.55, 0.7, 0.95):
            assert 0.5 - 1e-12 <= cc.apply_fit(f, p) <= p + 1e-12               # toward 50%, never past it: no side flip
    uf = []
    cc.walk_forward(rows, "2026-10-25", variant="zero", unfloored=uf)
    assert uf and min(uf) >= 0.5 - 1e-12
    ev = cc.evaluate({"cs2": rows}, "2026-10-25")["cs2"]
    assert ev["variants"]["zero"]["side_flips_if_unfloored"] == 0 and ev["variants"]["zero"]["side_flips_served"] == 0
    comp = ev["selection"]
    if ev["served"] != "zero":
        assert comp[f"{ev['served']}_minus_zero"]["ci"][1] < 0
    else:
        assert not any(c["beats_zero"] for c in comp.values())


def test_select_needs_the_whole_interval_below_zero():
    r = [{"win": 1.0, "match": f"m{i % 40}"} for i in range(400)]
    zero = [(x, 0.55, "k") for x in r]
    tiny = [(x, 0.5501, "k") for x in r]                    # better by a hair on every row: interval below zero
    worse = [(x, 0.54, "k") for x in r]
    assert cc.select({"zero": zero, "free": tiny, "shrunk": worse})[0] == "free"
    assert cc.select({"zero": zero, "free": worse, "shrunk": worse})[0] == "zero"


def test_rows_from_sources_use_the_raw_chance_and_drop_pushes():
    lol = cc.rows_from_lol([
        {"s_status": "graded", "s_push": 0, "s_over_hit": 1, "p_over": 0.3, "p_under": 0.6, "p_push": 0.1, "cal_p_over": 0.9,
         "stat": "kills", "sid": "s1", "series_start": "2026-10-20T10:00:00Z"},
        {"s_status": "push", "s_push": 1, "s_over_hit": None, "p_over": 0.6, "p_under": 0.4, "stat": "kills", "sid": "s1",
         "series_start": "2026-10-20T10:00:00Z"},
        {"s_status": "dnp", "s_push": 0, "s_over_hit": None, "p_over": 0.6, "p_under": 0.4, "stat": "kills", "sid": "s1",
         "series_start": "2026-10-20T10:00:00Z"}])
    assert len(lol) == 1 and lol[0]["side"] == "under" and abs(lol[0]["p"] - 0.6 / 0.9) < 1e-12 and lol[0]["win"] == 0.0
    cs2 = cc.rows_from_cs2_p1({"rows": [{"p": 0.42, "over": 0, "prop": "hs", "match": "hltv:1", "start": "2026-10-20T10:00:00+00:00"},
                                        {"p": 0.6, "over": None, "prop": "hs", "match": "hltv:1", "start": "2026-10-20T10:00:00+00:00"}]})
    assert cs2 == [{"game": "cs2", "stat": "hs", "side": "under", "p": pytest.approx(0.58), "win": 1.0, "match": "hltv:1",
                    "t": "2026-10-20T10:00:00+00:00", "book": None, "scope": "12", "version": None}]


def test_the_module_imports_no_numpy_until_it_fits():
    import ast
    tree = ast.parse(cc.PATH.with_name("chance_cal.py").read_text(encoding="utf-8"))
    top = {a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    top |= {n.module for n in tree.body if isinstance(n, ast.ImportFrom)}
    assert "numpy" not in top and not math.isnan(cc.FLOOR)
