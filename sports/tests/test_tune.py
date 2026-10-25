"""tw-1 walk-forward fine-tuning (REGISTER.md A7): registration before results, no look-ahead (refits and calibration
layers), frozen-artifact hashes, the forward layer = the replay path, results replay from the packaged history, bl-1
book-line calibration uses only props settled before each forecast."""
import gzip
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sports.model import datasets, dists, forward, shadow, study, tune

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path_factory.mktemp("tw1") / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    shadow.install_extras(db)
    return db


def _git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)


# --------------------------------------------------------------------------------------------- registration
def test_a7_registered_before_any_tw1_result():
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone (CI): commit order is not visible")
    reg = _git("log", "-S", "Amendment A7", "--format=%H", "--", "sports/research/REGISTER.md").stdout.split()
    for sport in ("nfl", "nba"):
        res = _git("log", "--diff-filter=A", "--format=%H", "--",
                   f"sports/research/results/model_tw1_{sport}.json").stdout.split()
        if not reg or not res:
            pytest.skip("git history not available")
        assert reg[-1] != res[-1] and _git("merge-base", "--is-ancestor", reg[-1], res[-1]).returncode == 0
    # the code that scores was not committed before the registration either
    code = _git("log", "--diff-filter=A", "--format=%H", "--", "sports/model/tune.py").stdout.split()
    assert code and _git("merge-base", "--is-ancestor", reg[-1], code[-1]).returncode == 0


# --------------------------------------------------------------------------------------------- layers
def _toy_w(n_orig=12, per=40, seed=1):
    rng = np.random.default_rng(seed)
    lo, ks = dists.grid("rec")
    mu = rng.uniform(2, 6, n_orig * per)
    pmf = dists.negbin(mu, 0.1, lo, ks)
    y = rng.poisson(mu * 1.1).astype(float)
    org = np.repeat(np.arange(n_orig), per)
    start = np.datetime64("2025-09-01T17:00:00") + (org * 7).astype("timedelta64[D]")
    t = start - np.timedelta64(3600, "s")
    return {"y": y, "origin": org, "start": start, "t": t, "lag_h": 6.0}, pmf, lo, ks


def test_layer_uses_only_earlier_origins():
    w, pmf, lo, ks = _toy_w()
    P1, cs1 = tune._layer_series(w, pmf, lo, ks, "rc", 28.0)
    w2 = dict(w, y=w["y"].copy())
    w2["y"][w["origin"] >= 8] += 50                       # change the outcomes of LATER origins only
    P2, cs2 = tune._layer_series(w2, pmf, lo, ks, "rc", 28.0)
    early = w["origin"] < 9                               # origin 8's layer may not see origin 8's own outcomes
    np.testing.assert_array_equal(P1[early], P2[early])
    np.testing.assert_array_equal(cs1[early], cs2[early])
    assert not np.array_equal(cs1[w["origin"] >= 9], cs2[w["origin"] >= 9])
    # fewer than MIN_RC earlier rows -> no layer (live unchanged)
    assert (cs1[w["origin"] < 5] == 1.0).all()
    D1, dc = tune._layer_series(w, pmf, lo, ks, "dr", 2)
    w3 = dict(w, y=w["y"].copy())
    w3["y"][w["origin"] <= 3] += 50                       # dr sees only the previous k = 2 game-weeks
    D3, dc3 = tune._layer_series(w3, pmf, lo, ks, "dr", 2)
    np.testing.assert_array_equal(dc[w["origin"] >= 6], dc3[w["origin"] >= 6])


def test_rc_removes_bias_and_fast_transform_matches():
    w, pmf, lo, ks = _toy_w()
    c, s = tune.fit_rc(pmf, lo, ks, w["y"], np.ones(len(w["y"])))
    assert 1.05 < c < 1.15                                # outcomes were drawn at 1.1 x the mean
    mu = dists.mean_of(pmf, ks)
    np.testing.assert_allclose(tune.fast_transform(pmf, lo, ks, mu, c, s), dists.transform(pmf, lo, ks, mu, c, s),
                               atol=1e-12)
    assert tune.weights(np.array([0.0, 28.0]), 28.0).tolist() == [1.0, 0.5]


