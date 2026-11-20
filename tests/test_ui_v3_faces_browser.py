"""v3 card pictures (web/v3/faces.js + img_cache.py) in a real browser against the real Handler with fixture data. No network: the
upstream is a stub (synthetic pictures; V3_IMG_SAMPLES=<folder> of saved real files for the review screenshots).

What it checks, SCOUT_UI_V3=1, 390x844 and 1280, dark and light:
- NBA / NFL / soccer / tennis board rows, the player card hero + match strip and slip legs show the same-origin picture of the
  exact board player (/img/...), never a third-party host; the team mark sits on its own disc;
- CS2 has no pictures (owner 2026-11-20 "stop using BO3.gg, we have HLTV"; the HLTV data stores no picture URLs): CS2 rows, the
  CS2 card and CS2 My picks rows keep the hub's monogram, and no CS2 picture or picture list is ever asked for;
- a picture that fails to load is removed; nothing else on the card moves: no sideways scroll, no script error.
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
    assert "bo3.gg" not in url, url                    # never a BO3.gg picture
    kind = "t" if "/teamlogos/" in url else "p"
    sport = next((s for s in ("nba", "nfl", "soccer", "tennis") if f"/{s}/" in url), "soccer")
    if "static.prizepicks.com" in url:
        sport = "tennis" if "/tennis/" in url else "soccer"
    if SAMPLES.parts:
        files = sorted(SAMPLES.glob(f"{kind}-{sport}-*.bin"))
        if files:                                      # one sample per id (distinct teams get distinct marks)
            m = re.search(r"/full/(\d+)\.png|/500/([a-z]+)\.png", url)
            tag = next((g for g in m.groups() if g), "") if m else url
            order = {"nyk": 0, "bos": 1, "mia": 2, "chi": 3, "kc": 0, "buf": 1}
            i = order.get(tag, int(tag) if tag.isdigit() else int(hashlib.sha1(tag.encode()).hexdigest(), 16))
            return files[i % len(files)].read_bytes()
    return _synthetic(kind, url)


def seed_pictures(monkeypatch, tmp_path, sports=("nba", "nfl")):
    """The fixture's players get pictures the way production does: registered from the board snapshot by exact id, fetched once
    (stubbed upstream), stored on disk."""
    monkeypatch.setenv("SCOUT_IMG_DIR", str(tmp_path / "img"))
    monkeypatch.setenv("SCOUT_IMG_WARM", "0")
    monkeypatch.setattr(ic, "HOST_GAP_S", 0)
    monkeypatch.setattr(ic, "_http_get", _stub_get)
    ic.reset()
    ix.reset()
    warm(sports)
    assert not any(k[1] == "cs2" for k in ic._SRC)


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


def no_cs2_pictures(p, scope):
    """CS2: no picture node, no CS2 picture or list request, never a BO3.gg image."""
    assert p.evaluate(f"!document.querySelector('{scope} .v3f-img, {scope} .v3f-badge, {scope} .v3f-team, {scope} [data-v3f]')")
    assert not [u for u in getattr(p, "img_requests", []) if "/cs2" in u], p.img_requests
    assert p.evaluate(f"![...document.querySelectorAll('{scope} img')].some(i => /bo3\\.gg/.test(i.src))")


def track_img(p):
    p.img_requests = []
    p.on("request", lambda r: p.img_requests.append(r.url) if "/img/" in r.url else None)


def board(browser, env, size, theme, sport):
    c = context(browser, env, size)
    c.add_init_script(f"try{{localStorage.setItem('scout-theme','{theme}')}}catch(e){{}}")
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    track_img(p)
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    p.locator(f"#hub-sports [data-id={sport}]").click()
    p.wait_for_function(f"[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('{sport}|'))")
    return c, p


def nba_index():
    return json.loads(ix.index(server, "nba"))


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_board_rows_cs2_monogram_nba_exact_picture(browser, env, monkeypatch, tmp_path, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seed_pictures(monkeypatch, tmp_path)
    c, p = board(browser, env, size, theme, "cs2")
    try:
        p.wait_for_timeout(800)
        no_cs2_pictures(p, "#hub-rows")
        assert p.locator("#hub-rows td.h-c-player .h-player b").count() >= 4      # every row still there
        no_h_scroll(p)
        shot(p, f"board-cs2-{size['width']}-{theme}")
        p.locator("#hub-sports [data-id=nba]").click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('nba|'))")
        p.wait_for_selector("#hub-rows .u-face .v3f-img.is-in", state="attached", timeout=15000)
        p.wait_for_selector("#hub-rows .u-face .v3f-badge.is-in", state="attached", timeout=15000)
        assert p.evaluate("!document.querySelector('#hub-rows img[src*=\"espncdn\"]')")    # the hotlinks were swapped
        rows = p.evaluate("""[...document.querySelectorAll('#hub-rows .u-face[data-v3f-pid]')].map(f => [f.getAttribute('data-v3f-pid'),
            f.querySelector('.v3f-img') && f.querySelector('.v3f-img').getAttribute('src')])""")
        assert rows
        for pid, src in rows:                                               # each row: its own exact ESPN id's picture
            assert src is None or src.startswith(f"/img/p/nba/{pid.rsplit(':', 1)[1]}.webp?v="), (pid, src)
        boxes = p.evaluate("[...document.querySelectorAll('#hub-rows .u-face.v3f-on')].map(f => f.getBoundingClientRect()).filter(r => r.width).map(r => [r.width, r.height])")
        assert boxes or size is PHONE, "no visible NBA face"                  # the hub hides sports row faces at phone width
        assert all(abs(w - h) < 0.5 and w >= 24 for w, h in boxes), boxes   # round, square, a real size
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"board-nba-{size['width']}-{theme}")
        assert not p.errors, p.errors
        assert all(re.search(r"/img/(index/(nba|nfl|soccer|tennis)\.json|[pt]/(nba|nfl|soccer|tennis)/)", u) for u in p.img_requests), p.img_requests
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_player_card_hero_and_match_strip(browser, env, monkeypatch, tmp_path, size, theme):
    card_setup(env, monkeypatch, theme)
    seed_pictures(monkeypatch, tmp_path)
    c, p = card_page(browser, env, size, theme)
    track_img(p)
    try:
        open_card(p, "cs2", "vargo")                   # CS2: the hub's monogram, no picture, no team mark
        p.wait_for_selector("#hub-detail .h-card-head > .u-face, #hub-detail .h-card-head > .v3p-avatar", timeout=15000)
        p.wait_for_timeout(1000)
        no_cs2_pictures(p, "#hub-detail")
        mono = p.evaluate("(document.querySelector('#hub-detail .h-card-head > .u-face, #hub-detail .h-card-head > .v3p-avatar').textContent || '').trim()")
        assert mono and mono.upper().startswith("V"), mono                    # the hub's own initial(s)
        assert p.locator("#hub-card-title").is_visible()
        no_h_scroll(p)
        shot(p, f"card-cs2-{size['width']}-{theme}")
        close(p)
        open_card(p, "nba", "Karl")
        p.wait_for_selector("#hub-detail .h-card-head > .v3f .v3f-img.is-in", timeout=15000)
        hero = p.evaluate("(() => { const r = document.querySelector('#hub-detail .h-card-head > .v3f').getBoundingClientRect(); return [r.width, r.height]; })()")
        assert hero[0] >= 56 and abs(hero[0] - hero[1]) < 0.5
        assert p.locator("#hub-card-title").is_visible()
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"card-nba-{size['width']}-{theme}")
        close(p)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_slip_legs_show_pictures(browser, env, monkeypatch, tmp_path, size, theme):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seed_pictures(monkeypatch, tmp_path)
    c, p = board(browser, env, size, theme, "nba")
    try:
        p.wait_for_selector("#hub-rows .u-face .v3f-img", state="attached", timeout=15000)
        adds = p.locator("#hub-rows tr[data-record] .h-add:not(.h-add-info):not([disabled])")
        for i in range(min(2, adds.count())):
            adds.nth(i).click()
            p.wait_for_timeout(250)
        if size is PHONE:
            p.goto(env.url + "/#/slips")
            p.wait_for_selector("#hub-studio-section:not([hidden])", timeout=20000)
        elif not p.locator("#u-hub .h-slip").is_visible():
            p.locator("#hub-focus").click()            # the slip rail starts closed below 1440 (polish2): the member's Show slip
            p.wait_for_selector("#u-hub .h-slip", state="visible", timeout=10000)
        sel = "#hub-studio-body .u-leg, #hub-slip-content .u-leg"
        p.wait_for_selector(sel, state="attached", timeout=20000)
        p.wait_for_selector(":is(#hub-studio-body,#hub-slip-content) .u-leg .v3f .v3f-img.is-in", state="attached", timeout=15000)
        assert p.evaluate(f"[...document.querySelectorAll('{sel}')].some(l => l.getBoundingClientRect().width && l.querySelector('.v3f-img'))")
        same_origin_only(p)
        no_h_scroll(p)
        shot(p, f"slips-{size['width']}-{theme}")
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("theme", ["dark", "light"])
@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_v3_my_picks_cs2_rows_keep_their_layout_without_pictures(browser, env, monkeypatch, tmp_path, size, theme):
    from tests.test_ui_v3_picks_browser import picks_page
    seed_pictures(monkeypatch, tmp_path)
    c, p = picks_page(browser, env, monkeypatch, size, theme)
    track_img(p)
    try:
        p.wait_for_timeout(800)
        no_cs2_pictures(p, "#hub-picks-section")
        assert p.locator("#hub-picks-section .u-pick").count() == 6              # every receipt still there
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
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-img.is-in", state="attached", timeout=15000)   # rows: desktop only (CSS)
        assert p.evaluate("document.querySelector('#hub-rows td.h-c-player .v3f-img').getAttribute('src')").startswith("/img/p/soccer/e777.webp")
        p.wait_for_selector("#hub-rows td.h-c-player .v3f-badge.is-in", state="attached", timeout=15000)  # ESPN team 359
        assert p.evaluate("[...document.querySelectorAll('#hub-rows td.h-c-player')].every(td => td.querySelectorAll(':scope > .u-face, :scope > .sc-bd-av').length <= 1)")  # one avatar per row
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


def test_v3_a_picture_that_fails_is_removed(browser, env, monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    seed_pictures(monkeypatch, tmp_path)
    pid = next(k for k, v in nba_index()["p"].items() if v[0])
    ix._MEMO.clear()
    ic.path_of(("p", "nba", pid.rsplit(":", 1)[1])).unlink()   # listed as ready, gone by the time the browser asks
    ic.reset()
    c, p = board(browser, env, DESK, "dark", "nba")
    try:
        p.wait_for_selector("#hub-rows .u-face .v3f-img.is-in", state="attached", timeout=15000)
        p.wait_for_timeout(500)
        got = p.evaluate(f"""[...document.querySelectorAll('#hub-rows td.h-c-player')].filter(td => td.querySelector('.u-face[data-v3f-pid="{pid}"]'))
            .map(td => [!!td.querySelector('.v3f-img'), !!td.querySelector('.h-player')])""")
        assert got and all(g == [False, True] for g in got), got        # the failed picture is gone; the row is whole
        assert not p.errors, p.errors
    finally:
        c.close()


def test_flag_off_no_pictures_and_no_requests(browser, env, monkeypatch, tmp_path):
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    seed_pictures(monkeypatch, tmp_path)
    c, p = board(browser, env, PHONE, "dark", "nba")
    try:
        p.wait_for_timeout(600)
        assert not p.img_requests
        assert p.evaluate("!document.querySelector('.v3f, .v3f-team') && !window.ScoutV3")
        assert not p.errors, p.errors
    finally:
        c.close()


# ------------------------------------------------------------------------------------------------ review 2026-11-20: strict sport + id
def _fake_index(monkeypatch, sport, edit):
    """Serve one sport's picture list changed by edit(d) (a name collision, an id without a picture); other sports unchanged."""
    real = json.loads(ix.index(server, sport))
    edit(real)
    body = json.dumps(real).encode()
    orig = ix.index
    monkeypatch.setattr(ix, "index", lambda srv, s: body if s == sport else orig(srv, s))
    return real


