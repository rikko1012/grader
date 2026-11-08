"""cs2_roles.py - CS2 player roles for the prop cards (owner request 2026-11-07). DISPLAY ONLY.

The role is never a model input: nothing here is read by grading, chances, projections, stakes, thresholds, slip selection or
the model tag (picklog.MODEL_VERSION does not hash this file). It does not touch roles.py (whose AWP rule feeds a stake gate),
igl.csv or a row's d["is_igl"]; grader.py's own "role" (rating rank primary/secondary) is a different thing - the key here is
`card_role`.

Where each role comes from (one primary role per player at most; blank when nothing clearly stands out - never "unknown",
never a hedge such as "likely"). Within one lineup a role is given once at most (IGL, AWPer, Entry, Lurker, Support, Star
rifler), so a 5-man lineup never shows two of the same role.
  IGL          a real listing only: the scraper's Liquipedia roles (player_roles.is_igl, team_match, status ok) for the
               team's current lineup, and only when exactly ONE lineup player is flagged (a past IGL still listed on a
               second player would otherwise give a team two). A Liquipedia team-page `igl=y` marker (roles.parse_igls,
               already cached by roles.py) may break a tie. Never from stats.
  AWPer        a real weapon field: HLTV share of kills with the AWP (player_awp, 90 days, >= 100 kills) >= 0.25 -
               roles.py's own AWP_SHARE / AWP_MIN_KILLS, copied here, never changed. The team's highest share only. With
               no share on file, a Liquipedia AWP listing for the current team (one player only).
  Listed role  (owner: "where no listed role exists, use stat-based roles") the Liquipedia infobox's entry / lurk /
               support for the current team, shown when the player's stats do not contradict it (Entry: T-side opening
               share >= league median; Lurker: <= median; Support: kill share <= p80).
  Stat role    Star rifler / Entry / Lurker / Support from map_player_stats per map and side, decayed, relative to the team
               (shares of the team's kills / opening duels / flash assists on the same map-side) and to the league
               (percentiles among active players), for riflers with no listed role: the player clears the league gate
               and the pattern holds on >= 2/3 of his maps with >= 10 played (and >= 2 such maps); the team's most extreme
               passer gets it.
  `role` = "IGL" when the IGL rule holds, else the AWP / listed / stat role. `stat_role` keeps that role beside an IGL.
  `map_note` (separate field) = "strongest on Mirage/Inferno": OFF (MAP_NOTES = False). Split-half test 2026-11-07: the
  named maps came back as the other half's two strongest maps at chance rate (70 of 140 vs 66.7 by chance; no lift / sample
  setting beat chance), so per-map differences in these signals are noise at the samples we have. The code stays for a
  re-test; with it off the field is always None.

Rounds: map_player_stats has no round count; the team's opening duels on a map-side (sum of ok + od) is one per round with a
kill (checked on the extract: median 1.00 per round, mean 1.03), so "per round" = per team opening duel.

build() is pure (rows in, artifact out) - the research validation runs exactly this function on the local HLTV extract.
Production: refresh() reads ONE bounded aggregate (180 days, grouped in the database to about 40k rows, 60 s statement
timeout, read-only pooled connection via hltv.py's helpers) at most once a day, in a background thread (maybe_refresh(); the
board loop may call it - it returns at once), keeps the last good artifact on any failure, and saves it to
DATA_DIR/cs2_roles.json. card_role(nick, team) is a dictionary lookup - safe on a member request.
"""
import array
import bisect
import json
import math
import os
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path

VERSION = "cr-1"

