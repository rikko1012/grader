"""pnf-1 NFL full-history projection distributions: invariants of the model and the walk-forward harness.

Research only - nothing here touches the live path (sports.model.forward never imports proj_nfl_full)."""
import sqlite3

import numpy as np

from sports.model import dists
from sports.model import proj_nfl_full as pnf
from sports.model.frame import Frame
from sports.research import proj_nfl_full as H


def test_live_path_does_not_import_pnf():
    import inspect

    from sports.model import forward
    src = inspect.getsource(forward)
    assert "proj_nfl_full" not in src


def test_volume_mixture_mean_preserved_and_normalised():
    mu = np.array([1.0, 5.0, 30.0])
    par = np.array([[0.1, 0.0, 1.0], [0.2, 0.1, 0.2], [0.02, 0.15, 0.45]])
    T = pnf.negbin_rows(mu, par, 70)
    assert np.allclose(T.sum(axis=1), 1.0, atol=1e-6)
    assert np.allclose(T @ np.arange(71), mu, rtol=2e-3)


def test_mixture_mle_recovers_partial_component():
    rng = np.random.default_rng(0)
    n = 6000
    mu = np.full(n, 34.0)
    part = rng.random(n) < 0.1
    full_mu = 34.0 / (1 - 0.1 + 0.1 * 0.2)
    y = np.where(part, rng.poisson(full_mu * 0.2, n), rng.poisson(full_mu, n))
    fit = pnf.fit_mix(y.astype(float), mu)
    assert fit["pi"] >= 0.06 and fit["rho"] <= 0.3


def test_yards_given_units_mean():
    lo, ks = dists.grid("rec_yds")
    units = np.zeros((2, 6))
    units[0, 3] = 1.0                       # exactly 3 receptions at 11 yards each
    units[1, 0] = 1.0                       # no reception -> 0 yards
    e = np.sort(np.random.default_rng(1).standard_normal(400))
    p = pnf.yards_given_units(units, np.array([11.0, 11.0]), np.array([4.0, 4.0]), e, lo, ks)
    assert abs(p[0] @ ks - 33.0) < 1.0
    assert p[1, -lo] == 1.0


def test_yards_bucket_shapes_used_per_unit_count():
    lo, ks = dists.grid("rush_yds")
    units = np.zeros((1, 21))
    units[0, 20] = 1.0                      # 20 carries -> bucket 1 (edges (10.5,)): its shape and scale apply
    e = np.sort(np.random.default_rng(2).standard_normal(400))
    narrow = [{"s": 1.0, "e": e}, {"s": 0.5, "e": e}]
    wide = [{"s": 1.0, "e": e}, {"s": 2.0, "e": e}]
    pn = pnf.yards_given_units(units, np.array([4.0]), np.array([9.0]), narrow, lo, ks, n_edges=(10.5,))
    pw = pnf.yards_given_units(units, np.array([4.0]), np.array([9.0]), wide, lo, ks, n_edges=(10.5,))

    def sd(p):
        return float(np.sqrt(p[0] @ ks ** 2 - (p[0] @ ks) ** 2))
    assert abs(pn[0] @ ks - 80.0) < 1.0 and abs(pw[0] @ ks - 80.0) < 1.0
    assert 3.5 < sd(pw) / sd(pn) < 4.5


def test_thresholds_are_half_integers_from_prior_games_only():
    cf = Frame({"l6_rec": np.array([3.2, 0.0, np.nan])})
    thr = H.thresholds_for(cf, "rec", np.arange(3))
    assert np.all((thr * 2) % 2 == 1)        # every threshold is x.5 -> no pushes
    assert thr[0, 2] == 3.5 and thr[1, 0] == 0.5


def test_boot_resamples_whole_groups():
    vals = np.array([1.0, 1.0, 0.0, 0.0])
    lo, hi = H.boot(vals, ["a", "a", "b", "b"], reps=200)
    assert lo[0] == 0.0 and hi[0] == 1.0     # a game is all-in or all-out of a resample


