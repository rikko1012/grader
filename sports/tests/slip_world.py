"""Test fixtures for the Slip Studio: a canonical store with events, pick'em offers and forecasts (no network).
Every name, line and probability here is a TEST FIXTURE."""
from datetime import datetime, timezone

from sports import store
from sports.contracts import Event, Forecast, OfferSnapshot, SideQuote, Settlement

OBS = "2026-10-23T20:00:00Z"
NOW = datetime(2026, 10, 23, 20, 5, tzinfo=timezone.utc)
START = "2026-10-24T00:00:00Z"


def ev(n, start=START, status="scheduled", league="nba"):
    return Event(event_id=f"{league}:espn:{n}", league=league, season=2027, season_type="regular",
                 home_team_id=f"{league}:team:espn:h{n}", away_team_id=f"{league}:team:espn:a{n}", start_utc=start,
                 start_source="espn", status=status, observed_at=OBS, known_at=OBS)


def offer(pid, team, n, line=20.5, stat="points", platform="underdog", oid=None, sides=("over", "under"),
          multiplier=1.0, observed_at=OBS, variant=None, league="nba", start=START, **kw):
    sq = tuple(SideQuote(s, s in sides, "payout_multiplier" if platform == "underdog" else "none",
                         multiplier=multiplier if platform == "underdog" and s in sides else None) for s in ("over", "under"))
    return OfferSnapshot(
        platform=platform, offer_id=oid or f"{platform[:2]}:{pid}:{stat}:{line}", market_id=None, league=league,
        event_id=f"{league}:espn:{n}", event_ref=f"A{n} @ H{n}", player_id=f"{league}:player:espn:{pid}",
        player_name=f"Player {pid}", player_key=f"player {pid}", team=team, stat=stat, source_stat=stat.title(),
        scope="full_game", line=line, sides=sq, variant=variant or ("balanced" if platform == "underdog" else "standard"),
        promotional=False, availability="open", rule_version="test", listed_start_utc=start, listed_time_kind="start",
        source_updated_at=None, observed_at=observed_at, source_time_unknown=True, known_at=observed_at, **kw)


def forecast(rid, o, side="over", p=0.6, push=0.0, grade="B", stake=1.0, research_only=False, model="test-m1",
             forecast_at=OBS):
    other = 1.0 - p - push
    po, pu = (p, other) if side == "over" else (other, p)
    return Forecast.build(
        forecast_at=forecast_at, league=o.league, event_id=o.event_id, player_id=o.player_id, player_key=o.player_key,
        stat=o.stat, scope="full_game", line=o.line, offer_snapshot_ids=(rid,), input_snapshot_ids=(),
        model_version=model, artifact_version=None, data_version=None, calibration_version=None,
        grade_policy_version="gp-test", projection_mean=None, projection_median=None, distribution=(),
        p_over=po, p_under=pu, p_push=push, p_over_given_no_push=po / (1 - push) if push < 1 else None,
        p_under_given_no_push=pu / (1 - push) if push < 1 else None, sides_available=o.offered_sides(),
        chosen_side=side, grade=grade, stake_units=stake, stake_policy_version="sp-test",
        research_only=research_only)


def put_leg(con, o, **fkw):
    rid = store.put(con, o)["record_id"]
    f = forecast(rid, o, **fkw)
    store.put(con, f)
    return rid, f


def world(con, specs, events=None):
    """specs: [(pid, team, event_n, side, p, extra_offer_kw?)] -> {pid: (rid, forecast)}"""
    for n in sorted(events or {s[2] for s in specs}):
        store.put(con, ev(n))
    out = {}
    for s in specs:
        pid, team, n, side, p = s[:5]
        kw = s[5] if len(s) > 5 else {}
        fkw = kw.pop("_f", {})
        out[pid] = put_leg(con, offer(pid, team, n, **kw), side=side, p=p, **fkw)
    return out


def settlement(o, side, status, value, revision=1, supersedes=None, at="2026-10-24T04:00:00Z"):
    outcome = None if status in ("void", "unresolved") else ("push" if status == "push" else
                                                              (side if status == "win" else
                                                               ("under" if side == "over" else "over")))
    return Settlement(settlement_id=f"st-{o.offer_id}-{side}-{revision}", platform=o.platform, offer_id=o.offer_id,
                      side=side, line=o.line, event_id=o.event_id, player_id=o.player_id, stat=o.stat, status=status,
                      outcome=outcome, source_value=value, source="espn", source_ref="test", rule_version="test",
                      revision=revision, supersedes=supersedes, settled_at=at, observed_at=at, known_at=at)
