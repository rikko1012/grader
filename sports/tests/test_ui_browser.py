"""Browser flows for the Scout Sports workspace (Playwright, Chromium). Skipped unless SPORTS_UI_BROWSER_URL points at a
running esports server with SPORTS_UI / SPORTS_API on and a sports service answering its token (the dev preview
configs sports_ui + sports_ui_api). Screenshots go to SPORTS_UI_SHOTS (default: pytest's tmp dir).

    SPORTS_UI_BROWSER_URL=http://localhost:8010 python -m pytest sports/tests/test_ui_browser.py -q

Flow: board -> player -> match context -> slip draft -> Slip Studio, proving the same forecast shows identical numbers
on every surface (and in the API record); states (empty, not-ready, error, offline, stale); 360 px / 768 / 1280 /
200 % zoom without page overflow; keyboard focus; touch targets; labels; contrast; reduced motion.
"""
import json
import os
import re
from pathlib import Path

import pytest

URL = os.environ.get("SPORTS_UI_BROWSER_URL")
pytestmark = pytest.mark.skipif(not URL, reason="SPORTS_UI_BROWSER_URL not set (needs the running dev preview)")


@pytest.fixture(scope="module")
def pw():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def shots(tmp_path):
    d = Path(os.environ.get("SPORTS_UI_SHOTS") or tmp_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _page(pw, width=1280, height=900, view="cards", **kw):
    ctx = pw.new_context(viewport={"width": width, "height": height}, **kw)
    # the board opens as a table (board agent); the card flows below pin the Cards view the member can choose
    ctx.add_init_script("try { if (!localStorage.getItem('scout.sports.board.v1')) localStorage.setItem('scout.sports.board.v1', JSON.stringify({view: %s})) } catch (e) {}"
                        % json.dumps(view))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    return ctx, page, errors


def _numbers(page, fid, scope="body"):
    out = {}
    for el in page.locator(f'{scope} [data-fc="{fid}"]').all():
        if not el.is_visible():
            continue
        side = el.get_attribute("data-fcside")
        out.setdefault(side, set()).add(el.inner_text().strip())
    return out


def _no_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


A11Y_JS = r"""
() => {
  const out = {small: [], unlabelled: [], contrast: [], svgNoLabel: 0};
  const lum = c => { const m = c.match(/\d+(\.\d+)?/g); if (!m) return null; const [r,g,b] = m.slice(0,3).map(Number).map(v => { v/=255; return v <= .03928 ? v/12.92 : Math.pow((v+.055)/1.055, 2.4); }); return .2126*r+.7152*g+.0722*b; };
  const alpha = c => { const m = c.match(/rgba\([^)]*,\s*([\d.]+)\)/); return m ? +m[1] : 1; };
  const bgOf = el => { while (el) { const c = getComputedStyle(el).backgroundColor; if (c && c !== "transparent" && alpha(c) >= 0.5) return c; el = el.parentElement; } return "rgb(9,11,16)"; };   // faint tints (< 50% alpha) read as the panel below
  document.querySelectorAll("a[href],button,input,select,summary").forEach(el => {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height) return;
    const dt = el.closest("details"); if (dt && !dt.open && !el.closest("summary")) return;          // inside a closed section: not shown
    const inline = el.tagName === "A" && getComputedStyle(el).display === "inline";
    const lab = (el.type === "checkbox" || el.type === "radio") && el.closest("label");
    const box = lab ? lab.getBoundingClientRect() : r;
    if (!inline && box.height < 24) out.small.push((el.innerText || el.getAttribute("aria-label") || el.tagName).slice(0, 40) + " " + Math.round(r.height));
    if ((el.tagName === "INPUT" || el.tagName === "SELECT") && !(el.labels && el.labels.length) && !el.getAttribute("aria-label")) out.unlabelled.push(el.id || el.name);
    if (el.tagName === "BUTTON" && !(el.innerText.trim() || el.getAttribute("aria-label"))) out.unlabelled.push("button");
  });
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, seen = 0;
  while ((n = walker.nextNode()) && seen < 4000) {
    if (!n.textContent.trim()) continue; const el = n.parentElement; if (!el || !el.getClientRects().length) continue; seen++;
    const cs = getComputedStyle(el); if (cs.visibility === "hidden" || el.closest("svg") || el.closest(".sw-sr")) continue;
    const fg = lum(cs.color), bg = lum(bgOf(el)); if (fg === null || bg === null) continue;
    const ratio = (Math.max(fg, bg) + .05) / (Math.min(fg, bg) + .05);
    const big = parseFloat(cs.fontSize) >= 18.66 || (parseFloat(cs.fontSize) >= 14 && +cs.fontWeight >= 700);
    if (ratio < (big ? 3 : 4.5) && !/opacity/.test(el.className)) out.contrast.push(el.tagName + "." + el.className + " " + ratio.toFixed(2) + " '" + n.textContent.trim().slice(0, 30) + "'");
  }
  document.querySelectorAll("svg.sw-chart").forEach(s => { if (!s.getAttribute("aria-label")) out.svgNoLabel++; });
  return out;
}
"""


def test_board_player_game_slip_same_numbers(pw, shots):
    ctx, page, errors = _page(pw)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-research-card", timeout=20000)
    page.screenshot(path=str(shots / "board_1280.png"))
    # a Scout-model number (projection shown); cards show whole-number chances, the player page 0.1 points
    over = page.locator(".sw-research-grid .sw-scout [data-fcside=mean][data-fc]").first
    fid = over.get_attribute("data-fc")
    board = _numbers(page, fid, ".sw-research-grid")
    assert {"over", "under", "mean"} <= set(board) and all(len(v) == 1 for v in board.values()), board
    # the record itself, through the API with the member token
    rec = page.evaluate("""async (fid) => { const a = window.ScoutSportsApp.api; const r = await a.get('/api/sports/forecast', {league: 'nba', id: fid}); return r.data.forecast; }""", fid)
    pct = lambda p: f"{round(p * 1000) / 10:.1f}%"
    pct0 = lambda p: "<1%" if 0 < p < 0.005 else f"{round(p * 100)}%"
    assert board["over"] == {pct0(rec["p_over"])} and board["under"] == {pct0(rec["p_under"])}
    assert board.get("push", {pct0(rec["p_push"])}) == {pct0(rec["p_push"])}
    assert board["mean"] == {f"{rec['projection_mean']:.1f}"}
    # board -> player (the line link of that quote)
    row = page.locator(".sw-research-card", has=page.locator(f'[data-fc="{fid}"]')).first
    row.locator("a.sw-line").first.click()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    page.wait_for_timeout(300)
    page.screenshot(path=str(shots / "player_1280.png"), full_page=True)
    player = _numbers(page, fid)
    for k in ("over", "under", "push"):
        assert player[k] == {pct(rec["p_" + k])}, (k, player, rec)
    assert player["mean"] == board["mean"], (player, board)
    assert page.locator("svg.sw-chart").count() >= 1
    # player -> match context
    page.locator("a", has_text="Match context").first.click()
    page.wait_for_selector("#sw-gameprops .sw-research-grid", timeout=20000)
    page.screenshot(path=str(shots / "game_1280.png"))
    game = _numbers(page, fid, ".sw-research-grid")
    for k in board:
        assert game[k] == board[k], (k, game, board)
    # back to the player, add the offered Over leg -> Slip Studio draft
    page.go_back()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    act = page.locator("#sw-main .sw-actions-card .sw-act").first
    choice = act.locator("label.sw-act-side:not(.off)").first
    side = "over" if "over" in choice.get_attribute("class") else "under"
    choice.click()
    act.locator("[data-act-add]").click()
    page.wait_for_selector("#sw-tray:not([hidden]) .sw-tray-leg")
    drafts = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('ss:draft:')).map(k => [k, JSON.parse(localStorage.getItem(k))])")
    legs = [l for _, d in drafts for l in d["legs"] if l.get("forecast_id") == fid]
    assert legs and legs[0]["side"] == side and legs[0]["p_win"] == rec["p_" + side]
    assert legs[0]["player"] in page.locator("#sw-tray").inner_text()
    page.locator("#sw-tray a", has_text="Open Slip Studio").click()
    page.wait_for_url(re.compile(r"/sports/slips\.html"))
    page.wait_for_timeout(1500)
    page.screenshot(path=str(shots / "slips_from_board_1280.png"), full_page=True)
    draft_text = page.locator("#ss-draft").inner_text()
    assert legs[0]["player"] in draft_text
    assert pct(rec["p_" + side]) in draft_text                      # Slip Studio shows the same chance
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