# ---- method constants (chosen on the extract; research/cs2_roles_2026_11_07/validate.py -> results.json)
WINDOW_DAYS = 180         # maps read: the last 180 days (median active player: 89 maps, 7 distinct maps)
HALF_LIFE_DAYS = 90       # a map 90 days old counts half: follows roster / role moves, keeps ~2/3 of the sample weight
MIN_MAPS = 30             # a player needs 30 maps in the window for any stat role
ACTIVE_DAYS = 45          # ... and a map within 45 days of the newest data
LINEUP_DAYS = 30          # lineup = players whose latest team is the team, last map within 30 days of the team's last map
MAP_MIN = 10              # maps on one map before it counts toward "holds across most maps"
MAP_HOLD = 2 / 3          # the role's pattern must hold on >= 2/3 of those maps (and >= 2 of them)
MAP_NOTES = False         # map note off: not reproducible on split halves (results.json split_half); NOTE_* kept for a re-test
NOTE_MIN = 15             # maps on each named map for a map note; >= 3 such maps
NOTE_LIFT = 1.10          # the named maps' mean signal >= 110% of the player's other qualifying maps' mean
FAIR = 0.2                # a fair share of five

AWP_SHARE, AWP_MIN_KILLS, RIFLE_SHARE = 0.25, 100, 0.10   # = roles.py (read only; that module's values feed a stake gate)

# league percentile gates (among active players) and per-map gates (team-relative share on that map)
STAR_K_PCT, STAR_RATING_PCT, STAR_MAP = 85, 80, 0.22      # kill share / rating; map: >= 22% of team kills
ENTRY_PCT, ENTRY_TD_PCT, ENTRY_MAP = 85, 50, 0.23         # T-side opening share / traded-death share; map: T open >= 23%
LURK_PCT, LURK_TD_PCT, LURK_TK_PCT, LURK_MAP = 25, 40, 50, 0.17  # T open <= p25, traded <= p40, T/CT kill rate >= p50
SUPPORT_FA_PCT, SUPPORT_K_PCT, SUPPORT_MAP = 80, 40, 0.25  # flash assists/round >= p80, kill share <= p40; map: >= 25% of team fa
LISTED_NAMES = {"entry": "Entry", "entryfragger": "Entry", "lurk": "Lurker", "lurker": "Lurker", "support": "Support"}


def mkey(key):
    """The per-map signal for a role's overall signal (flash assists: share of the team's on that map)."""
    return "fa_share" if key == "fa_pr" else key


# a listed role is shown when the player's stats do not contradict it (league percentiles)
LISTED_ROLES = (
    ("Entry", ("t_open", True), lambda q: (q["t_open"] or 0) >= 50),
    ("Lurker", ("t_open", False), lambda q: q["t_open"] is not None and q["t_open"] <= 50),
    ("Support", ("fa_pr", True), lambda q: q["k_share"] is not None and q["k_share"] <= 80),
)
# stat roles, in this order (a rifler takes the first he wins; each role once per lineup)
STAT_ROLES = (
    ("Star rifler", ("k_share", True),
     lambda q: (q["k_share"] or 0) >= STAR_K_PCT and (q["rating"] or 0) >= STAR_RATING_PCT, lambda v: v >= STAR_MAP),
    ("Entry", ("t_open", True),
     lambda q: (q["t_open"] or 0) >= ENTRY_PCT and (q["td_share"] or 0) >= ENTRY_TD_PCT, lambda v: v >= ENTRY_MAP),
    ("Lurker", ("t_open", False),
     lambda q: q["t_open"] is not None and q["t_open"] <= LURK_PCT and q["td_share"] is not None
     and q["td_share"] <= LURK_TD_PCT and (q["tk_ratio"] or 0) >= LURK_TK_PCT, lambda v: v <= LURK_MAP),
    ("Support", ("fa_pr", True),
     lambda q: (q["fa_pr"] or 0) >= SUPPORT_FA_PCT and q["k_share"] is not None and q["k_share"] <= SUPPORT_K_PCT,
     lambda v: v >= SUPPORT_MAP),
)

SUMS = ("w", "k", "a", "fa", "d", "td", "ok", "od", "cl", "rt", "tk", "tat", "tfa")


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _d(x):
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def decay(age_days, half_life=HALF_LIFE_DAYS):
    return 0.5 ** (max(0.0, float(age_days)) / half_life)


