"""UI snapshots (sports/snapshots.py) and the read endpoints (sports/api_board.py): every displayed number traces to a
typed record, one forecast renders identically on every surface, handlers never touch the store."""
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from sports import api_board, fair, snapshots, store, web
from sports.contracts import ContextSnapshot, Membership, Player, SideQuote, Settlement, Team
from sports.tests import helpers as h

NOW = "2026-10-23T21:00:00Z"
KAT = "nba:player:espn:3134907"
BRUNSON = "nba:player:espn:3934672"
WEB = Path(__file__).resolve().parents[2] / "web" / "sports"


def test_latest_stream_does_not_decode_the_whole_offer_history(monkeypatch):
    con = store.connect()
    for i in range(12):
        store.put(con, h.offer(oid=f"stream-{i}"))
    decoded = []
    unpack = store._unpack
    def counted(payload):
        decoded.append(1)
        return unpack(payload)
    monkeypatch.setattr(store, "_unpack", counted)
    rows = snapshots._iter_latest(con, "offer")
    first = next(rows)
    assert len(decoded) == 1
    remaining = list(rows)
    assert len(remaining) == 11
    assert [first, *remaining] == snapshots._latest(con, "offer")
    decoded.clear()
    current = store.iter_offers(con, league="nba")
    first = next(current)
    assert len(decoded) == 1
    assert [first, *current] == store.offers(con, league="nba")
    con.close()


def _gamelog_body():
    names = ["minutes", "fieldGoalsMade-fieldGoalsAttempted", "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
             "freeThrowsMade-freeThrowsAttempted", "totalRebounds", "assists", "blocks", "steals", "turnovers", "points"]
    games = [("501", "2026-10-21T23:30:00.000+00:00", "vs", "18", "2", ["34", "9-18", "2-6", "4-5", "10", "3", "1", "0", "2", "24"]),
             ("502", "2026-10-19T23:00:00.000+00:00", "@", "4", "18", ["36", "8-15", "1-3", "3-3", "12", "2", "0", "1", "1", "20"]),
             ("503", "2026-10-17T23:00:00.000+00:00", "vs", "18", "15", ["0", "0-0", "0-0", "0-0", "0", "0", "0", "0", "0", "0"])]
    return {"names": names, "filters": [{"name": "season", "value": "2027"}],
            "events": {g[0]: {"gameDate": g[1], "atVs": g[2], "homeTeamId": g[3], "awayTeamId": g[4], "team": {"id": "18"},
                              "opponent": {"abbreviation": "X"}, "gameResult": "W"} for g in games},
            "seasonTypes": [{"displayName": "2026-27 Regular Season",
                             "categories": [{"events": [{"eventId": g[0], "stats": g[5]} for g in games]}]}]}


def _fc_consistent(offer_rid, line=20.5, mean=21.0, **kw):
    a, b = fair.shape_params("basketball", "points", "normal")
    o, u, p = fair.dist_probs("normal", a, b, mean, line)
    return h.forecast(line=line, offer_snapshot_ids=(offer_rid,), model_version="sharp-2", projection_mean=mean,
                      distribution=(("family", "normal"), ("mean", mean), ("var_a", a), ("var_b", b)),
                      p_over=o, p_under=u, p_push=p, p_over_given_no_push=o / (o + u), p_under_given_no_push=u / (o + u),
                      chosen_side=None, **kw)


