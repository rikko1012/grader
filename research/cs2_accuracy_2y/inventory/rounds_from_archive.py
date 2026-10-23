"""Round history (INCLUDING overtime) from ARCHIVED HLTV map-stats overview pages already on disk.

Why: the production scraper parser (hltv_scraper/parsers/mapstats.py::_rounds) returns no rounds when a page has
more than two round-history rows, i.e. for EVERY overtime map (regulation box + "round-history-overtime" box = 4
rows), so map_rounds holds 0 rows for all overtime maps. The archived pages contain the full history: one icon per
round, icon = winner side + win type, title = running score "a-b".

Sources (read-only, no network except the bounded production SELECT in step 2):
  1. the verified archive C:/Users/tikic/cs2_scout/exports/hltv_mapstats_pages_2026-10-15 (part-*.tar + .csv)
  2. pages fetched after that export whose html_gz is still in the production DB (SELECT by explicit id list)
Output: DATA/rounds_archive.sqlite (local research DB; never the production DB).

Every map is validated: running-score titles must count up exactly round by round, the final per-team wins must
equal the map score stored in the HLTV DB (matches table), and where the scraper stored map_rounds (regulation
maps) our rows must equal them exactly. Nothing is inferred: a page that fails any check gets status != 'ok' and
its rounds go to rounds_rejected only.

usage: python rounds_from_archive.py [--limit N] [--no-db]
"""
import argparse, csv, glob, gzip, os, re, sqlite3, sys, tarfile, time

from bs4 import BeautifulSoup

DATA = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\cs2acc\data"
ARCH = r"C:\Users\tikic\cs2_scout\exports\hltv_mapstats_pages_2026-10-15"
SRC = os.path.join(DATA, "hltv_window.sqlite")
OUT = os.path.join(DATA, "rounds_archive.sqlite")
OUTCOMES = {"ct_win": ("ct", "elimination"), "t_win": ("t", "elimination"), "bomb_exploded": ("t", "bomb"),
            "bomb_defused": ("ct", "defuse"), "stopwatch": ("ct", "time")}
ICON = re.compile(r"/([a-zA-Z_]+)\.svg")
TITLE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")
BOX = re.compile(r'<div class="standard-box round-history-con[^"]*">')


class Bad(ValueError):
    pass


