"""v3 interface foundation (web/UI_V3.md) in a real browser against the real Handler with fixture data only: SCOUT_UI_V3=1, every page
group at 390x844 and 1280x900, dark and light. The shared shell (one header, phone bottom bar, theme, More) sits over each page
without hiding anything: its nav presses the hub's own buttons, counts mirror the hub's, no sideways scroll, no script errors.
Flag off, the same pages carry none of it. V3_SHOTS=<folder> keeps a screenshot per page, size and theme."""
import os
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401

PHONE, DESK = {"width": 390, "height": 844}, {"width": 1280, "height": 900}
SHOTS = Path(os.environ.get("V3_SHOTS") or "")
# page group -> (path, selector that says the page's own content is up, member?)
GROUPS = {
    "board": ("/#all-sports", "#hub-rows tr[data-record]", True),
    "slips": ("/#/slips", "#hub-studio-section:not([hidden])", True),
    "picks": ("/#/picks", "#hub-picks-section:not([hidden])", True),
    "record-hub": ("/#/record", "#hub-record-section:not([hidden])", True),
    "lol": ("/lol#all-sports", "#u-hub:not([hidden])", True),
    "sports": ("/sports#/board", "#u-hub:not([hidden])", True),
    "record": ("/record", "#record", False),
    "landing": ("/", "body", False),
}


