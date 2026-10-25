# Hit-rate investigation 2026-10-25 - registration (written before any LD-1 number was computed)

Owner, 2026-10-25: "our hit rate is steadily going down for esports - get this together, make projections accurate".
No grading change is made by this file.

## Status of the evidence used here (stated before the test)

- Data: the production P1 export (/admin/learning/p1_units.json, cycle 2026-10-25T18:31:28Z, 3,650 settled CS2 units,
  match starts 2026-10-11..2026-10-25) joined to the forecast rows (forecasts_page, no inputs).
- Already inspected before this registration: weekly hit rates, weekly signed error (actual - projection) by stat. In
  particular kills Maps 1-2 ran +0.06 (W1 Oct 11-17), +0.97 (W2 Oct 18-24), -0.21 (Oct 25) above the projection, and
  the book line by the same amount. Every number below is therefore DEVELOPMENT evidence on inspected data; a pass can
  only lead to a frozen forward shadow, never directly to a grade change.

## LD-1 - recency level-drift correction (CS2, Maps 1-2, kills and HS separately)

- lam' = lam + w * b, with b = mean(actual - lam) over settled P1 units of the same stat (kills / hs), scope Maps 1-2,
  whose match started in the 7 UTC days strictly before the forecast's UTC start day; n = their count; w = n / (n + 400).
  No other parameter. lam = the logged P1 projection.
- Test days: every UTC start day 2026-10-18..2026-10-25 (each has 7 prior days in the export). Units of earlier days are
  only used as the trailing window.
- Primary (per stat, 2 tests): paired absolute-error gain |actual - lam| - |actual - lam'| (+ = LD-1 better), mean over
  test units, match-clustered bootstrap (B = 2000, seed 7), two-sided 97.5% interval (Bonferroni over 2 stats). Pass
  for a stat only if the lower bound > 0.
- Secondary (reading only): squared-error gain; hit rate of the side chosen by lam' vs the line against the live
  favoured side on the same units; paired log loss with P(over) from a normal at the live implied spread (units with a
  live chance in (0.23, 0.77) and |p - 0.5| >= 0.04 only).
- Decision: a pass on the primary AND a side hit rate not worse makes LD-1 eligible for a frozen forward shadow
  (CHALLENGERS.md entry, own clock, REVIEW_RULE windows). A fail changes nothing. No variants (window / shrinkage)
  will be tried after the result.