@pytest.mark.parametrize("width,height,name", [(360, 780, "360"), (768, 1024, "768"), (1280, 900, "1280"), (1440, 900, "1440"), (640, 450, "zoom200")])
def test_layouts_have_no_page_overflow(pw, shots, width, height, name):
    ctx, page, errors = _page(pw, width, height, has_touch=width < 768, is_mobile=width < 768)
    for view, sel in (("slate", ".sw-game"), ("board?lg=nba", ".sw-count"), ("results", "h1")):
        page.goto(URL + "/sports#/" + view)
        page.wait_for_selector(sel, timeout=20000)
        page.wait_for_timeout(200)
        assert _no_overflow(page), (view, width)
        page.screenshot(path=str(shots / f"{view.split('?')[0]}_{name}.png"))
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-count")
    assert page.locator(".sw-research-grid").is_visible()
    link = page.locator(".sw-research-card a.sw-line").locator("visible=true").first
    link.click()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    page.wait_for_timeout(200)
    assert _no_overflow(page), ("player", width)
    page.screenshot(path=str(shots / f"player_{name}.png"), full_page=width < 768)
    ctx.close()


def test_states_empty_notready_error_offline_stale(pw, shots):
    ctx, page, errors = _page(pw)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-count")
    page.fill("#sw-q", "zzzz-no-such-player")
    page.wait_for_selector("text=No props match these filters")
    assert "0</b> of" in page.inner_html("#sw-bcount")
    page.screenshot(path=str(shots / "state_empty.png"))
    page.click("#sw-reset2")
    page.wait_for_selector(".sw-research-card")
    # filters persist across reload and back navigation
    page.select_option("#sw-stat", "rebounds")
    page.wait_for_timeout(200)
    page.reload()
    page.wait_for_selector(".sw-count")
    assert page.locator("#sw-stat").input_value() == "rebounds"
    page.click("#sw-reset")
    page.wait_for_timeout(200)
    assert page.locator("#sw-stat").input_value() == "all"
    # stale: a snapshot built 3 hours ago -> the banner says when, never silently
    def old_manifest(route):
        resp = route.fetch()
        m = resp.json()
        m["built_at"] = "2000-01-01T00:00:00Z"
        route.fulfill(response=resp, body=json.dumps(m))
    page.route("**/api/sports/manifest*", old_manifest)
    page.evaluate("window.ScoutSportsApp.api.mem = {}")
    page.goto(URL + "/sports#/slate")
    page.wait_for_selector("text=Board last rebuilt")
    page.screenshot(path=str(shots / "state_stale.png"))
    page.unroute("**/api/sports/manifest*")
    page.evaluate("window.ScoutSportsApp.api.mem = {}")
    # not ready / error from the API
    page.route("**/api/sports/results*", lambda r: r.fulfill(status=503, content_type="application/json",
                                                           body=json.dumps({"error": "snapshot_not_ready"})))
    page.goto(URL + "/sports#/results")
    page.wait_for_selector("text=Sports data is being prepared")
    page.screenshot(path=str(shots / "state_notready.png"))
    page.unroute("**/api/sports/results*")
    page.route("**/api/sports/results*", lambda r: r.fulfill(status=500, content_type="application/json", body="{}"))
    page.evaluate("window.ScoutSportsApp.api.mem = {}")
    page.goto(URL + "/sports#/slate")
    page.wait_for_selector(".sw-game")
    page.goto(URL + "/sports#/results")
    page.wait_for_selector("text=Something went wrong")
    page.screenshot(path=str(shots / "state_error.png"))
    page.unroute("**/api/sports/results*")
    # offline: the copy already loaded stays, clearly marked
    page.goto(URL + "/sports#/slate")
    page.wait_for_selector(".sw-game")
    page.evaluate("Object.values(window.ScoutSportsApp.api.mem).forEach(v => v.at -= 120000)")   # past the TTL
    ctx.set_offline(True)
    page.evaluate("location.hash = '#/board?lg=nba'")
    page.evaluate("location.hash = '#/slate'")
    page.wait_for_selector("text=Offline.")
    page.screenshot(path=str(shots / "state_offline.png"))
    ctx.set_offline(False)
    ctx.close()


