/* Independent research boards; no existing forecast or slip pool is changed. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const names = {prizepicks: 'PrizePicks', underdog: 'Underdog', betrivers:'BetRivers', draftkings:'DraftKings'};
  const periods = {full_game: 'Full match', regulation: 'Regulation time', set1: 'First set', first_half: 'First half', second_half: 'Second half'};
  const fold = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const el = (tag, text, cls) => {const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n;};
  const selections = new Map();
  let sport = new URLSearchParams(location.search).get('sport') === 'tennis' ? 'tennis' : 'soccer';
  let rows = [], odds = [], view = 'props', generation = 0, token = null, base = '', busy = false, limit = 36, version = null;

  function options(id, values) {
    const select = $(id), old = select.value;
    select.replaceChildren(new Option(id === 'market' ? 'All markets' : 'All periods', ''));
    for (const [key, label] of values) select.add(new Option(label, key));
    select.value = [...select.options].some(o => o.value === old) ? old : '';
  }
  async function api(path) {
    if (!token) {
      const r = await fetch('/api/sports/token', {credentials: 'same-origin', signal: AbortSignal.timeout(15000)});
      if (!r.ok) throw new Error(r.status === 401 ? 'Sign in to Scout to view these boards.' : 'Sports access is unavailable.');
      const a = await r.json(); token = a.token; base = a.api_base || '';
    }
    const r = await fetch(base + path, {headers: {Authorization: `Bearer ${token}`}, signal: AbortSignal.timeout(15000)});
    if (!r.ok) {
      if (r.status === 401) token = null;
      throw new Error(r.status === 401 ? 'Your session expired. Refresh to sign in again.' :
        r.status === 404 ? 'This sport or line is not available right now.' :
        r.status === 503 ? 'Current data is unavailable. Please retry.' : 'Could not load data. Please retry.');
    }
    return r.json();
  }
  function renderHistory(box, data) {
    box.replaceChildren(el('p', data.message || 'No verified history for this exact stat and period.', 'meta'));
    if (!data.games?.length) return;
    const windows = el('div', undefined, 'history-windows');
    for (const n of [5, 10, 20]) {
      const w = data.windows[`l${n}`];
      windows.append(el('div', `L${n}: ${w.over} Over · ${w.under} Under · ${w.push} Push (${w.games} games)`));
    }
    box.append(windows, el('h3', `Recent results · ${periods[data.scope] || data.scope}`));
    const table = el('table');
    const head = el('thead'), hr = el('tr');
    ['Date', 'Result', `vs ${data.line}`].forEach(t => {const th = el('th', t); th.scope = 'col'; hr.append(th);});
    head.append(hr); table.append(head);
    const body = el('tbody');
    for (const game of data.games) {
      const tr = el('tr');
      tr.append(el('td', new Date(game.date).toLocaleDateString()), el('td', String(game.value)),
        el('td', game.value > data.line ? 'Over' : game.value < data.line ? 'Under' : 'Push'));
      body.append(tr);
    }
    const sources = [...new Set(data.games.map(g => g.source === 'thescore' ? 'theScore' : g.source === 'espn' ? 'ESPN' : g.source === 'fotmob' ? 'FotMob' : g.source).filter(Boolean))];
    table.append(body); box.append(table, el('p', `Source: ${sources.join(', ')}. Results use this line, not historical closing lines. No predicted chance is inferred from the hit rate.`, 'meta'));
  }
  function card(list, key) {
    list.sort((a, b) => a.label.localeCompare(b.label) || Number(b.scope === 'full_game') - Number(a.scope === 'full_game') ||
      a.scope.localeCompare(b.scope) || a.line - b.line || a.platform.localeCompare(b.platform));
    const r = list[0], article = el('article', undefined, 'player'), intro = el('div', undefined, 'intro');
    intro.append(el('h2', r.player), el('p', [r.role, r.team, r.competition].filter(Boolean).join(' · '), 'context'),
      el('p', r.match, 'context'), el('p', new Date(r.start).toLocaleString([], {weekday:'short', month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}), 'context'));
    const quote = el('div', undefined, 'quote'), select = el('select'), detail = el('div');
    select.setAttribute('aria-label', `${r.player}: market and period`);
    list.forEach((v, i) => select.add(new Option(`${v.label} · ${periods[v.scope] || v.scope} · ${v.line} · ${names[v.platform]}`, i)));
    const saved = selections.get(key), index = list.findIndex(v => `${v.platform}|${v.offer_id}|${v.scope}` === saved);
    if (index >= 0) select.value = String(index);
    let request = 0;
    function show() {
      const v = list[Number(select.value) || 0], run = ++request;
      selections.set(key, `${v.platform}|${v.offer_id}|${v.scope}`);
      detail.replaceChildren(el('div', periods[v.scope] || v.scope, 'scope'), el('div', `${v.line} ${v.label}`, 'line'));
      const sides = el('div', undefined, 'sides');
      for (const [side, value] of Object.entries(v.sides)) sides.append(el('span', `${side === 'over' ? 'Over' : 'Under'} ${v.line}${value.multiplier == null ? '' : ` · ${value.multiplier}× modifier`}`));
      detail.append(sides, el('p', `${names[v.platform]} · checked ${Math.max(0, Math.floor((Date.now() - Date.parse(v.observed_at)) / 60000))} min ago`, 'meta'));
      if (!['standard','balanced'].includes(v.variant)) detail.append(el('p', `Line type: ${v.variant}`, 'meta'));
      const research = el('details', undefined, 'research'), summary = el('summary', 'Player history'), box = el('div');
      research.append(summary, box); detail.append(research);
      let loaded = false;
      async function fetchHistory() {
        if (Date.now() >= Date.parse(v.start) || Date.now() - Date.parse(v.observed_at) > 300000) {
          box.replaceChildren(el('p', 'This line needs a fresh check. Refresh lines first.', 'meta')); return;
        }
        loaded = true; box.replaceChildren(el('p', 'Loading verified history…', 'meta'));
        try {
          const data = await api('/api/sports/expansion/history?' + new URLSearchParams({sport, platform:v.platform, offer_id:v.offer_id}));
          if (run !== request || !article.isConnected) return;
          if (data.offer_revision !== v.revision) {box.replaceChildren(el('p', 'The line changed. Refresh lines before comparing history.', 'meta')); return;}
          renderHistory(box, data);
        } catch (e) {
          if (run !== request || !article.isConnected) return;
          box.replaceChildren(el('p', e.name === 'TimeoutError' ? 'History took too long. Please retry.' : e.message, 'meta'));
          const retry = el('button', 'Retry history'); retry.addEventListener('click', fetchHistory); box.append(retry);
        }
      }
      research.addEventListener('toggle', () => {if (research.open && !loaded) fetchHistory();});
    }
    select.addEventListener('change', show); quote.append(select, detail); show(); article.append(intro, quote); return article;
  }
  function render() {
    if(view==='odds'){renderOdds();return;}
    const now = Date.now(), q = fold($('search').value.trim()), m = $('market').value, p = $('period').value,
      book = $('book').value, side = $('side').value;
    const filtered = rows.filter(r => Date.parse(r.start) > now && now - Date.parse(r.observed_at) <= 300000 &&
      (!m || r.stat === m) && (!p || r.scope === p) && (!book || r.platform === book) && (!side || r.sides[side]) &&
      q.split(/\s+/).every(word => fold(`${r.player} ${r.team || ''} ${r.match || ''}`).includes(word)));
    filtered.sort((a,b) => Date.parse(a.start) - Date.parse(b.start) || a.player.localeCompare(b.player));
    const groups = new Map();
    for (const r of filtered) {const key = r.player_id + '|' + r.event_id; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(r);}
    const box = $('players'); box.replaceChildren();
    if (!groups.size) {
      $('status').textContent = '0 current lines match your filters.';
      box.append(el('div', 'No current lines match these filters. Try another market, app, or sport.', 'empty')); return;
    }
    let count = 0;
    for (const [key, list] of groups) {if (count++ >= limit) break; box.append(card(list, key));}
    if (groups.size > limit) {
      const more = el('button', `Show more players (${groups.size - limit} remaining)`, 'show-more');
      more.addEventListener('click', () => {limit += 36; render();}); box.append(more);
    }
    $('status').textContent = `${groups.size} player/match groups · ${filtered.length} current lines. App identities are kept separate.`;
  }
  function renderOdds() {
    const now=Date.now(), q=fold($('search').value.trim()), box=$('players'), all=$('all-markets').checked;
    const markets=odds.filter(r=>Date.parse(r.start)>now && now-Date.parse(r.observed_at)<=300000 &&
      (all || r.main) && q.split(/\s+/).every(w=>fold(`${r.match} ${r.competition} ${r.label}`).includes(w)));
    markets.sort((a,b)=>Date.parse(a.start)-Date.parse(b.start) || a.match.localeCompare(b.match) || Number(b.main)-Number(a.main));
    const groups=new Map();
    for (const r of markets) {const key=r.platform+'|'+r.event_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
    box.replaceChildren();
    for (const list of [...groups.values()].slice(0,limit)) {
      const r=list[0], article=el('article',undefined,'player odds-card'), intro=el('div',undefined,'intro');
      intro.append(el('h2',r.match),el('p',r.competition,'context'),el('p',`${names[r.platform]} · ${r.jurisdiction}`,'context'),
        el('p',new Date(r.start).toLocaleString(),'context'));
      const quote=el('div',undefined,'quote'),select=el('select'),detail=el('div');
      select.setAttribute('aria-label',`${r.match}: sportsbook market`);
      list.forEach((m,i)=>select.add(new Option(`${m.label}${m.main?' · Main':''}${m.outcomes[0]?.line==null?'':' · '+m.outcomes[0].line}`,i)));
      function show() {
        const m=list[Number(select.value)||0];detail.replaceChildren(el('p',m.main?'Main market':'Other market / alternate line','scope'));
        for (const o of m.outcomes) {
          const row=el('div',undefined,'book-outcome');
          row.append(el('span',`${o.label}${o.line==null?'':' '+(o.line>0 && m.market_type==='Handicap'?'+':'')+o.line}`),
            el('strong',`${o.american>0?'+':''}${o.american}`));detail.append(row);
        }
        detail.append(el('p',`${names[m.platform]} · checked ${Math.max(0,Math.floor((Date.now()-Date.parse(m.observed_at))/60000))} min ago`,'meta'),
          el('p','Book prices, not Scout predictions. Book-specific settlement rules apply.','meta'));
      }
      select.addEventListener('change',show);show();quote.append(select,detail);article.append(intro,quote);box.append(article);
    }
    if(!groups.size)box.append(el('div','No current book markets match this view. Try including other markets or refresh prices.','empty'));
    if(groups.size>limit){const more=el('button',`Show more matches (${groups.size-limit} remaining)`,'show-more');more.addEventListener('click',()=>{limit+=36;renderOdds();});box.append(more);}
    $('status').textContent=`${groups.size} book/match groups · ${markets.length} current markets. American odds; each book stays separate.`;
  }
  async function load() {
    const run = ++generation; busy = true; $('refresh').disabled = true; $('status').textContent = 'Checking current lines…';
    try {
      const data = await api(`/api/sports/expansion/board?sport=${sport}${version ? '&version=' + encodeURIComponent(version) : ''}`);
      if (run !== generation) return;
      rows = data.unchanged ? rows.map(r => ({...r, observed_at: data.sources?.[r.platform]?.checked_at || r.observed_at})) : data.offers || [];
      odds = data.unchanged ? odds.map(r => ({...r, observed_at: data.sources?.[r.platform]?.checked_at || r.observed_at})) : data.game_odds || [];
      version = data.version || null;
      options('market', [...new Map(rows.map(r => [r.stat, r.label]))].sort((a,b) => a[1].localeCompare(b[1])));
      options('period', [...new Set(rows.map(r => r.scope))].map(k => [k, periods[k] || k]));
      render();
      const failed = Object.entries(data.sources || {}).filter(([,v]) => !v.ok).map(([k]) => names[k] || k);
      $('feed-status').textContent = failed.length ? `${failed.join(', ')} is unavailable. Only current lines from responding sources are shown.` : '';
    } catch (e) {
      if (run === generation) {rows = []; odds = []; version = null; $('players').replaceChildren(); $('feed-status').textContent = ''; $('status').textContent = e.name === 'TimeoutError' ? 'The request took too long. Refresh to retry.' : e.message;}
    } finally {if (run === generation) {busy = false; $('refresh').disabled = false;}}
  }
  function chooseSport(next, navigate = true) {
    sport = next; rows = []; odds = []; limit = 36; version = null; $('players').replaceChildren();
    $('sport-title').textContent = sport === 'soccer' ? 'Soccer' : 'Tennis';
    document.querySelectorAll('[data-sport]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.sport === sport)));
    for (const id of ['market','period','book','side']) $(id).value = '';
    if (navigate) history.replaceState(null, '', '?sport=' + sport);
    load();
  }
  document.querySelectorAll('[data-sport]').forEach(b => b.addEventListener('click', () => chooseSport(b.dataset.sport)));
  document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{
    view=b.dataset.view;limit=36;
    document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.view===view)));
    $('market').parentElement.hidden=view==='odds';document.querySelector('.more-filters').hidden=view==='odds';
    $('extra-markets').hidden=view!=='odds';render();
  }));
  $('all-markets').addEventListener('change',()=>{limit=36;render();});
  for (const id of ['search','market','period','book','side']) $(id).addEventListener('input', () => {limit = 36; render();});
  $('refresh').addEventListener('click', load);
  setInterval(() => {if (!document.hidden && !busy && !document.querySelector('.research[open]')) load();}, 180000);
  setInterval(() => {if (!document.hidden && [...rows,...odds].some(r => Date.parse(r.start) <= Date.now() || Date.now()-Date.parse(r.observed_at)>300000)) render();}, 30000);
  document.addEventListener('visibilitychange', () => {if (!document.hidden && !busy) load();});
  chooseSport(sport, false);
})();
