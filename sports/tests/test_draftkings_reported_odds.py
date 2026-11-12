import copy,json
from datetime import datetime,timedelta,timezone
from sports.games import espn_props,store
from sports import game_books

T=datetime(2026,11,12,18,tzinfo=timezone.utc)
def rows(prices=(-120,-106)):
    items=[{'competition':{'$ref':'https://sports.core.api.espn.com/events/123/competitions/123'},
      'athlete':{'$ref':'https://sports.core.api.espn.com/athletes/7'},
      'type':{'id':'1','name':'Total Points'},'current':{'target':{'value':21.5}},
      'open':{'target':{'value':18.5}},'odds':{'american':{'value':str(p)}},
      'lastUpdated':'2026-11-12T18:00Z'} for p in prices]
    return espn_props.parse({'items':items},'nba','basketball','123','100','DraftKings',{'7':{'player':'Example Player'}})

def test_prices_preserved_without_guessing_selection_side():
    a=rows();b=rows((-106,-120))
    assert a==b and len(a)==1
    assert json.loads(a[0]['reported_prices'])==[-120,-106]
    assert a[0]['price_over'] is a[0]['price_under'] is None
    assert rows(('NaN','oops','50'))[0]['reported_prices'] is None

def test_price_only_revision_late_read_and_old_row_preserved(tmp_path):
    db=store.connect(tmp_path/'g.db',dsn='')
    try:
        old=rows()[0];old.pop('reported_prices')
        assert store.save_prop_lines(db,[old],T)==1
        before=dict(db.one('SELECT * FROM sports_game_prop_lines WHERE id=1'))
        assert store.save_prop_lines(db,rows(),T+timedelta(minutes=1))==1
        assert store.save_prop_lines(db,rows(),T+timedelta(minutes=2))==0
        assert store.save_prop_lines(db,rows((-125,105)),T+timedelta(minutes=3))==1
        assert store.save_prop_lines(db,rows((-999,900)),T+timedelta(minutes=1))==0
        assert dict(db.one('SELECT * FROM sports_game_prop_lines WHERE id=1'))==before
        out=game_books.reference_props(db,'nba','123',T+timedelta(minutes=4))
        assert out[0]['reported_prices']==[-125,105]
        assert out[0]['price_side_known'] is False
        assert out[0]['price_over'] is out[0]['price_under'] is None
    finally:db.close()
