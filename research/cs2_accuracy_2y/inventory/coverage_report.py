"""Coverage tables for COVERAGE.md, computed ONLY from the local research copies (no network):
  DATA/hltv_window.sqlite      (export_hltv.py)          DATA/rounds_archive.sqlite (rounds_from_archive.py)
  DATA/hltv_props.sqlite       (export_hltv_props.py)    DATA/props_data/*.csv      (scraper props-data branch)
  DATA/forecasts_lite.json, p1_units.json, fh1_records.json, admin_status.json, ud_moves_since_2026-09-01.json
  scratchpad/picks_now.json (Render picks export, 2026-10-20 00:40 UTC), picks_after_recheck.json (Railway, 2026-10-08)
Writes inventory/coverage_tables.md and inventory/coverage_numbers.json.
Window: match date 2024-10-22 .. 2026-10-21 (UTC date as stored by HLTV)."""
import collections, json, os, sqlite3
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SP = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
DATA = os.path.join(SP, "cs2acc", "data")
W0, W1 = "2024-10-22", "2026-10-21"
OUT, NUM = [], {}


def md(df, title=None, index=True):
    if title:
        OUT.append(f"\n### {title}\n")
    OUT.append(df.to_markdown(index=index) + "\n")


def note(s):
    OUT.append(s + "\n")


c = sqlite3.connect(os.path.join(DATA, "hltv_window.sqlite"))
c.execute(f"attach '{os.path.join(DATA, 'rounds_archive.sqlite')}' as ra")
S = lambda q: pd.read_sql_query(q, c)

# ---------------------------------------------------------------- per map
maps = S(f"""
select mm.matchid, mm.number, mm.mapid, mm.mapstatsid, mo.date, mo.lan, mo.forfeited_maps, mo.event,
  (select sum(score) from matches x where x.matchid=mm.matchid and x.mapid=mm.mapid and x.sideid=0) tot,
  (select count(*) from matches x where x.matchid=mm.matchid and x.mapid=mm.mapid and x.sideid=0) n_score_rows,
  (select count(*) from player_stats p where p.matchid=mm.matchid and p.mapid=mm.mapid and p.sideid=0) n_ps,
  (select count(distinct teamid) from player_stats p where p.matchid=mm.matchid and p.mapid=mm.mapid and p.sideid=0) n_ps_teams,
  (select max(cnt) from (select count(*) cnt from player_stats p where p.matchid=mm.matchid and p.mapid=mm.mapid and p.sideid=0 group by teamid)) max_team_rows,
  (select count(*) from player_stats p where p.matchid=mm.matchid and p.mapid=mm.mapid and p.sideid in (1,2)) n_ps_side,
  (select count(*) from map_headshots h where h.mapstatsid=mm.mapstatsid) n_hs,
  (select count(*) from map_player_stats s where s.mapstatsid=mm.mapstatsid and s.sideid=0) n_mps,
  (select count(*) from map_player_stats s where s.mapstatsid=mm.mapstatsid and s.sideid=0 and s.a is not null and s.kast is not null and s.adr is not null and s.rating is not null) n_mps_full,
  (select count(*) from map_rounds r where r.mapstatsid=mm.mapstatsid) n_rounds_db,
  (select count(*) from map_round_economy e where e.mapstatsid=mm.mapstatsid) n_econ,
  (select status from ra.pages p where p.mapstatsid=mm.mapstatsid) arch_status,
  (select n_rounds from ra.pages p where p.mapstatsid=mm.mapstatsid) arch_rounds,
  (select n_ot_rounds from ra.pages p where p.mapstatsid=mm.mapstatsid) arch_ot_rounds,
  (select source from ra.pages p where p.mapstatsid=mm.mapstatsid) arch_source
from match_maps mm join match_overview mo using(matchid)
where mo.date between '{W0}' and '{W1}'""")
maps["month"] = maps["date"].str[:7]
maps["ot"] = maps["tot"] > 24
maps["scoreboard_full"] = (maps.n_ps == 10) & (maps.n_ps_teams == 2) & (maps.max_team_rows == 5)
maps["hs_full"] = maps.n_hs == 10
maps["detail_full"] = maps.n_mps_full == 10
maps["side_split"] = maps.n_ps_side == 20
maps["rounds_db_full"] = maps.n_rounds_db == maps.tot
maps["rounds_any_full"] = maps["rounds_db_full"] | ((maps.arch_status == "ok") & (maps.arch_rounds == maps.tot))
maps["econ_reg_full"] = maps.n_econ == 2 * maps.tot.clip(upper=24)

