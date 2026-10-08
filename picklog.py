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
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / "picks.db"
_LOCK = threading.Lock()
VOID_AFTER_D = 3          # never found finished after 3 days -> void

COLUMNS = """id INTEGER PRIMARY KEY, key TEXT UNIQUE, logged_at TEXT, requests INTEGER DEFAULT 1,
player TEXT, slug TEXT, player_id INTEGER, team TEXT, opponent TEXT, match_slug TEXT, match_start TEXT,
prop TEXT, line REAL, line_type TEXT, lambda REAL, fair_line REAL, edge REAL,
p_more REAL, p_less REAL, pick TEXT, p_pick REAL, grade TEXT, units REAL, p_win REAL, market TEXT,
pred_maps TEXT, veto_known INTEGER, sample_maps INTEGER, rec_avg REAL, rec_median REAL, rec_hit TEXT,
flags TEXT, opp_def TEXT, status TEXT DEFAULT 'open', actual_maps TEXT, actual REAL, hit INTEGER,
closing_line REAL, clv REAL, settled_at TEXT"""


_SCHEMA_OK = [False]


def _model_version():
    """Short hash of everything that can move a grade (grader, simulation, the inputs pipeline):
    every pick says exactly which version made it."""
    import hashlib
    h = hashlib.sha1()
    for f in ("grader.py", "sim.py", "cs2_scout.py", "hltv.py"):
        try:
            h.update((Path(__file__).resolve().parent / f).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


MODEL_VERSION = _model_version()


def _save_config_snapshot():
    """The full grading settings for this version, kept once per version (frozen-test record)."""
    try:
        from grader import CONFIG
        p = DB.parent / "model_versions.json"
        cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if MODEL_VERSION not in cur:
            cur[MODEL_VERSION] = {"saved": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "config": json.loads(json.dumps(CONFIG, default=str))}
            p.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    except Exception:
        pass


def _db():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA synchronous=NORMAL")   # with WAL: safe, and writes don't wait on a full disk sync
    if _SCHEMA_OK[0]:
        return con
    DB.parent.mkdir(parents=True, exist_ok=True)
    con.execute("PRAGMA journal_mode=WAL")     # readers and the writer no longer block each other
    con.execute(f"CREATE TABLE IF NOT EXISTS picks ({COLUMNS})")
    try:                                   # added 2026-09-29: owner's load tests are not real picks
        con.execute("ALTER TABLE picks ADD COLUMN test INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass                               # column already there
    try:                                   # added 2026-10-07: the exact grader that made the pick (frozen-version tests)
        con.execute("ALTER TABLE picks ADD COLUMN model_version TEXT")
    except sqlite3.OperationalError:
        pass
    # 2026-10-07: the forecast history - the picks row is the first published forecast (immutable), every
    # changed forecast before the match is appended here (timestamp, line, side, chance, grade, version, shadows)
    con.execute("""CREATE TABLE IF NOT EXISTS forecasts (id INTEGER PRIMARY KEY, pick_id INTEGER, kind TEXT, logged_at TEXT,
                   match_start TEXT, line REAL, pick TEXT, p_pick REAL, p_more REAL, grade TEXT, units REAL, lambda REAL,
                   p_win REAL, rec_hit TEXT, sample_maps INTEGER, model_version TEXT, shadow TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS forecasts_pick ON forecasts (pick_id, id)")
    try:                                   # 2026-10-08: the exact grader inputs of each forecast (zlib JSON) - replayable
        con.execute("ALTER TABLE forecasts ADD COLUMN inputs BLOB")
    except sqlite3.OperationalError:
        pass
    # first_logged_at set = a board row that was overwritten by a later grade (2026-10-07 ~21:00-22:00Z only)
    for col in ("first_logged_at", "regrades"):
        try:
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} {'INTEGER DEFAULT 0' if col == 'regrades' else 'TEXT'}")
        except sqlite3.OperationalError:
            pass
    try:                                   # added 2026-10-06: which maps the line covers
        con.execute("ALTER TABLE picks ADD COLUMN scope TEXT DEFAULT '12'")
    except sqlite3.OperationalError:
        pass
    for col in ("factors", "result"):      # added 2026-10-02: every factor's value at grade time and
        try:                               # what happened (rounds, OT, map wins) - each measurable
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    _SCHEMA_OK[0] = True
    return con


def _factors(res):
    """Every input and every adjustment behind this grade (for measuring each factor later)."""
    gi, g, pb = res.get("grader_inputs") or {}, res.get("grade") or {}, res.get("player_block") or {}
    keep = ("kpr", "hs_pct", "maps_played", "sample_maps", "recent_avg", "recent_median", "recent_n", "kpr_last5",
            "round_swing", "rating", "teammate_ratings", "is_igl", "sniping", "dpr", "kast", "adr",
            "market_quality", "days_since_last_map", "map_win_probs", "trade_pr", "standin_for", "is_standin",
            "roster_confirmed", "on_listed_team", "recent", "per_map")
    out = {k: gi.get(k) for k in keep if k in gi}
    out["h2h_series"] = len(gi.get("h2h_series") or [])
    out["regional_series"] = len(gi.get("regional_series") or [])
    out["detail"] = g.get("detail")
    out["lambda_base"] = g.get("lambda_base")
    out["sd"] = g.get("sd")
    out["form_fight"], out["tape_fight"] = g.get("form_fight"), g.get("tape_fight")
    out["form_windows"] = (pb.get("overall") or {}).get("form_windows")
    out["team_kill_share"] = pb.get("team_kill_share")
    out["opening"] = pb.get("opening")
    out["hltv"] = {k: (res.get("hltv") or {}).get(k) for k in ("rating", "round_swing", "adr", "kast", "maps")}
    out["environment"] = (res.get("hltv_context") or {}).get("environment")   # LAN / online split (tested later)
    # frozen-test comparisons (2026-10-07): the same inputs graded by the alternatives - never shown to members
    try:
        from grader import grade_leg, CONFIG
        # fix_v2 (2026-10-08 review): gap compression centred on the line, Maps 1-3 pushes removed after mixing
        alts = {"old_spread": dict(CONFIG, c=1.55, c_hs=1.30),
                "no_rest_bonus": dict(CONFIG, rest_bonus=0.0),
                "fix_v2": dict(CONFIG, fix_v2=True),
                # 2026-10-08 environment revision: the old behaviour and the provisional taper, on identical inputs
                "online_none": dict(CONFIG, online_guard="none"),
                "online_taper": dict(CONFIG, online_guard="taper"),
                # 2026-10-08 perf-1 goes live on Maps 1-2: the old pipeline, on identical inputs
                "legacy": dict(CONFIG, perf_scopes=())}
        out["shadow"] = {"live_raw": g.get("probs_raw"),     # the live grade's own Over / Under / push chances
                         "live_online": g.get("online_adj")}   # online weight + the bonus actually applied
        for name, cfg in alts.items():
            gg = grade_leg(gi, cfg)
            out["shadow"][name] = {"p_more": gg.get("p_more"), "lambda": gg.get("lambda_final"),
                                   "grade": gg.get("grade"), "grade_less": gg.get("grade_less"),
                                   "raw": gg.get("probs_raw")}
    except Exception:
        pass
    out["opponent_defense"] = res.get("opponent_defense")
    out["sim"] = (res.get("simulation") or {}).get("p_more")
    out["map_prediction"] = (res.get("player_block") or {}).get("map_prediction")
    try:   # what the projection would be if predicted maps counted (to test them on real lines)
        from grader import grade_leg, CONFIG
        if gi.get("map_probs"):
            out["lambda_with_pred_maps"] = grade_leg(gi, dict(CONFIG, use_predicted_maps=True))["lambda_final"]
    except Exception:
        pass
    return out


_QUEUE = queue.Queue()
_WRITER = [None]


_save_config_snapshot_done = [False]


def log_async(res, verdict, test=False):
    """log_grade() without making the member wait: the row is built now (small) and written by one
    background writer (a busy pick log used to add 2-15 s to every grade)."""
    if not _save_config_snapshot_done[0]:
        _save_config_snapshot_done[0] = True
        _save_config_snapshot()
    try:
        row = pick_row(res, verdict, test)
    except Exception:
        return
    if row is None:
        return
    _QUEUE.put(row)
    if _WRITER[0] is None or not _WRITER[0].is_alive():
        with _LOCK:
            if _WRITER[0] is None or not _WRITER[0].is_alive():
                _WRITER[0] = threading.Thread(target=_writer, daemon=True, name="picklog-writer")
                _WRITER[0].start()


def _writer():
    while True:
        row = _QUEUE.get()
        try:
            write_row(row)
        except Exception:
            import traceback
            traceback.print_exc()


def log_grade(res, verdict, test=False):
    """Log a member's graded line. Returns the row id (or None when it isn't a wager).
    test=True (owner's load tests) is stored but left out of the report."""
    return write_row(pick_row(res, verdict, test))


def pick_row(res, verdict, test=False):
    """The pick-log row for this grade (None when it isn't a wager)."""
    g, pb, nm = res.get("grade") or {}, res.get("player_block") or {}, res.get("next_match") or {}
    slug = nm.get("slug") or (f"hltv-{nm['hltv_match']}" if nm.get("hltv_match") else None)
    if not verdict or verdict.get("state") != "GRADE" or "p_more" not in g or not slug:
        return None
    nm = dict(nm, slug=slug)   # matches only HLTV has are logged as hltv-<HLTV match id>
    # the record logged with the pick is in the LINE'S scope: Map 1 lines use the Map 1 record; Maps 1-3 has none
    # (until 2026-10-08 the Maps 1-2 record was logged against Map 1 / Maps 1-3 lines)
    _sc = (g.get("scope") or "12")
    rf = (pb.get("recent_form_map1") or {}) if _sc == "1" else {} if _sc == "123" else (pb.get("recent_form") or {})
    scope = g.get("scope") or "12"
    key = "|".join(str(x) for x in (pb["player"]["slug"], g["prop"], g["line"], g["line_type"], nm["slug"]))
    if scope != "12":
        key += "|maps" + scope                         # Maps 1-2 keys unchanged
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
        "rec_hit": f"{rf.get('overs')}/{rf.get('decided', rf.get('matches'))}" if rf.get("overs") is not None else None,   # pushes out
        "flags": json.dumps(g.get("flags")), "opp_def": json.dumps(res.get("opponent_defense")),
        "test": test if test == 2 else int(bool(test)),   # 1 = owner's tests, 2 = Underdog board (not a member pick)
        "factors": json.dumps(_factors(res), default=str),
        "scope": scope,
        "model_version": MODEL_VERSION,
    }
    try:
        import zlib
        row["_gi"] = zlib.compress(json.dumps(res.get("grader_inputs"), default=str).encode(), 6)
    except Exception:
        pass
    return row


def _shadow(row):
    try:
        return json.dumps((json.loads(row.get("factors") or "{}") or {}).get("shadow"))
    except (ValueError, TypeError):
        return None


def _forecast(con, pid, row, kind, gi=None):
    """Append one forecast to the history - only when it differs from the last one stored for this pick.
    The picks row itself stays the FIRST published forecast (never overwritten)."""
    last = con.execute("SELECT line, pick, p_pick, grade, units, lambda, model_version FROM forecasts "
                       "WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if last and kind == "update" and last["line"] == row["line"] and last["pick"] == row["pick"]             and last["grade"] == row["grade"] and last["units"] == row["units"]             and last["model_version"] == row.get("model_version")             and abs((last["p_pick"] or 0) - (row["p_pick"] or 0)) < 0.01             and abs((last["lambda"] or 0) - (row.get("lambda") or 0)) < 0.2:
        return
    con.execute("INSERT INTO forecasts (pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, "
                "lambda, p_win, rec_hit, sample_maps, model_version, shadow, inputs) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, kind, row["logged_at"], row.get("match_start"), row["line"], row["pick"], row["p_pick"],
                 row.get("p_more"), row["grade"], row["units"], row.get("lambda"), row.get("p_win"), row.get("rec_hit"),
                 row.get("sample_maps"), row.get("model_version"), _shadow(row), gi))


def _before_start(row, cur):
    """The new grade was made before the match started (by the start the row has, and the one it brings)."""
    now = row["logged_at"]
    for st in (cur["match_start"], row.get("match_start")):
        if st and now >= st.replace("Z", "+00:00")[:25]:
            return False
    return True


def write_row(row):
    if row is None:
        return None
    row = dict(row)
    gi = row.pop("_gi", None)                       # the exact grader inputs - forecasts table only
    key, test = row["key"], row["test"]
    with _LOCK:
        con = _db()
        try:
            cur = con.execute("SELECT id, test, status, match_start FROM picks WHERE key = ?", (key,)).fetchone()
            if cur and cur["status"] == "open" and _before_start(row, cur):
                _forecast(con, cur["id"], row, "update", gi)  # every changed pre-match forecast, timestamped
                con.execute("UPDATE picks SET regrades = COALESCE(regrades, 0) + 1 WHERE id = ?", (cur["id"],))
            if cur:
                # a real member asking for a line first seen in a test makes it a real pick
                con.execute("UPDATE picks SET requests = requests + 1, test = CASE WHEN ? THEN test ELSE 0 END "
                            "WHERE id = ?", (int(bool(test)), cur["id"]))
                con.commit()
                return cur["id"]
            cols = ", ".join(row)
            con.execute(f"INSERT INTO picks ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            pid = con.execute("SELECT id FROM picks WHERE key = ?", (key,)).fetchone()["id"]
            _forecast(con, pid, row, "first", gi)
            con.commit()
            return pid
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
        # the data decides, not the clock: HLTV's result settles a pick as soon as the scraper has it
        # (listed start times are often off); BO3.gg's only after SETTLE_MIN minutes
        early = now < start + timedelta(minutes=SETTLE_MIN)
        if now < start - timedelta(hours=12):
            continue                                  # far in the future: nothing to look for yet
        if (_col(r, "scope") or "12") != "12":         # Maps 1-3 / Map 1 lines: HLTV only (has map 3 and BO1)
            upd = _settle_scope(r, now, start)
            if upd is not None:
                with _LOCK:
                    con = _db()
                    con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                                (*upd, now.isoformat(timespec="seconds"), r["id"]))
                    con.commit()
                    con.close()
                done += 1
            continue
        if early and not (r["match_slug"] or "").startswith("hltv-"):
            hv = _hltv_totals(r, same_day_only=True)
            if isinstance(hv, tuple):
                with _LOCK:
                    con = _db()
                    con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                                (*hv, now.isoformat(timespec="seconds"), r["id"]))
                    con.commit()
                    con.close()
                done += 1
            continue
        if (r["match_slug"] or "").startswith("hltv-"):
            upd = _settle_hltv(r, now, start)
            if upd is None:
                continue
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                            (*upd, now.isoformat(timespec="seconds"), r["id"]))
                con.commit()
                con.close()
            done += 1
            continue
        # HLTV first - the official record (BO3.gg's quick scoreboards and map order are sometimes off)
        hv = _hltv_totals(r)
        if hv == "wait" and now < start + timedelta(hours=HLTV_WAIT_H):
            continue          # HLTV has the match but not this stat yet (scraped every ~2 h)
        if isinstance(hv, tuple):
            upd = hv
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                            (*upd, now.isoformat(timespec="seconds"), r["id"]))
                con.commit()
                con.close()
            done += 1
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
                _save_result(r["id"], {"source": "bo3", "rounds": [x.get("rounds") for x in maps],
                                       "won": [x.get("won") for x in maps],
                                       "per_map": [[x.get("kills"), x.get("headshots")] for x in maps]})
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


