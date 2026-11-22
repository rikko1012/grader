"""audit_1121 round 2 (track nba-core): mvlean-1 (NBA_MVLEAN1), os-2 PrizePicks one-sided lines (SPORTS_PP_ONESIDED),
sports-gp-1 side choice (SPORTS_GP_SIDE_FIX), latest offered line in the record (SPORTS_LATEST_LINE) and the display-only
featured best prop (NBA_BEST_PROP). Every name, line and probability is a TEST FIXTURE."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from sports import capabilities, grade_rules, results_public
from sports.contracts import SideQuote
from sports.model import grade_policy
from sports.tests import helpers as h

AUDIT = Path("C:/Users/tikic/cs2_scout_ops/logs/audit_1121")
OVER_ONLY = (SideQuote("over", True), SideQuote("under", False))


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    for k in ("NBA_MVLEAN1", "SPORTS_PP_ONESIDED", "SPORTS_GP_SIDE_FIX", "SPORTS_LATEST_LINE", "NBA_BEST_PROP",
              "NBA_MVLEAN1_FROM", "SPORTS_LATEST_LINE_NFL", "SPORTS_PP_SIDE_HOLD", "SPORTS_PP_ONESIDED_FROM"):
        monkeypatch.delenv(k, raising=False)


def _mv_on(monkeypatch, start="2026-11-01T00:00:00Z"):
    monkeypatch.setenv("NBA_MVLEAN1", "1")
    monkeypatch.setenv("NBA_MVLEAN1_FROM", start)


def test_round2_defaults_only_the_side_hold_is_on():
    """Round-2 review: only the verified display bug fix (SPORTS_PP_SIDE_HOLD) defaults ON; every untested rule is off."""
    assert grade_rules.pp_side_hold_on()
    assert not grade_rules.pp_onesided_on() and not grade_rules.side_fix_on() and not grade_rules.best_prop_on("nba")
    assert not grade_rules.mvlean_on("nba") and not grade_rules.latest_line_on("nba")
    assert grade_rules.mvlean_from() is None                       # never relabel unless set
    assert not grade_rules.pp_onesided_record_drop("2026-11-21T00:00:00Z")


# ------------------------------------------------------------------------------------------------ mvlean-1
def test_mvlean_matches_the_frozen_rule_on_every_audit_row():
    """Parity with the frozen F03_mvlean1 rule() on every train + holdout P1 row (skipped off the ops machine)."""
    frozen = AUDIT / "frozen" / "nba" / "F03_mvlean1.py"
    if not frozen.exists():
        pytest.skip("audit evidence not on this machine")
    spec = importlib.util.spec_from_file_location("f03_mvlean1", frozen)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    n = mism = demoted = 0
    for name in ("train.csv", "holdout.csv"):
        with open(AUDIT / "data" / "nba" / name, encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["group"] != "P1":
                    continue
                n += 1
                fr = mod.rule(row)
                frozen_lean = fr["mv"] is not None and fr["mv"] > mod.THRESHOLD
                ok_time = bool(row["line_open_feed"]) and bool(row["line_open_feed_at"]) and \
                    mod._ts(row["line_open_feed_at"]) <= mod._ts(row["forecast_at"])
                mine = ok_time and grade_rules.mvlean_demotes(row["side"].lower(), row["line"], row["line_open_feed"])
                mism += mine != frozen_lean
                demoted += mine
    assert n > 20000 and demoted > 0 and mism == 0
    assert grade_rules.MV_THRESHOLD == mod.THRESHOLD


class _Fc:
    def __init__(self, fid, at, line, platform="prizepicks", model="sm-1", scope="full_game", stat="points"):
        self.forecast_id, self.forecast_at, self.line, self.model_version, self.scope = fid, at, line, model, scope
        self.event_id, self.player_id, self.stat = "nba:espn:1", "nba:player:espn:9", stat
        self.offer_snapshot_ids = (f"offer:{platform}:{platform[:2]}:1:r1:abc",)


def _row():
    return {"event_id": "nba:espn:1", "player_id": "nba:player:espn:9", "stat": "points"}


def test_open_line_is_the_first_sm1_forecast_per_platform_across_offers():
    o = grade_rules.OpenLines()
    o.add(_Fc("f2", "2026-11-20T15:00:00Z", 21.5))
    o.add(_Fc("f1", "2026-11-20T12:00:00Z", 20.5))
    o.add(_Fc("f0", "2026-11-20T09:00:00Z", 19.5, model="mi-1"))       # not sm-1
    o.add(_Fc("f9", "2026-11-20T08:00:00Z", 18.5, platform="fanduel"))   # not a pick'em book
    o.add(_Fc("fq", "2026-11-20T08:00:00Z", 9.5, scope="first_half"))
    o.add(_Fc("u1", "2026-11-20T13:00:00Z", 22.5, platform="underdog"))
    assert o.get("nba:espn:1", "nba:player:espn:9", "points", "prizepicks") == (20.5, "2026-11-20T12:00:00Z")
    assert o.get("nba:espn:1", "nba:player:espn:9", "points", "underdog")[0] == 22.5
    assert o.get("nba:espn:1", "nba:player:espn:9", "points", "fanduel") is None
    assert grade_rules.platform_of_record("offer:prizepicks:pp:12:r3:ab") == "prizepicks"


def test_mark_quote_demotes_a_letter_when_the_line_moved_toward_the_side(monkeypatch):
    o = grade_rules.OpenLines()
    o.add(_Fc("f1", "2026-11-20T12:00:00Z", 20.5))
    q = {"platform": "prizepicks", "line": 21.5}
    at = "2026-11-20T18:00:00Z"
    x = {"ln": "over", "gr": "A", "pl": 0.62}
    assert grade_rules.mark_quote("nba", _row(), q, x, o, at) is None and x["gr"] == "A"   # default OFF
    monkeypatch.setenv("NBA_MVLEAN1", "1")                      # on but NBA_MVLEAN1_FROM unset: never applies
    assert grade_rules.mark_quote("nba", _row(), q, x, o, at) is None and x["gr"] == "A"
    _mv_on(monkeypatch)
    assert grade_rules.mark_quote("nba", _row(), q, x, o, at) == 1.0   # +1.0 toward Over -> Lean, side kept
    assert x["gr"] == "Lean" and x["ln"] == "over" and x["gr0"] == "A" and x["mr"] == "mvlean-1"
    assert x["mw"] == grade_rules.MV_WHY                         # the tooltip's reason
    x = {"ln": "over", "gr": "A"}                               # forecast before FROM: unchanged (= the record)
    assert grade_rules.mark_quote("nba", _row(), q, x, o, "2026-10-31T00:00:00Z") is None and x["gr"] == "A"
    x = {"ln": "over", "gr": "Lean"}                            # already Lean: not counted as a demotion
    assert grade_rules.mark_quote("nba", _row(), q, x, o, at) is None and "mr" not in x
    x = {"ln": "under", "gr": "A"}                              # moved away from Under: unchanged
    assert grade_rules.mark_quote("nba", _row(), q, x, o, at) is None and x["gr"] == "A"
    q2 = {"platform": "prizepicks", "line": 21.0}               # exactly 0.5 (not > 0.75): unchanged
    x = {"ln": "over", "gr": "B"}
    assert grade_rules.mark_quote("nba", _row(), q2, x, o, at) is None and x["gr"] == "B"
    x = {"ln": "over", "gr": "A"}
    assert grade_rules.mark_quote("nfl", _row(), q, x, o, at) is None and x["gr"] == "A"     # NBA only
    monkeypatch.setenv("NBA_MVLEAN1", "0")
    x = {"ln": "over", "gr": "A"}
    assert grade_rules.mark_quote("nba", _row(), q, x, o, at) is None and x["gr"] == "A"     # rollback
    for bad in ("never", "", "garbage"):
        monkeypatch.setenv("NBA_MVLEAN1", "1")
        monkeypatch.setenv("NBA_MVLEAN1_FROM", bad)
        assert grade_rules.mvlean_from() is None and not grade_rules.mvlean_active("nba", at)


# ------------------------------------------------------------------------------------------------ os-2
def test_prizepicks_line_listing_one_side_is_one_sided_and_never_graded(monkeypatch):
    one = h.offer(sides=OVER_ONLY)
    assert capabilities.variant_of(one) == "standard" and capabilities.gate(one)[0]       # default OFF (shadow)
    monkeypatch.setenv("SPORTS_PP_ONESIDED", "1")
    assert capabilities.variant_of(one) == "one_sided" and capabilities.is_one_sided(one)
    assert capabilities.only_side(one) == "over"
    ok, why = capabilities.gate(one)
    assert not ok and capabilities.ONE_SIDED_REASON in why
    assert capabilities.gate(h.offer()) == (True, [])                       # two-sided standard: unchanged
    view = {"platform": "prizepicks", "variant": "standard", "sides": [{"side": "over", "offered": True},
                                                                       {"side": "under", "offered": False}]}
    assert capabilities.is_one_sided(view)
    monkeypatch.setenv("SPORTS_PP_ONESIDED", "0")
    assert capabilities.variant_of(one) == "standard" and capabilities.gate(one)[0]       # rollback


def test_side_hold_never_letters_a_side_the_quote_does_not_offer(monkeypatch):
    q = {"platform": "prizepicks", "sides": [{"side": "over", "offered": True}, {"side": "under", "offered": False}]}
    hold = grade_rules.side_hold(q, {"ln": "under", "gr": "B"})            # default ON (SPORTS_PP_SIDE_HOLD)
    assert hold["b"] == "side" and hold["t"] == grade_rules.HOLD_TEXT      # never claims the line is not graded
    assert grade_rules.side_hold(q, {"ln": "over", "gr": "A"}) is None     # the LISTED side keeps its letter
    assert grade_rules.side_hold({"platform": "prizepicks", "o": [1, 0]}, {"ln": "over", "gr": "B"}) is None
    assert grade_rules.side_hold({"platform": "prizepicks", "o": [1, 0]}, {"ln": "under", "gr": "B"})["b"] == "side"
    assert grade_rules.side_hold({"platform": "prizepicks", "o": [1, 1]}, {"ln": "under", "gr": "B"}) is None
    monkeypatch.setenv("SPORTS_PP_ONESIDED", "1")                          # with the reclassification: "Over only"
    assert "Over only" in grade_rules.side_hold(q, {"ln": "under", "gr": "B"})["t"]
    monkeypatch.setenv("SPORTS_PP_SIDE_HOLD", "0")                         # rollback
    assert grade_rules.side_hold(q, {"ln": "under", "gr": "B"}) is None


# ------------------------------------------------------------------------------------------------ gp-1 sides
def test_offer_forced_side_the_model_disfavours_gets_no_side_letter_or_lean(monkeypatch):
    assert grade_policy.apply(0.42, 0.58, ("over",), "forward-validated")["chosen_side"] == "over"   # default OFF
    monkeypatch.setenv("SPORTS_GP_SIDE_FIX", "1")
    r = grade_policy.apply(0.42, 0.58, ("over",), "forward-validated")
    assert r["chosen_side"] is None and r["grade"] is None and r["band"] is None and "Lean" not in r["label"]
    r = grade_policy.apply(0.62, 0.38, ("over",), "forward-validated")          # offered side favoured: graded
    assert r["chosen_side"] == "over" and r["grade"] == "A"


def test_offer_forced_check_compares_the_two_sides_not_one_half_on_integer_lines(monkeypatch):
    monkeypatch.setenv("SPORTS_GP_SIDE_FIX", "1")
    # integer line: push mass 0.16 leaves the favoured Over at 0.46 (< 0.5) - it is still the model's side
    r = grade_policy.apply(0.46, 0.38, ("over",), "forward-validated")
    assert r["chosen_side"] == "over"
    r = grade_policy.apply(0.38, 0.46, ("over",), "forward-validated")      # Over offered, Under favoured: no side
    assert r["chosen_side"] is None and r["grade"] is None


def test_exact_tie_goes_to_the_projection_mean_not_over(monkeypatch):
    assert grade_policy.apply(0.5, 0.5, ("over", "under"), mean=19.8, line=20.5)["chosen_side"] == "over"  # default
    monkeypatch.setenv("SPORTS_GP_SIDE_FIX", "1")
    assert grade_policy.apply(0.5, 0.5, ("over", "under"), mean=19.8, line=20.5)["chosen_side"] == "under"
    assert grade_policy.apply(0.5, 0.5, ("over", "under"), mean=21.0, line=20.5)["chosen_side"] == "over"
    assert grade_policy.apply(0.5, 0.5, ("over", "under"))["chosen_side"] is None
    monkeypatch.setenv("SPORTS_GP_SIDE_FIX", "0")
    assert grade_policy.apply(0.5, 0.5, ("over", "under"), mean=19.8, line=20.5)["chosen_side"] == "over"
    assert grade_policy.apply(0.42, 0.58, ("over",))["chosen_side"] == "over"


# ------------------------------------------------------------------------------------------------ latest line
def _rec(fid, line, at, plat="prizepicks", lean="over", outcome="over", p=0.6, mean=21.0, model="sm-1"):
    return {"forecast_id": fid, "model": model, "event_id": "nba:espn:1", "player_id": "p1", "stat": "points",
            "line": line, "mean": mean, "p_over": p if lean == "over" else 1 - p, "p_under": 1 - p if lean == "over" else p,
            "p_push": 0.0, "research_only": True, "forecast_at": at, "actual": 25.0, "platforms": [plat],
            "settled_at": "2026-11-21T05:00:00Z", "status": "settled", "outcome": outcome, "error": 1.0, "lean": lean,
            "lean_result": "win" if outcome == lean else "loss", "lean_p": p}


def test_record_uses_the_books_latest_line_not_a_superseded_one(monkeypatch):
    assert not grade_rules.latest_line_on("nba")               # default off (not proven at the record's own rule)
    monkeypatch.setenv("SPORTS_LATEST_LINE", "1")
    rows = [_rec("old", 20.5, "2026-11-20T12:00:00Z", mean=20.6),     # closest to the projection, but superseded
            _rec("new", 22.5, "2026-11-20T18:00:00Z", mean=21.0)]
    assert [r["forecast_id"] for r in results_public.representative(rows)] == ["new"]
    monkeypatch.setenv("SPORTS_LATEST_LINE", "0")
    assert [r["forecast_id"] for r in results_public.representative(rows)] == ["old"]


def test_latest_line_is_nba_only_unless_the_nfl_switch_is_set(monkeypatch):
    """NFL audit record: no gain from the latest-line cut, so it stays off there unless SPORTS_LATEST_LINE_NFL=1."""
    rows = [dict(r, event_id="nfl:espn:1") for r in (_rec("old", 20.5, "2026-11-20T12:00:00Z", mean=20.6),
                                                      _rec("new", 22.5, "2026-11-20T18:00:00Z", mean=21.0))]
    monkeypatch.setenv("SPORTS_LATEST_LINE", "1")
    assert grade_rules.latest_line_on("nba") and not grade_rules.latest_line_on("nfl")
    assert [r["forecast_id"] for r in results_public.representative(rows)] == ["old"]
    monkeypatch.setenv("SPORTS_LATEST_LINE_NFL", "1")
    assert grade_rules.latest_line_on("nfl")
    assert [r["forecast_id"] for r in results_public.representative(rows)] == ["new"]


def test_two_books_keep_their_own_latest_lines(monkeypatch):
    monkeypatch.setenv("SPORTS_LATEST_LINE", "1")
    rows = [_rec("pp", 20.5, "2026-11-20T12:00:00Z", mean=20.6),
            _rec("ud", 22.5, "2026-11-20T18:00:00Z", plat="underdog", mean=21.0)]
    assert [r["forecast_id"] for r in results_public.representative(rows)] == ["pp"]   # pp's 20.5 is still pp's latest


def test_record_grade_table_and_recent_list_count_mvlean_rows_as_lean(monkeypatch):
    rows = [_rec("f1", 20.5, "2026-11-20T12:00:00Z", p=0.7, mean=24.0),
            _rec("f2", 21.5, "2026-11-20T18:00:00Z", p=0.7, mean=24.0)]      # moved +1.0 toward Over since open 20.5
    reps = results_public.representative(rows)
    assert len(reps) == 1 and reps[0]["mv_open"] == 20.5
    g = {x["grade"]: x["n"] for x in results_public.grades(reps, "nba")["rows"]}
    assert g["A"] == 1 and g["Lean"] == 0                                    # default OFF: unchanged
    assert results_public.recent(reps, league="nba")[0]["grade"] == "A"
    _mv_on(monkeypatch)
    g = {x["grade"]: x["n"] for x in results_public.grades(reps, "nba")["rows"]}
    assert g["A"] == 0 and g["Lean"] == 1
    rec = results_public.recent(reps, league="nba")[0]                      # the recent list says Lean too (= board)
    assert rec["grade"] == "Lean" and rec["grade_why"] == grade_rules.MV_WHY and rec["p_side"] >= 0.6


# ------------------------------------------------------------------------------------------------ bp-1
def _q(rid, plat, pl, gr="A", ln="over", o=(1, 1), b=None):
    x = {"ln": ln, "gr": gr, "pl": pl} if b is None else {"b": b}
    return {"record_id": rid, "platform": plat, "line": 20.5, "x": x,
            "sides": [{"side": "over", "offered": bool(o[0])}, {"side": "under", "offered": bool(o[1])}]}


def test_featured_best_prop_is_one_per_player_game_and_display_only(monkeypatch):
    assert grade_rules.feature_best("nba", [{"event_id": "e", "player_id": "p", "quotes": [
        _q("z", "prizepicks", 0.61)]}]) == 0                                # default OFF
    monkeypatch.setenv("NBA_BEST_PROP", "1")
    rows = [{"event_id": "e1", "player_id": "p1", "quotes": [_q("a", "prizepicks", 0.61), _q("b", "underdog", 0.64)]},
            {"event_id": "e1", "player_id": "p1", "quotes": [_q("c", "underdog", 0.66, gr="Lean"),     # Lean: never
                                                             _q("d", "prizepicks", 0.70, b="l10_contra"),  # held
                                                             _q("e", "underdog", 0.69, ln="under", o=(1, 0))]},  # unoffered
            {"event_id": "e1", "player_id": "p2", "quotes": [_q("f", "prizepicks", 0.58, gr="B")]}]
    assert grade_rules.feature_best("nba", rows) == 2
    feat = {q["record_id"] for r in rows for q in r["quotes"] if (q.get("x") or {}).get("bp")}
    assert feat == {"b", "f"}
    assert rows[0]["quotes"][1]["x"]["gr"] == "A"                      # grade untouched
    assert grade_rules.feature_best("nfl", rows) == 0


def test_letter_for_reads_the_boards_marks(tmp_path, monkeypatch):
    lg = tmp_path / "nba"
    lg.mkdir()
    (lg / "grade_marks.json").write_text(json.dumps({"marks": {
        "r1": {"gr": "Lean", "mr": "mvlean-1", "ln": "over"}, "r2": {"b": "side", "ln": "under"},
        "r3": {"gr": "A", "bp": 1, "ln": "over"}}}), encoding="utf-8")
    monkeypatch.setattr("sports.snapshots.snap_root", lambda base=None: tmp_path)
    grade_rules._MARKS.clear()
    assert grade_rules.letter_for("nba", "r1", "over", "A") == "Lean"
    assert grade_rules.letter_for("nba", "r2", "under", "B") is None
    assert grade_rules.letter_for("nba", "r3", "over", "A") == "A" and grade_rules.featured("nba", "r3", "over")
    assert grade_rules.letter_for("nba", "zz", "over", "C") == "C"
    grade_rules._MARKS.clear()


# ------------------------------------------------------------------------------------- end to end (snapshot build)
def _build_world(con, moved_line):
    from sports.contracts import Membership, Player, Team
    from sports import store
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())
    pid = "nba:player:espn:3134907"
    store.put(con, Player(player_id=pid, league="nba", name="Fixture Forward", position="F", status="active",
                          observed_at=h.T0, known_at=h.T0))
    store.put(con, Membership(player_id=pid, team_id=h.NY, valid_from="2026-07-01T00:00:00Z", source="espn",
                              observed_at=h.T0, known_at=h.T0))
    early = "2026-10-23T15:00:00Z"
    r1 = store.put(con, h.offer(line=19.5, observed_at=early))["record_id"]           # the first line seen
    store.put(con, h.forecast(forecast_at=early, offer_snapshot_ids=(r1,), input_snapshot_ids=(r1,), line=19.5,
                              model_version="sm-1", p_over=0.66, p_under=0.34, p_push=0.0, p_over_given_no_push=0.66,
                              p_under_given_no_push=0.34, projection_mean=23.0))
    r2 = store.put(con, h.offer(line=moved_line))["record_id"]                       # the book moved its line
    store.put(con, h.forecast(offer_snapshot_ids=(r2,), input_snapshot_ids=(r2,), line=moved_line,
                              model_version="sm-1", p_over=0.62, p_under=0.38, p_push=0.0, p_over_given_no_push=0.62,
                              p_under_given_no_push=0.38, projection_mean=23.0))
    # a PrizePicks line listing Over only (os-2): shown, never lettered
    r3 = store.put(con, h.offer(oid="pp:2", stat="rebounds", line=8.5,
                                sides=OVER_ONLY))["record_id"]
    return r2, r3


def _snap_quotes(con, tmp_path):
    from sports import snapshots
    man = snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path, history=False, results=False)
    root = snapshots.snap_root(tmp_path) / "nba"
    pf = json.loads((root / "players" / f"{snapshots.safe_name('nba:player:espn:3134907')}.json")
                    .read_text(encoding="utf-8"))
    marks = json.loads((root / "grade_marks.json").read_text(encoding="utf-8"))["marks"]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return {q["record_id"]: q for r in pf["markets"] for q in r["quotes"]}, marks, manifest


@pytest.mark.parametrize("moved_line, demoted", [(20.5, True), (19.5, False)])
def test_snapshot_build_applies_mvlean_end_to_end(tmp_path, moved_line, demoted, monkeypatch):
    from sports import store
    _mv_on(monkeypatch, "2026-10-01T00:00:00Z")
    monkeypatch.setenv("NBA_BEST_PROP", "1")
    con = store.connect()
    r2, r3 = _build_world(con, moved_line)
    qs, marks, manifest = _snap_quotes(con, tmp_path)
    x = qs[r2]["x"]
    assert x.get("ln") == "over"
    if demoted:                                         # +1.0 toward Over since the first line: Lean, side kept
        assert x["gr"] == "Lean" and x["gr0"] == "A" and x["mr"] == "mvlean-1" and x["mo"] == 19.5
        assert marks[r2]["gr"] == "Lean" and marks[r2]["mr"] == "mvlean-1"
        assert not x.get("bp")                          # a Lean is never the featured best prop
    else:
        assert x["gr"] == "A" and "mr" not in x
        assert x.get("bp") == 1 and marks[r2]["bp"] == 1   # the only lettered line: featured (display only)
    assert manifest["grade_rules"]["mvlean"]["on"] and manifest["grade_rules"]["mvlean"]["open_keys"] >= 1
    q3 = qs.get(r3)
    assert q3 is None or not (q3.get("x") or {}).get("gr")      # Over-only PrizePicks line: never a letter
    con.close()


def test_board_file_and_workspace_record_carry_the_featured_mark(tmp_path, monkeypatch):
    monkeypatch.setenv("NBA_BEST_PROP", "1")
    import workspace_sports
    from sports import snapshots, store
    con = store.connect()
    r2, _ = _build_world(con, 19.5)
    snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path, history=False, results=False)
    board = json.loads((snapshots.snap_root(tmp_path) / "nba" / "board.json").read_text(encoding="utf-8"))
    hit = [(m, q) for m in board["markets"] for q in m.get("q") or () if q.get("r") == r2]
    assert hit, "quote not on the compact board"
    m, q = hit[0]
    assert (q.get("x") or {}).get("bp") == 1
    rec, _ = workspace_sports.record_of("nba", board, m, q, {})
    assert rec["scout"]["grade"] == "A" and rec["scout"]["featured"] is True
    con.close()


def test_side_hold_leaves_a_suspended_underdog_side_alone():
    q = {"platform": "underdog", "sides": [{"side": "over", "offered": True}, {"side": "under", "offered": False}]}
    assert grade_rules.side_hold(q, {"ln": "under", "gr": "B"}) is None          # cap-2: suspension never regrades


def test_forecast_run_records_no_side_for_a_lone_disfavoured_side_and_an_even_tie(monkeypatch):
    monkeypatch.setenv("SPORTS_GP_SIDE_FIX", "1")
    import numpy as np
    from sports.model import forward
    pmf = np.full(10, 0.1)                                   # 15..24 uniform
    ud_over_only = h.offer(platform="underdog", oid="ud:1", line=20.5, sides=OVER_ONLY)   # Under suspended right now
    fc, _ = forward.build_forecast(ud_over_only, "offer:underdog:ud:1:r1:ab", h.T0, "sm-1", None, pmf, 15, 19.5,
                                ("offer:underdog:ud:1:r1:ab",), (), None, None)
    assert fc.p_over < fc.p_under and fc.chosen_side is None and fc.grade is None
    two = h.offer(platform="underdog", oid="ud:2", line=19.5)
    tie, _ = forward.build_forecast(two, "offer:underdog:ud:2:r1:ab", h.T0, "sm-1", None, pmf, 15, 19.5,
                                 ("offer:underdog:ud:2:r1:ab",), (), None, None)
    assert tie.p_over == tie.p_under and tie.chosen_side is None                # mean == line: no side
    up, _ = forward.build_forecast(two, "offer:underdog:ud:2:r1:ab", h.T0, "sm-1", None, pmf, 15, 20.0,
                                ("offer:underdog:ud:2:r1:ab",), (), None, None)
    assert up.chosen_side == "over"                                              # mean above the line


def test_capture_listing_names_the_lines_type_and_listed_sides():
    from gradecore.capture import _listing
    assert _listing(h.offer(sides=OVER_ONLY)) == {"platform": "prizepicks", "variant": "standard",
                                                  "sides_offered": ["over"]}
    assert _listing(h.offer())["sides_offered"] == ["over", "under"]


def test_record_relabels_mvlean_only_from_the_effective_time(monkeypatch):
    rows = [_rec("f1", 20.5, "2026-11-20T12:00:00Z", p=0.7, mean=24.0),
            _rec("f2", 21.5, "2026-11-20T18:00:00Z", p=0.7, mean=24.0)]
    reps = results_public.representative(rows)
    monkeypatch.setenv("NBA_MVLEAN1", "1")                                # on, FROM unset: never relabels
    assert {x["grade"]: x["n"] for x in results_public.grades(reps, "nba")["rows"]}["A"] == 1
    _mv_on(monkeypatch, "2026-11-21T00:00:00Z")                           # forecast before the rule went live
    g = {x["grade"]: x["n"] for x in results_public.grades(reps, "nba")["rows"]}
    assert g["A"] == 1 and g["Lean"] == 0
    assert results_public.recent(reps, league="nba")[0]["grade"] == "A"
    _mv_on(monkeypatch, "2026-11-20T18:00:00+00:00")                      # exactly at FROM: relabelled
    assert results_public.recent(reps, league="nba")[0]["grade"] == "Lean"


# ------------------------------------------------------------------- os-2 defaults: the Spencer / Acuff case
def _onesided_world(con):
    """Three PrizePicks Over-only lines with sm-1 forecasts: Over favoured A (Cam Spencer A Over case), Over favoured B
    (Darius Acuff Jr. B Over case) and Under favoured B on the unlisted Under (De'Aaron Fox 6.0 Under B case)."""
    from sports.contracts import Membership, Player, Team
    from sports import store
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())
    pid = "nba:player:espn:3134907"
    store.put(con, Player(player_id=pid, league="nba", name="Fixture Forward", position="F", status="active",
                          observed_at=h.T0, known_at=h.T0))
    store.put(con, Membership(player_id=pid, team_id=h.NY, valid_from="2026-07-01T00:00:00Z", source="espn",
                              observed_at=h.T0, known_at=h.T0))
    out = {}
    for name, oid, st, line, po in (("a_over", "pp:11", "assists", 4.5, 0.64), ("b_over", "pp:12", "rebounds", 6.5, 0.57),
                                    ("b_under", "pp:13", "points", 20.5, 0.42)):
        rid = store.put(con, h.offer(oid=oid, stat=st, line=line, sides=OVER_ONLY))["record_id"]
        store.put(con, h.forecast(offer_snapshot_ids=(rid,), input_snapshot_ids=(rid,), line=line, stat=st,
                                  model_version="sm-1", p_over=po, p_under=1 - po, p_push=0.0,
                                  p_over_given_no_push=po, p_under_given_no_push=1 - po, sides_available=("over",),
                                  projection_mean=line + (1.5 if po > 0.5 else -1.5)))
        out[name] = rid
    return out


