"""SCOUT_LINE_MOVE_NOTES (owner 2026-11-07): a settled prop whose line was bumped between opening and close says so - "Opened 15.5
(Over won) · closed 16 (push)" - on the card's past games, My picks receipts and the classic Slip studio history. Wording only:
every result, count, record, grade and settlement stays exactly as before. Fixture data and temporary databases only."""
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

import closing_lines as cl
import server
import slip_batches
import workspace_api
import workspace_linehist as lh
from tests.test_closing_lines import FORBIDDEN, HEDGE
from tests.test_workspace_api import env, get, records, track, settle  # noqa: F401  (env = the workspace fixture)
from tests.workspace_fixture import leg_of, row

T0 = datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc).timestamp()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(cl.FLAG, "1")
    monkeypatch.setenv(cl.MOVE_FLAG, "1")
    cl.reset()
    yield tmp_path
    cl.reset()


def put(ident, line, open_line, lock=T0, seen=None, event_id="hltv:900", player="vargo", prop="kills", scope="12", book="underdog",
        line_type="standard", match_slug=None, settle_slug=None):
    """One saved closing row (the table is append-only: tests write it the way closing_lines._write does)."""
    con = cl._connect()
    con.execute("INSERT INTO closing_lines (ident, version, sport, book, event_id, match_slug, settle_slug, player_key, prop, scope, line_type, "
                "line, open_line, lock_utc, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, "cl-1", "cs2", book, event_id, match_slug, settle_slug, player, prop, scope, line_type, line, open_line, iso(lock),
                 iso(seen if seen is not None else lock - 60)))
    con.commit()
    con.close()


# ---------------------------------------------------------------------------------------------------- the wording
def test_b1eany_open_15_5_close_16_actual_16_is_a_flip_with_the_owner_text():
    m = cl.move_note(15.5, 16, 16, "prizepicks")
    assert m["kind"] == "flip" and m["dir"] == "up" and m["at_open"] == "Over won" and m["at_close"] == "push"
    assert m["text"] == "Opened 15.5 (Over won) · closed 16 (push)"
    assert m["chip"] == "▲ bumped"
    assert m["tip"] == "Bumped up: PrizePicks opened this line at 15.5; it closed at 16."


@pytest.mark.parametrize("o,c,a,kind,text", [
    (15.5, 16.5, 16, "flip", "Opened 15.5 (Over won) · closed 16.5 (Under won)"),
    (17.5, 16.5, 17, "flip", "Opened 17.5 (Under won) · closed 16.5 (Over won)"),
    (16, 16.5, 16, "flip", "Opened 16 (push) · closed 16.5 (Under won)"),
    (16.0, 15.5, 16.0, "flip", "Opened 16 (push) · closed 15.5 (Over won)"),
])
def test_every_flip_reads_the_result_at_each_line(o, c, a, kind, text):
    m = cl.move_note(o, c, a, "underdog")
    assert m["kind"] == kind and m["text"] == text
    assert m["dir"] == ("up" if c > o else "down") and m["chip"] == ("▲ bumped" if c > o else "▼ bumped")
    assert m["tip"].startswith(f"Bumped {m['dir']}: Underdog opened this line at ")


def test_the_same_result_at_both_lines_is_the_board_chip_only():
    m = cl.move_note(15.5, 16.5, 20, "prizepicks")
    assert m["kind"] == "bump" and m["chip"] == m["text"] == "▲ bumped from 15.5"
    assert m["tip"] == "Bumped up: PrizePicks opened this line at 15.5; it closed at 16.5. Over won at both lines."
    d = cl.move_note(17.5, 16.5, 12, None)
    assert d["chip"] == "▼ bumped from 17.5" and d["tip"] == "Bumped down: This line opened at 17.5; it closed at 16.5. Under won at both lines."


@pytest.mark.parametrize("o,c,a,stale", [(None, 16, 16, False), (16, 16, 16, False), (15.5, 16, None, False), (15.5, None, 16, False),
                                         (15.5, 16, 16, True), (16.0, 16, 17, False)])
def test_nothing_to_say_is_none(o, c, a, stale):
    assert cl.move_note(o, c, a, "prizepicks", stale=stale) is None


def test_whole_numbers_drop_the_trailing_zero():
    assert cl.fmt_line(16.0) == "16" and cl.fmt_line(15.5) == "15.5" and cl.fmt_line(40) == "40" and cl.fmt_line(0.25) == "0.25"
    assert "16.0" not in cl.move_note(15.5, 16.0, 16.0, "prizepicks")["text"]


