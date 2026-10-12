# Frozen shadow challengers

Shadow only: a challenger never changes a member's grade, chance or stake. It is scored next to the live model on the
same forecasts and decided only at the fixed review windows (learning.REVIEW_RULE: 300 / 600 / 1000 distinct forward
matches). Passing = "eligible for review"; promotion is an explicit admin action with a recorded reason, versioned, with
rollback. A correction or method change is a NEW family/version with its own evaluation period.

## lb-001 - probability-shrinkage challenger (family id `line-blend-1`, frozen 2026-10-12 15:49:04 UTC on production)

**Name.** It is a *probability-shrinkage* challenger: it pulls our own published chance toward 50%. It does **not** use
the book's price, and it does **not** test whether our projected totals beat the offered line (the family id
`line-blend-1` stays as frozen; the earlier "trust the line" wording was inaccurate). With kills w = 0 it is a constant
50% conditional Over baseline for kills. Two separate questions are therefore reported, never merged (learning
scorecard `questions`, dashboard "Two separate questions"):

- **Q1 - do our probabilities beat a 50% baseline?** Log-loss of the published P(Over | no push) vs 0.5, paired per
  unit, intervals by canonical match. lb-001's kills arm answers exactly this on forward matches.
- **Q2 - do our projected totals predict outcomes better than the offered lines?** |line - actual| - |projection - actual|
  per unit (positive = our projection was closer), intervals by canonical match. lb-001 does not test this.

A 300 / 600 / 1000-match checkpoint does not guarantee that either question is resolved: the review records the
interval; an interval that still contains 0 is "not demonstrated", not "no difference".

**What it transforms.** The live forecast's PROBABILITY - not the projected total and not the kill rate:

    p_ch = Phi( w * Phi^-1( p_live ) )

- `p_live` = the logged forecast's `p_more` = P(Over) / (P(Over) + P(Under)), i.e. the chance of Over **conditional on no
  push** (grader.p_more), rounded to 3 decimals, after every live adjustment (kills chance cap 78%, safety margins,
  the HS beta-binomial, the kills discretised normal). Clipped to [1e-6, 1 - 1e-6] before Phi^-1.
- `Phi` = the standard normal CDF. Code: `learning.line_blend_p`.
- `w` per stat on Maps 1-2: **kills 0.0, HS 0.5**. Other scopes (Map 1, Maps 1-3): `p_ch = p_live` (unchanged).
  - w = 0.0: `p_ch = 0.5` exactly, for every distribution and line - "the line is the median", no information beyond
    the market line.
  - w = 0.5: the probit distance from 50% is halved (e.g. 0.70 -> 0.6034, 0.60 -> 0.5504).
  - w = 1.0 would reproduce the live chance.
- **Relation to a projection blend.** If the live chance came from a normal distribution N(lambda, sd) with no cap,
  `Phi^-1(p_live) = (lambda - L*)/sd` and `p_ch` equals the chance under `projection' = L* + w (lambda - L*)` with the
  same sd (L* = the line incl. the half-point cut). The live model is NOT always that (kills cap, HS beta-binomial,
  push removal), so lb-001 is defined as the probability transform above; "pull the projection toward the line" is an
  approximation of it, not its definition.
- **Line used.** The forecast's own logged line at forecast time (the evaluation unit's forecast = the latest forecast
  logged >= 30 min before the start). No later line movement enters: the transform reads only that forecast's p_more.
- **Spread / distribution.** Not re-estimated: it is whatever produced `p_live`. Changing the line changes `p_live` the
  way the live model does, and `p_ch` follows monotonically (see properties).
- **Over / Under / push (exact).** lb-001 outputs the conditional pair
      P_ch(Over | no push) = p_ch,  P_ch(Under | no push) = 1 - p_ch.
  Unconditional values, when needed, combine it with q = P(push) of the live model's OWN predictive distribution:
      P_ch(Over) = p_ch * (1 - q),  P_ch(Under) = (1 - p_ch) * (1 - q),  P_ch(push) = q   (sum = 1).
  q comes from `forecasts.probs_raw["push"]` (saved with every forecast since 2026-10-12, together with the raw
  over / under chances before caps and margins); for earlier forecasts q is recomputed by replaying `grader.grade_leg` on
  the saved grader inputs (`forecasts.inputs`). Half-point lines have q = 0. Evaluation scores the decided outcome only
  (pushes are their own ledger bucket, never scored).
- **Published decision probabilities vs the raw predictive distribution.** p_live is the PUBLISHED decision chance -
  after the kills cap (22-78%), the old pipeline's Over margin, and rounding to 3 decimals - not necessarily the raw
  model distribution (`probs_raw`). Applying the transform to p_live therefore evaluates the published decision
  probabilities. In the unconditional formula above the push chance q is taken from the raw distribution while p_ch
  comes from the published one; reports label which is used.
- **Caps / margins.** Inherited through p_live; nothing is added or removed afterwards.

**Verified (tests/test_learning.py):** bounded in (0, 1); P(Over)+P(Under)+P(push) = 1 with the live push chance;
half-point lines -> push 0; for a fixed predictive distribution a higher line never raises P(Over) (live p_more is
non-increasing in the line and the transform is non-decreasing); deterministic replay from the saved forecast alone;
live and challenger values come from the same `forecast_copy` row (same input snapshot, same line).

**Fit and evidence (development, not a validation).** Board units Oct 3-12 (991 units / 132 matches; Maps 1-2: 800 units,
data hash 49e7ac29547b86a9). Walk-forward with w refitted on earlier days only: log-loss gain vs live +0.0149
[0.0039, 0.0264] (536 units / 87 matches), kills Maps 1-2 +0.0301 [0.0077, 0.0528], HS Maps 1-2 +0.0048 [-0.0062,
+0.0159]. Final w fitted on all 10 days (grid 0-1.2 step 0.05, minimum log-loss): kills 0.0, HS 0.5. Most of those
10 days were graded by the older pipeline (only 212 units / 29 matches were perf-2), so the forward test is what counts.
