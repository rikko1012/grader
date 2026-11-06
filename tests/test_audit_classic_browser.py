"""Audit 2026-11-05, classic group, in a real browser against a local server.Handler with fixture data (no network, no production
writes): the All sports research link, the phone Slip tray bar, started / expired / held legs in the account tray, the classic page
built only when shown, the Slips page's right-hand slip, the L20 cell, the phone CS2 header, owner-only tools and the Price check
default. AUDIT_SHOTS=<folder> keeps 390px and 1440px screenshots of every changed surface."""
import json
import os
import subprocess
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import server
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE, OWNER
from tests.test_hub_shell_browser import market_doc, mt_page

SHOTS = Path(os.environ["AUDIT_SHOTS"]) if os.environ.get("AUDIT_SHOTS") else None
ROOT = Path(__file__).resolve().parents[1]
CLASSIC = [("cs2", "/?view=classic#board"), ("lol", "/lol?view=classic"), ("sports", "/sports?view=classic#/board")]


def shot(p, name, full=False):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(300)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"), full_page=full)


def ready(p, name):
    sel = {"cs2": "#boardout .brow2.crow", "lol": ".u-rows .u-lol", "sports": "tr.sw-tb-row .u-add"}[name]
    p.wait_for_selector(sel, timeout=30000)
    p.wait_for_timeout(400)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------ A1: All sports research

@pytest.mark.parametrize("size", [PHONE, DESK])
@pytest.mark.parametrize("name,path", CLASSIC)
def test_all_sports_link_opens_the_board_on_all_sports_and_never_floats(browser, env, size, name, path):
    c = context(browser, env, size)
    try:
        p = open_page(c, env, path, size)
        ready(p, name)
        link = p.locator(".h-entry")
        assert link.get_attribute("href") == "/#all-sports"
        assert p.evaluate("getComputedStyle(document.querySelector('.h-entry')).zIndex") == "auto"
        if name == "lol":                                                          # in LoL's own filter bar, not hung below its sticky header
            p.wait_for_function("!!document.querySelector('.h-entry').closest('.bar')")
            p.evaluate("render()")                                                 # a redraw of the page keeps it there
            p.wait_for_function("!!document.querySelector('.h-entry').closest('.bar')")
        # scrolled: the link is under the sticky header and the Slip tray, never painted over them
        p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        p.wait_for_timeout(200)
        over = p.evaluate("""() => { const out = [];
          for (const sel of ['header', '#u-tray-btn']) { const el = document.querySelector(sel); if (!el) continue; const r = el.getBoundingClientRect();
            if (!r.width || !r.height) continue; const x = r.left + Math.min(r.width - 2, 20), y = r.top + r.height / 2, top = document.elementFromPoint(x, y);
            if (top && top.closest('.h-entry')) out.push(sel); }
          const t = document.getElementById('u-tray-btn'), e = document.querySelector('.h-entry');
          if (t && e) { const a = t.getBoundingClientRect(), b = e.getBoundingClientRect();
            if (a.width && b.width && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) { const hit = document.elementFromPoint((Math.max(a.left, b.left) + Math.min(a.right, b.right)) / 2, (Math.max(a.top, b.top) + Math.min(a.bottom, b.bottom)) / 2); if (hit && hit.closest('.h-entry')) out.push('tray-overlap'); } }
          return out; }""")
        assert over == [], over
        shot(p, f"a1-{name}-scrolled")
        p.evaluate("window.scrollTo(0, 0)")
        link.scroll_into_view_if_needed()
        link.click()
        p.wait_for_url("**/#all-sports")
        p.wait_for_selector("#u-hub:not([hidden]) #hub-rows tr", timeout=30000)
        assert p.evaluate("location.pathname + location.search + location.hash") == "/#all-sports"
        assert p.locator('#hub-sports [data-id="all"]').get_attribute("aria-pressed") == "true"
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ A14: phone Slip tray bar

