"""sg-2 (2026-11-05): broken / degenerate projections are HELD - no letter, a "Research estimate" with the reason - on
every surface (board quote signal, sheets, slip pool, tracked picks). Holds only: no chance is lowered and no letter of a
row that is not held changes. Live case: Travis Hunter receptions 1.5 - projection 0.05 (the model's floor), 80% range
[0, 0], P(Under) 1.0 shown as an A, while he cleared 1.5 in 4 of his last 10 games."""
import json
from types import SimpleNamespace

from sports import board_view, picks, snapshots
from sports.slips import pool

HUNTER_L10 = [4, 6, 0, 10]
MKT = {"po": 0.5, "pu": 0.5, "n": 3}


def _fc(fid, po, pu, mu, line):
    return {"forecast_id": fid, "model_version": "sm-1", "kind": "scout", "p_over": po, "p_under": pu,
            "projection_mean": mu, "line": line, "forecast_at": "2026-11-05T00:00:00Z"}


# ------------------------------------------------------------------------------------------------ the rules
def test_floor_projection_is_held_at_every_line_size():
    # the live Hunter rows: 1.5 (was graded A - sg-1 skipped lines under 2) and 9.5 (already held by ratio_low)
    assert board_view.sanity_check(0.05, 1.5, "under", HUNTER_L10) == "floor"
    assert board_view.sanity_check(0.05, 0.5, "under", HUNTER_L10) == "floor"
    assert board_view.sanity_check(0.05, 9.5, "under", HUNTER_L10) == "ratio_low"      # sg-1 code kept first
    assert board_view.sanity_check(0.051, 0.5, "under", [0, 10, 0, 10]) is None         # just above the floor


def test_certain_chance_is_held_shown_or_raw():
    assert board_view.sanity_check(3.5, 0.5, "over", [9, 1, 0, 10], chances=(0.9995, 0.0005)) == "certain"
    assert board_view.sanity_check(3.5, 0.5, "over", [9, 1, 0, 10], chances=(0.0, 1.0)) == "certain"
    assert board_view.sanity_check(3.5, 0.5, "over", [9, 1, 0, 10], chances=(0.998, 0.002)) is None
    # cal-mi passthrough: the shown chance looks sane, the raw forecast under it is a point mass -> held
    x, code = board_view.quote_x("nba", "points", 0.9, 0.1, 30.0, 20.5, MKT, [8, 2, 0, 10], raw=(1.0, 0.0))
    assert code == "certain" and x["b"] == "certain" and "gr" not in x


def test_thin_history_is_held_only_when_history_was_read():
    assert board_view.sanity_check(118.9, 161.5, "under", [0, 1, 0, 1]) == "thin"         # Joe Fagnano, 1 game
    assert board_view.sanity_check(20.0, 18.5, "over", [2, 0, 0, 2]) == "thin"
    assert board_view.sanity_check(20.0, 18.5, "over", None, played=2) == "thin"
    assert board_view.sanity_check(20.0, 18.5, "over", [2, 1, 0, 3]) is None              # 3 games: not thin
    assert board_view.sanity_check(20.0, 18.5, "over", None) is None                      # no history read: unknown


def test_stale_history_is_held():
    now = "2026-11-05T05:00:00Z"
    assert board_view.sanity_check(20.0, 18.5, "over", [6, 4, 0, 10], last_at="2025-10-01T23:00:00Z", now=now) == "stale"
    # an NBA season opener: last game in April is about 200 days back - never held for that
    assert board_view.sanity_check(20.0, 18.5, "over", [6, 4, 0, 10], last_at="2026-04-12T23:00:00Z", now=now) is None
    assert board_view.sanity_check(20.0, 18.5, "over", [6, 4, 0, 10], last_at="garbage", now=now) is None


def test_small_line_collapse_contradicted_by_history():
    # a projection under 25% of a small line is held when the player cleared that line in >= 30% of his last 10 ...
    assert board_view.sanity_check(0.3, 1.5, "under", HUNTER_L10) == "small_low"
    assert board_view.sanity_check(0.3, 1.5, "under", [3, 7, 0, 10]) == "small_low"
    # ... but a centre's 0.1 threes at 0.5 with one three-game in 10 is a real low projection: still graded
    assert board_view.sanity_check(0.1, 0.5, "under", [1, 9, 0, 10]) is None
    assert board_view.sanity_check(0.3, 1.5, "under", [2, 8, 0, 10]) is None
    assert board_view.sanity_check(0.3, 1.5, "under", [2, 2, 0, 4]) is None               # 4 games: too few to judge
    assert board_view.sanity_check(0.5, 1.5, "under", HUNTER_L10) is None                 # 33% of the line: no hold


