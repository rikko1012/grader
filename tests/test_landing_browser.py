"""The signed-out landing page (web/landing.html) in a real browser against a local server.Handler, signed out, at 360, 390 and
1440 px: no sideways scroll, every link and button at least 44 px tall, Sign in goes to /auth/login, Join goes to the Whop URL,
and the page asks for nothing beyond this server and the app's own Google Fonts. Skipped where Playwright or Chromium is missing.
LANDING_SHOTS=<folder> keeps a full-page screenshot per width (landing-<width>-<n>rev.png) and the first screens, taken with every
reveal-on-scroll section shown and every entrance animation finished."""
import os
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

import server
import whop_auth

sync_api = pytest.importorskip("playwright.sync_api")

JOIN = "https://whop.com/joined/1time-test/"
SIZES = ({"width": 360, "height": 780}, {"width": 390, "height": 844}, {"width": 768, "height": 1024}, {"width": 1280, "height": 800},
         {"width": 1440, "height": 900})
FONT_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com"}          # the same family request the member pages make
SHOTS = Path(os.environ.get("LANDING_SHOTS") or "")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f"chromium missing: {e}")
        yield b
        b.close()


@pytest.fixture
def base(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))                 # an empty reviews store unless a test seeds one
    monkeypatch.delenv("REVIEWS_PAGE", raising=False)
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)
    monkeypatch.setenv("SESSION_SECRET", "landing-browser-secret")
    monkeypatch.setenv("LANDING_JOIN_URL", JOIN)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0])):
        monkeypatch.setattr(server, name, val)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def settle(page, scroll=False):
    """Every reveal-on-scroll section shown and every finite animation / transition finished (the hero's endless glow is
    skipped): tap sizes and screenshots are read from the settled page, never mid entrance. scroll=True first scrolls the
    page through so the real observers fire (record counter, comparison rows), then returns to the top."""
    if scroll:
        h = page.evaluate("document.documentElement.scrollHeight")
        for y in range(0, h, 500):
            page.evaluate(f"window.scrollTo(0, {y})")
            page.wait_for_timeout(60)
        page.evaluate("window.scrollTo(0, 0)")
        # screenshots: stop the hero's example card on its first example (its own sport tab stops the rotation) so the picture
        # never catches it mid-swap, then wait for its number
        page.evaluate("(b => b && b.click())(document.querySelector('#hc-nav button'))")
        page.wait_for_function("(document.getElementById('hc-pct') || {textContent: 'x'}).textContent.trim() !== ''", timeout=10000)
        page.wait_for_timeout(900)
    page.evaluate("""async () => {
        document.querySelectorAll('.rv, #steps, .gd-plot').forEach(n => n.classList.add('in'));
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
        const finite = () => document.getAnimations().filter(a => {
          const t = a.effect && a.effect.getComputedTiming(); return a.playState === 'running' && t && isFinite(t.endTime);
        });
        for (let i = 0; i < 5 && finite().length; i++) await Promise.all(finite().map(a => a.finished.catch(() => null)));
      }""")


def seed(n):
    """n published member reviews (newest = the last one written), ratings 5,4,5,3,..."""
    import reviews
    bodies = ("Board is clean and the reasons read well, I check it before every slate.", "Love having every app's line in one place.",
              "Slip Studio saves me time every night and the cards are quick to read on my phone.", "Good tool, the player cards load fast.")
    for i in range(n):
        reviews.upsert(f"user_rv{i}", f"Member {chr(65 + i)}", {"stars": (5, 4, 5, 3)[i % 4], "title": "", "body": bodies[i % 4]})


