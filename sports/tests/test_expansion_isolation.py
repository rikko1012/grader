import sqlite3

import pytest

from sports.expansion import isolated_database


def test_every_competition_has_separate_storage(tmp_path):
    from sports.expansion import COMPETITIONS
    paths = [isolated_database(tmp_path, c) for c in COMPETITIONS]
    assert len(set(paths)) == len(COMPETITIONS)
    assert all(p != tmp_path / 'canonical.db' for p in paths)
    assert not any(p.exists() for p in paths)


@pytest.mark.parametrize('competition,foreign', [('atp','wta'), ('wta','nba'), ('eng.1','nfl'), ('atp','soccer')])
def test_foreign_sport_refused_before_any_migration(tmp_path, competition, foreign):
    path = isolated_database(tmp_path, competition)
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE records (league TEXT, event_id TEXT)')
    con.execute('INSERT INTO records VALUES (?, NULL)', (foreign,))
    con.commit()
    con.close()
    with pytest.raises(ValueError, match='another sport or tour'):
        isolated_database(tmp_path, competition)


def test_same_sport_other_competition_refused(tmp_path):
    path = isolated_database(tmp_path, 'eng.1')
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE records (league TEXT, event_id TEXT)')
    con.execute("INSERT INTO records VALUES ('soccer', 'soccer:espn:esp.1:123:123')")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match='another competition'):
        isolated_database(tmp_path, 'eng.1')


def test_new_collectors_are_explicitly_opt_in(monkeypatch):
    from sports.scheduler import default_jobs
    monkeypatch.delenv('SCOUT_SOCCER_ENABLED',raising=False)
    monkeypatch.delenv('SCOUT_TENNIS_ENABLED',raising=False)
    before=default_jobs()
    assert 'soccer-quotes' not in before and 'tennis-quotes' not in before
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1')
    after=default_jobs()
    assert after['tennis-quotes']['argv'][-1]=='tennis'
    assert 'soccer-quotes' not in after
    assert after['tennis-history']['argv'][-1]=='tennis'
    assert after['tennis-learning']['argv'][-1]=='tennis'
    assert {k:v for k,v in after.items() if k not in ('tennis-quotes','tennis-history','tennis-learning')}==before


def test_history_and_quotes_stay_fresh_but_learning_serializes_with_history(monkeypatch):
    from sports.scheduler import Scheduler, default_jobs, LIGHT, HEAVY
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED','1')
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED','1')
    jobs=default_jobs()
    for sport in ('soccer','tennis'):
        quote=jobs[sport+'-quotes']
        assert quote['cls']==LIGHT and Scheduler._beside(quote)
        assert quote['timeout_s']<=90 and quote['cap_mb']>0
        history=jobs[sport+'-history']
        assert history['cls']==LIGHT and Scheduler._beside(history)
        assert history['cap_mb']==120 and history['timeout_s']<=180
        assert history['every_s']==300
        assert not Scheduler._beside(jobs[sport+'-learning'])

    # Light history still reserves memory and cannot overlap its own learning
    # process, in either start order. Other sports retain independent slots.
    sch=Scheduler.__new__(Scheduler)
    sch.specs=jobs;sch.running={};sch.budget=100;sch.max_heavy=2
    assert not sch._fits('soccer-history')
    sch.budget=1000
    sch.running={'soccer-history':None}
    assert not sch._fits('soccer-learning')
    assert sch._fits('tennis-history') and sch._fits('soccer-quotes')
    sch.running={'soccer-learning':None}
    assert not sch._fits('soccer-history')
    assert sch._fits('tennis-history')


# ---- Kalshi reference prices never reach a forecast, learning or grading path ----------------------------------

def test_grading_and_model_code_never_reads_reference_prices():
    from pathlib import Path
    sports = Path(__file__).resolve().parents[1]
    files = [sports / 'expansion_forecasts.py', sports / 'expansion_learning.py', sports / 'expansion_research.py',
             sports / 'expansion_challenger.py'] + sorted((sports.parent / 'gradecore').rglob('*.py'))
    assert len(files) > 5
    for path in files:
        text = path.read_text(encoding='utf-8').casefold()
        assert 'reference_prices' not in text and 'expansion_kalshi' not in text, path
        if path.parent.name != 'gradecore' and 'gradecore' not in path.parts[-3:]:
            assert 'kalshi' not in text, path


