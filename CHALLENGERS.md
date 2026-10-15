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

### lb-001 provenance notes (added 2026-10-12 after the evidence audit; the frozen config is unchanged)

- **Data hash recipe.** `49e7ac29547b86a9` = first 16 hex of SHA-256 of `json.dumps(sorted((player.lower(), match_slug, prop)))`
  over the board units of `diag/units.pkl` (one per player x match slug x stat x scope, latest board forecast logged
  before the start; Railway export <= Oct 10 Central + Render export fetched 2026-10-12 15:31:44 UTC) with status
  settled, a projection, actual != line, scope Maps 1-2: 800 units. Reproduced from the saved file.
- **Training cutoff label.** The config's `training_cutoff` "2026-10-12T15:00:00Z" is approximate: the latest match
  start in the development data is 13:00 UTC, but 29 units (2 matches) settled 15:11-15:28 UTC, after 15:00 and before
  the freeze (15:49:04). Effective data cutoff = the export at 15:31:44 UTC. No development match can enter the forward
  cohort: forward requires forecast logged after 15:49:04 AND match start after 15:49:04; every development match
  started by 13:00 UTC.
- **Development units still count units of the old slug identity** (a match logged under a BO3.gg slug and hltv-<id> was
  two units then); the forward evaluation uses the canonical match identity (identity.assign).

## pl-2 - pl-1 with corrected anchor inputs (family `prop-line-2`, id `pl-002-<hash>`, frozen at its first learning cycle after deploy)

**Why.** pl-1 can use boosted PrizePicks anchors and stale or withdrawn other-platform lines. Freezing preserved its
record; it did not make those inputs valid. **pl-1 is kept as recorded and still logged**, so that pl-2 vs pl-1 isolates
input eligibility, but its **promotion is blocked** (status `blocked`; never reviewed).

**Unchanged from pl-1:** weights (kills 0.0, HS 0.25), the normal with the live sd, the half-integer cuts, pushes
conditioned out, and the kills cap.

**Inputs: only the anchors logged with the forecast at forecast time** (`forecasts.quote.anchors`). They are never taken
from a later board snapshot.
- Target: the quote from the feed snapshot the line was graded on.
- Other: the other platform's offer from its feed cache at that same moment, matched on BO3.gg player id (from that
  line's own board grade) + canonical match + stat + map scope + standard market type.
- Each record keeps: source id / market id, line, market type, sides offered, side prices and payouts, the provider's
  times (`source_updated_at`, `source_seen_at`, PrizePicks `provider_read_at`), and **when our server read its cache**
  (`observed_at`) - kept distinct from the source quote's age.

**Eligibility, fixed before any outcome:**
- **Target:**
  - standard market type (never promo / boosted, alternate, goblin / demon);
  - both sides confirmed offered by the feed (missing = unknown = not verified);
  - Underdog side prices equal when shown (unequal = priced off the median);
  - our cache read no older than 30 min at the forecast (a board pass can be long).
  - Otherwise there is **no pl-2 forecast** for that line, and the reason is recorded.
- **Other platform:** the same rules, a cache read no older than 10 min, and its feed state ok. Otherwise it is not used,
  its reason is recorded, and the eligible target line anchors alone.
- **Labels:** Underdog with equal side prices = price-supported. PrizePicks (no side prices) = price-unverified; its
  ordinary lines are not invalid, but equal probability is never inferred.

**Comparison set (pl-2's own; never mixed with pl-1's C1).** Arms on pl-2's eligible forward cohort:
- A2: the target line only.
- B2: the corrected anchor (target + eligible other platform).
- C2: pl-2.
- D: live.

Reported:
- coverage and every rejection reason;
- the other platform's use and rejection reasons;
- the subset where the other platform CHANGED the anchor (an identical line adds no information);
- the kills identity C2 = B2 (exact);
- an exact replay of every pl-2 value, settled or not, by re-running pl-2 on its saved inputs: the forecast copy's
  grader-input features, line, platform and time, plus the logged anchors (`learning.pl2_replay_check`). The note's trace
  is a rounded summary, never the replay source;
- point forecasts: MAE for median-type quantities, RMSE for the mean;
- probability scores, separately.

**Forward clock:** from pl-2's own freeze. Only forecasts logged after it, before the match start, count. Same registered
review windows.

## pl-1 - corrections and experiment definition (2026-10-15, owner review; pl-1 itself unchanged)

pl-1 = `pl-001-bb351ec5`, frozen 2026-10-15 02:22:18 UTC. Its code, weights and logged rows are unchanged. These
corrections fix how it is described and add analyses registered before any of its forward forecasts settled.

