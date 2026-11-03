/* One real research surface over the existing authenticated sport adapters.
 * No grades or prices are calculated here. The original pages and their tools stay intact.
 */
export function mount(C) {
  const style=document.createElement('link');style.rel='stylesheet';style.href='/upgrade/hub.css';document.head.appendChild(style);
  const {api, R, T, SPORTS, esc, pct, when} = C;
  const V2 = R.me.features?.workspace_v2 === true;
  const $ = id => document.getElementById(id);
  const H = {minGrade:'all',sport:'all', book:'all', q:'', side:'all', sort:'start', stat:'all', game:'all', onlyDiff:false, watched:false, freshOnly:false, soonOnly:false, movedOnly:false,
    expanded:null, detail:null, detailDoc:null, detailState:'idle', detailSeq:0, histN:10, histSide:'over', pickSport:'all', pickStatus:'all',
    page:0, section:'board', loading:false, seq:0, records:[], views:[], watch:[], alerts:[], seen:new Map(), notices:[], saveBusy:false, generating:false, batch:null, batchSaved:false};
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
    return H.records.filter(r=>!C.started(r)&&matches(r)).sort((a,b)=> {
      if(H.sort==='chance') return (b.scout.p_side??-1)-(a.scout.p_side??-1);
      if(H.sort==='player') return a.player.name.localeCompare(b.player.name);
      return Date.parse(a.event.start_utc)-Date.parse(b.event.start_utc)||a.player.name.localeCompare(b.player.name);
    });
  }
  function syncFields() {
    for(const n of ['book','side','sort','q','minGrade']) $('hub-'+n).value=H[n];
    $('hub-diff').setAttribute('aria-pressed',String(H.onlyDiff));
    $('hub-watch').setAttribute('aria-pressed',String(H.watched));
    if(V2)for(const f of ['freshOnly','soonOnly','movedOnly'])$('hub-'+f).setAttribute('aria-pressed',String(H[f]));
    $('hub-sports').innerHTML=['all',...allowed()].map(s=>`<button type="button" data-hub="sport" data-id="${s}" aria-pressed="${H.sport===s}">${s==='all'?'All sports':SPORTS[s]}<span>${H.records.filter(r=>!C.started(r)&&(s==='all'||r.sport===s)).length}</span></button>`).join('');
    const sportRows=H.records.filter(r=>!C.started(r)&&(H.sport==='all'||r.sport===H.sport));
    const markets=[...new Map(sportRows.map(r=>[statKey(r),r.market.stat_label+(H.sport==='all'?' · '+SPORTS[r.sport]:'')])).entries()];
    $('hub-stat').innerHTML='<option value="all">All markets</option>'+markets.sort((a,b)=>a[1].localeCompare(b[1])).map(([id,name])=>`<option value="${esc(id)}">${esc(name)}</option>`).join('');
    $('hub-stat').value=H.stat;
    const games=[...new Map(sportRows.map(r=>[gameKey(r),r])).values()].sort((a,b)=>Date.parse(a.event.start_utc)-Date.parse(b.event.start_utc));
    $('hub-games').innerHTML=`<button data-hub="game" data-id="all" aria-pressed="${H.game==='all'}"><b>All matchups</b><small>${games.length} upcoming</small></button>`+games.slice(0,30).map(r=>`<button data-hub="game" data-id="${esc(gameKey(r))}" aria-pressed="${H.game===gameKey(r)}"><b>${esc(r.event.label)}</b><small>${SPORTS[r.sport]} · ${esc(when(r.event.start_utc))}</small></button>`).join('');
    if(H.section==='board')$('hub-title').textContent=H.sport==='all'?'Player props':SPORTS[H.sport]+' player props';
    document.querySelectorAll('.h-sidebar [data-hub="sport"]').forEach(b=>b.setAttribute('aria-current',b.dataset.id===H.sport&&H.section==='board'?'page':'false'));
  }
  function cell(r,n) {
    const t=C.tallies(r)['l'+n], side=r.scout.side;
    if(!t||!side||!t.n) return `<span class="h-hit empty" title="No comparable history">—</span>`;
    const hit=side==='over'?t.over:t.under, rate=hit/t.n;
    return `<span class="h-hit ${V2?'h-tone-'+Math.min(6,Math.floor(rate*7)):rate>=.6?'positive':rate<.4?'negative':''}" style="--hit:${Math.round(rate*100)}%" title="${hit} hits, ${t.push||0} pushes in ${t.n}; historical results at this line">${hit}<small>/${t.n}</small></span>`;
  }
  function row(r) {
    const k=esc(key(r)), s=r.scout, side=s.side, on=C.onSlip(r), watched=H.watch.includes(playerKey(r));
    const choose=!side||!r.offer[side]?.offered;
    const alternatives=C.siblings(r).filter(x=>x.offer.platform!==r.offer.platform);
    const state=unavailableReason(r), unavailable=!!state;
    const label=s.hold?'Research':s.grade||'Lean';
    return `<tr data-record="${k}" class="${H.expanded===key(r)?'is-expanded':''}"><td><button class="h-star" data-hub="watch" data-id="${k}" aria-label="Watch ${esc(r.player.name)}" aria-pressed="${watched}">${watched?'★':'☆'}</button></td>
      <td><button class="h-player" data-hub="expand" data-id="${k}" aria-expanded="${H.expanded===key(r)}"><span class="h-avatar">${esc(r.player.name.slice(0,2).toUpperCase())}</span><span><b>${esc(r.player.name)}</b><small>${esc(r.player.team)} <i>${SPORTS[r.sport]}</i></small></span><span class="h-chevron" aria-hidden="true">${H.expanded===key(r)?'−':'+'}</span></button><div class="h-match">${esc(r.event.label)} · ${esc(when(r.event.start_utc))}</div></td>
      <td><b>${esc(r.market.stat_label)}</b><small>${esc(r.market.period_label)}</small></td><td class="h-number"><b>${esc(r.offer.line)}</b><small>${esc(r.offer.platform_label)}</small></td>
      <td class="h-number">${num(s.projection)?esc(s.projection.toFixed(1)):'—'}</td><td><b>${side?side==='over'?'Over':'Under':'No side'}</b><small>${pct(s.p_side)}</small></td>
      <td>${cell(r,5)}</td><td>${cell(r,10)}</td><td>${cell(r,20)}</td>
      <td><button class="h-book" data-hub="expand" data-id="${k}">${alternatives.length?`${alternatives.length} other ${alternatives.length===1?'line':'lines'}`:'Research'}${different(r)?'<i>Lines differ</i>':''}</button></td>
      <td><span class="h-grade ${s.hold?'held':''}">${esc(label)}</span><small title="Offer revision first observed ${esc(r.offer.seen_at||'at an unavailable time')}">Seen ${esc(age(r.offer.seen_at))}</small>${unavailable?`<small class="h-warning">${esc(state)}</small>`:''}</td>
      <td><button class="h-add ${on||choose?'selected':''}" data-hub="add" data-id="${k}" aria-label="${on?'Review':choose?'Choose a side for':'Add'} ${esc(r.player.name)} to slip" ${unavailable?'disabled':''}>${on?'Added':choose?'Side':'+'}</button></td></tr>${H.expanded===key(r)?'<tr class="h-detail-row"><td colspan="12"><div id="hub-detail" aria-live="polite"></div></td></tr>':''}`;
  }
  function paintBoard() {
    if(!isOpen()||H.section!=='board') return;
    const restoreFocus=focusAfter($('hub-board-section'));
    syncFields();
    const list=rows(), size=40, pages=Math.max(1,Math.ceil(list.length/size));
    H.page=Math.min(H.page,pages-1);
    $('hub-count').textContent=`${list.length.toLocaleString()} upcoming lines`;
    const issues=allowed().filter(s=>R.boards[s].error);
    const stale=allowed().flatMap(s=>Object.entries(R.boards[s].feeds||{}).filter(([,f])=>f.state!=='ok').map(([p,f])=>SPORTS[s]+' '+(C.PLATFORMS[p]?.label||p)+': '+(f.state||'unavailable')));
    const held= [...new Set(list.filter(r=>r.scout.hold).map(r=>SPORTS[r.sport]))];
    $('hub-health').innerHTML=(V2&&held.length?`<p>${esc(held.join(' / '))}: official grades are held by the accuracy check. History and lines remain available for research.</p>`:'')+(issues.length?`<p class="h-warning">${issues.map(s=>SPORTS[s]).join(', ')} could not refresh. Saved rows are labelled and cannot be added here. <button data-hub="refresh">Retry</button></p>`:'')
      +(stale.length?`<details class="h-warning"><summary>${stale.length} ${stale.length===1?'source needs':'sources need'} a fresh update</summary><p>${esc(stale.join(' · '))}. Affected offers cannot be added until the source recovers.</p></details>`:'')
      +(list.some(r=>r.scout.validation?.kind==='research')?`<details><summary>About Sports grades</summary><p>Published letters rank the model’s current picks. NBA and NFL calibration has not passed validation; see Performance record for stated chances and actual results.</p></details>`:'');
    $('hub-rows').innerHTML=list.slice(H.page*size,(H.page+1)*size).map(row).join('');
    paintDetail();
    $('hub-empty').hidden=list.length>0;
    $('hub-empty').textContent=H.loading?'Loading the available boards…':H.records.length?'No lines match these filters. Try another sport, book or player.':'No upcoming lines are available. Refresh to check again.';
    $('hub-pagination').innerHTML=`<span>${list.length?H.page*size+1:0}–${Math.min((H.page+1)*size,list.length)} of ${list.length}</span><button data-hub="page" data-id="-1" ${H.page===0?'disabled':''}>Previous</button><button data-hub="page" data-id="1" ${H.page===pages-1?'disabled':''}>Next</button>`;
    $('hub-views').innerHTML='<option value="">Saved views</option>'+H.views.map((v,i)=>`<option value="${i}">${esc(v.name)}</option>`).join('');
    restoreFocus();
  }
  function refreshSlip() {
    if(!isOpen()) return;
    const box=$('hub-slip-content'), top=box.scrollTop;
    // Repainting retains focus by action identity, including while an asynchronous recheck finishes.
    const a=document.activeElement, u=box.contains(a)?a.dataset.u:null, id=u?a.dataset.id:null;
    box.innerHTML=C.slipTab().replace('data-u="tab" data-id="picks"','data-hub="receipts"'); box.scrollTop=top;
    if(u) (box.querySelector(`[data-u="${CSS.escape(u)}"]${id!=null?`[data-id="${CSS.escape(id)}"]`:''}`)||box).focus({preventScroll:true});
    $('hub-slip-count').textContent=String(T.legs.length);
    $('hub-save-status').textContent=T.saveFail?'Waiting to sync':T.pending||T.saving?'Saving…':'Saved to your account';
    if(!T.legs.length&&!T.tracked) box.innerHTML='<div class="h-empty-slip"><span class="h-slip-glyph" aria-hidden="true">+</span><h3>Start with a player prop</h3><p>Add a side from any sport. Your lineup stays here as you research.</p><div><span>1 &nbsp; Choose your legs</span><span>2 &nbsp; Recheck current lines</span><span>3 &nbsp; Track your picks</span></div><small>Different platforms become separate entries.</small></div>';
    $('u-hub').querySelector('.h-mobile-slip').textContent=`Slip builder · ${T.legs.length} ${T.legs.length===1?'leg':'legs'}`;
    paintBoard();
    if(H.section==='picks')paintPicks();
    if(H.section==='studio')paintStudio();
  }
  async function load(force=false) {
    if(H.loading) return;
    H.loading=true; paintBoard();
    await Promise.allSettled(allowed().map(s=>C.loadBoard(s,force)));
    H.records=allowed().flatMap(s=>[...R.boards[s].recs.values()]);
    const current=rec(H.detail),shown=H.detailDoc?.record;
    if(shown&&(!current||current.offer.revision!==shown.offer.revision||current.scout.read_rev!==shown.scout.read_rev)){H.detailDoc=null;H.detailState='error';H.detailSeq++;}
    H.loading=false; paintBoard(); checkAlerts(); if(V2&&H.section==='games')paintGames();
    if(allowed().includes('cs2')) { await C.loadHits(); paintBoard(); }
  }
  async function savePreferences() {
    if(H.saveBusy) {H.saveAgain=true;return;}
    H.saveBusy=true;
    try {
      for(let i=0;i<3;i++) {
        const state=await api('state');
        try {
          await api('state',{rev:state.rev,prefs:{...state.prefs,researchHub:{views:H.views,watch:H.watch,alerts:H.alerts}}});
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
      }
      const clean = values => !Array.isArray(values)?[]:values.filter(v=>v&&typeof v.name==='string'&&v.filters&&typeof v.filters==='object').slice(0,12).map((v,i)=>{
        const f=v.filters;
        return {id:typeof v.id==='string'?v.id.slice(0,100):'saved-'+i,name:v.name.slice(0,80),filters:{
          minGrade:gradeOrder.includes(f.minGrade)?f.minGrade:'all',freshOnly:f.freshOnly===true,soonOnly:f.soonOnly===true,movedOnly:f.movedOnly===true,
          sport:['all',...allowed()].includes(f.sport)?f.sport:'all',book:['all','prizepicks','underdog'].includes(f.book)?f.book:'all',
          q:typeof f.q==='string'?f.q.slice(0,100):'',side:['all','over','under'].includes(f.side)?f.side:'all',
          sort:['start','player','chance'].includes(f.sort)?f.sort:'start',stat:typeof f.stat==='string'?f.stat.slice(0,150):'all',game:typeof f.game==='string'?f.game.slice(0,150):'all',onlyDiff:f.onlyDiff===true,watched:f.watched===true}};
      });
      H.views=clean(p.views);
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
  function paintAlerts() {
    if(!isOpen()) return;
    $('hub-alert-count').textContent=H.notices.length?String(H.notices.length):'';
    if(H.section!=='alerts') return;
    $('hub-alerts-body').innerHTML=`<h2>Follow the lines that matter.</h2><p>Saved filters flag new matching offers and revisions. Always recheck the line before building a slip.</p>
      ${R.me.features?.background_alerts?`<div class="h-alert"><div><b>Whop notifications</b><p>${H.delivery?.enabled?'Enabled for this account, including when Scout is closed.':'Opt in to personal Whop notifications while Scout is closed. Uses your Whop account for delivery; consent expires after 30 days.'}</p>${H.delivery?.status?.startsWith('delivery_')?'<p class="h-warning">The last delivery could not be confirmed. In-page alerts still work.</p>':''}</div><button data-hub="toggle-delivery" ${H.deliveryBusy?'disabled':''}>${H.delivery?.enabled?'Turn off':'Enable Whop alerts'}</button></div>`:'<p>In-page alerts are active while this screen is open. No email or push notifications are enabled.</p>'}
      ${H.deliveryError?`<p class="h-warning" role="status">${esc(H.deliveryError)}</p>`:''}
      ${H.saveError?'<p class="h-warning">Preferences have not synced. <button data-hub="save-retry">Retry save</button></p>':''}
      <h3>Your alerts</h3>${H.alerts.length?H.alerts.map((a,i)=>`<div class="h-alert"><b>${esc(a.name)}</b><span>${esc(a.filters.sport==='all'?'All sports':SPORTS[a.filters.sport]||a.filters.sport)} · ${esc(a.filters.book)}</span><button data-hub="delete-alert" data-id="${i}">Remove</button></div>`).join(''):'<p>No alerts yet. Set your board filters and choose “Alert me”.</p>'}
      <h3>This visit</h3>${H.notices.length?H.notices.map(n=>`<button class="h-notice" data-hub="research" data-id="${esc(n.id)}"><b>${esc(n.name)}</b><span>${esc(n.text)}</span><small>${esc(n.view)} · ${new Date(n.at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}</small></button>`).join(''):'<p>New matches will appear here after the next refresh. The first load establishes your baseline.</p>'}`;
  }
  async function expand(id) {
    if(H.expanded===id){H.expanded=null;H.detail=null;H.detailSeq++;paintBoard();return;}
    const r=rec(id);if(!r)return;
    H.expanded=id;H.histN=10;H.histSide=r.scout.side||(r.offer.over.offered?'over':'under');
    await selectDetail(id);
  }
  async function selectDetail(id) {
    const r=rec(id), parent=rec(H.expanded);
    if(!r||!parent||!C.siblings(parent).some(x=>key(x)===id))return;
    H.detail=id;H.detailDoc=null;H.detailState='loading';
    const seq=++H.detailSeq;paintBoard();
    try {
      const doc=await C.researchDoc(r);
      if(seq!==H.detailSeq)return;
      H.detailDoc=doc;H.detailState='ready';
    } catch(e){if(seq!==H.detailSeq)return;H.detailState='error';}
    paintDetail();
  }
  function paintDetail() {
    const box=$('hub-detail'),r=rec(H.detail);if(!box||!r)return;
    const restoreFocus=focusAfter(box);
    const side=H.histSide, line=r.offer.line, doc=H.detailDoc;
    const series=(doc?.history?.series||[]).filter(g=>num(g.value)).slice(0,H.histN);
    const over=series.filter(g=>g.value>line).length,under=series.filter(g=>g.value<line).length,push=series.filter(g=>g.value===line).length;
    const tally=series.length?{n:series.length,over,under,push}:C.tallies(r)['l'+H.histN];
    const sortedValues=series.map(g=>g.value).sort((a,b)=>a-b), middle=Math.floor(sortedValues.length/2);
    const median=sortedValues.length?(sortedValues.length%2?sortedValues[middle]:(sortedValues[middle-1]+sortedValues[middle])/2):null;
    const count=tally?(side==='over'?tally.over:tally.under):null;
    const eligible=H.detailState==='ready'&&!unavailableReason(r)&&!C.started(r)&&r.offer[side].offered;
    const quotes=C.siblings(r), quoteText=(q,sd)=>{
      const o=q.offer[sd];
      if(!o.offered)return 'Unavailable';
      const kind=C.PLATFORMS[q.offer.platform]?.kind;
      if(kind==='decimal')return num(o.payout)?o.payout.toFixed(2):'Odds unavailable';
      if(num(o.price))return o.price.toFixed(2);
      if(num(o.payout)&&o.payout!==1)return o.payout.toFixed(2)+'× modifier';
      return q.market.line_type==='standard'?'Standard':q.market.line_type;
    };
    const facts=(doc?.context?.rows||[]).filter(f=>f.label && f.value!=null);
    const playerMarkets=[...new Map(H.records.filter(x=>x.sport===r.sport&&x.player.key===r.player.key&&x.event.id===r.event.id&&x.offer.platform===r.offer.platform).map(x=>[x.market.stat+'|'+x.market.period,x])).values()];
    box.innerHTML=`<div class="h-detail-head"><div><h3>${esc(r.player.name)} <span>${esc(r.market.stat_label)}</span></h3><p>${esc(r.market.period_label)} · ${esc(r.event.label)}</p></div><button data-hub="research" data-id="${esc(key(r))}">Full player research ↗</button></div>
      ${playerMarkets.length>1?`<nav class="h-player-markets" aria-label="Player markets">${playerMarkets.map(x=>`<button data-hub="player-market" data-id="${esc(key(x))}" aria-pressed="${key(x)===key(r)}">${esc(x.market.stat_label)} <b>${esc(x.offer.line)}</b><small>${esc(x.market.period_label)}</small></button>`).join('')}</nav>`:''}
      <div class="h-detail-grid"><section class="h-history-panel"><div class="h-history-controls"><div class="h-segment" role="group" aria-label="History side">${['over','under'].map(sd=>`<button data-hub="history-side" data-id="${sd}" aria-pressed="${side===sd}">${sd==='over'?'Over':'Under'} ${esc(line)}</button>`).join('')}</div><div class="h-segment" role="group" aria-label="History window">${[5,10,20].map(n=>`<button data-hub="history-window" data-id="${n}" aria-pressed="${H.histN===n}">L${n}</button>`).join('')}</div></div>
      <div class="h-history-summary"><div><b>${tally?`${count}<em>/${tally.n}</em>`:'—'}</b><span>Past results ${side} ${esc(line)}${tally?.push?` · ${tally.push} pushes`:''}</span></div><div><b>${series.length?(series.reduce((a,g)=>a+g.value,0)/series.length).toFixed(1):'—'}</b><span>Average · ${series.length||'no'} games loaded</span></div><div><b>${num(r.scout.projection)?r.scout.projection.toFixed(1):'—'}</b><span>Saved projection</span></div>${V2?`<div><b>${median==null?'—':median.toFixed(1)}</b><span>Median · same ${series.length} games</span></div>`:''}</div>
      ${H.detailState==='loading'?'<p class="h-detail-loading">Loading this player’s saved results…</p>':H.detailState==='error'?`<p class="h-warning">This offer changed or research is unavailable. <button data-hub="detail-retry">Retry research</button> <button data-hub="refresh">Refresh board</button></p>`:series.length?`<div class="h-history-chart" role="img" aria-label="${series.length} past results compared with ${side} ${esc(line)}">${C.chartSvg(series,line,side,560)}</div><details class="h-history-table"><summary>View dates and results</summary><table><thead><tr><th>Date</th><th>Opponent</th><th>Result</th><th>At ${esc(line)}</th></tr></thead><tbody>${series.map(g=>`<tr><td>${esc(g.date)}</td><td>${esc(g.opponent)}</td><td>${esc(g.value)}</td><td>${g.value===line?'Push':g.value>line?'Over':'Under'}</td></tr>`).join('')}</tbody></table></details>`:'<p class="h-detail-loading">Game-by-game results are not available for this market. Any hit count above comes from its saved board summary.</p>'}
      <p class="h-history-note">History uses today’s selected line, not the closing lines. Past hit rate is not a predicted chance.</p></section>
      <section class="h-line-shop"><div class="h-shop-head"><h3>Compare lines</h3><small>Same event · stat · period</small></div><table><thead><tr><th>Platform / line</th><th>Over</th><th>Under</th></tr></thead><tbody>${quotes.map(q=>`<tr class="${key(q)===H.detail?'is-selected':''}"><td><button data-hub="detail-line" data-id="${esc(key(q))}" aria-pressed="${key(q)===H.detail}"><span>${esc(q.offer.platform_label)}</span><b>${esc(q.offer.line)}</b></button></td><td>${esc(quoteText(q,'over'))}</td><td>${esc(quoteText(q,'under'))}</td></tr>`).join('')}</tbody></table><p>Choose a line to recalculate its historical results. A lower Over line or higher Under line can help, but payouts and availability also matter.</p>
      <div class="h-detail-action"><span>${r.scout.hold?'Research estimate':esc(C.verdictText(r,side))} · ${pct(C.chanceOf(r,side))}</span><button data-hub="detail-add" ${eligible?'':'disabled'}>${C.onSlip(r)?'Review saved slip':`Add ${side==='over'?'Over':'Under'} ${esc(line)} · ${esc(r.offer.platform_label)}`}</button>${R.me.features?.book_links&&r.offer.platform==='underdog'?`<button data-hub="open-line" ${eligible?'':'disabled'}>Open in Underdog</button>`:''}${!r.offer[side].offered?'<small>This side is not offered on the selected platform.</small>':''}</div></section></div>
      ${facts.length?`<details class="h-stat-library" open><summary>Player stats & matchup <span>${facts.length} available measures</span></summary><dl>${facts.map(f=>`<div><dt>${esc(f.label)}</dt><dd>${esc(typeof f.value==='object'?JSON.stringify(f.value):f.value)}</dd></div>`).join('')}</dl></details>`:''}`;
    restoreFocus();
  }
  function paintPicks() {
    const box=$('hub-picks-body');if(!box||H.section!=='picks')return;
    if(!T.mine){box.innerHTML=C.picksTab();return;}
    const all=T.mine.receipts||[], list=all.filter(r=>(H.pickSport==='all'||r.sport===H.pickSport)&&(H.pickStatus==='all'||r.status===H.pickStatus));
    const hits=list.filter(r=>r.status==='hit').length,miss=list.filter(r=>r.status==='miss').length,push=list.filter(r=>r.status==='push').length;
    box.innerHTML=`<div class="h-pick-summary"><span><b>${list.length}</b> tracked picks</span><span><b>${hits}–${miss}</b> wins / losses</span><span><b>${push}</b> pushes</span><span><b>${hits+miss?pct(hits/(hits+miss)):'—'}</b> hit rate</span></div>
      ${list.length?Object.keys(C.PLATFORMS).map(p=>{const rows=list.filter(r=>r.platform===p||r.book===p||r.platform_label===C.PLATFORMS[p].label);return rows.length?`<section class="h-picks-group"><h3>${esc(C.PLATFORMS[p].label)} <span>${rows.length} picks</span></h3>${rows.map(C.receiptHtml).join('')}</section>`:'';}).join(''):'<div class="h-empty"><h3>No picks in this view</h3><p>Recheck a slip and track it to save the exact line and forecast you saw.</p><button data-hub="section" data-id="board">Find a player prop</button></div>'}`;
  }
  function paintStudio() {
    const box=$('hub-studio-body');if(!box||H.section!=='studio')return;
    const a=document.activeElement,u=box.contains(a)?a.dataset.u:null,id=u?a.dataset.id:null;
    box.innerHTML=C.slipTab().replace('data-u="tab" data-id="picks"','data-hub="receipts"');
    if(!T.legs.length&&!T.tracked)box.innerHTML='<div class="h-empty"><h3>Your slip is empty</h3><p>Choose a prop from any sport, review the line, then add your side.</p><button data-hub="section" data-id="board">Browse player props</button></div>';
    if(u)box.querySelector(`[data-u="${CSS.escape(u)}"]${id==null?'':`[data-id="${CSS.escape(id)}"]`}`)?.focus({preventScroll:true});
    if(V2&&$('hub-slip-history'))$('hub-slip-history').innerHTML=C.picksTab();
    $('hub-studio-sync').textContent=T.saveFail?'Not yet synced. Your changes remain on this device.':T.pending||T.saving?'Saving your changes…':'Saved to your account · available on your other devices';
    if($('hub-studio-summary'))$('hub-studio-summary').textContent=`${T.legs.length} picks · ${new Set(T.legs.map(l=>l.sport)).size} sports · ${new Set(T.legs.map(l=>l.book)).size} ${new Set(T.legs.map(l=>l.book)).size===1?'platform':'platforms'}`;
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
        mode:$('hub-generation-mode').value,target:Number($('hub-generation-target').value),tolerance:Number($('hub-generation-tolerance').value),mix:$('hub-generation-mix').value,games:$('hub-generation-games').value,sports};
      const batch=await C.generateBatch(pool,options,text=>{status.textContent=text;});
      if(!batch.slips.length)throw new Error(batch.note||'No combinations passed recheck. Widen your settings.');
      H.batch=batch;H.batchSaved=false;paintBatch();
      await saveBatch();
      status.textContent=`${batch.slips.length} of ${options.count} slips generated and rechecked. ${batch.note||'Review them below; your working draft is unchanged.'}`;
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
    if(!slips.length){box.innerHTML='<div class="h-empty"><h3>Your generated slips appear here</h3><p>Choose how many slips and how many picks per slip. Your working draft stays separate.</p></div>';return;}
    const use=new Map();slips.forEach(s=>s.legs.forEach(l=>{const k=l.sport+'|'+l.player_key;use.set(k,(use.get(k)||0)+1);}));
    box.innerHTML=`<header class="h-batch-head"><div><span class="h-eyebrow">GENERATED LINEUPS</span><h2>${slips.length} separate slips</h2><p>${H.batchSaved?'Saved to your account':'Not yet saved across devices'} · generated ${esc(when(b.created_at))}</p></div>${!H.batchSaved?'<button data-hub="save-batch">Retry save</button>':''}</header><p class="h-batch-note">Chances are the model’s published estimates for individual props, not a combined slip win probability. Repeated players across slips share risk. Every slip is rechecked when opened.</p><div class="h-batch-grid">${slips.map((s,i)=>{
      const ps=s.legs.map(l=>l.shown_p).filter(num),avg=ps.reduce((a,b)=>a+b,0)/ps.length;
      return `<article class="h-generated-card"><header><span>SLIP ${String(i+1).padStart(2,'0')}</span><strong>${esc(C.PLATFORMS[s.platform].label)} · ${esc(s.entry_type)}</strong><small>${s.legs.length} picks · ${esc([...new Set(s.legs.map(l=>SPORTS[l.sport]))].join(' + '))}</small></header><div class="h-batch-range"><span>Prop chances <b>${pct(Math.min(...ps))}–${pct(Math.max(...ps))}</b></span><span>Average <b>${pct(avg)}</b></span></div><ol>${s.legs.map(l=>`<li><div><strong>${esc(l.player)}</strong><small>${esc(SPORTS[l.sport])} · ${esc(l.event_label)}</small><span>${esc(l.side)} <b>${esc(l.line)}</b> ${esc(l.stat_label)} · ${esc(l.period_label)}</span><small>${use.get(l.sport+'|'+l.player_key)} of ${slips.length} slips use this player</small></div><b>${pct(l.shown_p)}</b></li>`).join('')}</ol><footer><button data-hub="use-generated" data-id="${i}">${T.legs.some(l=>l.book===s.platform)?'Replace working '+esc(C.PLATFORMS[s.platform].label)+' entry':'Open in working draft'}</button>${R.me.features?.book_links?`<button data-hub="open-generated" data-id="${i}">Open in ${esc(C.PLATFORMS[s.platform].label)}</button>`:""}<small id="hub-batch-message-${i}" role="status"></small></footer></article>`;
    }).join('')}</div>`;
  }
  async function useGenerated(id) {
    const s=H.batch?.slips?.[Number(id)];if(!s||H.usingBatch)return;
    H.usingBatch=true;
    try {await C.useGenerated(s);refreshSlip();paintBatch();$('hub-batch-message-'+id).textContent='Rechecked and opened in your working draft below.';}
    catch(e){$('hub-batch-message-'+id).textContent=e.status?'Could not recheck this slip. Your draft was kept.':e.message;}
    finally{H.usingBatch=false;}
  }
  function generatorHtml() {
    return `<section class="h-generator" aria-label="Generate slips"><span class="h-eyebrow">LINEUP GENERATOR</span><h2>Build your batch</h2><p>Separate slips, balanced around the prop chances you choose.</p><fieldset id="hub-generation-sports"><legend>Eligible sports</legend>${allowed().map(s=>`<label><input type="checkbox" value="${s}" checked><span>${SPORTS[s]}</span></label>`).join('')}</fieldset><div class="h-generator-controls">
      <label>Platform<select id="hub-generation-book"><option value="prizepicks">PrizePicks</option><option value="underdog">Underdog</option></select></label>
      <label>Entry type<select id="hub-generation-type"><option value="power">Power</option><option value="flex">Flex</option></select></label>
      <label>Number of slips<select id="hub-generation-slips">${[1,2,4,8,12].map(n=>`<option value="${n}" ${n===8?'selected':''}>${n} slips</option>`).join('')}</select></label>
      <label>Picks per slip<select id="hub-generation-count">${[2,3,4,5,6].map(n=>`<option value="${n}">${n} picks</option>`).join('')}</select></label>
      <label>Selection style<select id="hub-generation-mode"><option value="balanced">Balanced</option><option value="ranked">Highest chance</option></select></label>
      <label>Sport mix<select id="hub-generation-mix"><option value="any">Any mix</option><option value="mixed">Mixed sports</option><option value="single">One sport</option></select></label>
      <label>Target prop chance<select id="hub-generation-target">${[50,55,60,65,70,75,80].map(n=>`<option value="${n}" ${n===60?'selected':''}>${n}%</option>`).join('')}</select></label>
      <label>Allowed range<select id="hub-generation-tolerance"><option value="3">±3 points</option><option value="5" selected>±5 points</option><option value="10">±10 points</option></select></label>
      <label>Game selection<select id="hub-generation-games"><option value="spread">No stacks</option><option value="stacks">Allow stacks</option></select></label>
      <button id="hub-generate" data-hub="generate">Generate slips</button></div><p id="hub-generation-status" role="status" aria-live="polite"></p><details><summary>How lineups are built</summary><p>Each slip has distinct players. No stacks uses one player per game; Allow stacks permits supported same-game combinations, whose results can be correlated. The batch varies players to spread exposure; slips may share picks. Balanced mode keeps every prop inside your target range. Current published estimates are not calibrated equally across sports. No return or combined win probability is promised. Each candidate passes the server’s platform and freshness checks.</p></details></section>`;
  }
  async function showRecord() {
    const seq=++H.seq, sport=$('hub-record-sport').value;
    $('hub-record-body').innerHTML='<p class="h-empty">Loading the saved record…</p>';
    const sports=sport==='all'?allowed():[sport];
    const docs=await Promise.allSettled(sports.map(s=>api(s==='cs2'?'stated':'results?sport='+s)));
    if(seq!==H.seq||H.section!=='record') return;
    $('hub-record-body').innerHTML=docs.map((res,i)=>{
      const s=sports[i];
      if(res.status!=='fulfilled') return `<article class="h-record-card"><h2>${SPORTS[s]}</h2><p>Record unavailable. <button data-hub="record-retry">Retry</button></p></article>`;
      const d=res.value,t=d.totals||{},bands=s==='cs2'?d.by_letter:d.groups?.chance;
      return `<article class="h-record-card"><header><h2>${SPORTS[s]}</h2><span>${s==='nba'||s==='nfl'?'Research forecasts':'Saved forecasts'}</span></header><div class="h-score"><div><b>${t.wins??'—'}<em> / </em>${t.losses??'—'}</b><span>Wins / losses · ${t.pushes??'—'} pushes separately</span></div><div><b>${pct(t.observed)}</b><span>Actual hit rate</span></div><div><b>${pct(t.printed)}</b><span>Average stated chance</span></div></div>
        <p>${esc(d.population||d.rule||'')}</p>${d.population?`<details><summary>Counting rule</summary><p>${esc(d.rule)}</p></details>`:''}
        ${bands?.length?`<div class="h-reliability"><div class="h-rel-head"><span>Tier / sample</span><span>Stated ○ · Actual ●</span></div>${bands.map(b=>`<div class="h-rel-row"><span><b>${esc(b.name)}</b><small>${b.picks??0} picks</small></span><div class="h-rel-scale">${num(b.printed)?`<i class="stated" style="left:${Math.max(0,Math.min(100,b.printed*100))}%"></i>`:''}${num(b.observed)?`<i class="actual" style="left:${Math.max(0,Math.min(100,b.observed*100))}%"></i>`:''}</div><span>${pct(b.printed)} / ${pct(b.observed)}</span></div>`).join('')}<p class="h-axis">0%<span>50%</span>100%</p></div>`:''}
        <footer>${num(t.wins)&&num(t.losses)?t.wins+t.losses:'—'} decided picks · ${t.matches??'—'} ${esc(t.match_word||'matches')} · Updated ${esc(d.as_of?when(d.as_of):'time unavailable')}${t.ci?` · 95% interval ${pct(t.ci[0])}–${pct(t.ci[1])}`:''}</footer></article>`;
    }).join('');
  }

  function paintGames() {
    const box=$('hub-games-body');if(!box||H.section!=='games')return;
    const games=new Map();
    H.records.filter(r=>!C.started(r)).forEach(r=>{const k=gameKey(r);if(!games.has(k))games.set(k,[]);games.get(k).push(r);});
    const ordered=[...games.entries()].sort((a,b)=>Date.parse(a[1][0].event.start_utc)-Date.parse(b[1][0].event.start_utc));
    box.innerHTML=ordered.length?ordered.map(([k,rs])=>{const r=rs[0],players=new Set(rs.map(x=>x.player.key)),markets=new Set(rs.map(x=>x.market.stat)),books=new Set(rs.map(x=>x.offer.platform_label));return `<article class="h-game-card"><header><span class="h-eyebrow">${esc(SPORTS[r.sport])}</span><time>${esc(when(r.event.start_utc))}</time></header><h2>${esc(r.event.label)}</h2><p>${players.size} players · ${markets.size} markets · ${rs.length} lines</p><div class="h-game-books">${[...books].map(b=>`<span>${esc(b)}</span>`).join('')}</div><button data-hub="game-board" data-id="${esc(k)}">Research matchup <span aria-hidden="true">→</span></button></article>`;}).join(''):`<div class="h-empty"><h2>${H.loading?'Loading games…':'No upcoming games'}</h2><p>${H.loading?'Reading your available boards.':'Refresh the boards to check the latest slate.'}</p><button data-hub="refresh">Refresh boards</button></div>`;
  }
  function paintTools() {
    $('hub-tools-body').innerHTML=`<div class="h-tool-grid"><article><span class="h-eyebrow">MARKETS</span><h2>Moneylines</h2><p>Compare available game markets in the existing moneyline workspace.</p><a href="/?view=classic#moneylines">Open moneylines →</a></article><article><span class="h-eyebrow">RESEARCH</span><h2>Single prop grader</h2><p>Inspect a player and line with the existing grading tools.</p><a href="/?view=classic#one">Open grader →</a></article><article><span class="h-eyebrow">SAVED FILTERS</span><h2>Line alerts</h2><p>Watch for new matches to your saved filters while this page is open.</p><button data-hub="section" data-id="alerts">Manage alerts →</button></article><article><span class="h-eyebrow">EXPLAIN A PROP</span><h2>Ask the Genie</h2><p>Open player research from the Board and use its Genie tab. Answers stay attached to the selected prop.</p><button data-hub="section" data-id="board">Choose a player →</button></article></div>`;
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
  function marketHTML(){
    if(H.marketLoading)return '<p role="status">Checking current quotes…</p>';
    if(H.marketError)return `<p class="h-warning">${esc(H.marketError)} Use Refresh prices to retry.</p>`;
    if(!H.marketDoc)return '<p>Choose Refresh prices to read the latest verified market snapshot.</p>';
    return Object.entries(H.marketDoc.leagues||{}).map(([sport,d])=>{
      const stale=!d.as_of||Date.now()-Date.parse(d.as_of)>120000;
      if(d.state!=='ready'||stale)return `<article><h3>${esc(SPORTS[sport])}</h3><p class="h-warning">${stale?'Feed stale — last update '+age(d.as_of):'Market feed unavailable'}. Opportunity actions are disabled.</p></article>`;
      const fmt=x=>num(x)?(x*100).toFixed(1)+'%':'—';
      const quote=q=>`${esc(q.book)} · ${esc(q.player_id)} · ${esc(q.side)} ${esc(q.line)} ${esc(q.stat)} · ${esc(q.period)} · ${Number(q.decimal_odds).toFixed(2)}`;
      return `<article><h3>${esc(SPORTS[sport])} <small>${age(d.as_of)}</small></h3><h4>Positive expected value</h4>${d.ev?.length?d.ev.map(x=>`<div class="h-alert"><b>${quote(x.quote)}</b><span>Chance ${fmt(x.p)} · EV ${fmt(x.ev)}${x.wide_market?' · Wide market':''}</span></div>`).join(''):'<p>No verified positive-EV offers right now.</p>'}<h4>Two-book price comparisons</h4>${d.arbitrage?.length?d.arbitrage.map(x=>`<div class="h-alert"><div>${x.legs.map((q,i)=>`<p>${quote(q)} · stake share ${fmt(x.stake_shares[i])}</p>`).join('')}</div><b>Quoted return ${fmt(x.quoted_return)}</b></div>`).join(''):'<p>No compatible fresh arbitrage quotes right now.</p>'}<details><summary>Why quotes were excluded</summary>${Object.entries(d.excluded||{}).map(([reason,n])=>`<p>${esc(reason)}: ${Number(n)}</p>`).join('')}</details></article>`;
    }).join('')||'<p>No supported market feeds are available to this account.</p>';
  }
  function paintMarkets(){
    if(!R.me.features?.market_tools||H.section!=='tools')return;
    if(!$('hub-market-results'))$('hub-tools-body').insertAdjacentHTML('beforeend',`<section class="h-market-tools"><div class="h-section-head"><div><span class="h-eyebrow">PRICE RESEARCH</span><h2>EV &amp; arbitrage</h2><p>Exact lines, periods and settlement rules. No bet placement or guaranteed profit.</p></div><button data-hub="refresh-markets">Refresh prices</button></div><div id="hub-market-results" aria-live="polite"></div></section>`);
    $('hub-market-results').innerHTML=marketHTML();
  }
  function installV2() {
    if(!V2)return;
    $('u-hub').classList.add('h-v2');
    const nav=[['board','Board'],['games','Games'],['studio','Slips'],['record','Results'],['tools','Tools']];
    const html=nav.map(([id,label])=>`<button data-hub="section" data-id="${id}">${label}</button>`).join('');
    document.querySelector('.h-sidebar nav[aria-label="Research hub"]').innerHTML=html;
    document.querySelector('.h-sidebar [data-id="tools"]').insertAdjacentHTML('beforeend','<span id="hub-alert-count" aria-label="New filter matches"></span>');
    document.querySelector('.h-nav-mobile').innerHTML=html;
    $('hub-board-section').insertAdjacentHTML('afterend','<section id="hub-games-section" hidden><div class="h-section-head"><div><h2>The upcoming slate</h2><p>Every available sport. Research a matchup without losing your slip.</p></div></div><div id="hub-games-body" class="h-game-grid"></div></section><section id="hub-tools-section" hidden><div class="h-section-head"><h2>Your research toolkit</h2></div><div id="hub-tools-body"></div></section>');
    document.querySelector('.h-quick').insertAdjacentHTML('afterbegin',[['freshOnly','Current only'],['soonOnly','Starts within 1h'],['movedOnly','Line moved']].map(([f,label])=>`<button id="hub-${f}" data-hub="quick" data-id="${f}" aria-pressed="false">${label}</button>`).join(''));
    $('hub-studio-section').insertAdjacentHTML('beforeend','<section class="h-tracked-slips"><header><div><span class="h-eyebrow">YOUR RECORD</span><h2>Tracked slips</h2><p>Exact saved lines, grouped by entry. Each leg keeps its settlement receipt.</p></div><button data-hub="receipts">Filter individual picks →</button></header><div id="hub-slip-history"></div></section>');
    document.querySelector('.h-working-draft>p').insertAdjacentHTML('afterend','<button data-hub="share-card">Download lineup image</button>');
  }

  function section(name,navigate=true) {
    if(!['board','studio','record','alerts','picks',...(V2?['games','tools']:[])].includes(name))name='board';
    H.section=name;
    if(navigate){const hash=(V2?'#/'+({studio:'slips',record:'results'}[name]||name):'#scout/'+name)+(name==='board'?'?sport='+H.sport:'');if(location.hash!==hash)history.pushState(null,'',hash);H.routeHash=location.pathname+location.hash;}
    for(const n of ['board','studio','record','alerts','picks',...(V2?['games','tools']:[])]) {$('hub-'+n+'-section').hidden=n!==name; document.querySelectorAll(`[data-hub="section"][data-id="${n}"]`).forEach(b=>b.setAttribute('aria-current',n===name?'page':'false'));}
    $('u-hub').classList.toggle('h-studio-mode',name==='studio');
    $('hub-title').textContent=({games:'Games',tools:'Research tools',record:'Performance record',picks:'My picks',alerts:'Line alerts',studio:'Slip Studio'})[name]||'Player props';
    if(name==='board')paintBoard();else if(name==='studio'){paintStudio();paintBatch();if(V2)C.loadMine();}else if(name==='record')showRecord();else if(name==='picks'){paintPicks();C.loadMine();}else if(name==='games')paintGames();else if(name==='tools'){paintTools();paintMarkets();}else paintAlerts();
  }
  function route() {
    const query=new URLSearchParams(location.search),classic=query.get('view')==='classic'||query.has('slip')||query.has('p')||query.get('bv')==='1';
    const csBoard=/^#board(?:\/(?:all|prizepicks|underdog))?$/.test(location.hash);
    const modern=V2&&location.hash.match(/^#\/(board|games|slips|results|tools|picks|alerts)(?:\?(.*))?$/);
    const scout=location.hash.match(/^#scout\/(board|studio|picks|record|alerts)(?:\?(.*))?$/),slips=/^#\/?slips(?:\?|$)/.test(location.hash);
    const mainBoard=location.pathname==='/lol'&&!location.hash || location.pathname==='/sports'&&(!location.hash||location.hash.startsWith('#/board')) || location.pathname==='/'&&(!location.hash||csBoard);
    const open=location.hash==='#all-sports'||!!scout||!!modern||(!classic&&(mainBoard||slips));
    if(open&&H.routeHash!==location.pathname+location.hash){
      H.routeHash=location.pathname+location.hash;
      H.sport=modern?(new URLSearchParams(modern[2]||'').get('sport')||H.sport):scout?(new URLSearchParams(scout[2]||'').get('sport')||H.sport):location.hash==='#all-sports'?'all':location.pathname==='/lol'?'lol':location.pathname==='/sports'?(location.hash.includes('lg=nfl')?'nfl':'nba'):'cs2';
      if(!allowed().includes(H.sport))H.sport='all';
      const routeBook=location.hash.split('/')[1];
      if(csBoard&&['all','prizepicks','underdog'].includes(routeBook))H.book=routeBook;
      H.stat='all';H.game='all';H.page=0;
    }
    $('u-hub').hidden=!open; document.documentElement.classList.toggle('hub-open',open);
    // Original content remains mounted, but keyboard/screen readers cannot enter the hidden layer.
    for(const node of document.body.children) {
      if(node.id==='u-hub'||node.classList.contains('u-dialog')||node.classList.contains('u-toast')||node.id==='u-tray-btn'||node.tagName==='SCRIPT') continue;
      if(open&&!node.inert){node.dataset.hubInert='1';node.inert=true;}
      if(!open&&node.dataset.hubInert){node.inert=false;delete node.dataset.hubInert;}
    }
    if(open) {section(modern?({slips:'studio',results:'record'}[modern[1]]||modern[1]):scout?scout[1]:slips?'studio':'board',false);syncFields();paintBoard();refreshSlip();load();}
  }
  const rec = id => H.records.find(r=>key(r)===id);
  const actions={
    'share-card'(){C.shareSlip();},
    quick(id){if(!V2||!['freshOnly','soonOnly','movedOnly'].includes(id))return;H[id]=!H[id];H.page=0;paintBoard();},
    'game-board'(id){H.game=id;H.minGrade='all';H.sport='all';H.book='all';H.stat='all';H.side='all';H.q='';H.onlyDiff=false;H.watched=false;H.freshOnly=false;H.soonOnly=false;H.movedOnly=false;H.page=0;section('board');},
    generate, 'use-generated':useGenerated, 'open-generated':i=>C.openGenerated(H.batch?.slips?.[Number(i)]), 'open-line':()=>C.openLine(rec(H.detail),H.histSide), 'save-batch':saveBatch,
    'player-market'(id){H.stat='all';H.game='all';H.q='';H.side='all';H.onlyDiff=false;H.watched=false;H.expanded=null;const r=rec(id);if(!r)return;H.page=Math.max(0,Math.floor(rows().findIndex(x=>key(x)===id)/40));expand(id);},
    filters(){const open=$('hub-board-section').classList.toggle('filters-open');$('hub-filters-toggle').setAttribute('aria-expanded',String(open));},
    section, sport(id){H.sport=id;H.stat='all';H.game='all';H.page=0;section('board');}, game(id){H.game=id;H.page=0;paintBoard();},page(id){H.page+=Number(id);paintBoard();$('hub-table').scrollIntoView({block:'start'});},
    expand,'detail-line':selectDetail,'detail-retry'(){selectDetail(H.detail);},'history-side'(id){H.histSide=id;paintDetail();},'history-window'(id){H.histN=Number(id);paintDetail();},
    'detail-add'(){const r=rec(H.detail);if(!r||H.detailState!=='ready'||unavailableReason(r)||C.started(r)||!r.offer[H.histSide].offered)return;if(C.onSlip(r))return C.openTray();C.addLeg(r,H.histSide);refreshSlip();},
    research(id){const r=rec(id);if(r)C.research(r);else C.say('This offer is no longer on the board. Refresh to see current lines.');},
    books(id){const r=rec(id);if(r)C.research(r,'books');},
    add(id){const r=rec(id);if(!r)return;if(unavailableReason(r)||C.started(r))return C.say('This offer needs a fresh source check. Refresh the board.');if(C.onSlip(r))return C.openTray();if(!r.scout.side||!r.offer[r.scout.side]?.offered)return C.research(r);C.addLeg(r,r.scout.side);refreshSlip();},
    watch(id){const r=rec(id);if(!r)return;const k=playerKey(r);H.watch=H.watch.includes(k)?H.watch.filter(x=>x!==k):[...H.watch,k].slice(-100);savePreferences();paintBoard();},
    diff(){H.onlyDiff=!H.onlyDiff;H.page=0;paintBoard();}, watched(){H.watched=!H.watched;H.page=0;paintBoard();},
    refresh(){H.detailSeq++;H.expanded=null;H.detail=null;load(true);}, tray(){section('studio');},receipts(){section('picks');},close(){const u=new URL(location.href);u.searchParams.set('view','classic');u.hash=PAGE_BACK;location.assign(u.href);},
    'save-view'(){openSave('view');}, 'save-alert'(){openSave('alert');}, 'save-retry':savePreferences,
    'grade-review'(id,el){showGradeReview(id,el?.dataset.before);},
    async 'refresh-markets'(){
      H.marketLoading=true;H.marketError=null;paintMarkets();
      try{H.marketDoc=await api('market-tools');}catch(e){H.marketError=e.message||'Prices could not be loaded.';}
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
  const PAGE_BACK=location.pathname==='/sports'?'#/board':location.pathname==='/lol'?'':'#board';
  function openSave(kind) {H.saveKind=kind;$('hub-save-title').textContent=kind==='alert'?'Alert on these filters':'Save this research view';$('hub-save-name').value='';$('hub-save-dialog').showModal();$('hub-save-name').focus();}
  document.body.insertAdjacentHTML('beforeend',`<section id="u-hub" class="scout-hub" aria-label="All sports research" hidden>
    <aside class="h-sidebar"><a class="h-brand" href="/"><span>1T</span><b>1TIME <em>SCOUT</em></b></a><p class="h-nav-label h-sport-nav-label">PLAYER PROPS</p><nav class="h-sport-nav" aria-label="Sports boards">${['all',...allowed()].map(s=>`<button data-hub="sport" data-id="${s}"><span class="h-nav-icon">${s==='all'?'◈':s==='cs2'?'CS':s==='lol'?'LoL':s==='nba'?'NBA':'NFL'}</span>${s==='all'?'All sports':SPORTS[s]}</button>`).join('')}</nav><p class="h-nav-label">YOUR RESEARCH</p><nav aria-label="Research hub"><button data-hub="section" data-id="board">Player props</button><button data-hub="section" data-id="studio">Slip Studio</button><button data-hub="section" data-id="picks">My picks</button><button data-hub="section" data-id="record">Performance record</button><button data-hub="section" data-id="alerts">Line alerts <span id="hub-alert-count"></span></button></nav><p class="h-nav-label">MORE TOOLS</p><nav aria-label="Original boards"><a href="/?view=classic#moneylines">Moneylines <span>↗</span></a><a href="/?view=classic#one">Single prop grader <span>↗</span></a><a href="/?view=classic#matches">Match research <span>↗</span></a></nav><div class="h-sidebar-foot"><b>Your boards, connected.</b><p>One saved slip across CS2, LoL, NBA and NFL.</p><button data-hub="close">Classic board ↗</button></div></aside>
    <div class="h-center"><header class="h-top"><div><span class="h-eyebrow">1TIME SCOUT / RESEARCH</span><h1 id="hub-title">Player props</h1></div><div class="h-top-actions"><details class="h-tools-menu"><summary>More tools</summary><nav><a href="/?view=classic#moneylines">Moneylines</a><a href="/?view=classic#one">Single prop grader</a><a href="/?view=classic#matches">Match research</a><button data-hub="close">Classic board</button></nav></details><button data-hub="close" class="h-classic">Classic board ↗</button></div></header>
      <section id="hub-board-section"><div id="hub-sports" class="h-sports" role="group" aria-label="Sport filter"></div><div id="hub-games" class="h-games" role="group" aria-label="Matchup filter"></div><div class="h-tools"><label class="h-search"><span>Search</span><input id="hub-q" type="search" placeholder="Player, team or game" aria-label="Search all sports"></label><label>Platform<select id="hub-book"><option value="all">All platforms</option><option value="prizepicks">PrizePicks</option><option value="underdog">Underdog</option></select></label><label>Market<select id="hub-stat"><option value="all">All markets</option></select></label><label>Side<select id="hub-side"><option value="all">Both sides</option><option value="over">Over</option><option value="under">Under</option></select></label><label>Sort<select id="hub-sort"><option value="start">Starting soon</option><option value="player">Player name</option><option value="chance">Stated chance</option></select></label><button data-hub="refresh" aria-label="Refresh all boards">↻</button></div>
      <div class="h-quick"><label>Minimum grade<select id="hub-minGrade"><option value="all">Any grade</option>${gradeOrder.slice().reverse().map(g=>`<option value="${g}">${g} or higher</option>`).join('')}</select></label><button id="hub-diff" data-hub="diff" aria-pressed="false">Lines differ</button><button id="hub-watch" data-hub="watched" aria-pressed="false">★ Watchlist</button><select id="hub-views" aria-label="Saved research views"><option>Saved views</option></select><button data-hub="save-view">Save view</button><button data-hub="save-alert">Alert me</button></div><div id="hub-health" class="h-health" role="status"></div>
      <div class="h-table-title"><h2>Available lines</h2><span id="hub-count"></span><small>History is measured against the displayed line</small></div><div class="h-table-wrap"><table id="hub-table" class="h-table"><thead><tr><th><span class="u-sr">Watch</span></th><th>Player / matchup</th><th>Market</th><th>Line</th><th>Proj.</th><th>Scout side</th><th>L5</th><th>L10</th><th>L20</th><th>Compare</th><th>Scout grade</th><th><span class="u-sr">Add to slip</span></th></tr></thead><tbody id="hub-rows"></tbody></table><p id="hub-empty" class="h-empty">Loading the available boards…</p></div><div id="hub-pagination" class="h-pagination"></div></section>
      <section id="hub-studio-section" hidden><div class="h-studio-intro"><div><h2>Build across sports. Keep every line.</h2><p>CS2, LoL, NBA and NFL in one account draft. Each platform gets its own entry and its own rule check.</p><span id="hub-studio-sync" class="h-sync"></span></div><button data-hub="section" data-id="board">+ Add player props</button></div>${generatorHtml()}<section id="hub-generated" aria-label="Generated slips"></section><section class="h-working-draft"><h2>Your working draft</h2><p>Open one generated slip here to review, track or copy. Your other generated slips stay saved above.</p><div id="hub-studio-body"></div></section><div class="h-studio-explain"><h3>Before you track</h3><p>Scout rechecks the event, line revision, offered side and platform rules. Tracking saves a receipt; it does not place a bet. Unsupported combinations stay blocked, with the reason beside that entry.</p><button data-hub="receipts">View tracked picks →</button></div></section><section id="hub-picks-section" hidden><div class="h-section-head"><div><h2>Your picks, with receipts.</h2><p>The line and forecast saved when you tracked. Settled at that line; pushes stay separate.</p></div><div class="h-pick-filters"><label>Sport<select id="hub-pick-sport"><option value="all">All sports</option>${allowed().map(s=>`<option value="${s}">${SPORTS[s]}</option>`).join('')}</select></label><label>Status<select id="hub-pick-status"><option value="all">All outcomes</option><option value="pending">Pending</option><option value="hit">Won</option><option value="miss">Lost</option><option value="push">Pushed</option><option value="void">Void</option></select></label></div></div><div id="hub-picks-body"></div></section><section id="hub-record-section" hidden><div class="h-section-head"><div><h2>The record, in context.</h2><p>Stated chances beside actual outcomes. Different sport cohorts stay separate.</p></div><select id="hub-record-sport" aria-label="Record sport"><option value="all">All sports</option>${allowed().map(s=>`<option value="${s}">${SPORTS[s]}</option>`).join('')}</select></div><div id="hub-record-body"></div></section><section id="hub-alerts-section" hidden><div id="hub-alerts-body"></div></section>
    </div><aside class="h-slip" aria-label="Saved cross-sport slip"><header><div><span class="h-eyebrow">YOUR LINEUP</span><h2>Slip builder <span id="hub-slip-count">0</span></h2></div><button data-hub="receipts" title="View tracked picks">Receipts ↗</button></header><p id="hub-save-status" class="h-sync" aria-live="polite">Saved to your account</p><div id="hub-slip-content" tabindex="-1"></div><footer>One entry per platform. Rechecked against current offers before tracking.</footer></aside>
    <button class="h-mobile-slip" data-hub="tray">Open saved slip</button></section>
    <dialog id="hub-save-dialog" class="h-save u-dialog" aria-labelledby="hub-save-title"><form method="dialog"><h2 id="hub-save-title">Save view</h2><label>Name<input id="hub-save-name" maxlength="48" required autocomplete="off"></label><p>Saved to your account. Alerts run while the research screen is open.</p><div><button value="cancel" formnovalidate>Cancel</button><button id="hub-save-confirm" value="save">Save</button></div></form></dialog>`);
  const entry=document.createElement('a');entry.href='#all-sports';entry.className='h-entry';entry.textContent='All sports research';
  $('hub-board-section').querySelector('.h-tools>button').insertAdjacentHTML('beforebegin','<button class="h-filter-toggle" id="hub-filters-toggle" data-hub="filters" aria-expanded="false" aria-label="Filters and saved views">Filters</button>');
  $('hub-board-section').insertAdjacentHTML('beforebegin','<nav class="h-nav-mobile" aria-label="Research sections"><button data-hub="section" data-id="board">Props</button><button data-hub="section" data-id="studio">Slip Studio</button><button data-hub="section" data-id="picks">My picks</button><button data-hub="section" data-id="record">Results</button><button data-hub="section" data-id="alerts">Alerts</button></nav>');
  installV2();
  const entryHost=document.querySelector('.workspace-tools')||document.querySelector('.sw-tabs')||document.querySelector('.ot-lol .bar')||document.querySelector('body header');
  if(entryHost) entryHost.appendChild(entry); else document.body.prepend(entry);
  $('hub-save-dialog').addEventListener('close',()=>{
    if($('hub-save-dialog').returnValue!=='save')return;
    const name=$('hub-save-name').value.trim();if(!name)return;
    const v={id:Date.now().toString(36),name,filters:filters()};
    if(H.saveKind==='alert'){H.alerts=[...H.alerts,v].slice(-12);checkAlerts();}else H.views=[...H.views,v].slice(-12);
    savePreferences();paintBoard();C.say(H.saveKind==='alert'?'Filter alert added for this research screen.':'Research view saved.');
  });
  $('u-hub').addEventListener('click',e=>{const b=e.target.closest('[data-hub]');if(b&&!b.disabled){e.preventDefault();actions[b.dataset.hub]?.(b.dataset.id,b);}});
  for(const f of ['q','book','side','sort','stat']) $('hub-'+f).addEventListener(f==='q'?'input':'change',()=>{H[f]=$('hub-'+f).value;H.page=0;paintBoard();});
  $('hub-minGrade').addEventListener('change',()=>{H.minGrade=$('hub-minGrade').value;H.page=0;paintBoard();});
  $('hub-views').addEventListener('change',()=>{const i=$('hub-views').value;if(i==='')return;const v=H.views[Number(i)];if(v){H.minGrade='all';for(const k of filterKeys) if(k in v.filters)H[k]=v.filters[k];H.page=0;paintBoard();}});
  $('hub-record-sport').addEventListener('change',showRecord);
  function generationControls() {
    const p=$('hub-generation-book').value,old=$('hub-generation-type').value;
    $('hub-generation-type').innerHTML=C.PLATFORMS[p].types.map(([v,label])=>`<option value="${v}">${label}</option>`).join('');
    if(C.PLATFORMS[p].types.some(t=>t[0]===old))$('hub-generation-type').value=old;
    for(const o of $('hub-generation-count').options)o.disabled=(p==='prizepicks'&&Number(o.value)>6)||(p==='underdog'&&$('hub-generation-type').value==='flex'&&Number(o.value)<3);
    if($('hub-generation-count').selectedOptions[0]?.disabled)$('hub-generation-count').value=p==='prizepicks'?'6':'3';
    const ranked=$('hub-generation-mode').value==='ranked';
    $('hub-generation-target').disabled=ranked;$('hub-generation-tolerance').disabled=ranked;
  }
  for(const id of ['book','type','mode'])$('hub-generation-'+id).addEventListener('change',generationControls);
  generationControls();
  $('hub-pick-sport').addEventListener('change',()=>{H.pickSport=$('hub-pick-sport').value;paintPicks();});
  $('hub-pick-status').addEventListener('change',()=>{H.pickStatus=$('hub-pick-status').value;paintPicks();});
  window.addEventListener('hashchange',route);
  window.addEventListener('popstate',route);
  window.addEventListener('keydown',e=>{
    if(!isOpen()||e.key!=='/'||e.ctrlKey||e.metaKey||e.altKey||document.querySelector('dialog[open]')||e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
    e.preventDefault();e.stopImmediatePropagation();section('board');$('hub-q').focus();
  },true);
  setInterval(()=>{if(isOpen()&&!document.hidden)load(true);},60000);
  loadPreferences().then(()=>{route();});
  return {refreshSlip};
}
