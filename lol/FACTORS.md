# LoL factor inventory

Started 2026-10-17. For each proposed factor the table records:
- **Source:** where it comes from.
- **Coverage:** in the canonical history.
- **Timestamp / availability:** whether it exists before the forecast is made.
- **Missing data:** how a gap is treated.
- **Status:** where the factor stands.

The factor groups come from the owner's brief of 2026-10-17. The owner's original factor list and pairing log were not attached, so the brief's enumeration is used.

**Statuses**
- **used:** in a fitted model compared under lol/EVALUATION.md.
- **tested-rejected:** tested on the development split and no improvement was found.
- **to test:** queued for the A4 development tests.
- **unavailable:** no pre-match source exists in the data center.
- **not allowed pre-match:** known only after the forecast time.

**Rules that apply to every factor**
- Historical summaries use games on days before the match day only.
- Missing data is never imputed as a favourable value. A player without history falls back to the role and league prior, and that is flagged.
- Correlated signals (favourite status, team strength, expected kill share) enter one fitted model once. They are never stacked as separate bonuses.

## Player
| Factor | Source | Coverage | Pre-match? | Missing | Status |
|---|---|---|---|---|---|
| Role | LP ScoreboardPlayers.Role / LE participant role | all games | yes (last fielded role) | prior role from history; a genuine role change is shown, not guessed | used (all models) |
| Recency-weighted kills / assists / deaths | canonical history | all games | yes | role × league prior | used (B0 unweighted; lol-1 time-weighted half-life; lol-2 kill / death shares) |
| Kill share of team | history (kills / team kills) | all games | yes | role prior | used (lol-1 share form, lol-2) |
| Assist participation (other team kills) | history | all games | yes | role prior | used (lol-2) |
| CS per minute | history (LP CS; LE creepScore) | all games | yes | role prior | used (lol-2 CS, fantasy points) |
| Damage share, DPM | LP DamageToChampions | LP games only (LE has none) | yes | none, not imputed | to test (player group) |
| Vision score | LP VisionScore | LP games only | yes | none | to test |
| GD / XPD / CSD at 15 | needs the 15-minute frame (LE livestats window at 15:00) | not stored | would be (earlier games) | n/a | unavailable (not ingested; one more feed request per game) |
| Current-game DPM / GD@15 / duration | after the game | n/a | **no** | n/a | not allowed pre-match |

