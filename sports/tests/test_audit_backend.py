"""Sports backend audit 2026-10-26: one regression test per bug fixed (service / gateway tokens, caches, settlement,
slips, picks, market view, compare, admin paging). Every name, line and probability here is a TEST FIXTURE."""
import http.client
import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from sports import (api_board, api_compare, authtoken, game_lines, market_view, picks, service, settlement, store,
                    web)
from sports.contracts import SideQuote
from sports.slips import log as slip_log
from sports.slips import settle as slip_settle
from sports.tests import helpers as h
from sports.tests import slip_world as w

SECRET = "s" * 40


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.delenv("SPORTS_TOKEN_SECRET_PREV", raising=False)
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(web, "_CACHE", {})
    yield


def _req(path, body=None, role="member", member="m_owner", query=None):
    return web.Request(path=path, method="POST" if body is not None else "GET", query=query or {},
                       body=body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None
                                                                  else b""), member=member, role=role)


def _call(req):
    code, body, _ = web.dispatch(req)
    return code, json.loads(body)


# ------------------------------------------------------------------------------- 1. non-ASCII token / admin header
@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(service, "_STATE", {"draining": False})
    srv = service.Server(("127.0.0.1", 0), threads=4)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _raw_get(port, path, headers):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.putrequest("GET", path)
    for k, v in headers.items():
        c.putheader(k, v.encode("latin-1"))
    c.endheaders()
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, json.loads(data) if data else None


def test_non_ascii_bearer_and_admin_header_answer_401_403_not_a_dropped_connection(svc, monkeypatch):
    with pytest.raises(authtoken.TokenError) as e:
        authtoken.verify("st1.\xe9t\xe9.\xe9")
    assert e.value.reason == "malformed"                       # was UnicodeEncodeError out of the MAC step
    code, body = _raw_get(svc, "/api/sports/status", {"Authorization": "Bearer st1.\xe9t\xe9.x"})
    assert code == 401 and body["error"] == "members_only"
    monkeypatch.setenv("ADMIN_TOKEN", "a" * 30)
    code, body = _raw_get(svc, "/api/sports/admin/ops", {"X-Admin-Token": "\xe9" * 30})
    assert code == 403                                         # was TypeError in compare_digest -> no response


