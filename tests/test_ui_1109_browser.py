"""UI fixes 2026-11-09 (owner: "bro fix the ui why is filters getting blocked by picks tab on pc", "fix ui fix any bug's"), in a real
browser against the real Handler and fixture data (no network, no production writes). Display only: no grade, chance, stake, side or
projection moves.

- the Filters and Views pop-overs open fully inside the board column: their box never meets the Slip column, and every control in
  them is the element on top at its own centre (elementFromPoint), at 1280 / 1440 / 1600 / 1920 with the slip empty and with a pick
  on it, with the default features and with Best prices + the account bar (wider sidebar and slip); on a phone (390) they sit
  inside the window, above the tab bar
- the toolbar's own buttons (Best prices, Filters, Views, Refresh) never run under the Slip column: when one row does not fit, the
  sport tabs take a row of their own
- with the account bar on, "All sports research" is not shown inside the research hub it links to
- the slip's "Checked <time>." stays on one line; the sidebar's "Classic board" button is one line
UI_1109_SHOTS=<folder> keeps a screenshot of every case."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll  # noqa: F401

SHOTS = Path(os.environ["UI_1109_SHOTS"]) if os.environ.get("UI_1109_SHOTS") else None
DESKTOPS = [1280, 1440, 1600, 1920]
FLAGS = {"default": {}, "ev+tokens": {"SCOUT_PICKEM_EV": "1", "SCOUT_UI_TOKENS": "1"}}

# the pop-over's box, the Slip column's box (null when hidden) and every visible control that is NOT the top element at its centre
POP = """(sel) => { const pop = document.querySelector(sel), slip = document.querySelector('#u-hub .h-slip');
  const box = (e) => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom}; };
  const shown = getComputedStyle(slip).display !== 'none' && slip.getBoundingClientRect().width > 0;
  const blocked = [...pop.querySelectorAll('select,button,input')].filter(e => e.offsetParent).filter(e => {
    const r = e.getBoundingClientRect(), at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !(at && (at === e || e.contains(at))); }).map(e => e.id || e.textContent.trim());
  return {pop: box(pop), slip: shown ? box(slip) : null, vw: document.documentElement.clientWidth, vh: document.documentElement.clientHeight, blocked}; }"""

# toolbar controls outside the pop-overs, the sport tabs and the platform segment: any one past the Slip column's left edge or covered
BAR = """() => { const slip = document.querySelector('#u-hub .h-slip');
  const edge = getComputedStyle(slip).display === 'none' ? document.documentElement.clientWidth : slip.getBoundingClientRect().left;
  return [...document.querySelectorAll('#u-hub .h-toolbar button, #u-hub .h-toolbar input')]
    .filter(e => e.offsetParent && !e.closest('.h-pop,.h-platform,.h-sports'))
    .filter(e => { const r = e.getBoundingClientRect(), at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return r.right > edge + 0.5 || !(at && (at === e || e.contains(at))); })
    .map(e => e.id || e.textContent.trim()); }"""


def meets(a, b):
    return b is not None and a["l"] < b["r"] - 0.5 and a["r"] > b["l"] + 0.5 and a["t"] < b["b"] - 0.5 and a["b"] > b["t"] + 0.5


def hub(browser, env, w, h=900):
    size = {"width": w, "height": h}
    c = context(browser, env, size)
    p = open_page(c, env, "/#all-sports", size)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    return c, p


def check_pops(p, tag):
    for toggle, pop in (("#hub-filters-toggle", "#hub-filter-pop"), ("#hub-views-toggle", "#hub-views-pop")):
        p.locator(toggle).click()
        p.wait_for_selector(pop + ":not([hidden])")
        p.wait_for_timeout(50)
        g = p.evaluate(POP, pop)
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=str(SHOTS / f"{tag}-{pop[5:]}.png"))
        assert not meets(g["pop"], g["slip"]), (tag, pop, g)
        assert g["pop"]["l"] >= 0 and g["pop"]["r"] <= g["vw"] and g["pop"]["b"] <= g["vh"], (tag, pop, g)
        assert not g["blocked"], (tag, pop, g)
        p.keyboard.press("Escape")
        p.wait_for_selector(pop, state="hidden")


@pytest.mark.parametrize("flags", list(FLAGS), ids=list(FLAGS))
@pytest.mark.parametrize("leg", [False, True], ids=["slip-empty", "slip-1"])
@pytest.mark.parametrize("w", DESKTOPS, ids=[str(w) for w in DESKTOPS])
def test_filters_and_views_clear_the_slip_column_on_desktop(browser, env, monkeypatch, w, leg, flags):
    for k, v in FLAGS[flags].items():
        monkeypatch.setenv(k, v)
    c, p = hub(browser, env, w)
    try:
        if leg:
            p.locator("#hub-rows tr[data-record] [data-hub=add]").first.click()
            p.wait_for_function("document.getElementById('hub-slip-count').textContent === '1'")
        assert p.locator("#u-hub .h-slip").is_visible()
        assert p.evaluate(BAR) == []
        check_pops(p, f"{flags}-{w}-{'leg' if leg else 'empty'}")
        # a sport tab with a different toolbar width (fewer counts) keeps the same guarantees
        p.locator("#hub-sports [data-id=cs2]").click()
        p.wait_for_function("location.hash.includes('sport=cs2')")
        assert p.evaluate(BAR) == []
        check_pops(p, f"{flags}-{w}-{'leg' if leg else 'empty'}-cs2")
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("flags", list(FLAGS), ids=list(FLAGS))
def test_short_desktop_window_keeps_filters_reachable(browser, env, monkeypatch, flags):
    for k, v in FLAGS[flags].items():
        monkeypatch.setenv(k, v)
    c, p = hub(browser, env, 1280, 560)
    try:
        assert p.evaluate(BAR) == []
        check_pops(p, f"{flags}-1280x560")
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("flags", list(FLAGS), ids=list(FLAGS))
def test_phone_pop_overs_sit_inside_the_window(browser, env, monkeypatch, flags):
    for k, v in FLAGS[flags].items():
        monkeypatch.setenv(k, v)
    c, p = hub(browser, env, 390, 844)
    try:
        p.locator("#hub-rows tr[data-record] [data-hub=add]").first.click()
        check_pops(p, f"{flags}-390")
        tab = p.locator("#u-hub .h-tabbar").bounding_box()
        p.locator("#hub-filters-toggle").click()
        pop = p.locator("#hub-filter-pop").bounding_box()
        assert pop["y"] + pop["height"] <= tab["y"] + 0.5                    # above the tab bar, not under it
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_account_bar_hub_has_no_link_to_itself(browser, env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_TOKENS", "1")
    c, p = hub(browser, env, 1280)
    try:
        p.locator("#u-hub .h-sidebar [data-id=tools]").click()          # a section that shows the hub's top bar
        p.wait_for_selector("#hub-tools-section:not([hidden])")
        assert p.locator("#u-hub .h-account").is_visible()
        assert not p.locator("#u-hub a.h-entry").is_visible()
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("w", [1280, 1440])
def test_slip_check_time_and_classic_button_stay_on_one_line(browser, env, w):
    c, p = hub(browser, env, w)
    try:
        p.locator("#hub-rows tr[data-record] [data-hub=add]").first.click()
        p.wait_for_function("/Checked \\d/.test(document.getElementById('hub-slip-content').textContent)", timeout=20000)
        lines = p.evaluate("""() => { const w = document.createTreeWalker(document.getElementById('hub-slip-content'), NodeFilter.SHOW_TEXT);
          let n; while ((n = w.nextNode())) if (/Checked \\d/.test(n.textContent)) { const r = document.createRange(); r.selectNodeContents(n);
            return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; } return -1; }""")
        assert lines == 1
        btn = p.locator("#u-hub .h-sidebar-foot button").bounding_box()
        assert btn["height"] <= 44
        assert not p.errors
    finally:
        c.close()
