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


def _page(pw, width=1280, height=900, **kw):
    ctx = pw.new_context(viewport={"width": width, "height": height}, **kw)
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
    over = page.locator(".sw-research-grid [data-fcside=over][data-fc]").first
    fid = over.get_attribute("data-fc")
    board = _numbers(page, fid, ".sw-research-grid")
    assert {"over", "under", "push", "mean"} <= set(board) and all(len(v) == 1 for v in board.values()), board
    # the record itself, through the API with the member token
    rec = page.evaluate("""async (fid) => { const a = window.ScoutSportsApp.api; const r = await a.get('/api/sports/forecast', {league: 'nba', id: fid}); return r.data.forecast; }""", fid)
    pct = lambda p: f"{round(p * 1000) / 10:.1f}%"
    assert board["over"] == {pct(rec["p_over"])} and board["under"] == {pct(rec["p_under"])} and board["push"] == {pct(rec["p_push"])}
    assert board["mean"] == {f"{rec['projection_mean']:.1f}"}
    # board -> player (the line link of that quote)
    row = page.locator(".sw-research-card", has=page.locator(f'[data-fc="{fid}"]')).first
    row.locator("a.sw-line").first.click()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    page.wait_for_timeout(300)
    page.screenshot(path=str(shots / "player_1280.png"), full_page=True)
    player = _numbers(page, fid)
    for k in ("over", "under", "push", "mean"):
        assert player[k] == board[k], (k, player, board)
    assert page.locator("svg.sw-chart").count() >= 1
    # player -> match context
    page.locator("a", has_text="Match context").first.click()
    page.wait_for_selector("#sw-gameprops .sw-research-grid", timeout=20000)
    page.screenshot(path=str(shots / "game_1280.png"))
    game = _numbers(page, fid, ".sw-research-grid")
    for k in ("over", "under", "push", "mean"):
        assert game[k] == board[k], (k, game, board)
    # back to the player, add the offered Over leg -> Slip Studio draft
    page.go_back()
    page.wait_for_selector("#sw-fc-h", timeout=20000)
    act = page.locator("#sw-main .sw-actions-card .sw-act").first
    choice = act.locator("label.sw-act-side:not(.off)").first
    side = "over" if "over" in choice.get_attribute("class") else "under"
    choice.click()
    act.locator("[data-act-add]").click()
    page.wait_for_selector("#sw-toast .sw-banner")
    drafts = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('ss:draft:')).map(k => [k, JSON.parse(localStorage.getItem(k))])")
    legs = [l for _, d in drafts for l in d["legs"] if l.get("forecast_id") == fid]
    assert legs and legs[0]["side"] == side and legs[0]["p_win"] == rec["p_" + side]
    page.locator("#sw-toast a", has_text="Open Slip Studio").click()
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
            assert off.locator("input").is_disabled() and off.locator(".sw-act-why").inner_text().strip()
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
    assert not [e for e in errors if "favicon" not in e], errors
    ctx.close()