- **It is a LINE-ANCHORED baseline, not a market median.** Both sides being offered does not establish equal chances.
  - Underdog shows per-side prices. In a sample of 184 offers (2026-10-15 01:16-02:31 UTC), 180 were equal (1.90 / 1.90)
    and 4 unequal (1.80 / 2.03, about 53% Over after removing the margin).
  - PrizePicks shows no side prices.
  - pl-1 read neither, so its "median" reading is an assumption. The note key `median_market` holds the line anchor m,
    and `mean_combined` holds pl-1's distribution LOCATION (frozen key names).
- **Eligibility as pl-1 applies it.**
  - The target line must be a standard board line. Underdog lines with unequal payout multipliers never reach the board.
  - PrizePicks promo (boosted) lines are logged as "standard", so pl-1 does NOT exclude them.
  - Underdog unequal side prices are not checked.
  - Another platform's line enters if it is the latest standard board line for the same player (canonical slug) x
    canonical match x stat x map scope, logged at or before the forecast. There is **no staleness limit**: the latest
    earlier log is used even if the line was pulled.
  - If no other line exists, only the target line enters. Lines are combined by a simple mean.
  - From 2026-10-15 every board forecast logs its quote (`forecasts.quote`: side prices / payouts, the feed's market type
    incl. promo, sides offered, provider update time, board fetch time). That lets each pl-1 anchor be classed:
    - Underdog equal prices: price-supported median.
    - Underdog unequal prices: not a median.
    - PrizePicks: line-anchored assumption.
    - PrizePicks promo: not a valid anchor.
    - Before the quote column existed: no quote.
  - **A version that uses prices or drops stale or boosted lines would be a new version (pl-2), registered before its
    data, never retrofitted.**
- **Predictive distribution, exactly.**
  - Location mu: pl-1 = m + w (lam_H - m); the baseline = m. Scale: the live predictive sd.
  - Discretised as a continuous normal at the half-integer cut, so the outcome is an integer count:
    - x.5 line L: P(Over) = 1 - Phi((floor(L) + 0.5 - mu) / sd).
    - Whole line L: P(Over) = 1 - Phi((L + 0.5 - mu) / sd), P(Under) = Phi((L - 0.5 - mu) / sd), push = the rest.
    - The chance used is P(Over | no push) = Over / (Over + Under). Kills are capped at 22-78%.
  - mu is the location of the continuous normal (its median and its mean). The implied count distribution's median and
    mean are close to mu but not identical.
  - lam_H is the live model's mean. The line is a threshold. The three are not interchangeable.
- **Headshot weight 0.25 is FITTED, weakly identified.**
  - The frozen rule (pl_fit.py, written before running) fits on all earlier days. Folds only estimate out-of-sample
    performance. The 1.2 / 0 / 0 fold weights came from 6 / 17 / 22 training matches.
  - All-day profile, mean log-loss: w 0 0.69356, 0.2 0.69245, **0.25 0.69238**, 0.3 0.69239, 0.4 0.69266, 1.0 0.70115.
    It is nearly flat between 0.1 and 0.4.
  - Design history: free spread first (HS w 0.4, k 1.5 at the grid edge), then a widened grid (w 0.8, k 3.0 at the edge),
    then spread fixed, which gave 0.25. Spread-fixing was decided after seeing the first two fits.
  - In development, the line-anchored baseline beat pl-1 (-0.0113, -0.0228 to -0.0012). **pl-1 is not the preferred
    candidate.**
- **Comparison set C1, registered 2026-10-15 03:00 UTC** (learning `C1_REGISTERED`), arms on identical forward
  book-line forecasts:
  - A: target line only, N(L, sd) at L (about 50%).
  - B: target + eligible other platform (the logged baseline).
  - C: pl-1.
  - D: live.
  - Reported: B vs A (cross-platform information, also on the units with another line), A vs D, B vs D, C vs B, C vs D,
    and B vs D by anchor class.
  - Kills identity check: w = 0, so C must equal B to its 4-decimal logged rounding. 0 differences expected; it is
    counted on the dashboard.
  - Point forecasts: MAE for the median-type quantities (target line, anchor m, pl-1 location) and RMSE for the
    mean-type lam_H. A small MAE does not show an accurate mean.

## pl-1 - market-informed prop challenger (family `prop-line-1`, id `pl-001-<hash>`, frozen at its first learning cycle after deploy)

**Does rr-1 already cover it? No.** rr-1 takes the MATCH-WINNER price (Thunderpick, else Polymarket) and turns it into
expected rounds. It never sees a prop line. pl-1 takes the PROP LINES and never sees the match-winner price. Separate
families, so each market contribution stays traceable. rr-1 and its results are unchanged.