def test_no_hedge_or_forbidden_word_in_any_note():
    cases = [(15.5, 16, 16), (15.5, 16.5, 16), (17.5, 16.5, 17), (16, 16.5, 16), (15.5, 16.5, 20), (17.5, 16.5, 12)]
    for book in ("prizepicks", "underdog", None):
        for o, c, a in cases:
            m = cl.move_note(o, c, a, book)
            words = " ".join(str(m[k]) for k in ("text", "chip", "tip")).lower()
            assert not [w for w in HEDGE + FORBIDDEN + ("first seen",) if w in words], words


def test_band_rule_keeps_a_note_off_a_line_beside_the_move():
    r = {"open_line": 15.5, "line": 16.5}
    assert cl.in_band(15.5, r) and cl.in_band(16, r) and cl.in_band(16.5, r)
    assert not cl.in_band(14.5, r) and not cl.in_band(17.5, r)
    assert cl.row_note(r, 17.5, 16, "prizepicks") is None and cl.row_note(r, 15.5, 16, "prizepicks")["kind"] == "flip"
    assert cl.row_note(None, 15.5, 16, "prizepicks") is None and cl.row_note({"open_line": None, "line": 16}, 16, 16, "x") is None


# ---------------------------------------------------------------------------------------------------- card: L10 vs closing + marks
SER = [{"date": "2026-11-07", "opponent": "Alpha", "value": 16}, {"date": "2026-11-05", "opponent": "Bravo", "value": 20},
       {"date": "2026-11-03", "opponent": "Charlie", "value": 12}, {"date": "2026-11-01", "opponent": "Delta", "value": 15}]
MIDS = ["hltv-1", "hltv-2", "hltv-3", "hltv-4"]
ROWS = [{"event_id": "hltv:1", "line": 16.5, "open_line": 15.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 60)},      # flip
        {"event_id": "hltv:2", "line": 16.5, "open_line": 15.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 60)},      # same result
        {"event_id": "hltv:3", "line": 14.5, "open_line": 14.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 60)},      # not moved
        {"event_id": "hltv:4", "line": 15.5, "open_line": 14.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 7200)}]    # stale: left off


def test_rows_for_returns_the_opening_line(data):
    put("a", 16.5, 15.5)
    assert cl.rows_for("vargo", "kills", "12", "underdog", "standard") == [
        {"event_id": "hltv:900", "line": 16.5, "open_line": 15.5, "lock_utc": iso(T0), "last_seen_at": iso(T0 - 60)}]


def test_l10_games_carry_a_move_only_under_the_rule_and_the_counts_never_change():
    on = cl.l10_vs_closing(SER, MIDS, ROWS, notes=True, book="prizepicks")
    off = cl.l10_vs_closing(SER, MIDS, ROWS, notes=False, book="prizepicks")
    bare = cl.l10_vs_closing(SER, MIDS, [{k: v for k, v in r.items() if k != "open_line"} for r in ROWS], notes=True)
    strip = lambda d: json.dumps(dict(d, games=[{k: v for k, v in g.items() if k != "move"} for g in d["games"]]), sort_keys=True)
    assert strip(on) == strip(off) == strip(bare) == json.dumps(off, sort_keys=True)          # over / under / push / n / at identical
    assert (on["over"], on["under"], on["push"], on["n"]) == (1, 2, 0, 3) and [g["at"] for g in on["games"]] == ["Under", "Over", "Under"]
    g = on["games"]
    assert g[0]["move"]["text"] == "Opened 15.5 (Over won) · closed 16.5 (Under won)" and g[0]["at"] == "Under"
    assert g[1]["move"]["kind"] == "bump" and g[1]["move"]["chip"] == "▲ bumped from 15.5"
    assert "move" not in g[2] and all("move" not in x for x in off["games"] + bare["games"])


def test_l10_notes_follow_both_flags(monkeypatch, data):
    assert cl.move_notes_on()
    assert "move" in cl.l10_vs_closing(SER, MIDS, ROWS)["games"][0]
    monkeypatch.setenv(cl.MOVE_FLAG, "0")
    assert not cl.move_notes_on() and "move" not in cl.l10_vs_closing(SER, MIDS, ROWS)["games"][0]
    monkeypatch.setenv(cl.MOVE_FLAG, "1")
    monkeypatch.delenv(cl.FLAG)
    assert not cl.move_notes_on()


