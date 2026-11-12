"""Slate offer lookup: index-bounded work, identical current revisions and provenance."""
from dataclasses import replace

import pytest
from sports import snapshots, store
from sports.tests import helpers as h
from sports.tests.test_line_correctness import at, t


def old_query(con, ids, as_of):
    inside = ','.join('?' * len(ids)) or 'NULL'
    return snapshots._latest(con, 'offer',
        "league = ? AND (source, source_key) IN (SELECT source, source_key FROM records WHERE "
        f"kind = 'offer' AND league = ? AND event_id IN ({inside}))", ['nba', 'nba', *ids], as_of)


@pytest.mark.parametrize('as_of', [t(5), t(15), t(25), t(45)])
def test_slate_identity_lookup_preserves_revisions_and_metadata(as_of):
    con = store.connect()
    base = h.offer()
    for oid in ('line', 'withdraw', 'remap', 'late', 'future'):
        store.put(con, replace(base, offer_id=oid))
    store.put(con, at(replace(base, offer_id='line', line=21.5), 10))
    store.put(con, at(replace(base, offer_id='line', line=20.5), 20))
    store.put(con, at(replace(base, offer_id='withdraw', availability='removed'), 10))
    store.put(con, at(replace(base, offer_id='remap', event_id='nba:espn:other'), 10))
    store.put(con, at(replace(base, offer_id='late', line=22.5), 20))
    late = store.put(con, at(replace(base, offer_id='late', line=19.5), 10))
    assert late['status'] == 'late'
    store.put(con, at(replace(base, offer_id='future', line=23.5), 40))
    before = con.total_changes
    ids = [base.event_id]
    actual = snapshots._slate_offers(con, 'nba', ids, as_of)
    expected = old_query(con, ids, as_of)
    assert sorted(actual, key=lambda r: r[0]) == sorted(expected, key=lambda r: r[0])
    assert con.total_changes == before
    assert snapshots._slate_offers(con, 'nba', [], as_of) == []
    con.close()


def test_lookup_work_does_not_scan_unrelated_league_history():
    con = store.connect()
    store.put(con, h.offer(oid='current'))
    for i in range(2000):
        store.put(con, h.offer(oid=f'past-{i}', event_id='nba:espn:past'))
    def measured(fn):
        steps = [0]
        def progress():
            steps[0] += 1
            return 0
        con.set_progress_handler(progress, 1)
        try:
            rows = fn()
        finally:
            con.set_progress_handler(None, 0)
        return rows, steps[0]
    ids = ['nba:espn:1']
    old, old_steps = measured(lambda: old_query(con, ids, t(30)))
    new, new_steps = measured(lambda: snapshots._slate_offers(con, 'nba', ids, t(30)))
    assert new == old
    assert new_steps * 10 < old_steps, (new_steps, old_steps)
    con.close()