def _player_quotes(con, tmp_path):
    from sports import snapshots
    snapshots.build(con, "nba", now="2026-10-23T21:00:00Z", base=tmp_path, history=False, results=False)
    root = snapshots.snap_root(tmp_path) / "nba"
    pf = json.loads((root / "players" / f"{snapshots.safe_name('nba:player:espn:3134907')}.json")
                    .read_text(encoding="utf-8"))
    return {q["record_id"]: q for r in pf["markets"] for q in r["quotes"]}


def test_defaults_hold_only_the_unlisted_side_and_keep_the_listed_sides_letters(tmp_path, monkeypatch):
    """Defaults (side hold on, reclassification off): the Over-only lines' favoured Overs keep A / B (Spencer / Acuff
    case); the Under-favoured line loses only its letter on the unlisted Under (Fox case). With SPORTS_PP_SIDE_HOLD=0
    the build equals main's (the Under keeps its letter); with SPORTS_PP_ONESIDED=1 every one-sided line is unlettered."""
    from sports import store
    con = store.connect()
    rid = _onesided_world(con)
    qs = _player_quotes(con, tmp_path)
    assert qs[rid["a_over"]]["x"]["ln"] == "over" and qs[rid["a_over"]]["x"]["gr"] == "A"
    assert qs[rid["b_over"]]["x"]["ln"] == "over" and qs[rid["b_over"]]["x"]["gr"] == "B"
    held = qs[rid["b_under"]]["x"]
    assert held["b"] == "side" and held["t"] == grade_rules.HOLD_TEXT and "gr" not in held
    monkeypatch.setenv("SPORTS_PP_SIDE_HOLD", "0")                         # rollback = main's display
    qs0 = _player_quotes(con, tmp_path / "off")
    assert qs0[rid["b_under"]]["x"]["ln"] == "under" and qs0[rid["b_under"]]["x"]["gr"] == "B"
    assert qs0[rid["a_over"]]["x"]["gr"] == "A" and qs0[rid["b_over"]]["x"]["gr"] == "B"
    monkeypatch.delenv("SPORTS_PP_SIDE_HOLD")
    monkeypatch.setenv("SPORTS_PP_ONESIDED", "1")                          # the owner-call reclassification
    qs1 = _player_quotes(con, tmp_path / "on")
    for k in rid.values():
        assert not (qs1.get(k, {}).get("x") or {}).get("gr")
    con.close()


