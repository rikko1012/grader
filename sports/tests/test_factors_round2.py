"""Factor round 2 (REGISTER.md A5): point-in-time guards for every new factor, registration before results, the sm-2
shadow path (bounded, never displayed, sm-1 untouched) and the grade-band validation report."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sports import features
from sports.model import datasets, featurize, featurize2, learning, shadow
from sports.model.frame import Frame, group_starts

ROOT = Path(__file__).resolve().parents[2]
from sports.tests.test_fwd import fworld  # noqa: E402,F401 - shared forward-run world (fixture)
from sports.tests.test_line_correctness_model import world  # noqa: E402,F401


# ------------------------------------------------------------------------------------------------ tiny history
def _nba_db(tmp_path):
    """Two teams, games every other day; player P1 (team AAA) plays all; P2 (BOS) misses game 4; game 5 upcoming."""
    con = datasets.connect(tmp_path / "h.db")
    starts = ["2025-11-01T00:00:00Z", "2025-11-03T00:00:00Z", "2025-11-05T00:00:00Z", "2025-11-07T00:00:00Z"]
    for i, s in enumerate(starts):
        gid = f"nba:espn:{i}"
        home, away = ("BOS", "NY") if i % 2 == 0 else ("NY", "BOS")
        con.execute("INSERT INTO games (league, game_id, season, season_type, start_utc, home, away, status, neutral) "
                    "VALUES ('nba', ?, 2026, 'regular', ?, ?, ?, 'final', 0)", (gid, s, home, away))
        for team, opp in (("BOS", "NY"), ("NY", "BOS")):
            con.execute("INSERT INTO nba_tg (game_id, team, opp, home, pts, fga, oreb, tov, fta, player_min) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?)", (gid, team, opp, int(team == home), 100 + 10 * i, 80, 10, 12, 20,
                                                  240))
        players = [("P1", "BOS", 1, 30)] + ([("P2", "BOS", 1, 32)] if i != 3 else []) + [("Q1", "NY", 1, 30)]
        for pid, team, st, mn in players:
            con.execute("INSERT INTO nba_pg (game_id, player_id, team, opp, home, starter, played, position, min, pts, "
                        "fga, fg3a, fg3m, fta, tov, ast, reb, oreb, dreb, ftm, fgm) VALUES (?,?,?,?,?,?,1,'G',?,20,15,"
                        "5,2,4,2,5,5,1,4,3,8)", (gid, pid, team, "NY" if team == "BOS" else "BOS", int(team == home),
                                                 st, mn))
    con.commit()
    return con


UP = [{"game_id": "nba:espn:9", "player_id": "P1", "team": "BOS", "opp": "NY", "home": 1,
       "start_utc": "2025-11-12T00:00:00Z", "position": "G", "season": 2026, "season_type": "regular"}]


def test_augment_never_changes_fr1_columns(tmp_path):
    con = _nba_db(tmp_path)
    df = featurize.nba_frame(con, 3, 10)
    before = {c: np.array(v, copy=True) for c, v in df.cols.items()}
    featurize2.augment(df, "nba", con, (3, 10))
    for c, v in before.items():
        assert np.array_equal(v, df.cols[c], equal_nan=True) if v.dtype != object else (v == df.cols[c]).all()
    assert {c for c in df.cols if c not in before} and all(c.startswith("x_") for c in df.cols if c not in before)


def test_team_context_uses_only_games_public_at_forecast(tmp_path):
    con = _nba_db(tmp_path)
    # forecast made on 2025-11-06 (as_of) for a game on 11-12: game 4 (11-07) is NOT public yet
    df = featurize.nba_frame(con, 3, 10, upcoming=UP, as_of="2025-11-06T12:00:00Z")
    featurize2.augment(df, "nba", con, (3, 10), as_of="2025-11-06T12:00:00Z")
    up = int(np.flatnonzero(df["is_up"] == 1)[0])
    ortg_asof = df["x_team_ortg"][up]
    df2 = featurize.nba_frame(con, 3, 10, upcoming=UP, as_of="2025-11-09T12:00:00Z")
    featurize2.augment(df2, "nba", con, (3, 10), as_of="2025-11-09T12:00:00Z")
    up2 = int(np.flatnonzero(df2["is_up"] == 1)[0])
    assert ortg_asof != df2["x_team_ortg"][up2]           # game 4 enters only once it is public
    # team_prior directly: a game started 3 h before the cut (not yet known_at) is excluded
    ts = np.array([0.0, 100.0, 200.0])
    v = np.array([1.0, 2.0, 30.0])
    got = featurize2.team_prior(np.array(["T"], dtype=object), np.array([150.0]), np.array(["T"] * 3, dtype=object),
                                ts, v, 10, 1)
    assert got[0] == pytest.approx(1.5)


def test_same_game_facts_unknown_forward_without_a_report(tmp_path):
    con = _nba_db(tmp_path)
    df = featurize.nba_frame(con, 3, 10, upcoming=UP, as_of="2025-11-10T00:00:00Z")
    featurize2.augment(df, "nba", con, (3, 10), as_of="2025-11-10T00:00:00Z")
    up = int(np.flatnonzero(df["is_up"] == 1)[0])
    assert np.isnan(df["x_starter_today"][up])               # no lineup source -> unknown, never "bench"
    assert np.isnan(df["x_absent_usage"][up])                # no injury report -> unknown, never "nobody out"
    df = featurize.nba_frame(con, 3, 10, upcoming=UP, as_of="2025-11-10T00:00:00Z")
    featurize2.augment(df, "nba", con, (3, 10), as_of="2025-11-10T00:00:00Z",
                       absences={("nba:espn:9", "BOS"): ["P2"]})
    up = int(np.flatnonzero(df["is_up"] == 1)[0])
    assert df["x_absent_usage"][up] > 0
    # history: P2 missed game 4 -> P1's row of game 4 has a positive absent-usage share; game 3 (all played) has 0
    rows = {df["game_id"][i]: df["x_absent_usage"][i] for i in range(len(df)) if df["player_id"][i] == "P1"}
    assert rows["nba:espn:3"] > 0 and rows["nba:espn:2"] == 0


def test_travel_and_schedule_known_in_advance_for_upcoming_rows(tmp_path):
    con = _nba_db(tmp_path)
    df = featurize.nba_frame(con, 3, 10, upcoming=UP, as_of="2025-11-10T00:00:00Z")
    featurize2.augment(df, "nba", con, (3, 10), as_of="2025-11-10T00:00:00Z")
    up = int(np.flatnonzero(df["is_up"] == 1)[0])
    assert np.isfinite(df["x_travel_km"][up])                 # previous game at BBB, this one at AAA
    assert df["x_travel_km"][up] > 0 and df["x_games_in_4"][up] == 0


def test_player_history_features_use_prior_rows_only():
    # ew features of row k use rows < k only (featurize2 builds them with frame.ew_prior)
    from sports.model.frame import ew_prior
    x = np.array([1.0, 2.0, 100.0])
    m, _ = ew_prior(x, group_starts(np.array(["a", "a", "a"], dtype=object)), 2)
    assert np.isnan(m[0]) and m[1] == 1.0 and m[2] < 2.0


def test_nfl_extras_and_teammates_out_point_in_time():
    df = Frame({"team": np.array(["A"] * 4, dtype=object), "game_id": np.array(["g1", "g1", "g2", "g3"], dtype=object),
                "player_id": np.array(["W1", "W2", "W1", "W1"], dtype=object), "ts": np.array([1.0, 1.0, 2.0, 3.0]),
                "is_up": np.array([0.0, 0.0, 0.0, 1.0]), "ew_target_share": np.array([0.1, 0.25, 0.1, 0.1]),
                "x_carry_share": np.array([0.0, 0.0, 0.0, 0.0])})
    to, _ = featurize2._teammates_out(df, {})
    assert to[0] == 0 and to[2] == pytest.approx(0.25)        # W2 (rotation) absent in g2
    assert np.isnan(to[3])                                     # upcoming without a report: unknown
    to, _ = featurize2._teammates_out(df, {("g3", "A"): ["W2"]})
    assert to[3] == pytest.approx(0.25)


def test_registry_round2_and_effects_are_joined():
    assert len(features.REGISTRY) == 100 and len(features.FR1_IDS) == 72
    r2 = [r for r in features.REGISTRY if r.get("round") == 2]
    assert r2 and all(r["status"] in (features.A, features.G, features.F) for r in r2)
    # round-1 candidate lists are unchanged by fr-2 (no second look at round-1 factors)
    assert [r["id"] for r in features.candidates("nfl", "pass_yds", "history")] == ["nfl.wind", "nfl.temp"]
    eff = {r["id"]: r["observed_effect"] for r in features.with_effects()}
    e = eff["nba.starter_today"]["points"]
    assert e["round"] == 2 and e["ci"][1] < 0 and e["in_model"].startswith("sm-2 challenger")
    assert eff["nfl.travel"]["pass_yds"]["passed"] is False and eff["nfl.travel"]["pass_yds"]["in_model"] is None


def _first_commit(path):
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%H %cI", "--", path], cwd=ROOT,
                         capture_output=True, text=True, timeout=30).stdout.split()
    return out[-2:] if out else None


def test_registration_committed_before_round2_results():
    reg = subprocess.run(["git", "log", "-S", "Amendment A5", "--format=%cI", "--", "sports/research/REGISTER.md"],
                         cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.split()
    res = _first_commit("sports/research/results/model_study2_nba_tune.json")
    if not reg or not res:
        pytest.skip("git history not available")
    assert min(reg) < res[1], "REGISTER A5 must be committed before any round-2 result"
    # the frozen challenger configs only for markets the registered joint test passed
    for sport in ("nba", "nfl"):
        st = json.loads((ROOT / f"sports/research/results/model_study2_{sport}_tune.json").read_text(encoding="utf-8"))
        cfg = json.loads((ROOT / f"sports/model/artifacts/model_config2_{sport}.json").read_text(encoding="utf-8"))
        assert set(cfg["markets"]) == {m for m, r in st["markets"].items() if r["challenger"]}


# ---------------------------------------------------------------------------------------------------- shadow
def test_shadow_artifacts_verify_and_tamper_refuses(tmp_path, monkeypatch):
    for sport in ("nba", "nfl"):
        a = shadow.load(sport)
        assert a and a["model_version"] == "sm-2" and set(a["markets"]) <= set(
            json.loads((shadow.ART_DIR / f"model_config2_{sport}.json").read_text(encoding="utf-8"))["markets"])
    bad = tmp_path / "sm2_nba.json"
    a = json.loads(shadow.art_path("nba").read_text(encoding="utf-8"))
    a["markets"]["points"]["beta"][0] += 0.01
    bad.write_text(json.dumps(a), encoding="utf-8")
    monkeypatch.setattr(shadow, "art_path", lambda s: bad)
    assert shadow.load("nba") is None
    monkeypatch.setenv("SPORTS_SHADOW", "0")
    assert not shadow.enabled()


class _O:
    def __init__(self, eid, pid, line):
        self.event_id, self.player_id, self.line, self.platform = eid, pid, line, "prizepicks"

    def offered_sides(self):
        return ("over", "under")


def test_shadow_recorder_writes_own_table_and_restores_the_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "game_context", lambda *a, **k: {})
    hp = ROOT / "sports" / "model" / "artifacts" / "history_ds1_2026-10-23.db.gz"
    if not hp.exists():
        pytest.skip("packaged history not present")
    import gzip
    import shutil
    db = tmp_path / "h.db"
    with gzip.open(hp, "rb") as g, open(db, "wb") as f:
        shutil.copyfileobj(g, f)
    hcon = datasets.connect(db)
    shadow.install_extras(db)
    up = [{"game_id": "nba:espn:t1", "player_id": "nba:player:espn:1966", "team": "LAL", "opp": "BOS", "home": 1,
           "start_utc": "2026-10-25T23:00:00Z", "position": "F", "season": 2027, "season_type": "regular"}]
    df = featurize.nba_frame(hcon, 3, 10, upcoming=up, as_of="2026-10-24T20:00:00Z", with_lags=False)
    cols_before = set(df.cols)
    i = int(np.flatnonzero(df["is_up"] == 1)[0])
    gk = (df["game_id"][i], df["player_id"][i])
    rec = shadow.Recorder("nba", hcon, "2026-10-24T20:00:00Z", path=tmp_path / "shadow.db")
    rec.context = {}
    n = rec.market("points", df, (3, 10), {gk: i}, [("offer:1", _O("nba:espn:t1", "nba:player:espn:1966", 20.5), gk,
                                                                     0.55)], {})
    assert n == 1 and set(df.cols) == cols_before                  # sm-1 frame exactly as before
    con = shadow.db(tmp_path / "shadow.db")
    r = con.execute("SELECT model_version, p_over, p_under, p_push, band, factors_json FROM shadow_forecasts").fetchone()
    assert r[0] == "sm-2" and abs(r[1] + r[2] + r[3] - 1) < 1e-9 and r[4] in ("A", "B", "C", "Lean")
    vals = json.loads(r[5])
    assert vals["nba.starter_today"] is None                        # no lineup source -> unknown, logged as such
    assert "injury_status" in vals
    rec.close()


def test_shadow_forecasts_never_reach_the_member_snapshot():
    src = (ROOT / "sports" / "model" / "pipeline.py").read_text(encoding="utf-8")
    snap = src[src.index("def write_snapshot"):src.index("def _chunks")]
    # the member files (forecasts_<lg>.json / pmf_<lg>.json) are built from store records + inputs only
    assert "shadow_forecasts" not in snap and "shadow.scorecard_rows" not in snap
    assert "shadow" not in (ROOT / "sports" / "snapshots.py").read_text(encoding="utf-8")
    fsrc = (ROOT / "sports" / "model" / "shadow.py").read_text(encoding="utf-8")
    assert "store.put" not in fsrc                                   # never a store record


def test_shadow_memory_is_one_markets_columns():
    # the recorder adds only fr-2 columns (x_*) and the F_ feature columns of ONE market, then drops them
    src = (ROOT / "sports" / "model" / "shadow.py").read_text(encoding="utf-8")
    assert "for c in [c for c in df.cols if c not in before]" in src and "del df.cols[c]" in src


# ------------------------------------------------------------------------------------------------ grade bands
def test_grade_band_report_groups_by_band_with_clustered_intervals():
    rows = []
    rng = np.random.default_rng(1)
    for i in range(400):
        p = [0.62, 0.57, 0.54, 0.51][i % 4]
        rows.append({"model_version": "sm-1", "league": "nba", "stat": "points", "event_id": f"e{i // 8}",
                     "p_side": p, "p_push": 0.0, "status": "win" if rng.random() < p else "loss"})
    rows.append({"model_version": "sm-1", "league": "nba", "stat": "points", "event_id": "e0", "p_side": 0.62,
                 "p_push": 0.05, "status": "push"})
    out = learning.grade_bands(rows)
    assert len(out) == 1
    b = {x["band"]: x for x in out[0]["bands"]}
    assert set(b) == {"A", "B", "C", "Lean"} and b["A"]["graded"] == 100 and b["A"]["pushes"] == 1
    assert b["A"]["mean_p"] == pytest.approx(0.62) and b["A"]["ci"][0] <= b["A"]["hit_minus_p"] <= b["A"]["ci"][1]
    assert b["Lean"]["mean_p"] == pytest.approx(0.51) and out[0]["ece_over_bands"] is not None
    assert "withheld" in out[0]["shown_grade"]


def test_grade_band_report_in_admin_learning_snapshot(tmp_path):
    from sports.model import pipeline
    pipeline.write_snapshot(now="2026-10-24T20:00:00Z")
    d = json.loads((pipeline.api_dir() / "admin_learning.json").read_text(encoding="utf-8"))
    assert "grade_bands" in d and "shadow" in d and d["shadow"]["display"] == "never (shadow only)"


def test_forward_run_records_shadow_and_leaves_sm1_identical(fworld, monkeypatch):
    from sports import store
    from sports.model import fwd
    scon, *_ = fworld
    monkeypatch.setenv("SPORTS_SHADOW", "0")
    out0 = fwd.run(["nba"], forecast_at="2026-01-31T21:00:00Z", log=lambda *x: None)
    (_, fc0), = store.forecasts(scon, model_version="sm-1")
    assert "shadow" not in out0["leagues"]["nba"]
    monkeypatch.setenv("SPORTS_SHADOW", "1")
    out1 = fwd.run(["nba"], forecast_at="2026-01-31T21:00:00Z", log=lambda *x: None)
    sh = out1["leagues"]["nba"]["shadow"]
    assert sh["model_version"] == "sm-2" and sh["written"] >= 1 and not sh["skipped"]
    assert out1["forecasts_added"] == 0                                 # sm-1 re-run: unchanged, nothing new
    (_, fc1), = store.forecasts(scon, model_version="sm-1")
    assert fc1.forecast_id == fc0.forecast_id and fc1.data_version == fc0.data_version
    assert not store.forecasts(scon, model_version="sm-2")              # never a store record (never displayed)
    con = shadow.db()
    assert con.execute("SELECT COUNT(*) FROM shadow_forecasts").fetchone()[0] == sh["written"]


def test_l2_locked_test_waits_for_registered_threshold_and_never_rescores(tmp_path, monkeypatch):
    con = datasets.connect(tmp_path / "h.db")
    man = json.loads(shadow.MANIFEST.read_text(encoding="utf-8"))
    freeze = man["freeze_at"]["nfl"]
    for i in range(47):
        con.execute("INSERT INTO games (league, game_id, start_utc, status) VALUES ('nfl', ?, ?, 'final')",
                    (f"g{i}", "2026-11-01T18:00:00Z"))
        con.execute("INSERT INTO nfl_pg (game_id, player_id) VALUES (?, 'p')", (f"g{i}",))
    con.execute("INSERT INTO games (league, game_id, start_utc, status) VALUES ('nfl', 'old', ?, 'final')", (freeze,))
    con.execute("INSERT INTO nfl_pg (game_id, player_id) VALUES ('old', 'p')")
    con.commit()
    r = shadow.l2_check("nfl", con, "2026-12-01T00:00:00Z")
    assert r["state"].startswith("waiting (47/48")          # the freeze-time game itself is not after the freeze
    con.execute("INSERT INTO games (league, game_id, start_utc, status) VALUES ('nfl', 'g47', '2026-11-02T18:00:00Z', "
                "'final')")
    con.execute("INSERT INTO nfl_pg (game_id, player_id) VALUES ('g47', 'p')")
    con.commit()
    assert shadow.l2_check("nfl", con, "2026-12-01T00:00:00Z")["state"].startswith("threshold reached")
    assert not shadow.l2_path("nfl").exists()                # the forward job never scores (bounded)
    shadow.l2_path("nfl").parent.mkdir(parents=True, exist_ok=True)
    shadow.l2_path("nfl").write_text("{}", encoding="utf-8")
    assert shadow.l2_check("nfl", con, score=True)["state"] == "scored"   # once scored, never re-scored


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not installed")
def test_player_page_factors_section_lists_every_factor_with_status(tmp_path):
    from sports.model import forward, pipeline
    meta = {"features": features.with_effects(),
            "artifacts": {s: pipeline._art_meta(forward.load_artifact(s)) for s in ("nba", "nfl")}}
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps(meta, default=float), encoding="utf-8")
    r = subprocess.run(["node", str(ROOT / "sports/tests/factors_section_check.js"), str(ROOT / "web/sports/app.js"),
                        str(mp), "nfl", "rec_yds"], capture_output=True, text=True, encoding="utf-8", timeout=60)
    out = r.stdout
    assert r.returncode == 0 and "placeholder ok: true" in out, r.stderr
    n_reg = sum(1 for x in meta["features"] if x["sport"] == "nfl" and (not x["markets"] or "rec_yds" in x["markets"]))
    rows = [ln for ln in out.splitlines() if " | " in ln]
    assert len(rows) == n_reg                                          # every registry factor for the market, once
    assert any(ln.startswith("Snap share | ") and "in the model" in ln for ln in rows)              # sm-1 input
    assert any(ln.startswith("Implied team total |") and "sm-2 challenger" in ln for ln in rows)   # shadow only
    assert any("weight 0 · tune change" in ln for ln in rows)                                      # tested, no effect
    assert any("not available" in ln for ln in rows) and not any("weight" in ln and "in the model" in ln for ln in rows)
