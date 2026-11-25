"""v3 CS2 player card: HLTV only (owner 2026-11-20, emphatic: "no BO3.gg data anywhere on member-page stats - HLTV only").

Renders REAL line docs (stadodo, Kills 28.5 and Headshots 16.5 Maps 1-2, PrizePicks, saved from the live board 2026-11-20:
tests/fixtures/v3_stadodo/) in the v3 card at 390 and 1280 and reads every tab (Overview with Why / Behind the grade, Lines, Stats
with every panel opened, Ask). The check is built from the doc's own BO3.gg-only fields (cs2_card.stats incl. form_windows,
per_map, checks, teammates, the "Saved with this forecast" grader inputs, the grade block's context-only factors, the saved L10 /
recent average, FACEIT / CS API): no number on the page may equal one of them, as the renderers print it (1-3 dp, %). A value that
also comes from an HLTV or model field of the same doc (or the HLTV per-map totals) is allowed, so a coincidence is not a failure.
Bare integers (maps, days) are checked as label / value pairs. Mutated copies of the real doc add what stadodo happens not to
carry: a BO3.gg per-map table, a BO3.gg rank band, a CS API block, a BO3.gg AWP note, a from_inputs-shaped card, and the server's
HLTV series for a line whose saved L10 HLTV does not reproduce. Also: the v3 factor rows carry the HLTV equivalents from
/api/upgrade/player-maps, and the Genie's payload on a v3 request (v3_hltv.scrub) carries no BO3.gg number either."""
import copy
import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import v3_hltv
import workspace_genie
from tests.test_upgrade_browser import browser, env, context, no_h_scroll  # noqa: F401

D = Path(__file__).parent / "fixtures" / "v3_stadodo"
BOARD = (D / "board_cs2.json").read_text(encoding="utf-8")
KILLS = json.loads((D / "line_stadodo_kills.json").read_text(encoding="utf-8"))
HS = json.loads((D / "line_stadodo_hs.json").read_text(encoding="utf-8"))
PMAPS = json.loads((D / "player_maps_stadodo.json").read_text(encoding="utf-8"))
PHONE, DESK = {"width": 390, "height": 844}, {"width": 1280, "height": 900}
NUM = re.compile(r"\d+\.\d+%?|\d+%")
# labels that only ever carry a BO3.gg number: never on the v3 card
BO3_LABELS = ("Saved with this forecast", "Trade kills", "Opening kills", "Recent kills per round", "Teammate ratings", "Second source check",
              "FACEIT", "Last-10 average", "Last-10 median", "Kills per round, last 10 matches", "Recent KPR", "BO3.gg", "Deaths / round")


