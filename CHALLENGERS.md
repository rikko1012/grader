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

## cm-1 - complete model for forward confirmation (family `complete-model-1`, id `cm-001-<hash>`; production shadow only)

- **Why:** the one candidate selected under PROTOCOL_MODELS.md (REALLINE_ARCHIVE_2026-10-16.md section 9). It is
  development evidence only: every period scored had been inspected before. It is NOT an established accuracy gain, and its
  58.8% development hit rate is not a validated future rate.
- **Frozen artifact:** `cm1_model.json`.
  - Artifact hash: `cm1.ARTIFACT_SHA` (SHA-256 of the canonical JSON) = `2799a7f4a1b545e1`.
  - Training: 98,121 player-series, matches 2025-11-01 .. 2026-10-16; training cutoff 2026-10-16; data hash 9073466e2d54.
  - Frozen 2026-10-16 22:08 UTC.
- **Mean:** Poisson regression (log link, L2 alpha 1e-4) on 14 standardised pre-match features. They are listed with their
  formulas in the artifact: full-history rate / environment rate / opponent factor, history sizes, LAN, pre-match Elo of
  both teams, both teams' rounds per map, and the opponent-adjusted rate with the joint opponent factor.
- **Uncertainty (not Poisson variance):** the study's discretised normal, sd = c sqrt(lambda).
  - Kills: c 1.4732, chances capped 22-78%.
  - HS: lambda_kills x the full-history HS ratio, c_hs 1.2781, no cap.
  - Pushes are excluded exactly as live p_more.
- **History rules:**
  - Full-scoreboard maps only (10 players), all tiers, since 2025-09-01.
  - Rows in [match day - 420, match day), weights exp(-age / 120); match day = the UTC date of the start.
  - Opponent-adjustment refits every Monday, on maps strictly before the refit; the latest refit on or before the match
    day that exists when the forecast is made.
  - Elo from every series since 2025-09-01, taken at the start of the match day.
- **Missing data:**
  - Ineligible (reason saved): no HLTV player; no full-scoreboard map since 2025-09-01; own team or opponent not
    identified; LAN / online unknown; not Maps 1-2 kills / HS; no refit.
  - A player with history but none in the window gets prior-only sums and opponent factor 1, as the study did.
- **Production (cm1.py):**
  - **Tables:** the learning cycle's separate low-priority process rebuilds them from the HLTV database every 2 h. It reads
    the study's dump query by month and writes an atomic file with its own hash: about 25 s, 3 MB.
  - **Forecast:** when the board writes a forecast's pre-match record (perf_shadow), cm-1 is computed from the tables on
    disk. The record saves the intermediate sums, the 14 features, lambda, the chance, the line, the offered sides and the
    platform / board times, and the tables' build id and hash.
  - **Isolation:** the live projection, chance, grade and stake are untouched. The fh-1 and live arms are byte-identical
    with and without cm-1 (tests/test_cm1.py).
- **Verified before rollout:** on the study's own dump, the production code reproduces the research implementation for
  all 17,028 test player-series.
  - Every history sum and Elo is exact; lambda agrees to 6e-15 relative.
  - Line probabilities agree to 5e-10 on all 1,927 decision-time forecasts.
  - A map on the match day itself changes no input.
  - About 0.13 ms per forecast (research/realline_archive_2026_10_16/repro_cm1.py).
- **Replay:** perf_shadow.check_record recomputes cm-1 from each record's saved sums (the learning cycle's fh-1 replay
  includes it).
- **Scoring:** on the same P1 units as live (Maps 1-2 kills + HS), from the latest pre-match record at least 30 min before
  the start.
  - Probability scores and the fixed 57% rule are reported separately, by stat, with match-grouped intervals.
  - **Forward record = forecasts logged after its registry freeze only.** Nothing is backdated or reconstructed.
  - Promotion only through the registered review.
- **Registry:** `cm-001-4e5f42fb`, frozen_at 2026-10-16T22:35:14Z (the forward clock). The shadow went live at 22:31:36 (commit
  325e947); no record with a cm-1 value was written before 22:35:14.
