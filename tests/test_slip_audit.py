"""slip_audit (owner 2026-11-16 "go over every 4 man ever generated through scout"): read-only pages of every source's 4-pick slips, each
CS2 leg settled from the pick log with slip history's rule. Synthetic stores only; nothing is written."""
import json
import os
import sqlite3

import pytest

import slip_audit
import slip_builder
import workspace_store

LEGS = [("whisper", "kills", 28.5, "More", 30), ("danistzz", "hs", 10.5, "More", 8), ("glowiing", "kills", 20.5, "Less", 25), ("oontoma", "hs", 9.5, "Less", 9)]


def _picks(slug):
    return [{"player": n, "prop": p, "scope": "12", "match_slug": f"{slug}-{i}", "status": "settled", "actual": a, "result": None}
            for i, (n, p, _l, _s, a) in enumerate(LEGS)]


@pytest.fixture
def stores(tmp_path, monkeypatch):
    rows = _picks("m") + [dict(_picks("x")[0], match_slug="hltv-77")]
    monkeypatch.setattr(slip_audit.picklog, "rows_for_matches", lambda slugs, cap=None: [r for r in rows if r["match_slug"] in slugs])
    st = tmp_path / "studio.db"
    con = sqlite3.connect(st)
    con.executescript(slip_builder._SCHEMA)
    con.execute("INSERT INTO batches VALUES ('b1', 'owner-session', 1, NULL, '2026-11-15T00:00:00+00:00', NULL)")
    rec = {"book": "prizepicks", "entry_type": "standard", "legs": [{"nick": n, "prop": p, "scope": "12", "line": l, "side": s, "p": .6, "grade": "B+",
                                                                    "match_slug": f"m-{i}"} for i, (n, p, l, s, _a) in enumerate(LEGS)]}
    con.execute("INSERT INTO slips VALUES ('b1/b1/s1', 'b1', 'b1/b1', 4, '2026-11-15T00:00:00+00:00', ?)", (json.dumps(rec),))
    con.execute("INSERT INTO slips VALUES ('b1/b1/s2', 'b1', 'b1/b1', 2, '2026-11-15T00:00:00+00:00', ?)", (json.dumps(dict(rec, legs=rec["legs"][:2])),))
    con.execute("INSERT INTO events(batch_id, slip_id, kind, at) VALUES ('b1', 'b1/b1/s1', 'copy', '2026-11-15T00:01:00+00:00')")
    con.commit(); con.close()
    ws = tmp_path / "workspace.db"
    con = sqlite3.connect(ws)
    con.executescript(workspace_store.SCHEMA)
    hub = {"created_at": "2026-11-16T01:00:00Z", "slips": [{"platform": "prizepicks", "entry_type": "power", "legs": [
        {"sport": "cs2", "player": "whisper", "team": "BAKS", "stat": "kills", "period": "maps12", "line": 28.5, "side": "over", "shown_p": .677, "event_id": "hltv:77"},
        {"sport": "nba", "player": "Paul Reed", "team": "DET", "stat": "rebounds", "period": "game", "line": 8.5, "side": "under", "event_id": "nba:1"},
        {"sport": "cs2", "player": "zz", "stat": "hs", "period": "maps12", "line": 5.5, "side": "over", "event_id": "hltv:78"},
        {"sport": "cs2", "player": "yy", "stat": "hs", "period": "maps12", "line": 5.5, "side": "over", "event_id": "hltv:79"}]}]}
    con.execute("INSERT INTO member_state(member, rev, updated_at, studio) VALUES ('m1', 1, '2026-11-16', ?)", (json.dumps(hub),))
    con.commit(); con.close()
    bl = tmp_path / "batches.jsonl"
    bl.write_text(json.dumps({"batch_id": "20261015T1200", "created_at": "2026-10-15T12:00:00+00:00", "slips": [
        {"kind": "diversified", "n": 4, "legs": [dict(l, awp=(l["nick"] == "danistzz")) for l in rec["legs"]]}]}) + "\n", encoding="utf-8")
    rl = tmp_path / "rec.jsonl"
    rl.write_text("not json\n" + "".join(json.dumps({"key": f"k{i}", "n": 4, "first_seen": "2026-10-01", "legs": rec["legs"]}) + "\n" for i in range(5)), encoding="utf-8")
    paths = {"studio": st, "workspace": ws, "recommended": rl, "loaded": tmp_path / "missing.jsonl", "batches": bl}
    before = {k: os.path.getmtime(v) for k, v in paths.items() if os.path.exists(v)}
    yield paths
    assert before == {k: os.path.getmtime(v) for k, v in paths.items() if os.path.exists(v)}      # read only


