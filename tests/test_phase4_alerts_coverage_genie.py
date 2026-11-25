"""Phase 4 items 3-5: background alert delivery rules (cap, cooldown, unsubscribe, deep link, dry run), SCOUT_GENIE_PERSONA and
SCOUT_ADMIN_COVERAGE. Each flag off and on. No network: Whop delivery is a fake; nothing is sent by the dry run."""
import json
import sqlite3

import pytest

import admin_coverage
import server
import workspace_alerts as a
import workspace_genie as genie
from tests.test_workspace_api import env, get, post, call, API, ADMIN  # noqa: F401
from tests.test_workspace_alerts import row


def setup(tmp_path, monkeypatch, daily=None, cooldown=None):
    monkeypatch.setenv('SCOUT_BACKGROUND_ALERTS', '1'); monkeypatch.setenv('WHOP_API_KEY', 'test-key')
    monkeypatch.setenv('WHOP_NOTIFICATION_EXPERIENCE_ID', 'exp_test'); monkeypatch.setenv('SCOUT_ALERT_DEEPLINK', '1')
    monkeypatch.delenv('SCOUT_ALERT_SECRET', raising=False)
    if daily:
        monkeypatch.setenv('SCOUT_ALERT_DAILY', str(daily))
    if cooldown:
        monkeypatch.setenv('SCOUT_ALERT_COOLDOWN_S', str(cooldown))
    a.subscription(tmp_path, 'member', True, 'user_test', ['cs2'], now=1000)
    state = {'prefs': {'researchHub': {'alerts': [{'name': 'Kills', 'filters': {'sport': 'cs2', 'book': 'underdog'}}]}}}
    sent = []

    def send(dest, title, content, rest_path=None):
        sent.append({'dest': dest, 'title': title, 'content': content, 'rest_path': rest_path}); return 'queued'
    return state, sent, send


def test_daily_cap_per_member_marks_seen_and_never_floods(tmp_path, monkeypatch):
    state, sent, send = setup(tmp_path, monkeypatch, daily=2, cooldown=60)
    t = lambda rows, now: a.tick(tmp_path, lambda m: state, rows, lambda u: True, send, now=now)
    t([row('1')], 1000)
    t([row('2')], 1100); t([row('3')], 1200)
    assert len(sent) == 2
    out = t([row('4')], 1300)
    assert len(sent) == 2 and out['capped'] == 1 and a.subscription(tmp_path, 'member', now=1300)['status'] == 'daily_cap'
    t([row('4')], 1000 + 86400 + 200)                         # the cap lifts: revision 4 was marked seen, nothing piles up
    assert len(sent) == 2
    t([row('5')], 1000 + 86400 + 300)
    assert len(sent) == 3


def test_cooldown_is_configurable(tmp_path, monkeypatch):
    state, sent, send = setup(tmp_path, monkeypatch, cooldown=3600)
    t = lambda rows, now: a.tick(tmp_path, lambda m: state, rows, lambda u: True, send, now=now)
    T = 100000
    t([row('1')], T); t([row('2')], T + 100); t([row('3')], T + 200)
    assert len(sent) == 1
    t([row('3')], T + 100 + 3599)
    assert len(sent) == 1
    t([row('3')], T + 100 + 3600)
    assert len(sent) == 2


def test_message_has_deep_link_and_signed_unsubscribe_within_500_chars(tmp_path, monkeypatch):
    state, sent, send = setup(tmp_path, monkeypatch)
    t = lambda rows, now: a.tick(tmp_path, lambda m: state, rows, lambda u: True, send, now=now)
    t([row('1')], 1000)
    long = row('2'); long['player']['name'] = 'x' * 600
    t([long], 2000)
    m = sent[0]
    assert m['rest_path'].startswith('/?#scout/board?sport=cs2&open=') and len(m['content']) <= 500
    assert '/alerts/unsubscribe?m=member&t=' in m['content'] and m['content'].endswith(a.unsub_token('member'))
    assert 'test-key' not in m['content']
    assert a.unsub_ok('member', a.unsub_token('member')) and not a.unsub_ok('member', 'x' * 32) and not a.unsub_ok('other', a.unsub_token('member'))


