"""One-off clean-up of DNP settlements that were really outdated series links (2026-11-09 audit: all 17 recorded LoL DNPs).

A unit whose latest settlement is "dnp" and whose settled forecast's team is not one of the two teams of the series it was
settled on gets a NEW settlement version "link_mismatch" (append-only: lol_settlements keeps the dnp row it supersedes; no
forecast is touched). dnp and link_mismatch are both outside the hit rate, so no hit / miss / grade count moves.

    python -m lol.center.fix_dnp_links            # dry run: lists the units, writes nothing
    python -m lol.center.fix_dnp_links --apply    # writes the new versions (owner approval required)
"""
import argparse
import json

from . import settle, store


def candidates(con):
    """[(unit_key, forecast row dict, series row dict)] - latest settlement dnp, forecast team not in the settled series."""
    out = []
    rows = con.execute("SELECT x.* FROM lol_settlements x JOIN (SELECT unit_key, MAX(version) AS v FROM lol_settlements "
                       "GROUP BY unit_key) z ON z.unit_key = x.unit_key AND z.v = x.version WHERE x.status = 'dnp'").fetchall()
    for s in rows:
        parts = s["unit_key"].split("|")          # platform | market_key (may contain "|") | line
        platform, line = parts[0], float(parts[-1])
        market_key = "|".join(parts[1:-1])
        f = con.execute("SELECT * FROM lol_forecasts WHERE platform = ? AND market_key = ? AND line = ? AND forecast_at < series_start "
                        "ORDER BY id DESC LIMIT 1", (platform, market_key, line)).fetchone()
        ser = con.execute("SELECT * FROM c_series WHERE sid = ?", (s["sid"],)).fetchone()
        if f is None or ser is None or not f["tid"]:
            continue
        if not settle._team_in(con, f["tid"], ser):
            out.append((s["unit_key"], dict(f), dict(ser)))
    return out


def run(con, apply=False):
    found = candidates(con)
    done = []
    for unit, f, ser in found:
        row = {"unit": unit, "player": f["pid"], "forecast_team": f["tid"], "settled_series": ser["sid"],
               "series_teams": [ser["team1"], ser["team2"]], "grade": f["grade_label"]}
        if apply:
            res = settle.record(con, unit, f["platform"], f["stat"], "maps_" + "_".join(f["scope"]),
                                store.canonical_id(con, f["pid"]), ser["sid"], f["line"], tid=f["tid"])
            row["new_status"], row["version"] = res["status"], res.get("version")
        done.append(row)
    return {"apply": apply, "units": len(done), "rows": done}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the new link_mismatch versions (default: dry run)")
    a = ap.parse_args(argv)
    con = store.connect()
    try:
        print(json.dumps(run(con, a.apply), indent=1, default=str))
    finally:
        con.close()


if __name__ == "__main__":
    main()
