"""lc-1: CS2 and LoL letter containment (owner, Scout upgrade prompt v2 Phase 0 item 4; absolute rule "Never force letters on").

Brief: "default to containing CS2/LoL letters until Phase 1 calibration passes". SCOUT_LETTER_CONTAINMENT ships OFF until the
owner confirms (coordinator 2026-11-05: it conflicts with the standing "never turn B into Lean as a shortcut" rule, so it
needs an explicit yes). DISPLAY ONLY: grades, chances, stakes and every forecast log stay exactly as computed and logged
(picklog, learning, the LoL worker). Only what members see changes.

Who decides (gradecore/containment.py ct-3, the shared rule - no second policy here):
  1. the owner's switch SCOUT_CONTAIN_CS2 / SCOUT_CONTAIN_LOL = 1 contains. It can only contain: 0 / off is ignored;
  2. SCOUT_LETTER_CONTAINMENT (default OFF: letters show as before; 1 / on turns the check's containment on). The owner's
     switch contains either way. The job below runs either way, so the verdicts and their log are ready when the flag is
     turned on;
  3. the state file DATA_DIR/letter_containment.json, written by the periodic job below with gradecore apply_verdicts:
     gradecore.containment.verdict on the last 300+ settled legs - |mean stated - hit| <= 2.5 points AND a match-clustered
     EV interval wholly above 0 (plus ct-3's per-side and per-stat checks). CS2 legs: the stored P1 scorecard export, on
     the member Results rule (favoured side at 57%+, the same Map 1 hold). LoL legs: the LoL worker's settled pre-match
     forecasts on lines that offered Scout's side (learning.json containment_rows). A leg counts only with a known
     break-even (gradecore.payouts: Underdog standard; PrizePicks power, verified by owner sign-off 2026-11-05);
  4. with the flag on and nothing known (no file yet, an unreadable file, the job not run): contained.
The job runs in its own thread every REFRESH_S seconds; a request only reads the cached state (gradecore re-checks the
file's mtime every 30 s). A source that cannot be read changes nothing (a transient failure never releases or contains).

What a contained market shows on every member surface: the chance and the side as "Lean Over / Lean Under", no letter,
0U stake, and one banner per contained sport: "<Sport> letters are held until stated chances match results."
Never "No bet". Slips: a held leg has no rating (workspace_rules: no EV, no slip header; the generators take rated legs
only)."""
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

VERSION = "lc-1"
FLAG = "SCOUT_LETTER_CONTAINMENT"
SPORTS = ("cs2", "lol")
LABEL = {"cs2": "CS2", "lol": "LoL"}
STAT_LABEL = {"kills": "Kills", "hs": "Headshots", "assists": "Assists", "deaths": "Deaths"}
LETTERS = ("A+", "A", "A-", "B+", "B", "B-")
FILE_NAME = "letter_containment.json"
REFRESH_S = 6 * 3600
FIRST_DELAY_S = 90
_ON = {"1", "on", "true", "yes"}

_JOB = {"thread": None, "at": None, "results": None, "applied": None, "errors": {}, "runs": 0}
_LOCK = threading.Lock()


def enabled():
    return os.environ.get(FLAG, "").strip().lower() in _ON


def configure(data_dir):
    """Point gradecore's containment state at this service's data folder (gradecore knows no sport's folder)."""
    from gradecore import containment as gc
    gc.set_path(Path(data_dir) / FILE_NAME)


def state(sport):
    """-> {"contained", "stats", "source"}: owner (switch 1) | flag off | check (the job's file) | default (nothing known).
    A dict read on the request path."""
    if sport not in SPORTS:
        raise ValueError(f"unknown sport {sport!r}")
    from gradecore import containment as gc
    st = gc.state(sport)
    if st["source"] == "env":
        return {"contained": True, "stats": [], "source": "owner"}
    if _calibration_held(sport):
        return {"contained": True, "stats": [], "source": "calibration"}
    if not enabled():
        return {"contained": False, "stats": [], "source": "flag off"}
    return {"contained": bool(st["contained"]), "stats": list(st.get("stats") or []),
            "source": "check" if st["source"] == "file" else "default"}


