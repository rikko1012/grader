"""Audit 2026-11-05, esports data group.

B1  Underdog CS2 lines read "UD · 32h" while the feed was a minute old: a row's seen_at was Underdog's own updated_at (when the line
    last CHANGED). Now seen_at is Scout's read time; a failed read keeps the last good rows with their own (ageing) seen_at; a feed
    with no good read for 2 h is not graded as current (its lines keep their last grade).
A2/B4  Sleeper / Dabble rows: L5 / L10 / L20 (H2H / Season) from the game log at the line shown, the board player's full research doc
    (history, stats, H2H, Ask) at that line, and Scout's chance at a different line read from the SAME saved distribution
    (CS2 grader dist / LoL saved NB) - no new model, no shrink, no letter, no stake.
No network: every feed is a fake."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import book_providers as bp
import grader
import line_chance
import server
import underdog
import workspace_api
import workspace_books as wb
from tests.test_workspace_api import env, get, post, records, fresh  # noqa: F401

ROOT = Path(server.__file__).parent


# ------------------------------------------------------------------------------------------------------------- B1 Underdog age

class _Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def _ud_body(updated="2026-11-04T19:50:15.168Z"):
    return {"appearances": [{"id": "a1", "player_id": "p1", "match_id": 5, "team_id": "t1"}],
            "players": [{"id": "p1", "first_name": "", "last_name": "jame"}],
            "games": [{"id": 5, "title": "NAVI vs PARIVISION", "full_team_names_title": "Natus Vincere vs PARIVISION", "home_team_id": "t1",
                       "away_team_id": "t2", "scheduled_at": "2099-11-06T06:30:00Z"}],
            "over_under_lines": [{"id": "L1", "status": "active", "stat_value": "26.5", "updated_at": updated, "line_type": "balanced",
                                  "over_under": {"appearance_stat": {"appearance_id": "a1", "stat": "kills_on_maps_1_2", "display_stat": "Kills on Maps 1+2"}},
                                  "options": [{"choice": "higher", "status": "active", "payout_multiplier": "1.0", "decimal_price": "1.9"},
                                              {"choice": "lower", "status": "active", "payout_multiplier": "1.0", "decimal_price": "1.9"}]}]}


@pytest.fixture
def ud_state(monkeypatch):
    monkeypatch.setattr(underdog, "_BOARD", {"at": 0.0, "rows": [], "error": None})
    monkeypatch.setattr(underdog, "_log_moves", lambda rows: None)
    monkeypatch.setattr(underdog, "_opening", lambda rows, now=None: None)
    yield


def test_underdog_row_seen_at_is_scouts_read_time_not_the_line_change_time(ud_state, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(_ud_body()))
    t0 = time.time()
    underdog._BUSY[0] = True
    underdog._refresh()
    r = underdog._BOARD["rows"][0]
    assert r["updated_at"] == "2026-11-04T19:50:15.168Z"                         # Underdog's change time stays as it was
    seen = datetime.fromisoformat(r["seen_at"]).timestamp()
    assert abs(seen - t0) < 5                                                     # the read time
    rec = workspace_api.record_of(dict(r, book="underdog", side="More", p=0.6, graded_model="M"))
    assert rec["offer"]["seen_at"] == r["seen_at"]                                # the row chip / Lines tab age = time since Scout's read


def test_underdog_failed_read_keeps_last_rows_and_their_age(ud_state, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(_ud_body()))
    underdog._BUSY[0] = True
    underdog._refresh()
    seen = underdog._BOARD["rows"][0]["seen_at"]

    def boom(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(requests, "get", boom)
    underdog._BUSY[0] = True
    underdog._refresh()
    assert underdog._BOARD["error"] and underdog._BOARD["rows"][0]["seen_at"] == seen    # last good kept, its age keeps growing


def test_feed_older_than_two_hours_is_not_graded_as_current():
    now = time.time()
    assert server.feed_current({"at": now - 60}, now) and server.feed_current({"at": now - 7100}, now)
    assert not server.feed_current({"at": now - 7300}, now)
    assert not server.feed_current({"at": 0.0}, now) and not server.feed_current({}, now)
    iso = datetime.fromtimestamp(now - 120, timezone.utc).isoformat()
    assert server.feed_current({"at": iso}, now) and server.feed_age_s({"at": iso}, now) == pytest.approx(120, abs=1)
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    loop = src[src.index("def board_forever():"):src.index("def one(r):", src.index("def board_forever():"))]
    assert "current = {bk_: feed_current(fb_) for bk_, fb_ in feeds.items()}" in loop and 'current.get(r.get("book") or "underdog", True)' in loop


def test_line_history_keeps_underdogs_change_time_for_the_current_line():
    import workspace_linehist as lh
    src = Path(lh.__file__).read_text(encoding="utf-8")
    assert 'x.get("updated_at") if ud else None' in src


def test_live_audit_shape_ud_rows_no_longer_read_32h(env):
    """The fixture's Underdog rows carry an updated_at 20 min old; with a fresh seen_at the record shows the read time."""
    for r in env.rows:
        if r["book"] == "underdog":
            r["updated_at"] = "2026-11-04T19:50:15Z"
            r["seen_at"] = underdog.stamp()
    recs = records(env)
    ud = recs[("vargo", "underdog")]
    assert abs(datetime.fromisoformat(ud["offer"]["seen_at"]).timestamp() - time.time()) < 60


