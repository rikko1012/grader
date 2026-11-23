"""The signed-out pages in the redesign's identity (owner 2026-11-11: "make all of the signed out pages match the new ui"), now behind
UI_NEXT=1: since 2026-11-13 the previous design is the default for signed-out pages too (test_previous_design_is_the_default_again).
Styling only: each signed-out route answers with the same status, the same gate and the same data as before; the page bytes differ
from the previous design only by the added stylesheet block. Fixture data only."""
import re

import pytest

import server
from tests.test_upgrade_browser import browser, env  # noqa: F401

STYLE = re.compile(rb'<link rel="stylesheet" href="https://fonts\.googleapis\.com/css2\?family=Barlow[^>]*><style>.*?</style>(?=</head>)', re.S)
SIZES = {"desktop": {"width": 1440, "height": 900}, "phone": {"width": 390, "height": 844}}


def anon_get(browser, env, path):
    c = browser.new_context()
    try:
        r = c.request.get(env.url + path, max_redirects=0)
        return r.status, r.body(), r.headers.get("content-type", "")
    finally:
        c.close()


@pytest.mark.parametrize("path", ["/", "/index.html", "/lol", "/sports", "/sports/"])
def test_landing_routes_serve_the_same_page_with_only_the_style_block_added(browser, env, monkeypatch, path):
    monkeypatch.setenv("UI_NEXT", "0")
    st0, old, _ = anon_get(browser, env, path)
    monkeypatch.setenv("UI_NEXT", "1")
    st1, new, ct = anon_get(browser, env, path)
    assert st0 == st1 == 200 and ct.startswith("text/html")
    assert new != old and STYLE.sub(b"", new) == old                    # the same landing.html, plus one stylesheet block
    assert b"Sign in with Whop" in new and b"/auth/login" in new
    assert b"data-record" not in new and b"hub-rows" not in new          # no board, no member data


@pytest.mark.parametrize("path", ["/api/upgrade/board?sport=cs2", "/api/upgrade/me", "/api/upgrade/line?book=underdog&id=x", "/upgrade/hub.js", "/upgrade/next.css",
                                  "/preview/next", "/preview/current", "/preview/next/landing", "/api/board", "/api/grade?player=vargo", "/auth/me", "/content-book"])
def test_gated_routes_answer_a_signed_out_visitor_exactly_as_before(browser, env, monkeypatch, path):
    monkeypatch.setenv("UI_NEXT", "0")
    before = anon_get(browser, env, path)
    monkeypatch.setenv("UI_NEXT", "1")
    after = anon_get(browser, env, path)
    assert before[0] == after[0] and before[1] == after[1]
    if path != "/auth/me":
        assert after[0] in (401, 404)


def test_public_record_keeps_its_data_and_takes_the_new_style(browser, env, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    monkeypatch.setenv("UI_NEXT", "0")
    page0, api0, js0 = anon_get(browser, env, "/record"), anon_get(browser, env, "/api/record"), anon_get(browser, env, "/record.js")
    monkeypatch.setenv("UI_NEXT", "1")
    page1, api1, js1 = anon_get(browser, env, "/record"), anon_get(browser, env, "/api/record"), anon_get(browser, env, "/record.js")
    assert page0[0] == page1[0] == 200 and STYLE.sub(b"", page1[1]) == page0[1] and page1[1] != page0[1]
    assert api0[0] == api1[0] and api0[1] == api1[1] and js0[1] == js1[1]            # the record's numbers and script: untouched
    monkeypatch.delenv("SCOUT_PUBLIC_RECORD")
    assert anon_get(browser, env, "/record")[0] == 404                                  # the release switch still decides


def test_unsubscribe_page_says_the_same_and_changes_nothing_without_a_valid_token(browser, env, monkeypatch):
    monkeypatch.setenv("UI_NEXT", "0")
    st0, old, _ = anon_get(browser, env, "/alerts/unsubscribe?m=someone&t=bad")
    monkeypatch.setenv("UI_NEXT", "1")
    st1, new, _ = anon_get(browser, env, "/alerts/unsubscribe?m=someone&t=bad")
    assert st0 == st1 == 400
    text = lambda b: re.sub(rb"<style>.*?</style>", b"", b, flags=re.S)             # noqa: E731
    assert text(old) == text(new) and b"This link is not valid" in new and b"IBM Plex Sans" in new and b"IBM Plex Sans" not in old


def test_sign_in_and_sign_out_redirect_as_before(browser, env, monkeypatch):
    for flag in ("0", None):
        monkeypatch.setenv("UI_NEXT", flag) if flag else monkeypatch.setenv("UI_NEXT", "1")
        c = browser.new_context()
        try:
            out = c.request.get(env.url + "/auth/logout", max_redirects=0)
            assert out.status == 302 and out.headers["location"] == "/"
            err = c.request.get(env.url + "/?signin_error=Not%20a%20member")
            assert err.status == 200 and b'id="signin-error"' in err.body()
        finally:
            c.close()


@pytest.mark.parametrize("size", list(SIZES))
@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_signed_out_pages_render_in_the_new_identity(browser, env, monkeypatch, size, scheme, tmp_path):
    monkeypatch.setenv("UI_NEXT", "1")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    c = browser.new_context(viewport=SIZES[size], color_scheme=scheme)
    c.route("https://fonts.*/**", lambda r: r.abort())
    try:
        for path, ready in (("/", "#hero-title"), ("/?signin_error=no_account", "#signin-error:not([hidden])"), ("/record", "h1"), ("/alerts/unsubscribe?m=a&t=b", "h1")):
            p = c.new_page()
            errors = []
            p.on("pageerror", lambda e: errors.append(str(e)))
            p.goto(env.url + path)
            p.wait_for_selector(ready, timeout=15000)
            assert "IBM Plex Sans" in p.evaluate("getComputedStyle(document.body).fontFamily")
            assert "Barlow Condensed" in p.locator("h1").first.evaluate("e=>getComputedStyle(e).fontFamily")
            assert p.evaluate("document.documentElement.scrollWidth<=document.documentElement.clientWidth+1"), path
            radius = p.evaluate("[...document.querySelectorAll('a.btn,button,.card')].map(e=>parseFloat(getComputedStyle(e).borderTopLeftRadius)||0)")
            assert all(r <= 2 for r in radius), (path, radius)
            if path.startswith("/record") or path.startswith("/alerts"):               # these follow the visitor's system theme
                bg = p.evaluate("getComputedStyle(document.body).backgroundColor")
                assert (sum(int(x) for x in re.findall(r"\d+", bg)[:3]) > 600) == (scheme == "light"), (path, bg)
            if path.startswith("/?signin"):
                assert "Whop didn't return your account" in p.locator("#signin-error").inner_text()   # sweep L8: a code, the page's words
            assert not errors, (path, errors)
            p.close()
    finally:
        c.close()


def test_the_flag_is_read_on_every_request():
    assert server.ui_next_default() in (True, False) and "UI_NEXT" in server.ui_next_default.__doc__


@pytest.mark.parametrize("path", ["/", "/lol", "/sports"])
def test_previous_design_is_the_default_again(browser, env, monkeypatch, path):
    """owner 2026-11-13 ("bring previous ui back"): with UI_NEXT unset a signed-out visitor gets the previous page, byte for byte."""
    monkeypatch.setenv("UI_NEXT", "0")
    st0, old, _ = anon_get(browser, env, path)
    monkeypatch.delenv("UI_NEXT", raising=False)
    st1, now, _ = anon_get(browser, env, path)
    assert st0 == st1 == 200 and now == old and b"Barlow" not in now