# ---------------------------------------------------------------- per match
mt = S(f"""
select mo.matchid, mo.date, mo.event, mo.lan, mo.forfeited_maps, q.format, q.found_at, q.team1, q.team2,
  (select count(*) from matches x where x.matchid=mo.matchid and x.mapid=0 and x.sideid=0) n_series_rows,
  (select sum(score) from matches x where x.matchid=mo.matchid and x.mapid=0 and x.sideid=0) series_maps,
  (select max(score) from matches x where x.matchid=mo.matchid and x.mapid=0 and x.sideid=0) win_score,
  (select group_concat(teamid) from (select teamid from matches x where x.matchid=mo.matchid and x.mapid=0 and x.sideid=0 order by teamid)) pair,
  (select group_concat(score) from (select score from matches x where x.matchid=mo.matchid and x.mapid=0 and x.sideid=0 order by teamid)) pair_score,
  (select count(*) from match_maps mm where mm.matchid=mo.matchid) n_maps
from match_overview mo left join scrape_queue q using(matchid) where mo.date between '{W0}' and '{W1}'""")
mt["month"] = mt["date"].str[:7]
agg = maps.groupby("matchid").agg(maps_ok=("scoreboard_full", "all"), hs_ok=("hs_full", "all"),
                                  rounds_ok=("rounds_any_full", "all"), rounds_db_ok=("rounds_db_full", "all"))
mt = mt.merge(agg, left_on="matchid", right_index=True, how="left")
# BO1: HLTV stores the MAP round score as the series row (e.g. 13-7), so a one-map series with a winner score >= 13 is a BO1
mt["bo1_round_score"] = (mt.n_maps == 1) & (mt.win_score >= 13)
mt["series_ok"] = (mt.n_series_rows == 2) & (mt.n_maps > 0) & ((mt.n_maps == mt.series_maps - mt.forfeited_maps) | mt.bo1_round_score)
mt["complete"] = mt.series_ok & mt.maps_ok.fillna(False) & mt.hs_ok.fillna(False)
fd = mt.found_at.fillna("").str[:10]
mt["ingest"] = pd.cut(pd.to_datetime(fd, errors="coerce"), [pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-12 23:59"),
                      pd.Timestamp("2026-10-13 23:59"), pd.Timestamp("2026-12-31")],
                      labels=["Oct 11-12 rebuild (top-200 rule)", "Oct 13 backfill (all tiers)", "Oct 14+ regular scan (all tiers)"])
fmt = mt.format.fillna("")
mt["bo"] = mt.win_score.map({1: "BO1", 2: "BO3", 3: "BO5"}).fillna("other")
mt.loc[mt.bo1_round_score, "bo"] = "BO1"
mt.loc[fmt.eq("bo2"), "bo"] = "BO2"
# duplicates: same day, same team pair, same series score, different match ids
dup = mt[mt.pair.notna()].groupby(["date", "pair", "pair_score"]).matchid.count()
NUM["duplicate_groups_same_day_pair_score"] = int((dup > 1).sum())

