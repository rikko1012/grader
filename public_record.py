"""Public aggregate accuracy only. No picks, member identities or forecast inputs.

/api/record (SCOUT_PUBLIC_RECORD=1): totals, tiers and the 30-day calendar (the answer as it was before these additions).
SCOUT_RECORD_RELIABILITY=1 adds each tier's and the total's match-resampled 95% range, 5-point reliability bins with n
(CS2, from the same P1 picks the totals count) and the labelled counts (voids, match word). A failure in those ranges
leaves them out; the record itself still answers.
SCOUT_RECORD_STATUS=1 adds each sport's letters fact (live / held) to that answer.
SCOUT_PUBLIC_RECORD_SLIPS=1 adds the Props / Slips switch and /api/record/slips: anonymised counts of the slips members
tracked, read from workspace.db read-only. Pushes and voids are never counted as hits. Nothing here grades, settles or writes."""
import json
import math
import os
import sqlite3
from urllib.parse import quote

FLAG_SLIPS = 'SCOUT_PUBLIC_RECORD_SLIPS'
FLAG_STATUS = 'SCOUT_RECORD_STATUS'
FLAG_RELIABILITY = 'SCOUT_RECORD_RELIABILITY'
BASE_METRICS = ('wins','losses','pushes','picks','printed','observed','matches')
SPORTS = ('cs2', 'lol', 'nba', 'nfl')
LABEL = {'cs2': 'CS2', 'lol': 'LoL', 'nba': 'NBA', 'nfl': 'NFL'}
MATCH_WORDS = ('matches', 'series', 'games')
SLIP_ROWS = 20000                   # newest tracked slips read per pass: one bounded query every cache window
PLATFORMS = {'underdog': 'Underdog', 'prizepicks': 'PrizePicks'}


def flags():
    return {'slips': os.environ.get(FLAG_SLIPS) == '1', 'status': os.environ.get(FLAG_STATUS) == '1',
            'reliability': os.environ.get(FLAG_RELIABILITY) == '1'}


def _n(v):
    return v if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) else None


def _text(v,limit=800):
    return v[:limit] if isinstance(v,str) else ''


def _ci(v):
    if isinstance(v,dict):
        v=[v.get('lo95',v.get('lo')),v.get('hi95',v.get('hi'))]
    if isinstance(v,(list,tuple)) and len(v)==2 and all(_n(x) is not None and 0<=x<=1 for x in v) and v[0]<=v[1]:
        return [round(float(v[0]),4),round(float(v[1]),4)]
    return None


def sanitize(doc,sport,status=None,slips_tab=False,rich=False):
    """rich (SCOUT_RECORD_RELIABILITY): ranges, bins, voids and the match word. Off: exactly the keys the record had before."""
    def metrics(row):
        out={k:_n(row.get(k)) for k in BASE_METRICS}
        if rich:
            out['void']=_n(row.get('void'))
            out['ci']=_ci(row.get('ci') if row.get('ci') is not None else row.get('observed_ci'))
        return out
    totals=doc.get('totals') or {}
    result={'sport':sport,'as_of':_text(doc.get('as_of'),40),
            'population':_text(doc.get('population') or doc.get('rule')),
            'rule':_text(doc.get('rule')),'totals':metrics(totals),
            'bands':[], 'calendar':None}
    if rich:
        result['reliability']=[]
        result['totals']['match_word']=totals.get('match_word') if totals.get('match_word') in MATCH_WORDS else 'matches'
    for b in (doc.get('by_letter') if sport=='cs2' else (doc.get('groups') or {}).get('chance')) or []:
        if isinstance(b,dict):result['bands'].append(dict(metrics(b),name=_text(b.get('name'),40)))
    result['bands']=result['bands'][:30]
    for b in (doc.get('reliability') or [])[:20] if rich else ():
        if isinstance(b,dict) and isinstance(b.get('n'),int) and b['n']>0:
            result['reliability'].append({'lo':_n(b.get('lo')),'hi':_n(b.get('hi')),'n':b['n'],'stated':_n(b.get('stated')),
                                          'hit':_n(b.get('hit')),'ci':_ci(b.get('ci'))})
    calendar=doc.get('calendar') or {}
    if calendar.get('state')=='ready':
        result['calendar']={'basis':_text(calendar.get('basis'),80),'days':[
            {'date':_text(d.get('date'),10),**{k:_n(d.get(k)) for k in ('wins','losses','pushes','decided','hit_rate')}}
            for d in (calendar.get('days') or [])[-30:] if isinstance(d,dict)]}
    if status:
        result['status']={'letters':status['letters'],'text':_text(status.get('text'),200)}
    if slips_tab:
        result['slips_tab']=True
    return result


