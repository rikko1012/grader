"""
LoL slips from the saved pre-match forecasts (one forecast format - center/forecast.py).

Rules (owner, 2026-10-17):
- one sport and one platform per slip; modes "overs", "unders", "mixed"
- every leg's exact line and the chosen side are re-checked against the archive's latest state before a slip is offered
- no-overlap setting: unique props (player + stat) and unique matches across the WHOLE generated batch, not only within
  a slip; when there are not enough legs, the batch is shorter and the reason is stated - picks are never repeated to
  fill the count
- legs from the same series need a joint model of their dependence; the current projection has none, so such legs are
  never combined (the simulation model can supply joint outcomes only once it is the selected model)
- the entry payout (Underdog's published base table x each pick's payout modifier; PrizePicks' table is not public)
  is shown apart from the hit chances; the chance that every leg hits is a model estimate that treats legs from
  different series as independent - never a validated joint hit rate
- user preferences (e.g. no support props) are selection settings, not changes to any probability
- while stakes are held at 0U (projections not yet validated at real lines) no slip is RECOMMENDED; preview=True builds
  the same slips for the admin view, labelled as a preview
"""
import json
from datetime import datetime, timezone

from . import forecast, store

UD_BASE = {2: 3.5, 3: 6.5, 4: 12.0, 5: 20.0, 6: 35.0, 7: 65.0, 8: 120.0}   # Underdog standard entries (help center)


def _latest(con, platform):
    rows = con.execute("SELECT f.* FROM lol_forecasts f JOIN (SELECT platform, market_key, MAX(id) AS id FROM lol_forecasts "
                       "WHERE platform = ? GROUP BY platform, market_key) z ON z.id = f.id", (platform,)).fetchall()
    return [dict(r) for r in rows]


def candidates(con, platform, mode="mixed", exclude_roles=(), preview=False, now=None):
    """Legs that may enter a slip, best first, each with the reasons a leg was left out."""
    now = now or datetime.now(timezone.utc)
    legs, out = [], {}
    for f in _latest(con, platform):
        why = []
        side = f["model_side"]
        if mode == "overs" and side != "over":
            why.append("mode overs: the model's side is under")
        if mode == "unders" and side != "under":
            why.append("mode unders: the model's side is over")
        if f["role"] in exclude_roles:
            why.append(f"role {f['role']} excluded by the settings")
        reasons = json.loads(f["slip_reasons"] or "[]")
        hard = [r for r in reasons if not (preview and r.startswith("stake 0U"))]
        why += hard
        start = forecast._when(f["series_start"])
        if start and start <= now:
            why.append("series started")
        p = f["p_over"] if side == "over" else f["p_under"]
        if why:
            out[f["id"]] = why
            continue
        legs.append(dict(f, p_side=p))
    legs.sort(key=lambda f: -f["p_side"])
    return legs, out


def build(con, platform, size, count, mode="mixed", no_overlap=True, exclude_roles=(), preview=False, now=None):
    """-> {"slips": [...], "shortage": reason or None, "left_out": {...}, "recommended": bool}"""
    legs, left = candidates(con, platform, mode, exclude_roles, preview, now)
    used_props, used_matches, slips = set(), set(), []
    pool = list(legs)
    for _ in range(count):
        slip, sids, props = [], set(), set()
        for f in pool:
            prop = (f["pid"], f["stat"])
            if f["sid"] in sids or prop in props:
                continue                                    # same series twice: no dependence model -> never combined
            if no_overlap and (prop in used_props or f["sid"] in used_matches):
                continue
            slip.append(f)
            sids.add(f["sid"])
            props.add(prop)
            if len(slip) == size:
                break
        if len(slip) < size:
            break
        check = forecast.slip_check(con, [f["id"] for f in slip], now)
        if not preview and not check["eligible"]:
            break
        mods = []
        for f in slip:
            sides = json.loads(f["sides"] or "{}")
            k = "higher" if f["model_side"] == "over" else "lower"
            m = (sides.get(k) or {}).get("payout_multiplier")
            mods.append(float(m) if m else None)
        payout = None
        if platform == "underdog" and size in UD_BASE and all(m is not None for m in mods):
            payout = UD_BASE[size]
            for m in mods:
                payout *= m
        p_all = 1.0
        for f in slip:
            p_all *= f["p_side"]
        slips.append({"legs": [{"forecast_id": f["id"], "market_key": f["market_key"], "pid": f["pid"], "sid": f["sid"],
                                "stat": f["stat"], "scope": f["scope"], "line": f["line"], "side": f["model_side"],
                                "p_side": round(f["p_side"], 4), "grade": f["grade_label"]} for f in slip],
                      "entry_payout": round(payout, 3) if payout else None,
                      "entry_payout_basis": "Underdog standard table x pick payout modifiers" if payout else
                      ("PrizePicks payout table not public" if platform == "prizepicks" else "a leg's payout modifier is missing"),
                      "model_chance_all_hit": round(p_all, 4),
                      "chance_note": "model estimate; legs from different series treated as independent; not a validated joint hit rate",
                      "line_check": check})
        if no_overlap:
            used_props |= props
            used_matches |= sids
        else:
            pool = [f for f in pool if f not in slip] + slip      # rotate: the next slip starts from other legs
    shortage = None
    if len(slips) < count:
        shortage = (f"{len(slips)} of {count} slips of {size}: {len(legs)} eligible legs across "
                    f"{len({f['sid'] for f in legs})} series" + (" with unique props and matches across the batch" if no_overlap else "")
                    + "; picks are not repeated to fill the count")
    return {"platform": platform, "size": size, "mode": mode, "no_overlap": no_overlap, "preview": preview,
            "recommended": bool(slips) and not preview, "slips": slips, "shortage": shortage,
            "left_out": {str(k): v for k, v in left.items()}}


def member_summary(con, sizes=(2, 3), now=None):
    """What members may be offered: the RECOMMENDED slips (preview=False - every rule applies, the 0U stake policy
    included) per platform, or exactly why there are none, counted over the forecasts of series not yet started.
    Nothing is added to reach a count."""
    now = now or datetime.now(timezone.utc)
    out = {"at": store.now_iso(now.timestamp()), "platforms": {}}
    for pl in ("underdog", "prizepicks"):
        res = [build(con, pl, size, 5, "mixed", no_overlap=True, preview=False, now=now) for size in sizes]
        upcoming = [f for f in _latest(con, pl) if (forecast._when(f["series_start"]) or now) > now]
        reasons = {}
        for f in upcoming:
            for r in json.loads(f["slip_reasons"] or "[]"):
                reasons[r] = reasons.get(r, 0) + 1
        out["platforms"][pl] = {
            "recommended": [dict(slip, size=r["size"]) for r in res for slip in r["slips"]],
            "upcoming_forecasts": len(upcoming), "upcoming_series": len({f["sid"] for f in upcoming}),
            "blocked_by": dict(sorted(reasons.items(), key=lambda kv: -kv[1]))}
    return out
