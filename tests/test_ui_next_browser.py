"""UI_NEXT, the redesigned hub (owner 2026-11-11), in a real browser against the real Handler with fixture data only.
The preview route and the one-flag default; one board row per player + stat with its ladder of lines; filters, search, sorting and the
slip on grouped rows; rows by player; keyboard; the card beside the board and as a phone sheet; both themes; a 6,000-line board."""
import json
import re
from datetime import timedelta

import pytest

import server
import workspace_api
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll  # noqa: F401
from tests.workspace_other_fixture import event, forecast, iso, market, nba_board, quote, soon

DESK, PHONE = {"width": 1440, "height": 900}, {"width": 390, "height": 844}
LADDER = [("bovada", 27.5), ("bovada", 28.5), ("bovada", 29.5), ("bovada", 30.5), ("fanduel", 31.5), ("prizepicks", 31.5), ("betrivers", 32.5), ("underdog", 32.5),
          ("bovada", 33.5), ("bovada", 34.5), ("bovada", 35.5), ("polymarket", 24.5), ("polymarket", 29.5), ("polymarket", 34.5), ("polymarket", 39.5)]


def ladder_board():
    """The fixture NBA board plus one prop the way the books list it: 15 lines over 6 apps, each with its own forecast."""
    doc, view = nba_board()
    qs = []
    for i, (book, line) in enumerate(LADDER):
        fid = f"fc-dddddddd{i:08x}"
        po = round(min(0.93, max(0.07, 0.5 + (32.9 - line) * 0.045)), 4)
        doc["fc"][fid] = forecast(po, round(1 - po, 4), 32.9, line=line, iv=[24.0, 41.0])
        kw = {"mult": [1.0, 1.0]} if book == "underdog" else {} if book == "prizepicks" else {"decimal": [round(1.6 + i * 0.04, 2), round(2.3 - i * 0.04, 2)]}
        if book == "polymarket":
            kw.update(over=1, under=0, decimal=[round(1.2 + i * 0.1, 2), None])
        qs.append(quote(book, f"{book[:2]}:sga{i}", line, fid, **kw))
    sga = market("nba", "nba:espn:1", "nba:player:espn:399", "Shai Ladder", "G", "BOS", "nba:team:espn:BOS", "points", qs)
    reb = market("nba", "nba:espn:1", "nba:player:espn:399", "Shai Ladder", "G", "BOS", "nba:team:espn:BOS", "rebounds", [
        quote("underdog", "ud:sgareb", 5.5, "fc-dddddddd000000f0", mult=[1.0, 1.0]), quote("prizepicks", "pp:sgareb", 5.5, "fc-dddddddd000000f1")])
    doc["fc"]["fc-dddddddd000000f0"] = forecast(0.57, 0.43, 6.1, line=5.5)
    doc["fc"]["fc-dddddddd000000f1"] = forecast(0.57, 0.43, 6.1, line=5.5)
    doc["markets"] += [sga, reb]
    view["rows"][sga["k"]] = {"lines": {str(ln): [6, 4, 0, 10, 3, 2, 0, 5] for _, ln in LADDER}, "played": 14, "seasons10": [2027], "from10": iso(soon(3)), "to10": iso(soon(3)), "sd10": 5.0}
    return doc, view


