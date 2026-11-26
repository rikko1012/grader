"""Landing feature sections (SCOUT_LANDING_FEATURES): one member screen per section, saved examples only.

Off by default: the story page keeps its tabbed block byte-for-byte reachable. On: five sections replace it, the page stays
one self-contained cached response, and nothing in the demo reads the member API."""
import re

import pytest
import landing_v3
from tests.test_landing_v3 import srv, seed, board, JOIN, wait_builder  # noqa: F401
from tests.test_landing_story import Structure, render

SECTIONS = ("f-board", "f-card", "f-maps", "f-lines", "f-slip")
STAGES = ("fx-board", "fx-card", "fx-maps", "fx-lines", "fx-slip")


@pytest.fixture
def page(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_STORY", "1")
    seed(srv, monkeypatch, board())
    landing_v3.reset()
    yield srv
    wait_builder()
    landing_v3.reset()


def test_on_by_default(monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_STORY", "1")
    monkeypatch.delenv("SCOUT_LANDING_FEATURES", raising=False)
    assert landing_v3.features_enabled()


def test_off_keeps_the_tabbed_block(page, monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "0")
    assert not landing_v3.features_enabled()
    s = render(page)
    assert "feature-theatre" in s and 'id="feature-tab-2"' in s
    assert "fx-sec" not in s and "fx-stage" not in s and "{{" not in s and "<!--if:" not in s


@pytest.mark.parametrize("value", ["1", "true", "on"])
def test_on_replaces_the_tabs_with_five_sections(page, monkeypatch, value):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", value)
    landing_v3.reset()
    s = render(page); doc = Structure(); doc.feed(s)
    assert len(doc.ids) == len(set(doc.ids))
    assert all(i in doc.ids for i in SECTIONS + STAGES)
    assert 'class="feature-theatre"' not in s and 'id="feature-panel"' not in s
    assert not doc.images and not doc.scripts                      # still one response: nothing fetched for the demos
    assert all(h[1:] in doc.ids for h in doc.links if h.startswith("#"))
    assert "{{" not in s and "<!--if:" not in s and "<!--/if:" not in s
    # nav, hero, the Slip Studio button, pricing, final, dock + the demo's own data-trial; the per-tool "Try it free"
    # links went with the CRO pass (2026-11-26: they repeated the trial without its terms)
    assert s.count(JOIN.replace("&", "&amp;")) >= 7 and 'class="fx-try"' not in s
    assert "Every player prop." in s and "hx" in doc.ids and "signin-error" in doc.ids          # the hero is on with them


def test_features_need_the_story_page(monkeypatch):
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "1")
    monkeypatch.setenv("SCOUT_LANDING_STORY", "0")
    assert not landing_v3.features_enabled()
    assert "fx-sec" not in landing_v3._template()


def test_demo_is_saved_examples_only_and_safe():
    js = landing_v3.FEAT_ASSETS["FEAT_JS"].read_text(encoding="utf-8")
    css = landing_v3.FEAT_ASSETS["FEAT_CSS"].read_text(encoding="utf-8")
    assert not re.search(r"\bfetch\(|XMLHttpRequest|/api/|localStorage|document\.cookie", js)
    assert "prefers-reduced-motion" in js and "prefers-reduced-motion" in css
    assert "document.hidden" in js and "IntersectionObserver" in js and "isTrusted" in js
    assert not re.search(r"hit rate|guarantee|lock of|sure thing", js, re.I)
    # the published payout tables the member Slip Studio reads (payout_reference.py)
    import payout_reference as pr
    assert "POWER = {2: 3, 3: 6, 4: 10}" in js and (pr.PP_POWER[2], pr.PP_POWER[3], pr.PP_POWER[4]) == (3.0, 6.0, 10.0)
    assert "FLEX = {3: [3, 1], 4: [6, 1.5]}" in js and pr.PP_FLEX[3] == {0: 3.0, 1: 1.0} and pr.PP_FLEX[4] == {0: 6.0, 1: 1.5}


def test_old_script_survives_without_the_tabbed_block():
    js = landing_v3.STORY_ASSETS["STORY_JS"].read_text(encoding="utf-8")
    assert "if(demoFeature){" in js and "if (!$('#feature-panel'))" in js


# ---- in a real browser, flag on: the controls work and reduced motion shows finished frames ----
from tests.test_upgrade_browser import browser  # noqa: E402,F401


@pytest.fixture
def live(page, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    monkeypatch.setenv("SCOUT_LANDING_FEATURES", "1")
    landing_v3.reset()
    import server
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    httpd.shutdown(); httpd.server_close()


@pytest.mark.parametrize("size", [(390, 844), (1440, 900)])
def test_controls_work_and_nothing_overflows(browser, live, size):
    c = browser.new_context(viewport={"width": size[0], "height": size[1]}, reduced_motion="reduce")
    p = c.new_page(); errs = []
    p.on("pageerror", lambda e: errs.append(str(e)))
    p.goto(live); p.wait_for_selector("#fx-slip .fx-legs li")
    assert p.evaluate("document.documentElement.scrollWidth") <= size[0]
    # reduced motion: every stage is in its finished frame, no cursor, no hidden rows
    assert p.locator("#fx-slip .fx-legs li[data-id]").count() == 3 and "6×" in p.inner_text("#fx-slip .fx-pay")
    assert p.locator("#fx-maps .fx-map[aria-pressed=true]").count() == 2 and "14.84" in p.inner_text("#fx-maps .fx-comb")
    assert p.locator(".fx-cur").count() == 0
    assert p.evaluate("[...document.querySelectorAll('.fx-row:not([hidden])')].every(r => getComputedStyle(r).opacity === '1')")
    # the board follows the app
    assert "53.8%" in p.inner_text("#fx-board .fx-row >> nth=0")
    p.click("#fx-board [data-app=pp]")
    assert "58.8%" in p.inner_text("#fx-board .fx-row >> nth=0") and "30.5" in p.inner_text("#fx-board .fx-row >> nth=0")
    p.click("#fx-board .fx-add >> nth=0"); assert p.inner_text("#fx-board .fx-cnt") == "1"
    # the card recounts for the chosen line
    p.click("#fx-card [data-tab=lines]"); p.click("#fx-card [data-book=ud]")
    assert "53.8%" in p.inner_text("#fx-card .fx-dp") and "29.5" in p.inner_text("#fx-card .fx-pline")
    p.click("#fx-card [data-tab=stats]"); assert "Weight 0" in p.inner_text("#fx-card .fx-tp")
    # maps: a third pick replaces the first; clear empties it
    p.click("#fx-maps [data-k=nuke]"); assert "15.38" in p.inner_text("#fx-maps .fx-comb")
    p.click("#fx-maps .fx-clear"); assert p.locator("#fx-maps .fx-map[aria-pressed=true]").count() == 0
    # views filter the board
    p.click("#fx-lines .fx-vbtn"); p.click("#fx-lines [data-view=moved]")
    assert p.locator("#fx-lines .fx-row:not([hidden])").count() == 3
    # the slip prices what is in it
    p.click("#fx-slip .fx-add[data-id=twi]"); assert "10×" in p.inner_text("#fx-slip .fx-pay") and p.inner_text("#fx-slip .fx-cnt2") == "4"
    p.click("#fx-slip [data-e=flex]"); assert "6×" in p.inner_text("#fx-slip .fx-pay") and "1.5×" in p.inner_text("#fx-slip .fx-pay")
    for _ in range(3): p.click("#fx-slip .fx-rm >> nth=0")
    assert "Add 1 more" in p.inner_text("#fx-slip .fx-pay")
    assert not errs, errs
    c.close()
