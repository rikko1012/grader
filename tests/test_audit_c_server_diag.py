"""2026-10-21 audit, group C (server.py, diagnostics.py, web/diagnostics.html): one regression test per confirmed finding
(audit_no in each test name). Every upstream is a stub or a temporary database - no network, never production."""
import json
import os
import sys
import time
import types
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import cs2_scout
import diagnostics
import grader
import picklog
import prizepicks
import server
import underdog
import whop_auth
from tests.test_audit13_grader import _base
from tests.test_audit13_site import H, get, post, site  # noqa: F401  (site = the shared fixture)

ROOT = Path(__file__).resolve().parent.parent


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ------------------------------------------------------------------ 0: diagnostics from bounded pages
@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    return tmp_path / "picks.db"


def _seed(n_picks=6):
    """Settled board / member picks, each with several forecasts (some with saved grader inputs, some too late)."""
    gi = _base()
    con = picklog._db()
    start = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
    for i in range(1, n_picks + 1):
        ver = "V1" if i % 2 else "V2"
        res = {"source": "hltv", "rounds": [24, 22, 26], "maps_used": [1, 2], "per_map": [[17, 8], [14, 6], [20, 9]]}
        con.execute("INSERT INTO picks (id, key, logged_at, player, prop, line, pick, p_pick, grade, units, lambda, status, "
                    "actual, hit, match_slug, match_start, scope, test, model_version, result, factors, book, on_board) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i, f"k{i}", iso(start - timedelta(hours=5)), f"p{i}", "kills", 29.0, "More", 0.6, "B", 0.5, 30.1,
                     "settled", 31.0 if i % 3 else 27.0, 1 if i % 3 else 0, f"m{i // 2}", iso(start), "12", 2 if i < 5 else 0,
                     ver, json.dumps(res), json.dumps({"big": "x" * 5000}), "underdog" if i < 5 else None, 1 if i < 5 else 0))
        for j, lead in enumerate((240, 90, 31, 30, 10)):        # 30+ min before the start: the 30-min one is the snapshot
            con.execute("INSERT INTO forecasts (pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, "
                        "lambda, model_version, shadow, inputs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (i, "update", iso(start - timedelta(minutes=lead)), iso(start), 29.0 + (j == 3) * (i % 2), "More",
                         0.55 + j / 100, 0.55 + j / 100, "B", 0.5, 30.0 + j / 10, ver, None,
                         zlib.compress(json.dumps(dict(gi, line=29.0 + (j == 3) * (i % 2))).encode()) if (i + j) % 4 else None))
    con.commit()
    con.close()


def test_0_diagnostics_from_pages_equals_the_full_export_and_never_loads_it(db, monkeypatch):
    _seed()
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    full = diagnostics.build(picklog.export(), picklog.export_forecasts(inputs=True), [], now=now)
    assert full["cohorts"]["eval"]["summary"] and any("in80" in json.dumps(s) for s in full["cohorts"]["eval"]["summary"])
    pages = []
    real_page = picklog.forecasts_page

    def page(*a, **k):
        out = real_page(*a, **k)
        pages.append((k.get("inputs"), len(out["rows"])))
        return out
    monkeypatch.setattr(picklog, "forecasts_page", page)
    monkeypatch.setattr(picklog, "PAGE_MAX", {False: 4, True: 2})          # tiny pages: the paging itself is exercised
    for name in ("export", "export_forecasts"):                             # the unbounded exports are never used
        monkeypatch.setattr(picklog, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("unbounded export")))
    paged = diagnostics.build_from_log(picklog, [], now=now)
    assert paged == full
    assert pages and all(n <= (2 if inp else 4) for inp, n in pages)
    assert "factors" not in diagnostics.picks_light(picklog)[0]