@pytest.fixture
def built(tmp_path):
    con = store.connect()
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())                                                       # NY @ BOS, starts 23:00
    for pid, name in ((KAT, "Karl-Anthony Towns"), (BRUNSON, "Jalen Brunson")):
        store.put(con, Player(player_id=pid, league="nba", name=name, position="C", status="active",
                              observed_at=h.T0, known_at=h.T0))
        store.put(con, Membership(player_id=pid, team_id=h.NY, valid_from="2026-07-01T00:00:00Z", source="espn",
                                  observed_at=h.T0, known_at=h.T0))
    store.put(con, ContextSnapshot(league="nba", kind="injury", subject_id=BRUNSON, event_id=None, status="questionable",
                                   present="yes", detail="Ankle", as_of="2026-10-22T18:00:00Z", observed_at=h.T0,
                                   known_at=h.T0, source_updated_at="2026-10-22T18:00:00Z", source_time_unknown=False))
    pp = store.put(con, h.offer())["record_id"]                                       # PrizePicks 20.5 both sides
    ud = store.put(con, h.offer(platform="underdog", oid="ud:1", variant="balanced",
                                sides=(SideQuote("over", True, "payout_multiplier", multiplier=1.03),
                                       SideQuote("under", False))))["record_id"]   # Under NOT offered
    store.put(con, h.offer(oid="pp:alt", line=17.5, variant="goblin", sides=(SideQuote("over", True), SideQuote("under", False))))
    store.put(con, h.offer(oid="pp:pra", stat="pra", line=35.5))
    store.put(con, h.kalshi_rung(19.5, 0.55, 0.59))
    store.put(con, h.kalshi_rung(21.5, 0.42, 0.46))
    store.put(con, h.kalshi_rung(29.5, 0.08, 0.11))
    f1 = _fc_consistent(pp)
    store.put(con, f1)
    f2 = h.forecast(line=20.5, offer_snapshot_ids=(ud,), model_version="sharp-2", p_over=0.58, p_under=0.42, p_push=0.0,
                    p_over_given_no_push=0.58, p_under_given_no_push=0.42, chosen_side=None,
                    reason_codes=("market_only_baseline", "between_rungs"), sides_available=("over",),
                    distribution=(("family", "normal"), ("mean", 21.0), ("var_a", 3.1), ("var_b", 0.0)))
    store.put(con, f2)                                                               # blended: ladder must be withheld
    evs, mems, stats = snapshots.gamelog_records("nba", "3134907", _gamelog_body(), h.T0)
    first = store.put_many(con, evs + mems + stats)
    again = store.put_many(con, evs + mems + stats)
    # a later box score of game 501 (source espn) beats the game log value
    store.put(con, h.stat(part="pts", value=25.0, eid="nba:espn:501", observed_at=h.T0))
    # a settled earlier forecast for the results ledger
    store.put(con, h.event(eid="nba:espn:501", home=h.NY, away=h.BOS, start="2026-10-21T23:30:00Z", status="final"))
    old = h.forecast(event_id="nba:espn:501", line=22.5, model_version="sharp-2", offer_snapshot_ids=("offer:old",),
                     forecast_at="2026-10-21T20:00:00Z", projection_mean=21.0, p_over=0.4, p_under=0.6, p_push=0.0,
                     p_over_given_no_push=0.4, p_under_given_no_push=0.6, chosen_side=None)
    store.put(con, old)
    for side, status in (("over", "win"), ("under", "loss")):
        store.put(con, Settlement(settlement_id=f"st-{side}", platform="prizepicks", offer_id="pp:old", side=side,
                                  line=22.5, event_id="nba:espn:501", player_id=KAT, stat="points", status=status,
                                  outcome="over", source_value=25.0, source="espn", source_ref="stat:x",
                                  rule_version="t", affected_forecast_ids=(old.forecast_id,),
                                  settled_at="2026-10-22T06:00:00Z", observed_at="2026-10-22T06:00:00Z",
                                  known_at="2026-10-22T06:00:00Z"))
    out = snapshots.build(con, "nba", now=NOW)
    con.close()
    return {"out": out, "pp": pp, "ud": ud, "f1": f1, "f2": f2, "old": old, "first": first, "again": again,
            "root": snapshots.snap_root() / "nba"}


def _load(b, name):
    return json.loads((b["root"] / name).read_text(encoding="utf-8"))


