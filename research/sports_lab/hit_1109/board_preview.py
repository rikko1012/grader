"""hit-1109 board preview: summarise a saved live board JSON (GET /api/sports/board?league=..., read-only).

Graded quotes (x.gr present) per grade letter and platform, Over / Under lean split, and the top picks by printed chance.
  python -m research.sports_lab.hit_1109.board_preview <board.json> [top_n]
"""
from __future__ import annotations

import collections
import json
import sys


def summary(path, top=10):
    d = json.load(open(path, encoding="utf-8"))
    by_grade, by_plat, side, picks = collections.Counter(), collections.Counter(), collections.Counter(), []
    for m in d["markets"]:
        for q in m.get("q", []):
            x = q.get("x") or {}
            if not x.get("gr"):
                continue
            by_grade[x["gr"]] += 1
            by_plat[q["p"]] += 1
            side[x.get("ln")] += 1
            picks.append((x.get("pl") or 0, m["n"], m["st"], q["p"], q["l"], x.get("ln"), x["gr"]))
    picks.sort(reverse=True)
    n = sum(side.values())
    return {"league": d.get("league"), "built_at": d.get("built_at"), "markets": len(d["markets"]), "graded": n,
            "by_grade": dict(sorted(by_grade.items())), "by_platform": dict(by_plat.most_common()),
            "over": side.get("over", 0), "under": side.get("under", 0),
            "over_share": round(side.get("over", 0) / n, 3) if n else None,
            "top": [{"p": round(p, 3), "player": nm, "stat": st, "book": bk, "line": ln, "side": sd, "grade": gr}
                    for p, nm, st, bk, ln, sd, gr in picks[:top]]}


if __name__ == "__main__":
    print(json.dumps(summary(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10), indent=1))
