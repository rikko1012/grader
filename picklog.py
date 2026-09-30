#!/usr/bin/env python3
"""
picklog.py - Phase 10: log every graded line, settle it from BO3.gg after the match, report
calibration on REAL lines (the only test that proves the grades beat PrizePicks).

- log_grade(res): called for each member grade (GRADE state only - SCOUT/NO GRADE are not
  wagers). One row per player + prop + line + line type + match; repeats just count requests.
- settle(b): finished matches get the actual Maps 1-2 total, hit/miss. Forfeits, BO1s and
  matches that never appear are voided.
- set_closing(id, line): you enter PrizePicks' closing line; CLV = how far it moved your way.
- report(): printed % vs actual, record by grade and units, CLV.

Stored in SQLite on the data volume (CS2_DATA_DIR/picks.db).
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / "picks.db"
_LOCK = threading.Lock()
SETTLE_AFTER_H = 3        # a BO3 is over well within 3 hours of its start
VOID_AFTER_D = 3          # never found finished after 3 days -> void

COLUMNS = """id INTEGER PRIMARY KEY, key TEXT UNIQUE, logged_at TEXT, requests INTEGER DEFAULT 1,
player TEXT, slug TEXT, player_id INTEGER, team TEXT, opponent TEXT, match_slug TEXT, match_start TEXT,
prop TEXT, line REAL, line_type TEXT, lambda REAL, fair_line REAL, edge REAL,
p_more REAL, p_less REAL, pick TEXT, p_pick REAL, grade TEXT, units REAL, p_win REAL, market TEXT,
pred_maps TEXT, veto_known INTEGER, sample_maps INTEGER, rec_avg REAL, rec_median REAL, rec_hit TEXT,
flags TEXT, opp_def TEXT, status TEXT DEFAULT 'open', actual_maps TEXT, actual REAL, hit INTEGER,
closing_line REAL, clv REAL, settled_at TEXT"""


def _db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute(f"CREATE TABLE IF NOT EXISTS picks ({COLUMNS})")
    try:                                   # added 2026-09-29: owner's load tests are not real picks
        con.execute("ALTER TABLE picks ADD COLUMN test INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass                               # column already there
    return con


def log_grade(res, verdict, test=False):
    """Log a member's graded line. Returns the row id (or None when it isn't a wager).
    test=True (owner's load tests) is stored but left out of the report."""
    g, pb, nm = res.get("grade") or {}, res.get("player_block") or {}, res.get("next_match") or {}
    if not verdict or verdict.get("state") != "GRADE" or "p_more" not in g or not nm.get("slug"):
        return None
    rf = pb.get("recent_form") or {}
    key = "|".join(str(x) for x in (pb["player"]["slug"], g["prop"], g["line"], g["line_type"], nm["slug"]))
    row = {
        "key": key, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "player": pb["player"]["nick"], "slug": pb["player"]["slug"], "player_id": pb["player"]["id"],
        "team": pb.get("current_team"), "opponent": nm.get("opponent"), "match_slug": nm["slug"],
        "match_start": nm.get("start"), "prop": g["prop"], "line": g["line"], "line_type": g["line_type"],
        "lambda": g.get("lambda_final"), "fair_line": (g.get("value") or {}).get("fair_line"),
        "edge": round(g["lambda_final"] - g["line"], 2), "p_more": g["p_more"], "p_less": g["p_less"],
        "pick": verdict["side"], "p_pick": verdict["p"], "grade": verdict["grade"], "units": verdict["units"],
        "p_win": (g.get("detail") or {}).get("p_win"), "market": g.get("market_quality"),
        "pred_maps": json.dumps(pb.get("map_prediction")), "veto_known": int(bool(nm.get("maps"))),
        "sample_maps": (pb.get("overall") or {}).get("maps_played"), "rec_avg": rf.get("avg_total"),
        "rec_median": rf.get("median_total"),
        "rec_hit": f"{rf.get('overs')}/{rf.get('matches')}" if rf.get("overs") is not None else None,
        "flags": json.dumps(g.get("flags")), "opp_def": json.dumps(res.get("opponent_defense")),
        "test": int(bool(test)),
    }
    with _LOCK:
        con = _db()
        try:
            cur = con.execute("SELECT id FROM picks WHERE key = ?", (key,)).fetchone()
            if cur:
                # a real member asking for a line first seen in a test makes it a real pick
                con.execute("UPDATE picks SET requests = requests + 1, test = CASE WHEN ? THEN test ELSE 0 END "
                            "WHERE id = ?", (int(bool(test)), cur["id"]))
                con.commit()
                return cur["id"]
            cols = ", ".join(row)
            con.execute(f"INSERT INTO picks ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            con.commit()
            return con.execute("SELECT id FROM picks WHERE key = ?", (key,)).fetchone()["id"]
        finally:
            con.close()


def _quick_rows(b, player_id, mt):
    """Maps 1-2 kills/headshots from BO3.gg's quick scoreboard (published right after the match)."""
    out = []
    for g in sorted((g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2),
                    key=lambda g: g.get("number") or 0):
        try:
            row = next((x for x in b.game_short_stats(g["id"]) or [] if x.get("player_id") == player_id), None)
        except Exception:
            row = None
        if not row or row.get("kills_sum") is None:
            return []
        out.append({"map": g.get("map_name"), "kills": row["kills_sum"], "headshots": row.get("headshots_sum") or 0})
    return out if len(out) == 2 else []


def settle(b, map_rows):
    """Fill in actual Maps 1-2 totals for matches that have finished."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status = 'open'").fetchall()
        con.close()
    done = 0
    for r in rows:
        try:
            start = datetime.fromisoformat((r["match_start"] or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if now < start + timedelta(hours=SETTLE_AFTER_H):
            continue
        # fresh list (15 min): a 12-hour-old copy still shows finished matches as upcoming
        ms = b.player_matches(r["player_id"], pages=1, ttl=0.25).get("results") or []
        mt = next((m for m in ms if m.get("slug") == r["match_slug"]), None)
        upd = None
        if mt and mt.get("status") == "finished":
            maps = map_rows(b, r["player_id"], [mt], lambda m, g: (g.get("number") or 9) <= 2)
            done12 = [g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2 and g.get("state") == "done"]
            if len(maps) < 2 and len(done12) >= 2:
                for g in done12:                          # maps done but a scoreboard read empty:
                    try:                                  # fetch it fresh once and look again
                        b.game_players_fresh(g["id"])
                    except Exception:
                        pass
                maps = map_rows(b, r["player_id"], [mt], lambda m, g: (g.get("number") or 9) <= 2)
            if len(maps) < 2:
                maps = _quick_rows(b, r["player_id"], mt) or maps   # full stats not out yet
            if len(maps) == 2:
                stat = "headshots" if r["prop"] == "hs" else "kills"
                actual = sum(x[stat] for x in maps)
                if actual == r["line"]:                   # whole-number line landed exactly: push
                    upd = ("push", json.dumps([x["map"] for x in maps]), actual, None)
                else:
                    hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
                    upd = ("settled", json.dumps([x["map"] for x in maps]), actual, int(hit))
            elif (len([g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2]) >= 2
                  and now < start + timedelta(days=VOID_AFTER_D)):
                continue      # Maps 1-2 were played but BO3.gg hasn't published the stats yet: wait
            else:
                upd = ("void", json.dumps([x["map"] for x in maps]), None, None)   # forfeit / BO1 / sat out
        elif mt and mt.get("status") == "defwin":            # forfeit: no maps played
            upd = ("void", None, None, None)
        elif now > start + timedelta(days=VOID_AFTER_D):
            upd = ("void", None, None, None)
        if upd:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                            (*upd, now.isoformat(timespec="seconds"), r["id"]))
                con.commit()
                con.close()
            done += 1
    return done


def set_closing(pick_id, closing):
    """CLV: positive = the line moved toward your side after you logged it."""
    with _LOCK:
        con = _db()
        r = con.execute("SELECT line, pick FROM picks WHERE id = ?", (pick_id,)).fetchone()
        if r:
            clv = (closing - r["line"]) if r["pick"] == "More" else (r["line"] - closing)
            con.execute("UPDATE picks SET closing_line = ?, clv = ? WHERE id = ?", (closing, round(clv, 2), pick_id))
            con.commit()
        con.close()


def resettle_pushes():
    """One-off: rows settled as a miss when the total landed exactly on a whole-number line."""
    with _LOCK:
        con = _db()
        n = con.execute("UPDATE picks SET status='push', hit=NULL WHERE status='settled' AND actual = line").rowcount
        con.commit()
        con.close()
    return n


def reopen(ids):
    """Admin: put rows back to open (e.g. voided before BO3.gg published the map stats)."""
    with _LOCK:
        con = _db()
        con.executemany("UPDATE picks SET status='open', actual=NULL, hit=NULL, actual_maps=NULL, settled_at=NULL "
                        "WHERE id = ?", [(i,) for i in ids])
        con.commit()
        con.close()


def mark_tests(ids):
    """Admin: flag rows that came from the owner's own testing (not members' real lines)."""
    with _LOCK:
        con = _db()
        con.executemany("UPDATE picks SET test = 1 WHERE id = ?", [(i,) for i in ids])
        con.commit()
        con.close()


def export():
    with _LOCK:
        con = _db()
        rows = [dict(r) for r in con.execute("SELECT * FROM picks ORDER BY id")]
        con.close()
    return rows


def report():
    with _LOCK:
        con = _db()
        rows = [dict(r) for r in con.execute("SELECT * FROM picks WHERE COALESCE(test, 0) = 0 ORDER BY id DESC")]
        con.close()
    settled = [r for r in rows if r["status"] == "settled"]

    def bucket(rs, key, bounds):
        out = []
        for lo, hi, name in bounds:
            b_ = [r for r in rs if lo <= (r[key] or 0) < hi]
            if b_:
                out.append({"bucket": name, "n": len(b_),
                            "printed": round(sum(r["p_pick"] for r in b_) / len(b_), 3),
                            "actual": round(sum(r["hit"] for r in b_) / len(b_), 3)})
        return out

    pbounds = ((0.5, 0.57, "C 50-57%"), (0.57, 0.63, "B 57-63%"), (0.63, 0.68, "B+ 63-68%"),
               (0.68, 0.74, "A- 68-74%"), (0.74, 0.80, "A 74-80%"), (0.80, 1.01, "A+ 80%+"))
    ubounds = ((0, 0.01, "0U"), (0.5, 0.51, "0.5U"), (1, 1.01, "1U"), (1.5, 1.51, "1.5U"), (2, 2.01, "2U"))
    units_won = sum((r["units"] or 0) * (1 if r["hit"] else -1) for r in settled)
    clv = [r["clv"] for r in rows if r["clv"] is not None]
    return {
        "total": len(rows), "open": sum(r["status"] == "open" for r in rows), "settled": len(settled),
        "void": sum(r["status"] == "void" for r in rows),
        "push": sum(r["status"] == "push" for r in rows),
        "by_printed_pct": bucket(settled, "p_pick", pbounds),
        "by_units": bucket(settled, "units", ubounds),
        "hit_rate_all": round(sum(r["hit"] for r in settled) / len(settled), 3) if settled else None,
        "units_net_flat": round(units_won, 2),
        "clv_avg": round(sum(clv) / len(clv), 2) if clv else None, "clv_n": len(clv),
        "clv_positive": round(sum(c > 0 for c in clv) / len(clv), 3) if clv else None,
        "rows": rows[:300],
    }
