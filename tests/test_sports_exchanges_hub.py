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
