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

@pytest.mark.parametrize('width',[390,1440])
def test_history_visible_without_model_side(ctx,env,width):
    def patch(route):
        h=dict(route.request.headers);h.pop('if-none-match',None)
        response=route.fetch(headers=h)
        doc=response.json()
        for rec in doc.get('records',[]):
            rec['scout']['side']=None
            rec['history']={k:{'over':3,'under':1,'push':1,'n':5} for k in ('l5','l10','l20')}
        route.fulfill(response=response,json=doc)
    ctx.route('**/api/upgrade/board?*',patch)
    p=start(ctx,env,width=width)
    p.wait_for_selector('#hub-rows .h-hit-both')
    assert 'No side' not in p.locator('#hub-rows').inner_text()
    assert p.locator('#hub-rows .h-direction-options').count()>0
    both = p.locator('#hub-rows .h-hit-both').first          # no side: both counts, no side picked for the member
    assert both.inner_text() == '3-1' and both.get_attribute('title').startswith('3 Over, 1 Under, 1 pushes of the last 5')
    no_h_scroll(p)
    p.screenshot(path=f'.side-cards-{width}.png',full_page=True)
    assert not p.errors

@pytest.mark.parametrize('width',[390,1440])
def test_player_props_are_collapsed_and_expand_without_losing_research(ctx,env,width):
    p=start(ctx,env,width=width)
    p.locator('#hub-q').fill('vargo')
    p.wait_for_selector('#hub-rows .h-prop-toggle')
    initial=p.locator('#hub-rows tr[data-record]').count()
    toggle=p.locator('#hub-rows .h-prop-toggle').first
    assert toggle.get_attribute('aria-expanded')=='false'
    toggle.click()
    assert p.locator('#hub-rows tr[data-record]').count()>initial
    p.locator('#hub-rows .h-player').last.click()
    p.wait_for_selector('#hub-detail .h-history-controls')
    if width < 1101:                                           # phone: the card is a full-screen sheet; Back returns to the board
        p.locator('#hub-detail [data-hub=card-close]').click()
    p.locator('#hub-rows .h-prop-toggle').first.click()
    assert p.locator('#hub-rows tr[data-record]').count()==initial
    assert p.locator('#hub-card').is_hidden()
    no_h_scroll(p)
    assert not p.errors


def test_game_route_uses_live_fixture_records_and_scoped_board(ctx, env):
    p = start(ctx, env)
    p.locator('#hub-q').fill('not-a-player')
    p.locator('#hub-filters-toggle').click()
    p.locator('#hub-watch').click()
    p.locator('#hub-soonOnly').click()
    p.evaluate("location.hash='/games'")
    # By game is a view of the board: it keeps the board's filters and says how many games they hide
    p.wait_for_selector('#hub-games-body:not([hidden]) .h-empty')
    assert 'hidden by filters' in p.locator('#hub-games-body').inner_text()
    p.locator('#hub-games-body [data-hub=clear-filters]').click()
    p.wait_for_selector('.h-game-card')
    assert p.locator('#hub-board-section').is_visible() and p.locator('#hub-board-section [data-hub=view][data-id=games]').get_attribute('aria-pressed')=='true'
    card = p.locator('.h-game-card').first
    event = card.locator('h2').inner_text()
    card.locator('button').click()
    assert '#/board' in p.url
    p.wait_for_selector('#hub-rows tr[data-record]')
    assert p.locator('#hub-rows .h-match').all_text_contents() == [event]       # one game header: the board filtered to that game
    assert p.locator('#hub-rows [data-hub=game]').get_attribute('aria-pressed') == 'true'
    p.go_back()
    p.wait_for_selector('#hub-games-body:not([hidden])')
    assert not p.errors


@pytest.mark.parametrize('width', [360, 390, 640, 1024, 1025, 1100, 1440])
def test_all_routes_fit_and_preserve_original_tools(ctx, env, width):
    p = start(ctx, env, width=width)
    for route, sel in [('games','#hub-games-body'),('slips','#hub-studio-section'),('results','#hub-record-section'),('tools','#hub-tools-section')]:
        p.evaluate('(h)=>{location.hash=h}', '/'+route)
        p.wait_for_selector(sel+':not([hidden])')
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
    p.locator('[role=tab][data-hub=studio-tab][data-id=tracked]').click()
    p.wait_for_selector('#hub-slip-history')
    p.wait_for_function("document.querySelector('#hub-slip-history').textContent.includes('No tracked picks')")
    assert not p.errors


def test_new_filters_are_explicit_and_do_not_invent_movement(ctx, env):
    p = start(ctx, env)
    p.wait_for_selector('#hub-rows tr[data-record]')
    p.locator('#hub-filters-toggle').click()
    p.locator('#hub-movedOnly').click()
    assert p.locator('#hub-movedOnly').get_attribute('aria-pressed') == 'true'
    # Whether populated or empty, the route and source state remain usable.
    assert p.locator('#hub-empty').is_visible() or p.locator('#hub-rows tr[data-record]').count()
    p.locator('#hub-movedOnly').click()
    p.locator('#hub-freshOnly').click()
    assert p.locator('#hub-freshOnly').get_attribute('aria-pressed') == 'true'
    assert not p.errors


def test_saved_filters_keep_freshness_and_grade_after_reload(ctx,env):
    from tests.test_research_hub_browser import saved_state
    p=start(ctx,env)
    p.locator('#hub-filters-toggle').click()
    p.locator('#hub-freshOnly').click()
    p.locator('#hub-minGrade').select_option('B')
    p.locator('#hub-views-toggle').click()
    p.locator('[data-hub=save-view]').click()
    p.locator('#hub-save-name').fill('Fresh B and above')
    p.locator('#hub-save-confirm').click()
    saved_state(p,lambda d:len((d.get('prefs') or {}).get('researchHub',{}).get('views',[]))==1)
    p.reload();p.wait_for_selector('#hub-rows tr')
    p.locator('#hub-views-toggle').click()
    p.locator('#hub-views').select_option('0')
    assert p.locator('#hub-freshOnly').get_attribute('aria-pressed')=='true'
    assert p.locator('#hub-minGrade').input_value()=='B'


def test_share_card_requires_checked_entry_and_does_not_track(ctx, env, tmp_path):
    p = start(ctx, env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-platform [data-id=prizepicks]').click()
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
    p.locator('#hub-platform [data-id=prizepicks]').click()
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
    for width in (390, 1440):
        p.set_viewport_size({'width':width,'height':900})
        bounds = box.bounding_box()
        assert bounds['width'] <= min(520, width-32) + 1
        assert bounds['height'] < 450
        assert box.locator('a').first.bounding_box()['height'] < 100
        no_h_scroll(p)
    assert p.evaluate("fetch('/api/upgrade/mine').then(r=>r.json())")['receipts'] == []
    box.locator('button').click()
    # A click must contact the server again, not reuse the earlier checked URL.
    p.route('**/api/upgrade/recheck', lambda r: r.fulfill(status=503, content_type='application/json', body='{"error":"offline"}'))
    p.locator('[data-u=open-entry]').first.click()
    p.wait_for_timeout(300)
    assert not box.is_visible()
    assert not p.errors
