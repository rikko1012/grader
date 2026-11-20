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


# ------------------------------------------------------------------------------------- forecasts.db / learning.db
from contextlib import closing  # noqa: E402

from sports import expansion_forecasts as fc, expansion_learning as learn, expansion_research as research  # noqa: E402
from sports.tests.test_expansion_learning import setup, result  # noqa: E402,F401
from sports.tests.test_expansion_forecasts import fixture  # noqa: E402

LATER = '2026-11-30T12:00:00Z'      # > 7 days after the fixture match


def _tennis_forecast(setup, monkeypatch):
    root, path, match, offer = setup
    offer.update(sport='tennis', variant='standard', player_id='native', event_id='native')
    _, facts = fixture('tennis')
    facts.update(identity={'competition': 'atp', 'event_id': match['event_id'], 'player_id': match['participants'][0]['id']},
                 scope='full_game', context={'best_of': 3},
                 event={'event_id': match['event_id'], 'start': offer['start'], 'status': 'scheduled'})
    for game in facts['games']:
        game.update(best_of=3, source='thescore')
    monkeypatch.setattr(board, 'board', lambda req: (200, {'offers': [offer]}))
    monkeypatch.setattr(research, 'saved', lambda sport: {'players': {research.key(offer): facts}})
    return offer, facts


def test_forecast_slimming_is_lossless_and_settle_still_reviews_corrections(setup, monkeypatch):
    _tennis_forecast(setup, monkeypatch)
    assert fc.publish('tennis', '2026-11-17T23:00:00Z')['published'] == 1
    with fc.connect('tennis') as c:
        original = tuple(c.execute('SELECT * FROM forecasts').fetchone())
    result(setup, 10, '2026-11-18T18:00:00Z')
    assert fc.settle('tennis', '2026-11-18T18:30:00Z')['added'] == 1
    out = fc.compact('tennis', LATER, budget_s=10)
    assert out['rows'] == 1 and out['parts'] == 1
    with fc.connect('tennis') as c:
        slim = json.loads(c.execute('SELECT payload FROM forecasts').fetchone()[0])
        back = list(xa.read(c, setup[0] / 'archive' / 'forecasts', 'forecasts'))
        assert xa.verify(c, setup[0] / 'archive' / 'forecasts')['bad'] == []
    full = json.loads(original[5])
    assert 'inputs' in full and 'inputs' not in slim and slim['archived'].startswith('forecasts-')
    assert all(slim[k] == full[k] for k in fc.SLIM_REQUIRED if k in full)
    assert tuple(back[0][c] for c in ('id', 'offer_key', 'competition', 'event_id', 'start', 'payload')) == original
    # a correction after slimming is still reviewed from the slim row
    result(setup, 3, '2026-11-18T19:00:00Z')
    assert fc.settle('tennis', '2026-11-30T12:30:00Z')['added'] == 1
    assert fc.compact('tennis', LATER, budget_s=10)['rows'] == 0          # never slimmed twice


def test_forecast_not_slimmed_before_its_event_was_checked_or_inside_7_days(setup, monkeypatch):
    _tennis_forecast(setup, monkeypatch)
    fc.publish('tennis', '2026-11-17T23:00:00Z')
    assert fc.compact('tennis', LATER, budget_s=10)['rows'] == 0           # never checked
    result(setup, 10, '2026-11-18T18:00:00Z')
    fc.settle('tennis', '2026-11-18T18:30:00Z')
    assert fc.compact('tennis', '2026-11-20T12:00:00Z', budget_s=10)['rows'] == 0   # inside 7 days


