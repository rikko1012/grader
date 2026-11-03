"""Bounded, read-only inspection of append-only shared-grader comparisons."""
import json
import sqlite3
from pathlib import Path


def comparisons(path, sport, before=None, limit=100):
    if sport not in ('cs2', 'lol', 'nba', 'nfl'):
        raise ValueError('unknown sport')
    limit = max(1, min(int(limit), 200))
    path = Path(path)
    empty = {'sport': sport, 'rows': [], 'state': 'awaiting_capture', 'next_before': None}
    if not path.exists():
        return empty
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as db:
        db.execute('PRAGMA query_only=ON')
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gradecore_shadow'").fetchone():
            return empty
        args = [sport]
        where = 'sport=?'
        if before is not None:
            where += ' AND rowid<?'
            args.append(int(before))
        rows = db.execute('SELECT rowid,payload,captured_at FROM gradecore_shadow WHERE ' + where +
                          ' ORDER BY rowid DESC LIMIT ?', (*args, limit)).fetchall()
        first = db.execute('SELECT captured_at FROM gradecore_shadow WHERE sport=? ORDER BY rowid LIMIT 1', (sport,)).fetchone()
    out = []
    for rid, raw, captured in rows:
        item = json.loads(raw)
        offer = item['offer']
        sides = item['candidate']['sides']
        out.append({'cursor': rid, 'forecast_id': item['forecast_id'], 'at': item['forecast_at'],
                    'captured_at': captured, 'event': offer['event_id'], 'player': offer['player'],
                    'stat': offer['stat'], 'period': offer['scope'], 'line': offer['line'], 'book': offer['book'],
                    'old_letter': item.get('old_letter'), 'old_p': item.get('old_p'),
                    'sides': sides, 'policy': item['candidate']['policy'],
                    'artifact_hash': item['artifact_hash']})
    return {'sport': sport, 'rows': out, 'state': 'capturing' if out else 'awaiting_capture',
            'started_at': first[0] if first else None,
            'next_before': rows[-1][0] if len(rows) == limit else None,
            'release_allowed': False, 'note': 'Candidate comparisons do not change published grades.'}
