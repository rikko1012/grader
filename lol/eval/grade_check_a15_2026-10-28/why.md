units 529

## components of the projection (deduped units; lambda_game = rate x team x opp x strength x factor_mult)

### kills: mean of each factor over 270 units
  team_factor: mean 0.9577, geometric mean 0.9556, min 0.861, max 1.196
  opp_factor: mean 0.8621, geometric mean 0.8600, min 0.739, max 0.963
  strength_factor: mean 0.9768, geometric mean 0.9763, min 0.887, max 1.037
  factor_mult: mean 1.0267, geometric mean 1.0250, min 0.856, max 1.199
  p_win: mean 0.4996, geometric mean nan, min 0.230, max 0.717
  lol-1 lambda vs actual: bias +0.060 (+1.8%), MAE 1.946 | lol-1f: bias +0.240 (+7.1%), MAE 1.971  (n 250)
  by team_factor tercile:
    [0.861, 0.934] n 90: factor mean 0.898, bias -0.175 (-3.9%)
    [0.934, 0.969] n 90: factor mean 0.949, bias +0.511 (+18.8%)
    [0.969, 1.196] n 90: factor mean 1.025, bias +0.293 (+7.4%)
  by opp_factor tercile:
    [0.739, 0.834] n 90: factor mean 0.797, bias +0.015 (+0.4%)
    [0.834, 0.889] n 90: factor mean 0.855, bias +0.292 (+7.6%)
    [0.889, 0.963] n 90: factor mean 0.934, bias +0.322 (+8.8%)
  by strength_factor tercile:
    [0.887, 0.962] n 90: factor mean 0.940, bias -0.383 (-8.5%)
    [0.962, 0.991] n 90: factor mean 0.975, bias +0.632 (+22.1%)
    [0.991, 1.037] n 90: factor mean 1.016, bias +0.380 (+10.2%)
  by factor_mult tercile:
    [0.856, 1.001] n 90: factor mean 0.966, bias -0.313 (-9.0%)
    [1.001, 1.047] n 90: factor mean 1.025, bias +0.513 (+17.2%)
    [1.047, 1.199] n 90: factor mean 1.089, bias +0.429 (+9.2%)

### assists: mean of each factor over 259 units
  team_factor: mean 0.9613, geometric mean 0.9607, min 0.896, max 1.041
  opp_factor: mean 0.8959, geometric mean 0.8947, min 0.809, max 0.971
  strength_factor: mean 0.9886, geometric mean 0.9878, min 0.910, max 1.058
  factor_mult: mean 1.0510, geometric mean 1.0497, min 0.907, max 1.186
  p_win: mean 0.5006, geometric mean nan, min 0.283, max 0.717
  lol-1 lambda vs actual: bias -0.289 (-3.8%), MAE 3.519 | lol-1f: bias +0.123 (+1.6%), MAE 3.607  (n 240)
  by team_factor tercile:
    [0.896, 0.948] n 84: factor mean 0.921, bias -0.146 (-1.4%)
    [0.948, 0.974] n 85: factor mean 0.960, bias +0.473 (+6.7%)
    [0.974, 1.041] n 90: factor mean 1.001, bias +0.771 (+11.2%)
  by opp_factor tercile:
    [0.809, 0.873] n 84: factor mean 0.843, bias -0.578 (-6.5%)
    [0.873, 0.922] n 85: factor mean 0.891, bias +0.992 (+14.4%)
    [0.922, 0.971] n 90: factor mean 0.950, bias +0.683 (+8.1%)
  by strength_factor tercile:
    [0.910, 0.968] n 86: factor mean 0.943, bias -0.427 (-5.0%)
    [0.968, 1.009] n 86: factor mean 0.988, bias +0.772 (+11.7%)
    [1.009, 1.058] n 87: factor mean 1.034, bias +0.778 (+8.6%)
  by factor_mult tercile:
    [0.907, 1.025] n 86: factor mean 0.993, bias -0.283 (-3.0%)
    [1.025, 1.073] n 86: factor mean 1.050, bias -0.136 (-1.6%)
    [1.073, 1.186] n 87: factor mean 1.110, bias +1.533 (+23.4%)

## lol-1f factor effects (mean effect % per factor, kills units)
  combined kill pace (both teams): mean +4.86%  (n 250)
  expected game length (both teams): mean -3.01%  (n 250)
  vision per minute: mean +2.34%  (n 250)
  lane opponent's deaths per game: mean -1.77%  (n 250)
  no earlier games: mean +1.42%  (n 250)
  strength of the opponents in his last 10: mean +1.17%  (n 250)
  lane opponent unknown: mean -1.09%  (n 250)
  damage per minute: mean -0.73%  (n 250)
  team form (last 10): mean +0.69%  (n 250)
  days since the patch: mean +0.54%  (n 250)
  kills last 5 vs his average: mean -0.42%  (n 250)
  playoffs: mean +0.35%  (n 250)
  patch unknown: mean -0.32%  (n 250)
  damage share: mean -0.28%  (n 250)
  assists last 5 vs his average: mean -0.21%  (n 250)
  CS per minute: mean -0.20%  (n 250)
  deaths last 5 vs his average: mean -0.19%  (n 250)
  lane opponent's kills per game: mean -0.19%  (n 250)
  roster continuity: mean +0.19%  (n 250)
  days since the team's last game: mean -0.15%  (n 250)
  his games on the current patch: mean -0.11%  (n 250)
  LCK: mean +0.10%  (n 250)
  LEC: mean +0.05%  (n 250)
  death share: mean +0.05%  (n 250)
  expected stomp (team strength gap): mean +0.02%  (n 250)
  kill share: mean +0.01%  (n 250)
  champion pool depth: mean -0.00%  (n 250)
  kill participation: mean -0.00%  (n 250)
  LPL: mean +0.00%  (n 250)
  Americas leagues: mean +0.00%  (n 250)