# ------------------------------------------------------------------------------------------------------------- line_chance

def _cs2_row(lam=41.0, sd=6.0, line=38.5, prop="kills"):
    o, u, q = grader.p_three(line, lam, sd)
    pm = min(max(o / (o + u), 1 - grader.CONFIG["p_cap_kills"]), grader.CONFIG["p_cap_kills"]) if prop == "kills" else o / (o + u)
    side = "More" if round(pm, 3) > round(1 - pm, 3) else "Less"
    return {"id": "L1", "prop": prop, "line": line, "graded_line": line, "side": side, "p": round(pm if side == "More" else 1 - pm, 3),
            "model_outcomes": {"over": round(o, 4), "under": round(u, 4), "push": round(q, 4)}, "dist": {"kind": "normal", "lam": lam, "sd": sd}}


def test_cs2_chance_at_another_line_is_the_graders_own_read_of_the_same_distribution():
    x = _cs2_row()
    for ln in (36.5, 37.5, 39.5, 40.5, 38.0):
        got = line_chance.cs2_at(x, ln)
        o, u, q = grader.p_three(ln, 41.0, 6.0)
        pm = min(max(o / (o + u), 1 - 0.78), 0.78)
        assert got["p_over"] == round(pm, 3) and got["p_under"] == round(1 - pm, 3) and got["p_push"] == pytest.approx(q, abs=1e-3)
        assert got["side"] == ("over" if round(pm, 3) > round(1 - pm, 3) else "under")
        assert got["p_side"] == (got["p_over"] if got["side"] == "over" else got["p_under"])
    assert line_chance.cs2_at(x, 38.5)["p_side"] == x["p"]                         # at its own line: the printed chance, exactly
    assert line_chance.cs2_at(x, 20.5)["p_over"] == 0.78                           # the kills cap, as the grader applies it


def test_cs2_chance_refuses_whatever_is_not_the_saved_distribution():
    x = _cs2_row()
    assert line_chance.cs2_at(dict(x, dist=None), 37.5) is None                     # no saved distribution
    assert line_chance.cs2_at(dict(x, model_outcomes={"over": 0.3, "under": 0.7, "push": 0}), 37.5) is None   # not this grade's
    assert line_chance.cs2_at(dict(x, p=round(x["p"] - 0.03, 3)), 37.5) is None    # printed chance not this distribution's read
    assert line_chance.cs2_at(dict(x, p_raw=x["p"], cal_v="ec-1:x"), 37.5) is None  # a display layer is never mixed with a raw read
    assert line_chance.cs2_at(dict(x, side="Less"), 37.5) is None