# ------------------------------------------------------------------------------------ 2. expired cache entries
def test_expired_cache_entries_are_released_not_kept_until_256_keys(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(web.time, "time", lambda: clock[0])
    monkeypatch.setattr(web, "_SWEPT", {"t": 0.0})
    web.cached(("model_snapshot", "forecasts_nba.json", 1), lambda: "old copy", ttl=300)
    assert len(web._CACHE) == 1
    clock[0] += 301                                            # the file changed: a new mtime key is loaded later
    web.cached(("model_snapshot", "forecasts_nba.json", 2), lambda: "new copy", ttl=300)
    assert list(web._CACHE) == [("model_snapshot", "forecasts_nba.json", 2)]
    assert web.cached(("model_snapshot", "forecasts_nba.json", 2), lambda: "never") == "new copy"


# ----------------------------------------------------------------------------------- 3. snapshot read stampede
def test_snapshot_file_is_read_and_parsed_once_under_simultaneous_misses(tmp_path, monkeypatch):
    p = tmp_path / "forecasts.json"
    p.write_text(json.dumps({"forecasts": {"fc-0123abcd": {"x": 1}}}), encoding="utf-8")
    monkeypatch.setattr(api_board, "_FILES", {})
    monkeypatch.setattr(api_board, "_PARSED", {})
    reads, loads = [0], [0]
    real_read, real_loads = type(p).read_bytes, api_board.json.loads

    def slow_read(self):
        reads[0] += 1
        time.sleep(0.05)
        return real_read(self)

    def counting_loads(s, *a, **k):
        loads[0] += 1
        return real_loads(s, *a, **k)
    monkeypatch.setattr(type(p), "read_bytes", slow_read)
    monkeypatch.setattr(api_board.json, "loads", counting_loads)
    got = []
    ts = [threading.Thread(target=lambda: got.append(api_board.parsed(p))) for _ in range(16)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(got) == 16 and all(g is got[0] for g in got)
    assert reads[0] == 1 and loads[0] == 1
    with pytest.raises(api_board.NotReady):
        api_board.parsed(tmp_path / "missing.json")


def test_forecast_route_uses_the_shared_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(api_board, "_root", lambda: tmp_path)
    (tmp_path / "nba").mkdir()
    (tmp_path / "nba" / "forecasts.json").write_text(json.dumps({"version": "v", "built_at": "t", "forecasts": {
        "fc-0123abcd": {"forecast_id": "fc-0123abcd"}}}), encoding="utf-8")
    code, b = _call(_req("/api/sports/forecast", query={"league": "nba", "id": "fc-0123abcd"}))
    assert code == 200 and b["forecast"]["forecast_id"] == "fc-0123abcd"
    assert _call(_req("/api/sports/forecast", query={"league": "nba", "id": "fc-ffffffff"}))[0] == 404
    assert _call(_req("/api/sports/forecast", query={"league": "nfl", "id": "fc-ffffffff"}))[0] == 503


# ------------------------------------------------------------------------------ 4. books without odds not shown
def _book(platform, oid, line=20.5, over=1.80, under=2.05):
    sides = (SideQuote("over", over is not None, "decimal" if over else "none", price=over),
             SideQuote("under", under is not None, "decimal" if under else "none", price=under))
    return h.offer(platform=platform, oid=oid, line=line, variant="main", sides=sides, observed_at=h.T0)


def test_a_book_without_odds_is_never_listed_but_its_priced_line_is():
    pp = h.offer()
    fd_dead = _book("fanduel", "fd:dead", over=None, under=None)           # suspended: no price at all
    br_dead = _book("betrivers", "br:dead", line=20.5, over=None, under=None)
    br_live = _book("betrivers", "br:live", line=21.5)                     # same book, a priced line further away
    kx = h.offer(platform="kalshi", oid="kx:1", variant="ladder_rung",
                 sides=(SideQuote("over", True, "exchange_yes_no"), SideQuote("under", True, "exchange_yes_no")))
    its = [(f"rid:{o.platform}:{o.offer_id}", o, {"last_seen_at": o.observed_at}) for o in (pp, fd_dead, br_dead,
                                                                                            br_live, kx)]
    mv = market_view.row_view([(its[0][0], pp)], its, h.event(), "2026-10-23T20:05:00Z", "nba", 0.0)
    by = {b["p"]: b for b in mv["bk"]}
    assert "fanduel" not in by and "kalshi" not in by                      # was listed with kind "none"
    assert by["betrivers"]["l"] == 21.5 and by["betrivers"]["kind"] == "devig"
    assert all(b["kind"] != "none" for b in mv["bk"])


class _FakeGameDb:
    def __init__(self, snaps):
        self.snaps = snaps

    def all(self, sql, args):
        return self.snaps if "sports_game_snapshots" in sql else []


def test_game_lines_keep_the_last_priced_row_when_espn_empties_pickcenter():
    priced = {"spread": -3.5, "total": 221.5, "home_ml": -160, "away_ml": 135, "provider": "Book A"}
    db = _FakeGameDb([{"observed_at": "2026-10-23T18:00:00Z", "data": json.dumps([priced])},
                      {"observed_at": "2026-10-23T19:00:00Z", "data": json.dumps([])},
                      {"observed_at": "2026-10-23T19:30:00Z", "data": json.dumps([{"provider": "Book A"}])}])
    v = game_lines._event_lines(db, "nba", "1", ("h",), ("a",))
    assert v["spread"] == -3.5 and v["total"] == 221.5 and v["observed_at"] == "2026-10-23T18:00:00Z"
    assert len(v["history"]) == 1
    none = game_lines._event_lines(_FakeGameDb([{"observed_at": "t", "data": "[]"}]), "nba", "1", ("h",), ("a",))
    assert none["provider"] is None and none["observed_at"] is None        # never a book name without odds


# ------------------------------------------------------------- 5. a partial box score never un-settles a result
def test_unresolved_reread_never_supersedes_a_decided_settlement():
    with store.opened() as con:
        ev = h.event(status="final")
        o = h.offer(line=23.5)
        s1 = settlement.settle_offer(o, {"pts": h.stat(value=25.0)}, ev, "2026-10-24T03:00:00Z")
        assert settlement.record(con, s1)["inserted"] == 2
        gap = settlement.settle_offer(o, {}, ev, "2026-10-24T05:00:00Z")     # ESPN lost the player on a re-read
        assert {s.status for s in gap} == {"unresolved"}
        c = settlement.record(con, gap)
        assert c["kept"] == 2 and c["revision"] == 0
        assert store.latest(con, "settlement", "prizepicks", "pp:1|over").status == "win"
        fix = settlement.settle_offer(o, {"pts": h.stat(value=22.0)}, ev, "2026-10-24T06:00:00Z")
        assert settlement.record(con, fix)["revision"] == 2                  # a real correction still lands


def test_completed_game_with_an_empty_box_score_is_not_settled_this_run(monkeypatch):
    from sports import adapters, espn, worker
    monkeypatch.setattr(espn, "summary_and_raw", lambda lg, eid, refresh=False: ({"completed": True}, None))
    box = {"n": 0}

    def espn_box(summ, lg, event_id, observed_at):
        b = adapters.Batch()
        if box["n"]:
            b.add(h.stat(value=25.0, eid=event_id, observed_at=observed_at))
        return b
    monkeypatch.setattr(adapters, "espn_box", espn_box)
    with store.opened() as con:
        store.put(con, h.event(start="2026-10-23T23:00:00Z"))
        store.put(con, h.offer(listed_start_utc="2026-10-23T23:00:00Z"))
        out = worker.settle_league(con, "nba", now="2026-10-24T06:00:00Z")
        assert out["empty_box"] == 1 and out["settlements"]["inserted"] == 0
        assert store.latest(con, "settlement", "prizepicks", "pp:1|over") is None   # not 'unresolved' for everyone
        box["n"] = 1
        out = worker.settle_league(con, "nba", now="2026-10-24T07:00:00Z")
        assert out["settlements"]["inserted"] == 2
        assert store.latest(con, "settlement", "prizepicks", "pp:1|over").status == "win"


# --------------------------------------------------------------- 6. slip leg settles from the other side's record
def test_slip_leg_settles_when_only_the_other_side_has_a_settlement():
    with store.opened() as con:
        o = w.offer("h1", "T1", 1, sides=("over",))                         # only Over offered at settlement
        store.put(con, o)
        settlement.record(con, [w.settlement(o, "over", "win", 25.0)])
        legs = [{"platform": o.platform, "offer_id": o.offer_id, "side": "under", "line": 20.5}]
        by = slip_settle.current_settlements(con, legs)
        assert by[(o.platform, o.offer_id, "under")] is not None             # was None -> pending forever
        res = slip_settle.settle_slip({"platform": "underdog", "entry_type": "standard", "legs": legs}, by)
        assert res["leg_results"][0]["status"] == "loss"


# --------------------------------------------------------------------------- 7. slip endpoints input validation
def test_slip_settle_and_validate_refuse_malformed_or_oversized_legs():
    leg = {"platform": "underdog", "offer_id": "ud:x", "side": "over"}
    for slip, why in (({"league": "nba", "platform": "underdog", "legs": ["x"]}, "object"),
                      ({"league": "nba", "platform": "underdog", "legs": [leg] * 9}, "1-8"),
                      ({"league": "nba", "platform": "underdog", "legs": [dict(leg, side=["over"])]}, "needs")):
        code, b = _call(_req("/api/sports/slips/settle", {"slip": slip}))
        assert code == 400 and why in b["detail"], (slip, b)
    code, b = _call(_req("/api/sports/slips/validate", {"mode": "load", "slip": {
        "league": "nba", "platform": "underdog", "legs": [7]}}))
    assert code == 400 and "object" in b["detail"]                             # was AttributeError -> 503


def test_copy_check_trusts_the_pools_lock_over_the_clients_start_time():
    from sports.slips import validate
    pool = {"platform": "underdog", "snapshot_at": "t", "snapshot_id": "ps-1", "legs": [],
            "state": {"ud:x": ("offer:ud:x:r1", 1, 20.5, False, "started / locked", -1)}}
    slip = {"platform": "underdog", "entry_type": "standard", "legs": [
        {"platform": "underdog", "offer_id": "ud:x", "record_id": "offer:ud:x:r1", "side": "over", "line": 20.5,
         "start_utc": "2099-01-01T00:00:00Z", "team": "T1"}]}
    res = validate.check(slip, pool, mode="copy", now=w.NOW)
    assert not res["ok"] and "locked" in [p["code"] for p in res["problems"]] and "copy_text" not in res


# ----------------------------------------------------------------------- 8. a malformed pick spools nothing
@pytest.fixture
def tw(monkeypatch):
    monkeypatch.setattr(picks, "clock", lambda: w.NOW)
    with store.opened() as con:
        legs = w.world(con, [("h1", "T1", 1, "over", 0.61)])
    yield legs


def test_a_malformed_pick_in_a_batch_refuses_the_request_before_any_pick_is_spooled(tw):
    rid, f = tw["h1"]
    good = {"platform": "underdog", "record_id": rid, "side": "over", "forecast_id": f.forecast_id,
            "shown": {"p_over": f.p_over, "p_under": f.p_under, "p_push": f.p_push}}
    before = picks.stats()["spooled"]
    code, b = _call(_req("/api/sports/picks/track", {"picks": [good, dict(good, side="sideways")]}))
    assert code == 400
    assert picks.stats()["spooled"] == before                                  # was: the first pick was tracked
    code, b = _call(_req("/api/sports/picks/track", {"picks": [good, "x"]}))
    assert code == 400 and picks.stats()["spooled"] == before
    code, b = _call(_req("/api/sports/picks/track", {"picks": [good]}))
    assert code == 200 and b["results"][0]["ok"]
    picks.drain()


def test_a_pick_on_a_postponed_game_says_postponed_not_started(tw):
    rid, f = tw["h1"]
    with store.opened() as con:
        store.put(con, replace(w.ev(1, status="postponed"), observed_at="2026-10-23T20:02:00Z",
                               known_at="2026-10-23T20:02:00Z"))
    code, b = _call(_req("/api/sports/picks/track", {"picks": [{"platform": "underdog", "record_id": rid,
                                                                "side": "over"}]}))
    r = b["results"][0]
    assert code == 200 and not r["ok"] and r["code"] == "started" and "postponed" in r["text"]


# --------------------------------------------------------------------------- 9. compare: postponed / cancelled
def test_compare_names_a_postponed_game_and_closes_a_cancelled_one(tmp_path, monkeypatch):
    monkeypatch.setattr(api_board, "_root", lambda: tmp_path)
    monkeypatch.setattr(api_compare, "_INDEX", {})
    fut = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    evs = {"g1": {"start_utc": fut, "status": "postponed"}, "g2": {"start_utc": fut, "status": "cancelled"}}
    q = (lambda oid, line: {"r": f"offer:prizepicks:{oid}:r2:ab", "p": "prizepicks", "l": line, "o": [1, 1]})
    (tmp_path / "nba").mkdir()
    (tmp_path / "nba" / "board.json").write_text(json.dumps({"events": evs, "markets": [
        {"k": "g1|a|points", "e": "g1", "q": [q("a1", 20.5)], "kn": []},
        {"k": "g2|b|points", "e": "g2", "q": [q("b1", 18.5)], "kn": []}]}), encoding="utf-8")
    code, d = _call(_req("/api/sports/compare/check", query={"league": "nba", "items": "prizepicks~a1~20.5,"
                                                                                       "prizepicks~b1~18.5"}))
    assert code == 200 and d["ok"] == 0
    assert [(i["status"], i["text"]) for i in d["items"]] == [("started", "Game postponed"),
                                                              ("started", "Game cancelled")]   # cancelled was "ok"


# ------------------------------------------------------------------------------ 10. bounded settle scans
def _pick_row(pid, tracked_at):
    return {"kind": "pick", "pick_id": pid, "member": "m", "tracked_at": tracked_at, "platform": "underdog",
            "offer_id": "ud:" + pid, "record_id": "offer:ud:" + pid, "side": "over", "line": 20.5}


def test_pick_settle_skips_old_decided_picks_but_retries_old_undecided_ones(tmp_path):
    pc = picks.connect(tmp_path / "p.db")
    for pid, t in (("new", "2026-10-25T20:00:00Z"), ("old_done", "2026-09-20T20:00:00Z"),
                   ("old_open", "2026-09-20T20:00:00Z"), ("ancient", "2026-07-01T20:00:00Z")):
        r = _pick_row(pid, t)
        pc.execute("INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pid, "m", t, "nba", "underdog", r["offer_id"], r["record_id"], 1, "nba:espn:1", "over", 20.5,
                    None, "board", json.dumps(r)))
    pc.execute("INSERT INTO settlements VALUES (?,?,?,?,?,?,?,?,?)",
               ("old_done", 1, "win", "over", 25.0, "st-x", 1, "2026-09-21T06:00:00Z", None))
    pc.commit()
    with store.opened() as con:
        c = picks.settle_all(con, pc, now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert c["picks"] == 2 and c["skipped_final"] == 2 and c["pending"] == 2   # 'new' + 'old_open' re-read
    pc.close()


def test_slip_settle_reads_payloads_in_chunks_within_the_window(tmp_path):
    lc = slip_log.connect(tmp_path / "s.db")
    legs = [{"platform": "underdog", "offer_id": "ud:q", "side": "over", "line": 20.5}]
    for i, t in enumerate(["2026-10-25T20:00:00Z"] * 3 + ["2026-08-01T20:00:00Z"] * 2):
        lc.execute("INSERT INTO slips VALUES (?,?,?,?,?,?,?,?,?)",
                   (f"k{i}", f"s{i}", "batch", "b", t, "underdog", "standard", "nba",
                    json.dumps({"slip_id": f"s{i}", "platform": "underdog", "entry_type": "standard", "legs": legs})))
    lc.commit()
    with store.opened() as con:
        c = slip_log.settle_all(con, lc, now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert c["slips"] == 3 and c["skipped_final"] == 2 and c["pending"] == 3
    lc.close()


def test_settle_window_measures_back_from_the_newest_record():
    rc, gu = picks.settle_window("2026-10-25T00:00:00Z", now=datetime(2027, 6, 1, tzinfo=timezone.utc))
    assert rc == "2026-10-04T00:00:00Z" and gu == "2026-08-26T00:00:00Z"
    rc2, _ = picks.settle_window(None, now=datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert rc2 == "2026-10-05T00:00:00Z"


# ------------------------------------------------------------------------------ 11. admin sanity log paging
def test_admin_sanity_log_is_paged(tmp_path, monkeypatch):
    from sports import snapshots
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path)
    (tmp_path / "nba").mkdir()
    (tmp_path / "nba" / "sanity.json").write_text(json.dumps({"entries": [{"i": i} for i in range(5)]}),
                                                  encoding="utf-8")
    code, b = _call(_req("/api/sports/admin/sanity", role="admin", query={"league": "nba", "limit": "2"}))
    assert code == 200 and [e["i"] for e in b["nba"]["entries"]] == [0, 1] and b["nba"]["next_offset"] == 2
    code, b = _call(_req("/api/sports/admin/sanity", role="admin", query={"league": "nba", "limit": "2",
                                                                         "offset": "4"}))
    assert [e["i"] for e in b["nba"]["entries"]] == [4] and b["nba"]["next_offset"] is None
    assert b["nba"]["total"] == 5


# ------------------------------------------------------------------------- 12. assistant cancel: non-object body
def test_assistant_cancel_with_a_json_list_is_400_not_503(monkeypatch):
    code, b = _call(_req("/api/sports/assistant/cancel", [1, 2]))
    assert code == 400


# --------------------------------------------------------------------- 15. lab export can be paged (admin)
def test_lab_export_pages_on_request_and_stays_whole_by_default(monkeypatch):
    from sports.lab import jobs
    rows = [{"row_key": f"r{i}", "split": "test"} for i in range(5)]
    monkeypatch.setattr(jobs, "predictions", lambda con, eid: list(rows))
    q = {"id": "lx-1"}
    code, b = _call(_req("/api/sports/admin/lab/export", role="admin", query=q))
    assert code == 200 and b["rows"] == 5 and b["total"] == 5 and b["next_offset"] is None
    code, b = _call(_req("/api/sports/admin/lab/export", role="admin", query=dict(q, limit="2", offset="2")))
    assert [r["row_key"] for r in b["predictions"]] == ["r2", "r3"] and b["next_offset"] == 4
    code, body, ctype = web.dispatch(_req("/api/sports/admin/lab/export", role="admin",
                                          query=dict(q, format="csv", limit="2", offset="4")))
    assert code == 200 and body.decode().splitlines()[1:] == ["r4,test" + "," * 13]
    assert _call(_req("/api/sports/admin/lab/export", role="admin", query=dict(q, limit="x")))[0] == 400


# ------------------------------------------- 14. public Results streams settlements by event (bounded memory)
def test_public_results_rows_are_streamed_by_event_and_equal_the_full_read(monkeypatch):
    from sports import results_public, snapshots
    monkeypatch.setattr(snapshots, "RESULTS_EVENTS_PER_PASS", 2)
    with store.opened() as con:
        for n in range(1, 6):
            eid, oid = f"nba:espn:{n}", f"pp:{n}"
            ev = h.event(eid=eid, start="2026-10-23T23:00:00Z", status="final")
            store.put(con, ev)
            o = h.offer(oid=oid, event_id=eid, listed_start_utc="2026-10-23T23:00:00Z")
            orid = store.put(con, o)["record_id"]
            store.put(con, h.forecast(event_id=eid, line=20.5, offer_snapshot_ids=(orid,), model_version="sm-1",
                                      chosen_side="over", p_over=0.55, p_under=0.45, p_push=0.0,
                                      p_over_given_no_push=0.55, p_under_given_no_push=0.45))
            settlement.record(con, settlement.settle_offer(o, {"pts": h.stat(value=18.0 + n, eid=eid)}, ev,
                                                           "2026-10-24T03:00:00Z"))
        chunks = list(results_public.settlement_chunks(con, "nba"))
        streamed = results_public.rows_for(con, "nba")
        full = results_public._rows_from(con, "nba", snapshots._current_settlements(con, "nba"))
    assert [len({s.event_id for s in c}) for c in chunks] == [2, 2, 1]
    key = (lambda r: r["forecast_id"])
    assert sorted(streamed, key=key) == sorted(full, key=key) and len(streamed) == 5
    assert sorted(r["outcome"] for r in streamed) == ["over", "over", "over", "under", "under"]


# --------------------------------------------- 13. the prebuilt slip pool uses the board's market + cal-mi-1 chance
def test_prebuilt_slip_pool_gets_the_same_market_and_calibrated_chances_as_the_on_demand_pool(monkeypatch):
    from sports import config, prebuilt
    from sports.slips import pool
    rid = "offer:underdog:ud:1:r1:ab"
    board = {"events": {}, "markets": [{"k": "k", "e": "g", "mk": {"l": 20.5, "c": {"po": 0.55, "pu": 0.45, "n": 2}},
                                        "q": [{"r": rid, "p": "underdog", "l": 20.5,
                                               "ca": {"po": 0.58, "pu": 0.42, "pp": 0.0, "f": "fc-1"}}]}]}
    d = config.DATA_DIR / "snapshots" / "nba"
    d.mkdir(parents=True)
    (d / "board.json").write_text(json.dumps(board), encoding="utf-8")
    seen = {}

    def fake_build(con, lg, plat, policy="member", market_at=None, cal_at=None, **kw):
        seen[(lg, plat)] = (market_at, cal_at)
        return {"legs": []}
    monkeypatch.setattr(pool, "build", fake_build)
    out = prebuilt.slips_pool(None)
    assert set(out) == {("slips_pool", lg, p, "member") for lg in ("nba", "nfl") for p in ("underdog", "prizepicks")}
    mk, ca = seen[("nba", "underdog")]
    assert mk[rid] == (0.55, 0.45) and ca[rid]["po"] == 0.58        # was: built with neither (raw chance, no N4)
    assert seen[("nfl", "underdog")] == ({}, {})