@pytest.mark.parametrize("name,path", CLASSIC)
def test_phone_slip_tray_is_a_bottom_bar_that_covers_no_row(browser, env, name, path):
    c = context(browser, env, PHONE)
    try:
        p = open_page(c, env, path, PHONE)
        ready(p, name)
        box = p.locator("#u-tray-btn").bounding_box()
        assert box["x"] == 0 and abs(box["width"] - PHONE["width"]) <= 1          # full width, not a floating corner button
        modes = p.evaluate("(() => { const m = document.getElementById('modes'); if (!m) return null; const s = getComputedStyle(m); if (s.position !== 'fixed' || s.display === 'none') return null; const r = m.getBoundingClientRect(); return [r.top, r.bottom]; })()")
        if modes:                                                                  # CS2: sits on top of the section nav, not over it
            assert box["y"] + box["height"] <= modes[0] + 1
        else:
            assert abs(box["y"] + box["height"] - PHONE["height"]) <= 1
        # the end of the page clears the bar: the last row ends above it
        p.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        p.wait_for_timeout(250)
        last = p.evaluate("""(() => { const rows = [...document.querySelectorAll('#boardout .brow2.crow, .u-rows .u-lol, tr.sw-tb-row')].filter(r => r.getBoundingClientRect().height);
          return rows.length ? rows[rows.length - 1].getBoundingClientRect().bottom : null; })()""")
        tray_top = p.locator("#u-tray-btn").bounding_box()["y"]
        assert last is not None and last <= tray_top + 1, (last, tray_top)
        no_h_scroll(p)
        shot(p, f"a14-{name}-end")
        p.evaluate("window.scrollTo(0, 0)")
        shot(p, f"a14-{name}-top")
        assert not p.errors
    finally:
        c.close()


def test_desktop_tray_stays_in_the_header(browser, env):
    c = context(browser, env, DESK)
    try:
        for name, path in CLASSIC:
            p = open_page(c, env, path, DESK)
            ready(p, name)
            assert p.evaluate("document.getElementById('u-tray-btn').classList.contains('u-docked')")
            p.close()
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ S5/A9: account tray legs

def leg(i, sport="cs2", start=None, added=None, label="B Over", book="prizepicks"):
    now = datetime.now(timezone.utc)
    out = {"id": f"{sport}|ev{i}|p{i}|kills|12|{book}|over|20.5", "key": f"k{i}", "sport": sport, "book": book, "row_id": f"gone-{i}", "side": "over",
           "line": 20.5, "rev": "r1", "read_rev": None, "shown_p": 0.61, "event_id": f"ev{i}", "event_label": "Alpha vs Bravo", "player": f"Player{i}",
           "player_key": f"p{i}", "team": "Alpha", "stat": "kills", "stat_label": "Kills", "period": "12", "period_label": "Maps 1-2",
           "platform_label": "PrizePicks", "shown_label": label, "added_at": iso(added or now)}
    if start is not None:
        out["start_utc"] = iso(start)
    return out


