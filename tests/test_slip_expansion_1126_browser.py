"""Soccer and tennis in Auto-build, in a real browser on the v3 Slips page (owner 2026-11-26 "add soccer & tennis to the auto
builder"): both boxes are listed and start unticked (every member's default batch is unchanged); ticked, they build slips that
open in My slip, pass recheck, track, and show in My picks. Fixture sports service only (no network)."""
from datetime import datetime, timezone

import pytest

import workspace_expansion as adapter
import workspace_sports
from tests.test_slip_expansion_1126 import board_doc
from tests.test_ui_v3_browser import v3_page, shot, PHONE, DESK
from tests.test_ui_v3_slips_browser import GEN_DONE, seg
from tests.test_upgrade_browser import browser, env, no_h_scroll  # noqa: F401


@pytest.fixture
def expansion(env, monkeypatch):
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    now = datetime.now(timezone.utc)
    docs = {s: board_doc(s, now) for s in adapter.SPORTS}
    original = workspace_sports.call

    def call(srv, path, query=None, **kw):
        if path == "/api/sports/expansion/board":
            return docs[query["sport"]], None
        if path == "/api/sports/expansion/record":
            raise workspace_sports.Down("not here")
        return original(srv, path, query, **kw)
    monkeypatch.setattr(workspace_sports, "call", call)
    adapter.reset()
    return docs


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_auto_build_lists_soccer_tennis_unticked_and_builds_trackable_slips(browser, env, expansion, monkeypatch, size):
    c, p = v3_page(browser, env, monkeypatch, "slips", size, "dark")
    try:
        p.goto(env.url + "/#/slips?tab=build")
        p.wait_for_selector('.sb-seg[data-for="hub-generation-book"]')
        box = p.locator("#hub-generation-sports")
        for s in ("soccer", "tennis"):
            assert box.locator(f"input[value={s}]").count() == 1, box.inner_html()
            assert not box.locator(f"input[value={s}]").is_checked()             # opt-in: the default batch is unchanged
        assert "Soccer" in box.inner_text() and "Tennis" in box.inner_text()
        for v in box.locator("input:checked").evaluate_all("els => els.map(e => e.value)"):
            box.locator(f"input[value={v}]").uncheck()
        box.locator("input[value=soccer]").check()
        box.locator("input[value=tennis]").check()
        seg(p, "hub-generation-book", "prizepicks")
        seg(p, "hub-generation-count", "2")
        seg(p, "hub-generation-slips", "2")
        p.locator("#hub-generate").click()
        p.wait_for_function(GEN_DONE, timeout=30000)
        cards = p.locator("#hub-generated .h-generated-card")
        assert cards.count() >= 1, p.locator("#hub-generation-status").inner_text()
        assert any(w in cards.first.inner_text() for w in ("Soccer", "Tennis")), cards.first.inner_text()
        no_h_scroll(p)
        shot(p, f"exp-build-{size['width']}")

        p.locator("#hub-generated [data-hub=use-generated]").first.click()
        p.locator("[role=tab][data-hub=studio-tab][data-id=slip]").click()
        p.wait_for_selector("#hub-studio-body .u-entry.ready", timeout=20000)
        assert p.locator("#hub-studio-body .u-leg").count() == 2
        p.wait_for_selector("#hub-studio-body [data-u=track]:not([disabled])", timeout=20000)
        p.locator("#hub-studio-body [data-u=track]").click()
        p.wait_for_selector("#hub-studio-body .u-note.good", timeout=20000)
        no_h_scroll(p)
        shot(p, f"exp-tracked-{size['width']}")
        p.goto(env.url + "/#/picks")
        p.wait_for_selector("#hub-picks-body .u-pick", timeout=20000)
        assert p.locator("#hub-picks-body .u-pick").count() == 2
        text = p.locator("#hub-picks-body").inner_text()
        assert "Soccer Player" in text or "Tennis Player" in text, text
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()
