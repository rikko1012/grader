"""The factor data contract, version "sports-factors/1": every factor on the owner's NBA / NFL list, the ONE source each
is taken from, and how. Other code (this branch's board and log, or a separate Scout implementation) reads factors by
these keys and checks CONTRACT before using them; a changed meaning gets a new key or a new contract version, never a
silent edit.

A factors payload (factors.compute) is:
  {"contract": "sports-factors/1", "as_of": ISO time (only games that STARTED before it are used),
   "league", "event_id" (ESPN), "athlete_id" (ESPN), "player_key", "team_id", "opp_id",
   "factors": {key: number | string | bool | None}, "missing": [keys with no value and why: {key: reason}]}
Ratios are 0-1 unless the unit says "%" or "per 100" etc. Windows: L3 / L5 / L10 = last N games the player played,
season = current season to date (a player with fewer than 5 NBA / 3 NFL games this season: current + previous season;
a team with fewer than 10 NBA / 4 NFL games: its last 20 / 8), prior = the previous season, ewm = recency-weighted
(half-life 10 games). League baselines (league pace, defense vs position, defense ranks) use the current season once
it has 150 NBA / 32 NFL games, else the current and previous seasons.

Status: "collected" = a field the source publishes; "derived" = computed from stored data of that one source;
"proxy" = the closest measurable stand-in for a factor no source we hold publishes (named so); "unavailable" = no
source we hold has it (the reason names what would). Every stat comes from exactly one source: box-score totals are
never recomputed from play-by-play, and game lines are not duplicated across feeds.
"""

CONTRACT = "sports-factors/1"

BOX = "ESPN box score"
PBP = "ESPN play-by-play (derived)"
PICK = "ESPN pickcenter (odds provider named in the row; DraftKings on 2026-10-23)"
KALSHI = "Kalshi game markets"
INJ = "ESPN injury report"
SCHED = "ESPN schedule"
VENUE = "ESPN venue"
POWER = "ESPN BPI / FPI"
WEATHER = "National Weather Service forecast (api.weather.gov)"
BOOKS = "FanDuel and BetRivers own feeds (books store)"
SHARP = "Kalshi and Polymarket player-prop markets"
ESPN_DK = "ESPN-supplied DraftKings prop lines (lines only, no prices)"
PICKEM = "PrizePicks / Underdog boards"
CHARTING = "charting / tracking data (PFF, NFL Next Gen Stats, Second Spectrum) - not held"
NFLVERSE = ("nflverse (snap counts from Pro-Football-Reference; nflverse player-week stats) - the installed history "
            "copy, refreshed every 12 h")

