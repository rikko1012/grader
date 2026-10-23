# Sports data-branch audit (2026-10-23)

Scope: the data foundation merged from `claude/dreamy-wozniak-kefq4r @ d393292` (sharp-1). Owner brief section 1 asks
for three audits before any member use: the 53.45% Underdog break-even, Kalshi handling, and the historical-spread
(line-shift) transformation. Every number below comes from a file in `sports/research/results/` or a test in
`sports/tests/`. Rule followed: where an assumption is wrong, the existing outputs stay as versioned research records
(`sharp-1`, imported unchanged by `sports/legacy.py`) and the correction is a new version (`sharp-2`,
`sports/sharp2.py`). Nothing was rewritten.

Status of sharp-1 and sharp-2: **research only**. Neither has a grade, a stake or a validated edge; no settled forecast
exists yet for either (the games had not been played).

## 1. The 53.45% Underdog break-even

Source: Underdog help article 13780101 "Pick'em Standard & Flex Entry Payouts", read 2026-10-23 ~20:35 UTC
("Updated over a month ago"). Payouts include the returned entry fee. Encoded in `sports/payouts.py`
(`UD_TABLE_VERSION = ud-payouts-2026-10-23`), tested in `sports/tests/test_payouts.py`.

| Entry | Published payout | Break-even per pick (equal p, modifiers 1.0x) |
|---|---|---|
| Standard 2 | 3.5x | **53.45%** |
| Standard 3 | 6.5x | 53.58% |
| Standard 4 | 12x | 53.73% |
| Standard 5 | 20x | 54.93% |
| Standard 6 | 35x | 55.29% |
| Standard 7 | 65x | 55.08% |
| Standard 8 | 120x | 54.97% |
| Flex 3 (0 / 1 loss) | 3.25x / 1.09x | 55.39% |
| Flex 4 (0 / 1) | 6x / 1.4x | 55.59% |
| Flex 5 (0 / 1) | 10x / 2.5x | 54.75% |
| Flex 6 (0 / 1 / 2) | 25x / 2.6x / 0.25x | 53.82% |
| Flex 7 (0 / 1 / 2) | 40x / 2.75x / 0.5x | 55.42% |
| Flex 8 (0 / 1 / 2) | 80x / 3x / 1x | 55.07% |

Standard: p* = B^(-1/n). Flex: the p solving sum_k C(n,k) p^(n-k) (1-p)^k F_n[k] = 1 (bisection, tested to 1e-9).

Findings
- **53.45% is arithmetically right but only for a standard 2-pick entry with every pick at 1.0x**; it is the lowest
  break-even in the table. Every other entry type needs more per pick (up to 55.6% for a 4-pick flex). The data
  branch's README quoted 3-pick 53.6% and 5-pick 54.9% correctly but ranked every Underdog line against 53.45%.
- **Per-side payout modifiers**: sharp-1 compared `chance x modifier` with 53.45%. Under the assumption entry payout =
  base x product of modifiers this is exactly the 2-pick break-even with a partner pick at break-even
  (`payouts.modifier_break_even`, test `test_modifier_break_even_matches_the_data_branch_rule_for_two_picks`).
  **Underdog does not publish how modifiers combine** ("your total payout decreases ... increases"), says correlated
  projections can change the payout, and that the modifier pick must be correct for the boost. The product rule is
  therefore labelled an assumption (`payouts.MODIFIER_RULE`) wherever it is used.
- Ties / voids: both platforms void a tied or DNP pick and reduce the entry; sharp-1's conditional-on-no-push chance is
  the right quantity for the surviving entry, but sharp-1 stored only the conditional value (see 4.1).
- **PrizePicks**: payout tables are in-app only. `payouts.PP_TABLE = None`; every PrizePicks payout / break-even is
  UNVERIFIED (as in the data branch). Goblin / demon payouts are also unpublished.
- Correction: no per-line "value" against a single break-even in sharp-2. Slip agents use `payouts.py` with the actual
  entry size and type; the expected-return arithmetic stays internal (no member-facing EV feature).

