"""Scout Sports SHEETS (sh-1): the PrizePicks sheet, the Underdog sheet and the Most-consistent sheet - one dense row per
current pick'em line, built in the WORKER from the UI snapshot files (sports/snapshots.py players/ + history/), never in
a member request. Served by sports/api_sheets.py as plain JSON bytes.

    python -m sports.sheets build [nba nfl]        # job "sheets" (sports/scheduler.py), also builds without + results

Files: <snapshots>/<league>/sheets/prizepicks.json, underdog.json, consistent.json (atomic writes).

ROW SEMANTICS are the board's, not re-implemented here (sports/UI.md "Prop board table"): the platform files carry
markets in the board's compact format (snapshots.compact_row / compact_quote / compact_forecasts) plus a bv-1 view
(sports/board_view.row_summary), and the page builds every row with SportsRows.propRow (web/sports/table.js pr-1):
lean, Scout chance, market chance at that line, edge (probability points), gap flag, L10 / L5, cf-1 confidence.
What this module adds:
  lines       every OPEN line of that platform on a game that has not started: ordinary lines AND the platform's goblin /
              demon / alternate lines, each with its variant `v`. Promotional lines are left out. Kalshi and books are
              never rows (they are the market).
  lr          a Scout line read at a line that has no Scout forecast of its own: the market's newest scout / informed
              forecast read at THAT line from its own distribution (stored line, its ladder, or its saved PMF) - the
              board's scout_line_reads rule extended to the ladder. Unreadable -> no lr, never a guess. Market-only
              forecasts are never read this way.
  view        bv-1 hit counts (board_view.row_summary) at every line of the platform, alternates included.
CONSISTENT (cs-1) - history, not a prediction
  current line = the PrizePicks standard line, else the Underdog main line. L10 / L20 = most recent 10 / 20 played games
  at that line (board_view.played_values / hit_counts - the bv-1 filter), strict over / under counts, pushes separately (a push is neither a hit nor a miss; rate =
  hits / games in the window, so a push never helps). Minimum sample: 10 played games (L20 needs 20, else blank).
  Rank: the higher of the L10 over / under rates, then the same direction's L20 rate, then games available.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import board_view, snapshots
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


# ------------------------------------------------------------------------------------------------------ builders
def _compact_fc(f):
    """The board's compact forecast (snapshots.compact_forecasts format) for one full forecast view."""
    fid = f["forecast_id"]
    return snapshots.compact_forecasts({fid: f}, [{"quotes": [{"forecast_id": fid}], "kalshi_near": []}])[fid]


