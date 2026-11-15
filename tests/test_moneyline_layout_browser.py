"""Regression coverage for member Moneylines rows and the periodic board refresh."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_upgrade_browser import DESK, PHONE, browser, context, env, no_h_scroll, open_page


@pytest.mark.parametrize("size", [PHONE, DESK])
def test_moneyline_pick_stays_readable_through_refresh(browser, env, size, monkeypatch):
    monkeypatch.setenv("UI_NEXT", "1")
    now = datetime.now(timezone.utc)
    teams = ["New England Patriots", "Detroit Lions"]
    game = {"id": "game-1", "start": (now + timedelta(hours=2)).isoformat(), "seen_at": now.isoformat(),
            "teams": teams, "quotes": [{"source": "DraftKings", "kind": "american", "prices": [125, -140]}],
            "pick": {"team": teams[1], "basis": "Market and matchup", "factors": [
                {"label": "DraftKings", "team": teams[1], "weight": 0.67},
                {"label": "Season record", "team": teams[0], "weight": 0.33}]}}
    with context(browser, env, size, user="member") as c:
        def games(route):
            league = parse_qs(urlsplit(route.request.url).query)["league"][0]
            older = dict(game, seen_at=(now - timedelta(hours=2)).isoformat())
            rows = {"nfl": [game], "nba": [older], "lol": []}
            route.fulfill(json={"league": league, "observed_at": now.isoformat(), "games": rows[league]})
        c.route("**/api/moneyline-games?*", games)
        match = {"match_id": "hltv:1", "teams": {"a": "Alpha", "b": "Bravo", "names_by_platform": {}},
                 "start": game["start"], "event": "Cup", "platforms": {"polymarket": {
                     "available": True, "usable": True, "prob_a": 0.6, "team_a_name": "Alpha",
                     "bid": 0.59, "ask": 0.61, "received_ts": now.isoformat()}},
                 "research_pick": {"team": "Alpha", "basis": "Market only", "factors": []}}
        c.route("**/api/moneylines", lambda r: r.fulfill(json={"matches": [match]}))
        p = open_page(c, env, "/?view=classic#moneylines", size)
        p.locator("[data-mlsport=nfl]").click()
        row = p.locator(".ml-game").first
        summary = row.locator(".ml-pick > summary")
        summary.focus()
        p.keyboard.press("Enter")
        assert row.locator(".ml-pick").get_attribute("open") is not None
        # Every line of the expanded explanation must fit inside its own container.
        info = row.locator(".ml-pick").locator("..")
        fit = info.evaluate("(e) => ({height:e.clientHeight, content:e.scrollHeight})")
        row.evaluate("e => e.scrollIntoView({block:'center',behavior:'instant'})")
        shots = os.environ.get("MONEYLINE_SHOTS")
        if shots:
            directory = Path(shots)
            directory.mkdir(exist_ok=True, parents=True)
            row.screenshot(path=str(directory / f"nfl-{size['width']}.png"))
        assert fit["content"] <= fit["height"] + 1, fit
        p.evaluate("drawMoneylines()")
        assert row.locator(".ml-pick").get_attribute("open") is not None
        assert summary.evaluate("e => document.activeElement === e")
        assert "+125" in row.inner_text() and "-140" in row.inner_text()
        no_h_scroll(p)
        p.locator("[data-mlsport=cs2]").click()
        prices = p.locator(".mlrow .ml-compare")
        assert prices.locator("thead th").all_text_contents() == ["Source", "Alpha", "Bravo"]
        assert prices.locator("tbody td").all_text_contents() == ["60.0%", "40.0%"]
        p.locator(".mlmore > summary").click()
        p.evaluate("drawMoneylines()")
        assert p.locator(".mlmore").get_attribute("open") is not None
        p.locator("[data-mlsport=nba]").click()
        assert not row.count()
        p.locator("#mlolder").check()
        assert "At last quote" in row.inner_text()
        p.locator("[data-mlsport=lol]").click()
        assert not row.count()
        assert "No current pre-game prices" in p.locator("#boardout").inner_text()
        assert not p.errors
