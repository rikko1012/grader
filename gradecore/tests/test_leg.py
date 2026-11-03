from dataclasses import FrozenInstanceError, replace
import pytest
from gradecore.contracts import Leg


def leg(sport='cs2'):
    return Leg(sport, sport, 'e1', 'm1', 't1', 'player', 'kills', 'game', 20.5,
               'over', 'underdog', 'standard', None, None,
               '2026-11-04T00:00:00Z', 'revision-1', '2026-11-03T00:00:00Z')


@pytest.mark.parametrize('sport', ['cs2','lol','nba','nfl'])
def test_unrated_leg_is_valid_and_immutable(sport):
    l = leg(sport)
    assert l.p_cal is None
    with pytest.raises(FrozenInstanceError):
        l.line = 21.5


@pytest.mark.parametrize('patch', [dict(line=float('nan')), dict(line=True), dict(p_cal=float('inf')),
                                    dict(p_cal=True), dict(p_cal=-1), dict(offer_revision=''),
                                    dict(side='both'), dict(letter='A'), dict(start_utc='2026-11-04')])
def test_invalid_leg_cannot_enter_shared_slip(patch):
    with pytest.raises(ValueError):
        replace(leg(), **patch)