def test_partial_data_player_without_history_or_chance(pw, shots):
    ctx, page, errors = _page(pw)
    page.route("**/api/sports/history*", lambda r: r.fulfill(status=503, content_type="application/json",
                                                           body=json.dumps({"error": "sports_busy"})))
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-research-grid a.sw-line")
    page.locator(".sw-research-grid a.sw-line").first.click()
    page.wait_for_selector("text=Game history did not load")
    page.screenshot(path=str(shots / "state_partial.png"))
    ctx.close()


def test_keyboard_focus_labels_targets_contrast_motion(pw, shots):
    ctx, page, errors = _page(pw, reduced_motion="reduce")
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-research-card")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "sw-skip"
    for _ in range(6):
        page.keyboard.press("Tab")
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline not in ("none", ""), outline
    rep = page.evaluate(A11Y_JS)
    assert not rep["unlabelled"], rep["unlabelled"]
    assert not rep["contrast"], rep["contrast"][:10]
    assert rep["svgNoLabel"] == 0
    assert len(rep["small"]) == 0, rep["small"][:10]
    page.locator(".sw-research-grid a.sw-line").first.click()
    page.wait_for_selector("#sw-fc-h")
    rep = page.evaluate(A11Y_JS)
    assert not rep["unlabelled"] and not rep["contrast"] and rep["svgNoLabel"] == 0, rep
    # reduced motion: the skeleton shimmer is off
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div class=\"sw-skel\" id=\"t-sk\"></div>')")
    assert page.evaluate("getComputedStyle(document.getElementById('t-sk')).animationName") == "none"
    ctx.close()


@pytest.mark.parametrize("width", [360, 1280])
def test_track_pick_and_my_picks(pw, shots, width):
    """Track pick is a real button after the Over / Under selector (keyboard: Space on the side, Enter on the button); the server answers per
    pick (recorded, or refused with its reason - e.g. a started game on an old snapshot) in the status toast; My picks
    renders (list or empty state) without page overflow, with labelled targets and readable contrast."""
    ctx, page, errors = _page(pw, width, 800, has_touch=width < 768, is_mobile=width < 768)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-count", timeout=20000)
    act = page.locator(".sw-act:has([data-act-track])").locator("visible=true").first
    btn = act.locator("[data-act-track]")
    assert btn.inner_text() == "Track pick" and btn.get_attribute("aria-label").startswith("Track pick: ") and btn.is_disabled() == (act.locator("input:checked").count() == 0)
    act.locator("input[type=radio]:not([disabled])").first.focus()       # keyboard: focus the side, Space selects it
    page.keyboard.press("Space")
    assert not btn.is_disabled()
    btn.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('sw-toast').innerText.length > 0", timeout=20000)
    toast = page.locator("#sw-toast").inner_text()
    assert toast.startswith(("Tracked:", "Not tracked:")), toast
    assert _no_overflow(page)
    page.screenshot(path=str(shots / f"track_{width}.png"))
    page.goto(URL + "/sports#/picks")
    page.wait_for_selector("h1:has-text('My picks')", timeout=20000)
    page.wait_for_selector(".sw-state, .sw-picklist", timeout=20000)
    assert page.locator('#sw-views a[data-view="picks"]').get_attribute("aria-current") == "page"
    assert _no_overflow(page)
    a = page.evaluate(A11Y_JS)
    assert not a["small"] and not a["unlabelled"] and not a["contrast"], a
    page.screenshot(path=str(shots / f"picks_{width}.png"), full_page=True)
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


