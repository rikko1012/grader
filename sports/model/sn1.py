"""sn-1: sm-1 + nfl.snap_trend for NFL rec / rec_yds, in SHADOW (sports/research/REGISTER.md amendment A10).

Never displayed, never graded, never changes sm-1. The recorder runs inside the existing forward job (forward.forecast_league)
after sm-1 wrote the market, reads the sm-1 frame WITHOUT adding columns, and writes to <SPORTS_DATA_DIR>/model/shadow.db:
shadow_forecasts (model_version "sn-1") + sn1_inputs (the frozen base sm-1 PMF of the same row, eligibility, ds-1 ids).
Off switch: SPORTS_SHADOW_SN1=0. Any error -> the shadow is skipped for that market; sm-1 is untouched.

  python -m sports.model.sn1 fit <history.db>      artifact sn1_nfl.json + MANIFEST_SN1.json (same data_as_of as sm-1)
  python -m sports.model.sn1 status                counts + held-out progress (admin, never member-facing)
  python -m sports.model.sn1 heldout <history.db>  the one-time A10 scores when their thresholds are reached
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

from .. import config
from . import dists, evaluate, grade_policy, model, study

VERSION = "sn-1"
SPORT = "nfl"
MARKETS = ("rec", "rec_yds")
FEATURE = "nfl.snap_trend"
ART_DIR = Path(__file__).resolve().parent / "artifacts"
ARTIFACT = ART_DIR / "sn1_nfl.json"
MANIFEST = ART_DIR / "MANIFEST_SN1.json"
BOX_MIN_GAMES = 48                      # A10 4
BOOK_MIN = (150, 30)                    # A10 5: props, games
TARGETS = ("prizepicks", "underdog")
_ON = {"1", "true", "yes", "on"}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def enabled():
    return os.environ.get("SPORTS_SHADOW_SN1", "1").strip().lower() in _ON


# ------------------------------------------------------------------------------------------------------------- fit
def fit(hcon, as_of=None, out_dir=None):
    """A10 1: sm-1's frozen configuration + FEATURE, refit by forward.fit's recipe at sm-1's own data_as_of (default)."""
    from ..contracts import now_utc
    from . import datasets, forward
    from .fwd import _file_sha
    out_dir = Path(out_dir) if out_dir else ART_DIR
    cfg = json.loads((ART_DIR / "model_config_nfl.json").read_text(encoding="utf-8"))
    sm1 = forward.load_artifact(SPORT)
    as_of = as_of or sm1["data_as_of"]
    arts, cur, df = {}, None, None
    for mk in sorted(MARKETS, key=lambda m: tuple(cfg["markets"][m]["half_lives"])):
        c = cfg["markets"][mk]
        hl = tuple(c["half_lives"])
        if hl != cur:
            df = None
            df = study._nfl_fill(forward._frame_asof(SPORT, hcon, hl, as_of))
            cur = hl
        el = model.eligible(df, mk)
        tr = df[el & np.isfinite(df.f(f"y_{mk}"))]
        f = study.Fitted(mk, hl)
        study.mean_model(f, tr, tr, mk, SPORT, list(c["features"]) + [FEATURE])
        study.fit_family_params(f, tr, mk, SPORT)
        f.family, f.cal = c["family"], tuple(c["cal"])
        conf = f.config()
        conf.pop("mi_hist", None)
        conf["fit_rows"], conf["fit_games"] = int(len(tr)), int(len(np.unique(tr["game_id"])))
        arts[mk] = conf
    art = {"model_version": VERSION, "sport": SPORT, "fitted_at": now_utc(), "data_as_of": as_of,
           "pairs_with_sm1": sm1["sha"], "config_sha": _sha(cfg)[:16], "added_feature": FEATURE,
           "dataset": datasets.coverage(hcon).get(SPORT), "markets": arts,
           "role": "challenger (shadow) - REGISTER.md A10; not displayed, not graded"}
    art["sha"] = _sha(art["markets"])[:16]
    p = out_dir / ARTIFACT.name
    p.write_text(json.dumps(art, indent=1, sort_keys=True, default=float), encoding="utf-8")
    man = {"version": "sn1-pkg-1", "model_version": VERSION, "artifact_sha": art["sha"], "freeze_at": art["fitted_at"],
           "pairs_with_sm1": sm1["sha"], "data_as_of": as_of,
           "files": {p.name: {"sha256": _file_sha(p), "bytes": p.stat().st_size}},
           "held_out": "NFL games starting after the first recorded sn-1 row (A10 3); box once at >= 48 games, book "
                       "once per market at >= 150 props / 30 games"}
    (out_dir / MANIFEST.name).write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    return {"sha": art["sha"], "fitted_at": art["fitted_at"], "markets": {m: v["fit_rows"] for m, v in arts.items()}}


