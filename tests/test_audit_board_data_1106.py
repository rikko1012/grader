"""Owner site audit 2026-11-06, new board (data side): N2 a failed / partial refresh never empties the board, N3 hit rates are
filled in the background with the board, N7 sports line moves, N8 one price everywhere and every book listed, N9 the Ask tab's
other books and the letter's settled record. N1 (book cadence) is in sports/tests/test_audit_board_data_1106.py."""
import copy
import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import workspace_api as api
import workspace_genie as genie
import workspace_market_edge as me
import workspace_sports as ws
from tests.workspace_other_fixture import big_nba_board, quote

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


# ---------------------------------------------------------------------------------------------------------------- N2 / N7

class Feed:
    """The sports service's three board parts; a part set to None fails the read (Down)."""

    def __init__(self, n=30):
        self.primary, self.view = big_nba_board(n)
        self.more = {"version": "ui-snap-1", "part": "secondary", "markets": [], "fc": {}}

    def __call__(self, srv, path, query=None, etag=None, timeout=None):
        doc = getattr(self, (query or {}).get("part") or "primary")
        if doc is None:
            raise ws.Down("no answer")
        return copy.deepcopy(doc), '"%d"' % (hash(json.dumps(doc, sort_keys=True)) & 0xffffff)


@pytest.fixture
def feed(monkeypatch):
    ws.reset()
    f = Feed()
    monkeypatch.setattr(ws, "call", f)
    yield f
    ws.reset()


def _records(body):
    return json.loads(body)["records"]


def test_a_refresh_that_comes_back_without_lines_keeps_the_last_good_board(feed):
    st, good = ws._build(None, "nba")
    assert st == 200 and len(_records(good)) == 60
    feed.primary = dict(feed.primary, markets=[])                 # the audit: PrizePicks 0, Underdog 0
    st, body = ws._build(None, "nba")
    assert st == 200 and body is good and len(_records(ws._SNAP["nba"]["body"])) == 60
    full = big_nba_board(30)[0]
    feed.primary = dict(full, markets=[dict(m, q=[q for q in m["q"] if q["p"] != "prizepicks"]) for m in full["markets"]])
    st, body = ws._build(None, "nba")                             # one platform's lines all gone at once: kept as well
    assert body is good