## Team / opponent
| Factor | Source | Coverage | Pre-match? | Missing | Status |
|---|---|---|---|---|---|
| Team strength (Elo from results) | history | all games | yes | starts at 1500 | used (lol-1 match strength, lol-2 winner draw) |
| Opponent-adjusted production (kills conceded) | history | all games | yes | league average | used (lol-1 opponent factor) |
| Kill pace (team kills for + against per minute) | history | all games | yes | league average | used (lol-2) |
| Game duration (team averages) | history | all games | yes | league average | used (lol-2) |
| Lane matchup (opponent's player in the same role) | history | all games | yes | none | to test |
| Team resource distribution (gold share by role) | LP Gold / LE gold | all games | yes | role prior | to test |
| Roster continuity (games played together) | history lineups | all games | yes | none | to test (also the "2-month roster" hypothesis) |

## Context
| Factor | Source | Coverage | Pre-match? | Missing | Status |
|---|---|---|---|---|---|
| Tournament patch | LP Patch / LE game version → patch | all games | the event's patch from its earlier games | unknown | to test (patch age) |
| League | LP Tournaments.League / LE league | all games | yes | none | used (B0 prior), to test as a group |
| Side | LP / LE side | all games | **no** for games 2+ (picked by the loser); game 1 sometimes announced | n/a | not allowed pre-match (unknown) |
| Game number | schedule ids | all games | yes (the scope is games 1..n) | none | used (the scope) |
| Series format (best-of) | schedule | all series | yes | none | used (the scope) |
| Stage (playoffs) | LP Tournaments.IsPlayoffs | LP tournaments | yes | none | to test |
| Schedule density (days since the last match) | history | all games | yes | none | to test |
| Travel | not in any source | none | n/a | n/a | unavailable (no verified source) |

## Draft
| Factor | Source | Coverage | Pre-match? | Missing | Status |
|---|---|---|---|---|---|
| Champion preference / comfort (earlier games' champion pool) | history (Champion) | all games | yes (as a pool) | none | to test (pre-draft scenario average) |
| Champion classes, composition | needs a champion-class table | not stored | pool only | n/a | unavailable (no class table ingested) |
| Final draft of the series | after the draft | n/a | **no** before the draft | n/a | not allowed pre-match. A post-draft forecast would be separate and timestamped, and the props close at the start of game 1. |
| Fearless draft rules of the event | event rules | not ingested | yes | n/a | unavailable (not ingested) |

## Market
| Factor | Source | Coverage | Pre-match? | Missing | Status |
|---|---|---|---|---|---|
| Match odds (verified) | Polymarket LoL series (lol/odds.py) | current markets only; no saved pre-match history | yes, live | n/a | unavailable historically (no timestamped history saved) |
| Prop-line differences (identical markets) | quote archive | from 2026-10-17 | yes | price-unverified stays unverified | forward only (archive too new) |
| Side prices | Underdog decimal_price (undocumented); PrizePicks none | from 2026-10-17 | yes | not 50/50 | research only (see center/prices.py) |
| Line movement | quote archive (timestamped) | from 2026-10-17 | yes | none | forward only |

## The owner's earlier decision rules (hypotheses, not rules)

Each is tested on the development split as an increment over the chosen projection (lol/evaluate.py, lol/hypotheses.py). None is hard-coded.
- an LCK B- ceiling (after four losses);
- a universal support-kills exclusion;
- a -150 to -400 favourite "sweet spot";
- an automatic fade above -1000;
- a fixed rematch downgrade;
- a two-month roster penalty;
- the one-kill discrepancy treated alike for every role and scope;
- post-spike fades and post-dud discounts;
- head-to-head, shrunk toward broader evidence.

A selection preference, such as no support props in a slip, belongs in the slip settings, not in the probabilities.

## Update 2026-10-19 (A7 / A7.1, lol/EVALUATION.md)

The owner's full list (role; draft; game length / pace; opponent and matchup; form and sample quality; individual
stats; team context; patch; situational; market) was tested as one combined increment over lol-1 and by group.
- **In the grade for kills and assists (lol-1f):** expected game length and kill pace of both teams; expected stomp; lane
  opponent; last-5 form and opponents' strength in the sample; KP%, kill / death / damage share, DPM, CS / min, vision;
  team form, playoffs, league group; patch age and games on the current patch; rest days, roster continuity, champion
  pool depth - on top of lol-1's role, form, team pace, opponent factor and Elo win chance.
- **Live grade for kills and assists since 2026-11-09: lpf-1** - a full-history booster (93 pre-match features, artifact
  `lol/lpf1_model.json.gz`) started from the lol-1f projection above; it passed the owner's walk-forward hit-rate rule
  (lol/EVALUATION.md "lpf-1 live path"). Rollback `LOL_LPF1=0` = lol-1f (only while so-1 is off: with
  `LOL_SCOPE_OFFSET=1` the rollback also needs `LOL_SCOPE_OFFSET=0`; so-1 is shadow / off by default).
- **Deaths:** the same features are measured and shown at weight 0 (the combined model failed on Maps 1-3).
- **Market team win odds:** saved with every forecast from 2026-10-19 (Polymarket match winner); judged forward.
- **Still unavailable before props close:** the draft (champions, composition, counter-picks), side for games 2+, GD / XPD / CSD
  at 15 (not ingested), champion patch strength (no table), travel and coaching (no source).
