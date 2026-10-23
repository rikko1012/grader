"""The forecast log: what was predicted before each game, what happened, and how good the predictions were.

record   - append one row per offered line BEFORE its start (append-only: a new row only when the line, the side or the
           chance moved by a point or more; nothing is ever rewritten). Each row keeps the sharp quote it came from,
           the line gap, the history baseline when computed, and MODEL_TAG + the dispersion fit hash.
settle   - after the game: ESPN box score -> the stat -> over / under / push, or void (did not play: both platforms void
           a DNP), or pending with the reason (game not final, player not found). Settlements are separate rows; a
           forecast is never edited.
report   - the LAST pre-start forecast of each offer, one count per distinct prop (league, game, player, stat, line,
           side - the same prop on two platforms counts once), push / void / pending excluded and counted. Hit rate of
           the forecast side, mean forecast chance, Brier, log loss, a game-clustered bootstrap interval, and the
           history baseline scored on the same rows when it was recorded. Small samples prove nothing: the interval
           says how little.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import config, espn, markets, names

MODEL_TAG = "sharp-1"          # no-vig Pinnacle (or 2+ book consensus) + measured line-shift shapes
SETTLE_AFTER_H = {"basketball": 3.5, "football": 4.5}

SCHEMA = """
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at TEXT NOT NULL, league TEXT, platform TEXT, offer_id TEXT,
  player TEXT, player_key TEXT, team TEXT, game TEXT, start TEXT, market TEXT, line REAL, odds_type TEXT,
  sides_offered TEXT, side TEXT, chance REAL, push REAL, multiplier REAL, eff REAL,
  sharp_source TEXT, sharp_line REAL, sharp_p_over REAL, sharp_books INTEGER, sharp_limit REAL, gap REAL, far INTEGER,
  sharp_method TEXT, sharp_rungs TEXT, sharp_spread REAL,
  base_mean REAL, base_chance REAL, base_games INTEGER, model TEXT, fit TEXT, injury TEXT, role TEXT,
  book_consensus TEXT);
CREATE INDEX IF NOT EXISTS forecasts_offer ON forecasts (offer_id, id);
CREATE TABLE IF NOT EXISTS settlements (
  id INTEGER PRIMARY KEY AUTOINCREMENT, forecast_id INTEGER NOT NULL, settled_at TEXT NOT NULL, outcome TEXT,
  actual REAL, detail TEXT, espn_event TEXT);
