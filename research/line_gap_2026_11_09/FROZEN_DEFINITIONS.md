# CS2 line-gap study (line discrepancies + bumps) - frozen definitions

Frozen 2026-11-09, committed BEFORE any outcome in this study's dataset was scored.
Research only: nothing here is a live input. No production writes. Nothing in this branch is merged or deployed.

Owner terms: a **bump** is a line moved from its opening value; a **line discrepancy** is two apps offering different
lines (or prices) for the same player prop at the same time. Both are MARKET information. The Scout projection is left
unchanged; every rule below is a SIDE / LETTER layer on top of the live forecast at the same line.

## 1. Data (read-only)

- `p1_units.json.gz` (local export of `/admin/learning/p1_units.json`, snapshot 2026-11-09 13:08 UTC): P1 board units,
  settled outcome, live chance `p`, projection `lam`, stake `units`, book, line, forecast id. P1 already drops pushes,
  voids and the re-keyed duplicates (picks stored under an older match key).
- `/admin/forecasts_page.json?compact=1` (paged, 2,000 rows a page): forecast id -> pick id, logged time, line, chance,
  grade, stake.
- `/admin/picks.json` (paged, factors dropped): pick id -> player id, opponent, prop, scope, book, line type, start.
- `/admin/ud_moves` (paged): Underdog line observations with payouts (cross-check only).
- Thunderpick logs match-winner odds only (no player props) and is not a line source here. PrizePicks shows no side
  prices; Underdog payouts are recorded as equal 1.0 / 1.0 on (almost) every observation, so a no-vig price gap at the
  same line has no variation to test. It is reported as coverage only.

## 2. Rows

- **Unit** = player id x opponent (normalised) x prop x map scope x start (same unit if starts are within 3 h).
  The opponent check is required (one player can play two BO1s in a day).
- **App row** = (unit, book) for book in {prizepicks, underdog}: that book's latest BOARD forecast (pick with a book,
  standard line type) logged >= 30 min before the start (the P1 rule). The P1 book's app row must reproduce the P1
  forecast id; mismatches are counted and dropped.
- **Outcome** = the unit's settled total (from P1) vs the app row's own line. A push at that line is excluded (counted).
- **De-duplication**: one app row per (unit, book). Re-keyed picks of the same unit collapse into one unit because the
  unit key uses player id / opponent / start, not the match slug. Units whose P1 row is missing are not scored.
- Rows enter only if the unit is in P1 (settled, scoreable).

## 3. Features (only information logged at or before the app row's forecast time t)

- **L_A** = app row line. **L_B(t)** = the other book's line in effect at t = the line of the other book's latest board
  forecast for the unit logged <= t. If that forecast is older than 180 min before t, L_B(t) is unknown (stale).
- **F1 gap** = L_B(t) - L_A (stat units); **F1p** = 100 * F1 / L_A (% of line). Discrepancy = |F1| >= 0.5.
- **F2 outlier-favoured side at L_A**: F1 > 0 -> Over (the other app's market sits above this line); F1 < 0 -> Under.
- **F3 agreement**: Scout's live side at L_A (p > 0.5 Over, p < 0.5 Under, 0.5 no side) equals F2.
- **F4 opening** O_A = the line of the book's earliest board forecast for the unit (the first line Scout observed on
  that app). **Bump** = L_A - O_A (stat units); bumped = |bump| >= 0.5.
- **F5 bump direction vs Scout**: line raised and Scout Over, or lowered and Scout Under = "bump toward Scout's side"
  (the market moved the way Scout leans); the reverse = "away".
- **F6 bump timing**: hours from the first forecast at the current line to the start; late = < 3 h.
- **F7 price gap**: no-vig difference of the two apps' prices at the same line. Not testable (no price variation).

## 4. Rules (thresholds fixed here; the only thing learned is the ACTION, on earlier weeks only)

Each rule produces a side (and stake) for every app row; rows outside the rule's trigger keep the live side.
Actions per rule: KEEP (= live), FOLLOW (side = the signal's side), FADE (side = opposite of the signal's side).

- **(a) discrepancy side**: trigger |F1| >= 1.0 (a1) / |F1p| >= 5% (a2). Signal side = F2.
- **(b) letter by agreement** (side unchanged): agree -> stake + 0.5U; disagree -> stake - 0.5U (floor 0). Scored on
  the staked set (stake > 0): hit rate and count before / after. Variant b-down: disagree -> 0U only.
- **(c) best line**: per unit with both app rows and the same live side on both, grade only the softer line for that
  side (Over -> lower line, Under -> higher line). Compared paired with the harder line and with both lines. Variant c2:
  the softer line graded with the live side at that line (side may differ).
- **(d) bump**: trigger |bump| >= 1.0 (d1); d2 = d1 restricted to late bumps (< 3 h). Signal side = the move's
  direction (raised -> Over, lowered -> Under: momentum); FADE = against the move.

## 5. Walk-forward (expanding window, no look-ahead)

- Weeks by start date (UTC): W0 Oct 11-18, W1 Oct 19-25, W2 Oct 26 - Nov 1, W3 Nov 2-8.
- For test week k (k = 1..3): the action for each rule is chosen on weeks < k only: the action with the highest hit rate
  on the rule's triggered rows, provided n >= 30 triggered rows and it beats KEEP; otherwise KEEP.
- Pooled test = W1..W3 with each week's own learned action. Baseline = the live side / stake on the same rows.

## 6. Scoring and acceptance (owner rule)

- Hit rate = wins / (wins + losses) at the real offered line; Overs and Unders separately (by the side taken).
- 95% CIs: bootstrap 2,000 resamples grouped by canonical match (seed 7).
- **PASS** requires all of: paired overall hit diff > 0 with the 95% CI lower bound > 0; Over-pick hit and Under-pick
  hit each not lower than baseline (point estimates); graded count not reduced; Over share not further from 50% by
  more than 1 point. A point gain whose CI includes 0 is reported as "not established".
- Calibration gap / log loss: informational only. Chances are never shrunk.
- Thresholds are never re-tuned on the test weeks; any later variant is a new, separately frozen rule.
