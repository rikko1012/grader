# Two-way NFL players: position input (2026-11-04)

Status: **code behind `SCOUT_NFL_TWO_WAY` (default off). Not live. No grade, chance, stake or forecast changes while off.**

## Symptom
2026-11-05 board: Travis Hunter (JAX, WR/CB) rec 1.5 / rec_yds 9.5. sm-1 mean 0.05, 80% range [0, 0], P(Under) 1.0. His
game log: 10 played games, 4 over 1.5. sg-2 (`sports/board_view.py sanity_check`, branch `sports/sanity`) now holds the
letter. This note covers the model input.

## Cause (confirmed on the packaged history `history_ds1_2026-10-23.db`)
- nflverse lists Hunter as **CB / DB in all 13 nfl_pg rows** (2025 W1-W7, 2026 W2-W7), including 10 rows with targets.
- `featurize.nfl_frame` keeps only `position IN ('QB','RB','WR','TE','FB')`, so **his whole history is dropped**.
- `forward.upcoming_rows` sends nfl_pg's raw position, so his upcoming row is `CB`. That key has no fitted prior (eff_pos /
  catch_pos / allow_* are QB/RB/WR/TE), so efficiency is NaN, the PMF collapses onto 0 and `model` clamps the mean to 0.05.
  This is the same pattern as the Juszczyk FB bug (2026-10-24, N2).
- nflverse positions are per row. 65 players have more than one listing (Matlock DT/FB, Ricard RB/FB, Beck FB/TE). The
  forward dict uses whichever row SQLite returns last (no ORDER BY).
- Other defensive-listed players with offensive usage in the snapshot: Scott Matlock (LAC, DT/FB, 9 targets) and Marcus
  Jones (NE, CB, 5 targets in 58 games, returner).

## Change (flag on)
1. Row inclusion: a defensive-listed row is kept when **that row itself** shows offense (offense_snaps > 0 or any
   targets / carries / passes). The test is per row, so no future row decides inclusion.
2. Row position (`featurize.nfl_positions`): offensive listings are unchanged (FB -> RB). A defensive-listed row takes the
   player's most frequent offensive listing so far (Matlock -> RB). If the player has never been listed on offense
   (Hunter), the row's role comes from his summed usage so far: passes -> QB, carries > targets -> RB, else WR.
3. Upcoming position: the same rule over the player's last 8 rows, ties to the most recent. It no longer depends on SQLite
   row order. `forward.upcoming_rows` also orders the raw listing by game time when the flag is on.

## Replay with the frozen sm-1 artifact (as of 2026-10-24, 357 players x 5 markets = 1,785 forecasts)
- **1,780 forecasts are bit-identical.** Only 5 move: Hunter rec 0.05 -> 2.46, rec_yds 0.05 -> 28.8, rush_att 0.05 ->
  0.12; Matlock rec 0.05 -> 0.16, rec_yds 0.05 -> 1.06.
- Walk-forward pre-check: weekly as-of replays over every game since 2025-09 of a two-way player (Hunter 12, Matlock 4),
  scored against actual results. Mean absolute error, flag off -> on:

| player | market | n | MAE off | MAE on |
|---|---|---|---|---|
| Hunter | rec | 12 | 2.80 | 2.52 |
| Hunter | rec_yds | 12 | 31.13 | **31.54** |
| Hunter | rush_att | 12 | 0.20 | 0.27 |
| Matlock | rec | 4 | 0.97 | 0.93 |
| Matlock | rec_yds | 4 | 2.97 | 2.53 |

- **The fix removes the floor but overshoots Hunter's 2026 role.** For example, 2026-09-13: projected 4.0 rec / 47 yds,
  actual 1 / 8. On 2026-09-20 and 2026-09-27: projected about 3.5 / 40 and 3.0 / 34, actual 0 / 0. His offensive snaps
  fell from 37-67 a game in 2025 to 0-9 in 2026. sm-1 has no snap-share input, so it carries 2025 volume forward. At a
  9.5 line it would lean Over against a player who has mostly gone under. (Correction 2026-11-05: I first wrote
  "near-certain Over" without computing it. In the walk-forward, A1's P(over) at the reference lines was 0.26-0.47, so
  it is wrong, not certain.)

