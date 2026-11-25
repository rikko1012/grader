"""Opt-in saved-filter Whop push, independent of an open browser (SCOUT_BACKGROUND_ALERTS + WHOP_API_KEY +
WHOP_NOTIFICATION_EXPERIENCE_ID, all set by the owner in Render; any missing = off).

https://docs.whop.com/api-reference/notifications/create-notification
Requires notification:create and an explicitly configured experience. The
separate private delivery database holds consented Whop destinations; ordinary
workspace receipts still contain hashed member identities only.

Phase 4 delivery rules (one server-side job, never a member request):
  - cooldown: at most one message per member per SCOUT_ALERT_COOLDOWN_S (default 900 s); a match found inside the cooldown is
    kept for the next tick, never lost and never sent twice;
  - per-member daily cap: SCOUT_ALERT_DAILY messages per rolling 24 h (default 12); over the cap the matches are marked seen and
    not sent (no flood after the cap lifts);
  - deep link: each message opens the prop it names (SCOUT_ALERT_DEEPLINK, default on in the running server);
  - unsubscribe: every message ends with a one-tap link (signed per member) that turns delivery off; the in-page toggle does too;
  - dry run: preview() computes exactly what the next tick would send, without writing or sending (admin page).
The page-only feature (matches shown while the research screen is open) is called "Live highlight"; it never claims delivery.
"""
import hashlib
import hmac
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
COOLDOWN_DEFAULT=900
DAILY_DEFAULT=12
UNSUB_PATH='/alerts/unsubscribe'


def configured():
    return (os.getenv('SCOUT_BACKGROUND_ALERTS')=='1' and bool(os.getenv('WHOP_API_KEY'))
            and os.getenv('WHOP_NOTIFICATION_EXPERIENCE_ID','').startswith('exp_'))


def _int_env(name,default,low,high):
    try:v=int(os.getenv(name) or default)
    except ValueError:v=default
    return max(low,min(v,high))


def cooldown_s():
    return _int_env('SCOUT_ALERT_COOLDOWN_S',COOLDOWN_DEFAULT,60,86400)


def daily_cap():
    return _int_env('SCOUT_ALERT_DAILY',DAILY_DEFAULT,1,200)


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
    db.execute('CREATE TABLE IF NOT EXISTS deliveries(member TEXT NOT NULL, at REAL NOT NULL)')
    db.commit()
    if os.name!='nt': path.chmod(0o600)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:db.close()


def _forget(db,member):
    for table in ('subscriptions','seen','baselines','deliveries'):db.execute(f'DELETE FROM {table} WHERE member=?',(member,))


def subscription(root,member,enabled=None,destination=None,sports=(),now=None):
    now=time.time() if now is None else now
    with connect(root) as db:
        if enabled is False:
            _forget(db,member)
        elif enabled is True:
            if not configured() or not destination or not destination.startswith('user_'):
                raise ValueError('Whop push is not configured for this signed-in account')
            db.execute('INSERT INTO subscriptions VALUES (?,?,?,?,?,0,?) ON CONFLICT(member) DO UPDATE SET '
                       'destination=excluded.destination,sports=excluded.sports,consent_at=excluded.consent_at,'
                       'expires_at=excluded.expires_at,status=excluded.status',
                       (member,destination,json.dumps(list(sports)),now,now+30*86400,'enabled'))
        r=db.execute('SELECT expires_at,last_sent,status FROM subscriptions WHERE member=?',(member,)).fetchone()
        sent=db.execute('SELECT count(*) FROM deliveries WHERE member=? AND at>?',(member,now-86400)).fetchone()[0]
        return {'configured':bool(configured()),'enabled':bool(r and r['expires_at']>now),
                'expires_at':r['expires_at'] if r else None,'last_sent':r['last_sent'] if r else None,
                'status':r['status'] if r else 'off','sent_24h':sent,'daily_cap':daily_cap(),'cooldown_s':cooldown_s()}