def _div(a, b):
    return a / b if b else None


def _pct(sorted_vals, v):
    """Share of the pool strictly below v, in percent (None for no value)."""
    if v is None or not sorted_vals:
        return None
    return 100.0 * bisect.bisect_left(sorted_vals, v) / len(sorted_vals)


# ------------------------------------------------------------------------------------------------------------ pure core
def build(rows, awp=None, listed=None, ref_date=None, team_igl_marks=None):
    """rows: aggregated map-side rows, dicts with
         pid, nick, tid, team, map, side ('t' | 'ct'), n (maps, raw count), last (date of the newest map),
         and decayed sums w, k, a, fa, d, td, ok, od, cl, rt (rating x weight), tk / tat / tfa (the team's kills, opening
         duels = ok + od, flash assists on the same map-side).
       awp: {pid: (kills, awp_share)} from player_awp.  listed: {pid: {roles, is_igl, team_match, status, hltv_team}} from
       player_roles.  team_igl_marks: {norm team name: {norm nick, ...}} from a Liquipedia team page (tie-break only).
    Returns {"version", "ref_date", "players": {pid: entry}, "teams": {tid: [pid, ...]}, "counts"}; an entry has
    nick, team, tid, role, stat_role, igl, map_note, source (hltv-awp | liquipedia | stats), maps, last, signals, pct."""
    awp, listed, marks = awp or {}, listed or {}, team_igl_marks or {}
    # rows may be a generator (production streams the cursor): only these small accumulators are kept - per player and per
    # player-map, a (T sums, CT sums) pair of double arrays (~40k small arrays for a 180-day window, a few MB)
    P, M, info = {}, {}, {}          # pid -> [t, ct]; (pid, map) -> [t, ct]; pid -> meta
    nsum = len(SUMS)
    zero = [0.0] * nsum
    for r in rows:
        pid, side = r["pid"], r["side"]
        if side not in ("t", "ct"):
            continue
        last = _d(r["last"])
        i = info.setdefault(pid, {"nick": r.get("nick"), "teams": {}, "maps": 0, "last": last, "map_n": {}})
        t = i["teams"].setdefault(r["tid"], {"name": r.get("team"), "last": last, "n": 0})
        if last > t["last"]:
            t["last"] = last
        if last >= i["last"]:
            i["last"], i["nick"] = last, r.get("nick") or i["nick"]
        if side == "t":                                    # each map has a T and a CT row: count maps once
            i["maps"] += r["n"]
            t["n"] += r["n"]
            i["map_n"][r["map"]] = i["map_n"].get(r["map"], 0) + r["n"]
        si = 0 if side == "t" else 1
        vals = [float(r.get(c) or 0.0) for c in SUMS]
        for tgt in (P.setdefault(pid, (array.array("d", zero), array.array("d", zero)))[si],
                    M.setdefault((pid, r["map"]), (array.array("d", zero), array.array("d", zero)))[si]):
            for j in range(nsum):
                tgt[j] += vals[j]
    if not info:
        return {"version": VERSION, "ref_date": str(ref_date) if ref_date else None, "players": {}, "teams": {}, "counts": {}}
    ref = _d(ref_date) if ref_date else max(i["last"] for i in info.values())

    def sig(pair):
        t, c = dict(zip(SUMS, pair[0])), dict(zip(SUMS, pair[1]))
        tot = {k: t[k] + c[k] for k in SUMS}
        kt, kc = _div(t["k"], t["tat"]), _div(c["k"], c["tat"])
        return {"t_open": _div(t["ok"] + t["od"], t["tat"]), "open": _div(tot["ok"] + tot["od"], tot["tat"]),
                "k_share": _div(tot["k"], tot["tk"]), "fa_pr": _div(tot["fa"], tot["tat"]),
                "fa_share": _div(tot["fa"], tot["tfa"]), "td_share": _div(tot["td"], tot["d"]),
                "tk_ratio": _div(kt, kc) if kt is not None and kc else None,
                "rating": _div(tot["rt"], tot["w"]), "cl_pr": _div(tot["cl"], tot["tat"])}

    S = {pid: sig(s) for pid, s in P.items()}
    # current team = team of the newest map (most maps on a tie)
    for pid, i in info.items():
        tid, t = max(i["teams"].items(), key=lambda kv: (kv[1]["last"], kv[1]["n"]))
        i["tid"], i["team"] = tid, t["name"]
    active = {pid for pid, i in info.items() if i["maps"] >= MIN_MAPS and (ref - i["last"]).days <= ACTIVE_DAYS}
    pool = {k: sorted(S[p][k] for p in active if S[p][k] is not None)
            for k in ("t_open", "k_share", "fa_pr", "td_share", "tk_ratio", "rating")}
    pct = {p: {k: _pct(pool[k], S[p][k]) for k in pool} for p in active}

    team_last = {}
    for p in active:
        tid = info[p]["tid"]
        team_last[tid] = max(team_last.get(tid, info[p]["last"]), info[p]["last"])
    lineup = {}
    for p in active:
        tid = info[p]["tid"]
        if (team_last[tid] - info[p]["last"]).days <= LINEUP_DAYS:
            lineup.setdefault(tid, []).append(p)

    def awp_of(p):
        a = awp.get(p)
        if not a or a[1] is None or (a[0] or 0) < AWP_MIN_KILLS:
            return None
        return float(a[1])

    def listed_awper(p):
        x = listed.get(p) or {}
        return bool(x.get("team_match")) and "awp" in [str(v).strip().lower() for v in (x.get("roles") or [])]

    def listed_role(p):
        """The first specific role in the player's Liquipedia listing (entry / lurk / support), current team only."""
        x = listed.get(p) or {}
        if not x.get("team_match") or x.get("status") not in (None, "ok"):
            return None
        for v in x.get("roles") or []:
            r = LISTED_NAMES.get(str(v).strip().lower())
            if r:
                return r
        return None

    def holds(p, key, ok):
        """The pattern holds on >= MAP_HOLD of the player's maps with >= MAP_MIN played (and on >= 2 such maps)."""
        q = [m for m, n in info[p]["map_n"].items() if n >= MAP_MIN]
        vals = [sig(M[(p, m)])[key] for m in q]
        vals = [v for v in vals if v is not None]
        return len(vals) >= 2 and sum(ok(v) for v in vals) >= MAP_HOLD * len(vals)

    def note(p, key, high=True):
        if not MAP_NOTES:
            return None
        q = [m for m, n in info[p]["map_n"].items() if n >= NOTE_MIN]
        vals = [(sig(M[(p, m)])[key], m) for m in q]
        vals = [(v, m) for v, m in vals if v is not None]
        if len(vals) < 3:
            return None
        vals.sort(key=lambda x: x[0], reverse=high)
        top, rest = vals[:2], vals[2:]
        a, b = sum(v for v, _ in top) / 2, sum(v for v, _ in rest) / len(rest)
        lift = (a / b if b else math.inf) if high else (b / a if a else math.inf)
        if lift < NOTE_LIFT:
            return None
        return "strongest on " + "/".join(m for _, m in top)

    out, teams = {}, {}
    for tid, members in lineup.items():
        teams[tid] = sorted(members)
        roles = {p: None for p in members}
        notes = {}
        src = {}
        # AWPer: the real weapon field (HLTV AWP share); the team's highest share only. No share on file: a Liquipedia
        # AWP listing for the current team stands in, unless the field says otherwise (share <= RIFLE_SHARE)
        aw = sorted((awp_of(p), p) for p in members if (awp_of(p) or 0) >= AWP_SHARE)
        awper = aw[-1][1] if aw else None
        if awper is None:
            la = [p for p in members if awp_of(p) is None and listed_awper(p)]
            awper = la[0] if len(la) == 1 else None
            if awper is not None:
                src[awper] = "liquipedia"
        if awper is not None:
            roles[awper] = "AWPer"
            src.setdefault(awper, "hltv-awp")
        # riflers: no AWP signal (share known <= RIFLE_SHARE, or no share and no Liquipedia AWP listing)
        riflers = [p for p in members if p != awper and (
            (awp_of(p) is not None and awp_of(p) <= RIFLE_SHARE) or (awp_of(p) is None and not listed_awper(p)))]
        taken = set()

        # 1) a listed role (Liquipedia infobox: entry / lurk / support, current team) where the stats do not contradict it
        for role, (key, high), agrees in LISTED_ROLES:
            cs = [p for p in riflers if roles[p] is None and listed_role(p) == role and S[p][key] is not None
                  and agrees(pct[p])]
            if cs:
                p = (max if high else min)(cs, key=lambda q: S[q][key])
                roles[p], src[p], notes[p] = role, "liquipedia", note(p, mkey(key), high)
                taken.add(role)

        # 2) stat roles for the riflers still without one: league gate + per-map hold; the team's most extreme passer
        for role, (key, high), gate, ok in STAT_ROLES:
            if role in taken:
                continue
            cs = [p for p in riflers if roles[p] is None and S[p][key] is not None and gate(pct[p])
                  and holds(p, mkey(key), ok)]
            if cs:
                p = (max if high else min)(cs, key=lambda q: S[q][key])
                roles[p], src[p], notes[p] = role, "stats", note(p, mkey(key), high)
                taken.add(role)

        # IGL: exactly one listed lineup player (a team-page igl=y marker may pick one of several)
        def is_listed_igl(p):
            x = listed.get(p) or {}
            return bool(x.get("is_igl")) and bool(x.get("team_match")) and (x.get("status") in (None, "ok")) and (
                not x.get("hltv_team") or _norm(x["hltv_team"]) == _norm(info[p]["team"]))
        igls = [p for p in members if is_listed_igl(p)]
        if len(igls) > 1:
            mk = marks.get(_norm(info[members[0]]["team"])) or set()
            igls = [p for p in igls if _norm(info[p]["nick"]) in mk] if mk else []
        igl = igls[0] if len(igls) == 1 else None

        for p in members:
            i, s = info[p], S[p]
            out[p] = {"nick": i["nick"], "team": i["team"], "tid": tid,
                      "role": "IGL" if p == igl else roles[p], "stat_role": roles[p], "igl": p == igl,
                      "map_note": notes.get(p), "source": src.get(p), "maps": i["maps"], "last": i["last"].isoformat(),
                      "signals": {k: (round(v, 4) if v is not None else None) for k, v in s.items()},
                      "pct": {k: (round(v) if v is not None else None) for k, v in pct[p].items()},
                      "awp_share": awp_of(p)}
    counts = {}
    for e in out.values():
        counts[e["role"] or ""] = counts.get(e["role"] or "", 0) + 1
    return {"version": VERSION, "ref_date": ref.isoformat(), "players": out, "teams": teams, "counts": counts}


