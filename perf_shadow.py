"""fh-1 - the full-history performance-model SHADOW (CHALLENGERS.md fh-1; owner 2026-10-15: a shadow only - never a live
grade, and no claim of better book-line picks before settled results show one).

Live and fh-1 go through the same production projection code - perf.project() with perf_live.projection()'s
post-processing and cs2_scout._perf_input_once()'s BO3.gg fallback rule (project_arm below; every record checks that it
reproduces the published forecast exactly) - and differ ONLY in history eligibility:
  live - the rows the published forecast used: the live perf_live cache entries (input revision N0, rule n0-r4), exactly
         and in their order - the live selection itself, not an approximation of it;
  fh-1 - those rows plus every row of the same query that N0 excludes (the 2026-10-13 backfill and every later lower-tier
         match): perf_live._PLAYER_SQL_N0 / _OPP_SQL with the same player ids / opponent id, window and filters (10-player
         maps, the 0-HS-on-12+-kills flag), each row judged by the same perf_live.n0() / _in_n0.
Every record (picks.db shadow_forecasts, written with the board's pre-match forecasts) carries both predictions
(projection, chance, side, grade, stake), the input snapshot and the grader inputs both arms were graded on (content-
addressed, picks.db shadow_inputs), the history revisions, the read times and two checks:
  n0_check  - the live arm rebuilt from the saved snapshot reproduces the published projection and chance exactly, and the
              shadow's own N0 view of its read equals the live rows (if not, the two reads saw different data);
  identical - fh-1 adds no row: its projection and chance equal the live arm's exactly.
replay(snapshot, grader inputs) recomputes both arms from the saved inputs alone; replay_check() compares stored records.
Runs in the board loop only (background, never a member's request). A failed read skips that pass - nothing is guessed,
and the live grade never waits on or depends on this module."""
import collections, copy, hashlib, json, threading, time, zlib
from datetime import date, datetime, timedelta, timezone

import perf

ID = "fh-1"
FORMAT = 1
ESTABLISHED_ROUNDS = 600.0        # registered split (before any fh-1 forecast settled) - see group()
TTL = 15 * 60                     # = perf_live.TTL
FAIL_WAIT = 30                    # a failed read is not retried for this long (one retry per key, shared)
_LOCK = threading.Lock()
_RAW = {}                         # key -> (read_at, value)
_FAIL = {}                        # key -> failed_at
STATS = collections.Counter()
LAST = {"error": None, "error_at": None, "record_at": None, "skip": collections.Counter()}
NUMERIC = ("version", "lambda", "lambda_kills", "rate", "rate_all_envs", "opp_factor", "hs_per_kill", "sd",
           "history_rounds_weighted", "env_rounds_weighted", "opp_rounds_weighted", "player_maps", "opp_maps", "newest_map_day")


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds") if t else None


def sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:16]


def pack(obj):
    return zlib.compress(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(), 6)


def unpack(blob):
    return json.loads(zlib.decompress(blob))


# ------------------------------------------------------------------------------------------------ the two arms (pure)
def _hltv_arm(ids, pm, tid, om, lan, day, scope, prop):
    """perf_live.projection() after its reads: the same perf.project call and the same fields."""
    if not ids:
        return {"error": "player not found in HLTV"}
    out = perf.project(pm, om, lan, day, scope, prop)
    out.update(hltv_player_ids=ids, hltv_opp_team=tid, player_maps=len(pm), opp_maps=len(om), opp_known=bool(om),
               lan_known=lan is not None, newest_map_day=max((r[0] for r in pm if r[0] < day), default=None))
    if not om:
        out["data_quality"] = ((out["data_quality"] + "; " if out["data_quality"] != "ok" else "")
                               + ("opponent not in HLTV" if not tid else "opponent has no HLTV maps") + " - average opponent")
    return out


def _rows(arm, blk):
    """(id, rows) one arm reads from a snapshot block: live = the live answer as it was; fh-1 = the same id's live rows +
    the rows N0 excludes (production's full-eligibility answer: no id -> nothing; otherwise every 10-player row)."""
    if arm == "live":
        return blk.get("live_id"), list(blk.get("live") or [])
    if not blk.get("id"):
        return None, []
    return blk["id"], list(blk.get("live") or []) + list(blk.get("extra") or [])


