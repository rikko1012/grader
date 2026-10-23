"""Lab feature registry (version lf-1). Every feature is computed for a target game from the same player's EARLIER games
only (start < target start AND known_at <= target forecast time = start - 60 min), never from the target game.

validated_effect is None for every feature: a lab experiment measures an effect on its own test split, which is
reported with the experiment and does NOT register the feature for production (that is the model agent's registered
gate, sports/research/REGISTER.md).
"""
from __future__ import annotations

REGISTRY_VERSION = "lf-1"
_PRIOR = "earlier games of this player, known before the forecast time (start - 60 min)"

FEATURES = {
    "prior_mean": dict(definition="mean of the target stat over all earlier played games in the data window",
                       sports=("nba", "nfl"), sources=("ds-1", "canonical"), availability=_PRIOR,
                       missing="row ineligible (min_prior games required)", hypothesis="players have a stable level",
                       treatment="raw value; z-scored on train (ridge)"),
    "l5_mean": dict(definition="mean of the target stat over the last 5 earlier played games (fewer if fewer exist)",
                    sports=("nba", "nfl"), sources=("ds-1", "canonical"), availability=_PRIOR,
                    missing="falls back to the available games", hypothesis="recent form carries information",
                    treatment="raw; z-scored on train"),
    "l10_mean": dict(definition="mean over the last 10 earlier played games", sports=("nba", "nfl"),
                     sources=("ds-1", "canonical"), availability=_PRIOR, missing="available games",
                     hypothesis="medium-window level", treatment="raw; z-scored on train"),
    "l20_mean": dict(definition="mean over the last 20 earlier played games", sports=("nba", "nfl"),
                     sources=("ds-1", "canonical"), availability=_PRIOR, missing="available games",
                     hypothesis="long-window level", treatment="raw; z-scored on train"),
    "prior_sd": dict(definition="sample standard deviation of the target stat over earlier played games",
                     sports=("nba", "nfl"), sources=("ds-1", "canonical"), availability=_PRIOR,
                     missing="0 when fewer than 2 games + missing indicator", hypothesis="volatile players spread more",
                     treatment="raw; z-scored on train"),
    "prior_n": dict(definition="number of earlier played games (capped at 82)", sports=("nba", "nfl"),
                    sources=("ds-1", "canonical"), availability=_PRIOR, missing="never missing",
                    hypothesis="more history = less shrinkage needed", treatment="raw; z-scored on train"),
    "home": dict(definition="1 if the player's team is the home team, 0 if away", sports=("nba", "nfl"),
                 sources=("ds-1", "canonical"), availability="schedule, known before the game",
                 missing="train mean + missing indicator (canonical rows without a team membership)",
                 hypothesis="small home effect", treatment="0/1"),
    "rest_days": dict(definition="days since the player's previous game (capped at 10)", sports=("nba", "nfl"),
                      sources=("ds-1", "canonical"), availability="schedule", missing="train mean + indicator "
                      "(first game in window)", hypothesis="back-to-backs lower output", treatment="raw, capped"),
    "min_l5": dict(definition="mean minutes over the last 5 earlier played games", sports=("nba",),
                   sources=("ds-1",), availability=_PRIOR, missing="train mean + indicator",
                   hypothesis="opportunity drives counting stats", treatment="raw; z-scored"),
    "starter_l5": dict(definition="share of the last 5 earlier played games started", sports=("nba",),
                       sources=("ds-1",), availability=_PRIOR, missing="train mean + indicator",
                       hypothesis="role", treatment="raw"),
    "targets_l5": dict(definition="mean targets over the last 5 earlier played games", sports=("nfl",),
                       sources=("ds-1", "canonical"), availability=_PRIOR, missing="train mean + indicator",
                       hypothesis="receiving opportunity", treatment="raw; z-scored"),
    "carries_l5": dict(definition="mean carries over the last 5 earlier played games", sports=("nfl",),
                       sources=("ds-1", "canonical"), availability=_PRIOR, missing="train mean + indicator",
                       hypothesis="rushing opportunity", treatment="raw; z-scored"),
    "pass_att_l5": dict(definition="mean pass attempts over the last 5 earlier played games", sports=("nfl",),
                        sources=("ds-1", "canonical"), availability=_PRIOR, missing="train mean + indicator",
                        hypothesis="passing volume", treatment="raw; z-scored"),
    "snap_pct_l5": dict(definition="mean offensive snap share over the last 5 earlier played games", sports=("nfl",),
                        sources=("ds-1",), availability=_PRIOR, missing="train mean + indicator",
                        hypothesis="participation", treatment="raw; z-scored"),
}
for _k, _v in FEATURES.items():
    _v["name"] = _k
    _v["validated_effect"] = None
    _v["registry_version"] = REGISTRY_VERSION


def available(league, source):
    return [k for k, v in FEATURES.items() if league in v["sports"] and source in v["sources"]]


def listing():
    return [dict(v) for v in FEATURES.values()]
