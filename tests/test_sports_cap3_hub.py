"""cap-3 (owner 2026-11-08): the hub board maps a near-even-price hold (q.x b "cap") to scout.hold with the fact
"Grader grades lines priced near even." - no side, no letter, no stake, chances and projection still shown - and sorting by
Grader chance never puts these rows above graded ones (they carry no p_side)."""
import json
import time
from datetime import datetime, timezone

import workspace_books as wb
import workspace_sports as ws

TEXT = "Grader grades lines priced near even."


def _rec(p, x, mk=None, po=0.19, pu=0.81, line=24.5, rid="offer:1"):
    q = {"p": p, "r": rid, "l": line, "o": [1, 1], "f": "f1", "x": x}
    m = {"k": "nba:e1|p1|points|full_game", "e": "nba:e1", "pid": "p1", "n": "Julius Randle", "st": "points", "mk": mk}
    doc = {"fc": {"f1": {"m": "sm-1", "po": po, "pu": pu, "pp": 0, "mu": 19.2}}}
    return ws.record_of("nba", doc, m, q, {})


def test_cap3_hold_reaches_the_hub_as_the_fact():
    mk = {"at": {"offer:1": {"po": 0.202, "pu": 0.798, "n": 3}}}
    rec, x = _rec("polymarket", {"b": "cap", "t": TEXT, "v": "cap-3"}, mk)
    s = rec["scout"]
    assert s["side"] is None and s["grade"] is None and s["p_side"] is None and s["units"] is None and x["lean"] is None
    assert s["hold"] == {"label": ws.HOLD_LABEL, "reason": TEXT, "short": "Priced far from even"} and s["validation"]["detail"] == TEXT
    assert s["p_over"] == 0.19 and s["p_under"] == 0.81 and s["projection"] == 19.2
    assert rec["market_chance"] == {"p_over": 0.202, "p_under": 0.798, "sources": 3}
    for bad in ("may ", "might", "unsure", "not validated", "switched off", "held"):
        assert bad not in s["hold"]["reason"].lower(), bad


def test_graded_rows_sort_above_cap3_holds_by_scout_chance():
    held, _ = _rec("polymarket", {"b": "cap", "t": TEXT, "v": "cap-3"}, rid="offer:h")
    a, _ = _rec("prizepicks", {"v": "cf-2", "ln": "over", "gr": "A", "gp": "sports-gp-1"}, po=0.62, pu=0.38, line=18.5,
                rid="offer:a")
    lean, _ = _rec("underdog", {"v": "cf-2", "ln": "over", "gr": "Lean", "gp": "sports-gp-1"}, po=0.52, pu=0.48,
                   line=19.5, rid="offer:l")
    assert a["scout"]["grade"] == "A" and lean["scout"]["grade"] == "Lean"
    rows = [held, lean, a]
    # = web/upgrade-hub.js sortValue('chance') + byValue: an empty value sorts after every filled one
    rows.sort(key=lambda r: (r["scout"]["p_side"] is None, -(r["scout"]["p_side"] or 0)))
    assert [r["scout"]["grade"] for r in rows] == ["A", "Lean", None]


