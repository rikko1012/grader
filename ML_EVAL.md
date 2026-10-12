# ML_EVAL - series-winner market prices vs HLTV results

Generated 2026-10-12T00:25:37+00:00 by `ml_eval.py` (research only - no grader, prop grade or member output changed). Every number below comes with its count.

**What the prices are.** Polymarket numbers are *Polymarket's recorded price history (mid/last-trade series), not executable quotes we captured*: CLOB `/prices-history` on the outcome-0 token, fidelity 10 min, last point at or before scheduled start - 30 min and no older than 6 h before that cutoff. Returns computed from them are price-history returns, not fills.

## 1. Inventory
### Polymarket (gamma series 10310, closed=true)
- Events evaluated: **3000** (newest first by id; listing stopped at the 3000-event cap, older events exist; event end dates in the evaluated listing 2026-08-09 to 2026-11-10)
- Series-winner market identified: moneyline 2999
- **Eligible observations: 1171** (one per HLTV match)
- Span of eligible matches: 2026-08-12 to 2026-10-11

| Exclusion | Count |
|---|---:|
| unresolved team mapping | 971 |
| no HLTV match (same teams, +-1 day) | 657 |
| ambiguous HLTV match | 70 |
| no price before cutoff | 64 |
| post-start points only | 38 |
| forfeit | 26 |
| duplicate market for the same HLTV match (kept highest volume) | 2 |
| no series-winner market | 1 |
| no HLTV result | 0 |
| no price history | 0 |
| price already resolved before cutoff (start time wrong) | 0 |
| Polymarket resolution disagrees with HLTV winner | 0 |

Of the 657 'no HLTV match' exclusions (both names resolved): 9 have the same pair on HLTV within +-7 days (possible date mismatch), 8 start on/after the newest HLTV date; the rest are matches the HLTV database does not contain.

Most frequent unresolved team names (no exact HLTV name; the HLTV database only holds 368 teams): WRAITH PCIFIC (39), Teletubisie (35), Esport BERG (31), KUUSAMO.gg (28), MORROW (26), BET-M 33 (25), Nice Try (23), Fluxo W7M (23), Vitality Academy (17), Semente do Mal (17), NEXORA (16), Inner Circle Prospect (16), Passion Academy (15), Diamant Esports (15), Rebels Gaming (14), For Fun v4 (14), Rush (13), Orgless (12), ODDIK (12), VP.Prodigy (12)

### Kalshi (KXCS2GAME, settled; 1-minute candlesticks)
- Settled events listed: 6944; evaluated: **3000** (newest by close time, capped at 3000)
- **Eligible observations: 1089** (one per HLTV match)
- Span of eligible matches: 2026-08-09 to 2026-10-11

| Exclusion | Count |
|---|---:|
| unresolved team mapping | 1031 |
| no HLTV match (same teams, +-1 day) | 673 |
| no two-sided quote (spread <= 10c) at cutoff | 76 |
| ambiguous HLTV match | 59 |
| forfeit | 27 |
| no price before cutoff | 24 |
| post-start points only | 15 |
| no price history | 5 |
| duplicate market for the same HLTV match (kept highest volume) | 1 |
| no HLTV result | 0 |
| price already resolved before cutoff (start time wrong) | 0 |
| Kalshi resolution disagrees with HLTV winner | 0 |

Of the 673 'no HLTV match' exclusions (both names resolved): 11 have the same pair on HLTV within +-7 days (possible date mismatch), 8 start on/after the newest HLTV date; the rest are matches the HLTV database does not contain.

Most frequent unresolved team names (no exact HLTV name; the HLTV database only holds 368 teams): Permitta Esports (40), WRAITH PCIFIC (38), Gremio Esports (35), MORROW (34), Teletubisie (34), Esport BERG (34), QUINTESSENCIA (32), KUUSAMO.gg (28), 33 (20), Nice Try (20), Vitality Academy (18), Passion Academy (18), NEXORA (18), Fluxo W7M (18), Inner Circle Prospect (17), Diamant Esports (16), Semente do Mal (15), VP.Prodigy (15), FC Famalicao Esports (14), Rush (14)

Kalshi history: the official candlesticks endpoint returns 1-minute YES bid/ask closes before the match, so a pre-match quote history exists. Mid = (bid + ask)/2 of the last candle ending at or before the cutoff, two-sided book with spread <= 0.10 required; team A = first market of the event by ticker. Scheduled start parsed from rules_primary (EDT/EST).

