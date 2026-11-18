"""football-data.co.uk (2026-11-18): UK kick-off times, exact / verified-alias teams only, score must agree, a bad
answer keeps the last good rows, one file a run, and fd.db stays out of canonical.db and the forecasts."""
from datetime import datetime, timezone
import inspect
import json
import sqlite3

import pytest

from sports import expansion_footballdata as fd

HEAD = ('Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HxG,AxG,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,'
        'HR,AR,B365H,B365D,B365A,AvgH,AvgD,AvgA,B365CH,B365CD,B365CA,AvgCH,AvgCD,AvgCA,MaxCH,MaxCD,MaxCA,AvgC>2.5,AvgC<2.5')
ROW1 = 'E0,21/08/2026,20:00,Arsenal,Coventry,3,0,H,2,0,H,T Bramall,1.88,0.2,20,4,6,1,10,13,8,2,1,1,0,0,1.2,7,13,1.2,6.77,14.19,1.18,7.5,13,1.19,6.72,14,1.23,7.4,18,1.57,2.38'
ROW2 = 'E0,01/11/2026,15:00,Man United,Arsenal,1,1,D,0,1,A,X Ref,1.1,1.3,12,14,4,5,11,9,5,6,2,1,0,0,2.5,3.4,2.8,2.4,3.4,2.9,2.6,3.3,2.7,2.5,3.35,2.8,2.7,3.5,2.95,1.8,2.0'
TODAY = datetime(2026, 11, 18, tzinfo=timezone.utc)


def _match(eid, start, home, away, hs, as_, status='final'):
    return {'event_id': eid, 'competition': 'eng.1', 'start': start, 'status': status,
            'participants': [{'id': f't:{home}', 'name': home}, {'id': f't:{away}', 'name': away}],
            'scores': {f't:{home}': hs, f't:{away}': as_}}


def test_uk_local_time_converts_across_bst():
    assert fd.kickoff_utc('21/08/2026', '20:00') == '2026-08-21T19:00:00Z'      # BST (UTC+1)
    assert fd.kickoff_utc('01/11/2026', '15:00') == '2026-11-01T15:00:00Z'      # GMT after Oct 25
    assert fd.kickoff_utc('01/11/26', '15:00') == '2026-11-01T15:00:00Z'
    assert fd.kickoff_utc('31/02/2026', '15:00') is None


def test_parse_reads_stats_and_closing_prices_only():
    rows = fd.parse('﻿' + HEAD + '\n' + ROW1 + '\n', 'E0', TODAY)
    (r,) = rows
    assert (r['slug'], r['start'], r['home'], r['away'], r['fthg'], r['ftag']) == (
        'eng.1', '2026-08-21T19:00:00Z', 'Arsenal', 'Coventry', 3, 0)
    assert r['stats']['home'] == {'shots': 20, 'shots_on_target': 6, 'corners': 8, 'fouls': 10, 'yellow_cards': 1,
                                  'red_cards': 0, 'xg': 1.88}
    assert r['closing']['Avg'] == {'home': 1.19, 'draw': 6.72, 'away': 14, 'over25': 1.57, 'under25': 2.38}
    assert set(r['closing']) == {'Avg', 'Max', 'B365'}          # opening prices (B365H, AvgH) are never read


@pytest.mark.parametrize('text', ['<!DOCTYPE html><html><body>Not found</body></html>', '', 'Div,Date\n',
                                  'Country,League\nUSA,MLS\n'])
def test_a_non_csv_or_wrong_file_is_refused(text):
    with pytest.raises(ValueError):
        fd.parse(text, 'E0', TODAY)


def test_join_needs_exact_teams_time_and_score():
    row = fd.parse(HEAD + '\n' + ROW1, 'E0', TODAY)[0]
    good = _match('e1', '2026-08-21T19:00:00Z', 'Arsenal', 'Coventry', 3, 0)
    assert fd.join(row, [good]) == ('e1', {'home': 't:Arsenal', 'away': 't:Coventry'})
    assert fd.join(row, [_match('e1', '2026-08-21T19:00:00Z', 'Arsenal', 'Coventry', 2, 0)]) is None   # score
    assert fd.join(row, [_match('e1', '2026-08-21T23:30:00Z', 'Arsenal', 'Coventry', 3, 0)]) is None   # time
    assert fd.join(row, [good, dict(good, event_id='e2')]) is None                                      # not unique
    assert fd.join(row, [dict(good, status='scheduled')]) is None


def test_partial_names_never_match_only_a_verified_alias_does(monkeypatch):
    row = fd.parse(HEAD + '\n' + ROW2, 'E0', TODAY)[0]
    m = _match('e9', '2026-11-01T15:00:00Z', 'Manchester United', 'Arsenal', 1, 1)
    assert fd.join(row, [m]) is None                                 # "Man United" is not "Manchester United"
    monkeypatch.setitem(fd.ALIASES, ('eng.1', 'Man United'), 't:Manchester United')
    assert fd.join(row, [m]) == ('e9', {'home': 't:Manchester United', 'away': 't:Arsenal'})


def test_collect_reads_one_file_a_run_and_a_bad_answer_keeps_the_last_rows(tmp_path):
    catalogs = {'eng.1': {'e1': _match('e1', '2026-08-21T19:00:00Z', 'Arsenal', 'Coventry', 3, 0)}}
    asked = []
    answers = iter([HEAD + '\n' + ROW1, '<html>busy</html>'])

    def get_text(url, params):
        asked.append(url)
        return next(answers)
    first = fd.collect(get_text, tmp_path, catalogs)
    assert asked == ['https://www.football-data.co.uk/mmz4281/2627/E0.csv'] and first['joined'] == 1
    form = fd.load_form(tmp_path)
    assert form['t:Arsenal']['for_shots_l5'] == 20 and form['t:Coventry']['against_shots_l5'] == 20
    assert form['t:Arsenal']['closing'][0] == {'start': '2026-08-21', 'result': 'W', 'win': 1.19, 'draw': 6.72, 'loss': 14}
    second = fd.collect(get_text, tmp_path, catalogs)                # next file (E1) answers HTML
    assert len(asked) == 2 and asked[1].endswith('/E1.csv') and second['errors'] == ['ValueError']
    with sqlite3.connect(tmp_path / 'football-data' / 'fd.db') as con:
        assert con.execute('SELECT count(*), max(event_id) FROM fixtures').fetchone() == (1, 'e1')
    assert fd.load_form(tmp_path) == form
    state = json.loads((tmp_path / 'football-data' / 'state.json').read_text())
    assert state['E0']['checked_at'] and 'checked_at' not in state['E1'] and state['E1']['attempt_at']


def test_every_file_is_read_at_most_once_a_day(tmp_path):
    asked = []
    for _ in range(len(fd.MAIN) + len(fd.EXTRA) + 3):
        fd.collect(lambda url, p: asked.append(url) or HEAD + '\n', tmp_path, {})
    assert len(asked) == len(set(asked)) == len(fd.MAIN) + len(fd.EXTRA)


def test_fd_db_is_never_a_forecast_or_history_input():
    from sports import expansion_forecasts, expansion_history
    for obj in (expansion_forecasts, expansion_history._history, expansion_history.resolve):
        src = inspect.getsource(obj)
        assert 'football' not in src and 'fd.db' not in src and 'team-form' not in src
    assert not any(src == 'football-data' for _c, src, _s in expansion_forecasts.FORECAST_INPUTS)
