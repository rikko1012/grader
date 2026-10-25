"""Signed-out landing page (2026-10-24). With Whop sign-in on, a visitor with no valid session cookie gets
web/landing.html on /, /index.html, /lol (when released) and /sports (when SPORTS_UI is set). Signed-in visitors
(members or not), the owner's load tests and sign-in-off installs get exactly the bytes they got before - the member
pages are untouched. The landing page describes the product with none of the owner's forbidden phrases."""
import http.client
import json
import re
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import server
import whop_auth

PAGES = ("/", "/index.html", "/lol", "/sports", "/sports/")
JOIN = "https://whop.com/joined/1time-test/?a=1&b=2"
LOAD = "L" * 40


@pytest.fixture
def srv(monkeypatch, tmp_path):
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)     # never a Whop call from a test
    monkeypatch.setenv("SESSION_SECRET", "landing-test-secret")
    monkeypatch.setenv("WHOP_UPGRADE_URL", JOIN)
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.delenv("SPORTS_UI_ALLOW", raising=False)
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(server, "ADMIN_TOKEN", LOAD)
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(server, "_ENC_LOCKS", {})
    monkeypatch.setattr(server, "_RES_BYTES", [0, 0.0])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()


def get(port, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


def cookie(ok=True, exp_in=3600):
    now = int(time.time())
    return {"Cookie": f"{whop_auth.COOKIE}=" + whop_auth._sign({"u": "user_t", "n": "T", "ok": ok, "chk": now,
                                                                 "exp": now + exp_in})}


def landing():
    return server.landing_page()


def before(monkeypatch, port, path, headers=None):
    """The same request through the pre-landing code path (the signed-out check switched off)."""
    with monkeypatch.context() as m:
        m.setattr(server.Handler, "signed_out", lambda self: False)
        return get(port, path, headers)


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("ok", [True, False])
def test_signed_in_pages_are_byte_identical(srv, monkeypatch, path, ok):
    st, h, body = get(srv, path, cookie(ok))
    st0, h0, body0 = before(monkeypatch, srv, path, cookie(ok))
    assert (st, body) == (st0, body0) and h.get("etag") == h0.get("etag")
    assert b"{{JOIN_URL}}" not in body and body != landing()
    if path in ("/", "/index.html"):
        assert body == server.sports_nav((server.WEB / "index.html").read_bytes())     # SPORTS_UI=1 adds its one link
    if path == "/lol":
        assert body == server.sports_nav((server.WEB / "lol.html").read_bytes())


@pytest.mark.parametrize("path", PAGES)
def test_load_test_and_sign_in_off_are_unchanged(srv, monkeypatch, path):
    assert get(srv, path, {"X-Load-Test": LOAD})[2] == before(monkeypatch, srv, path, {"X-Load-Test": LOAD})[2] != landing()
    monkeypatch.setattr(whop_auth, "enabled", lambda: False)
    assert get(srv, path)[2] == before(monkeypatch, srv, path)[2] != landing()


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("hdr", [None, {"Cookie": f"{whop_auth.COOKIE}=garbage.0000"}, "expired"])
def test_signed_out_visitors_get_the_landing_page(srv, path, hdr):
    if hdr == "expired":
        hdr = cookie(exp_in=-5)
    st, h, body = get(srv, path, hdr)
    assert st == 200 and body == landing() and h["content-type"].startswith("text/html")
    assert h["cache-control"] == "private, no-cache" and "set-cookie" not in h       # no cookie written or cleared
    st, h2, again = get(srv, path, {**(hdr or {}), "If-None-Match": h["etag"]})
    assert st == 304 and again == b""


def test_unreleased_routes_keep_their_old_answer(srv, monkeypatch):
    monkeypatch.setattr(server, "lol_public", lambda: False)
    assert get(srv, "/lol")[2] == before(monkeypatch, srv, "/lol")[2] != landing()
    monkeypatch.delenv("SPORTS_UI")
    for p in ("/sports", "/sports/"):
        assert get(srv, p)[2] == before(monkeypatch, srv, p)[2] != landing()
    assert get(srv, "/sports/app.js")[2] != landing()                   # assets never get the landing page


def _text(page):
    s = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", page.decode("utf-8"))
    return re.sub(r"\s+", " ", htmlunescape(re.sub(r"<[^>]+>", " ", s))).strip()


def htmlunescape(s):
    import html
    return html.unescape(s)


def test_landing_content_and_wording(srv):
    page = landing()
    s = page.decode("utf-8")
    assert "{{JOIN_URL}}" not in s and s.count('href="https://whop.com/joined/1time-test/?a=1&amp;b=2"') >= 2
    assert s.count('href="/auth/login"') >= 3 and "Sign in with Whop" in s and "Join on Whop" in s
    for sec in ('id="cs2"', 'id="lol"', 'id="sports"', 'id="how"'):
        assert sec in s
    text = _text(page)
    for need in ("1time Scout", "CS2", "League of Legends", "NBA", "NFL", "One membership", "Single Prop", "Slate",
                 "Prop Board", "Matchroom", "Map veto", "Map 1 and Map 2", "Slip studio", "Moneylines",
                 "kills, assists and deaths", "Prop board", "Player research", "Slip Studio", "Track pick", "My picks",
                 "Results", "How it works", "HLTV", "BO3.gg", "Leaguepedia", "ESPN", "nflverse", "Underdog",
                 "PrizePicks", "Kalshi", "Polymarket", "FanDuel", "BetRivers", "The Odds API", "Thunderpick",
                 "A+ to B–", "15 props", "L5", "L10", "L20", "logged before the game and settled"):
        assert need in text, need
    low = text.lower()
    for bad in (r"financial advice", r"betting advice", r"research tool", r"not advice", r"guarantee", r"\block",
                r"\blocks\b", r"win[- ]rate", r"profit", r"\broi\b", r"free money", r"sure thing", r"can'?t lose",
                r"unconfirmed", r"not confirmed", r"uncertain", r"unsure", r"tentative", r"provisional", r"beta\b",
                r"\bmaybe\b", r"research estimate"):
        assert not re.search(bad, low), bad
    assert "<script src" not in s                                         # no external scripts


def test_landing_bytes_are_one_object_per_version(srv, monkeypatch):
    a, b = landing(), landing()
    assert a is b
    monkeypatch.setenv("WHOP_UPGRADE_URL", "https://whop.com/other/")
    c = landing()
    assert c is not a and b"https://whop.com/other/" in c
