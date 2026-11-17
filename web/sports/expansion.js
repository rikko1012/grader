/* Separate source-native boards. No link to existing forecast or slip pools. */
(()=>{'use strict';
const $=id=>document.getElementById(id), names={prizepicks:'PrizePicks',underdog:'Underdog'}, periods={full_game:'Full match',set1:'First set',first_half:'First half',second_half:'Second half'};
let sport='soccer', rows=[], generation=0, token=null, base='', busy=false;
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
function options(id,values){const s=$(id),old=s.value;s.replaceChildren(new Option(id==='market'?'All markets':'All periods',''));for(const [key,label]of values)s.add(new Option(label,key));s.value=[...s.options].some(o=>o.value===old)?old:'';}
function render(){
 const now=Date.now(), q=$('search').value.trim().toLowerCase(),m=$('market').value,p=$('period').value,b=$('book').value,side=$('side').value;
 const filtered=rows.filter(r=>Date.parse(r.start)>now&&now-Date.parse(r.observed_at)<=300000&&(!m||r.stat===m)&&(!p||r.scope===p)&&(!b||r.platform===b)&&(!side||r.sides[side])&&`${r.player} ${r.team||''} ${r.match}`.toLowerCase().includes(q));
 const groups=new Map();for(const r of filtered){const key=r.player_id+'|'+r.event_id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r);}
 const box=$('players');box.replaceChildren();if(!groups.size){$('status').textContent='0 current lines match your filters.';box.append(el('div','No current lines match these filters. Try another market, app, or sport.','empty'));return;}
 for(const list of groups.values()){
  list.sort((a,b)=>a.label.localeCompare(b.label)||(Number(b.scope==='full_game')-Number(a.scope==='full_game'))||a.scope.localeCompare(b.scope)||a.line-b.line);
  const r=list[0], card=el('article',undefined,'player'), intro=el('div',undefined,'intro');intro.append(el('h2',r.player),el('p',[r.role,r.team].filter(Boolean).join(' · '),'context'),el('p',r.match,'context'),el('p',new Date(r.start).toLocaleString([],{weekday:'short',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}),'context'));card.append(intro);
  const quote=el('div',undefined,'quote'), select=el('select');select.setAttribute('aria-label',`${r.player}: market and period`);list.forEach((v,i)=>select.add(new Option(`${v.label} · ${periods[v.scope]||v.scope} · ${v.line} · ${names[v.platform]}`,i)));
  const detail=el('div');function show(){const v=list[Number(select.value)||0];detail.replaceChildren(el('div',periods[v.scope]||v.scope,'scope'),el('div',`${v.line} ${v.label}`,'line'));const sides=el('div',undefined,'sides');for(const [s,value]of Object.entries(v.sides)){sides.append(el('span',`${s==='over'?'Over':'Under'} ${v.line}${value.multiplier===null?'':` · ${value.multiplier}× modifier`}`));}detail.append(sides,el('p',`${names[v.platform]} · checked ${Math.max(0,Math.floor((Date.now()-Date.parse(v.observed_at))/60000))} min ago`,'meta'));if(v.variant!=='standard'&&v.variant!=='balanced')detail.append(el('p',`Line type: ${v.variant}`,'meta'));}
  select.addEventListener('change',show);quote.append(select,detail);show();card.append(quote);box.append(card);
 }
 $('status').textContent=`${groups.size} player/match groups · ${filtered.length} current lines. Each app retains its own player and match identity.`;
}
async function load(){
 const run=++generation;busy=true;$('refresh').disabled=true;$('status').textContent='Checking current lines…';
 try{
  if(!token){const r=await fetch('/api/sports/token',{credentials:'same-origin'});if(!r.ok)throw new Error(r.status===401?'Sign in to Scout to view these boards.':'Sports access is unavailable.');const a=await r.json();token=a.token;base=a.api_base||'';}
  const r=await fetch(`${base}/api/sports/expansion/board?sport=${sport}`,{headers:{Authorization:`Bearer ${token}`}});
  if(!r.ok){if(r.status===401)token=null;throw new Error(r.status===404?'This sport is not released yet.':r.status===503?'Current prices are unavailable. Refresh to check again.':'Could not load lines. Please retry.');}
  const data=await r.json();if(run!==generation)return;rows=data.offers||[];options('market',[...new Map(rows.map(r=>[r.stat,r.label]))].sort((a,b)=>a[1].localeCompare(b[1])));options('period',[...new Set(rows.map(r=>r.scope))].map(k=>[k,periods[k]||k]));render();
 }catch(e){if(run===generation){rows=[];$('players').replaceChildren();$('status').textContent=e.message;}}
 finally{if(run===generation){busy=false;$('refresh').disabled=false;}}
}
document.querySelectorAll('[data-sport]').forEach(b=>b.addEventListener('click',()=>{sport=b.dataset.sport;$('sport-title').textContent=sport==='soccer'?'Soccer':'Tennis';rows=[];$('players').replaceChildren();document.querySelectorAll('[data-sport]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));load();}));
['search','market','period','book','side'].forEach(id=>$(id).addEventListener('input',render));$('refresh').addEventListener('click',load);
setInterval(()=>{if(!document.hidden&&!busy)load();},180000);setInterval(()=>{if(!document.hidden&&rows.some(r=>Date.parse(r.start)<=Date.now()||Date.now()-Date.parse(r.observed_at)>300000))render();},30000);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy)load();});load();
})();
