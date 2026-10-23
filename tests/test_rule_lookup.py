"""2026-10-23 closeout 2 (rulelookup): the PrizePicks forfeit / activity and both platforms' remake rules were searched
again on the official pages (platform_rules.SEARCHES). None was published, so they stay PENDING - and every pending pick
is listed, bounded, with its reason and the missing evidence (/admin/rule_pending.json). Temporary database only."""
import json

import pytest

import picklog
import platform_rules as pr
import server
from tests.test_audit13_site import get, site  # noqa: F401


def test_unpublished_rules_stay_pending_with_the_search_recorded():
    for name in ("forfeit_in_scope", "forfeit_outside_scope", "player_missed_map_in_scope", "partial_map_substitution",
                 "remake"):
        assert pr.rule("prizepicks", name)["verified"] is False
        ev = pr.missing_evidence("prizepicks", name)
        assert ev and ev["searched"] and ev["missing"]
    assert pr.rule("underdog", "remake")["verified"] is False and pr.missing_evidence("underdog", "remake")["missing"]
    assert pr.missing_evidence("underdog", "forfeit_in_scope") is None          # verified rule: nothing missing
    # behaviour unchanged: PrizePicks forfeits and partial-map DNPs remain pending, never inferred
    assert pr.forfeit_decision("prizepicks", "12", {3})["action"] == "pending"
    assert pr.forfeit_decision("prizepicks", "12", {2})["action"] == "pending"
    assert pr.dnp_decision("prizepicks", "12", {1}, {1, 2})["action"] == "pending"


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    con = picklog._db()
    rows = [(1, "open", "prizepicks", {"rule_pending": {"reason": "prizepicks: forfeit outside", "rule": "forfeit_outside_scope",
                                                         "platform": "prizepicks", "forfeited_maps": [3]}}),
            (2, "open", "underdog", {"rule_pending": {"reason": "forfeited map numbers not determined", "rule": "forfeit_in_scope",
                                                       "platform": "underdog", "forfeited_maps": None}}),
            (3, "settled", "prizepicks", {"rule_pending": {"reason": "old", "rule": "forfeit_in_scope", "platform": "prizepicks"}}),
            (4, "open", "prizepicks", {"source": "hltv"})]
    for i in range(5, 8):
        rows.append((i, "open", "prizepicks", {"rule_pending": {"reason": "prizepicks: did not play map [2]",
                                                                 "rule": "player_missed_map_in_scope", "platform": "prizepicks"}}))
    for i, st, bk, res in rows:
        con.execute("INSERT INTO picks (id, key, player, prop, line, pick, status, book, scope, result, match_slug) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (i, f"k{i}", f"p{i}", "kills", 29.5, "More", st, bk, "12",
                                                     json.dumps(res), f"m{i}"))
    con.commit()
    con.close()


def test_rule_pending_page_lists_open_pending_picks_bounded(db):
    out = picklog.rule_pending_page(limit=2)
    assert [r["id"] for r in out["rows"]] == [1, 2] and not out["done"]
    a, b = out["rows"]
    assert a["rule_pending"]["rule"] == "forfeit_outside_scope" and not a["rule_verified"]
    assert "scoring chart" in a["missing_evidence"]["missing"].lower()
    assert b["rule_verified"] and b["missing_evidence"]["missing"].startswith("data, not a rule")
    nxt = picklog.rule_pending_page(after_id=out["next_after"], limit=10)
    assert [r["id"] for r in nxt["rows"]] == [5, 6, 7] and nxt["done"]           # settled (3) and plain open (4) excluded
    assert picklog.rule_pending_page(limit=10_000)["limit"] == picklog.RULE_PENDING_MAX


def test_rule_pending_endpoint_is_admin_only(db, site):
    site.setattr(server, "ADMIN_TOKEN", "tok-123456")
    assert get("/admin/rule_pending.json").code != 200                # no token: not served
    h = get("/admin/rule_pending.json?limit=500", headers={"X-Admin-Token": "tok-123456"})
    assert h.code == 200 and [r["id"] for r in h.body()["rows"]] == [1, 2, 5, 6, 7]
