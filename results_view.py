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


def member_results(export, B=1000, seed=7):
    rows = (export or {}).get("rows") or []
    excl = (export or {}).get("excluded") or {}
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
    starts = sorted(r["start"] for r in rows if r.get("start"))
    by_stat = [_line([x for x in picks if (x["prop"], x["scope"]) == k], stat=STAT.get(k[0], k[0]), scope=SCOPE.get(k[1], k[1]))
               for k in sorted({(x["prop"], x["scope"]) for x in picks})]
    vers = collections.defaultdict(list)
    for x in picks:
        vers[x["version"]].append(x)
    by_version = sorted((_line(v, version=k, first=min(x["start"] for x in v)[:10], last=max(x["start"] for x in v)[:10])
                         for k, v in vers.items()), key=lambda d: d["first"], reverse=True)
    return {"as_of": (export or {}).get("at"),
            "rule": f"The favoured side when its printed chance was {int(RULE * 100)}% or more",
            "definition": "Lines the board listed on a standard Underdog or PrizePicks line, graded at least 30 minutes before "
                          "the match; one result per player, match, stat and maps (refreshes and the other platform's copy are "
                          "not counted twice); settled on official stats. Lines typed in by members and goblin / demon lines "
                          "are not included.",
            "lines": {"settled": len(rows), "matches": len({r['match'] for r in rows}), "first": starts[0][:10] if starts else None,
                      "last": starts[-1][:10] if starts else None, "pushes": excl.get("push on the board line", 0)},
            "picks": _line(picks, B, seed),
            "by_side": [_line([x for x in picks if x["side"] == s], side=s) for s in ("Over", "Under")],
            "by_stat": by_stat,
            "by_chance": [_line([x for x in picks if lo <= x["printed"] < hi], band=lab) for lo, hi, lab in BANDS],
            "by_version": by_version,
            "note": "The printed chance is the model's estimate at grading time; the observed rate is how often those picks "
                    "won. No returns are shown: a single prop has no payout of its own."}
