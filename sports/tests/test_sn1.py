"""sn-1 (REGISTER.md A10): frozen pair with sm-1, shadow recorder (own rows, sm-1 frame untouched, never displayed,
off switch) and the one-time held-out scores (thresholds, scored once, decision rule)."""
import gzip
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from sports.model import datasets, dists, featurize, forward, sn1, study

ROOT = Path(__file__).resolve().parents[2]
AS_OF = "2026-10-24T20:00:00Z"


@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path_factory.mktemp("sn1") / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return db


def test_artifact_verifies_pairs_with_live_sm1_and_tamper_refuses(tmp_path, monkeypatch):
    art = sn1.load()
    assert art is not None and set(art["markets"]) == {"rec", "rec_yds"}
    sm1 = forward.load_artifact("nfl")
    assert art["pairs_with_sm1"] == sm1["sha"] and art["data_as_of"] == sm1["data_as_of"]
    for mk in sn1.MARKETS:
        a, s = art["markets"][mk], sm1["markets"][mk]
        assert a["features"] == s["features"] + [sn1.FEATURE]           # the ONLY difference (A10 1)
        assert (a["half_lives"], a["family"], a["cal"], a["fit_rows"]) == (s["half_lives"], s["family"], s["cal"],
                                                                            s["fit_rows"])
    p = tmp_path / sn1.ARTIFACT.name
    p.write_text(sn1.ARTIFACT.read_text(encoding="utf-8").replace('"rec"', '"rec" ', 1), encoding="utf-8")
    assert sn1.load(p, sn1.MANIFEST) is None                           # file sha mismatch
    monkeypatch.setattr(forward, "load_artifact", lambda sport: dict(sm1, sha="refit"))
    assert sn1.load() is None                                          # sm-1 refit -> pair no longer exact


def test_fit_reproduces_the_frozen_artifact(hist, tmp_path):
    con = datasets.connect(hist)
    res = sn1.fit(con, out_dir=tmp_path)
    con.close()
    assert res["sha"] == sn1.load()["sha"]


def test_registration_committed_before_the_artifact():
    git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, timeout=30)  # noqa: E731
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
        pytest.skip("shallow clone (CI): commit order is not visible")
    reg = git("log", "-S", "Amendment A10", "--format=%H", "--", "sports/research/REGISTER.md").stdout.split()
    art = git("log", "--diff-filter=A", "--format=%H", "--", "sports/model/artifacts/sn1_nfl.json").stdout.split()
    if not reg or not art:
        pytest.skip("git history not available")
    assert reg[-1] != art[-1] and git("merge-base", "--is-ancestor", reg[-1], art[-1]).returncode == 0


class _O:
    def __init__(self, eid, pid, line):
        self.event_id, self.player_id, self.line, self.platform = eid, pid, line, "prizepicks"

    def offered_sides(self):
        return ("over", "under")


def _up_frame(hist, n=6):
    con = datasets.connect(hist)
    r = con.execute("SELECT p.player_id, p.team, p.opp, p.home, p.position, MAX(g.start_utc) s FROM nfl_pg p JOIN "
                    "games g ON g.game_id = p.game_id WHERE g.season = 2026 AND p.position = 'WR' AND p.targets >= 6 "
                    "GROUP BY p.player_id ORDER BY s DESC, p.player_id LIMIT ?", (n,)).fetchall()
    up = [{"game_id": f"UP_{i}", "player_id": x[0], "team": x[1], "opp": x[2], "home": x[3],
           "start_utc": "2026-10-26T17:00:00Z", "position": x[4], "season": 2026, "week": 8,
           "season_type": "regular"} for i, x in enumerate(r)]
    df = study._nfl_fill(featurize.nfl_frame(con, h_opp=8, upcoming=up, as_of=AS_OF, with_lags=False))
    return con, df


def test_recorder_writes_own_rows_leaves_sm1_frame_untouched_and_replays(hist, tmp_path):
    con, df = _up_frame(hist)
    sm1 = forward.load_artifact("nfl")
    upi = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
    rowpos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
    base_of, pmf_of = {}, {}
    for gk, i in rowpos.items():
        p, mu = forward.pmf_from_row(sm1["markets"]["rec_yds"], forward._row_dict(df, i), "rec_yds", "nfl")
        base_of[gk], pmf_of[gk] = p, (p, mu, True)
    before = {c: np.array(v, copy=True) for c, v in df.cols.items()}
    items = [(f"offer:{k}", _O(f"ev:{k}", f"pl:{k}", 55.5), gk, 0.6) for k, gk in enumerate(rowpos)]
    rec = sn1.Recorder(AS_OF, path=tmp_path / "shadow.db")
    assert rec.active("rec_yds") and not rec.active("pass_yds")
    assert rec.market("rec_yds", df, (4, 4), rowpos, items, {}, base_of, pmf_of) == 0     # other half-lives: skip
    assert "half-lives" in rec.skipped.pop("rec_yds")
    n = rec.market("rec_yds", df, (8, 8), rowpos, items, {}, base_of, pmf_of)
    assert n == len(items) >= 3 and not rec.skipped
    assert set(df.cols) == set(before)                                  # nothing added to the sm-1 frame
    for c, v in before.items():
        w = np.asarray(df[c])
        assert np.array_equal(w, v) or (v.dtype.kind == "f" and np.array_equal(w, v, equal_nan=True))
    rec.close()
    db = sn1.db(tmp_path / "shadow.db")
    rows = db.execute("SELECT f.model_version, f.p_over, f.p_under, f.p_push, f.pmf_lo, f.pmf_json, i.ds1_game_id, "
                      "i.ds1_player_id, i.sm1_pmf_json, i.snap_trend FROM shadow_forecasts f JOIN sn1_inputs i ON "
                      "i.shadow_id = f.shadow_id").fetchall()
    db.close()
    con.close()
    assert len(rows) == n
    am = sn1.load()["markets"]["rec_yds"]
    moved = 0
    for mv, po, pu, pp, lo, pj, g, pid, bj, st in rows:
        assert mv == "sn-1" and abs(po + pu + pp - 1) < 1e-9
        row = forward._row_dict(df, rowpos[(g, pid)])
        p, _ = forward.pmf_from_row(am, {k: v for k, v in row.items() if not k.startswith("F_")}, "rec_yds", "nfl")
        np.testing.assert_allclose(p, json.loads(pj), atol=1e-12)        # replay from the saved inputs
        np.testing.assert_allclose(json.loads(bj), base_of[(g, pid)], atol=1e-12)
        assert st is None or abs(st - (row["l3_snap"] - row["ew_offense_pct"])) < 1e-12
        moved += not np.allclose(p, base_of[(g, pid)])
    assert moved                                                         # snap_trend really changes the PMF