def test_0_diagnostics_endpoint_uses_the_paged_build_and_forecasts_json_is_capped(site, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok123")
    monkeypatch.setattr(server, "_DIAG", {})
    calls = []
    monkeypatch.setattr(diagnostics, "build_from_log", lambda pl, batches=(), now=None: calls.append(1) or {"cohorts": {}, "n": len(calls)})
    monkeypatch.setattr(picklog, "export_forecasts", lambda inputs=False, last=None: [{"last": last, "inputs": inputs}])
    monkeypatch.setattr(server.slip_batches, "read", lambda: [])
    h = get("/admin/diagnostics.json", headers={"X-Admin-Token": "tok123"})
    assert h.code == 200 and h.body()["n"] == 1
    assert get("/admin/diagnostics.json", headers={"X-Admin-Token": "tok123"}).body()["n"] == 1     # cached
    assert get("/admin/diagnostics.json?fresh=1", headers={"X-Admin-Token": "tok123"}).body()["n"] == 2
    b = get("/admin/forecasts.json?inputs=1", headers={"X-Admin-Token": "tok123"}).body()
    assert b["forecasts"][0]["last"] == picklog.PAGE_MAX[True] and b["capped_at"] == picklog.PAGE_MAX[True]
    b = get("/admin/forecasts.json?last=99999", headers={"X-Admin-Token": "tok123"}).body()
    assert b["forecasts"][0]["last"] == picklog.PAGE_MAX[False]
    assert get("/admin/forecasts.json?last=7", headers={"X-Admin-Token": "tok123"}).body()["forecasts"][0]["last"] == 7


# ------------------------------------------------------------------ 63: projection-source table per exact version
def test_63_projection_source_rows_never_pool_versions(db):
    _seed()
    d = diagnostics.build_from_log(picklog, [], now=datetime(2026, 10, 21, tzinfo=timezone.utc))
    src = d["cohorts"]["eval"]["by_projection_source"]
    assert src and all(len(x["versions"]) == 1 and x["versions"] == {x["version"]: x["n"]} for x in src)
    assert {x["version"] for x in src} == {"V1", "V2"}
    page = (ROOT / "web" / "diagnostics.html").read_text(encoding="utf-8")
    assert 'x.version === $("#fver").value' in page and "Underdog board lines only" in page


# ------------------------------------------------------------------ 64: an error body never hangs on Loading
def test_64_diagnostics_page_reports_an_error_body():
    page = (ROOT / "web" / "diagnostics.html").read_text(encoding="utf-8")
    load = page[page.index("async function load()"):page.index("function draw()")]
    assert "!r.ok" in load and "D.error" in load and "!D.cohorts" in load
    assert load.index("D.error") < load.index("D.cohorts[c]")                  # checked before the cohorts are read


# ------------------------------------------------------------------ 20: /api/match_compare is validated and rate limited
def test_20_match_compare_rejects_member_chosen_keys_and_is_rate_limited(site, monkeypatch):
    calls = []
    monkeypatch.setattr(server, "match_compare", lambda *a, **k: calls.append(a) or {"teams": []})
    monkeypatch.setattr(server, "_MC_HITS", {})
    st = iso(datetime.now(timezone.utc) + timedelta(hours=5)).replace("+", "%2B")     # as the page's URLSearchParams sends it
    q = f"/api/match_compare?team_slug=vit&opp_slug=mouz&team=Vitality&opponent=MOUZ&start={st}&maps="
    assert get(q + "12").code == 200 and len(calls) == 1
    for bad in (q + "99", q.replace("Vitality", "V" * 200) + "12", q.replace(st, iso(datetime.now(timezone.utc) + timedelta(days=400)).replace("+", "%2B")) + "12"):
        assert get(bad).code == 400
    assert len(calls) == 1                                                  # refused before any work
    codes = [get(q.replace("Vitality", f"Team{i}") + "12").code for i in range(server.MC_PER_MIN + 3)]
    assert codes.count(429) >= 3 and len(calls) <= server.MC_PER_MIN
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok123")                    # the owner's load test is exempt
    assert get(q.replace("Vitality", "TeamLT") + "12", headers={"X-Load-Test": "tok123"}).code == 200


# ------------------------------------------------------------------ 52 / 57: outage card keeps the board's cap reason + marker
def test_52_57_outage_card_shows_the_board_rows_cap_reason_and_flag(monkeypatch):
    st = "2026-10-14T18:00:00+00:00"
    row = {"book": "underdog", "nick": "ZywOo", "prop": "kills", "scope": "12", "line": 30.5, "graded_line": 30.5, "start": st,
           "match_slug": "vitality-vs-mouz", "line_type": "standard", "side": "More", "p": 0.64, "grade": "B+", "units": 1.0,
           "state": "PLAY", "graded_at": st, "dstat": ["ok"], "cap_reason": "stand-in", "flagged": True}   # a board_payload row
    monkeypatch.setattr(server, "_bs_rows", lambda: [row])
    res = {"player_block": {"player": {"nick": "ZywOo"}}, "next_match": {"start": st, "slug": "vitality-vs-mouz"},
           "grade": {"projection_source": "legacy (performance model unavailable: HLTV database unreachable right now)"}}
    v = server.outage_verdict(res, "kills", "12", 30.5, {"side": "More"})
    assert v["kept_from_board"] and v["units"] == 1.0 and v["cap_reason"] == "stand-in" and v["flagged"] is True
    monkeypatch.setattr(server, "_bs_rows", lambda: [dict(row, units=0.0, flagged=False)])
    v = server.outage_verdict(res, "kills", "12", 30.5, {"side": "More"})
    assert v["cap_reason"] is None and v["flagged"] is False


# ------------------------------------------------------------------ 56: outage card withholds form_gap's temporary projection
def test_56_withheld_card_drops_the_temporary_projection_from_form_gap():
    res = {"grade": {"line": 27.5, "prop": "kills", "scope": "12", "lambda_final": 31.2, "flags": ["TAPE: x"]},
           "form_gap": {"last10_avg": 27.3, "last10_n": 10, "last10_env": "online", "projection": 31.2, "gap": 3.9,
                        "months12_avg": 26.0, "stat": "kills"}}
    out = server.public(server.withhold_temporary(res))
    assert "projection" not in out["form_gap"] and "gap" not in out["form_gap"] and out["form_gap"]["last10_avg"] == 27.3
    assert "31.2" not in json.dumps(out)


# ------------------------------------------------------------------ 58: the spread never leaves in grade.dist / online_adj
def test_58_member_grade_has_no_dist_or_online_adj():
    g = grader.grade_leg(_base())
    assert g.get("dist") and "sd" in json.dumps(g["dist"])
    res = {"grade": g}
    server.protect_recipe(res)
    assert "dist" not in res["grade"] and "online_adj" not in res["grade"] and "sd" not in res["grade"]
    assert g.get("dist")                                                    # the full grade object itself is untouched


# ------------------------------------------------------------------ 53: a client that left is no server failure
class Gone:
    def write(self, b):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")


def test_53_client_disconnect_is_not_a_5xx_and_sends_nothing_more(site, capsys):
    before = dict(server.STATS)
    h = H("/no-such-page")
    h.wfile = Gone()
    h.do_GET()                                                              # no exception escapes
    assert h.close_connection is True and server.STATS["errors_5xx"] == before["errors_5xx"]
    assert server.STATS.get("client_gone", 0) == before.get("client_gone", 0) + 1
    assert "BrokenPipeError" not in capsys.readouterr().err                 # nothing logged as an error
    h = H("/api/slip_log", body=b"{}")
    h.wfile = Gone()
    h.do_POST()
    assert h.close_connection is True and server.STATS["errors_5xx"] == before["errors_5xx"]


def test_53_failure_after_the_response_started_sends_no_second_response(site, monkeypatch):
    sent = []

    class Half(H):
        def search(self, q):
            self.send(200, {"ok": 1})
            raise RuntimeError("after the body")
    real = server.Handler.send
    monkeypatch.setattr(server.Handler, "send", lambda self, code, *a, **k: sent.append(code) or real(self, code, *a, **k))
    h = Half("/api/search?q=zywoo")
    h.do_GET()
    assert sent == [200] and h.close_connection is True


# ------------------------------------------------------------------ 54: non-ASCII tokens are refused, never a 500
def test_54_non_ascii_tokens_are_refused_not_500(site, monkeypatch):
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok123")
    assert get("/admin?token=%C3%A9").code == 403
    assert get("/admin/timings", headers={"X-Admin-Token": "\xe9"}).code == 404
    assert server.token_eq("tok123", "tok123") and not server.token_eq("\xe9", "tok123") and not server.token_eq(None, "tok123")
    monkeypatch.setattr(whop_auth, "enabled", lambda: True)
    monkeypatch.setattr(whop_auth, "session", lambda v: None)
    assert get("/api/board", headers={"X-Load-Test": "\xe9"}).code == 401
    assert post("/api/slip_log", headers={"X-Load-Test": "\xe9"}, body=b"{}").code == 401
    monkeypatch.setenv("CONTENT_BOOK_KEY", "ck")
    assert get("/content-book?key=%C3%A9").code == 404


def test_54_an_exception_in_do_post_gets_a_response(site, monkeypatch):
    monkeypatch.setattr(server.Handler, "allowed", lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    h = post("/api/slip_log", body=b"{}")
    assert h.code == 500 and "ref" in h.body()["error"] and h.close_connection is True


# ------------------------------------------------------------------ 21 / 55: the board pass grades what the feed shows now
def _board_harness(site, ud_rows, pp_rows, on_grade):
    feeds = {"ud": ud_rows, "pp": pp_rows}
    site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [dict(r) for r in feeds["ud"]], "error": None})
    site.setattr(prizepicks, "board", lambda wait=False, now=None: {"state": "ok", "at": time.time(), "ingested_at": time.time(),
                                                                    "rows": [dict(r) for r in feeds["pp"]], "error": None})
    for name in ("_board_load", "_board_save"):
        site.setattr(server, name, lambda: None)
    for name in ("maybe_batch", "settle_all"):
        site.setattr(server.slip_batches, name, lambda *a, **k: None)
    site.setattr(server.slips, "log_recommended", lambda *a, **k: None)
    site.setattr(server.roles, "refresh_hltv", lambda *a, **k: None)
    site.setattr(server.roles, "refresh", lambda *a, **k: None)
    site.setattr(server, "BOARD_WORKERS", 1)
    site.setattr(server, "BOARD_RESCAN_S", 0)
    graded = []

    def grade(player, line, prop, lt, **k):
        graded.append((player, line, k.get("less_available")))
        on_grade(player, feeds)
        return {"error": "stub"}
    site.setattr(cs2_scout, "grade", grade)

    class Stop(BaseException):
        pass
    n = [0]

    def fake_sleep(s):
        n[0] += 1
        if n[0] > 1:
            raise Stop()
    site.setattr(server, "time", types.SimpleNamespace(time=time.time, sleep=fake_sleep))
    with pytest.raises(Stop):
        server.board_forever()
    return graded


