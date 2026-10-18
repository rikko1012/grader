"""Page wording the owner rules depend on (web/index.html is one static page - these are string checks)."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")


def test_capped_kills_chance_is_labelled_not_changed():
    assert "(capped)</span>" in HTML and "const KCAP = 0.78" in HTML


def test_scout_rows_say_no_match_and_cannot_be_added():
    assert "No match scheduled" in HTML
    assert 'disabled>+ Add to slip</button>' in HTML


def test_views_have_addresses_and_no_external_arrow_on_the_rail():
    for h in ('href="#one"', 'href="#board"', 'href="#slate"'):
        assert h in HTML
    assert '<span class="route-arrow">↗</span>' not in HTML


def test_new_filters_and_studio_controls_exist():
    for s in ('id="bflag"', 'id="cdiff"', 'id="csort"', 'id="mlusable"', '"Fill first"', "/api/slip_studio/capacity", 'id="bstudio" hidden'):
        assert s in HTML


def test_member_wording_rules():
    assert not re.search(r"No bet", HTML)           # sub-threshold picks stay "Lean Over / Under"
    assert ">More<" not in HTML and ">Less<" not in HTML


# ---------------------------------------------------------------- 2026-10-11 confirmed-findings batch (UI)
def test_scout_reads_lean_everywhere():
    # the internal SCOUT state never shows as a label next to Over / Under: sideWord / pickLine / chips map it to Lean
    # display gd-1 (owner 2026-10-15): Lean = the MODEL grade under 55% (or no match); a 0-unit graded pick keeps its letter
    assert 'const isLeanV = v => !!v && (mGrade(v) === "LEAN" || v.grade === "SCOUT" || v.state === "SCOUT");' in HTML
    assert "const mGrade = v => !v ? null : (v.model_grade || v.grade);" in HTML
    assert "sideWord = v => !v.side ? \"–\" : isLeanV(v)" in HTML
    assert 'isLeanV({grade: x.g})' in HTML                                    # Recent chips + slip legs
    assert not re.search(r"SCOUT (Over|Under)|· SCOUT ·", HTML)
    assert 'r.state === "SCOUT" ? `<button type="button" class="bslip" disabled' in HTML   # no '+ Slip' for a no-match row


def test_ladder_chips_show_the_model_grade_and_0u():
    # superseded 2026-10-15 (owner): a 0-unit chip used to read Lean; it now shows the grade its chance earns plus "0U"
    assert 'sideWord({side: x.side, grade: mGrade(x)})' in HTML
    assert '" · " + esc(mGrade(x)) + (+x.units > 0 ? "" : " · 0U")' in HTML
    assert "const isBet = r => (r.units || 0) > 0 && !sideGone(r);" in HTML        # eligibility stays on units / availability


def test_share_link_keeps_maps_platform_and_match():
    for k in ('searchParams.set("sc"', 'searchParams.set("bk"', 'searchParams.set("tm"', 'searchParams.set("vs"', 'searchParams.set("st"',
              'q.get("sc")', 'q.get("bk")', 'qs.set("start", ctx.start)'):
        assert k in HTML


def test_card_popup_is_a_history_step_and_board_hash_is_named():
    assert 'history.pushState({card: 1}, "", location.href)' in HTML and "function hidePop()" in HTML
    assert "'#board' alone: this entry gets its platform" in HTML


def test_on_slip_is_the_exact_line():
    assert "new Set(store.get(\"cs2s_slip\", []).map(slipKeyLeg))" in HTML and "onSlip.has(slipKeyRow(r))" in HTML
    assert "onSlip.has(nick.toLowerCase())" not in HTML


def test_underdog_counts_lines_still_grading():
    assert "graded Underdog lines${B.total > B.rows.length ? ` (${B.total - B.rows.length} still grading)`" in HTML
    assert "the first grades appear within a few minutes" in HTML


def test_studio_capacity_reads_max_slips_and_is_labelled():
    assert "d.max_slips" in HTML and "mx = d.max ||" not in HTML
    assert "cs2s_studio_open" not in HTML                                     # never reopened on its own
    assert "Research – not validated" not in HTML and "Is this validated?" not in HTML   # owner 2026-10-16: no unsure labels
    assert 'seg("repeats", [["as_needed", "When needed"], ["none", "Never"]])' in HTML       # studio-2: the count asked for
    assert "Smaller slips are planned first" in HTML                          # explainer follows Fill first


def test_spread_and_recipe_stay_private_on_the_page():
    assert "it0.sd" not in HTML and "cp.ours.sd" not in HTML and "Previous model <span" not in HTML


def test_help_text_recency_is_correct():
    assert "four months ago counts half" not in HTML and "4 months old counts half" not in HTML
    assert "about three months ago counts half" in HTML


def test_phone_layout_hooks():
    for k in ('class="slatelist"', "slateItem(", 'class="mclist"', "function foldCard()", ".folded>:not(h2){display:none!important}",
              'id="scout-filters"', "Filters${n ? ` (${n} active)` : \"\"}", "add m13 for Maps 1–3 or bo1 for Map 1"):
        assert k in HTML
    assert 'id="scout-expand"' not in HTML and 'id="scout-collapse"' not in HTML
    # one nav order everywhere (2026-10-18 release): Board, Matches, Moneylines, Slips, Results, then the tools
    m = re.search(r'<nav class="seg" id="mode"[^>]*>(.*?)</nav>', HTML)
    order = [m.group(1).index(f'data-v="{v}"') for v in ("board", "matches", "moneylines", "slips", "results", "one", "slate")]
    assert order == sorted(order)
    rail = HTML[HTML.index('class="scout-rail"'):HTML.index('<div class="rail-bottom">')]
    assert [rail.index(f'data-scout-route="{v}"') for v in ("board", "matches", "moneylines", "slips", "results", "one", "slate")] == \
        sorted(rail.index(f'data-scout-route="{v}"') for v in ("board", "matches", "moneylines", "slips", "results", "one", "slate"))


def test_no_unsure_badges_or_notes_on_the_member_page():
    CODE = re.sub(r"(?m)^\s*//.*$|\s//[^`'\"\n]*$", "", HTML)               # the page, without its code comments
    # owner 2026-10-16: "I never want to see that label or any other unsure label on my site" - what is not confirmed is
    # left out (grades, chances and stakes unchanged), only definite facts get a badge
    for t in ('"HLTV unreachable"', '"HLTV out of date"', "HLTV is missing his recent matches", '"Roster not confirmed"',
              '"Line not confirmed"', "Not usable now", "Research – not validated", "stale grade kept", "saved grade – not current",
              "research line – not verified", "couldn't check", "live data unavailable", "Unknown (no lineup data)",
              "lineup unclear", "– provisional", "Model: not validated yet"):
        assert t not in CODE, t
    assert '"Stand-in"' in HTML.split("const issueLabel")[1][:300] and '"Long break"' in HTML.split("const issueLabel")[1][:300]
    assert ".filter(f => !UNSURE.test(f))" in HTML                           # card notes: no hedge flags
