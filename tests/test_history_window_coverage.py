import json
import workspace_api as api


def test_short_history_keeps_actual_sample_and_pushes():
    rows=[{'value':4},{'value':5},{'value':6}]
    assert api._tally_of(rows,5,20)=={'over':1,'under':1,'push':1,'n':3}
    assert api._tally_of([],5,5) is None

def test_budgeted_hits_resume_and_invalidate_changed_lines(monkeypatch):
    api.reset()
    monkeypatch.setattr(api,'HITS_BUDGET_S',-1)
    monkeypatch.setattr(api,'HITS_FILL_TTL',0)
    monkeypatch.setattr(api,'_history',lambda *args,full=False:{'ser':[{'value':4},{'value':5},{'value':6}]} if full else [{'value':4},{'value':5},{'value':6}])
    snap={'index':{('prizepicks',str(i)):{'line':5} for i in range(3)}}
    for n in range(1,4):
        doc=json.loads(api._hits_doc(None,snap))
        assert len(doc['rows'])==n
        assert doc['complete']==(n==3)
    snap['index'][('prizepicks','0')]['line']=7
    doc=json.loads(api._hits_doc(None,snap))
    assert doc['rows']['prizepicks|0']['l5']['under']==3
    assert doc['rows']['prizepicks|0']['line']==7
    assert len(doc['rows'])==3          # owner 2026-11-06: the unmoved rows are KEPT, only the moved one is read again
    assert doc['complete']
    api.reset()


def test_busy_board_keeps_filling_instead_of_restarting(monkeypatch):
    """Live 2026-11-06: lines moving every minute wiped the pass, so most CS2 rows showed L10 only on phones."""
    api.reset()
    monkeypatch.setattr(api,'HITS_BUDGET_S',-1)          # one row per pass
    monkeypatch.setattr(api,'HITS_FILL_TTL',0)
    monkeypatch.setattr(api,'_history',lambda *a,full=False:{'ser':[{'value':4},{'value':5},{'value':6}]})
    snap={'index':{('underdog',str(i)):{'line':5} for i in range(6)}}
    for n in range(6):
        snap['index'][('underdog','5')]['line']=5+n*0.5   # one line moves on every pass
        doc=json.loads(api._hits_doc(None,snap))
    assert len(doc['rows'])==6 and all(v['l5'] for v in doc['rows'].values())
    snap['index'][('underdog','0')]['l10_n']=10                # a settled match changes the saved L10: that row is read again
    doc=json.loads(api._hits_doc(None,snap))
    assert len(doc['rows'])==6
    api.reset()
