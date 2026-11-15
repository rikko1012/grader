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


def test_matchup_uses_exact_teams_and_home_venue():
    d = fixture()
    c = d["header"]["competitions"][0]
    ids = {r["homeAway"]:r["team"]["id"] for r in c["competitors"]}
    d["predictor"] = {s+"Team":{"id":tid,"gameProjection":60 if s=="away" else 40} for s,tid in ids.items()}
    signals = gb.parse_summary(d)["winner_matchup"]
    assert signals["ESPN matchup projection"] == .2
    assert signals["Season record"] < 0 and signals["Home/road record"] < 0
    c["neutralSite"] = True
    d["predictor"]["homeTeam"]["id"] = ids["away"]
    assert set(gb.parse_summary(d)["winner_matchup"]) == {"Season record"}


def test_recent_winners_ignore_scores_first_seen_after_quote():
    event = {"winner_team_ids": ["A","B"]}
    game = {"home_id":"B","away_id":"A","home_score":80,"away_score":110,
            "start":"2026-11-01T00:00:00Z","fetched_at":"2026-11-02T00:00:00Z"}
    late = dict(game, home_score=190, fetched_at="2026-11-09T00:00:00Z")
    cutoff = "2026-11-08T00:00:00Z"
    actual = gb.recent_matchup([late, game], event, cutoff, "nba")
    assert actual == gb.recent_matchup([game], event, cutoff, "nba")
    assert actual["Recent scoring margin"] > 0 and actual["Recent head-to-head"] > 0

def test_no_opening_fallback_and_no_inferred_price():
    d=fixture()
    for key in ('pointSpread','moneyline','total'):
        for side in ('home','away','over','under'):
            d['pickcenter'][0].get(key,{}).get(side,{}).pop('close',None)
    assert gb.parse_summary(d) is None

def test_mismatched_total_rejected():
    d=fixture();d['pickcenter'][0]['total']['under']['close']['line']='u299.5'
    assert gb.parse_summary(d) is None


def test_first_collection_is_pending_not_a_service_outage():
    from types import SimpleNamespace
    status, doc = gb.view(SimpleNamespace(query={"league":"nba"}))
    assert status == 200 and doc["pending"] and doc["events"] == []

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


def test_reference_lines_latest_revision_exact_event_and_freshness(tmp_path):
    db=store.connect(tmp_path/'refs.db',dsn='')
    try:
        row={'source':'espn','book':'draftkings','provenance':'ESPN-supplied DraftKings','league':'nba',
             'event_id':'123','prop_key':'nba:123:100:7:1','athlete_id':'7','player':'Example Player',
             'player_key':'exampleplayer','team_id':'9','market':'points','period':'1q','kind':'milestone',
             'raw_type_id':'1','raw_type':'Points Milestones - 1st Quarter','line':4.5,'line_open':4.5,
             'price_over':None,'price_under':None,'provider_updated_at':None,'status':'open'}
        store.save_prop_lines(db,[row],NOW)
        store.save_prop_lines(db,[dict(row,line=5.5)],NOW+timedelta(minutes=5))
        store.save_prop_lines(db,[dict(row,line=99.5)],NOW+timedelta(minutes=1))
        rows=gb.reference_props(db,'nba','123',NOW+timedelta(minutes=10))
        assert len(rows)==1 and rows[0]['line']==5.5 and rows[0]['period']=='1q'
        assert rows[0]['price_over'] is None and rows[0]['price_under'] is None
        assert gb.reference_props(db,'nfl','123',NOW+timedelta(minutes=10))==[]
        assert gb.reference_props(db,'nba','999',NOW+timedelta(minutes=10))==[]
        assert gb.reference_props(db,'nba','123',NOW+timedelta(hours=2))==[]
    finally:db.close()
