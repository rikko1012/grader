#!/usr/bin/env python3
"""
ml_features.py - point-in-time features for the CS2 series-winner (moneyline) research model.

Research code: nothing here is imported by the server, the prop grader, moneyline.py or any member output.
The SAME functions compute features for finished matches (the research dataset) and for upcoming matches (live):
history and live differ only in which Snapshot they are given and which day they ask about.

  Snapshot        in-memory copy of the HLTV tables (teams, match_overview, per-map scores, per-map player rows,
                  events). Snapshot.from_db(con, before=D) reads ONLY rows dated < D (the leakage check uses it);
                  Snapshot.from_rows(...) builds one from plain tuples (tests, fixtures).
  FeatureEngine   fits every sequential rating system once over all matches in the snapshot, in (date, matchid)
                  order, and answers  features(team_a, team_b, day, ...)  from rows dated STRICTLY BEFORE `day`:
                  a rating "as of day D" is the rating after the last match dated < D; every window ends at D
                  (exclusive). Nothing dated D or later is ever read, so a snapshot cut at D gives identical values
                  (verified by verify_point_in_time()).
  market_features price at the cutoff (last point at or before it, no older than 6 h), price 24 h / 6 h before the
                  cutoff, max / min / point count in the 48 h before the cutoff, logit moves.

Day convention. HLTV dates matches by UTC calendar date only, so a match dated on the cutoff's UTC date may have been
played after the cutoff. feature_day(cutoff_ts, hltv_date) = the earlier of the cutoff's UTC date and the HLTV date;
features use rows dated < feature_day.

Rating systems (all fitted only on matches dated before the day asked about):
  team Elo        map-level Elo: each played map is one game, all maps of a series scored against the pre-series
                  ratings (so a 2-0 moves twice as much as a 2-1 net: margin-of-maps aware). Series win
                  probability from the map probability and the best-of (p_series(p_map, bo)). One column per K in
                  K_GRID; elo_* (no suffix) uses the K chosen by tune_k(): the K with the lowest series log-loss on
                  HLTV matches dated in the 365 days before the FIRST DAY OF THE MONTH of the day asked about
                  (so the choice never sees the asked month, let alone the match).
  roster Elo      the same map-level Elo per PLAYER: a lineup's strength on a map = mean Elo of the players who played
                  it; every player of the lineup takes the team's update. A player's first map starts him at the mean
                  of his rated teammates on that map (or the team Elo when none is rated). The roster Elo of a team
                  on day D = mean player Elo of its 5 expected starters (most maps for the team in the 60 days before
                  D; ties: the more recent, then the lower player id).
  Glicko-1        per-team rating + rating deviation (RD) with time decay (c = GLICKO_C per day, RD <= 350), one
                  rating period per series, maps as games; games played (series and maps).

Requires only the Python standard library (numpy optional, unused) - deployable as is.
"""
import bisect
import csv
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------------------- constants (fixed a priori)
CUTOFF_MIN = 30                  # prediction cutoff = scheduled start - 30 min
MAX_AGE_H = 6                    # price at the cutoff: last point at or before it, no older than 6 h before it
WINDOW_H = 48                    # market history window before the cutoff
MOVE_MAX_AGE_H = 6               # the "24 h before" / "6 h before" prices: last point at or before that time, <= 6 h old
RESOLVED_EPS = 0.01

ELO_INIT = 1500.0
K_GRID = (10, 16, 24, 32, 48)    # per-map K values fitted side by side; elo_* uses the tuned one
K_DEFAULT = 24                   # used only for form residuals (fixed a priori, never tuned)
TUNE_DAYS = 365                  # tune_k: series of the 365 days before the month start
TUNE_BURN_DAYS = 90              # ... skipping the first 90 days of the database (ratings still settling)
TUNE_MIN_N = 200                 # fewer scored series than this: K_DEFAULT
GLICKO_INIT_RD = 350.0
GLICKO_MIN_RD = 30.0
GLICKO_C = 18.0                  # RD growth per sqrt(day) of inactivity (50 -> 350 in ~1 year)

ROSTER_DAYS = 60
STANDIN_MAPS = 5                 # a starter with fewer maps than this for the team in ROSTER_DAYS = stand-in flag
FORM_DAYS = 365
REST_CAP = 60
LANGAP_DAYS = 365
LANGAP_K = 5                     # LAN / online mean Elo residual shrunk toward 0 with k = 5 series

# the five ml_eval.py features - same constants as ml_eval.py
MLE_ROSTER_DAYS = 60
MLE_RATING_K = 20
MLE_MAPPOOL_DAYS = 180
MLE_MAPPOOL_TOP = 4
MLE_MAPPOOL_K = 10
MLE_FORM_N = 10
MLE_FORM_DAYS = 365
MLE_FORM_K = 5
MLE_H2H_DAYS = 365
MLE_H2H_KEEP = 3
MLE_H2H_K = 3

MAPPOOL_DAYS = 180               # map-pool strength vs the opponent: union of both teams' top-4 maps
MAPPOOL_TOP = 4
MAPPOOL_K = 6                    # per-map win rate shrunk toward the team's overall map win rate, k = 6 maps


# ---------------------------------------------------------------------------------------------- small helpers
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def logit(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sigmoid(z):
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def elo_p(diff):
    """Map win probability of the side `diff` Elo points ahead."""
    return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))


