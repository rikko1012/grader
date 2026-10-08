"""Which maps a prop line covers - ONE shared, scope-aware definition (2026-10-08 correctness incident).

A Map 1 line must only ever be compared with the player's Map 1 total, Maps 1-2 with maps 1 and 2, Maps 1-3 with
every map actually played in the series (map 3 only exists after a 1-1). Before this module, an admin re-check
re-settled 44 Map 1 picks with their Maps 1-2 totals, and the card / pick log compared Maps 1-2 records with Map 1
lines. Everything that turns per-map numbers into a total for a line goes through scope_total().

Rules (never guess): incomplete, duplicated or ambiguous map data -> None (shown as a data error / left unsettled),
never a total from another scope.
"""

SCOPES = ("1", "12", "123")


def scope_total(maps, scope, stat):
    """maps: [{"number": int, "kills": int|None, "headshots": int|None, "won": bool|None}, ...] for ONE series, any order.
    stat: "kills" | "hs". Returns (total, map_numbers_used) or (None, reason)."""
    if scope not in SCOPES:
        return None, f"unknown scope {scope!r}"
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
        w1, w2 = by[1].get("won"), by[2].get("won")
        if w1 is None or w2 is None:
            return None, "map results unknown - can't tell whether map 3 is played"
        if w1 != w2:                                  # 1-1: map 3 decides the series
            if 3 not in by:
                return None, "series at 1-1 but map 3 missing"
            use = [1, 2, 3]
        else:
            use = [1, 2]                               # 2-0: no map 3 (a listed map 3 would be bad data)
            if 3 in by:
                return None, "map 3 listed after a 2-0"
    if any(n not in by for n in use):
        return None, f"map {[n for n in use if n not in by]} missing"
    vals = [by[n].get(key) for n in use]
    if any(v is None for v in vals):
        return None, f"{key} missing on a required map"
    return sum(vals), use


def outcome(total, line, side):
    """('push'|'settled', hit) for a total vs the line and the picked side ('More'/'Less')."""
    if total == line:
        return "push", None
    return "settled", int(total > line if side == "More" else total < line)