def project_arm(arm, snap):
    """One arm's performance-model input exactly as production builds it (perf_live.projection + the BO3.gg fallback rule
    of cs2_scout._perf_input_once), on that arm's eligible rows."""
    day, lan, sc, pr = snap["day"], snap["lan"], snap["scope"], snap["prop"]
    ids, pm = _rows(arm, snap["player"])
    tid, om = _rows(arm, snap["opp"]) if snap.get("opponent") else (None, [])
    out = _hltv_arm(ids, pm, tid, om, lan, day, sc, pr)
    no_hltv, err = bool(out.get("error")), out.get("error")
    if not out.get("error") and (out.get("player_maps") == 0 or out.get("newest_map_day") is None):
        no_hltv, err = True, "no HLTV maps in the last 420 days"
    if not no_hltv:
        return out
    rows = list(snap.get("bo3_rows") or [])
    if not rows:
        return {"error": err}
    tb, ob = _rows(arm, snap["opp_bo3"]) if snap.get("opponent") and snap.get("opp_bo3") else (None, [])
    bo3 = perf.project(rows, ob, lan, day, sc, pr, version=perf.VERSION + "-bo3")
    bo3.update(source="BO3.gg maps (HLTV has no history)", player_maps=len(rows), hltv_opp_team=tb, hltv_error=err,
               hltv_player_ids=ids or None, opp_maps=len(ob), opp_known=bool(ob))
    if not ob:
        dq = bo3.get("data_quality")
        bo3["data_quality"] = ((dq + "; ") if dq not in (None, "ok") else "") + ("opponent not in HLTV" if not tb else "opponent has no HLTV maps") + " - average opponent"
    return bo3


def grade_arm(gi, pf):
    """The live grader on the saved grader inputs with this arm's projection (grader.grade_leg + pick_verdict.verdict)."""
    import grader, pick_verdict
    d = copy.deepcopy(gi)
    d["perf"] = pf
    g = grader.grade_leg(d)
    return g, pick_verdict.verdict(g)


def _arm_summary(pf, g, v):
    return {"proj_version": pf.get("version"), "lambda_perf": pf.get("lambda"), "sd": pf.get("sd"),
            "player_maps": pf.get("player_maps"), "opp_maps": pf.get("opp_maps"),
            "history_rounds_weighted": pf.get("history_rounds_weighted"), "opp_rounds_weighted": pf.get("opp_rounds_weighted"),
            "lambda": g.get("lambda_final"), "p_more": g.get("p_more"), "p_more_raw": g.get("p_more_model_raw"),
            "projection_source": g.get("projection_source"), "side": v.get("side"), "grade": v.get("grade"),
            "units": v.get("units")}


def replay(snap, gi):
    """Both arms from saved inputs only: {"live": summary, "fh1": summary, "pf": {arm: projection}}."""
    out = {"pf": {}}
    for arm, k in (("live", "live"), ("fh1", "fh1")):
        pf = project_arm(arm, snap)
        if pf.get("error"):
            out[k] = {"error": pf["error"]}
            continue
        g, v = grade_arm(gi, pf)
        out["pf"][k] = pf
        out[k] = _arm_summary(pf, g, v)
    return out


def group(pub_pf):
    """Registered split (DEFINITION 'group'): from the PUBLISHED live projection, known before the match."""
    v = str(pub_pf.get("version") or "")
    rw = float(pub_pf.get("history_rounds_weighted") or 0.0) if not v.endswith("-bo3") else 0.0
    est = (not v.endswith("-bo3")) and rw >= ESTABLISHED_ROUNDS and bool(pub_pf.get("opp_known"))
    depth = ("BO3.gg fallback (no N0 history)" if v.endswith("-bo3") else "no N0 history" if rw <= 0 else "< 150 rounds" if rw < 150
             else "150-600 rounds" if rw < 600 else "600-1,500 rounds" if rw < 1500 else ">= 1,500 rounds")
    return ("established" if est else "thin history / lower tier"), depth


