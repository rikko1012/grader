"""sports must never touch CS2 or LoL: no sports module imports a CS2 / LoL module, no CS2 / LoL module imports sports,
and sports keeps its data in its own files."""
import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPORTS = ROOT / "sports"


def _imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_sports_imports_no_cs2_or_lol_module():
    other = {p.stem for p in ROOT.glob("*.py")} | {"cs2api", "ml_models", "lol"}
    for p in SPORTS.rglob("*.py"):
        assert not (_imports(p) & other), f"{p.relative_to(ROOT)} imports {_imports(p) & other}"


def test_cs2_and_lol_import_no_sports():
    files = list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py")) + list((ROOT / "lol").rglob("*.py"))
    for p in files:
        if p.name == "sports_scout.py":                  # the sports launcher itself
            continue
        assert "sports" not in _imports(p), f"{p.relative_to(ROOT)} imports sports"


def test_sports_data_is_its_own(monkeypatch, tmp_path):
    from sports import config
    monkeypatch.delenv("SPORTS_DATA_DIR", raising=False)
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    try:
        importlib.reload(config)
        assert config.DATA_DIR == tmp_path / "sports"
        assert config.CACHE_DB.parent == tmp_path / "sports" and config.FORECAST_DB.parent == tmp_path / "sports"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_production_needs_no_scipy_or_pandas():
    """Render installs requirements.txt only (requests, numpy, ...): the LoL hotfix d78bc1d was a scipy import."""
    for p in SPORTS.rglob("*.py"):
        if "tests" in p.parts:
            continue
        assert not (_imports(p) & {"scipy", "pandas", "pyarrow", "nba_api"}), p.name
