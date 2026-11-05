"""python -m gradecore: evaluate / reliability / cs2-local write artifacts to one folder, never release, never overwrite
silently, and read a picks.db copy read-only. The real-world empty case (a copy saved before raw distributions were
logged) produces a report that says so instead of invented bins."""
import json
import sqlite3
from pathlib import Path

import pytest

from gradecore import __main__ as cli
from gradecore.tests.test_evaluation import row

FIT_END, CAL_END = '2026-02-01T00:00:00Z', '2026-03-01T00:00:00Z'


def rows():
    fit = [row(i) for i in range(320)]
    cal = [{**row(i + 320), 'forecast_at': '2026-02-02T00:00:00Z', 'start_at': '2026-02-03T00:00:00Z',
            'settled_at': '2026-02-04T00:00:00Z'} for i in range(320)]
    test = [{**row(i + 640), 'forecast_at': '2026-03-02T00:00:00Z', 'start_at': '2026-03-03T00:00:00Z',
             'settled_at': '2026-03-04T00:00:00Z'} for i in range(520)]
    return fit + cal + test


def write_rows(path, rs):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rs), encoding='utf-8')
    return str(path)


def test_evaluate_writes_artifacts_never_release_and_refuses_a_silent_overwrite(tmp_path, capsys, monkeypatch):
    rp = write_rows(tmp_path / 'rows.jsonl', rows())
    out = tmp_path / 'art'
    argv = ['evaluate', '--rows', rp, '--fit-end', FIT_END, '--calibration-end', CAL_END, '--reps', '40', '--out', str(out)]
    assert cli.main(argv) == 0
    cal = json.loads((out / 'calibration_nba.json').read_text(encoding='utf-8'))
    assert cal['sport'] == 'nba' and cal['release_allowed'] is False and 'tables' in cal and cal['policy'] == 'gp-3'
    assert not (out / 'calibration_cs2.json').exists()                       # only sports present in the rows
    [report_path] = out.glob('evaluation_*.json')
    rep = json.loads(report_path.read_text(encoding='utf-8'))
    assert rep['release_allowed'] is False and rep['comparison']['nba']['release_allowed'] is False
    assert rep['counts'] == {'fit': 320, 'calibration': 320, 'test': 520, 'purged': 0}
    assert rep['artifacts_written'] == [str(out / 'calibration_nba.json')]
    summary = json.loads(capsys.readouterr().out)
    assert summary['release_allowed'] is False and summary['comparison']['nba']['unique_forecasts'] == 520
    with pytest.raises(SystemExit):
        cli.main(argv)                                                          # the artifact exists
    assert cli.main(argv + ['--replace']) == 0
    monkeypatch.setenv('SCOUT_GRADECORE_ARTIFACTS', str(tmp_path / 'env'))      # the env folder is the default
    assert cli.main(argv[:-2]) == 0 and (tmp_path / 'env' / 'calibration_nba.json').exists()
    monkeypatch.delenv('SCOUT_GRADECORE_ARTIFACTS')
    with pytest.raises(SystemExit):
        cli.main(argv[:-2])


def test_reliability_report_json_and_md_apply_the_three_point_rule_and_handle_no_rows(tmp_path, capsys):
    rp = write_rows(tmp_path / 'rows.jsonl', rows())
    base = ['reliability', '--rows', rp, '--fit-end', FIT_END, '--calibration-end', CAL_END, '--reps', '40']
    assert cli.main(base + ['--sport', 'nba', '--out', str(tmp_path)]) == 0
    [js] = tmp_path.glob('reliability_nba_*.json')
    [md] = tmp_path.glob('reliability_nba_*.md')
    doc = json.loads(js.read_text(encoding='utf-8'))
    assert doc['release_allowed'] is False and doc['held_out']['decided'] == 520 and doc['bins']
    assert doc['bins_within_max_gap'] == all(abs(b['gap']) <= .03 for b in doc['bins'])
    assert doc['counts']['test'] == 520 and doc['published']['n'] == 520 and doc['candidate']['n'] == 520
    text = md.read_text(encoding='utf-8')
    assert 'release_allowed: false' in text and '| bin |' in text and 'never permission' in text
    printed = json.loads(capsys.readouterr().out)
    assert printed['release_allowed'] is False and printed['held_out'] == 520
    # the real-world empty case: no rows for the sport -> a report that says so, no bins, never a pass
    empty = cli.reliability_report([], 'cs2', FIT_END, CAL_END, reps=40)
    assert empty['bins'] == [] and empty['bins_within_max_gap'] is False and empty['release_allowed'] is False
    assert empty['verdict'].startswith('not evaluable') and empty['counts'] == {'fit': 0, 'calibration': 0, 'test': 0, 'purged': 0}
    assert cli.main(base + ['--sport', 'cs2', '--out', str(tmp_path / 'empty')]) == 0
    [ejs] = (tmp_path / 'empty').glob('reliability_cs2_*.json')
    assert json.loads(ejs.read_text(encoding='utf-8'))['rows_in'] == 0
    assert '(no held-out bins)' in next((tmp_path / 'empty').glob('*.md')).read_text(encoding='utf-8')
    # a gap over three points fails the rule
    doc2 = dict(empty, bins=[{'gap': .031}])
    assert not all(abs(b['gap']) <= cli.MAX_GAP for b in doc2['bins'])


