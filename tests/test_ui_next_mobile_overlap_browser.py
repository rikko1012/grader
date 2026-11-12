"""Phones, new design (owner 2026-11-12: "im seeing overlapping words on player cards etc on Mobile", with a live iPhone screenshot of
two columns of prop tiles and text drawn over text). With the flags the live site runs with, at 320-430 px, in both themes and with web
fonts blocked: the board is ONE column of full-width rows, no two visible texts share the same spot, and nothing runs past its row,
the card sheet or the screen. Fixture data only (long names added)."""
import pytest

import server
import workspace_api
from tests.test_upgrade_browser import browser, env, context, open_page  # noqa: F401
from tests.test_ui_next_browser import ladder_board
from tests.workspace_fixture import iso, row
from datetime import datetime, timedelta, timezone

LIVE_FLAGS = ("SCOUT_WORKSPACE_V2", "SCOUT_BOARD_H2H", "SCOUT_PARITY_EDGE", "SCOUT_PARITY_HIT_SAMPLES", "SCOUT_PARITY_EV_BAND", "SCOUT_MARKET_EDGE", "SCOUT_PICKEM_EV",
              "SCOUT_MARKET_TOOLS", "SCOUT_GENIE", "SCOUT_CLOSING_LINES", "SCOUT_LINE_HISTORY", "SCOUT_LINE_MOVE_NOTES", "SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE")
WIDTHS = (320, 360, 375, 390, 412, 430, 768, 980)        # 768 = tablet; 980 = an iPhone showing the desktop-width page

# Every visible text run as a box (per line of text); two boxes from different elements may not overlap by more than a hair, and no
# box may leave the scope's box or the viewport sideways. Text clipped by its own ellipsis box is measured by that box.
OVERLAP = """(sel)=>{const root=document.querySelector(sel);if(!root)return {error:'no '+sel};const vw=document.documentElement.clientWidth,out=[],boxes=[];
const vis=e=>{for(let n=e;n&&n!==document.body;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||+s.opacity===0)return false;if(n.classList&&n.classList.contains('u-sr'))return false;
 const d=n.parentElement;if(d&&d.tagName==='DETAILS'&&!d.open&&n.tagName!=='SUMMARY')return false;}return true;};
const clip=e=>{let r=null;for(let n=e;n&&n!==root.parentElement;n=n.parentElement){const s=getComputedStyle(n);if(/(hidden|auto|scroll|clip)/.test(s.overflowX+s.overflowY)){const b=n.getBoundingClientRect();r=r?{l:Math.max(r.l,b.left),t:Math.max(r.t,b.top),r:Math.min(r.r,b.right),b:Math.min(r.b,b.bottom)}:{l:b.left,t:b.top,r:b.right,b:b.bottom};}}return r;};
const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;
while((n=w.nextNode())){const t=n.textContent.trim();if(!t||!n.parentElement||!vis(n.parentElement))continue;const rg=document.createRange();rg.selectNodeContents(n);const c=clip(n.parentElement);
 for(const q of rg.getClientRects()){let b={l:q.left,t:q.top,r:q.right,b:q.bottom};if(c)b={l:Math.max(b.l,c.l),t:Math.max(b.t,c.t),r:Math.min(b.r,c.r),b:Math.min(b.b,c.b)};
  if(b.r-b.l<2||b.b-b.t<2)continue;if(b.b<0||b.t>innerHeight*4)continue;boxes.push({b,el:n.parentElement,t:t.slice(0,24)});
  if(b.r>vw+1||b.l<-1)out.push(['off-screen',t.slice(0,24),Math.round(b.l),Math.round(b.r)]);}}
for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){const a=boxes[i],c=boxes[j];if(a.el===c.el||a.el.contains(c.el)||c.el.contains(a.el))continue;
 const x=Math.min(a.b.r,c.b.r)-Math.max(a.b.l,c.b.l),y=Math.min(a.b.b,c.b.b)-Math.max(a.b.t,c.b.t);if(x>2&&y>3)out.push(['overlap',a.t,c.t,Math.round(x),Math.round(y)]);}
return {n:boxes.length,bad:out.slice(0,8)};}"""


@pytest.fixture
def live(env, monkeypatch):
    for k in LIVE_FLAGS:
        monkeypatch.setenv(k, "1")
    monkeypatch.delenv("UI_NEXT", raising=False)
    soon = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    env.rows += [row("pp:7101", "prizepicks", "Tuurtle", "ex-Zero Tenacity", "Walczaki Esports Club", 28.5, "More", 0.641, 902, soon),
                 row("11111111-0000-4000-8000-0000000000b1", "underdog", "Tuurtle", "ex-Zero Tenacity", "Walczaki Esports Club", 29.5, "Less", 0.531, 902, soon),
                 row("pp:7102", "prizepicks", "ZywOoTheChosenOneLongName", "Team Vitality Rising Academy", "FORZE Reload International", 135.5, "Less", 0.702, 903, soon, odds_type="demon"),
                 row("11111111-0000-4000-8000-0000000000b2", "underdog", "ZywOoTheChosenOneLongName", "Team Vitality Rising Academy", "FORZE Reload International", 136.5, "Less", 0.552, 903, soon)]
    env.sports.boards["nba"] = ladder_board()
    for m in env.sports.boards["nba"][0]["markets"]:              # long names and a combo stat, as on the live NBA board
        if m["n"] == "Karl Anders":
            m["n"] = "Karl-Anthony Andersson-Washington Jr."
    env.sports.boards["nba"][0]["stats"]["points"] = "Points + Rebounds + Assists"
    workspace_api.reset()
    return env


