# LoL round 2 (claude/lol-r2-1121): pre-registration

**Written 2026-11-21, before any line-level number for the tests below was computed.**

Numbers I had seen before writing this, and which therefore don't count as results:
- the audit summary and its holdout judge output (`holdout/lol/judge_stdout.txt`);
- one series-team favourite check: plain Elo `p_win_elo` 53.5% (n 86) vs Polymarket `team_win_market_p` 63.5% (n 63); on the 67 with both, Elo 49.3% vs market 62.7%, correlation 0.24;
- the count of assists rows that are playable under current rules: 1 in all 18 days.

The audit holdout days (11-05..11-14) are development data for this round. I read the judge output, so they are not untouched. They are reported separately and never called a fresh holdout.

## Data
Group p1 rows in `data/lol/train.csv` + `data/lol/holdout.csv`: the latest forecast at least 30 min before the start, 18 UTC days from 10-17 to 11-14.

**Base L (saved forecasts).** The saved `lambda` / `nb_size` / `p_over` / `p_under` on every row: lol-1 on 10-17..18, lol-1f to 11-08, lpf-1 on 11-14. All 18 days.

**Base P (live model replay).** lpf-1 (variant W, live offset, 600 rounds; the harness from `lol/lpf_eval.py`, unchanged) is refit on 2026-10-01 and 2026-11-01. Each refit sees only days before it. It runs on the read-only Nov 5 copy `lol_full.db` and gives each settled line in that copy the lpf-1 mixture, its per-game won and lost means, and its per-game win chance p.
- Coverage is 10-17..11-05. The copy has no history after 11-05, so 11-06..11-08 and 11-14 can't be replayed.
- Base P is the PRIMARY base for anything that would ship, because lpf-1 is the live kills/assists model.
- Base L results are secondary. lol-1f is only the live fallback path.
- The replay is joined to the audit rows by `unit_key`.

## Outcomes and universes (as in `holdout/lol/judge.py`)
- Outcome is recomputed on the candidate's side from `over_hit` / `push`. Hit = W/(W+L).
- The base row set is rows with a saved grade. The os-1 one-sided rows shown with no grade sit outside it.
- `offered` is recomputed for the candidate's side from `ud_*_status` / `pp_*_offered`.
- **PRIMARY universe: `playable_current`.** The shown side is offered and the line is not one-sided. This is the member-record population (lol/center/learning.py `in_record`).
- Secondary universes: `all_graded`, `letters`, `playable_current_letters`.

## Gates (from judge.py)
A candidate passes only if all three hold:
- **Effect.** One of:
  - (a) the Holm-adjusted p < 0.05 with delta > 0 on the primary universe. The statistic is the mean over days of (hit_cand_d - hit_base_d), tested by an exact two-sided sign-flip randomisation over days (complete enumeration when there are 20 days or fewer);
  - (b) days under 53% on the primary universe fall by at least 2, with pooled delta >= 0.
- **Volume.** The candidate's primary-universe W+L count is at least 60% of base. Every projection candidate keeps every row graded by construction. Rows can only leave the playable universe by flipping to an unoffered side, and that loss is reported.
- **No new Under skew.** The Over share of W/L rows on the primary universe is no more than 5 points below base.

Reporting:
- Day-grouped and series-grouped bootstrap 95% CIs (2,000 draws, seed 7) on the pooled delta.
- W-L-P, rows, unique player-markets and series.
- Daily table, days under 53%, Over share and Over / Under hit.
- Split by model_version, and by original-train vs audit-holdout days.

**Holm family (5 tests, fixed now):**

| id | base | primary universe | what |
|---|---|---|---|
| T2a | P | playable_current | lpf-1 with market win chance |
| T2b | L | playable_current | lol-1f strength factor with market win chance |
| T4a | P | playable_current | lpf-1 stat x scope offset, walk-forward |
| T4b | L | playable_current | saved-lambda stat x scope offset, walk-forward |
| T1b | P and L, reported for both; L is the gate (18 days) | n/a | best playable line per player-market vs all playable rows |

## Candidates (exact forms)

**T2a mkt-p (0 fitted parameters).**
- Source: the Polymarket match-winner price `team_win_market_p`, the no-margin midpoint saved with the forecast and known before lock.
- When a row's team has a price `m` for the series, convert it to a per-game win chance `p_g` by solving the series-win formula for the series' best_of:
  - BO1: `p_g = m`;
  - BO3: `m = p^2 (3 - 2p)`;
  - BO5: `m = p^3 (10 - 15p + 6p^2)`;
  - bisection, clipped to [0.02, 0.98].
- Rebuild the lpf-1 W-variant mixture with `p_g` in place of the win model's p. The decider chance is the replay win model's own p3 mapping applied to `p_g`. The per-game won and lost means and the sizes are unchanged.
- Chance at the line = the mixture's P(over) / P(under). Side = the larger. Grade = the live bands.
- Rows with no price: unchanged.

**T2b mkt-sf (0 fitted parameters).**
- For the saved lol-1f rows with `m` and `p_win_elo`, recompute the strength factor with `p_g` in place of `p_win_elo`.
  - `sf = ((p R + 1 - p) / D)^EW`, where D is backed out of the saved `strength_factor` and `p_win_elo`, and R, EW come from `lol/model.py` (`PRIORS['win_ratio']`, `PARAMS[stat]['ew']`).
- `lambda' = lambda * sf_new / sf_old`; NB with the saved `nb_size`; side, grade and bands as above.
- Rows with no price, an `sf` of 1.0 / missing, or lpf-1 / lol-1 rows: unchanged.

**T4a / T4b scope offset (walk-forward).**
- For each stat x scope cell, on test day d: `c = log(sum actual / sum mean)` over the unique player-markets settled on days before d.
  - Use the base's mean: the lpf-1 mixture mean for T4a, the saved lambda for T4b.
  - Take the first line per pm_key so alternates don't multiply weight.
- The cell needs at least 30 earlier player-markets; otherwise c = 0.
- Apply `mean' = mean * exp(c)`: every lpf-1 mixture component mean is scaled for T4a; the NB mean with the saved size for T4b.
- Side and grade as above.

**T1b best line (0 fitted parameters, a selection rule, not a grade change).**
- Among the lines of one player-market (both books, all alternates) whose shown side is offered and that are not one-sided, mark as "best" the line with the highest chance on its shown side. Ties go to the standard line, then to the lower unit_key.
- Compare the hit of best-marked rows with the hit of all playable_current rows by paired day differences (same test and gates).
- If it passes, the board may mark the best line. Nothing is regraded.

**Track 3 (assists over-projection when the team loses).** Info-only, declared now:
- Under current rules there is one playable assists row in 18 days, so no hit-rate test at playable lines is possible.
- I report lpf-1 replay and saved-lambda assists residuals by `post_team_won_series` as a diagnostic only. Those are outcome columns.

**Track 1 (record counts only playable sides).** An accounting fix, not a grade change, so no walk-forward.
- I trace every consumer of the LoL settled record and give before/after counts.

## What passing means
- A passer on Base P ships behind a named env flag, default ON with rollback `=0`.
- Its forecasts carry a model_version suffix.
- A passer only on Base L applies only to the lol-1f fallback path and is reported as such.
- A failure stays documented. Any code for it sits behind a flag that defaults OFF.
