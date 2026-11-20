"""v3 card pictures (web/v3/faces.js + img_cache.py) in a real browser against the real Handler with fixture data. No network: the
upstream is a stub (synthetic pictures; V3_IMG_SAMPLES=<folder> of saved real files for the review screenshots).

What it checks, SCOUT_UI_V3=1, 390x844 and 1280, dark and light:
- board rows (CS2, NBA), the player card hero + match strip, slip legs and My picks rows show the same-origin picture of the exact
  board player (/img/...), never a third-party host; the team mark sits on its own disc;
- a player without a picture keeps his monogram; a picture that fails to load is removed and the monogram stays;
- nothing else on the card moves: no sideways scroll, no script error.
Flag off: no picture request and no faces node. V3_FACES_SHOTS=<folder> keeps screenshots."""
import hashlib
import io
import json
import os
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import img_cache as ic
import img_index as ix
import server
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401
from tests.test_ui_v3_browser import PHONE, DESK
from tests.test_ui_v3_player_browser import setup as card_setup, page as card_page, open_card, close
from tests.test_card_factors_browser import FLAGS

SHOTS = Path(os.environ.get("V3_FACES_SHOTS") or "")
SAMPLES = Path(os.environ.get("V3_IMG_SAMPLES") or "")
TEAMS = {"Halcyon": 101, "Norrland": 102, "Vanta": 103, "Kestrel Five": 104}
NICKS = {"vargo": "Halcyon", "brandr": "Norrland", "orlo": "Vanta", "mika7": "Kestrel Five", "sable": "Vanta", "raiko": "Kestrel Five"}
NO_PIC = "raiko"                                       # BO3.gg lists him with no picture: his monogram stays


@pytest.fixture(autouse=True)
def flags(monkeypatch):
    for k in FLAGS:
        monkeypatch.setenv(k, "1")


