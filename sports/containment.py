"""ct-1 / ct-1b: THE research-letter containment source per league (REGISTER.md amendments A9a 4 and A9b). Pure
python, no numpy.

    contained(league, stat) -> True = no lean / letter / edge for that market anywhere (board, cards, compare, sheets,
                               slips, tracked picks, public results) - members see the existing contained wording
                               (board_view.GATE_TEXT).

ct-1b (owner decision 2026-11-08, in chat: "drop grades for NFL and NBA" = publish them): NBA and NFL letters and leans
are RELEASED by the owner (OWNER_RELEASE). The daily check keeps computing its A9 5 verdict and logging it (file "log",
admin gradecheck view: snapshot()["verdict"] beside snapshot()["letters"]), but neither its league verdict, its per-stat
"contain:<stat>" verdicts nor the pc-1 held-out result (A10) hides an owner-released league's letters. Data-integrity
holds are not containment and stay as they are (sg-2 sanity holds, one-sided lines, no matching forecast).

Order (first that answers wins):
  1. env SPORTS_CONTAIN_NBA / SPORTS_CONTAIN_NFL = 1 (contained) - the owner's kill switch; it always contains.
     0 / off / false asks for nothing the owner release does not already give: logged as asked, no change applied;
  2. OWNER_RELEASE (ct-1b): the league's letters are shown;
  3. pc-1 held-out failure (A10, chance_cal.letters_held) - only for a league the owner has not released;
  4. <SPORTS_DATA_DIR>/model/containment.json - written by the daily grade check (sports/model/daily.py, A9 5 verdict);
  5. the committed seed sports/model/artifacts/containment_default.json (owner-run gradecheck 2026-10-28: NBA + NFL
     contained);
  6. the legacy rule: board_view.VG_CONTAIN_LEAN and the stat failed its registered gate.
For an owner-released league steps 3-6 are still read and reported (the verdict), never applied.

State per league: {"contained": bool, "stats": [stat, ...]} - the league as a whole, or only the named stats (A9 5
"contain:<stat>"). Every decision is appended to the file's "log" (date, verdict, n, hit, stated, CI, before / after,
"applied" = what members see). The file's mtime is re-checked every CHECK_S seconds, so a daily decision reaches the
next board build without a deploy.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "ct-1"                        # the state FILE format (containment.json); unchanged by ct-1b
RULE = "ct-1b"
REGISTER = "sports/research/REGISTER.md A9a"
# ct-1b (REGISTER A9b amendment): the owner released these leagues' letters on this date. SPORTS_CONTAIN_<LG>=1 still
# contains (kill switch). Removing a league here returns it to the daily verdict / pc-1 rule (a deploy).
_OWNER_WHY = "owner decision 2026-11-08 (chat): publish the NBA and NFL grades"
OWNER_RELEASE = {
    "nba": {"rule": RULE, "date": "2026-11-08", "by": "owner", "why": _OWNER_WHY},
    "nfl": {"rule": RULE, "date": "2026-11-08", "by": "owner", "why": _OWNER_WHY},
}
LEAGUES = ("nba", "nfl")
FILE_NAME = "containment.json"
DEFAULT_PATH = Path(__file__).resolve().parent / "model" / "artifacts" / "containment_default.json"
LOG_MAX = 400
CHECK_S = 30.0
_ON, _OFF = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def path():
    from . import config
    return config.DATA_DIR / "model" / FILE_NAME


ENV_RULE = ("ct-1b: SPORTS_CONTAIN_<LG>=1 contains (owner kill switch); 0 / off / false changes nothing - NBA and NFL "
            "letters are released by owner decision 2026-11-08; the daily verdict is computed and logged, not applied")


def env_override(league):
    """True / False from SPORTS_CONTAIN_<LG>, None when unset or not a switch value. What the switch ASKS: only True is
    applied (_resolve); False is recorded as a refused release."""
    v = os.environ.get(f"SPORTS_CONTAIN_{(league or '').upper()}", "").strip().lower()
    return True if v in _ON else (False if v in _OFF else None)


_logged = set()


def _log_env(league, st, log=print):
    """One line per process per (league, asked, applied, source): what the env asked for and what applied."""
    asked = st.get("env_asked")
    if asked is None:
        return
    key = (league, asked, st["contained"], st["source"])
    if key in _logged:
        return
    _logged.add(key)
    try:
        log(f"[containment {VERSION}] {league.upper()}: SPORTS_CONTAIN_{league.upper()}={st.get('env_value')!r} asked to "
            f"{asked}; applied: {'contained' if st['contained'] else 'letters shown'} (source {st['source']})"
            + ("" if st.get("env_applied") else
               " - the switch can only contain; " + ("the owner released this league (ct-1b)" if league in OWNER_RELEASE
                                                     else "release comes from the daily verdict")),
            flush=True)
    except Exception:                                   # noqa: BLE001 - a log line never breaks the board
        pass


def _valid(obj):
    if not isinstance(obj, dict) or obj.get("version") != VERSION or not isinstance(obj.get("leagues"), dict):
        return None
    for lg, s in obj["leagues"].items():
        if not isinstance(s, dict) or not isinstance(s.get("contained"), bool) or \
                not all(isinstance(x, str) for x in (s.get("stats") or [])):
            return None
    return obj


_cache = {"t": 0.0, "key": None, "obj": None, "src": None}


def reset_cache():
    _cache.update(t=0.0, key=None, obj=None, src=None)
    _states.update(t=0.0, key=None, by={})


def load(force=False):
    """-> (state obj, "file" | "default") or (None, None). Cached; mtimes re-checked every CHECK_S seconds."""
    now = time.time()
    if not force and _cache["key"] is not None and now - _cache["t"] < CHECK_S:
        return _cache["obj"], _cache["src"]
    cands = []
    try:
        cands.append((path(), "file"))
    except Exception:                                   # noqa: BLE001
        pass
    cands.append((DEFAULT_PATH, "default"))
    keys = []
    for p, _src in cands:
        try:
            keys.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            keys.append((str(p), None))
    key = tuple(keys)
    if not force and key == _cache["key"]:
        _cache["t"] = now
        return _cache["obj"], _cache["src"]
    obj, used = None, None
    for (p, src), (_, mt) in zip(cands, keys):
        if mt is None:
            continue
        try:
            obj = _valid(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            obj = None
        if obj is not None:
            used = src
            break
    _cache.update(t=now, key=key, obj=obj, src=used)
    return obj, used


_states = {"t": 0.0, "key": None, "by": {}}


def league_state(league):
    """-> {"contained", "stats", "source"} for one league (source: env / file / default / legacy). Resolved once per
    CHECK_S window (an env change restarts the process on Render; reset_cache() for tests) - a dict read per call on
    the member request path, not an environment read."""
    now = time.time()
    if now - _states["t"] < CHECK_S and _states["key"] is _cache["key"] and league in _states["by"]:
        return _states["by"][league]
    if now - _states["t"] >= CHECK_S or _states["key"] is not _cache["key"]:
        _states.update(t=now, by={})
    st = _resolve(league)
    _states["key"] = _cache["key"]
    _states["by"][league] = st
    return st


def _resolve(league):
    ov = env_override(league)
    if ov is True:                                      # the owner's switch can only contain
        st = {"contained": True, "stats": [], "source": "env"}
    else:
        obj, src = load()
        s = ((obj or {}).get("leagues") or {}).get(league)
        if s is not None:
            st = {"contained": s["contained"], "stats": sorted(s.get("stats") or []), "source": src}
        else:
            st = {"contained": None, "stats": [], "source": "legacy"}
    if st["source"] != "env" and league in OWNER_RELEASE:   # ct-1b: reported beside the verdict state
        st["owner_release"] = dict(OWNER_RELEASE[league])
    if ov is not None:
        st.update(env_asked="contain" if ov else "release", env_applied=bool(ov),
                  env_value=os.environ.get(f"SPORTS_CONTAIN_{league.upper()}"))
        _log_env(league, dict(st, contained=_letters_of(league, st) != "live"))
    return st


def pc_held(league):
    """REGISTER A10 (pc-1): True when the printed-chance calibration in use failed its held-out test for this league -
    letters stay held there (the printed chance is still the calibrated one). It can only contain, never release.
    ct-1b: reported, not applied, for an owner-released league."""
    try:
        from .model import chance_cal
        return chance_cal.letters_held(league)
    except Exception:                                   # noqa: BLE001 - never breaks the board
        return False


def released(league):
    """ct-1b: True when the owner released this league's letters and the kill switch (SPORTS_CONTAIN_<LG>=1) is off."""
    return bool(league) and league in OWNER_RELEASE and league_state(league)["source"] != "env"


