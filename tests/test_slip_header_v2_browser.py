"""SCOUT_SLIP_HEADER_V2 in the slip rail (research hub, real Handler + isolated fixtures). Screenshots at 390 and 1440 go to
SLIP_HEADER_SHOTS when set."""
import os
from pathlib import Path

import pytest

from tests.test_research_hub_browser import book, hub, saved_state
from tests.test_upgrade_browser import DESK, PHONE, browser, context, ctx, env, no_h_scroll  # noqa: F401  (fixtures)

SHOTS = Path(os.environ["SLIP_HEADER_SHOTS"]) if os.environ.get("SLIP_HEADER_SHOTS") else None


def build_pp_pair(p, env, size):
    p.locator("#hub-sports [data-id=cs2]").click()
    book(p, "prizepicks")
    for name in ("vargo", "orlo"):
        p.locator("#hub-q").fill(name)
        p.locator("#hub-rows .h-add:not([disabled])").first.click()
    saved_state(p, lambda d: len((d.get("slip") or {}).get("legs", [])) == 2)
    p.set_viewport_size(size)
    p.goto(env.url + "/#slips")
    p.wait_for_selector("#hub-studio-body .u-entry", timeout=20000)


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_v2_header_shows_payout_win_ev_kelly_and_toggles_at_once(ctx, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    p = hub(ctx, env)
    build_pp_pair(p, env, size)
    p.wait_for_selector("#hub-studio-body .u-head2[data-entry=power]", timeout=20000)
    body = p.locator("#hub-studio-body")
    text = body.inner_text()
    assert "Each leg needs" in text and "≥ 57.7%" in text and "(-137)" in text
    metrics = body.locator(".u-head2 .u-slip-metrics>div")
    if metrics.count() == 4:
        assert all(w in text for w in ("Payout", "Win (no push)", "True EV", "Kelly ¼ units", "3×"))
    else:
        assert "Can't rate" in text
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        body.locator(".u-entry").first.screenshot(path=str(SHOTS / f"slip_header_v2_power_{size['width']}.png"))
    # spec: the toggle recomputes within 300 ms. Timed inside the page (click -> flex header in the DOM), so a busy test host
    # does not count the driver's own round trip; the repaint uses the answer already held for these legs, no request.
    took = p.evaluate("""() => new Promise((done) => { const t0 = performance.now();
        document.querySelector("#hub-studio-body [data-u=etype][data-id='prizepicks:flex']").click();
        const tick = () => document.querySelector('#hub-studio-body .u-head2[data-entry=flex]') ? done(performance.now() - t0)
          : performance.now() - t0 > 3000 ? done(1e9) : requestAnimationFrame(tick);
        tick(); })""")
    assert took < 300, took
    assert "≥ 61.8%" in body.inner_text()   # 2-pick Flex (2x / 0.5x) break-even
    p.wait_for_selector("#hub-studio-body .u-entry.ready .u-head2[data-entry=flex]", timeout=20000)   # the recheck agrees
    if SHOTS:
        body.locator(".u-entry").first.screenshot(path=str(SHOTS / f"slip_header_v2_flex_{size['width']}.png"))
    no_h_scroll(p)
    assert not p.errors


def test_v2_header_red_line_below_break_even_and_cant_rate(ctx, env, monkeypatch):
    """Rendering of server answers the fixture cannot produce: a below-break-even leg and unrated legs."""
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    p = hub(ctx, env)

    def patch(route):
        doc = route.fetch().json()
        for e in doc.get("entries", {}).values():
            h = e.get("slip_header")
            if h:
                pw = h["types"]["power"]
                pw["legs"][1].update(p=0.5, below=True)
                h["types"]["flex"] = {"entry": "flex", "n": 2, "rated": False, "payout": 2.0, "break_even": 0.618,
                                      "break_even_american": -162, "unrated": 1, "reason": "Can't rate: 1 unrated leg."}
        route.fulfill(json=doc)
    p.route("**/api/upgrade/recheck", patch)
    build_pp_pair(p, env, DESK)
    p.wait_for_selector("#hub-studio-body .u-head2[data-entry=power]", timeout=20000)
    body = p.locator("#hub-studio-body")
    assert body.locator(".u-leg.u-leg-below").count() == 1 and "Below the 57.7% each leg needs" in body.inner_text()
    red = p.evaluate("getComputedStyle(document.querySelector('#hub-studio-body .u-leg-below')).boxShadow")
    assert "inset" in red
    body.locator("[data-u=etype][data-id='prizepicks:flex']").click()
    p.wait_for_selector("#hub-studio-body .u-head2[data-entry=flex]", timeout=2000)
    assert "Can't rate: 1 unrated leg." in body.inner_text() and body.locator(".u-head2 .u-slip-metrics>div").count() == 1
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        body.locator(".u-entry").first.screenshot(path=str(SHOTS / "slip_header_v2_cant_rate_1440.png"))
    assert not p.errors


def test_v2_flag_off_keeps_the_rail_as_before(ctx, env, monkeypatch):
    monkeypatch.delenv("SCOUT_SLIP_HEADER_V2", raising=False)
    p = hub(ctx, env)
    build_pp_pair(p, env, DESK)
    p.wait_for_selector("#hub-studio-body .u-entry-foot .u-dim", timeout=20000)
    assert p.locator("#hub-studio-body .u-head2").count() == 0 and p.locator("#hub-studio-body .u-leg-below").count() == 0
    assert "True EV" not in p.locator("#hub-studio-body").inner_text()
    assert not p.errors
