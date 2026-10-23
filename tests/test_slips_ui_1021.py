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


# ================================================================ upgrades
ROW = """const fut = h => new Date(Date.now() + (h || 2) * 3600e3).toISOString(), T0 = fut();
  const row = (id, nick, o) => Object.assign({id, book: "underdog", nick, player: nick, line: 20.5, graded_line: 20.5, prop: "kills", scope: "12", side: "More", grade: "B", p: 0.6, units: 0.5,
     team: "Alpha", team_name: "Alpha", opponent: "Bravo", opp_name: "Bravo", start: T0, match_slug: "alpha-bravo", igl: false, awp: false, state: "GRADE"}, o || {});"""
VERIFIED = """fetch = async u => ({ok: true, status: 200, json: async () => (String(u).includes("line_check") ? {status: "verified"} : {})});"""


@needs_node
def test_u11_u13_u5_slip_panel_content(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      """ + LEG + """
      store.set("cs2s_slip", [leg("a", {st: new Date(Date.now() + 80 * 60e3 + 20e3).toISOString()}), leg("b", {s: "Less"}), leg("c", {st: past})]);
      $("#slip").hidden = false; drawSlip();
      return {panel: $("#slip").innerHTML, page: $("#myslip").innerHTML, out: SLIP_OUT.reduced[0]};
    })()""")
    h = out["panel"]
    assert "<h4>Slip · Underdog · 3-pick</h4>" in h and "Bet slip" not in h                          # U13
    assert "slippay" not in h and "payout" not in h.lower()                                            # U11
    assert "starts in 1h 20m" in h and "first lock in 1h" in h and "2 Overs · 1 Under" in h            # U5 / U4
    assert "Started – remove" in h and 'class="slipfoot"' in h and "Est. chance" in h                  # U5 / U1 footer
    assert "Your slip · Underdog · 3-pick" in out["page"] and 'id="slipcopy"' not in out["page"]      # U1 page card, no duplicate ids
    assert out["out"] == ""                                                                            # U13 neutral push/void pill


def test_u13_desktop_header_keeps_the_wordmark():
    from tests.test_audit13_site_ui import HTML
    assert "body.hasslip:not(.locked) header h1{display:none}" not in HTML


@needs_node
def test_u1_refused_add_is_a_toast_and_sheet_closes_on_tab_switch(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = []; toast = m => T.push(m); let opened = 0; openSlip = () => { opened++; };
      """ + LEG + """
      store.set("cs2s_slip", [leg("a")]);
      pushLeg(leg("a")); pushLeg(leg("z", {bk: "prizepicks"}));
      innerWidth = 375; $("#slip").hidden = false; setMode("board", true);
      return {opened, toasts: T.length, hidden: $("#slip").hidden};
    })()""")
    assert out == {"opened": 0, "toasts": 2, "hidden": True}


@needs_node
def test_u4_named_pairs_and_swap_by_studio_rules(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      """ + ROW + """
      BK.underdog.rows = [row("ud:1", "stavn"), row("ud:2", "Krimbo"), row("ud:3", "rival", {team_name: "Bravo", team: "Bravo", opp_name: "Alpha", opponent: "Alpha"}),
        row("ud:4", "best", {match_slug: "c-d", team_name: "C", opp_name: "D", p: 0.66}),
        row("ud:5", "role", {match_slug: "e-f", team_name: "E", opp_name: "F", p: 0.7, igl: null}),
        row("ud:6", "lean", {match_slug: "g-h", team_name: "G", opp_name: "H", p: 0.72, grade: "B-", units: 0}),
        row("ud:7", "moved", {match_slug: "i-j", team_name: "I", opp_name: "J", p: 0.75, graded_line: 19.5}),
        row("ud:8", "gone", {match_slug: "k-l", team_name: "K", opp_name: "L", p: 0.76, start: new Date(Date.now() - 6e4).toISOString()})];
      BK.underdog.loaded = true;
      const r = BK.underdog.rows, legs = [r[0], r[1], r[2]].map(x => Object.assign(legFrom(boardCard(x)), {bk: "underdog", now: {status: "verified"}}));
      store.set("cs2s_slip", legs); $("#slip").hidden = false; drawSlip();
      return {html: $("#slip").innerHTML, best: (swapBest(legs) || {}).nick};
    })()""")
    assert "<b>stavn</b> and <b>Krimbo</b> are teammates in the same match (both Over)" in out["html"]
    assert "<b>stavn</b> and <b>rival</b> play each other" in out["html"]
    assert out["best"] == "best" and "data-swap=" in out["html"]


@needs_node
def test_u2_send_studio_slip_to_my_slip(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const T = []; toast = m => T.push(m); ask = async () => "go";
      """ + VERIFIED + """
      const fut = new Date(Date.now() + 3600e3).toISOString();
      const lg = (i, o) => Object.assign({row_id: "ud:" + i, nick: "p" + i, line: 20.5, side: "Less", prop: "kills", scope: "12", grade: "B+", p: 0.63, units: 1, book: "underdog",
        team: "T" + i, opponent: "O" + i, start: fut, match_slug: "m" + i, market_type: "standard", model_outcomes: {over: 0.36, under: 0.63, push: 0.01}}, o || {});
      STUDIO.batch = {batch_id: "b", slips: {2: [{slip_id: "b/b1/s1", build_id: "b/b1", n: 2, book: "underdog", legs: [lg(1), lg(2)]}]}};
      BK.underdog.rows = [lg(1), lg(2)].map(l => Object.assign({}, l, {id: l.row_id, player: l.nick, graded_line: l.line})); BK.underdog.loaded = true;
      store.set("cs2s_slip", [{p: "old", bk: "underdog", t: "kills", sc: "12", l: 1, s: "More", st: fut, now: {status: "verified"}}]);
      await studioToMySlip("b/b1/s1");
      return store.get("cs2s_slip", []).map(x => [x.p, x.l, x.s, x.g, x.pc, x.u, x.bk, x.rid, x.now.status, x.m]);
    })()""")
    assert out == [["p1", 20.5, "Less", "B+", 0.63, 1, "underdog", "ud:1", "verified", "m1"],
                   ["p2", 20.5, "Less", "B+", 0.63, 1, "underdog", "ud:2", "verified", "m2"]]