def test_cs2_headshot_distribution_reads_too():
    import perf
    pmf = perf.hs_pmf(40.0, "12", 0.45, c=None)
    o = sum(v for k, v in pmf.items() if k > 17.5)
    u = sum(v for k, v in pmf.items() if k < 17.5)
    p = o / (o + u)
    x = {"id": "H", "prop": "hs", "line": 17.5, "graded_line": 17.5, "side": "More" if p > 0.5 else "Less", "p": round(max(p, 1 - p), 3),
         "model_outcomes": {"over": round(o, 4), "under": round(u, 4), "push": 0.0},
         "dist": line_chance.compact_dist({"kind": "hs_bb", "lam_kills": 40.0, "hs_per_kill": 0.45, "scope": "12", "c": None})}
    got = line_chance.cs2_at(x, 16.5)
    o2 = sum(v for k, v in pmf.items() if k > 16.5)
    u2 = sum(v for k, v in pmf.items() if k < 16.5)
    assert got and got["p_over"] == round(o2 / (o2 + u2), 3)


def test_lol_chance_reads_the_saved_nb_and_refuses_a_mixture_or_calibrated_chance():
    o, u, q = line_chance.nb_probs(4.2, 9.0, 3.5)
    got = line_chance.lol_at(4.2, 9.0, 3.5, round(o, 4), round(u, 4), 4.5)
    o2, u2, q2 = line_chance.nb_probs(4.2, 9.0, 4.5)
    assert got["p_over"] == round(o2, 4) and got["p_under"] == round(u2, 4) and got["side"] == ("over" if o2 >= u2 else "under")
    assert line_chance.lol_at(4.2, 9.0, 3.5, round(o, 4) + 0.05, round(u, 4) - 0.05, 4.5) is None
    assert line_chance.lol_at(4.2, None, 3.5, o, u, 4.5) is None
    # the same formula as lol/model.py probs() (CS2 tests never import lol): the source lines match and the NB mean is lambda
    src = (ROOT / "lol" / "model.py").read_text(encoding="utf-8")
    assert "+ size * math.log(size / (size + mu)) + k * math.log(mu / (size + mu)))" in src
    assert "top = int(lam + 12 * math.sqrt(lam + lam * lam / size)) + 10" in src
    mean = sum(k * line_chance._nb_pmf(k, 4.2, 9.0) for k in range(200))
    assert mean == pytest.approx(4.2, abs=1e-9)