def test_learning_slimming_keeps_residual_fit_identical(setup):
    learn.cycle('tennis', '2026-11-17T21:00:00Z')
    result(setup)
    learn.cycle('tennis', '2026-11-18T18:00:00Z')
    identity = {'competition': 'atp', 'event_id': 'atp:espn:atp:tour:next'}
    with closing(learn.connect('tennis')) as c:
        before = learn.residual_fit(c, identity, 'aces', 'full_game', '2026-11-19T00:00:00Z', 6)
        n = c.execute('SELECT COUNT(*) FROM predictions').fetchone()[0]
        full = c.execute('SELECT payload FROM predictions').fetchone()[0]
    out = learn.compact('tennis', LATER, budget_s=10)
    assert out['rows'] == n == 1
    with closing(learn.connect('tennis')) as c:
        after = learn.residual_fit(c, identity, 'aces', 'full_game', '2026-11-19T00:00:00Z', 6)
        slim = json.loads(c.execute('SELECT payload FROM predictions').fetchone()[0])
        back = list(xa.read(c, setup[0] / 'archive' / 'learning', 'predictions'))
    assert after == before and before['player_events'] == 1
    assert back[0]['payload'] == full and 'history' not in slim
    assert slim['baseline'] == json.loads(full)['baseline']
    # a correction after slimming is reviewed from the slim row (baseline / candidate / line_for_review kept)
    result(setup, 3, '2026-11-30T13:00:00Z')
    with closing(learn.connect('tennis')) as c:
        assert learn.settle_reviews(c, 'tennis', '2026-11-30T13:30:00Z', 1e12)['added'] == 1


def test_cycle_steps_are_isolated_publish_failure_does_not_stop_learning(setup, monkeypatch):
    monkeypatch.setenv('SPORTS_FORECAST', '1')

    def broken(*a, **k):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(fc, 'publish', broken)
    out = learn.cycle('tennis', '2026-11-17T21:00:00Z')
    assert 'disk I/O error' in out['published_model']['error']
    assert out['forecasts']['saved'] == 1 and out['storage']['learning']['live_mb'] is not None


def test_learning_capture_stops_at_its_cap_but_never_raises(setup, monkeypatch):
    monkeypatch.setattr(learn, 'MAX_BYTES', 0)
    out = learn.cycle('tennis', '2026-11-17T21:00:00Z')
    assert out['forecasts'] == {'saved': 0, 'reason': 'storage_full'}


def test_forecast_store_hard_stop_publishes_saved_forecasts_only(setup, monkeypatch):
    offer, _ = _tennis_forecast(setup, monkeypatch)
    assert fc.publish('tennis', '2026-11-17T23:00:00Z')['published'] == 1
    monkeypatch.setattr(fc, 'HARD_BYTES', 0)
    assert fc.publish('tennis', '2026-11-17T23:01:00Z')['published'] == 1   # already saved: still published
    offer['revision'] = 'moved'
    out = fc.publish('tennis', '2026-11-17T23:02:00Z')
    assert out['published'] == 0 and out['pending']['storage_full'] == 1