def test_closing_marks_carry_opened_and_move_only_with_a_note():
    note = lambda o, c, a: cl.move_note(o, c, a, "prizepicks")
    on = lh.closing_marks(SER, MIDS, ROWS, stale=cl.is_stale, note=note)
    off = lh.closing_marks(SER, MIDS, ROWS, stale=cl.is_stale)
    assert [{k: v for k, v in m.items() if k not in ("opened", "move")} for m in on["marks"]] == off["marks"]
    assert (on["avg_l10"], on["n_l10"]) == (off["avg_l10"], off["n_l10"]) == (15.83, 3)
    assert on["marks"][0]["opened"] == 15.5 and on["marks"][0]["move"]["at_close"] == "Under won"
    assert on["marks"][1]["move"]["kind"] == "bump" and "move" not in on["marks"][2] and "opened" not in on["marks"][2]
    assert all("move" not in m and "opened" not in m for m in off["marks"])


# ---------------------------------------------------------------------------------------------------- receipt identity
def _receipt_fields(x):
    return {"platform": workspace_api._book(x), "nick": workspace_api._nick(x), "prop": x.get("prop"), "scope": x.get("scope") or "12",
            "line_type": workspace_api.line_type_of(x)}


@pytest.mark.parametrize("x", [
    row("pp:1", "prizepicks", "B1eany", "A", "B", 15.5, "More", 0.6, 2400317, "2026-11-08T12:00:00+00:00", prop="hs"),
    row("pp:2", "prizepicks", "b1eany", "A", "B", 14.5, "More", 0.6, 2400317, "2026-11-08T12:00:00+00:00", prop="hs", odds_type="goblin"),
    row("ud:1", "underdog", "B1-eany", "A", "B", 15.5, "Less", 0.6, 2400317, "2026-11-08T12:00:00+00:00", prop="hs", scope="1"),
    dict(row("pp:3", "prizepicks", "vargo", "A", "B", 38.5, "More", 0.6, 900, "2026-11-08T12:00:00+00:00"), match_id=None, settle_slug="hltv-2400304", match_slug="x"),
    dict(row("ud:2", "underdog", "vargo", "A", "B", 38.5, "More", 0.6, 900, "2026-11-08T12:00:00+00:00"), match_id=None, settle_slug=None, match_slug="plain-slug"),
])
def test_the_ident_built_from_receipt_fields_matches_the_closing_row(x):
    assert workspace_api._receipt_ident(_receipt_fields(x), workspace_api.event_id(x)[0]) == cl.ident(x) is not None


def test_a_row_hash_match_key_has_no_closing_row():
    x = dict(row("ud:3", "underdog", "vargo", "A", "B", 38.5, "More", 0.6, 900, "2026-11-08T12:00:00+00:00"), match_id=None, settle_slug=None, match_slug=None)
    mk = workspace_api.event_id(x)[0]
    assert mk.startswith("row-") and workspace_api._receipt_ident(_receipt_fields(x), mk) is None and cl.ident(x) is None


# ---------------------------------------------------------------------------------------------------- lookup cache
def test_lookup_hits_are_kept_misses_expire_and_ins_are_chunked(data, monkeypatch):
    put("a", 16.5, 15.5)
    put("b", 20.5, 19.5)
    assert cl.lookup_idents([], now=T0) == {}
    got = cl.lookup_idents(["a", "missing"], now=T0)
    assert set(got) == {"a"} and got["a"]["open_line"] == 15.5
    opens = []
    real = cl._reader
    monkeypatch.setattr(cl, "_reader", lambda: opens.append(1) or real())
    assert set(cl.lookup_idents(["a", "missing"], now=T0 + 10)) == {"a"} and opens == []      # both answered from the cache
    put("missing", 11.5, 10.5)
    assert cl.lookup_idents(["missing"], now=T0 + 200) == {} and opens == []                     # a miss holds MISS_TTL
    assert set(cl.lookup_idents(["missing"], now=T0 + cl.MISS_TTL + 11)) == {"missing"} and len(opens) == 1
    monkeypatch.setattr(cl, "IN_CHUNK", 2)
    cl.reset()
    q = []

    class Spy:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, args=()):
            q.append(len(args))
            return self.con.execute(sql, args)

        def close(self):
            self.con.close()
    monkeypatch.setattr(cl, "_reader", lambda: Spy(real()))
    got = cl.lookup_idents(["a", "b", "missing", "x1", "x2"], now=T0)
    assert set(got) == {"a", "b", "missing"} and q == [2, 2, 1]