def test_board_quotes_to_hub_records():
    """The compact board quotes sports/snapshots.guard_row + compact_quote write for one row (asserted on the sports side in
    sports/tests/test_cap3_long_odds.py test_compact_board_row_carries_the_hold; this module may not import sports) ->
    hub records: long-odds Polymarket / BetRivers lines held with the fact, the near-even Polymarket rung and the pick'em
    lines keep their letters; Grader-chance sorting puts the holds last."""
    cap = {"b": "cap", "t": TEXT, "v": "cap-3"}
    graded = {"ln": "under", "v": "cf-2", "n": 0, "c": 3, "cw": [], "pl": 0.81, "gr": "A", "gp": "sports-gp-1"}
    qs = [{"r": "pm245", "p": "polymarket", "l": 24.5, "v": "ladder_rung", "o": [1, 1], "f": "f245", "k": [0.19, 0.21], "x": cap},
          {"r": "pm195", "p": "polymarket", "l": 19.5, "v": "ladder_rung", "o": [1, 1], "f": "f195", "k": [0.41, 0.44],
           "x": dict(graded, pl=0.64)},
          {"r": "br245", "p": "betrivers", "l": 24.5, "v": "standard", "o": [1, 1], "f": "fbr", "d": [4.6, 1.18], "x": cap},
          {"r": "pp245", "p": "prizepicks", "l": 24.5, "v": "standard", "o": [1, 1], "f": "fpp", "x": graded},
          {"r": "ud245", "p": "underdog", "l": 24.5, "v": "standard", "o": [1, 1], "f": "fud", "x": graded}]
    m = {"k": "nba:e1|p1|points|full_game", "e": "nba:e1", "pid": "p1", "n": "Julius Randle", "st": "points", "q": qs,
         "mk": {"at": {"pp245": {"po": 0.2, "pu": 0.8, "n": 3}, "ud245": {"po": 0.2, "pu": 0.8, "n": 3}}}}
    fc = {f: {"m": "sm-1", "po": 0.19, "pu": 0.81, "pp": 0.0, "mu": 19.2} for f in ("f245", "fbr", "fpp", "fud")}
    fc["f195"] = {"m": "sm-1", "po": 0.36, "pu": 0.64, "pp": 0.0, "mu": 19.2}
    recs = {q["r"]: ws.record_of("nba", {"fc": fc}, m, q, {})[0] for q in qs}
    for rid in ("pm245", "br245"):
        s = recs[rid]["scout"]
        assert s["side"] is None and s["grade"] is None and s["p_side"] is None and s["units"] is None
        assert s["hold"]["reason"] == TEXT and s["p_under"] == 0.81 and s["projection"] == 19.2
    assert {rid: recs[rid]["scout"]["grade"] for rid in ("pm195", "pp245", "ud245")} == {"pm195": "A", "pp245": "A",
                                                                                          "ud245": "A"}
    order = sorted(recs.values(), key=lambda r: (r["scout"]["p_side"] is None, -(r["scout"]["p_side"] or 0)))
    assert [r["scout"]["p_side"] is None for r in order] == [False, False, False, True, True]


# ------------------------------------------------------------------ the hub's extra-book view (workspace_books, review 2026-11-08)
GRADED = {"ln": "under", "v": "cf-2", "n": 0, "c": 3, "cw": [], "pl": 0.81, "gr": "A", "gp": "sports-gp-1"}


def _pickem_rec(platform="underdog", mk=None):
    rec, _ = _rec(platform, dict(GRADED), mk or {"at": {"offer:1": {"po": 0.2, "pu": 0.8, "n": 3}}})
    assert rec["scout"]["side"] == "under" and rec["scout"]["grade"] == "A"
    return rec


def _side(price):
    return {"offered": price is not None, "payout": price, "price": price}


def _held(s):
    assert s["side"] is None and s["grade"] is None and s["p_side"] is None and s["units"] is None, s
    assert s["hold"] == {"label": ws.HOLD_LABEL, "reason": TEXT} and s["validation"]["detail"] == TEXT


# = sports/tests/test_cap3_long_odds.py GOLDEN (asserted there against sports/board_view.price_cap; hub tests never import
# the sports package): (over price, under price, side, market (po, pu) or None, held)
GOLDEN = [(1.10, 7.0, "over", None, True), (1.10, 7.0, "under", None, False), (7.0, 1.10, "under", None, True),
          (1.87, 1.95, "over", None, False), (1.87, 1.95, "under", None, False), (1.50, 2.60, "over", None, False),
          (1.45, 2.80, "over", None, True), (None, 1.10, "under", (0.2, 0.8), True), (None, 1.80, "under", (0.45, 0.55), False),
          (None, 1.80, "under", (0.35, 0.65), False), (None, None, "over", None, True), (4.6, 1.18, "under", (0.45, 0.55), True),
          (2.0, 1.83, "under", (0.2, 0.8), False)]


