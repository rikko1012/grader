# Grader versions (test week from the 2026-10-07 freeze)

Every logged pick and forecast carries `model_version` = the first 10 hex characters of SHA-1 over
`grader.py`, `sim.py`, `cs2_scout.py`, `hltv.py`, in that order, as checked out on the server (LF line endings).
From G8 (2026-10-09) the tag also covers `perf.py`, `perf_live.py`, `scope.py` (appended in that order).
A Windows checkout computes different (CRLF) hashes, so always compute tags from `git show <commit>:<file>`.

**Keep every exact tag when reporting.** Tags can be pooled only inside a "same grading behaviour" group, and only
on the evidence listed. To replay a forecast:
1. Check out the commit for its tag.
2. Feed the forecast's stored `inputs` (in `forecasts.inputs`, from 2026-10-08 00:54 UTC) to `grade_leg`.
3. Use the config snapshot for that tag (`model_versions.json` on the data volume).

| Tag | Commit | First committed (UTC) | Grading-behaviour group | Evidence / note |
|---|---|---|---|---|
| `110c4ed1da` | f9c2072 | 2026-10-07 03:11 | G1 frozen | The freeze: spread x1.2, continuity fix, Over margin 1.0/0.6, rest +4%, LAN/online-only stats |
| `38c4271737` | 535d0b4 | 2026-10-07 03:18 | G1 frozen? | Admin veto backtest only (hltv.py) - not re-tested for identity |
| `2dff48ab19` | 0c3a745 | 2026-10-07 03:20 | G1b | Map predictions calibrated (weight 0 in the grade); BO1 LAN/online via HLTV events - can change the environment for BO1 titles |
| `d408f04949` | 4bfb153 | 2026-10-07 03:31 | G1b | Map 1 display record (display only) |
| `34af63e159` | f105ce0 | 2026-10-07 ~04:31 | G1b | Role lookup added to hltv.py (board/slips only) - same grading |
| `d408f04949` | 5bdc59c | 2026-10-07 04:35 | G1b | hltv.py restored exactly - same tag |
| `6021f88807` | 33c01c7 | 2026-10-07 05:01 | **G2** | BUG FIX (owner-approved): last 10 in tonight's environment reaches back a year - changes inputs |
| `9abfe8cf5f` | 856f12b | 2026-10-07 05:14 | **G3** | BUG FIX completed: last 10 = usable series only - changes inputs |
| `e4a0a0d278` | acc882a | 2026-10-08 00:44 | G3 | Logging only: raw Over/Under/push and fix_v2 comparison - live output identical to 9abfe8cf5f on 6,000 cases |
| `11edec8d68` | d4397e3 | 2026-10-08 00:59 | G3 | Refactor only: Maps 1-3 mixing as named functions (mix_p_more_live / mix_three) - live output identical to the frozen grader, fix_v2 identical to e4a0a0d278, on 6,000 cases each; hand-calculated Maps 1-3 example reproduced exactly |
| `f80baad46e` | 5a3ced6 | 2026-10-08 01:02 | **G4** | BUG FIX (owner): a total exactly on a whole line is a push - the last-10 record's n leaves pushes out (form fight / tape fight). Changes grades on WHOLE lines only; x.5 lines identical to G3 |
| `beb4b5ba0c` | ea74078 | 2026-10-08 01:12 (push time below) | **G5** | Environment revision UNDER EVALUATION: no online +6% when the base rate is fully online stats (the +6% was fitted on all-maps projections). Comparisons on identical inputs: online_none (old) and online_taper (provisional). Changes grades for fully-online players only; tonight's replayable board: Over bets 26 -> 10. Not claimed to improve accuracy |
| `4a9d15b1fa` | b24c4ed | 2026-10-08 22:10 | **G6** | PROJECTION UPGRADE: performance model perf-1 (perf.py) drives Maps 1-2 kills + HS projections and spreads; Map 1 / Maps 1-3 stay on the old pipeline. Old pipeline logged beside every forecast as `legacy`. Evidence before deploy: held-out HLTV months (80% intervals 79-83%), 600 verified board lines (Maps 1-2 projection error 5.08 -> 4.83). Final confirmation: matches from 2026-10-09 on |
| `2391c7f756` | (perf-1 transparency) | 2026-10-08 22:21 | G6 | Labels only: Maps 1-2 grades falling back to the old pipeline (no HLTV data) say so; the kills cap is labelled when it changes the shown chance; raw model chance kept. Grades identical to 4a9d15b1fa |
| `25e9e825c6` | (candidates) | 2026-10-08 22:30 | G6 | Comparison-only candidates added (perf2_hs, perf-1-bo3, then perf_map1 at 22:42 - log only); grades identical to 2391c7f756 |
| `457dc4bfab` | 09bcdd3 | **2026-10-08 22:51 (live)** | **G7** | RELEASE, PROVISIONAL: perf2_hs headshot distribution live on Maps 1-2 HS; perf-1-bo3 live for players HLTV lacks; one forecast per card (simulation's overall chance = the grade's). NOT promoted: Map 1 candidate (tied/worse). Rollback: redeploy commit 8441f0f (tag 25e9e825c6). 59/59 production forecasts replay identically |
| `757f51be04` | c13dc7b | **2026-10-09 (live)** | G8.2 (same grading) | Search speed-up only (hltv.py index): ~7 ms instead of ~110 ms per distinct search. 618/618 production forecasts replay identically. Rollback: 675a017 (405653024c) |
| `405653024c` | d8f7a80 | 2026-10-09 19:28Z | G8.2 (same grading) | Map predictions (context only) prefer the event's verified pool (latest finished BO3 veto at the same event), else an inferred pool labelled as such. Grades identical to 1118ff120e: 199/199 production forecasts replay identically. Rollback: 9c37c7b (1118ff120e) or bf13754 (G8.1) |
| `1118ff120e` | 56bf17a | 2026-10-09 19:15Z | **G8.2** | Consolidated release. GRADING CORRECTNESS: odd-decimal lines (e.g. 30.2) no longer get a push band - continuity cut floor + 0.5 for every non-whole line; whole and x.5 lines identical to G8.1 (replay below). Everything else is not a grading change: impossible-line validation, active map pool for map predictions (context only), search sharing, data-status wording, diagnostics, shadow M1 ("match_m1"), slip load log. Rollback: redeploy bf13754 (G8.1 `83b2b67d6b`, verified to hash to that tag) |
| `83b2b67d6b` | b9637d1 | 2026-10-08 23:35 - 2026-10-09 | **G8.1** | BUG FIX on G8: the HLTV team lookup matched exact names only, so Underdog's "Team Vitality" / "Chicken Coop Esports" found no HLTV opponent and 99 of 866 performance-model forecasts since G6 (11%) used an average opponent (Falcons, Aurora, Spirit, Chicken Coop, SINNERS, FURIA, Vitality, Ground Zero). Now exact-match name variants (leading "Team", trailing Esports / Gaming / Club), also for the namesake tie-break. Changes the opponent effect on those lines only. Rollback: commit 9d747e0 (G8) or 4cbd3cb (G7) |
| `4527c7974b` | 9b8642a | 2026-10-08 23:20 | **G8** | RELEASE: perf-2 (newcomer prior 0.93, HS shrink 25 rounds; spreads re-measured on TRAIN) on Maps 1-2 kills + HS and the BO3.gg fallback (perf-2-bo3, provisional). Held-out HLTV VALID/TEST: kills MAE -0.035/-0.033, HS MAE -0.035/-0.036 (95% intervals below 0); board lines same direction within noise. fix_v2 live on the legacy paths (Map 1, Maps 1-3, Maps 1-2 without HLTV): gap centred on the line, Maps 1-3 push removed after mixing (4 of 177 G7 forecasts change, Map 1, <= 2.9 points). Simulation samples the grade's own distribution. Slip builder batch-3 (shadow). Replay: 177/177 G7 production forecasts identical with fix_v2 off and perf-1 inputs. Rollback: redeploy commit 4cbd3cb (G7 `457dc4bfab`) |
| `c3505e489e` | none found | 2026-10-07 03:13-03:18 | unknown | 8 member grades; no commit on any branch reproduces this tag - not replayable, report separately |
| `f0fdc11ece` | none found | 2026-10-07 03:09-03:12 | unknown | 4 member grades; same - not replayable |
| untagged | before 2026-10-07 ~03:00 | - | pre-freeze | Old grader; the model_version column did not exist yet |

