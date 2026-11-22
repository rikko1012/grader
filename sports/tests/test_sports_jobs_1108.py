"""Sports jobs 2026-11-08 (live ops: sheets killed over its cap 13x, settle timed out 5x in a row, model-forward exit 1 x4).

sheets   public Results (results_public.build) kept one dict per forecast REVISION of the season and both leagues at
         once, and its bootstrap built a fresh row list per draw -> now reduced as rows stream, one league at a time,
         per-game bootstrap; and it runs as its own scheduler job. The file must be IDENTICAL (built_at aside).
settle   the results ledger + model / shadow scorecards (~1,000 s) ran inside the settle process -> settle --no-post +
         a settle-post job; the scorecards skip events whose records are unchanged since their last full pass and keep
         per-event progress when killed. Scorecards must be IDENTICAL to the full re-read.
forward  a streaming offer cursor stayed open across store.put -> a concurrent books commit made the next BEGIN
         IMMEDIATE fail with SQLITE_BUSY_SNAPSHOT ("database is locked"). The run must survive and write the same."""
import argparse
import json
import os
import random
import sqlite3
import tracemalloc
from collections import defaultdict
from dataclasses import replace

import pytest

from sports import config, results_public as rp, scheduler, sheets, snapshots, store, worker
from sports.tests import ref_ledger_ecfa81a as ledger_ref, ref_results_public_ecfa81a as rp_ref, season_world
from sports.tests.test_fwd import AT, fworld  # noqa: F401  (fixture chain: world -> fworld)
from sports.tests.test_line_correctness_model import world  # noqa: F401

NOW = "2026-11-08T21:00:00Z"


# ------------------------------------------------------------------------------------------------ the old code paths
def old_cluster_ci(items, stat, reps=1000, seed=7):
    """results_public.cluster_ci as of origin/main ecfa81a, verbatim."""
    groups = defaultdict(list)
    for it in items:
        groups[it["event_id"] or it["forecast_id"]].append(it)
    keys = sorted(groups)
    if len(keys) < 2:
        return None
    rng = random.Random(seed)
    vals = []
    for _ in range(reps):
        sample = [x for _k in range(len(keys)) for x in groups[keys[rng.randrange(len(keys))]]]
        v = stat(sample)
        if v is not None:
            vals.append(v)
    if len(vals) < reps // 2:
        return None
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4), round(vals[int(0.975 * (len(vals) - 1))], 4)]


def _built(con, monkeypatch, old):
    """results_public.json (built_at dropped) from the current build, or from the frozen ecfa81a module (old=True)."""
    (rp_ref if old else rp).build(con, now=NOW)
    d = json.loads(rp.path().read_text(encoding="utf-8"))
    d.pop("built_at")
    return d


def _ledger(con, lg, base, old):
    """<league>/results.json (built_at dropped) from the current ledger or the frozen ecfa81a one (old=True)."""
    out = (ledger_ref if old else snapshots).write_results(con, lg, now=NOW, base=base)
    d = json.loads((snapshots.snap_root(base) / lg / "results.json").read_text(encoding="utf-8"))
    d.pop("built_at")
    return out, d


# ------------------------------------------------------------------------------------------------- cluster_ci
def _ci_rows(rng, games, per_game, none_share=0.15):
    rows = []
    for g in range(games):
        for i in range(rng.choice(per_game)):
            res = rng.choice(["win", "loss", "push", None])
            err = None if rng.random() < none_share else rng.uniform(-9, 9) * rng.choice([1, 1e-3, 1e3])
            if rng.random() < 0.1 and err is not None:
                err = float(int(err))                    # integer-valued floats mixed in
            rows.append({"event_id": f"nba:{g}" if rng.random() > 0.02 else None, "forecast_id": f"f{g}-{i}",
                         "lean_result": res, "error": err, "outcome": rng.choice(["over", "under"]),
                         "sb": rng.random(), "mb": None if rng.random() < 0.1 else rng.random(),
                         "sh": rng.choice([None, 0.0, 1.0]), "mh": rng.choice([None, 0.0, 1.0])})
    return rows


def _hh_stats():
    """head_to_head's own mean / diff closures (built the way head_to_head builds them)."""
    out = []

    def mean(key):
        def f(xs):
            v = [x[key] for x in xs if x[key] is not None]
            return sum(v) / len(v) if v else None
        return rp._mean_spec(f, lambda x: x[key])

    def diff(a, b):
        def f(xs):
            v = [x[a] - x[b] for x in xs if x[a] is not None and x[b] is not None]
            return sum(v) / len(v) if v else None
        return rp._mean_spec(f, lambda x: x[a] - x[b] if x[a] is not None and x[b] is not None else None)
    out += [mean("sb"), mean("mb"), diff("sb", "mb"), diff("sh", "mh"), mean("sh")]
    return out


def _obs():
    def obs(x):
        return sum(1 for r in x if r["outcome"] == "over") / len(x) if x else None
    return rp._ratio_spec(obs, lambda r: 1 if r["outcome"] == "over" else 0, lambda r: 1)


