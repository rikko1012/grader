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


def test_sg2_degenerate_projection_is_a_research_estimate_with_its_reason():
    """sg-2 (2026-11-05): a floor / degenerate / thin-history hold reaches the member board as a Research estimate with
    the reason, no letter, no stake - even though the published row carries a 100% chance."""
    for code, text in (('floor', "Held: projection is at the model's floor (no usable inputs)"),
                       ('certain', 'Held: the forecast puts 100% on one side (degenerate distribution)'),
                       ('thin', 'Held: fewer than 3 games with this stat')):
        q={'p':'underdog','r':'offer:underdog:9','l':1.5,'o':[1,1],'f':'f1','x':{'b':code,'t':text,'v':'sg-2'}}
        m={'k':'event|player|rec|full_game','e':'event','pid':'player','n':'Travis Hunter','st':'rec','mk':None}
        doc={'fc':{'f1':{'m':'sm-1','po':0.0,'pu':1.0,'pp':0,'mu':0.05}}}
        rec,x=ws.record_of('nfl',doc,m,q,{})
        s=rec['scout']
        assert s['grade'] is None and s['units'] is None and s['side'] is None and x['lean'] is None
        assert s['hold']=={'label':'Research estimate','reason':text} and s['validation']['label']=='Research estimate'


def test_explicit_containment_and_sanity_reasons_are_preserved():
    assert record({'b':'gate'})['hold']['reason']==ws.HOLD_DETAIL.format(LG='NBA')
    assert record({'b':'l10_contra','t':'Recent results contradict this lean'})['hold']['reason']=='Recent results contradict this lean'
