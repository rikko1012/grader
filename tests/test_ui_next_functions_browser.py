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
    """One member's CS2 board on one design. ui: 'next' (the redesign, UI_NEXT=1) or 'old' (the default again since 2026-11-13, UI_NEXT unset)."""

    def __init__(self, browser, env, ui, size, path="/"):
        if ui == "next":
            env.mp.setenv("UI_NEXT", "1")
        else:
            env.mp.delenv("UI_NEXT", raising=False)
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
    row = __import__("re").sub(r"\s*·?\s*was ", " · was ", flat(h.row("vargo").locator(".h-c-line small").text_content()))
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
        assert was.text_content() == "was 37.5" == old_row.split("· ")[-1]
        if not n.phone:                     # phone simplification (owner 2026-11-12): the phone row drops app · age and bump notes; card + ladder say it
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
    row = (chip.text_content().strip(), chip.get_attribute("title"))
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
        if not n.phone:                     # the phone row drops the chip (owner 2026-11-12); the card's Lines tab carries it
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


# --------------------------------------------- UI sweep 2026-11-12 #24: Grader's side not offered on the app (Over-only demon)

DEMON = "cs2|prizepicks|pp:7012"            # vargo's PrizePicks demon 42.5, Grader Under 61.1%; here PrizePicks offers only More


@pytest.fixture
def over_only(live):
    next(r for r in live.rows if r["id"] == "pp:7012").update(under_available=False)
    workspace_api.reset()
    return live


@SIZES
def test_card_opens_on_scouts_side_when_the_app_offers_only_the_other(browser, over_only, size):
    with Hub(browser, over_only, "next", size) as h:
        p = h.p
        h.card(DEMON)
        p.wait_for_timeout(300)
        fix_shot(p, f"over-only-card-{size['width']}", before=True)
        dec = p.locator("#hub-decision")
        assert dec.locator("[data-hub=history-side][data-id=under]").get_attribute("aria-pressed") == "true"
        assert "61.1%" in dec.locator(".h-dec-chance b").inner_text()
        assert dec.locator(".h-dec-add").count() == 0                                      # no main Add for a side the app does not offer
        note = flat(dec.locator(".h-dec-unoffered .h-dec-note").inner_text())
        # CS2 follows the NBA / NFL "Not offered" rule (owner 2026-11-12): no letter, no stake, no add for the other side
        assert "not offered: prizepicks does not offer under 42.5, scout's side." in note, note
        assert dec.locator("[data-hub=detail-add-other]").count() == 0 and dec.locator(".h-grade").count() == 0
        assert "add over" not in flat(dec.inner_text())
        fix_shot(p, f"over-only-card-{size['width']}")
        assert not p.errors


def fix_shot(p, name, before=False):
    """FIX_SHOTS=<dir> FIX_TAG=before|after; the test's own pre-check frame is taken only on a 'before' run"""
    import os
    tag = os.environ.get("FIX_TAG", "after")
    if os.environ.get("FIX_SHOTS") and before == (tag == "before"):
        from pathlib import Path
        out = Path(os.environ["FIX_SHOTS"])
        out.mkdir(parents=True, exist_ok=True)
        p.screenshot(path=str(out / f"{name}-{tag}.png"))


# ---------------------------- UI sweep 2026-11-12 H1 / H13 / #30: the hub owns the /sports address and the tab title

