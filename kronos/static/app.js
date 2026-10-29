'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d});
const money=v=>v==null?'—':'₹'+num(v);
const pct=(v,sign=false)=>v==null?'—':(sign&&v>0?'+':'')+num(v,1)+'%';
const prettyDate=d=>d?new Date(d.length===10?d+'T12:00:00':d).toLocaleDateString('en-IN',{day:'numeric',month:'short',year:'numeric'}):'—';
const patternName=x=>({stage:'Stage 1 → 2',darvas:'Darvas box',cup:'Cup & handle'}[x]||x||'No pattern');
const state={token:'',settings:null,setups:[],runs:[],symbols:[],symbol:'',tf:'D',scanner:'all',context:null,forecast:null,forecastId:null,backtest:null,backtestId:null,active:null,contextSequence:0,candles:true,pollTimer:null};
const reviewDefaults={model:'small',lookback:128,samples:32,batch_size:4,seed:42,temperature:1,top_p:.9,buffer_pct:.1,confirmation_bars:2,target_r:1,min_target_pct:5,fee_bps:5,slippage_bps:5,min_success_rate:50};

async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Kronos-Token':state.token},body:JSON.stringify(body)};
  const response=await fetch(path,options);
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||'Request failed.');
  return data;
}
function notice(message,error=false){$('notice').classList.remove('hidden');$('notice').classList.toggle('error',error);$('notice').querySelector('span').textContent=message;}
function guarded(fn){return async event=>{try{await fn(event);}catch(error){notice(error.message,true);}};}
function showPage(page){
  document.body.dataset.page=page;
  for(const name of ['research','performance','runs'])$(name+'-page').classList.toggle('hidden',name!==page);
  document.querySelectorAll('.nav-tab').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
  requestAnimationFrame(()=>KronosCharts.resize());
}
function settingsFromForm(){
  if(!$('settings-form').reportValidity())throw new Error('Check the highlighted setting.');
  const result={...state.settings,...reviewDefaults,timeframe:state.tf};
  for(const field of $('settings-form').elements){if(field.name)result[field.name]=Number(field.value);}
  return result;
}
function holdingOptions(horizon){
  const periods=state.tf==='W'?[2,4,6,8]:[5,10,15,20];
  if(!periods.includes(horizon))periods.push(horizon);
  $('holding-period').innerHTML=periods.sort((a,b)=>a-b).map(n=>`<option value="${n}">${n} trading ${state.tf==='W'?'weeks':'days'}</option>`).join('');
}
function renderReview(){
  const review=KronosWorkflow.review(state.forecast||state.context);
  $('review-title').textContent=review.title;$('review-reason').textContent=review.reason;
  $('review-card').dataset.tone=review.tone;
  $('review-checks').innerHTML=review.checks.map(([key,value])=>`<tr><td>${esc(key)}</td><td>${esc(value)}</td></tr>`).join('');
}
function fillSettings(settings){
  state.settings={...settings};state.tf=settings.timeframe;holdingOptions(settings.horizon);
  for(const field of $('settings-form').elements)if(field.name&&settings[field.name]!==undefined)field.value=settings[field.name];
  document.querySelectorAll('[data-tf]').forEach(b=>b.classList.toggle('selected',b.dataset.tf===state.tf));
  $('timeframe-label').textContent=state.tf==='D'?'DAILY':'WEEKLY';
  $('test-timeframe').textContent=state.tf==='D'?'Daily bars':'Weekly bars';
  $('settings-dirty').classList.add('hidden');
}
function markEdited(){
  $('settings-dirty').classList.remove('hidden');
  if(state.forecast)$('chart-state').textContent='SAVED FORECAST · SETTINGS EDITED';
  renderReview();
}
function renderSetups(){
  const query=$('setup-search').value.trim().toLowerCase(),filter=$('setup-filter').value;
  const rows=state.setups.filter(r=>r.timeframe===state.tf&&(filter==='all'||r.scanner===filter)&&r.symbol.toLowerCase().includes(query));
  $('setup-count').textContent=rows.length;
  $('setup-list').innerHTML=rows.length?rows.map(r=>`<button class="setup-row ${r.symbol===state.symbol&&r.scanner===state.scanner?'active':''}" data-symbol="${esc(r.symbol)}" data-scanner="${esc(r.scanner)}" data-date="${esc(r.date)}"><span><strong>${esc(r.symbol)}</strong><small>${esc(patternName(r.scanner))}</small><small>${esc(prettyDate(r.date))}</small></span><span class="score">${num(r.score,0)}<small>pattern</small></span></button>`).join(''):'<div class="no-setups">No setups in this view.<br>Use <b>Scan cache</b> to run your detectors, or load any cached symbol above.</div>';
}
function researchOptions(){return {candles:state.candles,volume:$('show-volume').checked,sma20:$('show-sma20').checked,sma50:$('show-sma50').checked,band:$('show-band').checked};}
async function drawResearch(){await KronosCharts.price('price-chart',state.forecast||state.context,researchOptions());}
function renderHeader(data){
  if(!data)return;
  state.symbol=data.symbol;state.tf=data.timeframe;
  $('symbol-input').value=data.symbol;$('asset-title').textContent=data.symbol;$('test-symbol').textContent=data.symbol;
  $('timeframe-label').textContent=state.tf==='D'?'DAILY':'WEEKLY';$('test-timeframe').textContent=state.tf==='D'?'Daily bars':'Weekly bars';
  const history=data.history||[],last=history.at(-1),previous=history.at(-2);
  $('last-price').textContent=money(last?.close);
  const change=previous?(last.close/previous.close-1)*100:0;
  $('last-change').textContent=pct(change,true)+' · last bar';$('last-change').className=change>=0?'positive':'negative';
  const patterns=data.signals||[data.pattern].filter(Boolean);
  $('asset-subtitle').textContent=`Scanner cache · ${prettyDate(data.asof)} · ${patterns.length?patterns.map(r=>patternName(r.scanner)).join(' / '):'No detected pattern at this date'}`;
  const notes=[];
  const age=Math.floor((Date.now()-new Date(data.asof+'T15:30:00+05:30').getTime())/86400000);
  if(age>1)notes.push(`Data view ends ${prettyDate(data.asof)}. Fetch newer bars with the original scanner when needed.`);
  if(data.partial_week||data.metadata?.partial_week)notes.push('This weekly candle may still be forming.');
  $('data-note').textContent=notes.join(' ');$('data-note').classList.toggle('hidden',!notes.length);$('data-note').classList.add('warning');
  if(!$('test-end').value&&history.length){$('test-end').value=history.at(-1).date;$('test-start').value=history[Math.max(0,history.length-100)].date;}
  renderSetups();
}
function clearForecast(){
  state.forecast=null;state.forecastId=null;
  for(const id of ['median-finish','end-range','success-rate','success-rate'])$(id).textContent='—';
  $('bias-badge').textContent='NOT RUN';$('chart-state').textContent='HISTORY';$('actual-legend').classList.add('hidden');
  $('outlook-text').textContent='Generate a forecast to see the model’s price range and directional bias.';
  $('outcome-bars').innerHTML='';$('sample-note').textContent='Observed prices are market data. Dashed lines and shaded ranges are AI forecasts.';
  $('comparison-status').textContent='Actual prices appear here as later bars become available.';
  $('comparison-metrics').innerHTML='<tr><td>Mean absolute error</td><td>—</td></tr><tr><td>Unchanged-price baseline</td><td>—</td></tr><tr><td>AI range coverage</td><td>—</td></tr>';
  $('method-content').textContent='Model and data details will appear after a forecast.';

}
async function loadContext(symbol=state.symbol,asof=$('asof').value){
  symbol=symbol.trim().toUpperCase();if(!symbol)throw new Error('Choose a symbol first.');
  const sequence=++state.contextSequence;
  const query=new URLSearchParams({symbol,timeframe:state.tf});if(asof)query.set('asof',asof);
  $('asset-subtitle').textContent='Loading cached market data…';
  const context=await api('/api/context?'+query);
  if(sequence!==state.contextSequence)return;
  state.context=context;clearForecast();renderHeader(context);renderReview();await drawResearch();
}
function renderComparison(comparison){
  const c=comparison||{available:0,required:state.settings.horizon,metrics:{}};
  $('comparison-status').textContent=c.available?`${c.available}/${c.required} later bars available · ${c.complete?'full horizon':'partial horizon; outcome still open'}`:'No later bars yet. This is a forecast, not a completed test.';
  const m=c.metrics||{};
  $('comparison-metrics').innerHTML=`<tr><td>AI mean absolute error</td><td>${money(m.mae)}</td></tr><tr><td>Unchanged-price baseline</td><td>${money(m.baseline_mae)}</td></tr><tr><td>10–90% range coverage</td><td>${pct(m.coverage_pct)}</td></tr><tr><td>Endpoint direction</td><td>${m.direction_correct==null?'—':m.direction_correct?'Matched':'Missed'}</td></tr>`;
  $('actual-legend').classList.toggle('hidden',!c.available);
}
async function renderForecast(result,id){
  ++state.contextSequence;state.forecast=result;state.forecastId=id;state.context=null;state.scanner=result.pattern?.scanner||'all';
  fillSettings(result.settings);$('asof').value=result.asof;renderHeader(result);
  const s=result.summary,b=result.breakout,p=result.plan;
  $('median-finish').innerHTML=`${money(s.median_end)}<small class="${s.median_return_pct>=0?'positive':'negative'}">${pct(s.median_return_pct,true)} over ${result.settings.horizon} bars</small>`;
  $('end-range').innerHTML=`${num(s.end_q10)} – ${num(s.end_q90)}<small>Model sample range · ₹</small>`;
  $('success-rate').innerHTML=b?`${pct(b.success_rate)}<small>${b.outcome_counts.success}/${b.sample_count} paths · ${num(b.success_interval[0],1)}–${num(b.success_interval[1],1)}% sampling interval</small>`:'—<small>No pattern to evaluate</small>';
  $('bias-badge').textContent=s.bias.toUpperCase();$('chart-state').textContent=`SAVED REVIEW · ${prettyDate(result.asof)}`;
  $('outlook-text').textContent=`${pct(s.upside_rate)} of sampled paths finish above the last observed close. The median finish is ${money(s.median_end)} (${pct(s.median_return_pct,true)}). ${b?`The pattern reference target is ${money(p.target)}; ${b.outcome_counts.success} of ${b.sample_count} paths confirm and reach it before invalidation.`:'The displayed levels are a recent-range reference, not a detected pattern.'}`;
  const labels={no_confirmation:'No confirmation',success:'Target reached',fakeout:'Failed breakout',breakdown:'Support broken',unresolved:'Target unresolved',no_breakout:'No confirmation',extended:'Already above target'};
  $('outcome-bars').innerHTML=b?Object.entries(b.outcome_counts).filter(([,n])=>n>0).map(([name,count])=>`<div class="outcome-row"><span>${labels[name]||esc(name)}</span><div class="outcome-track"><span style="width:${count/b.sample_count*100}%;background:${name==='success'?'var(--green)':name==='breakdown'||name==='fakeout'?'var(--red)':'#78716c'}"></span></div><strong>${count}/${b.sample_count}</strong></div>`).join(''):'';
  $('sample-note').textContent=`${s.sample_count} sampled paths. ${s.sample_count<20?'Small sample; no favourable verdict is assigned. ':''}These frequencies are not calibrated market odds.`;
  renderComparison(result.comparison);
  const m=result.metadata;
  const rows=[['Model',m.model||result.settings.model],['Model revision',m.model_revision||'—'],['Tokenizer revision',m.tokenizer_revision||'—'],['Observed bars',m.lookback_used],['Device',m.device||'—'],['Seed',m.seed],['Calendar',m.calendar],['Repaired forecast candles',m.repaired_candles],['Data fingerprint',m.data_hash],['Run ID',id]];
  $('method-content').innerHTML='<ul>'+result.notes.map(n=>'<li>'+esc(n)+'</li>').join('')+'</ul><p>Success: close above the buffered pivot for '+result.settings.confirmation_bars+' bars, then reach target before stop or a close back below pivot. Confirmation at or above the target is extended.</p><table class="plain-table"><tbody>'+rows.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')+'</tbody></table>';
  renderReview();await drawResearch();
}
async function renderWindow(){
  const index=Number($('window-select').value||0),w=state.backtest?.windows[index];if(!w)return;
  await KronosCharts.price('backtest-chart',w,{candles:false,volume:false,band:true});
}
async function renderBacktest(result,id){
  state.backtest=result;state.backtestId=id;state.symbol=result.symbol;
  fillSettings(result.settings);$('symbol-input').value=result.symbol;$('test-symbol').textContent=result.symbol;
  $('performance-empty').classList.add('hidden');$('performance-result').classList.remove('hidden');
  $('test-start').value=result.start;$('test-end').value=result.end;
  const s=result.summary;
  $('backtest-title').textContent=`${result.symbol} · ${s.windows} test windows · ${prettyDate(result.start)} – ${prettyDate(result.end)}`;
  $('bt-mae').innerHTML=money(s.mae)+`<small>${s.observations} price observations</small>`;
  $('bt-baseline').innerHTML=money(s.baseline_mae)+'<small>Price stays at origin close</small>';
  $('bt-direction').innerHTML=pct(s.direction_accuracy_pct)+'<small>End-of-window direction</small>';
  $('bt-coverage').innerHTML=pct(s.coverage_pct)+'<small>Nominal band: 80%</small>';
  $('baseline-note').textContent=s.mae_skill_pct==null?'Baseline error is zero; relative skill is undefined.':s.mae_skill_pct>=0?`Kronos reduced absolute price error by ${pct(s.mae_skill_pct)} against the unchanged-price baseline in this test.`:`Kronos had ${pct(-s.mae_skill_pct)} more absolute price error than the unchanged-price baseline in this test.`;
  $('baseline-note').classList.toggle('warning',s.mae_skill_pct==null||s.mae_skill_pct<0);
  $('window-select').innerHTML=result.windows.map((w,i)=>`<option value="${i}">${i+1}. ${esc(prettyDate(w.asof))}</option>`).join('');
  $('quality-metrics').innerHTML=[['Pattern windows',result.windows.filter(w=>w.pattern).length],['Breakout Brier error',s.brier_score==null?'No pattern windows':num(s.brier_score,3)],['Price skill vs baseline',pct(s.mae_skill_pct,true)]].map(([k,v])=>`<tr><td>${k}</td><td>${v}</td></tr>`).join('');
  $('window-rows').innerHTML=result.windows.map((w,i)=>`<tr><td><button class="text-button" data-window="${i}">${esc(prettyDate(w.asof))}</button></td><td>${esc(w.pattern?patternName(w.pattern.scanner):'No pattern')}</td><td>${money(w.comparison.metrics.mae)}</td><td>${money(w.comparison.metrics.baseline_mae)}</td><td>${w.comparison.metrics.direction_correct?'Matched':'Missed'}</td><td>${w.breakout?pct(w.breakout.success_rate):'No pattern'}</td><td>${esc(w.actual_outcome?.replaceAll('_',' ')||'No pattern')}</td></tr>`).join('');
  $('backtest-limits').textContent='Historical tests may overlap model pretraining data. Sampled breakout frequencies are uncalibrated. Use fresh forward outcomes to assess live prediction quality.';
  await renderWindow();
  renderSetups();
}
function renderRuns(){
  $('run-count').textContent=state.runs.length;
  $('run-rows').innerHTML=state.runs.length?state.runs.map(r=>{const s=r.request.settings||{},complete=r.status==='complete',exports=complete&&r.kind!=='scan';return `<tr><td>${esc(prettyDate(r.created))}<small>${esc(new Date(r.created).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}))}</small></td><td>${esc(r.kind==='scan'?'Setup scan':r.kind==='backtest'?'Backtest':'Forecast')}<small>${esc(r.request.symbol||'Cached universe')} · ${s.timeframe==='W'?'Weekly':'Daily'}</small></td><td>${esc(s.model||'—')}</td><td>${s.horizon||'—'} bars / ${s.samples||'—'} paths</td><td><span class="badge ${r.status==='failed'?'negative':complete?'positive':''}">${esc(r.status.toUpperCase())}</span>${r.error?'<small>'+esc(r.error)+'</small>':''}</td><td>${complete?`<button class="text-button" data-open-run="${r.id}">Open</button>`:''}${exports?`<a class="text-button" href="/api/runs/${r.id}?format=csv">CSV ↓</a> <a class="text-button" href="/api/runs/${r.id}?format=json">JSON ↓</a>`:''}</td></tr>`;}).join(''):'<tr><td colspan="6" class="muted">No runs yet. Generate a forecast or run a historical test.</td></tr>';
}
async function refreshRuns(){state.runs=await api('/api/runs');renderRuns();}
async function openRun(id){
  const run=await api('/api/runs/'+encodeURIComponent(id));
  if(run.status!=='complete'){notice(run.error||run.message,run.status==='failed');return;}
  if(run.kind==='scan'){state.setups=run.result.setups;renderSetups();showPage('research');notice(`Loaded ${run.result.setups.length} detected setups.`);return;}
  if(run.kind==='backtest'){showPage('performance');await renderBacktest(run.result,run.id);}
  else {showPage('research');await renderForecast(run.result,run.id);}
  window.history.replaceState(null,'','/ai?run='+run.id);
}
function setBusy(key){
  state.active=key;document.body.classList.toggle('busy',Boolean(key));$('job').classList.toggle('hidden',!key);
  for(const id of ['forecast-button','quick-forecast','run-backtest','scan-cache'])$(id).disabled=Boolean(key);
}
async function pollJob(){
  if(!state.active)return;
  try{
    const run=await api('/api/runs/'+state.active);
    $('job-title').textContent=run.kind==='backtest'?'Backtest running':run.kind==='scan'?'Scanning cached data':'Forecast running';
    $('job-message').textContent=run.message;$('job-progress').style.width=run.progress+'%';
    $('cancel-job').disabled=run.status==='cancelling';
    if(['complete','failed','cancelled','interrupted'].includes(run.status)){
      setBusy(null);await refreshRuns();
      if(run.status==='complete'){
        if(run.kind==='scan'){state.setups=run.result.setups;renderSetups();notice(`${run.result.setups.length} setups found in ${run.result.scanned} cached symbols.`);}
        else await openRun(run.id);
      }else {if(state.forecast)$('chart-state').textContent='PREVIOUS RESULT · NEW REVIEW NOT COMPLETED';notice(run.error||run.message,run.status==='failed');}
      return;
    }
  }catch(error){notice('Could not reach the local server. Keep the Kronos launcher window open.',true);}
  state.pollTimer=setTimeout(pollJob,1500);
}
async function startJob(kind){
  const settings=settingsFromForm();
  if(kind!=='scan'&&!state.symbol)throw new Error('Load a cached symbol first.');
  const request={kind,symbol:state.symbol,scanner:state.scanner,settings};
  if(kind==='forecast'&&$('asof').value)request.asof=$('asof').value;
  if(kind==='backtest'){request.start=$('test-start').value;request.end=$('test-end').value;request.max_windows=Number($('test-windows').value);}
  const run=await api('/api/jobs',request);
  state.settings=settings;$('settings-dirty').classList.add('hidden');$('notice').classList.add('hidden');
  $('job-message').textContent='Preparing market data and model';$('job-progress').style.width='0%';$('cancel-job').disabled=false;
  setBusy(run.id);await pollJob();
}

