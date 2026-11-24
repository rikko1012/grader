"""The v3 interface gate (web/UI_V3.md): SCOUT_UI_V3 off and no owner preview cookie = every page, asset and route answers exactly
as before (same bytes, same ETag, the unknown-route answer for /v3/* and /preview/v3); a member cannot forge or reach the preview;
the owner's /preview/v3 turns it on for that browser only; SCOUT_UI_V3=1 turns it on for everyone. No network, no production data."""
import shutil
import subprocess
import urllib.error
import urllib.request

import pytest

import server
import ui_v3
import whop_auth
from tests.test_upgrade_gate import ROOT, OWNER, env, get  # noqa: F401

V3_MARK = b"data-ui-v3"


def cookie_get(base, path, cookies, headers=None):
    h = dict(headers or {})
    h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=h), timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def raw(base, path, cookies=None):
    """-> (status, headers as a list of (name, value)) without following a redirect."""
    h = {"Cookie": "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())} if cookies else {}
    op = urllib.request.build_opener(NoRedirect)
    try:
        with op.open(urllib.request.Request(base + path, headers=h), timeout=20) as r:
            return r.status, r.headers.items()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.items()


def member(c="good-u1"):
    return {whop_auth.COOKIE: c}


@pytest.fixture
def v3env(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UI_V3", "0")
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    return env


def test_the_page_files_carry_no_v3_code():
    for name in ("index.html", "lol.html", "sports.html", "landing.html", "record.html", "sports/app.js", "upgrade-hub.js", "upgrade.js"):
        body = (ROOT / "web" / name).read_bytes()
        assert b"/v3/" not in body and V3_MARK not in body and b"ScoutV3" not in body, name


def test_flag_off_pages_are_unchanged_for_members_forged_cookies_and_signed_out(v3env):
    for path, name in (("/", "index.html"), ("/lol", "lol.html")):
        st, body, hdr = get(v3env.base, path, "good-u1")
        assert st == 200 and body == (ROOT / "web" / name).read_bytes(), path
        for forged in ("1", "true", "on", ui_v3.preview_value("not-the-token"), "x" * 40):
            st2, body2, hdr2 = cookie_get(v3env.base, path, {**member(), ui_v3.COOKIE: forged})
            assert st2 == 200 and body2 == body and hdr2["ETag"] == hdr["ETag"], (path, forged)
    st, body, hdr = get(v3env.base, "/")
    assert st == 200 and V3_MARK not in body and b"/v3/" not in body          # signed-out landing
    st, body, _ = get(v3env.base, "/record")
    assert st == 200 and body == (ROOT / "web" / "record.html").read_bytes()
    assert get(v3env.base, "/?ui=v3", "good-u1")[1] == (ROOT / "web" / "index.html").read_bytes()


def test_flag_off_v3_routes_are_the_unknown_route_answer(v3env):
    before = get(v3env.base, "/no/such/route", "good-u1")
    for path in ("/v3/member.css", "/v3/member.js", "/v3/landing.css", "/v3/record.js", "/v3/art/dust2.svg", "/v3/tokens.css",
                 "/preview/v3", "/preview/v3/landing", "/preview/v3?off=1"):
        for who in ("good-u1", None):
            st, body, _ = get(v3env.base, path, who)
            assert (st, body) == before[:2] or (who is None and st == 200 and V3_MARK not in body), (path, who)
    st, hdrs = raw(v3env.base, "/preview/v3", member())
    assert st == 404 and not any(k.lower() == "set-cookie" and ui_v3.COOKIE in v for k, v in hdrs)


def test_a_member_cannot_reach_the_preview_and_the_owner_can(v3env, monkeypatch):
    st, hdrs = raw(v3env.base, "/preview/v3?token=wrong", member())
    assert st == 404
    st, hdrs = raw(v3env.base, "/preview/v3?token=owner-token", member())
    sc = [v for k, v in hdrs if k.lower() == "set-cookie" and v.startswith(ui_v3.COOKIE + "=")]
    assert st == 302 and len(sc) == 1 and "HttpOnly" in sc[0] and "owner-token" not in sc[0]
    value = sc[0].split(";")[0].split("=", 1)[1]
    assert value == ui_v3.preview_value("owner-token")
    jar = {**member(), ui_v3.COOKIE: value}
    for path, name in (("/", "cs2"), ("/lol", "lol")):
        st, body, _ = cookie_get(v3env.base, path, jar)
        assert st == 200 and V3_MARK in body and f'data-v3-page="{name}"'.encode() in body
        assert body.count(b'href="/v3/member.css"') == 1 and body.count(b'src="/v3/member.js"') == 1
    st, body, hdr = cookie_get(v3env.base, "/v3/member.css", jar)
    assert st == 200 and hdr["Content-Type"].startswith("text/css") and b"--sc-bg" in body and b".sc-appbar" in body
    st, body, hdr = cookie_get(v3env.base, "/v3/member.js", jar)
    assert st == 200 and hdr["Content-Type"].startswith("text/javascript") and b"window.ScoutV3" in body
    st, body, hdr = cookie_get(v3env.base, "/v3/art/dust2.svg", jar)
    assert st == 200 and hdr["Content-Type"] == "image/svg+xml" and body.startswith(b"<svg")
    before = get(v3env.base, "/no/such/route", "good-u1")[:2]
    for bad in ("/v3/art/nope.svg", "/v3/art/../tokens.css", "/v3/art/%2e%2e/x.svg", "/v3/tokens.css", "/v3/shell.js", "/v3/member.html"):
        assert cookie_get(v3env.base, bad, jar)[:2] == before, bad
    # the owner allow list (no token in the URL) can start it too; another member on the list check cannot
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    assert raw(v3env.base, "/preview/v3", member(f"good-{OWNER}"))[0] == 302
    assert raw(v3env.base, "/preview/v3", member("good-u1"))[0] == 404
    # ?off=1 clears it
    st, hdrs = raw(v3env.base, "/preview/v3?off=1&token=owner-token", jar)
    assert st == 302 and any(k.lower() == "set-cookie" and v.startswith(ui_v3.COOKIE + "=;") and "Max-Age=0" in v for k, v in hdrs)


def test_no_admin_token_means_no_preview(v3env, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_TOKEN", None)
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    assert raw(v3env.base, "/preview/v3", member(f"good-{OWNER}"))[0] == 404
    assert ui_v3.preview_value(None) is None and not ui_v3.preview_ok("anything", None)


@pytest.mark.parametrize("story, marker", [("0", b'class="browser"'), ("1", b'class="wrap hero-layout"')])
def test_flag_on_everyone_gets_v3_with_its_own_etag(v3env, monkeypatch, story, marker):
    monkeypatch.setenv("SCOUT_LANDING_STORY", story)
    off = {p: get(v3env.base, p, "good-u1") for p in ("/", "/lol")}
    off_landing = get(v3env.base, "/")
    off_record = get(v3env.base, "/record")
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    for p, name in (("/", "cs2"), ("/lol", "lol")):
        st, body, hdr = get(v3env.base, p, "good-u1", {"If-None-Match": off[p][2]["ETag"]})
        assert st == 200 and V3_MARK in body and hdr["ETag"] != off[p][2]["ETag"]
        assert body.replace(b'<html data-ui-v3="1" data-v3-page="' + name.encode() + b'" data-sc-sport="' + (b"lol" if name == "lol" else b"cs2") + b'"', b"<html", 1) \
            .replace(ui_v3.head(name)[0], b"", 1).replace(b'<script src="/v3/member.js" defer></script>', b"", 1) == off[p][1]
        assert get(v3env.base, p, "good-u1", {"If-None-Match": hdr["ETag"]})[0] == 304
    st, body, _ = get(v3env.base, "/")
    assert st == 200 and b'data-v3-page="landing"' in body and marker in body and body != off_landing[1]
    st, body, _ = get(v3env.base, "/record")
    assert st == 200 and b'data-v3-page="record"' in body and b"family=Outfit:wght@400;500;600" in body and body != off_record[1]
    for path in ("/v3/landing.css", "/v3/landing.js", "/v3/record.css", "/v3/record.js", "/v3/art/rift-red.svg"):
        assert get(v3env.base, path)[0] == 200, path                       # public pages need their assets signed out
    monkeypatch.setenv("SCOUT_UI_V3", "0")
    assert get(v3env.base, "/", "good-u1")[1] == off["/"][1]


def test_v3_pages_never_take_the_ui_next_redesign(v3env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setenv("UI_NEXT", "1")
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    body = get(v3env.base, "/", "good-u1")[1]
    assert b"const ck=document.cookie,NEXT=false,_v3next=" in body and body.count(b'data-upgrade="cs2"') == 1
    assert body.index(b"/v3/member.css") < body.index(b"</head>") and body.rindex(b"/v3/member.js") > body.index(b"<body")


def test_bundles_list_existing_files_and_every_script_parses():
    for name, parts in ui_v3.BUNDLES.items():
        for p in parts:
            assert (ui_v3.V3 / p).is_file(), (name, p)
        assert ui_v3.bundle(name) is ui_v3.bundle(name)                      # memoised: one bytes object per file version
    for a in ui_v3.ART:
        assert (ui_v3.V3 / "art" / (a + ".svg")).read_bytes().startswith(b"<svg"), a
    assert {p.stem for p in (ui_v3.V3 / "art").glob("*.svg")} == set(ui_v3.ART)
    node = shutil.which("node")
    if not node:
        pytest.skip("node missing: the browser tests still run every bundle")
    for name in ui_v3.BUNDLES:
        if name.endswith(".js"):
            r = subprocess.run([node, "--check", "-"], input=ui_v3.bundle(name), capture_output=True, timeout=60)
            assert r.returncode == 0, (name, r.stderr[-400:])


def test_page_without_markers_goes_out_as_it_is():
    assert ui_v3.page(b"<p>no head</p>", "cs2") == b"<p>no head</p>"


def test_one_answer_per_request_on_a_kept_alive_connection(v3env, monkeypatch):
    """Each v3 route sends exactly one response: a browser reuses one connection for the page, its bundle and its art."""
    import http.client
    from urllib.parse import urlsplit
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    host = urlsplit(v3env.base)
    c = http.client.HTTPConnection(host.hostname, host.port, timeout=20)
    seq = [("/v3/member.css", 200, b".sc-appbar"), ("/v3/member.js", 200, b"window.ScoutV3"), ("/v3/art/dust2.svg", 200, b"<svg"),
           ("/v3/art/nope.svg", 404, b"not found"), ("/preview/v3/landing", 200, b'data-v3-page="landing"'), ("/v3/landing.js", 200, b"ScoutV3"),
           ("/", 200, b'data-v3-page="cs2"')]
    for path, status, mark in seq:
        c.request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-u1"})
        r = c.getresponse()
        body = r.read()
        assert (r.status, mark in body) == (status, True), path
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    for path, status in (("/preview/v3", 302), ("/preview/v3?off=1", 302), ("/v3/member.js", 200)):
        c.request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-{OWNER}"})
        r = c.getresponse()
        r.read()
        assert r.status == status, path
    c.close()


def test_v3_is_the_default_and_zero_turns_it_off(monkeypatch):
    """Nov 20 2026: v3 is ON for everyone unless SCOUT_UI_V3=0 (the instant way back)."""
    monkeypatch.delenv("SCOUT_UI_V3", raising=False)
    assert ui_v3.ui_v3_default() is True
    for off in ("0", "false", "off", "no", " OFF "):
        monkeypatch.setenv("SCOUT_UI_V3", off)
        assert ui_v3.ui_v3_default() is False
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    assert ui_v3.ui_v3_default() is True
