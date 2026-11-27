"""sports/expansion_settle.py: soccer / tennis slip legs settle from their own forecast's latest review, never from a missing or
void one, and the settled chance-band record counts each offer once (its last forecast). Real HTTP service with member auth."""
import json
import sqlite3

from sports import expansion_forecasts as forecasts, expansion_settle as es
from sports.tests.test_service import env, svc, _get, _auth  # noqa: F401


def _seed(sport, rows):
    with forecasts.connect(sport) as c:
        for fid, offer_key, payload, reviews in rows:
            c.execute("INSERT INTO forecasts VALUES (?,?,?,?,?,?)", (fid, offer_key, "eng.1", "e1", "2026-11-20T00:00:00Z", json.dumps(payload)))
            for i, rv in enumerate(reviews):
                c.execute("INSERT INTO reviews VALUES (?,?,?,?)", (f"{fid}-r{i}", fid, f"2026-11-21T0{i}:00:00Z", json.dumps(rv)))
        c.commit()


def _f(fid, p, side, grade, line=2.5, prop=None, made="2026-11-20T00:00:00Z"):
    """prop: the (event, player) the forecast is for; by default its own id (a separate prop)."""
    ev, pl = prop or (f"ev-{fid}", f"pl-{fid}")
    return {"id": fid, "p_side": p, "side": side, "grade": grade, "line": line, "stat": "aces", "scope": "full_game",
            "identity": {"event_id": ev, "player_id": pl}, "made_at": made}


def _rv(actual, line=2.5):
    outcome = None if actual is None else "push" if actual == line else "over" if actual > line else "under"
    return {"actual": actual, "outcome": outcome, "known_at": "2026-11-21T00:00:00Z"}


