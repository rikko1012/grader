"""Authenticated file-only market-tools route."""
import os
from . import api_board


def view(req):
    if os.getenv('SCOUT_MARKET_TOOLS')!='1':return 404,{'error':'not found'}
    lg=req.query.get('league')
    if lg not in ('nba','nfl'):return 400,{'error':'league must be nba or nfl'}
    return api_board._serve(api_board._root()/lg/'market_tools.json')