def test_an_empty_board_is_taken_once_the_last_good_one_is_past_its_age(feed):
    st, good = ws._build(None, "nba")
    ws._SNAP["nba"]["as_of"] = (datetime.now(timezone.utc) - timedelta(seconds=ws.STALE_S + 60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    feed.primary = dict(feed.primary, markets=[])                 # a slate that really emptied never freezes the board
    st, body = ws._build(None, "nba")
    assert _records(body) == []


def test_a_refresh_whose_hit_rates_failed_keeps_the_last_good_board_for_a_while(feed):
    st, good = ws._build(None, "nba")
    feed.view = None                                              # the hit-rate part did not answer
    st, body = ws._build(None, "nba")
    assert body is good                                           # was a board with every hit rate blank
    ws._SNAP["nba"]["good_at"] = time.time() - ws.PART_KEEP_S - 1
    feed.primary = copy.deepcopy(feed.primary)
    feed.primary["markets"][0]["q"][0]["l"] = 21.5               # the primary lines moved meanwhile
    st, body = ws._build(None, "nba")
    assert body is not good and len(_records(body)) == 60         # a part that stays down never holds the lines back


def test_sports_lines_that_moved_carry_their_first_line(feed):
    ws._build(None, "nba")
    assert all(r["offer"]["moved_from"] is None for r in _records(ws._SNAP["nba"]["body"]))
    feed.primary = copy.deepcopy(feed.primary)
    m = feed.primary["markets"][3]
    ud = next(q for q in m["q"] if q["p"] == "underdog")
    ud["l"] = 21.5
    st, body = ws._build(None, "nba")
    moved = [r for r in _records(body) if r["offer"]["moved_from"] is not None]
    assert len(moved) == 1 and moved[0]["offer"]["platform"] == "underdog" and moved[0]["offer"]["line"] == 21.5
    assert moved[0]["offer"]["moved_from"] == 20.5 and moved[0]["offer"]["moved_at"]
    # the card / drawer records come from the same index entry
    x = next(x for x in ws._SNAP["nba"]["index"].values() if x["p"] == "underdog" and x["l"] == 21.5)
    assert json.loads(x["rec"])["offer"]["moved_from"] == 20.5
    ud["l"] = 20.5                                                # back where it opened: not a moved line
    st, body = ws._build(None, "nba")
    assert all(r["offer"]["moved_from"] is None for r in _records(body))


def test_two_standard_lines_on_one_platform_market_have_no_single_opening_line(feed):
    ws._build(None, "nba")
    feed.primary = copy.deepcopy(feed.primary)
    m = feed.primary["markets"][5]
    m["q"].append(quote("underdog", "ud:alt5", 22.5, None, mult=[1.0, 1.0]))
    st, body = ws._build(None, "nba")
    assert all(r["offer"]["moved_from"] is None for r in _records(body))


# ---------------------------------------------------------------------------------------------------------------- N1 board side

def test_a_book_quote_read_on_the_new_cadence_counts_as_fresh_on_the_board_and_in_market_edge():
    """N1 (the sports side is in sports/tests/test_audit_board_data_1106.py): with FanDuel / BetRivers read every 8 min up to
    72 h out (+ a 1-min job check), the board's stale rule and Market edge's limit both still count the quote as fresh; the
    audit's 56-min quotes stay stale."""
    import workspace_books as wb
    import workspace_ev as we
    from gradecore import fair
    cfg = json.loads((ROOT / "book_providers.json").read_text(encoding="utf-8"))
    now = time.time()
    assert we.price_limits()["sportsbook"] == fair.FRESHNESS_S["sportsbook"] - 2 * we.TTL       # the sports test's copy of the rule
    for pid in ("fanduel", "betrivers"):
        feed = cfg["providers"][pid]["feed"]
        for hours in (3, 30, 48, 70):
            recs = [{"event": {"start_utc": datetime.fromtimestamp(now + hours * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}}]
            every = next(e for h, e in feed["ladder"] if hours <= h)
            assert every <= 10, (pid, hours)                                      # was 180 min from 24 to 72 h
            limit = wb.quote_stale_after(feed, recs, now)
            assert every * 60 + 60 <= limit <= we.price_limits()["sportsbook"], (pid, hours, limit)
            assert 56 * 60 > limit                                                # the audit's 56-min quotes: still stale


# ---------------------------------------------------------------------------------------------------------------- N2 client

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_page_keeps_its_lines_when_a_read_comes_back_empty(tmp_path):
    src = (ROOT / "web" / "upgrade.js").read_text(encoding="utf-8")
    keep = re.search(r"EMPTY_KEEP = (\d+)", src).group(1)
    fn = re.search(r"  const emptyRead = .*\n", src).group(0)
    assert "emptyRead(doc, b, Date.now())" in src and 'throw new Error("empty board")' in src
    js = (f"const EMPTY_KEEP = {keep};\n{fn}\nconst now = 1e12, held = {{recs: new Map([[1, 1]]), goodAt: now - 60000}};\n"
          "console.log(JSON.stringify([emptyRead({records: []}, held, now), emptyRead({records: [{}]}, held, now),"
          " emptyRead({records: []}, {recs: new Map()}, now), emptyRead({records: []}, {...held, goodAt: now - EMPTY_KEEP - 1}, now)]));")
    (tmp_path / "t.js").write_text(js, encoding="utf-8")
    out = subprocess.run([NODE, str(tmp_path / "t.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [True, False, False, False]


# ---------------------------------------------------------------------------------------------------------------- N3

@pytest.fixture
def hits(monkeypatch):
    api.reset()
    reads = []

    def history(srv, x, full=False):
        reads.append(threading.current_thread().name)
        time.sleep(0.01)
        return {"ser": [{"value": v} for v in (4, 5, 6, 7, 8, 9)]}
    monkeypatch.setattr(api, "_history", history)
    monkeypatch.setattr(api, "HITS_BUDGET_S", 0.05)
    monkeypatch.setattr(api, "HITS_SLICE_S", 0.05)
    monkeypatch.setattr(api.workspace_h2h, "board_enabled", lambda: False)
    yield reads
    api.reset()


def _wait(cond, s=10.0):
    t = time.time() + s
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _rows():
    return json.loads(api._HITS_DOC["body"] or b'{"rows":{}}')["rows"]


def test_hit_rates_fill_every_row_in_the_background_without_more_reads(hits):
    snap = {"index": {("underdog", str(i)): {"line": 5.5} for i in range(120)}}
    first = json.loads(api._hits_request(None, snap))
    assert first["complete"] is False and len(first["rows"]) < 120  # the first read waited one short slice, no longer
    assert _wait(lambda: len(_rows()) == 120 and json.loads(api._HITS_DOC["body"])["complete"])   # no member read since
    assert all(r["l5"]["n"] == 5 and r["l20"]["n"] == 6 for r in _rows().values())
    assert "MainThread" not in hits[len(hits) - 1:] and set(hits) == {"ws-hits-fill"}   # never read on a member's thread


def test_a_new_board_is_filled_as_soon_as_it_is_built(hits):
    snap = {"index": {("underdog", str(i)): {"line": 5.5} for i in range(10)}}
    api._hits_request(None, snap)
    assert _wait(lambda: len(_rows()) == 10)
    more = {"index": {**snap["index"], **{("prizepicks", f"m{i}"): {"line": 6.5} for i in range(30)}}}   # "+N more props"
    api._hits_kick(None, more)                                    # what _build does with each new board
    assert _wait(lambda: len(_rows()) == 40)
    assert _rows()["prizepicks|m3"]["line"] == 6.5


def test_many_members_reading_at_once_share_one_filler(hits):
    snap = {"index": {("underdog", str(i)): {"line": 5.5} for i in range(60)}}
    out = []
    ts = [threading.Thread(target=lambda: out.append(api._hits_request(None, snap))) for _ in range(80)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 80
    assert sum(1 for t in threading.enumerate() if t.name == "ws-hits-fill") <= 1
    assert _wait(lambda: len(_rows()) == 60)
    assert len(hits) == 60                                        # each row's history read once, for every member together


def test_the_board_build_hands_each_new_board_to_the_filler(monkeypatch):
    src = (ROOT / "workspace_api.py").read_text(encoding="utf-8")
    build = src[src.index("def _build(srv):"):src.index("\ndef ", src.index("def _build(srv):") + 10)]
    assert "_hits_kick(srv, _SNAP)" in build
    assert "_hits_request(srv, snap)" in src[src.index("def get_hits("):src.index("def get_stated(")]


# ---------------------------------------------------------------------------------------------------------------- N8

def _rec(book, line, over=None, under=None, label=None, **side):
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"sport": "cs2", "event": {"id": "e1", "start_utc": start, "status": "scheduled"}, "player": {"key": "p1", "name": "Ace"},
            "market": {"stat": "kills", "period": "Maps 1-2", "line_type": "standard"}, "state": {"kind": "ok"},
            "offer": {"platform": book, "platform_label": label or book.title(), "line": line, "offer_id": book + "1",
                      "over": dict({"offered": True}, **(over or {})), "under": dict({"offered": True}, **(under or {}))}}


def test_best_line_shows_the_same_price_as_books_and_lines():
    ud = _rec("underdog", 29.5, {"payout": 1.0, "price": 1.9}, {"payout": 1.0, "price": 1.9}, "Underdog")
    from gradecore import payouts
    offers = me._offers_of_record(ud, payouts.pickem_config(), me.we.price_limits(), time.time())
    assert {x[0]: x[4] for x in offers} == {"over": "1.90", "under": "1.90"}      # was None, printed as "1.00x" beside 1.90 / 1.90
    plain = _rec("underdog", 29.5, {"payout": 1.0}, {"payout": 1.0}, "Underdog")
    assert all(x[4] is None for x in me._offers_of_record(plain, payouts.pickem_config(), me.we.price_limits(), time.time()))
    js = (ROOT / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    assert "'1.00x'" not in js                                    # a standard Underdog line reads "Standard", as Books and lines does


def test_best_lines_lists_every_book_it_counts(monkeypatch):
    recs = [_rec("underdog", 29.5, {"payout": 1.0, "price": 1.9}, {"payout": 1.0, "price": 1.9}, "Underdog"),
            _rec("prizepicks", 28.5, {"payout": 1.0}, {"payout": 1.0}, "PrizePicks")]
    extra = [(_rec("sleeper", 29.5, {"payout": 1.8}, {"payout": 1.75}, "Sleeper"), True, "pickem", None),
             (_rec("dabble", 30.5, {"price": 1.85}, {"price": 1.92}, "Dabble"), False, "pickem", "Dabble price")]
    monkeypatch.setattr(me, "_extra_records", lambda srv, sport: extra)
    ent = me.best_lines(None, "cs2", recs, {"feeds": {}}, time.time())["e1|p1|kills|Maps 1-2"]
    assert ent["n"] == 4
    assert [(b[0], b[1]) for b in ent["bk"]] == [("PrizePicks", 28.5), ("Sleeper", 29.5), ("Underdog", 29.5), ("Dabble", 30.5)]
    by = {b[2]: b for b in ent["bk"]}
    assert by["underdog"][3:] == ["1.90", "1.90"] and by["sleeper"][3:] == ["1.80x", "1.75x"]
    assert by["dabble"][3:] == ["Dabble price 1.85", "Dabble price 1.92"]
    js = (ROOT / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    assert "bestOf(r)?.bk" in js and "${moreRows}" in js           # the card's Books and lines table lists them too


# ---------------------------------------------------------------------------------------------------------------- N9

def _ask_doc(**kw):
    rec = _rec("prizepicks", 28.5, {"payout": 1.0}, {"payout": 1.0}, "PrizePicks")
    rec["scout"] = {"side": "over", "grade": "B-", "p_over": 0.57, "p_under": 0.43}
    return dict({"record": rec, "books": [{"platform": "prizepicks", "platform_label": "PrizePicks", "line": 28.5, "row_id": "prizepicks1"}],
                 "market_books": {}}, **kw)


def test_better_line_answer_names_every_book():
    doc = _ask_doc(extra_books=[{"platform": "dabble", "platform_label": "Dabble", "line": 30.5, "line_type": "standard"},
                                {"platform": "sleeper", "platform_label": "Sleeper", "line": 27.5, "line_type": "standard"}])
    out = genie.answer(doc, "Is there a better line elsewhere?", "over")
    text = " ".join(out["sentences"])
    assert out["matched"] and "Dabble lists 30.5" in text and "Sleeper lists 27.5" in text
    assert "The easiest Over line listed is 27.5 on Sleeper" in text
    out = genie.answer(doc, "Is there a better line elsewhere?", "under")
    assert "The easiest Under line listed is 30.5 on Dabble" in " ".join(out["sentences"])


def test_should_i_take_the_over_states_the_letters_settled_record():
    tier = {"name": "B-", "wins": 61, "losses": 49, "decided": 110, "observed": 0.5545}
    out = genie.answer(_ask_doc(tier_record=tier), "Should I take the over?", "over")
    assert "Settled B- picks have won 61 of 110 (55.5%)." in out["sentences"]
    assert not any(w in " ".join(out["sentences"]).lower() for w in ("unvalidated", "beta", "estimate"))
    out = genie.answer(_ask_doc(tier_record=None), "Should I take the over?", "over")
    assert not any("Settled" in s for s in out["sentences"])        # no record data: no sentence, never a guess
    out = genie.answer(_ask_doc(tier_record=dict(tier, name="A")), "Should I take the over?", "over")
    assert not any("Settled" in s for s in out["sentences"])        # another letter's record is never quoted


class _Srv:
    def __init__(self):
        self.store, self.calls = {}, 0

    def shared(self, key, compute, ttl=60):
        if key not in self.store:
            self.calls += 1
            self.store[key] = compute()
        return self.store[key]


def test_tier_record_reads_the_record_page_rows_once_for_everyone(monkeypatch):
    srv = _Srv()
    rows = {"letters": [{"name": "B-", "wins": 61, "losses": 49, "observed": 0.5545}, {"name": "A", "wins": 0, "losses": 0, "observed": None}]}
    monkeypatch.setattr(ws, "results", lambda s, lg: rows)
    rec = _ask_doc()["record"]
    for _ in range(50):
        t = genie.tier_record(srv, "nba", rec)
    assert t == {"name": "B-", "wins": 61, "losses": 49, "decided": 110, "observed": 0.5545} and srv.calls == 1
    srv2 = _Srv()
    monkeypatch.setattr(ws, "results", lambda s, lg: (_ for _ in ()).throw(ws.Down("results")))
    assert genie.tier_record(srv2, "nba", rec) is None


def test_extra_book_lines_are_read_from_their_shared_board(monkeypatch):
    import book_providers as bp
    import workspace_books as wb
    wb.reset()
    dab = _rec("dabble", 30.5, {"price": 1.85}, {"price": 1.92}, "Dabble")
    body = json.dumps({"feed": {"state": "ok"}, "records": [dab]}).encode()
    monkeypatch.setattr(bp, "enabled_ids", lambda: ["dabble"])
    monkeypatch.setattr(bp, "providers", lambda: {"dabble": {"source": "direct", "sports": ["cs2"]}})
    monkeypatch.setattr(wb, "board", lambda srv, sport, pid: (200, body))
    got = wb.prop_lines(None, "cs2", _ask_doc()["record"])
    assert got == [{"platform": "dabble", "platform_label": "Dabble", "line": 30.5, "line_type": "standard"}]
    other = dict(_ask_doc()["record"], player={"key": "p2"})
    assert wb.prop_lines(None, "cs2", other) == []
    wb.reset()