def _board_view_constants():
    """sports/board_view.py's cap-3 constants read from its source (hub tests never import the sports package)."""
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).resolve().parents[1] / "sports" / "board_view.py").read_text(encoding="utf-8"))
    want = {"CAP_VERSION", "MAX_SIDE_MARKET", "CAP_TEXT"}
    return {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
            if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in want}


def test_mirrored_constants_match_the_hub_board_and_board_view():
    assert wb.CAP_TEXT == TEXT and wb.CAP_HOLD_LABEL == ws.HOLD_LABEL
    assert _board_view_constants() == {"CAP_VERSION": wb.CAP_VERSION, "MAX_SIDE_MARKET": wb.CAP_MAX_SIDE_MARKET,
                                       "CAP_TEXT": wb.CAP_TEXT}


def test_mirror_gives_the_golden_answers():
    for o, u, side, mc, held in GOLDEN:
        po = wb.own_po(_side(o), _side(u))
        assert wb.cap_held("sportsbook", side, po, {"p_over": mc[0], "p_under": mc[1]} if mc else None) == held, (o, u, side, mc)
        assert not wb.cap_held("pickem", side, po, None)


def test_extra_book_same_line_long_odds_is_held():
    """The reviewed case: Underdog 24.5 Under .81 graded A; FanDuel 24.5 at 7.0 / 1.10 (Under ~.86) in the book toggle showed
    'A Under 81%' copied from Underdog. Now held with the fact, chances and projection kept."""
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [_pickem_rec()], 24.5, _side(7.0), _side(1.10), None, 1.0, "k")
    _held(r["scout"])
    assert r["scout"]["p_over"] == 0.19 and r["scout"]["p_under"] == 0.81 and r["scout"]["projection"] == 19.2
    assert r["extra"]["same_line_as"] == "Underdog"


def test_extra_book_same_line_near_even_keeps_the_board_read():
    base = _pickem_rec()
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [base], 24.5, _side(2.25), _side(1.70), None, 1.0, "k")
    s = r["scout"]                                      # Under no-vig ~.57: near even -> Underdog's side, chance and letter
    assert s["side"] == "under" and s["grade"] == "A" and s["p_side"] == 0.81 and s["units"] is None and s["hold"] is None


def test_extra_book_one_sided_line_uses_the_board_market_chance_at_that_line():
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [_pickem_rec()], 24.5, _side(None), _side(1.10), None, 1.0, "k")
    _held(r["scout"])                                   # market Under .80 at 24.5
    even = _pickem_rec(mk={"at": {"offer:1": {"po": 0.45, "pu": 0.55, "n": 3}}})
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [even], 24.5, _side(None), _side(1.80), None, 1.0, "k")
    assert r["scout"]["side"] == "under" and r["scout"]["grade"] == "A"


def test_extra_book_other_line_long_odds_is_held_and_near_even_keeps_its_side():
    def read(chance):
        return lambda base, line: (chance, None)
    long_ = {"side": "under", "p_side": 0.9, "p_over": 0.1, "p_under": 0.9, "p_push": 0.0}
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [_pickem_rec()], 26.5, _side(9.0), _side(1.05), None, 1.0, "k",
                  read=read(long_))
    _held(r["scout"])
    assert r["scout"]["p_under"] == 0.9 and r["extra"]["line_chance"] is True
    even = {"side": "under", "p_side": 0.6, "p_over": 0.4, "p_under": 0.6, "p_push": 0.0}
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [_pickem_rec()], 22.5, _side(2.0), _side(1.83), None, 1.0, "k",
                  read=read(even))
    assert r["scout"]["side"] == "under" and r["scout"]["p_side"] == 0.6 and r["scout"]["hold"] is None
    # another line with no own prices: the board's market chance is at a different line, so nothing judges the price -> held
    r = wb.derive("fanduel", "FanDuel", "sportsbook", "nba", [_pickem_rec()], 22.5, _side(None), _side(1.83), None, 1.0, "k",
                  read=read(even))
    _held(r["scout"])


def test_pickem_extra_book_is_never_capped():
    r = wb.derive("sleeper", "Sleeper", "pickem", "nba", [_pickem_rec()], 24.5, _side(7.0), _side(1.10), None, 1.0, "k")
    assert r["scout"]["side"] == "under" and r["scout"]["grade"] == "A" and r["scout"]["hold"] is None


