# Grader learning audit — 2026-10-28

Base: `0b7626c`. This is an engineering audit, not a new model registration or evidence of profitability.

Validation: the full Sports suite plus CS2 learning/calibration and LoL cohort/calibration/daily-fit suites completed with 916 passed and 68 skipped. The run emitted 104 empty-history aggregation warnings. These are local regression results, not live deployment or market-performance validation.

## Existing capabilities to retain

Sports already learns from player-game history, archived forecasts and settled results. The statistical models separate opportunity from efficiency; saved inputs and frozen artifacts support forecast replay. Market-informed forecasts use compatible recorded prices. Daily cal-mi fitting and grade containment are implemented in `model/daily.py` under REGISTER A9a. Training and inference remain separate.

CS2 already has immutable forecast collection, corrected settlements, error decomposition, chronological challenger evaluation, and shadow market-line candidates in root `learning.py`. LoL has its own cohort learning and calibration evaluation. These should remain separate models: a basketball player's minutes, a quarterback's attempts and a CS2 player's rounds are different opportunities.

## Repairs in this change

1. A settlement learned today must score its original forecast even when that forecast is older than the settlement polling window. Previously such forecasts were skipped after 30 days. Event-scoped reads now stream forecasts without the forecast-age cutoff. Regression coverage includes late results, corrections, idempotence and avoiding unrelated historical events.
2. The retraining sample trigger must count new data since the most recent fit's data snapshot, including a challenger fit. Previously it reused the older champion's snapshot, potentially triggering repeated fits on already-used data. Fit time and data cutoff now come from the same registry row. NBA and NFL regression coverage includes an out-of-order cutoff and the no-registry fallback.

## Outstanding evidence and work

- `learning.forward_gate` subtracts aggregate sharp-2 log loss from aggregate candidate loss. That diagnostic is not a paired market comparison and must not be presented as proof of beating the market. A replacement needs matching event/player/stat/period/line/forecast-time cohorts and game-clustered intervals. Current code reports a review, never automatic promotion.
- Audit correction availability through calibration walk-forward: scorecard consumers use settlement time; a corrected result's discovery time must not be treated as information available before the correction arrived. This audit has not established that this is safe end to end.
- Direct FD/BR quotes are captured but excluded from the existing consensus under its registration. Expanding consensus requires a separately versioned shadow candidate, source coverage checks and deduplication when multiple feeds represent the same sportsbook. Do not silently change mc-1.
- Historical box scores are not historical executable player-prop odds. Missing historical prices cannot be reconstructed from today's lines or substituted with a fabricated price. Collect prospective price snapshots and record missingness.
- Validate the production history refresh and model inputs per league. A successful job or health endpoint does not prove player statistics are current.

## Acceptance criteria for future model improvements

Use only information available at each prediction timestamp: past player statistics, expected opportunity, opponent/context, contemporaneous lines and actual two-sided prices. Keep provider time, observation time, event time and settlement/correction discovery time distinct. Match player, event, statistic, period, line, jurisdiction and settlement rules before comparing offers.

Evaluate chronological held-out matches against both the current production model and a contemporaneous de-vigged market baseline on the same eligible props. Report log loss, Brier score, calibration, interval coverage and game-clustered uncertainty. Separately report price availability, closing-line movement and realized returns under actual payout/push/void rules; probability accuracy alone is not profitability. Count distinct opportunities rather than repeated polling snapshots. Preserve unfavorable results and failed candidates.

Promote only after the applicable registered forward gate; do not tune repeatedly on the test set, increase confidence labels to improve appearances, or enable stakes to manufacture a profitable backtest. A weak or unsupported prediction should be withheld. None of these checks can guarantee profit or a flawless grader.
