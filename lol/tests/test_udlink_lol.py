"""Underdog line links (PP_OPEN_LINK, 2026-10-29), the LoL side: the Underdog parser keeps Underdog's market id
(over_under_id) and the appearance's match_type without changing any offer's state hash (no new events, no re-forecast),
and board() hands each Underdog row the ids the member page builds its "Open in Underdog" link from (server.py; the link
itself is tested in tests/test_udlink.py). The fixture is the saved Underdog LoL board of 2026-10-17; the XANTARES id is
the owner's verified CS2 market id, used only as a well-formed uuid."""
import json
from pathlib import Path

from lol.center import forecast, markets
from lol.tests.test_consolidation import con  # noqa: F401 - a fixture

ROOT = Path(__file__).resolve().parents[2]
XAN_OU = "cc625c31-9790-4a65-b814-766222f427d2"


def test_lol_board_ud_ids():
    assert forecast._ud_ids({"market_key": f"ud:{XAN_OU}|balanced", "match_source_id": "272955", "ud_match_type": "Game"}) == \
        {"ud_over_under_id": XAN_OU, "ud_match_id": "272955", "ud_match_type": "Game"}
    assert forecast._ud_ids({"market_key": "ud:not-a-uuid|", "match_source_id": "", "ud_match_type": None}) == \
        {"ud_over_under_id": None, "ud_match_id": None, "ud_match_type": None}


def test_lol_parse_keeps_the_ids_and_the_state_hash():
    body = json.loads((ROOT / "lol" / "tests" / "fixtures" / "underdog_lol_2026-10-17.json").read_text(encoding="utf-8"))
    offs = markets.parse_underdog(body)
    assert offs and all(o["over_under_id"] == o["market_key"][3:].split("|")[0] for o in offs)
    assert all(o["over_under_id"] != o["line_id"] for o in offs) and {o["extra"]["match_type"] for o in offs} == {"Game"}
    for o in offs:                                              # a re-poll with the new fields is not a new event
        old = dict(o, extra={k: v for k, v in o["extra"].items() if k != "match_type"})
        old.pop("over_under_id")
        assert markets.state_sha(o) == markets.state_sha(old)


def test_board_rows_carry_the_underdog_ids(con, tmp_path, monkeypatch):
    from lol.tests.test_consolidation import _future_world
    _db, _start, raw = _future_world(con, tmp_path, monkeypatch)
    ln = raw["over_under_lines"][0]
    app = next(a for a in raw["appearances"] if a["id"] == "a1")
    row, = forecast.board(con)["rows"]
    assert row["platform"] == "underdog" and ln["over_under_id"] == "OU1" and row["ud_over_under_id"] is None   # not a uuid: no id
    raw["over_under_lines"][0]["over_under_id"] = XAN_OU                 # Underdog's real ids are uuids
    raw["appearances"] = [dict(a, match_type="Game") if a["id"] == "a1" else a for a in raw["appearances"]]
    markets.poll(con, "underdog", fetcher=lambda p: (200, json.dumps(raw).encode()))
    row = next(r for r in forecast.board(con)["rows"] if XAN_OU in r["market_key"])
    assert row["ud_over_under_id"] == XAN_OU and row["ud_match_id"] == str(app["match_id"]) and row["ud_match_type"] == "Game"
    assert row["line_id"] == ln["id"] != XAN_OU
