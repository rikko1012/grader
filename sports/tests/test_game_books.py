import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from sports import game_books as gb
from sports.games import store, espn_game
NOW=datetime(2026,11,11,12,tzinfo=timezone.utc)
def fixture():
    return json.loads((Path(__file__).parent/'fixtures/game_books/nfl.json').read_text())

def test_explicit_current_prices():
    d=fixture();e=gb.parse_summary(d)
    assert e['books']['draftkings']['markets']['spread']=={'homeSpread':-2.5,'homeOdds':-105.,'awaySpread':2.5,'awayOdds':-115.}
    assert e['books']['draftkings']['markets']['money-line']=={'homeOdds':-142.,'awayOdds':120.}
    assert espn_game.context(d)['book_game_prices']==e

def test_wrong_provider_team_and_started():
    for change in ('provider','team','started'):
        d=fixture()
        if change=='provider': d['pickcenter'][0]['provider']['id']='999'
        if change=='team': d['pickcenter'][0]['homeTeamOdds']['teamId']='999'
        if change=='started':d['header']['competitions'][0]['status']['type']['state']='in'
        assert gb.parse_summary(d) is None

def test_no_opening_fallback_and_no_inferred_price():
    d=fixture()
    for key in ('pointSpread','moneyline','total'):
        for side in ('home','away','over','under'):
            d['pickcenter'][0].get(key,{}).get(side,{}).pop('close',None)
    assert gb.parse_summary(d) is None

def test_mismatched_total_rejected():
    d=fixture();d['pickcenter'][0]['total']['under']['close']['line']='u299.5'
    assert gb.parse_summary(d) is None

def test_current_revision_persistence_and_seen_time(tmp_path):
    db=store.connect(tmp_path/'games.db',dsn='')
    try:
        e=gb.parse_summary(fixture())
        store.save_snapshot(db,'nfl',e['id'],'book_game_prices','espn',e,NOW)
        e['books']['draftkings']['markets']['money-line']['homeOdds']=-160.
        store.save_snapshot(db,'nfl',e['id'],'book_game_prices','espn',e,NOW+timedelta(minutes=5))
        e['books']['draftkings']['markets']['money-line']['homeOdds']=-999.
        store.save_snapshot(db,'nfl',e['id'],'book_game_prices','espn',e,NOW+timedelta(minutes=1))
        assert gb.refresh(db,'nfl',NOW+timedelta(minutes=20))=={'events':1}
        doc=json.loads(gb.path('nfl').read_text());event=doc['events'][0]
        assert event['books']['draftkings']['markets']['money-line']['homeOdds']==-160.
        assert datetime.fromisoformat(event['seen_at'].replace('Z','+00:00'))==NOW+timedelta(minutes=5)
        assert gb.refresh(db,'nfl',NOW+timedelta(days=5))=={'events':0}
    finally:db.close()
