"""pnu-1 NFL projection candidates: research harness invariants (no live path is touched)."""
import numpy as np

from sports.model import proj_nfl_cand as pc
from sports.research import proj_nfl_upgrade as up


def _row(i, start, settled, line=50.5, p=0.6, side="under", status="win", market="rec_yds", player="1", game=None):
    return {"forecast_id": f"f{i}", "match_key": game or f"nfl:espn:{start[:10]}", "player_id": f"nfl:player:espn:{player}",
            "market": market, "line": line, "book": "prizepicks", "side": side, "p": p, "status": status,
            "start_at": start, "settled_at": settled, "forecast_at": start[:11] + "10:00:00Z"}


def _synthetic(n_days=4, per_day=400, k_true=1.5, seed=3):
    rng = np.random.default_rng(seed)
    rows, i = [], 0
    for d in range(n_days):
        day = f"2026-10-{10 + 3 * d:02d}"
        for g in range(4):
            for _ in range(per_day // 4):
                z = rng.normal(0, 0.8)
                po = float(pc._phi(z)); over = rng.random() < float(pc._phi(z / k_true))
                side = "over" if po >= 0.5 else "under"
                p = po if side == "over" else 1 - po
                status = "win" if (side == "over") == over else "loss"
                rows.append(_row(i, f"{day}T18:00:00Z", f"{day}T23:00:00Z", line=40.5 + (i % 3), p=p, side=side,
                                 status=status, player=str(i % 50), game=f"g{d}-{g}"))
                i += 1
    return pc.prepare(rows, {})


def test_zero_params_reproduce_live_chance_exactly():
    rows = _synthetic(n_days=1, per_day=40)
    for names in ((), ("rr-1",), ("lr-1",), ("pp-1",), ("dv-1",), ("rr-1", "lr-1", "pp-1", "dv-1")):
        c = pc.Candidate(names)
        np.testing.assert_allclose(c.p_over(np.zeros(c.n), rows), [r["po"] for r in rows], atol=1e-6)


def test_point_in_time_history_excludes_games_after_cutoff():
    games = [{"pos": "WR", "team": "X", "season": 2026, "start": f"2026-10-{d:02d}T17:00:00Z", "rec": 5, "rec_yds": y,
              "carries": 0, "rush_yds": 0, "pass_yds": 0, "targets": 8, "pass_att": 0, "offense_pct": 0.9}
             for d, y in ((1, 40), (8, 50), (15, 60), (22, 70), (25, 500))]
    f = pc.features(games, "2026-10-25T20:00:00Z", "rec_yds", 55.5)   # Oct 25 17:00 game is < 6 h old -> excluded
    assert f["n_hist"] == 4 and f["n_season"] == 4
    f2 = pc.features(games, "2026-10-26T00:00:00Z", "rec_yds", 55.5)  # now start + 6 h <= forecast time
    assert f2["n_hist"] == 5


def test_walk_forward_trains_only_on_rows_settled_before_test_day():
    rows = _synthetic()
    late = dict(rows[0]); late["settled_at"] = "2026-12-31T00:00:00Z"   # settles after every test day
    rows = [late] + rows[1:]
    seen = []
    orig = pc.Candidate.fit

    def spy(self, tr, ridge=0.02):
        seen.append(tr)
        return orig(self, tr, ridge=ridge)
    pc.Candidate.fit = spy
    try:
        te, pl, pcd, win = up.walk_forward(rows, ("dv-1",), ridge=0.002)
    finally:
        pc.Candidate.fit = orig
    assert win[0]["scored"] is False and all(w["scored"] for w in win[1:])
    for tr, w in zip(seen, win[1:]):
        first = min(r["start_at"] for r in rows if pc.slate_day(r["start_at"]) == w["day"])
        assert all(r["settled_at"] < first for r in tr)
        assert late not in tr
    np.testing.assert_allclose(pl, [r["po"] for r in te])


def test_dispersion_fit_recovers_true_spread():
    rows = _synthetic(n_days=1, per_day=4000, k_true=1.5)
    c = pc.Candidate(("dv-1",))
    th = c.fit(rows, ridge=0.0)
    k = float(np.exp(th[0]))           # rec family log k (thin flag is 1 for every row without history)
    k_total = float(np.exp(th[0] + th[3]))
    assert 1.3 < k_total < 1.75, (k, k_total)


def test_market_centre_never_flips_mean_across_consensus():
    rows = _synthetic(n_days=1, per_day=200)
    c = pc.Candidate(("ma-1",))
    A = pc._arrays(rows)
    for th0 in (-30.0, -3.0, 0.0, 5.0):
        z2 = c.zprime(np.array([th0, 0, 0, 0, 0]), A)
        mask = A["u"] == 0
        assert np.all(np.sign(z2[mask]) == np.sign(A["z"][mask]))


def test_bfgs_minimises_quadratic():
    x = pc.bfgs(lambda v: float((v[0] - 1.5) ** 2 + 3 * (v[1] + 0.25) ** 2), np.zeros(2))
    np.testing.assert_allclose(x, [1.5, -0.25], atol=1e-3)


def test_gate_requires_every_condition():
    rows = _synthetic(n_days=1, per_day=400)
    p = np.array([r["po"] for r in rows])
    res = up.compare(p, p, rows)
    assert res["gate"]["graded_count_90pct"] and res["gate"]["over_share_not_pushed_5"]
    assert res["gate"]["logloss_ci_below_0"] is False and res["gate"]["pass"] is False
    assert res["graded_hit_diff"]["diff"] == 0.0


def test_over_only_offer_stays_over():
    rows = pc.prepare([_row(1, "2026-10-10T18:00:00Z", "2026-10-10T23:00:00Z", p=0.3, side="over")], {})
    side, pcs = pc.chosen(np.array([0.3]), rows)
    assert side[0] == 1 and abs(pcs[0] - 0.3) < 1e-9