# lc-1 (owner 2026-10-16, ACCURACY_2026-10-16.md): the one compact correction of the live chance at offered lines -
# logit P(Over) = a_s + b_s * z, z = (projection - line) / (c_s * sqrt(projection)) (the projection's standardized
# disagreement with the line, c = the live spread constants), ridge-fitted per stat on every settled real offered line
# through 2026-10-16 (1,309 units / 78 matches; ridge 100 chosen leave-last-day-out). FROZEN - saved with every record
# before the match and scored after settlement; never a live input.
LC1 = {"version": "lc-1", "w": {"kills": (-0.02688, -0.00717), "hs": (-0.04084, 0.00771)}, "data_hash": "654e9c6827b6",
       "cutoff_day": "2026-10-16", "ridge": 100}


def lc1_p(lam, line, prop, scope="12"):
    """lc-1's chance of Over for one offered line (None without a projection or line)."""
    if lam is None or line is None or prop not in LC1["w"]:
        return None
    import math
    c = perf.SPREAD.get((prop, scope or "12"), perf.SPREAD[(prop, "12")])
    z = (float(lam) - float(line)) / (c * math.sqrt(max(float(lam), 1.0)))
    a, b = LC1["w"][prop]
    return round(1 / (1 + math.exp(-(a + b * z))), 6), round(z, 4)


def _same(a, b):
    return all(a.get(k) == b.get(k) for k in NUMERIC)


# ------------------------------------------------------------------------------------------------ reads (board loop only)
def _cached(key, fn, live_at=0.0):
    """15-minute memory cache, never older than the live answer it is compared with (a live entry refreshed after our read
    means re-read); one read per key at a time; a failure raises (the record is skipped) and waits FAIL_WAIT s."""
    import hltv
    now = time.time()
    with _LOCK:
        hit = _RAW.get(key)
        if hit and now - hit[0] < TTL and hit[0] >= live_at:
            return hit[1], hit[0]
        if now - _FAIL.get(key, 0) < FAIL_WAIT:
            raise hltv.Unavailable("fh-1 read failed under 30 s ago")

    def read():
        try:
            val = fn()
        except Exception:
            with _LOCK:
                _FAIL[key] = time.time()
            raise
        t = time.time()
        with _LOCK:
            if len(_RAW) > 20000:
                _RAW.clear()
            _RAW[key] = (t, val)
            _FAIL.pop(key, None)
        return val, t

    def peek():
        with _LOCK:
            h = _RAW.get(key)
        return (h[1], h[0]) if h and time.time() - h[0] < TTL and h[0] >= live_at else hltv.MISS
    return hltv.single_flight(("fh1",) + key, read, peek)


def _player_ids(nick, team):
    import hltv

    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                return list(hltv._player_ids(cur, nick, team) or [])
        finally:
            con.close()
    return _cached(("pid", hltv._norm(nick), hltv._norm(team)), load)[0]


def _team_id(name):
    import hltv

    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                return hltv.canonical_team_id(cur, name)
        finally:
            con.close()
    return _cached(("tid", hltv._norm(name)), load)[0]


def _player_raw(ids, since, live_at):
    """Every row of the live player query (perf_live._PLAYER_SQL_N0) for these ids: (day, k, rounds, hs, lan, hs_ok, n, matchid)."""
    import hltv, perf_live

    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute(perf_live._PLAYER_SQL_N0, {"ids": list(ids), "since": since})
                raw = cur.fetchall()
        finally:
            con.close()
        return [(d.toordinal(), k, r, hs, bool(lan), not (hs == 0 and k >= 12), int(n), int(mid)) for d, k, r, hs, lan, n, mid in raw]
    return _cached(("p", tuple(sorted(ids)), since), load, live_at)


def _opp_raw(tid, since, live_at):
    """Every row of the live opponent query (perf_live._OPP_SQL): (day, kills conceded, rounds, matchid)."""
    import hltv, perf_live

    def load():
        con = hltv._connect()
        try:
            with con.cursor() as cur:
                cur.execute(perf_live._OPP_SQL, {"tid": tid, "since": since})
                raw = cur.fetchall()
        finally:
            con.close()
        return [(d.toordinal(), int(k), int(r), int(mid)) for d, k, r, mid in raw]
    return _cached(("o", tid, since), load, live_at)