def _calibration_held(sport):
    """ec-1 (gradecore/chance_cal.py): a game whose calibrated chances failed their held-out test keeps the calibrated chance
    but holds its letters - independent of SCOUT_LETTER_CONTAINMENT (owner 2026-11-05). Never raises."""
    try:
        from gradecore import chance_cal
        return chance_cal.held(sport)
    except Exception:                                   # noqa: BLE001 - unreadable = nothing held by this source
        return False


def held(st, stat=None):
    """True when this market's letter is held under the state st."""
    return bool(st and (st.get("contained") or (stat is not None and stat in (st.get("stats") or ()))))


def contained(sport, stat=None):
    return held(state(sport), stat)


def banner(sport, st=None):
    """The one plain-fact banner for a contained sport, or None."""
    st = state(sport) if st is None else st
    if st.get("contained"):
        return f"{LABEL[sport]} letters are held until stated chances match results."
    if st.get("stats"):
        names = " and ".join(STAT_LABEL.get(s, s) for s in st["stats"])
        return f"{LABEL[sport]} {names} letters are held until stated chances match results."
    return None


def hold_info(sport, st=None):
    """{"sport", "contained", "stats", "banner"} when anything is held, else None (the member payload's letter_hold)."""
    st = state(sport) if st is None else st
    b = banner(sport, st)
    return None if b is None else {"sport": sport, "contained": bool(st.get("contained")), "stats": list(st.get("stats") or []),
                                   "banner": b, "rule": VERSION}


def key(sport):
    """A small cache key for payloads built once and reused (the LoL member board): changes when the state does."""
    st = state(sport)
    return (bool(st["contained"]), tuple(st["stats"]))


# ------------------------------------------------------------------------------------------------ CS2 display masks
def _cs2_lean(r):
    """A copy of one CS2 verdict / board row / ladder chip with no letter and 0 units. The chance and side stay."""
    x = dict(r)
    for k in ("units_full", "cap_n", "cap_reason", "exact_grade"):
        x.pop(k, None)
    x.update(grade="LEAN", model_grade="LEAN", units=0.0, stake_reason=None, letter_held=True)
    return x


def _cs2_maskable(r):
    return isinstance(r, dict) and bool(r.get("side")) and r.get("state") != "SCOUT" and not r.get("shadow")


def cs2_row(r, st, prop=None):
    if not _cs2_maskable(r) or not held(st, prop if prop is not None else r.get("prop")):
        return r
    return _cs2_lean(r)


def cs2_rows(rows, st=None):
    st = state("cs2") if st is None else st
    if not held(st) and not st.get("stats"):
        return rows
    return [cs2_row(r, st) for r in rows or []]


def cs2_board(doc):
    """board_payload(book) as members see it: held rows as Lean, 0U; letter_hold carries the banner. The board itself, the
    logged forecasts and BOARD are untouched (a new dict)."""
    if not isinstance(doc, dict):
        return doc
    st = state("cs2")
    info = hold_info("cs2", st)
    if info is None:
        return doc
    return dict(doc, rows=[cs2_row(r, st) for r in doc.get("rows") or []], letter_hold=info)


def cs2_card(res):
    """A member card (/api/grade) with no letter on a held market: the verdict, both sides' grades in the grade block and
    the 'Nearby lines' chips read Lean, 0U. New dicts only (the pick log row was built before this)."""
    if not isinstance(res, dict):
        return res
    st = state("cs2")
    g = res.get("grade") if isinstance(res.get("grade"), dict) else {}
    prop = g.get("prop")
    if not held(st, prop):
        return res
    out = dict(res, letter_hold=hold_info("cs2", st))
    if _cs2_maskable(res.get("verdict")):
        out["verdict"] = _cs2_lean(res["verdict"])
    if g:
        g2 = dict(g)
        for k in ("grade", "grade_less"):
            if g2.get(k) in LETTERS + ("C",):
                g2[k] = "LEAN"
        for k in ("units", "units_less"):
            if k in g2:
                g2[k] = 0.0
        for k in ("units_free", "units_free_less"):
            g2.pop(k, None)
        out["grade"] = g2
    if isinstance(res.get("ladder"), list):
        out["ladder"] = [_cs2_lean(x) if _cs2_maskable(x) else x for x in res["ladder"]]
    return out


