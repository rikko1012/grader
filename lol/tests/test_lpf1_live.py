"""lpf-1 live path (lol/lpf1_live.py, flag LOL_LPF1, default off): the numpy tree evaluator equals lightgbm, the
mixture arithmetic equals the research harness, the projector on production-shaped history equals the research
walk-forward, LOL_LPF1=0 changes nothing, and on (the default) saves the lpf-1 chance; no answer = the saved lpf-1 forecast stands, else the live lol-1f.
Every team, player and number is a TEST FIXTURE."""
import gzip
import json
import sqlite3

import numpy as np
import pytest

from lol import lpf, lpf1_client as C, lpf1_live as L
from lol.center import forecast, store
from lol.tests.test_consolidation import _future_world
from lol.tests.test_lpf import _db


# ------------------------------------------------------------------ trees
def test_trees_equal_lightgbm_raw_score_with_nan_zero_and_categorical():
    lgb = pytest.importorskip("lightgbm")
    from lol.lpf1_train import compact
    rng = np.random.default_rng(3)
    n = 4000
    X = np.column_stack([rng.normal(size=n), rng.integers(0, 5, n).astype(float), rng.normal(size=n), rng.poisson(1.0, n).astype(float)])
    X[rng.random(n) < 0.15, 0] = np.nan
    X[rng.random(n) < 0.10, 2] = 0.0
    y = rng.poisson(np.exp(0.3 * np.nan_to_num(X[:, 0]) + 0.2 * X[:, 1] - 0.1 * X[:, 3] + 1.0))
    off = np.log(np.full(n, 2.0))
    names = ["a", "role", "c", "d"]
    b = lgb.train(dict(lpf.LGB_PARAMS, num_threads=1), lgb.Dataset(X, y, feature_name=names, categorical_feature=["role"],
                                                                     init_score=off), num_boost_round=40)
    d = b.dump_model()
    T = L.Trees({"feature_names": d["feature_names"], "tree_info": [{"tree_structure": compact(t["tree_structure"])} for t in d["tree_info"]]})
    Xt = X.copy()
    Xt[:50, 1] = -1                            # an unknown role goes right, as lightgbm
    Xt[50:100, 2] = np.nan
    assert np.allclose(T.raw(Xt), b.predict(Xt, raw_score=True), atol=1e-9)
    for lo in (0, 40, 90):                      # the plain-python path a forecast uses (<= 64 rows)
        assert np.allclose(T.raw(Xt[lo:lo + 20]), b.predict(Xt[lo:lo + 20], raw_score=True), atol=1e-9)


# ------------------------------------------------------------------ mixture
def test_mixnb_equals_the_research_mixture():
    pytest.importorskip("scipy")
    rng = np.random.default_rng(1)
    for _ in range(30):
        c = int(rng.integers(1, 5))
        W, M, S = rng.random(c) + 0.1, rng.uniform(1, 25, c), rng.uniform(1, 40, c)
        line = float(rng.choice([2.5, 4.5, 7.0, 11.5, 15.0]))
        a = L.MixNB(W, M, S)
        po, pu, pp = a.probs(line)
        ref_o, ref_p = lpf.Mix(W[None], M[None], S[None]).p_over(np.array([line]))
        assert abs(po - ref_o[0]) < 1e-9 and abs(pp - ref_p[0]) < 1e-9 and abs(po + pu + pp - 1) < 1e-9
        assert abs(a.mean() - lpf.Mix(W[None], M[None], S[None]).mean()[0]) < 1e-9


# ------------------------------------------------------------------ projector on production-shaped history
class _HDB:
    def __init__(self, path):
        self.c = sqlite3.connect(str(path))
        self.c.row_factory = sqlite3.Row

    def query(self, sql, args=()):
        return [dict(r) for r in self.c.execute(sql, args)]


