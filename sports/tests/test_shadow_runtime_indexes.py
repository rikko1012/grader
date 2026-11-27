"""Additive migration keeps shadow payloads intact and avoids full payload scans."""
from sports.model import shadow


def test_shadow_indexes_preserve_rows_and_cover_progress(tmp_path):
    path = tmp_path / 'legacy.db'
    con = shadow.db(path)
    for i in range(100):
        con.execute('INSERT INTO shadow_forecasts (shadow_id,model_version,forecast_at,event_start,platform,stat,line,pmf_json) VALUES (?,?,?,?,?,?,?,?)',
                    (str(i), 'sn-1' if i % 2 else 'sm-2', f'2026-11-{1+i%20:02}T00:00:00Z',
                     '2026-11-30T00:00:00Z', 'prizepicks', 'rec', 3.5, '[0.1,0.9]' * 100))
    con.commit()
    # Reconstruct the old schema in this disposable test DB.
    con.execute('DROP INDEX shadow_forecast_at')
    con.execute('DROP INDEX shadow_model_progress')
    before = con.execute('SELECT * FROM shadow_forecasts ORDER BY shadow_id').fetchall()
    query = 'SELECT MIN(forecast_at) FROM shadow_forecasts WHERE model_version=?'
    expected = con.execute(query, ('sn-1',)).fetchone()
    con.close()
    con = shadow.db(path)
    assert con.execute('SELECT * FROM shadow_forecasts ORDER BY shadow_id').fetchall() == before
    assert con.execute(query, ('sn-1',)).fetchone() == expected
    progress = ' '.join(str(tuple(r)) for r in con.execute('EXPLAIN QUERY PLAN '+query, ('sn-1',)))
    assert 'COVERING INDEX shadow_model_progress' in progress
    retention = ' '.join(str(tuple(r)) for r in con.execute(
        'EXPLAIN QUERY PLAN SELECT shadow_id FROM shadow_forecasts WHERE forecast_at < ?', ('2026-01-01',)))
    assert 'shadow_forecast_at' in retention
    con.close()
