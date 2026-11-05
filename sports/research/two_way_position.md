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
  9.5 line it would print a near-certain Over: the same failure as before, on the other side.

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
