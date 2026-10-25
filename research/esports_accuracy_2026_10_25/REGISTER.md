# Esports accuracy register, 2026-10-25 (written BEFORE any number of the tests below is computed)

Owner mandate 2026-10-25: "fix esports and improve hit-rates - find a fix; if you need to go over every single game, do it."
This file fixes every candidate, data rule, metric and pass rule before scoring. Nothing here changes live grading; a
candidate can only be proposed for release if it passes the rule in section 6, and the coordinator ships.

Honesty note: every period used below has been inspected by earlier studies (FINDINGS.md / LD1_RESULT.md on
esports/hitrate, ACCURACY_2026-10-16.md, REALLINE_ARCHIVE_2026-10-16.md, research/cs2_accuracy_2y). In particular the
slope of (actual - line) on (projection - line) is already known (kills +0.11, HS +0.42). The walk-forward below is
therefore chronological development evidence (fit on earlier days only, score the next day), not a holdout. What it
protects against is look-ahead inside the fit, not forking paths across studies; to limit those, the candidates, groups,
feature list, penalties and pass rules are all fixed here and run once.

## 1. Data

- **CS2 units:** the P1 export (`/admin/learning/p1_units.json`, paged) at the time of the run: one unit per player x
  canonical match x stat x scope, the latest standard board forecast logged >= 30 min before the start, pushes excluded,
  older-key duplicates counted once (the P1 rule, EVALUATION.md section 8). Joined by forecast id to the forecast row
  (`/admin/forecasts_page.json`, paged) for `probs_raw`, `p_win`, `units`, `grade`, `shadow`, `quote`, and to the saved
  inputs (`inputs=1`, one id per request) for the features in section 3.
- **live chance:** P1 `p` (published P(Over | no push), after caps). **live z:** `Phi^-1` of the raw no-push Over chance
  `probs_raw.p_more_no_push` (before the kills 22-78% cap), clipped to [0.001, 0.999]; if `probs_raw` is missing, z uses
  `p`.
- **LoL units:** `lol_forecasts` latest standard / balanced forecast >= 30 min before the series start, latest
  settlement, graded only, one unit per series x player x stat x scope (latest forecast id wins across platforms), the
  same rule as FINDINGS.md. live chance = p_over / (p_over + p_under); z = Phi^-1 of it.
- **Days:** UTC date of the match start. Walk-forward: for each score day d, fit on every unit with start date < d
  (expanding window), score day d. CS2 burn-in: the first three P1 days (2026-10-11..13) are training only; every
  later day is scored. LoL burn-in: the first two series days are training only.
- **Data audit** (direction 6) is reported separately and never filters units unless a bug is proven; a proven bug is
  fixed in code with a test, and the affected units are reported both ways.

## 2. Candidates

All candidates keep the live projection's information and add the book line as information; none uses the outcome of
the scored day. "Group" g is stat x scope: CS2 {kills, hs} x {1, 12, 123}; LoL {kills, assists, deaths} x {scope}.

- **LW-1 (line-informed projection, learned weight).** `p = Phi(a_g + w_g * z)`.
  - Equivalent projection for a normal live distribution N(lam, sd) at continuity cut L*: `mu = L* + sd * a_g + w_g (lam - L*)`.
    So w_g is the share of the projection's distance from the line that the history shows is real, and a_g is the
    group's level offset at the line (both learned, both can be 0 or 1).
  - Fit: maximum likelihood on the binary decided outcome (Over vs Under) of the training units, per group.
  - Groups with fewer than 300 training units use the stat-level pooled fit (all scopes of that stat); fewer than 300
    for the stat as well -> the live chance (a = 0, w = 1).
  - Kills keep the live 22-78% cap after the transform. Chances are not rounded for scoring.
- **LW-2 (LW-1 + pre-match features beyond the line).** `p = Phi(a_g + w_g z + sum_k b_k x_k + sum_k c_k x_k z)`,
  features x_k from section 3, standardised on the training units, ridge L2 penalty 25 (in log-likelihood units) on
  every b_k and c_k, none on a_g, w_g. One fit across groups for b, c (a_g, w_g per group as LW-1). Missing feature ->
  0 after standardisation.
