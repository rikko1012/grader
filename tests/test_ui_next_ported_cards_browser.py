"""Ported to the redesign (html[data-ui=next], the default since 2026-11-11): the player-card and phone hit-window checks of
tests/test_card_and_chips_browser.py, test_card_factors_browser.py, test_card_why_browser.py, test_line_history_browser.py and
test_phone_hit_chips_browser.py that failed when those suites ran on the new design (tests/UI_NEXT_TRIAGE.md). The legacy tests stay
as they are and keep covering the rollback design (UI_NEXT=0); here the same fixtures and helpers run with UI_NEXT unset, so every
page is the redesign (each test asserts it). What differs from the legacy checks, and only that:
- headings are upper-cased by CSS, so titles are read with textContent or compared case-insensitively;
- desktop: the card sits beside the board in the slip's column and the slip is hidden while a card is open (it comes back, with its
  legs, when the card closes);
- phone: the five hit windows are one line of equal columns inside the card, label above value (the redesign's sizes).
Display only: no grade, side, chance, stake or projection is touched."""
import re

import pytest

import tests.test_card_and_chips_browser as cc
import tests.test_card_factors_browser as cf
import tests.test_card_why_browser as cw
import tests.test_line_history_browser as lhb
import tests.test_phone_hit_chips_browser as ph
from tests.test_card_and_chips_browser import rich  # noqa: F401 (fixture)
from tests.test_phone_hit_chips_browser import live  # noqa: F401 (fixture)
from tests.test_upgrade_browser import browser, env, ctx, context, no_h_scroll, DESK, PHONE  # noqa: F401 (fixtures)

PHONES = ph.PHONES


@pytest.fixture(autouse=True)
def on_next(env, monkeypatch):
    """the redesign is the default: the legacy fixture's rollback switch is taken away"""
    monkeypatch.delenv("UI_NEXT", raising=False)


@pytest.fixture
def card_flags(monkeypatch):
    """the flags the legacy card-why and card-factors modules turn on for every test of theirs"""
    assert cf.FLAGS == cw.FLAGS
    for k in cf.FLAGS:
        monkeypatch.setenv(k, "1")


def is_next(p):
    assert p.locator("html").get_attribute("data-ui") == "next"
    return p


def opens_next(monkeypatch, mod, name):
    """the legacy helper that opens the board, with the design asserted on the page it returns"""
    real = getattr(mod, name)
    monkeypatch.setattr(mod, name, lambda *a, **k: is_next(real(*a, **k)))


# headings are upper-cased by CSS on the redesign: read the words as written (textContent), not as painted (innerText)
WHY_NEXT = (cc.WHY.replace("w.querySelector('h4').innerText", "w.querySelector('h4').textContent")
            .replace("w.querySelector('.h-sec-head').innerText", "w.querySelector('.h-sec-head').textContent")
            .replace("map(h => h.innerText)", "map(h => h.textContent)"))
assert WHY_NEXT.count("textContent") >= 3


# ------------------------------------------------------------------------------------------- card-and-chips: the Why section

@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
def test_every_sport_card_explains_the_prop_with_real_data_on_next(browser, rich, monkeypatch, size):
    """legacy: test_card_and_chips_browser.py::test_every_sport_card_explains_the_prop_with_real_data[1440|390]. Every rule of the
    Why section (facts for the side / against it / good to know, never green under 50%, dots only with a grade, no jargon, L5 /
    L10 / L20 counted) on CS2, LoL, NBA and both NFL cards."""
    monkeypatch.setattr(cc, 'WHY', WHY_NEXT)
    opens_next(monkeypatch, cc, 'open_board')
    cc.test_every_sport_card_explains_the_prop_with_real_data(browser, rich, size)


@pytest.mark.parametrize('size', PHONES + [DESK], ids=lambda s: str(s['width']))
def test_board_and_card_fit_every_width_on_next(browser, rich, monkeypatch, size):
    """legacy: test_card_and_chips_browser.py::test_board_and_card_fit_every_width[360|390|414|1440]. CS2, LoL, NBA, NFL, Dabble and
    Sleeper: the board and the card fit the screen, the Why section is first on the card, nothing errors."""
    monkeypatch.setattr(cc, 'WHY', WHY_NEXT)
    opens_next(monkeypatch, cc, 'open_board')
    cc.test_board_and_card_fit_every_width(browser, rich, size)


# ------------------------------------------------------------------------------------------- card-why: the card opens with it

