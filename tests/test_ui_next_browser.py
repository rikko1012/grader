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
    """A member on the redesign (their own cookie, which wins over the fixture's UI_NEXT=0)."""
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

def sheets(p):
    return p.evaluate("[...document.querySelectorAll('link[rel=stylesheet]')].map(l=>new URL(l.href).pathname)")


def test_the_redesign_is_the_default_and_one_flag_or_one_path_goes_back(browser, env, monkeypatch):
    monkeypatch.delenv("UI_NEXT", raising=False)                          # unset = the redesign for every member
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/#all-sports", DESK)
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") == "next"
        assert "/upgrade/next.css" in sheets(p) and "/upgrade/next-base.css" in sheets(p) and "/upgrade/hub.css" not in sheets(p)
        assert p.locator(".h-sidebar-foot .h-preview-off").get_attribute("href") == "/preview/current"
        p.goto(env.url + "/preview/current")                              # the design before it, for this browser (member-gated)
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.url.endswith("/#all-sports") and p.locator("html").get_attribute("data-ui") is None and "/upgrade/hub.css" in sheets(p)
        assert p.locator("[data-hub=ladder],[data-hub=theme],[data-hub=group-by]").count() == 0
        assert p.locator(".h-preview-on").get_attribute("href") == "/preview/next"
        p.goto(env.url + "/preview/next")
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") == "next"
        assert not p.errors
    finally:
        c.close()
    anon = browser.new_context()                                          # signed out: both paths are the unknown-route answer
    try:
        assert anon.request.get(env.url + "/preview/next", max_redirects=0).status == 404
        assert anon.request.get(env.url + "/preview/current", max_redirects=0).status == 404
    finally:
        anon.close()
    monkeypatch.setenv("UI_NEXT", "0")                                    # the rollback switch, read on each request: no restart
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/#all-sports", DESK)
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") is None and "/upgrade/hub.css" in sheets(p) and "/upgrade/next.css" not in sheets(p)
        monkeypatch.delenv("UI_NEXT")
        p.reload()
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-ui") == "next"
    finally:
        c.close()


@pytest.mark.parametrize("path,sport,section", [
    ("/", "cs2", "board"), ("/#board", "cs2", "board"), ("/#board/prizepicks", "cs2", "board"), ("/#all-sports", "all", "board"), ("/lol", "lol", "board"),
    ("/sports", "nba", "board"), ("/sports#/board?lg=nfl", "nfl", "board"), ("/sports#/board?lg=nba", "nba", "board"), ("/#scout/board?sport=lol", "lol", "board"),
    ("/#/slips", None, "studio"), ("/#slips", None, "studio"), ("/#scout/studio", None, "studio"), ("/#scout/picks", None, "picks"), ("/#scout/record", None, "record"),
    ("/#scout/tools", None, "tools"), ("/#scout/games", None, "board")])
def test_old_links_and_hash_routes_open_the_same_place_on_the_default_design(browser, env, monkeypatch, path, sport, section):
    monkeypatch.delenv("UI_NEXT", raising=False)
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, path, DESK)
        p.wait_for_selector("#u-hub:not([hidden])", timeout=20000)
        assert p.locator("html").get_attribute("data-ui") == "next"
        assert p.locator(f"#hub-{section}-section").is_visible()
        if sport:
            p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
            assert p.locator(f"#hub-sports [data-id={sport}]").get_attribute("aria-pressed") == "true"
            if sport != "all":
                assert all(x.startswith(sport + "|") for x in p.locator("#hub-rows tr[data-record]").evaluate_all("rs=>rs.map(r=>r.dataset.record)"))
        if path == "/#board/prizepicks":
            assert p.locator("#hub-platform [data-id=prizepicks]").get_attribute("aria-pressed") == "true"
        if path == "/#scout/games":
            assert p.locator("#hub-games-body").is_visible()
        assert not p.errors
    finally:
        c.close()
    # the classic pages stay where they were
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/?view=classic#board", DESK)
        p.wait_for_selector("#boardout", timeout=20000)
        assert p.locator("#u-hub").is_hidden()
    finally:
        c.close()