def test_never_displayed_and_off_switch(monkeypatch):
    src = (ROOT / "sports" / "model" / "sn1.py").read_text(encoding="utf-8")
    assert "store.put" not in src
    for f in ("snapshots.py", "board_view.py"):
        assert "sn1" not in (ROOT / "sports" / f).read_text(encoding="utf-8")
    assert forward._sn1_recorder("nba", AS_OF) is None
    monkeypatch.setenv("SPORTS_SHADOW_SN1", "0")
    assert forward._sn1_recorder("nfl", AS_OF) is None
    monkeypatch.delenv("SPORTS_SHADOW_SN1")
    assert forward._sn1_recorder("nfl", AS_OF) is not None


def _pm(lo, n, at):
    p = np.full(n, 1e-6)
    p[int(at) - lo] = 1.0
    return p / p.sum()


def _synth(games, market="rec", good=True, line=3.5):
    lo, ks = dists.grid(market)
    out = []
    for g in range(games):
        for k in range(4):
            y = float((g + k) % 7)
            sharp, wide = _pm(lo, len(ks), y), np.full(len(ks), 1.0 / len(ks))
            p2, p1 = (sharp, wide) if good else (wide, sharp)
            po2, pu2, pp2 = dists.line_probs(p2, lo, line)
            po1, pu1, pp1 = dists.line_probs(p1, lo, line)
            out.append({"game": f"g{g}", "player": f"p{k}", "market": market, "line": line, "y": y, "eligible": True,
                        "lo": lo, "pmf": p2, "sm1_pmf": p1, "p_over": po2, "p_under": pu2, "p_push": pp2,
                        "sm1_p_over": po1, "sm1_p_under": pu1, "sm1_p_push": pp1})
    return out


def test_scores_pass_and_fail_in_the_right_direction():
    assert sn1.box_score(_synth(40, good=True))["pass"]
    assert not sn1.box_score(_synth(40, good=False))["pass"]
    assert not sn1.book_score(_synth(40, good=True))["fail"]
    assert sn1.book_score(_synth(40, good=False))["fail"]


def test_heldout_waits_for_thresholds_and_scores_each_part_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sn1, "heldout_path", lambda: tmp_path / "heldout_sn1.json")
    rows = _synth(47, "rec") + _synth(47, "rec_yds")
    monkeypatch.setattr(sn1, "_rows", lambda *a, **k: (rows, "2026-11-06T00:00:00Z"))
    res = sn1.heldout(None, now="2026-12-01T00:00:00Z")
    assert "box" not in res and set((res.get("book") or {})) == {"rec", "rec_yds"}   # 188 props / 47 games each
    assert res["decision"] == {"rec": "waiting", "rec_yds": "waiting"}
    rows += _synth(48, "rec")[-4:]                                       # game 48 (g47) arrives
    calls = []
    real = sn1.box_score
    monkeypatch.setattr(sn1, "box_score", lambda r: calls.append(1) or real(r))
    res = sn1.heldout(None, now="2026-12-08T00:00:00Z")
    first = json.loads((tmp_path / "heldout_sn1.json").read_text(encoding="utf-8"))
    sn1.heldout(None, now="2026-12-15T00:00:00Z")
    again = json.loads((tmp_path / "heldout_sn1.json").read_text(encoding="utf-8"))
    assert len(calls) == 2 and first["box"] == again["box"]               # once per market, never re-scored
    assert first["book"] == again["book"]
    assert res["decision"]["rec"].startswith("pass") and res["decision"]["rec_yds"].startswith("pass")


def test_recorder_memory_is_small(hist, tmp_path):
    """Reads the sm-1 frame only (no added columns): a few PMFs per row, far below the forward job's cap."""
    import tracemalloc
    con, df = _up_frame(hist, 30)
    sm1 = forward.load_artifact("nfl")
    upi = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
    rowpos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
    base_of = {gk: forward.pmf_from_row(sm1["markets"]["rec"], forward._row_dict(df, i), "rec", "nfl")[0]
               for gk, i in rowpos.items()}
    pmf_of = {gk: (p, 0.0, True) for gk, p in base_of.items()}
    items = [(f"offer:{k}", _O(f"ev:{k}", f"pl:{k}", 4.5), gk, 0.6) for k, gk in enumerate(rowpos)]
    rec = sn1.Recorder(AS_OF, path=tmp_path / "shadow.db")
    tracemalloc.start()
    rec.market("rec", df, (8, 8), rowpos, items, {}, base_of, pmf_of)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rec.close()
    con.close()
    assert rec.written == len(items) and not rec.skipped and peak / 1e6 < 20, peak / 1e6