@pytest.mark.parametrize("nrev", [0, 12], ids=lambda n: f"{n}rev")
@pytest.mark.parametrize("size", SIZES, ids=lambda s: str(s["width"]))
def test_landing_fits_every_width_with_big_targets_and_the_right_links(browser, base, size, nrev):
    seed(nrev)
    ctx = browser.new_context(viewport=size)
    page = ctx.new_page()
    hosts, errors = [], []
    page.on("request", lambda r: hosts.append(urlparse(r.url).hostname))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/", wait_until="load")
    page.wait_for_timeout(300)
    if nrev:
        page.wait_for_selector("#rvq:not([hidden])")
    settle(page, scroll=bool(SHOTS.parts))
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"landing-{size['width']}-{nrev}rev.png"), full_page=True)
    assert not errors
    assert page.evaluate("document.documentElement.scrollWidth") <= size["width"]
    assert page.evaluate("document.body.scrollWidth") <= size["width"]
    # no element pokes out sideways either (an overflow hidden by clip would still look cut off)
    wide = page.evaluate("""w => [...document.querySelectorAll('body *')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && (r.right > w + 0.5 || r.left < -0.5) && getComputedStyle(e).position !== 'absolute'
      }).map(e => e.tagName + '.' + e.className)""", size["width"])
    assert not wide, wide
    small = page.evaluate("""() => [...document.querySelectorAll('a[href],button')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && r.height && getComputedStyle(e).visibility !== 'hidden' && (r.height < 44 || r.width < 44)
      }).map(e => e.textContent.trim() + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height))""")
    small = [s for s in small if not s.startswith("Skip to content")]    # the skip link sits off-screen until focused
    assert not small, small
    signin = page.locator("a", has_text="Sign in with Whop").first
    join = page.locator("a", has_text="Join on Whop").first
    assert signin.get_attribute("href") == "/auth/login" and signin.is_visible()
    assert join.get_attribute("href") == JOIN and join.is_visible()
    # both buttons sit in the first screen, on a phone too
    for el in (signin, join):
        assert el.bounding_box()["y"] + el.bounding_box()["height"] <= size["height"]
    assert set(hosts) <= {"127.0.0.1"} | FONT_HOSTS, set(hosts)
    ctx.close()


def test_a_sign_in_error_shows_as_text(browser, base):
    ctx = browser.new_context(viewport=SIZES[1])
    page = ctx.new_page()
    page.goto(base + "/?signin_error=" + "<b>Whop%20said%20no</b>")
    el = page.locator("#signin-error")
    assert el.is_visible() and el.text_content() == "Sign-in did not complete - please try again"     # sweep L8: never URL text
    page.goto(base + "/?signin_error=no_account")
    assert el.is_visible() and el.text_content() == "Whop didn't return your account - please try again"
    ctx.close()


# ---------------------------------------------------------------- reviews carousel under the headline (owner 2026-11-19)
def _open(browser, base, size=SIZES[-1], **ctx_kw):
    ctx = browser.new_context(viewport=size, **ctx_kw)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.clock.install()
    page.goto(base + "/", wait_until="load")
    page.clock.run_for(500)
    return ctx, page, errors


def _idx(page):
    return page.evaluate("+document.getElementById('rvq').getAttribute('data-i')")


@pytest.mark.parametrize("size", (SIZES[1], SIZES[-1]), ids=lambda s: str(s["width"]))
def test_carousel_under_the_headline_advances_and_pauses(browser, base, size):
    seed(3)
    ctx, page, errors = _open(browser, base, size)
    page.wait_for_selector("#rvq:not([hidden])")
    box = lambda sel: page.locator(sel).bounding_box()
    h1, rv, lede = box("#hero-title"), box("#rvq"), box(".lede")
    assert h1["y"] + h1["height"] <= rv["y"] + 0.5 and rv["y"] + rv["height"] <= lede["y"] + 0.5
    assert rv["height"] <= (64 if size["width"] > 640 else 100)          # slim: about one line on a desktop, two on a phone
    fs = lambda sel: page.evaluate(f"parseFloat(getComputedStyle(document.querySelector('{sel}')).fontSize)")
    assert fs("#rvq .rvq-s.on blockquote") < fs(".lede")
    assert page.locator("#rvq .rvq-s").count() == 3 and page.locator("#rvq .rvq-s.on").count() == 1
    assert page.locator("#rvq .rvq-s.on figcaption").text_content().startswith("Member C. \u00b7 ")     # newest first, name and date
    assert page.locator("#rvq-who").text_content() == page.locator("#rvq .rvq-s.on figcaption").text_content()
    assert page.locator("#rvq-all").text_content() == "All reviews" and page.locator("#rvq-all").get_attribute("href") == "/reviews"
    # quieter than the sub-copy: the quote in --faint, name and date fainter still, the link plain until hovered or focused
    css = lambda sel, prop: page.evaluate(f"getComputedStyle(document.querySelector('{sel}')).{prop}")
    lum = lambda c: sum(float(v) for v in re.findall(r"[\d.]+", c)[:3]) * (255 if c.startswith("color(") else 1)
    assert lum(css("#rvq .rvq-s.on blockquote", "color")) < lum(css(".lede", "color"))
    assert lum(css("#rvq-who", "color")) < lum(css("#rvq .rvq-s.on blockquote", "color"))
    assert css("#rvq-all", "fontWeight") == "400" and css("#rvq-all", "color") == css("#rvq .rvq-s.on blockquote", "color")
    away = (5, size["height"] - 5)
    # advances on its own
    assert _idx(page) == 0
    page.mouse.move(*away)
    page.clock.run_for(6000)
    assert _idx(page) == 1
    # holds while hovered
    page.hover("#rvq-stage")
    page.clock.run_for(20000)
    assert _idx(page) == 1
    page.mouse.move(*away)
    page.clock.run_for(6000)
    assert _idx(page) == 2
    # holds while focused (keyboard); the arrows still step
    page.focus("#rvq-next")
    page.clock.run_for(20000)
    assert _idx(page) == 2
    page.keyboard.press("Enter")
    assert _idx(page) == 0
    page.focus(".ctas a")
    page.clock.run_for(6000)
    assert _idx(page) == 1
    # wraps backwards
    page.locator("#rvq-prev").click()
    page.locator("#rvq-prev").click()
    assert _idx(page) == 2
    assert not errors
    ctx.close()


