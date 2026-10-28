# CS2 grading check 2026-10-28 - register (written BEFORE any candidate was scored)

Owner directive 2026-10-28: "do a multilevel full check for grading logic across every single board ... level up these
projections and grades". This file fixes the data rules, the levels and the candidate decision rules before scoring.
Branch `grade/cs2-1028`. Nothing here changes live grading; a candidate ships on the branch only if it passes its rule.

## Data (production, read-only, paged admin exports pulled 2026-10-28 ~16:10 UTC; scratchpad `cs2grade1028/`)

- `/admin/picks.json` (16,768 rows, cutoff id 16768) - the pick log: one row per exact line and platform, holding the
  FIRST published grade ("PICKS rule").
- `/admin/learning/p1_units.json` (4,376 units, learning cycle 2026-10-28T16:07:22Z) - the registered P1 product units:
  the latest BOARD forecast >= 30 min before the start on a standard line the platform feed offered; pushes excluded
  ("P1 rule"). Member-typed and goblin / demon lines are not P1 units by construction.
- `/admin/forecasts_page.json` (every forecast row, no inputs) - joins a P1 unit to its forecast (side, grade, units, raw
  chance, projection source). `/admin/fh1.json` - the shadow records (fh-1 / cm-1 / cm-2 arms).
- HLTV Postgres (read-only session, indexed lookups only): team ranks on the match day (tier), the Map 1 share of Maps
  1-2 kills and rounds per map by format (candidate design inputs).
- `/admin/learning.json` (one stored read) - the registered challengers' interim forward scorecards (learning.REVIEW_RULE).

Rules applied to every table:
- rows logged at or after the match start are dropped and counted; owner tests (test = 1) dropped; void / open excluded;
  pushes shown as P and left out of hit / Brier / log loss;
- the chance scored is the chance of the published side (first grade) or of the favoured side of the P1 forecast
  (p > 50% = Over), so "stated" = mean chance of the picked side and "hit" = that side's hit rate;
- intervals: match-grouped bootstrap (1,000 resamples; 600 for the per-level tables) of the hit rate and of the
  (stated - hit) gap; Brier and log loss on the picked side; bias = projection - actual total (same scope);
- cross-platform duplicates: a line offered on both books is one row per platform under the PICKS rule (its own first
  grade each) and one unit per platform under P1; both are counted and reported;
- grade order check: hit rate must not increase from A to B to Lean (bands) and along the letters.

Levels (each with the figures above): overall; platform (Underdog / PrizePicks / member-typed; Thunderpick is an odds
source, not a prop platform, so it appears under "odds source"); stat; scope; stat x scope; grade letter and band;
staked vs 0U; Over vs Under; stat x side; projection source (perf-2 HLTV N0 / perf-cm2 / perf-2-bo3 BO3.gg / legacy);
line type; series format; tier (best HLTV rank of the two teams on the match day, HLTV-keyed matches only); strength gap
(the player's team win chance from the selected market); strength gap x side; LAN / online; overtime in the series;
odds source; model version tag (never pooled across tags for a decision); UTC start day (all and staked only).

The PICKS-rule tables were inspected first (development data for everything below); the P1 join is scored after this
file is committed. Both are reported and labelled.

## Registered challengers (evaluated against their own rules, not mine)

cm-1, cm-2, hs-1, lc-1, fh-1, pl-2, lb-1, ch-9: the dashboard's interim forward scorecard (learning.REVIEW_RULE: decisions
only at 300 / 600 / 1,000 distinct forward matches; log-loss gain >= 0.002 with a family-wise 98.3% interval above 0,
slope checks, slices, replay gap <= 0.01). rm-1 / th-1: AM-1 (research/cs2_accuracy_2y/AMENDMENT_2026-10-23.md) on the
P1 units' saved `cands` arms. vm-1: research/map_veto/props REGISTER section 4 (forward P1 test). LW-1: the final frozen
weights of research/esports_accuracy_2026_10_25 RESULTS section 2, scored on P1 units that started after 2026-10-25
19:38 UTC (its data cut), log loss vs live with a match-grouped interval - a forward reading of a parked candidate, not
a new rule. hs-cm2: the HS Maps 1-2 lines graded on perf-cm2 since 2026-10-21 vs the saved perf-2 numbers of the same
forecasts (MODEL_VERSIONS f663af8917: perf-cm2 forecasts save `perf2`), log loss with a match-grouped interval.

