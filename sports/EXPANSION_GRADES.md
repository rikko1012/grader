# Soccer and tennis model estimates

Owner request, 2026-11-17: connect forecasts and publish grades on the existing
Scout board. This releases model estimates, not a claim of validated accuracy.

`expansion-count-predictive-v1` uses up to 20 distinct completed games at the
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
