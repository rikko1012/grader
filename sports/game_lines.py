"""Game lines for the slate (spread / total / moneyline + a short movement history), read from the game store
(sports/games): ESPN pickcenter snapshots (the odds provider ESPN names in the row - DraftKings on 2026-10-23) and the
Kalshi game ladders (implied home-win chance, spread and total from mid prices). Nothing is computed beyond those
records; every value carries its source and observation time. Read only, bounded to the slate's events.

game_lines(league, events) -> {canonical event_id: view} for the board build (sports/snapshots.py). Never raises: a
missing / unreadable game store gives {} (the slate shows no game lines rather than failing the build).

View (field names are the UI contract, sports/UI.md "Game lines"):
  provider       the book ESPN names ("DraftKings"), None when no pickcenter row
  spread         HOME team's spread (negative = home favoured), total, home_ml / away_ml (American odds)
  open           {"spread", "total", "home_ml", "away_ml"} at the first pickcenter row we stored (our first look,
                 not necessarily the book's opening line), observed_at
  observed_at    when Grader last saw the current pickcenter row (ESPN gives no update time; source_time_unknown True)
  history        up to HISTORY_POINTS [{"t", "spread", "total", "home_ml", "away_ml"}] oldest first - a row is stored
                 only when the line changed, so the points are the line's moves
  kalshi         {"home_win", "home_margin", "total", "observed_at"} from the Kalshi ladders (mid prices), or None
"""
from __future__ import annotations

import json

HISTORY_POINTS = 24


def _f(x):
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def _row(pc):
    p = (pc or [{}])[0] if isinstance(pc, list) else (pc or {})
    return {"spread": _f(p.get("spread")), "total": _f(p.get("total")), "home_ml": _f(p.get("home_ml")),
            "away_ml": _f(p.get("away_ml")), "provider": p.get("provider")}


def _event_lines(db, league, espn_id, home_tid, away_tid):
    snaps = db.all("SELECT observed_at, data FROM sports_game_snapshots WHERE league = ? AND event_id = ? AND kind = ? "
                   "AND late = 0 ORDER BY id", (league, espn_id, "pickcenter"))
    out = {"provider": None, "spread": None, "total": None, "home_ml": None, "away_ml": None, "open": None,
           "observed_at": None, "source_time_unknown": True, "history": [], "kalshi": None}
    pts = []
    for s in snaps:
        try:
            r = _row(json.loads(s["data"]))
        except (TypeError, ValueError):
            continue
        if all(r[k] is None for k in ("spread", "total", "home_ml", "away_ml")):
            continue                                    # a row without odds (ESPN emptied pickcenter): never shown as
            #                                             the book's current line - the last priced row stays current
        pts.append({"t": s["observed_at"], **{k: r[k] for k in ("spread", "total", "home_ml", "away_ml")}})
        out["provider"] = r["provider"] or out["provider"]
    if pts:
        cur = pts[-1]
        out.update({k: cur[k] for k in ("spread", "total", "home_ml", "away_ml")})
        out["observed_at"] = cur["t"]
        first = pts[0]
        out["open"] = {**{k: first[k] for k in ("spread", "total", "home_ml", "away_ml")}, "observed_at": first["t"]}
        out["history"] = pts[-HISTORY_POINTS:]
    rows = db.all("SELECT m.* FROM sports_game_market_lines m JOIN (SELECT provider_market_id, MAX(id) AS id FROM "
                  "sports_game_market_lines WHERE league = ? AND event_id = ? AND late = 0 GROUP BY provider_market_id) "
                  "z ON z.id = m.id", (league, espn_id))
    if rows:
        from .games import kalshi_lines
        rows = [dict(r) for r in rows]
        tids = {r.get("team_id") for r in rows}
        h = next((x for x in home_tid if x in tids), home_tid[0])
        a = next((x for x in away_tid if x in tids), away_tid[0])
        imp = kalshi_lines.implied(rows, h, a)
        if any(imp.get(k) is not None for k in ("home_win", "home_margin", "total")):
            out["kalshi"] = {"home_win": imp["home_win"], "home_margin": imp["home_margin"], "total": imp["total"],
                             "observed_at": max(r["observed_at"] for r in rows)}
    return out


def game_lines(league, events):
    """events: {canonical event_id: Event}. -> {event_id: view} (events without any game line are left out)."""
    import os

    from . import config
    if not os.environ.get("SPORTS_DB_DSN") and not (config.DATA_DIR / "games.db").exists():
        return {}                                       # never create the game store from a read
    try:
        from .games import store as game_store
        db = game_store.connect()
    except Exception:                                   # noqa: BLE001 - no game store: no game lines
        return {}
    out = {}
    try:
        for eid, e in events.items():
            espn_id = eid.split(":")[-1]
            ids = lambda t: (t.split(":")[-1], t) if t else (None,)      # noqa: E731 - store keeps ESPN team ids
            try:
                v = _event_lines(db, league, espn_id, ids(e.home_team_id), ids(e.away_team_id))
            except Exception:                           # noqa: BLE001 - one game never breaks the slate
                continue
            if v["observed_at"] or v["kalshi"]:
                out[eid] = v
    finally:
        try:
            db.close()
        except Exception:                               # noqa: BLE001
            pass
    return out
