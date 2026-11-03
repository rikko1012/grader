import json
import public_record
from tests.test_upgrade_gate import env,get


def test_public_sanitizer_never_copies_private_fields_or_nonfinite_values():
    doc={'ADMIN_TOKEN':'secret','member':'user_private','totals':{'wins':2,'losses':1,'printed':float('nan'),'user':'private'},
         'by_letter':[{'name':'B','wins':2,'losses':1,'private':'hidden'}],
         'calendar':{'state':'ready','basis':'UTC','days':[{'date':'2026-11-03','wins':2,'forecast_id':'private'}]}}
    result=public_record.sanitize(doc,'cs2')
    wire=json.dumps(result,allow_nan=False)
    assert 'secret' not in wire and 'private' not in wire and 'hidden' not in wire
    assert result['totals']['printed'] is None
    assert result['bands'][0]['wins']==2


def test_public_record_is_explicitly_enabled_without_unlocking_member_routes(env,monkeypatch):
    monkeypatch.delenv('SCOUT_PUBLIC_RECORD',raising=False)
    assert get(env.base,'/record')[0]==404
    assert get(env.base,'/api/record')[0]==404
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1')
    assert get(env.base,'/record')[0]==200
    assert get(env.base,'/record.js')[0]==200
    assert get(env.base,'/api/record?sport=private')[0]==400
    status,body,_=get(env.base,'/api/record?sport=cs2')
    assert status==200 and json.loads(body)['sport']=='cs2'
    assert get(env.base,'/api/upgrade/mine')[0]!=200
    assert get(env.base,'/admin/forecasts_page.json')[0]!=200