# tier proxy: worse-ranked team's VRS rank in the EARLIEST stored snapshot (2026-10-11; look-ahead, no history exists)
rk = dict(c.execute("select teamid, rank from team_rankings where date=(select min(date) from team_rankings)").fetchall())
def band(pair):
    if not isinstance(pair, str):
        return "unknown"
    rs = [rk.get(int(t)) for t in pair.split(",")]
    if any(r is None for r in rs):
        return "4: a team unranked / >200"
    w = max(rs)
    return "1: both top-30" if w <= 30 else "2: worse team 31-100" if w <= 100 else "3: worse team 101-200" if w <= 200 else "4: a team unranked / >200"
mt["tier"] = mt.pair.map(band)
maps = maps.merge(mt[["matchid", "tier", "ingest", "bo"]], on="matchid", how="left")

# ======================================================== 1. matches
g = mt.groupby("month")
t1 = pd.DataFrame({"matches": g.size(), "complete series": g.complete.sum(), "forfeit flag": g.forfeited_maps.apply(lambda s: (s > 0).sum()),
                   "LAN": g.lan.apply(lambda s: (s == 1).sum()), "BO1": g.bo.apply(lambda s: (s == "BO1").sum()),
                   "BO3": g.bo.apply(lambda s: (s == "BO3").sum()), "BO5": g.bo.apply(lambda s: (s == "BO5").sum()), "BO2/other": g.bo.apply(lambda s: (~s.isin(["BO1", "BO3", "BO5"])).sum())})
for lab in mt.ingest.cat.categories:
    t1[lab] = g.ingest.apply(lambda s, l=lab: (s == l).sum())
t1.loc["TOTAL"] = t1.sum()
md(t1, "1. Matches by month (HLTV DB, match date)")
NUM["matches"] = int(len(mt)); NUM["complete_series"] = int(mt.complete.sum())
reasons = collections.Counter()
for r in mt.itertuples():
    if r.complete:
        continue
    if r.n_series_rows != 2: reasons["no two-team series score row"] += 1
    elif r.n_maps == 0: reasons["no played-map rows"] += 1
    elif r.n_maps != r.series_maps - r.forfeited_maps: reasons["played maps != series score - forfeited maps"] += 1
    elif not r.maps_ok: reasons["a map without a full 5+5 scoreboard"] += 1
    elif not r.hs_ok: reasons["a map without 10 headshot rows"] += 1
md(pd.Series(reasons, name="matches").to_frame(), "1b. Matches not counted as complete series, first failing reason")
t1t = pd.crosstab(mt.tier, mt.ingest, margins=True)
md(t1t, "1c. Matches by tier proxy (worse team's VRS rank, 2026-10-11 snapshot) x ingestion cohort")
mt["q"] = pd.PeriodIndex(mt.date, freq="Q").astype(str)
md(pd.crosstab(mt.q, mt.tier, margins=True), "1d. Matches by quarter x tier proxy")
md(pd.crosstab(mt.q, mt.lan.map({1: "LAN", 0: "online"}).fillna("unknown"), margins=True), "1e. Matches by quarter x LAN / online")

# ======================================================== 2. maps
g = maps.groupby("month")
t2 = pd.DataFrame({"maps": g.size(), "full 5+5 scoreboard": g.scoreboard_full.sum(), "10 HS rows": g.hs_full.sum(),
                   "assists/KAST/ADR/rating (10 rows)": g.detail_full.sum(), "CT/T split (20 rows)": g.side_split.sum(),
                   "overtime maps": g.ot.sum()})
t2.loc["TOTAL"] = t2.sum()
md(t2, "2. Maps by month")
NUM.update(maps=int(len(maps)), maps_full_scoreboard=int(maps.scoreboard_full.sum()), maps_hs_full=int(maps.hs_full.sum()))
md(pd.crosstab(maps.tier, maps.scoreboard_full, margins=True), "2b. Maps by tier proxy x full scoreboard")