def p_series(p, bo):
    """Series win probability from the map win probability p for a best-of-bo (odd bo; anything else -> p)."""
    try:
        bo = int(bo)
    except (TypeError, ValueError):
        return p
    if bo <= 1 or bo % 2 == 0:
        return p
    n, q = (bo + 1) // 2, 1 - p
    return sum(math.comb(n - 1 + k, k) * p ** n * q ** k for k in range(n))


def utc_date(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date()


def feature_day(cutoff_ts, hltv_date=None):
    """The day features are keyed to: rows dated STRICTLY BEFORE it are used. The earlier of the cutoff's UTC date and
    the HLTV match date (HLTV dates only - a row dated on the cutoff's date may have been played after the cutoff)."""
    d = utc_date(cutoff_ts)
    if hltv_date is not None:
        d = min(d, hltv_date if isinstance(hltv_date, date) else date.fromisoformat(str(hltv_date)))
    return d


def _before(rows, day):
    """Index of the first row dated >= day in a (date, ...) sorted list."""
    return bisect.bisect_left(rows, (day,))


def _window(rows, day, days):
    return rows[bisect.bisect_left(rows, (day - timedelta(days=days),)):_before(rows, day)]


# ---------------------------------------------------------------------------------------------- regions
_REGION_ALIASES = {"EU": "Europe", "NAM": "North America", "SAM": "South America", "CIS": "CIS", "ASIA": "Asia",
                   "OCE": "Oceania"}
_EVENT_REGION_WORDS = (
    (re.compile(r"\bnorth america|\bNA\b|\bNSTLGA\b", re.I), "North America"),
    (re.compile(r"\bsouth america|\bSA\b|\bLATAM|\bbrazil|\bargentin|\bgamers club\b|\bcircuit x\b", re.I), "South America"),
    (re.compile(r"\beurope|\beuropean\b|\bEU\b", re.I), "Europe"),
    (re.compile(r"\bCIS\b", re.I), "CIS"),
    (re.compile(r"\boceania\b|\baustralia|\bANZ|\bDFRAG\b", re.I), "Oceania"),
    (re.compile(r"\basia\b|\basia-pacific|\bAPAC\b|\bchina\b|\bmongolia|\bMENA\b|\bmiddle east", re.I), "Asia"),
)
_REGIONS = {}


def _regions():
    """regions.csv: {country code: region}, {normalised country name: region}."""
    if not _REGIONS:
        by_code, by_name = {}, {}
        try:
            with open(os.path.join(HERE, "regions.csv"), encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    if r.get("country_code") and r.get("region"):
                        by_code[r["country_code"].strip().upper()] = r["region"].strip()
                        by_name[_norm(r.get("country"))] = r["region"].strip()
        except OSError:
            pass
        for k, v in (("russia", "CIS"), ("uk", "Europe"), ("us", "North America"), ("usa", "North America"),
                     ("czechrepublic", "Europe"), ("turkiye", "Europe"), ("southkorea", "Asia"), ("hongkong", "Asia")):
            by_name.setdefault(k, v)
        _REGIONS.update(code=by_code, name=by_name)
    return _REGIONS


def team_region(country_code):
    if not country_code:
        return None
    c = str(country_code).strip().upper()
    return _REGION_ALIASES.get(c) or _regions()["code"].get(c)


def event_region(event_name, location=None):
    """Region of an event: from its location ('City, Country' / 'Europe (Online)') when known, else from unambiguous
    words in its name ('... Europe Cup 9', 'CCT South America Series'); None when unknown or ambiguous."""
    loc = str(location or "").replace("|", "").strip()
    if loc and loc.upper() != "TBA":
        m = re.match(r"^(.*)\(online\)\s*$", loc, re.I)
        if m:
            loc = m.group(1).strip()
        for word, reg in _EVENT_REGION_WORDS:
            if word.fullmatch(loc) or word.search(loc) and "," not in loc:
                return reg
        last = loc.split(",")[-1].strip()
        reg = _regions()["name"].get(_norm(last)) or _regions()["code"].get(last.upper())
        if reg:
            return reg
    hits = {reg for word, reg in _EVENT_REGION_WORDS if word.search(str(event_name or ""))}
    return hits.pop() if len(hits) == 1 else None


# ---------------------------------------------------------------------------------------------- snapshot
class Snapshot:
    """HLTV rows needed by the features. matches: {mid: {mid, date, event, lan, forfeited, teams (sorted tuple of 2),
    maps: [(mapid, mapname, {tid: score})], series: {tid: score}, maps_won: {tid: n}, winner, bo}}.
    players: [(date, mid, tid, pid, mapid, rating)] sorted."""

    def __init__(self):
        self.teams = {}            # tid -> name
        self.country = {}          # tid -> country code
        self.matches = {}
        self.players = []
        self.events = {}           # normalised event name -> {"lan", "location"}
        self.before = None         # the snapshot holds only rows dated < before (None = everything)

    # ---- construction
    @classmethod
    def from_rows(cls, teams, matches, maps, players, events=(), formats=None, before=None):
        """teams [(tid, name, country)], matches [(mid, date, event, lan, forfeited)],
        maps [(mid, tid, mapid, own score, opp score, map name)] (mapid 0 = series row),
        players [(mid, tid, pid, mapid, rating)], events [(name, lan, location)], formats {mid: 'bo3'/'anc'/...}.
        before: keep only matches dated < before (rows of other matches are dropped)."""
        s = cls()
        s.before = before
        for tid, name, ctry in teams:
            s.teams[int(tid)] = name
            if ctry:
                s.country[int(tid)] = ctry
        for name, lan, loc in events or ():
            s.events[_norm(name)] = {"lan": lan, "location": loc}
        for mid, d, ev, lan, ff in matches:
            if before is not None and d >= before:
                continue
            s.matches[int(mid)] = {"mid": int(mid), "date": d, "event": ev, "lan": lan, "forfeited": int(ff or 0),
                                   "teams": set(), "maps": {}, "series": {}, "maps_won": Counter(), "winner": None,
                                   "format": (formats or {}).get(mid)}
        for mid, tid, mapid, own, opp, mname in maps:
            m = s.matches.get(mid)
            if m is None:
                continue
            m["teams"].add(int(tid))
            if mapid == 0:
                m["series"][int(tid)] = own
            elif own is not None and opp is not None:
                m["maps"].setdefault(int(mapid), [_norm(mname), {}])[1][int(tid)] = int(own)
        for m in s.matches.values():
            m["teams"] = tuple(sorted(m["teams"]))
            m["maps"] = sorted((mapid, nm, sc) for mapid, (nm, sc) in m["maps"].items() if len(sc) == 2)
            for _, _, sc in m["maps"]:
                (a, sa), (b, sb) = sorted(sc.items())
                if sa != sb:
                    m["maps_won"][a if sa > sb else b] += 1
            m["winner"] = _winner(m)
            m["bo"] = _bo_of(m)
        for mid, tid, pid, mapid, rating in players:
            m = s.matches.get(mid)
            if m is None or not mapid:
                continue
            s.players.append((m["date"], int(mid), int(tid), int(pid), int(mapid),
                              None if rating is None else float(rating)))
        s.players.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[4]))
        return s

    @classmethod
    def from_db(cls, con, before=None):
        """Read the HLTV database (psycopg2 connection). before: only rows dated < before are read (SQL-side)."""
        cur = con.cursor()
        cur.execute("SET statement_timeout = 300000")
        w, args = ("WHERE mo.date < %s", (before,)) if before is not None else ("", ())
        cur.execute("SELECT teamid, name, country FROM teams")
        teams = cur.fetchall()
        cur.execute("SELECT name, lan, location FROM events")
        events = cur.fetchall()
        cur.execute(f"SELECT mo.matchid, mo.date, mo.event, mo.lan, coalesce(mo.forfeited_maps, 0) FROM match_overview mo {w}",
                    args)
        matches = cur.fetchall()
        cur.execute(f"""SELECT own.matchid, own.teamid, own.mapid, own.score, opp.score, mp.name
                        FROM matches own
                        JOIN match_overview mo ON mo.matchid = own.matchid
                        JOIN matches opp ON opp.matchid = own.matchid AND opp.teamid <> own.teamid
                                        AND opp.mapid = own.mapid AND opp.sideid = 0
                        LEFT JOIN maps mp ON mp.mapid = own.mapid
                        WHERE own.sideid = 0 {('AND mo.date < %s' if before is not None else '')}""", args)
        maps = cur.fetchall()
        cur.execute(f"""SELECT ps.matchid, ps.teamid, ps.playerid, ps.mapid, ps.rating FROM player_stats ps
                        JOIN match_overview mo ON mo.matchid = ps.matchid
                        WHERE ps.mapid <> 0 AND ps.sideid = 0 {('AND mo.date < %s' if before is not None else '')}""",
                    args)
        players = cur.fetchall()
        formats = {}
        try:
            cur.execute(f"""SELECT sq.matchid, sq.format FROM scrape_queue sq JOIN match_overview mo ON mo.matchid = sq.matchid
                            {w}""", args)
            formats = {mid: f for mid, f in cur.fetchall() if f}
        except Exception:
            con.rollback()
        return cls.from_rows(teams, matches, maps, players, events, formats, before=before)

    def restrict(self, before):
        """The same snapshot with only rows dated < before (in memory)."""
        s = Snapshot()
        s.teams, s.country, s.events = dict(self.teams), dict(self.country), dict(self.events)
        s.before = before if self.before is None else min(before, self.before)
        s.matches = {k: m for k, m in self.matches.items() if m["date"] < before}
        s.players = [r for r in self.players if r[0] < before]
        return s

    def event_meta(self, name):
        return self.events.get(_norm(name)) or {}