def test_quote_x_hunter_row_is_held_without_a_letter():
    x, code = board_view.quote_x("nba", "points", 0.0, 1.0, 0.05, 1.5, None, HUNTER_L10, [0, 0])
    assert code == "floor" and x == {"b": "floor", "t": board_view.SANITY_TEXT["floor"], "v": "sg-2"}


def test_sane_rows_keep_their_chance_and_letter():
    """Holds only: a row no rule holds carries exactly the sg-1 signal (same chance, same letter)."""
    cases = [(0.62, 0.38, 25.0, 20.5, [6, 4, 0, 10]), (0.30, 0.70, 0.4, 1.5, [1, 9, 0, 10]),
             (0.93, 0.07, 3.5, 0.5, [9, 1, 0, 10]), (0.41, 0.59, 0.1, 0.5, [1, 9, 0, 10])]
    for po, pu, mu, line, l10 in cases:
        x, code = board_view.quote_x("nba", "points", po, pu, mu, line, None, l10, raw=(po, pu),
                                     view={"played": 40, "to10": "2026-11-01T00:00:00Z"}, now="2026-11-05T00:00:00Z")
        assert code is None and x["v"] == "cf-2"
        side = po if x["ln"] == "over" else pu
        assert x["pl"] == round(side, 4) and x["gr"] == board_view.research_grade(side)


# ------------------------------------------------------------------------------------------------ the snapshot
def _row(quotes, stat="points"):
    return {"key": f"nba:espn:9|p1|{stat}|full_game", "event_id": "nba:espn:9", "player_id": "p1", "player": "P",
            "stat": stat, "quotes": quotes, "kalshi": [], "alternates": []}


def test_guard_row_reads_played_and_dates_from_the_view():
    fcs = {"a": _fc("a", 0.62, 0.38, 25.0, 20.5), "h": _fc("h", 0.0, 1.0, 0.05, 1.5)}
    row = _row([{"platform": "prizepicks", "record_id": "r1", "line": 20.5, "forecast_id": "a"},
                {"platform": "underdog", "record_id": "r2", "line": 1.5, "forecast_id": "h"}])
    now = "2026-11-05T05:00:00Z"
    thin = {"lines": {"20.5": [2, 0, 0, 2, 2, 0, 0, 2], "1.5": [1, 1, 0, 2, 1, 1, 0, 2]}, "played": 2,
            "to10": "2026-11-01T00:00:00Z"}
    log = snapshots.guard_row("nba", row, fcs, thin, now)
    assert [q["x"]["b"] for q in row["quotes"]] == ["thin", "floor"]
    assert [e["reason"] for e in log] == ["thin", "floor"] and log[0]["rule"] == "sg-2"
    stale = {"lines": {"20.5": [6, 4, 0, 10, 3, 2, 0, 5]}, "played": 30, "to10": "2025-09-01T00:00:00Z"}
    snapshots.guard_row("nba", row, fcs, stale, now)
    assert row["quotes"][0]["x"]["b"] == "stale"
    # history not read (view None): only the input-only rules run, the sane row keeps its letter
    log = snapshots.guard_row("nba", row, fcs, None, now)
    assert row["quotes"][0]["x"]["v"] == "cf-2" and row["quotes"][0]["x"]["gr"] == "A"
    assert [e["reason"] for e in log] == ["floor"]