def _order(row):
    """A fixed order for the rows N0 excludes (the query has no ORDER BY): the same rows always make the same snapshot."""
    return json.dumps(row)


def _live_entry(key):
    """The live perf_live cache entry (read_at, value) the published forecast was built from - None when absent / failed."""
    import perf_live
    with perf_live._LOCK:
        hit = perf_live._CACHE.get(key)
    if not hit or hit[1] is perf_live._FAILED:
        return None
    return hit


def _bo3_rows(pb):
    """cs2_scout._perf_input_once's BO3.gg rows, built the same way."""
    me = pb.get("_match_env") or {}
    return [(date.fromisoformat(r["date"]).toordinal(), r["kills"], r["rounds"], r.get("headshots"),
             (r.get("env") or me.get(r.get("match_id"))) == "lan",
             not (r.get("headshots") == 0 and (r["kills"] or 0) >= 12))
            for r in pb.get("recent_maps") or [] if r.get("kills") is not None and r.get("rounds") and r.get("date")]


def build(res, r):
    """(record, bodies) for one board grade, or (None, skip reason). Reads only - the live grade is already made."""
    import hltv, perf_live, cs2_scout
    gi = res.get("grader_inputs") or {}
    pub = gi.get("perf") if isinstance(gi.get("perf"), dict) else None
    g = res.get("grade") or {}
    if not pub or pub.get("error") or pub.get("lambda") is None:
        return None, "live grade not on the performance model"
    ver = str(pub.get("version") or "")
    if ver not in (perf.VERSION, perf.VERSION + "-bo3") or g.get("projection_source") != ver or "p_more" not in g:
        return None, "live grade not on the performance model"
    pb = res.get("player_block") or {}
    nm = res.get("next_match") or {}
    nick = (pb.get("player") or {}).get("nick")
    team = pb.get("playing_for") or pb.get("current_team")
    opponent = nm.get("opponent") or r.get("opponent") or None
    scope = g.get("scope") or r.get("scope") or "12"
    prop = "hs" if g.get("prop") == "hs" else "kills"
    tonight = (pb.get("_env") or {}).get("tonight")
    lan = None if tonight is None else tonight == "lan"
    day = cs2_scout.match_day(r.get("start") or nm.get("start"))
    since = (date.fromordinal(day) - timedelta(days=420)).isoformat()
    since_today = (date.today() - timedelta(days=420)).isoformat()
    rev, rule = perf_live.INPUT_REV, perf_live.INPUT_RULE
    lp = _live_entry(("p", hltv._norm(nick), hltv._norm(team), since, rev, rule))
    if lp is None:
        return None, "live player rows not in the live cache"
    lo = _live_entry(("o", hltv._norm(opponent), since, rev, rule)) if opponent else None
    bo3 = ver.endswith("-bo3")
    lob = _live_entry(("o", hltv._norm(opponent), since_today, rev, rule)) if opponent and bo3 else None
    if not bo3 and opponent and lo is None:
        return None, "live opponent rows not in the live cache"
    s = perf_live.n0()
    n0_sha = s.get("sha")
    # the player: the live answer, the same ids (or the same resolution when N0 had none), every row of the same query
    live_ids, live_pm = lp[1]
    ids = list(live_ids) if live_ids else _player_ids(nick, team)
    raw, p_at = _player_raw(ids, since, lp[0]) if ids else ([], None)
    own_n0 = [list(x[:6]) for x in raw if x[6] == 10 and perf_live._in_n0(x[7], s)]
    extra = sorted((list(x[:6]) for x in raw if x[6] == 10 and not perf_live._in_n0(x[7], s)), key=_order)
    any_n0 = any(perf_live._in_n0(x[7], s) for x in raw)
    live_rows = [list(x) for x in live_pm]
    p_consistent = (sorted(own_n0, key=str) == sorted(live_rows, key=str)) if live_ids else (not any_n0)
    snap = {"format": FORMAT, "day": day, "lan": lan, "scope": scope, "prop": prop, "since": since, "since_today": since_today,
            "opponent": opponent,
            "player": {"live_id": list(live_ids) if live_ids else None, "live": live_rows, "id": ids or None, "extra": extra}}
    tid, o_at, o_consistent, o_extra_n = None, None, True, 0
    if opponent:
        tid = (lo[1][0] if lo and lo[1][0] else None) or (lob[1][0] if lob and lob[1][0] else None) or _team_id(opponent)
        oraw, o_at = _opp_raw(tid, since_today, max(lo[0] if lo else 0, lob[0] if lob else 0)) if tid else ([], None)
        lo_d = date.fromisoformat(since).toordinal()

        def block(entry, rows, lo_day):
            win = [x for x in rows if x[0] >= lo_day]
            own = [list(x[:3]) for x in win if perf_live._in_n0(x[3], s)]
            ext = sorted((list(x[:3]) for x in win if not perf_live._in_n0(x[3], s)), key=_order)
            if entry is not None:
                lt, lr = entry[1]
                lr = [list(x) for x in lr]
                ok = (sorted(own, key=str) == sorted(lr, key=str)) if lt else not any(perf_live._in_n0(x[3], s) for x in win)
                return {"live_id": lt, "live": lr, "id": tid, "extra": ext, "live_source": "live cache"}, ok
            # this window was not read by the live forecast (it never needed it): the N0 part from our own read, by
            # perf_live.opp_maps' rule (rows but none in N0 = not in HLTV under N0)
            return {"live_id": tid if (own or not win) else None, "live": own, "id": tid, "extra": ext,
                    "live_source": "shadow read (N0 view)"}, True
        snap["opp"], ok1 = block(lo, oraw, lo_d)
        o_consistent = ok1
        o_extra_n = sum(1 for x in snap["opp"]["extra"] if x[0] < day)
        if bo3:
            snap["opp_bo3"], ok2 = block(lob, oraw, date.fromisoformat(since_today).toordinal())
            o_consistent = o_consistent and ok2
    else:
        snap["opp"] = {"live_id": None, "live": [], "id": None, "extra": []}
    if bo3:
        snap["bo3_rows"] = [list(x) for x in _bo3_rows(pb)]
    # grader inputs both arms are graded on: the published forecast's, as stored (JSON), without the projection (rebuilt
    # from the snapshot) and the odds re-check (never read by the grader)
    gi_s = json.loads(json.dumps({k: v for k, v in gi.items() if k not in ("perf", "odds_check")}, default=str))
    rp = replay(snap, gi_s)
    if "error" in rp["live"] or "error" in rp["fh1"]:
        return None, "an arm has no performance-model projection: " + str(rp["live"].get("error") or rp["fh1"].get("error"))
    pub_ok = _same(rp["pf"]["live"], pub) and rp["live"]["p_more"] == g.get("p_more")
    p_extra_n = sum(1 for x in extra if x[0] < day)
    identical = not extra and not snap["opp"].get("extra") and not (snap.get("opp_bo3") or {}).get("extra")
    if identical and not (_same(rp["pf"]["live"], rp["pf"]["fh1"]) and rp["live"]["p_more"] == rp["fh1"]["p_more"]):
        raise AssertionError("fh-1 differs from live on identical history")       # never expected: the same code on the same rows
    grp, depth = group(pub)
    in_sha, gi_sha = sha(snap), sha(gi_s)
    rec = {"format": FORMAT, "challenger": ID, "computed_at": _now_iso(), "line": g.get("line"), "prop": prop, "scope": scope,
           "day": day, "live": rp["live"], "fh1": rp["fh1"],
           "published": {"p_more": g.get("p_more"), "lambda": g.get("lambda_final"), "proj_version": ver},
           "n0_check": {"published_reproduced": pub_ok, "player_read_consistent": p_consistent, "opp_read_consistent": o_consistent,
                        "n0_sha": n0_sha, "live_revision": (pub.get("hltv_data") or {}).get("input_revision"),
                        "exact": bool(pub_ok and p_consistent and o_consistent)},
           "identical_history": identical, "adds": {"player_maps": p_extra_n, "opp_maps": o_extra_n},
           "group": grp, "depth": depth,
           "history": {"live": f"{rev}/{rule} (sha {n0_sha})", "fh1": "N - every HLTV match in the database (all tiers)",
                       "inputs_sha": in_sha},
           "read_at": {"live_player": _iso(lp[0]), "live_opp": _iso(lo[0]) if lo else None, "player": _iso(p_at), "opp": _iso(o_at)},
           "inputs_sha": in_sha, "gi_sha": gi_sha}
    lc = lc1_p(g.get("lambda_final"), g.get("line"), prop, scope)
    if lc:
        rec["lc1"] = {"p_more": lc[0], "z": lc[1], "version": LC1["version"], "from": "published projection and line"}
    rec["cm1"] = _cm1(ids, team, tid, day, lan, g.get("line"), prop, scope, (res.get("book") or {}).get("quote"))
    return rec, {in_sha: ("snapshot", pack(snap)), gi_sha: ("grader_inputs", pack(gi_s))}