def test_gamelog_ingest_is_typed_and_idempotent(built):
    assert built["first"]["invalid"] == 0 and built["first"]["inserted"] > 20
    assert built["again"]["inserted"] == 0 and built["again"]["revision"] == 0


def test_board_rows_align_only_identical_markets(built):
    b = _load(built, "board.json")
    rows = [r for r in b["markets"] if r["st"] == "points"]
    assert len(rows) == 1
    r = rows[0]
    assert sorted(q["p"] for q in r["q"]) == ["prizepicks", "underdog"]       # same event / player / stat / scope
    assert r["alt"] == 1                                                       # goblin kept apart, counted
    assert [q["l"] for q in r["kn"]] == [19.5, 21.5] and r["kr"] == 3          # nearest Kalshi rungs + ladder size
    ud = [q for q in r["q"] if q["p"] == "underdog"][0]
    assert ud["o"] == [1, 0] and ud["m"][0] == 1.03                            # unavailable side stays unavailable
    assert not any(m["st"] == "pra" for m in b["markets"])                    # combo -> secondary file
    more = _load(built, "board_more.json")
    assert [m["st"] for m in more["markets"]] == ["pra"] and more["markets"][0]["cap"] == "research_only"


def test_same_forecast_same_numbers_on_every_surface(built):
    fid = built["f1"].forecast_id
    board = _load(built, "board.json")["fc"][fid]
    full = _load(built, "forecasts.json")["forecasts"][fid]
    player = _load(built, f"players/{snapshots.safe_name(KAT)}.json")["forecasts"][fid]
    rec = built["f1"]
    for a, b_ in (("po", "p_over"), ("pu", "p_under"), ("pp", "p_push"), ("mu", "projection_mean")):
        assert board[a] == full[b_] == player[b_] == getattr(rec, b_)
    code, body = api_board.forecast(web.Request(path="", query={"league": "nba", "id": fid}))
    assert code == 200 and body["forecast"]["p_over"] == rec.p_over


def test_ladder_reproduces_the_stored_chance_or_is_withheld(built):
    p = _load(built, f"players/{snapshots.safe_name(KAT)}.json")
    d1 = p["forecast_detail"][built["f1"].forecast_id]
    stored = [r for r in d1["ladder"] if r["stored"]][0]
    assert stored["p_over"] == built["f1"].p_over and stored["line"] == 20.5
    overs = [r["p_over"] for r in d1["ladder"]]
    assert overs == sorted(overs, reverse=True)                                # monotonic in the line
    for r in d1["ladder"]:
        assert abs(r["p_over"] + r["p_under"] + r["p_push"] - 1) < 1e-5
        if float(r["line"]).is_integer():
            assert r["p_push"] > 0
        else:
            assert r["p_push"] == 0
    assert abs(sum(b[2] for b in d1["pmf"]["bins"]) - 1) < 0.01
    d2 = p["forecast_detail"][built["f2"].forecast_id]
    assert d2["ladder"] is None and "blends" in d2["ladder_withheld"]


def test_history_is_typed_chronological_and_prefers_the_box_score(built):
    hist = _load(built, f"history/{snapshots.safe_name(KAT)}.json")["games"]
    assert [g["event_id"] for g in hist] == ["nba:espn:501", "nba:espn:502", "nba:espn:503"]
    g501 = hist[0]
    assert g501["values"]["points"] == 25.0 and g501["record_ids"]["pts"].startswith("stat:espn:")
    assert g501["values"]["pra"] == 25.0 + 10 + 3                             # combo summed from typed parts
    assert g501["home"] is True and g501["opponent"]["abbr"] == "BOS"
    assert hist[1]["home"] is False and hist[1]["opponent"]["abbr"] == "CHI" and hist[1]["parts"]["fga"] == 15
    assert hist[2]["participation"] == "did_not_play" and hist[2]["values"] == {}
    assert all(g["start_utc"] < NOW for g in hist)


