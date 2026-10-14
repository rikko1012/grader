"""Slip studio (slip_builder studio-1): batch-wide diversity, size planning, overlap caps, shortages, stored exposure,
idempotency, concurrency, verify-before-copy, one platform / entry type per slip, moneylines apart. No network."""
import itertools
import json
import math
import random
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import slip_builder as sb

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
M = "MODEL1"


@pytest.fixture(autouse=True)
def private_db(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "slip_studio.db")


def row(i, match, **kw):
    """One graded board row (server.board_payload shape)."""
    r = {"id": f"r{i}", "book": "underdog", "player": f"p{i}", "nick": f"p{i}", "player_id": 1000 + i,
         "team": f"T{match}a", "team_name": f"T{match}a", "opponent": f"T{match}b", "opp_name": f"T{match}b",
         "match_slug": f"m{match}", "start": (NOW + timedelta(hours=3)).isoformat(), "prop": "kills", "scope": "12",
         "line": 30.5, "graded_line": 30.5, "graded_model": M, "side": "More", "p": 0.60, "grade": "B+", "units": 1.0,
         "state": "GRADE", "graded_at": (NOW - timedelta(minutes=5)).isoformat(), "awp": False, "awp_conf": "confirmed",
         "igl": False, "higher": 1.0, "lower": 1.0, "alt": False, "ud_player_id": f"u{i}"}
    r.update(kw)
    return r


def board(n_matches, per_match=2, book="underdog", seed=1, **kw):
    rnd = random.Random(seed)
    rows, i = [], 0
    for m in range(n_matches):
        for k in range(per_match):
            rows.append(row(i, m, book=book, p=round(rnd.uniform(0.56, 0.74), 3), **kw))
            i += 1
    return rows


def all_slips(res):
    return [s for n in (2, 3, 4) for s in res["slips"][n]]


def keys(s):
    for l in s["legs"]:
        yield l["player_id"], l["match_slug"], (l["player_id"], l["prop"], l["scope"])


def assert_no_repeats(slips):
    pl, ma, pr = [], [], []
    for s in slips:
        for p, m, q in keys(s):
            pl.append(p); ma.append(m); pr.append(q)
    assert len(pl) == len(set(pl)), "player repeated"
    assert len(ma) == len(set(ma)), "match repeated"
    assert len(pr) == len(set(pr)), "prop repeated"


# ---------------------------------------------------------------- batch-wide diversity
def test_no_repeated_prop_player_or_match_across_all_sizes():
    rows = board(20)
    # the same player's headshots line (shared exposure with his kills) and a namesake row by nick only
    rows.append(row(500, 0, player="p0", nick="p0", player_id=1000, prop="hs", line=15.5, graded_line=15.5, p=0.9, side="Less"))
    res = sb.build(rows, {"count": 3}, model=M, now=NOW)
    slips = all_slips(res)
    assert [len(res["slips"][n]) for n in (4, 3, 2)] == [3, 2, 1]        # 20 matches: 12 + 6 + 2 legs
    assert_no_repeats(slips)
    for s in slips:
        assert len(s["legs"]) == s["n"]
    e = res["exposure"]
    assert e["max_uses"] == {"prop": 1, "player": 1, "match": 1}
    assert e["distinct_matches"] == e["legs"] == 20


def test_opposing_sides_of_one_market_never_in_one_batch():
    rows = [row(1, 1, side="More", p=0.7), row(2, 1, player="p1", nick="p1", player_id=1001, side="Less", p=0.69,
                                                  line=31.5, graded_line=31.5)]
    rows += board(6, seed=3)[2:]
    res = sb.build(rows, {"overlap": "limited", "max_uses": {"player": 3, "match": 3}, "sizes": [2], "count": 10}, model=M, now=NOW)
    sides = [l["side"] for s in all_slips(res) for l in s["legs"] if l["player_id"] == 1001]
    assert sides and len(set(sides)) == 1                               # he is used, on one side only


