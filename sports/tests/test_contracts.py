"""Canonical contracts sc-1: validators, hashing, immutability, round trips."""
import json

import pytest

from sports.contracts import (ContextSnapshot, ContractError, Forecast, OfferSnapshot, RECORD_TYPES, Settlement,
                              SideQuote, from_dict, json_schema, utc)
from sports.tests import helpers as h


@pytest.mark.parametrize("field", ["p_over", "p_under", "p_push"])
def test_forecast_requires_unconditional_probabilities_without_crashing(field):
    f = h.forecast(**{field: None})
    assert any(field in err for err in f.validate())
    with pytest.raises(ContractError):
        f.validate_or_raise()


@pytest.mark.parametrize("field", ["line", "projection_mean", "projection_median", "stake_units"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "bad", True])
def test_forecast_rejects_invalid_numeric_fields_without_crashing(field, value):
    f = h.forecast(**{field: value})
    assert any(field in err for err in f.validate())
    with pytest.raises(ContractError):
        f.validate_or_raise()


def test_forecast_rejects_boolean_probabilities_but_keeps_unknown_optional_values():
    assert h.forecast(projection_mean=None, projection_median=None, stake_units=None).validate() == []
    assert h.forecast(p_over=True).validate()


def test_utc_normalises_offsets_and_refuses_naive_times():
    assert utc("2026-10-23T19:00:00.000-04:00") == "2026-10-23T23:00:00Z"
    assert utc("2026-10-23T23:00Z") == "2026-10-23T23:00:00Z"
    assert utc("2026-10-22T23:03:46.547601Z") == "2026-10-22T23:03:46.547601Z"
    assert utc(None) is None
    with pytest.raises(ContractError):
        utc("2026-10-23T23:00:00")                    # no zone = no provenance


def test_offer_times_are_three_separate_things():
    ok = h.offer(source_updated_at="2026-10-23T19:00:00Z", source_time_unknown=False)
    assert ok.validate() == []
    bad = h.offer(source_updated_at=None, source_time_unknown=False)
    assert any("source_time_unknown" in e for e in bad.validate())     # never invent a source time
    both = h.offer(source_updated_at="2026-10-23T19:00:00Z", source_time_unknown=True)
    assert both.validate()


def test_offer_sides_prices_and_lines():
    assert h.offer(line=-0.5, stat="rush_yds").validate() == []        # negative rushing lines are real
    assert h.offer(line=float("nan")).validate()
    crossed = h.offer(sides=(SideQuote("over", True, "exchange_yes_no", bid=0.6, ask=0.5), SideQuote("under", False)))
    assert any("crossed" in e for e in crossed.validate())
    priced_closed = h.offer(sides=(SideQuote("over", True), SideQuote("under", False, "payout_multiplier",
                                                                       multiplier=1.1)))
    assert any("not offered" in e for e in priced_closed.validate())
    dup = h.offer(sides=(SideQuote("over", True), SideQuote("over", True)))
    assert dup.validate()


def test_offer_revision_hash_ignores_bookkeeping_but_not_prices():
    a = h.kalshi_rung(19.5, 0.40, 0.42)
    b = h.kalshi_rung(19.5, 0.40, 0.42, observed_at="2026-10-23T20:10:00Z", volume=999.0)
    assert a.content_hash() == b.content_hash()                        # same quote seen again = duplicate
    c = h.kalshi_rung(19.5, 0.41, 0.42)
    assert a.content_hash() != c.content_hash()                        # price moved = revision
    s1 = h.offer(source_updated_at="2026-10-23T19:00:00Z", source_time_unknown=False)
    s2 = h.offer(source_updated_at="2026-10-23T19:30:00Z", source_time_unknown=False)
    assert s1.content_hash() == s2.content_hash()                      # a source "touch" without change


def test_context_unknown_is_not_false():
    c = ContextSnapshot(league="nba", kind="injury", subject_id="nba:player:espn:1", event_id=None, status="unknown",
                        present="unknown", observed_at=h.T0)
    assert c.validate() == []
    assert c.present == "unknown" and c.present != "no"
    assert ContextSnapshot(league="nba", kind="injury", subject_id="x", event_id=None, status="out", present="maybe",
                           observed_at=h.T0).validate()


def test_forecast_probabilities_unconditional_and_named_conditional():
    f = h.forecast()
    assert f.validate() == []
    assert abs(f.p_over + f.p_under + f.p_push - 1) < 1e-12
    bad_sum = h.forecast(p_over=0.6, p_under=0.45, p_push=0.0, p_over_given_no_push=None, p_under_given_no_push=None)
    assert any("!= 1" in e for e in bad_sum.validate())
    half_push = h.forecast(line=20.5)
    assert any("half-point" in e for e in half_push.validate())
    wrong_cond = h.forecast(p_over_given_no_push=0.5)
    assert any("given_no_push" in e for e in wrong_cond.validate())


def test_forecast_side_must_be_offered_and_id_is_content():
    f = h.forecast(sides_available=("over",), chosen_side="under")
    assert any("not offered" in e for e in f.validate())
    a, b = h.forecast(), h.forecast()
    assert a.forecast_id == b.forecast_id                              # replay: same inputs -> same id
    c = h.forecast(model_version="test-2")
    assert c.forecast_id != a.forecast_id
    fields = {k: v for k, v in a.to_dict().items() if k != "kind"}
    tampered = Forecast(**(fields | {"p_over": 0.51, "p_under": 0.44, "p_over_given_no_push": 0.51 / 0.95,
                                     "p_under_given_no_push": 0.44 / 0.95}))
    assert any("immutable" in e for e in tampered.validate())


def test_settlement_consistency_rules():
    base = dict(settlement_id="st-1", platform="prizepicks", offer_id="pp:1", side="over", line=20.5,
                event_id="nba:espn:1", player_id="p", stat="points", source="espn", source_ref=None,
                rule_version="r", settled_at=h.T0)
    assert Settlement(status="win", outcome="over", source_value=24.0, **base).validate() == []
    assert Settlement(status="loss", outcome="over", source_value=24.0, **base).validate()
    assert Settlement(status="win", outcome="under", source_value=24.0, **base).validate()
    assert Settlement(status="win", outcome="over", source_value=18.0, **base).validate()      # value contradicts
    assert Settlement(status="void", outcome=None, source_value=None, **base).validate() == []
    assert Settlement(status="win", outcome="over", source_value=24.0, revision=2, **base).validate()   # no supersedes


def test_round_trip_every_kind_through_json():
    recs = [h.event(), h.offer(), h.kalshi_rung(9.5, 0.3, 0.32), h.stat(), h.forecast()]
    for r in recs:
        back = from_dict(json.loads(json.dumps(r.to_dict())))
        assert back == r and back.content_hash() == r.content_hash()


def test_json_schemas_exist_for_every_record_type():
    for kind in RECORD_TYPES:
        js = json_schema(kind)
        assert js["type"] == "object" and js["properties"]
    assert "observed_at" in json_schema("offer")["required"] and "line" in json_schema("offer")["required"]
    assert isinstance(OfferSnapshot.HASH_EXCLUDE, tuple)