def page(browser, live, width, theme, path):
    c = context(browser, live, {"width": width, "height": 780})          # the shared helper blocks web fonts: the layout must hold on the fallback face
    c.add_init_script("try{localStorage.setItem('scout-theme','%s')}catch(e){}" % theme)
    p = open_page(c, live, path, {"width": width, "height": 780})
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    p.wait_for_timeout(400)
    return c, p


def clean(p, sel, where):
    r = p.evaluate(OVERLAP, sel)
    assert "error" not in r and r["n"] > 3, (where, r)
    assert r["bad"] == [], (where, r["bad"])
    assert p.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+1"), where


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_phone_board_is_one_column_of_rows_with_no_text_on_text(browser, live, width, theme):
    for path in ("/", "/sports"):
        c, p = page(browser, live, width, theme, path)
        try:
            assert p.locator("html").get_attribute("data-ui") == "next"
            rows = p.locator("#hub-rows tr.h-row").evaluate_all("rs=>rs.map(r=>{const b=r.getBoundingClientRect();return [Math.round(b.left),Math.round(b.width),Math.round(b.top)]})")
            assert len(rows) >= 4
            assert all(left <= 1 and w >= width - 2 for left, w, _ in rows), rows                      # full-width rows...
            assert [t for _, _, t in rows] == sorted({t for _, _, t in rows}), rows                    # ...one under another, never side by side
            clean(p, "#hub-board-section", (path, width, theme, "board"))
            # every control of a row stays inside its row
            out = p.evaluate("""()=>[...document.querySelectorAll('#hub-rows tr.h-row')].flatMap(r=>{const b=r.getBoundingClientRect();
                return [...r.querySelectorAll('button,.h-gap,.u-lt,.h-grade')].filter(e=>e.offsetParent).map(e=>e.getBoundingClientRect()).filter(q=>q.width&&(q.left<b.left-1||q.right>b.right+1||q.bottom>b.bottom+1||q.top<b.top-1)).map(q=>[Math.round(q.left),Math.round(q.right)])})""")
            assert out == [], (path, width, out)
            lad = p.locator("#hub-rows [data-hub=ladder]").first
            if lad.count():
                lad.click()
                p.wait_for_selector("tr.h-ladder-row")
                clean(p, "tr.h-ladder-row", (path, width, theme, "ladder"))
                p.locator("#hub-rows [data-hub=ladder][aria-expanded=true]").first.click()
            p.locator("[data-hub=group-by][data-id=player]").click()
            p.wait_for_selector("tr.h-player-row")
            clean(p, "#hub-rows", (path, width, theme, "by player"))
            assert not p.errors
        finally:
            c.close()


@pytest.mark.parametrize("width", (320, 375, 430))
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_phone_card_sheet_has_no_text_on_text_on_any_tab(browser, live, width, theme):
    for path in ("/", "/sports", "/lol"):
        c, p = page(browser, live, width, theme, path)
        try:
            p.locator("#hub-rows tr.h-row .h-player").first.click()
            p.wait_for_selector("#hub-card .h-why, #hub-card .h-history-summary", timeout=20000)
            p.wait_for_function("!document.querySelector('#hub-detail .h-detail-loading') || document.querySelector('#hub-detail .h-history-summary')", timeout=20000)
            for tab in ("overview", "lines", "stats", "ask"):
                p.locator(f"#hub-tab-{tab}").click()
                p.wait_for_timeout(250)
                p.locator("#hub-card details:not([open]) > summary").evaluate_all("ss=>ss.slice(0,12).forEach(s=>s.click())")      # folded panels too
                p.wait_for_timeout(150)
                clean(p, "#hub-card", (path, width, theme, tab))
                box = p.locator("#hub-card").bounding_box()
                assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1
            assert not p.errors
        finally:
            c.close()


@pytest.mark.skipif(not __import__("os").environ.get("MOBILE_SHOTS"), reason="MOBILE_SHOTS not set")
@pytest.mark.parametrize("width", (375, 390))
def test_mobile_shots(browser, live, width):
    from pathlib import Path
    import os
    out = Path(os.environ["MOBILE_SHOTS"])
    out.mkdir(parents=True, exist_ok=True)
    tag = os.environ.get("MOBILE_TAG", "after")
    for path, name in (("/", "board-cs2"), ("/sports", "board-nba")):
        c, p = page(browser, live, width, "dark", path)
        try:
            p.screenshot(path=str(out / f"{name}-{width}-{tag}.png"))
            p.locator("#hub-rows [data-hub=ladder]").first.click()
            p.wait_for_timeout(300)
            p.screenshot(path=str(out / f"{name}-ladder-{width}-{tag}.png"))
            p.locator("#hub-rows tr.h-row .h-player").first.click()
            p.wait_for_selector("#hub-card .h-why, #hub-card .h-history-summary", timeout=20000)
            p.wait_for_timeout(600)
            p.screenshot(path=str(out / f"card-{name[6:]}-{width}-{tag}.png"))
            p.locator("#hub-tab-lines").click()
            p.wait_for_timeout(300)
            p.screenshot(path=str(out / f"card-{name[6:]}-lines-{width}-{tag}.png"))
        finally:
            c.close()
