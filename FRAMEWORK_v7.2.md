# CS2 PrizePicks Grading Logic — v7.2

Map 1-2 kills and headshot props. Not financial advice.
The math lives in `grader.py`; the data comes from `cs2_scout.py`. The last section lists how
each rule is applied now that BO3.gg replaced HLTV, and every rule change the owner approved.

## Changelog (v7.2)
1. **CS2 round count** fixed to MR12: **46 standard / 40 stomp** (old CSGO MR15 52/44 inflated every projection).
2. **Mandatory live roster + role gate** before any projection. Role is overall rating vs teammates, **not** entry rating.
3. **Sample-size hard gate**: under 10 maps = variance flag; under 5 maps = **NO GRADE**.
4. **Phase 10**: results logging and calibration loop.

## Core discipline
Most factors are already inside KPR, HS%, Round Swing, Rating, KAST. Do **not** stack extra
multipliers for utility, smokes, eco, positioning, or map "bonuses".
v7 does six things:
1. Fold map and side into the **base** via filters, not extra bonuses.
2. Replace cliff thresholds with **smooth continuous functions**.
3. Compute P(More) from a **distribution**, not a lookup table.
4. **Cap** the adjustment stack.
5. Separate **grade** (point estimate) from **confidence** (unit size).
6. Use sub-ratings to **confirm role**, not add new multipliers.

## PHASE 0 — Intake and hard gates
**Prop type fork**
- HS prop: λ = KPR × rounds × HS%
- Kills prop: λ = KPR × rounds

**Gate A — AWPer on HS prop.** Sniping >40/100 on an HS prop = **NO PLAY**. Near 0 = pure rifler, allowed.

**Gate B — Confirmed IGL.** Apply **-15%** volume. Remove from fragger hierarchy. Do not NO PLAY.

**Gate C — Live roster + role (mandatory).** Pull current lineup at grade time.
- Confirm player is on the listed team and not a stand-in.
- Rank by **overall rating vs teammates**.
- Tag nationality from current roster only.
- If roster cannot be confirmed, disable regional H2H extension.

**Gate D — Sample size (mandatory)**
- <10 maps on deepest filter: variance flag, inflate SD, cap **1U**.
- <5 maps: **NO GRADE**.

## PHASE 1 — Data pull
**Waterfall (current team; map filter when maps known)**
1. Last 3 months if maps ≥10
2. Else last 6 months
3. Else last 12 months
4. Else all-time current team, flag ±8% variance

Map filter is mandatory when maps are known.
**Pull:** KPR, filtered HS% (never career as primary), Round Swing, Sniping, Rating, KAST, DPR, ADR,
Multi-kill %, four teammate ratings.
**Recency:** if the last 10 matches (Maps 1-2 only) trend hard vs window, blend **70% window / 30% recent** *(owner change from last 5 maps, 2026-09-27)*.
**Role signal:** overall rating hierarchy, then AWPer/IGL gates. Entry rating is **not** the
hierarchy. Confirm with Firepower + Opening. High Trading + low Firepower = support cap.
**Transfer check:** current team and current teammates only.

## PHASE 2 — Base lambda
Rounds (CS2 MR12, first to 13): Standard **46**; Stomp **40** when favorite is **-400 or heavier**. Never 52/44.