GEN_CARD = """(cards => { const host = document.querySelector('#hub-generated') || document.querySelector('#u-hub');
  const art = document.createElement('article'); art.className = 'h-generated-card'; art.id = 'faces-probe';
  art.innerHTML = '<ol>' + cards.map(c => '<li data-probe="' + c[0] + '"><div><strong>' + c[1] + '</strong><small>' + c[0] + ' · Probe event</small></div></li>').join('') + '</ol>';
  host.appendChild(art); })"""


def test_v3_no_face_across_sports_lol_cs2_rows_card_and_autobuild_legs(browser, env, monkeypatch, tmp_path):
    """A LoL player whose name the NBA list also names keeps his monogram on his row, his card and an Auto-build leg; an Auto-build
    leg tagged NBA gets that board player's picture; a CS2 leg (no CS2 pictures) and a leg of an unknown sport get none."""
    card_setup(env, monkeypatch, "dark")
    seed_pictures(monkeypatch, tmp_path)
    real = nba_index()
    nba_name, nba_pid = next((n, i) for n, i in real["n"].items() if real["p"][i][0])
    c, p = card_page(browser, env, DESK, "dark")
    track_img(p)
    try:
        p.locator('#hub-sports [data-id=lol]').click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].length && [...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('lol|'))")
        lol = p.evaluate("document.querySelector('#hub-rows tr[data-record] .h-player b').textContent.trim()")

        def collide(d):
            d["n"][lol] = nba_pid                      # the NBA board "has" a player named like the LoL player
            d["n"]["vargo"] = nba_pid                  # ... and like a CS2 player
        _fake_index(monkeypatch, "nba", collide)
        p.reload()
        p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
        p.locator('#hub-sports [data-id=nba]').click()           # the NBA list is loaded in this page
        p.wait_for_selector("#hub-rows .u-face .v3f-img.is-in", state="attached", timeout=15000)
        p.locator('#hub-sports [data-id=lol]').click()
        p.wait_for_function("[...document.querySelectorAll('#hub-rows tr[data-record]')].every(r => r.dataset.record.startsWith('lol|'))")
        # Auto-build legs (sport from the leg's own tag)
        p.evaluate(GEN_CARD + "(%s)" % json.dumps([["LoL", lol], ["NBA", nba_name], ["CS2", "vargo"], ["Darts", lol]]))
        p.wait_for_selector('#faces-probe li[data-probe=NBA] .v3f-img', state="attached", timeout=10000)
        p.wait_for_timeout(500)
        got = p.evaluate("""[...document.querySelectorAll('#faces-probe li')].map(li => [li.dataset.probe,
            li.querySelector('.v3f-img') && li.querySelector('.v3f-img').getAttribute('src')])""")
        by = {g[0]: g for g in got}
        assert by["LoL"][1] is None and by["CS2"][1] is None and by["Darts"][1] is None, got   # never another sport's face
        assert by["NBA"][1].startswith(f"/img/p/nba/{nba_pid.rsplit(':', 1)[1]}.webp"), got  # its own sport's board player
        # the LoL rows and the LoL card
        assert p.evaluate("!document.querySelector('#hub-rows .v3f-img')"), "LoL rows show an NBA face"
        p.locator('#hub-rows tr[data-record] .h-player').first.click()
        p.wait_for_selector('#hub-detail .h-card-head', timeout=20000)
        p.wait_for_timeout(2500)                       # the LoL card's own loads (its saved results may stay loading here)
        assert p.evaluate("!document.querySelector('#hub-detail .h-card-head .v3f-img')"), "LoL card shows an NBA face"
        assert not [u for u in p.img_requests if "/cs2" in u], p.img_requests
        assert not p.errors, p.errors
    finally:
        c.close()