def contained(league, stat):
    """THE containment rule for one market (A9a 4; A10: a failed pc-1 held-out test holds the league; ct-1b: NBA / NFL
    are released by the owner - only SPORTS_CONTAIN_<LG>=1 contains them)."""
    if not league:
        return False
    st = league_state(league)
    if st["source"] == "env":                           # the owner's kill switch always contains
        return True
    if league in OWNER_RELEASE:                         # ct-1b: released; the daily / pc-1 verdicts are logged only
        return False
    if pc_held(league):                                 # pc-1: a league whose held-out calibration test failed
        return True
    if st["source"] != "legacy":
        return bool(st["contained"]) or stat in st["stats"]
    from . import board_view                            # legacy: VG_CONTAIN_LEAN + failed registered gate
    return bool(board_view.VG_CONTAIN_LEAN) and board_view.gate_reason(league, stat) is not None


def _verdict_word(league, st):
    """What the daily verdict / pc-1 result alone would apply ('contained' | 'contained (stat, ...)' | 'live')."""
    return "contained" if pc_held(league) else letters_word(st, league)


def _letters_of(league, st):
    if st.get("source") == "env":
        return "contained"
    if league in OWNER_RELEASE:
        return "live"
    return _verdict_word(league, st)


def letters(league):
    """What members see for this league: 'contained' | 'contained (stat, ...)' | 'live' (ct-1b: owner release)."""
    return _letters_of(league, league_state(league))