def test_line_chance_imports_no_lol_or_sports_package():
    import ast
    tree = ast.parse((ROOT / "line_chance.py").read_text(encoding="utf-8"))
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names} | \
            {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(str(x).split(".")[0] in ("lol", "sports") for x in names), names


# ------------------------------------------------------------------------------------------------------------- derive

def _rec(line_=38.5, p=0.61):
    return {"sport": "cs2", "key": f"cs2.e1.vargo.kills.maps12.{line_}", "event": {"id": "e1", "label": "A vs B", "start_utc": "2099-01-01T00:00:00+00:00"},
            "player": {"key": "vargo", "name": "vargo", "team": "Halcyon", "opponent": "Norrland"},
            "market": {"stat": "kills", "stat_label": "Kills", "period": "maps12", "period_label": "Maps 1-2", "line_type": "standard"},
            "offer": {"platform": "underdog", "platform_label": "Underdog", "offer_id": "o1", "line": line_},
            "scout": {"side": "over", "p_side": p, "p_over": p, "p_under": None, "p_push": 0.0, "projection": 40.0, "grade": "B+", "units": 1.0,
                      "stake_reason": "x", "hold": None, "model_version": "M", "read_rev": "r", "forecast_at": "t"},
            "history": {"l10": {"over": 6, "under": 4, "push": 0, "n": 10}, "avg": 39.1}, "state": {"kind": "ok"}}


HIST = {"l5": {"over": 3, "under": 2, "push": 0, "n": 5}, "l10": {"over": 7, "under": 3, "push": 0, "n": 10}, "l20": {"over": 12, "under": 8, "push": 0, "n": 20},
        "avg": 39.0, "recent": None}


def test_derive_other_line_gets_scouts_chance_at_that_line_no_letter_no_stake():
    ch = {"side": "under", "p_side": 0.52, "p_over": 0.48, "p_under": 0.52, "p_push": 0.0, "basis": "saved distribution"}
    r = wb.derive("sleeper", "Sleeper", "pickem", "cs2", [_rec()], 40.5, {"offered": True, "payout": 1.8}, {"offered": True, "payout": 1.76}, None, 1.0, "k",
                  read=lambda base, line: (ch, HIST))
    s = r["scout"]
    assert s["side"] == "under" and s["p_side"] == 0.52 and s["p_over"] == 0.48 and s["line_chance"] is True
    assert s["grade"] is None and s["units"] is None and s["stake_reason"] is None
    assert r["history"] == HIST and r["extra"]["line_chance"] is True and r["extra"]["same_line_as"] is None
    assert r["extra"]["base"] == {"platform": "underdog", "offer_id": "o1", "line": 38.5}


def test_derive_same_line_keeps_the_saved_read_and_saved_l10_adds_l5_l20():
    r = wb.derive("sleeper", "Sleeper", "pickem", "cs2", [_rec()], 38.5, {"offered": True}, {"offered": True}, None, 1.0, "k",
                  read=lambda base, line: (None, HIST))
    assert r["scout"]["grade"] == "B+" and r["scout"]["p_side"] == 0.61 and r["scout"]["units"] is None and "line_chance" not in r["scout"]
    assert r["history"]["l10"] == {"over": 6, "under": 4, "push": 0, "n": 10}       # the saved L10 at this exact line
    assert r["history"]["l5"] == HIST["l5"] and r["history"]["l20"] == HIST["l20"] and r["history"]["avg"] == 39.1


def test_derive_without_a_saved_distribution_shows_line_and_price_but_still_history():
    r = wb.derive("dabble", "Dabble", "pickem", "cs2", [_rec()], 36.5, {"offered": True, "price": 1.93}, {"offered": True, "price": 2.07}, None, 1.0, "k",
                  read=lambda base, line: (None, HIST))
    assert r["scout"]["side"] is None and r["scout"]["p_side"] is None and r["history"]["l5"] == HIST["l5"] and r["extra"]["line_chance"] is False


def test_derive_base_is_the_nearest_standard_line():
    a, b = _rec(38.5), _rec(36.5)
    b["offer"] = dict(b["offer"], platform="prizepicks", platform_label="PrizePicks", offer_id="pp:1")
    seen = []
    wb.derive("sleeper", "Sleeper", "pickem", "cs2", [a, b], 37.0, {"offered": True}, {"offered": True}, None, 1.0, "k",
              read=lambda base, line: seen.append(base["offer"]["offer_id"]) or (None, None))
    assert seen == ["pp:1"] or seen == ["o1"]                                       # equal distance: the platform order decides
    seen.clear()
    wb.derive("sleeper", "Sleeper", "pickem", "cs2", [a, b], 36.0, {"offered": True}, {"offered": True}, None, 1.0, "k",
              read=lambda base, line: seen.append(base["offer"]["offer_id"]) or (None, None))
    assert seen == ["pp:1"]


# ------------------------------------------------------------------------------------------------------------- HTTP end to end

def _maps(n=20, base=19):
    out = []
    for i in range(n):
        val = base + (i % 4)                                                    # two maps: 38, 40, 42, 44 ...
        for m in (1, 2):
            out.append({"match_id": f"hltv-{i}", "number": m, "date": f"2026-{10 - i // 28:02d}-{28 - i % 28:02d}", "kills": val, "headshots": 8,
                        "opponent": "Norrland" if i % 5 == 0 else f"Team {i}", "map": "Mirage"})
    return out


@pytest.fixture
def books(env, monkeypatch):
    for f in ("SCOUT_BOOK_SLEEPER", "SCOUT_BOOK_DABBLE", "SCOUT_BOOK_FANDUEL", "SCOUT_BOOK_BETRIVERS", "SCOUT_BOOK_ODDSAPI"):
        monkeypatch.delenv(f, raising=False)
    bp.reset()
    wb.reset()
    monkeypatch.setattr(bp, "start", lambda d: False)
    monkeypatch.setenv("SCOUT_BOOK_SLEEPER", "1")
    monkeypatch.setenv("SCOUT_BOARD_H2H", "1")
    monkeypatch.setattr(server.hltv, "recent_series", lambda *a, **k: _maps())
    x = env.by("vargo")                                                         # Underdog 38.5, graded from a normal(41, 6) distribution
    row = _cs2_row(41.0, 6.0, 38.5)
    for r in (x, env.by("vargo", "prizepicks")):
        r.update(p=row["p"], side=row["side"], model_outcomes=row["model_outcomes"])
    server.BOARD[x["id"]] = {"dist": row["dist"]}
    server.BOARD[env.by("vargo", "prizepicks")["id"]] = {"dist": row["dist"]}
    now = time.time()
    off = lambda line, sid: {"provider": "sleeper", "book": "sleeper", "sport": "cs2", "raw_stat": "kills_maps_1_2", "stat": "kills", "period": "maps12",
                             "subject_id": sid, "player": "vargo", "player_norm": "vargo", "team": "Halcyon", "team_norm": "halcyon", "game_id": "g", "line": line,
                             "over": {"offered": True, "payout": 1.8, "price": None}, "under": {"offered": True, "payout": 1.76, "price": None},
                             "source_at": now - 60, "observed_at": now}
    bp.state("sleeper").update(at=now, offers=[off(37.5, "7"), off(38.5, "7b")])
    fresh()
    yield env
    bp.reset()
    wb.reset()


def _sleeper(env):
    fresh()
    st, doc = get(env, "book-board?sport=cs2&book=sleeper")
    assert st == 200 and doc["feed"]["state"] == "ok", doc
    if any(not (r.get("history") or {}).get("l5") for r in doc["records"]):        # the first build may pass its read budget: cached now
        fresh()
        st, doc = get(env, "book-board?sport=cs2&book=sleeper")
    return {r["offer"]["line"]: r for r in doc["records"]}, doc


def test_sleeper_rows_carry_hit_rates_at_their_own_line_and_the_chance_at_that_line(books):
    by, doc = _sleeper(books)
    other, same = by[37.5], by[38.5]
    vals = [v["kills"] * 2 for v in _maps()[::2]]                                  # newest first, Maps 1-2 totals
    for r, ln in ((other, 37.5), (same, 38.5)):
        h = r["history"]
        assert h["l5"]["n"] == 5 and h["l5"]["over"] == sum(v > ln for v in vals[:5])
        assert h["l20"]["n"] == 20 and h["l20"]["over"] == sum(v > ln for v in vals[:20])
        assert h["h2h"]["opponent"] == "Norrland" and h["h2h"]["n"] == 4 and h["season"]["n"] >= 1
    assert other["history"]["l10"]["over"] == sum(v > 37.5 for v in vals[:10])     # L10 at 37.5 from the game log (no forecast at 37.5)
    assert same["history"]["l10"] == {"over": 6, "under": 4, "push": 0, "n": 10}   # the saved L10 at the graded line stays
    s = other["scout"]
    want = line_chance.cs2_at(_cs2_row(41.0, 6.0, 38.5), 37.5)
    assert s["line_chance"] and s["side"] == want["side"] and s["p_side"] == want["p_side"] and s["grade"] is None and s["units"] is None
    assert same["scout"]["grade"] is not None and "line_chance" not in same["scout"]   # same line: the saved read, unchanged
    assert doc["coverage"]["line_chance"] == 1 and doc["coverage"]["with_history"] == 2
    assert "dist" not in json.dumps(doc) and '"sd"' not in json.dumps(doc)       # the distribution itself never leaves the server


def test_board_payload_never_carries_the_distribution(books):
    st, doc = get(books, "board")
    assert st == 200 and '"dist"' not in json.dumps(doc)
    p = server.board_payload("underdog")
    assert all("dist" not in r for r in p["rows"])


def test_sleeper_line_opens_the_full_research_doc_at_its_own_line(books):
    by, _ = _sleeper(books)
    r = by[37.5]
    st, d = get(books, f"line?book=sleeper&id={r['offer']['offer_id']}")
    assert st == 200, d
    assert d["record"]["offer"]["offer_id"] == r["offer"]["offer_id"] and d["record"]["offer"]["line"] == 37.5
    assert d["record"]["scout"]["read_rev"] == r["scout"]["read_rev"] and d["record"]["offer"]["revision"] == r["offer"]["revision"]
    assert len(d["history"]["series"]) == 20 and d["forecast"] is None
    vals = [g["value"] for g in d["history"]["series"]]
    assert d["h2h"]["n"] == 4 and d["h2h"]["over"] == sum(v > 37.5 for g, v in zip(d["h2h"]["series"], [g["value"] for g in d["h2h"]["series"]]))
    assert {b["platform"] for b in d["books"]} >= {"underdog", "prizepicks", "sleeper"}
    assert d["context"] and d["market"]["available"] is False and vals
    st, d2 = get(books, f"line?book=sleeper&id={by[38.5]['offer']['offer_id']}")
    assert st == 200 and d2["forecast"] is not None                              # same line: the saved forecast behind that read
    assert get(books, "line?book=sleeper&id=x-nope")[0] == 404
    assert get(books, "line?book=fanduel&id=x-nope")[0] == 404                  # a book that is off


def test_ask_answers_about_the_sleeper_line(books, monkeypatch):
    import workspace_genie as genie
    monkeypatch.setenv("SCOUT_GENIE", "1")
    genie.reset()
    by, _ = _sleeper(books)
    r = by[37.5]
    d = {"sport": "cs2", "book": "sleeper", "id": r["offer"]["offer_id"], "revision": r["offer"]["revision"], "side": r["scout"]["side"],
         "question": "Which side should I pick?"}
    st, out = post(books, "genie", d)
    assert st == 200 and out["matched"], out
    assert any("chance at this line" in s for s in out["sentences"]), out["sentences"]
    st, out = post(books, "genie", dict(d, question="How often did this line hit?"))
    assert st == 200 and out["matched"] and out["removed_numbers"] == 0
    genie.reset()


def test_extra_lines_never_change_the_board_or_go_on_a_slip(books):
    before = records(books)
    _sleeper(books)
    assert records(books) == before
    js = (ROOT / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    assert "if(r.extra)return openCard(id,'overview')" in js                    # Info opens the research card (it only toasted)
    assert "'detail-add'(){const r=rec(H.detail);if(!r||r.extra)return;" in js   # never on a slip
    assert "const eligible=!r.extra&&" in js and "extraDetail(" not in js
    assert "Scout\\u2019s chance at this line" in js


# ------------------------------------------------------------------------------------------------------------- LoL

def test_lol_extra_line_reads_the_snapshot_series_and_saved_nb(monkeypatch):
    import workspace_lol
    o, u, q = line_chance.nb_probs(3.1, 7.0, 2.5)
    ser = {"P1": [{"date": f"2026-10-{20 - i:02d}", "opponent": "T1" if i < 2 else "G2", "best_of": 3,
                   "games": {1: {"kills": 2 + i % 3, "deaths": 1, "assists": 3, "cs": 200}}} for i in range(12)]}
    x = {"pid": "P1", "stat": "kills", "scope": "Map 1", "lam": 3.1, "nb": 7.0, "fc_line": 2.5, "line": 2.5, "po": round(o, 4), "pu": round(u, 4)}
    s = {"index": {("prizepicks", "k1"): x}, "series": ser}
    base = {"offer": {"platform": "prizepicks", "offer_id": "k1"}, "player": {"opponent": "T1"}}
    monkeypatch.setattr(workspace_lol, "PERIOD", dict(workspace_lol.PERIOD, **{"Map 1": ("map1", "Map 1", (1,))}))
    read = wb.reader(None, "lol", s)
    ch, h = read(base, 3.5)
    want = line_chance.lol_at(3.1, 7.0, 2.5, x["po"], x["pu"], 3.5)
    assert ch == want
    vals = [2 + i % 3 for i in range(12)]
    assert h["l5"] == {"over": sum(v > 3.5 for v in vals[:5]), "under": sum(v < 3.5 for v in vals[:5]), "push": 0, "n": 5} and h["l10"]["n"] == 10


def test_lol_saved_nb_reaches_the_workspace_index_never_a_member_record():
    """The LoL worker's board.json forecast carries its saved NB spread; the workspace index keeps it server side only."""
    fc = (ROOT / "lol" / "center" / "forecast.py").read_text(encoding="utf-8")
    assert '"nb_size": f["nb_size"] if "nb_size" in f.keys() else None' in fc
    wl = (ROOT / "workspace_lol.py").read_text(encoding="utf-8")
    assert '"nb": (r.get("forecast") or {}).get("nb_size")' in wl and '"nb": r.get("nb"), "lam": x.get("projection")' in wl
    rec_src = wl[wl.index("def record_of("):wl.index("def _build(")]
    assert "nb_size" not in rec_src and '"nb"' not in rec_src
