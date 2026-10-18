"""cm-1 - the complete-model SHADOW (owner 2026-10-16: production shadow, forward confirmation only; REALLINE_ARCHIVE_2026-10-16.md
section 9). Never a live input: no projection, chance, grade or stake changes.

The frozen candidate (cm1_model.json, artifact hash ARTIFACT_SHA): a Poisson regression for the MEAN of Maps 1-2 kills on 14
pre-match features, with the study's uncertainty model - a discretised normal, sd = c sqrt(lambda) (NOT Poisson variance) -
for the chance at a line (kills 22-78% cap; HS = kills x the full-history HS ratio; pushes excluded as live p_more).

Two parts:
- tables (build_tables / refresh_tables): from the HLTV database, by the study's exact rules (full-scoreboard maps since
  2025-09-01, the weekly opponent-adjustment refits, the Elo ratings, the per-player / per-team map rows). Built in the
  learning cycle's separate low-priority process every REFRESH_S and written atomically to CS2_DATA_DIR/.cache - never in a
  member's request, never in the server's request threads.
- forecast (for_forecast): at the moment the board writes a forecast's pre-match record (perf_shadow.build), cm-1 from the
  latest tables on disk: only rows strictly before the match day, the latest refit that exists on or before it, Elo at the
  start of that day. The record saves the intermediate sums, the 14 features, lambda, the chance and the tables' build id, so
  replay() reproduces it exactly from the saved values."""