- **Logging repair (2026-10-18; picklog `_shadow_arms`):** a record used to be repeated only when the live / fh-1 arms
  changed.
  - So cm-1 becoming available after its deploy, or moving with its 2-hourly tables, was not saved while the live grade
    stayed the same.
  - In 48 of 102 consecutive records over a tables change, its chance had moved.
  - **Correction (2026-10-18 17:00 UTC):** in all 49 record pairs where cm-1 moved, the history inputs (inputs_sha) also
    changed, so the old rule saved those records too. A recurrence in ordinary operation is not shown; the only
    observed gap is the deploy transition below.
  - Now any shadow arm that appears, becomes available or unavailable, or changes value appends a new timestamped
    pre-match record. Earlier records are unchanged.
  - **Gap, permanent:** 37 of the first 271 forward units (Oct 17 matches) have no cm-1 value. Their last record at least
    30 minutes before the start predates the deploy. They are reported as missing, never filled in.

## cm-2 - corrected complete model, Maps 1-2 kills only (family `complete-model-2-kills`, id `cm-002-<hash>`; production shadow only)

**2026-10-20: promoted by owner override** (MODEL_VERSIONS `8404c86d3c`): kills Maps 1-2 live grades use cm-2 (`perf-cm2`) before
the 300-match review. The shadow record, its registration and its forward clock are unchanged and keep running; from the
promotion on, the "live" side of the cm-2 comparison is cm-2 itself on those lines, so the review compares against the
saved `perf2` projection (fh-1 live arm). Rollback `CM2_LIVE=0`.

Accuracy handoff 2026-10-18 (claude-handoffs/accuracy-2026-10-18). A SEPARATE experiment from cm-1. cm-1's artifact,
records, registration, clock and results are unchanged, and none of them count for cm-2.

- **Artifact:** `cm2_model.json` = the research's `candidate_artifact.json`, the same JSON content (canonical sha256
  6e586047...c37c; the supplied CRLF file's raw sha256 3997cfce...11b1 is kept as provenance).
  - 14-feature GLM, fixed regularization, fit through 2026-09-30.
  - Nothing is fitted, searched or tuned in production.
  - The feature constants equal cm-1's (checked at import), so the weights apply to the features they were fitted on.
- **Inference:** `cm2.forecast` = `candidate_runtime.forecast`, the same arithmetic in the same order.
  - It replays all 522 strict development kills props: mean within 1e-10, chance within 1e-12 (tests/test_cm2.py).
  - mean = exp(intercept + standardised features x weights); sd = spread_c x sqrt(max(mean, 1)).
  - Whole and half lines have separate thresholds.
  - **Saved:** raw Over, Under and push; Over given no push (uncapped); and p_more, which is that conditional chance inside
    the old kills cap of 22-78%.
  - p_more is the value scored, like the live p_more on decided props. It is not an outright win probability.
  - No Over margin or legacy adjustment is applied.