def test_lookup_without_a_database_is_empty_and_never_creates_one(data):
    assert cl.lookup_idents(["a"], now=T0) == {} and not (data / "closing_lines.db").exists()
    assert cl.find_for_leg("vargo", "kills", "12", "underdog", "standard", "slug-900", iso(T0)) is None


def test_the_lookup_cache_is_bounded(data, monkeypatch):
    monkeypatch.setattr(cl, "LOOKUP_MAX", 3)
    put("a", 16.5, 15.5)
    cl.lookup_idents(["a", "m1", "m2", "m3", "m4"], now=T0)
    assert len(cl._LOOKUP) == 3


def test_find_for_leg_needs_the_exact_slug_inside_the_window(data):
    put("a", 16.5, 15.5, match_slug="hltv-2400304", settle_slug="hltv-2400304", event_id="hltv:2400317")
    assert cl.find_for_leg("vargo", "kills", "12", "underdog", "standard", "hltv-2400304", iso(T0 - 3600))["line"] == 16.5
    assert cl.find_for_leg("vargo", "kills", "12", "underdog", "standard", "hltv-2400317", iso(T0 - 3600)) is None     # no fuzzy slug
    assert cl.find_for_leg("vargo", "kills", "12", "underdog", "standard", "hltv-2400304", iso(T0 - 13 * 3600)) is None  # outside 12 h
    assert cl.find_for_leg("vargo", "kills", "12", "prizepicks", "standard", "hltv-2400304", iso(T0)) is None


# ---------------------------------------------------------------------------------------------------- My picks receipts (CS2)
def _on(monkeypatch, on=True):
    monkeypatch.setenv(cl.FLAG, "1")
    if on:
        monkeypatch.setenv(cl.MOVE_FLAG, "1")
    else:
        monkeypatch.delenv(cl.MOVE_FLAG, raising=False)
    workspace_api._PRIVATE.clear()
    cl.reset()


def mine(env):
    workspace_api._PRIVATE.clear()
    st, doc = get(env, "mine")
    assert st == 200
    return doc


def tracked_settled(env, vargo_final=39.0, orlo_final=None):
    r = records(env)
    assert track(env, [leg_of(r[("vargo", "underdog")]), leg_of(r[("orlo", "underdog")])])[0] == 200
    env.settle(env.by("vargo"), actual=vargo_final)
    if orlo_final is not None:
        env.settle(env.by("orlo"), actual=orlo_final)
    settle(env)


def test_a_settled_receipt_keeps_its_result_and_gains_the_move(env, monkeypatch):
    _on(monkeypatch, False)
    v = env.by("vargo")
    put(cl.ident(v), 39.5, 38.5, event_id="hltv:900")          # Over 38.5 tracked; the line closed at 39.5; final 39
    tracked_settled(env)
    off = mine(env)
    _on(monkeypatch, True)
    on = mine(env)
    rv = next(x for x in on["receipts"] if x["player"] == "vargo")
    ro = next(x for x in on["receipts"] if x["player"] == "orlo")
    assert rv["status"] == "hit" and rv["final"] == 39 and rv["line"] == 38.5
    assert rv["line_move"]["kind"] == "flip" and rv["line_move"]["text"] == "Opened 38.5 (Over won) · closed 39.5 (Under won)"
    assert rv["line_move"]["tip"] == "Bumped up: Underdog opened this line at 38.5; it closed at 39.5."
    assert ro["status"] == "pending" and "line_move" not in ro                                  # not settled: nothing
    for k in ("record", "by_sport", "by_platform", "by_version", "slips"):
        assert on[k] == off[k], k                                                                # every count identical
    strip = lambda d: [{k: v for k, v in x.items() if k != "line_move"} for x in d["receipts"]]
    assert strip(on) == strip(off)
    assert all("_match_key" not in x and "_settle_key" not in x for x in on["receipts"] + off["receipts"])
    assert all("line_move" not in x for x in off["receipts"])                                    # flag off: no field


