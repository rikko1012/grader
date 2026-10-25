"""Scout Sports SHEETS (sh-1): the PrizePicks sheet, the Underdog sheet and the Most-consistent sheet - one dense row per
current pick'em line, built in the WORKER from the UI snapshot files (sports/snapshots.py players/ + history/), never in
a member request. Served by sports/api_sheets.py as plain JSON bytes.

    python -m sports.sheets build [nba nfl]        # job "sheets" (sports/scheduler.py), also builds without + results

Files: <snapshots>/<league>/sheets/prizepicks.json, underdog.json, consistent.json (atomic writes).

DEFINITIONS (every number traces to a snapshot record; nothing is estimated here)
  rows        every OPEN line of that platform on a game that has not started: the ordinary line(s) (standard /
              balanced / main) AND the platform's goblin / demon / alternate lines, each with its own variant. Promotional
              lines are left out (not a normal line). Kalshi and sportsbooks are never rows (they are the market).
  Scout %     Over / Under / Push at THAT exact line, unconditional, from ONE distribution per market: the row's chosen
              forecast (the forecast on this platform's own quote first, then any quote's, then the board's line read).
              Its stored line = the stored chances; another line = the same forecast's ladder (its own distribution) or
              its saved PMF (sports/forecast_detail.line_probs). No distribution readable at a line -> blank, never a guess.
  market %    the board's market estimate (mv-1 display of the mc-1 rule) at that line, excluding the platform itself;
              only books with a usable price enter it (books without odds are not shown). Goblin / demon lines have no
              market estimate (books do not list those lines) -> blank.
  edge        probability POINTS = Scout chance minus market chance for the same side at the same line. Not expected
              value, not ROI.
  lean        "over" only when P(over) > P(under) AND the projection (mean, else median) is above the line; "under"
              symmetric; the side must be offered on that line. Otherwise no lean - a lean never contradicts the
              projection or the distribution.
  L10         the player's 10 most recent games he PLAYED (history file, box score beats game log) at the CURRENT line:
              over = value > line, under = value < line, push = equal (strict). `seasons` > 1 = the window crosses a
              season break (shown).
CONSISTENT (cs-1) - history, not a prediction
  current line = the PrizePicks standard line, else the Underdog main line. L10 / L20 = most recent 10 / 20 played games
  at that line, strict over / under counts, pushes counted separately (a push is neither a hit nor a miss; rate =
  hits / games in the window, so a push never helps). Minimum sample: 10 played games (L20 needs 20, else blank).
  Rank: the higher of the L10 over / under rates, then the same direction's L20 rate, then games available.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import snapshots
from .contracts import now_utc

VERSION = "sh-1"
CONSISTENT_VERSION = "cs-1"
PLATFORMS = ("prizepicks", "underdog")
SHEETS = PLATFORMS + ("consistent",)
MIN_SAMPLE = 10
CONSISTENT_MAX_ROWS = 600
ORDINARY = {"standard", "balanced", "main", "ladder_rung"}


def sheet_dir(league, base=None) -> Path:
    return snapshots.snap_root(base) / league / "sheets"


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _r(x, n=4):
    return None if x is None else round(float(x), n)


# --------------------------------------------------------------------------------------------- distribution reads
def _reader(fid, forecasts, fdetail):
    """-> fn(line) -> (po, pu, pp, how) | None for ONE forecast (its stored line, its ladder, or its saved PMF)."""
    f = forecasts.get(fid)
    if not f:
        return None
    lad = {float(x["line"]): x for x in ((fdetail.get(fid) or {}).get("ladder") or [])}
    saved = (f.get("detail") or {}).get("pmf")

    def read(line):
        line = float(line)
        if f.get("line") is not None and float(f["line"]) == line and f.get("p_over") is not None:
            return f["p_over"], f["p_under"], f.get("p_push") or 0.0, "stored"
        x = lad.get(line)
        if x and x.get("p_over") is not None:
            return x["p_over"], x["p_under"], x.get("p_push") or 0.0, "ladder"
        if saved:
            from .forecast_detail import line_probs
            o, u, p = line_probs(saved[0], saved[1], line)
            if o is not None:
                return o, u, p, "pmf"
        return None
    return read


def _row_forecast(row, platform, forecasts):
    """The ONE forecast a row's lines are read from: this platform's own ordinary quote's forecast first, then any
    quote's, then the board's line read source; ties -> newest."""
    own, other = [], []
    for q in row.get("quotes") or []:
        fid = q.get("forecast_id") or (q.get("line_read") or {}).get("f")
        if fid and fid in forecasts:
            (own if q.get("platform") == platform else other).append(fid)
    for group in (own, other):
        if group:
            return max(group, key=lambda x: forecasts[x].get("forecast_at") or "")
    return None


def lean_of(po, pu, mean, line, offered):
    """The model's side only when the distribution AND the projection agree (never a contradicting side)."""
    if po is None or pu is None or mean is None:
        return None
    if po > pu and mean > line and offered.get("over"):
        return "over"
    if pu > po and mean < line and offered.get("under"):
        return "under"
    return None


