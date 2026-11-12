"""Public PointsBet Australia pregame player prices; no account or paid feed.

Strict observed full-game O/U labels only. Specials, hidden selections, live
games, combos across players and unverified period markets are not imported.
"""
import os
import re
from datetime import datetime, timezone
from .. import net
from ..lines import utc_iso
from .base import Provider, Event, FetchResult, make_offer

BASE = 'https://api.au.pointsbet.com/api/mes/v3'
COMP = {'nba': '7176', 'nfl': '11444'}
STATS = {
 'nba': {'Player Points Over/Under':'points', 'Player Assists Over/Under':'assists',
         'Player Rebounds Over/Under':'rebounds', 'Player 3-Pointers Made':'threes',
         'Player Pts + Rebs + Asts Over/Under':'pra', 'Player Points + Assists Over/Under':'pa',
         'Player Points + Rebounds Over/Under':'pr', 'Player Assists + Rebounds Over/Under':'ra'},
 'nfl': {'Quarterback Passing Yards':'pass_yds', 'Quarterback Passing Touchdowns':'pass_td',
         'Rushing Yards':'rush_yds', 'Receiving Yards':'rec_yds', 'Player Receptions':'rec',
         'Rushing + Receiving Yards':'rush_rec_yds'},
}
SIDE = re.compile(r'(.+?) (Over|Under) (\d+(?:\.\d+)?)$')


def event(row, league, now=None):
    now = now or datetime.now(timezone.utc)
    if str(row.get('competitionKey')) != COMP[league] or row.get('competitionName') != league.upper(): return None
    if row.get('isLive') is not False or not row.get('homeTeam') or not row.get('awayTeam') or not row.get('key'): return None
    if row['homeTeam'] == row['awayTeam']: return None
    start = utc_iso(row.get('startsAt'))
    if not start or datetime.fromisoformat(start.replace('Z','+00:00')) <= now: return None
    return Event(str(row['key']), league, row.get('name') or '', row['homeTeam'], row['awayTeam'], start)


def parse_props(body, lg, ev, now=None):
    current = event(body, lg['key'], now)
    if not current or current != ev: return []
    offers = []
    for market in body.get('fixedOddsMarkets') or []:
        label = market.get('eventName')
        stat = STATS[lg['key']].get(label)
        if not stat or not market.get('key'): continue
        for o in market.get('outcomes') or []:
            if o.get('isOpenForBetting') is not True or o.get('isHidden') is not False or not o.get('playerId'): continue
            match = SIDE.fullmatch(o.get('name') or '')
            if not match or float(match[3]) != o.get('points'): continue
            offers.append(make_offer(provider='pointsbet_au', book='pointsbet_au', jurisdiction='AU',
                league=lg['key'], provider_event_id=ev.provider_event_id, event_name=ev.name,
                home=ev.home, away=ev.away, start=ev.start, player=match[1], provider_player_id=str(o['playerId']),
                market=stat, stat_label=label, period='game', line=o['points'], side=match[2].lower(),
                price_decimal=o.get('price'), market_type='standard', status='open',
                provider_market_id=str(market['key']), provider_selection_id=o.get('optionId') or str(o.get('fixedMarketId')),
                provider_updated_at=utc_iso(o.get('priceLastUpdated'))))
    return offers


class PointsBetAU(Provider):
    name, books = 'pointsbet_au', ('pointsbet_au',)

    def configured(self):
        # Normal collector was refused on 2026-11-12 despite earlier probes.
        # Keep disabled until this same feed accepts the normal collector.
        return os.getenv('SPORTS_POINTSBET_AU','0') == '1', 'SPORTS_POINTSBET_AU is off (collector refused)'

    def _read(self, path, parser):
        res = FetchResult()
        try:
            body = net.get_json(BASE + path, tries=1, timeout=12)
            res.requests, res.http_status = 1, 200
            parser(body, res)
        except net.HttpError as e:
            res.requests, res.http_status, res.error, res.blocked = int(not e.held), e.status, str(e), e.status in (401,403)
            res.not_before = e.not_before_iso
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            res.requests, res.offers, res.events, res.error = 1, [], [], 'answer not readable: ' + type(e).__name__
        return res

    def events(self, lg):
        def read(body, res):
            # Never quietly accept a truncated league listing.
            if body.get('nextPage'): raise ValueError('paginated listing requires review')
            if str(body.get('key')) != COMP[lg['key']]: raise ValueError('wrong competition')
            res.events = [e for row in body.get('events') or [] if (e := event(row, lg['key']))]
        return self._read('/events/featured/competition/' + COMP[lg['key']], read)

    def props(self, lg, ev):
        def read(body, res): res.offers = parse_props(body, lg, ev)
        return self._read('/events/' + ev.provider_event_id, read)
