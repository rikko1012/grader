"""Run a reproducible chronological experiment from an explicit JSONL export.

Does not use a live model or modify live grading. See gradecore/evaluation.py for
the required source fields. Missing event/forecast times must not be inferred.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gradecore.evaluation import evaluate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True)
    p.add_argument('--fit-end', required=True)
    p.add_argument('--calibration-end', required=True)
    p.add_argument('--out', required=True)
    args = p.parse_args()
    source, target = Path(args.input), Path(args.out)
    if source.resolve() == target.resolve() or target.exists():
        p.error('choose a new output path; source and previous reports are preserved')
    with source.open(encoding='utf-8') as f:
        rows = [json.loads(line) for line in f if line.strip()]
    result = evaluate(rows, args.fit_end, args.calibration_end)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2, allow_nan=False)
    print(json.dumps({'report': str(target), 'counts': result['counts'], 'release_allowed': False,
                      'sports': {s: {'historical_gate_pass': x['historical_gate_pass'],
                                     'unique_forecasts': x['unique_forecasts'], 'delta_ci': x['delta_ci']}
                                 for s, x in result['comparison'].items()}}))


if __name__ == '__main__':
    main()