def test_projector_equals_research_walk_forward(tmp_path):
    pytest.importorskip("lightgbm")
    pytest.importorskip("sklearn")
    from lol import lpf_data as D, lpf_eval as E
    from lol.lpf1_train import build_artifact
    path = _db(tmp_path / "h.db")
    c0 = sqlite3.connect(str(path))
    c0.execute("CREATE TABLE forecasts (id, platform, market_key, line, forecast_at, series_start, sid, pid, stat, scope, inputs, model_version)")
    c0.execute("CREATE TABLE settlements (unit_key, version, status, over_hit, push, actual)")
    c0.commit()
    c0.close()
    cutoff_day = 739000 + 65
    import datetime as dt
    cutoff = dt.date.fromordinal(cutoff_day).isoformat()
    art, boosters, fr, cand = build_artifact(str(path), cutoff, rounds=8, log=lambda *a: None)
    raw = json.dumps(art).encode()
    ap = tmp_path / "a.json.gz"
    ap.write_bytes(gzip.compress(raw))
    art = L.load_artifact(ap)
    h = _HDB(path)
    tours = {r["skey"]: json.loads(r["data"]) for r in h.query("SELECT skey, data FROM lp_Tournaments")}
    st = L.ProdStore(h, h.c, tours, player_table="hist_player_games", game_table="hist_games",
                     series_sql="SELECT sid, tournament_page, tournament, best_of FROM center_c_series")
    pr = L.Projector(st, art)
    # research: the same refit predicts the targets on / after the cutoff
    wm = E.win_model_fit(fr, cutoff_day)
    sizes = cand.fit_sizes(cutoff_day, wm)
    idx = np.where(fr.day >= cutoff_day)[0][:20]
    mus = cand.game_means(boosters, idx)
    p = wm.p(fr.F(idx))
    for s in ("kills", "assists"):
        for sc in ("1", "12"):
            ref = cand.mix(cand.comps(mus, s, sc, idx, p, wm.p3(p)), s, fr.role[idx], sizes)
            for j, i in enumerate(idx):
                t = fr.T[i]
                tl = pr.target(t["match"], t["pid"], t["team"], t["opp"], t["role"], t["day"], t["league"], t["tournament"],
                               t["best_of"], st.series[t["match"]]["tournament_page"])
                tl["patch"] = t["patch"]
                mix, pw, _ = pr.mixture(tl, s, sc, t["live"][s][0])
                assert abs(pw - p[j]) < 1e-9
                assert abs(mix.mean() - ref.mean()[j]) < 1e-6 * max(1.0, ref.mean()[j])
                o, _ = ref.p_over(np.full(len(idx), 4.5))
                assert abs(mix.probs(4.5)[0] - o[j]) < 1e-6


# ------------------------------------------------------------------ forecast hook
@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


class _Stub:
    def target(self, sid, pid, tid, opp, role, day, league, tournament, best_of, page=None):
        return {"match": sid, "pid": pid, "patch": "26.1", "best_of": best_of}

    def project(self, t, stat, scope, lam_game, line):
        assert lam_game > 0 and t["best_of"] == 3
        return {"p_over": 0.71, "p_under": 0.29, "p_push": 0.0, "mean": line + 2.0, "median": int(line + 2), "range80": [1, 30],
                "p_win": 0.6, "p_decider": 0.4}


def test_on_is_the_default_and_0_is_the_rollback(monkeypatch):
    monkeypatch.delenv("LOL_LPF1", raising=False)
    assert forecast.lpf1_on() and C.enabled()
    monkeypatch.setenv("LOL_LPF1", "1")
    assert forecast.lpf1_on() and C.enabled()
    monkeypatch.setenv("LOL_LPF1", "0")
    assert not forecast.lpf1_on() and not C.enabled()


def test_rollback_switch_changes_nothing(con, tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_LPF1", "0")
    assert forecast.lpf1_state(con, object()) is None
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    assert f["model_version"] != "lpf-1" and "lpf1" not in json.loads(f["inputs"])


def test_flag_on_saves_the_lpf1_chance_and_projection(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lpf1_state", lambda c, h, *a, **k: {"proj": _Stub(), "stats": ("kills",), "version": "lpf-1",
                                                               "sha": "abc", "cutoff": "2026-11-09"})
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    inp = json.loads(f["inputs"])
    assert f["model_version"] == "lpf-1" and f["p_over"] == 0.71 and f["model_side"] == "over"
    assert abs(f["lambda"] - (f["line"] + 2.0)) < 1e-9 and inp["lpf1"]["artifact"] == "abc" and inp["lpf1"]["lambda_lol1f"] > 0


def _no_answers(c, h, *a, **k):
    return {"proj": None, "stats": ("kills",), "version": "lpf-1", "sha": None, "cutoff": None, "error": "RuntimeError: x"}


def test_flag_on_without_a_projector_falls_back_to_the_live_lol1f(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lpf1_state", _no_answers)
    out = forecast.run(con, db)
    assert out.get("saved") == 1 and out["lpf1_error"] == "RuntimeError: x"     # never hidden, never an error
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    assert f["model_version"] != "lpf-1" and "lpf1" not in json.loads(f["inputs"]) and f["grade_label"]


def test_a_saved_lpf1_forecast_stands_through_a_failure(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lpf1_state", lambda c, h, *a, **k: {"proj": _Stub(), "stats": ("kills",), "version": "lpf-1",
                                                               "sha": "abc", "cutoff": "2026-11-09"})
    assert forecast.run(con, db)["saved"] == 1
    monkeypatch.setattr(forecast, "lpf1_state", _no_answers)
    out = forecast.run(con, db)
    assert "saved" not in out and any(k.startswith("skip: lpf-1 answer unavailable") for k in out)
    rows = con.execute("SELECT model_version, p_over FROM lol_forecasts").fetchall()
    assert [tuple(r) for r in rows] == [("lpf-1", 0.71)]             # the transient failure changed no grade


