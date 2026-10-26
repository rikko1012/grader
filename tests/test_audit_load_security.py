"""2026-10-26 load / security / robustness audit (server.py, picklog.py, prizepicks.py, slips.py, slip_batches.py,
lol/history.py): one regression test per fix. No network, no database server. Grades, chances and stakes are never
touched by these fixes - the tests check how responses travel and how much admin views hold, not what they say."""
import gzip
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import cs2_scout
import picklog
import prizepicks
import server
import slip_batches
import slips
import underdog
import whop_auth
from tests.test_audit13_site import H, _grade_stub, _pp_cut_row, get, site  # noqa: F401  (site is a fixture)

TOKEN = "audit-admin-token-0001"


@pytest.fixture
def card(site):
    calls = []
    site.setattr(cs2_scout, "grade", lambda *a, **k: calls.append(1) or json.loads(json.dumps(_grade_stub())))
    site.setattr(underdog, "line_for", lambda *a, **k: None)
    site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [], "error": None})
    site.setattr(prizepicks, "board", lambda now=None, wait=False: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    site.setattr(picklog, "log_async", lambda *a, **k: None)
    site.setattr(server, "mark_warm", lambda *a: None)
    site.setattr(server, "_both_boards", lambda: [_pp_cut_row()])
    site.setattr(server, "GZIP_MIN", 10)                 # the stub card is small; real cards are tens of KB
    return calls


def hdr(h, name):
    return dict(h.sent).get(name)


# ------------------------------------------------------------------ 1. cards / results / match compare: gzip + ETag/304
def test_card_is_gzipped_once_and_revalidates_with_304(card):
    q = "/api/grade?player=s0und&line=36.5&prop=kills&maps=12"
    plain = get(q)
    assert plain.code == 200 and hdr(plain, "Content-Encoding") is None
    body = plain.body()
    etag = hdr(plain, "ETag")
    assert etag and hdr(plain, "Cache-Control") == server.MEMBER_CACHE
    z = get(q, headers={"Accept-Encoding": "gzip, br"})
    assert hdr(z, "Content-Encoding") == "gzip" and json.loads(gzip.decompress(z.wfile.getvalue())) == body   # same bytes
    nm = get(q, headers={"If-None-Match": etag})
    assert nm.code == 304 and nm.wfile.getvalue() == b""
    assert len(card) == 1                                # one grade for the three requests (shared), unchanged


def test_card_errors_still_go_uncached(card, site):
    site.setattr(cs2_scout, "grade", lambda *a, **k: {"error": "no player check the spelling"})
    h = get("/api/grade?player=zzzz&line=20.5&prop=kills&maps=12")
    assert h.code == 404 and hdr(h, "ETag") is None and hdr(h, "Cache-Control") == "no-store"


def test_results_and_match_compare_are_gzipped(site):
    site.setattr(server, "GZIP_MIN", 10)
    site.setattr(server, "results_payload", lambda: {"rows": [{"x": "y" * 50}] * 20})
    site.setattr(server, "match_compare", lambda *a, **k: {"match": {"team": a[2]}, "players": [{"n": "z" * 50}] * 20})
    r = get("/api/results", headers={"Accept-Encoding": "gzip"})
    assert r.code == 200 and hdr(r, "Content-Encoding") == "gzip" and hdr(r, "ETag")
    m = get("/api/match_compare?team_slug=&opp_slug=&team=A&opponent=B&start=2026-10-26T17:00:00Z&maps=12",
            headers={"Accept-Encoding": "gzip"})
    assert m.code == 200 and hdr(m, "Content-Encoding") == "gzip"
    assert json.loads(gzip.decompress(m.wfile.getvalue()))["match"]["team"] == "A"


# ------------------------------------------------------------------ 2. response headers
def test_every_response_has_nosniff_and_same_origin_referrer(site):
    for path in ("/health", "/api/nothing", "/auth/me"):
        h = get(path)
        assert hdr(h, "X-Content-Type-Options") == "nosniff" and hdr(h, "Referrer-Policy") == "same-origin"
        assert hdr(h, "X-Frame-Options") is None          # Whop frames the member pages: never blocked


# ------------------------------------------------------------------ 3. fail closed on Render without Whop settings
def test_member_data_stays_locked_on_render_when_whop_is_off(site):
    assert H("/api/board").allowed() is True              # local, no Whop: open (dev)
    site.setenv("RENDER", "true")
    assert H("/api/board").allowed() is False
    assert get("/api/board").code == 401 and get("/api/grade?player=a&line=20.5").code == 401


# ------------------------------------------------------------------ 4. admin exports are bounded
@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    return tmp_path / "picks.db"


BASE = {"logged_at": "2026-10-13T10:00:00+00:00", "player": "ZywOo", "slug": "zywoo", "player_id": 1, "team": "Vitality",
        "opponent": "FURIA", "match_slug": "vitality-vs-furia", "match_start": "2026-10-13T15:00:00.000+00:00", "prop": "hs",
        "line": 12.5, "line_type": "standard", "lambda": 13.4, "p_more": 0.61, "p_less": 0.39, "pick": "More", "p_pick": 0.61,
        "grade": "B", "units": 1.0, "factors": "{}", "scope": "12", "model_version": "x", "test": 0}


def test_admin_picks_json_is_a_bounded_keyset_page(db, site):
    site.setattr(server, "ADMIN_TOKEN", TOKEN)
    for i in range(7):
        picklog.write_row(dict(BASE, key=f"k{i}", line=10.5 + i))
    full = picklog.export()
    seen, after, cutoff = [], 0, None
    while True:
        q = f"/admin/picks.json?limit=3&after={after}" + (f"&cutoff={cutoff}" if cutoff else "")
        page = get(q, headers={"X-Admin-Token": TOKEN}).body()
        assert len(page["picks"]) <= 3 and page["total_in_cutoff"] == 7
        cutoff = page["cutoff_id"]
        seen += page["picks"]
        picklog.write_row(dict(BASE, key=f"late{after}", line=40.5 + after))     # rows added while paging: never mixed in
        if page["done"]:
            break
        after = page["next_after"]
    assert seen == full                                  # every row exactly once, unchanged
    assert picklog.picks_page(limit=10 ** 9)["limit"] == picklog.PICKS_PAGE_MAX
    assert get("/admin/picks.json").code == 404          # no token: nothing (unchanged)


def test_tail_views_are_capped_and_streamed(site, tmp_path):
    site.setattr(server, "ADMIN_TOKEN", TOKEN)
    f = tmp_path / "comparisons.jsonl"
    f.write_text("".join(json.dumps({"i": i}) + "\n" for i in range(12)))
    site.setattr(server.comparison, "snapshot_path", lambda: f)
    b = get("/admin/comparisons.json?last=5", headers={"X-Admin-Token": TOKEN}).body()
    assert b["total"] == 12 and [x["i"] for x in b["snapshots"]] == [7, 8, 9, 10, 11]
    assert server.admin_last("99999999", 500) == server.ADMIN_TAIL_MAX
    assert server.admin_last("abc", 500) == 500 and server.admin_last("-3", 500) == 500 and server.admin_last("0", 7) == 7
    assert server.tail_lines(tmp_path / "missing.jsonl", 3) == (0, [])
    h = tmp_path / "pp_history.jsonl"
    h.write_text("".join(json.dumps({"market": "m1" if i % 2 else "m2", "i": i}) + "\n" for i in range(10)) + "\n")
    site.setattr(prizepicks, "HISTORY", h)
    assert [e["i"] for e in prizepicks.history(None, 3)] == [7, 8, 9]
    assert [e["i"] for e in prizepicks.history("m1", 2)] == [7, 9]
    assert prizepicks.history(None, 500) == [json.loads(x) for x in h.read_text().splitlines() if x.strip()]


def test_pp_history_typo_limit_is_not_a_500(site):
    site.setattr(server, "ADMIN_TOKEN", TOKEN)
    site.setattr(prizepicks, "history", lambda market=None, limit=500: [{"event": "open", "limit": limit}])
    site.setattr(prizepicks, "ingests", lambda: [])
    b = get("/admin/pp_history?limit=5x", headers={"X-Admin-Token": TOKEN})
    assert b.code == 200 and b.body()["events"][0]["limit"] == 500


def test_slip_logs_read_only_their_tail(tmp_path, monkeypatch):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps({"i": i}) + "\n" for i in range(9)) + "\n")
    assert [x["i"] for x in slips.read(p, last=4)] == [6, 7, 8]          # the trailing blank line counts, as before
    assert [x["i"] for x in slips.read(p)] == list(range(9))
    assert slips.read(tmp_path / "none.jsonl") == []
    monkeypatch.setattr(slip_batches, "LOG", p)
    assert [x["i"] for x in slip_batches.read(last=3)] == [7, 8]
    assert len(slip_batches.read()) == 9


