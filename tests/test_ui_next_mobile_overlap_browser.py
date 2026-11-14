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
    monkeypatch.setenv("UI_NEXT", "1")
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


# ---- UI sweep 2026-11-12 batch 2 (#23): every app pill reachable at every width with all 8 apps on NBA / NFL ----
ALL_BOOKS = ("SCOUT_BOOK_UNIBET_AU", "SCOUT_BOOK_POINTSBET_AU", "SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE", "SCOUT_BOOK_FANDUEL",
             "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_BOVADA", "SCOUT_BOOK_DRAFTKINGS")


def fix_shot(p, name, full=False):
    """FIX_SHOTS=<dir> FIX_TAG=before|after: the screenshot of each fix (release_screenshots_2026-11-11/fix_b2)"""
    import os
    if os.environ.get("FIX_SHOTS"):
        from pathlib import Path
        out = Path(os.environ["FIX_SHOTS"])
        out.mkdir(parents=True, exist_ok=True)
        p.evaluate("document.getElementById('hub-platform')&&(document.getElementById('hub-platform').scrollLeft=0)")
        p.screenshot(path=str(out / f"{name}-{os.environ.get('FIX_TAG', 'after')}.png"), full_page=full)


@pytest.fixture
def many_apps(live, monkeypatch):
    """PP + UD + the 7 extra books with lines on every sport (the live NBA tab shows 8 apps; counts as wide as the live ones)."""
    import json
    for k in ALL_BOOKS + tuple(server.DEFAULT_ON):
        monkeypatch.setenv(k, "1")
    monkeypatch.delenv("SCOUT_BOOK_ODDSAPI", raising=False)
    import book_providers
    ids = [i for i in book_providers.providers() if i != "oddsapi"]
    monkeypatch.setattr(workspace_api, "_book_lines", lambda srv, sports: (200, json.dumps(
        {"as_of": "x", "books": {pid: {s: 300 + 11 * n for s in sports} for n, pid in enumerate(ids)}})))
    monkeypatch.setattr(book_providers, "start", lambda *a, **k: None)
    return live


PILLS = """()=>{const plat=document.getElementById('hub-platform'),pb=plat.getBoundingClientRect(),vw=document.documentElement.clientWidth,out=[];
 const bs=[...plat.querySelectorAll('[data-hub=book]')].filter(b=>!b.hidden);
 for(const b of bs){b.scrollIntoView({block:'nearest',inline:'nearest'});const r=b.getBoundingClientRect(),q=plat.getBoundingClientRect();
  if(r.width<36||r.height<30)out.push(['tiny',b.dataset.id,Math.round(r.width)]);
  if(r.left<q.left-1||r.right>q.right+1||r.left<-1||r.right>vw+1)out.push(['cut',b.dataset.id,Math.round(r.left),Math.round(r.right),Math.round(q.left),Math.round(q.right)]);
  if(b.scrollWidth>b.clientWidth+1)out.push(['text clipped',b.dataset.id,b.scrollWidth,b.clientWidth]);}
 plat.scrollLeft=0;const s=document.getElementById('hub-q').getBoundingClientRect();
 return {n:bs.length,bad:out,search:Math.round(s.width),scrolls:plat.scrollWidth>plat.clientWidth+1,ox:getComputedStyle(plat).overflowX,
   bar:document.querySelector('#u-hub .h-toolbar').scrollWidth<=document.querySelector('#u-hub .h-toolbar').clientWidth+1};}"""


@pytest.mark.parametrize("width", (320, 360, 390, 430, 768, 1100, 1280, 1440, 1920))
def test_every_app_pill_is_reachable_at_every_width(browser, many_apps, width):
    c, p = page(browser, many_apps, width, "dark", "/sports")
    try:
        p.wait_for_function("document.querySelectorAll('#hub-platform [data-hub=book]:not([hidden])').length>=10", timeout=20000)
        p.wait_for_timeout(300)
        for card in (False, True):
            if card:
                p.locator("#hub-rows tr.h-row .h-player").first.click()
                p.wait_for_selector("#hub-card:not([hidden])", timeout=20000)
                p.wait_for_timeout(400)
                if width < 1101:
                    break                                   # the phone / tablet card is a sheet over the board
            fix_shot(p, f"apps-{'card-' if card else ''}{width}")
            if width < 600:                                    # phones keep the app pills in the Filters sheet (2026-11-12)
                p.locator("#hub-filters-toggle").click()
                p.wait_for_timeout(200)
            r = p.evaluate(PILLS)
            if width < 600:
                p.keyboard.press("Escape")
            assert r["n"] >= 9 and r["bad"] == [], (width, card, r)
            assert r["bar"], (width, card, r)               # nothing runs past the toolbar
            assert r["search"] >= (150 if width >= 1101 else 120) or width < 600, (width, card, r)
            if r["scrolls"]:
                assert width < 1101 and r["ox"] in ("auto", "scroll"), (width, card, r)    # desktop wraps; only a phone strip scrolls
        assert not p.errors
    finally:
        c.close()


