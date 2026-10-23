"""End-to-end consistency check (night/consistency, 2026-10-23): one available offer must read the same recommended side,
grade (display gd-1), stake, chance (and its denominator), line, scope, platform, player, match and model version on the
BOARD row, the player CARD (/api/grade with the board's own player / line / prop / maps / team / opponent / book / start,
as the page's board click sends them), the MATCH table (/api/match_compare, as the card's Match players panel asks), and
the SLIP (the page's own legFrom on the board row, on the card and on a Studio leg - run in node from web/index.html).

Research tool, read only. Bounded: <= --max-cards card requests (default 40, hard cap 40), --sleep seconds between calls
(default 3 s = 20/min, under the 60/min grade limit), one match table per match / platform / maps.

  python scripts/consistency_check.py --base https://cs2-scout.onrender.com --token-file <lq.py dir>   (production)
  python scripts/consistency_check.py --base http://127.0.0.1:8080                                    (a local server)

Production reads send X-Load-Test (the owner's audit header) read from lq.py's T; it is never printed. Output: a mismatch
table on stdout and the full evidence as JSON (--out).
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[2], "utf8");
const main = html.split("<script>")[1].split("</script>")[0];
function stub(name) {
  const store = {innerHTML: "", textContent: "", value: "", hidden: false, checked: false, dataset: {}, style: {}, children: [], options: []};
  const f = function () { return stub(name + "()"); };
  return new Proxy(f, {
    get(t, k) {
      if (k in store) return store[k];
      if (k === Symbol.iterator) return function* () {};
      if (k === Symbol.toPrimitive) return () => "";
      if (k === "classList") return {add() {}, remove() {}, toggle() {}, contains() { return false; }};
      if (k === "getBoundingClientRect") return () => ({left: 0, right: 0, width: 0, top: 0, bottom: 0});
      if (k === "querySelectorAll") return () => [];
      if (k === "querySelector") return () => null;
      if (k === "closest") return () => stub("closest");
      if (k === "parentElement") return null;
      if (k === "then") return undefined;
      return stub(name + "." + String(k));
    },
    set(t, k, v) { store[k] = v; return true; },
    apply() { return stub(name + "()"); },
  });
}
const els = {}, ls = {};
const ctx = {
  console: {log() {}, warn() {}, error() {}, info() {}}, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  document: {querySelector: s => (els[s] = els[s] || stub(s)), querySelectorAll: () => [], addEventListener() {}, createElement: () => stub("el"),
             body: stub("body"), activeElement: null, visibilityState: "visible"},
  localStorage: {getItem: k => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }},
  matchMedia: () => ({matches: false}), fetch: async () => ({ok: false, status: 599, json: async () => ({})}),
  history: {replaceState() {}, pushState() {}, back() {}, state: null}, location: {search: "", hash: "", pathname: "/", origin: "https://x", href: "https://x/"},
  IntersectionObserver: function () { return {observe() {}}; }, MutationObserver: function () { return {observe() {}}; },
  requestAnimationFrame: () => 0, innerWidth: 1400, navigator: {clipboard: {writeText: async () => {}}}, crypto: {randomUUID: () => "u"},
  URL, URLSearchParams, Date, Math, JSON, Promise, Set, Map, Array, Object, String, Number, RegExp, isNaN, Error,
  addEventListener() {}, scrollTo() {},
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(main, ctx, {filename: "index.html"});
ctx.INPUT = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = vm.runInContext(`(() => INPUT.map(x => {
  const r = {};
  try { r.board = legFrom(boardCard(x.row)); r.board_key = slipKeyLeg(r.board); r.row_key = slipKeyRow(x.row); } catch (e) { r.board_err = String(e); }
  try { r.studio = x.studio ? legFrom(studioCard(x.studio)) : null; if (r.studio) r.studio_key = slipKeyLeg(r.studio); } catch (e) { r.studio_err = String(e); }
  if (x.card) {
    try { const d = JSON.parse(JSON.stringify(x.card)); d.book = x.book; d.asked_side = x.row.side; if (x.row.start) d.line_start = x.row.start;
          r.card = legFrom(d); r.card_key = slipKeyLeg(r.card); } catch (e) { r.card_err = String(e); }
    try {
      const d = JSON.parse(JSON.stringify(x.card)); d.book = x.book; d.asked_side = x.row.side; try { render(d); } catch (e) { r.render_err = String(e); }
      const h = document.querySelector("#out").innerHTML || "";
      const h3 = (/<h3>([^<]*)<\\/h3>/.exec(h) || [])[1] || "";
      const badge = (/<div class="badge [^"]*" title="[^"]*">([^<]*)<\\/div>/.exec(h) || [])[1] || "";
      const pickSide = /<div class="side pick"><div class="k">Over/.test(h) ? "More" : /<div class="side pick"><div class="k">Under/.test(h) ? "Less" : null;
      const label = (/<div class="(?:sidelabel|ysel)[^"]*">([^<]*)</.exec(h) || [])[1] || "";
      r.view = {headline: h3, badge, pick_panel: pickSide, label};
    } catch (e) { r.view_err = String(e); }
  }
  return r;
}))()`, ctx);
console.log(JSON.stringify(out));
"""