def window(games, stat, line, n):
    """Strict counts over the n most recent PLAYED games with a value for this stat."""
    vals = []
    seasons = set()
    for g in games:                                   # history files are newest first
        if g.get("participation") not in (None, "played"):
            continue
        v = (g.get("values") or {}).get(stat)
        if v is None:
            continue
        vals.append(v)
        seasons.add(g.get("season"))
        if len(vals) >= n:
            break
    o = sum(1 for v in vals if v > line + 1e-9)
    u = sum(1 for v in vals if v < line - 1e-9)
    return {"n": len(vals), "o": o, "u": u, "p": len(vals) - o - u, "seasons": len(seasons - {None}) or (1 if vals else 0),
            "last": vals[:n]}


def _offered(q):
    return {s["side"]: bool(s.get("offered")) for s in q.get("sides") or []}


def _mult(q):
    m = {s["side"]: s.get("multiplier") for s in q.get("sides") or [] if s.get("multiplier") is not None}
    return [m.get("over"), m.get("under")] if m else None


def _market_at(row, q):
    """mv-1 market estimate at this quote's line (ordinary quotes only; `at` lists quotes whose value differs)."""
    mk = row.get("market") or {}
    if not mk or (q.get("variant") not in ORDINARY):
        return None
    at = mk.get("at") or {}
    c = at[q["record_id"]] if q.get("record_id") in at else mk.get("c")   # absent from `at` = same as the headline
    if not c or c.get("po") is None:
        return None
    return c


