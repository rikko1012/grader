"""Owner site audit 2026-11-06 in a real browser (real Handler, fixture upstreams, the production flags server.DEFAULT_ON):
- B: the sport tabs All, CS2, LoL, NBA and NFL are all visible at 320 / 360 / 390 / 414 / 768 / 1440 - equal widths, 44px targets, no
  sideways strip, no page scroll sideways; the count badges never move a tab;
- A: the Market edge column on every sport (NBA / NFL: the books' chance minus the 2-pick break-even, with or without a Scout side, green
  only when positive; CS2 / LoL: "—" with the plain reason); the Scout Edge column stays;
- C: the best Over and Under across books in each row and on the card's Lines tab; the discrepancy chip where books differ by 1+.
MARKET_SHOTS=<folder> keeps a screenshot of every sport at every width."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import server
import workspace_books as wb
import workspace_ev as we
import workspace_market_edge as wme
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.workspace_other_fixture import forecast, iso, market, nba_board, nfl_board, quote

SHOTS = Path(os.environ['MARKET_SHOTS']) if os.environ.get('MARKET_SHOTS') else None
WIDTHS = (320, 360, 390, 414, 768, 1280, 1440)          # 1280: the narrow desktop where the Edge column steps aside
SPORTS = ('all', 'cs2', 'lol', 'nba', 'nfl')


def bk(p, line, over, under, mins=1):
    return {"p": p, "l": line, "kind": "devig", "c": None, "over": over, "under": under, "t": iso(datetime.now(timezone.utc) - timedelta(minutes=mins)),
            "su": None, "age": 60 * mins, "in": True, "why": None}


def boards():
    """The fixture NBA / NFL boards with fresh sportsbook rows: an Avdija-like row (no Scout side, two books), a two-book Karl Anders
    row, and an NFL row whose third book lists a line 2 yards higher (a discrepancy)."""
    doc, view = nba_board()
    towns = doc["markets"][0]
    towns["mk"]["bk"] = [bk("fanduel", 20.5, 1.91, 1.91), bk("betrivers", 20.5, 1.87, 1.95)]
    av = market("nba", "nba:espn:2", "nba:player:espn:305", "Deni Avdija", "F", "CHI", "nba:team:espn:CHI", "rebounds", [
        quote("underdog", "ud:av", 6.5, "fc-aaaaaaaa00000008", mult=[1.0, 1.0]), quote("prizepicks", "pp:av", 7.5, "fc-aaaaaaaa00000009")],
        mk={"l": 6.5, "hp": "underdog", "c": None, "bk": [bk("fanduel", 6.5, 1.80, 2.02), bk("betrivers", 6.5, 1.78, 2.05, 2)], "at": {}})
    doc["markets"].append(av)
    doc["fc"]["fc-aaaaaaaa00000008"] = forecast(0.49, 0.51, 6.5, line=6.5)
    doc["fc"]["fc-aaaaaaaa00000009"] = forecast(0.47, 0.53, 6.5, line=7.5)
    view["rows"][av["k"]] = {"lines": {"6.5": [6, 4, 0, 10, 3, 2, 0, 5], "7.5": [4, 6, 0, 10, 2, 3, 0, 5]}, "played": 12}
    ndoc, nview = nfl_board()
    ndoc["markets"][0]["mk"] = {"l": 264.5, "hp": "underdog", "c": None, "at": {},
                                "bk": [bk("fanduel", 264.5, 1.87, 1.95), bk("betrivers", 264.5, 1.85, 1.97), bk("oddsapi:draftkings", 266.5, 1.91, 1.91)]}
    return {"nba": (doc, view), "nfl": (ndoc, nview)}


@pytest.fixture(autouse=True)
def fixture_books_settle_like_the_app(monkeypatch):
    """The page test prices fixture books; which real books qualify is gradecore.book_rules' table (tests/test_ev_two_books.py)."""
    from tests.book_rules_fixture import like_the_app
    like_the_app(monkeypatch)


@pytest.fixture
def live(env, monkeypatch):
    for k in server.DEFAULT_ON:
        monkeypatch.setenv(k, '1')
    for k in ('SCOUT_BOOK_FANDUEL', 'SCOUT_BOOK_BETRIVERS', 'SCOUT_BOOK_SLEEPER', 'SCOUT_BOOK_DABBLE'):
        monkeypatch.delenv(k, raising=False)
    env.sports.boards.update(boards())
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()
    yield env
    wb.reset(); we._FAIR.clear(); we._LEGS.clear()


def shot(p, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(200)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


def open_hub(browser, env, width):
    c = context(browser, env, {"width": width, "height": 900 if width >= 768 else 844})
    p = c.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    p.ctx = c
    p.goto(env.url + '/#all-sports')
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && !document.querySelector('#hub-sports .h-n-wait')", timeout=30000)
    return p


def pick(p, s):
    p.locator(f'#hub-sports [data-id={s}]').click()
    if s != 'all':
        p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{s}|'))", timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-rows .h-medge-wait')", timeout=20000)


TABS = """() => { const g = document.querySelector('#hub-sports'), vw = document.documentElement.clientWidth;
  return {scroll: g.scrollWidth - g.clientWidth, tabs: [...g.querySelectorAll('button')].map(b => { const r = b.getBoundingClientRect();
    return {id: b.dataset.id, l: r.left, r: r.right, w: r.width, h: r.height, vis: r.left >= -0.5 && r.right <= vw + 0.5 && r.width > 0}; })}; }"""


