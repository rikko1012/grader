"""python -m gradecore: offline evaluation of the shared grading candidate (gp-2). Read-only inputs, artifacts to a folder.

  evaluate     rows (JSONL in the evaluation contract) -> calibration_<sport>.json per sport + evaluation_<stamp>.json
  reliability  one sport's held-out reliability bins (candidate beside the saved published chances) ->
               reliability_<sport>_<stamp>.json + .md, with the <= 3-point rule the release gate enforces
  cs2-local    a picks.db copy, opened read-only -> rows JSONL through gradecore.history.cs2 (+ .selection.json);
               --list prints the model versions in the copy instead

The output folder is --out or SCOUT_GRADECORE_ARTIFACTS. An existing artifact is never overwritten without --replace.
Every artifact carries release_allowed=false: a passing report is evidence for the owner's review, never permission to
publish grades. Nothing here reads or writes a member path, a live database or a flag.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import calibrate, evaluation, history
from .VERSION import GRADE_POLICY_VERSION

MAX_GAP = 0.03                     # release.readiness: every held-out bin within three points
SPORTS = ('cs2', 'lol', 'nba', 'nfl')


def _stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def read_rows(paths):
    rows = []
    for p in paths:
        with Path(p).open(encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def _out_dir(arg):
    folder = arg or os.environ.get('SCOUT_GRADECORE_ARTIFACTS') or ''
    if not folder.strip():
        raise SystemExit('choose --out or set SCOUT_GRADECORE_ARTIFACTS')
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _write(path, doc, replace=False):
    if path.exists() and not replace:
        raise SystemExit(f'{path.name} exists; pass --replace to write a new version of this artifact')
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(doc, indent=1, allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------------------------------------------- evaluate
def run_evaluate(args):
    out = _out_dir(args.out)
    rows = read_rows(args.rows)
    report = evaluation.evaluate(rows, args.fit_end, args.calibration_end, min_test=args.min_test, reps=args.reps)
    present = sorted({r['sport'] for r in rows})
    written = []
    for sport in present:
        doc = dict(report['artifacts'][sport], release_allowed=False, policy=GRADE_POLICY_VERSION)
        written.append(str(_write(calibrate.artifact_path(out, sport), doc, args.replace)))
    report['release_allowed'] = False
    report['policy'] = GRADE_POLICY_VERSION
    report['inputs'] = [str(Path(p)) for p in args.rows]
    report['artifacts_written'] = written
    path = _write(out / f'evaluation_{_stamp()}.json', report, True)
    summary = {'report': str(path), 'artifacts': written, 'counts': report['counts'], 'release_allowed': False,
               'comparison': {s: {k: c.get(k) for k in ('unique_forecasts', 'events', 'log_loss_delta', 'delta_ci',
                                                        'historical_gate_pass', 'baseline_stage')}
                              for s, c in report['comparison'].items() if s in present}}
    print(json.dumps(summary, indent=1, allow_nan=False))
    return 0


# ---------------------------------------------------------------------------------------------------- reliability
def reliability_report(rows, sport, fit_end, calibration_end, reps=2000, max_gap=MAX_GAP, min_test=500):
    """-> the held-out reliability document for one sport (pure; the CLI writes it)."""
    rows = [r for r in rows if r.get('sport') == sport]
    counts = {k: 0 for k in ('fit', 'calibration', 'test', 'purged')}
    comparison = {}
    if rows:
        report = evaluation.evaluate(rows, fit_end, calibration_end, min_test=min_test, reps=reps)
        counts = report['counts']
        comparison = report['comparison'].get(sport, {})
    cand = (comparison.get('candidate') or {})
    old = (comparison.get('old') or {})
    bins = cand.get('reliability') or []
    gaps = [b.get('gap') for b in bins]
    within = bool(bins) and all(isinstance(g, (int, float)) and abs(g) <= max_gap for g in gaps)
    decided = cand.get('n', 0)
    if not rows:
        verdict = 'not evaluable: no settled rows for this sport in the input'
    elif not decided:
        verdict = 'not evaluable: no decided held-out forecasts after the chronological split'
    elif within and decided >= min_test:
        verdict = f'held-out bins all within {max_gap * 100:g} points on {decided} decided forecasts (release still needs review)'
    elif within:
        verdict = f'bins within {max_gap * 100:g} points but only {decided} decided held-out forecasts (< {min_test})'
    else:
        verdict = f'at least one held-out bin is more than {max_gap * 100:g} points off'
    return {'schema': 'grade-reliability-1', 'sport': sport, 'policy': GRADE_POLICY_VERSION, 'generated_at': _stamp(),
            'fit_end': fit_end, 'calibration_end': calibration_end, 'bin_width': 0.05, 'max_gap': max_gap,
            'rows_in': len(rows), 'counts': counts,
            'held_out': {'decided': decided, 'events': comparison.get('events'), 'unique_forecasts': comparison.get('unique_forecasts'),
                         'baseline_stage': comparison.get('baseline_stage'),
                         'log_loss_delta': comparison.get('log_loss_delta'), 'delta_ci': comparison.get('delta_ci')},
            'candidate': {k: cand.get(k) for k in ('n', 'hit_rate', 'hit_ci', 'stated', 'gap', 'brier', 'log_loss')},
            'published': {k: old.get(k) for k in ('n', 'hit_rate', 'hit_ci', 'stated', 'gap', 'brier', 'log_loss')},
            'bins': bins, 'published_bins': old.get('reliability') or [],
            'bins_within_max_gap': within, 'verdict': verdict, 'release_allowed': False,
            'note': 'A passing report is evidence for the owner review, never permission to publish grades.'}


def _md(doc):
    def pct(x):
        return '—' if not isinstance(x, (int, float)) else f'{100 * x:.1f}%'
    lines = [f"# {doc['sport'].upper()} held-out reliability ({doc['policy']}, {doc['generated_at']})", '',
             f"Split: fit < {doc['fit_end']} <= calibration < {doc['calibration_end']} <= test (whole events purged at the boundaries).",
             f"Rows in: {doc['rows_in']}; fit {doc['counts'].get('fit', 0)}, calibration {doc['counts'].get('calibration', 0)}, "
             f"test {doc['counts'].get('test', 0)}, purged {doc['counts'].get('purged', 0)}.",
             f"Held-out decided forecasts: {doc['held_out']['decided']}; events: {doc['held_out']['events']}; "
             f"baseline: {doc['held_out']['baseline_stage']}.", '',
             f"**Verdict: {doc['verdict']}.** release_allowed: false.", '']
    for name, score in (('Candidate', doc['candidate']), ('Published', doc['published'])):
        lines.append(f"{name}: n {score.get('n')}, hit {pct(score.get('hit_rate'))}, stated {pct(score.get('stated'))}, "
                     f"Brier {score.get('brier')}, log loss {score.get('log_loss')}.")
    lines += ['', '| bin | n | stated | hit | gap (pts) | 95% CI (hit) |', '|---|---|---|---|---|---|']
    for b in doc['bins']:
        ci = b.get('ci')
        lines.append(f"| {pct(b['lo'])}-{pct(b['hi'])} | {b['n']} | {pct(b['stated'])} | {pct(b['hit'])} | "
                     f"{100 * b['gap']:+.1f} | {'—' if not ci else pct(ci[0]) + ' to ' + pct(ci[1])} |")
    if not doc['bins']:
        lines.append('| (no held-out bins) | 0 | — | — | — | — |')
    lines += ['', doc['note']]
    return '\n'.join(lines) + '\n'


def run_reliability(args):
    out = _out_dir(args.out)
    doc = reliability_report(read_rows(args.rows), args.sport, args.fit_end, args.calibration_end,
                             reps=args.reps, max_gap=args.max_gap, min_test=args.min_test)
    base = out / f"reliability_{args.sport}_{doc['generated_at']}"
    _write(base.with_suffix('.json'), doc, True)
    base.with_suffix('.md').write_text(_md(doc), encoding='utf-8')
    print(json.dumps({'json': str(base.with_suffix('.json')), 'md': str(base.with_suffix('.md')),
                      'rows_in': doc['rows_in'], 'counts': doc['counts'], 'held_out': doc['held_out']['decided'],
                      'bins': len(doc['bins']), 'bins_within_max_gap': doc['bins_within_max_gap'],
                      'verdict': doc['verdict'], 'release_allowed': False}, indent=1, allow_nan=False))
    return 0


# ------------------------------------------------------------------------------------------------------ cs2-local
def _ro(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f'{p} does not exist')
    con = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    return con


def cs2_local_rows(db_path, model_version):
    """A picks.db copy -> (rows, selection report) through history.cs2. Read-only; a copy without the probs_raw column
    (saved before 2026-10-15) yields no eligible rows and says so."""
    con = _ro(db_path)
    try:
        cols = [r[1] for r in con.execute('PRAGMA table_info(forecasts)')]
        missing = [c for c in ('probs_raw', 'quote') if c not in cols]
        forecasts = [dict(r) for r in con.execute('SELECT * FROM forecasts')]
        picks = [dict(r) for r in con.execute('SELECT * FROM picks')]
        versions = {r[0]: r[1] for r in con.execute('SELECT model_version, COUNT(*) FROM forecasts GROUP BY 1')}
    finally:
        con.close()
    for f in forecasts:
        for c in missing:
            f[c] = None
    rows, excluded = history.cs2(forecasts, picks, model_version)
    report = {'sport': 'cs2', 'source': str(Path(db_path)), 'read_only': True, 'model_version': model_version,
              'forecasts_in_copy': len(forecasts), 'picks_in_copy': len(picks),
              'real_settled_picks': sum(1 for p in picks if p.get('test', 0) in (0, 2, None) and p.get('status') in ('settled', 'push', 'void')),
              'columns_missing': missing, 'model_versions': versions, 'selected': len(rows), 'excluded': excluded,
              'release_allowed': False}
    return rows, report


def run_cs2_local(args):
    if args.list:
        con = _ro(args.db)
        try:
            cols = [r[1] for r in con.execute('PRAGMA table_info(forecasts)')]
            versions = [{'model_version': r[0], 'forecasts': r[1], 'first': r[2], 'last': r[3]} for r in
                        con.execute('SELECT model_version, COUNT(*), MIN(logged_at), MAX(logged_at) FROM forecasts GROUP BY 1 ORDER BY 2 DESC')]
        finally:
            con.close()
        print(json.dumps({'db': str(Path(args.db)), 'has_probs_raw': 'probs_raw' in cols, 'has_quote': 'quote' in cols,
                          'model_versions': versions}, indent=1))
        return 0
    if not args.model_version:
        raise SystemExit('--model-version is required (or --list)')
    rows, report = cs2_local_rows(args.db, args.model_version)
    target = Path(args.rows_out) if args.rows_out else _out_dir(args.out) / f"cs2_rows_{args.model_version}_{_stamp()}.jsonl"
    if target.exists():
        raise SystemExit('choose a new rows file; historical evidence is never overwritten')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as f:
        for row in sorted(rows, key=lambda r: (r['forecast_at'], r['forecast_id'])):
            f.write(json.dumps(row, allow_nan=False) + '\n')
    report['output'] = str(target)
    Path(str(target) + '.selection.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps(report, indent=1))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m gradecore', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('evaluate', help='chronological fit / calibration / held-out comparison; writes artifacts')
    e.add_argument('--rows', action='append', required=True, help='rows JSONL (repeatable)')
    e.add_argument('--fit-end', required=True)
    e.add_argument('--calibration-end', required=True)
    e.add_argument('--min-test', type=int, default=500)
    e.add_argument('--reps', type=int, default=2000)
    e.add_argument('--out', default=None, help='artifact folder (default SCOUT_GRADECORE_ARTIFACTS)')
    e.add_argument('--replace', action='store_true', help='allow replacing an existing calibration_<sport>.json')
    e.set_defaults(fn=run_evaluate)
    r = sub.add_parser('reliability', help="one sport's held-out reliability bins (<= 3-point rule)")
    r.add_argument('--rows', action='append', required=True)
    r.add_argument('--sport', choices=SPORTS, required=True)
    r.add_argument('--fit-end', required=True)
    r.add_argument('--calibration-end', required=True)
    r.add_argument('--reps', type=int, default=2000)
    r.add_argument('--min-test', type=int, default=500)
    r.add_argument('--max-gap', type=float, default=MAX_GAP)
    r.add_argument('--out', default=None)
    r.set_defaults(fn=run_reliability)
    c = sub.add_parser('cs2-local', help='rows from a picks.db copy (read-only) through history.cs2')
    c.add_argument('--db', required=True)
    c.add_argument('--model-version', default=None)
    c.add_argument('--list', action='store_true', help='print the model versions in the copy and exit')
    c.add_argument('--rows-out', default=None, help='rows JSONL path (default: <out>/cs2_rows_<version>_<stamp>.jsonl)')
    c.add_argument('--out', default=None)
    c.set_defaults(fn=run_cs2_local)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