def test_record_drop_of_prizepicks_one_sided_forecasts_needs_the_flag_and_a_from_date(monkeypatch):
    from sports import snapshots, store
    pp = h.offer(sides=OVER_ONLY)
    ud = h.offer(platform="underdog", oid="ud:1", sides=OVER_ONLY, variant="one_sided")
    offers = {"offer:prizepicks:pp:1:r1:a": pp, "offer:underdog:ud:1:r1:a": ud}
    monkeypatch.setattr(store, "get", lambda con, rid: offers.get(rid))

    def fc(rid, at="2026-11-20T12:00:00Z"):
        return h.forecast(forecast_at=at, offer_snapshot_ids=(rid,), sides_available=("over",))
    pp_fc, ud_fc = fc("offer:prizepicks:pp:1:r1:a"), fc("offer:underdog:ud:1:r1:a")
    assert snapshots.one_sided_forecast(None, ud_fc)                       # Underdog cap-2: main's rule, unchanged
    assert not snapshots.one_sided_forecast(None, pp_fc)                   # default: the record keeps it
    monkeypatch.setenv("SPORTS_PP_ONESIDED", "1")
    assert not snapshots.one_sided_forecast(None, pp_fc)                   # on, no FROM: never rewrites history
    monkeypatch.setenv("SPORTS_PP_ONESIDED_FROM", "2026-11-21T00:00:00Z")
    assert not snapshots.one_sided_forecast(None, pp_fc)                   # saved before FROM: kept
    assert snapshots.one_sided_forecast(None, fc("offer:prizepicks:pp:1:r1:a", "2026-11-21T06:00:00Z"))