def load(art_path=None, man_path=None):
    """The frozen artifact if its file sha, content sha and manifest agree AND it pairs with the live sm-1; else None."""
    from . import forward
    from .fwd import _file_sha
    p, mp = Path(art_path or ARTIFACT), Path(man_path or MANIFEST)
    if not p.exists() or not mp.exists():
        return None
    man = json.loads(mp.read_text(encoding="utf-8"))
    a = json.loads(p.read_text(encoding="utf-8"))
    if (man.get("files", {}).get(p.name, {}).get("sha256") != _file_sha(p) or a.get("sha") != _sha(a["markets"])[:16]
            or man.get("artifact_sha") != a.get("sha")):
        return None
    sm1 = forward.load_artifact(SPORT)
    if sm1 is None or sm1.get("sha") != a.get("pairs_with_sm1"):     # sm-1 refit -> the pair is no longer exact
        return None
    a["freeze_at"] = man.get("freeze_at")
    return a


# ---------------------------------------------------------------------------------------------------------- record
def db(path=None):
    from . import shadow
    con = shadow.db(path)
    con.execute("""CREATE TABLE IF NOT EXISTS sn1_inputs (shadow_id TEXT PRIMARY KEY, ds1_game_id TEXT,
        ds1_player_id TEXT, eligible INTEGER, snap_trend REAL, sm1_mean REAL, sm1_pmf_json TEXT, sm1_p_over REAL,
        sm1_p_under REAL, sm1_p_push REAL)""")
    return con


