"""Sheets agent: PrizePicks / Underdog / Most-consistent sheets (sports/sheets.py), the games-without index
(sports/without.py), public Results (sports/results_public.py), their routes (sports/api_sheets.py) and the
SPORTS_RESULTS_PUBLIC switch (sports/web.py PUBLIC_WHEN)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import api_sheets, results_public, sheets, snapshots, store, web, without
from sports.contracts import Membership, Player, Settlement
from sports.tests import helpers as h
from sports.tests.test_ui_snapshots import BRUNSON, KAT, NOW, built  # noqa: F401 - the shared built world


def _sheet(name):
    return json.loads((sheets.sheet_dir("nba") / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def sheets_built(built):                                # noqa: F811
    out = sheets.build("nba", now=NOW)
    assert out["ok"], out
    return dict(built, sheets=out)


# ------------------------------------------------------------------------------------------------------- sheets
def _q(rid, line, variant="standard", fid=None, over=True, under=True, plat="prizepicks"):
    return {"record_id": rid, "platform": plat, "line": line, "variant": variant, "promotional": False,
            "availability": "open", "sides": [{"side": "over", "offered": over}, {"side": "under", "offered": under}],
            "last_seen_at": "2026-10-23T20:59:00Z", "source_updated_at": None, "gradable": True, "forecast_id": fid}


def _fc(fid, kind="scout", line=20.5, po=0.55, pu=0.45, mean=21.4, pmf=None):
    return {"forecast_id": fid, "kind": kind, "model_version": "sm-1" if kind == "scout" else "sharp-2", "line": line,
            "p_over": po, "p_under": pu, "p_push": 0.0, "projection_mean": mean, "forecast_at": "2026-10-23T20:00:00Z",
            "research_only": True, "for_revision": None, "detail": {"pmf": pmf} if pmf else {}}


def _pf(quotes, alternates, forecasts, ladder=None):
    return {"forecasts": {f["forecast_id"]: f for f in forecasts},
            "forecast_detail": {f["forecast_id"]: {"ladder": ladder} for f in forecasts},
            "markets": [{"key": "e1|p1|points|full_game", "event_id": "e1", "player_id": KAT, "player": "K",
                         "position": "C", "team": "NY", "stat": "points", "stat_label": "Points", "primary": True,
                         "capability": "supported", "quotes": quotes, "alternates": alternates,
                         "market": {"l": 20.5, "c": {"po": 0.5, "pu": 0.5, "pp": 0.0, "n": 2, "used": []}, "bk": [],
                                    "at": {}}}]}


EV = {"e1": {"start_utc": "2099-01-01T00:00:00Z"}}


def test_sheet_markets_use_the_board_format_and_one_distribution():
    lad = [{"line": 17.5, "p_over": 0.71, "p_under": 0.29, "p_push": 0.0},
           {"line": 20.5, "p_over": 0.55, "p_under": 0.45, "p_push": 0.0}]
    pf = _pf([_q("r1", 20.5, fid="fc-a"), _q("r9", 20.5, plat="underdog")],
             [_q("r2", 17.5, "goblin", under=False), _q("r3", 23.5, "demon", under=False)], [_fc("fc-a")], lad)
    m, fc, view = sheets.sheet_markets(pf, [], "prizepicks", EV, NOW)
    assert len(m) == 1 and [q["r"] for q in m[0]["q"]] == ["r1", "r2", "r3"]        # this platform only
    q1, q2, q3 = m[0]["q"]
    assert q1["f"] == "fc-a" and "lr" not in q1 and q2["v"] == "goblin" and q2["o"] == [1, 0]
    assert q2["lr"] == {"f": "fc-a", "ln": 20.5, "po": 0.71, "pu": 0.29, "pp": 0.0, "via": "ladder"}
    assert "lr" not in q3                                       # 23.5 not in the ladder and no PMF: never a guess
    assert fc["fc-a"]["po"] == 0.55 and fc["fc-a"]["k"] == "scout"
    assert set(view) == {"e1|p1|points|full_game"} and m[0]["mk"]["c"]["n"] == 2
    # a saved PMF reads any line from the same distribution
    pf = _pf([_q("r1", 20.5, fid="fc-a")], [_q("r3", 23.5, "demon")], [_fc("fc-a", pmf=[18, [0.1, 0.2, 0.3, 0.2, 0.1, 0.05, 0.05]])])
    q3 = sheets.sheet_markets(pf, [], "prizepicks", EV, NOW)[0][0]["q"][1]
    assert q3["lr"]["via"] == "pmf" and q3["lr"]["po"] == 0.05 and q3["lr"]["pu"] == 0.95    # P(24) / P(18..23)


def test_market_only_forecasts_are_never_read_at_other_lines():
    pf = _pf([_q("r1", 20.5, fid="fc-m")], [_q("r2", 17.5, "goblin")], [_fc("fc-m", kind="market")],
             [{"line": 17.5, "p_over": 0.7, "p_under": 0.3, "p_push": 0.0}])
    m, fc, _ = sheets.sheet_markets(pf, [], "prizepicks", EV, NOW)
    assert all("lr" not in q for q in m[0]["q"]) and fc["fc-m"]["k"] == "market"


def test_fixture_world_sheets_and_started_games(sheets_built):
    d = _sheet("prizepicks")
    pts = [m for m in d["markets"] if m["st"] == "points"][0]
    assert sorted((q["l"], q["v"]) for q in pts["q"]) == [(17.5, "goblin"), (20.5, "standard")]
    assert pts["k"] in d["view"] and d["view_version"] == "bv-1"
    assert all(m["e"] in d["events"] for m in d["markets"])
    u = [m for m in _sheet("underdog")["markets"] if m["st"] == "points"][0]["q"][0]
    assert u["o"] == [1, 0] and u["m"][0] == 1.03                        # an unoffered side stays unoffered
    out = sheets.build("nba", now="2026-10-24T01:00:00Z")               # after the 23:00 start
    assert out["ok"] and out["prizepicks"] == 0 and out["underdog"] == 0


def test_consistent_sheet_needs_the_minimum_sample(sheets_built, tmp_path):
    d = _sheet("consistent")
    assert d["label"].startswith("History") and d["rows"] == []                    # the world has < 10 games
    pf = {"markets": [{"key": "e1|k|points|full_game", "player_id": KAT, "player": "K", "event_id": "e1", "stat": "points", "primary": True,
                       "quotes": [{"platform": "prizepicks", "variant": "standard", "line": 20.5}]}]}
    games = [{"participation": "played", "values": {"points": v}, "season": 2026}
             for v in [25, 22, 21, 19, 30, 24, 23, 26, 27, 20, 18, 22, 25, 21, 24, 23, 26, 19, 28, 22]]
    rows = sheets.consistent_rows(pf, games, {"e1": {"start_utc": "2099-01-01T00:00:00Z"}}, NOW)
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "over" and r["l10"]["o"] == 8 and r["r10"] == 0.8 and r["l20"]["n"] == 20
    assert r["r20"] == round(16 / 20, 4)
    assert sheets.consistent_rows(pf, games[:9], {"e1": {"start_utc": "2099-01-01T00:00:00Z"}}, NOW) == []


def test_sheet_route_serves_the_file_and_validates(sheets_built):
    code, body, _ = web.dispatch(web.Request(path="/api/sports/sheets", query={"league": "nba", "sheet": "prizepicks"},
                                             role="member", member="m_1"))
    assert code == 200 and json.loads(body)["platform"] == "prizepicks"
    assert api_sheets.sheet(web.Request(path="", query={"league": "nba", "sheet": "x"}))[0] == 400
    assert api_sheets.sheet(web.Request(path="", query={"league": "mlb"}))[0] == 400
    code, body, _ = web.dispatch(web.Request(path="/api/sports/sheets", query={"league": "nba"}, role=None))
    assert code == 401


# ------------------------------------------------------------------------------------------------ games without
def _without_world():
    con = store.connect()
    T1, T2 = KAT, BRUNSON
    for pid, name in ((T1, "Karl-Anthony Towns"), (T2, "Jalen Brunson")):
        store.put(con, Player(player_id=pid, league="nba", name=name, position="G", status="active",
                              observed_at=h.T0, known_at=h.T0))
    starts = ["2026-10-17T23:00:00Z", "2026-10-19T23:00:00Z", "2026-10-21T23:30:00Z"]
    for i, s in enumerate(starts):
        store.put(con, h.event(eid=f"nba:espn:60{i}", home=h.NY, away=h.BOS, start=s, status="final"))
    # Towns plays all three; Brunson plays games 0 and 2 (game-log memberships) and has no line in game 1
    for i, s in enumerate(starts):
        store.put(con, h.stat(pid=T1, part="min", value=34.0, eid=f"nba:espn:60{i}", observed_at=h.T0))
        store.put(con, Membership(player_id=T1, team_id=h.NY, valid_from=s, valid_to=s, source="espn_gamelog",
                                  observed_at=h.T0, known_at=h.T0))
    for i in (0, 2):
        store.put(con, h.stat(pid=T2, part="min", value=36.0, eid=f"nba:espn:60{i}", observed_at=h.T0))
        store.put(con, Membership(player_id=T2, team_id=h.NY, valid_from=starts[i], valid_to=starts[i],
                                  source="espn_gamelog", observed_at=h.T0, known_at=h.T0))
    return con


def test_without_index_marks_the_missed_game_point_in_time():
    con = _without_world()
    out = without.build(con, "nba", now=NOW)
    con.close()
    assert out["ok"] and out["teams"] >= 1
    doc = without.load("nba")
    games = doc["teams"][h.NY]["games"]
    assert games["nba:espn:601"]["out"] == {BRUNSON: "absent"}           # bracketed by his games 600 and 602
    assert games["nba:espn:600"]["out"] == {} and games["nba:espn:602"]["out"] == {}
    assert doc["players"][BRUNSON] == "Jalen Brunson"
    code, body, _ = web.dispatch(web.Request(path="/api/sports/without", query={"league": "nba", "team": h.NY},
                                             role="member", member="m_1"))
    got = json.loads(body)
    assert code == 200 and got["games"]["nba:espn:601"]["out"] == {BRUNSON: "absent"}
    assert api_sheets.without_view(web.Request(path="", query={"league": "nba", "team": "nba:team:espn:999"}))[0] == 404
    assert api_sheets.without_view(web.Request(path="", query={"league": "nba", "team": "bad"}))[0] == 400


def test_without_explicit_dnp_and_unplaced_players():
    con = _without_world()
    store.put(con, h.stat(pid=BRUNSON, part="min", value=0.0, participation="did_not_play", eid="nba:espn:601",
                          observed_at=h.T0))
    stranger = "nba:player:espn:777"                                     # never placed on the team: never counted
    store.put(con, h.stat(pid=stranger, part="min", value=0.0, participation="did_not_play", eid="nba:espn:601",
                          observed_at=h.T0))
    without.build(con, "nba", now=NOW)
    con.close()
    g = without.load("nba")["teams"][h.NY]["games"]["nba:espn:601"]
    assert g["out"] == {BRUNSON: "dnp"}


def test_team_at_rules():
    t = without.ts
    s = [t("2026-10-17T23:00:00Z"), t("2026-10-19T23:00:00Z"), t("2026-10-21T23:00:00Z")]
    m = [(s[0], s[0], "A", True), (s[2], s[2], "A", True)]
    assert without.team_at(m, s[1], ("A", "B"), False) == ("A", 3)
    m2 = [(s[0], s[0], "A", True), (s[2], s[2], "C", True)]                       # traded in between: not placed
    assert without.team_at(m2, s[1], ("A", "B"), False) == (None, None)
    m3 = [(s[0], s[0], "A", True), (t("2026-07-01T00:00:00Z"), None, "A", False)]
    assert without.team_at(m3, s[1], ("A", "B"), False)[0] == "A"                    # rule 2 (roster interval)
    m4 = [(t("2025-03-01T00:00:00Z"), t("2025-03-01T00:00:00Z"), "A", True)]          # last season only
    assert without.team_at(m4, s[1], ("A", "B"), False) == (None, None)
    assert without.team_at(m4, s[1], ("A", "B"), True) == ("A", 4)


# ----------------------------------------------------------------------------------------------- public results
def test_results_public_counts_settled_props_against_the_gate(built):     # noqa: F811
    con = store.connect(read_only=True)
    out = results_public.build(con, now=NOW)
    con.close()
    d = json.loads(results_public.path().read_text(encoding="utf-8"))
    nba = d["leagues"]["nba"]
    assert nba["settled"] == 1 and nba["props"] == 1
    assert nba["gate"]["text"] == "1 settled (minimum 300)" and not nba["gate"]["validated"]
    assert "gate F" in nba["gate"]["rule"] and not nba["gate"]["under_review"]
    assert out["nba"] == nba["gate"]["text"]
    # the settled forecast is sharp-2 = a market-only record: no Scout lean (the board's rule)
    assert nba["leans"]["n"] == 0 and nba["leans"]["hit_rate"] is None
    assert nba["projection"]["mae"] == 4.0 and nba["projection"]["bias"] == 4.0
    assert [b["label"] for b in nba["bands"]] == ["probability band"] * 4
    assert "returns" in d and "ROI" in d["returns"] and "roi" not in json.dumps(nba).lower()
    assert d["leagues"]["nfl"]["gate"]["text"] == "0 settled (minimum 300)"


def test_lean_rule_and_cluster_interval():
    L = results_public.lean_side
    assert L({"model": "sm-1", "p_over": 0.6, "p_under": 0.4, "mean": 23, "line": 22.5}) == "over"
    assert L({"model": "sm-1", "p_over": 0.6, "p_under": 0.4, "mean": 22, "line": 22.5}) is None
    assert L({"model": "sm-1", "p_over": 0.3, "p_under": 0.7, "mean": None, "line": 22.5}) == "under"
    assert L({"model": "sm-1", "p_over": 0.49, "p_under": 0.45, "mean": 23, "line": 22.5}) is None   # not > 50%
    assert L({"model": "sharp-2", "p_over": 0.7, "p_under": 0.3, "mean": 23, "line": 22.5}) is None  # market-only
    rows = [{"event_id": f"g{i // 2}", "forecast_id": str(i), "lean_result": "win" if i % 3 else "loss"}
            for i in range(40)]
    ci = results_public.cluster_ci(rows, results_public._hit)
    assert ci and ci[0] < results_public._hit(rows) < ci[1]


def test_results_route_is_members_only_unless_the_public_flag_is_on(built, monkeypatch):  # noqa: F811
    con = store.connect(read_only=True)
    results_public.build(con, now=NOW)
    con.close()
    req = dict(path="/api/sports/results/public", method="GET")
    monkeypatch.delenv("SPORTS_RESULTS_PUBLIC", raising=False)
    assert web.dispatch(web.Request(role=None, **req))[0] == 401
    assert web.dispatch(web.Request(role="member", member="m_1", **req))[0] == 200
    monkeypatch.setenv("SPORTS_RESULTS_PUBLIC", "true")                     # only exactly "1" opens it
    assert web.dispatch(web.Request(role=None, **req))[0] == 401
    monkeypatch.setenv("SPORTS_RESULTS_PUBLIC", "1")
    code, body, _ = web.dispatch(web.Request(role=None, **req))
    assert code == 200 and json.loads(body)["version"] == results_public.VERSION
    # the flag opens THAT path only - every other route still refuses a request without a role
    for path, (target, access, methods) in web.ROUTES.items():
        if path in web.PUBLIC_WHEN:
            continue
        p = path[:-1] + "x" if path.endswith("/*") else path
        assert web.dispatch(web.Request(path=p, method=methods[0], role=None))[0] == 401, path
    assert all(web.ROUTES[p][1] == web.MEMBER for p in web.PUBLIC_WHEN)


def test_settlement_model_types_used_by_results_exist():
    assert Settlement.__dataclass_fields__["affected_forecast_ids"]
    assert snapshots.LEAGUES == ("nba", "nfl")


# ---------------------------------------------------------------------------------------------------- UI modules
WEB = Path(__file__).resolve().parents[2] / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_sheet_helpers_in_node():
    script = Path(__file__).with_name("ui_sheets_test.js")
    r = subprocess.run(["node", str(script), str(WEB / "sports")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr + r.stdout


def test_page_hooks_are_one_line_each_and_scoped():
    page = (WEB / "sports.html").read_text(encoding="utf-8")
    assert page.count('data-view="sheets"') == 1 and "/sports/sheets.js" in page and "/sports/without.js" in page
    assert page.index("/sports/sheets.js") < page.index("/sports/app.js")
    app = (WEB / "sports" / "app.js").read_text(encoding="utf-8")
    assert app.count("window.SportsSheets.render(") == 1
    css = (WEB / "sports" / "sheets.css").read_text(encoding="utf-8")
    for line in css.splitlines():
        if line.startswith((".", "@media")) and not line.startswith("@media"):
            assert line.startswith(".sw "), line                     # every rule scoped under body.sw
    res = (WEB / "sports" / "results.html").read_text(encoding="utf-8")
    assert "/sports/results_public.js" in res and 'class="sw"' in res
    for f in ("sheets.js", "without.js", "results_public.js"):
        src = (WEB / "sports" / f).read_text(encoding="utf-8").lower()
        assert "stake_units" not in src and "profit:" not in src                # never a return on these views
        assert "localstorage.setitem" not in src or "try" in src


def test_results_page_alias_and_public_api_base(monkeypatch):
    from sports import gateway
    monkeypatch.setenv("SPORTS_API_BASE", "https://sports.example")
    code, body, ctype, _ = gateway.handle_get("/sports/results", "", {}, member=lambda: None, allowed=lambda: False,
                                              admin=lambda: True, headers={})
    assert code == 200 and b"results_public.js" in body and ctype.startswith("text/html")
    # owner 2026-11-09: no member Results page - everyone but the admin is sent to the slate (alias and file name alike)
    for path in ("/sports/results", "/sports/results.html"):
        code, body, _, hdrs = gateway.handle_get(path, "", {}, member=lambda: "m1", allowed=lambda: True,
                                                 admin=lambda: False, headers={})
        assert code == 302 and hdrs["Location"] == "/sports#/slate" and body == b""
    code, body, _, _ = gateway.handle_get("/api/sports/token", "", {}, member=lambda: None, allowed=lambda: False,
                                          admin=lambda: False, headers={})
    assert code == 401 and json.loads(body) == {"error": "members_only", "api_base": "https://sports.example"}


def test_sheets_job_is_registered_with_a_memory_cap():
    from sports import scheduler
    j = scheduler.default_jobs()["sheets"]
    assert j["cls"] == scheduler.HEAVY and j["cap_mb"] <= 200 and j["every_s"] >= 300 and j["flag"] == "SPORTS_INGEST"
    assert j["argv"][-3:] == ["build", "nba", "nfl"] or "build" in j["argv"]
