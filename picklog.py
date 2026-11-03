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
import math
import os
import sqlite3
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import platform_rules

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
    # perf.py / perf_live.py / scope.py added 2026-10-09 (the performance model moves grades too)
    # market_odds.py / thunderpick.py added 2026-10-13 (the projection's market input can come from Thunderpick)
    # pick_verdict.py added 2026-10-13 (it sets the logged side, Lean / grade and stake). The alias / matching state is
    # NOT hashed here: it is mapping.revision(), recorded on every forecast (mapping_rev)
    # bo3.py / roles.py added 2026-10-13 audit (cached scoreboards and the AWP role move grades: BO3.gg fallback rows, Gate A);
    # polymarket.py / oddspapi.py too (they decide the fallback market input: team matching, live/ended events)
    # cm1.py / cm2.py / cm2_model.json added 2026-10-20: cm-2 projects kills Maps 1-2 live (perf-cm2)
    for f in ("grader.py", "sim.py", "cs2_scout.py", "hltv.py", "perf.py", "perf_live.py", "scope.py", "hltv_store.py",
              "market_odds.py", "thunderpick.py", "pick_verdict.py", "bo3.py", "roles.py", "polymarket.py", "oddspapi.py",
              "cm1.py", "cm2.py", "cm2_model.json"):
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
    # 2026-10-12 (owner review): the model's own outcome chances before caps / margins (over, under, PUSH, p_more_no_push)
    # and the HLTV dataset revision the forecast was graded on - a later backfill changes inputs without a code change
    # 2026-10-13 (forecast record): the odds policy that selected the market input, its signature (what the input IS -
    # a moved price or another source is a new forecast, an unchanged quote re-polled is not), the alias / matching
    # revision the odds were linked under (content in mapping_revisions) and the deployed commit (RENDER_GIT_COMMIT)
    # 2026-10-15: the board quote the forecast was graded on (prices / payouts / feed market type / sides / times) -
    # measurement only, for checking whether a line can serve as a market anchor (CHALLENGERS pl-1)
    for col in ("probs_raw", "data_rev", "odds_source", "odds_policy", "mapping_rev", "odds_sig", "git_commit", "quote"):
        try:
            con.execute(f"ALTER TABLE forecasts ADD COLUMN {col} TEXT")
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
    # 2026-10-12 recap audit: a member opening a board line rewrote the row's test 2 -> 0, so board grades drifted into
    # the member cohort. on_board keeps the board provenance for good: 1 = written by the board (recorded), 2 = inferred
    # for rows from before this column (test 0 with a platform recorded = a board line a member opened), 0 = never.
    try:
        con.execute("ALTER TABLE picks ADD COLUMN on_board INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # added 2026-10-10: platform + provider market id (+ PrizePicks market); 2026-10-13: profile_team = the player's BO3.gg
    # PROFILE team (picks.team is now the team he plays THIS match for - ALLE: team 'ex-Keyd Stars', profile 'UNO MILLE')
    for col in ("book", "market_id", "market_key", "profile_team"):
        try:
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    # 2026-10-13 audit D6: the on_board backfill ran only right after its ALTER - lost for good when that connection never
    # committed (a reader first) or when `book` did not exist yet. It now runs once every column exists, until its marker
    # is stored (same transaction), and only fills 0s it is sure of: test 2 = the board wrote it; test 0 with a platform =
    # a board line a member opened (only board grades carry a platform). A recorded value is never changed.
    # 2026-10-15 fh-1 shadow (perf_shadow.py, CHALLENGERS.md): its immutable pre-match records, kept apart from the live
    # forecasts (whose rows, dedupe and every existing challenger's inputs stay as they were), and their saved inputs
    # (content-addressed: the history snapshot and the grader inputs each record was computed from)
    con.execute("""CREATE TABLE IF NOT EXISTS shadow_forecasts (id INTEGER PRIMARY KEY, pick_id INTEGER, forecast_id INTEGER,
                   challenger TEXT, logged_at TEXT, match_start TEXT, line REAL, prop TEXT, scope TEXT, live_p_more REAL,
                   ch_p_more REAL, live_lambda REAL, ch_lambda REAL, n0_exact INTEGER, identical INTEGER, grp TEXT,
                   inputs_sha TEXT, gi_sha TEXT, git_commit TEXT, rec TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS shadow_fc_pick ON shadow_forecasts (pick_id, id)")
    # 2026-10-21 audit 11: member Slips history (rows_for_matches) and posts.drafts look picks up by match slug - an
    # index instead of a full table scan per request (one-time build, single TEXT column)
    try:
        con.execute("CREATE INDEX IF NOT EXISTS picks_match ON picks (match_slug)")
    except sqlite3.OperationalError:
        pass                               # a pre-match_slug database (old test fixtures): nothing to index
    con.execute("CREATE TABLE IF NOT EXISTS shadow_inputs (sha TEXT PRIMARY KEY, kind TEXT, created_at TEXT, body BLOB)")
    con.execute("CREATE TABLE IF NOT EXISTS schema_marks (name TEXT PRIMARY KEY, at TEXT)")
    # 2026-10-23 closeout 2 (reschedules): every start time seen for a pick, kept apart from the immutable pick row -
    # kind 'platform' = the platform's listed start for the pick's line (ref = its market id), 'match' = the match
    # source's start for the pick's own match (same pick key = same match slug / HLTV id), 'actual' = the start evidence
    # read at settlement (HLTV schedule time / BO3.gg map-1 begin / match date). Changes only, OBS_MAX per pick and kind
    con.execute("""CREATE TABLE IF NOT EXISTS schedule_obs (id INTEGER PRIMARY KEY, pick_id INTEGER, kind TEXT, start TEXT,
                   seen_at TEXT, source TEXT, ref TEXT)""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS schedule_obs_u ON schedule_obs (pick_id, kind, start, source)")
    if not con.execute("SELECT 1 FROM schema_marks WHERE name = 'on_board_backfill'").fetchone():
        con.execute("UPDATE picks SET on_board = CASE WHEN test = 2 THEN 1 ELSE 2 END "
                    "WHERE COALESCE(on_board, 0) = 0 AND (test = 2 OR (test = 0 AND book IS NOT NULL))")
        con.execute("INSERT OR IGNORE INTO schema_marks (name, at) VALUES ('on_board_backfill', ?)",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
    con.commit()                           # the migrations are kept whoever opens the database first
    for col in ("factors", "result"):      # added 2026-10-02: every factor's value at grade time and
        try:                               # what happened (rounds, OT, map wins) - each measurable
            con.execute(f"ALTER TABLE picks ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    try:                                   # 2026-10-13: every alias / matching revision a forecast used (append-only)
        import mapping
        mapping.ensure_schema(con)
        con.commit()
    except Exception:
        pass
    _SCHEMA_OK[0] = True
    return con


def _read_db():
    """A read-only connection that does NOT take _LOCK (2026-10-21 audit 11/41): under WAL a reader sees the last
    committed state and never blocks the writer, so member reads no longer queue behind each other, the pick-log writer
    or settlement. The schema / migrations still run once under _LOCK, before the first lock-free read."""
    if not _SCHEMA_OK[0]:
        with _LOCK:
            _db().close()
    con = _db()
    con.execute("PRAGMA query_only=ON")
    return con


PRIOR_SMOOTH = {"m0": 0.93, "T": 800.0}     # chosen on TRAIN only (kills MAE grid), 2026-10-09 - shadow, never shown


def smooth_prior_pf(pf, m0=PRIOR_SMOOTH["m0"], T=PRIOR_SMOOTH["T"]):
    """The live perf-2 projection re-run with the challenger prior target m(r) = 1 - (1 - m0) * T / (T + r) instead of
    the flat prior_m, r = the player's time-weighted rounds. Exact: the prior enters rate_all linearly and reaches the
    final rate through the environment blend with weight ke / (env rounds + ke). None when pf is not a perf-2 projection."""
    import perf
    if not pf or not str(pf.get("version") or "").startswith("perf-2") or pf.get("rate") is None or pf.get("history_rounds_weighted") is None:
        return None
    p, r = perf.PARAMS, float(pf["history_rounds_weighted"])
    m_new = 1 - (1 - m0) * T / (T + r)
    d_all = p["k0"] * p["pop"] * (m_new - p["prior_m"]) / (r + p["k0"])
    rate = float(pf["rate"]) + d_all * p["ke"] / (float(pf.get("env_rounds_weighted") or 0.0) + p["ke"])
    s = rate / float(pf["rate"]) if pf["rate"] else 1.0
    out = dict(pf, rate=rate, lambda_kills=pf["lambda_kills"] * s, **{"lambda": pf["lambda"] * s}, prior_target=round(m_new, 4),
               version=str(pf["version"]) + "-prior-smooth")
    out["sd"] = float(pf["spread_c"]) * (max(out["lambda"], 1.0) ** 0.5)
    out.pop("g7", None)
    return out


def _perf_challenger(gi, pf, cfg):
    """(inputs, config) that grade gi with another performance-model projection pf THROUGH the performance model.
    grader.grade_leg uses perf only for versions in cfg["perf_versions"] and otherwise falls back to the old pipeline, so
    pf's own version is added to a copy of the config - the live CONFIG is never changed. 2026-10-13: 'perf-2-prior-smooth'
    was missing there, so the prior_smooth shadow logged from 2026-10-09 was the old pipeline (lambda = shadow.legacy)."""
    vs = tuple(cfg.get("perf_versions", ()))
    return dict(gi, perf=pf), (cfg if pf.get("version") in vs else dict(cfg, perf_versions=vs + (pf.get("version"),)))


def shadow_alts(gi):
    """The comparison versions graded on these inputs: name -> config, or (inputs, config) for a comparison that swaps
    in another performance-model projection (graded by the performance model under that projection's own version)."""
    from grader import CONFIG
    # pre_fix_v2: the behaviour before the 2026-10-09 fix (gap centred on line + 0.5; Maps 1-3 pushes removed per case)
    alts = {"old_spread": dict(CONFIG, c=1.55, c_hs=1.30),
            "no_rest_bonus": dict(CONFIG, rest_bonus=0.0),
            "pre_fix_v2": dict(CONFIG, fix_v2=False),
            # 2026-10-08 environment revision: the old behaviour and the provisional taper, on identical inputs
            "online_none": dict(CONFIG, online_guard="none"),
            "online_taper": dict(CONFIG, online_guard="taper"),
            # 2026-10-08 perf-1 goes live on Maps 1-2: the old pipeline, on identical inputs
            "legacy": dict(CONFIG, perf_scopes=()),
            # 2026-10-08 release comparisons: without the BO3.gg fallback / with the perf-1 normal HS curve
            "no_bo3": dict(CONFIG, perf_versions=("perf-2", "perf-1")),
            "perf1_normal_hs": dict(CONFIG, perf_hs_dist=False),
            # Map 1 candidate (frozen 2026-10-08): the performance model for ONE map (no halving of Maps 1-2)
            "perf_map1": dict(CONFIG, perf_scopes=("12", "1"))}
    pf_ = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    if pf_.get("g7"):                     # 2026-10-09 perf-2 live: the perf-1 (G7) projection on the same inputs
        alts["perf1_g7"] = _perf_challenger(gi, pf_["g7"], CONFIG)
    sp = smooth_prior_pf(pf_)
    if sp:                                # 2026-10-09 challenger: prior that relaxes toward the population as history grows
        alts["prior_smooth"] = _perf_challenger(gi, sp, CONFIG)
    return alts


# lm-0 (CHALLENGERS.md, frozen 2026-10-23 06:08 UTC): the old pipeline with its Over / Under margins neutralised, on the
# same saved inputs. Shadow only - the live CONFIG keeps over_margin 1.0 / 0.6 (owner decision).
LM0 = {"id": "lm-0", "family": "legacy-margin-0", "version": "lm-0/2026-10-23",
       "keys": {"over_margin": {"kills": 0.0, "hs": 0.0}, "under_margin": {"kills": 0.0, "hs": 0.0}}}


def legacy_margin_shadow(gi, g):
    """lm-0 entry for one live grade g graded from inputs gi. Eligible only when the LIVE grade is on the old pipeline
    (projection_source 'legacy ...'); a performance-model grade gets an explicit not-eligible entry with no probability.
    Raw outcome chances and the recommendation (side / grade / units) are stored apart."""
    from grader import CONFIG, grade_leg
    src = str((g or {}).get("projection_source") or "")
    out = {"id": LM0["id"], "version": LM0["version"], "live_source": src or None}
    if not src.startswith("legacy"):
        return dict(out, eligible=False, reason="live grade not on the old pipeline (the margin never applies)")
    gg = grade_leg(gi, dict(CONFIG, **{k: dict(v) for k, v in LM0["keys"].items()}))
    gsrc = str(gg.get("projection_source") or "")
    if not gsrc.startswith("legacy") or "p_more" not in gg:
        return dict(out, eligible=False, source=gsrc or None, reason="regrade not on the old pipeline (inputs differ)")
    import pick_verdict
    v = pick_verdict.verdict(gg)
    side = {"More": "Over", "Less": "Under"}.get(v.get("side"), v.get("side"))
    return dict(out, eligible=True, source=gsrc, p_more=gg.get("p_more"), p_more_model=gg.get("p_more_model"),
                raw=gg.get("probs_raw"), **{"lambda": gg.get("lambda_final")},
                rec={"side": side, "grade": v.get("grade"), "units": v.get("units")},
                live={"p_more": (g or {}).get("p_more")},
                changed=(g or {}).get("p_more") != gg.get("p_more"))


def _factors(res):
    """Every input and every adjustment behind this grade (for measuring each factor later)."""
    gi, g, pb = res.get("grader_inputs") or {}, res.get("grade") or {}, res.get("player_block") or {}
    keep = ("kpr", "hs_pct", "maps_played", "sample_maps", "recent_avg", "recent_median", "recent_n", "kpr_last5",
            "round_swing", "rating", "teammate_ratings", "is_igl", "sniping", "dpr", "kast", "adr",
            "market_quality", "days_since_last_map", "map_win_probs", "trade_pr", "standin_for", "is_standin",
            "roster_confirmed", "on_listed_team", "recent", "per_map", "p_team_market", "odds_policy", "mapping_rev")
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
        from grader import grade_leg
        out["shadow"] = {"live_raw": g.get("probs_raw"),     # the live grade's own Over / Under / push chances
                         "live_online": g.get("online_adj")}   # online weight + the bonus actually applied
        for name, cfg in shadow_alts(gi).items():
            gin, cfg = cfg if isinstance(cfg, tuple) else (gi, cfg)
            gg = grade_leg(gin, cfg)
            # source = the projection that graded it (2026-10-13): a prior_smooth row is valid only with a
            # '...-prior-smooth' source - rows logged before then carry none and were the old pipeline
            out["shadow"][name] = {"p_more": gg.get("p_more"), "lambda": gg.get("lambda_final"),
                                   "grade": gg.get("grade"), "grade_less": gg.get("grade_less"),
                                   "raw": gg.get("probs_raw"), "source": gg.get("projection_source")}
    except Exception:
        pass
    try:   # lm-0 (shadow, 2026-10-23): the old pipeline without its Over margin - eligible old-pipeline grades only
        out.setdefault("shadow", {})["lm0"] = legacy_margin_shadow(gi, g)
    except Exception as e:     # noqa: BLE001 - a shadow never stops the pick log
        out.setdefault("shadow", {})["lm0"] = {"id": LM0["id"], "version": LM0["version"], "eligible": None,
                                               "error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:   # rm-1 / th-1 (shadow, CHALLENGERS.md 2026-10-23): rounds-mixture / thin-history chances - logged, never shown
        import cands_shadow
        out.setdefault("shadow", {})["cands"] = cands_shadow.entries(gi, g)
    except Exception as e:     # noqa: BLE001 - a shadow never stops the pick log
        out.setdefault("shadow", {})["cands"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:   # map veto veto-1 (display + logging only, 2026-10-23): the veto record (veto_serve, veto_log.db) this forecast's
        import veto_serve   # match had when it was logged - replayable from that record; never a grade input
        out.setdefault("shadow", {})["veto"] = veto_serve.forecast_ref(veto_serve.match_id_of(res))
    except Exception as e:     # noqa: BLE001 - a shadow never stops the pick log
        out.setdefault("shadow", {})["veto"] = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    try:   # shared-match challenger M1 (shadow, 2026-10-09): one simulated match per forecast - logged, never shown
        import match_model
        pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
        if (gi.get("scope") or "12") in ("1", "12") and pf.get("lambda_kills") is not None:
            m1 = match_model.shadow(pf, g.get("detail") or {}, gi.get("scope") or "12", gi.get("prop"), float(gi["line"]))
            if m1:
                out.setdefault("shadow", {})["match_m1"] = m1
    except Exception:
        pass
    try:   # perf2_hs (live on Maps 1-2 since G7; Map 1 still comparison): HS distribution averaged over kills
        pf = gi.get("perf") or {}
        if gi.get("prop") == "hs" and pf.get("lambda_kills") is not None and (gi.get("scope") or "12") in ("1", "12"):
            import perf
            o, u, pu = perf.hs_probs(pf["lambda_kills"], gi.get("scope") or "12", pf["hs_per_kill"], gi["line"], c=perf.kills_c(pf, gi.get("scope") or "12"))
            out.setdefault("shadow", {})["perf2_hs"] = {"p_more": round(o / (o + u), 4) if o + u else 0.5, "version": pf.get("version"),
                                                       "raw": {"over": round(o, 4), "under": round(u, 4), "push": round(pu, 4)}}
    except Exception:
        pass
    try:   # Maps 1-2 market-rounds candidate (perf_market, shadow only, 2026-10-13): logged, never shown. The module is
        # optional - absent (or failing) = nothing logged, the grade and the row are unaffected
        try:
            import perf_market
        except ImportError:
            perf_market = None
        if perf_market is not None and hasattr(perf_market, "shadow_entry"):
            se = perf_market.shadow_entry(gi, g)
            if se is not None:
                out.setdefault("shadow", {})["market_rounds"] = se
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
    rf = (pb.get("recent_form_map1") or {}) if _sc == "1" else (pb.get("recent_form_123") or {}) if _sc == "123" else (pb.get("recent_form") or {})
    scope = g.get("scope") or "12"
    key = "|".join(str(x) for x in (pb["player"]["slug"], g["prop"], g["line"], g["line_type"], nm["slug"]))
    if scope != "12":
        key += "|maps" + scope                         # Maps 1-2 keys unchanged
    bk = res.get("book") or {}                         # board grades: the platform + the provider's market id
    if bk.get("book") == "prizepicks":
        key += "|pp"                                   # one row per platform: the same number on both books never merges
    row = {
        "key": key, "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "player": pb["player"]["nick"], "slug": pb["player"]["slug"], "player_id": pb["player"]["id"],
        # 2026-10-13 (UNO MILLE audit): team = the team he plays THIS match for (the one the projection, the odds and the
        # match use), else the platform's label, else his team now; the BO3.gg profile team is kept as profile_team.
        # Before: always the BO3.gg profile team (ALLE 'UNO MILLE' while playing ex-Keyd Stars vs Galorys)
        "team": pb.get("playing_for") or res.get("team_hint") or pb.get("current_team"),
        "profile_team": pb.get("profile_team", pb.get("current_team")),
        "opponent": nm.get("opponent"), "match_slug": nm["slug"],
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
        # 2026-10-10: the platform the line came from (board grades) and its market id; member research lines: None
        "book": bk.get("book"), "market_id": bk.get("market_id"), "market_key": bk.get("market"),
    }
    try:
        import zlib
        row["_gi"] = zlib.compress(json.dumps(res.get("grader_inputs"), default=str).encode(), 6)
    except Exception:
        pass
    row["_probs"] = json.dumps(g.get("probs_raw")) if g.get("probs_raw") else None
    # which market supplied this grade's win probability (market_odds.select) - a change of source is a new forecast;
    # 2026-10-13: so is a changed market input of the same source (odds_sig) or another odds policy; the alias / matching
    # revision is recorded on every row (a mapping change that alters a line's selection shows up in odds_sig / source)
    gi = res.get("grader_inputs") or {}
    pv = (res.get("odds") or {}).get("provenance") or {}
    row["_odds_src"] = pv.get("selected")
    row["_odds_sig"] = pv.get("sig")
    if row["_odds_sig"] is None and res.get("odds") is not None:
        try:
            import market_odds
            row["_odds_sig"] = market_odds.odds_signature(res.get("odds"))
        except Exception:
            pass
    row["_odds_policy"] = pv.get("odds_policy") or gi.get("odds_policy")
    row["_mapping_rev"] = gi.get("mapping_rev") or pv.get("mapping_rev")
    row["_quote"] = json.dumps(bk.get("quote"), default=str) if bk.get("quote") else None   # board quote (measurement only)
    row["_fh1"] = res.get("_fh1")                     # fh-1 shadow record (perf_shadow; board grades only) - never a live input
    row["_platform_start"] = bk.get("start")          # the platform's listed start for this line (schedule_obs; 2026-10-23)
    try:   # vm-1 prop shadow (map context, research/map_veto/props): computed by the writer thread, only for a forecast
        import veto_serve   # row it actually appends - never on the member's request, never a grade input
        row["_vm1"] = {"match_id": veto_serve.match_id_of(res), "live_p_more": g.get("p_more"),
                       "gi": {k: (dict(gi[k]) if isinstance(gi.get(k), dict) else gi.get(k)) for k in ("prop", "line", "scope", "bo", "perf")}}
    except Exception:
        row["_vm1"] = None
    return row


_REV = {"at": 0.0, "rev": None}
REV_TTL = 600


def data_revision():
    """The HLTV dataset revisions a forecast was graded on - MIXED since 2026-10-14: 'matches=<n>;max_id=<id>;max_date=<day>'
    = the full tables (match_overview; what the last-10 / H2H records, stake checks, settlement and display read), cached
    10 minutes, then ';records_input=full;perf_input=<perf_live.input_revision()>' = the performance model's pinned N0
    (read each time, so a STALE mark shows at once). None when nothing can be read (never blocks logging). Called from the
    pick-log writer thread only."""
    now = time.time()
    if now - _REV["at"] >= REV_TTL:
        _REV["at"] = now
        try:
            import hltv
            con = hltv._connect()
            try:
                with con.cursor() as cur:
                    cur.execute("SELECT count(*), max(matchid), max(date) FROM match_overview")
                    n, mx, md = cur.fetchone()
            finally:
                con.close()
            _REV["rev"] = f"matches={n};max_id={mx};max_date={md}"
        except Exception:
            _REV["rev"] = None
    try:
        import perf_live
        pin = perf_live.input_revision()
    except Exception:
        pin = None
    if not pin:
        return _REV["rev"]
    return (_REV["rev"] or "matches=?") + f";records_input=full;perf_input={pin}"


def _shadow(row, vm1_in=None):
    try:
        sh = (json.loads(row.get("factors") or "{}") or {}).get("shadow")
    except (ValueError, TypeError):
        return None
    if vm1_in is not None:          # vm-1 shadow (A1 map context / C0 no-map control), added for the forecast row only
        try:
            import veto_serve
            ent = veto_serve.vm1_shadow(vm1_in.get("match_id"), vm1_in.get("gi"), vm1_in.get("live_p_more"))
        except Exception as e:     # noqa: BLE001 - a shadow never stops the pick log
            ent = {"A1": {"eligible": None, "reason": "shadow failed", "error": type(e).__name__},
                   "C0": {"eligible": None, "reason": "shadow failed", "error": type(e).__name__}}
        sh = dict(sh or {}, vm1=ent)
    return json.dumps(sh, default=str)


def _unchanged(last, row, extra):
    """The new grade repeats the last stored forecast: same line, side, grade, stake, model version, odds source, odds
    policy and market input (odds_sig), chance within 0.01 and projection within 0.2. mapping_rev is recorded, not
    compared: a mapping change that alters a line's selection already changes odds_sig / odds_source; one that does not
    leaves the forecast as it was (unchanged polling never writes a row)."""
    return (last["line"] == row["line"] and last["pick"] == row["pick"] and last["grade"] == row["grade"]
            and last["units"] == row["units"] and last["model_version"] == row.get("model_version")
            and abs((last["p_pick"] or 0) - (row["p_pick"] or 0)) < 0.01
            and abs((last["lambda"] or 0) - (row.get("lambda") or 0)) < 0.2
            and last["odds_source"] == extra.get("odds_source") and last["odds_sig"] == extra.get("odds_sig")
            and last["odds_policy"] == extra.get("odds_policy"))


def _record_mapping(con, rev):
    """The first forecast made under a mapping revision stores that revision's full content (mapping_revisions,
    append-only, same transaction as the forecast - a rolled-back write leaves no orphan) - any old alias / matching
    state can be replayed (mapping.content(rev))."""
    if not rev:
        return
    try:
        import mapping
        if con.execute("SELECT 1 FROM mapping_revisions WHERE rev = ?", (rev,)).fetchone() or mapping.revision() != rev:
            return
        mapping.record(con)
    except Exception:
        pass


def _forecast(con, pid, row, kind, gi=None, extra=None):
    """Append one forecast to the history - only when it differs from the last one stored for this pick.
    The picks row itself stays the FIRST published forecast (never overwritten)."""
    extra = extra or {}
    last = con.execute("SELECT line, pick, p_pick, grade, units, lambda, model_version, odds_source, odds_sig, odds_policy "
                       "FROM forecasts WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    if last and kind == "update" and _unchanged(last, row, extra):
        return False
    con.execute("INSERT INTO forecasts (pick_id, kind, logged_at, match_start, line, pick, p_pick, p_more, grade, units, "
                "lambda, p_win, rec_hit, sample_maps, model_version, shadow, inputs, probs_raw, data_rev, odds_source, "
                "odds_policy, mapping_rev, odds_sig, git_commit, quote) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, kind, row["logged_at"], row.get("match_start"), row["line"], row["pick"], row["p_pick"],
                 row.get("p_more"), row["grade"], row["units"], row.get("lambda"), row.get("p_win"), row.get("rec_hit"),
                 row.get("sample_maps"), row.get("model_version"), _shadow(row, extra.get("vm1")), gi, extra.get("probs_raw"), extra.get("data_rev"),
                 extra.get("odds_source"), extra.get("odds_policy"), extra.get("mapping_rev"), extra.get("odds_sig"),
                 extra.get("git_commit"), extra.get("quote")))
    if os.getenv('SCOUT_GRADECORE_SHADOW', '0') == '1':
        from gradecore.capture import capture
        capture(con, 'cs2', row, extra, con.execute('SELECT last_insert_rowid()').fetchone()[0])
    _record_mapping(con, extra.get("mapping_rev"))
    return True


SHADOW_KEYS = (("live", "p_more"), ("fh1", "p_more"), ("live", "lambda"), ("fh1", "lambda"), ("live", "units"), ("fh1", "units"),
               ("live", "grade"), ("fh1", "grade"), ("live", "side"), ("fh1", "side"))
# a record's two main arms (compared field by field above) and its parts that are not forecasts; every OTHER dict in a
# record is a shadow arm (lc1, cm1, any later registration)
_NOT_ARMS = ("live", "fh1", "published", "n0_check", "history", "read_at", "adds")


def _shadow_arms(rec):
    """Each shadow arm's own forecast and availability. 2026-10-18 audit: a record was repeated only when the live / fh-1
    arms changed, so cm-1 becoming available after its deploy, or moving with its 2-hourly tables, was never saved while
    the live grade stayed the same (37 forward units had no cm-1 value; in 48 of 102 consecutive records over a tables
    change its chance moved). Now an arm that appears, becomes available or unavailable (its reason / error type), or
    changes its value appends a new timestamped record. Old records are never touched."""
    return {k: (v.get("p_more"), v.get("lambda"), v.get("ineligible"), str(v["error"]).split(":")[0][:60] if v.get("error") else None)
            for k, v in sorted(rec.items()) if k not in _NOT_ARMS and isinstance(v, dict)}


def _shadow_forecast(con, pid, row, fh, git_commit=None):
    """Append the fh-1 record for this pre-match grade when it differs from the last one stored for this pick: either arm's
    chance / projection / stake / grade / side, the line, the history snapshot, or any shadow arm's value or availability
    (_shadow_arms). An unchanged record is not repeated (a
    moved price alone changes neither arm's chance - the performance model ignores odds - so it writes no record). Its inputs
    are stored once each (content-addressed). Never raises: the live forecast is already written."""
    if not fh or not isinstance(fh.get("rec"), dict):
        return False
    try:
        rec = fh["rec"]
        last = con.execute("SELECT rec FROM shadow_forecasts WHERE pick_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
        if last:
            lr = json.loads(last["rec"])
            if (lr.get("line") == rec.get("line") and lr.get("inputs_sha") == rec.get("inputs_sha")
                    and all((lr.get(a) or {}).get(b) == (rec.get(a) or {}).get(b) for a, b in SHADOW_KEYS)
                    and _shadow_arms(lr) == _shadow_arms(rec)):
                return False
        for h, (kind, body) in (fh.get("bodies") or {}).items():
            con.execute("INSERT OR IGNORE INTO shadow_inputs (sha, kind, created_at, body) VALUES (?,?,?,?)",
                        (h, kind, rec.get("computed_at"), body))
        fid = con.execute("SELECT MAX(id) FROM forecasts WHERE pick_id = ?", (pid,)).fetchone()[0]
        con.execute("INSERT INTO shadow_forecasts (pick_id, forecast_id, challenger, logged_at, match_start, line, prop, scope, "
                    "live_p_more, ch_p_more, live_lambda, ch_lambda, n0_exact, identical, grp, inputs_sha, gi_sha, git_commit, rec) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, fid, rec.get("challenger"), row["logged_at"], row.get("match_start"), rec.get("line"), rec.get("prop"),
                     rec.get("scope"), rec["live"].get("p_more"), rec["fh1"].get("p_more"), rec["live"].get("lambda"),
                     rec["fh1"].get("lambda"), int(bool(rec["n0_check"].get("exact"))), int(bool(rec.get("identical_history"))),
                     rec.get("group"), rec.get("inputs_sha"), rec.get("gi_sha"), git_commit,
                     json.dumps(rec, sort_keys=True, default=str)))
        return True
    except Exception:
        import traceback
        traceback.print_exc()
        return False


def settlement_lag(now=None, hours=8):
    """Operations (/health): member and board picks still open `hours` after their listed start - a best-of-3 is long over
    by then, so these wait on missing results or an identity / settlement problem. Read-only."""
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    con = _db()
    try:
        n, oldest = con.execute("SELECT COUNT(*), MIN(substr(match_start, 1, 19)) FROM picks WHERE status = 'open' AND test IN (0, 2) "
                                "AND match_start IS NOT NULL AND substr(match_start, 1, 19) < ?", (cut,)).fetchone()
        m = con.execute("SELECT COUNT(DISTINCT match_slug) FROM picks WHERE status = 'open' AND test IN (0, 2) "
                        "AND match_start IS NOT NULL AND substr(match_start, 1, 19) < ?", (cut,)).fetchone()[0]
    finally:
        con.close()
    return {"open_after_8h": n, "matches": m, "oldest_start": oldest, "hours": hours}

def _utc_time(s):
    """An ISO time as an aware UTC datetime (no offset = UTC, as recap / learning read it); None when unreadable."""
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _before_start(row, cur):
    """The new grade was made before the match started (by the start the row has, and the one it brings). Compared as
    times (2026-10-13 audit: as strings a start with another UTC offset, or written without the 'T', compared wrong)."""
    now = row["logged_at"]
    for st in (cur["match_start"], row.get("match_start")):
        if not st:
            continue
        a, b = _utc_time(now), _utc_time(st)
        if (a >= b) if a is not None and b is not None else now >= st.replace("Z", "+00:00")[:25]:
            return False
    return True


def write_row(row):
    if row is None:
        return None
    row = dict(row)
    gi = row.pop("_gi", None)                       # the exact grader inputs - forecasts table only
    fh = row.pop("_fh1", None)                      # fh-1 shadow record - shadow_forecasts only
    pstart = row.pop("_platform_start", None)       # the platform's listed start - schedule_obs only
    vm1_in = row.pop("_vm1", None)                  # vm-1 shadow inputs - forecasts.shadow only
    extra = {"vm1": vm1_in, "probs_raw": row.pop("_probs", None), "data_rev": data_revision(), "odds_source": row.pop("_odds_src", None),
             "odds_sig": row.pop("_odds_sig", None), "odds_policy": row.pop("_odds_policy", None),
             "mapping_rev": row.pop("_mapping_rev", None), "quote": row.pop("_quote", None),
             "git_commit": os.environ.get("RENDER_GIT_COMMIT") or None}    # the deployed commit (Render sets it)
    key, test = row["key"], row["test"]
    with _LOCK:
        con = _db()
        try:
            cur = con.execute("SELECT id, test, status, match_start FROM picks WHERE key = ?", (key,)).fetchone()
            if cur and cur["status"] == "open" and _before_start(row, cur):
                _forecast(con, cur["id"], row, "update", gi, extra)  # every changed pre-match forecast, timestamped
                _shadow_forecast(con, cur["id"], row, fh, extra.get("git_commit"))
                con.execute("UPDATE picks SET regrades = COALESCE(regrades, 0) + 1 WHERE id = ?", (cur["id"],))
            if cur:
                _observe(con, cur["id"], row, pstart)        # a re-listed start of this line / match (changes only)
                # a real member asking for a line first seen in a test makes it a real pick. 2026-10-13 audit D3: so does the
                # board grading a line an owner load test reached first (it stayed test 1 - out of every cohort - with no
                # platform); the board's write fills the platform + market id the row lacks. A board row the owner marked
                # as a test by hand (mark_tests) has its platform and stays a test.
                bw = int(int(test or 0) == 2)
                con.execute("UPDATE picks SET requests = requests + 1, "
                            "test = CASE WHEN ? AND test = 1 AND book IS NULL THEN 2 WHEN ? THEN test ELSE 0 END, "
                            "on_board = CASE WHEN ? THEN 1 ELSE COALESCE(on_board, 0) END, "
                            "book = CASE WHEN ? THEN COALESCE(book, ?) ELSE book END, "
                            "market_id = CASE WHEN ? THEN COALESCE(market_id, ?) ELSE market_id END, "
                            "market_key = CASE WHEN ? THEN COALESCE(market_key, ?) ELSE market_key END WHERE id = ?",
                            (bw, int(bool(test)), bw, bw, row.get("book"), bw, row.get("market_id"), bw, row.get("market_key"),
                             cur["id"]))
                con.commit()
                return cur["id"]
            row["on_board"] = 1 if int(test or 0) == 2 else 0
            cols = ", ".join(row)
            con.execute(f"INSERT INTO picks ({cols}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            pid = con.execute("SELECT id FROM picks WHERE key = ?", (key,)).fetchone()["id"]
            _forecast(con, pid, row, "first", gi, extra)
            _observe(con, pid, row, pstart)                  # the start(s) as first listed - the ORIGINAL scheduled start
            if _before_start(row, {"match_start": row.get("match_start")}):
                _shadow_forecast(con, pid, row, fh, extra.get("git_commit"))
            con.commit()
            return pid
        finally:
            con.close()


OBS_MAX = 20              # schedule_obs rows per pick and kind (a line re-listed more often keeps its first 20 starts)


def _z(t):
    """A start as 'YYYY-MM-DDTHH:MM:SSZ' (UTC) - one spelling per instant, so a re-read never looks like a reschedule."""
    t = t if isinstance(t, datetime) else _utc_time(t) if t else None
    if t is None:
        return None
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_obs(con, pid, kind, start, seen_at, source, ref=None):
    """One schedule observation (caller holds _LOCK and commits). Same start again = nothing; bounded per pick and kind."""
    st = _z(start)
    if pid is None or st is None:
        return
    con.execute("INSERT OR IGNORE INTO schedule_obs (pick_id, kind, start, seen_at, source, ref) SELECT ?, ?, ?, ?, ?, ? "
                "WHERE (SELECT COUNT(*) FROM schedule_obs WHERE pick_id = ? AND kind = ?) < ?",
                (pid, kind, st, seen_at, source, None if ref is None else str(ref), pid, kind, OBS_MAX))


def _observe(con, pid, row, pstart):
    """The starts a write brings: the match source's start for the pick's own match, and the platform's listed start for
    its line (board grades)."""
    _record_obs(con, pid, "match", row.get("match_start"), row.get("logged_at"), "grade")
    if row.get("book") and pstart:
        _record_obs(con, pid, "platform", pstart, row.get("logged_at"), row.get("book"), row.get("market_id"))


def _save_obs(pid, kind, start, source, ref=None):
    try:
        with _LOCK:
            con = _db()
            try:
                _record_obs(con, pid, kind, start, datetime.now(timezone.utc).isoformat(timespec="seconds"), source, ref)
                con.commit()
            finally:
                con.close()
    except sqlite3.Error:
        pass


def schedule_obs(pick_id):
    """Every start observation of one pick, oldest first ([] when none / unreadable). Lock-free read."""
    if pick_id is None:
        return []
    try:
        con = _read_db()
        try:
            return [dict(x) for x in con.execute("SELECT kind, start, seen_at, source, ref FROM schedule_obs "
                                                 "WHERE pick_id = ? ORDER BY seen_at, id", (pick_id,))]
        finally:
            con.close()
    except sqlite3.Error:
        return []


_BACKFILLED = [False]


def backfill_obs(force=False):
    """Once (schema_marks 'schedule_obs_backfill'): the start observations picks made before 2026-10-23 already have
    evidence for - every match_start their forecast history recorded (kind 'match', source 'forecast'), and the
    platforms' own line logs: Underdog's ud_moves.db (every new start of a line id) and PrizePicks' pp_lines.jsonl
    (opens / moves / removals with the line's start, by market id), as kind 'platform' with the time each was logged.
    Read outside the lock; one write transaction. Returns the rows offered (duplicates are ignored)."""
    if _BACKFILLED[0] and not force:
        return 0
    with _LOCK:
        con = _db()
        try:
            done = con.execute("SELECT 1 FROM schema_marks WHERE name = 'schedule_obs_backfill'").fetchone()
        finally:
            con.close()
    if done and not force:
        _BACKFILLED[0] = True
        return 0
    con = _read_db()
    try:
        fc = con.execute("SELECT pick_id, match_start, MIN(logged_at) FROM forecasts WHERE match_start IS NOT NULL "
                         "GROUP BY pick_id, match_start").fetchall()
        lines = {}
        for pid, book, mid in con.execute("SELECT id, book, market_id FROM picks WHERE book IS NOT NULL AND market_id IS NOT NULL"):
            lines.setdefault((book, str(mid)), []).append(pid)
    finally:
        con.close()
    rows = [(pid, "match", st, at, "forecast", None) for pid, st, at in fc]
    ud = DB.parent / ".cache" / "ud_moves.db"
    if ud.exists():
        try:
            uc = sqlite3.connect(f"file:{ud}?mode=ro", uri=True, timeout=10)
            try:
                for at, start, lid in uc.execute("SELECT at, start, line_id FROM moves WHERE line_id IS NOT NULL ORDER BY at"):
                    for pid in lines.get(("underdog", str(lid)), ()):
                        rows.append((pid, "platform", start, at, "underdog line log", lid))
            finally:
                uc.close()
        except sqlite3.Error:
            pass
    pp = DB.parent / ".cache" / "pp_lines.jsonl"
    try:
        with pp.open(encoding="utf-8") as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                for pid in lines.get(("prizepicks", str(e.get("id"))), ()):
                    rows.append((pid, "platform", e.get("start"), e.get("at"), "prizepicks line log", e.get("id")))
    except OSError:
        pass
    with _LOCK:
        con = _db()
        try:
            for pid, kind, st, at, src, ref in sorted(rows, key=lambda x: (x[0], str(x[3] or ""))):
                _record_obs(con, pid, kind, st, at, src, ref)
            con.execute("INSERT OR IGNORE INTO schema_marks (name, at) VALUES ('schedule_obs_backfill', ?)",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
            con.commit()
        finally:
            con.close()
    _BACKFILLED[0] = True
    return len(rows)


def original_start(r, obs=None):
    """(the pick's ORIGINAL scheduled start, where it comes from). The platform's listed start for the pick's line when
    it was recorded with the first forecast (picks made before 2026-10-23 have none: a later observation may already be
    the re-listed start, so it is never taken as the original); else the start logged with the pick."""
    obs = schedule_obs(_col(r, "id")) if obs is None else obs
    logged, mid = _utc_time(_col(r, "logged_at")), _col(r, "market_id")
    for o in obs:
        seen = _utc_time(o.get("seen_at"))
        if (o.get("kind") == "platform" and (mid is None or o.get("ref") in (None, str(mid)))
                and logged is not None and seen is not None and seen <= logged + timedelta(minutes=1)):
            return _utc_time(o["start"]), f"{o.get('source')} listing at the first forecast"
    t = _utc_time(_col(r, "match_start"))
    return t, "start logged with the pick" if t else None


def relisted_starts(r, obs=None, min_h=None):
    """[(start, observation)] - other starts seen for THIS pick's own line (the platform listing with its market id) or
    own match (the same pick key = the same match slug / HLTV id), more than min_h (default matchid.WINDOW_H) from the
    logged start; in the order they were seen."""
    import matchid
    obs = schedule_obs(_col(r, "id")) if obs is None else obs
    base, mid = _utc_time(_col(r, "match_start")), _col(r, "market_id")
    gap = timedelta(hours=matchid.WINDOW_H if min_h is None else min_h)
    out = []
    for o in obs:
        if o.get("kind") == "match" or (o.get("kind") == "platform" and (mid is None or o.get("ref") in (None, str(mid)))):
            t = _utc_time(o.get("start"))
            if t is not None and base is not None and abs(t - base) > gap:
                out.append((t, o))
    return sorted(out, key=lambda x: (x[1].get("seen_at") or "", x[0]))


def _hltv_start_evidence(mid, day=None):
    """(lo, hi, source) of HLTV match `mid`'s start: HLTV's schedule time for that exact id (matchid's copy), else the
    match's UTC date (00:00 .. 24:00), else (None, None, why)."""
    try:
        import matchid
        t = matchid.hltv_start(mid)
    except Exception:
        t = None
    if t is not None:
        return t, t, f"HLTV schedule time of match {mid}"
    try:
        d = datetime.fromisoformat(str(day)[:10]).replace(tzinfo=timezone.utc) if day else None
    except ValueError:
        d = None
    if d is not None:
        return d, d + timedelta(days=1), f"HLTV match {mid} dated {str(day)[:10]} (UTC day only)"
    return None, None, f"HLTV match {mid}: no schedule time or date"


def _bo3_start_evidence(mt):
    """(lo, hi, source) of a BO3.gg match's start (its exact slug): map 1's begin_at, else the match's start_date."""
    g1 = next((g for g in (mt or {}).get("games") or [] if g.get("number") == 1), None)
    t = _utc_time(g1.get("begin_at")) if g1 and g1.get("begin_at") else None
    if t is not None:
        return t, t, "BO3.gg map 1 begin_at"
    t = _utc_time((mt or {}).get("start_date")) if (mt or {}).get("start_date") else None
    return (t, t, "BO3.gg match start_date") if t is not None else (None, None, "BO3.gg match: no start time")


def settlement_timing(r, lo, hi, source, obs=None):
    """The platform's reschedule rule for a pick whose match was found by exact identity -> (decision, evidence).
    decision: platform_rules.reschedule_decision; evidence: what is stored with the settlement (result.schedule) - the
    original start and where it comes from, the start bounds and their source, every re-listed start seen."""
    obs = schedule_obs(_col(r, "id")) if obs is None else obs
    orig, how = original_start(r, obs)
    d = platform_rules.reschedule_decision(_col(r, "book"), orig, lo, hi, source)
    ev = {"original": _z(orig), "original_from": how, "start_lo": _z(lo), "start_hi": _z(hi), "source": source,
          "relisted": [_z(t) for t, _o in relisted_starts(r, obs)], "decision": d["action"], "late_h": d.get("late_h"),
          "rule_verified": d.get("verified"), "reason": d["reason"]}
    if lo is not None:
        _save_obs(_col(r, "id"), "actual", lo, source + (" (lower bound)" if hi is not None and hi != lo else ""))
    return d, ev


def _reschedule_hold(r, obs=None):
    """A pick whose own line / match was seen re-listed to a later start (inside the platform's window) and that the
    exact identity has not found: pending with the reason (never voided by the VOID_AFTER_D clock) - None otherwise
    (no verified rule, no re-listing, or re-listed beyond the window: the clock's void is the rule's void)."""
    p = platform_rules.platform_of(_col(r, "book"))
    rule = platform_rules.rule(p, "reschedule")
    if p is None or not rule.get("verified") or not rule.get("void_after_h"):
        return None
    obs = schedule_obs(_col(r, "id")) if obs is None else obs
    orig, _how = original_start(r, obs)
    later = relisted_starts(r, obs)
    if orig is None or not later:
        return None
    t, o = later[-1]
    late = (t - orig).total_seconds() / 3600
    if late > rule["void_after_h"]:
        return None
    return {"reason": f"{p}: line re-listed for {_z(t)} (seen {o.get('seen_at')}, {round(late, 1)} h from the original "
                      f"start {_z(orig)}) - no exact HLTV identity found at the new start; pending, not voided by the clock",
            "rule": "reschedule", "platform": p}


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
        if 0 < (g.get("winner_clan_score") or 0) + (g.get("loser_clan_score") or 0) < 13:
            return []                                  # a map can't end under 13 rounds: forfeit / technical win
        # 2026-10-13 audit D13: a quick scoreboard without headshots has not published them yet - never 0 (an HS pick
        # used to settle as a big Under); the caller waits for them (or HLTV)
        out.append({"map": g.get("map_name"), "kills": row["kills_sum"], "headshots": row.get("headshots_sum")})
    return out if len(out) == 2 else []


def settle(b, map_rows):
    """Fill in actual Maps 1-2 totals for matches that have finished."""
    now = datetime.now(timezone.utc)
    try:
        backfill_obs()                                # once: start evidence older picks already have (2026-10-23)
    except Exception:
        import traceback
        traceback.print_exc()
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status = 'open'").fetchall()
        con.close()
    done = 0
    for r in rows:
        try:                                          # 2026-10-13 audit B4: one failing pick (a BO3.gg error, a missing
            done += _settle_one(b, map_rows, r, now)  # stat) used to stop the whole pass - every pass, every later pick
        except Exception:
            import traceback
            traceback.print_exc()
    try:
        revert_full_scoreboard_corrections(now=now)
    except Exception:
        pass
    try:
        recheck_bo3_quick(b, map_rows, now=now)
    except Exception:
        pass                                           # never blocks settling; the next pass tries again
    try:
        recheck_bo3_with_hltv(now=now)
    except Exception:
        pass
    return done


def _settle_one(b, map_rows, r, now):
    """One open pick: 1 when it was settled / pushed / voided, else 0."""
    try:
        start = datetime.fromisoformat((r["match_start"] or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    if start.tzinfo is None:                      # 2026-10-13 audit B4: a start without an offset is UTC (it used to raise
        start = start.replace(tzinfo=timezone.utc)  # on the first comparison and stop the pass)
    # the data decides, not the clock: HLTV's result settles a pick as soon as the scraper has it
    # (listed start times are often off); BO3.gg's only after SETTLE_MIN minutes
    early = now < start + timedelta(minutes=SETTLE_MIN)
    if now < start:
        return 0          # 2026-10-13 audit D1: never before the listed start (was 12 h before: an earlier meeting of the
        #                   same teams that day could settle it)
    if (_col(r, "scope") or "12") != "12":         # Maps 1-3 / Map 1 lines: HLTV only (has map 3 and BO1)
        return _set_result(r, _settle_scope(r, now, start), now)
    if early and not (r["match_slug"] or "").startswith("hltv-"):
        hv = _hltv_totals(r, same_day_only=True)
        return _set_result(r, hv if isinstance(hv, tuple) else None, now)
    if (r["match_slug"] or "").startswith("hltv-"):
        return _set_result(r, _settle_hltv(r, now, start), now)
    # HLTV first - the official record (BO3.gg's quick scoreboards and map order are sometimes off)
    hv = _hltv_totals(r)
    if hv in ("transient", "pending"):
        return 0          # 2026-10-13 audit D5: HLTV unreachable - never BO3.gg's result or a void as if HLTV had no match
        #                   2026-10-23: "pending" = HLTV shows a case the platform's published rules do not decide
    if hv == "wait" and now < start + timedelta(hours=HLTV_WAIT_H):
        return 0          # HLTV has the match but not this stat yet (scraped every ~2 h)
    if isinstance(hv, tuple):
        return _set_result(r, hv, now)
    # fresh list (15 min): a 12-hour-old copy still shows finished matches as upcoming
    ms = b.player_matches(r["player_id"], pages=1, ttl=0.25).get("results") or []
    mt = next((m for m in ms if m.get("slug") == r["match_slug"]), None)
    upd = None
    stat = "headshots" if r["prop"] == "hs" else "kills"
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
        quick = False
        if len(maps) < 2 or any(x.get(stat) is None for x in maps):
            qr = _quick_rows(b, r["player_id"], mt)       # full stats not out yet
            maps, quick = (qr, True) if qr else (maps, False)
        if len(maps) == 2 and all(x.get(stat) is not None for x in maps):   # D13: a missing stat is never a 0
            # quick=True: settled from BO3.gg's quick scoreboard - recheck_bo3_quick() re-derives it from the full
            # scoreboard once that is out (2026-10-12 audit: the quick one can be a kill short)
            actual = sum(x[stat] for x in maps)
            if actual == r["line"]:                   # whole-number line landed exactly: push
                upd = ("push", json.dumps([x["map"] for x in maps]), actual, None)
            else:
                hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
                upd = ("settled", json.dumps([x["map"] for x in maps]), actual, int(hit))
            # 2026-10-23: the platform's reschedule window, on the start of this exact BO3.gg match (its slug)
            lo, hi, src = _bo3_start_evidence(mt)
            upd = _timed(r, upd, {"source": "bo3", "rounds": [x.get("rounds") for x in maps],
                                  "won": [x.get("won") for x in maps], **({"quick": True} if quick else {}),
                                  "per_map": [[x.get("kills"), x.get("headshots")] for x in maps]}, lo, hi, src)
            if upd is None:
                return 0                              # pending with the reason: never the clock's void below
        elif (len([g for g in mt.get("games") or [] if (g.get("number") or 9) <= 2]) >= 2
              and now < start + timedelta(days=VOID_AFTER_D)):
            return 0      # Maps 1-2 were played but BO3.gg hasn't published the stats yet: wait
        else:
            upd = ("void", json.dumps([x["map"] for x in maps]), None, None)   # forfeit / BO1 / sat out
    elif mt and mt.get("status") == "defwin":            # forfeit: no maps played
        upd = ("void", None, None, None)
    elif now > start + timedelta(days=VOID_AFTER_D):
        upd = ("void", None, None, None)
    return _set_result(r, upd, now)


def _set_result(r, upd, now):
    """Store a settlement (status, actual_maps, actual, hit) for one pick: 1, or 0 when there is none yet."""
    if not upd:
        return 0
    with _LOCK:
        con = _db()
        con.execute("UPDATE picks SET status=?, actual_maps=?, actual=?, hit=?, settled_at=? WHERE id=?",
                    (*upd, now.isoformat(timespec="seconds"), r["id"]))
        con.commit()
        con.close()
    return 1


HLTV_RECHECK_MAX = 60        # picks per settle pass
_RECHECK = {}                # recheck rotation: pass name -> the last key it checked (this process)


def _rotate(items, key, name, limit):
    """Up to `limit` items, starting after the last one the previous pass checked and wrapping around. 2026-10-13 audit
    D7: the first `limit` rows in row order were taken every pass, so rows HLTV never has (outside the scraper's coverage)
    held every newer row back until they left the window."""
    items = sorted(items, key=key)
    last = _RECHECK.get(name)
    if last is not None:
        items = [x for x in items if key(x) > last] + [x for x in items if key(x) <= last]
    out = items[:limit]
    if out:
        _RECHECK[name] = key(out[-1])
    return out


def _hltv_due(r):
    """A BO3.gg settlement (the row's own source - a corrected row keeps the BO3.gg detail nested) HLTV has not checked,
    or checked while it was held for review."""
    try:
        res = json.loads(r["result"] or "{}")
    except ValueError:
        return False
    if res.get("source") != "bo3":
        return False
    if not res.get("hltv_checked") or res.get("review"):
        return True
    # 2026-10-14: an HLTV-confirmed row whose stored BO3.gg map detail CONTRADICTS its settled total (confirmations before
    # this date copied the BO3.gg detail forward - 7 Fruju vs MARKandLARRY picks) is due once more; the confirmation now
    # writes HLTV's own detail and source. Confirmed rows with consistent detail are not re-queued (they would lengthen the
    # rotation for new settlements waiting for their first HLTV check).
    try:
        t = _scope_total_of(res, r["scope"], r["prop"])
    except Exception:
        return False
    return t is not None and r["actual"] is not None and t != r["actual"]


def recheck_bo3_with_hltv(now=None, apply=True, days=7, limit=HLTV_RECHECK_MAX):
    """2026-10-12 audit D4: picks settled from BO3.gg (cool4st 23 vs HLTV 24, chelo 15 vs 16) are re-derived from HLTV
    once HLTV has the match, through scope_result() (strict identity: the player's own team, the opponent's HLTV id,
    exactly one match). A different total is corrected with old and new values in settlement_corrections.jsonl; an
    equal one is marked hltv_checked (and a hold for review released). HLTV unreachable / not there yet = untouched (next
    pass); `limit` rows per pass, in rotation."""
    from scope import outcome as _oc
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _LOCK:
        con = _db()
        # a pick HLTV confirmed while it was held for review (before 2026-10-13 the hold was kept) is re-derived once more:
        # confirmed again -> released, different -> corrected to HLTV's total
        rows = con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push') AND settled_at >= ? "
                           "AND (scope IS NULL OR scope = '12') AND result LIKE '%\"source\": \"bo3\"%'", (since,)).fetchall()
        con.close()
    rows = [r for r in rows if _hltv_due(r)]
    out = {"checked": 0, "confirmed": 0, "corrected": [], "waiting": 0}
    for r in _rotate(rows, lambda x: x["id"], "hltv", limit):
        try:
            res = scope_result(r)
        except Exception:
            out["waiting"] += 1
            continue
        if res.get("status") != "ok":
            out["waiting"] += 1
            continue
        out["checked"] += 1
        try:
            old = json.loads(r["result"] or "{}")
        except ValueError:
            old = {}
        st, hit = _oc(res["total"], r["line"], r["pick"])
        if res["total"] == r["actual"] and st == r["status"] and (hit == r["hit"] or (hit is None and r["hit"] is None)):
            out["confirmed"] += 1
            if apply:
                # 2026-10-13 audit D4: HLTV (authoritative) confirms the total - a hold for review / provisional mark is
                # released (it used to be copied forward, and the row was never looked at again: unscored for good)
                # 2026-10-14: the stored detail becomes HLTV's own (source, per-map, rounds, won); the BO3.gg settlement is kept
                # nested as bo3_detail, and the change is logged (same total / status / hit) - before, the BO3.gg per-map
                # numbers were copied forward and could disagree with the confirmed total (settlement audit)
                rel = {k: False for k in ("review", "provisional") if k in old}
                # the BO3.gg record is kept for the audit trail with its hold released too (the hold itself is recorded in
                # settlement_reviews.jsonl): a confirmed pick carries no review flag anywhere
                bo3 = dict({k: v for k, v in old.items() if k != "bo3_detail"}, **rel)
                detail = {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                          "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], "hltv_checked": True,
                          "hltv_total": res["total"], **rel, "bo3_detail": bo3, **_ident(res),
                          **({"schedule": old["schedule"]} if isinstance(old, dict) and old.get("schedule") else {})}
                old_t = None
                try:
                    old_t = _scope_total_of(old, r["scope"], r["prop"])
                except Exception:
                    pass
                with _LOCK:
                    con = _db()
                    con.execute("UPDATE picks SET actual_maps=?, result=? WHERE id=?", (json.dumps(res["maps"]), json.dumps(detail, default=str), r["id"]))
                    con.commit()
                    con.close()
                if old_t is not None and old_t != res["total"]:
                    d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": "12", "line": r["line"], "pick": r["pick"],
                         "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
                         "new": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]}, "hltv_match": res["hltv_match"],
                         "detail_old": {"per_map": old.get("per_map"), "detail_total": old_t, "source": old.get("source")},
                         "detail_new": {"per_map": res["per_map"], "detail_total": res["total"], "source": "hltv"},
                         "reason": "HLTV confirms the settled total; stored map detail replaced by HLTV's (it was BO3.gg's full scoreboard)"}
                    out.setdefault("detail_fixed", []).append(d)
                    with open(CORRECTIONS, "a", encoding="utf-8") as f:
                        f.write(json.dumps(dict(d, corrected_at=now.isoformat(timespec="seconds")), default=str) + "\n")
            continue
        detail = {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                  "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], "hltv_checked": True, "bo3_detail": old, **_ident(res),
                  **({"schedule": old["schedule"]} if isinstance(old, dict) and old.get("schedule") else {})}   # start evidence kept
        d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": "12", "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": res["total"], "status": st, "hit": hit}, "hltv_match": res["hltv_match"],
             "reason": "HLTV result differs from the BO3.gg settlement"}
        out["corrected"].append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, actual_maps=?, result=? WHERE id=?",
                            (st, res["total"], hit, json.dumps(res["maps"]), json.dumps(detail, default=str), r["id"]))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=now.isoformat(timespec="seconds")), default=str) + "\n")
    return out


QUICK_RECHECK_D = 7          # BO3.gg settlements are compared with the full scoreboard for this long
QUICK_RECHECK_MAX = 40       # matches per settle pass (each = one player list + two scoreboards, cached)
REVIEWS = DB.parent / "settlement_reviews.jsonl"
SOURCE_RULE = ("HLTV authoritative; BO3.gg provisional until HLTV re-derives it; BO3.gg quick scoreboard preferred over the "
               "full one (2026-10-12 audit: from 2026-03 quick = HLTV on ~99.2% of player-maps, full on 96.1% - the full "
               "scoreboard subtracts team-kills); quick != full with the line between them -> held for review, not scored")


def _is_quick(res):
    """A BO3.gg settlement not yet compared with the full scoreboard: marked quick, or (before the mark, 2026-10-12) no
    round counts."""
    return res.get("source") == "bo3" and not res.get("full_checked") and (
        res.get("quick") or all(x is None for x in (res.get("rounds") or [None])))


def _between(line, a, b):
    """The line separates the two totals (the outcome depends on which source is right)."""
    lo, hi = min(a, b), max(a, b)
    return lo != hi and lo <= line <= hi


def recheck_bo3_quick(b, map_rows, now=None, apply=True, days=QUICK_RECHECK_D, limit=QUICK_RECHECK_MAX):
    """Compare every BO3.gg quick-scoreboard settlement with BO3.gg's full scoreboard once it is out (same player id,
    match slug, maps 1-2 by number). The quick total STANDS (SOURCE_RULE). Equal -> marked full_checked. Different ->
    the result records both totals ("discrepancy") and stays provisional; when the line sits between them the pick is
    held for review ("review": True - the learning ledger does not score it) and logged in settlement_reviews.jsonl.
    Status / total / hit are never changed here. Returns {"checked", "confirmed", "disagree", "held", "waiting"}."""
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with _LOCK:
        con = _db()
        rows = con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push') AND settled_at >= ? "
                           "AND (scope IS NULL OR scope = '12') AND result LIKE '%\"source\": \"bo3\"%'", (since,)).fetchall()
        con.close()
    groups = {}
    for r in rows:
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            continue
        # 2026-10-13 audit D4: a total HLTV (authoritative) has confirmed is never held again by BO3.gg's full scoreboard
        if _is_quick(res) and not res.get("hltv_checked") and r["player_id"] and r["match_slug"]:
            groups.setdefault((r["player_id"], r["match_slug"]), []).append((r, res))
    out = {"checked": 0, "confirmed": 0, "disagree": [], "held": 0, "waiting": 0}
    for (pid, slug), picks in _rotate(list(groups.items()), lambda kv: min(x["id"] for x, _ in kv[1]), "quick", limit):
        try:                                            # one BO3.gg error never stops the other matches (audit B4)
            ms = b.player_matches(pid, pages=1, ttl=1).get("results") or []
            mt = next((m for m in ms if m.get("slug") == slug), None)
            if not mt or mt.get("status") != "finished":
                out["waiting"] += len(picks)
                continue
            maps = sorted(map_rows(b, pid, [mt], lambda m, g: (g.get("number") or 9) <= 2), key=lambda x: x.get("number") or 9)
        except Exception:
            out["waiting"] += len(picks)
            continue
        if [x.get("number") for x in maps] != [1, 2] or any(x.get("rounds") in (None, 0) for x in maps):
            out["waiting"] += len(picks)                # full scoreboard not published yet
            continue
        for r, old in picks:
            out["checked"] += 1
            stat = "headshots" if r["prop"] == "hs" else "kills"
            if any(x.get(stat) is None for x in maps) or r["actual"] is None:
                out["waiting"] += 1
                continue
            full = sum(x[stat] for x in maps)
            new = dict(old, rounds=[x["rounds"] for x in maps], won=[x.get("won") for x in maps], full_checked=True,
                       quick=True, full_per_map=[[x.get("kills"), x.get("headshots")] for x in maps], rule=SOURCE_RULE)
            if full == r["actual"]:
                out["confirmed"] += 1
                if apply:
                    _save_result(r["id"], new)
                continue
            held = _between(r["line"], r["actual"], full)
            new.update(provisional=True, discrepancy={"quick_total": r["actual"], "full_total": full}, review=held)
            d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "line": r["line"], "pick": r["pick"],
                 "quick_total": r["actual"], "full_total": full, "held_for_review": held, "match": slug}
            out["disagree"].append(d)
            out["held"] += int(held)
            if apply:
                _save_result(r["id"], new)
                with open(REVIEWS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, at=now.isoformat(timespec="seconds"), rule=SOURCE_RULE), default=str) + "\n")
    return out