def test_preview_is_read_only_and_matches_what_tick_sends(tmp_path, monkeypatch):
    state, sent, send = setup(tmp_path, monkeypatch)
    a.tick(tmp_path, lambda m: state, [row('1')], lambda u: True, send, now=1000)
    db = tmp_path / 'workspace_alerts.db'
    before = sqlite3.connect(db).execute('SELECT count(*) FROM seen').fetchone()[0]
    p = a.preview(tmp_path, lambda m: state, [row('2')], now=2000, base='https://scout.test')
    assert p['subscribers'] == 1 and p['payloads'][0]['would_send'] and p['payloads'][0]['destination'] != 'user_test'
    payload = p['payloads'][0]['payload']
    assert sqlite3.connect(db).execute('SELECT count(*) FROM seen').fetchone()[0] == before and not sent
    a.tick(tmp_path, lambda m: state, [row('2')], lambda u: True, send, now=2000)
    assert sent[0]['title'] == payload['title'] and sent[0]['rest_path'] == payload['rest_path']
    assert p['example'] and p['example']['content']


def test_preview_without_a_database_or_flag_still_shows_an_example(tmp_path, monkeypatch):
    monkeypatch.delenv('SCOUT_BACKGROUND_ALERTS', raising=False)
    p = a.preview(tmp_path, lambda m: {}, [row('1')], base='https://scout.test')
    assert p['configured'] is False and p['subscribers'] == 0 and p['example']['title'].startswith('Prop Grader · ')
    assert not (tmp_path / 'workspace_alerts.db').exists()


def raw(env, method, path, body=None, headers=None):
    import http.client
    c = http.client.HTTPConnection('127.0.0.1', env.port, timeout=30)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    out = r.status, r.read().decode('utf-8', 'replace')
    c.close()
    return out