def http_json(base, path, token=None, timeout=200):
    h = {"X-Load-Test": token} if token else {}
    req = urllib.request.Request(base.rstrip("/") + path, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"error": str(e)}


def grade_type(r):
    t = "promo" if r.get("odds_type") == "promo" else (r.get("line_type") or r.get("odds_type") or "standard")
    return t if t in ("goblin", "demon") else "standard"


def card_query(r):
    """/api/grade exactly as the page's board click sends it (boardClick -> gradeNow -> the form submit)."""
    q = {"player": r.get("nick") or r.get("player"), "line": r["line"], "prop": r["prop"], "line_type": grade_type(r),
         "maps": r.get("scope") or "12"}
    if r.get("team"):
        q["team"] = r["team"]
    if r.get("opponent"):
        q["opponent"] = r["opponent"]
    q["book"] = r.get("book") or "underdog"
    if r.get("opponent") and r.get("start"):
        q["start"] = r["start"]
    return q


def mc_query(d, book, scope):
    """/api/match_compare exactly as the card's Match players panel asks (mcFetch)."""
    nm, pb = d.get("next_match") or {}, d.get("player_block") or {}
    return {"team_slug": nm.get("team_slug") or "", "opp_slug": nm.get("opponent_slug") or "",
            "team": pb.get("playing_for") or pb.get("current_team") or "", "opponent": nm.get("opponent") or "",
            "start": nm.get("start") or "", "maps": scope, "book": book, "match": nm.get("slug") or ""}


def _ts(x):
    from datetime import datetime
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()


def mgrade(x):
    return None if not x else (x.get("model_grade") or x.get("grade"))


def denom(p, mo, side):
    """Which probability the shown chance is: P(side | no push) or the outright P(side) (model_outcomes)."""
    if p is None or not mo or side not in ("More", "Less"):
        return None
    o, u, pu = mo.get("over"), mo.get("under"), mo.get("push") or 0.0
    if o is None or u is None:
        return None
    s = o if side == "More" else u
    tags = []
    if o + u > 0 and abs(p - s / (o + u)) <= 0.0006:
        tags.append("no_push")
    if abs(p - s) <= 0.0006:
        tags.append("outright")
    return "+".join(tags) or "neither"


def sample_rows(rows, n, rnd):
    """Strata first (kills / HS, each maps scope, whole-number lines, same-match capped, a 0U letter grade, side not
    offered, BO3.gg source), then random rows to n."""
    picked, seen = [], set()

    def take(pred, k=1):
        c = [r for r in rows if pred(r) and r["id"] not in seen]
        rnd.shuffle(c)
        for r in c[:k]:
            seen.add(r["id"]); picked.append(r)
    for prop in ("kills", "hs"):
        for sc in ("1", "12", "123"):
            take(lambda r: r.get("prop") == prop and (r.get("scope") or "12") == sc, 2)
    take(lambda r: float(r["line"]) % 1 == 0, 3)
    take(lambda r: r.get("cap_n") and (r.get("units_full") or 0) > (r.get("units") or 0), 2)
    take(lambda r: r.get("side") and not (r.get("units") or 0) and mgrade(r) not in (None, "LEAN"), 2)
    take(lambda r: r.get("unavailable"), 1)
    take(lambda r: r.get("source") and r.get("source") != "HLTV", 2)
    take(lambda r: (r.get("units") or 0) > 0, 3)
    take(lambda r: r.get("side") == "Less", 1)
    take(lambda r: True, max(0, n - len(picked)))
    return picked[:n]


