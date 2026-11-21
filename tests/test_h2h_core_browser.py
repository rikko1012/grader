"""hc-1 roster-core H2H on the v3 player card, in a real browser against the real Handler (fixture data only): the hub's H2H section
counts the roster meetings (3+ of tonight's opponent's current players faced, under another team name), and the v3 block inside it
names the team then, the players faced, the maps and his number per map vs the line, plus his current core's meetings without him."""
import os
from pathlib import Path

import pytest

import h2h_core
import hltv
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_ui_v3_player_browser import setup, page, open_card, maps_fixture, PHONE, DESK

SHOTS = Path(os.environ.get("H2H_SHOTS") or "")
NAMES = {1: "Maka", 2: "Lucky", 3: "misutaaa", 4: "Kursy", 5: "Graviti"}
CORE = {"team_id": 5991, "lineup": [{"id": k, "name": v} for k, v in NAMES.items()], "names": NAMES,
        "matches": {0: {"side": 100, "team": "3DMAX", "date": "2026-10-28", "faced": [1, 2, 3, 5]},
                    3: {"side": 103, "team": "3DMAX", "date": "2026-10-25", "faced": [1, 2, 3]},
                    5: {"side": 999, "team": "3DMAX", "date": "2026-10-23", "faced": [1, 2, 3]}}}   # 5: they were his teammates
TEAM_CORE = [{"date": "2026-09-14", "team": "MIBR", "opponent": "3DMAX", "hltv_match": 77, "maps_won": 1, "maps_lost": 2,
              "faced": ["Maka", "Lucky", "Graviti"]}]


def with_oids():
    rows = maps_fixture()
    for r in rows:
        i = int(r["match_id"].split("-")[1])
        r["opponent_id"] = 100 + i
        if i in (0, 3):
            r["opponent"] = "3DMAX"                  # the core's team name then (tonight they play as another org)
    return rows


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_card_h2h_counts_roster_meetings_and_names_them(browser, env, monkeypatch, size):
    setup(env, monkeypatch, "dark")
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: with_oids())
    monkeypatch.setattr(h2h_core, "opponent_core", lambda opp: CORE)
    monkeypatch.setattr(h2h_core, "team_core", lambda nick, team, opp: TEAM_CORE)
    c, p = page(browser, env, size, "dark")
    try:
        open_card(p, "cs2", "vargo")
        p.wait_for_selector("#hub-detail .h-h2h .v3p-h2hcore", timeout=20000)
        sec = p.locator("#hub-detail .h-h2h")
        txt = sec.inner_text()
        assert "2/2" in txt.replace(" ", "") or "/2" in txt                          # the hub's tiles count the 2 roster meetings
        core = p.locator("#hub-detail .v3p-h2hcore").inner_text()
        assert "Maka, Lucky, misutaaa, Kursy, Graviti" in core and "3 or more" in core
        rows = p.locator("#hub-detail .v3p-h2hcore table").first.locator("tbody tr")
        assert rows.count() == 2                                                     # the teammates series (5) never counts
        first = rows.nth(0).inner_text()
        assert "3DMAX" in first and "Maka, Lucky, misutaaa, Graviti" in first and "2026-10-28" in first
        assert "Anubis" in first and "21 + 27" in first and "48" in first and ("Over" in first or "Under" in first or "Push" in first)
        assert "de_" not in core and "NaN" not in core and "undefined" not in core
        tc = p.locator("#hub-detail .v3p-h2hcore table").nth(1).inner_text()
        assert "MIBR vs 3DMAX" in tc and "1–2" in tc and "—" in tc                  # his number: a dash (he did not play)
        if SHOTS.parts:
            SHOTS.mkdir(parents=True, exist_ok=True)
            p.locator("#hub-detail .h-h2h").scroll_into_view_if_needed()
            p.wait_for_timeout(300)
            p.locator("#hub-detail .h-h2h").screenshot(path=str(SHOTS / f"h2h-core-{size['width']}.png"))
        over = p.evaluate("[...document.querySelectorAll('#hub-detail .v3p-h2hcore .v3p-tablewrap')].map(w => w.scrollWidth - w.clientWidth)")
        assert over and max(over) <= 1, over                                        # every column on screen, no sideways scroll
        no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_card_without_a_core_shows_the_hub_section_unchanged(browser, env, monkeypatch):
    setup(env, monkeypatch, "dark")
    monkeypatch.setattr(h2h_core, "opponent_core", lambda opp: None)
    c, p = page(browser, env, DESK, "dark")
    try:
        open_card(p, "cs2", "vargo")
        p.wait_for_timeout(800)
        assert p.locator("#hub-detail .v3p-h2hcore").count() == 0
        assert not p.errors, p.errors
    finally:
        c.close()
