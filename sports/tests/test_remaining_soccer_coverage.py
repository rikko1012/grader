from copy import deepcopy
from datetime import date
import json

import pytest

from sports import expansion_history as history, expansion_soccer_detail as detail
from sports.contracts import now_utc
from sports.tests.test_expansion_soccer_detail import fixture, NOW


def test_offered_future_dates_rotate_without_expanding_request_batch(tmp_path):
    rows = [{'sport': 'soccer', 'start': f'2026-11-{day}T17:30:00Z'} for day in range(24, 33)]
    rows += [{'sport': 'soccer', 'start': 'bad'}, {'sport': 'tennis', 'start': '2026-11-27T17:00:00Z'}]
    (tmp_path/'board.json').write_text(json.dumps({'offers': rows}))
    seen = set()
    for offset in range(5):
        days = history.soccer_offer_dates(tmp_path, date(2026, 11, 24), offset)
        assert len(days) <= 2
        seen.update(days)
    assert seen == {'20261127', '20261128', '20261129', '20261130'}


def test_saudi_short_labels_still_require_exact_opponent_time_and_player_team():
    match = {'event_id': 'soccer:espn:ksa.1:401900443:401900443', 'competition': 'ksa.1',
             'start': '2026-11-26T15:05:00Z',
             'participants': [{'id': 'soccer:team:espn:793', 'name': 'Al Shabab'},
                              {'id': 'soccer:team:espn:21833', 'name': 'Abha'}],
             'players': [{'id':'p1','name':'Test Player','team_id':'soccer:team:espn:793'}]}
    offer = {'sport':'soccer','team':'Shabab','teams':['Shabab','Abha'], 'player':'Test Player',
             'start':match['start'], 'competition_code':'ksa.1'}
    assert history.resolve(offer, [match])['player_id'] == 'p1'
    for change in ({'teams':['Shabab','Other']}, {'start':'2026-11-27T15:05:00Z'},
                   {'team':'Abha'}, {'competition_code':'eng.1'}):
        assert history.resolve(dict(offer, **change), [match]) is None


@pytest.mark.parametrize('source_id', list(detail.PLAYER_LINKS))
def test_verified_provider_identity_recovers_exact_stats(source_id):
    match, body = fixture()
    target, source_name, canonical_name = detail.PLAYER_LINKS[source_id]
    match['players'][0].update(id=target, name=canonical_name)
    body['content']['playerStats']['1'].update(id=int(source_id), name=source_name)
    records = detail.parse_detail(body, 'bra.1', [match], NOW).records
    assert {r.stat:r.value for r in records}['passes_attempted'] == 46
    assert all(r.participant_id == target for r in records)
    for field, value in [('id', 9999999), ('name', 'Unverified Player'), ('teamId', 12)]:
        broken = deepcopy(body)
        broken['content']['playerStats']['1'][field] = value
        assert not detail.parse_detail(broken, 'bra.1', [match], NOW).records


def test_verified_team_alias_keeps_final_score_check():
    match, body = fixture()
    match['participants'][0]['name'] = 'Athletico-PR'
    body['header']['teams'][0].update(id=10273, name='Athletico Paranaense')
    body['content']['playerStats']['1']['teamId'] = 10273
    assert detail.parse_detail(body, 'bra.1', [match], NOW).records
    body['header']['teams'][0]['score'] = 99
    assert not detail.parse_detail(body, 'bra.1', [match], NOW).records


def test_identity_repair_retries_recently_checked_match_once_without_erasing_history(tmp_path):
    match, body = fixture()
    target, source_name, canonical_name = detail.PLAYER_LINKS['1130833']
    match['players'][0].update(id=target, name=canonical_name)
    body['content']['playerStats']['1'].update(id=1130833, name=source_name)
    state_path = tmp_path/'detail-history-state.json'
    state_path.write_text(json.dumps({match['event_id']: now_utc()}))
    stored = []
    listing = {'leagues':[{'primaryId':268,'matches':[{'id':44,'home':body['header']['teams'][0],
               'away':body['header']['teams'][1],'status':body['header']['status']}]}]}
    calls = []
    def get(url, params):
        calls.append(params)
        return body if 'matchDetails' in url else listing
    detail.collect_details({'bra.1':{'one':match}}, get, lambda feed, comp: stored.extend(feed.records), tmp_path)
    assert stored and len(calls) == 2
    assert json.loads((tmp_path/'detail-identity-rechecks.json').read_text())[match['event_id']] == detail.IDENTITY_REPAIR
    detail.collect_details({'bra.1':{'one':match}}, get, lambda *a: pytest.fail('repeat write'), tmp_path)
    assert len(calls) == 2


def test_unmatched_repair_obeys_retry_cooldown(tmp_path):
    match, body = fixture()
    target, _, canonical_name = detail.PLAYER_LINKS['1130833']
    match['players'][0].update(id=target, name=canonical_name)
    calls = []
    def get(url, params):
        calls.append(params)
        return {'leagues': []}
    for _ in range(2):
        detail.collect_details({'bra.1': {'one': match}}, get, lambda *a: pytest.fail('unmatched write'), tmp_path)
    assert len(calls) == 1
    saved = json.loads((tmp_path/'detail-identity-rechecks.json').read_text())
    assert saved[match['event_id']]['version'] == detail.IDENTITY_REPAIR


@pytest.mark.parametrize('tid,canonical,book', [('494','Slavia Prague','Slavia'),('132','Bayern Munich','FC Bayern')])
def test_verified_ucl_labels_still_require_fixture_identity(tid, canonical, book):
    match = {'event_id': 'soccer:espn:uefa.champions:1:1', 'competition': 'uefa.champions',
             'start': '2026-11-25T20:00:00Z',
             'participants': [{'id': 'soccer:team:espn:'+tid, 'name': canonical}, {'id':'opponent','name':'Other Team'}],
             'players':[{'id':'player','name':'Test Player','team_id':'soccer:team:espn:'+tid}]}
    offer = {'sport':'soccer','team':book,'teams':[book,'Other Team'],'player':'Test Player',
             'start':match['start'],'competition_code':'uefa.champions'}
    assert history.resolve(offer,[match])['player_id'] == 'player'
    assert history.resolve(dict(offer, teams=[book,'Wrong Team']),[match]) is None