document.querySelectorAll('.nav-tab').forEach(button=>button.addEventListener('click',()=>showPage(button.dataset.page)));
$('notice').querySelector('button').onclick=()=>$('notice').classList.add('hidden');
$('setup-search').oninput=renderSetups;$('setup-filter').onchange=renderSetups;
$('setup-list').onclick=guarded(async event=>{const row=event.target.closest('[data-symbol]');if(!row)return;state.scanner=row.dataset.scanner;$('asof').value=row.dataset.date;showPage('research');await loadContext(row.dataset.symbol,row.dataset.date);});
$('load-symbol').onclick=guarded(async()=>{state.scanner='all';await loadContext($('symbol-input').value);});
$('symbol-input').addEventListener('keydown',guarded(async event=>{if(event.key==='Enter'){event.preventDefault();state.scanner='all';await loadContext($('symbol-input').value);}}));
$('asof').onchange=guarded(async()=>{if(state.symbol)await loadContext();});
$('latest').onclick=guarded(async()=>{$('asof').value='';state.scanner='all';await loadContext();});
document.querySelectorAll('[data-tf]').forEach(button=>button.onclick=guarded(async()=>{if(state.tf===button.dataset.tf)return;state.tf=button.dataset.tf;document.querySelectorAll('[data-tf]').forEach(b=>b.classList.toggle('selected',b===button));holdingOptions(state.tf==='W'?6:15);$('settings-form').elements.horizon.value=state.tf==='W'?6:15;state.scanner='all';markEdited();renderSetups();if(state.symbol)await loadContext();}));
$('settings-form').onsubmit=event=>event.preventDefault();$('settings-form').addEventListener('input',markEdited);
$('save-settings').onclick=guarded(async()=>{const settings=settingsFromForm();state.settings=await api('/api/settings',settings);$('settings-dirty').classList.add('hidden');notice('Forecast period saved to your one shared setup.');});
$('edit-period').onclick=()=>$('settings-form').scrollIntoView({behavior:'smooth',block:'center'});
$('test-this-setup').onclick=()=>{showPage('performance');$('test-end').value=state.forecast?.asof||state.context?.asof||$('test-end').value;};
$('forecast-button').onclick=guarded(()=>startJob('forecast'));
$('quick-forecast').onclick=guarded(()=>startJob('forecast'));
$('run-backtest').onclick=guarded(()=>startJob('backtest'));
$('scan-cache').onclick=guarded(()=>startJob('scan'));
$('cancel-job').onclick=guarded(async()=>{await api('/api/cancel/'+state.active,{});$('cancel-job').disabled=true;$('job-message').textContent='Stopping after the current batch…';});
$('candles-view').onclick=guarded(async()=>{state.candles=true;$('candles-view').classList.add('selected');$('line-view').classList.remove('selected');await drawResearch();});
$('line-view').onclick=guarded(async()=>{state.candles=false;$('line-view').classList.add('selected');$('candles-view').classList.remove('selected');await drawResearch();});
for(const id of ['show-volume','show-sma20','show-sma50','show-band'])$(id).onchange=guarded(drawResearch);
$('reset-chart').onclick=guarded(()=>KronosCharts.reset('price-chart'));
$('save-chart').onclick=guarded(()=>KronosCharts.save('price-chart'));
document.querySelectorAll('[data-range]').forEach(b=>b.onclick=guarded(async()=>{document.querySelectorAll('[data-range]').forEach(x=>x.classList.toggle('selected',x===b));await KronosCharts.range('price-chart',b.dataset.range);}));
$('refresh-comparison').onclick=guarded(async()=>{if(!state.forecastId)throw new Error('Generate or open a forecast first.');const run=await api('/api/runs/'+state.forecastId);state.forecast.comparison=run.result.comparison;renderComparison(run.result.comparison);await drawResearch();});
$('window-select').onchange=guarded(renderWindow);
$('window-rows').onclick=guarded(async event=>{const b=event.target.closest('[data-window]');if(b){$('window-select').value=b.dataset.window;await renderWindow();$('backtest-chart').scrollIntoView({behavior:'smooth',block:'center'});}});
$('refresh-runs').onclick=guarded(refreshRuns);
$('run-rows').onclick=guarded(async event=>{const b=event.target.closest('[data-open-run]');if(b)await openRun(b.dataset.openRun);});
for(const [id,format] of [['export-backtest-csv','csv'],['export-backtest-json','json']])$(id).onclick=()=>{if(state.backtestId)window.location.assign('/api/runs/'+state.backtestId+'?format='+format);};
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>KronosCharts.resize(),120);});

