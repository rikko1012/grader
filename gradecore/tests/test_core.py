import ast
from pathlib import Path

import pytest

from gradecore import GRADE_POLICY_VERSION, contracts, registry

CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parent


def _imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


class Fake:
    sport = "cs2"

    def offers(self, as_of): return []
    def predict(self, offer): return contracts.Prediction(0.5, 0.4, 0.1)
    def market(self, offer): return None
    def flags(self, offer): return set()
    def settle(self, offer): return None
    def match_key(self, offer): return "m"
    def team_key(self, offer): return "t"
    def correlation(self, a, b): return None


def test_core_imports_no_sport_code():
    banned = {p.stem for p in ROOT.glob("*.py")} | {"sports", "lol", "cs2api", "ml_models"}
    for p in CORE.rglob("*.py"):
        if "tests" not in p.parts:
            assert not (_imports(p) & banned), p.name


def test_only_explicit_forecast_capture_boundaries_import_core():
    allowed = {'picklog.py', 'lol/center/forecast.py', 'sports/model/forward.py', 'sports/market_tools.py',
               'workspace_api.py', 'sports/api_model.py'}
    reporting = {'results_stated.py', 'lol/center/learning.py', 'sports/results_public.py'}
    scoring = {'public_record.py'}
    pricing = {'workspace_markets.py'}
    # ec-1 (owner 2026-11-05): the printed CS2 / LoL chance = the calibrated one. These read ONLY gradecore.chance_cal (the
    # committed tables); the projection, the pick log and every logged forecast stay the model's own.
    calibration = {'esports_cal.py', 'lol/cal.py', 'lol/center/forecast.py'}
    for p in list(ROOT.glob("*.py")) + list((ROOT / "sports").rglob("*.py")) + list((ROOT / "lol").rglob("*.py")):
        if 'tests' in p.parts:
            continue
        if 'gradecore' in _imports(p) and p.relative_to(ROOT).as_posix() in calibration:
            tree = ast.parse(p.read_text(encoding='utf-8'))
            nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')
                     and any(a.name == 'chance_cal' for a in n.names)]
            assert nodes and all(n.module == 'gradecore' and [a.name for a in n.names] == ['chance_cal'] for n in nodes), p.name
            if p.name != 'forecast.py':          # the forecast writer is also a capture boundary (checked below)
                others = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')
                          and not any(a.name == 'chance_cal' for a in n.names)]
                assert not others, p.name
                continue
        if 'gradecore' in _imports(p):
            if p.relative_to(ROOT).as_posix() == 'sports/expansion_forecasts.py':
                # Owner 2026-11-17: new isolated sports publish model-estimate
                # letters through the shared policy, without importing capture,
                # calibration artifacts or models belonging to another sport.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and [a.name for a in n.names] == ['policy'] for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() == 'workspace_game_books.py':
                # The member Moneylines read path can select a research winner. It has no stake or letter imports.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and [a.name for a in n.names] == ['winner'] for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() in pricing:
                # Phase 4 Tools sheet: prices book quotes (market_tools, devig) and reads containment's state; never grades or stakes.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and {a.name for a in n.names} <= {'market_tools', 'devig', 'containment', 'book_rules'} for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() == 'workspace_auto.py':
                # auto EV / arbitrage slips (owner 2026-11-20): price freshness, settlement rules and market tools only;
                # never grades, logs or stakes (slips come from workspace_ev / workspace_api's generator).
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and {a.name for a in n.names} <= {'fair', 'book_rules', 'market_tools'} for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() == 'workspace_ev.py':
                # SCOUT_PICKEM_EV board: prices pick'em legs (fair, line shift, verified payouts); never grades, logs or stakes.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module in ('gradecore', 'gradecore.fair') and {a.name for a in n.names} <= {'ev_board', 'fair', 'line_shift', 'payouts', 'Quote', 'book_rules'}
                                     for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() == 'workspace_market_edge.py':
                # SCOUT_MARKET_EDGE (owner site audit 2026-11-06): the books' fair chance (via workspace_ev) against the verified
                # 2-pick break-even, and the best line per side; display only, never grades, logs or stakes.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and {a.name for a in n.names} <= {'ev_board', 'fair', 'payouts'} for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() == 'board_parity.py':
                # SCOUT_PARITY_* board parity (4.16): display numbers from gradecore.parity (and slip's unit share); never grades, logs or stakes.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and [a.name for a in n.names] in (['parity'], ['slip']) for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() in scoring:
                # The public record scores saved results (bins, match-resampled ranges); it never grades or stakes.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and [a.name for a in n.names] == ['scorecard'] for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() == 'letter_containment.py':
                # lc-1 display containment: reads the shared containment rule and the payout table, never grades or stakes.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore' and [a.name for a in n.names] in (['containment'], ['payouts'], ['chance_cal'])
                                     for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names) for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() in ('sports/expansion_learning.py','sports/model/learning.py'):
                # Post-result point-error diagnostics only, not policy/calibration
                # or live grades. Keep this boundary narrower than a core import.
                tree = ast.parse(p.read_text(encoding='utf-8'))
                nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                         and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore.projection_review' and
                                     [a.name for a in n.names] == ['review'] for n in nodes)
                assert not any(isinstance(n,ast.Import) and any(a.name.startswith('gradecore') for a in n.names)
                               for n in ast.walk(tree))
                continue
            if p.relative_to(ROOT).as_posix() in reporting:
                # Shared calendar formatting cannot introduce live grading.
                nodes = [n for n in ast.walk(ast.parse(p.read_text(encoding='utf-8')))
                         if isinstance(n, ast.ImportFrom) and (n.module or '').startswith('gradecore')]
                assert nodes and all(n.module == 'gradecore.calendar' and
                                     [a.name for a in n.names] == ['daily_record'] for n in nodes)
                assert not any(isinstance(n, ast.Import) and any(a.name.startswith('gradecore') for a in n.names)
                               for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))))
                continue
            assert p.relative_to(ROOT).as_posix() in allowed, p.name
            if p.name not in ('market_tools.py', 'workspace_api.py', 'api_model.py'):
                assert 'SCOUT_GRADECORE_SHADOW' in p.read_text(encoding='utf-8'), p.name


def test_registry_holds_one_adapter_per_sport():
    registry.clear()
    a = registry.register(Fake())
    assert registry.get("cs2") is a and registry.sports() == ["cs2"]
    with pytest.raises(ValueError):
        registry.register(Fake())
    with pytest.raises(TypeError):
        registry.register(object())
    registry.clear()


def test_chances_must_add_up_and_no_push_rescales():
    p = contracts.Prediction(0.5, 0.4, 0.1)
    assert p.p_over_given_no_push == pytest.approx(5 / 9)
    with pytest.raises(ValueError):
        contracts.Prediction(0.5, 0.4, 0.0)


def test_version_is_set():
    assert GRADE_POLICY_VERSION


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), True, None, '0.5', -0.1, 1.1])
@pytest.mark.parametrize('field', ['p_over', 'p_under', 'p_push'])
def test_predictions_reject_non_probabilities(field, bad):
    kw = dict(p_over=.5, p_under=.4, p_push=.1)
    kw[field] = bad
    with pytest.raises(ValueError):
        contracts.Prediction(**kw)


def test_certain_push_has_no_conditional_chance():
    p = contracts.Prediction(0, 0, 1)
    assert p.p_over_given_no_push is None
    assert p.p_under_given_no_push is None


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, 'unknown'])
def test_projection_must_be_finite_or_explicitly_absent(bad):
    with pytest.raises(ValueError):
        contracts.Prediction(.6, .4, projection=bad)
