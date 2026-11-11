"""gu-1111 (REGISTER A13) research scoring helpers: letters from the pc-1 printed chance, the letter steps and the
owner acceptance checks. Research code only - nothing live imports it."""
import numpy as np

from research.sports_lab.grade_up_1111 import common as C


def test_printed_letter_uses_the_committed_pc1_slope_and_gp1_bands():
    assert abs(C.B_CAL["nba"] - 0.280821) < 1e-9 and abs(C.B_CAL["nfl"] - 0.157121) < 1e-9
    q = C.printed(np.array([0.5, 0.62, 0.70, 0.82]), "nba")
    assert abs(q[0] - 0.5) < 1e-12
    assert list(C.band_idx(q)) == [0, 0, 2, 3]                    # 0.62 raw prints 53.4% (Lean), 0.70 -> B, 0.82 -> A
    assert list(C.band_idx(np.array([0.5344, 0.5345, 0.5559, 0.60]))) == [0, 1, 2, 3]


def test_steps_never_hide_a_side_and_lean_needs_52_to_step_up():
    L = np.array([0, 0, 1, 2, 3])
    q = np.array([0.51, 0.52, 0.54, 0.57, 0.61])
    assert list(C.step_up(L, q)) == [0, 1, 2, 3, 3]
    assert list(C.step_down(L)) == [0, 0, 0, 1, 2]


def _rows(n_games=40, per=30, seed=3):
    rng = np.random.default_rng(seed)
    games = np.repeat(np.arange(n_games), per)
    ov = rng.random(len(games)) < 0.3
    hit = (rng.random(len(games)) < np.where(ov, 0.50, 0.56)).astype(float)
    L = rng.integers(0, 4, len(games))
    return games, ov, hit, L


def test_letter_arm_identity_fails_h1_only_and_count_cut_fails_h3():
    games, ov, hit, L = _rows()
    bt = C.Boot(games, b=200)
    same = C.letter_arm(bt, hit, ov, L, L.copy())
    assert same["changed"] == 0 and same["failed"] == ["H1"] and not same["pass"]
    cut = C.letter_arm(bt, hit, ov, L, np.where(ov, C.step_down(L), L))    # the G1 over-bar shape
    assert "H3" in cut["failed"] and "H4" in cut["failed"]
    assert cut["graded"]["arm"]["n"] < cut["graded"]["base"]["n"]


def test_side_arm_scores_flips_and_never_passes_an_unchanged_arm():
    games, ov, hit, _ = _rows()
    over = np.where(ov, hit, 1 - hit)
    po = np.where(ov, 0.7, 0.3)
    bt = C.Boot(games, b=200)
    same = C.side_arm(bt, over, po, po.copy(), "nba")
    assert same["flips"] == 0 and same["all"]["diff"] == 0 and not same["pass"]
    oracle = np.where(over == 1, 0.9, 0.1)                         # always right: every check passes
    best = C.side_arm(bt, over, po, oracle, "nba")
    assert best["all"]["arm"]["hit"] == 100.0 and best["checks"]["H1"] and best["checks"]["H2"]


def test_bootstrap_is_game_grouped_and_seeded():
    games, ov, hit, L = _rows()
    a = C.Boot(games, b=100).diff_ci(hit, L >= 2, hit, L >= 1)
    b = C.Boot(games, b=100).diff_ci(hit, L >= 2, hit, L >= 1)
    assert a == b and a[0] <= a[1]
