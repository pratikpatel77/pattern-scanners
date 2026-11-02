'use strict';
// Options Desk: live chain (one API call per refresh), strategy builder, payoff, paper positions.
const $ = id => document.getElementById(id);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ok = x => x != null && isFinite(x);
const num = (x, d = 2) => ok(x) ? Number(x).toLocaleString('en-IN', {minimumFractionDigits: d, maximumFractionDigits: d}) : '—';
const inr = x => ok(x) ? '₹' + Math.round(x).toLocaleString('en-IN') : '—';
const sinr = x => ok(x) ? (x > 0 ? '+' : x < 0 ? '−' : '') + '₹' + Math.abs(Math.round(x)).toLocaleString('en-IN') : '—';
const pct = x => ok(x) ? (x > 0 ? '+' : '') + Number(x).toFixed(2) + '%' : '';
const tone = x => !ok(x) || x === 0 ? '' : x > 0 ? 'up' : 'down';
const big = x => !ok(x) ? '—' : Math.abs(x) >= 1e7 ? (x / 1e7).toFixed(2) + 'Cr' : Math.abs(x) >= 1e5 ? (x / 1e5).toFixed(1) + 'L' : Math.abs(x) >= 1e3 ? (x / 1e3).toFixed(1) + 'K' : String(Math.round(x));
const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } };
const paused = () => document.hidden || !!window.frameElement?.hidden;

const S = {token: '', universe: [], ideas: [], ai: {}, paper: [], symbol: '', expiry: '', chain: null,
  legs: [], span: Number(store('desk-span')) || 12, target: 0, view: 'chain', built: '', quotes: {},
  aiData: {}, job: null, pendingIdea: null, posOpen: null, posChains: {}, posExpiry: {}, ctx: {}, monitor: null};

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {cache: 'no-store'} : {method: 'POST', cache: 'no-store',
    headers: {'Content-Type': 'application/json', 'X-Kronos-Token': S.token}, body: JSON.stringify(body)});
  const v = await r.json();
  if (!r.ok) throw new Error(v.error || 'Request failed');
  return v;
}
let toastTimer;
function toast(text, err = false) {
  const t = $('toast'); t.textContent = text; t.className = err ? 'err' : ''; t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, err ? 6000 : 3000);
}

// ---------- Black-Scholes (same 6% rate as the server) ----------
const cdf = x => (1 + erf(x / Math.SQRT2)) / 2;
function erf(x) { const s = Math.sign(x); x = Math.abs(x); const t = 1 / (1 + .3275911 * x);
  return s * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - .284496736) * t + .254829592) * t * Math.exp(-x * x)); }
const intrinsic = (type, k, s) => type === 'CE' ? Math.max(0, s - k) : Math.max(0, k - s);
function bs(s, k, days, iv, type) {
  if (days <= 0 || !iv) return intrinsic(type, k, s);
  const t = days / 365, v = Math.max(.01, iv / 100), r = .06;
  const d1 = (Math.log(Math.max(1e-5, s) / k) + (r + v * v / 2) * t) / (v * Math.sqrt(t)), d2 = d1 - v * Math.sqrt(t);
  const call = s * cdf(d1) - k * Math.exp(-r * t) * cdf(d2);
  return Math.max(0, type === 'CE' ? call : call - s + k * Math.exp(-r * t));
}

// ---------- search ----------
let pick = -1;
function searchMatches() {
  const q = $('search').value.trim().toUpperCase();
  const tags = new Map(S.ideas.map(i => [i.symbol, i.patterns.join(', ')]));
  const list = S.universe.filter(s => s.includes(q)).sort((a, b) => ((b === q) - (a === q)) || (b.startsWith(q) - a.startsWith(q)) || a.length - b.length || a.localeCompare(b));
  return list.slice(0, 40).map(s => ({s, tag: tags.get(s) || ''}));
}
function renderSearch() {
  const items = searchMatches(), ul = $('search-list');
  pick = Math.min(pick, items.length - 1);
  ul.innerHTML = items.map((it, i) => `<li role="option" data-s="${esc(it.s)}" aria-selected="${i === pick}"><span>${esc(it.s)}</span><small>${esc(it.tag)}</small></li>`).join('') || '<li><small>No F&O stock matches</small></li>';
  ul.hidden = false;
}
$('search').addEventListener('input', () => { pick = 0; renderSearch(); });
$('search').addEventListener('focus', () => { $('search').select(); pick = 0; renderSearch(); });
$('search').addEventListener('blur', () => setTimeout(() => $('search-list').hidden = true, 150));
$('search').addEventListener('keydown', e => {
  const items = searchMatches();
  if (e.key === 'ArrowDown') { pick = Math.min(items.length - 1, pick + 1); renderSearch(); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { pick = Math.max(0, pick - 1); renderSearch(); e.preventDefault(); }
  else if (e.key === 'Enter' && items[Math.max(0, pick)]) { choose(items[Math.max(0, pick)].s); }
  else if (e.key === 'Escape') { $('search').blur(); }
});
$('search-list').addEventListener('mousedown', e => { const li = e.target.closest('[data-s]'); if (li) choose(li.dataset.s); });
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement !== $('search')) { e.preventDefault(); $('search').focus(); }
});
function choose(symbol) {
  $('search').value = ''; $('search').blur(); $('search-list').hidden = true;
  loadSymbol(symbol);
}

// ---------- symbol & chain polling ----------
function loadSymbol(symbol) {
  if (!symbol) return;
  if (symbol !== S.symbol) { S.legs = []; S.expiry = ''; S.chain = null; S.built = ''; S.target = 0; }
  S.symbol = symbol; store('desk-symbol', symbol);
  const recent = [symbol, ...(JSON.parse(store('desk-recent') || '[]')).filter(s => s !== symbol)].slice(0, 8);
  store('desk-recent', JSON.stringify(recent));
  $('sym').textContent = symbol; $('spot').textContent = '…'; $('spot-ch').textContent = '';
  $('chain').innerHTML = '<tr><td colspan="11" class="empty">Loading option chain…</td></tr>';
  renderAI(); renderWatch(); renderLegs(); pollChain(); fetchAI(symbol); renderContext(); fetchContext(symbol);
}
let chainTimer = null, chainSeq = 0;
async function pollChain() {
  clearTimeout(chainTimer);
  if (!S.symbol) return;
  const seq = ++chainSeq;
  if (!paused()) {
    try {
      const c = await api('/api/desk/chain?' + new URLSearchParams({symbol: S.symbol, expiry: S.expiry}));
      if (seq !== chainSeq || c.symbol !== S.symbol) return;
      onChain(c);
    } catch (e) {
      if (seq !== chainSeq) return;
      setLive('error', e.message);
      if (!S.chain) $('chain').innerHTML = `<tr><td colspan="11" class="empty">${esc(e.message)}</td></tr>`;
    }
  }
  if (seq === chainSeq) chainTimer = setTimeout(pollChain, paused() ? 1000 : S.chain && !S.chain.market_open ? 20000 : 1500);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) { pollChain(); pollWatch(); } });
window.addEventListener('message', e => { if (e.origin === location.origin && e.data === 'refresh-prices') { pollChain(); pollWatch(); } });

let liveClock = null;
function setLive(state, text) {
  const el = $('live'); el.dataset.state = state; $('live-text').textContent = text;
  if (state === 'live') { el.classList.remove('tick'); void el.offsetWidth; el.classList.add('tick'); }
}
function liveAge() {
  if (!S.chain || $('live').dataset.state === 'error') return;
  const age = Math.max(0, Math.round((Date.now() - S.chainAt) / 1000));
  if (S.chain.stale) setLive('stale', `FYERS chain unavailable · stock price via ${S.chain.spot.source}`);
  else if (!S.chain.market_open) setLive('closed', 'Market closed · last prices');
  else $('live-text').textContent = `Live · ${age < 2 ? 'just now' : age + 's ago'}`;
}
setInterval(liveAge, 1000);

function onChain(c) {
  const prev = S.chain; S.chain = c; S.chainAt = Date.now();
  if (!S.expiry && c.expiry) S.expiry = c.expiry.ts;
  // header
  const sp = $('spot'); const old = prev && prev.symbol === c.symbol ? prev.spot.ltp : null;
  sp.textContent = num(c.spot.ltp);
  if (old != null && old !== c.spot.ltp) flash(sp, c.spot.ltp > old);
  $('spot-ch').textContent = `${c.spot.ch > 0 ? '+' : ''}${num(c.spot.ch)} (${pct(c.spot.chp)})`;
  $('spot-ch').className = 'ch ' + tone(c.spot.ch);
  $('f-fut').textContent = num(c.spot.fut); $('f-lot').textContent = c.lot ? c.lot.toLocaleString('en-IN') : '—';
  $('f-iv').textContent = ok(c.atm_iv) ? num(c.atm_iv, 1) + '%' : '—'; $('f-pcr').textContent = num(c.pcr);
  $('f-pain').textContent = ok(c.max_pain) ? num(c.max_pain, c.max_pain % 1 ? 1 : 0) : '—';
  $('f-dte').textContent = num(c.days, 1) + 'd'; $('f-vix').textContent = num(c.vix);
  document.title = `${c.symbol} ${num(c.spot.ltp)} · Options Desk`;
  const sel = $('expiry'), opts = c.expiries.map(e => `<option value="${e.ts}">${esc(e.date)}</option>`).join('');
  if (sel.dataset.opts !== opts) { sel.innerHTML = opts; sel.dataset.opts = opts; }
  sel.value = c.expiry?.ts || '';
  $('days').max = Math.max(0, Math.floor(c.days)); if (S.target > c.days) S.target = 0;
  liveAge(); if (c.market_open && !c.stale) setLive('live', $('live-text').textContent || 'Live');
  // chain table: rebuild only when the visible window changes, otherwise update cells in place
  const rows = windowRows(c), key = [c.symbol, c.expiry_date, S.span, rows[0]?.strike, rows.at(-1)?.strike, spotSlot(rows, c.spot.ltp)].join('|');
  if (key !== S.built) { buildChain(c, rows); S.built = key; if (!prev || prev.symbol !== c.symbol || prev.expiry_date !== c.expiry_date) centreATM(); }
  updateChain(c, rows, prev);
  syncLegs();
  if (S.pendingIdea && c.expiry_date === S.pendingIdea.expiry) { const idea = S.pendingIdea; S.pendingIdea = null; loadIdea(idea); }
  updateLegPrices(); renderSummary();
  if (S.view === 'ai' && !prev) renderAIPane();
}
function windowRows(c) {
  const i = c.rows.findIndex(r => r.strike === c.atm), n = S.span;
  return n >= 99 ? c.rows : c.rows.slice(Math.max(0, i - n), i + n + 1);
}
const spotSlot = (rows, spot) => rows.findIndex(r => r.strike > spot);
function centreATM() {
  const row = $('chain').querySelector('tr.atm'), pane = document.querySelector('.chain-pane');
  if (row && pane) pane.scrollTop = row.offsetTop - pane.clientHeight / 2 + row.offsetHeight;
}
const CELLS = side => side === 'CE'
  ? `<td class="c oi" data-f="CE.oi"></td><td class="c oich" data-f="CE.oich"></td><td class="c" data-f="CE.iv"></td><td class="c dl" data-f="CE.delta"></td><td class="c ltp" data-cell="CE"><span class="bs"><button data-side="1" data-t="CE" title="Buy call">B</button><button data-side="-1" data-t="CE" title="Sell call">S</button></span><b data-f="CE.ltp"></b><small data-f="CE.chp"></small></td>`
  : `<td class="p ltp" data-cell="PE"><b data-f="PE.ltp"></b><small data-f="PE.chp"></small><span class="bs"><button data-side="1" data-t="PE" title="Buy put">B</button><button data-side="-1" data-t="PE" title="Sell put">S</button></span></td><td class="p dl" data-f="PE.delta"></td><td class="p" data-f="PE.iv"></td><td class="p oich" data-f="PE.oich"></td><td class="p oi" data-f="PE.oi"></td>`;