# ======================================================== 3. rounds
g = maps.groupby("month")
t3 = pd.DataFrame({"maps": g.size(), "OT maps": g.ot.sum(), "DB map_rounds complete": g.rounds_db_full.sum(),
                   "DB rounds rows": g.n_rounds_db.sum(),
                   "archive-parsed ok (all)": g.arch_status.apply(lambda s: (s == "ok").sum()),
                   "OT maps recovered from archive": g.apply(lambda d: ((d.ot) & (d.arch_status == "ok") & (d.arch_rounds == d.tot)).sum()),
                   "complete round sequence (DB or archive)": g.rounds_any_full.sum(),
                   "economy = regulation rounds": g.econ_reg_full.sum()})
t3.loc["TOTAL"] = t3.sum()
md(t3, "3. Round-by-round coverage by month")
NUM.update(ot_maps=int(maps.ot.sum()), rounds_db_full=int(maps.rounds_db_full.sum()), rounds_any_full=int(maps.rounds_any_full.sum()),
           ot_recovered=int(((maps.ot) & (maps.arch_status == "ok") & (maps.arch_rounds == maps.tot)).sum()))
md(maps.groupby(["ot", maps.arch_status.fillna("not in window export")]).size().rename("maps").to_frame(),
   "3b. Archive parse status by overtime flag")
ra = sqlite3.connect(os.path.join(DATA, "rounds_archive.sqlite"))
md(pd.read_sql_query("select status, db_match, count(*) maps, sum(n_rounds) rounds, sum(n_ot_rounds) ot_rounds from pages group by 1,2", ra),
   "3c. rounds_archive.sqlite: every exported map (incl. Oct 1-21 2024 / Oct 22+ 2026 outside the window)", index=False)
md(pd.read_sql_query("select error, count(*) n from pages where status not in ('ok') group by 1 order by 2 desc limit 15", ra),
   "3d. Non-ok archive pages (first 15 reasons)", index=False)

# ======================================================== 4. player stats fields
ps = S(f"""select count(*) n, count(k) k, count(d) d, count(adr) adr, count(kast) kast, count(rating) rating, count(ek) ek, count(roundswing) swing
           from player_stats p join match_overview mo using(matchid) where p.sideid=0 and p.mapid<>0 and mo.date between '{W0}' and '{W1}'""")
md(ps, "4a. player_stats (scoreboard, total side) non-null counts", index=False)
mps = S(f"""select count(*) n, count(hs) hs, count(a) a, count(fa) fa, count(td) td, count(ok) ok, count(od) od, count(mk) mk, count(clutches) clutches,
                   count(kast) kast, count(adr) adr, count(swing) swing, count(rating) rating, count(ehs) ehs, count(ekast) ekast, count(eadr) eadr
            from map_player_stats p join match_overview mo using(matchid) where p.sideid=0 and mo.date between '{W0}' and '{W1}'""")
md(mps, "4b. map_player_stats (map-stats page detail, total side) non-null counts", index=False)
md(S(f"""select count(*) orphan_rows, count(distinct h.mapstatsid) orphan_ids, min(h.mapstatsid) min_id, max(h.mapstatsid) max_id
         from map_headshots h join match_overview mo using(matchid)
         where mo.date between '{W0}' and '{W1}' and h.mapstatsid not in (select mapstatsid from match_maps)"""),
   "4c. map_headshots rows whose mapstatsid is not a played map (placeholder ids)", index=False)

# ======================================================== 6. prop lines
h = pd.read_csv(os.path.join(DATA, "props_data", "history.csv"))
h["seen_day"] = h.seen_at.str[:10]
md(h.groupby(["book", "market"]).agg(rows=("line_id", "size"), line_ids=("line_id", "nunique"), first_seen=("seen_at", "min"),
                                      last_seen=("seen_at", "max")), "6a. props-data history.csv (seen_at UTC) by book x market")
