"""Player-page context factors from the factor engine (sports/games/factors.py, contract sports-factors/1), built in
the WORKER inside the snapshot build (sports/snapshots.build) - never in the web process.

One LeagueData load per board (the engine's own rule), one factors.compute per (player, stat) on the board. The engine
owns the point-in-time rules (a game's result only once it ENDED before as_of, snapshots / lines only if observed
before it, EPA only with an expected-points table trained on earlier games). Values only - no weights. These values are
descriptive context: the Grader model does not use them (its own inputs are the forecast's saved input row), so every
value is labelled CONTEXT_LABEL until a registered factor test promotes a factor into a model version.

NFL snap share: the engine's game store has no snap counts, but nflverse snap counts (free, CC-BY) are in the model's
forward history copy (<SPORTS_DATA_DIR>/model/fwd/history.db, refreshed by the forecast job), read here read-only with
the same point-in-time rule as the model (game start + 6 h <= as_of).

Everything is best effort: no games store, no history copy or any error -> the section says why, the build never fails.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import timedelta

from . import config
from .contracts import parse_utc

CONTEXT_LABEL = "Context - not yet in the Grader model"
ENGINE_VERSION = "player-context-1"
# owner factor-list items no source we hold has - one-word reason each (everything else comes from the engine)
NOT_HELD = {
    "basketball": [("Likely defender", "tracking"), ("Potential assists", "tracking"), ("PFF / NGS / Second Spectrum data", "licensed")],
    "football": [("Coverage scheme (man / zone, shells)", "licensed"), ("PFF / NGS / DVOA", "licensed"),
                 ("Defender matchup", "licensed"), ("Route participation / TPRR", "unpublished")],
}
SNAP_KNOWN_AFTER_H = 6
ON_OFF_LABEL = "On/off (net, from lineups inferred from substitutions)"


def _tail(record_id):
    return str(record_id).rsplit(":", 1)[-1] if record_id else None


def _dvp_rows(f, fam, stat):
    """Opponent defense vs the player's position group from the engine (factors.dvp_table): stat allowed per game and
    rank (1 = fewest allowed of `teams`), each row carrying its sample n (opponent games in the engine window)."""
    if f.get("dvp_allowed_pg") is None:
        return []
    from . import markets
    grp, n, teams = f.get("dvp_group"), f.get("dvp_n"), f.get("dvp_teams")
    lab = (markets.market(fam, stat) or {}).get("label") or stat
    base = {"group": "Defensive matchup" if fam == "basketball" else "Matchup", "source": "ESPN box score",
            "status": "derived", "n": n, "pos": grp, "teams": teams}
    rows = [dict(base, key="dvp_allowed_pg", label=f"{lab} allowed per game to {grp}", value=f["dvp_allowed_pg"])]
    if f.get("dvp_rank") is not None:
        rows.append(dict(base, key="dvp_rank", label=f"Rank allowing {lab} to {grp} (1 = fewest, of {teams})",
                         value=f["dvp_rank"]))
    return rows


def _games_db():
    from .games import store as gstore
    if not os.environ.get("SPORTS_DB_DSN") and not (config.DATA_DIR / "games.db").exists():
        return None
    return gstore.connect()


def _snap_share(hist_path, athlete_ids, as_of):
    """{espn athlete id: {"snap_share_l3", "snap_share_season", "games", "through"}} from nflverse snap counts."""
    out = {}
    if not athlete_ids or not hist_path.exists():
        return out
    cut = (parse_utc(as_of) - timedelta(hours=SNAP_KNOWN_AFTER_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(f"file:{hist_path.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        ids = sorted(athlete_ids)
        for i in range(0, len(ids), 400):
            part = ids[i:i + 400]
            rows = con.execute(
                "SELECT i.espn_id, p.offense_pct, g.start_utc, g.season FROM nfl_ids i JOIN nfl_pg p ON p.player_id = "
                "i.gsis_id JOIN games g ON g.league = 'nfl' AND g.game_id = p.game_id WHERE g.status = 'final' AND "
                f"g.start_utc <= ? AND p.offense_pct IS NOT NULL AND i.espn_id IN ({','.join('?' * len(part))}) "
                "ORDER BY g.start_utc", [cut] + part).fetchall()
            by = {}
            for esp, pct, st, season in rows:
                by.setdefault(str(esp), []).append((pct, st, season))
            for esp, xs in by.items():
                season = xs[-1][2]
                sv = [x[0] for x in xs if x[2] == season]
                l3 = [x[0] for x in xs[-3:]]
                out[esp] = {"snap_share_l3": round(sum(l3) / len(l3), 4), "snap_share_season": round(sum(sv) / len(sv), 4),
                            "games_season": len(sv), "through": xs[-1][1]}
    finally:
        con.close()
    return out


def build(league, fam, now, wanted, hist_path=None):
    """wanted: {player record id: {"team_id", "opp_id", "event_id", "start_utc", "home", "stats": [..]}}
    -> ({player id: section}, summary). A section: {"label", "contract", "as_of", "by_stat": {stat: [{key, label,
    group, value, source, status}]}, "missing": {stat: {key: reason}}, "not_held": [{factor, reason}]}."""
    t0 = time.time()
    summ = {"version": ENGINE_VERSION, "players": 0, "computed": 0, "state": "ok"}
    out = {}
    if not wanted:
        return out, dict(summ, state="no players")
    try:
        db = _games_db()
    except Exception as e:                                       # noqa: BLE001 - context never breaks a build
        db, summ["state"] = None, f"games store unavailable: {type(e).__name__}"
    snaps = {}
    if fam == "football":
        try:
            snaps = _snap_share(hist_path or (config.DATA_DIR / "model" / "fwd" / "history.db"),
                                {_tail(p) for p in wanted}, now)
        except (sqlite3.Error, OSError) as e:
            summ["snap_share"] = f"unavailable: {type(e).__name__}"
    data = None
    if db is not None:
        try:
            from .games import contract, factors
            data = factors.LeagueData(db, league, parse_utc(now))      # ONE load per board
            reg = contract.REGISTRY[league]
        except Exception as e:                                   # noqa: BLE001
            summ["state"] = f"engine failed: {type(e).__name__}: {str(e)[:120]}"
            data = None
    for pid, w in wanted.items():
        ath = _tail(pid)
        sec = {"label": CONTEXT_LABEL, "contract": None, "as_of": now, "by_stat": {}, "missing": {},
               "not_held": [{"factor": f, "reason": r} for f, r in NOT_HELD[fam]]}
        if data is not None and w.get("event_id") and w.get("team_id") and w.get("opp_id"):
            for stat in w.get("stats") or ():
                try:
                    pay = factors.compute(db, data, ath, _tail(w["team_id"]), _tail(w["opp_id"]), _tail(w["event_id"]),
                                          w["start_utc"], stat, home=w.get("home"))
                except Exception as e:                           # noqa: BLE001 - one player never stops the board
                    sec["missing"][stat] = {"*": f"engine error: {type(e).__name__}"}
                    continue
                summ["computed"] += 1
                sec["contract"], sec["as_of"] = pay["contract"], pay["as_of"]
                rows = []
                for k, v in pay["factors"].items():
                    spec = reg.get(k)
                    if spec is None:                             # helper fields (e.g. blowout_strike, provider)
                        continue
                    rows.append({"key": k, "label": spec["label"], "group": spec["group"], "value": v,
                                 "source": spec["source"], "status": spec["status"]})
                rows += _dvp_rows(pay["factors"], fam, stat)
                for r in rows:
                    if r["key"] == "on_off_net":
                        r["label"] = ON_OFF_LABEL
                        r["n"] = pay["factors"].get("on_off_games")
                        r["on_court_min"] = pay["factors"].get("on_off_on_min")
                sec["by_stat"][stat] = rows
                sec["missing"][stat] = {k: r for k, r in pay["missing"].items()
                                        if (reg.get(k) or {}).get("status") != "unavailable"}
        elif data is None:
            sec["why_none"] = summ["state"] if summ["state"] != "ok" else "game store not present on this host"
        else:
            sec["why_none"] = "no upcoming game for this player in the store"
        sn = snaps.get(ath)
        if sn:
            src = "nflverse snap counts (PFR, CC-BY 4.0)"
            extra = [{"key": "snap_share_l3", "label": "Snap share L3", "group": "Usage & role",
                      "value": sn["snap_share_l3"], "source": src, "status": "collected", "through": sn["through"]},
                     {"key": "snap_share_season", "label": "Snap share season", "group": "Usage & role",
                      "value": sn["snap_share_season"], "source": src, "status": "collected", "through": sn["through"],
                      "n": sn["games_season"]}]
            for stat in (w.get("stats") or ()):
                sec["by_stat"].setdefault(stat, [])
                sec["by_stat"][stat] = [r for r in sec["by_stat"][stat] if r["key"] != "snap_share"] + extra
                sec["missing"].get(stat, {}).pop("snap_share", None)
        out[pid] = sec
        summ["players"] += 1
    if db is not None:
        try:
            db.close()
        except Exception:                                       # noqa: BLE001
            pass
    del data
    summ["build_s"] = round(time.time() - t0, 2)
    return out, summ


# ------------------------------------------------------------------------------------- scheduled job (worker)
RESULT_MAX_AGE_H = 2


class _Sections:
    """{player id: section} read one player file at a time (the snapshot build holds one section, not all)."""

    def __init__(self, d, pids):
        self.d, self.pids = d, pids

    def get(self, pid, default=None):
        import json
        if pid not in self.pids:
            return default
        from .snapshots import safe_name
        try:
            return json.loads((self.d / f"{safe_name(pid)}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default


def read_result(root, now, wanted_pids):
    """The latest player-context job result for this board -> ({pid: section} read lazily per player, summary);
    nothing when the result is older than RESULT_MAX_AGE_H."""
    import json
    try:
        res = json.loads((root / "player_context.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {"state": "waiting for the player-context job"}
    age_h = (parse_utc(now) - parse_utc(res["as_of"])).total_seconds() / 3600
    if age_h > RESULT_MAX_AGE_H:
        return {}, dict(res.get("summary") or {}, state=f"stale: player-context result {age_h:.1f} h old")
    return (_Sections(root / "context", set(res.get("pids") or ()) & set(wanted_pids)),
            dict(res.get("summary") or {}, computed_at=res["as_of"]))


def main(argv=None):
    """SPORTS_INGEST=1 python -m sports.player_context build [nba nfl] - for each league, the players the last board
    build wanted (<snapshots>/<league>/context_wanted.json) -> <snapshots>/<league>/player_context.json."""
    import argparse
    import json
    import sys
    import tempfile
    from . import flags
    from .contracts import now_utc
    from .snapshots import FAMILY, safe_name, snap_root
    ap = argparse.ArgumentParser(prog="python -m sports.player_context")
    ap.add_argument("cmd", choices=("build",))
    ap.add_argument("leagues", nargs="*", default=["nba", "nfl"])
    a = ap.parse_args(argv)
    flags.require("SPORTS_INGEST")
    out = {}
    for lg in a.leagues:
        root = snap_root() / lg
        try:
            w = json.loads((root / "context_wanted.json").read_text(encoding="utf-8"))["wanted"]
        except (OSError, ValueError, KeyError):
            out[lg] = {"state": "no board build yet"}
            continue
        now = now_utc()
        by_pid, summ = build(lg, FAMILY[lg], now, w)
        cdir = root / "context"
        cdir.mkdir(parents=True, exist_ok=True)

        def put(path, obj):
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, separators=(",", ":"), default=str)
            os.replace(tmp, path)
        names = set()
        for pid, sec in by_pid.items():
            names.add(f"{safe_name(pid)}.json")
            put(cdir / f"{safe_name(pid)}.json", sec)
        for f in cdir.glob("*.json"):                   # players no longer on the board
            if f.name not in names:
                f.unlink()
        put(root / "player_context.json", {"as_of": now, "summary": summ, "pids": sorted(by_pid)})
        out[lg] = summ
    print(json.dumps(out, indent=1, default=str), flush=True)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
