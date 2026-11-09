"""nrm-1 (REGISTER.md A12): NFL role-trend mean layer for rush_att / rec in SHADOW - frozen pair with sm-1, point-in-time
inputs, the recorder writes only its own rows (sm-1 frame untouched, never displayed, off switch), and the pre-stated
hit-rate decision rule."""
import gzip
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sports.model import datasets, dists, featurize, forward, nrm1, study

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-10-24T20:00:00Z"


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path_factory.mktemp("nrm1") / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return db


def test_artifact_verifies_pairs_with_live_sm1_and_tamper_refuses(tmp_path, monkeypatch):
    art = nrm1.load()
    assert art is not None and set(art["markets"]) == {"rush_att", "rec"}
    assert art["z_names"] == list(nrm1.Z_NAMES)
    for mk in nrm1.MARKETS:
        assert len(art["markets"][mk]["gamma"]) == len(nrm1.Z_NAMES)
    p = tmp_path / nrm1.ARTIFACT.name
    a = json.loads(nrm1.ARTIFACT.read_text(encoding="utf-8"))
    a["markets"]["rec"]["gamma"][1] += 0.01
    p.write_text(json.dumps(a), encoding="utf-8")
    assert nrm1.load(p) is None                                         # content sha mismatch
    sm1 = forward.load_artifact("nfl")
    monkeypatch.setattr(forward, "load_artifact", lambda sport: dict(sm1, sha="refit"))
    assert nrm1.load() is None                                          # sm-1 refit -> pair no longer exact


def test_registration_committed_before_the_artifact():
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)  # noqa: E731
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone (CI): commit order is not visible")
    reg = git("log", "-S", "Amendment A12 - nrm-1", "--format=%H", "--", "sports/research/REGISTER.md").stdout.split()
    art = git("log", "--diff-filter=A", "--format=%H", "--", "sports/model/artifacts/nrm1_nfl.json").stdout.split()
    if not reg or not art:
        pytest.skip("git history not available")
    assert reg[-1] != art[-1] and git("merge-base", "--is-ancestor", reg[-1], art[-1]).returncode == 0


def test_remap_keeps_mass_and_moves_the_mean():
    lo, ks = dists.grid("rush_att")
    p = np.zeros(len(ks))
    p[10 - lo], p[14 - lo] = 0.5, 0.5
    q = nrm1.remap(p, lo, 1.1)
    assert abs(q.sum() - 1) < 1e-12
    assert abs(float(q @ ks) - 1.1 * float(p @ ks)) < 1e-9
    assert np.allclose(nrm1.remap(p, lo, 1.0), p)


def _toy_frame():
    from sports.model.frame import Frame
    pid = np.array(["a"] * 4 + ["b"] * 3, dtype=object)
    ts = np.array([0, 7, 14, 35, 0, 7, 14], float) * 86400
    car = np.array([10, 12, 20, np.nan, 3, 4, np.nan], float)
    return Frame({"player_id": pid, "ts": ts, "carries": car, "targets": car / 2,
                  "offense_pct": np.array([.5, .6, .7, np.nan, .2, .2, np.nan]),
                  "l3_snap": np.array([np.nan, .5, .55, .6, np.nan, .2, .2]),
                  "season": np.array([2026] * 7, dtype=object), "team": np.array(["X", "X", "X", "Y", "Z", "Z", "Z"],
                                                                                  dtype=object),
                  "team_spread": np.array([0, 0, 0, -7, 3, 3, 3], float), "implied_total": np.full(7, 22.0),
                  "qb_change_lag": np.zeros(7)})


def test_role_inputs_use_only_earlier_rows():
    df = _toy_frame()
    Z = nrm1.role_inputs(df, "rush_att")
    df2 = _toy_frame()
    df2["carries"] = np.where(np.arange(7) == 3, 99.0, df2["carries"])     # the row's own result must not matter
    Z2 = nrm1.role_inputs(df2, "rush_att")
    assert np.allclose(Z[3], Z2[3])
    assert Z[0, 1] == 0 and Z[0, 2] == 0                                  # no history -> no trend
    assert Z[3, 6] == 1.0 and Z[3, 8] == 1.0                              # 21-day gap -> back from absence; team change
    assert Z[2, 1] > 0                                                    # last game above the EW mean -> up trend
    assert Z[3, 4] == -1.0                                                # team spread / 7


class _O:
    def __init__(self, eid, pid, line):
        self.event_id, self.player_id, self.line, self.platform = eid, pid, line, "prizepicks"

    def offered_sides(self):
        return ("over", "under")


def _up_frame(hist, n=6):
    con = datasets.connect(hist)
    r = con.execute("SELECT p.player_id, p.team, p.opp, p.home, p.position, MAX(g.start_utc) s FROM nfl_pg p JOIN "
                    "games g ON g.game_id = p.game_id WHERE g.season = 2026 AND p.position = 'RB' AND p.carries >= 8 "
                    "GROUP BY p.player_id ORDER BY s DESC, p.player_id LIMIT ?", (n,)).fetchall()
    up = [{"game_id": f"UP_{i}", "player_id": x[0], "team": x[1], "opp": x[2], "home": x[3],
           "start_utc": "2026-10-26T17:00:00Z", "position": x[4], "season": 2026, "week": 8,
           "season_type": "regular"} for i, x in enumerate(r)]
    df = study._nfl_fill(featurize.nfl_frame(con, h_opp=2, upcoming=up, as_of=AS_OF, with_lags=False))
    return con, df


