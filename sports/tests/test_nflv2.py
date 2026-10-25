"""sm-nfl-2 (REGISTER.md A6): participation split point in time, frozen-artifact hashes, replay, no look-ahead, the
shadow path (own rows, frame restored, never displayed, bounded memory) and the one-time held-out H2 scorer."""
import gzip
import json
import shutil
import subprocess
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from sports.model import datasets, dists, featurize, forward, nflv2, shadow
from sports.model.frame import Frame

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-10-24T20:00:00Z"


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path_factory.mktemp("nflv2") / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    shadow.install_extras(db)
    return db


def _toy(snaps, opp, up_last=False):
    """One player, games in order; snaps / targets per game; optional upcoming last row."""
    n = len(snaps)
    is_up = np.zeros(n)
    if up_last:
        is_up[-1] = 1
    return Frame({"player_id": np.array(["P"] * n, dtype=object), "offense_pct": np.array(snaps, float),
                  "targets": np.array(opp, float), "carries": np.zeros(n), "pass_att": np.zeros(n), "is_up": is_up})


# ------------------------------------------------------------------------------------------ participation split
def test_participation_columns_use_prior_games_only():
    df = nflv2.augment(_toy([0.9, 0.2, 0.8, 0.95], [8, 1, 6, 9]), (8, 8))
    # row 0: no prior game -> no full-role history, unknown previous snap
    assert df["v2_nfh"][0] == 1 and np.isnan(df["v2_lag_snap"][0]) and np.isnan(df["v2_ewf_targets"][0])
    # row 2: prior games = [full 8 targets, partial 1 target] -> the partial game is skipped (but ages the weight)
    assert df["v2_ewf_targets"][2] == pytest.approx(8.0) and df["v2_lag_snap"][2] == pytest.approx(0.2)
    assert df["v2_nfh"][2] == 0 and 0 < df["v2_ew_partial"][2] < 1
    # the row's OWN game is only the training label, never an input of its own row
    a = nflv2.augment(_toy([0.9, 0.2, 0.8, 0.95], [8, 1, 6, 9]), (8, 8))
    b = nflv2.augment(_toy([0.9, 0.2, 0.8, 0.05], [8, 1, 6, 0]), (8, 8))
    for c in ("v2_ewf_targets", "v2_lag_snap", "v2_ew_partial", "v2_nfh"):
        np.testing.assert_array_equal(np.asarray(a[c]), np.asarray(b[c]))
    assert a["v2_full"][3] == 1 and b["v2_full"][3] == 0
    # unknown snap share counts as full-role (as sm-1 treats it)
    assert nflv2.augment(_toy([np.nan, 0.9], [5, 7]), (8, 8))["v2_ewf_targets"][1] == pytest.approx(5.0)


def test_upcoming_row_label_unknown_and_inputs_from_history():
    df = nflv2.augment(_toy([0.9, 0.3, 0.9, np.nan], [8, 2, 10, np.nan], up_last=True), (8, 8))
    assert np.isnan(df["v2_full"][3])                                # no outcome for an upcoming row
    assert df["v2_lag_snap"][3] == pytest.approx(0.9) and df["v2_nfh"][3] == 0
    w = 0.5 ** (1 / 8)                                               # EW over full games 0 and 2 (game 1 ages them)
    assert df["v2_ewf_targets"][3] == pytest.approx((8 * w ** 2 + 10) / (w ** 2 + 1))


def test_no_look_ahead_later_games_never_change_earlier_inputs(hist):
    con = datasets.connect(hist, read_only=True)
    cut = "2026-10-01T00:00:00Z"
    a = nflv2.augment(featurize.nfl_frame(con, h_opp=8, as_of=cut, with_lags=False), (8, 8))
    b = nflv2.augment(featurize.nfl_frame(con, h_opp=8, with_lags=False), (8, 8))
    ka = {(g, p): i for i, (g, p) in enumerate(zip(a["game_id"], a["player_id"]))}
    rows = [(i, ka[(g, p)]) for i, (g, p) in enumerate(zip(b["game_id"], b["player_id"])) if (g, p) in ka]
    assert len(rows) == len(a) > 1000
    ib, ia = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    for c in nflv2.ADDED:
        np.testing.assert_array_equal(np.asarray(b[c], float)[ib], np.asarray(a[c], float)[ia])
    con.close()