## 2. Baseline: market price vs a fixed 50%
### Polymarket (n = 1171 matches)

| Metric | Market | Fixed 50% | Gain vs 50% (95% match-bootstrap CI) |
|---|---:|---:|---|
| Log-loss | 0.6036 [+0.5843, +0.6239] | 0.6931 | +0.0896 [+0.0692, +0.1087] |
| Brier | 0.2078 [+0.1991, +0.2167] | 0.2500 | +0.0422 [+0.0333, +0.0509] |
| Favourite won | 792/1171 (67.6%) (results from before the coin-flip rule: p = 0.5 counted as a team-A favourite) | | |

Calibration (outcome-0 price; observed = share of team-A wins, Wilson 95% interval):

| Price bin | n | Mean price | Observed | 95% CI |
|---|---:|---:|---:|---|
| 0.0-0.1 | 2 | 0.090 | 0.500 | 0.095-0.905 |
| 0.1-0.2 | 25 | 0.166 | 0.160 | 0.064-0.346 |
| 0.2-0.3 | 65 | 0.259 | 0.231 | 0.145-0.346 |
| 0.3-0.4 | 130 | 0.354 | 0.339 | 0.263-0.423 |
| 0.4-0.5 | 202 | 0.452 | 0.396 | 0.331-0.465 |
| 0.5-0.6 | 252 | 0.549 | 0.516 | 0.454-0.577 |
| 0.6-0.7 | 255 | 0.645 | 0.722 | 0.664-0.773 |
| 0.7-0.8 | 150 | 0.743 | 0.800 | 0.729-0.856 |
| 0.8-0.9 | 72 | 0.845 | 0.861 | 0.763-0.923 |
| 0.9-1.0 | 18 | 0.926 | 0.889 | 0.672-0.969 |

By favourite price range:

| Favourite price | n | Mean fav price | Fav won | Fav win rate (95% CI) | Log-loss (CI) | Brier |
|---|---:|---:|---:|---|---|---:|
| 0.5-0.6 | 451 | 0.548 | 250 | 0.554 (0.508-0.600) | 0.6858 [+0.6753, +0.6957] | 0.2463 |
| 0.6-0.7 | 387 | 0.645 | 271 | 0.700 (0.653-0.744) | 0.6152 [+0.5860, +0.6442] | 0.2120 |
| 0.7-0.8 | 216 | 0.743 | 171 | 0.792 (0.733-0.841) | 0.5085 [+0.4531, +0.5674] | 0.1637 |
| 0.8-0.9 | 97 | 0.842 | 83 | 0.856 (0.772-0.912) | 0.4210 [+0.3049, +0.5555] | 0.1258 |
| 0.9-1.0 | 20 | 0.924 | 17 | 0.850 (0.640-0.948) | 0.4353 [+0.0798, +0.8015] | 0.1307 |

LAN / online (HLTV match_overview.lan):

| Segment | n | Log-loss (CI) | 50% log-loss | Gain vs 50% (CI) | Brier |
|---|---:|---|---:|---|---:|
| LAN | 416 | 0.5944 [+0.5600, +0.6301] | 0.6931 | +0.0988 [+0.0630, +0.1329] | 0.2031 |
| online | 755 | 0.6086 [+0.5866, +0.6310] | 0.6931 | +0.0845 [+0.0621, +0.1065] | 0.2104 |
| unknown | 0 | - | - | - | - |

Tier proxy (Polymarket volume tertiles):

| Segment | n | Log-loss (CI) | 50% log-loss | Gain vs 50% (CI) | Brier |
|---|---:|---|---:|---|---:|
| low volume (< $30,036) | 390 | 0.5616 [+0.5274, +0.5943] | 0.6931 | +0.1315 [+0.0977, +0.1657] | 0.1893 |
| mid volume ($30,036 - $127,266) | 390 | 0.6220 [+0.5914, +0.6538] | 0.6931 | +0.0712 [+0.0389, +0.1016] | 0.2168 |
| high volume (>= $127,266) | 391 | 0.6271 [+0.5921, +0.6610] | 0.6931 | +0.0661 [+0.0320, +0.1006] | 0.2172 |

### Kalshi (n = 1089 matches)

