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
    monkeypatch.setenv("LANDING_JOIN_URL", JOIN)
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.delenv("SPORTS_UI_ALLOW", raising=False)
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(server, "ADMIN_TOKEN", LOAD)
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_INFLIGHT", {})
    monkeypatch.setattr(server, "_ENC_LOCKS", {})
    monkeypatch.setattr(server, "_RES_BYTES", [0, 0.0])
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))                 # an empty reviews store unless a test seeds one
    monkeypatch.delenv("REVIEWS_PAGE", raising=False)
    monkeypatch.setattr(server, "_LANDING", {"raw": None, "url": None, "data": b""})
    monkeypatch.setattr(server, "_LANDING_RV", {"body": None, "has": False})
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
    for four sports, the books it reads, hit rates, the "Why" card, HLTV map chances, line history, the price gap board, Slip Studio,
    the Genie and the public record - and none of the retired claims (letter range and units, The Odds API, the Results
    ledger, the long per-sport tool lists). No numbers about accuracy, no hedge labels."""
    page = landing()
    s = page.decode("utf-8")
    assert "{{JOIN_URL}}" not in s and s.count('href="https://whop.com/joined/1time-test/?a=1&amp;b=2"') >= 3
    assert s.count('href="/auth/login"') >= 4 and s.count('href="/record"') >= 2
    for sec in ('id="features"', 'id="how"', 'id="signin-error"'):
        assert sec in s
    text = _text(page)
    for need in ("Prop Grader", "CS2", "League of Legends", "NBA", "NFL", "Every player prop. One board.", "Slips built for you", "Get your slips",
                 "Sign in with Whop", "Join on Whop", "Four sports, one board", "letter grade",
                 "PrizePicks", "Underdog", "Sleeper", "Dabble", "FanDuel", "BetRivers", "Kalshi", "Polymarket",
                 "L5, L10, L20, head-to-head and season", "Why Grader leans Over or Under", "HLTV map chances",
                 "Line history", "Price gap board", "Slip Studio", "break even", "Slips save to your account", "Ask the Genie",
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
    assert set(re.findall(r'<img src="([^"]+)"', s)) <= {"/landing/board.webp", "/landing/card.webp", "/landing/odds.webp", "/landing/slips.webp", "/landing/slip.webp", "/landing/realslip-scout.webp", "/landing/realslip-pp.webp"}
    assert "{{PRICE_LABEL}}" not in s and "$" not in text                     # no price unless LANDING_PRICE is set
    assert "realslip-pp" not in s                  # owner 2026-11-16: the PrizePicks screenshot never comes back on the page


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
    monkeypatch.setenv("LANDING_JOIN_URL", "https://whop.com/other/")
    c = landing()
    assert c is not a and b"https://whop.com/other/" in c


def test_landing_join_goes_to_checkout_by_default(srv, monkeypatch):
    """Owner 2026-11-16: every Join button goes to Whop checkout, never the member app page that paywalls an ad visitor; the
    member upgrade link (WHOP_UPGRADE_URL) does not change it. Owner blueprint 2026-11-22: the checkout is the 1-day free trial
    (then $24.99/month), no longer paid Monthly checkout straight away."""
    monkeypatch.delenv("LANDING_JOIN_URL")
    monkeypatch.setenv("WHOP_UPGRADE_URL", "https://whop.com/1timeesports/exp_W6a3m1Ps4dxbCG/app/")
    s = landing().decode("utf-8")
    assert s.count('href="https://whop.com/checkout/plan_llXQ8ebwMzfRd"') >= 3 and "exp_W6a3m1Ps4dxbCG" not in s
    assert "plan_8d2GVuU7671mD" not in s


def test_landing_ad_safe_wording(srv):
    """Owner 2026-11-16, before ads: no offshore book named, no stake wording, and the review rating shows only from ten
    reviews. The slip example keeps its payout lines (owner asked for them back the same day)."""
    s = landing().decode("utf-8")
    text = _text(landing())
    for gone in ("Bovada", "stake", "+EV"):
        assert gone not in s, gone
    assert '"Payout if all "' in s and '"Flex pays "' in s
    assert "Price gap board" in text and "s.count >= 10 && s.average" in s


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


# ---------------------------------------------------------------- reviews carousel under the headline (owner 2026-11-19)
def _seed_reviews(n):
    import reviews
    bodies = ("Board is clean and the reasons read well.", "Love having every app's line in one place.",
              "Slip Studio saves me time every night.", "Cards are quick to read on my phone.")
    for i in range(n):
        reviews.upsert(f"user_rv{i}", f"Member {chr(65 + i)}", {"stars": (5, 4, 5, 3)[i % 4], "title": "", "body": bodies[i % 4]})


def test_reviews_carousel_sits_under_the_headline_and_stays_quiet():
    """The carousel is the first thing after the h1 and before the lede; hidden in the HTML until the script finds a review; built
    with textContent only (member text is user text); one reviews request (the cached list) feeds the carousel, the proof card and
    the reviews line; the rating shows only from ten reviews; the carousel leads with the newest 4-5 star reviews, then the rest."""
    s = (server.WEB / "landing.html").read_text(encoding="utf-8")
    h1, rvq, lede = s.index('<h1 id="hero-title">'), s.index('<div class="rvq" id="rvq"'), s.index('<p class="lede">')
    assert h1 < rvq < lede
    tag = s[rvq:s.index(">", rvq)]
    assert " hidden" in tag and 'aria-roledescription="carousel"' in tag
    assert s.count('href="/reviews"') >= 5                       # nav, proof card, record links, feature card... and All reviews
    js = s[s.index("/* ---------- member reviews: ONE request"):s.index("/* ---------- hero trust row")]
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "createTextNode(" in js
    assert s.count('"/api/reviews?sort=new&page=0"') == 1 and "/api/reviews/summary" not in s
    assert "s.count >= 10 && s.average" in js                      # carousel's rating, same rule as the proof card
    assert "RM.matches" in js                                      # reduced motion: no rotation
    css = re.search(r"(?s)<style>(.*?)</style>", s).group(1)
    size = lambda sel: float(re.search(re.escape(sel) + r"\{[^}]*font-size:([\d.]+)px", css).group(1))
    assert size(".rvq-s blockquote") < size(".lede") and size(".rvq-who") < size(".rvq-s blockquote")


def test_reviews_list_the_landing_reads_holds_700_signed_out_visitors(big_queue, srv, monkeypatch, tmp_path):
    """Every landing visitor now asks for /api/reviews?sort=new&page=0 (the existing cached route, no new route): 700 signed-out
    visitors at once get one shared answer, the same bytes and ETag, built once."""
    from concurrent.futures import ThreadPoolExecutor
    import reviews
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("REVIEWS_PAGE", raising=False)
    _seed_reviews(12)
    calls = [0]
    real = reviews.public_page

    def counted(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    monkeypatch.setattr(reviews, "public_page", counted)

    def one(_):
        for attempt in range(3):
            try:
                return get(srv, "/api/reviews?sort=new&page=0")
            except OSError:
                time.sleep(0.2 * (attempt + 1))
        return get(srv, "/api/reviews?sort=new&page=0")

    with ThreadPoolExecutor(max_workers=700) as pool:
        out = list(pool.map(one, range(700)))
    assert len(out) == 700 and all(st == 200 for st, _h, _b in out)
    assert len({b for _s, _h, b in out}) == 1 and len({h["etag"] for _s, h, _b in out}) == 1
    d = json.loads(out[0][2])
    assert d["summary"]["count"] == 12 and len(d["reviews"]) == 12 and calls[0] == 1


# ---------------------------------------------------------------- no layout shift: the reserved carousel height (2026-11-19)
HAS = b'<html lang="en" class="has-reviews">'


def test_landing_marks_has_reviews_only_when_a_review_is_published(srv, monkeypatch):
    """<html class="has-reviews"> (the carousel's height reserved at first paint) only when the cached summary counts a published
    review: none with no reviews, none with the reviews page switched off (its API 404s), and a write flips it at once."""
    import reviews
    assert HAS not in landing() and b'<html lang="en">' in landing()
    _seed_reviews(1)
    assert HAS in landing()
    monkeypatch.setenv("REVIEWS_PAGE", "0")
    assert HAS not in landing()
    monkeypatch.delenv("REVIEWS_PAGE")
    assert HAS in landing()
    reviews.delete_own("user_rv0")
    assert HAS not in landing()


def test_landing_review_flag_reads_the_cached_summary_never_per_request(srv, monkeypatch):
    """The flag comes from the shared /api/reviews/summary entry: many landing builds = one SQLite read until the next write;
    a failing read keeps the last answer and never fails the page."""
    import reviews
    _seed_reviews(2)
    calls = [0]
    real = reviews.summary

    def counted(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    monkeypatch.setattr(reviews, "summary", counted)
    for _ in range(50):
        assert HAS in landing()
    assert calls[0] == 1
    st, _h, body = get(srv, "/api/reviews/summary")             # the route answers from the same entry
    assert st == 200 and json.loads(body)["summary"]["count"] == 2 and calls[0] == 1

    def boom(*a, **k):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(reviews, "summary", boom)
    reviews._bump()                                             # a new generation: the next read fails
    assert HAS in landing()                                     # the last answer stands; the page is served


def test_700_signed_out_visitors_with_reviews_get_one_page_and_one_summary_read(big_queue, srv, monkeypatch):
    """700 signed-out visitors on / at once with reviews published: every answer is the same has-reviews page (one ETag), and
    the reviews summary is read once."""
    from concurrent.futures import ThreadPoolExecutor
    import reviews
    _seed_reviews(12)
    calls = [0]
    real = reviews.summary

    def counted(*a, **k):
        calls[0] += 1
        return real(*a, **k)
    monkeypatch.setattr(reviews, "summary", counted)

    def one(_):
        for attempt in range(3):
            try:
                return get(srv, "/")
            except OSError:
                time.sleep(0.2 * (attempt + 1))
        return get(srv, "/")

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as pool:
        out = list(pool.map(one, range(700)))
    took = time.time() - t0
    want = landing()
    assert len(out) == 700 and all(st == 200 and body == want for st, _h, body in out)
    assert HAS in want and len({h["etag"] for _st, h, _b in out}) == 1 and calls[0] == 1
    print(f"700 landing visitors with reviews: {took:.2f}s")