def test_carousel_swipes_on_a_phone(browser, base):
    seed(3)
    ctx, page, errors = _open(browser, base, SIZES[1], has_touch=True)
    page.wait_for_selector("#rvq:not([hidden])")
    b = page.locator("#rvq-stage").bounding_box()
    y = b["y"] + b["height"] / 2
    page.mouse.move(b["x"] + b["width"] - 10, y)
    page.mouse.down()
    page.mouse.move(b["x"] + 20, y, steps=5)
    page.mouse.up()
    assert _idx(page) == 1
    page.mouse.move(b["x"] + 20, y)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] - 10, y, steps=5)
    page.mouse.up()
    assert _idx(page) == 0
    assert not errors
    ctx.close()


def test_carousel_reduced_motion_starts_paused(browser, base):
    """Reduced motion: the carousel starts paused (the button offers Play); the arrows step; Play starts the rotation."""
    seed(3)
    ctx, page, errors = _open(browser, base, reduced_motion="reduce")
    page.wait_for_selector("#rvq:not([hidden])")
    pp = page.locator("#rvq-pp")
    assert pp.get_attribute("aria-pressed") == "true" and pp.get_attribute("aria-label") == "Play reviews"
    page.mouse.move(5, 895)
    page.clock.run_for(30000)
    assert _idx(page) == 0
    page.locator("#rvq-next").click()
    assert _idx(page) == 1
    pp.click()
    page.mouse.move(5, 895)
    assert pp.get_attribute("aria-pressed") == "false" and pp.get_attribute("aria-label") == "Pause reviews"
    page.clock.run_for(6000)
    assert _idx(page) == 2
    assert not errors
    ctx.close()


@pytest.mark.parametrize("size", (SIZES[0], SIZES[-1]), ids=lambda s: str(s["width"]))
def test_carousel_pause_button(browser, base, size):
    """WCAG 2.2.2: a 44 px Pause / Play toggle in the carousel controls. Pause holds for good (hover and focus leaving do not
    restart it); Play resumes; it works from the keyboard."""
    seed(3)
    ctx, page, errors = _open(browser, base, size)
    page.wait_for_selector("#rvq:not([hidden])")
    pp = page.locator("#rvq-pp")
    b = pp.bounding_box()
    assert b["width"] >= 44 and b["height"] >= 44 and pp.is_visible()
    assert pp.get_attribute("aria-pressed") == "false" and pp.get_attribute("aria-label") == "Pause reviews"
    away = (5, size["height"] - 5)
    pp.click()
    page.mouse.move(*away)
    page.locator(".ctas a").first.focus()
    assert pp.get_attribute("aria-pressed") == "true" and pp.get_attribute("aria-label") == "Play reviews"
    page.clock.run_for(30000)
    assert _idx(page) == 0
    page.locator("#rvq-next").click()                    # the arrows still step while paused
    page.mouse.move(*away)
    page.clock.run_for(30000)
    assert _idx(page) == 1
    pp.focus()
    page.keyboard.press("Enter")                          # Play from the keyboard: plays even while the button keeps focus
    assert pp.get_attribute("aria-pressed") == "false"
    page.clock.run_for(6000)
    assert _idx(page) == 2
    assert not errors
    ctx.close()


