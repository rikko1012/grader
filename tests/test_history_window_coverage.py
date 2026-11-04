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
    assert len(doc['rows'])==1
    api.reset()
