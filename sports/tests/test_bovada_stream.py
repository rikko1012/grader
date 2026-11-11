import json
from copy import deepcopy
import pytest
import websocket
from sports.books import bovada, bovada_stream as stream
from sports.tests.test_bovada import coupon, CLOCK


def frame(kind, body=None, **kw):
    head = dict(type=kind, target='prematch', **kw)
    return json.dumps(head) + ('|' + json.dumps(body) if body is not None else '')


class Socket:
    def __init__(self, frames): self.frames=iter(frames); self.closed=False; self.sent=[]
    def send(self, s): self.sent.append(s)
    def settimeout(self, t): assert 0 < t <= 8
    def recv(self): return next(self.frames, '')
    def close(self, **kw): self.closed=True


def install(monkeypatch, frames):
    sock=Socket(frames)
    monkeypatch.setattr(websocket, 'create_connection', lambda *a, **k: sock)
    monkeypatch.setattr(stream.net, '_wait', lambda *a: None)
    return sock


def test_listing_requires_complete_catchup(monkeypatch):
    b={'path':[{'id':'2958468'}], 'events':[]}
    s=install(monkeypatch,[frame('chgEnablement', {'enabled':False}), frame('competition_catchup_start'),frame('competition',b),frame('competition_catchup_end')])
    assert stream.snapshot('nba') == [b]
    assert s.closed and 'preMatchOnly=true' in s.sent[0]


@pytest.mark.parametrize('frames', [
    [frame('competition_catchup_end')],
    [frame('competition_catchup_start')],
    [frame('competition_catchup_start'),frame('competition',{'path':[{'id':'241'}],'events':[]})],
    [frame('competition_catchup_start'),frame('competition',{'path':[{'id':'2958468'}],'events':[]})],
    ['not json'],
])
def test_incomplete_or_wrong_snapshot_fails_closed(monkeypatch, frames):
    s=install(monkeypatch,frames)
    with pytest.raises(ValueError): stream.snapshot('nba')
    assert s.closed


@pytest.mark.parametrize('kind,body',[('chg',{}),('chgEnablement',{'enabled':True})])
def test_access_challenge_is_not_solved_or_retried(monkeypatch,kind,body):
    s=install(monkeypatch,[frame(kind,body)])
    with pytest.raises(stream.net.HttpError) as err: stream.snapshot('nba')
    assert err.value.status==403 and s.closed and len(s.sent)==1


def test_main_and_alternate_frames_preserved(monkeypatch):
    e=coupon()[0]['events'][0]; path=e['link']; alt=deepcopy(e)
    alt['displayGroups'][0]['markets'][0]['id']='alt'
    s=install(monkeypatch,[frame('event_catchup_start'),frame('fullEvent',e),frame('fullEvent',alt),frame('event_catchup_end',path=path.replace('/','$'))])
    assert stream.snapshot('nba',path)==[{'events':[e]},{'events':[alt]}]
    assert s.closed


def test_byte_limit(monkeypatch):
    monkeypatch.setattr(stream,'MAX_BYTES',10)
    s=install(monkeypatch,[frame('competition_catchup_start')])
    with pytest.raises(ValueError): stream.snapshot('nba')
    assert s.closed


@pytest.mark.parametrize('league',['nba','nfl'])
def test_empty_legacy_listing_uses_all_stream_events(monkeypatch,league):
    bodies=[coupon(str(i),league) for i in range(16)]
    for b in bodies: b[0]['events'][0]['link']=bovada.PATHS[league]+'/a-b-'+b[0]['events'][0]['id']
    calls=[]
    monkeypatch.setattr(bovada.net,'get_json',lambda *a,**k:{})
    def snap(lg,path=None):
        calls.append(path)
        return [{'events':[b[0]['events'][0] for b in bodies]}] if path is None else bodies[int(path.rsplit('-',1)[1])]
    monkeypatch.setattr(stream,'snapshot',snap)
    r=bovada.Bovada().read(None,{'key':league},CLOCK)
    assert not r.error and len(r.events)==16 and len(r.offers)==32
    assert len(calls)==17 and r.requests==18


def test_partial_stream_failure_does_not_publish_partial_event(monkeypatch):
    body=coupon('1','nfl')
    monkeypatch.setattr(bovada.net,'get_json',lambda *a,**k:{})
    def snap(lg,path=None):
        if path is None:return body
        raise ValueError('incomplete')
    monkeypatch.setattr(stream,'snapshot',snap)
    batches=list(bovada.Bovada().read_batches(None,{'key':'nfl'},CLOCK))
    assert batches[-1].error and not batches[-1].events and not batches[-1].offers


def test_rate_limit_preserves_retry_after(monkeypatch):
    monkeypatch.setattr(stream.net,'_wait',lambda *a:None)
    held=[]
    monkeypatch.setattr(stream.net,'hold',lambda host,until:held.append((host,until)))
    def connect(*a,**kw):
        raise websocket.WebSocketBadStatusException('limited',status_code=429,resp_headers={'Retry-After':'3600'})
    monkeypatch.setattr(websocket,'create_connection',connect)
    with pytest.raises(stream.net.HttpError) as err:stream.snapshot('nba')
    assert err.value.status==429 and err.value.not_before_iso and held


def test_wrong_event_and_end_marker_rejected(monkeypatch):
    e=coupon()[0]['events'][0]
    for frames in ([frame('event_catchup_start'),frame('fullEvent',e)],
                   [frame('event_catchup_start'),frame('event_catchup_end',path='$wrong')]):
        s=install(monkeypatch,frames)
        with pytest.raises(ValueError):stream.snapshot('nba','/basketball/nba/expected')
        assert s.closed


def test_network_timeout_closes_socket(monkeypatch):
    s=install(monkeypatch,[])
    def recv():raise websocket.WebSocketTimeoutException('timeout')
    s.recv=recv
    with pytest.raises(ValueError):stream.snapshot('nba')
    assert s.closed