def test_factors_have_sample_sizes_and_unheld_factors_are_named(built):
    p = _load(built, f"players/{snapshots.safe_name(KAT)}.json")
    mins = [s for s in p["factors"]["stats"] if s["key"] == "min"][0]
    l3 = mins["windows"][0]
    assert l3["window"] == "L3" and l3["n"] == 2 and l3["mean"] == 35.0       # DNP game is not a played game
    ctx = {c["key"]: c for c in p["factors"]["context"]}
    assert ctx["teammates_out"]["value"] == 1 and ctx["teammates_out"]["items"][0]["status"] == "questionable"
    assert ctx["venue"]["value"] == "away" and ctx["back_to_back"]["value"] is False
    assert "Likely defender" in p["factors"]["not_held"] and "Usage rate" not in p["factors"]["not_held"]


def test_slate_has_coverage_injuries_and_start_provenance(built):
    s = _load(built, "slate.json")
    e = [x for x in s["events"] if x["event_id"] == "nba:espn:1"][0]
    assert e["start_utc"] == h.START and e["start_source"] == "espn" and e["home"]["abbr"] == "BOS"
    assert e["coverage"]["by_platform"] == {"prizepicks": 2, "underdog": 1, "kalshi": 3}   # points + the pra combo row
    assert [i["player_id"] for i in e["injuries"]] == [BRUNSON]
    assert all(x["start_source"] == "espn" for x in s["events"])             # game-log events never on the slate


def test_results_ledger_scores_accuracy_separately_from_payouts(built):
    r = _load(built, "results.json")
    row = [x for x in r["rows"] if x["forecast_id"] == built["old"].forecast_id][0]
    assert row["outcome"] == "over" and row["actual"] == 25.0 and row["error"] == 4.0
    assert row["p_outcome"] == 0.4
    assert math.isclose(row["brier"], (0.4 - 1) ** 2 + 0.6 ** 2, rel_tol=1e-6)
    assert math.isclose(row["log_loss"], -math.log(0.4), rel_tol=1e-6)
    s = r["summary"]["sharp-2"]
    assert s["settled"] == 1 and s["returns"]["recorded"] == 0 and s["projection"]["mae"] == 4.0


def test_endpoints_serve_snapshot_bytes_without_touching_the_store(built, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a handler opened the store")
    monkeypatch.setattr(store, "connect", boom)
    R = lambda **q: web.Request(path="", query=q)
    code, body, ctype = api_board.board(R(league="nba"))
    assert code == 200 and ctype == "application/json" and json.loads(body)["part"] == "primary"
    assert api_board.board(R(league="nba", part="more"))[0] == 200
    assert api_board.board(R(league="mlb"))[0] == 400
    assert api_board.board(R(league="nba", part="x"))[0] == 400
    assert api_board.slate(R(league="nba"))[0] == 200
    assert api_board.player(R(league="nba", id=KAT))[0] == 200
    assert api_board.history(R(league="nba", id=KAT))[0] == 200
    assert api_board.player(R(league="nba", id="nba:player:espn:999"))[0] == 404
    assert api_board.player(R(league="nba", id="../../etc"))[0] == 400
    assert api_board.player(R(league="nfl", id=KAT))[0] == 400
    assert api_board.forecast(R(league="nba", id="fc-00000000"))[0] == 404
    assert api_board.forecast(R(league="nba", id="x"))[0] == 400
    assert api_board.results(R(league="nba"))[0] == 200


def test_endpoints_before_the_first_build_say_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path / "none")
    R = lambda **q: web.Request(path="", query=q)
    assert api_board.board(R(league="nba"))[0] == 503
    assert api_board.player(R(league="nba", id=KAT))[0] == 503
    assert api_board.forecast(R(league="nba", id="fc-00000000"))[0] == 503


def test_cache_follows_the_file(built):
    R = web.Request(path="", query={"league": "nba"})
    a = api_board.slate(R)[1]
    path = built["root"] / "slate.json"
    obj = json.loads(path.read_text())
    obj["events"] = []
    snapshots._write(path, obj)
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10 ** 9))
    b = api_board.slate(R)[1]
    assert a != b and json.loads(b)["events"] == []


