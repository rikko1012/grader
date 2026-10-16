"""Owner 2026-10-16: publication drafts come only from a verified saved forecast and the line offered now (fixed context,
never a picked window); lc-1 is saved with the pre-match record and scored after settlement; the recommendation rule
(favoured side >= 57%) is reported on the subset members would play. All names, ids and numbers are TEST FIXTURES."""
import json
from datetime import datetime, timedelta, timezone

import pytest

import learning as L
import perf_shadow as S
import picklog
import posts

START = (datetime.now(timezone.utc) + timedelta(hours=8)).replace(microsecond=0)


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    con = picklog._db()
    con.close()
    return tmp_path / "picks.db"


def add(db, pid, nick, prop, line, book, side, p, grade, units, rec="7/10", scope="12", logged=None, lam=31.2):
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO picks (id, key, logged_at, player, slug, player_id, team, opponent, match_slug, match_start, prop, line, "
                "line_type, pick, p_pick, grade, units, scope, book, status, test, on_board) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"k{pid}", "2026-10-16T01:00:00+00:00", nick, nick.lower(), 1000 + pid, "Alpha", "Bravo", "m1",
                 START.isoformat(), prop, line, "standard", side, p, grade, units, scope, book, "open", 2, 1))
    con.execute("INSERT INTO forecasts (pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, "
                "model_version, rec_hit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, "first", logged or "2026-10-16T01:38:00+00:00", START.isoformat(), line, side, p,
                 p if side == "More" else 1 - p, grade, units, lam, "TESTVER", rec))
    con.commit()
    con.close()


def row(pid, nick, prop, line, book, side, grade, units, **kw):
    return dict({"book": book, "player_id": 1000 + pid, "match_slug": "m1", "prop": prop, "line": line, "scope": "12",
                 "side": side, "grade": grade, "units": units, "nick": nick, "start": START.isoformat(),
                 "team_name": "Alpha", "opp_name": "Bravo"}, **kw)


def test_a_draft_states_the_saved_forecast_and_a_fixed_context(db):
    add(db, 1, "tester", "kills", 27.5, "prizepicks", "More", 0.608, "B", 0.5, rec="6/10")
    out = posts.drafts([row(1, "tester", "kills", 27.5, "prizepicks", "More", "B", 0.5)], "prizepicks", db)
    d = out["drafts"][0]
    assert d["verified"] and d["forecast_id"] and d["model_version"] == "TESTVER" and d["chance"] == 0.608 and d["grade"] == "B"
    t = d["text"]
    assert t.startswith("tester OVER 27.5 kills · Grade B · 60.8% model chance") and "Grader's last-10 record at this line (Maps 1-2): 6 over, 4 under." in t
    assert "Alpha vs Bravo, " in t and " CT (PrizePicks)." in t and "Research tool, not betting advice. 21+" in t
    assert d["fits_x"] and d["chars"] == posts.x_len(t) > len(t)            # X counts the link as 23 and an emoji as 2
    assert posts.x_len("whop.com/1timeesports 🧞") == 23 + 1 + 2


def test_nothing_is_drafted_without_a_matching_saved_forecast(db):
    add(db, 2, "moved", "kills", 30.5, "prizepicks", "Less", 0.6, "B", 0.5)
    add(db, 3, "regraded", "hs", 14.5, "prizepicks", "Less", 0.58, "B", 0.5)
    rows = [row(2, "moved", "kills", 31.5, "prizepicks", "Less", "B", 0.5),          # the line moved: no saved forecast at 31.5
            row(3, "regraded", "hs", 14.5, "prizepicks", "Less", "B+", 1.0),         # the board's grade is not the saved one
            row(4, "nobody", "kills", 25.5, "prizepicks", "More", "A", 1.0),         # no pick / forecast at all
            row(2, "lean", "kills", 30.5, "prizepicks", "Less", "LEAN", 0.0),        # a Lean is never a post
            row(2, "gone", "kills", 30.5, "prizepicks", "Less", "B", 0.5, unavailable="Unavailable—side no longer offered")]
    out = posts.drafts(rows, "prizepicks", db)
    assert out["drafts"] == []
    why = " | ".join(s["why"] for s in out["skipped"])
    assert "no saved forecast of this exact line" in why and "latest saved forecast says B" in why
    assert "no longer offers this side" in why and len(out["skipped"]) == 4


def test_lc1_is_the_frozen_correction_and_is_saved_with_the_record():
    p, z = S.lc1_p(31.17, 26.5, "kills")
    assert abs(p - 0.4923) < 1e-3 and z > 0                                 # frozen: about the base rate whatever z says
    assert S.lc1_p(None, 26.5, "kills") is None and S.lc1_p(30, None, "kills") is None
    assert S.LC1["w"]["kills"] == tuple(L.LINE_CORRECTION["coefficients"]["kills"])
    assert S.LC1["w"]["hs"] == tuple(L.LINE_CORRECTION["coefficients"]["hs"])


