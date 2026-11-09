# line-gap-1109 NFL - frozen definitions (committed BEFORE any row was scored)

Owner request 2026-11-09: projections / grades should account for all factors "including line discrepancies etc".
This study tests line discrepancy and bump as a SIDE / LETTER factor layered on the UNCHANGED projection. The book line
is never an input to the projection. Everything below is MARKET INFORMATION (what other apps post / how a line moved),
not a football factor. Research only: no live change, no flag, no deploy.

## Data (read-only, paged)

**D1 - primary ("feed").** `GET /api/sports/admin/model/gradecore-comparison?league=nfl` (100 rows / page, cursor
paging, ~1.2 s / request), every page back to captured_at < 2026-11-03 (capture started 2026-11-04 00:30Z).
- Keep `period == full_game`, stats rec, rec_yds, rush_att, rush_yds, pass_yds.
- **Standard lines only:** both sides' `grade.break_even` non-null (PrizePicks standard 0.5774, Underdog standard
  0.5345). A null break-even is an alternate line ("Alt line: payout unknown") or a PrizePicks goblin / demon ("Break-even
  unknown for prizepicks"); those rows are not used (alt lines are not the app's main line, so a gap to them is
  not a discrepancy).
- **De-duplication:** rows are de-duplicated on `forecast_id` + `book` + `line` (re-captures of one forecast). The
  scoring unit is one row per (game, player, stat, book, line): the LAST forecast with `at <= start - 30 min`. Counts
  before / after each step are reported.
- **Outcome:** ESPN public box score (sports.espn.summary, read-only, cached). Pushes (y == line) and players without a
  box-score row (void) are dropped. Games not final are dropped (Monday 2026-11-09 night game is not final).

**D2 - secondary ("export", weeks 8-9 + Thursday 2026-11-05).** `gradecore_export_nfl.json` (read-only saved report,
built 2026-11-06 05:21Z): last saved sm-1 forecast >= 30 min before start per event/player/stat/period/line/book, with
its settled status. The export carries NO variant flag, so standard lines are INFERRED: a (game, player, stat, book)
is kept only when that book has exactly ONE distinct line in the export (goblin / demon / alt / moved keys dropped).
No opening line can be known in D2 (only the last forecast per line is saved), so D2 carries discrepancy features
only. Games also present in D1 are taken from D1 only. Pushes dropped.

## Baseline (the live side / letter at the same line)

- Side = raw sm-1 P(Over | no push) >= 0.5 -> Over, else Under. (pc-1 printed calibration has intercept 0, so the
  printed side is the raw side.)
- Letter = sports-gp-1 band of the pc-1 PRINTED chosen-side chance (sports.model.chance_cal.adjust with the committed
  artifact): A >= 0.60, B >= 0.5559, C >= 0.5345, else Lean. Graded = A / B / C.
- Hit = chosen side won (wins / (wins + losses)).

## Features (only information available at the row's forecast time t = `at`)

For row r (book b, line L, game g, player p, stat s, time t):
- **Other-app line L'** = the line of the latest STANDARD row of every other book b' != b for (g, p, s) with
  t - 6 h <= at' <= t. NFL has two graded apps (PrizePicks, Underdog), so L' is one line; if several, their mean.
  No other-app line inside that window = no discrepancy row.
- **F1 gap_units** = L' - L (stat units). **F2 gap_pct** = |L' - L| / ((L + L') / 2).
- **F3 soft_side** = the side THIS line favours relative to the other app: Over if L < L' (Over easier here), Under if
  L > L', none if equal.
- **F4 agree_disc** = Scout's side == soft_side.
- **F5 open_line** (D1 only) = line of b's EARLIEST standard row for (g, p, s) in the feed (first line Scout saw). Known
  only when that earliest row is >= 1 h after capture start (2026-11-04 01:30Z); else unknown (opening not observed).
  **bump_units** = L - open_line (0 = no bump); bump_dir up / down.
- **F6 bump_vs_scout**: `market_agrees` when the line moved the way Scout's side predicts (raised and Scout = Over, or
  lowered and Scout = Under: the line got HARDER for Scout's side); `market_disagrees` when it moved the other way (the
  line got EASIER for Scout's side); none when no bump.
- **F7 bump_hours** = hours before start of the first row at the current line after the last change.
- **F8 no-vig price gap at the same line: NOT COMPUTABLE.** The graded NFL apps are pick'em apps with fixed standard
  payouts in the store (PrizePicks 0.5774, Underdog 0.5345 break-even on every standard row); Underdog per-pick
  multipliers and sportsbook prices are not saved with the forecasts; no paid odds feed (owner rule). Reported as
  unavailable, no rule uses it.

## Rules (fixed in advance; no threshold is chosen on the scored rows)

Letter steps: Lean < C < B < A. "Up one" caps at A, "down one" floors at Lean. A rule that flips a side keeps the row's
baseline letter (the factor changes the side, not the confidence).

- **a1 follow-soft**: on every discrepancy row (gap != 0) side := soft_side.
- **a2 fade-soft**: side := opposite of soft_side.
- **a3 follow-soft-large**: a1 only where gap_pct >= 10%.
- **aW walk-forward**: for test game-day d, the direction is learned on game-days < d (D2 + D1): follow if the prior
  soft_side hit rate > 50%, fade if < 50%, nothing if fewer than 50 prior discrepancy rows.
- **b1 agree up / disagree down** (letter +-1 by F4, side unchanged).
- **b2 disagree down only.  b3 agree up only.**
- **bW walk-forward**: on game-days < d, if graded agree rows hit more than graded disagree rows use b1, else the
  reverse (agree down / disagree up); nothing if either group has < 50 prior graded rows.
- **c best line**: in each (g, p, s) with a discrepancy at t, keep a row only if its line is the softest for its OWN
  Scout side among the lines live at t (Over: L <= L'; Under: L >= L'). Scored: kept rows vs the dropped (harder) rows
  and vs every row of the same groups; count = distinct (g, p, s, side) graded.
- **d1 market-agrees up / market-disagrees down** (letter +-1 by F6). **d2** the reverse. **d3** flip side when
  market_disagrees. **d4** d1 only for bumps made within 24 h of the start (F7 <= 24).
- **dW walk-forward**: on game-days < d, if graded market_agrees rows hit more than market_disagrees rows use d1, else
  d2; nothing if either group < 30 prior graded rows (D1 only has bumps, so the first D1 game-day has no prior).

## Scoring and acceptance (owner rule, unchanged)

- Hit rate at the real offered lines, Over picks and Under picks separately, graded (C+) and all rows.
- 95% CIs: whole-GAME bootstrap (B = 2,000, seed 1109), the difference rule - baseline paired on the same games.
- Game-day = start - 10 h (UTC date). Walk-forward rules are scored only on game-days that had prior data.
- **PASS** needs all of: H1 graded hit rate up vs baseline AND its 95% CI of the difference entirely above 0 (the
  owner's "improves" on a chronological test); H2 Over and Under graded hit rates each not down; H3 graded count >=
  baseline; H4 graded Over share no further from 50% than baseline. A point-estimate-only improvement is reported as
  "not proven", never as a pass. Calibration gap is informational only. No chance is shrunk by any arm.
- Many arms are tested on one slate: a single arm clearing the CI by chance is expected about 1 in 20; any pass would
  still need the next unseen week before promotion.
