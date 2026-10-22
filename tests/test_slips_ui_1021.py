"""2026-10-21 slips bug hunt + upgrades (web/index.html only - nothing here changes a grade, chance, projection or stake).
The page's own script runs in node (the tests.test_audit_h_web harness, which keeps event listeners). Bug tests reuse the
reproductions in the slips audit (r1, r2, r3, r5, r7) and the UI review (9, 10, 11). Every row, name and number below is a
LABELLED TEST FIXTURE."""
import json

from tests.test_audit_h_web import run
from tests.test_audit13_site_ui import needs_node

# flush the page's async chains (fetch -> json -> ask -> copy)
FLUSH = "for (let i = 0; i < 400; i++) await Promise.resolve();"
COPY_CLICK = 'LIS["#slip"].find(x => x[0] === "click")[1]({target: {closest: s => s === "#slipcopy" ? {} : null}});'
LEG = """const fut = new Date(Date.now() + 3600e3).toISOString(), past = new Date(Date.now() - 3600e3).toISOString();
  const leg = (p, o) => Object.assign({p, bk: "underdog", t: "kills", sc: "12", l: 30.5, s: "More", g: "B", u: 1, pc: 0.6, kg: "", st: fut,
                                        m: "m-" + p, tm: "T" + p, vs: "O" + p, now: {status: "verified"}}, o || {});"""


# ---------------------------------------------------------------- bug 1: a stale Underdog FEED never copies
@needs_node
def test_bug1_stale_feed_line_check_is_refused(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], C = [], P = [];
      toast = m => T.push(m); copy = t => C.push(t);
      fetch = async (u, o) => { if (String(u).includes("slip_log")) { P.push(o.body); return {ok: true, status: 200, json: async () => ({})}; }
        return {ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: "stale", note: "Underdog feed not fresh - refresh before copying"} : {})}; };
      """ + LEG + """
      store.set("cs2s_slip", [leg("a"), leg("b")]);
      """ + COPY_CLICK + FLUSH + """
      return {toasts: T, copied: C, states: store.get("cs2s_slip", []).map(x => x.now.status), posts: P.length};
    })()""")
    assert out["copied"] == [] and out["posts"] == 0, out
    assert "stale" not in out["states"], out["states"]                  # "stale" stays reserved for kept-grade legs
    assert any("not fresh" in t or "did not complete" in t for t in out["toasts"]), out["toasts"]


@needs_node
def test_bug1_stale_feed_on_add_is_not_verified_and_kept_grade_still_copies(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], C = [];
      toast = m => T.push(m); copy = t => C.push(t);
      let feed = "stale";
      fetch = async u => ({ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: feed, note: "feed"} : {})});
      const fut = new Date(Date.now() + 3600e3).toISOString();
      const row = nick => ({id: "ud:" + nick, book: "underdog", nick, player: nick, line: 30.5, prop: "kills", scope: "12", side: "More", grade: "B", p: 0.6, units: 1,
        team_name: "T" + nick, opp_name: "O" + nick, start: fut, match_slug: "m-" + nick});
      store.set("cs2s_slip", []);
      await addToSlip(boardCard(row("feedstale")));
      const added = store.get("cs2s_slip", []).map(x => x.now.status);
      // a grade kept during an HLTV outage on a line the feed verifies: still the copyable "stale" (audit 22)
      feed = "verified";
      const kept = Object.assign(row("kept"), {hltv_retry: true, graded_at: fut});
      store.set("cs2s_slip", []);
      await addToSlip(boardCard(kept)); await addToSlip(boardCard(row("ok")));
      """ + COPY_CLICK + FLUSH + """
      return {added, states: store.get("cs2s_slip", []).map(x => x.now.status), copied: C};
    })()""")
    assert out["added"] in ([], ["failed"]), out["added"]
    assert out["states"] == ["stale", "verified"] and len(out["copied"]) == 1