def v3_page(browser, env, monkeypatch, group, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    path, ready, signed_in = GROUPS[group]
    c = context(browser, env, size) if signed_in else browser.new_context(viewport=size)
    c.route("https://fonts.*/**", lambda r: r.abort())
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + path)
    p.wait_for_selector(ready, timeout=30000, state="attached")
    if group != "landing":                     # the v3 landing (landing_v3.py) is one self-contained page: no shell bundle
        p.wait_for_function("!!window.ScoutV3")
    return c, p


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(300)
        p.screenshot(path=str(SHOTS / f"{name}.png"))


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
@pytest.mark.parametrize("group", list(GROUPS))
def test_v3_shell_on_every_page_group(browser, env, monkeypatch, group, size, theme):
    c, p = v3_page(browser, env, monkeypatch, group, size, theme)
    try:
        member = GROUPS[group][2]
        assert p.evaluate("document.documentElement.dataset.uiV3") == "1"
        want = "dark" if group == "landing" else theme        # the v3 landing (landing_v3.py) is pinned dark
        assert p.evaluate("document.documentElement.dataset.theme") == want
        bg = p.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--sc-bg').trim()")
        assert bg.upper() == ("#F6F7F9" if want == "light" else "#080B13")
        if group == "landing":                                 # the Prop Grader landing (owner brief Nov 23): Geist, its own tokens
            assert p.evaluate("getComputedStyle(document.body).fontFamily").startswith('Geist')
        else:
            assert p.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--sc-font')").strip().startswith("Outfit")
        if member:
            p.wait_for_selector(".sc-appbar.sc-v3-bar", state="visible")
            assert p.locator(".sc-v3-bar").count() == 1 and p.locator(".sc-v3-tabs").count() == 1
            top = p.evaluate("document.querySelector('.sc-v3-bar').getBoundingClientRect().top")
            assert top == 0
            hub = p.evaluate("(()=>{const h=document.getElementById('u-hub');return h&&!h.hidden?h.getBoundingClientRect().top:null})()")
            if hub is not None:
                assert hub >= 55                                                     # the hub starts below the header
                assert p.evaluate("getComputedStyle(document.querySelector('#u-hub .h-tabbar')).display") == "none"
            if size is PHONE:
                assert p.locator(".sc-v3-tabs").is_visible() and not p.locator(".sc-v3-bar .sc-nav").is_visible()
                assert p.locator(".sc-v3-tabs a:visible").count() == 5          # Board Slips Edge|Record My-picks More (org 11-20)
            else:
                assert p.locator(".sc-v3-bar .sc-nav").is_visible() and not p.locator(".sc-v3-tabs").is_visible()
                if hub is not None:
                    assert p.evaluate("getComputedStyle(document.querySelector('#u-hub .h-sidebar')).visibility") == "hidden"
                    left = p.evaluate("document.getElementById('hub-center').getBoundingClientRect().left")
                    assert left < 2                                                  # no empty sidebar column
            labels = p.locator(".sc-v3-bar .sc-nav a:visible").all_inner_texts()
            assert "Results" not in " ".join(labels) and "Lab" not in " ".join(labels)
        else:
            assert p.locator(".sc-v3-bar").count() == 0                              # public pages: their own header (D / C groups)
        no_h_scroll(p)
        shot(p, f"{group}-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_nav_presses_the_hubs_own_buttons_and_mirrors_its_state(browser, env, monkeypatch, size):
    c, p = v3_page(browser, env, monkeypatch, "board", size, "dark")
    try:
        bar = ".sc-v3-tabs" if size is PHONE else ".sc-v3-bar .sc-nav"
        assert p.locator(f"{bar} [aria-current=page]").inner_text().strip().startswith("Board")
        p.locator(f"{bar} [data-v3-nav=picks]").click()
        p.wait_for_selector("#hub-picks-section:not([hidden])")
        p.wait_for_function(f"document.querySelector('{bar} [data-v3-nav=picks]').getAttribute('aria-current')==='page'")
        assert p.url.split("#", 1)[1] in ("/picks", "scout/picks")                   # the hub's own route, as its own nav sets it
        if p.locator(f"{bar} [data-v3-nav=record]").is_visible():
            p.locator(f"{bar} [data-v3-nav=record]").click()
        else:                                                      # phone with an edge tool: Record sits in More's Scout group
            p.locator(".sc-v3-tabs [data-v3=more]").click()
            p.locator(".sc-v3-more [data-v3-more-key=record]").click()
        p.wait_for_selector("#hub-record-section:not([hidden])")
        p.locator(f"{bar} [data-v3-nav=board]").click()
        p.wait_for_selector("#hub-board-section:not([hidden])")
        # the slip count is the hub's own
        p.locator("#hub-rows tr[data-record] .h-add:not(.h-add-info)").first.click()
        p.wait_for_function("document.querySelector('[data-v3-count=slip]').textContent===(document.getElementById('hub-tab-slip-n')||document.getElementById('hub-slip-count')).textContent.trim()")
        assert p.locator("[data-v3-count=slip]").first.inner_text() != "0"
        # theme toggle: flips and remembers (same key as the hub's own)
        p.locator(".sc-v3-bar [data-v3=theme]").click()
        assert p.evaluate("document.documentElement.dataset.theme") == "light"
        assert p.evaluate("localStorage.getItem('scout-theme')") == "light"
        # More (phone tab / desktop header button): every item of the hub's own More list is reachable on this surface, in More or in
        # the bar (org 11-20: a destination already in the bar is not listed again), each pressing that item
        more = ".sc-v3-tabs [data-v3=more]" if size is PHONE else ".sc-v3-bar [data-v3=more]"
        p.locator(more).click()
        p.wait_for_selector(".sc-v3-more:not([hidden])")
        rows = [t.strip() for t in p.locator(".sc-v3-more .sc-more__a").all_inner_texts()]
        barl = [t.strip().split(" ")[0] for t in p.locator(f"{bar} [data-v3-nav]:visible").all_inner_texts()]
        hub_items = p.evaluate(r"[...document.querySelectorAll('#hub-more > button, #hub-more > a')].map(e=>e.textContent.replace(/[↗→]/g,'').replace(/\s+/g,' ').trim().replace(/\s*\d+$/,''))")
        alias = {"Tools": "All tools"}
        assert hub_items and all(h in rows or alias.get(h) in rows or h.split(" ")[0] in barl for h in hub_items), (hub_items, rows, barl)
        assert len(rows) == len(set(rows)), rows
        assert "Classic board" in rows and "Results" not in rows and not any("Lab" == r for r in rows)
        if size is PHONE:
            p.locator(".sc-v3-more .sc-more__a", has_text="All tools").first.click()
        else:
            p.locator(".sc-v3-more .sc-more__a", has_text="By game").first.click()
            p.wait_for_selector("#hub-board-section:not([hidden])")
            assert p.locator(".sc-v3-more").is_hidden()
            p.locator(".sc-v3-bar [data-v3-nav=tools]").click()
        p.wait_for_selector("#hub-tools-section:not([hidden])")
        assert p.locator(".sc-v3-more").is_hidden()
        if size is DESK:
            p.locator(".sc-v3-bar [data-v3=search]").click()
            p.wait_for_function("document.activeElement && document.activeElement.id==='hub-q'")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_v3_states_and_art_render_from_the_shared_shell(browser, env, monkeypatch, theme):
    c, p = v3_page(browser, env, monkeypatch, "board", PHONE, theme)
    try:
        p.evaluate("""async () => {
          const box = document.createElement('div'); box.id = 'v3-probe'; box.style.cssText = 'position:fixed;inset:60px 0 auto 0;z-index:99;padding:16px;background:var(--sc-bg)';
          document.body.appendChild(box);
          const a = document.createElement('div'); ScoutV3.state(a, 'loading', {rows: 3}); box.appendChild(a);
          const b = document.createElement('div'); ScoutV3.state(b, 'empty', {title: 'No picks yet', text: 'Tracked picks show here.', action: 'Open the board', onAction(){ window.v3Acted = 1; }}); box.appendChild(b);
          const d = document.createElement('div'); ScoutV3.state(d, 'error', {text: 'The board did not load.', retry(){ window.v3Retried = 1; }}); box.appendChild(d);
          const s = document.createElement('div'); s.className = 'sc-scene'; s.style.setProperty('--h', ScoutV3.HUE.mirage);
          s.innerHTML = '<div class="sc-scene__art"></div>'; box.appendChild(s);
          await ScoutV3.mountArt(s.querySelector('.sc-scene__art'), ScoutV3.mapArt('de_mirage'));
        }""")
        assert p.locator("#v3-probe .sc-skrow").count() == 3
        assert p.locator("#v3-probe .sc-skel").inner_text().strip() == "Loading"            # no digits, no zeros in a skeleton
        p.locator("#v3-probe .sc-empty .sc-btn").click()
        p.locator("#v3-probe .sc-errbox .sc-btn").click()
        assert p.evaluate("window.v3Acted===1 && window.v3Retried===1")
        assert p.locator("#v3-probe .sc-scene__art svg").count() == 1
        assert p.evaluate("ScoutV3.mapArt('Vertigo')") == ""                                  # no scene = none, never a guess
        assert p.evaluate("ScoutV3.art('../tokens').then(()=>1,()=>0)") == 0
        shot(p, f"states-390-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_flag_off_the_same_pages_carry_no_v3(browser, env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "0")
    c = context(browser, env, PHONE)
    try:
        p = c.new_page()
        p.goto(env.url + "/#all-sports")
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        assert p.evaluate("!window.ScoutV3 && !document.documentElement.dataset.uiV3")
        assert p.locator(".sc-v3-bar, .sc-v3-tabs").count() == 0
        assert p.locator("#u-hub .h-tabbar").is_visible()
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_player_card_opens_below_the_header_and_its_controls_work(browser, env, monkeypatch, size):
    c, p = v3_page(browser, env, monkeypatch, "board", size, "dark")
    try:
        p.locator("#hub-rows .h-player").first.click()
        p.wait_for_selector("#hub-card:not([hidden]) #hub-detail [data-hub=card-close]", timeout=15000)
        box = p.evaluate("(()=>{const r=document.getElementById('hub-card').getBoundingClientRect();return [r.top,r.bottom]})()")
        assert box[0] >= 55                                                           # not under the header
        if size is PHONE:
            assert box[1] <= 844 - 59                                                 # ends above the bottom bar
        p.locator("#hub-detail [data-hub=card-close]").click()                       # its own Back works (nothing covers it)
        p.wait_for_selector("#hub-card", state="hidden")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("path,ready", [("/?view=classic#board", "#boardout"), ("/lol?view=classic", "body"), ("/sports?view=classic#/board", "body")],
                         ids=["cs2", "lol", "sports"])
def test_v3_classic_views_keep_their_look_under_the_header(browser, env, monkeypatch, path, ready, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    c = context(browser, env, PHONE)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    try:
        p = c.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        p.goto(env.url + path)
        p.wait_for_selector(ready, state="attached", timeout=30000)
        p.wait_for_selector(".sc-v3-bar", state="visible")
        # the shell does not recolour the classic page: its body keeps the colours it has with the flag off
        bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
        monkeypatch.setenv("SCOUT_UI_V3", "0")
        q = c.new_page()
        q.goto(env.url + path)
        q.wait_for_selector(ready, state="attached", timeout=30000)
        assert q.evaluate("getComputedStyle(document.body).backgroundColor") == bg
        # a classic sticky header stays below the fixed bar
        tops = p.evaluate("[...document.querySelectorAll('header')].filter(h=>!h.closest('#u-hub')&&getComputedStyle(h).position==='sticky').map(h=>getComputedStyle(h).top)")
        assert all(t == "56px" for t in tops), tops
        no_h_scroll(p)
        shot(p, f"classic-{path.split('?')[0].strip('/') or 'cs2'}-390-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()
