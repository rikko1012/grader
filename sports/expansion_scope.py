"""Soccer settlement scope per fixture (sg-scope, 2026-11-20).

A full-game soccer line equals the regulation total whenever its fixture cannot reach extra time. That is decided per
fixture from ESPN's own stage facts and the stored finals of the same stage, never from a league name:

- a first leg of a two-legged tie never has extra time;
- a stage (ESPN season.type id: one stage of one season) whose stored single-leg finals include drawn full-time
  results and no extra-time / penalty finish does not use extra time (a drawn single-leg knockout would have gone on);
- the same competition stage name in earlier seasons (year tokens stripped) counts the same way;
- anything else - a knockout stage, a stage with an extra-time finish, or a stage never seen - CAN reach extra time.

When a fixture can reach extra time, the platform's settlement rule decides what is graded:
- PrizePicks: extra-time stats count (except Goals Allowed), shootouts do not  -> the full-game box total (extra time
  included) is what settles; the model for it is the regulation predictive (walk-forward below: on 161 fixtures that
  could reach extra time, box totals INCLUDING extra time came in 7.4% under the regulation projection, 95% CI
  -15.6% .. -0.6%, and a regulation + 30-minute mixture lost on log loss, +0.0063, CI +0.0050 .. +0.0079);
- Underdog: regulation incl. stoppage time; extra time and shootouts do not count -> the regulation total itself.

Nothing here requests a provider or opens a database: inputs are the history job's catalogs and its stages.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

VERSION = 'sg-scope-1'
# Settlement rules, with where they were read. Same pattern as expansion_formats.SOURCES.
PLATFORM_RULES = {
    'prizepicks': {'extra_time_counts': True, 'shootout_counts': False,
                   'source': 'https://x.com/PrizePicks/status/2071589350358278620 (PrizePicks, 2026-06-29: extra-time '
                             'stats count except Goals Allowed; shootouts do not)', 'read_at': '2026-11-20T06:40:00Z'},
    'underdog': {'extra_time_counts': False, 'shootout_counts': False,
                 'source': 'https://help.underdogsports.com/en/articles/11006231-pick-em-scoring-soccer-fifa and '
                           '/14127572-prediction-picks-soccer (regulation plus stoppage time; extra time and penalties '
                           'excluded)', 'read_at': '2026-11-20T07:05:00Z'},
}
ET_STATUSES = frozenset({'STATUS_FINAL_AET', 'STATUS_FINAL_PEN', 'STATUS_END_OF_EXTRATIME', 'STATUS_SHOOTOUT',
                         'STATUS_FIRST_HALF_EXTRA_TIME', 'STATUS_SECOND_HALF_EXTRA_TIME', 'STATUS_HALFTIME_ET'})
MIN_DRAWS = 2          # drawn single-leg full-time finals that prove a stage has no extra time
# Prior for P(extra time) of a fixture that can reach it: Beta(2, 6) (mean 0.25, the single-leg knockout draw rate
# order of magnitude), updated with the stored finals of extra-time-capable stages (walk-forward: only finals known
# before the forecast). Recorded on the forecast; it moves the chance only through ET_SHARE.
P_ET_PRIOR = (2.0, 6.0)
# Extra-time share of the regulation rate added with P(extra time). Pre-registered walk-forward 2026-11-20
# (cs2_scout_ops/logs/soccer_grades_1120/scope/walkforward_report.json, part C): 30/90 FAILED (worse log loss, CI
# above 0) and the observed share was below 0 -> 0.0: the regulation predictive is the tested model for these lines.
# A positive value re-enables the Poisson-Gamma mixture in expansion_forecasts.fit (needs a new walk-forward first).
ET_SHARE = 0.0
STAGES_BYTES = 1_500_000
STAGE_EVENTS = 600     # events kept per stage (newest first by id order of insertion)


def norm_slug(slug):
    """'torneo-apertura-2026' / '2025-26-...-apertura---finals' -> stage name without year tokens."""
    s = re.sub(r'\b(19|20)\d{2}(-\d{2,4})?\b', ' ', str(slug or '').lower())
    return '-'.join(x for x in re.split(r'[-\s]+', s) if x)


def outcome_code(match):
    """One stored match -> 'E' extra time / penalties, 'D' drawn single-leg full-time final, 'L' drawn leg final,
    'R' decided in regulation, None (not finished / unknown)."""
    status = str(match.get('source_status') or '')
    clock = re.match(r"\s*(\d+)", str(match.get('clock') or ''))
    if status == 'STATUS_FINAL_PEN' and clock and int(clock[1]) <= 100 and match.get('period') in (None, 2):
        return 'D' if not match.get('leg') else 'L'   # straight to penalties after 90 minutes: no extra time played
    if status in ET_STATUSES or (isinstance(match.get('period'), int) and match['period'] > 2):
        return 'E'
    if match.get('status') != 'final' or status not in ('STATUS_FULL_TIME', 'STATUS_FINAL'):
        return None
    scores = list((match.get('scores') or {}).values())
    if len(scores) != 2:
        return None
    if scores[0] == scores[1]:
        return 'L' if match.get('leg') else 'D'
    return 'R'


def update_stages(directory, catalogs):
    """Merge every catalog match that carries stage facts into <sport dir>/stages.json (durable across the 100-day
    catalog window, so last season's knockout stages stay known). -> number of stages."""
    path = Path(directory) / 'stages.json'
    try:
        doc = json.loads(path.read_text(encoding='utf-8')) if path.stat().st_size <= 2 * STAGES_BYTES else {}
    except (OSError, ValueError):
        doc = {}
    stages = doc.get('stages') if isinstance(doc.get('stages'), dict) else {}
    for competition, matches in catalogs.items():
        merge(stages, competition, matches.values())
    out = {'version': VERSION, 'stages': stages}
    if len(json.dumps(out, separators=(',', ':'))) > STAGES_BYTES:
        # Bound the file, least information first: plain regulation results of stages without an extra-time finish
        # (neither rule reads them), then whole stages in insertion order (oldest first).
        for entry in stages.values():
            if 'E' not in entry['ev'].values():
                entry['ev'] = {k: v for k, v in entry['ev'].items() if v != 'R'}
        while stages and len(json.dumps(out, separators=(',', ':'))) > STAGES_BYTES:
            del stages[next(iter(stages))]
    from .expansion_history import atomic_json
    atomic_json(path, out)
    return len(stages)


def merge(stages, competition, matches):
    """Add each finished match with stage facts to the in-memory stages dict (shared by the job and the replays)."""
    for match in matches:
        stype = match.get('stage_type')
        code = outcome_code(match)
        if not stype or not code:
            continue
        entry = stages.setdefault(f'{competition}|{stype}', {'slug': norm_slug(match.get('stage_slug')), 'ev': {}})
        if match.get('stage_slug'):
            entry['slug'] = norm_slug(match['stage_slug'])
        eid = str(match.get('event_id') or '').split(':')[-2:-1]
        if eid:
            entry['ev'][eid[0]] = code
            if len(entry['ev']) > STAGE_EVENTS:
                for k in list(entry['ev'])[:len(entry['ev']) - STAGE_EVENTS]:
                    del entry['ev'][k]
    return stages


def load_stages(directory):
    try:
        path = Path(directory) / 'stages.json'
        if path.stat().st_size > 2 * STAGES_BYTES:
            return {}
        doc = json.loads(path.read_text(encoding='utf-8'))
        return doc.get('stages') if isinstance(doc.get('stages'), dict) else {}
    except (OSError, ValueError):
        return {}


def _counts(entries):
    total = {'E': 0, 'D': 0, 'L': 0, 'R': 0}
    for entry in entries:
        for code in entry['ev'].values():
            if code in total:
                total[code] += 1
    return total


def p_extra_time(competition, slug, stages):
    """P(extra time | the fixture can reach it): Beta prior updated with finals of this competition's same-named
    stage when it has an extra-time finish, else of every extra-time-capable stage stored. Leg-1 draws are excluded
    (a first leg never goes on)."""
    capable = [e for e in stages.values() if _counts([e])['E'] > 0]
    same = [e for k, e in stages.items() if k.split('|')[0] == competition and e.get('slug') == slug and e in capable]
    pool = same or capable
    c = _counts(pool)
    finals = c['E'] + c['D'] + c['R']      # decided or drawn single-leg / 2nd-leg finals; 'L' draws may be leg 1
    a, b = P_ET_PRIOR
    return (c['E'] + a) / (finals + a + b), ('same_stage' if same else 'all_extra_time_stages' if capable else 'prior'), finals


def rule(competition, match, stages):
    """-> {'extra_time': 'none' | 'possible', 'basis': ..., ...} for one scheduled fixture (match from the catalog)."""
    match = match or {}
    stype, slug = match.get('stage_type'), norm_slug(match.get('stage_slug'))
    out = {'version': VERSION, 'stage_type': stype, 'stage': slug or None}
    if match.get('leg') == 1:
        return {**out, 'extra_time': 'none', 'basis': 'first_leg'}
    if stype:
        own = stages.get(f'{competition}|{stype}')
        if own:
            c = _counts([own])
            if c['E'] == 0 and c['D'] >= MIN_DRAWS and not match.get('leg'):
                return {**out, 'extra_time': 'none', 'basis': 'stage_draws', 'draws': c['D'], 'finals': sum(c.values())}
        if slug:
            earlier = [e for k, e in stages.items() if k.split('|')[0] == competition and k != f'{competition}|{stype}'
                       and e.get('slug') == slug]
            c = _counts(earlier)
            if earlier and c['E'] == 0 and c['D'] >= MIN_DRAWS and not match.get('leg') and not (own and _counts([own])['E']):
                return {**out, 'extra_time': 'none', 'basis': 'same_stage_earlier_seasons', 'draws': c['D'],
                        'finals': sum(c.values())}
    p, basis, n = p_extra_time(competition, slug, stages)
    return {**out, 'extra_time': 'possible', 'basis': 'knockout_or_unseen_stage', 'p_extra_time': round(p, 4),
            'p_basis': basis, 'p_finals': n}


def settlement(platform, extra_time):
    """-> 'regulation' (the book line equals the regulation total) or 'regulation_plus_extra_time', or None for a
    platform without a registered rule."""
    if extra_time == 'none':
        return 'regulation'
    rule_ = PLATFORM_RULES.get(platform)
    if not rule_:
        return None
    return 'regulation_plus_extra_time' if rule_['extra_time_counts'] else 'regulation'