# ---- UI sweep 2026-11-12 H5: every ladder fits a 300-360px screen (300 = a 390px iPhone at Safari 130%): Add never cut, no text under it
@pytest.mark.parametrize("width", (300, 320, 360))
def test_every_ladder_fits_a_narrow_phone(browser, live, width):
    for path in ("/", "/sports", "/lol"):
        c, p = page(browser, live, width, "dark", path)
        try:
            n = p.locator("#hub-rows [data-hub=ladder]").count()
            for i in range(n):
                p.locator("#hub-rows [data-hub=ladder]").nth(i).click()
                p.wait_for_selector("tr.h-ladder-row .h-ladder-table tbody tr")
                p.wait_for_timeout(150)
                fix_shot(p, f"ladder-{path.strip('/') or 'cs2'}-{width}-{i}") if i == 0 else None
                wide = p.evaluate("""()=>[...document.querySelectorAll('tr.h-ladder-row .h-ladder')].map(l=>{const b=l.getBoundingClientRect();
                    return {sw:l.scrollWidth,cw:l.clientWidth,acts:[...l.querySelectorAll('.h-ld-act')].map(a=>a.getBoundingClientRect()).filter(a=>a.width&&(a.right>b.right+1||a.left<b.left-1)).length}})""")
                assert all(x["sw"] <= x["cw"] + 1 and x["acts"] == 0 for x in wide), (path, width, i, wide)
                clean(p, "tr.h-ladder-row", (path, width, i, "ladder"))
                p.locator("#hub-rows [data-hub=ladder][aria-expanded=true]").first.click()
                p.wait_for_timeout(100)
            assert not p.errors
        finally:
            c.close()


# ---- UI sweep 2026-11-12 H3: on a short phone screen the card's content gets the sheet (it had 45-150px under the pinned header)
@pytest.mark.parametrize("vp", [(375, 553), (844, 390), (667, 375), (320, 568), (390, 664)], ids=lambda v: f"{v[0]}x{v[1]}")
def test_short_phone_card_gives_its_content_most_of_the_sheet(browser, live, vp):
    w, h = vp
    for path in ("/", "/sports"):
        c = context(browser, live, {"width": w, "height": h})
        p = open_page(c, live, path, {"width": w, "height": h})
        try:
            p.wait_for_selector("#hub-rows tr[data-record]", timeout=20000)
            p.locator("#hub-rows tr.h-row .h-player").first.click()
            p.wait_for_selector("#hub-card .h-why, #hub-card .h-history-summary", timeout=20000)
            p.wait_for_timeout(400)
            fix_shot(p, f"card-short-{path.strip('/') or 'cs2'}-{w}x{h}")
            for tab in ("overview", "stats"):
                p.locator(f"#hub-tab-{tab}").click()
                p.wait_for_timeout(250)
                p.locator("#hub-card-body").evaluate("b=>{b.scrollTop=400;const d=document.getElementById('hub-detail');d.scrollTop=d.scrollHeight;}")
                p.wait_for_timeout(150)
                r = p.evaluate("""()=>{const s=document.getElementById('hub-card').getBoundingClientRect(),t=document.getElementById('hub-tab-overview').closest('.h-card-tabs').getBoundingClientRect(),
                   b=document.getElementById('hub-card-body').getBoundingClientRect();return {sheet:s.height,room:Math.min(s.bottom,b.bottom)-Math.max(t.bottom,b.top),tabs:t.top-s.top}}""")
                assert r["room"] >= 0.6 * r["sheet"], (path, vp, tab, r)
            assert not p.errors
        finally:
            c.close()