async function init(){
  const data=await api('/api/bootstrap');state.token=data.token;state.setups=data.setups;state.symbols=data.symbols;state.runs=data.runs;
  fillSettings(data.settings);renderSetups();renderRuns();
  $('symbol-options').innerHTML=data.symbols.map(s=>`<option value="${esc(s)}"></option>`).join('');
  if(!data.runtime_ready)notice('Kronos model packages are missing. Run the launcher to install them.',true);
  const requested=new URLSearchParams(location.search).get('run');
  if(requested)await openRun(requested);
  else if(new URLSearchParams(location.search).get('symbol')){const q=new URLSearchParams(location.search);state.tf=q.get('tf')==='W'?'W':'D';fillSettings({...data.settings,timeframe:state.tf,horizon:data.settings.timeframe===state.tf?data.settings.horizon:(state.tf==='W'?6:15)});state.scanner='all';$('asof').value='';await loadContext(q.get('symbol'),'');}
  else if(data.symbols.length){const first=data.setups.find(s=>s.timeframe===state.tf);state.scanner=first?.scanner||'all';$('asof').value='';await loadContext(first?.symbol||data.symbols[0],'');}
  else {notice('No cached market data found. Run your original scanner first.');await drawResearch();}
  if(data.active){setBusy(data.active);await pollJob();}
}
init().catch(error=>notice(error.message,true));