function buildChain(c, rows) {
  const slot = spotSlot(rows, c.spot.ltp);
  $('chain').innerHTML = rows.map((r, i) =>
    (i === slot ? `<tr class="spot-row"><td colspan="11"><span id="spot-tag">${num(c.spot.ltp)}</span></td></tr>` : '') +
    `<tr data-k="${r.strike}" class="${r.strike === c.atm ? 'atm' : ''}">${CELLS('CE')}<td class="k">${num(r.strike, r.strike % 1 ? 1 : 0)}</td>${CELLS('PE')}</tr>`).join('');
}
function updateChain(c, rows, prev) {
  const before = new Map((prev && prev.symbol === c.symbol ? prev.rows : []).map(r => [r.strike, r]));
  const maxOI = Math.max(1, ...rows.flatMap(r => [r.CE?.oi || 0, r.PE?.oi || 0]));
  if ($('spot-tag')) $('spot-tag').textContent = num(c.spot.ltp);
  const held = new Map(S.legs.map(l => [l.symbol, l]));
  for (const tr of $('chain').querySelectorAll('tr[data-k]')) {
    const k = Number(tr.dataset.k), r = rows.find(x => x.strike === k); if (!r) continue;
    const p = before.get(k);
    for (const side of ['CE', 'PE']) {
      const o = r[side] || {}, q = p?.[side];
      const itm = side === 'CE' ? k < c.spot.ltp : k > c.spot.ltp;
      tr.querySelectorAll(`td.${side === 'CE' ? 'c' : 'p'}`).forEach(td => td.classList.toggle('itm', itm));
      const set = (f, text, cls) => { const el = tr.querySelector(`[data-f="${side}.${f}"]`); if (el.textContent !== text) el.textContent = text; if (cls !== undefined) el.className = cls; };
      set('ltp', o.ltp ? num(o.ltp) : '—');
      set('chp', o.ltp ? pct(o.chp) : '', tone(o.chp));
      set('iv', ok(o.iv) ? num(o.iv, 1) : '—');
      set('delta', ok(o.delta) ? num(o.delta) : '—');
      set('oich', o.oich ? (o.oich > 0 ? '+' : '') + big(o.oich) : '—', tone(o.oich));
      const oi = tr.querySelector(`[data-f="${side}.oi"]`);
      if (oi.textContent !== big(o.oi)) oi.textContent = o.oi ? big(o.oi) : '—';
      oi.style.backgroundSize = `${Math.round((o.oi || 0) / maxOI * 100)}% 70%`;
      if (q && q.ltp && o.ltp && q.ltp !== o.ltp) flash(tr.querySelector(`[data-cell="${side}"]`), o.ltp > q.ltp);
      const cell = tr.querySelector(`[data-cell="${side}"]`), leg = held.get(o.symbol);
      cell.classList.toggle('held', !!leg); cell.classList.toggle('hb', leg?.side === 1); cell.classList.toggle('hs', leg?.side === -1);
      if (leg) cell.dataset.held = (leg.side > 0 ? 'B' : 'S') + leg.lots; else delete cell.dataset.held;
    }
  }
}
function flash(el, up) { if (!el) return; el.classList.remove('flash-up', 'flash-down'); void el.offsetWidth; el.classList.add(up ? 'flash-up' : 'flash-down'); }
$('chain').addEventListener('click', e => {
  const b = e.target.closest('button[data-side]'); if (!b) return;
  addLeg(b.dataset.t, Number(b.closest('tr').dataset.k), Number(b.dataset.side));
});
$('expiry').onchange = () => { S.expiry = $('expiry').value; S.built = ''; pollChain(); };
$('span').value = String(S.span);
$('span').onchange = () => { S.span = Number($('span').value); store('desk-span', S.span); S.built = ''; if (S.chain) onChain(S.chain); setTimeout(centreATM); };

// ---------- strategy builder ----------
function optionAt(type, strike) { return S.chain?.rows.find(r => r.strike === strike)?.[type]; }
function addLeg(type, strike, side, lots = 1) {
  const o = optionAt(type, strike); if (!o?.symbol) return toast('No contract at this strike.', true);
  const same = S.legs.find(l => l.symbol === o.symbol && l.side === side);
  if (same) same.lots += lots; else S.legs.push({symbol: o.symbol, type, strike, side, lots});
  renderLegs(); updateLegPrices(); renderSummary(); if (S.chain) updateChain(S.chain, windowRows(S.chain), null);
}
function syncLegs() {
  // Same strikes in the newly selected expiry; legs without a matching strike are dropped.
  let dropped = 0;
  S.legs = S.legs.filter(l => { const o = optionAt(l.type, l.strike); if (!o?.symbol) { dropped++; return false; } if (o.symbol !== l.symbol) l.symbol = o.symbol; return true; });
  if (dropped) { toast(`${dropped} leg(s) removed: strike not listed in this expiry.`, true); renderLegs(); }
}
function updateLegPrices() {
  for (const l of S.legs) { const o = optionAt(l.type, l.strike) || {}; l.price = o.ltp || 0; l.iv = o.iv || S.chain?.atm_iv || 25; l.bid = o.bid; l.ask = o.ask; }
  document.querySelectorAll('.leg').forEach((el, i) => { const l = S.legs[i]; if (l) el.querySelector('.px').innerHTML = `${num(l.price)}<small>${num(l.bid)} / ${num(l.ask)}</small>`; });
}
const PRESETS = [
  ['Buy call', [[1, 'CE', 0]]], ['Buy put', [[1, 'PE', 0]]],
  ['Bull call spread', [[1, 'CE', 0], [-1, 'CE', 2]]], ['Bear put spread', [[1, 'PE', 0], [-1, 'PE', -2]]],
  ['Bull put spread', [[-1, 'PE', 0], [1, 'PE', -2]]], ['Bear call spread', [[-1, 'CE', 0], [1, 'CE', 2]]],
  ['Long straddle', [[1, 'CE', 0], [1, 'PE', 0]]], ['Short straddle', [[-1, 'CE', 0], [-1, 'PE', 0]]],
  ['Short strangle', [[-1, 'CE', 2], [-1, 'PE', -2]]], ['Iron condor', [[1, 'PE', -4], [-1, 'PE', -2], [-1, 'CE', 2], [1, 'CE', 4]]],
  ['Iron fly', [[1, 'PE', -3], [-1, 'PE', 0], [-1, 'CE', 0], [1, 'CE', 3]]],
];
let strategyName = 'Custom strategy';
$('presets').innerHTML = PRESETS.map((p, i) => `<button data-p="${i}">${esc(p[0])}</button>`).join('');
$('presets').onclick = e => {
  const b = e.target.closest('[data-p]'); if (!b || !S.chain) return;
  const [name, legs] = PRESETS[b.dataset.p], rows = S.chain.rows, atm = rows.findIndex(r => r.strike === S.chain.atm);
  S.legs = [];
  for (const [side, type, off] of legs) { const r = rows[Math.min(rows.length - 1, Math.max(0, atm + off))]; if (r?.[type]?.symbol) S.legs.push({symbol: r[type].symbol, type, strike: r.strike, side, lots: 1}); }
  strategyName = name; renderLegs(); updateLegPrices(); renderSummary(); updateChain(S.chain, windowRows(S.chain), null);
};
function renderLegs() {
  if (!S.legs.length) strategyName = 'Custom strategy';
  $('legs').innerHTML = S.legs.length ? S.legs.map((l, i) => `<div class="leg" data-i="${i}">
      <button class="side ${l.side > 0 ? 'b' : 's'}" data-a="side" title="Switch buy / sell">${l.side > 0 ? 'B' : 'S'}</button>
      <span class="type">${l.type}</span>
      <span class="step"><button data-a="down" title="Lower strike">◀</button><span>${num(l.strike, l.strike % 1 ? 1 : 0)}</span><button data-a="up" title="Higher strike">▶</button></span>
      <span class="step lots"><button data-a="less" title="Fewer lots">−</button><span>${l.lots}</span><button data-a="more" title="More lots">+</button></span>
      <span class="px"></span>
      <button class="rm" data-a="rm" title="Remove leg">×</button></div>`).join('')
    : '<p class="hint">Hover a strike in the chain and click <b>B</b> or <b>S</b>, or pick a ready strategy above.</p>';
}
$('legs').onclick = e => {
  const b = e.target.closest('[data-a]'); if (!b) return;
  const i = Number(b.closest('.leg').dataset.i), l = S.legs[i], a = b.dataset.a;
  if (a === 'side') l.side = -l.side;
  if (a === 'more') l.lots = Math.min(50, l.lots + 1);
  if (a === 'less') { if (l.lots > 1) l.lots--; else S.legs.splice(i, 1); }
  if (a === 'rm') S.legs.splice(i, 1);
  if (a === 'up' || a === 'down') {
    const rows = S.chain.rows, j = rows.findIndex(r => r.strike === l.strike) + (a === 'up' ? 1 : -1), r = rows[j];
    if (r?.[l.type]?.symbol) { l.strike = r.strike; l.symbol = r[l.type].symbol; }
  }
  strategyName = 'Custom strategy';
  renderLegs(); updateLegPrices(); renderSummary(); if (S.chain) updateChain(S.chain, windowRows(S.chain), null);
};
$('clear').onclick = () => { S.legs = []; renderLegs(); renderSummary(); if (S.chain) updateChain(S.chain, windowRows(S.chain), null); };

