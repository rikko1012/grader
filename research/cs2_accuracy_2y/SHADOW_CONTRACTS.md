# rm-1 / th-1 / lm-0 shadow contracts - closeout verification (2026-10-23)

Verification only. No frozen constant, definition or rule was changed. Live grading untouched. Deployed code checked:
main 7d22556 (cands_shadow.py, learning.py, CHALLENGERS.md); rm-1 / th-1 frozen 2026-10-23 07:45 UTC, constants sha256
3d00b8c801fb160f96368e8e5b743f4d699fbac2fd920a610febef0db0744b33, deployed 07:55:42 UTC; lm-0 frozen 06:08 UTC.
One report-only addition (branch close/shadows, see item 5): `learning.cands_report` now also prints each reached look on its
frozen REVIEW_RULE cohort. No shadow value, eligibility rule or decision rule changed.

## Data used
- Production forecasts: `/admin/forecasts_page.json?inputs=1`, keyset pages of 500, cutoff 83212 (2026-10-23 15:40:55 UTC);
  every forecast logged at/after 07:55:42 UTC = ids 81456..83212, **1,757 forecasts**, all git_commit 7d22556.
- Development P1 forecasts with saved inputs (research pull `under/p1_fc.json`, 2,922 rows; 297 rm-1-eligible, 10 th-1-eligible).
- Research implementation: `research/cs2_accuracy_2y/candidates/lib.py` with `results/params_FINAL.json`
  (sha256 66a39c0c20ef2e35a5559517569ab4c3de988ec6e1a8f9ef6ffa2f10eca70c32).

## Contract table

| # | Contract | Result | Evidence |
|---|---|---|---|
| 0 | Frozen constants = the research fit | verified | cands_shadow PI_Q / PI_REG / PI_OT / RHO / TH equal params_FINAL exactly; CONSTANTS_SHA = 3d00b8c8...; pi_map() = FINAL pmf (max diff 1.4e-17, 29 support points) |
| 1 | th-1 scored only in its scope (kills Map 1, perf-2 / perf-2-bo3, history_rounds_weighted < 150) | verified | `th1_entry`: other cells / sources -> `eligible: false` + reason, no probability; >= 150 rounds -> `eligible: false`. `cands_report` counts `eligible is False` as "not eligible" and never adds it to the paired set. Production: 1,757 / 1,757 th-1 entries `eligible: false` (924 "kills 12", 833 "hs 12"), 0 ineligible entries carrying a probability, 0 out-of-scope eligible |
| 2 | rm-1 = its frozen definition; production code = research code | verified (parity on development inputs; no production rm-1-eligible forecast exists yet) | Production since deploy: 0 rm-1-eligible forecasts (all 1,757 are Maps 1-2: 1,683 perf-cm2, 66 perf-2-bo3, 8 old pipeline / no perf). Deployed module vs research lib.py on the 2,922 development saved inputs: rm-1 297 eligible, max abs difference 5.0e-7; th-1 10 eligible, max 4.9e-7 (= the 6-decimal storage rounding). Cells are exactly kills 1 / kills 123 / hs 1 (`RM1["cells"]`) |
| 3 | Replayable inputs saved before the start; replay reproduces the logged shadow | verified | 1,757 / 1,757: inputs present, cands entry present, logged_at < match_start. Re-running grader.grade_leg on the saved inputs reproduced the logged live p_more on 1,757 / 1,757; `cands_shadow.entries(saved inputs, regrade)` is byte-identical (sorted JSON) to the logged rm1 and th1 entries on 1,757 / 1,757. Stored `live.p_more` equals the row's p_more on all rows |
| 4 | Meaning of the "gain" | verified, stated below | learning.cands_report / paired_stats |
| 5a | Checkpoints count eligible distinct matches | rm-1 verified; th-1 AMBIGUOUS (doc vs code) | see item 5 |
| 5b | Repeated-look decision rule (alpha spending) | NOT the 98.33% REVIEW_RULE level; registered rule is a plain 95% at each look | see item 5 |
| 5c | Nothing promoted outside the rule | verified | no code path promotes rm-1 / th-1 / lm-0 (no status field, no grader input; promotion = owner action + new MODEL_VERSIONS entry). Report is labelled interim |
| 6 | lm-0 eligibility (legacy-source grades only) and replay | verified | production since 07:55: 8 lm-0-eligible forecasts (live source legacy), 1,749 `eligible: false` (1,683 perf-cm2, 66 perf-2-bo3) with no probability; `picklog.legacy_margin_shadow(saved inputs, regrade)` byte-identical to the logged lm0 entry on 1,757 / 1,757. `legacy_margin_report` counts ineligible / not logged / rejected separately; 95% CI, min 30 matches + 60 units |

## Item 4 - what the positive gain means

- Scored probability: P(Over | no push) at the offered (P1 board) line. Candidate: `p_more` = over / (over + under), then the
  live 22-78% cap for kills (HS uncapped, as live). Live: the logged live `p_more` of the same P1 forecast (stored at 0.001).
