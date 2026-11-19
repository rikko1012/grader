"""Member reviews (/reviews, reviews.py) against a local server.Handler with a temporary data dir: every rating publishes, hidden
reviews leave the average, one review per member, photos re-encoded without EXIF/GPS, refusals answered before the body is read,
the switches, the owner tools behind the admin token header, and no Whop user id in any answer. Nothing reaches a network."""
import http.client
import io
import json
import socket
import struct
import threading
import zlib
from http.server import ThreadingHTTPServer

import pytest
from PIL import Image

import reviews
import server
import whop_auth

TOKEN = "test-admin-token-reviews"
NAMES = {"user_SECRETAAA": "Sam Kim", "user_SECRETBBB": "Lee Park", "user_SECRETCCC": "jordan", "free_SECRETDDD": "Free Person"}


@pytest.fixture
def srv(monkeypatch, tmp_path):
    for k in ("REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "REVIEWS_PAGE", "REVIEWS_WRITE", "REVIEWS_PHOTOS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": NAMES.get(v[5:], "Sam Kim"), "ok": not v[5:].startswith("free")}
                        if v and v.startswith("good-") else None)
    monkeypatch.setattr(whop_auth, "cookie_valid", lambda v: bool(v and v.startswith("good-")))
    monkeypatch.setattr(server, "ADMIN_TOKEN", TOKEN)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0])):
        monkeypatch.setattr(server, name, val)
    monkeypatch.setattr(reviews, "_RATE", {})
    monkeypatch.setattr(reviews, "MIN_FREE_BYTES", 0)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    yield {"port": port, "base": f"http://127.0.0.1:{port}", "dir": tmp_path}
    httpd.shutdown()
    httpd.server_close()


def req(s, method, path, body=None, user=None, headers=None, ctype=None):
    c = http.client.HTTPConnection("127.0.0.1", s["port"], timeout=10)
    h = {"Origin": s["base"]}
    if user:
        h["Cookie"] = f"{whop_auth.COOKIE}=good-{user}"
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if ctype:
        h["Content-Type"] = ctype
    h.update(headers or {})
    h = {k: v for k, v in h.items() if v is not None}
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    out = {"status": r.status, "headers": {k.lower(): v for k, v in r.getheaders()}, "raw": data}
    try:
        out["json"] = json.loads(data)
    except ValueError:
        out["json"] = None
    c.close()
    return out


def unread(s, path, length, user=None, extra=""):
    """POST headers that promise `length` bytes and send none: the answer must come at once (body never read) and close."""
    sock = socket.create_connection(("127.0.0.1", s["port"]), timeout=5)
    cookie = f"Cookie: {whop_auth.COOKIE}=good-{user}\r\n" if user else ""
    sock.sendall((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{s['port']}\r\nOrigin: {s['base']}\r\n{cookie}{extra}"
                  f"Content-Length: {length}\r\n\r\n").encode())
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    head = data.split(b"\r\n\r\n")[0].decode()
    return int(head.split()[1]), head.lower()


def jpeg(size=(800, 600), gps=True, color=(30, 120, 200)):
    im = Image.new("RGB", size, color)
    b = io.BytesIO()
    if gps:
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"
        ex[0x8825] = {1: "N", 2: (51.0, 30.0, 12.0), 3: "W", 4: (0.0, 7.0, 40.0)}
        im.save(b, "JPEG", exif=ex.tobytes())
    else:
        im.save(b, "JPEG")
    return b.getvalue()


def upload(s, user, data=None, ctype="image/jpeg"):
    return req(s, "POST", "/api/reviews/photo", body=data if data is not None else jpeg(), user=user, ctype=ctype)


def post_review(s, user, stars=5, body="Solid board, the hit rates save me time.", **kw):
    return req(s, "POST", "/api/reviews", {"stars": stars, "body": body, **kw}, user=user)


# ------------------------------------------------------------------------------------------------ image pipeline
def test_reencode_drops_gps_and_all_metadata():
    raw = jpeg((3000, 2000))
    assert b"Exif" in raw
    full, thumb = reviews.process_image(raw)
    for out, cap in ((full, reviews.FULL_PX), (thumb, reviews.THUMB_PX)):
        assert b"Exif" not in out and b"ICC_PROFILE" not in out
        im = Image.open(io.BytesIO(out))
        assert im.format == "JPEG" and max(im.size) <= cap
        assert not dict(im.getexif()) and "exif" not in im.info and "icc_profile" not in im.info


