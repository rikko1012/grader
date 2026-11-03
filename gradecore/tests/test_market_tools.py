from dataclasses import replace
import pytest
from gradecore.market_tools import Quote, expected_value, compare_pair


def quote(**kw):
    values=dict(sport='nba',event_id='e',player_id='p',stat='points',period='full',line=20.5,
                settlement_rule='full-including-ot',book='a',jurisdiction='US-OH',side='over',
                decimal_odds=2.1,source='feed:a',observed_at=100,source_at=99)
    return Quote(**{**values,**kw})


def test_ev_is_return_not_probability_gap_and_accounts_for_push():
    assert expected_value(.3,.7,4)==pytest.approx(.2)
    assert expected_value(.5,.4,2)==pytest.approx(.1)
    assert expected_value(.5,.4,2,.1)==pytest.approx(.05)


@pytest.mark.parametrize('kw',[{'period':'half'}, {'line':21.5}, {'event_id':'other'},
    {'player_id':'other'}, {'settlement_rule':'regulation'}, {'jurisdiction':'US-NY'},
    {'side':'over'}, {'book':'a'}, {'available':False}, {'liquid':False}, {'source_at':1}])
def test_no_false_arb_across_incompatible_or_stale_quotes(kw):
    a=quote();b=quote(book='b',side='under')
    assert not compare_pair(a,replace(b,**kw),now=150,max_age=120)['eligible']


def test_stakes_equalize_actual_returns_and_fee_can_remove_opportunity():
    a=quote();b=quote(book='b',side='under',decimal_odds=2.2)
    r=compare_pair(a,b,150)
    assert r['opportunity'] and sum(r['stake_shares'])==pytest.approx(1)
    assert r['stake_shares'][0]*2.1-1==pytest.approx(r['quoted_return'])
    assert r['stake_shares'][1]*2.2-1==pytest.approx(r['quoted_return'])
    assert not r['execution_guaranteed']
    assert not compare_pair(replace(a,commission=.3),replace(b,commission=.3),150)['opportunity']


@pytest.mark.parametrize('kw',[{'line':float('nan')},{'decimal_odds':1},{'source_at':101},{'source':''}])
def test_quote_contract_rejects_bad_prices_or_provenance(kw):
    with pytest.raises(ValueError):quote(**kw)