def _save_result(pick_id, result):
    try:
        with _LOCK:
            con = _db()
            con.execute("UPDATE picks SET result = ? WHERE id = ?", (json.dumps(result, default=str), pick_id))
            con.commit()
            con.close()
    except sqlite3.Error:
        pass


SETTLE_MIN = 75     # BO3.gg results are trusted this long after the listed start (Maps 1-2 can't be faster)
HLTV_WAIT_H = 12   # HLTV has the match but not its headshots yet: wait this long before BO3.gg


def _outcome(r, actual, maps):
    if actual == r["line"]:
        return ("push", json.dumps(maps), actual, None)
    hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
    return ("settled", json.dumps(maps), actual, int(hit))


def _col(r, k):
    try:
        return r[k]
    except (IndexError, KeyError):
        return None


def _forfeited(cur, mid):
    """HLTV's forfeited-map count for a match (None = unknown)."""
    try:
        cur.execute("SELECT forfeited_maps FROM match_overview WHERE matchid = %s", (mid,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def scope_result(r, same_day_only=False):
    """The pick's series from HLTV and its total for the line's scope, via the shared scope rules (scope.py).
    Returns {"status": "ok", "total", "maps_used", "maps", "per_map", "rounds", "won", "hltv_match"} or
    {"status": "wait"|"none"|"error", "reason"}. Never substitutes another scope; two candidate matches = error."""
    import hltv
    from scope import scope_total
    day = (r["match_start"] or "")[:10]
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            ids = hltv._player_ids(cur, r["player"], r["team"])
            rows = hltv._series_rows(cur, ids, "2000-01-01", "AND mo.date BETWEEN %(d)s::date - 1 AND %(d)s::date + 1",
                                     {"d": day}, maxn=3, need=(1,)) if ids else []
            opp = hltv._norm(r["opponent"] or "")
            on = [x for x in rows if not opp or opp in hltv._norm(x["opponent"]) or hltv._norm(x["opponent"]) in opp]
            if same_day_only:
                on = [x for x in on if x["date"] >= day]     # an earlier meeting is not this match
            mids = {x["hltv_match"] for x in on}
            forfeits = _forfeited(cur, next(iter(mids))) if len(mids) == 1 else None
    finally:
        con.close()
    if not on:
        return {"status": "none", "reason": "match not in HLTV yet"}
    if len(mids) > 1:
        return {"status": "error", "reason": f"{len(mids)} HLTV matches vs this opponent around that day - ambiguous"}
    if forfeits:
        return {"status": "void", "reason": f"{forfeits} forfeited map(s)"}
    ms = sorted(on, key=lambda x: x["number"])
    maps = [{"number": x["number"], "kills": x["kills"], "headshots": x["headshots"], "won": x["won"]} for x in ms]
    sc = _col(r, "scope") or "12"
    total, used = scope_total(maps, sc, r["prop"])
    if total is None:
        if sc == "123" and len(ms) == 2 and ms[0]["won"] != ms[1]["won"]:
            return {"status": "wait", "reason": "1-1 after two maps: map 3 not in yet"}
        if any(x[("headshots" if r["prop"] == "hs" else "kills")] is None for x in ms):
            return {"status": "wait", "reason": "stat not scraped yet"}
        return {"status": "error", "reason": used}
    return {"status": "ok", "total": total, "maps_used": used, "maps": [x["map"] for x in ms if x["number"] in used],
            "per_map": [[x["kills"], x["headshots"]] for x in ms], "rounds": [x["rounds"] for x in ms],
            "won": [x["won"] for x in ms], "hltv_match": ms[0]["hltv_match"]}


def _settle_scope(r, now, start):
    """A Maps 1-3 or Map 1 line, from HLTV via scope_result(). None = not yet; void after VOID_AFTER_D days."""
    try:
        res = scope_result(r)
    except Exception:
        res = {"status": "none"}
    if res["status"] == "void":
        return ("void", None, None, None)
    if res["status"] != "ok":
        return ("void", None, None, None) if now > start + timedelta(days=VOID_AFTER_D) else None
    _save_result(r["id"], {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                           "maps_used": res["maps_used"], "hltv_match": res["hltv_match"]})
    return _outcome(r, res["total"], res["maps"])


CORRECTIONS = DB.parent / "settlement_corrections.jsonl"


def fix_scope_settlements(apply=False, scopes=("1", "123"), test=None):
    """2026-10-08 incident: recheck_with_hltv() re-settled Map 1 / Maps 1-3 picks with Maps 1-2 totals. Re-derive every
    settled non-Maps-1-2 pick from HLTV through scope_result(); with apply=True correct the mismatches and append the
    old and new values to settlement_corrections.jsonl (nothing is lost). Returns what differs / what was skipped."""
    from scope import outcome
    with _LOCK:
        con = _db()
        rows = [x for x in con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push')").fetchall()
                if (x["scope"] or "12") in scopes and (test is None or (x["test"] or 0) == test)]
        con.close()
    diffs, skipped = [], []
    for r in rows:
        try:
            res = scope_result(r)
        except Exception as e:
            skipped.append({"id": r["id"], "reason": f"error {e}"}); continue
        if res["status"] != "ok":
            skipped.append({"id": r["id"], "reason": res.get("reason")}); continue
        st, hit = outcome(res["total"], r["line"], r["pick"])
        if res["total"] == r["actual"] and st == r["status"] and (hit == r["hit"] or (hit is None and r["hit"] is None)):
            continue
        d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": res["total"], "status": st, "hit": hit}, "maps_used": res["maps_used"],
             "hltv_match": res["hltv_match"]}
        diffs.append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, actual_maps=? WHERE id=?",
                            (st, res["total"], hit, json.dumps(res["maps"]), r["id"]))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                            reason="recheck_with_hltv used Maps 1-2 totals for a non-Maps-1-2 line")) + "\n")
    return {"checked": len(rows), "differs": diffs, "skipped": skipped, "applied": bool(apply)}