def _winner(m):
    """Series winner from maps won, cross-checked against HLTV's series row; None if missing / tied / inconsistent."""
    if len(m["teams"]) != 2:
        return None
    a, b = m["teams"]
    wa, wb = m["maps_won"].get(a, 0), m["maps_won"].get(b, 0)
    if wa == wb:
        return None
    w = a if wa > wb else b
    s = m["series"]
    if s.get(a) is not None and s.get(b) is not None and s[a] != s[b] and (s[a] > s[b]) != (w == a):
        return None
    return w


def bo_from_text(text):
    """Best-of from a market title / rules ('(BO3)', 'best of 3', 'Bo5'); None if not stated."""
    m = re.search(r"\bBO\s*([1-7])\b|\bbest[- ]of[- ](\d|one|two|three|five)\b", str(text or ""), re.I)
    if not m:
        return None
    v = m.group(1) or m.group(2)
    return {"one": 1, "two": 2, "three": 3, "five": 5}.get(str(v).lower(), None) or int(v)


def _bo_of(m):
    """HLTV best-of: scrape_queue format ('bo3' / 'bo5' / a map code = bo1), else from maps played (1 -> 1,
    2-3 -> 3, 4-5 -> 5). Only used for HLTV-internal tuning and when a market states none."""
    f = str(m.get("format") or "").lower()
    if re.fullmatch(r"bo\d", f):
        return int(f[2])
    if f:
        return 1
    n = len(m["maps"])
    return 1 if n == 1 else 3 if n in (2, 3) else 5 if n in (4, 5) else None


