"""2026-10-21 slips U3 (LoL): "Send to my slip" on a suggested slip goes through Add to slip's own rules (one platform,
one pick per match and per player, the platform's leg limit, started matches refused); suggested slips show "Est. chance
all hit" from the server's model_chance_all_hit; the member's own slip follows the CS2 copy rule - a leg whose match has
started, or whose line moved or was pulled at recheck, is never copied (refused, and each such pick is named).
Stakes stay 0U. The page's own script runs in node against the stub DOM (tests/test_audit_h_web.py)."""
from tests.test_audit_h_web import FUTURE, PAST, _lol_row, needs_node, run

SOON = "2099-01-02T18:00:00Z"


def _row(key, player, match, start=FUTURE, p_over=0.6, platform="underdog"):
    r = _lol_row(key, player, start, p_over=p_over)
    r.update(match=match, team=player + "-team", opponent="Opp-" + match, grade="B+")
    if platform == "prizepicks":
        r.update(platform="prizepicks", platform_label="PrizePicks")
    return r


def _board():
    rows = [_row("k-a", "Alpha", "M1"), _row("k-b", "Bravo", "M2", p_over=0.7), _row("k-c", "Charlie", "M3", start=SOON),
            _row("k-s", "Started", "M4", start=PAST), _row("k-a2", "Alpha2", "M1"),
            _row("k-p", "Papa", "M5", platform="prizepicks")]
    slips = {"Underdog": {"recommended": [
        {"size": 2, "legs": [{"key": "k-a", "side": "over", "line": 4.5}, {"key": "k-b", "side": "over", "line": 4.5}],
         "model_chance_all_hit": 0.42, "entry_payout": None},
        {"size": 3, "legs": [{"key": "k-a2", "side": "over", "line": 4.5}, {"key": "k-c", "side": "over", "line": 4.5},
                             {"key": "k-s", "side": "over", "line": 4.5}], "model_chance_all_hit": 0.2163},
        {"size": 2, "legs": [{"key": "k-b", "side": "over", "line": 3.5}, {"key": "gone", "side": "over", "line": 4.5}],
         "model_chance_all_hit": 0.3}], "why_none": []},
        "PrizePicks": {"recommended": [{"size": 2, "legs": [{"key": "k-p", "side": "over", "line": 4.5}],
                                        "model_chance_all_hit": 0.6}], "why_none": []}}
    return {"rows": rows, "match_tables": [], "slips": slips, "stake_note": "Stakes are 0U."}


@needs_node
def test_suggested_slip_shows_chance_and_sends_through_the_add_rules(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; F.view = "slip"; render();
      const view = $("#app").innerHTML;
      sendSuggested("Underdog|0");
      const s1 = SLIP.legs.map(l => l.key), v1 = $("#app").innerHTML;
      sendSuggested("Underdog|1");                  // Alpha2 shares Alpha's match; Started has started; Charlie fits
      const s2 = SLIP.legs.map(l => l.key), v2 = $("#app").innerHTML;
      sendSuggested("PrizePicks|0");                // another platform: nothing added
      const s3 = SLIP.legs.map(l => l.key), v3 = $("#app").innerHTML;
      SLIP.legs = []; sendSuggested("Underdog|2");  // the suggested line is no longer the board's line / the line was pulled
      const s4 = SLIP.legs.map(l => l.key), v4 = $("#app").innerHTML;
      return {view, s1, v1, s2, v2, s3, v3, s4, v4, stored: localStorage.getItem("scout.lol.slip")};
    })()""", _board())
    assert "Est. chance all hit <b>42.0%</b>" in out["view"] and "Est. chance all hit <b>21.6%</b>" in out["view"]
    assert out["view"].count('data-send="') == 4 and 'data-send="Underdog|0"' in out["view"] and "Send to my slip" in out["view"]
    assert out["s1"] == ["k-a", "k-b"] and "Sent 2 picks to your Underdog slip." in out["v1"]
    assert out["s2"] == ["k-a", "k-b", "k-c"]
    assert "Sent 1 pick to your Underdog slip." in out["v2"] and "Alpha2: Your slip already has a pick from this match" in out["v2"]
    assert "Started: This match has started" in out["v2"]
    assert out["s3"] == out["s2"] and "Nothing sent - Papa: Your slip is Underdog" in out["v3"] and "one platform per slip" in out["v3"]
    assert out["s4"] == [] and "the line moved: 3.5 → 4.5" in out["v4"] and "a pick: this line is no longer on the board" in out["v4"]
    assert '"k-c"' in out["stored"]                                # saved like Add to slip


@needs_node
def test_send_respects_the_platform_leg_limit(tmp_path):
    d = _board()
    d["rows"] += [_row(f"k-{i}", f"P{i}", f"X{i}") for i in range(8)]
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; F.view = "slip";
      for (let i = 0; i < 7; i++) SLIP.legs.push(legOf(D.rows.find(r => r.key === "k-" + i)));
      sendSuggested("Underdog|0"); return {n: SLIP.legs.length, html: $("#app").innerHTML};
    })()""", d)
    assert out["n"] == 8 and "Underdog slips hold up to 8 picks" in out["html"]


