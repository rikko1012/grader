"""Sports service isolation boundary: st1 tokens, the route table contract, the standalone service (auth on every route,
CORS, caps, busy), the esports-side gateway (token issue, service / proxy modes, budgets, circuit) and the worker
supervisor. No network beyond 127.0.0.1."""
import base64
import hashlib
import hmac
import http.client
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sports import authtoken, gateway, service, store, web
from sports.tests import helpers as h

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.delenv("SPORTS_TOKEN_SECRET_PREV", raising=False)
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.delenv("SPORTS_API_MODE", raising=False)
    monkeypatch.setattr(web, "_CACHE", {})
    yield


# ------------------------------------------------------------------------------------------------------- tokens
def _forge(claims, secret=SECRET):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    mac = base64.urlsafe_b64encode(hmac.new(secret.encode(), f"st1.{body}".encode(), hashlib.sha256).digest())
    return f"st1.{body}.{mac.decode().rstrip('=')}"


def test_token_round_trip_and_pseudonymous_subject():
    t = authtoken.mint("user_abc123", "member", now=1000)
    c = authtoken.verify(t["token"], now=1001)
    assert c["role"] == "member" and c["aud"] == authtoken.AUD and c["ent"] == "paid"
    assert c["sub"].startswith("m_") and "user_abc123" not in t["token"] and "abc123" not in c["sub"]
    assert authtoken.mint("user_abc123", now=5)["sub"] == c["sub"]               # stable per member
    assert authtoken.mint("user_other", now=5)["sub"] != c["sub"]


@pytest.mark.parametrize("mutate,reason", [
    (lambda t: t[:-2] + ("A" if t[-2] != "A" else "B") + t[-1], "bad_signature"),
    (lambda t: "st2" + t[3:], "malformed"),
    (lambda t: t.split(".")[0] + ".." + t.split(".")[2], "malformed"),
    (lambda t: "", "malformed"),
    (lambda t: "x" * 2000, "malformed"),
])
def test_tampered_tokens_are_refused(mutate, reason):
    t = authtoken.mint("u1", now=1000)["token"]
    with pytest.raises(authtoken.TokenError) as e:
        authtoken.verify(mutate(t), now=1001)
    assert e.value.reason == reason


def test_client_cannot_raise_its_role_or_lifetime():
    t = authtoken.mint("u1", "member", now=1000)["token"]
    body = json.loads(base64.urlsafe_b64decode(t.split(".")[1] + "=="))
    body["role"] = "admin"                              # edit claims, keep the old MAC
    edited = "st1." + base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=") + "." + t.split(".")[2]
    with pytest.raises(authtoken.TokenError) as e:
        authtoken.verify(edited, now=1001)
    assert e.value.reason == "bad_signature"
    base = {"v": 1, "aud": authtoken.AUD, "sub": "m_x", "role": "member", "ent": "paid", "iat": 1000, "exp": 1000 + 3600}
    with pytest.raises(authtoken.TokenError) as e:                              # validly signed but too long-lived
        authtoken.verify(_forge(base), now=1001)
    assert e.value.reason == "bad_claims"
    for bad in ({"aud": "other"}, {"role": "owner"}, {"ent": "free"}, {"sub": "raw-whop-id"}):
        with pytest.raises(authtoken.TokenError):
            authtoken.verify(_forge(dict(base, exp=1500, **bad)), now=1001)
    with pytest.raises(authtoken.TokenError) as e:
        authtoken.verify(_forge(dict(base, exp=1500), secret="w" * 40), now=1001)
    assert e.value.reason == "bad_signature"


def test_expiry_clock_skew_and_ttl_cap(monkeypatch):
    t = authtoken.mint("u1", now=1000, ttl=600)
    assert authtoken.verify(t["token"], now=1599)
    with pytest.raises(authtoken.TokenError) as e:
        authtoken.verify(t["token"], now=1600)
    assert e.value.reason == "expired"
    with pytest.raises(authtoken.TokenError) as e:
        authtoken.verify(authtoken.mint("u1", now=2000)["token"], now=1000)
    assert e.value.reason == "not_yet_valid"
    monkeypatch.setenv("SPORTS_TOKEN_TTL_S", "99999")
    assert authtoken.mint("u1", now=0)["ttl_s"] == authtoken.MAX_TTL_S