class Recorder:
    """One per NFL forecast_league call; market() after sm-1 wrote that market. Reads the sm-1 frame, adds nothing."""

    def __init__(self, forecast_at, path=None, art=None):
        self.forecast_at = forecast_at
        self.art = art if art is not None else (load() if enabled() else None)
        self.written, self.skipped = 0, {}
        self.con = db(path) if self.art else None

    def active(self, market):
        return self.art is not None and market in self.art["markets"]

    def market(self, market, df, hl, rowpos, items, events, base_of, pmf_of):
        """items: [(offer record id, offer, (game_id, player_id), sm-1 chosen p)]; base_of {gk: frozen sm-1 PMF};
        pmf_of {gk: (pmf, mean, eligible)}."""
        if not self.active(market) or not items:
            return 0
        from .forward import _row_dict, pmf_from_row
        am = self.art["markets"][market]
        if tuple(am["half_lives"]) != tuple(hl):
            self.skipped[market] = "half-lives differ from the sm-1 frame"
            return 0
        try:
            lo, ks = dists.grid(market)
            pm, n = {}, 0
            for rid, o, gk, _ in items:
                if gk not in pm:
                    row = {k: v for k, v in _row_dict(df, rowpos[gk]).items() if not k.startswith("F_")}
                    pmf, mean = pmf_from_row(am, row, market, SPORT)
                    l3, ew = row.get("l3_snap"), row.get("ew_offense_pct")
                    st = None if l3 is None or ew is None else float(l3) - float(ew)
                    base = base_of[gk]
                    pm[gk] = (pmf, mean, st, base, float(dists.mean_of(base[None, :], ks)[0]), bool(pmf_of[gk][2]))
                pmf, mean, st, base, bmean, elig = pm[gk]
                po, pu, pp = dists.line_probs(pmf, lo, o.line)
                b_po, b_pu, b_pp = dists.line_probs(base, lo, o.line)
                pol = grade_policy.apply(po, pu, o.offered_sides(), "unvalidated")
                ev = events.get(o.event_id)
                sid = _sha([VERSION, self.art["sha"], rid, self.forecast_at])[:24]
                sm1_side = (b_po if pol["chosen_side"] == "over" else b_pu) if pol["chosen_side"] else None
                self.con.execute("INSERT OR IGNORE INTO shadow_forecasts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                                 "?,?,?,?,?)",
                                 (sid, VERSION, self.art["sha"], SPORT, market, o.event_id, o.player_id, rid, o.platform,
                                  o.line, self.forecast_at, ev.start_utc if ev else None, mean, po, pu, pp,
                                  ",".join(o.offered_sides()), pol["chosen_side"], pol.get("chosen_p"), pol["band"],
                                  json.dumps({FEATURE: st}, default=float), int(lo),
                                  json.dumps([round(float(x), 12) for x in pmf]), sm1_side))
                self.con.execute("INSERT OR IGNORE INTO sn1_inputs VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (sid, gk[0], gk[1], int(elig), st, bmean,
                                  json.dumps([round(float(x), 12) for x in base]), b_po, b_pu, b_pp))
                n += 1
            self.con.commit()
            self.written += n
            return n
        except Exception as e:                                       # noqa: BLE001 - shadow never breaks sm-1
            self.skipped[market] = f"{type(e).__name__}: {str(e)[:160]}"
            return 0

    def close(self):
        if self.con is not None:
            self.con.close()
        return {"model_version": VERSION, "artifact": (self.art or {}).get("sha"), "written": self.written,
                "skipped": self.skipped, "enabled": enabled(), "markets": sorted((self.art or {}).get("markets", {}))}


# --------------------------------------------------------------------------------------------------------- held-out
def heldout_path():
    return config.DATA_DIR / "model" / "heldout_sn1.json"


def _rows(hcon, shadow_path=None):
    """Last pre-start sn-1 forecast per (game, player, stat, line) on target books, game final with a box score."""
    p = Path(shadow_path) if shadow_path else config.DATA_DIR / "model" / "shadow.db"
    if not p.exists():
        return [], None
    con = db(p)
    first = con.execute("SELECT MIN(forecast_at) FROM shadow_forecasts WHERE model_version = ?", (VERSION,)).fetchone()[0]
    q = ("SELECT f.stat, f.line, f.platform, f.forecast_at, f.event_start, f.p_over, f.p_under, f.p_push, f.pmf_lo, "
         "f.pmf_json, i.ds1_game_id, i.ds1_player_id, i.eligible, i.sm1_pmf_json, i.sm1_p_over, i.sm1_p_under, "
         "i.sm1_p_push FROM shadow_forecasts f JOIN sn1_inputs i ON i.shadow_id = f.shadow_id WHERE "
         "f.model_version = ? AND f.forecast_at < f.event_start")
    raw = con.execute(q, (VERSION,)).fetchall()
    con.close()
    last = {}
    for r in raw:
        if r[2] not in TARGETS or (first and (r[4] or "") <= first):          # A10 3: games after the first row
            continue
        k = (r[10], r[11], r[0], float(r[1]))
        if k not in last or r[3] > last[k][3]:
            last[k] = r
    final = {g for (g,) in hcon.execute("SELECT game_id FROM games WHERE league = 'nfl' AND status = 'final'")}
    col = {"rec": "rec", "rec_yds": "rec_yds"}
    out = []
    for k, r in last.items():
        if k[0] not in final:
            continue
        y = hcon.execute(f"SELECT {col[k[2]]} FROM nfl_pg WHERE game_id = ? AND player_id = ?", (k[0], k[1])).fetchone()
        if not y or y[0] is None:
            continue
        out.append({"game": k[0], "player": k[1], "market": k[2], "line": k[3], "y": float(y[0]), "eligible": bool(r[12]),
                    "lo": int(r[8]), "pmf": np.array(json.loads(r[9])), "sm1_pmf": np.array(json.loads(r[13])),
                    "p_over": r[5], "p_under": r[6], "p_push": r[7], "sm1_p_over": r[14], "sm1_p_under": r[15],
                    "sm1_p_push": r[16]})
    return out, first


