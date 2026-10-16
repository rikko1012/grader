# Actionable-line question - protocol (written 2026-10-16 20:56 UTC - file time - before this analysis ran)

Question: did the grader provide useful information at a line that was actually available when the pick was made, and did
its fixed recommendation rule (favoured side when the recorded chance >= 57%) benefit there?

Observation = one DECISION-TIME forecast per player x HLTV match x stat x scope x platform:
- the EARLIEST saved board forecast of that unit on that platform logged >= 30 min before the start (Render forecast log;
  Railway forecast log, else the Railway pick row at its last write time - the earliest record that exists);
- recorded values only: the live chance (p_more) and projection (lambda) as logged at that time, its version; never a
  reconstruction;
- the line = the platform line of that board row at that time (board rows come from the platform feed);
- offered side: from the logged quote (over_offered / under_offered) where it exists (forecasts from 2026-10-15 02:44);
  before that it is NOT recorded and stays missing ("line-verified" cohort vs "line + side verified" cohort);
- comparison-platform information = the logged other-platform anchor (quote.anchors.other, observed at or before the
  forecast); it exists from 2026-10-15 03:26 only;
- outcome = the HLTV total vs THAT original line (pushes out). Identity: the corrected join (opponent must agree; the
  HLTV record's opponent must be the pick's opponent); archive rows marked ambiguous or with another opponent are never
  used to identify a match. A same line on both platforms counts once (the earlier decision).
Closing benchmark (separate, never an input): the LAST line our own board recorded for the unit on that platform before
the start, with its capture time (forecast logged_at < start). The scraper archive's last line has no capture time and is
excluded. Railway matches starting after its export (2026-10-10 16:40 UTC) have no verifiable later line history.
Movement = last verified line - original line (size and sign); toward the projection = same sign as (recorded lambda -
original line); unchanged vs moved reported separately; the recommendation rule is scored on ALL decision-time forecasts.
Comparisons: recorded live chance vs FLAT 50% and vs BASE (earlier-day Over rate per stat x scope, 50 pseudo-units);
log loss, Brier, AUC, calibration slope; slope of (actual - line) on (recorded lambda - line); recommendation rule: picks,
matches, hit, hit - 50%, hit - BASE's estimate for the same side. Match-grouped bootstrap (2,000), 95%. By era (Railway
Oct 3-10 = mostly older pipelines; Render Oct 11-16 = perf-2 tags). No model is fitted; no candidate is added or changed.
