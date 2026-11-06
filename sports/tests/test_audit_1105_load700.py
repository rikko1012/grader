"""700 members at once on the sports routes the 2026-11-05 audit fix touched: the PrizePicks / Underdog / Most-consistent
sheets and the public Results file (rp-2). Every answer is a worker-built file served from api_board's file cache: no
5xx, no rebuild in a member request, the S1/S2 / S3 rows stay out under load. Fixture world only (test_ui_snapshots)."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from sports import results_public, sheets, store, web
from sports.tests.test_ui_snapshots import NOW, built  # noqa: F401 - the shared built world


def test_700_members_on_sheets_and_results(built, monkeypatch):            # noqa: F811
    assert sheets.build("nba", now=NOW)["ok"]
    con = store.connect()
    results_public.build(con, now=NOW)
    con.close()
    builds = []
    monkeypatch.setattr(sheets, "build", lambda *a, **k: builds.append(a))           # never in a member request
    monkeypatch.setattr(results_public, "build", lambda *a, **k: builds.append(a))
    paths = [("/api/sports/sheets", {"league": "nba", "sheet": s}) for s in sheets.SHEETS] + \
            [("/api/sports/results/public", {})]

    def member(i):
        path, q = paths[i % len(paths)]
        code, body, _ = web.dispatch(web.Request(path=path, query=q, role="member", member=f"m_{i}"))
        return code, path, body

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=700) as ex:
        out = list(ex.map(member, range(700)))
    took = time.time() - t0
    assert [c for c, _, _ in out if c != 200] == []
    assert builds == []
    for code, path, body in out[:len(paths)]:
        doc = json.loads(body)
        if path.endswith("public"):
            assert doc["version"] == "rp-2" and "raw_counts" in doc["leagues"]["nba"]
        elif doc.get("markets") is not None:
            assert all(m["k"].rsplit("|", 1)[-1] == "full_game" for m in doc["markets"])
        else:
            assert all(r["k"].rsplit("|", 1)[-1] == "full_game" for r in doc["rows"])
    assert took < 60, took
