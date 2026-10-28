"""gc-1 multilevel grade check (REGISTER.md A9): registered before the code, read-only, side = the board lean (never
the max-chance side), letters from the unrounded lean-side chance, as-shown holds (N4 / sg-1), pushes apart,
game-clustered intervals, the A9 5 verdict rule, the compact CLI output - and the two grading-logic fixes of the same
audit (slip legs follow the board lean; the L10 hold reads the post-N4 lean)."""
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from sports import board_view
from sports.model import cal, gradecheck as gc, grade_policy, tune
from sports.slips import pool

ROOT = Path(__file__).resolve().parents[2]


def _git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)


# --------------------------------------------------------------------------------------------- registration
def test_a9_registered_with_the_exact_numbers_and_before_the_code():
    text = (ROOT / "sports" / "research" / "REGISTER.md").read_text(encoding="utf-8")
    i = text.index("Amendment A9")
    a9 = text[i:]
    for s in ("n(AB) >= 150", ">= 3 game-days", ">= 10 games", "stated(AB) - 0.05", "lower bound >= 0.50",
              "<= 0.15", "upper bound is < 0.50", "**release**", "**contain**", "**keep**", "**insufficient**",
              "VG_CONTAIN_LEAN", "read-only"):
        assert s in a9, s
    assert (gc.AB_MIN, gc.AB_MIN_DAYS, gc.AB_MIN_GAMES, gc.REL_TOL, gc.CONTAIN_TOL, gc.SHARE_TOL,
            gc.STAT_FAIL_N, gc.STAT_CONTAIN_N) == (150, 3, 10, 0.05, 0.10, 0.15, 30, 60)
    if _git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone (CI): commit order is not visible")
    reg = _git("log", "-S", "Amendment A9", "--format=%H", "--", "sports/research/REGISTER.md").stdout.split()
    code = _git("log", "--diff-filter=A", "--format=%H", "--", "sports/model/gradecheck.py").stdout.split()
    if not reg or not code:
        pytest.skip("git history not available (code not committed yet)")
    assert reg[-1] != code[-1] and _git("merge-base", "--is-ancestor", reg[-1], code[-1]).returncode == 0


# --------------------------------------------------------------------------------------------- synthetic data
SC_DDL = """CREATE TABLE scorecard (forecast_id TEXT, settlement_id TEXT, model_version TEXT, league TEXT, stat TEXT,
    event_id TEXT, platform TEXT, line REAL, side TEXT, p_side REAL, p_over REAL, p_under REAL, p_push REAL,
    status TEXT, outcome TEXT, value REAL, pit REAL, forecast_at TEXT, settled_at TEXT, superseded INTEGER DEFAULT 0,
    PRIMARY KEY (forecast_id, settlement_id))"""
IN_DDL = """CREATE TABLE inputs (forecast_id TEXT PRIMARY KEY, model_version TEXT, league TEXT, market TEXT,
    offer_record_id TEXT, artifact_sha TEXT, forecast_at TEXT, row_json TEXT, market_json TEXT, pmf_lo INTEGER,
    pmf_json TEXT, policy_json TEXT, created_at TEXT)"""


def _pmf(mean):
    """A 3-atom PMF with exactly this (integer) mean -> (lo, pmf)."""
    m = int(round(mean))
    return m - 1, [0.25, 0.5, 0.25]


