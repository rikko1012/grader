"""Team Elo from the stored game results - a pre-match win chance for every match, computed only from games on earlier
days, so the backtest and the live scout get the same number. Used by model.py's match-strength factor (when the TRAIN
fit keeps it). Regions meet only at international events, so cross-region ratings are rough; the Polymarket price is
shown next to the projection for that reason, but it isn't used in the projection (there's no history of prices to
test it on)."""
from collections import defaultdict

from . import teams

K = 24.0
BASE = 1500.0


class Elo:
    def __init__(self, games):
        """games: [(day_ordinal, game_order, team_a, team_b, a_won)] - one entry per game (a_won None = unknown result,
        skipped). Team names are folded to one identity per team (teams.key: alias group, else the core name)."""
        self.timeline = defaultdict(list)          # team -> [(day, rating after that day)]
        rating = {}
        by_day = defaultdict(list)
        for g in games:
            if g[4] is not None:
                by_day[g[0]].append(g)
        for day in sorted(by_day):
            for _, _, a, b, a_won in sorted(by_day[day], key=lambda g: g[1]):
                ka, kb = teams.key(a), teams.key(b)
                ra, rb = rating.get(ka, BASE), rating.get(kb, BASE)
                ea = 1 / (1 + 10 ** ((rb - ra) / 400))
                rating[ka], rating[kb] = ra + K * (a_won - ea), rb - K * (a_won - ea)
            for t in {teams.key(g[2]) for g in by_day[day]} | {teams.key(g[3]) for g in by_day[day]}:
                self.timeline[t].append((day, rating[t]))

    def rating(self, team, day):
        """The team's rating before `day` (BASE when it has no earlier game)."""
        best = BASE
        for d, r in self.timeline.get(teams.key(team), ()):
            if d >= day:
                break
            best = r
        return best

    def p_win(self, team, opp, day):
        return 1 / (1 + 10 ** ((self.rating(opp, day) - self.rating(team, day)) / 400))


def from_rows(rows, day_of):
    """Elo from history rows (lol_player_games / team_games dicts): one game per game_key, side as stored.
    day_of(row) -> the day ordinal the row counts on (the match day)."""
    seen, games = set(), []
    for r in rows:
        if r.get("game_key") in seen or r.get("win") is None:
            continue
        seen.add(r.get("game_key"))
        games.append((day_of(r), r.get("game_number") or 0, r["team"], r["opponent"], int(r["win"])))
    return Elo(games)