def test_publish_cut_by_budget_carries_previous_grades_of_current_offers(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    offer, facts = fixture()
    second = dict(offer, offer_id='o2', start='2026-11-18T19:00:00Z')
    monkeypatch.setattr(board, 'board', lambda req: (200, {'offers': [offer, second]}))
    monkeypatch.setattr(research, 'saved', lambda sport: {'players': {research.key(offer): facts,
                                                                      research.key(second): facts}})
    full = fc.publish('soccer', '2026-11-17T23:00:00Z')
    assert full['published'] == 2
    saved = json.loads((tmp_path / 'soccer' / 'forecasts.json').read_text(encoding='utf-8'))['forecasts']
    ticks = {'n': 0}

    def clock():                          # deadline at 5; first offer at t=0, the second check at t=100
        ticks['n'] += 1
        return 0 if ticks['n'] <= 2 else 100
    monkeypatch.setattr(fc.time, 'monotonic', clock)
    out = fc.publish('soccer', '2026-11-17T23:01:00Z', budget_s=5)
    assert out['pending'].get('budget') == 1 and out['pending']['carried'] == 1 and out['published'] == 2
    again = json.loads((tmp_path / 'soccer' / 'forecasts.json').read_text(encoding='utf-8'))['forecasts']
    assert again == saved


def test_fit_publication_drops_furthest_starts_only_over_the_limit():
    cur = {f'k{i}': {'start': f'2026-11-{18 + i}T00:00:00Z', 'x': 'y' * 100} for i in range(5)}
    assert fc.fit_publication(cur) is cur
    small = fc.fit_publication(cur, limit=400)
    assert 'k4' not in small and 'k0' in small and list(small) == sorted(small)


# ------------------------------------------------------------------------------------------------- compaction
import dataclasses  # noqa: E402
from datetime import timedelta  # noqa: E402

from sports import archive, expansion_compact, store  # noqa: E402
from sports.contracts import ContextSnapshot, StatObservation  # noqa: E402
from sports.tests import test_expansion_storage_1119 as st1119  # noqa: E402


def _reread(path, recs, at):
    """The 24 h re-read of every final: the same content, observed again `at` (what parse_summary produces)."""
    c = store.connect(path)
    try:
        again = [dataclasses.replace(r, observed_at=st1119._iso(at), known_at=st1119._iso(at))
                 for r in recs if isinstance(r, (StatObservation, ContextSnapshot))]
        res = store.put_many(c, again)
    finally:
        c.close()
    return res


def test_unchanged_daily_rereads_no_longer_keep_finals_hot(tmp_path):
    """Root cause of 'compaction archives almost nothing': each unchanged re-read moved last_seen_at, and coldness
    used max(known_at, last_seen_at) - an event re-read every 24 h never became cold. Coldness is on content now."""
    path = tmp_path / 'soccer' / 'eng.1' / 'canonical.db'
    recs = st1119._expansion_store(path)
    _reread(path, recs, st1119.NOW - timedelta(hours=3))
    con = sqlite3.connect(path)
    seen = con.execute("SELECT MAX(last_seen_at), MAX(known_at) FROM records WHERE kind='stat'").fetchone()
    con.close()
    assert seen[0] > seen[1]                                     # the re-read moved last_seen_at, not known_at
    before = st1119._reads(path)
    out = expansion_compact.compact(path, 60, now=st1119.NOW)
    assert out['error'] is None and out['rows'] == 5 * 8 * 4     # the old rule archived 0 here
    assert st1119._reads(path) == before
    # the next daily re-read of the archived finals writes no new revision
    _reread(path, recs, st1119.NOW + timedelta(hours=21))
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM records WHERE kind IN ('stat','context')").fetchone()[0] == 8 * 4
    con.close()

    def content(reads):                 # a hot row's re-read moves its sighting counters, nothing else
        strip = ('last_seen_at', 'seen_count', 'last_source_updated_at')
        return {k: [{c: v for c, v in r.items() if c not in strip} for r in rows] if isinstance(rows, list)
                and rows and isinstance(rows[0], dict) and 'rid' in rows[0] else rows for k, rows in reads.items()}
    assert content(st1119._reads(path)) == content(before)


def test_a_correction_learned_inside_48h_keeps_its_event_hot(tmp_path):
    path = tmp_path / 'soccer' / 'eng.1' / 'canonical.db'
    recs = st1119._expansion_store(path)
    old = next(r for r in recs if isinstance(r, StatObservation) and r.event_id.endswith(':900:900'))
    c = store.connect(path)
    store.put(c, dataclasses.replace(old, value=old.value + 1, known_at=st1119._iso(st1119.NOW - timedelta(hours=5)),
                                     observed_at=st1119._iso(st1119.NOW - timedelta(hours=5))))
    c.close()
    out = expansion_compact.compact(path, 60, now=st1119.NOW)
    assert out['rows'] == 4 * 8 * 4                              # event 900 stays hot for its correction window


def test_service_cache_share_for_expansion_archives_leaves_the_global_lru(tmp_path, monkeypatch):
    """Member history reads in the web process: archived soccer / tennis files get their own small share."""
    files = archive._Files()
    monkeypatch.setattr(archive, 'PREFIX_CAPS', {})
    exp_dir = tmp_path / 'expansion'
    for d in ('expansion/soccer/eng.1', 'nba'):
        (tmp_path / d / 'archive' / 'records').mkdir(parents=True)
    blob = lzma.compress(b'{"a":1}\n' * 20000)             # ~160 KB raw
    for i in range(6):
        (tmp_path / 'expansion/soccer/eng.1/archive/records' / f'f{i}').write_bytes(blob)
        (tmp_path / 'nba/archive/records' / f'f{i}').write_bytes(blob)
    exp_db, nba_db = str(tmp_path / 'expansion/soccer/eng.1/canonical.db'), str(tmp_path / 'nba/canonical.db')
    monkeypatch.setattr(archive, 'LRU_BYTES', 10_000_000)
    monkeypatch.setattr(expansion_compact, 'EXPANSION_LRU_MB', 0.4)
    expansion_compact.bound_service_cache(exp_dir)
    for i in range(6):
        files.rows(nba_db, i, lambda f: f'f{f}')
        files.rows(exp_db, i, lambda f: f'f{f}')
    mine = [k for k in files.lru if k[0] == exp_db]
    nba = [k for k in files.lru if k[0] == nba_db]
    assert len(nba) == 6                                      # the NBA / NFL share is untouched
    assert 1 <= len(mine) <= 3 and sum(files.lru[k][1] for k in mine) <= 400_000 + 160_000
    assert files.size == sum(v[1] for v in files.lru.values())


def test_cgroup_reports_anon_file_and_pressure(tmp_path, monkeypatch):
    from sports import service_life
    (tmp_path / 'memory.current').write_text('2009400000\n')
    (tmp_path / 'memory.max').write_text('2147500000\n')
    (tmp_path / 'memory.stat').write_text('anon 800000000\nfile 1100000000\nfile_dirty 2000000\nsock 0\n')
    (tmp_path / 'memory.pressure').write_text('some avg10=0.10 avg60=0.25 avg300=0.05 total=123\n'
                                              'full avg10=0.00 avg60=0.01 avg300=0.00 total=9\n')
    monkeypatch.setattr(service_life, 'CGROUP', str(tmp_path))
    out = service_life.cgroup()
    assert out['stat_mb'] == {'anon': 800.0, 'file': 1100.0, 'file_dirty': 2.0}
    assert out['pressure']['some']['avg60'] == 0.25 and 'total' not in out['pressure']['full']
    assert out['current_mb'] == 2009.4


def test_net_max_bytes_refuses_a_huge_answer_without_holding_it(monkeypatch):
    import importlib.util
    spec = importlib.util.find_spec('sports.net')            # a fresh copy: conftest replaces sports.net.get offline
    net = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(net)

    class R:
        status_code, text = 200, ''

        def __init__(self, size, declared=None):
            self.size, self.headers, self.read = size, ({'Content-Length': str(declared)} if declared else {}), 0

        def iter_content(self, n):
            while self.read < self.size:
                self.read += n
                yield b'x' * n

        def close(self):
            pass

        def json(self):
            return json.loads(self._content)

    sent = []

    class S:
        def get(self, url, **kw):
            sent.append(kw.get('stream'))
            return resp

    monkeypatch.setattr(net, 'session', lambda: S())
    monkeypatch.setattr(net, '_wait', lambda host: None)
    resp = R(10 * 65536)
    with pytest.raises(ValueError, match='too large'):
        net.get('https://example.invalid/a', max_bytes=100_000)
    assert resp.read <= 2 * 65536 and sent == [True]          # stopped after the limit, never read whole
    resp = R(0, declared=10**9)
    with pytest.raises(ValueError, match='too large'):
        net.get('https://example.invalid/b', max_bytes=100_000)
    assert resp.read == 0
    resp = R(1)
    resp.iter_content = lambda n: iter([b'{"ok": 1}'])
    assert net.get_json('https://example.invalid/c', max_bytes=100) == {'ok': 1}
    sent.clear()
    resp = R(1)
    resp._content = b'{"x": 2}'
    assert net.get_json('https://example.invalid/d') == {'x': 2} and sent == [None]   # default: unchanged call