FULL_REASONS = ("BO3.gg full scoreboard replaced the quick scoreboard", "settlement audit: BO3.gg full scoreboard differs")


def revert_full_scoreboard_corrections(now=None, apply=True):
    """Undo the 2026-10-12 corrections that replaced a BO3.gg quick-scoreboard settlement with the full scoreboard
    (SOURCE_RULE): restore the logged old status / total / hit, record both totals, hold for review when the line sits
    between them, and append the reversal to settlement_corrections.jsonl. Idempotent (marker reverted_full); never
    touches a pick HLTV has since settled (source hltv)."""
    now = now or datetime.now(timezone.utc)
    try:
        lines = [json.loads(x) for x in CORRECTIONS.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    first = {}
    for e in lines:
        if e.get("reason") in FULL_REASONS and e.get("id") is not None and e["id"] not in first:
            first[e["id"]] = e                          # the first full-scoreboard correction holds the quick values
    done = []
    for pid, e in first.items():
        with _LOCK:
            con = _db()
            r = con.execute("SELECT * FROM picks WHERE id = ?", (pid,)).fetchone()
            con.close()
        if r is None:
            continue
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            res = {}
        if res.get("reverted_full") or res.get("source") == "hltv" or res.get("hltv_checked"):
            continue                                    # HLTV settled / confirmed it: never changed back (2026-10-13 audit)
        old = e.get("old") or {}
        if old.get("actual") is None:
            continue
        full = r["actual"]
        held = full is not None and _between(r["line"], old["actual"], full)
        new = dict(res, source="bo3", quick=True, full_checked=True, reverted_full=True, provisional=True, rule=SOURCE_RULE,
                   discrepancy={"quick_total": old["actual"], "full_total": full}, review=held,
                   # 2026-10-14: the correction log never kept the quick scoreboard's per-map numbers - the restored total gets
                   # no per-map detail rather than the full scoreboard's (which gave a different total: settlement audit)
                   per_map=old.get("per_map"), full_per_map=res.get("per_map"))
        d = {"id": pid, "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": old["actual"], "status": old.get("status"), "hit": old.get("hit")}, "held_for_review": held,
             "reason": "reverted: the BO3.gg quick scoreboard matches HLTV's definition better than the full one (SOURCE_RULE)"}
        done.append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, result=? WHERE id=?",
                            (old.get("status") or r["status"], old["actual"], old.get("hit"), json.dumps(new, default=str), pid))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=now.isoformat(timespec="seconds")), default=str) + "\n")
    return done


