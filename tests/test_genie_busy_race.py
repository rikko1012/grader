"""Bug sweep 2026-11-06 #13 / #14: the Genie stayed on 'Checking…' (card) / a skeleton (research drawer) for good when the
member switched side or line while a question was out: askHtml changed G.key but left busy=true, and genieAsk's finally
only cleared busy when the key still matched. The page's own Genie code is run with node against a controlled api()."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _slice(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


HUB_STUBS = r"""
const esc = s => String(s == null ? '' : s), num = x => typeof x === 'number' && isFinite(x), pct = x => String(x);
const sideWord = s => s, countOf = () => 0, when = s => String(s), $ = () => null;
const R = {me: {features: {genie: true}}}, H = {histSide: 'over', detail: 'A'};
const C = {tallies: () => ({l10: null}), siblings: () => [], chanceOf: () => null};
const RECS = {A: {sport: 'nba', scout: {projection: null}, player: {name: 'P'}, market: {period_label: 'G', stat_label: 'pts'},
                  offer: {platform: 'pp', offer_id: 'o1', revision: 1, line: 20.5, platform_label: 'PP'}}};
const rec = id => RECS[id], key = r => r.offer.offer_id;
let paints = 0; const paintBody = () => { paints++; };
"""

DRAWER_STUBS = r"""
const esc = s => String(s == null ? '' : s), $ = () => null;
class ApiError extends Error {}
const R = {me: {features: {genie: true}}};
const P = {side: 'over', rec: {key: 'recA', sport: 'nba', offer: {platform: 'pp', offer_id: 'o1', revision: 1}}};
let paints = 0; const paintDrawer = () => { paints++; };
"""

DRIVER = r"""
let calls = 0, pending = [];
const api = () => { calls++; return new Promise((res, rej) => pending.push({res, rej})); };
(async () => {
  const out = {};
  PAINT();
  const first = genieAsk('What is the projection?');
  out.busy_while_out = G.busy;
  SWITCH();                                        // the member switches side / line before the reply arrives
  PAINT();
  pending.shift().res({sentences: ['Late answer for the old line.'], quota: null});
  await first;
  out.busy_after = G.busy;
  out.stale_answer_shown = JSON.stringify(G.ans || null).includes('Late answer');
  out.html = PAINT();
  const second = genieAsk('How often did this line hit?');   // the Genie still answers on the new line
  out.second_call_sent = calls === 2;
  pending.shift().res({sentences: ['Fresh answer.'], quota: null});
  await second;
  out.busy_end = G.busy;
  out.answer = G.ans;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def _run(tmp_path, js):
    p = tmp_path / "genie.js"
    p.write_text(js, encoding="utf-8")
    r = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=60, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _check(out, busy_marker):
    assert out["busy_while_out"] is True
    assert out["busy_after"] is False                       # was true for good
    assert out["stale_answer_shown"] is False               # the old line's reply never lands on the new line
    assert busy_marker not in out["html"]
    assert out["second_call_sent"] is True                  # was: genieAsk returned at G.busy
    assert out["busy_end"] is False and out["answer"] == ["Fresh answer."]


@needs_node
def test_hub_card_genie_survives_a_side_switch_mid_question(tmp_path):
    src = (WEB / "upgrade-hub.js").read_text(encoding="utf-8")
    code = _slice(src, "const G={key:null", "function tabHtml(")
    js = (HUB_STUBS + code + DRIVER.replace("PAINT()", "askHtml(rec(H.detail))")
          .replace("SWITCH()", "H.histSide = 'under'"))
    _check(_run(tmp_path, js), "Checking this line")


@needs_node
def test_drawer_genie_survives_a_line_change_mid_question(tmp_path):
    src = (WEB / "upgrade.js").read_text(encoding="utf-8")
    code = _slice(src, "const G = { key: null", "function genieHtml(")
    js = (DRAWER_STUBS + code + DRIVER.replace("PAINT()", "askHtml(P.rec)")
          .replace("SWITCH()", "P.rec = Object.assign({}, P.rec, {key: 'recB'})"))
    _check(_run(tmp_path, js), "u-skel")