def parse(html):
    """-> (team_a, team_b, [(round, half, ot, 'a'|'b', side, how, title)])"""
    starts = [m.start() for m in BOX.finditer(html)]
    if not starts:
        raise Bad("no round history on the page")
    # each box is a flat div: cut from its start to the next box (or a generous tail for the last one)
    out, names, rnd, half = [], None, 0, 0
    for bi, s in enumerate(starts):
        e = starts[bi + 1] if bi + 1 < len(starts) else s + 30000
        box = BeautifulSoup(html[s:e], "html.parser").select_one("div.round-history-con")
        ot = 1 if "round-history-overtime" in (box.get("class") or []) else 0
        rows = box.select(".round-history-team-row")
        if len(rows) != 2:
            raise Bad(f"box {bi}: {len(rows)} team rows")
        per = []
        for row in rows:
            logo = row.select_one(".round-history-team")
            team = (logo.get("title") or logo.get("alt") or "").strip() if logo else ""
            h, icons = 0, []
            for ch in row.children:
                if getattr(ch, "name", None) is None:
                    continue
                cl = ch.get("class") or []
                if "round-history-bar" in cl:
                    h += 1
                elif "round-history-outcome" in cl:
                    m = ICON.search(ch.get("src") or "")
                    ic = m.group(1) if m else None
                    icons.append((max(h, 1), None if ic in (None, "emptyHistory") else ic, ch.get("title")))
            per.append((team, icons))
        (ta, a), (tb, b) = per
        if names is None:
            names = (ta, tb)
        elif (ta, tb) != names:
            raise Bad(f"box {bi}: team order differs")
        if len(a) != len(b):
            raise Bad(f"box {bi}: rows differ in length")
        halves_here = 0
        for n, ((ha, ia, tia), (hb, ib, tib)) in enumerate(zip(a, b)):
            if ia is None and ib is None:
                if any(x or y for (_, x, _), (_, y, _) in zip(a[n + 1:], b[n + 1:])):
                    raise Bad(f"box {bi} slot {n}: empty slot before a played round")
                break
            if ia and ib:
                raise Bad(f"box {bi} slot {n}: both teams won")
            w, ic, ti = ("a", ia, tia) if ia else ("b", ib, tib)
            if ic not in OUTCOMES:
                raise Bad(f"box {bi} slot {n}: unknown icon {ic!r}")
            rnd += 1
            halves_here = max(halves_here, ha)
            side, how = OUTCOMES[ic]
            out.append((rnd, half + ha, ot, w, side, how, ti))
        half += halves_here
    if not out:
        raise Bad("round history present but empty")
    sa = sb = 0
    for r in out:
        sa, sb = (sa + 1, sb) if r[3] == "a" else (sa, sb + 1)
        m = TITLE.match(r[6] or "")
        if not m or (int(m.group(1)), int(m.group(2))) != (sa, sb):
            raise Bad(f"round {r[0]}: title {r[6]!r} != running score {sa}-{sb}")
    return names[0], names[1], out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()
    src = sqlite3.connect(SRC)
    maps = {ms: (mid, mapid) for ms, mid, mapid in
            src.execute("select mapstatsid, matchid, mapid from match_maps where mapstatsid is not null")}
    sc = {}
    for mid, tid, mapid, score in src.execute("select matchid, teamid, mapid, score from matches where sideid=0 and mapid<>0"):
        sc.setdefault((mid, mapid), {})[tid] = score
    tname = {t: (n or "").strip() for t, n in src.execute("select teamid, name from teams")}
    dbr = {}
    for ms, rn, w, side, how in src.execute("select mapstatsid, round, winnerid, winner_side, win_type from map_rounds"):
        dbr.setdefault(ms, []).append((rn, w, side, how))
    if os.path.exists(OUT):
        os.remove(OUT)
    o = sqlite3.connect(OUT)
    o.executescript("""
      create table rounds (mapstatsid int, matchid int, round int, half int, ot int, winnerid int, winner_name text,
                           winner_side text, win_type text, score_title text, primary key (mapstatsid, round));
      create table rounds_rejected (mapstatsid int, round int, half int, ot int, winner_name text, winner_side text,
                           win_type text, score_title text);
      create table pages (mapstatsid int primary key, matchid int, source text, status text, error text,
                          n_rounds int, n_ot_rounds int, team_a text, team_b text, teamid_a int, teamid_b int,
                          score_check text, db_rounds int, db_match text);
    """)
    stat = {}

    def page(ms, *vals):
        o.execute("insert into pages values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (ms, maps[ms][0]) + vals)

    def handle(ms, html, source):
        mid, mapid = maps[ms]
        score = sc.get((mid, mapid), {})
        try:
            ta, tb, rr = parse(html)
        except Bad as e:
            page(ms, source, "parse_error", str(e)[:200], 0, 0, None, None, None, None, None, len(dbr.get(ms, [])), None)
            stat["parse_error"] = stat.get("parse_error", 0) + 1
            return
        ids = list(score)
        ida = next((t for t in ids if tname.get(t) == ta), None)
        idb = next((t for t in ids if tname.get(t) == tb), None)
        if len(ids) == 2:   # one exact name resolves the other by elimination
            if ida is None and idb is not None:
                ida = next(t for t in ids if t != idb)
            if idb is None and ida is not None:
                idb = next(t for t in ids if t != ida)
        wa = sum(1 for r in rr if r[3] == "a")
        wb = len(rr) - wa
        if ida is None or idb is None or ida == idb:
            chk = status = "team_unresolved"
        elif score.get(ida) == wa and score.get(idb) == wb:
            chk = status = "ok"
        else:
            chk, status = f"db {score.get(ida)}-{score.get(idb)} page {wa}-{wb}", "score_mismatch"
        dm = None
        if ms in dbr and status == "ok":
            mine = [(r[0], ida if r[3] == "a" else idb, r[4], r[5]) for r in rr]
            dm = "equal" if sorted(dbr[ms]) == mine else "DIFFERENT"
            if dm == "DIFFERENT":
                status = "db_conflict"
        page(ms, source, status, None if status == "ok" else chk, len(rr), sum(r[2] for r in rr), ta, tb, ida, idb, chk,
             len(dbr.get(ms, [])), dm)
        for r in rr:
            if status == "ok":
                o.execute("insert into rounds values (?,?,?,?,?,?,?,?,?,?)",
                          (ms, mid, r[0], r[1], r[2], ida if r[3] == "a" else idb, ta if r[3] == "a" else tb, r[4], r[5], r[6]))
            else:
                o.execute("insert into rounds_rejected values (?,?,?,?,?,?,?,?)",
                          (ms, r[0], r[1], r[2], ta if r[3] == "a" else tb, r[4], r[5], r[6]))
        stat[status] = stat.get(status, 0) + 1

    done, t0 = set(), time.time()
    for f in sorted(glob.glob(os.path.join(ARCH, "part-*.csv"))):
        want = {}
        for r in csv.DictReader(open(f, newline="")):
            ms = int(r["mapstatsid"])
            if r["kind"] == "overview" and ms in maps and ms not in done:
                want[r["member"]] = ms
        if want:
            with tarfile.open(f[:-4] + ".tar") as t:
                for m in t:
                    ms = want.get(m.name)
                    if ms is None or ms in done:
                        continue
                    html = gzip.decompress(t.extractfile(m).read()).decode("utf-8", "replace")
                    handle(ms, html, "archive:" + os.path.basename(f[:-4]) + ".tar")
                    done.add(ms)
                    if args.limit and len(done) >= args.limit:
                        break
            o.commit()
            print(os.path.basename(f), len(done), stat, f"{time.time() - t0:.0f}s", flush=True)
        if args.limit and len(done) >= args.limit:
            break
    if not args.no_db and not args.limit:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from db import q
        rest = [ms for ms, h in src.execute("select mapstatsid, html_in_db from mapstats_pages where kind='overview'")
                if h and ms in maps and ms not in done]
        for i in range(0, len(rest), 100):
            for row in q("select mapstatsid, html_gz from mapstats_pages where kind='overview' and mapstatsid = any(%s) "
                         "and html_gz is not null", (rest[i:i + 100],)):
                handle(row["mapstatsid"], gzip.decompress(bytes(row["html_gz"])).decode("utf-8", "replace"), "db:mapstats_pages")
                done.add(row["mapstatsid"])
            o.commit()
            time.sleep(0.3)
    if not args.limit:
        for ms in maps:
            if ms not in done:
                page(ms, None, "no_page", "no archived or stored overview page", 0, 0, None, None, None, None, None,
                     len(dbr.get(ms, [])), None)
    o.commit()
    print("FINAL", stat, f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
