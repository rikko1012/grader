import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from sports import config, market_view
from sports.books import pointsbet_au as pb, betrivers, bridge, providers
from sports.books.base import Event
from sports.books.unibet_au import UnibetAU

NOW=datetime(2026,11,12,12,tzinfo=timezone.utc)
F=Path(__file__).parent/'fixtures/new_books'

@pytest.mark.parametrize('lg',['nba','nfl'])
def test_real_pointsbet_prices_identity_and_scope(lg):
    d=json.loads((F/f'pointsbet-{lg}.json').read_text());ev=pb.event(d,lg,NOW)
    rows=pb.parse_props(d,config.league(lg),ev,NOW)
    assert rows and {r['side'] for r in rows}=={'over','under'}
    assert all(r['book']=='pointsbet_au' and r['jurisdiction']=='AU' and r['period']=='game' for r in rows)
    assert all(r['provider_selection_id'] and r['price_decimal']>1 for r in rows)
    for change in ('live','wrong_game','started','hidden','closed','period','line'):
        x=copy.deepcopy(d)
        if change=='live':x['isLive']=True
        if change=='wrong_game':x['key']='999'
        if change=='started':x['startsAt']='2026-11-11T00:00:00Z'
        if change=='period':
            for m in x['fixedOddsMarkets']:m['eventName']+=' First Half'
        for m in x['fixedOddsMarkets']:
            for o in m['outcomes']:
                if change=='hidden':o['isHidden']=True
                if change=='closed':o['isOpenForBetting']=False
                if change=='line':o['points']+=1
        assert pb.parse_props(x,config.league(lg),ev,NOW)==[],change

@pytest.mark.parametrize('lg,fixture',[('nba','unibet.json'),('nfl','unibet-nfl.json')])
def test_unibet_au_real_prices_never_relabelled_betrivers(lg,fixture):
    d=json.loads((F/fixture).read_text());ev=Event(**d['event'])
    rows=betrivers.parse_props(d,config.league(lg),ev,provider='unibet_au',book='unibet_au',region='AU')
    assert rows and all(r['provider']=='unibet_au' and r['jurisdiction']=='AU' for r in rows)
    assert lg in UnibetAU().leagues
    b=bridge.to_offers(rows,config.league(lg),'espn:1','2026-11-12T12:00:00Z')
    assert b.records and all(r.platform=='unibet_au' and dict(r.extras)['jurisdiction']=='AU' for r in b.records)
    for r in b.records:
        if r.variant=='alternate':assert not r.side('under').offered

def test_new_books_do_not_change_registered_or_display_consensus():
    from sports.model import market
    for name in ('unibet_au','pointsbet_au'):
        assert name not in market.DIRECT_BOOKS
        assert market_view.source_status(name)[0] is False
        assert providers.ROUTES[name]==(name,)

@pytest.mark.parametrize('provider',[UnibetAU,pb.PointsBetAU])
def test_refusal_is_not_retried_or_disguised(monkeypatch,provider):
    from sports import net
    calls=[]
    def refused(*a,**kw):
        calls.append((a,kw));raise net.HttpError(403,'refused')
    monkeypatch.setattr(net,'get_json',refused)
    result=provider().events(config.league('nba'))
    assert result.blocked and len(calls)==1 and calls[0][1]['tries']==1
