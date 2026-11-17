"""The actual Sports HTTP service, including member auth and stale removal."""
from datetime import datetime, timedelta, timezone
from sports import expansion_board as board, expansion_offers as offers
from sports.contracts import now_utc, utc
from sports.tests.test_service import env, svc, _get, _auth
from sports.tests.test_expansion_offers import pp


def test_http_board_history_and_disabled_paths(svc,monkeypatch,tmp_path):
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED','1')
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR',str(tmp_path))
    b=pp();b['data'][0]['attributes']['start_time']=utc(datetime.now(timezone.utc)+timedelta(hours=3))
    now=now_utc();row=offers.prizepicks(b,'soccer',now)[0]
    board.save('soccer',[row],{'prizepicks':{'ok':True,'checked_at':now}},now)
    path='/api/sports/expansion/board?sport=soccer'
    assert _get(svc,path)[0]==401
    code,body,_=_get(svc,path,_auth())
    assert code==200 and body['offers'][0]['revision']==row['revision']
    assert _get(svc,path+'&version='+body['version'],_auth())[1]['unchanged']
    hp='/api/sports/expansion/history?sport=soccer&offer_id=o1&platform=prizepicks'
    assert _get(svc,hp)[0]==401
    code,body,_=_get(svc,hp,_auth())
    assert code==200 and body['state']=='identity_pending'
    assert _get(svc,hp.replace('o1','arbitrary-player'),_auth())[0]==404
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED','0')
    assert _get(svc,path,_auth())[0]==404
    assert _get(svc,hp,_auth())[0]==404
