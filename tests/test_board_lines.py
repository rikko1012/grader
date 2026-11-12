"""Board rows by prop (web/upgrade-lines.js): the real module run in node on plain records. One row per event + player + stat +
period, the ladder of every line, the line a row shows, the best line for a side, the filter match count, rows by player.
Also: the built stylesheet of the redesign is up to date with its sources."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
RUN = """
import {readFileSync} from 'node:fs';
import * as L from %s;
const inp = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const id = r => r.offer.offer_id;
const groups = L.groupProps(inp.sorted.map(i => inp.all[i]), inp.all, inp.ranked);
const players = L.groupPlayers(groups);
console.log(JSON.stringify({
  groups: groups.map(g => ({key: g.key, main: id(g.main), lines: g.lines.map(id), matched: g.matched.map(id), total: g.total, values: g.values, books: g.books,
    best: g.best ? id(g.best) : null, match: g.lines.filter(r => g.match.has(L.lineId(r))).map(id)})),
  players: players.map(p => ({key: p.key, props: p.props.map(g => g.main.market.stat + '/' + g.main.market.period), lines: p.lines})),
  bestOver: (L.bestFor(inp.all.filter(r => r.player.key === 'sga' && r.market.stat === 'points' && r.market.period === 'full_game'), 'over') || {offer: {}}).offer.offer_id,
  bestUnder: (L.bestFor(inp.all.filter(r => r.player.key === 'sga' && r.market.stat === 'points' && r.market.period === 'full_game'), 'under') || {offer: {}}).offer.offer_id,
  one: L.bestFor(inp.all.slice(0, 1), 'over'), none: L.bestFor(inp.all, null),
}));
"""


def rec(oid, player, stat, book, line, side=None, p=None, grade=None, period="full_game", lt="standard", over=True, under=True, price=(None, None), proj=30.0, hold=None, event="e1"):
    return {"sport": "nba", "event": {"id": event, "label": "A at B", "start_utc": "2030-01-01T00:00:00Z"}, "player": {"key": player, "name": player.upper(), "team": "A"},
            "market": {"stat": stat, "stat_label": stat.title(), "period": period, "period_label": period, "line_type": lt},
            "offer": {"platform": book, "platform_label": book.title(), "offer_id": oid, "line": line, "over": {"offered": over, "price": price[0]}, "under": {"offered": under, "price": price[1]}},
            "scout": {"side": side, "p_side": p, "grade": grade, "projection": proj, "hold": hold}}


def board():
    return [
        rec("bv-27", "sga", "points", "bovada", 27.5, price=(1.5, 2.5), hold={"reason": "x"}),
        rec("bv-33", "sga", "points", "bovada", 33.5, "under", 0.505, "Lean", price=(2.1, 1.7)),
        rec("bv-34", "sga", "points", "bovada", 34.5, "under", 0.535, "C", price=(2.3, 1.6)),
        rec("br-33", "sga", "points", "betrivers", 33.5, "under", 0.505, "Lean", price=(2.0, 1.8)),
        rec("pm-39", "sga", "points", "polymarket", 39.5, under=False, price=(4.0, None)),
        rec("ud-32", "sga", "points", "underdog", 32.5, "over", 0.52, None, proj=32.9),
        rec("pp-33", "sga", "points", "prizepicks", 33.0, "under", 0.51, None, proj=32.9),
        rec("pp-29g", "sga", "points", "prizepicks", 29.5, "over", 0.66, "B+", lt="goblin", proj=32.9),
        rec("ud-a", "sga", "assists", "underdog", 6.5, "over", 0.58, "B"),
        rec("ud-q1", "sga", "points", "underdog", 8.5, "over", 0.56, "B-", period="q1"),
        rec("ud-j", "jdub", "points", "underdog", 20.5, "under", 0.6, "B"),
        rec("pp-j", "jdub", "points", "prizepicks", 20.5, "under", 0.59, "B"),
    ]


def run(tmp_path, sorted_ids, ranked=False):
    all_ = board()
    idx = {r["offer"]["offer_id"]: i for i, r in enumerate(all_)}
    js = tmp_path / "run.mjs"
    js.write_text(RUN % json.dumps((ROOT / "web" / "upgrade-lines.js").as_uri()), encoding="utf-8")
    data = tmp_path / "in.json"
    data.write_text(json.dumps({"all": all_, "sorted": [idx[i] for i in sorted_ids], "ranked": ranked}), encoding="utf-8")
    out = subprocess.run([NODE, str(js), str(data)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


EVERY = ["bv-27", "bv-33", "bv-34", "br-33", "pm-39", "ud-32", "pp-33", "pp-29g", "ud-a", "ud-q1", "ud-j", "pp-j"]
pytestmark = pytest.mark.skipif(not NODE, reason="node is not installed")


def test_one_row_per_player_stat_period_with_every_line_in_the_ladder(tmp_path):
    out = run(tmp_path, EVERY)
    groups = out["groups"]
    assert [g["key"].split("|", 2)[2] for g in groups] == ["sga|points|full_game", "sga|assists|full_game", "sga|points|q1", "jdub|points|full_game"]
    pts = groups[0]
    assert pts["total"] == 8 and pts["values"] == 7 and pts["books"] == 5
    # the ladder: by line, then by app name at the same line
    assert pts["lines"] == ["bv-27", "pp-29g", "ud-32", "pp-33", "br-33", "bv-33", "bv-34", "pm-39"]
    assert sorted(pts["match"]) == sorted(pts["lines"])                 # no filter: every line is a match
    assert sum(g["total"] for g in groups) == len(EVERY)                # no line is lost or counted twice


def test_the_row_shows_a_graded_slip_line_when_the_sort_does_not_rank_lines(tmp_path):
    pts = run(tmp_path, EVERY)["groups"][0]
    # graded lines first (pp-29g B+, bv-34 C, bv-33 / br-33 Lean), then a PrizePicks / Underdog line, then a standard line: bv-34 and
    # the Leans are graded but not slip lines, pp-29g is a goblin slip line with a letter -> it leads
    assert pts["main"] == "pp-29g"
    jd = run(tmp_path, EVERY)["groups"][3]
    assert jd["main"] == "ud-j" and jd["total"] == 2 and jd["values"] == 1 and jd["books"] == 2


def test_a_ranked_sort_shows_the_line_that_ranked_the_row(tmp_path):
    # sorted by chance, high to low: the row leads with the line the sort put first
    order = ["pp-29g", "ud-j", "pp-j", "ud-a", "ud-q1", "bv-34", "ud-32", "pp-33", "bv-33", "br-33"]
    out = run(tmp_path, order, ranked=True)
    assert [g["main"] for g in out["groups"]] == ["pp-29g", "ud-j", "ud-a", "ud-q1"]
    assert out["groups"][0]["matched"] == ["pp-29g", "bv-34", "ud-32", "pp-33", "bv-33", "br-33"]


def test_a_filtered_row_keeps_its_whole_ladder_and_counts_the_matches(tmp_path):
    # the filter matched two of the eight Points lines (say Platform = Bovada, Side = Under)
    out = run(tmp_path, ["bv-33", "bv-34", "ud-j"])
    pts = out["groups"][0]
    assert pts["matched"] == ["bv-33", "bv-34"] and pts["total"] == 8 and len(pts["lines"]) == 8
    assert pts["match"] == ["bv-33", "bv-34"]                           # the ladder marks exactly the matched lines
    assert pts["main"] in ("bv-33", "bv-34")                            # the row never leads with a line the filter hid
    assert len(out["groups"]) == 2                                      # props with no matched line are not listed


def test_best_line_for_a_side(tmp_path):
    out = run(tmp_path, EVERY)
    # Over: the lowest line whose app offers the Over; Under: the highest line whose app offers the Under (Polymarket 39.5 lists no Under)
    assert out["bestOver"] == "bv-27" and out["bestUnder"] == "bv-34"
    assert out["one"] is None and out["none"] is None
    # the row's mark follows the row line's Scout side (pp-29g leans Over)
    assert out["groups"][0]["best"] == "bv-27"
    # same line on two apps: nothing is priced on either, the first stays (no invented tie-break)
    assert out["groups"][3]["best"] in ("ud-j", "pp-j")


def test_rows_by_player_group_each_players_props_by_stat(tmp_path):
    out = run(tmp_path, EVERY)
    assert [p["key"].split("|")[-1] for p in out["players"]] == ["sga", "jdub"]
    assert out["players"][0]["props"] == ["points/full_game", "points/q1", "assists/full_game"]     # one stat together, then the next
    assert out["players"][0]["lines"] == 10 and out["players"][1]["lines"] == 2


def test_the_built_stylesheet_is_current():
    """upgrade-next-base.css is built from hub.css + the grade panel sheet: a change to either must be rebuilt (scripts/build_next_css.py)."""
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_next_css.py"), "--check"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, "run: python scripts/build_next_css.py"
    css = (ROOT / "web" / "upgrade-next-base.css").read_text(encoding="utf-8")
    import re
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![^{}]*\{)", re.sub(r"--u-[\w-]+:[^;}]+", "", css)), "a literal colour is left in the built sheet"
    assert "blur(" not in css and "gradient(" not in css.replace("var(--hit", "")
