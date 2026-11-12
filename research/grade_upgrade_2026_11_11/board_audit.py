"""Live-board audit + before/after diff for awp-hs-1 (read-only; board JSON saved by a GET of /api/board per platform).
usage: python board_audit.py <board.json>                 audit one snapshot
       python board_audit.py <before.json> <after.json>   audit the second and diff it against the first"""
import json, os, sys
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import grader


def rows(path):
    d = json.load(open(path))
    return {r["id"]: r for bk in ("underdog", "prizepicks") for r in (d.get(bk) or {}).get("rows") or []}, d


def audit(R):
    c = Counter(); bad = []
    for r in R.values():
        if r.get("p") is None or r.get("side") not in ("More", "Less"):
            c["no grade (state %s)" % r.get("state")] += 1; continue
        c["graded"] += 1
        p = r["p"]; u = r.get("units") or 0.0
        mo = r.get("model_outcomes") or {}
        if not (0.5 <= p <= 1.0):
            bad.append(("p out of range", r["id"]))
        if mo.get("over") is not None and mo.get("under") is not None:
            big = "More" if mo["over"] >= mo["under"] else "Less"
            if big != r["side"] and abs(mo["over"] - mo["under"]) > 1e-9:
                bad.append(("side is not the larger chance", r["id"], r["player"]))
        letter = grader.grade_from_p(p)
        if (r.get("model_grade") or r.get("grade")) not in (letter, "LEAN" if letter == "C" else letter, "C" if letter == "C" else letter):
            # the shown chance is rounded to 3 decimals: a letter may sit one band away only at a band edge
            edge = min(abs(p - b) for b in (0.55, 0.57, 0.63, 0.68, 0.74, 0.80))
            (c.update(["letter at a rounding edge"]) if edge <= 0.0006 else bad.append(("grade != chance", r["id"], r["player"], p, r.get("model_grade"))))
        free = grader.units_for_p(p)
        if u > free + 1e-9:
            bad.append(("units above what the chance earns", r["id"], r["player"], p, u))
        if u < free - 1e-9 and u > 0 and not (r.get("cap_reason") or r.get("stake_reason")) and r.get("market") == "HARD" \
                and min(abs(p - b) for b in (0.55, 0.63, 0.68, 0.74, 0.80)) > 0.0006:
            bad.append(("units below the chance with no cap reason", r["id"], r["player"], p, u))
        if u == 0 and free > 0 and not r.get("stake_reason") and min(abs(p - b) for b in (0.55,)) > 0.0006 \
                and not (r["side"] == "Less" and r.get("under_available") is False) and not (r["side"] == "More" and r.get("over_available") is False):
            bad.append(("graded 55%+ but 0U with no reason", r["id"], r["player"], p))
        if u > 0:
            c["bets"] += 1; c["bets " + ("Over" if r["side"] == "More" else "Under")] += 1
        else:
            c["0U"] += 1
        if r.get("stake_reason"):
            c["0U reason: " + r["stake_reason"]] += 1
        if r.get("prop") == "hs" and r.get("awp"):
            c["AWPer HS rows"] += 1
            c["AWPer HS rows staked"] += u > 0
    return dict(c), bad


def main():
    A, da = rows(sys.argv[-1])
    c, bad = audit(A)
    print("AUDIT", sys.argv[-1], "| versions", dict(Counter((da.get(b) or {}).get("version") for b in ("underdog", "prizepicks"))),
          "| graded_model", dict(Counter(r.get("graded_model") for r in A.values())))
    print(json.dumps(c, indent=1)); print("problems", len(bad))
    for b in bad[:40]:
        print("  ", b)
    if len(sys.argv) > 2:
        B, _ = rows(sys.argv[1])
        both = [k for k in A if k in B and B[k].get("line") == A[k].get("line")]
        ch = Counter(); stakes = []
        for k in both:
            b, a = B[k], A[k]
            awp_hs = a.get("prop") == "hs" and a.get("awp")
            same_grade_time = False
            for f, tol in (("side", None), ("model_grade", None), ("p", 0.0)):
                if b.get(f) != a.get(f):
                    ch[f + (" (AWPer HS)" if awp_hs else " (other rows: regraded with new data)")] += 1
            if (b.get("units") or 0) != (a.get("units") or 0):
                ch["units " + ("AWPer HS" if awp_hs else "other")] += 1
                stakes.append((a["player"], a["book"], a["prop"], a["scope"], a["line"], "Over" if a["side"] == "More" else "Under",
                               a["p"], a.get("model_grade"), b.get("units") or 0, a.get("units") or 0, b.get("stake_reason"),
                               a.get("stake_reason"), awp_hs, b.get("p") == a.get("p") and b.get("side") == a.get("side")))
        print("\nDIFF rows on both boards at the same line:", len(both), "| before", len(B), "after", len(A))
        print(json.dumps(dict(ch), indent=1))
        print("stake changes (player, app, prop, maps, line, side, chance, letter, before U, after U, before reason, after reason, AWPer HS, same chance+side):")
        for s in sorted(stakes, key=lambda s: (not s[12], s[0])):
            print("  ", s)
        gate = [(a["player"], a["book"], a["line"], a["p"], a.get("units"), a.get("stake_reason")) for a in A.values() if a.get("prop") == "hs" and a.get("awp")]
        print("AWPer HS rows after:", len(gate), "| still", grader.AWP_HS_GATE_REASON, ":", sum(1 for g in gate if g[5] == grader.AWP_HS_GATE_REASON))


if __name__ == "__main__":
    main()
