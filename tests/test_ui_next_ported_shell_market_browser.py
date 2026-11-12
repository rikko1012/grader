"""Ported to the redesign (html[data-ui=next], the default since 2026-11-11) from the suites written for the design before it:
tests/test_hub_shell_browser.py (Price check on Tools), tests/test_market_edge_browser.py (sport tabs, Market edge, best lines),
tests/test_pickem_ev_browser.py (Best prices), tests/test_phase4_browser.py (Alerts wording), tests/test_slip_header_v2_browser.py
(rated slip header, the leg below break-even) and tests/test_ui_1109_browser.py (slip check time, sidebar foot). The originals stay
as they are and keep covering the rollback design (UI_NEXT=0); these run the same member functions, numbers and words on the new
one. Real Handler + the isolated fixtures of tests/test_upgrade_browser.py: no network, no production data. Display only.
Differences the ports allow for: headings and some labels are upper-cased by CSS (compared without case), one board row per
player + stat with a ladder of its lines, desktop controls 30-32px high and phone controls 44px."""
import re

import pytest

import workspace_alerts
import workspace_market_edge as wme
from tests.test_hub_shell_browser import JARGON as MT_JARGON, _aged, market_doc, market_rows, mt_page
from tests.test_market_edge_browser import SPORTS, TABS, live, pick  # noqa: F401  (live is a fixture)
from tests.test_pickem_ev_browser import JARGON as EV_JARGON, ev_on, ev_page  # noqa: F401  (ev_on is a fixture)
from tests.test_research_hub_browser import hub as research_hub
from tests.test_slip_header_v2_browser import JARGON as SLIP_JARGON, build_pair
from tests.test_ui_next_browser import nxt
from tests.test_upgrade_browser import DESK, PHONE, browser, env, no_h_scroll, open_page  # noqa: F401  (fixtures)


@pytest.fixture
def ctx(browser, env):
    """A member on the redesign: the legacy helpers that take ctx run on the new design."""
    c = nxt(browser, env)
    yield c
    c.close()


def is_next(p):
    assert p.locator("html").get_attribute("data-ui") == "next"


def low(loc):
    return loc.inner_text().lower()


# ------------------------------------------------------------------------------------------------ Alerts (test_phase4_browser)

def test_alert_me_with_background_delivery_on_the_new_design(ctx, env, monkeypatch):
    monkeypatch.setattr(workspace_alerts, 'configured', lambda: True)
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    p = open_page(ctx, env, '/#/alerts')
    p.wait_for_selector('#hub-alerts-body h2')
    is_next(p)
    assert p.locator('[data-hub="save-alert"]').first.text_content() == 'Alert me on these filters'
    assert 'Whop notifications' in p.locator('#hub-alerts-body').inner_text()
    assert 'Live highlight · this visit' in p.locator('#hub-alerts-body').text_content()
    assert 'live highlight · this visit' in low(p.locator('#hub-alerts-body'))                # on screen (upper-cased by CSS)
    assert 'line alerts' not in low(p.locator('#u-hub'))
    assert not p.errors


# ------------------------------------------------------------------------------------------------ Price check (test_hub_shell_browser)