def box_score(rows):
    """A10 4: unique (game, player) sm-1-eligible rows; paired CRPS sn-1 - sm-1, game-clustered CI."""
    uniq = {}
    for r in rows:
        if r["eligible"]:
            uniq[(r["game"], r["player"])] = r
    if not uniq:
        return {"rows": 0}
    rs = list(uniq.values())
    lo = rs[0]["lo"]
    y = np.array([r["y"] for r in rs])
    g = np.array([r["game"] for r in rs], dtype=object)
    c2 = dists.crps(np.stack([r["pmf"] for r in rs]), lo, y)
    c1 = dists.crps(np.stack([r["sm1_pmf"] for r in rs]), lo, y)
    d = evaluate.boot_mean(c2 - c1, g)
    return {"rows": len(rs), "games": int(len(set(g))), "crps_sn1": float(c2.mean()), "crps_sm1": float(c1.mean()),
            "crps_sn1_minus_sm1": {"mean": d[0], "ci_game": [d[1], d[2]]},
            "pass": bool(d[2] is not None and d[2] < 0)}


def book_score(rows):
    """A10 5: every recorded offer at its own line, pushes excluded; paired logloss of P(over | no push)."""
    rs = [r for r in rows if r["y"] != r["line"]]
    if not rs:
        return {"props": 0}
    g = np.array([r["game"] for r in rs], dtype=object)
    hit = np.array([r["y"] > r["line"] for r in rs], float)

    def cond(po, pp):
        return np.clip(np.array(po, float) / np.maximum(1 - np.array(pp, float), 1e-9), 1e-6, 1 - 1e-6)
    p2 = cond([r["p_over"] for r in rs], [r["p_push"] for r in rs])
    p1 = cond([r["sm1_p_over"] for r in rs], [r["sm1_p_push"] for r in rs])
    ll = lambda p: -(hit * np.log(p) + (1 - hit) * np.log(1 - p))                  # noqa: E731
    d = evaluate.boot_mean(ll(p2) - ll(p1), g)

    def side(p):
        over = p > 0.5
        res = {"share_over": float(over.mean())}
        for nm, m, h in (("over", over, hit), ("under", ~over, 1 - hit)):
            b = evaluate.boot_mean(h[m], g[m]) if m.any() else (None, None, None)
            res[f"hit_{nm}"] = {"n": int(m.sum()), "mean": b[0], "ci_game": [b[1], b[2]]}
        return res
    return {"props": len(rs), "games": int(len(set(g))), "over_rate": float(hit.mean()),
            "logloss_sn1": float(ll(p2).mean()), "logloss_sm1": float(ll(p1).mean()),
            "logloss_sn1_minus_sm1": {"mean": d[0], "ci_game": [d[1], d[2]]},
            "fail": bool(d[1] is not None and d[1] > 0),
            "sides_sn1": side(p2), "sides_sm1": side(p1),
            "reliability_sn1": evaluate.reliability(p2, hit, min_n=20)}


