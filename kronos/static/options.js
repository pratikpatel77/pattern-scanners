'use strict';
if(window.parent!==window)document.body.classList.add('embedded');
const $=id=>document.getElementById(id);
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=x=>x==null?'—':new Intl.NumberFormat('en-IN',{style:'currency',currency:'INR',maximumFractionDigits:0}).format(x);
const fmt=x=>x==null?'—':Number(x).toLocaleString('en-IN',{maximumFractionDigits:2});
const time=x=>new Date(x).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',hour12:false});
let token='',state=null,analysis=null,selected=null,active=null,timer=null;
let universe=[],universeLoaded=false,selectedSymbol='',contracts=[],draft=null,planIndex=0,editVersion=0;
async function api(path,body){
  const r=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Kronos-Token':token},body:JSON.stringify(body)});
  const value=await r.json();if(!r.ok)throw new Error(value.error||'Request failed');return value;
}
function message(text){$('message').textContent=text;$('message').hidden=!text;}
async function safely(action){try{await action();}catch(e){message(e.message);}}
const VIEWS=['ideas','scanner','journal','settings'];
function view(name){
  for(const v of VIEWS){$(v+'-view').hidden=v!==name;$(v+'-tab').classList.toggle('selected',v===name);}
  if(name==='journal'&&window.Plotly&&$('equity')?.data)Plotly.Plots.resize($('equity'));
}
for(const v of VIEWS)$(v+'-tab').onclick=()=>view(v);
function marketOpenNow(){
  const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));
  const m=Number(p.hour)*60+Number(p.minute);return !['Sat','Sun'].includes(p.weekday)&&m>=555&&m<930;
}
const pct=x=>x==null||!isFinite(x)?'—':(x>0?'+':'')+Number(x).toFixed(1)+'%';
const signed=x=>x==null?'—':(x>0?'+':'')+money(x);
const tone=x=>x==null?'':x>0?'profit':x<0?'loss':'';
function ago(iso){const m=(Date.now()-new Date(iso))/60000;return m<1?'just now':m<60?Math.round(m)+' min ago':m<1440?Math.round(m/60)+' h ago':Math.round(m/1440)+' d ago';}
function store(key,value){try{if(value===undefined)return localStorage.getItem(key);localStorage.setItem(key,value);}catch(e){return null;}}
function download(name,rows){
  const csv=rows.map(r=>r.map(v=>{const s=String(v??'');return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;}).join(',')).join('\n');
  const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));link.download=name;link.click();
  setTimeout(()=>URL.revokeObjectURL(link.href),1000);
}
function settings(){
  const form=$('settings-form'),values={};
  for(const [key,val] of Object.entries(state.settings)){const el=form.elements[key];if(!el)continue;values[key]=typeof val==='boolean'?el.checked:Number(el.value)/(key==='slippage'?100:1);}
  return values;
}
function busy(value){for(const id of ['analyse','scan-all','scan-patterns','scan-fno'])$(id).disabled=value;$('cancel').hidden=!value;$('scanner-cancel').hidden=!value;if(!value)$('scanner-progress').textContent='';}
async function refresh(){
  state=await api('/api/options');
  $('market').textContent=marketOpenNow()?'Live market hours':'Market closed · EOD review';
  $('login').textContent=state.login_ready?'FYERS credentials found · connection checked on scan':'FYERS login required';
  const s=state.stats;
  $('risk').textContent=money(s.open.risk);
  $('risk-note').textContent=`${s.open.count} open · sum of planned maximum losses`;
  $('pnl').textContent=s.open.count?signed(s.open.unrealised):'—';$('pnl').className=tone(s.open.unrealised);
  $('pnl-note').textContent=s.open.unmarked?`${s.open.unmarked} open trade(s) not yet marked`:'Last fresh marks · not streaming';
  $('realised').textContent=signed(s.closed.realised);$('realised').className=tone(s.closed.realised);
  $('realised-note').textContent=s.closed.count?`${s.closed.count} closed · ${fmt(s.closed.win_rate)}% winners`:'No closed paper trades yet';
  const a=state.accuracy;$('accuracy').textContent=a.pct==null?'Not measured':a.pct+'%';
  $('accuracy-note').textContent=`${a.scored} scored / ${a.recorded} saved forecasts · fixed holding date`;
  for(const [key,val] of Object.entries(state.settings)){const el=$('settings-form').elements[key];if(!el)continue;if(typeof val==='boolean')el.checked=val;else el.value=key==='slippage'?Math.round(val*1e4)/100:val;}
  updateFilters();renderStocks();renderScanner();renderJournal();renderAnalytics();renderAlerts();
  if(state.active&&!active){active=state.active;busy(true);poll();}
}