def sheet_markets(player_file, history_games, platform, events, now):
    """One player's markets for one platform in the BOARD's compact row format (snapshots.compact_row / compact_quote),
    so the page builds every row with the shared SportsRows.propRow (web/sports/table.js, pr-1): lean, Scout chance,
    market chance, edge, gap, confidence. Adds what the board does not carry: the platform's goblin / demon / alternate
    lines (`v` = variant), a Scout line read `lr` at each such line from the market's ONE scout distribution (same
    rule as snapshots.scout_line_reads, extended to the forecast's ladder), and the bv-1 hit counts at every line.
    -> (markets, fc, view)"""
    pf = player_file
    forecasts = pf.get("forecasts") or {}
    fdetail = pf.get("forecast_detail") or {}
    markets_out, fc, view = [], {}, {}
    for row in pf.get("markets") or []:
        ev = events.get(row.get("event_id"))
        if not ev or not ev.get("start_utc") or ev["start_utc"] <= now:
            continue                                  # started games leave the sheet
        lines = [q for q in (row.get("quotes") or []) + (row.get("alternates") or [])
                 if isinstance(q, dict) and q.get("platform") == platform and not q.get("promotional")
                 and q.get("availability", "open") == "open"]
        if not lines:
            continue
        scout = [fid for fid in {q.get("forecast_id") for q in row.get("quotes") or []} - {None}
                 if forecasts.get(fid, {}).get("kind") in ("scout", "informed")]
        src = max(scout, key=lambda x: forecasts[x].get("forecast_at") or "") if scout else None
        read = _reader(src, forecasts, fdetail) if src else None
        qs = []
        for q in lines:
            cq = snapshots.compact_quote(dict(q, last_seen_at=q.get("last_seen_at") or q.get("observed_at"),
                                              gradable=bool(q.get("gradable"))))
            own = forecasts.get(cq.get("f") or "")
            if own:
                fc[own["forecast_id"]] = _compact_fc(own)
            if not (own and own.get("kind") in ("scout", "informed")) and not cq.get("lr") and read:
                got = read(float(q["line"]))
                if got:
                    cq["lr"] = {"f": src, "ln": forecasts[src]["line"], "po": _r(got[0]), "pu": _r(got[1]),
                                "pp": _r(got[2]), "via": got[3]}
            if (cq.get("lr") or {}).get("f") in forecasts:
                fc[cq["lr"]["f"]] = _compact_fc(forecasts[cq["lr"]["f"]])
            qs.append(cq)
        vr = board_view.row_summary(row["stat"], [q["line"] for q in lines], history_games)
        if os.environ.get("SCOUT_BOARD_H2H") == "1":  # research board H2H + Season columns; flag off = the sheet bytes as before
            hm, aw = (ev.get("home") or {}).get("team_id"), (ev.get("away") or {}).get("team_id")
            tid = row.get("team_id")
            opp = aw if tid and tid == hm else hm if tid and tid == aw else None
            vr.update(board_view.h2h_season_lines(row["stat"], [q["line"] for q in lines], history_games, opp))
        lg =(row.get("event_id") or "").split(":", 1)[0]
        nv = board_view.contain_reason(lg, row["stat"])
        for cq in qs:                                 # the board's row signal on the sheet's own line reads (cf-2)
            own = forecasts.get(cq.get("f") or "")
            if cq.get("x") or not (cq.get("lr") or (own or {}).get("kind") in ("scout", "informed")):
                continue
            if own and own.get("kind") in ("scout", "informed"):
                po, pu, pp, src = own.get("p_over"), own.get("p_under"), own.get("p_push"), own
            else:
                po, pu, pp = cq["lr"]["po"], cq["lr"]["pu"], cq["lr"].get("pp")
                src = forecasts.get(cq["lr"].get("f") or "") or {}
            ln = float(cq["l"])
            raw = (po, pu)
            l10 = vr["lines"].get(board_view.line_key(ln))
            mkc = board_view.market_at(row.get("market"), cq.get("r"), ln)
            ca = board_view.calibrated(lg, row["stat"], po, pu, pp, src.get("model_version") or "sm-1", mkc)
            if ca:                                    # cal-mi-1 (REGISTER A8): the same displayed chance as the board
                ca["f"] = src.get("forecast_id")
                cq["ca"] = ca
                po, pu = ca["po"], ca["pu"]
            # sg-2: raw chance for the degenerate check; the history rules only when the player's history was read
            cq["x"], _ = board_view.quote_x(lg, row["stat"], po, pu, src.get("projection_mean"), ln, mkc,
                                            list(l10[:4]) if l10 else None, (src.get("detail") or {}).get("interval"),
                                            raw=raw, view=vr if history_games is not None else None, now=now)
        markets_out.append({**({"nv": nv} if nv else {}), "k": row["key"], "e": row["event_id"], "pid": row["player_id"],
                            "n": row.get("player"),
                            "pos": row.get("position"), "tm": row.get("team"), "tid": row.get("team_id"),
                            "st": row["stat"], "sl": row.get("stat_label"), "pr": 1 if row.get("primary") else 0,
                            "cap": row.get("capability"), "mk": row.get("market"), "q": qs})
        view[row["key"]] = vr
    return markets_out, fc, view


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
        pv = board_view.played_values(history_games, row["stat"])
        vals = [x[0] for x in pv]
        o10, u10, p10, n10 = board_view.hit_counts(vals, line, 10)
        if n10 < MIN_SAMPLE:
            continue
        o20, u20, p20, n20 = board_view.hit_counts(vals, line, 20)
        side = "over" if o10 >= u10 else "under"
        r10 = max(o10, u10) / n10
        r20 = ((o20 if side == "over" else u20) / n20) if n20 >= 20 else None
        out.append({"k": row["key"], "pid": row["player_id"], "n": row.get("player"), "pos": row.get("position"),
                    "tm": row.get("team"), "e": row["event_id"], "st": row["stat"], "sl": row.get("stat_label"),
                    "pr": 1 if row.get("primary") else 0, "plat": plat, "l": line, "side": side, "r10": _r(r10, 4),
                    "r20": _r(r20, 4), "l10": {"n": n10, "o": o10, "u": u10, "p": p10, "last": vals[:10],
                                               "seasons": sorted({x[1] for x in pv[:10] if x[1] is not None})},
                    "l20": {"n": n20, "o": o20, "u": u20, "p": p20}, "avail": len(vals)})
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
    fcs = {p: {} for p in PLATFORMS}
    views = {p: {} for p in PLATFORMS}
    cons = []
    players = 0
    for pf_path in sorted((root / "players").glob("*.json")):
        pf = _load(pf_path)
        if not pf:
            continue
        hist = _load(root / "history" / pf_path.name) or {}
        games = (hist.get("games") or []) if hist else None       # None = no history file: sg-2 history rules skip
        players += 1
        for plat in PLATFORMS:
            m, f, v = sheet_markets(pf, games, plat, events, now)
            rows[plat] += m
            fcs[plat].update(f)
            views[plat].update(v)
        cons += consistent_rows(pf, games, events, now)
        del pf, hist, games
    ev_view = {eid: {"s": e.get("start_utc"), "st": e.get("status"), "a": (e.get("away") or {}).get("abbr"),
                     "h": (e.get("home") or {}).get("abbr")}
               for eid, e in events.items() if e.get("start_utc") and e["start_utc"] > now}
    meta = {"version": VERSION, "league": league, "built_at": now_utc(), "as_of": now,
            "board_built_at": board.get("built_at"), "events": ev_view, "stats": board.get("stats") or {}}
    method = {"rows": "built in the page with the board's shared row (SportsRows.propRow, sports/UI.md 'Prop board "
                      "table'): lean, Scout chance, market at this line, edge in probability points, L10 / L5, "
                      "confidence - research until forward validation",
              "lines": "every open line of this platform, goblin / demon / alternate included (promotions left out)",
              "scout_at_other_lines": "read from the market's own Scout distribution at that exact line (stored line, "
                                      "its ladder or its saved PMF); none when it cannot be read",
              "market": "market estimate at this line without this platform, only books with a usable price; none "
                        "for goblin / demon lines (books do not list them)"}
    sizes = {}
    d = sheet_dir(league, base)
    for plat in PLATFORMS:
        rr = sorted(rows[plat], key=lambda x: (ev_view.get(x["e"], {}).get("s") or "", x["n"] or "", x["st"]))
        sizes[plat] = snapshots._write(d / f"{plat}.json", {
            **meta, "platform": plat, "markets": rr, "fc": fcs[plat], "view": views[plat],
            "view_version": board_view.VERSION, "count": sum(len(m["q"]) for m in rr), "method": method,
            "format": "markets = board compact rows (k, e, pid, n, pos, tm, st, mk, q[r, p, l, v, o, m, t, f, lr]); "
                      "fc = board compact forecasts; view = bv-1 row summaries keyed by market key"})
    for c in cons:
        c["s"] = (ev_view.get(c["e"]) or {}).get("s")
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
    return {"league": league, "ok": True, "players": players, **{p: sum(len(m["q"]) for m in rows[p]) for p in PLATFORMS},
            "consistent": len(cons), "sizes": sizes, "build_s": round(time.time() - t0, 2)}


