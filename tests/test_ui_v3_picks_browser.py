"""v3 group C (web/UI_V3.md) in a real browser against the real Handler with fixture data only, SCOUT_UI_V3=1: My picks, the
member Record, the public /record, EV (Best prices), Arbitrage / price check, Sportsbooks game lines and line-move chips, each at
390x844 and 1280x900, dark and light. Every field the page shows today is still there (the v3 files only restyle and add derived
items computed from the numbers beside them), no sideways scroll, no script error. V3_SHOTS=<folder> keeps a screenshot each."""
import os
from pathlib import Path

import pytest

import workspace_api
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401 (fixtures)
from tests.test_ui_v3_browser import v3_page, PHONE, DESK
from tests.workspace_fixture import leg_of

SHOTS = Path(os.environ.get("V3_SHOTS") or "")
SIZES = [PHONE, DESK]
THEMES = ["dark", "light"]


def shot(p, name, full=False):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(350)
        p.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def api(p, method, path, body=None):
    return p.evaluate("""async ([m, path, body]) => { const r = await fetch('/api/upgrade/' + path, {method: m, headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : undefined}); return [r.status, await r.json()]; }""", [method, path, body])


def seed_picks(p, env):
    """Two tracked slips through the real track route: hit, miss, push, void and two pending receipts (picklog settles, as in prod)."""
    st, doc = api(p, "GET", "board")
    assert st == 200
    r = {(x["player"]["name"], x["offer"]["platform"]): x for x in doc["records"]}
    ud = [leg_of(r[(n, "underdog")]) for n in ("vargo", "orlo", "brandr", "raiko")]
    st, out = api(p, "POST", "track", {"idem": "idem-v3c-000001", "entry_key": "underdog", "entry_type": "standard", "legs": ud})
    assert st == 200, out
    pp = [leg_of(r[(n, "prizepicks")]) for n in ("vargo", "orlo")]
    st, out = api(p, "POST", "track", {"idem": "idem-v3c-000002", "entry_key": "prizepicks", "entry_type": "power", "legs": pp})
    assert st == 200, {k: v for k, v in out.items() if k not in ("legs",)}
    env.settle(env.by("vargo"), actual=41.0)                    # Over 38.5: hit
    env.settle(env.by("orlo"), actual=30.0)                     # Over 33.5: miss
    env.settle(env.by("brandr"), status="void")                 # void
    env.settle(env.by("raiko"), status="push", actual=36.5)    # push (the PrizePicks pair stays pending)
    workspace_api.store().settle_once()


def picks_page(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "board", size, theme)
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    seed_picks(p, env)
    p.goto(env.url + "/#/picks")
    p.wait_for_selector("#hub-picks-body .u-pick", timeout=20000)
    p.wait_for_selector("#hub-picks-section .v3-score .v3-tile", timeout=10000)
    return c, p


