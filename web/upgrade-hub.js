/* One real research surface over the existing authenticated sport adapters.
 * No grades or prices are calculated here. The original pages and their tools stay intact.
 */
export function mount(C) {
  if(!document.querySelector('link[href="/upgrade/hub.css"]')){const style=document.createElement('link');style.rel='stylesheet';style.href='/upgrade/hub.css';document.head.appendChild(style);}
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
  const $ = id => document.getElementById(id);
  const H = {minGrade:'all',sport:'all', book:'all', q:'', side:'all', sort:'start', stat:'all', game:'all', onlyDiff:false, watched:false, freshOnly:false, soonOnly:false, movedOnly:false,
    expanded:null, detail:null, detailDoc:null, detailState:'idle', detailSeq:0, histN:10, histSide:'over', pickSport:'all', pickStatus:'all',
    page:0, section:'board', loading:false, seq:0, records:[], views:[], watch:[], alerts:[], seen:new Map(), notices:[], saveBusy:false, generating:false, batch:null, batchSaved:false,
    cardTab:'overview', view:'list', studioTab:'slip'};
  H.pendingOpen=null;
  const CARD_TABS=[['overview','Overview'],['lines','Lines'],['stats','Stats'],['ask','Ask']];
  const DESKTOP=matchMedia('(min-width:1101px)');
  H.extraRecs=[];H.extraState=null;
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
  const allowed = () => Object.keys(SPORTS).filter(s => R.me.sports && R.me.sports[s]);
  const key = r => [r.sport,r.offer.platform,r.offer.offer_id].join('|');
  const playerKey = r => r.sport+'|'+r.player.key;
  const num = n => typeof n === 'number' && Number.isFinite(n);
  const filters = () => ({minGrade:H.minGrade,sport:H.sport,book:H.book,q:H.q,side:H.side,sort:H.sort,stat:H.stat,game:H.game,onlyDiff:H.onlyDiff,watched:H.watched,freshOnly:H.freshOnly,soonOnly:H.soonOnly,movedOnly:H.movedOnly});
  const filterKeys = ['minGrade','sport','book','q','side','sort','stat','game','onlyDiff','watched','freshOnly','soonOnly','movedOnly'];
  const gradeOrder = ['B-','B','B+','A-','A','A+'];
  const gradeMatches = (r,minimum) => !minimum||minimum==='all'||(gradeOrder.includes(minimum)&&!r.scout.hold&&gradeOrder.indexOf(r.scout.grade)>=gradeOrder.indexOf(minimum));
  const gameKey = r => r.sport+'|'+r.event.id;
  const statKey = r => r.sport+'|'+r.market.stat;
  const isOpen = () => !$('u-hub').hidden;
  const focusAfter = container => {
    const el=document.activeElement;
    if(container?.contains(el)&&el.id&&!el.dataset.hub)return ()=>{const now=document.getElementById(el.id);if(now&&now!==document.activeElement)now.focus({preventScroll:true});};
    if(!container?.contains(el)||!el.dataset.hub)return ()=>{};
    const action=el.dataset.hub,id=el.dataset.id;
    return ()=>container.querySelector(`[data-hub="${CSS.escape(action)}"]${id==null?'':`[data-id="${CSS.escape(id)}"]`}`)?.focus({preventScroll:true});
  };
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
      && (f.side==='all'||r.scout.side===f.side) && (!f.onlyDiff||different(r))
      && gradeMatches(r,f.minGrade)
      && (!f.watched||H.watch.includes(playerKey(r)))
      && (!f.freshOnly||!unavailableReason(r))
      && (!f.movedOnly||r.offer.moved_from!=null)
      && (!f.soonOnly||(Date.parse(r.event.start_utc)>Date.now()&&Date.parse(r.event.start_utc)-Date.now()<=3600000))
      && (!f.q||[r.player.name,r.player.team,r.event.label,r.market.stat_label].join(' ').toLowerCase().includes(f.q.toLowerCase()));
  }
  function rows() {
    // owner rules (no unsure labels; transient failures hide the line): a row whose source is stale, failed or held is left off, never labelled
    // games stay together: start time, then the game, then the player (a game header row opens each run)
    // never sort by an empty column: with no edge on any shown line, Edge sorting falls back to start time and the sort control says so
    const list=(isExtra(H.book)?H.extraRecs:H.records).filter(r=>!C.started(r)&&!unavailableReason(r)&&matches(r));
    H.sortFallback=H.sort==='edge'&&!list.some(r=>edgeValue(r)!=null);
    H.sortBy=H.sortFallback?'start':H.sort;
    const byStart=(a,b)=>Date.parse(a.event.start_utc)-Date.parse(b.event.start_utc)||gameKey(a).localeCompare(gameKey(b))||a.player.name.localeCompare(b.player.name);
    return list.sort((a,b)=> {
      if(H.sortBy==='chance') return (b.scout.p_side??-1)-(a.scout.p_side??-1);
      if(H.sortBy==='player') return a.player.name.localeCompare(b.player.name);
      if(H.sortBy==='edge'){const x=edgeValue(a),y=edgeValue(b);return x==null&&y==null?byStart(a,b):x==null?1:y==null?-1:y-x||byStart(a,b);}
      return byStart(a,b);
    });
  }
  /* the filters that live in the Filters pop-over; the count on its button. Search, sport and platform are on the toolbar itself */
  const activeFilters = () => [H.stat!=='all',H.side!=='all',H.minGrade!=='all',H.onlyDiff,H.watched,H.freshOnly,H.soonOnly,H.movedOnly,H.game!=='all'].filter(Boolean).length;
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
    for(const n of ['side','sort','q','minGrade']) $('hub-'+n).value=H[n];
    syncBooks();
    $('hub-platform').querySelectorAll('[data-hub="book"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===H.book)));
    $('hub-diff').setAttribute('aria-pressed',String(H.onlyDiff));
    $('hub-watch').setAttribute('aria-pressed',String(H.watched));
    if(V2)for(const f of ['freshOnly','soonOnly','movedOnly'])$('hub-'+f).setAttribute('aria-pressed',String(H[f]));
    const read=s=>s==='all'?allowed().every(x=>R.boards[x]?.at):!!R.boards[s]?.at;
    $('hub-sports').innerHTML=['all',...allowed()].map(s=>`<button type="button" data-hub="sport" data-id="${s}" aria-pressed="${H.sport===s}">${s==='all'?'All':SPORTS[s]}${read(s)?`<span>${H.records.filter(r=>!C.started(r)&&(s==='all'||r.sport===s)).length}</span>`:''}</button>`).join('');
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
    return `<span class="h-hit h-tone-${Math.min(6,Math.floor(rate*7))}${few?' h-few':''}" style="--hit:${rate.toFixed(3)}" title="${esc(text)}" aria-label="${label}: ${esc(text)}">${Math.round(rate*100)}%</span>`;
  }
  const stripHtml = r => `<div class="h-strip">${STRIP.map(([w,l])=>`<span class="h-strip-cell h-s-${w}" data-label="${l}">${stripCell(r,w,l)}</span>`).join('')}</div>`;
  /* Edge vs break-even (owner 2026-11-05): the row's current Scout chance minus the platform's published per-pick break-even.
     Underdog standard at a 1.0x pick: the gradecore payout table (gradecore/payouts.py, Underdog's published payouts, the server sends
     the value and its source). PrizePicks and goblin / demon lines publish no fixed per-pick payout here: their cell stays empty.
     A fact about the chance against the payout, not a grade: a held or contained letter does not change it. */
  function edgeValue(r) {
    const s=r.scout, be=COLS?.ud_standard_break_even, mult=s.side?r.offer[s.side]?.payout:null;
    if(r.offer.platform!=='underdog'||r.market.line_type!=='standard'||!s.side||!num(s.p_side)||!num(be)||!num(mult)||Math.abs(mult-1)>1e-9)
      return null;
    return (s.p_side-be)*100;
  }
  const EDGE_WHY=()=>`Scout chance minus the per-pick break-even of an ${COLS?.entry||'Underdog standard 2-pick'} entry`+(num(COLS?.ud_standard_break_even)?` (${(COLS.ud_standard_break_even*100).toFixed(1)}%`+(COLS?.source?`, from Underdog's published payouts${COLS.read_at?' read '+COLS.read_at:''}`:'')+')':'')+', in points. PrizePicks and goblin or demon lines publish no fixed per-pick payout, so their cells stay empty.';
  function edgeCell(r) {
    const d=edgeValue(r);
    if(d==null)return `<span class="h-edge empty" title="${esc(r.offer.platform==='underdog'?'No fixed per-pick payout is published for this line type':'No fixed per-pick payout is published for this platform')}">—</span>`;
    const be=COLS.ud_standard_break_even;
    return `<span class="h-edge${d>0?' positive':''}" title="${esc(`Scout chance ${pct(r.scout.p_side)} against the ${(be*100).toFixed(1)}% break-even of an ${COLS.entry||'Underdog standard'} entry`)}"><b>${d>0?'+':d<0?'−':'±'}${Math.abs(d).toFixed(1)}</b><small>pts</small></span>`;
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
  const boardPlayerKey = r => [r.sport,r.event.id,r.player.key].join('|');
  /* the letter as on classic: gold for A, gold outline for B; a sub-threshold pick keeps its Lean wording; NBA/NFL letters say Research grade */
  function gradeChip(r) {
    const s=r.scout;
    if(s.hold) return '<span class="h-grade-text">Research estimate</span>';
    if(s.letter_held&&s.side) return `<span class="h-grade-text">Lean ${sideWord(s.side)}</span><small class="h-grade-tag">0U</small>`;   /* lc-1: held letter */
    if(s.grade) return `<span class="h-grade h-grade-${/^A/.test(s.grade)?'a':/^B/.test(s.grade)?'b':'c'}">${esc(s.grade)}</span>${['nba','nfl'].includes(r.sport)?'<small class="h-grade-tag">Research grade</small>':''}`;
    if(s.side) return `<span class="h-grade-text">Lean ${sideWord(s.side)}</span>`;
    return '';
  }
  /* ONE Scout cell: side, chance and the letter (or Lean Over/Under) together. No published side = both chances, no side shown */
  function scoutCell(r) {
    const s=r.scout;
    if(r.extra&&!s.side)return '<div class="h-scout"><div class="h-direction-options"><span>No saved chance at this line</span></div></div>';
    if(s.side) return `<div class="h-scout"><div class="h-direction ${s.side}"><b>${s.side==='over'?'↗ Over':'↘ Under'}</b><span>${pct(s.p_side)}</span></div><div class="h-scout-grade">${gradeChip(r)}</div></div>`;
    const both=num(s.p_over)&&num(s.p_under);
    return `<div class="h-scout"><div class="h-direction-options" aria-label="Model probabilities for both sides">${both?`<span>Over <b>${pct(s.p_over)}</b></span><span>Under <b>${pct(s.p_under)}</b></span>`:'<span>Forecast pending</span>'}</div>${s.hold?'<div class="h-scout-grade"><span class="h-grade-text">Research estimate</span></div>':''}</div>`;
  }
  function priceSummary(q) {
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
    return `<button class="h-compare${different(r)?' differ':''}" data-hub="books" data-id="${esc(key(r))}" title="${esc(full)}" aria-label="Compare books and lines for ${esc(r.player.name)}: ${esc(full)}"><b>${esc(main)}</b><small>${esc(sub)}</small></button>`;
  }
  function row(r, group=null) {
    const k=esc(key(r)), s=r.scout, side=s.side, on=C.onSlip(r), watched=H.watch.includes(playerKey(r)), open=H.expanded===key(r)||H.detail===key(r);
    const choose=!side||!r.offer[side]?.offered;
    const unavailable=!!unavailableReason(r);
    const bk=boardPlayerKey(r), propsOpen=openPlayers.has(bk), more=group?.length>1?group.length-1:0;
    const toggle=more?`<button class="h-prop-toggle" data-hub="player-props" data-id="${esc(bk)}" aria-expanded="${propsOpen}" aria-label="${propsOpen?'Hide':'Show'} ${more} more ${more===1?'prop':'props'} for ${esc(r.player.name)}" title="${esc(group.slice(1).map(x=>x.market.stat_label+' '+x.offer.line+' · '+x.market.period_label).join('\n'))}">${propsOpen?'−':'+'}${more}</button>`:'';
    // the matchup sits on the game header row; without headers (another sort) the row names the opponent itself
    const vs=H.sortBy!=='start'&&r.player.opponent?` vs ${r.player.opponent}`:'', name=esc(r.player.name);
    const add=on?['On slip ✓',`On slip: show ${name} in the slip`]:choose?['Side',`Choose a side for ${name}`]:['+ Add',`Add ${name} ${sideWord(side)} ${esc(r.offer.line)} to slip`];
    return `<tr data-record="${k}" data-hub="expand" data-id="${k}" class="h-row${open?' is-expanded':''}${group?'':' is-sub'}">
      <td class="h-c-watch"><button class="h-star" data-hub="watch" data-id="${k}" aria-label="Watch ${name}" aria-pressed="${watched}">${watched?'★':'☆'}</button></td>
      <td class="h-c-player"><button class="h-player" data-hub="expand" data-id="${k}" aria-haspopup="dialog" aria-expanded="${open}"><b>${name}</b><small>${esc(r.player.team)}${esc(vs)}<i>${SPORTS[r.sport]}</i></small></button></td>
      <td class="h-c-market"><div class="h-mcell"><div><b>${esc(r.market.stat_label)}</b><small>${esc(r.market.period_label)}</small></div>${toggle}</div></td>
      <td class="h-c-line h-number"><b>${esc(r.offer.line)}</b><small title="${esc(`${r.offer.platform_label} · offer revision first observed ${r.offer.seen_at||'at an unavailable time'}`)}">${esc(BOOK_SHORT[r.offer.platform]||r.offer.platform_label)} · ${esc(age(r.offer.seen_at).replace(' ago',''))}${r.offer.moved_from!=null?` · was ${esc(r.offer.moved_from)}`:''}</small></td>
      <td class="h-c-proj h-number">${num(s.projection)?esc(s.projection.toFixed(1)):'—'}</td>
      <td class="h-c-scout">${scoutCell(r)}</td>
      <td class="h-c-hits">${stripHtml(r)}</td>${H.showEdge?`<td class="h-c-edge h-number">${edgeCell(r)}</td>`:''}
      <td class="h-c-compare">${compareCell(r)}</td>
      <td class="h-c-add">${r.extra?`<button class="h-add h-add-info" data-hub="add" data-id="${k}" aria-label="${esc(r.offer.platform_label)} line: view only, not on slips" title="View only: slips are PrizePicks or Underdog entries">Info</button>`:`<button class="h-add${on?' is-on':choose?' selected':''}" data-hub="add" data-id="${k}" aria-label="${add[1]}" ${unavailable?'disabled':''}>${add[0]}</button>`}</td></tr>`;
  }
  /* the game header row: matchup, sport, start and countdown once per game; a click shows only that game (and back) */
  function gameRow(r,list,cols) {
    const g=gameKey(r), on=H.game===g, books=[...new Set(list.filter(x=>gameKey(x)===g).map(x=>BOOK_SHORT[x.offer.platform]||x.offer.platform_label))].join(', ');
    return `<tr class="h-game-row" data-game="${esc(g)}"><td colspan="${cols}"><button class="h-game-head" data-hub="game" data-id="${esc(g)}" aria-pressed="${on}" aria-label="${esc((on?'Show all games, now showing ':'Show only ')+r.event.label)}"><b class="h-match">${esc(r.event.label)}</b><span>${esc(SPORTS[r.sport])}${r.event.competition?' · '+esc(r.event.competition):''} · ${esc(when(r.event.start_utc))}</span>${countdownHtml(r)}<small>${esc(books)}</small><em>${on?'All games ✕':'This game →'}</em></button></td></tr>`;
  }
  function headHtml(showEdge) {
    return `<tr><th class="h-c-watch"><span class="u-sr">Watch</span></th><th class="h-c-player">Player</th><th class="h-c-market">Market</th><th class="h-c-line">Line</th><th class="h-c-proj">Proj.</th><th class="h-c-scout">Scout</th>`
      +`<th class="h-c-hits"><span class="h-strip h-strip-head">${STRIP.map(([w,l])=>`<span title="${esc(STRIP_TITLE[w])}">${l}</span>`).join('')}</span></th>`
      +(showEdge?`<th class="h-c-edge" title="${esc(EDGE_WHY())}">Edge <small>vs break-even</small></th>`:'')
      +'<th class="h-c-compare">Compare</th><th class="h-c-add"><span class="u-sr">Add to slip</span></th></tr>';
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
    const hidden=currentLines().filter(r=>(H.sport==='all'||r.sport===H.sport)&&!matches(r)).length;
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
    for(const r of list){const k=boardPlayerKey(r);if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push(r);}
    const players=[...grouped.values()],size=40,pages=Math.max(1,Math.ceil(players.length/size));
    H.page=Math.min(H.page,pages-1);
    const page=players.slice(H.page*size,(H.page+1)*size);
    H.showEdge=!!COLS&&page.some(g=>(openPlayers.has(boardPlayerKey(g[0]))?g:[g[0]]).some(r=>edgeValue(r)!=null));
    $('hub-table').tHead.innerHTML=headHtml(H.showEdge);$('hub-table').classList.toggle('h-has-edge',H.showEdge);
    const cols=9+(H.showEdge?1:0), headers=H.sortBy==='start';
    $('hub-sort-note').textContent=H.sortFallback?'No line on screen has an edge, so the board is sorted by start time.':'';
    let last=null;
    $('hub-rows').innerHTML=page.map(group=>{const r=group[0],g=gameKey(r),head=headers&&g!==last?gameRow(r,list,cols):'';last=g;
      return head+row(r,group)+(openPlayers.has(boardPlayerKey(r))?group.slice(1).map(x=>row(x)).join(''):'');}).join('');
    $('hub-count').textContent=`${players.length.toLocaleString()} players · ${list.length.toLocaleString()} lines`;
    $('hub-clear').hidden=!activeFilters()&&H.book==='all'&&!H.q;
    $('hub-sources').innerHTML=H.loading?$('hub-sources').innerHTML:sourcesHtml();
    /* lc-1 (SCOUT_LETTER_CONTAINMENT, off unless the owner turns it on): one plain-fact banner per visible sport whose letters
       the server holds; those rows read Lean Over/Under with the chance, no letter, 0U */
    const heldSports=[...new Set(list.filter(r=>r.scout.letter_held).map(r=>r.sport))];
    // the Sports grades note belongs to the NBA and NFL boards only
    $('hub-health').innerHTML=(isExtra(H.book)?extraBanner():'')+heldSports.map(sp=>`<p class="h-letter-hold" role="status">${esc(SPORTS[sp]||sp.toUpperCase())} letters are held until stated chances match results.</p>`).join('')+(['nba','nfl'].includes(H.sport)&&list.some(r=>r.scout.validation?.kind==='research')?`<details><summary>About ${esc(SPORTS[H.sport])} grades</summary><p>Published letters rank the model’s current picks. NBA and NFL calibration has not passed validation; see Record for stated chances and actual results.</p></details>`:'');
    const games=H.view==='games';
    $('hub-board-section').classList.toggle('h-by-game',games);
    $('hub-board-section').querySelectorAll('[data-hub="view"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.id===(games?'games':'list'))));
    $('hub-games-body').hidden=!games;
    if(games)paintGames(list);
    $('hub-empty').hidden=games||list.length>0;
    if(H.loading)$('hub-empty').textContent='Loading the available boards…';
    else if(!list.length)$('hub-empty').innerHTML=emptyReasons();
    $('hub-pagination').innerHTML=games||!players.length?'':`<span>${H.page*size+1}–${Math.min((H.page+1)*size,players.length)} of ${players.length} players</span><button data-hub="page" data-id="-1" ${H.page===0?'disabled':''}>Previous</button><button data-hub="page" data-id="1" ${H.page===pages-1?'disabled':''}>Next</button>`;
    $('hub-views').innerHTML='<option value="">Choose a saved view</option>'+H.views.map((v,i)=>`<option value="${i}">${esc(v.name)}</option>`).join('');
    paintStrip();
    restoreFocus();
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
  function extraDetail(r) {
    const o=r.offer,x=r.extra||{},price=sd=>!o[sd]?.offered?'Not offered':num(o[sd].price)?o[sd].price.toFixed(2)+' decimal':num(o[sd].payout)?o[sd].payout.toFixed(2)+'x pick multiplier':'Price unavailable';
    return `<header class="h-card-head"><button class="h-back" data-hub="card-close" aria-label="Back to the board"><span aria-hidden="true">←</span> Back</button><div class="h-card-id"><h3 id="hub-card-title" tabindex="-1">${esc(r.player.name)} <span>${esc(r.market.stat_label)} · ${esc(r.market.period_label)}</span></h3><p>${esc(r.player.team)} · ${esc(r.event.label)} · ${esc(when(r.event.start_utc))}</p></div></header>
      <div class="h-extra-detail"><h3>${esc(o.platform_label)} · ${esc(o.line)}</h3><dl class="h-extra-kv"><div><dt>Over</dt><dd>${price('over')}</dd></div><div><dt>Under</dt><dd>${price('under')}</dd></div><div><dt>Read</dt><dd>${esc(o.seen_at?age(o.seen_at):'—')}</dd></div><div><dt>Scout's read</dt><dd>${x.same_line_as?`Same line as ${esc(x.same_line_as)}: ${r.scout.side?esc(sideWord(r.scout.side))+' '+pct(r.scout.p_side):'no side'}`:'No saved chance at this line'}</dd></div></dl><p>View only. Slips are PrizePicks or Underdog entries; open ${esc(o.platform_label)} to play this line.</p></div>`;
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
  async function load(force=false, sports=visibleSports()) {
    if(H.loading){H.queued=[...new Set([...(H.queued||[]),...sports])];H.queuedForce=H.queuedForce||force;return;}
    H.loading=true; paintBoard();
    await Promise.allSettled(sports.map(s=>C.loadBoard(s,force)));
    H.lastLoad=Date.now();
    H.records=allowed().flatMap(s=>[...R.boards[s].recs.values()]);
    const current=rec(H.detail),shown=H.detailDoc?.record;
    if(shown&&(!current||current.offer.revision!==shown.offer.revision||current.scout.read_rev!==shown.scout.read_rev)){H.detailDoc=null;H.detailState='error';H.detailSeq++;paintDetail();}
    H.loading=false; paintBoard(); checkAlerts();
    if(isExtra(H.book))loadExtra();
    loadBookLines();
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
          await api('state',{rev:state.rev,prefs:{...state.prefs,researchHub:{views:H.views,watch:H.watch,alerts:H.alerts,slipLines:H.slipLines}}});
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
          sort:['start','player','chance','edge'].includes(f.sort)?f.sort:'start',stat:typeof f.stat==='string'?f.stat.slice(0,150):'all',game:typeof f.game==='string'?f.game.slice(0,150):'all',onlyDiff:f.onlyDiff===true,watched:f.watched===true}};
      });
      H.views=clean(p.views);
      /* only a choice made in the Goblins control counts (prefs.researchHub.slipLines). A batch saved before it existed carries the old
         Lines select's 'all' (its first option and default), which is not a choice to allow goblins, so it reads as No goblins; the
         old 'Standard lines only' was a deliberate opt-out and carries over as No goblins or demons (review 2026-11-05). */
      H.slipLines=cleanLines(p.slipLines||(state.studio?.options?.lines==='standard'?'standard':null));T.slipLines=H.slipLines;
      if($('hub-generation-lines'))$('hub-generation-lines').value=H.slipLines;
      C.refreshAdvice?.();
      H.watch=Array.isArray(p.watch)?p.watch.filter(v=>typeof v==='string').slice(0,100):[];
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
      <h3>${BG?'Your alerts':'Your saved filters'}</h3>${H.alerts.length?H.alerts.map((a,i)=>`<div class="h-alert"><b>${esc(a.name)}</b><span>${esc(a.filters.sport==='all'?'All sports':SPORTS[a.filters.sport]||a.filters.sport)} · ${esc(a.filters.book)}</span><button data-hub="delete-alert" data-id="${i}">Remove</button></div>`).join(''):`<p>Nothing saved yet. Set your board filters, then choose Views › ${BG?'Alert me on these filters':'Live highlight these filters'}.</p>`}
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
    if(!cardOpen())H.cardFrom=document.activeElement;
    if(r.extra){H.expanded=id;H.detail=id;H.detailSeq++;$('hub-card').hidden=false;$('u-hub').classList.add('h-card-open');paintBoard();paintDetail();$('hub-card-title')?.focus({preventScroll:true});return Promise.resolve();}
    const sd=r.scout.side;
    H.expanded=id;H.histN=10;H.histSide=sd&&r.offer[sd]?.offered?sd:r.offer.over.offered?'over':r.offer.under.offered?'under':(sd||'over');
    $('hub-card').hidden=false;$('u-hub').classList.add('h-card-open');
    const done=selectDetail(id);
    $('hub-card-title')?.focus({preventScroll:true});
    return done;
  }
  function closeCard(restore=true) {
    const id=H.expanded;
    H.expanded=null;H.detail=null;H.detailDoc=null;H.detailSeq++;
    $('hub-card').hidden=true;$('u-hub').classList.remove('h-card-open');$('hub-detail').innerHTML='';
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
  const tallyOf = (list,line) => ({n:list.length,over:list.filter(g=>g.value>line).length,under:list.filter(g=>g.value<line).length,push:list.filter(g=>g.value===line).length});
  /* a window from the loaded games when they cover it, else the board's own count (the newest results, n may be below the window) */
  function windowTally(r,all,n) {
    const list=all.slice(0,n), saved=C.tallies(r)['l'+n];
    if(list.length&&(list.length>=n||!saved||!saved.n))return tallyOf(list,r.offer.line);
    return saved&&saved.n?saved:null;
  }
  /* head-to-head vs tonight's opponent at tonight's line (owner 2026-11-04: as on the classic CS2 card); past results, not a chance */
  function h2hHtml(hh,line,side) {
    if(!hh||!hh.opponent)return '';
    const head=`<div class="h-sec-head"><h4>Head-to-head vs ${esc(hh.opponent)}</h4></div>`;
    if(!hh.n)return `<section class="h-h2h">${head}<p class="h-history-note">No past meetings with ${esc(hh.opponent)} in this player's saved results.</p></section>`;
    return `<section class="h-h2h">${head}<div class="h-mini-tiles"><div><b>${countOf(hh,side)}<em>/${hh.n}</em></b><span>${sideWord(side)} ${esc(line)}${hh.push?` · ${hh.push} ${hh.push===1?'push':'pushes'}`:''}</span></div><div><b>${num(hh.avg)?hh.avg.toFixed(1):'—'}</b><span>Average in ${hh.n} ${hh.n===1?'meeting':'meetings'}</span></div></div>
      <table class="h-h2h-table"><thead><tr><th>Date</th><th>Result</th><th>At ${esc(line)}</th></tr></thead><tbody>${hh.series.map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.value)}</td><td class="h-at-${atLine(g.value,line).toLowerCase()}">${atLine(g.value,line)}</td></tr>`).join('')}</tbody></table>
      <p class="h-history-note">Past meetings at today's line. Not a predicted chance.</p></section>`;
  }
  const factText = v => typeof v==='object'?JSON.stringify(v):String(v);
  /* every saved measure under its own heading (context rows carry a group); nothing is computed here except the map split of loaded games */
  function statsHtml(r,doc,all) {
    const rows=(doc?.context?.rows||[]).filter(f=>f.label&&f.value!=null), groups=new Map();
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
    const list=(H.detailDoc?.history?.series||[]).filter(g=>num(g.value)).slice(0,H.histN);
    if(list.length)el.innerHTML=C.chartSvg(list,r.offer.line,H.histSide,Math.max(260,Math.round(el.clientWidth)||560));
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
    const eligible=H.detailState==='ready'&&!unavailableReason(r)&&!C.started(r)&&r.offer[side].offered;
    const chance=C.chanceOf(r,side);
    const pick=s.side&&side===s.side?gradeChip(r):s.side?`<span class="h-grade-text">Scout side is ${sideWord(s.side)}</span>`:s.hold?'<span class="h-grade-text">Research estimate</span>':'';
    const opt=q=>`<option value="${esc(key(q))}"${key(q)===key(r)?' selected':''}>${esc(q.offer.platform_label)} ${esc(q.offer.line)}${q.market.line_type!=='standard'?' · '+esc(C.lineLabel(q)):''}</option>`;
    return `<label class="h-dec-line"><span>Line</span><select id="hub-card-line" aria-label="Line and book">${quotes.map(opt).join('')}</select></label>
      <div class="h-segment h-side-toggle" role="group" aria-label="Side">${['over','under'].map(sd=>`<button data-hub="history-side" data-id="${sd}" aria-pressed="${side===sd}">${sideWord(sd)}</button>`).join('')}</div>
      <div class="h-dec-chance"><b title="Forecast chance · ${sideWord(side)} ${esc(line)}">${chance==null?'—':pct(chance)}</b>${pick?`<span class="h-dec-pick">${pick}</span>`:''}</div>
      <div class="h-dec-proj"><b>${num(s.projection)?esc(s.projection.toFixed(1)):'—'}</b><span>Proj.</span></div>
      <div class="h-dec-act"><button class="h-dec-add${on?' is-on':''}" data-hub="detail-add" ${on||eligible?'':'disabled'}>${on?'On slip ✓':`Add ${sideWord(side)} ${esc(line)}`}</button>${R.me.features?.book_links&&r.offer.platform==='underdog'?`<button data-hub="open-line" ${eligible?'':'disabled'}>Open in Underdog</button>`:''}</div>
      ${!r.offer[side].offered?'<small class="h-dec-note">This side is not offered on the selected platform.</small>':''}`;
  }
  function overviewHtml(r) {
    const side=H.histSide, line=r.offer.line, doc=H.detailDoc;
    const all=(doc?.history?.series||[]).filter(g=>num(g.value)), series=all.slice(0,H.histN);
    const sorted=series.map(g=>g.value).sort((a,b)=>a-b), mid=Math.floor(sorted.length/2);
    const median=sorted.length?(sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2):null;
    const tile=(value,label,extra='',cls='')=>`<div class="h-tile${cls}"><b>${value}</b><span>${label}</span>${extra}</div>`;
    const hitTiles=[5,10,20].map(n=>{
      const t=windowTally(r,all,n), pending=!t&&n!==10&&C.hitsPending(r);
      return tile(t?`${countOf(t,side)}<em>/${t.n}</em>`:pending?'<i class="h-hit-wait" aria-hidden="true"></i>':'—',`L${n} · ${sideWord(side)} ${esc(line)}`,t&&t.n<n?`<small>${t.n} ${t.n===1?'game':'games'}${t.push?` · ${t.push} ${t.push===1?'push':'pushes'}`:''}</small>`:t?.push?`<small>${t.push} ${t.push===1?'push':'pushes'}</small>`:'',H.histN===n?' is-window':'');
    }).join('');
    const hh=doc?.h2h, h2hTile=hh?.opponent&&hh.n?tile(`${countOf(hh,side)}<em>/${hh.n}</em>`,`H2H vs ${esc(hh.opponent)} · ${sideWord(side)}`):'';
    const se=COLS?C.tallies(r).season:null, seasonTile=se?.n?tile(`${countOf(se,side)}<em>/${se.n}</em>`,`Season · ${sideWord(side)} ${esc(line)}`):'';
    /* L10 vs closing (SCOUT_CLOSING_LINES): only when the server sent past games with a closing line saved at lock */
    const cl=doc?.history?.closing, clCount=cl?.n?(side==='under'?cl.under:cl.over):null;
    const closingTile=cl?.n?tile(`${clCount}<em>/${cl.n}</em>`,`L10 vs closing · ${sideWord(side)}`,`<small>${cl.n} ${cl.n===1?'game':'games'} with a closing line${cl.push?` · ${cl.push} ${cl.push===1?'push':'pushes'}`:''}</small>`,' is-closing'):'';
    const closingTable=cl?.n?`<details class="h-history-table h-closing-table"><summary>L10 vs closing lines${cl.platform_label?` · ${esc(cl.platform_label)}`:''}</summary><div class="h-scroll-x"><table><thead><tr><th>Date</th><th>Opponent</th><th>Result</th><th>Closing line</th><th>At closing</th></tr></thead><tbody>${(cl.games||[]).map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.opponent)}</td><td>${esc(g.value)}</td><td>${esc(g.closing)}</td><td class="h-at-${esc(String(g.at).toLowerCase())}">${esc(g.at)}</td></tr>`).join('')}</tbody></table></div></details>`:'';
    const chart=H.detailState==='loading'?'<p class="h-detail-loading">Loading this player’s saved results…</p>'
      :H.detailState==='error'?''
      :series.length?`<div class="h-history-chart" data-hub-chart role="img" aria-label="${series.length} past results compared with ${side} ${esc(line)}"></div><div class="h-legend"><span><i class="hit"></i>${sideWord(side)} hit</span><span><i class="miss"></i>Miss</span><span><i class="push"></i>Push</span><span><i class="line"></i>Line ${esc(line)}</span></div><details class="h-history-table"><summary>View dates and results</summary><div class="h-scroll-x"><table><thead><tr><th>Date</th><th>Opponent</th><th>Result</th><th>At ${esc(line)}</th></tr></thead><tbody>${series.map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.opponent)}</td><td>${esc(g.value)}</td><td class="h-at-${atLine(g.value,line).toLowerCase()}">${atLine(g.value,line)}</td></tr>`).join('')}</tbody></table></div></details>`
      :'<p class="h-detail-loading">Game-by-game results are not available for this market. The counts above come from its saved board summary.</p>';
    return `<div class="h-history-summary">${hitTiles}${h2hTile}${seasonTile}${closingTile}</div>
      <section class="h-history-panel"><div class="h-sec-head"><h4>Last ${series.length||H.histN} results · ${sideWord(side)} ${esc(line)}</h4><div class="h-history-controls"><div class="h-segment" role="group" aria-label="History window">${[5,10,20].map(n=>`<button data-hub="history-window" data-id="${n}" aria-pressed="${H.histN===n}">L${n}</button>`).join('')}</div></div></div>
        ${series.length?`<p class="h-history-stats"><span>Average <b>${(series.reduce((a,g)=>a+g.value,0)/series.length).toFixed(1)}</b></span>${V2?`<span>Median <b>${median.toFixed(1)}</b></span>`:''}<span>${series.length} ${series.length===1?'game':'games'}${series.length<H.histN?' on file':''}</span></p>`:''}
        ${chart}<p class="h-history-note">History uses today’s selected line, not the closing lines. Past hit rate is not a predicted chance.</p>${closingTable}</section>
      ${h2hHtml(hh,line,side)}`;
  }
  /* Lines: the card's Compare lines and the old drawer's Books table in one place: every book and line for this prop, its prices,
     the Scout read, when the offer was seen; then the market books and the market chance when the server has them */
  function linesHtml(r) {
    const doc=H.detailDoc, quotes=C.siblings(r);
    const quoteText=(q,sd)=>{
      const o=q.offer[sd];
      if(!o.offered)return 'Not offered';
      const kind=C.PLATFORMS[q.offer.platform]?.kind;
      if(kind==='decimal')return num(o.payout)?o.payout.toFixed(2):'Odds unavailable';
      if(num(o.price))return o.price.toFixed(2);
      if(num(o.payout)&&o.payout!==1)return o.payout.toFixed(2)+'× modifier';
      return q.market.line_type==='standard'?'Standard':C.lineLabel(q);
    };
    const mb=doc?.market_books, extra=mb?(mb.same||[]).filter(b=>!quotes.some(q=>q.offer.platform===b.platform&&q.offer.line===r.offer.line)):[], other=mb?.other||[];
    const price=v=>v==null?'—':esc(v);
    const mrow=(b,showLine)=>`<tr><td>${esc(b.platform_label)}${showLine?` <small>${esc(b.line)}</small>`:''}</td><td>${price(b.over)}</td><td>${price(b.under)}</td><td>${num(b.chance)?pct(b.chance):'—'}</td></tr>`;
    const market=doc?.market?(doc.market.available?`<p class="h-history-note">Market chance from ${esc(doc.market.source||'the market')}: Over ${pct(doc.market.p_over)}, Under ${pct(doc.market.p_under)}.</p>`:doc.market.reason?`<p class="h-history-note">${esc(doc.market.reason)}</p>`:''):'';
    return `<section class="h-line-shop"><div class="h-sec-head"><h4>Books and lines</h4><small>Same event · stat · period</small></div><div class="h-scroll-x"><table><thead><tr><th>Book / line</th><th>Over</th><th>Under</th><th>Scout</th><th>Seen</th></tr></thead><tbody>${quotes.map(q=>`<tr class="${key(q)===H.detail?'is-selected':''}"><td><button data-hub="detail-line" data-id="${esc(key(q))}" aria-pressed="${key(q)===H.detail}"><span>${esc(q.offer.platform_label)}${q.market.line_type!=='standard'?` <small>${esc(C.lineLabel(q))}</small>`:''}</span><b>${esc(q.offer.line)}</b></button></td><td>${esc(quoteText(q,'over'))}</td><td>${esc(quoteText(q,'under'))}</td><td>${q.scout.hold?'Research estimate':esc(C.verdictText(q))}${num(q.scout.p_side)?` <b>${pct(q.scout.p_side)}</b>`:''}</td><td title="${esc(q.offer.seen_at||'')}">${q.offer.seen_at?esc(age(q.offer.seen_at)):'—'}</td></tr>`).join('')}</tbody></table></div>
      <p class="h-history-note">Choose a line to recount its past results. Each line is its own pick with its own forecast; payouts and availability differ.</p>
      ${extra.length||other.length?`<div class="h-sec-head"><h4>Market books</h4></div><div class="h-scroll-x"><table><thead><tr><th>Book</th><th>Over</th><th>Under</th><th>Its chance</th></tr></thead><tbody>${extra.map(b=>mrow(b,false)).join('')}${other.map(b=>mrow(b,true)).join('')}</tbody></table></div>`:''}${market}</section>`;
  }
  function statsTabHtml(r) {
    const doc=H.detailDoc, all=(doc?.history?.series||[]).filter(g=>num(g.value));
    const full=['nba','nfl'].includes(r.sport)&&r.player.id!=null?`<p class="h-full-research"><a href="${esc(`/sports?view=classic#/player/${encodeURIComponent(r.player.id)}?lg=${r.sport}&stat=${encodeURIComponent(r.market.stat)}`)}" target="_blank" rel="noopener">Full player research ↗</a></p>`:'';
    if(H.detailState==='loading')return full+'<p class="h-detail-loading">Loading this player’s saved stats…</p>';
    const body=statsHtml(r,doc,all)+(r.sport==='cs2'?mapChancesHtml(doc?.map_chances):'');
    return full+(body||'<p class="h-detail-loading">No saved stats are on file for this player and market.</p>');
  }
  /* Ask: the Genie (SCOUT_GENIE) answers one question about this line from its own payload, every number checked by the server;
     the evidence guide sits folded underneath (open when the Genie is off) */
  const G={key:null,q:'',busy:false,ans:null,err:null,quota:null};
  const SUGGEST=['What is the projection?','How often did this line hit?','What is the head-to-head?','Is there a better line elsewhere?'];
  function askHtml(r) {
    const side=H.histSide, k=key(r)+'|'+r.offer.revision+'|'+side;
    if(G.key!==k)Object.assign(G,{key:k,ans:null,err:null});
    const genie=R.me.features?.genie===true, s=r.scout, t=C.tallies(r).l10, other=C.siblings(r).filter(x=>x.offer.platform!==r.offer.platform), chance=C.chanceOf(r,side);
    // SCOUT_\u{1F9DE}_PERSONA (features.genie_persona): a light genie voice in the intro and the answer opener; the checked sentences are unchanged
    const persona=genie&&R.me.features?.genie_persona===true;
    const out=G.busy?'<p class="h-detail-loading">Checking this line’s saved numbers…</p>':G.err?`<p>${esc(G.err)}</p>`:G.ans?(persona&&G.intro?`<p class="h-genie-intro">${esc(G.intro)}</p>`:'')+G.ans.map(x=>`<p>${esc(x)}</p>`).join(''):'';
    const form=genie?`${persona?`<p class="h-genie-intro">\u{1F9DE} Ask the Genie. I grant answers from this line's saved Scout data only, never a made-up stat.</p>`:''}<form class="h-ask" id="hub-ask" novalidate><label for="hub-ask-q">${persona?'Make a wish about this line':'Ask about this line'}</label><div class="h-ask-row"><input id="hub-ask-q" type="text" maxlength="300" autocomplete="off" value="${esc(G.q)}"><button type="submit"${G.busy?' aria-disabled="true"':''}>Ask</button></div>
      <div class="h-ask-sugg" role="group" aria-label="Suggested questions">${SUGGEST.map(x=>`<button type="button" data-hub="genie-ask" data-id="${esc(x)}">${esc(x)}</button>`).join('')}</div>
      <div class="h-ask-out" id="hub-ask-out" role="status" aria-live="polite">${out}</div>${G.quota?`<p class="h-history-note">${Number(G.quota.remaining_questions)} of ${Number(G.quota.questions_limit)} questions left today.</p>`:''}</form>`:'';
    return `${form}<details class="h-guide"${genie?'':' open'}><summary>Scout evidence guide</summary><p class="h-history-note">Answers use this offer's saved forecast and research data only.</p>
      <h5>What supports this pick?</h5><p>${esc(r.player.name)} has a saved projection of ${num(s.projection)?esc(s.projection.toFixed(1)):'unavailable'} for ${esc(r.market.period_label)} ${esc(r.market.stat_label)}. ${chance==null?'No probability was saved for the selected side.':`The saved chance for ${sideWord(side)} ${esc(r.offer.line)} is ${pct(chance)}.`}${s.hold?' This is a research estimate; the validation gate has not released official grades.':''}</p>
      <h5>How often did this line hit?</h5><p>${t?.n?`${countOf(t,side)} of the last ${t.n} results hit ${sideWord(side)} at today's line of ${esc(r.offer.line)}; ${t.push||0} pushed.`:'Comparable past-game results are not available for this line.'} <button class="h-link" data-hub="card-tab" data-id="overview">See each result</button></p>
      <h5>Is there a better line elsewhere?</h5><p>${other.length?other.slice(0,8).map(x=>`${esc(x.offer.platform_label)} lists ${esc(x.offer.line)} (${esc(x.market.line_type)}), seen ${esc(when(x.offer.seen_at))}.`).join(' '):'No other platform offer is available for this exact event, player, stat and period.'} <button class="h-link" data-hub="card-tab" data-id="lines">Compare lines</button></p>
      <h5>What should I verify before tracking?</h5><p>Confirm ${esc(r.market.period_label)}, the ${esc(r.offer.platform_label)} line and offered side, the start time, and any player context. Scout rechecks the exact revision when you track.${s.stake_reason?' '+esc(s.stake_reason):''} <button class="h-link" data-hub="card-tab" data-id="stats">See saved stats</button></p></details>`;
  }
  async function genieAsk(text) {
    const r=rec(H.detail);if(!r||G.busy)return;
    const box=$('hub-ask-q'), q=(text||(box?box.value:'')||'').trim();
    G.q=q;
    if(!q){G.err='Type a question about this line first.';paintBody();return;}
    const k=G.key;
    Object.assign(G,{busy:true,err:null,ans:null});paintBody();
    try {
      const d=await api('genie',{sport:r.sport,book:r.offer.platform,id:r.offer.offer_id,revision:r.offer.revision,side:H.histSide,question:q});
      if(G.key!==k)return;
      G.ans=Array.isArray(d.sentences)?d.sentences:[];G.quota=d.quota||null;G.intro=typeof d.intro==='string'?d.intro:null;
    } catch(e) {
      if(G.key!==k)return;
      const c=e.status||0;
      if(c===429&&e.doc?.quota)G.quota=e.doc.quota;
      G.err=c===429?(e.doc?.error==='daily_limit_reached'?(R.me.features?.genie_persona?"\u{1F9DE} Your wishes are spent for today. They reset at midnight UTC.":"You have used today's questions. They reset at midnight UTC."):'Too many questions in a minute. Try again shortly.')
        :c===409?'This line changed. Reopen it to ask about the current offer.':c===413?'Keep the question under 300 characters.':'The Genie is unavailable right now.';
    } finally {if(G.key===k){G.busy=false;paintBody();}}
  }
  function tabHtml(r) {
    const warn=H.detailState==='error'?`<p class="h-warning">This offer changed or research is unavailable. <button data-hub="detail-retry">Retry research</button> <button data-hub="refresh">Refresh board</button></p>`:'';
    return warn+(H.cardTab==='lines'?linesHtml(r):H.cardTab==='stats'?statsTabHtml(r):H.cardTab==='ask'?askHtml(r):overviewHtml(r));
  }
  function paintDetail() {
    const box=$('hub-detail'),r=rec(H.detail);if(!box||!r||!cardOpen())return;
    if(r.extra){box.innerHTML=extraDetail(r);return;}
    const restoreFocus=focusAfter(box), body=$('hub-card-body'), top=body?body.scrollTop:0, wasTab=box.dataset.tab, wasId=box.dataset.id;
    const playerMarkets=[...new Map(H.records.filter(x=>x.sport===r.sport&&x.player.key===r.player.key&&x.event.id===r.event.id&&x.offer.platform===r.offer.platform&&!C.started(x)).map(x=>[x.market.stat+'|'+x.market.period,x])).values()];
    const watched=H.watch.includes(playerKey(r));
    box.innerHTML=`<header class="h-card-head"><button class="h-back" data-hub="card-close" aria-label="Back to the board"><span aria-hidden="true">←</span> Back</button>
        <div class="h-card-id"><h3 id="hub-card-title" tabindex="-1">${esc(r.player.name)} <span>${esc(r.market.stat_label)} · ${esc(r.market.period_label)}</span></h3><p>${esc(r.player.team)} · ${esc(r.event.label)}${r.event.competition?` · ${esc(r.event.competition)}`:''} · ${esc(when(r.event.start_utc))} ${countdownHtml(r)}${r.offer.moved_from!=null?` · moved from ${esc(r.offer.moved_from)}`:''}</p></div>
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
        research:$('hub-generation-research')?.value==='include',sports};
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
    const lineTag=l=>l.gen?.line_type&&l.gen.line_type!=='standard'?` <em class="h-line-tag">${esc(l.gen.line_type[0].toUpperCase()+l.gen.line_type.slice(1))}</em>`:'';
    box.innerHTML=`<header class="h-batch-head"><div><span class="h-eyebrow">GENERATED LINEUPS</span><h2>${slips.length} separate slips</h2><p>${H.batchSaved?'Saved to your account':'Not yet saved across devices'} · generated ${esc(when(b.created_at))}${b.options?.noRepeatProps?' · No repeat props':''}${{no_goblins:' · No goblins',one_goblin:' · At most 1 goblin',standard:' · No goblins or demons'}[b.options?.lines]||''}</p></div>${!H.batchSaved?'<button data-hub="save-batch">Retry save</button>':''}</header><p class="h-batch-note">Ranked by what each chance band has won on settled results. The chance beside a leg is the model’s published chance; all-hit chance multiplies the ranked leg chances across different matches, and a push counts as not winning. Repeated players across slips share risk. Every slip is rechecked when opened.</p><div class="h-batch-grid">${slips.map((s,i)=>{
      const metrics=[num(s.all_hit)?`<span>All-hit chance <b>${pct(s.all_hit)}</b></span>`:(s.same_match?'<span>Legs share a match <b>No all-hit chance</b></span>':''),
        s.payout&&num(s.payout.multiple)?`<span>All-hit payout <b>${esc(s.payout.multiple)}×</b></span>`:'',
        s.ev&&num(s.ev.ev)?`<span>Expected return <b>${pct(s.ev.ev)}</b></span>`:''].filter(Boolean).join('');
      return `<article class="h-generated-card"><header><span>SLIP ${String(i+1).padStart(2,'0')}</span><strong>${esc(C.PLATFORMS[s.platform].label)} · ${esc(s.entry_type)}</strong><small>${s.legs.length} picks · ${esc([...new Set(s.legs.map(l=>SPORTS[l.sport]))].join(' + '))}</small></header>${s.why?`<p class="h-batch-why">${esc(s.why)}</p>`:''}${metrics?`<div class="h-batch-range">${metrics}</div>`:''}<ol>${s.legs.map(l=>`<li><div><strong>${esc(l.player)}</strong>${l.gen?.research?' <em class="h-line-tag">Research estimate</em>':''}<small>${esc(SPORTS[l.sport])} · ${esc(l.event_label)}</small><span>${esc(l.side)} <b>${esc(l.line)}</b> ${esc(l.stat_label)} · ${esc(l.period_label)}${lineTag(l)}</span>${l.gen?.record?`<small class="h-batch-record">${esc(l.gen.record)}</small>`:''}<small>${use.get(l.sport+'|'+l.player_key)} of ${slips.length} slips use this player</small></div><b>${pct(l.shown_p)}</b></li>`).join('')}</ol><footer><button data-hub="use-generated" data-id="${i}">${T.legs.some(l=>l.book===s.platform)?'Replace the '+esc(C.PLATFORMS[s.platform].label)+' entry in My slip':'Open in My slip'}</button>${R.me.features?.book_links?`<button data-hub="open-generated" data-id="${i}">Open in ${esc(C.PLATFORMS[s.platform].label)}</button>`:""}<small id="hub-batch-message-${i}" role="status"></small></footer></article>`;
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
    return `<section class="h-generator" aria-label="Generate slips"><span class="h-eyebrow">LINEUP GENERATOR</span><h2>Build your batch</h2><p>Each slip is built for the best chance that all its legs hit, ranked by how each chance band has done on settled results.</p><fieldset id="hub-generation-sports"><legend>Eligible sports</legend>${allowed().map(s=>`<label><input type="checkbox" value="${s}" checked><span>${SPORTS[s]}</span></label>`).join('')}</fieldset><div class="h-generator-controls">
      <label>Platform<select id="hub-generation-book"><option value="prizepicks">PrizePicks</option><option value="underdog">Underdog</option></select></label>
      <label>Entry type<select id="hub-generation-type"><option value="power">Power</option><option value="flex">Flex</option></select></label>
      <label>Number of slips<select id="hub-generation-slips">${[1,2,4,8,12].map(n=>`<option value="${n}" ${n===8?'selected':''}>${n} slips</option>`).join('')}</select></label>
      <label>Picks per slip<select id="hub-generation-count">${[2,3,4,5,6].map(n=>`<option value="${n}">${n} picks</option>`).join('')}</select></label>
      <label>Sport mix<select id="hub-generation-mix"><option value="any">Any mix</option><option value="mixed">Mixed sports</option><option value="single">One sport</option></select></label>
      <label class="h-goblin-rule">Goblins<select id="hub-generation-lines" aria-describedby="hub-generation-lines-help">${LINE_RULES.map(([v,t])=>`<option value="${v}"${v===H.slipLines?' selected':''}>${t}</option>`).join('')}</select><small id="hub-generation-lines-help">Goblins are easier lines that pay less. Scout leaves them out unless you allow them.</small></label>
      <label>Game selection<select id="hub-generation-games"><option value="spread">One per match</option><option value="stacks">Same match (Underdog)</option></select></label>
      <label>Prop reuse<select id="hub-generation-repeats"><option value="allow">Allow across slips</option><option value="none">No repeat props</option></select></label>
      ${research?'<label>Research estimates<select id="hub-generation-research"><option value="leave">Leave out</option><option value="include">Include, marked</option></select></label>':''}
      <button id="hub-generate" data-hub="generate">Generate slips</button></div><p id="hub-generation-status" role="status" aria-live="polite"></p><details><summary>How lineups are built</summary><p>Only open lines enter: the platform feed is current, the match has not started, Scout’s side is offered and the line is not held. CS2 legs carry a live grade of B or better with a stake; LoL legs carry the model’s letter; NBA and NFL research estimates stay out unless you include them, and are then marked. Each leg ranks by what its chance band (sport, stat, side and chance) has won on settled results, so a band that has not held up ranks lower. A slip takes the combination with the best all-hit chance: one leg per player, one leg per match unless you choose same-match legs on Underdog, and the platform’s entry rules. All-hit chance multiplies the leg chances across different matches; a push counts as not winning. Goblins (PrizePicks lines set below the standard line, which pay less) stay out of every slip and every suggested swap unless you allow them under Goblins: at most one per slip, or any number; No goblins or demons keeps standard lines only. A goblin you add yourself is never removed. No repeat props uses each player’s stat and period once across the batch, every line and side included; with repeats allowed, a leg already used ranks lower, and the same slip is never built twice. Every slip is rechecked against the current offer before it appears.</p></details></section>`;
  }
  /* 30 days of the record; a run of days with no decided pick folds into one quiet tile instead of a wall of dashes */
  function calendarHTML(doc){
    if(!doc?.days?.length)return '<p class="h-muted">Daily history will appear after the next Record update.</p>';
    const out=[];let run=[];
    const flush=()=>{if(!run.length)return;const a=run[0].date.slice(5),b=run[run.length-1].date.slice(5);
      out.push(`<div class="h-cal-gap" role="note" aria-label="${esc(`${run[0].date}${run.length>1?' to '+run[run.length-1].date:''}: no decided picks`)}"><time>${esc(a)}${run.length>1?' – '+esc(b):''}</time><small>${run.length>1?`${run.length} days · `:''}none decided</small></div>`);run=[];};
    for(const d of doc.days){
      if(!d.decided&&!d.wins&&!d.losses&&!d.pushes){run.push(d);continue;}
      flush();
      out.push(`<details class="${d.decided?'has-results':''}"><summary aria-label="${esc(d.date)}: ${d.wins} wins, ${d.losses} losses, ${d.pushes} pushes"><time>${esc(d.date.slice(5))}</time><b>${num(d.hit_rate)?Math.round(d.hit_rate*100)+'%':'—'}</b><small>${d.decided} decided</small></summary><p>${d.wins} wins · ${d.losses} losses · ${d.pushes} pushes</p></details>`);
    }
    flush();
    return `<section class="h-record-calendar" aria-label="30-day accuracy calendar"><header><h3>Last 30 days</h3><span>${esc(doc.basis)}</span></header><div class="h-calendar-grid">${out.join('')}</div><p>Accuracy only, not profit. ${doc.missing_dates?`${Number(doc.missing_dates)} records have no saved date and are excluded from this calendar.`:'Pushes are listed separately.'}</p></section>`;
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
      return `<article class="h-record-card"><header><h2>${SPORTS[s]}</h2><span>${s==='nba'||s==='nfl'?'Research forecasts':'Saved forecasts'}</span></header><div class="h-score"><div><b>${t.wins??'—'}<em> / </em>${t.losses??'—'}</b><span>Wins / losses · ${t.pushes??'—'} pushes separately</span></div><div><b>${pct(t.observed)}</b><span>Actual hit rate</span></div><div><b>${pct(t.printed)}</b><span>Average stated chance</span></div></div>
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
    box.innerHTML=ordered.length?ordered.map(([k,rs])=>{const r=rs[0],players=new Set(rs.map(x=>x.player.key)),markets=new Set(rs.map(x=>x.market.stat)),books=new Set(rs.map(x=>x.offer.platform_label));return `<article class="h-game-card"><header><span class="h-eyebrow">${esc(SPORTS[r.sport])}${r.event.competition?' · '+esc(r.event.competition):''}</span><time>${esc(when(r.event.start_utc))} ${countdownHtml(r)}</time></header><h2>${esc(r.event.label)}</h2><p>${players.size} players · ${markets.size} markets · ${rs.length} lines</p><div class="h-game-books">${[...books].map(b=>`<span>${esc(b)}</span>`).join('')}</div><button data-hub="game-board" data-id="${esc(k)}">Show this game's props <span aria-hidden="true">→</span></button></article>`;}).join('')
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
      ${classic?card('CLASSIC','Classic boards','The original boards, with every original filter.',classic):''}${R.me.features?.market_tools?card('PRICES','Price check','Prices that pay more than they should, and lines where two books let you bet both sides and win either way.','<button data-hub="mt-jump">Open price check ↓</button>'):''}</div>
      <section class="h-tools-alerts" id="hub-tools-alerts" aria-label="${ALERT_WORD}"></section>`;
    if(R.me.owner)$('hub-tools-body').insertAdjacentHTML('beforeend',`<section class="h-market-tools"><div class="h-section-head"><div><span class="h-eyebrow">OWNER REVIEW</span><h2>Grading comparison</h2><p>Published chances beside the candidate grader, from saved forecast revisions.</p></div></div><div class="h-game-books">${['cs2','lol','nba','nfl'].map(s=>`<button data-hub="grade-review" data-id="${s}">${SPORTS[s]}</button>`).join('')}</div><div id="hub-grade-review" aria-live="polite"><p>Select a sport to inspect its comparison log.</p></div></section>`);
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
  const mtText=x=>[x.player,x.team,x.event,x.stat_label,x.book_label,...(x.legs||[]).map(l=>l.book_label)].join(' ').toLowerCase();
  const mtSports=d=>Object.keys(d?.sports||{}).filter(s=>H.mt.league==='all'||H.mt.league===s);
  const mtBook=x=>x.kind!=='pickem';   /* a single bet: sportsbook or prediction market (pick'em multipliers are owner-only) */
  function mtKeep(sport,x,legs){
    const f=H.mt,books=legs?x.legs.map(l=>l.book):[x.book],sides=legs?x.legs.map(l=>l.side):[x.side];
    const age=legs?mtLegsAge(x):(mtAge(x)??Infinity);
    return (f.league==='all'||f.league===sport)&&(f.book==='all'||books.includes(f.book))&&(f.side==='all'||sides.includes(f.side))
      &&(f.fresh==='all'||age<=Number(f.fresh))&&(!f.q||mtText(x).includes(f.q.toLowerCase()));
  }
  /* the Book list follows the Sport filter: only books with a row in the sports in view (a book with no CS2 or LoL row never shows there) */
  function mtBooks(d){
    const out=new Map();
    for(const s of mtSports(d)){const x=d.sports[s]||{};
      for(const r of (x.ev||[]).filter(r=>d.owner||mtBook(r)))out.set(r.book,r.book_label);
      for(const r of [...(x.arbitrage||[]),...(d.owner?x.flagged||[]:[])])for(const l of r.legs||[])out.set(l.book,l.book_label);}
    return [...out.entries()].sort((a,b)=>a[1].localeCompare(b[1])).map(([id,label])=>({id,label}));
  }
  function mtFollowTab(){
    const d=H.marketDoc;if(!d||H.mt.tab===H.sport)return;
    H.mt.tab=H.sport;H.mt.league=H.sport!=='all'&&d.sports?.[H.sport]?H.sport:'all';
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
  function mtEvRow(x){
    return `<li class="h-pc-row"><div class="h-pc-cell h-pc-pick" data-label="Pick">${mtPick(x)}</div>`
      +`<div class="h-pc-cell" data-label="Book & price">${mtPrice(x.book_label,x.american)}${x.kind==='prediction'?'<small>Prediction market</small>':''}${H.marketDoc?.owner&&(x.flags||[]).includes('Wide market')?'<small>Wide market (owner)</small>':''}</div>`
      +`<div class="h-pc-cell h-pc-fair" data-label="Fair chance vs needed"><span class="h-pc-wide">${pct(x.p_cal)} vs ${pct(x.needs)}</span><span class="h-pc-narrow">Fair ${pct(x.p_cal)} · needs ${pct(x.needs)}</span><small>${esc(mtFrom(x))}</small>${num(x.p_push)&&x.p_push>0?`<small>Exactly ${esc(x.line)} refunds the stake (${pct(x.p_push)})</small>`:''}</div>`
      +`<div class="h-pc-cell h-pc-value" data-label="Expected per $100"><span class="h-pc-chip">+${mtMoney(x.per_100)}<i> / $100</i></span></div>`
      +`<div class="h-pc-cell h-pc-age" data-label="Checked">${esc(mtAgo(mtAge(x)))}</div></li>`;
  }
  function mtArbRow(x){
    const leg=(l,i)=>`<div class="h-pc-cell h-pc-leg" data-label="Bet ${i+1}"><span>${esc(l.book_label)} ${esc(mtSide(l.side))} ${esc(x.line)} ${esc(l.american||'')}</span><small>stake <b data-stake-per100="${Number(l.stake_per_100)}">${mtMoney(l.stake_per_100*H.mt.stake/100)}</b></small></div>`;
    const back=(100+Number(x.return_per_100))*H.mt.stake/100;
    return `<li class="h-pc-row h-pc-arb"><div class="h-pc-cell h-pc-pick" data-label="Pick">${mtPick({...x,side:null})}</div>${(x.legs||[]).map(leg).join('')}`
      +`<div class="h-pc-cell h-pc-value" data-label="Either way you get"><span class="h-pc-chip"><b data-back-per100="${100+Number(x.return_per_100)}">${mtMoney(back)}</b> back <i data-gain-per100="${Number(x.return_per_100)}">(+${mtMoney(back-H.mt.stake)})</i></span></div>`
      +`<div class="h-pc-cell h-pc-age" data-label="Checked">${esc(mtAgo(mtLegsAge(x)))}</div></li>`;
  }
  const mtHead=cols=>`<li class="h-pc-row h-pc-head" aria-hidden="true">${cols.map(c=>`<div class="h-pc-cell">${c}</div>`).join('')}</li>`;
  function marketHTML(){
    if(H.marketLoading&&!H.marketDoc)return '<p class="h-mt-state" role="status">Checking current prices…</p>';
    if(H.marketError)return `<p class="h-mt-state h-warning" role="status">${esc(H.marketError)} Use Refresh prices to try again.</p>`;
    if(!H.marketDoc)return '<p class="h-mt-state">Choose Refresh prices to read the latest prices.</p>';
    const d=H.marketDoc,f=H.mt,sports=mtSports(d),owner=!!d.owner;
    const ageText=t=>t==null||!Number.isFinite(t)?'—':t<60?Math.round(t)+'s':t<5400?Math.floor(t/60)+'m':(t/3600).toFixed(1)+'h';
    const builtAge=(Date.now()-Date.parse(d.as_of))/1000;
    const head=Number.isFinite(builtAge)&&builtAge>120?`<p class="h-mt-state" role="status">These prices were built ${ageText(builtAge)} ago. Refresh prices for the current sheet.</p>`:'';
    const ready=sports.filter(s=>d.sports[s]?.state!=='unavailable');
    if(sports.length&&!ready.length)return head+'<p class="h-mt-state" role="status">Prices could not be read just now. Use Refresh prices to try again.</p>';
    let ev=[],arb=[],all=0,quotes=0,lines=0;
    /* the server's own age limits, applied again on every draw: a sheet left open never shows a pair or price it would no longer
       build (review 2026-11-05). The tab also reloads the sheet when it comes back into view (see visibilitychange below). */
    const m0=d.method||{},evMax=Number(m0.ev_stale_s)||1800,arbMax=Number(m0.arb_max_age_s)||300;
    const live=r=>(mtAge(r)??Infinity)<=evMax,liveArb=r=>mtLegsAge(r)<=arbMax;
    for(const s of ready){const x=d.sports[s]||{};
      quotes+=x.quotes_checked||0;lines+=x.markets_checked||0;
      const e=(x.ev||[]).filter(r=>mtBook(r)&&live(r)),a=(x.arbitrage||[]).filter(liveArb);all+=e.length+a.length;
      ev.push(...e.filter(r=>mtKeep(s,r,false)&&num(r.ev)&&r.ev>=Number(f.minEv)));
      arb.push(...a.filter(r=>mtKeep(s,r,true)));}
    ev.sort((a,b)=>f.sort==='fresh'?(mtAge(a)??1e12)-(mtAge(b)??1e12):f.sort==='chance'?b.p_cal-a.p_cal:b.per_100-a.per_100);
    arb.sort((a,b)=>f.sort==='fresh'?mtLegsAge(a)-mtLegsAge(b):b.return_per_100-a.return_per_100||mtLegsAge(a)-mtLegsAge(b));
    const at=new Date(d.as_of),clock=Number.isFinite(at.getTime())?at.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}):'';
    const one=sports.length===1&&['cs2','lol'].includes(sports[0])?sports[0]:null;
    let body='';
    if(one&&!all&&!(d.sports[one]?.sections_possible?.ev||d.sports[one]?.sections_possible?.arb))
      body=`<p class="h-mt-state h-pc-none">No sportsbook offers ${esc(SPORTS[one])} prices right now, so there is nothing to check here. ${esc(SPORTS[one])} lines are on the Board.</p>`;
    else if(!ev.length&&!arb.length)
      body=all?`<p class="h-mt-state h-pc-none">No prices match these filters. Clear a filter to see all ${all} rows.</p>`
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
    +'#u-hub .h-pc-chip i{font-style:normal;font-weight:400;color:var(--u-muted)}#u-hub .h-pc-age{color:var(--u-muted);font-size:12px}'
    +'#u-hub .h-pc-list.is-compact .h-pc-row{padding-top:5px;padding-bottom:5px;font-size:12px}#u-hub .h-pc-list.is-compact .h-pc-cell small{display:inline;margin-left:6px}'
    +'#u-hub .h-pc-stake{display:inline-flex;align-items:center;gap:6px;margin:0 0 8px;color:var(--u-muted)}#u-hub .h-pc-stake input{width:110px;min-height:40px}'
    +'#u-hub .h-mt-state{padding:12px;border:1px dashed var(--u-line);border-radius:8px;color:var(--u-muted)}#u-hub .h-mt-state.h-warning{color:var(--u-text);border-color:var(--u-line2)}'
    +'#u-hub .h-mt-method ul{margin:6px 0;padding-left:18px}#u-hub .h-mt-method li{margin:4px 0}'
    +'#u-hub .h-market-tools .h-table-wrap{overflow-x:auto;margin:6px 0 14px}#u-hub .h-owner-table{width:100%;border-collapse:collapse}#u-hub .h-owner-table small{display:block}#u-hub .h-owner-table .h-num{white-space:nowrap}'
    +'#u-hub .h-market-tools details{margin:6px 0 12px}#u-hub .h-market-tools summary{min-height:40px;display:flex;align-items:center;cursor:pointer;font-weight:600}'
    +'@media(max-width:639px){#u-hub .h-pc-head{display:none}#u-hub .h-pc-row{grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;padding:12px}'
    +'#u-hub .h-pc-row>.h-pc-cell{grid-column:1}#u-hub .h-pc-row>.h-pc-value{grid-column:2;grid-row:1;align-self:start;text-align:right}'
    +'#u-hub .h-pc-wide{display:none}#u-hub .h-pc-narrow{display:inline}#u-hub .h-pc-fair small{display:inline;margin-left:6px}'
    +'#u-hub .h-pc-arb .h-pc-leg::before{content:attr(data-label) " · ";color:var(--u-muted);font-size:12px}#u-hub .h-pc-age{font-size:11px}}';
  function paintMarketResults(){const box=$('hub-market-results');if(box)box.innerHTML=marketHTML();}
  function paintMarkets(){
    if(!R.me.features?.market_tools||H.section!=='tools')return;
    if(!$('hub-market-results')){
      // flag-on styles travel with the flag-on markup, so hub.css keeps its bytes
      if(!$('hub-mt-style'))document.head.insertAdjacentHTML('beforeend',`<style id="hub-mt-style">${MT_CSS}</style>`);
      ($('hub-tools-alerts')||$('hub-tools-body')).insertAdjacentHTML($('hub-tools-alerts')?'beforebegin':'beforeend',`<section class="h-market-tools" id="hub-market-tools"><div class="h-section-head"><div><span class="h-eyebrow">PRICE RESEARCH</span><h2 tabindex="-1">Price check</h2><p>Prices that pay more than they should, and lines where two books let you bet both sides and win either way.</p></div><button data-hub="refresh-markets">Refresh prices</button></div><p class="h-pc-lede">Each row is a single bet at one book. Green is what the price pays over its fair chance, per $100.</p><div id="hub-market-filters"></div><div id="hub-market-results" aria-live="polite"></div></section>`);
      const box=$('hub-market-filters');
      const read=e=>{const id=(e.target.id||'').replace('hub-mt-','');if(!(id in H.mt)||id==='dense')return;H.mt[id]=e.target.value;paintMarketResults();};
      box.addEventListener('input',e=>{if(e.target.id==='hub-mt-q')read(e);});
      box.addEventListener('change',e=>{if(e.target.id!=='hub-mt-q')read(e);if(e.target.id==='hub-mt-league')paintMarkets();});
      box.addEventListener('toggle',e=>{if(e.target.classList?.contains('h-mt-more'))H.mt.moreOpen=e.target.open;},true);
      $('hub-market-results').addEventListener('input',mtStakeInput);
      if(!H.marketDoc&&!H.marketLoading&&!H.marketError)actions['refresh-markets']();
    }
    mtFollowTab();
    const box=$('hub-market-filters'),had=box.contains(document.activeElement)?document.activeElement.id:null;
    box.innerHTML=H.marketDoc?marketFilters():'';
    if(had)$(had)?.focus({preventScroll:true});
    paintMarketResults();
  }
  function installV2() {
    if(!V2)return;
    $('u-hub').classList.add('h-v2');
    document.querySelector('#hub-filter-pop .h-quick').insertAdjacentHTML('afterbegin',[['freshOnly','Current only'],['soonOnly','Starts within 1h'],['movedOnly','Line moved']].map(([f,label])=>`<button id="hub-${f}" data-hub="quick" data-id="${f}" aria-pressed="false">${label}</button>`).join(''));
    document.querySelector('#hub-studio-section .h-studio-intro').insertAdjacentHTML('beforeend','<button data-hub="share-card">Download lineup image</button>');
  }

  const SECTIONS=['board','studio','record','alerts','picks','tools'];
  const TITLES={tools:'Tools',record:'Record',picks:'My picks',alerts:ALERT_WORD,studio:'Slips'};
  function section(name,navigate=true) {
    if(name==='games'){H.view='games';name='board';}
    if(!SECTIONS.includes(name))name='board';
    if(name!=='board'&&cardOpen())closeCard(false);
    togglePop(null);
    H.section=name;
    const games=name==='board'&&H.view==='games';
    if(navigate){const hash=(V2?'#/'+(games?'games':({studio:'slips',record:'results'}[name]||name)):'#scout/'+(games?'games':name))+(name==='board'&&!games?'?sport='+H.sport:'');if(location.hash!==hash)history.pushState(null,'',hash);H.routeHash=location.pathname+location.hash;}
    for(const n of SECTIONS)$('hub-'+n+'-section').hidden=n!==name;
    const here=name==='alerts'?'tools':name;
    document.querySelectorAll('#u-hub .h-sidebar nav [data-hub="section"], #u-hub .h-tabbar [data-hub="section"]').forEach(b=>b.setAttribute('aria-current',b.dataset.id===here?'page':'false'));
    $('u-hub').classList.toggle('h-studio-mode',name==='studio');
    $('u-hub').classList.toggle('h-board-mode',name==='board');
    $('hub-title').textContent=TITLES[name]||'Player props';
    if(name==='board')paintBoard();else if(name==='studio'){paintStudio();paintBatch();C.loadMine();}else if(name==='record')showRecord();else if(name==='picks'){paintPicks();C.loadMine();}else if(name==='tools'){paintTools();paintMarkets();paintAlerts();}else paintAlerts();
  }
  function route() {
    const query=new URLSearchParams(location.search),classic=query.get('view')==='classic'||query.has('slip')||query.has('p')||query.get('bv')==='1';
    const csBoard=/^#board(?:\/(?:all|prizepicks|underdog))?$/.test(location.hash);
    const modern=V2&&location.hash.match(/^#\/(board|games|slips|results|record|tools|picks|alerts)(?:\?(.*))?$/);
    const scout=location.hash.match(/^#scout\/(board|games|studio|picks|record|alerts|tools)(?:\?(.*))?$/),slips=/^#\/?slips(?:\?|$)/.test(location.hash);
    const mainBoard=location.pathname==='/lol'&&!location.hash || location.pathname==='/sports'&&(!location.hash||location.hash.startsWith('#/board')) || location.pathname==='/'&&(!location.hash||csBoard);
    const open=!classic&&(location.hash==='#all-sports'||!!scout||!!modern||mainBoard||slips);
    if(open&&H.routeHash!==location.pathname+location.hash){
      H.routeHash=location.pathname+location.hash;
      H.sport=modern?(new URLSearchParams(modern[2]||'').get('sport')||H.sport):scout?(new URLSearchParams(scout[2]||'').get('sport')||H.sport):location.hash==='#all-sports'?'all':location.pathname==='/lol'?'lol':location.pathname==='/sports'?(location.hash.includes('lg=nfl')?'nfl':'nba'):'cs2';
      if(!allowed().includes(H.sport))H.sport='all';
      const deep=R.me.features?.background_alerts?new URLSearchParams((modern?modern[2]:scout?scout[2]:'')||'').get('open'):null;
      if(deep&&deep.length<=300)H.pendingOpen=deep;
      const routeBook=location.hash.split('/')[1];
      if(csBoard&&['all','prizepicks','underdog'].includes(routeBook))H.book=routeBook;
      const tab=new URLSearchParams((modern?modern[2]:scout?scout[2]:location.hash.split('?')[1])||'').get('tab');
      if(['slip','build','tracked'].includes(tab))H.studioTab=tab;
      H.stat='all';H.game='all';H.page=0;
    }
    $('u-hub').hidden=!open; document.documentElement.classList.toggle('hub-open',open);
    // Original content remains mounted, but keyboard/screen readers cannot enter the hidden layer.
    for(const node of document.body.children) {
      if(node.id==='u-hub'||node.classList.contains('u-dialog')||node.classList.contains('u-toast')||node.id==='u-tray-btn'||node.tagName==='SCRIPT') continue;
      if(open&&!node.inert){node.dataset.hubInert='1';node.inert=true;}
      if(!open&&node.dataset.hubInert){node.inert=false;delete node.dataset.hubInert;}
    }
    if(!open)return;
    const target=modern?({slips:'studio',results:'record'}[modern[1]]||modern[1]):scout?scout[1]:slips?'studio':'board';
    if(target==='board')H.view='list';
    section(target,false);syncFields();paintBoard();refreshSlip();load();
  }
  /* the Filters and Views pop-overs and the phone's More sheet: one open at a time; a click outside or Escape closes it */
  const POPS={filter:['hub-filters-toggle','hub-filter-pop'],views:['hub-views-toggle','hub-views-pop'],more:['hub-more-toggle','hub-more']};
  function togglePop(which) {
    for(const [w,[btn,pop]] of Object.entries(POPS)){const open=w===which&&$(pop).hidden;$(pop).hidden=!open;$(btn).setAttribute('aria-expanded',String(open));}
  }
  const openPop = () => Object.entries(POPS).find(([,[,pop]])=>!$(pop).hidden)?.[0]||null;
  const rec = id => H.records.find(r=>key(r)===id)||H.extraRecs.find(r=>key(r)===id);
  const actions={
    'share-card'(){C.shareSlip();},
    quick(id){if(!V2||!['freshOnly','soonOnly','movedOnly'].includes(id))return;H[id]=!H[id];H.page=0;paintBoard();},
    // By game keeps the board's filters: the games listed are the ones those filters show
    'game-board'(id){H.game=id;H.view='list';H.page=0;section('board');$('hub-center').scrollTop=0;},
    generate, 'use-generated':useGenerated, 'open-generated':i=>C.openGenerated(H.batch?.slips?.[Number(i)]), 'open-line':()=>C.openLine(rec(H.detail),H.histSide), 'save-batch':saveBatch,
    'player-market'(id){if(rec(id))openCard(id,H.cardTab);},
    filters(){togglePop('filter');}, 'views-menu'(){togglePop('views');}, more(){togglePop('more');},
    book(id){if(!['all','prizepicks','underdog'].includes(id)&&!isExtra(id))return;if(!bookHasLines(id))return;H.book=id;H.page=0;paintBoard();if(isExtra(id))loadExtra();},
    'mt-jump'(){$('hub-market-tools')?.scrollIntoView({block:'start'});$('hub-market-tools')?.querySelector('h2')?.focus?.({preventScroll:true});},
    'clear-filters'(){Object.assign(H,{book:'all',q:'',stat:'all',side:'all',minGrade:'all',onlyDiff:false,watched:false,freshOnly:false,soonOnly:false,movedOnly:false,game:'all',page:0});paintBoard();},
    view(id){H.view=id==='games'?'games':'list';H.page=0;section('board');},
    section(id){if(id==='board')H.view='list';if(id==='studio')H.studioTab='slip';section(id);},
    'studio-open'(id){H.studioTab=['slip','build','tracked'].includes(id)?id:'slip';section('studio');},
    'studio-tab'(id){if(!['slip','build','tracked'].includes(id))return;H.studioTab=id;paintStudio();if(id==='build')paintBatch();if(id==='tracked')C.loadMine();sectionFocus('studio-tab',id);},
    sport(id){H.sport=id;H.stat='all';H.game='all';H.page=0;H.view==='games'?section('games'):section('board');load(false);},
    game(id){H.game=H.game===id?'all':id;H.page=0;paintBoard();document.querySelector(`#hub-rows [data-hub="game"][data-id="${CSS.escape(id)}"]`)?.focus({preventScroll:true});},
    page(id){H.page+=Number(id);paintBoard();$('hub-table').scrollIntoView({block:'start'});},
    'player-props'(id){const open=rec(H.expanded);if(openPlayers.has(id)){openPlayers.delete(id);if(cardOpen()&&open&&boardPlayerKey(open)===id)closeCard(false);}else openPlayers.add(id);paintBoard();},
    expand(id){if(cardOpen()&&H.expanded===id)closeCard();else openCard(id,'overview');},
    'card-close'(){closeCard();},
    'card-tab'(id){if(!CARD_TABS.some(t=>t[0]===id))return;H.cardTab=id;paintDetail();$('hub-card-body').scrollTop=0;$('hub-tab-'+id)?.focus({preventScroll:true});},
    'genie-ask'(q){genieAsk(q);},
    'detail-line':selectDetail,'detail-retry'(){selectDetail(H.detail);},'history-side'(id){H.histSide=id==='under'?'under':'over';paintDetail();},'history-window'(id){H.histN=Number(id);paintDetail();},
    'detail-add'(){const r=rec(H.detail);if(!r)return;if(C.onSlip(r))return revealSlip(r);if(H.detailState!=='ready'||unavailableReason(r)||C.started(r)||!r.offer[H.histSide].offered)return;C.addLeg(r,H.histSide);refreshSlip();},
    // alert notices open the same card as the board
    research(id){const r=rec(id);if(!r)return C.say('This offer is no longer on the board. Refresh to see current lines.');if(H.section!=='board')section('board');openCard(id);},
    books(id){if(rec(id))openCard(id,'lines');},
    add(id){const r=rec(id);if(!r)return;if(r.extra)return C.say(`${r.offer.platform_label} lines are view only. Slips are PrizePicks or Underdog entries.`);if(unavailableReason(r)||C.started(r))return C.say('This offer needs a fresh source check. Refresh the board.');if(C.onSlip(r))return revealSlip(r);if(!r.scout.side||!r.offer[r.scout.side]?.offered)return openCard(id,'overview');C.addLeg(r,r.scout.side);refreshSlip();},
    watch(id){const r=rec(id);if(!r)return;const k=playerKey(r);H.watch=H.watch.includes(k)?H.watch.filter(x=>x!==k):[...H.watch,k].slice(-100);savePreferences();paintBoard();if(cardOpen())paintDetail();},
    diff(){H.onlyDiff=!H.onlyDiff;H.page=0;paintBoard();}, watched(){H.watched=!H.watched;H.page=0;paintBoard();},
    refresh(){H.detailSeq++;if(cardOpen())closeCard(false);H.bookLinesAt=0;load(true);}, tray(){H.studioTab='slip';section('studio');},receipts(){section('picks');},close(){const u=new URL(location.href);u.searchParams.set('view','classic');u.hash=PAGE_BACK;location.assign(u.href);},
    'save-view'(){togglePop(null);openSave('view');}, 'save-alert'(){togglePop(null);openSave('alert');}, 'save-retry':savePreferences,
    'pick-clear'(){H.pickSport='all';H.pickStatus='all';$('hub-pick-sport').value='all';$('hub-pick-status').value='all';paintPicks();},
    'grade-review'(id,el){showGradeReview(id,el?.dataset.before);},
    skip(){$('hub-title').focus();},
    'market-density'(){H.mt.dense=!H.mt.dense;try{localStorage.setItem('scout-market-density',H.mt.dense?'compact':'comfortable');}catch(e){}$('hub-mt-dense')?.setAttribute('aria-pressed',String(H.mt.dense));paintMarketResults();},
    async 'refresh-markets'(){
      H.marketLoading=true;H.marketError=null;paintMarkets();
      try{H.marketDoc=await api('market-tools');}catch(e){H.marketError='Prices could not be loaded.';}
      finally{H.marketLoading=false;paintMarkets();}
    },
    async 'toggle-delivery'(){
      H.deliveryBusy=true;H.deliveryError=null;paintAlerts();
      try{H.delivery=await api('alert-delivery',{enabled:!H.delivery?.enabled});}
      catch(e){H.deliveryError=e.message||'Could not update delivery. Try again.';}
      finally{H.deliveryBusy=false;paintAlerts();}
    },
    'delete-alert'(i){const a=H.alerts.splice(Number(i),1)[0];if(a)H.seen.delete(a.id);savePreferences();paintAlerts();},
    'record-retry':showRecord,
  };
  const sectionFocus=(action,id)=>document.querySelector(`#u-hub [data-hub="${action}"][data-id="${CSS.escape(id)}"]`)?.focus({preventScroll:true});
  const PAGE_BACK=location.pathname==='/sports'?'#/board':location.pathname==='/lol'?'':'#board';
  function openSave(kind) {H.saveKind=kind;$('hub-save-title').textContent=kind==='alert'?(BG?'Alert on these filters':'Live highlight these filters'):'Save this research view';$('hub-save-note').textContent=kind!=='alert'?'Saved to your account.':BG?'Saved to your account. Whop delivers matches when Scout is closed once delivery is on under Alerts; this screen highlights them live.':'Saved to your account. New matches are highlighted while this research screen is open. Nothing is sent when it is closed.';$('hub-save-name').value='';$('hub-save-dialog').showModal();$('hub-save-name').focus();}
  const NAV=[['board','Board'],['studio','Slips'],['picks','My picks'],['record','Record'],['tools','Tools']];
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
                <label>Minimum grade<select id="hub-minGrade"><option value="all">Any grade</option>${gradeOrder.slice().reverse().map(g=>`<option value="${g}">${g} or higher</option>`).join('')}</select></label><label>Sort<select id="hub-sort"><option value="start">Starting soon</option><option value="player">Player name</option><option value="chance">Stated chance</option>${COLS?'<option value="edge">Edge vs break-even</option>':''}</select></label></div><small id="hub-sort-note" class="h-sort-note" role="status"></small>
              <div class="h-quick"><button id="hub-diff" data-hub="diff" aria-pressed="false">Lines differ</button><button id="hub-watch" data-hub="watched" aria-pressed="false">★ Watchlist</button></div>
              <div class="h-pop-foot"><button data-hub="clear-filters">Clear filters</button><button data-hub="filters">Done</button></div></div></div>
          <div class="h-pop-wrap"><button id="hub-views-toggle" data-hub="views-menu" aria-expanded="false" aria-controls="hub-views-pop">Views <span aria-hidden="true">▾</span></button>
            <div id="hub-views-pop" class="h-pop h-pop-end" role="group" aria-label="Saved views and alerts" hidden><label>Saved views<select id="hub-views"><option value="">Choose a saved view</option></select></label><button data-hub="save-view">Save this view</button><button data-hub="save-alert">${BG?'Alert me on these filters':'Live highlight these filters'}</button><button data-hub="section" data-id="tools">Manage alerts →</button></div></div>
          <button class="h-refresh" data-hub="refresh" aria-label="Refresh all boards" title="Refresh">↻</button>
        </div>
        <div id="hub-sources" class="h-sources" role="status"></div><div id="hub-health" class="h-health" role="status"></div>
        <div class="h-table-title"><h2 class="u-sr">Available lines</h2><span id="hub-count"></span><button id="hub-clear" class="h-link" data-hub="clear-filters" hidden>Clear filters</button><div class="h-segment h-viewmode" role="group" aria-label="Board view"><button data-hub="view" data-id="list" aria-pressed="true">List</button><button data-hub="view" data-id="games" aria-pressed="false">By game</button></div></div>
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
      <section id="hub-tools-section" hidden><div class="h-section-head"><div><p>Every research tool outside the board, in one place.</p></div></div><div id="hub-tools-body"></div></section>
      <section id="hub-alerts-section" hidden><div id="hub-alerts-body"></div></section>
    </div><aside class="h-slip" aria-label="Slip"><header><h2>Slip <span id="hub-slip-count">0</span></h2><button data-hub="receipts">My picks →</button></header><p id="hub-save-status" class="h-sync" aria-live="polite"></p><div id="hub-slip-content" tabindex="-1"></div><footer>One entry per platform. Rechecked against current offers before tracking.</footer></aside>
    <nav class="h-tabbar" aria-label="Sections"><button data-hub="section" data-id="board">Board</button><button id="hub-tab-slip" data-hub="section" data-id="studio">Slip <span id="hub-tab-slip-n" class="h-count-badge">0</span></button><button data-hub="section" data-id="picks">My picks</button><button data-hub="section" data-id="record">Record</button><button id="hub-more-toggle" data-hub="more" aria-expanded="false" aria-controls="hub-more">More <span id="hub-alert-count-m" class="h-count-badge" aria-label="New filter matches"></span></button></nav>
    <div id="hub-more" class="h-more-sheet" role="group" aria-label="More" hidden><button data-hub="view" data-id="games">By game</button><button data-hub="section" data-id="tools">Tools</button><button data-hub="section" data-id="alerts">${ALERT_WORD}</button><button data-hub="studio-open" data-id="build">Auto-build slips</button><button data-hub="studio-open" data-id="tracked">Tracked slips</button><a href="/?view=classic#moneylines">Moneylines ↗</a><a href="/?view=classic#one">Single prop grader ↗</a><a href="/?view=classic#matches">Match research ↗</a><button data-hub="close">Classic board ↗</button></div>
    <aside id="hub-card" class="h-card-panel" role="dialog" aria-modal="false" aria-labelledby="hub-card-title" hidden><div id="hub-detail" class="h-card"></div></aside></section>
    <dialog id="hub-save-dialog" class="h-save u-dialog" aria-labelledby="hub-save-title"><form method="dialog"><h2 id="hub-save-title">Save view</h2><label>Name<input id="hub-save-name" maxlength="48" required autocomplete="off"></label><p id="hub-save-note">Saved to your account.</p><div><button value="cancel" formnovalidate>Cancel</button><button id="hub-save-confirm" value="save">Save</button></div></form></dialog>`);
  const entry=document.createElement('a');entry.href='#all-sports';entry.className='h-entry';entry.textContent='All sports research';
  installV2();
  if(EXTRA){
    if(!$('hub-extra-style'))document.head.insertAdjacentHTML('beforeend','<style id="hub-extra-style">#u-hub .h-add.h-add-info{font-size:12px;font-weight:500}'
      +'#u-hub .h-extra-detail{padding:12px 16px}#u-hub .h-extra-detail h3{margin:0 0 10px;font-size:15px}#u-hub .h-extra-kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 10px}'
      +'#u-hub .h-extra-kv dt{font-size:11px;color:var(--u-muted)}#u-hub .h-extra-kv dd{margin:2px 0 0}#u-hub .h-extra-state{margin:0 0 10px}#u-hub .h-extra-state summary{min-height:40px;display:flex;align-items:center;cursor:pointer}</style>');
    $('hub-platform').insertAdjacentHTML('beforeend',EXTRA.map(x=>`<button data-hub="book" data-id="${esc(x.id)}" aria-pressed="false" hidden aria-label="${esc(x.label)} (view only)" title="${esc(x.label)}">${esc(x.short||x.label)}</button>`).join(''));
  }
  installCols();
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
  const entryHost=document.querySelector('.workspace-tools')||document.querySelector('.sw-tabs')||document.querySelector('.ot-lol .bar')||document.querySelector('body header');
  if(entryHost) entryHost.appendChild(entry); else document.body.prepend(entry);
  $('hub-save-dialog').addEventListener('close',()=>{
    if($('hub-save-dialog').returnValue!=='save')return;
    const name=$('hub-save-name').value.trim();if(!name)return;
    const v={id:Date.now().toString(36),name,filters:filters()};
    if(H.saveKind==='alert'){H.alerts=[...H.alerts,v].slice(-12);checkAlerts();}else H.views=[...H.views,v].slice(-12);
    savePreferences();paintBoard();C.say(H.saveKind==='alert'?(BG?'Alert saved. Turn on Whop delivery under Alerts to get it when Scout is closed.':'Live highlight saved for this research screen.'):'Research view saved.');
  });
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
  for(const f of ['q','side','sort','stat']) $('hub-'+f).addEventListener(f==='q'?'input':'change',()=>{H[f]=$('hub-'+f).value;H.page=0;paintBoard();});
  $('hub-minGrade').addEventListener('change',()=>{H.minGrade=$('hub-minGrade').value;H.page=0;paintBoard();});
  $('hub-views').addEventListener('change',()=>{const i=$('hub-views').value;if(i==='')return;const v=H.views[Number(i)];if(v){H.minGrade='all';for(const k of filterKeys) if(k in v.filters)H[k]=v.filters[k];H.page=0;paintBoard();}});
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
  window.addEventListener('popstate',route);
  window.addEventListener('keydown',e=>{
    if(!isOpen())return;
    if(e.key==='Escape'&&!document.querySelector('dialog[open]')){
      const pop=openPop();
      if(pop){togglePop(null);$(POPS[pop][0]).focus({preventScroll:true});e.preventDefault();return;}
      if(cardOpen()){closeCard();e.preventDefault();return;}
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
  document.addEventListener('visibilitychange',()=>{if(isOpen()&&!document.hidden&&H.section==='tools'&&H.marketDoc&&!H.marketLoading&&mtOld())actions['refresh-markets']();});
  setInterval(()=>{const box=$('hub-market-results');if(!document.hidden&&H.section==='tools'&&H.marketDoc&&box&&!box.contains(document.activeElement))paintMarketResults();},60000);
  route();
  loadPreferences().then(()=>{if(H.section==='board')paintBoard();else if(H.section==='alerts'||H.section==='tools')paintAlerts();});
  return {refreshSlip};
}
