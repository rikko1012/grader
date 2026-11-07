"""Owner site audit 2026-11-06 (5:40-6:05 PM CST), new board UI group, in a real browser against the real Handler with fixture data
(no network: ESPN images are blocked). Display only: no grade, letter, chance, projection or stake changes anywhere.
N6  NBA / NFL Side and Minimum grade filters: Side reads the offered side when a line has no Scout lean; Minimum grade is hidden (and
    not applied) on a tab with no graded line.
N10 the Views pop-over opens inside the window; the save / alert dialog keeps Cancel / Save at button height.
N11 search finds a team by its exact other spellings (the book's own name, maintained aliases) and never joins an academy to its main
    team.
N12 "Show this game's props" shows a removable chip naming the game.
N13 player names are never cut; the toolbar stays one row on desktop; a tab's count equals the board's line count.
N14 "Manage alerts" opens Tools at its Alerts section.
Old links: /sports#/board?lg=nba opens the board on NBA.
Phone: on the classic CS2 page at 390px, "All sports research" is not cut off at the right edge.
Every player, team and number is a TEST FIXTURE. AUDIT_SHOTS=<folder> keeps screenshots."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import server
import workspace_api
from tests.test_market_edge_browser import boards
from tests.test_upgrade_browser import browser, env, context  # noqa: F401
from tests.workspace_fixture import iso, row

SHOTS = Path(os.environ.get("AUDIT_SHOTS") or "")


@pytest.fixture
def live(env, monkeypatch):
    """The production flags (server.DEFAULT_ON) and the new board (SCOUT_WORKSPACE_V2); extra books off (they need the network)."""
    for k in server.DEFAULT_ON:
        monkeypatch.setenv(k, "1")
    for k in ("SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_WORKSPACE_V2", "1")
    env.sports.boards.update(boards())
    yield env


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(250)
        p.screenshot(path=str(SHOTS / f"{name}-{p.viewport_size['width']}.png"))


def page(browser, env, width=1440, path="/#all-sports", height=900):
    c = context(browser, env, {"width": width, "height": height})
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.ctx = c
    p.goto(env.url + path)
    return p


def hub(browser, env, width=1440, path="/#all-sports"):
    p = page(browser, env, width, path)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    if path == "/#all-sports":                                           # every sport read (a sport link reads its own board first)
        p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL') && !document.querySelector('#hub-sports .h-n-wait')", timeout=30000)
    return p


def pick(p, s):
    p.locator(f"#hub-sports [data-id={s}]").click()
    if s != "all":
        p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{s}|'))", timeout=20000)


def lines_shown(p):
    """the line count over the list ("3 players · 6 lines")"""
    t = p.locator("#hub-count").inner_text()
    return int(t.split("·")[1].split()[0].replace(",", "")) if "·" in t else 0


def tab_count(p, s):
    return int(p.locator(f"#hub-sports [data-id={s}] span").inner_text())


def expected(p, sport, side):
    """the board's own records for one sport: current lines listed under `side` (Scout's lean, else each side the book offers)"""
    return p.evaluate("""async ([s, side]) => { const d = await (await fetch('/api/upgrade/board?sport=' + s, {credentials: 'same-origin'})).json();
      const cur = d.records.filter(r => ['fresh','ok','ready','live'].includes(r.state.kind) && Date.parse(r.event.start_utc) > Date.now());
      return {n: cur.filter(r => r.scout.side ? r.scout.side === side : r.offer[side].offered).length,
              nolean: cur.filter(r => !r.scout.side && r.offer[side].offered).length,
              graded: cur.some(r => ['B-','B','B+','A-','A','A+'].includes(r.scout.grade) && !r.scout.hold)}; }""", [sport, side])


# ------------------------------------------------------------------------------------------------ N6: Side / Minimum grade
def test_side_filter_lists_offered_sides_when_there_is_no_lean_and_grade_filter_hides_without_grades(browser, live):
    p = hub(browser, live)
    try:
        pick(p, "nfl")
        want = expected(p, "nfl", "under")
        assert want["nolean"] > 0 and not want["graded"]                  # the audit's case: NFL lines with no lean and no letter
        p.locator("#hub-filters-toggle").click()
        assert p.locator("#hub-minGrade").is_hidden()                     # no letters on this tab: no Minimum grade control
        p.locator("#hub-side").select_option("under")
        p.wait_for_timeout(150)
        assert lines_shown(p) == want["n"] > 0                            # was 0 lines with no explanation
        assert p.locator("#hub-empty").is_hidden()
        shot(p, "n6-nfl-under")
        # NBA: lines with a lean keep their lean; lines without one are listed under each side their book offers
        pick(p, "nba")
        for side in ("under", "over"):
            if not p.locator("#hub-side").is_visible():
                p.locator("#hub-filters-toggle").click()
            p.locator("#hub-side").select_option(side)
            p.wait_for_timeout(150)
            assert lines_shown(p) == expected(p, "nba", side)["n"], side
        assert expected(p, "nba", "under")["nolean"] > 0
        # Minimum grade set on a graded tab is not applied on a tab with no letters (it stays saved for the graded tabs)
        p.locator("#hub-side").select_option("all")
        pick(p, "lol")
        if not p.locator("#hub-minGrade").is_visible():
            p.locator("#hub-filters-toggle").click()
        assert p.locator("#hub-minGrade").is_visible()
        p.locator("#hub-minGrade").select_option("A-")
        p.wait_for_timeout(150)
        assert lines_shown(p) == tab_count(p, "lol") > 0                 # the A- lines only
        assert p.locator("#hub-filter-n").inner_text() == "1"
        pick(p, "nfl")
        assert lines_shown(p) == 3 and p.locator("#hub-filter-n").inner_text() == ""
        assert p.locator("#hub-minGrade").is_hidden()
        # an alert saved here saves what this tab applies: no hidden minimum grade that would match none of its lines
        p.locator("#hub-views-toggle").click()
        p.locator("#hub-views-pop [data-hub=save-alert]").click()
        p.locator("#hub-save-name").fill("NFL board")
        p.locator("#hub-save-confirm").click()
        saved = None
        for _ in range(50):
            saved = p.evaluate("""async () => { const d = await (await fetch('/api/upgrade/state', {credentials: 'same-origin'})).json();
              return ((d.prefs && d.prefs.researchHub && d.prefs.researchHub.alerts) || [])[0]?.filters || null; }""")
            if saved:
                break
            p.wait_for_timeout(200)
        assert saved["sport"] == "nfl" and saved["minGrade"] == "all", saved
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ N10: Views menu, dialog
@pytest.mark.parametrize("width", [1101, 1180, 1280, 1440, 390])
def test_views_menu_opens_inside_the_window(browser, live, width):
    p = hub(browser, live, width)
    try:
        p.locator("#hub-views-toggle").click()
        p.wait_for_selector("#hub-views-pop:not([hidden])")
        r = p.evaluate("(() => { const b = document.querySelector('#hub-views-pop').getBoundingClientRect(); return [b.left, b.right, document.documentElement.clientWidth]; })()")
        assert r[0] >= 7.5 and r[1] <= r[2] - 7.5, r                     # was left -10 at 1180 ("ed views")
        lab = p.locator("#hub-views-pop label").evaluate("e => e.getBoundingClientRect().left")
        assert lab >= 0
        p.locator("#hub-views-pop [data-hub=save-view]").click()         # every item is reachable (not under the sidebar)
        p.wait_for_selector("#hub-save-dialog[open]")
        assert not p.errors
    finally:
        p.ctx.close()


@pytest.mark.parametrize("path", ["/#all-sports", "/sports#/board?lg=nba", "/lol"])
def test_save_and_alert_dialog_buttons_keep_button_height(browser, live, path):
    p = hub(browser, live, 1440, path)
    try:
        p.locator("#hub-views-toggle").click()
        p.locator("#hub-views-pop [data-hub=save-alert]").click()
        p.wait_for_selector("#hub-save-dialog[open]")
        hs = p.evaluate("[...document.querySelectorAll('#hub-save-dialog form > div > button')].map(b => Math.round(b.getBoundingClientRect().height))")
        assert len(hs) == 2 and all(38 <= h <= 48 for h in hs), hs      # was 96px tall on the CS2 page
        name, buttons = p.evaluate("""[document.querySelector('#hub-save-name').getBoundingClientRect(), document.querySelector('#hub-save-dialog form > div').getBoundingClientRect()]
          .map(r => ({top: r.top, bottom: r.bottom}))""")
        assert buttons["top"] >= name["bottom"]                           # one column: the buttons sit under the name field
        shot(p, "n10-alert-dialog")
        p.locator("#hub-save-dialog button[value=cancel]").click()
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ N11: search by short names
def test_record_carries_exact_team_spellings_for_search_only():
    base = row("33333333-0000-4000-8000-000000000001", "underdog", "kyousuke", "NAVI", "MongolZ", 20.5, "More", 0.6, 950,
               iso(datetime.now(timezone.utc) + timedelta(hours=3)))
    base.update(team_name="Natus Vincere", opp_name="The MongolZ")
    rec = workspace_api.record_of(base)
    assert rec["player"]["team"] == "Natus Vincere" and rec["player"]["opponent"] == "The MongolZ"
    assert set(rec["player"]["aka"]) == {"NAVI", "MongolZ"}                # the book's own names for the same two teams
    nip = row("33333333-0000-4000-8000-000000000002", "underdog", "a", "Ninjas in Pyjamas", "NAVI Junior", 20.5, "More", 0.6, 951,
              iso(datetime.now(timezone.utc) + timedelta(hours=3)))
    rec = workspace_api.record_of(nip)
    assert rec["player"]["aka"] == ["NIP"]                                # team_aliases.json: NIP = Ninjas in Pyjamas
    assert not any("natus" in a.lower() for a in rec["player"]["aka"])  # NAVI Junior is never Natus Vincere
    plain = row("33333333-0000-4000-8000-000000000003", "underdog", "b", "Halcyon", "Norrland", 20.5, "More", 0.6, 952,
                iso(datetime.now(timezone.utc) + timedelta(hours=3)))
    assert "aka" not in workspace_api.record_of(plain)["player"]           # nothing to add: the record is unchanged
    # search text only: the grade, chance, side, stake and revisions are the same with or without the alias
    a, b = workspace_api.record_of(base), workspace_api.record_of(dict(base, team="Natus Vincere", opponent="The MongolZ"))
    assert a["scout"] == b["scout"] and a["offer"] == b["offer"] and a["key"] == b["key"]


def test_search_finds_a_team_by_its_short_name_and_never_an_academy_by_its_main_team(browser, live):
    s = iso(datetime.now(timezone.utc) + timedelta(hours=4))
    navi = row("33333333-0000-4000-8000-000000000011", "underdog", "kyousuke", "NAVI", "Halcyon", 20.5, "More", 0.61, 960, s)
    navi.update(team_name="Natus Vincere")
    junior = row("33333333-0000-4000-8000-000000000012", "underdog", "jrplayer", "NAVI Junior", "Vanta", 18.5, "More", 0.6, 961, s)
    live.rows += [navi, junior]
    p = hub(browser, live)
    try:
        def found(q):
            p.locator("#hub-q").fill(q)
            p.wait_for_timeout(150)
            return set(p.evaluate("[...document.querySelectorAll('#hub-rows .h-player b')].map(b => b.textContent)"))
        assert "kyousuke" in found("NAVI")                                 # was: nothing (the board shows "Natus Vincere")
        assert "kyousuke" in found("navi") and "kyousuke" in found("Natus")
        assert found("Natus") == {"kyousuke"}                             # the academy never matches its main team's name
        assert "jrplayer" in found("NAVI Junior") and "kyousuke" not in found("NAVI Junior")
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ N12: the game chip
def test_show_this_games_props_shows_a_removable_chip_naming_the_game(browser, live):
    p = hub(browser, live)
    try:
        pick(p, "nba")
        p.locator("#hub-board-section [data-hub=view][data-id=games]").click()
        p.wait_for_selector("#hub-games-body .h-game-card")
        label = p.locator("#hub-games-body .h-game-card h2").first.inner_text()
        p.locator("#hub-games-body [data-hub=game-board]").first.click()
        chip = p.locator("#hub-chips .h-chip")
        chip.wait_for()
        assert chip.inner_text().split("\n")[0].strip() == f"Game: {label}"
        assert chip.get_attribute("aria-label") == f"Game: {label}. Remove this filter"
        shot(p, "n12-game-chip")
        n_one = lines_shown(p)
        chip.click()
        assert p.locator("#hub-chips .h-chip").count() == 0 and p.locator("#hub-filter-n").inner_text() == ""
        assert lines_shown(p) > n_one
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ N13: names, toolbar, counts
@pytest.mark.parametrize("width", [1101, 1180, 1280, 1366, 1440, 1536])
def test_player_names_are_never_cut_and_the_toolbar_controls_share_one_row(browser, live, width):
    p = hub(browser, live, width)
    try:
        cut = p.evaluate("""[...document.querySelectorAll('#hub-rows .h-player b')].filter(b => b.scrollWidth > b.clientWidth + 1
          || getComputedStyle(b).textOverflow === 'ellipsis' && getComputedStyle(b).whiteSpace === 'nowrap').map(b => b.textContent)""")
        assert cut == [], cut                                             # was "Karl And..." at 1440 and no name at all at 1101
        col = p.evaluate("Math.round(document.querySelector('#hub-table thead th.h-c-player').getBoundingClientRect().width)")
        assert col >= 110, col
        tops = p.evaluate("""(() => { const t = s => { const r = document.querySelector(s).getBoundingClientRect(); return Math.round((r.top + r.bottom) / 2); };
          return {search: t('#u-hub .h-toolbar .h-search'), views: t('#hub-views-toggle'), refresh: t('#u-hub .h-toolbar .h-refresh'),
                  filters: t('#hub-filters-toggle'), tabs: t('#hub-sports')}; })()""")
        assert tops["search"] == tops["views"] == tops["refresh"] == tops["filters"], tops   # no control drops alone to a row of its own
        if width >= 1280:
            assert abs(tops["tabs"] - tops["views"]) <= 4, tops            # one toolbar row on desktop
        plat = p.evaluate("(() => { const g = document.querySelector('#hub-platform'); return [g.clientWidth, g.scrollWidth]; })()")
        assert plat[0] >= plat[1] - 1, plat                                # All / PP / UD all on screen
        shot(p, "n13-board")
        assert not p.errors
    finally:
        p.ctx.close()


def test_tab_counts_equal_the_lines_each_tab_lists(browser, live):
    held = next(r for r in live.rows if r["book"] == "underdog" and r["nick"] == "raiko")
    held["hltv_retry"] = True                                             # a held line is left off the board (never labelled)
    p = hub(browser, live)
    try:
        for s in ("all", "cs2", "lol", "nba", "nfl"):
            pick(p, s)
            assert tab_count(p, s) == lines_shown(p), (s, tab_count(p, s), lines_shown(p))   # was CS2 190 vs 148 lines
        pick(p, "cs2")
        p.locator("#hub-q").fill("vargo")
        p.wait_for_timeout(150)
        assert tab_count(p, "cs2") == lines_shown(p) > 0
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ N14: Manage alerts
def test_manage_alerts_opens_tools_at_the_alerts_section(browser, live):
    p = hub(browser, live)
    try:
        p.locator("#hub-views-toggle").click()
        p.locator("#hub-views-pop [data-hub=manage-alerts]").click()
        p.wait_for_selector("#hub-tools-section:not([hidden]) #hub-tools-alerts h3")
        p.wait_for_function("document.querySelector('#hub-market-results') && !document.querySelector('#hub-market-results').textContent.includes('Loading')", timeout=20000)
        p.wait_for_timeout(400)
        box = p.evaluate("(() => { const r = document.querySelector('#hub-tools-alerts').getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; })()")
        assert 0 <= box[0] and box[1] <= box[2] + 1, box                   # the Alerts section is on screen (it opened at the top of Tools)
        assert p.evaluate("document.activeElement.closest('#hub-tools-alerts') !== null")
        assert "/tools" in p.evaluate("location.hash")
        shot(p, "n14-manage-alerts")
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ old links keep the sport
@pytest.mark.parametrize("v2", [True, False])
@pytest.mark.parametrize("path,sport", [("/sports#/board?lg=nba", "nba"), ("/sports#/board?lg=nfl", "nfl"), ("/sports#/board", "nba"),
                                        ("/#scout/board?sport=nfl", "nfl")])
def test_old_sports_links_open_the_board_on_their_sport(browser, live, monkeypatch, v2, path, sport):
    """the classic sports page rewrites any /sports board hash to its own ?lg= form before the board reads it, so the old lg= is the
    sport a /sports link carries; a #scout/ link (alert deep links) names it with sport=, which still wins"""
    if not v2:
        monkeypatch.delenv("SCOUT_WORKSPACE_V2", raising=False)
    p = hub(browser, live, 1440, path)
    try:
        assert p.locator(f"#hub-sports [data-id={sport}]").get_attribute("aria-pressed") == "true", p.locator("#hub-sports").inner_text()
        assert all(x.startswith(sport + "|") for x in p.locator("#hub-rows tr[data-record]").evaluate_all("rs => rs.map(r => r.dataset.record)"))
        assert not p.errors
    finally:
        p.ctx.close()


# ------------------------------------------------------------------------------------------------ phone: classic CS2 tools row
@pytest.mark.parametrize("path", ["/?view=classic#slips", "/?view=classic#board"])
@pytest.mark.parametrize("width", [390, 360])
def test_all_sports_research_link_is_whole_on_the_classic_cs2_phone_page(browser, live, path, width):
    p = page(browser, live, width, path, 844)
    try:
        p.wait_for_selector(".workspace-tools .h-entry", timeout=20000)
        p.wait_for_timeout(300)
        m = p.evaluate("""(() => { const e = document.querySelector('.workspace-tools .h-entry'), t = e.parentElement, a = e.getBoundingClientRect(), b = t.getBoundingClientRect();
          return {right: a.right, box: b.left + t.clientWidth, vw: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth,
                  textCut: e.scrollWidth > e.clientWidth + 1, h: a.height}; })()""")
        assert m["right"] <= m["box"] + 0.5 and m["right"] <= m["vw"], m   # was 5px past the row's edge at 390
        assert not m["textCut"] and m["h"] <= 56 and m["sw"] <= m["vw"], m
        shot(p, "phone-classic-entry")
        assert not p.errors
    finally:
        p.ctx.close()
