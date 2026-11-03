"""Opt-in saved-filter Whop push, independent of an open browser.

https://docs.whop.com/api-reference/notifications/create-notification
Requires notification:create and an explicitly configured experience. The
separate private delivery database holds consented Whop destinations; ordinary
workspace receipts still contain hashed member identities only.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import contextmanager

_THREAD=None
_LOCK=threading.Lock()
_STATUS={'state':'not_started'}


def configured():
    return (os.getenv('SCOUT_BACKGROUND_ALERTS')=='1' and bool(os.getenv('WHOP_API_KEY'))
            and os.getenv('WHOP_NOTIFICATION_EXPERIENCE_ID','').startswith('exp_'))


@contextmanager
def connect(root):
    path=Path(root)/'workspace_alerts.db'
    db=sqlite3.connect(path,timeout=3)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS subscriptions(member TEXT PRIMARY KEY, destination TEXT NOT NULL, '
               'sports TEXT NOT NULL, consent_at REAL NOT NULL, expires_at REAL NOT NULL, last_sent REAL NOT NULL DEFAULT 0, status TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS seen(member TEXT NOT NULL, rule TEXT NOT NULL, revision TEXT NOT NULL, '
               'at REAL NOT NULL, PRIMARY KEY(member,rule,revision))')
    db.execute('CREATE TABLE IF NOT EXISTS baselines(member TEXT NOT NULL, rule TEXT NOT NULL, PRIMARY KEY(member,rule))')
    db.commit()
    if os.name!='nt': path.chmod(0o600)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:db.close()


def subscription(root,member,enabled=None,destination=None,sports=(),now=None):
    now=time.time() if now is None else now
    with connect(root) as db:
        if enabled is False:
            for table in ('subscriptions','seen','baselines'):db.execute(f'DELETE FROM {table} WHERE member=?',(member,))
        elif enabled is True:
            if not configured() or not destination or not destination.startswith('user_'):
                raise ValueError('Whop push is not configured for this signed-in account')
            db.execute('INSERT INTO subscriptions VALUES (?,?,?,?,?,0,?) ON CONFLICT(member) DO UPDATE SET '
                       'destination=excluded.destination,sports=excluded.sports,consent_at=excluded.consent_at,'
                       'expires_at=excluded.expires_at,status=excluded.status',
                       (member,destination,json.dumps(list(sports)),now,now+30*86400,'enabled'))
        r=db.execute('SELECT expires_at,last_sent,status FROM subscriptions WHERE member=?',(member,)).fetchone()
        return {'configured':bool(configured()),'enabled':bool(r and r['expires_at']>now),
                'expires_at':r['expires_at'] if r else None,'last_sent':r['last_sent'] if r else None,
                'status':r['status'] if r else 'off'}


def _market_key(r):
    return (r['sport'],r['event']['id'],r['player']['key'],json.dumps(r['market'],sort_keys=True))


def _different_lines(rows):
    """Index once per worker tick, rather than scan the board for every member/row."""
    groups={}
    for r in rows:
        try: groups.setdefault(_market_key(r),{}).setdefault(r['offer']['platform'],set()).add(r['offer']['line'])
        except (KeyError,TypeError,ValueError): continue
    out=set()
    for key,books in groups.items():
        for book,lines in books.items():
            other=set().union(*(v for b,v in books.items() if b!=book))
            for line in lines:
                if other-{line}: out.add((key,book,line))
    return out


def match(r,f,watch,rows,now,differences=None):
    try:
        from datetime import datetime
        start=datetime.fromisoformat(r['event']['start_utc'].replace('Z','+00:00')).timestamp()
        if start<=now or r['state']['kind'] not in ('fresh','ok','ready','live'):return False
        minimum=f.get('minGrade','all')
        if minimum!='all':
            order=('B-','B','B+','A-','A','A+')
            grade=r['scout'].get('grade')
            if minimum not in order or grade not in order or r['scout'].get('hold') or order.index(grade)<order.index(minimum):return False
        for k,v in (('sport',r['sport']),('book',r['offer']['platform']),('side',r['scout']['side']),
                    ('stat',r['sport']+'|'+r['market']['stat']),('game',r['sport']+'|'+r['event']['id'])):
            if f.get(k,'all') not in ('all',v):return False
        if f.get('soonOnly') and start-now>3600:return False
        if f.get('movedOnly') and r['offer'].get('moved_from') is None:return False
        if f.get('watched') and r['sport']+'|'+r['player']['key'] not in watch:return False
        if f.get('onlyDiff'):
            differences=_different_lines(rows) if differences is None else differences
            if (_market_key(r),r['offer']['platform'],r['offer']['line']) not in differences:return False
        text=' '.join(str(x or '') for x in (r['player']['name'],r['player'].get('team'),r['event'].get('label'),r['market']['stat_label']))
        return str(f.get('q') or '').lower() in text.lower()
    except (KeyError,TypeError,ValueError):return False


def deliver(destination,title,content,session=None):
    import requests
    sender=session or requests
    response=sender.post('https://api.whop.com/api/v1/notifications',timeout=10,allow_redirects=False,
        headers={'Authorization':'Bearer '+os.environ['WHOP_API_KEY']},json={
            'experience_id':os.environ['WHOP_NOTIFICATION_EXPERIENCE_ID'],'user_ids':[destination],
            'title':title[:100],'content':content[:500],'rest_path':'/?#/alerts'})
    # Never log the response or token. An uncertain result is not blindly resent.
    if response.status_code==200 and response.json().get('success') is True:return 'queued'
    return 'delivery_refused' if response.status_code in (401,403,404) else 'delivery_failed'


def tick(root,state_for,rows,access,send=deliver,now=None,after=''):
    now=time.time() if now is None else now
    differences=_different_lines(rows)
    stats={'scanned':0,'queued':0,'cursor':''}
    with connect(root) as db:
        expired=[r[0] for r in db.execute('SELECT member FROM subscriptions WHERE expires_at<=?',(now,))]
        for m in expired:
            for table in ('subscriptions','seen','baselines'):db.execute(f'DELETE FROM {table} WHERE member=?',(m,))
        subs=db.execute('SELECT * FROM subscriptions WHERE member>? ORDER BY member LIMIT 100',(after,)).fetchall()
        db.commit()
        for sub in subs:
            m=sub['member']; stats['scanned']+=1; stats['cursor']=m
            if not access(sub['destination']):continue
            prefs=(state_for(m).get('prefs') or {}).get('researchHub') or {}
            allowed=set(json.loads(sub['sports']))
            available=[r for r in rows if r['sport'] in allowed]
            notices=[]
            for rule in (prefs.get('alerts') or [])[:12]:
                if not isinstance(rule,dict) or not isinstance(rule.get('filters'),dict):continue
                # A changed rule establishes a fresh baseline, never a flood.
                rid=hashlib.sha256(json.dumps(rule,sort_keys=True).encode()).hexdigest()
                baseline=db.execute('SELECT 1 FROM baselines WHERE member=? AND rule=?',(m,rid)).fetchone()
                found=[r for r in available if match(r,rule['filters'],prefs.get('watch') or [],available,now,differences)]
                for r in found:
                    rev=hashlib.sha256(json.dumps([r['sport'],r['offer']['platform'],r['offer']['offer_id'],r['offer']['revision']]).encode()).hexdigest()
                    seen=db.execute('SELECT 1 FROM seen WHERE member=? AND rule=? AND revision=?',(m,rid,rev)).fetchone()
                    if not seen and baseline and now-sub['last_sent']<900:
                        # Leave the current revision eligible for the next tick.
                        # Re-evaluating it then also drops withdrawn/started props.
                        continue
                    if not seen and baseline:
                        notices.append((rule.get('name','Saved filter'),r))
                    db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?,?)',(m,rid,rev,now))
                db.execute('INSERT OR IGNORE INTO baselines VALUES(?,?)',(m,rid))
            db.commit()
            if notices:
                # Persist dedup before sending. Ambiguous network failures must
                # not repeat a message after restart.
                db.execute('UPDATE subscriptions SET last_sent=?,status=? WHERE member=?',(now,'sending',m));db.commit()
                latest=db.execute('SELECT 1 FROM subscriptions WHERE member=?',(m,)).fetchone()
                if not latest:continue
                label,r=notices[0]
                text=f"{label}: {r['player']['name']} · {r['offer']['line']} {r['market']['stat_label']} · {r['offer']['platform']}. Recheck the current line in Scout."
                try:status=send(sub['destination'],'Scout · '+str(label),text)
                except Exception:status='delivery_unknown'
                db.execute('UPDATE subscriptions SET status=? WHERE member=?',(status,m))
                db.commit()
                stats['queued']+=status=='queued'
        db.execute('DELETE FROM seen WHERE at<?',(now-7*86400,))
    return stats


def start(srv):
    global _THREAD
    if not configured():return
    with _LOCK:
        if _THREAD and _THREAD.is_alive():return
        def run():
            import workspace_api as api
            import whop_auth
            cursor=''
            while configured():
                try:
                    records=[]
                    for sport in api.SPORTS:
                        if sport=='lol' and not srv.lol_public():continue
                        if sport in ('nba','nfl') and os.getenv('SPORTS_UI','0') not in ('1','true','on'):continue
                        snap=api.snapshot(srv) if sport=='cs2' else api._adapter(sport).snapshot(srv,*(() if sport=='lol' else (sport,)))
                        if snap:
                            doc=json.loads(snap['body'])
                            feeds=doc.get('feeds') or {}
                            records.extend(r for r in doc.get('records',[]) if (feeds.get(r['offer']['platform']) or {}).get('state')=='ok')
                    result=tick(api.data_dir(),api.store().get_state,records,whop_auth.has_access,after=cursor)
                    cursor=result['cursor'] if result['scanned']==100 else ''
                    _STATUS.update(state='running',checked_at=time.time(),**result)
                except Exception as exc:_STATUS.update(state='error',error=type(exc).__name__)
                time.sleep(60)
        _THREAD=threading.Thread(target=run,name='saved-filter-alerts',daemon=True);_THREAD.start()
