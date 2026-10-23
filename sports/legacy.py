"""Import the data branch's sharp-1 research log (<SPORTS_DATA_DIR>/sports.db, record.py) into the canonical store as
VERSIONED RESEARCH RECORDS (model_version "sharp-1"), without changing a single stored value.

sharp-1 stored `chance` = the chosen side's chance GIVEN NO PUSH and `push` = the unconditional push chance. The
canonical Forecast needs unconditional values, so the import derives them and keeps the original as the named
conditional field:
    p_side = chance * (1 - push),  p_other = (1 - chance) * (1 - push),  p_push = push
    p_<side>_given_no_push = chance
The legacy row's id, offer id, sharp quote and fit hash stay in the reason codes / snapshot ids; nothing is
re-computed and the legacy file is opened read-only.

The 3,656 NBA / 3,247 NFL rows reported on 2026-10-23 live in the originating cloud session's sports/.data/sports.db
(not in git, not on the Render disk - sports/INTEGRATION_MANIFEST.md); this importer is ready for that file.
    python -m sports.legacy /path/to/sports.db
"""
from __future__ import annotations

import json
import sqlite3
import sys

from . import store
from .contracts import ContractError, Forecast, utc


def to_forecast(row):
    side = row["side"]
    chance, push = float(row["chance"]), float(row["push"] or 0.0)
    if abs(row["line"] - round(row["line"])) > 1e-9:
        push = 0.0
    p_side, p_other = chance * (1 - push), (1 - chance) * (1 - push)
    po, pu = (p_side, p_other) if side == "over" else (p_other, p_side)
    sides = tuple(json.loads(row["sides_offered"] or "[]"))
    return Forecast.build(
        forecast_at=utc(row["recorded_at"]), league=row["league"], event_id=None, player_id=None,
        player_key=row["player_key"], stat=row["market"], scope="full_game", line=float(row["line"]),
        offer_snapshot_ids=(f"legacy:{row['platform']}:{row['offer_id']}",),
        input_snapshot_ids=(f"legacy:sharp:{row['sharp_source']}:{row['sharp_line']}:{row['sharp_p_over']}",),
        model_version="sharp-1", artifact_version=f"dispersion:{row['fit']}", data_version=row["sharp_source"],
        calibration_version=None, grade_policy_version=None, projection_mean=None, projection_median=None,
        distribution=(("ref", "sharp-1 fair.at_line"), ("gap", row["gap"]), ("far", row["far"])),
        p_over=po, p_under=pu, p_push=push,
        p_over_given_no_push=chance if side == "over" else 1 - chance,
        p_under_given_no_push=chance if side == "under" else 1 - chance,
        sides_available=sides, chosen_side=side, grade=None, stake_units=None, stake_policy_version=None,
        reason_codes=("legacy_sharp1_import", f"legacy_row:{row['id']}", "conditional_chance_converted",
                      f"start:{row['start']}", f"game:{row['game']}"),
        research_only=True, observed_at=utc(row["recorded_at"]), known_at=utc(row["recorded_at"]))


def import_file(path, con):
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    c = {"rows": 0, "inserted": 0, "duplicate": 0, "invalid": 0}
    try:
        for row in src.execute("SELECT * FROM forecasts ORDER BY id"):
            c["rows"] += 1
            try:
                res = store.put(con, to_forecast(row))
            except (ContractError, TypeError, ValueError) as e:
                c["invalid"] += 1
                store.quarantine(con, "forecast", "legacy-sharp-1", str(row["id"]), f"invalid: {str(e)[:160]}",
                                 dict(row), row["league"])
                continue
            c["inserted" if res["status"] == "inserted" else "duplicate"] += 1
    finally:
        src.close()
    return c


if __name__ == "__main__":
    with store.opened() as con:
        print(import_file(sys.argv[1], con))
