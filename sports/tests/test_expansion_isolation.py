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
        # sg-stability 2026-11-20: learning (the grade publisher) is LIGHT - beside exclusive jobs, cap still reserved
        assert jobs[sport+'-learning']['cls']==LIGHT and Scheduler._beside(jobs[sport+'-learning'])

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
