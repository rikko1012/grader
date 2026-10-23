"""Write inventory/MANIFEST.json: every dataset this phase created or relies on, with path, source, query, row counts,
sha256 and created_at (file mtime, UTC)."""
import datetime as dt, hashlib, json, os, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
SP = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
DATA = os.path.join(SP, "cs2acc", "data")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sqlite_counts(p):
    c = sqlite3.connect(p)
    return {t: c.execute(f"select count(*) from {t}").fetchone()[0]
            for (t,) in c.execute("select name from sqlite_master where type='table'")}


def json_rows(p):
    d = json.load(open(p))
    if isinstance(d, list):
        return {"rows": len(d)}
    return {k: len(v) for k, v in d.items() if isinstance(v, list)}


ENTRIES = [
    ("hltv_window.sqlite", "production HLTV Postgres (Render hltv-db), read-only session, statement_timeout 120 s",
     "export_hltv.py: per month 2024-10-01..2026-10-31, SELECT <cols> FROM <table> JOIN match_overview ON matchid WHERE date in month; "
     "dimension tables whole; html_gz never exported (metadata only)"),
    ("rounds_archive.sqlite", "archived HLTV map-stats overview pages (C:/Users/tikic/cs2_scout/exports/hltv_mapstats_pages_2026-10-15, "
     "verified copy of mapstats_pages.html_gz) + 726 bodies still in the production DB (SELECT by id list)",
     "rounds_from_archive.py: round-history icons incl. the overtime box, validated vs running-score titles, HLTV map score and map_rounds"),
    ("hltv_props.sqlite", "production HLTV Postgres", "export_hltv_props.py: prop_grades, prop_lines, prop_status, upcoming_matches, "
     "thunderpick_snapshots (no body)"),
    ("forecasts_lite.json", "production admin /admin/forecasts_page.json (keyset pages of 2000, fixed cutoff, no inputs)", "fetch_admin.py forecasts"),
    ("p1_units.json", "production admin /admin/learning/p1_units.json (paged)", "fetch_admin.py p1"),
    ("fh1_records.json", "production admin /admin/fh1.json (keyset pages of 1000, no inputs)", "fetch_admin.py fh1"),
    ("admin_status.json", "production admin /admin/moneylines_status, /admin/thunderpick.json, /admin/fh1.json?limit=1", "fetch_admin.py status"),
    ("ud_moves_since_2026-09-01.json", "production admin /admin/ud_moves?since=2026-09-01 (Render ud_moves.db)", "fetch_admin.py udmoves 2026-09-01"),
    ("ud_moves_since_2026-10-22.json", "production admin /admin/ud_moves?since=2026-10-22 (volume probe)", "fetch_admin.py udmoves 2026-10-22"),
    ("props_data/history.csv", "github rikko1012/hltv-scraper branch props-data (git show; commit in props_data/COMMIT)", "every UD/PP line when first seen and on each move"),
    ("props_data/graded.csv", "same branch", "closing line per prop graded vs HLTV"),
    ("props_data/scout.csv", "same branch", "board now with bumps"),
    ("props_data/latest.csv", "same branch", "current lines"),
    ("picks_local_copy.db", "C:/Users/tikic/cs2_scout/picks.db (local dev DB, copied read-only)", "file copy"),
]
EXTERNAL = [  # earlier-study files used as-is (not copied)
    (os.path.join(SP, "picks_now.json"), "Render /admin/picks.json export pulled 2026-10-19/20 by an earlier session (picks to 2026-10-20 00:40 UTC)"),
    (os.path.join(SP, "picks_after_recheck.json"), "Railway picks export 2026-10-08 23:56 UTC (earlier session)"),
    (os.path.join(SP, "forecasts_now2.json"), "Railway forecast log 2026-10-07 21:11 .. 2026-10-08 23:24 UTC (earlier session)"),
    (os.path.join(SP, "ud_moves.json"), "Railway ud_moves 2026-10-04 .. 2026-10-08 (earlier session)"),
    (os.path.join(SP, "ml_research", "dataset.pkl"), "Polymarket/Kalshi series-winner research dataset 2025-10-15..2026-10-11 (DATA_CARD.md)"),
]
out = {"created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "data_dir": DATA, "datasets": [], "external_inputs": []}
for rel, src, query in ENTRIES:
    p = os.path.join(DATA, rel)
    if not os.path.exists(p):
        continue
    rows = sqlite_counts(p) if p.endswith((".sqlite", ".db")) else json_rows(p) if p.endswith(".json") else \
        {"rows": sum(1 for _ in open(p, encoding="utf-8")) - 1}
    out["datasets"].append({"path": p, "source": src, "query": query, "rows": rows, "bytes": os.path.getsize(p), "sha256": sha(p),
                            "created_at": dt.datetime.fromtimestamp(os.path.getmtime(p), dt.timezone.utc).isoformat()})
for p, src in EXTERNAL:
    if os.path.exists(p):
        out["external_inputs"].append({"path": p, "source": src, "bytes": os.path.getsize(p), "sha256": sha(p),
                                       "modified_at": dt.datetime.fromtimestamp(os.path.getmtime(p), dt.timezone.utc).isoformat()})
json.dump(out, open(os.path.join(HERE, "MANIFEST.json"), "w"), indent=1)
json.dump(out, open(os.path.join(DATA, "MANIFEST.json"), "w"), indent=1)
print(len(out["datasets"]), "datasets")
