"""2026-10-14 evaluation-pipeline repairs: the bounded forecast export, the settlement-detail fault that paused learning
(7 Fruju vs MARKandLARRY picks: HLTV-confirmed totals with BO3.gg full-scoreboard map detail), and the learning loop
resuming on its own once the fault is fixed."""
import json
import sqlite3
import tracemalloc
from datetime import datetime, timedelta, timezone

import pytest

import learning
import picklog

NOW = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
FSLUG = "fruju-vs-markandlarry-12-10-2026"
# the production record of pick 536 before the fix (trimmed): settled total 29 (= HLTV), detail = BO3.gg FULL scoreboard (30)
PROD_536 = {"source": "bo3", "rounds": [18, 19], "won": [True, True], "per_map": [[15, 8], [15, 4]], "full_checked": True,
            "quick": True, "reverted_full": True, "provisional": True, "discrepancy": {"quick_total": 29.0, "full_total": 30.0},
            "review": True, "full_per_map": [[15, 8], [15, 4]], "hltv_checked": True, "hltv_match": 2399144}
# HLTV's records for yourwombat in match 2399144 (read-only production query 2026-10-14): 15 + 14 = 29
HLTV_2399144 = {"status": "ok", "total": 29, "maps_used": [1, 2], "maps": ["de_inferno", "de_mirage"], "per_map": [[15, 8], [14, 4]],
                "rounds": [18, 19], "won": [True, True], "hltv_match": 2399144}


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "corrections.jsonl")
    monkeypatch.setattr(picklog, "REVIEWS", tmp_path / "reviews.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    monkeypatch.setattr(picklog, "_RECHECK", {}, raising=False)
    return tmp_path


def _pick(pid, result, line=29.5, actual=29, hit=1, test=2):
    con = picklog._db()
    con.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, status, "
                "actual, hit, result, settled_at, test, on_board, units, logged_at, book, market_id, line_type) VALUES "
                "(?, 'yourwombat', 14956, 'Fruju', 'MARKandLARRY', ?, '2026-10-12T09:00:00Z', 'kills', '12', ?, 'Less', 'settled', ?, ?, ?, "
                "'2026-10-12T11:33:45+00:00', ?, 1, 1.0, '2026-10-12T06:00:00+00:00', 'underdog', 'm1', 'standard')",
                (pid, FSLUG, line, actual, hit, json.dumps(result), test))
    con.commit()
    con.close()


# ------------------------------------------------------------------------------------------------ settlement detail
def test_the_production_fault_is_reproduced_and_fixed_from_hltv(db, monkeypatch):
    _pick(536, PROD_536)
    assert [a["issue"] for a in learning.audit(picklog.export())] == ["stored map detail gives 30, the settled total is 29.0"]
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV_2399144))
    out = picklog.recheck_bo3_with_hltv(now=NOW)
    assert out["confirmed"] == 1 and not out["corrected"] and [d["id"] for d in out["detail_fixed"]] == [536]
    p = picklog.export()[0]
    res = json.loads(p["result"])
    assert (p["status"], p["actual"], p["hit"]) == ("settled", 29, 1)              # the settlement itself is unchanged
    assert res["source"] == "hltv" and res["per_map"] == [[15, 8], [14, 4]] and res["review"] is False and res["provisional"] is False
    assert res["bo3_detail"]["per_map"] == [[15, 8], [15, 4]]                       # the BO3.gg record is kept, nested
    assert learning.audit(picklog.export()) == []
    log = [json.loads(x) for x in (db / "corrections.jsonl").read_text().splitlines()]
    assert len(log) == 1 and log[0]["detail_old"]["detail_total"] == 30 and log[0]["detail_new"]["detail_total"] == 29
    assert log[0]["old"] == log[0]["new"]                                           # detail-only: nothing pending for learning
    assert learning.pending_corrections(picklog.export(), log) == []
    again = picklog.recheck_bo3_with_hltv(now=NOW)                                   # idempotent: never due again
    assert again["checked"] == 0 and len((db / "corrections.jsonl").read_text().splitlines()) == 1