// ---------- strategy maths: legs are {type, strike, qty (signed lots), lot, entry, iv} ----------
const lotSize = () => S.chain?.lot || 1;
const pnlExpiry = (legs, s, off = 0) => off + legs.reduce((t, l) => t + l.qty * l.lot * (intrinsic(l.type, l.strike, s) - l.entry), 0);
const pnlModel = (legs, s, days, bump = 0, off = 0) => off + legs.reduce((t, l) => t + l.qty * l.lot * (bs(s, l.strike, days, l.iv + bump, l.type) - l.entry), 0);
const builderLegs = () => S.legs.map(l => ({type: l.type, strike: l.strike, qty: l.side * l.lots, lot: lotSize(), entry: l.price, iv: l.iv}));
function stats(legs, spot, days, iv, off = 0) {
  const ks = [...new Set(legs.map(l => l.strike))].sort((a, b) => a - b);
  const pts = [0, ...ks, Math.max(...ks, spot) * 3];       // payoff is linear between strikes: extremes sit on these points
  const vals = pts.map(s => pnlExpiry(legs, s, off)), calls = legs.reduce((t, l) => t + (l.type === 'CE' ? l.qty : 0), 0);
  const be = [];
  for (let i = 1; i < pts.length; i++) { const a = vals[i - 1], b = vals[i]; if ((a < 0) !== (b < 0) && a !== b) be.push(pts[i - 1] + (pts[i] - pts[i - 1]) * (-a) / (b - a)); }
  const sigma = (iv || 25) / 100, T = Math.max(days, .04) / 365, drift = (.06 - sigma * sigma / 2) * T, sd = sigma * Math.sqrt(T);
  let pop = 0;
  for (let i = 0; i < 400; i++) {            // lognormal probability of finishing in profit at expiry
    const z0 = -5 + i * .025, s = spot * Math.exp(drift + sd * (z0 + .0125));
    if (pnlExpiry(legs, s, off) > 0) pop += cdf(z0 + .025) - cdf(z0);
  }
  const h = spot * .001, base = pnlModel(legs, spot, days);
  return {premium: -legs.reduce((t, l) => t + l.qty * l.lot * l.entry, 0), be, pop: pop * 100,
    maxProfit: calls > 0 ? Infinity : Math.max(...vals), maxLoss: calls < 0 ? -Infinity : Math.min(...vals),
    delta: (pnlModel(legs, spot + h, days) - pnlModel(legs, spot - h, days)) / (2 * h),
    theta: pnlModel(legs, spot, days - 1) - base, vega: pnlModel(legs, spot, days, 1) - base};
}
const cell = (k, v, cls = '') => `<div><small>${k}</small><strong class="${cls}">${v}</strong></div>`;
function aiBand(symbol) {
  const a = S.aiData[symbol]?.analysis, f = a?.forecast; if (!f?.rows?.length) return null;
  const end = f.rows.at(-1), s = f.summary || {};
  return {lo: s.end_q10 ?? end.q10, hi: s.end_q90 ?? end.q90, mid: s.median_end ?? end.median, date: end.date};
}
function renderSummary() {
  const box = $('summary'), trade = $('trade');
  const empty = !S.legs.length || !S.chain;
  for (const el of [document.querySelector('.chart-head'), $('payoff'), $('payoff-grip')]) el.hidden = empty;
  renderContext();
  if (empty) { box.innerHTML = ''; $('payoff').innerHTML = ''; trade.disabled = true; trade.textContent = 'Paper trade'; $('trade-note').textContent = 'Market-order fill: buy at ask, sell at bid (deeper levels if needed), plus brokerage and charges. No broker order is placed.'; return; }
  const c = S.chain, legs = builderLegs(), m = stats(legs, c.spot.ltp, c.days, c.atm_iv);
  const band = aiBand(S.symbol);
  box.innerHTML = cell(m.premium >= 0 ? 'Net credit' : 'Net debit', inr(Math.abs(m.premium)), m.premium >= 0 ? 'up' : '') +
    cell('Max profit', m.maxProfit === Infinity ? 'Unlimited' : inr(m.maxProfit), 'up') +
    cell('Max loss', m.maxLoss === -Infinity ? 'Unlimited' : inr(Math.abs(m.maxLoss)), 'down') +
    cell('Breakeven', m.be.length ? m.be.map(x => num(x, 1)).join(' · ') : '—') +
    cell('POP · model', num(m.pop, 0) + '%') +
    cell(band ? 'At AI median' : 'Δ / Θ day / Vega', band ? sinr(pnlModel(legs, band.mid, Math.max(0, c.days - daysUntil(band.date)))) : `${num(m.delta, 0)} / ${sinr(m.theta)} / ${sinr(m.vega)}`, band ? tone(pnlModel(legs, band.mid, Math.max(0, c.days - daysUntil(band.date)))) : '');
  drawChart($('payoff'), {legs, spot: c.spot.ltp, days: c.days, ahead: S.target, be: m.be, band});
  const open = c.market_open && !c.stale;
  trade.disabled = !open; trade.textContent = `Paper trade · ${S.legs.length} leg${S.legs.length > 1 ? 's' : ''}`;
  $('trade-note').textContent = open ? 'Market-order fill: buy at ask, sell at bid (deeper levels if needed), plus brokerage and charges. No broker order is placed.' : 'Paper entry opens in market hours with live two-sided quotes.';
}
$('days').oninput = () => { S.target = Number($('days').value); $('days-label').textContent = S.target ? `+${S.target}d` : 'Today'; renderSummary(); };
const todayIST = () => new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Kolkata'}).format(new Date());
const daysUntil = iso => Math.max(0, (new Date(iso + 'T15:30:00+05:30') - Date.now()) / 864e5);
const dmyToIso = d => d.split('-').reverse().join('-');
const shortDate = iso => new Date(iso + 'T12:00:00+05:30').toLocaleDateString('en-IN', {day: 'numeric', month: 'short'});