def test_the_b1eany_case_on_a_receipt_reads_push_at_the_close(env, monkeypatch):
    _on(monkeypatch)
    put(cl.ident(env.by("vargo")), 39, 38.5)
    tracked_settled(env, vargo_final=39.0)
    rv = next(x for x in mine(env)["receipts"] if x["player"] == "vargo")
    assert rv["status"] == "hit" and rv["line_move"]["text"] == "Opened 38.5 (Over won) · closed 39 (push)"


def test_a_line_off_the_move_and_a_stale_row_get_no_note(env, monkeypatch):
    _on(monkeypatch)
    put(cl.ident(env.by("vargo")), 37.5, 36.5)                       # tracked at 38.5: outside 36.5 .. 37.5
    put(cl.ident(env.by("orlo")), 34.5, 33.5, seen=T0 - 7200)        # stale closing row
    tracked_settled(env, vargo_final=37.0, orlo_final=34.0)
    doc = mine(env)
    assert all("line_move" not in x for x in doc["receipts"]) and {x["status"] for x in doc["receipts"]} == {"miss", "hit"}


def test_a_same_result_receipt_gets_the_chip(env, monkeypatch):
    _on(monkeypatch)
    put(cl.ident(env.by("vargo")), 38.5, 37.5)
    tracked_settled(env, vargo_final=44.0)
    m = next(x for x in mine(env)["receipts"] if x["player"] == "vargo")["line_move"]
    assert m["kind"] == "bump" and m["chip"] == "▲ bumped from 37.5" and m["tip"].endswith("Over won at both lines.")