def test_v3_card_with_a_known_id_and_no_picture_keeps_the_monogram(browser, env, monkeypatch, tmp_path):
    """The open card's exact id has no ready picture: the monogram stays, even when the name would map to someone else's picture
    (bySport is strict once an id is known)."""
    card_setup(env, monkeypatch, "dark")
    seed_pictures(monkeypatch, tmp_path)
    real = nba_index()
    karl = next(n for n in real["n"] if n.startswith("Karl"))
    karl_id = real["n"][karl]
    other_id = next(i for i in real["n"].values() if i != karl_id and real["p"][i][0])

    def no_pic(d):
        d["p"].pop(karl_id, None)
        d["n"][karl] = other_id                        # a name lookup would give the other player's picture
    _fake_index(monkeypatch, "nba", no_pic)
    c, p = card_page(browser, env, DESK, "dark")
    try:
        p.evaluate("""window.__heroImgs = []; new MutationObserver(l => l.forEach(m => m.addedNodes.forEach(n => {
            if (n.nodeType === 1 && n.classList.contains('v3f-img') && n.closest('#hub-detail')) window.__heroImgs.push(n.getAttribute('src')); })))
            .observe(document.documentElement, {childList: true, subtree: true})""")
        open_card(p, "nba", "Karl")
        p.wait_for_selector("#hub-detail .h-card-head > .v3p-avatar, #hub-detail .h-card-head > .u-face", timeout=15000)
        p.wait_for_timeout(1500)
        assert p.evaluate("window.__heroImgs") == [], p.evaluate("window.__heroImgs")
        assert p.evaluate("!document.querySelector('#hub-detail .h-card-head .v3f-img')")
        mono = p.evaluate("(document.querySelector('#hub-detail .h-card-head > .u-face, #hub-detail .h-card-head > .v3p-avatar').textContent || '').trim()")
        assert mono, "the hero lost its monogram"       # (test_v3_player_card_hero_and_match_strip: Karl's own picture shows)
        assert not p.errors, p.errors
    finally:
        c.close()