def test_card_actions_selector(pw, shots):
    """Card actions (ux item 2): Over / Under selector (no side preselected when both are offered; an unoffered side is
    disabled with its reason), then Add to slip / Track pick, disabled until a side is chosen, with the helper text."""
    ctx, page, errors = _page(pw)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-act", timeout=20000)
    assert page.locator("[data-leg], [data-track]").count() == 0             # the four old buttons are gone
    acts = page.locator(".sw-act:has(.sw-act-sides)")
    assert acts.count() > 0
    for i in range(min(acts.count(), 30)):
        a = acts.nth(i)
        enabled = a.locator("input[type=radio]:not([disabled])").count()
        checked = a.locator("input:checked").count()
        assert checked == (1 if enabled == 1 else 0), (i, enabled, checked)
        for off in a.locator("label.sw-act-side.off").all():
            assert off.locator("input").is_disabled() and off.locator(".sw-act-why").text_content().strip()   # text_content: also inside a closed Compare section
        assert "Track" in a.locator(".sw-act-help").text_content()
    a = acts.filter(has=page.locator("input:not(:checked):not([disabled])")).first
    add, tr = a.locator("[data-act-add]"), a.locator("[data-act-track]")
    if a.locator("input:checked").count() == 0:
        assert add.is_disabled() and tr.is_disabled()
    a.locator("label.sw-act-side:not(.off)").first.click()
    assert not add.is_disabled() and not tr.is_disabled()
    add.click()
    page.wait_for_selector("#sw-toast .sw-banner")
    n = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('ss:draft:')).map(k => JSON.parse(localStorage.getItem(k)).legs.length).reduce((a, b) => a + b, 0)")
    assert n >= 1
    page.screenshot(path=str(shots / "card_actions_1280.png"))
    # exactly one action area per offer on the board, the match view and the player card; no old buttons anywhere
    def one_per_offer():
        for slot in page.locator(".sw-actions-slot").all()[:40]:
            assert slot.locator(".sw-act").count() <= 1
        assert page.locator("[data-leg], [data-track]").count() == 0
    one_per_offer()
    page.locator(".sw-research-card .sw-matchline a").first.click()
    page.wait_for_selector("#sw-gameprops .sw-act", timeout=20000)
    one_per_offer()
    page.locator("#sw-gameprops a.sw-line").first.click()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    assert page.locator("#sw-main .sw-actions-card .sw-act").count() == 1
    one_per_offer()
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


@pytest.mark.parametrize("width", [360, 1280])
def test_slip_tray_persistent_and_in_sync(pw, shots, width):
    """Slip tray (ux item 3): side panel (desktop) / bottom tray (mobile) with the picks, book, count, remove, Open Slip
    Studio and a validation hint; it is Slip Studio's own draft (ss:draft / ss:ctx), so both stay in sync; it never
    navigates by itself; only the book the picks came from is shown."""
    ctx, page, errors = _page(pw, width, 800, has_touch=width < 768, is_mobile=width < 768)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-act", timeout=20000)
    page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('ss:')).forEach(k => localStorage.removeItem(k))")
    page.evaluate("location.hash = '#/board?lg=nba&x=1'")
    page.wait_for_timeout(300)
    assert page.locator("#sw-tray").is_hidden()                                 # nothing to show: no space used
    acts = page.locator(".sw-act:has(.sw-act-sides)").locator("visible=true")
    added, book = [], None                                                      # two picks from the same book
    for i in range(acts.count()):
        a = acts.nth(i)
        if a.locator("label.sw-act-side:not(.off)").count() == 0:
            continue
        bk = a.locator("xpath=ancestor::div[contains(@class,'sw-offer')][1]").locator(".sw-book").inner_text().strip()
        if book and bk != book:
            continue
        book = bk
        a.locator("label.sw-act-side:not(.off)").first.click()
        a.locator("[data-act-add]").click()
        added.append(i)
        if len(added) == 2:
            break
    assert "#/board" in page.url and "slips" not in page.url                   # research is never left
    tray = page.locator("#sw-tray")
    assert tray.is_visible()
    if width < 1200:
        bar = tray.locator("[data-tray-toggle]")
        assert bar.get_attribute("aria-expanded") == "false"
        bar.focus(); page.keyboard.press("Enter")
        assert tray.locator("[data-tray-toggle]").get_attribute("aria-expanded") == "true"
    ctxv = page.evaluate("JSON.parse(localStorage.getItem('ss:ctx'))")
    draft = page.evaluate("k => JSON.parse(localStorage.getItem(k))", f"ss:draft:nba:{ctxv['platform']}")
    text = tray.inner_text()
    books = {"prizepicks": "PrizePicks", "underdog": "Underdog"}
    assert books[ctxv["platform"]] in text
    for p, label in books.items():                                              # a book with no picks is never named
        has = page.evaluate("k => (JSON.parse(localStorage.getItem(k)) || {legs: []}).legs.length", f"ss:draft:nba:{p}")
        if not has:
            assert label not in text, (label, text)
    assert tray.locator(".sw-tray-leg").count() == len(draft["legs"])
    assert tray.locator(".sw-tray-hint").count() == 1 and tray.locator(".sw-tray-hint").inner_text().strip()
    assert tray.locator("a.sw-tray-go").get_attribute("href") == "/sports/slips.html"
    assert _no_overflow(page)
    page.mouse.move(0, 0)
    a11y = page.evaluate(A11Y_JS)
    assert not a11y["small"] and not a11y["unlabelled"] and not a11y["contrast"], a11y
    page.screenshot(path=str(shots / f"tray_{width}.png"))
    # Slip Studio shows the same draft; removing a leg there is reflected in the tray
    n = len(draft["legs"])
    sp = ctx.new_page()
    sp.goto(URL + "/sports/slips.html")
    sp.wait_for_timeout(1500)
    for l in draft["legs"]:
        assert l["player"] in sp.locator("#ss-draft").inner_text()
    sp.locator("#ss-draft [data-rm]").first.click()
    page.wait_for_function(f"document.querySelectorAll('#sw-tray .sw-tray-leg').length === {n - 1} || ({n - 1} === 0 && document.getElementById('sw-tray').hidden)", timeout=5000)
    sp.close()
    # remove from the tray (keyboard), the draft follows
    if n - 1 > 0:
        rm = tray.locator("[data-tray-rm]").first
        rm.focus(); page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        left = page.evaluate("k => (JSON.parse(localStorage.getItem(k)) || {legs: []}).legs.length", f"ss:draft:nba:{ctxv['platform']}")
        assert left == n - 2
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


