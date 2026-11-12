"""Member functions that had no test on the redesigned hub (the default since 2026-11-11), in a real browser at 1440x900 and
390x844 against the real Handler with fixture data only and the live flags (server.DEFAULT_ON, as the Edge / Market edge / H2H
columns change the row's layout):
- the map veto panel on the CS2 card's Stats tab;
- the matchroom (the classic CS2 match page) from the Tools section and the phone's More sheet;
- a bumped line on the board row ("was X"), on its ladder line and in the card's header ("moved from X");
- the line discrepancy chip on the row and on the card's Lines tab, and the "Lines differ" filter;
- the PrizePicks pre-fill link (board?projections=<id>-<o|u>-<line>) for the side and line chosen on the card and through the ladder;
- the Underdog link of one line, from the card.
Each test runs the same fixture on the design before it (UI_NEXT=0) and asserts the redesign shows the same text and builds the same
URL. Display only: nothing here reads or changes a grade. Phone controls a test touches are at least 44px."""
from datetime import datetime, timezone

import pytest

import cs2_card_ctx
import pplink
import server
import workspace_api
import workspace_books as wb
import workspace_ev as we
from tests.card_factors_fixture import classic_res, VETO
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll  # noqa: F401
from tests.workspace_fixture import iso

sync_api = pytest.importorskip("playwright.sync_api")
DESK, PHONE = {"width": 1440, "height": 900}, {"width": 390, "height": 844}
SIZES = pytest.mark.parametrize("size", [DESK, PHONE], ids=["desktop", "phone"])
VARGO_UD, VARGO_PP = "cs2|underdog|11111111-0000-4000-8000-000000000001", "cs2|prizepicks|pp:7001"
ORLO_UD, ORLO_PP = "cs2|underdog|11111111-0000-4000-8000-000000000003", "cs2|prizepicks|pp:7003"
BOX = "e => { const r = e.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height, r: r.right, sw: e.scrollWidth, cw: e.clientWidth}; }"


