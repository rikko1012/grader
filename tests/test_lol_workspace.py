"""Regression coverage for actual page behavior, without production/network access."""
from datetime import datetime, timedelta, timezone

from tests.test_audit_h_web import FUTURE, _lol_row, needs_node, run


@needs_node
def test_scope_side_and_platform_comparison_filters(tmp_path):
    a = _lol_row('a', 'Player', FUTURE)
    b = dict(a, key='b', scope='Map 1', pick_side='Under', p_over=.3, p_under=.7)
    lines = [dict(a, platform='Underdog'), dict(b, platform='PrizePicks')]
    data = {'rows': [a, b], 'match_tables': [{'teams': 'T1 vs GEN', 'start': FUTURE,
        'markets': [{'player': 'Player', 'team': 'T1', 'stat_name': 'Kills', 'scope': 'Map 1', 'lines': lines}]}]}
    out = run(tmp_path, 'lol.html', '''(() => {
      D=INPUT; F.scope="Map 1"; F.side="Under";
      const selected=rows().map(x=>x.key);
      F.plat="prizepicks"; const comparison=tablesView();
      F.scope="Maps 1-2"; const empty=tablesView();
      return {selected,comparison,empty};
    })()''', data)
    assert out['selected'] == ['b']
    assert 'PrizePicks' in out['comparison'].split('</section>')[0]
    assert 'Underdog' not in out['comparison'].split('</section>')[0]
    assert 'No posted LoL lines match these filters.' in out['empty']


@needs_node
def test_cached_http_success_does_not_verify_an_old_quote(tmp_path):
    row = _lol_row('a', 'Player', FUTURE)
    row['line_checked'] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    out = run(tmp_path, 'lol.html', '''(async () => {
      D=INPUT; const leg=legOf(D.rows[0]);
      fetch=async()=>({ok:true,json:async()=>INPUT});
      await fresh(); const stale=legStatus(leg), add=slipRule(D.rows[0]);
      D.rows[0].line_checked=null; const missing=legStatus(leg);
      D.rows[0].line_checked=new Date().toISOString(); const valid=legStatus(leg);
      D.rows[0].start=null; const noStart=legStatus(leg);
      return {stale,add,missing,valid,noStart};
    })()''', {'rows': [row], 'match_tables': []})
    assert not out['stale']['ok'] and '15 minutes' in out['stale']['text']
    assert '15 minutes' in out['add']
    assert not out['missing']['ok'] and out['valid']['ok']
    assert not out['noStart']['ok']


@needs_node
def test_card_default_and_table_preference_keep_exact_values(tmp_path):
    row = _lol_row('a', 'Player', FUTURE)
    out = run(tmp_path, 'lol.html', '''(() => {
      D=INPUT; const cards=boardView(); F.layout="table"; const table=boardView();
      card("a"); return {cards,table,detail:$("#card").innerHTML};
    })()''', {'rows': [row], 'match_tables': []})
    assert 'cards card-grid' in out['cards'] and 'board hide-table' in out['cards']
    assert 'cards card-grid' not in out['table'] and 'board hide-table' not in out['table']
    assert '60.0%' in out['cards'] and '5.1' in out['cards']
    assert out['detail'].index('id="closecard"') < out['detail'].index('id="cardtitle"')


@needs_node
def test_large_board_renders_in_batches_without_losing_filtered_rows(tmp_path):
    data = {'rows': [_lol_row(str(i), f'Player {i}', FUTURE) for i in range(100)], 'match_tables': []}
    out = run(tmp_path, 'lol.html', '''(() => {
      D=INPUT; render(); const initial=$("#app").innerHTML;
      $("#morelines").onclick(); const more=$("#app").innerHTML;
      return {initial,more,total:rows().length};
    })()''', data)
    assert 'Showing 48 of 100' in out['initial'] and 'Showing 96 of 100' in out['more']
    assert out['total'] == 100
