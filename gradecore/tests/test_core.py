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
    for p in list(ROOT.glob("*.py")) + list((ROOT / "sports").rglob("*.py")) + list((ROOT / "lol").rglob("*.py")):
        if 'tests' in p.parts:
            continue
        if 'gradecore' in _imports(p):
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