def _cs2(srv):
    """The member Results letters on the same P1 picks, plus each tier's and the total's match-resampled 95% range and
    the 5-point reliability bins (gradecore.scorecard: whole matches resampled, 2,000 draws, seed 7)."""
    import results_stated
    export=getattr(srv,'_p1_export_printed',srv._p1_export)()   # ec-1: the chance members were shown
    doc=results_stated.stated_observed(export)
    try:
        return _cs2_ranges(doc,export)
    except Exception:               # the ranges are extras: a failure there never takes the record down
        return doc


def _cs2_ranges(doc,export):
    import results_stated
    from gradecore import scorecard
    picks,_,_=results_stated.rule_picks(export)

    def ci(xs):
        if not xs:return None
        _,lo,hi=scorecard.cluster_ci([x['win'] for x in xs],[str(x['match']) for x in xs])
        return None if lo is None else [lo,hi]
    totals=dict(doc.get('totals') or {},ci=ci(picks))
    by_letter=[dict(b,ci=ci([x for x in picks if x['letter']==b.get('name')])) for b in doc.get('by_letter') or []]
    rel=scorecard.reliability([x['printed'] for x in picks],[x['win'] for x in picks],[str(x['match']) for x in picks]) if picks else []
    return dict(doc,totals=totals,by_letter=by_letter,reliability=rel)


def _sports(srv,sport):
    """The sports Results answer, with each chance band's 95% range read from the same published file."""
    import workspace_sports
    doc=workspace_sports.results(srv,sport)
    st,pub=workspace_sports._public(srv)
    raw={b.get('band'):b for b in (((pub or {}).get('leagues') or {}).get(sport) or {}).get('bands') or []} if st==200 else {}
    doc['groups']=dict(doc.get('groups') or {},chance=[dict(b,ci=(raw.get(b.get('name')) or {}).get('hit_rate_ci95')) for b in (doc.get('groups') or {}).get('chance') or []])
    return doc


def letter_status(srv,sport):
    """What a member sees today, stated as a fact, or None when it cannot be read (then nothing is shown).
    NBA / NFL: held when the sports file's containment snapshot (model_gate, sports/containment.py ct-1) or its withheld
    grades say so. Otherwise None: the board shows a letter only on rows carrying a published cf-2 grade
    (workspace_sports.board_row), so a league-wide "live" would claim more than the board shows.
    CS2 / LoL: the board's own containment state (letter_containment lc-1, the state every member surface reads): held
    with the banner's words, or live (LoL letters always carry 0 units)."""
    if sport in ('cs2','lol'):
        import letter_containment
        st=letter_containment.state(sport)
        b=letter_containment.banner(sport,st)
        if b:
            return {'letters':'held','text':b}
        # audit 2026-11-05: a plain fact about what the board shows, never a claim about the letters (they sit beside each tier's hit rate)
        return {'letters':'live','text':'The CS2 board shows letter grades.' if sport=='cs2' else 'The LoL board shows letter grades, at 0 units.'}
    import workspace_sports
    st,pub=workspace_sports._public(srv)
    d=((pub or {}).get('leagues') or {}).get(sport) if st==200 else None
    if not d:
        return None
    held=bool((d.get('model_gate') or {}).get('contained')) or bool((d.get('grades') or {}).get('withheld'))
    if held:
        return {'letters':'held','text':f'{LABEL[sport]} letters are held. {LABEL[sport]} figures are research estimates.'}
    return None