CREATE INDEX IF NOT EXISTS settlements_f ON settlements (forecast_id, id);
"""


def connect(path=None):
    p = path or config.FORECAST_DB
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    have = {r["name"] for r in con.execute("PRAGMA table_info(forecasts)")}
    for col, typ in (("role", "TEXT"), ("book_consensus", "TEXT")):                   # columns added after a log was first created
        if col not in have:
            con.execute(f"ALTER TABLE forecasts ADD COLUMN {col} {typ}")
    return con


def fit_hash():
    try:
        return hashlib.sha256(config.DISPERSION_FILE.read_bytes()).hexdigest()[:10]
    except OSError:
        return None


def _now():
    return datetime.now(timezone.utc)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def save(con, rows, baselines=None, now=None):
    """Append matched rows that start in the future. -> number of rows written."""
    now = now or _now()
    fit, n = fit_hash(), 0
    for r in rows:
        if r.get("chance") is None or not r.get("start") or _dt(r["start"]) <= now:
            continue
        last = con.execute("SELECT line, side, chance FROM forecasts WHERE offer_id = ? ORDER BY id DESC LIMIT 1",
                           (r["offer_id"],)).fetchone()
        if last and last["line"] == r["line"] and last["side"] == r["side"] and abs(last["chance"] - r["chance"]) < 0.01:
            continue
        b = (baselines or {}).get(r["offer_id"]) or {}
        base_chance = None
        if b.get("p_over") is not None:
            dec = 1 - b.get("p_push", 0)
            base_chance = (b["p_over"] if r["side"] == "over" else b["p_under"]) / dec if dec > 0 else None
        s = r["sharp"]
        con.execute("INSERT INTO forecasts (recorded_at, league, platform, offer_id, player, player_key, team, game, "
                    "start, market, line, odds_type, sides_offered, side, chance, push, multiplier, eff, sharp_source, "
                    "sharp_line, sharp_p_over, sharp_books, sharp_limit, gap, far, sharp_method, sharp_rungs, "
                    "sharp_spread, base_mean, base_chance, base_games, model, fit, injury, role, book_consensus) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_iso(now), r["league"], r["platform"], r["offer_id"], r["player"], r["player_key"], r.get("team"),
                     r.get("game"), r["start"], r["market"], r["line"], r.get("odds_type"),
                     json.dumps(r["sides_offered"]), r["side"], r["chance"], r.get("push"), r.get("multiplier"),
                     r.get("eff"), s["source"], s["line"], s["p_over"], s.get("books"), s.get("limit"), s["gap"],
                     int(bool(s["far"])), s.get("method"), json.dumps(s.get("rungs")), s.get("spread"),
                     b.get("mean"), base_chance, b.get("games"), MODEL_TAG, fit, r.get("injury"), r.get("role"),
                     json.dumps(r["books"]) if r.get("books") else None))
        n += 1
    con.commit()
    return n


def _outcome(actual, line):
    if actual > line:
        return "over"
    if actual < line:
        return "under"
    return "push"


def _find_player(lg, start, player, player_key):
    """-> (row, event_id, reason). Looks in every ESPN game starting within 3 h of `start`."""
    t = _dt(start)
    evs = espn.scoreboard(lg, (t - timedelta(hours=12)).date(), (t + timedelta(hours=12)).date())
    cands = [e for e in evs if e["start"] and abs((_dt(e["start"]) - t).total_seconds()) <= 3 * 3600]
    if not cands:
        return None, None, "no ESPN game at that time"
    found, unfinished = [], False
    for e in cands:
        box = espn.summary(lg, e["id"])
        rows = box["players"].get(player_key) or []
        if rows and not box["completed"]:
            return None, e["id"], "game not final"
        unfinished = unfinished or not box["completed"]
        found += [(r, e["id"]) for r in rows]
    if len(found) == 1:
        return found[0][0], found[0][1], None
    if len(found) > 1:
        return None, None, "ambiguous: name in two box scores"
    if unfinished:
        return None, None, "game not final"
    if lg["family"] == "football":
        return _from_gamelog(lg, player, {e["id"] for e in cands})
    return None, None, "player not in the box score"


def _from_gamelog(lg, player, event_ids):
    """Football: a player with no stat line is missing from every box-score group. His ESPN game log says whether he
    played one of these games (then with 0s for what the log lacks) or not (did not play)."""
    aths = [a for a in espn.search_athlete(lg, player) if names.key(a["name"]) == names.key(player)]
    if len(aths) != 1:
        return None, None, "player not in the box score (no single ESPN athlete for the name)"
    for g in espn.gamelog(lg, aths[0]["id"], seasons=1):
        if g["event_id"] in event_ids:
            return {"played": True, "parts": g["parts"]}, g["event_id"], None
    return {"played": False, "parts": {}}, None, None


def settle(con, league=None, now=None):
    """Settle every forecast whose game should be over. -> counts by outcome."""
    now = now or _now()
    q = ("SELECT f.* FROM forecasts f WHERE NOT EXISTS (SELECT 1 FROM settlements s WHERE s.forecast_id = f.id AND "
         "s.outcome IN ('over','under','push','void'))")
    args = []
    if league:
        q += " AND f.league = ?"
        args.append(league)
    counts = {}
    memo = {}
    for f in con.execute(q, args).fetchall():
        lg = config.league(f["league"])
        if _dt(f["start"]) > now - timedelta(hours=SETTLE_AFTER_H[lg["family"]]):
            continue
        k = (f["league"], f["start"], f["player_key"])
        if k not in memo:
            try:
                memo[k] = _find_player(lg, f["start"], f["player"], f["player_key"])
            except Exception as e:                    # a source error leaves the pick pending, never settled wrong
                memo[k] = (None, None, f"source error: {str(e)[:80]}")
        row, event_id, why = memo[k]
        outcome, actual, detail = "pending", None, why
        if row is not None:
            if row["played"] is False:
                outcome, detail = "void", "did not play"
            elif row["played"]:
                actual = markets.value(lg["family"], f["market"],
                                       markets.defaults_for(lg["family"], row["parts"], True))
                if actual is None:
                    outcome, detail = "pending", "stat missing from the box score"
                else:
                    outcome, detail = _outcome(actual, f["line"]), None
            else:
                detail = "playing status unknown"
        last = con.execute("SELECT outcome, detail FROM settlements WHERE forecast_id = ? ORDER BY id DESC LIMIT 1",
                           (f["id"],)).fetchone()
        if not (last and outcome == "pending" and last["outcome"] == "pending" and last["detail"] == detail):
            con.execute("INSERT INTO settlements (forecast_id, settled_at, outcome, actual, detail, espn_event) "
                        "VALUES (?,?,?,?,?,?)", (f["id"], _iso(now), outcome, actual, detail, event_id))
        counts[outcome] = counts.get(outcome, 0) + 1
    con.commit()
    return counts


def _latest_settled(con, league=None):
    q = ("SELECT f.*, s.outcome, s.actual FROM forecasts f JOIN (SELECT offer_id, MAX(id) AS id FROM forecasts "
         "WHERE recorded_at < start GROUP BY offer_id) z ON z.id = f.id LEFT JOIN settlements s ON s.id = "
         "(SELECT MAX(id) FROM settlements WHERE forecast_id = f.id)")
    rows = [dict(r) for r in con.execute(q).fetchall()]
    return [r for r in rows if not league or r["league"] == league]


def _scores(rows, key):
    import math
    ps = [min(max(r[key], 1e-6), 1 - 1e-6) for r in rows]
    ys = [1.0 if r["outcome"] == r["side"] else 0.0 for r in rows]
    n = len(ps)
    return {"brier": sum((p - y) ** 2 for p, y in zip(ps, ys)) / n,
            "log_loss": -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in zip(ps, ys)) / n}


def _cluster_ci(rows, reps=2000, seed=7):
    import numpy as np
    games = {}
    for r in rows:
        games.setdefault((r["league"], r["start"], r["game"]), []).append(1.0 if r["outcome"] == r["side"] else 0.0)
    if len(games) < 2:
        return None
    hits = np.array([sum(v) for v in games.values()])
    ns = np.array([len(v) for v in games.values()])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(hits), size=(reps, len(hits)))
    rates = hits[idx].sum(axis=1) / ns[idx].sum(axis=1)
    return float(np.percentile(rates, 2.5)), float(np.percentile(rates, 97.5))


def summarize(rows):
    decided = [r for r in rows if r["outcome"] in ("over", "under")]
    out = {"props": len(decided),
           "excluded": {k: sum(1 for r in rows if r["outcome"] == k) for k in ("push", "void", "pending")},
           "unsettled": sum(1 for r in rows if r["outcome"] is None)}
    if not decided:
        return out
    hits = sum(1 for r in decided if r["outcome"] == r["side"])
    out.update({"hits": hits, "hit_rate": hits / len(decided),
                "mean_chance": sum(r["chance"] for r in decided) / len(decided),
                "games": len({(r["league"], r["start"], r["game"]) for r in decided}),
                "hit_rate_95": _cluster_ci(decided), **_scores(decided, "chance")})
    both = [r for r in decided if r.get("base_chance") is not None]
    if both:
        out["same_rows_with_baseline"] = {"props": len(both), "sharp": _scores(both, "chance"),
                                          "baseline": _scores(both, "base_chance")}
    return out


_F_KEY = ("recorded_at", "platform", "offer_id", "line", "side", "chance")


def export(con, path):
    """Write the whole log (forecasts, then settlements) as gzipped JSON lines -> (forecasts, settlements). The file
    is the portable copy: the log itself lives on one machine's disk."""
    import gzip
    nf = ns = 0
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for r in con.execute("SELECT * FROM forecasts ORDER BY id"):
            fh.write(json.dumps({"t": "forecast", **dict(r)}) + "\n")
            nf += 1
        for r in con.execute("SELECT * FROM settlements ORDER BY id"):
            fh.write(json.dumps({"t": "settlement", **dict(r)}) + "\n")
            ns += 1
    return nf, ns