# ------------------------------------------------------------------------------------------------- production (read only)
_AGG_SQL = """
WITH s AS (
  SELECT s.playerid, s.teamid, s.matchid, s.mapstatsid, s.sideid, mo.date,
         power(0.5, (%(ref)s::date - mo.date) / %(hl)s::float) AS w,
         s.k, s.a, s.fa, s.d, s.td, s.ok, s.od, s.clutches, s.rating,
         sum(s.k) OVER tm AS tk, sum(s.ok + s.od) OVER tm AS tat, sum(s.fa) OVER tm AS tfa
  FROM map_player_stats s JOIN match_overview mo ON mo.matchid = s.matchid
  WHERE s.sideid IN (1, 2) AND mo.date >= %(since)s AND mo.date <= %(ref)s
  WINDOW tm AS (PARTITION BY s.mapstatsid, s.teamid, s.sideid)
), g AS (
  SELECT s.playerid, s.teamid, mp.name AS map, s.sideid, count(*) AS n, max(s.date) AS last, sum(w) AS w,
         sum(w * k), sum(w * a), sum(w * fa), sum(w * d), sum(w * td), sum(w * ok), sum(w * od), sum(w * clutches),
         sum(w * coalesce(rating, 0)), sum(w * tk), sum(w * tat), sum(w * tfa)
  FROM s JOIN match_maps mm ON mm.matchid = s.matchid AND mm.mapstatsid = s.mapstatsid JOIN maps mp ON mp.mapid = mm.mapid
  GROUP BY 1, 2, 3, 4
)
SELECT g.*, p.name, t.name FROM g JOIN players p ON p.playerid = g.playerid LEFT JOIN teams t ON t.teamid = g.teamid
"""
_LISTED_SQL = """
SELECT coalesce(r.playerid, a.playerid), r.roles, r.is_igl, r.team_match, r.status, r.hltv_team, a.kills, a.awp_share
FROM player_roles r FULL JOIN player_awp a ON a.playerid = r.playerid
"""
TTL = 24 * 3600           # one rebuild a day
RETRY = 30 * 60           # a failed rebuild is tried again after 30 minutes (the last good artifact stays meanwhile)
MIN_GOOD = 200            # an artifact with fewer players than this is treated as a failed read, never swapped in
STATEMENT_MS = 60000