def _scope_total_of(result, scope, prop):
    """The total the stored per-map detail gives (None without detail) - as learning.audit computes it."""
    from scope import scope_total
    pm, won = result.get("per_map"), result.get("won")
    if not pm:
        return None
    maps = [{"number": i + 1, "kills": km[0], "headshots": km[1], **({"won": won[i]} if won and i < len(won) else {})}
            for i, km in enumerate(pm) if isinstance(km, (list, tuple)) and len(km) >= 2]
    t, _ = scope_total(maps, scope or "12", "hs" if prop == "hs" else "kills")
    return t


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
    """HLTV's forfeited-map count for a match (None = unknown). HLTV unreachable raises (2026-10-13 audit: read as
    'unknown', a forfeited match settled on its 0-kill map instead of waiting)."""
    try:
        cur.execute("SELECT forfeited_maps FROM match_overview WHERE matchid = %s", (mid,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        import hltv
        if hltv.is_transient(e):
            raise
        return None


def _match_date(cur, mid):
    """HLTV's UTC date of a match (match_overview.date) or None. HLTV unreachable raises (as _forfeited)."""
    try:
        cur.execute("SELECT date FROM match_overview WHERE matchid = %s", (mid,))
        row = cur.fetchone()
        return str(row[0])[:10] if row and row[0] else None
    except Exception as e:
        import hltv
        if hltv.is_transient(e):
            raise
        return None


_MAP_STATE_SQL = """
SELECT mm.number, count(ps.playerid) FILTER (WHERE t.name = %(team)s OR %(team)s IS NULL)
FROM match_maps mm
LEFT JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
LEFT JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
LEFT JOIN teams t ON t.teamid = own.teamid
WHERE mm.matchid = %(mid)s
GROUP BY mm.number
"""


def _map_state(cur, mid, team=None):
    """(played map numbers, numbers whose stats are scraped for the player's team - 5+ of its players' rows, the same
    joins as hltv._series_rows -, series map count from the score or None). None when it cannot be read (unknown ->
    the platform rules leave the pick pending). HLTV unreachable raises (as _forfeited)."""
    try:
        cur.execute(_MAP_STATE_SQL, {"mid": mid, "team": team})
        rows = cur.fetchall()
        cur.execute("SELECT score1, score2 FROM scrape_queue WHERE matchid = %s", (mid,))
        sq = cur.fetchone()
    except Exception as e:
        import hltv
        if hltv.is_transient(e):
            raise
        return None
    try:
        played = {int(n) for n, _c in rows}
        scraped = {int(n) for n, c in rows if (c or 0) >= 5}
        total = (int(sq[0]) + int(sq[1])) if sq and sq[0] is not None and sq[1] is not None else None
    except (TypeError, ValueError, IndexError):
        return None
    return played, scraped, total


def _note_pending(r, res, base=None):
    """A settlement the platform's published rules do not decide: the pick stays open (never voided by the clock) and
    the reason is recorded on it once (result.rule_pending) for the admin output. base: the result to store it with
    (the match's result read this pass, 2026-10-23 reschedule rule) instead of the row's stored one."""
    note = {"rule_pending": {"reason": res.get("reason"), "rule": res.get("rule"), "platform": res.get("platform"),
                             "forfeited_maps": res.get("forfeited_maps")}}
    if base is not None:
        _save_result(r["id"], dict(base, **note))
        return
    try:
        cur = json.loads(_col(r, "result") or "{}")
    except (TypeError, ValueError):
        cur = {}
    if not isinstance(cur, dict) or cur.get("rule_pending") != note["rule_pending"]:
        _save_result(r["id"], dict(cur if isinstance(cur, dict) else {}, **note))


def _timed(r, upd, result, lo, hi, source):
    """The platform's reschedule rule on a settlement found by exact identity (2026-10-23 closeout 2). upd: the outcome
    tuple; result: the result dict to store (None = keep the row's). Stores the start evidence with it (result.schedule)
    and returns the tuple to store - the outcome, a void (played beyond the platform's window) - or None (pending: the
    evidence does not decide it; the pick stays open, never voided by the clock, the reason recorded)."""
    d, ev = settlement_timing(r, lo, hi, source)
    if result is None:
        try:
            result = json.loads(_col(r, "result") or "{}")
        except (TypeError, ValueError):
            result = {}
        result = result if isinstance(result, dict) else {}
        result.pop("rule_pending", None)
    result = dict(result, schedule=ev)
    if d["action"] == "pending":
        if (_col(r, "status") or "open") != "open":   # an admin recheck of a settled pick: evidence only, no pending note
            _save_result(r["id"], result)
            return None
        _note_pending(r, {"reason": d["reason"], "rule": "reschedule", "platform": platform_rules.platform_of(_col(r, "book"))},
                      base=result)
        return None
    _save_result(r["id"], result)
    if d["action"] == "void":
        return ("void", upd[1] if upd else None, None, None)
    return upd


def _team_confirmed(cur, ids, team):
    """The HLTV player's maps include the pick's team: an exact name variant, or the team's HLTV id via the alias table
    (hltv.resolve_team_id). No team on the pick = nothing to confirm against (unconfirmed)."""
    import hltv
    if not team:
        return False
    cur.execute("SELECT DISTINCT t.teamid, t.name FROM player_stats ps JOIN teams t ON t.teamid = ps.teamid "
                "WHERE ps.playerid = ANY(%s)", (list(ids),))
    got = cur.fetchall()
    variants = set(hltv.team_name_variants(team))
    if any(variants & set(hltv.team_name_variants(name)) for _tid, name in got):
        return True
    tid = hltv.resolve_team_id(cur, team)
    return tid is not None and any(t == tid for t, _n in got)


def _settle_teams(r):
    """The team names settlement may identify the player by, in order: picks.team (since 2026-10-13 the team he plays
    THIS match for - the projection's and the odds' team), then picks.profile_team (his BO3.gg profile team - what
    picks.team held before; NULL on older rows). The settlement keys themselves are unchanged: HLTV player id + the
    opponent's HLTV team id + the listed match (the hltv-<id>, or the candidate matchid verifies - 2026-10-13), or the
    BO3.gg player id + match slug."""
    out = []
    for t in (_col(r, "team"), _col(r, "profile_team")):
        if t and str(t).strip().lower() not in [x.strip().lower() for x in out]:
            out.append(str(t))
    return out or [None]


def _player_identity(cur, r):
    """(HLTV player ids, refused): the first of _settle_teams whose HLTV player is confirmed on that team. A stand-in's
    match team usually confirms (that match is in his HLTV rows once played); his profile team is the fallback, so a
    pick never settles less than before. refused = candidates found but none confirmed."""
    import hltv
    found = False
    for t in _settle_teams(r):
        ids = hltv._player_ids(cur, r["player"], t)
        if not ids:
            continue
        found = True
        if _team_confirmed(cur, ids, t):
            return ids, False
    return [], found


def _canonical_hltv(r, how=False):
    """2026-10-13 audit D1 (owner: settlement on the SAME canonical identity as grading): the pick's HLTV match =
    matchid.canonical_match(team, opponent, listed start) - the call the projection's odds link makes
    (thunderpick.projection_odds): canonical team ids + the maintained aliases, an HLTV schedule time within
    matchid.WINDOW_H of the start, or a finished match dated that UTC day. picks.team first, then the BO3.gg profile team
    (_settle_teams). Returns (HLTV match id, opponent's HLTV team id or None, None), (None, None, why) when the canonical
    match names no HLTV match (hashed / ambiguous / unresolved), or (None, None, None) when matchid has no HLTV team
    table loaded (tests; a first boot without the database) - the previous rule (date window) applies."""
    import matchid
    try:
        if not ((matchid.status().get("teams") or {}).get("rows")):
            return (None, None, None) + ((None,) if how else ())
    except Exception:
        return (None, None, "match identity unavailable") + ((None,) if how else ())
    why = []
    for t in _settle_teams(r):
        cm = matchid.canonical_match(t, _col(r, "opponent"), _col(r, "match_start"))
        if cm.get("hltv_match") is not None:
            # the opponent's key as the match was linked: a dated rename (matchid match-rules-3, 'LP' listed for HLTV
            # BARCA on 2026-10-21) links the new team id, which the opponent's rows carry
            side = cm.get("team_keys_by_side") or []
            k = str((side[1] if len(side) == 2 else None) or matchid.team_key(_col(r, "opponent")) or "")
            return (int(cm["hltv_match"]), (int(k[5:]) if k.startswith("team:") else None), None) + (("canonical match",) if how else ())
        why.append(f"{t} vs {_col(r, 'opponent')}: {cm.get('reason') or cm.get('status')}")
        if cm.get("status") == "ambiguous":
            break
    else:
        rel = _relisted_hltv(r, matchid)
        if rel[0] is not None:
            return rel if how else rel[:3]
        if rel[2]:
            why.append(rel[2])
    out = (None, None, "; ".join(why) or "no canonical match")
    return out + (None,) if how else out


def _relisted_hltv(r, matchid):
    """2026-10-23 closeout 2: the canonical match at a start THIS pick's own line / match was re-listed for (schedule_obs:
    the platform's listing of its market id, or its own match's start on a later grade) - the same canonical_match rule
    (exact team ids + aliases, WINDOW_H around that observed start), never a wider window around the logged start. Linked
    only when every observed start names the same single HLTV match. (match id, opponent id, why, how)."""
    found, why = {}, []
    for st, o in relisted_starts(r):
        for t in _settle_teams(r):
            cm = matchid.canonical_match(t, _col(r, "opponent"), st.isoformat(), mint=False)
            if cm.get("hltv_match") is not None:
                side = cm.get("team_keys_by_side") or []
                k = str((side[1] if len(side) == 2 else None) or matchid.team_key(_col(r, "opponent")) or "")
                found[int(cm["hltv_match"])] = (int(k[5:]) if k.startswith("team:") else None, o)
                break
            if cm.get("status") == "ambiguous":
                why.append(f"re-listed start {_z(st)}: {cm.get('reason')}")
                break
    if len(found) == 1 and not why:
        mid, (oid, o) = next(iter(found.items()))
        return mid, oid, None, f"canonical match at the re-listed start ({o.get('kind')} {o.get('source')} {o.get('start')})"
    if len(found) > 1:
        why.append(f"re-listed starts name {len(found)} HLTV matches ({', '.join(str(m) for m in sorted(found))}) - not linked")
    return None, None, "; ".join(why) or None, None


def matchid_renamed_id(name, start):
    """matchid.renamed_team_id (dated team renames); None when matchid is unavailable. Memory only."""
    try:
        import matchid
        return matchid.renamed_team_id(name, start)
    except Exception:
        return None


def _rename_roster_ok(cur, r, mid):
    """Settlement gate for a dated team rename (matchid match-rules-4): when a name on the pick (its team or opponent)
    resolves to a renamed team's OLD id at this start and HLTV match `mid` has the NEW team but not the old one (the link
    went through the rename), the new team's lineup in `mid` must hold >= matchid.RENAME_MIN_PLAYERS of the rename
    entry's HLTV player ids. True when no rename is involved. Raises on a database error (the caller's transient rule)."""
    import matchid
    pairs = set()
    for name in _settle_teams(r) + [_col(r, "opponent")]:
        to = matchid_renamed_id(name, r["match_start"]) if name else None
        k = str(matchid.team_key(name) or "") if to is not None else ""
        if to is not None and k.startswith("team:"):
            pairs.add((int(k[5:]), int(to)))
    if not pairs:
        return True
    cur.execute("SELECT DISTINCT teamid FROM matches WHERE matchid = %s", (int(mid),))
    teams = {x[0] for x in cur.fetchall()}
    for old, new in pairs:
        if old in teams or new not in teams:
            continue                                  # not linked through the rename
        cur.execute("SELECT DISTINCT playerid FROM player_stats WHERE matchid = %s AND teamid = %s", (int(mid), new))
        if len({x[0] for x in cur.fetchall()} & matchid.rename_players(old, new)) < matchid.RENAME_MIN_PLAYERS:
            return False
    return True


def scope_result(r, same_day_only=False):
    """The pick's series from HLTV and its total for the line's scope, via the shared scope rules (scope.py).
    Returns {"status": "ok", "total", "maps_used", "maps", "per_map", "rounds", "won", "hltv_match"} or
    {"status": "wait"|"none"|"error", "reason"}. Never substitutes another scope; two candidate matches = error.
    2026-10-13 audit D1: only the pick's own match, read by its HLTV match id - an hltv-<id> pick's own id (every scope),
    a BO3.gg-slug pick's canonical match (_canonical_hltv, the identity grading uses). Never a date window: an earlier
    meeting of the same teams (yesterday's upper-bracket final, this morning's opener) used to settle the pick."""
    import hltv
    from scope import scope_total, MAP3_NOT_PLAYED
    day = (r["match_start"] or "")[:10]
    slug = str(_col(r, "match_slug") or "")
    own = int(slug[5:]) if slug.startswith("hltv-") and slug[5:].isdigit() else None
    link, oid, why, how = (own, None, None, None) if own is not None else _canonical_hltv(r, how=True)
    if link is None and why:
        return {"status": "none", "reason": f"no canonical HLTV match for this pick ({why}) - not settled from HLTV"}
    best_of = None
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            ids, refused = _player_identity(cur, r)
            if refused:
                # 2026-10-12 audit: a nick-only match (foggers of Rooster -> an HLTV 'foggers' who never played for
                # Rooster) is not this player - never settle on it (Maps 1-2 falls back to BO3.gg's player id)
                return {"status": "none", "reason": "player/team identity not confirmed in HLTV"}
            if link is not None:
                rows = hltv._series_rows(cur, ids, "2000-01-01", "AND mo.matchid = %(mid)s", {"mid": link}, maxn=3, need=(1,)) if ids else []
            else:                                            # matchid not loaded: the previous rule
                rows = hltv._series_rows(cur, ids, "2000-01-01", "AND mo.date BETWEEN %(d)s::date - 1 AND %(d)s::date + 1",
                                         {"d": day}, maxn=3, need=(1,)) if ids else []
            if link is not None and not _rename_roster_ok(cur, r, link):
                # closeout 2026-10-23: unconfirmed = pending (never voided by the clock, no BO3.gg fallback), not a guess
                return {"status": "pending", "reason": f"HLTV match {link}: dated team rename not confirmed by its lineup - not settled",
                        "rule": "rename-lineup", "platform": platform_rules.platform_of(_col(r, "book"))}
            opp = (r["opponent"] or "").strip()
            # an hltv-<id> pick whose opponent is listed under a renamed team's old name (dated rename, matchid
            # match-rules-3): its own match's rows against the NEW team id are this opponent; else the usual rule
            rid = matchid_renamed_id(opp, r["match_start"]) if own is not None and oid is None else None
            if rid is not None and any(x.get("opponent_id") == rid for x in rows):
                on = [x for x in rows if x.get("opponent_id") == rid]
            elif oid is not None:                            # the canonical opponent (matchid team id + aliases)
                on = [x for x in rows if x.get("opponent_id") in (None, oid)]
            else:
                on, how = hltv.opponent_rows(cur, rows, opp)     # opponent's HLTV team id first, exact names as fallback
            if same_day_only and link is None:
                on = [x for x in on if x["date"] >= day]     # an earlier meeting is not this match (previous rule)
            mids = {x["hltv_match"] for x in on}
            forfeits = _forfeited(cur, next(iter(mids))) if len(mids) == 1 else None
            plat = platform_rules.platform_of(_col(r, "book"))
            # the match's map rows (played / scraped numbers, series score): which map a forfeit was, and whether he
            # sat out a map of the line - only needed when a platform rule depends on it (2026-10-23 platform rules)
            mstate = (_map_state(cur, next(iter(mids)), on[0].get("team"))
                      if len(mids) == 1 and (forfeits or plat) else None)
            if len(mids) == 1 and (_col(r, "scope") or "12") == "123" and not any(x["number"] == 3 for x in on):
                # Two matching map results do not prove a sweep: a BO5 can be 2-0 and still be playing.
                # Use the same stored series format as the match model; unknown format means wait.
                cur.execute("SELECT format FROM scrape_queue WHERE matchid = %s", (next(iter(mids)),))
                fmt = cur.fetchone()
                import re
                match = re.fullmatch(r"(?:bo|best\s*of\s*)([135])", str(fmt[0] if fmt else "").strip(), re.I)
                best_of = int(match.group(1)) if match else None
    finally:
        con.close()
    if not on and rows and link is not None:
        return {"status": "none", "reason": f"HLTV match {link}: the player's rows are not against {opp!r} - not settled"}
    if not on:
        return {"status": "none", "reason": "match not in HLTV yet"}
    if len(mids) > 1:
        return {"status": "error", "reason": f"{len(mids)} HLTV matches vs this opponent around that day - ambiguous"}
    sc = _col(r, "scope") or "12"
    if forfeits:
        # 2026-10-23 platform rules (platform_rules.py): Underdog voids only a forfeit inside the line's maps (a map-3
        # forfeit after Maps 1-2 were played grades); PrizePicks publishes no CS2 forfeit rule -> pending, never guessed;
        # a line with no platform keeps the standing rule (any forfeited map voids, 2026-10-08)
        played, scraped, n_series = mstate or (None, None, None)
        fset = platform_rules.forfeited_numbers(forfeits, played, n_series) if played is not None else None
        d = platform_rules.forfeit_decision(plat, sc, fset, any_played=bool(played) if played is not None else True)
        if d["action"] == "void":
            return {"status": "void", "reason": f"{forfeits} forfeited map(s)" if plat is None else d["reason"],
                    "rule": d["rule"], "forfeited_maps": sorted(fset) if fset else None}
        if d["action"] == "pending":
            return {"status": "pending", "reason": d["reason"], "rule": d["rule"], "platform": plat,
                    "forfeited_maps": sorted(fset) if fset else None}
    if plat and mstate:
        d = platform_rules.dnp_decision(plat, sc, {x["number"] for x in on}, mstate[1])
        if d and d["action"] == "void":
            return {"status": "void", "reason": d["reason"], "rule": d["rule"]}
        if d and d["action"] == "pending":
            return {"status": "pending", "reason": d["reason"], "rule": d["rule"], "platform": plat}
    ms = sorted(on, key=lambda x: x["number"])
    sc = _col(r, "scope") or "12"
    if any(x.get("partial") and x["number"] <= {"1": 1, "12": 2}.get(sc, 3) for x in ms):
        # 2026-10-25 audit: a line map captured before it finished (stored rounds < final score) - wait, never settle on it
        return {"status": "wait", "reason": "HLTV map scoreboard incomplete (captured before the map finished)"}
    maps = [{"number": x["number"], "kills": x["kills"], "headshots": x["headshots"], "won": x["won"]} for x in ms]
    total, used = scope_total(maps, sc, r["prop"], best_of=best_of)
    if total is None and used == MAP3_NOT_PLAYED:
        return {"status": "void", "reason": used}      # Underdog voids Maps 1-3 when map 3 isn't played
    if total is None:
        if sc == "123" and not any(x["number"] == 3 for x in ms):
            return {"status": "wait", "reason": "map 3 not scraped yet; no confirmed BO3 sweep"}
        if sc == "123" and len(ms) == 2 and ms[0]["won"] != ms[1]["won"]:
            return {"status": "wait", "reason": "1-1 after two maps: map 3 not in yet"}
        if any(x[("headshots" if r["prop"] == "hs" else "kills")] is None for x in ms):
            return {"status": "wait", "reason": "stat not scraped yet"}
        return {"status": "error", "reason": used}
    return {"status": "ok", "total": total, "maps_used": used, "maps": [x["map"] for x in ms if x["number"] in used],
            "per_map": [[x["kills"], x["headshots"]] for x in ms], "rounds": [x["rounds"] for x in ms],
            "won": [x["won"] for x in ms], "hltv_match": ms[0]["hltv_match"],
            # which identity the match was read under (stored with the settlement - evaluation keys on verified ones)
            "identity": "hltv id" if own is not None else (how or "canonical match") if link is not None else "date window (matchid not loaded)",
            "date": ms[0].get("date")}             # HLTV's UTC match date (start evidence for the reschedule rule)


def _ident(res):
    """The settlement's identity label, for the stored result (absent when the lookup did not say)."""
    return {"identity": res["identity"]} if res.get("identity") else {}


def _settle_scope(r, now, start):
    """A Maps 1-3 or Map 1 line, from HLTV via scope_result(). None = not yet; void after VOID_AFTER_D days."""
    try:                                              # the match's own day first; the +-1 day window (time zones) only once the
        res = scope_result(r, same_day_only=now < start + timedelta(hours=36))   # right match had time to be scraped
    except Exception as e:
        import hltv
        if hltv.is_transient(e):
            return None                               # HLTV unreachable: never a void on that (2026-10-11 audit)
        res = {"status": "none"}
    if res["status"] == "void":
        return ("void", None, None, None)
    if res["status"] == "pending":                    # platform rule not published: open, never voided by the clock
        _note_pending(r, res)
        return None
    if res["status"] != "ok":
        if now > start + timedelta(days=VOID_AFTER_D):
            hold = _reschedule_hold(r) if not str(_col(r, "match_slug") or "").startswith("hltv-") else None
            if hold:                                  # 2026-10-23: re-listed inside the platform's window, identity not
                _note_pending(r, hold)                # found at the new start - pending with the reason, not a void
                return None
            return ("void", None, None, None)
        return None
    lo, hi, src = _hltv_start_evidence(res["hltv_match"], res.get("date"))
    return _timed(r, _outcome(r, res["total"], res["maps"]),
                  {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                   "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], **_ident(res)}, lo, hi, src)


CORRECTIONS = DB.parent / "settlement_corrections.jsonl"


def fix_scope_settlements(apply=False, scopes=("1", "123"), test=None, until=None, ids=None, bo3=None, map_rows=None):
    """2026-10-08 incident: recheck_with_hltv() re-settled Map 1 / Maps 1-3 picks with Maps 1-2 totals. Re-derive every
    settled non-Maps-1-2 pick from HLTV through scope_result(); with apply=True correct the mismatches and append the
    old and new values to settlement_corrections.jsonl (nothing is lost). Returns what differs / what was skipped.
    2026-10-12 owner review: a FROZEN cohort (until = settled_at cutoff and/or ids) so totals don't move between runs;
    picks HLTV can't verify (player/team not in HLTV) are checked against BO3.gg's full scoreboard (bo3 + map_rows:
    the original settlement source, by player id + match slug + map numbers); the summary says
    "N wrong among V verified; U unresolved"."""
    from scope import outcome
    want = {int(i) for i in ids} if ids else None
    with _LOCK:
        con = _db()
        rows = [x for x in con.execute("SELECT * FROM picks WHERE status IN ('settled', 'push')").fetchall()
                if (x["scope"] or "12") in scopes and (test is None or (x["test"] or 0) == test)
                and (until is None or (x["settled_at"] or "") <= until) and (want is None or x["id"] in want)]
        con.close()
    diffs, skipped, details = [], [], []
    import time as _time
    import hltv as _hltv
    for r in rows:
        res, err = None, None
        for attempt in range(4):              # an audit waits out a database blip instead of skipping the rest (2026-10-12)
            try:
                res = scope_result(r)
                break
            except Exception as e:
                err = e
                if not _hltv.is_transient(e):
                    break
                _time.sleep(2 * (attempt + 1))
                _hltv.allow_probe()
        if res is None:
            skipped.append({"id": r["id"], "reason": f"error {err}"}); continue
        if res["status"] == "void":
            # 2026-10-11: a Maps 1-3 pick whose BO3 ended 2-0 (no map 3) is a void, not a 2-map total
            d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
                 "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
                 "new": {"actual": None, "status": "void", "hit": None}, "reason": res.get("reason")}
            diffs.append(d)
            if apply:
                with _LOCK:
                    con = _db()
                    # 2026-10-21 audit 10: store exactly what the correction log records (actual cleared too) -
                    # a stale actual made learning.pending_corrections report it unapplied and paused learning
                    con.execute("UPDATE picks SET status='void', actual=NULL, hit=NULL, "
                                "settled_at=COALESCE(settled_at, ?) WHERE id=?",
                                (datetime.now(timezone.utc).isoformat(timespec="seconds"), r["id"]))
                    con.commit()
                    con.close()
                    with open(CORRECTIONS, "a", encoding="utf-8") as f:
                        f.write(json.dumps(dict(d, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds")), default=str) + "\n")
            continue
        if res["status"] != "ok":
            skipped.append({"id": r["id"], "reason": res.get("reason")}); continue
        st, hit = outcome(res["total"], r["line"], r["pick"])
        new_detail = {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                      "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], **_ident(res)}
        if res["total"] == r["actual"] and st == r["status"] and (hit == r["hit"] or (hit is None and r["hit"] is None)):
            # 2026-10-09: the result is right but the stored detail may be stale (the 2026-10-08 correction updated the
            # total only): if the stored maps don't reproduce the settled total under this scope's rules, refresh it
            try:
                old = json.loads(r["result"] or "{}")
            except ValueError:
                old = {}
            won = old.get("won") or []
            om = [{"number": i + 1, "kills": k, "headshots": h, **({"won": won[i]} if i < len(won) else {})}
                  for i, (k, h) in enumerate(old.get("per_map") or [])]
            from scope import scope_total
            t_old, _ = scope_total(om, r["scope"] or "12", "hs" if r["prop"] == "hs" else "kills") if om else (None, None)
            if t_old != r["actual"]:
                dd = {"id": r["id"], "player": r["player"], "scope": r["scope"], "actual": r["actual"], "old_detail": old, "new_detail": new_detail}
                details.append(dd)
                if apply:
                    with _LOCK:
                        con = _db()
                        con.execute("UPDATE picks SET result=? WHERE id=?", (json.dumps(new_detail, default=str), r["id"]))
                        con.commit()
                        con.close()
                        with open(CORRECTIONS, "a", encoding="utf-8") as f:
                            f.write(json.dumps(dict(dd, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                    reason="stale settlement detail (total already correct)"), default=str) + "\n")
            continue
        d = {"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": r["scope"], "line": r["line"], "pick": r["pick"],
             "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
             "new": {"actual": res["total"], "status": st, "hit": hit}, "maps_used": res["maps_used"],
             "hltv_match": res["hltv_match"]}
        diffs.append(d)
        if apply:
            with _LOCK:
                con = _db()
                con.execute("UPDATE picks SET status=?, actual=?, hit=?, actual_maps=?, result=? WHERE id=?",
                            (st, res["total"], hit, json.dumps(res["maps"]), json.dumps(new_detail, default=str), r["id"]))
                con.commit()
                con.close()
                with open(CORRECTIONS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(dict(d, corrected_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                            reason="recheck_with_hltv used Maps 1-2 totals for a non-Maps-1-2 line")) + "\n")
    by_bo3, unresolved, disagree = 0, [], []
    byid = {r["id"]: r for r in rows}
    for sk in skipped:
        r = byid[sk["id"]]
        if bo3 is None or (r["scope"] or "12") != "12" or not r["player_id"] or not (r["match_slug"] or "") \
                or r["match_slug"].startswith("hltv-"):
            unresolved.append(sk); continue
        try:
            got = _bo3_totals(bo3, map_rows, r)
        except Exception as e:
            got = f"BO3.gg error {type(e).__name__}"
        if isinstance(got, str):
            unresolved.append(dict(sk, bo3=got)); continue
        by_bo3 += 1
        q, f = got["quick"], got["full"]
        if q is not None and f is not None and q != f:
            disagree.append({"id": r["id"], "player": r["player"], "line": r["line"], "stored": r["actual"], "quick": q, "full": f,
                             "line_between": _between(r["line"], q, f)})
        ref = q if q is not None else f                 # SOURCE_RULE: quick first
        upd = _outcome(r, ref, got["maps"])
        if ref == r["actual"] and upd[0] == r["status"] and upd[3] == r["hit"]:
            continue
        diffs.append({"id": r["id"], "player": r["player"], "prop": r["prop"], "scope": "12", "line": r["line"], "pick": r["pick"],
                      "test": r["test"], "old": {"actual": r["actual"], "status": r["status"], "hit": r["hit"]},
                      "new": {"actual": ref, "status": upd[0], "hit": upd[3]},
                      "source": "bo3 quick scoreboard" if q is not None else "bo3 full scoreboard (no quick)"})
    verified = len(rows) - len(skipped) + by_bo3
    summary = {"cohort": len(rows), "settled_until": until or max((r["settled_at"] or "" for r in rows), default=None),
               "verified": verified, "verified_hltv": len(rows) - len(skipped), "verified_bo3": by_bo3,
               "wrong": len(diffs), "unresolved": len(unresolved),
               "line": f"{len(diffs)} wrong among {verified} verified; {len(unresolved)} unresolved"}
    summary["bo3_quick_full_disagree"] = len(disagree)
    summary["rule"] = SOURCE_RULE
    return {"summary": summary, "checked": len(rows), "differs": diffs, "stale_details": details, "skipped": unresolved,
            "bo3_disagreements": disagree, "applied": bool(apply),
            "note": "applied covers the HLTV-verified changes only; BO3.gg-based differences are reported, never applied "
                    "by the audit (provisional source)"}


def _bo3_totals(b, map_rows, r):
    """{"quick", "full", "maps"} totals of a Maps 1-2 pick from BO3.gg (the pick's BO3.gg player id, its exact match slug,
    maps 1 and 2 by number, the pick's team on the scoreboard) - or a reason string. Either total may be None."""
    import hltv
    ms = b.player_matches(r["player_id"], pages=3, ttl=1).get("results") or []
    mt = next((m for m in ms if m.get("slug") == r["match_slug"]), None)
    if not mt:
        return "BO3.gg match not in the player's list"
    stat = "headshots" if r["prop"] == "hs" else "kills"
    maps = sorted(map_rows(b, r["player_id"], [mt], lambda m, g: (g.get("number") or 9) <= 2), key=lambda x: x.get("number") or 9)
    full = None
    if [x.get("number") for x in maps] == [1, 2] and all(x.get("rounds") for x in maps):
        if r["team"] and not (hltv.same_team(r["team"], maps[0].get("team") or "")
                              or (maps[0].get("team_slug") or "#") in (r["match_slug"] or "")):
            return f"BO3.gg team {maps[0].get('team')!r} is not the pick's team"
        if all(x.get(stat) is not None for x in maps):
            full = sum(x[stat] for x in maps)
    qr = _quick_rows(b, r["player_id"], mt)
    quick = sum(x[stat] for x in qr) if len(qr) == 2 and all(x[stat] is not None for x in qr) else None   # D13: missing != 0
    if quick is None and full is None:
        return "BO3.gg scoreboards for maps 1-2 not available"
    names = [x.get("map") for x in maps] if len(maps) == 2 else [x.get("map") for x in qr]
    return {"quick": quick, "full": full, "maps": names}


def _hltv_totals(r, same_day_only=False):
    """HLTV's result for a Maps 1-2 pick through the shared scope rules (scope_result): an update tuple, "wait" (match
    there, stat not yet), "transient" (HLTV unreachable) or None. Since 2026-10-08 it refuses ambiguous matches (two
    candidates used to take the first) and voids forfeits. 2026-10-13 audit D5: an outage was read as 'not in HLTV', so
    the BO3.gg fallback could settle or void the pick while HLTV was down."""
    try:
        res = scope_result(r, same_day_only=same_day_only)
    except Exception as e:
        import hltv
        return "transient" if hltv.is_transient(e) else None
    if res["status"] == "void":
        return ("void", None, None, None)
    if res["status"] == "pending":                    # 2026-10-23: platform rule not published - no BO3.gg fallback either
        _note_pending(r, res)
        return "pending"
    if res["status"] == "wait":
        return "wait"
    if res["status"] != "ok":
        return None
    lo, hi, src = _hltv_start_evidence(res["hltv_match"], res.get("date"))
    upd = _timed(r, _outcome(r, res["total"], res["maps"]),
                 {"source": "hltv", "rounds": res["rounds"], "won": res["won"], "per_map": res["per_map"],
                  "maps_used": res["maps_used"], "hltv_match": res["hltv_match"], **_ident(res)}, lo, hi, src)
    return "pending" if upd is None else upd          # 2026-10-23: start evidence does not decide the platform's window


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
            # never silently void a settled pick here - listed for review by hand instead (2026-10-11 audit: a BO3.gg
            # settlement on a forfeited map used to stay settled with no sign of it)
            changed.append({"id": r["id"], "player": r["player"], "prop": r["prop"], "line": r["line"], "pick": r["pick"],
                            "bo3": r["actual"], "hltv": None, "was_hit": r["hit"], "now_hit": None,
                            "review": "HLTV voids this match (forfeit / map not played) - settled from BO3.gg; review by hand"})
            continue
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
    import hltv
    tot, transient, mdate = {"found": False}, False, None
    for team in _settle_teams(r):                     # the match's team first, then the BO3.gg profile team (2026-10-13)
        try:
            tot = hltv.settle_totals(r["player"], team, int(r["match_slug"].split("-", 1)[1]))
        except Exception as e:
            tot = {"found": False, "transient": hltv.is_transient(e)}
        transient = transient or bool(tot.get("transient"))
        if tot.get("found"):
            break
    if not tot.get("found") and transient:
        tot = dict(tot, transient=True)
    actual = tot.get("headshots" if r["prop"] == "hs" else "kills") if tot.get("found") else None
    if actual is None and tot.get("transient"):
        return None                                   # HLTV unreachable: never a void on that (2026-10-11 audit)
    try:                                              # a forfeited map voids the pick (2026-10-08) - per platform since
        con = hltv._connect()                         # 2026-10-23 (platform_rules.forfeit_decision, as scope_result)
        try:
            with con.cursor() as cur:
                mid = int(r["match_slug"].split("-", 1)[1])
                if actual is not None:                # the match's UTC date (start evidence, 2026-10-23 reschedule rule)
                    mdate = _match_date(cur, mid)
                ff = _forfeited(cur, mid)
                if ff:
                    plat = platform_rules.platform_of(_col(r, "book"))
                    ms = _map_state(cur, mid) if plat else None
                    fset = platform_rules.forfeited_numbers(ff, ms[0], ms[2]) if ms else None
                    d = platform_rules.forfeit_decision(plat, "12", fset, any_played=bool(ms[0]) if ms else True)
                    if d["action"] == "void":
                        return ("void", None, None, None)
                    if d["action"] == "pending":
                        _note_pending(r, {"reason": d["reason"], "rule": d["rule"], "platform": plat,
                                          "forfeited_maps": sorted(fset) if fset else None})
                        return None
        finally:
            con.close()
    except Exception as e:
        if hltv.is_transient(e):
            return None                               # forfeit unknown while HLTV is unreachable: next pass (2026-10-13 audit)
    if actual is None:
        return ("void", None, None, None) if now > start + timedelta(days=VOID_AFTER_D) else None
    maps = json.dumps(tot.get("maps"))
    if actual == r["line"]:
        upd = ("push", maps, actual, None)
    else:
        hit = actual > r["line"] if r["pick"] == "More" else actual < r["line"]
        upd = ("settled", maps, actual, int(hit))
    mid = int(r["match_slug"].split("-", 1)[1])
    lo, hi, src = _hltv_start_evidence(mid, mdate)
    return _timed(r, upd, None, lo, hi, src)          # 2026-10-23: the platform's reschedule window (its own HLTV id)


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
    con = _read_db()                       # lock-free WAL read (audit 41): one statement = one consistent snapshot
    try:
        return [dict(r) for r in con.execute("SELECT * FROM picks ORDER BY id")]
    finally:
        con.close()


PICKS_PAGE_MAX = 1000                   # picks rows per page (~4-5 KB of factors each -> a page stays ~5 MB)


def picks_page(after_id=0, cutoff_id=None, limit=None):
    """Bounded keyset export of the pick log for /admin/picks.json (2026-10-26 audit: the endpoint returned the whole
    table, factors included, in one response - the same failure as the unbounded forecasts export that restarted
    production on 2026-10-14). Same contract as forecasts_page: rows with after_id < id <= cutoff_id ascending, at most
    `limit` (capped by PICKS_PAGE_MAX); the cutoff is fixed by the first call and passed back unchanged. Read-only."""
    limit = max(1, min(int(limit or PICKS_PAGE_MAX), PICKS_PAGE_MAX))
    after_id = max(0, int(after_id or 0))
    con = _read_db()
    try:
        if cutoff_id is None:
            cutoff_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM picks").fetchone()[0]
        cutoff_id = int(cutoff_id)
        rows = [dict(r) for r in con.execute("SELECT * FROM picks WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
                                             (after_id, cutoff_id, limit))]
        total = con.execute("SELECT COUNT(*) FROM picks WHERE id <= ?", (cutoff_id,)).fetchone()[0]
    finally:
        con.close()
    nxt = rows[-1]["id"] if rows else after_id
    return {"picks": rows, "cutoff_id": cutoff_id, "next_after": nxt, "done": len(rows) < limit or nxt >= cutoff_id,
            "total_in_cutoff": total, "page_max": PICKS_PAGE_MAX}


SETTLEMENT_COLS = "player, prop, scope, match_slug, status, actual, result"   # what slip_batches.leg_result_from_picks reads


def settlement_rows():
    """Every pick-log row, settlement fields only (no factors) - for slip_batches.leg_result_from_picks over all batches.
    Same result as passing export(), at a fraction of the memory (audit 41: export() carries ~4-5 KB of factors per row)."""
    con = _read_db()
    try:
        return [dict(r) for r in con.execute(f"SELECT {SETTLEMENT_COLS} FROM picks ORDER BY id")]
    finally:
        con.close()


def rows_for_matches(slugs, cap=200):
    """The pick-log rows of these exact match slugs (settlement fields only) - for settling a member's slip legs
    (slip_batches.leg_result_from_picks) without exporting the whole log. Indexed (picks_match), lock-free;
    cap=None reads every slug (in chunks of 200 bound parameters)."""
    slugs = sorted({s for s in slugs if s})
    if cap is not None:
        slugs = slugs[:cap]
    if not slugs:
        return []
    con = _read_db()
    try:
        out = []
        for i in range(0, len(slugs), 200):
            part = slugs[i:i + 200]
            out += [dict(r) for r in con.execute(
                f"SELECT {SETTLEMENT_COLS} FROM picks WHERE match_slug IN ({','.join('?' * len(part))})", part)]
        return out
    finally:
        con.close()

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


RULE_PENDING_MAX = 500


def rule_pending_page(after_id=0, limit=None):
    """2026-10-23 closeout: the OPEN picks held pending by an unpublished platform rule (result.rule_pending), each with
    its reason and the evidence still missing (platform_rules.missing_evidence). Bounded keyset page (<= RULE_PENDING_MAX
    rows, settlement fields only), lock-free WAL read. Read-only: nothing is settled or changed here."""
    limit = max(1, min(int(limit or RULE_PENDING_MAX), RULE_PENDING_MAX))
    after_id = max(0, int(after_id or 0))
    con = _read_db()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, logged_at, player, team, opponent, match_slug, match_start, prop, scope, line, pick, book, test, "
            "status, result FROM picks WHERE status = 'open' AND result LIKE '%\"rule_pending\"%' AND id > ? "
            "ORDER BY id LIMIT ?", (after_id, limit))]
    finally:
        con.close()
    out = []
    for r in rows:
        try:
            rp = (json.loads(r.pop("result") or "{}") or {}).get("rule_pending")
        except (TypeError, ValueError, AttributeError):
            rp = None
        if not isinstance(rp, dict):
            continue
        plat = rp.get("platform") or platform_rules.platform_of(r.get("book"))
        rr = platform_rules.rule(plat, rp.get("rule")) if rp.get("rule") else {"verified": False}
        r.update(rule_pending=rp, rule_verified=bool(rr.get("verified")),
                 missing_evidence=platform_rules.missing_evidence(plat, rp.get("rule")) if rp.get("rule") else None)
        if r["rule_verified"]:                      # the rule is published; the DATA did not decide it (e.g. map numbers)
            r["missing_evidence"] = {"missing": "data, not a rule: " + str(rp.get("reason") or "")}
        out.append(r)
    nxt = rows[-1]["id"] if rows else after_id
    return {"rows": out, "next_after": nxt, "done": len(rows) < limit, "limit": limit,
            "searched_at": platform_rules.SEARCHED_AT}


PAGE_MAX = {False: 2000, True: 500}     # rows per page without / with decoded grader inputs (~4 KB / ~20 KB a row)


def forecasts_page(after_id=0, cutoff_id=None, limit=None, inputs=False, compact=False):
    """Bounded export of the forecast history (2026-10-14: the full export of ~25k rows, ~95 MB, restarted production).
    Keyset pagination on the primary key: rows with after_id < id <= cutoff_id, ascending, at most `limit` (capped by
    PAGE_MAX). The cutoff is fixed by the first call (the current max id) and passed back unchanged, so rows appended while
    a client pages are never mixed in, and no row is skipped or repeated (forecast ids are immutable and only ever grow).
    Only one page is ever in memory. Returns {rows, cutoff_id, next_after, done, total_in_cutoff}."""
    import zlib
    cap = PAGE_MAX[bool(inputs)]
    limit = max(1, min(int(limit or cap), cap))
    after_id = max(0, int(after_id or 0))
    cols = "*" if inputs else ", ".join(c for c in _forecast_cols() if c != "inputs") + ", inputs IS NOT NULL AS has_inputs"
    if compact:
        if inputs: raise ValueError('compact export does not include model inputs')
        cols = 'id,pick_id,kind,logged_at,match_start,line,pick,p_pick,p_more,grade,units,model_version,probs_raw'
    with _LOCK:
        con = _db()
        try:
            if cutoff_id is None:
                cutoff_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM forecasts").fetchone()[0]
            cutoff_id = int(cutoff_id)
            rows = [dict(r) for r in con.execute(f"SELECT {cols} FROM forecasts WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
                                                 (after_id, cutoff_id, limit))]
            total = con.execute("SELECT COUNT(*) FROM forecasts WHERE id <= ?", (cutoff_id,)).fetchone()[0]
        finally:
            con.close()
    for r in rows:
        if inputs:
            blob = r.pop("inputs", None)
            try:
                r["inputs"] = json.loads(zlib.decompress(blob)) if blob else None
            except Exception:
                r["inputs"] = None
        else:
            r["has_inputs"] = bool(r.get("has_inputs"))
    nxt = rows[-1]["id"] if rows else after_id
    return {"rows": rows, "cutoff_id": cutoff_id, "next_after": nxt, "done": len(rows) < limit or nxt >= cutoff_id,
            "total_in_cutoff": total, "page_max": cap}


SHADOW_PAGE_MAX = {False: 1000, True: 100}      # rows per page without / with each record's saved inputs


def shadow_page(after_id=0, cutoff_id=None, limit=None, inputs=False):
    """Bounded keyset export of the fh-1 shadow records (same contract as forecasts_page); inputs=True adds each record's
    saved snapshot and grader inputs."""
    import zlib
    cap = SHADOW_PAGE_MAX[bool(inputs)]
    limit = max(1, min(int(limit or cap), cap))
    after_id = max(0, int(after_id or 0))
    with _LOCK:
        con = _db()
        try:
            if cutoff_id is None:
                cutoff_id = con.execute("SELECT COALESCE(MAX(id), 0) FROM shadow_forecasts").fetchone()[0]
            cutoff_id = int(cutoff_id)
            rows = [dict(r) for r in con.execute("SELECT * FROM shadow_forecasts WHERE id > ? AND id <= ? ORDER BY id LIMIT ?",
                                                 (after_id, cutoff_id, limit))]
            total = con.execute("SELECT COUNT(*) FROM shadow_forecasts WHERE id <= ?", (cutoff_id,)).fetchone()[0]
            if inputs:
                for r in rows:
                    for k in ("inputs_sha", "gi_sha"):
                        b = con.execute("SELECT body FROM shadow_inputs WHERE sha = ?", (r[k],)).fetchone()
                        r[k.replace("_sha", "_body")] = json.loads(zlib.decompress(b["body"])) if b else None
        finally:
            con.close()
    for r in rows:
        r["rec"] = json.loads(r["rec"]) if r.get("rec") else None
    nxt = rows[-1]["id"] if rows else after_id
    return {"rows": rows, "cutoff_id": cutoff_id, "next_after": nxt, "done": len(rows) < limit or nxt >= cutoff_id,
            "total_in_cutoff": total, "page_max": cap}


def shadow_replay_check(after_id=0, limit=200):
    """Replay up to `limit` stored fh-1 records after `after_id` from their saved inputs (perf_shadow.check_record)."""
    import perf_shadow
    pg = shadow_page(after_id=after_id, limit=min(int(limit or 100), 100), inputs=True)
    n, bad = 0, []
    cm2c = {"computed_replayed": 0, "ineligible_skipped": 0, "missing_or_error": 0, "mismatched": 0}
    cm2bad = []
    for r in pg["rows"]:
        try:
            import cm2
            st, why = cm2.replay_status((r["rec"] if isinstance(r["rec"], dict) else json.loads(r["rec"])).get("cm2"))
        except Exception as e:      # noqa: BLE001
            st, why = "mismatch", [f"{type(e).__name__}: {str(e)[:120]}"]
        k = {"replayed": "computed_replayed", "ineligible": "ineligible_skipped", "mismatch": "mismatched"}.get(st, "missing_or_error")
        cm2c[k] += 1
        if st == "mismatch":
            cm2bad.append({"id": r["id"], "why": why[:3]})
        if r.get("inputs_body") is None or r.get("gi_body") is None:
            bad.append({"id": r["id"], "why": ["saved inputs missing"]})
            continue
        ok, why = perf_shadow.check_record(r["rec"], r["inputs_body"], r["gi_body"])
        n += 1
        if not ok:
            bad.append({"id": r["id"], "why": why})
    return {"rows_checked": n, "mismatches": len(bad), "examples": bad[:5], "next_after": pg["next_after"], "done": pg["done"],
            "source": "saved inputs (history snapshot + grader inputs) only",
            # cm-2 accounted apart: only "computed_replayed" are reconstructed forecasts; ineligible records are skipped, not
            # replayed (finish-fixes 2026-10-18)
            "cm2": dict(cm2c, examples=cm2bad[:5])}


def _forecast_cols():
    with _LOCK:
        con = _db()
        try:
            return [r[1] for r in con.execute("PRAGMA table_info(forecasts)")]
        finally:
            con.close()


REPORT_ROWS = 300                       # the newest picks the /admin page lists in full


def report():
    """The /admin pick-log page. 2026-10-26 audit: it read EVERY pick with SELECT * (factors and results, ~4-5 KB a row)
    into memory to count and bucket a handful of fields and list 300 rows. Now the counts read only the fields they use
    (every pick, light), and only the newest REPORT_ROWS picks are read in full. Same answer."""
    con = _read_db()
    try:
        rows = [dict(r) for r in con.execute("SELECT id, status, hit, p_pick, units, clv FROM picks "
                                             "WHERE COALESCE(test, 0) = 0 ORDER BY id DESC")]
        shown = [dict(r) for r in con.execute("SELECT * FROM picks WHERE COALESCE(test, 0) = 0 ORDER BY id DESC LIMIT ?",
                                              (REPORT_ROWS,))]
    finally:
        con.close()
    settled = [r for r in rows if r["status"] == "settled"]
    scored = [r for r in settled if r.get("hit") in (0, 1)]
    calibrated = [r for r in scored if isinstance(r.get("p_pick"), (int, float))
                  and math.isfinite(r["p_pick"]) and 0 <= r["p_pick"] <= 1]

    def bucket(rs, key, bounds):
        out = []
        for lo, hi, name in bounds:
            b_ = [r for r in rs if lo <= (r[key] or 0) < hi]
            if b_:
                out.append({"bucket": name, "n": len(b_),
                            "printed": round(sum(r["p_pick"] for r in b_) / len(b_), 3),
                            "actual": round(sum(r["hit"] for r in b_) / len(b_), 3)})
        return out

    pbounds = ((0.5, 0.55, "Lean 50-55%"), (0.55, 0.57, "B- 55-57%"), (0.57, 0.63, "B 57-63%"), (0.63, 0.68, "B+ 63-68%"),
               (0.68, 0.74, "A- 68-74%"), (0.74, 0.80, "A 74-80%"), (0.80, 1.01, "A+ 80%+"))
    ubounds = ((0, 0.01, "0U"), (0.5, 0.51, "0.5U"), (1, 1.01, "1U"), (1.5, 1.51, "1.5U"), (2, 2.01, "2U"))
    units_won = sum((r["units"] or 0) * (1 if r["hit"] else -1) for r in scored)
    clv = [r["clv"] for r in rows if r["clv"] is not None]
    return {
        "total": len(rows), "open": sum(r["status"] == "open" for r in rows), "settled": len(settled),
        "void": sum(r["status"] == "void" for r in rows),
        "push": sum(r["status"] == "push" for r in rows),
        "scored": len(scored), "calibration_scored": len(calibrated),
        "missing_outcome": len(settled) - len(scored), "missing_probability": len(scored) - len(calibrated),
        "by_printed_pct": bucket(calibrated, "p_pick", pbounds),
        "by_units": bucket(calibrated, "units", ubounds),
        "hit_rate_all": round(sum(r["hit"] for r in scored) / len(scored), 3) if scored else None,
        "units_net_flat": round(units_won, 2),
        "clv_avg": round(sum(clv) / len(clv), 2) if clv else None, "clv_n": len(clv),
        "clv_positive": round(sum(c > 0 for c in clv) / len(clv), 3) if clv else None,
        "rows": shown,
    }