def big_ladder_board(players=150, stats=("points", "rebounds", "assists", "threes"), lines=10):
    """A slate the size of the live one: players x stats x lines offers (150 x 4 x 10 = 6,000), spread over six apps."""
    s = soon(3)
    books = ("underdog", "prizepicks", "fanduel", "betrivers", "bovada", "polymarket")
    events, markets, fc, rows = {}, [], {}, {}
    n = 0
    for i in range(players):
        g = i % 12
        eid = f"nba:espn:g{g}"
        events[eid] = event(f"H{g:02d}", f"A{g:02d}", s + timedelta(minutes=20 * g))
        team = f"H{g:02d}" if i % 2 else f"A{g:02d}"
        for st in stats:
            base = {"points": 20, "rebounds": 6, "assists": 5, "threes": 2}[st] + i % 9
            qs = []
            for j in range(lines):
                book, line = books[j % len(books)], base + j - lines / 2 + 0.5
                fid = f"fc-eeeeeeee{n:08x}"
                n += 1
                po = round(min(0.9, max(0.1, 0.5 + (base - line) * 0.04)), 4)
                fc[fid] = forecast(po, round(1 - po, 4), float(base), line=line, iv=[base - 8.0, base + 8.0])
                kw = {"mult": [1.0, 1.0]} if book == "underdog" else {} if book == "prizepicks" else {"decimal": [1.87, 1.95]}
                qs.append(quote(book, f"{book[:2]}:{i}:{st}:{j}", line, fid, **kw))
            m = market("nba", eid, f"nba:player:espn:p{i}", f"Player {i:03d}", "G", team, f"nba:team:espn:{team}", st, qs)
            markets.append(m)
            rows[m["k"]] = {"lines": {str(q["l"]): [5, 5, 0, 10, 3, 2, 0, 5] for q in qs}, "played": 12, "seasons10": [2027], "from10": iso(s), "to10": iso(s), "sd10": 4.0}
    doc = {"version": "ui-snap-1", "league": "nba", "built_at": iso(s - timedelta(hours=3)), "as_of": iso(s - timedelta(hours=3)), "part": "primary", "secondary_markets": 0,
           "stats": {"points": "Points", "rebounds": "Rebounds", "assists": "Assists", "threes": "3-pointers"}, "models": [], "events": events, "injuries": {}, "markets": markets, "fc": fc}
    return doc, {"version": "ui-snap-1", "league": "nba", "part": "view", "rule": "bv-1", "rows": rows}


def nxt(browser, env, size=DESK, theme=None, board=None):
    if board:
        env.sports.boards["nba"] = board
        workspace_api.reset()
    c = context(browser, env, size)
    c.add_cookies([{"name": server.UI_NEXT_COOKIE, "value": "next", "url": env.url}])
    if theme:
        c.add_init_script("try{localStorage.setItem('scout-theme','%s')}catch(e){}" % theme)
    return c


def nba(c, env, size):
    p = open_page(c, env, "/sports", size)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
    return p


def row_of(p, name, stat):
    return p.locator("#hub-rows tr.h-row").filter(has_text=name).filter(has=p.locator(".h-mcell b", has_text=re.compile(f"^{stat}$")))


def records(p):
    return p.evaluate("fetch('/api/upgrade/board?sport=nba',{credentials:'same-origin'}).then(r=>r.json()).then(d=>d.records)")


# ---------------------------------------------------------------------------------------------------------------- the switch

def test_preview_route_turns_the_redesign_on_for_one_browser_and_one_flag_makes_it_the_default(browser, env, monkeypatch):
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/#all-sports", DESK)
        p.wait_for_selector("#hub-rows tr[data-record]")
        sheets = lambda: p.evaluate("[...document.querySelectorAll('link[rel=stylesheet]')].map(l=>new URL(l.href).pathname)")   # noqa: E731
        assert p.locator("html").get_attribute("data-ui") is None and "/upgrade/hub.css" in sheets() and "/upgrade/next.css" not in sheets()
        assert p.locator("[data-hub=ladder],[data-hub=theme],[data-hub=group-by]").count() == 0          # the current design is untouched
        p.goto(env.url + "/preview/next")                                                               # member-gated, sets the cookie, lands on the board
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.url.endswith("/#all-sports") and p.locator("html").get_attribute("data-ui") == "next"
        assert "/upgrade/next.css" in sheets() and "/upgrade/next-base.css" in sheets() and "/upgrade/hub.css" not in sheets()
        assert p.locator(".h-preview-off").first.get_attribute("href") == "/preview/next?off=1"
        p.goto(env.url + "/preview/next?off=1")
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") is None and "/upgrade/hub.css" in sheets()
        assert not p.errors
    finally:
        c.close()
    # signed out: the preview path is the unknown-route answer, like the board's own files
    anon = browser.new_context()
    try:
        r = anon.request.get(env.url + "/preview/next", max_redirects=0)
        assert r.status == 404
    finally:
        anon.close()
    # UI_NEXT=1: the redesign is the default for everyone; a member's "current" cookie still opts out
    monkeypatch.setenv("UI_NEXT", "1")
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/#all-sports", DESK)
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") == "next" and p.locator(".h-preview-off").count() == 0
        c.add_cookies([{"name": server.UI_NEXT_COOKIE, "value": "current", "url": env.url}])
        p.reload()
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") is None
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- many lines