def role(nick, team):
    return (nick == "danistzz", "test roles")


def test_studio_four_pick_slips_settle_from_the_pick_log(stores):
    d = slip_audit.page("studio", 4, 0, 200, role, stores)
    assert len(d["slips"]) == 1 and d["next"] is None
    s = d["slips"][0]
    assert s["used"] == {"copy": 1} and s["member"] and s["member"] != "owner-session"
    assert [l["result"] for l in s["legs"]] == ["hit", "miss", "miss", "hit"] and s["result"] == "lost"
    assert [l["actual"] for l in s["legs"]] == [30, 8, 25, 9]
    assert [l["awp_now"] for l in s["legs"]] == [False, True, False, False]


def test_hub_batches_hltv_event_ids_and_other_sports(stores):
    s = slip_audit.page("hub", 4, 0, 200, role, stores)["slips"][0]
    assert s["book"] == "prizepicks" and [l["sport"] for l in s["legs"]] == ["cs2", "nba", "cs2", "cs2"]
    assert s["legs"][0]["result"] == "hit" and s["legs"][0]["scope"] == "12"          # hltv:77 settles on hltv-77
    assert s["legs"][1]["result"] is None and s["result"] == "pending"


def test_paging_and_jsonl_sources(stores):
    a = slip_audit.page("recommended", 4, 0, 2, None, stores)
    b = slip_audit.page("recommended", 4, a["next"], 2, None, stores)
    c = slip_audit.page("recommended", 4, b["next"], 2, None, stores)
    assert [len(x["slips"]) for x in (a, b, c)] == [2, 2, 1] and a["next"] == 2 and b["next"] == 4 and c["next"] is None
    assert len({s["id"] for x in (a, b, c) for s in x["slips"]}) == 5
    bt = slip_audit.page("batches", 4, 0, 200, None, stores)["slips"][0]
    assert bt["legs"][1]["awp"] is True and bt["result"] == "lost"
    assert slip_audit.page("loaded", 4, 0, 200, None, stores)["slips"] == []
    assert slip_audit.page("tracked", 4, 0, 200, None, stores)["slips"] == []
    assert len(slip_audit.page("studio", 2, 0, 200, None, stores)["slips"]) == 1


def test_bad_source_and_limits(stores):
    with pytest.raises(ValueError):
        slip_audit.page("everything", 4, 0, 10, None, stores)
    assert slip_audit.page("recommended", 4, 0, 10 ** 6, None, stores)["limit"] == slip_audit.LIMIT_MAX


def test_admin_route_serves_a_page(stores, monkeypatch):
    """The real handler path (server.py /admin/slip_audit.json): admin only, the page as JSON, a bad source is a 400."""
    import server
    import slip_batches
    import slips
    import workspace_api
    from tests.test_audit13_site import get
    monkeypatch.setattr(server, "ADMIN_TOKEN", "tok-slipaudit")
    monkeypatch.setattr(slip_builder, "DB_PATH", stores["studio"])
    monkeypatch.setattr(workspace_api, "data_dir", lambda: stores["workspace"].parent)
    monkeypatch.setattr(slips, "RECOMMENDED", stores["recommended"])
    monkeypatch.setattr(slips, "LOADED", stores["loaded"])
    monkeypatch.setattr(slip_batches, "LOG", stores["batches"])
    h = {"X-Admin-Token": "tok-slipaudit"}
    for src in slip_audit.SOURCES:
        r = get(f"/admin/slip_audit.json?source={src}&n=4&limit=2", headers=h)
        assert r.code == 200, (src, r.body())
    assert get("/admin/slip_audit.json?source=studio", headers=h).body()["slips"][0]["result"] == "lost"
    assert get("/admin/slip_audit.json?source=nope", headers=h).code == 400
    assert get("/admin/slip_audit.json?source=studio").code != 200