def cs2_slip_records(obj, st=None):
    """Stored studio batches / history as members see them while CS2 is held: each leg's letter reads Lean, 0U."""
    st = state("cs2") if st is None else st
    if not held(st) and not st.get("stats"):
        return obj

    def walk(v):
        if isinstance(v, list):
            return [walk(x) for x in v]
        if not isinstance(v, dict):
            return v
        out = {k: walk(x) for k, x in v.items() if k != "legs"}
        if isinstance(v.get("legs"), list):
            legs = []
            for l in v["legs"]:
                if isinstance(l, dict) and held(st, l.get("prop")) and (l.get("grade") in LETTERS or l.get("units")):
                    l = dict(l, grade="LEAN", units=0.0, letter_held=True)
                    l.pop("model_grade", None)
                    l.pop("exact_grade", None)
                legs.append(walk(l) if isinstance(l, dict) else l)
            out["legs"] = legs
        return out
    return walk(obj)


# ------------------------------------------------------------------------------------------------ LoL display masks
def lol_member_doc(doc):
    """The LoL member board (server._lol_member_rebuild's dict) as members see it while LoL is held: letters read Lean
    (the page's existing Lean wording), units stay 0, the worker's recommended slips are not offered (held legs have no
    rating), letter_hold carries the banner."""
    st = state("lol")
    info = hold_info("lol", st)
    if info is None:                                    # the market's stat travels only to here
        return dict(doc, match_tables=[dict(t, markets=[{k: v for k, v in m.items() if k != "stat"} for m in t.get("markets") or []])
                                       for t in doc.get("match_tables") or []])
    stat_of = {}
    rows = []
    for x in doc.get("rows") or []:
        stat_of[x.get("key")] = x.get("stat")
        if held(st, x.get("stat")) and x.get("grade") in LETTERS:
            x = dict(x, grade="Lean", units=0.0, letter_held=True)
        rows.append(x)
    tables = []
    for t in doc.get("match_tables") or []:
        mk = []
        for m in t.get("markets") or []:
            if held(st, m.get("stat")):
                m = dict(m, lines=[dict(ln, grade="Lean") if ln.get("grade") in LETTERS else ln for ln in m.get("lines") or []])
            mk.append({k: v for k, v in m.items() if k != "stat"})
        tables.append(dict(t, markets=mk))
    slips = {}
    for pl, d in (doc.get("slips") or {}).items():
        keep = [s for s in d.get("recommended") or [] if not any(held(st, stat_of.get(l.get("key"))) for l in s.get("legs") or [])]
        why = list(d.get("why_none") or [])
        if len(keep) < len(d.get("recommended") or []):
            why = [{"text": info["banner"], "picks": 0}] + why
        slips[pl] = dict(d, recommended=keep, why_none=why)
    return dict(doc, rows=rows, match_tables=tables, slips=slips, letter_hold=info)


# ------------------------------------------------------------------------------------------------ the periodic check
def _break_even(book):
    from gradecore import payouts
    try:
        return payouts.break_even(str(book or "").lower(), None, 2)
    except Exception:                                   # noqa: BLE001
        return None


def cs2_legs(export):
    """Settled CS2 legs, oldest first, from the stored P1 export on the member Results rule (results_stated.rule_picks: the
    favoured side when its printed chance is 57%+, the same Map 1 hold). Pushes are counted apart by the export."""
    import results_view
    rows = (export or {}).get("rows") or []
    if results_view.map1_staked_settled(export) < results_view.MAP1_RESULTS_MIN_STAKED:
        rows = [r for r in rows if (r.get("scope") or "12") != "1"]
    out = []
    for r in rows:
        p, over = r.get("p"), r.get("over")
        if p is None or over not in (0, 1) or max(p, 1 - p) < results_view.RULE:
            continue
        win = over if p >= 0.5 else 1 - over
        out.append({"p": max(p, 1 - p), "status": "win" if win else "loss", "match_key": str(r.get("match")),
                    "stat": r.get("prop"), "side": "over" if p >= 0.5 else "under", "book": r.get("book"),
                    "break_even": _break_even(r.get("book") or "underdog"), "at": r.get("start")})
    return out


def lol_legs(doc):
    """Settled LoL legs, oldest first, from the worker's learning.json (containment_rows: saved pre-match forecasts on lines
    that offered Scout's side, the shown chance of that side, graded or pushed). Absent (an older worker) -> []."""
    out = []
    for r in (doc or {}).get("containment_rows") or []:
        if not isinstance(r, dict) or r.get("status") not in ("win", "loss", "push") or r.get("p") is None:
            continue
        out.append({"p": float(r["p"]), "status": r["status"], "match_key": str(r.get("match_key")), "stat": r.get("stat"),
                    "side": r.get("side"), "book": r.get("platform"), "break_even": _break_even(r.get("platform")),
                    "at": r.get("at")})
    out.sort(key=lambda r: str(r.get("at") or ""))
    return out