def progress(hcon, shadow_path=None):
    """Forward job: counts only, no PMF is read and nothing is scored (A10 3: no interim looks). Counts are upper
    bounds (pushes / missing box rows are dropped only by the one-time score)."""
    p = Path(shadow_path) if shadow_path else config.DATA_DIR / "model" / "shadow.db"
    done = json.loads(heldout_path().read_text(encoding="utf-8")) if heldout_path().exists() else {}
    if not p.exists():
        return {"first_recorded": None, "games": 0, "scored": sorted(done)}
    con = db(p)
    first = con.execute("SELECT MIN(forecast_at) FROM shadow_forecasts WHERE model_version = ?", (VERSION,)).fetchone()[0]
    keys = con.execute("SELECT DISTINCT i.ds1_game_id, f.stat, i.ds1_player_id, f.line FROM shadow_forecasts f JOIN "
                       "sn1_inputs i ON i.shadow_id = f.shadow_id WHERE f.model_version = ? AND f.forecast_at < "
                       "f.event_start AND f.event_start > ? AND f.platform IN ('prizepicks', 'underdog')",
                       (VERSION, first or "")).fetchall()
    con.close()
    gids = sorted({k[0] for k in keys})
    final = set()
    for i in range(0, len(gids), 500):
        part = gids[i:i + 500]
        final |= {g for (g,) in hcon.execute(f"SELECT game_id FROM games WHERE league = 'nfl' AND status = 'final' "
                                             f"AND game_id IN ({','.join('?' * len(part))})", part)}
    keys = [k for k in keys if k[0] in final]
    per = {mk: {"props": sum(1 for k in keys if k[1] == mk), "games": len({k[0] for k in keys if k[1] == mk})}
           for mk in MARKETS}
    games = len({k[0] for k in keys})
    return {"first_recorded": first, "games": games, "box_threshold": BOX_MIN_GAMES,
            "box_due": games >= BOX_MIN_GAMES and "box" not in done, "book": per, "book_threshold": BOOK_MIN,
            "scored": sorted(k for k in done if k != "decision")}


def heldout(hcon, shadow_path=None, now=None):
    """The one-time A10 scores: box once at >= 48 games; each market's book once at >= 150 props / 30 games. A part
    that was scored is never re-scored. Then the per-market decision (A10 6) - owner decides any switch."""
    from ..contracts import now_utc
    p = heldout_path()
    done = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    rows, first = _rows(hcon, shadow_path)
    if "box" not in done and len({r["game"] for r in rows}) >= BOX_MIN_GAMES:
        done["box"] = {"scored_at": now or now_utc(), "first_recorded": first,
                       "markets": {mk: box_score([r for r in rows if r["market"] == mk]) for mk in MARKETS}}
    for mk in MARKETS:
        rm = [r for r in rows if r["market"] == mk and r["y"] != r["line"]]
        if mk not in done.get("book", {}) and len(rm) >= BOOK_MIN[0] and len({r["game"] for r in rm}) >= BOOK_MIN[1]:
            done.setdefault("book", {})[mk] = {"scored_at": now or now_utc(), **book_score(rm)}
    dec = {}
    for mk in MARKETS:
        bx = (done.get("box") or {}).get("markets", {}).get(mk)
        bk = (done.get("book") or {}).get(mk)
        dec[mk] = ("waiting" if bx is None or bk is None else
                   "pass - owner decision to switch (A10 6)" if bx.get("pass") and not bk.get("fail") else
                   "fail - retired for this market (A10 6)")
    done["decision"] = dec
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(done, indent=1, default=float), encoding="utf-8")
    return done


def status(path=None):
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None
    out = {"model_version": VERSION, "enabled": enabled(), "manifest": man, "display": "never (shadow only)",
           "loaded": load() is not None, "counts": {}}
    p = Path(path) if path else config.DATA_DIR / "model" / "shadow.db"
    if p.exists():
        con = db(p)
        for st, n, ev in con.execute("SELECT stat, COUNT(*), COUNT(DISTINCT event_id) FROM shadow_forecasts WHERE "
                                     "model_version = ? GROUP BY 1", (VERSION,)):
            out["counts"][st] = {"forecasts": n, "events": ev}
        con.close()
    return out


def main(argv=None):
    import argparse
    import sqlite3
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    ap = argparse.ArgumentParser(prog="python -m sports.model.sn1")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("fit", "heldout"):
        s = sub.add_parser(c)
        s.add_argument("history")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(), indent=1, default=str))
        return
    hcon = sqlite3.connect(f"file:{Path(a.history).as_posix()}?mode=ro", uri=True)
    hcon.row_factory = sqlite3.Row
    res = fit(hcon) if a.cmd == "fit" else heldout(hcon)
    print(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    main()