@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_market_tools_ev_and_arbitrage_sheet_on_tools(browser, env, monkeypatch, size):
    """Price check on Tools: two plain sections, money per $100, a stake box, no jargon and no status chips for members."""
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    c = nxt(browser, env, size)
    try:
        p = mt_page(c, env, market_doc())
        is_next(p)
        assert 'price check' in low(p.locator('#hub-tools-body'))
        assert p.locator('#hub-market-tools h2').text_content() == 'Price check' and p.locator('#hub-market-tools h2').is_visible()
        assert market_rows(p) == 4                                              # 3 single bets (NBA 2, NFL 1) + 1 both-sides pair
        titles = p.locator('#hub-market-results .h-pc-section h3').all_text_contents()
        assert titles == ['Prices better than the fair chance', 'Bet both sides at two books']
        assert all(h.is_visible() for h in p.locator('#hub-market-results .h-pc-section h3').all())
        heads = p.locator('#hub-market-results .h-pc-ev .h-pc-head .h-pc-cell').all_text_contents()
        assert heads == ['Pick', 'Book & price', 'Fair chance vs needed', 'Expected per $100', 'Checked']
        arb_heads = p.locator('#hub-market-results .h-pc-arbs .h-pc-head .h-pc-cell').all_text_contents()
        assert arb_heads == ['Pick', 'Bet 1', 'Bet 2', 'Either way you get', 'Checked']
        text = p.locator('#hub-market-results').inner_text()
        assert '+$6.40' in text and '+$5.00' in text and '+$1.40' in text            # expected per $100, best first
        assert 'Alpha +110' in text and '(2.10)' not in text                          # American price only: no unexplained decimal
        assert '50.0%' in text and '47.6%' in text                                    # fair chance vs the chance the price needs
        assert '$48.67' in text and '$51.33' in text and '$101.24 back' in text and '(+$1.24)' in text
        assert 'big cut' not in text and 'not a sure win' not in text and 'Prices move fast' not in text   # no hedge lines
        assert ('Gamma pays -111 on Over 24.5, which needs a 52.6% chance to break even. The fair chance is 56.0%, so over many bets '
                'like this you would average about +$6.40 per $100.') in text                  # the worked example uses the best row
        assert 'Beta Under 24.5 +105' in text and '(2.05)' not in text
        assert ('Put $48.67 on Over at Alpha +108 and $51.33 on Under at Beta +105: either result pays about $101.24 for $100.00 staked.') in text
        for word in MT_JARGON:
            assert word.lower() not in text.lower(), word
        assert p.locator('#hub-market-results .h-flag, details.h-mt-flagged, details.h-mt-owner').count() == 0
        assert 'guaranteed' not in text.lower() and 'not bet placement' not in text.lower()
        # the stake box only scales the server's numbers
        p.locator('#hub-mt-stake').fill('250')
        p.wait_for_function("document.querySelector('#hub-market-results').innerText.includes('$253.10 back')")
        t250 = p.locator('#hub-market-results').inner_text()
        assert '$121.6' in t250 and p.evaluate("document.activeElement.id") == 'hub-mt-stake'
        assert 'for $250.00 staked' in t250 and 'pays about $253.10' in t250 and 'for $100.00 staked' not in t250   # the example follows the box
        assert t250.count('$121.67') == 2 and t250.count('$128.32') == 2
        # filters: Search, Sport and Book on the bar; the rest under More filters
        p.locator('#hub-mt-book').select_option('gamma')
        assert 'Nfl Back' in p.locator('#hub-market-results').inner_text() and 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-book').select_option('all')
        assert p.locator('#hub-mt-minEv').is_hidden()
        p.locator('details.h-mt-more > summary').click()
        p.locator('#hub-mt-minEv').select_option('0.02')
        assert 'Nba Wing' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-minEv').select_option('0')
        p.locator('#hub-mt-fresh').select_option('120')
        assert 'Nba Wing' not in p.locator('#hub-market-results').inner_text() and 'Nba Guard' in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-fresh').select_option('all')
        assert p.locator('#hub-mt-sort option').all_text_contents() == ['Best value', 'Highest fair chance', 'Newest check']
        p.locator('#hub-mt-sort').select_option('chance')
        names = p.locator('#hub-market-results .h-pc-ev .h-pc-row:not(.h-pc-head) .h-pc-pick b').all_text_contents()
        assert names == ['Nfl Back', 'Nba Wing', 'Nba Guard']                    # 56%, 52%, 50%
        p.locator('#hub-mt-sort').select_option('ev')
        p.locator('#hub-mt-league').select_option('nfl')
        assert 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        assert p.locator('#hub-mt-book option').evaluate_all('os=>os.map(o=>o.value)') == ['all', 'gamma']   # books follow the sport
        p.locator('#hub-mt-league').select_option('all')
        p.locator('#hub-mt-q').fill('center')
        assert p.evaluate("document.activeElement.id") == 'hub-mt-q'
        assert 'Nba Center' in p.locator('#hub-market-results').inner_text() and 'Nba Guard' not in p.locator('#hub-market-results').inner_text()
        p.locator('#hub-mt-q').fill('')
        dense = p.locator('#hub-mt-dense')
        dense.click()
        assert dense.get_attribute('aria-pressed') == 'true' and p.locator('.h-pc-list.is-compact').count() == 2
        need = 40 if size['width'] < 640 else 30                                 # the new design's control heights
        hs = p.locator('#hub-market-filters :is(select,input,button,summary)').evaluate_all(
            "es=>es.filter(e=>e.getBoundingClientRect().height>0).map(e=>[e.id||e.tagName,e.getBoundingClientRect().height])")
        assert hs and all(h >= need - 0.5 for _, h in hs), hs
        assert p.locator('.h-market-table').count() == 0                         # no 720px table: rows are cards on a phone
        if size['width'] < 640:
            assert p.locator('#hub-market-results .h-pc-head').first.is_hidden()
            chip = p.locator('#hub-market-results .h-pc-ev .h-pc-row:not(.h-pc-head) .h-pc-chip').first.bounding_box()
            assert chip and chip['x'] + chip['width'] <= size['width']
            assert 'Fair 50.0% · needs 47.6%' in p.locator('#hub-market-results').inner_text()
        marks = p.locator('#hub-market-tools summary').evaluate_all(
            "ss=>ss.filter(s=>s.getBoundingClientRect().height>0).map(s=>[s.textContent.trim(),s.getBoundingClientRect().height])")
        assert any(t.startswith('More filters') for t, _ in marks) and any(t.startswith('How these numbers') for t, _ in marks)
        assert all(h >= need - 0.5 for _, h in marks), marks
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_market_tools_drop_rows_past_the_servers_age_limits_on_the_new_design(ctx, env, monkeypatch):
    """A sheet left open applies the server's own limits on every draw (both prices within arb_max_age_s, a price within ev_stale_s)."""
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS', '1')
    p = mt_page(ctx, env, _aged(market_doc(age_built=1500), arb_age=1500))
    is_next(p)
    res = p.locator('#hub-market-results')
    text = low(res)
    assert 'bet both sides at two books' not in text and 'nba center' not in text and 'either result pays' not in text
    assert 'prices better than the fair chance' in text and market_rows(p) == 3          # the single bets are 25 min old: still in
    p.unroute('**/api/upgrade/market-tools')
    p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(json=_aged(market_doc(age_built=2000), ev_age=2000, arb_age=2000)))
    p.locator('[data-hub="refresh-markets"]').click()
    p.wait_for_function("!document.querySelector('#hub-market-results').textContent.includes('Prices better than the fair chance')")
    assert market_rows(p) == 0 and 'Nothing worth acting on right now' in res.inner_text()
    # a fresh pair that ages on screen drops off on the next redraw (the page redraws once a minute), with no new request
    p.unroute('**/api/upgrade/market-tools')
    p.route('**/api/upgrade/market-tools', lambda r: r.fulfill(json=market_doc()))
    p.locator('[data-hub="refresh-markets"]').click()
    p.wait_for_function("document.querySelector('#hub-market-results').textContent.includes('Bet both sides at two books')")
    p.evaluate("""() => { const real = Date.now; Date.now = () => real() + 400000; }""")         # 6 min 40 s later
    p.evaluate("""() => { document.getElementById('hub-market-filters').querySelector('#hub-mt-league').dispatchEvent(new Event('change', {bubbles: true})); }""")
    p.wait_for_function("!document.querySelector('#hub-market-results').textContent.includes('Bet both sides at two books')")
    assert not p.errors