def test_side_report_over_only_offers_stay_over():
    rows = [{"one_sided_over": True}, {"one_sided_over": False}]
    out, side, pc, hit, g = H.side_report(np.array([0.3, 0.3]), rows, np.array([1.0, 0.0]), ["g1", "g2"])
    assert list(side) == [1, 0]
    assert np.isclose(pc[0], 0.3) and np.isclose(pc[1], 0.7)


def test_augment_features_use_prior_games_only(tmp_path):
    """Changing a player's LAST game outcome must not change any feature of that last row (no leakage)."""
    db = tmp_path / "h.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE games (league, game_id, season, season_type, week, start_utc, home, away, status, "
                "spread_home, total_line, roof, temp, wind, espn_id)")
    con.execute("CREATE TABLE nfl_tg (game_id, team, opp, home, plays, pass_plays, rush_plays, neutral_pass, "
                "neutral_plays, pass_att, carries, targets)")
    cols = ("game_id, player_id, espn_id, name, position, position_group, team, opp, home, season, week, season_type, "
            "pass_att, pass_cmp, pass_yds, pass_td, pass_int, carries, rush_yds, rush_td, targets, rec, rec_yds, "
            "rec_td, rec_air_yds, target_share, air_yards_share, offense_snaps, offense_pct, rz_targets, rz_carries, "
            "i10_targets, i10_carries, src")
    con.execute(f"CREATE TABLE nfl_pg ({cols})")
    for w in range(1, 7):
        gid = f"g{w}"
        con.execute("INSERT INTO games VALUES ('nfl',?,2024,'regular',?,?, 'AAA','BBB','final',-3,45,'outdoors',60,5,?)",
                    (gid, w, f"2024-09-{w * 3 + 1:02d}T17:00:00Z", str(w)))
        for t, o, h in (("AAA", "BBB", 1), ("BBB", "AAA", 0)):
            con.execute("INSERT INTO nfl_tg VALUES (?,?,?,?,60,35,25,20,40,34,24,33)", (gid, t, o, h))
        for pid, pos, team, opp, tg_ in (("p1", "WR", "AAA", "BBB", 6 + w), ("p2", "WR", "BBB", "AAA", 5),
                                         ("q1", "QB", "AAA", "BBB", 0)):
            con.execute(f"INSERT INTO nfl_pg ({cols}) VALUES (?,?,NULL,?,?,?,?,?,?,2024,?,'regular',?,?,?,0,0,0,0,0,"
                        "?,?,?,0,?,0.2,0.2,50,0.8,0,0,0,0,'nflverse')",
                        (gid, pid, pid, pos, pos, team, opp, 1 if team == "AAA" else 0, w,
                         30 if pos == "QB" else 0, 20 if pos == "QB" else 0, 230 if pos == "QB" else 0,
                         tg_, tg_ - 2, 10.0 * tg_, 8.0 * tg_))
    con.commit()
    from sports.model import featurize

    def feats(c):
        df = featurize.nfl_frame(c, h_opp=4, with_lags=False)
        df = pnf.augment(df, c)
        i = [j for j in range(len(df)) if df["player_id"][j] == "p1" and df["game_id"][j] == "g6"][0]
        return {k: v[i] for k, v in df.cols.items() if k.startswith(("e2_", "e8_", "W8_", "lag1_", "sm_", "oa_", "team_e_"))
                or k in ("adot", "implied_targets", "qb1_lag", "n_season", "gap_days")}
    a = feats(con)
    con.execute("UPDATE nfl_pg SET targets = 30, rec = 25, rec_yds = 400 WHERE game_id = 'g6' AND player_id = 'p1'")
    con.commit()
    b = feats(con)
    for k in a:
        assert (a[k] == b[k]) or (a[k] != a[k] and b[k] != b[k]), k
    con.close()
