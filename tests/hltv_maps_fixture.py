"""HLTV map rows for the v3 map-card tests (hltv_maps rows: name, date, matchid, k, d, hs, own, opp, rating, adr, kast), dated
relative to today so the 12-month and 3-month windows always hold them. Mirage, Inferno, Anubis, Dust 2, Ancient are in the pool;
Overpass and Train are outside it; Nuke and Cache have no maps (their cards show "—")."""
from datetime import date, timedelta
from fractions import Fraction as F

HLTV_ID = 99001
IN_POOL = ("Mirage", "Inferno", "Anubis", "Dust 2", "Ancient")
OUTSIDE = ("Overpass", "Train")


def rows(today=None):
    today = today or date.today()
    out = []
    names = IN_POOL * 7 + OUTSIDE * 2                   # 35 pool maps + 4 outside
    for i, m in enumerate(names):
        k = 11 + (i * 7) % 15
        d = 9 + (i * 5) % 10
        hs = None if i == 3 else (0 if i == 8 else (k * (35 + (i % 4) * 5)) // 100)   # one map with no headshot row; i=8: 0 with 12+ kills
        own, opp = (13, 4 + i % 9) if i % 3 else (8 + i % 5, 13)
        out.append((m, (today - timedelta(days=2 + i * 9)).isoformat(), 5000 + i // 2, k, d, hs, own, opp,
                    round(0.78 + (i % 9) * 0.05, 2), round(61.0 + i % 27, 1), round(64.0 + i % 14, 1)))
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def loader(nick, team, since):
    return HLTV_ID, [r for r in rows() if r[1] >= since]


def key(name):
    return "".join(c for c in name.lower() if c.isalnum())


def totals_of(rs):
    """Exact totals (Fractions for the weighted sums) - the reference the tests compare the page and the module with."""
    t = {"maps": 0, "rounds": 0, "kills": 0, "deaths": 0, "hs": 0, "hs_kills": 0, "hs_maps": 0, "rx": F(0), "rr": 0, "wins": 0, "decided": 0}
    for _, _, _, k, d, hs, own, opp, rating, adr, kast in rs:
        t["maps"] += 1
        t["rounds"] += own + opp
        t["kills"] += k
        t["deaths"] += d
        if hs is not None and not (hs == 0 and k >= 12):
            t["hs"] += hs
            t["hs_kills"] += k
            t["hs_maps"] += 1
        t["rx"] += F(str(rating)) * (own + opp)
        t["rr"] += own + opp
        t["decided"] += own != opp
        t["wins"] += own > opp
    return t


def per_map(window_days=365):
    since = (date.today() - timedelta(days=window_days)).isoformat()
    by = {}
    for r in rows():
        if r[1] >= since:
            by.setdefault(key(r[0]), []).append(r)
    return {k: totals_of(v) for k, v in by.items()}
