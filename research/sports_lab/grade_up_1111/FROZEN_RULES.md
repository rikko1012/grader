# gu-1111 sports (NBA / NFL) grading-logic upgrade study - definitions FROZEN before any arm is scored

Owner request 2026-11-11: "full grading logic upgrade testing new factors ... on previous projections versus how it
ended to see what will improve grades". Research only. Nothing here changes a live grade, chance, side or stake.
This file is committed BEFORE any arm below is fitted or scored at an offered line. Anything changed after the first
score is labelled post-hoc in the report.

What had been seen before this file was written (so nobody mistakes it for a blind start):
- the Nov 9 reports (sports_accuracy, line_gap_nba / nfl) and their tables: Over picks hit less than Under picks, the
  stated chance runs above the hit rate, lg-1 a1 was a near miss, NFL d1 passed everything but the balance check;
- the column names and row counts of the local datasets. No arm in this file had been scored on any row.

## 0. Rows, baseline, scoring (all arms)

- **Row** = one (game, player, stat, book, line): the last saved sm-1 forecast at least 30 min before the start, settled
  win / loss at that real offered line. Pushes and voids (did not play) are dropped. Rows are de-duplicated on that key
  and the builder asserts 0 duplicates. The prop line is only the threshold a forecast is scored at; it is never an
  input of any projection arm.
- **Live side** = the side with the higher saved raw sm-1 chance.
- **Live letter** = sports-gp-1 band of the pc-1 PRINTED chance: q = sigmoid(b * logit(raw chosen-side chance)), b from
  the committed `sports/model/artifacts/chance_cal_pc1.json` (NBA 0.280821, NFL 0.157121, intercept 0);
  A q >= 0.60, B q >= 0.5559, C q >= 0.5345, else Lean. "Graded" = C and above. "Top" = A + B.
  Secondary (information, comparable with the Nov 9 tables): the same bands on the RAW chance.
- **Hit rate** = wins / (wins + losses). CIs: 95%, game-grouped bootstrap, B = 2000, seed 1111, differences resampled
  as paired ratios per game draw.
- **Days.** NBA slate day = (start - 10 h) UTC date. NFL game-day = (start - 10 h) UTC date; NFL week = the Thursday-
  to-Monday block.
- The stated-vs-hit gap, log loss and Brier are information only. No arm shrinks a chance toward 50%.

### Acceptance (owner rule 2026-11-09 / 2026-11-11)

Projection and side arms (P*, N*, G7):
- H1 hit rate on ALL props up (point estimate > 0) AND on graded props up with the CI lower bound > 0.
- H2 Over picks and Under picks separately not lower (point estimate), on all props and on graded props.
- H3 graded count >= baseline.
- H4 graded Over share no further from 50% than baseline.

Letter arms (G1-G5; sides never change, so all-props hit rate cannot move):
- H1 graded (C+) hit rate not lower AND top (A+B) hit rate not lower, and at least one of the two up with CI lower
  bound > 0.
- H2 Over picks and Under picks separately not lower (point estimate) at the graded and at the top tier.
- H3 graded count >= baseline AND top count >= baseline. No prop loses its side (a Lean still shows Over / Under).
- H4 Over share of graded picks and of top picks no further from 50% than baseline.

Pass = H1-H4 on the test rows. A pass makes an arm eligible for a REGISTERED SHADOW / default-off flag and an owner
preview - never a live switch. The number of arms tested is reported with every pass (about 1 arm in 20 clears a 95%
CI by chance). Per-market results are reported; a per-market pass with a pooled fail is "shadow candidate only".

## 1. Grade-layer arms (letters only, on the saved chances; NBA and NFL)

Fixed arms (no fitted number, scored on every row and separately on the walk-forward test days):
- **G1 over-bar.** Every Over pick is demoted one letter step (A -> B, B -> C, C -> Lean). Unders unchanged.
- **G1b over-floor.** Over picks at C become Lean; other letters unchanged.
- **G2 over-bar + under-step.** G1, and every Under pick is promoted one step (Lean -> C only when its printed chance
  is >= 0.52; C -> B; B -> A).
