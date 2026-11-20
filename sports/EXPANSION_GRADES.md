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

## sg-scope (2026-11-20): extra-time scope per fixture, kept extra-time history, history inputs

Owner mandate 2026-11-20: every offered soccer / tennis prop gets a grade from a model. This part removes
`extra_time_scope_unverified` at the root and widens history inputs where a walk-forward passed.

- **Scope per fixture** (`sports/expansion_scope.py`). ESPN scoreboards now carry each fixture's stage
  (`season.type` id + slug), leg and final period / clock into the catalog. The history job keeps the finished
  matches' stage outcomes in `stages.json` (bounded 1.5 MB, survives the 100-day catalog). A full-game line in a
  competition outside `REGULATION_LEAGUES` equals the regulation total when the fixture is a first leg, or its
  stage (or the same stage name in earlier seasons) has >= 2 drawn single-leg full-time finals and no extra-time /
  penalty finish. Otherwise the fixture CAN reach extra time and the platform rule decides:
  PrizePicks counts extra time (not shootouts), Underdog counts regulation + stoppage only.
  `REGULATION_LEAGUES` lines are untouched (byte-identical forecasts: 4,026 of 4,026 graded grid cases).
- **Walk-forward** (pre-registered, `cs2_scout_ops/logs/soccer_grades_1120/scope/`): 4,444 fixtures classified
  "no extra time" from earlier finals only across eng.1, ita.1, aus.1, mex.1, usa.1 and UCL, 0 went to extra time.
  For PrizePicks lines on fixtures that can reach extra time, a regulation + 30-minute mixture lost to the
  regulation predictive (log loss +0.0063, 95% CI +0.0050..+0.0079; box totals incl. extra time came in 7.4% under
  the regulation projection). So `ET_SHARE = 0`: those lines use the regulation predictive and settle on the
  extra-time box total (`settle_scopes`), else the 90-minute total.
- **Extra-time history is kept**: ESPN summaries of AET / penalty finals are stored as scope `full_game`
  (`includes_overtime` yes / unknown; shootout kicks are not in ESPN's player totals). Regulation readers skip them.
- **History inputs** (`PROMOTED_INPUTS`, same pre-registered criterion: ECE within 0.03 of the graded control at the
  upper 95% bound, |bias| <= 0.03): aus.1 ESPN stats (ECE 0.0117 vs control 0.0114, diff CI -0.0041..+0.0047);
  FotMob passes / tackles / crosses / clearances / dribbles for mex.1, aus.1, usa.1 and UCL (ECE 0.0104-0.0197 vs
  eng.1 FotMob 0.0153, every diff CI upper bound <= 0.0153). Newer competitions get 4 more "deficit" summaries a run (never-read finals of
  teams with an offered fixture, ESPN only, +6 requests max), read once each.
- **Tennis tour format** (`expansion_formats.tour_rule`): from ESPN's Grand Slam flag, WTA singles and ATP
  non-major singles (ATP Finals included) are best of three; men's majors and Next Gen are not inferred. Used for
  full-match lines and their history only; set-scope lines keep their bytes.
- **Ops**: `learning-status.json published_model.pending_by` counts each pending reason by
  competition | stat | platform (top 60).
- Stakes: unchanged (this model assigns none); new paths carry `scope_rule` / `settlement_scope` / `format_rule`.