Comparison versions logged beside every forecast (`shadow`), from the same grading run:
- `old_spread`: c 1.55 / 1.30.
- `no_rest_bonus`: rest bonus 0.
- `fix_v2`: gap compression centred on the line; Maps 1-3 Over/Under/push mixed before the push is removed. LIVE from G8;
  from G8 the comparison is `pre_fix_v2` (the old behaviour).
- `perf1_g7` (from G8): the perf-1 projection (G7 parameters and spreads) on the same HLTV inputs - the forward test of perf-2.
- `match_m1` (from G8.2): the shared-match challenger (match_model.py, constants fitted on TRAIN) - shadow only.
- `online_none` / `online_taper` (from G5): old +6%-always behaviour / provisional taper 6% x (1 - online weight); `live_online` records each forecast's online weight and the bonus applied.
- `no_bo3`, `perf1_normal_hs` (from G7): the release without the BO3.gg fallback / with the perf-1 normal HS curve.
- `perf_map1` (from 22:42 Oct 8): the Map 1 candidate.
- `legacy` (from G6): the old projection pipeline on identical inputs.
- `live_raw`: the live model's own Over/Under/push chances before the kills cap and the Over margin.

Still open (not fixed live or in fix_v2):
- The Map 1 / BO1 model (half of the Maps 1-2 projection).
- The displayed chance mixes the model's chance with the Over margin (legacy paths only; the card explains the gap to the simulation).
- Whole-line chances are conditional on no push. That is valid, but they must not be shown as unconditional or
  multiplied into slip all-hit chances without handling pushes.
