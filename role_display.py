"""Player role tags for display (owner 2026-11-07: "put the roles of every player prop on their cards ... under their name").

DISPLAY ONLY. Not a model input: nothing here is read by the grader, the slip builder or any forecast, and this file is not
part of the model tag (picklog._model_version). It only READS the role state roles.py already keeps in memory:

CS2 - an explicit role listing from the source, never a guess from stats:
  1. Liquipedia roles copied by the HLTV scraper into hltv-db player_roles (roles.refresh_hltv, 30-min bulk load in the
     board loop), used only when the scraper marked the row team_match and the row's team is the board team;
  2. the Liquipedia API cache of the player page (roles.refresh, bounded, board loop), only when the page lists a team
     and that team is the board team;
  3. the Liquipedia team page's active squad with its IGL marked: "IGL" for the marked player, nothing for the others.
  Never used as a role: AWP kill share, BO3.gg body-shot rate, the known-AWPer list, HS%, the grader's "primary /
  secondary" rank, a feed's generic position ("G", "Gamer"), igl.csv. "Star rifler" has no source field: not shown.
  CS2_ROLE_LABELS=0 shows no CS2 role anywhere.

LoL has its own (lol/center/role_display.py); NBA / WNBA / NFL theirs (sports/role_display.py).

No source for a player = None, and the page shows no element at all (no "unknown", no placeholder)."""
import os

import roles

_CS2_WORD = {"igl": "IGL", "awp": "AWPer", "awper": "AWPer", "rifle": "Rifler", "rifler": "Rifler", "entry": "Entry",
             "entry fragger": "Entry", "lurk": "Lurker", "lurker": "Lurker", "support": "Support"}
CS2_MAX = 2


def cs2_enabled():
    return os.environ.get("CS2_ROLE_LABELS", "1").strip() != "0"


def normalize_cs2(roles_list, igl_flag=False):
    """['rifle', 'awp'] -> 'Rifler · AWPer'. The source's own order (Liquipedia lists the main role first), IGL moved to the front
    when listed or flagged, coach / unknown words dropped, duplicates once, at most 2. None if empty."""
    out = []
    for r in roles_list or ():
        w = _CS2_WORD.get(str(r or "").strip().lower())
        if w and w not in out:
            out.append(w)
    if igl_flag or "IGL" in out:
        out = ["IGL"] + [w for w in out if w != "IGL"]
    return " · ".join(out[:CS2_MAX]) or None


def _usable_team(t):
    # an empty or "ex-" team matches every row in roles._team_ok (namesake risk): never used for a display role
    return bool(t) and bool(roles._norm(t)) and not str(t).strip().lower().startswith("ex-")


def _label_for(nick, team):
    h = roles._hltv(nick, team)
    if h and h.get("team_match") and h.get("roles") and h.get("team") and roles._team_ok(team, [h["team"]]):
        lab = normalize_cs2(h["roles"], bool(h.get("igl")))
        if lab:
            return lab
    p = roles._player(nick, team)
    if p and p.get("teams"):                          # a page with no team listed would match any board team
        lab = normalize_cs2(p.get("roles"))
        if lab:
            return lab
    t = (roles._load().get("teams") or {}).get(roles._norm(team))
    if t and t.get("page") and t.get("igl"):
        if roles._norm(nick) in {roles._norm(x) for x in t["igl"]}:
            return "IGL"
    return None


def cs2_label(nick, team, own_teams=()):
    """The CS2 role text for one player on one team, or None. Board team first, then each of the player's own teams (the
    order server.awp_role uses). Memory reads only - never a network or database call."""
    if not cs2_enabled() or not nick:
        return None
    try:
        for t in dict.fromkeys([team, *(own_teams or ())]):
            if not _usable_team(t):
                continue
            lab = _label_for(nick, t)
            if lab:
                return lab
    except Exception:
        return None
    return None