**Inputs.** All of these are logged at or before the forecast's own time.
- **Independent projection:** lam_H, the live perf-2 HLTV mean, with its predictive sd. It is logged as it is in every
  shadow row (`lam_base`, `note.lam_independent`) and never replaced.
- **The target platform's standard line:** the line being graded.
- **The other platform's latest standard board line** for the same player x canonical match (current identity, by pick
  id) x stat x map scope, logged at or before the forecast (`note.other`: book, line, time, forecast id).
- **Never entered:** match-winner odds (rr-1), member-entered lines, adjusted markets (goblin / demon), and lines the
  feed offers Over only.

**Threshold vs mean.**
- A two-sided standard line (equal payouts on both sides) is read as the market's MEDIAN: P(over) = P(under). It is
  never read as its mean.
- A line offered Over only is a threshold with unknown pricing, so it never enters the median. Before 2026-10-14 the
  feed's side availability was not saved, so unknown counts as two-sided; it is labelled `side_info: assumed`.
- m = the mean of the two-sided lines. Combined median mu = m + w[stat] x (lam_H - m).
- P(Over) is computed from a normal with the live sd at the target line, pushes conditioned out on whole lines and kills
  capped 22-78% like live. For a normal, median = mean, so `lam_ch` = mu. The normal replay of the live chance differs
  from the published one by 0.009 on average.

**Simple market baseline,** logged in the same row: N(m, sd_H) at the target line (`note.p_market`). It has no free
parameter.

**Fit (earlier data only, outcomes as the target).**
- Data: board standard lines, 2026-10-11..14, 635 units / 36 matches, data hash f1274a945c16b1da.
- w per stat on a grid 0..1.2, minimum log-loss of the Over outcome at the target line.
- A free spread multiplier ran to the grid edge (3.0, i.e. "push everything to 50%"): ill-posed on 36 matches. The
  spread is therefore fixed at the live sd, whose width was validated on held-out HLTV months and in the checkpoint's
  width check.
- Frozen weights: **kills w = 0.0, headshots w = 0.25.**
- With kills w = 0, pl-1's kills chance is the market median's; it differs from 50% only when the platforms' lines
  differ. Overlap with lb-001: with no other-platform line, both pull toward the target line.

**Development evidence (walk-forward by match day; 521 units / 30 matches; NOT a validation).** These are the days
already reported at the checkpoint.
- Per-fold weights: kills 0 / 0 / 0, headshots 1.2 / 0 / 0. Headshots are unstable.
- Kills, market baseline vs live: +0.0247 log-loss (-0.0011 to +0.0529). The market median's average miss was 5.29 kills
  vs the independent projection's 5.55.
- Headshots, market baseline vs live: +0.0122 (-0.0102 to +0.0342).
- pl-1 vs live: +0.0073 (-0.0107 to +0.0244).
- pl-1 vs the market baseline: -0.0113 (-0.0228 to -0.0012), driven by the 6-match first headshot fold.

**Decision rule.** Unchanged: the registered 300 / 600 / 1,000-match windows (REVIEW_RULE). Reported on P1 board lines
beside the registered view, with three arms on identical forecasts: live, market baseline and pl-1. The dashboard shows
the market baseline vs live, pl-1 vs the market baseline, and the projection error of the independent, market-median
and combined means. An improved agreement with the line is never itself the target.

## rr-1 - market-informed projection (variant `rr-ks`, frozen 2026-10-13 17:51 UTC; logged from this release)

