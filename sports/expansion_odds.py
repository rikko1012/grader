"""Isolated source-native soccer/tennis book markets, never pick'em multipliers.

The ordinary BetRivers Ohio feed is the same public transport used by the
existing adapter. Names/periods remain native: these markets are not silently
joined to another book, used for arbitrage or treated as a calibrated forecast.
"""
from datetime import datetime, timezone
import json

from .contracts import now_utc, parse_utc
from .expansion_offers import number, timestamp, finish

BASE = 'https://eu-offering-api.kambicdn.com/offering/v2018/rsiusoh'
PARAMS = {'lang':'en_US','market':'US'}
PATHS = {'soccer':'football','tennis':'tennis'}


def kambi_event(event, offers, sport, observed):
    """Keep exact offered outcomes and source market labels; no fake sides."""
    rows=[]
    now=timestamp(observed); start=timestamp(event.get('start'))
    if (sport not in PATHS or event.get('sport') != PATHS[sport].upper()
            or event.get('state')!='NOT_STARTED' or not now or not start or start<=now
            or not event.get('id') or not event.get('homeName') or not event.get('awayName')):
        return rows
    path=event.get('path') or []
    competition=' / '.join(p.get('name','') for p in path[1:])
    # Singles research boards cannot quietly absorb doubles or virtual matches.
    if 'virtual' in competition.lower() or (sport=='tennis' and
            any(x in competition.lower() for x in ('doubles','esport','table tennis'))):
        return rows
    for market in offers:
        tags=set(market.get('tags') or [])
        crit=market.get('criterion') or {}
        if (str(market.get('eventId'))!=str(event['id']) or not market.get('id')
                or not crit.get('label') or 'OFFERED_PREMATCH' not in tags
                or tags & {'BOOST','PROMO'}):
            continue
        outcomes=[]
        for o in market.get('outcomes') or []:
            dec=number(o.get('odds')); american=number(o.get('oddsAmerican'))
            changed=timestamp(o.get('changedDate'))
            if (o.get('status')!='OPEN' or not o.get('id') or not o.get('label')
                    or dec is None or dec<=1000 or american is None or abs(american)<100
                    or not american.is_integer() or not changed or changed>now):
                continue
            dec/=1000
            # Source decimal and American presentations can round differently.
            implied=1+american/100 if american>0 else 1+100/abs(american)
            if abs(implied-dec)>max(.02, dec*.005):
                continue
            rawline=o.get('line'); line=number(rawline)
            if rawline is not None and line is None:
                continue
            outcomes.append({'selection_id':str(o['id']), 'label':o['label'],
                'participant':o.get('participant'), 'type':o.get('type'),
                'line':None if line is None else line/1000,
                'decimal':dec, 'american':int(american), 'source_updated_at':changed})
        if not outcomes:
            continue
        rows.append(finish({'sport':sport,'platform':'betrivers','source':'kambi',
            'jurisdiction':'US-OH','offer_id':str(market['id']),
            'event_id':f'{sport}:betrivers:{event["id"]}', 'match':event['name'],
            'teams':[event['homeName'],event['awayName']], 'competition':competition,
            'start':start,'label':crit['label'],'scope':crit.get('lifetime'),
            'market_type':(market.get('betOfferType') or {}).get('name'),
            # One-sided player ladders can also carry MAIN_LINE. They must not
            # appear as the default two-way line merely because of that tag.
            'main':bool(tags & {'MAIN','MAIN_LINE'}) and len(outcomes)>=2 and
                   (market.get('betOfferType') or {}).get('id')!=127, 'outcomes':outcomes,
            'observed_at':now,'identity_status':'provider_only',
            'settlement_rules':'book_native_not_cross_book_verified'}))
    return rows