## Recommendation
- **Keep `SCOUT_NFL_TWO_WAY` off.** Keep the sg-2 hold on two-way players. Under the factor rule, nothing moves grades
  until it passes a walk-forward test. The pre-check says this change alone does not pass for Hunter.
- Turning it on needs:
  (a) a role input, such as the player's recent offense snap share, added as a registry candidate and walk-forward tested
      (it is a new factor, so the rule applies);
  (b) a refit walk-forward (REGISTER section 6 scoring, game-clustered CI) with the flag on, where every non-two-way
      forecast must stay bit-identical and two-way rows must beat flag-off on CRPS and on logloss at the offered line.
- Separately, and safe today: the forward raw-position ORDER BY could ship without the flag. In the replay it changes
  nothing, because SQLite already returns rows in insertion order. It is still behind the flag so this branch moves
  nothing.

## Not checked
- The live forward board on 2026-11-05. The ops admin token is refused on `/api/sports/board`, and an st1 token needs a
  paid member session. To list every live NFL forecast at the 0.05 floor, use `/api/sports/admin/sanity` with an admin
  st1 token: it lists the sg-2 `floor` holds, which are exactly the 0.05 means. Each forecast's saved input row carries
  the `position` that was used.
- The snapshot ends 2026-10-23. Hunter's games after that are not in it.