def test_no_or_short_secret_means_nothing_is_minted_or_accepted(monkeypatch):
    t = authtoken.mint("u1")["token"]
    for v in (None, "short"):
        if v is None:
            monkeypatch.delenv("SPORTS_TOKEN_SECRET")
        else:
            monkeypatch.setenv("SPORTS_TOKEN_SECRET", v)
        for fn in (lambda: authtoken.mint("u1"), lambda: authtoken.verify(t)):
            with pytest.raises(authtoken.TokenError) as e:
                fn()
            assert e.value.reason == "not_configured"


def test_rotation_accepts_the_previous_secret(monkeypatch):
    old = authtoken.mint("u1")["token"]
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", "n" * 40)
    monkeypatch.setenv("SPORTS_TOKEN_SECRET_PREV", SECRET)
    assert authtoken.verify(old)["role"] == "member"
    monkeypatch.delenv("SPORTS_TOKEN_SECRET_PREV")
    with pytest.raises(authtoken.TokenError):
        authtoken.verify(old)


def test_bearer_parsing():
    assert authtoken.bearer("Bearer abc") == "abc" and authtoken.bearer("bearer  abc ") == "abc"
    assert authtoken.bearer("Basic abc") is None and authtoken.bearer(None) is None and authtoken.bearer("Bearer") is None


# -------------------------------------------------------------------------------------------- route table contract
def test_every_route_is_member_or_admin_and_admin_paths_are_admin():
    for path, entry in web.ROUTES.items():
        assert path.startswith("/api/sports/"), path
        assert len(entry) == 3 and ":" in entry[0], path
        target, access, methods = entry
        assert access in (web.MEMBER, web.ADMIN), path
        assert set(methods) <= {"GET", "POST"} and methods, path
        if path.startswith("/api/sports/admin/"):
            assert access == web.ADMIN, path
        assert path not in ("/api/sports/health", "/api/sports/token", "/api/sports/admin/ops",
                            "/api/sports/admin/gateway"), f"{path} is reserved by the service / gateway"


def test_every_route_handler_resolves():
    for path, (target, _, _) in web.ROUTES.items():
        assert callable(web._resolve(target)), path


def test_no_route_answers_without_a_verified_role():
    for path, (target, access, methods) in web.ROUTES.items():
        p = path[:-1] + "x" if path.endswith("/*") else path
        for m in methods:
            code, body, _ = web.dispatch(web.Request(path=p, method=m, role=None))
            assert code == 401 and json.loads(body)["error"] == "members_only", path
        if access == web.ADMIN:
            code, _, _ = web.dispatch(web.Request(path=p, method=methods[0], role="member", member="m_1"))
            assert code == 403, path


def test_dispatch_method_prefix_and_lazy_import_failure(monkeypatch):
    monkeypatch.setitem(web.ROUTES, "/api/sports/zz/*", ("sports.web:status", web.MEMBER, ("GET",)))
    monkeypatch.setitem(web.ROUTES, "/api/sports/broken", ("sports.api_does_not_exist:fn", web.MEMBER, ("GET",)))
    seen = {}
    monkeypatch.setitem(web._HANDLERS, "sports.web:status", lambda req: (seen.update(rest=req.rest), (200, {}))[1])
    assert web.dispatch(web.Request(path="/api/sports/zz/a/b", role="member"))[0] == 200 and seen["rest"] == "a/b"
    assert web.dispatch(web.Request(path="/api/sports/status", method="POST", role="member"))[0] == 405
    with pytest.raises(ModuleNotFoundError):                                     # service turns this into a 503
        web.dispatch(web.Request(path="/api/sports/broken", role="member"))
    assert web.dispatch(web.Request(path="/api/sports/nope", role="member"))[0] == 404


# --------------------------------------------------------------------------------------------- store pool limits
def test_pool_interrupts_a_query_over_the_budget():
    con = store.connect()
    store.put(con, h.offer())
    con.close()
    with pytest.raises(web.Busy):
        with web.store_ro(budget_s=0.05) as c:
            c.execute("WITH RECURSIVE r(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM r) SELECT count(*) FROM r").fetchone()
    assert web.pool_stats()["queries_interrupted"] >= 1
    with web.store_ro() as c:                                                    # the pool recovers
        assert c.execute("SELECT count(*) FROM records").fetchone()[0] == 1


