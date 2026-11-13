'use strict';
// ADX trend tab: ADX/DMI setups on F&O stocks, an option spread for each, and your own ADX trades (kronos/adx_desk.py).
// Independent of Kronos: trades are placed and managed here.
// The page has two of these tabs, built from this one template: the daily desk and the 1-hour desk
// (kronos/adx_hour.py). `P` is what differs between them; see the two trendTab() calls at the end.
function trendTab(P) {
const el = name => $(P.id + '-' + name);
const TREND = {board: null, section: store(P.key + '-s') === 'trades' ? 'trades' : 'setups', verdict: store(P.key + '-v') || 'all', dir: store(P.key + '-d') || 'all',
  pick: '', detail: null, timer: null, seq: 0, error: '', seen: null};
const TREND_VERDICT = {enter: ['Enter', 'ripe'], armed: ['Armed', 'watch'], range: ['Range', 'range'], watch: ['Watch', ''], skip: ['Skipped', 'skip']};

window[P.open] = () => { trendSegs(); trendLoad(); };
// Checked every minute on every tab, so an exit signal on an open ADX trade is never missed while the page is open.
TREND.timer = setInterval(() => { if (!paused()) trendLoad(); }, 60000);
setTimeout(trendLoad, 4000);

// Alerts come from the server's own watcher (every 20 s in market hours), so they do not depend on which tab is open.
const trendNotifyOn = () => store(P.key + '-notify') === '1' && typeof Notification !== 'undefined' && Notification.permission === 'granted';
function trendNotifyRender() {
  const b = el('notify');
  if (typeof Notification === 'undefined') { b.hidden = true; return; }
  const denied = Notification.permission === 'denied', on = trendNotifyOn();
  b.setAttribute('aria-pressed', on); b.disabled = denied;
  b.textContent = denied ? '🔕 Blocked in browser' : on ? '🔔 Alerts on' : '🔔 Alerts';
}
el('notify').onclick = async () => {
  if (trendNotifyOn()) store(P.key + '-notify', '0');
  else {
    if (Notification.permission === 'default') await Notification.requestPermission();
    if (Notification.permission === 'granted') { store(P.key + '-notify', '1'); toast('Desktop alerts are on for ADX entries, exits and adjustments while this page is open.'); }
  }
  trendNotifyRender();
};
trendNotifyRender();
function trendBeep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)(), o = ctx.createOscillator(), g = ctx.createGain();
    o.frequency.value = 880; g.gain.value = .08; o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + .25);
  } catch (e) { /* sound is optional */ }
}
async function trendAlerts() {
  let r;
  try { r = await api(P.api + '/alerts?after=' + (TREND.seen ?? 0)); } catch (e) { return; }
  const first = TREND.seen == null;
  TREND.seen = r.last;
  if (first || !r.alerts.length) return;            // the first read only sets the starting point
  for (const a of r.alerts) {
    const title = a.action === 'missed' ? `${P.name} entry missed: ${a.symbol}` : a.kind === 'entry' ? `${P.name} entry: ${a.symbol}` : `${P.name} ${a.action === 'profit' ? 'book profit' : a.action}: ${a.symbol}`;
    toast(`${title}. ${a.text}`, a.action === 'exit' || a.action === 'missed');
    if (trendNotifyOn()) { try { new Notification(title, {body: a.text, tag: P.id + '-' + a.n}); } catch (e) { /* notification blocked */ } }
  }
  trendBeep(); trendLoad();
}
setInterval(() => { if (!paused()) trendAlerts(); }, 15000);
setTimeout(trendAlerts, 5000);

function trendSegs() {
  const seg = (id, items, cur) => $(id).innerHTML = items.map(([v, t, n]) => `<button type="button" data-v="${v}" aria-pressed="${v === cur}">${t}${n != null ? ` <b>${n}</b>` : ''}</button>`).join('');
  const c = TREND.board?.counts || {};
  if (!['all', 'enter', 'armed', 'range', 'watch', 'skip'].includes(TREND.verdict)) TREND.verdict = 'all';
  seg(P.id + '-views', [['setups', 'Setups'], ['trades', `My ${P.name} trades`, TREND.board?.open_trades || null]], TREND.section);
  seg(P.id + '-verdicts',[['all', 'All'], ['enter', 'Enter', c.enter], ['armed', 'Armed', c.armed], ['range', 'Range', c.range], ['watch', 'Watch', c.watch], ['skip', 'Skipped', c.skip]], TREND.verdict);
  seg(P.id + '-dirs',[['all', 'All'], ['bullish', 'Bullish'], ['bearish', 'Bearish'], ['neutral', 'No direction']], TREND.dir);
  el('verdicts').hidden = el('dirs').hidden = TREND.section !== 'setups';
}
for (const [id, key, name] of [['views', 'section', '-s'], ['verdicts', 'verdict', '-v'], ['dirs', 'dir', '-d']])
  el(id).addEventListener('click', e => {
    const b = e.target.closest('button[data-v]'); if (!b) return;
    TREND[key] = b.dataset.v; store(P.key + name, TREND[key]); trendSegs();
    if (TREND.section === 'trades') trendTrades(); else trendRender();
  });