def test_lc1_is_scored_from_the_pre_match_record_and_the_rule_is_reported(tmp_path):
    con = L.connect(tmp_path / "l.db")
    st = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
    freeze = st - timedelta(hours=10)
    L._ensure_line_correction(con, freeze)
    L._ensure_line_correction(con, freeze + timedelta(hours=1))
    ch = con.execute("SELECT * FROM challengers WHERE family = 'line-correction-1'").fetchone()
    assert ch["id"].startswith("lc-001-") and ch["status"] == "shadow"
    t0 = st - timedelta(hours=3)
    con.execute("INSERT INTO forecast_copy (forecast_id, pick_id, logged_at, match_start, line, side, p_more, lambda, source, book, line_type, prop, scope) "
                "VALUES (1, 7, ?, ?, 29.5, 'More', 0.62, 31.4, 'board', 'underdog', 'standard', 'kills', '12')", (L._iso(t0), L._iso(st)))
    rec = {"challenger": "fh-1", "live": {"p_more": 0.62, "lambda": 31.4}, "fh1": {"p_more": 0.6, "lambda": 31.0},
           "lc1": {"p_more": 0.4921, "z": 0.21}, "n0_check": {"exact": True}, "group": "established"}
    L._copy_fh1(con, [{"id": 1, "pick_id": 7, "forecast_id": 1, "logged_at": L._iso(t0), "match_start": L._iso(st), "line": 29.5,
                       "prop": "kills", "scope": "12", "live_p_more": 0.62, "ch_p_more": 0.6, "live_lambda": 31.4, "ch_lambda": 31.0,
                       "n0_exact": 1, "identical": 0, "grp": "established", "inputs_sha": "a", "gi_sha": "b", "rec": json.dumps(rec)}], freeze)
    assert L._fh1_shadow(con, ch, st - timedelta(minutes=15), arm="lc1") == 1
    s = con.execute("SELECT * FROM shadow WHERE challenger_id = ?", (ch["id"],)).fetchone()
    assert (s["p_over_ch"], s["p_over_base"], s["lam_ch"]) == (0.4921, 0.62, 31.4)      # same projection, corrected chance
    us = [{"forecast_id": 1, "logged": L._iso(t0), "start": L._iso(st), "match": "m1", "over": 0, "actual": 27.0, "line": 29.5,
           "prop": "kills", "scope": "12", "p": 0.62}]
    ex = L._fh1_extras(con, ch, us, arm="lc1")["lc1"]
    assert ex["units"] == 1 and ex["arm"] == "lc1"
    r = ex["all"]["recommended"]
    assert r["live"]["picks"] == 1 and r["live"]["hit"] == 0.0 and r["challenger"]["picks"] == 0     # lc-1 recommends nothing here
    rs = L.recommend_stats([{"p": 0.61, "over": 1, "match": "a"}, {"p": 0.40, "over": 0, "match": "b"}, {"p": 0.55, "over": 1, "match": "c"}], "p")
    assert rs["picks"] == 2 and rs["hit"] == 1.0 and rs["by_side"]["Under"]["picks"] == 1 and rs["rule"].endswith("0.57")


def test_post_drafts_endpoint_is_admin_only_and_returns_verified_drafts(db, monkeypatch):
    import http.client
    import threading as _th
    from http.server import ThreadingHTTPServer
    import server
    add(db, 5, "endpoint", "kills", 28.5, "prizepicks", "Less", 0.6, "B", 0.5)
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok-test")
    monkeypatch.setattr(server, "board_payload", lambda bk: {"rows": [row(5, "endpoint", "kills", 28.5, "prizepicks", "Less", "B", 0.5)]})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    _th.Thread(target=httpd.serve_forever, daemon=True).start()

    def get(path, token=None):
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=30)
        c.request("GET", path, headers={"X-Admin-Token": token} if token else {})
        r = c.getresponse(); body = r.read(); c.close()
        return r.status, body
    try:
        st, body = get("/admin/post_drafts.json?book=prizepicks")
        assert b"endpoint UNDER" not in body
        st, body = get("/admin/post_drafts.json?book=prizepicks", "tok-test")
        d = json.loads(body)
        assert st == 200 and [x["text"].split(" · ")[0] for x in d["drafts"]] == ["endpoint UNDER 28.5 kills"]
        assert d["drafts"][0]["verified"] and "grader_record_at_real_lines" in d
    finally:
        httpd.shutdown(); httpd.server_close()


def test_a_map1_line_never_shows_the_maps12_record():
    fc = {"pick": "Less", "prop": "kills", "scope": "1", "nick": "solo", "line": 14.5, "grade": "B", "p_pick": 0.58,
          "rec_hit": "9/10", "match_start": START.isoformat(), "book": "underdog", "team": "Alpha", "opponent": "Bravo"}
    t = posts.text(fc, {})
    assert t.startswith("solo UNDER 14.5 kills (Map 1) · Grade B · 58.0% model chance") and "record" not in t and "(Underdog)." in t