# ---------------------------------------------------------------- bug 2: a changed line has no "Copy anyway"
@needs_node
def test_bug2_changed_line_offers_review_only(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], C = [], A = [];
      toast = m => T.push(m); copy = t => C.push(t); ask = async (title, body, btns) => { A.push(btns.map(b => b.k)); return btns[btns.length - 1].k; };
      fetch = async u => ({ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: "changed", new_line: 32.5, note: "Underdog now shows 32.5"} : {})});
      """ + LEG + """
      store.set("cs2s_slip", [leg("a"), leg("b")]);
      """ + COPY_CLICK + FLUSH + """
      return {asks: A, copied: C};
    })()""")
    assert out["copied"] == [], out
    assert out["asks"] and all(ks == ["review"] for ks in out["asks"]), out["asks"]


# ---------------------------------------------------------------- bug 3: started / pulled / moved legs never copy
@needs_node
def test_bug3_started_match_is_not_added(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], A = [];
      toast = m => T.push(m); ask = async (title, body, btns) => { A.push(btns.map(b => b.k)); return "keep"; };
      fetch = async u => ({ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: "unavailable", note: "Match started / locked"} : {})});
      const past = new Date(Date.now() - 3600e3).toISOString(), fut = new Date(Date.now() + 3600e3).toISOString();
      const row = (nick, start) => ({id: "ud:" + nick, book: "underdog", nick, player: nick, line: 30.5, prop: "kills", scope: "12", side: "More", grade: "B", p: 0.6, units: 1,
         team_name: "T" + nick, opp_name: "O" + nick, start, match_slug: "m-" + nick, graded_at: fut});
      store.set("cs2s_slip", []);
      await addToSlip(boardCard(row("started", past)));      // the start has passed
      await addToSlip(boardCard(row("locked", fut)));        // the platform says started / locked
      await addToSlip(boardCard(row("pulled", fut)));        // board line no longer offered: never kept as "your own line"
      return {legs: store.get("cs2s_slip", []).map(x => x.p), asks: A, toasts: T};
    })()""")
    assert out["legs"] == [], out
    assert not any("keep" in a for a in out["asks"]), out["asks"]


@needs_node
def test_bug3_started_or_moved_research_legs_block_copy(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = [], C = [], A = [], F = [];
      toast = m => T.push(m); copy = t => C.push(t); ask = async (title, body, btns) => { A.push(title); return "review"; };
      fetch = async u => { F.push(String(u)); return {ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: "verified"} : {})}; };
      """ + LEG + """
      const res = {};
      store.set("cs2s_slip", [leg("a"), leg("b", {bk: null, st: past, now: {status: "research", note: ""}})]);
      """ + COPY_CLICK + FLUSH + """
      res.started = {copied: C.length, asks: A.slice(), state: store.get("cs2s_slip", [])[1].now.status};
      store.set("cs2s_slip", [leg("a"), leg("c", {bk: null, now: {status: "research", note: "Kept at 30.5; Underdog shows 32.5"}})]);
      """ + COPY_CLICK + FLUSH + """
      res.moved = {copied: C.length, asks: A.slice()};
      store.set("cs2s_slip", [leg("a"), leg("d", {bk: null, now: {status: "research", note: ""}})]);    // a typed line no platform posts
      """ + COPY_CLICK + FLUSH + """
      res.typed = {copied: C.length};
      res.checks = F.filter(u => u.includes("line_check")).length;
      return res;
    })()""")
    assert out["started"]["copied"] == 0 and out["started"]["state"] == "started", out
    assert out["moved"]["copied"] == 0 and len(out["moved"]["asks"]) == 2, out
    assert out["typed"]["copied"] == 1                                   # a plain research line still copies
    assert out["checks"] == 3          # one check per platform leg per Copy; started / research legs are never fetched


# ---------------------------------------------------------------- bug 6: "+ Slip" adds the exact row pressed
@needs_node
def test_bug6_bslip_adds_the_row_pressed(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const added = [];
      addToSlip = d => added.push([d.grade.scope, d.next_match.slug, d.next_match.opponent]); drawBoard = () => {};
      const fut = h => new Date(Date.now() + h * 3600e3).toISOString();
      const row = (id, scope, slug, opp, h) => ({id, book: "underdog", nick: "zont1x", player: "zont1x", line: 15.5, prop: "kills", scope, side: "More", grade: "B", p: 0.6, units: 1,
         team_name: "Spirit", opp_name: opp, start: fut(h), match_slug: slug});
      boardRows = [row("ud:1", "1", "m-early", "FaZe", 2), row("ud:2", "1", "m-late", "NaVi", 8), row(undefined, "12", null, "MOUZ", 9)];
      const press = r => boardClick({stopPropagation() {}, target: {closest: s => s === "[data-bslip]" ? {dataset: {bslip: bslipKey(r)}} : null}});
      press(boardRows[1]); press(boardRows[2]);
      return {added};
    })()""")
    assert out["added"] == [["1", "m-late", "NaVi"], ["12", None, "MOUZ"]], out


