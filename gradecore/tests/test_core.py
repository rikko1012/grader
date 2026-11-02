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


def test_no_live_code_reads_the_skeleton_yet():
    for p in list(ROOT.glob("*.py")) + list((ROOT / "sports").rglob("*.py")) + list((ROOT / "lol").rglob("*.py")):
        assert "gradecore" not in _imports(p), p.name


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