def test_four_pick_section_not_starved_by_two_picks():
    rows = board(9)
    res = sb.build(rows, {"sizes": [2, 3, 4], "count": 3}, model=M, now=NOW)
    assert res["plan"] == {4: 2, 3: 0, 2: 0}                          # planned before picks, largest first
    assert len(res["slips"][4]) == 2 and not res["slips"][2]
    assert "not relaxed" in res["shortage"][2] and "not relaxed" in res["shortage"][3]
    # the 4-pick slips take the strongest legs (one per match)
    best = sorted((max(r["p"] for r in rows if r["match_slug"] == f"m{m}") for m in range(9)), reverse=True)
    got = sorted((l["p"] for s in res["slips"][4] for l in s["legs"]), reverse=True)
    assert got == best[:8]


def test_either_side_semantics():
    rows = board(8, seed=4)
    for r in rows[::2]:
        r["side"] = "Less"
    either = sb.build(rows, {"side": "either", "sizes": [2], "count": 4}, model=M, now=NOW)
    more = sb.build(rows, {"side": "More", "sizes": [2], "count": 4}, model=M, now=NOW)
    less = sb.build(rows, {"side": "Less", "sizes": [2], "count": 4}, model=M, now=NOW)
    assert {l["side"] for s in all_slips(more) for l in s["legs"]} == {"More"}
    assert {l["side"] for s in all_slips(less) for l in s["legs"]} == {"Less"}
    assert {l["side"] for s in all_slips(either) for l in s["legs"]} == {"More", "Less"}
    assert any(e["reason"] == "side not selected" for e in more["excluded"])
    # "either" never forces a mix: when every top pick is an Over the slip is all Overs
    rows2 = board(4, per_match=1, seed=5) + [row(90 + m, 10 + m, side="Less", p=0.56) for m in range(2)]
    for r in rows2[:4]:
        r["p"] = 0.72
    res = sb.build(rows2, {"side": "either", "sizes": [4], "count": 1}, model=M, now=NOW)
    assert {l["side"] for l in res["slips"][4][0]["legs"]} == {"More"}


def test_overlap_cap_respected_and_visible():
    rows = board(5, per_match=2, seed=6)
    res = sb.build(rows, {"overlap": "limited", "max_uses": {"player": 2, "match": 2}, "sizes": [2, 3], "count": 5},
                   model=M, now=NOW)
    slips = all_slips(res)
    pc, mc = {}, {}
    for s in slips:
        ids = [l["player_id"] for l in s["legs"]]
        ms = [l["match_slug"] for l in s["legs"]]
        assert len(set(ids)) == len(ids) and len(set(ms)) == len(ms)     # never twice inside one slip
        for p in ids:
            pc[p] = pc.get(p, 0) + 1
        for m in ms:
            mc[m] = mc.get(m, 0) + 1
    assert max(pc.values()) <= 2 and max(mc.values()) <= 2
    assert max(mc.values()) == 2                                         # overlap happened (10 legs, 5 matches) ...
    e = res["exposure"]
    assert e["caps"] == {"player": 2, "match": 2}
    assert e["repeated_matches"] and all(x["uses"] <= 2 for x in e["repeated_matches"])
    assert any(s["repeats"] for s in slips)                              # ... and is shown on the slips
    assert len({frozenset(l["row_id"] for l in s["legs"]) for s in slips}) == len(slips)   # no slip twice


def test_overlap_prefers_fresh_picks_before_reuse():
    rows = board(8, per_match=1, seed=7)
    res = sb.build(rows, {"overlap": "limited", "sizes": [4], "count": 2}, model=M, now=NOW)
    assert_no_repeats(all_slips(res))                                    # 8 matches fill 2 slips without any reuse


def test_shortage_message_instead_of_relaxing():
    rows = board(3)
    res = sb.build(rows, {"sizes": [4], "count": 2}, model=M, now=NOW)
    assert res["slips"][4] == []
    assert "3 unused eligible matches" in res["shortage"][4] and "not relaxed" in res["shortage"][4]
    res = sb.build([], {"sizes": [2]}, model=M, now=NOW)
    assert "No eligible picks" in res["shortage"]["all"]
    lim = sb.build(board(3), {"sizes": [4], "overlap": "limited"}, model=M, now=NOW)
    assert lim["slips"][4] == [] and "overlap caps" in lim["shortage"][4]


