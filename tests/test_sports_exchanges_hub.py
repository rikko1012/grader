"""Hub side of the display-only exchanges (Novig, Gemini; sports/books/exchanges.py): labelled like the other exchanges,
read as exchange prices for the best line, never an input of the +EV fair chance."""
import workspace_ev as we
import workspace_market_edge as me
import workspace_markets as wm
import workspace_sports as ws

NOW = 1_800_000_000.0


def _row(platform, over, line=20.5, kind="mid"):
    return {"platform": platform, "line": line, "kind": kind, "over": over, "under": None,
            "seen_at": "2027-01-15T08:00:00Z", "in_consensus": False}


def test_labels():
    assert ws.book_label("novig") == "Novig" and ws.book_label("gemini") == "Gemini"
    assert ws.book_short("novig") == "NV" and ws.book_short("gemini") == "GM"
    assert me.SHORT["novig"] == "Novig" and wm.kind_of("novig") == "prediction" == wm.kind_of("gemini")


def test_never_an_input_of_the_fair_chance():
    qs = we.quotes_of([_row("novig", "0.50 / 0.53"), _row("gemini", [0.4, 0.42]), _row("polymarket", "0.45 / 0.48")])
    assert [q.book for q in qs] == ["polymarket"]


def test_best_line_reads_it_as_an_exchange_price(monkeypatch):
    monkeypatch.setattr(me.wb, "_ts", lambda v: NOW)
    book, offers = me._offers_of_bookrow(_row("novig", "0.50 / 0.53"), {"exchange": 600, "sportsbook": 600}, NOW)
    assert book == "novig"
    assert [(o[0], o[4]) for o in offers] == [("over", "0.53"), ("under", "0.50")]
    assert me._offers_of_record({"offer": {"platform": "novig", "line": 20.5}}, None, None, NOW) == []


def test_exchange_quotes_never_take_a_pickem_record_slot(monkeypatch):
    """Board quotes are sorted by platform, so 'gemini' / 'novig' come first in every market: they have their own
    record budget and every PrizePicks / Underdog line up to MAX_RECORDS still reaches the hub (check_leg finds it)."""
    import copy

    from tests.workspace_other_fixture import big_nba_board, quote
    primary, view = big_nba_board(30)
    for i, m in enumerate(primary["markets"]):
        m["q"] = [quote("gemini", f"gm:b{i}", 20.5, k=[0.5, 0.53]), quote("novig", f"nv:b{i}", 20.5, k=[0.5, 0.53])] + m["q"]
    more = {"version": "ui-snap-1", "part": "secondary", "markets": [], "fc": {}}
    docs = {"primary": primary, "view": view, "more": more}
    monkeypatch.setattr(ws, "call", lambda srv, path, query=None, etag=None, timeout=None: (
        copy.deepcopy(docs[(query or {}).get("part") or "primary"]), '"1"'))
    monkeypatch.setattr(ws, "MAX_RECORDS", 60)
    monkeypatch.setattr(ws, "MAX_DISPLAY_RECORDS", 10)
    ws.reset()
    try:
        st, body = ws._build(None, "nba")
        import json
        recs = json.loads(body)["records"]
        plats = [r["offer"]["platform"] for r in recs]
        assert plats.count("prizepicks") == 30 and plats.count("underdog") == 30
        assert plats.count("novig") + plats.count("gemini") == 10
        idx = ws._SNAP["nba"]["index"]
        assert sum(1 for (p, _) in idx if p in ws.PICKEM) == 60
    finally:
        ws.reset()