def _reference_row(sport):
    return {'sport': sport, 'platform': 'kalshi', 'offer_id': 'KXEPLGAME-X', 'revision': 'r', 'start': '2026-11-18T18:00:00Z',
            'observed_at': '2026-11-17T23:00:00Z', 'reference_only': True, 'event_id': sport + ':event:1',
            'outcomes': [{'label': 'A', 'bid': .6, 'ask': .61, 'mid': .605}, {'label': 'B', 'bid': .39, 'ask': .4, 'mid': .395}]}


def test_forecasts_are_identical_with_and_without_reference_prices(tmp_path, monkeypatch):
    import json
    from sports import expansion_board as boards, expansion_forecasts as model, expansion_research as research
    from sports.tests.test_expansion_forecasts import fixture, NOW
    offer, facts = fixture()
    monkeypatch.setattr(research, 'saved', lambda sport: {'players': {research.key(offer): facts}})
    answers = []
    for name, extra in (('plain', {}), ('with', {'reference_prices': [_reference_row('soccer')]})):
        monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path / name))
        monkeypatch.setattr(boards, 'board', lambda req, extra=extra: (200, {'offers': [offer], **extra}))
        report = model.publish('soccer', NOW)
        answers.append((report, json.loads((tmp_path / name / 'soccer' / 'forecasts.json').read_text())))
    assert answers[0] == answers[1] and answers[0][0]['published'] == 1


def test_learning_is_identical_with_and_without_reference_prices(tmp_path, monkeypatch):
    import json
    from contextlib import closing
    from sports import expansion_learning as learn
    from sports.expansion_history import atomic_json
    from sports.tests.test_expansion_learning import setup as learning_setup, NOW
    saved = []
    for name in ('plain', 'with'):
        (tmp_path / name).mkdir()
        root, path, m, offer = learning_setup.__wrapped__(tmp_path / name, monkeypatch)   # the learning tests' own store
        if name == 'with':
            atomic_json(root / 'board.json', {'sport': 'tennis', 'offers': [offer], 'reference_prices': [_reference_row('tennis')]})
        report = learn.cycle('tennis', NOW)
        with closing(learn.connect('tennis')) as c:
            saved.append((report['forecasts'], [json.loads(r[0]) for r in c.execute('SELECT payload FROM predictions')]))
    assert saved[0] == saved[1] and saved[0][0]['saved'] == 1


def test_reference_rows_are_written_only_under_the_sports_own_expansion_folder(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sports import config, expansion_board as boards
    from sports.contracts import utc
    monkeypatch.delenv('SCOUT_EXPANSION_DATA_DIR', raising=False)
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    (tmp_path / 'canonical.db').write_bytes(b'nba-nfl')
    now = datetime.now(timezone.utc)
    row = dict(_reference_row('soccer'), start=utc(now + timedelta(hours=1)), observed_at=utc(now))
    boards.save('soccer', [], {}, utc(now), reference_prices=[row])
    written = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*') if p.is_file())
    assert written == ['canonical.db', 'expansion/soccer/board.json', 'expansion/soccer/quotes.db']
    assert (tmp_path / 'canonical.db').read_bytes() == b'nba-nfl'


def test_kalshi_adds_no_job_and_changes_no_job_spec(monkeypatch):
    from sports.scheduler import default_jobs
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    monkeypatch.delenv('SCOUT_SOCCER_KALSHI', raising=False)
    on = default_jobs()
    monkeypatch.setenv('SCOUT_SOCCER_KALSHI', '0')
    monkeypatch.setenv('SCOUT_TENNIS_KALSHI', '0')
    assert default_jobs() == on
    assert {k for k in on if k.startswith(('soccer-', 'tennis-'))} == {
        f'{s}-{j}' for s in ('soccer', 'tennis') for j in ('quotes', 'history', 'learning')}
    quotes = on['soccer-quotes']
    assert quotes['timeout_s'] == 90 and quotes['every_s'] == 180 and quotes['argv'][-2:] == ['sports.expansion_board', 'soccer']