# --------------------------------------------------------------------------------------------- frozen artifacts
def test_frozen_artifact_and_config_hashes_verify_and_tamper_refuses(tmp_path):
    art = nflv2.load()
    assert art is not None and art["model_version"] == "sm-nfl-2"
    man = json.loads(nflv2.MANIFEST.read_text(encoding="utf-8"))
    from sports.model.fwd import _file_sha
    for name, meta in man["files"].items():
        assert _file_sha(nflv2.ART_DIR / name) == meta["sha256"]
    cfg = json.loads(nflv2.CONFIG.read_text(encoding="utf-8"))
    assert art["config_sha"] == nflv2._sha(cfg)[:16] and set(art["markets"]) == set(cfg["markets"])
    tune = json.loads((nflv2.RESULTS / "model_nflv2_tune.json").read_text(encoding="utf-8"))
    assert set(cfg["markets"]) == {m for m, r in tune["markets"].items() if not r["dropped_by_sanity_rule"]}
    assert tune["config_sha"] == nflv2._sha(cfg)[:16]
    bad = json.loads(nflv2.ARTIFACT.read_text(encoding="utf-8"))
    bad["markets"]["rec"]["beta"][0] += 0.01
    p = tmp_path / nflv2.ARTIFACT.name
    p.write_text(json.dumps(bad), encoding="utf-8")
    assert nflv2.load(p, nflv2.MANIFEST) is None                     # content / file sha mismatch -> refused


def test_registration_committed_before_any_nflv2_result():
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)  # noqa: E731
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone (CI): commit order is not visible")
    reg = git("log", "-S", "Amendment A6", "--format=%H", "--", "sports/research/REGISTER.md").stdout.split()
    res = git("log", "--diff-filter=A", "--format=%H", "--", "sports/research/results/model_nflv2_tune.json").stdout.split()
    if not reg or not res:
        pytest.skip("git history not available")
    assert reg[-1] != res[-1] and git("merge-base", "--is-ancestor", reg[-1], res[-1]).returncode == 0


# ------------------------------------------------------------------------------------------------- replay
def _up_frame(hist, n=6):
    con = datasets.connect(hist)
    r = con.execute("SELECT p.player_id, p.team, p.opp, p.home, p.position, MAX(g.start_utc) s FROM nfl_pg p JOIN "
                    "games g ON g.game_id = p.game_id WHERE g.season = 2026 AND p.position = 'WR' AND p.targets >= 6 "
                    "GROUP BY p.player_id ORDER BY s DESC, p.player_id LIMIT ?", (n,)).fetchall()
    up = [{"game_id": f"UP_{i}", "player_id": x[0], "team": x[1], "opp": x[2], "home": x[3],
           "start_utc": "2026-10-26T17:00:00Z", "position": x[4], "season": 2026, "week": 8,
           "season_type": "regular"} for i, x in enumerate(r)]
    df = featurize.nfl_frame(con, h_opp=8, upcoming=up, as_of=AS_OF, with_lags=False)
    from sports.model import study
    study._nfl_fill(df)
    return con, df


class _O:
    def __init__(self, eid, pid, line):
        self.event_id, self.player_id, self.line, self.platform = eid, pid, line, "prizepicks"

    def offered_sides(self):
        return ("over", "under")