def _path():
    from bo3 import DATA_DIR
    return Path(DATA_DIR) / "cs2_roles.json"


_STATE = {"art": None, "by_nick": {}, "at": 0.0, "tried": 0.0, "error": None, "loaded": False}
_LOCK = threading.Lock()
_RUN = threading.Lock()


def _index(art):
    by = {}
    for pid, e in (art.get("players") or {}).items():
        by.setdefault(_norm(e.get("nick")), []).append(dict(e, pid=pid))
    return by


def _install(art, at):
    by = _index(art)
    with _LOCK:
        _STATE.update(art=art, by_nick=by, at=at, loaded=True)


def _load_saved():
    """The last saved artifact (restart): yesterday's roles beat none. Once per process."""
    if _STATE["loaded"]:
        return
    with _LOCK:
        if _STATE["loaded"]:
            return
        _STATE["loaded"] = True
    try:
        art = json.loads(_path().read_text(encoding="utf-8"))
        if len(art.get("players") or {}) >= MIN_GOOD:
            _install(art, float(art.get("built_at") or 0.0))
    except (OSError, ValueError, TypeError):
        pass


def read_and_build(ref=None, team_igl_marks=None):
    """The production read: the two small listing tables, then ONE aggregate statement whose result is streamed straight
    into build() (no Python copy of the ~40k rows). Read only, pooled connection, 60 s statement timeout. Raises on failure.
    Measured 2026-11-07 against production: 42,562 rows, read 5.5 s + build 3.7 s."""
    import hltv
    ref = _d(ref) if ref else date.today()
    since = ref - timedelta(days=WINDOW_DAYS)
    con = hltv._connect()
    try:
        with con.cursor() as cur:
            cur.execute("SET statement_timeout = %d" % STATEMENT_MS)   # the pool resets it before reuse (hltv._TAINTED)
            cur.execute(_LISTED_SQL)
            awp, listed = {}, {}
            for pid, roles_, igl, tmatch, status, hteam, kills, share in cur.fetchall():
                pid = str(pid)
                if share is not None:
                    awp[pid] = (kills, float(share))
                if roles_ is not None or igl is not None:
                    listed[pid] = {"roles": [x.strip() for x in (roles_ or "").split(",") if x.strip()], "is_igl": igl,
                                   "team_match": tmatch, "status": status, "hltv_team": hteam}
            cur.execute(_AGG_SQL, {"ref": ref.isoformat(), "since": since.isoformat(), "hl": float(HALF_LIFE_DAYS)})
            return build(_as_dicts(iter(cur.fetchone, None)), awp, listed, ref_date=ref, team_igl_marks=team_igl_marks)
    finally:
        con.close()


