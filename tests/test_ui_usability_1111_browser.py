"""Member-facing controls against the real Handler, using isolated fixture data."""

import pytest
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll  # noqa: F401


@pytest.mark.parametrize('width', [390, 1280, 1920])
def test_individual_filters_and_readable_markets(browser, env, width):
    c = context(browser, env, {'width': width, 'height': 900})
    try:
        p = open_page(c, env, '/#all-sports', {'width': width, 'height': 900})
        p.wait_for_selector('#hub-rows tr[data-record]')
        p.locator('#hub-q').fill('vargo')
        p.locator('#hub-filters-toggle').click()
        p.locator('#hub-side').select_option('over')
        p.keyboard.press('Escape')
        assert p.locator('#hub-chips [data-id=q]').is_visible()
        p.locator('#hub-chips [data-id=side]').click()
        assert p.locator('#hub-q').input_value() == 'vargo'
        assert p.locator('#hub-side').input_value() == 'all'
        assert p.locator('#hub-rows .h-mcell b').first.evaluate('(e)=>getComputedStyle(e).whiteSpace') == 'normal'
        no_h_scroll(p)
        if width > 1100:
            before = p.locator('#hub-center').bounding_box()['width']
            p.locator('#hub-focus').click()
            assert not p.locator('#u-hub > .h-slip').is_visible()
            assert p.locator('#hub-center').bounding_box()['width'] > before + 150
            p.locator('#hub-focus').click()
            assert p.locator('#u-hub > .h-slip').is_visible()
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize('width', [390, 1280, 1920])
def test_studio_summary_layout_and_settings(browser, env, width, tmp_path):
    c = context(browser, env, {'width': width, 'height': 900})
    try:
        p = open_page(c, env, '/#slips', {'width': width, 'height': 900})
        p.get_by_role('tab', name='Auto-build', exact=True).click()
        p.locator('#hub-generation-slips').select_option('8')
        p.locator('#hub-generation-count').select_option('6')
        p.locator('#hub-generation-repeats').select_option('none')
        assert '8 slips × 6 picks' in p.locator('#hub-generation-summary').inner_text()
        p.get_by_role('tab', name='My slip', exact=False).click()
        p.get_by_role('tab', name='Auto-build', exact=True).click()
        assert p.locator('#hub-generation-repeats').input_value() == 'none'
        assert p.locator('#hub-generation-count').input_value() == '6'
        assert not p.locator('#hub-generation-side').is_visible()
        p.locator('.h-generation-more summary').click()
        p.locator('#hub-generation-side').select_option('under')
        assert 'Unders only' in p.locator('#hub-generation-summary').inner_text()
        assert p.locator('#hub-generation-filter-count').inner_text() == '1 active'
        controls = p.locator('.h-generator-controls select').evaluate_all('(es)=>es.map(e=>({w:e.clientWidth,scroll:e.scrollWidth,h:e.getBoundingClientRect().height}))')
        assert all(x['h'] >= 44 and x['w'] > 90 for x in controls)
        for control in p.locator('.h-generator-controls select, #hub-generate').all():
            control.scroll_into_view_if_needed()
            assert control.evaluate('(e)=>{const r=e.getBoundingClientRect();const at=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return at===e||e.contains(at)}')
        no_h_scroll(p)
        out = tmp_path / 'screenshots';out.mkdir(parents=True, exist_ok=True)
        p.locator('#hub-generate').scroll_into_view_if_needed()
        p.screenshot(path=str(out / f'studio-{width}.png'))
        assert not p.errors
    finally:
        c.close()