| Metric | Market | Fixed 50% | Gain vs 50% (95% match-bootstrap CI) |
|---|---:|---:|---|
| Log-loss | 0.5988 [+0.5792, +0.6198] | 0.6931 | +0.0943 [+0.0733, +0.1140] |
| Brier | 0.2060 [+0.1973, +0.2153] | 0.2500 | +0.0440 [+0.0347, +0.0527] |
| Favourite won | 742/1089 (68.1%) (results from before the coin-flip rule: p = 0.5 counted as a team-A favourite) | | |

Calibration (team A = first market of the event by ticker, YES mid; observed = share of team-A wins, Wilson 95% interval):

| Price bin | n | Mean price | Observed | 95% CI |
|---|---:|---:|---:|---|
| 0.0-0.1 | 14 | 0.070 | 0.071 | 0.013-0.315 |
| 0.1-0.2 | 39 | 0.155 | 0.179 | 0.090-0.327 |
| 0.2-0.3 | 107 | 0.254 | 0.224 | 0.156-0.312 |
| 0.3-0.4 | 163 | 0.353 | 0.301 | 0.235-0.375 |
| 0.4-0.5 | 203 | 0.450 | 0.433 | 0.367-0.502 |
| 0.5-0.6 | 203 | 0.549 | 0.571 | 0.503-0.638 |
| 0.6-0.7 | 192 | 0.644 | 0.661 | 0.592-0.725 |
| 0.7-0.8 | 106 | 0.744 | 0.811 | 0.727-0.874 |
| 0.8-0.9 | 50 | 0.842 | 0.920 | 0.812-0.969 |
| 0.9-1.0 | 12 | 0.933 | 0.833 | 0.552-0.953 |

By favourite price range:

| Favourite price | n | Mean fav price | Fav won | Fav win rate (95% CI) | Log-loss (CI) | Brier |
|---|---:|---:|---:|---|---|---:|
| 0.5-0.6 | 400 | 0.549 | 227 | 0.568 (0.518-0.615) | 0.6835 [+0.6730, +0.6945] | 0.2452 |
| 0.6-0.7 | 356 | 0.644 | 242 | 0.680 (0.630-0.726) | 0.6245 [+0.5961, +0.6547] | 0.2166 |
| 0.7-0.8 | 216 | 0.744 | 171 | 0.792 (0.733-0.841) | 0.5134 [+0.4564, +0.5736] | 0.1656 |
| 0.8-0.9 | 91 | 0.842 | 79 | 0.868 (0.783-0.923) | 0.4037 [+0.2922, +0.5353] | 0.1182 |
| 0.9-1.0 | 26 | 0.932 | 23 | 0.885 (0.710-0.960) | 0.3381 [+0.0693, +0.6150] | 0.0998 |

LAN / online (HLTV match_overview.lan):

| Segment | n | Log-loss (CI) | 50% log-loss | Gain vs 50% (CI) | Brier |
|---|---:|---|---:|---|---:|
| LAN | 369 | 0.5801 [+0.5420, +0.6204] | 0.6931 | +0.1131 [+0.0726, +0.1511] | 0.1965 |
| online | 720 | 0.6085 [+0.5862, +0.6322] | 0.6931 | +0.0847 [+0.0609, +0.1068] | 0.2108 |
| unknown | 0 | - | - | - | - |

Tier proxy (Kalshi volume tertiles):

| Segment | n | Log-loss (CI) | 50% log-loss | Gain vs 50% (CI) | Brier |
|---|---:|---|---:|---|---:|
| low volume (< 27,523 contracts) | 363 | 0.5121 [+0.4810, +0.5458] | 0.6931 | +0.1810 [+0.1473, +0.2121] | 0.1678 |
| mid volume (27,523 contracts - 96,806 contracts) | 363 | 0.6459 [+0.6127, +0.6813] | 0.6931 | +0.0472 [+0.0118, +0.0804] | 0.2274 |
| high volume (>= 96,806 contracts) | 363 | 0.6385 [+0.6012, +0.6759] | 0.6931 | +0.0546 [+0.0170, +0.0917] | 0.2227 |

### Same matches on both venues (n = 1048)
Polymarket log-loss 0.5990, Kalshi 0.5961; difference (Kalshi - Polymarket) -0.0029 [-0.0086, +0.0021]; mean absolute price gap 0.019.

## 3. Staged feature tests (Polymarket set)
Chronological split by match start (one observation per match): development n = 703 (2026-08-12 to 2026-09-19), validation n = 234 (2026-09-19 to 2026-10-01), final test n = 234 (2026-10-01 to 2026-10-11).

