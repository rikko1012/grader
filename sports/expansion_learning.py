"""Isolated prospective soccer/tennis projection experiment, never live grades.

Freeze a 20-game mean baseline and a residual-adjusted challenger before start.
Review each saved forecast against final exact-stat/period observations. Every
correction appends a new review. Future challengers use prior reviews; published
forecasts, probabilities, stakes and model artifacts are never changed here.

This is a diagnostic baseline, not a fully specified sport model. No automatic
promotion: independent chronological and prospective evidence remains required.
"""
from contextlib import closing
import hashlib
import json
import math
import os
import sqlite3
import time

from gradecore.projection_review import review
from . import expansion_board as boards, store
from .contracts import now_utc, parse_utc
from .expansion import COMPETITIONS, isolated_database
from .expansion_history import resolve, atomic_json

VERSION = 'expansion-history-mean20-residual-v1'
MIN_HISTORY = 5
MAX_BYTES = 100 * 1024 * 1024


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def connect(sport):
    if sport not in boards.SPORTS:
        raise ValueError('unsupported learning sport')
    directory = boards.root(sport)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'learning.db'
    c = sqlite3.connect(path, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('CREATE TABLE IF NOT EXISTS owner(sport TEXT PRIMARY KEY)')
    owners = [r[0] for r in c.execute('SELECT sport FROM owner')]
    if owners and owners != [sport]:
        c.close()
        raise ValueError('learning database belongs to another sport')
    c.execute('INSERT OR IGNORE INTO owner VALUES (?)', (sport,))
    c.execute('''CREATE TABLE IF NOT EXISTS predictions (
        id TEXT PRIMARY KEY, competition TEXT, event_id TEXT, player_id TEXT,
        stat TEXT, scope TEXT, start_at TEXT, made_at TEXT, payload TEXT)''')
    c.execute('CREATE INDEX IF NOT EXISTS prediction_event ON predictions(competition,event_id)')
    c.execute('CREATE INDEX IF NOT EXISTS prediction_cohort ON predictions(competition,stat,scope,start_at)')
    c.execute('''CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY, prediction_id TEXT, result_id TEXT, observed_at TEXT,
        payload TEXT, UNIQUE(prediction_id,result_id))''')
    c.execute('CREATE INDEX IF NOT EXISTS review_prediction ON reviews(prediction_id,id DESC)')
    c.execute('CREATE TABLE IF NOT EXISTS progress(event_key TEXT PRIMARY KEY, signature TEXT, checked_at TEXT)')
    c.commit()
    return c


def history(c, identity, stat, scope, before):
    rows = store._latest_rows(c, 'stat', 'player_id=? AND stat=?',
                             [identity['player_id'], stat], before, 2000)
    events = {}
    for row in rows:
        obs = store._rec(row)
        if obs.scope != scope or obs.event_id == identity['event_id']:
            continue
        event = store._any_event(c, obs.event_id, before)
        if not event or not event.start_utc or event.start_utc >= before:
            continue
        value = {'record_id': row['record_id'], 'event_id': obs.event_id, 'start_at': event.start_utc,
                 'value': obs.value, 'known_at': obs.known_at, 'source': obs.source,
                 'valid': obs.final and obs.participation == 'played' and
                          obs.value is not None and math.isfinite(obs.value)}
        events.setdefault(obs.event_id, []).append(value)
    # Two sources are not two games. Conflicting source values need reconciliation.
    good = [sorted(rs, key=lambda r:r['record_id'])[0] for rs in events.values()
            if all(r['valid'] for r in rs) and len({r['value'] for r in rs}) == 1]
    return sorted(good, key=lambda r:(r['start_at'],r['event_id']), reverse=True)[:20]


def residual_fit(c, identity, stat, scope, before, baseline):
    """One original pre-match forecast per player/event; no book/revision inflation.

    Last 200 reviewed player-events in the same competition, stat and period.
    Fixed 20-observation shrinkage and 20% correction cap are experimental rules,
    not claimed optimal settings. No held-out result is used in its own forecast.
    """
    rows = c.execute('''SELECT p.id,p.event_id,p.player_id,p.made_at,p.start_at,p.payload,
        r.id AS review_id,r.observed_at,r.payload AS result FROM predictions p JOIN reviews r
        ON r.id=(SELECT r2.id FROM reviews r2 WHERE r2.prediction_id=p.id AND r2.observed_at<=?
                 ORDER BY r2.id DESC LIMIT 1)
        WHERE p.competition=? AND p.stat=? AND p.scope=? AND p.start_at<? AND p.made_at<?
        ORDER BY p.start_at DESC,p.event_id,p.player_id,p.made_at,p.id LIMIT 4000''',
        (before,identity['competition'],stat,scope,before,before))
    unique = {}
    for row in rows:
        if row['event_id'] == identity['event_id']:
            continue
        key = (row['event_id'],row['player_id'])
        pred, result = json.loads(row['payload']), json.loads(row['result'])
        if pred['model_version'] != VERSION or result.get('actual') is None:
            continue
        if key not in unique:
            unique[key] = (result['actual']-pred['baseline'],row['review_id'],row['observed_at'])
    selected = list(unique.values())[:200]
    shift = sum(x[0] for x in selected) / (len(selected)+20)
    shift = max(-baseline*.2,min(baseline*.2,shift))
    return {'shift':shift,'player_events':len(selected),
            'review_ids':[x[1] for x in selected], 'known_at':max((x[2] for x in selected),default=None)}


def storage(sport):
    """learning.db live pages + WAL against MAX_BYTES (sg-stability: slimmed rows free pages that new rows reuse)."""
    from . import expansion_archive as xa
    live = xa.live_bytes(boards.root(sport)/'learning.db')
    return {'live_mb': round(live/1048576, 1), 'cap_mb': MAX_BYTES//1048576, 'full': live >= MAX_BYTES}


SLIM_AFTER_DAYS = 7
SLIM_KEEP_BYTES = 1024
SLIM_REQUIRED = ('model_version', 'baseline', 'candidate', 'line_for_review', 'identity', 'projection_scope',
                 'made_at', 'source_event_start', 'shadow_only', 'release_allowed')


def compact(sport, now=None, budget_s=4, days=SLIM_AFTER_DAYS):
    """Slim reviewed predictions of events that started more than `days` ago: the full row goes to a verified archive
    file (<sport>/archive/learning/), the store keeps every key residual_fit / settle_reviews read (model_version,
    baseline, candidate, line_for_review) and every small key. Lossless; the shadow's numbers are unchanged."""
    from datetime import timedelta
    from . import expansion_archive as xa
    now = now or now_utc()
    t0 = time.monotonic()
    cutoff = (parse_utc(now) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    adir = boards.root(sport)/'archive'/'learning'
    cols = ('id', 'competition', 'event_id', 'player_id', 'stat', 'scope', 'start_at', 'made_at', 'payload')
    out = {'rows': 0, 'parts': 0}
    with closing(connect(sport)) as c:
        xa.ensure(c)
        c.commit()
        out['leftovers_removed'] = xa.remove_leftovers(c, adir, 'learning')
        while time.monotonic() - t0 < budget_s:
            rows = c.execute('''SELECT id,competition,event_id,player_id,stat,scope,start_at,made_at,payload
                FROM predictions p WHERE start_at<? AND json_extract(payload,'$.archived') IS NULL
                AND EXISTS (SELECT 1 FROM reviews r WHERE r.prediction_id=p.id) ORDER BY start_at,id LIMIT 2000''',
                             (cutoff,)).fetchall()
            if not rows:
                break
            rows = [tuple(r) for r in rows]
            name = xa.next_name(c, adir, 'learning')

            def slim(payload):
                kept = {k: v for k, v in payload.items() if k in SLIM_REQUIRED or len(encode(v)) <= SLIM_KEEP_BYTES}
                kept['archived'] = name
                return kept
            res = xa.archive_part(c, adir, 'learning', 'predictions', cols, rows,
                                  'UPDATE predictions SET payload=? WHERE id=?',
                                  [(encode(slim(json.loads(r[8]))), r[0]) for r in rows], key_col='start_at')
            out['rows'] += res['rows']
            out['parts'] += 1
        out['archived'] = xa.stats(c)
    out['seconds'] = round(time.monotonic()-t0, 2)
    return out


def capture(c, sport, now, deadline):
    directory = boards.root(sport)
    if storage(sport)['full']:
        return {'saved': 0, 'reason': 'storage_full'}
    paths = [directory/'board.json',directory/'current-matches.json']
    if any(not p.exists() or p.stat().st_size>3_000_000 for p in paths):
        return {'saved':0,'reason':'board_or_identity_unavailable'}
    board, matches = [json.loads(p.read_text(encoding='utf-8')) for p in paths]
    counts = {'saved':0,'unlinked':0,'insufficient_history':0,'expired':0}
    seen = set()
    # Earliest starts first. Explicit deadline bounds work independently of slate size.
    for offer in sorted(board.get('offers',[]),key=lambda r:r['start']):
        if time.monotonic() >= deadline:
            counts['partial'] = True
            break
        age = (parse_utc(now)-parse_utc(offer['observed_at'])).total_seconds()
        if offer['start']<=now or not 0<=age<=300:
            counts['expired'] += 1
            continue
        identity = resolve(offer,matches)
        if not identity:
            counts['unlinked'] += 1
            continue
        # Research regulation totals for soccer, but never compare them with a
        # full-game book line until its extra-time/participation rules are verified.
        scope = offer['scope']
        line_for_review = offer['line']
        if sport == 'soccer' and scope == 'full_game':
            scope, line_for_review = 'regulation', None
        key = digest([VERSION,identity,offer['stat'],scope,offer['platform'],offer['offer_id'],offer['revision']])
        if key in seen or c.execute('SELECT 1 FROM predictions WHERE id=?',(key,)).fetchone():
            continue
        seen.add(key)
        path = isolated_database(directory.parent,identity['competition'])
        if not path.exists():
            counts['insufficient_history'] += 1
            continue
        with closing(store.connect(path,read_only=True)) as source:
            event = store._any_event(source,identity['event_id'],now)
            if not event or event.status!='scheduled' or not event.start_utc or event.start_utc<=now:
                counts['event_not_pregame'] = counts.get('event_not_pregame',0)+1
                continue
            games = history(source,identity,offer['stat'],scope,now)
            if len(games)<MIN_HISTORY:
                counts['insufficient_history'] += 1
                continue
            facts = [dict(r) for r in store._latest_rows(source,'context','event_id=?',
                     [identity['event_id']],now,32)]
        baseline = sum(g['value'] for g in games)/len(games)
        fit = residual_fit(c,identity,offer['stat'],scope,now,baseline)
        payload = {'model_version':VERSION,'shadow_only':True,'identity':identity,'offer':offer,
                   'history':games,'context_records':[{'record_id':r['record_id'], 'known_at':r['known_at'],
                       'payload':json.loads(r['payload'])} for r in facts],
                   'baseline':baseline,'candidate':max(0,baseline+fit['shift']),'fit':fit,
                   'projection_scope':scope,'line_for_review':line_for_review,
                   'source_event_start':event.start_utc,'source_event_known_at':event.known_at,
                   'book_scope_verified':line_for_review is not None,
                   'inputs_used':['same_stat_period_history','prior_baseline_residuals'],
                   'context_used_in_model':False,'made_at':now,'release_allowed':False}
        c.execute('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?)',
                  (key,identity['competition'],identity['event_id'],identity['player_id'],offer['stat'],
                   scope,min(offer['start'],event.start_utc),now,encode(payload)))
        counts['saved'] += 1
    c.commit()
    return counts


def settle_reviews(c,sport,now,deadline):
    counts = {'added':0,'events_checked':0}
    # Least recently checked first prevents a missing result starving later games.
    events = c.execute('''SELECT DISTINCT p.competition,p.event_id,coalesce(g.checked_at,'') AS last_check
        FROM predictions p LEFT JOIN progress g ON g.event_key=p.competition||'|'||p.event_id
        WHERE p.start_at<? ORDER BY last_check,p.event_id LIMIT 100''',(now,)).fetchall()
    for item in events:
        if time.monotonic()>=deadline:
            counts['partial']=True
            break
        comp,eid=item['competition'],item['event_id']
        path=isolated_database(boards.root(sport).parent,comp)
        if not path.exists():continue
        with closing(store.connect(path,read_only=True)) as source:
            observations=store._latest_rows(source,'stat','event_id=?',[eid],now,2000)
        signature=digest([r['record_id'] for r in observations])
        eventkey=comp+'|'+eid
        previous=c.execute('SELECT signature FROM progress WHERE event_key=?',(eventkey,)).fetchone()
        if not previous or previous[0]!=signature:
            by_key={}
            for r in observations:
                obs=store._rec(r)
                by_key.setdefault((obs.participant_id,obs.stat,obs.scope),[]).append((r,obs))
            for pred in c.execute('SELECT * FROM predictions WHERE competition=? AND event_id=?',(comp,eid)):
                candidates=by_key.get((pred['player_id'],pred['stat'],pred['scope']),[])
                if not candidates:continue
                result_id=digest(sorted(r['record_id'] for r,_ in candidates))
                if c.execute('SELECT 1 FROM reviews WHERE prediction_id=? AND result_id=?',
                             (pred['id'],result_id)).fetchone():continue
                valid=all(o.final and o.participation=='played' and o.value is not None for _,o in candidates)
                values={o.value for _,o in candidates}
                actual=next(iter(values)) if valid and len(values)==1 else None
                payload=json.loads(pred['payload'])
                report={'actual':actual,'result_records':[r['record_id'] for r,_ in candidates],
                        'known_at':max(o.known_at for _,o in candidates),'reviewed_at':now,
                        'reason':None if actual is not None else 'unresolved_or_nonparticipation',
                        'settlement_status':None,'release_allowed':False}
                if actual is not None:
                    report['baseline']=review(payload['baseline'],actual,payload['line_for_review'])
                    report['candidate']=review(payload['candidate'],actual,payload['line_for_review'])
                c.execute('INSERT INTO reviews(prediction_id,result_id,observed_at,payload) VALUES (?,?,?,?)',
                          (pred['id'],result_id,now,encode(report)))
                counts['added']+=1
        c.execute('INSERT OR REPLACE INTO progress VALUES (?,?,?)',(eventkey,signature,now))
        c.commit()
        counts['events_checked']+=1
    return counts


def _step(fn, *a, **k):
    """sg-stability: one step's failure (a full store, a locked file) is recorded and never stops the next step -
    above all never the publication of the grades."""
    try:
        return fn(*a, **k)
    except (sqlite3.Error, OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
        return {'error': f'{type(e).__name__}: {str(e)[:200]}'}


def cycle(sport,now=None,budget_s=20):
    now=now or now_utc();deadline=time.monotonic()+budget_s
    from . import expansion_forecasts as fc
    published=(_step(fc.publish, sport, now, budget_s=budget_s/3) if os.environ.get('SPORTS_FORECAST')=='1'
               else {'state':'forecast_disabled'})
    settled=_step(fc.settle, sport, now, budget_s=budget_s/6)
    upkeep={'forecasts': _step(fc.compact, sport, now, budget_s=budget_s/10),
            'learning': _step(compact, sport, now, budget_s=budget_s/10)}

    def learning_steps():
        with closing(connect(sport)) as c:
            reviewed=_step(settle_reviews,c,sport,now,min(deadline,time.monotonic()+budget_s/4))
            captured=_step(capture,c,sport,now,deadline)
            totals={name:c.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in ('predictions','reviews')}
        return reviewed, captured, totals
    steps=_step(learning_steps)
    reviewed, captured, totals = steps if isinstance(steps, tuple) else (steps, steps, {})
    result={'sport':sport,'at':now,'reviews':reviewed,'forecasts':captured,'totals':totals,
            'mode':'shadow','release_allowed':False, 'published_model':published, 'published_reviews':settled,
            'upkeep':upkeep,'storage':{'learning':_step(storage,sport),'forecasts':_step(fc.storage,sport)}}
    atomic_json(boards.root(sport)/'learning-status.json',result)
    return result


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('sport',choices=boards.SPORTS)
    args=p.parse_args()
    if not boards.enabled(args.sport):raise SystemExit('sport disabled')
    from .expansion_compact import bound_cache
    bound_cache()                   # drain-1119: archived results are read through a smaller file cache
    print(encode(cycle(args.sport)))


if __name__=='__main__':main()
