"""Which maps a prop line covers - ONE shared, scope-aware definition (2026-10-08 correctness incident).

A Map 1 line must only ever be compared with the player's Map 1 total, Maps 1-2 with maps 1 and 2, Maps 1-3 with maps
1, 2 and 3 - never maps 4-5 (2026-10-11: the map scope, not the match format. A BO5 always plays maps 1-3, so a 2-0 after
two maps still has a map 3; a BO3 that ends 2-0 never plays map 3, and Underdog voids Maps 1-3 picks then - "a player
must play in each map of their selection"). Before this module, an admin re-check
re-settled 44 Map 1 picks with their Maps 1-2 totals, and the card / pick log compared Maps 1-2 records with Map 1
lines. Everything that turns per-map numbers into a total for a line goes through scope_total().

Rules (never guess): incomplete, duplicated or ambiguous map data -> None (shown as a data error / left unsettled),
never a total from another scope.
"""

import math
from numbers import Real

SCOPES = ("1", "12", "123")
MAP3_NOT_PLAYED = "map 3 not played (series ended 2-0) - Maps 1-3 picks void"


def scope_total(maps, scope, stat, best_of=None):
    """maps: [{"number": int, "kills": int|None, "headshots": int|None, "won": bool|None}, ...] for ONE series, any order.
    stat: "kills" | "hs". Returns (total, map_numbers_used) or (None, reason)."""
    if scope not in SCOPES:
        return None, f"unknown scope {scope!r}"
    if stat not in ("kills", "hs"):
        return None, f"unknown stat {stat!r}"
    key = "headshots" if stat == "hs" else "kills"
    nums = [m.get("number") for m in maps]
    if any(n is None for n in nums) or len(set(nums)) != len(nums):
        return None, "missing or duplicated map numbers"
    by = {m["number"]: m for m in maps}
    if scope == "1":
        use = [1]
    elif scope == "12":
        use = [1, 2]
    else:
        if 1 not in by or 2 not in by:
            return None, "maps 1-2 not both played"
        if 3 not in by:
            w1, w2 = by[1].get("won"), by[2].get("won")
            if best_of == 3 and w1 is not None and w1 == w2:
                return None, MAP3_NOT_PLAYED          # a BO3 over 2-0: the Maps 1-3 pick voids
            return None, "map 3 missing"               # 1-1 / unknown: map 3 not in yet
        use = [1, 2, 3]                                # maps 1, 2 and 3 only - BO5 maps 4-5 never count
    if any(n not in by for n in use):
        return None, f"map {[n for n in use if n not in by]} missing"
    vals = [by[n].get(key) for n in use]
    if any(v is None for v in vals):
        return None, f"{key} missing on a required map"
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) or v < 0 or int(v) != v for v in vals):
        return None, f"invalid {key} on a required map"
    return sum(vals), use


def outcome(total, line, side):
    """('push'|'settled', hit) for a total vs the line and the picked side ('More'/'Less')."""
    if side not in ("More", "Less"):
        raise ValueError("outcome requires a More or Less side")
    if any(isinstance(x, bool) or not isinstance(x, Real) or not math.isfinite(x) for x in (total, line)):
        raise ValueError("outcome requires finite numeric total and line")
    if total == line:
        return "push", None
    return "settled", int(total > line if side == "More" else total < line)
