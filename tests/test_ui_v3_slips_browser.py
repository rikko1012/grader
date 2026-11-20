"""v3 group B (slips) in a real browser against the real Handler with fixture data (web/UI_V3.md): SCOUT_UI_V3=1, 390x844 and
1280x900, dark and light. Slip Studio (My slip / Auto-build / Tracked), the desktop slip column, the phone toast dock. Every field the
slip shows today stays: the hub's own nodes keep their ids and text (slips.js only wraps, moves within the section and mirrors them).
V3_SHOTS=<folder> keeps a screenshot per page, size and theme."""
import re

import pytest

from tests.test_ui_v3_browser import v3_page, shot, PHONE, DESK
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401

GEN_DONE = (r"document.querySelector('#hub-generated .h-generated-card li') || /\w/.test(document.querySelector('#hub-generation-status').textContent)"
            r" && !document.querySelector('#hub-generate').disabled")


def seg(p, sel_id, value):
    p.locator(f'.sb-seg[data-for="{sel_id}"] button[data-v="{value}"]').click()
    p.wait_for_function(f"document.getElementById('{sel_id}').value === '{value}'")


def build_and_open(p, env):
    """Auto-build one PrizePicks 3-pick slip through the v3 controls, then open it in My slip (the hub's own flow)."""
    p.goto(env.url + '/#/slips?tab=build')
    p.wait_for_selector('.sb-seg[data-for="hub-generation-book"]')
    p.locator('#hub-generation-sports input[value=lol]').uncheck()
    seg(p, 'hub-generation-book', 'prizepicks')
    seg(p, 'hub-generation-count', '3')
    seg(p, 'hub-generation-slips', '2')
    p.locator('#hub-generate').click()
    p.wait_for_function(GEN_DONE, timeout=30000)
    assert p.locator('#hub-generated .h-generated-card li').count(), p.locator('#hub-generation-status').inner_text()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_slip_studio_my_slip_auto_build_and_tracked(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "slips", size, theme)
    w = size["width"]
    try:
        # empty My slip: band (title, the hub's sync line, its action), tabs, the hub's empty state and Before you track
        p.wait_for_selector('#hub-studio-section .sb-band .h-studio-intro #hub-studio-sync')
        band = p.locator('#hub-studio-section .sb-band')
        assert band.locator('.sc-lit__title').inner_text() == 'Slips'
        assert band.locator('[data-hub=section][data-id=board]').is_visible()
        assert 'Add player props' in band.locator('[data-hub=section][data-id=board]').inner_text()
        assert [t.strip() for t in p.locator('.h-studio-tabs [role=tab]').all_inner_texts()][1:] == ['Auto-build', 'Tracked']
        assert p.locator('#hub-studio-body .h-empty h3').inner_text() == 'Your slip is empty'
        assert p.locator('.sb-aside .h-studio-explain h3').inner_text() == 'Before you track'
        no_h_scroll(p)
        shot(p, f"slips-empty-{w}-{theme}")

        # Auto-build: two panels; every select has a segmented control that drives it; the rail mirrors the live summary
        build_and_open(p, env)
        assert p.locator('.sb-panel').count() == 2
        for sid in ('hub-generation-book', 'hub-generation-type', 'hub-generation-slips', 'hub-generation-count', 'hub-generation-mix',
                    'hub-generation-lines', 'hub-generation-games', 'hub-generation-repeats', 'hub-generation-side'):
            assert p.locator(f'#{sid}').count() == 1, sid                                     # the hub's own control stays
            assert p.locator(f'.sb-seg[data-for="{sid}"] button').count() == p.locator(f'#{sid} option').count(), sid
            assert p.locator(f'.sb-seg[data-for="{sid}"] [aria-pressed=true]').get_attribute('data-v') == p.evaluate(f"document.getElementById('{sid}').value"), sid
        assert p.locator('.sb-seg[data-for="hub-generation-type"] button').all_inner_texts() == ['Power', 'Flex']   # PrizePicks types
        summary = p.evaluate("document.getElementById('hub-generation-summary').textContent")
        assert summary.startswith('2 slips × 3 picks · PrizePicks')
        assert p.locator('.sb-rail .sb-big').inner_text().startswith('2 slips × 3 picks')
        assert p.locator('.sb-rail #hub-generate').is_visible() and p.locator('.sb-rail #hub-generation-status').count() == 1
        assert p.locator('.sb-build > details summary').inner_text() == 'How lineups are built'
        cards = p.locator('#hub-generated .h-generated-card')
        assert cards.count() >= 1 and p.locator('#hub-generated .h-batch-head h2').inner_text().endswith('separate slips')
        for i in range(cards.locator('li').count()):                                          # each leg: chance + its texts stay
            li = cards.locator('li').nth(i)
            assert re.fullmatch(r"\d+\.\d%", li.locator(':scope > b').inner_text()), li.inner_text()
            assert li.locator('.sb-av, .u-face').count() == 1
        no_h_scroll(p)
        shot(p, f"slips-build-{w}-{theme}")
        p.locator('#hub-generated').scroll_into_view_if_needed()
        shot(p, f"slips-generated-{w}-{theme}")
        # switching platform rebuilds the entry-type control from the hub's own options
        seg(p, 'hub-generation-book', 'underdog')
        p.wait_for_function("[...document.querySelectorAll('.sb-seg[data-for=\"hub-generation-type\"] button')].map(b=>b.textContent).join()===[...document.getElementById('hub-generation-type').options].map(o=>o.textContent.trim()).join()")
        seg(p, 'hub-generation-book', 'prizepicks')

        # My slip: the generated slip opened in the hub's own flow
        p.locator('#hub-generated [data-hub=use-generated]').first.click()
        p.locator('[role=tab][data-hub=studio-tab][data-id=slip]').click()
        p.wait_for_selector('#hub-studio-body .u-entry.ready', timeout=20000)
        p.wait_for_selector('.sb-sum--slip:not([hidden])')
        legs = p.locator('#hub-studio-body .u-leg')
        assert legs.count() == 3
        for i in range(3):
            leg = legs.nth(i)
            assert leg.locator('.u-leg-name').count() and leg.locator('.u-leg-pick').count() and leg.locator('.u-x').count()
            src = leg.locator('.u-leg-meta .sb-src').text_content()                             # today's read text, kept for readers
            assert re.match(r"^(?:[A-F][+-]? |Lean )(?:Over|Under) \d+\.\d%$", src), src
            read = leg.locator('.sb-read')
            assert read.locator('b').inner_text() == src.split()[-1]
            assert read.locator('small').inner_text() == 'Model estimate'
            assert read.locator('.sc-grade').inner_text() in src
            assert leg.locator('.sb-av, .sb-face').count() == 1
        for sel in ('.u-status', '.u-det[data-det=checks]', '.u-entry-h .u-seg', '.u-entry-foot', '[data-u=track]', '[data-u=copy]', '[data-u=clear]', '[data-u=check]'):
            assert p.locator(f'#hub-studio-body {sel}').count() >= 1, sel
        assert p.locator('.sb-sum--slip h3').inner_text() == 'This slip'
        rows = p.locator('.sb-sum--slip dt').all_inner_texts()
        assert rows[0].startswith('PrizePicks · ') and rows[-1] == 'Checked'
        assert p.locator('.sb-sum--slip dd').first.inner_text().startswith('3 picks')
        assert p.locator('#hub-studio-tab-n').inner_text() == '3'
        p.wait_for_function("document.querySelector('#hub-studio-sync').textContent.startsWith('Saved to your account')", timeout=20000)
        no_h_scroll(p)
        shot(p, f"slips-my-slip-{w}-{theme}")
        # entry-type switch is the hub's own button; the leg reads repaint
        p.locator('#hub-studio-body .u-seg [data-u=etype]').nth(1).click()
        p.wait_for_function("document.querySelector('#hub-studio-body .u-seg [aria-pressed=true]').textContent==='Flex'")
        p.wait_for_function("document.querySelectorAll('#hub-studio-body .u-leg .sb-read').length===3")

        # Tracked: the hub's own panel
        p.locator('[role=tab][data-hub=studio-tab][data-id=tracked]').click()
        p.wait_for_selector('[data-studio-panel=tracked]:not([hidden]) .h-tracked-slips h2')
        assert p.locator('.h-tracked-slips h2').inner_text() == 'Tracked slips'
        assert not p.locator('.sb-band [data-hub=section][data-id=board]').is_visible()          # My slip's action stays in My slip
        assert p.locator('.sb-band #hub-studio-sync').is_visible()
        no_h_scroll(p)
        shot(p, f"slips-tracked-{w}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_v3_desktop_slip_column_reads_like_the_board_row(browser, env, monkeypatch, theme):
    c, p = v3_page(browser, env, monkeypatch, "board", DESK, theme)
    try:
        p.wait_for_selector('#u-hub .h-slip #hub-slip-content')
        p.locator("#hub-rows tr[data-record] .h-add:not(.h-add-info)").first.click()
        p.wait_for_selector('#hub-slip-content .u-leg .sb-read', timeout=20000)
        leg = p.locator('#hub-slip-content .u-leg').first
        assert leg.locator('.u-leg-meta .sb-src').count() == 1 and leg.locator('.u-x').count() == 1
        assert p.locator('.h-slip > header h2').inner_text().startswith('Slip')
        assert p.locator('.h-slip > footer').inner_text().startswith('One entry per platform')
        bg = p.evaluate("getComputedStyle(document.querySelector('#u-hub .h-slip')).backgroundColor")
        assert bg == ('rgb(246, 247, 249)' if theme == 'light' else 'rgb(8, 11, 19)')
        no_h_scroll(p)
        shot(p, f"slips-column-1280-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_v3_phone_toast_docks_above_the_bottom_bar(browser, env, monkeypatch, theme):
    c, p = v3_page(browser, env, monkeypatch, "board", PHONE, theme)
    try:
        p.locator("#hub-rows tr[data-record] .h-add:not(.h-add-info)").first.click()
        p.wait_for_selector('.u-toast.on')
        t = p.evaluate("document.querySelector('.u-toast.on').getBoundingClientRect().bottom")
        bar = p.evaluate("document.querySelector('.sc-v3-tabs').getBoundingClientRect().top")
        assert t <= bar
        shot(p, f"slips-toast-390-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_v3_book_dialog_and_lineup_image_action(browser, env, monkeypatch, theme):
    import server
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setattr(server, 'PP_OPEN_LINK', True)
    c, p = v3_page(browser, env, monkeypatch, "slips", DESK, theme)
    try:
        p.wait_for_selector('.sb-band [data-hub=share-card] svg')                            # the hub's own button, in the band
        assert 'Download lineup image' in p.locator('.sb-band [data-hub=share-card]').inner_text()
        p.goto(env.url + '/#/board')
        p.wait_for_selector('#hub-rows tr[data-record]')
        p.locator('#hub-sports [data-id=cs2]').click()
        p.locator('#hub-platform [data-id=prizepicks]').click()
        for name in ('vargo', 'orlo'):                                                       # as test_workspace_v2_browser does
            p.locator('#hub-q').fill(name)
            p.locator('#hub-rows .h-add:not([disabled])').first.click()
        p.evaluate("location.hash='/slips'")
        p.wait_for_selector('#hub-studio-body [data-u=open-entry]', timeout=20000)
        p.locator('#hub-studio-body [data-u=open-entry]').first.click()
        box = p.locator('#u-book-open')
        box.wait_for(state='visible')
        assert box.locator('a.u-gold').get_attribute('href').startswith('https://app.prizepicks.com/board?projections=')
        assert box.bounding_box()['width'] <= 421
        shot(p, f"slips-book-dialog-1280-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_slips_between_1024_and_1100(browser, env, monkeypatch):
    c, p = v3_page(browser, env, monkeypatch, "slips", {"width": 1060, "height": 900}, "dark")
    try:
        build_and_open(p, env)
        no_h_scroll(p)
        shot(p, "slips-build-1060-dark")
        p.locator('#hub-generated [data-hub=use-generated]').first.click()
        p.locator('[role=tab][data-hub=studio-tab][data-id=slip]').click()
        p.wait_for_selector('#hub-studio-body .u-entry.ready', timeout=20000)
        no_h_scroll(p)
        shot(p, "slips-my-slip-1060-dark")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_flag_off_the_slip_studio_is_untouched(browser, env, monkeypatch):
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    c = context(browser, env, PHONE)
    try:
        p = c.new_page()
        p.goto(env.url + '/#/slips')
        p.wait_for_selector('#hub-studio-section:not([hidden])')
        assert p.evaluate("!window.ScoutV3") and p.locator('.sb-band, .sb-panel, .sb-seg, .sb-aside').count() == 0
        assert p.locator('[data-studio-panel=slip] > .h-studio-intro').count() == 1
    finally:
        c.close()