md(pd.crosstab(h.maps, h.odds_type, margins=True), "6b. history.csv rows by maps x odds_type")
hd = h.groupby(["seen_day", "book"]).line_id.nunique().unstack(fill_value=0)
md(hd, "6c. history.csv distinct line ids first/re-seen per UTC day")
pg = pd.read_sql_query("select * from prop_grades", sqlite3.connect(os.path.join(DATA, "hltv_props.sqlite")))
md(pg.groupby(["book", "status"]).agg(n=("line_id", "size"), first_start=("start_time", "min"), last_start=("start_time", "max")),
   "6d. HLTV DB prop_grades (closing line before start, graded) by book x status")
ud = json.load(open(os.path.join(DATA, "ud_moves_since_2026-09-01.json")))["rows"]
udf = pd.DataFrame(ud, columns=["at", "player", "team", "opponent", "start", "prop", "line", "higher", "lower", "line_id", "scope"])
md(udf.groupby(udf["at"].str[:10]).agg(rows=("line_id", "size"), line_ids=("line_id", "nunique")).T, "6e. production ud_moves (Render) rows by UTC day")
NUM["ud_moves"] = {"rows": len(udf), "first": udf["at"].min(), "last": udf["at"].max()}
NUM["history_csv"] = {"rows": int(len(h)), "first_seen": h.seen_at.min(), "last_seen": h.seen_at.max()}

# ======================================================== 7. forecasts + settlements
F = pd.DataFrame(json.load(open(os.path.join(DATA, "forecasts_lite.json")))["rows"])
F["has_quote"] = F.quote.notna()
fv = F.groupby("model_version").agg(rows=("id", "size"), picks=("pick_id", "nunique"), first_logged=("logged_at", "min"),
                                    last_logged=("logged_at", "max"), with_quote=("has_quote", "sum"), with_inputs=("has_inputs", "sum"))
md(fv.sort_values("first_logged"), "7a. Render forecast log (forecasts table) by model_version tag")
md(F.groupby(F.odds_source.fillna("none")).size().rename("rows").to_frame(), "7b. forecasts by odds_source (market input recorded)")
P = json.load(open(os.path.join(DATA, "p1_units.json")))
p1 = pd.DataFrame(P["rows"])
md(p1.groupby("version").agg(units=("forecast_id", "size"), matches=("match", "nunique"), first_start=("start", "min"),
                             last_start=("start", "max")).sort_values("first_start"), f"7c. P1 settled book-line units (stored export at {P['meta']['at']})")
md(pd.crosstab(p1.prop, p1.scope, margins=True), "7d. P1 units by prop x scope")
pk = pd.DataFrame(json.load(open(os.path.join(SP, "picks_now.json"))))
md(pd.crosstab(pk.model_version.fillna("none"), pk.status, margins=True), "7e. Render picks export (2026-10-20 00:40 UTC) model_version x status")
rw = pd.DataFrame(json.load(open(os.path.join(SP, "picks_after_recheck.json"))))
md(pd.crosstab(rw.model_version.fillna("none (pre 2026-10-07)"), rw.status, margins=True), "7f. Railway picks export (2026-10-08 23:56 UTC) model_version x status")
fh = pd.DataFrame(json.load(open(os.path.join(DATA, "fh1_records.json")))["rows"])
md(fh.groupby(["challenger", "prop", "scope"]).agg(records=("id", "size"), first=("logged_at", "min"), last=("logged_at", "max")),
   "7g. fh-1 shadow records (shadow_forecasts)")

# ======================================================== 8. markets
hp = sqlite3.connect(os.path.join(DATA, "hltv_props.sqlite"))
md(pd.read_sql_query("select outcome, path, count(*) n, min(fetched_at) first, max(fetched_at) last, sum(events) events from thunderpick_snapshots group by 1,2", hp),
   "8a. HLTV DB thunderpick_snapshots (scraper copy)", index=False)
open(os.path.join(HERE, "coverage_tables.md"), "w", encoding="utf-8").write("\n".join(OUT))
json.dump(NUM, open(os.path.join(HERE, "coverage_numbers.json"), "w"), indent=1, default=str)
print(json.dumps(NUM, indent=1, default=str))
