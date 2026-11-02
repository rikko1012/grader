"""Research workspace preview (/preview/workspace): off by default, on with SCOUT_WORKSPACE_PREVIEW or for the owner, its
assets served only through fixed routes, and every existing page byte-identical with the route in place. The prototype is
sample data only; the wording tests hold it to the owner's site rules."""
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
const D = window.WORKSPACE_DATA, bad = [];
const sports = new Set();
for (const r of D.records) {
  sports.add(r.sport);
  for (const k of ["sport", "event", "player", "market", "offer", "scout", "history", "state", "consensus"]) if (!(k in r)) bad.push("no " + k);
  for (const k of ["id", "id_kind", "label", "competition", "start_utc", "lock_utc", "status"]) if (!(k in r.event)) bad.push("event " + k);
  for (const k of ["id", "name", "team", "opponent", "role"]) if (!(k in r.player)) bad.push("player " + k);
  for (const k of ["stat", "stat_label", "period", "period_label", "line_type"]) if (!(k in r.market)) bad.push("market " + k);
  for (const k of ["platform", "platform_label", "offer_id", "revision", "revision_kind", "line", "over", "under", "seen_at", "checked_at", "link"]) if (!(k in r.offer)) bad.push("offer " + k);
  for (const k of ["side", "projection", "range80", "p_over", "p_under", "p_push", "p_side", "grade", "units", "hold", "model_version", "forecast_id", "forecast_at", "validation"]) if (!(k in r.scout)) bad.push("scout " + k);
  for (const k of ["kind", "reason", "board_at", "retry_at"]) if (!(k in r.state)) bad.push("state " + k);
  const total = r.scout.p_over + r.scout.p_under + r.scout.p_push;
  if (Math.abs(total - 1) > 1e-9) bad.push(r.key + " chances sum to " + total);
  if (["nba", "nfl"].includes(r.sport) && (r.scout.grade || !r.scout.hold)) bad.push(r.key + " sports row with a letter or no hold");
  if (r.offer.link) bad.push(r.key + " sample offer with a live link");
  const h = r.history;
  if (h.l10) {
    if (h.l10.over + h.l10.under + h.l10.push !== h.l10.n) bad.push(r.key + " L10 does not add up");
    const over = h.recent.results.slice(0, 10).filter((g) => g.value > r.offer.line).length;
    if (over !== h.l10.over) bad.push(r.key + " L10 disagrees with its results");
  }
  if (r.sport === "lol" && (h.l10 || h.l5 || h.recent.results)) bad.push(r.key + " LoL row with per-game history");
}
const letter = (p) => (p >= 0.8 ? "A+" : p >= 0.74 ? "A" : p >= 0.68 ? "A-" : p >= 0.63 ? "B+" : p >= 0.57 ? "B" : p >= 0.55 ? "B-" : null);
const stake = (p) => (p >= 0.8 ? 3 : p >= 0.74 ? 2 : p >= 0.68 ? 1.5 : p >= 0.63 ? 1 : p >= 0.55 ? 0.5 : 0);
for (const r of D.records) {
  const s = r.scout;
  if (s.hold || !s.side) continue;
  if (s.grade !== letter(s.p_side)) bad.push(r.key + " shows " + s.grade + " at " + s.p_side);
  if (s.units !== stake(s.p_side) && !s.stake_reason) bad.push(r.key + " stake " + s.units + " with no reason");
  if (s.grade && !s.units && !s.stake_reason) bad.push(r.key + " graded 0U with no reason");
}
for (const row of new Set(D.records.map((r) => r.key))) {
  const offers = D.records.filter((r) => r.key === row), c = offers[0].consensus;
  if (c && c.n !== offers.filter((o) => o.offer.over.payout != null && o.offer.under.payout != null).length) bad.push(row + " consensus counts books not listed");
}
for (const id of new Set(D.receipts.map((r) => r.slip_id))) {
  const legs = D.receipts.filter((r) => r.slip_id === id);
  if (new Set(legs.map((r) => r.sport + r.platform + r.tracked_at)).size !== 1) bad.push(id + " mixes sport, platform or tracking time");
  if (legs.length < D.platforms[legs[0].platform].min) bad.push(id + " is below the platform minimum");
}
for (const r of D.records) if (r.sport === "lol" && r.offer.seen_at > D.feeds.lol.at) bad.push(r.key + " seen after its stale feed");
for (const tag of ["perf-", "lol-1", "-est-", "cm-", "Live model"]) if (JSON.stringify(D).includes(tag)) bad.push("real model name in the sample: " + tag);
console.log(JSON.stringify({ bad, sports: [...sports].sort(), records: D.records.length, withdrawn: D.records.filter((r) => r.state.kind === "withdrawn").length,
  push: D.records.filter((r) => r.scout.p_push > 0).length, unknownPayout: D.records.filter((r) => r.offer.over.payout === null).length,
  corrections: D.receipts.filter((r) => r.timeline.some((t) => t.kind === "correction")).length, pushes: D.receipts.filter((r) => r.timeline.some((t) => t.result === "push")).length }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_sample_records_match_the_display_row_contract(tmp_path):
    script = tmp_path / "check.js"
    script.write_text(NODE_CHECK, encoding="utf-8")
    out = subprocess.run(["node", str(script), str(WEB / "workspace-data.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["bad"] == [] and r["sports"] == ["cs2", "lol", "nba", "nfl"]
    assert r["records"] >= 30 and r["withdrawn"] >= 1 and r["push"] >= 1 and r["unknownPayout"] >= 1
    assert r["corrections"] >= 1 and r["pushes"] >= 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_scripts_parse():
    for name in ("workspace.js", "workspace-data.js"):
        assert subprocess.run(["node", "--check", str(WEB / name)], capture_output=True, timeout=60).returncode == 0, name
