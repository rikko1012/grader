"""LoL must never touch CS2: no LoL module imports a CS2 module, no CS2 module imports LoL, and LoL keeps its data in
its own files."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOL = ROOT / "lol"


def _imports(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_lol_imports_no_cs2_module():
    cs2 = {p.stem for p in ROOT.glob("*.py")} | {"cs2api", "ml_models"}
    for p in LOL.rglob("*.py"):
        assert not (_imports(p) & cs2), f"{p.relative_to(ROOT)} imports a CS2 module: {_imports(p) & cs2}"


def test_cs2_imports_no_lol():
    for p in list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py")):
        if p.name == "lol_scout.py":                 # the LoL launcher itself
            continue
        assert "lol" not in _imports(p), f"{p.name} imports lol"


def test_lol_data_is_its_own(monkeypatch, tmp_path):
    import importlib
    from lol import config
    monkeypatch.delenv("LOL_DATA_DIR", raising=False)
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    try:
        importlib.reload(config)
        assert config.DATA_DIR == tmp_path / "lol"
        assert config.CACHE_DB.name == "cache.db" and config.CACHE_DB.parent == tmp_path / "lol"
    finally:
        monkeypatch.undo()
        importlib.reload(config)