# ------------------------------------------------------------------------------------------------ slip header (test_slip_header_v2_browser)

def test_v2_header_rated_entry_plain_words_and_red_line_below_break_even_on_the_new_design(ctx, env, monkeypatch):
    """A rated answer in plain words, and the leg under its break-even: named in words and marked with its red line. On the
    redesign the slip rule removed every leg's box-shadow, the red line with it (fixed in upgrade-next.css 2026-11-12)."""
    monkeypatch.setenv("SCOUT_SLIP_HEADER_V2", "1")
    p = research_hub(ctx, env)
    is_next(p)

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
    assert all(w.lower() in text.lower() for w in ("Payout", "Chance all hit", "Average return", "36.6%", "+28.1%"))
    assert body.locator(".u-head2 .u-slip-metrics>div").count() == 3 and not [w for w in SLIP_JARGON if w.lower() in text.lower()]
    assert body.locator(".u-leg.u-leg-below").count() == 1 and "below the 53.5% each leg needs" in text.lower()
    marks = p.evaluate("""() => [...document.querySelectorAll('#hub-studio-body .u-leg')].map(l => [l.classList.contains('u-leg-below'), getComputedStyle(l).boxShadow])""")
    assert len(marks) == 2
    for below, shadow in marks:                                   # the red line on the leg below its break-even, and only there
        assert ("inset" in shadow) is below, marks
    assert not p.errors