# ------------------------------------------------------------------------------------------------ unsubscribe link

def _secret():
    s=os.getenv('SCOUT_ALERT_SECRET') or ''
    if not s and os.getenv('WHOP_API_KEY'):
        s=hashlib.sha256(('scout-alert-unsubscribe|'+os.environ['WHOP_API_KEY']).encode()).hexdigest()
    return s.encode()


def unsub_token(member):
    key=_secret()
    return hmac.new(key,('unsub|'+member).encode(),hashlib.sha256).hexdigest()[:32] if key else None


def unsub_ok(member,token):
    good=unsub_token(member) if isinstance(member,str) and member else None
    return bool(good and isinstance(token,str) and hmac.compare_digest(good,token))


def unsub_url(member,base=None):
    t=unsub_token(member)
    if not t:return None
    if base is None:
        import whop_auth
        base=whop_auth.public_url()
    from urllib.parse import quote
    return f"{base.rstrip('/')}{UNSUB_PATH}?m={quote(member,safe='')}&t={t}"


# ------------------------------------------------------------------------------------------------------ matching

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


ALERTS_PATH='/?#/alerts'
DEEPLINK_FLAG='SCOUT_ALERT_DEEPLINK'        # owner flag: off = every alert links to the alerts list, as before


def deeplink_on():
    return os.environ.get(DEEPLINK_FLAG)=='1'


def prop_path(r):
    """The deep link from an alert to the prop it names: the hub opens that sport's board with the row expanded
    (web/upgrade-hub.js openPending). The key is the hub's own row key, sport|platform|offer_id, percent-encoded.
    Anything missing -> the alerts list, never a guessed prop."""
    from urllib.parse import quote
    try:
        sport,book,offer=str(r['sport']),str(r['offer']['platform']),str(r['offer']['offer_id'])
    except (KeyError,TypeError):return ALERTS_PATH
    if not (sport and book and offer):return ALERTS_PATH
    return '/?#scout/board?sport='+quote(sport,safe='')+'&open='+quote(sport+'|'+book+'|'+offer,safe='')


def deliver(destination,title,content,session=None,rest_path=ALERTS_PATH):
    import requests
    sender=session or requests
    path=rest_path if isinstance(rest_path,str) and rest_path.startswith('/?#') and len(rest_path)<=400 else ALERTS_PATH
    response=sender.post('https://api.whop.com/api/v1/notifications',timeout=10,allow_redirects=False,
        headers={'Authorization':'Bearer '+os.environ['WHOP_API_KEY']},json={
            'experience_id':os.environ['WHOP_NOTIFICATION_EXPERIENCE_ID'],'user_ids':[destination],
            'title':title[:100],'content':content[:500],'rest_path':path})
    # Never log the response or token. An uncertain result is not blindly resent.
    if response.status_code==200 and response.json().get('success') is True:return 'queued'
    return 'delivery_refused' if response.status_code in (401,403,404) else 'delivery_failed'


def message(member,label,r,more,base=None):
    """(title, content, rest_path) of one delivery. Content stays within Whop's 500 characters with the unsubscribe link kept whole."""
    text=(f"{label}: {r['player']['name']} · {r['offer']['line']} {r['market']['stat_label']} · {r['offer']['platform']}"
          +(f", plus {more} more matching line{'s' if more>1 else ''}" if more else '')+". Recheck the current line in Prop Grader.")
    stop=unsub_url(member,base)
    tail=f" Stop these alerts: {stop}" if stop else " Turn alerts off in Prop Grader > Alerts."
    content=text[:max(0,500-len(tail))]+tail
    return 'Prop Grader · '+str(label),content,(prop_path(r) if deeplink_on() else ALERTS_PATH)


