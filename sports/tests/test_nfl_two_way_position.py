"""Two-way NFL players (sanity 2026-11-05): nflverse lists Travis Hunter (JAX WR/CB) as CB in every nfl_pg row, so the
offensive-only filter in nfl_frame dropped his whole history and his upcoming row kept "CB", which no fitted position
prior knows -> PMF collapsed onto 0 (sm-1 mean 0.05 at rec 1.5 / rec_yds 9.5). SCOUT_NFL_TWO_WAY=1 keeps the rows that
show offense and gives each row / upcoming row a deterministic offensive position. Off = today's behaviour."""
import gzip
import shutil
import sqlite3

import numpy as np
import pytest

from sports.model import featurize, forward
from sports.model.frame import Frame


def _pg(rows):
    cols = ("player_id", "game_id", "ts", "position", "pass_att", "carries", "targets")
    return Frame({c: np.array([r[k] for r in rows], dtype=object if c in ("player_id", "game_id", "position") else float)
                  for k, c in enumerate(cols)})


def test_row_role():
    assert featurize.nfl_row_role("FB", 0, 0, 0) == "RB"
    assert featurize.nfl_row_role("TE", 0, 0, 0) == "TE"
    assert featurize.nfl_row_role("CB", 0, 0, 3) == "WR"
    assert featurize.nfl_row_role("CB", 0, 4, 1) == "RB"
    assert featurize.nfl_row_role("LB", 2, 0, 0) == "QB"
    assert featurize.nfl_row_role("CB", 0, 0, 0) is None
    assert featurize.nfl_row_role("CB", np.nan, np.nan, np.nan) is None


def test_positions_point_in_time_and_deterministic():
    pg = _pg([
        ("hunter", "g1", 1, "CB", 0, 0, 5), ("hunter", "g2", 2, "CB", 0, 0, 0), ("hunter", "g3", 3, "CB", 0, 2, 1),
        ("matlock", "g1", 1, "DT", 0, 0, 1), ("matlock", "g2", 2, "RB", 0, 0, 0), ("matlock", "g3", 3, "DT", 0, 0, 1),
        ("beck", "g1", 1, "RB", 0, 0, 1), ("beck", "g2", 2, "TE", 0, 0, 0),
        ("plain", "g1", 1, "WR", 0, 0, 6),
    ])
    shuffled = pg[np.array([8, 3, 0, 6, 5, 2, 7, 1, 4])]            # input order must not matter
    for frame in (pg, shuffled):
        pos, last = featurize.nfl_positions(frame)
        got = {(frame["player_id"][i], frame["game_id"][i]): pos[i] for i in range(len(frame))}
        assert got[("hunter", "g1")] == got[("hunter", "g2")] == got[("hunter", "g3")] == "WR"
        assert got[("matlock", "g1")] == "WR"                      # no offensive listing yet: usage
        assert got[("matlock", "g3")] == "RB"                      # his own offensive listing wins once seen
        assert got[("plain", "g1")] == "WR" and got[("beck", "g2")] == "TE"   # offensive listings untouched
        assert last == {"hunter": "WR", "matlock": "RB", "beck": "TE", "plain": "WR"}   # beck tie -> most recent


def _history(tmp_path):
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


HUNTER = "00-0040718"


def _up(position="CB"):
    return [{"game_id": "UP_1", "player_id": HUNTER, "team": "JAX", "opp": "HOU", "home": 1,
             "start_utc": "2026-10-26T17:00:00Z", "position": position, "season": 2026, "week": 8,
             "season_type": "REG"}]


def test_flag_off_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("SCOUT_NFL_TWO_WAY", raising=False)
    con = _history(tmp_path)
    if con.execute("SELECT 1 FROM nfl_pg WHERE player_id = ?", (HUNTER,)).fetchone() is None:
        pytest.skip("Hunter not in the packaged history")
    df = featurize.nfl_frame(con, upcoming=_up(), as_of="2026-10-24T00:00:00Z", with_lags=False)
    mine = np.asarray(df["player_id"]) == HUNTER
    assert mine.sum() == 1 and df["position"][np.flatnonzero(mine)[0]] == "CB"     # the bug, kept while off


def test_flag_on_keeps_two_way_history_and_maps_upcoming(tmp_path, monkeypatch):
    monkeypatch.setenv("SCOUT_NFL_TWO_WAY", "1")
    con = _history(tmp_path)
    if con.execute("SELECT 1 FROM nfl_pg WHERE player_id = ?", (HUNTER,)).fetchone() is None:
        pytest.skip("Hunter not in the packaged history")
    df = featurize.nfl_frame(con, upcoming=_up(), as_of="2026-10-24T00:00:00Z", with_lags=False)
    mine = np.flatnonzero(np.asarray(df["player_id"]) == HUNTER)
    up = [i for i in mine if df["is_up"][i] == 1]
    assert len(mine) >= 10 and len(up) == 1                        # his offensive rows are back
    assert set(df["position"][i] for i in mine) == {"WR"}
    assert set(np.asarray(df["position"])) <= set(featurize.NFL_POS)
    # linemen / specialists play offensive snaps but are never pulled in (walk-forward amendment 2026-11-05)
    ol = {r[0] for r in con.execute("SELECT DISTINCT player_id FROM nfl_pg WHERE position IN ('OT','G','C') "
                                    "AND offense_snaps > 0 LIMIT 50")}
    assert ol and not (set(np.asarray(df["player_id"])) & ol)
    art = forward.load_artifact("nfl")
    if art is None:
        pytest.skip("no NFL artifact")
    from sports.model import study
    df = study._nfl_fill(df)
    _, mu = forward.pmf_from_row(art["markets"]["rec"], forward._row_dict(df, up[0]), "rec", "nfl")
    assert mu > 0.5                                                 # was the 0.05 floor
