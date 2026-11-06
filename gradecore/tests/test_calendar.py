from gradecore.calendar import daily_record


def test_calendar_has_exact_window_utc_dates_and_separate_pushes():
    rows=[{'at':'2026-11-01T23:30:00-06:00','status':'win'},
          {'at':'2026-11-02T08:00:00Z','status':'loss'},
          {'at':'2026-11-02T08:00:00Z','status':'push'},
          {'at':'2026-09-01T08:00:00Z','status':'win'},
          {'at':'2026-11-02T08:00:00Z','status':'void'},
          {'at':None,'status':'win'}]
    doc=daily_record(rows,'2026-11-03T00:00:00Z','Match start date · UTC')
    assert len(doc['days'])==30 and doc['days'][0]['date']=='2026-10-05'
    d=doc['days'][-2]
    assert (d['wins'],d['losses'],d['pushes'],d['decided'],d['hit_rate'])==(1,1,1,2,.5)
    assert doc['days'][-1]['hit_rate'] is None
    assert doc['missing_dates']==1


def test_missing_or_naive_snapshot_date_is_not_invented():
    assert daily_record([],None,'UTC')['state']=='date_unavailable'
    assert daily_record([],'2026-11-03','UTC')['days']==[]


def test_member_zone_window_ends_today_in_us_central():
    # audit 2026-11-05: read at 9:27 PM CST on Nov 5 (03:27 UTC Nov 6) the window is Oct 7 - Nov 5, never a future day, and a
    # 7 PM CST game counts on Nov 5
    from gradecore.calendar import MEMBER_ZONE
    doc=daily_record([{'at':'2026-11-06T01:00:00Z','status':'win'}],'2026-11-06T03:27:00Z','Match start date · US Central',MEMBER_ZONE)
    assert len(doc['days'])==30 and doc['days'][0]['date']=='2026-10-07' and doc['days'][-1]['date']=='2026-11-05'
    assert doc['days'][-1]['wins']==1
