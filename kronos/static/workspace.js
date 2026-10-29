'use strict';
const scanner=document.getElementById('scanner-frame'),ai=document.getElementById('ai-frame');
function showAI(symbol,tf){
  if(symbol)ai.src='/ai?symbol='+encodeURIComponent(symbol)+'&tf='+encodeURIComponent(tf||'D');
  else if(!ai.getAttribute('src'))ai.src='/ai';
  show('ai');
}
function show(view){
  scanner.hidden=view!=='scanner';ai.hidden=view!=='ai';
  document.getElementById('scanner-tab').setAttribute('aria-selected',view==='scanner');
  document.getElementById('ai-tab').setAttribute('aria-selected',view==='ai');
}
document.getElementById('scanner-tab').onclick=()=>show('scanner');
document.getElementById('ai-tab').onclick=()=>showAI();
document.getElementById('refresh-report').onclick=()=>{scanner.src='/scanner?refresh='+Date.now();show('scanner');};
scanner.onload=()=>{
  const doc=scanner.contentDocument;
  function addButtons(){
    doc.querySelectorAll('td.kronos-cell').forEach(cell=>{
      if(cell.dataset.sai)return;
      cell.dataset.sai='true';
      const row=cell.closest('tr'),symbol=row.querySelector('td.sym a')?.textContent.trim(),tf=row.querySelector('.tf')?.textContent.trim();
      cell.textContent='Open AI prediction →';cell.title='Review current Kronos predictions and quality';
      cell.onclick=()=>showAI(symbol,tf);
    });
    const heading=doc.querySelector('th[data-k="kronos"]');
    if(heading&&heading.textContent!=='AI PREDICTION')heading.textContent='AI PREDICTION';
    doc.querySelectorAll('td.sym').forEach(cell=>{
      if(cell.querySelector('.sai-predict'))return;
      const symbol=cell.querySelector('a')?.textContent.trim(),tf=cell.querySelector('.tf')?.textContent.trim();
      if(!symbol)return;
      const button=doc.createElement('button');button.className='sai-predict';button.textContent='AI ↗';button.title='Analyze '+symbol+' with Kronos';
      button.style.cssText='margin-left:8px;padding:3px 7px;font:inherit;font-size:11px;border:1px solid #d6d3d1;border-radius:6px;background:#fafaf9;color:#1c1917;cursor:pointer';
      button.onclick=()=>showAI(symbol,tf);cell.appendChild(button);
    });
  }
  addButtons();
  const table=doc.getElementById('table');if(table)new doc.defaultView.MutationObserver(addButtons).observe(table,{childList:true,subtree:true});
};
