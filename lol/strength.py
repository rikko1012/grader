"""Sharper win chance (sp-1, lol/EVALUATION.md A12): Elo's win chance re-scaled and joined with each team's recent kill
margin. Standard library only (the live forecast imports it).

  p' = sigmoid(a * logit(p_elo) + c * (KD_team - KD_opp))
  KD = time-weighted (half-life HL days) kill differential per game over the team's games before the match day (0 if none)
"""
import math

HL = 60.0


def kd(games, day, hl=HL):
    """games: iterable of (day ordinal, team kills, opponent kills); only days before `day` count."""
    w = s = 0.0
    for d, tk, ok in games:
        if d < day:
            x = 0.5 ** ((day - d) / hl)
            w += x
            s += x * (tk - ok)
    return s / w if w > 0 else 0.0


def p_sharp(p_elo, kd_team, kd_opp, a, c):
    p = min(max(float(p_elo), 1e-6), 1 - 1e-6)
    z = a * math.log(p / (1 - p)) + c * (kd_team - kd_opp)
    return 1 / (1 + math.exp(-z))
