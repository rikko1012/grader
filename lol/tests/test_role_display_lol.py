"""LoL role tags on the board rows (owner 2026-11-07: Top / Jungle / Mid / ADC / Support on every player card). Display only:
the offer's linked role (game data), else the latest forecast's role; Bot reads "ADC"; a combined feed position is never a
role; neither source = no role. The forecast, its inputs and the model identity are untouched."""
from lol import model
from lol.center import forecast, names, role_display as lrd
from lol.tests.test_consolidation import con, _future_world  # noqa: F401 - a fixture


def _row(con):
    row, = forecast.board(con)["rows"]
    return row


def test_board_row_role_text_and_fallbacks(con, tmp_path, monkeypatch):
    db, _start, _raw = _future_world(con, tmp_path, monkeypatch)
    assert con.execute("SELECT role FROM market_links").fetchone()[0] == "Mid"
    assert _row(con)["role"] == "Mid"                                           # the linked role, before any forecast
    assert forecast.run(con, db)["saved"] == 1
    saved = [tuple(r) for r in con.execute("SELECT * FROM lol_forecasts")]
    assert _row(con)["role"] == "Mid"
    assert [tuple(r) for r in con.execute("SELECT * FROM lol_forecasts")] == saved     # reading the board writes nothing
    con.execute("UPDATE market_links SET role = 'Bot'")
    assert _row(con)["role"] == "ADC"                                           # owner wording, not "Bot/ADC"
    con.execute("UPDATE market_links SET role = NULL")
    assert _row(con)["role"] == "Mid"                                           # the latest forecast's role (game data)
    con.execute("UPDATE lol_forecasts SET role = 'bot'")
    assert _row(con)["role"] == "ADC"
    con.execute("UPDATE market_links SET role = 'TOP/JNG/MID'")
    con.execute("DELETE FROM lol_forecasts")
    assert _row(con)["role"] is None                                            # combined feed position, no forecast: nothing
    con.execute("UPDATE market_links SET role = NULL")
    assert _row(con)["role"] is None
    assert model.VERSION == "lol-1"                                             # the model identity is untouched


def test_match_table_carries_the_same_role(con, tmp_path, monkeypatch):
    _future_world(con, tmp_path, monkeypatch)
    con.execute("UPDATE market_links SET role = 'Bot'")
    b = forecast.board(con)
    row, = b["rows"]
    roles = [m["role"] for t in b["match_tables"] for m in t["markets"].values()]
    assert row["role"] == "ADC" and roles == ["ADC"]


def test_lol_label_text():
    assert lrd.label("Bot") == "ADC" and lrd.label("Top") == "Top" and lrd.label("Jungle") == "Jungle"
    assert lrd.label("Mid") == "Mid" and lrd.label("Support") == "Support"
    assert lrd.label(None, "bot") == "ADC" and lrd.label(None, "support") == "Support"     # forecast fallback (game data)
    assert lrd.label("TOP/JNG/MID") is None and lrd.label(None) is None and lrd.label("", "") is None
    assert lrd.label("TOP/JNG/MID", "jungle") == "Jungle"


def test_lol_names_role_label_untouched():
    assert names.ROLE_LABEL["Bot"] == "Bot/ADC"
    assert names.role("TOP/JNG/MID") is None and names.role("adc") == "Bot"