def test_walk_forward_refits_never_see_the_origin(hist, monkeypatch):
    con = datasets.connect(hist, read_only=True)
    cfg = json.loads((tune.ART_DIR / "model_config_nfl.json").read_text(encoding="utf-8"))["markets"]["rush_att"]
    df = tune._frame("nfl", con, tuple(cfg["half_lives"]))
    seen = []
    real = tune._base_fit

    def spy(sport, mk, c, tr):
        seen.append(max(tune._ts(tr["start_utc"])) + np.timedelta64(6 * 3600, "s"))
        return real(sport, mk, c, tr)
    monkeypatch.setattr(tune, "_base_fit", spy)
    w = tune.walk("nfl", "rush_att", df, cfg, None, log=lambda *_: None)
    ts = sorted(set(w["t"].tolist()))
    assert len(seen) == len(ts)
    for known_max, t in zip(seen, ts):
        assert known_max <= t                                  # every fit row known before the origin
    assert (w["start"] > w["t"]).all()                         # every forecast row starts after its origin
    roles = {r for _, r in w["order"]}
    assert roles == {"warm", "sel", "eval"}


def test_results_replay_from_packaged_history(hist, tmp_path, monkeypatch):
    committed = study.RESULTS / "model_tw1_nfl.json"
    if not committed.exists():
        pytest.skip("no committed tw-1 NFL result")
    monkeypatch.setattr(tune, "RESULTS", tmp_path)
    con = datasets.connect(hist, read_only=True)
    a = json.loads(committed.read_text(encoding="utf-8"))
    out = tune.run("nfl", con, ["rush_att"], log=lambda *_: None, as_of=a["as_of"])
    want = a["markets"]["rush_att"]
    got = json.loads(json.dumps(out["markets"]["rush_att"], default=float))
    assert got == want                                          # deterministic: the committed numbers replay


# --------------------------------------------------------------------------------------------- artifact
def _art(tmp_path, markets, sm1="abc"):
    art = {"version": "tw-1", "sport": "nba", "applies_to_sm1": sm1, "markets": markets}
    art["sha"] = tune._sha(markets)[:16]
    p = tmp_path / "tw1_nba.json"
    p.write_text(json.dumps(art), encoding="utf-8")
    from sports.model.fwd import _file_sha
    (tmp_path / "MANIFEST_TW1.json").write_text(json.dumps({"files": {p.name: {"sha256": _file_sha(p)}},
                                                            "artifacts": {"nba": {"sha": art["sha"]}}}))
    return p


def test_artifact_hash_and_switches(tmp_path, monkeypatch):
    mk = {"points": {"arm": "rc", "param": 28.0, "c": 1.01, "s": 1.02}}
    p = _art(tmp_path, mk)
    assert tune.load("nba", "abc", tmp_path)["markets"] == mk
    assert tune.load("nba", "other-sm1", tmp_path) is None              # built for a different sm-1 artifact
    monkeypatch.setenv("SPORTS_TUNE_TW1_OFF", "points")
    assert tune.load("nba", "abc", tmp_path)["markets"] == {}            # per-market off switch
    monkeypatch.delenv("SPORTS_TUNE_TW1_OFF")
    monkeypatch.setenv("SPORTS_TUNE_TW1", "0")
    assert tune.load("nba", "abc", tmp_path) is None                     # global off switch
    monkeypatch.delenv("SPORTS_TUNE_TW1")
    d = json.loads(p.read_text())
    d["markets"]["points"]["c"] = 1.05                                   # tampered parameters
    p.write_text(json.dumps(d))
    assert tune.load("nba", "abc", tmp_path) is None