def test_carousel_leads_with_the_newest_good_reviews(browser, base):
    """The carousel shows the newest 4-5 star reviews first, then the rest newest first; /api/reviews (the /reviews page's list)
    keeps plain newest first, every rating."""
    import reviews
    for i, (stars, name) in enumerate(((5, "Ann A"), (2, "Bo B"), (4, "Cy C"), (3, "Di D"), (5, "Ed E"), (1, "Flo F"))):
        reviews.upsert(f"user_o{i}", name, {"stars": stars, "title": "", "body": f"review number {i} from {name}"})
    con = reviews._connect()                              # distinct created_at: one second apart, oldest first
    for i in range(6):
        con.execute("UPDATE reviews SET created_at=?, updated_at=? WHERE user_id=?", (1_700_000_000 + i, 1_700_000_000 + i, f"user_o{i}"))
    con.commit()
    con.close()
    reviews._bump()
    ctx, page, errors = _open(browser, base)
    page.wait_for_selector("#rvq:not([hidden])")
    order = page.evaluate("[...document.querySelectorAll('#rvq .rvq-s figcaption')].map(f => f.textContent.split(' \u00b7 ')[0])")
    assert order == ["Ed E.", "Cy C.", "Ann A.", "Flo F.", "Di D.", "Bo B."], order
    api = page.evaluate("fetch('/api/reviews?sort=new&page=0').then(r => r.json()).then(d => d.reviews.map(r => r.name))")
    assert api == ["Flo F.", "Ed E.", "Di D.", "Cy C.", "Bo B.", "Ann A."], api
    assert not errors
    ctx.close()


def seed_long():
    import reviews
    long_body = ("The board is the first thing I open every night. " * 30)[:1400] + "\n\nSecond paragraph\nthird line."
    reviews.upsert("user_l1", "Alexandria Montgomery-Fitzgerald Wellington", {"stars": 4, "title": "", "body": long_body, "name_style": "full"})
    reviews.upsert("user_l2", "Pat Q", {"stars": 5, "title": "", "body": "x" * 600})
    reviews.upsert("user_l3", "Sam K", {"stars": 3, "title": ("Title " * 20)[:80], "body": "short body here."})


@pytest.mark.parametrize("kind", ("short", "long"))
@pytest.mark.parametrize("size", (SIZES[0], SIZES[1], SIZES[-1]), ids=lambda s: str(s["width"]))
def test_carousel_fills_without_moving_the_join_button(browser, base, size, kind):
    """No layout shift: the server marks <html> has-reviews from its cached summary, so the carousel's height is held at first
    paint. The reviews answer is held back, Join's top is measured, the answer is let through, and Join must not move (2 px at
    most) - with short reviews and long ones, on every slide."""
    seed(3) if kind == "short" else seed_long()
    ctx = browser.new_context(viewport=size)
    page = ctx.new_page()
    errors, held = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route(lambda url: "/api/reviews" in url, lambda route: held.append(route))
    page.goto(base + "/", wait_until="load")
    page.evaluate("document.fonts.ready.then(() => 1)")
    page.wait_for_timeout(200)
    assert page.evaluate("document.documentElement.classList.contains('has-reviews')")
    assert page.locator("#rvq").is_hidden()
    join = page.locator(".ctas a").first
    y0 = join.bounding_box()["y"]
    assert held, "the reviews request was not made"
    for r in held:
        r.continue_()
    page.wait_for_selector("#rvq:not([hidden])")
    page.wait_for_timeout(100)
    tops = [join.bounding_box()["y"]]
    for _ in range(3):
        page.locator("#rvq-next").click()
        tops.append(join.bounding_box()["y"])
    assert all(abs(y - y0) <= 2 for y in tops), (y0, tops)
    assert not errors
    ctx.close()


def test_no_reserved_gap_without_reviews_or_when_the_list_fails(browser, base):
    """No reviews: no has-reviews class, no gap. Reviews counted but the list request failing: the reserved height is given back."""
    ctx, page, errors = _open(browser, base)
    assert not page.evaluate("document.documentElement.classList.contains('has-reviews')")
    lede0 = page.locator(".lede").bounding_box()["y"]
    ctx.close()
    seed(3)
    ctx = browser.new_context(viewport=SIZES[-1])
    page = ctx.new_page()
    page.route(lambda url: "/api/reviews" in url, lambda route: route.fulfill(status=503, body="{}"))
    page.goto(base + "/", wait_until="load")
    page.wait_for_function("!document.documentElement.classList.contains('has-reviews')")
    assert page.locator("#rvq").is_hidden() and abs(page.locator(".lede").bounding_box()["y"] - lede0) <= 1
    ctx.close()


def test_carousel_hidden_without_reviews(browser, base):
    ctx, page, errors = _open(browser, base)
    page.clock.run_for(2000)
    page.wait_for_timeout(300)
    assert page.locator("#rvq").is_hidden() and page.locator("#rvq .rvq-s").count() == 0
    assert page.locator("#rvq").bounding_box() is None
    assert not errors
    ctx.close()