# ------------------------------------------------------------------------------------------------------ builders
def sheet_rows(player_file, history_games, platform, events, now):
    """Rows of one player for one platform."""
    pf = player_file
    forecasts = pf.get("forecasts") or {}
    fdetail = pf.get("forecast_detail") or {}
    out = []
    for row in pf.get("markets") or []:
        ev = events.get(row.get("event_id"))
        if not ev or not ev.get("start_utc") or ev["start_utc"] <= now:
            continue                                  # started games leave the sheet
        lines = [q for q in (row.get("quotes") or []) + (row.get("alternates") or [])
                 if isinstance(q, dict) and q.get("platform") == platform and not q.get("promotional")
                 and q.get("availability", "open") == "open"]
        if not lines:
            continue
        fid = _row_forecast(row, platform, forecasts)
        read = _reader(fid, forecasts, fdetail) if fid else None
        f = forecasts.get(fid) or {}
        mean = f.get("projection_mean")
        median = (f.get("detail") or {}).get("median", f.get("projection_median"))
        for q in lines:
            line = float(q["line"])
            got = read(line) if read else None
            po, pu, pp, how = got if got else (None, None, None, None)
            offered = _offered(q)
            proj = mean if mean is not None else median
            lean = lean_of(po, pu, proj, line, offered)
            mk = _market_at(row, q)
            edge = None
            if po is not None and mk:
                side = lean or ("over" if po >= pu else "under")
                edge = (po - mk["po"]) if side == "over" else (pu - mk["pu"])
            h10 = window(history_games, row["stat"], line, 10)
            out.append({
                "r": q.get("record_id"), "pid": row["player_id"], "n": row.get("player"), "pos": row.get("position"),
                "tm": row.get("team"), "e": row["event_id"], "st": row["stat"], "sl": row.get("stat_label"),
                "pr": 1 if row.get("primary") else 0, "l": line, "v": q.get("variant") or "standard",
                "o": [1 if offered.get("over") else 0, 1 if offered.get("under") else 0], "m": _mult(q),
                "t": q.get("last_seen_at"), "f": fid, "fm": f.get("model_version"), "fk": f.get("kind"),
                "ro": 1 if f.get("research_only", True) else 0, "mu": _r(mean, 2), "md": _r(median, 2),
                "po": _r(po), "pu": _r(pu), "pp": _r(pp), "how": how,
                "mpo": _r(mk["po"]) if mk else None, "mpu": _r(mk["pu"]) if mk else None,
                "mn": mk.get("n") if mk else None, "edge": _r(edge * 100, 1) if edge is not None else None,
                "lean": lean, "h10": {k: h10[k] for k in ("n", "o", "u", "p", "seasons")}})
    return out


def headline_line(row):
    """CONSISTENT's current line: PrizePicks standard, else the Underdog main line."""
    for plat in PLATFORMS:
        for q in row.get("quotes") or []:
            if q.get("platform") == plat and q.get("variant") in ORDINARY and not q.get("promotional"):
                return plat, float(q["line"])
    return None, None


def consistent_rows(player_file, history_games, events, now):
    out = []
    for row in player_file.get("markets") or []:
        ev = events.get(row.get("event_id"))
        if not ev or not ev.get("start_utc") or ev["start_utc"] <= now:
            continue
        plat, line = headline_line(row)
        if plat is None:
            continue
        w10 = window(history_games, row["stat"], line, 10)
        if w10["n"] < MIN_SAMPLE:
            continue
        w20 = window(history_games, row["stat"], line, 20)
        o10, u10 = w10["o"] / w10["n"], w10["u"] / w10["n"]
        side = "over" if o10 >= u10 else "under"
        r10 = max(o10, u10)
        r20 = None
        if w20["n"] >= 20:
            r20 = (w20["o"] if side == "over" else w20["u"]) / w20["n"]
        out.append({"pid": row["player_id"], "n": row.get("player"), "pos": row.get("position"), "tm": row.get("team"),
                    "e": row["event_id"], "st": row["stat"], "sl": row.get("stat_label"), "pr": 1 if row.get("primary")
                    else 0, "plat": plat, "l": line, "side": side, "r10": _r(r10, 4), "r20": _r(r20, 4),
                    "l10": {k: w10[k] for k in ("n", "o", "u", "p", "seasons", "last")},
                    "l20": {k: w20[k] for k in ("n", "o", "u", "p", "seasons")},
                    "avail": len([g for g in history_games if (g.get("values") or {}).get(row["stat"]) is not None])})
    return out


