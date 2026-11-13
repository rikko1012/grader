import pytest
from tests.test_upgrade_browser import browser, env, no_h_scroll
from tests.test_ui_next_browser import nxt, nba, ladder_board, row_of


@pytest.mark.parametrize('width',[390,1440])
def test_book_main_line_heads_row_and_alternates_stay_labeled(browser,env,width):
    doc,view=ladder_board()
    market=next(m for m in doc['markets'] if ':399|points' in m['k'])
    quotes=[q for q in market['q'] if q['p']=='bovada'][:3]
    for q,variant,price in zip(quotes,['ladder_rung','main','ladder_rung'],[[1.1,8],[1.91,1.91],[1.4,3]]):
        q['v']=variant;q['d']=price
    market['q']=quotes;doc['markets']=[market]
    size={'width':width,'height':900}
    c=nxt(browser,env,size,board=(doc,view))
    try:
        p=nba(c,env,size);row=row_of(p,'Shai Ladder','Points')
        assert row.count()==1
        assert row.locator('.h-c-line b').inner_text().strip()=='28.5'
        row.locator('[data-hub=ladder]').click()
        ladder=p.locator('tr.h-ladder-row .h-ladder-table tbody tr')
        assert [x.strip() for x in ladder.locator('.h-ld-line b').all_inner_texts()]==['28.5','29.5','27.5']
        assert 'alternate' in ladder.nth(1).inner_text().lower() and 'alternate' in ladder.nth(2).inner_text().lower()
        assert '-1000' in ladder.nth(2).inner_text() or '−1000' in ladder.nth(2).inner_text()
        no_h_scroll(p)
    finally:
        c.close()
