"""Feature-flagged append-only shadow capture in the caller's transaction.

No member path reads this table. An error rolls back only the shadow savepoint,
never the original forecast. No fitting, provider calls or threads are started.
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from . import adapters, engine

LOG=logging.getLogger(__name__)


@lru_cache(maxsize=16)
def _artifact(path, mtime):
    if Path(path).stat().st_size > 1024*1024: raise ValueError('calibration artifact exceeds budget')
    return json.loads(Path(path).read_text(encoding='utf-8'))


def capture(con, kind, *args):
    if os.getenv('SCOUT_GRADECORE_SHADOW','0') != '1': return 'disabled'
    active=False
    try:
        adapter=getattr(adapters,kind)(*args)
        art={}
        folder=os.getenv('SCOUT_GRADECORE_ARTIFACTS')
        if folder:
            path=Path(folder)/('calibration_'+adapter.sport+'.json')
            if path.exists(): art=_artifact(str(path),path.stat().st_mtime_ns)
        result=engine.assess(adapter,adapter.offer,art)
        artifact_hash=hashlib.sha256(json.dumps(art,sort_keys=True).encode()).hexdigest()
        body={'forecast_id':adapter.forecast_id,'forecast_at':adapter.forecast_at,
              'offer':vars(adapter.offer),'old_letter':adapter.old_letter,'old_p':adapter.old_p,
              'artifact_hash':artifact_hash,'candidate':result}
        key=hashlib.sha256(json.dumps([adapter.sport,adapter.forecast_id,result['policy'],artifact_hash]).encode()).hexdigest()
        con.execute('SAVEPOINT gradecore_capture'); active=True
        con.execute('CREATE TABLE IF NOT EXISTS gradecore_shadow (id TEXT PRIMARY KEY, sport TEXT NOT NULL, '
                    'forecast_id TEXT NOT NULL, forecast_at TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL)')
        for verb in ('UPDATE','DELETE'):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS gradecore_no_{verb.lower()} BEFORE {verb} ON gradecore_shadow "
                        "BEGIN SELECT RAISE(ABORT, 'gradecore shadow is append-only'); END")
        con.execute('INSERT OR IGNORE INTO gradecore_shadow VALUES (?,?,?,?,?,?)',
                    (key,adapter.sport,adapter.forecast_id,adapter.forecast_at,
                     datetime.now(timezone.utc).isoformat(),json.dumps(body,allow_nan=False)))
        con.execute('RELEASE gradecore_capture')
        return 'captured'
    except Exception as exc:
        if active:
            con.execute('ROLLBACK TO gradecore_capture'); con.execute('RELEASE gradecore_capture')
        # Log only exception type: an input or environment path could be private.
        LOG.warning('gradecore shadow skipped: %s',type(exc).__name__)
        return 'skipped'
