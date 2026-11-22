"""thin-t1 (soccer-grades-1120): tennis finite-score lines (games won / played, sets won / played, tie-breaks; set-1
games won / played) of players with 0-4 earlier exact matches at the same tour and best-of - the count model refuses
them (more_exact_history_needed). Dirichlet-multinomial predictive over the stat's finite support with a population
prior per (tour gender, best_of, scope, stat): mass_k = (count_k + K * pop_k) / (n + K).

Frozen from the walk-forward (cs2_scout_ops/logs/soccer_grades_1120/integrate/wf_thin_tennis_v2.py,
wf/thin_t1v2_walkforward.json): ESPN ATP / WTA singles 2026-01-01..11-19, 19,040 player-matches; population = the
tune window's (before 2026-09-01) thin player-matches; K by log loss (every stat chose the largest K, 20: a player's own
0-4 matches add almost nothing). Holdout 2026-09-01..11-19: every stat FAILED the pre-registered tier rule (Brier equal
to the base rate within 0.0013, chance bands outside their intervals) - v1 (all players' population) failed worse and
was seen first, so the holdout was scored twice. Every thin-t1 line is therefore 0U with the 'failed' reason; it shows
the model's letter and chance (owner 2026-11-20: testing decides the stake, never a blank prop). Challenger / WTA 125
are not in the test data: the tour prior is applied there untested (same 0U).
Changing any number here changes grades and needs a new walk-forward test."""

VERSION = 'thin-t1'
GENDER = {'atp': 'm', 'atp_challenger': 'm', 'wta': 'w', 'wta125': 'w'}
PRIOR = {
    ('m', 3, 'full_game', 'games_played'): (1397, [0.001063, 0.007442, 0.015237, 0.031538, 0.046421, 0.082566, 0.083983, 0.097449, 0.077605, 0.059178, 0.075478, 0.069809, 0.00815, 0.03012, 0.04146, 0.016655, 0.024451, 0.03012, 0.033664, 0.050673, 0.038625, 0.020907, 0.014529, 0.020907, 0.010985, 0.002481, 0.004607, 0.003898]),
    ('m', 3, 'full_game', 'games_won'): (1397, [0.001066, 0.004618, 0.008881, 0.018117, 0.026643, 0.048668, 0.057904, 0.061456, 0.053641, 0.043694, 0.048668, 0.020249, 0.197158, 0.145293, 0.063588, 0.054352, 0.055062, 0.038721, 0.030195, 0.019538, 0.002487]),
    ('m', 3, 'full_game', 'sets_played'): (1397, [0.678469, 0.321531]),
    ('m', 3, 'full_game', 'sets_won'): (1397, [0.37576, 0.16625, 0.457991]),
    ('m', 3, 'full_game', 'tie_breakers_played'): (1397, [0.621515, 0.324875, 0.049678, 0.003931]),
    ('m', 3, 'set1', 'games_played'): (1397, [0.039615, 0.07459, 0.128123, 0.240186, 0.243041, 0.000357, 0.090293, 0.183797]),
    ('m', 3, 'set1', 'games_won'): (1397, [0.021056, 0.04247, 0.075303, 0.128123, 0.123126, 0.04818, 0.437188, 0.124554]),
    ('m', 5, 'full_game', 'games_played'): (643, [0.00075, 0.00075, 0.00075, 0.005247, 0.003748, 0.008246, 0.006747, 0.056222, 0.032234, 0.03973, 0.056222, 0.038231, 0.06072, 0.035232, 0.042729, 0.035232, 0.023238, 0.026237, 0.02024, 0.030735, 0.033733, 0.027736, 0.035232, 0.032234, 0.042729, 0.035232, 0.023238, 0.024738, 0.014243, 0.030735, 0.014243, 0.014243, 0.036732, 0.024738, 0.02024, 0.018741, 0.014243, 0.003748, 0.006747, 0.002249, 0.005247, 0.003748, 0.00075, 0.003748, 0.005247, 0.00075, 0.00075, 0.00075]),
    ('m', 5, 'full_game', 'games_won'): (643, [0.000758, 0.000758, 0.000758, 0.003788, 0.003788, 0.005303, 0.005303, 0.029545, 0.02197, 0.032576, 0.029545, 0.038636, 0.034091, 0.031061, 0.026515, 0.02803, 0.029545, 0.031061, 0.115909, 0.115909, 0.047727, 0.041667, 0.044697, 0.055303, 0.056818, 0.059848, 0.031061, 0.025, 0.023485, 0.011364, 0.008333, 0.006818, 0.002273, 0.000758]),
    ('m', 5, 'full_game', 'sets_played'): (643, [0.45384, 0.328161, 0.217998]),
    ('m', 5, 'full_game', 'sets_won'): (643, [0.244186, 0.18062, 0.109302, 0.465891]),
    ('m', 5, 'full_game', 'tie_breakers_played'): (643, [0.460526, 0.389319, 0.118421, 0.01935, 0.01161, 0.000774]),
    ('m', 5, 'set1', 'games_played'): (643, [0.013138, 0.078053, 0.107419, 0.25425, 0.257342, 0.000773, 0.073416, 0.215611]),
    ('m', 5, 'set1', 'games_won'): (643, [0.006955, 0.042504, 0.051777, 0.136785, 0.129057, 0.036321, 0.453632, 0.142968]),
    ('w', 3, 'full_game', 'games_played'): (2476, [0.007831, 0.025904, 0.044378, 0.068072, 0.086145, 0.096988, 0.103815, 0.073293, 0.07249, 0.042369, 0.040361, 0.039157, 0.023494, 0.026707, 0.034739, 0.02992, 0.032329, 0.025502, 0.042771, 0.027108, 0.018273, 0.012651, 0.011044, 0.006225, 0.003815, 0.001406, 0.001406, 0.001807]),
    ('w', 3, 'full_game', 'games_won'): (2476, [0.005027, 0.016288, 0.029157, 0.043233, 0.049668, 0.066157, 0.073799, 0.051277, 0.047657, 0.02956, 0.028353, 0.023527, 0.23145, 0.110798, 0.046451, 0.046853, 0.04082, 0.027549, 0.022723, 0.007842, 0.00181]),
    ('w', 3, 'full_game', 'sets_played'): (2476, [0.691764, 0.308236]),
    ('w', 3, 'full_game', 'sets_won'): (2476, [0.410293, 0.16448, 0.425227]),
    ('w', 3, 'full_game', 'tie_breakers_played'): (2476, [0.816182, 0.168483, 0.013519, 0.001816]),
    ('w', 3, 'set1', 'games_played'): (2476, [0.065524, 0.135685, 0.186492, 0.207056, 0.223992, 0.000202, 0.091331, 0.089718]),
    ('w', 3, 'set1', 'games_won'): (2476, [0.038508, 0.083266, 0.109073, 0.112298, 0.116734, 0.048185, 0.407863, 0.084073]),
}
K = {"full_game|games_played": 20, "full_game|games_won": 20, "full_game|sets_played": 20, "full_game|sets_won": 20, "full_game|tie_breakers_played": 20, "set1|games_played": 20, "set1|games_won": 20}
TIERS = {"full_game|games_played": "failed", "full_game|games_won": "failed", "full_game|sets_played": "failed", "full_game|sets_won": "failed", "full_game|tie_breakers_played": "failed", "set1|games_played": "failed", "set1|games_won": "failed"}