import hashlib
import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("CS2_DATA_DIR") or HERE)
TABLES = DATA_DIR / ".cache" / "cm1_tables.npz"
TABLES_META = DATA_DIR / ".cache" / "cm1_tables.json"
REFRESH_S = 2 * 3600
DATA_START = date(2025, 9, 1)
FIRST_REFIT = date(2025, 10, 27)
MODEL = json.load(open(HERE / "cm1_model.json", encoding="utf-8"))
ARTIFACT_SHA = hashlib.sha256(json.dumps(MODEL, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
VERSION = MODEL["version"]
K = MODEL["constants"]
HL, WIN = K["half_life_days"], K["window_days"]
_MEAN = np.array(MODEL["mean_model"]["mean"])
_STD = np.array(MODEL["mean_model"]["std"])
_COEF = np.array(MODEL["mean_model"]["coef"])
_ICPT = float(MODEL["mean_model"]["intercept"])
C_KILLS = float(MODEL["uncertainty_model"]["kills"]["c_kills"])
C_HS = float(MODEL["uncertainty_model"]["hs"]["c_hs"])

# the study's dump query (research/accuracy_2026_10_15/dump.py), the columns cm-1 reads
SQL = """SELECT mo.matchid, mo.date, mo.lan, mm.number, ps.playerid, ps.teamid, opp.teamid, ps.k, h.hs, own.score, opp.score
FROM match_maps mm JOIN match_overview mo USING (matchid) JOIN maps mp ON mp.mapid = mm.mapid
JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
JOIN matches opp ON opp.matchid = mm.matchid AND opp.teamid <> ps.teamid AND opp.mapid = mm.mapid AND opp.sideid = 0
LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
WHERE mo.date >= %s AND mo.date <= %s ORDER BY mo.date, mo.matchid, mm.number"""


# ------------------------------------------------------------------------------------------------ tables (pure)
def build_tables(rows, built_at=None, through=None, env=None):
    """rows: (matchid, date 'YYYY-MM-DD', lan, map_no, player, team, opp, k, hs, own, opp_score) - the study's dump rows.
    Returns the tables dict (numpy arrays + meta). Exactly the study's rules (acc2/build.py, oa_build.py).
    env: cm-1 itself never passes it (its frozen rule: 1.0 if lan else 0.0, so an unknown environment counts as online);
    cm2.py passes its corrected rule (unknown = NaN: overall history only, never the LAN or online subset)."""
    built_at = built_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    maps = {}
    for r in rows:
        maps.setdefault((r[0], r[3]), []).append(r)
    dn = lambda s: date.fromisoformat(str(s)[:10]).toordinal()
    ph, tc, tr = [], [], []                                  # player rows, conceded rows, team-rounds rows
    for (m, mn), v in maps.items():
        if len(v) != 10:
            continue
        day = dn(v[0][1])
        conc = {}
        for r in v:
            k = r[7] or 0
            rd = (r[9] or 0) + (r[10] or 0)
            hs = r[8]
            ok = hs is not None and not (hs == 0 and k >= 12)
            ph.append((r[4], day, k, rd, hs if hs is not None else 0, env(r[2]) if env else (1.0 if r[2] else 0.0), 1.0 if ok else 0.0, r[6]))
            c = conc.setdefault(r[6], [0, 0, day])
            c[0] += k
            c[1] = rd
        for t, (k, rd, d) in conc.items():
            tc.append((t, d, k, rd))
        for t in {r[5] for r in v}:
            tr.append((t, day, v[0][9] + v[0][10]))
    # per-entity sorted arrays (the study sorted each entity's tuples - the same order, the same sums)
    def pack(items, ncol):
        items.sort()
        ids = np.array([x[0] for x in items], dtype=np.int64)
        body = np.array([x[1:] for x in items], dtype=float).reshape(-1, ncol)
        uniq, start = np.unique(ids, return_index=True)
        end = np.append(start[1:], len(ids))
        return uniq, start, end, body
    p_ids, p_s, p_e, p_rows = pack(ph, 7)                    # (day, k, rd, hs, lan, hs_ok, opp)
    c_ids, c_s, c_e, c_rows = pack(tc, 3)                    # (day, kills conceded, rd)
    t_ids, t_s, t_e, t_rows = pack(tr, 2)                    # (day, rounds)
    # opponent-adjustment refits (oa_build.py)
    F = np.array([(x[1], x[0], x[7], x[2], x[3]) for x in ph], dtype=float).reshape(-1, 5)   # day, player, opp, k, rd
    players = np.unique(F[:, 1]).astype(np.int64)
    teams = np.unique(F[:, 2]).astype(np.int64)
    fp = np.searchsorted(players, F[:, 1].astype(np.int64))
    ft = np.searchsorted(teams, F[:, 2].astype(np.int64))
    last = (through or date.fromisoformat(built_at[:10]))
    refits = []
    d0 = FIRST_REFIT
    while d0 <= last:
        refits.append(d0)
        d0 += timedelta(days=7)
    D = np.ones((len(refits), len(teams)))
    for ri, R in enumerate(refits):
        Rd = R.toordinal()
        msk = (F[:, 0] < Rd) & (F[:, 0] >= Rd - WIN)
        if msk.sum() < 1000:
            continue
        w = np.exp(-(Rd - F[msk, 0]) / HL)
        k, rd, P_, T_ = F[msk, 3], F[msk, 4], fp[msk], ft[msk]
        mu = (w * k).sum() / (w * rd).sum()
        A = np.ones(len(players))
        Dd = np.ones(len(teams))
        sa, sd = K["k0"] * mu, 5 * K["ko"] * mu
        for _ in range(12):
            num = np.bincount(P_, weights=w * k, minlength=len(players))
            den = np.bincount(P_, weights=w * rd * mu * Dd[T_], minlength=len(players))
            A = (num + sa) / (den + sa)
            num_t = np.bincount(T_, weights=w * k, minlength=len(teams))
            den_t = np.bincount(T_, weights=w * rd * mu * A[P_], minlength=len(teams))
            Dd = (num_t + sd) / (den_t + sd)
            expo = np.bincount(T_, weights=w * rd * A[P_], minlength=len(teams))
            Dd = Dd / ((Dd * expo).sum() / expo.sum())
        D[ri] = Dd
    # Elo (acc2/build.py): every series in the rows, start of day ratings, K 24 on the map score
    series = {}
    for (m, mn), v in maps.items():
        for r in v:
            series.setdefault((m, r[5]), [r[1], r[6], 0, 0])
    for (m, mn), v in maps.items():
        for t in {r[5] for r in v}:
            r0 = next(r for r in v if r[5] == t)
            series[(m, t)][2] += int(r0[9] > r0[10])
            series[(m, t)][3] += 1
    by_day = {}
    for (m, t), (d, o, w_, n) in series.items():
        by_day.setdefault(str(d)[:10], {})[(m, t)] = (o, w_, n)
    elo, timeline = {}, []
    for d in sorted(by_day):
        upd = {}
        for (m, t), (o, w_, n) in by_day[d].items():
            if (m, o) in by_day[d] and t < o:
                et, eo = elo.get(t, 1500.0), elo.get(o, 1500.0)
                ea = 1 / (1 + 10 ** ((eo - et) / 400))
                res = 1.0 if w_ > n - w_ else 0.0 if w_ < n - w_ else 0.5
                upd[t] = upd.get(t, 0) + 24 * (res - ea)
                upd[o] = upd.get(o, 0) - 24 * (res - ea)
        for t, x in upd.items():
            elo[t] = elo.get(t, 1500.0) + x
            timeline.append((t, dn(d), elo[t]))
    e_ids, e_s, e_e, e_rows = pack(timeline, 2)             # (day, rating after that day)
    arrays = {"p_ids": p_ids, "p_s": p_s, "p_e": p_e, "p_rows": p_rows, "c_ids": c_ids, "c_s": c_s, "c_e": c_e, "c_rows": c_rows,
              "t_ids": t_ids, "t_s": t_s, "t_e": t_e, "t_rows": t_rows, "teams": teams, "D": D,
              "refits": np.array([R.toordinal() for R in refits], dtype=np.int64),
              "e_ids": e_ids, "e_s": e_s, "e_e": e_e, "e_rows": e_rows}
    h = hashlib.sha256()
    for k_ in sorted(arrays):
        h.update(k_.encode())
        h.update(np.ascontiguousarray(arrays[k_]).tobytes())
    meta = {"built_at": built_at, "rows": len(rows), "full_maps": int(sum(1 for v in maps.values() if len(v) == 10)),
            "players": int(len(p_ids)), "teams": int(len(teams)), "refits": [str(R) for R in refits[-3:]], "n_refits": len(refits),
            "data_start": str(DATA_START), "tables_sha": h.hexdigest()[:16], "artifact_sha": ARTIFACT_SHA}
    return {"arrays": arrays, "meta": meta}


def save_tables(t, path=TABLES, meta_path=TABLES_META):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(tmp, **t["arrays"])
    os.replace(tmp, path)
    tmpm = meta_path.with_suffix(".tmp")
    tmpm.write_text(json.dumps(t["meta"]))
    os.replace(tmpm, meta_path)


def refresh_tables(now=None, force=False, path=TABLES, meta_path=TABLES_META):
    """Learning child process: rebuild the tables from the HLTV database when older than REFRESH_S. Never raises."""
    import hltv
    try:
        if not hltv.dsn():
            return {"skipped": "HLTV database not configured"}
        if not force and meta_path.exists() and time.time() - meta_path.stat().st_mtime < REFRESH_S:
            return {"skipped": "fresh"}
        import psycopg2
        today = (now or datetime.now(timezone.utc)).date()
        con = psycopg2.connect(hltv.dsn(), connect_timeout=20)
        con.set_session(readonly=True, autocommit=True)
        rows = []
        try:
            cur = con.cursor()
            cur.execute("SET statement_timeout = 300000")
            d0 = DATA_START
            while d0 <= today:
                d1 = min(date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1) - timedelta(days=1), today)
                cur.execute(SQL, (d0, d1))
                rows += [(int(a), str(b), bool(c), int(d) if d is not None else None, int(e), int(f), int(g), h_, i_, j_, k_)
                         for a, b, c, d, e, f, g, h_, i_, j_, k_ in cur.fetchall()]
                d0 = d1 + timedelta(days=1)
        finally:
            con.close()
        t = build_tables(rows, through=today)
        save_tables(t, path, meta_path)
        return {"built": t["meta"]}
    except Exception as e:      # noqa: BLE001 - shadow only: the cycle never fails on it
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


# ------------------------------------------------------------------------------------------------ tables in the server (read-only)
_T = {"tables": None, "mtime": 0.0, "checked": 0.0, "meta": None}
_LOCK = threading.Lock()


def current(path=TABLES, meta_path=TABLES_META):
    """The latest tables on disk (reloaded when the file changes; checked at most once a minute). None when absent."""
    now = time.time()
    with _LOCK:
        if now - _T["checked"] < 60 and _T["tables"] is not None:
            return _T["tables"]
        _T["checked"] = now
        try:
            mt = path.stat().st_mtime
        except OSError:
            return _T["tables"]
        if _T["tables"] is not None and mt == _T["mtime"]:
            return _T["tables"]
    with np.load(path) as z:
        arrays = {k: z[k] for k in z.files}
    meta = json.loads(meta_path.read_text())
    t = {"arrays": arrays, "meta": meta}
    with _LOCK:
        _T.update(tables=t, mtime=mt, meta=meta)
    return t


# ------------------------------------------------------------------------------------------------ one forecast (pure)
def _block(a, ids_key, s_key, e_key, rows_key, ent):
    ids = a[ids_key]
    i = np.searchsorted(ids, ent)
    if i >= len(ids) or ids[i] != ent:
        return None
    return a[rows_key][a[s_key][i]:a[e_key][i]]


def _window(b, day):
    """Rows with day - 420 <= map day < day and their weights."""
    if b is None or not len(b):
        return b, np.zeros(0)
    i = np.searchsorted(b[:, 0], day, "left")
    j = np.searchsorted(b[:, 0], day - WIN, "left")
    w = b[j:i]
    return w, np.exp(-(day - w[:, 0]) / HL)


def inputs(t, player_ids, team_id, opp_id, day, lan):
    """The intermediate sums of one forecast from the tables (every row strictly before `day`) - or {"ineligible": why}."""
    a = t["arrays"]
    blocks = [b for b in (_block(a, "p_ids", "p_s", "p_e", "p_rows", int(p)) for p in player_ids or []) if b is not None]
    if not blocks:
        return {"ineligible": "no HLTV player rows"}
    pb = np.concatenate(blocks)
    pb = pb[np.lexsort(tuple(pb[:, c] for c in range(pb.shape[1] - 1, -1, -1)))]
    b, w = _window(pb, day)
    empty = not len(b)              # the study kept these players with zero sums (prior only) and no opponent factor
    env = b[:, 4] == (1.0 if lan else 0.0)
    ok = b[:, 5] > 0.5
    ref = a["refits"]
    ri = int(np.searchsorted(ref, day, "right") - 1)
    if ri < 0:
        return {"ineligible": "no opponent-adjustment refit on or before the match day"}
    teams = a["teams"]
    ti = np.searchsorted(teams, b[:, 6].astype(np.int64))
    Dh = a["D"][ri][ti] if len(b) else np.ones(0)
    ob, ow = _window(_block(a, "c_ids", "c_s", "c_e", "c_rows", int(opp_id)), day)
    t1, w1 = _window(_block(a, "t_ids", "t_s", "t_e", "t_rows", int(team_id)), day)
    t2, w2 = _window(_block(a, "t_ids", "t_s", "t_e", "t_rows", int(opp_id)), day)
    oi = np.searchsorted(teams, int(opp_id))
    dup = float(a["D"][ri][oi]) if oi < len(teams) and teams[oi] == int(opp_id) and not empty else None

    def elo(tid):
        e = _block(a, "e_ids", "e_s", "e_e", "e_rows", int(tid))
        if e is None:
            return 1500.0
        i = np.searchsorted(e[:, 0], day, "left")             # ratings after days strictly before the match day
        return float(e[i - 1, 1]) if i > 0 else 1500.0
    return {"k": float((w * b[:, 1]).sum()), "r": float((w * b[:, 2]).sum()), "ke": float((w * b[:, 1])[env].sum()),
            "re": float((w * b[:, 2])[env].sum()), "hk": float((w * b[:, 3])[ok].sum()), "hkk": float((w * b[:, 1])[ok].sum()),
            "maps": int(len(b)), "ka": float((w * b[:, 1] / Dh).sum()),
            "ck": float((ow * ob[:, 1]).sum()) if ob is not None and len(ob) else 0.0, "cr": float((ow * ob[:, 2]).sum()) if ob is not None and len(ob) else 0.0,
            "t1r": float((w1 * t1[:, 1]).sum()) if t1 is not None and len(t1) else 0.0, "t1w": float(w1.sum()) if t1 is not None and len(t1) else 0.0,
            "t2r": float((w2 * t2[:, 1]).sum()) if t2 is not None and len(t2) else 0.0, "t2w": float(w2.sum()) if t2 is not None and len(t2) else 0.0,
            "dup": dup, "elo_own": elo(team_id), "elo_opp": elo(opp_id), "lan": bool(lan), "day": int(day),
            "refit": date.fromordinal(int(ref[ri])).isoformat()}


def features(s):
    """The 14 features from the saved sums (models.py feats)."""
    pop, pm = K["pop"], K["pm"]
    rate_all = (s["k"] + K["k0"] * pop * pm) / (s["r"] + K["k0"])
    rate = (s["ke"] + K["ke"] * rate_all) / (s["re"] + K["ke"])
    opp = ((s["ck"] + K["ko"] * pop * 5) / (s["cr"] * 5 + K["ko"] * 5)) / pop
    adj = (s["ka"] + K["k0"] * pop * pm) / (s["r"] + K["k0"])
    trpm = (s["t1r"] + K["rounds_prior"] * K["rounds_prior_maps"]) / (s["t1w"] + K["rounds_prior_maps"])
    orpm = (s["t2r"] + K["rounds_prior"] * K["rounds_prior_maps"]) / (s["t2w"] + K["rounds_prior_maps"])
    return [math.log(rate_all), math.log(rate), math.log(opp), math.log1p(s["r"]), math.log1p(s["re"]), math.log1p(s["maps"]),
            1.0 if s["lan"] else 0.0, s["elo_own"] / 400, s["elo_opp"] / 400, (s["elo_own"] - s["elo_opp"]) / 400,
            math.log(max(trpm, 1)), math.log(max(orpm, 1)), math.log(adj), math.log(s["dup"] if s["dup"] else 1.0)]


def hs_ratio(s):
    return (s["hk"] + K["k_hs"] * K["pop_hs"]) / (s["hkk"] + K["k_hs"])


def predict(x):
    return float(math.exp(_ICPT + float(np.dot(_COEF, (np.asarray(x, float) - _MEAN) / _STD))))


_Phi = lambda v: 0.5 * (1 + math.erf(v / math.sqrt(2)))


def p_over(lam, line, prop):
    """The study's chance of Over at a line: discretised normal, push excluded; kills capped 22-78%."""
    c = C_KILLS if prop == "kills" else C_HS
    sd = c * math.sqrt(max(lam, 1.0))
    if line != int(line):
        po = 1 - _Phi((math.floor(line) + 0.5 - lam) / sd)
        pu = 1 - po
    else:
        po, pu = 1 - _Phi((line + 0.5 - lam) / sd), _Phi((line - 0.5 - lam) / sd)
    p = po / (po + pu) if po + pu > 0 else 0.5
    if prop == "kills":
        lo, hi = MODEL["uncertainty_model"]["kills"]["cap"]
        p = min(max(p, lo), hi)
    return p


def score(s, line, prop):
    """lambda (of this stat), the chance of Over and the features from saved sums - the same function forecasts and replay use."""
    x = features(s)
    lam_k = predict(x)
    hsr = hs_ratio(s)
    lam = lam_k if prop == "kills" else lam_k * hsr
    return {"features": [round(v, 12) for v in x], "lambda_kills": round(lam_k, 9), "hs_per_kill": round(hsr, 9), "lambda": round(lam, 9),
            "p_more": round(p_over(lam, float(line), prop), 9)}


def for_forecast(t, player_ids, team_id, opp_id, day, lan, line, prop, scope, quote=None):
    """The cm-1 record of one forecast (saved inside the pre-match fh-1 record), or {"ineligible": why}."""
    base = {"version": VERSION, "artifact_sha": ARTIFACT_SHA, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if t is None:
        return dict(base, ineligible="cm-1 tables not built yet")
    base["tables"] = {k: t["meta"].get(k) for k in ("built_at", "tables_sha", "rows")}
    if (scope or "12") != "12" or prop not in ("kills", "hs"):
        return dict(base, ineligible="cm-1 covers Maps 1-2 kills and headshots only")
    if lan is None:
        return dict(base, ineligible="LAN / online unknown")
    if not team_id or not opp_id:
        return dict(base, ineligible="own team or opponent not identified in HLTV")
    s = inputs(t, player_ids, team_id, opp_id, day, lan)
    if s.get("ineligible"):
        return dict(base, **s)
    out = dict(base, inputs=s, line=float(line), prop=prop, player_ids=[int(p) for p in player_ids], team_id=int(team_id), opp_id=int(opp_id))
    out.update(score(s, line, prop))
    q = quote or {}
    tq = ((q.get("anchors") or {}).get("target")) or {}
    out["offer"] = {"over_offered": tq.get("over_offered", q.get("over_available")), "under_offered": tq.get("under_offered", q.get("under_available")),
                    "feed_odds_type": q.get("feed_odds_type"), "source_updated_at": tq.get("source_updated_at") or q.get("updated_at"),
                    "observed_at": tq.get("observed_at") or q.get("board_at")}
    return out


def replay(rec):
    """(ok, detail): recompute the features, lambda and chance of a saved cm-1 record from its saved sums only."""
    if not rec or rec.get("ineligible") or rec.get("error"):
        return True, []
    r = score(rec["inputs"], rec["line"], rec["prop"])
    bad = [f"{k}: stored {rec.get(k)!r} replay {r[k]!r}" for k in ("lambda", "p_more", "lambda_kills", "hs_per_kill") if rec.get(k) != r[k]]
    if rec.get("features") != r["features"]:
        bad.append("features differ")
    if rec.get("artifact_sha") != ARTIFACT_SHA:
        bad.append(f"artifact {rec.get('artifact_sha')} != {ARTIFACT_SHA}")
    return not bad, bad


STATS = {"computed": 0, "ineligible": 0, "error": 0}


def status():
    m = _T.get("meta")
    if m is None and TABLES_META.exists():
        try:
            m = json.loads(TABLES_META.read_text())
        except (OSError, ValueError):
            m = None
    return {"version": VERSION, "artifact_sha": ARTIFACT_SHA, "tables": m, "counts": dict(STATS)}
