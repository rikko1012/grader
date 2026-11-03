import copy
import workspace_alerts as a


def row(rev='1',sport='cs2'):
    return dict(sport=sport,event={'id':'e','start_utc':'2099-01-01T00:00:00Z','label':'A v B'},
                offer={'platform':'underdog','offer_id':'o','revision':rev,'line':20.5},
                player={'key':'p','name':'Player'},market={'stat':'kills','stat_label':'Kills','scope':'12'},
                scout={'side':'over'},state={'kind':'fresh'})


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('SCOUT_BACKGROUND_ALERTS','1');monkeypatch.setenv('WHOP_API_KEY','test')
    monkeypatch.setenv('WHOP_NOTIFICATION_EXPERIENCE_ID','exp_test')
    a.subscription(tmp_path,'member',True,'user_test',['cs2'],now=1000)
    state={'prefs':{'researchHub':{'alerts':[{'name':'Kills','filters':{'sport':'cs2','book':'underdog'}}]}}}
    sent=[]
    def send(*args):sent.append(args);return 'queued'
    return state,sent,send


def test_background_baseline_change_dedup_restart_and_unsubscribe(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    def tick(rows,t):return a.tick(tmp_path,lambda m:state,rows,lambda user:True,send,now=t)
    tick([row()],1000);assert not sent
    tick([row('2')],2000);assert len(sent)==1
    tick([row('2')],4000);assert len(sent)==1
    tick([row('3')],4100);assert len(sent)==2
    a.subscription(tmp_path,'member',False,now=4200)
    tick([row('4')],6000);assert len(sent)==2
    with a.connect(tmp_path) as db:assert db.execute('SELECT count(*) FROM seen').fetchone()[0]==0


def test_denied_access_stale_rows_wrong_sport_never_notify(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    a.tick(tmp_path,lambda m:state,[row()],lambda u:True,send,now=1000)
    a.tick(tmp_path,lambda m:state,[row('2')],lambda u:False,send,now=2000)
    stale=row('3');stale['state']['kind']='stale'
    a.tick(tmp_path,lambda m:state,[stale,row('4','nba')],lambda u:True,send,now=3000)
    assert not sent


def test_filter_change_baselines_and_ambiguous_delivery_is_not_retried(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    a.tick(tmp_path,lambda m:state,[row()],lambda u:True,send,now=1000)
    state['prefs']['researchHub']['alerts'][0]['filters']['side']='over'
    a.tick(tmp_path,lambda m:state,[row('2')],lambda u:True,send,now=2000)
    assert not sent
    def failed(*args):sent.append(args);raise TimeoutError()
    a.tick(tmp_path,lambda m:state,[row('3')],lambda u:True,failed,now=3000)
    a.tick(tmp_path,lambda m:state,[row('3')],lambda u:True,send,now=5000)
    assert len(sent)==1
    assert a.subscription(tmp_path,'member',now=5000)['status']=='delivery_unknown'


def test_expired_consent_erases_destination(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    a.tick(tmp_path,lambda m:state,[row()],lambda u:True,send,now=1000+31*86400)
    with a.connect(tmp_path) as db:assert not db.execute('SELECT destination FROM subscriptions').fetchone()
    assert not sent


def test_delivery_is_targeted_and_no_redirects(monkeypatch):
    monkeypatch.setenv('WHOP_API_KEY','private');monkeypatch.setenv('WHOP_NOTIFICATION_EXPERIENCE_ID','exp_test')
    class Session:
        def post(self,url,**kw):
            assert url=='https://api.whop.com/api/v1/notifications'
            assert kw['json']['user_ids']==['user_only']
            assert kw['json']['experience_id']=='exp_test'
            assert kw['allow_redirects'] is False
            return self
        status_code=200
        def json(self):return {'success':True}
    assert a.deliver('user_only','title','content',Session())=='queued'


def test_cooldown_defers_current_revision_instead_of_losing_it(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    def tick(rows,t):return a.tick(tmp_path,lambda m:state,rows,lambda u:True,send,now=t)
    tick([row()],1000)
    tick([row('2')],2000)
    tick([row('3')],2100)
    assert len(sent)==1
    tick([row('3')],2900)
    assert len(sent)==2
    tick([row('3')],4000)
    assert len(sent)==2


def test_deferred_withdrawn_revision_is_not_sent(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    for rows,t in [([row()],1000),([row('2')],2000),([row('3')],2100),([],2900)]:
        a.tick(tmp_path,lambda m:state,rows,lambda u:True,send,now=t)
    assert len(sent)==1
