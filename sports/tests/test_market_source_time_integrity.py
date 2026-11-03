"""A quote cannot borrow freshness from a source timestamp after prediction."""
import pytest

from sports.contracts import SideQuote
from sports.model import market
from sports.tests import helpers as h


@pytest.mark.parametrize('platform', ['fanduel', 'betrivers', 'oddsapi:fanduel'])
def test_future_original_source_time_cannot_make_an_old_quote_eligible(platform):
    event = h.event(start='2026-11-03T23:00:00Z')
    quote = h.offer(platform=platform, oid='test:quote', line=20.5,
                    observed_at='2026-11-03T21:00:00Z',
                    source_updated_at='2026-11-03T22:31:00Z', source_time_unknown=False,
                    sides=(SideQuote('over', True, 'decimal', price=1.9),
                           SideQuote('under', True, 'decimal', price=1.9)))
    probability, reason = market.quote_prob(quote, event, '2026-11-03T22:30:00Z')
    assert probability is None
    assert reason == 'source time after forecast time'
