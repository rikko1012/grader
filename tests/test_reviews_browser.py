"""Member reviews (/reviews) in a real browser against a local server.Handler with fixture data (the shared fixtures of
tests/test_upgrade_browser.py): at 390 and 1280 px a signed-in fixture member posts a review with a photo, it shows with the
Verified member badge, member text stays text, the photo opens in the lightbox and Esc closes it, no sideways scroll, 44 px
targets and no console errors. Signed out: the sign-in link returns to /reviews. Skipped where Playwright or Chromium is missing."""
import io

import pytest
from PIL import Image

import reviews
import whop_auth
from tests.test_upgrade_browser import browser, env, no_h_scroll, shot  # noqa: F401

sync_api = pytest.importorskip("playwright.sync_api")

SIZES = ({"width": 390, "height": 844}, {"width": 1280, "height": 900})


def watch(p):
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.on("console", lambda m: p.errors.append(m.text) if m.type == "error" else None)
    return p


def new_ctx(browser, env, size, user=None):
    c = browser.new_context(viewport=size)
    c.route("https://fonts.*/**", lambda r: r.abort())
    if user:
        c.add_cookies([{"name": whop_auth.COOKIE, "value": f"good-{user}", "url": env.url}])
    return c


def small_targets(p):
    return p.evaluate("""() => [...document.querySelectorAll('main a[href], main button, .rate label, .names label, .addphoto')].filter(e => {
        const r = e.getBoundingClientRect(); return r.width && r.height && !e.closest('[hidden]') && (r.height < 44 || r.width < 44)
      }).map(e => (e.textContent || '').trim().slice(0, 30) + ' ' + Math.round(e.getBoundingClientRect().width) + 'x' + Math.round(e.getBoundingClientRect().height))""")


@pytest.mark.parametrize("size", SIZES, ids=lambda s: str(s["width"]))
def test_member_posts_a_review_with_a_photo(browser, env, monkeypatch, tmp_path, size):
    monkeypatch.setattr(reviews, "_RATE", {})
    monkeypatch.setattr(reviews, "MIN_FREE_BYTES", 0)
    photo = tmp_path / "pic.jpg"
    ex = Image.Exif()
    ex[0x8825] = {1: "N", 2: (51.0, 30.0, 12.0)}
    Image.new("RGB", (2400, 1800), (40, 140, 90)).save(photo, "JPEG", exif=ex.tobytes())
    c = new_ctx(browser, env, size, user=f"rv-member-{size['width']}")
    try:
        p = watch(c.new_page())
        p.goto(env.url + "/reviews")
        p.wait_for_selector("#review-form")
        assert p.locator(".empty").inner_text().startswith("No reviews yet")
        assert p.locator("text=Every review is posted, good or bad.").count() == 1
        p.locator(".rate label").nth(3).click()                          # 4 stars
        p.fill("#rv-title", "Good for <b>props</b>")
        text = "The hit rates and the Why card save me time.\n<img src=x onerror=alert(1)> stays text."
        p.fill("#rv-body", text)
        assert "/ 1500" in p.locator("#rv-count").inner_text()
        p.set_input_files("#rv-file", str(photo))
        p.wait_for_selector("#rv-photos img")
        assert p.locator("#rv-photos figure").count() == 1
        no_h_scroll(p)
        assert not small_targets(p), small_targets(p)
        p.click("#rv-post")
        p.wait_for_selector("#list .review")
        card = p.locator("#list .review").first
        assert "Verified member" in card.inner_text() and "Tester" in card.inner_text()
        assert card.locator(".body").inner_text() == text and card.locator(".body img").count() == 0   # member text stays text
        assert card.locator("h3").inner_text() == "Good for <b>props</b>" and card.locator("h3 b").count() == 0
        assert p.locator("#avg").inner_text() == "4.0" and "from 1 member review" in p.locator("#count").inner_text()
        assert p.locator("#write-title").inner_text() == "Your review" and p.locator("#rv-delete").count() == 1
        thumb = card.locator(".thumbs button").first
        thumb.click()
        p.wait_for_selector("#lightbox[open]")
        p.wait_for_function("document.querySelector('#lb-img').complete && document.querySelector('#lb-img').naturalWidth > 0")
        assert p.evaluate("document.querySelector('#lb-img').naturalWidth") <= reviews.FULL_PX
        assert p.evaluate("document.activeElement.id") == "lb-close"
        shot(p, "reviews-lightbox")
        p.keyboard.press("Escape")
        p.wait_for_function("!document.querySelector('#lightbox').open")
        assert p.evaluate("document.activeElement === document.querySelector('#list .thumbs button')")
        no_h_scroll(p)
        assert not small_targets(p), small_targets(p)
        shot(p, "reviews-member")
        # the saved photo carries no EXIF / GPS
        src = card.locator(".thumbs img").first.get_attribute("src").replace("_t.jpg", ".jpg")
        raw = c.request.get(env.url + src).body()
        assert b"Exif" not in raw and not dict(Image.open(io.BytesIO(raw)).getexif())
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: str(s["width"]))
def test_signed_out_visitor_reads_and_is_offered_sign_in(browser, env, size):
    c = new_ctx(browser, env, size)
    try:
        p = watch(c.new_page())
        p.goto(env.url + "/reviews")
        link = p.locator("#write a", has_text="Sign in with Whop")
        link.wait_for()
        assert link.get_attribute("href") == "/auth/login?next=reviews"
        assert p.locator("#review-form").count() == 0
        p.wait_for_selector(".empty")
        no_h_scroll(p)
        assert not small_targets(p), small_targets(p)
        assert not p.errors, p.errors
    finally:
        c.close()


def test_review_dates_are_the_utc_day_the_landing_states(browser, env, monkeypatch):
    """sweep L7: the landing (server-rendered) dates reviews by their UTC day ("All 15 were posted on Nov 16, 2026"); /reviews must show
    the same day in every time zone - a review written 2026-11-16T05:52Z reads Nov 16, 2026 in Chicago too, never Nov 15."""
    import json as _json
    monkeypatch.setattr(reviews, "_RATE", {})
    reviews.upsert("user_tz", "Member TZ", {"stars": 5, "title": "", "body": "The board and the player cards are quick to read on my phone."})
    c = browser.new_context(viewport=SIZES[1], timezone_id="America/Chicago")
    c.route("https://fonts.*/**", lambda r: r.abort())

    def early(route):
        resp = route.fetch()
        d = resp.json()
        for r in d.get("reviews") or []:
            r["created"] = "2026-11-16T05:52:31Z"
        route.fulfill(response=resp, body=_json.dumps(d), headers=dict(resp.headers, **{"content-type": "application/json"}))
    c.route("**/api/reviews?*", early)
    try:
        p = watch(c.new_page())
        p.goto(env.url + "/reviews")
        p.wait_for_selector(".when")
        assert p.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "America/Chicago"
        assert p.locator(".when").first.inner_text().startswith("Nov 16, 2026"), p.locator(".when").first.inner_text()
    finally:
        c.close()