function patternName(s){return ({cup:'Cup & handle',darvas:'Darvas box',stage:'Stage analysis',cup_bear:'Inverted cup & handle (bearish)',darvas_bear:'Darvas breakdown box (bearish)',stage_bear:'Stage 3 → 4 (bearish)'})[s.scanner]||s.scanner||s.label||'Pattern';}
function updateFilters(){
  const names=[...new Set((state.stock_ideas||[]).flatMap(r=>(r.setups||[]).map(patternName)))].sort();
  const current=$('pattern-filter').value;
  $('pattern-filter').innerHTML='<option value="">All patterns</option>'+names.map(n=>`<option value="${esc(n)}">${esc(n)}</option>`).join('');
  $('pattern-filter').value=names.includes(current)?current:'';
}
function latestAnalysis(symbol){return state.analyses.find(a=>a.symbol===symbol&&a.has_forecast)||state.analyses.find(a=>a.symbol===symbol);}
function renderStocks(){
  const q=$('filter').value.toUpperCase(),scope=$('stock-scope').value,pattern=$('pattern-filter').value,tf=$('timeframe-filter').value;
  const saved=new Map((state.stock_ideas||[]).map(r=>[r.symbol,r]));
  const only=s=>universeLoaded&&universe.includes(s);  // the desk covers F&O stocks only
  let rows=scope==='patterns'?[...saved.values()]:scope==='fno'?universe.map(symbol=>saved.get(symbol)||{symbol,patterns:[],setups:[]}):[...new Set(state.analyses.map(a=>a.symbol))].map(symbol=>saved.get(symbol)||{symbol,patterns:[],setups:[]});
  rows=rows.filter(r=>(r.symbol+' '+r.patterns.join(' ')+' '+(r.setups||[]).map(patternName).join(' ')).toUpperCase().includes(q))
    .filter(r=>!pattern||(r.setups||[]).some(s=>patternName(s)===pattern&&(!tf||s.timeframe===tf)))
    .filter(r=>!tf||(r.setups||[]).some(s=>s.timeframe===tf))
    .filter(r=>only(r.symbol));
  $('stock-count').textContent=rows.length;
  $('stock-list').innerHTML=rows.map(r=>{
    const a=latestAnalysis(r.symbol);
    const tags=[...new Set((r.setups||[]).map(s=>patternName(s)+' · '+s.timeframe))];
    return `<button class="stock ${r.symbol===selectedSymbol?'current':''}" data-stock="${esc(r.symbol)}"><strong>${esc(r.symbol)}</strong><span class="pattern-tags">${(tags.length?tags:r.patterns).map(p=>`<span>${esc(p)}</span>`).join('')||'<span>No pattern</span>'}</span><small>${a?'Saved '+esc(time(a.created)):'Not analysed yet'}</small></button>`;
  }).join('')||`<p class="empty">${universeLoaded?'No F&O stocks match these filters. Clear the pattern or timeframe filter.':'Loading the F&O list…'}</p>`;
  $('stock-list').querySelectorAll('[data-stock]').forEach(b=>b.onclick=()=>safely(()=>selectStock(b.dataset.stock)));
}
$('filter').oninput=renderStocks;
for(const id of ['pattern-filter','timeframe-filter'])$(id).onchange=renderStocks;
$('stock-scope').onchange=()=>safely(async()=>{if($('stock-scope').value==='fno'&&!universeLoaded)await loadUniverse(true);renderStocks();});
let selectionVersion=0;
$('clear-filters').onclick=()=>{$('filter').value='';$('pattern-filter').value='';$('timeframe-filter').value='';renderStocks();};
$('toggle-stocks').onclick=()=>{
  const hidden=document.querySelector('.desk').classList.toggle('focus-mode');
  $('toggle-stocks').textContent=hidden?'Show stock list':'Hide stock list';
  $('toggle-stocks').setAttribute('aria-expanded',String(!hidden));
  if(window.Plotly)for(const id of ['prediction','payoff'])if($(id)?.data)Plotly.Plots.resize($(id));
};
function detailView(name){
  document.querySelectorAll('[data-detail-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.detailView===name)));
  document.querySelectorAll('[data-detail-panel]').forEach(p=>p.hidden=p.dataset.detailPanel!==name);
  if(name==='forecast')renderPrediction();
  if(name==='trade'&&$('payoff')?.data&&$('payoff').offsetParent&&window.Plotly)Plotly.Plots.resize($('payoff'));
}

function patternPanel(symbol){
  const rows=(state.stock_ideas||[]).find(r=>r.symbol===symbol)?.setups||[];
  const unique=[...new Map(rows.map(s=>[[s.scanner,s.timeframe,s.date].join('|'),s])).values()];
  return `<details class="patterns-panel" open><summary>Detected patterns · ${unique.length}</summary>${unique.length?`<div class="table-wrap"><table><thead><tr><th>Pattern</th><th>Timeframe</th><th>Detected</th><th>Breakout / breakdown level</th><th>Stop</th><th>Score</th></tr></thead><tbody>${unique.map(s=>`<tr><td>${esc(patternName(s))}<br><small>${esc(s.label||'')}</small></td><td>${s.timeframe==='W'?'Weekly':'Daily'}</td><td>${esc(s.date||'—')}</td><td>${fmt(s.pivot)}</td><td>${fmt(s.support)}</td><td>${fmt(s.score)}</td></tr>`).join('')}</tbody></table></div><p class="hint">Saved scanner detections, with their original dates. Levels are not live quotes.</p>`:'<p>No saved pattern. Kronos can still analyse this stock.</p>'}</details>`;
}
function syncSymbol(symbol){
  const select=$('symbol');
  if(![...select.options].some(option=>option.value===symbol))select.add(new Option(symbol,symbol));
  select.value=symbol;
}
async function selectStock(symbol){
  const version=++selectionVersion;selectedSymbol=symbol;refreshOptionsPrice();analysis=null;draft=null;contracts=[];editVersion++;message('');renderStocks();
  const saved=latestAnalysis(symbol);
  if(saved){const loaded=await api('/api/options/analysis/'+saved.id);if(version!==selectionVersion)return;analysis=loaded;renderDetail();}
  else $('detail').innerHTML=`<div class="detail-head"><h2>${esc(symbol)} <small id="current-stock-ltp">Fetching LTP…</small></h2><p id="current-stock-time"></p><p id="stock-loading">${active?'Another forecast is running. You can browse patterns while it finishes.':'Preparing stock analysis…'}</p></div>${patternPanel(symbol)}<p class="empty">The prediction chart and available strategies appear after analysis. After market hours, the latest completed EOD candles are used.</p>`;
  refreshOptionsPrice();
  if(!universeLoaded){try{await loadUniverse(true);}catch(e){message(e.message);}}
  if(version!==selectionVersion)return;
  syncSymbol(symbol);
  if(!saved&&!active)await scan(false,symbol);
  else if(!saved&&$('stock-loading'))$('stock-loading').textContent=marketOpenNow()?'Run a fresh forecast when the current job finishes.':'After-hours analysis uses the latest completed daily candles.';
}
function plot(id,traces,layout){
  if(!window.Plotly){$(id).innerHTML='<p class="notice">Chart library could not load. Reload the page. The values remain in the tables.</p>';return;}
  Plotly.newPlot(id,traces,{margin:{t:18,r:20,b:45,l:65},paper_bgcolor:'#fff',plot_bgcolor:'#fff',font:{family:'Segoe UI',color:'#667085',size:11},legend:{orientation:'h',y:1.12},...layout},{responsive:true,displayModeBar:false});
}
function renderPrediction(){
  const f=analysis.forecast;if(!f||!$('prediction'))return;
  const h=(f.history||[]).slice(-45),future=f.forecast||[],anchor=h.length?h[h.length-1]:{date:f.asof,close:f.last_close};
  const x=[anchor.date,...future.map(r=>r.date)];
  const traces=[{name:'Stock close',x:h.map(r=>r.date),y:h.map(r=>r.close),type:'scatter',mode:'lines',line:{color:'#253b55',width:2}}];
  for(const path of (f.paths||[]).slice(0,32))traces.push({x:[anchor.date,...path.map(r=>r.date)],y:[anchor.close,...path.map(r=>r.close)],type:'scatter',mode:'lines',line:{color:'rgba(107,118,182,.12)',width:1},showlegend:false,hoverinfo:'skip'});
  traces.push({name:'10th percentile',x,y:[anchor.close,...future.map(r=>r.q10)],type:'scatter',mode:'lines',line:{width:0},showlegend:false},
    {name:'10–90% sampled close band',x,y:[anchor.close,...future.map(r=>r.q90)],type:'scatter',mode:'lines',line:{width:0},fill:'tonexty',fillcolor:'rgba(81,103,216,.14)'},
    {name:'Kronos median',x,y:[anchor.close,...future.map(r=>r.median)],type:'scatter',mode:'lines+markers',line:{color:'#5369d8',width:3,dash:'dash'}});
  plot('prediction',traces,{xaxis:{type:'date',gridcolor:'#f0f2f6'},yaxis:{title:'Stock price · ₹',gridcolor:'#f0f2f6'},shapes:[{type:'line',x0:anchor.date,x1:anchor.date,y0:0,y1:1,yref:'paper',line:{color:'#94a3b8',dash:'dot'}}]});
}
function eventLine(a){
  const e=a.events||{},next=(e.upcoming||e.events||[]).slice().sort((x,y)=>x.date<y.date?-1:1)[0];
  const manual=' <button id="update-reference" class="linklike">Add manual check</button>';
  if(!e.checked)return `<p class="event-line warn">Upcoming events: calendar unavailable, so none can be ruled out.${manual}</p>`;
  if(!next)return '<p class="event-line">Upcoming events: none scheduled in the next 90 days.</p>';
  const days=next.days_away,inside=next.during_trade!==false;
  return `<p class="event-line ${inside?'warn':''}">Next event: ${esc(next.title)} on ${esc(next.date)}${days!=null?' ('+(days===0?'today':days+' day'+(days===1?'':'s'))+')':''} · ${inside?'⚠ falls inside the trade period':'after the planned exit'}</p>`;
}
function renderDetail(){
  if(!analysis)return;const a=analysis,f=a.forecast,age=(Date.now()-new Date(a.forecast_created||a.created))/60000;
  $('detail').innerHTML=`<div class="detail-head"><div class="section-title"><div><span class="eyebrow">${a.data_mode==='EOD'?'EOD · '+esc(a.data_asof):age>10?'SAVED SNAPSHOT':'ANALYSIS SNAPSHOT'} · ${esc(time(a.created))} IST</span><h2>${esc(a.symbol)} <small id="current-stock-ltp">Fetching LTP…</small></h2><p id="current-stock-time"></p><small>Forecast input: ${money(a.spot)} · ${esc(a.data_asof||a.created)}</small></div></div>${f?`<div class="detail-stats"><span>Kronos view<strong>${esc(f.summary.bias)}</strong></span><span>Path low–high zone<strong>${fmt(a.lower)} – ${fmt(a.upper)}</strong></span><span>ATM IV / IV percentile<strong>${fmt(a.iv)}% / ${fmt(a.ivp?.value)}</strong></span><span>Forecast until<strong>${esc(a.forecast_end)}</strong></span></div>`:''}</div>
  <nav class="detail-nav" aria-label="Stock details"><button data-detail-view="trade" aria-pressed="true">Trade ideas</button><button data-detail-view="forecast" aria-pressed="false">Forecast</button><button data-detail-view="patterns" aria-pressed="false">Patterns</button></nav>
  <div class="plans" data-detail-panel="trade">${eventLine(a)}<div id="plan-area"></div></div>
  <section data-detail-panel="forecast" hidden>${f?`<section class="chart-panel"><h3>Price forecast <span class="badge">${esc(f.summary.sample_count)} sampled paths</span></h3><div id="prediction" role="img" aria-label="Stock history and Kronos prediction chart"></div><p class="hint">Median and sampled range · not a price guarantee or measured win rate.</p></section>`:'<p class="empty">Run Analyse stock to create a forecast.</p>'}</section>
  <section data-detail-panel="patterns" hidden>${patternPanel(a.symbol)}</section>`;
  document.querySelectorAll('[data-detail-view]').forEach(b=>b.onclick=()=>detailView(b.dataset.detailView));
  refreshOptionsPrice();
  if($('update-reference'))$('update-reference').onclick=()=>{const form=$('reference-form');form.elements.symbol.value=a.symbol;form.elements.source.value='https://web.sensibull.com/option-chain?tradingsymbol='+encodeURIComponent(a.symbol);form.elements.event_source.value='https://www.nseindia.com/companies-listing/corporate-filings-board-meetings';view('settings');};
  if(!a.candidates?.length){$('plan-area').innerHTML='<div class="notice"><strong>No options structure available.</strong><p>'+esc(a.reason||'Run a fresh analysis to load the option chain.')+'</p></div>';return;}
  const ideas=topIdeas(a.candidates);
  if(!ideas.length){selected=null;draft=null;$('plan-area').innerHTML='<div class="notice"><strong>No option structure found</strong><p>No liquid, bounded-loss structure could be built from this chain.</p><ul>'+[...new Set(a.candidates.flatMap(c=>c.reasons||[]))].filter(r=>!r.startsWith('Chain preview:')).map(r=>'<li>'+esc(r)+'</li>').join('')+'</ul><p>Liquidity limits are adjustable under Rules &amp; data. Use Analyse stock after changing them.</p></div>';return;}
  const weak=ideas.filter(c=>c.status!=='Paper candidate').length;
  $('plan-area').innerHTML=`<h3>Best ${ideas.length===1?'idea':'two ideas'}${weak?` · ${weak} with warnings`:''}</h3><p class="hint">${weak?'Ranked by modelled net reward after costs. Ideas with warnings are for study; paper entry needs your acknowledgement, and hard blocks (liquidity, events, stale prices) stop entry. ':'Passes every check. '}Model scenarios are not a measured win rate.</p><div class="idea-cards">${ideas.map((c,i)=>`<button class="idea-card ${c.status==='Research only'?'research':c.status==='Skip'?'review':''}" data-plan="${a.candidates.indexOf(c)}"><small>IDEA ${i+1} · ${c.status==='Paper candidate'?'Passes all checks':c.status==='Research only'?'Research only · '+esc(checksFor(c).warnings.length)+' warning(s)':'Review only · entry blocked'}</small><strong>${esc(c.name)}</strong><span>${c.entry_credit>=0?'Credit':'Debit'} ${money(Math.abs(c.entry_credit))} · Max loss ${money(c.max_loss)}</span><span>${fmt(c.scenario_profit_pct)}% profitable scenarios · not a measured win rate</span><span>Stressed net ${money(c.stressed_mean_net)} · ${fmt(c.stressed_mean_net/c.max_loss*100)}% of max loss</span></button>`).join('')}</div><div id="plan-content"></div>`;
  $('plan-area').querySelectorAll('[data-plan]').forEach(b=>b.onclick=()=>safely(()=>choosePlan(Number(b.dataset.plan))));
  safely(()=>choosePlan(a.candidates.indexOf(ideas[0])));
}
const RANK={'Paper candidate':2,'Research only':1,'Skip':0};
function topIdeas(candidates){const names=new Set();return candidates.slice().sort((a,b)=>(RANK[b.status]||0)-(RANK[a.status]||0)||b.score-a.score).filter(c=>{if(names.has(c.name))return false;names.add(c.name);return true;}).slice(0,2);}
async function choosePlan(index){
  planIndex=index;draft=null;selected=analysis.candidates[index];editVersion++;const version=editVersion;
  $('plan-area').querySelectorAll('[data-plan]').forEach(b=>b.classList.toggle('selected',Number(b.dataset.plan)===index));
  renderPlan(index);
  if(!marketOpenNow())$('edit-status').textContent='Market closed. Loading reported prices for review only; no paper fill is available.';
  try{const data=await api('/api/options/contracts?analysis='+encodeURIComponent(analysis.id));
    if(version!==editVersion)return;contracts=data.contracts;renderPlan(index);await repriceSelected();
  }catch(e){if(version!==editVersion)return;$('edit-status').textContent=e.message+' Saved payoff remains below.';}
}
async function repriceSelected(){
  const version=++editVersion,origin=analysis.id,index=planIndex;
  const strikes=[...document.querySelectorAll('.strike-select')].map(el=>Number(el.value));
  draft=null;$('paper-open').disabled=true;$('reprice').disabled=true;
  document.querySelectorAll('.strike-select').forEach(el=>{const row=el.closest('tr');[4,5,6,7].forEach(i=>row.cells[i].textContent='Refreshing…');});
  $('edit-status').textContent='Fetching option quotes… Payoff below belongs to the last priced strikes.';
  try{const result=await api('/api/options/reprice',{analysis:origin,candidate:analysis.candidates[index].id,strikes});
    if(version!==editVersion||analysis.id!==origin)return;
    draft=result;message('');renderPlan(index);
    $('edit-status').textContent='Prices refreshed; paper entry rechecks every price.';
  }catch(e){if(version!==editVersion)return;
    // Restore the last priced strikes so an old payoff cannot be mistaken for an edited one.
    renderPlan(index);$('edit-status').textContent=e.message+' Showing the original priced strikes and payoff.';
    message(e.message);
  }
}
function checksFor(c){
  if(c.checks)return c.checks;
  const advisory=['Stressed net profit below minimum','Stressed net return on maximum loss below minimum','Negative average outcome under IV stress','Too few profitable model scenarios','Credit too small for the spreads','IV percentile needs a dated source','Event calendar needs review'];
  const reasons=(c.reasons||[]).filter(r=>!r.startsWith('Chain preview:')&&r!=='One lot exceeds the loss budget');
  return {warnings:reasons.filter(r=>advisory.includes(r)),blockers:reasons.filter(r=>!advisory.includes(r))};
}
function payoffAt(c,price){
  const rows=c.payoff;let i=rows.findIndex(p=>p.price>=price);if(i<1)i=i===0?1:rows.length-1;
  const a=rows[i-1],b=rows[i];return a.net+(b.net-a.net)*(price-a.price)/(b.price-a.price);
}
function renderPlan(index){
  const c=draft?.plan||analysis.candidates[index];selected=c;const checks=checksFor(c);
  const stop=c.underlying_stop;
  const stopText=stop?[stop.lower!=null?'Exit at or below ₹'+fmt(stop.lower):'',stop.upper!=null?'Exit at or above ₹'+fmt(stop.upper):''].filter(Boolean).join(' · '):'Refresh this strategy to calculate its underlying stop';
  const stale=(Date.now()-new Date(analysis.forecast_created||analysis.created))/60000>10;
  const blockers=[...checks.blockers];if(analysis.data_mode==='EOD'&&!blockers.some(r=>r.startsWith('EOD')))blockers.push('EOD analysis: run a live forecast before paper entry');if(!marketOpenNow())blockers.push('Market closed. Return during market hours.');if(stale)blockers.push('Refresh the stock forecast before paper entry.');
  const canEnter=!!draft&&!blockers.length;
  const needsForecast=marketOpenNow()&&(analysis.data_mode==='EOD'||stale||blockers.some(r=>/older than 10|moved more|live forecast/.test(r)));
  $('plan-content').innerHTML=`<div class="builder-heading"><h3>${esc(c.name)} <span class="badge">${esc(c.expiry)} expiry</span></h3><span>One strategy lot · ${c.lot} units per lot</span></div>
  <div class="plan-stats"><div><small>${c.entry_credit>=0?'Entry credit':'Entry debit'}</small><strong>${money(Math.abs(c.entry_credit))}</strong></div><div><small>Max loss · estimated costs included</small><strong class="loss">${money(c.max_loss)}</strong></div><div><small>Max expiry profit · estimated costs included</small><strong class="profit">${c.max_profit==null?'Uncapped':money(c.max_profit)}</strong></div><div><small>Expiry breakeven</small><strong>${esc(c.breakevens.map(fmt).join(' / ')||'None')}</strong></div></div>
  <div class="table-wrap"><table class="legs"><thead><tr><th>Side</th><th>Strike</th><th>Type</th><th>Lots</th><th>LTP</th><th>Bid</th><th>Ask</th><th>Price time · IST</th></tr></thead><tbody>${c.legs.map((l,i)=>{const choices=contracts.filter(o=>o.type===l.type&&o.expiry===l.expiry);if(!choices.some(o=>o.symbol===l.symbol))choices.push(l);choices.sort((a,b)=>a.strike-b.strike);return `<tr><td><span class="side ${l.qty>0?'buy':'sell'}">${l.qty>0?'BUY':'SELL'}</span></td><td><select class="strike-select" aria-label="Strike for leg ${i+1}">${choices.map(o=>`<option value="${o.strike}" ${o.strike===l.strike?'selected':''}>${fmt(o.strike)}</option>`).join('')}</select></td><td>${esc(l.type)}</td><td>${Math.abs(l.qty)}</td><td><strong>${fmt(l.ltp)}</strong></td><td>${fmt(l.bid)}</td><td>${fmt(l.ask_price)}</td><td>${l.quote_time?esc(time(l.quote_time*1000)):'Analysis '+esc(time(analysis.created))}<br><small>${draft?(l.fresh===false?'Stale · display only':'Live book · '+esc(l.source||'FYERS')):'Saved snapshot'}</small></td></tr>`;}).join('')}</tbody></table></div>
  <div class="edit-bar"><button id="reprice">Refresh LTP & payoff</button><span id="edit-status" role="status">${draft?'Depth loaded.':'Analysis snapshot. Loading live quotes when the market is open.'}</span></div>
  <section class="chart-panel payoff-panel"><div id="payoff" role="img" aria-label="Strategy payoff chart"></div></section>
  <p class="stop-line">${stop?`Stop loss · <strong>${esc(stopText)}</strong>`:esc(stopText)}</p>
  <details><summary>Payoff table & model checks</summary><div class="table-wrap"><table><thead><tr><th>Stock move</th><th>Stock price</th><th>Net expiry P&L</th></tr></thead><tbody>${[-10,-5,-2,0,2,5,10].map(p=>{const price=analysis.spot*(1+p/100);const near={price,net:payoffAt(c,price)};return `<tr><td>${p>0?'+':''}${p}%</td><td>${fmt(near.price)}</td><td>${money(near.net)}</td></tr>`;}).join('')}</tbody></table></div><div class="table-wrap"><table><tbody><tr><td>Profitable model scenarios</td><td>${fmt(c.scenario_profit_pct)}% · not measured win rate</td></tr><tr><td>IV-stressed average net outcome</td><td>${money(c.stressed_mean_net)}</td></tr><tr><td>10th percentile net outcome</td><td>${money(c.scenario_tail_net)}</td></tr><tr><td>Short strike touched in sampled paths</td><td>${fmt(c.short_touch_pct)}%</td></tr><tr><td>Theta per day / Vega per IV point</td><td>${money(c.greeks?.theta_day)} / ${money(c.greeks?.vega_point)}</td></tr><tr><td>Estimated costs / exit reserve</td><td>${money(c.cost_estimate)} / ${money(c.exit_cost_reserve)}</td></tr><tr><td>Planned exit date</td><td>${esc(c.exit_by)}</td></tr></tbody></table></div></details>
  <section class="entry-panel"><div class="entry-row"><span>Max loss for one lot <strong class="loss">${money(c.max_loss)}</strong> · costs included · margin not calculated</span>${checks.warnings.length?`<label class="check" title="${esc(checks.warnings.join(' · '))}"><input id="accept-warnings" type="checkbox"> Research trade: accept ${checks.warnings.length} warning(s)</label>`:''}<span class="actions">${needsForecast?'<button id="refresh-forecast" class="primary" title="Runs a new Kronos forecast with live prices. Takes a few minutes.">Refresh forecast for live entry</button>':''}<button id="paper-open" class="primary" title="${esc([...new Set(blockers)].join(' · '))}" ${!canEnter||checks.warnings.length?'disabled':''}>Paper trade this strategy</button><button id="fix-risk">Rules</button></span></div><p id="entry-status" role="status" class="hint">${!draft?'Refresh quotes first. ':''}${blockers.length?'Entry unavailable now: '+esc([...new Set(blockers)][0])+(blockers.length>1?' (+'+(new Set(blockers).size-1)+' more, hover the button)':''):'No broker order is placed.'}</p></section>`;
  const traces=[{name:'Expiry profit',x:c.payoff.map(p=>p.price),y:c.payoff.map(p=>Math.max(0,p.net)),type:'scatter',mode:'lines',line:{color:'#15916d',width:3},fill:'tozeroy',fillcolor:'rgba(21,145,109,.16)'},
    {name:'Expiry loss',x:c.payoff.map(p=>p.price),y:c.payoff.map(p=>Math.min(0,p.net)),type:'scatter',mode:'lines',line:{color:'#d64550',width:3},fill:'tozeroy',fillcolor:'rgba(214,69,80,.17)'}];
  if(c.holding_payoff)traces.push({name:'Planned exit · model estimate',x:c.holding_payoff.map(p=>p.price),y:c.holding_payoff.map(p=>p.net),type:'scatter',mode:'lines',line:{color:'#6675da',dash:'dash',width:2}});
  plot('payoff',traces,{xaxis:{title:'Stock price',gridcolor:'#f0f2f6'},yaxis:{title:'Net P&L · ₹',gridcolor:'#f0f2f6',zerolinecolor:'#94a3b8'},annotations:[...(stop?[stop.lower,stop.upper].filter(v=>v!=null).map(v=>({x:v,y:1,yref:'paper',text:'SL ₹'+fmt(v),showarrow:false,yanchor:'bottom',font:{color:'#d64550',size:10}})):[])],shapes:[...(stop?[stop.lower,stop.upper].filter(v=>v!=null).map(v=>({type:'line',x0:v,x1:v,y0:0,y1:1,yref:'paper',line:{color:'#d64550',dash:'dash',width:2}})):[]),{type:'line',x0:analysis.spot,x1:analysis.spot,y0:0,y1:1,yref:'paper',line:{color:'#d49b43',dash:'dot'}}]});
  $('reprice').onclick=()=>safely(repriceSelected);
  document.querySelectorAll('.strike-select').forEach(el=>el.onchange=()=>safely(repriceSelected));
  $('fix-risk').onclick=()=>view('settings');
  if($('refresh-forecast'))$('refresh-forecast').onclick=()=>safely(()=>scan(false,analysis.symbol));
  if($('accept-warnings'))$('accept-warnings').onchange=()=>{$('paper-open').disabled=!canEnter||!$('accept-warnings').checked;};
  $('paper-open').onclick=()=>safely(async()=>{const button=$('paper-open');button.disabled=true;
    $('entry-status').textContent='Rechecking quotes and recording the paper trade…';
    try{await api('/api/options/paper/open',{analysis:draft.analysis,candidate:c.id,accept_warnings:!!$('accept-warnings')?.checked});message('Paper trade recorded. Open the journal to track its fills and P&L.');await refresh();view('journal');}
    catch(e){$('entry-status').textContent=e.message;throw e;}
    finally{button.disabled=!canEnter||(checks.warnings.length&&!$('accept-warnings')?.checked);}
  });
}
// ---------- Scanner dashboard ----------
const KIND_RANK={'Paper candidate':5,'Research only':4,Skip:3,none:2,'Data needed':1};
const KIND_LABEL={'Paper candidate':'Ready','Research only':'Research only',Skip:'Review only',none:'No idea','Data needed':'Data needed'};
let scannerSort={key:'rank',dir:-1};
function scannerRows(){
  const latest=new Map();for(const a of state.analyses)if(!latest.has(a.symbol))latest.set(a.symbol,a);
  const patterns=new Map((state.stock_ideas||[]).map(r=>[r.symbol,r]));
  const open=new Set(state.paper.filter(t=>t.status==='open').map(t=>t.payload.symbol));
  return [...latest.values()].filter(a=>universeLoaded&&universe.includes(a.symbol)&&a.status!=='Stock only').map(a=>{
    const b=a.best,kind=b?b.status:a.status==='Data needed'?a.status:'none';
    return {...a,kind,patternList:(patterns.get(a.symbol)?.setups||[]).map(s=>patternName(s)+' · '+s.timeframe).filter((v,i,l)=>l.indexOf(v)===i),
      isPattern:patterns.has(a.symbol),open:open.has(a.symbol),ror:b&&b.max_loss?b.stressed_mean_net/b.max_loss*100:null};
  });
}
const SCANNER_COLUMNS=[
  {key:'symbol',label:'Stock',value:r=>r.symbol,cell:r=>`<strong>${esc(r.symbol)}</strong>${r.open?' <span class="badge good">Open trade</span>':''}<div class="pattern-tags">${r.patternList.slice(0,3).map(p=>`<span>${esc(p)}</span>`).join('')}</div>`},
  {key:'rank',label:'Status',value:r=>KIND_RANK[r.kind]*1e6+(r.best?.score||0),cell:r=>`<span class="status s-${r.kind.replace(/\s/g,'-')}">${esc(KIND_LABEL[r.kind])}</span>${r.data_mode==='EOD'?' <span class="badge">EOD</span>':''}`},
  {key:'bias',label:'Kronos view',value:r=>r.expected_pct??-1e9,cell:r=>r.bias?`${esc(r.bias.replace(' bias','').replace(' outlook',''))}<br><small class="${tone(r.expected_pct)}">${pct(r.expected_pct)} median</small>`:'—'},
  {key:'idea',label:'Best idea',value:r=>r.best?.name||'',cell:r=>r.best?esc(r.best.name):`<small>${esc(r.reason||'—')}</small>`},
  {key:'credit',label:'Credit / debit',value:r=>r.best?.entry_credit??-1e12,cell:r=>r.best?`${r.best.entry_credit>=0?'Cr':'Dr'} ${money(Math.abs(r.best.entry_credit))}`:'—'},
  {key:'loss',label:'Max loss',value:r=>r.best?.max_loss??1e12,cell:r=>r.best?`<span class="loss">${money(r.best.max_loss)}</span>`:'—'},
  {key:'net',label:'Stressed net',value:r=>r.best?.stressed_mean_net??-1e12,cell:r=>r.best?`<span class="${tone(r.best.stressed_mean_net)}">${money(r.best.stressed_mean_net)}</span>`:'—'},
  {key:'ror',label:'Net / max loss',value:r=>r.ror??-1e12,cell:r=>r.ror==null?'—':pct(r.ror)},
  {key:'scenarios',label:'Profitable paths',value:r=>r.best?.scenario_profit_pct??-1,cell:r=>r.best?fmt(r.best.scenario_profit_pct)+'%':'—'},
  {key:'ivp',label:'IVP',value:r=>r.ivp_value??-1,cell:r=>r.ivp_value==null?'<small>Missing</small>':fmt(r.ivp_value)},
  {key:'event',label:'Next event',value:r=>r.next_event?.date||'9999',cell:r=>r.next_event?`<span class="${r.event_in_trade?'loss':''}">${r.event_in_trade?'⚠ ':''}${esc(r.next_event.date)}</span><br><small>${esc(r.next_event.title)}</small>`:r.events_checked?'<small>None in 90d</small>':'<small>Unchecked</small>'},
  {key:'age',label:'Analysed',value:r=>new Date(r.created).getTime(),cell:r=>`<span title="${esc(time(r.created))} IST">${esc(ago(r.created))}</span>`},
];
function filteredScanner(){
  const q=$('scanner-search').value.toUpperCase(),status=$('scanner-status').value,bias=$('scanner-bias').value,days=Number($('scanner-age').value);
  let rows=scannerRows().filter(r=>(r.symbol+' '+r.patternList.join(' ')+' '+(r.best?.name||'')).toUpperCase().includes(q))
    .filter(r=>!status||(status==='actionable'?['Paper candidate','Research only'].includes(r.kind):r.kind===status))
    .filter(r=>!bias||r.bias===bias)
    .filter(r=>!days||Date.now()-new Date(r.created)<days*864e5)
    .filter(r=>!$('scanner-no-events').checked||!r.event_in_trade)
    .filter(r=>!$('scanner-patterns').checked||r.isPattern);
  const col=SCANNER_COLUMNS.find(c=>c.key===scannerSort.key);
  return rows.sort((a,b)=>{const x=col.value(a),y=col.value(b);return (x<y?-1:x>y?1:0)*scannerSort.dir;});
}
function renderScanner(){
  if(!state)return;
  const all=scannerRows(),counts={};for(const r of all)counts[r.kind]=(counts[r.kind]||0)+1;
  $('scanner-chips').innerHTML=`<button data-chip="">All <b>${all.length}</b></button>`+Object.keys(KIND_LABEL).filter(k=>counts[k]).map(k=>`<button data-chip="${esc(k)}" class="chip-${k.replace(/\s/g,'-')}">${esc(KIND_LABEL[k])} <b>${counts[k]}</b></button>`).join('');
  $('scanner-chips').querySelectorAll('[data-chip]').forEach(b=>{b.setAttribute('aria-pressed',String($('scanner-status').value===b.dataset.chip));b.onclick=()=>{$('scanner-status').value=b.dataset.chip;renderScanner();};});
  const rows=filteredScanner();
  $('scanner-table').innerHTML=rows.length?`<div class="table-wrap scanner-wrap"><table class="scanner"><thead><tr>${SCANNER_COLUMNS.map(c=>`<th><button data-sort="${c.key}" class="sort ${scannerSort.key===c.key?'on':''}">${esc(c.label)}${scannerSort.key===c.key?(scannerSort.dir>0?' ▲':' ▼'):''}</button></th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr data-open="${esc(r.symbol)}" tabindex="0" class="k-${r.kind.replace(/\s/g,'-')}">${SCANNER_COLUMNS.map(c=>`<td>${c.cell(r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
    :`<p class="empty">${all.length?'No stocks match these filters.':'No analyses yet. Use Scan F&O pattern stocks to fill the scanner.'}</p>`;
  $('scanner-table').querySelectorAll('[data-sort]').forEach(b=>b.onclick=()=>{const k=b.dataset.sort;scannerSort=scannerSort.key===k?{key:k,dir:-scannerSort.dir}:{key:k,dir:['symbol','loss','event'].includes(k)?1:-1};renderScanner();});
  $('scanner-table').querySelectorAll('[data-open]').forEach(tr=>{const go=()=>{view('ideas');safely(()=>selectStock(tr.dataset.open));};tr.onclick=go;tr.onkeydown=e=>{if(e.key==='Enter')go();};});
}
for(const id of ['scanner-search'])$(id).oninput=renderScanner;
for(const id of ['scanner-status','scanner-bias','scanner-age','scanner-no-events','scanner-patterns'])$(id).onchange=renderScanner;
$('export-scanner').onclick=()=>download(`options-scanner-${new Date().toISOString().slice(0,10)}.csv`,[
  ['symbol','status','analysed','data_mode','bias','median_return_pct','best_idea','entry_credit','max_loss','stressed_net','net_over_max_loss_pct','profitable_paths_pct','ivp','next_event','event_in_trade','patterns','reason'],
  ...filteredScanner().map(r=>[r.symbol,KIND_LABEL[r.kind],r.created,r.data_mode,r.bias,r.expected_pct,r.best?.name,r.best?.entry_credit,r.best?.max_loss,r.best?.stressed_mean_net,r.ror?.toFixed(2),r.best?.scenario_profit_pct,r.ivp_value,r.next_event?.date,r.event_in_trade,r.patternList.join('; '),r.reason])]);

// ---------- Journal, alerts and analytics ----------
let journalFilter='all';const dismissed=new Set();
const needsAction=t=>t.status==='open'&&t.payload.exit_signal&&t.payload.exit_signal!=='Monitor';
function sparkline(marks){
  const v=(marks||[]).map(m=>m.net_pnl);if(v.length<2)return '';
  const lo=Math.min(0,...v),hi=Math.max(0,...v),span=hi-lo||1,x=i=>i/(v.length-1)*88+1,y=n=>23-(n-lo)/span*22;
  return `<svg class="spark" viewBox="0 0 90 24" aria-hidden="true"><line x1="0" x2="90" y1="${y(0)}" y2="${y(0)}"/><polyline class="${v[v.length-1]>=0?'up':'down'}" points="${v.map((n,i)=>x(i).toFixed(1)+','+y(n).toFixed(1)).join(' ')}"/></svg>`;
}
function stopDistance(p){
  const u=p.mark?.underlying,s=p.plan.underlying_stop;if(!u||!s)return '';
  const gap=(d,level,side)=>d>0?`${Math.abs(d).toFixed(1)}% ${side} SL ₹${fmt(level)}`:`SL ₹${fmt(level)} breached`;
  const parts=[];if(s.lower!=null)parts.push(gap((u-s.lower)/u*100,s.lower,'above'));if(s.upper!=null)parts.push(gap((s.upper-u)/u*100,s.upper,'below'));
  return `<small>Stock ₹${fmt(u)} · ${esc(parts.join(' · '))}</small>`;
}
function renderJournal(){
  document.querySelectorAll('[data-journal-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.journalFilter===journalFilter)));
  const trades=state.paper.filter(t=>journalFilter==='all'||(journalFilter==='alerts'?needsAction(t):t.status===journalFilter));
  $('paper-table').innerHTML=trades.length?`<div class="table-wrap"><table class="journal"><thead><tr><th>Stock / strategy</th><th>State</th><th>Entry</th><th>Max loss</th><th>Net P&L</th><th>Trend</th><th>Underlying vs stop</th><th>Exit review</th><th>Actions</th></tr></thead><tbody>${trades.map(t=>{const p=t.payload,n=p.mark?.net_pnl,sig=t.status==='closed'?'Closed '+time(t.updated):p.exit_signal||(p.exit_by?'Close by '+p.exit_by:'Monitor'),danger=sig.startsWith('EXIT NOW'),days=((t.status==='open'?Date.now():new Date(t.updated))-new Date(t.created))/864e5;return `<tr class="${needsAction(t)?(danger?'row-danger':'row-warn'):''}"><td><strong>${esc(p.symbol)}</strong><br>${esc(p.plan.name)} <small>· exp ${esc(p.plan.expiry)}</small><details><summary>Legs & fills</summary><table><thead><tr><th>Contract</th><th>Side / units</th><th>Entry fill</th><th>Last exit price</th></tr></thead><tbody>${p.legs.map(l=>`<tr><td>${esc(l.symbol)}</td><td>${l.qty>0?'Buy':'Sell'} ${Math.abs(l.qty)*l.lot}</td><td>${fmt(l.entry_fill)}</td><td>${fmt(p.mark?.legs?.find(m=>m.symbol===l.symbol)?.price)}</td></tr>`).join('')}</tbody></table><p>Entry fees: ${money(Object.values(p.entry_charges||{}).reduce((a,b)=>a+b,0))}${p.mark?.exit_charges?' · Exit fees: '+money(Object.values(p.mark.exit_charges).reduce((a,b)=>a+b,0)):''}</p>${p.accepted_warnings?.length?`<p>Accepted warnings: ${esc(p.accepted_warnings.join(' · '))}</p>`:''}<p>Recorded ${esc(time(p.entry_time))} · spot ₹${fmt(p.entry_spot)}</p></details></td>
      <td>${t.status==='open'?'Open':'Closed'}<br><small>${fmt(days)} d</small>${p.research?'<br><span class="badge warn">Research</span>':''}</td>
      <td>${p.entry_credit>=0?'Cr':'Dr'} ${money(Math.abs(p.entry_credit))}</td><td>${money(p.plan.max_loss)}</td>
      <td><strong class="${tone(n)}">${n==null?'Not marked':signed(n)}</strong>${n!=null?`<br><small>${pct(n/p.plan.max_loss*100)} of max loss</small>`:''}<br><small>${p.mark?esc(ago(p.mark.time)):''}</small></td>
      <td>${sparkline(p.marks)||'<small>—</small>'}</td><td>${stopDistance(p)||`<small>${t.status==='open'?'Mark to check':'—'}</small>`}</td>
      <td><span class="signal ${danger?'danger':needsAction(t)?'warn':''}">${esc(sig)}</span></td>
      <td>${t.status==='open'?`<button data-mark="${esc(t.id)}">Mark</button> <button data-close="${esc(t.id)}">Close</button>`:'<small>Recorded</small>'}</td></tr>`;}).join('')}</tbody></table></div>`
    :`<p class="empty">${state.paper.length?'No trades in this view.':'No paper trades yet. Eligible ideas can be recorded from Find a trade.'}</p>`;
  $('paper-table').querySelectorAll('[data-mark],[data-close]').forEach(b=>b.onclick=()=>safely(async()=>{
    if(b.dataset.close&&!confirm('Close this paper trade now? Longs sell at the live bid, shorts buy back at the live ask.'))return;
    b.disabled=true;try{await api('/api/options/paper/mark',{id:b.dataset.mark||b.dataset.close,close:!!b.dataset.close});await refresh();message(b.dataset.close?'Paper exit recorded.':'Fresh paper mark recorded.');}finally{b.disabled=false;}}));
}
document.querySelectorAll('[data-journal-filter]').forEach(b=>b.onclick=()=>{journalFilter=b.dataset.journalFilter;renderJournal();});
function renderAnalytics(){
  const s=state.stats,c=s.closed;
  const tiles=[['Closed trades',c.count,`${c.wins} won · ${c.losses} lost`],['Win rate',c.win_rate==null?'—':c.win_rate+'%','Share of closed trades with net profit'],
    ['Realised P&L',signed(c.realised),c.return_on_risk_pct==null?'':pct(c.return_on_risk_pct)+' of planned risk',tone(c.realised)],
    ['Profit factor',c.profit_factor??'—','Gross wins ÷ gross losses'],['Expectancy',signed(c.expectancy),'Average net per closed trade',tone(c.expectancy)],
    ['Average win / loss',`${money(c.avg_win)} / ${money(c.avg_loss)}`,''],['Max drawdown',money(c.max_drawdown),'Peak-to-trough, realised curve'],
    ['Best / worst',`${money(c.best)} / ${money(c.worst)}`,''],['Average holding',c.avg_days_held==null?'—':c.avg_days_held+' days','']];
  $('stat-grid').innerHTML=tiles.map(([k,v,n,cl])=>`<div><small>${esc(k)}</small><strong class="${cl||''}">${esc(v)}</strong><span>${esc(n)}</span></div>`).join('');
  $('stats-note').textContent=s.note+(c.count&&c.count<20?` Only ${c.count} closed trade(s) so far.`:'');
  $('groups').innerHTML=s.groups.length?`<table><thead><tr><th>Group</th><th>Trades</th><th>Win rate</th><th>Net P&L</th><th>Net / risk</th></tr></thead><tbody>${s.groups.map(g=>`<tr><td>${esc(g.name)}</td><td>${g.trades}</td><td>${fmt(g.win_rate)}%</td><td class="${tone(g.pnl)}">${signed(g.pnl)}</td><td>${pct(g.pnl/g.risk*100)}</td></tr>`).join('')}</tbody></table>`:'<p class="empty">Close paper trades to compare strategies.</p>';
  if(!s.curve.length){$('equity').innerHTML='<p class="empty">The equity curve appears after the first closed paper trade.</p>';return;}
  if(!$('equity').offsetParent&&$('equity').data)return;
  plot('equity',[{name:'Trade P&L',x:s.curve.map(r=>r.time),y:s.curve.map(r=>r.pnl),type:'bar',marker:{color:s.curve.map(r=>r.pnl>=0?'rgba(18,132,97,.55)':'rgba(196,71,77,.55)')},text:s.curve.map(r=>r.symbol+' · '+r.strategy),textposition:'none',hovertemplate:'%{text}<br>%{y:,.0f}<extra></extra>'},
    {name:'Cumulative',x:s.curve.map(r=>r.time),y:s.curve.map(r=>r.cumulative),type:'scatter',mode:'lines+markers',line:{color:'#5369d8',width:2.5}}],
    {xaxis:{type:'date',gridcolor:'#f0f2f6'},yaxis:{title:'Net P&L · ₹',gridcolor:'#f0f2f6',zerolinecolor:'#94a3b8'}});
}
function renderAlerts(){
  const live=state.paper.filter(needsAction);
  $('alert-count').textContent=live.length;$('alert-count').hidden=!live.length;
  document.title=(live.length?`(${live.length}) `:'')+'Kronos · Options desk';
  const show=live.filter(t=>!dismissed.has(t.id+'|'+t.payload.exit_signal));
  $('alert-banner').hidden=!show.length;
  $('alert-banner').className=show.some(t=>t.payload.exit_signal.startsWith('EXIT NOW'))?'danger':'';
  $('alert-banner').innerHTML=show.length?`<div><strong>${show.length} open trade${show.length>1?'s need':' needs'} attention</strong><ul>${show.map(t=>`<li>${esc(t.payload.symbol)} · ${esc(t.payload.plan.name)}: ${esc(t.payload.exit_signal)} · ${signed(t.payload.mark?.net_pnl)}</li>`).join('')}</ul></div><button id="alert-open">Open journal</button><button id="alert-dismiss">Dismiss</button>`:'';
  if(show.length){$('alert-open').onclick=()=>{journalFilter='alerts';view('journal');renderJournal();};$('alert-dismiss').onclick=()=>{show.forEach(t=>dismissed.add(t.id+'|'+t.payload.exit_signal));renderAlerts();};}
}
let marking=false;
async function markAll(auto=false){
  if(marking)return;marking=true;$('mark-all').disabled=true;$('mark-status').textContent='Fetching fresh bid/ask for open trades…';
  try{const r=await api('/api/options/paper/mark-all',{});await refresh();
    $('mark-status').textContent=`${auto?'Auto-marked':'Marked'} ${r.marked} trade(s) at ${time(r.time)} IST.`+(r.errors.length?' Not marked: '+r.errors.map(e=>e.symbol+' ('+e.error+')').join('; '):'');
  }catch(e){$('mark-status').textContent=e.message;if(!auto)throw e;}
  finally{marking=false;$('mark-all').disabled=false;}
}
$('mark-all').onclick=()=>safely(()=>markAll());
$('auto-mark').checked=store('kronos-auto-mark')==='1';
$('auto-mark').onchange=()=>{store('kronos-auto-mark',$('auto-mark').checked?'1':'0');if($('auto-mark').checked)autoMark();};
function autoMark(){
  if(!$('auto-mark').checked||!state||document.hidden)return;
  if(!marketOpenNow()){$('mark-status').textContent='Auto-mark paused: market closed.';return;}
  if(state.paper.some(t=>t.status==='open'))markAll(true);
}
setInterval(autoMark,60000);
$('export-journal').onclick=()=>download(`options-paper-journal-${new Date().toISOString().slice(0,10)}.csv`,[
  ['id','symbol','strategy','status','research','opened','updated','expiry','entry_credit','max_loss','net_pnl','last_mark','exit_signal','legs'],
  ...state.paper.map(t=>{const p=t.payload;return [t.id,p.symbol,p.plan.name,t.status,!!p.research,t.created,t.updated,p.plan.expiry,p.entry_credit,p.plan.max_loss,p.mark?.net_pnl,p.mark?.time,p.exit_signal,p.legs.map(l=>`${l.qty>0?'B':'S'}${Math.abs(l.qty)} ${l.symbol} @${l.entry_fill}`).join(' | ')];})]);
async function loadUniverse(silent=false){const data=await api('/api/options/universe');universe=data.symbols;universeLoaded=true;$('symbol').innerHTML=universe.map(s=>`<option value="${esc(s)}">${esc(s)}</option>`).join('');if(selectedSymbol)syncSymbol(selectedSymbol);renderStocks();if(state)renderScanner();if(!silent)message(`${data.symbols.length} F&O stocks loaded from the current FYERS instrument list.`);}
$('load-universe').onclick=()=>safely(()=>loadUniverse());
async function scan(all,symbol=null,scope='fno'){
  message('');if(active){message('A forecast is already running. Browse saved stocks or wait for it to finish.');return;}if(!universeLoaded)await loadUniverse(true);
  if(!all)selectedSymbol=symbol||$('symbol').value;
  if(!all){editVersion++;draft=null;if($('paper-open'))$('paper-open').disabled=true;$('progress').textContent='Starting fresh forecast for '+selectedSymbol+'…';}
  else $('progress').textContent='Starting scan…';
  let job;try{job=await api('/api/options/scan',{symbol:all?null:selectedSymbol,scope,settings:settings()});}catch(e){$('progress').textContent='Analysis could not start';if($('stock-loading'))$('stock-loading').textContent=e.message;throw e;}active=job.id;busy(true);poll();
}
$('analyse').onclick=()=>safely(()=>scan(false));$('scan-all').onclick=()=>safely(()=>scan(true));
$('scan-fno').onclick=()=>safely(()=>scan(true));$('scan-patterns').onclick=()=>safely(()=>scan(true,null,'patterns'));
$('cancel').onclick=$('scanner-cancel').onclick=()=>safely(()=>api('/api/cancel/'+active,{}));
async function poll(){
  if(timer)clearTimeout(timer);
  try{
    const job=await api('/api/runs/'+active);$('progress').textContent=`${job.progress}% · ${job.message}`;
    $('scanner-progress').textContent=`Scanning · ${job.progress}% · ${job.message}`;
    if(['complete','cancelled','failed','interrupted'].includes(job.status)){active=null;busy(false);await refresh();if(job.kind==='options'&&job.result?.stocks?.length){const row=job.result.stocks.find(s=>s.symbol===selectedSymbol);if(!row)return;analysis=await api('/api/options/analysis/'+row.id);selectedSymbol=analysis.symbol;contracts=[];draft=null;editVersion++;renderDetail();renderStocks();}if(job.error)message(job.error);}
    else timer=setTimeout(poll,3000);
  }catch(e){active=null;busy(false);message(e.message);}
}
$('score').onclick=()=>safely(async()=>{const r=await api('/api/options/score',{});await refresh();message(`${r.scored} completed forecasts scored against observed stock closes.`);});
$('settings-form').onsubmit=e=>{e.preventDefault();safely(async()=>{await api('/api/options/settings',settings());await refresh();message('Rules saved. They apply to new analyses and are kept for future sessions.');});};
$('reference-form').onsubmit=e=>{e.preventDefault();safely(async()=>{const form=e.target,body=Object.fromEntries(new FormData(form));body.symbol=body.symbol.toUpperCase().trim();body.ivp=body.ivp===''?null:Number(body.ivp);body.events=body.event_date?[{date:body.event_date,title:body.event_title||'Scheduled event'}]:[];await api('/api/options/reference',body);message('Source check saved for today. Analyse the stock again to apply it.');});};
safely(async()=>{const bootstrap=await api('/api/bootstrap');token=bootstrap.token;await refresh();try{await loadUniverse(true);}catch(e){message(e.message);}const first=state.analyses.find(a=>a.has_forecast)||state.analyses[0];if(first&&!active)await selectStock(first.symbol);});

const refreshOptionsPrice=LivePrices.watch(()=>selectedSymbol?[selectedSymbol]:[],(quotes,loading)=>{
  const el=$('current-stock-ltp'),stamp=$('current-stock-time'),q=quotes[selectedSymbol];
  if(el)el.textContent=loading?'Fetching LTP…':q?money(q.ltp):'LTP unavailable';
  if(stamp)stamp.textContent=loading?'Checking API…':LivePrices.label(q);
},error=>{if($('current-stock-ltp'))$('current-stock-ltp').textContent='LTP unavailable';if($('current-stock-time'))$('current-stock-time').textContent=error.message;});
// Leg LTP and payoff refresh independently of saved AI forecast snapshots.
function refreshOptionLegs(){
  if(document.hidden||window.frameElement?.hidden||active||!contracts.length||!analysis?.candidates?.length||!$('reprice')||$('reprice').disabled)return;
  safely(repriceSelected);
}
setInterval(refreshOptionLegs,30000);
window.addEventListener('focus',refreshOptionLegs);
window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data==='refresh-prices')refreshOptionLegs();});
window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data==='patterns-updated')safely(refresh);});
