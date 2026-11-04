'use strict';
const $ = id => document.getElementById(id);
const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => typeof n === 'number' && Number.isFinite(n) ? n.toLocaleString('en-IN', {maximumFractionDigits:2}) : '—';
const when = value => value ? new Date(value).toLocaleString('en-IN', {timeZone:'Asia/Kolkata'}) + ' IST' : 'Time unknown';
const titles = {stage:'Stage 1 → 2',darvas:'Darvas box',cup:'Cup & handle',stage_bear:'Stage 3 → 4',darvas_bear:'Darvas breakdown',cup_bear:'Inverted cup & handle'};
const filters = ['search','list','direction','timeframe','review','sort'];
let setups = [], entries = {}, quotes = {}, token = '', shown = [], conflicts = new Set(), editing = '', busy = false;
const pendingPins = new Set();
try {
  const saved = JSON.parse(localStorage.getItem('scanner-shortlist-filters') || '{}');
  for (const id of filters) if (typeof saved[id] === 'string') {
    if (id === 'search' || [...$(id).options].some(o => o.value === saved[id])) $(id).value = saved[id];
  }
} catch (_) { /* Filters are optional; pins and notes live on the server. */ }
function status(message, error = false) { $('status').textContent = message; $('status').dataset.error = String(error); }
async function api(path, body) {
  const response = await fetch(path, {cache:'no-store', ...(body === undefined ? {} : {
    method:'POST', headers:{'Content-Type':'application/json','X-Kronos-Token':token}, body:JSON.stringify(body)
  })});
  if (response.status === 404) throw new Error('Restart Run Pattern Scanners.bat to load the new shortlist service.');
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed. Please retry.');
  return result;
}
function allRows() {
  const present = new Set(setups.map(r => r.symbol));
  return [...setups, ...Object.values(entries).filter(e => (e.pinned || e.note) && !present.has(e.symbol)).map(e => ({symbol:e.symbol}))];
}
function baseMatch(row) {
  const entry = entries[row.symbol] || {}, query = $('search').value.trim().toLowerCase();
  return (!$('search').value.trim() || `${row.symbol} ${entry.note || ''}`.toLowerCase().includes(query)) &&
    ($('list').value === 'all' || entry.pinned) &&
    ($('direction').value === 'all' || row.direction === $('direction').value) &&
    ($('timeframe').value === 'all' || row.timeframe === $('timeframe').value);
}
function assessed(row) { const q = quotes[row.symbol]; return {...row, ...ShortlistModel.assess(row, q, q?.market_open)}; }
function render() {
  const focused = document.activeElement;
  const focusKey = focused?.matches('[data-pin],[data-note],[data-ai]') ?
    {attribute:focused.hasAttribute('data-pin') ? 'data-pin' : focused.hasAttribute('data-note') ? 'data-note' : 'data-ai',symbol:focused.getAttribute('data-pin') || focused.getAttribute('data-note') || focused.getAttribute('data-ai')} : null;
  const rows = allRows().map(assessed);
  const countStocks = predicate => new Set(rows.filter(predicate).map(r => r.symbol)).size;
  $('count-saved').textContent = Object.values(entries).filter(e => e.pinned).length;
  $('count-near').textContent = countStocks(r => r.review === 'Near trigger');
  $('count-stop').textContent = countStocks(r => r.review === 'Past stop');
  $('count-conflict').textContent = conflicts.size;
  shown = rows.filter(baseMatch).filter(r => $('review').value === 'all' || r.review === $('review').value);
  shown.sort((a,b) => {
    if ($('sort').value === 'score') return (b.score ?? -1) - (a.score ?? -1) || a.symbol.localeCompare(b.symbol);
    if ($('sort').value === 'symbol') return a.symbol.localeCompare(b.symbol);
    return (a.distance == null ? Infinity : Math.abs(a.distance)) - (b.distance == null ? Infinity : Math.abs(b.distance)) || a.symbol.localeCompare(b.symbol);
  });
  $('count').textContent = `${shown.length} setups · ${new Set(shown.map(r => r.symbol)).size} stocks shown`;
  $('rows').innerHTML = shown.map(r => {
    const entry = entries[r.symbol] || {}, q = quotes[r.symbol];
    const date = r.date ? r.date.slice(0,10) : '';
    const badge = r.review === 'Near trigger' ? 'near' : r.review === 'Past stop' ? 'stop' : ['Past trigger','Check quote','Check levels'].includes(r.review) ? 'warn' : '';
    const quoteTime = q?.quote_time ? when(q.quote_time * 1000) : q?.reported_trade_time ? `${q.reported_trade_time} IST · date unknown` : '';
    return `<tr><td><button class="pin" data-pin="${esc(r.symbol)}" aria-label="${entry.pinned ? 'Unpin' : 'Pin'} ${esc(r.symbol)}" aria-pressed="${!!entry.pinned}" ${pendingPins.has(r.symbol) ? 'disabled' : ''}>${entry.pinned ? '★' : '☆'}</button></td>
      <td><b>${esc(r.symbol)}</b><small>${esc(titles[r.scanner] || r.scanner || 'No current scanner setup')}${r.timeframe ? ' · ' + esc(r.timeframe) : ''}</small>${date ? `<small>${r.direction === 'short' ? 'Bearish' : 'Bullish'} · Score ${num(r.score)} · ${esc(date)}</small>` : ''}${conflicts.has(r.symbol) ? '<small class="conflict">Both directions — compare timeframes</small>' : ''}</td>
      <td>${r.price == null ? '—' : '₹' + num(r.price)}<small>${esc(r.source)}${q?.source ? ' · ' + esc(q.source) : ''}</small><small>${esc(quoteTime || (r.source !== 'Saved close' ? 'Quote date/time unknown' : date ? 'Close dated ' + date : ''))}</small></td>
      <td>${num(r.pivot)}</td><td>${num(r.support)}</td><td>${r.distance == null ? '—' : num(r.distance) + '%'}${r.distance < 0 ? '<small>Beyond trigger</small>' : ''}</td>
      <td><span class="badge ${badge}">${esc(r.review)}</span>${r.review === 'Check quote' ? `<small>Reference: ${esc(r.state.toLowerCase())}</small>` : ''}${r.review === 'Past trigger' ? '<small>Wait for candle confirmation</small>' : ''}</td>
      <td><button class="note-button" data-note="${esc(r.symbol)}">${esc(entry.note || '+ Add note')}</button></td>
      <td><div class="links"><a href="https://www.tradingview.com/chart/?symbol=${encodeURIComponent('NSE:' + r.symbol.replace(/[-&]/g,'_'))}" target="_blank" rel="noopener">Chart ↗</a>${r.direction === 'long' ? `<a href="/ai?${new URLSearchParams({symbol:r.symbol,tf:r.timeframe || 'D'})}" data-ai="${esc(r.symbol)}" data-tf="${esc(r.timeframe)}">AI</a>` : ''}</div></td></tr>`;
  }).join('') || '<tr><td colspan="9" class="empty">No setups match. Clear filters or refresh the scanner. Pinned stocks stay saved even when their pattern disappears.</td></tr>';
  if (focusKey) [...$('rows').querySelectorAll('[' + focusKey.attribute + ']')].find(el => el.getAttribute(focusKey.attribute) === focusKey.symbol)?.focus({preventScroll:true});
}
async function load() {
  const data = await api('/api/shortlist');
  setups = data.setups; entries = data.entries; token = data.token;
  const directions = new Map();
  for (const row of setups) { if (!directions.has(row.symbol)) directions.set(row.symbol,new Set()); directions.get(row.symbol).add(row.direction); }
  conflicts = new Set([...directions].filter(([,s]) => s.size > 1).map(([symbol]) => symbol));
  render();
}
async function refresh() {
  if (busy) return;
  busy = true; $('refresh').disabled = true;
  try {
    status('Loading saved setups and current quotes…');
    await load();
    const names = [...new Set(allRows().filter(baseMatch).map(r => r.symbol))];
    let missing = 0, failures = 0, received = 0;
    for (let i = 0; i < names.length; i += 50) {
      const batch = names.slice(i,i+50);
      // A failed request must not keep the previous batch labelled current.
      for (const symbol of batch) if (quotes[symbol]) quotes[symbol].fresh = false;
      try {
        const result = await api('/api/quotes?' + new URLSearchParams({symbols:batch.join(',')}));
        for (const symbol of batch) {
          const q = result.quotes?.[symbol];
          if (q) { quotes[symbol] = {...q, market_open:result.market_open}; received++; }
          else { delete quotes[symbol]; missing++; }
        }
      } catch (_) { failures += batch.length; }
      render();
    }
    status(names.length ? `Checked ${received}/${names.length} stocks at ${when(Date.now())}.${missing ? ` ${missing} quotes unavailable.` : ''}${failures ? ` ${failures} quotes failed; check broker login and retry.` : ''} Closed-market or undated prices are reference only.` : 'No stocks in this view. Clear filters or pin stocks from All setups.', missing + failures > 0);
  } catch (error) { status(error.message,true); }
  finally { busy = false; $('refresh').disabled = false; render(); }
}
$('refresh').onclick = refresh;
for (const id of filters) $(id).addEventListener(id === 'search' ? 'input' : 'change', () => {
  try { localStorage.setItem('scanner-shortlist-filters',JSON.stringify(Object.fromEntries(filters.map(key => [key,$(key).value])))); } catch (_) {}
  render();
});
$('clear').onclick = () => {
  for (const id of filters) $(id).value = id === 'search' ? '' : id === 'sort' ? 'distance' : 'all';
  try { localStorage.removeItem('scanner-shortlist-filters'); } catch (_) {}
  render();
};
$('rows').addEventListener('click', async event => {
  const pin = event.target.closest('[data-pin]'), note = event.target.closest('[data-note]'), ai = event.target.closest('[data-ai]');
  if (pin) {
    const symbol = pin.dataset.pin;
    if (pendingPins.has(symbol)) return;
    pendingPins.add(symbol); render();
    try { entries[symbol] = await api('/api/shortlist',{symbol,pinned:!entries[symbol]?.pinned}); }
    catch (error) { status(error.message,true); }
    finally { pendingPins.delete(symbol); render(); }
  }
  if (note) {
    editing = note.dataset.note; $('note-title').textContent = editing + ' · review note';
    $('note').value = entries[editing]?.note || ''; $('note-error').textContent = '';
    $('note-dialog').showModal(); $('note').focus();
  }
  if (ai && window.parent !== window) {
    event.preventDefault(); window.parent.postMessage({type:'open-research',symbol:ai.dataset.ai,timeframe:ai.dataset.tf},location.origin);
  }
});
$('cancel-note').onclick = () => $('note-dialog').close();
$('note-form').onsubmit = async event => {
  event.preventDefault(); $('save-note').disabled = true;
  try {
    entries[editing] = await api('/api/shortlist',{symbol:editing,note:$('note').value});
    $('note-dialog').close(); render();
  } catch (error) { $('note-error').textContent = error.message; }
  finally { $('save-note').disabled = false; }
};
$('export').onclick = () => {
  render(); // Recheck quote age at export time.
  const columns = ['Stock','Pinned','Direction','Pattern','Timeframe','Setup date','Score','Price','Price source','Quote provider','Quote time IST','Fetched IST','Trigger','Stop','To trigger %','Review','Reference state','Both directions','Note'];
  const rows = shown.map(r => { const q = quotes[r.symbol]; return [r.symbol,!!entries[r.symbol]?.pinned,r.direction,r.scanner,r.timeframe,r.date,r.score,r.price,r.source,q?.source,q?.quote_time ? when(q.quote_time * 1000) : 'Unknown',q?.fetched_at ? when(q.fetched_at) : '',r.pivot,r.support,r.distance,r.review,r.state,conflicts.has(r.symbol),entries[r.symbol]?.note]; });
  const csv = '\uFEFF' + [columns,...rows].map(row => row.map(ShortlistModel.csvCell).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})), link = document.createElement('a');
  link.href = url; link.download = 'trading-shortlist.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
};
window.addEventListener('message', event => {
  if (event.origin !== location.origin) return;
  if (event.data === 'patterns-updated') load().catch(error => status(error.message,true));
  if (event.data === 'refresh-prices') render();
});
setInterval(() => { if (!document.hidden && !window.frameElement?.hidden) render(); },15000);
load().then(() => status('Saved setups loaded. Refresh quotes to check current trigger and stop levels.')).catch(error => status(error.message,true));
