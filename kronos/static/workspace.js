'use strict';
const scanner=document.getElementById('scanner-frame'),ai=document.getElementById('ai-frame'),options=document.getElementById('options-frame');
const optionsTab=document.createElement('button');optionsTab.id='options-tab';optionsTab.textContent='Options desk';optionsTab.setAttribute('aria-selected','false');document.querySelector('nav').appendChild(optionsTab);
const shortlist=document.getElementById('shortlist-frame');
const shortlistTab=document.createElement('button');shortlistTab.id='shortlist-tab';shortlistTab.textContent='Shortlist';shortlistTab.setAttribute('aria-selected','false');optionsTab.before(shortlistTab);
shortlistTab.onclick=()=>{if(!shortlist.getAttribute('src'))shortlist.src='/shortlist.html';show('shortlist');};
let scannerExpanded=false;
let scannerQuotes={},priceLoading=false,quotedSymbols='',marketToken='',revision=null,scannerObserver=null;
function scannerSymbols(){return [...new Set([...(scanner.contentDocument?.querySelectorAll('td.sym a')||[])].map(a=>a.textContent.trim()))];}
function show(view){
  if(!['scanner','shortlist','options','ai'].includes(view))view='scanner';
  if(view==='shortlist'&&!shortlist.getAttribute('src'))shortlist.src='/shortlist.html';
  if(view==='options'&&!options.getAttribute('src'))options.src='/options';
  if(view==='ai'&&!ai.getAttribute('src'))ai.src='/ai';
  history.replaceState(null,'','#'+view);
  scanner.hidden=view!=='scanner';ai.hidden=view!=='ai';options.hidden=view!=='options';
  shortlist.hidden=view!=='shortlist';shortlistTab.setAttribute('aria-selected',view==='shortlist');
  optionsTab.setAttribute('aria-selected',view==='options');
  document.getElementById('scanner-tab').setAttribute('aria-selected',view==='scanner');
  if(view==='scanner')refreshScannerPrices();else (view==='ai'?ai:view==='shortlist'?shortlist:options).contentWindow?.postMessage('refresh-prices',location.origin);
}
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==shortlist.contentWindow||event.data?.type!=='open-research')return;
  const symbol=event.data.symbol;if(typeof symbol!=='string'||! /^[A-Z0-9&_-]{1,40}$/.test(symbol))return;
  ai.src='/ai?'+new URLSearchParams({symbol,tf:event.data.timeframe==='W'?'W':'D'});show('ai');
});
optionsTab.onclick=()=>{if(!options.getAttribute('src'))options.src='/options';show('options');};
document.getElementById('scanner-tab').onclick=()=>show('scanner');
document.getElementById('refresh-report').onclick=()=>startRefresh();
function paintQuotes(){
  const doc=scanner.contentDocument,table=doc?.querySelector('#table table');if(!table)return;
  if(!table.querySelector('th.api-ltp')){const th=doc.createElement('th');th.className='api-ltp';th.textContent='API LTP / time';th.style.minWidth='180px';const head=table.querySelector('thead tr');head.insertBefore(th,head.children[4]||null);}
  for(const row of table.querySelectorAll('tbody tr')){
    const symbol=row.querySelector('td.sym a')?.textContent.trim();if(!symbol)continue;
    let cell=row.querySelector('td.api-ltp');if(!cell){cell=doc.createElement('td');cell.className='api-ltp';cell.style.cssText='white-space:pre-line;min-width:180px';row.insertBefore(cell,row.children[4]||null);}
    const q=scannerQuotes[symbol],label=priceLoading?'Fetching…':q?'₹'+q.ltp.toLocaleString('en-IN',{maximumFractionDigits:2})+'\n'+(q.quote_time?(q.timestamp_kind==='quote'?'Quote ':'Trade ')+new Date(q.quote_time*1000).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'}):(q.reported_trade_time||'Time unavailable'))+' IST':'Unavailable';
    if(cell.textContent!==label)cell.textContent=label;
    cell.title=q?LivePrices.label(q):'No current API quote. Saved close remains in the Close column.';
  }
}
const refreshScannerPrices=LivePrices.watch(scannerSymbols,(quotes,loading)=>{scannerQuotes=quotes;priceLoading=loading;paintQuotes();},error=>{scannerQuotes={};priceLoading=false;paintQuotes();setStatus(error.message,true);});
scanner.onload=()=>{
  scannerObserver?.disconnect();
  const doc=scanner.contentDocument;
  const compactStyle=doc.createElement('style');compactStyle.textContent=`
    .wrap{max-width:none;padding:14px 20px 20px}h1{font-size:21px;margin:0 0 4px}.sub{font-size:11px;margin:0 0 12px}.tabs{gap:8px;margin:10px 0}.tab{padding:10px 14px}.tab .t{font-size:13px}.tab .c{font-size:11px}.desc{font-size:12px;margin:8px 0}.bar{gap:8px;margin:10px 0}.bar input,.bar button{padding:7px 9px}th,td{padding:8px 9px}th{font-size:10px}table{font-size:12px}.score i{display:none}.api-ltp{min-width:145px!important;font-size:11px}.sai-extra{display:none!important}th[data-k=kronos],td.kronos-cell{display:none!important}.sym{white-space:nowrap}.scroll{max-height:calc(100dvh - 230px);overflow:auto}footer{margin-top:12px;font-size:11px}.scanner-help{margin-top:12px;font-size:12px}.scanner-help summary{cursor:pointer}.scanner-help footer{padding:10px 0}.scanner-count{color:var(--muted);font-size:12px;white-space:nowrap}.bar .tv{max-width:300px;margin-left:auto}.sai-column-toggle{white-space:nowrap}@media(max-width:650px){.wrap{padding:12px 10px}.scroll{max-height:65dvh}.tabs{flex-wrap:wrap}.bar .tv{max-width:100%;margin-left:0}}`;
  doc.head.appendChild(compactStyle);
  const bar=doc.querySelector('.bar'),columnButton=doc.createElement('button');columnButton.type='button';columnButton.className='sai-column-toggle';
  const count=doc.createElement('span');count.className='scanner-count';count.setAttribute('role','status');
  const reset=doc.createElement('button');reset.type='button';reset.textContent='Clear filters';
  reset.onclick=()=>{for(const [id,value] of [['q',''],['minscore','0'],['maxdist','']]){const el=doc.getElementById(id);if(el){el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));}}};
  bar?.append(reset,columnButton,count);
  const footer=doc.querySelector('footer');if(footer){const help=doc.createElement('details'),summary=doc.createElement('summary');help.className='scanner-help';summary.textContent='Column guide & data notes';footer.before(help);help.append(summary,footer);}
  function compactColumns(){
    const table=doc.querySelector('#table table');if(!table){count.textContent='0 results';return;}
    const essentials=new Set(['symbol','score','close','pivot','distance','support','risk','analysis']);
    const heads=[...table.querySelectorAll('thead th')];
    heads.forEach((th,i)=>{const hide=!scannerExpanded&&!essentials.has(th.dataset.k)&&!th.classList.contains('api-ltp');th.classList.toggle('sai-extra',hide);for(const row of table.tBodies[0]?.rows||[])row.cells[i]?.classList.toggle('sai-extra',hide);});
    columnButton.textContent=scannerExpanded?'Key columns':'All columns';columnButton.setAttribute('aria-pressed',String(scannerExpanded));
    count.textContent=(table.tBodies[0]?.rows.length||0)+' results';
  }
  columnButton.onclick=()=>{scannerExpanded=!scannerExpanded;compactColumns();};
  const table=doc.getElementById('table');if(table?.isConnected){scannerObserver=new MutationObserver(()=>{
    paintQuotes();compactColumns();const names=scannerSymbols().join(',');if(names!==quotedSymbols){quotedSymbols=names;refreshScannerPrices();}
  });scannerObserver.observe(table,{childList:true,subtree:true});}
  paintQuotes();compactColumns();quotedSymbols=scannerSymbols().join(',');refreshScannerPrices();
};
function setStatus(text,error=false){const el=document.getElementById('market-status');el.textContent=text;el.dataset.error=error;}
async function refreshStatus(){
  const response=await fetch('/api/market/status',{cache:'no-store'}),data=await response.json();
  setStatus(data.message,data.status==='failed'||data.status==='partial');
  if(data.revision&&data.revision!==revision){revision=data.revision;scanner.src='/scanner?refresh='+Date.now();for(const frame of [ai,options,shortlist])frame.contentWindow?.postMessage('patterns-updated',location.origin);}
  if(data.status==='running')setTimeout(()=>refreshStatus().catch(e=>setStatus(e.message,true)),2000);
}
async function startRefresh(){
  try{
    setStatus('Fetching prices and checking the latest completed candles…');
    if(!marketToken){const r=await fetch('/api/bootstrap',{cache:'no-store'});marketToken=(await r.json()).token;}
    const r=await fetch('/api/market/refresh',{method:'POST',headers:{'Content-Type':'application/json','X-Kronos-Token':marketToken},body:'{}'});
    const d=await r.json();if(!r.ok)throw new Error(d.error||'Market refresh failed.');
    refreshScannerPrices();for(const frame of [ai,options])frame.contentWindow?.postMessage('refresh-prices',location.origin);
    await refreshStatus();
  }catch(e){setStatus(e.message,true);}
}
show(location.hash.slice(1)||'scanner');
startRefresh();