@pytest.mark.parametrize("n,rated", [(3, False), (9, False), (12, True)])
def test_carousel_rating_only_from_ten_reviews(browser, base, n, rated):
    seed(n)
    ctx, page, errors = _open(browser, base)
    page.wait_for_selector("#rvq:not([hidden])")
    link = page.locator("#rvq-all").text_content()
    assert page.locator("#rvq .rvq-s").count() == min(n, 8)
    is_rated = page.locator("#proofs").evaluate("e => e.classList.contains('rated')")
    if rated:
        assert link.startswith("\u2605 ") and link.endswith(" \u00b7 All reviews") and is_rated
    else:
        assert link == "All reviews" and not is_rated
    assert not errors
    ctx.close()


def test_carousel_shows_member_text_as_text(browser, base):
    import reviews
    evil = '<img src=x onerror="window.__pwn=1"><script>window.__pwn=2</script> great board'
    reviews.upsert("user_evil", "<b>Bad</b> <i>Name</i>", {"stars": 5, "title": "", "body": evil})
    ctx, page, errors = _open(browser, base)
    page.wait_for_selector("#rvq:not([hidden])")
    page.clock.run_for(1000)
    assert page.locator("#rvq img, #rvq script, #rvq b").count() == 0
    q = page.locator("#rvq .rvq-s.on blockquote").text_content()
    assert "<img src=x" in q and "<script>" in q
    assert "<b>Bad" in page.locator("#rvq .rvq-s.on figcaption").text_content()
    assert page.evaluate("window.__pwn === undefined")
    assert not errors
    ctx.close()


@pytest.mark.parametrize("size", (SIZES[1], SIZES[-1]), ids=lambda s: str(s["width"]))
@pytest.mark.parametrize("nrev", [0, 3, 12], ids=lambda n: f"{n}rev")
def test_first_screen_with_reviews(browser, base, size, nrev):
    """The first screen at 390x844 and 1440x900 with 0, 3 and 12 reviews (LANDING_SHOTS keeps the pictures): Join and Sign in
    still sit in the first screen with the carousel in place."""
    seed(nrev)
    ctx = browser.new_context(viewport=size)
    page = ctx.new_page()
    page.goto(base + "/", wait_until="load")
    if nrev:
        page.wait_for_selector("#rvq:not([hidden])")
    page.wait_for_timeout(300)
    settle(page, scroll=bool(SHOTS.parts))
    for txt in ("Join on Whop", "Sign in with Whop"):
        b = page.locator("a", has_text=txt).first.bounding_box()
        assert b["y"] + b["height"] <= size["height"], txt
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / f"first-{size['width']}x{size['height']}-{nrev}rev.png"))
    ctx.close()


@pytest.mark.parametrize("size", (SIZES[0], SIZES[-1]), ids=lambda s: str(s["width"]))
def test_carousel_clamps_real_shaped_reviews(browser, base, size):
    """Long reviews stay one line on a desktop and two on a phone: a 1,500-character body with line breaks, an unbroken string,
    an 80-character title and a long full name - no sideways overflow, the same height on every slide."""
    seed_long()
    ctx, page, errors = _open(browser, base, size)
    page.wait_for_selector("#rvq:not([hidden])")
    line = page.evaluate("parseFloat(getComputedStyle(document.querySelector('#rvq .rvq-s.on blockquote')).lineHeight)")
    lines = 2 if size["width"] <= 640 else 1
    heights = set()
    if SHOTS.parts:
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.clock.run_for(1000)
        page.screenshot(path=str(SHOTS / f"first-{size['width']}x{size['height']}-long-reviews.png"))
    for i in range(3):
        assert _idx(page) == i
        q = page.locator("#rvq .rvq-s.on blockquote").bounding_box()
        assert q["height"] <= line * lines + 1, (i, q["height"])
        heights.add(round(page.locator("#rvq").bounding_box()["height"]))
        assert page.evaluate("document.documentElement.scrollWidth") <= size["width"]
        r = page.locator("#rvq").bounding_box()
        assert r["x"] >= 0 and r["x"] + r["width"] <= size["width"]
        page.locator("#rvq-next").click()
    assert len(heights) == 1, heights
    assert not errors
    ctx.close()


def test_carousel_resumes_after_an_arrow_click(browser, base):
    """A mouse click on an arrow steps once; rotation carries on after the pointer leaves (only keyboard focus holds it)."""
    seed(3)
    ctx, page, errors = _open(browser, base)
    page.wait_for_selector("#rvq:not([hidden])")
    page.locator("#rvq-next").click()
    assert _idx(page) == 1
    page.mouse.move(5, 895)
    page.clock.run_for(6000)
    assert _idx(page) == 2
    assert not errors
    ctx.close()
