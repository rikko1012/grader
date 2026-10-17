# LoL projection (lol-1): chronological evaluation

## Protocol (written 2026-10-17 before the TEST numbers were seen)

- **Data:** the LoL Esports feed history in the default leagues (`lolesports.MAJOR`: Worlds, MSI, First Stand, LCK, LPL,
  LEC, LCS, LCP, CBLOL, LTA N/S, EWC, Demacia Cup, LCK CL, EMEA Masters, LFL, Prime League, PCS, VCS, LJL), every
  completed match from 2025-01-01 to 2026-10-16. Leaguepedia was not configured, so the LPL is effectively absent
  (the feed carries 7 LPL matches).
- **Unit:** one (match, player). Totals: Map 1 = game 1; Maps 1-2 = games 1 + 2; Maps 1-3 = games 1-3 (only series where
  the player played every game of the scope; Maps 1-3 in a best-of-3 therefore means "map 3 was played").
- **No look-ahead:** a projection uses only games on days before the match day (the same rule live); Elo uses only
  earlier results.
- **TRAIN:** matches 2025-03-01 to 2026-03-31 (January-February 2025 is history only). Everything is chosen here: role
  priors, the stat-in-wins / stat-in-losses ratio, the form (rate / share), HL, K0, KO, KT and the match-strength
  weight EW (grid, lowest per-game squared error), then the negative-binomial size per (stat, scope) by maximum
  likelihood.
- **TEST:** matches 2026-04-01 to 2026-10-16, never used by the fit, reported once.
- **Reported:** MAE and bias against two baselines (the role average; the player's last 10 games), the same model
  without match strength, log-likelihood against Poisson, how often the 80% range holds the result, and calibration
  of P(over) at the x.5 line nearest the projection. Kills Maps 1-2 is also broken down by role and league group.
- **Pass criteria for showing the projection as a "model estimate" (kills, Maps 1-2, TEST):** MAE below both baselines;
  |bias| under 0.3 kills; the 80% range holds 76-84% of results; every calibration bin with 100+ series within 5
  points. This is **not** a betting rule. The calibration lines here are synthetic (the x.5 nearest the projection),
  and the CS2 work showed real offered lines behave differently. Grades and stakes need the real-line check first
  (`lines_archive.jsonl` collects those lines from now on).

## Amendment A1 (2026-10-17, owner decision, written before any TEST number was seen)

The owner chose to cover the current props with about 8 months of recent history and to fetch the full history in a
separate run. Until that run exists, the fit and the results below use:

- **Data:** LoL Esports feed games from **2026-02-17** on only, for history and for targets (`--history-from
  2026-02-17`): the same window the live database holds after a default `sync`.
- **TRAIN:** matches 2026-03-17 to 2026-07-31 (17 Feb to 16 Mar is history only). **TEST:** matches 2026-08-01 to
  2026-10-16.
- Everything else (grid, metrics, pass criteria) as above.

The full-history protocol (TRAIN 2025-03-01 to 2026-03-31, TEST 2026-04-01 to 2026-10-16, Leaguepedia included) is
for the separate full-history run, which replaces this fit when it is done.

```
python -m lol.backtest --history-from 2026-02-17 --train-from 2026-03-17 --test-from 2026-08-01 --test-to 2026-10-17 --write
```

## Amendment A2 (2026-10-17, measurement correction found in code review, before any TEST number was seen)

Counts are whole numbers, so the "80%" range (`model.interval`) is the narrowest whole-number range holding *at least*
80% of the model's probability, often 85-90%. Judging it against a flat 80% would call a well-calibrated model
under-confident. The coverage criterion becomes: **the share of results inside the range is within 4 points of the
probability the model itself puts on that range** (`cover80_nominal`, averaged over the same series). Everything else
is unchanged. The same review also fixed, before results: the last-10 baseline taking a day's games in kill order, and
a game held by both sources being counted twice.

## Results

(filled in from the command above once the recent history is synced)