- **G5 standard-only.** Rows on a line that is not the app's standard pick'em line (alt / goblin / demon / priced
  multiplier; flag = the feed's break-even field is null) are demoted one step. Scored only where the flag exists
  (NBA window S, NFL feed rows from 2026-11-06 06:15Z); elsewhere reported as not measurable.

Walk-forward arms (expanding by day; everything learned from EARLIER days only; burn-in = first 5 NBA slate days /
first NFL week; test = the later days):
- **G3 cell-step.** Cell = (stat, side). On day d, from all earlier decided rows: h_cell = hit rate of the cell's
  rows (all props), h_all = hit rate of all rows. n_cell >= 150 and h_cell - h_all >= +3.0 pts -> every pick in the
  cell is promoted one step (Lean -> C only when printed >= 0.52). n_cell >= 150 and h_cell - h_all <= -3.0 pts ->
  demoted one step. Otherwise unchanged.
- **G4 evidence letters (distance-from-line buckets).** Bucket = side x raw chosen-side chance in [.50,.55), [.55,.60),
  [.60,.65), [.65,.70), [.70,.80), [.80,1]. (The raw chance is the model's distance from the line in probability
  units.) A bucket with >= 150 earlier decided rows gets the sports-gp-1 band of its own earlier hit rate
  (A >= 60%, B >= 55.59%, C >= 53.45%, else Lean); smaller buckets keep the live letter.

Descriptive only (no rule; sample too thin or flag missing - reported with the n each would need):
- **G6 cap-3 cutoff.** Hit rate of Scout's side by the saved market price of that side (mc-1 consensus q): < 0.45,
  0.45-0.55, 0.55-0.65, >= 0.65. cap-3 hides sides at >= 0.65 on book / exchange lines.
- Hit rate by letter x side x stat, by raw-chance bucket, PrizePicks vs Underdog, standard vs non-standard.

Re-tests of frozen Nov 9 rules, unchanged (their own RULES files apply as written):
- **G7a** lg-1 a1 (NBA: flip to the side this app's line is soft for when the PP/UD gap is >= max(1.0, 10% of the
  line) and Scout disagrees) on slates after 2026-11-08 US, alone and pooled with the Nov 9 test days.
