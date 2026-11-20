# Soccer and tennis model estimates

Owner request, 2026-11-17: connect forecasts and publish grades on the existing
Scout board. This releases model estimates, not a claim of validated accuracy.

`expansion-count-predictive-v1.1` uses up to 20 distinct completed games at the
same player, competition, stat and period; at least five are required. Inputs
must be known before issuance, and the canonical event must still be scheduled.
Missing, conflicting or nonparticipating results never become zeros.

Soccer counts use a Poisson-Gamma predictive distribution, widened when observed
game variance is greater. A half-count prior retains uncertainty after all-zero
results. It models historical rates and variation, not opponent tactics,
projected minutes, injuries or weather. Full-match/regulation equivalence is
restricted to the listed non-knockout league competitions.

Tennis scores use finite categorical distributions with one total pseudogame
spread over score support. Aces, double faults and breaks use the count model.
Full-match histories require matching best-of format. A known current surface
requires matching historical surface. First-set history is never full-match
history. Explicit Challenger/WTA125 singles formats use these official rules,
checked 2026-11-17:

- [ATP 2026 Circuit Regulations](https://www.atptour.com/-/media/files/rulebook/2026/2026-rulebook-chapter-1_atp-circuit-regulations19dec25.pdf)
- [WTA 2026 Rulebook](https://photoresources.wtatennis.com/wta/document/2025/12/24/b300b2a4-8d71-4346-969f-1f6b9399661f/2026-WTA-Rulebook-12-22-2025-.pdf)

The unchanged `gradecore.policy` assigns letters to offered sides using its
existing payout policy and non-push probabilities. Unknown variant payouts do
not receive invented letters or EV. No stakes are assigned by this new model.
Cards identify Model estimates and retain real sample counts and push counts.
The model keeps its most likely side even when the app only offers the other
side. That case is labelled Not offered, with no letter. Availability never
turns an Under prediction into an Over recommendation. Version 1.1 corrects
this selection issue; the forecast distributions are unchanged.
No existing sport's grading rules, models or containment change.

The existing learning worker publishes with `SPORTS_FORECAST=1`; training stays
off. Each sport has its own append-only `forecasts.db`, storing exact offer
revision, identity, inputs, probabilities and model/policy versions. Identical
inputs retain original issuance; changed inputs append a new prediction.
Member reads use a bounded materialized file, with no fitting or history scans.

Completed outcomes append model-evaluation reviews. Corrections append another
review without rewriting a forecast. These are not bookmaker retirement/void
settlements or member-pick tracking. The existing residual experiment remains
shadow-only. Automatic model promotion or recalibration is not enabled.

Database budget: 128 MB per sport. Publication receives one-third of the existing
20-second learning-worker budget, evaluation one-sixth. No new service or paid
resource. Tennis history backfill uses seven-day bounded windows.

Release checks cover distributions/pushes, offered sides, unknown payouts,
input timing, identity/format/surface/period isolation, immutable issuance,
corrections and desktop/phone cards. Passing them does not establish prediction
accuracy. Forward calibration and complete data coverage remain separate work.

## Component models for the other stat types (sg-stats, 2026-11-20)

`expansion-components-v1` (`sports/expansion_stats.py`) gives a model, a letter and a
chance to the soccer and tennis stat types the count model never graded. It builds
each one from component stats the history workers already store, and collects
nothing new. Count-model forecasts keep their exact payloads and ids. A golden test
pins one against origin/main e6e155d. Component-model offers run after every
count-model offer, so the research and publish budgets cannot drop an existing grade.

| Stat (book label) | Model | Walk-forward tier |
|---|---|---|
| Goal + Assist (PP), Goals + Assists (UD) | count predictive on the per-game goals + assists, population prior (m0 0.153, k 8) | failed (A band 69% printed / 63% hit) |
| Cards (UD; 0.5 lines only, where the red-card weight cannot matter) | count on yellow + red, prior (0.131, 16) | close (max band gap 1.3 pts) |
| Shots Assisted (PP) | count on FotMob chances created, prior (0.657, 4) | close (1.8 pts) |
| 1H Goals (UD) | regulation-goals predictive, prior (0.090, 8), binomial thinning at the measured first-half share 0.442 | passed |
| Outfield Fantasy Score (PP) | PP playbook formula per past game, kernel mixture, 0.2 shrink to 8.05 | close (2.3 pts) |
| Goalie Fantasy Score (PP) | 5 + 2 x saves - 2 x conceded (catalog score) + 5 clean sheet, Student-t, 0.4 shrink to 9.36 | failed (3.4 pts) |
| Fantasy Score (PP tennis) | 10 + games won - lost + 3 x sets won - 3 x lost + 0.5 x aces - 0.5 x DFs, kernel mixture | failed (games/sets part only) |
| 2nd Set Total Games (PP tennis) | both players' set-2 games, finite-score Dirichlet | failed |
| 1st Set Total Games Won (PP), Fouls Committed / Fouls Drawn (UD) | the existing count model (label maps only) | (count model) |

- **Tiers.** All three tiers ship at 0 units, each with its letter, its chance and a definite stake reason (display gd-1).
- **Walk-forward data.** Every forecast used only that player's earlier games (latest 20, at least 5). The test covers:
  - 2,749 ESPN match summaries across nine leagues;
  - 1,449 FotMob matches from the top five leagues;
  - ESPN ATP/WTA set scores for Jan 1 – Nov 19, 2026.
- **Tuning and holdout.** Every setting was chosen on matches before Sep 1. Sep 1 – Nov 19 was scored once, after the settings were frozen.
- **Where the evidence is.** Tables are in `cs2_scout_ops/logs/soccer_grades_1120/stats/wf_final/`.
- **FotMob fields.** FotMob lists a stat only when it is non-zero. Across 44,170 played player-games there was no contradiction, and the key shares are in `fotmob_zero_check.json`.
  - An absent FotMob component counts as 0 when FotMob reported that player's match, which is shown by a minutes row.
  - The live crosses and attempted-dribbles histories hold only non-zero games. They are therefore biased toward Over. This release does not change them.
- **Not modelled.** PrizePicks tennis "Matches Won" covers a whole tournament, not one match, so it is not modelled.
- **Unverified scoring rules, all at 0U:** FotMob tackles (won or attempted), the PrizePicks goalie start and clean-sheet conditions, the tennis fantasy formula (Rotowire) and Underdog Cards above 0.5.