# ------------------------------------------------------------------------------------------------ slip / sidebar foot (test_ui_1109_browser)

ONE_LINE = """() => { const w = document.createTreeWalker(document.getElementById('hub-slip-content'), NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) if (/Checked \\d/.test(n.textContent)) { const r = document.createRange(); r.selectNodeContents(n);
    return new Set([...r.getClientRects()].map(x => Math.round(x.top))).size; } return -1; }"""
FOOT = """() => [...document.querySelectorAll('#u-hub .h-sidebar-foot :is(button,a)')].map(e => { const r = e.getBoundingClientRect(), t = document.createRange();
  t.selectNodeContents(e); return {text: e.textContent.trim(), h: r.height, w: r.width, lines: new Set([...t.getClientRects()].map(x => Math.round(x.top))).size,
    inside: r.left >= 0 && r.bottom <= innerHeight && e.scrollWidth <= e.clientWidth + 1}; })"""


@pytest.mark.parametrize("w", [1280, 1440])
def test_slip_check_time_and_sidebar_foot_buttons_stay_on_one_line(browser, env, w):
    size = {"width": w, "height": 900}
    c = nxt(browser, env, size)
    try:
        p = open_page(c, env, "/#all-sports", size)
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        is_next(p)
        p.locator("#hub-rows tr[data-record] [data-hub=add]:not(.h-add-info):not([disabled])").first.click()
        p.wait_for_function("/Checked \\d/.test(document.getElementById('hub-slip-content').textContent)", timeout=20000)
        assert p.evaluate(ONE_LINE) == 1
        # the foot now holds the theme button, the way back to the previous design and Classic board: each on one line, on screen
        foot = p.evaluate(FOOT)
        assert [f["text"] for f in foot] == ["Light theme", "Previous design", "Classic board ↗"], foot
        assert all(24 <= f["h"] <= 44 and f["lines"] == 1 and f["inside"] for f in foot), foot
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Best prices (test_pickem_ev_browser)