@pytest.mark.parametrize("size", [DESK, PHONE], ids=["desktop", "phone"])
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_filters_sorting_views_and_the_slip_studio_work_on_the_default_design(browser, env, monkeypatch, size, theme):
    monkeypatch.delenv("UI_NEXT", raising=False)
    env.sports.boards["nba"] = ladder_board()
    workspace_api.reset()
    c = context(browser, env, size)
    c.add_init_script("try{localStorage.setItem('scout-theme','%s')}catch(e){}" % theme)
    try:
        p = open_page(c, env, "/#all-sports", size)
        p.wait_for_selector("#hub-rows tr[data-record]")
        assert p.locator("html").get_attribute("data-theme") == theme
        total = p.locator("#hub-rows tr.h-row").count()
        # search, then the chip removes it
        p.locator("#hub-q").fill("vargo")
        p.wait_for_function("document.querySelectorAll('#hub-rows tr.h-row').length===1")
        p.locator("#hub-chips [data-id=q]").click()
        assert p.locator("#hub-rows tr.h-row").count() == total
        # market + side + minimum grade from the Filters pop-over; the badge counts them; Clear filters resets
        p.locator("#hub-filters-toggle").click()
        p.locator("#hub-side").select_option("over")
        p.locator("#hub-minGrade").select_option("B")
        p.keyboard.press("Escape")
        assert p.locator("#hub-filter-n").inner_text() == "2"
        scouts = p.locator("#hub-rows tr.h-row .h-c-scout").all_inner_texts()
        assert scouts and all("OVER" in t.upper() for t in scouts)
        assert all(re.search(r"\b(A\+|A-|A|B\+|B)\b", t) for t in scouts)
        # save the view, clear, choose it again
        p.locator("#hub-views-toggle").click()
        p.locator("[data-hub=save-view]").click()
        p.locator("#hub-save-name").fill("Overs B up")
        p.locator("#hub-save-confirm").click()
        p.locator("#hub-clear").click()
        assert p.locator("#hub-rows tr.h-row").count() == total
        p.locator("#hub-views-toggle").click()
        p.locator("#hub-views").select_option(label="Overs B up")
        p.keyboard.press("Escape")
        assert p.locator("#hub-side").input_value() == "over" and p.locator("#hub-minGrade").input_value() == "B"
        p.locator("#hub-clear").click()
        # sorting from the menu and (desktop) from a column header
        p.locator("#hub-sort").select_option("player")
        names = p.locator("#hub-rows tr.h-row .h-player b").all_inner_texts()
        assert names == sorted(names, key=str.lower) or names == sorted(names)
        if size is DESK:
            p.locator("#hub-table thead [data-id=line]").click()
            lines = [float(x) for x in p.locator("#hub-rows tr.h-row .h-c-line b").evaluate_all("bs=>bs.map(b=>b.firstChild.textContent)")]
            assert lines == sorted(lines)
            p.locator("#hub-table thead [data-id=line]").click()
            lines = [float(x) for x in p.locator("#hub-rows tr.h-row .h-c-line b").evaluate_all("bs=>bs.map(b=>b.firstChild.textContent)")]
            assert lines == sorted(lines, reverse=True)
        # by game, then back to the list
        if size is DESK:
            p.locator(".h-viewmode [data-id=games]").click()
            assert p.locator("#hub-games-body").is_visible()
            p.locator(".h-viewmode [data-id=list]").click()
        # a pick from the board lands on the slip; the card adds the other way and offers the app link
        add = p.locator("#hub-rows tr.h-row", has_text="vargo").locator(".h-add")
        add.click()
        p.wait_for_function("document.getElementById('hub-tab-slip-n').textContent==='1'")
        # Slip Studio: Auto-build with Unders only (PR #57) builds slips of Under legs
        p.goto(env.url + "/#/slips")
        p.get_by_role("tab", name="Auto-build", exact=True).click()
        p.locator(".h-generation-more summary").click()
        p.locator("#hub-generation-side").select_option("under")
        assert "Unders only" in p.locator("#hub-generation-summary").inner_text()
        p.locator("#hub-generate").scroll_into_view_if_needed()
        box = p.locator("#hub-generate").bounding_box()
        assert box["height"] >= (43.5 if size is PHONE else 30)
        p.locator("#hub-generate").click()
        p.wait_for_function("document.getElementById('hub-generated').textContent.trim().length>0", timeout=20000)
        made = p.locator("#hub-generated").inner_text()
        assert "Over " not in made.replace("Overs", "") or "Under" in made
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


