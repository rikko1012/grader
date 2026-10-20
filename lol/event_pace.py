"""Event pace (ep-1, lol/EVALUATION.md A8): each tournament's kill / assist environment, learned from its own earlier days.

For a target series on day d in tournament T and a stat s: every game of T on days before d gives
  actual   = both teams' total s in that game,
  expected = the mean of the two teams' average per-game total s (own + opponent's) over their games outside T in the
             120 days before d (a team with no such games is left out),
and c = (sum actual + K * m) / (sum expected + K * m), m = mean expected per game (c = 1 with no earlier game of T).
The projection is multiplied by c ** b. Pure functions over game rows; the same code serves the study and live forecasts.
"""
from collections import defaultdict

WINDOW = 120


class Pace:
    def __init__(self, games):
        """games: iterable of dicts {game_key, day (ordinal), tournament, teams: {team_key: {"kills": k, "assists": a}}}
        - one entry per game with both teams present."""
        self.by_t = defaultdict(list)          # tournament -> [(day, game)]
        self.team = defaultdict(list)          # team -> [(day, tournament, {stat: both teams' total})]
        for g in games:
            if len(g["teams"]) != 2:
                continue
            tot = {s: sum(v[s] for v in g["teams"].values()) for s in ("kills", "assists")}
            g = dict(g, tot=tot)
            self.by_t[g["tournament"]].append((g["day"], g))
            for tk in g["teams"]:
                self.team[tk].append((g["day"], g["tournament"], tot))
        self._cache = {}

    def _team_avg(self, tk, day, t, stat):
        xs = [tot[stat] for d, tt, tot in self.team.get(tk, ()) if day - WINDOW <= d < day and tt != t]
        return sum(xs) / len(xs) if xs else None

    def sums(self, t, day, stat):
        """(sum actual, sum expected, games) over T's games before `day` with an expected value."""
        key = (t, day, stat)
        if key not in self._cache:
            a = e = 0.0
            n = 0
            for d, g in self.by_t.get(t, ()):
                if d >= day:
                    continue
                ex = [self._team_avg(tk, d, t, stat) for tk in g["teams"]]
                ex = [x for x in ex if x is not None]
                if not ex:
                    continue
                a += g["tot"][stat]
                e += sum(ex) / len(ex)
                n += 1
            self._cache[key] = (a, e, n)
        return self._cache[key]

    def factor(self, t, day, stat, K, b):
        a, e, n = self.sums(t, day, stat)
        if not n or e <= 0:
            return 1.0, n
        m = e / n
        return ((a + K * m) / (e + K * m)) ** b, n


def games_from_rows(rows, games_meta, match_day):
    """Pace input from player-game rows (history.all_player_games shape)."""
    from . import teams as T
    acc = {}
    for r in rows:
        gk = r.get("game_key")
        if not gk or r.get("kills") is None or r.get("assists") is None:
            continue
        g = acc.setdefault(gk, {"game_key": gk, "day": match_day.get(r["match_key"]),
                                "tournament": (games_meta.get(gk) or {}).get("tournament"), "teams": {}})
        tk = r.get("team_id") or T.key(r["team"])
        v = g["teams"].setdefault(tk, {"kills": 0, "assists": 0})
        v["kills"] += r["kills"]
        v["assists"] += r["assists"]
    return [g for g in acc.values() if g["day"] is not None and g["tournament"]]