@pytest.mark.parametrize('size', [DESK, PHONE], ids=['1440', '390'])
@pytest.mark.parametrize('sp', ['cs2', 'lol', 'nba', 'nfl'])
def test_every_sport_card_opens_with_why_scout_leans_on_next(ctx, env, card_flags, monkeypatch, sp, size):
    """legacy: test_card_why_browser.py::test_every_sport_card_opens_with_why_scout_leans[<sport>-1440|390] (8 ids)."""
    real = cw.why

    def why(p):
        w = real(p)
        w['title'] = p.locator('#hub-detail .h-why h4').text_content()      # "Why Scout leans Over 38.5", painted upper-case
        assert p.locator('#hub-detail .h-why h4').inner_text().lower() == w['title'].lower()
        return w
    monkeypatch.setattr(cw, 'why', why)
    opens_next(monkeypatch, cw, 'board')
    cw.test_every_sport_card_opens_with_why_scout_leans(ctx, env, sp, size)


# ------------------------------------------------------------------------------------------- card-factors: every panel, all odds

def beside_the_board(p):
    """desktop on the redesign: the open card is on screen, beside the board's table (never over it), in the slip's column; the slip
    is hidden while the card is open"""
    box = p.evaluate("""() => { const r = e => { const b = e.getBoundingClientRect(); return [b.left, b.right, b.width, b.height]; };
      const s = document.querySelector('#u-hub .h-slip');
      return {card: r(document.querySelector('#hub-card')), table: r(document.querySelector('#hub-table')), w: innerWidth,
              slip: !!s && getComputedStyle(s).display !== 'none' && s.getBoundingClientRect().width > 0}; }""")
    assert box['card'][2] >= 320 and box['card'][3] >= 300 and box['card'][1] <= box['w'] + 1, box
    assert box['card'][0] >= box['table'][1] - 1, box
    assert not box['slip'], box


@pytest.mark.parametrize('sp', ['cs2', 'lol', 'nba', 'nfl'])
def test_hub_card_shows_every_restored_panel_and_all_odds_on_next(ctx, env, card_flags, monkeypatch, sp):
    """legacy: test_card_factors_browser.py::test_hub_card_shows_every_restored_panel_and_all_odds[<sport>-1440] (the 390 ids pass on
    both designs). Every restored panel on the Stats tab and the full odds table on the Lines tab; the legacy "slip column is never
    covered" check is the old layout's - here the card takes the slip's column, and the slip (with the leg added before the card
    opened) is back when the card closes."""
    monkeypatch.setattr(cf, 'not_under_slip', beside_the_board)
    seen = []
    real = cf.board
    monkeypatch.setattr(cf, 'board', lambda *a, **k: seen.append(is_next(real(*a, **k))) or seen[-1])
    cf.test_hub_card_shows_every_restored_panel_and_all_odds(ctx, env, monkeypatch, sp, DESK)
    p = seen[0]
    beside_the_board(p)
    p.locator('#hub-card [data-hub="card-close"]').first.click()
    p.wait_for_selector('#hub-card', state='hidden', timeout=10000)
    assert p.locator('#u-hub .h-slip').is_visible() and p.locator('#hub-slip-content .u-leg').count() == 1   # the leg added before the card opened
    assert p.locator('#hub-slip-count').text_content() == "1"
    assert not p.errors, p.errors


# ------------------------------------------------------------------------------------------- line history on the card

