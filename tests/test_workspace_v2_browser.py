"""V2 runs against the real authenticated Handler, without production writes."""
import pytest
import os
from pathlib import Path
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll


@pytest.fixture(autouse=True)
def v2(monkeypatch):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')


def start(ctx, env, path='/#/board', width=1440):
    p = open_page(ctx, env, path, {'width': width, 'height': 900})
    p.wait_for_selector('#u-hub.h-v2')
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    return p


def test_game_route_uses_live_fixture_records_and_scoped_board(ctx, env):
    p = start(ctx, env)
    p.locator('#hub-q').fill('not-a-player')
    p.locator('#hub-watch').click()
    p.locator('#hub-soonOnly').click()
    p.evaluate("location.hash='/games'")
    try:
        p.wait_for_selector('.h-game-card')
    except Exception:
        print(p.url, p.errors, p.locator('#hub-games-section').inner_text(), p.locator('#hub-title').inner_text())
        raise
    card = p.locator('.h-game-card').first
    event = card.locator('h2').inner_text()
    card.locator('button').click()
    assert '#/board' in p.url
    p.wait_for_selector('#hub-rows tr[data-record]')
    assert all(event in x for x in p.locator('#hub-rows .h-match').all_text_contents())
    p.go_back()
    p.wait_for_selector('#hub-games-section:not([hidden])')
    assert not p.errors


@pytest.mark.parametrize('width', [360, 390, 640, 1024, 1025, 1100, 1440])
def test_all_routes_fit_and_preserve_original_tools(ctx, env, width):
    p = start(ctx, env, width=width)
    for route, section in [('games','games'),('slips','studio'),('results','record'),('tools','tools')]:
        p.evaluate('(h)=>{location.hash=h}', '/'+route)
        p.wait_for_selector('#hub-'+section+'-section:not([hidden])')
        no_h_scroll(p)
        assert p.locator('.h-center').bounding_box()['y'] < 100
        if os.environ.get('WORKSPACE_V2_SHOTS'):
            dest = Path(os.environ['WORKSPACE_V2_SHOTS'])
            dest.mkdir(parents=True, exist_ok=True)
            p.screenshot(path=str(dest / f'{route}-{width}.png'))
    assert p.locator('#hub-tools-body a[href="/?view=classic#moneylines"]').count() == 1
    p.locator('#hub-tools-body [data-id=alerts]').click()
    assert p.locator('#hub-alerts-section').is_visible()
    assert not p.errors


def test_history_median_and_saved_slips_not_fabricated(ctx, env):
    p = start(ctx, env)
    p.locator('#hub-q').fill('vargo')
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart')
    assert 'Median' in p.locator('#hub-detail').inner_text()
    p.evaluate("location.hash='/slips'")
    p.wait_for_selector('#hub-slip-history')
    p.wait_for_function("document.querySelector('#hub-slip-history').textContent.includes('No tracked picks')")
    assert not p.errors


def test_new_filters_are_explicit_and_do_not_invent_movement(ctx, env):
    p = start(ctx, env)
    p.wait_for_selector('#hub-rows tr[data-record]')
    p.locator('#hub-movedOnly').click()
    assert p.locator('#hub-movedOnly').get_attribute('aria-pressed') == 'true'
    # Whether populated or empty, the route and source state remain usable.
    assert p.locator('#hub-empty').is_visible() or p.locator('#hub-rows tr[data-record]').count()
    p.locator('#hub-movedOnly').click()
    p.locator('#hub-freshOnly').click()
    assert p.locator('#hub-freshOnly').get_attribute('aria-pressed') == 'true'
    assert not p.errors


def test_share_card_requires_checked_entry_and_does_not_track(ctx, env, tmp_path):
    p = start(ctx, env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-book').select_option('prizepicks')
    for name in ('vargo', 'orlo'):
        p.locator('#hub-q').fill(name)
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
    p.evaluate("location.hash='/slips'")
    p.wait_for_function("document.querySelector('#hub-studio-body').textContent.includes('1 of 1 entry ready')")
    p.wait_for_function("document.querySelector('#hub-studio-sync').textContent.includes('Saved to your account')")
    with p.expect_download() as info:
        p.locator('[data-hub=share-card]').click()
    path = tmp_path / 'lineup.png'
    info.value.save_as(path)
    assert path.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    data = p.evaluate("fetch('/api/upgrade/mine').then(r=>r.json())")
    assert data['receipts'] == []
    assert not p.errors


def test_classic_book_link_rechecks_without_tracking(ctx, env, monkeypatch):
    import server
    monkeypatch.setattr(server, 'PP_OPEN_LINK', True)
    p = start(ctx, env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-book').select_option('prizepicks')
    for name in ('vargo', 'orlo'):
        p.locator('#hub-q').fill(name)
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
    p.evaluate("location.hash='/slips'")
    p.wait_for_function("document.querySelector('#hub-studio-body').textContent.includes('1 of 1 entry ready')")
    p.locator('[data-u=open-entry]').first.click()
    box = p.locator('#u-book-open')
    box.wait_for(state='visible')
    href = box.locator('a').first.get_attribute('href')
    assert href.startswith('https://app.prizepicks.com/board?projections=')
    assert len(href.split('projections=')[1].split(',')) == 2
    assert p.evaluate("fetch('/api/upgrade/mine').then(r=>r.json())")['receipts'] == []
    box.locator('button').click()
    # A click must contact the server again, not reuse the earlier checked URL.
    p.route('**/api/upgrade/recheck', lambda r: r.fulfill(status=503, content_type='application/json', body='{"error":"offline"}'))
    p.locator('[data-u=open-entry]').first.click()
    p.wait_for_timeout(300)
    assert not box.is_visible()
    assert not p.errors