EV_SMALL = """(need) => [...document.querySelectorAll('#hub-ev-body button, #hub-ev-body select, #hub-ev-body input, #hub-ev-body summary, #hub-ev-body .ev-req label, .h-ev-link')]
  .filter(e => e.offsetParent !== null && e.type !== 'checkbox').map(e => [e.id || e.className || e.textContent.trim().slice(0, 30), e.getBoundingClientRect().height])
  .filter(([, h]) => h < need - 0.5)"""


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_ev_board_on_the_new_design(browser, env, ev_on, size):
    c = nxt(browser, env, size)
    try:
        p = ev_page(c, env, size)
        is_next(p)
        assert p.locator('#hub-ev-section').is_visible()
        assert p.locator('[data-ev="app"]').count() == 2 and p.locator('[data-ev="app"][data-id="prizepicks"]').count() == 1
        assert 'need 57.7%' in p.locator('#hub-ev-body').inner_html()
        assert p.evaluate("document.querySelector('#ev-entry').value").startswith('power-')
        assert 'Flex 2-pick' not in p.locator('#ev-entry').inner_text()          # not an entry the PrizePicks app offers
        p.locator('[data-ev="app"][data-id="underdog"]').click()
        p.wait_for_function("document.querySelector('#hub-ev-body').innerHTML.includes('need 53.5%')", timeout=30000)
        p.wait_for_selector('#hub-ev-body .ev-row', timeout=30000)
        txt = p.locator('#hub-ev-body').inner_text()
        assert 'best prices' in txt.lower() and 'need 53.5%' in p.locator('#hub-ev-body').inner_html()
        assert '2 plays hidden: 2 with only old prices.' in txt
        assert p.evaluate("document.querySelector('#ev-entry').value").startswith('standard-')
        rows = p.locator('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row')
        assert rows.count() >= 1 and 'Austin Reaves' in rows.first.inner_text()
        assert not any(w in txt.lower() for w in ('research estimate', 'provisional', 'not validated', 'lock of'))
        rows.first.locator('[data-ev="books"]').click()
        p.wait_for_selector('#hub-ev-body .ev-src table')
        src = p.locator('#hub-ev-body .ev-src').first.inner_text()
        assert 'FanDuel' in src and 'BetRivers' in src and 'read at 20.5' in src
        p.locator('#hub-ev-body .ev-drawer summary').click()
        assert p.locator('#ev-preset').count() == 1 and p.locator('#ev-method').count() == 0 and p.locator('#ev-min_books').count() == 1
        assert p.locator('[data-ev-req]').count() >= 2 and p.locator('#ev-lo').count() == 1
        assert p.evaluate(EV_SMALL, 40 if size['width'] < 640 else 30) == []       # every control is a full target
        lowtxt = p.locator('#hub-ev-body').inner_text().lower()
        assert not [w for w in EV_JARGON if w in lowtxt], [w for w in EV_JARGON if w in lowtxt]
        p.locator('[data-ev="evonly"]').first.click()
        p.wait_for_function("document.querySelectorAll('#hub-ev-body .ev-list:not(.ev-list-model) .ev-row').length >= 3")
        p.locator('[data-ev="model"]').click()
        p.wait_for_selector('#hub-ev-body .ev-list-model .ev-row')
        assert 'no book price' in p.locator('#hub-ev-body .ev-list-model').inner_text().lower()
        assert 'LeBron James' in p.locator('#hub-ev-body .ev-list-model').inner_text()
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Market edge (test_market_edge_browser)

WIDTHS = (320, 360, 390, 414, 768, 1280, 1440)


def open_hub_next(browser, env, width):
    c = nxt(browser, env, {"width": width, "height": 900 if width >= 768 else 844})
    p = c.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    p.ctx = c
    p.goto(env.url + '/#all-sports')
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && !document.querySelector('#hub-sports .h-n-wait')", timeout=30000)
    is_next(p)
    return p


def find_player(p, name):
    p.locator('#hub-q').fill(name)
    p.wait_for_function("(n) => { const rows = [...document.querySelectorAll('#hub-rows tr[data-record]')]; return rows.length > 0 && rows.every(r => r.closest('tbody').textContent.includes(n)); }", arg=name)