def test_oversize_pixels_refused_from_the_header(monkeypatch):
    # a 70-byte PNG whose header claims 100000 x 100000 pixels: refused before any decode
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    bomb = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 100000, 100000, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")
    with pytest.raises(reviews.Refused) as e:
        reviews.process_image(bomb)
    assert e.value.status == 413
    monkeypatch.setattr(reviews, "MAX_PIXELS", 10_000)          # a real JPEG just over the cap
    with pytest.raises(reviews.Refused) as e:
        reviews.process_image(jpeg((200, 100), gps=False))
    assert e.value.status == 413


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_wrong_image_formats_are_refused(fmt):
    b = io.BytesIO()
    Image.new("RGB", (40, 40)).save(b, fmt)
    with pytest.raises(reviews.Refused) as e:
        reviews.process_image(b.getvalue())
    assert e.value.status == 415


def test_non_images_are_refused():
    for data in (b"hello, this is not a photo at all", b"<svg xmlns='http://www.w3.org/2000/svg'/>", jpeg()[:200]):
        with pytest.raises(reviews.Refused) as e:
            reviews.process_image(data)
        assert e.value.status == 415


def test_png_with_transparency_and_webp_are_accepted():
    for mode, fmt in (("RGBA", "PNG"), ("RGB", "WEBP")):
        b = io.BytesIO()
        Image.new(mode, (300, 200)).save(b, fmt)
        full, thumb = reviews.process_image(b.getvalue())
        assert Image.open(io.BytesIO(full)).mode == "RGB"


def test_display_names():
    assert reviews.names("Sam Kim") == {"short": "Sam K.", "full": "Sam Kim"}
    assert reviews.names("  mary  ann  smith ")["short"] == "mary S."
    assert reviews.names("jordan23") == {"short": "jordan23", "full": "jordan23"}
    assert reviews.names("someone@example.com")["short"] == "Scout member"
    assert reviews.names("")["full"] == "Scout member"
    assert reviews.names("Bad‮Name\x00")["full"] == "Bad Name"


# ------------------------------------------------------------------------------------------------ HTTP: who may write
def test_signed_out_and_unpaid_writes_get_401_without_the_body_read(srv, monkeypatch):
    for path in ("/api/reviews", "/api/reviews/photo", "/api/reviews/delete"):
        st, head = unread(srv, path, 5_000_000, extra="Content-Type: application/json\r\n")
        assert st == 401 and "connection: close" in head, path
        st, head = unread(srv, path, 5_000_000, user="free_SECRETDDD", extra="Content-Type: image/jpeg\r\n")
        assert st == 401 and "connection: close" in head, path
    # the owner's load-test header passes allowed() but carries no member to write as
    st, head = unread(srv, "/api/reviews", 100, extra=f"X-Load-Test: {TOKEN}\r\nContent-Type: application/json\r\n")
    assert st == 401


def test_photo_over_the_cap_refused_unread(srv):
    st, head = unread(srv, "/api/reviews/photo", reviews.PHOTO_MAX + 1, user="user_SECRETAAA", extra="Content-Type: image/jpeg\r\n")
    assert st == 413 and "connection: close" in head
    st, head = unread(srv, "/api/reviews", reviews.JSON_MAX + 1, user="user_SECRETAAA", extra="Content-Type: application/json\r\n")
    assert st == 413 and "connection: close" in head
    st, head = unread(srv, "/api/reviews/photo", 1000, user="user_SECRETAAA", extra="Content-Type: image/gif\r\n")
    assert st == 415


def test_cross_origin_and_non_json_writes_are_refused(srv):
    u = "user_SECRETAAA"
    assert req(srv, "POST", "/api/reviews", {"stars": 5, "body": "x" * 20}, user=u, headers={"Origin": "https://evil.example"})["status"] == 403
    assert req(srv, "POST", "/api/reviews", {"stars": 5, "body": "x" * 20}, user=u, headers={"Origin": None})["status"] == 403
    assert req(srv, "POST", "/api/reviews", {"stars": 5, "body": "x" * 20}, user=u,
               headers={"Origin": None, "Referer": srv["base"] + "/reviews"})["status"] == 201
    r = req(srv, "POST", "/api/reviews", b'{"stars":5,"body":"xxxxxxxxxxxx"}', user=u, ctype="text/plain")
    assert r["status"] == 415


