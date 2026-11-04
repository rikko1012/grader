import payout_reference as p
import workspace_rules as rules


def test_published_reference_rates_and_flex_tiers():
    assert [p.prizepicks('power', n)['multiple'] for n in range(2, 7)] == [3, 6, 10, 20, 37.5]
    assert p.prizepicks('flex', 6)['tiers'] == {0: 25, 1: 2, 2: .4}
    assert p.prizepicks('power', 7) is None
    assert p.prizepicks('unknown', 2) is None


def test_reference_is_never_presented_as_executable_ev():
    legs=[{'sport':'nba','line_type':'standard'} for _ in range(6)]
    result=rules.ev_status('prizepicks','power',legs,None,0)
    assert not result['available']
    assert 'payout' not in result and 'only in its app' in result['reason']      # audit 2026-11-04: unknown payouts stay unknown
    assert 'ev' not in result
    legs[0]['line_type']='goblin'
    assert 'payout' not in rules.ev_status('prizepicks','power',legs,None,0)