@pytest.mark.parametrize("seed", range(6))
def test_cluster_ci_fast_path_is_bit_identical_to_the_original(seed):
    rng = random.Random(seed)
    for games, per_game in ((1, (1, 3)), (2, (1,)), (7, (1, 2, 9)), (40, (1, 5, 30))):
        rows = _ci_rows(rng, games, per_game)
        for stat in [rp._mae, rp._bias, rp._hit, _obs()] + _hh_stats():
            assert getattr(stat, "ci_spec", None) is not None
            assert rp.cluster_ci(rows, stat, reps=300) == old_cluster_ci(rows, stat, reps=300), (games, stat)
    # one decided lean in 30 games: ~1/e of the draws have none (skipped); no usable value at all -> None on both
    rows = [{"event_id": f"g{g}", "forecast_id": str(g), "lean_result": "win" if g == 0 else "push", "error": None}
            for g in range(30)]
    assert rp.cluster_ci(rows, rp._hit) == old_cluster_ci(rows, rp._hit)
    assert rp.cluster_ci(rows, rp._mae) is None and old_cluster_ci(rows, rp._mae) is None
    # fewer than reps // 2 usable draws: the reps // 2 rule on both paths
    few = [{"event_id": f"g{g}", "forecast_id": str(g), "lean_result": "win" if g == 0 else "push",
            "error": 1.5 if g == 0 else None} for g in range(30)]
    for stat in (rp._hit, rp._mae):
        assert rp.cluster_ci(few, stat, reps=1000) == old_cluster_ci(few, stat, reps=1000)
        assert rp.cluster_ci(few, stat, reps=10 ** 4 // 7) == old_cluster_ci(few, stat, reps=10 ** 4 // 7)
    # a stat without a spec still runs the original loop
    plain = lambda xs: len(xs) / 10                       # noqa: E731
    rows = _ci_rows(rng, 9, (1, 4))
    assert rp.cluster_ci(rows, plain, reps=200) == old_cluster_ci(rows, plain, reps=200)


def test_cluster_ci_does_not_copy_the_sampled_rows_per_draw():
    rows = [{"event_id": f"g{i % 40}", "forecast_id": str(i), "error": (i % 13) - 6.5, "lean_result": "win"}
            for i in range(40000)]
    tracemalloc.start()
    try:
        assert rp.cluster_ci(rows, rp._mae, reps=50) is not None
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # per-game value lists (~40k floats) plus small per-draw lists; the original built a 40k-row list every draw
    assert peak < 3_000_000, peak


# -------------------------------------------------------------------------------------- results_public equality
def _edge_cases(con):
    """Duplicate-line forecasts with a forecast_at tie across apps (PrizePicks + Underdog at the same line, same
    model, same run) are in the world already; add a market-only and a Scout forecast on a SHARED line whose newest
    revision is unresolved, and one forecast named by a settlement's affected ids."""
    from sports.tests import helpers as h
    rows = con.execute("SELECT record_id FROM records WHERE kind='offer' AND platform='prizepicks' AND "
                       "event_id='nba:espn:900' ORDER BY rid LIMIT 1").fetchall()
    o = store.get(con, rows[0][0])
    for model in ("sm-1", "mc-1"):
        store.put(con, h.forecast(forecast_at="2026-10-21T20:59:00Z", league=o.league, event_id=o.event_id,
                                  player_id=o.player_id, player_key=o.player_key, stat=o.stat, line=o.line,
                                  offer_snapshot_ids=(rows[0][0],), input_snapshot_ids=(rows[0][0], "x"),
                                  model_version=model, projection_mean=o.line + 1, p_over=0.6, p_under=0.4, p_push=0.0,
                                  p_over_given_no_push=0.6, p_under_given_no_push=0.4, chosen_side="over"))


@pytest.fixture
def season(tmp_path):
    with store.opened() as con:
        info = season_world.build(con, events=4, players=5, stats=3, revisions=3, extra_settled=4)
        _edge_cases(con)
    assert info["forecasts"] > 1500 and info["settlements"] > 300
    return info


def test_results_public_file_is_identical_to_the_old_build(season, monkeypatch):
    monkeypatch.setenv("SPORTS_LATEST_LINE", "0")        # audit_1121 rules off = the ecfa81a build exactly
    monkeypatch.setenv("NBA_MVLEAN1", "0")
    with store.opened(read_only=True) as con:
        new = _built(con, monkeypatch, old=False)
        old = _built(con, monkeypatch, old=True)
    assert new == old
    lg = new["leagues"]["nba"]
    # the world exercises what the rules decide on: several models, voids / unresolved, head-to-head pairs, leans
    assert lg["raw_counts"]["forecasts"] > lg["forecasts"] > 0
    assert sum(new["leagues"][x]["void"] + new["leagues"][x]["unresolved"] for x in new["leagues"]) > 0
    assert len(lg["models"]) >= 4 and lg["head_to_head"]["rows"] and lg["leans"]["n"] > 0
    assert lg["projection"]["mae_ci95"] and lg["leans"]["hit_rate_ci95"]


def _hand_rows(seed=3):
    """Hand-made rows (no store): equal forecast_at on one key, the same forecast id twice, void + unresolved, market
    and Scout on shared lines, a closeness tie, a forecast with no mean, several stats and games."""
    base = dict(player_id="p", stat="points", mean=21.0, p_over=0.55, p_under=0.45, p_push=0.0, research_only=False,
                actual=25.0, settled_at="2026-11-02T04:00:00Z", outcome="over", error=4.0, lean="over",
                lean_result="win", lean_p=0.55)
    rng = random.Random(seed)
    rows = []
    for g in range(4):
        for stat in ("points", "rebounds"):
            for model in ("sm-1", "mi-1", "mc-1", "mkt-fanduel"):
                for line, plats in ((20.5, ["prizepicks"]), (20.5, ["underdog"]), (21.5, ["fanduel"]),
                                    (19.5, ["kalshi"])):
                    for rev in (0, 1, 1, 2):
                        st = rng.choice(["settled"] * 6 + ["void", "unresolved"])
                        out = rng.choice(["over", "under", "push"])
                        lean = rng.choice(["over", "under", None]) if not model.startswith(("mc", "mkt")) else None
                        rows.append(dict(base, forecast_id=f"fc-{g}-{stat}-{model}-{line}-{rev}-{plats[0][:2]}",
                                         model=model, event_id=f"nba:{g}", stat=stat, line=line, platforms=plats,
                                         status=st, outcome=out if st == "settled" else None,
                                         mean=None if rng.random() < 0.05 else 21.0 + rng.choice([-1, 0, 1]),
                                         p_over=round(rng.uniform(0.3, 0.7), 3), p_under=None, error=rng.uniform(-5, 5),
                                         lean=lean if st == "settled" else None,
                                         lean_result=(None if not lean or st != "settled" else
                                                      "push" if out == "push" else "win" if out == lean else "loss"),
                                         lean_p=rng.uniform(0.5, 0.7), settled_at=f"2026-11-0{1 + g % 3}T04:00:00Z",
                                         forecast_at=f"2026-11-01T0{rev}:00:00Z"))
                        rows[-1]["p_under"] = round(1 - rows[-1]["p_over"], 3)
    rows.append(dict(rows[5]))                            # the same forecast twice (two settlement links)
    rng.shuffle(rows)
    return rows


@pytest.mark.parametrize("seed", range(3))
def test_results_public_ties_and_duplicates_keep_the_original_choice(monkeypatch, seed):
    """The spill build (SQL choices + streamed summaries) on hand-made rows equals the ecfa81a list build on them."""
    monkeypatch.setenv("SPORTS_LATEST_LINE", "0")        # audit_1121 rules off = the ecfa81a build exactly
    monkeypatch.setenv("NBA_MVLEAN1", "0")
    rows = _hand_rows(seed)
    monkeypatch.setattr(rp, "settlement_chunks", lambda con, lg: iter([[1], [2]] if lg == "nba" else []))
    halves = iter([rows[:70], rows[70:]])
    monkeypatch.setattr(rp, "_rows_from", lambda con, lg, setts: next(halves))
    monkeypatch.setattr(rp_ref, "rows_for", lambda con, lg: list(rows) if lg == "nba" else [])
    new = _built(None, monkeypatch, old=False)
    old = _built(None, monkeypatch, old=True)
    assert new == old
    nba = new["leagues"]["nba"]
    assert nba["head_to_head"]["rows"] and nba["recent"] and len(nba["models"]) == 4


@pytest.mark.parametrize("seed", range(3))
def test_spill_build_equals_the_list_rules_with_the_audit_1121_rules_on(monkeypatch, seed):
    """SPORTS_LATEST_LINE + NBA_MVLEAN1 on: the spill build (SQL lat / opn tables) equals the list build when the list
    build uses the current representative() and grades() (latest line + mvlean-1 open line)."""
    monkeypatch.setenv("SPORTS_LATEST_LINE", "1")
    monkeypatch.setenv("NBA_MVLEAN1", "1")               # default off: turned on here with an effective time
    monkeypatch.setenv("NBA_MVLEAN1_FROM", "2026-01-01T00:00:00Z")
    rows = _hand_rows(seed)
    for i, r in enumerate(rows):                          # spread the runs so lines are superseded / moved
        r["forecast_at"] = f"2026-11-01T{i % 24:02d}:00:00Z"
    # one prop whose PrizePicks line moved +1.0 toward the Over Scout graded A: mvlean-1 shows it as Lean on the board,
    # so the record's Grades table AND its recent list must say Lean (both builds)
    base = dict(rows[0], event_id="nba:99", player_id="p-mv", stat="points", model="sm-1", platforms=["prizepicks"],
                mean=24.0, p_over=0.66, p_under=0.34, research_only=True, status="settled", outcome="over",
                lean="over", lean_result="win", lean_p=0.66, settled_at="2026-11-09T04:00:00Z", error=1.0)
    rows += [dict(base, forecast_id="fc-mv-open", line=19.5, forecast_at="2026-10-31T00:00:00Z"),
             dict(base, forecast_id="fc-mv-now", line=20.5, forecast_at="2026-11-02T00:00:00Z")]
    monkeypatch.setattr(rp, "settlement_chunks", lambda con, lg: iter([[1], [2]] if lg == "nba" else []))
    halves = iter([rows[:70], rows[70:]])
    monkeypatch.setattr(rp, "_rows_from", lambda con, lg, setts: next(halves))
    monkeypatch.setattr(rp_ref, "rows_for", lambda con, lg: list(rows) if lg == "nba" else [])
    monkeypatch.setattr(rp_ref, "representative", rp.representative)
    monkeypatch.setattr(rp_ref, "grades", rp.grades)
    monkeypatch.setattr(rp_ref, "recent", rp.recent)      # mvlean-1: the recent list shows the board's Lean too
    new = _built(None, monkeypatch, old=False)
    old = _built(None, monkeypatch, old=True)
    assert new == old
    mv = [r for r in new["leagues"]["nba"]["recent"] if r["forecast_id"] == "fc-mv-now"]
    assert mv and mv[0]["grade"] == "Lean" and mv[0].get("grade_why")       # recent list = the board's Lean
    off = dict(os.environ, SPORTS_LATEST_LINE="0")
    monkeypatch.setattr(os, "environ", off)
    assert len(rp.representative(rows)) >= len([1 for _ in rp.one_per_prop(rp.representative(rows))])


@pytest.mark.parametrize("seed", range(4))
def test_streamed_summaries_equal_the_list_functions(seed):
    """Summary / Calibration / Bands / Grades / HeadToHead fed row by row = the ecfa81a list functions."""
    rows = _hand_rows(seed)
    for r in rows[::7]:
        r["event_id"] = None                              # the forecast id is the game key then
    assert rp.summarize(rows) == rp_ref.summarize(rows)
    assert rp.calibration(rows) == rp_ref.calibration(rows)
    assert rp.bands(rows) == rp_ref.bands(rows)
    assert rp.grades(rows, "nba") == rp_ref.grades(rows, "nba")
    assert rp.grades(rows) == rp_ref.grades(rows)
    assert rp.head_to_head(rows) == rp_ref.head_to_head(rows)
    reps = rp_ref.representative(rows)
    assert rp.one_per_prop(reps, pref=("mi-1", "sm-1")) == rp_ref.one_per_prop(reps, pref=("mi-1", "sm-1"))


def _peak_mb(fn):
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1] / 1e6
    finally:
        tracemalloc.stop()