def test_unsubscribe_route_get_confirms_post_turns_off(env, monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_BACKGROUND_ALERTS', '1'); monkeypatch.setenv('WHOP_API_KEY', 'k'); monkeypatch.setenv('WHOP_NOTIFICATION_EXPERIENCE_ID', 'exp_t')
    a.subscription(env.tmp, 'wm-abc', True, 'user_x', ['cs2'])
    tok = a.unsub_token('wm-abc')
    st, body = raw(env, 'GET', f'/alerts/unsubscribe?m=wm-abc&t={tok}')
    assert st == 200 and 'Stop alerts' in body and a.subscription(env.tmp, 'wm-abc')['enabled']     # GET never unsubscribes
    assert raw(env, 'POST', '/alerts/unsubscribe', f'm=wm-abc&t={"0" * 32}', {'Content-Type': 'application/x-www-form-urlencoded'})[0] == 400
    assert a.subscription(env.tmp, 'wm-abc')['enabled']
    st, body = raw(env, 'POST', '/alerts/unsubscribe', f'm=wm-abc&t={tok}', {'Content-Type': 'application/x-www-form-urlencoded'})
    assert st == 200 and 'off' in body and not a.subscription(env.tmp, 'wm-abc')['enabled']


def test_dryrun_admin_page_needs_the_token_and_writes_nothing(env, monkeypatch):
    assert raw(env, 'GET', '/admin/alerts_dryrun.json')[0] in (401, 403, 404)
    st, body = raw(env, 'GET', f'/admin/alerts_dryrun.json?token={ADMIN}')
    doc = json.loads(body)
    assert st == 200 and doc['subscribers'] == 0 and doc['example']['content']
    st, body = raw(env, 'GET', f'/admin/alerts_dryrun?token={ADMIN}')
    assert st == 200 and 'Alert dry run' in body and 'Nothing here is sent' in body
    assert not (env.tmp / 'workspace_alerts.db').exists()


# ---------------------------------------------------------------------------------------------------- SCOUT_ADMIN_COVERAGE

def test_coverage_off_is_404_and_on_lists_every_source(env, monkeypatch):
    monkeypatch.delenv('SCOUT_ADMIN_COVERAGE', raising=False)
    assert raw(env, 'GET', f'/admin/coverage?token={ADMIN}')[0] == 404
    monkeypatch.setenv('SCOUT_ADMIN_COVERAGE', '1')
    assert raw(env, 'GET', '/admin/coverage.json')[0] in (401, 403, 404)
    st, body = raw(env, 'GET', f'/admin/coverage.json?token={ADMIN}')
    doc = json.loads(body)
    assert st == 200
    names = {r['source'] for r in doc['rows']}
    assert {'Underdog (board)', 'PrizePicks (board)', 'Sleeper', 'FanDuel', 'BetRivers', 'The Odds API books', 'Background alerts (Whop push)'} <= names
    ud = next(r for r in doc['rows'] if r['source'] == 'Underdog (board)')
    assert ud['state'] == 'ok' and ud['count'] >= 1 and ud['age_s'] is not None
    sl = next(r for r in doc['rows'] if r['source'] == 'Sleeper')
    assert sl['state'] == 'off' and sl['flag'] == 'SCOUT_BOOK_SLEEPER'
    assert all(r['state'] in ('ok', 'stale', 'failing', 'backoff', 'refused', 'starting', 'off', 'blocked', 'unknown') for r in doc['rows'])
    assert {r['source'] for r in doc['rows'] if r['state'] == 'blocked'} == {'Betr Picks', 'ParlayPlay', 'Boom Fantasy'}
    st, body = raw(env, 'GET', f'/admin/coverage?token={ADMIN}')
    assert st == 200 and 'Source health' in body and 'Underdog (board)' in body and ADMIN not in body


def test_coverage_row_marks_old_reads_stale():
    r = admin_coverage._row('X', 'Y', 'ok', at=1000.0, now=1000.0 + 31 * 60)
    assert r['state'] == 'stale' and r['age_s'] == 1860.0


# ---------------------------------------------------------------------------------------------------- SCOUT_GENIE_PERSONA

DOC = {"record": {"sport": "cs2", "player": {"name": "s1mple"}, "market": {"stat_label": "Kills", "period_label": "Maps 1-2"},
                  "offer": {"platform": "underdog", "platform_label": "Underdog", "offer_id": "7", "line": 38.5},
                  "scout": {"side": "over", "p_over": 0.6123, "projection": 40.26, "grade": "B+"}}}


def test_persona_off_answers_are_unchanged(monkeypatch):
    monkeypatch.delenv('SCOUT_GENIE_PERSONA', raising=False)
    out = genie.answer(DOC, 'What is the projection?', 'over')
    assert 'intro' not in out and not any('\U0001F9DE' in s for s in out['sentences'])
    assert genie.answer(DOC, 'weather?', 'over')['sentences'] == [genie.HELP]


def test_persona_on_adds_the_voice_but_keeps_checked_numbers(monkeypatch):
    monkeypatch.setenv('SCOUT_GENIE_PERSONA', '1')
    off = dict(genie.answer(DOC, 'What is the projection?', 'over'))
    assert off['intro'].startswith('\U0001F9DE')
    assert not genie._NUM_RE.findall(off['intro']) and not genie._NUM_RE.findall(genie.PERSONA_HELP) and not genie._NUM_RE.findall(genie.PERSONA_INTRO)
    assert all(not genie._NUM_RE.findall(v) for v in genie.PERSONA_OPEN.values())
    monkeypatch.delenv('SCOUT_GENIE_PERSONA')
    assert genie.answer(DOC, 'What is the projection?', 'over')['sentences'] == off['sentences']
    monkeypatch.setenv('SCOUT_GENIE_PERSONA', '1')
    assert genie.answer(DOC, 'weather?', 'over')['sentences'] == [genie.PERSONA_HELP]


def test_persona_feature_flag_in_me_and_genie_route(env, monkeypatch):
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: [])
    genie.reset()
    monkeypatch.setenv('SCOUT_GENIE', '1')
    monkeypatch.delenv('SCOUT_GENIE_PERSONA', raising=False)
    assert 'genie_persona' not in get(env, 'me')[1]['features'] and 'intro' not in get(env, 'genie')[1]
    monkeypatch.setenv('SCOUT_GENIE_PERSONA', '1')
    assert get(env, 'me')[1]['features']['genie_persona'] is True and get(env, 'genie')[1]['intro'] == genie.PERSONA_INTRO
    monkeypatch.delenv('SCOUT_GENIE')                                   # persona never turns the Genie on by itself
    assert 'genie_persona' not in get(env, 'me')[1]['features']
    genie.reset()