## team kills per game: Worlds 2026 games vs the same teams' games in the 245 days before Oct 17 (all leagues)
| team | Worlds games | kills/g | conceded/g | earlier games | earlier kills/g | conceded/g | earlier league |
|---|---|---|---|---|---|---|---|
| Karmine Corp | 14 | 13.5 | 10.4 | 127 | 16.0 | 13.4 | Mid-Season Invitational |
| MVK Esports | 13 | 12.9 | 13.4 | 62 | 13.8 | 15.3 | League of Legends Championship Pacific |
| FURIA | 11 | 11.5 | 15.4 | 91 | 15.3 | 11.4 | Mid-Season Invitational |
| Cloud9 | 9 | 12.6 | 11.6 | 91 | 13.4 | 12.3 | League of Legends Championship Series |
| Gen.G Esports | 5 | 15.8 | 8.6 | 122 | 16.4 | 12.5 | LoL Champions Korea |
| LYON | 5 | 12.6 | 11.2 | 115 | 14.9 | 12.2 | Mid-Season Invitational |
| Movistar KOI | 4 | 12.5 | 13.5 | 100 | 15.3 | 14.1 | LoL EMEA Championship |
| Anyone's Legend | 4 | 9.0 | 15.8 | 115 | 15.4 | 12.1 | Tencent LoL Pro League |
| Dplus KIA | 4 | 12.8 | 7.2 | 137 | 14.2 | 14.0 | LoL Champions Korea |
| Team Liquid Alienware | 4 | 11.5 | 13.0 | 93 | 15.4 | 11.2 | Mid-Season Invitational |
| LOS | 4 | 12.0 | 15.8 | 105 | 15.8 | 12.8 | Esports World Cup |
| Team Secret Whales | 4 | 10.0 | 19.8 | 79 | 18.1 | 13.0 | Mid-Season Invitational |
| Invictus Gaming | 3 | 20.0 | 14.3 | 95 | 15.1 | 15.2 | World Championship |
| Hanwha Life Esports | 3 | 13.3 | 19.3 | 137 | 18.0 | 15.4 | Mid-Season Invitational |
| TOP ESPORTS | 3 | 14.3 | 13.0 | 110 | 15.7 | 15.3 | World Championship |
| BILIBILI GAMING | 3 | 15.0 | 10.3 | 152 | 18.9 | 12.8 | Tencent LoL Pro League |
| G2 Esports | 3 | 12.0 | 11.7 | 120 | 16.3 | 13.3 | Mid-Season Invitational |
| CTBC Flying Oyster | 3 | 8.3 | 14.7 | 70 | 15.5 | 16.4 | League of Legends Championship Pacific |
| T1 | 3 | 13.7 | 6.3 | 151 | 17.2 | 15.3 | Mid-Season Invitational |
ALL: Worlds 12.75 kills per team-game (102 team-games); earlier 15.98 (2072 team-games); fit prior team_kills 17.04
Worlds mean duration 32.9 min

## other international events in the history: event kills per team-game vs the fit prior 17.04
  2026 Academic Esports World Tournament Sydney: 14 games, 20.89 kills per team-game, 25.8 min
  Esports World Cup 2026: 51 games, 16.31 kills per team-game, 32.6 min
  Esports World Cup 2026 Online Qualifier: Asia-Pacific: 35 games, 15.09 kills per team-game, 31.4 min
  Esports World Cup 2026 Online Qualifier: EMEA: 49 games, 15.30 kills per team-game, 33.1 min
  Esports World Cup 2026 Online Qualifier: Korea: 34 games, 17.46 kills per team-game, 30.5 min
  Esports World Cup 2026 Online Qualifier: North America: 33 games, 16.18 kills per team-game, 32.4 min
  Esports World Cup 2026 Online Qualifier: South America: 30 games, 14.93 kills per team-game, 33.1 min
  First Stand 2026: 45 games, 13.97 kills per team-game, 30.5 min
  LCK 2026 Road to MSI: 20 games, 14.38 kills per team-game, 34.5 min
  MSI 2026: 71 games, 16.63 kills per team-game, 32.5 min
  World Star Challengers Invitational 2026: 74 games, 17.28 kills per team-game, 28.7 min
  Worlds 2025 Main Event: 5 games, 16.60 kills per team-game, 36.4 min
  Worlds 2026 Main Event: 29 games, 12.78 kills per team-game, 33.1 min
  Worlds 2026 Play-In: 22 games, 12.70 kills per team-game, 32.6 min

## projected team kill pool (sum of 5 kills lambda_game) vs actual team kills, Worlds Main Event Map 1
team-series with 5 kills units 40: projected pool mean 14.40, actual team kills mean 13.00, ratio 1.108