def test_results_public_memory_does_not_grow_with_the_season(monkeypatch):
    """Peak traced memory of one league's block at 6 and at 18 games: the ecfa81a list build grows with every game,
    the spill build by a small fraction of it (per-game numbers and the means' value lists only)."""
    monkeypatch.setattr(snapshots, "RESULTS_EVENTS_PER_PASS", 1)
    peaks = {}
    root = config.DATA_DIR
    for games in (6, 18):
        monkeypatch.setattr(config, "DATA_DIR", root / f"g{games}")
        config.DATA_DIR.mkdir()
        with store.opened() as con:
            season_world.build(con, leagues=("nba",), events=games, players=6, stats=3, revisions=3, extra_settled=1)
        with store.opened(read_only=True) as con:
            peaks[games] = (_peak_mb(lambda: rp._league(con, "nba", NOW)),
                            _peak_mb(lambda: rp_ref.representative(rp_ref.rows_for(con, "nba"))))
    new_growth, old_growth = peaks[18][0] - peaks[6][0], peaks[18][1] - peaks[6][1]
    assert old_growth > 2.0, peaks                       # the old path holds every row of the season
    assert new_growth < 0.25 * old_growth, peaks


def test_build_releases_a_league_before_the_next(season, monkeypatch):
    """The NBA block is a finished dict before NFL's rows are read: nothing of NBA's row lists stays bound."""
    seen = []
    real = rp.league_block

    def spy(con, lg, now, rows):
        import gc
        gc.collect()
        seen.append((lg, sum(1 for o in gc.get_objects() if isinstance(o, dict) and "lean_result" in o
                             and "forecast_id" in o)))
        return real(con, lg, now, rows)
    monkeypatch.setattr(rp, "league_block", spy)
    with store.opened(read_only=True) as con:
        rp.build(con, now=NOW)
    assert [x[0] for x in seen] == ["nba", "nfl"]
    nba_recent = len(json.loads(rp.path().read_text(encoding="utf-8"))["leagues"]["nba"]["recent"])
    assert seen[1][1] <= nba_recent + 5, seen         # only the NBA 'recent' block (copies) may remain