def _as_dicts(rows):
    """The aggregate tuples, one dict at a time (build() keeps only its accumulators)."""
    for (pid, tid, mp, sideid, n, last, w, k, a, fa, d, td, ok, od, cl, rt, tk, tat, tfa, nick, team) in rows:
        yield {"pid": str(pid), "tid": str(tid), "map": mp, "side": "ct" if sideid == 1 else "t", "n": int(n),
               "last": last, "w": w, "k": k, "a": a, "fa": fa, "d": d, "td": td, "ok": ok, "od": od, "cl": cl, "rt": rt,
               "tk": tk, "tat": tat, "tfa": tfa, "nick": nick, "team": team}


def _team_marks():
    """Liquipedia team-page igl=y markers already cached by roles.py (memory/disk only - never a request)."""
    try:
        import roles
        teams = (roles._load() or {}).get("teams") or {}
        return {k: {_norm(x) for x in (v.get("igl") or [])} for k, v in teams.items() if v.get("igl")}
    except Exception:
        return {}


def refresh(now=None):
    """Rebuild from the database. Keeps the last good artifact on any failure. Never raises. Returns True on a swap."""
    now = time.time() if now is None else now
    _STATE["tried"] = now
    try:
        art = read_and_build(team_igl_marks=_team_marks())
        if len(art["players"]) < MIN_GOOD:
            _STATE["error"] = "too few players (%d)" % len(art["players"])
            return False
        art["built_at"] = now
        _install(art, now)
        _STATE["error"] = None
        try:
            p = _path()
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(art, default=str), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass
        return True
    except Exception as e:                       # database down / slow / tables missing: the last good artifact stays
        _STATE["error"] = type(e).__name__
        return False


