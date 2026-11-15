"""Match current series to completed series by canonical team identity, before the quote day."""
from collections import defaultdict
from datetime import timedelta

from .. import elo, teams
from ..odds import when


def attach(con, doc):
    day = when(doc["observed_at"]).date()
    rows = con.execute(
        "SELECT team1,team2,team1_name,team2_name,winner,scheduled_utc,sid FROM c_series "
        "WHERE status='complete' AND winner IN (1,2) AND COALESCE(ff,0)=0 AND COALESCE(nullified,0)=0 "
        "AND scheduled_utc>=? AND scheduled_utc<? AND team1 IS NOT NULL AND team2 IS NOT NULL "
        "ORDER BY scheduled_utc,sid", ((day-timedelta(days=180)).isoformat(),day.isoformat()))
    names, recent, meetings = defaultdict(set), defaultdict(list), defaultdict(list)
    games = []
    for a,b,an,bn,win,at,sid in rows:
        start = when(at)
        if a == b or not start or start.date() >= day:
            continue
        for team, label in ((a,an),(b,bn)):
            if label:
                names[teams.key(label)].add(team)
        winner = a if win == 1 else b
        recent[a].append(winner == a)
        recent[b].append(winner == b)
        meetings[frozenset((a,b))].append(winner)
        games.append((start.date().toordinal(), (at,sid), a,b,win==1))
    ratings = elo.Elo(games)
    for g in doc["games"]:
        ids = [names.get(teams.key(n),set()) for n in g["teams"]]
        if any(len(options)!=1 for options in ids) or ids[0]==ids[1]:
            continue
        a,b = [next(iter(options)) for options in ids]
        def form(team):
            results = recent[team][-10:]
            return (sum(results)+2)/(len(results)+4)
        signals = {"Series Elo": 2*ratings.p_win(a,b,day.toordinal())-1, "Recent series form": form(a)-form(b)}
        h2h = meetings[frozenset((a,b))]
        if h2h:
            signals["Head-to-head"] = (h2h.count(a)-h2h.count(b))/(len(h2h)+4)
        g["winner_matchup"] = signals