@pytest.mark.parametrize("size", [DESK, PHONE], ids=["desktop", "phone"])
def test_one_row_per_prop_and_a_ladder_of_every_line(browser, env, size, tmp_path):
    c = nxt(browser, env, size, board=ladder_board())
    try:
        p = nba(c, env, size)
        recs = [r for r in records(p) if r["player"]["name"] == "Shai Ladder" and r["market"]["stat"] == "points"]
        assert len(recs) == 15
        row = row_of(p, "Shai Ladder", "Points")
        assert row.count() == 1                                                    # 15 lines, one row
        assert "15 lines" in p.locator("#hub-count").inner_text().lower() or "lines" in p.locator("#hub-count").inner_text().lower()
        btn = row.locator("[data-hub=ladder]")
        assert btn.inner_text().split("\n")[0].strip() == "+14 lines" and "6 apps" in btn.inner_text() and "24.5–39.5" in btn.inner_text()
        assert btn.get_attribute("aria-expanded") == "false" and p.locator(".h-ladder").count() == 0     # the ladder is not built until asked for
        btn.click()
        lad = p.locator("tr.h-ladder-row .h-ladder-table tbody tr")
        assert lad.count() == 15 and row.locator("[data-hub=ladder]").get_attribute("aria-expanded") == "true"
        lines = [float(x) for x in lad.locator(".h-ld-line b").all_inner_texts()]
        assert lines == sorted(lines)                                              # by line, low to high
        # every ladder line shows that record's own side, chance and letter - the numbers the board API sent
        by_id = {f"nba|{r['offer']['platform']}|{r['offer']['offer_id']}": r for r in recs}
        for tr in lad.all():
            r = by_id[tr.get_attribute("data-line")]
            text = tr.inner_text()
            assert r["offer"]["platform_label"] in text and str(r["offer"]["line"]).rstrip("0").rstrip(".") in text
            if r["scout"]["side"]:
                assert ("Over" if r["scout"]["side"] == "over" else "Under").upper() in text.upper() and f"{r['scout']['p_side'] * 100:.1f}%" in text
            if r["scout"].get("grade") and not r["scout"].get("hold") and r["scout"]["side"]:
                assert r["scout"]["grade"] in tr.locator(".h-ld-grade").inner_text() or "Lean" in tr.locator(".h-ld-grade").inner_text()
        # the row's own line is marked in the ladder, and the best line for its Scout side is named
        main = p.locator("tr.h-ladder-row tr.is-main")
        assert main.count() == 1 and main.get_attribute("data-line") == row.get_attribute("data-record")
        side = by_id[row.get_attribute("data-record")]["scout"]["side"]
        best = p.locator("tr.h-ladder-row .h-ld-best")
        assert best.count() == 1 and best.inner_text().lower() == f"best {side}"
        offered = [r for r in recs if r["offer"][side]["offered"]]
        want = min(offered, key=lambda r: r["offer"]["line"]) if side == "over" else max(offered, key=lambda r: r["offer"]["line"])
        assert float(best.locator("xpath=..").locator("b").inner_text()) == want["offer"]["line"]
        # a slip line adds from the ladder; a sportsbook line opens its card instead
        slip_line = p.locator("tr.h-ladder-row tr", has_text="Underdog").locator("[data-hub=add]")
        slip_line.click()
        p.wait_for_function("document.getElementById('hub-tab-slip-n').textContent==='1'")
        assert p.locator("tr.h-ladder-row .h-ladder-table tbody tr").count() == 15      # the ladder stayed open through the repaint
        assert p.locator("tr.h-ladder-row tr", has_text="Bovada").first.locator(".h-ld-act").inner_text() == "Card"
        no_h_scroll(p)
        if size is PHONE:                                                          # thumb targets
            for b in p.locator("#hub-rows tr.h-row").first.locator("button:visible").all() + p.locator("tr.h-ladder-row button:visible").all()[:6]:
                box = b.bounding_box()
                assert box["height"] >= 43.5, (b.get_attribute("class"), box)
        row.locator("[data-hub=ladder]").click()
        assert p.locator(".h-ladder").count() == 0
        assert not p.errors
    finally:
        c.close()


