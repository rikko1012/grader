"""OT-round restore reconciliation (read-only). Production HLTV Postgres: bounded SELECTs only (READ ONLY session,
statement_timeout 120 s). Archive: the 2026-10-15 page archive (part-*.csv manifests, part-*.tar pages). No writes
anywhere except the output folder.

    python -I reconcile.py --out <scratch dir> [--scraper <hltv-scraper clone>] [--archive <dir>]

Writes <out>/ot_maps.csv (one row per OT map, all time) and <out>/ot_reconcile.json (every count in OT_RECONCILIATION.md).

Definitions (identical to inventory/coverage_report.py and the scraper import):
* a map = match_maps row with a mapstatsid whose match is in match_overview;
* OT map = exactly 2 team rows in `matches` (sideid 0 = total, mapid = the map) and their scores sum to > 24;
* date = match_overview.date (UTC date); windows: inventory 2024-10-22..2026-10-21, Codex 2024-10-23..2026-10-22;
* complete = map_rounds rows == score total, rounds numbered 1..n, each team's round wins == its map score.
"""
import argparse, csv, glob, gzip, hashlib, json, os, sys, tarfile
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "inventory"))
import db  # noqa: E402  (read-only helper: READ ONLY session, statement_timeout)

SCRATCH = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
ARCHIVE = r"C:\Users\tikic\cs2_scout\exports\hltv_mapstats_pages_2026-10-15"
WINDOWS = {"inventory_2024-10-22..2026-10-21": ("2024-10-22", "2026-10-21"),
           "codex_2024-10-23..2026-10-22": ("2024-10-23", "2026-10-22")}
CLOUD_FROM = "2026-10-23 06:00:00+00"     # first re-download of the cloud scan: 06:38:55 UTC (status rows); none 00-06 UTC

MAPS_SQL = """
WITH sc AS (SELECT mm.mapstatsid, mm.matchid, sum(m.score) AS total, count(*) AS teams,
                   json_object_agg(m.teamid, m.score) AS score
            FROM match_maps mm JOIN match_overview mo USING (matchid)
            JOIN matches m ON m.matchid = mm.matchid AND m.mapid = mm.mapid AND m.sideid = 0
            WHERE mm.mapstatsid IS NOT NULL GROUP BY mm.mapstatsid, mm.matchid),
     r AS (SELECT mapstatsid, count(*) AS n, count(DISTINCT round) AS nd, min(round) AS rmin, max(round) AS rmax,
                  count(*) FILTER (WHERE half >= 3) AS n_ot, count(DISTINCT matchid) AS n_matchids, min(matchid) AS r_matchid,
                  md5(string_agg(concat_ws(',', matchid, round, half, winnerid, winner_side, win_type), ';' ORDER BY round)) AS md5
           FROM map_rounds GROUP BY mapstatsid)
SELECT sc.mapstatsid, sc.matchid, mo.date::text AS date, mo.forfeited_maps, sc.total, sc.teams, sc.score::text AS score,
       r.n, r.nd, r.rmin, r.rmax, r.n_ot, r.n_matchids, r.r_matchid, r.md5,
       pg.parser_version, pg.fetched_at::text AS fetched_at, (pg.html_gz IS NOT NULL) AS has_html, pg.matchid AS page_matchid,
       st.attempts, left(st.last_error, 120) AS last_error, st.updated_at::text AS status_updated,
       (SELECT string_agg(DISTINCT di.code, '|') FROM data_issues di WHERE di.matchid = sc.matchid) AS issues
FROM sc JOIN match_overview mo ON mo.matchid = sc.matchid
LEFT JOIN r ON r.mapstatsid = sc.mapstatsid
LEFT JOIN mapstats_pages pg ON pg.mapstatsid = sc.mapstatsid AND pg.kind = 'overview'
LEFT JOIN mapstats_page_status st ON st.mapstatsid = sc.mapstatsid AND st.kind = 'overview'
"""