# ---------------------------------------------------------------------------------------------- engine
class FeatureEngine:
    """All sequential ratings fitted once over the snapshot; features(...) answers from rows dated < day."""

    def __init__(self, snap, k_grid=K_GRID):
        self.snap = snap
        self.k_grid = tuple(k_grid)
        ms = sorted((m for m in snap.matches.values() if len(m["teams"]) == 2 and not m["forfeited"] and m["maps"]),
                    key=lambda m: (m["date"], m["mid"]))
        self.order = ms
        self.first_date = min((m["date"] for m in snap.matches.values()), default=None)
        # per-team / per-player indexes (all lists sorted by date first)
        self.team_maps = defaultdict(list)        # tid -> [(date, mid, mapname, won)]
        self.team_series = defaultdict(list)      # tid -> [(date, mid, opp, won, lan)]
        self.team_player = defaultdict(list)      # tid -> [(date, mid, pid)] one row per player per map
        self.player_rows = defaultdict(list)      # pid -> [(date, rating)]
        self.lineups = defaultdict(set)           # (mid, tid) -> pids
        self.map_lineup = defaultdict(list)       # (mid, mapid, tid) -> pids
        for m in ms:
            a, b = m["teams"]
            for _, nm, sc in m["maps"]:
                if sc[a] != sc[b]:
                    self.team_maps[a].append((m["date"], m["mid"], nm, sc[a] > sc[b]))
                    self.team_maps[b].append((m["date"], m["mid"], nm, sc[b] > sc[a]))
            if m["winner"] is not None:
                self.team_series[a].append((m["date"], m["mid"], b, m["winner"] == a, m["lan"]))
                self.team_series[b].append((m["date"], m["mid"], a, m["winner"] == b, m["lan"]))
        valid = {m["mid"] for m in ms}
        for d, mid, tid, pid, mapid, rating in snap.players:
            if mid not in valid:
                continue
            self.team_player[tid].append((d, mid, pid))
            self.lineups[(mid, tid)].add(pid)
            self.map_lineup[(mid, mapid, tid)].append(pid)
            if rating is not None:
                self.player_rows[pid].append((d, rating))
        for dct in (self.team_maps, self.team_series, self.team_player, self.player_rows):
            for k in dct:
                dct[k].sort(key=lambda r: r[:2])
        self._fit_elo()
        self._fit_player_elo()
        self._fit_glicko()
        self._kcache = {}

    # ---- team Elo (map level, one column per K)
    def _fit_elo(self):
        self.elo_hist = {k: defaultdict(list) for k in self.k_grid}    # k -> tid -> [(date, mid, rating after)]
        self.elo_pre = {k: {} for k in self.k_grid}                    # k -> mid -> (Ra, Rb) before the series
        for k in self.k_grid:
            r = {}
            hist, pre = self.elo_hist[k], self.elo_pre[k]
            for m in self.order:
                a, b = m["teams"]
                ra, rb = r.get(a, ELO_INIT), r.get(b, ELO_INIT)
                pre[m["mid"]] = (ra, rb)
                e = elo_p(ra - rb)
                s = sum((1.0 if sc[a] > sc[b] else 0.0 if sc[a] < sc[b] else 0.5) for _, _, sc in m["maps"])
                delta = k * (s - e * len(m["maps"]))
                r[a], r[b] = ra + delta, rb - delta
                hist[a].append((m["date"], m["mid"], r[a]))
                hist[b].append((m["date"], m["mid"], r[b]))

    # ---- player Elo (map level, one column per K)
    def _fit_player_elo(self):
        self.pelo_hist = {k: defaultdict(list) for k in self.k_grid}   # k -> pid -> [(date, mid, rating after)]
        self.pelo_pre = {k: {} for k in self.k_grid}                   # k -> mid -> (lineup mean a, b) first map
        for k in self.k_grid:
            r = {}
            hist, pre = self.pelo_hist[k], self.pelo_pre[k]
            team_r = self.elo_hist[k]
            for m in self.order:
                a, b = m["teams"]
                deltas = defaultdict(float)
                means = {}
                for mapid, _, sc in m["maps"]:
                    lus = {t: self.map_lineup.get((m["mid"], mapid, t)) or [] for t in (a, b)}
                    if not lus[a] or not lus[b]:
                        continue
                    mean = {}
                    for t in (a, b):
                        known = [r[p] for p in lus[t] if p in r]
                        if len(known) < len(lus[t]):
                            base = (sum(known) / len(known)) if known else self._team_elo_before(team_r, t, m)
                            for p in lus[t]:
                                r.setdefault(p, base)
                        mean[t] = sum(r[p] for p in lus[t]) / len(lus[t])
                    means.setdefault("first", (mean[a], mean[b]))
                    e = elo_p(mean[a] - mean[b])
                    s = 1.0 if sc[a] > sc[b] else 0.0 if sc[a] < sc[b] else 0.5
                    for p in lus[a]:
                        deltas[p] += k * (s - e)
                    for p in lus[b]:
                        deltas[p] -= k * (s - e)
                if "first" in means:
                    pre[m["mid"]] = means["first"]
                for p, dlt in deltas.items():           # simultaneous update after the series
                    r[p] += dlt
                    hist[p].append((m["date"], m["mid"], r[p]))

    @staticmethod
    def _team_elo_before(team_hist, t, m):
        rows = team_hist.get(t) or []
        i = bisect.bisect_left(rows, (m["date"], m["mid"]))
        return rows[i - 1][2] if i else ELO_INIT

    # ---- Glicko-1 (series = rating period, maps = games)
    def _fit_glicko(self):
        q = math.log(10) / 400
        st = {}                                          # tid -> [r, rd, last date, n series, n maps]
        self.glicko_hist = defaultdict(list)             # tid -> [(date, mid, r, rd, n series, n maps)]

        def g(rd):
            return 1 / math.sqrt(1 + 3 * q * q * rd * rd / math.pi ** 2)

        for m in self.order:
            a, b = m["teams"]
            cur = {}
            for t in (a, b):
                r, rd, last, ns, nm = st.get(t, [ELO_INIT, GLICKO_INIT_RD, None, 0, 0])
                if last is not None:
                    rd = min(math.sqrt(rd * rd + GLICKO_C ** 2 * max((m["date"] - last).days, 0)), GLICKO_INIT_RD)
                cur[t] = [r, rd, last, ns, nm]
            new = {}
            for t, o in ((a, b), (b, a)):
                r, rd = cur[t][0], cur[t][1]
                ro, rdo = cur[o][0], cur[o][1]
                gg = g(rdo)
                e = 1 / (1 + 10 ** (-gg * (r - ro) / 400))
                ssum = sum((1.0 if sc[t] > sc[o] else 0.0 if sc[t] < sc[o] else 0.5) - e for _, _, sc in m["maps"])
                n = len(m["maps"])
                d2 = 1 / (q * q * gg * gg * e * (1 - e) * n)
                denom = 1 / (rd * rd) + 1 / d2
                new[t] = [r + q / denom * gg * ssum, max(math.sqrt(1 / denom), GLICKO_MIN_RD), m["date"],
                          cur[t][3] + 1, cur[t][4] + n]
            for t in (a, b):
                st[t] = new[t]
                self.glicko_hist[t].append((m["date"], m["mid"], *[new[t][i] for i in (0, 1, 3, 4)]))

    # ---- K tuning (inside the training window only)
    def tune_k(self, day):
        """K (team Elo) and K (player Elo) with the lowest series log-loss on HLTV matches dated in
        [month start - TUNE_DAYS, month start) where month start = first day of `day`'s month; also skipping the first
        TUNE_BURN_DAYS of the database. Returns (k_team, k_player, n scored)."""
        ms = day.replace(day=1)
        if ms in self._kcache:
            return self._kcache[ms]
        lo = ms - timedelta(days=TUNE_DAYS)
        if self.first_date is not None:
            lo = max(lo, self.first_date + timedelta(days=TUNE_BURN_DAYS))
        rows = [m for m in self.order if lo <= m["date"] < ms and m["winner"] is not None]
        best = [K_DEFAULT, K_DEFAULT, len(rows)]
        if len(rows) >= TUNE_MIN_N:
            for slot, pre in ((0, self.elo_pre), (1, self.pelo_pre)):
                ll = {}
                for k in self.k_grid:
                    tot, n = 0.0, 0
                    for m in rows:
                        x = pre[k].get(m["mid"])
                        if x is None:
                            continue
                        p = p_series(elo_p(x[0] - x[1]), m["bo"] or 1)
                        y = m["winner"] == m["teams"][0]
                        p = min(max(p, 1e-6), 1 - 1e-6)
                        tot -= math.log(p if y else 1 - p)
                        n += 1
                    ll[k] = tot / n if n else float("inf")
                best[slot] = min(self.k_grid, key=lambda k: (ll[k], k))
        self._kcache[ms] = tuple(best)
        return self._kcache[ms]

    # ---- lookups as of day (rows dated < day)
    @staticmethod
    def _last_before(rows, day):
        i = _before(rows, day)
        return rows[i - 1] if i else None

    def elo(self, tid, day, k):
        r = self._last_before(self.elo_hist[k].get(tid) or [], day)
        return r[2] if r else ELO_INIT

    def elo_known(self, tid, day):
        return bool(self._last_before(self.elo_hist[self.k_grid[0]].get(tid) or [], day))

    def player_elo(self, pid, day, k):
        r = self._last_before(self.pelo_hist[k].get(pid) or [], day)
        return r[2] if r else None

    def glicko(self, tid, day):
        """(rating, RD decayed to `day`, series played, maps played) as of day."""
        r = self._last_before(self.glicko_hist.get(tid) or [], day)
        if not r:
            return ELO_INIT, GLICKO_INIT_RD, 0, 0
        d, _, rating, rd, ns, nm = r
        rd = min(math.sqrt(rd * rd + GLICKO_C ** 2 * max((day - d).days, 0)), GLICKO_INIT_RD)
        return rating, rd, ns, nm

    def starters(self, tid, day, days=ROSTER_DAYS):
        """5 expected starters: most maps for the team in the `days` before day (ties: more recent, lower id)."""
        n, last = Counter(), {}
        for d, _, pid in _window(self.team_player.get(tid) or [], day, days):
            n[pid] += 1
            last[pid] = d
        return sorted(n, key=lambda p: (-n[p], -last[p].toordinal(), p))[:5], n

    def last_lineup(self, tid, day):
        """Players of the team's most recent match dated < day (every map of it)."""
        rows = self.team_player.get(tid) or []
        i = _before(rows, day)
        if not i:
            return []
        mid = rows[i - 1][1]
        return sorted(self.lineups.get((mid, tid)) or [])

    def roster_elo(self, tid, day, k, players=None):
        st = players if players is not None else self.starters(tid, day)[0]
        vals = [v for v in (self.player_elo(p, day, k) for p in st) if v is not None]
        return sum(vals) / len(vals) if vals else None

    # ---- ml_eval.py's five features (identical definitions)
    def _mle_roster(self, tid, day):
        last, n = {}, Counter()
        for d, _, pid in _window(self.team_player.get(tid) or [], day, MLE_ROSTER_DAYS):
            n[pid] += 1
            last[pid] = max(last.get(pid, d), d)
        return sorted(n, key=lambda p: (last[p], n[p], -p), reverse=True)[:5]

    def _mle_player_rating(self, pid, day):
        vals = [r for _, r in _window(self.player_rows.get(pid) or [], day, MLE_ROSTER_DAYS)]
        return (sum(vals) + MLE_RATING_K * 1.0) / (len(vals) + MLE_RATING_K)

    def mle_rating(self, ta, tb, day):
        out = []
        for t in (ta, tb):
            r = self._mle_roster(t, day)
            if not r:
                return None
            out.append(sum(self._mle_player_rating(p, day) for p in r) / len(r))
        return out[0] - out[1]

    def mle_mappool(self, ta, tb, day):
        out = []
        for t in (ta, tb):
            cnt, won = Counter(), Counter()
            for _, _, mp, w in _window(self.team_maps.get(t) or [], day, MLE_MAPPOOL_DAYS):
                cnt[mp] += 1
                won[mp] += bool(w)
            if not cnt:
                return None
            top = [mp for mp, _ in sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:MLE_MAPPOOL_TOP]]
            n, w = sum(cnt[x] for x in top), sum(won[x] for x in top)
            out.append((w + 0.5 * MLE_MAPPOOL_K) / (n + MLE_MAPPOOL_K))
        return out[0] - out[1]

    def mle_form(self, ta, tb, day):
        out = []
        for t in (ta, tb):
            last = _window(self.team_series.get(t) or [], day, MLE_FORM_DAYS)[-MLE_FORM_N:]
            if not last:
                return None
            w = sum(1 for r in last if r[3])
            out.append((w + 0.5 * MLE_FORM_K) / (len(last) + MLE_FORM_K))
        return out[0] - out[1]

    def mle_h2h(self, ta, tb, day):
        ra, rb = set(self._mle_roster(ta, day)), set(self._mle_roster(tb, day))
        wa = wb = 0
        for _, mid, opp, won, _ in _window(self.team_series.get(ta) or [], day, MLE_H2H_DAYS):
            if opp != tb:
                continue
            if (len(self.lineups.get((mid, ta), set()) & ra) >= MLE_H2H_KEEP
                    and len(self.lineups.get((mid, tb), set()) & rb) >= MLE_H2H_KEEP):
                wa += bool(won)
                wb += not won
        return (wa - wb) / (wa + wb + MLE_H2H_K), wa + wb

    # ---- other team features
    def form(self, tid, day, n):
        """(win rate, mean Elo residual S - E with K_DEFAULT pre-match ratings) over the last n series (365 d)."""
        last = _window(self.team_series.get(tid) or [], day, FORM_DAYS)[-n:]
        if not last:
            return None, None
        pre = self.elo_pre[K_DEFAULT if K_DEFAULT in self.k_grid else self.k_grid[0]]
        res = []
        for _, mid, opp, won, _ in last:
            m = self.snap.matches[mid]
            ra, rb = pre[mid]
            if m["teams"][0] != tid:
                ra, rb = rb, ra
            res.append((1.0 if won else 0.0) - p_series(elo_p(ra - rb), m["bo"] or 1))
        return sum(1 for r in last if r[3]) / len(last), sum(res) / len(res)

    def lan_gap(self, tid, day):
        """Shrunk mean Elo residual on LAN minus online, series of the last LANGAP_DAYS (None if either side empty)."""
        pre = self.elo_pre[K_DEFAULT if K_DEFAULT in self.k_grid else self.k_grid[0]]
        acc = {True: [], False: []}
        for _, mid, opp, won, lan in _window(self.team_series.get(tid) or [], day, LANGAP_DAYS):
            if lan is None:
                continue
            m = self.snap.matches[mid]
            ra, rb = pre[mid]
            if m["teams"][0] != tid:
                ra, rb = rb, ra
            acc[bool(lan)].append((1.0 if won else 0.0) - p_series(elo_p(ra - rb), m["bo"] or 1))
        if not acc[True] or not acc[False]:
            return None
        return sum(acc[True]) / (len(acc[True]) + LANGAP_K) - sum(acc[False]) / (len(acc[False]) + LANGAP_K)

    def mappool_vs(self, ta, tb, day):
        """Map-pool strength vs the opponent: over the union of both teams' top-4 most-played maps (180 d), each team's
        win rate per map shrunk toward its overall map win rate (k = MAPPOOL_K); returns (mean a - b, n maps in union,
        overlap of the two top-4 sets)."""
        stats = {}
        for t in (ta, tb):
            cnt, won = Counter(), Counter()
            for _, _, mp, w in _window(self.team_maps.get(t) or [], day, MAPPOOL_DAYS):
                cnt[mp] += 1
                won[mp] += bool(w)
            if not cnt:
                return None, 0, 0
            stats[t] = (cnt, won, sum(won.values()) / sum(cnt.values()))
        tops = {t: [mp for mp, _ in sorted(stats[t][0].items(), key=lambda kv: (-kv[1], kv[0]))[:MAPPOOL_TOP]]
                for t in (ta, tb)}
        pool = sorted(set(tops[ta]) | set(tops[tb]))
        diffs = []
        for mp in pool:
            wr = []
            for t in (ta, tb):
                cnt, won, base = stats[t]
                wr.append((won[mp] + MAPPOOL_K * base) / (cnt[mp] + MAPPOOL_K))
            diffs.append(wr[0] - wr[1])
        return sum(diffs) / len(diffs), len(pool), len(set(tops[ta]) & set(tops[tb]))

    def team_block(self, tid, day):
        """Per-team features (suffix-free names)."""
        f = {}
        st, n = self.starters(tid, day)
        f["n_starters"] = len(st)
        f["maps60"] = sum(n.values()) // 5 if n else 0
        f["standin"] = (int(any(n[p] < STANDIN_MAPS for p in st)) if len(st) == 5 else None)
        f["starters_lt5"] = sum(1 for p in st if n[p] < STANDIN_MAPS) if st else None
        lu = self.last_lineup(tid, day)          # the most recent lineup sees a roster change before the 60-day counts do
        f["lastlu_lt5"] = sum(1 for p in lu if n[p] < STANDIN_MAPS) if lu else None
        f["lastlu_overlap"] = len(set(lu) & set(st)) if lu and st else None
        for back in (30, 90):
            old, _ = self.starters(tid, day - timedelta(days=back))
            f[f"stab{back}"] = len(set(st) & set(old)) if st and old else None
        ser = self.team_series.get(tid) or []
        i = _before(ser, day)
        f["rest_days"] = min((day - ser[i - 1][0]).days, REST_CAP) if i else None
        f["m7"] = len(_window(ser, day, 7))
        f["series365"] = len(_window(ser, day, 365))
        for nn in (5, 10):
            wr, adj = self.form(tid, day, nn)
            f[f"form{nn}"], f[f"form{nn}_adj"] = wr, adj
        f["lan_gap"] = self.lan_gap(tid, day)
        r, rd, ns, nm = self.glicko(tid, day)
        f.update(glicko=r, glicko_rd=rd, games_series=ns, games_maps=nm)
        f["region"] = team_region(self.snap.country.get(tid))
        return f

    # ---- the feature vector
    def features(self, ta, tb, day, lan=None, bo=None, event=None, location=None):
        """Features for team A (ta) vs team B (tb), keyed to `day` (rows dated < day). Differences are A - B.
        lan: the match's LAN flag (known before the match), bo: best-of, event / location: for the region proxy."""
        out = {"feature_day": day.isoformat(), "bo": bo, "lan": None if lan is None else int(bool(lan))}
        kt, kp, ntune = self.tune_k(day)
        out.update(elo_k=kt, relo_k=kp, tune_n=ntune)
        bo_eff = bo if bo in (1, 3, 5) else 3
        for k in self.k_grid:
            ea, eb = self.elo(ta, day, k), self.elo(tb, day, k)
            out[f"elo_diff_k{k}"] = ea - eb
            ra, rb = self.roster_elo(ta, day, k), self.roster_elo(tb, day, k)
            out[f"relo_diff_k{k}"] = (ra - rb) if ra is not None and rb is not None else None
        ea, eb = self.elo(ta, day, kt), self.elo(tb, day, kt)
        out.update(elo_a=ea, elo_b=eb, elo_diff=ea - eb, elo_p=p_series(elo_p(ea - eb), bo_eff),
                   elo_known_a=int(self.elo_known(ta, day)), elo_known_b=int(self.elo_known(tb, day)),
                   tier_mean=(ea + eb) / 2, tier_min=min(ea, eb))
        ra, rb = self.roster_elo(ta, day, kp), self.roster_elo(tb, day, kp)
        out.update(relo_a=ra, relo_b=rb, relo_diff=(ra - rb) if ra is not None and rb is not None else None)
        la = self.roster_elo(ta, day, kp, self.last_lineup(ta, day))
        lb = self.roster_elo(tb, day, kp, self.last_lineup(tb, day))
        out["relo_last_diff"] = (la - lb) if la is not None and lb is not None else None
        out["relo_p"] = p_series(elo_p(out["relo_diff"]), bo_eff) if out["relo_diff"] is not None else None
        A, B = self.team_block(ta, day), self.team_block(tb, day)
        for k, v in A.items():
            out[f"{k}_a"] = v
        for k, v in B.items():
            out[f"{k}_b"] = v
        q = math.log(10) / 400
        rd = math.sqrt(A["glicko_rd"] ** 2 + B["glicko_rd"] ** 2)
        gg = 1 / math.sqrt(1 + 3 * q * q * rd * rd / math.pi ** 2)
        out["glicko_diff"] = A["glicko"] - B["glicko"]
        out["glicko_p"] = p_series(1 / (1 + 10 ** (-gg * out["glicko_diff"] / 400)), bo_eff)
        for k in ("form5", "form10", "form5_adj", "form10_adj", "lan_gap", "rest_days", "m7", "stab30", "stab90",
                  "standin", "lastlu_lt5", "games_series"):
            a, b = A.get(k), B.get(k)
            out[f"{k}_diff"] = (a - b) if a is not None and b is not None else None
        out["lan_gap_x_lan"] = (out["lan_gap_diff"] * out["lan"]) if out["lan_gap_diff"] is not None and lan is not None else None
        mp, npool, ov = self.mappool_vs(ta, tb, day)
        out.update(mappool_vs=mp, mappool_n=npool, mappool_overlap=ov)
        # ml_eval.py's five (None = not computable)
        a = self.mle_rating(ta, tb, day)
        out["mle_a_rating"] = a
        out["mle_b_mappool"] = self.mle_mappool(ta, tb, day)
        out["mle_c_form"] = self.mle_form(ta, tb, day)
        out["mle_d_lan_x_rating"] = (a * (1.0 if lan is True or lan == 1 else 0.0)) if a is not None else None
        e, n = self.mle_h2h(ta, tb, day)
        out["mle_e_h2h"], out["mle_e_h2h_n"] = e, n
        # region proxy
        er = event_region(event, location if location is not None else self.snap.event_meta(event).get("location"))
        out["event_region"] = er
        if er and A["region"] and B["region"]:
            out["home_diff"] = int(A["region"] == er) - int(B["region"] == er)
        else:
            out["home_diff"] = None
        return out


