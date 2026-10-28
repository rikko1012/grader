"""P1 units joined to their forecast row (side, grade, units, raw chance, source) and the pick's static context (factors)."""
import json, os, collections
from ana import source_of, tier_of, load_ctx, ot_of, D


def units():
    p1 = json.load(open(os.path.join(D, "p1_units.json")))["rows"]
    fc = {r["id"]: r for r in json.load(open(os.path.join(D, "forecasts.json")))["rows"]}
    picks = {r["id"]: r for r in json.load(open(os.path.join(D, "picks.json")))["rows"]}
    teams, ranks = load_ctx()
    out, meta = [], collections.Counter()
    for u in p1:
        f = fc.get(u["forecast_id"])
        if f is None:
            meta["forecast row not in export"] += 1
        pk = picks.get((f or {}).get("pick_id")) if f else None
        if u["logged"] >= u["start"]:
            meta["logged after start"] += 1; continue
        p_over = float(u["p"])
        side = "Over" if p_over > 0.5 else "Under"
        p = p_over if side == "Over" else 1 - p_over
        hit = int(u["over"]) if side == "Over" else 1 - int(u["over"])
        grade = (f or {}).get("grade") or "LEAN"
        grade = grade if grade in ("A+", "A", "A-", "B+", "B", "B-") else "LEAN"
        fac = {}
        try: fac = json.loads((pk or {}).get("factors") or "{}")
        except Exception: pass
        det = fac.get("detail") or {}
        env = fac.get("environment") or {}
        sh = {}
        try: sh = json.loads((f or {}).get("shadow") or "{}")
        except Exception: pass
        raw = (sh.get("live_raw") or {}).get("p_more_no_push")
        src = (sh.get("old_spread") or {}).get("source") or source_of((pk or {}).get("flags"))
        src = {"perf-2": "perf-2 (HLTV N0)", "perf-2-bo3": "perf-2-bo3 (BO3.gg)"}.get(src, src if src in ("perf-cm2",) else source_of((pk or {}).get("flags")) if pk else "unknown")
        mid = int(u["match"][5:]) if u["match"].startswith("hltv:") else None
        day = u["start"][:10]
        out.append({"p": p, "hit": hit, "proj": u.get("lam"), "actual": u.get("actual"), "match": u["match"], "side": side,
                    "book": u["book"], "prop": u["prop"], "scope": u["scope"], "grade": grade, "units": u.get("units") or 0,
                    "line_type": (pk or {}).get("line_type") or "standard", "version": u["version"], "source": src,
                    "bo": ((det.get("map_prob_basis") or {}).get("format") or "unknown"), "p_win": det.get("p_win"),
                    "env": env.get("tonight") or "unknown", "day": day, "tier": tier_of(mid, day, teams, ranks),
                    "ot": ot_of((pk or {}).get("result")), "odds_src": ((det.get("market_effect") or {}).get("selected") or "none"),
                    "key": (pk or {}).get("key"), "player": (pk or {}).get("slug"), "line": u["line"],
                    "sd_reasons": det.get("sd_reasons") or [], "sample_maps": (pk or {}).get("sample_maps"), "id": u["forecast_id"],
                    "raw": raw, "p_over": p_over, "over": int(u["over"]), "challengers": u.get("challengers") or {},
                    "cands": u.get("cands"), "revision": u.get("revision"), "logged": u["logged"], "start": u["start"],
                    "pick_id": (f or {}).get("pick_id"), "market": (pk or {}).get("market")})
    meta["units"] = len(out)
    return out, dict(meta)