def snapshot(path):
    d = json.load(open(path))
    return {int(k): v for k, v in d["maps"].items()}, {k: v for k, v in d.items() if k != "maps"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(SCRATCH, "close_otcounts"))
    ap.add_argument("--scraper", default=os.path.join(SCRATCH, "night_scraper", "hltv-scraper-night"))
    ap.add_argument("--archive", default=ARCHIVE)
    ap.add_argument("--snapshots", default=os.path.join(SCRATCH, "night_scraper"))
    ap.add_argument("--inventory", default=os.path.join(SCRATCH, "cs2acc", "data", "rounds_archive.sqlite"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    sys.path.insert(0, args.scraper)
    from hltv_scraper.parsers.common import make_soup
    from hltv_scraper.parsers.mapstats import _rounds
    from hltv_scraper.db import Database
    res = {}

    # ---- 1. every map, one row (bounded: ~63k rows) --------------------------------------------------------------------
    rows = db.q(MAPS_SQL)
    for r in rows:
        r["score"] = {int(k): v for k, v in json.loads(r["score"]).items()}
    allmaps = {r["mapstatsid"]: r for r in rows}
    ot = {s: r for s, r in allmaps.items() if r["teams"] == 2 and r["total"] > 24}
    reg = {s: r for s, r in allmaps.items() if r["teams"] == 2 and r["total"] <= 24}
    res["maps_all_time"] = {"maps_with_mapstatsid": len(allmaps), "two_team_scored": len(ot) + len(reg),
                            "not_two_team_scored": len(allmaps) - len(ot) - len(reg), "ot": len(ot), "regulation": len(reg),
                            "date_range_ot": [min(r["date"] for r in ot.values()), max(r["date"] for r in ot.values())]}

    # per-team wins on OT maps (bounded: OT maps only)
    wins = defaultdict(dict)
    for w in db.q("SELECT mapstatsid, winnerid, count(*) AS n FROM map_rounds WHERE mapstatsid = ANY(%s) GROUP BY 1, 2",
                  (list(ot),)):
        wins[w["mapstatsid"]][w["winnerid"]] = w["n"]

    def state(r):
        if not r["n"]:
            return "none"
        ok = (r["n"] == r["total"] == r["nd"] and r["rmin"] == 1 and r["rmax"] == r["total"]
              and wins.get(r["mapstatsid"], {}) == r["score"] and r["n_matchids"] == 1 and r["r_matchid"] == r["matchid"])
        return "complete" if ok else "partial"

    for r in ot.values():
        r["state"] = state(r)
    for r in reg.values():
        r["state"] = "complete" if r["n"] == r["total"] else ("none" if not r["n"] else "partial")
    res["ot_state_all_time"] = dict(Counter(r["state"] for r in ot.values()))
    res["reg_state_all_time"] = dict(Counter(r["state"] for r in reg.values()))
    res["ot_not_complete"] = [{k: r[k] for k in ("mapstatsid", "matchid", "date", "total", "score", "n", "n_ot", "has_html",
                                                   "fetched_at", "parser_version", "last_error", "issues")}
                              for r in ot.values() if r["state"] != "complete"]
    res["ot_rounds_stored_all_time"] = sum(r["n_ot"] or 0 for r in ot.values())
    res["ot_rows_stored_all_time"] = sum(r["n"] or 0 for r in ot.values())

    # ---- 2. integrity: duplicate keys, orphan rows -----------------------------------------------------------------------
    res["dup_keys"] = db.q("SELECT count(*) AS n FROM (SELECT mapstatsid, round FROM map_rounds GROUP BY 1, 2 "
                           "HAVING count(*) > 1) d")[0]["n"]
    res["pk"] = db.q("SELECT indexdef FROM pg_indexes WHERE tablename = 'map_rounds' AND indexname = 'map_rounds_pkey'")
    res["orphan_round_maps"] = db.q("SELECT count(DISTINCT r.mapstatsid) AS n FROM map_rounds r LEFT JOIN match_maps mm "
                                    "ON mm.mapstatsid = r.mapstatsid WHERE mm.mapstatsid IS NULL")[0]["n"]
    res["map_rounds_total_rows"] = db.q("SELECT count(*) AS n FROM map_rounds")[0]["n"]
    res["data_revisions"] = db.q("SELECT id, name, applied_at::text, rows, note FROM data_revisions ORDER BY id")

    # ---- 3. windows ----------------------------------------------------------------------------------------------------
    win = {}
    for name, (a, b) in WINDOWS.items():
        sel = [r for r in ot.values() if a <= r["date"] <= b]
        win[name] = {"ot": len(sel), "states": dict(Counter(r["state"] for r in sel)),
                     "forfeit_flag_matches": sum(1 for r in sel if (r["forfeited_maps"] or 0) > 0),
                     "with_data_issues": sum(1 for r in sel if r["issues"]),
                     "maps_all": sum(1 for r in allmaps.values() if a <= r["date"] <= b)}
    sel = [r for r in ot.values() if r["date"] < "2024-10-22"]
    win["before_2024-10-22"] = {"ot": len(sel), "states": dict(Counter(r["state"] for r in sel))}
    sel = [r for r in ot.values() if r["date"] > "2026-10-21"]
    win["after_2026-10-21"] = {"ot": len(sel), "states": dict(Counter(r["state"] for r in sel)),
                               "dates": dict(Counter(r["date"] for r in sel))}
    res["windows"] = win

    # ---- 4. snapshots: who wrote what ------------------------------------------------------------------------------------
    snaps = {}
    for nm in ("before", "before2", "after"):
        p = os.path.join(args.snapshots, nm + ".json")
        if os.path.exists(p):
            snaps[nm] = snapshot(p)
    b1, b2, af = (snaps[k][0] for k in ("before", "before2", "after"))
    otc = {s for s, r in ot.items() if r["state"] == "complete"}
    cloud_page = {s for s, r in ot.items() if r["fetched_at"] and r["fetched_at"] >= "2026-10-23 06:00"}
    html_old = {s for s, r in ot.items() if r["has_html"] and s not in cloud_page}
    arch_only = {s for s, r in ot.items() if not r["has_html"]}
    att = {
        "ot_with_rows_before_0619": sorted(s for s in ot if s in b1),
        "ot_complete_by_0643_(before2)": len(otc & set(b2)),
        "ot_new_rows_0643_to_0700": len({s for s in ot if s in af and s not in b2}),
        "ot_new_rows_after_0700": len({s for s in ot if s not in af and allmaps[s]["n"]}),
        "ot_complete_now": len(otc),
        "complete_now_by_page_source": {
            "page re-downloaded by cloud scan (fetched_at >= 10-23 06:00, html in DB)": len(otc & cloud_page),
            "page html in DB from before 10-23 06:00 (reparsed in place)": len(otc & html_old),
            "page html not in DB (archive only)": len(otc & arch_only)},
    }
    # import set = OT maps with no rows at 06:43, rows by 07:00, page archive-only (html NULL: no scan read it)
    imported = {s for s in ot if s in af and s not in b2 and s in arch_only}
    att["import_set_(new 0643-0700, archive-only page)"] = len(imported)
    att["import_set_ot_rows"] = sum(allmaps[s]["n"] for s in imported)
    att["import_set_ot_rounds"] = sum(allmaps[s]["n_ot"] for s in imported)
    new_0643_0700 = {s for s in ot if s in af and s not in b2}
    att["new_0643_0700_by_page_source"] = {"archive-only": len(new_0643_0700 & arch_only),
                                           "cloud re-download": len(new_0643_0700 & cloud_page),
                                           "old html": len(new_0643_0700 & html_old)}
    # rows changed since the 07:00 snapshot (a later scan rewriting a map)
    att["ot_md5_changed_since_0700"] = sorted(s for s in ot if s in af and af[s][1] != allmaps[s]["md5"])
    att["reg_md5_changed_since_0619"] = len([s for s in reg if s in b1 and b1[s][1] != allmaps[s]["md5"]])
    att["reg_maps_in_0619"] = len([s for s in reg if s in b1])
    att["snapshot_summaries"] = {k: v[1] for k, v in snaps.items()}
    res["attribution"] = att

    # ---- 5. archive manifest -------------------------------------------------------------------------------------------
    man = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(args.archive, "part-*.csv"))):
        with open(f, newline="", encoding="utf-8") as fh:
            for m in csv.DictReader(fh):
                if m["kind"] == "overview":
                    man[int(m["mapstatsid"])].append(m)
    res["manifest"] = {
        "overview_entries": sum(len(v) for v in man.values()), "overview_ids": len(man),
        "ids_with_more_than_one_entry": sum(1 for v in man.values() if len(v) > 1),
        "ot_maps_in_manifest": len(set(ot) & set(man)),
        "ot_complete_not_in_manifest": sorted(s for s in otc if s not in man),
        "import_set_not_in_manifest": sorted(s for s in imported if s not in man),
        "import_set_multi_entry": sorted(s for s in imported if len(man.get(s, [])) > 1),
        "import_set_matchid_mismatch": sorted(s for s in imported if man.get(s) and int(man[s][0]["matchid"]) != allmaps[s]["matchid"]),
        "ot_maps_not_in_manifest_dates": dict(Counter(ot[s]["date"] for s in ot if s not in man)),
    }

    # ---- 6. re-parse every OT page we hold (archive + DB html) and compare with the stored rows ------------------------
    teams = defaultdict(dict)
    for t in db.q("SELECT DISTINCT m.matchid, t.name, t.teamid FROM matches m JOIN teams t USING (teamid) "
                  "WHERE m.mapid = 0 AND m.matchid = ANY(%s)", (list({r["matchid"] for r in ot.values()}),)):
        teams[t["matchid"]][t["name"]] = t["teamid"]
    stored = defaultdict(list)
    ids = sorted(ot)
    for i in range(0, len(ids), 2000):
        for x in db.q("SELECT mapstatsid, round, half, winnerid, winner_side, win_type FROM map_rounds "
                      "WHERE mapstatsid = ANY(%s) ORDER BY mapstatsid, round", (ids[i:i + 2000],)):
            stored[x["mapstatsid"]].append((x["round"], x["half"], x["winnerid"], x["winner_side"], x["win_type"]))

    def compare(sid, html_bytes):
        try:
            html = gzip.decompress(html_bytes).decode("utf-8", "replace")
            rr = _rounds(make_soup(html))
        except Exception as e:
            return "parse_error:" + type(e).__name__ + ": " + str(e)[:100]
        r = ot[sid]
        look = Database._team_lookup(teams.get(r["matchid"], {}))
        idmap = {n: look(n) for n in {x.winner for x in rr}}
        mine = [(x.round, x.half, idmap[x.winner], x.winner_side, x.win_type) for x in rr]
        won = Counter(m[2] for m in mine)
        if dict(won) != r["score"]:
            return f"page_vs_score: page {dict(won)} score {r['score']}"
        if not stored.get(sid):
            return "no_stored_rows"
        return "equal" if stored[sid] == mine else "DIFFERENT"

    cmp_arch, cmp_db = {}, {}
    for f in sorted(glob.glob(os.path.join(args.archive, "part-*.tar"))):
        want = {f"overview/{s}.html.gz": s for s in ot if s in man and man[s][0]["part"] == os.path.basename(f)}
        if not want:
            continue
        md5 = {s: man[s][0]["md5"] for s in want.values()}
        with tarfile.open(f) as t:
            for m in t:
                s = want.get(m.name)
                if s is None:
                    continue
                data = t.extractfile(m).read()
                cmp_arch[s] = "md5_mismatch" if hashlib.md5(data).hexdigest() != md5[s] else compare(s, data)
        print(os.path.basename(f), len(cmp_arch), flush=True)
    with_html = sorted(s for s in ot if ot[s]["has_html"])
    for i in range(0, len(with_html), 50):
        for x in db.q("SELECT mapstatsid, html_gz FROM mapstats_pages WHERE kind = 'overview' AND mapstatsid = ANY(%s) "
                      "AND html_gz IS NOT NULL", (with_html[i:i + 50],)):
            cmp_db[x["mapstatsid"]] = compare(x["mapstatsid"], bytes(x["html_gz"]))
    print("db pages", len(cmp_db), flush=True)
    both = set(cmp_arch) & set(cmp_db)
    res["reparse"] = {
        "archive_pages_parsed": len(cmp_arch), "archive_outcomes": dict(Counter(v.split(":")[0] for v in cmp_arch.values())),
        "db_pages_parsed": len(cmp_db), "db_outcomes": dict(Counter(v.split(":")[0] for v in cmp_db.values())),
        "maps_with_both_pages": len(both),
        "both_pages_equal_stored": sum(1 for s in both if cmp_arch[s] == cmp_db[s] == "equal"),
        "both_pages_other": {s: [cmp_arch[s], cmp_db[s]] for s in both if not (cmp_arch[s] == cmp_db[s] == "equal")},
        "non_equal_archive": {s: v for s, v in cmp_arch.items() if v != "equal"},
        "non_equal_db": {s: v for s, v in cmp_db.items() if v != "equal"},
        "complete_maps_without_any_page_check": sorted(s for s in otc if s not in cmp_arch and s not in cmp_db),
        "import_set_archive_equal": sum(1 for s in imported if cmp_arch.get(s) == "equal"),
    }

    # ---- 7. inventory file (research copy) ------------------------------------------------------------------------------
    if os.path.exists(args.inventory):
        import sqlite3
        c = sqlite3.connect(args.inventory)
        inv = {ms: (st, n, n_ot) for ms, st, n, n_ot in c.execute("SELECT mapstatsid, status, n_rounds, n_ot_rounds FROM pages")}
        a, b = WINDOWS["inventory_2024-10-22..2026-10-21"]
        winot = {s for s, r in ot.items() if a <= r["date"] <= b}
        res["inventory_file"] = {
            "pages": len(inv), "ot_window_now": len(winot),
            "ot_window_inventory_ok": sum(1 for s in winot if inv.get(s, ("",))[0] == "ok"),
            "ot_window_inventory_status": dict(Counter(inv.get(s, ("absent",))[0] for s in winot)),
            "ot_window_rows_equal_inventory_count": sum(1 for s in winot if s in inv and inv[s][0] == "ok" and inv[s][1] == allmaps[s]["n"]),
            "ot_window_ot_rounds_inventory": sum(inv[s][2] for s in winot if s in inv and inv[s][0] == "ok"),
            "ot_window_ot_rounds_db_now": sum(allmaps[s]["n_ot"] or 0 for s in winot),
        }

    # ---- 8. page-status noise (read only; not changed) ------------------------------------------------------------------
    res["page_status"] = db.q("""SELECT kind, (updated_at >= '2026-10-23 06:30+00' AND updated_at < '2026-10-23 07:30+00') AS cloud_window,
        split_part(last_error, ':', 1) AS err, count(*) AS n, min(updated_at)::text AS first, max(updated_at)::text AS last
        FROM mapstats_page_status GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC""")
    res["page_status_cloud_rows_detail"] = db.q("""SELECT count(*) AS n, count(*) FILTER (WHERE pg.parser_version = 2) AS page_now_v2,
        count(*) FILTER (WHERE r.mapstatsid IS NOT NULL) AS map_has_rounds, count(*) FILTER (WHERE st.not_before > now()) AS still_waiting
        FROM mapstats_page_status st
        LEFT JOIN mapstats_pages pg ON pg.mapstatsid = st.mapstatsid AND pg.kind = st.kind
        LEFT JOIN (SELECT DISTINCT mapstatsid FROM map_rounds) r ON r.mapstatsid = st.mapstatsid
        WHERE st.kind = 'overview' AND st.updated_at >= '2026-10-23 06:30+00' AND st.updated_at < '2026-10-23 07:30+00'""")
    res["pages_refetched_cloud"] = db.q("""SELECT date_trunc('hour', fetched_at)::text AS hour, count(*) AS n
        FROM mapstats_pages WHERE kind = 'overview' AND fetched_at >= '2026-10-23 06:00+00' AND fetched_at < '2026-10-23 08:00+00'
        GROUP BY 1 ORDER BY 1""")
    res["pages_refetched_cloud_range"] = db.q("""SELECT min(fetched_at)::text, max(fetched_at)::text, count(*) AS n,
        count(*) FILTER (WHERE mm.mapstatsid = ANY(%s)) AS ot FROM mapstats_pages pg JOIN match_maps mm USING (mapstatsid)
        WHERE pg.kind = 'overview' AND fetched_at >= '2026-10-23 06:00+00' AND fetched_at < '2026-10-23 08:00+00'""", (list(ot),))

    with open(os.path.join(args.out, "ot_maps.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["mapstatsid", "matchid", "date", "total", "rows", "ot_rows", "state", "has_html", "fetched_at",
                    "in_manifest", "archive_check", "db_check", "in_import_set", "rows_at_0643", "rows_at_0700"])
        for s in sorted(ot):
            r = ot[s]
            w.writerow([s, r["matchid"], r["date"], r["total"], r["n"] or 0, r["n_ot"] or 0, r["state"], r["has_html"],
                        r["fetched_at"], s in man, cmp_arch.get(s, ""), cmp_db.get(s, ""), s in imported, s in b2, s in af])
    json.dump(res, open(os.path.join(args.out, "ot_reconcile.json"), "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k not in ("ot_not_complete",)}, indent=1, default=str)[:20000])


if __name__ == "__main__":
    main()