@pytest.mark.parametrize("width", WIDTHS)
def test_sport_tabs_market_edge_and_best_lines_at_every_width_on_the_new_design(browser, live, width):
    p = open_hub_next(browser, live, width)
    try:
        t = p.evaluate(TABS)
        assert [x['id'] for x in t['tabs']] == list(SPORTS)
        assert t['scroll'] <= 1, t                                            # never a sideways strip
        assert all(x['vis'] for x in t['tabs']), t                           # every tab on screen
        tall = 44 if width <= 1100 else 30                                    # phone / tablet targets; the desktop's compact tabs
        assert all(x['w'] >= 44 - 0.5 and x['h'] >= tall - 0.5 for x in t['tabs']), t
        ws = [x['w'] for x in t['tabs']]
        assert max(ws) - min(ws) <= 1.5, ws                                  # equal tap targets
        before = [round(x['l']) for x in t['tabs']]
        for s in SPORTS:
            pick(p, s)
            no_h_scroll(p)
            assert [round(x['l']) for x in p.evaluate(TABS)['tabs']] == before   # a count changing never moves a tab
            assert p.locator('#hub-table thead th.h-c-medge').count() == 1
            assert p.locator('#hub-table thead th.h-c-edge').count() == 1      # Scout's Edge column stays
        # NBA, Deni Avdija rebounds: one board row for the prop (PrizePicks 7.5, the line with a Scout side) and a ladder with both lines
        pick(p, 'nba')
        find_player(p, 'Deni Avdija')
        rows = p.locator('#hub-rows tr.h-row')
        assert rows.count() == 1 and rows.first.get_attribute('data-record').startswith('nba|prizepicks|')
        assert rows.first.locator('.h-gap').inner_text() == '≠ Underdog 6.5'  # the discrepancy chip names the other app's line
        btn = rows.first.locator('[data-hub=ladder]')
        assert btn.is_visible() and '+1 line' in btn.inner_text() and '2 apps · 6.5–7.5' in btn.inner_text()
        btn.click()
        lad = p.locator('#hub-rows tr.h-ladder-row')
        ud = lad.locator('tr[data-line^="nba|underdog|"]')
        pp = lad.locator('tr[data-line^="nba|prizepicks|"]')
        assert lad.locator('tr[data-line]').count() == 2 and ud.count() == 1 and pp.count() == 1
        assert ud.locator('.h-ld-line b').inner_text() == '6.5' and pp.locator('.h-ld-line b').inner_text() == '7.5'
        assert ud.locator('.h-ld-both').inner_text() == 'O 49.0% · U 51.0%'    # no Scout side on the Underdog line: both chances
        assert pp.locator('.h-ld-best').text_content() == 'Best Under' and pp.locator('.h-ld-best').is_visible()   # the highest line
        assert pp.locator('.h-ld-p').inner_text() == '53.0%' and pp.locator('.h-ld-side').text_content().endswith('Under')
        btn.click()
        # the Underdog line as the row (Platform filter): no Scout side, and still a market edge on the side the books favour
        p.locator('#hub-platform [data-id=underdog]').click()
        p.wait_for_selector('#hub-rows tr.h-row[data-record^="nba|underdog|"]')
        row = p.locator('#hub-rows tr.h-row[data-record^="nba|underdog|"]').first
        cell = row.locator('td.h-c-medge')
        assert row.locator('.h-direction').count() == 0
        assert cell.locator('.h-medge b').text_content().startswith(('+', '−', '±'))
        assert cell.locator('.h-medge-side').first.text_content() in ('Over', 'Under')
        title = cell.locator('.h-medge-wrap').get_attribute('title')
        assert 'an Underdog standard 2-pick needs 53.5%' in title and "not Scout’s grade" in title and 'BetRivers, FanDuel' in title
        green = cell.locator('.h-medge').evaluate("e => e.classList.contains('positive')")
        assert green == (cell.locator('.h-medge b').text_content().startswith('+'))
        if width >= 1281:      # wide desktop: Scout's edge first, the books' number labelled under it (its own column from 1700px)
            books = row.locator('td.h-c-edge .h-edge-books')
            assert books.is_visible() and books.inner_text().upper().startswith('BOOKS')
            assert row.locator('td.h-c-edge .h-edge').is_visible()
            assert cell.locator('.h-medge b').text_content().replace('−', '-').lstrip('±') in books.inner_text().replace('−', '-')   # one number, both places
        assert row.locator('.h-gap').inner_text() == '≠ PrizePicks 7.5'
        # NFL: DraftKings lists 266.5 against 264.5 elsewhere
        p.locator('#hub-q').fill('')
        pick(p, 'nfl')
        nrow = p.locator('#hub-rows tr.h-row[data-record^="nfl|underdog|"]', has_text='Sam Field').first
        assert nrow.locator('.h-gap').inner_text() == '≠ DraftKings 266.5'
        assert nrow.locator('td.h-c-medge .h-medge b').count() == 1
        p.locator('#hub-platform [data-id=all]').click()
        # CS2 / LoL: no sportsbook prices player props, so "—" with the plain reason
        for s in ('cs2', 'lol'):
            pick(p, s)
            c = p.locator('#hub-rows tr[data-record] td.h-c-medge').first
            assert c.text_content().strip() == '—' and c.locator('.h-medge').get_attribute('title') == wme.ESPORTS_WHY
        # the card's Lines tab (every width): best line per side and the market edge, never worded as a grade
        pick(p, 'nba')
        find_player(p, 'Deni Avdija')
        opener = p.locator('#hub-rows tr.h-row .h-player').first            # By player rows: the row's Card button
        box = opener.bounding_box()
        assert box and box['width'] >= 44 - 0.5 and box['height'] >= (44 if width <= 1100 else 24) - 0.5, box
        opener.click()
        p.wait_for_selector('#hub-detail .h-card-head', timeout=20000)
        p.locator('#hub-detail [data-hub=card-tab][data-id=lines]').click()
        p.wait_for_selector('#hub-detail .h-best-lines', timeout=20000)
        best = p.locator('#hub-detail .h-best-lines')
        txt = best.inner_text()
        assert 'best line per side' in txt.lower() and 'market edge' in txt.lower() and "The books’ number, not Scout’s grade." in txt
        assert best.locator('tbody tr').count() == 2 and best.locator('.h-gap').count() == 1
        sides = [re.sub(r'\s+', ' ', x).strip() for x in best.locator('tbody tr').all_text_contents()]
        assert sides[0].startswith('Over') and '6.5 Underdog' in sides[0] and '53.5%' in sides[0], sides      # lowest line for the Over
        assert sides[1].startswith('Under') and '7.5 PrizePicks' in sides[1] and '57.7%' in sides[1], sides   # highest for the Under
        assert re.search(r'Under \+1\.6 pts the books give the Under 59\.4% \(BetRivers, FanDuel\)', re.sub(r'\s+', ' ', best.text_content()))
        no_h_scroll(p)
        seen = p.locator('#u-hub').inner_text()
        for word in ('no bet', 'questionable', 'day-to-day'):
            assert word not in seen.lower(), word
        for word in ('undefined', 'NaN'):
            assert word not in seen, word
        assert not p.errors, p.errors
    finally:
        p.ctx.close()


