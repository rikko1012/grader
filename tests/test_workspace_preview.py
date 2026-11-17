"""Research workspace preview (/preview/workspace): off by default, on with SCOUT_WORKSPACE_PREVIEW or for the owner, its
assets served only through fixed routes, and every existing page byte-identical with the route in place. The wording tests
hold it to the owner's site rules."""
import http.client
import json
import re
import shutil
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import server
import whop_auth

ROUTES = tuple(server.WORKSPACE_ROUTES)
ASSETS = tuple(r for r in ROUTES if r != "/preview/workspace")
ADMIN = "A" * 40
EXISTING = ("/", "/index.html", "/lol", "/scout-ui.css", "/auth/me")
NOT_FOUND = {"error": "not found"}
WEB = Path(server.__file__).parent / "web"


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.delenv("SCOUT_WORKSPACE_PREVIEW", raising=False)
    monkeypatch.delenv("SPORTS_UI_ALLOW", raising=False)
    monkeypatch.delenv("REQUIRE_WHOP", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    monkeypatch.setattr(server, "ADMIN_TOKEN", ADMIN)
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(whop_auth, "enabled", lambda: False)
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


def paid_session(user="user_owner"):
    now = int(time.time())
    return {"Cookie": f"{whop_auth.COOKIE}=" + whop_auth._sign({"u": user, "n": "T", "ok": True, "chk": now, "exp": now + 3600})}


@pytest.mark.parametrize("path", ROUTES)
def test_off_by_default_answers_as_an_unknown_route(srv, path):
    st, _, body = get(srv, path)
    assert st == 404 and json.loads(body) == NOT_FOUND


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no"])
def test_off_values_stay_off(srv, monkeypatch, value):
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", value)
    assert get(srv, "/preview/workspace")[0] == 404


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_on_with_flag(srv, monkeypatch, value):
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", value)
    st, h, body = get(srv, "/preview/workspace")
    assert st == 200 and h["content-type"].startswith("text/html") and body == (WEB / "workspace.html").read_bytes()


def test_assets_served_with_their_types_and_validators(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    for path in ROUTES:
        name, ctype = server.WORKSPACE_ROUTES[path]
        st, h, body = get(srv, path)
        assert st == 200 and h["content-type"] == ctype and body == (WEB / name).read_bytes()
        assert h["cache-control"] == "private, no-cache" and h["x-content-type-options"] == "nosniff"
        st2, _, again = get(srv, path, {"If-None-Match": h["etag"]})
        assert st2 == 304 and again == b""


def test_page_only_links_routes_that_exist(srv):
    page = (WEB / "workspace.html").read_text(encoding="utf-8")
    local = re.findall(r'(?:src|href)="(/[^"]+)"', page)
    assert sorted(local) == sorted(ASSETS)
    assert not re.search(r'<script[^>]+src="https?:', page)                      # no external scripts
    remote = [h for h in re.findall(r'<link[^>]+href="(https?://[^"]+)"', page)]
    assert remote and all(h.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com")) for h in remote)


def test_no_path_reaches_the_file_reader(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    for path in ("/preview/workspace/../server.py", "/preview/workspace/index.html", "/preview/workspace/workspace.py",
                 "/preview/workspace/", "/preview/workspace/workspace.js/x"):
        assert get(srv, path)[0] == 404, path


def test_owner_can_open_it_with_the_flag_off(srv, monkeypatch):
    st, _, body = get(srv, f"/preview/workspace?token={ADMIN}")
    assert st == 200 and b"1TIME SCOUT" in body
    assert get(srv, "/preview/workspace", {"X-Admin-Token": ADMIN})[0] == 200
    # the page's scripts and styles carry no token: they are authorised from the page URL in their Referer
    for path in ASSETS:
        assert get(srv, path, {"Referer": f"http://x/preview/workspace?token={ADMIN}"})[0] == 200
        assert get(srv, path, {"Referer": "http://x/preview/workspace?token=wrong"})[0] == 404
        assert get(srv, path, {"Referer": f"http://x/elsewhere?token={ADMIN}"})[0] == 404
    assert get(srv, "/preview/workspace?token=wrong")[0] == 404


def test_flag_never_opens_it_to_signed_out_visitors_on_the_live_site(srv, monkeypatch):
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    monkeypatch.setenv("SESSION_SECRET", "workspace-test-secret")
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: False)
    assert get(srv, "/preview/workspace")[0] == 404
    assert get(srv, "/preview/workspace/workspace-data.js")[0] == 404


def test_allow_listed_owner_session_with_the_flag_off(srv, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "workspace-test-secret")
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "has_access", lambda *a, **k: True)
    assert get(srv, "/preview/workspace", paid_session())[0] == 404              # a paid member, not on the list
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id("user_owner"))
    assert get(srv, "/preview/workspace", paid_session())[0] == 200
    assert get(srv, "/preview/workspace", paid_session("user_other"))[0] == 404


@pytest.mark.parametrize("path", EXISTING)
def test_existing_pages_unchanged(srv, monkeypatch, path):
    off = get(srv, path)
    monkeypatch.setenv("SCOUT_WORKSPACE_PREVIEW", "1")
    on = get(srv, path)
    assert off[0] == on[0] and off[2] == on[2] and off[1].get("etag") == on[1].get("etag")


def test_existing_page_files_do_not_mention_the_preview():
    for name in ("index.html", "lol.html", "sports.html", "landing.html", "scout-ui.css"):
        assert b"workspace-data" not in (WEB / name).read_bytes() and b"/preview/workspace" not in (WEB / name).read_bytes()


def workspace_text():
    return "\n".join((WEB / n).read_text(encoding="utf-8") for n in ("workspace.html", "workspace.js", "workspace-data.js"))


@pytest.mark.parametrize("bad", [
    r"financial advice", r"betting advice", r"not advice", r"not validated", r"unvalidated", r"unconfirmed", r"not confirmed",
    r"provisional", r"tentative", r"uncertain", r"unsure", r"\bmaybe\b", r"hypothetical", r"\broi\b", r"profit", r"win[- ]rate",
    r"guarantee", r"no bet\b", r"\bmore\b(?= \d)", r"\bless\b(?= \d)"])
def test_owner_wording_rules(bad):
    assert not re.search(bad, workspace_text(), re.I), bad


def test_colours_and_fonts():
    css = (WEB / "workspace.css").read_text(encoding="utf-8")
    assert "--bg:#000" in css and "--gold:#f5c842" in css and "--hit:#3FD07A" in css
    for f in ("Bebas Neue", "Outfit", "DM Mono"):
        assert f in css
    assert "#3FD07A" not in css.replace("--hit:#3FD07A", "")                     # green only through the hit token
    for sel in re.findall(r"([^{}]+)\{[^{}]*var\(--hit\)", css):                 # and only on verified hits
        assert re.search(r"hit|\.ob|\.h\b", sel), sel
    assert "@import" not in css


NODE_CHECK = r"""
global.window = {};
require(process.argv[2]);
const D = window.WORKSPACE_DATA;
console.log(JSON.stringify({ keys: Object.keys(D).sort(), sports: Object.keys(D.sports).sort(), slip: Object.keys(D.platforms).filter((k) => D.platforms[k].slip).sort() }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_data_file_holds_names_and_no_rows(tmp_path):
    script = tmp_path / "check.js"
    script.write_text(NODE_CHECK, encoding="utf-8")
    out = subprocess.run(["node", str(script), str(WEB / "workspace-data.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["keys"] == ["platforms", "presets", "sports"] and r["sports"] == ["cs2", "lol", "nba", "nfl", "soccer", "tennis"] and r["slip"] == ["prizepicks", "underdog"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_scripts_parse():
    for name in ("workspace.js", "workspace-data.js"):
        assert subprocess.run(["node", "--check", str(WEB / name)], capture_output=True, timeout=60).returncode == 0, name