def test_eligibility_exclusions():
    rows = [row(1, 1), row(2, 2, grade="B-"), row(3, 3, grade="LEAN", units=0), row(4, 4, graded_line=29.5),
            row(5, 5, graded_model="OLD"), row(6, 6, alt=True), row(7, 7, prop="hs", awp=True), row(8, 8, igl=None),
            row(9, 9, prop="hs", awp_conf="inferred"), row(10, 10, start=(NOW - timedelta(minutes=1)).isoformat()),
            row(11, 11, higher=None), row(12, 12, state="SCOUT"), row(13, 13, book="prizepicks")]
    res = sb.build(rows, {"sizes": [2]}, model=M, now=NOW)
    why = {e["reason"]: e["count"] for e in res["excluded"]}
    assert why == {"below B / no units (Lean, B-)": 2, "line changed since the grade": 1, "grade from an older model": 1,
                   "Underdog alternate line": 1, "AWPer HS / IGL kills": 1, "role not confirmed": 1,
                   "AWP role only inferred": 1, "started / locked": 1, "side not offered by Underdog": 1,
                   "not graded / scout": 1, "other platform": 1}
    assert res["eligible"]["lines"] == 1


# ---------------------------------------------------------------- platform / entry type
def test_one_platform_and_entry_type_per_slip():
    rows = board(6, book="underdog", seed=8) + [dict(r, id="pp" + r["id"]) for r in board(6, book="prizepicks", seed=9)]
    gob = [row(200 + m, 20 + m, book="prizepicks", odds_type="goblin", p=0.8) for m in range(4)]
    gob.append(row(300, 30, book="prizepicks", odds_type="goblin", side="Less", p=0.8))
    promo = [row(400, 40, book="prizepicks", odds_type="promo", p=0.9)]
    for book in ("underdog", "prizepicks"):
        res = sb.build(rows + gob + promo, {"book": book, "sizes": [2, 3]}, model=M, now=NOW)
        for s in all_slips(res):
            assert s["book"] == book and {l["book"] for l in s["legs"]} == {book}
            assert {l["market_type"] for l in s["legs"]} == {"standard"}
    pp = sb.build(rows + gob + promo, {"book": "prizepicks", "market": "goblin", "sizes": [2]}, model=M, now=NOW)
    legs = [l for s in all_slips(pp) for l in s["legs"]]
    assert legs and {l["market_type"] for l in legs} == {"goblin"} and {l["side"] for l in legs} == {"More"}
    why = {e["reason"] for e in pp["excluded"]}
    # 2026-10-14 owner: the sides the FEED offers - a goblin Less leg with no feed confirmation is excluded as such
    assert any(w.startswith("Less not offered or not confirmed by the PrizePicks feed") for w in why)
    assert "PrizePicks promo line (payout differs)" in why
    confirmed = row(301, 31, book="prizepicks", odds_type="goblin", side="Less", p=0.8)
    confirmed.update(under_available=True, direction_basis="feed allowed_wager_types: over, under")      # SYNTHETIC
    assert sb.why_excluded(confirmed, sb.normalize_settings({"book": "prizepicks", "market": "goblin"}), M, NOW) is None
    over_only = row(302, 32, book="prizepicks", side="Less", p=0.8)
    over_only.update(under_available=False, direction_basis="feed allowed_wager_types: over")              # live case
    assert "allowed_wager_types: over" in sb.why_excluded(over_only, sb.normalize_settings({"book": "prizepicks"}), M, NOW)
    with pytest.raises(ValueError):
        sb.build(rows, {"book": "underdog", "market": "goblin"})
    with pytest.raises(ValueError):
        sb.normalize_settings({"sizes": [5]})


def test_moneyline_never_mixed_with_props():
    rows = board(6, seed=10)
    ml = [row(600 + m, m, prop="moneyline", player=f"T{m}a", nick=None, player_id=None, p=0.7, line=None, graded_line=None)
          for m in range(6)]
    for r in ml:
        r["graded_line"] = r["line"] = 0.0
    res = sb.build(rows + ml, {"sizes": [2, 3]}, model=M, now=NOW)
    assert all(l["market_type"] == "standard" for s in all_slips(res) for l in s["legs"])
    assert any(e["reason"].startswith("moneyline") for e in res["excluded"])
    mres = sb.build(rows + ml, {"market": "moneyline", "sizes": [2, 3]}, model=M, now=NOW)
    assert all(l["market_type"] == "moneyline" for s in all_slips(mres) for l in s["legs"]) and all_slips(mres)
    # in one batch: a moneyline entry never reuses a match already carrying a prop
    bid = sb.current_batch("sess-ml")
    a = sb.build(rows + ml, {"sizes": [2], "count": 1}, batch=bid, key="k1", model=M, now=NOW)
    b = sb.build(rows + ml, {"market": "moneyline", "sizes": [2], "count": 3}, batch=bid, key="k2", model=M, now=NOW)
    used = {l["match_slug"] for s in all_slips(a) for l in s["legs"]}
    mlm = {l["match_slug"] for s in all_slips(b) for l in s["legs"]}
    assert len(used) == 2 and len(mlm) == 4 and not used & mlm
    assert "to win vs" in sb.copy_text(all_slips(b)[0]) and "series moneyline" in sb.copy_text(all_slips(b)[0])


