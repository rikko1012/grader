# Full real-line archive study - protocol (written 2026-10-16 ~21:00 UTC BEFORE any model below was scored)

## Evidence status
Every settled day in these sets (2026-10-02 .. 10-17) has been inspected in earlier reports or diagnoses (lb-001 Oct 3-12,
P1 Oct 11-16): all of it is DEVELOPMENT evidence for anything fitted or chosen here. A frozen candidate's own saved
pre-match values after its freeze remain ITS forward evidence (lb-001 > 10-12 15:49, rr-1 > 10-13 17:51, pl-2 > 10-15
03:29, fh-1 > 10-15 18:39). Independent confirmation of anything found here = matches after this study (2026-10-17 onward).

## Sets
- E1 offered-line archive (prop_grades): the last standard line each platform offered before the start, Maps 1-2 / Map 1,
  graded by HLTV; one observation per player x HLTV match x stat x scope x DISTINCT line (a same-line copy on the other
  platform counts once). Models are reconstructions from strictly earlier HLTV days (not saved forecasts).
- E2 our saved forecasts: the latest board forecast >= 30 min before the start per player x match x stat x scope x
  platform, at its own line; same-line copies across platforms once; outcome = HLTV total.
- Uncertainty: whole HLTV matches resampled (2,000), 95%.

## Models (identical rows within a set)
FLAT 50% · BASE = earlier-day Over rate per stat x scope shrunk with 50 pseudo-units · LIVE (E2: as published; reported
by pipeline: perf-2 tags vs older) · PROJ (perf-2 rebuilt, live-pin history view n0) · FULL (same, full history = fh-1
analogue) · frozen candidates: lb-001 transform (kills w 0, HS 0.5) of the model's chance; pl-2 anchoring (location =
anchor + w (projection - anchor), anchor = mean of both platforms' lines when they differ, else the line; w kills 0,
HS 0.25); rr-1 saved values (E2, from 10-13) · rolling refits of the SAME families (fit on earlier days only, scored on
the next day; grids fixed: CORR logit a+bz ridge {0.3,1,3,10,30,100} chosen leave-last-day-out; lb w 0..1.2 step 0.05;
pl anchor w 0..1.2 step 0.05). Scored days: every day with >= 300 earlier observations. No other model is added.

## Questions and metrics
1. Probability accuracy vs LIVE and vs BASE: paired log loss and Brier; calibration slope and mean gap; AUC.
2. Selection (fixed): favoured side when its chance >= 57%; picks, matches, hit, hit minus BASE's chance of the same side.
3. Why the projection's disagreement with the line fails (E1, totals): slope b of (actual - line) on (projection - line);
   D = var(projection - line); MSE(projection) - MSE(line); the implied split of D into projection-only and line-only
   error ((1-b) D and b D); by stat x scope, platform, history depth (n0 rounds < / >= 600), original top-200 vs lower
   tier, LAN / online. Also whether the line's departure from our projection follows pre-match observables (Elo gap,
   history depth) and whether that departure is right (slope of actual - projection on line - projection).
4. Market information beyond one line, only because pl-2 already uses it: where the two platforms' lines differ, slope of
   (actual - this line) on (other line - this line).
5. Power: the smallest log-loss gain and hit-rate edge detectable with the matches available; a prospective sequential
   rule proposal for several candidates (not adopted here).
