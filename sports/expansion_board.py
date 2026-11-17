"""Isolated soccer and tennis quote collection, snapshots and member API.

One process per sport. No writes to existing NBA/NFL data, models or settlement.
Quotes are source-native research offers until sport-specific identity, historical
features, model validation and house-rule settlement have been connected.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from functools import lru_cache

from .contracts import now_utc, parse_utc
from . import expansion_offers as adapters

MAX_AGE = 300
SPORTS = ("soccer", "tennis")


@lru_cache(maxsize=2)
def _read_snapshot(path, stamp, size):
    # At most one parsed snapshot per new sport, rather than per member request.
    return json.loads(Path(path).read_text(encoding="utf-8"))


def root(sport):
    if sport not in SPORTS:
        raise ValueError("unknown sport")
    from . import config
    return Path(os.environ.get("SCOUT_EXPANSION_DATA_DIR") or config.DATA_DIR / "expansion") / sport


def enabled(sport):
    return sport in SPORTS and os.environ.get(f"SCOUT_{sport.upper()}_ENABLED") == "1"


def save(sport, rows, sources, observed_at, directory=None):
    directory = Path(directory) if directory else root(sport)
    if any(r.get("sport") != sport for r in rows):
        raise ValueError("mixed sport batch refused")
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / "quotes.db"
    # Stop before growing past the isolated budget; no silent history deletion.
    if sum(p.stat().st_size for p in directory.glob("quotes.db*")) > 250 * 1024 * 1024:
        raise RuntimeError("expansion quote history storage budget reached")
    con = sqlite3.connect(db)
    try:
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS ownership (sport TEXT NOT NULL UNIQUE)")
            owners = con.execute("SELECT sport FROM ownership").fetchall()
            if owners and owners != [(sport,)]:
                raise ValueError("database belongs to another sport")
            con.execute("INSERT OR IGNORE INTO ownership VALUES (?)", (sport,))
            con.execute("CREATE TABLE IF NOT EXISTS revisions (id INTEGER PRIMARY KEY, platform TEXT, offer_id TEXT, revision TEXT, observed_at TEXT, payload TEXT)")
            con.execute("CREATE INDEX IF NOT EXISTS quote_latest ON revisions(platform, offer_id, id DESC)")
            for row in rows:
                previous = con.execute("SELECT revision FROM revisions WHERE platform=? AND offer_id=? ORDER BY id DESC LIMIT 1",
                                       (row['platform'], row['offer_id'])).fetchone()
                if not previous or previous[0] != row['revision']:
                    con.execute("INSERT INTO revisions(platform,offer_id,revision,observed_at,payload) VALUES (?,?,?,?,?)",
                                (row['platform'], row['offer_id'], row['revision'], observed_at, json.dumps(row)))
    finally:
        con.close()
    snapshot = {"sport": sport, "built_at": observed_at, "sources": sources, "offers": rows,
                "mode": "lines", "grading_ready": False}
    fd, temporary = tempfile.mkstemp(prefix="board-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(temporary, directory / "board.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return snapshot


def collect(sport):
    from . import net
    rows, sources = [], {}
    jobs = [("prizepicks", "https://partner-api.prizepicks.com/projections",
             {"league_id": 82 if sport == "soccer" else 5, "per_page": 1000, "single_stat": "true", "game_mode": "pickem"})]
    if sport == "tennis":
        jobs.append(("underdog", "https://api.underdogfantasy.com/v1/over_under_lines", {"sport_id": "TENNIS"}))
    for source, url, params in jobs:
        try:
            body = net.get_json(url, params=params, timeout=25, tries=1)
            observed = now_utc()
            found = adapters.prizepicks(body, sport, observed) if source == "prizepicks" else adapters.underdog(body, observed)
            rows.extend(found)
            sources[source] = {"ok": True, "checked_at": observed, "offers": len(found)}
        except (net.HttpError, ValueError, OSError) as e:
            sources[source] = {"ok": False, "checked_at": now_utc(), "error": type(e).__name__}
    return save(sport, rows, sources, now_utc())


def board(req):
    sport = req.query.get("sport", "soccer")
    if sport not in SPORTS:
        return 400, {"error": "sport must be soccer or tennis"}
    if not enabled(sport):
        return 404, {"error": "not_found"}
    path = root(sport) / "board.json"
    try:
        st = path.stat()
        if st.st_size > 3_000_000:
            raise ValueError("oversize")
        data = dict(_read_snapshot(str(path), st.st_mtime_ns, st.st_size))
        now = datetime.now(timezone.utc)
        age = (now - parse_utc(data["built_at"])).total_seconds()
        if age < 0 or age > MAX_AGE:
            return 503, {"error": "prices_stale", "sport": sport, "last_checked": data["built_at"]}
        data["offers"] = [r for r in data["offers"] if r.get("sport") == sport
                          and parse_utc(r["start"]) > now
                          and 0 <= (now - parse_utc(r["observed_at"])).total_seconds() <= MAX_AGE]
        return 200, data
    except (OSError, ValueError, KeyError, TypeError):
        return 503, {"error": "board_not_ready", "sport": sport}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sport", choices=SPORTS)
    args = p.parse_args()
    if not enabled(args.sport):
        raise SystemExit("sport collector disabled")
    result = collect(args.sport)
    print(json.dumps({"sport": args.sport, "offers": len(result["offers"]), "sources": result["sources"]}))
    if not any(s["ok"] for s in result["sources"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