def seed(p, legs):
    return p.evaluate("""async (legs) => { const r0 = await fetch('/api/upgrade/state'); const d = await r0.json();
      const r = await fetch('/api/upgrade/state', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({rev: d.rev || 0, slip: {legs, types: {}}})});
      return r.status; }""", legs)


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_account_tray_clears_started_and_expired_legs_and_never_shows_a_held_letter(browser, env, size):
    now = datetime.now(timezone.utc)
    legs = [leg(1, start=now - timedelta(hours=2)),                                        # started (Oct 26 style)
            leg(2, added=now - timedelta(days=10)),                                       # saved before legs carried a start: expired
            leg(3, sport="nba", start=now + timedelta(hours=5), label="A Over · Research grade"),   # NBA, letter since held, row not loaded
            leg(4, start=now + timedelta(hours=5), label="Lean Over")]                     # a Lean stays Lean
    c = context(browser, env, size)
    c.add_init_script("""window.__toasts = []; new MutationObserver(() => document.querySelectorAll('.u-toast').forEach(t => {
        if (t.textContent && !window.__toasts.includes(t.textContent)) window.__toasts.push(t.textContent); }))
      .observe(document, {subtree: true, childList: true, characterData: true});""")
    try:
        p = open_page(c, env, "/lol?view=classic", size)
        ready(p, "lol")
        assert seed(p, legs) == 200
        p.reload()
        ready(p, "lol")
        p.wait_for_function("document.getElementById('u-tray-btn').textContent.startsWith('Slip tray · 2')", timeout=20000)
        saved = None
        for _ in range(40):                                                              # the cleared slip is saved to the account
            saved = p.evaluate("async () => (await (await fetch('/api/upgrade/state', {cache: 'no-store'})).json()).slip.legs.map(l => l.player)")
            if len(saved) == 2:
                break
            p.wait_for_timeout(250)
        assert sorted(saved) == ["Player3", "Player4"]
        # follow-up 2026-11-06: "pick", never "leg"; the expired pick is never called started
        p.wait_for_function("(window.__toasts || []).some(t => t.includes('Removed'))", timeout=10000)
        said = " ".join(p.evaluate("window.__toasts"))
        assert "Removed 1 started pick: Player1." in said and "Removed 1 expired pick (added more than 4 days ago): Player2." in said, said
        assert " leg" not in said and "Player2." not in said.split("expired")[0], said
        p.locator("#u-tray-btn").click()
        body = p.locator("#u-tray-body")
        body.wait_for()
        p.wait_for_timeout(600)
        text = body.inner_text()
        assert "Player1" not in text and "Player2" not in text
        assert "A Over" not in text and "Research grade" not in text                    # the saved letter is not repeated
        assert "Over 61.0%" in text and "Lean Over 61.0%" in text
        shot(p, "s5-account-tray")
        assert not p.errors
    finally:
        c.close()