**What it changes (in shadow only).** The projected kills / headshots: the market's series price p for the player's
team (the forecast's own selected qualified market input - Thunderpick when eligible, else Polymarket) -> a
model-derived per-map chance q by the series format -> a calibrated MR12 round race (rounds won W(q) / lost L(q)) ->
kill-weighted rounds a W + b L, divided by the history the performance model already contains (both teams' historical
round-win shares) so strength is not counted twice. Headshots stay conditional on the candidate kills. Exact definition,
constants, data, splits and pass rules: FACTOR_AUDIT.md round 6; code `perf_market.py` (+ `perf_market_tables.json`).

**Logged as** `shadow.market_rounds` on every perf-2 forecast: version, variant, p_series, market source and retrieval
time, format, q, both round shares, expected (kill-weighted) rounds, H, lambda_kills, lambda, sd, P(Over) / raw
Over-Under-push, side, grade and stake from the SAME grader and stake rules, and the perf-2 lambda. No price, BO2 /
unknown format, BO1 beyond Map 1, a BO3.gg-provisional projection or an unreadable round share: no candidate number
(status says why) - never a stand-in.

**Evidence so far (development only).** On 947 settled production lines (19 matches; latest forecast >= 30 min before
start; Polymarket prices) vs the live formula: projection MAE -0.097 (95% -0.174..-0.022), log-loss -0.010
(-0.020..-0.0005), Brier -0.0048 (-0.0097..-0.0003); Maps 1-2 kills alone crosses 0. 19 matches is far below the review
windows. No settled Thunderpick-priced line yet. Historical frozen test (once, after freezing): PASS - TEST Maps 1-2 kills MAE -0.0535 (-0.0774..-0.0337), 996 matches; the gain comes from the market (vs no-market control -0.0848); see FACTOR_AUDIT.md round-6 results.

**Decision rule.** Unchanged from the registry: reviewed only at the 300 / 600 / 1000 forward-match windows, plus the
round-6 historical result; promotion is an explicit admin action with a recorded reason and a new model version.
lb-001 and the other frozen challengers are unchanged.

**rr-1 forward note 2026-10-14 (first settled Thunderpick-priced lines; development only).** 350 settled lines whose
latest forecast >= 30 min before start used a captured Thunderpick quote (8 matches). C as logged at the time vs the
published forecast: kills MAE -0.022 (-0.473..+0.488), HS MAE -0.073 (-0.280..+0.153), log-loss / Brier intervals
across 0 - no conclusion at 8 matches. Pure source swap A (pre-cutoff Polymarket price on identical inputs) vs B
(Thunderpick): projections identical on all 287 comparable lines, side / grade / stake different on 9 (market-quality
cap). Scripts: scratchpad tp_settled.py.

## prior_smooth - smooth newcomer prior (picklog shadow, logged since 2026-10-09) - ROWS BEFORE THE 2026-10-13 FIX ARE INVALID

**What it is.** The live perf-2 projection re-run with the prior target m(r) = 1 - (1 - 0.93) * 800 / (800 + r) instead of
the flat 0.93 (`picklog.PRIOR_SMOOTH`, `picklog.smooth_prior_pf`; exact rescale of `perf.project`, tests/test_prior_smooth.py).
Its version is the live version + `-prior-smooth`: `perf-2-prior-smooth`, or `perf-2-bo3-prior-smooth` for players graded on
BO3.gg history. Logged as `shadow.prior_smooth`; never shown, never changes a member's grade.

**Logging bug (found 2026-10-13).** `grader.grade_leg` uses the performance model only when the projection's version is in
`cfg["perf_versions"]`, and otherwise falls back to the old pipeline. The challenger's versions were not in
`CONFIG["perf_versions"]`, so every `shadow.prior_smooth` row logged from 2026-10-09 until the fix is the OLD pipeline's
output (the same numbers as `shadow.legacy`), not the smooth prior. Replay of all 909 forecasts with saved inputs in the
2026-10-08 to 10-11 pick log: under the old code prior_smooth lambda and p_more equal `shadow.legacy` on 316 of 316 rows.

**Fix.** `picklog._perf_challenger` grades a swapped-in projection on a COPY of the config with that projection's own
version added (the live `CONFIG` is unchanged), and every shadow row now carries `source` = grade_leg's
`projection_source`. Same 909-forecast replay with the new code: 316 of 316 prior_smooth rows graded by
`perf-2-prior-smooth` (266) / `perf-2-bo3-prior-smooth` (50), kills lambda = the challenger's own projection; every other
shadow (including `perf1_g7`, whose perf-1 versions were always live) identical apart from the added `source`. Member grades
are untouched: grader.py is unchanged, so the model_version tags do not change.

**Exclusion rule (all prior_smooth evaluations).**
- A `shadow.prior_smooth` row is valid only when `shadow.prior_smooth.source` ends with `-prior-smooth`.
- Rows without `source` (everything logged before the fix was deployed - it ships in release `23bea2fe37`, live from
  2026-10-14 22:53:13 UTC, MODEL_VERSIONS.md), or with a `legacy ...` source, are INVALID. They measure the old pipeline, which is already logged as
  `shadow.legacy`. Exclude and count them; never pool them with valid rows. From that release the performance model reads
  the pinned input revision N0 (HLTV_COVERAGE.md), so valid prior_smooth rows are on N0 too.
- Use the per-row rule, not a date cutoff on the match start. A forecast is appended only when the live forecast changes,
  so an evaluation snapshot logged before the deploy keeps its invalid row even for a match played after it.
- `diagnostics.shadow_p_more` applies the rule. The forward check (admin diagnostics, "Forward check") lists the excluded
  rows as `excluded_invalid` and the exact live tag of every counted row as `versions`.
- Report per exact tag (MODEL_VERSIONS.md). The fix does not create a new tag (picklog.py and diagnostics.py are not
  hashed), so `source` is the marker. Every prior_smooth result published before this fix (the forward HS check's
  "smooth-prior challenger" line included) described the old pipeline and is withdrawn.
