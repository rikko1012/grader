"""sg-stability (2026-11-20): soccer / tennis can never take the sports service down or lose data.

Quote history no longer darkens the board at its cap and moves cold revisions into verified archive files; learning /
forecast stores slim cold rows into verified archives; compaction judges coldness on content; the history job
checkpoints its catalog; scheduled archive verification; alerts."""
import json
import lzma
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from sports import expansion_archive as xa, expansion_board as board, expansion_offers as offers

NOW = '2026-11-17T12:00:00Z'


def pp(pid='o1', start='2026-11-18T12:00:00Z', line=2.5):
    return {'data': [{'type': 'projection', 'id': pid, 'attributes': {'stat_type': 'Shots', 'line_score': line,
        'start_time': start, 'status': 'pre_game', 'projection_type': 'Single Stat',
        'allowed_wager_types': 'over', 'odds_type': 'standard'}, 'relationships': {
        k: {'data': {'type': k, 'id': v}} for k, v in [('new_player', 'p1'), ('game', 'g1'), ('duration', 'd1')]}}],
        'included': [{'type': 'new_player', 'id': 'p1', 'attributes': {'league': 'SOCCER', 'name': 'Player', 'position': 'Forward'}},
                     {'type': 'duration', 'id': 'd1', 'attributes': {'name': 'Full'}},
                     {'type': 'game', 'id': 'g1', 'attributes': {}}]}


def row(pid='o1', start='2026-11-18T12:00:00Z', line=2.5, now=NOW):
    return offers.prizepicks(pp(pid, start, line), 'soccer', now)[0]


def _all_revisions(path):
    with sqlite3.connect(path) as con:
        return con.execute('SELECT id, platform, offer_id, revision, observed_at, payload FROM revisions ORDER BY id').fetchall()


# ------------------------------------------------------------------------------------------------ quote history
def test_board_written_even_when_quote_history_is_at_its_cap(tmp_path, monkeypatch):
    a = row()
    board.save('soccer', [a], {}, NOW, tmp_path)
    monkeypatch.setattr(board, 'QUOTES_CAP_MB', 0)          # any store is over a 0 MB cap
    b = row(line=3.5)
    snap = board.save('soccer', [b], {}, '2026-11-17T12:01:00Z', tmp_path)
    saved = json.loads((tmp_path / 'board.json').read_text(encoding='utf-8'))
    assert saved == json.loads(json.dumps(snap)) and saved['offers'][0]['line'] == 3.5
    st = json.loads((tmp_path / board.QUOTE_STATUS).read_text(encoding='utf-8'))
    assert st['state'] == 'paused_at_cap' and st['written'] == 0
    assert len(_all_revisions(tmp_path / 'quotes.db')) == 1  # skipped, never raised, never deleted


def test_board_json_bytes_unchanged_by_the_reorder(tmp_path):
    """Same snapshot bytes as before the change: the history write moved after the board write, nothing else."""
    a, b = row(), row('o2', line=1.5)
    snap = board.save('soccer', [a, b], {'prizepicks': {'ok': True}}, NOW, tmp_path)
    expect = json.dumps({"sport": "soccer", "built_at": NOW, "sources": {'prizepicks': {'ok': True}},
                         "offers": [a, b], "mode": "lines", "grading_ready": False, "game_odds": []},
                        separators=(",", ":"))
    assert (tmp_path / 'board.json').read_text(encoding='utf-8') == expect
    assert snap['offers'] == [a, b]


def test_quote_history_error_never_stops_the_board(tmp_path, monkeypatch):
    board.save('soccer', [row()], {}, NOW, tmp_path)
    def locked(db):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(board, '_history_connect', locked)
    snap = board.save('soccer', [row(line=4.5)], {}, '2026-11-17T12:02:00Z', tmp_path)
    assert json.loads((tmp_path / 'board.json').read_text(encoding='utf-8'))['offers'][0]['line'] == 4.5
    st = json.loads((tmp_path / board.QUOTE_STATUS).read_text(encoding='utf-8'))
    assert st['state'] == 'error' and 'locked' in st['error'] and snap


def _fill_history(tmp_path, n_old=30, n_new=5):
    """n_old offers whose revisions are 5 days old and whose matches started 4 days ago, plus n_new current ones."""
    old_t, old_start = '2026-11-12T12:00:00Z', '2026-11-13T12:00:00Z'
    rows_old = [offers.prizepicks(pp(f'old{i}', old_start, 1.5), 'soccer', old_t)[0] for i in range(n_old)]
    board.save('soccer', rows_old, {}, old_t, tmp_path)
    moved = [offers.prizepicks(pp(f'old{i}', old_start, 2.5), 'soccer', '2026-11-12T13:00:00Z')[0] for i in range(n_old)]
    board.save('soccer', moved, {}, '2026-11-12T13:00:00Z', tmp_path)
    cur = [row(f'new{i}') for i in range(n_new)]
    board.save('soccer', cur, {}, NOW, tmp_path)
    return cur


