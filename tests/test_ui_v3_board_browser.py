"""v3 boards (page group A, web/v3/board.css + board.js) in a real browser against the real Handler with fixture data only.

SCOUT_UI_V3=1 plus the board flags the running server turns on by default (server.DEFAULT_ON: H2H / Season columns, Edge, hit
samples, Market edge), so the columns the owner approved are on screen. Every board (CS2 hub, LoL, Sports; NBA / NFL tabs; soccer and
tennis through the expansion fixture) at 390x844 and 1280, dark and light: the hub's own cells are all still there and visible in
each row (nothing the board shows today is dropped), no sideways scroll, no script error. Plus the pop-overs, By game, a picked
row, the empty board and the 1024-1100px band where the hub is in its card mode under the v3 desktop header.
V3_BOARD_SHOTS=<folder> keeps a screenshot of each."""
import os
import re
from pathlib import Path

import pytest

from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_ui_v3_browser import PHONE, DESK

SHOTS = Path(os.environ.get("V3_BOARD_SHOTS") or "")
PROD_FLAGS = ("SCOUT_BOARD_H2H", "SCOUT_PARITY_EDGE", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_HIT_SORT", "SCOUT_PARITY_EV_BAND", "SCOUT_PARITY_PLAYS",
              "SCOUT_PICKEM_EV", "SCOUT_MARKET_EDGE")
PAGES = {"cs2": "/#all-sports", "lol": "/lol#all-sports", "sports": "/sports#/board"}
ROW = "#hub-rows>tr[data-record]"
# every cell the hub's board row draws today (web/upgrade-hub.js row()), by class. A row with no past result in any window (h-nohits)
# has no hit line on the hub's own phone card today, so its hits cell is not required there.
CELLS = ("h-c-watch", "h-c-player", "h-c-market", "h-c-line", "h-c-proj", "h-c-scout", "h-c-hits", "h-c-compare", "h-c-add")


def open_board(browser, env, monkeypatch, path, size, theme, flags=True):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    for k in PROD_FLAGS if flags else ():
        monkeypatch.setenv(k, "1")
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + path)
    p.wait_for_selector(ROW, timeout=30000)
    p.wait_for_function("!!window.ScoutV3 && !!document.querySelector('#hub-rows>tr[data-sc=\"1\"]')")
    return c, p


