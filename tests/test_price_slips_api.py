import json
import time
from urllib.parse import urlencode
import workspace_ev as we
import workspace_markets as wm
from tests.test_workspace_api import env, get, post, idem
from tests.test_slip_generate_api import keys_on_board
from tests.workspace_fixture import leg_of
from tests.test_price_slips import market


def ev_doc(env):
    board=keys_on_board(env)
    rows=[]
    for r in board.values():
        if r['offer']['platform']!='prizepicks':continue
        side=r['scout'].get('side')
        rows.append(dict(key='|'.join((r['sport'],'prizepicks',r['offer']['offer_id'])),side=side,
                         line=r['offer']['line'],basis='market',ev=.12,p_market=.65,sources=[dict(kind='sportsbook',age_s=30)]))
    return {'as_of':time.time(),'app':'prizepicks','entry':'power','n':2,'rows':rows}


def test_ev_slips_generate_and_recheck_existing_entry_rules(env,monkeypatch):
    monkeypatch.setenv('SCOUT_PICKEM_EV','1')
    monkeypatch.setattr(we,'shared',lambda *a:(200,json.dumps(ev_doc(env)).encode()))
    st,d=get(env,'ev-slips?app=prizepicks&entry=power&n=2&slips=1&sports=cs2')
    assert st==200 and d['version']=='ev-slips-1' and len(d['slips'])==1,d
    board=keys_on_board(env)
    legs=[leg_of(board[(l['book'],l['row_id'])],l['side']) for l in d['slips'][0]['legs']]
    st,c=post(env,'recheck',{'idem':idem(),'legs':legs,'entry_types':{'prizepicks':'power'}})
    assert st==200 and c['entries']['prizepicks']['ok']


def test_feature_gates(env,monkeypatch):
    monkeypatch.delenv('SCOUT_PICKEM_EV',raising=False)
    monkeypatch.delenv('SCOUT_MARKET_TOOLS',raising=False)
    assert get(env,'ev-slips')[0]==404
    assert get(env,'market-slips')[0]==404


def test_market_route_checks_member_access_and_current_quotes(env,monkeypatch):
    monkeypatch.setenv('SCOUT_MARKET_TOOLS','1')
    d,r=market();r['sport']='cs2';d['sports']={'cs2':d['sports']['nba']}
    from workspace_books import _iso
    for l in r['legs']:l['seen_at']=_iso(time.time())
    monkeypatch.setattr(wm,'shared',lambda *a:(200,json.dumps(d).encode()))
    query=urlencode(dict(mode='arb',leg=r['leg_id'],books='fanduel,betrivers',stake='100',
                         quote=json.dumps([[l['book'],l['side'],l['odds']] for l in r['legs']])))
    st,out=get(env,'market-slips?'+query)
    assert st==200 and len(out['slips'])==2,out
    r['legs'][0]['odds']=2.0
    st,out=get(env,'market-slips?'+query)
    assert st==409 and 'price changed' in out['error']