def snapshot():
    """Admin / gradecheck view: per league state + the env switches and where the state came from. "letters" = what
    members see; "verdict" = what the daily check / pc-1 alone would apply - logged, not applied, for a league in
    "owner_release" (ct-1b)."""
    obj, src = load()
    leagues = {lg: league_state(lg) for lg in LEAGUES}
    return {"version": VERSION, "rule": RULE, "source": src, "updated_at": (obj or {}).get("updated_at"), "leagues": leagues,
            "env": {f"SPORTS_CONTAIN_{lg.upper()}": os.environ.get(f"SPORTS_CONTAIN_{lg.upper()}") for lg in LEAGUES},
            "env_rule": ENV_RULE, "letters": {lg: _letters_of(lg, leagues[lg]) for lg in LEAGUES},
            "verdict": {lg: _verdict_word(lg, leagues[lg]) for lg in LEAGUES},
            "owner_release": {lg: OWNER_RELEASE.get(lg) for lg in LEAGUES},
            "pc1_held": {lg: pc_held(lg) for lg in LEAGUES},
            "env_requests": {lg: {"name": f"SPORTS_CONTAIN_{lg.upper()}", "value": os.environ.get(f"SPORTS_CONTAIN_{lg.upper()}"),
                                  "asked": leagues[lg].get("env_asked"), "applied": bool(leagues[lg].get("env_applied"))}
                             for lg in LEAGUES}}


def letters_word(st, league=None):
    """'contained' | 'contained (stat, ...)' | 'live' for one league state - the status every admin view shows."""
    c = st.get("contained")
    if c is None and league:                            # legacy rule: NFL held while VG_CONTAIN_LEAN is on
        from . import board_view
        c = bool(board_view.VG_CONTAIN_LEAN) and league == "nfl"
    if c:
        return "contained"
    return "contained (" + ", ".join(st.get("stats") or []) + ")" if st.get("stats") else "live"


# ============================================================================================== the A9 5 reading
def decide(before, verdict):
    """(before {"contained", "stats"}, A9 5 verdict string) -> after. release -> shown; contain -> league hidden;
    contain:<a,b> -> those stats hidden too (a league hidden as a whole stays hidden); keep / insufficient -> same."""
    b = {"contained": bool(before.get("contained")), "stats": sorted(before.get("stats") or [])}
    v = (verdict or "").strip()
    if v == "release":
        return {"contained": False, "stats": []}
    if v == "contain":
        return {"contained": True, "stats": b["stats"]}
    if v.startswith("contain:"):
        named = [x for x in v[len("contain:"):].split(",") if x]
        return {"contained": b["contained"], "stats": sorted(set(b["stats"]) | set(named))}
    return b                                            # keep / insufficient / anything unknown: no change


def _before(obj, league):
    s = ((obj or {}).get("leagues") or {}).get(league)
    if s is not None:
        return {"contained": s["contained"], "stats": sorted(s.get("stats") or [])}
    from . import board_view                            # no state yet: the legacy league state (NFL failed every gate)
    return {"contained": bool(board_view.VG_CONTAIN_LEAN) and league == "nfl", "stats": []}


def apply_verdicts(gc_result, day, source, out=None):
    """Daily step 3 (A9a 4): read each league's A9 5 verdict from a gradecheck result, decide, append the log, write
    the state atomically. -> {league: {"verdict", "before", "after", "changed"}}."""
    obj, _src = load(force=True)
    cur = dict(obj or {})
    leagues = {lg: dict(s) for lg, s in ((cur.get("leagues") or {}).items())}
    log = list(cur.get("log") or [])
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    summary = {}
    for lg, rep in sorted(((gc_result or {}).get("leagues") or {}).items()):
        v = (rep or {}).get("verdict") or {}
        before = _before(cur, lg)
        after = decide(before, v.get("verdict"))
        changed = after != before
        leagues[lg] = {**after, "verdict": v.get("verdict"), "decided": day, "since": (
            day if changed else (leagues.get(lg) or {}).get("since") or day)}
        log.append({"date": day, "at": at, "league": lg, "verdict": v.get("verdict"), "why": v.get("why"),
                    "n": v.get("n"), "games": v.get("games"), "game_days": v.get("game_days"), "hit": v.get("hit"),
                    "stated": v.get("stated"), "ci": v.get("ci"), "before": before, "after": after,
                    "changed": changed, "source": source,
                    "applied": "owner release ct-1b: verdict logged, letters shown" if lg in OWNER_RELEASE else "applied"})
        summary[lg] = {"verdict": v.get("verdict"), "before": before, "after": after, "changed": changed,
                       **({"owner_release": RULE} if lg in OWNER_RELEASE else {})}
    doc = {"version": VERSION, "register": REGISTER, "updated_at": at, "leagues": leagues, "log": log[-LOG_MAX:]}
    p = Path(out) if out else path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    reset_cache()
    return summary