// ---------- payoff chart (SVG, no library) ----------
// cfg: legs, spot, days, ahead, off (fixed costs), be, band {lo,hi,mid}, hlines [{y,label,color}], vlines [{x,label,color}], now {x,y}
const hovers = {};
function drawChart(el, cfg) {
  const W = el.clientWidth || 380, H = el.clientHeight || 240, pad = {l: 52, r: 8, t: 12, b: 22};
  const {legs, spot, off = 0} = cfg, ks = legs.map(l => l.strike), iv = legs.reduce((t, l) => t + l.iv, 0) / legs.length || 25;
  const move = Math.min(.3, Math.max(.05, 2.2 * iv / 100 * Math.sqrt(Math.max(cfg.days, 1) / 365)));
  const extra = [...(cfg.band ? [cfg.band.lo, cfg.band.hi] : []), ...(cfg.vlines || []).map(v => v.x)].filter(ok);
  const lo = Math.min(spot * (1 - move), ...ks.map(k => k * .97), ...extra.map(x => x * .99)), hi = Math.max(spot * (1 + move), ...ks.map(k => k * 1.03), ...extra.map(x => x * 1.01));
  const N = 160, xs = Array.from({length: N + 1}, (_, i) => lo + (hi - lo) * i / N);
  const left = Math.max(0, cfg.days - (cfg.ahead || 0)), exp = xs.map(x => pnlExpiry(legs, x, off)), mod = xs.map(x => pnlModel(legs, x, left, 0, off));
  const ys = [0, ...exp, ...mod, ...(cfg.hlines || []).map(h => h.y), ...(cfg.now ? [cfg.now.y] : [])].filter(ok);
  let ymin = Math.min(...ys), ymax = Math.max(...ys); const yp = (ymax - ymin) * .08 || 100; ymin -= yp; ymax += yp;
  const X = x => pad.l + (x - lo) / (hi - lo) * (W - pad.l - pad.r), Y = y => pad.t + (ymax - y) / (ymax - ymin) * (H - pad.t - pad.b);
  const line = vs => xs.map((x, i) => `${X(x).toFixed(1)},${Y(vs[i]).toFixed(1)}`).join(' ');
  const z = Y(0), area = `${X(lo)},${z} ${line(exp)} ${X(hi)},${z}`, id = el.id;
  const yt = Array.from({length: 5}, (_, i) => ymin + (ymax - ymin) * i / 4), xt = Array.from({length: 5}, (_, i) => lo + (hi - lo) * i / 4);
  const b = cfg.band;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><clipPath id="${id}-u"><rect x="0" y="0" width="${W}" height="${z}"/></clipPath><clipPath id="${id}-d"><rect x="0" y="${z}" width="${W}" height="${H}"/></clipPath></defs>
    ${b ? `<rect x="${X(b.lo)}" y="${pad.t}" width="${Math.max(1, X(b.hi) - X(b.lo))}" height="${H - pad.t - pad.b}" fill="rgba(99,91,255,.08)"/><line x1="${X(b.mid)}" x2="${X(b.mid)}" y1="${pad.t}" y2="${H - pad.b}" stroke="#635bff" stroke-dasharray="2 3"/><text x="${X(b.lo) + 3}" y="${H - pad.b - 4}" style="fill:#635bff">AI 10–90%</text>` : ''}
    ${yt.map(v => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eef1f5"/><text x="${pad.l - 6}" y="${Y(v) + 3}" text-anchor="end">${big(v)}</text>`).join('')}
    ${xt.map(v => `<text x="${X(v)}" y="${H - 6}" text-anchor="middle">${num(v, 0)}</text>`).join('')}
    <polygon points="${area}" fill="rgba(10,155,106,.16)" clip-path="url(#${id}-u)"/><polygon points="${area}" fill="rgba(217,58,58,.14)" clip-path="url(#${id}-d)"/>
    <line x1="${pad.l}" x2="${W - pad.r}" y1="${z}" y2="${z}" stroke="#94a3b8"/>
    ${(cfg.hlines || []).map(h => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(h.y)}" y2="${Y(h.y)}" stroke="${h.color}" stroke-dasharray="6 4"/><text x="${W - pad.r - 2}" y="${Y(h.y) - 3}" text-anchor="end" style="fill:${h.color};font-weight:600">${esc(h.label)}</text>`).join('')}
    ${(cfg.vlines || []).map((v, i) => `<line x1="${X(v.x)}" x2="${X(v.x)}" y1="${pad.t}" y2="${H - pad.b}" stroke="${v.color}" stroke-dasharray="4 3"/><text x="${X(v.x) + 3}" y="${pad.t + 20 + i * 12}" style="fill:${v.color}">${esc(v.label)}</text>`).join('')}
    <polyline points="${line(exp)}" fill="none" stroke="#d93a3a" stroke-width="1.8" clip-path="url(#${id}-d)"/>
    <polyline points="${line(exp)}" fill="none" stroke="#0a9b6a" stroke-width="1.8" clip-path="url(#${id}-u)"/>
    <polyline points="${line(mod)}" fill="none" stroke="#2457d6" stroke-width="1.5" stroke-dasharray="5 4"/>
    <line x1="${X(spot)}" x2="${X(spot)}" y1="${pad.t}" y2="${H - pad.b}" stroke="#e0a100" stroke-dasharray="3 3"/>
    <text x="${X(spot)}" y="${pad.t - 2}" text-anchor="middle" style="fill:#a16d00;font-weight:600">${num(spot)}</text>
    ${(cfg.be || []).filter(v => v > lo && v < hi).map(v => `<circle cx="${X(v)}" cy="${z}" r="3" fill="#0f172a"/>`).join('')}
    ${cfg.now && ok(cfg.now.y) ? `<circle cx="${X(cfg.now.x)}" cy="${Y(cfg.now.y)}" r="5" fill="${cfg.now.y >= 0 ? '#0a9b6a' : '#d93a3a'}" stroke="#fff" stroke-width="2"/><text x="${X(cfg.now.x) + 8}" y="${Y(cfg.now.y) + 4}" style="font-weight:700;fill:${cfg.now.y >= 0 ? '#0a9b6a' : '#d93a3a'}">Now ${sinr(cfg.now.y)}</text>` : ''}
    <g class="hover"></g><rect class="hit" x="${pad.l}" y="0" width="${W - pad.l - pad.r}" height="${H}" fill="transparent"/>
  </svg><div class="tip" hidden></div>`;
  const hit = el.querySelector('.hit'), g = el.querySelector('.hover'), tip = el.querySelector('.tip');
  const tag = cfg.ahead ? '+' + cfg.ahead + 'd' : 'Today';
  const show = px => {
    if (px == null) { g.innerHTML = ''; tip.hidden = true; return; }
    const price = lo + (px - pad.l) / (W - pad.l - pad.r) * (hi - lo), e = pnlExpiry(legs, price, off), t = pnlModel(legs, price, left, 0, off);
    g.innerHTML = `<line x1="${px}" x2="${px}" y1="${pad.t}" y2="${H - pad.b}" stroke="#64748b"/><circle cx="${px}" cy="${Y(e)}" r="3.5" fill="${e >= 0 ? '#0a9b6a' : '#d93a3a'}"/><circle cx="${px}" cy="${Y(t)}" r="3" fill="#2457d6"/>`;
    tip.innerHTML = `₹${num(price)} <span style="opacity:.7">(${pct((price / spot - 1) * 100)})</span><br>Expiry <b>${sinr(e)}</b> · ${tag} <b>${sinr(t)}</b>`;
    tip.hidden = false; tip.style.left = Math.min(W - 170, px + 10) + 'px'; tip.style.top = '6px';
  };
  hit.addEventListener('mousemove', e => { const r = hit.getBoundingClientRect(); hovers[id] = pad.l + (e.clientX - r.left) * (W - pad.l - pad.r) / r.width; show(hovers[id]); });
  hit.addEventListener('mouseleave', () => { hovers[id] = null; show(null); });
  if (hovers[id] != null) show(hovers[id]);
}
window.addEventListener('resize', () => { if (S.legs.length && S.chain) renderSummary(); if (S.view === 'ai') renderAIPane(); });

$('trade').onclick = async () => {
  const b = $('trade'); b.disabled = true; const label = b.textContent; b.textContent = 'Recording…';
  try {
    await api('/api/desk/paper', {symbol: S.symbol, name: strategyName, legs: S.legs.map(l => ({symbol: l.symbol, lots: l.side * l.lots}))});
    toast('Paper trade recorded. See Positions.'); await loadPaper();
  } catch (e) { toast(e.message, true); }
  finally { b.textContent = label; renderSummary(); }
};

// ---------- Kronos AI: saved forecast and best strategies (instant), refresh runs in the background ----------
let aiTimer = null, myJob = null;
async function fetchAI(symbol = S.symbol) {
  clearTimeout(aiTimer);
  try {
    const r = await api('/api/desk/ai?' + new URLSearchParams({symbol}));
    S.aiData[symbol] = r; S.job = r.job; S.aiError = '';
    if (myJob && (!r.job || r.job.id !== myJob)) {          // our run finished: say how it went
      const run = await api('/api/runs/' + myJob).catch(() => null); myJob = null;
      const stock = run?.result?.stocks?.[0];
      if (run?.status === 'failed' || run?.error) toast('Kronos could not finish: ' + (run.error || run.message), true);
      else if (run?.status === 'cancelled') toast('Kronos run stopped.');
      else if (stock && !r.analysis?.ideas?.length && stock.reason) toast('Forecast saved. ' + stock.reason, true);
      else toast(`Kronos forecast and strategies updated for ${symbol}.`);
      fetchContext(symbol);
    }
  } catch (e) { S.aiError = e.message; }
  if (symbol === S.symbol) { renderAI(); if (S.legs.length) renderSummary(); }
  aiTimer = setTimeout(() => fetchAI(S.symbol), S.job ? 1500 : 30000);
}
const STATUS = {'Paper candidate': ['Ready', 'good'], 'Research only': ['Research', 'warn'], Skip: ['Review only', 'bad']};
function aiAge(a) {
  const when = new Date(a.forecast_created || a.created), mins = (Date.now() - when) / 6e4;
  return mins < 60 ? Math.round(mins) + ' min ago' : mins < 1440 ? Math.round(mins / 60) + ' h ago' : when.toLocaleDateString('en-IN', {day: 'numeric', month: 'short'});
}
function jobInfo() {
  // Job messages look like "AUROPHARMA · 1/1 · 24/32 forecast paths".
  const j = S.job; if (!j) return null;
  const parts = String(j.message || '').split(' · '), stock = parts[0] || j.symbol, count = /^(\d+)\/(\d+)$/.exec(parts[1] || '');
  const paths = /(\d+)\/(\d+) forecast paths/.exec(j.message || '');
  const single = !count || count[2] === '1', mine = single && (j.symbol === S.symbol || stock === S.symbol);
  let stage = 1, detail = 'Loading live prices and daily candles';
  if (paths) { stage = 2; detail = `Sampling price paths · ${paths[1]} of ${paths[2]}`; if (paths[1] === paths[2]) { stage = 3; detail = 'Pricing the option chain and ranking strategies'; } }
  const secs = j.created ? Math.max(0, Math.round((Date.now() - new Date(j.created)) / 1000)) : 0;
  return {j, stage, detail, single, mine, stock, count, pct: Math.max(2, Math.min(100, j.progress || 0)), elapsed: `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`};
}
function jobBox(compact = false) {
  const i = jobInfo(); if (!i) return '';
  if (!i.mine) {   // Kronos is busy with something else, e.g. a full F&O scan
    const what = i.single || !i.count ? esc(i.stock) : `${esc(i.j.symbol || 'scan')} · stock ${i.count[1]} of ${i.count[2]} (${esc(i.stock)})`;
    return `<div class="kjob busy"><div class="kjob-top"><i class="spin"></i><b>Kronos is busy: ${what}</b><span>${i.elapsed}</span><button data-ai="stop">Stop it</button></div>
      <div class="pbar"><i style="width:${i.pct}%"></i></div>${compact ? '' : `<small>Only one Kronos run at a time. Stop this to forecast ${esc(S.symbol)} now (about 30 seconds).</small>`}</div>`;
  }
  const steps = ['Live prices', 'Kronos forecast', 'Best option strategies'];
  return `<div class="kjob"><div class="kjob-top"><i class="spin"></i><b>Kronos is working on ${esc(S.symbol)}</b><span>${i.elapsed} · ${num(i.pct, 0)}%</span><button data-ai="stop">Stop</button></div>
    <div class="pbar"><i style="width:${i.pct}%"></i></div>
    ${compact ? `<small>${esc(i.detail)}</small>` : `<ol class="ksteps">${steps.map((s, n) => `<li class="${n + 1 < i.stage ? 'done' : n + 1 === i.stage ? 'on' : ''}">${n + 1 < i.stage ? '✓' : n + 1}. ${s}</li>`).join('')}</ol><small>${esc(i.detail)} · usually 15–40 seconds</small>`}</div>`;
}
function renderAI() {
  const d = S.aiData[S.symbol], a = d?.analysis, chip = $('ai'), box = $('ai-ideas');
  const open = '<button class="link" data-ai="open">AI forecast ›</button>';
  chip.hidden = !a;
  if (a) chip.textContent = `AI · ${a.forecast.summary?.bias || '—'} · ${a.data_mode === 'EOD' ? 'EOD ' + shortDate(a.data_asof) : 'live'} · ${aiAge(a)}`;
  if (S.aiError) box.innerHTML = `<div class="ai-top"><b>Kronos ideas</b><span class="down">${esc(S.aiError)}</span></div>`;
  else if (!d) box.innerHTML = '<div class="ai-top"><b>Kronos ideas</b><span>Loading…</span></div>';
  else if (!a) box.innerHTML = `<div class="ai-top"><b>Kronos ideas</b><span>No forecast yet for ${esc(S.symbol)}.</span>${open}</div>${jobBox(true)}`;
  else box.innerHTML = `<div class="ai-top"><b>Kronos ideas</b><span>${esc(a.forecast.summary?.bias || '')} · ${a.data_mode === 'EOD' ? 'EOD close ' + shortDate(a.data_asof) : 'Live'} · ${aiAge(a)}</span>${open}</div>${jobBox(true)}
    ${a.ideas.length ? a.ideas.map((c, i) => { const [label, cls] = STATUS[c.status] || ['—', '']; return `<div class="idea">
      <div><b>${esc(c.name)}</b> <span class="st ${cls}" title="${esc(c.reasons.join(' · ') || 'Passes all checks')}">${label}</span>
      <small>${c.legs.map(l => `${l.qty > 0 ? 'B' : 'S'}${Math.abs(l.qty) > 1 ? Math.abs(l.qty) : ''} ${num(l.strike, l.strike % 1 ? 1 : 0)}${l.type}`).join(' · ')} · ${esc(shortDate(c.expiry))}</small>
      <small>${c.entry_credit >= 0 ? 'Cr' : 'Dr'} ${inr(Math.abs(c.entry_credit))} · max loss ${inr(c.max_loss)} · ${num(c.scenario_profit_pct, 0)}% paths profit</small></div>
      <button data-ai="load" data-i="${i}">Load</button></div>`; }).join('') : `<p class="muted">${esc(a.reason || 'No liquid hedged structure found.')}</p>`}`;
  if (S.view === 'ai') renderAIPane();
}
$('ai').onclick = () => view('ai');
document.addEventListener('click', e => {
  const b = e.target.closest('[data-ai]'); if (!b) return;
  const act = b.dataset.ai;
  if (act === 'load') loadIdea(S.aiData[S.symbol].analysis.ideas[Number(b.dataset.i)]);
  if (act === 'refresh') runKronos(b);
  if (act === 'open') view('ai');
  if (act === 'stop') stopKronos(b);
});
async function runKronos(button) {
  if (button) button.disabled = true;
  try {
    const job = await api('/api/options/scan', {symbol: S.symbol});
    myJob = job.id; S.job = {...job, symbol: S.symbol, progress: 0, message: S.symbol + ' · 1/1'}; renderAI();
    fetchAI(S.symbol);
  } catch (e) { toast(e.message, true); if (button) button.disabled = false; }
}
async function stopKronos(button) {
  const j = S.job; if (!j?.id) return;
  if (!jobInfo().mine && !confirm(`Stop the running Kronos job (${j.symbol || 'scan'})? Stocks already analysed stay saved.`)) return;
  button.disabled = true;
  try { await api('/api/cancel/' + j.id, {}); toast('Stopping Kronos after the current step…'); } catch (e) { toast(e.message, true); }
  fetchAI(S.symbol);
}
function loadIdea(idea) {
  if (!S.chain || !idea) return;
  if (S.chain.expiry_date !== idea.expiry) {
    const e = S.chain.expiries.find(x => dmyToIso(x.date) === idea.expiry);
    if (!e) return toast('This idea\'s expiry is no longer listed.', true);
    S.pendingIdea = idea; S.expiry = e.ts; S.built = ''; pollChain(); return;
  }
  const legs = [];
  for (const l of idea.legs) {
    const o = optionAt(l.type, l.strike);
    if (!o?.symbol) return toast(`Strike ${l.strike} ${l.type} is not in the chain now.`, true);
    legs.push({symbol: o.symbol, type: l.type, strike: l.strike, side: Math.sign(l.qty), lots: Math.abs(l.qty)});
  }
  S.legs = legs; strategyName = idea.name + ' · Kronos';
  renderLegs(); updateLegPrices(); renderSummary(); updateChain(S.chain, windowRows(S.chain), null);
  toast(`${idea.name} loaded at live prices. Check the payoff, then Paper trade.`);
}

// forecast view
function renderAIPane() {
  const d = S.aiData[S.symbol], a = d?.analysis, head = $('ai-head');
  if (!d && !S.aiError) { head.innerHTML = '<p class="empty">Loading saved Kronos forecast…</p>'; $('ai-chart').innerHTML = ''; $('ai-detail').innerHTML = ''; return; }
  if (!a) { head.innerHTML = S.aiError ? `<div class="ai-empty"><b>Could not load Kronos data</b><p class="down">${esc(S.aiError)}</p></div>` : S.job ? jobBox() : `<div class="ai-empty"><b>No Kronos forecast for ${esc(S.symbol)} yet.</b><p>Kronos samples 32 possible price paths for the next 5 trading days, then the desk builds the best hedged option strategies for that view. It takes about 30 seconds; you can keep using the chain meanwhile.</p><button class="primary" data-ai="refresh">Run Kronos forecast</button></div>`; $('ai-chart').innerHTML = ''; $('ai-detail').innerHTML = ''; return; }
  const f = a.forecast, s = f.summary || {}, spot = S.chain?.symbol === S.symbol ? S.chain.spot.ltp : null, end = f.rows.at(-1);
  const since = spot && f.last_close ? (spot / f.last_close - 1) * 100 : null;
  head.innerHTML = `<div class="ai-stats">${cell('Kronos view', esc(s.bias || '—'), s.bias?.startsWith('Up') ? 'up' : s.bias?.startsWith('Down') ? 'down' : '')}
    ${cell('Median by ' + shortDate(end.date), `${num(s.median_end ?? end.median)} <small class="${tone(s.median_return_pct)}">${pct(s.median_return_pct)}</small>`)}
    ${cell('10–90% close', `${num(s.end_q10 ?? end.q10, 1)} – ${num(s.end_q90 ?? end.q90, 1)}`)}
    ${cell('Path low / high zone', `${num(a.lower, 1)} – ${num(a.upper, 1)}`)}
    ${cell('Upside paths', ok(s.upside_rate) ? num(s.upside_rate, 0) + '%' : '—')}
    ${cell('Price now vs forecast base', spot ? `${num(spot)} <small class="${tone(since)}">${pct(since)}</small>` : '—')}</div>
    <div class="ai-bar"><span>Based on ${a.data_mode === 'EOD' ? 'end-of-day close of <b>' + shortDate(a.data_asof) + '</b>' : 'live prices'} (₹${num(f.last_close)}) · saved ${esc(aiAge(a))} · ${esc(String(s.sample_count || ''))} sampled paths</span>
    ${S.job ? '' : '<button class="primary" data-ai="refresh">Refresh forecast</button>'}</div>${jobBox()}`;
  drawForecast($('ai-chart'), a, spot);
  $('ai-detail').innerHTML = a.ideas.length ? `<table class="ptable"><thead><tr><th>Kronos strategy</th><th>Legs · ${esc(shortDate(a.ideas[0].expiry))} expiry</th><th class="n">Credit / debit</th><th class="n">Max profit</th><th class="n">Max loss</th><th class="n">Profitable paths</th><th class="n">IV-stressed avg</th><th>Stop (stock)</th><th>Checks</th><th></th></tr></thead><tbody>${a.ideas.map((c, i) => { const [label, cls] = STATUS[c.status] || ['—', '']; const st = c.underlying_stop; return `<tr>
      <td><b>${esc(c.name)}</b><br><small>${esc(c.bias || '')}</small></td><td class="legtxt">${c.legs.map(l => `${l.qty > 0 ? 'Buy' : 'Sell'} ${Math.abs(l.qty)} ${num(l.strike, l.strike % 1 ? 1 : 0)} ${l.type} @${num(l.ltp)}`).join('<br>')}</td>
      <td class="n">${c.entry_credit >= 0 ? 'Cr' : 'Dr'} ${inr(Math.abs(c.entry_credit))}</td><td class="n up">${c.max_profit == null ? 'Uncapped' : inr(c.max_profit)}</td><td class="n down">${inr(c.max_loss)}</td>
      <td class="n">${num(c.scenario_profit_pct, 0)}%</td><td class="n ${tone(c.stressed_mean_net)}">${sinr(c.stressed_mean_net)}</td>
      <td>${st ? [st.lower != null ? '≤ ' + num(st.lower) : '', st.upper != null ? '≥ ' + num(st.upper) : ''].filter(Boolean).join(' / ') : '—'}</td>
      <td><span class="st ${cls}">${label}</span>${c.reasons.length ? `<div class="legtxt">${c.reasons.map(esc).join('<br>')}</div>` : ''}</td>
      <td><button class="primary small" data-ai="load" data-i="${i}">Load in builder</button></td></tr>`; }).join('')}</tbody></table>
    <p class="note">Prices shown are from when Kronos ran; "Load in builder" re-prices every leg live. Profitable paths are model scenarios, not a measured win rate.</p>` : `<p class="empty">${esc(a.reason || 'No liquid hedged structure found.')}</p>`;
}
function drawForecast(el, a, spot) {
  const f = a.forecast, h = f.history.slice(-30), rows = f.rows, W = el.clientWidth || 700, H = el.clientHeight || 320, pad = {l: 56, r: 64, t: 14, b: 26};
  // Forecast days get 3x the width of history days so the fan is readable.
  const n = h.length + rows.length, unit = i => i < h.length ? i : h.length - 1 + (i - h.length + 1) * 3, span = unit(n - 1) || 1;
  const X = i => pad.l + unit(i) / span * (W - pad.l - pad.r);
  const vals = [...h.map(r => r.close), ...rows.flatMap(r => [r.q10, r.q90]), a.lower, a.upper, spot].filter(ok);
  let lo = Math.min(...vals), hi = Math.max(...vals); const p = (hi - lo) * .06; lo -= p; hi += p;
  const Y = v => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b), ai = h.length - 1, anchor = h.at(-1);
  const fx = i => X(ai + 1 + i), pts = (arr, key) => arr.map((r, i) => `${fx(i).toFixed(1)},${Y(r[key]).toFixed(1)}`).join(' ');
  const band = `${X(ai)},${Y(anchor.close)} ${pts(rows, 'q90')} ${rows.map((r, i) => `${fx(i).toFixed(1)},${Y(r.q10).toFixed(1)}`).reverse().join(' ')}`;
  const today = todayIST(), ti = rows.findIndex(r => r.date === today), nowX = ti >= 0 ? fx(ti) : h.at(-1).date === today ? X(ai) : fx(0);
  const dates = [...h.map(r => r.date), ...rows.map(r => r.date)], step = Math.ceil(h.length / 5);
  const yt = Array.from({length: 5}, (_, i) => lo + (hi - lo) * i / 4);
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    ${yt.map(v => `<line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eef1f5"/><text x="${pad.l - 6}" y="${Y(v) + 3}" text-anchor="end">${num(v, 0)}</text>`).join('')}
    ${dates.map((d, i) => (i < h.length ? i % step : 0) ? '' : `<text x="${X(i)}" y="${H - 8}" text-anchor="middle">${shortDate(d)}</text>`).join('')}
    <rect x="${X(ai)}" y="${pad.t}" width="${W - pad.r - X(ai)}" height="${H - pad.t - pad.b}" fill="#f7f6ff"/>
    ${[['lower', a.lower], ['upper', a.upper]].filter(x => ok(x[1])).map(([k, v]) => `<line x1="${X(ai)}" x2="${W - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#a5a1ff" stroke-dasharray="4 4"/><text x="${W - pad.r + 4}" y="${Y(v) + 3}" style="fill:#635bff">${k === 'lower' ? 'Low' : 'High'} ${num(v, 0)}</text>`).join('')}
    <polygon points="${band}" fill="rgba(99,91,255,.16)"/>
    <polyline points="${h.map((r, i) => `${X(i).toFixed(1)},${Y(r.close).toFixed(1)}`).join(' ')}" fill="none" stroke="#0f172a" stroke-width="1.8"/>
    <polyline points="${X(ai)},${Y(anchor.close)} ${pts(rows, 'median')}" fill="none" stroke="#635bff" stroke-width="2.4" stroke-dasharray="6 4"/>
    ${rows.map((r, i) => `<circle cx="${fx(i)}" cy="${Y(r.median)}" r="2.6" fill="#635bff"/>`).join('')}
    <line x1="${X(ai)}" x2="${X(ai)}" y1="${pad.t}" y2="${H - pad.b}" stroke="#94a3b8" stroke-dasharray="2 3"/>
    <text x="${X(ai) - 4}" y="${pad.t + 10}" text-anchor="end">Forecast from ${shortDate(anchor.date)} ▸</text>
    <text x="${W - pad.r + 4}" y="${Y(rows.at(-1).median) + 3}" style="fill:#635bff;font-weight:700">${num(rows.at(-1).median, 0)}</text>
    ${spot ? `<circle cx="${nowX}" cy="${Y(spot)}" r="5.5" fill="#e0a100" stroke="#fff" stroke-width="2"/><text x="${nowX + 8}" y="${Y(spot) - 8}" style="fill:#a16d00;font-weight:700">Now ${num(spot)}</text>` : ''}
    <g class="hover"></g><rect class="hit" x="${pad.l}" y="0" width="${W - pad.l - pad.r}" height="${H}" fill="transparent"/>
  </svg><div class="tip" hidden></div>`;
  const hit = el.querySelector('.hit'), g = el.querySelector('.hover'), tip = el.querySelector('.tip');
  hit.addEventListener('mousemove', e => {
    const r = hit.getBoundingClientRect(), px = pad.l + (e.clientX - r.left) / r.width * (W - pad.l - pad.r);
    let i = 0; for (let k = 1; k < n; k++) if (Math.abs(X(k) - px) < Math.abs(X(i) - px)) i = k;
    const x = X(i);
    const hist = h[i], fr = rows[i - ai - 1];
    g.innerHTML = `<line x1="${x}" x2="${x}" y1="${pad.t}" y2="${H - pad.b}" stroke="#64748b"/>`;
    tip.innerHTML = hist ? `${shortDate(hist.date)} · close <b>${num(hist.close)}</b>` : fr ? `${shortDate(fr.date)} · median <b>${num(fr.median)}</b><br>10–90%: ${num(fr.q10)} – ${num(fr.q90)}` : '';
    tip.hidden = !tip.innerHTML; tip.style.left = Math.min(W - 190, x + 10) + 'px'; tip.style.top = '8px';
  });
  hit.addEventListener('mouseleave', () => { g.innerHTML = ''; tip.hidden = true; });
}

// ---------- watchlist ----------
function watchSymbols() {
  const recent = JSON.parse(store('desk-recent') || '[]').filter(s => S.universe.includes(s));
  const ideas = S.ideas.map(i => i.symbol).filter(s => !recent.includes(s));
  return {recent, ideas, all: [...recent, ...ideas].slice(0, 50)};
}
function renderWatch() {
  const {recent, ideas} = watchSymbols(), tags = new Map(S.ideas.map(i => [i.symbol, i.patterns.join(', ')]));
  const chip = s => { const q = S.quotes[s]; return `<button data-w="${esc(s)}" class="${s === S.symbol ? 'on' : ''}" title="${esc(tags.get(s) || 'Recently viewed')}"><b>${esc(s)}</b><span>${q ? num(q.ltp) : ''}</span><span class="${tone(q?.change_pct)}">${q && ok(q.change_pct) ? pct(q.change_pct) : ''}</span></button>`; };
  $('watch').innerHTML = recent.map(chip).join('') + (recent.length && ideas.length ? '<span class="sep"></span>' : '') + ideas.slice(0, 50 - recent.length).map(chip).join('');
}
$('watch').onclick = e => { const b = e.target.closest('[data-w]'); if (b) loadSymbol(b.dataset.w); };
let watchTimer = null;
async function pollWatch() {
  clearTimeout(watchTimer);
  const names = watchSymbols().all;
  if (names.length && !paused()) {
    try { const r = await api('/api/quotes?' + new URLSearchParams({symbols: names.join(',')})); Object.assign(S.quotes, r.quotes); renderWatch(); }
    catch (e) { /* watchlist is secondary; the chain shows its own status */ }
  }
  watchTimer = setTimeout(pollWatch, S.chain && !S.chain.market_open ? 60000 : 5000);
}

// ---------- next event + Kronos stop (builder) ----------
function stratDirection(legs, spot) {
  // Same rule as the server: which way does a 7% move help the expiry payoff?
  const v = s => legs.reduce((t, l) => t + l.qty * l.lot * intrinsic(l.type, l.strike, s), 0);
  const up = v(spot * 1.07), down = v(spot * .93), scale = Math.max(Math.abs(up - v(spot)), Math.abs(down - v(spot)), 1);
  return up - down > .25 * scale ? 'bullish' : down - up > .25 * scale ? 'bearish' : 'range';
}
const fullDate = iso => new Date(iso + 'T12:00:00+05:30').toLocaleDateString('en-IN', {weekday: 'short', day: 'numeric', month: 'short'});
function eventHtml(e, checked, exitBy) {
  if (!e) return `<div class="ev none">${checked ? 'No results or corporate event in the next 60 days' : 'Event calendar unavailable — check results dates manually'}</div>`;
  const days = e.days_away != null ? (e.days_away === 0 ? 'today' : e.days_away === 1 ? 'tomorrow' : `in ${e.days_away} days`) : '';
  const inside = exitBy ? e.date <= exitBy : null;
  return `<div class="ev ${inside === false ? '' : 'hot'}"><b>${esc(e.title)} · ${esc(fullDate(e.date))}</b> <span>${esc(days)}${inside == null ? '' : inside ? ' · before your exit' : ' · after your exit'}</span></div>`;
}
function stopHtml(stop, bias) {
  if (!stop) return '<div class="sl none">Kronos stop: run a Kronos forecast to get one</div>';
  const lo = stop.lower != null && bias !== 'bearish', hi = stop.upper != null && bias !== 'bullish';
  const parts = [lo ? `exit if stock falls below <b>₹${num(stop.lower)}</b>` : '', hi ? `exit if stock rises above <b>₹${num(stop.upper)}</b>` : ''].filter(Boolean);
  return `<div class="sl">Kronos stop: ${parts.join(' · ') || '—'}${bias && bias !== 'range' ? ` <small>(${bias} position)</small>` : ''}</div>`;
}
async function fetchContext(symbol = S.symbol) {
  try { S.ctx[symbol] = await api('/api/desk/context?' + new URLSearchParams({symbol})); } catch (e) { S.ctx[symbol] = {error: e.message}; }
  if (symbol === S.symbol) renderContext();
}
function renderContext() {
  const c = S.ctx[S.symbol], el = $('ctx');
  if (!c) { el.innerHTML = '<div class="ev none">Checking events…</div>'; return; }
  if (c.error) { el.innerHTML = `<div class="ev none">${esc(c.error)}</div>`; return; }
  const legs = S.legs.length && S.chain ? builderLegs() : null, bias = legs ? stratDirection(legs, S.chain.spot.ltp) : null;
  el.innerHTML = eventHtml(c.event, c.events_checked, S.chain?.expiry_date) + stopHtml(c.stop, bias);
}

// ---------- active position monitor ----------
const ACTION = {Hold: 'hold', Watch: 'watch', 'Book profit': 'book', Adjust: 'adjust', Exit: 'exit'};
let monTimer = null;
async function pollMonitor() {
  clearTimeout(monTimer);
  try {
    const m = await api('/api/desk/monitor'); S.monitor = m;
    for (const g of Object.values(m.positions)) {
      const key = 'desk-alert-' + g.trade, seen = store(key);
      if (['Exit', 'Adjust', 'Book profit'].includes(g.action) && seen !== g.action + '|' + g.since) {
        store(key, g.action + '|' + g.since);
        toast(`${g.symbol}: ${g.action.toUpperCase()} — ${g.reasons[0] || ''}`, g.action !== 'Book profit');
      }
    }
    renderAlert(); if (S.view === 'pos') renderPositions();
  } catch (e) { /* older app version or offline: positions still work */ }
  monTimer = setTimeout(pollMonitor, 10000);
}
function renderAlert() {
  const urgent = Object.values(S.monitor?.positions || {}).filter(g => ['Exit', 'Adjust', 'Book profit'].includes(g.action) && openTrades().some(t => t.id === g.trade));
  const el = $('pos-alert');
  el.hidden = !urgent.length;
  el.className = 'pos-alert ' + (urgent.some(g => g.action === 'Exit') ? 'exit' : urgent.some(g => g.action === 'Adjust') ? 'adjust' : 'book');
  el.innerHTML = urgent.map(g => `<b>${esc(g.symbol)}: ${esc(g.action)}</b> ${esc(g.reasons[0] || '')}`).join(' &nbsp;|&nbsp; ') + ' <button data-go-pos>Open positions</button>';
  document.title = (urgent.length ? `(${urgent.length}) ` : '') + document.title.replace(/^\(\d+\) /, '');
  $('tab-pos').classList.toggle('alert', urgent.length > 0);
}
document.addEventListener('click', e => { if (e.target.closest('[data-go-pos]')) view('pos'); });
function guideBadge(g) {
  if (!g) return '<span class="act none">Monitoring…</span>';
  return `<span class="act ${ACTION[g.action] || ''}" title="${esc(g.reasons.join(' '))}">${esc(g.action)}</span>`;
}
function guidePanel(t) {
  const g = S.monitor?.positions?.[t.id], st = S.monitor?.state || {};
  if (!g) return `<div class="guide"><p class="muted">The monitor checks open positions every minute in market hours and re-runs Kronos every ${S.monitor?.refresh_minutes || 30} minutes. First result shortly.</p></div>`;
  const s = g.stats || {}, b = g.baseline || {};
  const trend = b.profit_pct != null && s.profit_pct != null ? ` <small class="${tone(s.profit_pct - b.profit_pct)}">(${s.profit_pct - b.profit_pct >= 0 ? '+' : ''}${num(s.profit_pct - b.profit_pct, 0)} pts since entry)</small>` : '';
  const age = g.forecast_age_min != null ? (g.forecast_age_min < 60 ? Math.round(g.forecast_age_min) + ' min' : Math.round(g.forecast_age_min / 60) + ' h') + ' old' : '';
  const busy = st.busy ? (st.refreshing === g.symbol ? 'Kronos is re-forecasting this stock now…' : 'Kronos is busy; re-check queued.') : '';
  return `<div class="guide ${ACTION[g.action] || ''}"><div class="guide-head"><span class="act big ${ACTION[g.action] || ''}">${esc(g.action)}</span>
      <div><b>Kronos guidance for this position</b><small>Kronos view: ${esc(g.view || '—')} · forecast ${esc(g.forecast_mode || '')} ${esc(age)} · checked ${esc(new Date(g.checked).toLocaleTimeString('en-IN', {timeZone: 'Asia/Kolkata', timeStyle: 'short'}))}</small></div>
      <button data-recheck="${esc(t.id)}" ${st.busy ? 'disabled' : ''} title="Runs a fresh Kronos forecast for this stock (about 30 s), then re-tests the position">Re-check now</button></div>
    <ul>${g.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>${g.suggestion ? `<p class="sugg"><b>Suggested:</b> ${esc(g.suggestion)}</p>` : ''}${busy ? `<p class="muted">${esc(busy)}</p>` : ''}
    <div class="gstats">${cell('Profitable Kronos paths', s.profit_pct == null ? '—' : num(s.profit_pct, 0) + '%' + trend)}${cell('Expected by ' + (s.eval_date ? shortDate(s.eval_date) : 'exit'), sinr(s.expected), tone(s.expected))}${cell('Bad case (10%)', sinr(s.tail), tone(s.tail))}${cell('Paths hitting stop', s.stop_touch_pct == null ? '—' : num(s.stop_touch_pct, 0) + '%')}${cell('Paths past a short strike', s.short_breach_pct == null ? '—' : num(s.short_breach_pct, 0) + '%')}</div>
    <p class="note">Advice only; nothing is closed automatically. Use Exit on the row to close at live bid/ask.</p></div>`;
}
document.addEventListener('click', async e => {
  const b = e.target.closest('[data-recheck]'); if (!b) return;
  b.disabled = true; b.textContent = 'Running Kronos…';
  try { await api('/api/desk/monitor/check', {id: b.dataset.recheck, force: true}); toast('Kronos re-forecast started; guidance updates when it finishes (about 30 s).'); }
  catch (err) { toast(err.message, true); }
  pollMonitor();
});

// ---------- views ----------
function view(name) {
  S.view = name;
  $('view-chain').hidden = name === 'pos'; $('view-pos').hidden = name !== 'pos';
  document.querySelector('.chain-pane').hidden = name !== 'chain'; $('ai-pane').hidden = name !== 'ai';
  for (const [id, v] of [['tab-chain', 'chain'], ['tab-ai', 'ai'], ['tab-pos', 'pos']]) $(id).setAttribute('aria-selected', name === v);
  if (name === 'pos') pollPositions();
  else { if (S.legs.length) renderSummary(); if (name === 'ai') renderAIPane(); else setTimeout(centreATM); }
}
$('tab-chain').onclick = () => view('chain'); $('tab-ai').onclick = () => view('ai'); $('tab-pos').onclick = () => view('pos');

// ---------- positions ----------
async function loadPaper() {
  const b = await api('/api/desk/bootstrap'); S.paper = b.paper; renderPositions();
}
const openTrades = () => S.paper.filter(t => t.status === 'open');
const entryCosts = p => Object.values(p.entry_charges || {}).reduce((a, b) => a + b, 0);
function livePnl(t) {
  const p = t.payload; let total = -entryCosts(p), missing = false;
  for (const l of p.legs) { const q = S.quotes[l.symbol]; if (!q) { missing = true; continue; } total += l.qty * l.lot * (q.ltp - l.entry_fill); }
  return missing ? null : total;
}
const legText = p => p.legs.map(l => `${l.qty > 0 ? 'B' : 'S'}${Math.abs(l.qty)} ${num(l.strike, l.strike % 1 ? 1 : 0)}${l.type} @${num(l.entry_fill)}`).join(' · ');
function positionDetail(t) {
  const p = t.payload, c = S.posChains[t.id];
  if (!c) return '<p class="muted">Loading live chain for the payoff…</p>';
  const ivs = new Map(c.rows.flatMap(r => [r.CE, r.PE]).filter(Boolean).map(o => [o.symbol, o.iv]));
  const legs = p.legs.map(l => ({type: l.type, strike: l.strike, qty: l.qty, lot: l.lot, entry: l.entry_fill, iv: ivs.get(l.symbol) || c.atm_iv || 25}));
  const off = -entryCosts(p), spot = c.spot.ltp, m = stats(legs, spot, c.days, c.atm_iv, off), pnl = livePnl(t);
  const credit = p.entry_credit > 0, maxProfit = m.maxProfit, maxLoss = p.plan.unlimited_loss ? null : p.plan.max_loss;
  const hlines = [];
  if (credit) hlines.push({y: p.entry_credit * .5, label: 'Target · 50% of credit', color: '#0a9b6a'});
  else if (ok(maxProfit) && maxProfit !== Infinity) hlines.push({y: maxProfit * .5, label: 'Target · 50% of max profit', color: '#0a9b6a'});
  if (maxLoss) hlines.push({y: -maxLoss * .5, label: 'Review · half max loss', color: '#d93a3a'});
  const vlines = [{x: p.entry_spot, label: 'Entry ' + num(p.entry_spot, 0), color: '#64748b'}];
  const st = p.plan.underlying_stop;
  if (st?.lower != null) vlines.push({x: st.lower, label: 'Stop ' + num(st.lower, 0), color: '#d93a3a'});
  if (st?.upper != null) vlines.push({x: st.upper, label: 'Stop ' + num(st.upper, 0), color: '#d93a3a'});
  const band = aiBand(p.symbol), exitDays = Math.max(0, c.days - daysUntil(p.exit_by || p.plan.exit_by || p.plan.expiry));
  const expected = band ? pnlModel(legs, band.mid, exitDays, 0, off) : null;
  const captured = pnl != null && maxProfit > 0 && maxProfit !== Infinity ? pnl / maxProfit * 100 : null;
  setTimeout(() => { const el = $('pos-chart-' + t.id); if (el) drawChart(el, {legs, off, spot, days: c.days, be: m.be, band, hlines, vlines, now: pnl == null ? null : {x: spot, y: pnl}}); });
  return `${guidePanel(t)}<div class="pos-detail"><div class="pos-chart" id="pos-chart-${esc(t.id)}"></div><div class="pos-stats">
    ${cell('Stock now', `${num(spot)} <small class="${tone(spot - p.entry_spot)}">${pct((spot / p.entry_spot - 1) * 100)} since entry</small>`)}
    ${cell('Live P&L', pnl == null ? '…' : sinr(pnl), tone(pnl))}
    ${cell('Of max profit', captured == null ? '—' : num(captured, 0) + '%')}
    ${cell('Max profit · expiry', maxProfit === Infinity ? 'Unlimited' : inr(maxProfit), 'up')}
    ${cell('Max loss · expiry', maxLoss ? inr(maxLoss) : 'Unlimited', 'down')}
    ${cell('Breakeven', m.be.map(x => num(x, 1)).join(' · ') || '—')}
    ${cell('POP now · model', num(m.pop, 0) + '%')}
    ${cell('Exit by · DTE', `${esc(p.exit_by || p.plan.exit_by || '—')} · ${num(c.days, 1)}d`)}
    ${cell(band ? 'Expected at AI median ' + num(band.mid, 0) : 'AI expectation', band ? sinr(expected) : 'No Kronos forecast', band ? tone(expected) : '')}
  </div></div>${fillNote(p)}`;
}
// Contract-note style: every leg's fill (and price levels used) plus the charges billed.
function fillNote(p) {
  const fees = c => c ? Object.entries(c).filter(([, v]) => v > .005).map(([k, v]) => `${k.toUpperCase()} ₹${num(v)}`).join(' · ') : '';
  const exitBy = new Map((p.mark?.legs || []).map(l => [l.symbol, l]));
  const levels = ls => ls && ls.length > 1 ? ` <small>(${ls.map(x => `${Math.round(x.units)} @ ${num(x.price)}`).join(' + ')})</small>` : '';
  return `<div class="fills"><table><thead><tr><th>Leg</th><th>Qty</th><th class="n">Entry fill</th><th class="n">LTP now</th><th class="n">Exit price now · ${p.legs.every(l => exitBy.has(l.symbol)) ? 'last mark' : 'mark to see'}</th></tr></thead><tbody>${p.legs.map(l => {
    const x = exitBy.get(l.symbol), q = S.quotes[l.symbol];
    return `<tr><td><span class="side ${l.qty > 0 ? 'b' : 's'}">${l.qty > 0 ? 'B' : 'S'}</span> ${num(l.strike, l.strike % 1 ? 1 : 0)} ${l.type}</td><td>${Math.abs(l.qty)} lot · ${Math.abs(l.qty) * l.lot} qty</td><td class="n">${num(l.entry_fill)}${levels(l.entry_levels)}</td><td class="n">${q ? num(q.ltp) : '—'}</td><td class="n">${x ? num(x.price) + levels(x.levels) : '—'}</td></tr>`;
  }).join('')}</tbody></table>
  <p class="note">Entry charges ₹${num(entryCosts(p))}: ${esc(fees(p.entry_charges))}${p.mark?.exit_charges ? ` &nbsp;·&nbsp; Exit charges if closed at last mark ₹${num(Object.values(p.mark.exit_charges).reduce((a, b) => a + b, 0))}: ${esc(fees(p.mark.exit_charges))}` : ''}.
  ${p.fill_model === 'market' ? 'Filled like a market order: buys at the live ask, sells at the live bid, deeper levels if the best price had too little quantity.' : 'Opened before market-order fills; brokerage was not charged on this trade.'} Live P&amp;L uses LTP like a broker MTM; Exit uses the live bid/ask.</p></div>`;
}
function renderPositions() {
  const open = openTrades(), closed = S.paper.filter(t => t.status === 'closed');
  $('pos-count').textContent = open.length || '';
  let total = 0, all = true;
  $('open-pos').innerHTML = open.length ? `<table class="ptable"><thead><tr><th>Stock</th><th>Strategy · legs</th><th class="n">Entry</th><th class="n">Max loss</th><th class="n">Live P&amp;L · LTP</th><th class="n">Last mark · bid/ask</th><th>Review</th><th></th></tr></thead><tbody>${open.map(t => {
    const p = t.payload, pnl = livePnl(t), isOpen = S.posOpen === t.id; if (pnl == null) all = false; else total += pnl;
    const sig = p.exit_signal && p.exit_signal !== 'Monitor' ? `<span class="signal ${p.exit_signal.startsWith('EXIT') ? 'danger' : ''}">${esc(p.exit_signal)}</span>` : `<small>Exit by ${esc(p.exit_by || p.plan.exit_by || '')}</small>`;
    const g = S.monitor?.positions?.[t.id];
    return `<tr class="prow ${isOpen ? 'on' : ''} ${g ? 'g-' + (ACTION[g.action] || '') : ''}" data-row="${esc(t.id)}" title="Show payoff and Kronos guidance"><td><b>${isOpen ? '▾' : '▸'} ${esc(p.symbol)}</b><br><small>${esc(p.plan.expiry)}</small><div>${guideBadge(g)}</div></td><td>${esc(p.plan.name)}<div class="legtxt">${esc(legText(p))}</div>${g ? `<div class="rowctx">${eventHtml(g.event, true, p.exit_by || p.plan.exit_by)}${stopHtml(g.stop, g.stats?.bias)}</div>` : ''}</td>
      <td class="n">${p.entry_credit >= 0 ? 'Cr' : 'Dr'} ${inr(Math.abs(p.entry_credit))}</td><td class="n">${p.plan.unlimited_loss ? 'Unlimited' : inr(p.plan.max_loss)}</td>
      <td class="n"><b class="${tone(pnl)}">${pnl == null ? '…' : sinr(pnl)}</b></td><td class="n ${tone(p.mark?.net_pnl)}">${p.mark ? sinr(p.mark.net_pnl) : '—'}</td><td>${sig}</td>
      <td><button data-mark="${esc(t.id)}">Mark</button> <button class="close" data-close="${esc(t.id)}">Exit</button></td></tr>
      ${isOpen ? `<tr class="pdetail"><td colspan="8">${positionDetail(t)}</td></tr>` : ''}`;
  }).join('')}</tbody></table>` : '<p class="empty">No open paper positions. Build a strategy on the chain and press Paper trade.</p>';
  $('pos-total').textContent = open.length ? (all ? 'Total ' + sinr(total) : 'Total …') : '';
  $('pos-total').className = 'total ' + tone(total);
  const realised = closed.reduce((a, t) => a + (t.payload.mark?.net_pnl || 0), 0);
  $('closed-pos').innerHTML = closed.length ? `<table class="ptable"><thead><tr><th>Stock</th><th>Strategy · legs</th><th>Closed</th><th class="n">Net P&amp;L <span class="${tone(realised)}">${sinr(realised)}</span></th></tr></thead><tbody>${closed.slice(0, 50).map(t => {
    const p = t.payload, n = p.mark?.net_pnl;
    return `<tr><td><b>${esc(p.symbol)}</b></td><td>${esc(p.plan.name)}<div class="legtxt">${esc(legText(p))}</div></td><td>${esc(new Date(t.updated).toLocaleString('en-IN', {timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short'}))}</td><td class="n"><b class="${tone(n)}">${sinr(n)}</b></td></tr>`;
  }).join('')}</tbody></table>` : '<p class="empty">No closed trades yet.</p>';
}
$('view-pos').addEventListener('click', async e => {
  const b = e.target.closest('[data-mark],[data-close]');
  if (!b) {
    const row = e.target.closest('[data-row]'); if (!row) return;
    S.posOpen = S.posOpen === row.dataset.row ? null : row.dataset.row; renderPositions(); if (S.posOpen) pollPositions();
    return;
  }
  const close = !!b.dataset.close;
  if (close && !confirm('Exit this paper trade now? Longs sell at the live bid, shorts buy back at the live ask.')) return;
  b.disabled = true;
  try { await api('/api/options/paper/mark', {id: b.dataset.mark || b.dataset.close, close}); await loadPaper(); toast(close ? 'Paper exit recorded.' : 'Marked at live bid/ask.'); }
  catch (err) { toast(err.message, true); } finally { b.disabled = false; }
});
async function positionChain(t) {
  // The chain for the trade's own expiry gives spot, days left and IVs for the today-curve.
  const p = t.payload, key = p.symbol + '|' + p.plan.expiry;
  let c = await api('/api/desk/chain?' + new URLSearchParams({symbol: p.symbol, expiry: S.posExpiry[key] || ''}));
  if (c.expiry_date !== p.plan.expiry) {
    const e = c.expiries.find(x => dmyToIso(x.date) === p.plan.expiry); if (!e) return null;
    S.posExpiry[key] = e.ts; c = await api('/api/desk/chain?' + new URLSearchParams({symbol: p.symbol, expiry: e.ts}));
  }
  return c;
}
let posTimer = null;
async function pollPositions() {
  clearTimeout(posTimer);
  const symbols = [...new Set(openTrades().flatMap(t => t.payload.legs.map(l => l.symbol)))].slice(0, 50);
  if (symbols.length && !paused()) {
    try { const r = await api('/api/desk/ltp?' + new URLSearchParams({symbols: symbols.join(',')})); Object.assign(S.quotes, r.quotes); } catch (e) { /* keep last values */ }
    const t = openTrades().find(x => x.id === S.posOpen);
    if (t) {
      try { const c = await positionChain(t); if (c) S.posChains[t.id] = c; } catch (e) { /* chart keeps last chain */ }
      if (!S.aiData[t.payload.symbol]) await fetchAI(t.payload.symbol);
    }
  }
  renderPositions();
  if (S.view === 'pos') posTimer = setTimeout(pollPositions, S.chain && !S.chain.market_open ? 30000 : 2000);
}

// ---------- resizable builder width and payoff height (remembered) ----------
function sizer(handle, prop, key, axis, fallback, clamp) {
  const root = document.documentElement, saved = Number(store(key));
  if (saved) root.style.setProperty(prop, saved + 'px');
  handle.addEventListener('pointerdown', e => {
    e.preventDefault(); handle.classList.add('drag');
    document.body.classList.add('resizing'); if (axis === 'y') document.body.classList.add('rows');
    const move = ev => { const v = Math.round(clamp(ev)); root.style.setProperty(prop, v + 'px'); store(key, v); };
    const up = () => { window.removeEventListener('pointermove', move); handle.classList.remove('drag'); document.body.classList.remove('resizing', 'rows'); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up, {once: true});
  });
  handle.addEventListener('dblclick', () => { root.style.setProperty(prop, fallback + 'px'); store(key, fallback); });
}
sizer($('split'), '--builder-w', 'desk-builder-w', 'x', 420, ev => {
  const grid = $('view-chain').getBoundingClientRect();
  return Math.min(grid.width - 420, Math.max(340, grid.right - 14 - ev.clientX));
});
sizer($('payoff-grip'), '--payoff-h', 'desk-payoff-h', 'y', 260, ev => Math.min(900, Math.max(180, ev.clientY - $('payoff').getBoundingClientRect().top)));
// Redraw charts whenever their box changes size (drag, window resize, layout change).
let redraw = 0;
new ResizeObserver(() => { cancelAnimationFrame(redraw); redraw = requestAnimationFrame(() => { if (S.legs.length && S.chain) renderSummary(); if (S.view === 'ai') renderAIPane(); }); })
  .observe($('payoff'));
new ResizeObserver(() => { if (S.view === 'ai') { cancelAnimationFrame(redraw); redraw = requestAnimationFrame(renderAIPane); } }).observe($('ai-chart'));

// ---------- start ----------
const DESK_VERSION = '2.3.0';   // must match /api/health; a mismatch means the app window is running older code
function restartBanner() {
  const el = document.createElement('div'); el.className = 'restart';
  el.innerHTML = '<b>Restart needed.</b> The app window is running an older version, so some features will not work. Close the black "Pattern Scanner - SAI Research" window and run <b>Run Pattern Scanners.bat</b> again.';
  document.body.prepend(el);
}
(async () => {
  try {
    api('/api/health').then(h => { if (h.version !== DESK_VERSION) restartBanner(); }).catch(() => {});
    const b = await api('/api/desk/bootstrap');
    Object.assign(S, {token: b.token, universe: b.universe, ideas: b.ideas, ai: b.ai, paper: b.paper});
    renderPositions(); renderWatch(); renderLegs();
    const last = store('desk-symbol');
    const first = S.universe.includes(last) ? last : S.ideas[0]?.symbol || S.universe[0];
    if (first) loadSymbol(first); else setLive('error', 'F&O list unavailable. Check the FYERS login.');
    pollWatch(); pollMonitor();
  } catch (e) { setLive('error', e.message); toast(e.message, true); }
})();