def shot(p, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(350)
        p.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def visible_cells(p):
    """Per decorated row: each hub cell's on-screen box (display:contents cells report their children's)."""
    return p.evaluate("""(cells) => [...document.querySelectorAll('#hub-rows>tr[data-record]')].slice(0, 12).map(tr => {
      const box = el => { if (!el) return 0; if (getComputedStyle(el).display === 'contents') return [...el.children].some(c => box(c)) ? 1 : 0;
        const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? 1 : 0; };
      return Object.fromEntries(cells.map(k => [k, box(tr.querySelector('td.' + k))]).concat([['edge', tr.querySelector('td.h-c-edge') ? box(tr.querySelector('td.h-c-edge')) : -1],
        ['h-c-hits', tr.classList.contains('h-nohits') && innerWidth <= 1100 ? -1 : box(tr.querySelector('td.h-c-hits'))]]));
    })""", list(CELLS))


def check_rows(p, edge=True, allow=()):
    rows = visible_cells(p)
    assert rows
    for r in rows:
        missing = [k for k, v in r.items() if v == 0 and k not in allow]
        assert not missing, (missing, r)
        if edge:
            assert r["edge"] == 1
    # the hub's own values are on screen, in the approved wording
    seen = p.locator(f"{ROW} .sc-seen").all_inner_texts()
    assert seen and all(re.match(r"^(PrizePicks|Underdog|[A-Z][\w ]+)( · checked (just now|\d+ (min|h) ago))?", s) for s in seen), seen
    assert not any(re.search(r"(?<![\d.])0(\.0)?%", t) for t in p.locator(f"{ROW} .h-direction").all_inner_texts())
    words = " ".join(p.locator(f"{ROW} .h-scout").all_inner_texts())
    assert "Results" not in words


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
@pytest.mark.parametrize("page", list(PAGES))
def test_v3_board_keeps_every_cell_and_takes_the_approved_look(browser, env, monkeypatch, page, size, theme):
    c, p = open_board(browser, env, monkeypatch, PAGES[page], size, theme)
    try:
        assert p.evaluate("document.documentElement.dataset.uiV3") == "1"
        top = p.locator("#u-hub .h-top.sc-bd-band")
        assert top.count() == 1 and top.is_visible()
        assert p.locator("#hub-title").inner_text().strip().endswith("player props") or p.locator("#hub-title").inner_text().strip() == "Player props"
        assert p.locator("#u-hub .sc-bd-meta").inner_text() == p.locator("#hub-count").inner_text()
        check_rows(p)
        # value inks only where the legend says: 60%+ green, under 40% red
        legend = p.locator("#sc-bd-legend")
        assert legend.is_visible() and "60%+" in legend.inner_text() and "under 40%" in legend.inner_text()
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-table .h-scout .h-direction>span')).fontWeight") == "600"
        if size is PHONE:
            # the 4-line card: name and chance on one line, line / market / Proj. on the next, meta on the last
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-table>thead')).display") == "none"
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-rows>tr[data-record]')).display") == "grid"
            first = p.locator(ROW).first
            y = lambda sel: first.locator(sel).first.bounding_box()["y"]
            assert abs(y(".h-player b") - y(".h-direction")) < 14
            assert y("td.h-c-line>b") < y(".sc-seen")
            assert p.locator("#hub-q").is_hidden()                                       # behind the header's search button
        else:
            assert p.evaluate("getComputedStyle(document.querySelector('#hub-table>thead')).display") != "none"
            wrap = p.evaluate("(()=>{const w=document.querySelector('#hub-board-section .h-table-wrap');return [w.scrollWidth,w.clientWidth]})()")
            assert wrap[0] <= wrap[1] + 1, wrap                                          # beside the slip column, no inner sideways scroll
        no_h_scroll(p)
        shot(p, f"{page}-{theme}-{size['width']}-top")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_sport_tabs_set_the_accent_and_band(browser, env, monkeypatch, size):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark")
    try:
        for sport, label in (("cs2", "CS2"), ("lol", "LoL"), ("nba", "NBA"), ("nfl", "NFL")):
            tab = p.locator(f"#hub-sports [data-hub=sport][data-id={sport}]")
            if not tab.count():
                continue
            tab.click()
            p.wait_for_function(f"document.documentElement.dataset.scSport==='{sport}'")
            p.wait_for_function(f"document.getElementById('hub-title').textContent.trim()==='{label} player props'")
            p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
            if p.locator(ROW).count():
                check_rows(p)
            no_h_scroll(p)
            shot(p, f"tab-{sport}-dark-{size['width']}")
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-sports [aria-pressed=true]')).boxShadow").startswith("rgb")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_controls_popovers_by_game_picked_and_empty(browser, env, monkeypatch, size, theme):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, theme)
    w = size["width"]
    try:
        # Filters pop-over (the hub's own controls, restyled)
        p.locator("#hub-filters-toggle").click()
        p.wait_for_selector("#hub-filter-pop:not([hidden])")
        for sel in ("#hub-stat", "#hub-side", "#hub-minGrade", "#hub-diff", "#hub-watch"):
            assert p.locator(sel).is_visible(), sel
        shot(p, f"filters-{theme}-{w}")
        p.locator("#hub-filter-pop .h-pop-foot [data-hub=filters]").click()
        p.wait_for_selector("#hub-filter-pop", state="hidden")
        # Views
        p.locator("#hub-views-toggle").click()
        p.wait_for_selector("#hub-views-pop:not([hidden])")
        assert p.locator("#hub-views-pop [data-hub=save-view]").is_visible()
        shot(p, f"views-{theme}-{w}")
        p.locator("#hub-views-toggle").click()
        # a picked row: raised surface + accent spine, the add button shows the tick
        add = p.locator(f"{ROW} .h-add:not(.h-add-info):not(.selected)").first
        add.click()
        p.wait_for_selector(f"{ROW} .h-add.is-on")
        picked = p.locator(f"{ROW}:has(.h-add.is-on)").first
        assert "inset" in p.evaluate("e=>getComputedStyle(e).boxShadow", picked.element_handle())
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        check_rows(p)
        shot(p, f"picked-{theme}-{w}")
        # By game
        if size is PHONE:
            p.locator(".sc-v3-tabs [data-v3=more]").click()
            p.locator(".sc-v3-more .sc-more__a", has_text="By game").first.click()
        else:
            p.locator("#hub-board-section [data-hub=view][data-id=games]").click()
        p.wait_for_selector("#hub-games-body:not([hidden]) .h-game-card")
        cards = p.locator("#hub-games-body .h-game-card")
        assert cards.count() >= 1
        for sel in ("h2", "p", ".h-game-books span", "time", "button"):
            assert cards.first.locator(sel).first.is_visible(), sel
        no_h_scroll(p)
        shot(p, f"bygame-{theme}-{w}")
        p.locator("#hub-board-section [data-hub=view][data-id=list]").click()
        # the empty board: the hub's own reason + action
        if size is PHONE:
            p.locator(".sc-v3-bar [data-v3=search]").click()
            p.wait_for_function("document.activeElement && document.activeElement.id==='hub-q'")
        p.locator("#hub-q").fill("zzzz-no-such-player")
        p.wait_for_selector("#hub-empty:not([hidden]) [data-hub=clear-filters]")
        assert p.locator("#hub-q").is_visible()                                         # a typed search keeps the box open
        shot(p, f"empty-{theme}-{w}")
        p.locator("#hub-empty [data-hub=clear-filters]").click()
        p.wait_for_selector(ROW)
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_board_between_1024_and_1100_keeps_the_card_layout_under_the_desktop_header(browser, env, monkeypatch):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", {"width": 1060, "height": 900}, "dark")
    try:
        assert p.locator(".sc-v3-bar .sc-nav").is_visible()
        assert p.evaluate("getComputedStyle(document.querySelector('#hub-rows>tr[data-record]')).display") == "grid"
        check_rows(p)
        no_h_scroll(p)
        shot(p, "cs2-dark-1060")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_board_without_the_default_on_columns(browser, env, monkeypatch):
    """The board flags off (as in the other suites): no Edge / H2H / SZN columns, the card and the table still hold every cell."""
    for size in (PHONE, DESK):
        c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark", flags=False)
        try:
            check_rows(p, edge=False)
            no_h_scroll(p)
            assert not p.errors, p.errors
        finally:
            c.close()


def test_v3_board_rows_keep_working_after_repaints(browser, env, monkeypatch):
    """The hub repaints #hub-rows on every change: each new row is decorated once (no doubled meta or struck line)."""
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", DESK, "dark")
    try:
        p.locator("#hub-sort").select_option("player")
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        p.locator("#hub-sort").select_option("chance")
        p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
        assert p.evaluate("[...document.querySelectorAll('#hub-rows>tr[data-record]')].every(tr=>tr.querySelectorAll('.sc-seen').length===1&&tr.querySelectorAll('.sc-was').length<=1)")
        toggle = p.locator(f"{ROW} .h-prop-toggle").first
        if toggle.count():
            assert re.match(r"^\+\d+ more props?$", toggle.inner_text().strip()), toggle.inner_text()
            label = toggle.get_attribute("aria-label")
            assert label.startswith("Show ")                                             # the hub's own label, unchanged
            toggle.click()
            p.wait_for_selector(f"{ROW}.is-sub")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_soccer_and_tennis(browser, env, monkeypatch, size, theme):
    import workspace_expansion as adapter
    import workspace_sports
    from tests.test_expansion_main_board import source
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    original = workspace_sports.call
    docs = {s: source(s) for s in adapter.SPORTS}
    monkeypatch.setattr(workspace_sports, "call", lambda srv, path, query=None, **kw: (docs[query["sport"]], None)
                        if path == "/api/sports/expansion/board" else original(srv, path, query, **kw))
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, theme)
    try:
        for sport, label in (("soccer", "Soccer"), ("tennis", "Tennis")):
            tab = p.locator(f"#hub-sports [data-hub=sport][data-id={sport}]")
            assert tab.count() == 1, sport
            tab.click()
            p.wait_for_function(f"document.documentElement.dataset.scSport==='{sport}'")
            p.wait_for_selector(f"{ROW}[data-sc='1']")
            p.wait_for_function("!document.querySelector('#hub-rows>tr[data-record]:not([data-sc=\"1\"])')")
            rows = visible_cells(p)
            assert rows and all(all(v != 0 for k, v in r.items()) for r in rows), rows
            assert p.locator("#hub-health .h-history-note").is_visible()                  # the coverage note stays
            no_h_scroll(p)
            shot(p, f"{sport}-{theme}-{size['width']}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_flag_off_board_carries_none_of_it(browser, env, monkeypatch):
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    c = context(browser, env, PHONE)
    p = c.new_page()
    try:
        p.goto(env.url + "/#all-sports")
        p.wait_for_selector(ROW, timeout=30000)
        p.wait_for_timeout(300)
        assert p.evaluate("document.documentElement.dataset.uiV3") is None
        assert p.evaluate("!window.ScoutV3 && !document.querySelector('.sc-seen,.sc-bd-band,#sc-bd-legend,[data-sc]')")
        assert p.evaluate("[...document.querySelectorAll('link[rel=stylesheet],script[src]')].every(e=>!(e.href||e.src).includes('/v3/'))")
    finally:
        c.close()


@pytest.mark.parametrize("width", [1110, 1180, 1240, 1440])
def test_v3_board_narrow_and_wide_desktop_keep_the_player_column(browser, env, monkeypatch, width):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", {"width": width, "height": 900}, "dark")
    try:
        # 1101-1180px: Compare and the watch star step aside, as the hub's own table does there today (both stay on the card)
        check_rows(p, edge=True, allow=("h-c-watch", "h-c-compare") if width <= 1180 else ())
        wrap = p.evaluate("(()=>{const w=document.querySelector('#hub-board-section .h-table-wrap'),t=document.getElementById('hub-table');return [t.getBoundingClientRect().width,w.clientWidth]})()")
        assert wrap[0] <= wrap[1] + 1, wrap
        name = p.evaluate("document.querySelector('#hub-rows>tr[data-record] td.h-c-player').getBoundingClientRect().width")
        assert name >= 110, name
        if width >= 1181:
            assert p.locator(f"{ROW} td.h-c-compare").first.is_visible()
        no_h_scroll(p)
        shot(p, f"cs2-dark-{width}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_board_scrolled_header_and_phone_end_of_list(browser, env, monkeypatch):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", DESK, "dark")
    try:
        p.evaluate("document.getElementById('hub-center').scrollTop=600")
        p.wait_for_timeout(300)
        bar = p.evaluate("document.querySelector('#hub-board-section .h-toolbar').getBoundingClientRect().bottom")
        th = p.evaluate("document.querySelector('#hub-table>thead th').getBoundingClientRect().top")
        assert th >= bar - 1, (th, bar)
        shot(p, "cs2-dark-1280-rows")
    finally:
        c.close()
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", PHONE, "dark")
    try:
        p.evaluate("document.getElementById('hub-center').scrollTop=1e6")
        p.wait_for_timeout(300)
        last = p.evaluate("Math.max(...[...document.querySelectorAll('#hub-rows>tr[data-record], #hub-pagination')].map(e=>e.getBoundingClientRect().bottom))")
        tabs = p.evaluate("document.querySelector('.sc-v3-tabs').getBoundingClientRect().top")
        assert last <= tabs + 1, (last, tabs)
        shot(p, "cs2-dark-390-end")
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_row_opens_the_player_card(browser, env, monkeypatch, size):
    c, p = open_board(browser, env, monkeypatch, "/#all-sports", size, "dark")
    try:
        p.locator(f"{ROW} .h-player").first.click()
        p.wait_for_selector("#hub-card:not([hidden]) #hub-detail *")
        assert p.locator("#hub-card").is_visible()
        no_h_scroll(p)
        shot(p, f"card-dark-{size['width']}")
        if size is DESK:
            check_rows(p)
        assert not p.errors, p.errors
    finally:
        c.close()