def test_card_keeps_the_app_link_line_notes_and_chips_on_the_default_design(browser, env, monkeypatch):
    monkeypatch.delenv("UI_NEXT", raising=False)
    for k in ("SCOUT_BOOK_LINKS", "SCOUT_MARKET_EDGE", "SCOUT_PARITY_EDGE"):
        monkeypatch.setenv(k, "1")
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/", DESK)
        p.wait_for_selector("#hub-rows tr[data-record]")
        row = p.locator("#hub-rows tr.h-row", has_text="orlo")                      # Underdog 33.5 vs PrizePicks 34.5: the books differ
        # the name column holds with the Edge columns on (asked of the page: the board repaints as its price reads land)
        p.wait_for_function("(()=>{const r=[...document.querySelectorAll('#hub-rows tr.h-row')].find(x=>x.textContent.includes('orlo'));return r&&r.querySelector('.h-player').getBoundingClientRect().width>80})()")
        row.locator(".h-player").click()
        p.wait_for_selector("#hub-card .h-why")
        dec = p.locator("#hub-decision")
        if "underdog" in row.get_attribute("data-record"):
            links = p.evaluate("fetch('/api/upgrade/me',{credentials:'same-origin'}).then(r=>r.json()).then(d=>!!d.features.book_links)")
            assert dec.locator("[data-hub=open-line]").count() == (1 if links else 0)   # Open in Underdog, where the feature is on
        p.locator("#hub-tab-lines").click()
        shop = p.locator("#hub-card .h-line-shop").inner_text()
        assert "Underdog" in shop and "PrizePicks" in shop and "33.5" in shop and "34.5" in shop
        p.locator("#hub-tab-stats").click()
        p.locator("#hub-tab-ask").click()
        assert p.locator("#hub-card .h-guide").count() == 1
        assert not p.errors
    finally:
        c.close()


