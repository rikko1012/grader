"""Screenshots of every hub screen, current design and UI_NEXT, light and dark, desktop and phone (UI_NEXT_SHOTS=<folder>).
Fixture data only. Skipped unless the folder is named."""
import os
from pathlib import Path

import pytest
import workspace_api
from tests.test_upgrade_browser import browser, env, context, open_page  # noqa: F401
from tests.test_ui_next_browser import ladder_board

OUT = os.environ.get("UI_NEXT_SHOTS")
ONLY = [x for x in os.environ.get("UI_NEXT_SHOTS_ONLY", "").split(",") if x]
pytestmark = pytest.mark.skipif(not OUT, reason="UI_NEXT_SHOTS not set")
SIZES = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}
SCREENS = [("board-all", "/#all-sports"), ("board-cs2", "/"), ("board-lol", "/lol"), ("board-nba", "/sports"), ("board-nfl", "/sports#/board?lg=nfl"),
           ("slips", "/#slips"), ("games", "/#/games"), ("tools", "/#/tools"), ("picks", "/#/picks"), ("record", "/#/record"), ("ev", "/sports#/ev"), ("arb", "/sports#/arb")]


def variants():
    v = [] if os.environ.get("UI_NEXT_SHOTS_OLD") == "0" else [("old", None, "dark")]
    if os.environ.get("UI_NEXT_SHOTS_NEW", "1") == "1":
        v += [("new", "next", "dark"), ("new", "next", "light")]
    return v


@pytest.mark.parametrize("size", list(SIZES))
def test_shots(browser, env, size, monkeypatch):
    for k in ("SCOUT_MARKET_TOOLS", "SCOUT_PICKEM_EV", "SCOUT_PARITY_EDGE", "SCOUT_MARKET_EDGE", "SCOUT_BOARD_H2H", "SCOUT_WORKSPACE_V2"):
        monkeypatch.setenv(k, "1")                      # the flags the live site runs with
    out = Path(OUT)
    out.mkdir(parents=True, exist_ok=True)
    env.sports.boards["nba"] = ladder_board()           # a prop the way the books list it: 15 lines over 6 apps
    workspace_api.reset()
    for tag, ui, theme in variants():
        c = context(browser, env, SIZES[size])
        if ui:
            c.add_cookies([{"name": "scout_ui", "value": ui, "url": env.url}])
            c.add_init_script("try{localStorage.setItem('scout-theme','%s')}catch(e){}" % theme)
            c.unroute("https://fonts.*/**")             # the real faces, for the pictures only
        try:
            for name, path in SCREENS:
                if ONLY and name not in ONLY:
                    continue
                p = open_page(c, env, path, SIZES[size])
                try:
                    p.wait_for_selector("#u-hub:not([hidden])", timeout=15000)
                    p.wait_for_timeout(1200)
                    p.screenshot(path=str(out / f"{name}-{size}-{tag}{'-' + theme if ui else ''}.png"))
                    sfx = f"{size}-{tag}{'-' + theme if ui else ''}.png"
                    if name == "board-nba":                 # the many-lines prop: the current "+N" list, the new ladder, rows by player
                        many = p.locator("#hub-rows tr.h-row", has_text="Shai Ladder").first
                        if ui:
                            many.locator("[data-hub=ladder]").click()
                            p.wait_for_timeout(300)
                            many.scroll_into_view_if_needed()
                            p.screenshot(path=str(out / f"lines-nba-{sfx}"))
                            p.locator("#hub-rows [data-hub=ladder][aria-expanded=true]").click()
                            p.locator("[data-hub=group-by][data-id=player]").click()
                            p.wait_for_timeout(300)
                            p.screenshot(path=str(out / f"byplayer-nba-{sfx}"))
                            p.locator("[data-hub=group-by][data-id=prop]").click()
                        elif many.locator("[data-hub=player-props]").count():
                            many.locator("[data-hub=player-props]").click()
                            p.wait_for_timeout(300)
                            many.scroll_into_view_if_needed()
                            p.screenshot(path=str(out / f"lines-nba-{sfx}"))
                            many.locator("[data-hub=player-props]").click()
                    if name.startswith("board-") and name != "board-all":
                        row = p.locator("#hub-rows tr[data-record] .h-player").first
                        if row.count():
                            row.click()
                            p.wait_for_timeout(1500)
                            p.screenshot(path=str(out / f"card-{name[6:]}-{size}-{tag}{'-' + theme if ui else ''}.png"))
                            for t in ("lines", "stats"):
                                tab = p.locator(f"#hub-tab-{t}")
                                if tab.count():
                                    tab.click()
                                    p.wait_for_timeout(500)
                                    p.screenshot(path=str(out / f"card-{name[6:]}-{t}-{size}-{tag}{'-' + theme if ui else ''}.png"))
                finally:
                    p.close()
        finally:
            c.close()


@pytest.mark.parametrize("size", list(SIZES))
def test_landing_shots(browser, env, size):
    out = Path(OUT)
    out.mkdir(parents=True, exist_ok=True)
    anon = browser.new_context(viewport=SIZES[size])
    try:
        p = anon.new_page()
        p.goto(env.url + "/")
        p.wait_for_timeout(1200)
        p.screenshot(path=str(out / f"landing-{size}-old.png"))
    finally:
        anon.close()
    c = context(browser, env, SIZES[size])
    c.unroute("https://fonts.*/**")
    try:
        p = open_page(c, env, "/preview/next/landing", SIZES[size])
        p.wait_for_timeout(1500)
        p.screenshot(path=str(out / f"landing-{size}-new-dark.png"))
    finally:
        c.close()


@pytest.mark.parametrize("size", list(SIZES))
def test_public_page_shots(browser, env, size, monkeypatch):
    out = Path(OUT)
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    for tag, flag in (("old", "0"), ("new-dark", None)):
        monkeypatch.setenv("UI_NEXT", flag) if flag else monkeypatch.delenv("UI_NEXT", raising=False)
        anon = browser.new_context(viewport=SIZES[size], color_scheme="dark")
        try:
            for name, path in (("record", "/record"), ("unsubscribe", "/alerts/unsubscribe?m=a&t=b")):
                p = anon.new_page()
                p.goto(env.url + path)
                p.wait_for_timeout(1500)
                p.screenshot(path=str(out / f"{name}-{size}-{tag}.png"))
                p.close()
        finally:
            anon.close()