class World:
    """A learning.db + forecasts.db pair built row by row."""

    def __init__(self, tmp):
        self.l = tmp / "learning.db"
        self.i = tmp / "forecasts.db"
        self.lc = sqlite3.connect(self.l)
        self.lc.execute(SC_DDL)
        self.ic = sqlite3.connect(self.i)
        self.ic.execute(IN_DDL)
        self.n = 0

    def prop(self, event, po, pu, mean, value, line=10.5, stat="points", platform="prizepicks", pp=0.0,
             start="2026-10-23T23:00:00Z", forecast_at=None, mkt=None, sha="abc", home=1.0, rest=2.0, mv="sm-1",
             status=None, rid=None):
        self.n += 1
        fid = f"fc-{self.n:04d}"
        rid = rid or f"offer:{platform}:{platform}:{self.n:04d}:r1:aa"
        self.last_rid = rid
        fat = forecast_at or start[:11] + "22:00:00Z"
        side = "over" if po >= pu else "under"
        if status is None:
            outcome = "over" if value > line else ("under" if value < line else "push")
            status = "push" if outcome == "push" else ("win" if outcome == side else "loss")
        else:
            outcome = None
        self.lc.execute("INSERT INTO scorecard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                        (fid, "st-" + fid, mv, "nba", stat, event, platform, line, side, po if side == "over" else pu,
                         po, pu, pp, status, outcome, value, None, fat, start[:11] + "23:59:00Z"))
        lo, pmf = _pmf(mean) if mean is not None else (None, None)
        row = {"home": home, "rest_days": rest, "start_utc": start, "prior_n": 20, "player_id": f"p{self.n}"}
        self.ic.execute("INSERT INTO inputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, mv, "nba", stat, rid, sha, fat, json.dumps(row), None, lo,
                         json.dumps(pmf) if pmf else None, "{}", fat))
        if mkt is not None:                                 # a settled market forecast at the same offer + forecast_at
            mfid = fid + "-mkt"
            self.lc.execute("INSERT INTO scorecard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                            (mfid, "st-" + mfid, "mkt-kalshi", "nba", stat, event, "kalshi", line,
                             "over" if mkt >= 0.5 else "under", max(mkt, 1 - mkt), mkt, 1 - mkt, 0.0, status, outcome,
                             value, None, fat, start[:11] + "23:59:00Z"))
            self.ic.execute("INSERT INTO inputs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (mfid, "mkt-kalshi", "nba", stat, rid, None, fat, "{}", None, None, None, "{}", fat))
        return fid

    def close(self):
        self.lc.commit()
        self.ic.commit()
        self.lc.close()
        self.ic.close()
        return self


def _run(w, **kw):
    return gc.run(w.l, w.i, None, None, None, "nba", **kw)["leagues"]["nba"]


# --------------------------------------------------------------------------------------------- side and letters
def test_side_is_the_board_lean_never_the_max_chance_side(tmp_path):
    w = World(tmp_path)
    w.prop("nba:espn:1", 0.62, 0.38, mean=12, value=14)       # mean above the line: lean Over, A, hit
    w.prop("nba:espn:1", 0.62, 0.38, mean=9, value=14)        # chance says Over, mean below the line: NO lean
    w.prop("nba:espn:2", 0.30, 0.70, mean=8, value=7)         # lean Under, A, hit
    w.prop("nba:espn:2", 0.30, 0.70, mean=None, value=14)     # no mean: chance alone -> lean Under, loss
    r = _run(w.close())
    c = r["counts"]
    assert (c["props"], c["no_lean"], c["win_loss"]) == (4, 1, 4)
    assert r["overall"]["all_leans"]["n"] == 3 and r["overall"]["all_leans"]["w"] == 2
    assert r["overall"]["scorecard_max_p_side"]["n"] == 4         # the max-p side scores all four (comparison only)
    assert r["side"]["over"]["n"] == 1 and r["side"]["under"]["n"] == 2


def test_letters_are_sports_gp1_bands_of_the_unrounded_unconditional_lean_chance(tmp_path):
    w = World(tmp_path)
    for i, po in enumerate((0.60, 0.5999, 0.5559, 0.5558, 0.5345, 0.5344, 0.51)):
        w.prop(f"nba:espn:{i}", po, 1 - po, mean=12, value=14)
    r = _run(w.close())
    L = r["letter"]
    assert (L["A"]["n"], L["B"]["n"], L["C"]["n"], L["Lean"]["n"]) == (1, 2, 2, 2)
    assert [grade_policy.band(p) for p in (0.60, 0.5999, 0.5559, 0.5558, 0.5345, 0.5344)] == \
        ["A", "B", "B", "C", "C", "Lean"]
    assert L["AB"]["n"] == 3 and r["letter_by_stat"]["points"]["A"]["n"] == 1


def test_stated_and_hit_are_conditional_on_no_push_and_pushes_are_counted_apart(tmp_path):
    w = World(tmp_path)
    w.prop("nba:espn:1", 0.57, 0.38, mean=12, value=14, line=11.0, pp=0.05)    # Over, hit
    w.prop("nba:espn:1", 0.57, 0.38, mean=12, value=11, line=11.0, pp=0.05)    # push: not scored
    w.prop("nba:espn:2", 0.57, 0.38, mean=12, value=10, line=11.0, pp=0.05)    # loss
    r = _run(w.close())
    s = r["overall"]["all_leans"]
    assert (s["n"], s["w"], s["l"], s["p"]) == (2, 1, 1, 1) and r["counts"]["push"] == 1
    assert s["stated"] == pytest.approx(0.57 / 0.95, abs=1e-3) and s["hit"] == 0.5
    assert s["ci"] == [0.0, 1.0] or (s["ci"][0] is not None and 0 <= s["ci"][0] <= 0.5 <= s["ci"][1] <= 1)


def test_intervals_are_game_clustered_and_null_below_two_games(tmp_path):
    w = World(tmp_path)
    for k in range(6):
        w.prop("nba:espn:1", 0.62, 0.38, mean=12, value=14 if k % 2 else 9)
    r = _run(w.close())
    assert r["overall"]["all_leans"]["games"] == 1 and r["overall"]["all_leans"]["ci"] == [None, None]
    (tmp_path / "b").mkdir()
    w = World(tmp_path / "b")
    for g in range(4):
        for k in range(5):
            w.prop(f"nba:espn:{g}", 0.62, 0.38, mean=12, value=14 if (k + g) % 2 else 9)
    r = _run(w.close())
    lo, hi = r["overall"]["all_leans"]["ci"]
    assert r["overall"]["all_leans"]["games"] == 4 and lo is not None and lo <= 0.5 <= hi


# --------------------------------------------------------------------------------------------- as-shown holds
def test_n4_and_sg1_holds_are_removed_from_as_shown_and_their_results_reported(tmp_path):
    w = World(tmp_path)
    w.prop("nba:espn:1", 0.62, 0.38, mean=12, value=14, mkt=0.70)     # market prices Over higher: N4 blocked (hit)
    w.prop("nba:espn:1", 0.62, 0.38, mean=12, value=14, mkt=0.55)     # positive edge: shown
    w.prop("nba:espn:2", 0.62, 0.38, mean=12, value=14)               # no market chance: shown
    w.prop("nba:espn:2", 0.30, 0.70, mean=1.0, value=3, line=10.5)    # mean under 25% of the line: sg-1 ratio_low
    r = _run(w.close())
    c = r["counts"]
    assert (c["n4_blocked"], c["sg1_ratio"], c["as_shown"], c["with_market_chance"]) == (1, 1, 2, 2)
    assert r["guards"]["n4_blocked"]["n"] == 1 and r["guards"]["n4_blocked"]["w"] == 1
    assert r["guards"]["sg1_ratio_held"]["n"] == 1 and r["market_chance"]["yes"]["n"] == 1
    assert r["overall"]["as_shown"]["n"] == 2 and r["overall"]["all_leans"]["n"] == 4
    assert c["l10_unknown"] == 4                                     # no history store: never counted as held


def test_one_row_per_offer_keeps_the_last_pre_start_forecast(tmp_path):
    w = World(tmp_path)
    a = w.prop("nba:espn:1", 0.62, 0.38, mean=12, value=14, forecast_at="2026-10-23T20:00:00Z")
    # the same offer record id, a later forecast with the opposite lean: only the later one is a row
    b = w.prop("nba:espn:1", 0.40, 0.60, mean=9, value=14, forecast_at="2026-10-23T22:30:00Z", rid=w.last_rid)
    r = _run(w.close())
    assert r["counts"]["props"] == 1 and r["side"].get("under", {}).get("n") == 1 and a != b


# --------------------------------------------------------------------------------------------- verdict (A9 5)
def _s(n, hit, stated, lo, hi, bal=0.05, games=20):
    return {"n": n, "games": games, "hit": hit, "stated": stated, "ci": [lo, hi], "bal": bal}


def test_verdict_rule_release_keep_contain_insufficient():
    allr = _s(400, 0.56, 0.60, 0.51, 0.61)
    v = gc.verdict(_s(160, 0.62, 0.65, 0.54, 0.70), allr, {"points": _s(80, 0.6, 0.65, 0.5, 0.7)}, 4)
    assert v["verdict"] == "release" and all(v["release_conditions"].values())
    assert gc.verdict(_s(149, 0.62, 0.65, 0.54, 0.70), allr, {}, 4)["verdict"] == "insufficient"
    assert gc.verdict(_s(200, 0.62, 0.65, 0.54, 0.70), allr, {}, 2)["verdict"] == "insufficient"
    v = gc.verdict(_s(160, 0.58, 0.65, 0.49, 0.66), allr, {}, 4)              # CI lower < 0.50, hit within 10 pts
    assert v["verdict"] == "keep" and "ci_lower_ge_50" in v["why"]
    v = gc.verdict(_s(160, 0.62, 0.65, 0.54, 0.70, bal=0.2), allr, {}, 4)     # balance
    assert v["verdict"] == "keep" and "balance" in v["why"]
    v = gc.verdict(_s(160, 0.62, 0.65, 0.54, 0.70), allr, {"threes": _s(35, 0.4, 0.65, 0.3, 0.49)}, 4)
    assert v["verdict"] == "keep" and v["stat_ci_upper_below_50_n30"] == ["threes"]
    v = gc.verdict(_s(160, 0.53, 0.65, 0.46, 0.60), allr, {}, 4)              # 12 pts under stated
    assert v["verdict"] == "contain"
    v = gc.verdict(_s(160, 0.44, 0.65, 0.38, 0.49), allr, {}, 4)              # interval entirely below 50%
    assert v["verdict"] == "contain"
    v = gc.verdict(_s(100, 0.5, 0.65, 0.4, 0.6), allr, {"points": _s(60, 0.35, 0.65, 0.25, 0.48)}, 4)
    assert v["verdict"] == "contain:points"                                   # per-stat, even below 150 overall
    v = gc.verdict(_s(160, 0.60, 0.65, 0.54, 0.70, games=9), allr, {}, 4)     # < 10 games: not a release
    assert v["verdict"] == "keep" and "games_ge_10" in v["why"]


# --------------------------------------------------------------------------------------------- cal-mi (A9 2)
def test_cal_mi_on_market_scores_the_walk_forward_chance_only_after_a_fit(tmp_path, monkeypatch):
    w = World(tmp_path)
    for g in range(3):                                                 # day 1: 150 props, 3 games (fit material)
        for k in range(50):
            po = 0.62 if k % 2 else 0.40                               # Overs hit 80% when favoured, 20% when not
            over = (k % 5 != 0) if po > 0.5 else (k % 5 == 0)
            w.prop(f"nba:espn:d1g{g}", po, 1 - po, mean=12 if po > 0.5 else 9, value=14 if over else 9,
                   start="2026-10-23T23:00:00Z", mkt=0.6 if k % 2 else 0.45)
    for k in range(20):                                                # day 2: scored with day-1 parameters
        w.prop("nba:espn:d2g0", 0.62, 0.38, mean=12, value=14 if k % 2 else 9, start="2026-10-25T23:00:00Z",
               mkt=0.6)
    w.close()
    params = {"nba:points": {"on": True, "M": [0.0, 1.0, 0.0], "S": [0.0, 1.0]}}
    obj = {"version": cal.VERSION, "params": params, "sha": cal._sha(params)}
    p = cal.weights_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    cal.reset_cache()
    try:
        r = _run(w)
    finally:
        p.unlink()
        cal.reset_cache()
    assert r["cal_mi"]["on"] == ["nba:points"] and r["counts"]["chance_cal_mi"] == 20
    assert r["counts"]["props"] == 170 and r["game_days"] == 2
    r2 = _run(w)                                                       # weights gone: raw chance everywhere
    assert r2["counts"]["chance_cal_mi"] == 0


# --------------------------------------------------------------------------------------------- CLI, read-only, size
def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def test_cli_prints_compact_json_writes_nothing_and_filters(tmp_path, capsys, monkeypatch):
    w = World(tmp_path)
    for g in range(3):
        for k in range(12):
            po = (0.62, 0.57, 0.54, 0.52, 0.45, 0.30)[k % 6]
            w.prop(f"nba:espn:{g}", po, 1 - po, mean=12 if po > 0.5 else 9, value=14 if (k + g) % 2 else 9,
                   stat=("points", "rebounds")[k % 2], platform=("prizepicks", "underdog")[g % 2],
                   start=f"2026-10-2{g + 1}T23:00:00Z", mkt=0.5 if k % 4 == 0 else None)
    w.close()
    before = {p.name: _sha(p) for p in tmp_path.iterdir()}
    tune.main(["gradecheck", "--learning", str(w.l), "--inputs", str(w.i), "--store", str(tmp_path / "none.db"),
               "--history-db", str(tmp_path / "none2.db"), "--games", str(tmp_path / "none3.db")])
    out = capsys.readouterr().out
    assert {p.name: _sha(p) for p in tmp_path.iterdir()} == before            # nothing written, nothing created
    doc = json.loads(out)
    assert doc["version"] == "gc-1" and doc["read_only"] is True and doc["sources"]["store"] is None
    assert len(out.splitlines()) < 400
    nba = doc["leagues"]["nba"]
    assert nba["counts"]["props"] == 36 and nba["game_days"] == 3 and nba["verdict"]["verdict"] == "insufficient"
    assert set(nba["letter"]) >= {"A", "B", "C", "Lean", "AB", "order_holds"}
    assert nba["platform"]["prizepicks"]["n"] + nba["platform"]["underdog"]["n"] == nba["overall"]["as_shown"]["n"]
    assert set(nba["weekday"]) <= set(gc.WEEKDAYS)                    # forecasts exactly 1 h before the start
    assert nba["forecast_age"]["1-3h"]["n"] == nba["overall"]["as_shown"]["n"]
    assert nba["guards"]["vg1_gate"]["points"] == board_view.gate_state("points")
    assert "nfl" in doc["leagues"] and doc["leagues"]["nfl"]["verdict"]["verdict"] == "insufficient"
    # every slice is one JSON object on one line (the owner pastes it back); n < 30 slices carry counts only (the
    # AB / overall sets are always full - they are what the verdict reads)
    for level in ("stat", "side", "platform", "letter_by_stat"):
        for s in nba[level].values():
            for x in (s.values() if level == "letter_by_stat" else [s]):
                assert ("bal" in x) == (x["n"] >= 30), (level, x)
    assert any(line.strip().startswith('"A": {"n":') for line in out.splitlines())
    # --days keeps only forecasts inside the window; --league restricts the leagues printed
    tune.main(["gradecheck", "--league", "nba", "--days", "2", "--learning", str(w.l), "--inputs", str(w.i),
               "--store", str(tmp_path / "x"), "--history-db", str(tmp_path / "x"), "--games", str(tmp_path / "x")])
    doc2 = json.loads(capsys.readouterr().out)
    assert list(doc2["leagues"]) == ["nba"] and doc2["leagues"]["nba"]["counts"]["props"] < 36


def test_dumps_puts_every_leaf_dict_on_one_line():
    s = gc.dumps({"a": {"n": 1, "ci": [0.1, 0.2], "x": None}, "b": {"c": {"n": 2}}})
    assert s.splitlines()[1].strip() == '"a": {"n": 1, "ci": [0.1, 0.2], "x": null},'
    assert json.loads(s)["b"]["c"]["n"] == 2


# --------------------------------------------------------------------------------------------- grading-logic fixes
def test_slip_leg_follows_the_board_lean_rule():
    # the chosen side has the higher chance but the projection mean sits on the other side: the board says No lean
    assert pool.lean_mismatch(0.62, 0.38, 9.0, 10.5, "over") == "no lean: the projection mean is on the other side of the line"
    assert pool.lean_mismatch(0.62, 0.38, 12.0, 10.5, "over") is None
    assert pool.lean_mismatch(0.38, 0.62, 9.0, 10.5, "under") is None
    assert pool.lean_mismatch(0.62, 0.38, None, 10.5, "over") is None          # no mean: chance-only rule as before
    assert pool.lean_mismatch(0.45, 0.45, 12.0, 10.5, "over") == "no lean: the board does not lean to this side"


def test_l10_hold_reads_the_lean_after_n4():
    # market prices the Over at least as high (N4): no lean - the L10 contradiction of that absent lean is not a hold
    mkc = {"po": 0.70, "pu": 0.30, "n": 2}
    x, code = board_view.quote_x("nba", "points", 0.62, 0.38, 12.0, 10.5, mkc, l10=[3, 7, 0, 10])
    assert code is None and x.get("ln") is None and x["cw"] == ["No lean"]
    # without the market the same row leans Over and the L10 rule holds it
    x, code = board_view.quote_x("nba", "points", 0.62, 0.38, 12.0, 10.5, None, l10=[3, 7, 0, 10])
    assert code == "l10_contra" and x["b"] == "l10_contra"
    # the ratio rules still apply to a row with no lean
    x, code = board_view.quote_x("nba", "points", 0.62, 0.38, 1.0, 10.5, mkc, l10=[3, 7, 0, 10])
    assert code == "ratio_low"
