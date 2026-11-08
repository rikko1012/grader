"""Player role tags for display (owner 2026-11-07: "put the roles of every player prop on their cards ... under their name";
"Stats also help identify roles"; "And map data").

DISPLAY ONLY. Not a model input: nothing here is read by the grader, the slip builder or any forecast, and this file is not
part of the model tag (picklog._model_version). It only READS state already kept in memory: roles.py's role listings and
the stat-role entry server.py hands in (`stat`, from the stat-role module's card_role(..., facts=True) - this module never
imports that module; its guard test lets only server.py call it).

CS2 - ONE role text per player, shown the same on the board row, the card, the matchroom desk and the hub card:
  Listing (primary) - an explicit Liquipedia role listing for the board team:
    1. Liquipedia roles copied by the HLTV scraper into hltv-db player_roles (roles.refresh_hltv, 30-min bulk load in the
       board loop), used only when the scraper marked the row team_match and the row's team is the board team;
    2. the Liquipedia API cache of the player page (roles.refresh, bounded, board loop), only when the page lists a team
       and that team is the board team;
    3. the Liquipedia team page's active squad with its IGL marked: "IGL" for the marked player, nothing for the others.
  Stat role (the stat-role module: per-map / per-side decayed HLTV stats, one role per lineup) - only where the listing
  names no playing role:Star rifler / Entry / Lurker / Support / AWPer. A listing that says only the generic "Rifler" is refined
  to the stat role when that is a rifle role (Star rifler / Entry / Lurker / Support) - the same answer, more specific.
  AWPer - HLTV's real weapon field (player_awp.awp_share):
    when the stat roles name him his team's AWPer from that share, "AWPer" goes first (stavn 47% -> "AWPer · Rifler", even
    though Liquipedia lists rifle first); when the share says rifler (<= roles.RIFLE_SHARE with >= roles.AWP_MIN_KILLS
    kills) a Liquipedia AWP listing is dropped.
  IGL - first when listed / flagged, one per team: the stat roles' IGL is shown; a listed IGL is not shown when they have
  another player as that team's IGL or the team has several listed and none picked (igl_block - also handed in for a board
  player who is not in the stat roles when his team's IGL slot is taken there), nor when the team (not in the stat roles)
  has two or more team_match IGL listings and the Liquipedia team page does not mark exactly him (_igl_contested).
  Contradiction - the listing names one specific rifle role (Entry / Lurker / Support) and the stat role another, or
  Support against Star rifler: both are dropped (the agreed part is shown, or nothing) - never two different roles on
  one page.
  At most two words. Benched (BO3.gg status 2) = nothing. Never a hedge or "unknown": no role = None and the page shows no
  element at all. CS2_ROLE_LABELS=0 shows no CS2 role anywhere; CS2_STAT_ROLES=0 drops the stat side (listing only, as
  before the stat roles were wired).
  Never used as a role: BO3.gg body-shot rate, the known-AWPer list, HS%, the grader's "primary / secondary" rank, a feed's
  generic position ("G", "Gamer"), igl.csv. roles.py's own AWP chip (x["awp"], slips and the HS stake gate) is unchanged.

LoL has its own (lol/center/role_display.py); NBA / WNBA / NFL theirs (sports/role_display.py)."""
import os

import roles

_CS2_WORD = {"igl": "IGL", "awp": "AWPer", "awper": "AWPer", "rifle": "Rifler", "rifler": "Rifler", "entry": "Entry",
             "entry fragger": "Entry", "lurk": "Lurker", "lurker": "Lurker", "support": "Support"}
CS2_MAX = 2
_SPECIFIC = ("Entry", "Lurker", "Support")                 # rifle roles that exclude each other
_RIFLE_STAT = ("Star rifler", "Entry", "Lurker", "Support")


