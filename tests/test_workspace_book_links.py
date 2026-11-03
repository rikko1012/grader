from types import SimpleNamespace
import pytest
import pplink
import workspace_api as api
import workspace_lol
import workspace_sports
from tests.test_workspace_all_sports import env, call, recs
from tests.workspace_other_fixture import leg


def test_mixed_four_sport_prizepicks_uses_classic_builder(monkeypatch):
    srv=SimpleNamespace(PP_OPEN_LINK=True,pplink=pplink,_pp_leg=lambda row,hit:{'book':'prizepicks','projection_id':'100','side':'over','line':20.5})
    monkeypatch.setattr(api,'_feed_leg',lambda row,side:row)
    legs=[{'id':s,'sport':s,'side':'under' if s=='nfl' else 'over'} for s in api.SPORTS]
    rows={'cs2':{'_x':{}},'lol':{'_x':{'platform':'prizepicks','projection_id':'101','line':12.5,'lt':'standard'}},
          'nba':{'_x':{'p':'prizepicks','r':'offer:prizepicks:102:r2:abc','l':25.5,'variant':'standard'}},
          'nfl':{'_x':{'p':'prizepicks','r':'offer:prizepicks:103:r1:abc','l':250.5,'variant':'standard'}}}
    result=api._open_link(srv,'prizepicks',legs,rows)
    assert result['url']=='https://app.prizepicks.com/board?projections=100-o-20.5,101-o-12.5,102-o-25.5,103-u-250.5'
    rows['nba']['_x']['r']='offer:prizepicks:unknown:r2:abc'
    assert api._open_link(srv,'prizepicks',legs,rows) is None


@pytest.mark.parametrize('sport',['lol','nba','nfl'])
def test_two_pick_links_for_each_sport_and_flag(monkeypatch,sport):
    srv=SimpleNamespace(PP_OPEN_LINK=True,pplink=pplink)
    rows={};legs=[]
    for i in range(2):
        legs.append({'id':str(i),'sport':sport,'side':'over'})
        x={'platform':'prizepicks','projection_id':str(200+i),'line':10.5,'lt':'standard'} if sport=='lol' else {
            'p':'prizepicks','r':f'offer:prizepicks:pp:{200+i}:r1:abc','l':10.5,'variant':'standard'}
        rows[str(i)]={'_x':x}
    assert api._open_link(srv,'prizepicks',legs,rows)['url'].endswith('200-o-10.5,201-o-10.5')
    srv.PP_OPEN_LINK=False
    assert api._open_link(srv,'prizepicks',legs,rows) is None


def test_goblin_under_and_duplicate_projection_refused():
    srv=SimpleNamespace(PP_OPEN_LINK=True,pplink=pplink)
    legs=[{'id':'a','sport':'nba','side':'under'},{'id':'b','sport':'nfl','side':'over'}]
    rows={k:{'_x':{'p':'prizepicks','r':f'offer:prizepicks:{i}:r1:abc','l':10.5,'variant':'goblin'}} for k,i in [('a',1),('b',2)]}
    assert api._open_link(srv,'prizepicks',legs,rows) is None
    legs[0]['side']='over'; rows['b']['_x']['r']=rows['a']['_x']['r']
    assert api._open_link(srv,'prizepicks',legs,rows) is None


def test_underdog_preserves_per_leg_links_not_fabricated_prefill():
    link=pplink.ud_link('cc625c31-9790-4a65-b814-766222f427d2')
    srv=SimpleNamespace(PP_OPEN_LINK=True,pplink=pplink,ud_row_link=lambda rid:link)
    legs=[{'id':s,'sport':s,'row_id':s,'side':'over'} for s in api.SPORTS]
    out=api._open_link(srv,'underdog',legs,{s:{'_x':{'ud':link}} for s in api.SPORTS})
    assert out['url']==pplink.UD_HOME and out['legs']==[link]*4


@pytest.mark.parametrize('sport', api.SPORTS)
def test_single_market_link_rechecks_real_member_record(env, monkeypatch, sport):
    import server
    monkeypatch.setattr(server, 'PP_OPEN_LINK', True)
    link = pplink.ud_link('cc625c31-9790-4a65-b814-766222f427d2')
    monkeypatch.setattr(server, 'ud_row_link', lambda row: link)
    monkeypatch.setattr(workspace_lol, 'entry_link', lambda row, side: link)
    monkeypatch.setattr(workspace_sports, 'entry_link', lambda row, side: link)
    records = recs(env, sport)
    r = next(r for r in records.values() if r['offer']['platform']=='underdog')
    l = leg(r)
    status, body = call(env, 'POST', 'book-line', {'legs':[l]})
    assert status == 200, body
    assert body['url'] == link
    l['line'] += 1
    status, body = call(env, 'POST', 'book-line', {'legs':[l]})
    assert status == 409 and 'url' not in body
    status, body = call(env, 'POST', 'book-line', {'legs':[l]}, user=None)
    assert status in (401, 403, 404) and 'url' not in body
