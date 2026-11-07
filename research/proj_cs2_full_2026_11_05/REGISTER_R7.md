# Round 7 factor test (r7): round-level, economy, score-state, side, trade and opponent-tier factors on top of pd-1

Frozen 2026-11-07 BEFORE any r7 result was computed. Research only; nothing is deployed; live grades are unchanged.

## Why
The owner's checklist (2026-11-07) asks for every factor that can move a projection: map/round level, score state, economy,
side splits, pistol / full-buy situations, rank-tier splits, trades, clutches, openings. pd-1 (2026-11-05) already uses
openings, multi-kills, clutches, KAST, ADR, rating, swing, kill share, team/opponent rounds, kills conceded, overtime rate,
Elo, rest, lineup changes, expected map pick. It does NOT use the round tables (`map_rounds`, `map_round_economy`) or the
per-side player rows. FACTOR_AUDIT.md listed economy and openings as "unavailable"; the extract of 2026-11-05 has them for
28,959 matches (2024-01-09 .. 2026-11-05).

## Candidate
pd-2r = pd-1's model (same LightGBM Poisson mean + gamma variance ratio + negative binomial, same params, same monthly
walk-forward, same windows) with the r7 feature groups of `features_r7.py` added: ECON, ROUND, STATE, SIDE, TRADE, TIER.
No line input, no chance shrink, no side intercept, no balance forcing. Nothing tuned after the first run.

## Reference
pd-1's saved walk-forward predictions (`pd1_preds.pkl`, same code and data); the offline live reconstruction; the exact
logged live chance on P1 units.

## Pass rule (all must hold)
1. All outcomes, development window (2025-11 .. 2026-09): pooled log-score gain of pd-2r over pd-1 with the 95%
   match-clustered interval above 0.
2. All outcomes, final window (2026-10-01 .. 2026-11-05): the same, interval above 0.
3. Settled book lines (graded standard PP/UD props): log loss pd-2r minus pd-1 not above 0 (point estimate), stated minus
   hit within 2.5 pts overall / Over / Under, graded count >= 90% of pd-1, graded hit not below pd-1's, and the Over share
   of picked sides not more than 3 pts lower than pd-1's (correlated-Under rule).
4. Group ablation (only if 1-2 pass): each group is dropped in turn; a group is credited only if dropping it lowers the
   development log score with the interval below 0.

Passing makes pd-2r a stronger research candidate only. Promotion would still need the pd-1 blockers (right-skew Under
lean, a real line gain over the live tag, the `bo` leak) and a forward shadow, and the owner's OK.
