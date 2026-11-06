"""SCOUT_SLIP_HEADER_V2 in the slip rail (research hub, real Handler + isolated fixtures). Screenshots at 390 and 1440 go to
SLIP_HEADER_SHOTS when set. Review 2026-11-05: an entry type is rated only where the live EV check rates it (else that check's
reason), PrizePicks payouts stay unknown, plain words (Payout / Chance all hit / Average return), no Kelly on the member slip."""
import os
from pathlib import Path

import pytest

from tests.test_research_hub_browser import book, hub, saved_state
from tests.test_upgrade_browser import DESK, PHONE, browser, context, ctx, env, no_h_scroll  # noqa: F401  (fixtures)

SHOTS = Path(os.environ["SLIP_HEADER_SHOTS"]) if os.environ.get("SLIP_HEADER_SHOTS") else None
JARGON = ("Kelly", "True EV", "no push", "correlated", "95% range", "units")


def build_pair(p, env, size, app="underdog"):
    p.locator("#hub-sports [data-id=cs2]").click()
    book(p, app)
    for name in ("vargo", "orlo"):
        p.locator("#hub-q").fill(name)
        p.locator("#hub-rows .h-add:not([disabled])").first.click()
    saved_state(p, lambda d: len((d.get("slip") or {}).get("legs", [])) == 2)
    p.set_viewport_size(size)
    p.goto(env.url + "/#slips")
    p.wait_for_selector("#hub-studio-body .u-entry", timeout=20000)


def shot(body, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        body.locator(".u-entry").first.screenshot(path=str(SHOTS / name))


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_v2_header_follows_the_live_check_and_toggles_at_once(ctx, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    p = hub(ctx, env)
    build_pair(p, env, size)
    p.wait_for_selector("#hub-studio-body .u-head2[data-entry=standard]", timeout=20000)
    body = p.locator("#hub-studio-body")
    text = body.inner_text()
    assert "Each leg needs" in text and "≥ 53.5%" in text and "(-115)" in text and "3.5×" in text
    assert "settled matches" in text                      # the fixture has no settled results: the live check's own reason
    assert not [w for w in JARGON if w in text]
    shot(body, f"slip_header_v2_standard_{size['width']}.png")
    # spec: the toggle recomputes within 300 ms, from the answer already held for these legs (no request)
    took = p.evaluate("""() => new Promise((done) => { const t0 = performance.now();
        document.querySelector("#hub-studio-body [data-u=etype][data-id='underdog:flex']").click();
        const tick = () => document.querySelector('#hub-studio-body .u-head2[data-entry=flex]') ? done(performance.now() - t0)
          : performance.now() - t0 > 3000 ? done(1e9) : requestAnimationFrame(tick);
        tick(); })""")
    assert took < 300, took
    assert "no flex payout for 2 picks" in body.inner_text()
    shot(body, f"slip_header_v2_flex_{size['width']}.png")
    no_h_scroll(p)
    assert not p.errors


def test_v2_header_rated_entry_plain_words_and_red_line_below_break_even(ctx, env, monkeypatch):
    """Rendering of a rated answer (the fixture has no settled results to rate one) and a below-break-even leg."""
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    p = hub(ctx, env)

    def patch(route):
        doc = route.fetch().json()
        for e in doc.get("entries", {}).values():
            h = e.get("slip_header")
            if h:
                h["types"]["standard"] = {"entry": "standard", "n": 2, "rated": True, "payout": 3.5, "break_even": 0.534522,
                                          "break_even_american": -115, "win": 0.366, "ev": 0.281, "paid": 0.366,
                                          "legs": [{"index": 0, "p": 0.61, "need": 0.534522, "below": False, "ev": 0.141},
                                                   {"index": 1, "p": 0.52, "need": 0.534522, "below": True, "ev": -0.027}]}
        route.fulfill(json=doc)
    p.route("**/api/upgrade/recheck", patch)
    build_pair(p, env, DESK)
    p.wait_for_selector("#hub-studio-body .u-head2[data-entry=standard] .u-slip-metrics", timeout=20000)
    body = p.locator("#hub-studio-body")
    text = body.inner_text()
    assert all(w in text for w in ("Payout", "Chance all hit", "Average return", "36.6%", "+28.1%"))
    assert body.locator(".u-head2 .u-slip-metrics>div").count() == 3 and not [w for w in JARGON if w in text]
    assert body.locator(".u-leg.u-leg-below").count() == 1 and "Below the 53.5% each leg needs" in text
    assert "inset" in p.evaluate("getComputedStyle(document.querySelector('#hub-studio-body .u-leg-below')).boxShadow")
    shot(body, "slip_header_v2_rated_1440.png")
    assert not p.errors


def test_v2_header_prizepicks_payout_stays_unknown(ctx, env, monkeypatch):
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    p = hub(ctx, env)
    build_pair(p, env, DESK, "prizepicks")
    p.wait_for_selector("#hub-studio-body .u-head2[data-entry=power]", timeout=20000)
    text = p.locator("#hub-studio-body .u-head2").first.inner_text()
    assert "only in its app" in text and "Each leg needs" not in text and "×" not in text
    shot(p.locator("#hub-studio-body"), "slip_header_v2_prizepicks_1440.png")
    assert not p.errors


def test_v2_flag_off_keeps_the_rail_as_before(ctx, env, monkeypatch):
    monkeypatch.delenv("SCOUT_SLIP_HEADER_V2", raising=False)
    p = hub(ctx, env)
    build_pair(p, env, DESK, "prizepicks")
    p.wait_for_selector("#hub-studio-body .u-entry-foot .u-dim", timeout=20000)
    assert p.locator("#hub-studio-body .u-head2").count() == 0 and p.locator("#hub-studio-body .u-leg-below").count() == 0
    assert "Chance all hit" not in p.locator("#hub-studio-body").inner_text()
    assert not p.errors