def test_shadow_recorder_writes_own_rows_restores_frame_and_replays(hist, tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "game_context", lambda *a, **k: {})
    con, df = _up_frame(hist)
    cols_before = {c: np.array(v, copy=True) for c, v in df.cols.items()}
    upi = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
    rowpos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
    items = [(f"offer:{k}", _O(f"ev:{k}", f"pl:{k}", 55.5), gk, 0.6) for k, gk in enumerate(rowpos)]
    sm1 = {rid: (0.4, 0.6, 0.0) for rid, *_ in items}
    rec = nflv2.Recorder(con, AS_OF, path=tmp_path / "shadow.db")
    assert rec.active("rec_yds") and not rec.active("rush_att")      # dropped by the tune sanity rule
    assert rec.market("rush_att", df, (2, 2), rowpos, items, {}, sm1) == 0
    n = rec.market("rec_yds", df, (8, 8), rowpos, items, {}, sm1)
    assert n == len(items) >= 3 and not rec.skipped
    assert set(df.cols) == set(cols_before)                           # sm-1 frame exactly as before
    for c, v in cols_before.items():
        assert np.array_equal(np.asarray(df[c]), v) or (v.dtype.kind == "f" and np.array_equal(
            np.asarray(df[c]), v, equal_nan=True))
    rec.close()
    db = nflv2._db(tmp_path / "shadow.db")
    rows = db.execute("SELECT f.model_version, f.p_over, f.p_under, f.p_push, f.pmf_lo, f.pmf_json, i.row_json, "
                      "i.sm1_p_over, f.projection_mean FROM shadow_forecasts f JOIN shadow_inputs i ON i.shadow_id = "
                      "f.shadow_id").fetchall()
    assert len(rows) == n
    art = nflv2.load()
    for mv, po, pu, pp, lo, pj, rj, s1, mean in rows:
        assert mv == "sm-nfl-2" and abs(po + pu + pp - 1) < 1e-9 and s1 == 0.4
        p, m, _ = nflv2.pmf_from_row(art["markets"]["rec_yds"], json.loads(rj))
        np.testing.assert_allclose(p, json.loads(pj), atol=1e-12)     # replay from the saved row alone
        assert m == pytest.approx(mean) and dists.line_probs(p, lo, 55.5)[0] == pytest.approx(po, abs=1e-12)
    db.close()
    con.close()


def test_shadow_never_reaches_members_and_off_switch(monkeypatch):
    src = (ROOT / "sports" / "model" / "nflv2.py").read_text(encoding="utf-8")
    assert "store.put" not in src                                     # never a store record
    pipe = (ROOT / "sports" / "model" / "pipeline.py").read_text(encoding="utf-8")
    snap = pipe[pipe.index("def write_snapshot"):pipe.index("def _chunks")]
    member = snap.split("_atomic_write(api_dir() / \"model_meta.json\"")[0].split("shadow_state =")[0]
    assert "nflv2" not in member                                      # only the admin learning file mentions it
    assert "nflv2" not in (ROOT / "sports" / "snapshots.py").read_text(encoding="utf-8")
    assert "nflv2" not in (ROOT / "sports" / "board_view.py").read_text(encoding="utf-8")   # containment unchanged
    assert forward._nflv2_recorder(None, None, "nba", AS_OF, []) is None
    monkeypatch.setenv("SPORTS_SHADOW_NFLV2", "0")
    assert forward._nflv2_recorder(None, None, "nfl", AS_OF, []) is None


def test_shadow_memory_is_bounded(hist, tmp_path, monkeypatch):
    """One market's added columns at a time; the recorder's own allocations stay far below the forward job's
    340 MB cap (measured on the packaged history frame + 30 upcoming rows)."""
    monkeypatch.setattr(shadow, "game_context", lambda *a, **k: {})
    con, df = _up_frame(hist, 30)
    upi = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
    rowpos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
    items = [(f"offer:{k}", _O(f"ev:{k}", f"pl:{k}", 4.5), gk, 0.6) for k, gk in enumerate(rowpos)]
    rec = nflv2.Recorder(con, AS_OF, path=tmp_path / "shadow.db")
    tracemalloc.start()
    rec.market("rec", df, (8, 8), rowpos, items, {}, {})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rec.close()
    con.close()
    assert rec.written == len(items) and peak / 1e6 < 80, peak / 1e6