def test_21_lines_that_move_or_appear_mid_pass_are_graded_in_the_same_pass(site):
    fut = iso(datetime.now(timezone.utc) + timedelta(hours=5))
    row = lambda i, line: {"id": f"u{i}", "scope": "12", "player": f"p{i}", "team": "A", "opponent": "B", "matchup": "A vs B",
                           "start": fut, "prop": "kills", "line": line, "alt": False}
    ud = [row(i, 30.5) for i in range(6)]

    def on_grade(player, feeds):
        if player == "p0":                                                 # mid-pass: one line moves, one is posted
            feeds["ud"][4] = row(4, 31.5)
            feeds["ud"].append(row(9, 28.5))
            del feeds["ud"][5]                                             # and one leaves the feed
    graded = _board_harness(site, ud, [], on_grade)
    players = [(p, l) for p, l, _ in graded]
    assert ("p4", 31.5) in players and ("p4", 30.5) not in players          # graded at the line the feed shows now
    assert ("p9", 28.5) in players                                          # the new line in this pass, not the next one
    assert not any(p == "p5" for p, _ in players)                           # a line that left the feed is not graded


def test_55_an_under_opened_since_the_grade_is_regraded_and_hidden_meanwhile(site):
    fut = iso(datetime.now(timezone.utc) + timedelta(hours=5))
    pp = {"id": "pp:1", "book": "prizepicks", "scope": "12", "player": "Crisp", "team": "A", "opponent": "B", "matchup": "A vs B",
          "start": fut, "prop": "kills", "line": 29.5, "alt": False, "odds_type": "standard", "under_available": True,
          "over_available": True}
    kept = {"at": time.time(), "side": "Less", "grade": "LEAN", "p": 0.688, "units": 0.0, "graded_line": 29.5,
            "graded_model": picklog.MODEL_VERSION, "nick": "Crisp", "graded_sides": {"under": False, "over": True},
            "available": False, "model_grade": "A-"}
    server.BOARD["pp:1"] = dict(kept)
    site.setattr(prizepicks, "board", lambda wait=False, now=None: {"state": "ok", "at": time.time(), "rows": [dict(pp)], "error": None})
    assert server.side_opened(kept, pp) and not server.side_opened(kept, dict(pp, under_available=False))
    assert not server.side_opened({"graded_sides": {"under": None, "over": None}}, {})               # Underdog rows: no sides
    assert server.board_payload("prizepicks")["rows"] == []                 # never shown with the Over-only grade
    g = {"perf_transient": True, "projection_source": "legacy (performance model unavailable: HLTV database unreachable)"}
    assert server.keep_last_hltv_grade(kept, g, pp) is None                 # and never kept through an outage
    graded = _board_harness(site, [], [pp], lambda *a: None)
    assert graded == [("Crisp", 29.5, True)]                                # regraded at once (15 min not up), Under offered
