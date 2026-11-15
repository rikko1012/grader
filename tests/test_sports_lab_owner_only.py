"""UI 1114 (owner 2026-11-14): /sports/lab.html is owner/admin only ON THE SERVER - the page, its assets, every lab API
route and the lab's own token mint. A signed-in member who pastes any lab URL gets a readable 403 (HTML for a browser
navigation, JSON for /api and scripts) while the Sports board keeps working; signed out = the sign-in flow. No member
page carries an admin-token field. Real server.Handler on a local port in SPORTS_API_MODE=proxy, with a local stub
standing in for the private sports service (it records what reaches it): no network, no sports store, no production
data. This file imports no sports module (sports/tests/test_sports_isolation.py); the service's own refusal of a member
token on every lab route is sports/tests/test_lab.py::test_every_lab_route_refuses_a_member_token. Every user id /
token here is a TEST FIXTURE."""
import base64
import http.client
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import server
import whop_auth

ROOT = Path(server.__file__).parent
ADMIN = "fixture-owner-token"
OWNER_USER = "fixture-owner"
LAB_PAGES = ("/sports/lab.html", "/sports/lab", "/sports/lab.js", "/sports/lab.css", "/sports/LAB.html", "/sports/./lab.html",
             "/sports/lab.html/", "/sports/Lab.Js",
             # verify pass: pathlib drops "." segments, so these were served as the lab's own files
             "/sports/lab.html/.", "/sports/lab.js/./", "/sports/LAB.HTML/.", "/sports/lab.css/./.")
LAB_API = ("/api/sports/admin/lab/overview", "/api/sports/admin/lab/experiments", "/api/sports/admin/lab/experiment?id=lx-1",
           "/api/sports/admin/lab/export?id=lx-1&format=csv", "/api/sports/admin/lab/replay", "/api/sports/admin/lab",
           "/api/sports/token?scope=lab",
           # verify pass: these spellings reached the sports service with a member token
           "/api/sports//admin/lab/overview", "/api/sports/./admin/lab/overview", "/api/sports/admin/%6cab/overview",
           "/api/sports/admin/lab/overview/.")
NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}


def req(base, path, cookie=None, headers=None, method="GET", body=None):
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = f"{whop_auth.COOKIE}={cookie}"
    c = http.client.HTTPConnection(base[0], base[1], timeout=20)       # http.client: a 302 is seen, never followed
    try:
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        return r.status, r.read(), {k.lower(): v for k, v in r.getheaders()}
    finally:
        c.close()


def claims(token):
    """The st1 token's claims (format: st1.<base64url claims>.<mac>) - read only to check the role the server minted."""
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def role_of(body):
    return claims(json.loads(body)["token"])["role"]