@needs_node
def test_u6_latest_build_first_and_short_repeat_note(tmp_path):
    out = run(tmp_path, "index.html", """(() => {
      const fut = h => new Date(Date.now() + h * 3600e3).toISOString();
      const lg = (i, h) => ({nick: "p" + i, line: 20.5, side: "More", prop: "kills", scope: "12", grade: "B", p: 0.6, start: fut(h), team: "T", opponent: "O", book: "underdog", row_id: "ud:" + i});
      const sl = (b, k, ah, h, rep) => ({slip_id: `x/b${b}/s${k}`, build_id: `x/b${b}`, n: 2, book: "underdog", legs: [lg(k, h), lg(k + 50, h)], all_hit: ah, status: "live", repeats: rep || []});
      STUDIO.batch = {batch_id: "x", builds: 2, slips: {2: [sl(1, 1, 0.3, 1), sl(2, 2, 0.31, 5), sl(2, 3, 0.4, 9, ["p3 in 2 slips", "T vs O in 2 slips"])]}, exposure: {slips: 3}, settings: {}};
      STUDIO.sort = "chance"; studioRender(); const a = $("#st-out").innerHTML;
      STUDIO.sort = "start"; studioRender(); const b = $("#st-out").innerHTML;
      return {a, b};
    })()""")
    a = out["a"]
    assert a.index("Build 2 · 2 slips · latest") < a.index('<details class="studio-build"><summary>Build 1 · 1 slip</summary>')
    assert a.index("x/b2/s3") < a.index("x/b2/s2") and out["b"].index("x/b2/s2") < out["b"].index("x/b2/s3")   # chance / earliest start
    assert ">2 picks reused</span>" in a and "Send to my slip" in a