def test_a_broken_closing_database_never_breaks_my_picks(env, monkeypatch):
    _on(monkeypatch)
    put(cl.ident(env.by("vargo")), 39.5, 38.5)
    tracked_settled(env)
    monkeypatch.setattr(cl, "lookup_idents", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    doc = mine(env)
    assert next(x for x in doc["receipts"] if x["player"] == "vargo")["status"] == "hit"
    assert all("line_move" not in x and "_match_key" not in x for x in doc["receipts"])


def test_invariance_results_payload_is_unchanged(env, monkeypatch):
    _on(monkeypatch, False)
    put(cl.ident(env.by("vargo")), 39.5, 38.5)
    tracked_settled(env, orlo_final=30.0)
    server._RESULTS.clear()
    off = get(env, "results")[1]
    _on(monkeypatch, True)
    server._RESULTS.clear()
    on = get(env, "results")[1]
    drop = lambda d: {k: v for k, v in d.items() if k not in ("at", "as_of", "generated", "updated")} if isinstance(d, dict) else d
    assert json.dumps(drop(on), sort_keys=True, default=str) == json.dumps(drop(off), sort_keys=True, default=str)


# ---------------------------------------------------------------------------------------------------- classic Slip studio history
def _batch(leg_over, slip_extra=None):
    s = {"slip_id": "b1/s1", "n": 2, "book": "prizepicks", "entry_type": "standard", "legs": leg_over}
    s.update(slip_extra or {})
    return [{"batch_id": "b1", "created_at": iso(T0 - 7200), "slips": {"2": [s]}}]


def _leg(nick, line, side="More", slug="hltv-2400304", match_id="hltv:2400317", prop="hs", scope="12"):
    return {"nick": nick, "player": nick, "prop": prop, "scope": scope, "line": line, "side": side, "book": "prizepicks",
            "match_slug": slug, "match_id": match_id, "start": iso(T0 - 1800), "team": "A", "opponent": "B"}


def _picks(monkeypatch, rows):
    import picklog
    monkeypatch.setattr(picklog, "rows_for_matches", lambda slugs, cap=200: [r for r in rows if r["match_slug"] in slugs])


def _pick(nick, actual, slug="hltv-2400304", prop="hs"):
    return {"player": nick, "prop": prop, "scope": "12", "match_slug": slug, "status": "settled", "actual": actual, "result": '{"source": "hltv"}'}


def test_studio_legs_keep_their_results_and_gain_parallel_moves(data, monkeypatch):
    b1 = {"book": "prizepicks", "match_id": "hltv:2400317", "nick": "b1elany", "prop": "hs", "scope": "12", "odds_type": "standard"}
    put(cl.ident(b1), 16.5, 15.5, event_id="hltv:2400317", player="b1elany", prop="hs", book="prizepicks", match_slug="hltv-2400304")
    legs = [_leg("b1elany", 15.5), _leg("mika7", 30.5, prop="kills")]
    _picks(monkeypatch, [_pick("b1elany", 16), _pick("mika7", 33, prop="kills")])
    on = server.studio_history_payload(_batch(legs), now=datetime.fromtimestamp(T0, timezone.utc))
    monkeypatch.setenv(cl.MOVE_FLAG, "0")
    off = server.studio_history_payload(_batch(legs), now=datetime.fromtimestamp(T0, timezone.utc))
    s_on, s_off = on["batches"][0]["slips"][0], off["batches"][0]["slips"][0]
    assert s_on["leg_results"] == s_off["leg_results"] == ["hit", "hit"] and s_on["outcome"] == s_off["outcome"] == "won"
    assert "leg_moves" not in s_off and len(s_on["leg_moves"]) == 2
    assert s_on["leg_moves"][0]["text"] == "Opened 15.5 (Over won) · closed 16.5 (Under won)" and s_on["leg_moves"][1] is None
    assert {k: v for k, v in s_on.items() if k != "leg_moves"} == s_off


def test_a_studio_leg_without_its_canonical_id_needs_the_exact_slug(data, monkeypatch):
    put("pp-b1", 16.5, 15.5, event_id="hltv:2400317", player="b1elany", prop="hs", book="prizepicks", match_slug="hltv-2400304",
        lock=T0 - 1800)
    _picks(monkeypatch, [_pick("b1elany", 16), _pick("b1elany", 16, slug="hltv-999")])
    now = datetime.fromtimestamp(T0, timezone.utc)
    same = server.studio_history_payload(_batch([_leg("b1elany", 15.5, match_id=None)]), now=now)["batches"][0]["slips"][0]
    other = server.studio_history_payload(_batch([_leg("b1elany", 15.5, match_id=None, slug="hltv-999")]), now=now)["batches"][0]["slips"][0]
    assert same["leg_moves"][0]["kind"] == "flip"
    assert other["leg_results"] == ["hit"] and other["leg_moves"] == [None]


def test_unsettled_or_pending_studio_legs_get_no_move(data, monkeypatch):
    b1 = {"book": "prizepicks", "match_id": "hltv:2400317", "nick": "b1elany", "prop": "hs", "scope": "12"}
    put(cl.ident(b1), 16.5, 15.5, event_id="hltv:2400317", player="b1elany", prop="hs", book="prizepicks")
    _picks(monkeypatch, [dict(_pick("b1elany", None), status="open")])
    s = server.studio_history_payload(_batch([_leg("b1elany", 15.5)]), now=datetime.fromtimestamp(T0, timezone.utc))["batches"][0]["slips"][0]
    assert s["leg_results"] == [None] and s["leg_moves"] == [None]


def test_leg_actual_matches_the_leg_result_rule():
    rows = [_pick("x", 16, prop="hs"), dict(_pick("x", 17, prop="hs"), result='{"source": "bo3"}'), dict(_pick("y", 9), result='{"review": true}')]
    act, res = slip_batches.leg_actual_from_picks(rows), slip_batches.leg_result_from_picks(rows)
    lx = _leg("x", 15.5)
    assert act(lx) == 16 and res(lx) == "hit"                         # HLTV before BO3.gg, as leg_result_from_picks decides
    assert act(dict(lx, match_slug=None)) is None and act(_leg("nobody", 1)) is None


def test_slip_records_masking_keeps_leg_moves():
    import letter_containment
    obj = {"batches": [{"slips": [{"legs": [{"grade": "A", "units": 1.0, "prop": "kills"}], "leg_moves": [{"kind": "flip"}]}]}]}
    out = letter_containment.cs2_slip_records(obj, {"contained": True, "stats": []})
    assert out["batches"][0]["slips"][0]["leg_moves"] == [{"kind": "flip"}]
    assert out["batches"][0]["slips"][0]["legs"][0]["grade"] == "LEAN"                   # the mask ran, the note passed through


def test_the_flag_is_on_in_the_running_server_and_off_on_import():
    assert cl.MOVE_FLAG in server.DEFAULT_ON and "SCOUT_CLOSING_LINES" in server.DEFAULT_ON
    env_ = {}
    server.apply_default_flags(env_)
    assert env_[cl.MOVE_FLAG] == "1"


# ---------------------------------------------------------------------------------------------------- review 1107: caps and misses
def _many(n_slips, legs_per_slip, leg):
    """n_slips slips, each holding the same leg plus filler legs (different players, no closing row)."""
    slips = []
    for j in range(n_slips):
        legs = [dict(leg)] + [_leg(f"filler{j}_{k}", 10.5, prop="kills") for k in range(legs_per_slip - 1)]
        slips.append({"slip_id": f"b1/s{j}", "n": legs_per_slip, "book": "prizepicks", "entry_type": "standard", "legs": legs})
    return [{"batch_id": "b1", "created_at": iso(T0 - 7200), "slips": {str(legs_per_slip): slips}}]


def test_a_leg_repeated_across_many_slips_gets_its_note_on_every_slip(data, monkeypatch):
    """Reviewer 2 (#1): the caps count DIFFERENT lookups, not every copy of a leg - 100 slips x 4 legs, all 100 get the B1eany note."""
    b1 = {"book": "prizepicks", "match_id": "hltv:2400317", "nick": "b1elany", "prop": "hs", "scope": "12", "odds_type": "standard"}
    put(cl.ident(b1), 16, 15.5, event_id="hltv:2400317", player="b1elany", prop="hs", book="prizepicks", match_slug="hltv-2400304")
    fill = [_pick(f"filler{j}_{k}", 12, prop="kills") for j in range(100) for k in range(3)]
    _picks(monkeypatch, [_pick("b1elany", 16)] + fill)
    out = server.studio_history_payload(_many(100, 4, _leg("b1elany", 15.5)), now=datetime.fromtimestamp(T0, timezone.utc))
    slips = out["batches"][0]["slips"]
    assert len(slips) == 100
    assert all(s["leg_moves"][0] and s["leg_moves"][0]["text"] == "Opened 15.5 (Over won) · closed 16 (push)" for s in slips)
    assert all(s["leg_results"][0] == "hit" for s in slips)           # Over 15.5 finished 16: settled at the member's own line


def test_studio_fallbacks_share_one_connection_and_count_only_reads(data, monkeypatch):
    """Reviewer 2 (#2): every fallback of one request shares one read-only connection, and a cached answer is not counted."""
    for k in range(3):
        put(f"pp-x{k}", 16, 15.5, event_id="hltv:2400317", player=f"p{k}", prop="hs", book="prizepicks", match_slug="hltv-2400304",
            lock=T0 - 1800)
    legs = [_leg(f"p{k}", 15.5, match_id=None) for k in range(3)]
    _picks(monkeypatch, [_pick(f"p{k}", 16) for k in range(3)])
    opens = []
    real = cl._reader
    monkeypatch.setattr(cl, "_reader", lambda: opens.append(1) or real())
    now = datetime.fromtimestamp(T0, timezone.utc)
    s = server.studio_history_payload(_batch(legs), now=now)["batches"][0]["slips"][0]
    assert [m and m["kind"] for m in s["leg_moves"]] == ["flip", "flip", "flip"] and len(opens) == 1
    monkeypatch.setattr(server, "STUDIO_MOVE_FALLBACKS", 0)            # every answer cached: no read, so no cap applies
    s = server.studio_history_payload(_batch(legs), now=now)["batches"][0]["slips"][0]
    assert [m and m["kind"] for m in s["leg_moves"]] == ["flip", "flip", "flip"] and len(opens) == 1


def test_a_miss_long_after_the_start_is_kept_a_recent_one_is_asked_again(data, monkeypatch):
    opens = []
    real = cl._reader
    put("other", 16, 15.5)
    monkeypatch.setattr(cl, "_reader", lambda: opens.append(1) or real())
    old, recent = iso(T0 - cl.FINAL_AFTER_S - 60), iso(T0 - 600)
    cl.lookup_idents(["gone", "soon"], now=T0, starts={"gone": old, "soon": recent})
    assert len(opens) == 1
    cl.lookup_idents(["gone"], now=T0 + 10 * cl.MISS_TTL, starts={"gone": old})
    assert len(opens) == 1                                             # final: never asked again
    cl.lookup_idents(["soon"], now=T0 + cl.MISS_TTL + 1, starts={"soon": recent})
    assert len(opens) == 2                                             # recent: asked again after MISS_TTL
    late = T0 - cl.LEG_WINDOW_S - cl.FINAL_AFTER_S - 60
    assert cl.find_for_leg("nobody", "hs", "12", "prizepicks", "standard", "hltv-1", iso(late), now=T0) is None and len(opens) == 3
    assert cl.find_for_leg("nobody", "hs", "12", "prizepicks", "standard", "hltv-1", iso(late), now=T0 + 10 * cl.MISS_TTL) is None
    assert len(opens) == 3
