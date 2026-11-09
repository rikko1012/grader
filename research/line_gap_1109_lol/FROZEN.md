# LoL line-gap study (lg-lol-1) - frozen definitions, 2026-11-09

Frozen and committed BEFORE any hit rate, win/loss or result was computed on these features.
Research only. No live code, no flag, no deploy. The projection, chance and side of every forecast stay exactly as
they were saved; everything here is MARKET INFORMATION layered on top of the unchanged projection
(owner rule: the book line is never an input to the projection).

## Data

- Source: read-only local copy of the production LoL data-center tables, pulled 2026-11-09 ~07:30 UTC with paged
  admin GETs (`lol_1109.db`, the same copy used by `lol_accuracy_2026-11-09.md`). No new production read is needed:
  the next LoL series (Worlds final) starts 2026-11-14, so nothing has settled since the pull.
- Tables: `forecasts` (every saved pre-game forecast), `settlements` (versioned results), `quotes`
  (collector events per app market: open / change / removed / reopen, with line and sides/prices).
- Apps: PrizePicks (standard, goblin, demon) and Underdog (balanced). No other book is collected; no paid odds feed.

## Rows (one per app line)

1. Record = A15 rule: the latest forecast with `forecast_at < series_start` per (platform, market_key, line),
   joined to the latest settlement version of unit `platform|market_key|line`. Status graded or push.
2. De-duplication: key (sid, pid, stat, scope, platform, odds_type, line). If two records share the key (re-keyed
   market), keep the one with the latest forecast id. The number removed is reported.
3. Forecast time t = that record's `forecast_at`. Every feature uses only quote events with `observed_at <= t`.
4. Baseline side = the saved model side (p_over >= p_under -> Over, else Under; saved model_side breaks ties), exactly as
   in A15 / `lol_accuracy_2026-11-09.md`. Baseline letter = saved `grade` (A+ A A- B+ B B- = lettered, Lean = not).
5. Win = side matches the settled result; pushes are excluded from hit rates and counted apart.

## Lines at time t

- Player-market = (sid, pid, stat, scope). A quote market maps to it through the forecasts that used that market_key;
  quote markets never forecast are mapped by exact lower-case player name + same series start day to a player-market
  already mapped (no fuzzy matching). Unmapped quotes are dropped and counted.
- Main line per app: PrizePicks `standard`, Underdog `balanced`. Goblin/demon are alternative-payout lines, not
  discrepancies; they enter only the bump features.
- App line state at t: the last event of that (player-market, app, odds_type) with observed_at <= t; `removed` = not
  offered.
- Opening line: the line of the first event observed for that (player-market, app, odds_type). It is "known" only
  if that first event was observed at least 30 minutes after the collector's first observation (2026-10-17 02:40:59Z);
  otherwise the open is unknown and bump features are missing.

## Features (frozen)

- F1 `gap` = this row's line - the other app's main line at t (stat units); `gap_pct` = gap / other line.
  Defined only on main-line rows where both apps' main lines are offered at t.
- F2 `soft_side` (side the outlier line favours at this app): gap > 0 -> Under (this line is higher), gap < 0 -> Over,
  gap = 0 -> none.
- F3 `agree_gap` = baseline side == soft_side (only when gap != 0).
- F4 `bump` = this row's line - opening line of the same app/odds_type (stat units), sign = direction.
- F5 `market_agrees` = (bump > 0 and side Over) or (bump < 0 and side Under) - the line moved in the direction of
  Scout's side; `market_disagrees` = the opposite; bump = 0 -> neither.
- F6 `bump_hours` = hours from the last line change (<= t) to series start (descriptive only).
- F7 `ud_nv_over` = Underdog no-vig P(over) at its line from american prices (both sides priced only);
  `price_side` = Over if ud_nv_over > 0.5, Under if < 0.5. `price_gap` = |ud_nv_over - 0.5|. Used when both main
  lines are equal (gap = 0): the "same line, different price" case.

## Rules (all fixed now; nothing is tuned on the test period)

Baseline = saved side/letter on the same rows.

- R-a1 side by discrepancy: main-line rows with |gap| >= 0.5: if baseline side != soft_side, take soft_side.
- R-a2 the same with |gap| >= 1.0.
- R-a3 same line, price: main-line rows with gap = 0 and price_gap >= 0.03: if baseline side != price_side, take price_side.
- R-b1 letter by agreement: main-line rows with |gap| >= 0.5: agree -> letter one step up (Lean -> B- -> B -> B+ -> A- ->
  A -> A+; A+ stays), disagree -> one step down (B- -> Lean; Lean stays). Side unchanged.
- R-c1 best line: player-markets with both main lines offered at t, unequal, and the same baseline side on both: keep
  only the softer line for that side (Over -> lower line, Under -> higher line); the other row is not graded.
- R-d1 bump side: rows with known open and bump != 0: if market_disagrees, take the other side.
- R-d2 bump letter: rows with known open and bump != 0: market_agrees -> letter one step up, market_disagrees -> one
  step down. Side unchanged.

## Walk-forward

- Order by series start day. Burn-in: series days 2026-10-17 .. 2026-10-23 are training only, never scored.
  Test = series days 2026-10-24 .. 2026-11-08.
- For each test day d the rule is ACTIVE only if, on all rows of days < d (expanding window), (i) it changed at least
  30 rows (side rules) / 30 letters (letter rules), and (ii) its training score beats baseline on the same training
  rows (side rules: hit rate of all graded rows; letter rules: hit rate of lettered rows). Otherwise day d = baseline.
  This decides on/off only; thresholds are never re-fitted.
- R-c1 has no parameter; it is scored on the test days directly (and the same on/off gate is applied).
- Also reported, as descriptive in-sample evidence only: each rule applied fixed on every day.

## Scoring and acceptance (owner rule)

- Metrics on test days, rule vs baseline on identical rows: hit rate of graded rows (side rules) or lettered rows
  (letter rules / R-c1), Over picks and Under picks separately, graded count, Over share.
- 95% CIs: series-grouped bootstrap, 2,000 resamples, seed 7; the rule-minus-baseline difference is paired
  (same resampled series).
- PASS only if all hold on the test days: (1) hit-rate difference CI lower bound > 0; (2) Over-pick hit and Under-pick
  hit each not below baseline; (3) graded count not below baseline; (4) |Over share - 50%| not larger than baseline.
- The calibration gap is information only. No chance is shrunk. A pass would still only make the rule a shadow
  candidate; nothing goes live from this study.
