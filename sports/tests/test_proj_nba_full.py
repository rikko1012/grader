"""pnf-1 (sports/model/proj_nba_full.py) and its full-history harness (sports/research/proj_nba_full): distribution
maths, point-in-time features (no outcome leakage), teammates-out rule, walk-forward cut, book-line reader."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sports.model import proj_nba_full as pnf
from sports.research.proj_nba_full import bookline, data as D, walkforward as W


# --------------------------------------------------------------------------------------------------- distributions
def test_nb_logpmf_normalised_and_poisson_limit():
    ks = np.arange(0, 200, dtype=float)
    for mu, a in ((3.0, 0.2), (17.5, 0.05), (0.7, 1.0)):
        p = np.exp(pnf.nb_logpmf(ks, mu, a))
        assert abs(p.sum() - 1) < 1e-6
        assert abs((p * ks).sum() - mu) < 1e-4
        assert abs((p * (ks - mu) ** 2).sum() - (mu + a * mu * mu)) < 1e-2
    from scipy.stats import poisson
    p = np.exp(pnf.nb_logpmf(ks[:30], 4.0, 1e-6))
    assert np.allclose(p, poisson.pmf(ks[:30], 4.0), atol=1e-5)


def test_mixture_pmf_mean_and_normalisation():
    rate = np.array([0.5, 0.2])
    mq = np.array([[20.0] * 10 + [30.0] * 10, [10.0] * 20])
    p = pnf.mixture_pmf(rate, mq, lambda mu: np.full_like(mu, 0.1), 0, 100)
    assert np.allclose(p.sum(1), 1)
    ks = np.arange(0, 101)
    assert np.allclose(p @ ks, [12.5, 2.0], atol=1e-3)
    # a minutes mixture is wider than the single NB at the mean minutes
    single = pnf.mixture_pmf(rate[:1], np.full((1, 20), 25.0), lambda mu: np.full_like(mu, 0.1), 0, 100)
    var = lambda q: float(q @ ks ** 2 - (q @ ks) ** 2)   # noqa: E731
    assert var(p[0]) > var(single[0])


def test_ratio_mixture_pmf_mean_and_split():
    rate = np.array([0.5])
    mq = np.array([[20.0, 30.0]])
    R = np.ones((2, 4))                                       # ratio 1: point masses at rate * m
    p = pnf.ratio_mixture_pmf(rate, mq, [100.0], R, 0, 100, [0.5, 0.5])
    assert np.isclose(p.sum(), 1)
    assert np.isclose(p[0, 10], 0.5) and np.isclose(p[0, 15], 0.5)
    p2 = pnf.ratio_mixture_pmf(np.array([0.41]), np.array([[25.0]]), [100.0], np.array([[0.8, 1.2], [1, 1]]), 0, 100,
                               [1.0])
    assert np.isclose(p2[0] @ np.arange(101), 0.41 * 25.0)


def test_bookline_uses_latest_refit_before_forecast(tmp_path):
    def save(name, keys, mean):
        pmf = np.zeros((len(keys), 101))
        for i, m in enumerate(mean):
            pmf[i, int(m)] = 1.0
        np.savez_compressed(tmp_path / f"pnf_points__{name}.npz", keys=np.array(keys), y=np.zeros(len(keys)),
                            pmf=pmf.astype(np.float32), mean=np.array(mean, float), lo=0)
    save("final_2026-10-24", ["g1|p"], [10])
    save("final_2026-11-01", ["g2|p"], [20])
    save("xtra_2026-10-24", ["g2|p"], [30])
    props = pd.DataFrame({"market": ["points"] * 3, "key": ["g1|p", "g2|p", "g2|p"], "line": [15.5] * 3,
                          "forecast_at": ["2026-10-25T00:00:00Z", "2026-11-01T05:00:00Z", "2026-10-31T20:00:00Z"]})
    out = bookline.attach(props, tmp_path, "pnf")
    assert out["mean_pnf"].tolist() == [10.0, 20.0, 30.0]    # Oct-31 forecast of a Nov-1 game: the Oct-24 refit


def test_scores_pit_and_threshold():
    p = np.array([[0.1, 0.2, 0.3, 0.4]])
    assert np.isclose(pnf.logscore(p, 0, [2])[0], np.log(0.3))
    lo, hi = pnf.pit_bounds(p, 0, [2])
    assert np.isclose(lo[0], 0.3) and np.isclose(hi[0], 0.6)
    assert np.isclose(pnf.p_over(p, 0, 1.5)[0], 0.7)
    assert np.isclose(bookline._p_line(p, 0, [2.0])[0], 0.4 / 0.7)       # push at 2 removed


# ------------------------------------------------------------------------------------------------- synthetic league
def _league(n_games=40, seed=0):
    rng = np.random.default_rng(seed)
    teams = ["AAA", "BBB", "CCC", "DDD"]
    pgs, tgs, sch, ab = [], [], [], []
    t0 = pd.Timestamp("2024-01-01T00:00:00Z")
    for gi in range(n_games):
        h, a = teams[gi % 4], teams[(gi + 1 + gi // 4) % 4]
        if h == a:
            a = teams[(gi + 2) % 4]
        gid = f"nba:espn:{gi}"
        start = (t0 + pd.Timedelta(days=gi)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for team, opp, home in ((h, a, 1), (a, h, 0)):
            tot = dict(pts=0.0, fga=0.0, fta=0.0, oreb=0.0, tov=0.0, player_min=0.0)
            for j in range(6):
                pid = f"nba:player:espn:{team}{j}"
                played = 0 if (j == 1 and gi % 7 == 3) else 1
                mins = float(rng.integers(15, 38)) if played else 0.0
                row = dict(game_id=gid, player_id=pid, name=pid, team=team, opp=opp, home=home, starter=int(j < 5),
                           played=played, dnp_reason=None if played else "LEFT ANKLE SPRAIN", position="GFC"[j % 3],
                           min=mins, pts=float(rng.poisson(mins * 0.5)), fga=float(rng.poisson(mins * 0.4)),
                           fg3m=float(rng.poisson(mins * 0.05)), fg3a=float(rng.poisson(mins * 0.15)),
                           fta=float(rng.poisson(mins * 0.1)), oreb=1.0, dreb=3.0, reb=4.0,
                           ast=float(rng.poisson(mins * 0.1)), tov=1.0, pm=0.0, start_utc=start, season=2024,
                           season_type="regular", spread_home=-3.5, total_line=220.5, neutral=0)
                pgs.append(row)
                if not played:
                    ab.append(dict(game_id=gid, team=team, player_id=pid, kind="injury_illness"))
                for c in ("pts", "fga", "fta", "oreb", "tov"):
                    tot[c] += row[c]
                tot["player_min"] += mins
            tgs.append(dict(game_id=gid, team=team, opp=opp, home=home, start_utc=start, season=2024,
                            season_type="regular", poss_est=tot["fga"] - tot["oreb"] + tot["tov"] + 0.44 * tot["fta"],
                            spread_home=-3.5, total_line=220.5, home_team=h, **{k: v for k, v in tot.items()}))
            sch.append(dict(game_id=gid, team=team, days_rest=1.0, b2b=0, games_last_7d=3))
    return (pd.DataFrame(pgs), pd.DataFrame(tgs), pd.DataFrame(sch), pd.DataFrame(ab),
            pd.DataFrame(columns=["model_version"]))


def test_features_are_point_in_time():
    """Changing a game's box score must not change ANY feature of that game's rows (only targets) - and must not
    change any feature of earlier games."""
    data = _league()
    f0, _ = D.build(data=data)
    pg = data[0].copy()
    gid = "nba:espn:25"
    m = pg["game_id"] == gid
    for c in ("min", "pts", "fga", "fta", "fg3m", "fg3a", "ast", "reb", "tov"):
        pg.loc[m, c] = pg.loc[m, c] * 3 + 7
    tg = data[1].copy()
    tg.loc[tg["game_id"] == gid, ["pts", "fga"]] += 50
    f1, _ = D.build(data=(pg, tg) + data[2:])
    feats = [c for c in D.FEATURES if c not in ("pos_c", "pos_f")]
    ts_g = f0.loc[f0["game_id"] == gid, "ts"].iloc[0]
    keep = f0["ts"] <= ts_g
    a = f0.loc[keep].set_index(["game_id", "player_id"])[feats].sort_index()
    b = f1.loc[f1["ts"] <= ts_g].set_index(["game_id", "player_id"])[feats].sort_index()
    pd.testing.assert_frame_equal(a, b)
    later = f0["ts"] > ts_g
    assert not f0.loc[later, feats].equals(f1.loc[f1["ts"] > ts_g, feats])     # later games do see it


def test_teammates_out_counts_known_absence_only():
    data = _league()
    f, _ = D.build(data=data)
    # game 3: player AAA1 / DDD1 ... (j == 1) sat with an injury in games where gi % 7 == 3
    g = f[f["game_id"] == "nba:espn:10"]
    assert (g["out_n"] >= 1).all()
    pg, tg, sch, ab, pr = data
    ab2 = ab.assign(kind="coach_or_unlisted")                    # same DNPs as coach's decisions: not known pre-game
    f2, _ = D.build(data=(pg, tg, sch, ab2, pr))
    g2 = f2[f2["game_id"] == "nba:espn:10"]
    assert (g2["out_n"] == 0).all()


def test_model_fit_predict_synthetic(monkeypatch):
    monkeypatch.setattr(pnf, "CAL_ROWS", 150)
    data = _league(n_games=60)
    f, _ = D.build(data=data)
    f = D.add_pos(f)
    tr = f[(f["prior_n"] >= 1) & (f["ts"] < f["ts"].quantile(0.8))]
    te = f[f["ts"] >= f["ts"].quantile(0.8)]
    m = pnf.Model(D.FEATURES, {"n_estimators": 30, "min_child_samples": 10, "n_jobs": 1}).fit(tr)
    out = m.predict(te)
    for st in pnf.STATS:
        p, mu, lo = out[st]
        assert p.shape == (len(te), pnf.SUPPORT[st][1] + 1)
        assert np.allclose(p.sum(1), 1)
        assert np.all(mu > 0)
    assert abs(np.mean(out["points"][1]) - te["pts"].mean()) < 5


# ------------------------------------------------------------------------------------------------- harness rules
def test_windows_chronological_and_final_untouched():
    w = W.windows("all")
    starts = [W._ep(a) for _, a, _ in w]
    assert starts == sorted(starts)
    dev = [x for x in w if x[0] == "dev"]
    fin = [x for x in w if x[0] == "final"]
    assert W._ep(dev[-1][2]) <= W._ep(fin[0][1])
    assert all(W._ep(a) < W._ep(b) for _, a, b in w)


def test_final_end_default_and_override(monkeypatch):
    import importlib
    monkeypatch.delenv("PNF_FINAL_END", raising=False)
    m = importlib.reload(W)
    assert m.FINAL_END == "2026-11-06T00:00:00Z"            # earlier committed results stay reproducible
    monkeypatch.setenv("PNF_FINAL_END", "2026-11-07T00:00:00Z")
    m = importlib.reload(W)
    assert m.windows("final")[-1][2] == "2026-11-07T00:00:00Z"
    monkeypatch.delenv("PNF_FINAL_END")
    importlib.reload(W)


def test_bookline_load_props_dedupes():
    rows = [
        dict(src="gradecore_export_x", model_version="sm-1", game_id="g", player_id="p", market="points", line=10.5,
             book="pp", side="over", p_side=0.6, p_over=None, p_under=None, forecast_at="2026-10-30T10:00:00Z",
             start_at="2026-10-30T23:00:00Z", actual_box=12.0),
        dict(src="results_ledger_x", model_version="sm-1", game_id="g", player_id="p", market="points", line=10.5,
             book="pp", side=None, p_side=None, p_over=0.3, p_under=0.7, forecast_at="2026-10-30T20:00:00Z",
             start_at="2026-10-30T23:00:00Z", actual_box=12.0),
        dict(src="results_ledger_x", model_version="sm-1", game_id="g", player_id="q", market="points", line=8.5,
             book="pp", side=None, p_side=None, p_over=0.3, p_under=0.7, forecast_at="2026-10-30T20:00:00Z",
             start_at="2026-10-30T23:00:00Z", actual_box=8.0),
        dict(src="results_ledger_x", model_version="sm-1", game_id="g", player_id="q", market="points", line=8.5,
             book="pp", side=None, p_side=None, p_over=0.6, p_under=0.4, forecast_at="2026-10-30T22:50:00Z",
             start_at="2026-10-30T23:00:00Z", actual_box=8.0),
        dict(src="results_ledger_x", model_version="mkt-fanduel", game_id="g", player_id="q", market="points",
             line=8.5, book="fd", side=None, p_side=None, p_over=0.5, p_under=0.5,
             forecast_at="2026-10-30T20:00:00Z", start_at="2026-10-30T23:00:00Z", actual_box=8.0),
    ]
    out = bookline.load_props(pd.DataFrame(rows))
    assert len(out) == 2                                       # export row kept, later dup dropped, < 30 min dropped
    assert out.loc[out["player_id"] == "p", "po_live"].iloc[0] == pytest.approx(0.6)
    assert out.loc[out["player_id"] == "q", "po_live"].iloc[0] == pytest.approx(0.3)


def test_bookline_comparison_offline_scoring(tmp_path):
    import json
    def row(fid, at, event="g2", player="p", stat="points", line=10.5, book="underdog", po=0.4):
        return {"forecast_id": fid, "at": at, "event": event, "player": player, "stat": stat, "line": line,
                "book": book, "policy": "gp-2", "sides": {"over": {"raw": po}, "under": {"raw": 1 - po}}}
    rows = [row("a", "2026-11-05T20:00:00Z", po=0.4),
            row("b", "2026-11-05T22:40:00Z", po=0.7),          # latest >= 30 min before 23:30 tip: kept
            row("c", "2026-11-05T23:10:00Z", po=0.9),          # < 30 min before tip: dropped
            row("d", "2026-11-05T20:00:00Z", player="dnp"),    # did not play: void
            row("e", "2026-11-05T20:00:00Z", event="g9"),      # game not final: dropped
            row("f", "2026-11-05T20:00:00Z", player="q", line=8.5, po=0.2)]   # key also in results: results win
    path = tmp_path / "cmp.json"
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    games = {"g2": "2026-11-05T23:30:00Z"}
    box = {("g2", "p"): {"pts": 12.0}, ("g2", "q"): {"pts": 9.0}}
    comp, cnt = bookline.load_comparison(path, games, box)
    assert cnt["under_30_min"] == 1 and cnt["not_final_game"] == 1 and cnt["void_did_not_play"] == 1
    assert len(comp) == 2
    assert comp.loc[comp["player_id"] == "p", "po_live"].iloc[0] == pytest.approx(0.7)
    assert comp.loc[comp["player_id"] == "p", "actual_box"].iloc[0] == 12.0
    res = pd.DataFrame([dict(src="results_ledger_x", model_version="sm-1", game_id="g2", player_id="q",
                             market="points", line=8.5, book="underdog", side=None, p_side=None, p_over=0.6,
                             p_under=0.4, forecast_id="r1", forecast_at="2026-11-05T20:00:00Z",
                             start_at="2026-11-05T23:30:00Z", actual_box=9.0)])
    out = bookline.load_props(res, comp)
    assert len(out) == 2
    q = out[out["player_id"] == "q"].iloc[0]
    assert q["src"] == "results_ledger_x" and q["po_live"] == pytest.approx(0.6)
    assert out.loc[out["player_id"] == "p", "over"].iloc[0] == 1.0


def test_bookline_live_tag_filter():
    rows = [dict(src="gradecore_export_x", model_version="sm-1", forecast_id=f, game_id="g", player_id=f,
                 market="points", line=10.5, book="pp", side="over", p_side=0.6, p_over=None, p_under=None,
                 forecast_at="2026-10-30T10:00:00Z", start_at="2026-10-30T23:00:00Z", actual_box=12.0)
            for f in ("x", "y")]
    out = bookline.load_props(pd.DataFrame(rows), tags={"x": "sm-1:aaa", "y": "sm-1:old"}, live_tag="sm-1:aaa")
    assert list(out["player_id"]) == ["x"]


def test_bookline_export_only_drops_other_sources():
    base = dict(model_version="sm-1", game_id="g", market="points", line=10.5, book="pp",
                forecast_at="2026-10-30T10:00:00Z", start_at="2026-10-30T23:00:00Z", actual_box=12.0)
    rows = [dict(base, src="gradecore_export_x", forecast_id="x", player_id="x", side="over", p_side=0.6,
                 p_over=None, p_under=None),
            dict(base, src="results_ledger_x", forecast_id="r", player_id="r", side=None, p_side=None,
                 p_over=0.6, p_under=0.4)]
    assert len(bookline.load_props(pd.DataFrame(rows))) == 2
    out = bookline.load_props(pd.DataFrame(rows), export_only=True)
    assert list(out["src"]) == ["gradecore_export_x"]
