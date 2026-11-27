import json
from copy import deepcopy

import pytest

from sports import expansion_history as history
from sports.service import expansion_side_alerts


@pytest.mark.parametrize('comp,tid,name,book,opp', [
    ('aus.1', '11143', 'Melbourne City FC', 'Melbourne City', 'Macarthur'),
    ('ksa.1', '21827', 'Al Fayha', 'Fayha', 'Al Qadsiah'),
    ('ksa.1', '21829', 'Al Khaleej', 'Khaleej', 'Al Kholood'),
    ('ksa.1', '22022', 'Al Qadsiah', 'Al-Qadsiah', 'Al Fayha'),
    ('ksa.1', '22022', 'Al Qadsiah', 'Qadsiah', 'Al Fayha'),
])
def test_verified_book_names_unlock_history_but_not_wrong_fixture(comp, tid, name, book, opp):
    team = 'soccer:team:espn:' + tid
    match = {'competition': comp, 'event_id': 'event', 'start': '2026-11-27T12:00:00Z',
             'participants': [{'id': team, 'name': name}, {'id': 'opponent', 'name': opp}],
             'players': [{'id': 'player', 'name': 'Test Player', 'team_id': team}]}
    offer = {'sport': 'soccer', 'team': book, 'teams': [book, opp], 'player': 'Test Player',
             'start': match['start'], 'competition_code': comp}
    assert history.resolve(offer, [match])['player_id'] == 'player'
    assert history.soccer_teams(offer, match)[0]['id'] == team
    for altered in (dict(offer, teams=[book, 'Wrong']), dict(offer, player='Wrong'),
                    dict(offer, start='2026-11-28T12:00:00Z')):
        assert history.resolve(altered, [match]) is None
    wrong = deepcopy(match)
    wrong['players'][0]['team_id'] = 'opponent'
    assert history.resolve(offer, [wrong]) is None
    assert history.resolve(offer, [match, dict(match, event_id='second')]) is None


def test_partial_success_is_visible_without_database_reads(tmp_path):
    state = {'finished_at': '2026-11-27T03:36:34Z', 'records': 4380,
             'errors': ['HttpError'], 'phases': {'live': 'partial'},
             'sources': {'fotmob': {'ok': 1, 'errors': []}}, 'unrelated': 'omit'}
    path = tmp_path / 'history-state.json'
    path.write_text(json.dumps(state))
    run = history.history_run_status(tmp_path)
    assert run['records'] == 4380 and run['errors'] == ['HttpError']
    assert 'unrelated' not in run
    assert expansion_side_alerts('soccer', {'history_run': run})[0]['code'] == 'expansion_history_partial'
    state['errors'] = []
    path.write_text(json.dumps(state))
    assert not expansion_side_alerts('soccer', {'history_run': history.history_run_status(tmp_path)})


@pytest.mark.parametrize('pid,canonical,book', [
    ('239483', 'Georgios Masouras', 'Giorgos Masouras'),
    ('357660', 'Ala Al-Hajji', 'Alaa Al Hejji'),
    ('268468', 'Abdi Faris', 'Faris Abdi'),
])
def test_player_labels_are_id_scoped(pid, canonical, book):
    player = {'id': 'soccer:player:espn:' + pid, 'name': canonical}
    assert history.normalized(book) in history.player_names(player)
    assert history.normalized(book) not in history.player_names(dict(player, id='other'))


def test_status_reader_handles_missing_corrupt_and_oversized_files(tmp_path):
    assert history.history_run_status(tmp_path) is None
    path = tmp_path / 'history-state.json'
    for content in ('[]', '{', 'x' * 65537):
        path.write_text(content)
        assert history.history_run_status(tmp_path).get('error')
