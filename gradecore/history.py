"""Convert immutable esports exports into the shared experiment contract.

Explicit model cohorts only. Never reconstruct an earlier forecast from today's
player stats or use a later grade as if it had been available before the game.
"""
import json
from collections import Counter
from . import adapters
from .evaluation import timestamp


def object_value(value):
    return json.loads(value) if isinstance(value, str) else value


def cs2(forecasts, picks, model_version):
    lookup = {str(r['id']): r for r in picks}
    chosen, excluded = {}, Counter()
    for f in forecasts:
        try:
            if f.get('model_version') != model_version:
                excluded['other_model'] += 1; continue
            p = lookup.get(str(f['pick_id']))
            # picklog: 0=member, 1=owner/load test, 2=automatic real board.
            # A truthiness check would discard the board's actual forecasts.
            if not p or p.get('test',0) not in (0,2,None) or p.get('status') not in ('settled', 'push', 'void'):
                excluded['not_real_settled_pick'] += 1; continue
            at, start, settled = map(timestamp, (f['logged_at'], f['match_start'], p['settled_at']))
            if (start-at).total_seconds() < 1800 or settled < start:
                excluded['invalid_time_or_inside_30_minutes'] += 1; continue
            if f['line'] != p['line'] or not all(p.get(k) for k in ('match_slug','slug','prop','scope','book')):
                excluded['identity_not_replayable'] += 1; continue
            raw = object_value(f.get('probs_raw'))
            if not isinstance(raw, dict):
                excluded['no_raw_probability'] += 1; continue
            values = [raw[k] for k in ('over','under','push')]
            if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not 0 <= x <= 1 for x in values) or abs(sum(values)-1) > .0002:
                excluded['invalid_raw_probability'] += 1; continue
            side = adapters.SIDES.get(f['pick'])
            if not side or raw['over'] + raw['under'] <= 0:
                excluded['no_selected_decided_side'] += 1; continue
            # the same settlement rule as the shadow adapters (void stays void; actual == line is a push, never a win)
            result = adapters.settlement_of(p['status'], p.get('actual'), f['line'], side)
            if result is None:
                excluded['missing_actual'] += 1; continue
            status = result.status
            # the quote saved WITH the forecast (full export rows carry forecasts.quote; compact pages do not):
            # Underdog's two decimal prices, observed at or before the forecast, else no market for this row
            quote, observed = adapters.cs2_quote(f.get('quote'), p.get('book'), f['logged_at'])
            key = tuple(p[k] for k in ('match_slug','slug','prop','scope','book')) + (f['line'],)
            row = dict(forecast_id=str(f['id']), sport='cs2', match_key=p['match_slug'], market=p['prop'],
                       player_id=p['slug'], scope=p['scope'], line=f['line'], book=p['book'], side=side,
                       p=raw[side]/(raw['over']+raw['under']),
                       market_p=getattr(quote, 'p_' + side) if quote else None, market_at=observed if quote else None,
                       market_source=quote.source if quote else None, status=status,
                       published_p=f.get('p_pick'),
                       model_version=model_version, artifact_version=model_version,
                       probability_stage='raw_distribution_rounded_4dp', forecast_at=f['logged_at'],
                       start_at=f['match_start'], settled_at=p['settled_at'])
            previous = chosen.get(key)
            if previous is None or (at,int(f['id'])) > (timestamp(previous['forecast_at']),int(previous['forecast_id'])):
                chosen[key] = row
        except (KeyError, TypeError, ValueError):
            excluded['malformed_record'] += 1
    return list(chosen.values()), dict(excluded)


def lol(forecasts, settlements, model_version, artifact_version):
    settled, chosen, excluded = {}, {}, Counter()
    def identity(r):
        scope = {'1':'maps_1', '12':'maps_1_2', '123':'maps_1_2_3',
                 '1234':'maps_1_2_3_4', '12345':'maps_1_2_3_4_5'}.get(r['scope'],r['scope'])
        return tuple(r[k] for k in ('platform','sid','pid','stat')) + (scope,r['line'])
    def key(r):
        # Match the serving worker's immutable settlement unit, including the
        # market/variant identifier. Forecast scopes use '123', settlements
        # use 'maps_1_2_3'; identity is checked separately after the join.
        if r.get('unit_key'): return r['unit_key']
        if r.get('market_key'): return r['platform']+'|'+r['market_key']+'|'+str(float(r['line']))
        return identity(r)
    for r in settlements:
        try:
            k = key(r)
            if k not in settled or int(r['version']) > int(settled[k]['version']): settled[k] = r
        except (KeyError,TypeError,ValueError): excluded['malformed_settlement'] += 1
    for f in forecasts:
        try:
            if f.get('model_version') != model_version or f.get('fit_sha') != artifact_version:
                excluded['other_model_or_artifact'] += 1; continue
            if not f.get('side_offered'):
                excluded['unplayable_side'] += 1; continue
            k = key(f); s = settled.get(k)
            if not s:
                excluded['not_settled'] += 1; continue
            if identity(f) != identity(s):
                excluded['settlement_identity_mismatch'] += 1; continue
            if s.get('status') not in ('graded', 'push', 'void'):
                excluded['unverified_or_pending_result'] += 1; continue
            at,start,done = map(timestamp,(f['forecast_at'],f['series_start'],s['settled_at']))
            if (start-at).total_seconds() < 1800 or done < start:
                excluded['invalid_time_or_inside_30_minutes'] += 1; continue
            side = f['model_side']
            if side not in ('over','under'):
                excluded['no_selected_side'] += 1; continue
            values=[f['p_over'],f['p_under'],f['p_push']]
            if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not 0 <= x <= 1 for x in values) or abs(sum(values)-1) > .0002:
                excluded['invalid_raw_probability'] += 1; continue
            dec = f['p_over']+f['p_under']
            if dec <= 0:
                excluded['no_decided_mass'] += 1; continue
            if s['status'] == 'void': status='void'
            elif s.get('push'): status='push'
            elif s.get('over_hit') in (0,1,False,True):
                status='win' if bool(s['over_hit']) == (side == 'over') else 'loss'
            else:
                excluded['missing_result'] += 1; continue
            quote, observed = adapters.lol_quote(f, f['forecast_at'])      # Underdog decimal prices saved with the row
            row=dict(forecast_id=str(f['id']),sport='lol',match_key=str(f['sid']),market=f['stat'],
                     player_id=str(f['pid']),scope=f['scope'],line=f['line'],book=f['platform'],side=side,
                     p=f['p_'+side]/dec,market_p=getattr(quote,'p_'+side) if quote else None,
                     market_at=observed if quote else None,market_source=quote.source if quote else None,
                     status=status,model_version=model_version,
                     published_p=f.get('cal_p_'+side),
                     artifact_version=artifact_version,probability_stage='raw_distribution_rounded_4dp',
                     forecast_at=f['forecast_at'],start_at=f['series_start'],settled_at=s['settled_at'])
            old=chosen.get(k)
            if old is None or (at,int(f['id'])) > (timestamp(old['forecast_at']),int(old['forecast_id'])): chosen[k]=row
        except (KeyError,TypeError,ValueError): excluded['malformed_record'] += 1
    return list(chosen.values()), dict(excluded)
