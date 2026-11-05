"""Steam detection (competitor spec section 3, 'Steam'; feature 4.5 SCOUT_STEAM, off by default).

Fire when the sharp fair chance moves >= MIN_MOVE (3 points) within a 5-15 minute window, confirmed by >= MIN_SOURCES
(2) independent sources moving the same way, while the pick'em line has NOT moved over that window. Pure arithmetic: the
caller hands in each source's fair-chance history and the pick'em line history; nothing is fetched or sent here.
"""
MIN_MOVE = 0.03
WINDOW_S = (5 * 60.0, 15 * 60.0)
MIN_SOURCES = 2


def _value_at(series, t):
    """The last value observed at or before t, or None. series: [(time, value)] in any order."""
    best = None
    for ts, v in series:
        if ts <= t and (best is None or ts >= best[0]):
            best = (ts, v)
    return None if best is None else best[1]


def source_move(series, now, window=WINDOW_S):
    """The largest move of one source between `now` and any observation 5-15 min before now -> (move, from, to, from_t)
    or None. The move is signed (to - from); the largest by size wins."""
    lo, hi = window
    cur = _value_at(series, now)
    if cur is None:
        return None
    best = None
    for ts, v in series:
        age = now - ts
        if lo <= age <= hi:
            mv = cur - v
            if best is None or abs(mv) > abs(best[0]):
                best = (mv, v, cur, ts)
    return best


def detect(sources, pickem_lines, now, min_move=MIN_MOVE, window=WINDOW_S, min_sources=MIN_SOURCES):
    """sources: {source name: [(time, fair chance of one side)]}; pickem_lines: [(time, line)] of the pick'em offer.

    -> {"fire": bool, "direction": +1 toward that side / -1 away / 0, "confirmed_by": [...], "moves": {...}, "reason"}.
    A pick'em line that changed within the window never fires (the app already moved)."""
    moves = {}
    for name, series in (sources or {}).items():
        m = source_move(series, now, window)
        if m is not None and abs(m[0]) >= min_move - 1e-12:
            moves[name] = {"move": m[0], "from": m[1], "to": m[2], "from_t": m[3], "ago_s": now - m[3]}
    out = {"fire": False, "direction": 0, "confirmed_by": [], "moves": moves, "reason": None}
    if not moves:
        out["reason"] = "no source moved enough"
        return out
    up = sorted(k for k, v in moves.items() if v["move"] > 0)
    down = sorted(k for k, v in moves.items() if v["move"] < 0)
    if len(up) >= min_sources and len(down) >= min_sources:
        out["reason"] = "sources disagree"
        return out
    agree, direction = (up, 1) if len(up) >= len(down) else (down, -1)
    if len(agree) < min_sources:
        out["reason"] = f"{len(agree)} source(s) moved; {min_sources} needed"
        return out
    start = min(moves[k]["from_t"] for k in agree)
    line_now = _value_at(pickem_lines, now)
    line_then = _value_at(pickem_lines, start)
    if line_now is None:
        out["reason"] = "no pick'em line"
        return out
    if any(start <= ts <= now and v != line_now for ts, v in pickem_lines) or (line_then is not None and line_then != line_now):
        out["reason"] = "the pick'em line already moved"
        return out
    out.update(fire=True, direction=direction, confirmed_by=agree, line=line_now)
    return out