class Stages:
    """The run's steps on disk after EACH step, so a killed run still names the step it died in (admin ops):
    `current` is this run (finished false after a kill), `last_ok` the last run that finished."""

    def __init__(self, path):
        self.path, self.t0, self.steps = path, time.time(), []
        try:
            self.last_ok = json.loads(path.read_text(encoding="utf-8")).get("last_ok")
        except (OSError, ValueError):
            self.last_ok = None
        self.started = now_utc()

    def _save(self, step, finished):
        cur = {"started_at": self.started, "step": step, "finished": finished, "ran_s": round(time.time() - self.t0, 1),
               "steps": self.steps, "rss_mb": _rss_mb()}
        doc = {"current": cur, "last_ok": cur if finished else self.last_ok}
        try:
            self.path.write_text(json.dumps(doc), encoding="utf-8")
        except OSError:
            pass

    def run(self, name, fn):
        self._save(name, False)
        t = time.time()
        out = fn()
        self.steps = self.steps + [[name, round(time.time() - t, 1), _rss_mb()]]
        return out

    def done(self):
        self._save(None, True)


def _rss_mb():
    try:
        with open("/proc/self/statm") as fh:
            return round(int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6, 1)
    except (OSError, ValueError, AttributeError):
        return None


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
    from . import config
    st = Stages(config.DATA_DIR / "sheets_stages.json")
    out = {}
    for lg in a.leagues:
        if lg not in snapshots.LEAGUES:
            raise SystemExit(f"league {lg}: nba or nfl")
        if a.only in (None, "sheets"):
            out[lg] = st.run(f"sheets:{lg}", lambda lg=lg: build(lg, now=a.now))
    if a.only in (None, "without", "results"):
        from . import results_public, store, without
        with store.opened(read_only=True) as con:
            if a.only in (None, "without"):
                out["without"] = {lg: st.run(f"without:{lg}", lambda lg=lg: without.build_if_stale(con, lg, force=a.force))
                                  for lg in a.leagues}
            if a.only in (None, "results"):
                out["results"] = st.run("results", lambda: results_public.build_if_stale(con, force=a.force))
    st.done()
    print(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
