/* One real research surface over the existing authenticated sport adapters.
 * No grades or prices are calculated here. The original pages and their tools stay intact.
 */
import {panels as cardPanels} from '/upgrade/cards.js';
import {groupProps,groupPlayers,lineId,propKey as linePropKey} from '/upgrade/lines.js';
export function mount(C) {
  /* UI_NEXT (redesign 2026-11-11): the page's head script chose the design (UI_NEXT=1 or the member's /preview/next cookie) and
     loaded its sheets; hub.css is not loaded with it. Display only: no number on the board or a card differs between the designs. */
  const NEXT=document.documentElement.dataset.ui==='next';
  if(!NEXT&&!document.querySelector('link[href="/upgrade/hub.css"]')){const style=document.createElement('link');style.rel='stylesheet';style.href='/upgrade/hub.css';document.head.appendChild(style);}
  const {api, R, T, SPORTS, esc, pct, when} = C;
  const V2 = R.me.features?.workspace_v2 === true;
  // SCOUT_BOARD_H2H (default off): H2H + Season columns, 7-tone cells, start countdown, Edge vs the Underdog standard break-even
  const COLS = R.me.features?.board_h2h && typeof R.me.features.board_h2h === 'object' ? R.me.features.board_h2h : null;
  // SCOUT_UI_TOKENS (default off): classic 1Time tokens from their own sheet, a skip link and an account control. Off = nothing here runs.
  const TOKENS = R.me.features?.ui_tokens === true;
  if(TOKENS){document.documentElement.dataset.uiTokens='1';if(!document.querySelector('link[href="/upgrade/tokens.css"]')){const t=document.createElement('link');t.rel='stylesheet';t.href='/upgrade/tokens.css';document.head.appendChild(t);}}
  // SCOUT_BOOK_<PROVIDER> (each default off): extra books in the Platform toggle. Display-only lines, never on a slip (one book per slip).
  const EXTRA = Array.isArray(R.me.features?.extra_books) && R.me.features.extra_books.length ? R.me.features.extra_books : null;
  const isExtra = b => !!EXTRA && EXTRA.some(x => x.id === b);
  // SCOUT_BACKGROUND_ALERTS: "Alert me" is delivered by the server job; without it the page-only feature is "Live highlight"
  const BG = R.me.features?.background_alerts === true;
  const ALERT_WORD = BG ? 'Alerts' : 'Live highlight';
  /* 4.16 board parity (SCOUT_PARITY_*, each default off; borrowed from Upside / Outlier). Every number is the server's
     (gradecore/parity.py: EV = p / break-even - 1, the verified break-even, the band, the hit-rate key and the display sizes).
     No flag on = PAR is null and none of the parity code below runs. */
  const PAR = R.me.features?.parity && typeof R.me.features.parity === 'object' ? R.me.features.parity : null;
  const P = n => !!PAR?.[n];
  // SCOUT_PICKEM_EV (default off): the pick'em +EV board at #/ev (web/upgrade-ev.js, served only with the flag on)
  const EVB = R.me.features?.pickem_ev === true;
  /* SCOUT_MARKET_EDGE (owner site audit 2026-11-06): the Market edge column, the best line and price per side across books and the
     line discrepancy chip on every sport. The server's numbers only (workspace_market_edge.py): the books' chance with their margin
     removed (2+ books with a current price) minus what one pick needs on that app's 2-pick entry. Never Scout's grade. */
  const MX = R.me.features?.market_edge && typeof R.me.features.market_edge === 'object' ? R.me.features.market_edge : null;
  const $ = id => document.getElementById(id);
  const H = {minGrade:'all',sport:'all', book:'all', q:'', side:'all', sort:'start', stat:'all', game:'all', onlyDiff:false, watched:false, freshOnly:false, soonOnly:false, movedOnly:false,
    expanded:null, detail:null, detailDoc:null, detailState:'idle', detailSeq:0, histN:10, histSide:'over', pickSport:'all', pickStatus:'all',
    page:0, section:'board', loading:false, seq:0, records:[], views:[], watch:[], alerts:[], seen:new Map(), notices:[], saveBusy:false, generating:false, batch:null, batchSaved:false,
    cardTab:'overview', view:'list', studioTab:'slip'};
  H.pendingOpen=null;H.graded=new Set();
  /* A6 (audit 2026-11-05): one sort for every sport, from the Sort menu or a column header. Default: Edge where the Edge column
     exists, else start time. The member's choice is remembered on their account (and on this device until it syncs). */
  const HAS_EDGE=!!COLS||P('edge');
  const SORTS=[...(HAS_EDGE?[['edge','Edge vs break-even']]:[]),...(MX?[['medge','Market edge']]:[]),['chance','Scout chance'],['start','Start time'],['l5','L5 hit rate'],['l10','L10 hit rate'],['l20','L20 hit rate'],
    ...(COLS?[['h2h','H2H hit rate'],['season','Season hit rate']]:[]),['line','Line'],['player','Player name'],...(P('hit_sort')?[['hit','Hit rate vs break-even']]:[])];
  const SORT_KEYS=SORTS.map(x=>x[0]);
  const DEFAULT_SORT=HAS_EDGE?'edge':'start';
  const ASC_FIRST=new Set(['start','line','player']);
  const sortDirOf=k=>ASC_FIRST.has(k)?'asc':'desc';
  H.sort=DEFAULT_SORT;H.sortDir=sortDirOf(DEFAULT_SORT);H.sortChosen=false;
  const SORT_LS=()=>'scout-board-sort:'+(R.me?.member||'me');
  function adoptSort(v){if(!v||typeof v!=='object'||!SORT_KEYS.includes(v.key))return false;H.sort=v.key;H.sortDir=v.dir==='asc'||v.dir==='desc'?v.dir:sortDirOf(v.key);H.sortChosen=true;return true;}
  try{adoptSort(JSON.parse(localStorage.getItem(SORT_LS())||'null'));}catch(e){/* no saved sort on this device */}
  const CARD_TABS=[['overview','Overview'],['lines','Lines'],['stats','Stats'],['ask','Ask']];
  const DESKTOP=matchMedia('(min-width:1101px)');
  H.extraRecs=[];H.extraState=null;H.mx={};
  H.par={odds:'american',sizing:'quarter',bankroll:null};H.parKeep=null;
  /* owner 2026-11-05: a book shows in the Platform toggle only on a sport tab where it has lines right now. Extra books: the shared
     /book-lines counts (null until the first read, so extra buttons start hidden; a failed read keeps the last good copy). PrizePicks /
     Underdog: a row on the board for that sport (shown while the sport is still loading). */
  H.bookLines=null;
  /* owner 2026-11-05: the member's goblin rule for every slip Scout builds or suggests (generator and adder). Saved in prefs. */
  const LINE_RULES=[['no_goblins','No goblins'],['one_goblin','At most 1 goblin per slip'],['standard','No goblins or demons'],['all','Any lines']];
  const cleanLines=v=>LINE_RULES.some(r=>r[0]===v)?v:'no_goblins';
  H.slipLines='no_goblins';T.slipLines=H.slipLines;
  H.mt={league:'all',book:'all',side:'all',q:'',minEv:'0',fresh:'all',sort:'ev',dense:false,stake:100,tab:null,moreOpen:false};
  try{H.mt.dense=localStorage.getItem('scout-market-density')==='compact';}catch(e){}
  /* QOL 2026-11-07: Price check's Book / Side / Minimum / Checked within / Sort are kept on this device across reloads (Sport still
     follows the board's sport tab; a saved book with no row today falls back to All books when the sheet draws) */
  const MT_KEEP={book:v=>/^[\w:.-]{1,40}$/.test(v),side:v=>['all','over','under'].includes(v),minEv:v=>['0','0.01','0.02','0.05'].includes(v),fresh:v=>v==='all'||/^\d{1,6}$/.test(v),sort:v=>['ev','chance','fresh'].includes(v)};
  try{const v=JSON.parse(localStorage.getItem('scout-price-filters')||'null');if(v&&typeof v==='object')for(const [k,ok] of Object.entries(MT_KEEP))if(typeof v[k]==='string'&&ok(v[k]))H.mt[k]=v[k];}catch(e){/* storage blocked */}
  const mtRemember=()=>{try{localStorage.setItem('scout-price-filters',JSON.stringify(Object.fromEntries(Object.keys(MT_KEEP).map(k=>[k,String(H.mt[k])]))));}catch(e){/* storage blocked */}};
  /* QOL 2026-11-07: search ignores case and accents (Jokić = jokic) and matches every word typed, in any order */
  /* one fold for the board search and the Price check search (merge 1107): case, accents and extra spaces folded */
  const fold=s=>String(s??'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/\s+/g,' ').trim();
  const textHas=(text,q)=>{const t=fold(text);return fold(q).split(/\s+/).filter(Boolean).every(w=>t.includes(w));};
  const allowed = () => Object.keys(SPORTS).filter(s => R.me.sports && R.me.sports[s]);
  const key = r => [r.sport,r.offer.platform,r.offer.offer_id].join('|');
  const playerKey = r => r.sport+'|'+r.player.key;
  const num = n => typeof n === 'number' && Number.isFinite(n);
  const filters = () => ({minGrade:H.minGrade,sport:H.sport,book:H.book,q:H.q,side:H.side,sort:H.sort,sortDir:H.sortDir,stat:H.stat,game:H.game,onlyDiff:H.onlyDiff,watched:H.watched,freshOnly:H.freshOnly,soonOnly:H.soonOnly,movedOnly:H.movedOnly});
  const filterKeys = ['minGrade','sport','book','q','side','sort','sortDir','stat','game','onlyDiff','watched','freshOnly','soonOnly','movedOnly'];
  const gradeOrder = ['B-','B','B+','A-','A','A+'];
  const gradeMatches = (r,minimum) => !minimum||minimum==='all'||(gradeOrder.includes(minimum)&&!r.scout.hold&&gradeOrder.indexOf(r.scout.grade)>=gradeOrder.indexOf(minimum));
  const gameKey = r => r.sport+'|'+r.event.id;
  const statKey = r => r.sport+'|'+r.market.stat;
  const isOpen = () => !$('u-hub').hidden;
  const focusAfter = container => {
    const el=document.activeElement;
    if(container?.contains(el)&&el.id&&!el.dataset.hub)return ()=>{const now=document.getElementById(el.id);if(now&&now!==document.activeElement)now.focus({preventScroll:true});};
    if(!container?.contains(el)||!el.dataset.hub)return ()=>{};
    const tag=el.tagName.toLowerCase(),action=el.dataset.hub,id=el.dataset.id,prop=el.closest('tr[data-prop]')?.dataset.prop;
    // Match the focused control, not its non-focusable row with the same action and ID.
    // A grouped prop can choose a different offer as data arrives. Keep keyboard focus on that prop,
    // even when the old offer ID no longer owns its main row.
    return ()=>{const exact=container.querySelector(`${tag}[data-hub="${CSS.escape(action)}"]${id==null?'':`[data-id="${CSS.escape(id)}"]`}`);
      const fallback=prop?container.querySelector(`tr[data-prop="${CSS.escape(prop)}"] .h-player`):null;
      (exact||fallback)?.focus({preventScroll:true});};
  };
  const plural = (n,w) => `${n.toLocaleString()} ${w}${n===1?'':'s'}`;
  const compOf = r => {const c=String(r.event.competition||'').trim();return c&&c.toLowerCase()!==String(SPORTS[r.sport]||r.sport).toLowerCase()?c:'';};
  const eventTag = r => esc(SPORTS[r.sport]||r.sport)+(compOf(r)?' · '+esc(compOf(r)):'');
  const age = at => { const m = Math.max(0,Math.floor((Date.now()-Date.parse(at))/60000)); return Number.isFinite(m) ? m < 1 ? 'Just now' : m < 60 ? m+'m ago' : Math.floor(m/60)+'h ago' : 'Time unavailable'; };
  const different = r => C.siblings(r).some(x => x.offer.platform !== r.offer.platform && x.offer.line !== r.offer.line);
  const unavailableReason = r => {
    const b=R.boards[r.sport], feed=b.feeds?.[r.offer.platform];
    if(b.error) return 'Refresh failed';
    if(feed && feed.state!=='ok') return 'Source '+(feed.state||'unavailable');
    if(!['fresh','ok','ready','live'].includes(r.state.kind)) return r.state.kind;
    return null;
  };
  function matches(r, f=H) {
    return (f.sport==='all'||r.sport===f.sport) && (f.book==='all'||r.offer.platform===f.book)
      && (!f.stat||f.stat==='all'||statKey(r)===f.stat) && (!f.game||f.game==='all'||gameKey(r)===f.game)
      && (f.side==='all'||sideMatches(r,f.side)) && (!f.onlyDiff||different(r))
      && gradeMatches(r,f.minGrade)
      && (!f.watched||H.watch.includes(playerKey(r)))
      && (!f.freshOnly||!unavailableReason(r))
      && (!f.movedOnly||r.offer.moved_from!=null)
      && (!f.soonOnly||(Date.parse(r.event.start_utc)>Date.now()&&Date.parse(r.event.start_utc)-Date.now()<=3600000))
      && (!fold(f.q)||searchText(r).includes(fold(f.q)));
  }
  /* N6 (site audit 2026-11-06): Side reads Scout's lean; a line with no lean (most NBA / NFL rows) is listed under each side its book
     offers, so "Under" never empties a board that has Under lines. Minimum grade applies only where the tab has letters to rank: on a
     tab with no graded line the control is hidden and the board ignores it (a saved alert keeps its own minimum). */
  const sideMatches=(r,sd)=>r.scout.side?r.scout.side===sd:!!r.offer[sd]?.offered;
  const graded=r=>gradeOrder.includes(r.scout.grade)&&!r.scout.hold;
  const gradeFilterOn=(sport=H.sport)=>sport==='all'?H.graded.size>0:H.graded.has(sport);
  /* the board's filters for one sport tab: the tab a member opens starts on All markets and all games (as the sport action does) */
  const boardFilter=(sport=H.sport)=>({...filters(),sport,...(sport===H.sport?{}:{stat:'all',game:'all'}),minGrade:gradeFilterOn(sport)?H.minGrade:'all'});
  /* N11: search reads the name, team, game and market, plus the exact other spellings the server sends for the row's two teams
     (the book's own name and maintained team aliases: player.aka). Plain substring of those exact strings, nothing fuzzy. */
  /* QOL 2026-11-07: case, accents and extra spaces never hide a row ("jokic" finds Jokić, " vargo  " finds vargo). The same exact
     strings, only folded (fold is defined once near the top of mount); one folded copy per record (records are replaced on every board read) */
  const SEARCH_TEXT=new WeakMap();
  const searchText=r=>{let t=SEARCH_TEXT.get(r);if(t==null){t=fold([r.player.name,r.player.team,r.event.label,r.market.stat_label,...(Array.isArray(r.player.aka)?r.player.aka:[])].join(' '));SEARCH_TEXT.set(r,t);}return t;};
  const boardSource=()=>isExtra(H.book)?H.extraRecs:H.records;
  function rows() {
    // owner rules (no unsure labels; transient failures hide the line): a row whose source is stale, failed or held is left off, never labelled
    // games stay together: start time, then the game, then the player (a game header row opens each run)
    // never sort by an empty column: with no edge on any shown line, Edge sorting falls back to start time and the sort control says so
    const f=boardFilter(), list=boardSource().filter(r=>!C.started(r)&&!unavailableReason(r)&&matches(r,f));
    const sort=SORT_KEYS.includes(H.sort)?H.sort:DEFAULT_SORT;
    H.sortFallback=(sort==='edge'&&!list.some(r=>edgeValue(r)!=null))||(sort==='medge'&&!list.some(r=>mxValue(r)!=null))||(sort==='hit'&&!list.some(r=>parOf(r)?.hit))||(WINDOWS.has(sort)&&!list.some(r=>sortValue(r,sort)!=null));
    H.sortBy=H.sortFallback?'start':sort;
    const asc=H.sortFallback?true:H.sortDir==='asc';
    const byStart=(a,b)=>Date.parse(a.event.start_utc)-Date.parse(b.event.start_utc)||gameKey(a).localeCompare(gameKey(b))||a.player.name.localeCompare(b.player.name);
    /* an empty cell (no side, no games at this line, no edge) sorts after every filled one, in either direction */
    const byValue=(x,y)=>x==null&&y==null?0:x==null?1:y==null?-1:(asc?x-y:y-x);
    return list.sort((a,b)=> {
      if(H.sortBy==='start') return asc?byStart(a,b):byStart(b,a);
      if(H.sortBy==='player') return (asc?1:-1)*a.player.name.localeCompare(b.player.name)||byStart(a,b);
      if(H.sortBy==='line'||H.sortBy==='chance'||WINDOWS.has(H.sortBy)) return byValue(sortValue(a,H.sortBy),sortValue(b,H.sortBy))||byStart(a,b);
      if(H.sortBy==='medge') return byValue(mxValue(a),mxValue(b))||byStart(a,b);   /* rows with no market edge last, in either direction */
      if(H.sortBy==='edge'){const x=edgeValue(a),y=edgeValue(b),ea=P('edge')&&!!evOf(a),eb=P('edge')&&!!evOf(b);   /* rated EV % first, then points */
        return x==null&&y==null?byStart(a,b):x==null?1:y==null?-1:(eb-ea)||(asc?x-y:y-x)||byStart(a,b);}
      /* SCOUT_PARITY_HIT_SORT: hit rate minus break-even (the server's key); a line whose hit rate is under its break-even sorts after
         every line above it, and a line with no key after both, so 100% hit rates at a price that cannot pay never flood the top */
      if(H.sortBy==='hit'){const x=parOf(a)?.hit,y=parOf(b)?.hit,rk=h=>!h?2:h.below?1:0;return rk(x)-rk(y)||(x&&y?y.key-x.key:0)||byStart(a,b);}
      return byStart(a,b);
    });
  }
  /* the value a row sorts on: Scout's chance on its side; a window's hit rate for Scout's side (no side: empty, sorted last); the line */
  const WINDOWS=new Set(['l5','l10','l20','h2h','season']);
  function sortValue(r,k) {
    if(k==='chance')return num(r.scout.p_side)?r.scout.p_side:null;
    if(k==='line')return Number.isFinite(Number(r.offer.line))?Number(r.offer.line):null;
    if(WINDOWS.has(k)){const t=C.tallies(r)[k];if(!t||!t.n||!r.scout.side)return null;return countOf(t,r.scout.side)/t.n;}
    return null;
  }
  const sortName=k=>(SORTS.find(x=>x[0]===k)||[k,k])[1];
  /* the filters that live in the Filters pop-over; the count on its button. Search, sport and platform are on the toolbar itself */
  const activeFilters = () => [H.stat!=='all',H.side!=='all',H.minGrade!=='all'&&gradeFilterOn(),H.onlyDiff,H.watched,H.freshOnly,H.soonOnly,H.movedOnly,H.game!=='all'].filter(Boolean).length;
  const BOOK_SHORT={prizepicks:'PP',underdog:'UD',...Object.fromEntries((EXTRA||[]).map(x=>[x.id,x.short||x.label]))};
  /* 'yes' = lines on this tab now; 'no' = the server counted 0 open lines on every sport here (or the book lists none of them);
     'unknown' = no count yet for a sport here (first read pending, or a feed blip with no earlier count). Unknown never moves a member
     off a book they chose (transient-failure rule); it only keeps a book they did not choose hidden. */
  function bookLinesState(id,sport=H.sport) {
    if(id==='all')return 'yes';
    const sports=sport==='all'?visibleSports():allowed().filter(s=>s===sport);
    if(isExtra(id)){
      const p=EXTRA.find(x=>x.id===id),here=sports.filter(s=>p.sports.includes(s));
      const n=here.map(s=>H.bookLines?.[id]?.[s]);
      return n.some(v=>v>0)?'yes':n.some(v=>!Number.isInteger(v))?'unknown':'no';
    }
    if(!['prizepicks','underdog'].includes(id))return 'no';
    return sports.some(s=>!R.boards[s]?.at||H.records.some(r=>r.sport===s&&r.offer.platform===id&&!C.started(r)))?'yes':'no';
  }
  const bookHasLines=(id,sport)=>bookLinesState(id,sport)==='yes';
  /* hide each book with no lines on this tab; a chosen book the server reports empty falls back to All (never an empty board or a banner) */
  function syncBooks() {
    $('hub-platform').querySelectorAll('[data-hub="book"]').forEach(b=>{if(b.dataset.id!=='all'){const st=bookLinesState(b.dataset.id);b.hidden=!(st==='yes'||(st==='unknown'&&b.dataset.id===H.book));}});
    if(H.book!=='all'&&bookLinesState(H.book)==='no'){H.book='all';H.page=0;H.extraState=null;H.extraRecs=[];}
    if(P('plays'))syncPlays();
  }
  /* the counts change slowly; one read per BOOK_LINES_EVERY ms however often the board refreshes (the board polls every 60 s) */
  const BOOK_LINES_EVERY=120000;
  async function loadBookLines(force) {
    if(!EXTRA)return;
    if(!force&&H.bookLinesAt&&Date.now()-H.bookLinesAt<BOOK_LINES_EVERY)return;
    H.bookLinesAt=Date.now();
    try{
      const d=await api('book-lines');
      /* merge only the counts the server sent: a book or sport it left out (its feed blipped before any good count) keeps our last copy */
      if(d&&d.books&&typeof d.books==='object'){const next={...(H.bookLines||{})};
        for(const [pid,v] of Object.entries(d.books))if(v&&typeof v==='object'){next[pid]={...(next[pid]||{})};for(const [s,n] of Object.entries(v))if(Number.isInteger(n)&&n>=0)next[pid][s]=n;}
        H.bookLines=next;}
    }catch(e){H.bookLinesAt=0;/* keep the last good copy; try again on the next board refresh */}
    if(H.section==='board')paintBoard();else syncBooks();
  }
  function syncFields() {
    H.graded=new Set(currentLines().filter(graded).map(r=>r.sport));
    $('hub-minGrade').closest('label').hidden=!gradeFilterOn();
    for(const n of ['side','q','minGrade']) $('hub-'+n).value=H[n];
    $('hub-sort').value=SORT_KEYS.includes(H.sort)?H.sort:DEFAULT_SORT;
    const dirBtn=$('hub-sort-dir');if(dirBtn){const asc=H.sortDir==='asc';dirBtn.setAttribute('aria-label',`${sortName(H.sort)}: ${asc?'low to high':'high to low'}. Reverse order`);dirBtn.dataset.dir=asc?'asc':'desc';dirBtn.textContent=asc?'↑':'↓';}
    syncBooks();
    $('hub-platform').querySelectorAll('[data-hub="book"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===H.book)));
    $('hub-diff').setAttribute('aria-pressed',String(H.onlyDiff));
    $('hub-watch').setAttribute('aria-pressed',String(H.watched));
    if(V2)for(const f of ['freshOnly','soonOnly','movedOnly'])$('hub-'+f).setAttribute('aria-pressed',String(H[f]));
    const read=s=>s==='all'?allowed().every(x=>R.boards[x]?.at):!!R.boards[s]?.at;
    /* N13 (site audit 2026-11-06): a tab's count is the lines that tab lists, by the board's own rule (current lines only, the
       toolbar's search, platform and filters), so the open tab always equals the count over the list */
    const tabCount=s=>{const f=boardFilter(s);return boardSource().filter(r=>!C.started(r)&&!unavailableReason(r)&&matches(r,f)).length;};
    $('hub-sports').innerHTML=['all',...allowed()].map(s=>`<button type="button" data-hub="sport" data-id="${s}" aria-pressed="${H.sport===s}">${s==='all'?'All':SPORTS[s]}${read(s)?`<span>${tabCount(s)}</span>`:'<span class="h-n-wait" aria-hidden="true"></span>'}</button>`).join('');
    const sportRows=H.records.filter(r=>!C.started(r)&&(H.sport==='all'||r.sport===H.sport));
    const markets=[...new Map(sportRows.map(r=>[statKey(r),r.market.stat_label+(H.sport==='all'?' · '+SPORTS[r.sport]:'')])).entries()];
    $('hub-stat').innerHTML='<option value="all">All markets</option>'+markets.sort((a,b)=>a[1].localeCompare(b[1])).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
    $('hub-stat').value=H.stat;
    const n=activeFilters();
    $('hub-filter-n').textContent=n?String(n):'';
    $('hub-filters-toggle').setAttribute('aria-label',n?`Filters, ${n} on`:'Filters');
    if(H.section==='board')$('hub-title').textContent=H.sport==='all'?'Player props':SPORTS[H.sport]+' player props';
  }
  /* ---- board rows (2026-11-05 structure review): one compact line per prop. Player, market (+N more props), line with its book and
     seen time, projection, ONE Scout cell, ONE hit strip, compare, add. The matchup and the countdown sit on the game header row. ---- */
  const sideWord = sd => sd==='over'?'Over':'Under';
  const countOf = (t,sd) => sd==='over'?t.over:t.under;
  /* owner 2026-11-09: the classic cards' factor panels and every book's odds, on the hub card (web/upgrade-cards.js, display only) */
  let PXc=null;
  const PX = () => PXc||(PXc=cardPanels({esc,pct,when,sideWord}));
  const pxCtx = r => ({platforms:C.PLATFORMS,lineKind:C.lineKind,oddsPref:H.par.odds,age,bookLabel:p=>C.PLATFORMS[p]?.label||String(p||'').replace(/^./,c=>c.toUpperCase()),extra:MX?(bestOf(r)?.bk||[]):[]});
  const STRIP = [['l5','L5'],['l10','L10'],['l20','L20'],...(COLS?[['h2h','H2H'],['season','SZN']]:[])];
  const STRIP_TITLE = {l5:'Last 5 results at this line',l10:'Last 10 results at this line',l20:'Last 20 results at this line',h2h:"Past meetings with tonight's opponent, at this line",season:"This season's results, at this line"};
  const currentLines = () => H.records.filter(r=>!C.started(r)&&!unavailableReason(r));
  /* one hit-strip cell: the rate on the cell, the exact counts in its tooltip; a window with fewer games than its size says so there */
  function stripCell(r,w,label) {
    const t=C.tallies(r)[w], side=r.scout.side, opp=r.player.opponent||'this opponent';
    const what=n=>w==='h2h'?`of the last ${n} ${n===1?'meeting':'meetings'} with ${opp}`:w==='season'?`of ${n} ${t?.season?t.season+' ':''}season ${n===1?'result':'results'}`:`of the last ${n} at this line`;
    if(!t||!t.n) {
      // the shared CS2 pass has not reached this row yet: a quiet placeholder that fills in when it arrives
      if(w!=='l10'&&C.hitsPending(r)) return `<span class="h-hit h-hit-wait" role="img" aria-label="${label} loading"></span>`;
      const none=w==='h2h'?`No past meetings with ${opp} at this line`:w==='season'?'No completed results this season at this line':'No completed results at this line';
      return `<span class="h-hit empty" title="${esc(none)}" aria-label="${label}: ${esc(none)}">—</span>`;
    }
    const few=w[0]==='l'&&t.n<Number(w.slice(1))?` (${t.n} ${t.n===1?'game':'games'} on file)`:'';
    if(!side) {
      const text=`${t.over} Over, ${t.under} Under, ${t.push||0} pushes ${what(t.n)}${few}`;
      return `<span class="h-hit h-hit-both" title="${esc(text)}" aria-label="${label}: ${esc(text)}">${t.over}-${t.under}</span>`;
    }
    const hit=countOf(t,side), rate=hit/t.n, text=`${hit} ${sideWord(side)} ${what(t.n)}, ${t.push||0} pushes${few}`;
    if(P('hit_samples')) {   /* SCOUT_PARITY_HIT_SAMPLES: the sample on the cell ("6/10"), the % in its tooltip */
      const full=`${text} (${Math.round(rate*100)}%)`;
      return `<span class="h-hit h-hit-n h-tone-${Math.min(6,Math.floor(rate*7))}${few?' h-few':''}" style="--hit:${rate.toFixed(3)}" title="${esc(full)}" aria-label="${label}: ${esc(full)}">${hit}/${t.n}</span>`;
    }
    return `<span class="h-hit h-tone-${Math.min(6,Math.floor(rate*7))}${few?' h-few':''}" style="--hit:${rate.toFixed(3)}" title="${esc(text)}" aria-label="${label}: ${esc(text)}">${Math.round(rate*100)}%</span>`;
  }
  /* card-and-chips 2026-11-06: a row with no side shows "Over-Under" counts ("3-2"): its phone labels say so ("L5 O-U"); a row with no
     past result in any window (and none still loading) gets no strip line on the phone card at all (h-nohits), not five dashes */
  const stripHtml = r => `<div class="h-strip">${STRIP.map(([w,l])=>`<span class="h-strip-cell h-s-${w}" data-label="${l}${r.scout.side?'':' O-U'}">${stripCell(r,w,l)}</span>`).join('')}</div>`;
  const noHits = r => STRIP.every(([w])=>!C.tallies(r)[w]?.n)&&!C.hitsPending(r);
  /* Edge vs break-even (owner 2026-11-05): the row's current Scout chance minus the platform's published per-pick break-even.
     Underdog standard at a 1.0x pick: the gradecore payout table (gradecore/payouts.py, Underdog's published payouts, the server sends
     the value and its source). With the parity payload (r.parity.be: Underdog's table and PrizePicks' help-center table, verified by
     owner sign-off 2026-11-05) the gap is measured against that row's own book break-even, so an unrated PrizePicks row shows its
     points gap too. Goblin / demon / promo lines pay an unpublished multiplier: their cell stays empty.
     A fact about the chance against the payout, not a grade: a held or contained letter does not change it. */
  function edgeValue(r) {
    if(P('edge')&&evOf(r))return evOf(r).ev*100;     /* SCOUT_PARITY_EDGE: EV in % only where the slip check rates the line */
    const par=edgeBe(r);
    if(par)return (r.scout.p_side-par.be)*100;      /* not rated: the points gap against this row's own book break-even */
    const s=r.scout, be=COLS?.ud_standard_break_even, mult=s.side?r.offer[s.side]?.payout:null;
    if(r.offer.platform!=='underdog'||r.market.line_type!=='standard'||!s.side||!num(s.p_side)||!num(be)||!num(mult)||Math.abs(mult-1)>1e-9)
      return null;
    return (s.p_side-be)*100;
  }
  const EDGE_WHY=()=>P('edge')?parEdgeWhy():`Scout chance minus the per-pick break-even of an ${COLS?.entry||'Underdog standard 2-pick'} entry`+(num(COLS?.ud_standard_break_even)?` (${(COLS.ud_standard_break_even*100).toFixed(1)}%`+(COLS?.source?`, from Underdog's published payouts${COLS.read_at?' read '+COLS.read_at:''}`:'')+')':'')+', in points. Rows that carry their own book break-even (PrizePicks from its help-center payout table) are measured against it. Goblin, demon and boosted lines publish no fixed per-pick payout, so their cells stay empty.';
  function edgeCell(r) {
    if(P('edge')&&evOf(r))return parEdgeCell(r);     /* otherwise the points gap below, as before */
    const d=edgeValue(r);
    if(d==null)return `<span class="h-edge empty" title="${esc(r.market.line_type!=='standard'?'No fixed per-pick payout is published for this line type':!r.scout.side?'No Scout side on this line':'No published per-pick break-even for this line')}">—</span>`;
    const par=edgeBe(r), be=par?par.be:COLS.ud_standard_break_even, entry=par?par.entry:(COLS.entry||'Underdog standard')+' entry';
    const why=par&&P('edge')?' · not rated: Scout’s settled results don’t back this chance yet, so no EV is shown':'';
    return `<span class="h-edge${d>0?' positive':''}" title="${esc(`Scout chance ${pct(r.scout.p_side)} against the ${(be*100).toFixed(1)}% break-even of a ${entry}${why}`)}"><b>${d>0?'+':d<0?'−':'±'}${Math.abs(d).toFixed(1)}</b><small>pts</small></span>`;
  }
  /* ---- 4.16 parity helpers: display of the server's numbers only ---- */
  const parOf = r => r.parity && typeof r.parity === 'object' && num(r.parity.be) ? r.parity : null;
  /* the server sends ev / band / sizes only for a line the live slip check would rate (CS2, letter not held, half-point line,
     a chance band that passed the settled-results check); every other row keeps the points gap and gets no band */
  const evOf = r => {const p=parOf(r);return p&&num(p.ev)?p:null;};
  /* the row's own book break-even for the points gap: standard line, Scout side, a chance; never a goblin / demon / promo line */
  const edgeBe = r => {const p=parOf(r), s=r.scout||{};
    if(!p||r.market?.line_type!=='standard'||!s.side||!num(s.p_side))return null;
    if(r.offer?.platform==='underdog'){const m=r.offer[s.side]?.payout;if(!num(m)||Math.abs(m-1)>1e-9)return null;}   /* Underdog: 1.0x picks only */
    return p;};
  const evText = ev => `${ev>0?'+':ev<0?'−':'±'}${Math.abs(ev*100).toFixed(1)}%`;
  const beOdds = p => H.par.odds==='decimal'&&num(p.be_decimal)?p.be_decimal.toFixed(2):num(p.be_american)?(p.be_american>0?'+':'−')+Math.abs(p.be_american):pct(p.be);
  const SIZING=[['quarter','¼ Kelly'],['half','½ Kelly'],['flat','Flat 1%']];
  const sizingLabel = () => (SIZING.find(x=>x[0]===H.par.sizing)||SIZING[0])[1];
  /* SCOUT_PARITY_PREFS: the member's display size for one line, as a share of bankroll or in dollars, never in units (Scout's
     stake on the line stays the only unit figure) */
  function sizeText(p) {
    const v=p.sizes_pct?.[H.par.sizing];
    if(!num(v))return null;
    return num(H.par.bankroll)&&H.par.bankroll>0?'$'+(v/100*H.par.bankroll).toFixed(2):v.toFixed(1)+'% of bankroll';
  }
  function parEdgeWhy() {
    const t=PAR?.break_even||{}, parts=Object.values(t).filter(v=>v&&num(v.be)).map(v=>`${v.entry} needs ${pct(v.be)} a pick`);
    return 'On lines where Scout’s settled results back the chance: average return of one pick against its app’s published payout (Scout chance ÷ break-even − 1).'+(parts.length?' '+parts.join('; ')+'.':'')+' Other lines (not rated yet) show Scout chance minus their app’s break-even, in points. Goblin, demon and boosted lines have no published per-pick payout, so they show —.';
  }
  function parEdgeCell(r) {
    const p=evOf(r);
    if(!p)return `<span class="h-edge empty" title="No published payout for this line, so there is no break-even to measure it against">—</span>`;
    const size=P('prefs')?sizeText(p):null;
    const title=`Scout chance ${pct(r.scout.p_side)} against the ${pct(p.be)} break-even (${beOdds(p)}) of a ${p.entry}: ${evText(p.ev)} average return a pick`+(size?` · ${sizingLabel()}: ${size}`:'');
    const cell=`<span class="h-edge${p.ev>0?' positive':p.ev<0?' negative':''}" title="${esc(title)}" aria-label="${esc('Edge '+title)}"><b>${evText(p.ev)}</b></span>`;
    return P('ev_band')?`<span class="h-edge-wrap">${cell}${bandChip(r)}</span>`:cell;     /* the band sits under the edge when both are on */
  }
  /* SCOUT_PARITY_EV_BAND: Skip below 0 · Marginal 0–3% · Strong 3–6% · Premium 6%+ (the server's band). The letter is unchanged. */
  function bandChip(r) {
    const p=evOf(r);
    if(!p||!p.band)return '';
    return `<span class="h-band h-band-${esc(p.band)}" title="${esc(`${evText(p.ev)} average return against the ${pct(p.be)} break-even of a ${p.entry}. Skip below 0, Marginal 0–3%, Strong 3–6%, Premium 6% or more`)}">${esc(p.band_label)}</span>`;
  }
  /* SCOUT_PARITY_PLAYS: how many current lines each app has on this sport tab (extra books: the shared /book-lines count) */
  function playsCount(id) {
    if(isExtra(id)){const sp=H.sport==='all'?visibleSports():[H.sport],v=sp.map(s=>H.bookLines?.[id]?.[s]).filter(Number.isInteger);return v.length?v.reduce((a,b)=>a+b,0):null;}
    return H.records.filter(r=>r.offer.platform===id&&(H.sport==='all'||r.sport===H.sport)&&!C.started(r)&&!unavailableReason(r)).length;
  }
  function syncPlays() {
    $('hub-platform').querySelectorAll('[data-hub="book"]').forEach(b=>{
      if(b.dataset.id==='all')return;
      if(!b.dataset.base)b.dataset.base=b.getAttribute('aria-label')||b.textContent;
      const n=playsCount(b.dataset.id);let chip=b.querySelector('.h-plays');
      if(n==null){chip?.remove();b.setAttribute('aria-label',b.dataset.base);return;}
      if(!chip){chip=document.createElement('span');chip.className='h-plays';chip.setAttribute('aria-hidden','true');b.appendChild(chip);}
      chip.textContent=String(n);b.setAttribute('aria-label',`${b.dataset.base}, ${n} ${n===1?'play':'plays'}`);
    });
  }
  /* start countdown on the game header row and the card; the ticker below keeps it current between board refreshes */
  function countdown(at) {
    const ms=Date.parse(at)-Date.now();
    if(!Number.isFinite(ms)) return '';
    if(ms<=0) return 'Starting';
    const m=Math.floor(ms/60000), d=Math.floor(m/1440), h=Math.floor(m%1440/60), mm=m%60;
    return 'in '+(d?`${d}d ${h}h`:h?`${h}h ${String(mm).padStart(2,'0')}m`:`${Math.max(1,mm)}m`);
  }
  const countdownHtml = r => `<span class="h-countdown${Date.parse(r.event.start_utc)-Date.now()<=3600000?' soon':''}" data-start="${esc(r.event.start_utc)}">${esc(countdown(r.event.start_utc))}</span>`;
  const openPlayers = new Set();
  /* UI_NEXT: one board row per player + stat; its ladder (every line of that prop, by app) opens in place. Open ladders and the
     By prop / By player choice survive repaints and refreshes (display state only). */
  const openLadders = new Set();
  H.groupBy='prop';
  try{if(localStorage.getItem('scout-board-group')==='player')H.groupBy='player';}catch(e){/* storage blocked */}
  const RANKED=new Set(['chance','line','edge','medge','l5','l10','l20','h2h','season','hit']);
  const PAGE_PROPS=50,PAGE_PLAYERS=25;
  /* by player: the member's choice, or a search that lands on a few players (their props together, grouped by stat) */
  const byPlayer=groups=>H.groupBy==='player'||(!!fold(H.q)&&new Set(groups.map(g=>g.player)).size<=3);
  function boardGroups(list){
    const src=boardSource().filter(r=>!C.started(r)&&!unavailableReason(r)&&(H.sport==='all'||r.sport===H.sport));
    return groupProps(list,src,RANKED.has(H.sortBy));
  }
  /* NBA / NFL faces (audit 2026-11-05): ESPN headshot in a circle with the team logo; esports rows get none */
  const faceOf = (r,size) => C.faceHtml?C.faceHtml(r.sport,{id:r.player.id,key:r.player.key,name:r.player.name,team:r.player.team_abbr||r.player.team,size,cls:'h-face'}):'';
  const kindOf = r => C.lineKind?C.lineKind(r.offer.platform,r.market.line_type):'';
  const boardPlayerKey = r => [r.sport,r.event.id,r.player.key].join('|');
  /* the letter as on classic: gold for A, gold outline for B; a sub-threshold pick keeps its Lean wording; NBA/NFL letters carry no tag (ct-1b, owner 2026-11-08: released) */
  function gradeChip(r) {
    const s=r.scout;
    if(C.oneSided(r)) return `<span class="h-grade-text h-one-sided">${esc(C.oneSided(r))}</span>`;
    if(s.hold) return '<span class="h-grade-text">Research estimate</span>';
    if(s.letter_held&&s.side) return `<span class="h-grade-text">Lean ${sideWord(s.side)}</span><small class="h-grade-tag">0U</small>`;   /* lc-1: held letter */
    if(s.line_chance&&s.side) return '<span class="h-grade-text h-line-chance" title="Scout\u2019s chance at this line">Chance at this line</span>';   /* short on the row (follow-up 2026-11-06: was cut off); the card keeps the full sentence */   /* audit 2026-11-05: another book's line, same saved distribution */
    if(s.grade) return `<span class="h-grade h-grade-${/^A/.test(s.grade)?'a':/^B/.test(s.grade)?'b':'c'}">${esc(s.grade)}</span>`;
    if(s.side) return `<span class="h-grade-text">Lean ${sideWord(s.side)}</span>`;
    return '';
  }
  /* B2 (audit 2026-11-05): Scout's chance on its side is under what the line needs to pay. The side, its Lean wording and its letter
     stay as published; a muted chip says it is below break-even so the row is not read as a play. Break-even comes from the server
     (Underdog standard per-pick, or the parity break-even of the line); no break-even = no chip. It sits under the Edge figure (in
     the Scout cell when the board has no Edge column), so the row keeps its height. */
  function belowBreakEven(r) {
    const s=r.scout;if(!s.side||!num(s.p_side))return null;
    const d=edgeValue(r);
    /* bug #55: the same break-even edgeValue measured against (rated EV, else the row's own book), as edgeCell names it */
    const par=evOf(r)||edgeBe(r);
    if(d!=null)return d<0?{be:par?par.be:COLS?.ud_standard_break_even,entry:par?par.entry:(COLS?.entry||'Underdog standard')}:null;
    const p=parOf(r);
    return p&&s.p_side<p.be?{be:p.be,entry:p.entry||'entry'}:null;
  }
  function belowChip(r) {
    const b=belowBreakEven(r);if(!b)return '';
    const t=`Scout's chance ${pct(r.scout.p_side)} is under the ${num(b.be)?pct(b.be)+' ':''}break-even of a ${b.entry}`;
    return `<small class="h-below" title="${esc(t)}" aria-label="${esc(t)}">Below break‑even</small>`;
  }
  /* ---- SCOUT_MARKET_EDGE: display of the server's numbers only ---- */
  const mxDoc = r => MX?H.mx[r.sport]||null:null;
  const mxRow = r => mxDoc(r)?.rows?.[r.offer.platform+'|'+r.offer.offer_id]||null;
  const mxValue = r => {const v=mxRow(r)?.fav;return v&&num(v.edge)?v.edge*100:null;};
  const ptsText = e => `${e>0?'+':e<0?'−':'±'}${Math.abs(e*100).toFixed(1)}`;
  const MX_WHY = () => `The books’ chance for a side with their margin removed (at least ${MX?.min_books||2} books with a current price), minus what one pick needs to break even (${Object.values(MX?.entries||{}).map(e=>`${e.label} ${pct(e.be)}`).join(', ')}), in points. Shown for the side the books favour, and for Scout’s side too when Scout picks the other one. This is the books’ number, not Scout’s grade.`;
  function mxWhy(r) {
    const x=mxRow(r), d=mxDoc(r);
    if(x?.why)return x.why;
    if(d?.why)return d.why;
    if(r.extra||!['prizepicks','underdog'].includes(r.offer.platform))return 'Market edge is measured on PrizePicks and Underdog lines.';
    return 'No sportsbook prices this line right now.';
  }
  const mxSay = v => `the books give the ${sideWord(v.side)} ${pct(v.p)} (${v.books.join(', ')})${num(v.be)?`; ${/^[AEIOU]/i.test(v.entry)?'an':'a'} ${v.entry} needs ${pct(v.be)} a pick, so ${ptsText(v.edge)} points`:'; no published break-even for this payout'}`;
  function mxTitle(r) {
    const x=mxRow(r);if(!x?.fav)return mxWhy(r);
    const scout=x.scout?` For Scout’s side, ${mxSay(x.scout)}.`:x.scout_why&&r.scout.side?` Scout’s side (${sideWord(r.scout.side)}): ${x.scout_why}`:'';
    return `Market edge: ${mxSay(x.fav)}.${scout} The books’ number, not Scout’s grade.`;
  }
  /* one cell: the favoured side's edge (green only when positive; a negative one is shown in plain text, never hidden) and, when Scout
     picks the other side, that side's edge on its own line */
  function mxCell(r) {
    if(!mxDoc(r))return '<span class="h-medge h-medge-wait" aria-hidden="true"></span>';
    const x=mxRow(r), v=x?.fav;
    if(!v||!num(v.edge)){const w=mxWhy(r);return `<span class="h-medge empty" title="${esc(w)}" aria-label="${esc('Market edge: '+w)}">—</span>`;}
    const t=mxTitle(r), sc=x.scout&&num(x.scout.edge)?x.scout:null;
    return `<span class="h-medge-wrap" title="${esc(t)}" aria-label="${esc(t)}"><span class="h-medge${v.edge>0?' positive':''}"><b>${ptsText(v.edge)}</b><small>pts</small></span><small class="h-medge-side">${sideWord(v.side)}</small>${sc?`<small class="h-medge-side h-medge-scout${sc.edge>0?' positive':''}">Scout ${sc.side==='over'?'↗':'↘'}${ptsText(sc.edge)}</small>`:''}</span>`;
  }
  /* wide desktop: the Edge column holds both numbers, each labelled (Scout's edge as before, then the books' edge with an arrow for its
     side), so the board keeps the player's name; the separate Market edge column is used where the Edge column steps aside
     (1101-1280px) and on the phone card's own line */
  function booksLine(r) {
    if(!mxDoc(r))return '<span class="h-edge-books" aria-hidden="true"></span>';
    const v=mxRow(r)?.fav;
    if(!v||!num(v.edge)){const w=mxWhy(r);return `<span class="h-edge-books empty" title="${esc(w)}" aria-label="${esc('Market edge: '+w)}"><i>Books</i>—</span>`;}
    const t=mxTitle(r);
    return `<span class="h-edge-books" title="${esc(t)}" aria-label="${esc(t)}"><i>Books</i><b class="h-medge${v.edge>0?' positive':''}">${v.side==='over'?'↗':'↘'}${ptsText(v.edge)}</b></span>`;
  }
  /* best line and price per side across every book (Over: the lowest line, then the payout needing the lowest chance; Under: the highest) */
  const propKeyOf = r => [r.event.id,r.player.key,r.market.stat,r.market.period].join('|');
  const bestOf = r => mxDoc(r)?.props?.[propKeyOf(r)]||null;
  const bestText = (x,sd) => `${sd==='over'?'O':'U'}\u2009${x.line} ${x.short}${x.price?'\u2009'+x.price:''}`;   /* thin spaces: the price fits the 90px button */
  const bestSay = (x,sd) => `Best ${sideWord(sd)}: ${x.label} ${x.line}${x.price?` at ${x.price}`:''}${num(x.needs)?` (needs ${pct(x.needs)} a pick)`:''}`;
  /* line discrepancy (owner 2026-10-19, CS2 chip ca84528, now every sport): another book's standard line for the same player, stat and
     game at least gap_min away from this line. Facts only: which line is easier depends on the side; payouts can differ. */
  function gapOf(r) {
    /* on the pick'em lines a member enters (PrizePicks, Underdog, Sleeper, Dabble), as the CS2 chip; never on an exchange's ladder rung */
    const pickem=['prizepicks','underdog'].includes(r.offer.platform)||r.extra?.kind==='pickem';
    const b=bestOf(r), L=Number(r.offer.line);if(!pickem||!b||r.market.line_type!=='standard'||!Number.isFinite(L))return null;
    let far=null;
    for(const [label,line,book] of b.ls||[]){if(book===r.offer.platform)continue;const d=Number(line)-L;
      const app=['prizepicks','underdog'].includes(book);   /* at an equal distance, the other pick'em app first (as the CS2 chip) */
      if(Math.abs(d)>=(MX.gap_min||1)-1e-9&&(!far||Math.abs(d)>Math.abs(far.d)+1e-9||(Math.abs(d)>Math.abs(far.d)-1e-9&&app&&!far.app)))far={label,line,book,d,app};}
    return far?{...far,all:b.ls}:null;
  }
  function gapChip(r) {
    const g=gapOf(r);if(!g)return '';
    const sd=r.scout.side, easier=sd?` For the ${sideWord(sd)}, the ${sd==='over'?'lower':'higher'} line is the easier one; payouts can differ.`:' For an Over the lower line is the easier one, for an Under the higher one; payouts can differ.';
    const tip=`Line discrepancy: ${g.label} lists ${g.line} for the same player, stat and ${['cs2','lol'].includes(r.sport)?'maps':'game'}, ${+Math.abs(g.d).toFixed(1)} ${g.d>0?'higher':'lower'} than this line. Lines now: ${g.all.map(x=>x[0]+' '+x[1]).join(' · ')}.${easier}`;
    return `<span class="h-gap" title="${esc(tip)}" aria-label="${esc(tip)}" tabindex="0">≠ ${esc(g.label)} ${esc(g.line)}</span>`;
  }
  /* the board's market-edge doc per sport, read with the board (one shared server build per sport every 30 s); a failed read keeps
     the last good copy (transient-failure rule) */
  async function loadMarketEdge(sports) {
    if(!MX)return;
    const res=await Promise.allSettled(sports.map(s=>api('market-edge?sport='+encodeURIComponent(s)).then(d=>[s,d])));
    let got=false;
    for(const x of res)if(x.status==='fulfilled'&&x.value[1]&&typeof x.value[1]==='object'){H.mx[x.value[0]]=x.value[1];got=true;}
    if(got){if(H.section==='board')paintBoard();if(cardOpen()&&H.cardTab==='lines')paintDetail();}
  }
  /* ONE Scout cell: side, chance and the letter (or Lean Over/Under) together. No published side = both chances, no side shown */
  function scoutCell(r) {
    const s=r.scout;
    /* an extra book's line with no saved chance: the cell stays empty (no status sentence, owner rule 2026-10-16); with both chances
       it reads like any other row without a side */
    if(r.extra?.reference_only)return `<div class="h-scout"><b>${esc(reportedOdds(r)||'Odds not supplied')}</b><small>Reported odds / side not supplied</small></div>`;
    if(r.extra&&!s.side&&!(num(s.p_over)&&num(s.p_under)))return '';
    if(C.oneSided(r)) return `<div class="h-scout"><div class="h-direction-options" title="${esc(C.oneSidedNote(r))}"><span class="h-one-sided">${esc(C.oneSided(r))}</span></div></div>`;
    if(s.side) return `<div class="h-scout"><div class="h-direction ${s.side}"><b>${s.side==='over'?'↗ Over':'↘ Under'}</b><span>${pct(s.p_side)}</span></div><div class="h-scout-grade">${gradeChip(r)}${P('ev_band')&&!(P('edge')&&H.showEdge)?bandChip(r):''}</div>${H.showEdge?(NEXT?belowChip(r).replace('class="h-below"','class="h-below h-below-alt"'):''):belowChip(r)}</div>`;   /* UI_NEXT: where the redesign hides the Edge column (phone, card open, narrow desktop) the Scout cell carries the same chip; CSS shows one of the two */
    const both=num(s.p_over)&&num(s.p_under);
    return `<div class="h-scout"><div class="h-direction-options" aria-label="Model probabilities for both sides">${both?`<span>Over <b>${pct(s.p_over)}</b></span><span>Under <b>${pct(s.p_under)}</b></span>`:'<span>Forecast pending</span>'}</div>${s.hold?'<div class="h-scout-grade"><span class="h-grade-text">Research estimate</span></div>':''}</div>`;
  }
  const reportedOdds = r => (r.offer.reported_prices||[]).map(n=>n>0?'+'+n:String(n)).join(' / ');
  function priceSummary(q) {
    if(q.extra?.reference_only)return reportedOdds(q)||'Odds not supplied';
    const o=C.priceText(q,'over'), u=C.priceText(q,'under');
    if(o||u) return [o&&'O '+o,u&&'U '+u].filter(Boolean).join(' · ');
    return C.lineLabel(q)+' line';
  }
  /* the real alternatives for this prop (other books, other lines) as one small button; the detail is in its tooltip and the card's Lines tab */
  function compareCell(r) {
    const all=C.siblings(r), others=all.filter(x=>key(x)!==key(r));
    const lines=[...new Set(all.map(x=>x.offer.line))].sort((a,b)=>a-b), books=new Set(all.map(x=>x.offer.platform));
    let main, sub, full;
    if(!others.length) { main=`${BOOK_SHORT[r.offer.platform]||r.offer.platform_label} only`; sub=priceSummary(r); full=`${r.offer.platform_label} only · ${sub}`; }
    else {
      main=lines.length>1?`${lines.length} lines`:`${books.size} books`;
      sub=lines.length>1?`${lines[0]}–${lines[lines.length-1]}`:`at ${lines[0]}`;
      full=`${main} · ${sub} · `+others.map(x=>`${x.offer.platform_label} ${x.offer.line}${x.market.line_type!=='standard'?' '+C.lineLabel(x):''}`).join(' · ');
    }
    /* SCOUT_MARKET_EDGE: the best Over and the best Under across every book (sportsbooks and extra books too) replace the line range,
       which they contain (lowest line for the Over, highest for the Under); the count names every book that lists the prop */
    const b=bestOf(r);
    if(b&&(b.over||b.under)){
      if(b.n>books.size)main=`${b.n} books`;
      const best=['over','under'].filter(sd=>b[sd]);
      full=best.map(sd=>bestSay(b[sd],sd)).join(' · ')+' · '+full;
      return `<button class="h-compare h-compare-best${different(r)?' differ':''}" data-hub="books" data-id="${esc(key(r))}" title="${esc(full)}" aria-label="Compare books and lines for ${esc(r.player.name)}: ${esc(full)}"><b>${esc(main)}</b>${best.map(sd=>`<small class="h-best h-best-${sd}">${esc(bestText(b[sd],sd))}</small>`).join('')}</button>`;
    }
    return `<button class="h-compare${different(r)?' differ':''}" data-hub="books" data-id="${esc(key(r))}" title="${esc(full)}" aria-label="Compare books and lines for ${esc(r.player.name)}: ${esc(full)}"><b>${esc(main)}</b><small>${esc(sub)}</small></button>`;
  }
  /* owner 2026-11-08 ("i dont see players roles"): the row's second line opens with the player's role, the same text as the card's
     tag (r.player.role, every sport); no role, no tag. It sits before the matchup, which is cut first, so the name keeps its width. */
  const rowRole = r => r.player.role?`<em class="h-role h-row-role">${esc(r.player.role)}</em>`:'';
  function row(r, group=null, g=null) {
    const k=esc(key(r)), s=r.scout, side=s.side, on=C.onSlip(r), watched=H.watch.includes(playerKey(r)), open=H.expanded===key(r)||H.detail===key(r)||(!!g&&g.lines.some(x=>key(x)===H.detail));
    const choose=!side||!r.offer[side]?.offered;
    const unavailable=!!unavailableReason(r);
    const bk=boardPlayerKey(r), propsOpen=openPlayers.has(bk), more=group?.length>1?group.length-1:0;
    const toggle=g?'':more?`<button class="h-prop-toggle" data-hub="player-props" data-id="${esc(bk)}" aria-expanded="${propsOpen}" aria-label="${propsOpen?'Hide':'Show'} ${more} more ${more===1?'prop':'props'} for ${esc(r.player.name)}" title="${esc(group.slice(1).map(x=>x.market.stat_label+' '+x.offer.line+' · '+x.market.period_label).join('\n'))}">${propsOpen?'−':'+'}${more}</button>`:'';
    // the matchup sits on the game header row; without headers (another sort) the row names the opponent itself
    const vs=H.sortBy!=='start'&&r.player.opponent?` vs ${r.player.opponent}`:'', name=esc(r.player.name);
    const add=on?['On slip ✓',`On slip: show ${name} in the slip`]:choose?['Side',`Choose a side for ${name}`]:['+ Add',`Add ${name} ${sideWord(side)} ${esc(r.offer.line)} to slip`];
    return `<tr data-record="${k}" data-hub="expand" data-id="${k}" class="h-row${open?' is-expanded':''}${group||(g&&!g.inPlayer)?'':' is-sub'}${noHits(r)?' h-nohits':''}${g&&openLadders.has(g.key)?' has-ladder':''}"${g?` data-prop="${esc(g.key)}"`:''}>
      <td class="h-c-watch"><button class="h-star" data-hub="watch" data-id="${k}" aria-label="Watch ${name}" aria-pressed="${watched}">${watched?'★':'☆'}</button></td>
      <td class="h-c-player">${faceOf(r,32)}<button class="h-player" data-hub="expand" data-id="${k}" aria-haspopup="dialog" aria-expanded="${open}"><b>${name}</b><small>${rowRole(r)}<span class="h-vs">${esc(r.player.team)}${esc(vs)}</span><i>${SPORTS[r.sport]}</i></small></button></td>
      <td class="h-c-market"><div class="h-mcell"><div><b title="${esc(r.market.stat_label)}">${esc(r.market.stat_label)}</b><small>${esc(r.market.period_label)}</small>${MX?gapChip(r):''}</div>${toggle}</div></td>
      <td class="h-c-line h-number"><b>${esc(r.offer.line)}${kindOf(r)?`<em class="u-lt u-lt-${kindOf(r).toLowerCase()}" title="PrizePicks ${kindOf(r).toLowerCase()} line">${kindOf(r)}</em>`:''}</b><small title="${esc(`${r.offer.platform_label}${kindOf(r)?' '+kindOf(r):''} · Scout last read this line ${r.offer.seen_at||'at an unavailable time'}`)}">${esc(BOOK_SHORT[r.offer.platform]||r.offer.platform_label)} · ${esc(age(r.offer.seen_at).replace(' ago','').replace('Just now','now').replace('Time unavailable','—'))}${r.offer.moved_from!=null?(NEXT?`<span class="h-was" title="The line moved from ${esc(r.offer.moved_from)} to ${esc(r.offer.line)}">was ${esc(r.offer.moved_from)}</span>`:` · was ${esc(r.offer.moved_from)}`):''}</small></td>
      <td class="h-c-proj h-number">${num(s.projection)?esc(s.projection.toFixed(1)):'—'}</td>
      <td class="h-c-scout">${scoutCell(r)}</td>
      <td class="h-c-hits">${stripHtml(r)}</td>${H.showEdge?`<td class="h-c-edge h-number">${edgeCell(r)}${belowChip(r)}${MX?booksLine(r):''}</td>`:''}${MX?`<td class="h-c-medge h-number${mxValue(r)==null?' is-empty':''}">${mxCell(r)}</td>`:''}
      <td class="h-c-compare">${g?linesCell(g):compareCell(r)}</td>
      <td class="h-c-add">${C.oneSided(r)&&!r.extra?`<button class="h-add h-add-info" data-hub="add" data-id="${k}" aria-label="Open ${name}'s research. ${esc(C.oneSidedNote(r))}" title="${esc(C.oneSidedNote(r))}">Info</button>`:r.extra?`<button class="h-add h-add-info" data-hub="add" data-id="${k}" aria-label="Open ${name}'s research for the ${esc(r.offer.platform_label)} line" title="Research for this line. View only: slips are PrizePicks or Underdog entries">Info</button>`:`<button class="h-add${on?' is-on':choose?' selected':''}" data-hub="add" data-id="${k}" aria-label="${add[1]}" ${unavailable?'disabled':''}>${add[0]}</button>`}</td></tr>`;
  }
  /* UI_NEXT: the row's Lines cell. One line = the app's name; more = a button that opens the ladder under the row, with the number
     of lines and apps and, when a filter or search is on, how many of them it matched (a grouped row never hides a matched line). */
  const filtering=()=>activeFilters()>0||H.book!=='all'||!!fold(H.q);
  function linesCell(g) {
    const r=g.main,open=openLadders.has(g.key),n=g.total;
    if(n<2)return `<span class="h-lines-one">${esc(r.offer.platform_label)}</span>`;
    const part=filtering()&&g.matched.length<n?`${g.matched.length} of ${n} match`:'';
    const range=g.values>1?`${g.lines[0].offer.line}–${g.lines[n-1].offer.line}`:`at ${r.offer.line}`;
    const say=`${open?'Hide':'Show'} all ${n} lines for ${r.player.name} ${r.market.stat_label}, ${g.books} ${g.books===1?'app':'apps'}, ${range}${part?', '+part:''}`;
    return `<button class="h-lines-btn" data-hub="ladder" data-id="${esc(g.key)}" aria-expanded="${open}" aria-label="${esc(say)}"><b>${open?'−':'+'}${n-1} ${n-1===1?'line':'lines'}</b><small>${g.books} ${g.books===1?'app':'apps'} · ${esc(range)}</small>${part?`<em>${esc(part)}</em>`:''}</button>`;
  }
  /* the ladder: every line of the prop by line then app, each with its prices and Scout's side, chance and letter for that line (the
     server's own numbers for that record); the best line for the row's Scout side is marked; a line adds to the slip from here */
  function ladderScout(q) {
    const s=q.scout;
    if(C.oneSided(q))return `<span class="h-ld-none">${esc(C.oneSided(q))}</span>`;
    if(s.side)return `<span class="h-ld-side ${s.side}">${s.side==='over'?'▲':'▼'} ${sideWord(s.side)}</span><b class="h-ld-p">${pct(s.p_side)}</b>`;
    if(num(s.p_over)&&num(s.p_under))return `<span class="h-ld-both">O ${pct(s.p_over)} · U ${pct(s.p_under)}</span>`;
    return '';
  }
  function ladderHtml(g,cols) {
    const side=g.main.scout.side,dim=filtering()&&g.matched.length<g.total;
    const price=(q,sd)=>!q.offer[sd]?.offered?'<span class="h-ld-off">—</span>':esc(C.priceText(q,sd)||(q.market.line_type==='standard'?'Std':C.lineLabel(q)));
    const act=q=>{const k=esc(key(q)),on=C.onSlip(q),nm=esc(q.player.name);
      if(q.extra||C.oneSided(q)||!['prizepicks','underdog'].includes(q.offer.platform))return `<button class="h-ld-act" data-hub="expand" data-id="${k}" aria-label="Open ${nm} ${esc(q.offer.platform_label)} ${esc(q.offer.line)}">Card</button>`;
      const choose=!q.scout.side||!q.offer[q.scout.side]?.offered;
      return `<button class="h-ld-act${on?' is-on':''}" data-hub="add" data-id="${k}" aria-label="${on?`On slip: ${nm} ${esc(q.offer.platform_label)} ${esc(q.offer.line)}`:choose?`Choose a side for ${nm} ${esc(q.offer.platform_label)} ${esc(q.offer.line)}`:`Add ${nm} ${sideWord(q.scout.side)} ${esc(q.offer.line)} on ${esc(q.offer.platform_label)} to slip`}">${on?'On slip':choose?'Side':'+ Add'}</button>`;};
    const body=g.lines.map(q=>{const k=key(q),kind=kindOf(q)||(q.market.line_type!=='standard'?C.lineLabel(q):''),hit=g.match.has(lineId(q));
      return `<tr class="${[k===key(g.main)?'is-main':'',dim&&!hit?'is-dim':'',dim&&hit?'is-match':'',H.detail===k?'is-open':''].filter(Boolean).join(' ')}" data-line="${esc(k)}">
        <th scope="row"><button class="h-ld-book" data-hub="expand" data-id="${esc(k)}" aria-label="Open the card for ${esc(q.offer.platform_label)} ${esc(q.offer.line)}">${esc(q.offer.platform_label)}${kind?` <em class="u-lt u-lt-${esc(String(kind).toLowerCase())}">${esc(kind)}</em>`:''}</button></th>
        <td class="h-ld-line"><b>${esc(q.offer.line)}</b>${g.best&&key(g.best)===k?`<em class="h-ld-best">Best ${sideWord(side)}</em>`:''}${q.offer.moved_from!=null?`<small>was ${esc(q.offer.moved_from)}</small>`:''}</td>
        <td class="h-ld-px">${price(q,'over')}</td><td class="h-ld-px">${price(q,'under')}</td>
        <td class="h-ld-scout">${ladderScout(q)}<span class="h-ld-grade">${q.scout.side&&!q.scout.hold&&!q.scout.line_chance&&!C.oneSided(q)?gradeChip(q):''}</span></td>
        <td class="h-ld-do">${act(q)}</td></tr>`;}).join('');
    return `<tr class="h-ladder-row h-span-row" data-ladder="${esc(g.key)}"><td colspan="${cols}"><div class="h-ladder"><table class="h-ladder-table" aria-label="${esc(`All ${g.total} lines for ${g.main.player.name} ${g.main.market.stat_label}`)}"><thead><tr><th scope="col">App</th><th scope="col">Line</th><th scope="col">Over</th><th scope="col">Under</th><th scope="col">Scout</th><th scope="col"><span class="u-sr">Slip</span></th></tr></thead><tbody>${body}</tbody></table>${dim?`<p class="h-ladder-note">${g.matched.length} of ${g.total} lines match the filters; the rest are listed dimmed.</p>`:''}</div></td></tr>`;
  }
  /* by player: one header per player (sticky while their props scroll), then their props by stat */
  function playerRow(p,cols) {
    const r=p.first,watched=H.watch.includes(playerKey(r));
    return `<tr class="h-player-row h-span-row" data-player="${esc(p.key)}"><td colspan="${cols}"><div class="h-player-head">${faceOf(r,24)}<b>${esc(r.player.name)}</b>${rowRole(r)}<span>${esc(r.player.team)}${r.player.opponent?` vs ${esc(r.player.opponent)}`:''} · ${SPORTS[r.sport]}</span><small>${plural(p.props.length,'prop')} · ${plural(p.lines,'line')}</small><button class="h-star" data-hub="watch" data-id="${esc(key(r))}" aria-label="Watch ${esc(r.player.name)}" aria-pressed="${watched}">${watched?'★':'☆'}</button></div></td></tr>`;
  }
  function paintRowsNext(list,cols,headers) {
    const groups=boardGroups(list),players=byPlayer(groups),units=players?groupPlayers(groups):groups,size=players?PAGE_PLAYERS:PAGE_PROPS,pages=Math.max(1,Math.ceil(units.length/size));
    H.page=Math.min(H.page,pages-1);
    const page=units.slice(H.page*size,(H.page+1)*size);
    let last=null;
    const prop=(g,inPlayer)=>{g.inPlayer=inPlayer;return row(g.main,null,g)+(openLadders.has(g.key)&&g.total>1?ladderHtml(g,cols):'');};
    $('hub-rows').innerHTML=page.map(u=>{const r=players?u.first:u.main,gk=gameKey(r),head=headers&&gk!==last?gameRow(r,list,cols):'';last=gk;
      return head+(players?playerRow(u,cols)+u.props.map(g=>prop(g,true)).join(''):prop(u,false));}).join('');
    $('hub-table').classList.toggle('h-by-player',players);
    const nPlayers=new Set(groups.map(g=>g.player)).size;
    $('hub-count').textContent=`${plural(nPlayers,'player')} · ${plural(groups.length,'prop')} · ${plural(list.length,'line')}`;
    $('hub-board-section').querySelectorAll('[data-hub="group-by"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===(players?'player':'prop'))));
    return {n:units.length,size,pages,word:players?'players':'props'};
  }
  /* the game header row: matchup, sport, start and countdown once per game; a click shows only that game (and back) */
  function gameRow(r,list,cols) {
    const g=gameKey(r), on=H.game===g, books=[...new Set(list.filter(x=>gameKey(x)===g).map(x=>BOOK_SHORT[x.offer.platform]||x.offer.platform_label))].join(', ');
    return `<tr class="h-game-row" data-game="${esc(g)}"><td colspan="${cols}"><button class="h-game-head" data-hub="game" data-id="${esc(g)}" aria-pressed="${on}" aria-label="${esc((on?'Show all games, now showing ':'Show only ')+r.event.label)}"><b class="h-match">${esc(r.event.label)}</b><span>${eventTag(r)} · ${esc(when(r.event.start_utc))}</span>${countdownHtml(r)}<small>${esc(books)}</small><em>${on?'All games ✕':'This game →'}</em></button></td></tr>`;
  }
  /* each sortable header is a button: a click sorts by it, a second click turns the order around; the arrow is drawn by CSS from
     data-sort, so the header text stays the column's name */
  const sortState = k => H.sortBy===k?(H.sortDir==='asc'?'ascending':'descending'):'none';
  const sortBtn = (k,label,extra='') => `<button class="h-th-sort" data-hub="sort-col" data-id="${k}" data-sort="${sortState(k)}" aria-label="Sort by ${esc(sortName(k))}${H.sortBy===k?`, now ${H.sortDir==='asc'?'low to high':'high to low'}`:''}"><span class="h-th-l">${label}</span>${extra}</button>`;
  function headHtml(showEdge) {
    return `<tr><th class="h-c-watch"><span class="u-sr">Watch</span></th><th class="h-c-player" aria-sort="${sortState('player')}">${sortBtn('player','Player')}</th><th class="h-c-market">Market</th><th class="h-c-line" aria-sort="${sortState('line')}">${sortBtn('line','Line')}</th><th class="h-c-proj">Proj.</th><th class="h-c-scout" aria-sort="${sortState('chance')}">${H.book==='draftkings'?'Reported odds':sortBtn('chance','Scout')}</th>`
      +`<th class="h-c-hits"><span class="h-strip h-strip-head">${STRIP.map(([w,l])=>`<i class="h-strip-th" title="${esc(STRIP_TITLE[w])}">${sortBtn(w,l)}</i>`).join('')}</span></th>`
      +(showEdge?`<th class="h-c-edge" title="${esc(EDGE_WHY())}" aria-sort="${sortState('edge')}">${sortBtn('edge','Edge',' <small>vs break-even</small>')}</th>`:'')
      +(MX?`<th class="h-c-medge" title="${esc(MX_WHY())}" aria-sort="${sortState('medge')}">${sortBtn('medge','Market edge',' <small>books vs break-even</small>')}</th>`:'')
      +`<th class="h-c-compare">${NEXT?'Lines':'Compare'}</th>`+'<th class="h-c-add"><span class="u-sr">Add to slip</span></th></tr>';
  }
  /* audit 2026-11-04: one plain banner when a source feeding the visible sport(s) is behind: its feed is not current, or its newest
     offer was seen more than 2 hours ago. It names the source and how long since it updated; its lines stay off the board. */
  const SOURCE_MAX_MS=7200000;
  function sourcesHtml() {
    const out=[];
    for(const s of visibleSports()) {
      const b=R.boards[s];if(!b?.at)continue;
      if(b.error){out.push(`${SPORTS[s]}: the last refresh did not complete`);continue;}
      for(const [p,f] of Object.entries(b.feeds||{})) {
        const recs=[...b.recs.values()].filter(r=>r.offer.platform===p), newest=Math.max(0,...recs.map(r=>Date.parse(r.offer.seen_at)||0));
        const old=newest>0&&Date.now()-newest>SOURCE_MAX_MS;
        if((f?.state||'ok')==='ok'&&!old)continue;
        const at=Date.parse(f?.at)||newest||0, label=C.PLATFORMS[p]?.label||p;
        out.push(`${SPORTS[s]} · ${label} ${at?'last updated '+age(new Date(at).toISOString()).toLowerCase():'has sent no update'}`);
      }
    }
    return out.length?`<p><b>Source updates:</b> ${out.map(esc).join(' · ')}. <button data-hub="refresh">Refresh</button></p>`:'';
  }
  /* an empty board says only what the member can act on: how many current lines the filters hide (with Clear filters), or that no
     current line exists (with Refresh). Owner rule: a failed, stale or held line is left off, never labelled. */
  function emptyReasons() {
    const f=boardFilter(), hidden=currentLines().filter(r=>(H.sport==='all'||r.sport===H.sport)&&!matches(r,f)).length;
    if(hidden)return `<b class="h-empty-title">${hidden.toLocaleString()} ${hidden===1?'line':'lines'} hidden by filters</b><button data-hub="clear-filters">Clear filters</button>`;
    return '<b class="h-empty-title">No current lines</b><button data-hub="refresh">Refresh</button>';
  }
  function installCols() {
    if(COLS)$('u-hub').classList.add('h-cols');
    setInterval(()=>{if(!isOpen()||document.hidden)return;document.querySelectorAll('#u-hub .h-countdown[data-start]').forEach(el=>{const t=countdown(el.dataset.start);if(el.textContent!==t)el.textContent=t;el.classList.toggle('soon',Date.parse(el.dataset.start)-Date.now()<=3600000);});},30000);
  }
  function paintBoard() {
    if(!isOpen()||H.section!=='board') return;
    const restoreFocus=focusAfter($('hub-board-section'));
    syncFields();
    const list=rows(), grouped=new Map();
    /* the default sort fell back to start time: the menu names the order on screen (the saved choice is unchanged) */
    if(H.sortFallback&&!H.sortChosen)$('hub-sort').value='start';
    for(const r of list){const k=boardPlayerKey(r);if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push(r);}
    const players=[...grouped.values()],size=40,pages=Math.max(1,Math.ceil(players.length/size));
    H.page=Math.min(H.page,pages-1);
    const page=players.slice(H.page*size,(H.page+1)*size);
    H.showEdge=HAS_EDGE;   /* B5: the same columns on every sport tab; a line with no published per-pick payout shows — */
    $('hub-table').tHead.innerHTML=headHtml(H.showEdge);$('hub-table').classList.toggle('h-has-edge',H.showEdge);$('hub-table').classList.toggle('h-has-medge',!!MX);
    const cols=9+(H.showEdge?1:0)+(MX?1:0), headers=H.sortBy==='start';
    /* the default Edge sort falls back to start time quietly; a sort the member chose says why it fell back */
    $('hub-sort-note').textContent=H.sortFallback&&H.sortChosen?(H.sort==='hit'?'No line on screen has a hit rate and a published payout, so the board is sorted by start time.':H.sort==='edge'?'No line on screen has an edge, so the board is sorted by start time.':H.sort==='medge'?'No line on screen has a market edge, so the board is sorted by start time.':`No line on screen has ${sortName(H.sort).replace(' hit rate','')} results, so the board is sorted by start time.`)
      :H.sortBy==='hit'?'Lines whose hit rate is below their break-even are listed last.':'';
    let last=null,nx=null;
    if(NEXT)nx=paintRowsNext(list,cols,headers);
    else{$('hub-rows').innerHTML=page.map(group=>{const r=group[0],g=gameKey(r),head=headers&&g!==last?gameRow(r,list,cols):'';last=g;
      return head+row(r,group)+(openPlayers.has(boardPlayerKey(r))?group.slice(1).map(x=>row(x)).join(''):'');}).join('');
    $('hub-count').textContent=`${players.length.toLocaleString()} ${players.length===1?'player':'players'} · ${list.length.toLocaleString()} ${list.length===1?'line':'lines'}`;}
    $('hub-clear').hidden=!activeFilters()&&H.book==='all'&&!H.q;
    $('hub-chips').innerHTML=filterChips();
    $('hub-sources').innerHTML=H.loading?$('hub-sources').innerHTML:sourcesHtml();
    /* lc-1 (SCOUT_LETTER_CONTAINMENT, off unless the owner turns it on): one plain-fact banner per visible sport whose letters
       the server holds; those rows read Lean Over/Under with the chance, no letter, 0U */
    const heldSports=[...new Set(list.filter(r=>r.scout.letter_held).map(r=>r.sport))];
    // the Sports grades note belongs to the NBA and NFL boards only
    $('hub-health').innerHTML=(isExtra(H.book)?extraBanner():'')+heldSports.map(sp=>`<p class="h-letter-hold" role="status">${esc(SPORTS[sp]||sp.toUpperCase())} letters are held until stated chances match results.</p>`).join('')+(['nba','nfl'].includes(H.sport)&&list.some(r=>r.scout.validation?.kind==='research')?`<details><summary>About ${esc(SPORTS[H.sport])} grades</summary><p>Published letters rank the model’s current picks. Stakes are 0. See Record for stated chances and actual results.</p></details>`:'');
    const games=H.view==='games';
    $('hub-board-section').classList.toggle('h-by-game',games);
    $('hub-board-section').querySelectorAll('[data-hub="view"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===(games?'games':'list'))));
    $('hub-games-body').hidden=!games;
    if(games)paintGames(list);
    $('hub-empty').hidden=games||list.length>0;
    if(H.loading)$('hub-empty').textContent='Loading the available boards…';
    else if(!list.length)$('hub-empty').innerHTML=emptyReasons();
    if(nx)$('hub-pagination').innerHTML=games||!nx.n?'':`<span>${H.page*nx.size+1}–${Math.min((H.page+1)*nx.size,nx.n)} of ${nx.n.toLocaleString()} ${nx.word}</span><button data-hub="page" data-id="-1" ${H.page===0?'disabled':''}>Previous</button><button data-hub="page" data-id="1" ${H.page===nx.pages-1?'disabled':''}>Next</button>`;
    else $('hub-pagination').innerHTML=games||!players.length?'':`<span>${H.page*size+1}–${Math.min((H.page+1)*size,players.length)} of ${players.length} players</span><button data-hub="page" data-id="-1" ${H.page===0?'disabled':''}>Previous</button><button data-hub="page" data-id="1" ${H.page===pages-1?'disabled':''}>Next</button>`;
    /* QOL 2026-11-07: with nothing saved the menu says so and looks disabled (it offered "Choose a saved view" with nothing to choose) */
    $('hub-views').innerHTML=`<option value="">${H.views.length?'Choose a saved view':'No saved views yet'}</option>`+H.views.map((v,i)=>`<option value="${i}">${esc(v.name)}</option>`).join('');
    $('hub-views').disabled=!H.views.length;
    paintStrip();
    fitGameRows();
    keepBoard();
    restoreFocus();
  }
  /* 2026-11-08: a game header row spans only the columns on screen. The desktop hides Edge (1101-1280px), Compare (1101-1180px) or
     Market edge (1281px+ with Edge); counted in the header row's colspan, a hidden column became an empty extra column that took the
     player column's width (on a start-time board names and role tags were cut, at 1101-1280px hidden). Display only; refit on resize. */
  function fitGameRows() {
    const head=$('hub-table')?.tHead?.rows[0], rows=document.querySelectorAll('#hub-rows tr.h-game-row>td,#hub-rows tr.h-span-row>td');if(!head||!rows.length)return;
    const n=[...head.cells].filter(c=>getComputedStyle(c).display!=='none').length||1;
    rows.forEach(td=>{if(td.colSpan!==n)td.colSpan=n;});
  }
  window.addEventListener('resize',fitGameRows);
  /* QOL 2026-11-07: the toolbar's search, platform and filters survive a reload or a trip back to this tab (this browser tab only,
     sessionStorage; a new visit starts clean). The sport is already in the address; sort has its own saved choice. Display state only. */
  const BOARD_SS=()=>'scout-board-state:'+(R.me?.member||'me');
  const BOARD_KEEP=['q','book','side','minGrade','onlyDiff','watched','freshOnly','soonOnly','movedOnly'];
  function keepBoard(){try{sessionStorage.setItem(BOARD_SS(),JSON.stringify(Object.fromEntries(BOARD_KEEP.map(k=>[k,H[k]]))));}catch(e){/* private mode: nothing kept */}}
  function restoreBoard(){
    let v=null;try{v=JSON.parse(sessionStorage.getItem(BOARD_SS())||'null');}catch(e){return;}
    if(!v||typeof v!=='object')return;
    if(typeof v.q==='string')H.q=v.q.slice(0,100);
    if(['all','prizepicks','underdog',...(EXTRA||[]).map(x=>x.id)].includes(v.book))H.book=v.book;
    if(['all','over','under'].includes(v.side))H.side=v.side;
    if(v.minGrade==='all'||gradeOrder.includes(v.minGrade))H.minGrade=v.minGrade;
    for(const k of ['onlyDiff','watched',...(V2?['freshOnly','soonOnly','movedOnly']:[])])if(typeof v[k]==='boolean')H[k]=v[k];
  }
  /* N12 (site audit 2026-11-06): "Show this game's props" (By game) or a game header limits the board to one game; a chip by the
     count names that game and removes the limit (the Filters badge alone did not say which filter was on) */
  function gameChip() {
    if(!H.game||H.game==='all')return '';
    const r=H.records.find(x=>gameKey(x)===H.game)||H.extraRecs.find(x=>gameKey(x)===H.game), label=r?r.event.label:'One game';
    return `<button class="h-chip" data-hub="game-clear" aria-label="${esc('Game: '+label+'. Remove this filter')}" title="Show all games"><span>Game: ${esc(label)}</span><b aria-hidden="true">✕</b></button>`;
  }
  function filterChips() {
    const chips=[];
    const add=(id,label)=>chips.push(`<button class="h-chip" data-hub="filter-remove" data-id="${id}" aria-label="${esc('Remove '+label+' filter')}"><span>${esc(label)}</span><b aria-hidden="true">×</b></button>`);
    if(H.q)add('q','Search: '+H.q);
    if(H.book!=='all')add('book',C.PLATFORMS[H.book]?.label||EXTRA?.find(x=>x.id===H.book)?.label||H.book);
    if(H.stat!=='all')add('stat',$('hub-stat').selectedOptions[0]?.textContent||H.stat);
    if(H.side!=='all')add('side',H.side==='over'?'Over':'Under');
    if(H.minGrade!=='all')add('minGrade',H.minGrade+' or better');
    for(const [id,label] of [['onlyDiff','Different lines'],['watched','Watchlist'],['freshOnly','Fresh only'],['soonOnly','Starting soon'],['movedOnly','Moved lines']])if(H[id])add(id,label);
    return gameChip()+chips.join('');
  }
  function extraBanner() {
    const x=H.extraState,p=EXTRA.find(e=>e.id===H.book),label=esc(p?.label||H.book);
    if(!x||x.book!==H.book||x.state==='loading')return `<p class="h-extra-state" role="status">Reading ${label} lines…</p>`;
    const msg={stale:`${label} lines are older than their freshness limit, so they are hidden until the next good read.`,
      starting:`${label} lines are being read for the first time. Check again in a minute.`,
      failing:`${label} lines could not be read just now. Scout retries on its own schedule.`,error:`${label} lines could not be loaded. Use Refresh to try again.`,
      backoff:`${label} asked Scout to slow down. Lines return after its retry time.`,empty:`${label} lists no lines for these sports right now.`,
      not_offered:`${label} lists no lines for this sport.`}[x.state];
    if(msg)return `<p class="h-extra-state h-warning" role="status">${msg}</p>`;
    if(H.book==='draftkings')return `<p class="h-extra-state">DraftKings player lines via ESPN / reported American odds. The feed does not identify the price's side; research only.</p>`;
    const r=p?.rules||{},src=(r.sources||[]).map(s=>/^https:\/\//.test(s.url)?`<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url.replace(/^https:\/\//,''))}</a>`:esc(s.url)).join(' · ');
    return `<details class="h-extra-state"><summary>${label} lines · read ${esc(x.at?age(x.at):'time unavailable')} · view only</summary><p>Where ${label} offers the same line as PrizePicks or Underdog, the row shows Scout's saved side, chance and letter for that line, unchanged. Other lines show the line and price only. Slips stay PrizePicks or Underdog entries.</p>${r.pick_price?`<p>Price: ${esc(r.pick_price)}.</p>`:''}${r.entry_payout?`<p>Entry payout: ${esc(r.entry_payout)}.</p>`:''}${r.flex?`<p>Flex: ${esc(r.flex)}.</p>`:''}${src?`<p>Rules source: ${src}</p>`:''}</details>`;
  }
  async function loadExtra() {
    const b=H.book;if(!isExtra(b))return;
    /* a sport the server counted 0 for is skipped; a sport with no count yet is still read (a feed blip never drops the member to All) */
    const p=EXTRA.find(x=>x.id===b),sports=allowed().filter(s=>p.sports.includes(s)&&H.bookLines?.[b]?.[s]!==0);
    if(!sports.length){H.book='all';H.extraState=null;H.extraRecs=[];paintBoard();return;}   // counted empty here: back to All, never an error
    if(H.extraState?.book!==b){H.extraState={book:b,state:'loading'};H.extraRecs=[];paintBoard();}
    const res=await Promise.allSettled(sports.map(s=>api('book-board?sport='+encodeURIComponent(s)+'&book='+encodeURIComponent(b))));
    if(H.book!==b)return;
    const docs=res.filter(x=>x.status==='fulfilled').map(x=>x.value),states=docs.map(d=>d.feed?.state);
    H.extraRecs=docs.flatMap(d=>Array.isArray(d.records)?d.records:[]);
    H.extraState={book:b,state:!docs.length?'error':states.includes('ok')?'ok':states.find(x=>x&&x!=='not_offered')||'empty',at:docs.map(d=>d.feed?.at).filter(Boolean).sort().pop()||null};
    paintBoard();
  }
  /* an extra book's line in the full card (audit 2026-11-05 A2/B4): its own prices and Scout's read at this line, above the research */
  function extraOffer(r) {
    if(r.extra?.reference_only)return `<section class="h-extra-detail"><h4>DraftKings / ${esc(r.offer.reference_kind==='milestone'?(r.offer.line+0.5)+'+':r.offer.line)}</h4><p><b>${esc(reportedOdds(r)||'Odds not supplied')}</b> / Reported American odds</p><p>Via ESPN / ${esc(age(r.offer.seen_at))}. Selection sides are not supplied. These prices are not assigned to Over or Under and cannot be added to a slip.</p></section>`;
    const o=r.offer,x=r.extra||{},price=sd=>!o[sd]?.offered?'Not offered':x.price_comparable===false?(num(o[sd].price)?esc(x.price_label||'App price')+' '+o[sd].price.toFixed(2)+' <small>(not what an entry pays)</small>':'Listed'):num(o[sd].price)?o[sd].price.toFixed(2)+' decimal':num(o[sd].payout)?o[sd].payout.toFixed(2)+'x pick multiplier':'Price unavailable';
    /* Scout's read: the side and its chance, or both chances; nothing saved at this line = no row (no status sentence) */
    const s=r.scout,both=num(s.p_over)&&num(s.p_under)?`Over ${pct(s.p_over)} · Under ${pct(s.p_under)}`:null;
    const said=s.side?`${esc(sideWord(s.side))} ${pct(s.p_side)}`:both;
    const read=!said?null:x.same_line_as?`Same line as ${esc(x.same_line_as)}: ${said}`:s.line_chance&&s.side?`${said}, Scout’s chance at this line`:said;
    return `<section class="h-extra-detail"><div class="h-sec-head"><h4>${esc(o.platform_label)} · ${esc(o.line)}</h4><small>Read ${esc(o.seen_at?age(o.seen_at):'—')}</small></div><dl class="h-extra-kv"><div><dt>Over</dt><dd>${price('over')}</dd></div><div><dt>Under</dt><dd>${price('under')}</dd></div>${read?`<div><dt>Scout's read</dt><dd>${read}</dd></div>`:''}</dl>${s.line_chance?'<p class="h-history-note">Read from the same saved projection as this player\u2019s graded line. No letter or stake: those are set for the PrizePicks or Underdog entry only.</p>':''}</section>`;
  }
  function refreshSlip() {
    if(!isOpen()) return;
    const box=$('hub-slip-content'), top=box.scrollTop;
    // Repainting retains focus by action identity, including while an asynchronous recheck finishes.
    const a=document.activeElement, u=box.contains(a)?a.dataset.u:null, id=u?a.dataset.id:null;
    const empty=!T.legs.length&&!T.tracked;
    box.innerHTML=empty?'<div class="h-empty-slip"><p><b>No picks yet.</b> Add a side from any board. It stays here while you research.</p><small>Each platform becomes its own entry.</small></div>'
      :C.slipTab().replace('data-u="tab" data-id="picks">View receipts','data-hub="receipts">View in My picks');
    box.scrollTop=top;
    if(u) (box.querySelector(`[data-u="${CSS.escape(u)}"]${id!=null?`[data-id="${CSS.escape(id)}"]`:''}`)||box).focus({preventScroll:true});
    const n=String(T.legs.length);
    $('hub-slip-count').textContent=n;$('hub-tab-slip-n').textContent=n;$('hub-studio-tab-n').textContent=n;
    $('hub-tab-slip').setAttribute('aria-label',`Slip, ${n} ${n==='1'?'pick':'picks'}`);
    // owner 2026-11-05 (structure review): the slip column keeps its width when empty, so a first add never squeezes the board
    $('u-hub').classList.toggle('h-slip-empty',empty);
    $('hub-save-status').textContent=empty?'':T.saveFail?'Waiting to sync':T.pending||T.saving?'Saving…':'Saved to your account';
    paintBoard();
    if(H.section==='picks')paintPicks();
    if(H.section==='studio')paintStudio();
  }
  /* audit 2026-11-04 (polling): read only the sport(s) on screen. Another sport's board is read when its tab opens; the minute
     refresh runs only while the board is on screen and the page is visible; the browser revalidates each read with its ETag (304). */
  const visibleSports = () => H.sport==='all'?allowed():allowed().filter(s=>s===H.sport);
  /* QOL 2026-11-07: the refresh button shows it is working while the boards are read (it gave no sign a click did anything) */
  const busyRefresh = on => document.querySelectorAll('#u-hub .h-refresh').forEach(b=>{b.setAttribute('aria-busy',String(on));b.title=on?'Refreshing…':'Refresh';});
  async function load(force=false, sports=visibleSports()) {
    if(H.loading){H.queued=[...new Set([...(H.queued||[]),...sports])];H.queuedForce=H.queuedForce||force;return;}
    H.loading=true; busyRefresh(true); paintBoard();
    await Promise.allSettled(sports.map(s=>C.loadBoard(s,force)));
    H.lastLoad=Date.now();
    H.records=allowed().flatMap(s=>[...R.boards[s].recs.values()]);
    const current=rec(H.detail),shown=H.detailDoc?.record;
    if(shown&&(!current||current.offer.revision!==shown.offer.revision||current.scout.read_rev!==shown.scout.read_rev)){H.detailDoc=null;H.detailState='error';H.detailSeq++;paintDetail();}
    H.loading=false; busyRefresh(false); paintBoard(); checkAlerts();
    if(isExtra(H.book))loadExtra();
    loadBookLines();
    loadMarketEdge(sports);
    openPending();
    if(sports.includes('cs2')) { await C.loadHits(); paintBoard(); pollHits(); }
    if(H.queued){const next=H.queued.filter(x=>visibleSports().includes(x)),f=H.queuedForce;H.queued=null;H.queuedForce=false;if(next.length)load(f,next);}
  }
  // SCOUT_BACKGROUND_ALERTS deep link (workspace_alerts.prop_path): #scout/board?sport=..&open=<sport|platform|offer_id>
  // opens that prop's card once the boards are read. A prop that is gone or not current says so; nothing is guessed.
  function openPending() {
    const id=H.pendingOpen;if(!id||!isOpen())return;
    H.pendingOpen=null;
    const clean=location.hash.replace(/([?&])open=[^&]*(&|$)/,(m,a,b)=>b?a:'').replace(/[?&]$/,'');
    if(clean!==location.hash){history.replaceState(null,'',location.pathname+location.search+clean);H.routeHash=location.pathname+location.hash;}
    const r=rec(id);
    if(!r||unavailableReason(r)||C.started(r)){C.say('This offer is no longer on the board. Refresh to see current lines.');return;}
    Object.assign(H,{sport:allowed().includes(r.sport)?r.sport:'all',book:'all',stat:'all',game:'all',q:'',side:'all',minGrade:'all',onlyDiff:false,watched:false,freshOnly:false,soonOnly:false,movedOnly:false,view:'list',expanded:null,detail:null});
    if(H.section!=='board')section('board',false);
    syncFields();
    const players=[...new Set(rows().map(boardPlayerKey))];
    H.page=Math.max(0,Math.floor(players.indexOf(boardPlayerKey(r))/40));
    if(NEXT){const gs=boardGroups(rows()),pl=byPlayer(gs),us=pl?groupPlayers(gs):gs,k=pl?boardPlayerKey(r):linePropKey(r);
      H.page=Math.max(0,Math.floor(us.findIndex(u=>u.key===k)/(pl?PAGE_PLAYERS:PAGE_PROPS)));openLadders.add(linePropKey(r));}
    openPlayers.add(boardPlayerKey(r));
    openCard(id).then(()=>{document.querySelector(`#hub-rows tr[data-record="${CSS.escape(id)}"]`)?.scrollIntoView({block:'center'});});
  }
  function pollHits() {
    clearTimeout(H.hitsTimer);
    if(C.R.hitsComplete||!visibleSports().includes('cs2'))return;
    H.hitsTimer=setTimeout(async()=>{if(isOpen()&&!document.hidden&&visibleSports().includes('cs2')){await C.loadHits();if(H.section==='board')paintBoard();}pollHits();},10500);
  }
  async function savePreferences() {
    if(H.saveBusy) {H.saveAgain=true;return;}
    H.saveBusy=true;
    try {
      for(let i=0;i<3;i++) {
        const state=await api('state');
        try {
          await api('state',{rev:state.rev,prefs:{...state.prefs,researchHub:{views:H.views,watch:H.watch,alerts:H.alerts,slipLines:H.slipLines,...(H.sortChosen?{sort:{key:H.sort,dir:H.sortDir}}:{}),...(H.parKeep?{parity:H.parKeep}:{})}}});
          H.saveError=false; break;
        } catch(e) { if(e.status!==409||i===2) throw e; }
      }
    } catch(e) {H.saveError=true;C.say('Could not save research preferences. Your slip is separate; retry from Saved views.');}
    H.saveBusy=false;
    if(H.saveAgain){H.saveAgain=false;savePreferences();}
    paintAlerts();
  }
  async function loadPreferences() {
    try {
      const state=await api('state'), p=state.prefs?.researchHub||{};
      if(state.studio?.version===1 && Array.isArray(state.studio.slips)) {
        const slips=state.studio.slips.slice(0,12).filter(s=>s&&C.PLATFORMS[s.platform]&&C.PLATFORMS[s.platform].types.some(t=>t[0]===s.entry_type)&&Array.isArray(s.legs)&&s.legs.length>=2&&s.legs.length<=8&&s.legs.every(C.cleanLeg));
        H.batch={...state.studio,slips};H.batchSaved=true;paintBatch();
        if($('hub-generation-repeats'))$('hub-generation-repeats').value=state.studio.options?.noRepeatProps?'none':'allow';
      }
      const clean = values => !Array.isArray(values)?[]:values.filter(v=>v&&typeof v.name==='string'&&v.filters&&typeof v.filters==='object').slice(0,12).map((v,i)=>{
        const f=v.filters;
        return {id:typeof v.id==='string'?v.id.slice(0,100):'saved-'+i,name:v.name.slice(0,80),filters:{
          minGrade:gradeOrder.includes(f.minGrade)?f.minGrade:'all',freshOnly:f.freshOnly===true,soonOnly:f.soonOnly===true,movedOnly:f.movedOnly===true,
          sport:['all',...allowed()].includes(f.sport)?f.sport:'all',book:['all','prizepicks','underdog',...(EXTRA||[]).map(x=>x.id)].includes(f.book)?f.book:'all',
          q:typeof f.q==='string'?f.q.slice(0,100):'',side:['all','over','under'].includes(f.side)?f.side:'all',
          sort:SORT_KEYS.includes(f.sort)?f.sort:'start',sortDir:f.sortDir==='asc'||f.sortDir==='desc'?f.sortDir:sortDirOf(SORT_KEYS.includes(f.sort)?f.sort:'start'),stat:typeof f.stat==='string'?f.stat.slice(0,150):'all',game:typeof f.game==='string'?f.game.slice(0,150):'all',onlyDiff:f.onlyDiff===true,watched:f.watched===true}};
      });
      H.views=clean(p.views);
      if(p.sort&&adoptSort(p.sort)){try{localStorage.setItem(SORT_LS(),JSON.stringify({key:H.sort,dir:H.sortDir}));}catch(e){}}
      /* only a choice made in the Goblins control counts (prefs.researchHub.slipLines). A batch saved before it existed carries the old
         Lines select's 'all' (its first option and default), which is not a choice to allow goblins, so it reads as No goblins; the
         old 'Standard lines only' was a deliberate opt-out and carries over as No goblins or demons (review 2026-11-05). */
      H.slipLines=cleanLines(p.slipLines||(state.studio?.options?.lines==='standard'?'standard':null));T.slipLines=H.slipLines;
      if($('hub-generation-lines'))$('hub-generation-lines').value=H.slipLines;
      C.refreshAdvice?.();
      H.watch=Array.isArray(p.watch)?p.watch.filter(v=>typeof v==='string').slice(0,100):[];
      if(p.parity&&typeof p.parity==='object'){   /* SCOUT_PARITY_PREFS: kept as saved even while the flag is off */
        const q=p.parity;H.parKeep=q;
        H.par={odds:q.odds==='decimal'?'decimal':'american',sizing:SIZING.some(x=>x[0]===q.sizing)?q.sizing:'quarter',bankroll:num(q.bankroll)&&q.bankroll>0&&q.bankroll<=1e7?q.bankroll:null};
      }
      H.alerts=clean(p.alerts);
      if(R.me.features?.background_alerts)H.delivery=await api('alert-delivery');
    } catch(e) {H.saveError=true;}
  }
  function checkAlerts() {
    for(const alert of H.alerts) {
      // No alert for a stale or unavailable source; baseline only once per visit.
      const found=H.records.filter(r=>!unavailableReason(r)&&!C.started(r)&&matches(r,alert.filters));
      const now=new Set(found.map(r=>key(r)+'|'+r.offer.revision));
      const prev=H.seen.get(alert.id);
      if(prev) for(const r of found) if(!prev.has(key(r)+'|'+r.offer.revision)) H.notices.unshift({id:key(r),name:r.player.name,text:`${r.offer.platform_label}: ${r.offer.line} ${r.market.stat_label}`,at:Date.now(),view:alert.name});
      H.seen.set(alert.id,now);
    }
    H.notices=H.notices.slice(0,30); paintAlerts();
  }
  /* line alerts: one body, shown on the Tools page (managed right there) and on its own #/alerts route */
  function alertsHtml(full) {
    return `${full?`<h2>${BG?'Follow the lines that matter.':'Live highlight'}</h2><p>${BG?'Saved filters flag new matching offers and revisions.':'Saved filters highlight new matching offers and revisions while this research screen is open. Nothing is sent when it is closed.'} Always recheck the line before building a slip.</p>`:`<div class="h-sec-head"><h3>${ALERT_WORD}</h3><button data-hub="section" data-id="alerts">${BG?'Alert settings':'Live highlight'} →</button></div>`}
      ${full?(R.me.features?.background_alerts?`<div class="h-alert"><div><b>Whop notifications</b><p>${H.delivery?.enabled?`Enabled for this account, including when Scout is closed. At most ${H.delivery.daily_cap??12} a day, one every ${Math.round((H.delivery.cooldown_s??900)/60)} minutes; each opens the prop. Every message has a one-tap stop link.`:'Opt in to personal Whop notifications while Scout is closed. Uses your Whop account for delivery; consent expires after 30 days.'}</p>${H.delivery?.status?.startsWith('delivery_')?'<p class="h-warning">The last delivery could not be confirmed. Live highlight still works on this screen.</p>':''}</div><button data-hub="toggle-delivery" ${H.deliveryBusy?'disabled':''}>${H.delivery?.enabled?'Turn off':'Enable Whop alerts'}</button></div>`:''):''}
      ${full&&H.deliveryError?`<p class="h-warning" role="status">${esc(H.deliveryError)}</p>`:''}
      ${H.saveError?'<p class="h-warning">Preferences have not synced. <button data-hub="save-retry">Retry save</button></p>':''}
      <h3>${BG?'Your alerts':'Your saved filters'}</h3>${H.alerts.length?H.alerts.map((a,i)=>`<div class="h-alert"><b>${esc(a.name)}</b><span>${esc(a.filters.sport==='all'?'All sports':SPORTS[a.filters.sport]||a.filters.sport)} · ${esc(a.filters.book)}</span><button data-hub="delete-alert" data-id="${i}" aria-label="Remove ${esc(a.name)}">Remove</button></div>`).join(''):`<p>Nothing saved yet. Set your board filters, then choose Views › ${BG?'Alert me on these filters':'Live highlight these filters'}.</p><button class="h-alert-new" data-hub="alerts-new">Set filters on the board</button>`}
      <h3>Live highlight · this visit</h3>${H.notices.length?H.notices.map(n=>`<button class="h-notice" data-hub="research" data-id="${esc(n.id)}"><b>${esc(n.name)}</b><span>${esc(n.text)}</span><small>${esc(n.view)} · ${new Date(n.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</small></button>`).join(''):'<p>New matches will appear here after the next refresh. The first load establishes your baseline.</p>'}`;
  }
  function paintAlerts() {
    if(!isOpen()) return;
    for(const id of ['hub-alert-count','hub-alert-count-m'])$(id).textContent=H.notices.length?String(H.notices.length):'';
    if(H.section==='alerts')$('hub-alerts-body').innerHTML=alertsHtml(true);
    if(H.section==='tools'&&$('hub-tools-alerts'))$('hub-tools-alerts').innerHTML=alertsHtml(false);
  }
  /* ---- the player card: ONE component for every entry point (row, Side, Compare, alerts, Genie). A right panel on desktop that
     leaves the board and the slip column where they are; a full-screen sheet with Back on phone. The decision strip (line and book,
     Over/Under, chance, letter or Lean, projection, Add) is pinned above four tabs: Overview, Lines, Stats, Ask. ---- */
  const cardOpen = () => !$('hub-card').hidden;
  function openCard(id,tab='overview') {
    const r=rec(id);if(!r)return Promise.resolve();
    const same=cardOpen()&&H.expanded===id;
    H.cardTab=CARD_TABS.some(t=>t[0]===tab)?tab:'overview';
    if(same){paintDetail();return Promise.resolve();}
    if(!cardOpen()){
      H.cardFrom=document.activeElement;
      /* QOL 2026-11-07: on a phone the card fills the screen, so the phone's Back button closes it (it left the board for the page
         before). One history entry at the same address; closing the card any other way takes that entry back off. */
      /* a marker left on this entry with no card open (a reload with the card up) is cleared first, so the new card still gets
         its own entry and Back still closes it */
      if(!DESKTOP.matches&&history.state?.hubCard&&!H.popSkip){try{history.replaceState(null,'',location.href);}catch(e){}}
      if(!DESKTOP.matches&&!history.state?.hubCard){try{history.pushState({hubCard:1},'',location.href);H.cardPushed=true;}catch(e){}}
    }
    const sd=r.scout.side;
    /* #24: the card opens on Scout's side even when the app offers only the other one (it opened on the offered side: "Add Over 2.5"
       under a 28.7% chance while the board said Lean Under); decisionHtml then says that side is not offered here */
    H.expanded=id;H.histN=10;H.histSide=sd==='over'||sd==='under'?sd:r.offer.over.offered?'over':r.offer.under.offered?'under':'over';
    $('hub-card').hidden=false;$('u-hub').classList.add('h-card-open');
    const done=selectDetail(id);
    $('hub-card-title')?.focus({preventScroll:true});
    return done;
  }
  function closeCard(restore=true) {
    const id=H.expanded;
    H.expanded=null;H.detail=null;H.detailDoc=null;H.detailSeq++;
    $('hub-card').hidden=true;$('u-hub').classList.remove('h-card-open');$('hub-detail').innerHTML='';
    /* closed in place (Back button, Escape, the row again): take the phone's extra history entry off, so Back then leaves as before.
       Closed by moving to another page: the entry stays behind as a plain board entry (Back shows the board) - its card marker is
       cleared, or the next card would find it, add no entry of its own, and Back would leave the card open. */
    if(H.cardPushed){H.cardPushed=false;if(history.state?.hubCard){if(restore){H.popSkip=true;history.back();}else{try{history.replaceState(null,'',location.href);}catch(e){}}}}
    paintBoard();
    if(!restore)return;
    const back=id&&document.querySelector(`#hub-rows .h-player[data-id="${CSS.escape(id)}"]`);
    (back||(H.cardFrom?.isConnected?H.cardFrom:null)||$('hub-q')).focus({preventScroll:true});
  }
  async function selectDetail(id) {
    const r=rec(id), parent=rec(H.expanded);
    if(!r||!parent||!C.siblings(parent).some(x=>key(x)===id))return;
    H.detail=id;H.detailDoc=null;H.detailState='loading';
    const seq=++H.detailSeq;paintBoard();paintDetail();
    try {
      const doc=await C.researchDoc(r);
      if(seq!==H.detailSeq)return;
      H.detailDoc=doc;H.detailState='ready';
    } catch(e){if(seq!==H.detailSeq)return;H.detailState='error';}
    paintDetail();
  }
  /* card helpers: windows, head-to-head, grouped stats, the chart, HLTV map chances */
  const atLine = (v,line) => v===line?'Push':v>line?'Over':'Under';
  /* SCOUT_LINE_MOVE_NOTES: a bumped past game's opening -> closing line in a full-width row under it (upgrade.js renders it) */
  const movedRow = (m,cols) => C.moveRow?C.moveRow(m,cols,'h-moved'):'';
  const tallyOf = (list,line) => ({n:list.length,over:list.filter(g=>g.value>line).length,under:list.filter(g=>g.value<line).length,push:list.filter(g=>g.value===line).length});
  /* a window from the loaded games when they cover it, else the board's own count (the newest results, n may be below the window) */
  function windowTally(r,all,n) {
    const list=all.slice(0,n), saved=C.tallies(r)['l'+n];
    if(list.length&&(list.length>=n||!saved||!saved.n))return tallyOf(list,r.offer.line);
    return saved&&saved.n?saved:null;
  }
  /* head-to-head vs tonight's opponent at tonight's line (owner 2026-11-04: as on the classic CS2 card); past results, not a chance */
  function h2hHtml(hh,line,side,moves) {
    if(!hh||!hh.opponent)return '';
    const head=`<div class="h-sec-head"><h4>Head-to-head vs ${esc(hh.opponent)}</h4></div>`;
    if(!hh.n)return `<section class="h-h2h">${head}<p class="h-history-note">No past meetings with ${esc(hh.opponent)} in this player's saved results.</p></section>`;
    return `<section class="h-h2h">${head}<div class="h-mini-tiles"><div><b>${countOf(hh,side)}<em>/${hh.n}</em></b><span>${sideWord(side)} ${esc(line)}${hh.push?` · ${hh.push} ${hh.push===1?'push':'pushes'}`:''}</span></div><div><b>${num(hh.avg)?hh.avg.toFixed(1):'—'}</b><span>Average in ${hh.n} ${hh.n===1?'meeting':'meetings'}</span></div></div>
      <table class="h-h2h-table"><thead><tr><th>Date</th><th>Result</th><th>At ${esc(line)}</th></tr></thead><tbody>${hh.series.map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.value)}</td><td class="h-at-${atLine(g.value,line).toLowerCase()}">${atLine(g.value,line)}</td></tr>${movedRow(moves?.get(g.date+'|'+(g.opponent||hh.opponent)),3)}`).join('')}</tbody></table>
      <p class="h-history-note">Past meetings at today's line. Not a predicted chance.</p></section>`;
  }
  const factText = v => typeof v==='object'?JSON.stringify(v):String(v);
  const HEDGE_RX = /\b(questionable|doubtful|probable|day[-_ ]to[-_ ]day|unconfirmed|uncertain)\b/i;
  /* every saved measure under its own heading (context rows carry a group); nothing is computed here except the map split of loaded games */
  function statsHtml(r,doc,all) {
    /* hedge statuses (questionable, day-to-day...) never show on a member page: definite facts only */
    const rows=(doc?.context?.rows||[]).filter(f=>f.label&&f.value!=null&&!(typeof f.value==='string'&&HEDGE_RX.test(f.value))), groups=new Map();
    for(const f of rows){const g=typeof f.group==='string'&&f.group?f.group:'More measures';if(!groups.has(g))groups.set(g,[]);groups.get(g).push(f);}
    const block=(title,body,cls='')=>`<section class="h-stat-group${cls}"><h5>${esc(title)}</h5>${body}</section>`;
    const blocks=[...groups].map(([g,list])=>block(g,`<dl>${list.map(f=>`<div><dt>${esc(f.label)}</dt><dd>${esc(factText(f.value))}</dd></div>`).join('')}</dl>`));
    const maps=new Map();
    all.forEach(g=>(g.maps||[]).forEach((name,i)=>{if(!name||!g.values||!num(g.values[i]))return;const e=maps.get(name)||{n:0,t:0};e.n++;e.t+=g.values[i];maps.set(name,e);}));
    if(maps.size)blocks.push(block(`By map · last ${all.length} ${all.length===1?'series':'series'}`,`<dl>${[...maps].sort((a,b)=>b[1].n-a[1].n).map(([name,e])=>`<div><dt>${esc(name)} <small>${e.n} played</small></dt><dd>${(e.t/e.n).toFixed(1)}</dd></div>`).join('')}</dl>`));
    const drivers=(doc?.context?.drivers||[]).filter(Boolean);
    if(drivers.length)blocks.push(block('What moved this forecast',`<ul>${drivers.map(d=>`<li>${esc(factText(d))}</li>`).join('')}</ul>`));
    const s=r.scout, method=s.validation?.detail||s.stake_reason, notes=[];
    if(Array.isArray(s.range80))notes.push(`<div><dt>80% range</dt><dd>${esc(s.range80[0])} to ${esc(s.range80[1])}</dd></div>`);
    if(doc?.forecast?.model_version||s.model_version)notes.push(`<div><dt>Model</dt><dd>${esc(doc?.forecast?.model_version||s.model_version)}</dd></div>`);
    if(notes.length||method)blocks.push(block('Forecast',`${notes.length?`<dl>${notes.join('')}</dl>`:''}${method?`<p>${esc(method)}</p>`:''}`));
    if(!blocks.length)return '';
    return `<section class="h-stat-library"><div class="h-sec-head"><h4>Player stats</h4><small>${rows.length} ${rows.length===1?'measure':'measures'}${groups.size?' · '+groups.size+' '+(groups.size===1?'group':'groups'):''}</small></div><div class="h-stat-groups">${blocks.join('')}</div></section>`;
  }
  function drawChart() {
    const box=$('hub-detail'), el=box?.querySelector('[data-hub-chart]'), r=rec(H.detail);
    if(!el||!r)return;
    const lh=H.detailDoc?.history?.line_history, cm=new Map((lh?.closing_marks||[]).map(m=>[m.date+'|'+m.opponent,m]));
    const mv=C.movesOf?C.movesOf(H.detailDoc):new Map();   /* the move only through movesOf: a same-day rematch gets none */
    const list=(H.detailDoc?.history?.series||[]).filter(g=>num(g.value)).slice(0,H.histN).map(g=>{const m=cm.get(g.date+'|'+g.opponent),k=mv.get(g.date+'|'+g.opponent);return m?{...g,closing:m.closing,...(k?{move:k}:{})}:g;});
    const sorted=list.map(g=>g.value).sort((a,b)=>a-b),mid=Math.floor(sorted.length/2),median=sorted.length?(sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2):null;
    if(list.length)el.innerHTML=C.chartSvg(list,r.offer.line,H.histSide,Math.max(260,Math.round(el.clientWidth)||560),{median});
    const lel=box.querySelector('[data-hub-lh]');
    if(lel&&lh)lel.innerHTML=lhSvg(lh,Math.max(260,Math.round(lel.clientWidth)||560));
  }
  /* SCOUT_LINE_HISTORY (lh-1, default off): every app's line for this prop over time, from Scout's own stored moves. Times are the
     member's own clock; each point's detail is on hover. Nothing here is a grade input. */
  const lhTime=iso=>new Date(iso).toLocaleString([],{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  const LH_ZONE=(()=>{try{return Intl.DateTimeFormat().resolvedOptions().timeZone||'';}catch{return '';}})();
  function lhSvg(lh,w) {
    const apps=lh.apps||[], pts=apps.flatMap(a=>a.points), lines=pts.map(p=>p.line).filter(num);
    if(!lines.length)return '';
    const ts=pts.map(p=>Date.parse(p.at)).filter(Number.isFinite), t1=Math.max(...ts,Date.parse(lh.as_of)||0), t0=Math.min(...ts)<t1?Math.min(...ts):t1-3600e3;
    const lo=Math.min(...lines), hi=Math.max(...lines), pad=Math.max(.5,(hi-lo)*.15), y0=lo-pad, y1=hi+pad;
    const h=150, m={l:34,r:10,t:12,b:24}, pw=w-m.l-m.r, ph=h-m.t-m.b, X=t=>m.l+(t-t0)/(t1-t0)*pw, Y=v=>m.t+ph-(v-y0)/(y1-y0)*ph;
    const ticks=[...new Set(lines)].sort((a,b)=>a-b), shown=ticks.length<=5?ticks:[lo,(lo+hi)/2,hi].map(v=>Math.round(v*2)/2);
    let out=`<svg viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">`;
    shown.forEach(v=>{out+=`<line class="grid" x1="${m.l}" x2="${w-m.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${m.l-6}" y="${Y(v)+3.5}" text-anchor="end">${esc(v)}</text>`;});
    out+=`<text x="${m.l}" y="${h-6}">${esc(lhTime(new Date(t0).toISOString()))}</text><text x="${w-m.r}" y="${h-6}" text-anchor="end">${esc(lhTime(new Date(t1).toISOString()))}</text>`;
    apps.forEach((a,i)=>{
      let d='', open=false;
      a.points.forEach(p=>{
        const x=X(Date.parse(p.at));
        if(!num(p.line)){if(open)d+=`H${x}`;open=false;return;}
        d+=open?`H${x}V${Y(p.line)}`:`M${x},${Y(p.line)}`;open=true;
      });
      if(open)d+=`H${X(t1)}`;
      out+=`<path class="lh-path lh-c${i%4}" d="${d}"/>`;
      a.points.forEach(p=>{
        const x=X(Date.parse(p.at)), y=num(p.line)?Y(p.line):m.t+ph, pay=num(p.higher)||num(p.lower)?` · payouts ${num(p.higher)?p.higher+'x more':''}${num(p.higher)&&num(p.lower)?', ':''}${num(p.lower)?p.lower+'x less':''}`:'';
        out+=`<g class="lh-pt lh-c${i%4}"><circle class="lh-dot" cx="${x}" cy="${y}" r="3.2"/><circle class="lh-hit" cx="${x}" cy="${y}" r="9"><title>${esc(`${a.label} ${lhWord(p)} · ${lhTime(p.at)}${pay}`)}</title></circle></g>`;
      });
    });
    return out+'</svg>';
  }
  function lhWord(p) {
    return p.kind==='pulled'?'pulled':p.kind==='seen'?`on the board at ${p.line}`:p.kind==='open'?`opened at ${p.line}`:p.kind==='payout'?`payout changed at ${p.line}`:`moved to ${p.line}`;
  }
  function lineHistHtml(lh) {
    if(!lh)return '';
    const apps=lh.apps||[], moves=apps.reduce((n,a)=>n+a.moves,0);
    const rows=apps.flatMap(a=>a.points.map(p=>({a,p}))).sort((x,y)=>Date.parse(y.p.at)-Date.parse(x.p.at));
    const chart=apps.length?`<div class="h-lh-chart" data-hub-lh role="img" aria-label="Line history for ${apps.length} ${apps.length===1?'app':'apps'}, ${moves} ${moves===1?'move':'moves'}"></div>
      <div class="h-legend h-lh-legend">${apps.map((a,i)=>`<span><i class="lh-c${i%4}"></i>${esc(a.label)} ${esc(a.current)}</span>`).join('')}${(lh.closing_marks||[]).length?'<span><i class="lh-close"></i>Closing line (bars above)</span>':''}</div>`:'';
    const table=rows.length?`<details class="h-history-table h-lh-table"><summary>Every move (${rows.length})</summary><div class="h-scroll-x"><table><thead><tr><th>Time</th><th>App</th><th>Line</th><th>Change</th></tr></thead><tbody>${rows.map(({a,p})=>`<tr><td><time datetime="${esc(p.at)}">${esc(lhTime(p.at))}</time></td><td>${esc(a.label)}</td><td>${num(p.line)?esc(p.line):'—'}</td><td>${esc(lhWord(p))}</td></tr>`).join('')}</tbody></table></div></details>`:'';
    const closes=(lh.closing_marks||[]).length?`<p class="h-history-note">Closing lines on ${esc(lh.closing_platform)}: the white tick on each bar is the line that game closed at.${num(lh.avg_closing_l10)?` Average closing line, last ${lh.closing_n_l10} ${lh.closing_n_l10===1?'game':'games'}: <b>${esc(lh.avg_closing_l10)}</b>.`:''}</p>`:'';
    return `<section class="h-lh"><div class="h-sec-head"><h4>Line history</h4><small>${apps.length} ${apps.length===1?'app':'apps'} · ${moves} ${moves===1?'move':'moves'}${LH_ZONE?` · times in ${esc(LH_ZONE)}`:''}</small></div>${chart}${closes}${table}</section>`;
  }
  /* HLTV map chances (owner 2026-11-04, as on the classic CS2 card): each map's chance to be played and the likeliest simulated
     veto; display only, weight 0 in the grade. No projection for the match = no block. */
  function mapChancesHtml(m) {
    if(!m||!(m.maps||[]).length)return '';
    const lv=m.veto;
    return `<section class="h-mapchances"><div class="h-sec-head"><h4>HLTV map chances</h4><small>${m.at?`Updated ${esc(when(m.at))}`:''}</small></div><ul class="h-mc-maps">${m.maps.map(x=>`<li>${esc(x.name)} <b>${esc(x.pct)}%</b></li>`).join('')}</ul>
      ${lv&&(lv.maps||[]).length?`<p class="h-history-note">Likeliest veto${lv.pct!=null?` (${esc(lv.pct)}% of HLTV's simulations)`:''}: ${lv.maps.map(x=>`${esc(x.slot)} ${esc(x.name)}`).join(' · ')}</p>`:''}
      ${mapChancesUse(m)}</section>`;
  }
  /* CS2_MAP_WEIGHTED (map_weighted.saved_status, from the saved forecast): "HLTV map chances used" + capture time when they fed
     this grade, otherwise "Shadow only – not in this grade" with the map-weighted (vm-2) projection beside the live one. */
  function mapChancesUse(m) {
    const u=m.grade_use;
    if(u&&u.used)return `<p class="h-history-note"><b>${esc(u.label)}</b>${u.at?` · captured ${esc(when(u.at))}`:''}${u.projection!=null?` · projection ${esc(u.projection)}${u.unweighted!=null?` (without map chances ${esc(u.unweighted)})`:''}`:''}</p>`;
    return `<p class="h-history-note">Weight in the grade: 0. ${esc(u?u.label:(m.note||'The grade does not use HLTV map chances yet.'))}</p>`
      +(u&&u.shadow!=null&&u.projection!=null?`<p class="h-history-note">Map-weighted projection (shadow) ${esc(u.shadow)} · live projection ${esc(u.projection)}</p>`:'');
  }
  function decisionHtml(r) {
    const side=H.histSide, line=r.offer.line, s=r.scout, quotes=C.siblings(r), on=C.onSlip(r);
    const eligible=!r.extra&&H.detailState==='ready'&&!unavailableReason(r)&&!C.started(r)&&r.offer[side].offered;
    const chance=C.chanceOf(r,side);
    const pick=C.oneSided(r)?gradeChip(r):s.side&&side===s.side?gradeChip(r):s.side?`<span class="h-grade-text">Scout side is ${sideWord(s.side)}</span>`:s.hold?'<span class="h-grade-text">Research estimate</span>':'';
    /* #24: Scout's side is not offered on this app (Over-only demon, PrizePicks one-way lines): no main Add button. The board row
       says "Side" there; the card says plainly which side the app offers, and adding that side is a secondary, labelled action. */
    const other=s.side==='over'?'under':'over', against=!!s.side&&!C.oneSided(r)&&!r.extra&&!r.offer[s.side]?.offered&&!!r.offer[other]?.offered;
    const otherOk=against&&!on&&H.detailState==='ready'&&!unavailableReason(r)&&!C.started(r);
    const againstHtml=()=>`<div class="h-dec-act h-dec-unoffered"><small class="h-dec-note">${esc(r.offer.platform_label)} offers only ${sideWord(other)} ${esc(line)} on this line. Scout's side, ${sideWord(s.side)}, is not offered here.</small>${on?`<button class="h-dec-add is-on" data-hub="detail-add">On slip ✓</button>`:`<button class="h-dec-alt" data-hub="detail-add-other" ${otherOk?'':'disabled'}>Add ${sideWord(other)} ${esc(line)} (against Scout's side)</button>`}</div>`;
    const opt=q=>`<option value="${esc(key(q))}"${key(q)===key(r)?' selected':''}>${esc(q.offer.platform_label)} ${esc(q.offer.line)}${kindOf(q)?' · '+esc(kindOf(q)):q.market.line_type!=='standard'?' · '+esc(C.lineLabel(q)):''}</option>`;
    return `<label class="h-dec-line"><span>Line</span><select id="hub-card-line" aria-label="Line and book">${quotes.map(opt).join('')}</select></label>
      <div class="h-segment h-side-toggle" role="group" aria-label="Side">${['over','under'].map(sd=>`<button data-hub="history-side" data-id="${sd}" aria-pressed="${side===sd}">${sideWord(sd)}</button>`).join('')}</div>
      <div class="h-dec-chance"><b title="Forecast chance · ${sideWord(side)} ${esc(line)}">${chance==null?'—':pct(chance)}</b>${pick?`<span class="h-dec-pick">${pick}</span>`:''}${s.side===side?belowChip(r):''}</div>
      <div class="h-dec-proj"><b>${num(s.projection)?esc(s.projection.toFixed(1)):'—'}</b><span>Proj.</span></div>${P('prefs')&&s.side===side&&parOf(r)?`<small class="h-dec-size">Break-even ${esc(beOdds(parOf(r)))}${sizeText(parOf(r))?` · ${esc(sizingLabel())}: ${esc(sizeText(parOf(r)))}`:''}</small>`:''}
      ${against?againstHtml():C.oneSided(r)&&!r.extra?`<div class="h-dec-act h-dec-view"><small>${esc(C.oneSidedNote(r))}</small></div>`:r.extra?`<div class="h-dec-act h-dec-view"><small>View only. Slips are PrizePicks or Underdog entries; open ${esc(r.offer.platform_label)} to play this line.</small></div>`:`<div class="h-dec-act"><button class="h-dec-add${on?' is-on':''}" data-hub="detail-add" ${on||eligible?'':'disabled'}>${on?'On slip ✓':`Add ${sideWord(side)} ${esc(line)}`}</button>${R.me.features?.book_links&&r.offer.platform==='underdog'?`<button data-hub="open-line" ${eligible?'':'disabled'}>Open in Underdog</button>`:''}</div>`}
      ${!r.offer[side].offered&&!against?'<small class="h-dec-note">This side is not offered on the selected platform.</small>':''}`;
  }
  /* ---- "Why Scout leans Over / Under" (owner 2026-11-06: "people have no idea why they should take the props"). Plain facts at the
     top of the card, each with its number and its sample, sorted honestly:
       - Scout's own number (projection and chance; LoL's model factors; CS2 map chances only when the saved forecast used them),
         its edge coloured by the chance on this side (under 50% is never green);
       - "Other numbers that point to <side>": past results and prices that agree with the side (not part of the grade);
       - "What could go wrong": facts against the side, the player's own definite injury status first;
       - "Also good to know": even records, the format, other lines (no colour, never under "Why").
     Dots only where a grade exists (filled = used in the grade, hollow = not used). NBA / NFL letters are held: no dots there.
     Hedge statuses (day-to-day, questionable) are never shown. Every figure is read from what the card already loaded (the board
     record and its research doc): nothing is fetched, modelled or graded here. ---- */
  const f1 = v => String(Math.round(v*10)/10);
  const ctxRows = doc => (doc?.context?.rows||[]).filter(f=>f&&f.label&&f.value!=null&&f.value!=='');
  const ctxRow = (doc,label) => ctxRows(doc).find(f=>f.label===label);
  const ctxVal = (doc,label) => ctxRow(doc,label)?.value;
  const DEFINITE = {out:'is listed out',injured_reserve:'is on injured reserve',suspended:'is suspended'};
  /* a factor as plain words: 0.88 -> "12% fewer", 1.05 -> "5% more" */
  const moreFewer = v => {const d=Math.round((v-1)*100);return d===0?'as many':`${Math.abs(d)}% ${d>0?'more':'fewer'}`;};
  /* market sources in plain words ("margin removed" is jargon) */
  const plainSource = t => String(t||'').replace(/,?\s*margin removed/i,', with the bookmaker’s cut taken out').replace(/^(\d+) market sources? at this line$/i,(m,n)=>`${n} sportsbook ${n==='1'?'price':'prices'} at this line`);
  /* NBA / NFL averages rows ("Minutes, L3", "Targets, Season 2026") -> {L3, L5, L10, Season} of the first measure that has them */
  function usageOf(doc,sport,stat) {
    const rows=ctxRows(doc).filter(f=>f.group==='Averages'&&num(Number(f.value)));
    const want=sport==='nba'?(/three|3pt|fg3/i.test(stat)?['3-point attempts','Minutes']:['Minutes','Field-goal attempts']):/rec|tgt|target/i.test(stat)?['Snaps','Snap share','Targets']:/rush|carr/i.test(stat)?['Snaps','Snap share','Carries']:/pass|cmp|comp|att/i.test(stat)?['Snaps','Pass attempts']:['Snaps','Snap share','Targets','Carries'];
    for(const name of want){
      const got={};rows.forEach(f=>{const m=/^(.*), (L3|L5|L10|Season.*)$/.exec(f.label);if(m&&m[1]===name)got[m[2].startsWith('Season')?'Season':m[2]]=Number(f.value);});
      if(num(got.L3)&&num(got.L10))return {name,...got};
    }
    return null;
  }
  /* one LoL model factor (lol/center/forecast.py _drivers) in plain words, with the way it pushes this side; null = leave it out */
  function lolDriver(d,r,side) {
    const t=String(d), stat=esc(r.market.stat_label.toLowerCase()), opp=esc(r.player.opponent||'The opponent'), team=esc(r.player.team||'His team');
    const push=v=>Math.abs(v-1)<0.005?0:(v>1)===(side==='over')?1:-1;
    let m;
    if((m=/^player (share of team output|per-game rate) ([\d.]+) \((\d+) earlier games\)/.exec(t)))
      return {html:`His base rate: <b>${f1(+m[2])}</b> ${stat} a game${m[1].startsWith('share')?' at an average team’s pace':''} <small>(${esc(m[3])} earlier games)</small>`,dir:0};
    if((m=/^team pace factor ([\d.]+)/.exec(t)))return {html:`${team} ${r.market.stat==='deaths'?'give up':'get'} <b>${moreFewer(+m[1])}</b> kills a game than an average team`,dir:push(+m[1])};
    if((m=/^opponent factor ([\d.]+)/.exec(t)))return {html:`${opp} ${r.market.stat==='deaths'?'get':'give up'} <b>${moreFewer(+m[1])}</b> kills a game than an average team`,dir:push(+m[1])};
    if((m=/^match-strength factor ([\d.]+) \(win chance (\d+)%\)/.exec(t)))
      return Math.abs(+m[1]-1)<0.005?null:{html:`${team}’s <b>${esc(m[2])}%</b> win chance moves his number <b>${moreFewer(+m[1]).replace('more','up').replace('fewer','down')}</b>`,dir:push(+m[1])};
    if(/^player's per-game average shrunk/.test(t))return {html:`His per-game average, pulled toward his role’s average <small>(${esc((/\((\d+) earlier games\)/.exec(t)||[])[1]||'')} earlier games)</small>`,dir:0};
    if(/^whole-match simulation/.test(t))return {html:'Scout simulates the whole match: both teams’ kill pace, game length and the win chance',dir:0};
    return null;
  }
  function whyFacts(r) {
    const doc=H.detailDoc, s=r.scout, line=Number(r.offer.line), lean=s.side||null, side=lean||H.histSide, Side=sideWord(side);
    const graded=!!s.grade&&!s.hold;     /* a grade exists: only then does a fact say whether it is used in it */
    const out={scout:[],for:[],risk:[],also:[]};
    const fact=(html,used,dir)=>({html,used:!!used&&graded,dir});
    const add=(html,dir=0,first=false)=>{const l=dir>0?out.for:dir<0?out.risk:out.also;l[first?'unshift':'push'](fact(html,false,dir));};
    const toward=v=>side==='over'?v>line:v<line, away=v=>side==='over'?v<line:v>line;
    const recDir=(k,n)=>k*2>n?1:k*2<n?-1:0;
    const stat=esc(r.market.stat_label);
    /* 1. Scout's own number: the projection against the line and the chance on this side. The colour follows the chance */
    const chance=C.chanceOf(r,side), cdir=num(chance)&&Math.abs(chance-.5)>1e-9?(chance>.5?1:-1):null;
    if(num(s.projection)&&Number.isFinite(line)){
      const gap=s.projection-line;
      out.scout.push(fact(`Scout projects <b>${f1(s.projection)}</b> ${stat}, ${Math.abs(gap)<0.05?'right on':`<b>${f1(Math.abs(gap))}</b> ${gap>0?'above':'below'}`} the ${esc(r.offer.line)} line${num(chance)?` · <b>${pct(chance)}</b> chance ${Side}`:''}`,true,cdir??(toward(s.projection)?1:away(s.projection)?-1:0)));
    } else if(num(chance)) out.scout.push(fact(`Scout gives <b>${pct(chance)}</b> chance ${Side} ${esc(r.offer.line)}`,true,cdir??0));
    /* 2. results at THIS line: L10 first, L5 / L20 / season beside it (the card's own tiles) */
    const all=(doc?.history?.series||[]).filter(g=>num(g.value));
    const w={5:windowTally(r,all,5),10:windowTally(r,all,10),20:windowTally(r,all,20)}, se=C.tallies(r).season;
    const main=w[10]?.n?w[10]:w[5]?.n?w[5]:w[20]?.n?w[20]:null;
    if(main){
      const k=countOf(main,side), rest=[[5,w[5]],[20,w[20]]].filter(([n,t])=>t?.n&&t!==main&&t.n!==main.n).map(([n,t])=>`L${n} ${countOf(t,side)}/${t.n}`);
      if(se?.n)rest.push(`season ${countOf(se,side)}/${se.n}`);
      add(`Went ${Side} in <b>${k}</b> of the last <b>${main.n}</b> at ${esc(r.offer.line)}${rest.length?` <small>(${rest.join(' · ')})</small>`:''}`,recDir(k,main.n));
      const l5=w[5];
      if(l5?.n>=5&&main!==l5&&k*2>main.n&&countOf(l5,side)*2<l5.n)add(`Recent form turned: ${Side} in only <b>${countOf(l5,side)}</b> of the last <b>5</b>`,-1);
      if(main.n<5)add(`Small sample: only <b>${main.n}</b> past ${main.n===1?'result':'results'} on file for this market`,-1);
    }
    /* 3. average and median of the last 10 (or fewer) results, on the line's own maps / period */
    const ten=all.slice(0,10);
    if(ten.length>=3&&Number.isFinite(line)){
      const avg=ten.reduce((a,g)=>a+g.value,0)/ten.length, srt=ten.map(g=>g.value).sort((a,b)=>a-b), m=Math.floor(srt.length/2), med=srt.length%2?srt[m]:(srt[m-1]+srt[m])/2;
      const text=`Averaged <b>${f1(avg)}</b>, median <b>${f1(med)}</b> over the last ${ten.length} · line ${esc(r.offer.line)}`;
      if(toward(avg)&&toward(med))add(text,1);
      else if(away(avg)&&away(med))add(text,-1);
      else add(`${text} <small>(the two do not agree on a side)</small>`,0);
    } else if(r.sport==='cs2'&&!all.length&&num(r.history?.avg)&&Number.isFinite(line)){
      /* CS2's saved recent average is on the line's own maps; LoL's board fallback is a per-game rate, never set against a series line */
      const a=r.history.avg;
      add(`Recent average <b>${esc(a)}</b> on ${esc(r.market.period_label)} · line ${esc(r.offer.line)}`,toward(a)?1:away(a)?-1:0);
    }
    /* a Q1 / first-half line with no results at its own period: his full-game log as context, never counted against the line */
    const fg=(doc?.history?.full_game||[]).filter(g=>num(g.value));
    if(!all.length&&fg.length>=3)add(`Full game: averaged <b>${f1(fg.reduce((a,g)=>a+g.value,0)/fg.length)}</b> ${stat} over his last ${fg.length} games <small>(this line counts ${esc(r.market.period_label)} only)</small>`,0);
    /* 4. this opponent */
    const hh=doc?.h2h;
    if(hh?.opponent&&hh.n){
      const k=countOf(hh,side);
      add(`Vs ${esc(hh.opponent)}: ${Side} in <b>${k}</b> of <b>${hh.n}</b> past ${hh.n===1?'meeting':'meetings'}${num(hh.avg)?`, average <b>${f1(hh.avg)}</b>`:''}`,hh.n>=2?recDir(k,hh.n):0);
    }
    /* 5. the matchup, per sport, from the doc's own blocks */
    if(r.sport==='cs2'){
      const mc=doc?.map_chances, used=!!mc?.grade_use?.used;
      if(mc&&(mc.maps||[]).length){
        const top=mc.maps.filter(x=>num(Number(x.pct))).slice(0,3), per=new Map();
        all.forEach(g=>(g.maps||[]).forEach((name,i)=>{if(!name||!g.values||!num(g.values[i]))return;const e=per.get(name)||{n:0,t:0};e.n++;e.t+=g.values[i];per.set(name,e);}));
        const norm=n=>String(n).toLowerCase().replace(/^de_/,'').replace(/\s+/g,'');
        const mine=x=>[...per].find(([n])=>norm(n)===norm(x.name))?.[1];
        const his=top.filter(x=>mine(x)?.n).map(x=>{const e=mine(x);return `${esc(x.name)} <b>${f1(e.t/e.n)}</b> <small>(${e.n} ${e.n===1?'map':'maps'})</small>`;});
        const html=`Likely maps: ${top.map(x=>`${esc(x.name)} <b>${esc(x.pct)}%</b>`).join(' · ')}${his.length?`. His ${stat.toLowerCase()} a map there: ${his.join(' · ')}`:''}`;
        if(used)out.scout.push(fact(html,true,0));else add(html,0);
      }
      PX().cs2Facts(r,doc).forEach(x=>add(x.html,0));     /* vs similar-ranked opponents: context, weight 0 */
      const bo=r.event.best_of, days=ctxRows(doc).find(f=>f.key==='days_since_last_map')?.value;
      if(num(Number(bo))&&Number(bo)>0)add(`Best of <b>${esc(bo)}</b> · ${esc(r.market.period_label)} count for this line`,0);
      if(num(Number(days))&&Number(days)>=15)add(`Last official map <b>${esc(days)} days</b> ago`,-1);
    } else if(r.sport==='lol'){
      (doc?.context?.drivers||[]).filter(Boolean).map(d=>lolDriver(d,r,side)).filter(Boolean).slice(0,3).forEach(d=>out.scout.push(fact(d.html,true,d.dir)));
      const series=ctxVal(doc,'Series length'), counted=ctxVal(doc,'Maps counted');
      if(series)add(`${esc(series)}${counted?` · ${esc(String(counted).replace(/ added$/,''))} count for this line`:''}`,0);
      const per=ctxVal(doc,'Recent per game'), rg=ctxVal(doc,'Recent games');
      if(num(Number(per)))add(`Recent form: <b>${f1(Number(per))}</b> ${stat.toLowerCase()} a game${num(Number(rg))?` over his last ${esc(rg)} games`:''}`,0);
      const ts=ctxVal(doc,'Team win chance, Scout'), tm=ctxVal(doc,'Team win chance, market');
      if(tm&&parseFloat(tm)<50&&ts&&parseFloat(ts)>=50)add(`Betting markets make ${esc(r.player.team||'his team')} the underdog: <b>${esc(tm)}</b> to win (Scout <b>${esc(ts)}</b>)`,-1);
      else if(ts||tm)add(`${esc(r.player.team||'His team')}’s win chance: ${ts?`Scout <b>${esc(ts)}</b>`:''}${ts&&tm?' · ':''}${tm?`betting markets <b>${esc(tm)}</b>`:''}`,0);
    } else {
      /* the player's own status first, when it is definite (out / injured reserve / suspended); hedge statuses never show */
      const inj=String(ctxVal(doc,'Injury report')||''), [st,...det]=inj.split(' · '), say=DEFINITE[String(st).trim().toLowerCase()];
      if(say)add(`${esc(r.player.name)} ${say} on the injury report${det.length?` <small>(${esc(det.join(' · '))})</small>`:''}`,-1,true);
      const u=usageOf(doc,r.sport,r.market.stat);
      if(u){
        const nums=`<b>${f1(u.L3)}</b> last 3 · <b>${f1(u.L10)}</b> last 10${num(u.Season)?` · <b>${f1(u.Season)}</b> season`:''}`, drop=u.L3<u.L10*0.9, rise=u.L3>u.L10*1.1;
        if(drop||rise)add(`${esc(u.name)} ${drop?'down':'up'}: ${nums}`,(rise===(side==='over'))?1:-1);
        else add(`${esc(u.name)}: ${nums}`,0);
      }
      /* teammates out: the definite outs over the whole saved report (the server counts them); never day-to-day or questionable */
      const to=ctxRow(doc,'Teammates out');
      if(to&&Array.isArray(to.names)&&to.names.length){
        const n=Number(to.value)||to.names.length, shown=to.names.slice(0,4);
        add(`Teammates out: ${shown.map(esc).join(', ')}${n>shown.length?` and ${n-shown.length} more`:''}`,0);
      } else if(!to){
        /* a doc saved before the server counted them: only the rows it lists, worded as a partial list (no count) */
        const rows=ctxRows(doc), i=rows.findIndex(f=>f.label==='Teammates on the injury report');
        const outs=i<0?[]:rows.slice(i+1,i+7).filter(f=>f.group==='Matchup'&&/^(out|injured_reserve|suspended)$/i.test(String(f.value))).map(f=>f.label);
        if(outs.length)add(`Teammates listed out include ${outs.slice(0,4).map(esc).join(', ')}`,0);
      }
      if(ctxVal(doc,'Back-to-back')==='Yes')add('Second game of a back-to-back',-1);
      const venue=ctxVal(doc,'Venue');
      if(venue&&r.player.opponent)add(`${venue==='home'?'Home':'Away'} game vs ${esc(r.player.opponent)}`,0);
      /* the factor engine's rows are context (weight 0): said so in the words, never listed as Scout's reasons */
      const eng=ctxRows(doc).filter(f=>f.group&&(f.group.startsWith('Matchup numbers')||f.group==='Model inputs')).slice(0,2);
      if(eng.length)add(`Matchup numbers Scout’s forecast does not use: ${eng.map(f=>`${esc(f.label)} <b>${esc(factText(f.value))}</b>`).join(' · ')}`,0);
    }
    /* 6. the line itself: moves since it opened, then where other books and the market sit */
    const from=r.offer.moved_from;
    const opened=(doc?.history?.line_history?.apps||[]).find(a=>a.platform===r.offer.platform)?.points?.find(p=>p.kind==='open'&&num(p.line))?.line;
    const was=from!=null?Number(from):num(opened)&&opened!==line?opened:null;
    if(was!=null&&Number.isFinite(line)&&was!==line){
      const against=side==='over'?line>was:line<was;
      add(`Line moved ${line>was?'up':'down'} from <b>${esc(was)}</b> to <b>${esc(r.offer.line)}</b> on ${esc(r.offer.platform_label)} <small>(${against?'harder':'easier'} for the ${Side})</small>`,against?-1:1);
    }
    const others=C.siblings(r).filter(x=>x.offer.platform!==r.offer.platform||x.offer.line!==r.offer.line).slice(0,4);
    if(others.length)add(`Other lines: ${others.map(x=>`${esc(x.offer.platform_label)}${kindOf(x)?' '+esc(kindOf(x)):''} <b>${esc(x.offer.line)}</b>`).join(' · ')}`,0);
    const priceFact=(p,src)=>{const even=Math.abs(p-.5)<.02;   /* within 2 points of even: the prices take no side */
      add(`${even?'Betting prices are close to even':p<.5?'Betting prices lean the other way':'Betting prices agree'}: <b>${pct(p)}</b> ${Side} <small>(${src})</small>`,even?0:p<.5?-1:1);};
    const mk=doc?.market;
    if(mk?.available&&num(mk.p_over)&&num(mk.p_under))priceFact(side==='over'?mk.p_over:mk.p_under,esc(plainSource(mk.source||'betting market')));
    else {
      /* one sportsbook at this exact line: a no-cut price first; a prediction market's midpoint says it is a midpoint */
      const same=(doc?.market_books?.same||[]).filter(x=>num(x.chance)), b=same.find(x=>x.kind==='devig')||same.find(x=>x.kind==='mid');
      if(b&&Number(b.line)===line)priceFact(side==='over'?b.chance:1-b.chance,`${esc(b.platform_label)}${b.kind==='devig'?', with the bookmaker’s cut taken out':', midpoint price'}`);
    }
    return {lean,side,graded,...out};
  }
  function keyStatsHtml(r) {
    const line=Number(r.offer.line), games=(H.detailDoc?.history?.series||[]).filter(g=>num(g.value)).slice(0,20);
    if(!Number.isFinite(line)||!games.length)return '<p class="h-history-note h-insight-empty">'+(H.detailState==='loading'?'Loading current-line insights…':'No comparable game log for this exact stat and period. Check the Stats tab for available context.')+'</p>';
    const ten=games.slice(0,10), values=ten.map(g=>g.value), over=values.filter(x=>x>line).length, under=values.filter(x=>x<line).length, pushes=values.length-over-under;
    const avg=values.reduce((a,b)=>a+b,0)/values.length, sorted=[...values].sort((a,b)=>a-b), middle=Math.floor(values.length/2), median=values.length%2?sorted[middle]:(sorted[middle-1]+sorted[middle])/2;
    const last5=values.slice(0,5), previous5=values.slice(5,10), mean=xs=>xs.reduce((a,b)=>a+b,0)/xs.length;
    const tile=(label,value,sub)=>`<div><span>${label}</span><b>${value}</b><small>${sub}</small></div>`;
    const streakSide=values[0]>line?'Over':values[0]<line?'Under':'Push';let streak=0;
    for(const value of values){if((value>line?'Over':value<line?'Under':'Push')!==streakSide)break;streak++;}
    const trend=previous5.length===5?`Last 5 average <b>${f1(mean(last5))}</b> vs <b>${f1(mean(previous5))}</b> in the previous 5.`:'';
    return `<div class="h-insight-stats" aria-label="Key stats at the current line">${tile('Over '+esc(line),over+'/'+values.length,'Last '+values.length+' results')}${tile('Under '+esc(line),under+'/'+values.length,pushes+' '+(pushes===1?'push':'pushes')+' kept separate')}${tile('Average / median',f1(avg)+' / '+f1(median),esc(r.market.period_label))}${tile('Recent range',f1(sorted[0])+'–'+f1(sorted[sorted.length-1]),'Observed results, not a forecast')}</div><details class="h-insight-more"><summary>More supporting stats</summary><p>${trend}</p><p>Average result was <b>${f1(Math.abs(avg-line))}</b> ${avg>=line?'above':'below'} today’s line. Latest sequence: <b>${streak} ${streakSide}${streak>1?' results':' result'}</b> at this line.</p><small>Uses the same stat and period as this prop. Historical counts are not a predicted chance; a streak does not make the next result due.</small></details>`;
  }
  function whyHtml(r) {
    const f=whyFacts(r), {lean,side,graded}=f, Side=sideWord(side);
    if(!f.scout.length&&!f.for.length&&!f.risk.length&&!f.also.length)return `<section class="h-why"><span class="h-eyebrow">Key insights</span>${keyStatsHtml(r)}</section>`;
    const dot=x=>!graded?'':x.used?'<i class="h-why-dot is-used" title="Used in the grade"></i><span class="u-sr">Used in the grade: </span>':'<i class="h-why-dot" title="Not used in the grade"></i><span class="u-sr">Not used in the grade: </span>';
    const li=x=>`<li class="${x.dir>0?'is-for':x.dir<0?'is-against':''}${graded?'':' no-dot'}">${dot(x)}<span>${x.html}</span></li>`;
    const list=(cls,xs)=>`<ul class="h-why-list ${cls}">${xs.map(li).join('')}</ul>`;
    const title=lean?`Why Scout leans ${sideWord(lean)} ${esc(r.offer.line)}`:`The numbers for ${Side} ${esc(r.offer.line)}`;
    return `<section class="h-why" aria-labelledby="hub-why-title"><span class="h-eyebrow">Key insights</span><div class="h-sec-head"><h4 id="hub-why-title">${title}</h4></div>
      ${f.scout.length?list('h-why-scout',f.scout.slice(0,4)):''}${keyStatsHtml(r)}
      ${f.for.length?`<h5 class="h-why-sub h-why-for-head">${f.scout.length?'Other numbers that point':'What points'} to ${Side}</h5>${list('h-why-for',f.for.slice(0,5))}`:''}
      ${f.risk.length?`<h5 class="h-why-risk-head">What could go wrong</h5>${list('h-why-risk',f.risk.slice(0,4))}`:''}
      ${f.also.length?`<h5 class="h-why-sub">Also good to know</h5>${list('h-why-also',f.also.slice(0,4))}`:''}
      ${H.detailState==='loading'?'<p class="h-history-note">Loading the rest of this player’s research…</p>':''}
      ${graded?'<p class="h-why-key"><span><i class="h-why-dot is-used"></i>Used in the grade</span><span><i class="h-why-dot"></i>Not used in the grade</span></p>':''}</section>`;
  }
  function overviewHtml(r) {
    const side=H.histSide, line=r.offer.line, doc=H.detailDoc, moves=C.movesOf?C.movesOf(doc):new Map();   /* SCOUT_LINE_MOVE_NOTES: a bumped past game says so under its row */
    const all=(doc?.history?.series||[]).filter(g=>num(g.value)), series=all.slice(0,H.histN);
    const sorted=series.map(g=>g.value).sort((a,b)=>a-b), mid=Math.floor(sorted.length/2);
    const median=sorted.length?(sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2):null;
    const tile=(value,label,extra='',cls='')=>`<div class="h-tile${cls}"><b>${value}</b><span>${label}</span>${extra}</div>`;
    /* card-and-chips 2026-11-06: no result on file at any window (e.g. a Q1 line whose period has no saved results) = no row of
       three dashes; the Why section names the full-game log instead */
    const noTiles=[5,10,20].every(n=>!windowTally(r,all,n)&&!(n!==10&&C.hitsPending(r)));
    const hitTiles=noTiles?'':[5,10,20].map(n=>{
      const t=windowTally(r,all,n), pending=!t&&n!==10&&C.hitsPending(r);
      return tile(t?`${countOf(t,side)}<em>/${t.n}</em>`:pending?'<i class="h-hit-wait" aria-hidden="true"></i>':'—',`L${n} · ${sideWord(side)} ${esc(line)}`,t&&t.n<n?`<small>${t.n} ${t.n===1?'game':'games'}${t.push?` · ${t.push} ${t.push===1?'push':'pushes'}`:''}</small>`:t?.push?`<small>${t.push} ${t.push===1?'push':'pushes'}</small>`:'',H.histN===n?' is-window':'');
    }).join('');
    const hh=doc?.h2h, h2hTile=hh?.opponent&&hh.n?tile(`${countOf(hh,side)}<em>/${hh.n}</em>`,`H2H vs ${esc(hh.opponent)} · ${sideWord(side)}`):'';
    const se=COLS?C.tallies(r).season:null, seasonTile=se?.n?tile(`${countOf(se,side)}<em>/${se.n}</em>`,`Season · ${sideWord(side)} ${esc(line)}`):'';
    /* L10 vs closing (SCOUT_CLOSING_LINES): only when the server sent past games with a closing line saved at lock */
    const cl=doc?.history?.closing, clCount=cl?.n?(side==='under'?cl.under:cl.over):null;
    const closingTile=cl?.n?tile(`${clCount}<em>/${cl.n}</em>`,`L10 vs closing · ${sideWord(side)}`,`<small>${cl.n} ${cl.n===1?'game':'games'} with a closing line${cl.push?` · ${cl.push} ${cl.push===1?'push':'pushes'}`:''}</small>`,' is-closing'):'';
    const closingTable=cl?.n?`<details class="h-history-table h-closing-table"><summary>L10 vs closing lines${cl.platform_label?` · ${esc(cl.platform_label)}`:''}</summary><div class="h-scroll-x"><table><thead><tr><th>Date</th><th>Opponent</th><th>Result</th><th>Closing line</th><th>At closing</th></tr></thead><tbody>${(cl.games||[]).map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.opponent)}</td><td>${esc(g.value)}</td><td>${esc(g.closing)}</td><td class="h-at-${esc(String(g.at).toLowerCase())}">${esc(g.at)}</td></tr>${movedRow(g.move,5)}`).join('')}</tbody></table></div></details>`:'';
    const chart=H.detailState==='loading'?'<p class="h-detail-loading">Loading this player’s saved results…</p>'
      :H.detailState==='error'?''
      :series.length?`<div class="h-history-chart" data-hub-chart role="img" aria-label="${series.length} past results compared with ${side} ${esc(line)}"></div><div class="h-legend"><span><i class="hit"></i>${sideWord(side)} hit</span><span><i class="miss"></i>Miss</span><span><i class="push"></i>Push</span><span><i class="line"></i>Line ${esc(line)}</span>${median!=null?`<span><i class="median"></i>Median ${esc(Math.round(median*10)/10)}</span>`:''}</div><details class="h-history-table"><summary>View dates and results</summary><div class="h-scroll-x"><table><thead><tr><th>Date</th><th>Opponent</th><th>Result</th><th>At ${esc(line)}</th></tr></thead><tbody>${series.map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.opponent)}</td><td>${esc(g.value)}</td><td class="h-at-${atLine(g.value,line).toLowerCase()}">${atLine(g.value,line)}</td></tr>${movedRow(moves.get(g.date+'|'+g.opponent),4)}`).join('')}</tbody></table></div></details>`
      :'<p class="h-detail-loading">Game-by-game results are not available for this market. The counts above come from its saved board summary.</p>';
    const lh=doc?.history?.line_history, avgClose=num(lh?.avg_closing_l10)?tile(esc(lh.avg_closing_l10),'Avg closing line',`<small>last ${lh.closing_n_l10} ${lh.closing_n_l10===1?'game':'games'} · ${esc(lh.closing_platform)}</small>`,' is-closing'):'';
    return `${whyHtml(r)}${NEXT&&PX().grade?PX().grade(r,doc,pxCtx(r)):''}${r.extra?extraOffer(r):''}<div class="h-history-summary">${hitTiles}${h2hTile}${seasonTile}${closingTile}${avgClose}</div>
      <section class="h-history-panel"><div class="h-sec-head"><h4>Last ${series.length||H.histN} results · ${sideWord(side)} ${esc(line)}</h4><div class="h-history-controls"><div class="h-segment" role="group" aria-label="History window">${[5,10,20].map(n=>`<button data-hub="history-window" data-id="${n}" aria-pressed="${H.histN===n}">L${n}</button>`).join('')}</div></div></div>
        ${series.length?`<p class="h-history-stats"><span>Average <b>${(series.reduce((a,g)=>a+g.value,0)/series.length).toFixed(1)}</b></span>${V2?`<span>Median <b>${median.toFixed(1)}</b></span>`:''}<span>${series.length} ${series.length===1?'game':'games'}${series.length<H.histN?' on file':''}</span></p>`:''}
        ${chart}<p class="h-history-note">History uses today’s selected line, not the closing lines. Past hit rate is not a predicted chance.</p>${closingTable}</section>
      ${lineHistHtml(doc?.history?.line_history)}
      ${h2hHtml(hh,line,side,moves)}${P('report')&&!r.extra?'<p class="h-report-row"><button class="h-link" data-hub="report-open">Report a problem with this line</button></p>':''}`;
  }
  /* Lines: the card's Compare lines and the old drawer's Books table in one place: every book and line for this prop, its prices,
     the Scout read, when the offer was seen; then the market books and the market chance when the server has them */
  function linesHtml(r) {
    const doc=H.detailDoc, quotes=C.siblings(r);
    const quoteText=(q,sd)=>{
      const o=q.offer[sd];
      if(!o.offered)return 'Not offered';
      const P=C.PLATFORMS[q.offer.platform]||{},kind=P.kind;
      if(P.price_comparable===false)return num(o.price)?(P.price_label||'App price')+' '+o.price.toFixed(2):'Listed';   /* Dabble: its own unit, never odds */
      if(kind==='decimal')return num(o.payout)?o.payout.toFixed(2):'Odds unavailable';
      if(num(o.price))return o.price.toFixed(2);
      if(num(o.payout)&&o.payout!==1)return o.payout.toFixed(2)+'× modifier';
      return q.market.line_type==='standard'?'Standard':C.lineLabel(q);
    };
    const mb=doc?.market_books, extra=mb?(mb.same||[]).filter(b=>!quotes.some(q=>q.offer.platform===b.platform&&q.offer.line===r.offer.line)):[], other=mb?.other||[];
    const price=v=>v==null?'—':esc(v);
    const mrow=(b,showLine)=>`<tr><td>${esc(b.platform_label)}${showLine?` <small>${esc(b.line)}</small>`:''}</td><td>${price(b.over)}</td><td>${price(b.under)}</td><td>${num(b.chance)?pct(b.chance):'—'}</td></tr>`;
    const market=doc?.market?(doc.market.available?`<p class="h-history-note">Market chance from ${esc(doc.market.source||'the market')}: Over ${pct(doc.market.p_over)}, Under ${pct(doc.market.p_under)}.</p>`:doc.market.reason?`<p class="h-history-note">${esc(doc.market.reason)}</p>`:''):'';
    /* site audit 2026-11-06 N8: every book the Best line count names is listed here too - the books that are not a board line of
       this prop (Sleeper, Dabble) come from the market-edge build's book list, with the same price text */
    const base=p=>String(p||'').split(':').pop().toLowerCase(), have=new Set([...quotes.map(q=>base(q.offer.platform)),...extra.map(b=>base(b.platform)),...other.map(b=>base(b.platform))]);
    const more=MX?(bestOf(r)?.bk||[]).filter(([,,bk])=>!have.has(base(bk))):[];
    const side=v=>v==null?'Not offered':v||'Listed';
    const moreRows=more.map(([label,line,,o,u])=>`<tr><td><span>${esc(label)}</span> <b>${esc(line)}</b></td><td>${esc(side(o))}</td><td>${esc(side(u))}</td><td>—</td><td>—</td></tr>`).join('');
    return `${PX().odds(r,doc,pxCtx(r))}${MX?bestLinesHtml(r):''}<section class="h-line-shop"><div class="h-sec-head"><h4>Books and lines</h4><small>Same event · stat · period</small></div><div class="h-scroll-x"><table><thead><tr><th>Book / line</th><th>Over</th><th>Under</th><th>Scout</th><th>Seen</th></tr></thead><tbody>${quotes.map(q=>`<tr class="${key(q)===H.detail?'is-selected':''}"><td><button data-hub="detail-line" data-id="${esc(key(q))}" aria-pressed="${key(q)===H.detail}"><span>${esc(q.offer.platform_label)}${kindOf(q)?` <small>${esc(kindOf(q))}</small>`:q.market.line_type!=='standard'?` <small>${esc(C.lineLabel(q))}</small>`:''}</span><b>${esc(q.offer.line)}</b></button></td><td>${esc(quoteText(q,'over'))}</td><td>${esc(quoteText(q,'under'))}</td><td>${C.oneSided(q)?esc(C.oneSided(q)):q.scout.hold?'Research estimate':esc(C.verdictText(q))}${num(q.scout.p_side)?` <b>${pct(q.scout.p_side)}</b>`:''}</td><td title="${esc(q.offer.seen_at||'')}">${q.offer.seen_at?esc(age(q.offer.seen_at)):'—'}</td></tr>`).join('')}${moreRows}</tbody></table></div>
      <p class="h-history-note">Choose a line to recount its past results. Each line is its own pick with its own forecast; payouts and availability differ.</p>
      ${extra.length||other.length?`<div class="h-sec-head"><h4>Market books</h4></div><div class="h-scroll-x"><table><thead><tr><th>Book</th><th>Over</th><th>Under</th><th>Its chance</th></tr></thead><tbody>${extra.map(b=>mrow(b,false)).join('')}${other.map(b=>mrow(b,true)).join('')}</tbody></table></div>`:''}${market}</section>`;
  }
  /* SCOUT_MARKET_EDGE on the card's Lines tab: the best line and price for each side across every book, the discrepancy chip, and
     this line's market edge (the books' chance minus the entry's break-even) for both sides the server measured */
  function bestLinesHtml(r) {
    const b=bestOf(r), x=mxRow(r);
    if(!mxDoc(r))return '';
    const cell=(sd)=>{const v=b?.[sd];if(!v)return `<tr><th scope="row">${sideWord(sd)}</th><td colspan="3">No book lists this side now</td></tr>`;
      return `<tr><th scope="row">${sideWord(sd)}</th><td><b>${esc(v.line)}</b> <small>${esc(v.label)}</small></td><td>${esc(v.price||(v.book==='underdog'?'Standard':v.book==='prizepicks'?'Power 2-pick':'—'))}</td><td>${num(v.needs)?pct(v.needs):'—'}</td></tr>`;};
    const edges=[x?.fav,x?.scout].filter(v=>v&&num(v.p));
    const edgeLine=v=>`<li><b class="h-medge${num(v.edge)&&v.edge>0?' positive':''}">${sideWord(v.side)} ${num(v.edge)?ptsText(v.edge)+' pts':'—'}</b> <span>${esc(mxSay(v))}${v===x?.scout?' (Scout’s side)':''}.</span></li>`;
    const why=x?.fav?'':`<p class="h-history-note">${esc(mxWhy(r))}</p>`;
    return `<section class="h-best-lines"><div class="h-sec-head"><h4>Best line per side</h4>${MX?gapChip(r):''}</div>
      <div class="h-scroll-x"><table><thead><tr><th>Side</th><th>Best line</th><th>Price</th><th>Needs</th></tr></thead><tbody>${cell('over')}${cell('under')}</tbody></table></div>
      <p class="h-history-note">Across ${b?.n||1} ${b?.n===1?'book':'books'}: the Over's best line is the lowest, the Under's the highest; at the same line, the payout that needs the lowest chance to break even. Payouts and rules differ by app.</p>
      <div class="h-sec-head"><h4>Market edge</h4><small>books vs break-even</small></div>${edges.length?`<ul class="h-medge-list">${edges.map(edgeLine).join('')}</ul><p class="h-history-note">The books’ number, not Scout’s grade.</p>`:why}</section>`;
  }
  function statsTabHtml(r) {
    const doc=H.detailDoc, all=(doc?.history?.series||[]).filter(g=>num(g.value));
    const full=['nba','nfl'].includes(r.sport)&&r.player.id!=null?`<p class="h-full-research"><a href="${esc(`/sports?view=classic#/player/${encodeURIComponent(r.player.id)}?lg=${r.sport}&stat=${encodeURIComponent(r.market.stat)}`)}" target="_blank" rel="noopener">Full player research ↗</a></p>`:'';
    if(H.detailState==='loading')return full+'<p class="h-detail-loading">Loading this player’s saved stats…</p>';
    const body=PX().factors(r,doc)+statsHtml(r,doc,all)+(r.sport==='cs2'?mapChancesHtml(doc?.map_chances):'');
    return full+(body||'<p class="h-detail-loading">No saved stats are on file for this player and market.</p>');
  }
  /* Ask: the Genie (SCOUT_GENIE) answers one question about this line from its own payload, every number checked by the server;
     the evidence guide sits folded underneath (open when the Genie is off) */
  /* seq: one number per question; a reply whose seq is no longer current is dropped (bug sweep 2026-11-06 #13: switching side or line
     mid-question left busy=true for good and the Genie said 'Checking…' on every line until a reload) */
  const G={key:null,q:'',busy:false,ans:null,err:null,quota:null,seq:0};
  const SUGGEST=['What is the projection?','How often did this line hit?','What is the head-to-head?','Is there a better line elsewhere?'];
  function askHtml(r) {
    const side=H.histSide, k=key(r)+'|'+r.offer.revision+'|'+side;
    if(G.key!==k){Object.assign(G,{key:k,ans:null,err:null,busy:false});G.seq++;}
    const genie=R.me.features?.genie===true, s=r.scout, t=C.tallies(r).l10, other=C.siblings(r).filter(x=>x.offer.platform!==r.offer.platform), chance=C.chanceOf(r,side);
    // SCOUT_\u{1F9DE}_PERSONA (features.genie_persona): a light genie voice in the intro and the answer opener; the checked sentences are unchanged
    const persona=genie&&R.me.features?.genie_persona===true;
    const out=G.busy?'<p class="h-detail-loading">Checking this line’s saved numbers…</p>':G.err?`<p>${esc(G.err)}</p>`:G.ans?(persona&&G.intro?`<p class="h-genie-intro">${esc(G.intro)}</p>`:'')+G.ans.map(x=>`<p>${esc(x)}</p>`).join(''):'';
    const form=genie?`${persona?`<p class="h-genie-intro">\u{1F9DE} Ask the Genie. I grant answers from this line's saved Scout data only, never a made-up stat.</p>`:''}<form class="h-ask" id="hub-ask" novalidate><label for="hub-ask-q">${persona?'Make a wish about this line':'Ask about this line'}</label><div class="h-ask-row"><input id="hub-ask-q" type="text" maxlength="300" autocomplete="off" value="${esc(G.q)}"><button type="submit"${G.busy?' aria-disabled="true"':''}>${G.busy?'Checking…':'Ask'}</button></div>
      <div class="h-ask-sugg" role="group" aria-label="Suggested questions">${SUGGEST.map(x=>`<button type="button" data-hub="genie-ask" data-id="${esc(x)}"${G.busy?' aria-disabled="true"':''}>${esc(x)}</button>`).join('')}</div>
      <div class="h-ask-out" id="hub-ask-out" role="status" aria-live="polite">${out}</div>${G.quota?`<p class="h-history-note">${Number(G.quota.remaining_questions)} of ${Number(G.quota.questions_limit)} questions left today.</p>`:''}</form>`:'';
    return `${form}<details class="h-guide"${genie?'':' open'}><summary>Scout evidence guide</summary><p class="h-history-note">Answers use this offer's saved forecast and research data only.</p>
      <h5>What supports this pick?</h5><p>${esc(r.player.name)} has a saved projection of ${num(s.projection)?esc(s.projection.toFixed(1)):'unavailable'} for ${esc(r.market.period_label)} ${esc(r.market.stat_label)}. ${chance==null?'No probability was saved for the selected side.':`The saved chance for ${sideWord(side)} ${esc(r.offer.line)} is ${pct(chance)}.`}${s.hold?' This line has no published grade.':''}</p>
      <h5>How often did this line hit?</h5><p>${t?.n?`${countOf(t,side)} of the last ${t.n} results hit ${sideWord(side)} at today's line of ${esc(r.offer.line)}; ${t.push||0} pushed.`:'Comparable past-game results are not available for this line.'} <button class="h-link" data-hub="card-tab" data-id="overview">See each result</button></p>
      <h5>Is there a better line elsewhere?</h5><p>${other.length?other.slice(0,8).map(x=>`${esc(x.offer.platform_label)} lists ${esc(x.offer.line)} (${esc(x.market.line_type)}), seen ${esc(when(x.offer.seen_at))}.`).join(' '):'No other platform offer is available for this exact event, player, stat and period.'} <button class="h-link" data-hub="card-tab" data-id="lines">Compare lines</button></p>
      <h5>What should I verify before tracking?</h5><p>Confirm ${esc(r.market.period_label)}, the ${esc(r.offer.platform_label)} line and offered side, the start time, and any player context. Scout rechecks the exact revision when you track.${s.stake_reason?' '+esc(s.stake_reason):''} <button class="h-link" data-hub="card-tab" data-id="stats">See saved stats</button></p></details>`;
  }
  async function genieAsk(text) {
    const r=rec(H.detail);if(!r||G.busy)return;
    const box=$('hub-ask-q'), q=(text||(box?box.value:'')||'').trim();
    G.q=q;
    if(!q){G.err='Type a question about this line first.';paintBody();return;}
    const tok=++G.seq;
    Object.assign(G,{busy:true,err:null,ans:null});paintBody();
    try {
      const d=await api('genie',{sport:r.sport,book:r.offer.platform,id:r.offer.offer_id,revision:r.offer.revision,side:H.histSide,question:q});
      if(G.seq!==tok)return;
      G.ans=Array.isArray(d.sentences)?d.sentences:[];G.quota=d.quota||null;G.intro=typeof d.intro==='string'?d.intro:null;
    } catch(e) {
      if(G.seq!==tok)return;
      const c=e.status||0;
      if(c===429&&e.doc?.quota)G.quota=e.doc.quota;
      G.err=c===429?(e.doc?.error==='daily_limit_reached'?(R.me.features?.genie_persona?"\u{1F9DE} Your wishes are spent for today. They reset at midnight UTC.":"You have used today's questions. They reset at midnight UTC."):'Too many questions in a minute. Try again shortly.')
        :c===409?'This line changed. Reopen it to ask about the current offer.':c===413?'Keep the question under 300 characters.':'The Genie is unavailable right now.';
    } finally {if(G.seq===tok){G.busy=false;paintBody();}}
  }
  function tabHtml(r) {
    const warn=H.detailState==='error'?`<p class="h-warning">This offer changed or research is unavailable. <button data-hub="detail-retry">Retry research</button> <button data-hub="refresh">Refresh board</button></p>`:'';
    return warn+(H.cardTab==='lines'?linesHtml(r):H.cardTab==='stats'?statsTabHtml(r):H.cardTab==='ask'?askHtml(r):overviewHtml(r));
  }
  function paintDetail() {
    const box=$('hub-detail'),r=rec(H.detail);if(!box||!r||!cardOpen())return;
    const restoreFocus=focusAfter(box), body=$('hub-card-body'), top=body?body.scrollTop:0, wasTab=box.dataset.tab, wasId=box.dataset.id;
    const playerMarkets=[...new Map(H.records.filter(x=>x.sport===r.sport&&x.player.key===r.player.key&&x.event.id===r.event.id&&x.offer.platform===r.offer.platform&&!C.started(x)).map(x=>[x.market.stat+'|'+x.market.period,x])).values()];
    const watched=H.watch.includes(playerKey(r));
    box.innerHTML=`<header class="h-card-head"><button class="h-back" data-hub="card-close" aria-label="Back to the board"><span aria-hidden="true">←</span> Back</button>
        ${faceOf(r,48)}<div class="h-card-id"><h3 id="hub-card-title" tabindex="-1">${esc(r.player.name)} <span>${esc(r.market.stat_label)} · ${esc(r.market.period_label)}</span></h3><p>${r.player.role?`<i class="h-role">${esc(r.player.role)}</i>`:''}${esc(r.player.team)} · ${esc(r.event.label)}${compOf(r)?` · ${esc(compOf(r))}`:''} · ${esc(when(r.event.start_utc))} ${countdownHtml(r)}${r.offer.moved_from!=null?` · moved from ${esc(r.offer.moved_from)}`:''}</p></div>
        <button class="h-star" data-hub="watch" data-id="${esc(key(r))}" aria-label="Watch ${esc(r.player.name)}" aria-pressed="${watched}">${watched?'★':'☆'}</button></header>
      ${playerMarkets.length>1?`<nav class="h-player-markets" aria-label="Player markets">${playerMarkets.map(x=>`<button data-hub="player-market" data-id="${esc(key(x))}" aria-pressed="${x.market.stat===r.market.stat&&x.market.period===r.market.period}">${esc(x.market.stat_label)} <b>${esc(x.offer.line)}</b><small>${esc(x.market.period_label)}</small></button>`).join('')}</nav>`:''}
      <div class="h-decision" id="hub-decision" role="group" aria-label="Your pick">${decisionHtml(r)}</div>
      <div class="h-card-tabs" role="tablist" aria-label="Player research">${CARD_TABS.map(([t,l])=>`<button role="tab" id="hub-tab-${t}" data-hub="card-tab" data-id="${t}" aria-selected="${H.cardTab===t}" aria-controls="hub-card-body" tabindex="${H.cardTab===t?0:-1}">${l}</button>`).join('')}</div>
      <div class="h-card-body" id="hub-card-body" role="tabpanel" aria-labelledby="hub-tab-${H.cardTab}" tabindex="0">${tabHtml(r)}</div>`;
    box.dataset.tab=H.cardTab;box.dataset.id=H.detail;
    if(wasTab===H.cardTab&&wasId===H.detail)$('hub-card-body').scrollTop=top;
    if(H.cardTab==='overview')drawChart();
    restoreFocus();
  }
  function paintStrip() {
    const el=$('hub-decision'), r=rec(H.detail);
    if(!el||!r||!cardOpen())return;
    const restore=focusAfter(el);el.innerHTML=decisionHtml(r);restore();
  }
  function paintBody() {
    const el=$('hub-card-body'), r=rec(H.detail);
    if(!el||!r||!cardOpen())return;
    const restore=focusAfter(el), had=document.activeElement?.id==='hub-ask-q';
    el.innerHTML=tabHtml(r);
    if(H.cardTab==='overview')drawChart();
    restore();if(had)$('hub-ask-q')?.focus({preventScroll:true});
  }
  /* "On slip ✓" shows where the pick is: the slip column on desktop, the Slip tab on phone. Remove lives in the slip itself. */
  function revealSlip(r) {
    const leg=T.legs.find(l=>l.key===r.key&&l.book===r.offer.platform);
    if(!DESKTOP.matches||H.section==='studio'){if(cardOpen())closeCard(false);H.studioTab='slip';section('studio');}
    const box=H.section==='studio'?$('hub-studio-body'):$('hub-slip-content');
    const el=leg&&box?.querySelector(`[data-u="leg-x"][data-id="${CSS.escape(leg.id)}"]`)?.closest('.u-leg');
    if(el){el.scrollIntoView({block:'nearest'});el.classList.add('h-flash');setTimeout(()=>el.classList.remove('h-flash'),1600);}
    C.say(`${r.player.name} is on your slip.`);
  }
  /* My picks: the member's own tracked receipts, pending first, then settled, grouped by the slip they were tracked on.
     Won and lost are counted together; pushes and voids are counted apart. No units, no returns. */
  function paintPicks() {
    const box=$('hub-picks-body');if(!box||H.section!=='picks')return;
    const filters=$('hub-picks-section').querySelector('.h-pick-filters');
    if(!T.mine){filters.hidden=true;box.innerHTML=C.picksTab();return;}
    const all=T.mine.receipts||[];
    filters.hidden=!all.length;
    if(!all.length){box.innerHTML='<div class="h-empty"><h3>No tracked picks yet</h3><p>Track a slip to save the exact line and chance you saw. Each pick settles here.</p><button data-hub="section" data-id="board">Find a player prop</button></div>';return;}
    const list=all.filter(r=>(H.pickSport==='all'||r.sport===H.pickSport)&&(H.pickStatus==='all'||r.status===H.pickStatus));
    if(!list.length){box.innerHTML='<div class="h-empty"><h3>No picks in this view</h3><p>Change the sport or outcome above to see your other tracked picks.</p><button data-hub="pick-clear">Show all picks</button></div>';return;}
    const n=st=>list.filter(r=>r.status===st).length, won=n('hit'),lost=n('miss'),push=n('push'),voids=n('void'),pending=n('pending');
    const parts=[won+lost?`<b>${won}–${lost}</b> won–lost`:'',push?`${push} ${push===1?'push':'pushes'} counted apart`:'',voids?`${voids} void`:'',pending?`${pending} pending`:''].filter(Boolean);
    const slips=T.mine.slips||[], ids=new Set(slips.map(t=>t.slip_id));
    const groups=slips.map(t=>({t,legs:list.filter(r=>r.slip_id===t.slip_id)})).filter(g=>g.legs.length);
    const loose=list.filter(r=>!ids.has(r.slip_id));
    if(loose.length)groups.push({t:null,legs:loose});
    const card=({t,legs})=>{
      const c=st=>legs.filter(r=>r.status===st).length, bits=[c('hit')&&`${c('hit')} won`,c('miss')&&`${c('miss')} lost`,c('push')&&`${c('push')} ${c('push')===1?'push':'pushes'}`,c('void')&&`${c('void')} void`,c('pending')&&`${c('pending')} pending`].filter(Boolean).join(' · ');
      const head=t?`${esc((C.PLATFORMS[t.platform]||{}).label||t.platform)}${t.entry_type?` · ${esc(String(t.entry_type).replace(/^./,x=>x.toUpperCase()))}`:''}`:'Other tracked picks';
      return `<section class="h-picks-group"><header><h4>${head}</h4><span>${t?`Tracked ${esc(when(t.tracked_at))} · `:''}${esc(bits)}</span></header>${legs.map(C.receiptHtml).join('')}</section>`;
    };
    const open=groups.filter(g=>g.legs.some(r=>r.status==='pending')), done=groups.filter(g=>!g.legs.some(r=>r.status==='pending'));
    box.innerHTML=`<p class="h-pick-summary">Your tracked picks: ${parts.join(' · ')}</p>
      ${open.length?`<section class="h-picks-part"><h3>Pending <span>${open.length} ${open.length===1?'slip':'slips'}</span></h3>${open.map(card).join('')}</section>`:''}
      ${done.length?`<section class="h-picks-part"><h3>Settled <span>${done.length} ${done.length===1?'slip':'slips'}</span></h3>${done.map(card).join('')}</section>`:''}
      <p class="h-picks-foot"><button class="h-link" data-hub="section" data-id="record">The model's record →</button></p>`;
  }
  /* Slips page: three tabs. My slip (the working draft) is the default; Auto-build holds the generator; Tracked holds past slips */
  function paintStudio() {
    const box=$('hub-studio-body');if(!box||H.section!=='studio')return;
    const sec=$('hub-studio-section');
    sec.querySelectorAll('[data-hub="studio-tab"]').forEach(b=>{const on=b.dataset.id===H.studioTab;b.setAttribute('aria-selected',String(on));b.tabIndex=on?0:-1;});
    sec.querySelectorAll('[data-studio-panel]').forEach(p=>{p.hidden=p.dataset.studioPanel!==H.studioTab;});
    const a=document.activeElement,u=box.contains(a)?a.dataset.u:null,id=u?a.dataset.id:null;
    box.innerHTML=C.slipTab().replace('data-u="tab" data-id="picks">View receipts','data-hub="receipts">View in My picks');
    if(!T.legs.length&&!T.tracked)box.innerHTML='<div class="h-empty"><h3>Your slip is empty</h3><p>Choose a prop from any sport, review the line, then add your side. Or let Auto-build make slips for you.</p><div class="h-empty-acts"><button data-hub="section" data-id="board">Browse player props</button><button data-hub="studio-tab" data-id="build">Auto-build slips</button></div></div>';
    if(u)box.querySelector(`[data-u="${CSS.escape(u)}"]${id==null?'':`[data-id="${CSS.escape(id)}"]`}`)?.focus({preventScroll:true});
    if($('hub-slip-history'))$('hub-slip-history').innerHTML=C.picksTab();
    $('hub-studio-tab-n').textContent=String(T.legs.length);
    $('hub-studio-sync').textContent=T.saveFail?'Not yet synced. Your changes remain on this device.':T.pending||T.saving?'Saving your changes…':'Saved to your account · available on your other devices';
  }
  async function generate() {
    if(H.generating)return;
    H.generating=true;
    const button=$('hub-generate'), status=$('hub-generation-status');
    button.disabled=true;button.textContent='Checking live lines…';status.textContent='';
    try {
      const sports=[...document.querySelectorAll('#hub-generation-sports input:checked')].map(el=>el.value);
      if(!sports.length)throw new Error('Choose at least one sport.');
      await Promise.allSettled(sports.map(s=>C.loadBoard(s,true)));
      H.records=allowed().flatMap(s=>[...R.boards[s].recs.values()]);
      const pool=H.records.filter(r=>sports.includes(r.sport)&&!unavailableReason(r)&&!C.started(r));
      const options={platform:$('hub-generation-book').value,count:Number($('hub-generation-slips').value),size:Number($('hub-generation-count').value),entryType:$('hub-generation-type').value,
        mix:$('hub-generation-mix').value,games:$('hub-generation-games').value,noRepeatProps:$('hub-generation-repeats').value==='none',lines:$('hub-generation-lines').value,
        research:$('hub-generation-research')?.value==='include',side:$('hub-generation-side').value,sports};
      const batch=await C.generateBatch(pool,options,text=>{status.textContent=text;});
      if(!batch.slips.length)throw new Error(batch.note||'No combinations passed recheck. Widen your settings.');
      H.batch=batch;H.batchSaved=false;paintBatch();
      await saveBatch();
      status.textContent=`${batch.slips.length} of ${options.count} slips generated and rechecked. ${batch.note||'Review them below; your slip is unchanged.'}`;
    } catch(e){status.textContent=e.status?'Could not verify current offers. Your draft was kept. Try again.':e.message;}
    finally{H.generating=false;button.disabled=false;button.textContent='Generate slips';}
  }
  async function saveBatch() {
    try {
      for(let attempt=0;attempt<3;attempt++) {
        const state=await api('state');
        try {await api('state',{rev:state.rev,studio:H.batch});H.batchSaved=true;break;}
        catch(e){if(e.status!==409||attempt===2)throw e;}
      }
    } catch(e){H.batchSaved=false;}
    paintBatch();
  }
  function paintBatch() {
    const box=$('hub-generated');if(!box)return;
    const b=H.batch, slips=b?.slips||[];
    if(!slips.length){box.innerHTML='<div class="h-empty"><h3>Your generated slips appear here</h3><p>Choose how many slips and how many picks per slip. Your slip stays separate.</p></div>';return;}
    const use=new Map();slips.forEach(s=>s.legs.forEach(l=>{const k=l.sport+'|'+l.player_key;use.set(k,(use.get(k)||0)+1);}));
    const lineTag=l=>l.book==='prizepicks'&&C.lineKind?` <em class="h-line-tag">${esc(C.lineKind('prizepicks',l.gen?.line_type))}</em>`:l.gen?.line_type&&l.gen.line_type!=='standard'?` <em class="h-line-tag">${esc(l.gen.line_type[0].toUpperCase()+l.gen.line_type.slice(1))}</em>`:'';
    const legFace=l=>C.faceHtml?C.faceHtml(l.sport,{key:l.player_key,name:l.player,team:(R.boards[l.sport]?.recs.get(C.offerKey(l.book,l.row_id))?.player.team_abbr)||l.team,size:32,cls:'h-face'}):'';
    box.innerHTML=`<header class="h-batch-head"><div><span class="h-eyebrow">GENERATED LINEUPS</span><h2>${slips.length} separate slips</h2><p>${H.batchSaved?'Saved to your account':'Not yet saved across devices'} · generated ${esc(when(b.created_at))}${b.options?.noRepeatProps?' · No repeat props':''}${{no_goblins:' · No goblins',one_goblin:' · At most 1 goblin',standard:' · No goblins or demons'}[b.options?.lines]||''}</p></div>${!H.batchSaved?'<button data-hub="save-batch">Retry save</button>':''}</header><p class="h-batch-note">Ranked by what each chance band has won on settled results. The chance beside a leg is the model’s published chance; all-hit chance multiplies the ranked leg chances across different matches, and a push counts as not winning. Repeated players across slips share risk. Every slip is rechecked when opened.</p><div class="h-batch-grid">${slips.map((s,i)=>{
      const metrics=[num(s.all_hit)?`<span>All-hit chance <b>${pct(s.all_hit)}</b></span>`:(s.same_match?'<span>Legs share a match <b>No all-hit chance</b></span>':''),
        s.payout&&num(s.payout.multiple)?`<span>All-hit payout <b>${esc(s.payout.multiple)}×</b></span>`:'',
        s.ev&&num(s.ev.ev)?`<span>Expected return <b>${pct(s.ev.ev)}</b></span>`:''].filter(Boolean).join('');
      return `<article class="h-generated-card"><header><span>SLIP ${String(i+1).padStart(2,'0')}</span><strong>${esc(C.PLATFORMS[s.platform].label)} · ${esc(s.entry_type)}</strong><small>${s.legs.length} picks · ${esc([...new Set(s.legs.map(l=>SPORTS[l.sport]))].join(' + '))}</small></header>${s.why?`<p class="h-batch-why">${esc(s.why)}</p>`:''}${metrics?`<div class="h-batch-range">${metrics}</div>`:''}<ol>${s.legs.map(l=>`<li${['nba','nfl'].includes(l.sport)?' class="h-leg-face"':''}>${legFace(l)}<div><strong>${esc(l.player)}</strong>${l.gen?.research?' <em class="h-line-tag">Research estimate</em>':''}<small>${esc(SPORTS[l.sport])} · ${esc(l.event_label)}</small><span>${esc(l.side)} <b>${esc(l.line)}</b> ${esc(l.stat_label)} · ${esc(l.period_label)}${lineTag(l)}</span>${l.gen?.record?`<small class="h-batch-record">${esc(l.gen.record)}</small>`:''}<small>${use.get(l.sport+'|'+l.player_key)} of ${slips.length} slips use this player</small></div><b>${pct(l.shown_p)}</b></li>`).join('')}</ol><footer><button data-hub="use-generated" data-id="${i}">${T.legs.some(l=>l.book===s.platform)?'Replace the '+esc(C.PLATFORMS[s.platform].label)+' entry in My slip':'Open in My slip'}</button>${R.me.features?.book_links?`<button data-hub="open-generated" data-id="${i}">Open in ${esc(C.PLATFORMS[s.platform].label)}</button>`:""}<small id="hub-batch-message-${i}" role="status"></small></footer></article>`;
    }).join('')}</div>`;
  }
  async function useGenerated(id) {
    const s=H.batch?.slips?.[Number(id)];if(!s||H.usingBatch)return;
    H.usingBatch=true;
    try {await C.useGenerated(s);paintBatch();$('hub-batch-message-'+id).textContent='Rechecked and opened in My slip.';H.studioTab='slip';refreshSlip();C.say('Rechecked and opened in My slip.');}
    catch(e){$('hub-batch-message-'+id).textContent=e.status?'Could not recheck this slip. Your draft was kept.':e.message;}
    finally{H.usingBatch=false;}
  }
  function generatorHtml() {
    const research=allowed().some(s=>s==='nba'||s==='nfl');
    return `<section class="h-generator" aria-label="Generate slips"><span class="h-eyebrow">LINEUP GENERATOR</span><h2>Build your batch</h2><p>Choose your sports and entry size. Review each generated slip before adding it to your picks.</p><fieldset id="hub-generation-sports"><legend>Eligible sports</legend>${allowed().map(s=>`<label><input type="checkbox" value="${s}" checked><span>${SPORTS[s]}</span></label>`).join('')}</fieldset><div class="h-generator-controls">
      <div class="h-generator-step"><span>01</span><h3>Entry setup</h3></div><label>Platform<select id="hub-generation-book"><option value="prizepicks">PrizePicks</option><option value="underdog">Underdog</option></select></label>
      <label>Entry type<select id="hub-generation-type"><option value="power">Power</option><option value="flex">Flex</option></select></label>
      <label>Number of slips<select id="hub-generation-slips">${[1,2,4,8,12].map(n=>`<option value="${n}" ${n===8?'selected':''}>${n} slips</option>`).join('')}</select></label>
      <label>Picks per slip<select id="hub-generation-count">${[2,3,4,5,6].map(n=>`<option value="${n}">${n} picks</option>`).join('')}</select></label>
      <div class="h-generator-step"><span>02</span><h3>Selection rules</h3></div><label>Sport mix<select id="hub-generation-mix"><option value="any">Any mix</option><option value="mixed">Mixed sports</option><option value="single">One sport</option></select></label>
      <label class="h-goblin-rule">Goblins<select id="hub-generation-lines" aria-describedby="hub-generation-lines-help">${LINE_RULES.map(([v,t])=>`<option value="${v}"${v===H.slipLines?' selected':''}>${t}</option>`).join('')}</select><small id="hub-generation-lines-help">Goblins are easier lines that pay less. Scout leaves them out unless you allow them.</small></label>
      <label>Game selection<select id="hub-generation-games"><option value="spread">One per match</option><option value="stacks">Same match (Underdog)</option></select></label>
      <label>Prop reuse<select id="hub-generation-repeats"><option value="allow">Allow across slips</option><option value="none">No repeat props</option></select></label>
      ${research?'<label>Research estimates<select id="hub-generation-research"><option value="leave">Leave out</option><option value="include">Include, marked</option></select></label>':''}
      <details class="h-generation-more"><summary>More filters <span id="hub-generation-filter-count"></span></summary><div><label>Pick side<select id="hub-generation-side"><option value="any">Either side</option><option value="over">Overs only</option><option value="under">Unders only</option></select><small>Uses Scout’s recommended side. Picks are never flipped to fill a slip.</small></label></div></details>
      <div class="h-generation-submit"><p id="hub-generation-summary" role="status" aria-live="polite"></p><button id="hub-generate" data-hub="generate">Generate slips</button><small>Current lines are rechecked before results appear.</small></div></div><p id="hub-generation-status" role="status" aria-live="polite"></p><details><summary>How lineups are built</summary><p>Only open lines enter: the platform feed is current, the match has not started, Scout’s side is offered and the line is not held. CS2 legs carry a live grade of B or better with a stake; LoL legs carry the model’s letter; NBA and NFL research estimates stay out unless you include them, and are then marked. Each leg ranks by what its chance band (sport, stat, side and chance) has won on settled results, so a band that has not held up ranks lower. A slip takes the combination with the best all-hit chance: one leg per player, one leg per match unless you choose same-match legs on Underdog, and the platform’s entry rules. All-hit chance multiplies the leg chances across different matches; a push counts as not winning. Goblins (PrizePicks lines set below the standard line, which pay less) stay out of every slip and every suggested swap unless you allow them under Goblins: at most one per slip, or any number; No goblins or demons keeps standard lines only. A goblin you add yourself is never removed. No repeat props uses each player’s stat and period once across the batch, every line and side included; with repeats allowed, a leg already used ranks lower, and the same slip is never built twice. Every slip is rechecked against the current offer before it appears.</p></details></section>`;
  }
  /* 30 days of the record; a run of days with no decided pick folds into one quiet tile instead of a wall of dashes */
  function calendarHTML(doc){
    if(!doc?.days?.length)return '<p class="h-muted">Daily history will appear after the next Record update.</p>';
    const out=[];let run=[];
    /* follow-up 2026-11-06: every date on the calendar reads "Oct 7", the same as its header; counts take their singular */
    const md=t=>{const d=new Date(t+'T12:00:00Z');return Number.isFinite(d.getTime())?d.toLocaleDateString('en-US', {month:'short',day:'numeric',timeZone:'UTC'}):t;};
    const n_=(n,one,many)=>`${n} ${Number(n)===1?one:many}`;
    const wlp=d=>`${n_(d.wins,'win','wins')} · ${n_(d.losses,'loss','losses')} · ${n_(d.pushes,'push','pushes')}`;
    const flush=()=>{if(!run.length)return;const a=md(run[0].date),b=md(run[run.length-1].date);
      out.push(`<div class="h-cal-gap" role="note" aria-label="${esc(`${run[0].date}${run.length>1?' to '+run[run.length-1].date:''}: no decided picks`)}"><time>${esc(a)}${run.length>1?' – '+esc(b):''}</time><small>${run.length>1?`${run.length} days · `:''}none decided</small></div>`);run=[];};
    for(const d of doc.days){
      if(!d.decided&&!d.wins&&!d.losses&&!d.pushes){run.push(d);continue;}
      flush();
      out.push(`<details class="${d.decided?'has-results':''}"><summary aria-label="${esc(md(d.date))}: ${esc(wlp(d).replace(/ · /g,', '))}"><time datetime="${esc(d.date)}">${esc(md(d.date))}</time><b>${num(d.hit_rate)?Math.round(d.hit_rate*100)+'%':'—'}</b><small>${d.decided} decided</small></summary><p>${esc(wlp(d))}</p></details>`);
    }
    flush();
    const span=doc.days.length?md(doc.days[0].date)+' – '+md(doc.days[doc.days.length-1].date):'';
    const miss=Number(doc.missing_dates)||0;
    return `<section class="h-record-calendar" aria-label="30-day accuracy calendar"><header><h3>Last 30 days${span?` <small>${esc(span)}</small>`:''}</h3><span>${esc(doc.basis)}</span></header><div class="h-calendar-grid">${out.join('')}</div><p>Accuracy only, not profit. ${miss?`${miss===1?'1 record has':miss+' records have'} no saved date and ${miss===1?'is':'are'} excluded from this calendar.`:'Pushes are listed separately.'}</p></section>`;
  }
  async function showRecord() {
    const seq=++H.seq, sport=$('hub-record-sport').value;
    $('hub-record-body').innerHTML='<p class="h-empty">Loading the saved record…</p>';
    const sports=sport==='all'?allowed():[sport];
    const docs=await Promise.allSettled(sports.map(s=>api(s==='cs2'?'stated':'results?sport='+s)));
    if(seq!==H.seq||H.section!=='record') return;
    $('hub-record-body').innerHTML=(R.me.features?.public_record?'<p><a href="/record" target="_blank" rel="noopener">Open the shareable public record ↗</a></p>':'')+docs.map((res,i)=>{
      const s=sports[i];
      if(res.status!=='fulfilled') return `<article class="h-record-card"><h2>${SPORTS[s]}</h2><p>Record unavailable. <button data-hub="record-retry">Retry</button></p></article>`;
      const d=res.value,t=d.totals||{},bands=s==='cs2'?d.by_letter:d.groups?.chance;
      return `<article class="h-record-card"><header><h2>${SPORTS[s]}</h2><span>${s==='nba'||s==='nfl'?'Research forecasts':'Saved forecasts'}</span></header><div class="h-score"><div><b>${t.wins??'—'}<em> / </em>${t.losses??'—'}</b><span>Wins / losses · ${t.pushes??'—'} ${t.pushes===1?'push':'pushes'} counted apart</span></div><div><b>${pct(t.observed)}</b><span>Actual hit rate</span></div><div><b>${pct(t.printed)}</b><span>Average stated chance</span></div></div>
        <p>${esc(d.population||d.rule||'')}</p>${d.population?`<details><summary>Counting rule</summary><p>${esc(d.rule)}</p></details>`:''}
        ${bands?.length?`<div class="h-reliability"><div class="h-rel-head"><span>Tier / sample</span><span>Stated ○ · Actual ●</span></div>${bands.map(b=>`<div class="h-rel-row"><span><b>${esc(b.name)}</b><small>${b.picks??0} picks</small></span><div class="h-rel-scale">${num(b.printed)?`<i class="stated" style="left:${Math.max(0,Math.min(100,b.printed*100))}%"></i>`:''}${num(b.observed)?`<i class="actual" style="left:${Math.max(0,Math.min(100,b.observed*100))}%"></i>`:''}</div><span>${pct(b.printed)} / ${pct(b.observed)}</span></div>`).join('')}<p class="h-axis">0%<span>50%</span>100%</p></div>`:''}
        ${calendarHTML(d.calendar)}<footer>${num(t.wins)&&num(t.losses)?t.wins+t.losses:'—'} decided picks · ${t.matches??'—'} ${esc(t.match_word||'matches')} · Updated ${esc(d.as_of?when(d.as_of):'time unavailable')}${t.ci?` · 95% interval ${pct(t.ci[0])}–${pct(t.ci[1])}`:''}</footer></article>`;
    }).join('');
  }

  /* By game: the board's own view of the slate (#/games). It reads the board's current filters; a game opens the list filtered to it */
  function paintGames(list=rows()) {
    const box=$('hub-games-body');if(!box)return;
    const games=new Map();
    list.forEach(r=>{const k=gameKey(r);if(!games.has(k))games.set(k,[]);games.get(k).push(r);});
    const ordered=[...games.entries()].sort((a,b)=>Date.parse(a[1][0].event.start_utc)-Date.parse(b[1][0].event.start_utc));
    const hidden=!ordered.length&&!H.loading?new Set(currentLines().filter(r=>H.sport==='all'||r.sport===H.sport).map(gameKey)).size:0;
    box.innerHTML=ordered.length?ordered.map(([k,rs])=>{const r=rs[0],players=new Set(rs.map(x=>x.player.key)),markets=new Set(rs.map(x=>x.market.stat)),books=new Set(rs.map(x=>x.offer.platform_label));return `<article class="h-game-card"><header><span class="h-eyebrow">${eventTag(r)}</span><time>${esc(when(r.event.start_utc))} ${countdownHtml(r)}</time></header><h2>${esc(r.event.label)}</h2><p>${plural(players.size,'player')} · ${plural(markets.size,'market')} · ${plural(rs.length,'line')}</p><div class="h-game-books">${[...books].map(b=>`<span>${esc(b)}</span>`).join('')}</div><button data-hub="game-board" data-id="${esc(k)}">Show this game's props <span aria-hidden="true">→</span></button></article>`;}).join('')
      :H.loading?'<div class="h-empty"><b class="h-empty-title">Loading games…</b></div>'
      :hidden?`<div class="h-empty"><b class="h-empty-title">${hidden} ${hidden===1?'game':'games'} hidden by filters</b><button data-hub="clear-filters">Clear filters</button></div>`
      :'<div class="h-empty"><b class="h-empty-title">No upcoming games</b><button data-hub="refresh">Refresh</button></div>';
  }
  /* Tools: the one index of everything that is not the board. Alerts / Live highlight are managed right here; SCOUT_MARKET_TOOLS adds the EV & arbitrage sheet. */
  function paintTools() {
    const sports=allowed(), card=(eye,title,text,links)=>`<article><span class="h-eyebrow">${eye}</span><h2>${title}</h2><p>${text}</p><div class="h-tool-links">${links}</div></article>`;
    const a=(href,label)=>`<a href="${esc(href)}">${esc(label)} <span aria-hidden="true">↗</span></a>`;
    const leagues=['nba','nfl'].filter(s=>sports.includes(s));
    const classic=[sports.includes('cs2')&&a('/?view=classic#board','CS2 classic board'),sports.includes('lol')&&a('/lol?view=classic','LoL classic board'),leagues.length&&a('/sports?view=classic#/board','NBA / NFL classic board')].filter(Boolean).join('');
    $('hub-tools-body').innerHTML=`<div class="h-tool-grid">${card('MARKETS','Moneylines','Compare available game markets in the moneyline workspace.',a('/?view=classic#moneylines','Open moneylines'))}
      ${card('RESEARCH','Single prop grader','Inspect a player and line with the grading tools.',a('/?view=classic#one','Open grader'))}
      ${card('RESEARCH','Match research','Team and map research for an upcoming CS2 match.',a('/?view=classic#matches','Open match research'))}
      ${leagues.length?card('NBA · NFL','Sheets and Compare','Stat sheets, player comparison and with/without splits from the sports pages.',leagues.map(l=>a(`/sports?view=classic#/sheets?lg=${l}`,`${SPORTS[l]} sheets`)+a(`/sports?view=classic#/compare?lg=${l}`,`${SPORTS[l]} compare`)).join('')):''}
      ${classic?card('CLASSIC','Classic boards','The original boards, with every original filter.',classic):''}${EVB?card("PICK'EM PRICES",'Best prices',"Pick'em legs whose chance from the books beats what the entry needs.",'<button data-hub="section" data-id="ev">Open best prices</button>'):''}${R.me.features?.market_tools?card('PRICES','Price check','Prices that pay more than they should, and lines where two books let you bet both sides and win either way.','<button data-hub="mt-jump">Open price check ↓</button>'):''}</div>
      <section class="h-tools-alerts" id="hub-tools-alerts" aria-label="${ALERT_WORD}"></section>`;
    if(P('prefs'))$('hub-tools-body').insertAdjacentHTML('afterbegin',`<section class="h-prefs" aria-labelledby="hub-prefs-title"><h2 id="hub-prefs-title">Preferences</h2><p>How prices and sizes read on your board and player cards. Scout’s grades and stakes stay the same.</p>
      <div class="h-prefs-grid"><label>Odds format<select id="hub-pref-odds"><option value="american">American (−137)</option><option value="decimal">Decimal (1.73)</option></select></label>
      <label>Bet sizing<select id="hub-pref-sizing">${SIZING.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label>
      <label>Bankroll in $ (optional)<input id="hub-pref-bankroll" type="number" min="1" max="10000000" step="1" inputmode="decimal" placeholder="Sizes show as % of bankroll"></label></div></section>`);
    if(leagues.length){const box=document.createElement('section');box.className='h-market-tools';box.id='hub-game-prices';$('hub-tools-body').prepend(box);gameBooks(box,leagues[0]);}
    if(P('prefs')){$('hub-pref-odds').value=H.par.odds;$('hub-pref-sizing').value=H.par.sizing;$('hub-pref-bankroll').value=H.par.bankroll??'';}
    if(R.me.owner)$('hub-tools-body').insertAdjacentHTML('beforeend',`<section class="h-market-tools"><div class="h-section-head"><div><span class="h-eyebrow">OWNER REVIEW</span><h2>Grading comparison</h2><p>Published chances beside the candidate grader, from saved forecast revisions.</p></div></div><div class="h-game-books">${['cs2','lol','nba','nfl'].map(s=>`<button data-hub="grade-review" data-id="${s}">${SPORTS[s]}</button>`).join('')}</div><div id="hub-grade-review" aria-live="polite"><p>Select a sport to inspect its comparison log.</p></div></section>`);
  }
  document.head.insertAdjacentHTML('beforeend',`<style id="hub-book-style">
#u-hub .h-book-panel{min-width:0}#u-hub .h-book-controls{display:flex;align-items:end;flex-wrap:wrap;gap:12px;padding:12px 0;border-bottom:1px solid var(--u-line)}
#u-hub .h-book-controls label{color:var(--u-text);display:grid;gap:6px;font-size:12px;min-width:130px}#u-hub .h-book-controls :is(select,button){min-height:44px;max-width:100%;font:inherit}#u-hub .h-book-count{margin-left:auto;align-self:center;font-size:13px;color:var(--u-muted)}
#u-hub .h-book-status{font-size:12px;color:var(--u-muted);line-height:1.6}#u-hub .h-book-game{border:1px solid var(--u-line);margin:10px 0;background:var(--u-panel,var(--u-bg));border-radius:2px;min-width:0}
#u-hub .h-book-game>summary{display:flex;align-items:center;gap:16px;padding:14px;cursor:pointer;list-style:none;min-height:64px}#u-hub .h-book-game>summary::-webkit-details-marker{display:none}#u-hub .h-book-game>summary::after{content:'+';margin-left:auto;font-size:20px;flex:none;transform:none;border:0;width:auto;height:auto}#u-hub .h-book-game[open]>summary::after{content:'−'}
#u-hub .h-book-match{display:grid;gap:6px;min-width:0;flex:1}#u-hub .h-book-match strong{overflow-wrap:anywhere;font-size:15px}#u-hub .h-book-match strong span{color:var(--u-muted);font-weight:400}#u-hub .h-book-match time,#u-hub .h-book-seen{font-size:12px;color:var(--u-muted)}#u-hub .h-book-seen small{display:block;margin-top:4px}
#u-hub .h-book-game .h-table-wrap{overflow:auto;margin:0;border-top:1px solid var(--u-line)}#u-hub .h-book-table{width:100%;min-width:510px;border-collapse:collapse;font-variant-numeric:tabular-nums}#u-hub .h-book-table :is(th,td){padding:12px;text-align:left;white-space:nowrap}#u-hub .h-book-table small{display:block;color:var(--u-muted);font-size:11px;margin-top:4px}#u-hub .h-book-table caption{padding:10px 12px;text-align:left;font-size:11px;color:var(--u-muted);white-space:normal}#u-hub .h-book-empty{padding:24px 0}#u-hub .h-book-empty h3{margin:0 0 8px}
@media(max-width:600px){#u-hub .h-book-controls label{flex:1;min-width:110px}#u-hub .h-book-controls button{flex:1}#u-hub .h-book-game>summary{flex-wrap:wrap;gap:8px}#u-hub .h-book-seen{flex-basis:100%;order:2}#u-hub .h-book-seen small{display:inline;margin-left:8px}#u-hub .h-book-game>summary::after{order:1}#u-hub .h-book-controls select{width:100%}#u-hub .h-book-table{min-width:0;table-layout:fixed}#u-hub .h-book-table :is(th,td){padding:10px 6px;white-space:normal;overflow-wrap:anywhere;font-size:11px}#u-hub .h-book-table th:first-child{width:32%}#u-hub .h-book-table thead th{font-size:9px}#u-hub .h-book-table small{font-size:10px}}
#u-hub .h-reference-props{margin-top:32px;padding-top:24px;border-top:1px solid var(--u-line)}#u-hub .h-reference-props>p{max-width:760px;font-size:13px;line-height:1.6;color:var(--u-muted)}#u-hub .h-reference-props h3 small{font-size:12px;font-weight:400;color:var(--u-muted)}#u-hub .h-reference-props>label{display:grid;gap:8px;color:var(--u-text);font-size:12px;max-width:420px}#u-hub .h-reference-props input{width:100%;box-sizing:border-box;min-height:44px;padding:10px 12px;background:var(--u-bg);color:var(--u-text);border:1px solid var(--u-line);border-radius:4px}#u-hub .h-reference-props tbody th{text-transform:none;letter-spacing:normal;font-size:13px}
</style>`);
  function referenceProps(events){
    const rows=events.flatMap(e=>(e.reference_props||[]).map(p=>({...p,event:e})));
    if(!rows.length)return '';
    const periods={game:'Full game','1h':'First half','2h':'Second half','1q':'First quarter'};
    return `<section class="h-reference-props"><h3>DraftKings player lines <small>via ESPN</small></h3><p>DraftKings prices reported by ESPN. ESPN does not identify which price belongs to Over or Under, so prices are listed without assigning a side. These remain separate from executable book comparisons.</p><label>Find a player or stat <input type="search" data-gb-search placeholder="Search player, points, yards…" aria-label="Search DraftKings reference props"></label><p data-gb-reference-count role="status">${rows.length} reference lines</p>${events.filter(e=>e.reference_props?.length).map(e=>`<details class="h-book-game"><summary>${esc(e.away)} at ${esc(e.home)} · ${e.reference_props.length} lines</summary><div class="h-table-wrap" tabindex="0" role="region" aria-label="DraftKings reference lines"><table class="h-table h-book-table"><thead><tr><th>Player</th><th>Stat / period</th><th>Threshold</th><th>Reported odds<small>Side not supplied</small></th><th>Last observed</th></tr></thead><tbody>${e.reference_props.map(p=>`<tr data-gb-reference="${esc((p.player+' '+p.label+' '+p.market).toLowerCase())}"><th scope="row">${esc(p.player)}</th><td>${esc(p.label)}<small>${esc(periods[p.period]||p.period)}</small></td><td>${esc(p.kind==='milestone'?(p.line+0.5)+'+':p.line)}</td><td>${(p.reported_prices||[]).length?(p.reported_prices||[]).map(n=>esc(n>0?'+'+n:n)).join(' / '):'&mdash;'}</td><td>${esc(when(p.seen_at))}</td></tr>`).join('')}</tbody></table></div></details>`).join('')}</section>`;
  }
  async function gameBooks(box,league,book='draftkings'){
    const request=box._request=(box._request||0)+1;
    const focus=box.contains(document.activeElement)?document.activeElement.dataset.gb:null;
    const expanded=new Set([...box.querySelectorAll('details[open][data-event]')].map(e=>e.dataset.event));
    box.classList.add('h-book-panel');
    box.setAttribute('aria-busy','true');
    box.innerHTML='<p role="status">Loading sportsbook prices…</p>';
    try{
      const d=await api('game-books?league='+encodeURIComponent(league));
      if(!box.isConnected||request!==box._request)return;
      const age=(Date.now()-Date.parse(d.observed_at))/1000,stale=d.stale||!Number.isFinite(age)||age>1800||age< -60;
      const events=(d.events||[]).filter(e=>Date.parse(e.start_utc)>Date.now()&&e.books&&typeof e.books==='object').sort((a,b)=>Date.parse(a.start_utc)-Date.parse(b.start_utc));
      const books=new Map();events.forEach(e=>Object.values(e.books).filter(Boolean).forEach(b=>books.set(b.id,b.label)));
      // An unavailable selection must not silently display "All books" while filtering to no rows.
      if(book!=='all'&&!books.has(book)&&books.size)book='all';
      if(!books.size)books.set('draftkings','DraftKings');
      H.gameBookLeague=league;H.gameBook=book;
      const price=n=>Number.isFinite(n)?esc(n>0?'+'+n:n):'—';
      const spread=(m,s)=>Number.isFinite(m?.[s+'Spread'])&&Number.isFinite(m?.[s+'Odds'])?`${price(m[s+'Spread'])} <small>${price(m[s+'Odds'])}</small>`:'—';
      const total=(m,s)=>Number.isFinite(m?.total)&&Number.isFinite(m?.[s+'Odds'])?`${s==='over'?'Over':'Under'} ${esc(m.total)} <small>${price(m[s+'Odds'])}</small>`:'—';
      const selected=events.filter(e=>book==='all'||e.books[book]);
      const quoteState=e=>{const age=Date.now()-Date.parse(e.seen_at);return !Number.isFinite(age)||age< -60000?'Quote age unavailable':age>1800000?'Older prices':'Collected recently';};
      box.innerHTML=`<div class="h-section-head"><div><span class="h-eyebrow">NBA · NFL / GAME MARKETS</span><h2>Game lines</h2><p>DraftKings moneylines, spreads and totals via ${esc(d.source||'ESPN')}. These are full-game prices, separate from player props.</p></div></div>
      <div class="h-book-controls"><label>League <select data-gb="league">${allowed().filter(s=>['nba','nfl'].includes(s)).map(s=>`<option value="${s}"${s===league?' selected':''}>${SPORTS[s]}</option>`).join('')}</select></label>
      <label>Book <select data-gb="book"><option value="all"${book==='all'?' selected':''}>All books</option>${[...books].map(([id,label])=>`<option value="${esc(id)}"${id===book?' selected':''}>${esc(label)}</option>`).join('')}</select></label><button data-gb="refresh">Refresh prices</button><span class="h-book-count">${plural(selected.length,'game')}</span></div>
      <p class="h-book-status" role="status">${d.pending?'Waiting for the first collected prices.':stale?'Older snapshot — recheck prices at your book.':'Snapshot built '+esc(when(d.observed_at))+'.'} Books’ own update times are not supplied. Availability varies by location.</p>
      <div class="h-book-games">${selected.map(e=>`<details class="h-book-game" data-event="${esc(e.id)}"${expanded.has(e.id)?' open':''}><summary><span class="h-book-match"><strong>${esc(e.away)} <span>at</span> ${esc(e.home)}</strong><time>${esc(when(e.start_utc))}</time></span><span class="h-book-seen">${esc(quoteState(e))}<small>${Number.isFinite(Date.parse(e.seen_at))?'Seen '+esc(when(e.seen_at)):'No collection timestamp'}</small></span></summary><div class="h-table-wrap" tabindex="0" role="region" aria-label="${esc(e.away+' at '+e.home+' prices')}"><table class="h-table h-book-table"><caption>American odds · full game · totals apply to both teams combined</caption><thead><tr><th scope="col">Book / team</th><th scope="col">Moneyline</th><th scope="col">Spread</th><th scope="col">Game total</th></tr></thead><tbody>${Object.values(e.books).filter(b=>b&&(book==='all'||b.id===book)).map(b=>['away','home'].map((s,i)=>`<tr><th scope="row"><small>${esc(b.label)}</small>${esc(e[s])}</th><td>${price(b.markets?.['money-line']?.[s+'Odds'])}</td><td>${spread(b.markets?.spread,s)}</td><td>${total(b.markets?.totals,i?'under':'over')}</td></tr>`).join('')).join('')}</tbody></table></div></details>`).join('')||`<div class="h-book-empty"><h3>${d.pending?'Prices are being collected':'No upcoming game prices'}</h3><p>${d.pending?'Check back after collection finishes.':'Try another league or refresh the saved prices.'}</p></div>`}</div>`;
      box.insertAdjacentHTML('beforeend',referenceProps(events));
      box.oninput=e=>{if(!e.target.matches('[data-gb-search]'))return;const term=e.target.value.trim().toLowerCase();let count=0;box.querySelectorAll('[data-gb-reference]').forEach(row=>{row.hidden=!row.dataset.gbReference.includes(term);if(!row.hidden)count++;});box.querySelectorAll('.h-reference-props details').forEach(group=>{group.hidden=![...group.querySelectorAll('[data-gb-reference]')].some(row=>!row.hidden);if(term&&!group.hidden)group.open=true;});box.querySelector('[data-gb-reference-count]').textContent=count+' matching reference lines';};
      box.onchange=e=>{if(e.target.dataset.gb)gameBooks(box,box.querySelector('[data-gb="league"]').value,box.querySelector('[data-gb="book"]').value);};
      box.onclick=e=>{if(e.target.closest('[data-gb="refresh"]'))gameBooks(box,league,book);};
      if(focus)box.querySelector('[data-gb="'+focus+'"]')?.focus({preventScroll:true});
    }catch(e){if(box.isConnected&&request===box._request){box.innerHTML='<h2>Game lines</h2><p role="status">Sportsbook prices are temporarily unavailable.</p><button data-gb="retry">Retry</button>';box.onclick=e=>{if(e.target.closest('[data-gb="retry"]'))gameBooks(box,league,book);};if(focus)box.querySelector('button').focus({preventScroll:true});}}
    finally{if(request===box._request)box.setAttribute('aria-busy','false');}
  }
  function gradeSteps(side){
    if(!side)return '<p>No saved comparison for this side.</p>';
    return `<details><summary>Why this band</summary><p>Raw model ${pct(side.raw)} → market blend ${pct(side.blended)} → calibrated ${pct(side.p)}.</p><p>${side.market==null?'No matching market probability was used.':'Market '+pct(side.market)+'; model weight '+pct(side.blend_weight)+'.'} Calibration: ${esc(side.calibration||'not recorded')}.</p><p>${side.grade?.break_even==null?'No verified entry break-even.':'Entry break-even '+pct(side.grade.break_even)+'.'}</p>${(side.grade?.reasons||[]).map(reason=>`<p>${esc(reason)}</p>`).join('')}</details>`;
  }
  async function showGradeReview(sport,before){
    const box=$('hub-grade-review');if(!box)return;
    box.textContent='Reading saved comparisons…';
    const request=H.gradeReviewRequest=(H.gradeReviewRequest||0)+1;
    try{
      const d=await api('grade-review?sport='+encodeURIComponent(sport)+(before?'&before='+encodeURIComponent(before):''));
      if(request!==H.gradeReviewRequest||!box.isConnected)return;
      box.innerHTML=`<p><b>${esc(SPORTS[sport])}</b> · ${d.started_at?'Capturing since '+esc(when(d.started_at)):'No comparisons captured yet'}. Candidate grades do not change published picks.</p>${d.rows?.length?`<div class="h-table-wrap"><table class="h-table"><thead><tr><th>Player / event</th><th>Market / line</th><th>Published</th><th>Candidate Over</th><th>Candidate Under</th><th>Saved</th></tr></thead><tbody>${d.rows.map(r=>`<tr><td>${esc(r.player)}<small>${esc(r.event)}</small></td><td>${esc(r.stat)} ${esc(r.line)}<small>${esc(r.period)} · ${esc(r.book)}</small></td><td>${esc(r.old_letter||'No letter')}<small>${pct(r.old_p)}</small></td>${['over','under'].map(s=>`<td>${pct(r.sides?.[s]?.p)}<small>${esc(r.sides?.[s]?.grade?.band||'No band')}</small>${gradeSteps(r.sides?.[s])}</td>`).join('')}<td>${esc(when(r.at))}<small>${esc(r.policy)}</small></td></tr>`).join('')}</tbody></table></div>`:'<p>New forecasts appear here after shadow capture is enabled and the next forecast job completes.</p>'}${d.next_before?`<button data-hub="grade-review" data-id="${esc(sport)}" data-before="${Number(d.next_before)}">Older comparisons →</button>`:''}`;
    }catch(e){if(request===H.gradeReviewRequest&&box.isConnected)box.textContent='Comparison unavailable. Select the sport again to retry.';}
  }
  // SCOUT_MARKET_TOOLS "Price check" (owner 2026-11-05: the EV & arbitrage sheet "makes no sense"): the same shared build
  // (workspace_markets.py, math in gradecore/market_tools.py) in plain words. Section A answers "which single bets pay more than
  // their fair chance?", section B "where can I bet both sides at two books and win either way?". A section with no rows is not
  // drawn; nothing at all = one line. Every number is the server's (needs, per_100, stake_per_100, return_per_100); the stake box
  // only scales them. Members get sportsbook rows only; the owner's view keeps flagged pairs, exclusions, pick'em rows and formulas.
  // Filters only hide rows. The sheet follows the hub sport tab; its Sport filter can still widen it.
  const mtAge=x=>{const t=Date.parse(x?.seen_at);return Number.isFinite(t)?Math.max(0,(Date.now()-t)/1000):null;};
  const mtLegsAge=a=>Math.max(...(a.legs||[]).map(l=>mtAge(l)??Infinity));
  const mtText=x=>[x.player,x.team,x.event,x.stat_label,x.book_label,...(x.legs||[]).map(l=>l.book_label)].join(' ');
  const mtSports=d=>Object.keys(d?.sports||{}).filter(s=>H.mt.league==='all'||H.mt.league===s);
  const mtBook=x=>x.kind!=='pickem';   /* a single bet: sportsbook or prediction market (pick'em multipliers are owner-only) */
  function mtKeep(sport,x,legs){
    const f=H.mt,books=legs?x.legs.map(l=>l.book):[x.book],sides=legs?x.legs.map(l=>l.side):[x.side];
    const age=legs?mtLegsAge(x):(mtAge(x)??Infinity);
    return (f.league==='all'||f.league===sport)&&(f.book==='all'||books.includes(f.book))&&(f.side==='all'||sides.includes(f.side))
      &&(f.fresh==='all'||age<=Number(f.fresh))&&(!f.q||textHas(mtText(x),f.q));
  }
  /* the Book list follows the Sport filter: only books with a row in the sports in view (a book with no CS2 or LoL row never shows there) */
  function mtBooks(d){
    const out=new Map();
    for(const s of mtSports(d)){const x=d.sports[s]||{};
      for(const r of [...(x.ev||[]),...(x.closest||[])].filter(r=>d.owner||mtBook(r)))out.set(r.book,r.book_label);
      for(const r of [...(x.arbitrage||[]),...(d.owner?x.flagged||[]:[])])for(const l of r.legs||[])out.set(l.book,l.book_label);}
    return [...out.entries()].sort((a,b)=>a[1].localeCompare(b[1])).map(([id,label])=>({id,label}));
  }
  function mtFollowTab(){
    const d=H.marketDoc;if(!d||H.mt.tab===H.sport)return;
    // audit 2026-11-05: CS2 and LoL have no sportsbook prices, so from their tabs the sheet opens on NBA (the Sport filter still widens it)
    const want=['cs2','lol'].includes(H.sport)&&d.sports?.nba?'nba':H.sport;
    H.mt.tab=H.sport;H.mt.league=want!=='all'&&d.sports?.[want]?want:'all';
  }
  function marketFilters(){
    const d=H.marketDoc,sports=Object.keys(d?.sports||{}),books=mtBooks(d);
    const f=H.mt;if(f.book!=='all'&&!books.some(b=>b.id===f.book))f.book='all';
    if(!['ev','chance','fresh'].includes(f.sort))f.sort='ev';
    const opt=(v,label,cur)=>`<option value="${esc(v)}"${String(v)===String(cur)?' selected':''}>${esc(label)}</option>`;
    const fresh=d?.method?.fresh_filters||[[120,'2 min'],[600,'10 min'],[1800,'30 min']];
    const more=[f.side!=='all',f.minEv!=='0',f.fresh!=='all',f.sort!=='ev'].filter(Boolean).length;
    return `<div class="h-market-filters" role="group" aria-label="Price filters"><label class="h-search"><span>Search</span><input id="hub-mt-q" type="search" value="${esc(f.q)}" placeholder="Player, market or book" aria-label="Search prices"></label>`
      +`<label>Sport<select id="hub-mt-league">${opt('all','All sports',f.league)}${sports.map(l=>opt(l,SPORTS[l]||l,f.league)).join('')}</select></label>`
      +`<label>Book<select id="hub-mt-book">${opt('all','All books',f.book)}${books.map(b=>opt(b.id,b.label,f.book)).join('')}</select></label>`
      +`<details class="h-mt-more"${H.mt.moreOpen?' open':''}><summary>More filters${more?` (${more})`:''}</summary><div class="h-mt-more-body">`
      +`<label>Side<select id="hub-mt-side">${opt('all','Both sides',f.side)}${opt('over','Over',f.side)}${opt('under','Under',f.side)}</select></label>`
      +`<label>Minimum expected<select id="hub-mt-minEv">${[['0','Any amount'],['0.01','$1 or more per $100'],['0.02','$2 or more per $100'],['0.05','$5 or more per $100']].map(([v,l])=>opt(v,l,f.minEv)).join('')}</select></label>`
      +`<label>Checked within<select id="hub-mt-fresh">${opt('all','Any time',f.fresh)}${fresh.map(([s,l])=>opt(s,'The last '+l,f.fresh)).join('')}</select></label>`
      +`<label>Sort<select id="hub-mt-sort">${[['ev','Best value'],['chance','Highest fair chance'],['fresh','Newest check']].map(([v,l])=>opt(v,l,f.sort)).join('')}</select></label>`
      +`<button type="button" id="hub-mt-dense" data-hub="market-density" aria-pressed="${f.dense}">Compact rows</button></div></details></div>`;
  }
  const mtMoney=x=>'$'+Number(x).toFixed(2);
  const mtAgo=t=>t==null||!Number.isFinite(t)?'Time unavailable':t<60?'Just now':t<3600?Math.floor(t/60)+' min ago':Math.floor(t/3600)+' h ago';
  const mtSide=s=>s==='over'?'Over':'Under';
  const mtPick=x=>`<b>${esc(x.player)}</b><small>${x.side?esc(mtSide(x.side))+' ':''}${esc(x.line)} ${esc(x.stat_label||'')}${x.period_label?' · '+esc(x.period_label):''}</small><small>${esc(x.event||'')}${x.start_utc?' · '+esc(when(x.start_utc)):''}${mtSports(H.marketDoc).length>1?' · '+esc(SPORTS[x.sport]||x.sport):''}</small>`;
  const mtPrice=(label,american)=>`<span class="h-pc-nb">${esc(label)} ${esc(american||'')}</span>`;
  const mtFrom=x=>x.chance_from==='Scout'?'from Scout':/^Other books/.test(x.chance_from||'')?'from other books'+(x.chance_from.match(/\((.*)\)/)?' ('+x.chance_from.match(/\((.*)\)/)[1]+')':''):'';
  /* owner audit 2026-11-06 (T6): a price below its fair chance, shown in the closest list: the same row, the gap in plain money */
  const mtSigned=v=>(Number(v)<0?'−':'+')+'$'+Math.abs(Number(v)).toFixed(2);
  function mtEvRow(x,near){
    return `<li class="h-pc-row"><div class="h-pc-cell h-pc-pick" data-label="Pick">${mtPick(x)}</div>`
      +`<div class="h-pc-cell" data-label="Book & price">${mtPrice(x.book_label,x.american)}${x.kind==='prediction'?'<small>Prediction market</small>':''}${H.marketDoc?.owner&&(x.flags||[]).includes('Wide market')?'<small>Wide market (owner)</small>':''}</div>`
      +`<div class="h-pc-cell h-pc-fair" data-label="Fair chance vs needed"><span class="h-pc-wide">${pct(x.p_cal)} vs ${pct(x.needs)}</span><span class="h-pc-narrow">Fair ${pct(x.p_cal)} · needs ${pct(x.needs)}</span><small>${esc(mtFrom(x))}</small>${num(x.p_push)&&x.p_push>0?`<small>Exactly ${esc(x.line)} refunds the stake (${pct(x.p_push)})</small>`:''}</div>`
      +(near?`<div class="h-pc-cell h-pc-value" data-label="Per $100 against fair"><span class="h-pc-chip h-pc-short">${mtSigned(x.per_100)}<i> / $100</i></span></div>`
        :`<div class="h-pc-cell h-pc-value" data-label="Expected per $100"><span class="h-pc-chip">+${mtMoney(x.per_100)}<i> / $100</i></span></div>`)
      +`<div class="h-pc-cell h-pc-age" data-label="Checked">${esc(mtAgo(mtAge(x)))}</div></li>`;
  }
  function mtArbRow(x){
    const leg=(l,i)=>`<div class="h-pc-cell h-pc-leg" data-label="Bet ${i+1}"><span>${esc(l.book_label)} ${esc(mtSide(l.side))} ${esc(x.line)} ${esc(l.american||'')}</span><small>stake <b data-stake-per100="${Number(l.stake_per_100)}">${mtMoney(l.stake_per_100*H.mt.stake/100)}</b></small></div>`;
    const back=(100+Number(x.return_per_100))*H.mt.stake/100;
    return `<li class="h-pc-row h-pc-arb"><div class="h-pc-cell h-pc-pick" data-label="Pick">${mtPick({...x,side:null})}</div>${(x.legs||[]).map(leg).join('')}`
      +`<div class="h-pc-cell h-pc-value" data-label="Either way you get"><span class="h-pc-chip"><b data-back-per100="${100+Number(x.return_per_100)}">${mtMoney(back)}</b> back <i data-gain-per100="${Number(x.return_per_100)}">(+${mtMoney(back-H.mt.stake)})</i></span>${NEXT?`<small class="h-pc-margin">Margin ${Number(x.return_per_100).toFixed(2)}%</small>`:''}</div>`
      +`<div class="h-pc-cell h-pc-age" data-label="Checked">${esc(mtAgo(mtLegsAge(x)))}</div></li>`;
  }
  const mtHead=cols=>`<li class="h-pc-row h-pc-head" aria-hidden="true">${cols.map(c=>`<div class="h-pc-cell">${c}</div>`).join('')}</li>`;
  function marketHTML(){
    if(H.marketLoading&&!H.marketDoc)return '<p class="h-mt-state" role="status">Checking current prices…</p>';
    if(H.marketError&&!H.marketDoc)return `<p class="h-mt-state h-warning" role="status">${esc(H.marketError)} Use Refresh prices to try again.</p>`;
    if(!H.marketDoc)return '<p class="h-mt-state">Choose Refresh prices to read the latest prices.</p>';
    const d=H.marketDoc,f=H.mt,sports=mtSports(d),owner=!!d.owner,mode=mtMode();
    const ageText=t=>t==null||!Number.isFinite(t)?'—':t<60?Math.round(t)+'s':t<5400?Math.floor(t/60)+'m':(t/3600).toFixed(1)+'h';
    const builtAge=(Date.now()-Date.parse(d.as_of))/1000;
    /* QOL 2026-11-07: a refresh that fails keeps the last good sheet on screen under a warning (it used to blank the whole section);
       the server's age limits below still drop any price or pair that is too old */
    const head=H.marketError?`<p class="h-mt-state h-warning" role="status">Prices could not be refreshed. Showing the sheet built ${Number.isFinite(builtAge)?ageText(Math.max(0,builtAge))+' ago':'earlier'}. Use Refresh prices to try again.</p>`
      :Number.isFinite(builtAge)&&builtAge>120?`<p class="h-mt-state" role="status">These prices were built ${ageText(builtAge)} ago. Refresh prices for the current sheet.</p>`:'';
    const ready=sports.filter(s=>d.sports[s]?.state!=='unavailable');
    if(sports.length&&!ready.length)return head+'<p class="h-mt-state" role="status">Prices could not be read just now. Use Refresh prices to try again.</p>';
    let ev=[],arb=[],near=[],all=0,quotes=0,lines=0;
    /* the server's own age limits, applied again on every draw: a sheet left open never shows a pair or price it would no longer
       build (review 2026-11-05). The tab also reloads the sheet when it comes back into view (see visibilitychange below). */
    const m0=d.method||{},evMax=Number(m0.ev_stale_s)||1800,arbMax=Number(m0.arb_max_age_s)||300;
    const live=r=>(mtAge(r)??Infinity)<=evMax,liveArb=r=>mtLegsAge(r)<=arbMax;
    for(const s of ready){const x=d.sports[s]||{};
      quotes+=x.quotes_checked||0;lines+=x.markets_checked||0;
      const e=mode==='arb'?[]:(x.ev||[]).filter(r=>mtBook(r)&&live(r)),a=mode==='ev'?[]:(x.arbitrage||[]).filter(liveArb);all+=e.length+a.length;
      ev.push(...e.filter(r=>mtKeep(s,r,false)&&num(r.ev)&&r.ev>=Number(f.minEv)));
      arb.push(...a.filter(r=>mtKeep(s,r,true)));
      if(mode!=='arb')near.push(...(x.closest||[]).filter(r=>mtBook(r)&&live(r)&&mtKeep(s,r,false)&&num(r.per_100)));}
    ev.sort((a,b)=>f.sort==='fresh'?(mtAge(a)??1e12)-(mtAge(b)??1e12):f.sort==='chance'?b.p_cal-a.p_cal:b.per_100-a.per_100);
    arb.sort((a,b)=>f.sort==='fresh'?mtLegsAge(a)-mtLegsAge(b):b.return_per_100-a.return_per_100||mtLegsAge(a)-mtLegsAge(b));
    const at=new Date(d.as_of),clock=Number.isFinite(at.getTime())?at.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):'';
    const one=sports.length===1&&['cs2','lol'].includes(sports[0])?sports[0]:null;
    let body='';
    if(one&&!all&&!(d.sports[one]?.sections_possible?.ev||d.sports[one]?.sections_possible?.arb))
      body=`<p class="h-mt-state h-pc-none">No sportsbook offers ${esc(SPORTS[one])} prices right now, so there is nothing to check here. ${esc(SPORTS[one])} lines are on the Board.</p>`;
    else if(!ev.length&&!arb.length&&!all&&near.length){
      /* T6: nothing pays more than its fair chance, so the sheet lists what is closest, best first, instead of one empty line */
      near.sort((a,b)=>f.sort==='fresh'?(mtAge(a)??1e12)-(mtAge(b)??1e12):f.sort==='chance'?b.p_cal-a.p_cal:b.per_100-a.per_100);
      body=`<section class="h-pc-section h-pc-near" aria-label="Closest to their fair chance"><h3>Closest to their fair chance</h3><p class="h-pc-ask">No price pays more than its fair chance right now. Checked ${quotes.toLocaleString()} prices on ${lines.toLocaleString()} lines${clock?' at '+esc(clock):''}. These come closest, best first.</p>`
        +`<ol class="h-pc-list h-pc-ev${f.dense?' is-compact':''}">${mtHead(['Pick','Book &amp; price','Fair chance vs needed','Per $100 against fair','Checked'])}${near.map(x=>mtEvRow(x,true)).join('')}</ol></section>`;}
    else if(!ev.length&&!arb.length)
      body=all?`<p class="h-mt-state h-pc-none">No prices match these filters. Clear a filter to see all ${all} rows. <button type="button" data-hub="mt-clear">Clear filters</button></p>`
        :mode==='arb'?`<p class="h-mt-state h-pc-none"><b class="h-empty-title">No arbitrage right now</b>${clock?`Last checked ${esc(clock)}.`:''}</p>`
        :`<p class="h-mt-state h-pc-none">Nothing worth acting on right now. Checked ${quotes.toLocaleString()} prices on ${lines.toLocaleString()} lines${clock?' at '+esc(clock):''}.</p>`;
    if(ev.length){const x=ev[0];
      body+=`<section class="h-pc-section" aria-label="Prices better than the fair chance"><h3>Prices better than the fair chance</h3><p class="h-pc-ask">Which single bets pay more than their real chance is worth?</p>`
        +`<p class="h-pc-example">${esc(x.book_label)} pays ${esc(x.american)} on ${esc(mtSide(x.side))} ${esc(x.line)}, which needs a ${pct(x.needs)} chance to break even${num(x.p_push)&&x.p_push>0?` (exactly ${esc(x.line)} refunds the stake)`:''}. The fair chance is ${pct(x.p_cal)}, so over many bets like this you would average about +${mtMoney(x.per_100)} per $100.</p>`
        +`<ol class="h-pc-list h-pc-ev${f.dense?' is-compact':''}">${mtHead(['Pick','Book &amp; price','Fair chance vs needed','Expected per $100','Checked'])}${ev.map(mtEvRow).join('')}</ol></section>`;}
    if(arb.length){const x=arb[0],[l1,l2]=x.legs;
      body+=`<section class="h-pc-section" aria-label="Bet both sides at two books"><h3>Bet both sides at two books</h3><p class="h-pc-ask">Where can I cover Over and Under at different books and win either way?</p>`
        +`<label class="h-pc-stake">Total stake <span>$</span><input id="hub-mt-stake" type="number" inputmode="decimal" min="1" max="100000" step="1" value="${esc(H.mt.stake)}"></label>`
        +`<p class="h-pc-example">Put <b data-stake-per100="${Number(l1.stake_per_100)}">${mtMoney(l1.stake_per_100*H.mt.stake/100)}</b> on ${esc(mtSide(l1.side))} at ${esc(l1.book_label)} ${esc(l1.american)} and <b data-stake-per100="${Number(l2.stake_per_100)}">${mtMoney(l2.stake_per_100*H.mt.stake/100)}</b> on ${esc(mtSide(l2.side))} at ${esc(l2.book_label)} ${esc(l2.american)}: either result pays about <b data-back-per100="${100+Number(x.return_per_100)}">${mtMoney((100+Number(x.return_per_100))*H.mt.stake/100)}</b> for <b data-stake-total>${mtMoney(H.mt.stake)}</b> staked.</p>`
        +`<ol class="h-pc-list h-pc-arbs${f.dense?' is-compact':''}">${mtHead(['Pick','Bet 1','Bet 2','Either way you get','Checked'])}${arb.map(mtArbRow).join('')}</ol></section>`;}
    const m=d.method||{},mins=s=>Math.round((s||0)/60);
    const method=`<details class="h-mt-method"><summary>How these numbers are worked out</summary><ul>`
      +`<li>The fair chance is Scout’s chance only where Scout’s chances have been checked against results. Otherwise it is the average of at least two other sportsbooks at the same line, with their cut removed.</li>`
      +`<li>A price is only used if it was seen in the last ${mins(m.ev_stale_s||1800)} minutes. A both-sides pair needs both prices seen in the last ${mins(m.arb_max_age_s||300)} minutes.</li>`
      +`<li>Prediction markets and pick’em apps are never used for both-sides bets.</li></ul>`
      +(owner&&m.ev?`<details class="h-mt-formulas"><summary>Formulas (owner)</summary><p>${esc(m.ev)}.</p><p>Fair chance: ${esc(m.p_cal)}</p><p>Both sides: ${esc(m.arbitrage)}. Wide market above ${Math.round((1+(m.wide_overround||.08))*100)}%.</p></details>`:'')+`</details>`;
    return head+body+method+(owner?mtOwnerHTML(d,ageText):'');
  }
  /* owner only: what members never see (flagged pairs, pick'em rows, exclusion counts), so no original detail is lost */
  function mtOwnerHTML(d,ageText){
    const tbl=(cols,rows)=>`<div class="h-table-wrap"><table class="h-table h-owner-table"><thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
    const parts=mtSports(d).map(s=>{const x=d.sports[s]||{},name=esc(SPORTS[s]||s);
      const fl=(x.flagged||[]).filter(r=>mtKeep(s,r,true)),pk=(x.ev||[]).filter(r=>r.kind==='pickem'&&mtKeep(s,r,false));
      const ex=Object.entries(x.excluded||{}),exl=Object.entries(x.excluded_lines||{});
      return `<h4>${name} <small>${(x.ev||[]).length} priced · ${(x.arbitrage||[]).length} both-sides · ${x.quotes_checked||0} prices on ${x.markets_checked||0} lines · newest ${ageText(mtAge({seen_at:x.newest_quote_at}))}${x.sections_possible?` · possible: price ${x.sections_possible.ev?'yes':'no'}, both sides ${x.sections_possible.arb?'yes':'no'}`:''}</small></h4>`
        +(fl.length?`<details class="h-mt-flagged"><summary>Flagged and left out (${fl.length})</summary>${tbl(['Player / market','Legs','Margin %','Why'],fl.map(r=>`<tr><td>${esc(r.player)}<small>${esc(r.stat_label||'')} · ${esc(r.event||'')}</small></td><td>${r.legs.map(l=>`${esc(l.book_label)} ${esc(mtSide(l.side))} ${esc(r.line)} ${Number(l.odds).toFixed(2)} · ${ageText(mtAge(l))}`).join('<br>')}</td><td class="h-num">${Number(r.margin_pct).toFixed(2)}%</td><td>${esc((r.flags||[]).join(', '))}</td></tr>`).join(''))}</details>`:'')
        +(pk.length?`<details><summary>Pick'em, not a single bet (${pk.length})</summary>${tbl(['Player / market','Book','Side / line','Multiplier','Fair chance','Per $100'],pk.map(r=>`<tr><td>${esc(r.player)}</td><td>${esc(r.book_label)}</td><td>${esc(mtSide(r.side))} ${esc(r.line)}</td><td class="h-num">${Number(r.odds).toFixed(2)}x</td><td class="h-num">${pct(r.p_cal)}</td><td class="h-num">${num(r.per_100)?'+'+mtMoney(r.per_100):'—'}</td></tr>`).join(''))}</details>`:'')
        +(ex.length?`<details><summary>Prices not rated, and why</summary>${ex.map(([k,n])=>`<p>${esc(k)}: ${Number(n)} prices</p>`).join('')}${exl.map(([k,n])=>`<p>${esc(k)}: ${Number(n)} lines</p>`).join('')}</details>`:'');}).join('');
    return `<details class="h-mt-owner"><summary>Owner check</summary>${parts}</details>`;
  }
  function mtStakeInput(e){
    if(e.target.id!=='hub-mt-stake')return;
    const v=Number(e.target.value);if(!(v>0&&v<=100000))return;
    H.mt.stake=v;const box=$('hub-market-results');
    box.querySelectorAll('[data-stake-per100]').forEach(b=>{b.textContent=mtMoney(Number(b.dataset.stakePer100)*v/100);});
    box.querySelectorAll('[data-back-per100]').forEach(b=>{b.textContent=mtMoney(Number(b.dataset.backPer100)*v/100);});
    box.querySelectorAll('[data-gain-per100]').forEach(b=>{b.textContent='(+'+mtMoney(Number(b.dataset.gainPer100)*v/100)+')';});
    box.querySelectorAll('[data-stake-total]').forEach(b=>{b.textContent=mtMoney(v);});
  }
  const MT_CSS='#u-hub .h-market-filters{display:flex;flex-wrap:wrap;gap:8px;align-items:end;padding:12px;margin:0 0 14px;border:1px solid var(--u-line);border-radius:9px;background:var(--u-panel)}'
    +'#u-hub .h-market-filters label{display:grid;gap:4px;flex:1 1 130px;min-width:0;font-size:11px;color:var(--u-muted)}#u-hub .h-market-filters .h-search{flex:2 1 220px}'
    +'#u-hub .h-market-filters :is(input,select){width:100%;min-height:40px}#u-hub .h-market-filters button{flex:0 0 auto;min-height:40px}'
    +'#u-hub .h-mt-more{flex:1 1 100%;margin:0}#u-hub .h-market-tools details.h-mt-more>summary{min-height:40px;display:inline-flex;align-items:center;gap:6px;padding:0 12px;border:1px solid var(--u-line);border-radius:8px;cursor:pointer;color:var(--u-text);background:var(--u-bg,transparent)}'
    +'#u-hub .h-market-tools summary{list-style:none}#u-hub .h-market-tools summary::-webkit-details-marker{display:none}'
    +'#u-hub .h-market-tools summary::after{content:"";width:7px;height:7px;margin-left:8px;border-right:2px solid currentColor;border-bottom:2px solid currentColor;transform:rotate(45deg) translateY(-2px);transition:transform .15s;flex:none}'
    +'#u-hub .h-market-tools details[open]>summary::after{transform:rotate(225deg) translateY(-1px)}#u-hub .h-pc-nb{white-space:nowrap}'
    +'#u-hub .h-mt-more-body{display:flex;flex-wrap:wrap;gap:8px;align-items:end;padding-top:8px}'
    +'#u-hub .h-pc-lede{margin:0 0 12px;color:var(--u-muted)}#u-hub .h-pc-section{margin:0 0 18px}#u-hub .h-pc-section h3{margin:0 0 2px;font-size:16px}'
    +'#u-hub .h-pc-ask{margin:0 0 6px;color:var(--u-muted)}#u-hub .h-pc-example{margin:0 0 10px;padding:10px 12px;border-left:3px solid var(--u-line2);background:var(--u-panel);border-radius:0 8px 8px 0;font-size:13px}'
    +'#u-hub .h-pc-list{list-style:none;margin:0;padding:0;border:1px solid var(--u-line);border-radius:9px;overflow:hidden}'
    +'#u-hub .h-pc-row{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(0,1.4fr) minmax(0,1.3fr) minmax(0,1.1fr) minmax(0,.8fr);gap:10px;align-items:center;padding:10px 12px;border-top:1px solid var(--u-line)}'
    +'#u-hub .h-pc-row:first-child{border-top:0}#u-hub .h-pc-head{font-size:11px;color:var(--u-muted);background:var(--u-panel);padding-top:8px;padding-bottom:8px}'
    +'#u-hub .h-pc-cell{min-width:0;overflow-wrap:anywhere}#u-hub .h-pc-cell small{display:block;color:var(--u-muted);font-size:12px}#u-hub .h-pc-dec{color:var(--u-muted)}'
    +'#u-hub .h-pc-narrow{display:none}#u-hub .h-pc-chip{display:inline-block;padding:3px 8px;border-radius:6px;background:rgba(63,208,122,.12);color:#3FD07A;font-weight:600;white-space:nowrap}'
    +'#u-hub .h-pc-chip.h-pc-short{background:rgba(148,163,184,.14);color:var(--u-text)}'
    +'#u-hub .h-pc-chip i{font-style:normal;font-weight:400;color:var(--u-muted)}#u-hub .h-pc-age{color:var(--u-muted);font-size:12px}'
    +'#u-hub .h-pc-list.is-compact .h-pc-row{padding-top:5px;padding-bottom:5px;font-size:12px}#u-hub .h-pc-list.is-compact .h-pc-cell small{display:inline;margin-left:6px}'
    +'#u-hub .h-pc-stake{display:inline-flex;align-items:center;gap:6px;margin:0 0 8px;color:var(--u-muted)}#u-hub .h-pc-stake input{width:110px;min-height:40px}'
    +'#u-hub .h-mt-state{padding:12px;border:1px dashed var(--u-line);border-radius:8px;color:var(--u-muted)}#u-hub .h-mt-state.h-warning{color:var(--u-text);border-color:var(--u-line2)}'
    +'#u-hub .h-mt-method ul{margin:6px 0;padding-left:18px}#u-hub .h-mt-method li{margin:4px 0}'
    +'#u-hub .h-market-tools .h-table-wrap{overflow-x:auto;margin:6px 0 14px}#u-hub .h-owner-table{width:100%;border-collapse:collapse}#u-hub .h-owner-table small{display:block}#u-hub .h-owner-table .h-num{white-space:nowrap}'
    +'#u-hub .h-market-tools details{margin:6px 0 12px}#u-hub .h-market-tools summary{min-height:40px;display:flex;align-items:center;cursor:pointer;font-weight:600}'
    /* QOL 2026-11-07: Refresh prices kept its own height (it stretched to the heading block, ~110px tall on desktop); the both-sides
       "Either way you get" figure wraps inside its column instead of running into Checked; the no-match Clear filters is a real target */
    +'#u-hub .h-market-tools>.h-section-head>button{align-self:flex-start;min-height:40px;white-space:nowrap}'
    +'#u-hub .h-pc-arb .h-pc-value .h-pc-chip{white-space:normal}#u-hub .h-pc-none button{min-height:40px;margin-left:6px}'
    +'@media(max-width:639px){#u-hub .h-pc-head{display:none}#u-hub .h-pc-row{grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;padding:12px}'
    +'#u-hub .h-pc-row>.h-pc-cell{grid-column:1}#u-hub .h-pc-row>.h-pc-value{grid-column:2;grid-row:1;align-self:start;text-align:right}'
    +'#u-hub .h-pc-wide{display:none}#u-hub .h-pc-narrow{display:inline}#u-hub .h-pc-fair small{display:inline;margin-left:6px}'
    +'#u-hub .h-pc-arb .h-pc-leg::before{content:attr(data-label) " · ";color:var(--u-muted);font-size:12px}#u-hub .h-pc-age{font-size:11px}}';
  function paintMarketResults(){const box=$('hub-market-results');if(box)box.innerHTML=marketHTML();}
  /* N14: bring Tools' Alerts section to the top of the screen. The Price check above it fills in after its prices load and would
     push Alerts back down, so until the member scrolls, clicks or types (or 15 s pass) each Price check paint brings it back */
  function jumpAlerts(focus){
    const box=$('hub-tools-alerts');if(!box||H.section!=='tools')return;
    box.scrollIntoView({block:'start'});
    const h=box.querySelector('h3');if(focus&&h){h.tabIndex=-1;h.focus({preventScroll:true});}
  }
  const stopJump=()=>{H.alertsJump=0;};
  for(const t of ['wheel','touchmove','keydown','pointerdown'])document.addEventListener(t,stopJump,{passive:true,capture:true});
  function paintMarkets(){
    if(!R.me.features?.market_tools||!mtSection())return;
    const mode=mtMode(),host=mode==='arb'?$('hub-arb-body'):mode==='ev'?$('hub-ev-priced'):null;
    /* one Price check component: it lives in the section on screen (Tools, EV or Arbitrage) and is rebuilt when that changes */
    if($('hub-market-tools')&&$('hub-market-tools').dataset.mode!==mode)$('hub-market-tools').remove();
    if(!$('hub-market-results')){
      // flag-on styles travel with the flag-on markup, so hub.css keeps its bytes
      if(!$('hub-mt-style'))document.head.insertAdjacentHTML('beforeend',`<style id="hub-mt-style">${MT_CSS}</style>`);
      if(host)host.innerHTML=`<section class="h-market-tools h-mt-own" id="hub-market-tools" data-mode="${mode}"><div class="h-section-head"><div><h2 tabindex="-1">${mode==='arb'?'Both sides at two books':'Sportsbook prices above their fair chance'}</h2><p>${mode==='arb'?'Lines where two books let you bet Over and Under and get more back than you staked either way.':'Single bets whose price pays more than the fair chance is worth, best first.'}</p></div><button data-hub="refresh-markets">Refresh prices</button></div><div id="hub-market-filters"></div><div id="hub-market-results" aria-live="polite"></div></section>`;
      else ($('hub-tools-alerts')||$('hub-tools-body')).insertAdjacentHTML($('hub-tools-alerts')?'beforebegin':'beforeend',`<section class="h-market-tools" id="hub-market-tools" data-mode="all"><div class="h-section-head"><div><span class="h-eyebrow">PRICE RESEARCH</span><h2 tabindex="-1">Price check</h2><p>Prices that pay more than they should, and lines where two books let you bet both sides and win either way.</p></div><button data-hub="refresh-markets">Refresh prices</button></div><p class="h-pc-lede">Each row is a single bet at one book. Green is what the price pays over its fair chance, per $100.</p><div id="hub-market-filters"></div><div id="hub-market-results" aria-live="polite"></div></section>`);
      const box=$('hub-market-filters');
      const read=e=>{const id=(e.target.id||'').replace('hub-mt-','');if(!(id in H.mt)||id==='dense')return;H.mt[id]=e.target.value;if(id in MT_KEEP)mtRemember();paintMarketResults();};
      box.addEventListener('input',e=>{if(e.target.id==='hub-mt-q')read(e);});
      box.addEventListener('change',e=>{if(e.target.id!=='hub-mt-q')read(e);if(e.target.id==='hub-mt-league')paintMarkets();});
      box.addEventListener('toggle',e=>{if(e.target.classList?.contains('h-mt-more'))H.mt.moreOpen=e.target.open;},true);
      $('hub-market-results').addEventListener('input',mtStakeInput);
      if(!H.marketDoc&&!H.marketLoading&&!H.marketError)actions['refresh-markets']();
    }
    mtFollowTab();
    /* QOL 2026-11-07: while a refresh is out the button says so and does nothing (a second press sent a second request) */
    const rb=document.querySelector('#hub-market-tools [data-hub="refresh-markets"]');
    if(rb){rb.disabled=!!H.marketLoading;rb.textContent=H.marketLoading?'Checking prices…':'Refresh prices';}
    const box=$('hub-market-filters'),had=box.contains(document.activeElement)?document.activeElement.id:null;
    box.innerHTML=H.marketDoc?marketFilters():'';
    if(had)$(had)?.focus({preventScroll:true});
    paintMarketResults();
    if(H.alertsJump&&Date.now()-H.alertsJump<15000)jumpAlerts(false);
  }
  function installV2() {
    if(!V2)return;
    $('u-hub').classList.add('h-v2');
    document.querySelector('#hub-filter-pop .h-quick').insertAdjacentHTML('afterbegin',[['freshOnly','Current only'],['soonOnly','Starts within 1h'],['movedOnly','Line moved']].map(([f,label])=>`<button id="hub-${f}" data-hub="quick" data-id="${f}" aria-pressed="false">${label}</button>`).join(''));
    document.querySelector('#hub-studio-section .h-studio-intro').insertAdjacentHTML('beforeend','<button data-hub="share-card">Download lineup image</button>');
  }

  /* UI_NEXT (owner 2026-11-11: "where is EV and Arbitrage section"): both get their own screen and nav item. EV = the pick'em +EV
     board plus Price check's priced-book rows; Arbitrage = Price check's both-sides pairs. The same server rows, limits and maths:
     only where they are shown changes. A member without the feature gets no nav item (never a locked one). */
  const MT_ON=R.me.features?.market_tools===true, EV_ON=EVB||(NEXT&&MT_ON), ARB_ON=NEXT&&MT_ON;
  const mtMode=()=>NEXT&&H.section==='arb'?'arb':NEXT&&H.section==='ev'?'ev':'all';
  const mtSection=()=>H.section==='tools'||(NEXT&&MT_ON&&(H.section==='ev'||H.section==='arb'));
  const BOOKS_ON=allowed().some(s=>['nba','nfl'].includes(s));
  const SECTIONS=['board','studio','record','alerts','picks','tools',...(BOOKS_ON?['books']:[]),...(EV_ON?['ev']:[]),...(ARB_ON?['arb']:[])];
  const TITLES={books:'Sportsbooks',tools:'Tools',record:'Record',picks:'My picks',alerts:ALERT_WORD,studio:'Slips',ev:NEXT?'EV':'Best prices',arb:'Arbitrage'};
  async function paintEv(){
    const box=$('hub-ev-body');if(!box||H.section!=='ev'||!EVB)return;
    try{(await import('/upgrade/ev.js')).paint(box,{api,esc,when,SPORTS,addLeg(id,side){const r=rec(id);if(!r)return C.say('Open the board first, then add this line.');if(unavailableReason(r)||C.started(r))return C.say('This offer needs a fresh source check. Refresh the board.');if(C.onSlip(r))return revealSlip(r);if(!r.offer[side]?.offered)return openCard(id,'overview');C.addLeg(r,side);refreshSlip();C.say('Added to your slip.');}});}
    catch(e){box.innerHTML='<p class="h-mt-state">Best prices could not be loaded. Use Refresh to try again.</p>';}
  }
  function section(name,navigate=true) {
    if(name==='games'){H.view='games';name='board';}
    if(!SECTIONS.includes(name))name='board';
    if(name!=='board'&&cardOpen())closeCard(false);
    togglePop(null);
    /* QOL 2026-11-07: leaving the board for Slips, My picks, Record or Tools and coming back (tab bar, sidebar or Back) returns to the
       same place in the list; it opened at the top. A sport tab or a game link still starts at the top (they reset it after this). */
    const from=H.section,center=$('hub-center');
    if(from==='board'&&name!=='board'&&center)H.boardScroll=center.scrollTop;
    H.section=name;
    const games=name==='board'&&H.view==='games';
    if(navigate){const hash=(V2?'#/'+(games?'games':({studio:'slips',record:'results'}[name]||name)):'#scout/'+(games?'games':name))+(name==='board'&&!games?'?sport='+H.sport:'');if(location.hash!==hash)history.pushState(null,'',hash);H.routeHash=location.pathname+location.hash;}
    for(const n of SECTIONS)$('hub-'+n+'-section').hidden=n!==name;
    const here=name==='alerts'||(name==='ev'&&!NEXT)?'tools':name;
    document.querySelectorAll('#u-hub .h-sidebar nav [data-hub="section"], #u-hub .h-tabbar [data-hub="section"]').forEach(b=>b.setAttribute('aria-current',b.dataset.id===here?'page':'false'));
    $('u-hub').classList.toggle('h-studio-mode',name==='studio');
    $('u-hub').classList.toggle('h-board-mode',name==='board');
    $('hub-title').textContent=TITLES[name]||'Player props';
    if(name==='board'){paintBoard();if(from!=='board'&&H.boardScroll!=null&&center){center.scrollTop=H.boardScroll;H.boardScroll=null;}}else if(name==='studio'){paintStudio();paintBatch();C.loadMine();}else if(name==='record')showRecord();else if(name==='picks'){paintPicks();C.loadMine();}else if(name==='tools'){paintTools();paintMarkets();paintAlerts();}else if(name==='ev'){paintEv();paintMarkets();}else if(name==='arb')paintMarkets();else if(name==='books')gameBooks($('hub-book-board'),H.gameBookLeague||allowed().find(s=>['nba','nfl'].includes(s)),H.gameBook||'draftkings');else paintAlerts();
  }
  /* old Sports links (site audit 2026-11-06): /sports#/board?lg=nba opened the board on All. A route's sport= wins, then the old
     lg= (nba / nfl), then, for a bare board link on /sports, NBA as the classic sports page opened; else the tab kept */
  function routeSport(m) {
    const q=new URLSearchParams(m[2]||''),lg=q.get('lg');
    return q.get('sport')||(['nba','nfl'].includes(lg)?lg:null)||(location.pathname==='/sports'&&m[1]==='board'?'nba':null)||H.sport;
  }
  function route() {
    const query=new URLSearchParams(location.search),classic=query.get('view')==='classic'||query.has('slip')||query.has('p')||query.get('bv')==='1';
    const csBoard=/^#board(?:\/(?:all|prizepicks|underdog))?$/.test(location.hash);
    const modern=V2&&location.hash.match(/^#\/(board|games|slips|results|record|tools|picks|alerts|books|ev|arb)(?:\?(.*))?$/);
    const scout=location.hash.match(/^#scout\/(board|games|studio|picks|record|alerts|tools|books|ev|arb)(?:\?(.*))?$/),slips=/^#\/?slips(?:\?|$)/.test(location.hash);
    const mainBoard=location.pathname==='/lol'&&!location.hash || location.pathname==='/sports'&&(!location.hash||location.hash.startsWith('#/board')) || location.pathname==='/'&&(!location.hash||csBoard);
    const open=!classic&&(location.hash==='#all-sports'||!!scout||!!modern||mainBoard||slips);
    if(open&&H.routeHash!==location.pathname+location.hash){
      H.routeHash=location.pathname+location.hash;
      const wasSport=H.sport;
      H.sport=modern?routeSport(modern):scout?routeSport(scout):location.hash==='#all-sports'?'all':location.pathname==='/lol'?'lol':location.pathname==='/sports'?(location.hash.includes('lg=nfl')?'nfl':'nba'):'cs2';
      if(!allowed().includes(H.sport))H.sport='all';
      const deep=R.me.features?.background_alerts?new URLSearchParams((modern?modern[2]:scout?scout[2]:'')||'').get('open'):null;
      if(deep&&deep.length<=300)H.pendingOpen=deep;
      const routeBook=location.hash.split('/')[1];
      if(csBoard&&['all','prizepicks','underdog'].includes(routeBook))H.book=routeBook;
      const tab=new URLSearchParams((modern?modern[2]:scout?scout[2]:location.hash.split('?')[1])||'').get('tab');
      if(['slip','build','tracked'].includes(tab))H.studioTab=tab;
      /* QOL 2026-11-07: Back from Slips / My picks to the same sport keeps the market, game and page the member left (they were reset,
         so Back lost the place). A different sport starts clean as before; By game always lists every game. */
      if(H.sport!==wasSport||!H.routed){H.stat='all';H.game='all';H.page=0;}
      else if((modern||scout)?.[1]==='games')H.game='all';
      H.routed=true;
    }
    $('u-hub').hidden=!open; document.documentElement.classList.toggle('hub-open',open);
    // Original content remains mounted, but keyboard/screen readers cannot enter the hidden layer.
    for(const node of document.body.children) {
      if(node.id==='u-hub'||node.classList.contains('u-dialog')||node.classList.contains('u-toast')||node.id==='u-tray-btn'||node.tagName==='SCRIPT') continue;
      if(open&&!node.inert){node.dataset.hubInert='1';node.inert=true;}
      if(!open&&node.dataset.hubInert){node.inert=false;delete node.dataset.hubInert;}
    }
    if(!open){window.scoutClassicMount?.();return;}   /* the classic CS2 page is built only when it is shown (audit 2026-11-05) */
    const target=modern?({slips:'studio',results:'record'}[modern[1]]||modern[1]):scout?scout[1]:slips?'studio':'board';
    if(target==='board')H.view='list';
    section(target,false);syncFields();paintBoard();refreshSlip();load();
  }
  /* the Filters and Views pop-overs and the phone's More sheet: one open at a time; a click outside or Escape closes it */
  const POPS={filter:['hub-filters-toggle','hub-filter-pop'],views:['hub-views-toggle','hub-views-pop'],more:['hub-more-toggle','hub-more']};
  function togglePop(which) {
    for(const [w,[btn,pop]] of Object.entries(POPS)){const open=w===which&&$(pop).hidden;$(pop).hidden=!open;$(btn).setAttribute('aria-expanded',String(open));if(open)fitPop($(pop));}
  }
  /* N10 (site audit 2026-11-06): a pop-over anchored to a button that wrapped to the toolbar's left edge opened partly off screen
     ("ed views"). After opening, it is moved sideways just enough to sit 8px inside the window on both sides. */
  /* UI 2026-11-09 (owner: "why is filters getting blocked by picks tab on pc"): on desktop the pop-over lives inside the scrolling
     board column, so the part past that column's right edge was cut off under the Slip column (Side and Done unreachable at every
     desktop width). The room is now the board column's visible box, less an open player card, inside the window; the pop-over
     moves left (and narrows / scrolls only if the room is smaller than it) so every control stays on screen and clickable. */
  function fitPop(pop) {
    pop.style.transform='';pop.style.maxWidth='';pop.style.maxHeight='';pop.style.overflowY='';
    const pad=8,vw=document.documentElement.clientWidth,vh=document.documentElement.clientHeight,c=$('hub-center'),cr=c.getBoundingClientRect();
    let lo=Math.max(0,cr.left+c.clientLeft)+pad,hi=Math.min(vw,cr.left+c.clientLeft+c.clientWidth)-pad;
    const card=$('hub-card');
    if(!card.hidden&&getComputedStyle(pop).position!=='fixed'){const k=card.getBoundingClientRect();if(k.width&&k.left>lo+200)hi=Math.min(hi,k.left-pad);}
    if(hi-lo<120){lo=pad;hi=vw-pad;}
    if(pop.offsetWidth>hi-lo)pop.style.maxWidth=Math.floor(hi-lo)+'px';
    const r=pop.getBoundingClientRect();
    const dx=r.left<lo?lo-r.left:r.right>hi?Math.max(lo-r.left,hi-r.right):0;
    if(dx)pop.style.transform=`translateX(${Math.round(dx)}px)`;
    const room=Math.floor(vh-pad-r.top);
    if(r.bottom>vh-pad&&room>=160){pop.style.maxHeight=room+'px';pop.style.overflowY='auto';}
  }
  const openPop = () => Object.entries(POPS).find(([,[,pop]])=>!$(pop).hidden)?.[0]||null;
  window.addEventListener('resize',()=>{const w=openPop();if(w)fitPop($(POPS[w][1]));});
  const rec = id => H.records.find(r=>key(r)===id)||H.extraRecs.find(r=>key(r)===id);
  const actions={
    'share-card'(){C.shareSlip();},
    quick(id){if(!V2||!['freshOnly','soonOnly','movedOnly'].includes(id))return;H[id]=!H[id];H.page=0;paintBoard();},
    // By game keeps the board's filters: the games listed are the ones those filters show
    'game-board'(id){H.game=id;H.view='list';H.page=0;section('board');$('hub-center').scrollTop=0;},
    generate, 'use-generated':useGenerated, 'open-generated':i=>C.openGenerated(H.batch?.slips?.[Number(i)]), 'open-line':()=>C.openLine(rec(H.detail),H.histSide), 'save-batch':saveBatch,
    'player-market'(id){if(rec(id))openCard(id,H.cardTab);},
    /* QOL 2026-11-07: Done inside the pop-over hands focus back to the Filters button (it stayed on the hidden Done button) */
    filters(id,el){togglePop('filter');if(el?.closest('.h-pop'))$('hub-filters-toggle').focus({preventScroll:true});}, 'views-menu'(){togglePop('views');}, more(){togglePop('more');},
    book(id){if(!['all','prizepicks','underdog'].includes(id)&&!isExtra(id))return;if(!bookHasLines(id))return;H.book=id;H.page=0;paintBoard();if(isExtra(id))loadExtra();},
    'mt-jump'(){$('hub-market-tools')?.scrollIntoView({block:'start'});$('hub-market-tools')?.querySelector('h2')?.focus?.({preventScroll:true});},
    'clear-filters'(){Object.assign(H,{book:'all',q:'',stat:'all',side:'all',minGrade:'all',onlyDiff:false,watched:false,freshOnly:false,soonOnly:false,movedOnly:false,game:'all',page:0});paintBoard();},
    'filter-remove'(id){const defaults={q:'',book:'all',stat:'all',side:'all',minGrade:'all',onlyDiff:false,watched:false,freshOnly:false,soonOnly:false,movedOnly:false};if(!Object.hasOwn(defaults,id))return;H[id]=defaults[id];H.page=0;paintBoard();$('hub-filters-toggle').focus({preventScroll:true});},
    'focus-board'(){const on=$('u-hub').classList.toggle('h-focus-board');$('hub-focus').setAttribute('aria-pressed',String(on));$('hub-focus').textContent=on?'Show slip':'Hide slip';fitBar();},
    view(id){H.view=id==='games'?'games':'list';H.page=0;section('board');},
    /* N14 (site audit 2026-11-06): opens Tools scrolled to its Alerts section (it opened at the top of Tools) */
    'manage-alerts'(){togglePop(null);section('tools');H.alertsJump=Date.now();jumpAlerts(true);},
    section(id){if(id==='board')H.view='list';if(id==='studio')H.studioTab='slip';section(id);},
    'studio-open'(id){H.studioTab=['slip','build','tracked'].includes(id)?id:'slip';section('studio');},
    'studio-tab'(id){if(!['slip','build','tracked'].includes(id))return;H.studioTab=id;paintStudio();if(id==='build')paintBatch();if(id==='tracked')C.loadMine();sectionFocus('studio-tab',id);},
    sport(id){H.sport=id;H.stat='all';H.game='all';H.page=0;H.view==='games'?section('games'):section('board');scrollTop();load(false);},
    'sort-col'(id){if(!SORT_KEYS.includes(id))return;setSort(id,H.sortBy===id&&H.sort===id?(H.sortDir==='asc'?'desc':'asc'):sortDirOf(id));document.querySelector(`#hub-table [data-hub="sort-col"][data-id="${CSS.escape(id)}"]`)?.focus({preventScroll:true});},
    'sort-dir'(){setSort(H.sort,H.sortDir==='asc'?'desc':'asc');},
    'game-clear'(){H.game='all';H.page=0;paintBoard();$('hub-filters-toggle')?.focus({preventScroll:true});},
    game(id){H.game=H.game===id?'all':id;H.page=0;paintBoard();document.querySelector(`#hub-rows [data-hub="game"][data-id="${CSS.escape(id)}"]`)?.focus({preventScroll:true});},
    page(id){H.page+=Number(id);paintBoard();$('hub-table').scrollIntoView({block:'start'});},
    'player-props'(id){const open=rec(H.expanded);if(openPlayers.has(id)){openPlayers.delete(id);if(cardOpen()&&open&&boardPlayerKey(open)===id)closeCard(false);}else openPlayers.add(id);paintBoard();},
    expand(id){if(cardOpen()&&H.expanded===id)closeCard();else openCard(id,'overview');},
    ladder(id){if(openLadders.has(id))openLadders.delete(id);else openLadders.add(id);paintBoard();},
    'group-by'(id){H.groupBy=id==='player'?'player':'prop';H.page=0;try{localStorage.setItem('scout-board-group',H.groupBy);}catch(e){/* storage blocked */}paintBoard();},
    theme(){const root=document.documentElement,t=root.dataset.theme==='light'?'dark':'light';root.dataset.theme=t;try{localStorage.setItem('scout-theme',t);}catch(e){/* storage blocked */}
      document.querySelectorAll('#u-hub [data-hub="theme"]').forEach(b=>{b.textContent=t==='light'?'Dark theme':'Light theme';b.setAttribute('aria-pressed',String(t==='light'));});if(cardOpen()&&H.cardTab==='overview')drawChart();},
    'card-close'(){closeCard();},
    'card-tab'(id){if(!CARD_TABS.some(t=>t[0]===id))return;H.cardTab=id;paintDetail();$('hub-card-body').scrollTop=0;$('hub-tab-'+id)?.focus({preventScroll:true});},
    'genie-ask'(q){genieAsk(q);},
    'detail-line':selectDetail,'detail-retry'(){selectDetail(H.detail);},'history-side'(id){H.histSide=id==='under'?'under':'over';paintDetail();},'history-window'(id){H.histN=Number(id);paintDetail();},
    'detail-add-other'(){const r=rec(H.detail);if(!r||r.extra||C.oneSided(r)||!r.scout.side)return;const o=r.scout.side==='over'?'under':'over';
      if(C.onSlip(r))return revealSlip(r);if(H.detailState!=='ready'||unavailableReason(r)||C.started(r)||!r.offer[o]?.offered||r.offer[r.scout.side]?.offered)return;C.addLeg(r,o);refreshSlip();},
    'detail-add'(){const r=rec(H.detail);if(!r||r.extra)return;if(C.oneSided(r))return;if(C.onSlip(r))return revealSlip(r);if(H.detailState!=='ready'||unavailableReason(r)||C.started(r)||!r.offer[H.histSide].offered)return;C.addLeg(r,H.histSide);refreshSlip();},
    // alert notices open the same card as the board
    research(id){const r=rec(id);if(!r)return C.say('This offer is no longer on the board. Refresh to see current lines.');if(H.section!=='board')section('board');openCard(id);},
    books(id){if(rec(id))openCard(id,'lines');},
    add(id){const r=rec(id);if(!r)return;if(r.extra)return openCard(id,'overview');if(C.oneSided(r))return openCard(id,'overview');if(unavailableReason(r)||C.started(r))return C.say('This offer needs a fresh source check. Refresh the board.');if(C.onSlip(r))return revealSlip(r);if(!r.scout.side||!r.offer[r.scout.side]?.offered)return openCard(id,'overview');C.addLeg(r,r.scout.side);refreshSlip();},
    watch(id){const r=rec(id);if(!r)return;const k=playerKey(r);H.watch=H.watch.includes(k)?H.watch.filter(x=>x!==k):[...H.watch,k].slice(-100);savePreferences();paintBoard();if(cardOpen())paintDetail();},
    diff(){H.onlyDiff=!H.onlyDiff;H.page=0;paintBoard();}, watched(){H.watched=!H.watched;H.page=0;paintBoard();},
    refresh(){H.detailSeq++;if(cardOpen())closeCard(false);H.bookLinesAt=0;load(true);}, tray(){H.studioTab='slip';section('studio');},receipts(){section('picks');},close(){const u=new URL(location.href);u.searchParams.set('view','classic');u.hash=PAGE_BACK;location.assign(u.href);},
    'save-view'(){togglePop(null);openSave('view');}, 'save-alert'(){togglePop(null);openSave('alert');}, 'save-retry':savePreferences,
    'pick-clear'(){H.pickSport='all';H.pickStatus='all';$('hub-pick-sport').value='all';$('hub-pick-status').value='all';paintPicks();},
    'grade-review'(id,el){showGradeReview(id,el?.dataset.before);},
    skip(){$('hub-title').focus();},
    'market-density'(){H.mt.dense=!H.mt.dense;try{localStorage.setItem('scout-market-density',H.mt.dense?'compact':'comfortable');}catch(e){}$('hub-mt-dense')?.setAttribute('aria-pressed',String(H.mt.dense));paintMarketResults();},
    async 'refresh-markets'(){
      if(H.marketLoading)return;
      const refocus=document.activeElement?.dataset?.hub==='refresh-markets';
      H.marketLoading=true;H.marketError=null;paintMarkets();
      try{H.marketDoc=await api('market-tools');}catch(e){H.marketError='Prices could not be loaded.';}
      finally{H.marketLoading=false;paintMarkets();if(refocus)document.querySelector('#hub-market-tools [data-hub="refresh-markets"]')?.focus({preventScroll:true});}
    },
    /* QOL 2026-11-07: "No prices match these filters" clears them in one press (Sport keeps following the board's sport tab) */
    'mt-clear'(){Object.assign(H.mt,{q:'',book:'all',side:'all',minEv:'0',fresh:'all'});mtRemember();paintMarkets();$('hub-mt-q')?.focus({preventScroll:true});},
    /* QOL 2026-11-07: the empty alerts list's button opens the board with the Views menu open on "Alert me on these filters" */
    'alerts-new'(){if(cardOpen())closeCard(false);H.view='list';section('board');togglePop('views');$('hub-views-pop').querySelector('[data-hub="save-alert"]')?.focus({preventScroll:true});},
    async 'toggle-delivery'(){
      H.deliveryBusy=true;H.deliveryError=null;paintAlerts();
      try{H.delivery=await api('alert-delivery',{enabled:!H.delivery?.enabled});}
      catch(e){H.deliveryError=e.message||'Could not update delivery. Try again.';}
      finally{H.deliveryBusy=false;paintAlerts();}
    },
    'delete-alert'(i){const a=H.alerts.splice(Number(i),1)[0];if(a)H.seen.delete(a.id);savePreferences();paintAlerts();if(a)C.say(`Removed ${a.name}.`);},
    'record-retry':showRecord,
    'report-open'(){const r=rec(H.detail);if(!P('report')||!r)return;H.reportFor=r;$('hub-report-what').textContent=`${r.player.name} · ${r.market.stat_label} ${r.offer.line} · ${r.offer.platform_label}`;$('hub-report-reason').value='line';$('hub-report-note').value='';$('hub-report-dialog').showModal();$('hub-report-reason').focus();},
  };
  function setSort(k,dir) {
    H.sort=k;H.sortDir=dir==='asc'?'asc':'desc';H.sortChosen=true;H.page=0;
    try{localStorage.setItem(SORT_LS(),JSON.stringify({key:H.sort,dir:H.sortDir}));}catch(e){/* the account copy still saves */}
    paintBoard();saveSortSoon();
  }
  let sortTimer=null;
  const saveSortSoon=()=>{clearTimeout(sortTimer);sortTimer=setTimeout(savePreferences,1200);};
  /* a new sport tab starts at the top of the board, toolbar and first game in view */
  function scrollTop(){const c=$('hub-center');if(c)c.scrollTop=0;if(document.scrollingElement)document.scrollingElement.scrollTop=0;}
  const sectionFocus=(action,id)=>document.querySelector(`#u-hub [data-hub="${action}"][data-id="${CSS.escape(id)}"]`)?.focus({preventScroll:true});
  const PAGE_BACK=location.pathname==='/sports'?'#/board':location.pathname==='/lol'?'':'#board';
  function openSave(kind) {H.saveKind=kind;$('hub-save-title').textContent=kind==='alert'?(BG?'Alert on these filters':'Live highlight these filters'):'Save this research view';$('hub-save-note').textContent=kind!=='alert'?'Saved to your account.':BG?'Saved to your account. Whop delivers matches when Scout is closed once delivery is on under Alerts; this screen highlights them live.':'Saved to your account. New matches are highlighted while this research screen is open. Nothing is sent when it is closed.';$('hub-save-name').value='';$('hub-save-dialog').showModal();$('hub-save-name').focus();}
  const NAV=[['board','Board'],['studio','Slips'],...(NEXT&&EV_ON?[['ev','EV']]:[]),...(ARB_ON?[['arb','Arbitrage']]:[]),...(BOOKS_ON?[['books','Sportsbooks']]:[]),['picks','My picks'],['record','Record'],['tools','Tools']];
  const pickStatus=[['all','All outcomes'],['pending','Pending'],['hit','Won'],['miss','Lost'],['push','Pushed'],['void','Void']];
  document.body.insertAdjacentHTML('beforeend',`<section id="u-hub" class="scout-hub" aria-label="All sports research" hidden>
    <aside class="h-sidebar"><a class="h-brand" href="/"><span>1T</span><b>1TIME <em>SCOUT</em></b></a><nav aria-label="Research hub">${NAV.map(([id,label])=>`<button data-hub="section" data-id="${id}">${label}${id==='tools'?' <span id="hub-alert-count" aria-label="New filter matches"></span>':''}</button>`).join('')}</nav><div class="h-sidebar-foot"><button data-hub="close">Classic board ↗</button></div></aside>
    <div class="h-center" id="hub-center"><header class="h-top"><div><span class="h-eyebrow">1TIME SCOUT / RESEARCH</span><h1 id="hub-title">Player props</h1></div><div class="h-top-actions"></div></header>
      <section id="hub-board-section">
        <div class="h-toolbar" aria-label="Board controls" role="group">
          <div id="hub-sports" class="h-sports" role="group" aria-label="Sport"></div>
          <label class="h-search"><span class="u-sr">Search</span><input id="hub-q" type="search" placeholder="Search player, team or game" aria-label="Search all sports"></label>
          <div id="hub-platform" class="h-segment h-platform" role="group" aria-label="Platform"><button data-hub="book" data-id="all" aria-pressed="true">All</button><button data-hub="book" data-id="prizepicks" aria-pressed="false" aria-label="PrizePicks (PP)" title="PrizePicks">PP</button><button data-hub="book" data-id="underdog" aria-pressed="false" aria-label="Underdog (UD)" title="Underdog">UD</button></div>
          <div class="h-pop-wrap"><button id="hub-filters-toggle" class="h-filter-toggle" data-hub="filters" aria-expanded="false" aria-controls="hub-filter-pop">Filters<span id="hub-filter-n" class="h-count-badge"></span></button>
            <div id="hub-filter-pop" class="h-pop" role="group" aria-label="Filters and sort" hidden>
              <div class="h-pop-grid"><label>Market<select id="hub-stat"><option value="all">All markets</option></select></label><label>Side<select id="hub-side"><option value="all">Both sides</option><option value="over">Over</option><option value="under">Under</option></select></label>
                <label>Minimum grade<select id="hub-minGrade"><option value="all">Any grade</option>${gradeOrder.slice().reverse().map(g=>`<option value="${g}">${g} or higher</option>`).join('')}</select></label></div>
              <div class="h-quick"><button id="hub-diff" data-hub="diff" aria-pressed="false">Lines differ</button><button id="hub-watch" data-hub="watched" aria-pressed="false">★ Watchlist</button></div>
              <div class="h-pop-foot"><button data-hub="clear-filters">Clear filters</button><button data-hub="filters">Done</button></div></div></div>
          <div class="h-pop-wrap"><button id="hub-views-toggle" data-hub="views-menu" aria-expanded="false" aria-controls="hub-views-pop">Views <span aria-hidden="true">▾</span></button>
            <div id="hub-views-pop" class="h-pop h-pop-end" role="group" aria-label="Saved views and alerts" hidden><label>Saved views<select id="hub-views"><option value="">Choose a saved view</option></select></label><button data-hub="save-view">Save this view</button><button data-hub="save-alert">${BG?'Alert me on these filters':'Live highlight these filters'}</button><button data-hub="manage-alerts">Manage alerts →</button></div></div>
          <button class="h-refresh" data-hub="refresh" aria-label="Refresh all boards" title="Refresh">↻</button>
        </div>
        <div id="hub-sources" class="h-sources" role="status"></div><div id="hub-health" class="h-health" role="status"></div>
        <div class="h-table-title"><h2 class="u-sr">Available lines</h2><span id="hub-count"></span><div id="hub-chips" class="h-chips"></div><button id="hub-clear" class="h-link" data-hub="clear-filters" hidden>Clear filters</button><div class="h-sortbar"><label for="hub-sort">Sort</label><select id="hub-sort">${SORTS.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select><button id="hub-sort-dir" class="h-sort-dir" data-hub="sort-dir" type="button">↓</button></div><small id="hub-sort-note" class="h-sort-note" role="status"></small><div class="h-segment h-viewmode" role="group" aria-label="Board view"><button data-hub="view" data-id="list" aria-pressed="true">List</button><button data-hub="view" data-id="games" aria-pressed="false">By game</button></div></div>
        <div class="h-table-wrap"><table id="hub-table" class="h-table"><thead></thead><tbody id="hub-rows"></tbody></table><div id="hub-empty" class="h-empty" role="status">Loading the available boards…</div></div>
        <div id="hub-games-body" class="h-game-grid" hidden></div>
        <div id="hub-pagination" class="h-pagination"></div></section>
      <section id="hub-studio-section" hidden>
        <div class="h-studio-tabs" role="tablist" aria-label="Slips"><button role="tab" data-hub="studio-tab" data-id="slip" aria-selected="true">My slip <span id="hub-studio-tab-n" class="h-count-badge">0</span></button><button role="tab" data-hub="studio-tab" data-id="build" aria-selected="false" tabindex="-1">Auto-build</button><button role="tab" data-hub="studio-tab" data-id="tracked" aria-selected="false" tabindex="-1">Tracked</button></div>
        <div data-studio-panel="slip" role="tabpanel" aria-label="My slip"><div class="h-studio-intro"><span id="hub-studio-sync" class="h-sync"></span><button data-hub="section" data-id="board">+ Add player props</button></div><section class="h-working-draft" aria-label="My slip"><div id="hub-studio-body"></div></section><div class="h-studio-explain"><h3>Before you track</h3><p>Scout rechecks the event, line revision, offered side and platform rules. Tracking saves a receipt of the line and forecast you chose. Unsupported combinations stay blocked, with the reason beside that entry.</p><button data-hub="receipts">My picks →</button></div></div>
        <div data-studio-panel="build" role="tabpanel" aria-label="Auto-build" hidden>${generatorHtml()}<section id="hub-generated" aria-label="Generated slips"></section></div>
        <div data-studio-panel="tracked" role="tabpanel" aria-label="Tracked" hidden><section class="h-tracked-slips"><header><div><h2>Tracked slips</h2><p>Exact saved lines, grouped by entry. Each leg keeps its settlement receipt.</p></div><button data-hub="receipts">My picks →</button></header><div id="hub-slip-history"></div></section></div></section>
      <section id="hub-picks-section" hidden><div class="h-section-head"><div><p>Your tracked picks, with the line and chance saved when you tracked. Each settles at that line; pushes are counted apart.</p></div><div class="h-pick-filters" hidden><label>Sport<select id="hub-pick-sport"><option value="all">All sports</option>${allowed().map(s=>`<option value="${s}">${SPORTS[s]}</option>`).join('')}</select></label><label>Status<select id="hub-pick-status">${pickStatus.map(([v,l])=>`<option value="${v}">${l}</option>`).join('')}</select></label></div></div><div id="hub-picks-body"></div></section>
      <section id="hub-record-section" hidden><div class="h-section-head"><div><h2>The record, in context.</h2><p>Stated chances beside actual outcomes. Different sport cohorts stay separate.</p></div><select id="hub-record-sport" aria-label="Record sport"><option value="all">All sports</option>${allowed().map(s=>`<option value="${s}">${SPORTS[s]}</option>`).join('')}</select></div><div id="hub-record-body"></div></section>
      ${BOOKS_ON?'<section id="hub-books-section" hidden><section id="hub-book-board" class="h-market-tools"></section></section>':''}<section id="hub-tools-section" hidden><div class="h-section-head"><div><p>Every research tool outside the board, in one place.</p></div></div><div id="hub-tools-body"></div></section>
      <section id="hub-alerts-section" hidden><div id="hub-alerts-body"></div></section>${EV_ON?'<section id="hub-ev-section" hidden><div id="hub-ev-body"></div><div id="hub-ev-priced"></div></section>':''}${ARB_ON?'<section id="hub-arb-section" hidden><div id="hub-arb-body"></div></section>':''}
    </div><aside class="h-slip" aria-label="Slip"><header><h2>Slip <span id="hub-slip-count">0</span></h2><button data-hub="receipts">My picks →</button></header><p id="hub-save-status" class="h-sync" aria-live="polite"></p><div id="hub-slip-content" tabindex="-1"></div><footer>One entry per platform. Rechecked against current offers before tracking.</footer></aside>
    <nav class="h-tabbar" aria-label="Sections"><button data-hub="section" data-id="board">Board</button><button id="hub-tab-slip" data-hub="section" data-id="studio">Slip <span id="hub-tab-slip-n" class="h-count-badge">0</span></button><button data-hub="section" data-id="picks">My picks</button><button data-hub="section" data-id="record">Record</button><button id="hub-more-toggle" data-hub="more" aria-expanded="false" aria-controls="hub-more">More <span id="hub-alert-count-m" class="h-count-badge" aria-label="New filter matches"></span></button></nav>
    <div id="hub-more" class="h-more-sheet" role="group" aria-label="More" hidden>${BOOKS_ON?'<button data-hub="section" data-id="books">Sportsbooks</button>':''}<button data-hub="view" data-id="games">By game</button><button data-hub="section" data-id="tools">Tools</button><button data-hub="section" data-id="alerts">${ALERT_WORD}</button><button data-hub="studio-open" data-id="build">Auto-build slips</button><button data-hub="studio-open" data-id="tracked">Tracked slips</button><a href="/?view=classic#moneylines">Moneylines ↗</a><a href="/?view=classic#one">Single prop grader ↗</a><a href="/?view=classic#matches">Match research ↗</a><button data-hub="close">Classic board ↗</button></div>
    <aside id="hub-card" class="h-card-panel" role="dialog" aria-modal="false" aria-labelledby="hub-card-title" hidden><div id="hub-detail" class="h-card"></div></aside></section>
    <dialog id="hub-save-dialog" class="h-save u-dialog" aria-labelledby="hub-save-title"><form method="dialog"><h2 id="hub-save-title">Save view</h2><label>Name<input id="hub-save-name" maxlength="48" required autocomplete="off"></label><p id="hub-save-note">Saved to your account.</p><div><button value="cancel" formnovalidate>Cancel</button><button id="hub-save-confirm" value="save">Save</button></div></form></dialog>`);
  // audit 2026-11-05 A1: a full path, so the link opens the research board on All sports from every classic page (CS2, LoL, Sports)
  const entry=document.createElement('a');entry.href='/#all-sports';entry.className='h-entry';entry.textContent='All sports research';
  installV2();
  if(EXTRA){
    if(!$('hub-extra-style'))document.head.insertAdjacentHTML('beforeend','<style id="hub-extra-style">#u-hub .h-add.h-add-info{font-size:12px;font-weight:500}'
      +'#u-hub .h-extra-detail{padding:12px 16px}#u-hub .h-extra-detail h3{margin:0 0 10px;font-size:15px}#u-hub .h-extra-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 10px}'
      +'#u-hub .h-extra-kv dt{font-size:11px;color:var(--u-muted)}#u-hub .h-extra-kv dd{margin:2px 0 0}#u-hub .h-extra-state{margin:0 0 10px}#u-hub .h-extra-state summary{min-height:40px;display:flex;align-items:center;cursor:pointer}</style>');
    $('hub-platform').insertAdjacentHTML('beforeend',EXTRA.map(x=>`<button data-hub="book" data-id="${esc(x.id)}" aria-pressed="false" hidden aria-label="${esc(x.label)} (view only)" title="${esc(x.label)}">${esc(x.short||x.label)}</button>`).join(''));
  }
  installCols();
  /* B7: the sticky toolbar's height, so the table header pins right under it (the toolbar wraps on narrow screens) */
  /* UI 2026-11-09: the desktop toolbar is one row only while everything fits. With Best prices, the account bar's wider sidebar and
     slip, Views and Refresh ran past the board column and sat cut off under the Slip column (Filters' pop-over with them). When the
     row overflows, the sport tabs take a row of their own and the controls sit on the row under it (the narrow-desktop layout). */
  const fitBar=()=>{const bar=document.querySelector('#u-hub .h-toolbar');if(!bar||!bar.offsetWidth)return;
    bar.classList.remove('h-bar-wrap');if(DESKTOP.matches&&bar.scrollWidth>bar.clientWidth+1)bar.classList.add('h-bar-wrap');};
  {const bar=document.querySelector('#u-hub .h-toolbar'),setBar=()=>{fitBar();if(bar.offsetHeight)$('u-hub').style.setProperty('--h-bar',bar.offsetHeight+'px');};
    if(window.ResizeObserver){const ro=new ResizeObserver(setBar);ro.observe(bar);ro.observe($('hub-platform'));}setBar();   /* #23: the pill row grows as app counts arrive, the bar's own box does not */document.fonts?.ready?.then(setBar);}
  if(EVB){$('hub-platform').insertAdjacentHTML('afterend','<button class="h-ev-link" data-hub="section" data-id="ev" title="Pick&#39;em legs the books price above what the entry needs">Best prices</button>');fitBar();}
  if(TOKENS){
    // Skip link first in the hub (the classic page's own is inert under it) and one named account control in the top bar.
    $('u-hub').insertAdjacentHTML('afterbegin','<a class="h-skip" href="#hub-title" data-hub="skip" style="position:fixed;top:-80px;left:16px">Skip to content</a>');
    $('hub-title').tabIndex=-1;
    const who=String(R.me.name||'').trim(),initial=(who[0]||'A').toUpperCase();
    document.querySelector('#u-hub .h-top-actions').insertAdjacentHTML('afterbegin',`<details class="h-account"><summary aria-label="${esc('Account'+(who?': '+who:''))}"><i aria-hidden="true">${esc(initial)}</i><span>${esc(who||'Account')}</span></summary><nav aria-label="Account"><button data-hub="section" data-id="picks">My picks</button><button data-hub="section" data-id="tools">${ALERT_WORD}</button><button data-hub="close">Classic board</button><a href="/auth/logout">Sign out</a></nav></details>`);
    const acct=document.querySelector('#u-hub .h-account');
    acct.addEventListener('click',e=>{if(e.target.closest('nav'))acct.open=false;});
    acct.addEventListener('keydown',e=>{if(e.key==='Escape'&&acct.open){acct.open=false;acct.querySelector('summary').focus();}});
    document.addEventListener('pointerdown',e=>{if(acct.open&&!acct.contains(e.target))acct.open=false;});    /* a click outside closes it */
    document.addEventListener('focusin',e=>{if(acct.open&&!acct.contains(e.target))acct.open=false;});
  }
  const hostEntry=()=>{const entryHost=document.querySelector('.workspace-tools')||document.querySelector('.sw-tabs')||document.querySelector('.ot-lol .bar')||document.querySelector('body header');
    if(entryHost) entryHost.appendChild(entry); else if(!entry.isConnected) document.body.prepend(entry);};
  hostEntry();
  document.addEventListener('scout-classic-mount',hostEntry);   /* the classic page built later: the entry joins its toolbar */
  /* LoL draws its filter bar after its data loads: the entry then moves from the sticky header (no room on a phone, where it
     hung below the header over the rows) into that bar, and back into it whenever the page redraws the bar (audit 2026-11-05) */
  if(document.body.classList.contains('ot-lol')){let q=0;new MutationObserver(()=>{if(q)return;q=requestAnimationFrame(()=>{q=0;const bar=document.querySelector('.ot-lol .bar');if(bar&&entry.parentNode!==bar)bar.appendChild(entry);});}).observe(document.querySelector('main')||document.body,{childList:true,subtree:true});}
  /* QOL 2026-11-07: Enter in the name box saves (the form's first button is Cancel, so Enter cancelled and nothing was saved) */
  $('hub-board-section').querySelector('.h-viewmode').insertAdjacentHTML('afterend','<button id="hub-focus" class="h-focus-toggle" data-hub="focus-board" aria-pressed="false" aria-controls="hub-slip-content">Hide slip</button>');
  /* the design before the redesign, kept for comparison (/preview/current): its sidebar has the way back to the new one */
  if(!NEXT&&/(?:^|;\s*)scout_ui=current(?:;|$)/.test(document.cookie))document.querySelector('#u-hub .h-sidebar-foot').insertAdjacentHTML('afterbegin','<a class="h-preview-on" href="/preview/next" style="display:block;margin:0 0 8px;font-size:11px">New design</a>');
  if(NEXT){
    /* UI_NEXT shell: By prop / By player, the theme switch, and (preview cookie only) the way back to the current design */
    $('hub-board-section').querySelector('.h-viewmode').insertAdjacentHTML('beforebegin','<div class="h-segment h-groupmode" role="group" aria-label="Group rows"><button data-hub="group-by" data-id="prop" aria-pressed="true">By prop</button><button data-hub="group-by" data-id="player" aria-pressed="false">By player</button></div>');
    const light=document.documentElement.dataset.theme==='light',preview=/(?:^|;\s*)scout_ui=next(?:;|$)/.test(document.cookie);
    const extra=`<button data-hub="theme" aria-pressed="${light}">${light?'Dark theme':'Light theme'}</button><a class="h-preview-off" href="/preview/current">Previous design</a>`;
    document.querySelector('#u-hub .h-sidebar-foot').insertAdjacentHTML('afterbegin',extra);
    $('hub-more').insertAdjacentHTML('afterbegin',(EV_ON?'<button data-hub="section" data-id="ev">EV</button>':'')+(ARB_ON?'<button data-hub="section" data-id="arb">Arbitrage</button>':''));
    $('hub-more').insertAdjacentHTML('beforeend',extra);
    /* rows by keyboard: Up / Down move between rows, Right opens a row's ladder, Left closes it (Enter opens the card as before) */
    $('hub-rows').addEventListener('keydown',e=>{
      if(!['ArrowDown','ArrowUp','ArrowRight','ArrowLeft'].includes(e.key)||e.altKey||e.ctrlKey||e.metaKey)return;
      const tr=e.target.closest('tr.h-row');if(!tr||e.target.closest('.h-ladder'))return;
      if(e.key==='ArrowRight'||e.key==='ArrowLeft'){const k=tr.dataset.prop,b=tr.querySelector('[data-hub="ladder"]');if(!k||!b)return;
        if((e.key==='ArrowRight')!==openLadders.has(k)){e.preventDefault();openLadders[e.key==='ArrowRight'?'add':'delete'](k);paintBoard();document.querySelector(`#hub-rows tr[data-prop="${CSS.escape(k)}"] .h-player`)?.focus({preventScroll:true});}return;}
      const list=[...document.querySelectorAll('#hub-rows tr.h-row')],to=list[list.indexOf(tr)+(e.key==='ArrowDown'?1:-1)];
      if(to){e.preventDefault();to.querySelector('.h-player')?.focus();to.scrollIntoView({block:'nearest'});}
    });
  }
  function generationSummary(){
    const n=$('hub-generation-slips').value,size=$('hub-generation-count').value;
    const sports=[...$('hub-generation-sports').querySelectorAll('input:checked')].map(x=>SPORTS[x.value]);
    const book=$('hub-generation-book').selectedOptions[0].textContent;
    const side=$('hub-generation-side').value,sideText=side==='any'?'':side==='over'?'Overs only':'Unders only';
    $('hub-generation-filter-count').textContent=sideText?'1 active':'';
    $('hub-generation-summary').textContent=sports.length?`${n} slips × ${size} picks · ${book} · ${sports.join(' + ')}${sideText?' · '+sideText:''}`:'Select at least one sport to build your batch.';
  }
  $('hub-studio-section').addEventListener('change',generationSummary);
  generationSummary();
  $('hub-save-name').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing){e.preventDefault();$('hub-save-confirm').click();}});
  $('hub-save-dialog').addEventListener('close',()=>{
    /* focus returns to the Views button the dialog was opened from (its menu item is hidden by then) */
    if(isOpen()&&H.section==='board')$('hub-views-toggle').focus({preventScroll:true});
    if($('hub-save-dialog').returnValue!=='save')return;
    const name=$('hub-save-name').value.trim();if(!name)return;
    /* N6: a view or alert saved where Minimum grade is hidden (no letters on the tab) saves what the board applies there */
    const v={id:Date.now().toString(36),name,filters:{...filters(),minGrade:gradeFilterOn()?H.minGrade:'all'}};
    if(H.saveKind==='alert'){H.alerts=[...H.alerts,v].slice(-12);checkAlerts();}else H.views=[...H.views,v].slice(-12);
    savePreferences();paintBoard();C.say(H.saveKind==='alert'?(BG?'Alert saved. Turn on Whop delivery under Alerts to get it when Scout is closed.':'Live highlight saved for this research screen.'):'Research view saved.');
  });
  if(P('report')){   /* SCOUT_PARITY_REPORT: one report per line and reason; it goes to the Scout team's data issues list */
    const reasons=Array.isArray(PAR.reasons)?PAR.reasons:[];
    document.body.insertAdjacentHTML('beforeend',`<dialog id="hub-report-dialog" class="h-save u-dialog" aria-labelledby="hub-report-title"><form method="dialog"><h2 id="hub-report-title">Report a problem</h2><p id="hub-report-what"></p><label>What is wrong<select id="hub-report-reason">${reasons.map(([v,l])=>`<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select></label><label>Details (optional)<textarea id="hub-report-note" maxlength="300" rows="3"></textarea></label><div><button value="cancel" formnovalidate>Cancel</button><button value="send">Send report</button></div></form></dialog>`);
    $('hub-report-dialog').addEventListener('close',async()=>{
      const r=H.reportFor;H.reportFor=null;
      if($('hub-report-dialog').returnValue!=='send'||!r)return;
      try{const d=await api('report-problem',{sport:r.sport,book:r.offer.platform,id:r.offer.offer_id,revision:r.offer.revision,reason:$('hub-report-reason').value,note:$('hub-report-note').value});
        C.say(d?.duplicate?'You already reported this line. Thanks.':'Thanks. Your report was sent to the Scout team.');}
      catch(e){C.say(e.status===404?'This line is no longer on the board.':e.status===429?'Too many reports just now. Try again in a minute.':'The report could not be sent. Try again.');}
    });
  }
  if(P('prefs'))$('u-hub').addEventListener('change',e=>{
    if(!['hub-pref-odds','hub-pref-sizing','hub-pref-bankroll'].includes(e.target.id))return;
    const b=Number($('hub-pref-bankroll').value);
    H.par={odds:$('hub-pref-odds').value==='decimal'?'decimal':'american',sizing:SIZING.some(x=>x[0]===$('hub-pref-sizing').value)?$('hub-pref-sizing').value:'quarter',bankroll:Number.isFinite(b)&&b>0&&b<=1e7?b:null};
    H.parKeep=H.par;savePreferences();
  });
  if(PAR&&!$('hub-parity-style'))document.head.insertAdjacentHTML('beforeend','<style id="hub-parity-style">'
    +'#u-hub .h-plays{display:inline-block;margin-left:5px;padding:0 5px;border-radius:8px;background:rgba(122,162,255,.16);font:500 10.5px monospace;line-height:16px;vertical-align:1px}'
    +'#u-hub .h-band{display:inline-block;margin-left:6px;padding:1px 6px;border-radius:6px;font:600 10.5px monospace;letter-spacing:.02em;border:1px solid transparent}'
    +'#u-hub .h-band-skip{color:#9aa9bd;border-color:rgba(154,169,189,.35)}#u-hub .h-band-marginal{color:#c9d4e3;background:rgba(201,212,227,.08)}'
    +'#u-hub .h-band-strong{color:#9fe0bf;background:rgba(80,190,140,.12)}#u-hub .h-band-premium{color:#0d2a1d;background:#8fdcb5}'
    +'#u-hub .h-th-sort{all:unset;cursor:pointer;display:inline-flex;flex-direction:column;align-items:center;min-height:40px;justify-content:center}#u-hub .h-th-sort:focus-visible{outline:2px solid #7aa2ff;outline-offset:2px}'
    +'#u-hub .h-edge{display:inline-flex;align-items:baseline;gap:3px;font-variant-numeric:tabular-nums;white-space:nowrap}#u-hub .h-edge.positive{color:#b1e7cb}#u-hub .h-edge.negative{color:#f1c3c9}'
    +'#u-hub .h-edge-wrap{display:inline-flex;flex-direction:column;align-items:center;gap:3px}#u-hub .h-edge-wrap .h-band{margin-left:0}'
    +'#u-hub #hub-table .h-strip .h-hit.h-hit-n{font-size:11px;letter-spacing:-.03em;padding-left:0;padding-right:0}@media(max-width:700px){#u-hub #hub-table .h-strip .h-hit.h-hit-n{font-size:9px;letter-spacing:-.06em}}#u-hub .h-dec-size{display:block;grid-column:1/-1;color:var(--u-muted,#9aa9bd);font-size:12px}'
    +'#u-hub .h-report-row{margin:14px 0 4px}#u-hub .h-report-row .h-link{min-height:40px}'
    +'#u-hub .h-prefs{margin:0 0 18px;padding:16px;border:1px solid var(--u-line,#26364d);border-radius:12px}#u-hub .h-prefs h2{margin:0 0 4px;font-size:17px}#u-hub .h-prefs p{margin:0 0 12px;color:var(--u-muted,#9aa9bd)}'
    +'#u-hub .h-prefs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}#u-hub .h-prefs-grid label{display:grid;gap:6px}#u-hub .h-prefs-grid select,#u-hub .h-prefs-grid input{min-height:40px;box-sizing:border-box;width:100%}'
    +'#hub-report-dialog form{display:block;max-width:440px}#hub-report-dialog form>div{display:flex;gap:8px;justify-content:flex-end}#hub-report-dialog p{margin:0 0 12px}#hub-report-dialog textarea{width:100%;box-sizing:border-box}#hub-report-dialog label{display:grid;gap:6px;margin:0 0 12px}#hub-report-dialog select{min-height:40px}</style>');
  $('u-hub').addEventListener('click',e=>{
    const b=e.target.closest('[data-hub]');if(!b||b.disabled)return;
    if(b.tagName==='TR'&&String(getSelection()||'').trim())return;
    e.preventDefault();
    const inMore=!!b.closest('#hub-more');
    actions[b.dataset.hub]?.(b.dataset.id,b);
    if(inMore)togglePop(null);
  });
  $('hub-more').addEventListener('click',e=>{if(e.target.closest('a'))togglePop(null);});
  $('u-hub').addEventListener('change',e=>{if(e.target.id==='hub-card-line'&&rec(e.target.value))selectDetail(e.target.value);});
  $('u-hub').addEventListener('submit',e=>{if(e.target.id==='hub-ask'){e.preventDefault();genieAsk();}});
  $('u-hub').addEventListener('input',e=>{if(e.target.id==='hub-ask-q')G.q=e.target.value;});
  document.addEventListener('pointerdown',e=>{if(isOpen()&&openPop()&&!e.target.closest('.h-pop-wrap,#hub-more,#hub-more-toggle'))togglePop(null);});
  let chartWidth=0;window.addEventListener('resize',()=>{const el=document.querySelector('#hub-detail [data-hub-chart]');if(el&&Math.abs(el.clientWidth-chartWidth)>24){chartWidth=el.clientWidth;drawChart();}});
  for(const f of ['q','side','stat']) $('hub-'+f).addEventListener(f==='q'?'input':'change',()=>{H[f]=$('hub-'+f).value;H.page=0;paintBoard();});
  /* QOL 2026-11-07: the phone keyboard's Search key puts the keyboard away so the results show (the board filters as you type) */
  $('hub-q').enterKeyHint='search';
  $('hub-q').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.isComposing&&!DESKTOP.matches){e.preventDefault();$('hub-q').blur();}});
  $('hub-sort').addEventListener('change',()=>{const k=$('hub-sort').value;if(SORT_KEYS.includes(k))setSort(k,sortDirOf(k));});
  $('hub-minGrade').addEventListener('change',()=>{H.minGrade=$('hub-minGrade').value;H.page=0;paintBoard();});
  /* QOL 2026-11-07: a saved view on another sport opens that sport properly (its board is read and the address follows, so reload and
     Back keep it; before, the tab changed but the address and the read did not), the menu closes and a short note names the view */
  $('hub-views').addEventListener('change',()=>{const i=$('hub-views').value;if(i==='')return;const v=H.views[Number(i)];if(v){const was=H.sport;H.minGrade='all';for(const k of filterKeys) if(k in v.filters)H[k]=v.filters[k];H.page=0;H.view='list';togglePop(null);section('board');if(H.sport!==was){scrollTop();load(false);}$('hub-views-toggle').focus({preventScroll:true});C.say(`Showing your saved view: ${v.name}`);}});
  $('hub-record-sport').addEventListener('change',showRecord);
  function generationControls() {
    const p=$('hub-generation-book').value,old=$('hub-generation-type').value;
    $('hub-generation-type').innerHTML=C.PLATFORMS[p].types.map(([v,label])=>`<option value="${v}">${label}</option>`).join('');
    if(C.PLATFORMS[p].types.some(t=>t[0]===old))$('hub-generation-type').value=old;
    for(const o of $('hub-generation-count').options)o.disabled=(p==='prizepicks'&&Number(o.value)>6)||(p==='underdog'&&$('hub-generation-type').value==='flex'&&Number(o.value)<3);
    if($('hub-generation-count').selectedOptions[0]?.disabled)$('hub-generation-count').value=p==='prizepicks'?'6':'3';
    const stacks=$('hub-generation-games').querySelector('option[value="stacks"]');
    stacks.disabled=p!=='underdog';if(stacks.disabled&&$('hub-generation-games').value==='stacks')$('hub-generation-games').value='spread';
    const mixed=$('hub-generation-mix').querySelector('option[value="mixed"]');
    mixed.disabled=p==='underdog';if(mixed.disabled&&$('hub-generation-mix').value==='mixed')$('hub-generation-mix').value='any';
  }
  for(const id of ['book','type'])$('hub-generation-'+id).addEventListener('change',generationControls);
  $('hub-generation-lines').addEventListener('change',()=>{H.slipLines=cleanLines($('hub-generation-lines').value);T.slipLines=H.slipLines;savePreferences();C.refreshAdvice?.();});
  generationControls();
  $('hub-pick-sport').addEventListener('change',()=>{H.pickSport=$('hub-pick-sport').value;paintPicks();});
  $('hub-pick-status').addEventListener('change',()=>{H.pickStatus=$('hub-pick-status').value;paintPicks();});
  window.addEventListener('hashchange',route);
  window.addEventListener('popstate',e=>{
    if(H.popSkip){H.popSkip=false;return;}                                         /* the card's own entry, taken off by closeCard */
    if(H.cardPushed&&cardOpen()&&!e.state?.hubCard){H.cardPushed=false;closeCard();return;}   /* phone Back closes the card */
    route();
  });
  window.addEventListener('keydown',e=>{
    if(!isOpen())return;
    if(e.key==='Escape'&&!document.querySelector('dialog[open]')){
      const pop=openPop();
      if(pop){togglePop(null);$(POPS[pop][0]).focus({preventScroll:true});e.preventDefault();return;}
      if(cardOpen()){closeCard();e.preventDefault();return;}
      /* QOL 2026-11-07: Escape in the search box clears it in every browser (only some did); a second Escape leaves the box */
      if(e.target.id==='hub-q'){e.preventDefault();if($('hub-q').value){$('hub-q').value='';H.q='';H.page=0;paintBoard();}else $('hub-q').blur();return;}
    }
    // arrow keys move between the card's tabs and the Slips page tabs, as tabs do
    if((e.key==='ArrowRight'||e.key==='ArrowLeft')&&e.target.matches?.('[role="tab"][data-hub]')){
      const list=[...e.target.parentElement.querySelectorAll('[role="tab"][data-hub]')],i=list.indexOf(e.target),next=list[(i+(e.key==='ArrowRight'?1:list.length-1))%list.length];
      if(next){e.preventDefault();actions[next.dataset.hub]?.(next.dataset.id,next);}
      return;
    }
    if(e.key!=='/'||e.ctrlKey||e.metaKey||e.altKey||document.querySelector('dialog[open]')||e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
    e.preventDefault();e.stopImmediatePropagation();if(cardOpen())closeCard(false);H.view='list';section('board');$('hub-q').focus();
  },true);
  setInterval(()=>{if(isOpen()&&!document.hidden&&H.section==='board')load(true);},60000);
  document.addEventListener('visibilitychange',()=>{if(isOpen()&&!document.hidden&&H.section==='board'&&Date.now()-(H.lastLoad||0)>60000)load(true);});
  /* Price check left open: coming back to the tab reloads a sheet older than 2 min, and once a minute the rows are redrawn (no
     request) so a price or both-sides pair past the server's age limit drops off even if nobody touches the page */
  const mtOld=()=>{const t=Date.parse(H.marketDoc?.as_of);return !Number.isFinite(t)||Date.now()-t>120000;};
  document.addEventListener('visibilitychange',()=>{if(isOpen()&&!document.hidden&&mtSection()&&H.marketDoc&&!H.marketLoading&&mtOld())actions['refresh-markets']();});
  setInterval(()=>{const box=$('hub-market-results');if(!document.hidden&&mtSection()&&H.marketDoc&&box&&!box.contains(document.activeElement))paintMarketResults();},60000);
  restoreBoard();
  route();
  loadPreferences().then(()=>{if(H.section==='board')paintBoard();else if(H.section==='alerts'||H.section==='tools')paintAlerts();});
  return {refreshSlip};
}
