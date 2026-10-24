"""ux agent (card actions, slip tray, filters, My picks): node tests of the pure helpers + static checks of the page
wiring and CSS scope. Browser flows are in test_ui_browser.py (need the running preview)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_ux_helpers_in_node():
    script = Path(__file__).resolve().parent / "ui_ux_test.js"
    r = subprocess.run(["node", str(script), str(WEB)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout + r.stderr


def test_ux_assets_wired_and_scoped():
    page = (WEB.parent / "sports.html").read_text(encoding="utf-8")
    assert "/sports/ux.css" in page and (WEB / "ux.css").exists()
    for m in ("picks.js", "tray.js", "filters.js"):                                       # modules load before the app hooks run
        assert page.index("/sports/" + m) < page.index("/sports/app.js")
    css = re.sub(r"/\*.*?\*/", "", (WEB / "ux.css").read_text(encoding="utf-8"), flags=re.S)
    for rule in re.findall(r"(?:^|})\s*([^{}@][^{}]*)\{", css):
        for part in rule.strip().split(","):
            if part.strip() and not part.strip().startswith(("from", "to", "@")):
                assert part.strip().startswith(("body.sw", ".sw")), part


def test_card_actions_hook_replaces_four_buttons():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "function cardActions(" in app and "SportsPicks.actions" in app
    assert "data-track=" not in app and "data-leg=" not in app            # no "+ Over / + Under / Track Over / Track Under"
    js = (WEB / "picks.js").read_text(encoding="utf-8")
    assert "Add to slip" in js and "Track pick" in js and "/api/sports/picks/track" in js


def test_tray_uses_slip_studio_draft_keys():
    tray = (WEB / "tray.js").read_text(encoding="utf-8")
    slips = (WEB / "slips.js").read_text(encoding="utf-8")
    assert '"ss:draft:" + lg + ":" + pf' in tray and '"ss:draft:" + c.league + ":" + c.platform' in slips
    assert '"ss:ctx"' in tray and '"ss:ctx"' in slips
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "SportsTray.add(leg)" in app and "SportsTray.render(state.lg)" in app


def test_filters_hooks():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "SportsFilters.html(" in app and "SportsFilters.matches(" in app and "SportsFilters.bind(" in app
    assert "scout.sports.filters.v1" in app                                       # filters stay saved on the device