def _scan(db,root,sub,state_for,rows,now,differences,write):
    """-> (notices, cap_hit). All matching for one subscriber; writes seen / baseline rows only when write is True."""
    m=sub['member']
    prefs=(state_for(m).get('prefs') or {}).get('researchHub') or {}
    allowed=set(json.loads(sub['sports']))
    available=[r for r in rows if r['sport'] in allowed]
    notices=[]
    cool=now-sub['last_sent']<cooldown_s()
    for rule in (prefs.get('alerts') or [])[:12]:
        if not isinstance(rule,dict) or not isinstance(rule.get('filters'),dict):continue
        # A changed rule establishes a fresh baseline, never a flood.
        rid=hashlib.sha256(json.dumps(rule,sort_keys=True).encode()).hexdigest()
        baseline=db.execute('SELECT 1 FROM baselines WHERE member=? AND rule=?',(m,rid)).fetchone()
        found=[r for r in available if match(r,rule['filters'],prefs.get('watch') or [],available,now,differences)]
        for r in found:
            rev=hashlib.sha256(json.dumps([r['sport'],r['offer']['platform'],r['offer']['offer_id'],r['offer']['revision']]).encode()).hexdigest()
            seen=db.execute('SELECT 1 FROM seen WHERE member=? AND rule=? AND revision=?',(m,rid,rev)).fetchone()
            if not seen and baseline and cool:
                # Leave the current revision eligible for the next tick.
                # Re-evaluating it then also drops withdrawn/started props.
                continue
            if not seen and baseline:
                notices.append((rule.get('name','Saved filter'),r))
            if write:db.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?,?)',(m,rid,rev,now))
        if write:db.execute('INSERT OR IGNORE INTO baselines VALUES(?,?)',(m,rid))
    sent=db.execute('SELECT count(*) FROM deliveries WHERE member=? AND at>?',(m,now-86400)).fetchone()[0]
    return notices,sent>=daily_cap()


def tick(root,state_for,rows,access,send=deliver,now=None,after=''):
    now=time.time() if now is None else now
    differences=_different_lines(rows)
    stats={'scanned':0,'queued':0,'capped':0,'cursor':''}
    with connect(root) as db:
        expired=[r[0] for r in db.execute('SELECT member FROM subscriptions WHERE expires_at<=?',(now,))]
        for m in expired:_forget(db,m)
        subs=db.execute('SELECT * FROM subscriptions WHERE member>? ORDER BY member LIMIT 100',(after,)).fetchall()
        db.commit()
        for sub in subs:
            m=sub['member']; stats['scanned']+=1; stats['cursor']=m
            if not access(sub['destination']):continue
            notices,capped=_scan(db,root,sub,state_for,rows,now,differences,True)
            db.commit()
            if notices and capped:
                stats['capped']+=1                   # marked seen above: nothing piles up behind the cap
                db.execute('UPDATE subscriptions SET status=? WHERE member=?',('daily_cap',m));db.commit()
                continue
            if notices:
                # Persist dedup before sending. Ambiguous network failures must
                # not repeat a message after restart.
                db.execute('UPDATE subscriptions SET last_sent=?,status=? WHERE member=?',(now,'sending',m))
                db.execute('INSERT INTO deliveries VALUES(?,?)',(m,now));db.commit()
                latest=db.execute('SELECT 1 FROM subscriptions WHERE member=?',(m,)).fetchone()
                if not latest:continue
                label,r=notices[0]
                title,text,path=message(m,label,r,len(notices)-1)   # every new match is marked seen, so the message names them all
                try:status=(send(sub['destination'],title,text,rest_path=path) if deeplink_on()
                            else send(sub['destination'],title,text))
                except Exception:status='delivery_unknown'
                db.execute('UPDATE subscriptions SET status=? WHERE member=?',(status,m))
                db.commit()
                stats['queued']+=status=='queued'
        db.execute('DELETE FROM seen WHERE at<?',(now-7*86400,))
        db.execute('DELETE FROM deliveries WHERE at<?',(now-2*86400,))
    return stats


