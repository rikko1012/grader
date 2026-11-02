"""A forecast must belong to the offer's event, league and period (audit 2026-11-02). Synthetic records."""
from dataclasses import replace
import pytest
from sports import store, picks
from sports.tests import slip_world as w

@pytest.mark.parametrize('field,value', [('event_id','nba:espn:999'), ('scope','q1'), ('league','nfl')])
def test_track_rejects_inconsistent_forecast_identity(field, value):
    con = store.connect()
    try:
        rid, fc = w.world(con, [('audit1','T1',1,'over',0.61)])['audit1']
        invalid = replace(fc, **{field:value})
        invalid = replace(invalid, forecast_id='fc-'+invalid.content_hash()[:24])
        store.put(con, invalid)
        with pytest.raises(picks.Refused, match='another line|record|mismatch'):
            picks.check(con, {'platform':'underdog','record_id':rid,'side':'over',
                             'forecast_id':invalid.forecast_id}, now=w.NOW)
    finally:
        con.close()
