"""Build bounded market-tool snapshots from current canonical quotes, never a provider call.

Rule equivalence must be supplied by a reviewed house-rule registry. The legacy
'book house rules (not read)' marker is deliberately not an equivalence class.
"""
from collections import Counter, defaultdict
from dataclasses import asdict
from itertools import combinations
from .contracts import parse_utc


def build(items, now, validated=None, rules=None, limit=20000):
    from gradecore.market_tools import Quote, compare_pair, expected_value
    validated=validated or {};rules=rules or {}
    excluded=Counter();groups=defaultdict(list);ev=[];count=0
    ts=parse_utc(now).timestamp()
    for rid,o,meta in items:
        if o.platform in ('underdog','prizepicks'):continue
        count+=1
        if count>limit:
            return {'as_of':now,'state':'unavailable','error':'Quote budget exceeded; no partial opportunity feed.',
                    'ev':[],'arbitrage':[],'excluded':dict(excluded)}
        if not all((o.event_id,o.player_id,o.stat)):
            excluded['Unresolved event or player']+=1;continue
        if o.availability!='open' or not o.listed_start_utc or parse_utc(o.listed_start_utc).timestamp()<=ts:
            excluded['Closed or started market']+=1;continue
        seen=meta.get('last_seen_at') or o.observed_at
        if not 0<=ts-parse_utc(seen).timestamp()<=120:
            excluded['Quote older than two minutes']+=1;continue
        extra=dict(o.extras)
        jurisdiction=extra.get('jurisdiction')
        rule=rules.get(o.platform+'|'+o.rule_version)
        if not rule or not jurisdiction:
            excluded['Settlement rules or jurisdiction not verified']+=1;continue
        if o.platform in ('kalshi','polymarket'):
            excluded['Executable liquidity and fees not verified']+=1;continue
        source=meta.get('last_source_updated_at') or o.source_updated_at
        if not source:
            excluded['Provider timestamp unavailable']+=1;continue
        if not 0 <= ts-parse_utc(source).timestamp() <= 120:
            excluded['Provider quote older than two minutes or future dated']+=1;continue
        for side in o.sides:
            if not side.offered or side.price is None:continue
            try:
                q=Quote(o.league,o.event_id,o.player_id,o.stat,o.scope,o.line,rule,o.platform,jurisdiction,
                        side.side,side.price,extra.get('feed') or o.platform,parse_utc(seen).timestamp(),parse_utc(source).timestamp())
            except ValueError:
                excluded['Invalid price or timestamp']+=1;continue
            groups[q.identity()].append(q)
            pred=validated.get(rid)
            if pred and pred.get('validated') is True and pred.get('offer_revision')==rid and pred.get('identity')==list(q.identity()[:6]):
                p=pred['p_'+q.side];loss=pred['p_under' if q.side=='over' else 'p_over']
                value=expected_value(p,loss,q.decimal_odds,q.commission)
                if value>0:ev.append({'quote':asdict(q),'p':p,'ev':value,'model_version':pred['model_version'],
                                      'width':pred.get('market_width'),'wide_market':pred.get('market_width',0)>.05})
    arb=[]
    for quotes in groups.values():
        # One current quote per book/side, even if a provider exposes duplicate IDs.
        best={}
        for q in quotes:
            k=(q.book,q.side)
            if k not in best or q.observed_at>best[k].observed_at:best[k]=q
        for a,b in combinations(best.values(),2):
            result=compare_pair(a,b,ts)
            if result.get('opportunity'):arb.append({'legs':[asdict(a),asdict(b)],**result})
    if not validated:excluded['Validated shared-grader probabilities unavailable']=count
    return {'as_of':now,'state':'ready','quotes_checked':count,'ev':sorted(ev,key=lambda x:x['ev'],reverse=True)[:100],
            'arbitrage':sorted(arb,key=lambda x:x['quoted_return'],reverse=True)[:100],
            'excluded':dict(excluded),'execution_guaranteed':False}