# ---------------------------------------------------------------------------------------------- market features
def _last_at_or_before(pts, t, max_age_s):
    """pts sorted [(t, p)]: the last point at or before t and no older than max_age_s, else None."""
    i = bisect.bisect_right(pts, (t, float("inf")))
    if not i:
        return None
    pt = pts[i - 1]
    return pt if pt[0] >= t - max_age_s else None


def market_features(points, cutoff_ts, prefix="mkt"):
    """points: [(unix t, price of team A)] (any order; points after the cutoff are ignored - never read).
    Returns {prefix_p, prefix_t, prefix_logit, prefix_p24, prefix_p6, prefix_mv24, prefix_mv6, prefix_max48,
    prefix_min48, prefix_npts48, prefix_age_min} and the exclusion reason (None when the cutoff price exists)."""
    pts = sorted((int(t), float(p)) for t, p in points if t is not None and p is not None and t <= cutoff_ts)
    win = [x for x in pts if x[0] >= cutoff_ts - WINDOW_H * 3600]
    out = {f"{prefix}_npts48": len(win),
           f"{prefix}_max48": max((p for _, p in win), default=None),
           f"{prefix}_min48": min((p for _, p in win), default=None)}
    cut = _last_at_or_before(pts, cutoff_ts, MAX_AGE_H * 3600)
    if cut is None:
        why = "no price history" if not pts else "no price before cutoff"
        return out, why
    if cut[1] <= RESOLVED_EPS or cut[1] >= 1 - RESOLVED_EPS:
        return out, "price already resolved before cutoff (start time wrong)"
    out[f"{prefix}_p"] = cut[1]
    out[f"{prefix}_t"] = cut[0]
    out[f"{prefix}_age_min"] = (cutoff_ts - cut[0]) / 60
    out[f"{prefix}_logit"] = logit(cut[1])
    for h in (24, 6):
        x = _last_at_or_before(pts, cutoff_ts - h * 3600, MOVE_MAX_AGE_H * 3600)
        out[f"{prefix}_p{h}"] = x[1] if x else None
        out[f"{prefix}_mv{h}"] = (logit(cut[1]) - logit(x[1])) if x else None
    return out, None