def draftkings_soccer(body, observed):
    """ESPN's explicitly current DraftKings 3-way match odds, never open odds."""
    rows=[]; now=timestamp(observed)
    for event in body.get('events') or []:
        for c in event.get('competitions') or []:
            start=timestamp(c.get('date') or event.get('date'))
            if not start or start<=now or (c.get('status') or event.get('status') or {}).get('type',{}).get('state')!='pre':
                continue
            teams={x.get('homeAway'):(x.get('team') or {}).get('displayName') for x in c.get('competitors') or []}
            if not all(teams.get(k) for k in ('home','away')):continue
            for offer in c.get('odds') or []:
                if not isinstance(offer,dict):continue
                if str((offer.get('provider') or {}).get('id'))!='100':continue
                outcomes=[]
                for side in ('home','draw','away'):
                    value=number(((offer.get('moneyline') or {}).get(side) or {}).get('close',{}).get('odds'))
                    if value is None or abs(value)<100 or not value.is_integer():break
                    outcomes.append({'selection_id':side,'label':teams.get(side,'Draw'),'line':None,
                        'decimal':1+value/100 if value>0 else 1+100/abs(value),'american':int(value),
                        'source_updated_at':None})
                if len(outcomes)!=3:continue
                rows.append(finish({'sport':'soccer','platform':'draftkings','source':'espn',
                    'jurisdiction':'US (feed does not identify state)', 'offer_id':str(c['id'])+':3way',
                    'event_id':'soccer:draftkings:espn:'+str(c['id']), 'match':event.get('name') or ' · '.join(teams.values()),
                    'teams':[teams['home'],teams['away']], 'competition':'Soccer', 'start':start,
                    'label':'Match result (3-way)', 'scope':'regulation','market_type':'Match',
                    'main':True,'outcomes':outcomes,'observed_at':now,'identity_status':'provider_only',
                    'settlement_rules':'book_native_not_cross_book_verified'}))
    return rows


def collect(sport, get, directory):
    """One sport listing + two rotating detail requests; bounded and resumable."""
    from .expansion_history import atomic_json
    from . import net
    path=directory/'odds-cursor.json'
    try:state=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):state={}
    rows=[];sources={}
    try:
        listing=get(BASE+'/listView/'+PATHS[sport]+'.json',params=PARAMS,timeout=12,tries=1)
        observed=now_utc()
        eligible=[]
        for item in listing.get('events') or []:
            e=item.get('event') or {}
            parsed=kambi_event(e,item.get('betOffers') or [],sport,observed)
            rows.extend(parsed)
            if parsed:eligible.append(e)
        eligible.sort(key=lambda e:(state.get(str(e['id']),''),e['start'],e['id']))
        for e in eligible[:2]:
            try:
                body=get(BASE+'/betoffer/event/'+str(e['id'])+'.json',params=PARAMS,timeout=12,tries=1)
                actual=[x for x in body.get('events') or [] if str(x.get('id'))==str(e['id'])]
                if len(actual)!=1:raise ValueError('book detail event mismatch')
                checked=now_utc();state[str(e['id'])]=checked
                detail=kambi_event(actual[0],body.get('betOffers') or [],sport,checked)
                rows=[r for r in rows if r['event_id']!=f'{sport}:betrivers:{e["id"]}']+detail
            except (net.HttpError,ValueError,OSError):
                state[str(e['id'])]=now_utc()
        sources['betrivers']={'ok':True,'checked_at':observed,'markets':len(rows)}
        atomic_json(path,{str(e['id']):state[str(e['id'])] for e in eligible if str(e['id']) in state})
    except (net.HttpError,ValueError,OSError) as exc:
        sources['betrivers']={'ok':False,'checked_at':now_utc(),'error':type(exc).__name__}
    if sport=='soccer':
        try:
            today=datetime.now(timezone.utc).strftime('%Y%m%d')
            b=get('https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard',
                  params={'dates':today,'limit':1000},timeout=12,tries=1)
            checked=now_utc();found=draftkings_soccer(b,checked);rows.extend(found)
            sources['draftkings']={'ok':True,'checked_at':checked,'markets':len(found)}
        except (net.HttpError,ValueError,OSError) as exc:
            sources['draftkings']={'ok':False,'checked_at':now_utc(),'error':type(exc).__name__}
    return rows,sources