def test_confirmed_rows_with_consistent_detail_are_not_requeued(db, monkeypatch):
    _pick(1, dict(PROD_536, per_map=[[15, 8], [14, 4]], review=False, reverted_full=False))      # confirmed, detail = total
    _pick(2, {"source": "bo3", "per_map": [[15, 8], [14, 4]], "rounds": [18, 19], "won": [True, True]})   # never checked
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV_2399144))
    out = picklog.recheck_bo3_with_hltv(now=NOW)
    assert out["checked"] == 1 and out["confirmed"] == 1 and not out.get("detail_fixed") and not (db / "corrections.jsonl").exists()
    res = {p["id"]: json.loads(p["result"]) for p in picklog.export()}
    assert res[1]["source"] == "bo3" and res[2]["source"] == "hltv"                     # only the unchecked row was due


def test_the_revert_never_keeps_the_full_scoreboard_maps_as_the_quick_detail(db):
    _pick(7, {"source": "bo3", "per_map": [[15, 8], [15, 4]], "rounds": [18, 19], "won": [True, True]}, actual=30, hit=0)
    (db / "corrections.jsonl").write_text(json.dumps({"id": 7, "reason": "BO3.gg full scoreboard replaced the quick scoreboard",
                                                       "old": {"actual": 29, "status": "settled", "hit": 1}}) + "\n")
    picklog.revert_full_scoreboard_corrections(now=NOW)
    p = picklog.export()[0]
    res = json.loads(p["result"])
    assert p["actual"] == 29 and res["per_map"] is None and res["full_per_map"] == [[15, 8], [15, 4]]
    assert learning.audit(picklog.export()) == []                                  # no detail, so nothing contradicts the total


def test_hltv_disagreeing_with_the_settlement_is_still_a_logged_correction(db, monkeypatch):
    _pick(2, dict(PROD_536, per_map=[[15, 8], [15, 4]]), actual=30, hit=0)
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV_2399144))
    out = picklog.recheck_bo3_with_hltv(now=NOW)
    assert [c["id"] for c in out["corrected"]] == [2]
    p = picklog.export()[0]
    assert (p["actual"], p["hit"]) == (29, 1) and json.loads(p["result"])["source"] == "hltv"


# ------------------------------------------------------------------------------------------------ learning resumes
def _forecast(pid, fid, logged, p_more=0.40, line=29.5):
    con = picklog._db()
    con.execute("INSERT INTO forecasts (id, pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, lambda, "
                "model_version) VALUES (?, ?, 'first', ?, '2026-10-12T09:00:00Z', ?, 'Less', ?, ?, 'B', 1.0, 28.4, 'V')",
                (fid, pid, logged, line, 1 - p_more, p_more))
    con.commit()
    con.close()


def test_learning_pauses_on_the_fault_resumes_after_the_fix_and_advances_on_a_new_match(db, monkeypatch, tmp_path):
    ldb = tmp_path / "learning.db"
    _pick(536, PROD_536)
    _forecast(536, 1, "2026-10-12T06:00:00+00:00")
    picks, fcs = picklog.export(), picklog.export_forecasts(inputs=True)
    t0 = NOW + timedelta(hours=1)
    log = learning.run_cycle(now=t0, picks=picks, forecasts=fcs, batches=[], corrections=[], db=ldb, train=False)
    assert "settlement_audit" in (log.get("paused") or [])
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(HLTV_2399144))
    picklog.recheck_bo3_with_hltv(now=NOW)
    corr = [json.loads(x) for x in (db / "corrections.jsonl").read_text().splitlines()]
    log = learning.run_cycle(now=t0 + timedelta(minutes=10), picks=picklog.export(), forecasts=fcs, batches=[], corrections=corr,
                             db=ldb, train=False)
    assert not log.get("paused")
    con = learning.connect(ldb)
    sc1 = learning.scorecard(con)
    assert sc1["buckets"]["scored"] == 1
    # idempotent replay: the same inputs again add nothing
    learning.run_cycle(now=t0 + timedelta(minutes=20), picks=picklog.export(), forecasts=fcs, batches=[], corrections=corr, db=ldb, train=False)
    assert con.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == sc1["ledger_rows"]
    # a newly settled eligible match arrives -> the scored count advances
    con2 = picklog._db()
    con2.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, status, "
                 "actual, hit, result, settled_at, test, on_board, units, logged_at, book, market_id, line_type) VALUES "
                 "(900, 'ropz', 11816, 'FaZe', 'Vitality', 'faze-vs-vitality-13-10-2026', '2026-10-13T15:00:00Z', 'kills', '12', 32.5, "
                 "'More', 'settled', 35, 1, ?, '2026-10-13T18:00:00+00:00', 2, 1, 1.0, '2026-10-13T10:00:00+00:00', 'underdog', 'm9', 'standard')",
                 (json.dumps({"source": "hltv", "per_map": [[18, 9], [17, 8]], "rounds": [22, 24], "won": [True, False]}),))
    con2.commit()
    con2.close()
    _forecast(900, 2, "2026-10-13T10:00:00+00:00", p_more=0.58, line=32.5)
    learning.run_cycle(now=t0 + timedelta(days=1), picks=picklog.export(), forecasts=picklog.export_forecasts(inputs=True),
                       batches=[], corrections=corr, db=ldb, train=False)
    sc2 = learning.scorecard(learning.connect(ldb))
    assert sc2["buckets"]["scored"] == 2 and sc2["overall"]["matches"] == 2


