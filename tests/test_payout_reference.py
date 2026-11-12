import payout_reference as p
import workspace_rules as rules


def test_published_reference_rates_and_flex_tiers():
    assert [p.prizepicks('power', n)['multiple'] for n in range(2, 7)] == [3, 6, 10, 20, 37.5]
    assert p.prizepicks('flex', 6)['tiers'] == {0: 25, 1: 2, 2: .4}
    assert p.prizepicks('power', 7) is None
    assert p.prizepicks('unknown', 2) is None


def test_verified_table_is_shown_but_research_chances_get_no_ev():
    """Owner sign-off 2026-11-05: the PrizePicks table is verified, so its payout is shown; an NBA chance still has no EV."""
    legs=[{'sport':'nba','line_type':'standard','match':f'm{i}','line':20.5,'on_side':True,'p':0.6} for i in range(6)]
    result=rules.ev_status('prizepicks','power',legs,None,0)
    assert not result['available'] and 'ev' not in result
    assert result['payout']['multiple'] == 37.5 and 'settled-results check' in result['reason']
    legs[0]['line_type']='goblin'
    assert 'payout' not in rules.ev_status('prizepicks','power',legs,None,0)       # goblin / demon: unpublished payout