@pytest.mark.parametrize("size", [DESK, PHONE], ids=['1440', '390'])
def test_flag_on_line_history_closing_marks_and_moves_on_next(browser, env, monkeypatch, size):
    """legacy: test_line_history_browser.py::test_flag_on_line_history_closing_marks_and_moves[size0|size1]. Same numbers from the
    stored history; the tile labels are painted upper-case, so the words are compared without case."""
    monkeypatch.setenv("SCOUT_LINE_HISTORY", "1")
    monkeypatch.delenv("SCOUT_CLOSING_LINES", raising=False)
    ud = lhb.seed(env, monkeypatch)
    c = context(browser, env, size)
    try:
        p = is_next(lhb.open_card(c, env, size))
        doc = lhb.line_doc(p, "underdog", ud["id"])
        b = doc["history"]["line_history"]
        u = next(a for a in b["apps"] if a["platform"] == "underdog")
        assert [(x["kind"], x["line"]) for x in u["points"]] == [("open", 36.5), ("move", 37.5), ("payout", 37.5), ("move", 38.5)]
        pp = next(a for a in b["apps"] if a["platform"] == "prizepicks")
        assert [x["line"] for x in pp["points"]] == [37.5, 38.0, 38.5]
        assert b["avg_closing_l10"] == 39.5 and b["closing_n_l10"] == 5 and doc["history"]["closing"]["n"] == 5

        p.wait_for_selector("#hub-detail .h-lh [data-hub-lh] svg .lh-path")
        assert p.locator("#hub-detail .h-lh .lh-path").count() == len(b["apps"]) == 4            # UD, PP, PP Goblin, PP Demon
        assert p.locator("#hub-detail .h-lh [data-hub-lh] svg").is_visible()
        assert p.locator("#hub-detail .h-history-chart .close-mark").count() == 5          # the five past games with a closing line
        text = p.locator("#hub-detail").inner_text().lower()
        assert "avg closing line" in text and "39.5" in text and "l10 vs closing" in text
        written = p.locator("#hub-detail").text_content()
        assert "Avg closing line" in written and "L10 vs closing" in written              # the words as written, before the CSS case
        tiles = p.locator("#hub-detail .h-history-summary .h-tile").filter(has_text=re.compile("closing", re.I))
        assert tiles.count() >= 2 and all(tiles.nth(i).is_visible() for i in range(tiles.count()))
        assert any("39.5" in t for t in tiles.all_inner_texts())
        zone = p.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
        assert f"times in {zone}".lower() in p.locator("#hub-detail .h-lh .h-sec-head").inner_text().lower()
        tips = p.locator("#hub-detail .h-lh .lh-hit title").evaluate_all("ts => ts.map(t => t.textContent)")
        assert any("payout changed at 37.5" in t and "1.06x more" in t for t in tips)
        assert any(t.startswith("PrizePicks moved to 38.5") for t in tips)
        p.locator("#hub-detail .h-lh-table summary").click()
        assert p.locator("#hub-detail .h-lh-table tbody tr").count() == 9                   # 4 UD + 3 PP + Goblin + Demon
        first = p.locator("#hub-detail .h-lh-table tbody tr").first.inner_text()
        assert "underdog" in first.lower() and "38.5" in first                             # newest first
        assert "on the board at 34.5" in p.locator("#hub-detail .h-lh-table").inner_text().lower()   # no stored opening: never called one
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------- phone: the five hit windows

# every board card's cells: none overlaps another, none leaves the card or the screen
CELLS = """() => [...document.querySelectorAll('#hub-rows tr[data-record]')].filter(r => r.offsetParent).map(r => {
  const rb = r.getBoundingClientRect();
  const tds = [...r.children].filter(td => getComputedStyle(td).display !== 'none' && td.getBoundingClientRect().width > 0)
    .map(td => { const b = td.getBoundingClientRect(); return {c: td.className, l: b.left, r: b.right, t: b.top, b: b.bottom}; });
  const over = [];
  for (let i = 0; i < tds.length; i++) for (let j = i + 1; j < tds.length; j++) { const a = tds[i], b = tds[j];
    if (Math.min(a.r, b.r) - Math.max(a.l, b.l) > 1 && Math.min(a.b, b.b) - Math.max(a.t, b.t) > 1) over.push(a.c + ' x ' + b.c); }
  const out = tds.filter(a => a.l < rb.left - 0.5 || a.r > rb.right + 0.5 || a.r > innerWidth + 0.5).map(a => a.c);
  const inner = [...r.querySelectorAll('.h-player b, .h-c-line b, .h-direction, .h-lines-btn, .h-lines-one, .h-add')].filter(e => e.getBoundingClientRect().width > 0)
    .filter(e => e.getBoundingClientRect().right > innerWidth + 0.5 || e.getBoundingClientRect().left < -0.5).map(e => e.className || e.tagName);
  return {key: r.dataset.record, over, out, inner}; })"""


