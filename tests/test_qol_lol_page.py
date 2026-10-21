"""2026-10-21 QoL item 10: the LoL page catches up with CS2 - player/team search, sort by best grade, a grade floor,
"Forecast saved X min ago" on each row (from the row's own forecast_at), and a link to a single card by the line's key
with a Copy link button. Stakes stay 0U; rows keep their data-k keys and the started-match handling (audit 60 / 61).
The page's own script runs in node against the stub DOM (tests/test_audit_h_web.py)."""
from datetime import datetime, timedelta, timezone

from tests.test_audit_h_web import FUTURE, PAST, _lol_row, needs_node, run


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rows():
    a = _lol_row("k-a", "Alpha", FUTURE, p_over=0.75); a.update(grade="A", team="T1", forecast_at=_ago(12))
    b = _lol_row("k-b", "Bravo", FUTURE, p_over=0.64); b.update(grade="B+", team="GEN", opponent="T1", forecast_at=_ago(130))
    c = _lol_row("k-c", "Charlie", FUTURE, p_over=0.58); c.update(grade="B", team="HLE", opponent="DK", forecast_at=_ago(0))
    d = _lol_row("k-d", "Delta", FUTURE, p_over=0.52); d.update(grade="Lean Over", team="DK", opponent="HLE", forecast_at=_ago(3000))
    e = _lol_row("k-e", "Echo", FUTURE, p_over=0.80, offered=False); e.update(grade=None, team="KT", opponent="BRO")
    f = _lol_row("k-f", "Foxtrot", PAST, p_over=0.85); f.update(grade="A+", team="BRO", opponent="KT", forecast_at=_ago(400))
    return [a, b, c, d, e, f]


@needs_node
def test_search_floor_and_grade_sort(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; const keys = () => rows().map(x => x.key);
      F.q = "t1"; const t1 = keys();                 // team or opponent, any case
      F.q = " alp "; const alpha = keys();
      F.q = "zzz"; render(); const none = $("#app").innerHTML;
      F.q = ""; F.floor = "B+"; const floor = keys();
      F.floor = "A-"; const aminus = keys();
      F.floor = "all"; F.sort = "grade"; const graded = keys();
      F.sort = "start"; const start = keys();
      return {t1, alpha, none, floor, aminus, graded, start};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["t1"] == ["k-a", "k-b"] and out["alpha"] == ["k-a"]
    assert "No lines match these filters." in out["none"] and 'id="q"' in out["none"]   # the search box stays to edit
    assert sorted(out["floor"]) == ["k-a", "k-b", "k-f"]           # floor on the shown grade; no Lean, no unoffered line
    assert sorted(out["aminus"]) == ["k-a", "k-f"]
    # best grade first among playable picks; a started match (A+) and the unoffered line never rank above them
    assert out["graded"] == ["k-a", "k-b", "k-c", "k-d", "k-f", "k-e"]
    assert out["start"][0] == "k-f"                                 # start-time sort unchanged


@needs_node
def test_rows_say_when_the_forecast_was_saved(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render();
      return {html: $("#app").innerHTML, s: D.rows.map(savedAgo)};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["s"] == ["Forecast saved 12 min ago", "Forecast saved 2 h 10 min ago", "Forecast saved just now",
                        "Forecast saved 2 days ago", "", "Forecast saved 6 h 40 min ago"]
    assert out["html"].count("Forecast saved 12 min ago") == 2      # the table row and the phone card
    assert out["html"].count('data-k="k-a"') == 2 and "0U" in out["html"]
    assert out["html"].count('<span class="tag">Started</span>') == 2


@needs_node
def test_card_link_opens_by_key_and_copies(tmp_path):
    out = run(tmp_path, "lol.html", """(async () => {
      D = INPUT; render();
      const urls = []; history.replaceState = (a, b, u) => urls.push(u);
      let copied = null; navigator.clipboard.writeText = async t => { copied = t; };
      location.search = "?line=k-b"; openLinked();
      const opened = openRow, html = $("#card").innerHTML;
      await $("#copylink").onclick();
      const msg = $("#actmsg").textContent;
      closeCard();
      location.search = "?line=gone"; openLinked();
      const after = openRow, board = $("#app").innerHTML;
      render(); const again = $("#app").innerHTML;
      return {opened, html, copied, msg, urls, after, board, again};
    })()""", {"rows": _rows(), "match_tables": []})
    assert out["opened"] == "k-b" and '<h2 id="cardtitle">Bravo</h2>' in out["html"] and 'id="copylink"' in out["html"]
    assert out["copied"] == "https://x/lol?line=k-b" and out["msg"].startswith("Link copied")
    assert out["urls"] == ["/lol?line=k-b", "/lol", "/lol"]         # open -> the card's address; close / gone -> the board
    assert out["after"] is None and "That line is no longer on the board." in out["board"]
    assert "no longer on the board" not in out["again"]             # the notice shows once


@needs_node
def test_bar_has_floor_and_grade_sort_and_the_poll_is_jittered(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render();
      const waits = []; setTimeout = (fn, ms) => { waits.push(ms); return 0; };
      for (let i = 0; i < 20; i++) poll();
      return {bar: $("#app").innerHTML, waits};
    })()""", {"rows": _rows(), "match_tables": []})
    assert 'id="g"' in out["bar"] and "B+ and up" in out["bar"] and 'value="grade"' in out["bar"]
    assert all(120000 <= w < 140000 for w in out["waits"]) and len(set(out["waits"])) > 1
