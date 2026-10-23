"""Builders for contract tests (no network)."""
from sports import identity
from sports.adapters import Resolver
from sports.contracts import Event, Forecast, OfferSnapshot, SideQuote, StatObservation

T0 = "2026-10-23T20:00:00Z"
START = "2026-10-23T23:00:00Z"
NBA_TEAMS = [{"id": "18", "abbr": "NY", "name": "New York Knicks"}, {"id": "2", "abbr": "BOS", "name": "Boston Celtics"},
             {"id": "4", "abbr": "CHI", "name": "Chicago Bulls"}, {"id": "15", "abbr": "MIL", "name": "Milwaukee Bucks"},
             {"id": "9", "abbr": "GS", "name": "Golden State Warriors"}]
NY, BOS, CHI, MIL = (identity.team_id("nba", x) for x in ("18", "2", "4", "15"))


def event(eid="nba:espn:1", home=BOS, away=NY, start=START, status="scheduled", **kw):
    return Event(event_id=eid, league="nba", season=2027, season_type="regular", home_team_id=home, away_team_id=away,
                 start_utc=start, start_source="espn", status=status, observed_at=kw.pop("observed_at", T0),
                 known_at=kw.pop("known_at", T0), **kw)


def resolver(events=None, roster=None):
    teams = identity.Teams("nba", NBA_TEAMS)
    r = identity.Rosters()
    for team, pid, name in roster or [(NY, "nba:player:espn:3134907", "Karl-Anthony Towns"),
                                      (BOS, "nba:player:espn:4065648", "Jayson Tatum"),
                                      (NY, "nba:player:espn:3934672", "Jalen Brunson")]:
        r.add(team, pid, name)
    return Resolver("nba", teams, events if events is not None else [event()], r)


def offer(platform="prizepicks", oid="pp:1", line=20.5, stat="points", sides=None, variant="standard", scope="full_game",
          promotional=False, event_id="nba:espn:1", player_id="nba:player:espn:3134907", observed_at=T0, **kw):
    sides = sides if sides is not None else (SideQuote("over", True), SideQuote("under", True))
    base = dict(platform=platform, offer_id=oid, market_id=None, league="nba", event_id=event_id, event_ref="NYK @ BOS",
                player_id=player_id, player_name="Karl-Anthony Towns", player_key="karl anthony towns", team="NYK",
                stat=stat, source_stat="Points", scope=scope, line=line, sides=sides, variant=variant,
                promotional=promotional, availability="open", rule_version="test", listed_start_utc=START,
                listed_time_kind="start", source_updated_at=None, observed_at=observed_at, source_time_unknown=True,
                known_at=observed_at)
    base.update(kw)
    return OfferSnapshot(**base)


def kalshi_rung(line, bid, ask, oid=None, volume=100.0, oi=50.0, observed_at=T0, **kw):
    return offer(platform="kalshi", oid=oid or f"kalshi:K-{line}", line=line, variant="ladder_rung",
                 sides=(SideQuote("over", True, "exchange_yes_no", bid=bid, ask=ask),
                        SideQuote("under", True, "exchange_yes_no", bid=round(1 - ask, 4), ask=round(1 - bid, 4))),
                 liquidity=(("volume", volume), ("open_interest", oi)), listed_start_utc="2026-10-24T02:00:00Z",
                 listed_time_kind="expected_expiration", observed_at=observed_at, **kw)


def stat(pid="nba:player:espn:3134907", part="pts", value=24.0, participation="played", observed_at="2026-10-24T03:00:00Z",
         eid="nba:espn:1"):
    return StatObservation(event_id=eid, league="nba", participant_id=pid, participant_type="player", scope="full_game",
                           includes_overtime="yes", stat=part, unit="count", value=value, participation=participation,
                           source="espn", source_ref=f"espn:summary:1:{pid}", final=True, observed_at=observed_at,
                           known_at=observed_at)


def forecast(**kw):
    base = dict(forecast_at=T0, league="nba", event_id="nba:espn:1", player_id="nba:player:espn:3134907",
                player_key="karl anthony towns", stat="points", scope="full_game", line=20.0,
                offer_snapshot_ids=("offer:x",), input_snapshot_ids=("offer:k",), model_version="test-1",
                artifact_version=None, data_version=None, calibration_version=None, grade_policy_version=None,
                projection_mean=21.0, projection_median=None, distribution=(("family", "normal"),),
                p_over=0.5, p_under=0.45, p_push=0.05, p_over_given_no_push=0.5 / 0.95,
                p_under_given_no_push=0.45 / 0.95, sides_available=("over", "under"), chosen_side="over", grade=None,
                stake_units=None, stake_policy_version=None)
    base.update(kw)
    return Forecast.build(**base)
