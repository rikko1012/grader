"""One match identity for EVALUATION (learning ledger units, recap dedup) - 2026-10-12 audit: the pick log stored the same
match under a BO3.gg slug and under hltv-<id> (359 of 1,808 board rows), so it was counted twice. Original records keep
their slug; this key only groups them. Not used by grading."""
import json
import re
from datetime import datetime, timezone

RULE_VERSION = 4      # 2 = slug pairing (2026-10-12); 3 = + settlement's canonical HLTV match (2026-10-13); 4 = hltv/matchid's
                      # org affixes (+ clan, e-sport; 2026-10-21 audit) - learning re-keys on a change

# the same org affixes as hltv._TEAM_AFFIX / matchid._AFFIX (2026-10-21 audit: 'FaZe Clan' and HLTV 'FaZe' were two matches)
_AFFIX = re.compile(r"^(team)\s+|\s+(esports|e-sports|esport|e-sport|gaming|club|clan|team)$", re.I)


def team_norm(name):
    """Lower-case alphanumerics with the 'Team' / 'Esports' / 'Gaming' / 'Club' / 'Clan' affixes removed ('Aurora Gaming' = 'Aurora',
    'Team Spirit' = 'Spirit'). Academies and 'Junior' sides stay different teams."""
    s = str(name or "").strip()
    for _ in range(2):
        s = _AFFIX.sub("", s).strip()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _start(s):
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


LINK_H = 3.0          # an hltv-<id> record joins the BO3.gg match of the same teams whose start is nearest within 3 h


def assign(rows):
    """{row id: canonical match id} for evaluation grouping. Rules (2026-10-12 audit):
    - an 'hltv-<id>' slug and a BO3.gg slug of the same two teams (normalised names, the BO3.gg slug must name both
      teams) whose starts are within LINK_H hours are ONE match -> 'hltv:<id>';
    - two different BO3.gg slugs are never merged (a same-day rematch stays two matches), each hltv id joins at most one
      BO3.gg slug (the nearest start);
    - a row whose BO3.gg slug does not name its own teams, or without teams / start, keeps its slug (as is)."""
    out, groups = {}, {}
    for r in rows:
        slug = str(r.get("match_slug") or "")
        a, b, st = team_norm(r.get("team")), team_norm(r.get("opponent")), _start(r.get("match_start"))
        flat = re.sub(r"[^a-z0-9]", "", slug.lower())
        ok = a and b and a != b and st is not None and (slug.startswith("hltv-") or (a in flat and b in flat))
        out[r["id"]] = f"hltv:{slug[5:]}" if slug.startswith("hltv-") else slug     # unlinked: the slug itself
        if ok:
            g = groups.setdefault(tuple(sorted((a, b))), {})
            g.setdefault(slug, []).append(st)
    for g in groups.values():
        def mid(ts):
            ts = sorted(ts)
            return ts[len(ts) // 2]
        h = {s: mid(t) for s, t in g.items() if s.startswith("hltv-")}
        o = {s: mid(t) for s, t in g.items() if not s.startswith("hltv-")}
        pairs = sorted(((abs((h[x] - o[y]).total_seconds()), x, y) for x in h for y in o
                        if abs((h[x] - o[y]).total_seconds()) <= LINK_H * 3600))
        used_h, used_o, link = set(), set(), {}
        for _d, x, y in pairs:
            if x in used_h or y in used_o:
                continue
            used_h.add(x); used_o.add(y); link[y] = f"hltv:{x[5:]}"
        for r in rows:
            s = str(r.get("match_slug") or "")
            if s in link:
                out[r["id"]] = link[s]
    # 2026-10-13 audit (rule 3): the SAME identity settlement used - a BO3.gg slug whose settled rows name exactly one HLTV
    # match, linked by matchid's canonical match (canonical team ids + verified aliases), is that match
    canon = {}
    for r in rows:
        s = str(r.get("match_slug") or "")
        if s.startswith("hltv-"):
            continue
        try:
            res = r.get("result") if isinstance(r.get("result"), dict) else json.loads(r.get("result") or "{}")
        except (TypeError, ValueError):
            res = {}
        if (res or {}).get("identity") == "canonical match" and res.get("hltv_match"):
            canon.setdefault(s, set()).add(int(res["hltv_match"]))
    for r in rows:
        ids = canon.get(str(r.get("match_slug") or ""))
        if ids and len(ids) == 1:
            out[r["id"]] = f"hltv:{next(iter(ids))}"
    return out
