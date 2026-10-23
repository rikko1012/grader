# Model card - Scout Sports market candidates (mc-1, mkt-&lt;source&gt;, mi-1, sharp-2) and the grade policy

Registered in `sports/research/REGISTER.md` section 10 before any evaluation (2026-10-23). Forward only: no historical
player-prop quotes exist, so none of these has any evaluation yet. Validation state of every market version:
**unvalidated**. Data rights: Kalshi and ESPN display are owner-attested (owner attestation 2026-10-23).

## Sources (sports/books, sports/kalshi.py) - what actually covers NBA / NFL props (read 2026-10-23)

| Source | Access here | NBA props | NFL props | In mc-1 |
|---|---|---|---|---|
| Kalshi | public | points, rebounds, assists, threes, PRA, steals, blocks ("N+" ladders; 2,648 markets) | pass / rush / rec yards, receptions, rush and pass attempts, completions, TDs, INTs (3,378) | yes (sharp2.eligible_rung) |
| Polymarket | public gamma API | points, rebounds, assists, threes, steals, blocks: 2,622 "Player: Stat O/U line" markets, 100% resolved to ESPN players | passing / rushing / receiving yards, receptions, passing attempts / completions: 2,012 markets, 1,663 resolved | yes |
| The Odds API | ODDS_API_KEY on Render only | key not set here - parser = foundation adapter, event resolution by exact ESPN team names, credit ledger (500 / month, 50 reserve, <= 6 events x markets per cycle) | same | yes, once live |
| Thunderpick | THUNDERPICK_API_KEY on Render only | key not set here; NBA / NFL sport paths and prop format UNVERIFIED -> capture-only inventory | same | no, until verified |
| OddsPapi | ODDSPAPI_API_KEY on Render only | key not set here; prop market ids UNVERIFIED -> capture-only | same | no, until verified |

Polymarket contract texts (NBA and NFL): YES / Over = more than the line, NO / Under = the line or fewer, overtime
included, a player who is inactive or does not play resolves **NO / Under** - so its price is unconditional on
playing; mc-1 divides its midpoint by (1 - d), d = 0.0749 (NBA, ds-1 train DNP share of rotation players; NFL d = 0,
reason-coded). Kalshi settles an inactive player at a fair price; pick'em platforms void a DNP.

## mc-1 (market-only consensus) and mkt-&lt;source&gt;

Per source: eligible quote nearest the target line (two-sided, spread <= 0.05, liquidity / trades, observed before the
start and <= 15 min old, same event / player / stat / full-game scope / ordinary variant) -> P(Over | no push) at its
line -> moved to the target line with the foundation's dispersion.json shape (never sm-1) -> equal-weight log-odds mean
over sources (weights fixed in advance). Unconditional output with the shape's push mass at a whole line. The target's
own platform never anchors it (PrizePicks / Underdog are never sources). mkt-&lt;source&gt; = the same rule with one source.

## mi-1 (market-informed)

sm-1's calibrated PMF for that player, shifted by one mean factor fitted to the same eligible sources (least squares on
log-odds). Shape from the statistical model, location from the market. Uses the same quote ids (input_snapshot_ids).

## First forward run (scratch store, 2026-10-23 23:04 UTC, `sports/research/results/model_forward_run_2026-10-23.json`)

| League | sm-1 | mi-1 | mc-1 | of which consensus (>= 2 sources) | mkt-kalshi | mkt-polymarket | sharp-2 |
|---|---|---|---|---|---|---|---|
| NBA | 515 | 394 | 394 | 95 (both leagues) | 390 | 96 | 390 |
| NFL | 884 | 568 | 568 | | 568 | 3 | 568 |

Targets without an eligible market quote (NBA / NFL): spread > 0.05: 62 / 146; no quote for that player and stat:
55 / 158; untraded Kalshi rung: 3 / 9. Most Polymarket NFL books were empty (0.01 / 0.99) and failed the spread filter.
Evaluation: on the same settled target offers, separately per version (learning.scorecard), gate F (REGISTER.md 7).

## Grade policy sports-gp-1 (sports/model/grade_policy.py)

Unrounded calibrated unconditional probability of each OFFERED side; chosen side = the offered side with the higher
probability. Bands (anchored on published Underdog break-evens 53.45% / 55.59%): A >= 0.60, B >= 0.5559, C >= 0.5345,
else "Lean Over / Lean Under". Separate output fields: validation_state, band, grade (= band only when
forward-validated), stake_units (0 until gate F; then sports-sp-1: A 1.0U, B 0.5U, C / Lean 0U; any data-quality code
-> 0U with the letter kept), stake_reason, data_quality, per-side availability (an unoffered side is "unavailable",
never a tradable probability). Member text: unvalidated -> "Projection only - model not yet validated";
history-validated (sm-1 NBA points / rebounds / assists / threes) -> "Projection - validated on past games, not yet on
live lines".

## API (read-only, precomputed snapshots; sports/api_model.py, two ROUTES lines)

- `GET /api/sports/model/forecasts?league=nba|nfl[&event_id=&player_id=&stat=&model_version=&limit<=500]` (member)
- `GET /api/sports/model/ladder?league=&forecast_id=[&lines=a,b,c]` (member) - every alternate line from the
  forecast's one distribution: P(X > L), P(X < L), P(X = L)
- `GET /api/sports/model/registry` and `GET /api/sports/model/features` (member)
- `GET /api/sports/admin/model/learning` (admin) - scorecard, gate F status, diagnostic queue

Handlers only read `<SPORTS_DATA_DIR>/model/api/*.json` written by the worker (`train forecast | learn | snapshot`),
through web.cached (single flight, keyed by file mtime): 1,000 cached calls took 0.03 s here; first load 0.02 s.