@SIZES
@pytest.mark.parametrize("v2", ["1", "0"], ids=["v2", "no-v2"])
def test_nfl_tab_survives_reload_back_forward_and_deep_links_with_matching_title(browser, live, size, v2):
    live.mp.setenv("UI_NEXT", "1")
    live.mp.setenv("SCOUT_WORKSPACE_V2", v2)              # the live site runs with V2 on (#/board?sport=); off: #scout/board?sport=
    c = context(browser, live, size)
    try:
        p = open_page(c, live, "/sports#/board?sport=nfl", size)

        def on(sport):
            p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
            p.wait_for_timeout(600)                       # the classic sports page under the hub has had its turn
            assert p.locator(f"#hub-sports [data-id={sport}]").get_attribute("aria-pressed") == "true", (sport, p.url, p.title())
            assert f"sport={sport}" in p.url and "lg=" not in p.url, p.url
            assert p.title() == "Prop Grader · " + sport.upper(), p.title()
            assert all(x.startswith(sport + "|") for x in p.locator("#hub-rows tr[data-record]").evaluate_all("rs=>rs.map(r=>r.dataset.record)"))

        on("nfl")
        p.reload()
        on("nfl")
        p.locator("#hub-sports [data-id=nba]").click()
        on("nba")
        p.go_back()
        on("nfl")
        p.go_forward()
        on("nba")
        p.goto(live.url + "/sports#/board?lg=nfl")       # an old sports link still lands on NFL
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
        p.wait_for_timeout(600)
        assert p.locator("#hub-sports [data-id=nfl]").get_attribute("aria-pressed") == "true"
        assert p.title() == "Prop Grader · NFL"
        p.locator("#hub-sports [data-id=cs2]").click()
        p.wait_for_timeout(400)
        assert p.title() == "Prop Grader · CS2"
        assert not p.errors
    finally:
        c.close()


# ------------------------- UI sweep 2026-11-12 H2: the card's player-markets strip shows each stat's main line, not an alt rung

@SIZES
def test_player_markets_strip_shows_the_main_line_per_stat(browser, live, size):
    from tests.test_ui_next_browser import ladder_board
    live.mp.setenv("UI_NEXT", "1")
    live.sports.boards["nba"] = ladder_board()
    workspace_api.reset()
    c = context(browser, live, size)
    try:
        p = open_page(c, live, "/sports#/board?sport=nba", size)
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
        p.locator("#hub-q").fill("Shai Ladder")
        p.wait_for_timeout(300)
        row = p.locator("#hub-rows tr.h-row", has=p.locator("[data-hub=ladder]")).filter(has_text="Points").first
        row.locator("[data-hub=ladder]").click()
        p.wait_for_selector("tr.h-ladder-row .h-ladder-table tbody tr")
        bov = p.locator("tr.h-ladder-row tr[data-line]", has_text="27.5").first
        bov.locator(".h-ld-book").click()
        p.wait_for_selector("#hub-detail .h-player-markets", timeout=20000)

        def strip():
            p.wait_for_timeout(300)
            return [(" ".join(b.inner_text().split()), b.get_attribute("aria-pressed")) for b in p.locator("#hub-detail .h-player-markets button").all()]

        s = strip()
        assert ("Points 27.5 Full game", "true") in s and ("Rebounds 5.5 Full game", "false") in s, s      # the card's own line, pressed
        p.locator("#hub-detail .h-player-markets button", has_text="Rebounds").click()
        p.wait_for_function("document.querySelector('#hub-card-title')?.textContent.includes('Rebounds')", timeout=20000)
        s = strip()
        assert ("Points 31.5 Full game", "false") in s and ("Rebounds 5.5 Full game", "true") in s, s      # main line: most apps, PP / UD
        p.locator("#hub-detail .h-player-markets button", has_text="Points").click()
        p.wait_for_function("document.querySelector('#hub-card-title')?.textContent.includes('Points')", timeout=20000)
        assert ("Points 31.5 Full game", "true") in strip()
        assert "31.5" in p.locator("#hub-card-line option:checked").inner_text()
        assert not p.errors
    finally:
        c.close()


# ------------------------------- UI sweep 2026-11-12 H4: the open card survives a refresh that moves its line