def _clash(a, b):
    """Two role words that cannot both be true: two different specific rifle roles, or Support (a low share of the team's
    kills) against Star rifler (a top share). Rifler, AWPer and IGL clash with none of them."""
    return a != b and ({a, b} <= set(_SPECIFIC) or {a, b} == {"Support", "Star rifler"})


def cs2_enabled():
    return os.environ.get("CS2_ROLE_LABELS", "1").strip() != "0"


def stat_enabled():
    return os.environ.get("CS2_STAT_ROLES", "1").strip() != "0"


def _words(roles_list):
    out = []
    for r in roles_list or ():
        w = _CS2_WORD.get(str(r or "").strip().lower())
        if w and w not in out:
            out.append(w)
    return out


def normalize_cs2(roles_list, igl_flag=False):
    """['rifle', 'awp'] -> 'Rifler · AWPer'. The source's own order (Liquipedia lists the main role first), IGL moved to the front
    when listed or flagged, coach / unknown words dropped, duplicates once, at most 2. None if empty."""
    out = _words(roles_list)
    if igl_flag or "IGL" in out:
        out = ["IGL"] + [w for w in out if w != "IGL"]
    return " · ".join(out[:CS2_MAX]) or None


def _usable_team(t):
    # an empty or "ex-" team matches every row in roles._team_ok (namesake risk): never used for a display role
    return bool(t) and bool(roles._norm(t)) and not str(t).strip().lower().startswith("ex-")


def _share_of(h):
    """HLTV's AWP share of kills from a roles.py row, only with enough kills (roles.AWP_MIN_KILLS); else None."""
    if h and h.get("awp_share") is not None and (h.get("kills") or 0) >= roles.AWP_MIN_KILLS:
        return float(h["awp_share"])
    return None


def _listing_for(nick, team):
    """(playing-role words in source order, IGL listed) from the first listing source for this team, or None."""
    h = roles._hltv(nick, team)
    if h and h.get("team_match") and h.get("roles") and h.get("team") and roles._team_ok(team, [h["team"]]):
        w = _words(h["roles"])
        if w or h.get("igl"):
            return [x for x in w if x != "IGL"], bool(h.get("igl")) or "IGL" in w
    p = roles._player(nick, team)
    if p and p.get("teams"):                          # a page with no team listed would match any board team
        w = _words(p.get("roles"))
        if w:
            return [x for x in w if x != "IGL"], "IGL" in w
    t = (roles._load().get("teams") or {}).get(roles._norm(team))
    if t and t.get("page") and t.get("igl"):
        if roles._norm(nick) in {roles._norm(x) for x in t["igl"]}:
            return [], True
    return None


_IGLS = {"rows": None, "at": None, "teams": {}, "names": {}, "memo": {}}   # replaced whole on a rebuild (never half-built)


def _listed_igls(team):
    """{normalized nick} of every team_match IGL listing in roles.py's HLTV rows for this board team (exact name or
    hltv.same_team variants, as roles._team_ok). The per-team index is rebuilt once per roles.refresh_hltv load (keyed on
    its load time and the rows object it holds) and the name-variant lookup is memoized per board team: memory only, never a request."""
    rows, at = roles._HLTV.get("rows") or {}, roles._HLTV.get("at")
    idx = _IGLS
    if idx["rows"] is not rows or idx["at"] != at:
        teams, names = {}, {}
        for nk, lst in list(rows.items()):
            for r in lst or ():
                t = r.get("team")
                if not (t and r.get("team_match")):
                    continue
                if r.get("igl") or "igl" in {str(x).strip().lower() for x in r.get("roles") or ()}:
                    k = roles._norm(t)
                    teams.setdefault(k, set()).add(nk)
                    names.setdefault(k, t)
        idx = {"rows": rows, "at": at, "teams": teams, "names": names, "memo": {}}
        globals()["_IGLS"] = idx
    nt = roles._norm(team)
    if not nt:
        return set()
    memo = idx["memo"]
    if nt in memo:
        return memo[nt]
    got = set(idx["teams"].get(nt) or ())
    try:
        import hltv
        for k, raw in idx["names"].items():
            if k != nt and hltv.same_team(team, raw):
                got |= idx["teams"][k]
    except Exception:
        pass
    if len(memo) < 5000:
        memo[nt] = got
    return got


