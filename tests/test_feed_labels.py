"""Exact stat labels and the sides each feed really offers (2026-10-14 owner items 4b / 6). The fixtures are CAPTURED
SAMPLES of the live Underdog and PrizePicks feeds (2026-10-14 ~17:00 UTC), trimmed; synthetic variants are labelled."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import prizepicks as PP
import underdog as UD

FX = Path(__file__).parent / "fixtures"
PP_BODY = json.loads((FX / "prizepicks_cs2_2026-10-14.json").read_text(encoding="utf-8"))
UD_BODY = json.loads((FX / "underdog_cs2_2026-10-14.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc)


def test_underdog_live_codes_are_graded_exactly():
    rows = UD.parse(copy.deepcopy(UD_BODY))
    assert rows and {r["scope"] for r in rows} == {"12"}
    assert {r["prop"] for r in rows} <= {"kills", "hs"} and "hs" in {r["prop"] for r in rows}


def test_underdog_unsupported_labels_stay_unsupported():
    body = copy.deepcopy(UD_BODY)
    base = body["over_under_lines"][0]
    variants = [("kills", "Kills"),                                   # no map set: not assumed Maps 1-2 any more
                ("awp_kills_on_maps_1_2", "AWP Kills on Maps 1+2"),
                ("kills_plus_assists_on_maps_1_2", "Kills + Assists on Maps 1+2"),
                ("kills_on_map_2", "Kills on Map 2")]
    for code, label in variants:                                     # SYNTHETIC label variants of a captured line
        ln = copy.deepcopy(base)
        ln["over_under"]["appearance_stat"].update(stat=code, display_stat=label)
        body["over_under_lines"] = [ln]
        assert UD.parse(copy.deepcopy(body)) == [], (code, label)
    ln = copy.deepcopy(base)
    ln["over_under"]["appearance_stat"].update(stat="kills_on_maps_1_2_3", display_stat="Kills on Maps 1+2+3")
    body["over_under_lines"] = [ln]
    assert [r["scope"] for r in UD.parse(copy.deepcopy(body))] == ["123"]     # its own scope, never Maps 1-2


def _pp_rows():
    return PP.rows_from(PP.parse_direct(copy.deepcopy(PP_BODY), NOW))


def test_prizepicks_live_labels_map_exactly_and_map3_stays_unsupported():
    recs = PP.parse_direct(copy.deepcopy(PP_BODY), NOW)
    stats = {r["stat"]: (r["market"], r["maps"]) for r in recs}
    assert stats["MAPS 1-2 Kills"] == ("kills", "1-2") and stats["MAP 1 Headshots"] == ("headshots", "1")
    assert stats["MAP 3 Kills"] == ("kills", "3")
    rows = _pp_rows()
    assert rows and all(r["scope"] in ("12", "1", "123") for r in rows)
    assert not any(r["stat_label"].startswith("MAP 3") for r in rows)          # Map 3-only lines are not graded


def test_prizepicks_sides_come_from_the_feed():
    recs = PP.parse_direct(copy.deepcopy(PP_BODY), NOW)
    over_only = [r for r in recs if r["odds_type"] == "standard" and r["direction_basis"].startswith("feed allowed_wager_types: over")]
    both = [r for r in recs if r["odds_type"] == "standard" and r["direction_basis"].startswith("feed states no restriction")]
    adj = [r for r in recs if r["odds_type"] in ("goblin", "demon")]
    assert over_only and all(r["higher"] == 1.0 and r["lower"] is None for r in over_only)   # Over only, as the feed says
    assert both and all(r["higher"] == 1.0 and r["lower"] == 1.0 for r in both)
    assert adj and all(r["lower"] is None and "Less not confirmed" in r["direction_basis"] for r in adj)
    rows = {r["market_id"]: r for r in PP.rows_from(recs)}
    for r in recs:
        if r["line_id"] in rows:
            assert rows[r["line_id"]]["under_available"] is (r["lower"] is not None)


def test_backup_copy_is_re_read_from_its_raw_label():
    """The scraper's copy (prop_lines) keeps its own market / maps fields; they must agree with the raw stat label read by
    the same exact rule, else the line is not graded (SYNTHETIC records modelled on the copy's columns)."""
    good = {"line_id": "1", "player": "p", "team": "t", "opponent": "o", "start_time": NOW, "market": "kills", "maps": "1-2",
            "line": 30.5, "odds_type": "standard", "higher": 1.0, "lower": None, "stat": "MAPS 1-2 Kills"}
    assert len(PP.rows_from([good])) == 1 and PP.rows_from([good])[0]["under_available"] is False
    for bad in (dict(good, stat="MAPS 1-2 AWP Kills"), dict(good, stat="MAP 3 Kills", maps="3"),
                dict(good, maps="1"), dict(good, market="headshots"), dict(good, stat=None)):
        assert PP.rows_from([bad]) == [], bad