# ---- UI sweep 2026-11-12 batch B: light-theme contrast (#25 Ask tab, #28 bump chips) -------------------------------------------
# WCAG contrast of an element's text against the first non-transparent background behind it
CONTRAST = """(el)=>{const rgb=s=>{const m=s.match(/rgba?\(([^)]+)\)/);if(!m)return null;const v=m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);return {r:v[0],g:v[1],b:v[2],a:v.length>3?v[3]:1};};
 const lum=c=>{const f=x=>{x/=255;return x<=0.03928?x/12.92:Math.pow((x+0.055)/1.055,2.4);};return 0.2126*f(c.r)+0.7152*f(c.g)+0.0722*f(c.b);};
 const mix=(top,bot)=>({r:top.r*top.a+bot.r*(1-top.a),g:top.g*top.a+bot.g*(1-top.a),b:top.b*top.a+bot.b*(1-top.a),a:1});
 let layers=[];for(let n=el;n;n=n.parentElement){const c=rgb(getComputedStyle(n).backgroundColor);if(c&&c.a>0){layers.push(c);if(c.a>=1)break;}}
 let bg={r:255,g:255,b:255,a:1};for(const c of layers.reverse())bg=mix(c,bg);
 const fg=mix(rgb(getComputedStyle(el).color),bg),a=lum(fg),b=lum(bg);return {ratio:Math.round(100*(Math.max(a,b)+0.05)/(Math.min(a,b)+0.05))/100,bg:[Math.round(bg.r),Math.round(bg.g),Math.round(bg.b)]};}"""


def contrast(loc):
    return loc.evaluate(CONTRAST)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_card_ask_tab_is_readable_in_both_themes(browser, live, theme):
    c, p = page(browser, live, 390, theme, "/")
    try:
        p.locator("#hub-rows tr.h-row .h-player").first.click()
        p.wait_for_selector("#hub-card .h-why", timeout=20000)
        p.locator("#hub-tab-ask").click()
        p.wait_for_selector("#hub-card form.h-ask", timeout=10000)
        p.wait_for_timeout(300)
        fix_shot(p, f"card-ask-{theme}-390")
        form = p.locator("#hub-card form.h-ask")
        bg = contrast(form)["bg"]
        if theme == "light":
            assert sum(bg) > 600, bg                                                       # a light surface, not the dark panel
        for sel in ("label", "input", ".h-ask-sugg button"):
            for i in range(min(3, form.locator(sel).count())):
                r = contrast(form.locator(sel).nth(i))
                assert r["ratio"] >= 4.5, (theme, sel, i, r)
        assert not p.errors
    finally:
        c.close()


# ---- #26: Views opens like Filters: a full-width sheet with a backdrop on phones, a popover under its button on desktop
POPBOX = """(id)=>{const p=document.getElementById(id),b=p.getBoundingClientRect(),cs=getComputedStyle(p),hub=getComputedStyle(document.getElementById('u-hub'),'::after');
 return {l:b.left,r:b.right,t:b.top,bt:b.bottom,w:b.width,pos:cs.position,vw:document.documentElement.clientWidth,vh:innerHeight,scrim:hub.content!=='none'&&hub.content!=='normal'&&hub.position==='fixed'}}"""


@pytest.mark.parametrize("width", (320, 390, 768, 1440))
def test_views_opens_like_filters(browser, live, width):
    c, p = page(browser, live, width, "dark", "/")
    try:
        if width < 600:                     # phones (2026-11-12): Views lives inside the Filters sheet, shown open, with the sheet's backdrop
            p.locator("#hub-filters-toggle").click()
            p.wait_for_selector("#hub-filter-pop:not([hidden])")
            for sel in ("#hub-views", "[data-hub=save-view]", "[data-hub=save-alert]", "[data-hub=manage-alerts]"):
                el = p.locator("#hub-filter-pop " + sel)
                el.scroll_into_view_if_needed()
                assert el.is_visible(), sel
            f = p.evaluate(POPBOX, "hub-filter-pop")
            assert f["w"] >= f["vw"] - 20 and f["scrim"], f
            fix_shot(p, f"views-{width}")
            return
        out = {}
        for tog, pop in (("hub-filters-toggle", "hub-filter-pop"), ("hub-views-toggle", "hub-views-pop")):
            p.locator("#" + tog).click()
            p.wait_for_selector(f"#{pop}:not([hidden])")
            p.wait_for_timeout(200)
            out[pop] = p.evaluate(POPBOX, pop)
            out[pop]["btn"] = p.locator("#" + tog).bounding_box()
            if pop == "hub-views-pop":
                fix_shot(p, f"views-{width}")
            p.locator("#" + tog).click() if width >= 1101 else p.keyboard.press("Escape")
            p.wait_for_timeout(150)
        f, v = out["hub-filter-pop"], out["hub-views-pop"]
        if width < 1101:
            assert abs(v["l"] - f["l"]) <= 1 and abs(v["r"] - f["r"]) <= 1 and v["w"] >= v["vw"] - 20, out      # the same full-width sheet
            assert abs(v["bt"] - f["bt"]) <= 1 and v["scrim"] and f["scrim"], out                                 # with a backdrop
        else:
            b = v["btn"]
            assert 0 <= v["t"] - (b["y"] + b["height"]) <= 12 and v["l"] < b["x"] + b["width"] and v["r"] > b["x"], out   # under its button
        assert not p.errors
    finally:
        c.close()