def test_validation(srv):
    u = "user_SECRETAAA"
    for bad in ({"stars": 0, "body": "long enough text"}, {"stars": 6, "body": "long enough text"}, {"stars": True, "body": "long enough text"},
                {"stars": 4, "body": "short"}, {"stars": 4, "body": "y" * 1501}, {"stars": 4, "body": "long enough text", "title": "t" * 81},
                {"stars": 4, "body": "long enough text", "name_style": "email"}, {"stars": 4, "body": "long enough text", "photos": ["zz"]},
                [1, 2]):
        assert req(srv, "POST", "/api/reviews", bad, user=u)["status"] == 400, bad
    assert req(srv, "POST", "/api/reviews", b'{"stars": NaN}', user=u, ctype="application/json")["status"] == 400


# ------------------------------------------------------------------------------------------------ HTTP: reviews
def test_one_review_per_member_upsert_and_edit_date(srv, monkeypatch):
    u = "user_SECRETAAA"
    monkeypatch.setattr(reviews, "_now", lambda: 1_790_000_000)
    r1 = post_review(srv, u, stars=2, body="Too many lines missing for my book.")
    assert r1["status"] == 201 and r1["json"]["review"]["name"] == "Sam K." and r1["json"]["review"]["edited"] is None
    monkeypatch.setattr(reviews, "_now", lambda: 1_790_090_000)
    r2 = post_review(srv, u, stars=4, body="They added my book. Better now.", name_style="full")
    assert r2["status"] == 200
    rv = r2["json"]["review"]
    assert rv["id"] == r1["json"]["review"]["id"] and rv["stars"] == 4 and rv["name"] == "Sam Kim"
    assert rv["created"] == r1["json"]["review"]["created"] and rv["edited"] and rv["edited"] > rv["created"]
    page = req(srv, "GET", "/api/reviews")["json"]
    assert page["summary"]["count"] == 1 and len(page["reviews"]) == 1 and page["reviews"][0]["edited"]
    me = req(srv, "GET", "/api/reviews/me", user=u)["json"]
    assert me["can_write"] and me["review"]["stars"] == 4 and me["names"] == {"short": "Sam K.", "full": "Sam Kim"}
    assert req(srv, "POST", "/api/reviews/delete", {}, user=u)["json"]["deleted"] is True
    assert req(srv, "GET", "/api/reviews")["json"]["summary"]["count"] == 0          # the cached page follows the write at once


