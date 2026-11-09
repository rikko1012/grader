# lg-1 NBA line gaps / bumps as a SIDE / GRADE factor - frozen definitions (2026-11-09)

Committed BEFORE any row was scored. Nothing below may be changed after scoring; any later idea is a new, separately
registered rule. Research only: nothing live imports this folder, nothing is deployed, no flag exists.

Owner terms: **bump** = a line moved from its opening value; **line discrepancy** = apps/books offer different lines or
prices for the same player prop at the same time. Both are MARKET information. The projection (sm-1) is unchanged; the
rules below only act on the side and the letter layered on top of it (owner: the book line is never a projection input).

## Data population (read-only)

- Saved sm-1 pre-game forecasts of the NBA service (raw chance before board calibration), PrizePicks + Underdog full-game
  points / rebounds / assists / threes. These are the only apps with time-stamped saved forecasts on a read-only route.
  - Window E (games with start < 2026-11-04T12:00Z): gradecore export (built 2026-11-06 05:21Z), population = last saved
    forecast >= 30 min before start per (game, player, stat, book, line), result `status` from the service settlement.
  - Window S (games with start >= 2026-11-04T12:00Z, final in the local ESPN box-score copy): the gradecore-comparison
    stream (every saved forecast, captured since 2026-11-04 00:24Z), reduced to the SAME population rule (last forecast
    >= 30 min before start per game, player, stat, book, line). Result from the ESPN box score: did not play = void.
  - A game is in exactly one window, so a row is never counted twice. Pushes and voids are dropped from hit rates.
  - De-duplication: one row per (game, player, stat, book, line); the stream is de-duplicated on its cursor first, then
    the last forecast per key is kept. Re-keyed ids are not possible here (keys are ESPN ids on both sources); duplicate
    keys after this step are counted and reported (expected 0).
- FanDuel / BetRivers / Polymarket / Kalshi line histories are NOT available with forecast-time timestamps on any
  read-only route (the offers route gives only the current revision with its last observation time; offers/moves gives
  only opening and closing). Using their closing line would look ahead. They enter ONLY through the `mc-1 consensus`
  quote that the service saved inside the forecast at forecast time (window S only).

## Baseline (live)

- side S = the side with the higher saved raw chance; p = that chance.
- letter: A p >= 0.60, B p >= 0.5559, C p >= 0.5345, else Lean (sports-gp-1). "graded" = C or better.

## Features (only information known at the row's forecast time t)

For a row (book B, line L, forecast time t, start T), O = the other app (PP <-> UD), rows of O for the same game,
player and stat in the same population:

- **L_O (other app's line at t):** the line of O's row with the smallest forecast_at >= t, provided O also has a row with
  forecast_at <= t (proves O's offer existed at t). Same-run rows have equal forecast_at. Lines are on a 0.5 grid and
  each line's row carries its LAST forecast, so the O line whose last forecast is the first at/after t was live at t.
  Missing -> no discrepancy feature (rules (a)-(c) leave the row at baseline).
- **gap** = L - L_O (stat units); **gap_pct** = |gap| / ((L + L_O) / 2).
- **D1** = |gap| >= 0.5 (any discrepancy). **D2** = |gap| >= max(1.0, 0.10 x L_O) (large).
- **soft side on this app (fav):** Over if L < L_O, Under if L > L_O (this app's line is the easier one for that side).
- **agree** = S == fav (Scout's side is the side this app's line is soft for); **disagree** = D1 and S != fav.
- **L_open:** the line of B's row with the earliest forecast_at for that game, player, stat (first line Scout graded
  on B). **bump** = L - L_open. **toward** = (S = Over and bump > 0) or (S = Under and bump < 0) (the market moved the
  way Scout leans); **away** = bump != 0 and not toward. **big bump** = |bump| >= max(1.0, 0.10 x L_open).
- **bump time** = hours before T of the last forecast of the previous line (when the latest move happened).
- **price (window S only):** q = the saved `mc-1 consensus` p_over at this line (no-vig sportsbook consensus).
  price side = Over if q >= 0.52, Under if q <= 0.48, else none. **price disagree** = price side exists and != S.

## Rules tested (each against the baseline at the same lines)

- **a1** flip S to fav when D2 and disagree. **a2** flip S to fav when D1 and disagree.
- **a3** flip S to the price side when price disagree (window S only).
- **b1** letter one step up when D1 and agree (Lean->C->B->A), one step down when disagree (A->B->C->Lean). Side kept.
- **b2** letter one step down when disagree only.
- **c1 best line:** among props where both apps' rows share the same forecast run (equal forecast_at) and the same Scout
  side, the softer line for that side (lower for Over, higher for Under) vs the harder one: hit rate of soft-line rows
  vs hard-line rows (both graded as live). Rule c1 = grade only the soft line (the hard line becomes Lean). Equal
  lines are not part of c1.
- **d1** flip S when big bump and away. **d2** letter one step up when toward, one step down when away (side kept).
- **d3** letter one step down when away and the move happened within 3 h of start.

## Walk-forward (expanding window, no look-ahead)

- day = calendar date of (start - 6 h) UTC (US slate date). Days sorted; the first 5 days are burn-in (never scored).
- On test day d a rule is ACTIVE only if, on all earlier days pooled, the rows it changes number >= 30 and their hit
  rate after the change is above their hit rate before (point estimate; for letter rules: graded hit rate after vs
  before over the earlier days). Otherwise the baseline is used that day. Nothing else is fitted; all thresholds
  above are fixed here.
- Also reported: the same fixed rule applied on every day (no gate) - it has no fitted parameter, so it is an
  out-of-sample test too.

## Scoring and pass rule (owner rule, applied to the test days)

Hit rate = wins / (wins + losses) at the real offered line, pushes and voids out. Game-grouped bootstrap 95% CI,
B = 1,000, seed 7, on the rule-minus-baseline difference.

- **H1** all-props and graded hit-rate differences both > 0, and the graded difference CI lower bound > 0.
- **H2** neither Over picks nor Under picks lose hit rate (point estimate), all props and graded.
- **H3** graded count >= baseline graded count.
- **H4** graded Over share no further from 50% than the baseline's.
- PASS = H1 and H2 and H3 and H4. Calibration / stated-vs-hit gap is information only; chances are never shrunk.

## Amendment 1 (2026-11-09, made while building the dataset, BEFORE any rule was scored)

Found while checking the 1,431 keys present in both sources (games Nov 4-5): the export's `forecast_at` is the creation
time of the last DISTINCT saved forecast (the store keeps one forecast while its numbers do not change), while the
stream logs every re-evaluation. Same key: same side 99.93%, mean |dp| 0.0002, results agree 100%, stream time later
by a median 8.5 h. So the two windows carry different time semantics, and the L_O rule above would look ahead in
window E. Replaced (no outcome was looked at):

- **L_O, window E:** O's row with the largest forecast_at <= t (the O line most recently created at or before t; a line
  change creates a new forecast, so that line was live at t). **L_O, window S:** as above (smallest forecast_at >= t,
  with an O row <= t); the stream shows every run, so that line was live at t.
- **L_open, window S:** the line of B's FIRST stream forecast for that game, player, stat (any time before t). Window E:
  unchanged (earliest-created row).
- **bump time / d3:** window S only (first stream time the current line appeared on B, hours before start). Window E
  has no reliable move time, so d3 leaves window-E rows at baseline.
- c1 "same run" (equal forecast_at) is exact under both semantics and is unchanged.
