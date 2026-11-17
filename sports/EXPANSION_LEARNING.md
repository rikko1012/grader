# Prospective learning and review — 2026-11-17

This worker is a research baseline, not a released soccer or tennis grading
model. It never writes member grades, probabilities, stakes or model artifacts.
It does not establish an improvement in prediction accuracy.

## Frozen before the result

Each fresh, unstarted, exactly linked offered revision gets one immutable
research prediction when at least five comparable historical games exist.
The snapshot saves the complete offer, exact player/event/stat/period identity,
source record IDs, availability times, history values, available pregame context,
model version and the prior review IDs used for the candidate adjustment.
Unavailable factors are absent. Saved context is explicitly marked as not used
by this initial model. It is not described as having analysed all factors.

Baseline: mean of the newest 20 available exact-stat/period games in the same
competition/tour. The challenger adds the residual correction learned from up
to 200 previously reviewed player-events in that cohort, shrunk by 20 observations
and capped at 20% of the baseline. These are fixed experimental settings, not
optimized or validated settings. It changes the point projection, not confidence.

No current-match result or later-known input can enter that match's prediction.
Books and alternate lines do not multiply training observations: the earliest
saved prediction per player-event contributes once. ATP, WTA, Challenger,
WTA125 and each soccer competition remain separate.

Soccer full-game book rules have not been verified for every offered market.
Those research predictions target regulation only and deliberately have no
line-side comparison. Tennis numeric line comparisons are diagnostics only;
retirement, walkover, void and platform rules still require a settlement adapter.
Neither path records a bet win, loss, profit or public grade.

## After results

Every saved prediction is reviewed against the latest non-late, final,
participation-verified result with the exact event/player/stat/period. Errors
are computed by `gradecore.projection_review`. A correction appends a review,
preserves the old review and removes the superseded value from future fitting.
Conflicting sources and nonparticipation invalidate error-based fitting instead
of being treated as zero. Unchanged runs add nothing.

The isolated scheduled jobs run every three minutes, with a 20-second work
budget, 45-second kill limit and explicit memory cap. They perform no network
requests and no member-request work. Reads are bounded and the learning database
has a 100 MB hard stop; no forecast or review is deleted to make room. Owner ops
reports expose saved/reviewed counts and coverage gaps. Zero predictions means
zero predictions, not a passed validation gate.

No candidate is automatically published. A production projection model still
needs its registered feature set, chronological fitting/evaluation, prospective
comparison and shared gradecore release requirements. The stored forecasts make
that comparison possible without reconstructing predictions after results.

## Existing sports checked in production

- CS2: learning active, not paused; 547 scored matches and 9 challengers in the
  owner report retrieved Nov 17. The active forward comparison blocks a new fit
  under its existing rules. No frozen experiment was changed.
- LoL: review report updated Nov 17 20:29 UTC, 43 graded series and 5 candidate
  records. Existing weekly refit and release rules remain in place.
- NBA/NFL: settle-post finished Nov 17 20:14 UTC. Daily calibration ran at
  10:06 UTC. Full fitting remains disabled (`SPORTS_TRAIN=0`); enabling all old
  training jobs is not equivalent to safely enabling learning.

NBA/NFL now gain an additive `projection_reviews` table beside their existing
probability scorecard: mean error, absolute error and squared error for every
scorable forecast/result revision. Reports include MAE, RMSE and match-clustered
bias. Existing settled-event marks are versioned so the recent review window is
filled incrementally under the existing deadline. No canonical forecast,
settlement, probability, letter or stake changes.

## Verified local evidence

Live public feeds at 20:41 UTC linked 1,319 of 1,567 soccer offers and 261 of 304
tennis offers. 287 soccer pre-match research predictions were saved locally.
No tennis predictions were saved: those exact markets lacked five historical
observations at this stage of backfill. No new forecast had a result yet.
Fixture tests prove append-only correction, replay, deduplication, time cutoffs,
void handling, exact identity, disabled-job isolation and bounded owner reports.

All 23 failures in the broad local esports browser suite also reproduced on
untouched main a1b0a52 in a separate worktree. These are pre-existing failures,
not a clean UI certification. They are not removed or weakened by this work.