def test_extra_book_build_end_to_end_matches_the_hub_board(monkeypatch):
    """build() for FanDuel on NBA (sports_service): the long-odds FanDuel line is held exactly like the hub board's own FanDuel
    record (record_of with the server's cap-3 hold), the near-even one keeps the board's read, and Grader-chance sorting puts
    the held row last."""
    now = time.time()
    seen = datetime.fromtimestamp(now - 60, timezone.utc).isoformat(timespec="seconds")
    start = datetime.fromtimestamp(now + 6 * 3600, timezone.utc).isoformat(timespec="seconds")
    bk = [{"p": "fanduel", "l": 24.5, "kind": "book", "c": 0.14, "over": 7.0, "under": 1.10, "t": seen, "in": True},
          {"p": "fanduel", "l": 19.5, "kind": "book", "c": 0.44, "over": 2.25, "under": 1.75, "t": seen, "in": True}]
    m = {"k": "nba:e1|p1|points|full_game", "e": "nba:e1", "pid": "p1", "n": "Julius Randle", "st": "points", "tm": "NY",
         "mk": {"at": {"ud245": {"po": 0.2, "pu": 0.8, "n": 3}, "ud195": {"po": 0.44, "pu": 0.56, "n": 3}}, "bk": bk}}
    fc = {"f245": {"m": "sm-1", "po": 0.19, "pu": 0.81, "pp": 0, "mu": 19.2}, "f195": {"m": "sm-1", "po": 0.36, "pu": 0.64, "pp": 0, "mu": 19.2}}
    doc = {"fc": fc, "events": {"nba:e1": {"start_utc": start, "home": {"abbr": "NY"}, "away": {"abbr": "BOS"}}}}
    s = {"index": {}, "props": {}}
    recs = []
    for rid, line, f in (("ud245", 24.5, "f245"), ("ud195", 19.5, "f195")):
        q = {"p": "underdog", "r": rid, "l": line, "o": [1, 1], "f": f, "x": dict(GRADED, pl=0.81 if line == 24.5 else 0.64)}
        rec, x = ws.record_of("nba", doc, m, q, {})
        x["books"] = ws._book_rows(m["mk"], line)
        x["rec"] = json.dumps(rec).encode()
        s["index"][("underdog", x["rid"])] = x
        s["props"].setdefault((x["e"], x["pkey"], x["st"], x["scope"]), []).append(("underdog", x["rid"]))
        recs.append(rec)
    monkeypatch.setattr(wb, "records", lambda srv, sport: (recs, s))
    monkeypatch.setattr(wb, "reader", lambda srv, sport, snap: (lambda base, line: (None, None)))
    st, out = wb.build(None, "nba", "fanduel", now=now)
    assert st == 200
    rows = {r["offer"]["line"]: r for r in out["records"]}
    assert set(rows) == {24.5, 19.5}
    _held(rows[24.5]["scout"])
    assert rows[19.5]["scout"]["side"] == "under" and rows[19.5]["scout"]["grade"] == "A"
    # the hub board's own FanDuel 24.5 record (server hold) says the same
    board, _ = ws.record_of("nba", doc, m, {"p": "fanduel", "r": "fd245", "l": 24.5, "o": [1, 1], "d": [7.0, 1.10], "f": "f245",
                                            "x": {"b": "cap", "t": TEXT, "v": "cap-3"}}, {})
    for k in ("side", "grade", "p_side"):
        assert rows[24.5]["scout"][k] == board["scout"][k], k
    # same label and sentence; the board row also carries the short plain reason (owner 2026-11-12), which the page derives from
    # this sentence for an extra-book row
    assert {k: v for k, v in board["scout"]["hold"].items() if k != "short"} == rows[24.5]["scout"]["hold"]
    order = sorted(out["records"], key=lambda r: (r["scout"]["p_side"] is None, -(r["scout"]["p_side"] or 0)))
    assert [r["offer"]["line"] for r in order] == [19.5, 24.5]
