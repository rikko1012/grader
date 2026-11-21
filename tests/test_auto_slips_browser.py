"""EV and Arbitrage automatic (owner 2026-11-20): ready slips appear with no press, one tap adds / copies them (rechecked), the
Arbitrage sheet carries a one-tap copy of the best pair, and both screens re-read their prices every minute while on screen.
390 and 1280, v3 on and off, light/dark through v3. Fixture data only."""
import json
import os
from pathlib import Path

import pytest

import workspace_auto as wa_auto
import workspace_ev as we
import workspace_markets as wm
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE  # noqa: F401
from tests.test_hub_shell_browser import market_doc, mt_page

SHOTS = os.environ.get("AUTO_SHOTS")


def _ev_prices(board):
    original = we.shared

    def prices(srv, q, allowed):
        st, body = original(srv, q, allowed)
        d = json.loads(body)
        d["sports"] = ["cs2"]; d["rows"] = []; d["model_only"] = []
        for r in board.values():
            if r["offer"]["platform"] != d["app"] or not r["scout"].get("side"):
                continue
            d["rows"].append(dict(key="|".join((r["sport"], d["app"], r["offer"]["offer_id"])), sport="cs2", player=r["player"]["name"],
                                  team=r["player"]["team"], event=r["event"]["label"], stat_label=r["market"]["stat_label"],
                                  period_label=r["market"]["period_label"], start_utc=r["event"]["start_utc"], side=r["scout"]["side"],
                                  line=r["offer"]["line"], basis="market", ev=.12, p_market=.65, books=2, books_label="2 books",
                                  sources=[dict(kind="sportsbook", age_s=30)], american=-150, be=.58, p_model=r["scout"]["p_side"]))
        d["rows_total"] = len(d["rows"])
        return st, json.dumps(d).encode()
    return prices


def _auto_doc(board, as_of):
    """Two PrizePicks Power 2-pick slips made of current board rows from different matches (the shape workspace_auto sends)."""
    pp = [r for r in board.values() if r["offer"]["platform"] == "prizepicks" and r["scout"].get("side")]
    by_event = {}
    for r in pp:
        by_event.setdefault(r["event"]["id"], r)
    rows = list(by_event.values())[:4]
    leg = lambda r: {"sport": r["sport"], "book": "prizepicks", "row_id": r["offer"]["offer_id"], "side": r["scout"]["side"], "line": r["offer"]["line"],
                     "player": r["player"]["name"], "stat_label": r["market"]["stat_label"], "period_label": r["market"]["period_label"],
                     "event": r["event"]["label"], "start_utc": r["event"]["start_utc"], "be": .577, "books_label": "2 books", "p_market": .65, "ev": .126}
    slips = [{"platform": "prizepicks", "entry_type": "power", "n": 2, "legs": [leg(rows[0]), leg(rows[1])], "min_ev": .126, "mean_ev": .126}]
    if len(rows) >= 4:
        slips.append(dict(slips[0], legs=[leg(rows[2]), leg(rows[3])]))
    entry = {"app": "prizepicks", "app_label": "PrizePicks", "entry": "power", "entry_label": "Power", "n": 2, "break_even": .5774, "american": -137,
             "positive_legs": len(pp), "priced_legs": len(pp), "payout": None, "slips": slips}
    return {"version": "auto-1", "as_of": as_of, "cycle_s": 30, "arb": None,
            "ev": {"state": "ok", "entries": [entry], "sports": ["cs2"], "books": ["BetRivers", "FanDuel"], "priced_legs": len(pp)}}