## 2. Kalshi handling

Measured on the live public API 2026-10-23 20:20-21:25 UTC (raw answers in the scratch run, counts in
`sports/research/results/coverage_run_2026-10-23.json`) and in Kalshi's docs (docs.kalshi.com market lifecycle /
settlement / fee rounding, read the same day).

| Topic | Data branch (sharp-1) | Finding | sharp-2 / integration |
|---|---|---|---|
| Price | YES bid/ask midpoint as P(Over N-0.5) | Correct reading of a "N+" market (strike_type greater, floor_strike N-0.5; all 6,079 stored markets passed this check). Midpoint ignores the order-flow lean sharp-1 itself mentions (unmeasured: no settled pre-game quotes yet) | Same midpoint; bid, ask, NO bid/ask and book sizes stored per revision |
| Spread cut | <= 5c | NBA: 1,879 of 2,567 two-sided markets (73%); NFL 2,254 of 3,299 (68%). The cut is a policy choice, never tuned | Kept (`sharp2.MAX_SPREAD`) |
| Liquidity | not used | **963 of 2,567 NBA and 1,972 of 3,299 NFL two-sided markets had zero volume AND zero open interest** (market-maker quotes nobody traded). sharp-1 used them as "sharp" prices | sharp-2: a rung with no trades and no open interest is not an anchor; volume, 24 h volume, OI, last price recorded |
| Freshness | 5-min cache; cache read time only | Kalshi's `updated_time` does **not** move with quotes or trades (NYK@BOS markets: updated_time 21 h old while 24 h volume > 0). The source quote time is unknown | `source_time_unknown = True`; freshness = OBSERVED age of the quote's latest sighting <= 15 min at forecast time; reason code `kalshi_quote_time_unknown` on every sharp-2 forecast |
| In-play | not checked | Markets stay `active` during the game: CHI@MIL tipped 20:00Z and 227 of its 248 player markets were still open with a bid when read at 21:15Z and 21:23Z. A Kalshi quote read after tip-off is an in-play price | sharp-2 refuses a quote observed at/after the ESPN start (`test_sharp2_refuses_stale_in_play_wide_and_untraded_quotes`) |
| Start time | matched by ticker date (fixed UTC-6 rule) | `occurrence_datetime` / `expected_expiration_time` = ESPN start + 3 h on all 12 NBA events of 2026-10-23 - never a start. The UTC-6 rule is not DST-aware (worked for evening games) | Kalshi time stored as `listed_time_kind = expected_expiration`; dates by US Eastern with exact DST rules (`identity.us_date`, tested across both 2026 transitions); events by date + both teams |
| Fees | not modelled | Series `fee_type: quadratic`, `fee_multiplier: 1` (KXNBAPTS, KXNFLRECYDS). Published general taker formula round_up(0.07 x C x P x (1-P)) per third-party sources; Kalshi's fee schedule PDF answered 429 - coefficient UNVERIFIED. Fees do not change the midpoint; they widen the no-trade band (~1.75c per contract at 50c) | `payouts.kalshi_taker_fee` (labelled unverified); not used in any probability |
| Settlement rules | "both platforms void a DNP" applied to all | Kalshi contract: inactive / never enters -> **settles to a fair price** (not void, not No); postponed > 48 h -> fair price; settlement source NBA = "the Governing League" (nba.com) then ESPN, NFL = the governing league only; markets close early when the event occurs | `settlement.py` voids a Kalshi DNP with reason "fair-price settlement"; rule text kept per offer |
| Settlement vs ESPN | not checked | **NBA 2026-10-20..22: 3,967 of 3,968 settled results agree with ESPN box scores (99.97%)**; the one difference (Norman Powell 2+ steals: Kalshi No, ESPN 2) and **14 of 2,513 final values** on No-settled markets (ESPN one higher each time) look like box-score corrections after Kalshi settled. **NFL 2026-10-18/19/22: 4,533 of 4,533 results agree.** NBA `expiration_value` on a Yes is the value at early close, not the final; NFL `expiration_value` holds the stat NAME | `sports/research/results/kalshi_vs_espn_2026-10-23.json`; ESPN finals are re-read for 4 days and a changed value makes a superseding settlement revision |
| Team codes | none needed (matched by player name + date) | Kalshi uses JAC for ESPN's JAX (found by the verification: 376 markets of HOU vs JAX unmatched before the alias) | `team_aliases.json` (exact, corroborated) |
| Terms | "public, US-regulated" | Kalshi developer agreement / data terms not read (429): display rights in a paid product UNVERIFIED | Blocker listed in INTEGRATION_MANIFEST.md |