def test_filters_quick_strip_chips_saved_mobile(pw, shots):
    """Filters (ux item 5): quick stat buttons, game strip, chips with remove + Clear all, saved on the device, the book
    list = books with priced offers on the board only (owner rule 2026-10-24), secondary filters collapsed on phones."""
    ctx, page, errors = _page(pw)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector("[data-fstat]", timeout=20000)
    page.click("[data-fclear]") if page.locator("[data-fclear]").count() else None
    page.wait_for_selector("[data-fstat='all'][aria-pressed=true]")
    total = int(page.inner_text("#sw-bcount b"))
    priced = page.evaluate("""async () => { const a = window.ScoutSportsApp.api; const b = (await a.get('/api/sports/board', {league: 'nba'})).data;
      return window.SportsFilters.pricedBooks(b.markets); }""")
    opts = page.eval_on_selector_all("#sw-book option", "os => os.map(o => o.value)")
    assert opts[0] == "all" and sorted(opts[1:]) == sorted(priced), (opts, priced)
    # quick stat -> chip -> remove
    stat = page.locator("[data-fstat]:not([data-fstat='all'])").first
    key = stat.get_attribute("data-fstat")
    stat.click()
    assert stat.get_attribute("aria-pressed") == "true" and page.locator("#sw-stat").input_value() == key
    page.wait_for_selector("[data-fchip='stat']")
    n_stat = int(page.inner_text("#sw-bcount b"))
    assert n_stat <= total
    # game strip
    g = page.locator("[data-fev]:not([data-fev='all'])").first
    g.click()
    page.wait_for_selector("[data-fchip='ev']")
    assert g.get_attribute("aria-pressed") == "true"
    # saved on this device: reload keeps both
    page.reload()
    page.wait_for_selector("[data-fchip='ev']", timeout=20000)
    assert page.locator(f"[data-fstat='{key}']").get_attribute("aria-pressed") == "true"
    page.screenshot(path=str(shots / "filters_1280.png"))
    # chip remove (keyboard) and Clear all
    chip = page.locator("[data-fchip='stat']")
    chip.focus(); page.keyboard.press("Enter")
    page.wait_for_selector("[data-fchip='stat']", state="detached")
    assert page.locator("[data-fstat='all']").get_attribute("aria-pressed") == "true"
    page.click("[data-fclear]")
    page.wait_for_selector("[data-fchip]", state="detached")
    assert int(page.inner_text("#sw-bcount b")) == total
    # a saved book with no prices on the board: no option, no chip, falls back to All books
    page.evaluate("""() => { const k = 'scout.sports.filters.v1'; const v = JSON.parse(localStorage.getItem(k)); v.filters.nba.book = 'no-such-book'; localStorage.setItem(k, JSON.stringify(v)); }""")
    page.reload()
    page.wait_for_selector("[data-fstat]", timeout=20000)
    assert page.locator("#sw-book").input_value() == "all" and page.locator("[data-fchip='book']").count() == 0
    a11y = page.evaluate(A11Y_JS)
    assert not a11y["small"] and not a11y["unlabelled"] and not a11y["contrast"], a11y
    ctx.close()
    # phone: secondary filters collapsed so players appear sooner; the choice is remembered
    ctx, page, errors2 = _page(pw, 360, 780, has_touch=True, is_mobile=True)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-research-card", timeout=20000)
    det = page.locator("#sw-fx-more")
    assert det.get_attribute("open") is None and not page.locator("#sw-book, #sw-side").first.is_visible()
    assert page.locator("#sw-q").is_visible() and page.locator("[data-fstat]").first.is_visible()
    top_closed = page.locator(".sw-research-card").first.bounding_box()["y"]
    assert _no_overflow(page)
    page.screenshot(path=str(shots / "filters_360.png"))
    page.locator("#sw-fx-more > summary").click()
    page.wait_for_timeout(100)
    assert page.locator(".sw-research-card").first.bounding_box()["y"] > top_closed
    page.reload()
    page.wait_for_selector("#sw-fx-more", timeout=20000)
    assert page.locator("#sw-fx-more").get_attribute("open") is not None
    page.locator("#sw-fx-more > summary").click()
    assert _no_overflow(page)
    assert not [e for e in errors + errors2 if "favicon" not in e], errors + errors2
    ctx.close()


