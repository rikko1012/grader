# Proposed amendment P2 - book-line qualification (PROPOSED, not registered)

**Status: draft for the owner's decision (2026-10-16).**
- Nothing here is in force. It takes effect only if the owner approves it in writing. It then applies only to matches that
  start after the registration timestamp recorded with that approval.
- It changes no past result.
- The registered analysis (`learning.REVIEW_RULE`: 300 / 600 / 1,000 distinct forward matches on the registered primary
  cohort, which includes member-entered lines) and the P1 board-line scorecard stay exactly as they are and keep being
  reported.
- P2 adds a separate qualification route for real offered lines. It does not replace the registered one.

## Why
- **Member lines mix in.** The registered primary cohort includes member-entered lines (241 of 1,275 scored units at
  2026-10-16 19:48 UTC). The product objective is the platforms' offered lines.
- **Calibration and selection are different claims, so they need separate qualifications.** A candidate can improve live's
  estimates without finding better picks (pl-2 on its forward rows), and the reverse.
- **Multiplicity and repeated looks.** The registered rule corrects for its three looks (98.33%) but not for several
  candidates under review at once. It has no early stop for futility.
- **The archive's power (REALLINE_ARCHIVE_2026-10-16.md section 4).** At about 17 observations per match:

  | Matches | Smallest detectable log-loss gain (80%) | Smallest detectable selection edge |
  |---|---|---|
  | 300 | 0.0056 | 6.7 points |
  | 600 | 0.0040 | 4.8 points |

  So fixed windows do not delay a decision that the data could make earlier. A sequential design lets large effects
  qualify early and null candidates stop early.

## Cohort (P2)
- **Lines:** platform board lines only, with a standard market type, platform and feed ids recorded.
- **Scope:** Maps 1-2, and Map 1 only when the HLTV match is identified with the opponent checked.
  - Member-entered and adjusted (goblin / demon / alt) lines never enter.
  - Maps 1-3 enters only once an HLTV Maps 1-3 construction is tested.
- **One observation per player x canonical match x stat x scope x platform:** the latest saved forecast at least 30 min
  before the start.
  - A same-line copy on the other platform counts once.
  - Different platform lines count separately.
  - All uncertainty is grouped by match.
- **Outcome:** the HLTV total. Pushes and voids are out; a settlement conflict is held, never scored.
- **Counted values:** a candidate's own saved pre-match values only, for matches after its freeze and after P2's
  registration.

## Two qualifications (each per candidate)
| | Book-line calibration (P2-CAL) | Book-line pick selection (P2-SEL) |
|---|---|---|
| Question | Does it improve live's probability estimates at offered lines? | Do its picks beat the simple line baseline? |
| Primary statistic | mean paired log-loss gain vs live (candidate better > 0) | mean of (hit - BASE's estimate for the same side) over the candidate's fixed recommendation rule (registered with the candidate) |
| Must also hold at qualification | Brier gain not significantly < 0; calibration slope interval overlaps 0.8-1.2 and is at most 1.0 wide; mean estimate minus observed within +-0.03 or its interval covers 0; no stat x scope slice with >= 50 matches worse by > 0.005 | at least 150 picks; P2-CAL Brier and log loss vs BASE not significantly < 0 (picks from a miscalibrated model are not accepted on hit rate alone) |
| Minimum meaningful effect (for futility) | 0.004 log loss per observation | +0.04 |

Beating BASE is not required for P2-CAL, and P2-CAL alone never counts as P2-SEL.

## Sequential rule
- **Looks:** every 50 distinct counted matches (by start), from 100 to a maximum of 600 (11 looks, about 30-40 days at the
  current 15-20 board matches per day).
- **Error control:** per candidate and qualification, one-sided alpha 0.025. It is spent with the Lan-DeMets
  O'Brien-Fleming function alpha(t) = 2 - 2 Phi(z_{1-alpha/2} / sqrt(t)), where t = matches / 600 (z = 2.2414 for alpha
  0.025).
  - With K candidates registered for P2 at the same time, each uses 0.025 / K (Bonferroni).
  - Equivalently, Holm at each look across the K candidates' p-values. Holm order is fixed by the p-values, never chosen.
- **Test statistic:** z = mean / SE, where SE comes from the match-level ratio estimator (sums and counts per match). The
  same match-resampling bootstrap is reported beside it.
- **Efficacy:** qualify when z exceeds the look's boundary AND every "must also hold" check passes at that look.
  Indicative boundaries (simulated, 400,000 paths):
  - one candidate: z of about 6.0 at 100 matches, 3.75 at 200, 3.03 at 300, 2.59 at 400, 2.30 at 500, 2.10 at 600;
  - four candidates (0.025 / 4 each): about 4.6 at 200, 3.7 at 300 and 2.6 at 600.
  - The exact values are computed from the spending function at registration.
- **Futility (non-binding):** at any look from 200 matches, stop the qualification when the upper 80% bound of the effect
  is below the minimum meaningful effect.
- **Bookkeeping:** every look is recorded once and never recomputed. A changed or refitted candidate is a new candidate,
  with its own count from zero.
- **On qualification:** "eligible for the owner's review" only. Promotion stays an explicit written owner decision with a
  new model version.

## Candidates P2 would cover if approved
Existing frozen candidates (definitions unchanged; their earlier forward records stay reported as they are): lb-001, pl-2,
fh-1, rr-1, lc-1.

**One new frozen test, proposed only because the error analysis supports it.** The full-history HS projection's
disagreement with the line carries information (slope 0.59, 0.25 to 0.93; reproducible by era):

- **hs-1:**
  - P(Over) = 1 / (1 + exp(-(a + b z))), with z = (lambda_full - line) / (1.264 sqrt(lambda_full)).
  - lambda_full = fh-1's saved full-history HS projection in the pre-match fh-1 record.
  - HS Maps 1-2 only; any other stat or scope is not covered.
  - a = -0.15249, b = 1.01202.
  - One fit on every development HS Maps 1-2 offered line, 2026-10-02 .. 10-16: 1,276 lines / 155 matches; ridge 0.3
    chosen leave-last-day-out; data hash f359ac40f36f.
  - Recommendation rule: the favoured side when its estimate is 57% or more.
- Its values are a fixed function of records that are already saved before each match, so it needs no deployment. Forward
  evidence = matches after its registration.
- **Development support is weak:**
  - rolling-origin log-loss gain vs BASE +0.0025 (-0.0026, +0.0083);
  - selection +0.05 (-0.02, +0.12);
  - at our forecast-time lines, no gain.
- Expect it to need most of the 600-match maximum. It is listed so the one supported hypothesis can be tested
  prospectively, not because it is expected to qualify.
