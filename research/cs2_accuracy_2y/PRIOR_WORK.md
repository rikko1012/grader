# Phase 0 - digest of prior CS2 accuracy work (written 2026-10-22, read-only)

Purpose: everything the 2-year investigation must build on and must NOT repeat. Sources were read in full from this branch
(`research/cs2-accuracy-2y`, HEAD model tag computed from git = **`f663af8917`**), the live repo's `claude-handoffs/`, the
Codex 2026-10-18 evidence folder, and the owner-rule memory notes
(`C:\Users\tikic\.claude\projects\C--Users-tikic-OneDrive-Documents-cs2-scout\memory\`). Nothing was run against production.
Numbers are quoted as their source states them (95% match-grouped intervals unless noted; "+" = candidate better unless noted).

Source key used below: MV = `MODEL_VERSIONS.md`, CH = `CHALLENGERS.md`, FA = `FACTOR_AUDIT.md`, EV = `EVALUATION.md`,
P2 = `EVALUATION_P2_PROPOSAL.md`, A15 = `ACCURACY_2026-10-15.md`, A16 = `ACCURACY_2026-10-16.md`,
RA = `REALLINE_ARCHIVE_2026-10-16.md`, CK = `CHECKPOINT_2026-10-14_corrected.md` (original kept beside it), HC = `HLTV_COVERAGE.md`,
AU = `AUDIT_2026-10-18.txt`, ML = `ML_EVAL.md`, FW = `FRAMEWORK_v7.2.md`, UP = `UPGRADE_2026-10-22.txt`,
H18 = `C:\Users\tikic\cs2_scout\claude-handoffs\accuracy-2026-10-18\` (ACCURACY_REPORT.txt, CLAUDE_ACCURACY_UPGRADE.txt,
CLAUDE_FINISH_FIXES_2026-10-18.txt, CLAUDE_REPORT_2026-10-18.md; full evidence folder
`C:\Users\tikic\Documents\Codex\2026-10-06\we-re-trying-to-create-the-2\outputs\accuracy-study-2026-10-18\`, PROTOCOL.txt there),
R/... = `research/...` in this repo, MEM = memory note of that name.

---

## 1. The live CS2 model today (tag `f663af8917`, live since 2026-10-21 19:18 UTC, main 0e66c01)

Later commits (QoL efaffab, slips studio-4 4e77d48/f6d1530, PR #11 LoL) touch no hashed file: HEAD still hashes to
`f663af8917`. Tag = first 10 hex of SHA-1 over grader.py, sim.py, cs2_scout.py, hltv.py, perf.py, perf_live.py, scope.py,
hltv_store.py, market_odds.py, thunderpick.py, pick_verdict.py, bo3.py, roles.py, polymarket.py, oddspapi.py, cm1.py, cm2.py,
cm2_model.json, read with `git show <commit>:<file>` (LF) - `picklog._model_version`; MV header.

### 1.1 Routing by line type (cs2_scout.`_perf_input` -> `_perf_input_once` -> `_cm2_live`; grader.`grade_leg`)

| Line | Projection (version string) | History read | Spread / distribution | Fallbacks |
|---|---|---|---|---|
| **Kills Maps 1-2**, player in HLTV | **cm-2** (`perf-cm2`): Poisson-GLM mean `exp(3.3498 + Σ coef·(x-mean)/std)` on 14 features (cm2_model.json, fit through 2026-09-30, alpha 1e-4) | cm-2 tables = FULL HLTV history (all tiers, backfill incl.), corrected: complete 5v5 scoreboards only (every table incl. Elo), unknown historical env = NaN (overall history only), rows in [D-420, D), weight exp(-age/120), weekly Monday opponent-adjustment refits on earlier maps, Elo from all series since 2025-09-01 at start of match day; rebuilt every 2 h in the learning child | discretised normal, sd = **1.4757 x sqrt(max(λ,1))**; cuts: x.5 line -> floor+0.5; whole line -> ±0.5 with push band; P(Over \| no push); **kills cap 22-78%** | cm-2 ineligible (target env unknown, player/team/opponent not in HLTV, no tables, no player maps in window, any cm-2 error) -> **perf-2 on N0** with `cm2_live.ineligible` saved. HLTV DB error during cm-2 lookup = transient (re-raised; keep last grade) |
| **HS Maps 1-2**, player in HLTV | **hs-cm2** (`perf-cm2`, since f663af8917): cm-2 kills λ x `cm1.hs_ratio` on cm-2 tables ((hk + 25·0.5106)/(hkk + 25)) | cm-2 tables | HS \| kills beta-binomial (kappa 100) averaged over the cm-2 kills pmf (kills c 1.4757, conditioned ≥0); **no cap on HS** | as kills (perf-2 N0 HS) |
| **Map 1** kills / HS (HLTV) | **perf-2** (perf.py PARAMS): rate x opp x 21.7413 x 1 map | **N0 pin** (perf_live INPUT_REV "N0", rule `n0-r4`) | kills c 1.442; HS via beta-binomial with kills c 1.442 (HS sd field 1.241); kills cap | perf-2-bo3 / legacy below |
| **Maps 1-3** kills / HS (HLTV) | perf-2, 3 full maps (maps 1,2,3; BO5 maps 4-5 never; BO3 2-0 voids only when BO3 format confirmed - AU item 3) | N0 | c 1.457 / HS beta-binomial on kills c 1.457 | as above. Never tested on HLTV-settled offered lines (only ~20 props, H18) |
| Any scope, **player not in HLTV / no HLTV map in 420 d** | **perf-2-bo3** (provisional): same perf.project on BO3.gg map rows; env rule `bo3-env-2` (unknown stays unknown, c36104d079) | BO3.gg recent maps; opponent from HLTV N0 opp maps (else average opponent) | as perf-2 | cm-2 never applies (fallback returns before `_cm2_live`) |
| perf unavailable for a non-transient reason | **legacy** v7.x pipeline (rates, form, H2H 0.25, last-10 0.3, regional 0.4, market win/loss factor, Over margin 1.0/0.6, gap-trust 2.5/1.5, spread c 1.86 / 1.56) | BO3.gg + HLTV swing | normal; SD widening x1.10 per risk, max x1.25 | transient HLTV/Thunderpick blip -> board keeps last grade or hides (never legacy) |

perf-2 (perf.py `PARAMS`): hl 120 d, k0 300, ko 300, ke 800 (env blend), base_r 21.7413, pop 0.66442 kills/round,
prior_m 0.93 (shrink target 0.93 x pop), k_hs 25, pop_hs 0.5106, window 420 d, history strictly before the match day,
unknown env in overall rate only. Spreads (TRAIN residuals): kills 12 1.457 / 1 1.442 / 123 1.457; HS 12 1.264 / 1 1.241 /
123 1.264. HS_KAPPA 100. perf-2 takes **no market input** (Thunderpick/Polymarket only move the stake cap and shown win chance).
N0 = known original set (matches first fetched before 2026-10-12 21:27 UTC; 8,597) + later-fetched matches dated ≥2026-10-12
with both teams in the stored daily VRS top-200 snapshot (late_in 45 on 10-14, 241 at the 10-21 pull); fail-closed
inclusion list, 10-min re-evaluation (HC "Controlled rollback"; perf_live.py header).

### 1.2 Probability -> grade -> stake (grader.py)
- Grades from the unrounded chance: A+ ≥80, A ≥74, A- ≥68, B+ ≥63, B ≥57, B- ≥55, else Lean ("C"). Display gd-1 shows the model
  letter even at 0U (grade_display.py, outside the hash; logged grade unchanged, 0U logged LEAN).
- Units (`units_for_p`): 3U ≥80, 2U ≥74, 1.5U ≥68, 1U ≥63, 0.5U ≥55, else 0.
- Stake gates (never change a perf-line chance): <20 Maps 1-2 -> cap 1.5U; <5 -> 0U; market quality not HARD (SOFT incl. any
  Thunderpick/OddsPapi bookmaker price, or NONE) -> cap 1.5U; `standin_for` -> cap 1U; `is_standin` / not on listed team -> 0U;
  rust ≥29 d -> cap 1U (15-28 d flag only); AWPer on HS (sniping >40) -> 0U; FORM FIGHT -> 0U (perf lines: probability
  unchanged); TAPE FIGHT -> cap 0.5U; SCOUT -> 0U; a side the feed does not offer -> 0U/unavailable; same-match team cap
  3U total (server `SAME_RESULT_CAP`, extra picks 0U).
- Kills cap 22-78% applies to every kills line (perf, cm-2 included); Over margin, gap-trust, confidence halving: legacy only.
- One distribution per card: sim.py samples the grade's own distribution; match scripts are heuristic orderings (display).

### 1.3 Version tags on the current lineage and rollback flags (MV)
| Tag | Live (UTC) | Change |
|---|---|---|
| `c36104d079` | 2026-10-18 22:49 | bo3-env-2 (BO3.gg unknown env no longer online) on top of 807a289529 (audit: NULL env unknown, 10-player / 5-player completeness, verified-history-1 cache) |
| `8404c86d3c` | 2026-10-20 ~01:14 | cm-2 live for kills Maps 1-2 (**owner override** of the 300-match review); 23/259 lines fell back (opponent id) |
| `794a775f51` | 2026-10-20 ~01:45 | cm-2 opponent by shadow rule; cm-2 HLTV errors transient |
| **`f663af8917`** | 2026-10-21 19:18 | hs-cm2 live (passed registered rule) + 65 audit fixes |

Rollback: `CM2_LIVE=0` (all cm-2 -> perf-2), `CM2_HS_LIVE=0` (HS only). Every perf-cm2 forecast saves `perf2` (perf-2's
λ, sd, rate, opp factor) and `cm2_live` (features, inputs, tables sha, artifact sha). Every forecast records `data_rev`
(`...;perf_input=N0/n0-r4:...`), `odds_policy`, `mapping_rev`, `inputs` (exact grader inputs, from 2026-10-08 00:54),
`probs_raw` (from 2026-10-12), `quote` (from 2026-10-15: side prices, offered sides from 02:44, other-platform anchors from 03:26).
Odds policy tp-first-2 / odds-source-3; Thunderpick quote life 75 min since `9cfcbd217a` (owner 2026-10-16).
Mixed revisions: only perf-2 reads N0; last-10/H2H, stake checks, settlement, display read full tables; cm-2 reads full history.

---

## 2. Every experiment / challenger / factor tested

Status words: REJ = rejected; SH = shadow (logged, forward clock running); LIVE; OVR = live by owner override; INV = invalid run.
"Dev" = development (inspected). Splits unless stated: HLTV TRAIN < 2026-07-01, VALID < 2026-08-13, TEST 08-13..10-08.

### 2.1 Legacy pipeline era (v7.x, BO3.gg data, Sep 27 - Oct 7 2026) - FW, grader.py CONFIG comments
| id | date | hypothesis / change | data | result | decision |
|---|---|---|---|---|---|
| v7.3 calibration | 09-28 | SD c kills 1.55 / HS 1.30, kills cap 78%, 22 rounds/map, role 0%, map-KPR blend n/(n+12), thin-data rule, FORM FIGHT | 45 players x last 10 matches walk-forward, 5,850+5,850 lines | printed≈hit by band | live (legacy) |
| v7.4.1 win/loss factor | 09-28 | measured map-outcome factors 1.043/0.969/0.871 replace 1.05/0.85 + blowout | 450 series | calibration kept | live (legacy) |
| v7.5 s1 H2H / last-10 blends | 09-28 | owner formulas into projection | 900 walk-forward cases | worse (6.07->6.10) -> weight 0; later owner set last10 0.5/0.7/0.3, H2H 0.25 on member lines | legacy only |
| v7.5 s2 3/6/12-month blend | 09-28 | KPR blend 0.5/0.3/0.2 | 900 cases | kills 6.07->6.01, HS 3.76->3.69 | live (legacy) |
| v7.5 s3 match simulation | 09-28 | 20k-run sim in the grade | 900 cases | not better, thin tails | display only |
| v7.5 s4a/4b team-kill share, opening kills | 09-28 | multipliers | 900 cases, CV by team | worse, sign flips | weight 0 |
| v7.5 s4c trade kills | 09-28 | kills x (1-0.129(tr/0.1283-1)) | 900 cases | kills 6.007->5.976 | legacy kills only |
| over/under margin, gap trust, dog_drop, regional weight, B- band, rest +4%, online +6%, map HS mods, x1.2 spread | 10-02..10-07 | legacy tuning on member/real lines Oct 2-6 (1,040-1,193 lines) | development (day-held-out) | see grader CONFIG comments | legacy only; x1.2 spread reverted once by owner (MEM grades-accuracy-not-fewer) |

### 2.2 Performance-model rebuild and factor audit (HLTV history, Oct 8-13) - FA, MV
| id | date | hypothesis | data / split | metric -> result | decision / doc |
|---|---|---|---|---|---|
| **perf-1** | 10-08 | time-weighted shrunk KPR x env blend x opponent x 21.74 rounds | HLTV Sep 2025-Jun 2026 tune; held-out months; 600 verified board lines | 80% intervals 79-83%; board Maps 1-2 MAE 5.08->4.83 | LIVE G6 4a9d15b1fa (Maps 1-2); now shadow `perf1_g7` |
| perf2_hs | 10-08 | HS \| kills beta-binomial kappa 100 | TRAIN; board HS lines | log-loss 0.6666->0.6592 (-0.0148..-0.0004) | LIVE G7 457dc4bfab |
| perf-1-bo3 / perf-2-bo3 | 10-08 | perf model on BO3.gg maps for HLTV-missing players | 414 board lines (proxy) | MAE 5.08->4.91, range crosses 0; CK: removing fallback worse kills 0.032 (-0.008..+0.073), 8 matches | LIVE (provisional); shadow `no_bo3` |
| **perf-2** | 10-09 | newcomer prior 0.93, k_hs 25, spreads re-measured | HLTV VALID/TEST | kills MAE -0.035 (-0.061..-0.010) / -0.033 (-0.046..-0.021); HS -0.035/-0.036 | LIVE G8 4527c7974b; still live for Map 1, Maps 1-3, fallbacks |
| perf_map1 | 10-08 | own Map 1 model (21.77 rounds, map-1 history, BO1 blend) | TRAIN/VALID | tied (MAE 3.19 vs 3.14) | not promoted; shadow; Map 1 moved to perf-2 on 186 lines tie (MAE 3.135 vs 3.137), 10-10 |
| Maps 1-3 perf | 10-11 | three full maps on perf-2 | 7,958 player-series that went 3 maps (Jul-Oct) | kills bias -0.59, 80% 81.9%; HS -0.44 | LIVE ea44921fd9 |
| Elo team strength (G3) | 10-08/09 | Elo win-scenario term | TRAIN fit; re-tuned VALID | b=0 TRAIN; TEST -0.002 (-0.005..+0.001) | REJ (twice) |
| team-level prior; k0 600/1000 | 10-09 | thin-history shrink | VALID | within noise / worse | REJ |
| expected-rounds matchup (Elo gap, map lengths, LAN) | 10-09 | rounds model | HLTV | rounds MAE 5.07->5.06 | REJ |
| G4 maps / veto (oracle) | 10-09 | map mean rounds + per-map rate | TEST | -0.001 (-0.002..+0.000) | REJ (that estimator only) |
| G2 eco/rating family | 10-09 | eco-adj kills, DPR, KAST, rating, swing | TEST | kills -0.002 (-0.008..+0.003); HS -0.014 then R2 on perf-2 +0.0013 | REJ |
| G5 schedule load | 10-09 | days since last map, maps last 7/30 d | TEST | +0.001 (-0.003..+0.005) | REJ |
| G1 AWP share (proxy HS/kill<0.38) | 10-09 | rate/HS/spread | TEST | -0.002 (-0.004..+0.001) | REJ; true AWP share unavailable historically (daily snapshots from 10-09) |
| CT/T sides (R2-ctt) | 10-09 | starting-side gap | 92,982 side rows Jul-Oct | stable part ~0.04 kills | REJ (bound negligible) |
| R2-maps map-pair spread (oracle) | 10-09 | per-map-pair spread multiplier | TEST | log -0.0002 (-0.0009..+0.0006) | REJ |
| R2-igl IGL role | 10-09 | IGL residual | today's roles applied to history (proxy) | IGLs ~0.3 kills lower all periods; TEST -0.15 (-0.48..+0.22) | **shadow candidate, needs dated roles** (not built) |
| R2-moves / underdog line movement | 10-09 | result follows move | 144-170 moved lines / 31-36 matches | 45-46% follow move | REJ as tested; retest at 150+ matches |
| match_m1 (shared-match model) | 10-09 | one simulated match drives all props (NegBin kills, won/lost factors) | TEST 13,255 lines | marginal props tie/slightly worse (MAE +0.016); CK P1 615/35 +0.0033 (-0.0074..+0.0138); A15 656 lines +0.0037 | SH (`match_m1`) |
| prior_smooth | 10-09 | m(r)=1-0.07·800/(800+r) | TRAIN choice; VALID/TEST | ties (MAE +0.003..+0.009) | SH; **rows before 2026-10-14 22:53:13 INV** (graded by legacy) |
| online_none / online_taper (G5) | 10-08 | online +6% double count | board | taper changes 0 P1 matches | SH; +6% removed for fully-online |
| HS calibration check | 10-09 | HS Overs overstated? | board HS Oct 3-8 (169 lines/51 m); HLTV held-out 2,965 | Overs -8.7 pts (-17.9..+1.0) n.s.; HLTV HS -0.25 | no rebuild; forward bands from 10-09 23:40 |
| **Round 3** (R3-share, form, h2h, oppagg, blowout, stage, opproster, sameday, group) | 10-10 | 8 single factors + ridge group on perf-2 relative residual | 42,587 series-players; TEST 13,255 / 1,343 m | every TEST dMAE within ±0.001 / CI across 0 or worse (stage -0.0005, qualifier rows 0.15-0.46 lower, VALID opposite) | all REJ (FA round 3); stage "re-test with more data" |
| **Round 4** k0 / hl / hot-shrink / ko / thin spread / team consistency | 10-12 | how the rate is estimated | same HLTV + board 360 units/80 m (Oct 3-12) | none passes (best R4-hot -0.0057, -0.0119..+0.0008; team worse +0.0116) | all REJ |
| **Round 5** C0 BO3.gg fill, C1 shrink by r_w, C2 inactivity spread, C3 targeted line blend | 10-12 | thin / returning / low coverage | HLTV TRAIN Apr-Jun 2026, TEST 08-13..10-08; board 328 units/80 m (hash bafe9e4b078aa906) | C1/C2 fail TEST; C0 makes board worse (thin +0.207 MAE); C3 thin -0.054 (-0.094..-0.003) but fails criteria 3-4 vs lb-001 | nothing frozen; C3 = lead only |
| **lb-001** (`line-blend-1`) | frozen 10-12 15:49:04 | probit shrink of live chance toward 50% (kills w 0.0, HS 0.5) | board units Oct 3-12, 991 units/132 m, hash 49e7ac29547b86a9 | dev walk-forward +0.0149 (0.0039..0.0264); forward P1 932/57 -0.0001 (-0.0098..+0.0097); E2 1,238/55 -0.0004; picks 82 hit 41.5% | SH (A16 0.1) |
| staged before/after replay N0 vs N | 10-13 | repaired history (backfill) | 1,163 lines / 23 m Oct 11-13 | kills MAE -0.003 (-0.164..+0.174), log +0.0013; Over bias +0.93->+1.33; 20-50% band worse (exploratory) | not activated; N0 pinned (HC) |
| **Round 6 rr-1 / RR-KS** (market rounds) | frozen 10-13 17:51 | series price -> per-map q -> round race -> kill-weighted rounds, de-duplicated vs history | HLTV + Polymarket/Kalshi prices (ml_research/dataset.pkl); TEST ≥08-13 | TEST kills MAE -0.0535 (-0.0774..-0.0337), 996 m; vs no-market -0.0848; HS -0.0256; forward E2 1,060/48 +0.0016 (-0.0061..+0.0084); CK 275/15 +0.0025 | **PASS -> SH** (`shadow.market_rounds`); rr (rounds only) FAIL |
| Moneyline beat-the-market | 10-12 | 5 staged features; 4 families / 208 variants | 1,171 Polymarket / 1,089 Kalshi; dataset 3,543 matches, 1,790 OOS (2026-06..10) | none beats market (best GLM -0.00107, -0.00284..+0.00056) | ml-baseline-1 shadow; ml-candidate-1 frozen 10-12 05:14:25 (forward only) - ML |

### 2.3 Evaluation era on offered lines (Oct 14-22)
| id | date | hypothesis | data / periods | metric -> result | decision / doc |
|---|---|---|---|---|---|
| Checkpoint P1 | 10-14/15 | is live accurate on book lines | P1 635 units / 36 m, Oct 11-14 (+ R 978/67) | printed 57.1% vs 47.9%; log vs 50% -0.0145 (-0.0332..+0.0059); slope 0.09; kills line closer (-0.220) | P1 adopted (EV §8); CK |
| shadows on P1 (CK §6) | 10-15 | perf1_g7, legacy, no_bo3, perf-1 normal HS, old_spread, online_none, no_rest_bonus, pre_fix_v2 | 635/36 | legacy vs live log +0.0108 (-0.0073..+0.0285); perf-1 kills abs-err -0.072 (worse); others 0 changed | none promoted |
| **pl-1** (`prop-line-1`) | frozen 10-15 02:22:18 | line-anchored location m + w(λ-m) (kills 0, HS 0.25) | board std lines Oct 11-14, 635 units / 36 m, hash f1274a945c16b1da; walk-forward 521/30 | pl-1 vs market baseline -0.0113 (-0.0228..-0.0012) | SH, **promotion BLOCKED** (anchor defects) |
| C1 comparison set | reg 10-15 03:00 | A target line / B +other platform / C pl-1 / D live | forward | - | registered |
| **pl-2** (`prop-line-2`, pl-002-b5b076b5) | frozen 10-15 03:29:05 | pl-1 with eligible anchors only | forward | P1 440/33 +0.0128 (-0.0056..+0.0337), Brier +0.0061; recommends nothing; -0.0060 vs BASE | SH |
| error diagnosis (FA rd 7 qualifications) | 10-15 | rounds vs rate oracle split; spread coverage; Brier decomposition | same 36 m | rate dominates (oracle); 50%/80% coverage 55/53, 83/81; resolution ≤0.004 < calibration error | descriptive leads only (thin <500 rounds: kills printed 60.9% hit 31.0%, 15 m) |
| **A15 full-history study** | 10-15 | N0 starves perf-2 | HLTV Nov 2025-Oct 15 2026, 97,651 player-series / 9,778 m; scored Jan-Oct (85,391 / 8,550) | full history kills MAE +0.131 (+0.118..+0.143), 10/10 months, bias -0.70->+0.20; top-200 set +0.004 n.s.; archived lines (656/41) +0.0015 | -> fh-1 shadow |
| A15 one-change table | 10-15 | Elo term, opponent shrink 75/1200, no env blend, no opp adj, prior 1.0, no shrink, rounds league mean / both teams' length, HS constant | same | all ≤ +0.014 or worse; Elo on full history mixed (helps underdogs, hurts favourites) | REJ; Elo nonlinear = possible later test |
| **fh-1** (`full-history-1`) | 10-15 18:39 | perf-2 formula on all-tier history | forward P1 | 280/18 -0.0020 (-0.0100..+0.0060); line beats it on kills MAE -0.188; bias ~0 -> +0.24 | SH |
| **A16 CORR / lc-1** (`line-correction-1`) | lc-1 frozen 10-16 19:05 | logit P = a + b·z, z = (λ-line)/(c√λ); variants +thin, +form, +book, on logit(live) | P1 1,195 units / 72 m, Oct 12-16 (10-11 training); lc-1 fit 1,309/78, hash 654e9c6827b6 | +0.0132 vs live (-0.0022..+0.0292), -0.0004 vs BASE, AUC 0.473, recommends nothing | SH, no further development (owner) |
| A16 decision | 10-16 19:40 | do lb-001 / pl-2 / fh-1 improve live | production p1_units.json 19:37 | none established; live P1 1,211/72, 443 picks hit 50.1% (43.4-56.6), slope 0.06; Over-favoured 10 pts high | nothing qualifies; re-key fix 7a87e99 |
| **RA E1/E2 archive** | 10-16 | every offered line Oct 2-16 | E1 2,777 obs / 159 m (prop_grades, last line, no capture time); E2 1,831 / 106 | PROJ worse than BASE -0.0137 (-0.0249..-0.0033); kills disagreement ~90% projection error (b +0.11); HS full-history b +0.59 (+0.25..+0.93); anchor-w rolling selection +0.067 (+0.002..+0.135) = 1 of 9, not claimed | nothing qualifies (RA §5) |
| RA actionable-line (§6) | 10-16 21:00 | info at the line available when picked | 2,358 obs / 178 m, Oct 3-16, earliest forecast ≥30 min | -0.0066 vs flat; slope 0.25; 57% rule 52.6%, +0.028 (-0.026..+0.086); 30% of lines moved, 70% toward projection, move not predictive (slope 0.36, -0.11..+0.85) | CLOSED |
| RA HS comparison (§7): RECAL-LIVE, Z-REC, Z-FULL | 10-16 | correct HS overconfidence | 576 / 50 m Oct 12-16 | +0.0018 / +0.0030 / -0.0008 vs live, all n.s. | nothing qualifies |
| **hs-1** (`hs-calibration-1`, hs-001-9c8f70d8) | defined 10-16 20:50:42 (6e7dc53), registry 21:18:34 | logit a -0.15249 + b 1.01202·z on fh-1 full-history HS λ | fit 1,276 HS lines / 155 m Oct 2-16, hash f359ac40f36f | dev vs BASE +0.0025 (-0.0026..+0.0083); 0 forward at registration | SH (P2 itself on hold) |
| RA §8 opponent adjustment OA-1 / OA-2 / OA-3 | 10-16 | adjust history for opponent faced | HLTV Jan-Oct 16 2026, 85,861 / 8,597 m; lines 1,045/147 + 882/140 | OA-1 no gain (worse thin/lower tier); OA-2 established +0.014 MAE (+0.007..+0.021), log +0.0023; at lines +0.0016 kills n.s. | CLOSED, OA-2 not registered (absorbed into cm-1 features) |
| RA §9 complete models CUR / CUR-FULL / GLM / GBM | 10-16 | learned 14-feature model | train Nov 2025-Jun 2026, val Jul-Aug (4 configs/family), test A Sep, test B Oct 1-16; lines 1,927 / 149 m | GLM history book-listed +0.091 MAE; lines +0.0120 (+0.0032..+0.0203) vs CUR, +0.0098 vs BASE n.s., slope 0.85, 57% rule 58.8% (+0.092); GBM +0.0073 n.s. | GLM -> cm-1; GBM REJ |
| **cm-1** (`complete-model-1`, cm-001-4e5f42fb) | frozen 10-16 22:08:21; registry 22:35:14 | GLM refit through Oct 16 (98,121 rows, hash 9073466e2d54), c 1.4732 kills, HS λ·ratio c_hs 1.2781 | forward P1 | 10-18 scorecard (R/cm1_forward_2026_10_18): 325 / 20 m +0.0037 (-0.0112..+0.0177); kills +0.0116 (-0.0055..+0.0271); HS -0.0043 (-0.0217..+0.0111); 37 of first 271 units missing (deploy gap) | SH; known defect: NULL env -> online in its tables (AU limit 4) |
| P2 proposal (sequential O'Brien-Fleming looks, P2-CAL / P2-SEL) | 10-16 | qualification route on offered lines | - | power: 300 m detect 0.0056 / 6.7 pts; min gain 0.002 needs ~2,000 m | **NOT adopted (on hold)** - P2 |
| H18 Codex study: corrected cm-1 GLM (-> cm-2) | 10-18 | corrected inputs, fit through Sep 30 | HLTV 2025-09-01..10-18 (302,068 player-maps); strict P1 1,016 props / 59 m Oct 11-16 | kills 0.7016->0.6852 (+0.0159, +0.0058..+0.0264 eligible 521); HS +0.0078 n.s.; history MAE 6.238->6.101 | -> cm-2 |
| H18: +6 recent-context features | 10-18 | recent rate, time away, membership, depth, short-window rounds | same | no gain, slightly worse on lines | REJ |
| H18: temporal recalibration, half-strength log odds | 10-18 | probability compression | earlier-day fits | inconclusive; worse on Oct 17 | REJ |
| H18: native Map 1 model | 10-18 | own Map 1 model | 180 Map 1 props | HLTV MAE better; lines -0.0033 (CI across 0) | REJ |
| H18: normal HS distribution | 10-18 | replace beta-binomial | P1 | no gain | REJ |
| H18 diagnostics | 10-18 | map length; Map 1 / no_bo3 cohort | P1 verified kills | bias +6.22 (≤35 rounds) / -7.12 (51+) - post-match only; 63 no_bo3 props = tier-3 BO1 heavy underdogs on perf-2-bo3 | diagnostic only; no penalty added |
| **cm-2** (`complete-model-2-kills`, cm-002-9f1db397) | registry 10-18 21:55:07; **live 10-20** | corrected 14-feature GLM, kills Maps 1-2 | pre-release 738 lines: mean +0.22 kills, 41 >2 kills | forward +0.0097 on 11 m at promotion | **OVR** (owner "1"; review not reached) |
| bo3-env-2 | 10-18 | BO3.gg unknown env no longer online | 423/423 board lines identical | correctness | LIVE c36104d079 |
| results-gap pattern | 10-18 | why results trail printed | P1 1,642 lines Oct 11-18, 592 picks | projections unbiased (-0.02) but 2+ above line returns 2.5 under; Overs printed 62% won 47%, Unders 60% / 58% | descriptive (MEM results-gap-pattern-1018) |
| **perf-3** | reg + run 10-20 | λ3 = e^α λ2^β e^{γ(p_elo-0.5)} | fit Dec 2025-Jun 2026 (58,003), hold-out Jul 1-Oct 16 (32,041 / 3,208 m) | kills Maps 1-2 -0.028 (-0.043..-0.013); Map 1 -0.025; HS -0.008 | **FAIL** (R/proj_fix_2026_10_20); level drift Nov -1.04 -> Oct -0.46 |
| **hs-cm2** | reg 10-21 (e7e5e12), result c83d446 | HS Maps 1-2 on cm-2 kills + cm-2 HS ratio | hold-out matches 10-17 00:00 .. 10-21 18:57 UTC: HLTV 1,866 / 187 m; P1 580 / 70 m | HLTV MAE +0.063 (+0.020..+0.105) [+0.015 n.s. with N0 history]; P1 +0.0049 (-0.0013..+0.0112); bias +0.13, Over lean | **PASS -> LIVE** f663af8917 (R/hs_cm2_2026_10_21) |
| audit 10-21 (65 bugs) | 10-21 | correctness | - | see MV f663af8917 | fixed; leftovers #14 (LoL), #25 (older BO3.gg series only when last-10 short), #30 (BO5 sim labels), smooth_prior_pf perf-cm2 unwrap, settle_totals by nick |
| perf-refit-1 (150-match counter) e.g. **ch-009** | auto | refit k0, prior_m, ke, k_hs, c_mult, kappa on scored units | learning.TRAIN_RULE (time-ordered 60/20/20) | UP: "ch-009 observed change non-positive" | SH (details only in production learning.db; not in repo) |
| P1 aggregate look 10-22 ~15:25 UTC (Codex LoL session) | 10-22 | scorecard read | P1 2,727 props / 157 m, mixed versions | favoured-side 51.34% (48.46-54.17) vs 55.94% printed; log loss 0.7003; f663af8917 24/35 from 2 matches | descriptive (UP) |
| **UT-1** (this investigation) | reg 10-22 | Under concentration trace + bug hunt | P1 units + saved inputs (dev) | running | REGISTER.md |

---

## 3. Data periods already inspected (= development data now)

| Period (match dates) | Data | Inspected by |
|---|---|---|
| ≤ 2026-09-27 (BO3.gg) | 45 players' last 10 matches; 373-900 walk-forward cases | v7.3-v7.5 (FW) |
| 2025-09-01 .. 2026-10-08 | HLTV export (113,859 player-maps; TRAIN <07-01, VALID <08-13, TEST 08-13..10-08) | perf-1/2 tuning, FACTOR_AUDIT rounds 1-5 (rd 5 TRAIN 2026-04-01..06-30), match_m1, prior_smooth, rr-1 rd 6 (TEST outcomes reused) |
| 2025-10 .. 2026-10-11 | Polymarket / Kalshi series prices (3,543 matches; OOS 2026-06..10; feature test 2026-08-12..10-11, test split 10-01..10-11 scored >1x) | ML_EVAL, rr-1 prices |
| 2025-11-01 .. 2026-10-16 | full HLTV history (97,651 player-series; Jan-Oct scored) | A15, OA (Jan-Oct 16), complete models (train Nov-Jun / val Jul-Aug / test Sep-Oct 16), cm-1 fit |
| 2025-12-01 .. 2026-10-16 | HLTV series records | perf-3 fit (Dec-Jun) + hold-out (Jul 1-Oct 16) |
| 2025-09-01 .. 2026-10-18 | HLTV 302,068 player-maps | H18 Codex study, cm-2 fit (through 09-30) |
| 2026-09-28 .. 10-07 | member-entered lines (legacy tuning) | grader CONFIG history |
| 2026-10-02 .. 10-16 | prop_grades offered-line archive (UD from 10-02, PP from 10-11; 4,784 lines) | RA E1, hs-1 fit |
| 2026-10-03 .. 10-12 | board units (991 / 132 m) | lb-001 fit, rd 4/5 board sets, HS check (10-03..08) |
| 2026-10-03 .. 10-16 | our decision-time forecasts | RA E2 / actionable / HS / OA / complete-model line tests |
| 2026-10-11 .. 10-14 | P1 board lines | checkpoint, pl-1 fit, staged N vs N0 replay (10-11..13) |
| 2026-10-11 .. 10-16 | P1 (1,211 / 72 m) | A16, lc-1 fit, H18 strict set (1,016 / 59 m) |
| 2026-10-17 .. 10-18 | P1 forward + HLTV | cm-1 forward scorecard, H18 (to 10-18 21:10 export), results-gap note |
| 2026-10-11 .. 10-19 | P1 | perf-3 secondary check (rule 4) |
| **2026-10-17 00:00 .. 10-21 18:57 UTC** | HLTV (1,866 player-series / 187 m) + P1 HS lines (580 / 70 m) | hs-cm2 hold-out (spent) |
| 2026-10-11 .. ~10-22 15:25 | P1 aggregate scorecard (2,727 / 157 m) | UP (aggregate read only, no model fitted) |

- **Latest date any model study touched: 2026-10-21 18:57 UTC** (hs-cm2 HLTV pull / hold-out end). Production P1 aggregates were
  viewed (not modelled) at ~15:25 UTC on 2026-10-22 (UP), which may include a few matches started after the 2026-10-22 00:00
  cutoff (f663af8917's 2 matches). Phase 1 should list and flag any such matches before declaring the forward set clean.
- Every saved forward forecast of a frozen challenger stays THAT challenger's forward evidence even after inspection (owner
  10-16), but is development data for anything new.
- **Untouched for selection:** matches starting **2026-10-22 00:00 UTC onward** (the confirmation set; cm-2/hs-cm2 live and every
  shadow keep logging there). Within the 2-year window, **2024-10-22 .. 2025-08-31 HLTV history has not been used by any
  documented study** (all start 2025-09-01 or later; perf constants tuned Sep 2025-Jun 2026), but pre-2025-07 coverage is the old
  top-200 scan only (the backfill window was 420 d + 30 from ~2025-07-14; ML_EVAL saw the DB span 2024-01-09..2026-10-11 with
  8,578 matches / 368 teams pre-backfill) - phase 1 must measure what exists there. Note the cm-2 / A15 HLTV feature tables are
  built from 2025-09-01, so any 2024-25 use needs its own pipeline check.

---

## 4. Rules that constrain the investigation

Evaluation (EV, A16, CK, MEM evaluation-objectives-separate / recap-reporting-rules / frozen-test-week):
1. **P1** (EV §8, `learning.PRODUCT`): unit = player x canonical match (identity.assign) x stat x scope; forecast = latest BOARD
   forecast logged ≥30 min before start on a standard platform line with book + feed market id + player id; member-entered and
   goblin/demon/promo lines never enter; pushes excluded and counted; four sections (projection error, probability/calibration,
   side selection, staking). The registered primary cohort (any source) is kept unchanged beside it.
2. **Intervals by whole matches** (canonical match resampling); report units, rows, distinct matches, W-L-P, staked vs all, cross-platform
   duplicates; quote every cohort with date range, snapshot rule and version breakdown; never "since the deploy" for older versions.
3. **Calibration (probability accuracy) and selection are separate questions**: paired log loss AND Brier vs live on exact
   real-line forecasts plus calibration; failing to beat BASE does not by itself disqualify a calibration gain over live; selection
   must beat the simple baseline (BASE = earlier-day Over rate per stat, shrunk with 50 pseudo-units) on genuinely new lines;
   point estimates never establish a gain; fixed selection rule = favoured side ≥57%.
4. **REVIEW_RULE** (learning.py): decisions only at 300 / 600 / 1,000 distinct forward matches (forecast logged AND match started
   after the freeze); min gain 0.002 log loss with 98.33% interval > 0; calibration slope CI overlaps 0.8-1.2 and ≤1.0 wide; no
   stat x scope slice (≥50 m) worse than 0.005; live replay gap ≤0.01; pass = "eligible for review" only, promotion = explicit owner
   decision + new version. A changed / refitted candidate is a new id with its own clock; never inherit or backdate forward rows.
   Any new rule (e.g. P2) applies prospectively only after written owner approval.
5. **Forward evidence stays forward**; inspected periods are development for later changes.
6. **No new ~50% challengers** without new information (lb-001, lc-1, pl-2, BASE already cover "about 50%"); model the specific
   errors exposed (regression to the line) instead of searching inspected outcomes.
7. **Never fix calibration by lowering chances / turning B into Lean** without a validated correction; a validated probability
   correction that lowers chances IS acceptable once it passes the registered review vs live and the line baseline. Never raise
   stakes or force an Over/Under balance. Keep the number of playable lines; keep every product output (projection, side,
   Over/Under/push, grade, stake). Keep "Lean Over/Under" wording; no unsure labels on member pages.
8. **Grading factor rule**: a factor moves grades only after a pre-registered chronological (walk-forward / earlier-day fit,
   next-day score) test; untested/failed factors shown at weight 0; correlated factors grouped and regularised, never stacked
   multipliers; missing data stays unknown.
9. **Report per exact model tag**; pool only inside a MODEL_VERSIONS "same grading behaviour" group; history revision per
   forecast (N0 / partial / N / N0-pinned rule); cm-1 vs cm-2 reported separately; 8404c86d3c vs 794a775f51 separately.
10. **Transient rule**: an HLTV / Thunderpick blip never changes a projection/side/label; keep last grade or hide; never cache
    failures; settlement never voids on an outage.
11. **Owner Under concern**: test repeated owner concerns as hypotheses (correlated Unders on underdogs; upset days) - UT-1.
12. **Data rules**: HLTV is the source (BO3.gg = lookup / zero-history fallback); exact team-name variants and canonical ids,
    never fuzzy (academies); always check the opponent when mapping to an HLTV match (multi-BO1 days); benched players ignored;
    HLTV authoritative for settlement (BO3.gg provisional; team-kill definition differs on 4.22% of player-maps, EV §7).
13. **Process**: verify end-to-end on real data before any deploy; run the grade audit after grader changes; load-test server
    changes for 700 concurrent members, one quiet-time test; paged `/admin/forecasts_page.json` only (the unbounded export
    restarted prod); posts only from saved forecasts. (This phase: read-only; no production contact.)
14. **Investigation register rules** (REGISTER.md): live grading unchanged; shadow until passed; bugs fixed separately with
    regression test + own version; original forecasts never rewritten; register before running.

---

## 5. Established data facts

- **N0 pin and its bias.** perf-2 reads N0 (top-200 selection). A15: current model kills bias **-0.75** (-0.86..-0.65), negative in
  11 of 12 months; worst vs weakest-Elo opponents (-1.54), thin history (<150 rounds -1.51), online (-0.91), favourites (-1.20);
  big underdogs +1.36. Full history removes it (bias +0.20) but over-projects underdogs / strong-opponent matchups. perf-3 found the
  under-projection shrinking month by month (Nov -1.04 -> Oct -0.46). On offered lines P1 bias is near 0 (H18 kills -0.09,
  results-gap -0.02) because lines sit on deep-history players. Since 10-20/10-21 Maps 1-2 kills/HS read full history via cm-2;
  Map 1, Maps 1-3 and the perf-2 fallback still read N0.
- **Coverage gap / backfill** (HC): pre-repair scraper kept a match only if BOTH teams were in today's VRS top 200 -> 48.2% of
  board players' maps (tier 3 47%, tier 4 32%, online 39%, opponent 201+ ≤6.5%); missing maps not random (+0.051 KPR for the same
  player vs weak online opponents). Oct 11 rebuild reset ingest timestamps (8,578 of 8,597 raw_pages fetched 10-11). Backfill
  19,590 matches written straight into live tables 10-12 21:27-21:35 and 10-13 00:23-04:24 UTC (19,574 -> 61,671 maps); partial N
  live 00:49-04:26 (315 forecasts), N live 04:26:15 10-13 .. 22:53:13 10-14 (unintended activation; 20,290 affected forecast rows =
  773 units / 51 matches, MV table); N0 pinned n0-r3 from 23bea2fe37 (3 'cito' rows leaked, 736 rows), n0-r4 from 6b6a43f2a6
  (2026-10-15 00:38). Since then the scan takes every tier. Historical ingestion time ≠ match date (A15/H18 caveat).
- **HLTV DB size**: N0 8,642 of 28,244 matches (10-14); H18 302,068 player-maps / 30,203 maps / 13,227 matches 2025-09-01..10-18;
  hs-cm2 tables 304,940 clean rows; 37-38 incomplete maps and 20 unknown-environment rows; mapstats HTML archived to
  `C:\Users\tikic\cs2_scout\exports\hltv_mapstats_pages_2026-10-15` and `D:\hltv_page_archive\...` (parsed stats untouched).
- **Offered lines**: none before 2026-10-02 (Sep 28-Oct 1 member lines only). `prop_grades` (scraper DB) = LAST line only, no
  capture time: Underdog from 10-02, PrizePicks from 10-11, 4,784 lines through 10-16 (150 wrong-opponent + 133 ambiguous links in
  the scraper's own archive). `prop_lines` keeps only the current board (no historical opening lines except PrizePicks open_line and
  our `pp_lines.jsonl` / `ud_moves.db` / `ud_open.json`).
- **Our forecast archive**: Railway picks export to 10-10 16:40, Railway forecast log 10-07 21:11 .. 10-08 23:24; **lost 10-10 16:40
  .. 10-11 04:56**; Render complete from 10-11 04:56 (52,080 forecasts by 10-16; 59,313 by 10-18 21:10). Clean evaluation start
  2026-10-07 21:11:49 (picks row = first published forecast; `forecasts` table appends regrades). Exact inputs from 10-08 00:54;
  probs_raw 10-12; quote/sides/anchors 10-15. Railway board rows lack feed ids (BU, never P1).
- **P1 size over time**: 635 / 36 m (10-11..14) -> 1,211 / 72 (..16) -> 1,644 / 94 (..18) -> 2,727 / 157 (..10-22 15:25).
  Live has printed ~56-61% and hit ~48-53% on its favoured side throughout; calibration slope 0.06-0.38; AUC ~0.50-0.53;
  Overs consistently worse than Unders (e.g. 42.9% vs 56.4% on 428 picks; 47% vs 58% Oct 11-18) - development observation, not a rule.
- **Line vs projection**: the offered kills line is the better point forecast (MAE gain of projection -0.163, -0.309..-0.031; E1
  MSE +2.59); slope of (actual-line) on (proj-line) kills ~+0.1, HS +0.33 (+0.59 full history); line departures from our projection
  follow team strength (+0.50 kills per 100 Elo of own advantage). Outcomes follow the PrizePicks line over Underdog's where they
  differ (slope 1.21), not usable at 0.5-1 kill gaps.
- **Spread / mean**: interval coverage close to nominal (P1 50% 55/53%, 80% 83/81%; z sd 0.91 kills); overconfidence comes from the
  distance-from-line, not width. Oracle: rate error dominates rounds error (MAE 5.31 -> 2.76 with known rate, 5.22 with known rounds).
- **Power** (RA §4, conditional estimates): log-loss gain detectable at 80%: 0.0076 (159 m), 0.0056 (300), 0.0040 (600), 0.0031
  (1,000), 0.0022 (2,000); selection edge 9 / 6.7 / 4.8 / 3.7 / 2.6 pts. Board volume ~15-20 matches/day.
- **Forward review windows** (300 / 600 / 1,000 distinct forward matches) - none reached by any challenger; clocks: lb-001 10-12
  15:49:04; rr-1 10-13 17:51 (logged from 18:10); match_m1 10-09 19:15; perf_map1 10-08 22:42; prior_smooth valid from 10-14 22:53:13;
  pl-1 10-15 02:22:18 (blocked); pl-2 10-15 03:29:05; fh-1 10-15 18:39; lc-1 10-16 19:05; hs-1 10-16 21:18:34; cm-1 10-16 22:35:14;
  cm-2 10-18 21:55:07; ml-candidate-1 10-12 05:14:25 (own REVIEW_PLAN). Progress last documented (10-16): lb-001 65, pl-2 33, fh-1 18.
- **Settlement / identity**: HLTV vs BO3.gg kills differ on 4.22% of player-maps (BO3.gg full scoreboard subtracts team kills);
  Maps 1-2 totals differ 7.49% -> ~0.9% of settlements could flip; P1 re-key double count fixed (264 of 1,475 rows); identity rule v3;
  known identity gaps: dev1ce (HLTV 'device'), Hatz (no maps in window).

---

## 6. Open questions and leads not yet tested (or only partly)

1. **Regression to the line** (the central unresolved error): projections above the line overshoot (+1.31) and below undershoot
   (-0.81) while the line is unbiased (A16 0.4); live confidence comes from that distance. pl-2 (anchoring) and cm-2 (complete model)
   are the live/shadow attempts; no prospective test yet shows a selection edge. A model of WHAT the line knows (team strength /
   context: Elo gap effect, market rounds) beyond rr-1 is untested at lines.
2. **cm-2 / hs-cm2 forward**: live since 10-20 / 10-21 without a passed review (kills) - first registered look at 300 forward
   matches; watch the hs-cm2 Over tilt (bias +0.13; P1 Overs 46.4% vs 49.3% predicted) and cm-2's +0.22-0.48 kills mean shift
   (more Overs among 55%+ lines: 135 -> 168).
3. **Map 1 and Maps 1-3 still on perf-2 N0**: no cm-2-style model for them; the native Map 1 model failed at lines (H18); Maps 1-3
   HLTV construction never tested on offered lines.
4. **Team strength / blowout**: Elo term mixed on full history (A15: nonlinear form to be defined before data); perf-3 failed due to
   level drift; rr-1 passed HLTV but no line-relative gain; heavy-underdog tier-3 BO1 cluster on perf-2-bo3 (H18) unresolved.
5. **Rounds / map-length distribution**: post-match rounds explain large residuals (±6-7 kills at extremes); only simple pre-match
   rounds models tested (rejected). Untested: map-specific round distributions, side x map, map x opponent, veto-aware rounds
   (veto / side-choice data not collected historically).
6. **Thin / BO3.gg-only coverage gap**: cm-2 ineligible for HLTV-missing players; projection worst for thin/online/lower tier
   (defer-to-line candidates, C3 thin lead -0.054 dev, not frozen); returning-player definition that survives the first match back
   (rd 5 open item).
7. **Opponent-adjusted joint model** beyond cm-1 features (OA-2 standalone never registered); the opponent adjustment of the
   history helps only established/top-tier players.
8. **HS**: full-history HS projection carries information (b ~0.6) but Over-biased ~5 pts at lines; hs-1 forward only.
9. **Calibration correction**: live is overconfident at lines (slope 0.06-0.38); recalibration fits so far are inconclusive and
   lc-1/hs-1 are forward-only. A validated correction would be legitimate (owner) but must pass the registered review.
10. **Side asymmetry**: Overs underperform Unders in every live subset (development); UT-1 is tracing Under concentration; the
    owner's correlated-Unders-on-underdogs concern needs match-level correlation analysis.
11. **Data-blocked factors**: dated AWP share / weapon mix and IGL roles (daily snapshots from 2026-10-09, now ~2 weeks deep);
    CT/T starting side (needs veto side choice); openings/trades/economy not in the DB; stage re-test (R3) with more data;
    line movement re-test at 150+ matches; cross-book anchors now logged (from 10-15).
12. **Two-year window**: HLTV history 2024-10-22 .. 2025-08-31 is unused by any study (sparser, top-200 coverage) - a possible
    independent development/validation era if phase 1 confirms its quality and identity joins.
13. **Leftover audit items** (MEM release-1021-audit-qol): #25 older BO3.gg series read only when last-10 short; picklog
    `smooth_prior_pf` does not unwrap perf-cm2 (prior_smooth shadow on cm-2 lines may be wrong); `settle_totals` selects the player
    by nick; dev1ce alias gap (owner decision); cm-1's NULL->online encoding (frozen, not refit).
14. **perf-refit-1 / ch-009**: the automatic refit's candidates and results live only in production learning.db - read before any
    new perf-2 parameter work so it is not duplicated.
15. **P2 sequential qualification** (multiplicity across candidates, futility stops) proposed but not adopted; with the 0.002 minimum
    gain the registered windows cannot detect realistic effects (~2,000 matches needed).
