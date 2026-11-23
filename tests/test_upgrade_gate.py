"""The SCOUT_UPGRADE gate: off, the member pages, their assets and the API answer exactly as before and nothing loads; on, the same pages
carry one stylesheet and one script. The page files themselves are never edited. No network and no production data."""
import ast
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import grade_display
import hltv
import results_stated
import results_view
import server
import whop_auth
import workspace_api
from tests.workspace_fixture import Fixture

ROOT = Path(server.__file__).parent
MARK_CSS, MARK_JS = b"/upgrade/upgrade.css", b"/upgrade/upgrade.js"
OWNER = "the-owner"


def get(base, path, cookie=None, headers=None):
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = f"{whop_auth.COOKIE}={cookie}"
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=h), timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("SPORTS_UI", "SPORTS_API", "SPORTS_UI_ALLOW", "SCOUT_UPGRADE", "SCOUT_WORKSPACE_PREVIEW", "REQUIRE_WHOP", "RENDER", "RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SCOUT_UPGRADE", "owner")      # 2026-11-03: unset = every member; these tests pin the owner-only gate
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "ADMIN_TOKEN", "owner-token")
    monkeypatch.setattr(server, "lol_public", lambda: True)
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: {"user": v[5:], "name": "Tester", "ok": True} if v and v.startswith("good-") else None)
    monkeypatch.setattr(whop_auth, "cookie_valid", lambda v: bool(v and v.startswith("good-")))
    monkeypatch.setattr(whop_auth, "cookie_user", lambda v: v[5:] if v and v.startswith("good-") else None)
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_RES_BYTES", [0, 0.0]), ("_MAP1_STATUS", [0.0, None]), ("BOARD", {})):
        monkeypatch.setattr(server, name, val)
    fx = Fixture(tmp_path)
    fx.install(monkeypatch.setattr)
    workspace_api.reset()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    fx.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield fx
    httpd.shutdown()
    httpd.server_close()
    workspace_api.reset()


def test_the_page_files_carry_no_upgrade_code():
    for name in ("index.html", "lol.html", "sports.html", "sports/app.js", "sports/table.js"):
        body = (ROOT / "web" / name).read_bytes()
        assert b"/upgrade/" not in body and b"data-upgrade" not in body and b"SCOUT_UPGRADE" not in body, name


def test_flag_off_pages_are_the_files_byte_for_byte(env):
    for path, name in (("/", "index.html"), ("/lol", "lol.html")):
        st, body, _ = get(env.base, path, "good-u1")
        assert st == 200 and body == (ROOT / "web" / name).read_bytes(), path


def test_sports_pages_are_the_files_off_and_carry_the_tags_on(env, monkeypatch):
    monkeypatch.setenv("SPORTS_UI", "1")
    admin = {"X-Admin-Token": "owner-token"}                # owner 2026-11-09: the Results page is admin only
    st, body, _ = get(env.base, "/sports", "good-u1")
    assert st == 200 and body == (ROOT / "web" / "sports.html").read_bytes()
    monkeypatch.setenv("SCOUT_UPGRADE", "off")           # the admin passes the owner gate, so the plain file is checked flag off
    st, body, _ = get(env.base, "/sports/results", "good-u1", admin)
    assert st == 200 and body == (ROOT / "web" / "sports" / "results.html").read_bytes()
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    assert b'data-page="sports"' in get(env.base, "/sports", "good-u1")[1]
    assert b'data-page="sports-results"' in get(env.base, "/sports/results", "good-u1", admin)[1]
    st, body, _ = get(env.base, "/sports/app.js", "good-u1")
    assert st == 200 and body == (ROOT / "web" / "sports" / "app.js").read_bytes()


def test_the_nav_link_and_the_upgrade_sit_together_on_one_page(env, monkeypatch):
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    a = get(env.base, "/", "good-u1")[1]
    assert a.count(b'href="/sports"') == 1 and a.count(MARK_JS) == 1


