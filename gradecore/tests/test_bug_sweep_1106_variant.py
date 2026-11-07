"""Bug sweep 2026-11-06 #15: Underdog's ordinary 'balanced' line (LoL odds_type, sports adapter variant) and a sports
book's 'main' line are the STANDARD payout line - the shadow grader must give them a break-even / EV / band instead of
'Alt line: payout unknown'. Real alternates (goblin / demon / alternate / one_sided) keep their own variant."""
from types import SimpleNamespace

import pytest

from gradecore import adapters, payouts, policy
from gradecore.engine import assess
from gradecore.tests.test_capture import source


def test_15_lol_underdog_balanced_row_gets_the_standard_break_even():
    kind, (row, fid, at) = source('lol')
    a = adapters.lol(dict(row, odds_type='balanced', p_over=.6, p_under=.4), fid, at)
    assert a.offer.variant == payouts.STANDARD_VARIANT
    out = assess(a, a.offer)
    g = out['sides']['over']['grade']
    std = policy.grade(.6, 'underdog', variant='standard')
    assert g['break_even'] == pytest.approx(payouts.break_even('underdog')) == pytest.approx(std.break_even)
    assert g['band'] == std.band and g['ev_vs_breakeven'] == pytest.approx(std.ev_vs_breakeven)
    assert 'Alt line: payout unknown' not in repr(out)


def test_15_sports_underdog_balanced_and_book_main_are_standard_but_alternates_are_not():
    kind, (fc, offer, start) = source('nba')
    for v in ('balanced', 'main', 'standard', None):
        a = adapters.sports(fc, SimpleNamespace(**{**vars(offer), 'variant': v}), start)
        assert a.offer.variant == 'standard', v
    for v in ('alternate', 'goblin', 'demon', 'one_sided'):
        a = adapters.sports(fc, SimpleNamespace(**{**vars(offer), 'variant': v}), start)
        assert a.offer.variant == v
    a = adapters.sports(fc, SimpleNamespace(**{**vars(offer), 'variant': 'balanced'}), start)
    assert 'Alt line: payout unknown' not in repr(assess(a, a.offer))