# ---- #27: no chart label runs past the chart (the first angled date label, "ATL · Oct 23", was cut at the left)
@pytest.mark.parametrize("width", (390, 430, 1280, 1440))
def test_card_chart_labels_stay_inside_the_chart(browser, live, width):
    for path in ("/sports", "/"):
        c, p = page(browser, live, width, "dark", path)
        try:
            p.locator("#hub-rows tr.h-row .h-player").first.click()
            p.wait_for_selector("#hub-card svg text.opp", timeout=20000)
            p.wait_for_timeout(300)
            p.locator("#hub-card svg text.opp").first.scroll_into_view_if_needed()
            fix_shot(p, f"chart-{path.strip('/') or 'cs2'}-{width}")
            bad = p.evaluate("""()=>[...document.querySelectorAll('#hub-card svg')].flatMap(s=>{const b=s.getBoundingClientRect();
                return [...s.querySelectorAll('text')].map(t=>[t.textContent,t.getBoundingClientRect()]).filter(([_,r])=>r.width&&(r.left<b.left-1||r.right>b.right+1||r.bottom>b.bottom+1)).map(([x,r])=>[x,Math.round(r.left-b.left),Math.round(b.right-r.right)])})""")
            assert bad == [], (path, width, bad)
            assert not p.errors
        finally:
            c.close()