def fmts(v):
    """the forms the card prints a number in (n(), toFixed, %, the grade block's formats)"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return set()
    out = {f"{v:.1f}", f"{v:.2f}", f"{v:.3f}", f"{v:.1f}%", f"{v:.2f}%"}
    if 0 <= v <= 1:
        out |= {f"{round(v * 100)}%", f"{v * 100:.1f}%"}
    if float(v).is_integer():
        out.add(f"{int(v)}%")                     # a percent printed whole (map chances)
    return out


def walk(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)
    else:
        yield x


def bo3_values(doc):
    """every BO3.gg-only number of a raw doc, as printable strings"""
    c, out = doc.get("cs2_card") or {}, set()
    for part in (c.get("stats"), c.get("per_map"), c.get("teammates"), c.get("me"), c.get("team_kill_share"), c.get("csapi"), c.get("faceit"),
                 {k: v for k, v in (c.get("checks") or {}).items() if k in ("trade_pr", "opening")}):
        for v in walk(part):
            out |= fmts(v)
    rb = c.get("rank_band")
    if isinstance(rb, dict) and "bo3" in str(rb.get("source") or "").lower():
        for v in walk(rb):
            out |= fmts(v)
    for r in (doc.get("context") or {}).get("rows") or []:
        if r.get("group") == "Saved with this forecast" and not isinstance(r.get("value"), str):
            out |= fmts(r["value"])
    for f in (doc.get("grade_inputs") or {}).get("factors") or []:
        if f.get("live") is not True and f.get("id") in v3_hltv.GI_BO3:
            out |= set(NUM.findall(str(f.get("value"))))
    h = doc.get("history") or {}
    if not h.get("series"):
        out |= fmts(h.get("avg"))
    return {s for s in out if s not in ("0.0", "0.00", "0.000", "1.00", "1.0", "0%", "100%")}


def rates(t):
    out = set()
    if not t:
        return out
    pairs = ((t.get("kills"), t.get("maps")), (t.get("kills"), t.get("rounds")), (t.get("kills"), t.get("deaths")), (t.get("deaths"), t.get("rounds")),
             (100 * (t.get("hs") or 0), t.get("hs_kills")), (t.get("hs"), t.get("hs_maps")), (t.get("rating_x_rounds"), t.get("rating_rounds")),
             (t.get("adr_x_rounds"), t.get("adr_rounds")), (t.get("kast_x_rounds"), t.get("kast_rounds")))
    for a, b in pairs:
        if a is not None and b:
            out |= fmts(a / b)
    return out


def hltv_allowed(scrubbed):
    """numbers that come from HLTV or the model in the same doc, plus the HLTV per-map totals' rates (single maps, all, any pair)"""
    out = set()
    for v in walk(scrubbed):
        out |= fmts(v) if not isinstance(v, str) else set(NUM.findall(v))
    for w in (PMAPS.get("windows") or {}).values():
        maps = w.get("maps") or {}
        out |= rates(w.get("overall"))
        for t in maps.values():
            out |= rates(t)
        for a, b in itertools.combinations(maps.values(), 2):
            out |= rates({k: (a.get(k) or 0) + (b.get(k) or 0) for k in set(a) | set(b) if isinstance(a.get(k, 0), (int, float))})
    return out


def mutated(doc):
    """the real doc with the BO3.gg fields stadodo happens not to carry filled in (as cs2_card_ctx builds them)"""
    d = copy.deepcopy(doc)
    c = d["cs2_card"]
    c["per_map"] = [{"map": "de_mirage", "kpr": 0.7123, "maps_played": 9, "hs_pct": 61.7}, {"map": "de_nuke", "kpr": 0.5987, "maps_played": 7}]
    c["rank_band"] = {"opp_rank": 168, "band": 10, "maps": 13, "kpr": 0.6043, "kpr_all": 0.6687, "ratio": 0.9037, "teams": ["Lavked"], "source": "BO3.gg 12 months"}
    c["csapi"] = {"window": "3m", "maps": 27, "team": "RBLS", "vrs_rank": 74, "on_roster": True, "kills_per_map": 17.37, "rating": 1.0813, "swing": 0.743,
                  "adr": 77.77, "kast": 71.13, "ct": {"k": 8.83}, "t": {"k": 8.47}}
    c["checks"]["awp_note"] = "AWPer (Maps 1-2 HS% 21.7 < 30 with KPR 0.744)"
    c["checks"]["days_since_last_map"] = 34                     # the grade's rest rule fires (max 1U): kept as the grade's own yes / no
    return d


def from_inputs(doc):
    """a card in cs2_card_ctx.from_inputs shape (a line graded before its card copy existed)"""
    d = copy.deepcopy(doc)
    d["cs2_card"] = {"v": 1, "from_inputs": True, "stats": {"rating": 1.0457, "kast": 74.67, "dpr": 0.6377, "adr": 72.83, "kpr": 0.6633, "maps_played": 29, "kpr_recent": 0.6517},
                     "per_map": [{"map": "de_inferno", "kpr": 0.6871, "maps_played": 6, "hs_pct": 47.3}],
                     "checks": {"trade_pr": 0.1277, "days_since_last_map": 11, "standin_for": None, "roster_confirmed": True},
                     "rank_band": {"opp_rank": 168, "band": 10, "maps": 11, "kpr": 0.6211, "kpr_all": 0.6633, "ratio": 0.9364, "source": "BO3.gg 12 months"},
                     "environment": doc["cs2_card"]["environment"], "used": ["kpr", "environment", "sample", "opponent", "hs_per_kill", "rounds"]}
    return d