def test_routes_are_registered_member_only():
    for p in ("manifest", "slate", "board", "forecast", "player", "history", "results"):
        target, access, methods = web.ROUTES[f"/api/sports/{p}"]
        assert target == f"sports.api_board:{p}" and access == web.MEMBER and methods == ("GET",)
        assert callable(web._resolve(target))


def test_dispatch_refuses_without_a_member_role(built):
    code, body, _ = web.dispatch(web.Request(path="/api/sports/board", query={"league": "nba"}, role=None))
    assert code == 401
    code, body, _ = web.dispatch(web.Request(path="/api/sports/board", query={"league": "nba"}, role="member"))
    assert code == 200 and json.loads(body)["league"] == "nba"


def test_forecast_is_attached_only_to_the_offer_it_was_made_for(built):
    con = store.connect()
    # the PrizePicks offer moves its line: the old forecast must no longer be shown on it
    store.put(con, h.offer(line=21.5, observed_at="2026-10-23T20:30:00Z"))
    snapshots.build(con, "nba", now=NOW)
    b = _load(built, "board.json")
    pp = [q for r in b["markets"] for q in r["q"] if q["p"] == "prizepicks"][0]
    assert pp["l"] == 21.5 and pp["f"] is None
    # a price-only revision (same line and sides) keeps the forecast, flagged with the revision it was made for
    store.put(con, h.offer(platform="underdog", oid="ud:1", variant="balanced", observed_at="2026-10-23T20:31:00Z",
                           sides=(SideQuote("over", True, "payout_multiplier", multiplier=1.05),
                                  SideQuote("under", False))))
    snapshots.build(con, "nba", now=NOW)
    con.close()
    b = _load(built, "board.json")
    ud = [q for r in b["markets"] for q in r["q"] if q["p"] == "underdog"][0]
    assert ud["f"] == built["f2"].forecast_id and b["fc"][ud["f"]]["rv"] == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_display_functions_in_node():
    script = Path(__file__).resolve().parent / "ui_stats_test.js"
    r = subprocess.run(["node", str(script), str(WEB / "stats.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_page_and_assets_exist_and_are_scoped():
    page = (WEB.parent / "sports.html").read_text(encoding="utf-8")
    for a in ("/sports/sports.css", "/sports/shell.js", "/sports/stats.js", "/sports/app.js"):
        assert a in page and (WEB / a.split("/")[-1]).exists()
    import re
    css = re.sub(r"/\*.*?\*/", "", (WEB / "sports.css").read_text(encoding="utf-8"), flags=re.S)
    for rule in re.findall(r"(?:^|})\s*([^{}@][^{}]*)\{", css):
        sel = rule.strip()
        if not sel or sel.startswith("@") or sel.startswith(("from", "to", "0%", "100%")):
            continue
        for part in sel.split(","):
            assert part.strip().startswith(("body.sw", ".sw")), part
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "/api/sports/token" in app and "Authorization" in app and "Bearer " in app
    assert "localStorage.setItem" in app


def test_history_ingest_skips_unchanged_logs(monkeypatch):
    monkeypatch.setattr(snapshots, "_gamelog_bodies", lambda lg, aid, seasons, cache_only: [_gamelog_body()])
    con = store.connect()
    a = snapshots.ingest_history(con, "nba", [KAT], observed_at=h.T0)
    b = snapshots.ingest_history(con, "nba", [KAT], observed_at=h.T0)
    con.close()
    assert a["players"] == 1 and a["inserted"] > 20 and a["invalid"] == 0
    assert b["unchanged"] == 1 and b["inserted"] == 0 and b["duplicate"] == 0


def test_started_games_leave_the_board_but_stay_on_the_slate(built):
    con = store.connect()
    snapshots.build(con, "nba", now="2026-10-23T23:30:00Z")          # 30 min after tip-off
    con.close()
    assert _load(built, "board.json")["markets"] == []
    e = [x for x in _load(built, "slate.json")["events"] if x["event_id"] == "nba:espn:1"][0]
    assert e["started"] is True


def test_board_rows_carry_one_team_tag_from_the_game(tmp_path):
    """N9: a row without a usable roster membership (none, or a stale one after a trade) gets its team from THIS
    game's two teams by the offer's code; every tag uses one abbreviation table (ESPN 'NY' / board 'NYK' -> 'NYK')."""
    from sports import names
    con = store.connect()
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())                                                       # NY @ BOS
    traded = "nba:player:espn:77"
    store.put(con, Player(player_id=KAT, league="nba", name="Karl-Anthony Towns", observed_at=h.T0, known_at=h.T0))
    store.put(con, Player(player_id=traded, league="nba", name="Traded Guard", observed_at=h.T0, known_at=h.T0))
    store.put(con, Membership(player_id=traded, team_id=h.CHI, valid_from="2026-07-01T00:00:00Z", source="espn",
                              observed_at=h.T0, known_at=h.T0))                   # stale: CHI is not in this game
    store.put(con, h.kalshi_rung(19.5, 0.55, 0.59, team=None))                     # first offer of the row: no team
    store.put(con, h.offer())                                                       # team "NYK"
    store.put(con, h.offer(oid="pp:t", player_id=traded, player_name="Traded Guard", player_key="traded guard",
                           team="BOS"))
    snapshots.build(con, "nba", now=NOW, base=tmp_path, history=False, results=False)
    con.close()
    b = json.loads((tmp_path / "nba" / "board.json").read_text(encoding="utf-8"))
    rows = {m["pid"]: m for m in b["markets"] if m["st"] == "points"}
    assert rows[KAT]["tid"] == h.NY and rows[KAT]["tm"] == "NYK"
    assert rows[traded]["tid"] == h.BOS and rows[traded]["tm"] == "BOS"
    assert names.team_abbr("nba", "UTAH") == "UTA" and names.team_abbr("nba", "uta") == "UTA"
    assert names.team_abbr("nfl", "WSH") == "WAS" and names.team_abbr("nfl", "NO") == "NO"


def test_the_research_workspace_reads_these_names(built):
    """workspace_sports.py reads the sports API over HTTP and cannot import this package, so the names it reads are held here."""
    b = _load(built, "board.json")
    assert {"version", "as_of", "built_at", "stats", "events", "injuries", "markets", "fc"} <= set(b)
    m = b["markets"][0]
    assert {"k", "e", "pid", "n", "pos", "tm", "tid", "st", "q", "mk"} <= set(m) and m["k"].rsplit("|", 1)[-1] == "full_game"
    assert {"r", "p", "l", "o", "t", "g", "f"} <= set(m["q"][0]) and m["q"][0]["r"].startswith(f"offer:{m['q'][0]['p']}:")
    ev = b["events"][m["e"]]
    assert {"start_utc", "home", "away", "started", "locked"} <= set(ev) and {"team_id", "abbr", "name"} <= set(ev["home"])
    assert {"m", "mu", "po", "pu", "pp", "at"} <= set(next(iter(b["fc"].values())))
    row = next(iter(_load(built, "board_view.json")["rows"].values()))
    assert all(len(x) == 8 for x in row["lines"].values())
    g = json.loads(next((built["root"] / "history").glob("*.json")).read_text(encoding="utf-8"))["games"][0]
    assert {"start_utc", "opponent", "participation", "values", "season", "home"} <= set(g)
    pl = json.loads(next((built["root"] / "players").glob("*.json")).read_text(encoding="utf-8"))
    assert {"position", "injury", "next_event", "factors"} <= set(pl) and {"stats", "context", "engine"} <= set(pl["factors"])