el('scan').onclick = async () => {
  try { await api(P.api + '/scan', {}); toast('ADX scan started.'); trendLoad(); } catch (e) { toast(e.message, true); }
};

async function trendLoad() {
  try { TREND.board = await api(P.api + '/board'); TREND.error = ''; } catch (e) { TREND.error = e.message; }
  const b = TREND.board, job = b?.scanning;
  el('count').textContent = b?.counts ? (b.counts.enter + b.counts.armed) || '' : '';
  el('scan').disabled = !!job?.running;
  el('meta').textContent = job?.running ? `Scanning… ${job.progress}% · ${job.message}` : b?.ready
    ? `${b.symbols} F&O stocks · ${P.bars} to ${b.data_date}${b.market_open ? ` · ${b.live_quotes} live prices` : ' · market closed'}${b.auto ? ' · auto paper trading on' : ''}` : '';
  const warn = [];
  if (TREND.error) warn.push(esc(TREND.error));
  if (job?.error) warn.push(esc(job.error) + ' Press Rescan to retry.');
  if (b?.ready && b.behind && !job?.running) warn.push(P.behind(esc(b.data_date), esc(b.expected_date)));
  const cov = b?.coverage;
  if (cov?.failed_n) warn.push(`${cov.failed_n} of ${cov.total} stocks could not be fetched (${esc(cov.failed.join(', '))}${cov.failed_n > cov.failed.length ? ', …' : ''}). They are tried again on the next scan.`);
  else if (cov?.behind_n) warn.push(`${cov.behind_n} of ${cov.total} stocks have no bar for the latest completed hour yet (${esc(cov.behind.join(', '))}${cov.behind_n > cov.behind.length ? ', …' : ''}). They are tried again on the next scan.`);
  const alerts = b?.alerts || [];
  for (const a of alerts) warn.unshift(`<b>${esc(a.symbol)}: ${a.action === 'profit' ? 'book profit' : a.action}.</b> ${esc(a.advice)} Open My ${P.name} trades to act on it.`);
  const m = b?.market;
  el('market').hidden = !(m?.lines?.length);
  if (m?.lines?.length) el('market').innerHTML = `<b>Market${m.date ? ' · ' + esc(m.date) : ''}.</b> ${esc(m.lines.join(' '))} <b>${esc(m.stance)}</b>`;
  const key = alerts.map(a => a.id + a.action).join('|');
  if (key && key !== TREND.alerted) toast(`${P.name}: ${alerts[0].symbol} — ${alerts[0].advice}`, true);
  TREND.alerted = key;
  $('tab-' + P.id).classList.toggle('alert', alerts.length > 0);
  el('warn').hidden = !warn.length; el('warn').innerHTML = warn.join('<br>');
  trendSegs();
  if (S.view === P.id) { if (TREND.section === 'trades') trendTrades(); else if (!TREND.pick || !el('detail')) trendRender(); }
  if (job?.running) setTimeout(() => { if (S.view === P.id) trendLoad(); }, 2000);
}