@pytest.fixture
def live(env, monkeypatch):
    """The live flags, the app links on, vargo's line bumped 37.5 -> 38.5 on both apps, and the card context + veto the worker stores."""
    for k in server.DEFAULT_ON:
        monkeypatch.setenv(k, "1")
    for k in ("SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(server, "PP_OPEN_LINK", True)
    monkeypatch.setattr(server, "ud_row_link", lambda rid: pplink.ud_link(rid))      # Underdog's link of that line id (no network)
    for book in ("underdog", "prizepicks"):
        env.by("vargo", book).update(open_line=37.5, moved_at=iso(datetime.now(timezone.utc)))
    cs2_card_ctx.reset()
    for r in env.rows:
        cs2_card_ctx.put(r["id"], cs2_card_ctx.build(classic_res(r["nick"], r["team"], r["opponent"])))
    import veto_serve
    monkeypatch.setattr(veto_serve, "for_match", lambda mid: VETO)
    wb.reset()
    we._FAIR.clear()
    we._LEGS.clear()
    workspace_api.reset()
    env.mp = monkeypatch
    yield env
    wb.reset()
    we._FAIR.clear()
    we._LEGS.clear()
    cs2_card_ctx.reset()


class Hub:
    """One member's CS2 board on one design. ui: 'next' (the default, UI_NEXT unset) or 'old' (the rollback switch UI_NEXT=0)."""

    def __init__(self, browser, env, ui, size, path="/"):
        if ui == "next":
            env.mp.delenv("UI_NEXT", raising=False)
        else:
            env.mp.setenv("UI_NEXT", "0")
        self.ui, self.size, self.env = ui, size, env
        self.c = context(browser, env, size)
        self.p = p = open_page(self.c, env, path, size)
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        p.wait_for_function("!document.querySelector('#hub-rows .h-medge-wait')", timeout=20000)
        p.wait_for_load_state("networkidle")                  # the price reads that repaint the board have landed
        assert p.locator("html").get_attribute("data-ui") == ("next" if ui == "next" else None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.c.close()

    @property
    def phone(self):
        return self.size is PHONE

    def row(self, name):
        return self.p.locator("#hub-rows tr.h-row", has_text=name).first

    def tall(self, loc, least=44):
        """a control the test touches is a thumb target on the redesign's phone (and never zero-size on either design)"""
        b = None
        for _ in range(5):                                    # the board repaints its rows as each price read lands
            try:
                loc.scroll_into_view_if_needed(timeout=5000)
                b = loc.bounding_box(timeout=5000)
            except sync_api.Error:
                b = None
            if b:
                break
            self.p.wait_for_timeout(200)
        assert b and b["width"] > 0 and b["height"] >= (least - 0.5 if self.phone and self.ui == "next" else 12), (b, loc)
        return loc

    def card(self, key):
        """the card of one line: the row's own line directly; another line through the ladder (redesign) or the board's rows (old)"""
        p = self.p
        row = p.locator(f'#hub-rows tr[data-record="{key}"]')
        if not row.count() and self.ui == "next":
            g = p.locator("#hub-rows tr.h-row", has=p.locator('[data-hub=ladder]')).filter(has_text=self.name_of(key)).first
            self.ladder(g)
            self.tall(p.locator(f'tr.h-ladder-row tr[data-line="{key}"] .h-ld-book')).click()
        else:
            if not row.count():
                p.locator("#hub-q").fill(self.name_of(key))
                if p.locator("#hub-rows .h-prop-toggle[aria-expanded=false]").count():
                    p.locator("#hub-rows .h-prop-toggle[aria-expanded=false]").first.click()
                row.wait_for()
            self.tall(row.locator(".h-player")).click()
        p.wait_for_selector("#hub-detail .h-why", timeout=20000)
        p.wait_for_function("k => document.querySelector('#hub-detail').dataset.id === k && !document.querySelector('#hub-detail .h-detail-loading')", arg=key, timeout=20000)
        assert p.locator("#hub-card-line").input_value() == key
        return p.locator("#hub-detail")

    def name_of(self, key):
        return next(r["nick"] for r in self.env.rows if key.endswith("|" + str(r["id"])))

    def ladder(self, row):
        btn = row.locator("[data-hub=ladder]")
        if btn.get_attribute("aria-expanded") != "true":
            self.tall(btn).click()
        self.p.wait_for_selector("tr.h-ladder-row .h-ladder-table tbody tr")
        return self.p.locator("tr.h-ladder-row")

    def tab(self, name):
        p = self.p
        self.tall(p.locator(f"#hub-detail [data-hub=card-tab][data-id={name}]")).click()
        p.wait_for_function(f"document.querySelector('#hub-detail').dataset.tab === '{name}' && !document.querySelector('#hub-detail .h-detail-loading')", timeout=20000)

    def side(self, sd):
        b = self.p.locator(f"#hub-decision [data-hub=history-side][data-id={sd}]")
        self.tall(b).click()
        self.p.wait_for_function(f"document.querySelector('#hub-decision [data-hub=history-side][data-id={sd}]').getAttribute('aria-pressed') === 'true'")

    def close_card(self):
        self.tall(self.p.locator("#hub-detail [data-hub=card-close]")).click()
        self.p.wait_for_function("document.getElementById('hub-card').hidden")

    def in_view(self, loc):
        """shown, with a size, inside the screen's width and not cut off by its own box"""
        loc.scroll_into_view_if_needed()
        assert loc.is_visible()
        b = loc.evaluate(BOX)
        assert b["w"] > 0 and b["h"] > 0 and b["x"] >= -0.5 and b["r"] <= self.size["width"] + 0.5 and b["sw"] <= b["cw"] + 1, b
        return b


def flat(s):
    return " ".join((s or "").split()).lower()


# ------------------------------------------------------------------------------------------------------------------ map veto

def veto_panel(h):
    h.card(VARGO_UD)
    h.tab("stats")
    d = h.p.locator("#hub-card-body .h-fx[data-fx=cs2-match]")
    if d.get_attribute("open") is None:
        h.tall(d.locator("summary")).click()
    return d


@SIZES
def test_map_veto_panel_is_on_the_cs2_card_stats_tab_with_the_same_content(browser, live, size):
    with Hub(browser, live, "old", size) as o:
        want = flat(veto_panel(o).inner_text())
    assert "map veto" in want and "map 1 mirage (halcyon pick)" in want and "inferno 41.0%" in want and "nuke 33.0%" in want and "other maps 26.0%" in want
    assert "halcyon picked mirage in 4 of their last 5 bo3s" in want and "weight 0 in the grade" in want
    with Hub(browser, live, "next", size) as n:
        d = veto_panel(n)
        assert flat(d.inner_text()) == want
        for part in (".h-fx-chips", ".h-fx-ul", ".h-fx-lab"):
            n.in_view(d.locator(part).first)
        box = d.bounding_box()
        assert box["width"] > 300 and box["height"] > 200
        no_h_scroll(n.p)
        assert not n.p.errors


# ----------------------------------------------------------------------------------------------------------------- matchroom

def classic_links(h):
    p = h.p
    p.evaluate("location.hash = 'scout/tools'")
    p.wait_for_selector("#hub-tools-body .h-tool-grid a")
    tools = p.locator("#hub-tools-body a").evaluate_all("as => as.map(a => a.getAttribute('href'))")
    more = p.locator("#hub-more a").evaluate_all("as => as.map(a => a.getAttribute('href'))")
    return tools, more


def into_matchroom(h, link):
    """the link opens the classic match page (the hub hides); a match there opens its matchroom"""
    p = h.p
    assert link.get_attribute("href") == "/?view=classic#matches"
    h.tall(link).click()
    p.wait_for_selector("#boardout .mgroup[open] .brow2.crow", timeout=30000)
    assert "/?view=classic#matches" in p.url and p.locator("#u-hub").is_hidden()
    p.wait_for_selector("[data-ot-match]", timeout=20000)
    p.locator("[data-ot-match]").first.click()
    p.wait_for_function("document.querySelector('#ot-match-desk').open")
    names = p.locator("#ot-match-desk .ot-desk-player strong").all_inner_texts()
    assert "vargo" in names and "brandr" in names, names
    p.keyboard.press("Escape")
    p.wait_for_function("!document.querySelector('#ot-match-desk').open")
    assert not p.errors


@SIZES
def test_matchroom_opens_from_the_tools_section_and_the_more_sheet(browser, live, size):
    with Hub(browser, live, "old", size) as o:
        old_tools, old_more = classic_links(o)
    assert "/?view=classic#matches" in old_tools and "/?view=classic#matches" in old_more
    with Hub(browser, live, "next", size) as n:
        p = n.p
        tools, more = classic_links(n)
        assert tools == old_tools and [x for x in more if x in old_more] == old_more        # every link the design before it had
        into_matchroom(n, p.locator("#hub-tools-body a", has_text="Open match research"))
        back = p.locator(".workspace-tools .h-entry")                                       # and the way back is the redesign
        assert back.count() == 1
        back.click()
        p.wait_for_selector("#u-hub:not([hidden]) #hub-rows tr[data-record]", timeout=30000)
        assert p.locator("html").get_attribute("data-ui") == "next"
        if n.phone:                                                                         # the phone's More sheet
            n.tall(p.locator("#hub-more-toggle")).click()
            assert p.locator("#hub-more").is_visible()
            into_matchroom(n, p.locator("#hub-more a", has_text="Match research"))
        else:                                                                               # desktop: Tools is in the side nav
            assert p.locator("#u-hub .h-sidebar nav [data-id=tools]").is_visible() and p.locator("#u-hub .h-tabbar").is_hidden()


# ---------------------------------------------------------------------------------------------------------------------- bump

def bump_texts(h):
    """what the design says about vargo's bumped line: the row's note, the card's header and the card's line-move sentence"""
    p = h.p
    row = flat(h.row("vargo").locator(".h-c-line small").inner_text())
    h.card(VARGO_UD)
    head = flat(p.locator("#hub-detail .h-card-id p").inner_text())
    why = [flat(t) for t in p.locator("#hub-detail .h-why li").all_inner_texts() if "line moved" in t.lower()]
    return row, head, why


@SIZES
def test_a_bumped_line_says_so_on_the_row_the_ladder_line_and_the_card_header(browser, live, size):
    with Hub(browser, live, "old", size) as o:
        old_row, old_head, old_why = bump_texts(o)
    assert old_row.endswith("· was 37.5") and old_head.endswith("· moved from 37.5")
    assert len(old_why) == 1 and old_why[0].endswith("line moved up from 37.5 to 38.5 on underdog (harder for the over)")
    with Hub(browser, live, "next", size) as n:
        p = n.p
        # the row: the same "was 37.5", whole and inside the Line column; a line that did not move says nothing
        row = n.row("vargo")
        was = row.locator(".h-c-line .h-was")
        assert was.inner_text() == "was 37.5" == old_row.split("· ")[-1]
        b, cell = n.in_view(was), row.locator(".h-c-line").evaluate(BOX)
        assert b["x"] >= cell["x"] - 0.5 and b["r"] <= cell["r"] + 0.5 and cell["w"] >= 60, (b, cell)
        assert flat(row.locator(".h-c-line small").inner_text()).replace(" was", " · was") == old_row
        assert row.locator(".h-c-line b").evaluate("b => b.firstChild.textContent") == "38.5"
        assert n.row("orlo").locator(".h-was").count() == 0 and "was" not in n.row("orlo").locator(".h-c-line").inner_text()
        # the ladder: the bumped lines (Underdog and PrizePicks standard) carry it, the goblin and demon do not
        lad = n.ladder(row)
        notes = {tr.get_attribute("data-line"): tr.locator(".h-ld-line small").all_inner_texts() for tr in lad.locator("tbody tr").all()}
        assert notes == {"cs2|prizepicks|pp:7011": [], VARGO_PP: ["was 37.5"], VARGO_UD: ["was 37.5"], "cs2|prizepicks|pp:7012": []}
        for key in (VARGO_PP, VARGO_UD):
            n.in_view(lad.locator(f'tr[data-line="{key}"] .h-ld-line small'))
        if n.phone:                                           # the ladder fits the phone: its last control is on the screen
            n.in_view(lad.locator(".h-ld-act").first)
        # the card: the header and the line-move sentence, word for word
        _, head, why = bump_texts(n)
        assert head == old_head and why == old_why
        n.in_view(p.locator("#hub-detail .h-card-id p"))
        no_h_scroll(p)
        assert not p.errors


# --------------------------------------------------------------------------------------------------------------- discrepancy

def gap_texts(h):
    """orlo: Underdog 33.5 against PrizePicks 34.5. The row's chip (text + its sentence), the card's Lines tab chip, and the rows
    the "Lines differ" filter keeps."""
    p = h.p
    chip = h.row("orlo").locator(".h-gap")
    row = (chip.inner_text(), chip.get_attribute("title"))
    h.card(ORLO_UD)
    h.tab("lines")
    p.wait_for_selector("#hub-detail .h-best-lines .h-gap")
    c = p.locator("#hub-detail .h-best-lines .h-gap")
    card = (c.inner_text(), c.get_attribute("title"))
    shop = flat(p.locator("#hub-detail .h-line-shop").inner_text())
    h.close_card()
    popped = not p.locator("#hub-diff").is_visible()                  # in the Filters pop-over where the toolbar has no room for it
    if popped:
        h.tall(p.locator("#hub-filters-toggle")).click()
    h.tall(p.locator("#hub-diff")).click()
    p.wait_for_function("document.getElementById('hub-diff').getAttribute('aria-pressed') === 'true'")
    if popped:
        p.keyboard.press("Escape")
    kept = sorted(set(p.locator("#hub-rows tr.h-row .h-player b").all_inner_texts()))
    return row, card, shop, kept


@SIZES
def test_a_line_discrepancy_is_on_the_row_and_on_the_card_lines_tab(browser, live, size):
    with Hub(browser, live, "old", size) as o:
        old_row, old_card, old_shop, old_kept = gap_texts(o)
    assert old_row[0] == "≠ PrizePicks 34.5" and old_row[1].startswith("Line discrepancy: PrizePicks lists 34.5 for the same player, stat and maps, 1 higher than this line.")
    assert old_card == old_row and old_kept == ["orlo", "vargo"]          # vargo: PrizePicks' goblin and demon sit off Underdog's line
    with Hub(browser, live, "next", size) as n:
        p = n.p
        n.in_view(n.row("orlo").locator(".h-gap"))
        assert p.locator("#hub-rows .h-gap").count() == 1                               # only where the apps differ by a full point
        row, card, shop, kept = gap_texts(n)
        assert row == old_row and card == old_card and kept == old_kept
        assert "underdog" in shop and "prizepicks" in shop and "33.5" in shop and "34.5" in shop and shop == old_shop
        n.card(ORLO_UD)
        n.tab("lines")
        n.in_view(p.locator("#hub-detail .h-best-lines .h-gap"))
        no_h_scroll(p)
        assert not p.errors


# ------------------------------------------------------------------------------------------------- PrizePicks pre-fill link

def pp_link(h):
    """vargo PrizePicks 38.5 UNDER chosen on the card (reached through the ladder on the redesign), orlo PrizePicks 34.5 Over added
    from its line (the ladder on the redesign, the board row before it); then the checked slip's Open in PrizePicks."""
    p = h.p
    h.card(VARGO_PP)
    h.side("under")
    add = p.locator("#hub-decision [data-hub=detail-add]")
    assert add.inner_text() == "Add Under 38.5"
    h.tall(add).click()
    p.wait_for_function("document.getElementById('hub-tab-slip-n').textContent === '1'")
    h.close_card()
    if h.ui == "next":
        line = h.ladder(h.row("orlo")).locator(f'tr[data-line="{ORLO_PP}"]')
        assert line.locator("th").inner_text().split()[0] == "PrizePicks" and line.locator(".h-ld-line b").inner_text() == "34.5"
        act = line.locator(".h-ld-act")
        assert act.get_attribute("aria-label") == "Add orlo Over 34.5 on PrizePicks to slip"
        h.in_view(act)
        h.tall(act).click()
    else:
        p.locator("#hub-q").fill("orlo")
        if p.locator("#hub-rows .h-prop-toggle[aria-expanded=false]").count():
            p.locator("#hub-rows .h-prop-toggle[aria-expanded=false]").first.click()
        h.tall(p.locator(f'#hub-rows tr[data-record="{ORLO_PP}"] .h-add')).click()
    p.wait_for_function("document.getElementById('hub-tab-slip-n').textContent === '2'")
    p.evaluate("location.hash = '/slips'")
    p.wait_for_function("document.querySelector('#hub-studio-body').textContent.includes('1 of 1 entry ready')", timeout=20000)
    legs = flat(p.locator("#hub-studio-body").inner_text())
    assert "under 38.5 kills" in legs and "over 34.5 kills" in legs
    btn = p.locator("#hub-studio-body [data-u=open-entry]")
    assert btn.inner_text() == "Open in PrizePicks"
    h.tall(btn).click()
    box = p.locator("#u-book-open")
    box.wait_for(state="visible")
    a = box.locator("a").first
    out = (a.get_attribute("href"), a.inner_text(), a.get_attribute("target"), a.get_attribute("rel"), flat(box.inner_text()))
    h.tall(a)
    b = box.bounding_box()
    assert b["x"] >= 0 and b["x"] + b["width"] <= h.size["width"] + 0.5
    h.tall(box.locator(".u-x")).click()
    assert p.evaluate("fetch('/api/upgrade/mine').then(r => r.json())")["receipts"] == []        # opening a link tracks nothing
    return out


@SIZES
def test_prizepicks_link_is_prefilled_for_the_side_and_line_chosen_on_the_card_and_the_ladder(browser, live, size):
    with Hub(browser, live, "old", size) as o:
        want = pp_link(o)
    assert want[0] == "https://app.prizepicks.com/board?projections=7001-u-38.5,7003-o-34.5"        # <id>-<o|u>-<line>, in slip order
    assert want[1:4] == ("Open in PrizePicks", "_blank", "noopener noreferrer")
    with Hub(browser, live, "next", size) as n:
        assert pp_link(n) == want
        no_h_scroll(n.p)
        assert not n.p.errors
    # flag off: no button on the checked slip (the redesign reads the same feature flag)
    live.mp.setattr(server, "PP_OPEN_LINK", False)
    with Hub(browser, live, "next", size) as n:
        p = n.p
        for name in ("vargo", "orlo"):
            line = n.ladder(n.row(name)).locator(f'tr[data-line="{VARGO_PP if name == "vargo" else ORLO_PP}"]')
            line.locator(".h-ld-act").click()
        p.evaluate("location.hash = '/slips'")
        p.wait_for_function("document.querySelector('#hub-studio-body').textContent.includes('1 of 1 entry ready')", timeout=20000)
        assert p.locator("[data-u=open-entry]").count() == 0 and p.locator("[data-hub=open-line]").count() == 0


# ------------------------------------------------------------------------------------------------------------ Underdog link

def ud_link(h, key, side):
    """the card's Open in Underdog for one Underdog line and side -> the dialog's link and words"""
    p = h.p
    h.card(key)
    h.side(side)
    btn = p.locator("#hub-decision [data-hub=open-line]")
    assert btn.inner_text() == "Open in Underdog" and btn.is_enabled()
    h.in_view(btn)
    h.tall(btn).click()
    box = p.locator("#u-book-line")
    box.wait_for(state="visible")
    a = box.locator("a")
    out = (a.get_attribute("href"), a.inner_text(), a.get_attribute("target"), a.get_attribute("rel"), flat(box.inner_text()))
    h.tall(a)
    h.tall(box.locator(".u-x")).click()
    box.wait_for(state="hidden")
    h.close_card()
    return out


@SIZES
def test_underdog_link_opens_the_chosen_line_from_the_card(browser, live, size):
    asks = (ORLO_UD, "over"), (VARGO_UD, "under")
    with Hub(browser, live, "old", size) as o:
        want = [ud_link(o, k, sd) for k, sd in asks]
    for (key, sd), w in zip(asks, want):
        assert w[0] == pplink.ud_link(key.split("|")[-1]) and pplink.ud_link_ok(w[0])           # that line's own market screen
        assert w[1:4] == ("Open in Underdog", "_blank", "noopener noreferrer")
    assert "over 33.5 kills · maps 1-2" in want[0][4] and "under 38.5 kills · maps 1-2" in want[1][4] and "choose the side in underdog" in want[0][4]
    with Hub(browser, live, "next", size) as n:
        p = n.p
        assert [ud_link(n, k, sd) for k, sd in asks] == want
        # a PrizePicks line's card (through the ladder) never offers Open in Underdog
        n.card(VARGO_PP)
        assert p.locator("#hub-decision [data-hub=open-line]").count() == 0
        assert p.evaluate("fetch('/api/upgrade/mine').then(r => r.json())")["receipts"] == []
        no_h_scroll(p)
        assert not p.errors