@SIZES
def test_open_card_reloads_in_place_when_its_line_moves(browser, live, size):
    with Hub(browser, live, "next", size) as h:
        p = h.p
        h.card(VARGO_UD)
        h.tab("stats")
        p.locator("#hub-card-body").evaluate("b=>b.scrollTop=120")
        top = p.locator("#hub-card-body").evaluate("b=>b.scrollTop")
        uid = VARGO_UD.split("|")[-1]

        def moved(rec):
            if rec.get("offer", {}).get("offer_id") == uid and rec.get("offer", {}).get("platform") == "underdog":
                rec["offer"].update(line=39.5, revision="rev-moved")

        def board(route):                                    # the next board read: Underdog moved vargo's line 38.5 -> 39.5
            h = {k: v for k, v in route.request.headers.items() if k.lower() != "if-none-match"}
            resp = route.fetch(headers=h)
            d = resp.json()
            for rec in d.get("records") or []:
                moved(rec)
            route.fulfill(response=resp, json=d, headers={k: v for k, v in resp.headers.items() if k.lower() not in ("etag", "content-length", "content-encoding")})

        def line(route):
            resp = route.fetch()
            d = resp.json()
            moved(d.get("record") or {})
            route.fulfill(response=resp, json=d, headers={k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")})
        p.route("**/api/upgrade/board*", board)
        p.route(f"**/api/upgrade/line?*id={uid}*", line)
        p.evaluate("document.querySelector('#u-hub [data-hub=refresh]').click()")      # the board's refresh (the 60 s poll does the same)
        p.wait_for_function("[...document.querySelectorAll('#hub-card-line option')].some(o=>o.selected&&o.textContent.includes('39.5'))", timeout=20000)
        p.wait_for_function("!document.querySelector('#hub-detail .h-detail-loading')", timeout=20000)
        p.wait_for_timeout(500)
        assert not p.locator("#hub-card").is_hidden()
        assert p.locator("#hub-detail").get_attribute("data-tab") == "stats"
        assert "offer changed" not in p.locator("#hub-detail").inner_text()
        assert abs(p.locator("#hub-card-body").evaluate("b=>b.scrollTop") - top) <= 2
        assert not p.errors


# ------------------------------- UI sweep 2026-11-12 #31: "No comparable game log" never sits above "Went Over in 6 of the last 10"

@SIZES
def test_no_empty_game_log_message_when_the_card_has_counts(browser, live, size):
    with Hub(browser, live, "next", size) as h:
        p = h.p

        def no_series(route):                                # the live NBA / NFL case: no game log for this stat, the board's saved counts only
            resp = route.fetch()
            d = resp.json()
            if isinstance(d.get("history"), dict):
                d["history"]["series"] = []
            route.fulfill(response=resp, json=d, headers={k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")})
        p.route("**/api/upgrade/line?*", no_series)
        h.row("vargo").locator(".h-player").click()
        p.wait_for_selector("#hub-detail .h-why", timeout=20000)
        p.wait_for_timeout(1500)
        txt = flat(p.locator("#hub-card-body").inner_text())
        assert "went over in 6 of the last 10" in txt, txt[:300]
        assert "no comparable game log" not in txt, txt[:600]
        assert not p.errors


# --------------------------- bug 3 (owner 2026-11-12, "Not offered"): NBA / NFL lines where the app does not offer Grader's side

def not_offered_board():
    """the fixture NBA board plus: Nora Offset rebounds, PrizePicks 8.5 Over only and Underdog 8.5 both sides (an exact-match line),
    Grader Under 69.2% A on both; Nate Near assists, PrizePicks 4.5 Over only and Underdog 5.5 (a near line, never the alternate)."""
    from tests.workspace_other_fixture import market as mk, nba_board, quote as qt, forecast as fc
    doc, view = nba_board()
    x = {"v": "cf-2", "ln": "under", "pl": 0.692, "gr": "A", "e": 9.1, "g": 9.1, "c": 3, "cw": [], "s": 3.0}
    nora = mk("nba", "nba:espn:1", "nba:player:espn:351", "Nora Offset", "F", "BOS", "nba:team:espn:BOS", "rebounds", [
        qt("prizepicks", "pp:351", 8.5, "fc-bbbbbbbb00000001", over=1, under=0, x=x),
        qt("underdog", "ud:351", 8.5, "fc-bbbbbbbb00000002", mult=[1.0, 1.0], x=x)])
    nate = mk("nba", "nba:espn:2", "nba:player:espn:352", "Nate Near", "G", "MIA", "nba:team:espn:MIA", "assists", [
        qt("prizepicks", "pp:352", 4.5, "fc-bbbbbbbb00000003", over=1, under=0, x=x),
        qt("underdog", "ud:352", 5.5, "fc-bbbbbbbb00000004", mult=[1.0, 1.0], x=dict(x, pl=0.66))])
    doc["markets"] += [nora, nate]
    doc["fc"].update({"fc-bbbbbbbb00000001": fc(0.308, 0.692, 6.9, line=8.5), "fc-bbbbbbbb00000002": fc(0.308, 0.692, 6.9, line=8.5),
                      "fc-bbbbbbbb00000003": fc(0.308, 0.692, 3.4, line=4.5), "fc-bbbbbbbb00000004": fc(0.34, 0.66, 3.4, line=5.5)})
    return doc, view


@SIZES
def test_not_offered_side_shows_no_letter_edge_or_add_and_points_to_the_exact_match(browser, live, size):
    live.mp.setenv("UI_NEXT", "1")
    live.sports.boards["nba"] = not_offered_board()
    workspace_api.reset()
    c = context(browser, live, size)
    try:
        p = open_page(c, live, "/sports#/board?sport=nba", size)
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)

        def line_row(name, platform):
            p.locator("#hub-q").fill(name)
            p.wait_for_timeout(400)
            row = p.locator("#hub-rows tr.h-row", has_text=name).first
            if platform not in (row.get_attribute("data-record") or ""):
                row.locator("[data-hub=ladder]").click()
                p.wait_for_selector("tr.h-ladder-row tr[data-line]")
                return None, p.locator(f'tr.h-ladder-row tr[data-line*="{platform}"]').first
            return row, None

        # Nora: the PrizePicks line (Over only) - Grader's side, chance and projection stay; no letter, no edge, no add for Under
        row, lad = line_row("Nora Offset", "prizepicks")
        p.locator("[data-hub=group-by][data-id=prop]").count()
        both = [x for x in (row, lad) if x is not None][0]
        txt = flat(both.inner_text())
        assert "under" in txt and "69.2%" in txt and "not offered" in txt, txt
        assert both.locator(".h-grade").count() == 0, txt                              # no letter
        if row is not None:
            assert flat(row.locator(".h-c-edge").inner_text() if row.locator(".h-c-edge").count() else "—") in ("—", "")
            assert "under 8.5 on underdog" in txt
            btn = row.locator("[data-hub=add-alt]")
            assert btn.count() == 1 and "underdog" in (btn.get_attribute("aria-label") or "").lower()
        fix_shot(p, f"not-offered-row-{size['width']}")
        # the card opens on Grader's side, says Not offered, and adds the exact-match Underdog line, not the Over
        (row.locator(".h-player") if row is not None else lad.locator(".h-ld-book")).click()
        p.wait_for_selector("#hub-detail .h-why", timeout=20000)
        p.wait_for_timeout(400)
        dec = p.locator("#hub-decision")
        if "prizepicks" not in p.locator("#hub-card-line").input_value():
            p.locator("#hub-card-line").select_option(value=[o for o in p.locator("#hub-card-line option").evaluate_all("os=>os.map(o=>o.value)") if "prizepicks" in o][0])
            p.wait_for_timeout(600)
        assert dec.locator("[data-hub=history-side][data-id=under]").get_attribute("aria-pressed") == "true"
        dtxt = flat(dec.inner_text())
        assert "not offered" in dtxt and "add over" not in dtxt and "69.2%" in dtxt, dtxt
        assert dec.locator(".h-grade").count() == 0
        gi = flat(p.locator("#hub-card-body .h-gi").inner_text()) if p.locator("#hub-card-body .h-gi").count() else ""
        assert "stake" not in gi and ("not offered" in gi or not gi), gi
        fix_shot(p, f"not-offered-card-{size['width']}")
        alt = dec.locator("[data-hub=add-alt]")
        assert "add under 8.5 on underdog" in flat(alt.inner_text())
        alt.click()
        p.wait_for_function("document.getElementById('hub-slip-count').textContent.trim()==='1'", timeout=5000)
        slip = flat(p.locator("#hub-slip-content").inner_text()) if size is DESK else ""
        assert not slip or ("nora offset" in slip and "under" in slip and "8.5" in slip), slip
        p.locator("#hub-detail [data-hub=card-close]").click()
        # Nate: a near line on another app (Underdog 5.5) is never offered as the alternate
        row, lad = line_row("Nate Near", "prizepicks")
        both = [x for x in (row, lad) if x is not None][0]
        txt = flat(both.inner_text())
        assert "not offered" in txt and "underdog" not in txt.replace("underdog 5.5", ""), txt
        assert both.locator("[data-hub=add-alt]").count() == 0
        assert not p.errors
    finally:
        c.close()


def test_auto_build_never_takes_a_side_the_app_does_not_offer():
    import slip_quality
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = {"sport": "nba", "state": {"kind": "ok"}, "event": {"start_utc": start, "status": "scheduled"},
           "offer": {"platform": "prizepicks", "line": 8.5, "over": {"offered": True}, "under": {"offered": False}},
           "scout": {"side": "under", "p_over": 0.308, "p_under": 0.692, "projection": 6.9, "grade": "A"}}
    import inspect
    fn = next(f for n, f in inspect.getmembers(slip_quality, inspect.isfunction) if "return \"side\"" in inspect.getsource(f))
    args = inspect.signature(fn).parameters
    kw = {k: v for k, v in (("rec", rec), ("side", "under"), ("now", datetime.now(timezone.utc)), ("feeds", None)) if k in args}
    assert fn(**kw) == "side"



# ------------------------------------- T5: the book-link dialog (Open in Underdog) is readable in the light theme, in the new design's type

@pytest.mark.parametrize("theme", ["light", "dark"])
def test_book_link_dialog_is_readable_in_both_themes(browser, live, theme):
    from tests.test_ui_next_mobile_overlap_browser import SCAN, fix_shot as shot
    live.mp.setenv("UI_NEXT", "1")
    with Hub(browser, live, "next", PHONE) as h:
        p = h.p
        p.evaluate(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
        p.reload()
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        h.card(VARGO_UD)
        btn = p.locator("#hub-decision [data-hub=open-line]")
        h.tall(btn).click()
        box = p.locator("#u-book-line")
        box.wait_for(state="visible")
        p.wait_for_timeout(200)
        shot(p, f"dialog-book-link-{theme}")
        assert p.evaluate(SCAN, "#u-book-line") == [], theme
        font = box.evaluate("b=>getComputedStyle(b).fontFamily")
        assert "Plex" in font or "system-ui" in font, font                        # the new design's type, not the old Outfit
        assert not p.errors



# ---------------- owner 2026-11-12: "Not offered" on CS2 too; the held "Unavailable - side not offered" line is shown, not left off

@pytest.fixture
def cs2_not_offered(live):
    from tests.workspace_fixture import row
    soon = iso(datetime.now(timezone.utc).replace(microsecond=0) + __import__("datetime").timedelta(hours=3))
    held = "Unavailable—side not offered"
    # vargo: PrizePicks 40.5 Maps 1-2, Over only, Grader Under -> held by the grader; Underdog lists the exact 40.5 Maps 1-2 Under
    live.rows.append(row("pp:7401", "prizepicks", "vargo", "Halcyon", "Norrland", 40.5, "Less", 0.634, 900, live.rows[0]["start"], grade="B",
                         under_available=False, unavailable=held))
    live.rows.append(row("11111111-0000-4000-8000-0000000000c1", "underdog", "vargo", "Halcyon", "Norrland", 40.5, "Less", 0.634, 900, live.rows[0]["start"]))
    # sable: PrizePicks 9.5 Headshots Map 1, Over only, Grader Under; Underdog's 9.5 is Maps 1-2 (another scope) -> never the alternate
    live.rows.append(row("pp:7402", "prizepicks", "sable", "Vanta", "Kestrel Five", 9.5, "Less", 0.6, 901, soon, prop="hs", scope="1",
                         under_available=False, unavailable=held))
    live.rows.append(row("11111111-0000-4000-8000-0000000000c2", "underdog", "sable", "Vanta", "Kestrel Five", 9.5, "Less", 0.6, 901, soon, prop="hs", scope="12"))
    workspace_api.reset()
    return live


@SIZES
def test_cs2_not_offered_row_shows_and_points_to_the_exact_match_only(browser, cs2_not_offered, size):
    with Hub(browser, cs2_not_offered, "next", size) as h:
        p = h.p
        key = "cs2|prizepicks|pp:7401"
        h.card(key)
        dec = p.locator("#hub-decision")
        assert dec.locator("[data-hub=history-side][data-id=under]").get_attribute("aria-pressed") == "true"
        txt = flat(dec.inner_text())
        assert "63.4%" in txt and "not offered" in txt and "add over" not in txt and "stake" not in txt, txt
        assert dec.locator(".h-grade").count() == 0
        alt = dec.locator("[data-hub=add-alt]")
        assert "add under 40.5 on underdog" in flat(alt.inner_text())
        h.tall(alt).click()
        p.wait_for_function("document.getElementById('hub-slip-count').textContent.trim()==='1'", timeout=5000)
        h.close_card()
        # the held line is on the board (it was left off) as a Not offered line, and the Map 1 line has no alternate (Maps 1-2 differs)
        p.locator("#hub-q").fill("sable")
        p.wait_for_timeout(400)
        found = p.evaluate("""()=>[...document.querySelectorAll('#hub-rows tr[data-record], tr.h-ladder-row tr[data-line]')].map(r=>r.dataset.record||r.dataset.line)""")
        if "cs2|prizepicks|pp:7402" not in found:
            for b in p.locator("#hub-rows [data-hub=ladder]").all():
                b.click()
                p.wait_for_timeout(200)
            found = p.evaluate("""()=>[...document.querySelectorAll('#hub-rows tr[data-record], tr.h-ladder-row tr[data-line]')].map(r=>r.dataset.record||r.dataset.line)""")
        assert "cs2|prizepicks|pp:7402" in found, found
        k2 = "cs2|prizepicks|pp:7402"
        if p.locator(f'#hub-rows tr[data-record="{k2}"]').count():
            p.locator(f'#hub-rows tr[data-record="{k2}"] .h-c-line').click()
        else:
            p.locator(f'tr.h-ladder-row tr[data-line="{k2}"] .h-ld-book').click()
        p.wait_for_function("document.querySelector('#hub-card-line')&&document.querySelector('#hub-card-line').value.endsWith('pp:7402')", timeout=20000)
        p.wait_for_timeout(500)
        dec = p.locator("#hub-decision")
        assert "not offered" in flat(dec.inner_text()) and dec.locator("[data-hub=add-alt]").count() == 0
        assert not p.errors


@SIZES
def test_lol_not_offered_line_with_its_exact_underdog_line(browser, live, size):
    """LoL follows the same rule: Harky PrizePicks 10.5 Maps 1-3 offers only the More; Grader leans Under (69.8%) -> Not offered, no
    letter, no add for the Under there; Underdog's 10.5 Maps 1-3 Under is the exact match and adds from the card."""
    harky = next(r for r in live.lol.rows if r["player"] == "Harky" and r["platform"] == "prizepicks")
    harky["sides"] = {"basis": "allowed_wager_types", "higher": {"offered": True}, "lower": {"offered": False}}
    live.lol.write()
    workspace_api.reset()
    live.mp.setenv("UI_NEXT", "1")
    c = context(browser, live, size)
    try:
        p = open_page(c, live, "/#scout/board?sport=lol", size)
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        p.locator("#hub-q").fill("Harky")
        p.wait_for_timeout(500)
        key = p.evaluate("""()=>{for(const b of document.querySelectorAll('#hub-rows [data-hub=ladder]'))b.click();
            return [...document.querySelectorAll('#hub-rows tr[data-record], tr.h-ladder-row tr[data-line]')].map(r=>r.dataset.record||r.dataset.line).find(k=>k.startsWith('lol|prizepicks'))}""")
        assert key, "no PrizePicks Harky line"
        p.wait_for_timeout(300)
        loc = p.locator(f'#hub-rows tr[data-record="{key}"] .h-c-line')
        (loc if loc.count() else p.locator(f'tr.h-ladder-row tr[data-line="{key}"] .h-ld-book')).first.click()
        p.wait_for_function("document.querySelector('#hub-card-line')&&document.querySelector('#hub-card-line').value.startsWith('lol|prizepicks')", timeout=20000)
        p.wait_for_timeout(600)
        dec = p.locator("#hub-decision")
        assert dec.locator("[data-hub=history-side][data-id=under]").get_attribute("aria-pressed") == "true"
        txt = flat(dec.inner_text())
        assert "not offered" in txt and "add over" not in txt and dec.locator(".h-grade").count() == 0, txt
        alt = dec.locator("[data-hub=add-alt]")
        assert "add under 10.5 on underdog" in flat(alt.inner_text())
        alt.click()
        p.wait_for_function("document.getElementById('hub-slip-count').textContent.trim()==='1'", timeout=5000)
        assert not p.errors
    finally:
        c.close()
