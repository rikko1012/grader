"""Isolation proof (owner brief 2): with the sports flags off the esports server starts and serves without importing
sports or needing any sports credential / database; with them on, a sports failure is a 503 for that sports request
only and esports pages keep working. Rollback = flags off (no database restore)."""
import ast
import email.message
import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_server_imports_sports_only_inside_the_one_hook():
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    hits = []
    for fn in ast.walk(tree):
        for node in ast.walk(fn):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "sports":
                hits.append(node.lineno)
            elif isinstance(node, ast.Import) and any(a.name.split(".")[0] == "sports" for a in node.names):
                hits.append(node.lineno)
    owners = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if getattr(node, "lineno", None) in hits and isinstance(node, (ast.Import, ast.ImportFrom)):
                    owners.add(fn.name)
    assert hits and owners == {"_sports_route"}, (hits, owners)
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert src.count("self._sports_route(") == 1                  # one hook call site


_CHILD = textwrap.dedent("""
    import email.message, io, json, os, sys
    sys.path.insert(0, ROOT)
    import server

    class H(server.Handler):
        def __init__(self, path):
            self.path = path
            self.headers, self.rfile, self.wfile = email.message.Message(), io.BytesIO(), io.BytesIO()
            self.client_address, self.close_connection = ("10.0.0.1", 1), False
            self.code = None
        def send_response(self, code, message=None):
            self.code = code
        def send_header(self, k, v):
            pass
        def end_headers(self):
            pass

    out = {}
    for p in ("/sports", "/api/sports/status", "/api/sports/offers?league=nba", "/api/sports/token",
              "/definitely-not-a-route", "/auth/me"):
        h = H(p)
        h.do_GET()
        out[p] = [h.code, h.wfile.getvalue().decode("utf-8", "replace")[:400]]
    out["sports_imported"] = sorted(m for m in sys.modules if m == "sports" or m.startswith("sports."))
    print("RESULT" + json.dumps(out))
""")


def _child_env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_") and k not in ("ODDS_API_KEY",)}
    env.update({"CS2_DATA_DIR": str(tmp_path / "esports_data"), "PYTHONIOENCODING": "utf-8"})
    env.update(extra)
    return env