LONG_SCOUT = """() => [...document.querySelectorAll('#hub-rows tr.h-row')].map(r => { const b = e => e.getBoundingClientRect();
  const pl = b(r.querySelector('.h-player')), sc = b(r.querySelector('td.h-c-scout')), mk = r.querySelector('td.h-c-market'), name = b(mk.querySelector('b')), row = b(r);
  const hit = (a, c) => a.left < c.right - 0.5 && a.right > c.left + 0.5 && a.top < c.bottom - 0.5 && a.bottom > c.top + 0.5;
  return {k: r.dataset.record, card: [Math.round(pl.width), Math.round(pl.height)], market: Math.round(b(mk).width), name: Math.round(name.width),
          nameOverScout: hit(name, sc) && sc.width > 0, cardOverScout: hit(pl, sc), scoutPast: Math.round(sc.right - row.right), long: !!r.querySelector('.h-scout .h-below-alt, .h-scout .h-below')}; })"""


@pytest.mark.parametrize("width", [320, 360, 390, 768])
@pytest.mark.parametrize("mode", ["player", "prop"])
def test_phone_rows_keep_the_card_button_and_the_stat_name_beside_a_long_scout_cell(browser, live, width, mode):
    """Found porting the market edge test (fixed in upgrade-next.css 2026-11-12): a row whose Scout cell is long (side, chance,
    Research estimate, Below break-even) left its first column 0-20px. By player that column is the row's Card button; By prop it
    is the stat name, which was printed over the Scout side at 320-360px."""
    c = nxt(browser, live, {"width": width, "height": 900 if width >= 768 else 844})
    c.add_init_script("try{localStorage.setItem('scout-board-group','%s')}catch(e){}" % mode)
    try:
        p = c.new_page()
        p.errors = []
        p.on('pageerror', lambda e: p.errors.append(str(e)))
        p.goto(live.url + '/#all-sports')
        p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
        p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && !document.querySelector('#hub-sports .h-n-wait')", timeout=30000)
        is_next(p)
        assert p.locator('#hub-table').evaluate("t => t.classList.contains('h-by-player')") is (mode == 'player')
        rows = p.evaluate(LONG_SCOUT)
        assert len(rows) >= 10 and any(r['long'] for r in rows), rows          # the fixture has the long rows (NBA research lines)
        for r in rows:
            assert r['card'][0] >= 44 and r['card'][1] >= 44 and not r['cardOverScout'], r      # the way to the card is a full target
            assert r['market'] >= 90 and r['name'] > 0 and not r['nameOverScout'], r            # the stat name has its own room
            assert r['scoutPast'] <= 0, r                                                         # the Scout cell stays inside the row
        long_row = next(r['k'] for r in rows if r['long'])
        p.locator(f'#hub-rows tr.h-row[data-record="{long_row}"] .h-player').click()
        p.wait_for_selector('#hub-detail .h-card-head', timeout=20000)
        assert p.locator('#hub-card').is_visible()
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