def test_lpf1_state_raising_never_stops_the_cycle(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(forecast, "lpf1_state", boom)
    out = forecast.run(con, db)
    assert out.get("saved") == 1 and "boom" in out["lpf1_error"]


def test_flag_on_with_a_broken_artifact_reports_and_falls_back(con, tmp_path, monkeypatch):
    monkeypatch.delenv("LOL_LPF1", raising=False)                   # the default: on
    from lol import lpf1_client as C
    monkeypatch.setattr(C, "ARTIFACT", tmp_path / "missing.json.gz")
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    out = forecast.run(con, db)
    assert "artifact missing" in out.get("lpf1_error", "") and out.get("saved") == 1
    assert con.execute("SELECT model_version FROM lol_forecasts").fetchone()[0] != "lpf-1"


def test_only_one_child_at_a_time(tmp_path, monkeypatch):
    seen = []

    def runner(cmd, **k):
        a2, info = C.run_child({"k2": {}}, None, tmp_path / "c.db", runner=lambda *x, **y: None)    # nested = a second caller
        seen.append(info.get("error"))
        raise OSError("spawn failed")
    ans, info = C.run_child({"k1": {}}, None, tmp_path / "c.db", runner=runner)
    assert seen and seen[0].startswith("busy") and ans == {"k1": None} and "spawn failed" in info["error"]
    ans, info = C.run_child({"k3": {}}, None, tmp_path / "c.db", runner=lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert "busy" not in info["error"]                                # the lock was released


def test_shipped_artifact_loads_and_has_the_accepted_configuration():
    art = L.load_artifact()
    assert art is not None and art["version"] == "lpf-1" and art["variant"] == "W" and art["offset_live"] is True
    assert set(art["boosters"]) == {"kills", "assists"} and len(art["features"]) == 93
    assert art["booster_inputs"][-3:] == ["gn", "decider", "win"]
    T = L.Trees(art["boosters"]["kills"])
    assert T.names == art["booster_inputs"] and len(T.trees) == art["rounds"]


# ------------------------------------------------------------------ the child process (memory: nothing lpf-1 in the worker)
def test_client_is_stdlib_only():
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", "import sys, lol.lpf1_client; print(sorted(m for m in ('numpy', 'lol.lpf', 'lol.lp1', "
                          "'lol.lpf1_live') if m in sys.modules))"], cwd=str(root), capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr


def test_request_projector_collects_then_answers():
    rp = C.RequestProjector({})
    t = rp.target("s1", "p1", "t1", "t2", "mid", 739000, "LCK", "LCK 2026", 3, "LCK/2026")
    with pytest.raises(C.Pending):
        rp.project(t, "kills", "12", 2.5, 7.5)
    (k, q), = rp.requests.items()
    assert q["stat"] == "kills" and q["line"] == 7.5
    rp2 = C.RequestProjector({k: {"p_over": 0.6}})
    assert rp2.project(t, "kills", "12", 2.5, 7.5)["p_over"] == 0.6
    with pytest.raises(C.Unavailable):
        C.RequestProjector({k: None}).project(t, "kills", "12", 2.5, 7.5)


def test_failures_are_never_cached():
    C.remember(("v", 1), {"a": None, "b": {"p_over": 0.5}})
    assert C.answers_for(("v", 1)) == {"b": {"p_over": 0.5}}
    assert C.answers_for(("v", 2)) == {}


def test_flag_on_runs_the_real_child_once_and_reuses_its_answers(con, tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_LPF1", "1")
    monkeypatch.setenv("LOL_LPF1_STATS", "kills")
    monkeypatch.setattr(C, "_ANS", {"version": None, "map": {}})
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    out = forecast.run(con, db)
    assert out.get("saved") == 1, out
    assert out["lpf1_child"]["code"] == 0 and out["lpf1_child"]["answered"] == 1 and out["lpf1_child"]["peak_rss_mb"] is not None
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    inp = json.loads(f["inputs"])
    assert f["model_version"] == "lpf-1" and inp["lpf1"]["cutoff"] == L.load_artifact()["cutoff"]
    assert abs(f["p_over"] + f["p_under"] + f["p_push"] - 1) < 1e-3
    out2 = forecast.run(con, db)                        # same history, same line: answered from memory, no child
    assert "lpf1_child" not in out2 and out2.get("unchanged") == 1


def test_a_failed_child_falls_back_and_is_retried_next_cycle(con, tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_LPF1", "1")
    monkeypatch.setenv("LOL_LPF1_STATS", "kills")
    monkeypatch.setattr(C, "_ANS", {"version": None, "map": {}})
    calls = []

    def dead(requests, *a, **k):
        calls.append(len(requests))
        return {q: None for q in requests}, {"requests": len(requests), "error": "timeout"}
    monkeypatch.setattr(C, "run_child", dead)
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    out = forecast.run(con, db)
    assert out.get("saved") == 1 and "timeout" in out["lpf1_error"]
    assert con.execute("SELECT model_version FROM lol_forecasts").fetchone()[0] != "lpf-1"
    forecast.run(con, db)
    assert calls == [1, 1]                              # the failure was not cached: asked again