def _mask(dest):
    d=str(dest or '')
    return d[:5]+'…'+d[-2:] if len(d)>8 else '…'


def preview(root,state_for,rows,access=lambda d:True,now=None,limit=100,base=None):
    """Dry run: what the next tick would send to each subscriber, computed read-only. Nothing is written, nothing is sent.
    Destinations are masked; members appear as their hashed ids only."""
    now=time.time() if now is None else now
    differences=_different_lines(rows)
    out={'at':now,'configured':bool(configured()),'deeplink':deeplink_on(),'cooldown_s':cooldown_s(),'daily_cap':daily_cap(),
         'subscribers':0,'payloads':[],'example':None}
    path=Path(root)/'workspace_alerts.db'
    if path.exists():
        db=sqlite3.connect(f'file:{path.as_posix()}?mode=ro',uri=True,timeout=3)
        db.row_factory=sqlite3.Row
        try:
            subs=db.execute('SELECT * FROM subscriptions WHERE expires_at>? ORDER BY member LIMIT ?',(now,limit)).fetchall()
            out['subscribers']=len(subs)
            for sub in subs:
                row={'member':sub['member'][:12]+'…','destination':_mask(sub['destination']),'status':sub['status']}
                if not access(sub['destination']):
                    out['payloads'].append(dict(row,would_send=False,why='Whop access check fails'));continue
                notices,capped=_scan(db,root,sub,state_for,rows,now,differences,False)
                if not notices:
                    why='cooldown' if now-sub['last_sent']<cooldown_s() else 'no new match since the last check'
                    out['payloads'].append(dict(row,would_send=False,why=why));continue
                if capped:
                    out['payloads'].append(dict(row,would_send=False,why='daily cap reached'));continue
                label,r=notices[0]
                title,text,rest=message(sub['member'],label,r,len(notices)-1,base)
                out['payloads'].append(dict(row,would_send=True,payload={'experience_id':'(WHOP_NOTIFICATION_EXPERIENCE_ID)',
                                       'user_ids':[_mask(sub['destination'])],'title':title[:100],'content':text[:500],'rest_path':rest}))
        finally:db.close()
    sample=next((r for r in rows if r.get('state',{}).get('kind') in ('fresh','ok','ready','live')),None)
    if sample:
        title,text,rest=message('wm-example',"Example filter",sample,2,base)
        out['example']={'experience_id':'(WHOP_NOTIFICATION_EXPERIENCE_ID)','user_ids':['user_…'],'title':title[:100],
                        'content':text[:500],'rest_path':rest,'note':'Built from the current board; the unsubscribe link here is for a dummy member.'}
    return out


def live_rows(srv):
    """The rows the job reads: every sport's shared snapshot, fresh feeds only."""
    import workspace_api as api
    records=[]
    for sport in api.SPORTS:
        if sport=='lol' and not srv.lol_public():continue
        if sport in ('nba','nfl') and os.getenv('SPORTS_UI','0') not in ('1','true','on'):continue
        snap=api.snapshot(srv) if sport=='cs2' else api._adapter(sport).snapshot(srv,*(() if sport=='lol' else (sport,)))
        if snap:
            doc=json.loads(snap['body'])
            feeds=doc.get('feeds') or {}
            records.extend(r for r in doc.get('records',[]) if (feeds.get(r['offer']['platform']) or {}).get('state')=='ok')
    return records


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
                    result=tick(api.data_dir(),api.store().get_state,live_rows(srv),whop_auth.has_access,after=cursor)
                    cursor=result['cursor'] if result['scanned']==100 else ''
                    _STATUS.update(state='running',checked_at=time.time(),**result)
                except Exception as exc:_STATUS.update(state='error',error=type(exc).__name__)
                time.sleep(60)
        _THREAD=threading.Thread(target=run,name='saved-filter-alerts',daemon=True);_THREAD.start()