def prior(competition, best_of, scope, stat, support):
    """(population mass aligned to `support`, K) or None when no tested prior exists for this line."""
    g = GENDER.get(competition)
    bo = best_of if best_of in (3, 5) else (3 if scope == 'set1' else None)
    entry = PRIOR.get((g, bo, scope, stat))
    if g is None or bo is None or entry is None or len(entry[1]) != len(support):
        return None
    return entry[1], K[f'{scope}|{stat}']


def mass(values, support, pop, k):
    n = len(values)
    out = [(values.count(s) + k * p) / (n + k) for s, p in zip(support, pop)]
    total = sum(out)
    return [p / total for p in out]


# tp-shrink-1 (exp-proj-1121): the count model (5+ exact matches) spreads ONE pseudo-match uniformly over the score
# support, which lifts the minority score (tie-breaks, a third set, a 0-set loss) and leaves a lumpy 5-20 match pmf for
# games played. With TENNIS_POP_SHRINK=1 the pmf is (count_k + K * pop_k) / (n + K) instead: pop = every best-of-3
# ESPN ATP / WTA singles player-match before 2026-08-22 (+0.5 smoothing per score), K = 40 pseudo-matches chosen by log
# loss on 2026-06-01..08-21 with pop fitted before 06-01 (grid 1u/2/5/10/20/40: 40 for every stat - the grid edge).
# Recon holdout 2026-08-22..11-19 scored once (synthetic lines, NOT offered lines): book-like lines 64.19% -> 66.49%
# (+2.30, day CI +1.66..+2.96, 12,377 rows), letters 66.3% -> 67.7%, days < 53% 0 -> 0 - but at book lines it picks a
# constant side per stat (sets played / tie-breaks 0% Over, sets won 100% Over): base-rate side picking (audit F14).
# Real offered lines (482 of 486 count rows reproduce from the board columns; round2/expansion/r2b/real_tennis.py):
# first_seen 148-112 -> 151-109 (+1.2 pts, match CI -4.1..+6.8, 49 matches, 4 days), p1 126-96 -> 133-89 (+3.2, CI
# -2.5..+8.8) -> NOT passed; the flag defaults OFF (binding rule). K = 40 is the grid edge for every stat (not bracketed).
# Challenger / WTA 125 use their tour's pmf (untested there). Best of 5: no pmf, unchanged.
# Evidence: cs2_scout_ops/logs/audit_1121/round2/expansion/wf_tennis.py, wf_tennis_results.json.
SHRINK_VERSION = 'tp-shrink-1'
SHRINK_K = 40
SHRINK_POP = {
    'm|full_game|games_played': [0.001292, 0.006763, 0.015274, 0.037462, 0.051748, 0.079103, 0.084271, 0.088526, 0.075456, 0.050228, 0.066945, 0.057827, 0.01345, 0.037766, 0.045365, 0.025, 0.031687, 0.037462, 0.037766, 0.044149, 0.03503, 0.023784, 0.01193, 0.018313, 0.011626, 0.002204, 0.004939, 0.004635],
    'm|full_game|games_won': [0.000684, 0.003421, 0.007679, 0.018779, 0.025926, 0.041283, 0.04843, 0.054968, 0.047366, 0.037938, 0.036722, 0.026686, 0.223447, 0.148939, 0.063636, 0.054968, 0.056489, 0.04174, 0.037026, 0.019539, 0.004334],
    'm|full_game|sets_played': [0.639028, 0.360972],
    'm|full_game|sets_won': [0.319528, 0.18051, 0.499962],
    'm|full_game|tie_breakers_played': [0.665423, 0.282658, 0.047275, 0.004644],
    'm|set1|games_played': [0.030822, 0.088661, 0.14376, 0.234475, 0.246347, 7.6e-05, 0.090487, 0.165373],
    'm|set1|games_won': [0.015449, 0.044368, 0.071918, 0.117275, 0.123212, 0.045282, 0.454566, 0.12793],
    'w|full_game|games_played': [0.005206, 0.021709, 0.037426, 0.058644, 0.081434, 0.094794, 0.097151, 0.083006, 0.070825, 0.042141, 0.049607, 0.033104, 0.024853, 0.031139, 0.038605, 0.037819, 0.038998, 0.032711, 0.035069, 0.025246, 0.017387, 0.016208, 0.009136, 0.009921, 0.004028, 0.001277, 0.00167, 0.000884],
    'w|full_game|games_won': [0.002654, 0.010911, 0.018775, 0.029392, 0.040794, 0.050821, 0.058292, 0.049051, 0.040008, 0.031751, 0.028998, 0.025853, 0.271012, 0.127494, 0.051017, 0.048462, 0.048068, 0.036076, 0.020938, 0.008355, 0.001278],
    'w|full_game|sets_played': [0.674119, 0.325881],
    'w|full_game|sets_won': [0.337075, 0.162974, 0.499951],
    'w|full_game|tie_breakers_played': [0.801989, 0.179697, 0.017428, 0.000886],
    'w|set1|games_played': [0.059154, 0.13002, 0.181201, 0.207972, 0.224902, 9.8e-05, 0.100098, 0.096555],
    'w|set1|games_won': [0.029626, 0.065059, 0.09065, 0.104035, 0.1125, 0.050098, 0.449705, 0.098327],
}


def shrink_on():
    import os
    return os.environ.get('TENNIS_POP_SHRINK', '0') == '1'


def shrink_prior(competition, best_of, scope, stat, support):
    """(population pmf aligned to `support`, K) for tp-shrink-1, or None (flag off / no tested pmf for this line)."""
    g = GENDER.get(competition)
    bo = best_of if best_of in (3, 5) else (3 if scope == 'set1' else None)   # set-1 lines often carry no format
    if not shrink_on() or g is None or bo != 3:
        return None
    pop = SHRINK_POP.get(f'{g}|{scope}|{stat}')
    if pop is None or len(pop) != len(support):
        return None
    return pop, SHRINK_K