# (key, group, label, source, status, how)
_NBA = [
    ("min_proj", "Minutes & usage", "Projected minutes", BOX, "derived",
     "ewm minutes, the role-change and teammate-out adjustments applied (factors.nba_minutes)"),
    ("min_l5", "Minutes & usage", "Minutes L5", BOX, "derived", "mean"),
    ("min_l10", "Minutes & usage", "Minutes L10", BOX, "derived", "mean"),
    ("min_season", "Minutes & usage", "Minutes season", BOX, "derived", "mean"),
    ("usg_l10", "Minutes & usage", "Usage rate L10 (%)", BOX, "derived",
     "100 * (FGA + 0.44 FTA + TOV) * (team min / 5) / (min * team (FGA + 0.44 FTA + TOV)), sums over the window"),
    ("usg_season", "Minutes & usage", "Usage rate season (%)", BOX, "derived", "as above"),
    ("fga_per36", "Minutes & usage", "Field-goal attempts per 36", BOX, "derived", "season"),
    ("fg3a_per36", "Minutes & usage", "3-point attempts per 36", BOX, "derived", "season"),
    ("fta_per36", "Minutes & usage", "Free-throw attempts per 36", BOX, "derived", "season"),
    ("ast_pct", "Minutes & usage", "Assist rate (%)", BOX, "derived",
     "100 * AST / ((min / (team min / 5)) * team FGM - FGM), season"),
    ("role_change", "Minutes & usage", "Role change (minutes L3 - L10)", BOX, "derived", "minutes"),
    ("pace_team", "Pace", "Team pace L10 (possessions / 48)", BOX, "derived",
     "possessions = 0.5 * (both teams' FGA + 0.4 FTA - 1.07 * OREB% * missed FG + TOV), per 48"),
    ("pace_opp", "Pace", "Opponent pace L10", BOX, "derived", "as above"),
    ("pace_expected", "Pace", "Expected game pace", BOX, "derived", "team + opponent pace - league pace"),
    ("game_total", "Pace", "Game total (points)", PICK, "collected", "latest pre-game snapshot"),
    ("team_total", "Pace", "Implied team total", PICK, "derived", "total / 2 -/+ spread / 2"),
    ("spread", "Pace", "Point spread (team's)", PICK, "collected", "negative = favourite"),
    ("dvp", "Defensive matchup", "Defense vs position for this stat (1 = league average)", BOX, "derived",
     "opponent's allowed stat per game to the player's position group (G / F / C) over the league average, "
     "shrunk toward 1 by games (k = 10), current season"),
    ("opp_drtg", "Defensive matchup", "Opponent defensive rating (points / 100)", BOX, "derived", "season"),
    ("opp_fg3a_allowed_per100", "Defensive matchup", "Opponent 3PA allowed per 100", BOX, "derived", "season"),
    ("opp_rim_fga_allowed_per100", "Defensive matchup", "Opponent rim attempts allowed per 100", PBP, "derived",
     "season"),
    ("opp_rim_fg_allowed", "Defensive matchup", "Opponent FG% allowed at the rim", PBP, "derived", "season"),
    ("opp_mid_fg_allowed", "Defensive matchup", "Opponent FG% allowed in the mid-range", PBP, "derived", "season"),
    ("opp_fg3_allowed", "Defensive matchup", "Opponent 3P% allowed", BOX, "derived", "season"),
    ("rim_share", "Shot profile", "Share of FGA at the rim", PBP, "derived", "season (prior when < 5 games)"),
    ("mid_share", "Shot profile", "Share of FGA from mid-range", PBP, "derived", "season"),
    ("three_share", "Shot profile", "Share of FGA from 3", BOX, "derived", "3PA / FGA, season"),
    ("corner3_share", "Shot profile", "Share of 3PA from the corners", PBP, "derived", "season"),
    ("spotup_share", "Shot profile", "Catch-and-shoot proxy: plain jump shots / FGA", PBP, "proxy",
     "ESPN shot type 'Jump Shot'; touch tracking (true catch-and-shoot) is " + CHARTING),
    ("pullup_share", "Shot profile", "Pull-up / step-back jumpers / FGA", PBP, "derived", "season"),
    ("assisted_share", "Shot profile", "Share of made FG assisted", PBP, "derived", "season"),
    ("ts_pct", "Shot profile", "True shooting %", BOX, "derived", "PTS / (2 * (FGA + 0.44 FTA)), season"),
    ("efg_pct", "Shot profile", "Effective FG %", BOX, "derived", "(FGM + 0.5 * 3PM) / FGA, season"),
    ("ft_rate", "Shot profile", "Free-throw rate", BOX, "derived", "FTA / FGA, season"),
    ("rest_days", "Role & rest", "Days since the team's last game", SCHED, "derived", ""),
    ("b2b", "Role & rest", "Second night of a back-to-back", SCHED, "derived", ""),
    ("games_last4d", "Role & rest", "Team games in the previous 4 days", SCHED, "derived", ""),
    ("opp_rest_days", "Role & rest", "Opponent days of rest", SCHED, "derived", ""),
    ("blowout_prob", "Role & rest", "Chance either team wins by 15+", KALSHI, "derived",
     "spread ladders: P(home by > 14.5) + P(away by > 14.5) (nearest listed strike, named in blowout_strike)"),
    ("win_prob", "Role & rest", "Team win chance", KALSHI, "derived", "winner markets, midpoints normalised"),
    ("home", "Role & rest", "Home game", SCHED, "collected", ""),
    ("starter_rate_l10", "Role & rest", "Started, share of L10", BOX, "derived", ""),
    ("started_last", "Role & rest", "Started the last game", BOX, "collected", ""),
    ("teammates_out", "Role & rest", "Teammates Out / Doubtful", INJ, "collected", "latest report before as_of"),
    ("vacated_min", "Role & rest", "Minutes per game of teammates out", BOX, "derived", "their season minutes"),
    ("vacated_usage", "Role & rest", "Team usage share of teammates out", BOX, "derived", "their season usage share"),
    ("with_without_delta", "Role & rest", "Stat per 36 without the top teammate out minus with", BOX, "derived",
     "games he DNP'd (box) vs played, current + prior season, >= 3 games without"),
    ("rotation_depth", "Role & rest", "Coaching rotation: players averaging 12+ min L10", BOX, "proxy",
     "coaching tendencies measured by the rotation they produce"),
    ("starters_min_share", "Role & rest", "Coaching rotation: starters' share of minutes L10", BOX, "proxy", ""),
    ("on_off_net", "Advanced", "On-court minus off-court net points per 48", PBP, "derived",
     "lineups from substitutions; who starts a period is inferred (first action or first substitution), season"),
    ("rebound_chances", "Advanced", "Rebound chances", None, "unavailable", "player tracking: " + CHARTING),
    ("reb_per_live_miss_proxy", "Advanced", "Rebounds per live miss while on court (rebound-chance PROXY)", PBP,
     "proxy", "box rebounds / every reboundable miss while he was on court; true chances need " + CHARTING),
    ("oreb_pct", "Advanced", "Offensive rebound rate (%)", BOX, "derived", "season"),
    ("dreb_pct", "Advanced", "Defensive rebound rate (%)", BOX, "derived", "season"),
    ("opp_oreb_pct", "Advanced", "Opponent offensive rebound rate", BOX, "derived", "season"),
    ("opp_dreb_pct", "Advanced", "Opponent defensive rebound rate", BOX, "derived", "season"),
    ("potential_assists", "Advanced", "Potential assists", None, "unavailable", CHARTING),
    ("defender_matchup", "Defensive matchup", "Likely defender", None, "unavailable", "matchup tracking: " + CHARTING),
    ("team_rating", "Advanced", "Team BPI", POWER, "collected", "latest daily snapshot"),
    ("opp_rating", "Advanced", "Opponent BPI", POWER, "collected", ""),
]
_NFL = [
    ("snap_share_l3", "Usage & role", "Snap share L3 (offensive snaps)", NFLVERSE, "collected",
     "snap_counts offense_pct, mean of the last 3 games"),
    ("snap_share_season", "Usage & role", "Snap share season", NFLVERSE, "collected",
     "snap_counts offense_pct, season mean"),
    ("air_yds_share", "Usage & role", "Air-yards share (all targets) L5", NFLVERSE, "collected",
     "stats_player_week air_yards_share, mean of the last 5 games (incompletions included)"),
    ("wopr", "Usage & role", "WOPR (1.5 x target share + 0.7 x air-yards share) L5", NFLVERSE, "collected",
     "stats_player_week wopr, mean of the last 5 games"),
    ("route_participation", "Usage & role", "Route participation", None, "unavailable", CHARTING),
    ("tprr", "Usage & role", "Targets per route run", None, "unavailable", CHARTING),
    ("alignment", "Usage & role", "Alignment (slot / wide / inline)", None, "unavailable", CHARTING),
    ("involvement_rate", "Usage & role", "Involvement: (targets + carries) / team plays", BOX, "proxy",
     "stand-in for snap share; team plays from " + PBP),
    ("tgt_share_l3", "Usage & role", "Target share L3", BOX, "derived", "player targets / team targets"),
    ("tgt_share_season", "Usage & role", "Target share season", BOX, "derived", ""),
    ("air_share", "Usage & role", "Completed air-yards share", PBP, "proxy",
     "air yards on completions (yards - yards after catch); incomplete passes' air yards are " + CHARTING),
    ("deep_tgt_share", "Usage & role", "Deep share of targets (15+ air yards)", PBP, "derived", "season"),
    ("carry_share_l3", "Usage & role", "Carry share L3", BOX, "derived", ""),
    ("carry_share_season", "Usage & role", "Carry share season", BOX, "derived", ""),
    ("rb_committee", "Usage & role", "Share of the team's RB carries", BOX, "derived", "position from ESPN rosters"),
    ("rz_tgt_share", "Usage & role", "Red-zone target share", PBP, "derived", "inside the 20, season"),
    ("rz_carry_share", "Usage & role", "Red-zone carry share", PBP, "derived", ""),
    ("i5_carry_share", "Usage & role", "Inside-5 carry share", PBP, "derived", ""),
    ("usage_trend", "Usage & role", "Usage trend (L3 - season share)", BOX, "derived", "targets or carries"),
    ("coverage_scheme", "Matchup", "Opponent man / zone, coverage shells", None, "unavailable", CHARTING),
    ("cb_matchup", "Matchup", "Defender matchup", None, "unavailable", CHARTING),
    ("run_scheme", "Matchup", "Zone / gap run scheme", None, "unavailable", CHARTING),
    ("opp_pos_allowed", "Matchup", "Opponent yards allowed to the position (1 = league average)", BOX, "derived",
     "receiving yards to WR / TE / RB, rushing yards to RB, per game over league average, shrunk (k = 4)"),
    ("opp_pressure", "Matchup", "Opponent pressure proxy: (QB hits + sacks) / dropbacks", BOX, "proxy",
     "true pressure rate is " + CHARTING),
    ("qb_deep_rate", "QB", "Deep share of attempts", PBP, "derived", ""),
    ("qb_left_middle_right", "QB", "Attempts left / middle / right", PBP, "derived", "string L/M/R shares"),
    ("qb_air_per_cmp", "QB", "Completed air yards per completion (aDOT proxy)", PBP, "proxy",
     "true aDOT needs incomplete passes' depth: " + CHARTING),
    ("qb_epa_db", "QB", "EPA per dropback", PBP, "derived", "own expected-points fit (ep_nfl.json)"),
    ("qb_success_db", "QB", "Success rate per dropback", PBP, "derived", ""),
    ("qb_scramble_rate", "QB", "Scramble rate", PBP, "derived", "scrambles / dropbacks"),
    ("qb_sack_rate", "QB", "Sack rate", PBP, "derived", ""),
    ("qb_adj_qbr", "QB", "ESPN adjusted QBR (season mean)", BOX, "collected", ""),
    ("spread", "Game script", "Point spread (team's)", PICK, "collected", ""),
    ("game_total", "Game script", "Game total", PICK, "collected", ""),
    ("team_total", "Game script", "Implied team total", PICK, "derived", "total / 2 -/+ spread / 2"),
    ("win_prob", "Game script", "Team win chance", KALSHI, "derived", ""),
    ("blowout_prob", "Game script", "Chance either team wins by 14+", KALSHI, "derived", "nearest strike named"),
    ("neutral_pass_rate", "Game script", "Neutral-situation pass rate", PBP, "derived",
     "win probability 20-80%, not the last 2 min of a half, season"),
    ("pace_neutral_s", "Game script", "Neutral seconds per snap", PBP, "derived", ""),
    ("plays_pg", "Game script", "Team snaps per game L5", PBP, "derived", ""),
    ("indoor", "Environment", "Indoor stadium", VENUE, "collected", ""),
    ("weather_temp_f", "Environment", "Forecast temperature at kickoff (F)", WEATHER, "collected", "outdoor US"),
    ("weather_wind_mph", "Environment", "Forecast wind (mph)", WEATHER, "collected", ""),
    ("weather_precip_pct", "Environment", "Forecast chance of precipitation (%)", WEATHER, "collected", ""),
    ("rest_days", "Environment", "Days since the team's last game", SCHED, "derived", ""),
    ("short_week", "Environment", "Short week (< 6 days)", SCHED, "derived", ""),
    ("off_bye", "Environment", "Off a bye (10+ days)", SCHED, "derived", ""),
    ("teammates_out", "Environment", "Teammates Out / Doubtful", INJ, "collected", ""),
    ("vacated_tgt_share", "Environment", "Target share of teammates out", BOX, "derived", ""),
    ("team_epa_play", "Efficiency", "Team offensive EPA per play, season", PBP, "derived", ""),
    ("team_success", "Efficiency", "Team offensive success rate", PBP, "derived", ""),
    ("team_explosive", "Efficiency", "Team explosive-play rate", PBP, "derived", "rush 10+, pass 20+"),
    ("opp_def_epa_play", "Efficiency", "Opponent defensive EPA allowed per play", PBP, "derived", ""),
    ("opp_def_success", "Efficiency", "Opponent success rate allowed", PBP, "derived", ""),
    ("opp_def_explosive", "Efficiency", "Opponent explosive rate allowed", PBP, "derived", ""),
    ("opp_def_rank", "Efficiency", "Opponent defense rank by EPA allowed (1 = best)", PBP, "derived", ""),
    ("rz_td_rate", "Efficiency", "Team red-zone touchdown rate", PBP, "derived", "drives reaching the 20"),
    ("dvoa", "Efficiency", "DVOA", None, "unavailable",
     "proprietary (FTN); opponent-adjusted EPA above is the stand-in"),
    ("next_gen", "Efficiency", "Next Gen Stats (separation, CPOE, ...)", None, "unavailable", CHARTING),
    ("team_rating", "Efficiency", "Team FPI", POWER, "collected", ""),
    ("opp_rating", "Efficiency", "Opponent FPI", POWER, "collected", ""),
]
_COMMON = [
    ("stat_l3", "Form", "This stat, mean L3", BOX, "derived", ""),
    ("stat_l5", "Form", "This stat, mean L5", BOX, "derived", ""),
    ("stat_l10", "Form", "This stat, mean L10", BOX, "derived", ""),
    ("stat_season", "Form", "This stat, season mean", BOX, "derived", ""),
    ("stat_prior", "Form", "This stat, prior-season mean", BOX, "derived", ""),
    ("stat_home_away", "Form", "This stat, mean at this venue type (home / away), season + prior", BOX, "derived", ""),
    ("games_season", "Form", "Games played this season", BOX, "derived", ""),
    ("dk_line", "Market", "DraftKings line (ESPN-supplied)", ESPN_DK, "collected", "same game, stat, full game"),
    ("dk_line_open", "Market", "DraftKings opening line (ESPN-supplied)", ESPN_DK, "collected", ""),
    ("dk_line_move", "Market", "DraftKings line move, open to now", ESPN_DK, "derived", ""),
    ("book_consensus", "Market", "2+ book no-vig chance at the exact line", BOOKS, "derived",
     "the board's books column (compare.py); not repeated in this payload"),
    ("sharp_chance", "Market", "Exchange chance (Kalshi / Polymarket)", SHARP, "derived",
     "the board's sharp column; not repeated here"),
    ("pickem_line", "Market", "Pick'em line and movement", PICKEM, "collected", "the board row; not repeated here"),
]

REGISTRY = {"nba": {k: dict(zip(("key", "group", "label", "source", "status", "how"), (k, *rest)))
                    for k, *rest in _NBA + _COMMON},
            "nfl": {k: dict(zip(("key", "group", "label", "source", "status", "how"), (k, *rest)))
                    for k, *rest in _NFL + _COMMON}}
# market columns already carried by the board row: listed for completeness, never duplicated into the payload
ON_ROW = {"book_consensus", "sharp_chance", "pickem_line"}


def describe(league):
    return {"contract": CONTRACT, "league": league, "factors": list(REGISTRY[league].values())}
