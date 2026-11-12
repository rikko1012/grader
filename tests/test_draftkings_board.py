import copy
import json

import pytest
import workspace_draftkings as dk


@pytest.fixture
def board(monkeypatch):
    base = {'sport': 'nba', 'event': {'id': 'nba:espn:1', 'start_utc': dk.wb._iso(3000)},
            'player': {'id': 'nba:player:espn:7', 'key': '7', 'name': 'Example'},
            'market': {'stat': 'points', 'period': 'full_game'},
            'offer': {'platform': 'underdog', 'platform_label': 'Underdog', 'line': 20.5, 'offer_id': 'ud'},
            'scout': {'grade': 'A', 'side': 'over', 'p_side': .65, 'p_over': .65, 'p_under': .35,
                      'projection': 23, 'units': 1}, 'history': {'l5': {'n': 5, 'wins': 3}}}
    prop = {'player_id': '7', 'market': 'points', 'period': 'game', 'kind': 'line', 'line': 20.5,
            'revision_id': 42, 'seen_at': dk.wb._iso(900), 'reported_prices': [-117, 105]}
    doc = {'league': 'nba', 'events': [{'id': 'espn:1', 'start_utc': dk.wb._iso(3000), 'reference_props': [prop]}]}
    monkeypatch.setattr(dk.wb, 'records', lambda *a: ([base], {}))
    monkeypatch.setattr(dk.workspace_game_books, 'shared', lambda *a: (200, json.dumps(doc).encode()))
    return base, prop, doc


def test_exact_identity_keeps_reported_odds_not_executable(board):
    base, _, _ = board
    original = copy.deepcopy(base)
    status, d = dk.build(None, 'nba', 1000)
    assert status == 200 and len(d['records']) == 1
    r = d['records'][0]
    assert r['offer']['reported_prices'] == [-117, 105]
    assert not r['offer']['over']['offered'] and not r['offer']['under']['offered']
    assert not r['offer']['slip'] and not r['extra']['price_comparable']
    assert r['scout']['grade'] is None and r['scout']['units'] is None
    assert r['scout']['projection'] == 23 and r['offer']['revision'] == '42'
    assert base == original


@pytest.mark.parametrize('field,value', [('player_id', '8'), ('market', 'assists'), ('period', '1q'),
                                       ('seen_at', '1970-01-01T00:20:00+00:00')])
def test_mismatch_and_future_quote_not_joined(board, field, value):
    board[1][field] = value
    assert dk.build(None, 'nba', 1000)[1]['records'] == []


def test_wrong_event_stale_and_started_hidden(board):
    event = board[2]['events'][0]
    event['id'] = 'espn:2'
    assert dk.build(None, 'nba', 1000)[1]['records'] == []
    event['id'] = 'espn:1'
    board[2]['stale'] = True
    assert dk.build(None, 'nba', 1000)[1]['feed']['state'] == 'stale'
    board[2]['stale'] = False
    event['start_utc'] = dk.wb._iso(999)
    assert dk.build(None, 'nba', 1000)[1]['records'] == []


def test_other_line_cannot_inherit_probability_or_history(board):
    board[1]['line'] = 25.5
    r = dk.build(None, 'nba', 1000)[1]['records'][0]
    assert r['scout']['p_over'] is None and r['history']['l5'] is None


def test_half_point_line_is_not_treated_as_decimal_price(board):
    board[1]["line"] = 0.5
    assert dk.build(None, "nba", 1000)[1]["records"][0]["offer"]["line"] == 0.5
