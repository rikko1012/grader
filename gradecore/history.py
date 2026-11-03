"""Convert immutable esports exports into the shared experiment contract.

Explicit model cohorts only. Never reconstruct an earlier forecast from today's
player stats or use a later grade as if it had been available before the game.
"""
import json
from collections import Counter
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
            side = {'More':'over','Less':'under','Over':'over','Under':'under','over':'over','under':'under'}.get(f['pick'])
            if not side or raw['over'] + raw['under'] <= 0:
                excluded['no_selected_decided_side'] += 1; continue
            if p['status'] == 'void': status = 'void'
            elif not isinstance(p.get('actual'),(int,float)):
                excluded['missing_actual'] += 1; continue
            elif p['actual'] == f['line']: status = 'push'
            else: status = 'win' if ((p['actual'] > f['line']) == (side == 'over')) else 'loss'
            key = tuple(p[k] for k in ('match_slug','slug','prop','scope','book')) + (f['line'],)
            row = dict(forecast_id=str(f['id']), sport='cs2', match_key=p['match_slug'], market=p['prop'],
                       player_id=p['slug'], scope=p['scope'], line=f['line'], book=p['book'], side=side,
                       p=raw[side]/(raw['over']+raw['under']), market_p=None, status=status,
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
    def key(r): return tuple(r[k] for k in ('platform','sid','pid','stat','scope','line'))
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
            row=dict(forecast_id=str(f['id']),sport='lol',match_key=str(f['sid']),market=f['stat'],
                     player_id=str(f['pid']),scope=f['scope'],line=f['line'],book=f['platform'],side=side,
                     p=f['p_'+side]/dec,market_p=None,status=status,model_version=model_version,
                     published_p=f.get('cal_p_'+side),
                     artifact_version=artifact_version,probability_stage='raw_distribution_rounded_4dp',
                     forecast_at=f['forecast_at'],start_at=f['series_start'],settled_at=s['settled_at'])
            old=chosen.get(k)
            if old is None or (at,int(f['id'])) > (timestamp(old['forecast_at']),int(old['forecast_id'])): chosen[k]=row
        except (KeyError,TypeError,ValueError): excluded['malformed_record'] += 1
    return list(chosen.values()), dict(excluded)