def maybe_refresh(now=None):
    """For a periodic loop (e.g. the board loop): starts one background rebuild when due; returns at once. Never raises."""
    try:
        _load_saved()
        now = time.time() if now is None else now
        if now - _STATE["at"] < TTL or now - _STATE["tried"] < RETRY:
            return False
        try:
            import hltv
            if not hltv.dsn():
                return False
        except Exception:
            return False
        if not _RUN.acquire(blocking=False):
            return False
        _STATE["tried"] = now

        def run():
            try:
                refresh(now)
            finally:
                _RUN.release()
        threading.Thread(target=run, daemon=True, name="cs2-roles").start()
        return True
    except Exception:
        return False


def card_role(nick, team=None, benched=False):
    """The one accessor for a card payload: {"role", "stat_role", "map_note"} or None. A dictionary lookup on the precomputed artifact.
    Benched players (BO3.gg status 2 / not in the board lineup) get nothing. The team must match (exact name, or hltv.py's
    same_team name variants) - never a fuzzy / academy match."""
    if benched or not nick:
        return None
    if not _STATE["loaded"]:
        _load_saved()
    rows = _STATE["by_nick"].get(_norm(nick)) or []
    if not rows:
        return None
    hit = None
    if team:
        nt = _norm(team)
        same = [e for e in rows if _norm(e.get("team")) == nt]
        if not same:
            try:
                import hltv
                same = [e for e in rows if e.get("team") and hltv.same_team(team, e["team"])]
            except Exception:
                same = []
        hit = same[0] if len(same) == 1 else None
    elif len(rows) == 1:
        hit = rows[0]
    if not hit or not hit.get("role"):
        return None
    return {"role": hit["role"], "stat_role": hit.get("stat_role"), "map_note": hit.get("map_note")}


def status():
    return {"version": VERSION, "players": len((_STATE["art"] or {}).get("players") or {}), "built_at": _STATE["at"],
            "last_try": _STATE["tried"], "error": _STATE["error"], "counts": (_STATE["art"] or {}).get("counts")}


if __name__ == "__main__":      # one rebuild by hand (e.g. a Render Shell): reads only, writes DATA_DIR/cs2_roles.json
    refresh()
    print(json.dumps(status(), default=str))
