import json

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
    assert record(forecast=False)['hold']['reason'] == 'Grader has no forecast for this line.'


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
        assert s['hold']=={'label':'Research estimate','reason':text,'short':ws.HOLD_SHORT[code]} and s['validation']['label']=='Research estimate'


def test_explicit_containment_and_sanity_reasons_are_preserved():
    assert record({'b':'gate'})['hold']['reason']==ws.HOLD_DETAIL.format(LG='NBA')
    assert record({'b':'l10_contra','t':'Recent results contradict this lean'})['hold']['reason']=='Recent results contradict this lean'


def test_ct1b_released_row_gets_side_and_letter_with_no_hedge_text():
    """ct-1b (owner 2026-11-08): a released NBA / NFL quote carries the cf-2 signal with its letter (no x.b gate), so the hub
    row shows the side + letter, 0 units, and no "not validated" / "held" / "clear review" text anywhere on it."""
    for lg in ('nba', 'nfl'):
        q={'p':'underdog','r':'offer:underdog:5','l':10.5,'o':[1,1],'f':'f1','x':{'v':'cf-2','ln':'over','gr':'B','gp':'sports-gp-1'}}
        m={'k':'event|player|points|full_game','e':'event','pid':'player','n':'Player','st':'points','mk':None}
        doc={'fc':{'f1':{'m':'sm-1','po':.57,'pu':.43,'pp':0,'mu':12}}}
        rec,x=ws.record_of(lg,doc,m,q,{})
        s=rec['scout']
        assert s['side']=='over' and s['grade']=='B' and s['units']==0 and s['hold'] is None and x['lean']=='over'
        text=json.dumps(rec).lower()
        for bad in ('not validated','not forward-validated','held','clear review','switched off'):
            assert bad not in text, (lg, bad)


def test_ct1b_kill_switch_gate_reads_switched_off():
    s=record({'b':'gate','t':'NBA model not validated - projection only','v':'vg-1'})
    assert s['side'] is None and s['grade'] is None and s['hold']['reason']=='NBA grades are switched off.'
    assert 'clear review' not in ws.HOLD_DETAIL


# ---- owner 2026-11-12: every NBA / NFL row says why it has no letter (hold.short), and exchanges carry their own price ----

def test_no_forecast_reason_comes_from_the_pipeline_not_a_guess():
    def why(nf, st='pra'):
        q={'p':'prizepicks','r':'offer:prizepicks:2','l':30.5,'o':[1,1],'f':None,'nf':nf}
        m={'k':'event|player|pra|full_game','e':'event','pid':'player','n':'Player','st':st,'mk':None}
        return ws.record_of('nba',{'fc':{},'stats':{'pra':'Pts+Rebs+Asts'}},m,q,{})[0]['scout']['hold']
    h=why('Market not supported by the Grader model: combo')
    assert h['short']=='Stat not graded' and h['reason']=="Grader's model does not cover Pts+Rebs+Asts."
    assert why('Line appeared after the last forecast run')['short']=='New line'
    assert why('No Grader forecast: no games in the data')['reason'].endswith('no games in the data')
    assert why('Line not modelled: promotional')['short']=='Line not graded'
    assert why(None)['short']=='No forecast'


def test_every_hold_has_a_short_plain_reason():
    assert record()['hold']['short']=='No clear side'
    assert record(market={'l':10.5,'c':{'n':2,'po':.65,'pu':.35}})['hold']['short']=='No edge over books'
    assert record({'b':'av_out','t':'Ruled out (ESPN injury report)','v':'av-1'})['hold']['short']=='Ruled out'
    assert record({'b':'cap','t':'Grader grades lines priced near even.','v':'cap-3'})['hold']['short']=='Priced far from even'
    assert record({'b':'gate'})['hold']['short']=='Letters switched off'
    for code in ('l10_contra','ratio_low','thin','av_inactive'):
        assert record({'b':code,'t':'x'})['hold']['short']==ws.HOLD_SHORT[code]


def test_exchange_rows_carry_their_own_price_per_side():
    q={'p':'polymarket','r':'offer:polymarket:1','l':10.5,'o':[1,1],'f':'f1','k':[0.52,0.55],'x':{'v':'cf-2','ln':None}}
    m={'k':'event|player|points|full_game','e':'event','pid':'player','n':'Player','st':'points','mk':None}
    o=ws.record_of('nba',{'fc':{'f1':{'m':'sm-1','po':.6,'pu':.4,'pp':0,'mu':12}}},m,q,{})[0]['offer']
    assert o['over']['ask']==0.55 and o['under']['ask']==0.48 and o['over']['price'] is None
    q['k']=[None,0.55]
    o=ws.record_of('nba',{'fc':{}},m,q,{})[0]['offer']
    assert o['over']['ask']==0.55 and 'ask' not in o['under']
    q.pop('k')
    o=ws.record_of('nba',{'fc':{}},m,q,{})[0]['offer']
    assert 'ask' not in o['over'] and 'ask' not in o['under']


def test_pp_side_hold_names_its_reason_on_the_row():
    """sweep 2026-11-23 (nbanfl-2): an os-2 side hold (PrizePicks does not list Grader's side) says so in plain words on the
    row instead of a bare "No grade"; side, letter and stake stay removed exactly as before."""
    text="Grader's side is not offered on this line"            # sports/grade_rules.HOLD_TEXT (CS2 tests never import sports)
    s=record({'b':'side','t':text,'v':'os-2'})
    assert s['hold']['short']=='Side not offered' and s['hold']['reason']==text
    assert s['side'] is None and s['grade'] is None and s['units'] is None