def run_node(cases, html=None):
    node = shutil.which("node")
    if not node:
        return None
    td = Path(tempfile.mkdtemp(prefix="cc_"))
    (td / "h.js").write_text(HARNESS, encoding="utf-8")
    (td / "in.json").write_text(json.dumps(cases), encoding="utf-8")
    p = subprocess.run([node, str(td / "h.js"), str(html or ROOT / "web" / "index.html"), str(td / "in.json")],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-2000:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def compare(case, legs, mc_entry):
    """-> list of (field, surface, expected (board), got)."""
    r, d = case["row"], case.get("card")
    out = []
    bside, bgrade, bunits, bp = r.get("side"), mgrade(r), r.get("units") or 0.0, r.get("p")
    exp = {"side": bside, "grade": bgrade, "units": round(float(bunits), 2), "p": bp, "line": float(r["line"]),
           "scope": r.get("scope") or "12", "book": r.get("book") or "underdog", "player_id": r.get("player_id"),
           "match": r.get("match_slug"), "model": r.get("graded_model"), "available": not r.get("unavailable"),
           "match_id": r.get("match_id"), "start": r.get("start")}
    exp["denom"] = denom(bp, r.get("model_outcomes"), bside)

    def chk(surface, got):
        for k, v in got.items():
            if k not in exp or v is None and k in ("model", "denom", "player_id", "match", "match_id"):
                continue
            e = exp[k]
            if k == "p" and e is not None and v is not None:
                if abs(float(e) - float(v)) > 0.0005:
                    out.append((k, surface, e, v))
            elif k == "units":
                if round(float(v or 0), 2) != e:
                    out.append((k, surface, e, v))
            elif k == "line":
                if float(v) != e:
                    out.append((k, surface, e, v))
            elif k == "start":
                if not v or not e or abs(_ts(v) - _ts(e)) > 60:
                    out.append((k, surface, e, v))
            elif k == "player_id":
                if str(v) != str(e):
                    out.append((k, surface, e, v))
            elif v != e:
                out.append((k, surface, e, v))
    if d is not None and not d.get("error"):
        v, g, pb, nm = d.get("verdict") or {}, d.get("grade") or {}, d.get("player_block") or {}, d.get("next_match") or {}
        chk("card", {"side": v.get("side"), "grade": mgrade(v), "units": v.get("units") or 0.0, "p": v.get("p"),
                     "line": g.get("line"), "scope": g.get("scope") or "12", "player_id": (pb.get("player") or {}).get("id"),
                     "match": nm.get("slug"), "model": v.get("graded_model"), "match_id": v.get("match_id"),
                     "available": not (v.get("side_unavailable") or v.get("unavailable")),
                     "denom": denom(v.get("p"), g.get("probs_raw"), v.get("side"))})
        if v.get("graded_model") is None:
            out.append(("model", "card", exp["model"], "(card sends no model version)"))
    elif d is not None:
        out.append(("card", "card", "a card", d.get("error")))
    if mc_entry is not None:
        if mc_entry == "missing":
            out.append(("row", "match", "listed", "player/line not in the match table"))
        else:
            chk("match", {"side": mc_entry.get("side"), "grade": mgrade(mc_entry), "units": mc_entry.get("units") or 0.0,
                          "p": mc_entry.get("p"), "line": mc_entry.get("line"), "available": not mc_entry.get("unavailable"),
                          "model": mc_entry.get("graded_model"), "match_id": mc_entry.get("match_id"),
                          "denom": denom(mc_entry.get("p"), mc_entry.get("model_outcomes"), mc_entry.get("side")) if mc_entry.get("model_outcomes") else None})
    for surf in ("board", "card", "studio"):
        lg = (legs or {}).get(surf)
        if not lg:
            continue
        chk("slip(" + surf + ")", {"side": lg.get("s"), "grade": lg.get("g"), "units": lg.get("u") or 0.0, "p": lg.get("pc"),
                                   "line": lg.get("l"), "scope": lg.get("sc"), "book": lg.get("bk"),
                                   "player_id": lg.get("pid") or None, "match_id": lg.get("mid") or None,
                                   "start": lg.get("st") or None})
    keys = {surf: (legs or {}).get(surf + "_key") for surf in ("board", "card", "studio", "row") if (legs or {}).get(surf + "_key")}
    if len(set(keys.values())) > 1:
        out.append(("slip key", "slip (board/card/studio)", "one pick", keys))
    vw = (legs or {}).get("view")
    if vw and d is not None and not d.get("error"):
        v = d.get("verdict") or {}
        if v.get("side") and vw.get("pick_panel") != v.get("side"):
            out.append(("side", "card view (pick panel)", v.get("side"), vw.get("pick_panel")))
        if v.get("side") and r.get("side") and v.get("side") != r.get("side") and not str(vw.get("label", "")).startswith("Your side"):
            out.append(("label", "card view (your side)", "Your side: ...", vw.get("label") or "(no label)"))
        sd = {"More": "Over", "Less": "Under"}.get(v.get("side"))
        if sd and sd not in vw.get("headline", ""):
            out.append(("side", "card view (headline)", sd, vw.get("headline")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://cs2-scout.onrender.com")
    ap.add_argument("--token-file", default=None, help="directory holding lq.py (T = the audit header); never printed")
    ap.add_argument("--max-cards", type=int, default=40)
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--out", default=None)
    ap.add_argument("--boards", default=None, help="read the boards from this directory (board_<book>.json) instead")
    ap.add_argument("--html", default=None, help="the page to run the slip / card code from (default: this checkout's)")
    ap.add_argument("--simulate-server", action="store_true", help="with --replay: add what this branch's server adds to a saved "
                    "card / match cell (verdict.graded_model, the exact board row's match_id, the cell's model_outcomes / version / id)")
    ap.add_argument("--replay", default=None, help="re-check a saved --out file with this checkout's page code (no requests)")
    a = ap.parse_args()
    if a.replay:
        return replay(a)
    a.max_cards = min(a.max_cards, 40)
    tok = None
    if a.token_file:
        sys.path.insert(0, a.token_file)
        import lq
        tok = lq.T
    rnd = random.Random(a.seed)
    boards = {}
    for b in ("prizepicks", "underdog"):
        if a.boards:
            boards[b] = json.load(open(os.path.join(a.boards, f"board_{b}.json")))
        else:
            st, boards[b] = http_json(a.base, "/api/board?book=" + b, tok)
            time.sleep(a.sleep)
    rows_all = {b: [r for r in (boards[b].get("rows") or []) if r.get("side")] for b in boards}
    # board-only checks over every row (no requests): the studio leg letter, the denominator of whole-number lines
    import slip_builder
    board_only = []
    for b, rows in rows_all.items():
        for r in rows:
            if (r.get("units") or 0) > 0 and r.get("grade") != mgrade(r):
                board_only.append(("grade", "studio leg (pick_record.grade)", mgrade(r), r.get("grade"), r["id"]))
            dn = denom(r.get("p"), r.get("model_outcomes"), r.get("side"))
            if dn not in (None, "no_push", "no_push+outright"):
                board_only.append(("denom", "board", "no_push", dn, r["id"]))
    half = a.max_cards // 2
    sample = sample_rows(rows_all["prizepicks"], half, rnd) + sample_rows(rows_all["underdog"], a.max_cards - half, rnd)
    cases, mcs = [], {}
    for r in sample:
        b = r.get("book") or "underdog"
        st, d = http_json(a.base, "/api/grade?" + urllib.parse.urlencode(card_query(r)), tok)
        if st != 200:
            d = {"error": f"HTTP {st}: {d.get('error')}"}
        time.sleep(a.sleep)
        studio = slip_builder.pick_record(r) if (r.get("units") or 0) > 0 and not r.get("unavailable") else None
        cases.append({"row": r, "book": b, "card": d, "studio": studio})
        if not d.get("error"):
            q = mc_query(d, b, r.get("scope") or "12")
            k = urllib.parse.urlencode(q)
            if k not in mcs and len(mcs) < 25:
                st2, mcs[k] = http_json(a.base, "/api/match_compare?" + k, tok)
                time.sleep(a.sleep)
            cases[-1]["mc_key"] = k
    return evaluate(a, cases, mcs, board_only)


def replay(a):
    saved = json.load(open(a.replay, encoding="utf-8"))
    cases = saved["cases"]
    for c in cases:
        for k in ("mismatch", "legs", "mc_entry"):
            c.pop(k, None)
        if a.simulate_server and not (c.get("card") or {}).get("error"):
            v, r = c["card"].setdefault("verdict", {}), c["row"]
            if v.get("side") and not v.get("kept_from_board"):
                v.setdefault("graded_model", r.get("graded_model"))   # the running model = the board's (same tag on both)
            v.setdefault("match_id", r.get("match_id"))              # server.card_identity: the exact board row's id
    if a.simulate_server:
        brows = {}
        for c in cases:
            brows[(c["row"].get("book"), (c["row"].get("nick") or "").lower(), c["row"]["prop"], float(c["row"]["line"]))] = c["row"]
        for k, mc in saved["match_tables"].items():
            bk = urllib.parse.parse_qs(k).get("book", ["underdog"])[0]
            for t in mc.get("teams") or []:
                for e in t["players"]:
                    for pr in ("kills", "hs"):
                        cell = e.get(pr) or {}
                        r = brows.get((bk, e["nick"].lower(), pr, float(cell["line"]))) if cell.get("line") is not None else None
                        if r:
                            for f, g in (("model_outcomes", "model_outcomes"), ("graded_model", "graded_model"), ("match_id", "match_id")):
                                cell.setdefault(f, r.get(g))
    return evaluate(a, cases, saved["match_tables"], [tuple(x) for x in saved.get("board_only") or []])


def evaluate(a, cases, mcs, board_only):
    legs = run_node([{"row": c["row"], "book": c["book"], "card": c["card"] if not c["card"].get("error") else None,
                      "studio": c["studio"]} for c in cases], a.html) or [{} for _ in cases]
    table = []
    for c, lg in zip(cases, legs):
        r = c["row"]
        mce = None
        mc = mcs.get(c.get("mc_key"))
        if mc and mc.get("teams"):
            nick = (r.get("nick") or r.get("player") or "").lower()
            ent = next((e for t in mc["teams"] for e in t["players"] if e["nick"].lower() == nick), None)
            cell = (ent or {}).get(r["prop"])
            mce = cell if cell and cell.get("line") is not None and float(cell["line"]) == float(r["line"]) else "missing"
            if cell and cell.get("line") is not None and float(cell["line"]) != float(r["line"]):
                mce = dict(cell, _other_line=True)
        if isinstance(mce, dict) and mce.get("_other_line"):
            mce = None                                  # the table shows his standard line; this row is another line
        mm = compare(c, lg, mce)
        c["mismatch"] = mm
        c["legs"] = lg
        c["mc_entry"] = mce
        tag = f"{r['book'][:2]} {r.get('nick') or r['player']} {r['prop']} M{r.get('scope') or '12'} {r['line']}"
        v = (c["card"] or {}).get("verdict") or {}
        g = (c["card"] or {}).get("grade") or {}
        table.append({"row": tag, "id": r["id"], "board": f"{r.get('side')} {mgrade(r)} {r.get('units')}U {r.get('p')}",
                      "card": f"{v.get('side')} {mgrade(v)} {v.get('units')}U {v.get('p')} [{g.get('projection_source')}]",
                      "board_graded_at": r.get("graded_at"), "source": r.get("source"), "mismatch": mm})
    n_bad = sum(1 for t in table if t["mismatch"])
    print(f"cards {len(cases)} · match tables {len(mcs)} · rows with any mismatch {n_bad}")
    for t in table:
        flag = "MISMATCH" if t["mismatch"] else "ok"
        print(f"{flag:8} {t['row']:<42} board {t['board']:<28} card {t['card']}")
        for m in t["mismatch"]:
            print(f"           {m[0]:<9} {m[1]:<24} board={m[2]!r} got={m[3]!r}")
    print(f"board-only checks: {len(board_only)} issue(s)")
    for x in board_only[:30]:
        print("          ", x)
    if a.out:
        json.dump({"base": a.base, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "table": table, "board_only": board_only,
                   "cases": cases, "match_tables": mcs}, open(a.out, "w", encoding="utf-8"), default=str)
    return 1 if n_bad or board_only else 0


if __name__ == "__main__":
    sys.exit(main())
