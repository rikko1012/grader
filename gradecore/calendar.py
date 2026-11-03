"""Thirty UTC days of accuracy, never simulated returns or implied profitability."""
from datetime import datetime, timedelta, timezone


def daily_record(rows, as_of, basis):
    def day(value):
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        if dt.tzinfo is None: raise ValueError('timezone required')
        return dt.astimezone(timezone.utc).date()
    try: last=day(as_of)
    except (ValueError,TypeError,AttributeError):
        return {'days':[], 'basis':basis, 'state':'date_unavailable'}
    days={str(last-timedelta(days=i)):{'date':str(last-timedelta(days=i)), 'wins':0,'losses':0,'pushes':0} for i in range(29,-1,-1)}
    excluded=0
    for row in rows:
        try: key=str(day(row.get('at')))
        except (ValueError,TypeError,AttributeError):
            excluded+=1;continue
        status=row.get('status')
        if key not in days or status not in ('win','loss','push'):continue
        days[key][{'win':'wins','loss':'losses','push':'pushes'}[status]]+=1
    for value in days.values():
        n=value['wins']+value['losses']
        value['decided']=n
        value['hit_rate']=value['wins']/n if n else None
    return {'days':list(days.values()), 'basis':basis, 'state':'ready', 'missing_dates':excluded,
            'metric':'Accuracy only; no payouts or profit calculated.'}