def upstream(sent):
    """A stand-in private sports service: records (path, token role) and answers 200."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            tok = (self.headers.get("Authorization") or "")[len("Bearer "):]
            sent.append((self.path, claims(tok)["role"]))
            body = b'{"ok": true, "rows": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def site(monkeypatch, tmp_path):
    for k in ("SPORTS_UI", "SPORTS_API", "SPORTS_UI_ALLOW", "SCOUT_UPGRADE", "SCOUT_WORKSPACE_PREVIEW", "REQUIRE_WHOP", "RENDER",
              "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "SPORTS_API_BASE", "SPORTS_UPSTREAM"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setenv("SPORTS_API_MODE", "proxy")
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", "fixture-secret-" + "x" * 40)
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER_USER))
    monkeypatch.setenv("SCOUT_UPGRADE", "off")
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    monkeypatch.setattr(whop_auth, "cookie_valid", lambda v: bool(v and v.startswith("good-")))
    monkeypatch.setattr(whop_auth, "cookie_user", lambda v: v[5:] if v and v.startswith("good-") else None)
    sent = []                                           # what reached the stand-in private sports service
    up = upstream(sent)
    monkeypatch.setenv("SPORTS_UPSTREAM", f"127.0.0.1:{up.server_address[1]}")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield ("127.0.0.1", httpd.server_address[1]), sent
    httpd.shutdown()
    httpd.server_close()
    up.shutdown()
    up.server_close()


# ------------------------------------------------------------------------------------------------- the owner
def test_owner_by_admin_token_opens_the_lab_and_its_api(site):
    base, sent = site
    st, body, h = req(base, "/sports/lab.html", headers={"X-Admin-Token": ADMIN, **NAV})
    assert st == 200 and body == (ROOT / "web" / "sports" / "lab.html").read_bytes() and h["cache-control"] == "no-store"
    ref = {"Referer": f"http://{base[0]}:{base[1]}/sports/lab.html?token={ADMIN}"}      # the owner's link: assets ride its Referer
    for asset in ("lab.js", "lab.css"):
        st, body, _ = req(base, "/sports/" + asset, headers=ref)
        assert st == 200 and body == (ROOT / "web" / "sports" / asset).read_bytes(), asset
    st, body, _ = req(base, "/api/sports/token?scope=lab", headers={"X-Admin-Token": ADMIN})
    assert st == 200 and role_of(body) == "admin"
    st, _, _ = req(base, "/api/sports/admin/lab/overview", headers={"X-Admin-Token": ADMIN})
    assert st == 200 and sent == [("/api/sports/admin/lab/overview", "admin")]


def test_owner_by_signed_in_session_opens_the_lab_without_any_token(site):
    base, sent = site
    me = "good-" + OWNER_USER
    assert req(base, "/sports/lab.html", me, NAV)[0] == 200
    assert req(base, "/sports/lab.js", me)[0] == 200
    st, body, _ = req(base, "/api/sports/token?scope=lab", me)
    assert st == 200 and role_of(body) == "admin"               # the server mints the lab's admin token, not the browser
    st, body, _ = req(base, "/api/sports/token", me)
    assert st == 200 and role_of(body) == "member"              # the owner's own Sports board token is unchanged
    assert req(base, "/api/sports/admin/lab/experiment?id=lx-1", me)[0] == 200
    assert sent == [("/api/sports/admin/lab/experiment?id=lx-1", "admin")]


# --------------------------------------------------------------------------------------- an ordinary member
def test_member_gets_a_readable_403_page_for_every_lab_page_and_asset(site):
    base, sent = site
    for path in LAB_PAGES:
        st, body, h = req(base, path, "good-u1", NAV)
        assert st == 403 and h["content-type"].startswith("text/html"), path
        assert b"This page is for the site owner" in body and b'href=\'/sports#/board\'' in body, path
        assert b"lab-form" not in body and b"Model lab" not in body, path
        st, body, h = req(base, path, "good-u1")                 # a script / fetch: JSON
        assert st == 403 and json.loads(body)["error"] == "owner_only", path
    assert sent == []


def test_member_gets_403_json_on_every_lab_endpoint_and_nothing_reaches_the_sports_service(site):
    base, sent = site
    for path in LAB_API:
        for extra in ({}, NAV, {"X-Admin-Token": "wrong"}):
            st, body, h = req(base, path, "good-u1", extra)
            assert st == 403 and h["content-type"].startswith("application/json"), (path, extra)
            assert json.loads(body)["error"] == "owner_only"
    st, _, _ = req(base, "/api/sports/admin/lab/overview?token=wrong", "good-u1")
    assert st == 403
    forged = {"Referer": f"http://{base[0]}:{base[1]}/sports/lab.html?token=wrong"}
    assert req(base, "/sports/lab.js", "good-u1", forged)[0] == 403
    st, _, _ = req(base, "/api/sports/admin/lab/experiments", "good-u1", {"Content-Type": "application/json"}, "POST", b"{}")
    assert st == 403
    assert sent == []


def test_member_sports_board_keeps_working(site):
    base, sent = site
    st, body, _ = req(base, "/sports", "good-u1", NAV)
    assert st == 200 and body == (ROOT / "web" / "sports.html").read_bytes()
    for asset in ("app.js", "table.js", "sports.css", "labels.js"):            # labels.js is not the lab
        assert req(base, "/sports/" + asset, "good-u1")[0] == 200, asset
    st, body, _ = req(base, "/api/sports/token", "good-u1")
    assert st == 200 and role_of(body) == "member"
    st, body, _ = req(base, "/api/sports/board?league=nba", "good-u1")
    assert st == 200 and json.loads(body)["ok"] is True
    assert sent == [("/api/sports/board?league=nba", "member")]


# ----------------------------------------------------------------------------------------------- signed out
def test_signed_out_gets_the_sign_in_flow(site):
    base, sent = site
    st, _, h = req(base, "/sports/lab.html", None, NAV)
    assert st == 302 and h["location"] == "/auth/login?next=%2Fsports%2Flab.html"
    st, body, _ = req(base, "/sports/lab.js")
    assert st == 401 and json.loads(body)["signed_in"] is False
    for path in LAB_API:
        assert req(base, path)[0] == 401, path
    st, body, _ = req(base, "/sports/lab.html", "bad-cookie", NAV)          # an expired / invalid session: sign in again
    assert st == 302
    assert sent == []


# --------------------------------------------------------------------------------- no admin-token field anywhere


def test_no_member_page_carries_an_admin_token_field():
    hits = []
    for f in sorted((ROOT / "web").rglob("*")):
        if f.suffix.lower() not in (".html", ".js") or f.name == "diagnostics.html":      # diagnostics: /admin only
            continue
        for m in re.finditer(rb"[^\n]*(?:admin\s+token|lab-token|type=[\"']?password)[^\n]*", f.read_bytes(), re.I):
            hits.append((f.relative_to(ROOT).as_posix(), m.group(0)[:120]))
    assert hits == [], hits


def test_served_member_pages_carry_no_admin_token_text(site, monkeypatch):
    base, _ = site
    monkeypatch.setattr(server, "lol_public", lambda: True)
    for path in ("/", "/lol", "/sports", "/sports/app.js", "/sports/shell.js"):
        st, body, _ = req(base, path, "good-u1", NAV)
        assert st == 200, path
        assert not re.search(rb"admin\s+token|lab-token", body, re.I), path


def test_the_lab_page_itself_asks_for_no_token():
    html = (ROOT / "web" / "sports" / "lab.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "sports" / "lab.js").read_text(encoding="utf-8")
    assert "<input id=\"lab-token\"" not in html and "Admin token" not in html and "type=\"password\"" not in html
    assert "/api/sports/token?scope=lab" in js and "sessionStorage.setItem" not in js


@pytest.mark.parametrize("path,q,want", [
    ("/sports/lab.html", {}, True), ("/sports/labels.js", {}, False), ("/sports/app.js", {}, False), ("/sports", {}, False),
    ("/api/sports/admin/lab/overview", {}, True), ("/api/sports/token", {}, False), ("/api/sports/token", {"scope": "lab"}, True),
    ("/api/sports/board", {}, False), ("/sports/sub/lab.js", {}, True),
    ("/sports/lab.html/.", {}, True), ("/sports/labels.js/.", {}, False), ("/api/sports//admin/lab/x", {}, True),
    ("/api/sports/admin/%6Cab/x", {}, True), ("/api/sports/x/../admin/lab", {}, True), ("/sports/%6cab.html", {}, True),
    ("/api/sports/admin/labels", {}, True), ("/api/sports/admin/model/x", {}, False)])
def test_lab_path_matcher(path, q, want):
    assert server.sports_lab_path(path, q) is want