MINE = {"version": "pk-1", "total": 4, "shown": 4, "stake_units": 0, "picks": [
    {"pick_id": "pk-a", "tracked_at": "2026-10-24T18:05:00Z", "league": "nba", "platform": "underdog", "player": "Test Open",
     "event_ref": "BOS @ NYK", "stat": "points", "stat_label": "Points", "line": 20.5, "side": "over", "record_id": "offer:ud:1:r1:aa",
     "revision": 1, "forecast_id": "fc-a", "model_version": "sharp-2", "displayed_source": "scout_forecast", "p_side": 0.612,
     "validation_text": "Projection only - model not yet validated", "stake_units": 0, "result": None},
    {"pick_id": "pk-b", "tracked_at": "2026-10-23T18:05:00Z", "league": "nba", "platform": "prizepicks", "player": "Test Won",
     "event_ref": "LAL @ GSW", "stat": "rebounds", "stat_label": "Rebounds", "line": 8.5, "side": "under", "record_id": "offer:pp:2:r1:bb",
     "revision": 1, "forecast_id": None, "model_version": None, "displayed_source": None, "p_side": None,
     "validation_text": "No Scout chance on this line", "stake_units": 0,
     "result": {"status": "win", "outcome": "under", "value": 6, "revision": 1, "settled_at": "2026-10-24T06:00:00Z", "corrected": False}},
    {"pick_id": "pk-c", "tracked_at": "2026-10-23T17:05:00Z", "league": "nba", "platform": "underdog", "player": "Test Corrected",
     "event_ref": "LAL @ GSW", "stat": "assists", "stat_label": "Assists", "line": 5.5, "side": "over", "record_id": "offer:ud:3:r2:cc",
     "revision": 2, "forecast_id": "fc-c", "model_version": "sharp-2", "displayed_source": "scout_forecast", "p_side": 0.55,
     "validation_text": "Projection only - model not yet validated", "stake_units": 0,
     "result": {"status": "loss", "outcome": "under", "value": 5, "revision": 2, "settled_at": "2026-10-24T07:00:00Z", "corrected": True}},
    {"pick_id": "pk-d", "tracked_at": "2026-10-23T16:05:00Z", "league": "nba", "platform": "underdog", "player": "Test Void",
     "event_ref": "LAL @ GSW", "stat": "points", "stat_label": "Points", "line": 12.5, "side": "over", "record_id": "offer:ud:4:r1:dd",
     "revision": 1, "forecast_id": None, "model_version": None, "displayed_source": None, "p_side": None,
     "validation_text": "No Scout chance on this line", "stake_units": 0,
     "result": {"status": "void", "outcome": None, "value": None, "revision": 1, "settled_at": "2026-10-24T07:00:00Z", "corrected": False}}]}