def test_recorder_writes_own_rows_leaves_sm1_frame_untouched(hist, tmp_path, monkeypatch):
    con, df = _up_frame(hist)
    sm1 = forward.load_artifact("nfl")
    upi = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
    assert len(upi) > 0
    rowpos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
    base_of, pmf_of = {}, {}
    for gk, i in rowpos.items():
        p, mu = forward.pmf_from_row(sm1["markets"]["rush_att"], forward._row_dict(df, i), "rush_att", "nfl")
        base_of[gk], pmf_of[gk] = p, (p, mu, True)
    before = {c: np.array(v, copy=True) for c, v in df.cols.items()}
    items = [(f"offer:{k}", _O(f"ev:{k}", f"pl:{k}", 12.5), gk, 0.6) for k, gk in enumerate(rowpos)]
    db = tmp_path / "shadow.db"
    rec = nrm1.Recorder("2026-10-25T12:00:00Z", path=db)
    assert rec.art is not None
    n = rec.market("rush_att", df, (2, 2), rowpos, items, {}, base_of, pmf_of)
    out = rec.close()
    assert n == len(items) and out["written"] == n and not out["skipped"]
    assert set(df.cols) == set(before)
    for c, v in before.items():                                           # sm-1 frame untouched
        a = np.asarray(df[c])
        assert (np.array_equal(a, v, equal_nan=True) if a.dtype.kind == "f" else np.array_equal(a, v)), c
    s = sqlite3.connect(db)
    rows = s.execute("SELECT f.model_version, f.p_over, f.p_under, i.multiplier, i.live_p_over, i.sm1_p_over FROM "
                     "shadow_forecasts f JOIN nrm1_inputs i ON i.shadow_id = f.shadow_id").fetchall()
    s.close()
    assert len(rows) == n and all(r[0] == "nrm-1" for r in rows)
    assert all(abs(r[1] + r[2] - 1) < 0.2 and 0.13 <= r[3] <= 7.4 for r in rows)
    assert all(abs(r[4] - r[5]) < 1e-12 for r in rows)                   # printed = base here (no tw-1 in the test)
    con.close()
    # off switch: no recorder at all
    monkeypatch.setenv("SPORTS_SHADOW_NRM1", "0")
    assert forward._nrm1_recorder("nfl", "2026-10-25T12:00:00Z") is None
    monkeypatch.delenv("SPORTS_SHADOW_NRM1")
    assert forward._nrm1_recorder("nba", "2026-10-25T12:00:00Z") is None


def test_recorder_error_is_swallowed(tmp_path):
    rec = nrm1.Recorder("2026-10-25T12:00:00Z", path=tmp_path / "s.db")
    n = rec.market("rush_att", None, (2, 2), {}, [("r", _O("e", "p", 1.5), ("g", "p"), 0.5)], {}, {}, {})
    assert n == 0 and "rush_att" in rec.close()["skipped"]
    assert rec.market("pass_yds", None, (2, 2), {}, [1], {}, {}, {}) == 0      # not an nrm-1 market


def test_never_displayed():
    """Members read the store; nrm-1 rows live only in shadow.db and no member-facing module imports nrm1."""
    for f in (ROOT / "sports").glob("*.py"):
        assert "nrm1" not in f.read_text(encoding="utf-8"), f.name


def _rows(n_games, nrm_better):
    rows = []
    rng = np.random.default_rng(3)
    for g in range(n_games):
        for k in range(6):
            y = float(rng.integers(5, 20))
            line = 11.5
            truth_over = y > line
            good = 0.6 if truth_over else 0.4
            bad = 0.45 if truth_over else 0.55
            po = good if (nrm_better and k % 2 == 0) else bad
            rows.append({"game": f"g{g}", "player": f"p{k}", "market": "rush_att", "line": line, "y": y,
                         "p_over": po, "p_under": 1 - po, "live_p_over": bad, "live_p_under": 1 - bad})
    return rows


def test_decision_rule_is_hit_rate_by_side():
    sc = nrm1.hit_score(_rows(60, True))
    assert sc["games"] == 60 and sc["all_both"]["diff"] > 0
    assert nrm1.decide(sc).startswith("pass")
    same = nrm1.hit_score(_rows(60, False))
    assert same["all_both"]["diff"] == 0 and nrm1.decide(same).startswith("fail")
    assert nrm1.decide({"props": 0}) == "waiting"


def test_heldout_waits_for_48_games_and_scores_once(tmp_path, monkeypatch):
    monkeypatch.setattr(nrm1, "heldout_path", lambda: tmp_path / "heldout_nrm1.json")
    monkeypatch.setattr(nrm1, "_rows", lambda hcon, sp=None: (_rows(40, True), "2026-11-09T00:00:00Z"))
    assert "rush_att" not in nrm1.heldout(None, now="t1")
    monkeypatch.setattr(nrm1, "_rows", lambda hcon, sp=None: (_rows(50, True), "2026-11-09T00:00:00Z"))
    first = nrm1.heldout(None, now="t2")["rush_att"]
    assert first["scored_at"] == "t2" and first["decision"].startswith("pass")
    monkeypatch.setattr(nrm1, "_rows", lambda hcon, sp=None: (_rows(80, False), "2026-11-09T00:00:00Z"))
    assert nrm1.heldout(None, now="t3")["rush_att"]["scored_at"] == "t2"      # never re-scored
