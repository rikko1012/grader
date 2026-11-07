"""Audit follow-up 2026-11-06: the audit browser surfaces (board, card, slip column, Slips page, Tools, Record) run as the owner AND
as a member, at 390 and 1440, on the real routes. A member sees no owner-only part, and no API answer the member's page reads
carries one; the owner keeps them. Display only."""
import json

import pytest

import server
from tests.test_audit_board_browser import FLAGS, board, shot, sport
from tests.test_research_hub_browser import book
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE, OWNER  # noqa: F401

MEMBER = "member-2"
OWNER_TEXT = ("OWNER REVIEW", "Grading comparison", "Owner check", "Formulas (owner)", "Owner only", "owner only")
OWNER_SEL = ".h-mt-owner, .h-owner-table, [data-hub=grade-review], .h-mt-formulas"   # (.h-market-tools is the members' Price check too)
OWNER_JSON = ('"dist":', '"flagged":', '"owner": true', '"owner":true', 'OWNER REVIEW')


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS + ("SCOUT_MARKET_TOOLS",):
        monkeypatch.setenv(k, "1")
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))      # who the owner is (SPORTS_UI=1: sports open to all)


def watch_api(p):
    p.api = []

    def keep(r):
        if "/api/" in r.url and r.request.method == "GET":
            try:
                p.api.append((r.url, r.text()))
            except Exception:                    # noqa: BLE001 - a body that is gone (navigation) is not checked
                pass
    p.on("response", keep)


def owner_parts(p):
    text = p.locator("body").inner_text()
    return [t for t in OWNER_TEXT if t in text] + ([OWNER_SEL] if p.locator(OWNER_SEL).count() else [])


@pytest.mark.parametrize("prod", [False, True], ids=["test-flags", "production-flags"])
@pytest.mark.parametrize("size", [PHONE, DESK])
@pytest.mark.parametrize("who", [OWNER, MEMBER])
def test_audit_surfaces_as_owner_and_member(browser, env, size, who, prod, monkeypatch):
    if prod:                                     # the running server's flag state (server.apply_default_flags runs only as __main__)
        for k in server.DEFAULT_ON:
            monkeypatch.setenv(k, "1")
    c = context(browser, env, size, user=who)
    try:
        p = c.new_page()
        p.errors = []
        p.on("pageerror", lambda e: p.errors.append(str(e)))
        watch_api(p)
        p.goto(env.url + "/")
        me = p.evaluate("async () => (await (await fetch('/api/upgrade/me')).json())")
        assert me["owner"] is (who == OWNER)
        p.close()
        p = board(c, env, size)
        watch_api(p)
        seen = {}
        # board + card
        sport(p, "nba")
        seen["board"] = owner_parts(p)
        p.locator("#hub-rows .h-player").first.click()
        p.wait_for_selector("#hub-card:not([hidden]) #hub-detail")
        seen["card"] = owner_parts(p)
        shot(p, f"member-audit-card-{'owner' if who == OWNER else 'member'}")
        p.keyboard.press("Escape")
        # slip column / tray, then the Slips page
        book(p, "underdog")
        p.locator("#hub-rows .h-add:not([disabled]):not(.is-on)").first.click()
        if p.locator("#hub-card").is_visible():
            p.locator("#hub-detail [data-hub=detail-add]:not([disabled])").click()
            p.locator("#hub-detail [data-hub=card-close]").click()
        seen["slip"] = owner_parts(p)
        p.goto(env.url + "/#/slips")
        p.wait_for_selector("#hub-studio-body .u-leg")
        seen["slips"] = owner_parts(p)
        p.goto(env.url + "/#/tools")
        p.wait_for_selector("#hub-tools-body", state="attached")
        p.wait_for_timeout(600)
        seen["tools"] = owner_parts(p)
        p.goto(env.url + "/#scout/record")
        p.wait_for_selector("#hub-record-body", state="attached")
        p.wait_for_timeout(400)
        seen["record"] = owner_parts(p)
        shot(p, f"member-audit-record-{'owner' if who == OWNER else 'member'}")
        no_h_scroll(p)
        if who == MEMBER:
            assert all(not v for v in seen.values()), json.dumps(seen)
            leaks = [(u, m) for u, body in p.api for m in OWNER_JSON if m in body]
            assert not leaks, leaks[:5]
            assert p.api, "the member's page read no API answer"
        else:
            assert any(seen.values()), seen                                    # the owner keeps the owner parts
        assert not p.errors, p.errors
    finally:
        c.close()