@pytest.mark.parametrize("width", WIDTHS)
def test_sport_tabs_market_edge_and_best_lines_at_every_width(browser, live, width):
    p = open_hub(browser, live, width)
    try:
        t = p.evaluate(TABS)
        assert [x['id'] for x in t['tabs']] == list(SPORTS)
        assert t['scroll'] <= 1, t                                            # never a sideways strip
        assert all(x['vis'] for x in t['tabs']), t                           # every tab on screen
        assert all(x['w'] >= 44 - 0.5 and x['h'] >= 44 - 0.5 for x in t['tabs']), t
        ws = [x['w'] for x in t['tabs']]
        assert max(ws) - min(ws) <= 1.5, ws                                  # equal tap targets
        before = [round(x['l']) for x in t['tabs']]
        for s in SPORTS:
            pick(p, s)
            no_h_scroll(p)
            assert [round(x['l']) for x in p.evaluate(TABS)['tabs']] == before   # a count changing never moves a tab
            assert p.locator('#hub-table thead th.h-c-medge').count() == 1
            assert p.locator('#hub-table thead th.h-c-edge').count() == 1      # Scout's Edge column stays
            shot(p, f"board-{s}")
        # NBA: the Avdija-like row has no Scout side and still gets a market edge on the side the books favour
        pick(p, 'nba')
        p.locator('#hub-q').fill('Deni Avdija')
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record] .h-player b')].some(b => b.innerText === 'Deni Avdija')")
        if p.locator('#hub-rows .h-prop-toggle[aria-expanded=false]').count():
            p.locator('#hub-rows .h-prop-toggle').first.click()                # both lines of the player (PrizePicks 7.5, Underdog 6.5)
        p.wait_for_selector('#hub-rows tr[data-record^="nba|underdog|"]')
        row = p.locator('#hub-rows tr[data-record^="nba|underdog|"]', has_text='Deni Avdija').first
        cell = row.locator('td.h-c-medge')
        assert row.locator('.h-direction').count() == 0                       # no Scout side on this line
        assert cell.locator('.h-medge b').inner_text().startswith(('+', '−', '±'))
        assert cell.locator('.h-medge-side').first.inner_text() in ('Over', 'Under')
        title = cell.locator('.h-medge-wrap').get_attribute('title')
        assert 'an Underdog standard 2-pick needs 53.5%' in title and "not Scout’s grade" in title and 'BetRivers, FanDuel' in title
        if width >= 1281:      # wide desktop: both numbers in the Edge column, each labelled; the Market edge column steps aside
            books = row.locator('td.h-c-edge .h-edge-books')
            assert books.is_visible() and books.inner_text().startswith('BOOKS') and not cell.is_visible()
            assert row.locator('td.h-c-edge .h-edge').is_visible()                 # Scout's own edge stays first
        else:                  # 1101-1280: its own column; phone / tablet: its own labelled line on the card
            assert cell.is_visible() and not row.locator('td.h-c-edge .h-edge-books').is_visible()
        green = cell.locator('.h-medge').evaluate("e => e.classList.contains('positive')")
        assert green == (cell.locator('.h-medge b').inner_text().startswith('+'))
        # the discrepancy chip (PrizePicks lists 7.5, Underdog 6.5) and the best line per side in Compare
        assert row.locator('.h-gap').inner_text() == '≠ PrizePicks 7.5'
        best = row.locator('.h-compare .h-best').all_inner_texts()
        assert best[0].replace('\u2009', ' ').startswith('O 6.5 UD') and best[1].replace('\u2009', ' ').startswith('U 7.5 PP'), best
        # NFL: DraftKings lists 266.5 against 264.5 elsewhere
        p.locator('#hub-q').fill('')
        pick(p, 'nfl')
        nrow = p.locator('#hub-rows tr[data-record^="nfl|underdog|"]', has_text='Sam Field').first
        assert nrow.locator('.h-gap').inner_text() == '≠ DraftKings 266.5'
        assert nrow.locator('td.h-c-medge .h-medge b').count() == 1
        # CS2 / LoL: no sportsbook prices player props, so "—" with the plain reason (desktop) or no line (phone card)
        for s in ('cs2', 'lol'):
            pick(p, s)
            c = p.locator('#hub-rows tr[data-record] td.h-c-medge').first
            if width >= 1101:
                assert c.inner_text().strip() == '—' and c.locator('.h-medge').get_attribute('title') == wme.ESPORTS_WHY
            else:
                assert not c.is_visible()
        # the card's Lines tab: best line per side and the market edge, never worded as a grade
        pick(p, 'nba')
        p.locator('#hub-q').fill('Deni Avdija')
        if p.locator('#hub-rows .h-prop-toggle[aria-expanded=false]').count():
            p.locator('#hub-rows .h-prop-toggle').first.click()
        p.wait_for_selector('#hub-rows tr[data-record^="nba|underdog|"]')
        p.locator('#hub-rows tr[data-record^="nba|underdog|"] .h-compare').first.click()
        p.wait_for_selector('#hub-detail .h-best-lines', timeout=20000)
        txt = p.locator('#hub-detail .h-best-lines').inner_text()
        assert 'Best line per side' in txt and 'Market edge' in txt and "The books’ number, not Scout’s grade." in txt
        assert p.locator('#hub-detail .h-best-lines tbody tr').count() == 2 and p.locator('#hub-detail .h-best-lines .h-gap').count() == 1
        no_h_scroll(p)
        shot(p, 'card-lines-nba')
        for word in ('no bet', 'questionable', 'day-to-day', 'undefined', 'NaN'):
            assert word not in p.locator('#u-hub').inner_text(), word
        assert not p.errors, p.errors
    finally:
        p.ctx.close()
