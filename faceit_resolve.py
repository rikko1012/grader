#!/usr/bin/env python3
"""
faceit_resolve.py - find the right FACEIT account for an HLTV player.

First choice: the player's Steam ID (from BO3.gg match data) looked up on
FACEIT - an exact match, no guessing.

Fallback when there's no Steam ID or FACEIT doesn't know it:
pros' FACEIT nicknames often differ from their HLTV name, and lookalike/fan
accounts are everywhere. So a name match alone is never trusted. Each candidate
account is checked against independent evidence:

  name      FACEIT nickname or Steam in-game name matches the HLTV name
  badge     FACEIT "verified" badge (well-known pros)
  co-play   played FACEIT matches with the player's real teammates
            (teammates' own accounts are found the same way, then their last
            100 matches are scanned for this player)
  team      member of a FACEIT team whose roster matches the HLTV roster

Confidence:
  verified    name + at least one of badge / co-play / team
  likely      badge / co-play / team without a name match (renamed account)
  unverified  name match only - could be a fan account
  manual      set by hand in faceit_ids.csv (always wins)

Fully automatic: teammates come from the HLTV roster, and the member-facing app
only shows FACEIT data when show_to_members is True (verified / likely / manual,
not ambiguous). Everything else is hidden - members never have to fix anything.

Results are saved to faceit_ids.csv. Optional admin override: put the right
FACEIT nickname in and set confidence to "manual".
"""
import csv
import os
import re
from datetime import date, timedelta
from pathlib import Path

IDS_CSV = Path(os.environ.get("CS2_DATA_DIR") or Path(__file__).resolve().parent) / "faceit_ids.csv"
IDS_FIELDS = ["hltv_nick", "team", "faceit_nickname", "faceit_id", "confidence", "evidence", "checked"]
REUSE_DAYS = 7          # re-check auto matches weekly (players rename, change teams)
HISTORY = 100           # matches scanned per teammate
SEARCH_LIMIT = 20
TIERS = {"manual": 4, "verified": 3, "likely": 2, "unverified": 1}


# ---------------------------------------------------------------- names
def _alnum(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _letters(s):
    return re.sub(r"[^a-z]", "", str(s).lower())


def name_score(hltv, other):
    """3 identical, 2 same once symbols/digit-padding removed, 1 contains, 0 no."""
    if not hltv or not other:
        return 0
    if str(hltv).lower() == str(other).lower():
        return 3
    if _alnum(hltv) == _alnum(other) or (len(_letters(hltv)) >= 3 and _letters(hltv) == _letters(other)):
        return 2
    h, o = _alnum(hltv), _alnum(other)
    if len(h) >= 4 and h in o and len(o) <= len(h) + 6:
        return 1
    return 0


# ---------------------------------------------------------------- saved matches
def load_ids():
    if not IDS_CSV.exists():
        return []
    # utf-8-sig: Excel/Notepad on Windows add a BOM when the file is edited by hand
    with IDS_CSV.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("hltv_nick")]


def _saved(hltv_nick, team):
    rows = [r for r in load_ids() if r["hltv_nick"].lower() == hltv_nick.lower()
            and (not team or not r["team"] or r["team"].lower() == team.lower())]
    manual = [r for r in rows if r["confidence"] == "manual"]
    if manual:
        return manual[0]
    fresh = date.today() - timedelta(days=REUSE_DAYS)
    for r in rows:
        if r["confidence"] == "verified" and r.get("checked", "") >= fresh.isoformat():
            return r
    return None


def save_id(hltv_nick, team, res):
    rows = [r for r in load_ids() if not (r["hltv_nick"].lower() == hltv_nick.lower()
                                          and r["team"].lower() == (team or "").lower()
                                          and r["confidence"] != "manual")]
    if any(r["hltv_nick"].lower() == hltv_nick.lower() and r["confidence"] == "manual" for r in rows):
        return  # never overwrite a hand-set row
    rows.append({"hltv_nick": hltv_nick, "team": team or "", "faceit_nickname": res.get("nickname") or "",
                 "faceit_id": res.get("player_id") or "", "confidence": res["confidence"],
                 "evidence": " | ".join(res.get("evidence", [])), "checked": date.today().isoformat()})
    with IDS_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=IDS_FIELDS)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- resolver