def test_every_rating_publishes_and_hidden_reviews_leave_the_average(srv):
    a, b, c = "user_SECRETAAA", "user_SECRETBBB", "user_SECRETCCC"
    pa = upload(srv, b)["json"]["photo"]
    assert post_review(srv, a, stars=5)["status"] == 201
    rb = post_review(srv, b, stars=1, body="Didn't help me at all this week.", photos=[pa["id"]])
    assert rb["status"] == 201
    assert post_review(srv, c, stars=3)["status"] == 201
    s = req(srv, "GET", "/api/reviews/summary")["json"]["summary"]
    assert s == {"count": 3, "average": 3.0, "breakdown": {"5": 1, "4": 0, "3": 1, "2": 0, "1": 1}}
    low = req(srv, "GET", "/api/reviews?sort=low")["json"]["reviews"]
    assert [r["stars"] for r in low] == [1, 3, 5]                                   # 1-star posted straight away, no review step
    assert [r["stars"] for r in req(srv, "GET", "/api/reviews?sort=high")["json"]["reviews"]] == [5, 3, 1]
    assert req(srv, "GET", pa["thumb"])["status"] == 200
    rid = rb["json"]["review"]["id"]
    h = {"X-Admin-Token": TOKEN}
    assert req(srv, "POST", "/admin/reviews/action", {"action": "hide", "id": rid}, headers=h)["status"] == 400   # a reason is required
    assert req(srv, "POST", "/admin/reviews/action", {"action": "hide", "id": rid, "reason": "rating_too_low"}, headers=h)["status"] == 400
    assert req(srv, "POST", "/admin/reviews/action", {"action": "hide", "id": rid, "reason": "personal_info", "note": "phone number"}, headers=h)["status"] == 200
    s = req(srv, "GET", "/api/reviews/summary")["json"]["summary"]
    assert s["count"] == 2 and s["average"] == 4.0 and s["breakdown"]["1"] == 0
    assert all(r["id"] != rid for r in req(srv, "GET", "/api/reviews")["json"]["reviews"])
    assert req(srv, "GET", pa["thumb"])["status"] == 404 and req(srv, "GET", pa["full"])["status"] == 404   # hidden: photos stop serving
    mine = req(srv, "GET", "/api/reviews/me", user=b)["json"]["review"]
    assert mine["hidden"] and mine["hidden_reason"] == "Personal info"
    adm = req(srv, "GET", "/admin/reviews.json", headers=h)["json"]
    assert len(adm["reviews"]) == 3 and adm["log"][0]["action"] == "hide" and adm["log"][0]["reason"] == "personal_info"
    assert req(srv, "POST", "/admin/reviews/action", {"action": "unhide", "id": rid}, headers=h)["status"] == 200
    assert req(srv, "GET", "/api/reviews/summary")["json"]["summary"]["count"] == 3 and req(srv, "GET", pa["thumb"])["status"] == 200


def test_owner_reply_and_photo_removal(srv):
    u = "user_SECRETAAA"
    p = upload(srv, u)["json"]["photo"]
    rid = post_review(srv, u, stars=2, photos=[p["id"]])["json"]["review"]["id"]
    h = {"X-Admin-Token": TOKEN}
    assert req(srv, "POST", "/admin/reviews/action", {"action": "reply", "id": rid, "text": "Thanks - we added your book."}, headers=h)["status"] == 200
    r = req(srv, "GET", "/api/reviews")["json"]["reviews"][0]
    assert r["reply"]["name"] == "1Time (owner)" and r["reply"]["text"] == "Thanks - we added your book." and r["edited"] is None
    assert req(srv, "POST", "/admin/reviews/action", {"action": "remove_photo", "id": rid, "photo": p["id"], "reason": "personal_info"}, headers=h)["status"] == 200
    assert req(srv, "GET", "/api/reviews")["json"]["reviews"][0]["photos"] == []
    assert req(srv, "GET", p["full"])["status"] == 404 and not list((srv["dir"] / "review_photos").glob(p["id"] + "*"))


def test_admin_tools_need_the_token_header(srv):
    rid = post_review(srv, "user_SECRETAAA")["json"]["review"]["id"]
    assert req(srv, "GET", "/admin/reviews.json")["status"] == 403
    assert req(srv, "GET", "/admin/reviews.json?token=" + TOKEN)["status"] == 403            # scripts: header only, never a URL token
    assert req(srv, "GET", "/admin/reviews")["status"] == 403
    assert req(srv, "GET", "/admin/reviews?token=" + TOKEN)["status"] == 200                # the owner's link opens the page
    assert req(srv, "GET", "/admin/reviews.json", headers={"X-Admin-Token": "wrong"})["status"] == 403
    st, head = unread(srv, "/admin/reviews/action", 1000, extra="Content-Type: application/json\r\n")
    assert st == 403 and "connection: close" in head
    st, _ = unread(srv, "/admin/reviews/action", 1000, user="user_SECRETAAA", extra="Content-Type: application/json\r\n")
    assert st == 403                                                                           # a member is not the owner
    assert req(srv, "POST", "/admin/reviews/action", {"action": "hide", "id": rid, "reason": "spam"})["status"] == 403


def test_user_id_never_in_any_answer(srv):
    for u, st in (("user_SECRETAAA", 5), ("user_SECRETBBB", 2)):
        p = upload(srv, u)["json"]["photo"]
        post_review(srv, u, stars=st, photos=[p["id"]])
    bodies = [req(srv, "GET", p)["raw"] for p in ("/api/reviews", "/api/reviews?sort=low", "/api/reviews/summary")]
    bodies += [req(srv, "GET", "/api/reviews/me", user="user_SECRETAAA")["raw"]]
    bodies += [req(srv, "GET", "/admin/reviews.json", headers={"X-Admin-Token": TOKEN})["raw"]]
    for b in bodies:
        assert b"SECRET" not in b and b"user_" not in b