// Daily monitoring is server-side; closing this browser does not stop it.
async function refreshWatch(){
  const rows=await api('/api/watch');
  $('watch-rows').innerHTML=rows.length?rows.map(r=>{
    const c=r.quality,m=c?.metrics||{};
    const quality=c?.available?`Origin ${esc(r.quality_origin)} · ${c.available}/${c.required} actual bars · ${c.complete?'Complete':'Partial'}<small>MAE ${money(m.mae)} · baseline ${money(m.baseline_mae)} · range coverage ${pct(m.coverage_pct)}</small><small>${m.mae<m.baseline_mae?'Lower error than baseline':m.mae>m.baseline_mae?'Higher error than baseline':'Same error as baseline'} · this window only</small>`:'Waiting for actual prices';
    return `<tr><td>${esc(r.symbol)}<small>${esc(r.data_date||'Not checked')}</small></td><td>${r.enabled?esc(r.message):'Paused'}<small>Checked: ${r.checked?esc(new Date(r.checked).toLocaleString('en-IN')):'—'}</small></td><td>${esc(r.alert||'No forecast yet')}</td><td>${quality}</td><td>${r.last_success?`<button class="text-button" data-watch-open="${esc(r.last_success)}">Open forecast</button>`:''}<button class="text-button" data-watch-symbol="${esc(r.symbol)}" data-enable="${!r.enabled}">${r.enabled?'Pause':'Resume'}</button></td></tr>`;
  }).join(''):'<tr><td colspan="5">Choose a stock, then click Watch selected stock.</td></tr>';
}
$('watch-symbol').onclick=guarded(async()=>{
  if(!state.symbol)throw new Error('Choose a stock first.');
  if(state.tf!=='D')throw new Error('Choose Daily before adding a stock to daily watch.');
  const settings=settingsFromForm();
  await api('/api/settings',settings);state.settings=settings;
  await api('/api/watch',{symbol:state.symbol,enabled:true});
  await refreshWatch();notice('Daily watch enabled. The first check starts within one minute.');
});
$('watch-rows').onclick=guarded(async event=>{
  const open=event.target.closest('[data-watch-open]');if(open){await openRun(open.dataset.watchOpen);return;}
  const button=event.target.closest('[data-watch-symbol]');
  if(button){await api('/api/watch',{symbol:button.dataset.watchSymbol,enabled:button.dataset.enable==='true'});await refreshWatch();}
});
async function watchLoop(){
  try{await refreshWatch();}catch(error){$('watch-rows').innerHTML='<tr><td colspan="5">Daily watch unavailable. Keep the app running and refresh after a restart.</td></tr>';}
  setTimeout(watchLoop,15000);
}
watchLoop();


if(window.self!==window.top)document.body.classList.add('embedded-research');