Each feature alone: `logit(p) = logit(p_mkt) + b*x` (x scaled by its development RMS, no intercept), b fitted on development with L2 strength from [0.0, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0, 10000.0], the strength chosen on validation log-loss (the SAME validation rows then decide promotion - an optimistic selection; the current code cross-validates the strength inside development). Promotion rule: validation log-loss gain with a match-grouped 95% bootstrap CI excluding zero. Features used HLTV rows dated strictly before the HLTV match date (the current code uses the earlier of that date and the UTC start date). Missing feature = 0 (no correction): a_rating 21, b_mappool 14, c_form 11, e_h2h 684 of 1171 matches.

| Feature | Chosen L2 | b per SD | Val log-loss market -> model | Val gain (95% CI) | Promoted |
|---|---:|---:|---|---|---|
| (a) Shrunk HLTV rating differential of expected starting rosters | 100 | +0.0542 | 0.5883 -> 0.5878 | +0.00049 [-0.0026, +0.0035] | no |
| (b) Map-pool win rate (top-4 maps, 180 d, shrunk) differential | 10000 | +0.0001 | 0.5883 -> 0.5883 | -0.00000 [-0.0000, +0.0000] | no |
| (c) Recent series form (last 10 series, shrunk) differential | 10000 | +0.0006 | 0.5883 -> 0.5883 | -0.00003 [-0.0001, +0.0000] | no |
| (e) Roster-aware head-to-head (365 d, both teams kept >= 3 of today's 5), shrunk | 10000 | +0.0006 | 0.5883 -> 0.5883 | -0.00001 [-0.0000, +0.0000] | no |
| (d) Feature (a) x LAN flag (tested only if (a) is promoted) | - | - | - | not tested - (a) was not promoted | no |

Development baseline log-loss 0.6074 (n = 703); validation baseline 0.5883 (n = 234).

Hypothetical validation returns at recorded Polymarket prices + 1 cent, 1 unit per bet, side bought when the feature-corrected probability exceeds the recorded price by the threshold. **Price-history returns, not executable fills.**

| Feature | Edge >= | Bets | Won | Avg price | Net units | ROI (95% CI; n/a under 20 bets) |
|---|---:|---:|---:|---:|---:|---|
| (a) | 3 pts | 1 | 1 | 0.415 | +1.35 | +135.3% n/a |
| (a) | 4 pts | 0 | 0 | - | +0.00 | - |
| (a) | 5 pts | 0 | 0 | - | +0.00 | - |
| (a) | 6 pts | 0 | 0 | - | +0.00 | - |
| (b) | 3 pts | 0 | 0 | - | +0.00 | - |
| (b) | 4 pts | 0 | 0 | - | +0.00 | - |
| (b) | 5 pts | 0 | 0 | - | +0.00 | - |
| (b) | 6 pts | 0 | 0 | - | +0.00 | - |
| (c) | 3 pts | 0 | 0 | - | +0.00 | - |
| (c) | 4 pts | 0 | 0 | - | +0.00 | - |
| (c) | 5 pts | 0 | 0 | - | +0.00 | - |
| (c) | 6 pts | 0 | 0 | - | +0.00 | - |
| (e) | 3 pts | 0 | 0 | - | +0.00 | - |
| (e) | 4 pts | 0 | 0 | - | +0.00 | - |
| (e) | 5 pts | 0 | 0 | - | +0.00 | - |
| (e) | 6 pts | 0 | 0 | - | +0.00 | - |

**Freeze** before the test set was scored: 2026-10-12T00:26:26+00:00, sha256 `c4edd04742283033...` (pinned dev / validation / test match ids, promoted features, coefficients, code tag). Promoted: none.

**Final test (scored before the one-time guard existed - the number of scorings was not recorded):** market baseline log-loss 0.6072 [+0.5632, +0.6522] vs 0.6931 at 50% (gain +0.0859 [+0.0409, +0.1295]), Brier 0.2112 (gain +0.0388 [+0.0185, +0.0582]), favourite won 153/234, n = 234 matches.

No feature was promoted, so no feature has a test-set number (by design).

## 4. Forward cohort (ml_snapshots.db)
Not measurable yet: C:\Users\tikic\cs2_scout\.cache\ml_snapshots.db not found (the live collector has not written snapshots here yet). The cohort starts accumulating once the live collector writes snapshots; rerun `python ml_eval.py --forward-only` to score it.

- HLTV team_rankings holds a single snapshot date (2026-10-11), so point-in-time opponent ranks do not exist; using today's ranks for past matches would leak the future, so feature (c) is NOT rank-adjusted.
- HLTV database covers 2024-01-09 to 2026-10-11 with 8578 matches and 368 teams; Polymarket matches between teams outside it are excluded as unresolved/no match.
- HLTV match dates are calendar dates only, so the +-1 day window is applied to the UTC date of the scheduled start; two meetings of the same teams inside it are excluded as ambiguous.
- Winner = more maps won on HLTV's per-map rows, cross-checked with HLTV's series row; forfeits (match_overview.forfeited_maps > 0) excluded. A market whose own resolution disagrees with HLTV is excluded as a likely mismatch.
- Feature constants (60-day roster window, k = 20 rating shrinkage, top-4 maps over 180 days with k = 10, last 10 series within 365 days with k = 5, head-to-head 365 days with k = 3) were fixed before any test ran; the only fitted quantities are b (development) and the L2 strength (validation).
- Hypothetical returns use each tested feature's correction even when it was not promoted; they are on the validation set that also chose the L2 strength, so they are optimistic, and they are price-history returns at recorded prices + 1 cent, not executable fills.
- Expected roster (a, e) = the 5 players with the most recent maps for the team in the 60 days before the match (ties: more maps). Player rating = mean HLTV rating over his maps in those 60 days, shrunk to 1.0 with k = 20 maps.

## Run notes (added by hand after the generated report)

- **Test-set discipline.** The final test set (newest 20%, 234 matches, 2026-10-01 to 2026-10-11) was scored once, in the `--final` run above, after the freeze record was written (`ml_eval_freeze.json` in the download cache, sha256 above). One earlier draft run of the same pipeline (before the maintained `team_aliases.json` entries were picked up: 1115 instead of 1171 eligible matches) also wrote test-set baseline numbers to a scratch file. Those were never opened and no decision depended on them; no feature had been promoted in that draft either.
- **Edge threshold (4-5 pts).** Not supported by this data: no feature passed promotion, and the validation-chosen corrections are so small that only (a) produced a single 3-point edge on validation (1 bet), and none at 4, 5 or 6 points. A 4-5 point threshold cannot be evaluated as a strategy until a feature earns its place.
- **Kalshi history.** Kalshi's official candlesticks endpoint gives a 1-minute YES bid/ask history before the match (candles appear only in minutes where something changed; the last candle before the cutoff carries the standing quote). Markets settled before Kalshi's historical cutoff are served by `/historical/...` endpoints, which the code also lists. Kalshi numbers above are scored as a baseline only; the feature tests use the Polymarket set.
- **Known gap.** `ALIASES` is an exact-string lookup, so Kalshi's spelling "FC Famalicao Esports" (no accent) stayed unresolved (14 Kalshi events). This affects only the Kalshi baseline inventory, and was not fixed after the test set had been scored, so the frozen split stays as it is.
- **Forward cohort.** `moneyline.py` writes `CS2_DATA_DIR/.cache/ml_snapshots.db` (tables `ml_forecasts` = frozen baseline `ml-baseline-1`, `ml_quotes` = per-platform quotes with `prob_a`). The file did not exist when this ran, so the forward cohort has 0 rows. `python ml_eval.py --forward-only` scores it: per match, the latest row at or before start - 30 min (the earliest start any source states), scored only if it was available then and no older than 6 h before that cutoff (otherwise a counted exclusion), linked to HLTV through the `hltv:<id>` match id when present.

- **Re-rendered 2026-10-12 after the review fixes (nothing recomputed).** Display corrections only: the 50% log-loss prints 0.6931 (it was double-rounded to 0.6932), an ROI 'interval' from fewer than 20 bets prints n/a, the favourite count notes that this run counted p = 0.5 as a team-A favourite (4 Polymarket matches), the method text describes this run as it was made (L2 strength chosen on validation; features dated before the HLTV date), and the test line no longer says 'touched once' (see the discipline note above). The per-match rows of the 234 test matches were moved out of `ml_eval_results.json` into the ml_eval cache dir. The code now chooses the L2 strength inside development, keys features to the earlier of the HLTV and UTC start dates, excludes best-of-2 markets, pins the split in the freeze and refuses a second `--final` without `--rescore-test` - these apply from the next run, which needs a fresh non-final run (new freeze); because this test split has already been scored, the next `--final` is a rescore and the cache dir's scored-test record makes the code say so.