def test_archive_moves_cold_revisions_losslessly_and_keeps_current_offers_hot(tmp_path):
    cur = _fill_history(tmp_path)
    before = _all_revisions(tmp_path / 'quotes.db')
    assert len(before) == 65
    out = board.archive_quotes(tmp_path, {(r['platform'], r['offer_id']) for r in cur}, now=NOW, budget_s=30)
    assert out['rows'] == 60 and out['parts'] >= 1 and not out['pending']
    after = _all_revisions(tmp_path / 'quotes.db')
    assert [r[2] for r in after] == [f'new{i}' for i in range(5)]
    with sqlite3.connect(tmp_path / 'quotes.db') as con:
        back = list(xa.read(con, tmp_path / 'archive' / 'quotes', 'revisions'))
        v = xa.verify(con, tmp_path / 'archive' / 'quotes')
    cols = ('id', 'platform', 'offer_id', 'revision', 'observed_at', 'payload')
    restored = sorted([tuple(d[c] for c in cols) for d in back] + after)
    assert restored == sorted(before)                         # every column of every revision, nothing lost
    assert v['bad'] == [] and v['rows'] == 60


def test_archive_keeps_latest_revision_of_offers_that_may_still_be_offered(tmp_path):
    old_t = '2026-11-12T12:00:00Z'
    future = offers.prizepicks(pp('fut', '2026-11-30T12:00:00Z', 1.5), 'soccer', old_t)[0]  # unchanged for 5 days
    board.save('soccer', [future], {}, old_t, tmp_path)
    out = board.archive_quotes(tmp_path, set(), now=NOW, budget_s=30)   # rotated out of this run's tabs
    assert out['rows'] == 0 and len(_all_revisions(tmp_path / 'quotes.db')) == 1
    # the dedupe lookup still sees it: saving it again writes no duplicate revision
    board.save('soccer', [future], {}, NOW, tmp_path)
    assert len(_all_revisions(tmp_path / 'quotes.db')) == 1


def test_archive_failure_inside_the_transaction_leaves_every_row(tmp_path, monkeypatch):
    cur = _fill_history(tmp_path)
    before = _all_revisions(tmp_path / 'quotes.db')
    real = xa.archive_part

    def boom(con, adir, prefix, tbl, cols, rows, change_sql, params, **kw):
        return real(con, adir, prefix, tbl, cols, rows, change_sql + " AND 0", params, **kw)   # touches 0 rows
    monkeypatch.setattr(xa, 'archive_part', boom)
    with pytest.raises(RuntimeError, match='touched 0 rows'):
        board.archive_quotes(tmp_path, {(r['platform'], r['offer_id']) for r in cur}, now=NOW, budget_s=30)
    assert _all_revisions(tmp_path / 'quotes.db') == before
    assert not list((tmp_path / 'archive' / 'quotes').glob('*.xz'))
    with sqlite3.connect(tmp_path / 'quotes.db') as con:
        assert con.execute('SELECT COUNT(*) FROM exp_archive').fetchone()[0] == 0


def test_orphan_file_from_a_crash_is_removed_and_rows_archived_again(tmp_path):
    cur = _fill_history(tmp_path)
    adir = tmp_path / 'archive' / 'quotes'
    adir.mkdir(parents=True)
    (adir / 'quotes-000000.jsonl.xz').write_bytes(lzma.compress(b'{"id":1}\n'))   # renamed, never committed
    (adir / '.quotes-000001.jsonl.xz.tmp').write_bytes(b'partial')
    out = board.archive_quotes(tmp_path, {(r['platform'], r['offer_id']) for r in cur}, now=NOW, budget_s=30)
    assert out['leftovers_removed'] == 2 and out['rows'] == 60
    with sqlite3.connect(tmp_path / 'quotes.db') as con:
        assert xa.verify(con, adir)['bad'] == []


def test_archive_resumes_from_its_cursor_on_the_time_budget(tmp_path, monkeypatch):
    cur = _fill_history(tmp_path)
    monkeypatch.setattr(board, 'QUOTE_PART_ROWS', 10)
    out = board.archive_quotes(tmp_path, {(r['platform'], r['offer_id']) for r in cur}, now=NOW, budget_s=-1)
    assert out['pending'] and out['rows'] == 0 and out['cursor'] == 0
    total, cursor = 0, 0
    for _ in range(20):
        out = board.archive_quotes(tmp_path, {(r['platform'], r['offer_id']) for r in cur}, now=NOW, budget_s=30,
                                   cursor=cursor)
        total += out['rows']
        if not out['pending']:
            break
        cursor = out['cursor']
    assert total == 60


def test_live_bytes_reuses_freed_pages(tmp_path):
    cur = _fill_history(tmp_path, n_old=300)
    db = tmp_path / 'quotes.db'
    before_file, before_live = db.stat().st_size, xa.live_bytes(db)
    board.archive_quotes(tmp_path, {(r['platform'], r['offer_id']) for r in cur}, now=NOW, budget_s=30)
    assert db.stat().st_size == before_file                  # no VACUUM on the board path
    assert xa.live_bytes(db) < before_live / 2               # but the cap counts only live pages


def test_record_quote_history_runs_the_archive_hourly_and_saves_samples(tmp_path):
    cur = _fill_history(tmp_path)
    st = json.loads((tmp_path / board.QUOTE_STATUS).read_text(encoding='utf-8'))
    assert st['archive']['rows'] == 60 or st['archive'].get('ran_at_epoch')   # first run archives at once
    assert st['samples'] and st['live_mb'] is not None and st['cap_mb'] == board.QUOTES_CAP_MB