def _hltv_totals(r, same_day_only=False):
    """HLTV's result for a Maps 1-2 pick through the shared scope rules (scope_result): an update tuple, "wait" (match
    there, stat not yet), or None. Since 2026-10-08 it refuses ambiguous matches (two candidates used to take the first)
    and voids forfeits."""
    try:
        res = scope_result(r, same_day_only=same_day_only)
    except Exception:
        return None
    if res["status"] == "void":
        return ("void", None, None, None)
    if res["status"] == "wait":
        return "wait"
    if res["status"] != "ok":
        return None
    _save_result(r["id"], {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                           "maps_used": res["maps_used"], "hltv_match": res["hltv_match"]})
    return _outcome(r, res["total"], res["maps"])


def recheck_with_hltv():
    """Admin: re-settle already-settled picks from HLTV where HLTV has the match (corrects BO3.gg).
    Returns the rows that changed."""
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push')").fetchall()
        con.close()
    changed = []
    for r in rows:
        if (_col(r, "scope") or "12") != "12":
            continue          # _hltv_totals is Maps 1-2 only - Map 1 / Maps 1-3 lines settle via scope_result() (incident 2026-10-08)
        hv = _hltv_totals(r)
        if isinstance(hv, tuple) and hv[0] == "void":
            continue          # never silently void a settled pick here (forfeits are reviewed by hand)
        if isinstance(hv, tuple) and (hv[2] != r["actual"] or hv[0] != r["status"]):
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=? WHERE id=?", (*hv, r["id"]))
                con.commit()
                con.close()
            changed.append({"id": r["id"], "player": r["player"], "prop": r["prop"], "line": r["line"],
                            "pick": r["pick"], "bo3": r["actual"], "hltv": hv[2], "was_hit": r["hit"], "now_hit": hv[3]})
    return changed


