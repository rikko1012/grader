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
    assert "HOOKS.renderActions(" in app and "hooks: HOOKS" in app                # one action area, via the shared hook
    assert "A.hooks.renderActions = renderActions" in (WEB / "picks.js").read_text(encoding="utf-8")
    assert "data-track=" not in app and "data-leg=" not in app            # no "+ Over / + Under / Track Over / Track Under"
    js = (WEB / "picks.js").read_text(encoding="utf-8")
    assert "Add to slip" in js and "Track pick" in js and "/api/sports/picks/track" in js


def test_tray_uses_slip_studio_draft_keys():
    tray = (WEB / "tray.js").read_text(encoding="utf-8")
    slips = (WEB / "slips.js").read_text(encoding="utf-8")
    assert '"ss:draft:" + lg + ":" + pf' in tray and '"ss:draft:" + c.league + ":" + c.platform' in slips
    assert '"ss:ctx"' in tray and '"ss:ctx"' in slips
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "SportsTray.add(leg)" in app and "SportsTray.render(state.lg)" in app and "addToSlip: addToSlip" in app


def test_filters_hooks():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "SportsFilters.html(" in app and "SportsFilters.matches(" in app and "SportsFilters.bind(" in app
    assert "scout.sports.filters.v1" in app                                       # filters stay saved on the device


# ------------------------------------------------------------------------- My picks API fields (item 7)
from sports import picks as _picks, settlement as _settlement, store as _store            # noqa: E402
from sports.tests import slip_world as _w                                                   # noqa: E402
from sports.tests.test_picks import _call, _item, _req, _track, con, tw                     # noqa: E402,F401


def test_my_picks_carries_what_the_dashboard_shows(tw):
    """Every field the My picks dashboard renders comes from /api/sports/picks/mine: tracked time, the chance shown
    and its source (forecast / model), the outcome at the pick's own line, corrections (revision > 1); stake 0, no
    grade letters, no returns."""
    c, legs = tw
    rid, f = legs["h1"]
    _track([_item(rid, f)])
    pc = _picks.connect()
    _picks.ingest(pc)
    o = _store.get(c, rid)
    _settlement.record(c, [_w.settlement(o, "over", "win", 25.0)])
    _picks.settle_all(c, pc)
    _settlement.record(c, [_w.settlement(o, "over", "loss", 19.0, at="2026-10-24T06:00:00Z")])
    _picks.settle_all(c, pc)                                                                # provider correction
    pc.close()
    code, mine = _call(_req("/api/sports/picks/mine"))
    p = mine["picks"][0]
    assert code == 200 and p["tracked_at"] and p["platform"] == "underdog" and p["side"] == "over"
    assert p["p_side"] == f.p_over and p["displayed_source"] == "scout_forecast" and p["forecast_id"] == f.forecast_id
    assert p["model_version"] == f.model_version and p["forecast_at"] == f.forecast_at and p["event_id"]
    r = p["result"]
    assert r["status"] == "loss" and r["value"] == 19.0 and r["revision"] == 2 and r["corrected"] is True
    assert r["outcome"] and r["settled_at"]
    assert p["stake_units"] == 0 and not any(k in p for k in ("grade", "grade_letter", "returns", "payout"))
