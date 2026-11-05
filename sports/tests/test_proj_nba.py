"""pn-1 NBA projection candidates (sports/model/proj_nba.py) and their walk-forward harness: the distribution moves
are exact, the fits never read the props they are applied to, and the harness only trains on games that ended before
the test day's first forecast."""
import json
import pickle

import numpy as np
import pytest

from sports.model import dists, proj_nba
from sports.research.proj_nba import walkforward


def _pois(mu, stat="points"):
    lo, ks = dists.grid(stat)
    return lo, dists.poisson(np.array([mu], float), lo, ks)[0]


def test_shift_scale_identity_and_mass():
    lo, p = _pois(12.0)
    assert np.allclose(proj_nba.shift_scale(p, lo, 12.0, 0.0, 1.0), p)
    q = proj_nba.shift_scale(p, lo, 12.0, 2.5, 1.0)
    assert q.sum() == pytest.approx(1.0)
    m, _ = proj_nba.moments(q[None, :], lo)
    assert m[0] == pytest.approx(14.5, abs=1e-6)          # additive shift moves the mean exactly


def test_shift_scale_spread():
    lo, p = _pois(15.0)
    _, sd0 = proj_nba.moments(p[None, :], lo)
    q = proj_nba.shift_scale(p, lo, 15.0, 0.0, 1.5)
    m, sd1 = proj_nba.moments(q[None, :], lo)
    assert m[0] == pytest.approx(15.0, abs=0.01)          # only the tail clipped at 0 moves it
    assert sd1[0] > 1.4 * sd0[0]                          # spread grows (the integer split adds a little)


def test_p_over_no_push_and_median():
    lo, p = _pois(10.0)
    po = proj_nba.p_over_no_push(p[None, :], lo, np.array([10.0]))[0]
    ks = lo + np.arange(len(p))
    assert po == pytest.approx(p[ks > 10].sum() / (1 - p[ks == 10].sum()))
    med = proj_nba.cont_median(p[None, :], lo)[0]
    assert 9.0 < med < 10.5


def _rows(n_days=6, players=30, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        day = f"2026-10-{20 + d:02d}"
        for g in range(2):
            ev = f"g{d}{g}"
            start = f"{day}T23:{30 + g * 10}:00Z"
            for pl in range(players):
                for st in proj_nba.STATS:
                    mu = {"points": 14, "rebounds": 6, "assists": 4, "threes": 1.6}[st] * rng.uniform(0.5, 1.5)
                    lo, ks = dists.grid(st)
                    pmf = dists.poisson(np.array([mu]), lo, ks)[0]
                    line = np.floor(mu * rng.uniform(0.85, 1.15)) + 0.5
                    act = float(rng.poisson(mu * 1.05))
                    po = float(pmf[ks > line].sum())
                    side = "over" if po >= 0.5 else "under"
                    won = (act > line) == (side == "over")
                    rows.append(dict(src="export", fid=f"{ev}{pl}{st}", event=ev, player=f"p{pl}", stat=st, line=line,
                                     book="prizepicks", forecast_at=f"{day}T18:00:00Z", start=start, side=side,
                                     p_side=max(po, 1 - po), status="win" if won else "loss", mu=mu, pmf_lo=lo,
                                     pmf=pmf.astype(np.float32), actual=act, minutes=float(rng.uniform(10, 38)),
                                     row={"prior_n": float(rng.integers(0, 300)), "ew_min": float(rng.uniform(12, 36)),
                                          "l5_min": float(rng.uniform(10, 38)), "l10_min": float(rng.uniform(10, 38)),
                                          "starter_lag": float(rng.integers(0, 2)),
                                          "starter_rate10": float(rng.uniform(0, 1))}))
    return rows


@pytest.mark.parametrize("spec", ["ma", "md", "sk", "rm", "rm+sk+md"])
def test_fit_never_reads_test_outcomes(spec):
    rows = _rows(n_days=3, players=25)
    bk = proj_nba.Book(rows)
    days = np.array([r["forecast_at"][:10] for r in rows])
    tr = np.flatnonzero(days < "2026-10-22")
    te = np.flatnonzero(days == "2026-10-22")
    p1, m1, _ = proj_nba.run(bk, spec, tr, te)
    bk.actual[te] = 999.0                                  # scramble every test outcome
    bk.over[te] = 1 - bk.over[te]
    bk.minutes[te] = 0.0
    p2, m2, _ = proj_nba.run(bk, spec, tr, te)
    assert np.allclose(p1, p2) and np.allclose(m1, m2)
    assert np.all((p1 > 0) & (p1 < 1))


def test_median_anchor_zero_puts_median_on_line():
    rows = _rows(n_days=1, players=10)
    bk = proj_nba.Book(rows)
    ii = np.arange(bk.n)
    st = proj_nba.MedianAnchor()
    st.keys = np.char.add(np.char.add(bk.stat.astype(str), "|"), bk.rel.astype(str))
    st.a = {k: 0.0 for k in np.unique(st.keys)}
    d0, s0 = np.zeros(bk.n), np.ones(bk.n)
    dd, ss = st.adjust(bk, ii, d0, s0)
    _, med, _ = bk.state(ii, dd, ss)
    assert np.allclose(med, bk.line)


def test_walkforward_trains_only_on_finished_games(tmp_path):
    rows = _rows(n_days=6, players=30)
    src = tmp_path / "rc.pkl"
    pickle.dump({"rows": rows}, open(src, "wb"))
    dst = tmp_path / "out.json"
    walkforward.main(str(src), str(dst))
    rep = json.loads(dst.read_text())
    assert [w["test_day"] for w in rep["windows"]] == ["2026-10-23", "2026-10-24", "2026-10-25"]
    for w in rep["windows"]:
        assert w["train_through"] < w["first_forecast"]
    for k in walkforward.SPECS:
        c = rep["candidates"][k]
        assert set(c["vs_live"]["checks"]) == {"ll_ci_below_0", "calib_all", "calib_over", "calib_under", "over_share",
                                               "graded_count"}
        assert c["eval"]["n"] == rep["n_test"]
