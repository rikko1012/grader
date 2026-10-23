"""Scout Sports feature registry fr-1 (owner brief 5 + the owner's NFL / NBA prop-factor list of 2026-10-23).

EVERY owner factor is listed. Each entry: id, sport, factor (owner's wording, shortened), definition, source (data we
actually have), availability (when it is known relative to the forecast), missingness, hypothesis, treatment, status,
and observed_effect (filled from the registered study, sports/research/results/model_study_*.json - tune-partition CRPS
change when the feature is added alone to the sm-1 base; negative = better; with its game-clustered 95% interval).

status
  "available + candidate"      computed point-in-time from ds-1 and tested by the registered protocol
  "available (market track)"   a market input: tested only in the market-informed track, never as statistical-only
  "available historically only" computable on history, but no forward source in this build -> cannot be deployed
  "derivable later"            public data exists but is not ingested / not point-in-time yet
  "unavailable"                needs licensed data (PFF grades, coverage shells by defender, Next Gen Stats separation,
                               DVOA, shadow-coverage / defender assignment, on/off) - stays ABSENT, never imputed

Rules: a factor gets weight only through the chronological selection protocol (REGISTER.md 5); a model driver is shown
only when its contribution is computed (sm-1's GLM terms are multiplicative and exactly decomposable: driver_i =
exp(beta_i * z_i)); unknown stays unknown (missing -> train mean + missing indicator, never "0 = no").
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REGISTRY_VERSION = "fr-1"
RESULTS = Path(__file__).resolve().parent / "research" / "results"

A, M, H, D, U = ("available + candidate", "available (market track)", "available historically only", "derivable later",
                 "unavailable")
PIT_OWN = "before the game: the player's earlier games (known_at = start + 4 h NBA / 6 h NFL <= forecast_at)"
PIT_TEAM = "before the game: the team's / opponent's earlier games"
MISS_HIST = "no earlier games -> missing -> train mean + missing indicator (thin-history rows are a declared slice)"


def _f(id, sport, factor, definition, source, availability, missingness, hypothesis, treatment, status, markets=None,
       column=None):
    return {"id": id, "sport": sport, "factor": factor, "definition": definition, "source": source,
            "availability": availability, "missingness": missingness, "hypothesis": hypothesis,
            "treatment": treatment, "status": status, "markets": markets, "column": column}


NBA_ALL = ["points", "rebounds", "assists", "threes"]
NFL_ALL = ["pass_yds", "rush_yds", "rec_yds", "rush_att", "rec"]
BASE = "part of the sm-1 base (opportunity x rate), not a separate weight"
CAND = "candidate GLM term; kept only if the registered tune test passes"

REGISTRY = [
    # ------------------------------------------------------------------------------------------- NBA: minutes & usage
    _f("nba.expected_minutes", "nba", "Projected minutes (L5-L10 trend > season)", "EW mean of prior minutes, half-life "
       "chosen on tune from {3,5,10} games", "ESPN box scores (ds-1)", PIT_OWN, MISS_HIST, "minutes drive every counting "
       "stat", BASE, A, NBA_ALL),
    _f("nba.minutes_trend", "nba", "Minutes trend L5 vs longer window", "mean minutes of last 5 / EW minutes - 1",
       "ESPN box", PIT_OWN, MISS_HIST, "recent role change not yet in the EW mean", CAND, A, NBA_ALL, "minutes_trend"),
    _f("nba.per_minute_rate", "nba", "Shot attempts / production per minute", "empirical-Bayes per-minute rate of the "
       "market stat (half-life chosen on tune), shrunk to the position prior with k fitted on train", "ESPN box",
       PIT_OWN, MISS_HIST, "conversion of minutes into the stat", BASE, A, NBA_ALL),
    _f("nba.usage", "nba", "USG% (usage)", "EW (FGA + 0.44 FTA + TOV) per minute - a box-score usage proxy (true USG% "
       "needs team possessions while on court)", "ESPN box", PIT_OWN, MISS_HIST, "higher usage -> more points / shots",
       CAND, A, ["points", "threes", "assists"], "usage"),
    _f("nba.shot_trend", "nba", "Recent shot attempts", "last-5 FGA per minute / EW FGA per minute - 1", "ESPN box",
       PIT_OWN, MISS_HIST, "a recent jump in shots persists", CAND, A, ["points", "threes"], "shot_trend"),
    _f("nba.potential_assists", "nba", "Assist rate / potential assists", "potential assists need tracking data",
       "NBA tracking (not ingested)", "-", "-", "assist opportunities", "absent", U, ["assists"]),
    _f("nba.role_stability", "nba", "Role stability / sudden change", "started previous game minus share of starts in "
       "the last 10", "ESPN box (starter flag)", PIT_OWN, MISS_HIST, "a new starter plays more", CAND, A, NBA_ALL,
       "role_change"),
    _f("nba.starter_prev", "nba", "Starter / bench", "started his previous game (1/0)", "ESPN box", PIT_OWN, MISS_HIST,
       "starters' rates differ", CAND, A, NBA_ALL, "starter_prev"),
    # ------------------------------------------------------------------------------------------------------- NBA pace
    _f("nba.team_pace", "nba", "Team pace", "mean possessions per 48 of the team's last 10 games (FGA - OREB + TOV + "
       "0.44 FTA)", "ESPN team box", PIT_TEAM, "first 3 team games missing", "more possessions -> more stats", CAND, A,
       NBA_ALL, "team_pace"),
    _f("nba.opp_pace", "nba", "Opponent pace / projected game pace", "opponent's possessions per 48, last 10",
       "ESPN team box", PIT_TEAM, "first 3 games missing", "a fast opponent adds possessions", CAND, A, NBA_ALL,
       "opp_pace"),
    _f("nba.game_total", "nba", "Game total (market)", "pre-game over/under of the game", "ESPN pickcenter (historical "
       "rows mostly empty); forward: Kalshi / books game totals when ingested", "pre-game market (near closing)",
       "missing for most historical games -> market track only if >= 50% coverage", "high totals = more scoring",
       "market track only", M, NBA_ALL, "game_total"),
    # --------------------------------------------------------------------------------------------- NBA defence
    _f("nba.opp_positional_defence", "nba", "Positional defence (DVP)", "opponent's allowed market stat per minute to "
       "the player's position group (G/F/C) over its last 15 games", "ESPN box", PIT_TEAM, "first 5 games missing",
       "weak positional defence -> more", CAND, A, NBA_ALL, "opp_pos_allow"),
    _f("nba.likely_defender", "nba", "Likely defender + duration", "matchup tracking", "NBA tracking (not available)",
       "-", "-", "-", "absent", U),
    _f("nba.opp_drtg_zone", "nba", "Opponent DRtg overall and by zone", "team defensive rating by shot zone needs shot "
       "location data", "ESPN play-by-play shot charts (not ingested)", "-", "-", "-", "absent", D),
    _f("nba.switch_help", "nba", "Switchability / help", "scheme tracking", "not available", "-", "-", "-", "absent", U),
    _f("nba.opp_3pa_allowed", "nba", "Opponent 3PA allowed / contest rate", "3PA allowed derivable from box; contest "
       "rate needs tracking", "ESPN box (3PA allowed derivable later); tracking unavailable", PIT_TEAM, "-",
       "more threes allowed -> more threes", "absent in fr-1", D, ["threes"]),
    _f("nba.paint_perimeter", "nba", "Paint vs perimeter strength", "shot location data", "not ingested", "-", "-", "-",
       "absent", D),
    # ---------------------------------------------------------------------------------------------- NBA shot profile
    _f("nba.shot_profile", "nba", "Rim / mid / 3PT rate; C&S vs pull-up", "3PA share is box-derivable; zones and "
       "shot types need play-by-play / tracking", "ESPN box (3PA share only)", PIT_OWN, "-", "profile vs defence", "the "
       "threes mixture uses 3PA and 3P% directly (base)", D, ["threes", "points"]),
    _f("nba.ts_efg_ftr", "nba", "TS%, eFG%, FT rate", "box-derivable shooting efficiency", "ESPN box", PIT_OWN, MISS_HIST,
       "efficiency persists", "inside the EB per-minute rate (base); not a separate term in fr-1", D, ["points"]),
    # ---------------------------------------------------------------------------------- NBA role / rest / situation
    _f("nba.back_to_back", "nba", "Back-to-back / rest", "team played the previous calendar day (rest < 1.5 days)",
       "ESPN schedule", "schedule known in advance", "first team game missing", "fatigue -> fewer minutes", CAND, A,
       NBA_ALL, "b2b"),
    _f("nba.rest_days", "nba", "Rest days", "log(min(days since team's previous game, 7))", "ESPN schedule",
       "known in advance", "first game missing", "rest helps", CAND, A, NBA_ALL, "rest_days"),
    _f("nba.blowout_risk", "nba", "Blowout risk (spread)", "|pre-game spread|", "ESPN pickcenter / books (forward)",
       "pre-game market", "historical rows mostly empty", "blowouts cut starters' minutes", "market track only", M,
       NBA_ALL, "blowout_risk"),
    _f("nba.teammate_absent_lag", "nba", "Teammate injuries (redistribution)", "EW-minutes share of rotation teammates "
       "(EW min >= 20, played in last 3 team games) who missed the team's PREVIOUS game", "ESPN box (did-not-play rows)",
       PIT_TEAM, "0 when nobody missed (known), missing for a team's first game", "absences redistribute minutes / "
       "usage", CAND, A, NBA_ALL, "absent_lag"),
    _f("nba.teammate_absent_sameday", "nba", "Teammate injuries (same game)", "same share for THIS game's absences",
       "history: ESPN box did-not-play rows; forward: ESPN injury report (store context, status Out) - known at the "
       "report / inactive list (~30 min before tip), NOT for earlier forecasts", "injury report time", "forward: unknown "
       "until a report exists -> missing indicator + data-quality code", "same as lag, stronger", CAND, A, NBA_ALL,
       "absent_sameday"),
    _f("nba.espn_summary_injuries", "nba", "Injury report inside historical ESPN summaries", "-", "ESPN summary "
       "'injuries'", "NOT point-in-time: holds the report of the READ day (2026-10-23)", "-", "-", "excluded (leakage)",
       D),
    _f("nba.home", "nba", "Home / away", "home game (1/0)", "ESPN schedule", "known in advance", "never missing",
       "home role / rates", CAND, A, NBA_ALL, "home"),
    _f("nba.coaching", "nba", "Coaching tendencies (load management, rotation length)", "no structured source",
       "not available", "-", "-", "-", "absent", U),
    _f("nba.postseason", "nba", "Playoff context", "postseason / play-in game", "ESPN schedule", "known", "never",
       "rotations tighten", CAND, A, NBA_ALL, "postseason"),
    # -------------------------------------------------------------------------------------------- NBA advanced
    _f("nba.on_off", "nba", "On/off", "lineup data", "licensed / not available", "-", "-", "-", "absent", U),
    _f("nba.rebound_chances", "nba", "Rebound chances per minute", "tracking", "not available", "-", "-", "-", "absent",
       U, ["rebounds"]),
    _f("nba.ast_to", "nba", "AST/TO", "box-derivable", "ESPN box", PIT_OWN, MISS_HIST, "playmaking role", "absent in "
       "fr-1 (usage proxy covers TOV)", D, ["assists"]),
    _f("nba.opp_reb_rate", "nba", "Opponent rebounding rate", "opponent's allowed rebounds per minute to the position "
       "(= nba.opp_positional_defence for rebounds)", "ESPN box", PIT_TEAM, "-", "-", "covered by opp_pos_allow", A,
       ["rebounds"]),
    _f("nba.recent_form", "nba", "L5 / L10 vs season", "EW means with tuned half-lives (base) + minutes / shot trends",
       "ESPN box", PIT_OWN, MISS_HIST, "recency", BASE, A, NBA_ALL),
    _f("nba.team_ortg_net", "nba", "Team ORtg / net rating", "box-derivable team points per possession", "ESPN team box",
       PIT_TEAM, "-", "-", "absent in fr-1", D),
    _f("nba.market_line", "nba", "Market: line vs distribution, implied probability, best price, movement",
       "Kalshi ladders (sharp-2 / mi-1), book consensus (sports/books)", "Kalshi, Thunderpick, Polymarket, OddsPapi, The "
       "Odds API (keys on Render)", "quote observed before the start", "no eligible quote -> market candidates abstain",
       "the market prices information we lack", "market track (forward only: no historical player quotes)", M, NBA_ALL),
]

NFL_REG = [
    _f("nfl.expected_opportunity", "nfl", "Usage: targets / carries / attempts", "EW mean of prior opportunity "
       "(half-life chosen on tune from {2,4,8} games)", "nflverse weekly stats (+ zero rows from snap counts)", PIT_OWN,
       MISS_HIST, "volume drives yards and catches", BASE, A, NFL_ALL),
    _f("nfl.efficiency", "nfl", "Yards per opportunity / catch rate", "EB efficiency shrunk to the position prior (k "
       "fitted on train)", "nflverse", PIT_OWN, MISS_HIST, "conversion", BASE, A, NFL_ALL),
    _f("nfl.snap_share", "nfl", "Snap share", "EW offensive snap share", "nflverse snap counts (PFR)", PIT_OWN,
       "missing when PFR id unmapped -> indicator", "on-field time", CAND, A, NFL_ALL, "snap_share"),
    _f("nfl.snap_trend", "nfl", "Recent role trend (L3 vs season)", "mean snap share of last 3 - EW snap share",
       "nflverse snap counts", PIT_OWN, MISS_HIST, "role rising / falling", CAND, A, NFL_ALL, "snap_trend"),
    _f("nfl.route_participation", "nfl", "Route participation / routes run / TPRR", "routes per dropback need "
       "participation data", "nflverse pbp_participation (FTN) exists 2023-2025 but NOT for 2026 -> not deployable",
       "-", "-", "routes are the true receiving opportunity", "absent", D, ["rec", "rec_yds"]),
    _f("nfl.target_share", "nfl", "Target share / air-yards share", "EW target share (nflverse)", "nflverse weekly",
       PIT_OWN, MISS_HIST, "share of team targets", CAND, A, ["rec", "rec_yds"], "target_share"),
    _f("nfl.air_yards_share", "nfl", "Air-yards share", "EW air-yards share", "nflverse weekly", PIT_OWN, MISS_HIST,
       "downfield role -> yards", CAND, A, ["rec_yds"], "air_yards_share"),
    _f("nfl.adot", "nfl", "aDOT", "EW receiving air yards / EW targets", "nflverse weekly", PIT_OWN, MISS_HIST,
       "deeper targets -> more yards per catch, fewer catches", CAND, A, ["rec", "rec_yds"], "adot"),
    _f("nfl.redzone", "nfl", "Red-zone targets / carries (inside 20 / 10)", "EW red-zone looks (targets + carries "
       "inside the 20)", "nflverse play-by-play", PIT_OWN, MISS_HIST, "red-zone role ~ TDs, weakly yards", CAND, A,
       NFL_ALL, "redzone"),
    _f("nfl.carry_share", "nfl", "Carry / rush share; workhorse vs committee", "EW carries relative to team (via the "
       "base E[carries]) - a separate share term duplicates the opportunity measure", "nflverse", PIT_OWN, MISS_HIST,
       "-", "covered by the base (no stacking of correlated opportunity measures)", A, ["rush_att", "rush_yds"]),
    _f("nfl.alignment", "nfl", "Alignment / role (X, Z, slot, TE, RB receiving)", "charting data", "licensed (PFF) / "
       "FTN partial", "-", "-", "-", "absent", U),
    # coverage & defensive structure
    _f("nfl.coverage_scheme", "nfl", "Man vs zone, shells, single/two-high, opponent shell frequency, player splits "
       "vs coverage", "FTN charting via nflverse participation (defense_man_zone_type, defense_coverage_type) 2023-2025"
       "; no 2026 file", "nflverse pbp_participation (CC-BY-SA)", "-", "-", "scheme matchups", "absent (no forward "
       "source; player splits vs coverage need licensed data)", U),
    _f("nfl.blitz_pressure", "nfl", "Blitz rate, pressure allowed, time to throw", "FTN participation (2023-2025) / NGS",
       "not forward", "-", "-", "-", "absent", U),
    _f("nfl.individual_matchup", "nfl", "WR vs DB, shadow coverage, NGS separation, OL vs DL", "defender assignment",
       "licensed (PFF / NGS)", "-", "-", "-", "absent", U),
    _f("nfl.opp_positional_allowance", "nfl", "Opponent yards allowed by position / opponent rank", "opponent's mean "
       "allowed market stat to the player's position over its last 8 games", "nflverse weekly", PIT_TEAM,
       "first 3 opponent games missing", "weak defence vs the position -> more", CAND, A, NFL_ALL, "opp_pos_allow"),
    _f("nfl.run_scheme", "nfl", "Run schemes: zone vs gap", "charting", "licensed / not available", "-", "-", "-",
       "absent", U, ["rush_yds"]),
    _f("nfl.scramble", "nfl", "Designed rush vs scramble yards (QB)", "pbp qb_scramble", "nflverse pbp (derivable)",
       PIT_OWN, "-", "QB rushing role", "absent in fr-1", D, ["rush_yds", "rush_att"]),
    # QB
    _f("nfl.qb_throw_profile", "nfl", "QB throw distribution, aDOT, comp%/EPA vs man/zone/pressure", "pbp depth "
       "derivable; splits vs coverage need charting", "nflverse pbp (partial)", "-", "-", "-", "absent in fr-1", D,
       ["pass_yds"]),
    _f("nfl.qb_change", "nfl", "Personnel / QB change", "the team's main passer changed in its PREVIOUS game (lagged; "
       "this game's starter is a same-day fact)", "nflverse weekly", PIT_TEAM, "first team game missing",
       "a new QB changes volume and efficiency", CAND, A, NFL_ALL, "qb_change_lag"),
    # script / pace / environment
    _f("nfl.spread", "nfl", "Game script: spread", "team's pre-game spread (negative = favoured)", "history: nflverse "
       "schedule closing line; forward: Kalshi / books game markets", "closing line ~ kickoff (history); forward = "
       "quote time", "missing -> indicator", "favourites run more, underdogs pass more", "market track only", M,
       NFL_ALL, "team_spread"),
    _f("nfl.implied_total", "nfl", "Implied team total", "total / 2 - team spread / 2", "as spread", "as spread",
       "missing -> indicator", "more expected points -> more volume", "market track only", M, NFL_ALL,
       "implied_total"),
    _f("nfl.neutral_pass_rate", "nfl", "Neutral pass / rush rate", "team pass share of plays with |score diff| <= 7, "
       "Q1-3, 0.2 <= wp <= 0.8, last 6 games", "nflverse pbp", PIT_TEAM, "first 2 games missing", "pass-heavy teams feed "
       "receivers", CAND, A, NFL_ALL, "neutral_pass_rate"),
    _f("nfl.team_plays", "nfl", "Plays per game (pace)", "team offensive plays, last 6", "nflverse pbp", PIT_TEAM,
       "first 2 games missing", "more plays -> more volume", CAND, A, NFL_ALL, "team_plays"),
    _f("nfl.opp_plays", "nfl", "Opponent pace", "opponent offensive plays, last 6", "nflverse pbp", PIT_TEAM,
       "first 2 games missing", "fast opponents add possessions", CAND, A, NFL_ALL, "opp_plays"),
    _f("nfl.seconds_per_play", "nfl", "Seconds per play", "pbp clock derivable", "nflverse pbp", PIT_TEAM, "-",
       "pace", "absent in fr-1 (team_plays covers pace)", D),
    _f("nfl.wind", "nfl", "Weather: wind", "game wind (mph) from the nflverse schedule", "history: nflverse (game-time "
       "observation); forward: NO weather source ingested", "history only", "domes / missing -> indicator",
       "wind hurts passing", "tested on history for information; NOT deployable without a forward forecast source", H,
       ["pass_yds", "rec_yds", "rec"], "wind"),
    _f("nfl.temp", "nfl", "Weather: cold", "game temperature", "as wind", "history only", "as wind", "cold hurts passing",
       "as wind", H, ["pass_yds", "rec_yds", "rec"], "temp"),
    _f("nfl.dome", "nfl", "Venue: dome / closed roof", "roof dome or closed", "nflverse schedule (known in advance)",
       "known in advance", "never", "indoor passing", CAND, A, ["pass_yds", "rec_yds", "rec"], "dome"),
    _f("nfl.home", "nfl", "Home / away", "home game", "nflverse schedule", "known", "never", "home volume", CAND, A,
       NFL_ALL, "home"),
    _f("nfl.short_week", "nfl", "Rest / short week", "fewer than 6 days since the team's previous game", "nflverse "
       "schedule", "known", "first game missing", "short week hurts", CAND, A, NFL_ALL, "short_week"),
    _f("nfl.injury_redistribution", "nfl", "Injury usage redistribution", "teammates out (injury report)", "ESPN injury "
       "report forward only (store context); history has no point-in-time report", "report time", "-", "-", "absent "
       "in fr-1 (no historical point-in-time source)", D),
    # advanced
    _f("nfl.epa", "nfl", "EPA per play / target / carry; success rate; explosive rate", "nflverse weekly EPA (success "
       "/ explosive derivable from pbp)", "nflverse", PIT_OWN, MISS_HIST, "efficiency persists", "inside the EB "
       "efficiency (base); a separate EPA term is derivable later", D),
    _f("nfl.dvoa", "nfl", "DVOA", "proprietary", "licensed (FTN/FO)", "-", "-", "-", "absent", U),
    _f("nfl.ngs", "nfl", "Next Gen Stats", "tracking", "licensed", "-", "-", "-", "absent", U),
    _f("nfl.recent_form", "nfl", "Recent form windows L3/L4/L6 vs season", "EW half-life tuned (base) + snap trend",
       "nflverse", PIT_OWN, MISS_HIST, "recency", BASE, A, NFL_ALL),
    _f("nfl.postseason", "nfl", "Postseason", "postseason game", "nflverse", "known", "never", "-", CAND, A, NFL_ALL,
       "postseason"),
    # market
    _f("nfl.market_line", "nfl", "Market: line vs distribution, de-vigged probability, best number, movement, "
       "correlation with game / team total", "Kalshi ladders + book consensus", "Kalshi, Thunderpick, Polymarket, "
       "OddsPapi, The Odds API", "quote observed before kickoff", "no eligible quote -> market candidates abstain",
       "-", "market track (forward only)", M, NFL_ALL),
]
REGISTRY = REGISTRY + NFL_REG


def candidates(sport, market, track="statistical"):
    """Registry ids tested for a market. track 'statistical' = status available + candidate; 'market' = market track
    entries with a column; 'history' = historical-only entries (tested for information only)."""
    want = {"statistical": (A,), "market": (M,), "history": (H,)}[track]
    return [r for r in REGISTRY if r["sport"] == sport and r["status"] in want and r["column"]
            and (r["markets"] is None or market in r["markets"])]


MARKET_STAT = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m", "rec": "rec",
               "rec_yds": "rec_yds", "rush_yds": "rush_yds", "rush_att": "carries", "pass_yds": "pass_yds"}


def compute(df, sport, market, column):
    """The feature's pre-game value per row of a featurize.frame() (np.ndarray, NaN = unknown)."""
    g = lambda c: np.asarray(df[c], float)                               # noqa: E731
    with np.errstate(divide="ignore", invalid="ignore"):
        if sport == "nba":
            st = MARKET_STAT[market]
            return {
                "minutes_trend": g("l5_min") / g("ew_min") - 1,
                "usage": g("S_usage") / g("S_min"),
                "shot_trend": g("l5_fga_pm") / (g("S_fga") / g("S_min")) - 1,
                "role_change": g("starter_lag") - g("starter_rate10"),
                "starter_prev": g("starter_lag"),
                "team_pace": g("pace_l10"), "opp_pace": g("opp_pace_l10"),
                "game_total": g("total_line"), "blowout_risk": g("spread_abs"),
                "opp_pos_allow": g(f"allow_{st}") / g("allow_min"),
                "b2b": np.where(np.isfinite(g("rest_days")), (g("rest_days") < 1.5).astype(float), np.nan),
                "rest_days": np.log(np.clip(g("rest_days"), 0.5, 7)),
                "absent_lag": g("absent_share_lag"), "absent_sameday": g("absent_share"),
                "home": g("home"),
                "postseason": np.isin(df["season_type"], ["postseason", "play_in"]).astype(float),
            }[column]
        st = MARKET_STAT[market]
        allow = {"rec": "allow_rec", "rec_yds": "allow_rec_yds", "rush_yds": "allow_rush_yds",
                 "rush_att": "allow_carries", "pass_yds": "allow_pass_yds"}[market]
        return {
            "snap_share": g("ew_offense_pct"), "snap_trend": g("l3_snap") - g("ew_offense_pct"),
            "target_share": g("ew_target_share"), "air_yards_share": g("ew_air_yards_share"),
            "adot": g("ew_rec_air_yds") / g("ew_targets"),
            "redzone": g("ew_rz_targets") + g("ew_rz_carries"),
            "opp_pos_allow": g(allow), "qb_change_lag": g("qb_change_lag"),
            "team_spread": g("team_spread"), "implied_total": g("implied_total"),
            "neutral_pass_rate": g("npass_l6"), "team_plays": g("plays_l6"), "opp_plays": g("opp_plays_l6"),
            "wind": np.where(g("dome") == 1, 0.0, g("wind")),
            "temp": np.where(g("dome") == 1, 70.0, g("temp")),
            "dome": g("dome"), "home": g("home"),
            "short_week": np.where(np.isfinite(g("rest_days")), (g("rest_days") < 6).astype(float), np.nan),
            "postseason": (np.asarray(df["season_type"]) == "postseason").astype(float),
        }[column]


def with_effects():
    """Registry rows joined with the observed effects of the latest registered study results (if any)."""
    eff = {}
    for p in sorted(RESULTS.glob("model_study_*.json")):
        try:
            res = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for mk, m in (res.get("markets") or {}).items():
            for fid, e in (m.get("feature_tests") or {}).items():
                e = dict(e, study=p.name)
                eff.setdefault(fid, {})[mk] = e
    out = []
    for r in REGISTRY:
        x = dict(r)
        x["observed_effect"] = eff.get(r["id"]) or ("not tested (status: %s)" % r["status"]
                                                    if r["status"] not in (A, M, H) or not r["column"]
                                                    else "pending study")
        out.append(x)
    return out