def test_pool_saturation_is_busy_not_a_queue(monkeypatch):
    con = store.connect()
    con.close()
    monkeypatch.setattr(web, "_POOL", web._Pool(1))
    monkeypatch.setattr(web, "DB_WAIT_S", 0.05)
    with web.store_ro():
        t0 = time.time()
        with pytest.raises(web.Busy):
            with web.store_ro():
                pass
        assert time.time() - t0 < 1
    assert web.pool_stats()["waits_timed_out"] == 1 and web.pool_stats()["peak_in_use"] == 1


# ------------------------------------------------------------------------------------------- standalone service
@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(service, "_STATE", {"draining": False})
    srv = service.Server(("127.0.0.1", 0), threads=4)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(port, path, headers=None, method="GET", body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    hdrs = dict(r.getheaders())
    c.close()
    try:
        data = json.loads(data) if data else None
    except ValueError:
        pass
    return r.status, data, hdrs


def _auth(role="member"):
    return {"Authorization": "Bearer " + authtoken.mint("whop_user_1", role)["token"]}


def test_service_requires_a_valid_token_on_every_data_route(svc):
    con = store.connect()
    store.put(con, h.offer())
    con.close()
    assert _get(svc, "/api/sports/health")[0] == 200                             # the only open path, no data
    for path in ("/api/sports/status", "/api/sports/offers?league=nba", "/api/sports/admin/coverage"):
        code, body, _ = _get(svc, path)
        assert code == 401 and body["error"] == "members_only", path
        assert _get(svc, path, {"Authorization": "Bearer st1.forged.token"})[0] == 401
        assert _get(svc, path, {"Cookie": "cs2s=good-whop-cookie"})[0] == 401   # esports cookies mean nothing here
    code, body, _ = _get(svc, "/api/sports/offers?league=nba", _auth())
    assert code == 200 and body["count"] == 1 and body["offers"][0]["offer_id"] == "pp:1"
    assert _get(svc, "/api/sports/admin/coverage", _auth())[0] == 403
    assert _get(svc, "/api/sports/admin/coverage", _auth("admin"))[0] == 200


def test_service_refuses_everything_without_a_secret_and_with_the_flag_off(svc, monkeypatch):
    hdr = _auth()
    monkeypatch.setenv("SPORTS_API", "0")
    assert _get(svc, "/api/sports/status", hdr)[0] == 404
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.delenv("SPORTS_TOKEN_SECRET")
    code, body, _ = _get(svc, "/api/sports/status", hdr)
    assert code == 503 and body["error"] == "sports_auth_not_configured"


def test_service_cors_only_for_our_origin(svc, monkeypatch):
    monkeypatch.setenv("SPORTS_ALLOWED_ORIGINS", "https://cs2-scout.onrender.com")
    code, _, hd = _get(svc, "/api/sports/status", dict(_auth(), Origin="https://cs2-scout.onrender.com"))
    assert code == 200 and hd["Access-Control-Allow-Origin"] == "https://cs2-scout.onrender.com"
    code, _, hd = _get(svc, "/api/sports/status", dict(_auth(), Origin="https://evil.example"))
    assert code == 403 and "Access-Control-Allow-Origin" not in hd
    code, _, hd = _get(svc, "/api/sports/status", {"Origin": "https://cs2-scout.onrender.com"}, method="OPTIONS")
    assert code == 204 and "Authorization" in hd["Access-Control-Allow-Headers"]
    assert _get(svc, "/api/sports/status", {"Origin": "https://evil.example"}, method="OPTIONS")[0] == 403


def test_service_post_body_cap_and_admin_ops(svc, monkeypatch):
    big = b"{" + b" " * (service.BODY_MAX + 10) + b"}"
    code, _, _ = _get(svc, "/api/sports/status", dict(_auth(), **{"Content-Type": "application/json"}), "POST", big)
    assert code == 413
    assert _get(svc, "/api/sports/admin/ops", _auth())[0] == 403
    monkeypatch.setenv("ADMIN_TOKEN", "a" * 30)
    code, body, _ = _get(svc, "/api/sports/admin/ops", {"X-Admin-Token": "a" * 30})
    assert code == 200 and {"service", "db_pool", "cache", "feeds", "scheduler", "rss_mb"} <= set(body)
    assert _get(svc, "/api/sports/admin/ops", _auth("admin"))[0] == 200


def test_service_sheds_load_fast_when_every_slot_is_busy(monkeypatch):
    monkeypatch.setattr(service, "_STATE", {"draining": False})
    monkeypatch.setattr(service, "QUEUE_S", 0.05)
    gate = threading.Event()
    monkeypatch.setitem(web.ROUTES, "/api/sports/slow", ("sports.web:slow", web.MEMBER, ("GET",)))
    monkeypatch.setitem(web._HANDLERS, "sports.web:slow", lambda req: (gate.wait(5), (200, {"ok": 1}))[1])
    srv = service.Server(("127.0.0.1", 0), threads=2)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        res = []
        ts = [threading.Thread(target=lambda: res.append(_get(port, "/api/sports/slow", _auth())[0])) for _ in range(2)]
        for t in ts:
            t.start()
        time.sleep(0.3)
        t0 = time.time()
        code, body, _ = _get(port, "/api/sports/slow", _auth())
        assert code == 503 and body == {"error": "sports_busy"} and time.time() - t0 < 1.5
        gate.set()
        for t in ts:
            t.join(5)
        assert res == [200, 200]
    finally:
        gate.set()
        srv.shutdown()
        srv.server_close()


def test_a_failing_route_is_that_routes_503(svc, monkeypatch):
    def boom(req):
        raise RuntimeError("x")
    monkeypatch.setitem(web._HANDLERS, "sports.web:status", boom)
    code, body, _ = _get(svc, "/api/sports/status", _auth())
    assert code == 503 and body["error"] == "sports_unavailable"
    assert _get(svc, "/api/sports/health")[0] == 200


# ------------------------------------------------------------------------------------------------------- gateway
def _gw(path, query=None, allowed=True, admin=False, user="whop_user_1", headers=None, raw=""):
    code, body, ctype, hd = gateway.handle_get(path, raw, query or {}, member=lambda: user, allowed=lambda: allowed,
                                               admin=lambda: admin, headers=headers or {})
    return code, (json.loads(body) if ctype == "application/json" else body), hd


def test_gateway_token_needs_the_server_side_paid_check():
    code, body, _ = _gw("/api/sports/token", allowed=False)
    assert code == 401 and body["error"] == "members_only"
    code, body, hd = _gw("/api/sports/token")
    assert code == 200 and hd["Cache-Control"] == "no-store"
    c = authtoken.verify(body["token"])
    assert c["role"] == "member" and "whop_user_1" not in body["token"]
    assert authtoken.verify(_gw("/api/sports/token", admin=True)[1]["token"])["role"] == "admin"
    assert _gw("/api/sports/token", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403


def test_gateway_without_a_secret_refuses_to_mint(monkeypatch):
    monkeypatch.delenv("SPORTS_TOKEN_SECRET")
    code, body, _ = _gw("/api/sports/token")
    assert code == 503 and body["error"] == "sports_auth_not_configured"


def test_gateway_service_mode_runs_no_sports_work(monkeypatch):
    monkeypatch.setenv("SPORTS_API_BASE", "https://sports.example.test/")
    for m in [k for k in sys.modules if k in ("sports.web", "sports.store")]:
        monkeypatch.delitem(sys.modules, m)
    code, body, _ = _gw("/api/sports/offers", {"league": "nba"})
    assert code == 404 and body == {"error": "not_here", "api_base": "https://sports.example.test"}
    assert "sports.web" not in sys.modules and "sports.store" not in sys.modules
    assert _gw("/api/sports/token")[1]["api_base"] == "https://sports.example.test"
    assert _gw("/api/sports/admin/gateway")[0] == 403 and _gw("/api/sports/admin/gateway", admin=True)[0] == 200


def test_gateway_static_page_and_assets_are_contained():
    code, body, _ = _gw("/sports")
    assert code == 200 and b"Scout Sports" in body
    for bad in ("/sports/../server.py", "/sports/..%2fserver.py", "/sports/x.py", "/sports/nope.css"):
        assert _gw(bad)[0] == 404, bad


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    delay = 0.0
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        time.sleep(type(self).delay)
        body = json.dumps({"ok": True, "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def upstream(monkeypatch):
    _Upstream.delay, _Upstream.seen = 0.0, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("SPORTS_API_MODE", "proxy")
    monkeypatch.setenv("SPORTS_UPSTREAM", f"127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(gateway, "_PROXY", gateway._Proxy())
    yield srv
    srv.shutdown()
    srv.server_close()


def test_proxy_forwards_with_a_minted_token_and_never_the_admin_token(upstream):
    code, body, _ = _gw("/api/sports/offers", {"league": "nba", "token": "ADMIN-SECRET"}, raw="league=nba&token=ADMIN-SECRET")
    assert code == 200 and body["ok"] and "ADMIN-SECRET" not in body["path"] and "league=nba" in body["path"]
    path, auth = _Upstream.seen[-1]
    assert authtoken.verify(authtoken.bearer(auth))["role"] == "member"
    assert _gw("/api/sports/offers", allowed=False)[0] == 401                   # no paid session -> nothing forwarded
    assert len(_Upstream.seen) == 1
    _gw("/api/sports/status")
    assert gateway._PROXY.idle.qsize() >= 1                                       # kept-alive connection reused


def test_proxy_is_budgeted_busy_and_fails_fast(upstream, monkeypatch):
    monkeypatch.setenv("SPORTS_PROXY_MAX_INFLIGHT", "1")
    monkeypatch.setenv("SPORTS_PROXY_QUEUE_MS", "10")
    monkeypatch.setenv("SPORTS_PROXY_TIMEOUT_S", "0.3")
    _Upstream.delay = 1.0
    out = []
    t = threading.Thread(target=lambda: out.append(_gw("/api/sports/status")))
    t.start()
    time.sleep(0.1)
    t0 = time.time()
    code, body, _ = _gw("/api/sports/status")
    assert code == 503 and body["error"] == "sports_busy" and time.time() - t0 < 0.2
    t.join(3)
    assert out[0][0] == 503 and out[0][1]["error"] == "sports_unavailable"       # upstream over the 0.3 s budget


def test_proxy_circuit_opens_on_a_dead_upstream(monkeypatch):
    monkeypatch.setenv("SPORTS_API_MODE", "proxy")
    monkeypatch.setenv("SPORTS_UPSTREAM", "127.0.0.1:9")                          # nothing listens
    monkeypatch.setattr(gateway, "_PROXY", gateway._Proxy())
    for _ in range(5):
        assert _gw("/api/sports/status")[1]["error"] == "sports_unavailable"
    t0 = time.time()
    code, body, hd = _gw("/api/sports/status")
    assert code == 503 and hd.get("Retry-After") == "5" and time.time() - t0 < 0.05
    assert gateway.stats()["proxy_circuit_open"]


# ------------------------------------------------------------------------------------------------- scheduler
# (job lock / memory caps / triggers: sports/tests/test_scheduler.py)


def test_backoff_is_jittered_and_capped():
    xs = [service.backoff(a) for a in range(12) for _ in range(20)]
    assert max(xs) <= 900 and min(xs) >= 15 and len({round(x, 3) for x in xs}) > 50


# ------------------------------------------------------------------------------------------ deploy config / process
SPORTS_TEMPLATE = "deploy/render.sports-api.yaml"


def _render_services(rel="render.yaml"):
    """Tiny render.yaml reader (no yaml dependency in CI): service name -> {env key: (value or None if sync:false)}."""
    from pathlib import Path
    out, cur, key = {}, None, None
    for line in (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8").splitlines():
        s = line.split("#", 1)[0].rstrip()
        if s.strip().startswith("name:") and line.startswith("    name:"):
            cur = out.setdefault(s.split(":", 1)[1].strip(), {})
        elif s.strip().startswith("- key:"):
            key = s.split(":", 1)[1].strip()
            cur[key] = None
        elif s.strip().startswith("value:") and key:
            cur[key] = s.split(":", 1)[1].strip().strip('"')
        elif s.strip().startswith("sync:"):
            cur[key] = None
    return out


def test_render_config_keeps_every_sports_flag_off_and_secrets_out_of_git():
    svc = _render_services() | _render_services(SPORTS_TEMPLATE)
    assert {"cs2-scout", "scout-sports-api"} <= set(svc)
    for name in ("cs2-scout", "scout-sports-api"):
        for f in ("SPORTS_UI", "SPORTS_API", "SPORTS_INGEST", "SPORTS_TRAIN", "SPORTS_ASSISTANT"):
            assert svc[name].get(f, "0") == "0", (name, f)
        assert "SPORTS_TOKEN_SECRET" in svc[name] and svc[name]["SPORTS_TOKEN_SECRET"] is None   # sync: false
    assert svc["cs2-scout"]["SPORTS_API_MODE"] == "service"
    s = svc["scout-sports-api"]
    assert s["SPORTS_DATA_DIR"] == "/sports-data" and s["ODDS_API_KEY"] is None and s["ADMIN_TOKEN"] is None
    assert s["ANTHROPIC_API_KEY"] is None


def test_paid_sports_service_is_only_in_the_opt_in_template_not_the_synced_blueprint():
    """The root render.yaml is auto-synced: a service / disk declared there is created and billed on merge whatever the
    flags say. The paid scout-sports-api service + its 20 GB disk live only in the opt-in template, which claims no
    authorization it does not have."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    rnd = (root / "render.yaml").read_text(encoding="utf-8")
    assert set(_render_services()) == {"cs2-scout"}
    code = "\n".join(line.split("#", 1)[0] for line in rnd.splitlines())
    for paid in ("scout-sports-api", "scout-sports-data", "Dockerfile.sports", "sports.service"):
        assert paid not in code, paid
    assert code.count("disk:") == 1 and "name: cs2-scout-data" in code
    tpl = (root / SPORTS_TEMPLATE).read_text(encoding="utf-8")
    assert set(_render_services(SPORTS_TEMPLATE)) == {"scout-sports-api"}
    assert "name: scout-sports-data" in tpl and "sizeGB: 5" in tpl and "plan: starter" in tpl   # owner 2026-10-24
    head = tpl.split("services:", 1)[0].lower()
    assert "never create a second service" in head and "paid" in head and "pending a staging" in head
    assert "flags off" in head
    for claim in ("owner approved", "purchase approved", "approved 20"):
        assert claim not in tpl.lower() and claim not in rnd.lower(), claim
    # nothing else in the repository is a second Blueprint Render could pick up
    assert not list(root.glob("render.y*ml.*")) and not (root / "render.yml").exists()


def test_the_service_process_starts_with_flags_off_and_serves_nothing(tmp_path):
    import os
    import socket
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_") and k != "ODDS_API_KEY"}
    env.update({"SPORTS_DATA_DIR": str(tmp_path), "PYTHONPATH": str(root)})
    p = subprocess.Popen([sys.executable, "-m", "sports.service", "--host", "127.0.0.1", "--port", str(port)],
                         cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                code, body, _ = _get(port, "/api/sports/health")
                break
            except OSError:
                time.sleep(0.1)
        assert code == 200 and body["ok"] and body["store_ready"] is False
        assert _get(port, "/api/sports/status")[0] == 401
        code, body, _ = _get(port, "/api/sports/status", _auth())                 # child has no secret: refused
        assert code == 503 and body["error"] == "sports_auth_not_configured"
    finally:
        p.kill()
        p.wait(10)


def _ops(**kw):
    now = time.time()
    o = {"flags": {"SPORTS_INGEST": True}, "scheduler_alive": True,
         "scheduler": {"uptime_s": 10, "jobs": {"ingest": {"enabled": True, "every_s": 900, "last_ok_at": now - 60,
                                                          "failures_in_a_row": 0, "last_exit": {"code": 0}}}},
         "feeds": {"store_mb": 100.0, "platforms": {"prizepicks": {"age_min": 5}, "kalshi": {"age_min": 40}}},
         "rss_mb": 50, "rss_max_mb": 160, "service": {"requests": 1000, "rejected_busy": 0, "errors_5xx": 0},
         "db_pool": {"waits_timed_out": 0, "queries_interrupted": 0}, "token_configured": True}
    for k, v in kw.items():
        o[k] = v
    return o


def _sched(**ingest):
    j = {"enabled": True, "every_s": 900, "last_ok_at": time.time() - 60, "failures_in_a_row": 0,
         "last_exit": {"code": 0}}
    j.update(ingest)
    return {"uptime_s": 100000, "jobs": {"ingest": j}}


def test_alerts_clean_and_each_rule(monkeypatch):
    monkeypatch.setenv("SPORTS_STORE_MAX_MB", "8000")
    assert service.alerts(_ops()) == []
    codes = lambda o: {a["code"] for a in service.alerts(o)}                      # noqa: E731
    assert codes(_ops(scheduler_alive=False)) == {"scheduler_down"}
    assert codes(_ops(scheduler=_sched(last_ok_at=time.time() - 4 * 900))) == {"worker_down"}
    assert codes(_ops(scheduler=_sched(last_ok_at=None))) == {"worker_down"}      # never succeeded since start
    assert "job_failing" in codes(_ops(scheduler=_sched(failures_in_a_row=3)))
    assert "job_over_cap" in codes(_ops(scheduler=_sched(failures_in_a_row=2, cap_mb=290,
                                                         last_exit={"code": "killed_over_cap (301 > 290 MB)"})))
    assert "job_starved" in codes(_ops(scheduler=_sched(queued_since=time.time() - 4000)))
    assert codes(_ops(feeds={"store_mb": 1, "platforms": {"underdog": {"age_min": 60}, "kalshi": {"age_min": 60}}})) \
        == {"feed_stale"}                                                         # kalshi has the longer limit
    assert codes(_ops(feeds={"store_mb": 6500, "platforms": {}})) == {"store_large"}
    assert codes(_ops(feeds={"store_mb": 8100, "platforms": {}})) == {"store_full_ingest_paused"}
    assert codes(_ops(rss_mb=140)) == {"service_memory_high"}
    assert codes(_ops(feeds={"store_mb": 1, "platforms": {}, "disk": {"used_pct": 81.0}})) == {"disk_80"}
    assert codes(_ops(feeds={"store_mb": 1, "platforms": {}, "disk": {"used_pct": 93.0}})) == {"disk_90"}
    assert codes(_ops(service={"requests": 100, "rejected_busy": 5, "errors_5xx": 0})) == {"service_shedding"}
    assert codes(_ops(db_pool={"waits_timed_out": 1, "queries_interrupted": 0})) == {"store_slow"}
    assert codes(_ops(token_configured=False)) == {"token_secret_missing"}
    off = _ops(flags={"SPORTS_INGEST": False}, scheduler=_sched(last_ok_at=None))
    assert service.alerts(off) == []                                             # ingest off: no collector alerts


def test_member_answers_get_etag_304_and_gzip_once_per_body(svc, monkeypatch):
    import gzip as gz
    big = {"rows": [{"i": i, "player": "x" * 40} for i in range(500)]}
    monkeypatch.setitem(web.ROUTES, "/api/sports/bigsnap", ("sports.web:bigsnap", web.MEMBER, ("GET",)))
    monkeypatch.setitem(web._HANDLERS, "sports.web:bigsnap", lambda req: (200, big))
    built0 = service.ENC_STATS["gzip_built"]
    code, body, hd = _get(svc, "/api/sports/bigsnap", _auth())
    assert code == 200 and body == big and hd["Cache-Control"] == "private, no-cache" and "ETag" in hd
    assert "Content-Encoding" not in hd                                          # no Accept-Encoding: identity
    c = http.client.HTTPConnection("127.0.0.1", svc, timeout=10)
    for _ in range(3):
        c.request("GET", "/api/sports/bigsnap", headers=dict(_auth(), **{"Accept-Encoding": "gzip, deflate"}))
        r = c.getresponse()
        data = r.read()
        assert r.status == 200 and r.getheader("Content-Encoding") == "gzip"
        assert json.loads(gz.decompress(data)) == big and r.getheader("ETag").endswith('-gz"')
    gz_etag = r.getheader("ETag")
    c.close()
    assert service.ENC_STATS["gzip_built"] - built0 == 1                         # compressed once, served 3 times
    for tag in (hd["ETag"], gz_etag, "W/" + hd["ETag"]):
        code, body, h2 = _get(svc, "/api/sports/bigsnap", dict(_auth(), **{"If-None-Match": tag}))
        assert code == 304 and body is None
    assert _get(svc, "/api/sports/bigsnap", dict(_auth(), **{"If-None-Match": '"other"'}))[0] == 200
    code, _, h3 = _get(svc, "/api/sports/bigsnap")                               # no token: 401, never cached
    assert code == 401 and h3["Cache-Control"] == "no-store" and "ETag" not in h3


def test_gzip_single_flight_compresses_once_under_simultaneous_misses():
    body = b'{"x": "' + b"y" * 200000 + b'"}'
    etag = service.etag_of(body)
    out = []
    ts = [threading.Thread(target=lambda: out.append(service.gzipped(etag, body))) for _ in range(30)]
    n0 = service.ENC_STATS["gzip_built"]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(out) == 30 and len(set(out)) == 1 and service.ENC_STATS["gzip_built"] - n0 == 1
    assert service.accepts_gzip("br, gzip;q=0.8") and not service.accepts_gzip("gzip;q=0") and not service.accepts_gzip(None)