def build(league, now=None, base=None):
    """Read the league's snapshot files and write the three sheets. -> summary."""
    t0 = time.time()
    now = now or now_utc()
    root = snapshots.snap_root(base) / league
    board = _load(root / "board.json")
    if board is None:
        return {"league": league, "ok": False, "error": "board snapshot not built yet"}
    events = board.get("events") or {}
    rows = {p: [] for p in PLATFORMS}
    cons = []
    players = 0
    for pf_path in sorted((root / "players").glob("*.json")):
        pf = _load(pf_path)
        if not pf:
            continue
        hist = _load(root / "history" / pf_path.name) or {}
        games = hist.get("games") or []
        players += 1
        for plat in PLATFORMS:
            rows[plat] += sheet_rows(pf, games, plat, events, now)
        cons += consistent_rows(pf, games, events, now)
        del pf, hist, games
    ev_view = {eid: {"s": e.get("start_utc"), "a": (e.get("away") or {}).get("abbr"), "h": (e.get("home") or {}).get("abbr")}
               for eid, e in events.items() if e.get("start_utc") and e["start_utc"] > now}
    meta = {"version": VERSION, "league": league, "built_at": now_utc(), "as_of": now,
            "board_built_at": board.get("built_at"), "events": ev_view, "stats": board.get("stats") or {}}
    method = {"scout": "Over / Under / Push at this exact line, unconditional, from the market's one distribution "
                       "(stored line, else its ladder, else its saved PMF); blank when not readable",
              "market": "market estimate (mv-1 display of the mc-1 rule) at this line without this platform; only books "
                        "with a usable price; none for goblin / demon lines",
              "edge": "probability points: Scout chance minus market chance, same side, same line - not EV or ROI",
              "lean": "the model's side only when P(side) is larger AND the projection is on that side of the line, "
                      "and the side is offered",
              "l10": "10 most recent played games at this line: strict over / under, pushes separate"}
    sizes = {}
    d = sheet_dir(league, base)
    for plat in PLATFORMS:
        rr = sorted(rows[plat], key=lambda x: (ev_view.get(x["e"], {}).get("s") or "", x["n"] or "", x["st"], x["l"]))
        sizes[plat] = snapshots._write(d / f"{plat}.json", {**meta, "platform": plat, "rows": rr, "count": len(rr),
                                                             "method": method})
    cons.sort(key=lambda x: (-(x["r10"] or 0), -(x["r20"] or 0), -x["avail"], x["n"] or ""))
    sizes["consistent"] = snapshots._write(d / "consistent.json", {
        **meta, "version": CONSISTENT_VERSION, "rows": cons[:CONSISTENT_MAX_ROWS], "count": len(cons),
        "label": "History at today's line - not a prediction",
        "method": {"line": "the current PrizePicks standard line, else the Underdog main line",
                   "window": "L10 / L20 = the 10 / 20 most recent games played (box score beats game log)",
                   "hit": "strict: over = value above the line, under = value below; a push (equal) is counted "
                          "separately and is never a hit; rate = hits / games in the window",
                   "minimum": f"{MIN_SAMPLE} played games (L20 shown only with 20)",
                   "rank": "higher L10 rate (over or under), then the same direction's L20 rate, then games available"}})
    return {"league": league, "ok": True, "players": players, **{p: len(rows[p]) for p in PLATFORMS},
            "consistent": len(cons), "sizes": sizes, "build_s": round(time.time() - t0, 2)}


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.sheets")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build")
    s.add_argument("leagues", nargs="*", default=list(snapshots.LEAGUES))
    s.add_argument("--now", default=None)
    s.add_argument("--only", choices=("sheets", "without", "results"), default=None)
    s.add_argument("--force", action="store_true", help="rebuild without / results even when fresh")
    a = p.parse_args(argv)
    from . import flags
    flags.require("SPORTS_INGEST")
    out = {}
    for lg in a.leagues:
        if lg not in snapshots.LEAGUES:
            raise SystemExit(f"league {lg}: nba or nfl")
        if a.only in (None, "sheets"):
            out[lg] = build(lg, now=a.now)
    if a.only in (None, "without", "results"):
        from . import results_public, store, without
        with store.opened(read_only=True) as con:
            if a.only in (None, "without"):
                out["without"] = {lg: without.build_if_stale(con, lg, force=a.force) for lg in a.leagues}
            if a.only in (None, "results"):
                out["results"] = results_public.build_if_stale(con, force=a.force)
    print(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