function trendRender() {
  if (TREND.section !== 'setups') return;
  const b = TREND.board, body = el('body');
  if (!b?.ready) { body.innerHTML = `<p class="empty">${b?.scanning?.running ? 'Scanning every F&O stock…' : 'No scan yet. Press Rescan.'}</p>`; return; }
  const rows = b.rows.filter(r => (TREND.verdict === 'all' ? r.verdict !== 'skip' : r.verdict === TREND.verdict) && (TREND.dir === 'all' || r.direction === TREND.dir));
  const line = r => {
    const [label, cls] = TREND_VERDICT[r.verdict], live = r.state !== 'watch', band = r.state === 'range';
    const tag = r.direction === 'neutral' ? '<span class="chip">Range</span>' : `<span class="chip ${r.direction === 'bullish' ? 'good' : 'bad'}">${r.direction === 'bullish' ? 'Bull' : 'Bear'}</span>`;
    return `<tr data-id="${esc(r.id)}" class="${r.id === TREND.pick ? 'on' : ''}">
      <td><b>${esc(r.symbol)}</b> ${tag}${r.held ? ' <span class="chip warn">open trade</span>' : ''}</td>
      <td>${esc(r.setup)}</td><td><span class="verdict ${cls}">${label}</span></td>
      <td class="n">${r.score == null ? '' : `<span class="trend-score">${r.score}</span> <span class="chip ${r.grade === 'A' ? 'good' : r.grade === 'C' ? 'warn' : ''}">${r.grade}</span>`}</td>
      <td class="n">${num(r.ltp || r.close)}${r.price_stale ? ` <small class="stale" title="The live price refresh failed. Do not trade off this number.">${r.ltp ? `stale ${Math.round(r.ltp_age)}s` : 'no live price'}</small>` : r.ltp ? '' : ' <small>close</small>'}</td>
      <td class="n">${band ? num(r.high) : live ? num(r.trigger) : `<small>level</small> ${num(r.level)}`}</td>
      <td class="n">${band ? num(r.low) : live ? num(r.stop) : ''}</td>
      <td class="n">${band ? '<small>range</small>' : live ? num(r.target) : ''}</td>
      <td class="n">${band ? r.features.base_bars + P.unit : live ? r.risk_pct.toFixed(1) + '%' : ''}</td>
      <td class="n">${r.adx.toFixed(0)} <small class="up">${r.pdi.toFixed(0)}</small>/<small class="down">${r.mdi.toFixed(0)}</small></td>
      <td>${r.features.wk_adx == null ? '—' : `<span class="chip ${r.features.weekly_aligned ? 'good' : 'warn'}">${r.features.weekly_aligned ? 'with' : 'against'}</span>`}</td>
      <td class="n">${live ? r.quality + '%' : ''}</td>
      <td class="why">${esc(r.why)}</td></tr>`;
  };
  const table = rows.length ? `<table class="trend-table"><thead><tr><th>Stock</th><th>Setup</th><th>Verdict</th><th title="0 to 100, starting at 50: rules passed, ${P.higher.toLowerCase()} trend, market, relative strength and your own results. It orders setups; it is not a win probability.">Score</th><th>Price</th><th title="Trigger, or the top of the range">Entry / high</th><th title="Stop, or the bottom of the range">Stop / low</th><th>Target</th><th title="Risk to the stop, or how long the range has lasted">Risk</th>
    <th title="ADX with +DMI / −DMI">ADX <small>+/−</small></th><th>${P.higher}</th><th title="Share of the setup's rule checklist that passed">Rules</th><th>What to do</th></tr></thead><tbody>${rows.map(line).join('')}</tbody></table>`
    : '<p class="empty">Nothing matches this filter.</p>';
  body.innerHTML = `<div class="trend-split"><div class="trend-list">${table}</div><aside id="${P.id}-detail" class="trend-detail"></aside></div>`;
  trendDetailRender();
}
el('body').addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b?.dataset.trade) return trendTrade(Number(b.dataset.trade), b);
  if (b?.dataset.payoff) return trendPayoff(Number(b.dataset.payoff));
  if (b?.dataset.open) { view('chain'); loadSymbol(b.dataset.open); return; }
  if (b?.dataset.mark || b?.dataset.close) return trendMark(b);
  const tr = e.target.closest('.trend-split tr[data-id]'); if (!tr) return;
  TREND.pick = tr.dataset.id; TREND.detail = null;
  for (const x of document.querySelectorAll('.trend-table tr.on')) x.classList.remove('on');
  tr.classList.add('on'); trendDetailRender(); trendDetail();
});

async function trendDetail() {
  const row = TREND.board?.rows.find(r => r.id === TREND.pick); if (!row) return;
  const seq = ++TREND.seq;
  try {
    const d = await api(P.api + '/detail?' + new URLSearchParams({symbol: row.symbol, signal: row.id}));
    if (seq === TREND.seq) { TREND.detail = d; trendDetailRender(); }
  } catch (e) { if (seq === TREND.seq) { TREND.detail = {error: e.message}; trendDetailRender(); } }
}