@needs_node
def test_u9_share_link_round_trip_against_the_board(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      """ + ROW + """
      const A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";   // btoa / atob (the browser's) for node
      btoa = t => { let o = ""; for (let i = 0; i < t.length; i += 3) { const n = (t.charCodeAt(i) << 16) | ((t.charCodeAt(i + 1) || 0) << 8) | (t.charCodeAt(i + 2) || 0);
        o += A[n >> 18 & 63] + A[n >> 12 & 63] + (i + 1 < t.length ? A[n >> 6 & 63] : "=") + (i + 2 < t.length ? A[n & 63] : "="); } return o; };
      atob = t => { if (/[^A-Za-z0-9+/=]/.test(t)) throw new Error("bad"); let o = "", b = 0, n = 0; for (const c of t.replace(/=+$/, "")) { b = b << 6 | A.indexOf(c); n += 6;
        if (n >= 8) { n -= 8; o += String.fromCharCode(b >> n & 255); } } return o; };
      unescape = globalThis.unescape || (x => x); escape = globalThis.escape || (x => x);
      BK.underdog.rows = [row("ud:1", "ZywOo"), row("ud:2", "ropz", {side: "Less"})]; BK.underdog.loaded = true;
      const legs = BK.underdog.rows.map(x => legFrom(boardCard(x)));
      legs.push(Object.assign({}, legs[0], {p: "nobody"}));
      legs[1] = Object.assign({}, legs[1], {s: "More"});
      const v = new URL(slipLink(legs)).searchParams.get("slip");
      return {parsed: parseSlipLink(v).map(x => [x.p, x.l, x.s]), res: sharedLegs(parseSlipLink(v)).map(z => [z.x.p, !!z.r, z.why]), bad: parseSlipLink("%%%")};
    })()""")
    assert out["parsed"] == [["ZywOo", 20.5, "More"], ["ropz", 20.5, "More"], ["nobody", 20.5, "More"]]
    assert out["res"] == [["ZywOo", True, ""], ["ropz", False, "the grade's side is now Under"], ["nobody", False, "not offered on the board now"]]
    assert out["bad"] == []


@needs_node
def test_u8_exposure_counts_only(tmp_path):
    out = run(tmp_path, "index.html", """(() => {
      const L = (p, a, b) => ({p, mt: expoMatch(a, b), k: p + ":1"});
      expoAdd("studio", [L("vision", "GZ", "Abyssal"), L("FL1T", "PARIVISION", "BetBoom")]);
      expoAdd("studio", [L("vision", "GZ", "Abyssal"), L("x", "BetBoom", "PARIVISION")]);
      expoAdd("studio", [L("vision", "GZ", "Abyssal"), L("x", "BetBoom", "PARIVISION")]);
      store.set("cs2s_slip", [{p: "vision", tm: "GZ", vs: "Abyssal", s: "More"}]);
      drawExposure();
      return $("#slipexpo").innerHTML;
    })()""")
    assert "2 slips copied today" in out and "2 with <b>vision</b>" in out and "BetBoom vs PARIVISION in 2" in out
    assert "<b>vision</b> (already in 2 copied slips)" in out
    for w in ("won", "lost", " hit", "payout", "profit"):
        assert w not in out.lower(), w


@needs_node
def test_u7_build_around_sends_must_include_and_shows_left_out(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const B = []; toast = () => {};
      fetch = async (u, o) => { if (String(u).includes("/build")) { B.push(JSON.parse(o.body));
          return {ok: true, status: 200, json: async () => ({batch_id: "x", build_id: "x/b1", slips: {2: [], 3: [], 4: []}, settings: {book: "underdog"}, board: {graded_rows: 3},
            shortage: {}, excluded: [], eligible: {lines: 3, matches: 2}, pinned_left_out: [{id: "ud:2", why: "started / locked"}]})}; }
        if (String(u).includes("/batch")) return {ok: true, status: 200, json: async () => ({batch_id: "x", builds: 1, slips: {}, exposure: {}, current: true})};
        return {ok: true, status: 200, json: async () => ({batches: []})}; };
      """ + ROW + """
      BK.underdog.rows = [row("ud:1", "ZywOo"), row("ud:2", "ropz"), row("ud:3", "frozen")];
      for (let i = 0; i < 50; i++) await Promise.resolve();      // the page's own /auth/me answer (studio off) lands first
      STUDIO.off = false; MODE = "slips";
      pinPick("ud:1", "underdog", "ZywOo Over 20.5 kills"); pinPick("ud:2", "underdog", "ropz Over 20.5 kills"); pinPick("ud:3", "underdog", "frozen");
      const pins = STUDIO.pins.map(x => x.id);
      await studioBuild(false);
      for (let i = 0; i < 50; i++) await Promise.resolve();
      return {pins, sent: B[0].settings.must_include, out: $("#st-out").innerHTML, why: whyHtml(BK.underdog.rows[0])};
    })()""")
    assert out["pins"] == ["ud:1", "ud:2"] and out["sent"] == ["ud:1", "ud:2"]
    assert "ropz Over 20.5 kills – left out: started / locked" in out["out"]
    assert 'data-pin="ud:1"' in out["why"] and "Build around this pick" in out["why"]


@needs_node
def test_verify_on_an_already_replaced_slip_does_not_rebuild(tmp_path):
    out = run(tmp_path, "index.html", """(async () => {
      const B = []; toast = () => {};
      fetch = async (u, o) => { if (String(u).includes("/build")) B.push(1);
        if (String(u).includes("/verify")) return {ok: true, status: 200, json: async () => ({ok: false, action: "replaced", status: "replaced", replaced_by: "x/b3", changes: ["line moved 20.5 to 21.5"]})};
        return {ok: true, status: 200, json: async () => ({batch_id: "x", slips: {}, exposure: {}})}; };
      await studioVerify("x/b1/s1", {disabled: false, textContent: ""});
      for (let i = 0; i < 50; i++) await Promise.resolve();
      return {builds: B.length, notes: STUDIO.notes["x/b1/s1"]};
    })()""")
    assert out["builds"] == 0 and out["notes"] == ["line moved 20.5 to 21.5", "replaced in build b3"]


@needs_node
def test_u7_built_slip_marks_its_pinned_leg_from_built_around(tmp_path):
    """The builder stores a pinned build's ids per slip as built_around (slip_builder.build, studio-4); the slip marks that leg."""
    out = run(tmp_path, "index.html", """(() => {
      STUDIO.pins = [];
      const fut = new Date(Date.now() + 3600e3).toISOString();
      const leg = id => ({row_id: id, nick: "p" + id, prop: "kills", scope: "12", line: 30.5, side: "More", p: 0.6, grade: "B", units: 1, start: fut,
                          book: "underdog", match: "m" + id});
      const s = {slip_id: "b/1", book: "underdog", n: 2, legs: [leg("ud:1"), leg("ud:2")], built_around: ["ud:1"], status: "live"};
      const h = studioSlip(s);
      return {pins: (h.match(/build around/g) || []).length};
    })()""")
    assert out["pins"] == 1