def test_photos_are_owned_capped_and_served_immutable(srv, monkeypatch):
    a, b = "user_SECRETAAA", "user_SECRETBBB"
    r = upload(srv, a, jpeg())
    assert r["status"] == 201
    p = r["json"]["photo"]
    got = req(srv, "GET", p["full"])
    assert got["status"] == 200 and got["headers"]["content-type"] == "image/jpeg"
    assert got["headers"]["cache-control"] == "public, max-age=31536000, immutable"
    assert b"Exif" not in got["raw"] and b"PhoneMaker" not in got["raw"]
    assert upload(srv, b, jpeg())["json"]["photo"]["id"] != p["id"]           # same bytes, another member: another file
    assert post_review(srv, b, photos=[p["id"]])["status"] == 400            # never someone else's photo
    assert req(srv, "POST", "/api/reviews/photo/delete", {"id": p["id"]}, user=b)["status"] == 404
    for bad in ("/reviews/p/../../reviews.db", "/reviews/p/ABC.jpg", "/reviews/p/" + "a" * 32 + ".png", "/reviews/p/" + "0" * 32 + ".jpg"):
        assert req(srv, "GET", bad)["status"] == 404, bad
    assert upload(srv, a, b"not an image")["status"] == 415
    ids = [p["id"]] + [upload(srv, a, jpeg(color=(i, 9, 9)))["json"]["photo"]["id"] for i in range(3)]
    assert post_review(srv, a, photos=ids)["status"] == 400                    # at most 3
    assert post_review(srv, a, photos=ids[:3])["status"] == 201
    assert req(srv, "POST", "/api/reviews/photo/delete", {"id": ids[0]}, user=a)["json"]["ok"] is True
    assert len(req(srv, "GET", "/api/reviews/me", user=a)["json"]["review"]["photos"]) == 2
    assert req(srv, "GET", p["full"])["status"] == 404


def test_upload_rate_limit_and_storage_cap(srv, monkeypatch):
    u = "user_SECRETAAA"
    monkeypatch.setattr(reviews, "UPLOADS_PER_HOUR", 2)
    assert upload(srv, u)["status"] == 201 and upload(srv, u, jpeg(color=(1, 2, 3)))["status"] == 201
    st, head = unread(srv, "/api/reviews/photo", 5000, user=u, extra="Content-Type: image/jpeg\r\n")
    assert st == 429 and "connection: close" in head
    monkeypatch.setattr(reviews, "_RATE", {})
    monkeypatch.setattr(reviews, "PHOTOS_CAP_BYTES", 1)
    st, _ = unread(srv, "/api/reviews/photo", 5000, user=u, extra="Content-Type: image/jpeg\r\n")
    assert st == 507
    monkeypatch.setattr(reviews, "PHOTOS_CAP_BYTES", 300 * 1024 * 1024)
    monkeypatch.setattr(reviews, "MIN_FREE_BYTES", 1 << 62)
    st, _ = unread(srv, "/api/reviews/photo", 5000, user=u, extra="Content-Type: image/jpeg\r\n")
    assert st == 507


def test_orphan_photos_are_swept_after_a_day(srv, monkeypatch):
    u = "user_SECRETAAA"
    monkeypatch.setattr(reviews, "_now", lambda: 1_790_000_000)
    kept = upload(srv, u)["json"]["photo"]
    orphan = upload(srv, u, jpeg(color=(5, 5, 5)))["json"]["photo"]
    post_review(srv, u, photos=[kept["id"]])
    monkeypatch.setattr(reviews, "_now", lambda: 1_790_000_000 + reviews.ORPHAN_S + 10)
    assert reviews.sweep_orphans(force=True) == 1
    assert req(srv, "GET", orphan["full"])["status"] == 404 and req(srv, "GET", kept["full"])["status"] == 200