# ---------------------------------------------------------------- bug 7: "On slip" with no match slug
@needs_node
def test_bug7_on_slip_marks_without_a_match_slug(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const fut = new Date(Date.now() + 3600e3).toISOString();
      const mk = slug => ({book: "underdog", nick: "kyousuke", player: "kyousuke", line: 35.5, prop: "kills", scope: "12", side: "More", grade: "B", p: 0.6, units: 1,
         team: "Spirit Academy", opponent: "BC.Game", team_name: "Spirit Academy", opp_name: "BC.Game", start: fut, match_slug: slug});
      const res = {};
      for (const slug of ["s-123", null]) { const r = mk(slug), leg = legFrom(boardCard(r)); res[String(slug)] = slipKeyLeg(leg) === slipKeyRow(r); }
      // a slip saved before this fix (m = normalised team|opponent) still matches
      const old = Object.assign(legFrom(boardCard(mk(null))), {m: "bcgame|spiritacademy"});
      res.saved = slipKeyLeg(old) === slipKeyRow(mk(null));
      return res;
    })()""")
    assert out == {"s-123": True, "null": True, "saved": True}, out


# ---------------------------------------------------------------- 9: a direct link to #slips opens the studio once /auth/me answers
@needs_node
def test_9_direct_slips_link_opens_studio_after_auth(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const F = [];
      fetch = async u => { F.push(String(u)); return {ok: true, status: 200, json: async () => ({batches: []})}; };
      STUDIO.off = true; setMode("slips", true);
      const before = {open: STUDIO.open, hist: F.filter(u => u.includes("history")).length};
      studioSwitch(true);
      """ + FLUSH + """
      return {before, open: STUDIO.open, hist: F.filter(u => u.includes("history")).length};
    })()""")
    assert out["before"] == {"open": False, "hist": 0}
    assert out["open"] is True and out["hist"] >= 1, out


# ---------------------------------------------------------------- 10 / 11: studio wording
@needs_node
def test_10_full_build_has_no_couldnt_fill_heading(tmp_path):
    out = run(tmp_path, "index.html", """(() => {
      const fut = new Date(Date.now() + 3600e3).toISOString();
      const lg = i => ({nick: "p" + i, line: 20.5, side: "More", prop: "kills", scope: "12", grade: "B", p: 0.6, start: fut, team: "T" + i, opponent: "O" + i, book: "underdog", row_id: "ud:" + i});
      const slip = {slip_id: "b1/b1/s1", build_id: "b1/b1", n: 2, book: "underdog", legs: [lg(1), lg(2)], all_hit: 0.36, all_hit_basis: "model_unconditional", status: "live"};
      STUDIO.batch = {batch_id: "b1", builds: 1, slips: {2: [slip]}, exposure: {slips: 1}, settings: {fill: "large"}};
      STUDIO.last = {batch_id: "b1", build_id: "b1/b1", shortage: {}, excluded: [], eligible: {lines: 9, matches: 5}, settings: {book: "underdog"}};
      studioRender(); const full = $("#st-out").innerHTML;
      STUDIO.last.shortage = {4: "Only 3 matches left"}; studioRender(); const short = $("#st-out").innerHTML;
      return {full, short};
    })()""")
    assert "Couldn't fill every size" not in out["full"] and "Eligible right now" in out["full"]
    assert "Couldn't fill every size" in out["short"]


@needs_node
def test_11_still_free_only_when_repeats_never(tmp_path):
    out = run(tmp_path, "index.html", """(() => {
      STUDIO.batch = {batch_id: "b1"}; STUDIO.last = {batch_id: "b1", eligible: {max_slips_left: {4: 0, 2: 1}}};
      const d = {lines: 12, matches: 5, max_slips: {4: 1, 2: 2}};
      return {asn: capText(d, {book: "underdog", sizes: [4, 2], repeats: "as_needed"}), none: capText(d, {book: "underdog", sizes: [4, 2], repeats: "none"})};
    })()""")
    assert "Still free" not in out["asn"] and "Still free" in out["none"]