## Refit walk-forward: pre-registered 2026-11-05, before any result was seen
Script: `sports/research/two_way_walkforward.py`. Data: packaged history `history_ds1_2026-10-23.db`.
- **Window.** Every NFL week from 2025 W10 through the last completed 2026 week in the snapshot. Each week: as_of = first
  kickoff - 1 h (results public by KNOWN_AFTER_H only). The frozen model_config_nfl configuration is refit on everything
  known at as_of (forward.fit's recipe: eligible rows, priors, k, GLM, family params; frozen family and cal-1). The week
  is forecast through upcoming rows carrying nfl_pg's raw position, exactly like the forward job.
- **Upcoming rows.** Every row of the week listed QB/RB/WR/TE/FB, plus every defensive-listed row that shows offense.
- **Arms.** A0 = flag off (today). A1 = SCOUT_NFL_TWO_WAY on. A2 = A1 + `nfl.snap_trend` (registered fr-1 candidate:
  L3 snap share - EW snap share) in rec and rec_yds only.
- **Populations.** P = rows eligible under A0 (model.eligible), all players. T = two-way rows (listing outside
  QB/RB/WR/TE/FB in that row), scored in every arm whatever their eligibility. A0 scores T at its 0.05 floor, which is
  what the board printed.
- **Metrics.** CRPS (primary), MAE, logloss at l_ref = floor(last-10 mean) + 0.5 (study.score_partition's reference
  line). Paired differences use game-clustered bootstrap 95% CIs (evaluate.paired, B = 2000).
- **A1 qualifies as a bug fix** only if both hold: on P, the A1 - A0 CRPS CI upper bound is < +0.5% of A0 mean CRPS in
  every market (no harm), and on T, A1 - A0 CRPS is < 0 with the CI excluding 0 in rec and rec_yds.
- **A2 qualifies** only if all three hold, in rec and rec_yds: on P, A2 - A1 CRPS CI is entirely < 0; on P, the logloss
  CI upper bound is <= 0; on T, A2 - A1 CRPS < 0. Caveat registered now: snap_trend already failed the original tune
  (rec CI [-0.017, +0.0001]). This is a second look on later weeks, so a pass is weaker evidence than a first-look pass.
  It would still need forward confirmation on games after 2026-10-23 before it moves any grade.
- Nothing is promoted by this run. The flag stays off and the frozen config is unchanged either way.

## Walk-forward results (2026-11-05)
Files: `sports/research/results/two_way_wf_v1_preregistered.json` (as registered) and `two_way_wf_v2_amended.json`.
20 weeks (2025 W10 - 2026 W7), weekly refits, about 4 minutes per run.

**Amendments after v1 (post-hoc, so labelled):**
- v1's T contained 304 "two-way" players, mostly offensive linemen. An OT/G/C listing is outside QB-FB and linemen have
  offensive snaps, so the flag pulled them in as "WR". v2 excludes OL and specialist listings
  (`featurize.NFL_NOT_TWO_WAY`). P results are identical in v1 and v2.
- T logloss is invalid: for those rows A0 has no history, so its reference line differs from A1's. v2 reports logloss on
  P only.
- T2 (post-hoc) = two-way rows of players with 2 or more prior games of targets or carries: 9 rows (Hunter 5, Matlock 2,
  Simmons 1, Tonga 1).

**P: A0-eligible rows, 244 games, 1,986 rows per market.** Paired CRPS difference, game-clustered 95% CI:

| market | A1 - A0 | A2 - A1 | A2 - A1 logloss |
|---|---|---|---|
| rec | -0.0001 [-0.0004, +0.0001] | **-0.0128 [-0.0206, -0.0052]** (1.191 -> 1.178) | -0.0076 [-0.0126, -0.0021] |
| rec_yds | +0.0022 [-0.0034, +0.0089] | **-0.128 [-0.210, -0.041]** (16.42 -> 16.29) | -0.0051 [-0.0090, -0.0012] |
| pass_yds, rush_att, rush_yds | 0 (identical) | 0 (snap_trend not added) | - |

**Two-way rows (v2).** T = 48 rows / 42 games; T2 = 9 rows.

| market | pop | A0 floor CRPS | A1 | A2 | A1 - A0 CI |
|---|---|---|---|---|---|
| rec | T2 | 0.887 | 1.085 | 1.089 | [-0.78, +1.03] |
| rec_yds | T2 | 9.44 | 11.55 | 11.81 | [-9.6, +11.3] |

Hunter, 2026 (rec): actual 1, 0, 0, 1, 4. A1 projected 4.0, 3.5, 3.0, 2.5, 2.3. A2 projected 4.3, 3.4, 2.5, 1.8, 1.7.
snap_trend only catches his role drop once 3 low-snap games are in the L3 window.

**Verdict against the pre-registered rules:**
- **A1 (two-way rows) does not qualify.** It passes no-harm on P, but on two-way rows it is not better than the 0.05
  floor (point estimate worse, CI spans 0, only 9 meaningful rows). Most of those games ended at 0 or 1, so the floor
  scores well by accident. The flag stays off; Hunter stays at the floor and the sg-2 hold keeps him off the board.
- **A2 does not qualify as the two-way fix.** It improves nothing on two-way rows.
- **Separate finding: `nfl.snap_trend` in rec / rec_yds improves the whole population on these weeks.** CRPS and logloss
  CIs are entirely below 0 (about -1.1% rec, -0.8% rec_yds CRPS). It is a second look at a candidate that failed the
  original tune (rec CI touched 0), on 2025 W10+ / 2026 weeks that the locked test and test2 had already used. Per the
  factor rule and "forward evidence stays forward", this is a reason to put it on a forward shadow clock, not to switch
  it on. That is an owner decision; nothing has been registered or changed.

**Two-way players: recommended handling.** The data cannot support a model for them (a handful of players, about 9
usable rows in 20 weeks, and roles that change week to week). Keep them held: today's floor plus the sg-2 hold already
does that. If the board should say why, add an explicit sg-2 reason ("two-way player: no validated model") instead of
relying on the floor. Display only, no grade change.
