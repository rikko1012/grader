"""Audit 2026-11-06 (5:40-6:05 PM CST), classic group, in a real browser against a local server.Handler with fixture data (no
network, no production writes). C1: on the classic Sports Prop board the Scout chance (mean + Over + Under, about 160 px on
one line) never runs out of its column into Market, and no other cell of the row runs into the next, from 390 px to wide
desktops. AUDIT_SHOTS=<folder> keeps a screenshot of the board's first rows at every width tested."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll   # noqa: F401 (pytest fixtures)

SHOTS = Path(os.environ["AUDIT_SHOTS"]) if os.environ.get("AUDIT_SHOTS") else None

# every visible text box of every board cell against its own cell (CSS px): a box past the cell's edge paints over the next column
OVERFLOW = """() => {
  const out = [];
  for (const tr of document.querySelectorAll('tr.sw-tb-row')) {
    if (!tr.getBoundingClientRect().height) continue;
    for (const td of tr.querySelectorAll(':scope > td')) {
      const cr = td.getBoundingClientRect(), rg = document.createRange(); rg.selectNodeContents(td);
      for (const k of [...td.querySelectorAll('span, b, a, button'), ...[...rg.getClientRects()].map(r => ({getBoundingClientRect: () => r, className: 'text'}))]) {
        const r = k.getBoundingClientRect(); if (!r.width) continue;
        if (r.right > cr.right + 1 || r.left < cr.left - 1) { out.push({cell: td.dataset.label, text: td.textContent.trim().slice(0, 60),
          cell_w: Math.round(cr.width), el: k.className || k.tagName, el_w: Math.round(r.width), past: Math.round(Math.max(r.right - cr.right, cr.left - r.left))}); break; }
      }
    }
  }
  return out;
}"""

# the widest numbers a row can carry (NFL yardage means, 100.0% chances, a two-digit edge, a 10-game tally, the market's
# fallback note) written into the first row, so the check does not depend on the fixture's short numbers
WIDEST = """() => {
  const tr = document.querySelector('tr.sw-tb-row'), set = (sel, t) => { const e = tr.querySelector(sel); if (e) e.textContent = t; };
  set('.sw-tb-scout .sw-tb-mu', '312.5'); set('.sw-tb-scout .sw-p.over', 'O 100.0%'); set('.sw-tb-scout .sw-p.under', 'U 100.0%');
  tr.querySelector('.sw-tb-mkt').innerHTML = '<span class="sw-tb-note warn" data-fallback="2">O 100.0%<span class="sw-tb-note">older book prices</span></span>';
  set('.sw-tb-edge', '+45.3'); set('.sw-tb-hit', '↑10 ↓10'); set('.sw-tb-hit5', '↑5 ↓5');
}"""


@pytest.mark.parametrize("width", [390, 761, 800, 1024, 1099, 1100, 1280, 1440, 1920])
def test_sports_scout_chance_never_overlaps_the_market_column(browser, env, width):
    size = {"width": width, "height": 900}
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/sports?view=classic#/board", size)
        p.wait_for_selector("tr.sw-tb-row .u-add", timeout=30000)
        p.wait_for_selector(".u-books .u-bk b", timeout=20000)
        p.wait_for_timeout(400)
        assert p.locator("tr.sw-tb-row .sw-tb-scout .sw-p.over").count() >= 1      # the fixture has Scout chances to measure
        if SHOTS:
            SHOTS.mkdir(parents=True, exist_ok=True)
            p.locator("tr.sw-tb-row").first.scroll_into_view_if_needed()
            p.screenshot(path=str(SHOTS / f"sports-board-scout-{width}.png"))
        assert p.evaluate(OVERFLOW) == []
        p.evaluate(WIDEST)
        assert p.evaluate(OVERFLOW) == []
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ C2: Reset on the classic CS2 board

COUNT = "(document.querySelector('#bcount').textContent.match(/of (\\d+) lines/) || [])[1] || ''"


def test_cs2_reset_after_choosing_prizepicks_returns_to_all_books(browser, env):
    size = {"width": 1440, "height": 900}
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/?view=classic#board", size)
        p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
        p.wait_for_function(COUNT)
        everything = p.evaluate(COUNT)
        p.locator('#bbook button[data-v="prizepicks"]').click()
        p.wait_for_function(f"location.hash === '#board/prizepicks' && ({COUNT}) && ({COUNT}) !== '{everything}'")
        p.locator('#bprop button[data-v="hs"]').click()
        p.locator('#bquick button[data-q="gap"]').click()
        p.locator("#breset").click()
        p.wait_for_function("document.querySelector('#bbook button.on').dataset.v === 'all'")
        p.wait_for_function(f"({COUNT}) === '{everything}'")               # the whole board again, not PrizePicks' share
        assert p.evaluate("location.hash") == "#board/all"
        assert p.evaluate("document.querySelector('#bprop button.on').dataset.v") == "kills"
        assert p.evaluate("document.querySelector('#bquick button[data-q=gap]').getAttribute('aria-pressed')") == "false"
        assert p.locator("#bchips .fchip").count() == 0 and p.evaluate("document.querySelector('#bbets').checked")
        assert not p.errors
    finally:
        c.close()