# ------------------------------------------------------------------ 5. outbound calls have timeouts
def test_lol_history_postgres_connect_has_a_timeout(monkeypatch):
    import types
    seen = {}

    class Stop(Exception):
        pass

    def fake_connect(dsn, **kw):
        seen.update(kw)
        raise Stop()
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=fake_connect))
    from lol import history
    with pytest.raises(Stop):
        history.DB(dsn="postgresql://example.invalid/db")
    assert seen.get("connect_timeout") and seen["connect_timeout"] <= 30


# ------------------------------------------------------------------ 6. search: the BO3.gg fallback is rate limited per member
def test_search_bo3_fallback_is_rate_limited_but_cached_queries_are_free(site):
    import hltv
    calls = []

    class FakeBO3:
        def search_players(self, q, n):
            calls.append(q)
            return {"results": [{"slug": q, "nickname": q, "first_name": "A", "last_name": "B", "country": {"code": "FR"}}]}

        def close(self):
            pass
    site.setattr(hltv, "search_players", lambda q, n=8: None)          # the HLTV index is down
    site.setattr(server, "BO3", FakeBO3)
    site.setattr(server, "_SEARCH", {})
    site.setattr(server, "_SEARCH_BO3_HITS", {})
    site.setattr(server, "SEARCH_BO3_PER_MIN", 2)
    assert [get(f"/api/search?q={q}").code for q in ("zywoo", "donk", "m0nesy")] == [200, 200, 429]
    site.setattr(server, "_SEARCH", {})                                # the shared cache alone still answers, no limit
    assert get("/api/search?q=zywoo").code == 200 and calls == ["zywoo", "donk"]