# ---------------------------------------------------------------- batches, idempotency, concurrency
def test_exposure_persists_across_rebuilds_until_new_batch():
    rows = board(12, seed=11)
    bid = sb.current_batch("sess-a")
    assert sb.current_batch("sess-a") == bid                             # a refresh finds the same batch
    r1 = sb.build(rows, {"sizes": [4], "count": 1}, batch=bid, key="press-1", model=M, now=NOW)
    sb.build(rows, {"sizes": [2], "count": 2}, batch=bid, key="press-2", model=M, now=NOW)        # size change
    r3 = sb.build(rows, {"sizes": [3], "count": 3}, batch=bid, key="press-3", model=M, now=NOW)
    batch = sb.get_batch(bid)
    slips = [s for n in batch["slips"] for s in batch["slips"][n]]
    assert len(slips) == 1 + 2 + 1 and batch["builds"] == 3
    assert_no_repeats(slips)
    assert "start a new batch" in r3["shortage"][3]
    assert any(e["reason"] == "already in this batch (no-repeat rule)" for e in r3["excluded"])
    nb = sb.new_batch("sess-a", key="nb-1")
    assert nb != bid and sb.new_batch("sess-a", key="nb-1") == nb and sb.current_batch("sess-a") == nb
    r4 = sb.build(rows, {"sizes": [4], "count": 1}, batch=nb, key="press-1", model=M, now=NOW)
    assert [l["row_id"] for l in r4["slips"][4][0]["legs"]] == [l["row_id"] for l in r1["slips"][4][0]["legs"]]
    with pytest.raises(ValueError):
        sb.build(rows, {"sizes": [2]}, batch=bid, key="late", model=M, now=NOW)   # the old batch is closed
    assert sb.current_batch("sess-b") != nb                              # members never share a batch
    assert sb.owns("sess-a", nb) and not sb.owns("sess-b", nb)


def test_idempotent_retry_same_result_no_duplicate_rows():
    rows = board(10, seed=12)
    bid = sb.current_batch("sess-c")
    a = sb.build(rows, {"count": 2}, batch=bid, key="req-1", model=M, now=NOW)
    b = sb.build(rows, {"count": 2}, batch=bid, key="req-1", model=M, now=NOW + timedelta(seconds=5))
    assert a == b
    con = sqlite3.connect(str(sb.DB_PATH))
    assert con.execute("SELECT COUNT(*) FROM builds").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM slips").fetchone()[0] == len(all_slips(a))
    with pytest.raises(sqlite3.DatabaseError):                         # immutable recommendation records
        con.execute("UPDATE slips SET n=9")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM slips")
    con.close()
    # no key: the same board and settings = the same request
    c = sb.build(rows, {"sizes": [2]}, batch=bid, model=M, now=NOW)
    assert sb.build(rows, {"sizes": [2]}, batch=bid, model=M, now=NOW) == c
    # events are separate and idempotent
    sid = all_slips(a)[0]["slip_id"]
    assert sb.record_event(bid, sid, "copy", key="ev-1") is True
    assert sb.record_event(bid, sid, "copy", key="ev-1") is False
    assert [e["kind"] for e in sb.events(bid)] == ["copy"]
    assert sb.get_slip(sid)["legs"] == all_slips(a)[0]["legs"]
    with pytest.raises(ValueError):
        sb.record_event(bid, "nope", "copy")


