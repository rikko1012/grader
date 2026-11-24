import json
from types import SimpleNamespace

from sports import api_model, config, web


def request():
    return SimpleNamespace(rest='comparisons', query={'path': '../../secrets'})


def test_saved_comparisons_read_exact_reports_without_changing_files():
    folder = config.DATA_DIR / 'model'
    folder.mkdir(parents=True, exist_ok=True)
    document = {'markets': {'points': {'history_part_passed': True}}, 'games_at_trigger': 229}
    target = folder / 'l2_nba.json'
    target.write_text(json.dumps(document), encoding='utf-8')
    before = target.read_bytes(), target.stat().st_mtime_ns
    code, result = api_model.admin_model(request())
    assert code == 200
    assert result['reports']['nba_sm2_l2']['report'] == document
    assert result['reports']['nfl_sm2_l2']['state'] == 'not_scored'
    assert len(result['reports']) == 4
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert 'release decision' in result['note']


def test_reports_refresh_after_file_is_published():
    assert api_model.saved_comparisons()['reports']['nba_sm2_l2']['state'] == 'not_scored'
    folder = config.DATA_DIR / 'model'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'l2_nba.json').write_text('{"games_at_trigger":229}')
    assert api_model.saved_comparisons()['reports']['nba_sm2_l2']['report']['games_at_trigger'] == 229


def test_bad_or_oversized_report_cannot_break_other_reports(monkeypatch):
    folder = config.DATA_DIR / 'model'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'l2_nba.json').write_text('private-invalid-content')
    (folder / 'l2_nfl.json').write_text(' ' * 33)
    (folder / 'heldout_nflv2.json').write_text('[]')
    (folder / 'heldout_sn1.json').write_text('{"decision":{}}')
    monkeypatch.setattr(api_model, 'COMPARISON_MAX_BYTES', 32)
    body = api_model.saved_comparisons()
    assert body['reports']['nba_sm2_l2']['state'] == 'report_unavailable'
    assert body['reports']['nfl_sm2_l2']['state'] == 'report_too_large'
    assert body['reports']['nfl_v2_h2']['state'] == 'report_unavailable'
    assert body['reports']['nfl_sn1']['state'] == 'available'
    assert 'private-invalid-content' not in json.dumps(body)


def test_comparison_route_retains_admin_get_only_access():
    handler, role, methods = web.ROUTES['/api/sports/admin/model/*']
    assert role == web.ADMIN and methods == ('GET',)
    assert handler == 'sports.api_model:admin_model'
    assert api_model.model(request())[0] == 404
