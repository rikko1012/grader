"""Public aggregate accuracy only. No picks, member identities or forecast inputs."""
import math


def _n(v):
    return v if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) else None


def _text(v,limit=800):
    return v[:limit] if isinstance(v,str) else ''


def sanitize(doc,sport):
    def metrics(row):
        return {k:_n(row.get(k)) for k in ('wins','losses','pushes','picks','printed','observed','matches')}
    result={'sport':sport,'as_of':_text(doc.get('as_of'),40),
            'population':_text(doc.get('population') or doc.get('rule')),
            'rule':_text(doc.get('rule')),'totals':metrics(doc.get('totals') or {}),
            'bands':[], 'calendar':None}
    for b in (doc.get('by_letter') if sport=='cs2' else (doc.get('groups') or {}).get('chance')) or []:
        if isinstance(b,dict):result['bands'].append(dict(metrics(b),name=_text(b.get('name'),40)))
    result['bands']=result['bands'][:30]
    calendar=doc.get('calendar') or {}
    if calendar.get('state')=='ready':
        result['calendar']={'basis':_text(calendar.get('basis'),80),'days':[
            {'date':_text(d.get('date'),10),**{k:_n(d.get(k)) for k in ('wins','losses','pushes','decided','hit_rate')}}
            for d in (calendar.get('days') or [])[-30:] if isinstance(d,dict)]}
    return result


def read(srv,sport):
    if sport=='cs2':
        import results_stated
        doc=results_stated.stated_observed(srv._p1_export())
    elif sport=='lol':
        import workspace_lol
        doc=workspace_lol.results(srv)
    elif sport in ('nba','nfl'):
        import workspace_sports
        doc=workspace_sports.results(srv,sport)
    else:raise ValueError('unsupported sport')
    return sanitize(doc,sport)