def import_(con, path):
    """Append an export to this log -> (forecasts added, settlements added). Idempotent: a forecast already here (same
    recorded_at, platform, offer, line, side, chance) is not added twice, and settlements follow their forecast to its
    id in this log. Columns this log lacks are dropped; columns the export lacks stay empty."""
    import gzip
    cols = {r["name"] for r in con.execute("PRAGMA table_info(forecasts)")} - {"id"}
    scols = {r["name"] for r in con.execute("PRAGMA table_info(settlements)")} - {"id", "forecast_id"}
    ids, nf, ns = {}, 0, 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r.pop("t") == "forecast":
                old = r.pop("id")
                have = con.execute("SELECT id FROM forecasts WHERE " + " AND ".join(f"{c} IS ?" for c in _F_KEY),
                                   [r.get(c) for c in _F_KEY]).fetchone()
                if have:
                    ids[old] = have["id"]
                    continue
                keep = {k: v for k, v in r.items() if k in cols}
                cur = con.execute(f"INSERT INTO forecasts ({', '.join(keep)}) VALUES ({', '.join('?' * len(keep))})",
                                  list(keep.values()))
                ids[old] = cur.lastrowid
                nf += 1
            else:
                fid = ids.get(r.pop("forecast_id"))
                r.pop("id", None)
                if fid is None or con.execute("SELECT 1 FROM settlements WHERE forecast_id = ? AND settled_at = ? AND "
                                              "outcome IS ?", (fid, r.get("settled_at"), r.get("outcome"))).fetchone():
                    continue
                keep = {k: v for k, v in r.items() if k in scols}
                con.execute(f"INSERT INTO settlements (forecast_id, {', '.join(keep)}) VALUES "
                            f"(?, {', '.join('?' * len(keep))})", [fid, *keep.values()])
                ns += 1
    con.commit()
    return nf, ns


def report(con, league=None):
    rows = _latest_settled(con, league)
    uniq = {}
    for r in rows:                                    # one count per distinct prop
        k = (r["league"], r["start"], r["player_key"], r["market"], r["line"], r["side"])
        if k not in uniq or (r["outcome"] in ("over", "under") and uniq[k]["outcome"] not in ("over", "under")):
            uniq[k] = r
    u = list(uniq.values())
    out = {"model": MODEL_TAG, "offers": len(rows), "all": summarize(u), "by_league": {}, "by_market": {},
           "by_band": {}, "by_platform": {}}
    for name, keyf in (("by_league", lambda r: r["league"]), ("by_market", lambda r: f"{r['league']}:{r['market']}"),
                       ("by_platform", lambda r: r["platform"]),
                       ("by_band", lambda r: "60%+" if r["chance"] >= 0.60 else ("55-60%" if r["chance"] >= 0.55
                                                                                  else "under 55%"))):
        groups = {}
        for r in u:
            groups.setdefault(keyf(r), []).append(r)
        out[name] = {k: summarize(v) for k, v in sorted(groups.items())}
    return out
