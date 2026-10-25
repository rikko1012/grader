"""N2 genuine bug (sanity 2026-10-24): an upcoming NFL row of a fullback kept the raw "FB" position, which no fitted
position prior knows (training maps FB -> RB), so its efficiency was NaN and the PMF collapsed onto 0 (Kyle Juszczyk
rec_yds 0.05 projected at an 11.5 line)."""
import gzip
import json
import shutil
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from sports.model import featurize, forward

ROW = json.loads((Path(__file__).resolve().parent / "fixtures" / "nfl_fb_row_juszczyk.json").read_text(encoding="utf-8"))
# the saved forward input row of fc-3b9a5b3209188069d267a81b (store copy 2026-10-24, rec_yds 11.5, position "FB")


def test_fb_row_replays_to_a_real_projection_once_mapped():
    art = forward.load_artifact("nfl")
    if art is None:
        pytest.skip("no NFL artifact")
    _, mu_fb = forward.pmf_from_row(art["markets"]["rec_yds"], ROW, "rec_yds", "nfl")
    _, mu_rb = forward.pmf_from_row(art["markets"]["rec_yds"], dict(ROW, position="RB"), "rec_yds", "nfl")
    assert mu_fb < 0.5                      # the bug: no prior for "FB"
    assert 5 < mu_rb < 25                   # ew_rec_yds 14.0, 1.7 targets/game


def test_nfl_frame_maps_upcoming_fullbacks(tmp_path):
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT p.player_id, p.team, g.season FROM nfl_pg p JOIN games g ON g.game_id = p.game_id "
                    "WHERE p.position = 'FB' AND g.status = 'final' ORDER BY g.start_utc DESC LIMIT 1").fetchone()
    if r is None:
        pytest.skip("no fullback in the packaged history")
    up = [{"game_id": "UP_1", "player_id": r["player_id"], "team": r["team"], "opp": "ATL", "home": 0,
           "start_utc": "2026-10-25T17:00:00Z", "position": "FB", "season": r["season"], "week": 7,
           "season_type": "regular"}]
    df = featurize.nfl_frame(con, upcoming=up, as_of="2026-10-24T00:00:00Z", with_lags=False)
    i = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
    assert len(i) == 1 and df["position"][i[0]] == "RB"
    assert not (np.asarray(df["position"]) == "FB").any()