def test_the_page_answers_304_to_its_own_etag_and_a_change_of_flag_changes_it(env, monkeypatch):
    st, _, hdr = get(env.base, "/", "good-u1")
    off = hdr["ETag"]
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    st, _, hdr = get(env.base, "/", "good-u1", {"If-None-Match": off})
    assert st == 200 and hdr["ETag"] != off               # a browser holding the plain page gets the upgraded one
    assert get(env.base, "/", "good-u1", {"If-None-Match": hdr["ETag"]})[0] == 304


def test_flag_off_assets_and_api_are_the_unknown_route_answer(env):
    before = get(env.base, "/no/such/route", "good-u1")
    assert get(env.base, "/upgrade/upgrade.js", "good-u1")[0] == before[0] == 404
    assert get(env.base, "/upgrade/upgrade.css", "good-u1")[0] == 404
    for path in ("/upgrade/hub.js", "/upgrade/hub.css"):
        assert get(env.base, path, "good-u1")[0] == 404
    for route in ("board", "me", "hits", "stated"):
        st, body, _ = get(env.base, "/api/upgrade/" + route, "good-u1")
        assert st == 404 and b"upgrade" not in body or st in (401, 403)
    assert workspace_api._ST["store"] is None


def test_a_member_gets_the_upgrade_only_with_the_flag(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    for path, name in (("/", "cs2"), ("/lol", "lol")):
        st, body, _ = get(env.base, path, "good-u1")
        assert st == 200 and MARK_CSS in body and MARK_JS in body and f'data-page="{name}"'.encode() in body and f'data-upgrade="{name}"'.encode() in body
    st, body, hdr = get(env.base, "/upgrade/upgrade.js", "good-u1")
    assert st == 200 and b"/api/upgrade/" in body and hdr["Content-Type"].startswith("text/javascript")
    assert get(env.base, "/upgrade/upgrade.css", "good-u1")[0] == 200
    for path in ("/upgrade/hub.js", "/upgrade/hub.css"):
        assert get(env.base, path, "good-u1")[0] == 200
        assert get(env.base, path)[0] == 404
    assert json.loads(get(env.base, "/api/upgrade/me", "good-u1")[1])["sports"]["cs2"] is True


def test_unset_flag_means_every_paid_member(env, monkeypatch):
    monkeypatch.delenv("SCOUT_UPGRADE", raising=False)      # owner 2026-11-03: "make it live"
    st, body, _ = get(env.base, "/", "good-u1")
    assert st == 200 and MARK_JS in body


def test_signed_out_visitors_keep_the_landing_page(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    st, body, _ = get(env.base, "/")
    assert st == 200 and MARK_JS not in body
    assert get(env.base, "/upgrade/upgrade.js")[0] == 404
    assert get(env.base, "/api/upgrade/me")[0] == 404


def test_flag_unset_the_owner_alone_sees_it(env, monkeypatch):
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    assert MARK_JS in get(env.base, "/", f"good-{OWNER}")[1]
    assert MARK_JS not in get(env.base, "/", "good-u1")[1]
    assert get(env.base, "/api/upgrade/me", f"good-{OWNER}")[0] == 200
    assert get(env.base, "/api/upgrade/me", "good-u1")[0] == 404
    monkeypatch.delenv("SPORTS_UI_ALLOW")
    st, body, _ = get(env.base, "/?token=owner-token", "good-u1")
    assert st == 200 and MARK_JS in body
    assert get(env.base, "/upgrade/upgrade.js", "good-u1", {"Referer": env.base + "/?token=owner-token"})[0] == 200
    assert get(env.base, "/api/upgrade/me", "good-u1", {"X-Admin-Token": "owner-token"})[0] == 200
    assert get(env.base, "/upgrade/upgrade.js", "good-u1", {"Referer": env.base + "/?token=wrong"})[0] == 404


def test_a_member_off_the_list_never_reaches_whop_on_a_page_load(env, monkeypatch):
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    calls = []
    real = whop_auth.session
    monkeypatch.setattr(whop_auth, "session", lambda v: calls.append(v) or real(v))
    assert MARK_JS not in get(env.base, "/", "good-u1")[1] and not calls
    assert MARK_JS in get(env.base, "/", f"good-{OWNER}")[1] and calls


def test_cookie_user_reads_the_signed_cookie_alone(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("WHOP_COOKIE_SECRET", "test-secret")
    monkeypatch.setattr(whop_auth, "_secret", lambda: b"test-secret")
    good = whop_auth._sign({"u": "user_1", "exp": time.time() + 60})
    assert whop_auth.cookie_user(good) == "user_1"
    assert whop_auth.cookie_user(whop_auth._sign({"u": "user_1", "exp": time.time() - 1})) is None
    assert whop_auth.cookie_user("forged") is None and whop_auth.cookie_user(None) is None


def test_off_turns_it_off_for_the_owner_too(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "off")
    monkeypatch.setenv("SPORTS_UI_ALLOW", server.sports_allow_id(OWNER))
    assert MARK_JS not in get(env.base, "/?token=owner-token", f"good-{OWNER}")[1]
    assert get(env.base, "/api/upgrade/me", f"good-{OWNER}", {"X-Admin-Token": "owner-token"})[0] == 404


def test_the_upgraded_page_is_one_object_per_page_version():
    body = b'<html lang="en"><head></head><body><p>x</p></body></html>'
    a, b = server.upgrade_page(body, "cs2"), server.upgrade_page(body, "cs2")
    assert a is b and a != body and a.count(MARK_JS) == 1 and a.count(MARK_CSS) == 1 and b'<html data-upgrade="cs2" lang="en">' in a
    assert server.upgrade_page(body, "lol") is not a


def test_a_page_without_its_markers_goes_out_as_it_is():
    body = b"<p>no markers</p>"
    assert server.upgrade_page(body, "cs2") is body


def test_a_fresh_flag_off_process_never_imports_the_workspace_store(tmp_path):
    code = ("import sys, server, os\n"
            "assert not [m for m in ('workspace_api', 'workspace_store', 'workspace_rules', 'workspace_lol', 'workspace_sports') if m in sys.modules]\n"
            "assert server.upgrade_page(b'<html ><head></head><body></body></html>', 'cs2')\n"
            "assert 'workspace_api' not in sys.modules\n")
    env = dict(os.environ, CS2_DATA_DIR=str(tmp_path))
    env.pop("SCOUT_UPGRADE", None)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


def test_the_gate_is_the_only_new_way_in():
    tree = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "upgrade_page"]
    assert len(calls) == 5                       # index, index + sports link, lol, lol + sports link, sports
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert src.count("self._workspace_api(") == 4


def test_sports_letters_stay_out_while_a_league_is_held():
    import workspace_sports as ws
    rows = [{"grade": "A", "n": 3, "wins": 2, "losses": 1, "pushes": 0, "hit_rate": 0.667}]
    assert ws._letters({"grades": {"rows": rows}, "model_gate": {"contained": True}}) == []
    assert ws._letters({"grades": {"rows": rows, "withheld": True}}) == []
    assert ws._letters({"grades": {"rows": rows}}) == [{"name": "A", "picks": 3, "wins": 2, "losses": 1, "pushes": 0, "observed": 0.667, "printed": None}]


# ---------------------------------------------------------------------------------------------------------------- stated and observed

def rows():
    out = []
    for i, (p, over) in enumerate([(0.60, 1), (0.60, 0), (0.64, 1), (0.40, 0), (0.70, 1), (0.75, 1), (0.81, 0), (0.52, 1), (None, 1)]):
        out.append({"match": f"m{i // 2}", "p": p, "over": over, "prop": "kills" if i % 2 else "hs", "scope": "12", "version": "v1", "start": "2026-10-20T10:00:00+00:00", "units": 1, "forecast_id": i})
    return out


def test_stated_observed_counts_the_member_results_picks():
    ex = {"at": "2026-11-01T00:00:00+00:00", "rows": rows(), "pushes": [{"match": "m9", "p": 0.61, "prop": "kills", "scope": "12", "units": 1, "forecast_id": 90}, {"match": "m9", "p": 0.52, "prop": "kills", "scope": "12", "units": 1, "forecast_id": 91}], "excluded": {"push on the board line": 2}}
    base, d = results_view.member_results(ex, B=0), results_stated.stated_observed(ex)
    assert d["totals"]["picks"] == base["picks"]["picks"] == 7 and d["totals"]["wins"] == base["picks"]["wins"]
    assert d["totals"]["pushes"] == base["picks"]["pushes"] == 1
    assert sum(x['decided'] for x in d['calendar']['days'])==7
    assert d['calendar']['missing_dates']==1
    assert d["rule"] == base["rule"]
    assert sum(x["picks"] for x in d["by_letter"]) == 7 and sum(x["picks"] for x in d["by_market"]) == 7
    assert sum(x["pushes"] for x in d["by_letter"]) == 1 and sum(x["pushes"] for x in d["by_market"]) == 1
    letters = {x["name"]: x for x in d["by_letter"]}
    assert set(letters) == {"A+", "A", "A-", "B+", "B"}
    assert letters["B"]["picks"] == 3 and letters["B"]["wins"] == 2 and letters["B"]["observed"] == 0.6667 and letters["B"]["printed"] == 0.6     # 0.40 reads as Under at 60%
    assert letters["B"]["pushes"] == 1 and letters["B+"]["pushes"] == 0


def test_stated_observed_letter_is_the_published_scale():
    for p, want in ((0.57, "B"), (0.63, "B+"), (0.68, "A-"), (0.74, "A"), (0.80, "A+")):
        assert grade_display._letter_from_p(p) == want
        d = results_stated.stated_observed({"rows": [{"match": "m", "p": p, "over": 1, "prop": "kills", "scope": "12"}], "pushes": []})
        assert d["by_letter"][0]["name"] == want


def test_stated_observed_holds_map_1_back_with_member_results():
    ex = {"rows": [{"match": "m", "p": 0.6, "over": 1, "prop": "kills", "scope": "1", "units": 1, "forecast_id": 1}], "pushes": [{"match": "m", "p": 0.6, "prop": "kills", "scope": "1", "units": 1, "forecast_id": 2}]}
    d = results_stated.stated_observed(ex)
    assert d["totals"]["picks"] == 0 and d["totals"]["pushes"] == 0 and d["by_market"] == []
    assert results_stated.stated_observed(ex, map1_min=1)["totals"]["picks"] == 1


def test_stated_route_reads_the_stored_export(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    st, body, _ = get(env.base, "/api/upgrade/stated", "good-u1")
    d = json.loads(body)
    assert st == 200 and d["totals"]["picks"] == 60 and d["by_letter"][0]["name"] == "B" and d["by_market"][0]["name"] == "Kills Maps 1–2"
    assert "ev" not in d and "roi" not in json.dumps(d).lower()


# ---------------------------------------------------------------------------------------------------------------- hit-rate cells

def series(values):
    return [{"match_id": f"h-{i}", "number": n, "date": f"2026-10-{28 - i:02d}", "kills": v, "headshots": 8, "opponent": f"T{i}", "map": "Mirage"} for i, v in enumerate(values) for n in (1, 2)]


def test_hits_add_l5_and_l20_only_when_the_rows_reproduce_the_saved_l10(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    vals = [24 if i % 5 < 3 else 15 for i in range(20)]         # 6 of 10 over the fixture's saved tally at 38.5 (the map total is 48 or 30)
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: series(vals))
    d = json.loads(get(env.base, "/api/upgrade/hits", "good-u1")[1])
    x = d["rows"]["underdog|11111111-0000-4000-8000-000000000001"]
    assert x["l5"] == {"over": 3, "under": 2, "push": 0, "n": 5} and x["l20"] == {"over": 12, "under": 8, "push": 0, "n": 20}
    assert x["l10"] == {"over": 6, "under": 4, "push": 0, "n": 10}       # sweep cs2m-1: the same HLTV series' L10 rides along
    assert d["complete"] is True
    assert "underdog|11111111-0000-4000-8000-000000000003" not in d["rows"] or d["rows"]


def test_hits_count_the_newest_results_even_when_they_disagree_with_the_saved_l10(env, monkeypatch):
    # owner 2026-11-04: L5 is never empty when L10 is shown; the card's chart still keeps the saved-L10 check
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setattr(hltv, "recent_series", lambda *a, **k: series([10] * 20))
    rows = json.loads(get(env.base, "/api/upgrade/hits", "good-u1")[1])["rows"]
    assert rows and all(r["l5"]["n"] == 5 and r["l20"]["n"] == 20 for r in rows.values())
    # sweep cs2m-1: the HLTV L10 of those same results (what the v3 card counts), even where the saved L10 differs
    assert all(r["l10"]["n"] == 10 and r["l10"]["push"] + r["l10"]["over"] + r["l10"]["under"] == 10 for r in rows.values())


def test_hits_never_make_a_member_wait_and_stop_at_the_budget(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.setattr(workspace_api, "HITS_BUDGET_S", 0.05)
    calls = []

    def slow(*a, **k):
        calls.append(1)
        time.sleep(0.2)
        return series([24] * 20)
    monkeypatch.setattr(hltv, "recent_series", slow)
    d = json.loads(get(env.base, "/api/upgrade/hits", "good-u1")[1])
    assert d["complete"] is False and len(calls) < 4
    with workspace_api._HITS_DOC["lock"]:                       # a pass is running: the last copy goes out at once
        t0 = time.time()
        workspace_api._HITS_DOC["at"] = 0
        assert get(env.base, "/api/upgrade/hits", "good-u1")[0] == 200
        assert time.time() - t0 < 1


# ---------------------------------------------------------------------------------------------------------------- SCOUT_UI_TOKENS

TOKENS_CSS = b"/upgrade/tokens.css"


def test_ui_tokens_off_leaves_the_upgraded_pages_and_hub_css_as_they_were(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.delenv("SCOUT_UI_TOKENS", raising=False)
    before = get(env.base, "/no/such/route", "good-u1")
    for path in ("/", "/lol"):
        st, body, _ = get(env.base, path, "good-u1")
        assert st == 200 and TOKENS_CSS not in body and b"data-ui-tokens" not in body
        assert b"['/upgrade/upgrade.css','/upgrade/hub.css']" in body and b"html.scout-boot{background:#101215}" in body
    st, body, _ = get(env.base, "/upgrade/hub.css", "good-u1")
    assert st == 200 and body == (ROOT / "web" / "upgrade-hub.css").read_bytes()
    assert get(env.base, "/upgrade/tokens.css", "good-u1")[0] == before[0] == 404
    assert "ui_tokens" not in json.loads(get(env.base, "/api/upgrade/me", "good-u1")[1])["features"]   # flag off: no key at all


def test_ui_tokens_on_adds_only_the_token_sheet_and_its_mark(env, monkeypatch):
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    monkeypatch.delenv("SCOUT_UI_TOKENS", raising=False)
    off = {p: get(env.base, p, "good-u1")[1] for p in ("/", "/lol")}
    monkeypatch.setenv("SCOUT_UI_TOKENS", "1")
    for path, name in (("/", "cs2"), ("/lol", "lol")):
        st, body, _ = get(env.base, path, "good-u1")
        assert st == 200 and TOKENS_CSS in body and f'data-upgrade="{name}" data-ui-tokens="1" '.encode() in body
        undo = (body.replace(b",'/upgrade/tokens.css']", b"]", 1).replace(b' data-ui-tokens="1"', b"", 1)
                .replace(b"html.scout-boot{background:#080808}", b"html.scout-boot{background:#101215}", 1))
        assert undo == off[path], path                  # the three flag-on changes and nothing else
    st, body, hdr = get(env.base, "/upgrade/tokens.css", "good-u1")
    assert st == 200 and hdr["Content-Type"].startswith("text/css") and body == (ROOT / "web" / "upgrade-tokens.css").read_bytes()
    assert get(env.base, "/upgrade/tokens.css")[0] == 404                       # signed out: the unknown-route answer
    assert get(env.base, "/upgrade/hub.css", "good-u1")[1] == (ROOT / "web" / "upgrade-hub.css").read_bytes()
    assert json.loads(get(env.base, "/api/upgrade/me", "good-u1")[1])["features"]["ui_tokens"] is True
    monkeypatch.setenv("SCOUT_UPGRADE", "off")                                   # the upgrade gate still decides first
    assert get(env.base, "/upgrade/tokens.css", "good-u1")[0] == 404


def test_ui_token_sheet_is_the_classic_palette_scoped_with_three_breakpoints():
    import re
    css = (ROOT / "web" / "upgrade-tokens.css").read_text(encoding="utf-8")
    classic = (ROOT / "web" / "scout-ui.css").read_text(encoding="utf-8")
    for token in ("#f5c842", "#080808", "#101010", "#171717", "#292929", "#454545", "#f2f1eb", "#b4b3aa", "#96968e"):
        assert token in css and token in classic, token        # the existing 1Time tokens, no new palette
    for font in ("'Bebas Neue'", "'Outfit'", "'DM Mono'"):
        assert font in css
    assert "Inter" not in css and "#77adff" not in css
    assert sorted(set(re.findall(r"@media\(([^)]*)\)", css))) == ["max-width:1024px", "max-width:640px", "min-width:1101px"]   # desktop starts at 1101px with the hub (2026-11-05)
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    def top_level(block):                                       # split on commas outside :is(...)
        out, depth, cur = [], 0, ""
        for ch in block:
            depth += ch == "(" or -(ch == ")")
            if ch == "," and depth == 0:
                out.append(cur); cur = ""
            else:
                cur += ch
        return out + [cur]
    selectors = [s.strip() for block in re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"@media\([^)]*\)\{", "", body)) for s in top_level(block)]
    assert selectors and all(s.startswith("html[data-ui-tokens]") for s in selectors if s), [s for s in selectors if not s.startswith("html[data-ui-tokens]")]
    assert "min-height:40px" in css and ".h-skip" in css and ".h-account" in css



def test_member_pages_carry_no_does_not_place_line():
    """Owner rule (review 2026-11-04): no 'does not place a pick' / 'not a bet' style line on any member page."""
    import re
    for name in ("upgrade.js", "upgrade-hub.js", "record.html", "record.js"):
        text = (ROOT / "web" / name).read_text(encoding="utf-8").lower()
        assert not re.search(r"does not place|not a bet placement|not financial advice|not betting advice", text), name


def test_members_never_reach_the_sports_results_page(env, monkeypatch):
    """Owner 2026-11-09: no member-facing Results page. /sports/results (and its file name) send a member to the slate; the
    admin token still opens it for diagnostics."""
    import http.client
    from urllib.parse import urlparse
    monkeypatch.setenv("SPORTS_UI", "1")
    monkeypatch.setenv("SCOUT_UPGRADE", "1")
    u = urlparse(env.base)
    for path in ("/sports/results", "/sports/results.html"):
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=20)
        try:
            c.request("GET", path, headers={"Cookie": f"{whop_auth.COOKIE}=good-u1"})
            r = c.getresponse()
            assert r.status == 302 and r.getheader("Location") == "/sports#/slate", path
            assert b"results_public.js" not in r.read()
        finally:
            c.close()
    st, body, _ = get(env.base, "/sports/results", "good-u1", {"X-Admin-Token": "owner-token"})
    assert st == 200 and b"results_public.js" in body