def summary(rows):
    """Plain counts for the admin view: legs, legs with a known break-even, stated vs hit on the decided ones."""
    d = [r for r in rows if r["status"] in ("win", "loss")]
    w = [r for r in d if r.get("break_even")]
    f = lambda xs, k: round(sum(x[k] for x in xs) / len(xs), 4) if xs else None
    return {"legs": len(rows), "decided": len(d), "decided_with_break_even": len(w),
            "stated": f(d, "p"), "hit": round(sum(1 for x in d if x["status"] == "win") / len(d), 4) if d else None}


def compute(srv):
    """{sport: (rows, error)} from each source; a source that cannot be read gives (None, error)."""
    out = {}
    try:
        out["cs2"] = (cs2_legs(getattr(srv, "_p1_export_printed", srv._p1_export)()), None)   # ec-1: printed chances
    except Exception as e:                              # noqa: BLE001
        out["cs2"] = (None, f"{type(e).__name__}: {str(e)[:160]}")
    try:
        doc = json.loads((Path(srv.LOL_CENTER) / "learning.json").read_text(encoding="utf-8"))
        out["lol"] = (lol_legs(doc), None)
    except (OSError, ValueError) as e:
        out["lol"] = (None, f"{type(e).__name__}: {str(e)[:160]}")
    return out


def run_once(srv, now=None, log=print):
    """One pass: read both sources, the gradecore verdict per sport, apply_verdicts (decide, log, atomic write). A sport whose
    source failed is left exactly as it was."""
    from gradecore import containment as gc
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        got = compute(srv)
        results, sums, errors = {}, {}, {}
        for sport, (rows, err) in got.items():
            if rows is None:
                errors[sport] = err
                continue
            v = gc.verdict(rows)
            results[sport] = v
            sums[sport] = summary(rows)
        applied = gc.apply_verdicts(results, now.strftime("%Y-%m-%d"), f"letter containment job {VERSION}") if results else {}
        _JOB.update(at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), errors=errors, runs=_JOB["runs"] + 1,
                    results={s: {k: v.get(k) for k in ("verdict", "why", "n", "window", "matches", "hit", "stated", "gap",
                                                       "hit_ci", "ev_ci", "sides", "stats_failed")} | {"legs": sums[s]}
                             for s, v in results.items()},
                    applied=applied)
    try:
        for s in SPORTS:
            st = state(s)
            r = (_JOB["results"] or {}).get(s) or {}
            log(f"[letters {VERSION}] {LABEL[s]}: verdict {r.get('verdict') or 'not read'} (n {r.get('n')}, stated {r.get('stated')}, "
                f"hit {r.get('hit')}); letters {'held' if held(st) else 'held for ' + ', '.join(st['stats']) if st['stats'] else 'shown'} "
                f"(source {st['source']}){' - source error: ' + errors[s] if s in errors else ''}", flush=True)
    except Exception:                                   # noqa: BLE001
        pass
    return dict(_JOB)


def _loop(srv):
    time.sleep(FIRST_DELAY_S)
    while True:
        try:
            run_once(srv)
        except Exception:                               # noqa: BLE001 - the job never takes the server down
            traceback.print_exc()
        time.sleep(REFRESH_S)


def start(srv, data_dir):
    """configure + the periodic job thread (once per process)."""
    configure(data_dir)
    if _JOB["thread"] is None:
        _JOB["thread"] = threading.Thread(target=_loop, args=(srv,), daemon=True, name="letter-containment")
        _JOB["thread"].start()


def snapshot():
    """Admin view: each sport's state, what the owner's switches asked and what applied, the last job's verdicts."""
    from gradecore import containment as gc
    return {"version": VERSION, "flag": {FLAG: os.environ.get(FLAG), "on": enabled()},
            "sports": {s: dict(state(s), banner=banner(s), switch=gc.env_request(s)) for s in SPORTS},
            "job": {k: _JOB[k] for k in ("at", "runs", "results", "applied", "errors")}, "refresh_s": REFRESH_S}