def _cm1(ids, team, opp_tid, day, lan, line, prop, scope, quote):
    """cm-1 (complete-model shadow, cm1.py) for this forecast at this moment - never raises, never touches the live arms."""
    try:
        import cm1
        own = _team_id(team) if team and prop in ("kills", "hs") and (scope or "12") == "12" else None
        out = cm1.for_forecast(cm1.current(), ids, own, opp_tid, day, lan, line, prop, scope, quote)
        cm1.STATS["ineligible" if out.get("ineligible") else "computed"] += 1
        return out
    except Exception as e:      # noqa: BLE001 - shadow only
        try:
            import cm1
            cm1.STATS["error"] += 1
        except Exception:
            pass
        return {"version": "cm-1", "error": f"{type(e).__name__}: {str(e)[:160]}"}


def attach(res, r):
    """Board loop: put the fh-1 record on res["_fh1"] (picklog writes it with the forecast). Never raises."""
    try:
        rec, bodies = build(res, r)
    except Exception as e:      # noqa: BLE001 - shadow only: the live grade and its logging never depend on it
        STATS["error"] += 1
        LAST.update(error=f"{type(e).__name__}: {str(e)[:200]}", error_at=_now_iso())
        return None
    if rec is None:
        STATS["skipped"] += 1
        LAST["skip"][bodies] += 1
        return None
    STATS["built"] += 1
    STATS["n0_exact" if rec["n0_check"]["exact"] else "n0_not_exact"] += 1
    LAST["record_at"] = rec["computed_at"]
    res["_fh1"] = {"rec": rec, "bodies": bodies}
    return rec