def test_results_latest_review_wins_and_missing_actual_has_no_answer(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    _seed("soccer", [("f1", "k1", _f("f1", .7, "over", "A"), [_rv(1), _rv(4)]),       # corrected: the last review wins
                     ("f2", "k2", _f("f2", .6, "under", "B+"), [_rv(None)]),          # did not play: no answer
                     ("f3", "k3", _f("f3", .6, "under", "B+"), [])])
    got = es.results("soccer", ["f1", "f2", "f3", "nope"])
    assert list(got) == ["f1"]
    assert got["f1"]["actual"] == 4 and got["f1"]["line"] == 2.5 and got["f1"]["review_id"] == "f1-r1"


def test_record_counts_each_offer_once_and_leaves_out_lean(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    _seed("tennis", [("a1", "k1", _f("a1", .70, "over", "A", prop=("e", "p"), made="2026-11-20T01:00:00Z"), [_rv(1)]),   # older: out
                     ("a2", "k9", _f("a2", .71, "over", "A", prop=("e", "p"), made="2026-11-20T02:00:00Z"), [_rv(5)]),   # same prop, other
                     # line / platform (another offer key), made last: the one counted - a win at 65%+
                     ("b1", "k2", _f("b1", .58, "under", "B+"), [_rv(3)]),           # under at 2.5, actual 3: a loss at 55-60%
                     ("c1", "k3", _f("c1", .52, "over", "Lean"), [_rv(9)]),          # Lean: out
                     ("d1", "k4", _f("d1", .62, "over", "A-", line=3.0), [_rv(3, 3.0)])])   # push: kept apart
    doc = es.record("tennis")
    by = {b["name"]: b for b in doc["bands"]}
    assert (by["65%+"]["wins"], by["65%+"]["losses"], by["65%+"]["printed"]) == (1, 0, 0.71)
    assert (by["55-60%"]["wins"], by["55-60%"]["losses"]) == (0, 1)
    assert (by["60-65%"]["wins"], by["60-65%"]["losses"], by["60-65%"]["pushes"]) == (0, 0, 1)
    assert by["50-55%"]["wins"] + by["50-55%"]["losses"] == 0
    assert doc["forecasts"] == 3


def test_http_routes_are_member_only_gated_and_bounded(svc, monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    _seed("soccer", [("f1", "k1", _f("f1", .7, "over", "A"), [_rv(4)])])
    es._RECORD.clear()
    path = "/api/sports/expansion/settle?sport=soccer&ids=f1,f9"
    assert _get(svc, path)[0] == 401
    code, body, _ = _get(svc, path, _auth())
    assert code == 200 and list(body["results"]) == ["f1"]
    assert _get(svc, "/api/sports/expansion/settle?sport=soccer&ids=" + ",".join(f"x{i}" for i in range(121)), _auth())[0] == 400
    assert _get(svc, "/api/sports/expansion/settle?sport=soccer&ids=bad%20id", _auth())[0] == 400
    code, body, _ = _get(svc, "/api/sports/expansion/record?sport=soccer", _auth())
    assert code == 200 and {b["name"]: b["wins"] for b in body["bands"]}["65%+"] == 1
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "0")
    assert _get(svc, path, _auth())[0] == 404
    assert _get(svc, "/api/sports/expansion/record?sport=soccer", _auth())[0] == 404


def test_reader_is_read_only_and_absent_store_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    assert es.results("soccer", ["f1"]) == {} and es.record("soccer") == {"bands": [], "forecasts": 0}
    _seed("soccer", [("f1", "k1", _f("f1", .7, "over", "A"), [_rv(4)])])
    c = es._reader("soccer")
    try:
        try:
            c.execute("DELETE FROM reviews")
            raise AssertionError("writable")
        except sqlite3.OperationalError:
            pass
    finally:
        c.close()


def test_admin_export_pages_every_forecast_with_its_latest_review(svc, monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    _seed("tennis", [("a1", "k1", _f("a1", .7, "over", "A"), [_rv(1), _rv(5)]),
                     ("b1", "k2", _f("b1", .58, "under", "B+"), []),
                     ("c1", "k3", _f("c1", .52, "over", "Lean"), [_rv(None)])])
    p = "/api/sports/admin/expansion/results?sport=tennis&limit=2"
    assert _get(svc, p)[0] == 401 and _get(svc, p, _auth())[0] == 403          # admin only
    code, body, _ = _get(svc, p, _auth("admin"))
    assert code == 200 and [r["id"] for r in body["rows"]] == ["a1", "b1"] and body["next"]
    a1 = body["rows"][0]
    assert a1["actual"] == 5 and a1["outcome"] == "over" and a1["grade"] == "A" and a1["reviewed"]
    assert body["rows"][1]["reviewed"] is False and body["rows"][1]["actual"] is None
    code, body, _ = _get(svc, p + f"&after={body['next']}", _auth("admin"))
    assert [r["id"] for r in body["rows"]] == ["c1"] and body["next"] is None and body["rows"][0]["outcome"] is None
    assert _get(svc, p.replace("limit=2", "limit=0"), _auth("admin"))[0] == 400



def test_did_not_play_voids_and_other_missing_results_stay_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    _seed("soccer", [("d1", "k1", _f("d1", .7, "over", "A"), [dict(_rv(None), participation=["did_not_play"])]),
                     ("d2", "k2", _f("d2", .7, "over", "A"), [dict(_rv(None), participation=["did_not_play", "played"])]),   # sources disagree
                     ("d3", "k3", _f("d3", .7, "over", "A"), [_rv(None)])])                                               # older review: no field
    got = es.results("soccer", ["d1", "d2", "d3"])
    assert list(got) == ["d1"] and got["d1"]["void"] is True and got["d1"]["actual"] is None


def test_record_reads_a_large_store_quickly(monkeypatch, tmp_path):
    """review 2026-11-27: the first record query took minutes on 8,000 forecasts. Two plain scans now."""
    import time
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    rows = [(f"f{i}", f"k{i}", _f(f"f{i}", .6 + (i % 30) / 100, "over", "A"), [_rv(3 if i % 2 else 1)] if i % 3 else []) for i in range(20000)]
    _seed("tennis", rows)
    t0 = time.monotonic()
    doc = es.record("tennis")
    assert time.monotonic() - t0 < 5
    assert doc["forecasts"] == sum(1 for i in range(20000) if i % 3)
    t0 = time.monotonic()
    page = es.export("tennis", 0, 2000)
    assert time.monotonic() - t0 < 5 and len(page["rows"]) == 2000
