"""Member Results view (2026-10-18 release brief, section 6): a concise read of the EXISTING immutable P1 scorecard export
(learning.p1_units_export - built by the learning cycle from forecasts saved before each match; nothing is recomputed,
regraded or backfilled here). Pure functions: server.py serves member_results() from the stored export, cached.

What a member sees, and only this: picks the board listed on a standard Underdog / PrizePicks line, graded at least
30 minutes before the start (the latest such grade - one per player x match x stat x maps, so refreshes and the other
platform's copy are not counted twice), settled on official stats; the favoured side when its printed chance was 57%+
(the registered pick rule); wins / losses with pushes counted separately; the printed chance next to the observed rate,
with a 95% interval that resamples whole matches. No returns: a single prop has no payout of its own."""
import collections
import random

RULE = 0.57
# owner 2026-10-25: "get rid of map 1's on the result page until we have 500 stakes to go off of". Map 1 (scope "1") rows
# and every aggregate built from them stay off the member Results view until Map 1 has this many settled STAKED P1 units
# (units > 0 at the unit's own P1 forecast; one per player x match x stat x maps - the export's own dedupe), then they come
# back on their own. Display only: grading, the board, the stored export and admin diagnostics are untouched.
MAP1_RESULTS_MIN_STAKED = 500
BANDS = ((0.57, 0.60, "57–60%"), (0.60, 0.65, "60–65%"), (0.65, 0.70, "65–70%"), (0.70, 1.01, "70% and up"))
SCOPE = {"12": "Maps 1–2", "1": "Map 1", "123": "Maps 1–3"}
STAT = {"kills": "Kills", "hs": "Headshots"}


def _ci(picks, B, seed):
    g = collections.defaultdict(list)
    for x in picks:
        g[x["match"]].append(x["win"])
    ks = list(g)
    if len(ks) < 5:
        return None
    rnd, v = random.Random(seed), []
    for _ in range(B):
        s = [w for k in (rnd.choice(ks) for _ in ks) for w in g[k]]
        v.append(sum(s) / len(s))
    v.sort()
    return [round(v[int(.025 * B)], 4), round(v[int(.975 * B) - 1], 4)]


def _line(picks, B=0, seed=7, **extra):
    if not picks:
        return dict(extra, picks=0)
    w = sum(x["win"] for x in picks)
    out = dict(extra, picks=len(picks), matches=len({x["match"] for x in picks}), wins=w, losses=len(picks) - w,
               printed=round(sum(x["printed"] for x in picks) / len(picks), 4), observed=round(w / len(picks), 4))
    if B:
        out["observed_ci"] = _ci(picks, B, seed)
    return out


def map1_staked_settled(export):
    """Settled P1 units on Map 1 (scope "1") with a stake (units > 0) - the scored rows plus the pushed units, each unit
    once (the export is already one per player x match x stat x maps; a forecast id is never counted twice)."""
    seen, n = set(), 0
    for r in ((export or {}).get("rows") or []) + ((export or {}).get("pushes") or []):
        if (r.get("scope") or "12") != "1" or not (r.get("units") or 0) > 0:
            continue
        k = r.get("forecast_id")
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        n += 1
    return n


def map1_status(export, min_staked=MAP1_RESULTS_MIN_STAKED):
    """Admin: Map 1's settled staked count against the threshold and whether member Results show Map 1."""
    n = map1_staked_settled(export)
    return {"staked_settled": n, "threshold": min_staked, "shown_on_member_results": n >= min_staked,
            "as_of": (export or {}).get("at")}


def member_results(export, B=1000, seed=7, map1_min=MAP1_RESULTS_MIN_STAKED):
    rows = (export or {}).get("rows") or []
    excl = dict((export or {}).get("excluded") or {})
    pushes_in = (export or {}).get("pushes") or []
    hide_map1 = map1_staked_settled(export) < map1_min
    if hide_map1:                               # left out entirely: every total below is recomputed without Map 1
        m1 = lambda r: (r.get("scope") or "12") == "1"
        rows = [r for r in rows if not m1(r)]
        k = "push on the board line"
        if k in excl:
            excl[k] = max(0, excl[k] - sum(1 for r in pushes_in if m1(r)))
        pushes_in = [r for r in pushes_in if not m1(r)]
    picks = []
    for r in rows:
        p = r.get("p")
        if p is None or r.get("over") not in (0, 1):
            continue
        side_p = max(p, 1 - p)
        if side_p < RULE:
            continue
        picks.append({"match": r["match"], "printed": side_p, "win": int(r["over"] if p >= 0.5 else 1 - r["over"]),
                      "side": "Over" if p >= 0.5 else "Under", "prop": r.get("prop"), "scope": r.get("scope") or "12",
                      "version": r.get("version") or "untagged", "start": r.get("start"), "logged": r.get("logged")})
    pushed = 0
    for r in pushes_in:                                 # owner brief 2026-11-02: a push is reported apart, never a win
        p = r.get("p")
        if p is None or max(p, 1 - p) < RULE:
            continue
        pushed += 1
    starts = sorted(r["start"] for r in rows if r.get("start"))
    by_stat = [_line([x for x in picks if (x["prop"], x["scope"]) == k], stat=STAT.get(k[0], k[0]), scope=SCOPE.get(k[1], k[1]))
               for k in sorted({(x["prop"], x["scope"]) for x in picks})]
    vers = collections.defaultdict(list)
    for x in picks:
        vers[x["version"]].append(x)
    by_version = sorted((_line(v, version=k, first=min(x["start"] for x in v)[:10], last=max(x["start"] for x in v)[:10])
                         for k, v in vers.items()), key=lambda d: d["first"], reverse=True)
    return {"as_of": (export or {}).get("at"),
            "rule": f"The favoured side when its printed chance was {round(RULE * 100)}% or more",
            "definition": "Lines the board listed on a standard Underdog or PrizePicks line, graded at least 30 minutes before "
                          "the match; one result per player, match, stat and maps (refreshes and the other platform's copy are "
                          "not counted twice); settled on official stats. Lines typed in by members and goblin / demon lines "
                          "are not included." + (" Map 1 lines are not included." if hide_map1 else ""),
            "lines": {"settled": len(rows), "matches": len({r['match'] for r in rows}), "first": starts[0][:10] if starts else None,
                      "last": starts[-1][:10] if starts else None, "pushes": excl.get("push on the board line", 0)},
            "picks": dict(_line(picks, B, seed), pushes=pushed),
            "by_side": [_line([x for x in picks if x["side"] == s], side=s) for s in ("Over", "Under")],
            "by_stat": by_stat,
            "by_chance": [_line([x for x in picks if lo <= x["printed"] < hi], band=lab) for lo, hi, lab in BANDS],
            "by_version": by_version,
            "note": "The printed chance is the model's estimate at grading time; the observed rate is how often those picks "
                    "won; pushes are shown apart and left out of the rate. No returns are shown: a single prop has no payout of its own."}