def _build(tmp_path, history):
    from sports import fair, store
    from sports.contracts import Player, Team
    from sports.tests import helpers as h
    from sports.tests.test_ui_snapshots import KAT, NOW
    con = store.connect()
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())
    store.put(con, Player(player_id=KAT, league="nba", name="Karl-Anthony Towns", observed_at=h.T0, known_at=h.T0))
    rid = store.put(con, h.offer())["record_id"]                                   # PrizePicks points 20.5
    a, b = fair.shape_params("basketball", "points", "normal")
    o, u, p = fair.dist_probs("normal", a, b, 24.0, 20.5)
    store.put(con, h.forecast(line=20.5, offer_snapshot_ids=(rid,), model_version="sm-1", projection_mean=24.0,
                              distribution=(("family", "normal"), ("mean", 24.0), ("var_a", a), ("var_b", b)),
                              p_over=o, p_under=u, p_push=p, p_over_given_no_push=o / (o + u),
                              p_under_given_no_push=u / (o + u), chosen_side="over"))
    base = tmp_path / ("h" if history else "n")
    snapshots.build(con, "nba", now=NOW, base=base, history=history, results=False)
    con.close()
    doc = json.loads((base / "nba" / "board.json").read_text(encoding="utf-8"))
    return [q.get("x") for m in doc["markets"] for q in m["q"] if q.get("f")], base / "nba"


def test_snapshot_thin_hold_only_with_history_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SPORTS_CAL_MI", "0")
    xs, _ = _build(tmp_path, history=False)                       # history not read: never a thin hold
    assert xs and all((x or {}).get("b") != "thin" for x in xs)
    xs, root = _build(tmp_path, history=True)                     # read, and the player has no game: held
    assert xs and all((x or {}).get("b") == "thin" for x in xs)
    keys = json.loads((root / "sanity.json").read_text(encoding="utf-8"))["keys"]
    assert keys and all(k[2] == "thin" for k in keys)


def test_sanity_json_keys_are_never_truncated_and_feed_the_slip_pool(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "SANITY_MAX", 1)
    ent = [{"record_id": "r1", "forecast_id": "f1", "reason": "floor"},
           {"record_id": "r2", "forecast_id": "f2", "reason": "thin"}]
    snapshots.write_sanity(tmp_path / "nfl", ent, {"league": "nfl"}, [])
    d = json.loads((tmp_path / "nfl" / "sanity.json").read_text())
    assert len(d["entries"]) == 1 and d["truncated"] == 1
    assert d["keys"] == [["r1", "f1", "floor"], ["r2", "f2", "thin"]]
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path)
    assert pool._sanity_held("nfl") == {("r1", "f1"): "floor", ("r2", "f2"): "thin"}


def test_held_prop_is_never_a_slip_leg():
    f = SimpleNamespace(model_version="sm-1", stat="points", projection_mean=20.0, line=18.5, chosen_side="over",
                        forecast_id="f2", p_over=0.6, p_under=0.4)
    assert pool.guard_reason(f, "nba") is None
    assert pool.guard_reason(f, "nba", "r2", {("r2", "f2"): "thin"}) == \
        "held by the sanity guard: " + board_view.SANITY_TEXT["thin"][6:]
    assert pool.guard_reason(f, "nba", "r2", {("r2", "f2")}).endswith("contradict the lean")      # old set form
    f.projection_mean, f.line, f.chosen_side, f.p_over, f.p_under = 0.05, 1.5, "under", 0.0, 1.0
    assert pool.guard_reason(f, "nba").endswith(board_view.SANITY_TEXT["floor"][6:])
    f.projection_mean, f.p_over, f.p_under = 0.6, 0.2, 0.8
    assert pool.guard_reason(f, "nba", chances=(0.0005, 0.9995)).endswith(board_view.SANITY_TEXT["certain"][6:])


def test_tracked_pick_never_records_a_letter_for_a_held_quote(tmp_path, monkeypatch):
    fc = SimpleNamespace(p_over=0.0, p_under=1.0, model_version="sm-1", line=1.5, projection_mean=0.05, stat="points",
                         forecast_id="h")
    assert picks.research_grade(fc, "under", "nba") is None                               # floor
    fc.projection_mean, fc.p_over, fc.p_under = 20.0, 0.62, 0.38
    fc.line = 18.5
    assert picks.research_grade(fc, "over", "nba") == "A"
    (tmp_path / "nba").mkdir()
    snapshots.write_sanity(tmp_path / "nba", [{"record_id": "r9", "forecast_id": "h", "reason": "thin"}],
                           {"league": "nba"}, [])
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path)
    picks._HELD.clear()
    assert picks.held_quote("nba", "r9", "h") == "thin"
    assert picks.research_grade(fc, "over", "nba", rid="r9") is None
    assert picks.research_grade(fc, "over", "nba", rid="r8") == "A"