# ---- #28 and "every chip in light theme": every visible text on the board, the card's tabs and My picks has AA contrast in both themes
SCAN = """(sel)=>{const f=(""" + CONTRAST + """);const root=document.querySelector(sel);if(!root)return [];const out=[],seen=new Set();
 const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let n;
 while((n=w.nextNode())){const e=n.parentElement,t=n.textContent.trim();if(!t||!e||seen.has(e)||!e.offsetParent&&getComputedStyle(e).position!=='fixed')continue;seen.add(e);
  const cs=getComputedStyle(e);if(cs.visibility==='hidden'||+cs.opacity<0.99||e.closest('[disabled],[aria-disabled=true],.is-dim,.u-sr,svg'))continue;
  let hidden=false;for(let x=e;x&&x!==document.body;x=x.parentElement){if(+getComputedStyle(x).opacity<0.99){hidden=true;break}}if(hidden)continue;
  const r=f(e),big=parseFloat(cs.fontSize)>=18.66||parseFloat(cs.fontSize)>=14&&+cs.fontWeight>=700;if(r.ratio<(big?3:4.5))out.push([t.slice(0,30),e.className&&String(e.className).slice(0,30),r.ratio]);}
 return out.slice(0,12);}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", (390, 1440))
def test_text_contrast_on_board_card_and_my_picks(browser, live, theme, width):
    live.by("vargo", "underdog")["open_line"] = 37.5
    c, p = page(browser, live, width, theme, "/")
    try:
        assert p.evaluate(SCAN, "#hub-board-section") == [], "board"
        p.locator("#hub-rows tr.h-row .h-player").first.click()
        p.wait_for_selector("#hub-card .h-why", timeout=20000)
        for tab in ("overview", "lines", "stats", "ask"):
            p.locator(f"#hub-tab-{tab}").click()
            p.wait_for_timeout(300)
            assert p.evaluate(SCAN, "#hub-card") == [], tab
        assert not p.errors
    finally:
        c.close()


# ---- #29: sport tab counts on every tab or on none (they showed only for sports opened this session)
def test_sport_tab_counts_on_every_tab_or_none(browser, live):
    c, p = page(browser, live, 1440, "dark", "/sports")
    try:
        shown = lambda: p.locator("#hub-sports button").evaluate_all("bs=>bs.map(b=>{const s=b.querySelector('span');return !!s&&!s.classList.contains('h-n-wait')&&s.textContent.trim()!==''})")
        s = shown()
        assert all(s) or not any(s), s
        p.locator("#hub-sports [data-id=cs2]").click()
        p.wait_for_selector("#hub-rows tr[data-record]")
        p.wait_for_timeout(300)
        s = shown()
        assert all(s) or not any(s), s
        p.locator("#hub-sports [data-id=all]").click()
        p.wait_for_function("[...document.querySelectorAll('#hub-sports button span')].every(s=>!s.classList.contains('h-n-wait'))", timeout=20000)
        assert all(shown())
        assert not p.errors
    finally:
        c.close()



# ---- T1 / T5: every dialog outside the hub (save view, live highlight, the book-link dialog) is readable in both themes
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_dialogs_outside_the_hub_are_readable_in_both_themes(browser, live, theme):
    c, p = page(browser, live, 390, theme, "/")
    try:
        for kind in ("save-view", "save-alert"):
            p.locator("#hub-filters-toggle").click()            # 390px: Views is inside the Filters sheet
            p.locator(f"#hub-views-pop [data-hub={kind}]").click()
            p.wait_for_selector("#hub-save-dialog[open]")
            p.locator("#hub-save-name").fill("Unders tonight")
            p.wait_for_timeout(150)
            fix_shot(p, f"dialog-{kind}-{theme}")
            box = p.locator("#hub-save-dialog")
            assert p.evaluate(SCAN, "#hub-save-dialog") == [], (kind, theme)
            inp = contrast(p.locator("#hub-save-name"))
            assert inp["ratio"] >= 4.5, (kind, theme, inp)
            if theme == "light":
                assert sum(inp["bg"]) > 600, inp                                 # a light box to type in
            for b in box.locator("button").all():
                bb = b.bounding_box()
                assert bb and bb["height"] >= 44, bb                            # T16: thumb-size buttons on the phone
            assert p.locator("#hub-save-name").bounding_box()["height"] >= 44
            assert p.locator("#hub-save-name").evaluate("e=>parseFloat(getComputedStyle(e).fontSize)") >= 16       # T11: no iOS zoom
            box.locator("button[value=cancel]").click()
            p.wait_for_function("!document.getElementById('hub-save-dialog').open")
        assert not p.errors
    finally:
        c.close()


# ---- H11: the phone toolbar is three rows (it took 165-215px) and every control stays reachable
@pytest.mark.parametrize("width", (320, 375, 430))
def test_phone_toolbar_is_two_short_rows(browser, many_apps, width):
    for path in ("/", "/sports"):
        c, p = page(browser, many_apps, width, "dark", path)
        try:
            p.wait_for_timeout(500)
            fix_shot(p, f"toolbar-{path.strip('/') or 'cs2'}-{width}")
            r = p.evaluate("""()=>{const bar=document.querySelector('#u-hub .h-toolbar'),kids=[...bar.children].filter(e=>e.offsetParent&&e.getBoundingClientRect().height>0);
                const tops=kids.map(e=>Math.round(e.getBoundingClientRect().top/8));const rowsN=new Set(kids.map(e=>{const r=e.getBoundingClientRect();return Math.round((r.top+r.bottom)/2/12)})).size;
                return {h:Math.round(bar.getBoundingClientRect().height),rows:rowsN,small:kids.flatMap(e=>[...e.querySelectorAll('button,input'),e].filter(x=>x.matches('button,input')&&x.offsetParent&&!x.closest('.h-pop')).map(x=>[x.id||x.textContent.trim().slice(0,8),Math.round(x.getBoundingClientRect().height)]).filter(([_,h])=>h<43.5))}}""")
            assert r["rows"] <= 2 and r["h"] <= 120, (path, width, r)            # phones: sport tabs, then search + Filters (2026-11-12)
            assert r["small"] == [], (path, width, r)
            assert p.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+1")
            assert not p.errors
        finally:
            c.close()


# ---- T16: thumb-size controls in every section outside the board, on the phone
TARGETS = """(sel)=>{const root=document.querySelector(sel);if(!root)return null;return [...root.querySelectorAll('button,select,input:not([type=checkbox]):not([type=radio]):not([type=hidden]),a.u-btn,a.h-link,.h-link,summary,.h-tool-grid a')]
  .filter(e=>e.offsetParent&&getComputedStyle(e).visibility!=='hidden'&&!e.closest('[hidden],.h-pc-row,table')).map(e=>[(e.id||e.textContent.trim()).slice(0,24),Math.round(e.getBoundingClientRect().height)]).filter(([_,h])=>h>0&&h<43.5).slice(0,10)}"""


def test_phone_targets_outside_the_board(browser, live):
    c, p = page(browser, live, 390, "dark", "/")
    try:
        p.locator("#hub-rows tr.h-row [data-hub=add]").first.click()
        p.wait_for_timeout(300)
        bad = {}
        for hash_, sec in (("#scout/studio", "#hub-studio-section"), ("#scout/picks", "#hub-picks-section"), ("#scout/record", "#hub-record-section"),
                           ("#scout/tools", "#hub-tools-section"), ("#scout/alerts", "#hub-alerts-section"), ("#scout/ev", "#hub-ev-section"), ("#scout/arb", "#hub-arb-section")):
            p.evaluate(f"location.hash={hash_!r}")
            p.wait_for_timeout(900)
            r = p.evaluate(TARGETS, sec)
            if r:
                bad[sec] = r
        assert bad == {}, bad
        assert not p.errors
    finally:
        c.close()


# ---- phone simplification (owner 2026-11-12: "this new ui is super confusing on mobile"): one compact row per prop, two-row header,
# everything moved stays one tap away (Filters sheet, More list)
@pytest.mark.parametrize("width", (320, 375, 390, 430))
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_phone_board_is_simple(browser, many_apps, width, theme):
    for path in ("/", "/sports"):
        c, p = page(browser, many_apps, width, theme, path)
        try:
            fix_shot(p, f"simple-board-{path.strip('/') or 'cs2'}-{width}-{theme}")
            rows = p.locator("#hub-rows tr.h-row").evaluate_all("rs=>rs.slice(0,12).map(r=>Math.round(r.getBoundingClientRect().height))")
            assert rows and all(56 <= h <= (124 if width <= 320 else 104) for h in rows), (path, width, sorted(rows))          # 64-72px; a PrizePicks kind or a below-break-even note adds a line
            # the row: photo (sports), name, line, side + chance, one 44px add; no strip, no meta
            r0 = p.locator("#hub-rows tr.h-row").first
            assert r0.locator(".h-c-hits").is_hidden() and r0.locator(".h-c-line small:not(.h-odds)").is_hidden()
            if path == "/sports":
                assert r0.locator(".h-c-line .h-odds").is_visible()
            add = r0.locator(".h-c-add button").bounding_box()
            assert add["width"] >= 43.5 and add["height"] >= 43.5, add
            if path == "/sports":
                assert p.locator("#hub-rows tr.h-row .h-c-player .u-face").first.is_visible()
            # the header: sport tabs, then search + Filters
            bar = p.evaluate("""()=>[...document.querySelector('#u-hub .h-toolbar').children].filter(e=>e.offsetParent&&e.getBoundingClientRect().height).map(e=>e.className||e.id)""")
            assert len(bar) == 3, bar
            # moved controls are in the Filters sheet
            p.locator("#hub-filters-toggle").click()
            pop = p.locator("#hub-filter-pop")
            for sel in ("#hub-platform [data-id=all]", "#hub-sort", "[data-hub=save-view]", "#hub-views"):
                el = pop.locator(sel).first
                el.scroll_into_view_if_needed()
                assert el.is_visible(), sel
            if pop.locator(".h-ev-link").count():
                assert pop.locator(".h-ev-link").is_visible()
            fix_shot(p, f"simple-filters-{path.strip('/') or 'cs2'}-{width}-{theme}")
            p.locator("#hub-filter-pop .h-pop-foot [data-hub=filters]").click()
            p.locator("#hub-board-section [data-hub=group-by][data-id=player]").click()        # By prop / By player stays on the board
            p.wait_for_selector("tr.h-player-row")
            # More: Record and the other sections, sign out
            p.locator("#hub-more-toggle").click()
            more = p.locator("#hub-more")
            for t in ("Record", "Tools", "Sign out"):
                assert more.get_by_text(t, exact=False).first.is_visible(), t
            tabs = p.locator(".h-tabbar > button").evaluate_all("bs=>bs.filter(b=>getComputedStyle(b).display!=='none').map(b=>b.dataset.id||b.id)")
            assert len(tabs) == 4, tabs
            fix_shot(p, f"simple-more-{path.strip('/') or 'cs2'}-{width}-{theme}")
            more.get_by_text("Record").first.click()
            p.wait_for_selector("#hub-record-section:not([hidden])")
            assert p.locator("#hub-more-toggle").get_attribute("aria-current") == "page"
            assert p.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+1")
            assert not p.errors
        finally:
            c.close()