def with_hltv_series(doc):
    """what workspace_api adds when HLTV does not reproduce the saved L10: his HLTV series (made-up values here: the test checks the
    card counts these, not the saved tally)"""
    d = copy.deepcopy(doc)
    vals = [13, 18, 11, 15, 17, 12, 14, 19, 10, 16, 15, 13]
    d["history"]["hltv_series"] = [{"date": f"2026-11-{12 - i:02d}", "opponent": f"Team{i}", "value": v, "maps": ["Mirage", "Nuke"], "values": [v // 2, v - v // 2]}
                                   for i, v in enumerate(vals)]
    return d


def read_card(p):
    """the text of every tab, every <details> opened"""
    texts = {}
    for t in ("overview", "lines", "stats", "ask"):
        p.locator(f'#hub-detail [role=tab][data-hub=card-tab][data-id={t}]').click()
        p.wait_for_function(f"document.querySelector('#hub-detail').dataset.tab === '{t}' && ![...document.querySelectorAll('#hub-detail .h-detail-loading')].some(e => /Loading/.test(e.textContent))", timeout=20000)
        p.wait_for_timeout(200)
        p.evaluate("document.querySelectorAll('#hub-detail details').forEach(d => d.open = true)")
        p.wait_for_timeout(100)
        texts[t] = p.locator("#hub-card").inner_text()
    return texts


def factor_rows(p):
    p.locator('#hub-detail [role=tab][data-hub=card-tab][data-id=stats]').click()
    p.wait_for_selector('#hub-card-body .h-fx[data-fx="cs2-factors"]', timeout=20000)
    return dict(p.evaluate("""[...document.querySelectorAll('#hub-card-body .h-fx[data-fx="cs2-factors"] tbody tr')].map(r => [r.cells[0].innerText.trim(), r.cells[1].innerText.trim() + (r.cells[2] ? ' | ' + r.cells[2].innerText.trim() : '')])"""))


def fresh(x, when=None):
    """the saved match starts tomorrow from whenever the test runs (the live snapshot's own start would put the row in the past)"""
    when = when or (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat()
    if isinstance(x, dict):
        return {k: when if k in ("start_utc", "lock_utc") and isinstance(v, str) else fresh(v, when) for k, v in x.items()}
    if isinstance(x, list):
        return [fresh(v, when) for v in x]
    return x


def open_line(browser, env, monkeypatch, size, docs, pmaps=PMAPS, hold=None):
    """hold: {offer id: 'pending' | 500} - that /line is held unanswered (the routes go to hold['routes']) or fails"""
    monkeypatch.setenv("SCOUT_UI_V3", "1")
    monkeypatch.setenv("SCOUT_GENIE", "1")
    c = context(browser, env, size)
    board = json.dumps(fresh(json.loads(BOARD)))
    c.route(lambda u: u.split("?")[0].endswith("/api/upgrade/board") and ("sport=" not in u or "sport=cs2" in u),
            lambda r: r.fulfill(status=200, content_type="application/json", body=board))

    def line(r):
        for oid, d in docs.items():
            if "id=" + oid.replace(":", "%3A") in r.request.url or "id=" + oid in r.request.url:
                how = (hold or {}).get(oid)
                if how == "pending":
                    return hold.setdefault("routes", []).append((r, d))
                if how == 500:
                    return r.fulfill(status=500, content_type="application/json", body='{"error": "boom"}')
                return r.fulfill(status=200, content_type="application/json", body=json.dumps(fresh(d)))
        return r.fallback()
    c.route("**/api/upgrade/line?*", line)
    c.route("**/api/upgrade/player-maps?*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(pmaps)))
    p = c.new_page()
    p.errors = []
    p.on("pageerror", lambda e: p.errors.append(str(e)))
    p.goto(env.url + "/#all-sports")
    p.wait_for_selector("#hub-rows tr[data-record]", timeout=30000)
    q = p.locator("#hub-q")
    if not q.is_visible():
        p.locator(".sc-v3-bar [data-v3=search]").click()
    q.fill("stadodo")
    p.wait_for_timeout(500)
    return c, p


def open_prop(p, label):
    """Kills from the board row; Headshots from the card's own player-markets strip (as a member switches)"""
    if label == "Kills":
        p.locator("#hub-rows tr[data-record]", has_text="Kills").first.locator(".h-player").first.click()
    else:
        p.locator('#hub-detail [role=tab][data-hub=card-tab][data-id=overview]').click()
        p.locator("#hub-detail .h-player-markets button", has_text="Headshots").first.click()
        p.wait_for_function("document.querySelector('#hub-decision .v3p-lh') && document.querySelector('#hub-decision .v3p-lh').textContent.includes('16.5')", timeout=30000)
    p.wait_for_selector("#hub-detail .h-why", timeout=20000)
    p.wait_for_function("![...document.querySelectorAll('#hub-detail .h-detail-loading')].some(e => /Loading/.test(e.textContent))", timeout=20000)
    p.wait_for_selector("#hub-detail .v3p-grid[data-v3p-src=hltv]", timeout=20000)


def check_no_bo3(raw, texts, extra_allowed=()):
    deny = bo3_values(raw) - hltv_allowed(v3_hltv.scrub(raw)) - set(extra_allowed)
    assert deny, "the check found no BO3.gg values to look for"
    for t, text in texts.items():
        hit = sorted(set(NUM.findall(text)) & deny)
        assert not hit, (t, hit)
        for lab in BO3_LABELS:
            assert lab.lower() not in text.lower(), (t, lab)


def hv():
    t3, t12 = PMAPS["windows"]["3m"]["overall"], PMAPS["windows"]["12m"]["overall"]
    return {"kpr3": f"{t3['kills'] / t3['rounds']:.3f}", "maps3": t3["maps"], "kpr12": f"{t12['kills'] / t12['rounds']:.3f}",
            "hs3": f"{100 * t3['hs'] / t3['hs_kills']:.1f}%", "maps12": t12["maps"], "newest": PMAPS["newest_map"]}


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_real_stadodo_docs_show_no_bo3_value_on_any_tab(browser, env, monkeypatch, size):  # noqa: F811
    c, p = open_line(browser, env, monkeypatch, size, {"pp:17255714": KILLS, "pp:17255719": HS})
    try:
        for raw, label in ((KILLS, "Kills"), (HS, "Headshots")):
            open_prop(p, label)
            texts = read_card(p)
            check_no_bo3(raw, texts)
            # the v3 factor rows: the HLTV equivalents from /api/upgrade/player-maps, the HLTV source said
            f, want = factor_rows(p), hv()
            assert f["Kills per round, official maps"].startswith(want["kpr3"]) and "HLTV" in f["Kills per round, official maps"], f
            assert want["kpr12"] in f["Kills per round, official maps"] and f"{want['maps3']} maps" in f["Kills per round, official maps"], f
            assert f["Maps in his sample"].startswith(str(want["maps12"])) and "HLTV" in f["Maps in his sample"], f
            assert f["Days since his last map"].startswith("8 ") and want["newest"] in f["Days since his last map"], f   # as_of 11-20 - 11-12
            if label == "Headshots":
                assert f["Headshot rate"].startswith(want["hs3"]), f
            assert not any(k in f for k in ("Opening kills", "Trade kills", "Recent kills per round")), f
            # hit counts: HLTV series only. Kills: the verified series (3/10 Over, as the saved L10). Headshots: HLTV did not reproduce the
            # saved L10 and this doc predates the server's HLTV series, so no count is shown (never the saved BO3.gg + HLTV tally)
            hits = p.evaluate("[...document.querySelectorAll('#hub-decision .v3p-hits b')].map(b => b.textContent)")
            assert hits[1] == ("3/10" if label == "Kills" else "—"), hits
            no_h_scroll(p)
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("variant", ["mutated", "from_inputs"])
def test_bo3_fields_stadodo_does_not_carry_are_dropped_too(browser, env, monkeypatch, variant):  # noqa: F811
    kills = mutated(KILLS) if variant == "mutated" else from_inputs(KILLS)
    hs = with_hltv_series(mutated(HS) if variant == "mutated" else from_inputs(HS))
    c, p = open_line(browser, env, monkeypatch, PHONE, {"pp:17255714": kills, "pp:17255719": hs})
    try:
        for raw, label in ((kills, "Kills"), (hs, "Headshots")):
            open_prop(p, label)
            texts = read_card(p)
            check_no_bo3(raw, texts)
            body = texts["stats"].lower()
            assert "vs similar-ranked opponents" not in body and "awper" not in body, body[:300]    # BO3.gg rank band and AWP note
            f = factor_rows(p)
            if variant == "mutated":
                assert f.get("Days since his last map", "").endswith("| Stake rule: max 1U"), f      # the grade's rule, not the HLTV 8 days
            if label == "Headshots":
                ser = raw["history"]["hltv_series"]
                over = sum(1 for g in ser[:10] if g["value"] > raw["record"]["offer"]["line"])
                hits = p.evaluate("[...document.querySelectorAll('#hub-decision .v3p-hits b')].map(b => b.textContent)")
                assert hits[1] in (f"{over}/10", f"{10 - over}/10"), hits      # Grader's side (Under) on the HLTV series
        assert not p.errors, p.errors
    finally:
        c.close()


def test_genie_payload_on_v3_carries_no_bo3_number():
    for raw in (KILLS, HS, mutated(KILLS), from_inputs(HS)):
        deny = bo3_values(raw) - hltv_allowed(v3_hltv.scrub(raw))
        doc = v3_hltv.scrub(raw)
        said = []
        for q in ("How often did this line hit?", "What are his stats? kpr rating adr kast", "What is the projection?", "grade and stake"):
            for side in ("over", "under"):
                said += workspace_genie.answer(doc, q, side)["sentences"]
        hit = sorted(set(NUM.findall(" ".join(said))) & deny)
        assert not hit, (hit, said)
        assert raw["cs2_card"].get("stats") and not doc["cs2_card"]["stats"]
        assert not [r for r in doc["context"]["rows"] if r.get("group") == "Saved with this forecast"]
    # the scrub is display only: the record's grade, chance, stake and projection are untouched
    d = v3_hltv.scrub(HS)
    assert d["record"]["scout"] == HS["record"]["scout"] and d["grade_inputs"]["chance"] == HS["grade_inputs"]["chance"]
    assert d["grade_inputs"]["projection"] == HS["grade_inputs"]["projection"] and d["grade_inputs"]["stake"] == HS["grade_inputs"]["stake"]
    assert v3_hltv.scrub({"record": {"sport": "lol"}, "x": 1}) == {"record": {"sport": "lol"}, "x": 1}


@pytest.mark.parametrize("how", ["pending", 500])
def test_no_saved_l10_while_the_doc_loads_or_after_it_failed(browser, env, monkeypatch, how):  # noqa: F811
    """before the doc arrives (or when /line fails) the hub has only the board record: its saved L10 / recent average (BO3.gg + HLTV)
    must not show on the v3 CS2 card either"""
    hold = {"pp:17255714": how}
    c, p = open_line(browser, env, monkeypatch, PHONE, {"pp:17255714": KILLS, "pp:17255719": HS}, hold=hold)
    try:
        p.locator("#hub-rows tr[data-record]", has_text="Kills").first.locator(".h-player").first.click()
        p.wait_for_selector("#hub-detail .h-why", timeout=20000)
        if how == 500:
            p.wait_for_selector("#hub-detail .h-warning", timeout=20000)
        else:
            p.wait_for_timeout(800)
            assert hold.get("routes"), "the /line read was not held"
        texts = {}
        for t in ("overview", "ask"):
            p.locator(f'#hub-detail [role=tab][data-hub=card-tab][data-id={t}]').click()
            p.wait_for_timeout(300)
            p.evaluate("document.querySelectorAll('#hub-detail details').forEach(d => d.open = true)")
            texts[t] = p.locator("#hub-card").inner_text()
        for t, text in texts.items():
            assert "of the last" not in text and "Recent average" not in text and "26.2" not in text, (t, text[:800])
        for r, d in hold.get("routes") or []:
            r.fulfill(status=200, content_type="application/json", body=json.dumps(fresh(d)))
        assert not p.errors, p.errors
    finally:
        c.close()


@pytest.mark.parametrize("size", [PHONE, DESK], ids=["390", "1280"])
def test_board_row_l10_is_the_hltv_count_the_card_shows(browser, env, monkeypatch, size):  # noqa: F811
    """sweep cs2m-1: the board's saved L10 (BO3.gg + HLTV, here 7 Over of 10) is not reproduced by HLTV (3 of 10). The v3 row's L10 is the
    shared pass's HLTV count (/hits l10), the same series the card counts and the row's L5 / L20 come from - never the saved one."""
    import tests.test_v3_hltv_only_browser as me
    b = json.loads(BOARD)
    for x in b["records"]:
        if x["offer"]["offer_id"] == "pp:17255714":
            x["history"]["l10"] = {"over": 7, "under": 3, "push": 0, "n": 10}           # the saved tally HLTV does not reproduce
    monkeypatch.setattr(me, "BOARD", json.dumps(b))
    ser, line = KILLS["history"]["series"], KILLS["record"]["offer"]["line"]
    tal = lambda n: {"over": sum(g["value"] > line for g in ser[:n]), "under": sum(g["value"] < line for g in ser[:n]),
                     "push": sum(g["value"] == line for g in ser[:n]), "n": len(ser[:n])}
    hits = {"rows": {"prizepicks|pp:17255714": {"line": line, "l5": tal(5), "l10": tal(10), "l20": tal(20)}}, "complete": True}
    assert tal(10)["over"] == 3
    c, p = None, None
    real_context = me.context

    def ctx_with_hits(*a, **k):
        cx = real_context(*a, **k)
        cx.route("**/api/upgrade/hits*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(hits)))
        return cx
    monkeypatch.setattr(me, "context", ctx_with_hits)
    try:
        c, p = open_line(browser, env, monkeypatch, size, {"pp:17255714": KILLS, "pp:17255719": HS})
        row = p.locator("#hub-rows tr[data-record]", has_text="Kills").first
        p.wait_for_function("(() => { const e = [...document.querySelectorAll('#hub-rows tr[data-record]')].find(r => /Kills/.test(r.textContent));"
                            " const c = e && e.querySelector('.h-s-l10 .h-hit'); return c && !c.classList.contains('h-hit-wait'); })()", timeout=20000)
        cell = row.locator(".h-s-l10 .h-hit")
        label = cell.get_attribute("aria-label") or ""
        assert "3 Over of the last 10" in label and "7 Over" not in label, label
        assert "2 Over of the last 5" in (row.locator(".h-s-l5 .h-hit").get_attribute("aria-label") or "")
        assert not p.errors, p.errors
    finally:
        if c:
            c.close()