def read(srv,sport):
    f=flags()
    if sport=='cs2':
        if f['reliability']:
            doc=_cs2(srv)
        else:
            import results_stated
            doc=results_stated.stated_observed(getattr(srv,'_p1_export_printed',srv._p1_export)())
    elif sport=='lol':
        import workspace_lol
        doc=workspace_lol.results(srv)
    elif sport in ('nba','nfl'):
        import workspace_sports
        doc=workspace_sports.results(srv,sport)
        if f['reliability']:
            try:doc=_sports(srv,sport)
            except Exception:pass
    else:raise ValueError('unsupported sport')
    status=None
    if f['status']:
        try:status=letter_status(srv,sport)
        except Exception:status=None
    return sanitize(doc,sport,status=status,slips_tab=f['slips'],rich=f['reliability'])


# ---------------------------------------------------------------------------------------------------- slips (props vs slips)
SLIPS_SQL=("SELECT s.slip_id, s.platform, s.sports, s.legs, r.sport, "
           "(SELECT x.result FROM settlements x WHERE x.receipt_id = r.receipt_id ORDER BY x.revision DESC LIMIT 1) AS result "
           "FROM (SELECT slip_id, platform, sports, legs FROM slips WHERE scratch = 0 ORDER BY rowid DESC LIMIT ?) s "
           "JOIN receipts r ON r.slip_id = s.slip_id AND r.scratch = 0")


def slip_rows(path,limit=SLIP_ROWS):
    """One bounded read-only query. A missing file or a locked database raises: the page says the slip record is unavailable."""
    if not os.path.exists(path):
        raise FileNotFoundError('workspace.db')
    con=sqlite3.connect('file:'+quote(str(path).replace('\\','/'),safe='/:')+'?mode=ro',uri=True,timeout=2)
    try:
        con.execute('PRAGMA query_only=ON')
        return con.execute(SLIPS_SQL,(limit,)).fetchall()
    finally:
        con.close()


def slip_summary(rows):
    """rows: (slip_id, platform, sports json, legs, leg sport, latest result). -> {sport: counts}. A slip counts in each
    sport it holds. Settled = every leg has a final result (hit, miss, push, void). A push or void leg is never a hit:
    those slips are counted apart from 'every leg hit'."""
    slips={}
    for slip_id,platform,sports,legs,leg_sport,res in rows:
        s=slips.setdefault(slip_id,{'platform':platform,'legs':legs,'sports':set(),'results':[]})
        try:s['sports'].update(x for x in json.loads(sports or '[]') if x in SPORTS)
        except (TypeError,ValueError):pass
        if leg_sport in SPORTS:s['sports'].add(leg_sport)
        s['results'].append((leg_sport,res))
    out={}
    for sport in SPORTS:
        c={'tracked':0,'mixed':0,'pending':0,'settled':0,'all_hit':0,'missed':0,'push_or_void':0,
           'legs':{'hit':0,'miss':0,'push':0,'void':0,'pending':0},'by_platform':{},'by_size':{}}
        for s in slips.values():
            if sport not in s['sports']:
                continue
            c['tracked']+=1
            c['mixed']+=len(s['sports'])>1
            for leg_sport,res in s['results']:
                if leg_sport==sport:
                    c['legs'][res if res in ('hit','miss','push','void') else 'pending']+=1
            rs=[res for _,res in s['results']]
            if len(rs)<(s['legs'] or 0) or any(r not in ('hit','miss','push','void') for r in rs):
                c['pending']+=1
                continue
            c['settled']+=1
            kind='missed' if 'miss' in rs else 'all_hit' if all(r=='hit' for r in rs) else 'push_or_void'
            c[kind]+=1
            for key,val in (('by_platform',PLATFORMS.get(s['platform'],'Other')),('by_size',f"{len(rs)} picks")):
                g=c[key].setdefault(val,{'name':val,'settled':0,'all_hit':0,'missed':0,'push_or_void':0})
                g['settled']+=1;g[kind]+=1
        decided=c['all_hit']+c['missed']
        c['all_hit_rate']=round(c['all_hit']/decided,4) if decided else None
        for key in ('by_platform','by_size'):
            c[key]=[dict(g,all_hit_rate=round(g['all_hit']/(g['all_hit']+g['missed']),4) if g['all_hit']+g['missed'] else None)
                    for _,g in sorted(c[key].items())]
        out[sport]=c
    return out


def read_slips(path):
    return slip_summary(slip_rows(path))