@pytest.mark.parametrize("v3", ["0", "1"])
@pytest.mark.parametrize("size", [DESK, PHONE])
def test_ready_ev_slips_need_no_press_and_add_in_one_tap(browser, env, monkeypatch, size, v3):
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    monkeypatch.setenv("SCOUT_UI_V3", v3)
    wa_auto.reset()
    c = context(browser, env, size)
    c.grant_permissions(["clipboard-read", "clipboard-write"])
    records = c.request.get(env.url + "/api/upgrade/board?sport=cs2").json()["records"]
    board = {r["key"]: r for r in records}
    monkeypatch.setattr(we, "shared", _ev_prices(board))
    from workspace_books import _iso
    import time
    reads = []

    def serve(srv, allowed, now=None):
        reads.append(time.time())
        return 200, json.dumps(_auto_doc(board, _iso(time.time()))).encode()
    monkeypatch.setattr(wa_auto, "serve", serve)
    try:
        p = c.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        p.clock.install()
        p.goto(env.url + "/#/ev")
        p.wait_for_selector(".ev-ready-card", timeout=30000)                       # nothing pressed
        assert p.locator(".ev-ready-card").count() == len(_auto_doc(board, "")["ev"]["entries"][0]["slips"])
        card = p.locator(".ev-ready-card").first
        assert card.locator("li").count() == 2
        txt = card.inner_text()
        assert "Power 2-pick" in txt and "57.7%" in txt and "Books 65.0%" in txt and "+12.6%" in txt and ("Over" in txt or "Under" in txt)
        assert "updates every minute" in p.locator("#hub-ev-body .ev-refresh").inner_text()
        for word in ("Live", "Scanning", "Auto"):
            assert word not in p.locator(".ev-ready").inner_text().split(), word
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            p.screenshot(path=str(Path(SHOTS) / f"ev-ready-{size['width']}-v3{v3}.png"), full_page=True)
        # one tap: the slip goes to the tray through the normal recheck
        card.locator('[data-ev="auto-add"]').click()
        p.wait_for_function("document.querySelector('.ev-ready-card [role=status]')?.textContent.includes('Added')", timeout=30000)
        # one tap: copy (rechecked)
        card.locator('[data-ev="auto-copy"]').click()
        p.wait_for_function("document.querySelector('.ev-ready-card [role=status]')?.textContent.includes('Rechecked and copied')", timeout=30000)
        copied = p.evaluate("navigator.clipboard.readText()")
        assert copied.startswith("PrizePicks Power · 2 picks") and copied.count("\n") == 2
        # a line that fails recheck is refused, with no copy
        p.route("**/api/upgrade/recheck", lambda r: r.fulfill(status=409, json={"error": "A line changed. Generate again."}))
        card.locator('[data-ev="auto-copy"]').click()
        p.wait_for_function("document.querySelector('.ev-ready-card [role=status]')?.textContent.includes('line changed')", timeout=30000)
        assert p.evaluate("navigator.clipboard.readText()") == copied
        # the page re-reads by itself once a minute while it is on screen
        p.unroute("**/api/upgrade/recheck")
        n = len(reads)
        p.clock.run_for(61000)
        p.wait_for_function(f"true", timeout=2000)
        p.wait_for_timeout(500)
        assert len(reads) > n, (len(reads), n)
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [DESK, PHONE])
def test_ready_ev_empty_state_says_what_was_checked(browser, env, monkeypatch, size):
    monkeypatch.setenv("SCOUT_PICKEM_EV", "1")
    wa_auto.reset()
    c = context(browser, env, size)
    doc = {"version": "auto-1", "as_of": "2099-01-01T00:00:00Z", "arb": None,
           "ev": {"state": "ok", "entries": [{"app": "prizepicks", "entry": "power", "n": 2, "slips": [], "positive_legs": 0}],
                  "books": ["BetRivers", "FanDuel"], "priced_legs": 37}}
    c.route("**/api/upgrade/auto-slips", lambda r: r.fulfill(json=doc))
    try:
        p = open_page(c, env, "/#/ev", size)
        p.wait_for_function("document.querySelector('.ev-ready')?.textContent.includes('Checked 37 priced legs')", timeout=30000)
        t = p.locator(".ev-ready").inner_text()
        assert "BetRivers, FanDuel" in t and "No PrizePicks entry" in t
        assert p.locator(".ev-ready-card").count() == 0
        no_h_scroll(p)
        assert not p.errors
    finally:
        c.close()


@pytest.mark.parametrize("v3", ["0", "1"])
@pytest.mark.parametrize("size", [DESK, PHONE])
def test_arbitrage_one_tap_copy_and_clock(browser, env, monkeypatch, size, v3):
    monkeypatch.setenv("SCOUT_MARKET_TOOLS", "1")
    monkeypatch.setenv("SCOUT_UI_V3", v3)
    doc = market_doc()
    for sp, d in doc["sports"].items():
        for i, r in enumerate(d["ev"] + d["arbitrage"]):
            r.update(leg_id=f"{sp}|event|player{i}|points|full_game|24.5", event_id="event")
    monkeypatch.setattr(wm, "shared", lambda *a: (200, json.dumps(doc).encode()))
    c = context(browser, env, size)
    c.grant_permissions(["clipboard-read", "clipboard-write"])
    try:
        p = mt_page(c, env, doc, path="/#/arb")
        p.wait_for_function("document.querySelector('#hub-mt-clock')?.textContent.includes('updates every minute')", timeout=30000)
        btn = p.get_by_role("button", name="Copy both bets", exact=True)
        btn.click()
        p.wait_for_function("document.querySelector('#hub-price-draft [role=status]')?.textContent.includes('Rechecked and copied')", timeout=30000)
        copied = p.evaluate("navigator.clipboard.readText()")
        assert copied.startswith("Bet 1: Alpha") and "Bet 2: Beta" in copied and "Stake $" in copied
        if SHOTS:
            p.screenshot(path=str(Path(SHOTS) / f"arb-copy-{size['width']}-v3{v3}.png"))
        p.locator("#hub-price-draft form button").first.click()
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()