def check_phone_grid_next(p, where):
    cards = p.evaluate(CELLS)
    assert cards, where
    for c in cards:                      # the card's own layout: the line never sits on the Scout side, Lines / Add stay on screen
        assert c['over'] == [] and c['out'] == [] and c['inner'] == [], (where, c)
    rows = p.evaluate(ph.GEOM)
    assert rows, where
    width = p.viewport_size['width']
    for r in rows:
        cs = r['cells']
        if r['nohits']:                  # no result in any window = no strip line (not five dashes)
            assert r['tdHidden'] and all(c['text'] == '—' for c in cs), (where, r['key'])
            continue
        want = [w + ('' if r['side'] else ' O-U') for w in ph.WINDOWS]     # a row with no side counts Over-Under and says so
        assert [c['label'] for c in cs] == want, (where, r['key'])
        assert [c['labelShown'] for c in cs] == want, (where, r['key'])     # the label is on screen
        assert r['tdW'] >= r['rowW'] - 32, (where, r['key'], r['tdW'], r['rowW'])           # the strip has its own line of the card
        assert min(c['left'] for c in cs) >= r['tdLeft'] - 0.5 and max(c['right'] for c in cs) <= r['tdLeft'] + r['tdW'] + 0.5, (where, r['key'], cs)
        assert max(c['right'] for c in cs) <= width, (where, r['key'])                      # all five windows on screen
        widths = [c['w'] for c in cs]
        assert max(widths) - min(widths) <= 1.5 and min(widths) >= 50, (where, r['key'], widths)
        gaps = [b['left'] - a['right'] for a, b in zip(cs, cs[1:])]
        assert min(gaps) >= 4, (where, r['key'], gaps)
        assert len({round(c['top']) for c in cs}) == 1, (where, r['key'])                   # one line of five, never wrapped
        for c in cs:
            assert c['lines'] == 1 and not c['clip'], (where, r['key'], c)                  # "10/20" on one line, never cut
            assert c['fs'] >= 12 and c['ls'] >= -0.3 and c['vW'] >= 30, (where, r['key'], c)
            assert c['labelPx'] >= 9 and c['vTop'] >= c['top'] + 8, (where, r['key'], c)     # the label above its value
    return rows


@pytest.mark.parametrize('size', PHONES, ids=lambda s: str(s['width']))
def test_phone_rows_show_five_separated_windows_in_every_sport_and_book_on_next(browser, live, size):
    """legacy: test_phone_hit_chips_browser.py::test_phone_rows_show_five_separated_windows_in_every_sport_and_book[360|390|414].
    Real bugs this proved on the redesign with the live flags on: the phone card's cells overlapped (line on the Scout side, Lines
    button cut at the screen edge), the Season window was cut at 360px, and a row with no Scout side ("L5 O-U" labels) ran its H2H and
    Season windows off the screen at every phone width. Sizes are the redesign's (the old 44px labelled boxes are old-layout-only)."""
    if size["width"] < 600:
        pytest.skip("phone simplification (owner 2026-11-12): the phone row no longer carries the L5-SZN windows; the card has it")
    p = is_next(ph.open_board(browser, live, size))
    try:
        assert p.evaluate("document.querySelector('#u-hub').classList.contains('h-cols')")   # the live flags: H2H and Season windows
        check_phone_grid_next(p, 'all')
        no_h_scroll(p)
        for s in ('cs2', 'lol', 'nba', 'nfl'):
            ph.sport(p, s)
            p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
            check_phone_grid_next(p, s)
            no_h_scroll(p)
        ph.sport(p, 'cs2')
        sides = set()
        for b in ('prizepicks', 'underdog', 'sleeper', 'dabble'):
            ph.pick_book(p, b)
            p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.includes('|{b}|'))", timeout=20000)
            sides |= {r['side'] for r in check_phone_grid_next(p, b) if not r['nohits']}
            no_h_scroll(p)
        assert sides == {True, False}                         # rows with a Scout side and rows that count Over-Under were both measured
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------- legacy bodies, unchanged (desktop cards)
# In the listed run (2026-11-12 01:29-02:00, started before c0c5b06 was committed) these legacy tests could not press a desktop row's
# name. That did not happen again on c0c5b06: their bodies pass on the redesign as written, with and without this branch's fixes
# (tests/UI_NEXT_TRIAGE.md). They run here unchanged on a member's redesign context, so the redesign keeps what they check.

def test_nfl_card_injury_teammates_usage_and_engine_rows_on_next(browser, rich, monkeypatch):
    """legacy: test_card_and_chips_browser.py::test_nfl_card_injury_teammates_usage_and_engine_rows."""
    opens_next(monkeypatch, cc, "open_board")
    cc.test_nfl_card_injury_teammates_usage_and_engine_rows(browser, rich)


def test_hit_rates_in_why_match_the_tiles_on_next(ctx, env, card_flags, monkeypatch):
    """legacy: test_card_why_browser.py::test_hit_rates_in_why_match_the_tiles."""
    opens_next(monkeypatch, cw, "board")
    cw.test_hit_rates_in_why_match_the_tiles(ctx, env)


@pytest.mark.parametrize("sp", ["nba", "nfl"])
def test_key_insights_current_line_counts_on_next(ctx, env, card_flags, monkeypatch, sp):
    """legacy: test_card_why_browser.py::test_key_insights_current_line_counts[1440-nba] [1440-nfl]."""
    opens_next(monkeypatch, cw, "board")
    cw.test_key_insights_current_line_counts(ctx, env, sp, DESK)