def _settle_hltv(r, now, start):
    """A match only HLTV has: Maps 1-2 kills / headshots from the hltv-scraper database.
    None = not yet (stats or headshots not scraped); void after VOID_AFTER_D days without them."""
    try:
        import hltv
        tot = hltv.settle_totals(r["player"], r["team"], int(r["match_slug"].split("-", 1)[1]))
    except Exception:
        tot = {"found": False}
    actual = tot.get("headshots" if r["prop"] == "hs" else "kills") if tot.get("found") else None
    try:                                              # a forfeited map voids the pick (2026-10-08)
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                if _forfeited(cur, int(r["match_slug"].split("-", 1)[1])):
                    return ("void", None, None, None)
        finally:
            con.close()
    except Exception:
        pass
    if actual is None:
        return ("void", None, None, None) if now > start + timedelta(days=VOID_AFTER_D) else None
    maps = json.dumps(tot.get("maps"))
    if actual == r["line"]:
        return ("push", maps, actual, None)
    hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
    return ("settled", maps, actual, int(hit))


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


def export_forecasts(inputs=False, last=None):
    """The forecast history; inputs=True decodes each forecast's exact grader inputs (large)."""
    import zlib
    with _LOCK:
        con = _db()
        q = "SELECT * FROM forecasts ORDER BY id" + (f" DESC LIMIT {int(last)}" if last else "")
        rows = [dict(r) for r in con.execute(q)]
        con.close()
    for r in rows:
        blob = r.pop("inputs", None)
        if inputs and blob:
            try:
                r["inputs"] = json.loads(zlib.decompress(blob))
            except Exception:
                r["inputs"] = None
        elif inputs:
            r["inputs"] = None
        else:
            r["has_inputs"] = blob is not None
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