def test_filters_and_search_never_hide_a_matched_line_in_a_grouped_row(browser, env):
    c = nxt(browser, env, DESK, board=ladder_board())
    try:
        p = nba(c, env, DESK)
        p.locator("#hub-platform [data-id=prizepicks]").click()
        row = row_of(p, "Shai Ladder", "Points")
        assert row.count() == 1 and "PP" in row.locator(".h-c-line").inner_text()                     # the row leads with the matched line
        btn = row.locator("[data-hub=ladder]")
        assert "1 of 15 match" in btn.inner_text() and "1 of 15 match" in btn.get_attribute("aria-label")
        btn.click()
        lad = p.locator("tr.h-ladder-row .h-ladder-table tbody tr")
        assert lad.count() == 15 and p.locator("tr.h-ladder-row tr.is-match").count() == 1 and p.locator("tr.h-ladder-row tr.is-dim").count() == 14
        assert "1 of 15 lines match" in p.locator(".h-ladder-note").inner_text()
        p.locator("#hub-platform [data-id=all]").click()
        assert p.locator("tr.h-ladder-row tr.is-dim").count() == 0 and "match" not in row_of(p, "Shai Ladder", "Points").locator("[data-hub=ladder]").inner_text()
        # Side = Under keeps only the lines Scout leans Under on; the count says how many
        p.locator("#hub-filters-toggle").click()
        p.locator("#hub-side").select_option("under")
        p.keyboard.press("Escape")
        unders = sum(1 for r in records(p) if r["player"]["name"] == "Shai Ladder" and r["market"]["stat"] == "points" and r["scout"]["side"] == "under")
        assert 0 < unders < 15
        assert f"{unders} of 15 match" in row_of(p, "Shai Ladder", "Points").locator("[data-hub=ladder]").inner_text()
        assert p.locator("tr.h-ladder-row tr.is-match").count() == unders
        p.locator("#hub-chips [data-id=side]").click()
        # sorting by chance: the row leads with its highest-chance line
        p.locator("#hub-sort").select_option("chance")
        top = row_of(p, "Shai Ladder", "Points")
        best = max((r for r in records(p) if r["player"]["name"] == "Shai Ladder" and r["market"]["stat"] == "points" and r["scout"]["side"]), key=lambda r: r["scout"]["p_side"])
        assert f"{best['scout']['p_side'] * 100:.1f}%" in top.locator(".h-c-scout").inner_text()
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["desktop", "phone"])
def test_search_jumps_to_the_player_with_props_grouped_by_stat(browser, env, size):
    c = nxt(browser, env, size, board=ladder_board())
    try:
        p = nba(c, env, size)
        assert p.locator("tr.h-player-row").count() == 0                                              # by prop until asked
        p.locator("#hub-q").fill("shai")
        p.wait_for_selector("tr.h-player-row")
        head = p.locator("tr.h-player-row")
        assert head.count() == 1 and "Shai Ladder" in head.inner_text() and "2 props" in head.inner_text().lower() and "17 lines" in head.inner_text().lower()
        stats = p.locator("#hub-rows tr.h-row .h-mcell b").all_inner_texts()
        assert stats == ["Points", "Rebounds"]
        assert p.locator("[data-hub=group-by][data-id=player]").get_attribute("aria-pressed") == "true"
        p.locator("#hub-q").fill("")
        p.wait_for_function("document.querySelectorAll('tr.h-player-row').length===0")
        # the member's own choice: By player, kept on this device
        p.locator("[data-hub=group-by][data-id=player]").click()
        assert p.locator("tr.h-player-row").count() >= 4
        assert p.evaluate("localStorage.getItem('scout-board-group')") == "player"
        if size is DESK:                                                                                # the player header pins while his props scroll
            assert p.locator("tr.h-player-row>td").first.evaluate("e=>getComputedStyle(e).position") == "sticky"
        p.reload()
        p.wait_for_selector("tr.h-player-row")
        p.locator("[data-hub=group-by][data-id=prop]").click()
        assert p.locator("tr.h-player-row").count() == 0
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_rows_by_keyboard_and_no_shift_on_refresh(browser, env):
    c = nxt(browser, env, DESK, board=ladder_board())
    try:
        p = nba(c, env, DESK)
        row = row_of(p, "Shai Ladder", "Points")
        row.locator(".h-player").focus()
        p.keyboard.press("ArrowRight")                                                                 # opens the ladder, focus stays on the row
        assert p.locator(".h-ladder").count() == 1
        assert p.evaluate("document.activeElement.closest('tr').dataset.prop") == row.get_attribute("data-prop")
        before = [row_of(p, "Shai Ladder", "Points").bounding_box(), p.locator("tr.h-ladder-row").bounding_box(), p.locator("#hub-rows tr.h-row").last.bounding_box()]
        p.locator(".h-refresh").first.click()                                                          # a refresh repaints the board
        p.wait_for_function("document.querySelector('.h-refresh').getAttribute('aria-busy')!=='true'")
        p.wait_for_timeout(300)
        after = [row_of(p, "Shai Ladder", "Points").bounding_box(), p.locator("tr.h-ladder-row").bounding_box(), p.locator("#hub-rows tr.h-row").last.bounding_box()]
        assert before == after                                                                         # nothing moved: the ladder is still open, same place
        row_of(p, "Shai Ladder", "Points").locator(".h-player").focus()
        p.keyboard.press("ArrowLeft")
        assert p.locator(".h-ladder").count() == 0
        first = p.locator("#hub-rows tr.h-row").first
        first.locator(".h-player").focus()
        p.keyboard.press("ArrowDown")
        assert p.evaluate("[...document.querySelectorAll('#hub-rows tr.h-row')].indexOf(document.activeElement.closest('tr'))") == 1
        p.keyboard.press("ArrowUp")
        assert p.evaluate("[...document.querySelectorAll('#hub-rows tr.h-row')].indexOf(document.activeElement.closest('tr'))") == 0
        ring = p.evaluate("getComputedStyle(document.activeElement).outlineStyle")
        assert ring == "solid"                                                                         # a visible focus ring
        p.keyboard.press("Enter")                                                                      # Enter opens the card
        p.wait_for_selector("#hub-card:not([hidden]) #hub-card-title")
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- the card