def test_committed_artifacts_verify():
    for sport in ("nba", "nfl"):
        p = tune.art_path(sport)
        if not p.exists():
            continue
        sm1 = json.loads((tune.ART_DIR / f"sm1_{sport}.json").read_text(encoding="utf-8"))["sha"]
        a = tune.load(sport, sm1)
        assert a is not None and a["applies_to_sm1"] == sm1
        res = json.loads((study.RESULTS / f"model_tw1_{sport}.json").read_text(encoding="utf-8"))
        for m, v in a["markets"].items():                                 # only markets the registered rule switched
            r = res["markets"][m]
            assert r["chosen"] == v["arm"] and r["candidates"][v["arm"]]["walk_forward_pass"]
            assert (v["c"], v["s"]) == (r["freeze"]["c"], r["freeze"]["s"])


def test_layer_is_one_distribution_and_replays():
    lo, ks = dists.grid("points")
    base = dists.negbin(np.array([18.0]), 0.08, lo, ks)[0]
    art = {"sha": "x", "markets": {"points": {"arm": "rc", "c": 1.03, "s": 1.04}}}
    a, meta = tune.layer(art, "points", base, lo)
    b, _ = tune.layer(art, "points", base.copy(), lo)
    np.testing.assert_array_equal(a, b)                                   # replay-identical
    assert abs(a.sum() - 1) < 1e-12 and meta["calibration_version"].startswith("tw-1:rc:")
    po, pu, pp = dists.line_probs(a, lo, 18.0)
    assert abs(po + pu + pp - 1) < 1e-12
    same, none = tune.layer(art, "rebounds", base, lo)
    assert none is None and same is base                                  # not switched -> sm-1 untouched


# --------------------------------------------------------------------------------------------- bl-1
def test_bl1_shift_and_no_lookahead():
    rng = np.random.default_rng(3)
    n = 400
    p = rng.uniform(0.3, 0.7, n)
    hit = (rng.uniform(size=n) < np.clip(p - 0.08, 0, 1)).astype(float)   # true over-rate 8 points below p
    a = tune.fit_bl(p, 1 - p, np.zeros(n), hit)
    assert a > 0.1 and tune.bl_probs(p, 1 - p, np.zeros(n), a).mean() < p.mean()
    np.testing.assert_allclose(tune.bl_probs(p, 1 - p, np.zeros(n), 0.0), p, atol=1e-9)
    rows = [{"league": "nba", "stat": "points", "event": f"g{i // 4}", "line": 20.5, "po": float(p[i]),
             "pu": float(1 - p[i]), "pp": 0.0, "value": 25.0 if hit[i] else 15.0,
             "forecast_at": f"2026-10-{21 + i // 100}T{i % 100 // 10:02d}:00:00Z",
             "settled_at": f"2026-10-{21 + i // 100}T{10 + i % 100 // 10:02d}:00:00Z"} for i in range(n)]
    r1 = tune.bookline(rows)["nba"]["points"]
    rows2 = [dict(r) for r in rows]
    for r in rows2[300:]:
        r["value"] = 25.0                                                  # later outcomes changed
    r2 = tune.bookline(rows2)["nba"]["points"]
    assert r1["evaluable"] and r1["bl1"]["n"] == r2["bl1"]["n"] and r1["live"]["n"] == n
    # every forecast's a was fitted only on props settled before its forecast_at: rows 300+ settle after every
    # forecast of their own day and after all earlier days, so changing their outcomes changes no bl-1 probability
    b1, a1 = tune.bl_rolling(sorted(rows, key=lambda r: r["forecast_at"]))
    b2, a2 = tune.bl_rolling(sorted(rows2, key=lambda r: r["forecast_at"]))
    np.testing.assert_array_equal(b1, b2)
    assert (a1[:100] == 0).all() and (a1[100:] != 0).all()          # first day: < 100 settled props -> live
