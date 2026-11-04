import workspace_sports as ws


def record(signal=None, forecast=True, market=None):
    q={'p':'underdog','r':'offer:underdog:1','l':10.5,'o':[1,1],
       'f':'f1' if forecast else None,'x':signal or {'v':'cf-2','ln':None}}
    m={'k':'event|player|points|full_game','e':'event','pid':'player','n':'Player',
       'st':'points','mk':market}
    doc={'fc':{'f1':{'m':'sm-1','po':.6,'pu':.4,'pp':0,'mu':12}}}
    return ws.record_of('nba',doc,m,q,{})[0]['scout']


def test_no_direction_does_not_claim_league_is_contained():
    s=record()
    assert s['side'] is None
    assert s['hold']['reason']=='The forecast does not give a clear direction at this line.'
    assert s['validation']['detail']==s['hold']['reason']


def test_market_rejection_explains_price_instead_of_league_gate():
    s=record(market={'l':10.5,'c':{'n':2,'po':.65,'pu':.35}})
    assert 'market already prices' in s['hold']['reason']
    assert s['grade'] is None


def test_missing_forecast_is_not_a_containment_claim():
    assert 'matching forecast' in record(forecast=False)['hold']['reason']


def test_explicit_containment_and_sanity_reasons_are_preserved():
    assert record({'b':'gate'})['hold']['reason']==ws.HOLD_DETAIL.format(LG='NBA')
    assert record({'b':'l10_contra','t':'Recent results contradict this lean'})['hold']['reason']=='Recent results contradict this lean'
