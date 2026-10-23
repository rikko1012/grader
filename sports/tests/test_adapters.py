"""Adapters on the data branch's saved source answers (fixtures, 2026-10-23): typed records, quarantine reasons,
per-offer side availability, Kalshi time semantics."""
from sports import adapters, config, espn
from sports.tests import helpers as h
from sports.tests.conftest import fixture

NBA = config.league("nba")


def test_prizepicks_offers_are_typed_with_variant_sides_and_source_time():
    b = adapters.pp_offers(fixture("pp_nba.json"), NBA, h.T0, h.resolver())
    assert b.records and all(r.validate() == [] for r in b.records)
    assert {r.variant for r in b.records} <= {"standard", "goblin", "demon"}
    more_only = [r for r in b.records if r.offered_sides() == ("over",)]
    both = [r for r in b.records if r.offered_sides() == ("over", "under")]
    assert more_only and both                                           # availability is per offer
    assert all(not r.source_time_unknown and r.source_updated_at for r in b.records)
    assert all(r.listed_time_kind == "start" and r.scope == "full_game" for r in b.records)
    towns = [r for r in b.records if r.player_key == "karl anthony towns"]
    assert towns and all(r.event_id == "nba:espn:1" and r.player_id == "nba:player:espn:3134907" for r in towns)


def test_prizepicks_quarantine_reasons_are_counted():
    body = fixture("pp_nba.json")
    body["data"][0]["attributes"]["in_game"] = True
    body["data"][1]["attributes"]["line_score"] = None
    b = adapters.pp_offers(body, NBA, h.T0, None)
    reasons = {x["reason"] for x in b.quarantine}
    assert "in_game_or_not_pre_game" in reasons and "missing_line_player_or_start" in reasons
    assert sum(b.reason_counts().values()) == len(b.quarantine)


def test_underdog_modifiers_kept_and_undocumented_prices_labelled():
    b = adapters.ud_offers(fixture("ud_nba.json"), NBA, h.T0, h.resolver())
    assert b.records and all(r.validate() == [] for r in b.records)
    for r in b.records:
        for s in r.sides:
            assert s.offered or s.multiplier is None
            assert s.price is None                                     # decimal_price is NOT a contract price
        assert any(k.startswith("ud_undocumented_decimal_") for k, _ in r.extras) or not r.offered_sides()
    assert any(s.multiplier not in (None, 1.0) for r in b.records for s in r.sides)


def test_kalshi_quotes_have_unknown_source_time_and_expiration_not_start():
    ev = fixture("kalshi_nbapts.json")["events"]
    b = adapters.kalshi_offers(ev, NBA, h.T0, "points", h.resolver())
    assert b.records and all(r.validate() == [] for r in b.records)
    for r in b.records:
        assert r.source_time_unknown and r.source_updated_at is None
        assert r.listed_time_kind == "expected_expiration"
        assert r.variant == "ladder_rung" and r.line % 1 == 0.5
        assert dict(r.extras)["kalshi_updated_time"]
        assert dict(r.liquidity)["volume"] is not None
    towns = [r for r in b.records if r.player_key == "karl anthony towns"]
    assert towns and towns[0].team == "NYK" and towns[0].event_id == "nba:espn:1"
    s = towns[0].side("over")
    assert s.bid is not None and s.ask is not None and s.bid <= s.ask


def test_kalshi_unreadable_markets_are_quarantined():
    ev = fixture("kalshi_nbapts.json")["events"]
    ev[0]["markets"][0]["yes_sub_title"] = "Karl-Anthony Towns: ten+"
    ev[0]["markets"][1]["strike_type"] = "between"
    b = adapters.kalshi_offers(ev, NBA, h.T0, "points", None)
    assert sum(1 for x in b.quarantine if x["reason"] == "unreadable_market_title_or_strike") == 2


def test_espn_events_and_box_scores():
    b = adapters.espn_events(fixture("espn_scoreboard_nba.json"), NBA, h.T0)
    evs = [r for r in b.records if r.KIND == "event"]
    assert evs and all(e.start_source == "espn" and e.validate() == [] for e in evs)
    box = espn.parse_summary(fixture("espn_summary_nba.json"), "basketball")
    sb = adapters.espn_box(box, NBA, "nba:espn:401909094", h.T0)
    obs = [r for r in sb.records if r.KIND == "stat"]
    assert obs and all(o.includes_overtime == "yes" and o.final for o in obs)
    assert {o.participation for o in obs} <= {"played", "did_not_play", "unknown"}
    assert any(o.stat == "pts" and o.value and o.value > 0 for o in obs)


def test_in_progress_box_score_is_not_an_observation():
    box = espn.parse_summary(fixture("espn_summary_nba.json"), "basketball")
    box["completed"] = False
    assert adapters.espn_box(box, NBA, "nba:espn:401909094", h.T0).records == []