- **G7b** NFL d1 (letter up when the line moved Scout's way, down when it moved against) on NFL games after
  2026-11-09 01:20Z. If no new final NFL game with saved standard-line forecasts exists, it is reported as "no new
  data" and not re-scored.

## 2. Projection arms (one multiplier on the sm-1 mean; box scores only)

Mechanism (the registered rm-1 / nrm-1 form, sacc_1109.cand): the out-of-sample sm-1 PMF of a player-game is moved by
r = exp(clip(gamma . z, -2, 2)) (x -> r x on the grid). gamma is fitted by Poisson deviance with offset log(sm-1
mean), ridge 1e-2, on box-score outcomes only. Every non-constant z column is standardised with the TRAINING rows'
mean and sd; unknown values = 0 after standardising. Walk-forward: the gamma used for a window is fitted on every
EARLIER window (NBA: monthly dev windows 2024-10 .. 2026-06 then the final windows; NFL: monthly dev windows then the
weekly 2026 windows). No book line, settled prop or same-game box value enters a fit.

At the offered lines the arm's P(Over) is read from the moved PMF and transported onto the saved live chance:
logit(arm_live) = logit(saved raw) + logit(arm offline) - logit(base offline). Side, letter and scoring as section 0.
Test rows: NBA 2026-10-24 .. the last local slate (the Oct 24 - Nov 5 part was used for the Nov 9 DIAGNOSIS - no arm
here was scored on it; Nov 6 on is fresh). NFL: weeks 8-9 + the Nov 5 TNF game (the only NFL rows with a local
offline PMF). An NBA arm must also not be lower on the fresh part (graded hit diff >= 0) to pass.

Game spread / total: the pre-game consensus stored in the local history (ESPN pickcenter, else the median of non-live
ESPN core-odds providers). Caveat written now: in history this is the last pre-game number, not the number at the
forecast time; the forward job would read the games store at forecast time.

### NBA arms (markets: points, rebounds, assists, threes unless stated)

| arm | owner factor | z columns (frame = proj_nba_full.data frame; all pre-game) |
|---|---|---|
| P1 min2 | separate minutes model | z = log((m_hat + 1) / (sm-1 EW minutes + 1)). m_hat = ridge (alpha 1, standardised) regression of actual minutes, rows with prior_n >= 3, fitted on earlier windows only, inputs: min_ew3, min_ew10, min_ew30, min_last, min_l3, starter_lag, starter_r10, abs_spread, abs_spread x starter_lag, out_min / 48, out_starters, b2b, pf36_ew10 (player fouls per 36 min, EW half-life 10 prior games - foul-trouble rate), rot_len5 (mean number of teammates with >= 10 min in the team's 5 prior games - coach rotation length), season_n <= 3, missed_since_last > 0 |
| P2 opp_pos | opponent positional defence by stat | log(A / L): A = the stat per minute allowed by tonight's opponent to the player's position group (G / F / C) over the opponent's 20 prior games; L = the mean of A over all teams that day. 0 when the opponent has < 5 prior games |
| P3 pace_total | pace x total | log(total_line / 225), log(((t_pace_ew10 + o_pace_ew10) / 2) / 100), their product |
| P4 with_without | usage with specific teammates out (on/off splits) | sum over the team's top-3 usage teammates (usage_pg_ew10 before the game) who MISSED THE TEAM'S PREVIOUS GAME (known pre-game; same-day scratches are not used) of the shrunk log ratio: log(rate without q / rate with q) x n_without / (n_without + 10), rate = the player's stat per game in prior games of the same season and the one before, with / without q playing |
| P5 home | home / away | home |
| P6 alt_travel | altitude / travel | away game at DEN or UTA; km travelled since the team's previous game / 1000 (city coordinates by team, 0 for a season opener); b2b and > 500 km |
| P7 rest_load | days rest x minutes load | b2b; b2b x min_ew10 / 30; games_last_7d >= 4; that x min_ew10 / 30; days_rest >= 3. (Age: no birth date in any local source - not testable, stated in the report.) |
| P8 shot_profile | shot profile vs opponent 3-pt defence (points, threes) | (r_fg3a_ew40 / r_fga_ew40 - 0.40) x d3; d3 = log(allow_fg3a_pm / its training mean); log(allow_pts_pm / its training mean). (Rim defence needs shot-location data - not in any local source, not testable.) |
| P9 role_change | recent role change | starter_lag - starter_r10; log((min_l3 + 1) / (min_ew10 + 1)); log((min_last + 1) / (min_ew10 + 1)); team_changed; missed_since_last > 0 |
| P10 garbage | garbage-time / blowout risk from the spread | abs_spread / 10; that x starter_lag; team_spread / 10; that x starter_lag |
| P11 all | every column above together | |

### NFL arms (opp = targets for rec / rec_yds, carries for rush_att / rush_yds, pass_att for pass_yds; frame = proj_nfl_full)

| arm | owner factor | z columns |
|---|---|---|
| N1 snap_route | snap share and route participation trends | l3_snap - ew_offense_pct; lag1_offense_pct - ew_offense_pct; log((e2_opp + 1) / (e8_opp + 1)); log((lag1_opp + 1) / (ew_opp + 1)). (True routes run are not in any local source; snaps and targets are the proxy.) |
| N2 mates_out | target / carry share with teammates out | registry column mates_out_tshare (rec, rec_yds, pass_yds) / mates_out_carries (rush_att, rush_yds), sports.features.compute |
| N3 qb_change | QB change | qb_change_lag |
| N4 opp_def | opponent pass / rush defence by position | rec: allow_targets, allow_rec, oa_targets, oa_catch. rec_yds: allow_rec_yds, allow_targets, oa_targets, oa_ypr. rush_att: allow_carries, oa_carries. rush_yds: allow_rush_yds, allow_carries, oa_ypc. pass_yds: allow_pass_yds, allow_pass_att, oa_pass_att, oa_ypcmp |
| N5 script | game script from spread / total | team_spread; implied_total; abs(spread_home); team_spread x RB; team_spread x QB |
| N6 weather | wind / temperature for outdoor games | wind_x; temp_x; dome; (wind >= 15 and not dome). History weather = the game's recorded weather (nflverse); forward would be the NWS kickoff forecast |
| N7 surface | surface | NOT TESTABLE: no surface column in any local source |
| N8 redzone | red-zone role | ew_rz_targets; ew_rz_carries; e2_rz_targets - e8_rz_targets; e2_rz_carries - e8_rz_carries |
| N9 pace | pace / plays per game | plays_l6; opp_plays_l6; npass_l6 |
| N10 promoted | backup promoted | lag1_offense_pct - ew_offense_pct >= 0.25; back_from_absence |
| N11 new_team | player on a new team | team_change; n_season <= 2; their product |
| N12 all | every column above together | |

If a listed column turns out not to exist in the frame, that column is dropped and named in the report (no
replacement is invented after scoring starts).

## 3. What is reported

Per league: factor / arm, n, base hit, new hit, CI of the difference, Over / Under, graded count, Over share,
pass / fail with the failed checks. Per market the same. For every near miss (point estimate up, CI touching 0): the
number of games it would need for the CI to clear 0 at the observed effect and spread (n_needed = n_games x
(half-width / effect)^2).