@pytest.mark.parametrize("width", [360, 1280])
def test_my_picks_dashboard(pw, shots, width):
    """My picks (ux item 7): tabs Open / Settled / Voided; each row = player, stat, side, recorded line, platform,
    tracked time (local + zone), chance shown + source, settlement status, result at its own line, corrections marked;
    the member's W-L-P + hit rate (n) separate from model validation (n settled, minimum 300); stake 0, no grade letters, no returns."""
    ctx, page, errors = _page(pw, width, 800, has_touch=width < 768, is_mobile=width < 768, timezone_id="America/Chicago")
    page.route("**/api/sports/picks/mine*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(MINE)))
    page.goto(URL + "/sports#/picks")
    page.wait_for_selector(".sw-mp-tabs", timeout=20000)
    tabs = page.locator("[role=tab]")
    assert tabs.all_inner_texts() == ["Open 1", "Settled 2", "Voided 1"] or [t.replace("\n", " ") for t in tabs.all_inner_texts()] == ["Open 1", "Settled 2", "Voided 1"]
    sumtext = page.locator(".sw-mp-sum").inner_text()
    assert "1-1-0" in sumtext and "50.0%" in sumtext and "n = 2 decided" in sumtext
    val = page.locator(".sw-mp-val").inner_text()
    assert re.search(r"Model validation \(NBA, [^)]+\): [\d,]+ settled \(minimum 300\)", val), val
    assert page.locator(".sw-mp-val").bounding_box()["y"] > page.locator(".sw-mp-sum").bounding_box()["y"]    # separate panels
    row = page.locator(".sw-mp-row").first.inner_text()
    for txt in ("Test Open", "Over 20.5", "Points", "Underdog", "61.2%", "Scout forecast", "sharp-2", "Not settled yet", "CDT"):
        assert txt in row, (txt, row)
    # keyboard: arrows move between tabs
    page.locator("[role=tab][aria-selected=true]").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("#sw-mp-t-settled[aria-selected=true]")
    assert page.evaluate("document.activeElement.id") == "sw-mp-t-settled"
    settled = page.locator("#sw-mp-panel").inner_text()
    assert "Won" in settled and "Final 6 vs Under 8.5" in settled and "No Scout chance shown" in settled
    assert "Corrected (revision 2)" in settled and "Final 5 vs Over 5.5" in settled
    page.keyboard.press("ArrowRight")
    page.wait_for_selector("#sw-mp-t-voided[aria-selected=true]")
    assert "Void" in page.locator("#sw-mp-panel").inner_text()
    body = page.locator("#sw-main").inner_text()
    assert not re.search(r"\bgrade:? [A-F][+-]?(\s|$)|\bprofit\b|\bROI\b|\$\d|\bunits? (won|lost)\b", body, re.I), body
    assert _no_overflow(page)
    a = page.evaluate(A11Y_JS)
    assert not a["small"] and not a["unlabelled"] and not a["contrast"], a
    page.screenshot(path=str(shots / f"mypicks_{width}.png"), full_page=True)
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


def test_back_to_board_keeps_filters_and_scroll(pw, shots):
    ctx, page, errors = _page(pw)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-research-card", timeout=20000)
    stat = page.locator("#sw-stat")
    opts = [o for o in stat.locator("option").all_inner_texts()]
    if len(opts) > 1:
        stat.select_option(index=1)
        page.wait_for_timeout(300)
    link = page.locator(".sw-research-card a.sw-player-name").nth(6)
    link.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    y0, hash0 = page.evaluate("window.scrollY"), page.evaluate("location.hash")
    assert y0 > 300                                                   # really scrolled down the board
    h0 = page.evaluate("[document.documentElement.scrollHeight, innerHeight]")
    link.click()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    page.go_back()
    page.wait_for_selector(".sw-research-card", timeout=20000)
    page.wait_for_timeout(500)
    assert page.evaluate("location.hash") == hash0
    assert abs(page.evaluate("window.scrollY") - y0) < 40, (page.evaluate("window.scrollY"), y0, h0, page.evaluate("[document.documentElement.scrollHeight, innerHeight, document.querySelectorAll('.sw-research-card').length]"))
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


FIT_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); return r.width && r.height ? r : null; };
  const out = {clipped: [], svg: [], wrapped: [], pad: []};
  document.querySelectorAll('.sw-research-card').forEach(card => {
    const c = card.getBoundingClientRect();
    card.querySelectorAll('.sw-act button, .sw-act label, .sw-cmp-item a, .sw-cmp-item .sw-srcbtn').forEach(el => {
      const r = vis(el); if (!r) return;
      if (r.right > c.right - 4 || r.left < c.left + 4) out.clipped.push((el.innerText || '').trim().slice(0, 30) + ' ' + Math.round(r.left) + '..' + Math.round(r.right) + ' card ' + Math.round(c.left) + '..' + Math.round(c.right));
    });
    card.querySelectorAll('.sw-mkline, .sw-cmp-cap, .sw-altnote').forEach(el => {
      const r = vis(el); if (!r) return;
      const range = document.createRange(); range.selectNodeContents(el); const t = range.getBoundingClientRect();
      if (t.width && t.left - c.left < 12) out.pad.push(el.className + ' ' + Math.round(t.left - c.left));
    });
  });
  document.querySelectorAll('svg.sw-chart').forEach(s => {
    const sc = s.getBoundingClientRect().width / s.viewBox.baseVal.width;
    const ts = [...s.querySelectorAll('text')].map(t => [t, t.getBoundingClientRect()]).filter(([t, r]) => r.width);
    ts.forEach(([t, r]) => { const f = parseFloat(getComputedStyle(t).fontSize) * sc; if (f < 9.5) out.svg.push('font ' + f.toFixed(1) + " '" + t.textContent + "'"); });
    for (let i = 0; i < ts.length; i++) for (let j = i + 1; j < ts.length; j++) {
      const a = ts[i][1], b = ts[j][1];
      if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1) out.svg.push("overlap '" + ts[i][0].textContent + "' / '" + ts[j][0].textContent + "'");
    }
  });
  document.querySelectorAll('.sw-act-btns .sw-btn, .sw-views a, [data-ladder], .sw-tray-go, .sw-tray-rm').forEach(el => {
    if (!vis(el)) return;
    const range = document.createRange(); range.selectNodeContents(el);
    const tops = new Set([...range.getClientRects()].filter(x => x.width > 1 && x.height > 4).map(x => Math.round(x.bottom)));
    if (tops.size > 1) out.wrapped.push((el.innerText || '').trim().slice(0, 30));
  });
  return out;
}
"""


@pytest.mark.parametrize("width", [360, 390, 768, 1024, 1280, 1440])
def test_uiqa_cards_compare_charts_fit(pw, shots, width):
    """UI QA 2026-10-24: at every width the research cards' action areas and the opened comparison stay inside the card
    (the old 6-column compare table pushed Over / Under / Track pick out of view), card text keeps the card padding,
    chart labels are legible (>= 9.5 px) and never overlap (per-bar values thin out on narrow screens), buttons and
    nav items never wrap, and no raw grade code (gp_band:*) reaches a member page - the research letter (rg-1) is
    rendered instead, with its "Research grade" tag (owner decision 2026-10-25)."""
    ctx, page, errors = _page(pw, width, 900, has_touch=width < 768, is_mobile=width < 768)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-research-card", timeout=20000)
    cards = page.locator(".sw-research-card:has(details.sw-compare)")
    for i in range(min(cards.count(), 4)):
        cards.nth(i).locator("details.sw-compare > summary").click()
    side = page.locator(".sw-research-card .sw-cmp-item label.sw-act-side:not(.off)").first
    if side.count():
        side.click()
    page.wait_for_timeout(200)
    assert page.locator(".sw-research-card table.sw-cmp").count() == 0           # stacked list, no sideways table
    assert _no_overflow(page)
    fit = page.evaluate(FIT_JS)
    assert not fit["clipped"] and not fit["pad"] and not fit["wrapped"], fit
    page.screenshot(path=str(shots / f"uiqa_board_{width}.png"))
    page.locator(".sw-research-card a.sw-line").locator("visible=true").first.click()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    if page.locator('[data-win="20"]').count():
        page.locator('[data-win="20"]').first.click()
        page.wait_for_timeout(200)
    assert _no_overflow(page)
    fit = page.evaluate(FIT_JS)
    assert not fit["svg"] and not fit["wrapped"], fit
    main_text = page.locator("#sw-main").inner_text()
    assert "gp_band:" not in main_text
    for g in page.locator("#sw-main .sw-grade").all():                          # rendered letters only
        assert re.match(r"^(A|B|C|Lean) (Over|Under)$", g.inner_text().strip()), g.inner_text()
    if page.locator("#sw-main .sw-grade").count():
        assert "research grade" in main_text.lower()
    cur = page.locator("#sw-views a[aria-current=page]").bounding_box()
    nav = page.locator("#sw-views").bounding_box()
    assert cur["x"] >= nav["x"] - 1 and cur["x"] + cur["width"] <= nav["x"] + nav["width"] + 1, (cur, nav)   # current view in sight
    page.screenshot(path=str(shots / f"uiqa_player_{width}.png"), full_page=True)
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()


@pytest.mark.parametrize("width", [360, 1280, 1440])
def test_uiqa_tray_toast_nav_fit(pw, shots, width):
    """The slip tray never covers the cards (side panel at >= 1200 px) or the last action area (bottom bar), the status
    toast sits below the sticky header, the tray hint lists each problem once, and Slip Studio's nav shows every view."""
    ctx, page, errors = _page(pw, width, 800, has_touch=width < 768, is_mobile=width < 768)
    page.goto(URL + "/sports#/board?lg=nba")
    page.wait_for_selector(".sw-act", timeout=20000)
    page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('ss:')).forEach(k => localStorage.removeItem(k))")
    page.evaluate("location.hash = '#/board?lg=nba&x=1'")
    page.wait_for_timeout(300)
    acts = page.locator(".sw-offer .sw-act:has(.sw-act-sides)").locator("visible=true")
    n = 0
    for i in range(min(acts.count(), 20)):
        a = acts.nth(i)
        if a.locator("label.sw-act-side:not(.off)").count() == 0:
            continue
        a.locator("label.sw-act-side:not(.off)").first.click()
        a.locator("[data-act-add]").click()
        n += 1
        if n == 2:
            break
    page.wait_for_selector("#sw-tray:not([hidden])")
    toast = page.locator("#sw-toast").bounding_box()
    head = page.locator("#sw-top").bounding_box()
    if toast and toast["height"]:
        assert toast["y"] >= head["y"] + head["height"] - 1, (toast, head)
    tray = page.locator("#sw-tray").bounding_box()
    if width >= 1200:
        for c in page.locator(".sw-research-card").all()[:12]:
            b = c.bounding_box()
            assert b["x"] + b["width"] <= tray["x"] - 8, (b, tray)
    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_timeout(200)
    covered = page.evaluate("""() => { const t = document.getElementById('sw-tray').getBoundingClientRect(); const out = [];
      document.querySelectorAll('#sw-main .sw-act button, #sw-main .sw-act label, #sw-showmore').forEach(el => { const r = el.getBoundingClientRect();
        if (!r.width) return; const x = r.left + r.width / 2, y = r.top + r.height / 2; if (y < 0 || y > innerHeight) return;
        const hit = document.elementFromPoint(x, y); if (hit && document.getElementById('sw-tray').contains(hit)) out.push(el.innerText.trim()); }); return out; }""")
    assert not covered, covered
    hints = page.locator("#sw-tray .sw-tray-hint li").all_inner_texts()
    assert len(hints) == len(set(hints)), hints
    assert _no_overflow(page)
    page.screenshot(path=str(shots / f"uiqa_tray_{width}.png"))
    page.goto(URL + "/sports/slips.html")
    page.wait_for_selector(".ss-nav a[aria-current=page]", timeout=20000)
    cur = page.locator(".ss-nav a[aria-current=page]").bounding_box()
    assert cur["x"] + cur["width"] <= width, (cur, width)
    page.wait_for_timeout(1200)
    small = page.evaluate("() => [...document.querySelectorAll('summary')].filter(s => { const r = s.getBoundingClientRect(); return r.width && r.height < 24; }).map(s => s.innerText)")
    assert not small, small
    assert _no_overflow(page)
    ctx.close()