# ------------------------------------------------------------------------------------------------ held-out H2
def test_h2_waits_for_registered_threshold_and_box_is_scored_once(tmp_path, monkeypatch):
    con = datasets.connect(tmp_path / "h.db")
    freeze = nflv2.load()["freeze_at"]
    for i in range(47):
        con.execute("INSERT INTO games (league, game_id, start_utc, status) VALUES ('nfl', ?, '2026-11-01T18:00:00Z', "
                    "'final')", (f"g{i}",))
        con.execute("INSERT INTO nfl_pg (game_id, player_id) VALUES (?, 'p')", (f"g{i}",))
    con.execute("INSERT INTO games (league, game_id, start_utc, status) VALUES ('nfl', 'old', ?, 'final')", (freeze,))
    con.execute("INSERT INTO nfl_pg (game_id, player_id) VALUES ('old', 'p')")
    con.commit()
    assert nflv2.h2_check(con, "2026-12-01T00:00:00Z")["state"].startswith("waiting (47/48")
    calls = []
    monkeypatch.setattr(nflv2, "box_score", lambda *a: calls.append(1) or {"markets": {}})
    res = nflv2.heldout(con, tmp_path / "shadow.db", "2026-12-01T00:00:00Z")
    assert "box" not in res and not calls and all(v["contained"] for v in res["decision"].values())
    con.execute("INSERT INTO games (league, game_id, start_utc, status) VALUES ('nfl', 'g47', '2026-11-02T18:00:00Z', "
                "'final')")
    con.execute("INSERT INTO nfl_pg (game_id, player_id) VALUES ('g47', 'p')")
    con.commit()
    assert nflv2.h2_check(con, "2026-12-01T00:00:00Z")["state"].startswith("threshold reached")
    nflv2.heldout(con, tmp_path / "shadow.db", "2026-12-01T00:00:00Z")
    nflv2.heldout(con, tmp_path / "shadow.db", "2026-12-08T00:00:00Z")
    assert calls == [1]                                               # scored once, never re-scored
    assert nflv2.h2_check(con)["state"] == "scored"


def _rows(n, games, p_over, over_rate, seed=0):
    """Deterministic outcomes: exactly round(n * over_rate) Overs, spread evenly over the rows (and games)."""
    out = []
    for i in range(n):
        hit = int((i + 1 + seed) * over_rate) > int((i + seed) * over_rate)
        out.append({"game": f"g{i % games}", "line": 10.5, "y": 20.0 if hit else 5.0, "p_over": p_over,
                    "p_under": 1 - p_over, "p_push": 0.0, "mean": 10.0, "sm1_p_over": 0.40, "sm1_p_under": 0.60,
                    "sm1_p_push": 0.0})
    return out


def test_book_line_criteria_flag_one_sided_and_miscalibrated():
    ok = nflv2.book_score(_rows(300, 80, 0.47, 0.47) + _rows(300, 80, 0.55, 0.55))
    assert ok["criteria"]["e_calibration"] and ok["criteria"]["g_not_one_sided"]
    under = nflv2.book_score(_rows(400, 80, 0.38, 0.48))              # every prop favours Under; 10 pts too low
    assert not under["criteria"]["e_calibration"] and not under["criteria"]["g_not_one_sided"]
    assert under["share_over_favoured"] == 0.0 and under["over_rate"] == pytest.approx(0.48, abs=0.005)


def test_decision_needs_every_criterion_and_the_display_sample():
    box = {"markets": {"rec": {"criteria": {"a": True, "b": True, "c": True, "d": True}}}}
    good = {"criteria": {"e": True, "f": True, "g": True}}
    r = nflv2.decide({"box": box, "book": {"rec": dict(good)}}, ["rec", "rec_yds"])
    assert r["rec"]["promotion_eligible"] and not r["rec"]["display_bar"] and r["rec"]["contained"]
    assert r["rec_yds"]["state"] == "pending" and r["rec_yds"]["contained"]
    r = nflv2.decide({"box": box, "book": {"rec": dict(good, display_sample=good)}}, ["rec"])
    assert r["rec"]["display_bar"] and not r["rec"]["contained"]
    box["markets"]["rec"]["criteria"]["a"] = False
    r = nflv2.decide({"box": box, "book": {"rec": dict(good, display_sample=good)}}, ["rec"])
    assert not r["rec"]["promotion_eligible"] and r["rec"]["contained"]


def test_second_recorder_does_not_wait_on_the_sm2_recorders_lock(tmp_path, monkeypatch):
    """Regression (e2e 2026-10-25): the sm-2 recorder's retention DELETE held shadow.db's write lock until its first
    market, so the sm-nfl-2 recorder's table creation waited 30 s and then failed (no shadow rows)."""
    import time
    monkeypatch.setattr(shadow, "load", lambda sport: {"sha": "x", "markets": {"rec": {}}})
    sm2 = shadow.Recorder("nfl", None, AS_OF, path=tmp_path / "shadow.db")
    t = time.time()
    rec = nflv2.Recorder(None, AS_OF, path=tmp_path / "shadow.db", art={"sha": "y", "markets": {}})
    assert time.time() - t < 5 and rec.con is not None
    rec.close()
    sm2.close()
