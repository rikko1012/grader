"""Self-contained browser harness for the Sports pages (uifix): Playwright Chromium with every request answered in-process -
the static files straight from web/, /auth/me + /api/sports/token as a member, and /api/sports/* from a dict of
fixture answers (path -> dict or callable(query) -> (status, dict)). No server, no network, no store.
Every name / number in the fixtures that use this is a TEST FIXTURE."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB = Path(__file__).resolve().parents[2] / "web"
BASE = "http://scout.test"
TYPES = {".js": "application/javascript", ".css": "text/css", ".html": "text/html; charset=utf-8"}


def static(path):
    if path == "/sports":
        f = WEB / "sports.html"
    elif path == "/sports/results":                     # the public Results page (gateway serves results.html here)
        f = WEB / "sports" / "results.html"
    elif path.startswith("/sports/"):
        f = WEB / "sports" / path[len("/sports/"):]
    else:
        return None
    if not f.is_file():
        return None
    return f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream")


def install(page, api, member=None, posts=None):
    """api: {"/api/sports/<x>": dict | callable(query) -> dict | (status, dict)}. posts: list collecting POST bodies."""
    def handle(route):
        req = route.request
        u = urlparse(req.url)
        if u.netloc != "scout.test":
            return route.abort()
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/auth/me":
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps(member or {"required": True, "member": True, "name": "Test member"}))
        if u.path == "/api/sports/token":
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"token": "t", "exp": 4102444800, "api_base": ""}))
        if u.path.startswith("/api/"):
            if req.method == "POST" and posts is not None:
                posts.append((u.path, json.loads(req.post_data or "{}")))
            h = api.get(u.path)
            if h is None:
                return route.fulfill(status=404, content_type="application/json", body='{"error":"not_found"}')
            out = h(q) if callable(h) else h
            status, body = out if isinstance(out, tuple) else (200, out)
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
        s = static(u.path)
        if s is None:
            return route.fulfill(status=404, body="not found")
        return route.fulfill(status=200, content_type=s[1], body=s[0])
    page.route("**/*", handle)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return errors