def _synthetic(kind, url):
    h = hashlib.sha1(url.encode()).digest()
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0) if kind == "t" else (40 + h[0] % 80, 50 + h[1] % 80, 90 + h[2] % 120, 255))
    d = ImageDraw.Draw(im)
    if kind == "t":
        d.polygon([(150, 20), (280, 150), (150, 280), (20, 150)], fill=(h[3], h[4], h[5], 255))
    else:
        d.ellipse((95, 40, 205, 160), fill=(225, 190, 160, 255))
        d.rectangle((60, 170, 240, 300), fill=(h[3] // 2, h[4] // 2, h[5] // 2, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _stub_get(url):
    kind = "t" if ("/teamlogos/" in url or "/uploads/team/" in url) else "p"
    sport = next((s for s in ("nba", "nfl", "soccer", "tennis") if f"/{s}/" in url), "cs2" if "bo3.gg" in url else "soccer")
    if "static.prizepicks.com" in url:
        sport = "tennis" if "/tennis/" in url else "soccer"
    if SAMPLES.parts:
        files = sorted(SAMPLES.glob(f"{kind}-{sport}-*.bin"))
        if files:                                      # one sample per id (distinct teams get distinct marks)
            m = re.search(r"/(\d+)/image/|/full/(\d+)\.png|/500/([a-z]+)\.png", url)
            tag = next((g for g in m.groups() if g), "") if m else url
            order = {"nyk": 0, "bos": 1, "mia": 2, "chi": 3, "kc": 0, "buf": 1}
            i = order.get(tag, int(tag) if tag.isdigit() else int(hashlib.sha1(tag.encode()).hexdigest(), 16))
            return files[i % len(files)].read_bytes()
    return _synthetic(kind, url)


def seed_pictures(monkeypatch, tmp_path, sports=("cs2", "nba", "nfl")):
    """The fixture's players get pictures the way production does: registered from the board snapshot by exact id, fetched once
    (stubbed upstream), stored on disk. The CS2 ids are the fixture's BO3.gg player ids."""
    monkeypatch.setenv("SCOUT_IMG_DIR", str(tmp_path / "img"))
    monkeypatch.setenv("SCOUT_IMG_WARM", "0")
    monkeypatch.setattr(ic, "HOST_GAP_S", 0)
    monkeypatch.setattr(ic, "_http_get", _stub_get)
    monkeypatch.setattr(ix, "bo3_scan", lambda *a, **k: 0)
    ic.reset()
    ix.reset()
    pid = lambda n: sum(map(ord, n))                  # noqa: E731 - tests/workspace_fixture.row's player_id
    ix._BO3.update(players={pid(n): (1.0, None if n == NO_PIC else f"https://files.bo3.gg/uploads/player/{pid(n)}/image/p.webp", 1, TEAMS[t], False)
                            for n, t in NICKS.items()},
                   teams={tid: (1.0, f"https://files.bo3.gg/uploads/team/{tid}/image/t.webp", name) for name, tid in TEAMS.items()})
    warm(sports)


def warm(sports):
    for s in sports:
        ix.build(server, s)
    while (k := ic._next()) is not None:
        ic.fetch_one(k)
    ix._MEMO.clear()


def shot(p, name):
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p.wait_for_timeout(350)
        p.screenshot(path=str(SHOTS / f"{name}.png"))


def same_origin_only(p):
    srcs = p.evaluate("[...document.querySelectorAll('.v3f img, .v3f-team')].map(i => i.getAttribute('src'))")
    assert srcs and all(s.startswith("/img/") for s in srcs), srcs
    assert p.evaluate("![...document.querySelectorAll('.v3f img')].some(i => /espncdn|bo3\\.gg|prizepicks/.test(i.src))")


def board(browser, env, size, theme, sport):
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors, p.img_requests = [], []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.on("request", lambda r: p.img_requests.append(r.url) if "/img/" in r.url else None)
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    p.locator(f"#hub-sports [data-id={sport}]").click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{sport}|'))")
    return c, p


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_rows_show_the_exact_players_picture_and_team_mark(browser, env, monkeypatch, tmp_path, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seed_pictures(monkeypatch, tmp_path)
    c, p = board(browser, env, size, theme, "cs2")
    try:
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-img.is-in", timeout=15000)
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-badge.is-in", timeout=15000)
        rows = p.evaluate("""[...document.querySelectorAll('#hub-rows td.h-c-player')].map(td => ({name: td.querySelector('.h-player b').textContent,
            img: td.querySelector('.v3f-img') && td.querySelector('.v3f-img').getAttribute('src'),
            badge: td.querySelector('.v3f-badge') && td.querySelector('.v3f-badge').getAttribute('src')}))""")
        for r in rows:
            n = r["name"]
            if n == NO_PIC:
                assert r["img"] is None, r                                  # no picture: his monogram, aligned with the others
            else:
                assert r["img"].startswith(f"/img/p/cs2/{sum(map(ord, n))}.webp?v="), r
                assert r["badge"].startswith(f"/img/t/cs2/{TEAMS[NICKS[n]]}.webp?v="), r
        box = p.evaluate("(() => { const f = document.querySelector('#hub-rows td.h-c-player .v3f.v3f-on'); const r = f.getBoundingClientRect(); return [r.width, r.height]; })()")
        assert abs(box[0] - box[1]) < 0.5 and box[0] >= 24                  # round, square, a real size
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"board-cs2-{size['width']}-{theme}")
        p.locator("#hub-sports [data-id=nba]").click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('nba|'))")
        p.wait_for_selector("#hub-rows .u-face .v3f-img.is-in", timeout=15000)
        assert p.evaluate("!document.querySelector('#hub-rows img[src*=\"espncdn\"]')")    # the hotlinks were swapped
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"board-nba-{size['width']}-{theme}")
        assert not p.errors, p.errors
        assert all("/img/index/" in u or "/img/p/" in u or "/img/t/" in u for u in p.img_requests)
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_player_card_hero_and_match_strip(browser, env, monkeypatch, tmp_path, size, theme):
    card_setup(env, monkeypatch, theme)
    seed_pictures(monkeypatch, tmp_path)
    c, p = card_page(browser, env, size, theme)
    try:
        for sport, q, team in (("cs2", "vargo", "Halcyon"), ("nba", "Karl", None)):
            open_card(p, sport, q)
            p.wait_for_selector("#hub-detail .h-card-head > .v3f .v3f-img.is-in", timeout=15000)
            hero = p.evaluate("(() => { const r = document.querySelector('#hub-detail .h-card-head > .v3f').getBoundingClientRect(); return [r.width, r.height]; })()")
            assert hero[0] >= 56 and abs(hero[0] - hero[1]) < 0.5
            assert p.locator("#hub-card-title").is_visible()
            if team:
                p.wait_for_selector("#hub-detail .sc-matchup__team--a > .v3f-team.is-in", timeout=15000)
                assert p.locator("#hub-detail .sc-matchup__team--a > b").inner_text() == team
            same_origin_only(p)
            no_h_scroll(p)
            shot(p, f"card-{sport}-{size['width']}-{theme}")
            close(p)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_slip_legs_show_pictures(browser, env, monkeypatch, tmp_path, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seed_pictures(monkeypatch, tmp_path)
    c, p = board(browser, env, size, theme, "cs2")
    try:
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-img", timeout=15000)
        adds = p.locator("#hub-rows tr[data-record] .h-add:not(.h-add-info):not([disabled])")
        for i in range(min(2, adds.count())):
            adds.nth(i).click()
            p.wait_for_timeout(250)
        if size is PHONE:
            p.goto(env.url + "/#/slips")
            p.wait_for_selector("#hub-studio-section:not([hidden])", timeout=20000)
        sel = "#hub-studio-body .u-leg, #hub-slip-content .u-leg"
        p.wait_for_selector(sel, timeout=20000)
        p.wait_for_selector(":is(#hub-studio-body,#hub-slip-content) .u-leg .v3f .v3f-img.is-in", timeout=15000)
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"slips-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_my_picks_rows_show_pictures(browser, env, monkeypatch, tmp_path, size, theme):
    from tests.test_ui_v3_picks_browser import picks_page
    seed_pictures(monkeypatch, tmp_path)
    c, p = picks_page(browser, env, monkeypatch, size, theme)
    try:
        p.wait_for_selector("#hub-picks-section .u-pick .v3f-img.is-in", timeout=15000)
        names = p.evaluate("""[...document.querySelectorAll('#hub-picks-section .u-pick > button')].map(b => [b.querySelector(':scope > span > b').textContent,
            !!b.querySelector(':scope > .v3f .v3f-img')])""")
        for text, has in names:
            assert has == (not text.startswith(NO_PIC)), (text, has)
        assert p.locator("#hub-picks-section .u-pick").count() == 6              # every receipt still there
        same_origin_only(p)
        no_h_scroll(p)
        p.locator("#hub-picks-section .u-pick").first.scroll_into_view_if_needed()
        shot(p, f"picks-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


def soccer_docs():
    import workspace_expansion as adapter
    from tests.test_expansion_main_board import source
    docs = {}
    for s in adapter.SPORTS:
        d = source(s)
        offer, half, research = d["offers"][0], d["offers"][2], d["research"][adapter._key(d["offers"][0])]
        for o in (offer, half):
            o["player_id"] = f"{s}:prizepicks:555"
        research = {**research, "identity": {**research["identity"], "player_id": f"{s}:player:espn:777",
                                             **({"team_id": "soccer:team:espn:359"} if s == "soccer" else {})}}
        d["research"] = {adapter._key(offer): research, adapter._key(half): {**research, "scope": half["scope"]}}
        d["images"] = {f"{s}:prizepicks:555": f"https://static.prizepicks.com/images/players/{s}/verified.webp"}
        docs[s] = d
    return docs


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_soccer_and_tennis_pictures(browser, env, monkeypatch, tmp_path, size, theme):
    import workspace_sports
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setenv("SCOUT_TENNIS_ENABLED", "1")
    original, docs = workspace_sports.call, soccer_docs()
    monkeypatch.setattr(workspace_sports, "call", lambda srv, path, query=None, **kw: (docs[query["sport"]], None)
                        if path == "/api/sports/expansion/board" else original(srv, path, query, **kw))
    seed_pictures(monkeypatch, tmp_path, sports=("soccer", "tennis"))
    # ESPN has no picture for 777 here: the PrizePicks picture of the exact offer player is used
    assert ic._SRC[("p", "soccer", "e777")][1].startswith("https://static.prizepicks.com/")
    c, p = board(browser, env, size, theme, "soccer")
    try:
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-img.is-in", timeout=15000)
        assert p.evaluate("document.querySelector('#hub-rows td.h-c-player .v3f-img').getAttribute('src')").startswith("/img/p/soccer/e777.webp")
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-badge.is-in", timeout=15000)                 # ESPN team 359
        no_h_scroll(p)
        shot(p, f"board-soccer-{size['width']}-{theme}")
        p.locator("#hub-rows .h-player").first.click()
        p.wait_for_selector("#hub-detail .h-card-head > .v3f .v3f-img.is-in", timeout=20000)
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"card-soccer-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_a_picture_that_fails_leaves_the_monogram(browser, env, monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seed_pictures(monkeypatch, tmp_path)
    vargo = ("p", "cs2", str(sum(map(ord, "vargo"))))
    ic.path_of(vargo).unlink()                         # listed as ready, gone by the time the browser asks
    ic.reset()
    c, p = board(browser, env, DESK, "dark", "cs2")
    try:
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-img.is-in", timeout=15000)
        p.wait_for_timeout(500)
        got = p.evaluate("""[...document.querySelectorAll('#hub-rows td.h-c-player')].filter(td => td.querySelector('.h-player b').textContent === 'vargo')
            .map(td => [!!td.querySelector('.v3f-img'), !!td.querySelector('.h-player')])""")
        assert got and all(g == [False, True] for g in got), got        # the failed picture is gone; the row is whole
        mono = p.evaluate("""[...document.querySelectorAll('#hub-rows td.h-c-player')].filter(td => td.querySelector('.h-player b').textContent === 'vargo')
            .map(td => td.querySelector(':scope > .u-face > i') && td.querySelector(':scope > .u-face > i').textContent)""")
        assert mono and all(m == "VA" for m in mono), mono               # his monogram shows
        assert not p.errors, p.errors
    finally:
        c.close()


def test_flag_off_no_pictures_and_no_requests(browser, env, monkeypatch, tmp_path):
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    seed_pictures(monkeypatch, tmp_path)
    c, p = board(browser, env, PHONE, "dark", "cs2")
    try:
        p.wait_for_timeout(600)
        assert not p.img_requests
        assert p.evaluate("!document.querySelector('.v3f, .v3f-team') && !window.ScoutV3")
        assert not p.errors, p.errors
    finally:
        c.close()
