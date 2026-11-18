"""Offline alias PROPOSER for the research sources. It never applies anything: it writes candidates for a person to
check, and only checked entries go into a source's ALIASES table.

  python -m sports.expansion_aliases football-data OUT.json   (reads SCOUT_EXPANSION_DATA_DIR / soccer)

football-data: for every stored fixture that did not join an ESPN final, an ESPN final in the same competition within
2 h with the same score where exactly ONE side matched exactly proposes the other side's name -> that ESPN team.
A candidate seen in several fixtures (support) with no conflicting ESPN team is the strongest; nothing is fuzzy.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path


def football_data(directory):
    from . import expansion_footballdata as fd
    from .expansion import isolated_database
    from .expansion_history import normalized
    from .contracts import parse_utc
    catalogs = {}
    out = {}
    with closing(fd.connect(directory)) as con:
        rows = con.execute('SELECT slug, start, home, away, fthg, ftag FROM fixtures WHERE event_id IS NULL').fetchall()
    for slug, start, home, away, hg, ag in rows:
        if slug not in catalogs:
            try:
                catalogs[slug] = list(json.loads((isolated_database(directory.parent, slug).parent / 'matches.json')
                                                 .read_text(encoding='utf-8')).values())
            except (OSError, ValueError, KeyError):
                catalogs[slug] = []
        for m in catalogs[slug]:
            if (m.get('status') != 'final' or not m.get('start') or not m.get('scores')
                    or abs((parse_utc(m['start']) - parse_utc(start)).total_seconds()) > 7200):
                continue
            parts = m.get('participants') or []
            names = [{normalized(p.get(k)) for k in ('name', 'short_name', 'abbreviation')} - {''} for p in parts]
            for mine, other, goals, other_goals in ((home, away, hg, ag), (away, home, ag, hg)):
                hit = [i for i, n in enumerate(names) if normalized(mine) in n]
                if len(hit) != 1 or len(parts) != 2:
                    continue
                known, unknown = parts[hit[0]], parts[1 - hit[0]]
                if m['scores'].get(known['id']) != goals or m['scores'].get(unknown['id']) != other_goals:
                    continue
                if normalized(other) in names[1 - hit[0]]:
                    continue
                key = f'{slug}|{other}'
                c = out.setdefault(key, {'competition': slug, 'source_name': other, 'espn': {}})
                c['espn'].setdefault(unknown['id'], {'name': unknown.get('name'), 'support': 0})['support'] += 1
    for c in out.values():
        c['conflict'] = len(c['espn']) > 1
    return sorted(out.values(), key=lambda c: (c['conflict'], -max(v['support'] for v in c['espn'].values())))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('source', choices=['football-data'])
    p.add_argument('out')
    a = p.parse_args()
    from .expansion_board import root
    found = football_data(root('soccer'))
    Path(a.out).write_text(json.dumps(found, indent=1), encoding='utf-8')
    print(json.dumps({'candidates': len(found), 'no_conflict': sum(not c['conflict'] for c in found), 'out': a.out}))


if __name__ == '__main__':
    main()
