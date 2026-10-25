# Esports accuracy results, 2026-10-25 (registered in REGISTER.md, commit d0a6237, before any number below)

Data: P1 export at the 2026-10-25T19:38:29Z learning cycle (3,650 settled CS2 units, starts Oct 11-25, 207 matches), every
unit joined to its forecast row and saved inputs (3,646 / 3,650 inputs found). LoL: 388 units / 20 series (lol_forecasts
latest standard / balanced forecast >= 30 min before the start, latest settlement). Walk-forward by UTC start day: CS2
scored days Oct 14-25 (3,276 units / 188 matches; Oct 11-13 training only), LoL scored days Oct 22-25. All intervals are
match-clustered bootstraps (B = 2000 for probability, 1000 for hit rates). + = candidate better. Development evidence
on inspected data (see REGISTER.md honesty note). Scripts: `scripts/`, outputs: `outputs/`.

## 1. Decisions

| candidate | rule 1: log loss vs live (95%) / Brier | rule 2: 55%+ gap (printed - hit) | rule 3: staked hit vs live | decision |
|---|---|---|---|---|
| **LW-1** line-informed probability (learned w per stat x scope) | **+0.0067 [+0.0011, +0.0128]** / +0.0032 [+0.0005, +0.0060] | 3.7 pts vs live 7.4 | **56.3% (238-185, 423) vs 51.3% (648-614, 1,262): +4.9 [+0.1, +9.3]** | **PASS (rules 1-3); rule 4 = release replay** |
| LW-2 LW-1 + 11 pre-match features (22 coefs) | -0.0020 [-0.0115, +0.0075]; vs LW-1 **-0.0087 [-0.0157, -0.0016]** | 8.7 pts | 51.8% (1,418) vs 51.3%: +0.4 [-4.0, +4.7] | **FAIL** (worse than LW-1: the features overfit) |
| LW-OT LW-1 on the rm-1 rounds/overtime chance | not testable: **0** P1 units carry an eligible rm-1 record (rm-1's cells are Map 1 / Maps 1-3; none offered since Oct 23) | - | - | **NOT TESTABLE** |
| LW-LoL | -0.0074 [-0.0466, +0.0254] (349 units / 18 series; only Oct 25 had a fitted weight) | 12.3 vs 11.3 | 52.2% vs 53.4% | **FAIL** |
| SEL-1 cell selection on LW-1 | (selection test) | - | 56.4% (225) vs LW-1 56.3%: +0.2 [-5.0, +5.7] | **FAIL** (not used); 208 of 225 picks were Unders |

With the settlement correction of section 4 (19 units re-scored on HLTV's final scoreboard) LW-1 stays at
+0.0067 [+0.0011, +0.0126] log loss and 56.1% vs 51.4% staked (+4.7 [-0.0, +9.1]; the rule-3 point is unchanged).

## 2. LW-1 in detail

- **What it is.** `P(Over) = Phi(a_g + w_g * z)` with z = probit of the live model's raw no-push chance. Equivalent to the
  projection `mu = L* + sd * a_g + w_g (lam - L*)`: the line is kept, plus the share w of the projection's distance from it
  that history shows to be real. Kills keep the 22-78% cap.
- **Fitted weights move with the data** (walk-forward, by day): kills Maps 1-2 w 0.05 -> 0.24 (Oct 18) -> 0.07 (Oct 25);
  HS Maps 1-2 w 0.39 -> 0.72 (Oct 18) -> 0.41; intercepts -0.09..+0.03. Oct 14 had < 300 training units per stat, so LW-1 =
  live that day (139 of LW-1's 423 staked units; same picks, same 59.7%).
- **Final fit for release** (every P1 unit Oct 11-25, corrected settlements, data hash eb5822b13949; registered fallback rule):
  kills Maps 1-2 a +0.0338, w +0.0430 (1,731 units); HS Maps 1-2 a -0.0038, w +0.3963 (1,602); kills Map 1 / Maps 1-3 (stat
  pool, 1,901) a +0.0169, w -0.0608; HS Map 1 / Maps 1-3 (stat pool, 1,749) a -0.0094, w +0.2718.
- **vs the 50% line baseline: -0.0001 [-0.0043, +0.0046].** LW-1 stops live from being worse than the line (live vs 50%:
  -0.0068 [-0.0152, +0.0014]); it does not beat the line. Calibration slope 0.19 (live) -> 0.57 (LW-1).
- **By week:** W1 Oct 14-17 +0.0033 [-0.0072, +0.0144], W2 Oct 18-24 +0.0084 [+0.0016, +0.0162], W3 Oct 25 +0.0100
  [-0.0066, +0.0269]. Staked W1 58.0% (224) vs live 54.9% (499); W2 54.8% (197) vs 48.8% (689); W3 0-2 vs 51.4% (74).
- **By group** (log loss gain; staked live -> LW-1): kills Maps 1-2 +0.0065 [-0.0014, +0.0143], 649 at 50.8% -> 76 at 56.6%;
  HS Maps 1-2 +0.0013 [-0.0038, +0.0061], 503 at 55.1% -> 327 at 57.5%; kills Map 1 +0.0372 [+0.0037, +0.0744], 61 at 39.3%
  -> 0; HS Map 1 +0.0303 [+0.0055, +0.0625], 42 at 35.7% -> 17 at 29.4%.
- **Frozen-weight reading (the release procedure: fit <= Oct 17, frozen, scored Oct 18-25; not a registered test):**
  log loss +0.0071 [+0.0017, +0.0131], Brier +0.0034 [+0.0006, +0.0061]; staked 50.6% (159-155, 314) vs live 49.0%
  (374-389, 763): +1.6 [-3.7, +6.8]; 55%+ gap 8.3 vs 10.0. The probability gain holds; the staked hit-rate gain is NOT
  established when the weights are frozen.

## 3. What it does to the product (read before shipping)

- **Far fewer staked picks.** Walk-forward: 423 staked vs 1,262 live over 12 days (-66%); kills Maps 1-2 drops from 649 to
  76. With the final weights (kills w 0.04, HS w 0.40) an in-sample replay of all 15 days stakes only **92 picks** (59-33;
  in-sample, not evidence) against live's 1,434: about 6 a day instead of about 95. Kills essentially stop being staked
  (a kills Maps 1-2 chance reaches 55% only when the live raw chance is about 98%+); HS Maps 1-2 needs a live raw chance of about 63%+.
- **Under-heavy.** LW-1's walk-forward staked set was 73% Unders (307 / 423; live 53%); the frozen reading 87%. In matches
  with an overtime map LW-1's staked set went 6-25 (all Unders); without one 59.2% (392). This is the correlated-Under
  exposure the owner warned about (memory: under-lean-correlated-risk). With the final weights the in-sample mix is 46 / 46.
- **Owner rules in tension.** It is a validated probability correction (owner 2026-10-16: acceptable even if it lowers
  chances), but it does not beat the line baseline and it removes most B grades (memory: grades-accuracy-not-fewer). This is
  an owner decision, like the cm-2 override.

## 4. Data audit (direction 6) - every settled P1 unit vs HLTV's own map rows

| check | units |
|---|---:|
| settled total = the graded player's own HLTV total over the scope's maps | 3,357 |
| **settled total != HLTV total (one match: hltv:2399658, map 2 = mapstatsid 241953)** | **19** |
| graded HLTV id not on the scoreboard (known namesakes I1 Lucky 13843 / I2 brain 5615; fixed 10-21 / 10-23) | 6 |
| opponent id not in the HLTV match (LP -> BARCA dated rename, intended) | 10 |
| no HLTV player id (BO3.gg route) / non-HLTV match key | 116 / 152 |
| duplicate player units | 0 |
| stand-in flagged / roster not confirmed | 0 / 150 |

**Bug found and fixed: settlement on a partial map scoreboard.** Map 241953 (2026-10-17, a 29-round overtime map) was stored
from a page captured at 24 rounds; all 19 units of that match settled 1-8 kills / headshots short (3 outcomes flip:
36 -> 38, 28 -> 36, 17 -> 20). Fix (hltv._series_rows marks a map whose stored round history is shorter than its final score
as partial; settle_totals / settle_by_day / settle_scope and picklog.scope_result wait instead of settling on it) with
tests/test_partial_map_settle.py. The HLTV DB currently has no partial map (0 of 5,255 maps since Sep 1), so the guard
protects future captures. The 19 existing picks need the admin re-check (picklog.recheck_with_hltv) to correct their
stored settlement - a production write left to the coordinator.

No benched-player, stale-data, duplicate or legacy-grade problem was found beyond the known, fixed identity cases.

## 5. Direction 3 (kill-share factors) - status

Already tested on the 2-year HLTV history with registered chronological tests and rejected (FACTOR_AUDIT.md rounds 1-7):
team kill share, form windows (last 10 / hot form / half-lives), H2H, opponent pace and roster change, stage / tier,
LAN/online (used), schedule load, IGL (shadow), CT/T, map specifics and veto-1 predicted maps (vm-1 shadow: no gain).
Here the remaining pre-match information was tested where it matters - beyond the book line - as LW-2 (team market
strength, thin history, BO3.gg route, book, line level, LAN, roster risk, last 10 vs line, lead time, opponent rank,
AWPer): it is significantly WORSE than LW-1 out of sample. AWP role history is still blocked by data (dated snapshots only
since 2026-10-09). No new projection factor qualifies.

## 6. Release candidate (rule 4) and recommendation

- lw1.py + grader.py (lw-1 block after the model's raw chance, before the kills cap), CONFIG["lw1"], rollback LW1_LIVE=0.
  Experiments that must stay frozen grade with lw1=None (perf_shadow.grade_arm, picklog.shadow_alts, comparison g7,
  perf_market, lambda_with_pred_maps); fh-1's published check and lc-1's input use the model numbers kept in grade.lw1.
  The logged forecast lambda stays the model projection. Tests: tests/test_lw1.py (12), suite runs the model path by
  default (conftest marker lw1).
- Replay (scripts/replay_lw1.py): research transform vs release max |diff| 1.1e-16 (3,361 units); 500 / 500 latest
  production forecasts reproduce exactly with lw-1 off and equal the transform of their logged raw chance with lw-1 on.
- Impact on those 500 forecasts: staked 187 -> 10 (kills 106 -> 0, HS 81 -> 10; 9 / 10 Unders); B or better 118 -> 0.
  Cost +0.05 ms per grade.
- **Recommendation:** lw-1 meets the registered pass rules on probability and calibration, and the walk-forward staked hit
  rate rose (+4.9 points, interval just above 0), but the frozen-weight reading does not establish a hit-rate gain and
  the release would leave members about 6 staked picks a day, mostly HS Unders. It is honest about how little the
  projection adds beyond the book line; it does not find a new edge. Shipping it is an owner decision (it removes most B
  grades, memory grades-accuracy-not-fewer). If not shipped, keep pl-2 (the forward shadow of the same idea) running to
  its 300-match review. The partial-map settlement fix is independent and safe to ship on its own.
- Checks on the release code: full suite 1,623 passed + the one updated identity assertion (test_prior_smooth: the
  frozen comparisons now get the live CONFIG minus lw-1) = all green; 700-concurrent load (scripts/load700.py): 1,226
  requests, 0 5xx, p95 1.67 s (the 87 409s are the harness's intended one-team slip refusals); consistency audit on 500
  regraded production forecasts (scripts/audit_lw1.py): 0 side-vs-projection, units-vs-grade, chart or simulation
  mismatches (the 44 "wording" hits are lower-case "line-informed" in Lean reasons and over-only lines).