## 3. The historical-spread (line-shift) transformation

What sharp-1 does (`fair.at_line`): solve the mean of a fixed-variance distribution so P(X > sharp line) = the
de-vigged / midpoint price, read P(X > pick'em line). Shapes: negative binomial (var = m + b m^2) for counts, normal
(var = a m + b m^2, continuity-corrected) for points / yards. When rungs sit on both sides of the line, both readings
are averaged with 1/gap weights. Gaps beyond 1 (counts) / 3 (points, yards) are flagged "far" and left off the default
board.

The fit (`fit_dispersion.py` -> `dispersion.json`, fitted 2026-10-23 19:51Z): games-weighted least squares of each
player-season's sample variance on its mean. NBA: sportsdataverse ESPN box scores 2025-26, rotation players
(>= 20 games, >= 20 min), 256 players / 17,147 games (threes 246 / 16,485). NFL: nflverse weekly stats 2025 (REG+POST):
passing 38 players / 515 games, rushing 46 / 734, receiving 164 / 2,400. **The branch contains no validation of the
fit or of the transformation.** Its README claim that whole-season variance is wider than game-day variance ("the
cautious direction") is untested there.

Our tests (`sports/research/audit_line_shift.py`, results `sports/research/results/line_shift_2026-10-23.json`):

**A. Against Kalshi's own ladders** (no outcomes needed; eligible rungs: two-sided, spread <= 5c, midpoint 0.05-0.95;
NBA 99 points / 75 rebounds / 50 assists / 80 threes ladders with 3+ rungs; NFL 100 receiving-yards, 68 receptions,
51 rushing-yards, 23 passing-yards ladders).

- Between two rungs (what sharp-1 does for an in-between line): mean |predicted - market midpoint| = NBA points 1.6,
  rebounds 1.5, assists 2.0, threes 1.5 probability points (bias +1.2 / -0.7 / -1.1 / -0.9); NFL pass yards 0.8,
  receptions 0.8, receiving yards 0.7, rushing yards 0.7 (bias within +-0.5). Comparable to the quote noise of a
  <= 5c spread.
- One-sided shifts: NBA points +5 / +10 / +15 / +20 points -> +0.6 / +1.7 / +3.8 / +4.6 pp over the market (the fitted
  upper tail is too heavy far from the anchor). NFL receiving yards shifted down 10-40 yards -> +1.1 to +3.6 pp,
  up 15-40 -> -1.2 to -1.4 pp; rushing yards down 15-25 -> +1.8 to +3.6 pp: the **normal shape misses the right
  skew of yardage** (the market prices more mass near zero and in the upper tail). Counts at 1-2 rungs: within +-1.3 pp.
- Variance implied by the ladders, as a multiple of sharp-1's: NBA median 0.95-1.05 by stat (IQR 0.85-1.1); NFL
  passing 0.95, receptions 1.0, receiving yards 1.1 (IQR 1.0-1.5), rushing yards 1.05 (IQR 0.9-1.85). Descriptive only.

**B. Held-out outcomes** (the season before the fit: NBA 2024-25, NFL 2024; ESPN game logs of players on the
2026-10-23 Kalshi boards; centre = mean of the previous 10 games, known before each game).

| Stat | Games | Central 50% covers | Central 80% covers |
|---|---|---|---|
| NBA points | 4,314 | 47.9% | 78.6% |
| NBA rebounds | 4,113 | 48.2% | 77.8% |
| NBA assists | 3,646 | 48.9% | 77.4% |
| NBA threes | 3,563 | 47.0% | 77.9% |
| NFL rushing yards | 172 | 46.5% | 82.0% |
| NFL passing yards | 74 | 48.6% | 74.3% |
| NFL receiving yards | 249 | 51.4% | 88.4% |
| NFL receptions | 210 | 48.6% | 77.1% |
| NFL rushing attempts | 71 | 32.4% | 74.6% |

(71 NBA players, 53 NFL players with 12+ games; naive binomial SE about 0.8 pp for NBA - games within a player are
correlated, so the true uncertainty is larger; NFL samples are small.) Around a trailing mean - a NOISIER centre than a
market price - the NBA shape is about right to slightly narrow. Around a sharper market centre the right variance is
smaller still, so the README's "season variance is wider, therefore cautious" is **not supported for NBA**; the Kalshi
ladders agree that the market's variance is close to sharp-1's (ratio ~1.0).

Conclusions
- Reading the fitted distribution between two market rungs is consistent with the market within quote noise. Reading it
  one-sidedly far from the anchor is biased for NBA points (gaps >= 10) and NFL yards (normal shape, no skew). sharp-1's
  "far" flag excludes most of these from its default board; sharp-2 keeps the flag as reason code
  `line_gap_beyond_fitted_range` (154 of 1,259 sharp-2 forecasts in the scratch run).
- **Calibration of the transformation at real offered lines against outcomes is not established**: no forecast of
  either version has settled. The held-out test checks the shape around a trailing mean, not around the market.
- Not changed in sharp-2 (no tested replacement exists; inventing one would break the factor rule). Model agents:
  skewed / zero-inflated yardage shapes and ladder-fitted distributions are the obvious candidates, to be registered
  and tested chronologically before any comparison.

## 4. Other findings in the data branch

1. **Conditional probabilities only.** sharp-1 logs `chance` = P(side | no push) and `push` separately. The owner
   contract requires unconditional p_over / p_under / p_push. `legacy.py` converts on import (p_side = chance x
   (1 - push)) and keeps the original as `p_*_given_no_push`; sharp-2 stores both natively.
2. **ESPN date ranges are broken.** `scoreboard?dates=A-B` answers HTTP 400 "Failed to get events endpoint." for NBA
   and NFL (2026-10-23, with or without `limit`). sharp-1's `slate` and `settle` use ranges, so every sharp-1
   settlement would have stayed `pending` ("source error"). Fixed in `espn.scoreboard` (day-by-day reads).
3. **Box scores treated as final forever** (`espn.summary` caches a completed game permanently; "a final box score
   never changes"). Corrections exist (2 above). The canonical settlement re-reads finals for 4 days and keeps
   revisions; the legacy sharp-1 `record.settle` path is unchanged (research record).
4. Retries slept a fixed 3 s / 6 s (lock-step retries across workers) - now exponential with jitter (`net.backoff`).
5. `record.MODEL_TAG` comment says "no-vig Pinnacle (or 2+ book consensus)"; on the US server every sharp-1 price is
   Kalshi (Pinnacle refuses US locations; no Odds API key).
6. Player identity in sharp-1 is an exact normalised name + market + date only; the canonical path adds team + both
   event teams + roster membership (`identity.py`) - in the scratch run all 28,085 stored offers resolved to an ESPN
   event and 28,082 to an ESPN athlete; the 3 unresolved (Marquise Brown PHI, Mitch Tinsley CIN on two platforms -
   not on ESPN's roster under that exact name / team that day) stay unresolved and ungradable (no fuzzy join).
7. `pinnacle.py` embeds Pinnacle's public guest key and is geo-refused from the US; it is not wrapped by an adapter
   and not used. Kept untouched as the branch delivered it.
8. The reported 3,656 NBA / 3,247 NFL live forecasts and the "8.6-point sharp-vs-history gap" could not be checked:
   the forecast log is not reachable from here (INTEGRATION_MANIFEST.md, "Live recorded data").
