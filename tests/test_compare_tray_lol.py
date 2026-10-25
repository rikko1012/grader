"""Compare on the LoL page (owner 2026-10-25): the same tray as CS2 - up to 6 lines, one stated rule for the better line, the
board's own numbers, a same-match warning (never a combined chance) and Add to slip through the page's own slip rules.
Every player and number is a TEST FIXTURE."""
import re

from tests.test_audit_h_web import FUTURE, PAST, _lol_row, needs_node, run


def rows():
    a = _lol_row("k-a", "Alpha", FUTURE, p_over=0.66); a.update(grade="B+", projection=5.4)
    b = _lol_row("k-b", "Bravo", FUTURE, p_over=0.61); b.update(grade="B", team="GEN", opponent="T1")
    c = _lol_row("k-c", "Charlie", "2099-01-02T18:00:00Z", p_over=0.71); c.update(grade="Lean Over", team="HLE", opponent="DK", match="HLE vs DK")
    d = _lol_row("k-d", "Delta", PAST, p_over=0.80); d.update(grade="A", team="KT", opponent="BRO", match="KT vs BRO")
    # the same player, prop and games on PrizePicks at 5.5, where the model's pick is the Under
    e = _lol_row("k-e", "Alpha", FUTURE, p_over=0.44); e.update(platform="prizepicks", platform_label="PrizePicks", line=5.5, pick_side="Under",
                                                          pick="Under 5.5", grade="Lean Under", projection=5.4)
    return [a, b, c, d, e]


@needs_node
def test_lol_tray_verdict_rule_and_numbers_equal_the_board(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render(); const board = $("#app").innerHTML;
      for (const k of ["k-a", "k-b", "k-c", "k-d", "k-e"]) ctToggle(k);
      const S = ctState(), h = ctHtml();
      return {board, h, win: S.win.map(i => S.R[i].r.key), why: S.R.map(c => c.why), pairs: S.pairs, same: S.same,
              saved: JSON.parse(localStorage.getItem("scout.lol.cmp")).map(i => i.k)};
    })()""", {"rows": rows(), "match_tables": []})
    assert out["board"].count('data-lcmp="k-a"') == 2                # a Compare button on the table row and the phone card
    assert out["board"].count('data-k="k-a"') == 2                   # the row's own keys are unchanged
    assert out["win"] == ["k-a"]                                       # 66.0% beats 61.0%; the Lean and the started match are out
    assert out["why"][0] == "" and out["why"][1] == "" and out["why"][2].startswith("Lean Over")
    assert out["why"][3] == "This match has started."
    h = out["h"]
    assert "66.0%" in h and "61.0%" in h and "<h3>Alpha Over 4.5 Kills" in h and "Rule: the highest chance" in h
    # one prop on both platforms: the Over is graded (Underdog wins); Over chance at each line is the row's own p_over
    assert out["pairs"] == [{"i": 0, "j": 4, "G": "Over"}]
    assert "Underdog 4.5: <b>66.0%</b>" in h and "PrizePicks 5.5: <b>44.0%</b>" in h
    assert "<b>Underdog's 4.5</b> is the better line for the Over (lower line)." in h
    assert out["same"] == [[0, 1, 4]] and "Same match - results are related." in h and "no combined chance is shown" in h
    w = h.split('<div class="ctwarn">')[1].split("</div>")[0]
    assert "%" not in w
    assert out["saved"] == ["k-a", "k-b", "k-c", "k-e"]          # kept on this device; a match that started over 6 h ago drops out
    assert not re.search(r"not confirmed|provisional|unverified|\\bstale\\b|profit|ROI|guarantee", h, re.I)


@needs_node
def test_lol_tray_holds_six_names_a_pulled_line_and_adds_through_the_slip_rules(tmp_path):
    rs = [_lol_row(f"k{i}", f"P{i}", FUTURE, p_over=0.6) for i in range(7)]
    for i, r in enumerate(rs):
        r.update(team=f"T{i}", opponent=f"O{i}", match=f"T{i} vs O{i}", start=f"2099-01-0{i + 1}T18:00:00Z")
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render();
      const res = INPUT.rows.map(r => ctToggle(r.key));
      D = {rows: INPUT.rows.filter(r => r.key !== "k1").map(r => r.key === "k2" ? Object.assign({}, r, {key: "k2b", line: 5.5}) : r), match_tables: []};
      const S = ctState();
      ctClick({target: {id: "", closest: s => s === "[data-ctslip]" ? {dataset: {ctslip: "0"}} : null}});
      ctClick({target: {id: "", closest: s => s === "[data-ctslip]" ? {dataset: {ctslip: "3"}} : null}});
      return {full: res[6], why: S.R.map(c => c.why), legs: SLIP.legs.map(l => l.key)};
    })()""", {"rows": rs, "match_tables": []})
    assert out["full"]["text"] == "Compare holds 6 lines - remove one first."
    assert out["why"][1] == "This line is no longer on the board." and out["why"][2] == "The line moved: 4.5 → 5.5."
    assert out["legs"] == ["k0", "k3"]                                 # Add to slip from the tray: the slip's own rules


@needs_node
def test_lol_goblin_demon_lines_are_listed_not_ranked(tmp_path):
    """Owner 2026-10-25: alt lines pay differently, so a high chance alone can't make them 'the better line'."""
    a = _lol_row("k-a", "Alpha", FUTURE, p_over=0.62); a.update(grade="B")
    g = _lol_row("k-g", "Gamma", FUTURE, p_over=0.84); g.update(grade="A+", team="GEN", opponent="T1", line_type="Goblin",
                                                              platform="prizepicks", platform_label="PrizePicks")
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; render(); ctToggle("k-a"); ctToggle("k-g");
      const S = ctState(); return {win: S.win.map(i => S.R[i].r.key), why: S.R.map(c => c.why), h: ctHtml()};
    })()""", {"rows": [a, g], "match_tables": []})
    assert out["win"] == ["k-a"]                                   # 84% Goblin is not ranked over a 62% standard line
    assert out["why"][1].startswith("Goblin alt line") and "not ranked" in out["why"][1]
    assert "Only standard lines are ranked" in out["h"]