# ------------------------------------------------------------------------------------------------ My picks
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_my_picks_v3_keeps_every_field(browser, env, monkeypatch, size, theme):
    c, p = picks_page(browser, env, monkeypatch, size, theme)
    try:
        sec = "#hub-picks-section"
        # today's fields: the filters (both selects, unchanged), the summary line, slip groups, each receipt with status + final
        assert p.locator("#hub-pick-sport").count() == 1 and p.locator("#hub-pick-status").count() == 1
        summary = p.locator(f"{sec} .h-pick-summary").inner_text()
        assert "1–1" in summary and "won–lost" in summary and "void" in summary and "pending" in summary
        assert p.locator(f"{sec} .h-picks-group").count() >= 2
        assert p.locator(f"{sec} .u-pick").count() == 6
        sts = " ".join(p.locator(f"{sec} .u-st").all_inner_texts())
        for word in ("Hit", "Miss", "Push", "Void", "Pending"):
            assert word in sts, word
        assert "Pending" in p.locator(f"{sec} .h-picks-part h3").first.inner_text()
        assert p.locator(f"{sec} .h-picks-foot [data-hub=section][data-id=record]").count() == 1
        # v3: the server's own record (the tray's Record tab numbers): tiles, ticks and key reconcile with each other
        tiles = p.locator(f"{sec} .v3-score .v3-tile b").all_inner_texts()
        assert tiles[0] == "50.0%" and tiles[1].replace("–", "-").replace(" ", "") == "1-1-1" and tiles[2] == "1" and tiles[3] == "2"
        assert "Hit rate, 2 decided" in p.locator(f"{sec} .v3-score").inner_text()
        assert p.locator(f"{sec} .v3-ticks i").count() == 6
        # chance you saw, from the saved receipt, beside each status (never a 0 for a missing one)
        saw = p.locator(f"{sec} .u-pick .v3-saw").all_inner_texts()
        assert len(saw) == 6 and all(s.startswith("Chance you saw ") for s in saw)
        assert not any(s.endswith(" 0%") or s.endswith(" 0.0%") for s in saw)
        # pills mirror the selects and press them
        pills = p.locator(f"{sec} .v3-pills[data-for=hub-pick-status] button")
        assert [t.strip() for t in pills.all_inner_texts()] == ["All outcomes", "Pending", "Won", "Lost", "Pushed", "Void"]
        p.locator(f"{sec} .v3-pills[data-for=hub-pick-status] button", has_text="Won").click()
        p.wait_for_function("document.getElementById('hub-pick-status').value==='hit'")
        p.wait_for_function("document.querySelectorAll('#hub-picks-body .u-pick').length===1")
        assert p.locator(f"{sec} .v3-pills[data-for=hub-pick-status] button[aria-pressed=true]").inner_text().strip() == "Won"
        p.locator(f"{sec} .v3-pills[data-for=hub-pick-status] button", has_text="All outcomes").click()
        p.wait_for_function("document.querySelectorAll('#hub-picks-body .u-pick').length===6")
        # an open receipt keeps its full field list
        p.locator(f"{sec} .u-pick button[data-u=pick]").first.click()
        p.wait_for_selector(f"{sec} .u-pick-body .u-kv")
        kv = p.locator(f"{sec} .u-pick-body .u-kv").inner_text()
        for k in ("Chance you saw", "Scout read", "Projection", "Book", "Tracked", "Receipt"):
            assert k in kv, k
        assert p.locator(f"{sec} .u-pick-body .u-time li").count() >= 1
        # the light / dark ground is the v3 one, and the page clears the phone bottom bar
        bg = p.evaluate("getComputedStyle(document.getElementById('hub-center')).backgroundColor")
        assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")
        no_h_scroll(p)
        shot(p, f"picks-{size['width']}-{theme}")
        if size is PHONE:
            p.evaluate("document.getElementById('hub-center').scrollTop=1e6; window.scrollTo(0,1e6)")
            p.wait_for_timeout(200)
            foot = p.locator(f"{sec} .h-picks-foot").bounding_box()
            bar = p.locator(".sc-v3-tabs").bounding_box()
            assert foot["y"] + foot["height"] <= bar["y"] + 1
            shot(p, f"picks-{size['width']}-{theme}-end")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_my_picks_v3_empty_state_keeps_todays_words(browser, env, monkeypatch):
    c, p = v3_page(browser, env, monkeypatch, "picks", PHONE, "dark")
    try:
        p.wait_for_selector("#hub-picks-body .h-empty")
        t = p.locator("#hub-picks-body .h-empty").inner_text()
        assert "No tracked picks yet" in t and "Find a player prop" in t
        assert p.locator("#hub-picks-section .v3-score").count() == 0
        shot(p, "picks-empty-390-dark")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ member Record (hub)
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_member_record_v3_keeps_every_field(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "record-hub", size, theme)
    try:
        p.wait_for_selector("#hub-record-body .h-record-card .h-score", timeout=30000)
        card = p.locator("#hub-record-body .h-record-card").first
        txt = card.inner_text()
        for k in ("Wins / losses", "Actual hit rate", "Average stated chance", "decided picks", "Updated"):
            assert k in txt, k
        assert p.locator("#hub-record-sport").count() == 1
        assert card.locator(".h-rel-row").count() >= 1 and card.locator(".h-record-calendar").count() == 1
        # v3: the delta is the two numbers shown beside it, never computed from anything else
        nums = p.evaluate("""() => { const c = document.querySelector('#hub-record-body .h-record-card'), b = [...c.querySelectorAll('.h-score > div > b')].map(x => x.textContent);
          return [b[1], b[2], (c.querySelector('.v3-delta') || {}).textContent || ''] }""")
        obs, pr, delta = float(nums[0].rstrip("%")), float(nums[1].rstrip("%")), nums[2]
        want = round(obs - pr, 1)
        assert ("pts vs stated" in delta) and (f"{abs(want):.1f}" in delta)
        # the pill sport switch presses the select
        assert p.locator("#hub-record-section .v3-pills[data-for=hub-record-sport] button").count() >= 2
        bg = p.evaluate("getComputedStyle(document.getElementById('hub-center')).backgroundColor")
        assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")
        no_h_scroll(p)
        shot(p, f"record-hub-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ public /record
@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("size", SIZES, ids=["390", "1280"])
def test_public_record_v3_keeps_every_field(browser, env, monkeypatch, size, theme):
    c, p = v3_page(browser, env, monkeypatch, "record", size, theme)
    try:
        p.wait_for_selector("#record h2", timeout=30000)
        assert p.locator("a.skip").count() == 1
        for b in ("CS2", "LoL", "NBA", "NFL"):
            assert p.locator(f"nav[aria-label='Record sport'] button", has_text=b).count() == 1
        assert p.locator("main > header a[href='/reviews']").count() == 1 and p.locator("main > header a[href='/']").count() >= 1
        txt = p.locator("#record").inner_text()
        for k in ("Wins–losses", "Actual hit rate", "Average stated chance", "How this record is counted", "Stated vs actual, by tier", "Snapshot"):
            assert k in txt, k
        assert p.locator("main > footer").inner_text().startswith("Forecasts were saved before their games")
        assert p.evaluate("getComputedStyle(document.body).fontFamily").startswith("Outfit")
        bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
        assert bg == ("rgb(246, 247, 249)" if theme == "light" else "rgb(8, 11, 19)")
        # sport switch still works (record.js's own buttons)
        p.locator("nav[aria-label='Record sport'] button", has_text="LoL").click()
        p.wait_for_function("document.querySelector('#record h2') && document.querySelector('#record h2').textContent==='LoL'")
        p.locator("nav[aria-label='Record sport'] button", has_text="CS2").click()
        p.wait_for_function("document.querySelector('#record h2').textContent==='CS2'")
        no_h_scroll(p)
        shot(p, f"record-public-{size['width']}-{theme}", full=True)
        assert not p.errors, p.errors
    finally:
        c.close()