def test_the_card_sits_beside_the_board_and_shows_what_is_behind_the_grade(browser, env):
    c = nxt(browser, env, DESK, board=ladder_board())
    try:
        p = nba(c, env, DESK)
        row = row_of(p, "Reed Lean", "Assists")
        row.locator(".h-player").click()
        p.wait_for_selector("#hub-card .h-gi")
        card, center = p.locator("#hub-card").bounding_box(), p.locator("#hub-center").bounding_box()
        assert center["x"] + center["width"] <= card["x"] + 1                                          # beside the board, not over it
        assert card["x"] + card["width"] <= 1440 + 1 and not p.locator("#u-hub > .h-slip").is_visible()
        rec = next(r for r in records(p) if r["player"]["name"] == "Reed Lean" and r["offer"]["platform"] == row.get_attribute("data-record").split("|")[1])
        gi = p.locator("#hub-card .h-gi").inner_text()
        assert "Behind the grade" in gi or "BEHIND THE GRADE" in gi.upper()
        assert f"{rec['scout']['projection']:g}" in gi and f"{rec['scout']['p_side'] * 100:.1f}%" in gi and "sm-1" in gi and rec["scout"]["grade"] in gi
        assert f"{rec['scout']['p_side'] * 100:.1f}%" in p.locator("#hub-decision").inner_text()       # the card's headline agrees with the board
        assert f"{rec['scout']['p_side'] * 100:.1f}%" in row.locator(".h-c-scout").inner_text()
        # the board stays in reach: the next player is one click
        other = row_of(p, "Karl Anders", "Points")
        other.locator(".h-player").click()
        p.wait_for_function("document.getElementById('hub-card-title').textContent.includes('Karl Anders')")
        assert "is-expanded" in other.get_attribute("class")
        p.keyboard.press("Escape")
        p.wait_for_selector("#hub-card", state="hidden")
        assert p.locator("#u-hub > .h-slip").is_visible()
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_the_phone_card_is_a_bottom_sheet_with_thumb_targets(browser, env):
    c = nxt(browser, env, PHONE, board=ladder_board())
    try:
        p = nba(c, env, PHONE)
        row_of(p, "Reed Lean", "Assists").locator(".h-player").click()
        p.wait_for_selector("#hub-card .h-gi")
        box = p.locator("#hub-card").bounding_box()
        assert box["x"] == 0 and box["width"] == 390 and 20 < box["y"] < 120 and abs(box["y"] + box["height"] - 844) < 2      # from the bottom, the board peeks above
        for b in p.locator("#hub-card :is(.h-card-tabs button, .h-back, .h-dec-add, #hub-card-line, .h-side-toggle button)").all():
            assert b.bounding_box()["height"] >= 43.5
        gi = p.locator("#hub-card .h-gi")
        gi.scroll_into_view_if_needed()
        assert gi.bounding_box()["width"] <= 390
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- themes