@pytest.mark.skipif(not shutil.which("node"), reason="node missing")
def test_sports_slip_draft_prunes_started_picks():
    code = """const t = require(process.argv[1]); const now = Date.parse('2026-11-05T12:00:00Z');
      const r = t.pruneStarted({legs: [{player: 'Reed Sheppard', start_utc: '2026-10-26T23:00:00Z'}, {player: 'Tre Jones', start_utc: '2026-10-26T23:30:00Z'},
                                       {player: 'Later', start_utc: '2026-11-05T23:00:00Z'}, {player: 'No start'}], entry_type: 'power', payout_key: 'x'}, now);
      const same = t.pruneStarted({legs: [{player: 'Later', start_utc: '2026-11-05T23:00:00Z'}]}, now);
      console.log(JSON.stringify({left: r.draft.legs.map(l => l.player), gone: r.gone.map(l => l.player), entry: r.draft.entry_type, key: r.draft.payout_key || null,
        text: t.goneText(r.gone), unchanged: same.gone.length === 0}));"""
    out = subprocess.run(["node", "-e", code, str(ROOT / "web" / "sports" / "tray.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["left"] == ["Later", "No start"] and d["gone"] == ["Reed Sheppard", "Tre Jones"] and d["entry"] == "power" and d["key"] is None
    assert d["text"] == "Removed 2 started picks: Reed Sheppard, Tre Jones." and d["unchanged"]


def test_sports_tray_drops_a_started_pick_on_the_page(browser, env):
    now = datetime.now(timezone.utc)
    c = context(browser, env, PHONE)
    try:
        p = open_page(c, env, "/sports?view=classic#/board", PHONE)
        ready(p, "sports")
        p.evaluate("""([a, b]) => { localStorage.setItem('ss:draft:nba:prizepicks', JSON.stringify({legs: [
            {record_id: 'r1', player: 'Reed Sheppard', side: 'over', line: 10.5, stat: 'points', start_utc: a, platform: 'prizepicks'},
            {record_id: 'r2', player: 'Later Guy', side: 'over', line: 12.5, stat: 'points', start_utc: b, platform: 'prizepicks'}]}));
            localStorage.setItem('ss:ctx', JSON.stringify({league: 'nba', platform: 'prizepicks'})); }""", [iso(now - timedelta(days=10)), iso(now + timedelta(hours=4))])
        p.evaluate("window.SportsTray.render('nba')")
        assert json.loads(p.evaluate("localStorage.getItem('ss:draft:nba:prizepicks')"))["legs"][0]["player"] == "Later Guy"
        assert "Reed Sheppard" not in p.locator("#sw-tray").inner_text()
        assert "Removed 1 started pick: Reed Sheppard." in p.locator("#sw-tray-live").inner_text()
        p.wait_for_timeout(2300)                                                         # the account tray bar sits on top of the draft bar
        tray, draft = p.locator("#u-tray-btn").bounding_box(), p.locator("#sw-tray").bounding_box()
        assert tray["y"] + tray["height"] <= draft["y"] + 1
        shot(p, "s5-sports-tray")
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ the classic page, built only when shown

def test_the_research_board_page_does_not_carry_the_classic_page(ctx, env):
    p = open_page(ctx, env, "/")
    p.wait_for_selector("#u-hub:not([hidden]) #hub-rows tr", timeout=30000)
    st = p.evaluate("""() => ({board: !!document.getElementById('boardout'), tpl: !!document.getElementById('scout-classic'),
      nodes: document.querySelectorAll('body *').length, text: document.body.innerText.includes('Every angle. One player.')})""")
    assert st["board"] is False and st["tpl"] is True and st["text"] is False, st
    classic = open_page(ctx, env, "/?view=classic#board")
    classic.wait_for_selector("#boardout .brow2.crow", timeout=30000)
    assert classic.evaluate("!document.getElementById('scout-classic')")
    assert classic.evaluate("document.querySelectorAll('body *').length") > st["nodes"]
    assert not p.errors and not classic.errors


def test_a_classic_route_on_the_home_page_builds_it_with_every_upgrade(ctx, env):
    p = open_page(ctx, env, "/")
    p.wait_for_selector("#u-hub:not([hidden]) #hub-rows tr", timeout=30000)
    p.evaluate("location.hash = '#matches'")                                         # a classic route in the same visit
    p.wait_for_selector("#boardout .mgroup[open] .brow2.crow .u-hits", timeout=30000)   # built, and the upgrade's rows are on it
    assert p.locator("#u-hub").is_hidden()
    assert p.locator(".workspace-tools .h-entry").count() == 1                           # the link joined the classic toolbar
    assert not p.errors
    q = open_page(ctx, env, "/#matches")                                               # opened straight on a classic route
    q.wait_for_selector("#boardout .mgroup[open] .brow2.crow .u-hits", timeout=30000)
    assert not q.errors


def test_classic_lazy_off_switch_sends_the_page_built(ctx, env, monkeypatch):
    monkeypatch.setenv("SCOUT_CLASSIC_LAZY", "0")
    p = open_page(ctx, env, "/")
    p.wait_for_selector("#u-hub:not([hidden]) #hub-rows tr", timeout=30000)
    assert p.evaluate("!!document.getElementById('boardout') && !document.getElementById('scout-classic')")


def test_upgrade_page_wraps_only_the_cs2_body():
    body = b'<html lang="en"><head></head><body class="locked"><p id="x">x</p><script>var a=1</script></body></html>'
    out = server.upgrade_page(body, "cs2")
    assert b'<body class="locked"><template id="scout-classic"><p id="x">x</p><script>var a=1</script></template>' in out
    assert out.index(b"</template>") < out.index(b"scoutClassicMount") < out.index(b'/upgrade/upgrade.js')
    assert b"scout-classic" not in server.upgrade_page(body, "lol")


# ------------------------------------------------------------------------------------------------ A13: classic polish

@pytest.mark.parametrize("width", [1180, 1440])
def test_l20_cell_never_breaks_its_tally(browser, env, width):
    size = {"width": width, "height": 900}
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/?view=classic#matches", size)
        p.wait_for_selector("#boardout .u-hits .u-hc b", timeout=30000)
        tall = p.evaluate("""() => { document.querySelectorAll('#boardout .u-hc b').forEach(b => { b.textContent = '10/20'; });
          return [...document.querySelectorAll('#boardout .u-hc b')].filter(b => b.getBoundingClientRect().height > 20).length; }""")
        assert tall == 0
        p.locator("#boardout .mgroup[open] .brow2.crow").first.scroll_into_view_if_needed()
        shot(p, "a13-matches-l20")
    finally:
        c.close()


def test_phone_cs2_header_leaves_top_plays_on_the_first_screen(browser, env):
    c = context(browser, env, PHONE)
    try:
        p = open_page(c, env, "/?view=classic#board", PHONE)
        p.wait_for_selector(".ot-featured .blist > *", timeout=30000)
        p.wait_for_timeout(500)
        card = p.locator(".ot-featured .blist > *").first.bounding_box()
        tray = p.locator("#u-tray-btn").bounding_box()
        assert card["y"] + card["height"] <= tray["y"] + 1, (card, tray)                # the first card is whole above the tray bar
        tools = p.evaluate("[...document.querySelectorAll('.workspace-tools > *, .workspace-tools .tools-nav a')].filter(e => getComputedStyle(e).display !== 'none' && e.tagName !== 'NAV').map(e => Math.round(e.getBoundingClientRect().top))")
        assert len(set(tools)) == 1                                                       # one row of tools, every tool kept
        no_h_scroll(p)
        shot(p, "a13-cs2-header")
        assert not p.errors
    finally:
        c.close()


def test_desktop_cs2_header_unchanged(browser, env):
    c = context(browser, env, DESK)
    try:
        p = open_page(c, env, "/?view=classic#board", DESK)
        p.wait_for_selector(".ot-featured", timeout=30000)
        assert p.evaluate("getComputedStyle(document.getElementById('workspace-title')).fontSize") != "21px"
        shot(p, "a13-cs2-header")
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_slips_page_keeps_the_right_hand_slip(browser, env, size):
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/?view=classic#board", size)
        p.wait_for_selector("#boardout .brow2.crow [data-bslip]", timeout=30000)
        p.evaluate("""() => { const fut = new Date(Date.now() + 3 * 3600e3).toISOString();
          store.set('cs2s_slip', [{p: 'vargo', bk: 'underdog', t: 'kills', sc: '12', l: 30.5, s: 'More', g: 'B', u: 1, pc: 0.6, st: fut, m: 'a-b', vs: 'Bravo', tm: 'Alpha'},
                                  {p: 'orlo', bk: 'underdog', t: 'kills', sc: '12', l: 31.5, s: 'Less', g: 'B', u: 1, pc: 0.6, st: fut, m: 'c-d', vs: 'Delta', tm: 'Charlie'}]); }""")
        p.evaluate("location.hash = '#slips'")
        p.wait_for_function("document.body.dataset.route === 'slips' || document.body.classList.contains('mode-slips')")
        p.evaluate("drawSlip()")
        p.wait_for_timeout(400)
        if size["width"] >= 1180:
            assert p.locator("#slip").is_visible() and "vargo" in p.locator("#slip").inner_text()
            assert p.locator("#myslip").is_hidden()                                       # the same content once, in the panel
            box = p.locator("#slip").bounding_box()
            assert box["x"] > size["width"] / 2                                           # on the right
        else:
            assert p.locator("#myslip").is_visible() and "vargo" in p.locator("#myslip").inner_text()
        no_h_scroll(p)
        shot(p, "a13-slips-page")
        assert not p.errors
    finally:
        c.close()


def test_classic_slip_drops_a_started_pick_with_a_toast(ctx, env):
    p = open_page(ctx, env, "/?view=classic#board")
    p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
    p.evaluate("""() => { const past = new Date(Date.now() - 10 * 864e5).toISOString(), fut = new Date(Date.now() + 3 * 3600e3).toISOString();
      store.set('cs2s_slip', [{p: 'old', bk: 'underdog', t: 'kills', sc: '12', l: 30.5, s: 'More', g: 'B', u: 1, pc: 0.6, st: past},
                              {p: 'new', bk: 'underdog', t: 'kills', sc: '12', l: 31.5, s: 'Less', g: 'B', u: 1, pc: 0.6, st: fut}]); drawSlip(); }""")
    assert p.evaluate("store.get('cs2s_slip', []).map(x => x.p)") == ["new"]
    assert "Removed 1 started pick: old" in p.locator("#toast").inner_text()
    assert not p.errors


def test_classic_slip_prune_has_undo_for_a_fast_device_clock(ctx, env):
    """Follow-up 2026-11-06: the device clock runs 3 h fast, so a pick that starts in 2 h looks started; Undo keeps it for good."""
    p = open_page(ctx, env, "/?view=classic#board")
    p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
    p.evaluate("""() => { const real = Date.now(), soon = new Date(real + 2 * 3600e3).toISOString();
      store.set('cs2s_slip', [{p: 'soon', bk: 'underdog', t: 'kills', sc: '12', l: 30.5, s: 'More', g: 'B', u: 1, pc: 0.6, st: soon}]);
      const fast = real + 3 * 3600e3, base = performance.now(); Date.now = () => fast + (performance.now() - base); drawSlip(); }""")
    assert p.evaluate("store.get('cs2s_slip', []).length") == 0
    assert "Removed 1 started pick: soon" in p.locator("#toast").inner_text()
    p.locator("#toast [data-undo-prune]").click()
    kept = p.evaluate("store.get('cs2s_slip', [])")
    assert [x["p"] for x in kept] == ["soon"] and kept[0]["ku"] == 1
    p.evaluate("drawSlip()")                                                         # never removed by itself again
    assert p.evaluate("store.get('cs2s_slip', []).map(x => x.p)") == ["soon"]
    assert "Kept soon on your slip" in p.locator("#toast").inner_text()
    assert "Started – remove" in p.locator("#slip").inner_text() or p.locator("#slip").is_hidden()   # the kept pick keeps its button
    assert not p.errors


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_classic_prune_toast_with_undo_fits(browser, env, size):
    c = context(browser, env, size)
    try:
        p = open_page(c, env, "/?view=classic#board", size)
        p.wait_for_selector("#boardout .brow2.crow", timeout=30000)
        p.evaluate("""() => { const past = new Date(Date.now() - 3600e3).toISOString();
          store.set('cs2s_slip', [{p: 'old', bk: 'underdog', t: 'kills', sc: '12', l: 30.5, s: 'More', g: 'B', u: 1, pc: 0.6, st: past}]); drawSlip(); }""")
        t = p.locator("#toast")
        assert "Removed 1 started pick: old" in t.inner_text() and t.locator("[data-undo-prune]").is_visible()
        box, vw = t.bounding_box(), p.viewport_size["width"]
        assert box["x"] >= 0 and box["x"] + box["width"] <= vw, (box, vw)
        shot(p, "classic-prune-undo")
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Tools: owner-only parts, Price check default

@pytest.mark.parametrize("size", [PHONE, DESK])
def test_owner_tools_are_invisible_to_a_member(browser, env, monkeypatch, size):
    import workspace_markets
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    member = context(browser, env, size, user="member-2")
    owner = context(browser, env, size)
    try:
        # the server's own answer: a member is not the owner, and the member cut of an owner sheet carries no owner part
        p = member.new_page()
        p.goto(env.url + "/")
        me = p.evaluate("async () => (await (await fetch('/api/upgrade/me')).json())")
        assert me["owner"] is False
        cut = workspace_markets.for_member(json.dumps(market_doc(owner=True)), {"cs2": True, "lol": True, "nba": True, "nfl": True}, owner=False)
        assert cut["owner"] is False and not any(d.get("flagged") for d in cut["sports"].values())
        p.close()
        m = mt_page(member, env, cut)
        m.wait_for_selector("#hub-market-results")
        text = m.locator("#hub-tools-body").inner_text()
        assert "Owner check" not in text and "Grading comparison" not in text and "OWNER REVIEW" not in text
        assert m.locator('[data-hub="grade-review"]').count() == 0 and m.locator(".h-mt-owner").count() == 0
        shot(m, "tools-member")
        o = mt_page(owner, env, market_doc(owner=True))
        o.wait_for_selector("#hub-market-results")
        otext = o.locator("#hub-tools-body").inner_text()
        assert "Owner check" in otext and "Grading comparison" in otext                 # the owner keeps them
        assert not m.errors and not o.errors
    finally:
        member.close()
        owner.close()


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_price_check_opens_on_nba_from_the_cs2_board(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    c = context(browser, env, size)
    try:
        p = mt_page(c, env, market_doc(), path="/#/tools?sport=cs2")
        p.wait_for_selector("#hub-market-results .h-pc-row:not(.h-pc-head)")
        assert p.locator("#hub-mt-league").input_value() == "nba"
        assert "No sportsbook offers" not in p.locator("#hub-market-results").inner_text()
        p.select_option("#hub-mt-league", "all")                                       # the Sport filter still widens it
        p.wait_for_timeout(200)
        assert p.locator("#hub-market-results").inner_text().count("Nfl Back") >= 1
        p.locator("#hub-market-tools").scroll_into_view_if_needed()
        shot(p, "tools-price-check")
        assert not p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ Record calendar header

@pytest.mark.parametrize("size", [PHONE, DESK])
def test_record_calendar_names_its_exact_window(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    days = [{"date": (datetime(2026, 10, 7) + timedelta(days=i)).strftime("%Y-%m-%d"), "wins": 1 if i == 29 else 0, "losses": 0, "pushes": 0,
             "decided": 1 if i == 29 else 0, "hit_rate": 1.0 if i == 29 else None} for i in range(30)]
    doc = {"as_of": "2026-11-06T03:27:00Z", "totals": {"wins": 1, "losses": 0, "pushes": 0, "observed": 1.0, "printed": .6},
           "by_letter": [], "rule": "r", "calendar": {"basis": "Match start date · US Central", "days": days, "state": "ready", "missing_dates": 0}}
    ctx = context(browser, env, size)
    p = ctx.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.route("**/api/upgrade/stated", lambda r: r.fulfill(json=doc))
    p.route("**/api/upgrade/results?*", lambda r: r.fulfill(json=doc))
    p.goto(env.url + "/#scout/record")
    p.wait_for_selector("#hub-record-body .h-record-calendar")
    p.locator("#hub-record-sport").select_option("cs2")
    p.wait_for_function("document.querySelectorAll('.h-record-card').length===1")
    head = p.locator("#hub-record-body .h-record-calendar h3").first.text_content()
    assert head.startswith("Last 30 days") and "Oct 7" in head and "Nov 5" in head and "Nov 6" not in head
    # follow-up 2026-11-06: the cells read like the header ("Oct 7 – Nov 4", "Nov 5"), never "10-07"
    cells = p.locator("#hub-record-body .h-record-calendar time").all_inner_texts()
    assert cells == ["Oct 7 – Nov 4", "Nov 5"], cells
    card = p.locator("#hub-record-body .h-record-card").first.inner_text()
    assert "0 pushes counted apart" in card and "pushes separately" not in card
    p.locator("#hub-record-body .h-record-calendar").first.scroll_into_view_if_needed()
    shot(p, "record-calendar")
    assert not p.errors
    ctx.close()


# ------------------------------------------------------------------------------------------------ public /record: the letters line

@pytest.mark.parametrize("size", [PHONE, DESK])
def test_public_record_states_the_letters_fact_plainly(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    monkeypatch.setenv("SCOUT_RECORD_STATUS", "1")
    c = browser.new_context(viewport=size)
    try:
        p = c.new_page()
        p.goto(env.url + "/record")
        p.wait_for_function("document.body.innerText.includes('letter grades')", timeout=20000)
        text = p.locator("body").inner_text()
        assert "The CS2 board shows letter grades." in text and "letters are live" not in text
        shot(p, "public-record")
    finally:
        c.close()