- **LW-OT (LW-1 on the rounds-aware chance).** LW-1 with z taken from the rm-1 shadow chance (rounds mixture with
  overtime, saved pre-match in the P1 unit's `cands`) instead of the live chance, scored only on units where rm-1 was
  saved, against LW-1 and live on the same units. Training uses the same units-with-rm-1 rule.
- **LW-LoL.** LW-1 on the LoL units (groups stat x scope; pooled-stat fallback below 150 training units, live below
  150 for the stat). No cap.

## 3. Features for LW-2 (fixed list; each read only from the saved pre-match forecast / inputs)

| # | feature | definition |
|---|---|---|
| f1 | team market strength | `p_win` (series win chance saved with the forecast) - 0.5; missing -> 0 |
| f2 | thin history | 1 if `perf.history_rounds_weighted` < 150 else 0 (interaction with z enters as c) |
| f3 | BO3.gg route | 1 if the projection source is not HLTV (`perf.version` contains "bo3" or no perf block) |
| f4 | book | 1 if Underdog, 0 if PrizePicks / other |
| f5 | line level | (line - training mean line of the group) / training sd of the group's line |
| f6 | LAN tonight | 1 if `environment.tonight` == "lan" |
| f7 | roster risk | 1 if `is_standin` or `roster_confirmed` is false |
| f8 | last 10 vs line | `recent.overs / recent.n` - 0.5 (0 if n < 5) |
| f9 | forecast lead time | log(1 + hours between logging and start) |
| f10 | opponent rank | log(opp_rank) (missing -> training median) |
| f11 | AWPer (HS only) | 1 if the saved gate source / `sniping` marks the player as an AWPer; 0 for kills rows |

Each enters as b_k x_k and c_k x_k z (22 coefficients). The individual coefficients are reported but no single feature
is promoted or dropped after the run; LW-2 passes or fails as one model.

## 4. Metrics (scored days only; paired, same units for every arm)

- **Probability:** mean log-loss gain and Brier gain vs live (+ = candidate better), and vs the 50% line baseline;
  95% intervals by match-clustered bootstrap (B = 2000, canonical match = cluster, seed fixed).
- **Calibration:** favoured-side reliability in bins 50-55 / 55-57 / 57-63 / 63-68 / 68+; Over-favoured and
  Under-favoured gaps (mean chance - hit rate) with intervals; calibration slope of the outcome on logit(p).
- **Staked set (P1 rule, existing thresholds unchanged):** a unit is staked when `grader.units_for_p(favoured chance) > 0`
  (55%+) and no chance-independent stake rule held it at 0. The chance-independent rule is read from live: a unit
  whose live favoured chance was >= 55% but logged 0 units stays 0 for every arm. Report for live and each candidate:
  staked units, W-L, hit rate with 95% interval, mean printed chance, Over / Under mix, the 57%+ set, units-weighted
  simulated even-money result; and the difference candidate - live in hit rate (bootstrap over matches, each
  replicate recomputes both sets).
- **Correlated-Under check:** staked Under share, and the staked record in matches with an overtime map / where the
  team the Under backs against won (as available), for live and candidates.
- **Subsets (reading):** stat x scope, Over vs Under, favourite vs underdog, thin vs thick, |z| band, book, week.

## 5. Selection test SEL-1 (direction 4; separate from probability accuracy)

On top of LW-1's staked set: on score day d, a cell (stat x scope x favoured side, 12 cells) is staked only if LW-1's
staked picks of that cell on days < d number >= 40 and hit >= 52.4% (the -110 break-even). Reported against LW-1's
staked set and live's: hit rate, count, CI of the difference. A pass needs the hit-rate difference vs LW-1 > 0 with the
95% interval excluding 0; otherwise it is reported and not used.

## 6. Pass rule (fixed)

A candidate (LW-1, LW-2, LW-OT, LW-LoL) PASSES only if, on its scored days:
1. log-loss gain vs live > 0 with the 95% match-clustered interval excluding 0, AND Brier gain point > 0;
2. calibration not worse: the absolute favoured-side gap (mean printed - hit) on the 55%+ set is not larger than
   live's (point estimate);
3. staked-set hit rate not worse than live's (point estimate of candidate - live >= 0);
4. replay: the candidate recomputed from saved inputs reproduces its scored chances exactly (the release code is
   checked against the research script on every scored unit).
LW-2 additionally must beat LW-1 by rule 1 (interval of LW-2 vs LW-1 excluding 0) to be preferred over LW-1.
A pass makes a candidate the release proposal: a new model version, a MODEL_VERSIONS entry, tests, a 700-concurrent load
check and a board/card consistency audit. A fail leaves live unchanged. Every result, failures included, is reported.
