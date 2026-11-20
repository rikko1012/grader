"""Continuous-learning plumbing (worker only; REGISTER.md 7 + 9): settlement -> scorecard + diagnostic queue,
registered retrain trigger, champion / challenger registry. Nothing here promotes anything automatically.

on_settled(scon)       called by the worker after every settle cycle: every settled forecast of a model version gets a
                       scorecard row (once per forecast x settlement revision; a correction makes a new row and marks
                       the old one superseded) and a diagnostic-queue entry when it is a large miss (|PIT - 0.5| >=
                       0.45), a void, or a corrected settlement
scorecard(...)         per version x market x sport: n, distinct games, chosen-side hit rate vs mean probability,
                       log-loss / Brier at the offered line, pushes, voids - with game-clustered intervals
retrain_due(...)       cadence (7 days) or sample trigger (>= 2,000 NBA / >= 600 NFL new settled player-games since the
                       last fit) -> a CHALLENGER artifact (same frozen config, new data) in the registry
forward_gate(...)      registered gate F per sport x market (>= 300 settled props, >= 100 games, chosen-side calibration,
                       log-loss vs sharp-2 / B1) - evaluated only at the 300 / 600 / 1,000 windows; reports, never promotes
"""
from __future__ import annotations

import json
import heapq
import itertools
from contextlib import ExitStack, closing
from operator import itemgetter
import math
import sqlite3
import time
from datetime import timedelta

import numpy as np

from .. import config, settlement, store
from ..contracts import now_utc, parse_utc
from . import evaluate, forward

REVIEW_WINDOWS = (300, 600, 1000)
RETRAIN_DAYS = 7
SAMPLE_TRIGGER = {"nba": 2000, "nfl": 600}


