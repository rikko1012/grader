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
    monkeypatch.setenv("SCOUT_UPGRADE", "off")      # the page upgrade has its own gate (tests/test_upgrade_gate.py)
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
    """Sign-in refresh (owner 2026-11-06, "fix the sign in page now"): the page describes the product as it is live - one board
    for four sports, the books it reads, hit rates, the "Why" card, HLTV map chances, line history, the +EV board, Slip Studio,
    the Genie and the public record - and none of the retired claims (letter range and units, The Odds API, the Results
    ledger, the long per-sport tool lists). No numbers about accuracy, no hedge labels."""
    page = landing()
    s = page.decode("utf-8")
    assert "{{JOIN_URL}}" not in s and s.count('href="https://whop.com/joined/1time-test/?a=1&amp;b=2"') >= 3
    assert s.count('href="/auth/login"') >= 4 and s.count('href="/record"') >= 2
    for sec in ('id="features"', 'id="how"', 'id="signin-error"'):
        assert sec in s
    text = _text(page)
    for need in ("1time Scout", "CS2", "League of Legends", "NBA", "NFL", "Every player prop. One board.", "Slips built for you", "Get your slips",
                 "Sign in with Whop", "Join on Whop", "Four sports, one board", "letter grade",
                 "PrizePicks", "Underdog", "Sleeper", "Dabble", "FanDuel", "BetRivers", "Kalshi", "Polymarket",
                 "L5, L10, L20, head-to-head and season", "Why Scout leans Over or Under", "HLTV map chances",
                 "Line history", "+EV board", "Slip Studio", "break even", "Slips save to your account", "Ask the Genie",
                 "The record is public", "How it works", "One membership"):
        assert need in text, need
    for gone in ("A+ to B", "units", "The Odds API", "Results ledger", "Track pick", "Maps 1", "15 props", "Thunderpick",
                 "Slate", "Matchroom", "Sheets", "logged before the game and settled"):
        assert gone not in text, gone
    low = text.lower()
    for bad in (r"financial advice", r"betting advice", r"research tool", r"not advice", r"guarantee", r"\block",
                r"\blocks\b", r"win[- ]rate", r"accura", r"profit", r"\broi\b", r"free money", r"sure thing", r"can'?t lose",
                r"\bbeat\b", r"unconfirmed", r"not confirmed", r"uncertain", r"unsure", r"tentative", r"provisional", r"beta\b",
                r"\bmaybe\b", r"research estimate", r"\bheld\b", r"\d+(\.\d+)?\s*%"):
        assert not re.search(bad, low), bad
    assert "<script src" not in s and "url(" not in s                          # no external scripts, no CSS images
    # 2026-11-16 refresh: the product is shown with real screenshots, served from this app only (never a remote image)
    assert set(re.findall(r'<img src="([^"]+)"', s)) <= {"/landing/board.webp", "/landing/card.webp", "/landing/odds.webp", "/landing/slips.webp", "/landing/slip.webp"}
    assert "{{PRICE_LABEL}}" not in s and "$" not in text                     # no price unless LANDING_PRICE is set


def test_landing_uses_the_member_app_design():
    """The front door of the same app: the hub's charcoal / white / blue tokens and fonts (web/upgrade-hub.css, web/upgrade.css)
    and the hub sidebar's brand block - none of the old orange and black, none of the rejected gold."""
    s = (server.WEB / "landing.html").read_text(encoding="utf-8")
    app = ((server.WEB / "upgrade-hub.css").read_text(encoding="utf-8") + (server.WEB / "upgrade.css").read_text(encoding="utf-8")).lower()
    css = re.search(r"(?s)<style>(.*?)</style>", s).group(1).lower()
    for token in ("#101215", "#121519", "#161c24", "#303d4e", "#2b323c", "#435165", "#f4f6fa", "#b3becb", "#77adff",
                  "#347ff0", "#20344e", "#4775aa", "#dbeaff"):
        assert token in css, token
    for c in set(re.findall(r"#[0-9a-f]{6}\b", css)):        # every colour on the page is one the app already uses
        assert c in app, c
    for old in ("#ff7a1a", "#ffa53a", "#f5c842", "#f5d77b", "#f5c542", "chakra petch", "inter,"):
        assert old not in css, old
    assert "'outfit'" in css and "'dm mono'" in css
    font = re.search(r'<link href="(https://fonts\.googleapis\.com/[^"]+)" rel="stylesheet">', s).group(1)
    assert font in (server.WEB / "index.html").read_text(encoding="utf-8")   # the font request the member page already makes
    assert '<span aria-hidden="true">1T</span><b>1TIME <em>SCOUT</em></b>' in s


@pytest.fixture
def big_queue(monkeypatch):
    """As the running server sets it (server.py __main__): hundreds of connections may wait to be accepted."""
    monkeypatch.setattr(ThreadingHTTPServer, "request_queue_size", 2048)


def test_700_signed_out_visitors_at_once_get_one_shared_page(big_queue, srv):
    """700 signed-out visitors on / at the same moment: every answer is 200 with the same bytes and ETag."""
    from concurrent.futures import ThreadPoolExecutor
    want = landing()

    def one(_):
        for attempt in range(3):
            try:
                return get(srv, "/")
            except OSError:
                time.sleep(0.2 * (attempt + 1))
        return get(srv, "/")

    with ThreadPoolExecutor(max_workers=700) as pool:
        out = list(pool.map(one, range(700)))
    assert len(out) == 700 and all(st == 200 and body == want for st, _h, body in out)
    assert len({h["etag"] for _st, h, _b in out}) == 1 and landing() is want


def test_landing_bytes_are_one_object_per_version(srv, monkeypatch):
    a, b = landing(), landing()
    assert a is b
    monkeypatch.setenv("WHOP_UPGRADE_URL", "https://whop.com/other/")
    c = landing()
    assert c is not a and b"https://whop.com/other/" in c


def test_landing_price_only_when_set_and_screens_are_public(srv, monkeypatch):
    """LANDING_PRICE puts the price on the Join buttons (read per request, escaped); unset shows none. The screenshots are
    public webp with a long cache, and only the three named files are served."""
    monkeypatch.setenv("LANDING_PRICE", "$24.99/month <b>")
    s = landing().decode("utf-8")
    assert s.count("Join on Whop · $24.99/month &lt;b&gt;") == 2 and "<b>\"" not in s
    monkeypatch.delenv("LANDING_PRICE")
    assert "· $" not in landing().decode("utf-8")
    for name in ("board", "card", "odds", "slips", "slip"):
        st, h, body = get(srv, f"/landing/{name}.webp")
        assert st == 200 and h["content-type"] == "image/webp" and "max-age=86400" in h["cache-control"] and body[:4] == b"RIFF"
    assert get(srv, "/landing/../server.py")[0] != 200 and get(srv, "/landing/x.webp")[0] != 200