function trendChart(d, sig) {
  const bars = d.bars.slice(-90), W = 640, H1 = 210, H2 = 110, gap = 14, pad = 46;
  const levels = sig.state === 'range' ? [sig.high, sig.low] : sig.state !== 'watch' ? [sig.trigger, sig.stop, sig.target] : [sig.level];
  const live = sig.ltp && !sig.price_stale ? sig.ltp : null;     // the bar still forming is not a finished bar, so it is drawn as a price line
  if (live) levels.push(live);
  const lo = Math.min(...bars.map(b => b.l), ...levels), hi = Math.max(...bars.map(b => b.h), ...levels);
  const x = i => 4 + (i + .5) * (W - pad - 4) / bars.length, y = p => 16 + (hi - p) / (hi - lo || 1) * (H1 - 22);
  const top = Math.max(50, ...bars.map(b => Math.max(b.adx, b.pdi, b.mdi))), y2 = v => H1 + gap + 14 + (top - v) / top * (H2 - 18);
  const path = (f, yy) => bars.map((b, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${yy(f(b)).toFixed(1)}`).join('');
  const lvl = (p, cls, text) => {
    let ty = y(p) + 3;                       // keep the live label clear of a nearby entry/stop/target label
    if (cls === 'live') { const near = levels.filter(v => v !== p && Math.abs(y(v) - y(p)) < 11).map(v => y(v)); if (near.length) ty = y(p) + (near[0] <= y(p) ? 13 : -7); }
    return `<line class="${cls}" x1="0" x2="${W - pad}" y1="${y(p)}" y2="${y(p)}"/><text class="${cls}" x="${W - pad + 3}" y="${ty}">${text}</text>`;
  };
  return `<svg class="trend-chart" viewBox="0 0 ${W} ${H1 + gap + H2}" role="img" aria-label="Price on ${P.bars} with 20 EMA, and ADX with DMI lines">
    ${bars.map((b, i) => `<line class="bar ${b.c >= b.o ? 'u' : 'd'}" x1="${x(i)}" x2="${x(i)}" y1="${y(b.h)}" y2="${y(b.l)}"/><line class="bar ${b.c >= b.o ? 'u' : 'd'}" x1="${x(i)}" x2="${x(i) + 2.2}" y1="${y(b.c)}" y2="${y(b.c)}"/>`).join('')}
    <path class="ema" d="${path(b => b.ema, y)}"/>
    ${sig.state === 'range' ? lvl(sig.high, 'en', 'High') + lvl(sig.low, 'st', 'Low')
      : sig.state !== 'watch' ? lvl(sig.target, 'tg', 'Target') + lvl(sig.trigger, 'en', 'Entry') + lvl(sig.stop, 'st', 'Stop') : lvl(sig.level, 'en', 'Level')}
    ${live ? lvl(live, 'live', 'Live ' + num(live)) : ''}
    <line class="mid" x1="0" x2="${W - pad}" y1="${y2(25)}" y2="${y2(25)}"/><text class="mid" x="${W - pad + 3}" y="${y2(25) + 3}">25</text>
    <path class="pdi" d="${path(b => b.pdi, y2)}"/><path class="mdi" d="${path(b => b.mdi, y2)}"/><path class="trendl" d="${path(b => b.adx, y2)}"/>
    <text class="mid" x="2" y="${H1 + gap + 9}">ADX (thick) · +DMI (green) · −DMI (red, dashed)</text><text class="mid" x="2" y="10">${esc(bars[0].date)} to ${esc(bars[bars.length - 1].date)} · completed ${P.bars} · 20 EMA${live ? ' · ' + P.live : ''}</text></svg>`;
}

function trendDetailRender() {
  const box = el('detail'); if (!box) return;
  const row = TREND.board?.rows.find(r => r.id === TREND.pick), d = TREND.detail;
  if (!row) { box.innerHTML = '<p class="empty">Pick a stock to see what to trade, the risk, the stop loss and the chart.</p>'; return; }
  const neutral = row.direction === 'neutral';
  const head = `<div class="trend-head"><h3>${esc(row.symbol)} · ${esc(row.setup)} ${neutral ? '<span class="chip">no direction</span>' : `<span class="chip ${row.direction === 'bullish' ? 'good' : 'bad'}">${row.direction}</span>`}</h3>
    <button class="ghost" data-open="${esc(row.symbol)}">Open chain ↗</button></div><p class="trend-why">${esc(row.why)}</p>`;
  if (!d) { box.innerHTML = head + '<p class="empty">Loading…</p>'; return; }
  if (d.error) { box.innerHTML = head + `<p class="empty">${esc(d.error)}</p>`; return; }
  const s = d.state, p = d.plan, rec = row.record, dc = d.decision;
  const points = list => list.map(t2 => `<li class="${list === dc.pros ? 'y' : 'n'}">${esc(t2)}</li>`).join('');
  const call = dc ? `<h4>The desk's call</h4><div class="trend-call ${dc.call.toLowerCase()}"><b>${dc.call}</b>${esc(dc.why)}</div>
    ${dc.pros.length || dc.cons.length ? `<ul class="trend-checks">${points(dc.pros)}${points(dc.cons)}</ul>` : ''}` : '';
  let trade = '';
  if (p) {
    const best = p.ideas.find(x => x.name === p.best), can = row.verdict !== 'skip' && row.verdict !== 'watch' && !row.held && (!TREND.board?.auto || row.verdict === 'enter' || row.verdict === 'range');   // the 1-hour desk trades only a triggered setup
    const k = p.ideas.indexOf(best);
    const flags = (TREND.board?.auto && !row.held ? '<p class="trend-flag">Auto paper trading is on: when this setup is on Enter with a live price, the recommended position below is paper traded by itself, one lot.</p>' : '') + (p.stale ?'<p class="trend-flag">Market closed or chain not live: prices are the last reported quotes. Paper trades need live market hours.</p>' : '') +
      (row.held ? '<p class="trend-flag">You already have an open paper trade in this stock.</p>' : '') +
      (row.price_stale ? `<p class="trend-flag bad">${row.ltp ? `The stock price is ${Math.round(row.ltp_age)}s old` : 'No live stock price arrived'}: its refresh failed, so the entry check above is not reliable.</p>` : '') +
      (row.verdict === 'armed' ? `<p class="trend-flag">Not triggered yet: enter only once the stock trades ${row.direction === 'bullish' ? 'above' : 'below'} ${num(row.trigger)}.</p>` : '') +
      (row.verdict === 'skip' ? `<p class="trend-flag bad">${esc(row.why)}</p>` : '') +
      (d.event?.inside ? `<p class="trend-flag bad">${esc(d.event.next.title || 'Scheduled event')} on ${esc(d.event.next.date)} falls before this expiry: a result can gap the stock.</p>` : d.event && !d.event.checked ? '<p class="trend-flag">Event calendar could not be checked: review results dates before entry.</p>' : '');
    const money = (v, i) => `<div><dt>${esc(p.case_labels[i])}</dt><dd class="${tone(v)}">${sinr(v)}</dd></div>`;
    trade = `<h4>What to trade · expiry ${esc(p.expiry || '')} (${p.days} days)</h4>` + flags + (best ? `<div class="trend-idea first">
        <div class="t"><b>${esc(best.name)}</b> <span class="chip good">recommended</span><span class="b"><button class="ghost" data-payoff="${k}">Payoff</button><button class="primary" data-trade="${k}" ${can ? '' : 'disabled'}>Paper trade</button></span></div>
        <div class="legs">${best.legs.map(l => `<span class="${l.qty > 0 ? 'up' : 'down'}">${l.qty > 0 ? 'Buy' : 'Sell'} ${num(l.strike, 0)} ${l.type} @ ${num(l.price)}</span>`).join('')}</div>
        <p class="trend-pick">${esc(best.why)}</p>
        <dl><div><dt>${best.net >= 0 ? 'You receive' : 'You pay'} (1 lot of ${best.lot})</dt><dd>${inr(best.cost)}</dd></div>
          ${best.cases.map(money).join('')}
          <div><dt>Worst case (held to expiry)</dt><dd class="down">${sinr(-best.max_loss)}</dd></div>
          <div><dt>Best case</dt><dd class="up">${best.max_profit == null ? 'open-ended' : sinr(best.max_profit)}</dd></div>
          <div><dt>Breakeven${best.breakevens.length > 1 ? 's' : ''} at expiry</dt><dd>${best.breakevens.map(x => num(x)).join(' and ') || '—'}</dd></div></dl>
        ${best.warnings.map(w => `<p class="trend-flag">${esc(w)}</p>`).join('')}</div>
      <h4>Stop loss and exit plan</h4><ul class="trend-notes">${p.exits.map(n => `<li>${esc(n)}</li>`).join('')}</ul>`
      : '<p class="trend-flag bad">No option position gives a sensible payoff on this chain. Skip it.</p>') +
      `<h4>All choices compared</h4><div class="trend-cmp"><table class="trend-table"><thead><tr><th>Position</th><th>Cost</th>${p.case_labels.map(l => `<th>${esc(l)}</th>`).join('')}<th>Max loss</th><th title="${neutral ? 'Weighted outcome as a share of the risk' : 'Gain at target divided by loss at stop'}">Edge</th><th></th></tr></thead><tbody>${p.ideas.map((x, i) => `<tr class="${x === best ? 'on' : ''}">
        <td><b>${esc(x.name)}</b><br><small>${x.legs.map(l => `${l.qty > 0 ? 'B' : 'S'} ${num(l.strike, 0)} ${l.type}`).join(' / ')}</small><br><small class="w">${esc(x.why || '')}</small></td>
        <td class="n">${sinr(x.net >= 0 ? x.cost : -x.cost)}</td>${x.cases.map(v => `<td class="n ${tone(v)}">${sinr(v)}</td>`).join('')}
        <td class="n">${inr(x.max_loss)}</td><td class="n"><b>${esc(x.headline)}</b></td>
        <td>${x === best ? '' : `<button class="ghost" data-trade="${i}" ${can ? '' : 'disabled'}>Trade</button>`}</td></tr>`).join('')}</tbody></table></div>
      <p class="note">Estimates for about ${p.hold_days} days from now at today's implied volatility, one lot, before brokerage and charges. Naked short options are never offered: their loss is open-ended.</p>
      ${p.notes.length ? `<ul class="trend-notes">${p.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
      ${p.chain ? `<h4>What the option chain says</h4><dl class="trend-facts">${p.chain.facts.map(([k2, v]) => `<div><dt>${esc(k2)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>` : ''}`;
  } else if (d.plan_error) trade = `<h4>What to trade</h4><p class="trend-flag">${esc(d.plan_error)}</p>`;
  const facts = [['Trend', s.trend], ['ADX', s.adx], ['+DMI / −DMI', `${s.pdi} / ${s.mdi}`], ['Dominant', s.dominant + (s.dominance_by ? ` by ${s.dominance_by}, ${s.dominance_since}` : '')],
    [P.higher, `${s.weekly}${s.weekly_adx != null ? ` · ADX ${s.weekly_adx}` : ''}`], ['20 EMA', num(s.ema20)], ['ATR', num(s.atr)]];
  if (rec) facts.push(['Your trades in this setup', rec.n ? `${rec.wins}/${rec.n} won · ${sinr(rec.pnl)}` : 'none closed yet']);
  const checks = row.checks.length ? `<h4>Why this setup</h4><ul class="trend-checks">${row.checks.map(([t2, okay]) => `<li class="${okay ? 'y' : 'n'}">${esc(t2)}</li>`).join('')}</ul>` : '';
  box.innerHTML = head + call + trade + '<h4>Chart</h4>' + trendChart(d, row) + `<dl class="trend-facts">${facts.map(([k2, v]) => `<div><dt>${esc(k2)}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>` + checks;
}

async function trendTrade(k, button) {
  const row = TREND.board?.rows.find(r => r.id === TREND.pick), idea = TREND.detail?.plan?.ideas[k]; if (!row || !idea) return;
  const call = TREND.board?.auto ? idea.decision : null;        // use the selected position's call
  const against = call && call.call !== 'Take';
  if (!confirm(`${against ? `The desk's call is ${call.call}: ${call.why}\n\nOVERRIDE and paper trade` : 'Paper trade'} one lot of ${idea.name} on ${row.symbol}?\n\nWorst case: ${inr(idea.max_loss)}. Filled at live bid/ask, before charges. No broker order is placed.`)) return;
  button.disabled = true; button.textContent = 'Recording…';
  try {
    await api(P.api + '/paper', {symbol: row.symbol, signal: row.id, idea: idea.name, override: !!against});
    toast(`${P.name} paper trade recorded. See My ${P.name} trades.`); loadPaper();
    TREND.section = 'trades'; store(P.key + '-s', 'trades'); await trendLoad();
  } catch (e) { toast(e.message, true); button.disabled = false; button.textContent = 'Paper trade'; }
}

// Show the idea's payoff curve in the builder. Trading from the builder is a normal desk trade, not an ADX one.
async function trendPayoff(k) {
  const row = TREND.board?.rows.find(r => r.id === TREND.pick), idea = TREND.detail?.plan?.ideas[k]; if (!row || !idea) return;
  view('chain'); loadSymbol(row.symbol);
  const ready = async test => { for (let i = 0; i < 60 && !test(); i++) await new Promise(r => setTimeout(r, 250)); return test(); };
  if (!await ready(() => S.chain?.symbol === row.symbol)) return toast('The option chain did not load. Try again.', true);
  if (S.chain.expiry_date !== idea.expiry) {
    const e = S.chain.expiries.find(x => dmyToIso(x.date) === idea.expiry);
    if (!e) return toast('This idea\'s expiry is not listed now.', true);
    S.expiry = e.ts; S.built = ''; pollChain();
    if (!await ready(() => S.chain?.symbol === row.symbol && S.chain.expiry_date === idea.expiry)) return toast('The expiry did not load. Try again.', true);
  }
  const legs = [];
  for (const l of idea.legs) {
    const o = optionAt(l.type, l.strike);
    if (!o?.symbol) return toast(`Strike ${l.strike} ${l.type} is not in the chain now.`, true);
    legs.push({symbol: o.symbol, type: l.type, strike: l.strike, side: Math.sign(l.qty), lots: 1});
  }
  S.legs = legs; strategyName = idea.name;
  renderLegs(); updateLegPrices(); renderSummary(); updateChain(S.chain, windowRows(S.chain), null);
  toast('Payoff shown in the builder. To record it as an ' + P.name + ' trade, use Paper trade on the ' + P.name + ' tab.');
}

async function trendMark(b) {
  const close = !!b.dataset.close;
  if (close && !confirm('Close this paper trade at live bid/ask?')) return;
  b.disabled = true;
  try { await api('/api/options/paper/mark', {id: b.dataset.mark || b.dataset.close, close}); toast(close ? 'Paper exit recorded.' : 'Marked at live bid/ask.'); loadPaper(); }
  catch (e) { toast(e.message, true); }
  trendLoad();
}

async function trendTrades() {
  const body = el('body'); let T;
  try { T = await api(P.api + '/trades'); } catch (e) { body.innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  if (TREND.section !== 'trades') return;
  const legs = t => t.legs.map(l => `${l.qty > 0 ? 'B' : 'S'} ${num(l.strike, 0)} ${l.type}`).join(' / ');
  const dir = t => t.direction === 'neutral' ? '<span class="chip">Range</span>' : `<span class="chip ${t.direction === 'bullish' ? 'good' : 'bad'}">${t.direction === 'bullish' ? 'Bull' : 'Bear'}</span>`;
  const open = T.open.map(t => `<tr><td><b>${esc(t.symbol)}</b> ${dir(t)}${t.auto ? ' <span class="chip">auto</span>' : ''}</td><td>${esc(t.setup)}<br><small>${esc(legs(t))}</small></td>
    <td class="n">${num(t.spot)}${t.spot_stale ? ` <small class="stale" title="The live price refresh failed.">${t.spot_source === 'live' ? `stale ${Math.round(t.spot_age)}s` : 'close'}</small>` : ''}</td><td class="n">${t.direction === 'neutral' ? num(t.low) + ' – ' + num(t.high) : t.trailed ? num(t.stop_now) + ' <small>raised</small>' : num(t.stop)}</td><td class="n">${t.direction === 'neutral' ? '<small>range</small>' : num(t.target)}</td>
    <td class="n">${t.progress == null ? '—' : t.progress + '%'}</td>
    <td class="n ${tone(t.pnl)}">${t.pnl == null ? (t.last_mark != null ? `<small class="stale" title="Not every option price arrived, so the current P&L is unknown">last mark ${sinr(t.last_mark)}, now unknown</small>` : '<small>not marked</small>') : sinr(t.pnl) + (t.estimated ? ' <small>est.</small>' : '')}</td><td class="n">${t.days_held}d · ${t.days_left}d left</td>
    <td class="why"><span class="verdict ${t.action === 'exit' ? 'skip' : t.action === 'profit' ? 'ripe' : t.action === 'adjust' ? 'adjust' : ''}">${t.action === 'hold' ? 'Hold' : t.action === 'profit' ? 'Book' : t.action === 'adjust' ? 'Adjust' : 'Exit'}</span> ${esc(t.advice)}</td>
    <td><button class="ghost" data-mark="${esc(t.id)}">Mark</button> <button class="ghost" data-close="${esc(t.id)}">Close</button></td></tr>`).join('');
  const pc = x => (x > 0 ? '+' : '') + x + '%';
  const rec = r => r.n ? `${r.wins}/${r.n} won (${r.win_rate}%) · <b class="${tone(r.pnl)}">${sinr(r.pnl)}</b> · average ${pc(r.avg_return)} of max loss` : 'no closed trades yet';
  const groups = T.groups.map(g => `<tr><td>${g.by === 'setup' ? 'Setup' : 'Structure'}</td><td><b>${esc(g.name)}</b></td><td class="n">${g.n}</td><td class="n">${g.win_rate}%</td><td class="n ${tone(g.pnl)}">${sinr(g.pnl)}</td><td class="n ${tone(g.avg_return)}">${pc(g.avg_return)}</td></tr>`).join('');
  const closed = T.closed.map(t => `<tr><td>${esc(t.updated.slice(0, 10))}</td><td><b>${esc(t.symbol)}</b> ${dir(t)}</td><td>${esc(t.setup)}</td><td>${esc(t.name.split(' · ').pop())}</td><td class="n">${t.score == null ? '—' : t.score}</td><td class="n ${tone(t.pnl)}">${sinr(t.pnl)}</td><td class="why">${t.review?.mistake ? '<span class="chip warn">mistake</span> ' : ''}${esc(t.review?.text || '')}</td></tr>`).join('');
  const L = T.lessons, mistakes = L.mistakes.map(x => `<tr><td><b>${esc(x.name)}</b></td><td class="n">${x.n}</td><td class="n down">${sinr(-x.cost)}</td></tr>`).join('');
  body.innerHTML = `<div class="trend-learn">
    <h2>Open ${P.name} trades</h2>${open ? `<div class="trend-list"><table class="trend-table"><thead><tr><th>Stock</th><th>Setup</th><th>Stock now</th><th>Stop / range</th><th>Target</th><th title="How far the stock has moved from entry toward the target">To target</th><th>P&amp;L</th><th>Held</th><th>What to do</th><th></th></tr></thead><tbody>${open}</tbody></table></div>
      <p class="note">Guidance uses the stock only: its stop, its target, ${P.bar} DMI dominance and time. Once the stock has moved ${T.policy.trail}R your way the stop follows one risk-unit behind its best price, never below your entry. During market hours P&amp;L is estimated from last traded prices every minute; Mark gives the exact bid/ask figure.</p>` : `<p class="empty">No open ${P.name} trade. ${TREND.board?.auto ? 'One is opened by itself when a setup turns Enter in market hours; you can also pick' : 'Pick'} a setup and press Paper trade.</p>`}
    <h2>What your trades have taught so far</h2><p class="trend-rec">All closed ${P.name} trades:${rec(T.total)}</p>
    ${groups ? `<div class="trend-list"><table class="trend-table"><thead><tr><th></th><th>Group</th><th>Trades</th><th>Won</th><th>P&amp;L</th><th>Avg return on risk</th></tr></thead><tbody>${groups}</tbody></table></div>` : ''}
    ${L.changes.length ? `<h2>Rules changed by your mistakes</h2><ul class="trend-notes">${L.changes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    ${mistakes ? `<div class="trend-list"><table class="trend-table"><thead><tr><th>Mistake</th><th>Times</th><th title="Money lost to this mistake: the profit given back, the extra loss from a late exit, or the loss itself">Cost</th></tr></thead><tbody>${mistakes}</tbody></table></div>` : ''}
    ${L.notes.length ? `<ul class="trend-notes">${L.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    <ul class="trend-notes"><li>Every trade is stored with its score and the conditions it was taken in (with or against the market and the ${P.higher.toLowerCase()} trend). Results are grouped by those conditions and each group moves the score by at most 8 points; a group needs 3 closed trades before it counts.</li>
      <li>After each close the desk checks what went wrong: a profit given back, a late exit, a stalled stock, or a bought option that lost although the stock was right. Two of the same mistake change the rule that allowed it.</li>
      <li>After ${T.learn_min} closed trades in a setup, a losing setup is ranked lower on the board and flagged.</li>
      <li>After ${T.learn_min} closed trades of one position type (bought option, debit spread, credit spread, long or short volatility) in a setup, that type is marked up if it made money and down if it lost.</li>
</ul>
    ${closed ? `<h2>Closed</h2><div class="trend-list"><table class="trend-table"><thead><tr><th>Closed</th><th>Stock</th><th>Setup</th><th>Structure</th><th>Score</th><th>P&amp;L</th><th>What happened</th></tr></thead><tbody>${closed}</tbody></table></div>` : ''}</div>`;
}
}

trendTab({id: 'trend', api: '/api/trend', key: 'desk-trend', open: 'openTrend', name: 'ADX', bar: 'daily', bars: 'daily bars', higher: 'Weekly', unit: 'd',
  live: 'live price = today, not yet a bar',
  behind: (have, want) => `Daily bars end on ${have}, but the last completed session is ${want}. Refresh prices on the main workspace, then Rescan.`});
trendTab({id: 'trend1h', api: '/api/trend1h', key: 'desk-trend1h', open: 'openTrend1h', name: 'ADX 1H', bar: 'hourly', bars: '1-hour bars', higher: 'Daily', unit: 'h',
  live: 'live price = the hour still forming, not yet a bar',
  behind: (have, want) => `1-hour bars end at ${have}, but the last completed hour is ${want}. They are fetched again by themselves every two minutes in market hours (on an exchange holiday there is nothing new); press Rescan to fetch now.`});