def test_spill_file_is_removed_and_stale_ones_swept(season, monkeypatch):
    import os
    import time
    from sports import spill
    d = config.DATA_DIR / "tmp"
    d.mkdir(exist_ok=True)
    old = d / "results-public-1-dead.db"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - spill.STALE_S - 60,) * 2)
    fresh = d / "results-public-2-live.db"
    fresh.write_bytes(b"x")
    with store.opened(read_only=True) as con:
        rp.build(con, now=NOW)
    assert not old.exists() and fresh.exists()            # a running build's file is never touched
    assert sorted(p.name for p in d.iterdir()) == [fresh.name]


# ------------------------------------------------------------------------------------------ results ledger (settle-post)
def test_ledger_file_is_identical_to_the_old_build(season, tmp_path):
    voids = 0
    with store.opened(read_only=True) as con:
        for lg in ("nba", "nfl"):
            out_new, new = _ledger(con, lg, tmp_path / "new", old=False)
            out_old, old = _ledger(con, lg, tmp_path / "old", old=True)
            assert out_new == out_old and new == old, lg
            assert new["total_rows"] >= len(new["rows"]) > 0
            assert new["head_to_head"]["rows"] and len(new["summary"]) >= 4
            voids += sum(1 for r in new["rows"] if r["status"] != "settled")
    assert voids                                         # void / unresolved rows are in the world too


def test_ledger_top_rows_and_ties_match_the_sort(monkeypatch):
    """More rows than the kept RESULTS_ROWS, equal (settled_at, forecast id) pairs and missing settled_at: the heap keeps
    exactly results_from_rows' first rows in its order; the summary and head_to_head match on random rows."""
    monkeypatch.setattr(snapshots, "RESULTS_ROWS", 25)
    rng = random.Random(5)
    rows = []
    for i in range(400):
        g = rng.randrange(12)
        st = rng.choice(["settled"] * 5 + ["void", "unresolved"])
        out = rng.choice(["over", "under", "push"]) if st == "settled" else None
        po = round(rng.uniform(0.2, 0.8), 4)
        err = None if rng.random() < 0.1 else round(rng.uniform(-6, 6), 4)
        rows.append({"forecast_id": f"f{rng.randrange(150)}", "model_version": rng.choice(["sm-1", "mi-1", "mc-1",
                                                                                         "mkt-fanduel"]),
                     "event_id": None if rng.random() < 0.05 else f"nba:{g}", "player_id": f"p{rng.randrange(4)}",
                     "player_key": "k", "stat": rng.choice(["points", "assists"]), "line": rng.choice([10.5, 11.5]),
                     "platform": "prizepicks", "offer_id": "o", "forecast_at": f"2026-11-0{rng.randrange(1, 4)}T00:00Z",
                     "projection_mean": 11.0, "p_over": po, "p_under": round(1 - po, 4), "p_push": 0.0,
                     "status": st, "outcome": out, "actual": 12.0, "p_outcome": None,
                     "brier": None if out is None else round(rng.random(), 6),
                     "log_loss": None if out is None else round(rng.random() * 2, 6),
                     "brier_binary": None if out in (None, "push") else round(rng.random() / 2, 6),
                     "log_loss_binary": None, "error": err if st == "settled" else None, "settlement_ids": ["s"],
                     "settlement_revision": 1, "rule_version": "t",
                     "settled_at": None if rng.random() < 0.1 else f"2026-11-0{rng.randrange(1, 4)}T05:00:00Z",
                     "payouts": [{"side": "over", "stake": 1, "payout": 2}] * rng.randrange(3)})
    meta = {"version": "x", "league": "nba"}
    new = snapshots._ledger_streamed(iter([dict(r) for r in rows]), meta)
    old_rows = [dict(r) for r in rows]
    old = ledger_ref.results_from_rows(old_rows, meta)
    old["rows"] = old["rows"][:25]
    assert new == old
    assert json.dumps(new) == json.dumps(old)          # same key order too


def test_ledger_memory_does_not_grow_with_the_season(monkeypatch):
    """Peak traced memory of the ledger at 6 and 18 games: the ecfa81a ledger holds every row (grows with each game),
    the streamed one keeps the newest rows and per-game totals."""
    monkeypatch.setattr(snapshots, "RESULTS_EVENTS_PER_PASS", 1)
    root = config.DATA_DIR
    peaks = {}
    for games in (6, 18):
        monkeypatch.setattr(config, "DATA_DIR", root / f"g{games}")
        config.DATA_DIR.mkdir()
        with store.opened() as con:
            season_world.build(con, leagues=("nba",), events=games, players=6, stats=3, revisions=3, extra_settled=1)
        with store.opened(read_only=True) as con:
            peaks[games] = (_peak_mb(lambda: snapshots.write_results(con, "nba", now=NOW, base=root / "n")),
                            _peak_mb(lambda: ledger_ref.write_results(con, "nba", now=NOW, base=root / "o")))
    new_growth, old_growth = peaks[18][0] - peaks[6][0], peaks[18][1] - peaks[6][1]
    assert old_growth > 4.0, peaks
    assert new_growth < 0.15 * old_growth, peaks


