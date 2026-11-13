import json
import shutil
import subprocess
from pathlib import Path

import pytest
import workspace_books as wb
import workspace_sports as ws
from tests.test_phase4_books import rec


@pytest.mark.parametrize('variant,label', [('main','standard'), ('alternate','alternate'),
                                         ('ladder_rung','alternate'), (None,'book line')])
def test_derived_book_does_not_invent_standard_classification(variant, label):
    r = wb.derive('fanduel','FanDuel','sportsbook','cs2',[rec()],38.5,
                  {'offered': True, 'price': 1.91}, {'offered': True, 'price': 1.91},
                  None, 1000, 'k', variant=variant)
    assert r['market']['line_type'] == label
    assert r['scout']['projection'] == 40


def test_snapshot_variant_survives_comparison_adapter():
    same, _ = ws._book_rows({'bk':[{'p':'bovada','l':20.5,'v':'ladder_rung'}]},20.5)
    assert same[0]['variant'] == 'ladder_rung'
    assert ws.line_type_of({'p':'bovada','v':'ladder_rung'}) == 'alternate'


def test_native_main_quote_is_not_lost_when_comparison_points_at_an_alternate():
    base = rec(platform='fanduel',line_=38.5)
    base['offer'].update(seen_at=wb._iso(1000),over={'offered':True,'price':1.91},under={'offered':True,'price':1.91})
    x = {'e':'e1','pkey':'vargo','st':'kills','scope':'maps12','variant':'main','rec':json.dumps(base)}
    snap={'index':{('underdog','u'):dict(x,books=([{'platform':'fanduel','line':20.5,'over':1.1,'under':8,'seen_at':wb._iso(1000),'variant':'alternate'}],[])),
                   ('fanduel','f'):x},'props':{}}
    quotes,_=wb.display_book_quotes(snap,'fanduel')
    assert {(b['line'],b['variant']) for _,b,_ in quotes} == {(20.5,'alternate'),(38.5,'main')}


@pytest.mark.parametrize('book', ['fanduel','betrivers','bovada','draftkings','unibet_au','pointsbet_au'])
def test_main_headline_precedes_high_chance_short_price_and_alternates(tmp_path, book):
    if not shutil.which('node'):
        pytest.skip('node required')
    from tests.test_board_lines import rec as row
    lines=[row('short','p','points',book,10.5,'over',.96,'A',lt='alternate',price=(1.1,8)),
           row('alt','p','points',book,17.5,'over',.8,'A',lt='alternate',price=(1.4,3)),
           row('main','p','points',book,25.5,'over',.53,None,price=(1.91,1.91))]
    module=(Path(__file__).resolve().parents[1]/'web/upgrade-lines.js').as_uri()
    script=tmp_path/'check.mjs'
    script.write_text(f"import * as L from {json.dumps(module)}; const x={json.dumps(lines)}; console.log(JSON.stringify([L.pickMain(x,false).offer.offer_id,L.pickMain(x,true).offer.offer_id,L.ladder(x).map(r=>r.offer.offer_id)]));",encoding='utf-8')
    result=subprocess.run([shutil.which('node'),str(script)],capture_output=True,text=True,timeout=30,check=True)
    assert json.loads(result.stdout)==['main','main',['main','alt','short']]