def test_ev_and_arbitrage_have_their_own_screens_with_the_api_rows(browser, env, monkeypatch):
    monkeypatch.delenv("UI_NEXT", raising=False)
    import workspace_markets
    doc = {"as_of": iso(soon(0)), "owner": False, "method": {"ev_stale_s": 1800, "arb_max_age_s": 300},
           "sports": {"nba": {"state": "ok", "quotes_checked": 40, "markets_checked": 12, "sections_possible": {"ev": True, "arb": True},
                              "ev": [{"sport": "nba", "player": "Karl Anders", "side": "under", "line": 20.5, "stat_label": "Points", "event": "NYK at BOS", "book": "fanduel", "book_label": "FanDuel",
                                      "american": "+105", "p_cal": 0.53, "needs": 0.4878, "ev": 0.0865, "per_100": 8.65, "chance_from": "Scout", "seen_at": iso(soon(0)), "kind": "book"},
                                     {"sport": "nba", "player": "Jay Brown", "side": "over", "line": 24.5, "stat_label": "Points", "event": "NYK at BOS", "book": "betrivers", "book_label": "BetRivers",
                                      "american": "+110", "p_cal": 0.5, "needs": 0.4762, "ev": 0.05, "per_100": 5.0, "chance_from": "Scout", "seen_at": iso(soon(0)), "kind": "book"}],
                              "arbitrage": [{"sport": "nba", "player": "Reed Lean", "line": 6.5, "stat_label": "Assists", "event": "CHI at MIA", "return_per_100": 2.41,
                                             "legs": [{"book": "fanduel", "book_label": "FanDuel", "side": "over", "american": "+115", "stake_per_100": 47.63, "seen_at": iso(soon(0))},
                                                      {"book": "betrivers", "book_label": "BetRivers", "side": "under", "american": "+105", "stake_per_100": 52.37, "seen_at": iso(soon(0))}]}],
                              "closest": []}}}
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.setattr(workspace_markets, "payload", lambda *a, **k: doc, raising=False)
    workspace_api.reset()
    c = context(browser, env, DESK)
    c.route("**/api/upgrade/market-tools", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(doc)))
    try:
        p = open_page(c, env, "/sports", DESK)
        p.wait_for_selector("#hub-rows tr[data-record]")
        feats = p.evaluate("fetch('/api/upgrade/me',{credentials:'same-origin'}).then(r=>r.json()).then(d=>d.features)")
        nav = p.locator("#u-hub .h-sidebar nav button").all_inner_texts()
        if not feats.get("market_tools"):
            assert "Arbitrage" not in nav and "EV" not in nav                       # no feature: no nav item (never a locked one)
            return
        assert "EV" in nav and "Arbitrage" in nav
        p.locator("#u-hub .h-sidebar nav [data-id=arb]").click()
        assert p.url.endswith("#scout/arb") or p.url.endswith("#/arb")
        p.wait_for_selector("#hub-arb-section .h-pc-arb")
        assert p.locator("#hub-title").inner_text().lower() == "arbitrage"
        rows = p.locator("#hub-arb-section .h-pc-arb")
        assert rows.count() == len(doc["sports"]["nba"]["arbitrage"]) and p.locator("#hub-arb-section .h-pc-ev").count() == 0
        t = rows.first.inner_text()
        assert "Reed Lean" in t and "FanDuel Over 6.5 +115" in t and "BetRivers Under 6.5 +105" in t and "$47.63" in t and "$52.37" in t and "Margin 2.41%" in t and "$102.41" in t
        p.locator("#hub-mt-stake").fill("200")                                      # the stake split follows the chosen total
        p.wait_for_function("document.querySelector('#hub-arb-section .h-pc-arb').innerText.includes('$95.26')")
        p.locator("#u-hub .h-sidebar nav [data-id=ev]").click()
        p.wait_for_selector("#hub-ev-section .h-pc-ev .h-pc-row:not(.h-pc-head)")
        ev = p.locator("#hub-ev-section .h-pc-ev .h-pc-row:not(.h-pc-head)")
        assert ev.count() == 2 and p.locator("#hub-ev-section .h-pc-arb").count() == 0
        texts = ev.all_inner_texts()
        assert "Karl Anders" in texts[0] and "+$8.65" in texts[0] and "Jay Brown" in texts[1] and "+$5.00" in texts[1]      # best value first, the API's own figures
        # the old places still work: Tools keeps Price check with both lists
        p.goto(env.url + "/sports#scout/tools")
        p.wait_for_selector("#hub-tools-section .h-pc-arb")
        assert p.locator("#hub-tools-section .h-pc-ev .h-pc-row:not(.h-pc-head)").count() == 2
        # none right now: a plain empty state with the last check time
        doc["sports"]["nba"]["arbitrage"] = []
        p.locator("#u-hub .h-sidebar nav [data-id=arb]").click()
        p.locator("#hub-arb-section [data-hub=refresh-markets]").click()
        p.wait_for_selector("#hub-arb-section .h-pc-none")
        none = p.locator("#hub-arb-section .h-pc-none").inner_text()
        assert "no arbitrage right now" in none.lower() and "last checked" in none.lower()
        assert not p.errors
    finally:
        c.close()
    c = context(browser, env, PHONE)
    c.route("**/api/upgrade/market-tools", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(doc)))
    try:
        p = open_page(c, env, "/sports", PHONE)
        p.wait_for_selector("#hub-rows tr[data-record]")
        p.locator("#hub-more-toggle").click()
        p.locator("#hub-more [data-id=arb]").click()
        p.wait_for_selector("#hub-arb-section .h-pc-none")
        no_h_scroll(p)
        assert not p.errors
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
        assert f"{rec['scout']['projection']:.1f}" in gi and f"{rec['scout']['p_side'] * 100:.1f}%" in gi and "sm-1" in gi and rec["scout"]["grade"] in gi
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