## New candidates (at most 3; each registered here before any number was computed)

Common to all: the test set is the P1 units (registered product rule) joined to their forecasts; walk-forward by UTC start
day (fit on every unit whose start day is earlier than the scored day, score that day, never the same day); the candidate
and live are scored on identical units; the point estimate AND the match-grouped 95% bootstrap interval are reported;
Over / Under balance = |share of the candidate's staked picks that favour Over - the Over hit rate on those units| must
be <= 0.15 (owner: Under lean = correlated upset risk), checked overall and on the worst upset day; calibration and
selection are judged separately; nothing is fitted on the scored day. A candidate that fails ANY rule stays a documented
shadow (no live change); one that passes every rule is wired like the live model (board, card, slips, picks, results)
behind an env off-switch with tests and a MODEL_VERSIONS.md entry, and audit.py is run.

### oc-1 - offered-line chance calibration per cell (family `offered-cal-1`)

- Form: `P(Over) = Phi(a_c + w_c * z)`, z = probit of the live model's raw no-push chance (`probs_raw.p_more_no_push`),
  cell c = stat x scope group: kills Maps 1-2, HS Maps 1-2, kills other scopes, HS other scopes. (a, w) by maximum
  likelihood on the training units of the cell; a cell with fewer than 300 training units uses the identity (a 0, w 1).
  Kills keep the live 22-78% cap after the transform. Projection shown with it: mu = line-cut + sd * Phi^-1(P) (the
  LW-1 display rule), sd unchanged.
- Why registered despite LW-1: LW-1 passed its probability rules and was parked for cutting staked picks ~66-94%. oc-1 adds
  the owner's constraint as a REGISTERED rule instead of discovering it afterwards.
- Pass = ALL of: (1) log-loss gain vs live > 0 with the match-grouped 95% interval above 0; (2) Brier gain > 0;
  (3) staked retention: the candidate's staked count (chance >= 55%) >= 60% of live's staked count on the same units;
  (4) staked hit rate (candidate's own staked set) >= live's (point estimate); (5) the balance check; (6) no stat x scope
  cell with >= 50 matches loses more than 0.005 log loss.
- Expected failure mode, stated now: (3). If (1)-(2) pass and (3) fails the result is reported as "calibration gain,
  selection cost" and the candidate stays a shadow - the owner decides, as with LW-1.

### sd-bo3-1 - spread multiplier for BO3.gg-route projections (family `bo3-spread-1`)

- Cohort: lines whose projection source is `perf-2-bo3` (players HLTV has no history for; PROVISIONAL flag). The PICKS
  inspection showed this cohort at 37-40% hit vs 59-60% stated (about 260-310 rows, 34-35 matches).
- Form: sd' = m * sd on the cohort only, chance recomputed from the same discretised normal (kills cap kept). m from the
  grid {1.0, 1.25, 1.5, 1.75, 2.0, 2.5}, chosen walk-forward by day on the cohort's training units (minimum 80; else m = 1).
  sd is the forecast's logged spread (first-grade factors; the forecast's lambda and raw chance invert the same normal,
  reported as a cross-check).
- Pass: (1) log-loss gain on the cohort > 0 with the match-grouped 95% interval above 0 and at least 30 cohort matches
  scored; (2) Brier gain > 0; (3) balance check on the cohort; (4) the cohort's staked hit rate under the candidate is not
  below live's. A wider spread lowers chances in this cohort; it is registered as the existing "thin data: wider SD" rule
  family applied where the data say it is needed, not as a calibration shrink of the board.
- If the cohort is under 30 scored matches the verdict is "not decidable" (shadow).

### Checked and dropped before registration (no candidate)

- Map 1 projection share: HLTV series since 2025-10-01 show Map 1 = 49.8% (online) / 49.7% (LAN) of Maps 1-2 kills,
  so "Map 1 = half of Maps 1-2" has no bias to correct; the Map 1 / BO1 miss is a spread / selection question (rm-1's
  registered cell).
- BO1 rounds: scrape_queue records no "bo1" format, so a BO1 rounds-per-map correction cannot be built from the stored
  history without a new scrape; not registered.