class Resolver:
    def __init__(self, fc):
        self.fc = fc            # FaceitClient (cached, authenticated)
        self._details = {}

    def details(self, pid):
        if pid not in self._details:
            try:
                self._details[pid] = self.fc._get(f"/players/{pid}")
            except LookupError:
                self._details[pid] = {}
        return self._details[pid]

    def history(self, pid):
        try:
            return self.fc._get(f"/players/{pid}/history", {"game": "cs2", "limit": HISTORY}).get("items", [])
        except LookupError:
            return []

    def name_candidates(self, nick):
        """Exact lookup + FACEIT search -> {player_id: search info}."""
        found = {}
        try:
            p = self.fc._get("/players", {"nickname": nick})
            found[p["player_id"]] = {"nickname": p.get("nickname"), "verified": bool(p.get("verified"))}
        except LookupError:
            pass
        try:
            s = self.fc._get("/search/players", {"nickname": nick, "game": "cs2", "limit": SEARCH_LIMIT})
            for it in s.get("items", []):
                if name_score(nick, it.get("nickname")) or it.get("verified"):
                    found.setdefault(it["player_id"], {"nickname": it.get("nickname"),
                                                       "verified": bool(it.get("verified"))})
        except LookupError:
            pass
        return found

    def anchor(self, nick):
        """Best-guess account for a teammate (used only as a co-play anchor)."""
        cands = self.name_candidates(nick)
        best, key = None, None
        for pid, c in cands.items():
            d = self.details(pid)
            cs2 = (d.get("games") or {}).get("cs2") or {}
            if not cs2:
                continue
            k = (c["verified"], name_score(nick, c["nickname"]), cs2.get("faceit_elo") or 0)
            if k[0] or k[1] >= 2:
                if key is None or k > key:
                    best, key = pid, k
        return best

    def team_members(self, team, roster):
        """Members of FACEIT teams named like `team` whose roster overlaps the HLTV roster."""
        if not team or len(roster) < 2:
            return {}
        out = {}
        try:
            hits = self.fc._get("/search/teams", {"nickname": team, "game": "cs2", "limit": 10}).get("items", [])
        except LookupError:
            return {}
        for t in hits:
            try:
                members = self.fc._get(f"/teams/{t['team_id']}").get("members", [])
            except LookupError:
                continue
            overlap = sum(1 for r in roster if any(name_score(r, m.get("nickname")) >= 2 for m in members))
            if overlap >= 2:
                for m in members:
                    out[m["user_id"]] = {"nickname": m.get("nickname"), "team": t.get("name")}
        return out

    def by_steam(self, steam64):
        """Exact match: FACEIT stores each account's Steam ID; BO3.gg match data has the pro's."""
        try:
            p = self.fc._get("/players", {"game": "cs2", "game_player_id": str(steam64)})
        except LookupError:
            return None
        return {"nickname": p.get("nickname"), "player_id": p.get("player_id"), "confidence": "verified",
                "evidence": [f"Steam ID match ({steam64}) from BO3.gg match data"],
                "alternatives": [], "anchors": {}, "show_to_members": True}

    def resolve(self, hltv_nick, team=None, teammates=None, use_saved=True, steam_id=None):
        teammates = [t for t in (teammates or []) if t and t.lower() != hltv_nick.lower()]
        saved = _saved(hltv_nick, team) if use_saved else None
        if saved and saved["confidence"] == "manual":
            use_saved = True
        elif steam_id:
            res = self.by_steam(steam_id)
            if res:
                save_id(hltv_nick, team, res)
                return res
        if use_saved:
            row = saved
            if row and row["faceit_nickname"]:
                return {"nickname": row["faceit_nickname"], "player_id": row["faceit_id"] or None,
                        "confidence": row["confidence"],
                        "evidence": ["set by hand in faceit_ids.csv"] if row["confidence"] == "manual" else
                                    [f"from faceit_ids.csv ({row['checked']})"] + ([row["evidence"]] if row["evidence"] else []),
                        "alternatives": [], "show_to_members": row["confidence"] in ("verified", "manual")}

        cands ={pid: dict(c, sources=["name search"]) for pid, c in self.name_candidates(hltv_nick).items()}

        # co-play: scan each teammate's recent FACEIT matches
        coplay = {}        # pid -> {teammate: matches together}
        anchors = {}
        for mate in teammates:
            apid = self.anchor(mate)
            if not apid:
                continue
            anchors[mate] = apid
            for it in self.history(apid):
                for fac in (it.get("teams") or {}).values():
                    for pl in fac.get("players", []):
                        pid = pl.get("player_id")
                        if pid == apid:
                            continue
                        coplay.setdefault(pid, {}).setdefault(mate, 0)
                        coplay[pid][mate] += 1
                        if pid not in cands and (name_score(hltv_nick, pl.get("nickname"))
                                                 or name_score(hltv_nick, pl.get("game_player_name"))):
                            cands[pid] = {"nickname": pl.get("nickname"), "verified": False,
                                          "sources": [f"in {mate}'s matches"]}
        # accounts of the anchored teammates themselves can't be this player
        for apid in anchors.values():
            cands.pop(apid, None)
        # regular co-player of 2+ teammates with no name match = possibly renamed account
        for pid, mates in coplay.items():
            if pid not in cands and pid not in anchors.values() and len(mates) >= 2 and sum(mates.values()) >= 4:
                cands[pid] = {"nickname": None, "verified": False, "sources": ["plays with 2+ teammates"]}

        team_hits = self.team_members(team, [hltv_nick] + teammates)
        for pid, m in team_hits.items():
            if pid not in anchors.values() and name_score(hltv_nick, m["nickname"]):
                cands.setdefault(pid, {"nickname": m["nickname"], "verified": False, "sources": []})

        scored = []
        for pid, c in cands.items():
            d = self.details(pid)
            cs2 = (d.get("games") or {}).get("cs2") or {}
            if not cs2:
                continue  # no CS2 on this account
            nick = d.get("nickname") or c["nickname"]
            steam = cs2.get("game_player_name")
            n_name = max(name_score(hltv_nick, nick), name_score(hltv_nick, steam))
            mates = coplay.get(pid, {})
            ev, hard = [], 0
            if n_name:
                ev.append(f"name match ({'FACEIT' if name_score(hltv_nick, nick) >= name_score(hltv_nick, steam) else 'Steam'}"
                          f" '{nick if name_score(hltv_nick, nick) >= name_score(hltv_nick, steam) else steam}')")
            if c.get("verified") or d.get("verified"):
                ev.append("FACEIT verified badge")
                hard += 1
            together = sum(mates.values())
            if len(mates) >= 2 or together >= 3:
                ev.append(f"played {together} matches with {', '.join(sorted(mates))}")
                hard += 1
            elif together:
                ev.append(f"{together} match(es) with {', '.join(mates)} (weak)")
            if pid in team_hits:
                ev.append(f"on FACEIT team '{team_hits[pid]['team']}' matching the roster")
                hard += 1
            lvl, elo = cs2.get("skill_level") or 0, cs2.get("faceit_elo") or 0
            # a name alone never counts: level-10 fan accounts with pro names are common
            conf = "verified" if n_name and hard else "likely" if hard else "unverified"
            scored.append({"nickname": nick, "player_id": pid, "steam_name": steam, "level": lvl, "elo": elo,
                           "confidence": conf, "evidence": ev,
                           "_k": (TIERS[conf], hard, n_name, together, elo)})
        scored.sort(key=lambda s: s["_k"], reverse=True)
        for s in scored:
            s.pop("_k")

        if not scored:
            res = {"nickname": None, "player_id": None, "confidence": "not found",
                   "evidence": [f"no CS2 FACEIT account matched '{hltv_nick}'"
                                + ("" if teammates else " - pass teammates to search their matches")],
                   "alternatives": []}
        else:
            res = dict(scored[0], alternatives=scored[1:5])
            if res["confidence"] != "verified" and any(
                    a["confidence"] == res["confidence"] for a in res["alternatives"]):
                res["evidence"].append("AMBIGUOUS - other accounts look equally likely; check alternatives")
        res["anchors"] = {m: self.details(p).get("nickname") for m, p in anchors.items()}
        # the member-facing app only shows FACEIT data it can stand behind
        res["show_to_members"] = res["confidence"] in ("verified", "likely", "manual") and \
            not any("AMBIGUOUS" in e for e in res["evidence"])
        if teammates and not anchors:
            res["evidence"].append("none of the teammates' FACEIT accounts could be found")
        if res.get("nickname"):
            save_id(hltv_nick, team, res)
        return res
