"""A forecast must belong to the offer's event, league and period (audit 2026-11-02). Synthetic records."""
from dataclasses import replace
import pytest
from sports import store, picks
from sports.contracts import ContractError, Forecast
from sports.tests import slip_world as w

@pytest.mark.parametrize('field,value', [('event_id','nba:espn:999'), ('scope','q1'), ('league','nfl')])
def test_track_rejects_inconsistent_forecast_identity(field, value, monkeypatch):
    con = store.connect()
    try:
        rid, fc = w.world(con, [('audit1','T1',1,'over',0.61)])['audit1']
        invalid = replace(fc, **{field:value})
        invalid = replace(invalid, forecast_id='fc-'+invalid.content_hash()[:24])
        # Simulate a record written BEFORE the storage guard existed. Tracking
        # must continue protecting existing databases as well as new writes.
        with monkeypatch.context() as old_writer:
            old_writer.setattr(Forecast, 'validate_offers', lambda *args: None)
            store.put(con, invalid)
        with pytest.raises(picks.Refused, match='another line|record|mismatch'):
            picks.check(con, {'platform':'underdog','record_id':rid,'side':'over',
                             'forecast_id':invalid.forecast_id}, now=w.NOW)
    finally:
        con.close()


@pytest.mark.parametrize('field,value', [
    ('event_id', 'nba:espn:999'), ('scope', 'q1'), ('league', 'nfl'),
    ('player_id', 'nba:player:espn:other'), ('player_key', 'another player'),
    ('stat', 'assists'), ('line', 21.5),
    ('offer_snapshot_ids', ('missing-revision',)), ('offer_snapshot_ids', ()),
])
def test_storage_refuses_wrong_forecast_without_writing(field, value):
    con = store.connect()
    try:
        rid, fc = w.world(con, [('audit1', 'T1', 1, 'over', .61)])['audit1']
        invalid = replace(fc, **{field: value}, forecast_id='')
        invalid = replace(invalid, forecast_id='fc-' + invalid.content_hash()[:24])
        before = con.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        with pytest.raises(ContractError, match='offer'):
            store.put(con, invalid)
        assert con.execute('SELECT COUNT(*) FROM records').fetchone()[0] == before
        assert store.get(con, fc.forecast_id) == fc
        assert store.get(con, invalid.forecast_id) is None
    finally:
        con.close()


def test_creation_checks_the_supplied_offer_and_preserves_hash():
    o = w.offer('audit1', 'T1', 1)
    fc = w.forecast('revision1', o)
    kw = {k: v for k, v in fc.__dict__.items() if k != 'forecast_id'}
    assert Forecast.build(offers={'revision1': o}, **kw) == fc
    with pytest.raises(ContractError, match='event_id'):
        Forecast.build(offers={'revision1': replace(o, event_id='nba:espn:999')}, **kw)


def test_storage_keeps_historical_revision_and_quarantines_bad_batch():
    con = store.connect()
    try:
        rid, fc = w.world(con, [('audit1', 'T1', 1, 'over', .61)])['audit1']
        o = store.get(con, rid)
        store.put(con, replace(o, line=21.5))
        # Replay stays attached to the earlier 20.5 revision after a move.
        assert store.put(con, fc)['status'] == 'duplicate'
        bad = replace(fc, event_id='nba:espn:999', forecast_id='')
        bad = replace(bad, forecast_id='fc-' + bad.content_hash()[:24])
        result = store.put_many(con, [bad, fc])
        assert result['invalid'] == 1 and result['duplicate'] == 1
        assert store.get(con, fc.forecast_id) == fc
    finally:
        con.close()
