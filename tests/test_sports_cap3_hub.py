"""cap-3 (owner 2026-11-08): the hub board maps a near-even-price hold (q.x b "cap") to scout.hold with the fact
"Scout grades lines priced near even." - no side, no letter, no stake, chances and projection still shown - and sorting by
Scout chance never puts these rows above graded ones (they carry no p_side)."""
import workspace_sports as ws

TEXT = "Scout grades lines priced near even."


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
    assert s["hold"] == {"label": ws.HOLD_LABEL, "reason": TEXT} and s["validation"]["detail"] == TEXT
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
    lines keep their letters; Scout-chance sorting puts the holds last."""
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
