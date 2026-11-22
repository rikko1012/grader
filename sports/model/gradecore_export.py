"""Read-only, bounded export of saved sm-1 forecasts for offline calibration.

Run explicitly as a maintenance job; never from a member request. Keeps the last
forecast at least 30 minutes before start for each player/stat/period/line/book.
Does not train, settle, update forecasts, or change containment.
"""
import json
import os
from pathlib import Path
import sqlite3

from .. import store
from ..contracts import parse_utc, now_utc

MAX_ROWS = 100000


def export(data_dir, league, model_version='sm-1'):
    if league not in ('nba', 'nfl') or model_version != 'sm-1':
        raise ValueError('export selects the saved sm-1 model only')
    root = Path(data_dir)
    learning = sqlite3.connect('file:' + (root/'model/learning.db').as_posix() + '?mode=ro', uri=True)
    canonical = sqlite3.connect('file:' + (root/'canonical.db').as_posix() + '?mode=ro', uri=True)
    shown_path = root/'model/shown.db'
    shown = sqlite3.connect(shown_path.resolve().as_uri()+'?mode=ro', uri=True) if shown_path.exists() else None
    if shown:
        shown.row_factory = sqlite3.Row
        shown.execute('PRAGMA query_only=ON')
        shown.execute('BEGIN')
    learning.row_factory = canonical.row_factory = sqlite3.Row
    learning.execute('BEGIN')
    canonical.execute('BEGIN')
    selected, excluded, scanned = {}, {}, 0
    def skip(reason): excluded[reason] = excluded.get(reason, 0) + 1
    try:
        count = learning.execute('SELECT count(*) FROM scorecard WHERE league=? AND model_version=? AND superseded=0',
                                 (league,model_version)).fetchone()[0]
        if count > MAX_ROWS:
            raise ValueError('export limit exceeded; partition history explicitly instead of truncating')
        query = ('SELECT * FROM scorecard WHERE league=? AND model_version=? AND superseded=0 '
                 'ORDER BY forecast_at, forecast_id')
        for r in learning.execute(query, (league,model_version)):
            scanned += 1
            fr = canonical.execute("SELECT payload FROM records WHERE kind='forecast' AND record_id=?", (r['forecast_id'],)).fetchone()
            fc = store._rec(fr)
            if fc is None:
                skip('missing_forecast'); continue
            if (fc.league,fc.model_version,fc.event_id,fc.stat,fc.line) != (league,model_version,r['event_id'],r['stat'],r['line']):
                skip('scorecard_identity_mismatch'); continue
            if not fc.artifact_version:
                skip('missing_frozen_artifact_version'); continue
            er = canonical.execute("SELECT payload FROM records WHERE kind='event' AND event_id=? AND known_at<=? AND late=0 "
                                   'ORDER BY known_at DESC, revision DESC LIMIT 1', (fc.event_id,fc.forecast_at)).fetchone()
            event = store._rec(er)
            if event is None or not event.start_utc:
                skip('missing_point_in_time_start'); continue
            if (parse_utc(event.start_utc)-parse_utc(fc.forecast_at)).total_seconds() < 1800:
                skip('less_than_30_minutes_before_start'); continue
            if parse_utc(r['settled_at']) < parse_utc(event.start_utc):
                skip('invalid_settlement_time'); continue
            dec = fc.p_over + fc.p_under
            if dec <= 0 or r['outcome'] not in ('over','under','push','void'):
                skip('no_decided_mass_or_unsupported_outcome'); continue
            side = fc.chosen_side
            if side not in ('over','under'):
                skip('no_selected_side'); continue
            # audit_1121 nfl r2: chosen_side is the max over the OFFERED sides (grade_policy.apply), so an Over-only
            # offer (PrizePicks demon / goblin, Underdog Over-only) records Over even when the model favours Under
            # (143 NFL train rows, p < 0.5, hit 45.7%). 'side' / 'p' stay exactly as saved (chance_cal reads them);
            # max_p_side / p_max_side are the model's own preferred side, and side_offer_forced marks the rows where
            # the two differ, so max-p populations can drop or re-key them instead of scoring a forced side.
            max_side = 'over' if fc.p_over >= fc.p_under else 'under'
            offered = tuple(s for s in (fc.sides_available or ()) if s in ('over','under'))
            published = None
            if shown:
                sh = shown.execute('SELECT * FROM shown WHERE forecast_id=?',(fc.forecast_id,)).fetchone()
                if sh and (sh['league'],sh['stat'],sh['line']) == (league,fc.stat,fc.line):
                    if (parse_utc(event.start_utc)-parse_utc(sh['shown_at'])).total_seconds() >= 1800:
                        published = sh['p_'+side]
            key = (fc.event_id,fc.player_id,fc.stat,fc.scope,fc.line,r['platform'])
            selected[key] = {'forecast_id':fc.forecast_id,'sport':league,'model_version':model_version,
                             'artifact_version':fc.artifact_version,
                             'probability_stage':'saved_model_before_board_calibration',
                             'match_key':fc.event_id,'player_id':fc.player_id,'market':fc.stat,
                             'scope':fc.scope,'line':fc.line,'book':r['platform'],'side':side,
                             'p':getattr(fc,'p_'+side)/dec,'market_p':None,'published_p':published,
                             'sides_available':list(offered),'max_p_side':max_side,
                             'p_max_side':getattr(fc,'p_'+max_side)/dec,'side_offer_forced':side != max_side,
                             'status':r['outcome'] if r['outcome'] in ('push','void') else 'win' if r['outcome']==side else 'loss',
                             'forecast_at':fc.forecast_at,'start_at':event.start_utc,'settled_at':r['settled_at'],
                             'settlement_id':r['settlement_id'],
                             # audit_1121 round 2: the sides the line listed when forecast (an offer-forced side is
                             # one where only the side the model disfavours was listed)
                             'sides_offered':sorted(fc.sides_available or ())}
    finally:
        learning.close(); canonical.close()
        if shown: shown.close()
    return {'schema':'gradecore-export-1','built_at':now_utc(),'sport':league,'model_version':model_version,
            'population':'last saved sm-1 forecast >=30 minutes before start per event/player/stat/period/line/book',
            'scanned':scanned,'excluded':excluded,'rows':list(selected.values())}


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',required=True)
    p.add_argument('--league',choices=('nba','nfl'),required=True)
    args=p.parse_args()
    result=export(args.data_dir,args.league)
    dest=Path(args.data_dir)/'model/api'/('gradecore_export_'+args.league+'.json')
    dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest.with_suffix('.json.tmp')
    with tmp.open('w',encoding='utf-8') as f:json.dump(result,f,allow_nan=False)
    os.replace(tmp,dest)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}), 'rows=',len(result['rows']))


if __name__=='__main__': main()