def _run_child(tmp_path, **extra):
    code = f"ROOT = {str(ROOT)!r}\n" + _CHILD
    r = subprocess.run([sys.executable, "-c", code], cwd=str(tmp_path), env=_child_env(tmp_path, **extra),
                       capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT")), None)
    assert line, (r.stdout[-2000:], r.stderr[-2000:])
    return json.loads(line[6:])


def test_flags_off_esports_serves_and_sports_is_never_imported(tmp_path):
    out = _run_child(tmp_path)
    assert out["sports_imported"] == []                              # not even the package
    unknown = out["/definitely-not-a-route"]
    for p in ("/sports", "/api/sports/status", "/api/sports/offers?league=nba"):
        assert out[p] == unknown                                     # identical to a route that never existed
    assert out["/auth/me"][0] == 200


def test_flags_on_without_a_store_degrades_to_sports_errors_only(tmp_path):
    out = _run_child(tmp_path, SPORTS_API="1", SPORTS_UI="1", SPORTS_API_MODE="inprocess",
                     SPORTS_DATA_DIR=str(tmp_path / "sports_data"))
    assert out["/sports"][0] == 200
    assert out["/api/sports/status"][0] == 200 and '"ready": false' in out["/api/sports/status"][1]
    assert out["/api/sports/offers?league=nba"][0] == 503
    assert out["/auth/me"][0] == 200


def test_service_mode_esports_threads_never_load_sports_work(tmp_path):
    """Default SPORTS_API_MODE=service: the esports process imports only sports.gateway + sports.authtoken (standard
    library), mints tokens, serves the page and answers every other sports path 404 not_here - the sports store,
    models and route handlers are never imported in the esports process."""
    out = _run_child(tmp_path, SPORTS_API="1", SPORTS_UI="1", SPORTS_TOKEN_SECRET="t" * 40,
                     SPORTS_API_BASE="https://sports.example.test", SPORTS_DATA_DIR=str(tmp_path / "sports_data"))
    assert out["sports_imported"] == ["sports", "sports.authtoken", "sports.gateway"]
    assert out["/sports"][0] == 200
    for p in ("/api/sports/status", "/api/sports/offers?league=nba"):
        assert out[p][0] == 404 and '"not_here"' in out[p][1] and "sports.example.test" in out[p][1]
    tok = json.loads(out["/api/sports/token"][1])                  # no Whop configured locally -> allowed()
    assert out["/api/sports/token"][0] == 200 and tok["token"].startswith("st1.") and tok["api_base"]
    assert out["/auth/me"][0] == 200


# ------------------------------------------------------------------- in-process: a sports exception
class _H:
    pass


def _handler():
    import server

    class H(server.Handler):
        def __init__(self, path):
            self.path = path
            self.headers, self.rfile, self.wfile = email.message.Message(), io.BytesIO(), io.BytesIO()
            self.client_address, self.close_connection = ("10.0.0.1", 1), False
            self.code = None

        def send_response(self, code, message=None):
            self.code = code

        def send_header(self, k, v):
            pass

        def end_headers(self):
            pass
    return H


def test_a_sports_exception_cannot_break_esports(monkeypatch):
    H = _handler()
    from sports import gateway
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setenv("SPORTS_UI", "1")

    def boom(*a, **k):
        raise RuntimeError("sports exploded")
    monkeypatch.setattr(gateway, "handle_get", boom)
    for p in ("/api/sports/status", "/sports", "/api/sports/token", "/sports/app.css"):
        h = H(p)
        h.do_GET()
        assert h.code == 503 and json.loads(h.wfile.getvalue())["error"] == "sports_unavailable"
    h = H("/auth/me")
    h.do_GET()
    assert h.code == 200                                             # esports unaffected, same process


def test_flags_are_off_by_default(monkeypatch):
    from sports import flags
    for f in flags.FLAGS:
        monkeypatch.delenv(f, raising=False)
    assert flags.state() == {f: False for f in flags.FLAGS}
    monkeypatch.setenv("SPORTS_API", "yes")
    assert flags.on("SPORTS_API") and not flags.on("SPORTS_UI")
    monkeypatch.setenv("SPORTS_UI", "0")
    assert not flags.on("SPORTS_UI")
    with pytest.raises(flags.FlagOff):
        flags.require("SPORTS_INGEST")


def test_rollback_is_flags_off_even_with_the_service_configured(tmp_path):
    """Rollback proof: with every sports setting still present (mode, secret, api base, data dir) but SPORTS_UI /
    SPORTS_API off, the esports server imports nothing from sports and every sports path - including the token
    endpoint - is byte-identical to a route that never existed. No database restore, no esports rollback."""
    out = _run_child(tmp_path, SPORTS_API="0", SPORTS_UI="off", SPORTS_API_MODE="service",
                     SPORTS_TOKEN_SECRET="t" * 40, SPORTS_API_BASE="https://sports.example.test",
                     SPORTS_DATA_DIR=str(tmp_path / "sports_data"))
    assert out["sports_imported"] == []
    unknown = out["/definitely-not-a-route"]
    for p in ("/sports", "/api/sports/status", "/api/sports/offers?league=nba", "/api/sports/token"):
        assert out[p] == unknown, p
    assert out["/auth/me"][0] == 200


def test_esports_pages_are_byte_identical_with_sports_ui_off_and_get_one_link_with_it_on(monkeypatch):
    import server
    H = _handler()
    monkeypatch.setattr(server, "lol_public", lambda: True)
    raw = {"/": server.page(), "/lol": server.web_file("lol.html")}

    def get(path):
        h = H(path)
        h.do_GET()
        return h.code, h.wfile.getvalue()
    for flag in (None, "0", "off"):
        if flag is None:
            monkeypatch.delenv("SPORTS_UI", raising=False)
        else:
            monkeypatch.setenv("SPORTS_UI", flag)
        for p, body in raw.items():
            code, out = get(p)
            assert code == 200 and out == body, p                    # untouched bytes with the flag off
            assert b'href="/sports"' not in out
    monkeypatch.setenv("SPORTS_UI", "1")
    for p, body in raw.items():
        code, out = get(p)
        assert code == 200 and out.count(b'href="/sports"') == 1, p  # exactly ONE link
        assert out.replace(server._SPORTS_NAV, b"", 1) == body        # and nothing else changed


def test_sports_nav_returns_the_same_object_per_page_version():
    """send_shared caches ETag / gzip by body identity: the linked page must be ONE object per page version."""
    import server
    page = server.page()
    a, b = server.sports_nav(page), server.sports_nav(page)
    assert a is b and a != page
    other = page + b" "
    assert server.sports_nav(other) is not a


# --------------------------------------------------------------------------------------------------------------------
# SPORTS_UI=owner - staged, server-side owner-first access (2026-10-24). The /sports page, its assets, the esports nav
# link and /api/sports/token are served ONLY to a paid member whose allow id (server.sports_allow_id of the Whop user)
# is listed in SPORTS_UI_ALLOW. Everyone else gets exactly the flags-off answer (same bytes, byte-identical pages).

SECRET = "s" * 40
OWNER, OTHER = "user_owner123", "user_other456"


@pytest.fixture
def env(monkeypatch):
    import server
    for k in ("SPORTS_API", "SPORTS_API_MODE", "SPORTS_UI_ALLOW"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SPORTS_UI", "owner")
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("SPORTS_API_BASE", "https://scout-sports-api.onrender.com")
    monkeypatch.setenv("SPORTS_UI_ALLOW", " sa_000000000000000000000000 ," + server.sports_allow_id(OWNER))
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(server, "ADMIN_TOKEN", "a" * 40)
    return monkeypatch


def _client(user, paid=True, admin=False):
    H = _handler()

    class M(H):
        def member(self):
            return {"user": user, "ok": paid, "name": "x"} if user else None

        def allowed(self):
            return bool(user and paid)

    def get(path):
        h = M(path)
        if admin:
            h.headers["X-Admin-Token"] = "a" * 40
        h.do_GET()
        return h.code, h.wfile.getvalue()
    return get


def _baseline(monkeypatch, user, paid=True):
    """The same requests with SPORTS_UI unset (flags off)."""
    with monkeypatch.context() as m:
        m.delenv("SPORTS_UI", raising=False)
        get = _client(user, paid)
        return {p: get(p) for p in PATHS}


PATHS = ("/", "/lol", "/sports", "/sports/app.js", "/sports/slips.html", "/api/sports/token", "/api/sports/whoami",
         "/definitely-not-a-route")


def test_allow_id_formula_is_the_same_in_server_and_gateway():
    import server
    from sports import gateway
    assert server.sports_allow_id(OWNER) == gateway.allow_id(OWNER)
    assert server.sports_allow_id(OWNER).startswith("sa_") and len(server.sports_allow_id(OWNER)) == 27
    assert OWNER not in server.sports_allow_id(OWNER)


def test_owner_gets_the_page_the_link_and_a_token(env):
    import server
    get = _client(OWNER)
    code, body = get("/sports")
    assert code == 200 and b"<body class=\"sw\">" in body
    assert get("/sports/app.js")[0] == 200
    for p in ("/", "/lol"):
        code, body = get(p)
        assert code == 200 and body.count(b'href="/sports"') == 1
        assert server._SPORTS_NAV in body
    code, body = get("/api/sports/token")
    tok = json.loads(body)
    assert code == 200 and tok["token"].startswith("st1.") and tok["api_base"] == "https://scout-sports-api.onrender.com"
    from sports import authtoken
    claims = authtoken.verify(tok["token"])
    assert claims["role"] == "member" and claims["sub"] == authtoken.subject(OWNER)


@pytest.mark.parametrize("user,paid", [(OTHER, True), (OWNER, False), (None, False)])
def test_everyone_else_gets_exactly_the_flags_off_answer(env, user, paid):
    """Another paid member, the owner without a paid session, and an anonymous visitor: every sports path and both
    esports pages are byte-identical to SPORTS_UI unset."""
    off = _baseline(env, user, paid)
    get = _client(user, paid)
    for p in PATHS:
        assert get(p) == off[p], p
    assert b'href="/sports"' not in get("/")[1]


def test_an_empty_allow_list_admits_nobody(env):
    env.setenv("SPORTS_UI_ALLOW", " , ")
    off = _baseline(env, OWNER)
    get = _client(OWNER)
    for p in PATHS:
        assert get(p) == off[p], p


def test_whoami_needs_the_admin_token_and_never_returns_the_whop_id(env):
    import server
    code, body = _client(OTHER, admin=True)("/api/sports/whoami")
    out = json.loads(body)
    assert code == 200 and out["allow_id"] == server.sports_allow_id(OTHER) and out["paid"] is True
    assert OTHER.encode() not in body
    off = _baseline(env, OTHER)
    assert _client(OTHER)("/api/sports/whoami") == off["/api/sports/whoami"]      # no admin token: unknown route


def test_owner_mode_leaves_the_other_api_paths_on_sports_api(env):
    """SPORTS_UI=owner gates the page + token; other /api/sports/* paths keep SPORTS_API (off -> unknown route)."""
    off = _baseline(env, OWNER)
    get = _client(OWNER)
    assert get("/api/sports/status") == _client(OWNER)("/definitely-not-a-route") == off["/definitely-not-a-route"]


def test_owner_mode_in_a_fresh_process_imports_nothing_for_non_owners(tmp_path):
    """No Whop locally -> allowed() is True but there is no member, so nobody is the owner: every sports path equals
    an unknown route and the esports process never imports sports."""
    out = _run_child(tmp_path, SPORTS_UI="owner", SPORTS_UI_ALLOW="sa_" + "1" * 24, SPORTS_API_MODE="service",
                     SPORTS_TOKEN_SECRET="t" * 40, SPORTS_API_BASE="https://sports.example.test")
    assert out["sports_imported"] == []
    unknown = out["/definitely-not-a-route"]
    for p in ("/sports", "/api/sports/status", "/api/sports/offers?league=nba", "/api/sports/token"):
        assert out[p] == unknown, p
    assert out["/auth/me"][0] == 200