## PHASE 3 — Bounded adjustment stack (in this order, then clamp)
- **3a Role:** ~~Primary +5% (gap >0.10 to #2 → +6%); Secondary 0%; 3rd or lower -10%~~ **0% for every rank (owner change 2026-09-27)** — KPR already carries role, so the multiplier double-counted it (check: #3-5 under-projected ~7%, primaries over-projected ~6%). Rank still shown. Confirmed IGL -15% and out of hierarchy (unchanged; `grader.py` CONFIG `role_igl`).
- **3b Round Swing:** SwingAdj% = clamp(RoundSwing × 3, -6%, +6%)
- **3c Blowout Factor:** moneyline → P(win); BF = 1 + 0.12 × (P(win) − 0.5)
- **3d Win/Loss weighting (last):** λ_final = P(win)·λ·1.05 + P(loss)·λ·0.85
- **Stack cap:** role × swing × BF clamped to **[0.75, 1.20]** before the win/loss split.

## PHASE 4 — Distribution and P(More)
SD ≈ √λ_final × c (c starts 1.0–1.15, calibrated from logged results).
P(More than L) = 1 − Φ((L + 0.5 − λ_final) / SD). Continuity correction +0.5.

## PHASE 5 — Grade scale
| Grade | P(More) |
|---|---|
| A+ | ≥80% |
| A | 74–79% |
| A- | 68–73% |
| B+ | 63–67% |
| B | 57–62% |
| C | <57% → **NO PLAY** as a core leg |

Pairs require **both legs B+ or better**.

## PHASE 6 — Confidence and sizing
Widen SD or drop a half-grade for: sample <10 maps; poor DPR + KAST; stomp compression on overs;
underdog HS prop (needs elite HS% to survive); unconfirmed transfer/role shift.
**Units:** 2U A+ legs or pairs +60% EV · 1.5U A/A- legs, pairs +30–60% EV · 1U B+ or pairs
+15–30% EV · 0.5U/skip under +15% EV, double-underdog, small-sample flags.

## PHASE 7 — H2H override
Trigger: **≥4 maps** of player-level H2H across all past teams/IGNs *(owner changed to ≥2 maps /
1 series — see below)*. Build Maps 1-2 pairs in chronological order **within each series**. Never
sliding-window across series.
- ≥65% hit rate and clears in wins → **+1 grade**
- ≥70% hit rate and floor clears even in losses → **+2 grades**
- <45% hit rate → full underdog compression
- 45–65% → no H2H bump

Regional extension only if exact-opponent sample is too small **and** live roster is confirmed.

## PHASE 8 — Pair construction
Both legs B+ or better · no reused props · strong with strong · different matches = independent by
default · same-match correlation only if justified and the locked multiplier hasn't nerfed it ·
EV = P(both) × payout − 1.

## PHASE 9 — Market layer
PP lines in CS2 are soft; edge comes from lag on roster, role, veto, and form.
Break-even (independent legs): 2-pick 3x ≈ **57.7%**/leg; 3-pick 6x ≈ **55.0%**/leg.
A **B** sits on the vig line. Real edge starts at **A- (68%+)**.
Demon = raised line, usually a trap. Goblin = lowered line, low-variance anchor only if EV is better.

## PHASE 10 — Logging and calibration
Log **before** the match: player, line, λ, P(More), grade, flags, units, EV. Log **after**: actual
stat and hit/miss. Never edit pre-game fields. Calibrate by grade tier at ~20+ legs per tier. Main
dials: SD constant c; adjustment weights (esp. underdog HS and stomp rounds). Never recalibrate off
one slate.

## Hard rules that never move
- Verify current team first. Transfers flip the math.
- Career HS% is invalid as primary input.
- Map order in H2H is chronological within a series.
- The **line** is the variable, not the projection.
- Missing data is a fetch trigger, not a guess.
- **Kills props beat HS props at equal EV.**
- Two different matches are independent unless you prove otherwise.

**One line:** confirm live roster and role, pull filtered data, use 46/40 CS2 rounds, apply a
small capped stack, turn λ into P(More) with a distribution, check the line against vig, size with
confidence, then log every result so the model calibrates.

---

## How each rule is applied with BO3.gg (owner-approved, 2026-09-27)
| Rule | Implementation |
|---|---|
| Live roster (Gate C) | BO3.gg team page: 5 players (coach excluded). Stand-in = not on the roster. Confirmed = latest map's lineup matches 4+ of them. |
| Nationality tag (Gate C) | From the opponent's **current roster only**. 3+ players from one country — or from one region in `regions.csv` — = the team is from there. Mixed rosters get no tag and the regional rule doesn't apply. Nothing is guessed: player countries come straight from BO3.gg. |
| Regions | `regions.csv`: all 254 countries in BO3.gg's 8 regions (Europe, CIS, South America, North America, Asia, Oceania, Africa, Other). Owner override: Ukraine moved from Europe to CIS. Edit the file to change any country. |
| Regional extension (Phase 7) | Only when there's **no** exact-opponent series (one series counts on its own) **and** the roster is confirmed: adds the player's latest series (Maps 1-2) vs other teams with the same tag, up to 4 series. Marked "regional" in the output. |
| H2H trigger (Phase 7) | **≥2 maps (1 series)** instead of 4 — `grader.py` CONFIG `h2h_min_maps`. |
| Sniping (Gate A) | No weapon data on BO3.gg. AWP kills are one body shot: body-shot kills per body hit ≥0.30 = AWPer (Sniping 100 → HS NO PLAY), ≤0.22 = rifler (0), between/too few hits = unknown (flag). |
| Rating (role) | HLTV-style rating computed from raw BO3.gg stats over the same window for the player and all 4 teammates, using the public approximation of HLTV Rating 2.0: 0.0073·KAST + 0.3591·KPR − 0.5329·DPR + 0.2372·Impact + 0.0032·ADR + 0.1587, Impact = 2.13·KPR + 0.42·APR − 0.41. Check: ZywOo 3m = 1.326 vs HLTV 1.32. (Replaced BO3 rating ÷ 6, which misranked players, 2026-09-27.) |
| Recency (Phase 1) | Owner change: **last 10 matches, Maps 1-2 only** (was last 5 maps). Their KPR drives the 70/30 blend; each match's Maps 1-2 total is shown vs the line with the over-count. |
| Round Swing (3b) | Not available — 0 (no swing adjustment). |
| Trading/Firepower support cap | Not available — skipped. |
| IGL (Gate B) | `igl.csv` (owner-maintained); BO3.gg doesn't record IGLs. |
| Moneyline (3c/3d, stomp) | Polymarket series price, auto. No market = 50/50, flagged. |

## v7.3 calibration (2026-09-28, owner-approved; tested out-of-sample before going live)
Test: 45 players' last 10 matches graded with ONLY data from before each match, 13 lines each
(5,850 kill lines + 5,850 HS lines). Live settings, printed -> actual:
kills B+ 65.4 -> 64.3, A- 71.0 -> 71.4, A 76.4 -> 76.3 | HS B+ 65.4 -> 64.5, A- 71.0 -> 70.5, A 77.0 -> 77.3, A+ 86.1 -> 86.5.

| Setting | Now | Why |
|---|---|---|
| SD constant c (kills) | **1.55** | out-of-sample spread; 1.26 (in-sample) still printed 65% for 60% |
| c (headshots) | **1.30** | best out-of-sample fit (gap 0.8%) |
| Kills probability cap | **78%** (no A+ on kills) | printed 81% hit 69% - fat tails |
| Rounds | **22/map (44)**, stomp 40 when either side is -400 or heavier | real maps average 21.8 |
| Role multiplier | **0%** (rank shown) | owner asked for it back on; out-of-sample it made projections 1.9 kills too low and failed the 63-67% target |
| Map KPR | blended: weight n/(n+12) (8 maps = 40%) | no hard switch at 5 maps |
| Thin data | <20 Maps 1-2: wider SD, 1U cap (<5 still NO GRADE) | BO3.gg under-covers tier 2/3 |
| H2H | 2-3 maps: +/-2% projection nudge only; +1 grade needs 4+ maps; +2 needs 8+ and clears in losses; units never follow a bump | one series is one night |
| FORM FIGHT | if last 10 matches' smoothed hit rate at the line is on the other side of 50%, P capped at it and 0 units | the record vetoes the model |
| Units | follow P: 2U 80%+, 1.5U 74%+, 1U 68%+, 0.5U 63%+, 0U below; B = display only; 0U on form fight or HS prop on a #3+ player | B sits on break-even (57.7%) |
| AWP gate | body-shot kill rate >= 0.30 **or** Maps 1-2 HS% < 40 with KPR >= 0.70 | owner's HS% rule added |
| Map prediction (before the veto) | each team's picks/first-bans over its last 15 BO3s; Map 1 = first-banning team's pick (BO3.gg's 2nd-listed team, 19/21); projection = probability-weighted over maps | tested on 74 matches: top guess right 30%, actual map gets 23% on average vs 13% random - weighting, never a single guess |

## v7.4 - market selection (2026-09-28, owner-approved)
The calibrated core (c, kills cap, 44/40 rounds, map blend, role 0%, H2H, thresholds) is untouched.

| Addition | Rule |
|---|---|
| Pick display | Always a side (owner's choice), with grade, %, units and confidence |
| SCOUT | No upcoming match found for his team: shows the lean, projection and form, no grade/units, never logged as a wager |
| Market quality | HARD = Polymarket pre-match midpoint with $1,000+ liquidity. SOFT = thin/wide book. NONE = no market (50/50). SOFT/NONE -> max 1U |
| TAPE FIGHT | Model 57%+ More but last-10 over-rate <= 50%, or 57%+ Less but >= 60% -> 0 units (side and % kept). FORM FIGHT (3/10 or 7/10) still halves confidence |
| Last-10 median | Shown next to the average |
| Veto uncertainty | Predicted maps: a slot whose top-2 maps hold < 45% -> risk flag (wider SD) |
| Permabans | A team that first-bans a map 70%+ of the time (5+ vetoes) removes it from the prediction |
| Rust | Days since last official map: 8-14 note; 15-28 risk flag; 29+ risk flag + max 0.5U |
| Opponent defense | SHADOW ONLY - kills/round the opponent allowed on its last 15 Maps 1-2, logged for Phase 10, not in the projection |
| Phase 10 | Every GRADE logged once (player+prop+line+type+match), auto-settled from BO3.gg 3h after start (Maps 1-2 actual, hit/miss; forfeits/BO1 void), closing line entered on /admin for CLV. Report: printed % vs actual, by units |
| Not added (by decision) | FACEIT in the projection; role multipliers; Grok's 60/40 last-10 blend (double-counts recent form); opponent defense in λ until Phase 10 proves it |

## v7.4.1 - win/loss measured, not assumed (2026-09-28)
Measured on 450 real series: actual / base projection when the team won both Maps 1-2 / split /
lost both = kills 1.043 / 0.969 / 0.871, headshots 1.022 / 0.956 / 0.893. The projection now uses
the expected factor over those outcomes (series win chance -> per-map q, P(series) = q^2(3-2q)),
replacing the untested 1.05/0.85 split and 0.12 blowout factor (which penalised a 33.5% underdog
-5.4% vs the measured -1.9%). The separate stomp cut to 40 rounds is off (the factors already
include lopsided maps). Calibration after the change: kills B+ 65.4 -> 66.7, A- 70.9 -> 71.2,
A 76.4 -> 77.9; HS A- 71.0 -> 70.2, A 76.9 -> 77.1, A+ 86.1 -> 86.7.

## v7.4.2 (2026-09-28, owner-approved)
- Win/loss factor clamped to [0.90, 1.08]; uses Polymarket Map 1 / Map 2 winner prices when
  listed (P 2-0 = q1*q2, P 0-2 = (1-q1)(1-q2)), else derives q from the series price.
- "HS prop on a #3+ player = 0U" DELETED. Role rank is display only. Units follow P only
  (thin data still caps 1U, FORM/TAPE FIGHT still 0U).
- TAPE note (display only) when the projection is 1.0+ HS / 2.0+ kills from the last-10 average.
- Card shows base, win/loss factor, projection, line (+fair), edge (projection - line), last-10.
- Walk-forward after the change: kills B+ 65.4->66.7, A- 70.9->71.2, A 76.4->77.9; HS B+ 65.5->68.9,
  A- 71.0->70.2, A 76.9->77.1, A+ 86.1->86.7. HS 0.5U picks 202 -> 573 lines, hit 67.7%.
  Underdog-only slice not testable (no historical pre-match odds) - Phase 10 will show it.