# ------------------------------------------------------------------------------------------------ bounded export
def _fill(n):
    con = picklog._db()
    con.executemany("INSERT INTO forecasts (id, pick_id, kind, logged_at, line, p_more, shadow, inputs) VALUES (?, ?, 'update', ?, 30.5, 0.5, ?, ?)",
                    [(i, i, f"2026-10-13T00:00:{i % 60:02d}+00:00", json.dumps({"x": "y" * 200}),
                      __import__("zlib").compress(json.dumps({"i": i}).encode())) for i in range(1, n + 1)])
    con.commit()
    con.close()


def _page_all(limit, inputs=False, cutoff=None, between=None):
    seen, after, pages = [], 0, 0
    while True:
        pg = picklog.forecasts_page(after_id=after, cutoff_id=cutoff, limit=limit, inputs=inputs)
        cutoff = pg["cutoff_id"]
        seen += [r["id"] for r in pg["rows"]]
        after, pages = pg["next_after"], pages + 1
        if between:
            between()
        if pg["done"]:
            return seen, cutoff, pages, pg


@pytest.mark.parametrize("n,limit", [(0, 10), (1, 10), (10, 10), (11, 10), (25, 7), (2000, 2000)])
def test_pages_cover_every_row_once_at_every_boundary(db, n, limit):
    _fill(n)
    seen, cutoff, pages, last = _page_all(limit)
    assert seen == list(range(1, n + 1)) and cutoff == n and last["total_in_cutoff"] == n
    assert pages == max(1, -(-n // limit))                          # an exact multiple needs no empty extra page


def test_rows_added_while_paging_never_enter_the_fixed_cutoff(db):
    _fill(30)
    extra = iter(range(31, 100))

    def add():
        i = next(extra)
        con = picklog._db()
        con.execute("INSERT INTO forecasts (id, pick_id, kind, logged_at) VALUES (?, ?, 'update', '2026-10-14T00:00:00+00:00')", (i, i))
        con.commit()
        con.close()
    seen, cutoff, _, _ = _page_all(8, between=add)
    assert cutoff == 30 and seen == list(range(1, 31))
    seen2, _, _, _ = _page_all(8, cutoff=None)                      # a new export takes the new cutoff
    assert seen2 == sorted(set(seen2)) and seen2[-1] > 30


def test_page_size_is_capped_and_inputs_decode(db):
    _fill(700)
    pg = picklog.forecasts_page(limit=10 ** 6, inputs=True)
    assert len(pg["rows"]) == picklog.PAGE_MAX[True] == 500 and pg["rows"][0]["inputs"] == {"i": 1}
    pg = picklog.forecasts_page(limit=10 ** 6)
    assert len(pg["rows"]) == 700 and "inputs" not in pg["rows"][0] and pg["rows"][0]["has_inputs"] is True


def test_memory_of_one_page_does_not_grow_with_the_table(db):
    _fill(200)
    tracemalloc.start()
    picklog.forecasts_page(limit=100)
    small = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    _fill_more = [(i, i, "2026-10-13T00:00:00+00:00", json.dumps({"x": "y" * 200})) for i in range(201, 8201)]
    con = picklog._db()
    con.executemany("INSERT INTO forecasts (id, pick_id, kind, logged_at, shadow) VALUES (?, ?, 'update', ?, ?)", _fill_more)
    con.commit()
    con.close()
    tracemalloc.start()
    picklog.forecasts_page(limit=100)
    big = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert big < small * 1.5 + 200_000                               # 40x the rows, about the same peak for one page


# ------------------------------------------------------------------------------------------------ P1 product scorecard
def _row(pid, player, slug, line, test, book, line_type="standard", actual=30, logged="2026-10-13T10:00:00+00:00", start="2026-10-13T15:00:00Z"):
    con = picklog._db()
    con.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, status, "
                "actual, hit, result, settled_at, test, on_board, units, logged_at, book, market_id, line_type) VALUES "
                "(?, ?, 1, 'A', 'B', ?, ?, 'kills', '12', ?, 'More', ?, ?, ?, ?, '2026-10-13T18:00:00+00:00', ?, 1, 1.0, ?, ?, ?, ?)",
                (pid, player, slug, start, line, "push" if actual == line else "settled", actual, None if actual == line else int(actual > line),
                 json.dumps({"source": "hltv", "per_map": [[actual // 2, 5], [actual - actual // 2, 5]], "rounds": [20, 20], "won": [True, True]}),
                 test, logged, book, "m" if book else None, line_type))
    con.commit()
    con.close()
    _forecast(pid, pid, logged, p_more=0.6, line=line)


def test_product_scorecard_uses_only_standard_board_lines(db, tmp_path):
    _row(1, "a", "x-vs-y-13-10-2026", 28.5, 2, "underdog")                                # board standard: P1 unit
    _row(2, "a", "x-vs-y-13-10-2026", 18.5, 0, None, logged="2026-10-13T11:00:00+00:00")  # later member line, same unit
    _row(3, "b", "x-vs-y-13-10-2026", 30.0, 2, "underdog")                                # board line is a push
    _row(4, "c", "x-vs-y-13-10-2026", 24.5, 2, "prizepicks", line_type="goblin")          # adjusted market only
    ldb = tmp_path / "l.db"
    learning.run_cycle(now=datetime(2026, 10, 14, tzinfo=timezone.utc), picks=picklog.export(), forecasts=picklog.export_forecasts(inputs=True),
                       batches=[], corrections=[], db=ldb, train=False)
    con = learning.connect(ldb)
    us, excl = learning.product_units(con)
    assert [(u["line"], u["book"]) for u in us] == [(28.5, "underdog")]                   # never the member's 18.5
    assert excl["push on the board line"] == 1
    assert excl["no standard board line 30+ min before the start (member / adjusted lines only)"] == 1
    sc = learning.scorecard(con)["product"]
    assert sc["units"] == 1 and sc["protocol"]["id"] == "P1-board-lines" and sc["side_selection"]["ties_at_50"] == 0
    assert sc["staking"]["staked_units"] == 1 and sc["staking"]["stake_weighted_hit"] == 1.0


def test_history_revision_labels():
    assert learning.history_revision({"logged_at": "2026-10-12T00:00:00+00:00"}) == "N0 (before backfill)"
    assert learning.history_revision({"logged_at": "2026-10-13T02:00:00+00:00"}) == "partial backfill"
    assert learning.history_revision({"logged_at": "2026-10-14T02:00:00+00:00"}) == "N (backfill live, unintended)"
    assert learning.history_revision({"data_rev": "matches=1;perf_input=N0/n0-r4:pre=8597"}) == "N0 pinned (n0-r4)"


def test_a_stale_unit_key_on_a_forecast_copy_never_hides_the_forecast(db, tmp_path):
    """Production 2026-10-15: 202 units 'without a forecast' because the copies kept the unit key of the moment they were
    copied, and the canonical match changed afterwards. Units find their forecasts by pick id."""
    _row(1, "a", "x-vs-y-13-10-2026", 28.5, 2, "underdog")
    ldb = tmp_path / "l.db"
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    learning.run_cycle(now=now, picks=picklog.export(), forecasts=picklog.export_forecasts(inputs=True), batches=[], corrections=[],
                       db=ldb, train=False)
    con = learning.connect(ldb)
    con.execute("UPDATE forecast_copy SET unit_key = 'a|old-slug-identity|kills|12'")        # the key a later re-identification left behind
    con.execute("DELETE FROM ledger")
    con.commit()
    learning.run_cycle(now=now + timedelta(hours=1), picks=picklog.export(), forecasts=[], batches=[], corrections=[], db=ldb, train=False)
    sc = learning.scorecard(learning.connect(ldb))
    assert sc["buckets"]["scored"] == 1 and sc["buckets"]["incomplete"] == 0
    assert sc["product"]["units"] == 1


# ------------------------------------------------------------------------------------------------ pl-1 (shadow only)
def _fcopy(**kw):
    base = {"source": "board", "line_type": "standard", "book": "underdog", "prop": "kills", "line": 30.5,
            "logged_at": "2026-10-16T10:00:00+00:00", "lambda": 33.0}
    return dict(base, **kw)


PERF = {"perf": {"lambda": 33.0, "sd": 6.0}}


def test_pl1_target_line_only_is_the_market_median_and_keeps_the_independent_projection():
    p, mu, tr = learning.prop_line_value(_fcopy(), PERF, [])
    assert mu == 30.5 and abs(p - 0.5) < 1e-9                     # kills w = 0: mu = the market median = the line
    assert tr["lam_independent"] == 33.0 and tr["median_market"] == 30.5 and tr["other"] is None
    assert tr["p_replay"] > 0.6                                    # the independent projection is logged untouched
    p, mu, tr = learning.prop_line_value(_fcopy(prop="hs", line=14.5), {"perf": {"lambda": 16.5, "sd": 3.5}}, [])
    assert abs(mu - (14.5 + 0.25 * 2.0)) < 1e-9 and 0.5 < p < tr["p_replay"]


def test_pl1_uses_the_other_platform_only_when_logged_before_the_forecast_and_two_sided():
    before = ("2026-10-16T09:00:00+00:00", "prizepicks", 31.5, True, 7)
    after = ("2026-10-16T11:00:00+00:00", "prizepicks", 34.5, True, 8)
    p, mu, tr = learning.prop_line_value(_fcopy(), PERF, [before, after])
    assert tr["other"]["forecast_id"] == 7 and mu == 31.0 and p > 0.5      # (30.5 + 31.5) / 2; the later 34.5 never seen
    over_only = ("2026-10-16T09:00:00+00:00", "prizepicks", 31.5, False, 9)
    p, mu, tr = learning.prop_line_value(_fcopy(), PERF, [over_only])
    assert mu == 30.5 and tr["other"]["two_sided"] is False               # a threshold with unknown pricing: not a median


def test_pl1_never_scores_member_or_adjusted_lines_or_an_over_only_target_alone():
    assert learning.prop_line_value(_fcopy(source="member", book=None), PERF, [])[0] is None
    assert learning.prop_line_value(_fcopy(line_type="goblin", book="prizepicks"), PERF, [])[0] is None
    p, mu, why = learning.prop_line_value(_fcopy(book="prizepicks"), dict(PERF, less_available=False), [])
    assert p is None and "Over only" in why


def test_pl1_is_frozen_and_shadowed_on_forward_board_forecasts(db, tmp_path):
    ldb = tmp_path / "l.db"
    t0 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    learning.run_cycle(now=t0, picks=[], forecasts=[], batches=[], corrections=[], db=ldb, train=False)
    con = learning.connect(ldb)
    ch = con.execute("SELECT * FROM challengers WHERE family = 'prop-line-1'").fetchone()
    # 2026-10-15 owner: pl-1's promotion is blocked (known anchor defects); it keeps logging for the pl-2 comparison
    assert ch["status"] == "blocked" and ch["frozen_at"] == "2026-10-15T02:00:00+00:00" and json.loads(ch["config"])["w"] == {"kills": 0.0, "hs": 0.25}
    _row(1, "a", "x-vs-y-16-10-2026", 28.5, 2, "underdog", logged="2026-10-16T10:00:00+00:00", start="2026-10-16T15:00:00Z")
    gi = {"perf": {"version": "perf-2", "lambda": 31.0, "sd": 6.0}}
    con2 = picklog._db()
    con2.execute("UPDATE forecasts SET inputs = ? WHERE id = 1", (__import__("zlib").compress(json.dumps(gi).encode()),))
    con2.commit()
    con2.close()
    learning.run_cycle(now=t0 + timedelta(days=2), picks=picklog.export(), forecasts=picklog.export_forecasts(inputs=True),
                       batches=[], corrections=[], db=ldb, train=False)
    s = con.execute("SELECT * FROM shadow WHERE challenger_id = ?", (ch["id"],)).fetchone()
    t = json.loads(s["note"])
    assert s["lam_base"] == 31.0 and s["lam_ch"] == 28.5 and abs(s["p_over_ch"] - 0.5) < 1e-9 and t["target"]["book"] == "underdog"
    sc = learning.scorecard(learning.connect(ldb))["product"]
    pl = next(c for c in sc["challengers"] if c["id"] == ch["id"])
    c1 = pl["c1"]
    assert pl["units"] == 1 and c1["units"] == 1 and c1["identity_kills"]["pl1_differs_from_B"] == 0
    assert {x["pair"] for x in c1["pairs"]} == {"B vs A", "A vs D", "B vs D", "C vs B", "C vs D"}
    assert set(c1["point_forecasts"]["kills"]["mae_median_type"]) == {"target line", "line anchor m", "pl-1 location"}
    assert c1["by_anchor_class"][0]["anchor"] == "no quote recorded"            # forecast logged without a quote


def test_pl1_kills_identity_holds_with_another_platform_line():
    p, mu, tr = learning.prop_line_value(_fcopy(), PERF, [("2026-10-16T09:00:00+00:00", "prizepicks", 31.5, True, 7)])
    assert abs(p - tr["p_market"]) <= 5e-5 and mu == tr["median_market"] and tr["mean_combined"] == tr["median_market"]


def test_anchor_classes_follow_the_logged_quote():
    assert learning.anchor_class("underdog", {"price_over": 1.9, "price_under": 1.9}).startswith("Underdog: equal")
    assert learning.anchor_class("underdog", {"price_over": 1.8, "price_under": 2.03}).startswith("Underdog: unequal")
    assert learning.anchor_class("prizepicks", {"feed_odds_type": "promo"}).startswith("PrizePicks promo")
    assert learning.anchor_class("prizepicks", {"feed_odds_type": "standard"}).endswith("(line-anchored assumption)")
    assert learning.anchor_class("underdog", None) == "no quote recorded"


def test_a_board_forecast_records_its_quote(db):
    import cs2_scout  # noqa: F401  (pick_row needs the grade layout only)
    res = {"grade": {"prop": "kills", "line": 30.5, "line_type": "standard", "p_more": 0.55, "p_less": 0.45, "lambda_final": 31.0, "scope": "12"},
           "player_block": {"player": {"slug": "a", "nick": "a", "id": 1}}, "next_match": {"slug": "x-vs-y-16-10-2026", "start": "2026-10-16T15:00:00Z"},
           "book": {"book": "underdog", "market_id": "m1", "odds_type": "standard",
                    "quote": {"price_over": 1.8, "price_under": 2.03, "feed_odds_type": "standard", "updated_at": "2026-10-16T09:59:00Z"}}}
    v = {"state": "GRADE", "side": "More", "p": 0.55, "grade": "B-", "units": 0.5}
    picklog.log_grade(res, v, test=2)
    q = json.loads(picklog.forecasts_page()["rows"][0]["quote"])
    assert q["price_over"] == 1.8 and q["price_under"] == 2.03


def test_a_refit_is_a_new_immutable_version_that_inherits_no_forward_results(tmp_path, monkeypatch):
    con = learning.connect(tmp_path / "l.db")
    rows = [{"fid": None, "match": f"m{i}", "start": "2026-10-10T10:00:00+00:00"} for i in range(70)]
    monkeypatch.setattr(learning, "build_snapshot", lambda con_, now: ("snap-x", "h" * 16, rows * 3))
    k = iter([0.9, 1.1])
    monkeypatch.setattr(learning, "refit", lambda rows_, base: {"candidate": True, "config": dict(learning.LIVE_CFG, c_mult=next(k)), "folds": [],
                                                              "trace": [], "train_loss_live": 0.7, "train_loss_fit": 0.69})
    t1 = datetime(2026, 10, 20, tzinfo=timezone.utc)
    a = learning.training_step(con, t1, force=True)
    con.execute("INSERT INTO reviews (challenger_id, win, decided_at, passed, result) VALUES (?, 300, ?, 0, '{}')", (a["challenger"], learning._iso(t1)))
    b = learning.training_step(con, t1 + timedelta(days=9), force=True)
    assert a["challenger"] != b["challenger"]
    A = con.execute("SELECT * FROM challengers WHERE id = ?", (a["challenger"],)).fetchone()
    Bc = con.execute("SELECT * FROM challengers WHERE id = ?", (b["challenger"],)).fetchone()
    assert A["status"] == "superseded" and Bc["frozen_at"] > A["frozen_at"]
    prov = json.loads(Bc["provenance"])
    assert prov["snapshot_id"] == "snap-x" and prov["training_cutoff"] == Bc["frozen_at"] and "data_revisions" in prov
    assert con.execute("SELECT COUNT(*) FROM reviews WHERE challenger_id = ?", (b["challenger"],)).fetchone()[0] == 0


def test_p1_units_export_serves_each_challengers_stored_value_for_the_same_forecast(db, tmp_path, monkeypatch):
    """Owner 2026-10-16: exact paired evaluation off the server - the stored shadow value per P1 unit, paged."""
    _row(1, "a", "x-vs-y-13-10-2026", 28.5, 2, "underdog")
    _row(2, "b", "x-vs-y-13-10-2026", 26.5, 2, "prizepicks")
    ldb = tmp_path / "l.db"
    learning.run_cycle(now=datetime(2026, 10, 14, tzinfo=timezone.utc), picks=picklog.export(), forecasts=picklog.export_forecasts(inputs=True),
                       batches=[], corrections=[], db=ldb, train=False)
    con = learning.connect(ldb)
    ch = con.execute("SELECT id FROM challengers ORDER BY frozen_at LIMIT 1").fetchone()["id"]
    fid = learning.product_units(con)[0][0]["forecast_id"]
    con.execute("INSERT OR REPLACE INTO shadow (challenger_id, forecast_id, p_over_ch, p_over_base, p_over_logged, lam_ch, lam_base, changed) "
                "VALUES (?, ?, 0.512, 0.6, 0.6, 27.0, 27.0, 1)", (ch, fid))
    learning._set_meta(con, "p1_units", learning.p1_units_export(con))
    con.commit()
    con.close()
    monkeypatch.setattr(learning, "DB_PATH", ldb)
    st, body, _ = learning.http("/admin/learning/p1_units.json", {"limit": "1"}, tmp_path)
    assert st == 200 and body["total"] == 2 and len(body["rows"]) == 1 and body["next_after"] == 1
    assert body["protocol"] == "P1-board-lines" and {c["id"] for c in body["challengers"]} >= {ch}
    st, body2, _ = learning.http("/admin/learning/p1_units.json", {"after": "1", "limit": "5"}, tmp_path)
    rows = body["rows"] + body2["rows"]
    assert body2["next_after"] is None and sorted(r["forecast_id"] for r in rows) == sorted({r["forecast_id"] for r in rows})
    got = next(r for r in rows if r["forecast_id"] == fid)
    assert got["challengers"][ch] == {"p": 0.512, "p_base": 0.6, "lam": 27.0, "lam_base": 27.0} and got["p"] == 0.6
