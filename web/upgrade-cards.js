/* The research hub card's restored panels (owner 2026-11-09: "include all the factors in cards", "all odds on player props").
 * Every number is the server's, copied from what the classic card prints (workspace_api cs2_card / veto, workspace_lol lol_card,
 * workspace_sports sports_card, the line doc's books and market books). The only arithmetic here is the classic pages' own display
 * arithmetic, repeated so the hub and the classic card read the same: the sports splits and window summaries (web/sports/stats.js),
 * and a decimal price shown as American odds. Nothing here is a grade input: no grade, chance, stake or side reads it.
 * Hedge or status words are never shown; a panel with nothing definite to show is left out. */
export function panels(X) {
  const {esc, pct, when, sideWord} = X;
  const num = v => typeof v === 'number' && Number.isFinite(v);
  const n = (v, d = 1) => num(v) ? v.toFixed(d) : '—';
  const sgn = (v, d = 1) => num(v) ? (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(d) : '—';
  const pc0 = v => num(v) ? Math.round(v * 100) + '%' : '—';
  const HEDGE = /\b(questionable|doubtful|probable|day[-_ ]to[-_ ]day|unconfirmed|uncertain|not validated|research)\b/i;
  const sec = (id, title, body, open = false, sub = '') => body ? `<details class="h-fx" data-fx="${id}"${open ? ' open' : ''}><summary><span>${esc(title)}</span>${sub ? `<small>${esc(sub)}</small>` : ''}</summary><div class="h-fx-body">${body}</div></details>` : '';
  const kv = rows => {const r = rows.filter(x => x && x[1] != null && x[1] !== '' && x[1] !== '—');
    return r.length ? `<dl class="h-fx-kv">${r.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>` : '';};
  const table = (head, rows, cls = '') => rows.length ? `<div class="h-scroll-x"><table class="h-fx-table${cls ? ' ' + cls : ''}"><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : '';
  const note = t => t ? `<p class="h-history-note">${t}</p>` : '';
  const shortDate = iso => {const d = new Date(iso);return isNaN(d) ? esc(iso || '') : d.toLocaleDateString([], {month: 'short', day: 'numeric'});};
  /* the "every factor" list: what feeds this grade and what is shown at weight 0 (grading-factor rule) */
  const factorTable = (rows, showUse) => table(['Factor', 'Value', ...(showUse ? ['In this grade'] : [])],
    rows.filter(x => x && x[1] != null && x[1] !== '').map(([f, v, used]) => `<tr><td>${f}</td><td>${v}</td>${showUse ? `<td>${used === true ? '<b class="h-fx-used">Used</b>' : used ? esc(used) : '<span class="h-fx-w0">Weight 0</span>'}</td>` : ''}</tr>`), 'h-fx-factors');

  /* ---------------------------------------------------------------- CS2: the classic card's panels (web/index.html render) */
  const mapName = m => String(m || '').replace(/^de_/, '').replace(/^./, c => c.toUpperCase());
  const lanWord = e => e === 'lan' ? 'LAN' : e === 'online' ? 'online' : '';
  function awpReading(note_) {
    const m = /^(?:AWPer|Rifler)\s*\((.+)\)\s*$/i.exec(note_ || '');
    return !m || HEDGE.test(note_) || /^Liquipedia/i.test(m[1]) || /\b(AWPer|Rifler)\b/i.test(m[1]) ? '' : m[1];
  }
  function vetoHtml(v, scope) {
    if (!v || !v.state) return '';
    const only = scope === '1' ? ['Map 1', 'Map'] : null;
    let h = '';
    if ((v.confirmed || []).length) h += `<p class="h-fx-lab">Confirmed</p><div class="h-fx-chips">${v.confirmed.map(c => `<span>${esc(c.slot)} <b>${esc(c.name)}</b>${c.by ? ` <small>(${esc(c.by)} pick)</small>` : ''}</span>`).join('')}</div>`;
    if ((v.bans || []).length) h += note(`Removed: ${v.bans.map(b => `${esc(b.name)}${b.by ? ' (' + esc(b.by) + ')' : ''}`).join(' · ')}`);
    const slots = (v.slots || []).filter(s => !only || only.includes(s.slot));
    for (const s of slots) h += `<p class="h-fx-lab">${esc(s.slot)} · predicted</p><div class="h-fx-chips">${(s.top || []).map(([, name, p]) => `<span>${esc(name)} <b>${pct(p)}</b></span>`).join('')}${s.other > 0.0005 ? `<span>Other maps <b>${pct(s.other)}</b></span>` : ''}</div>`;
    if (slots.length > 1) h += note('Each map slot’s chances are for that slot on its own – they do not add up to one series.');
    if ((v.combos || []).length) h += `<p class="h-fx-lab">Most likely full series</p><ol class="h-fx-ol">${v.combos.map(([maps, p]) => `<li>${maps.map(esc).join(' · ')} <b>${pct(p)}</b></li>`).join('')}</ol>`;
    if ((v.facts || []).length) h += `<ul class="h-fx-ul">${v.facts.filter(f => !HEDGE.test(f)).map(f => `<li>${esc(f)}</li>`).join('')}</ul>`;
    if (!h) return '';
    return h + note(`${v.state === 'confirmed' ? 'Updated' : 'Forecast'} ${esc(when(v.at))} · weight 0 in the grade.`);
  }
  function cs2(r, doc) {
    const c = doc?.cs2_card;
    const out = [];
    if (!c && !doc?.veto) return out;
    const s = c?.stats || {}, ck = c?.checks || {}, hl = c?.hltv, hc = c?.context || {}, rb = c?.rank_band, env = c?.environment;
    const used = new Set(c?.used || []), show = used.size > 0, u = k => used.has(k) || 0;
    const stat = r.market.stat === 'hs' ? 'headshots' : 'kills';
    const tonight = env?.tonight, tw = lanWord(tonight);
    const fw3 = s.form_windows?.['3m'];
    /* every factor, with its weight in this grade (the classic card's "What this grade uses"); new factors at weight 0 */
    const rk = hc.ranking, ork = hc.opp_ranking;
    const rows = [
      ['Kills per round, official maps', num(fw3?.kpr) ? `${n(fw3.kpr, 3)} <small>3 months · ${esc(fw3.maps)} maps</small>` : num(s.kpr) ? n(s.kpr, 3) : null, u('kpr')],
      ['Tonight’s setting', tw ? `${tw === 'LAN' ? 'LAN' : 'Online'}${env?.[tonight]?.kpr != null ? ` · his ${tw} KPR ${n(env[tonight].kpr, 2)} <small>(${esc(env[tonight].maps)} maps)</small>` : ''}` : null, u('environment')],
      ['Maps in his sample', s.maps_played != null ? esc(s.maps_played) : null, u('sample')],
      ['Opponent', c?.match?.opponent ? esc(c.match.opponent) + (num(ork?.rank) ? ` <small>VRS #${esc(ork.rank)}</small>` : '') : null, u('opponent') ? 'Kills it gives up: used' : 0],
      ...(r.market.stat === 'hs' ? [['Headshot rate', num(fw3?.hs_pct) ? n(fw3.hs_pct, 1) + '%' : null, u('hs_per_kill')]] : []),
      ['Best of', c?.match?.bo ? 'BO' + esc(c.match.bo) : null, u('rounds') ? 'Expected rounds: used' : 0],
      ...(used.has('elo') ? [['Team strength', 'Elo ratings of both teams', true], ['Map length', 'Rounds each team’s maps usually last', true]] : []),
      ['Vs teams ranked like the opponent', rb && rb.maps ? `${sgn((rb.ratio - 1) * 100, 0)}% kills per round <small>(${esc(rb.maps)} maps vs #${Math.max(1, rb.opp_rank - (rb.band || 10))}–${rb.opp_rank + (rb.band || 10)})</small>` : null, 0],
      ['Team VRS rank', num(rk?.rank) ? `#${esc(rk.rank)}${rk.change ? ` <small>(${rk.change > 0 ? '▲' : '▼'}${Math.abs(rk.change)} in 4 weeks)</small>` : ''}` : null, 0],
      ['Opponent VRS rank', num(ork?.rank) ? `#${esc(ork.rank)}${ork.change ? ` <small>(${ork.change > 0 ? '▲' : '▼'}${Math.abs(ork.change)} in 4 weeks)</small>` : ''}` : null, 0],
      ['Rank gap', num(rk?.rank) && num(ork?.rank) ? `${rk.rank < ork.rank ? 'His team is ranked' : rk.rank > ork.rank ? 'Opponent is ranked' : 'Same rank'}${rk.rank !== ork.rank ? ` ${Math.abs(rk.rank - ork.rank)} places higher` : ''}` : null, 0],
      ['CT · T rating', hl && num(hl.ct_rating) ? `${n(hl.ct_rating, 2)} · ${n(hl.t_rating, 2)}` : null, 0],
      ['Round Swing', hl && num(hl.round_swing) ? sgn(hl.round_swing, 2) + '%' : null, 0],
      ['Recent kills per round', num(s.kpr_recent) ? `${n(s.kpr_recent, 3)}${num(s.trend_pct) ? ` <small>(${sgn(s.trend_pct, 0)}% vs his window)</small>` : ''}` : null, 0],
      ['Head-to-head, team vs opponent', hc.h2h ? `${esc(hc.h2h.wins)}–${esc(hc.h2h.losses)} in series <small>12 months</small>` : null, 0],
      ['Win chance, betting markets', num(c?.p_win) ? pct(c.p_win) : null, 0],
      ['Opening kills', num(ck.opening?.kills_per_round) ? `${n(ck.opening.kills_per_round, 3)}/round · ${pc0(ck.opening.duel_win)} duels won` : null, 0],
      ['Trade kills', num(ck.trade_pr) ? n(ck.trade_pr, 3) + '/round' : null, 0],
      ['Days since his last map', num(ck.days_since_last_map) ? esc(ck.days_since_last_map) : null, num(ck.days_since_last_map) && ck.days_since_last_map >= 30 ? 'Stake rule: max 1U' : 0],
    ];
    out.push(sec('cs2-factors', 'Every factor', factorTable(rows, show) + (show ? note('Used = it moves this projection. Weight 0 = shown for context: a factor moves grades only after a chronological test.') : ''), true));
    if (rb && rb.maps) out.push(sec('cs2-rank-band', 'Vs similar-ranked opponents', kv([
      [`Vs teams ranked #${Math.max(1, rb.opp_rank - (rb.band || 10))}–${rb.opp_rank + (rb.band || 10)} <small>(opponent #${esc(rb.opp_rank)})</small>`, `${esc(rb.maps)} maps · ${n(rb.kpr * 22, 1)} kills/map`],
      ['Kills per round there', `${n(rb.kpr, 3)} <small>vs ${n(rb.kpr_all, 3)} overall</small>`],
      ['Difference', `<b class="${rb.ratio >= 1 ? 'h-fx-up' : 'h-fx-dn'}">${sgn((rb.ratio - 1) * 100, 0)}%</b> vs his usual`],
      ['Teams', (rb.teams || []).length ? esc(rb.teams.slice(0, 8).join(', ')) : null]]) + note(`${esc(rb.source || 'HLTV')}, last 12 months · weight 0 in the grade.`), true));
    if (env && (env.lan || env.online)) out.push(sec('cs2-env', 'LAN vs online', kv([
      ['LAN', env.lan ? `${n(env.lan.kpr, 2)} KPR <small>(${esc(env.lan.maps)} maps)</small>` : null],
      ['Online', env.online ? `${n(env.online.kpr, 2)} KPR <small>(${esc(env.online.maps)} maps)</small>` : null],
      ['Tonight', tw ? `<b>${tw === 'LAN' ? 'LAN' : 'Online'}</b>${env.event ? ` <small>${esc(env.event)}</small>` : ''}` : null],
      ['Tonight vs his average', num(env.ratio) ? `${sgn((env.ratio - 1) * 100, 0)}% kills per round` : null]]) + note('Last 12 months, every official map.'), true));
    if (s && (s.rating != null || s.form_windows)) {
      const tiles = ['3m', '6m', '12m'].filter(k => s.form_windows?.[k]).map(k => [`${k} KPR · HS%`, `${n(s.form_windows[k].kpr, 3)} <small>${s.form_windows[k].hs_pct ?? '–'}% HS · ${esc(s.form_windows[k].maps)} maps</small>`]);
      const win = esc(String(s.window || '').replace('last_', 'last ').replace(/_/g, ' '));
      out.push(sec('cs2-stats', `Stats · Maps 1–2${s.env ? ` · ${lanWord(s.env)} maps` : ''} · BO3.gg`, kv([...tiles,
        ['Rating', num(s.rating) ? `${n(s.rating, 2)} <small>${win}${s.maps_played != null ? ` · ${esc(s.maps_played)} maps` : ''}</small>` : null],
        ['KAST', num(s.kast) ? `${esc(s.kast)}% <small>${win}</small>` : null], ['Deaths / round', num(s.dpr) ? n(s.dpr, 3) : null],
        ['ADR', num(s.adr) ? esc(s.adr) : null],
        ['Recent KPR', num(s.kpr_recent) ? `${n(s.kpr_recent, 3)}${num(s.trend_pct) ? ` <small>${sgn(s.trend_pct, 0)}% vs window</small>` : ''}` : null]])));
    }
    const pm = (c?.per_map || []).filter(m => m.map);
    const props = hc.prop_maps || [];
    if (pm.length || props.length) out.push(sec('cs2-maps', 'Map pool', (pm.length ? table(['Map', 'KPR', 'Maps'], pm.map(m => `<tr><td>${esc(mapName(m.map))}</td><td>${n(m.kpr, 3)}</td><td>${esc(m.maps_played ?? 0)}</td></tr>`)) + note('BO3.gg, his window.') : '')
      + (props.length ? `<p class="h-fx-lab">On the prop maps · last 6 months (HLTV)</p>` + table(['Map', 'Maps', 'KPR', 'Rating', 'ADR'], props.map(m => `<tr><td>${esc(m.map)}</td><td>${esc(m.maps)}</td><td>${n(m.kpr, 2)}</td><td>${n(m.rating, 2)}</td><td>${n(m.adr, 1)}</td></tr>`)) : '')));
    const vh = vetoHtml(doc?.veto, r.market.period === '1' || /map 1\b/i.test(r.market.period_label) && !/1.2|1.3/.test(r.market.period_label) ? '1' : null);
    const mt = c?.match || {};
    const matchBody = kv([['Opponent', mt.opponent ? esc(mt.opponent) + (mt.opponent_country ? ` <small>(${esc(mt.opponent_country)})</small>` : '') : null],
      ['Event', mt.event ? esc(mt.event) : null], ['Format', mt.bo ? 'Best of ' + esc(mt.bo) : null],
      ['Maps announced', (mt.maps || []).length ? mt.maps.map(x => esc(mapName(x))).join(' · ') : null],
      ['Team win chance', num(c?.p_win) ? `${pct(c.p_win)} <small>betting markets</small>` : null]]);
    if (matchBody || vh) out.push(sec('cs2-match', 'Match and map veto', matchBody + (vh ? `<p class="h-fx-lab">Map veto</p>${vh}` : '')));
    if (hl || hc.eco || hc.ranking || (hc.last5 || []).length) {
      const last = (hc.last5 || []).map(m => `<tr><td>${shortDate(m.date)}</td><td>${esc(m.map)}${m.number ? ` <small>M${esc(m.number)}</small>` : ''}</td><td>${esc(m.opponent)}</td><td>${esc(String(m.score ?? '–').replace(/-/g, '–'))}</td><td>${esc(String(m.kd ?? '–').replace(/-/g, '–'))}</td><td>${n(m.rating, 2)}</td></tr>`);
      const wr = x => x && x.played ? `${Math.round(100 * x.won / x.played)}% (${esc(x.played)})` : '–';
      out.push(sec('cs2-hltv', 'HLTV', kv([
        ['Rating 3.0', hl && num(hl.rating) ? `${n(hl.rating, 2)}${num(hl.ct_rating) ? ` <small>CT ${n(hl.ct_rating, 2)} · T ${n(hl.t_rating, 2)}</small>` : ''}` : null],
        ['Round Swing', hl && num(hl.round_swing) ? sgn(hl.round_swing, 2) + '%' : null],
        ['ADR · KAST · K/D', hl && num(hl.adr) ? `${n(hl.adr, 1)} · ${n(hl.kast, 1)}% · ${n(hl.kd, 2)}` : null],
        ['Eco-adjusted K/D · ADR · KAST', hc.eco ? `${n(hc.eco.ekd, 2)} · ${n(hc.eco.eadr, 1)} · ${n(hc.eco.ekast, 1)}%` : null],
        ['Team VRS rank', num(rk?.rank) ? `#${esc(rk.rank)}${rk.change ? ` (${rk.change > 0 ? '▲' : '▼'}${Math.abs(rk.change)} in 4 weeks)` : ''}` : null],
        ['Opponent VRS rank', num(ork?.rank) ? `#${esc(ork.rank)}` : null],
        ['Next match (HLTV)', hc.next_match ? `${esc(when(hc.next_match.time))} vs ${esc(hc.next_match.opponent)} · ${esc(hc.next_match.format || '')}` : null],
        ['Data up to', hl?.newest_map ? esc(hl.newest_map) : null]])
        + (hl?.window ? note(`Last ${esc(hl.window)} · ${esc(hl.maps)} maps.`) : '')
        + (hc.h2h ? `<p class="h-fx-lab">Team vs opponent · last 12 months: ${esc(hc.h2h.wins)}–${esc(hc.h2h.losses)} in series</p>` + table(['Map', 'Team win %', 'Opp win %'], (hc.h2h.map_win_rates || []).map(m => `<tr><td>${esc(m.map)}</td><td>${wr(m.team)}</td><td>${wr(m.opponent)}</td></tr>`)) : '')
        + (last.length ? `<p class="h-fx-lab">Last ${last.length} HLTV maps</p>` + table(['Date', 'Map', 'vs', 'Score', 'K–D', 'Rating'], last) : '')));
    }
    const mates = [...(c?.teammates || []), ...(c?.me?.n ? [{...c.me, me: true}] : [])].filter(m => m.n);
    if (mates.length > 1) {
      mates.sort((a, b) => (b.r || 0) - (a.r || 0));
      const maxR = Math.max(...mates.map(m => m.r || 0), 1.2);
      out.push(sec('cs2-mates', `Teammate ratings${num(c.team_kill_share) ? ` · ${pct(c.team_kill_share)} of team kills` : ''}`,
        `<ul class="h-fx-bars">${mates.map(m => `<li class="${m.me ? 'is-me' : ''}"><span>${esc(m.n)}</span><i><b style="width:${Math.round((m.r || 0) / maxR * 100)}%"></b></i><em>${n(m.r, 2)}</em></li>`).join('')}</ul>` + note('Active lineup only.')));
    }
    const awp = awpReading(ck.awp_note);
    const checks = kv([['Roster', ck.roster_confirmed ? `Confirmed${ck.standin_for ? ' · stand-in' : ''}` : ck.standin_for ? `Stand-in for ${esc(ck.standin_for)}` : null],
      ['AWP use', awp ? esc(awp) : null], ['Trade kills', num(ck.trade_pr) ? n(ck.trade_pr, 3) + '/round' : null],
      ['Opening kills', num(ck.opening?.kills_per_round) ? `${n(ck.opening.kills_per_round, 3)}/round · ${pc0(ck.opening.duel_win)} duels won` : null],
      ['Sample', (ck.sample || []).filter(f => !HEDGE.test(f)).length ? esc(ck.sample.filter(f => !HEDGE.test(f)).join('; ')) : null],
      ['Days since his last map', num(ck.days_since_last_map) ? esc(ck.days_since_last_map) : null]]);
    if (checks) out.push(sec('cs2-checks', 'Checks', checks));
    const ca = c?.csapi;
    if (ca) out.push(sec('cs2-csapi', 'Second source check', kv([['Team (CS API)', `${esc(ca.team || '–')}${ca.vrs_rank ? ` · VRS #${esc(ca.vrs_rank)}` : ''}`],
      ['On that roster', ca.on_roster === true ? 'Yes' : ca.on_roster === false ? 'No' : null], ['Kills / map', `${n(ca.kills_per_map, 1)} · rating ${n(ca.rating, 2)}`],
      ['Round Swing', num(ca.swing) ? sgn(ca.swing, 2) + '%' : null], ['ADR · KAST', `${n(ca.adr, 1)} · ${n(ca.kast, 1)}%`],
      ['CT · T kills/map', `${n(ca.ct?.k, 1)} · ${n(ca.t?.k, 1)}`], ['Data up to', ca.newest_match ? esc(ca.newest_match) : null]]) + note(`All maps · last ${esc(ca.window || '')} · ${esc(ca.maps ?? '')} maps.`)));
    const fc = c?.faceit;
    if (fc) out.push(sec('cs2-faceit', 'FACEIT form', kv([['Account', `${esc(fc.nickname)} · lvl ${esc(fc.level)} · ${esc(fc.elo)} elo`],
      [`Last ${esc(fc.recent_n)} games`, `${esc(fc.avg_kills)} ${stat === 'headshots' ? 'kills' : 'kills'} · KPR ${esc(fc.kpr)}`], ['HS%', fc.avg_hs_pct != null ? esc(fc.avg_hs_pct) + '%' : null]]) + note('FACEIT games never count in the grade.')));
    return out;
  }

  /* ---------------------------------------------------------------- LoL: the classic card's panels (web/lol.html card + factorsHtml) */
  function lol(r, doc) {
    const L = doc?.lol_card;
    if (!L) return [];
    const out = [], tw = L.team_win || {}, m = tw.market || {}, F = L.factors;
    const pw = v => num(v) ? (v * 100).toFixed(1) + '%' : null;
    const win = kv([['Team win chance · model (Elo)', pw(tw.elo) ? `<b>${pw(tw.elo)}</b>` : null],
      ['Team win chance · market', m.found && num(m.p) ? `<b>${pw(m.p)}</b> <small>Polymarket${m.liquidity != null ? ' · $' + Number(m.liquidity).toLocaleString() + ' liquidity' : ''}</small>` : null]]);
    if (F && Array.isArray(F.factors)) {
      const inG = F.model === 'lol-1f';
      const rows = F.factors.filter(x => x.key !== 'no_history' && !/^lg_|_missing$/.test(x.key || ''));
      const eff = x => x.effect_pct == null || !inG ? '<span class="h-fx-w0">Weight 0</span>' : `<b class="${x.effect_pct >= 0 ? 'h-fx-up' : 'h-fx-dn'}">${x.effect_pct >= 0 ? '+' : '−'}${Math.abs(x.effect_pct).toFixed(1)}%</b>`;
      const tr = x => `<tr><td>${esc(x.factor)}</td><td>${x.value == null ? '–' : (+x.value).toFixed(2)}</td><td>${eff(x)}</td></tr>`;
      const head = ['Factor', 'Value', 'Effect'];
      out.push(sec('lol-factors', 'Every factor',
        note(inG && num(F.multiplier) ? `In this grade: the projection is ${F.multiplier >= 1 ? 'raised' : 'lowered'} ${Math.abs((F.multiplier - 1) * 100).toFixed(1)}% by these factors together.` : 'Measured for this stat at weight 0: the test against results did not pass, so they do not move this grade.')
        + table(head, rows.slice(0, 8).map(tr)) + (rows.length > 8 ? `<details class="h-fx-more"><summary>All ${rows.length} factors</summary>${table(head, rows.slice(8).map(tr))}</details>` : '')
        + note(`Values are this player’s numbers relative to his role (log ratios; 0 = average).${(F.not_pre_match || []).length ? ` Not in any grade (decided after props close, or no data source yet): ${esc(F.not_pre_match.join('; '))}.` : ''}`), true));
    }
    if (win) out.push(sec('lol-win', 'Team win chance', win + note('The model’s win chance is in the projection. The market’s is saved with every forecast and is tested against results before it can count.'), true));
    const per = L.recent_per_game, games = {'game 1 only': 1, 'games 1 and 2 added': 2, 'games 1, 2 and 3 added': 3}[L.scope_games];
    out.push(sec('lol-model', 'The model', kv([['Projection (expected total)', num(r.scout.projection) ? n(r.scout.projection, 2) : null],
      ['Median · 80% range', L.median != null ? `${esc(L.median)} · ${(L.range80 || []).length ? L.range80.map(esc).join('–') : '–'}` : null],
      ['Recent average (not the projection)', num(per) ? `${n(per, 2)} a game over the last ${esc(L.recent_games)} games${games ? ` · ${n(per * games, 1)} over ${games} game${games > 1 ? 's' : ''}` : ''}` : null],
      ['Maps counted', L.scope_games ? esc(L.scope_games) : null], ['Line type', L.line_type_note ? esc(L.line_type_note) : null]])
      + ((L.drivers || []).length ? `<p class="h-fx-lab">What this projection uses</p><ul class="h-fx-ul">${L.drivers.map(d => `<li>${esc(d)}</li>`).join('')}</ul>` : '')
      + ((L.not_modelled || []).length ? `<p class="h-fx-lab">Not in this projection</p><p class="h-history-note">${esc(L.not_modelled.join('; '))}</p>` : '')));
    const t = L.times || {};
    out.push(sec('lol-times', 'Times', kv([['Line posted at this number', t.line_seen ? esc(when(t.line_seen)) : null], ['Line last checked', t.line_checked ? esc(when(t.line_checked)) : null],
      ['Forecast saved', t.forecast_at ? esc(when(t.forecast_at)) : null]])));
    return out;
  }

  /* ---------------------------------------------------------------- NBA / NFL: the classic player page (web/sports/app.js renderPlayer) */
  const median = xs => {const s = xs.slice().sort((a, b) => a - b), m = Math.floor(s.length / 2);return s.length ? (s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2) : null;};
  const sd = xs => {if (xs.length < 2) return null;const mu = xs.reduce((a, b) => a + b, 0) / xs.length;return Math.sqrt(xs.reduce((a, b) => a + (b - mu) * (b - mu), 0) / (xs.length - 1));};
  const outcome = (v, ln) => v > ln ? 'over' : v < ln ? 'under' : 'push';
  /* web/sports/stats.js windowSummary: the first n PLAYED games with a value (n null = all given) */
  function summary(games, ln, nn) {
    const played = games.filter(g => g.pl && num(g.v)), w = nn ? played.slice(0, nn) : played, vals = w.map(g => g.v);
    const o = {n: vals.length, mean: null, median: null, sd: null, min: null, max: null, over: 0, under: 0, push: 0, from: w.length ? w[w.length - 1].d : null, to: w.length ? w[0].d : null};
    if (!vals.length) return o;
    o.mean = vals.reduce((a, b) => a + b, 0) / vals.length;o.median = median(vals);o.sd = sd(vals);o.min = Math.min(...vals);o.max = Math.max(...vals);
    if (num(ln)) vals.forEach(v => {o[outcome(v, ln)] += 1;});
    return o;
  }
  /* web/sports/stats.js splits over the last 82 played games */
  function splits(games, ln, opp) {
    const g = games.filter(x => x.pl).slice(0, 82), rows = [];
    const add = (label, gs) => {const s = summary(gs, ln, null);if (s.n > 0) rows.push({label, ...s});};
    add('Home', g.filter(x => x.h === true));add('Away', g.filter(x => x.h === false));
    if (opp) add('vs ' + opp, g.filter(x => x.o === opp));
    add('Regular season', g.filter(x => x.t === 'regular'));add('Postseason', g.filter(x => x.t === 'postseason'));
    return rows;
  }
  const REASONS = {market_only_baseline: 'Market-only: the chance comes from Kalshi prices for this player and stat, not from his own statistics.',
    kalshi_quote_time_unknown: 'Kalshi does not timestamp its quotes; their age is measured from when Scout observed them.',
    between_rungs: 'The line sits between two Kalshi rungs; the chance blends the reading from both.', same_line: 'Kalshi lists this exact line.',
    one_sided_shift: 'Read from the nearest Kalshi rung on one side of the line, moved to this line with the stat’s measured spread.',
    line_gap_beyond_fitted_range: 'The nearest Kalshi rung is far from this line, so the reading leans on the fitted shape.',
    statistical_only: 'Built from the player’s own game statistics; book prices are not an input.',
    deterministic_quadrature: 'Over, Under and Push are read exactly from the projected distribution at this line.'};
  const DVP = {dvp: 1, dvp_rank: 1, dvp_allowed_pg: 1, opp_pos_allowed: 1, opp_pos_rank: 1, opp_pos_allowed_pg: 1, opp_def_rank: 1};
  function sports(r, doc) {
    const S = doc?.sports_card;
    if (!S) return [];
    const out = [], ln = Number(r.offer.line), stat = esc(r.market.stat_label), games = S.games || [], opp = S.next_opp || r.player.opponent || '';
    const fc = S.forecast, det = S.detail;
    /* every factor: the forecast's own inputs (used) and the matchup engine's values (weight 0) */
    const eng = (S.engine?.rows || []).filter(x => x.label && x.value != null && !(typeof x.value === 'string' && HEDGE.test(x.value)));
    const val = v => typeof v === 'boolean' ? (v ? 'Yes' : 'No') : num(v) ? n(v, Math.abs(v) < 10 ? 2 : 1) : esc(String(v));
    const fRows = [...(fc?.inputs || []).filter(x => x.label && x.value != null).map(x => [esc(x.label), val(x.value), true]),
      ...eng.map(x => [`${esc(x.label)}${x.n ? ` <small>n=${esc(x.n)}</small>` : ''}`, val(x.value), 0])];
    const part = games.filter(g => g.pl && num(g.p)), pAvg = k => {const w = part.slice(0, k);return w.length ? w.reduce((a, g) => a + g.p, 0) / w.length : null;};
    if (part.length >= 3) fRows.push([`${esc(S.part_label)} per game <small>last 3 · last 10</small>`, `${n(pAvg(3), 1)} · ${n(pAvg(10), 1)}`, 0]);
    const venue = S.next_home === true ? 'Home' : S.next_home === false ? 'Away' : null;
    if (venue) fRows.push(['Venue', venue + (opp ? ' vs ' + esc(opp) : ''), 0]);
    out.push(sec('sp-factors', 'Every factor', factorTable(fRows, true) + note('Used = an input this forecast was computed from. Weight 0 = shown for context: a factor moves grades only after a chronological test.'), true));
    if (fc) {
      let h = kv([['Projected', num(fc.mean) ? `<b>${n(fc.mean, 1)}</b>` : null], ['80% range', Array.isArray(fc.interval) ? `${esc(fc.interval[0])}–${esc(fc.interval[1])}${fc.median != null ? ` · median ${esc(fc.median)}` : ''}` : null],
        ['Projected at', fc.at ? esc(when(fc.at)) : null]]);
      const bins = (det?.bins || []).filter(b => b[2] > 0.0005);
      if (bins.length) {
        const mx = Math.max(...bins.map(b => b[2]));
        h += `<p class="h-fx-lab">Outcome distribution</p><div class="h-fx-dist" role="img" aria-label="Chance of each ${stat} total">${bins.map(b => {const mid = b[0] + (b[1] - 1) / 2;return `<i class="${mid > ln ? 'is-over' : mid < ln ? 'is-under' : 'is-push'}" style="height:${Math.max(2, Math.round(b[2] / mx * 100))}%" title="${esc(b[0] + (b[1] > 1 ? '–' + (b[0] + b[1] - 1) : ''))}: ${pct(b[2])}"></i>`;}).join('')}</div>`
          + `<details class="h-fx-more"><summary>Distribution as a table</summary>${table(['Value', 'Chance'], bins.filter(b => b[2] >= 0.005).map(b => `<tr><td>${esc(b[0])}${b[1] > 1 ? '–' + esc(b[0] + b[1] - 1) : ''}</td><td>${pct(b[2])}</td></tr>`))}</details>`;
      }
      if ((det?.ladder || []).length) h += `<p class="h-fx-lab">At another line</p>` + table(['Line', 'Over', 'Under', 'Push'], det.ladder.map(x => `<tr class="${x.line === ln ? 'is-selected' : ''}"><td>${esc(x.line)}${x.stored ? ' <small>this forecast</small>' : ''}</td><td>${pct(x.p_over)}</td><td>${pct(x.p_under)}</td><td>${pct(x.p_push)}</td></tr>`)) + note('Every line is read from this forecast’s one distribution. Other books may not offer these lines.');
      if ((fc.inputs || []).length) h += `<p class="h-fx-lab">Inputs the model used</p>` + table(['Input', 'Value'], fc.inputs.map(x => `<tr><td>${esc(x.label)}${x.definition ? `<small class="h-fx-def">${esc(x.definition)}</small>` : ''}</td><td>${x.value == null ? '–' : esc(String(x.value))}</td></tr>`));
      const why = (fc.reasons || []).filter(c => !/^gp_band:/.test(c) && !/^gp:/.test(c)).map(c => REASONS[c] || c).filter(t => !HEDGE.test(t));
      if (why.length) h += `<p class="h-fx-lab">Why this number</p><ul class="h-fx-ul">${why.map(t => `<li>${esc(t)}</li>`).join('')}</ul>`;
      if ((fc.contributions || []).length) h += table(['Computed contribution', 'Effect'], fc.contributions.map(c => `<tr><td>${esc(c.label)}</td><td>${n(c.value, 2)}</td></tr>`));
      out.push(sec('sp-forecast', 'Scout projection', h, true));
    }
    if (games.length) {
      const seasons = games.filter(g => g.pl), season = seasons.length ? seasons[0].s : null, sg = games.filter(g => g.s === season);
      const w = [[5, summary(games, ln, 5)], [10, summary(games, ln, 10)], [20, summary(games, ln, 20)], ['Season', summary(sg, ln, null)]].filter(([, s]) => s.n);
      const dnp = games.slice(0, 20).filter(g => !g.pl).length;
      let h = table(['Window', 'Games', 'Mean', 'Median', 'Spread', `At ${esc(r.offer.line)}`], w.map(([k, s]) => `<tr><td>${k === 'Season' ? 'Season' : 'L' + k}</td><td>${s.n}</td><td>${n(s.mean, 1)}</td><td>${n(s.median, 1)}</td><td>sd ${n(s.sd, 1)} · ${n(s.min, 0)}–${n(s.max, 0)}</td><td>Over ${s.over} · Under ${s.under}${s.push ? ` · Push ${s.push}` : ''}</td></tr>`));
      if (dnp) h += note(`Did not play in ${dnp} of his last 20 team games (not counted).`);
      const sp = splits(games, ln, opp);
      if (sp.length) h += `<p class="h-fx-lab">Splits (last ${Math.min(82, games.filter(g => g.pl).length)} games held)</p>` + table(['Split', 'Games', 'Mean', 'Median', 'Over', 'Under', 'Push'], sp.map(x => `<tr><td>${esc(x.label)}</td><td>${x.n}</td><td>${n(x.mean, 1)}</td><td>${n(x.median, 1)}</td><td>${x.over}</td><td>${x.under}</td><td>${x.push}</td></tr>`)) + note('Descriptive only: splits move the projection only after a chronological test.');
      out.push(sec('sp-splits', `History and splits · ${r.market.stat_label}`, h, true));
    }
    /* matchup: opponent defence vs position (the factor engine's rows) and his own games vs tonight's opponent */
    const dv = eng.filter(x => DVP[x.key]);
    const vs = games.filter(g => g.pl && num(g.v) && opp && g.o === opp), sm = vs.length ? summary(vs, ln, null) : null, dec = sm ? sm.over + sm.under : 0;
    const mu = (dv.length ? `<p class="h-fx-lab">${esc(opp || 'Opponent')} defense vs position</p><ul class="h-fx-ul">${dv.map(x => `<li>${esc(x.label)}: <b>${x.key === 'dvp' || x.key === 'opp_pos_allowed' ? n(x.value, 2) + '× the league average allowed' : /rank/.test(x.key) ? '#' + n(x.value, 0) : n(x.value, 1) + ' per game'}</b>${x.n ? ` <small>n=${esc(x.n)}</small>` : ''}</li>`).join('')}</ul>` : '')
      + (sm ? `<p class="h-fx-lab">${esc(r.player.name)} vs ${esc(opp)}</p>` + kv([['Games', `${sm.n} <small>${esc(sm.from || '')} – ${esc(sm.to || '')}</small>`], ['Average', `${n(sm.mean, 1)} ${stat.toLowerCase()} · median ${n(sm.median, 1)}`],
        [`At ${esc(r.offer.line)}`, `Over ${sm.over} · Under ${sm.under}${sm.push ? ` · Push ${sm.push}` : ''}${dec ? ` · Over hit rate ${pc0(sm.over / dec)} <small>(n = ${dec})</small>` : ''}`]]) : '');
    if (mu) out.push(sec('sp-matchup', `Matchup · ${r.market.stat_label}`, mu + note('Descriptive: matchup numbers move the Scout projection only after a chronological test.')));
    /* minutes / opportunity trend and the opportunity averages by window */
    const tr = part.slice(0, 20).reverse();
    let oh = '';
    if (tr.length >= 2) {
      const mx = Math.max(...tr.map(g => g.p), 1);
      oh += `<div class="h-fx-spark" role="img" aria-label="${esc(S.part_label)} in his last ${tr.length} games">${tr.map(g => `<i style="height:${Math.max(4, Math.round(g.p / mx * 100))}%" title="${esc(g.d)}: ${esc(g.p)}"></i>`).join('')}</div>` + note(`${esc(S.part_label)}, last ${tr.length} games (oldest left). Last 3: ${n(pAvg(3), 1)} · last 10: ${n(pAvg(10), 1)}.`);
    }
    const stats = (S.stats || []).filter(x => (x.windows || []).length);
    if (stats.length) oh += table(['Per game', ...stats[0].windows.map(w => esc(w.window))], stats.map(x => `<tr><td>${esc(x.label)}</td>${x.windows.map(w => `<td>${n(w.mean, 1)} <small>n=${esc(w.n)}</small></td>`).join('')}</tr>`));
    if (oh) out.push(sec('sp-usage', `${S.part_label} and role`, oh));
    if (eng.length) {
      const groups = new Map();eng.forEach(x => {const g = x.group || 'Context';if (!groups.has(g)) groups.set(g, []);groups.get(g).push(x);});
      out.push(sec('sp-engine', `${S.engine.label || 'Matchup and role context'} · ${eng.length} values`, `<div class="h-scroll-x"><table class="h-fx-table"><thead><tr><th>Factor</th><th>Value</th><th>Source</th></tr></thead><tbody>${[...groups].map(([g, xs]) => `<tr class="h-fx-group"><th colspan="3">${esc(g)}</th></tr>` + xs.map(x => `<tr><td>${esc(x.label)}${x.n ? ` <small>n=${esc(x.n)}</small>` : ''}</td><td>${val(x.value)}</td><td><small>${esc(x.source || '')}</small></td></tr>`).join('')).join('')}</tbody></table></div>`
        + note(`Values only, no weights${S.engine.as_of ? `. Only games finished and lines observed before ${esc(when(S.engine.as_of))} are used` : ''}.`)));
    }
    return out;
  }

  /* ---------------------------------------------------------------- every book's odds for this prop (owner 2026-11-09) */
  const amer = d => {if (!num(d) || d <= 1) return null;const a = d >= 2 ? Math.round((d - 1) * 100) : -Math.round(100 / (d - 1));return (a > 0 ? '+' : '−') + Math.abs(a);};
  function priceText(d, pref) {
    if (!num(d) || d <= 1) return null;
    const a = amer(d), dd = d.toFixed(2);
    return pref === 'decimal' ? `<b>${dd}</b> <small>${a}</small>` : `<b>${a}</b> <small>${dd}</small>`;
  }
  /* one side of a quote, as the site reads it: a sportsbook's decimal price (American beside it), an exchange's bid / ask, a pick'em
     app's payout modifier, or the app's standard payout */
  function sideCell(o, kind, pref) {
    if (!o) return {html: null};
    if (o.offered === false) return {html: null};
    if (num(o.dec)) return {html: priceText(o.dec, pref), dec: o.dec};
    if (o.bidask) return {html: esc(o.bidask)};
    if (num(o.mult) && o.mult !== 1) return {html: `<b>${o.mult.toFixed(2)}×</b> <small>payout</small>`, mult: o.mult};
    return {html: kind === 'pickem' ? 'Standard' : 'Listed'};
  }
  function oddsRows(r, doc, ctx) {
    const rows = [], seen = new Set(), base = p => String(p || '').split(':').pop().toLowerCase();
    const add = x => {const k = base(x.platform) + '|' + x.line + '|' + (x.type || '');if (seen.has(k)) return;seen.add(k);rows.push(x);};
    const P = ctx.platforms || {};
    for (const b of doc?.books || []) {           // the board's own rows of this prop: every app and line, alternates included
      const pl = P[b.platform] || {}, kind = pl.kind === 'decimal' ? 'book' : 'pickem';
      const type = ctx.lineKind ? ctx.lineKind(b.platform, b.line_type) : (b.line_type && b.line_type !== 'standard' ? b.line_type : '');
      /* the hub's own Lines-tab reading (linesHtml quoteText): decimal books' payout is the price; an app's own price unit is never odds */
      const side = s => !s ? null : pl.price_comparable === false ? {offered: s.offered, text: num(s.price) ? `${pl.price_label || 'App price'} ${s.price.toFixed(2)}` : 'Listed'}
        : {offered: s.offered, dec: kind === 'book' ? (num(s.payout) && s.payout > 1 ? s.payout : null) : num(s.price) && s.price > 1 ? s.price : null, mult: kind === 'pickem' ? s.payout : null};
      add({platform: b.platform, label: b.platform_label, line: b.line, type: type || (b.line_type && b.line_type !== 'standard' ? b.line_type : ''), kind, over: side(b.over), under: side(b.under), seen: b.seen_at, mine: b.row_id === r.offer.offer_id});
    }
    for (const q of doc?.sports_card?.quotes || []) {   // NBA / NFL: every book quote the player file holds for this market
      const side = s => !s || !Object.keys(s).length ? null : {offered: s.offered !== false, dec: s.price_format === 'decimal' && num(s.price) ? s.price : null,
        bidask: s.bid != null || s.ask != null ? `${s.bid ?? '–'} / ${s.ask ?? '–'}` : null, mult: num(s.multiplier) ? s.multiplier : null};
      const v = q.variant && !['standard', 'balanced', 'main', 'ladder_rung'].includes(q.variant) ? String(q.variant).replace(/_/g, ' ') : '';
      add({platform: q.platform, label: q.label || (ctx.bookLabel ? ctx.bookLabel(q.platform) : q.platform), line: q.line, type: v, kind: q.over?.price_format === 'decimal' || q.under?.price_format === 'decimal' ? 'book' : 'pickem',
           over: side(q.over), under: side(q.under), seen: q.seen_at});
    }
    const mb = doc?.market_books;                 // sportsbooks and exchanges at this line and others
    for (const b of [...(mb?.same || []), ...(mb?.other || [])]) {
      const one = v => v == null ? null : typeof v === 'number' ? {offered: true, dec: v} : {offered: true, bidask: String(v)};
      add({platform: b.platform, label: b.platform_label, line: b.line, type: b.kind === 'mid' ? 'exchange' : '', kind: 'book', over: one(b.over), under: one(b.under), seen: b.seen_at});
    }
    for (const [label, line, bk, o, u] of ctx.extra || []) add({platform: bk, label, line, type: '', kind: 'pickem', over: o == null ? null : {offered: true, text: o || 'Listed'}, under: u == null ? null : {offered: true, text: u || 'Listed'}, seen: null});
    return rows.filter(x => x.line != null && (x.over?.offered || x.under?.offered));
  }
  /* best per side: the Over's lowest line, the Under's highest; at the same line the higher price (sportsbooks) or payout (apps) */
  function bestOf(rows, sd) {
    const val = x => x[sd]?.dec || x[sd]?.mult || 1;
    let best = null;
    for (const x of rows) {
      if (!x[sd]?.offered) continue;
      if (!best) {best = x;continue;}
      const l = Number(x.line), bl = Number(best.line);
      if (sd === 'over' ? l < bl : l > bl) best = x;
      else if (l === bl && val(x) > val(best)) best = x;
    }
    return best;
  }
  function oddsHtml(r, doc, ctx) {
    const rows = oddsRows(r, doc, ctx).sort((a, b) => Number(a.line) - Number(b.line) || String(a.label).localeCompare(String(b.label)));
    if (!rows.length) return '';
    const pref = ctx.oddsPref === 'decimal' ? 'decimal' : 'american';
    const bo = bestOf(rows, 'over'), bu = bestOf(rows, 'under');
    const cell = (x, sd, best) => {
      const s = x[sd];
      const c = !s || s.offered === false ? {html: null} : s.text ? {html: esc(s.text)} : sideCell(s, x.kind, pref);
      if (!c.html) return '<td class="h-odds-na"></td>';
      return `<td class="${best ? 'is-best' : ''}">${c.html}${best ? ' <span class="h-odds-best">Best</span>' : ''}</td>`;
    };
    const line = x => x.line === r.offer.line ? `<b>${esc(x.line)}</b>` : esc(x.line);
    const kinds = rows.filter(x => x.kind === 'book').length, apps = rows.length - kinds;
    const ka = (doc?.sports_card?.kalshi || []);
    return `<section class="h-odds" aria-labelledby="hub-odds-h"><div class="h-sec-head"><h4 id="hub-odds-h">All odds for this prop</h4><small>${rows.length} ${rows.length === 1 ? 'line' : 'lines'}${apps ? ` · ${apps} pick’em` : ''}${kinds ? ` · ${kinds} sportsbook` : ''}</small></div>
      <div class="h-scroll-x"><table class="h-odds-table"><thead><tr><th>Book</th><th>Line</th><th>Over</th><th>Under</th><th class="h-odds-seen">Seen</th></tr></thead><tbody>${rows.map(x => `<tr class="${x.mine ? 'is-selected' : ''}"><th scope="row">${esc(x.label || x.platform)}${x.type ? ` <small>${esc(x.type)}</small>` : ''}${x.seen ? `<small class="h-odds-seen-m">Seen ${esc(ctx.age ? ctx.age(x.seen) : when(x.seen))}</small>` : ''}</th><td>${line(x)}</td>${cell(x, 'over', x === bo)}${cell(x, 'under', x === bu)}<td class="h-odds-seen">${x.seen ? `<time datetime="${esc(x.seen)}" title="${esc(when(x.seen))}">${esc(ctx.age ? ctx.age(x.seen) : when(x.seen))}</time>` : ''}</td></tr>`).join('')}</tbody></table></div>
      <p class="h-history-note">Best = the Over’s lowest line and the Under’s highest; at the same line, the better price or payout. Sportsbook prices in ${pref === 'decimal' ? 'decimal (American beside it)' : 'American (decimal beside it)'}; pick’em apps show their payout modifier. Odds are shown for comparison: they never move Scout’s grade.</p>
      ${ka.length ? `<details class="h-fx-more"><summary>Kalshi ladder · ${ka.length} ${ka.length === 1 ? 'rung' : 'rungs'}</summary>${table(['Rung (Over)', 'YES bid', 'YES ask', 'Volume', 'Open interest', 'Seen'], ka.map(k => `<tr><td>${esc(k.line)}</td><td>${k.bid != null ? Math.round(k.bid * 100) + '¢' : '–'}</td><td>${k.ask != null ? Math.round(k.ask * 100) + '¢' : '–'}</td><td>${k.volume != null ? esc(Math.round(k.volume)) : '–'}</td><td>${k.oi != null ? esc(Math.round(k.oi)) : '–'}</td><td>${k.seen_at ? esc(ctx.age ? ctx.age(k.seen_at) : when(k.seen_at)) : ''}</td></tr>`))}</details>` : ''}</section>`;
  }

  return {
    factors(r, doc) {
      const list = r.sport === 'cs2' ? cs2(r, doc) : r.sport === 'lol' ? lol(r, doc) : sports(r, doc);
      const body = list.filter(Boolean).join('');
      return body ? `<section class="h-fx-wrap" aria-labelledby="hub-fx-h"><div class="h-sec-head"><h4 id="hub-fx-h">Factors and matchup</h4><small>tap a section to open or close</small></div>${body}</section>` : '';
    },
    odds: oddsHtml,
    /* the hub's own facts list reads these (Why: "Also good to know") */
    cs2Facts(r, doc) {
      const c = doc?.cs2_card, out = [];
      const rb = c?.rank_band;
      if (rb && rb.maps) out.push({used: false, html: `Vs teams ranked like ${esc(r.player.opponent || 'the opponent')} (#${esc(rb.opp_rank)}): <b>${sgn((rb.ratio - 1) * 100, 0)}%</b> kills per round <small>(${esc(rb.maps)} maps)</small>`});
      /* tonight's LAN / online rate feeds the projection: it is in the Factors table (Used), not in this list of context facts */
      return out;
    },
  };
}