PICKS = ("CREATE TABLE picks (id INTEGER PRIMARY KEY, test INTEGER, status TEXT, settled_at TEXT, match_slug TEXT, slug TEXT, "
         "prop TEXT, scope TEXT, book TEXT, line REAL, actual REAL)")
PICK = (1, 2, 'settled', '2026-11-02T20:00:00Z', 'e', 'p', 'kills', '12', 'underdog', 20.5, 21)


def test_cs2_local_reads_a_copy_read_only_and_reports_a_copy_without_raw_columns(tmp_path, capsys):
    db = tmp_path / 'picks.db'
    con = sqlite3.connect(db)
    con.execute(PICKS)
    con.execute("CREATE TABLE forecasts (id INTEGER PRIMARY KEY, pick_id INTEGER, model_version TEXT, logged_at TEXT, "
                "match_start TEXT, line REAL, pick TEXT, p_pick REAL, probs_raw TEXT, quote TEXT)")
    con.execute("INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)", PICK)
    con.execute("INSERT INTO forecasts VALUES (1,1,'v','2026-11-02T10:00:00Z','2026-11-02T12:00:00Z',20.5,'More',.62,?,?)",
                (json.dumps({'over': .6, 'under': .4, 'push': 0}),
                 json.dumps({'price_over': 1.8, 'price_under': 2.05, 'updated_at': '2026-11-02T09:50:00Z'})))
    con.execute("INSERT INTO forecasts VALUES (2,1,'other','2026-11-02T10:00:00Z','2026-11-02T12:00:00Z',20.5,'More',.62,NULL,NULL)")
    con.commit()
    con.close()
    before = db.read_bytes()
    assert cli.main(['cs2-local', '--db', str(db), '--list']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed['has_probs_raw'] and listed['has_quote'] and {v['model_version'] for v in listed['model_versions']} == {'v', 'other'}
    outp = tmp_path / 'rows.jsonl'
    assert cli.main(['cs2-local', '--db', str(db), '--model-version', 'v', '--rows-out', str(outp)]) == 0
    got = [json.loads(l) for l in outp.read_text(encoding='utf-8').splitlines()]
    assert len(got) == 1 and got[0]['status'] == 'win' and got[0]['market_p'] == pytest.approx((1 / 1.8) / ((1 / 1.8) + (1 / 2.05)))
    sel = json.loads(Path(str(outp) + '.selection.json').read_text(encoding='utf-8'))
    assert sel['selected'] == 1 and sel['columns_missing'] == [] and sel['excluded'] == {'other_model': 1}
    assert sel['real_settled_picks'] == 1 and sel['release_allowed'] is False and sel['read_only'] is True
    assert db.read_bytes() == before
    with pytest.raises(SystemExit):
        cli.main(['cs2-local', '--db', str(db), '--model-version', 'v', '--rows-out', str(outp)])   # never overwritten
    with pytest.raises(SystemExit):
        cli.main(['cs2-local', '--db', str(db)])                                                   # version or --list
    # a copy saved before probs_raw / quote existed (the only local CS2 copy on 2026-11-04): 0 eligible, the reason counted
    old = tmp_path / 'old.db'
    con = sqlite3.connect(old)
    con.execute(PICKS)
    con.execute("CREATE TABLE forecasts (id INTEGER PRIMARY KEY, pick_id INTEGER, model_version TEXT, logged_at TEXT, "
                "match_start TEXT, line REAL, pick TEXT, p_pick REAL)")
    con.execute("INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?)", PICK)
    con.execute("INSERT INTO forecasts VALUES (1,1,'v','2026-11-02T10:00:00Z','2026-11-02T12:00:00Z',20.5,'More',.62)")
    con.commit()
    con.close()
    got2, rep2 = cli.cs2_local_rows(old, 'v')
    assert got2 == [] and rep2['columns_missing'] == ['probs_raw', 'quote'] and rep2['excluded'] == {'no_raw_probability': 1}
    assert rep2['real_settled_picks'] == 1 and rep2['forecasts_in_copy'] == 1 and rep2['release_allowed'] is False