def _igl_contested(nick, team):
    """The board team has two or more listed IGLs (Liquipedia via the HLTV scraper, team_match) and the Liquipedia team page
    does not mark exactly this player among them: the listed IGL is not shown (one IGL per team; the stat roles' own pick,
    st["igl"], is checked by the caller). Never raises."""
    try:
        listed = _listed_igls(team)
        if len(listed) < 2:
            return False
        t = (roles._load().get("teams") or {}).get(roles._norm(team)) or {}
        marks = {roles._norm(x) for x in t.get("igl") or ()} if t.get("page") else set()
        picked = [n for n in listed if n in marks]
        return picked != [roles._norm(nick)]
    except Exception:
        return True


def _share_for(nick, team):
    try:
        h = roles._hltv(nick, team)
        return _share_of(h) if h and (not h.get("team") or roles._team_ok(team, [h["team"]])) else None
    except Exception:
        return None


def combine(listing, stat=None, share=None):
    """The one CS2 role text from the Liquipedia listing ((words, igl) or None), the stat-role entry (card_role facts or
    None) and HLTV's AWP share (enough kills, or None). Pure; see the module docstring for the rule."""
    words, l_igl = (list(listing[0]), bool(listing[1])) if listing else ([], False)
    st = stat or {}
    srole = st.get("stat_role")
    if st.get("awp_share") is not None:
        share = st["awp_share"]
    igl = bool(st.get("igl")) or (l_igl and not st.get("igl_block"))
    if share is not None and share <= roles.RIFLE_SHARE:
        words = [w for w in words if w != "AWPer"]               # the weapon field says rifler: no AWPer, whatever is listed
    if srole == "AWPer" and st.get("source") == "hltv-awp":
        words = ["AWPer"] + [w for w in words if w != "AWPer"]   # the team's AWPer by the real share: first
    elif srole in _RIFLE_STAT and any(_clash(w, srole) for w in words):
        words = [w for w in words if not _clash(w, srole)]        # listing and stats name different roles: the agreed part only
        srole = None
    if not words and srole and not (srole == "AWPer" and share is not None and share <= roles.RIFLE_SHARE):
        words = [srole]                                           # no playing-role listing: the stat role
    elif words == ["Rifler"] and srole in _RIFLE_STAT:
        words = [srole]                                           # generic "Rifler" -> the agreeing, more specific stat role
    out = (["IGL"] if igl else []) + [w for w in words if w != "IGL"]
    return " · ".join(out[:CS2_MAX]) or None


def cs2_label(nick, team, own_teams=(), benched=False, stat=None):
    """The CS2 role text for one player on one team, or None. The listing: board team first, then each of the player's own
    teams (the order server.awp_role uses). stat: the stat-role card_role(nick, team, benched=..., facts=True), passed in by
    server.py (None = no stat entry). Memory reads only - never a network or database call."""
    if not cs2_enabled() or not nick or benched:
        return None
    try:
        if not stat_enabled() or not _usable_team(team):
            stat = None                               # no board team: a nick-only stat match could be a namesake
        listing, used = None, None
        for t in dict.fromkeys([team, *(own_teams or ())]):
            if not _usable_team(t):
                continue
            listing = _listing_for(nick, t)
            if listing:
                used = t
                break
        if not listing and not stat:
            return None
        if listing and listing[1] and not (stat or {}).get("igl") and _igl_contested(nick, used):
            listing = (listing[0], False)             # two or more listed IGLs on this team, none picked: no IGL word
        share = _share_for(nick, used) if used else None
        return combine(listing, stat, share)
    except Exception:
        return None
