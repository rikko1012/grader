"""v3 organisation (org 2026-11-20, owner: "fix the organization"), in a real browser against the real Handler with fixture data.

- Every hub destination (sidebar sections, the hub's More list, the sidebar foot) is reachable on each surface, phone 390 and desktop
  1280, on /, /lol and /sports, and listed once: the bar plus the More sheet never name one destination twice.
- Edge: EV and Arbitrage are one tap away on both surfaces (desktop header, phone Edge tab); the Edge strip on top of each edge tool
  switches between EV, Arbitrage, Line moves, Lines differ, Sportsbooks and Moneylines in one tap; the Edge tab reopens the last one.
- With no edge tool (their flags off) the phone bar is the previous one (Board · Slips · My picks · Record · More) and no strip shows.
- Tools repeats neither Sportsbooks' Game lines nor the EV / Arbitrage sheet; its cards name and open EV and Arbitrage.
- No sideways scroll, no script error, dark and light. V3_SHOTS=<folder> keeps screenshots."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_ui_v3_browser import v3_page, PHONE, DESK

SHOTS = Path(os.environ.get("V3_SHOTS") or "")
# the member display flags production has on (tests/conftest.py V3_PROD_FLAGS) + the hub's V2 workspace (production /me workspace_v2)
PROD = ("SCOUT_BOARD_H2H", "SCOUT_PARITY_EDGE", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_HIT_SORT", "SCOUT_PARITY_EV_BAND", "SCOUT_PARITY_PLAYS",
        "SCOUT_PICKEM_EV", "SCOUT_MARKET_EDGE", "SCOUT_MARKET_TOOLS", "SCOUT_SOCCER_ENABLED", "SCOUT_TENNIS_ENABLED", "SCOUT_WORKSPACE_V2")


def prod(monkeypatch):
    for k in PROD:
        monkeypatch.setenv(k, "1")


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"org-{name}.png"))


# every hub destination, keyed by kind (the same attributes shell.js reads), and what this surface offers: bar keys + More keys
SURFACE = r"""(()=>{
  const h=document.getElementById('u-hub'), vis=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
  const key=e=>{const a=e.getAttribute('data-hub')||'',id=e.getAttribute('data-id')||'',href=e.getAttribute('href')||'';
    if(e.hasAttribute('data-v3x-more-view'))return e.getAttribute('data-v3x-more-view');
    if(e.hasAttribute('data-v3x-ml')||/#moneylines$|[?&]v3=moneylines$/.test(href))return 'moneylines';
    if(a==='section')return id; if(a==='studio-open'||a==='studio-tab')return 'studio-'+id; if(a==='view')return 'view-'+id;
    if(/#one$/.test(href))return 'grader'; if(/#matches$/.test(href))return 'matches'; if(a==='close')return 'classic';
    return 'x-'+(e.textContent||'').replace(/[↗→]/g,'').replace(/\s+/g,' ').trim().toLowerCase();};
  const dest=new Set([...h.querySelectorAll('.h-sidebar nav [data-hub="section"], #hub-more > button, #hub-more > a, .h-sidebar-foot > button, .h-sidebar-foot > a')].map(key));
  if(document.getElementById('hub-movedOnly'))dest.add('moves'); if(document.getElementById('hub-diff'))dest.add('differ');
  const NAV={board:'board',slips:'studio',ev:'ev',arb:'arb',books:'books',picks:'picks',record:'record',tools:'tools'};
  const barSel=matchMedia('(min-width:1024px)').matches?'.sc-v3-bar .sc-nav [data-v3-nav]':'.sc-v3-tabs [data-v3-nav]';
  const bar=[...document.querySelectorAll(barSel)].filter(vis).map(a=>NAV[a.getAttribute('data-v3-nav')]).filter(Boolean);
  return {dest:[...dest], bar, tabs:[...document.querySelectorAll('.sc-v3-tabs a')].filter(vis).map(a=>a.textContent.replace(/\s+/g,' ').trim())};
})()"""


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
@pytest.mark.parametrize("page", ["board", "lol", "sports"])
def test_every_destination_is_reachable_once_per_surface(browser, env, monkeypatch, page, size, theme):
    prod(monkeypatch)
    c, p = v3_page(browser, env, monkeypatch, page, size, theme)
    try:
        p.wait_for_function("document.querySelector('#u-hub .h-sidebar [data-id=ev]') && document.getElementById('hub-diff')", timeout=20000)
        p.wait_for_timeout(300)
        s = p.evaluate(SURFACE)
        more = ".sc-v3-tabs [data-v3=more]" if size is PHONE else ".sc-v3-bar [data-v3=more]"
        p.locator(more).click()
        p.wait_for_selector(".sc-v3-more:not([hidden])")
        keys = p.evaluate("[...document.querySelectorAll('.sc-v3-more [data-v3-more-key]')].map(e=>e.getAttribute('data-v3-more-key'))")
        heads = p.evaluate("[...document.querySelectorAll('.sc-v3-more .sc-more__h')].map(e=>e.textContent.trim())")
        assert heads == ["Edge", "Slips", "Research", "Scout"], heads
        offered = s["bar"] + keys
        assert len(offered) == len(set(offered)), ("listed twice", s["bar"], keys)
        missing = [d for d in s["dest"] if d not in offered]
        assert not missing, (missing, s, keys)
        if size is PHONE:
            assert s["tabs"][:3] == ["Board", "Slips 0", "Edge"] and s["tabs"][3:] == ["My picks", "More"], s["tabs"]
            assert keys[:6] == ["ev", "arb", "moves", "differ", "books", "moneylines"], keys
            assert "record" in keys
        else:
            assert s["bar"] == ["board", "studio", "ev", "arb", "books", "picks", "record", "tools"], s["bar"]
            assert keys[:3] == ["moves", "differ", "moneylines"] and not {"ev", "arb", "books", "record", "tools"} & set(keys), keys
        no_h_scroll(p)
        shot(p, f"{page}-more-{size['width']}-{theme}")
        p.keyboard.press("Escape")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_edge_tools_are_one_tap_apart(browser, env, monkeypatch, size):
    prod(monkeypatch)
    c, p = v3_page(browser, env, monkeypatch, "board", size, "dark")
    try:
        p.wait_for_function("document.querySelector('#u-hub .h-sidebar [data-id=ev]') && document.getElementById('hub-diff')", timeout=20000)
        assert p.locator(".sc-v3-edge").is_hidden()                      # no strip on the plain board
        if size is PHONE:
            p.locator(".sc-v3-tabs [data-v3-nav=edge]").click()          # one tap: EV
        else:
            p.locator(".sc-v3-bar [data-v3-nav=ev]").click()
        p.wait_for_selector("#hub-ev-section:not([hidden])")
        p.wait_for_selector(".sc-v3-edge:not([hidden])")
        pills = [t.strip() for t in p.locator(".sc-v3-edge [data-v3-edge]").all_inner_texts()]
        assert pills == ["EV", "Arbitrage", "Line moves", "Lines differ", "Sportsbooks", "Moneylines"], pills
        assert p.locator(".sc-v3-edge [aria-current=page]").inner_text().strip() == "EV"
        if size is PHONE:
            assert p.locator(".sc-v3-tabs [data-v3-nav=edge]").get_attribute("aria-current") == "page"
        # the strip sits under the page title, above the tool
        assert p.evaluate("document.querySelector('.sc-v3-edge').previousElementSibling.classList.contains('h-top')")
        no_h_scroll(p)
        shot(p, f"ev-{size['width']}")
        p.locator(".sc-v3-edge [data-v3-edge=arb]").click()              # one more tap: Arbitrage
        p.wait_for_selector("#hub-arb-section:not([hidden])")
        p.wait_for_function("document.querySelector('.sc-v3-edge [aria-current=page]')?.dataset.v3Edge==='arb'")
        p.locator(".sc-v3-edge [data-v3-edge=differ]").click()           # Lines differ: the board with the hub's own quick filter on
        p.wait_for_selector("#hub-board-section:not([hidden])")
        p.wait_for_function("document.getElementById('hub-diff').getAttribute('aria-pressed')==='true'")
        p.wait_for_function("document.querySelector('.sc-v3-edge:not([hidden]) [aria-current=page]')?.dataset.v3Edge==='differ'")
        shot(p, f"differ-{size['width']}")
        p.locator(".sc-v3-edge [data-v3-edge=moves]").click()            # one board view at a time
        p.wait_for_function("document.getElementById('hub-movedOnly').getAttribute('aria-pressed')==='true' && document.getElementById('hub-diff').getAttribute('aria-pressed')==='false'")
        p.locator(".sc-v3-edge [data-v3-edge=books]").click()
        p.wait_for_selector("#hub-books-section:not([hidden])")
        if size is PHONE:                                                # the Edge tab reopens the last edge tool
            p.locator(".sc-v3-tabs [data-v3-nav=board]").click()
            p.wait_for_selector("#hub-board-section:not([hidden])")
            p.locator(".sc-v3-tabs [data-v3-nav=edge]").click()
            p.wait_for_selector("#hub-books-section:not([hidden])")
        else:
            p.locator(".sc-v3-bar [data-v3-nav=picks]").click()
            p.wait_for_selector("#hub-picks-section:not([hidden])")
            p.wait_for_selector(".sc-v3-edge", state="hidden")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_with_ev_and_arbitrage_off_edge_holds_the_rest(browser, env, monkeypatch, size):
    """EV / Arbitrage flags off: no EV / Arbitrage anywhere, Edge still groups Line moves / Lines differ / Sportsbooks / Moneylines.
    (Sportsbooks is on for every member with NBA or NFL, so production always has an Edge tab; with no edge tool at all the phone
    bar falls back to Board · Slips · My picks · Record · More.)"""
    for k in ("SCOUT_PICKEM_EV", "SCOUT_MARKET_TOOLS", "SCOUT_MARKET_EDGE"):
        monkeypatch.delenv(k, raising=False)
    c, p = v3_page(browser, env, monkeypatch, "board", size, "light")
    try:
        p.wait_for_function("document.querySelector('#u-hub .h-sidebar [data-id=books]')", timeout=20000)
        assert not p.evaluate("!!document.querySelector('#u-hub .h-sidebar [data-id=ev], #u-hub .h-sidebar [data-id=arb]')")
        p.locator(".sc-v3-tabs [data-v3=more]" if size is PHONE else ".sc-v3-bar [data-v3=more]").click()
        keys = p.evaluate("[...document.querySelectorAll('.sc-v3-more [data-v3-more-key]')].map(e=>e.getAttribute('data-v3-more-key'))")
        assert "moneylines" in keys and "classic" in keys and not {"ev", "arb"} & set(keys), keys
        p.keyboard.press("Escape")
        if size is PHONE:
            p.locator(".sc-v3-tabs [data-v3-nav=edge]").click()
        else:
            p.locator(".sc-v3-bar [data-v3-nav=books]").click()
        p.wait_for_selector("#hub-books-section:not([hidden])")
        pills = [t.strip() for t in p.locator(".sc-v3-edge:not([hidden]) [data-v3-edge]").all_inner_texts()]
        assert pills == ["Lines differ", "Sportsbooks", "Moneylines"], pills
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_tools_repeats_no_other_page_and_names_ev_and_arbitrage(browser, env, monkeypatch):
    prod(monkeypatch)
    c, p = v3_page(browser, env, monkeypatch, "board", DESK, "dark")
    try:
        p.wait_for_function("document.querySelector('#u-hub .h-sidebar [data-id=books]')", timeout=20000)
        p.locator(".sc-v3-bar [data-v3-nav=tools]").click()
        p.wait_for_selector("#hub-tools-section:not([hidden])")
        p.wait_for_function("document.querySelector('#hub-tools-body article[data-v3-tool=arb]')")
        assert p.evaluate("!document.getElementById('hub-game-prices') || !document.getElementById('hub-game-prices').offsetParent")
        assert p.evaluate("!document.querySelector('#hub-tools-body>#hub-market-tools') || !document.querySelector('#hub-tools-body>#hub-market-tools').offsetParent")
        titles = p.evaluate("[...document.querySelectorAll('#hub-tools-body article[data-v3-tool] h2')].map(e=>e.textContent)")
        assert titles == ["EV", "Arbitrage"], titles
        assert p.evaluate("![...document.querySelectorAll('#hub-tools-body h2')].some(e=>e.offsetParent&&/Best prices|Price check/.test(e.textContent))")
        shot(p, "tools-1280")
        p.locator("#hub-tools-body article[data-v3-tool=arb] [data-hub=section]").click()
        p.wait_for_selector("#hub-arb-section:not([hidden])")
        p.locator(".sc-v3-bar [data-v3-nav=tools]").click()
        p.locator("#hub-tools-body article[data-v3-tool=ev] [data-hub=section]").click()
        p.wait_for_selector("#hub-ev-section:not([hidden])")
        p.locator(".sc-v3-bar [data-v3-nav=books]").click()              # Game lines still have their own page
        p.wait_for_selector("#hub-books-section:not([hidden])")
        assert not p.errors, p.errors
    finally:
        c.close()