@needs_node
def test_own_slip_never_copies_a_started_moved_or_pulled_leg(tmp_path):
    d = _board()
    moved = dict(d["rows"][1], key="k-b-new", line=5.5)        # Bravo's line moved: a new key on the board
    after = {"rows": [d["rows"][0], moved, d["rows"][2]], "match_tables": [], "slips": {}, "stake_note": "0U"}
    out = run(tmp_path, "lol.html", """(async () => {
      D = INPUT.d; F.view = "slip";
      let copied = null; navigator.clipboard.writeText = async t => { copied = t; };
      SLIP.legs = ["k-a", "k-b", "k-c"].map(k => legOf(D.rows.find(r => r.key === k)));
      const gone = Object.assign({}, legOf(D.rows.find(r => r.key === "k-c")), {key: "k-gone", player: "Ghost", match_key: "x|y"});
      SLIP.legs.push(gone);
      const old = Object.assign({}, gone, {key: "k-old", player: "Oldie", start: "2020-01-01T00:00:00Z"});
      SLIP.legs.push(old);
      render(); const before = $("#app").innerHTML;
      fetch = async () => ({ok: true, status: 200, json: async () => INPUT.after});
      const r1 = await copySlip(), msg1 = $("#slipmsg").textContent, c1 = copied, html1 = $("#app").innerHTML;
      SLIP.legs = SLIP.legs.filter(l => l.key === "k-a" || l.key === "k-c");
      const r2 = await copySlip(), msg2 = $("#slipmsg").textContent;
      return {before, r1, msg1, c1, html1, r2, msg2, copied};
    })()""", {"d": d, "after": after})
    assert "This match has started - remove it." in out["before"]          # shown before any recheck
    assert out["r1"] is False and out["c1"] is None
    m = out["msg1"]
    assert m.startswith("Nothing copied - ") and "Bravo (Kills, Maps 1-2): The line moved: 4.5 → 5.5" in m
    assert "Ghost (Kills, Maps 1-2): This line was pulled - it is no longer on the board" in m
    assert "Oldie (Kills, Maps 1-2): This match has started" in m and "Alpha" not in m and "Charlie" not in m
    assert m.endswith("Remove these picks, then copy.")
    assert out["r2"] and out["copied"].startswith("Underdog - 2 picks\n") and "Alpha" in out["copied"] and "Charlie" in out["copied"]
    assert out["msg2"] == "Copied - every line rechecked just now."


@needs_node
def test_copy_pick_on_a_started_leg_is_refused_even_if_the_row_is_gone(tmp_path):
    out = run(tmp_path, "lol.html", """(() => {
      D = INPUT; const l = legOf(D.rows.find(r => r.key === "k-s"));
      D = {rows: [], match_tables: []};
      return [legStatus(l), legStatus(Object.assign({}, l, {start: "2099-01-01T00:00:00Z"}))];
    })()""", _board())
    assert out[0] == {"ok": False, "text": "This match has started."}
    assert out[1]["text"] == "This line was pulled - it is no longer on the board."