# ------------------------------------------------------------------------------------------- sheets / scheduler
def test_sheets_job_skips_results_and_results_public_is_its_own_job(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    J = scheduler.default_jobs()
    assert "--skip-results" in J["sheets"]["argv"]
    rp_job = J["results-public"]
    assert rp_job["argv"][-3:] == ["--only", "results", "--force"] and rp_job["cls"] == scheduler.HEAVY
    assert rp_job["every_s"] == rp.REFRESH_S and rp_job["locks"] == ("results-public", "season-results")
    assert scheduler.Scheduler._pairable(J["sheets"], rp_job)       # never one heavy slot held by both


def test_settle_runs_without_post_and_starts_settle_post(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    J = scheduler.default_jobs()
    s, p = J["settle"], J["settle-post"]
    assert "--no-post" in s["argv"] and s["then"] == ("settle-post",)
    assert p["argv"][2:4] == ["sports.worker", "settle-post"] and p["cls"] == scheduler.HEAVY
    # stall 2026-11-09: no longer a longer kill than settle's - the scorecards stop at --budget-s (resumed next run)
    # and the ledger follows well inside timeout_s; settle-post never starts while settle is due
    budget = float(p["argv"][p["argv"].index("--budget-s") + 1])
    assert "model-daily" in p["locks"] and budget + 600 <= p["timeout_s"] and p["yields_to"] == ("settle",)
    assert not scheduler.Scheduler._pairable(s, p)                  # never beside the settle run it follows
    assert not scheduler.Scheduler._pairable(p, J["grades-daily"])  # nor the daily fit reading learning.db
    assert scheduler._child_env(p)["SPORTS_WORKER_MAX_VM_MB"] == scheduler._child_env(s)["SPORTS_WORKER_MAX_VM_MB"]


def _prod_scheduler(monkeypatch, names=None):
    """Production sizing (Standard 2 GB, owner budget 1,200 MB -> cap_scale 1.8, two heavy slots), fake children."""
    import copy
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    monkeypatch.setenv("SPORTS_LEAGUES", "nba nfl")
    monkeypatch.setenv("SPORTS_INGEST", "1")
    for k in ("SPORTS_MAX_HEAVY", "SPORTS_HEAVY_SLOTS", "SPORTS_JOB_SETTLE_POST_CAP_MB"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(scheduler, "JOBS", {})
    monkeypatch.setattr(scheduler, "STATS", copy.deepcopy(scheduler.STATS))
    J = scheduler.default_jobs()
    if names:
        J = {k: J[k] for k in names}
    assert scheduler.cap_scale() == pytest.approx(1.8)
    return J


def test_settle_post_reservation_leaves_a_board_build_and_the_light_jobs_room(monkeypatch):
    """Review 2026-11-08: settle-post reserved 340 x 1.8 = 612 MB (~8x its measured 56-76 MB peak). While it ran a
    board build (540) did not fit once books or picks-ingest (108 each, every 60 s) was running, and neither light
    job fit beside settle-post + a board build. The old settle reservation (432) allowed all of it."""
    J = _prod_scheduler(monkeypatch)
    sch = scheduler.Scheduler(jobs=J, budget=scheduler.budget_mb(), max_heavy_n=scheduler.max_heavy())
    assert J["settle-post"]["cap_mb"] <= J["settle"]["cap_mb"]

    def fits(running, name):
        sch.running = {r: (None, 0.0) for r in running}
        try:
            return sch._fits(name)
        finally:
            sch.running = {}
    for lg in ("nba", "nfl"):
        build = f"ui-snapshots-{lg}"
        assert fits(["settle-post", "books"], build) and fits(["settle-post", "picks-ingest"], build)
        assert fits(["settle-post", build], "books") and fits(["settle-post", build], "picks-ingest")
        assert fits(["settle-post", build, "books"], "picks-ingest")
        assert fits(["settle-post"], f"ingest-{lg}")
        assert fits(["settle-post", f"ingest-{lg}", "books"], "picks-ingest")


def test_heavy_maintenance_jobs_declare_locks_and_get_a_slot_beside_back_to_back_board_builds(monkeypatch):
    """Review 2026-11-08: a lockless heavy job starts only when NO heavy job runs, and a freed slot is always
    backfilled by the next pairable job - with a board build always running, mapping-audit / archive never started
    (mapping-audit sat 4 h). settle-post and results-public, both full-season builds, may not fill both slots."""
    J = _prod_scheduler(monkeypatch)
    pair = scheduler.Scheduler._pairable
    lockless = sorted(n for n, sp in J.items() if sp["cls"] == scheduler.HEAVY and not sp.get("locks"))
    assert lockless == ["model-learn", "model-snapshot"], lockless          # SPORTS_TRAIN only
    for n in ("mapping-audit", "archive", "archive-verify"):
        assert pair(J[n], J["ui-snapshots-nba"]) and pair(J[n], J["ui-snapshots-nfl"]) and pair(J[n], J["sheets"])
    for writer in ("ingest-nba", "ingest-nfl", "ingest-kalshi", "settle", "history"):
        assert not pair(J["archive"], J[writer]) and not pair(J["archive-verify"], J[writer])   # store writers
    assert not pair(J["archive"], J["archive-verify"])
    assert not pair(J["results-ledger"], J["results-public"])   # settlepost-1120: the ledger left settle-post

    names = ("ui-snapshots-nba", "ui-snapshots-nfl", "mapping-audit", "archive", "archive-verify")
    J = _prod_scheduler(monkeypatch, names)

    class P:
        n = 0

        def __init__(self):
            P.n += 1
            self.pid, self.rc = 70000 + P.n, None

        def poll(self):
            return self.rc
    monkeypatch.setattr(scheduler, "rss_mb", lambda pid: 10.0)
    monkeypatch.setattr(scheduler, "spawn", lambda spec: P())
    clock = [1_794_000_000.0]
    sch = scheduler.Scheduler(jobs=J, budget=scheduler.budget_mb(), now=lambda: clock[0], max_heavy_n=2)
    for n in names:
        scheduler.JOBS[n]["next_start"] = clock[0]
    builds = ("ui-snapshots-nba", "ui-snapshots-nfl")
    for _ in range(400):                       # 400 x 5 s; a board build ends only while another heavy job still runs,
        for n, (p, t0) in list(sch.running.items()):    # so a heavy slot is never empty (production at 60 s cadence)
            others = [m for m, (q, _t) in sch.running.items() if m != n and q.rc is None]
            if clock[0] - t0 >= 40 and (n not in builds or others):
                p.rc = 0
        sch.tick()
        for n in builds:                                                    # always due again at once
            if n not in sch.running:
                scheduler.JOBS[n]["next_start"] = min(scheduler.JOBS[n]["next_start"], clock[0])
        heavy = [n for n in sch.running if J[n]["cls"] == scheduler.HEAVY]
        assert len(heavy) <= 2 and sch.reserved() <= sch.budget
        clock[0] += 5
    for n in ("mapping-audit", "archive", "archive-verify"):
        assert scheduler.JOBS[n]["starts"] >= 1, (n, dict(scheduler.JOBS[n]))
    assert scheduler.JOBS["ui-snapshots-nba"]["starts"] >= 10 and scheduler.JOBS["ui-snapshots-nfl"]["starts"] >= 10


def test_sheets_cli_skip_results_and_only_results(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTS_INGEST", "1")
    calls = []
    monkeypatch.setattr(sheets, "build", lambda lg, now=None: calls.append(("sheets", lg)) or {})
    from sports import without
    monkeypatch.setattr(without, "build_if_stale", lambda con, lg, force=False: calls.append(("without", lg)) or {})
    monkeypatch.setattr(rp, "build_if_stale", lambda con, force=False: calls.append(("results", force)) or {})
    with store.opened():
        pass
    sheets.main(["build", "nba", "--skip-results"])
    assert ("results", False) not in calls and ("sheets", "nba") in calls and ("without", "nba") in calls
    calls.clear()
    sheets.main(["build", "nba", "nfl", "--only", "results", "--force"])
    assert calls == [("results", True)]
    assert json.loads((config.DATA_DIR / "results_stages.json").read_text())["last_ok"]["steps"][0][0] == "results"
    assert "results" not in json.dumps(json.loads((config.DATA_DIR / "sheets_stages.json").read_text())["last_ok"])


# ------------------------------------------------------------------------------------------------ settle split
def _fake_espn(monkeypatch):
    from sports import adapters, espn
    from sports.tests import helpers as h
    monkeypatch.setattr(espn, "summary_and_raw", lambda lg, eid, refresh=False: ({"completed": True}, None))

    def box(summ, lg, event_id, observed_at):
        b = adapters.Batch()
        b.add(h.stat(pid="nba:player:espn:3134907", part="pts", value=25.0, eid=event_id, observed_at=observed_at))
        return b
    monkeypatch.setattr(adapters, "espn_box", box)


def _settle_world(con, eid="nba:espn:1", oid="pp:1"):
    from sports.tests import helpers as h
    store.put(con, h.event(eid=eid, start="2026-10-23T23:00:00Z"))
    orid = store.put(con, h.offer(oid=oid, event_id=eid, line=20.5, listed_start_utc="2026-10-23T23:00:00Z"))
    store.put(con, h.forecast(event_id=eid, line=20.5, offer_snapshot_ids=(orid["record_id"],), model_version="sm-1",
                              chosen_side="over", p_over=0.55, p_under=0.45, p_push=0.0, p_over_given_no_push=0.55,
                              p_under_given_no_push=0.45))


def _settlements(con):
    return sorted((s.settlement_id, s.platform, s.offer_id, s.side, s.status, s.outcome, s.source_value, s.revision)
                  for _, s in store.settlements(con))


def test_settle_no_post_writes_the_same_settlements_and_post_does_the_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("SPORTS_INGEST", "1")
    from sports import flags
    monkeypatch.setattr(flags, "on", lambda name: True)
    monkeypatch.setattr(flags, "require", lambda name: None)
    _fake_espn(monkeypatch)
    monkeypatch.setattr(worker, "now_utc", lambda: "2026-10-24T06:00:00Z")
    full_dir, split_dir = tmp_path / "full", tmp_path / "split"
    results = {}
    from sports.model import learning
    real_ws, real_ls = snapshots.write_results, learning.on_settled
    for d, no_post in ((full_dir, False), (split_dir, True)):
        d.mkdir()
        monkeypatch.setattr(config, "DATA_DIR", d)         # each run its own learning.db / snapshots
        with store.opened() as con:
            _settle_world(con)
            touched = []
            monkeypatch.setattr(snapshots, "write_results", lambda *a, **k: touched.append("results") or real_ws(*a, **k))
            monkeypatch.setattr(learning, "on_settled", lambda *a, **k: touched.append("learning") or real_ls(*a, **k))
            out = worker.cmd_settle(argparse.Namespace(leagues=["nba"], since=None, no_post=no_post), con)
            assert ("results" in out) is (not no_post) and bool(touched) is (not no_post)
            stages = json.loads((config.DATA_DIR / "settle_stages.json").read_text())
            assert [x[0] for x in stages["stage_s"]] == (["settle"] if no_post else
                                                         ["settle", "model_learning", "results"])
            if no_post:
                post = worker.cmd_settle_post(argparse.Namespace(leagues=["nba"]), con)
                assert touched == ["learning"]                      # settlepost-1120: scorecards only
                assert post["model_learning"]["scorecard_rows_added"] == 1
                ps = json.loads((config.DATA_DIR / "settle_post_stages.json").read_text())
                assert [x[0] for x in ps["stage_s"]] == ["model_learning"] and ps["finished"]
                assert ps["model_learning"]["scorecard_rows_added"] == 1        # counters for ops
                led = worker.cmd_results_ledger(argparse.Namespace(leagues=["nba"]), con)   # its own job now
                assert touched == ["learning", "results"]
                assert led["results"]["nba"]["settled_by_model"] == {"sm-1": 1}
            else:
                assert out["results"]["nba"]["settled_by_model"] == {"sm-1": 1}
                assert out["model_learning"]["scorecard_rows_added"] == 1
            results[no_post] = _settlements(con)
    assert results[True] == results[False] and results[True]


def test_settle_post_cli_is_wired():
    with pytest.raises(SystemExit):                       # the parser knows the command (argparse exits on --help)
        worker.main(["settle-post", "--help"])


# ------------------------------------------------------------------------------------- scorecard skip / resume
def _scorecard_state():
    from sports.model import learning, shadow
    lcon = learning.db()
    sc = sorted(lcon.execute("SELECT * FROM scorecard").fetchall())
    dg = sorted(lcon.execute("SELECT forecast_id, settlement_id, kind, detail, reviewed FROM diagnostics").fetchall())
    lcon.close()
    scon_ = shadow.db()
    sh = sorted(scon_.execute("SELECT * FROM shadow_scorecard").fetchall())
    scon_.close()
    return sc, dg, sh


def _add_shadow_rows(con):
    """shadow forecasts (sm-2) on a subset of the world's offers, the way the recorder stores them."""
    from sports.model import shadow
    sdb = shadow.db()
    rows = con.execute("SELECT record_id, event_id, league, stat, platform, line FROM records WHERE kind='offer' AND "
                       "platform IN ('prizepicks','underdog') ORDER BY rid").fetchall()
    for i, r in enumerate(rows[::3]):
        sdb.execute("INSERT OR IGNORE INTO shadow_forecasts (shadow_id, model_version, league, stat, event_id, "
                    "offer_record_id, platform, line, forecast_at, chosen_side, p_side, p_push) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"sh-{i}", "sm-2", r["league"], r["stat"], r["event_id"], r["record_id"], r["platform"],
                     r["line"], "2026-10-21T20:00:00Z", "over" if i % 2 else "under", 0.55, 0.0))
    sdb.commit()
    sdb.close()


def _correct_one(con):
    """A provider correction (new settlement revision) and a late forecast on an already scored event."""
    from sports.tests import helpers as h
    s = next(s for _, s in store.settlements(con) if s.status in ("win", "loss") and s.platform == "prizepicks")
    store.put(con, replace(s, settlement_id=s.settlement_id + "-c", revision=s.revision + 1,
                           supersedes=s.settlement_id, source_value=(s.source_value or 0) + 7,
                           outcome="over", status="win" if s.side == "over" else "loss",
                           known_at="2026-11-01T00:00:00Z", observed_at="2026-11-01T00:00:00Z"))
    rid = con.execute("SELECT record_id FROM records WHERE kind='offer' AND platform='underdog' AND event_id=? "
                      "ORDER BY rid DESC LIMIT 1", (s.event_id,)).fetchone()[0]
    o = store.get(con, rid)
    store.put(con, h.forecast(forecast_at="2026-10-30T20:00:00Z", league=o.league, event_id=o.event_id,
                              player_id=o.player_id, player_key=o.player_key, stat=o.stat, line=o.line,
                              offer_snapshot_ids=(rid,), input_snapshot_ids=(rid, "late"), model_version="sm-1",
                              projection_mean=o.line + 2, p_over=0.6, p_under=0.4, p_push=0.0,
                              p_over_given_no_push=0.6, p_under_given_no_push=0.4,
                              sides_available=tuple(o.offered_sides()), chosen_side=o.offered_sides()[0]))


def _run_sequence(monkeypatch, skip, kill_after=None):
    """on_settled (and shadow.settle) twice, a correction, twice more -> final tables."""
    from sports.model import learning
    if not skip:                                          # the original full re-read: a mark never matches
        n = iter(range(10 ** 9))
        real = learning.event_sig
        monkeypatch.setattr(learning, "event_sig", lambda scon, ev: f"{real(scon, ev)}#{next(n)}")
    with store.opened() as con:
        if kill_after is not None:                        # killed part way: the events done so far stay done
            real_sig = learning.event_sig
            calls = []

            def dying(scon, ev):
                calls.append(ev)
                if len(calls) > kill_after:
                    raise KeyboardInterrupt("killed")
                return real_sig(scon, ev)
            monkeypatch.setattr(learning, "event_sig", dying)
            with pytest.raises(KeyboardInterrupt):
                learning.on_settled(con, now=NOW)
            monkeypatch.setattr(learning, "event_sig", real_sig)
            r = learning.on_settled(con, now=NOW)
            assert r["events_unchanged"] == kill_after
        outs = [learning.on_settled(con, now=NOW), learning.on_settled(con, now=NOW)]
        _correct_one(con)
        outs += [learning.on_settled(con, now=NOW), learning.on_settled(con, now=NOW)]
    return _scorecard_state(), outs


@pytest.mark.parametrize("kill_after", [None, 3])
def test_scorecards_with_event_skip_equal_the_full_reread(monkeypatch, tmp_path, kill_after):
    states = {}
    for mode in ("full", "skip"):
        d = tmp_path / mode
        d.mkdir()
        with monkeypatch.context() as m:
            m.setattr(config, "DATA_DIR", d)
            with store.opened() as con:
                season_world.build(con, events=4, players=4, stats=2, revisions=2, extra_settled=3)
                _add_shadow_rows(con)
            states[mode] = _run_sequence(m, skip=mode == "skip", kill_after=kill_after if mode == "skip" else None)
    (full, fouts), (skip, souts) = states["full"], states["skip"]
    assert full[0] and full[2]                            # scorecard rows and shadow rows exist
    assert any(r[-1] == 1 for r in full[0])               # the correction superseded a row
    assert skip == full
    # the same rows were added per run; the later runs skipped unchanged events
    k = 0 if kill_after is None else 1
    assert [o["scorecard_rows_added"] for o in souts[k:]] == [o["scorecard_rows_added"] for o in fouts[k:]]
    assert [o["shadow"] for o in souts[k:]] == [o["shadow"] for o in fouts[k:]]
    if kill_after is not None:                            # its first two runs were the killed one + the resume
        return
    assert souts[1]["events_unchanged"] == souts[1]["events_read"] > 0
    assert souts[3]["events_unchanged"] == souts[3]["events_read"]
    assert 0 < souts[2]["events_unchanged"] < souts[2]["events_read"]     # only the corrected event re-read


def test_unchanged_events_decode_nothing(monkeypatch, season):
    from sports.model import learning
    with store.opened() as con:
        first = learning.on_settled(con, now=NOW)
        assert first["scorecard_rows_added"] > 0
        decoded = []
        real = store._rec
        monkeypatch.setattr(store, "_rec", lambda row: (decoded.append(1), real(row))[1])
        again = learning.on_settled(con, now=NOW)
    assert again["scorecard_rows_added"] == 0 and again["events_unchanged"] == again["events_read"]
    assert decoded == []


# ------------------------------------------------------------------------------------- model-forward + books
def test_forward_run_with_a_concurrent_books_writer_exits_clean_and_writes_the_same(fworld, monkeypatch, tmp_path):
    from sports.model import fwd
    scon, *_ = fworld
    path = scon.execute("PRAGMA database_list").fetchone()[2]
    other = store.connect(path)                          # the books job: another process, same store
    real = store.put
    fired = []

    def put(con, rec, *a, **k):
        if con is scon and rec.KIND == "forecast" and not fired:
            fired.append(1)
            store.quarantine(other, "offer", "fanduel", "books-commit", "test: a books write mid-run")
        return real(con, rec, *a, **k)
    monkeypatch.setattr(store, "put", put)
    out = fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    other.close()
    assert fired and out["leagues"]["nba"]["written"]["sm-1"] == 1 and out["forecasts_added"] == 1
    from sports.model import forward
    for _, f in store.forecasts(scon, model_version="sm-1"):    # what was written is what the model makes
        orig, again = forward.replay(f.forecast_id, scon)
        assert again == orig


def test_forward_offer_read_is_the_same_rows_as_the_stream(fworld):
    """list(iter_offers) is the stream's rows in the stream's order (the forecasts depend only on the offers)."""
    scon, *_ = fworld
    a = [(rid, o.to_dict()) for rid, o in store.iter_offers(scon, league="nba", stat="points", limit=500000)]
    b = [(rid, o.to_dict()) for rid, o in list(store.iter_offers(scon, league="nba", stat="points", limit=500000))]
    assert a == b and a


def test_base_stream_pattern_fails_with_a_concurrent_commit(tmp_path):
    """The mechanism (so the regression test above means something): writing inside an open SELECT on a WAL store
    after another connection committed fails at once with SQLITE_BUSY_SNAPSHOT; reading first does not."""
    p = tmp_path / "w.db"
    a, b = store.connect(p), store.connect(p)
    a.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    a.execute("CREATE TABLE IF NOT EXISTS u (x INTEGER)")
    a.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(50)])
    a.commit()
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        for i, (x,) in enumerate(a.execute("SELECT x FROM t GROUP BY x ORDER BY x")):
            if i == 1:
                b.execute("INSERT INTO u VALUES (1)")
                b.commit()
            with store.write_tx(a):
                a.execute("INSERT INTO u VALUES (?)", (x,))
    for i, (x,) in enumerate(list(a.execute("SELECT x FROM t GROUP BY x ORDER BY x"))):
        if i == 1:
            b.execute("INSERT INTO u VALUES (2)")
            b.commit()
        with store.write_tx(a):
            a.execute("INSERT INTO u VALUES (?)", (x,))
    a.close()
    b.close()



# ------------------------------------------------------------------------------------ archived offer revisions
def test_links_through_archived_offers_read_no_archive_file_and_match(monkeypatch, tmp_path):
    """settled_forecast_links finds an archived offer revision's identity from the metadata view (no file decode);
    results_public.json and the results ledger are the same before and after the offers move to the archive."""
    from datetime import datetime, timezone
    from sports import archive
    with store.opened() as con:
        season_world.build(con, events=3, players=3, stats=2, revisions=2, extra_settled=2)
        before = _built(con, monkeypatch, old=False)
        led0 = {lg: snapshots.write_results(con, lg, now=NOW, base=tmp_path / "a") for lg in ("nba", "nfl")}
        out = archive.run_pass(con, now=datetime(2026, 11, 8, 12, tzinfo=timezone.utc))
        assert out["error"] is None and out["rows"] > 100, out
        ids = [r[0] for r in con.execute("SELECT record_id FROM records_meta_all WHERE kind='offer' "
                                          "AND rid NOT IN (SELECT rid FROM records) LIMIT 50")]
        assert ids
        for rid in ids:                                    # same identity as the full-row lookup it replaces
            m = store.row_meta(con, rid)
            assert tuple(archive.identity_by_record_id(con, rid)) == (m["kind"], m["source"], m["source_key"])
        assert archive.identity_by_record_id(con, "offer:nope:x:r1:0123456789") is None
    with store.opened(read_only=True) as con:
        decodes = []
        real = archive.FILES.rows
        with monkeypatch.context() as m:
            m.setattr(archive.FILES, "rows", lambda *a, **k: decodes.append(1) or real(*a, **k))
            setts = snapshots._event_settlements(con, ["nba:espn:900"])
            links, _ = snapshots.settled_forecast_links(con, setts)
        assert links and decodes == []
        with monkeypatch.context() as m:                    # the full-row lookup it replaces decodes archive files
            m.setattr(archive.FILES, "rows", lambda *a, **k: decodes.append(1) or real(*a, **k))
            m.setattr(archive, "identity_by_record_id", lambda c, rid: store.row_meta(c, rid))
            assert snapshots.settled_forecast_links(con, setts)[0] == links
        assert decodes
        after = _built(con, monkeypatch, old=False)
        with monkeypatch.context() as m:                # the ecfa81a reference = the audit_1121 rules off
            m.setenv("SPORTS_LATEST_LINE", "0")
            m.setenv("NBA_MVLEAN1", "0")
            after_off = _built(con, monkeypatch, old=False)
            old_after = _built(con, monkeypatch, old=True)
        led1 = {lg: snapshots.write_results(con, lg, now=NOW, base=tmp_path / "b") for lg in ("nba", "nfl")}
    assert after == before, "archive changed the record"
    assert after_off == old_after
    assert led1 == led0
    for lg in ("nba", "nfl"):
        a = json.loads((tmp_path / "a" / lg / "results.json").read_text())
        b = json.loads((tmp_path / "b" / lg / "results.json").read_text())
        a.pop("built_at"), b.pop("built_at")
        assert a == b and a["rows"]