CONTRAST = """()=>{
const lum=c=>{const [r,g,b]=c.match(/[\\d.]+/g).slice(0,3).map(v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)});return .2126*r+.7152*g+.0722*b};
const probe=document.createElement('span');document.getElementById('u-hub').appendChild(probe);
const rgb=v=>{probe.style.color=`var(${v})`;return getComputedStyle(probe).color};
const ratio=(a,b)=>{const x=lum(rgb(a)),y=lum(rgb(b));return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
const pairs=[['--n-t1','--n-bg'],['--n-t1','--n-s1'],['--n-t2','--n-s1'],['--n-t3','--n-s1'],['--n-t3','--n-s2'],['--n-t3','--n-bg'],['--n-t4','--n-s1'],['--n-acc-t','--n-s1'],['--n-acc-on','--n-acc'],
 ['--n-over-t','--n-s1'],['--n-under-t','--n-s1'],['--n-warn-t','--n-s1'],['--n-alt-t','--n-s1'],['--n-over-on','--n-over'],['--n-under-on','--n-under'],['--n-bg','--n-t1'],['--n-t1','--n-acc-bg'],['--n-t1','--n-s3']];
const out=pairs.map(([a,b])=>[a,b,Math.round(ratio(a,b)*100)/100]);probe.remove();return out}"""


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_both_themes_meet_aa_contrast_and_the_switch_is_kept(browser, env, theme):
    c = nxt(browser, env, DESK, theme=theme, board=ladder_board())
    try:
        p = nba(c, env, DESK)
        assert p.locator("html").get_attribute("data-theme") == theme
        for a, b, ratio in p.evaluate(CONTRAST):
            assert ratio >= 4.5, (theme, a, b, ratio)
        bg = p.locator("#u-hub").evaluate("e=>getComputedStyle(e).backgroundColor")
        light = sum(int(x) for x in re.findall(r"\d+", bg)[:3]) > 600
        assert light == (theme == "light")
        btn = p.locator(".h-sidebar-foot [data-hub=theme]")
        btn.click()
        other = "light" if theme == "dark" else "dark"
        assert p.locator("html").get_attribute("data-theme") == other and p.evaluate("localStorage.getItem('scout-theme')") == other
        # no decoration left over from the old look: no blur, no drop shadow, no gradient, no rounded pill on the board or its controls
        bad = p.evaluate("""()=>[...document.querySelectorAll('#u-hub *')].filter(e=>e.offsetParent).filter(e=>{const s=getComputedStyle(e);
            return s.backdropFilter!=='none'||/gradient/.test(s.backgroundImage)||(s.boxShadow!=='none'&&!/inset/.test(s.boxShadow)&&/\\dpx \\dpx [1-9]/.test(s.boxShadow))||(parseFloat(s.borderTopLeftRadius)>4&&!s.borderTopLeftRadius.includes('%'))}).map(e=>e.className+'|'+e.tagName).slice(0,8)""")
        assert bad == []
        assert not p.errors
    finally:
        c.close()


