import pytest

from sports.books.base import american_to_decimal, decimal_to_american, make_offer


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_odds_converters_reject_nonfinite(bad):
    with pytest.raises(ValueError):
        american_to_decimal(bad)
    with pytest.raises(ValueError):
        decimal_to_american(bad)


@pytest.mark.parametrize("field,bad", [("line", float("nan")), ("line", float("inf")),
                                      ("price_decimal", float("nan")), ("price_decimal", 1),
                                      ("price_decimal", 0.9)])
def test_invalid_quote_cannot_receive_store_identity(field, bad):
    offer = dict(provider="test", book="test", league="nba", provider_event_id="event",
                 player="Test Player", market="points", period="game", line=20.5, side="over",
                 price_decimal=1.91, price_american=-110, market_type="standard")
    offer[field] = bad
    with pytest.raises(ValueError):
        make_offer(**offer)
