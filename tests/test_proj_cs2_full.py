"""pd-1 research harness (research/proj_cs2_full_2026_11_05): distributions match the live code, no same-day leakage,
scores and side statistics behave."""
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "..", "research", "proj_cs2_full_2026_11_05")
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
sys.path.insert(0, os.path.abspath(RES))

pytest.importorskip("lightgbm")
import dist  # noqa: E402
import features  # noqa: E402
import perf  # noqa: E402


def test_live_kills_pmf_matches_perf():
    for lam, sc in ((15.2, "1"), (31.7, "12"), (44.0, "123"), (0.4, "1")):
        c = perf.SPREAD[("kills", sc)]
        ref = perf.kills_pmf(lam, sc, c)
        P = dist.live_kills_pmf([lam], [c], dist.YMAX[sc])[0]
        for k, v in ref.items():
            assert abs(P[k] - v) < 1e-9
        assert abs(P.sum() - 1) < 1e-9


def test_hs_mixture_matches_perf_hs_pmf():
    lam, sc, hsr = 30.0, "12", 0.47
    c = perf.SPREAD[("kills", sc)]
    ref = perf.hs_pmf(lam, sc, hsr, c=c)
    K = dist.live_kills_pmf([lam], [c], dist.YMAX[sc])
    H = dist.betabinom_mix(K, [hsr])[0]
    for y, v in ref.items():
        assert abs(H[y] - v) < 1e-8
    assert abs(H.sum() - 1) < 1e-8


def test_p_over_line_whole_and_half():
    P = np.zeros((1, 11))
    P[0, [4, 5, 6]] = [0.25, 0.5, 0.25]
    o, u = dist.p_over_line(P, [5.0])            # push at exactly 5 excluded from both sides
    assert o[0] == pytest.approx(0.25) and u[0] == pytest.approx(0.25)
    o, u = dist.p_over_line(P, [4.5])
    assert o[0] == pytest.approx(0.75) and u[0] == pytest.approx(0.25)


def test_negbin_mean_and_variance():
    P = dist.negbin_pmf([20.0], [2.0], 200)[0]
    y = np.arange(len(P))
    m = (P * y).sum()
    assert m == pytest.approx(20.0, abs=1e-3)
    assert ((y - m) ** 2 * P).sum() == pytest.approx(40.0, rel=1e-3)


def test_logscore_and_pit_bounds():
    P = dist.norm_pmf([10.0, 3.0], [3.0, 2.0], 40)
    ls = dist.logscore(P, [10, 0])
    assert np.all(ls < 0) and np.all(np.isfinite(ls))
    u = dist.pit(P, [10, 0], np.random.default_rng(0))
    assert np.all((u >= 0) & (u <= 1))


def test_decay_reads_only_earlier_updates():
    D = features.Decay((10.0,), 2)
    assert D.read("p", 5) is None
    D.add("p", 5, [1, 10])
    v = D.read("p", 15)[0]                        # one half-life later: weight exp(-1) per the decay convention
    assert v[1] == pytest.approx(10 * math.exp(-1.0))
    D.add("p", 15, [1, 0])
    assert D.read("p", 15)[0][0] == pytest.approx(math.exp(-1.0) + 1)


def test_feature_pass_excludes_same_day(monkeypatch):
    """A row's features are read before its own day's maps are added: a player's first match has no history."""
    import pandas as pd
    rows = []
    for day, mid in ((738000, 1), (738001, 2)):
        for t, o in ((10, 20), (20, 10)):
            for j in range(5):
                rows.append(dict(mapstatsid=mid, matchid=mid, playerid=t * 10 + j, teamid=t, oppid=o, k=20 if t == 10 else 10,
                                 hs=8, a=3, d=15, ok=1, od=1, mk=2, clutches=0, kast=70.0, adr=80.0, swing=0.0, rating=1.0,
                                 number=1, mapid=31, picked_by=10, team_rounds=13 if t == 10 else 5,
                                 opp_rounds=5 if t == 10 else 13, rounds=18, date=pd.Timestamp.fromordinal(day).date().isoformat(),
                                 event="x", lan=False, bo=1, maps_played=1, map_won=1 if t == 10 else 0, team="A" if t == 10 else "B",
                                 opponent="B" if t == 10 else "A", day=day))
    pm = pd.DataFrame(rows)
    pm["complete"] = True
    pm["hs_ok"] = True
    pm["team_k"] = pm.groupby(["mapstatsid", "teamid"])["k"].transform("sum")
    pm["conc_k"] = pm.groupby("mapstatsid")["k"].transform("sum") - pm["team_k"]

    ctx = pd.DataFrame({"matchid": [1, 1, 2, 2], "teamid": [10, 20, 10, 20], "elo_pre": 1500.0, "elo_n_pre": 0,
                        "opp_elo_pre": 1500.0, "opp_elo_n_pre": 0, "rest_days_pre": np.nan, "lineup_changes_vs_prev": np.nan,
                        "players_new_60d": np.nan})
    real = pd.read_csv
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: ctx[k["usecols"]] if "usecols" in k else real(*a, **k))
    out = features.build(pm, verbose=False)
    first = out[out.matchid == 1]
    assert (first["r_90"] == 0).all()             # no history from its own match
    second = out[(out.matchid == 2) & (out.teamid == 10)]
    assert (second["r_90"] > 0).all()
    assert second["kpr_90"].iloc[0] > features.POP_KPR    # 20 kills in 18 rounds raised the rate