def db():
    p = config.DATA_DIR / "model" / "learning.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS scorecard (forecast_id TEXT, settlement_id TEXT, model_version TEXT,
        league TEXT, stat TEXT, event_id TEXT, platform TEXT, line REAL, side TEXT, p_side REAL, p_over REAL,
        p_under REAL, p_push REAL, status TEXT, outcome TEXT, value REAL, pit REAL, forecast_at TEXT, settled_at TEXT,
        superseded INTEGER DEFAULT 0, PRIMARY KEY (forecast_id, settlement_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS diagnostics (forecast_id TEXT, settlement_id TEXT, kind TEXT, detail TEXT,
        created_at TEXT, reviewed INTEGER DEFAULT 0, PRIMARY KEY (forecast_id, settlement_id, kind))""")
    con.execute("""CREATE TABLE IF NOT EXISTS fits (sport TEXT, artifact_sha TEXT, fitted_at TEXT, data_as_of TEXT,
        settled_rows INTEGER, role TEXT, PRIMARY KEY (sport, artifact_sha))""")
    # per-event progress of on_settled (2026-11-08): the event's stored settlement + forecast records (count, newest
    # rid, rid sum) at its last full pass - an unchanged event is a no-op and is skipped without decoding anything
    con.execute("CREATE TABLE IF NOT EXISTS learn_events (event_id TEXT PRIMARY KEY, sig TEXT, scored_at TEXT)")
    # Separate additive table: immutable forecast/settlement IDs preserve replay.
    # Probabilities alone cannot tell whether the point projection improved.
    con.execute("""CREATE TABLE IF NOT EXISTS projection_reviews (
        forecast_id TEXT, settlement_id TEXT, projection REAL, actual REAL,
        error REAL, absolute_error REAL, squared_error REAL, reason TEXT,
        PRIMARY KEY(forecast_id,settlement_id))""")
    return con


def event_sig(scon, ev):
    """What one event's scoring reads from the store: its settlement and forecast records, as (count, newest rid,
    rid sum) each (records are append-only - a correction, a late result or a new forecast is a new row). Index-only."""
    out = []
    for kind in ("settlement", "forecast"):
        n, top, tot = scon.execute("SELECT COUNT(*), MAX(rid), SUM(rid) FROM records WHERE kind=? AND event_id=?",
                                   (kind, ev)).fetchone()
        out.append(f"{n}:{top}:{tot}")
    return "projection-review-1|" + "|".join(out)


def _pit(fc, value, pmf_row=None, lo=None):
    if pmf_row is None or value is None:
        return None
    p = np.asarray(pmf_row, float)
    ks = np.arange(lo, lo + len(p))
    return float(p[ks < value].sum() + 0.5 * p[ks == value].sum())


SHADOW_SHARE = 0.15             # settle-post (2026-11-20): the last 15% of the time budget is the sm-2 shadow's own slice
HARD_GRACE_S = 60.0             # a single statement still running this long after the deadline is interrupted
PROGRESS_OPS = 20000            # SQLite VM steps between two checks of that backstop
STATE_FILE = "learning_state.json"


def _past(t):
    return t is not None and time.time() >= t


def state_path():
    return config.DATA_DIR / "model" / STATE_FILE


def read_state():
    """model/learning_state.json: the last on_settled run ({"at", "partial", "events_left", "complete_at"}) - what
    grades-daily checks before it fits on learning.db (None when no run of this code wrote it yet)."""
    try:
        return json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_state(partial, left, at):
    prev = read_state() or {}
    obj = {"at": at, "partial": bool(partial), "events_left": int(left or 0),
           "complete_at": prev.get("complete_at") if partial else at}
    try:
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _current_for_side(by_offer):
    """settlement_id -> the chosen sides it is the CURRENT settlement for, by the exact rule of the loop below (the
    side's own records, else all of the offer's; highest revision, first on a tie). Lets an already-scored forecast be
    recognised without reading its offer: a settlement id belongs to one offer, the one the earlier row was scored on."""
    out = {}
    for alls in by_offer.values():
        for side in ("over", "under"):
            sts = [x for x in alls if x.side == side] or alls
            cur = max(sts, key=lambda x: x.revision)
            out.setdefault(cur.settlement_id, set()).add(side)
    return out


def on_settled(scon, log=None, now=None, days=30, deadline=None, shadow_share=SHADOW_SHARE):
    """Score every forecast whose event has a settlement learned within the last `days`.
    Idempotent: one row per forecast x current settlement; a corrected settlement supersedes the earlier row.

    Bounded (Starter, 2026-10-24): event by event - only events with a settlement known in the window are read, each
    event's settlements are read once (records_event index) and only that event's forecasts are decoded; a forecast
    already scored against its current settlement costs one indexed lookup and no offer read (2026-11-20). Never
    retrains (retrain_due only REPORTS whether the registered trigger is met; fitting needs `train fit` under
    SPORTS_TRAIN).

    deadline (epoch seconds, settle-post's time budget - stall 2026-11-09; strict since 2026-11-20, when a run went
    343 s past it): the main scorecard stops at (1 - shadow_share) of the time left, checked before every event AND
    every forecast. Rows written so far are committed; an unfinished event keeps no mark (its scored forecasts are
    skipped by the next run). The sm-2 shadow scorecard then gets the rest (its own slice: a long main pass never
    starves it), and retrain_due (a report) runs only if time is left. A statement still running HARD_GRACE_S after
    the deadline is interrupted (SQLite progress handler; the open event's rows roll back with it) and counts as a stop.
    A stopped run says "partial": True, "events_left"; the next run starts at the event it stopped at (wrapping round,
    so a backlog drains in order and no event is starved by earlier ones) - at the forecast it stopped at when that
    event's records are unchanged (resume_rid / resume_sig: an event bigger than one budget still finishes) - and
    keeps the stopped run's window start until a run completes, so a backlog older than `days` is never dropped."""
    lcon = db()
    icon = forward.inputs_db()
    t_start = time.time()
    main_deadline = None if deadline is None else t_start + max(0.0, deadline - t_start) * (1.0 - shadow_share)
    lcon.execute("CREATE TABLE IF NOT EXISTS learn_meta (key TEXT PRIMARY KEY, value TEXT)")
    meta = dict(lcon.execute("SELECT key, value FROM learn_meta"))
    cutoff = (parse_utc(now or now_utc()) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if meta.get("pending_cutoff") and meta["pending_cutoff"] < cutoff:
        cutoff = meta["pending_cutoff"]                 # catch-up: a stopped run's window is kept until one completes
    hard = None if deadline is None else deadline + HARD_GRACE_S
    if hard is not None:
        scon.set_progress_handler(lambda: 1 if time.time() > hard else 0, PROGRESS_OPS)
    n_new = n_diag = n_skip = n_fc = n_offers = n_fast = n_scored_ev = 0
    evs, order, left, pos, interrupted, cursor = [], [], 0, 0, False, None
    try:
        evs = [r[0] for r in scon.execute("SELECT DISTINCT event_id FROM records WHERE kind='settlement' AND "
                                          "known_at >= ? AND event_id IS NOT NULL ORDER BY event_id", (cutoff,))]
        done = dict(lcon.execute("SELECT event_id, sig FROM learn_events"))
        resume = meta.get("resume_event")
        k = next((j for j, e in enumerate(evs) if e >= resume), len(evs)) if resume else 0
        order = evs[k:] + evs[:k]
        for pos, ev in enumerate(order):
            if _past(main_deadline):
                left = len(order) - pos                 # resumed by the next run (marks committed per event)
                break
            # Skip an event whose records are exactly those of its last full pass: every forecast it could score is
            # already in the scorecard against its current settlement, so the body below would add nothing. The mark
            # is committed with the event's rows, so a run killed mid-way keeps what it finished (settle 1,500 s
            # timeouts, 2026-11-08: each run re-decoded every forecast of the season before its already-scored check).
            sig = event_sig(scon, ev)
            if done.get(ev) == sig:
                n_skip += 1
                continue
            by_offer = {}
            for r in scon.execute("SELECT source, source_key, payload FROM records WHERE kind='settlement' AND "
                                  "event_id=? ORDER BY source_key, revision", (ev,)):
                by_offer.setdefault((r["source"], r["source_key"].rsplit("|", 1)[0]), []).append(store._rec(r))
            if not by_offer:
                continue
            n_scored_ev += 1
            current = _current_for_side(by_offer)
            stopped = False
            # inside the event a stopped run left off in, with the event's records unchanged since: its forecasts
            # before that point were all passed by that run (its rows committed before the cursor was saved), so they
            # are not walked again - an event bigger than one budget still finishes
            start = int(meta["resume_rid"]) if (pos == 0 and ev == meta.get("resume_event") and meta.get("resume_rid")
                                                and meta.get("resume_sig") == sig) else 0
            # The window selects newly learned results, not forecast ages. A delayed result or
            # correction must still update an older forecast. Stream this event's forecasts
            # so catching up a long history does not materialize every payload in memory.
            for fr in scon.execute("SELECT rid, record_id, payload FROM records WHERE kind='forecast' AND event_id=? "
                                   "AND rid >= ? ORDER BY rid", (ev, start)):
                if _past(main_deadline):
                    stopped = True                      # rows so far are committed below; no mark for this event
                    cursor = (fr["rid"], sig)
                    break
                fc = store._rec(fr)
                fid = fr["record_id"]
                if fc is None or not fc.offer_snapshot_ids or fc.chosen_side is None:
                    continue
                n_fc += 1
                if any(fc.chosen_side in current.get(sid, ()) for (sid,) in lcon.execute(
                        "SELECT settlement_id FROM projection_reviews WHERE forecast_id=?", (fid,))):
                    n_fast += 1                         # already scored against its current settlement
                    continue
                offer = store.get(scon, fc.offer_snapshot_ids[0])
                n_offers += 1
                if offer is None:
                    continue
                alls = by_offer.get((offer.platform, offer.offer_id), [])
                sts = [x for x in alls if x.side == fc.chosen_side] or alls  # the other side's record has the value
                if not sts:
                    continue
                cur = max(sts, key=lambda x: x.revision)
                if lcon.execute("SELECT 1 FROM projection_reviews WHERE forecast_id=? AND settlement_id=?",
                                (fid, cur.settlement_id)).fetchone():
                    continue
                a, d = _score_one(lcon, icon, fid, fc, offer, cur)
                n_new += a
                n_diag += d
            if stopped:
                lcon.commit()
                left = len(order) - pos
                break
            lcon.execute("INSERT OR REPLACE INTO learn_events (event_id, sig, scored_at) VALUES (?,?,?)",
                         (ev, sig, now_utc()))
            lcon.commit()
    except sqlite3.OperationalError as e:
        if "interrupt" not in str(e).lower():
            raise
        interrupted = True                              # the hard backstop: a statement ran past deadline + grace
        lcon.rollback()                                 # the open event's rows and mark go (or stay) together
        left, cursor = max(1, len(order) - pos), None    # (a rolled-back event restarts from its first forecast)
    lcon.execute("DELETE FROM learn_meta WHERE key IN ('pending_cutoff', 'resume_event', 'resume_rid', 'resume_sig')")
    if left:
        lcon.execute("INSERT OR REPLACE INTO learn_meta VALUES ('pending_cutoff', ?)", (cutoff,))
        if order:
            lcon.execute("INSERT OR REPLACE INTO learn_meta VALUES ('resume_event', ?)", (order[len(order) - left],))
        if cursor:
            lcon.executemany("INSERT OR REPLACE INTO learn_meta VALUES (?, ?)",
                             (("resume_rid", str(cursor[0])), ("resume_sig", cursor[1])))
    lcon.commit()
    out = {"scorecard_rows_added": n_new, "diagnostics_added": n_diag, "events_read": len(evs),
           "events_unchanged": n_skip, "events_scored": n_scored_ev, "forecasts_read": n_fc,
           "forecasts_already_scored": n_fast, "offers_read": n_offers, "window_from": cutoff,
           "main_s": round(time.time() - t_start, 1), "retrain": {}}
    if left:
        out.update(partial=True, events_left=left)
    if interrupted:
        out["interrupted"] = True
    _write_state(bool(left), left, now_utc())
    try:                                                # sm-2 shadow scorecard (REGISTER A5); never blocks sm-1
        from . import shadow
        out["shadow"] = shadow.settle(scon, deadline=deadline)
    except Exception as e:                              # noqa: BLE001 (an interrupted statement lands here too)
        out["shadow"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    finally:
        if hard is not None:
            scon.set_progress_handler(None, 0)
    for sport in ("nba", "nfl"):
        if _past(deadline):                             # a report only (pipeline `learn` runs it with no deadline)
            out["retrain"][sport] = {"due": None, "skipped": "settle-post time budget spent"}
        else:
            out["retrain"][sport] = retrain_due(sport, lcon)
    lcon.close()
    icon.close()
    if log:
        log(out)
    return out


def _score_one(lcon, icon, fid, fc, offer, cur):
    # scored at the FORECAST's own line and side (the settlement records the offer's line at settlement time)
    st_status, st_outcome = settlement.at_line(cur, fc.line, fc.chosen_side)
    r = icon.execute("SELECT pmf_lo, pmf_json FROM inputs WHERE forecast_id=?", (fid,)).fetchone()
    pit = _pit(fc, cur.source_value, json.loads(r[1]) if r and r[1] else None, r[0] if r else None)
    p_side = fc.p_over if fc.chosen_side == "over" else fc.p_under
    lcon.execute("UPDATE scorecard SET superseded=1 WHERE forecast_id=?", (fid,))
    inserted = lcon.execute("INSERT OR IGNORE INTO scorecard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                 (fid, cur.settlement_id, fc.model_version, fc.league, fc.stat, fc.event_id, offer.platform,
                  fc.line, fc.chosen_side, p_side, fc.p_over, fc.p_under, fc.p_push, st_status, st_outcome,
                  cur.source_value, pit, fc.forecast_at, cur.settled_at)).rowcount
    # A migration may revisit an already-scored immutable pair to fill the new
    # projection review. Restore that pair as current after superseding others.
    lcon.execute("UPDATE scorecard SET superseded=0 WHERE forecast_id=? AND settlement_id=?",
                 (fid, cur.settlement_id))
    from gradecore.projection_review import review
    pr = None
    if st_status in ('win', 'loss', 'push') and fc.projection_mean is not None and cur.source_value is not None:
        try:
            pr = review(fc.projection_mean, cur.source_value, fc.line)
        except ValueError:
            pass
    lcon.execute("INSERT OR IGNORE INTO projection_reviews VALUES (?,?,?,?,?,?,?,?)",
                 (fid,cur.settlement_id,fc.projection_mean,cur.source_value,
                  pr['error'] if pr else None,pr['absolute_error'] if pr else None,
                  pr['squared_error'] if pr else None,None if pr else 'not_a_scored_point_projection'))
    kinds = []
    if pit is not None and abs(pit - 0.5) >= 0.45:
        kinds.append(("large_miss", f"PIT {pit:.3f}, value {cur.source_value}, line {fc.line}"))
    if cur.status == "void":
        kinds.append(("void", cur.reason or ""))
    if cur.revision > 1:
        kinds.append(("settlement_corrected", f"revision {cur.revision} supersedes {cur.supersedes}"))
    for k, d in kinds:
        lcon.execute("INSERT OR IGNORE INTO diagnostics VALUES (?,?,?,?,?,0)", (fid, cur.settlement_id, k, d[:300],
                                                                                now_utc()))
    return inserted, len(kinds)


_group_key = itemgetter("model_version", "league", "stat")


def _report_rows(con, table, model_version=None):
    # Shadow tables may have no point estimate; their probability scoring stays
    # unchanged. The champion scorecard gains projection diagnostics separately.
    fields = "model_version, league, stat, event_id, p_side, p_push, status"
    if table == 'scorecard':
        q = (f"SELECT {fields},pr.error AS projection_error,pr.absolute_error AS projection_absolute_error,"
             "pr.squared_error AS projection_squared_error FROM scorecard LEFT JOIN projection_reviews pr "
             "ON pr.forecast_id=scorecard.forecast_id AND pr.settlement_id=scorecard.settlement_id WHERE superseded=0")
    else:
        q = f"SELECT {fields} FROM {table} WHERE superseded = 0"
    a = []
    if model_version:
        q += " AND model_version = ?"
        a.append(model_version)
    cur = con.execute(q + f" ORDER BY model_version, league, stat, {table}.rowid", a)
    names = [d[0] for d in cur.description]
    return (dict(zip(names, r)) for r in cur)


def scorecard(lcon=None, model_version=None):
    with ExitStack() as stack:
        con = lcon if lcon is not None else stack.enter_context(closing(db()))
        return _scorecard_groups(_report_rows(con, "scorecard", model_version))


def _scorecard_groups(rows):
    out = []
    for (mv, lg, st), group in itertools.groupby(rows, _group_key):
        rs = list(group)
        graded = [r for r in rs if r["status"] in ("win", "loss")]
        p = np.array([r["p_side"] / max(1e-9, 1 - (r["p_push"] or 0)) for r in graded], float)
        hit = np.array([1.0 if r["status"] == "win" else 0.0 for r in graded])
        games = np.array([r["event_id"] for r in graded], dtype=object)
        row = {"model_version": mv, "league": lg, "stat": st, "settled": len(rs), "graded": len(graded),
               "games": len(set(games)), "pushes": sum(r["status"] == "push" for r in rs),
               "voids": sum(r["status"] == "void" for r in rs)}
        projected = [r for r in rs if r.get('projection_error') is not None]
        if projected:
            row['projection'] = {'rows':len(projected),
                'mae':float(np.mean([r['projection_absolute_error'] for r in projected])),
                'rmse':float(np.sqrt(np.mean([r['projection_squared_error'] for r in projected]))),
                'bias':evaluate.boot_mean(np.array([r['projection_error'] for r in projected]),
                                         np.array([r['event_id'] for r in projected],dtype=object))}
        if len(graded):
            pc = np.clip(p, 1e-6, 1 - 1e-6)
            row["mean_p_chosen_given_no_push"] = float(p.mean())
            row["hit_rate"] = float(hit.mean())
            row["hit_minus_p"] = evaluate.boot_mean(hit - p, games)
            row["logloss"] = float(np.mean(-(hit * np.log(pc) + (1 - hit) * np.log(1 - pc))))
            row["brier"] = float(np.mean((p - hit) ** 2))
        row["review_window_reached"] = max([w for w in REVIEW_WINDOWS if len(graded) >= w], default=None)
        out.append(row)
    return out


def _band(p):
    from . import grade_policy
    return grade_policy.band(p) if p is not None else None


def grade_bands(rows):
    """Grade-band validation (REGISTER.md A5 11, sports-gp-1): settled forward props grouped by model version x league
    x stat x band of the chosen side's UNCONDITIONAL probability (the band the grade would be, shown or not).
    Per group: settled, graded (win / loss), pushes, voids, games, mean p (chosen side given no push), hit rate,
    hit - p with a game-clustered bootstrap 95% interval; per (version, league, stat) also an ECE over the bands.
    rows: scorecard dicts (model_version, league, stat, event_id, p_side, p_push, status)."""
    groups = {}
    for r in rows:
        b = _band(r.get("p_side"))
        if b is None:
            continue
        groups.setdefault((r["model_version"], r["league"], r["stat"]), {}).setdefault(b, []).append(r)
    out = []
    for (mv, lg, st), bands in sorted(groups.items()):
        n_all, ece, rows_out = 0, 0.0, []
        for b in ("A", "B", "C", "Lean"):
            rs = bands.get(b, [])
            graded = [r for r in rs if r["status"] in ("win", "loss")]
            row = {"band": b, "settled": len(rs), "graded": len(graded), "pushes": sum(r["status"] == "push" for r in rs),
                   "voids": sum(r["status"] == "void" for r in rs), "games": len({r["event_id"] for r in graded})}
            if graded:
                p = np.array([r["p_side"] / max(1e-9, 1 - (r["p_push"] or 0)) for r in graded], float)
                hit = np.array([1.0 if r["status"] == "win" else 0.0 for r in graded])
                games = np.array([r["event_id"] for r in graded], dtype=object)
                m, lo, hi = evaluate.boot_mean(hit - p, games)
                row.update(mean_p=float(p.mean()), hit_rate=float(hit.mean()), hit_minus_p=m, ci=[lo, hi])
                n_all += len(graded)
                ece += len(graded) * abs(float(hit.mean()) - float(p.mean()))
            rows_out.append(row)
        out.append({"model_version": mv, "league": lg, "stat": st, "graded": n_all,
                    "ece_over_bands": ece / n_all if n_all else None, "bands": rows_out,
                    "policy": "sports-gp-1: A p >= 0.60, B >= 0.5559, C >= 0.5345, Lean below (chosen side, "
                              "unconditional p); hit rate and p conditional on no push; game-clustered 95% intervals",
                    "shown_grade": "withheld until gate F - bands are validated whether or not letters are shown"})
    return out


def grade_band_report(lcon=None):
    """Grade bands of the main scorecard + the sm-2 shadow scorecard (shadow.db), for the admin learning report."""
    with ExitStack() as stack:
        con = lcon if lcon is not None else stack.enter_context(closing(db()))
        rows = _report_rows(con, "scorecard")
        path = config.DATA_DIR / "model" / "shadow.db"
        if path.exists():
            from . import shadow
            try:
                shadow_db = stack.enter_context(closing(shadow.db(path)))
                shadow_rows = _report_rows(shadow_db, "shadow_scorecard")
                rows = heapq.merge(rows, shadow_rows, key=_group_key)
            except sqlite3.Error:
                pass
        return [result for _, group in itertools.groupby(rows, _group_key) for result in grade_bands(group)]


def forward_gate(lcon=None, *, scored=None):
    """Gate F as registered (REGISTER.md 7) - reported at review windows only."""
    sc = scorecard(lcon) if scored is None else scored
    by = {(r["model_version"], r["league"], r["stat"]): r for r in sc}
    out = []
    for (mv, lg, st), r in by.items():
        if mv not in ("sm-1", "mi-1", "mc-1"):
            continue
        res = {"model_version": mv, "league": lg, "stat": st, "graded": r["graded"], "games": r["games"]}
        if r["graded"] < 300 or r["games"] < 100:
            res["state"] = f"waiting ({r['graded']}/300 props, {r['games']}/100 games)"
        else:
            hm = r["hit_minus_p"]
            ref = by.get(("sharp-2", lg, st))
            res["calibration_ok"] = abs(hm[0]) <= 0.03 and hm[1] <= 0 <= hm[2]
            res["logloss_vs_sharp2"] = (r["logloss"] - ref["logloss"]) if ref and ref.get("logloss") else None
            res["state"] = "review window reached - decision by the registered review, never automatic"
        out.append(res)
    return out


def retrain_due(sport, lcon):
    last = lcon.execute("SELECT fitted_at, data_as_of FROM fits WHERE sport=? "
                        "ORDER BY fitted_at DESC, artifact_sha DESC LIMIT 1", (sport,)).fetchone()
    art = forward.load_artifact(sport)
    fitted_at = (last[0] if last else None) or (art or {}).get("fitted_at")
    data_as_of = (last[1] if last else None) or (art or {}).get("data_as_of") or fitted_at
    if not fitted_at:
        return {"due": True, "why": "no artifact yet"}
    days = (parse_utc(now_utc()) - parse_utc(fitted_at)).total_seconds() / 86400
    from . import datasets
    try:
        hcon = datasets.connect(read_only=True)
        tbl = "nba_pg" if sport == "nba" else "nfl_pg"
        new = hcon.execute(f"SELECT COUNT(*) FROM {tbl} p JOIN games g ON g.game_id = p.game_id AND g.league = ? "
                           "WHERE g.start_utc > ?", (sport, data_as_of)).fetchone()[0]
        hcon.close()
    except sqlite3.Error:
        new = None
    due = days >= RETRAIN_DAYS or (new is not None and new >= SAMPLE_TRIGGER[sport])
    return {"due": bool(due), "days_since_fit": round(days, 2), "new_rows": new,
            "rule": f"{RETRAIN_DAYS} days or >= {SAMPLE_TRIGGER[sport]} new player-games"}


def retrain(sport, hcon):
    """Fit a CHALLENGER artifact (same frozen configuration, all data to now). The champion file is untouched."""
    path = config.DATA_DIR / "model" / "challengers" / f"sm1_{sport}_{now_utc().replace(':', '')}.json"
    art = forward.fit(sport, hcon, out_path=path)
    lcon = db()
    lcon.execute("INSERT OR REPLACE INTO fits VALUES (?,?,?,?,?,?)", (sport, art["sha"], art["fitted_at"],
                                                                       art["data_as_of"], None, "challenger"))
    lcon.commit()
    lcon.close()
    rp = config.DATA_DIR / "model" / "challengers" / "registry_runtime.json"
    reg = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {"challengers": []}
    reg["challengers"].append({"sport": sport, "sha": art["sha"], "path": str(path.name),
                               "fitted_at": art["fitted_at"], "promoted": False,
                               "note": "challenger only: the champion is the committed artifact; promotion needs the "
                                       "registered gate + the owner release process"})
    rp.write_text(json.dumps(reg, indent=1, sort_keys=True), encoding="utf-8")
    return {"challenger": art["sha"], "path": str(path)}


def diagnostics(limit=200):
    lcon = db()
    cur = lcon.execute("SELECT * FROM diagnostics WHERE reviewed = 0 ORDER BY created_at DESC LIMIT ?", (limit,))
    names = [d[0] for d in cur.description]
    out = [dict(zip(names, r)) for r in cur.fetchall()]
    lcon.close()
    return out


def _isfinite(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))
