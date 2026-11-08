"""2026-10-18 research-flow release (CLAUDE_NEXT_RELEASE sections 4-6): member Results from the stored scorecard, the
card's distribution view, operational checks, Slips history, and the page's destinations / compact rows / moneyline rows /
card tabs / Slate preview. Every row, id, team, time and number below is a LABELLED TEST FIXTURE."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import dist_view
import grader
import perf
import results_view
import server
from tests.test_audit13_site_ui import needs_node, run_page


# ------------------------------------------------------------------ member Results (stored scorecard, no returns)
def _unit(match, p, over, prop="kills", scope="12", version="v1", day="2026-10-17"):
    return {"match": match, "p": p, "over": over, "prop": prop, "scope": scope, "version": version, "start": day + "T12:00:00+00:00",
            "logged": day + "T10:00:00+00:00", "units": 0.5}


def test_results_count_the_registered_pick_rule_with_pushes_apart():
    rows = [_unit("m1", 0.62, 1), _unit("m1", 0.40, 1), _unit("m2", 0.30, 0, prop="hs"), _unit("m3", 0.55, 1),   # 0.55 / 0.45: no pick
            _unit("m4", 0.70, 0, version="v2", day="2026-10-18"), _unit("m5", 0.58, 1, scope="1")]
    out = results_view.member_results({"rows": rows, "at": "2026-10-18T06:00:00+00:00", "excluded": {"push on the board line": 3}}, B=200,
                                       map1_min=0)       # Map 1 shown here (its 500-stake gate: test_map1_results_gate.py)
    P = out["picks"]
    # picks: 0.62 Over won, 0.40 -> Under 0.60 lost, 0.30 -> Under 0.70 won (hs), 0.70 Over lost, 0.58 Over won (Map 1)
    assert (P["picks"], P["wins"], P["losses"], P["matches"]) == (5, 3, 2, 4) and P["observed"] == 0.6   # two picks in m1
    assert round(P["printed"], 4) == round((0.62 + 0.60 + 0.70 + 0.70 + 0.58) / 5, 4)
    assert out["lines"] == {"settled": 6, "matches": 5, "first": "2026-10-17", "last": "2026-10-18", "pushes": 3}
    assert {(r["stat"], r["scope"], r["picks"]) for r in out["by_stat"]} == {("Kills", "Maps 1–2", 3), ("Headshots", "Maps 1–2", 1), ("Kills", "Map 1", 1)}
    assert [r["version"] for r in out["by_version"]] == ["v2", "v1"]                      # newest first
    assert P["observed_ci"] is None                         # fewer than 5 matches: no interval is printed
    many = results_view.member_results({"rows": [_unit(f"m{i}", 0.62, i % 2) for i in range(10)], "excluded": {}}, B=200)["picks"]
    assert many["observed_ci"][0] <= many["observed"] <= many["observed_ci"][1]
    text = json.dumps(out).lower()
    for w in ("units", "profit", "roi", "return\""):
        assert w not in text


def test_results_payload_reads_the_stored_export(monkeypatch, tmp_path):
    import sqlite3
    import learning
    path = tmp_path / "learning.db"
    monkeypatch.setattr(learning, "DB_PATH", path)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT INTO meta VALUES('p1_units', ?)",
                    (json.dumps({"rows": [_unit("m1", 0.62, 1)], "excluded": {}, "at": "x"}),))
    assert server.results_payload()["picks"]["picks"] == 1


# ------------------------------------------------------------------ the card's distribution (display only, guarded)
@pytest.mark.parametrize("line", [24.5, 26.0, 27.0])
def test_distribution_reproduces_the_grades_own_chances(line):
    po, pu, pp = grader.p_three(line, 26.3, 6.1)
    v = dist_view.view({"line": line, "dist": {"kind": "normal", "lam": 26.3, "sd": 6.1},
                        "probs_raw": {"over": round(po, 4), "under": round(pu, 4), "push": round(pp, 4)}})
    assert v and abs(v["over"] - po) < 1e-3 and abs(v["under"] - pu) < 1e-3 and abs(v["push"] - pp) < 1e-3
    assert v["lo"] < v["median"] < v["hi"] and abs(sum(p for _, p in v["pmf"]) - 1) < 0.02
    assert (v["push"] > 0) == (line == int(line))


def test_headshot_distribution_and_the_mismatch_guard():
    o, u, pu = perf.hs_probs(30.0, "12", 0.5, 14.5)
    g = {"line": 14.5, "dist": {"kind": "hs_bb", "lam_kills": 30.0, "hs_per_kill": 0.5, "scope": "12", "c": None},
         "probs_raw": {"over": round(o, 4), "under": round(u, 4), "push": round(pu, 4)}}
    assert dist_view.view(g)["over"] == round(o, 4)
    assert dist_view.view(dict(g, probs_raw={"over": 0.9, "under": 0.1, "push": 0})) is None      # not this grade's distribution
    assert dist_view.view({"line": 14.5}) is None and dist_view.view({}) is None


# ------------------------------------------------------------------ operational checks
def test_ops_checks_read_real_input_state(monkeypatch):
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(server.underdog, "_BOARD", {"at": (now - timedelta(minutes=40)).timestamp(), "rows": [], "error": None})
    monkeypatch.setattr(server.prizepicks, "board", lambda: {"at": (now - timedelta(minutes=2)).timestamp(), "state": "ok", "rows": []})
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": []})
    monkeypatch.setattr(server.picklog, "settlement_lag", lambda n: {"open_after_8h": 2, "matches": 1, "oldest_start": "2026-10-17T20:00:00", "hours": 8})
    out = server.ops_checks(now=now)["checks"]
    assert out["feed_underdog"]["state"] == "fail" and out["feed_underdog"]["age_s"] == 2400       # 40 min old: real age
    assert out["feed_prizepicks"]["state"] == "ok" and out["settlement_lag"]["state"] == "warn"
    assert out["grading_backlog"]["listed"] == 0


def test_memory_growth_check_skips_the_warm_up_and_still_catches_real_growth(monkeypatch):
    import membudget
    monkeypatch.setattr(membudget, "process_memory", lambda: {"container_limit_mb": 2147.5, "container_anon_mb": 820.0, "container_mb": 1800.0,
                                                             "container_events": {"oom_kill": 0, "max": 5}})
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

    def mem(hist):
        monkeypatch.setattr(membudget, "HISTORY", __import__("collections").deque(hist, maxlen=1440))
        return server.ops_checks(now=now)["checks"]["memory"]
    warm = [{"anon": 30.0 * i} for i in range(20)]                       # 0 -> 570 MB in the first 20 minutes: the cache fill
    flat = mem(warm + [{"anon": 600.0 + 0.2 * i} for i in range(40)])   # then ~12 MB/h
    assert flat["state"] == "ok" and flat["anon_growth_mb_per_h"] < 20 and not flat["warming_up"]
    early = mem(warm[:15])
    assert early["state"] == "ok" and early["warming_up"] and early["anon_growth_mb_per_h"] is None
    leak = mem(warm + [{"anon": 600.0 + 5 * i} for i in range(40)])     # 300 MB/h after the warm-up
    assert leak["state"] == "warn" and leak["anon_growth_mb_per_h"] == 300.0


def test_settlement_lag_counts_open_picks_long_after_their_start(monkeypatch, tmp_path):
    import picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    con = picklog._db()
    for i, (st, status, test) in enumerate([("2026-10-17T10:00:00Z", "open", 2), ("2026-10-17T10:00:00.000+00:00", "open", 0),
                                            ("2026-10-18T09:00:00Z", "open", 2), ("2026-10-17T10:00:00Z", "settled", 2),
                                            ("2026-10-17T10:00:00Z", "open", 1)]):
        con.execute("INSERT INTO picks (key, logged_at, player, prop, line, status, test, match_start, match_slug) VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"k{i}", "2026-10-17T09:00:00Z", "p", "kills", 20.5, status, test, st, f"m{i % 2}"))
    con.commit(); con.close()
    out = picklog.settlement_lag(datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc))
    assert out["open_after_8h"] == 2 and out["oldest_start"] == "2026-10-17T10:00:00"


# ------------------------------------------------------------------ Slips history (records as stored, leg results)
def test_slip_history_outcomes(monkeypatch):
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    leg = lambda nick, slug, side="More", line=20.5, start="2026-10-17T12:00:00Z": {"nick": nick, "prop": "kills", "scope": "12", "line": line, "side": side,
                                                                                     "match_slug": slug, "start": start}
    rows = [{"player": "a", "prop": "kills", "scope": "12", "match_slug": "m1", "status": "settled", "actual": 25, "result": None},
            {"player": "b", "prop": "kills", "scope": "12", "match_slug": "m2", "status": "settled", "actual": 18, "result": None},
            {"player": "c", "prop": "kills", "scope": "12", "match_slug": "m3", "status": "push", "actual": 20, "result": None}]
    monkeypatch.setattr(server.picklog, "rows_for_matches", lambda slugs: rows)
    slips = {2: [{"slip_id": "b/1", "n": 2, "book": "underdog", "legs": [leg("a", "m1"), leg("b", "m2")]},
                 {"slip_id": "b/2", "n": 2, "book": "underdog", "legs": [leg("a", "m1"), leg("c", "m3", line=20)]},
                 {"slip_id": "b/3", "n": 2, "book": "underdog", "legs": [leg("a", "m1"), leg("d", "m9", start="2026-10-19T12:00:00Z")]},
                 {"slip_id": "b/4", "n": 2, "book": "underdog", "legs": [leg("e", "m8", start="2026-10-19T12:00:00Z"), leg("f", "m7", start="2026-10-19T12:00:00Z")]}]}
    out = server.studio_history_payload([{"batch_id": "b", "created_at": "2026-10-17T08:00:00Z", "closed_at": None, "slips": slips}], now=now)
    got = {s["slip_id"]: (s["outcome"], s["leg_results"], s["started"]) for s in out["batches"][0]["slips"]}
    assert got["b/1"] == ("lost", ["hit", "miss"], True)
    assert got["b/2"] == ("reduced", ["hit", "push"], True)          # no leg lost: the platform's rules decide - never "won"
    assert got["b/3"] == ("in play", ["hit", None], True)
    assert got["b/4"] == ("not started", [None, None], False)


def test_member_batches_lists_open_and_earlier_batches_unchanged(monkeypatch, tmp_path):
    import slip_builder as sb
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "studio.db")
    a = sb.current_batch("member-1")
    b = sb.new_batch("member-1")
    ids = [x["batch_id"] for x in sb.member_batches("member-1")]
    assert ids == [b, a] and sb.member_batches("someone-else") == []


# ------------------------------------------------------------------ the page
def _row(i, **kw):
    start = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    return dict({"id": f"r{i}", "book": "underdog", "player": f"p{i}", "nick": f"p{i}", "team": "A", "opponent": "B", "team_name": "A", "opp_name": "B",
                 "start": start, "prop": "kills", "scope": "12", "line": 27.5, "side": "More", "grade": "B", "model_grade": "B", "p": 0.58, "units": 0.5,
                 "state": "GRADE", "proj": 29.1, "dstat": ["ok", ""], "graded_at": datetime.now(timezone.utc).isoformat(), "match_id": "hltv:1"}, **kw)


@needs_node
def test_destinations_have_addresses(tmp_path):
    out = run_page(tmp_path, """(() => { const r = {};
      goRoute("matches", true); r.matches = [viewHash(), bview, curRoute()];
      goRoute("board", true); r.board = [viewHash(), bview];
      goRoute("moneylines", true); r.ml = [viewHash(), bbook];
      goRoute("board", true, "prizepicks"); r.pp = viewHash();
      goRoute("slips", true); r.slips = viewHash(); goRoute("results", true); r.results = viewHash(); goRoute("one", true); r.one = viewHash();
      return r; })()""")
    assert out["matches"] == ["#matches/all", "matches", "matches"] and out["board"] == ["#board/all", "all"]
    assert out["ml"] == ["#moneylines", "moneylines"] and out["pp"] == "#board/prizepicks"
    assert (out["slips"], out["results"], out["one"]) == ("#slips", "#results", "#one")


@needs_node
def test_compact_rows_keep_availability_and_count_offers_apart_from_markets(tmp_path):
    rows = [_row(1), _row(2, book="prizepicks", id="pp:2", nick="p1", player="p1"),               # p1 on both platforms: 2 offers, 1 market
            _row(3, side="Less", unavailable="Unavailable—side no longer offered", p=0.61)]
    out = run_page(tmp_path, """(() => {
      BK.underdog = {rows: INPUT.rows.filter(r => r.book === "underdog"), at: 1, ver: "", err: null, feed: null, total: 2, loaded: true};
      BK.prizepicks = {rows: INPUT.rows.filter(r => r.book === "prizepicks"), at: 1, ver: "", err: null, feed: null, total: 1, loaded: true};
      assignMatchKeys(INPUT.rows); bbook = "all"; boardRows = viewRows(bbook); bprop = "both"; bside = "both"; bscope = "12";
      bview = "all"; $("#bbets").checked = false; drawBoard();
      return {html: $("#boardout").innerHTML, sum: otBoardSummary(boardRows), chips: $("#bchips").innerHTML}; })()""", {"rows": rows})
    import re
    txt = re.sub(r"<[^>]+>", " ", out["html"])
    assert "Unavailable—side no longer offered" in txt and "61.0%" not in txt                 # never a playable chance
    assert 'disabled title="Unavailable—side no longer offered"' in out["html"]
    assert 'class="crow-head"' in out["html"] and out["html"].count('class="brow2 crow"') == 3
    s = re.sub(r"<[^>]+>", " ", out["sum"])
    assert re.search(r"Offers\s+3", s) and re.search(r"Markets\s+2", s)
    assert "Kills + headshots" in out["chips"] and "Including 0U lines" in out["chips"] and "Clear all" in out["chips"]


@needs_node
def test_moneyline_rows_show_each_sources_price_and_age(tmp_path):
    now = datetime.now(timezone.utc)
    iso = lambda t: t.isoformat(timespec="seconds")
    tp = {"eligible": True, "usable": True, "started": False, "retrieved_at": iso(now - timedelta(minutes=12)), "event_last_observed": iso(now - timedelta(minutes=12)),
          "max_age_s": 4500, "p_a": 0.6, "p_b": 0.4, "odds_a": 1.6, "odds_b": 2.4, "implied_a_raw": 0.625, "implied_b_raw": 0.417, "margin": 0.042,
          "market": "Match Winner", "url": "https://thunderpick.io/match/1"}
    poly = {"available": True, "usable": True, "prob_a": 0.57, "bid": 0.56, "ask": 0.58, "spread": 0.02, "team_a_name": "Alpha", "received_ts": iso(now - timedelta(seconds=40))}
    m = {"match_id": "hltv:1", "teams": {"a": "Alpha", "b": "Bravo", "names_by_platform": {}}, "event": "Cup", "start": iso(now + timedelta(hours=6)),
         "started": False, "bo": 3, "platforms": {"polymarket": poly, "kalshi": None}, "baseline": {}, "side": None, "thunderpick": tp}
    out = run_page(tmp_path, """(() => { ML = {data: {at: new Date().toISOString(), matches: []}, at: Date.now(), err: null}; return mlRow(INPUT.m); })()""", {"m": m})
    assert "Alpha 60.0%" in out and "Bravo 40.0%" in out and "retrieved 12 min ago" in out             # Thunderpick + its age
    assert "Alpha 57.0%" in out and "read " in out                                                     # Polymarket midpoint + read age
    assert 'class="mlmore"' in out and "Primary source · bookmaker odds" in out                       # the full detail, one tap away


@needs_node
def test_card_sections_go_to_their_tab_and_the_chart_is_guarded(tmp_path):
    out = run_page(tmp_path, """(() => ({
      tabs: ["Last 10 matches · Maps 1–2", "Head-to-head vs X", "Stats · Maps 1–2 only", "HLTV", "FACEIT form", "Match", "Match players · A vs B",
             "Match simulation · 20,000 runs", "Role · rifler", "Checks", "Projection comparison", "Notes", "Model distribution",
             "Round simulation · 20,000 runs", "Teammate ratings · 30.0% of team kills"].map(cardTab),
      rounds: roundSim({maps: 2, rounds_mean: 43.5, rounds_p10: 36, rounds_p90: 53, per_round: 0.69, runs: 20000,
                        rows: [{label: "Overtime", note: "a map to OT", chance: 0.233, rounds: 52.9, avg: 38.8, p_over: 0.894}]}, {line: 31.5}, "kills"),
      noRounds: roundSim(null, {line: 31.5}, "kills"),
      none: distChart({grade: {prop: "kills", scope: "12"}, dist_view: null}),
      chart: distChart({grade: {prop: "kills", scope: "12", line: 26}, verdict: {side: "More"},
                        dist_view: {pmf: [[24, 0.2], [25, 0.3], [26, 0.2], [27, 0.3]], mean: 25.6, median: 25, lo: 24, hi: 27, line: 26, over: 0.3, under: 0.5, push: 0.2, note: "N"}})
    }))()""")
    # owner 2026-10-20: the simulations ("sim") render on Overview right under the model distribution (cardTabs)
    assert out["tabs"] == ["history", "history", "history", "history", "history", "match", "match", "sim", "match", "match", "markets", "match",
                           "overview", "sim", "match"]
    assert "Round simulation · 20,000 runs" in out["rounds"] and "Over 31.5" in out["rounds"] and "89.4%" in out["rounds"] and out["noRounds"] == ""
    assert out["none"] == ""
    assert "Push at 26 20.0%" in out["chart"] and 'role="img"' in out["chart"] and "line 26" in out["chart"] and "8 in 10" in out["chart"]


@needs_node
def test_slate_preview_separates_typed_lines_and_shows_defaults(tmp_path):
    rows = [_row(1, nick="ZywOo", player="ZywOo", line=27.5), _row(2, nick="ropz", player="ropz", line=14.5, scope="1")]
    out = run_page(tmp_path, """(() => {
      BK.underdog = {rows: INPUT.rows, at: 1, ver: "", err: null, feed: null, total: 2, loaded: true}; assignMatchKeys(INPUT.rows);
      const x = parseSlate("ZywOo 27.5 k m12\\nropz 14.5\\ndonk 30.5 hs");
      return {x, posted: slatePosted(x[0]), scope: slatePosted(x[1]), typed: slatePosted(x[2])}; })()""", {"rows": rows})
    assert out["x"][0]["propSet"] and out["x"][0]["scSet"] and not out["x"][1]["scSet"] and not out["x"][1]["propSet"]
    assert out["posted"]["kind"] == "posted" and "Underdog 27.5" in out["posted"]["text"]
    assert out["scope"]["kind"] == "typed" and "Posted only on Map 1" in out["scope"]["text"]
    assert out["typed"]["kind"] == "typed" and "no platform posts it" in out["typed"]["text"]


@needs_node
def test_results_page_shows_wins_losses_and_pushes_without_returns(tmp_path):
    d = results_view.member_results({"rows": [_unit(f"m{i}", 0.62, i % 2) for i in range(8)], "excluded": {"push on the board line": 2}, "at": "2026-10-18T06:00:00+00:00",
                                     "pushes": [dict(_unit("m9", 0.30, 0), line=20), dict(_unit("m9", 0.52, 0), line=21)]}, B=100)
    out = run_page(tmp_path, """(() => { RES.data = INPUT; drawResults(); return $("#resultsout").innerHTML; })()""", d)
    # owner brief 2026-11-02: pushes are reported apart (the 0.52 push was no pick) and never count as won
    assert "4–4" in out and "1 push shown apart" in out and "Pushes are left out of the rate." in out and "50.0%" in out
    for w in ("profit", "ROI", "units", "return on"):
        assert w not in out


def test_a_pushed_pick_is_reported_apart_and_the_export_keeps_pushes_apart(tmp_path):
    out = results_view.member_results({"rows": [_unit("m1", 0.62, 0)], "pushes": [dict(_unit("m2", 0.70, 0))], "excluded": {}}, B=50)
    assert (out["picks"]["picks"], out["picks"]["wins"], out["picks"]["losses"], out["picks"]["pushes"]) == (1, 0, 1, 1)
    import learning as L
    con = L.connect(tmp_path / "l.db")
    ex = L.p1_units_export(con)
    assert "pushes" in ex and ex["pushes"] == [] and ex["rows"] == []      # pushes never enter the scored rows


def test_opening_an_address_routes_after_the_whole_script_ran():
    # production 2026-10-18: opening /#results directly threw "Cannot access 'RES' before initialization" (the first
    # applyHash ran before the Results / Slips state further down the script existed) and the page stayed on "Loading"
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert "else if (location.hash) setTimeout(applyHash, 0);" in html and "else if (location.hash) applyHash();" not in html
    assert html.index("const RES = {") > html.index("setTimeout(applyHash, 0)")      # the reason the deferral is needed


def test_more_settings_reads_as_a_button():
    # owner 2026-10-18: "more settings should look like a button on slips tab"
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert '<summary><span class="adv-btn">More settings</span>' in html and ".adv-btn{display:inline-flex" in html
    assert ".studio-adv>summary::-webkit-details-marker{display:none}" in html