def test_switches(srv, monkeypatch):
    u = "user_SECRETAAA"
    monkeypatch.setenv("REVIEWS_PHOTOS", "0")
    me = req(srv, "GET", "/api/reviews/me", user=u)["json"]
    assert me["can_write"] and not me["photos_on"]
    st, _ = unread(srv, "/api/reviews/photo", 5000, user=u, extra="Content-Type: image/jpeg\r\n")
    assert st == 403
    assert post_review(srv, u)["status"] == 201
    monkeypatch.setenv("REVIEWS_WRITE", "0")
    assert req(srv, "GET", "/reviews")["status"] == 200 and req(srv, "GET", "/api/reviews")["json"]["summary"]["count"] == 1
    assert req(srv, "GET", "/api/reviews/me", user=u)["json"]["can_write"] is False
    st, _ = unread(srv, "/api/reviews", 100, user=u, extra="Content-Type: application/json\r\n")
    assert st == 403
    monkeypatch.setenv("REVIEWS_PAGE", "0")
    for path in ("/reviews", "/reviews.js", "/api/reviews", "/api/reviews/me", "/api/reviews/summary"):
        assert req(srv, "GET", path, user=u)["status"] == 404, path
    assert req(srv, "GET", "/admin/reviews.json", headers={"X-Admin-Token": TOKEN})["status"] == 200   # owner tools stay
    for k in ("REVIEWS_PAGE", "REVIEWS_WRITE", "REVIEWS_PHOTOS"):
        monkeypatch.delenv(k)
    assert req(srv, "GET", "/reviews")["status"] == 200


def test_pages_are_public_and_served_before_the_landing_gate(srv):
    page = req(srv, "GET", "/reviews")
    assert page["status"] == 200 and b"Every review is posted, good or bad." in page["raw"] and b"/reviews.js" in page["raw"]
    assert req(srv, "GET", "/reviews.js")["status"] == 200
    me = req(srv, "GET", "/api/reviews/me")["json"]
    assert me == {**me, "signed_in": False, "member": False, "can_write": False, "review": None} and "names" not in me
    me = req(srv, "GET", "/api/reviews/me", user="free_SECRETDDD")["json"]
    assert me["signed_in"] and not me["member"] and not me["can_write"]
    assert req(srv, "GET", "/api/reviews?sort=random")["status"] == 400 and req(srv, "GET", "/api/reviews?page=-1")["status"] == 400


def test_sign_in_returns_only_to_allow_listed_pages(srv, monkeypatch):
    monkeypatch.setattr(whop_auth, "start_login", lambda: ("https://whop.invalid/oauth/authorize?x=1", "pkce-value"))
    monkeypatch.setattr(whop_auth, "finish_login", lambda code, state, pkce: ("session-value", "ok"))
    r = req(srv, "GET", "/auth/login?next=reviews")
    assert r["status"] == 302 and "cs2s_next=reviews" in r["headers"]["set-cookie"]
    for bad in ("https://evil.example", "/admin", "//evil.example", "reviews%0d%0aX:1"):
        r = req(srv, "GET", "/auth/login?next=" + bad)
        assert r["status"] == 302 and "cs2s_next" not in r["headers"].get("set-cookie", ""), bad
    r = req(srv, "GET", "/auth/callback?code=c&state=s", headers={"Cookie": "cs2s_pkce=x; cs2s_next=reviews"})
    assert r["status"] == 302 and r["headers"]["location"] == "/reviews"
    r = req(srv, "GET", "/auth/callback?code=c&state=s", headers={"Cookie": "cs2s_pkce=x; cs2s_next=https://evil.example"})
    assert r["headers"]["location"] == "/"
    r = req(srv, "GET", "/auth/callback?code=c&state=s", headers={"Cookie": "cs2s_pkce=x"})
    assert r["headers"]["location"] == "/"


def test_landing_and_record_link_to_reviews():
    landing = (server.WEB / "landing.html").read_text(encoding="utf-8")
    assert 'href="/reviews"' in landing and "/api/reviews?sort=new&page=0" in landing     # the cached list carries the summary too (2026-11-19 carousel)
    assert 'href="/reviews"' in (server.WEB / "record.html").read_text(encoding="utf-8")
    js = (server.WEB / "reviews.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js        # member text only through textContent
