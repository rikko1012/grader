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

## Results (run 2026-11-07 after the definition above was committed in 654855e; scoring plumbing fix only, no rule change)
Raw output: `r7_score_2026-11-07.json`. Log-score gain of pd-2r over pd-1 per row, 95% match-clustered.

| cell | dev rows | dev gain [95%] | final rows | final gain [95%] |
|---|---|---|---|---|
| kills Map 1 | 102,787 | -0.0005 [-0.0018, +0.0009] | 12,911 | -0.0011 [-0.0043, +0.0024] |
| kills Maps 1-2 | 93,384 | -0.0001 [-0.0016, +0.0011] | 11,059 | +0.0012 [-0.0020, +0.0050] |
| kills Maps 1-3 | 37,852 | -0.0017 [-0.0040, +0.0004] | 4,690 | -0.0040 [-0.0104, +0.0024] |
| HS Map 1 | 102,747 | +0.0002 [-0.0006, +0.0009] | 12,902 | +0.0014 [-0.0007, +0.0036] |
| HS Maps 1-2 | 93,315 | +0.0004 [-0.0003, +0.0012] | 11,049 | +0.0003 [-0.0017, +0.0024] |
| HS Maps 1-3 | 37,796 | -0.0005 [-0.0020, +0.0009] | 4,683 | -0.0001 [-0.0042, +0.0039] |
| **pooled** | | **-0.0002 [-0.0009, +0.0005]** | | **+0.0000 [-0.0017, +0.0018]** |

Book lines (11,717 settled graded standard props): log loss pd-1 0.6919 -> pd-2r 0.6926 (+0.0007 [-0.0008, +0.0022]);
stated - hit pd-2r +2.50 / Over +2.17 / Under +2.72 (pd-1 +2.12 / +1.62 / +2.42); graded 5,452 vs 5,309; graded hit
55.2% vs 55.3%; Over share 38.7% vs 38.3%. P1 exact (5,190): graded hit 55.4% vs pd-1 55.5% vs live 53.7%; log loss vs
live exact -0.0028 [-0.0070, +0.0014].

| rule | result |
|---|---|
| 1. dev gain CI above 0 | **FAIL** |
| 2. final gain CI above 0 | **FAIL** |
| 3. book lines | **FAIL** (log loss worse than pd-1; Under gap 2.72 > 2.5) |
| 4. ablation | not run (1-2 failed, as registered) |

**Verdict: nothing promoted.** Economy, pistol, round-end type, score state, CT/T side, trades, flash assists and
opponent-tier splits carry no information beyond pd-1's decayed player rates, team/opponent rounds and kills conceded, Elo
and map pool. Likely reason: each is a slower, noisier view of the same player and team strength that the decayed kill rate
and team round stats already measure; the round-level detail matters inside a map, not before it.
FACTOR_AUDIT.md correction: economy rounds, opening duels, trades and clutches are NOT unavailable - they are in the HLTV
extract (map_round_economy, map_rounds, map_player_stats) and are now tested (openings/clutches inside pd-1, the rest here).
