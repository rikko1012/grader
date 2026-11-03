"""Prepare a fixed model cohort from paged, read-only esports exports."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gradecore import history


def read(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sport', choices=['cs2','lol'],required=True)
    p.add_argument('--forecasts',required=True)
    p.add_argument('--results',required=True)
    p.add_argument('--model-version',required=True)
    p.add_argument('--artifact-version')
    p.add_argument('--out',required=True)
    a=p.parse_args()
    target=Path(a.out)
    if target.exists(): p.error('choose a new output file; historical evidence is never overwritten')
    if a.sport=='lol' and not a.artifact_version: p.error('LoL requires an explicit frozen artifact version')
    args=[read(a.forecasts),read(a.results),a.model_version]
    if a.sport=='lol': args.append(a.artifact_version)
    rows,excluded=getattr(history,a.sport)(*args)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as f:
        for row in sorted(rows,key=lambda r:(r['forecast_at'],r['forecast_id'])):
            f.write(json.dumps(row,allow_nan=False)+'\n')
    report={'sport':a.sport,'model_version':a.model_version,'artifact_version':a.artifact_version,
            'selected':len(rows),'excluded':excluded,'output':str(target)}
    with target.with_suffix(target.suffix+'.selection.json').open('x',encoding='utf-8') as f:
        json.dump(report,f,indent=2)
    print(json.dumps(report))


if __name__=='__main__': main()