# ---------------------------------------------------------------------------------------------- live entry point
def live_features(engine, team_a_id, team_b_id, start_ts, bo=None, lan=None, event=None, location=None,
                  price_points=None, now_ts=None):
    """Features for an upcoming match from the same code as history. The cutoff is min(start - 30 min, now): the
    feature day is the cutoff's UTC date (no HLTV date exists yet), the market block uses points up to the cutoff."""
    cutoff = start_ts - CUTOFF_MIN * 60
    if now_ts is not None:
        cutoff = min(cutoff, now_ts)
    if lan is None and event:
        lan = engine.snap.event_meta(event).get("lan")
    f = engine.features(team_a_id, team_b_id, feature_day(cutoff), lan=lan, bo=bo, event=event, location=location)
    if price_points is not None:
        mk, why = market_features(price_points, cutoff)
        f.update(mk)
        f["mkt_missing"] = why
    f["cutoff_ts"] = int(cutoff)
    return f


# ---------------------------------------------------------------------------------------------- leakage check
def _same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if math.isnan(a) and math.isnan(b):
                return True
            return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    return a == b


def verify_point_in_time(full_engine, cases, snapshot_for_day):
    """cases: [(ta, tb, day, kwargs)]. snapshot_for_day(day) -> a Snapshot holding only rows dated < day (e.g.
    Snapshot.from_db(con, before=day)). Recomputes each case on its own cut snapshot and compares every value.
    Returns [{"case", "n_features", "mismatches": [(name, full, cut)]}]."""
    out = []
    for ta, tb, day, kw in cases:
        full = full_engine.features(ta, tb, day, **kw)
        cut = FeatureEngine(snapshot_for_day(day), full_engine.k_grid).features(ta, tb, day, **kw)
        keys = sorted(set(full) | set(cut))
        bad = [(k, full.get(k), cut.get(k)) for k in keys if not _same(full.get(k), cut.get(k))]
        out.append({"case": (ta, tb, day.isoformat()), "n_features": len(keys), "mismatches": bad})
    return out