- **Corrected inputs (tables), applied in every table including ratings:**
  - Only complete scoreboards: ten unique players, five per team, kills and scores present, one positive round count,
    nonnegative kills.
  - An unknown historical environment stays unknown (NaN). It counts in overall history, never in LAN-only or online-only
    history.
  - Source rows are never deleted; excluded maps are counted by reason in the tables' meta.
  - The database read keeps NULL as None (cm-1's read turned it into False).
  - cm-1's table build is unchanged: an optional `env` argument that cm-1 never passes. Its tables hash on the test fixture
    is pinned.
  - **Three different counts - never merged in a claim:**
    - 522 = inference tests (the reference arithmetic on the research's saved features);
    - 521 = production-eligible history reconstructions;
    - 1 excluded, the player with no maps in the window.
    The research's 1,016-prop strict table has two such rows (that player's kills and HS props). The eligibility-aligned
    recomputation (no refit) is in research/accuracy_2026_10_18/ (rule, script, results).
  - **Replay validation (2026-10-18 finish fixes):**
    - Saved features and outputs must be finite real numbers; NaN, infinity, null, bool and text are rejected, as are
      vectors that are not 14 long.
    - Version, artifact (16-character and canonical) and constants identities must match the registered artifact.
      `artifact_canonical_sha` is the canonical-JSON hash. Older records carry the same value under the old name
      `artifact_file_sha`, which never meant raw file bytes.
    - Replay reports computed-and-replayed, ineligible/skipped, missing/error and mismatched separately. An ineligible
      record is never counted as a reconstructed forecast.
    - Forecasts, weights and the registration (`cm-002-9f1db397`, 21:55:07Z) are unchanged by this.
  - **Pipeline check (research source dump, 300,457 rows):**
    - 37 incomplete maps and 20 unknown-environment rows, as in the report.
    - 521 of the 522 strict props have identical features; mean within 2e-14.
    - The 522nd is a player with no maps in the 420-day window. The research scored it on priors only; cm-2 marks it
      ineligible, by the handoff's rule.
- **Ineligible, with the reason saved, and never borrowing a legacy or cm-1 value:**
  - another stat or scope;
  - tables not built yet;
  - unknown target environment;
  - player, team or opponent not identified in HLTV;
  - a line that is not a whole or half number;
  - no HLTV player rows, or no player maps in the window.
- **Record:** saved inside the same pre-match fh-1 record, in the same write. It contains:
  - sums, the 14 features with their names, all outputs;
  - artifact, file and constants hashes;
  - the tables' build time, hash and exclusions (history version and source);
  - the quote identity: book, source and market ids, canonical match, line, market type, sides offered, provider and
    observed times;
  - the computed time.
  - Building a table records when the database was read. A match's date does not prove when its rows arrived.
- **Replay:** `cm2.replay` recomputes features and every output from the record's own saved values. It never reads tables
  or the board. perf_shadow.check_record includes it.
- **Live output unchanged:** tests/test_cm2.py builds the pre-match record with cm-2 absent, failing and computed.
  - The board result, live arm, fh-1 arm, cm-1 values and published grade are identical in all three cases.
  - The live model tag is unchanged (cm2.py and perf_shadow.py are not in the model hash).
- **Tables:** built in the learning child process every 2 h, after cm-1's. Saved as plain .npy files that the server
  memory-maps read-only, so there is no second resident copy of the history in the server.
- **Registry:**
  - Registered once by the learning cycle; frozen_at = registration time (UTC) = the forward clock.
  - The config records the promotion rule BEFORE any forward result exists:
    - the existing REVIEW_RULE windows (300 / 600 / 1000 distinct forward matches of cm-2's own record), checked with
      review_checks;
    - a pass = eligible_for_review only; live grading changes only by an owner decision;
    - retired at a failed window with gain <= 0, or at the last window.
- **Forward unit:** a Maps 1-2 kills P1 unit logged after registration whose latest pre-match record before the 30-minute
  cut-off was also saved after registration. Older records are rejected with that reason.
- **Scorecard:** the P1 challengers row reports:
  - paired log-loss and Brier with match-resampled intervals;
  - MAE, RMSE and bias;
  - the fixed 57% rule for each arm, as different pick sets;
  - coverage and rejections;
  - match-equal sensitivity;
  - calibration bands for both arms;
  - exact live model tag x cm-2 tables.
- **Evidence status:** the development comparison is retrospective and was selected on months earlier research had seen.
  - Strict book lines Oct 11-16: kills log-loss 0.7016 -> 0.6852.
  - It is not forward evidence and not a production accuracy rate. No improvement is claimed before the registered review.

## lc-1 - real-line probability correction (family `line-correction-1`, id `lc-001-<hash>`; production shadow only)

- **Definition:** `logit P(Over) = a_s + b_s * z`, with z = (projection - line) / (c_s * sqrt(projection)) (c = the live
  spread constants) and s = kills / HS. It keeps the published projection and corrects only the chance.
- **Frozen fit:** every settled real offered line through 2026-10-16.
  - P1 units: 1,309 / 78 matches; data hash 654e9c6827b6.
  - Ridge 100 (chosen leave-last-day-out).
  - Kills (-0.02688, -0.00717); HS (-0.04084, +0.00771).
  - Code: `perf_shadow.LC1`, `learning.LINE_CORRECTION`.
- **Saved before each match.** The value goes in the fh-1 pre-match record (`rec["lc1"]`). It is scored after settlement
  on the same P1 units as live (the record of each forecast's pick logged 30+ min before the start). Records written
  before lc-1 existed are rejected and counted.
- **Recommendation rule (fixed before testing):** the favoured side when its chance is 57% or more. Its picks, matches,
  hit rate (match-grouped interval) and sides are reported for live and lc-1.
- **Development (ACCURACY_2026-10-16.md):**
  - Log loss +0.0132 vs live (-0.0022 to +0.0292), but -0.0004 vs the simple line baseline. AUC 0.473.
  - It collapses to the base rate (about 49% Over) and recommends nothing.
  - **Not an accuracy upgrade.** It is the forward test of whether the projection's disagreement with the line has
    value.
- **Forward:** the registered review windows (300 / 600 / 1,000 distinct forward matches); promotion only through it.
- **Status (owner 2026-10-16 evening):** no further development. It stays registered only so its prospective test is
  kept; no new named challenger that repeats "about 50%" without new information is added. Further modelling targets the
  error the fh-1 and pl-2 experiments expose (ACCURACY_2026-10-16.md section 0.4).

## fh-1 - full HLTV history for the performance model (family `full-history-1`, id `fh-001-<hash>`; production shadow only)

- **Definition:** the live perf-2 formula and parameters unchanged, reading every HLTV match (all tiers) dated before
  the match day within 420 days, instead of the pinned top-200 history (N0).
- **Shadow only (owner 2026-10-15).** Live history is not switched. This is not presented as an improvement on book-line
  picks before settled results show one.
- **Implementation (`perf_shadow.py`, board loop only).**
  - Live and fh-1 run through the same production projection code: perf.project, perf_live.projection's
    post-processing, the BO3.gg fallback rule, grader.grade_leg and the verdict.
  - They differ only in history eligibility:
    - live arm: the published forecast's exact rows (the live perf_live cache, N0 rule n0-r4), in their order;
    - fh-1: those rows plus the rows N0 excludes, from the same SQL with the same ids, window and filters.
  - fh-1 reads the live rows first, then the excluded rows in a fixed order (the query has no ORDER BY). Production with
    full eligibility could sum in another order; that changes only the last floating-point digits.
- **Saved before each match.** Each record goes in picks.db `shadow_forecasts`, beside the live forecasts table. The live
  forecast rows, their dedupe and every existing challenger's inputs are unchanged.
  - When: whenever either arm's chance, projection, stake, grade or side, the line, or the history snapshot changes. Only
    before the start. A moved price alone writes no record: the performance model's chances ignore odds.
  - Contents: both arms' projection, chance, side, grade and stake; the history revisions; the read times and the
    logging time.
  - Inputs (`shadow_inputs`, content-addressed): the history snapshot and the grader inputs.
- **Checks on every record:**
  - `n0_check`: the live arm rebuilt from the snapshot reproduces the published projection and chance exactly, and the
    shadow's own N0 view equals the live rows.
  - Identical history: when full history adds no row, fh-1 equals live exactly (asserted).
  - Exact replay: every record is re-run from its saved inputs (learning cycle; `/admin/fh1.json?replay=1`).
- **Evaluation (registered before any fh-1 forecast settled).**
  - Which record: per forecast, the latest fh-1 record of its pick logged 30+ min before the start. Both arms come from
    that one record (one grade, one moment).
  - Where: P1 archived book lines after settlement. Paired log-loss and Brier; projection RMSE, MAE and bias; unique
    matches.
  - Groups:
    - established = live arm on HLTV history with 600+ time-weighted N0 rounds, and the opponent has N0 maps;
    - thin history / lower tier = everything else.
  - Also reported: history depth, and whether full history added rows.
  - Retrospective reconstruction from earlier-dated database rows may supplement this. It is never labelled a forward
    forecast.
- **Development evidence (ACCURACY_2026-10-15.md):** 12 months, 8,550 scored matches.
  - Kills MAE +0.131 (+0.118 to +0.143) and better in 10/10 months.
  - Log score +0.031; HS MAE +0.175; bias -0.70 -> +0.20.
  - Not demonstrated on the original top-200 set (+0.004).
  - On archived book lines (41 matches): +0.0015 (-0.0054 to +0.0101).
  - This is supporting evidence, not a substitute for results on the props members can play. It reconstructs forecasts
    and only approximates the live pin (74% of pinned forecasts within 0.5%).
- **Forward:** saved pre-match records only, on matches after the freeze (the first learning cycle after the deploy).
  The registered review windows are unchanged (300 / 600 / 1,000 distinct forward matches).

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