# ---------------------------------------------------------------------------------------------------------------- 6,000 lines

PAINT = """async()=>{const q=document.getElementById('hub-q'),out=[];
for(let i=0;i<7;i++){const t=performance.now();q.value=i%2?'':'player 0';q.dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('hub-rows').offsetHeight;out.push(performance.now()-t);await new Promise(r=>setTimeout(r,30));}
q.value='';q.dispatchEvent(new Event('input',{bubbles:true}));
out.sort((a,b)=>a-b);return {median:out[3],max:out[6],rows:document.querySelectorAll('#hub-rows tr').length,nodes:document.querySelectorAll('#hub-rows *').length}}"""


def test_a_6000_line_board_paints_fast_and_ladders_are_built_only_when_opened(browser, env, tmp_path):
    board = big_ladder_board()
    out = {}
    env.sports.boards["nba"] = board
    workspace_api.reset()
    for tag in ("current", "next"):
        c = context(browser, env, DESK)
        if tag == "next":
            c.add_cookies([{"name": server.UI_NEXT_COOKIE, "value": "next", "url": env.url}])
        try:
            p = nba(c, env, DESK)
            p.wait_for_function("document.getElementById('hub-count').textContent.includes('6,000')", timeout=30000)
            out[tag] = p.evaluate(PAINT)
            if tag == "next":
                assert "150 players · 600 props · 6,000 lines" == p.locator("#hub-count").inner_text().lower().replace("lines", "lines")
                assert p.locator("#hub-rows tr.h-row").count() == 50 and p.locator(".h-ladder").count() == 0     # 50 rows a page, no ladder built
                t = p.evaluate("""()=>{const t=performance.now();document.querySelector('#hub-rows [data-hub=ladder]').click();document.getElementById('hub-rows').offsetHeight;return performance.now()-t}""")
                out["ladder_open_ms"] = t
                assert p.locator("tr.h-ladder-row .h-ladder-table tbody tr").count() == 10
            assert not p.errors
        finally:
            c.close()
    (tmp_path / "paint.json").write_text(json.dumps(out), encoding="utf-8")
    print("PAINT6000", json.dumps(out))
    assert out["next"]["median"] <= max(250.0, out["current"]["median"] * 2), out          # generous: CI machines vary
    assert out["ladder_open_ms"] <= max(300.0, out["next"]["median"] * 3), out