- `paired.gain_vs_logged` (the registered primary, CHALLENGERS / REGISTER "vs the logged live chance") =
  mean over UNITS of [LL(logged live) - LL(candidate)], LL = -ln p(outcome), p clipped to [1e-4, 1 - 1e-4], nats per unit.
  **Positive = the candidate has the lower log loss (better).** Interval `gain_vs_logged_ci`: match-grouped percentile bootstrap
  of the ratio sum / count (matches resampled, B = 300, seed 7, 95%) - i.e. unit-weighted, not an average of per-match means.
- `paired.gain` (secondary) = the same with live = `live_replay` (the live chance rebuilt by cands_shadow, unrounded; differs
  from the logged chance by <= 5e-4 on the development inputs, the 0.001 rounding). The rule is read on `gain_vs_logged`.
- Pushes and voids: `learning.product_units` drops a unit whose actual equals the board line ("push on the board line",
  counted) and buckets void / settlement_conflict / incomplete / unresolved_identity ("not scoreable", counted). The chances
  are already conditional on no push, so pushes never enter either arm. Units whose stored live chance differs from the P1
  chance, logged before the freeze, or for a match before 2026-10-22 are rejected and counted.

## Item 5 - checkpoints and the decision rule

**Counting (code).** `cands_report`: `eligible_matches` = distinct matches among the candidate's OWN eligible, paired forward
P1 units, pooled over its cells; `look_reached` = largest of 300 / 600 / 1,000 <= that count. For rm-1 this matches
REGISTER / CHALLENGERS ("distinct forward matches with >= 1 eligible logged unit").

**th-1 ambiguity.** For th-1 the eligible units ARE the cohort (< 150 rounds), so the code's look counter equals the cohort
count and "th-1 additionally needs >= 60 distinct cohort matches" can never bind (`cohort_ok` is always true at a look). The
text, however, says "the 60-cohort-match minimum may not be reached before the 1,000-match look" - which only makes sense if
the look counter counts a wider set (e.g. forward matches with a logged th-1 entry in its cell, kills Map 1 on perf-2, at any
depth). The two readings differ greatly: under the code, th-1's first look needs 300 thin-history matches (unlikely in any
useful time); under the text, its first look is at 300 kills-Map-1 matches with >= 60 of them thin. Not changed (frozen).
Proposed wording for the owner: "th-1 look counter = distinct forward matches with >= 1 P1 unit in the th-1 cell (kills
Map 1 on a perf-2 / perf-2-bo3 grade) carrying a logged th-1 entry, eligible or not; the look is skipped unless >= 60 of
those matches have a th-1-eligible (< 150 rounds) unit; the statistics use eligible units only."

**Level / alpha spending.** REVIEW_RULE (learning.py) sets `ci_level` = 0.9833 (95% family-wise over 3 looks, Bonferroni)
and other conditions (min_gain 0.002, slope band). The rm-1 / th-1 registration (REGISTER.md "Forward confirmation", repeated
in CHALLENGERS.md) borrows only the look WINDOWS and requires a **95%** match-grouped interval > 0 at the look; the code
computes 95% (`_ci_ratio` default) - so code = registration, but the registration has no alpha spending: with up to 3 looks
the chance that a no-gain candidate shows a favourable look is above 5%. CHALLENGERS.md's header ("decided only at the
fixed review windows (learning.REVIEW_RULE ...)") can be read as implying the 98.33% level; it does not apply to rm-1 / th-1
as registered. lm-0 likewise uses 95% with a minimum (30 matches / 60 units) and no look schedule. Not changed (frozen).
Proposed wording if the owner wants alpha spending (an amendment made before any forward rm-1 / th-1 unit exists - 0 so far):
"At each look the paired log-loss gain interval is read at REVIEW_RULE ci_level (98.33%, Bonferroni over the 3 looks); a 95%
interval is reported beside it for reading only."

**Cohort at a look.** REVIEW_RULE's cohort is "the first W distinct forward matches (by start), frozen at decision time".
Before this closeout `cands_report` only computed running statistics on all eligible units, so after a window was crossed no
figure matched the look's cohort. Added (report only): `looks` = one entry per reached window with the first W distinct
matches by start, units, 95% `log_loss_gain_vs_logged` + CI, Brier gain and favoured side per arm; no pass / fail computed.
Test: tests/test_cands_shadow.py::test_cands_look_uses_the_frozen_first_w_matches (fails before, passes after).

## Open (not a contract violation)
- Forward volume: since the deploy the board offered only Maps 1-2 lines (1,757 / 1,757), so rm-1 and th-1 have 0 eligible
  forward forecasts; the development set had kills / HS Map 1 and Maps 1-3 lines (317 of 2,922). The clock is running but
  will not advance until Map 1 / Maps 1-3 lines are offered again.
- Coverage identical (a promotion condition) is not computed by the report; it holds by construction (an eligible entry exists
  exactly when the live grade is on perf-2 / perf-2-bo3 in a candidate cell) and should be checked by hand at a look.