def test_concurrent_builds_no_duplicates():
    rows = board(40, seed=13)
    bid = sb.current_batch("sess-d")
    errs, out = [], []

    def go(k):
        try:
            out.append(sb.build(rows, {"sizes": [2], "count": 2}, batch=bid, key=f"k{k % 10}", model=M, now=NOW))
        except Exception as e:                                         # pragma: no cover
            errs.append(e)
    th = [threading.Thread(target=go, args=(k,)) for k in range(30)]   # 10 distinct presses, each retried 3 times
    [t.start() for t in th]
    [t.join() for t in th]
    assert not errs
    batch = sb.get_batch(bid)
    slips = [s for n in batch["slips"] for s in batch["slips"][n]]
    assert batch["builds"] == 10 and len(slips) == 20
    assert_no_repeats(slips)
    by_key = {}
    for r in out:
        by_key.setdefault(r["build_id"], []).append(r)
    assert len(by_key) == 10 and all(all(x == v[0] for x in v) for v in by_key.values())


def test_concurrent_members_get_their_own_batches():
    rows = board(10, seed=14)
    ids, errs = [], []

    def go(k):
        try:
            b = sb.current_batch(f"member-{k}")
            ids.append(b)
            sb.build(rows, {"sizes": [4], "count": 1}, batch=b, key="x", model=M, now=NOW)
        except Exception as e:                                         # pragma: no cover
            errs.append(e)
    th = [threading.Thread(target=go, args=(k,)) for k in range(25)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert not errs and len(set(ids)) == 25
    con = sqlite3.connect(str(sb.DB_PATH))
    assert con.execute("SELECT COUNT(*) FROM builds").fetchone()[0] == 25
    con.close()


# ---------------------------------------------------------------- search bound and optimality
def _brute(pool, n):
    best = None
    for c in itertools.combinations(pool, n):
        if len({r["match_slug"] for r in c}) < n or len({r["player_id"] for r in c}) < n:
            continue
        s = sum(math.log(r["p"]) for r in c)
        best = s if best is None or s > best else best
    return best


def test_search_is_exact_for_one_slip_and_bound_is_reported(monkeypatch):
    for seed in range(15):
        rows = board(7, per_match=3, seed=100 + seed)
        res = sb.build(rows, {"sizes": [4], "count": 1}, model=M, now=NOW)
        assert res["search"]["bound_hit"] is False
        assert abs(res["slips"][4][0]["log_chance"] - _brute(rows, 4)) < 1e-6
    monkeypatch.setattr(sb, "NODE_LIMIT", 2)
    rows = board(7, per_match=3, seed=1)
    res = sb.build(rows, {"sizes": [4, 2], "count": 3}, model=M, now=NOW)
    assert res["search"]["node_limit"] == 2
    assert_no_repeats(all_slips(res))                                   # a bounded search still never breaks a rule


# ---------------------------------------------------------------- before copying
def _boards(rows, at):
    graded = {"book": "underdog", "updated": at, "error": None, "rows": rows, "version": M}
    feed = {"at": at, "error": None, "rows": [{k: r[k] for k in ("id", "player", "team", "opponent", "start", "prop", "scope",
                                                                  "line", "higher", "lower", "alt", "ud_player_id")} for r in rows]}
    return {"underdog": {"feed": feed, "graded": graded}}


def test_line_moved_before_copy_means_rebuild():
    rows = board(4, per_match=1, seed=15)
    res = sb.build(rows, {"sizes": [4], "count": 1}, model=M, now=NOW)
    slip = res["slips"][4][0]
    at = NOW.timestamp()
    ok = sb.verify_before_copy(slip, _boards(rows, at), now=NOW, model=M)
    assert ok["ok"] is True and ok["action"] == "copy" and all(l["line_check"] == "verified" for l in ok["legs"])
    moved = [dict(r) for r in rows]
    tgt = next(r for r in moved if r["id"] == slip["legs"][0]["row_id"])
    tgt["line"] = 31.5                                                   # the platform moved the line; no new grade yet
    v = sb.verify_before_copy(slip, _boards(moved, at), now=NOW, model=M)
    assert v["ok"] is False and v["action"] == "rebuild" and v["legs"][0]["status"] == "changed"
    assert any("31.5" in c for c in v["changes"])
    assert "p" not in v["legs"][0]                                       # no chance is ever carried to the new line
    regraded = [dict(r) for r in rows]
    tgt = next(r for r in regraded if r["id"] == slip["legs"][1]["row_id"])
    tgt.update(p=tgt["p"] - 0.05)                                        # same line, forecast updated
    assert sb.verify_before_copy(slip, _boards(regraded, at), now=NOW, model=M)["ok"] is False
    assert sb.verify_before_copy(slip, _boards(rows, at), now=NOW, model="NEWMODEL")["ok"] is False
    late = NOW + timedelta(hours=4)
    assert sb.verify_before_copy(slip, _boards(rows, late.timestamp()), now=late, model=M)["ok"] is False


def test_copy_text_full_identity():
    rows = board(2, per_match=1, seed=16)
    res = sb.build(rows, {"sizes": [2], "count": 1}, model=M, now=NOW)
    s = res["slips"][2][0]
    t = sb.copy_text(s)
    for l in s["legs"]:
        assert f"{l['nick']} ({l['team']}) vs {l['opponent']}" in t
        assert f"Over {l['line']}" in t
    assert "Underdog - 2-pick entry - standard lines" in t and "Maps 1-2" in t and "Kills" in t and "UTC" in t
    json.dumps(res)                                                     # the whole result is JSON-serialisable


def test_both_teams_of_one_match_are_one_match_without_a_slug():
    """PrizePicks rows carry no `matchup`; slips-8 keyed matches by team|opponent, so the two sides of one match
    (live 2026-10-12: xKacpersky NIP + Prism Betclic) could share a slip. Here the match is the same either way round."""
    a = row(1, 1, match_slug=None, team="NIP", team_name="NIP", opponent="Betclic", opp_name="Betclic", p=0.74)
    b = row(2, 1, match_slug=None, team="Betclic", team_name="Betclic", opponent="NIP", opp_name="NIP", p=0.73)
    rows = [a, b] + board(3, per_match=1, seed=17)[1:]
    res = sb.build(rows, {"sizes": [2, 3], "count": 1, "book": "underdog"}, model=M, now=NOW)
    for s in all_slips(res):
        assert not {"r1", "r2"} <= {l["row_id"] for l in s["legs"]}
    assert not ({"r1", "r2"} <= {l["row_id"] for s in all_slips(res) for l in s["legs"]})   # one match, once per batch


# ---------------------------------------------------------------- review fixes (2026-10-12)
def test_stale_or_failing_underdog_feed_never_passes_verify():
    rows = board(4, per_match=1, seed=21)
    slip = sb.build(rows, {"sizes": [4], "count": 1}, model=M, now=NOW)["slips"][4][0]
    fresh = _boards(rows, NOW.timestamp())
    assert sb.verify_before_copy(slip, fresh, now=NOW, model=M)["action"] == "copy"
    old = _boards(rows, NOW.timestamp() - 3600)                         # last good rows kept, an hour old ...
    old["underdog"]["feed"]["error"] = "HTTP 503"                       # ... and the latest refresh failed
    v = sb.verify_before_copy(slip, old, now=NOW, model=M)
    assert v["ok"] is False and v["action"] == "rebuild"
    assert all(l["line_check"] == "stale" for l in v["legs"]) and "not fresh" in v["changes"][0]
    only_old = _boards(rows, NOW.timestamp() - 3600)                    # stale without an error
    assert sb.verify_before_copy(slip, only_old, now=NOW, model=M)["action"] == "rebuild"
    only_err = _boards(rows, NOW.timestamp())                           # fresh rows but the refresh reports an error
    only_err["underdog"]["feed"]["error"] = "Timeout"
    assert sb.verify_before_copy(slip, only_err, now=NOW, model=M)["action"] == "rebuild"


def test_both_sides_rule_spans_map_scopes():
    """limited overlap + Maps 'All': a player's Maps 1-2 kills More in one slip blocks his Map 1 kills Less later."""
    bid = sb.current_batch("sess-sides")
    first = [row(0, 0, p=0.74, scope="12", side="More")] + [row(i, i, p=0.60) for i in range(1, 4)]
    r1 = sb.build(first, {"sizes": [2], "count": 1, "overlap": "limited", "scope": "all"}, batch=bid, key="s-1", model=M, now=NOW)
    assert any(l["row_id"] == "r0" for l in r1["slips"][2][0]["legs"])
    later = [row(50, 0, player="p0", nick="p0", player_id=1000, scope="1", side="Less", line=16.5, graded_line=16.5, p=0.9,
                 higher=1.0, lower=1.0)] + [row(i, 10 + i, p=0.58) for i in range(60, 64)]
    r2 = sb.build(later, {"sizes": [2], "count": 2, "overlap": "limited", "scope": "all"}, batch=bid, key="s-2", model=M, now=NOW)
    assert all(l["row_id"] != "r50" for s in all_slips(r2) for l in s["legs"])
    assert any(e["reason"] == "other side of this player's stat already in this batch" for e in r2["excluded"])
    # the same side on another scope is not "both sides" (only the overlap cap applies)
    same = [dict(later[0], side="More", id="r51")] + later[1:]
    r3 = sb.build(same, {"sizes": [2], "count": 1, "overlap": "limited", "scope": "all"}, batch=sb.new_batch("sess-sides2"),
                  key="s-3", model=M, now=NOW)
    assert all_slips(r3)


def test_batch_exposure_mode_comes_from_every_build_not_the_last():
    rows = board(8, seed=22)
    bid = sb.current_batch("sess-mixed")
    sb.build(rows, {"sizes": [2], "count": 3, "overlap": "limited", "max_uses": {"player": 3, "match": 3}}, batch=bid,
             key="m-1", model=M, now=NOW)
    sb.build(rows, {"sizes": [2], "count": 1}, batch=bid, key="m-2", model=M, now=NOW)            # overlap off
    e = sb.get_batch(bid)["exposure"]
    assert e["overlap"] == "limited" and e["caps"] == {"player": 3, "match": 3} and e["mixed"] is True
    assert e["no_repeats"] is (max(e["max_uses"].values()) <= 1)
    assert not any(r.startswith("no repeated prop") for r in e["rules"])      # never claimed beside real repeats
    assert "mixes overlap settings" in e["rules"][0]
    plain = sb.current_batch("sess-plain")
    sb.build(rows, {"sizes": [2], "count": 1}, batch=plain, key="p-1", model=M, now=NOW)
    e2 = sb.get_batch(plain)["exposure"]
    assert e2["overlap"] == "none" and e2["mixed"] is False and e2["no_repeats"] is True


def test_changed_slip_frees_its_legs_and_replace_builds_one():
    """Thin board (3 matches): a slip found changed before copying no longer blocks its unchanged legs, and a replace
    voids it and builds exactly one slip of its size - the batch keeps the same number of live slips."""
    rows = board(3, per_match=2, seed=23)
    bid = sb.current_batch("sess-replace")
    a = sb.build(rows, {"sizes": [2], "count": 1}, batch=bid, key="r-1", model=M, now=NOW)
    old = a["slips"][2][0]
    b = sb.build(rows, {"sizes": [2], "count": 1}, batch=bid, key="r-2", model=M, now=NOW)
    assert b["shortage"].get(2)                                      # only one unused match left: no 2-pick
    sb.record_event(bid, old["slip_id"], "verify_changed", key="v-1")
    sb.record_event(bid, old["slip_id"], "verify_ok", key="v-2")    # a later ok never brings it back
    c = sb.build(rows, {"sizes": [2], "count": 1}, batch=bid, key="r-3", model=M, now=NOW)
    assert len(c["slips"][2]) == 1 and not c["shortage"]           # its matches are eligible again
    reused = {l["match_slug"] for l in c["slips"][2][0]["legs"]} & {l["match_slug"] for l in old["legs"]}
    assert reused
    # replace: void + one slip of the old size, whatever sizes / counts were asked
    bid2 = sb.current_batch("sess-replace-2")
    rows2 = board(6, per_match=2, seed=24)
    x = sb.build(rows2, {"sizes": [3, 2], "count": 1}, batch=bid2, key="x-1", model=M, now=NOW)
    live_before = len(all_slips(x))
    gone = x["slips"][3][0]
    rep = sb.build(rows2, {"sizes": [4, 3, 2], "count": 3}, batch=bid2, key="x-2", model=M, now=NOW, replace=gone["slip_id"])
    assert rep["replaced"] == gone["slip_id"] and [len(rep["slips"][n]) for n in (4, 3, 2)] == [0, 1, 0]
    assert {l["match_slug"] for l in rep["slips"][3][0]["legs"]} & {l["match_slug"] for l in gone["legs"]}   # legs reusable
    g = sb.get_batch(bid2)
    assert g["live_slips"] == live_before and g["superseded"] == 1 and g["exposure"]["slips"] == live_before
    st = {s["slip_id"]: s["status"] for n in g["slips"] for s in g["slips"][n]}
    assert st[gone["slip_id"]] == "replaced" and list(st.values()).count("live") == live_before
    assert sb.build(rows2, {"sizes": [2]}, batch=bid2, key="x-2", model=M, now=NOW, replace=gone["slip_id"]) == rep  # retry
    with pytest.raises(ValueError):
        sb.build(rows2, {"sizes": [2]}, batch=bid2, key="x-3", model=M, now=NOW, replace=gone["slip_id"])
    assert "void" not in sb.EVENT_KINDS                            # members can't void slips themselves
    # normal builds are unchanged by all this
    fresh = sb.current_batch("sess-replace-3")
    y = sb.build(rows2, {"sizes": [3, 2], "count": 1}, batch=fresh, key="x-1", model=M, now=NOW)
    legs = lambda r: {n: [[(l["row_id"], l["side"], l["p"]) for l in s["legs"]] for s in r["slips"][n]] for n in (2, 3, 4)}
    assert legs(y) == legs(x)


def test_reads_do_not_wait_for_a_write_transaction():
    import time as _t
    bid = sb.current_batch("sess-lock")
    sb.build(board(4, seed=25), {"sizes": [2], "count": 1}, batch=bid, key="l-1", model=M, now=NOW)
    held, release = threading.Event(), threading.Event()

    def writer():
        with sb._db(write=True) as con:                             # a long write transaction (e.g. a slow disk)
            con.execute("SELECT 1")
            held.set()
            release.wait(5)
    th = threading.Thread(target=writer)
    th.start()
    held.wait(5)
    t0 = _t.time()
    try:
        assert sb.get_batch(bid)["builds"] == 1 and sb.owns("sess-lock", bid) and sb.current_batch("sess-lock") == bid
        assert _t.time() - t0 < 1.0
    finally:
        release.set()
        th.join(5)


def test_match_diversity_holds_across_build_presses_and_limited_reports_exposure():
    """Owner review 2026-10-12: in no-overlap mode a DIFFERENT player from an already-used match never comes back, even
    on a later Build press of the same batch; with limited overlap the batch reports player AND match exposure; an
    explicit new batch resets usage."""
    rows = board(12, per_match=3, seed=8)                    # 3 different players per match
    b = sb.new_batch("owner-check")
    r1 = sb.build(rows, {"sizes": [4], "count": 2}, batch=b, key="press-0001", model=M, now=NOW)
    r2 = sb.build(rows, {"sizes": [2], "count": 3}, batch=b, key="press-0002", model=M, now=NOW)
    used = [l["match_slug"] for r in (r1, r2) for s in all_slips(r) for l in s["legs"]]
    assert len(used) == len(set(used)) == 12 and [len(r1["slips"][4]), len(r2["slips"][2])] == [2, 2]
    assert "not relaxed" in r2["shortage"][2]                # 3 asked, 2 possible: said, never filled by reuse
    lim = sb.new_batch("owner-check-2")
    r3 = sb.build(rows, {"sizes": [3], "count": 6, "overlap": "limited", "max_uses": {"player": 1, "match": 2}},
                  batch=lim, key="press-0003", model=M, now=NOW)
    e = r3["exposure"]
    per_match = {}
    for s in all_slips(r3):
        assert len({l["match_slug"] for l in s["legs"]}) == len(s["legs"])     # one pick per match inside a slip
        for l in s["legs"]:
            per_match[l["match_slug"]] = per_match.get(l["match_slug"], 0) + 1
    assert max(per_match.values()) == 2 and e["max_uses"]["match"] == 2 and e["max_uses"]["player"] == 1
    assert e["repeated_matches"] and e["distinct_players"] == e["legs"]
    fresh = sb.new_batch("owner-check", key="reset-0001")
    assert fresh != b and len(all_slips(sb.build(rows, {"sizes": [4], "count": 1}, batch=fresh, key="press-0004", model=M, now=NOW))) == 1
