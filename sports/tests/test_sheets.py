"""Sheets agent: PrizePicks / Underdog / Most-consistent sheets (sports/sheets.py), the games-without index
(sports/without.py), public Results (sports/results_public.py), their routes (sports/api_sheets.py) and the
SPORTS_RESULTS_PUBLIC switch (sports/web.py PUBLIC_WHEN)."""
import json

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
def test_prizepicks_sheet_lists_every_line_with_its_variant(sheets_built):
    d = _sheet("prizepicks")
    pts = [r for r in d["rows"] if r["st"] == "points"]
    assert sorted((r["l"], r["v"]) for r in pts) == [(17.5, "goblin"), (20.5, "standard")]
    assert all(r["e"] in d["events"] for r in d["rows"])
    std = [r for r in pts if r["v"] == "standard"][0]
    f1 = sheets_built["f1"]
    assert std["f"] == f1.forecast_id and std["how"] == "stored"                     # the stored chance at its own line
    assert (std["po"], std["pu"]) == (round(f1.p_over, 4), round(f1.p_under, 4))
    gob = [r for r in pts if r["v"] == "goblin"][0]
    assert gob["o"] == [1, 0] and gob["mpo"] is None and gob["edge"] is None        # goblin: no market estimate
    assert gob["how"] in ("ladder", "pmf") and gob["po"] > std["po"]                  # same distribution, lower line


def test_lean_never_contradicts_the_distribution_or_projection():
    assert sheets.lean_of(0.6, 0.4, 22.0, 20.5, {"over": True, "under": True}) == "over"
    assert sheets.lean_of(0.6, 0.4, 19.0, 20.5, {"over": True, "under": True}) is None   # mean disagrees
    assert sheets.lean_of(0.4, 0.6, 19.0, 20.5, {"over": True, "under": False}) is None  # side not offered
    assert sheets.lean_of(0.4, 0.6, 19.0, 20.5, {"over": True, "under": True}) == "under"
    assert sheets.lean_of(None, None, 19.0, 20.5, {"over": True}) is None


def test_edge_is_probability_points_against_the_market_at_that_line(sheets_built):
    for name in ("prizepicks", "underdog"):
        for r in _sheet(name)["rows"]:
            if r["edge"] is None:
                continue
            side = r["lean"] or ("over" if r["po"] >= r["pu"] else "under")
            mine, mkt = (r["po"], r["mpo"]) if side == "over" else (r["pu"], r["mpu"])
            assert abs(r["edge"] - round((mine - mkt) * 100, 1)) <= 0.11


def test_l10_is_strict_at_the_current_line():
    games = [{"participation": "played", "values": {"points": v}, "season": 2026} for v in (21, 20.5, 19, 25)] + \
            [{"participation": "did_not_play", "values": {}, "season": 2026},
             {"participation": "played", "values": {"points": 30}, "season": 2025}]
    w = sheets.window(games, "points", 20.5, 10)
    assert (w["n"], w["o"], w["u"], w["p"], w["seasons"]) == (5, 3, 1, 1, 2)          # push separate, season break


def test_underdog_sheet_keeps_unoffered_side_unoffered(sheets_built):
    d = _sheet("underdog")
    r = [x for x in d["rows"] if x["st"] == "points"][0]
    assert r["o"] == [1, 0] and r["m"][0] == 1.03 and r["lean"] in (None, "over")


def test_consistent_sheet_needs_the_minimum_sample(sheets_built, tmp_path):
    d = _sheet("consistent")
    assert d["label"].startswith("History") and d["rows"] == []                    # the world has < 10 games
    pf = {"markets": [{"player_id": KAT, "player": "K", "event_id": "e1", "stat": "points", "primary": True,
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


def test_started_games_leave_the_sheet(built):                       # noqa: F811
    out = sheets.build("nba", now="2026-10-24T01:00:00Z")            # after the 23:00 start
    assert out["ok"] and out["prizepicks"] == 0 and out["underdog"] == 0


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
    assert nba["gate"]["text"] == "1 of 300 settled props - not yet validated" and not nba["gate"]["validated"]
    assert out["nba"] == nba["gate"]["text"]
    # old forecast: p_over 0.4, mean 21.0, line 22.5 -> Under lean; settled value 25 -> Over -> a lean loss
    assert nba["leans"] == {"n": 1, "wins": 0, "losses": 1, "pushes": 0, "hit_rate": 0.0, "hit_rate_ci95": None}
    assert nba["projection"]["mae"] == 4.0 and nba["projection"]["bias"] == 4.0
    assert [b["label"] for b in nba["bands"]] == ["probability band"] * 4
    assert "returns" in d and "ROI" in d["returns"] and "roi" not in json.dumps(nba).lower()
    assert d["leagues"]["nfl"]["gate"]["text"].startswith("0 of 300")


def test_lean_rule_and_cluster_interval():
    assert results_public.lean_side({"p_over": 0.6, "p_under": 0.4, "mean": 23, "line": 22.5}) == "over"
    assert results_public.lean_side({"p_over": 0.6, "p_under": 0.4, "mean": 22, "line": 22.5}) is None
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