def status():
    out = {"id": ID, "counts": dict(STATS), "last_error": LAST["error"], "last_error_at": LAST["error_at"],
           "last_record_at": LAST["record_at"], "skips": dict(LAST["skip"].most_common(10))}
    try:
        import cm1
        out["cm1"] = cm1.status()
    except Exception as e:      # noqa: BLE001
        out["cm1"] = {"error": f"{type(e).__name__}: {e}"}
    return out


# ------------------------------------------------------------------------------------------------ replay from saved inputs
def check_record(rec, snap, gi):
    """Replay one stored record from its saved snapshot + grader inputs: (ok, detail)."""
    rp = replay(snap, gi)
    bad = [] if sha(snap) == rec.get("inputs_sha") and sha(gi) == rec.get("gi_sha") else ["saved input hash differs"]
    for k in ("live", "fh1"):
        a, b = rp.get(k) or {}, rec.get(k) or {}
        for f in ("lambda", "p_more", "p_more_raw", "lambda_perf", "sd", "side", "grade", "units"):
            if a.get(f) != b.get(f):
                bad.append(f"{k}.{f}: stored {b.get(f)!r} replay {a.get(f)!r}")
    if rec.get("cm1"):                                  # cm-1: features, lambda and chance from its saved sums only
        import cm1
        ok, why = cm1.replay(rec["cm1"])
        bad += [f"cm1 {w}" for w in why]
    return not bad, bad[:6]