LINK = """() => {const e=document.querySelector('.h-ev-link'), r=e.getBoundingClientRect(), s=getComputedStyle(e);
  const o=[...document.querySelectorAll('#u-hub .h-toolbar :is(.h-filter-toggle,.h-refresh)')].filter(x=>x.offsetParent).map(x=>x.getBoundingClientRect().height);
  return {h: r.height, w: r.width, right: r.right, left: r.left, vw: innerWidth, text: e.textContent.trim(), border: s.borderTopStyle, others: o}}"""


@pytest.mark.parametrize('size', [DESK, PHONE], ids=['desktop', 'phone'])
def test_best_prices_link_is_styled_sized_fits_and_opens_ev(browser, env, ev_on, size):
    """The toolbar's Best prices link: bordered, on screen, a full target (32px desktop; 44px on a phone like the buttons beside it,
    it stayed 32px there until 2026-11-12), and it opens the EV screen."""
    c = nxt(browser, env, size)
    try:
        p = open_page(c, env, '/#scout/board?sport=cs2', size)
        p.wait_for_selector('.h-ev-link', timeout=30000)
        is_next(p)
        box = p.evaluate(LINK)
        need = 44 if size['width'] <= 1100 else 30
        assert box['h'] >= need - 0.5 and box['w'] >= 40 and box['left'] >= 0 and box['right'] <= box['vw'], box
        assert box['others'] and all(abs(h - box['h']) <= 0.5 for h in box['others']), box       # the same height as Filters / Refresh
        assert box['text'] == 'Best prices' and box['border'] == 'solid', box
        no_h_scroll(p)
        p.locator('.h-ev-link').click()
        p.wait_for_selector('#hub-ev-body .ev-row', timeout=30000)
        assert p.locator('#hub-ev-section').is_visible() and p.evaluate('location.hash').endswith('ev')
        assert not p.errors
    finally:
        c.close()
