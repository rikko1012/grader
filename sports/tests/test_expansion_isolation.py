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
    assert {k:v for k,v in after.items() if k not in ('tennis-quotes','tennis-history')}==before
