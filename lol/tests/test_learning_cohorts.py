from datetime import timedelta

from lol.center import forecast, learning, store
from lol.tests.test_consolidation import con, _future_world, test_saved_forecast_settles_when_the_series_completes as settle_fixture


def test_evidence_deduplicates_book_lines_and_splits_model_versions(con, tmp_path, monkeypatch):
    settle_fixture(con, tmp_path, monkeypatch)
    original = dict(con.execute('SELECT * FROM lol_forecasts LIMIT 1').fetchone())
    cols = [k for k in original if k != 'id']
    duplicate = dict(original, market_key='other-book-line', platform='prizepicks', model_version='test-next',
                     fit_sha='new-fit', p_over=.7, p_under=.3, p_push=0, model_side='over', side_offered=0)
    con.execute(f"INSERT INTO lol_forecasts ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [duplicate[k] for k in cols])
    settlement = dict(con.execute('SELECT * FROM lol_settlements LIMIT 1').fetchone())
    settlement['unit_key'] = f"prizepicks|other-book-line|{duplicate['line']}"
    cols = [k for k in settlement if k != 'id']
    con.execute(f"INSERT INTO lol_settlements ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", [settlement[k] for k in cols])
    con.commit()
    d = learning.dashboard(con)
    assert d['metrics']['all']['n'] == 2
    assert d['counts']['graded_distinct_player_props'] == 1
    assert d['evidence']['graded_player_markets'] == 1
    assert d['metrics']['model test-next']['n'] == 1
    assert d['metrics']['side not offered']['n'] == 1
    assert 'model test-next offered picks printed 60%+' not in d['metrics']


def test_server_recheck_rejects_stale_quote_even_if_market_remains_open(con, tmp_path, monkeypatch):
    db, start, _ = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    f = con.execute('SELECT * FROM lol_forecasts LIMIT 1').fetchone()
    now = forecast.datetime.now(forecast.timezone.utc)
    con.execute('UPDATE market_state SET last_seen=?', (store.now_iso((now - timedelta(minutes=16)).timestamp()),))
    bad = forecast.slip_check(con, [f['id']], now=now)
    assert any('older than 15 minutes' in r for r in bad['reasons'])
    con.execute('UPDATE market_state SET last_seen=?', (store.now_iso(now.timestamp()),))
    fresh = forecast.slip_check(con, [f['id']], now=now)
    assert not any('line check' in r for r in fresh['reasons'])
    # Freshness never bypasses the existing stake/eligibility rules.
    assert not fresh['eligible']
