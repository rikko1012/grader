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
    def send(*args,**kw):sent.append(args+((kw.get('rest_path'),) if kw else ()));return 'queued'
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
    def failed(*args,**kw):sent.append(args);raise TimeoutError()
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


def test_minimum_grade_uses_published_unheld_letter():
    r=row();r['scout']['grade']='A-'
    assert a.match(r,{'minGrade':'B+'},[],[r],1000)
    assert not a.match(r,{'minGrade':'A'},[],[r],1000)
    assert not a.match(r,{'minGrade':'invalid'},[],[r],1000)
    r['scout']['hold']='Accuracy review'
    assert not a.match(r,{'minGrade':'B'},[],[r],1000)
    assert a.match(r,{'minGrade':'all'},[],[r],1000)


def test_line_difference_index_keeps_event_period_and_book_boundaries():
    import copy
    base=row(); other=copy.deepcopy(base)
    other['offer'].update(platform='prizepicks',line=21.5)
    wrong=copy.deepcopy(other); wrong['event']['id']='different-event'
    period=copy.deepcopy(other); period['market']['scope']='1'
    same=copy.deepcopy(other); same['offer']['line']=20.5
    for candidates,expected in [([base],False),([base,wrong,period,same],False),([base,other],True)]:
        index=a._different_lines(candidates)
        assert a.match(base,{'onlyDiff':True},[],[],1000,index) is expected


# ---- SCOUT_BACKGROUND_ALERTS deep link: the alert opens the prop it names

def test_prop_path_is_the_hub_row_key_encoded_or_the_alerts_list():
    assert a.prop_path(row())=='/?#scout/board?sport=cs2&open=cs2%7Cunderdog%7Co'
    odd=row();odd['offer']['offer_id']='a&b=c#d e'
    assert a.prop_path(odd)=='/?#scout/board?sport=cs2&open=cs2%7Cunderdog%7Ca%26b%3Dc%23d%20e'
    for broken in ({}, {'sport':'cs2'}, dict(row(),offer={'platform':'underdog','offer_id':''})):
        assert a.prop_path(broken)==a.ALERTS_PATH=='/?#/alerts'


def test_flag_on_delivery_links_to_the_first_matching_prop(tmp_path,monkeypatch):
    state,sent,send=setup(tmp_path,monkeypatch)
    a.tick(tmp_path,lambda m:state,[row()],lambda u:True,send,now=1000)
    a.tick(tmp_path,lambda m:state,[row('2')],lambda u:True,send,now=2000)
    assert len(sent)==1 and sent[0][3]=='/?#scout/board?sport=cs2&open=cs2%7Cunderdog%7Co'


def test_deliver_sends_the_prop_link_and_refuses_foreign_paths(monkeypatch):
    monkeypatch.setenv('WHOP_API_KEY','private');monkeypatch.setenv('WHOP_NOTIFICATION_EXPERIENCE_ID','exp_test')
    seen=[]
    class Session:
        status_code=200
        def post(self,url,**kw):seen.append(kw['json']['rest_path']);return self
        def json(self):return {'success':True}
    assert a.deliver('user_x','t','c',Session(),rest_path=a.prop_path(row()))=='queued'
    assert a.deliver('user_x','t','c',Session())=='queued'
    for bad in ('https://evil.example/','//evil.example','/?#'+'x'*500,None):
        a.deliver('user_x','t','c',Session(),rest_path=bad)
    assert seen==[a.prop_path(row()),'/?#/alerts']+['/?#/alerts']*4


def test_flag_off_nothing_is_configured_sent_or_started(tmp_path,monkeypatch):
    monkeypatch.delenv('SCOUT_BACKGROUND_ALERTS',raising=False)
    monkeypatch.setenv('WHOP_API_KEY','test');monkeypatch.setenv('WHOP_NOTIFICATION_EXPERIENCE_ID','exp_test')
    assert not a.configured()
    import pytest
    with pytest.raises(ValueError):a.subscription(tmp_path,'member',True,'user_test',['cs2'],now=1000)
    a.start(None)                                   # returns before touching srv
    assert a._THREAD is None or not a._THREAD.is_alive()
