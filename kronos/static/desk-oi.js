'use strict';
// OI tab: Sensibull open interest for the selected stock, recorded locally (see kronos/sensibull.py).
const OI = {windows: [[5, '5m'], [15, '15m'], [30, '30m'], [60, '1h'], [180, '3h']], minutes: Number(store('desk-oi-min')) || 30, seq: 0, timer: null};

window.openOI = () => {
  $('oi-windows').innerHTML = OI.windows.map(([m, t]) => `<button type="button" data-m="${m}" aria-pressed="${m === OI.minutes}">${t}</button>`).join('');
  loadOI(false);
  clearInterval(OI.timer);
  OI.timer = setInterval(() => { if (S.view === 'oi' && !paused()) loadOI(false); }, 60000);
};
$('oi-windows').addEventListener('click', e => {
  const b = e.target.closest('button[data-m]'); if (!b) return;
  OI.minutes = Number(b.dataset.m); store('desk-oi-min', OI.minutes);
  for (const x of $('oi-windows').children) x.setAttribute('aria-pressed', x === b);
  loadOI(false);
});
$('oi-refresh').onclick = () => loadOI(true);

const sgn = x => ok(x) ? (x > 0 ? '+' : x < 0 ? '−' : '') + big(Math.abs(x)) : '—';

async function loadOI(fresh) {
  const symbol = S.symbol, seq = ++OI.seq;
  $('oi-title').textContent = symbol ? `Open interest · ${symbol}` : 'Open interest';
  if (!symbol) { $('oi-body').innerHTML = '<p class="empty">Pick a stock first.</p>'; return; }
  const q = new URLSearchParams({symbol, minutes: OI.minutes});
  if (fresh) q.set('fresh', '1');
  try {
    const snap = await api('/api/sensibull/oi?' + q);
    const chg = snap.changes;
    if (seq !== OI.seq) return;
    renderOI(snap, chg);
  } catch (e) {
    if (seq !== OI.seq) return;
    $('oi-summary').innerHTML = ''; $('oi-meta').textContent = ''; $('oi-history').innerHTML = ''; $('oi-body').innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
  loadOIHistory(symbol, seq);
}

function renderOI(s, chg) {
  // Show the 10 strikes either side of ATM that carry any OI (Sensibull's default view); totals above still use every strike.
  const all = s.strikes, at = all.findIndex(r => r.strike === s.atm), mid = at >= 0 ? at : Math.floor(all.length / 2);
  s = {...s, shown: all.slice(Math.max(0, mid - 10), mid + 11).filter(r => r.call_oi || r.put_oi)};
  const calls = s.strikes.reduce((a, r) => r.call_oi > a.call_oi ? r : a, s.strikes[0]);
  const puts = s.strikes.reduce((a, r) => r.put_oi > a.put_oi ? r : a, s.strikes[0]);
  const moved = new Map((chg?.ready ? chg.rows : []).map(r => [r.strike, r]));
  const peak = Math.max(...s.shown.map(r => Math.max(r.call_oi, r.put_oi)), 1);
  const peakMove = Math.max(...(chg?.rows || []).map(r => Math.max(Math.abs(r.call_change), Math.abs(r.put_change))), 1);
  const dayCall = s.call_oi_change, dayPut = s.put_oi_change;
  $('oi-meta').textContent = `Sensibull data as of ${s.data_time ? new Date(s.data_time).toLocaleString('en-IN', {timeZone: 'Asia/Kolkata'}) : '-'} | front + next month: ${s.expiries.join(', ')} | ${(Date.now()-new Date(s.data_time))/60000 > 10 ? 'EOD / stale for live use' : 'recent snapshot'}`;
  $('oi-summary').innerHTML = [
    ['Spot', `${num(s.spot)} <small>${sgn(s.spot_change)} today</small>`], ['PCR', `${ok(s.pcr) ? Number(s.pcr).toFixed(2) : '-'} <small>${ok(s.pcr_change) ? (s.pcr_change > 0 ? '+' : '') + Number(s.pcr_change).toFixed(3) : '-'} today</small>`],
    ['Largest call OI (combined)', `${num(calls.strike, 0)} · ${big(calls.call_oi)}`], ['Largest put OI (combined)', `${num(puts.strike, 0)} · ${big(puts.put_oi)}`],
    ['Call OI', `${big(s.call_oi)} <small class="${tone(-dayCall)}">${sgn(dayCall)} today</small>`],
    ['Put OI', `${big(s.put_oi)} <small class="${tone(dayPut)}">${sgn(dayPut)} today</small>`]]
    .map(([k, v]) => `<div><small>${k}</small><strong>${v}</strong></div>`).join('');
  const split = Object.entries(s.by_expiry || {}).map(([e, p]) => `<tr><td>${esc(e)}</td><td>${big(p.call_oi)}</td><td>${big(p.put_oi)}</td><td>${ok(p.pcr) ? Number(p.pcr).toFixed(2) : '—'}</td><td>${sgn(p.call_oi_change)}</td><td>${sgn(p.put_oi_change)}</td></tr>`).join('');
  const splitTable = `<table class="oi-table"><thead><tr><th>Expiry</th><th>Call OI</th><th>Put OI</th><th>PCR</th><th>Call change today</th><th>Put change today</th></tr></thead><tbody>${split}</tbody></table>`;
  const note = chg?.ready ? `Change over the last ${chg.covered_minutes} min (${chg.snapshots} snapshots today)` : esc(chg?.note || 'Change since a few minutes ago needs recorded snapshots.');
  $('oi-body').innerHTML = splitTable + `<table class="oi-table"><thead><tr><th colspan="3" class="down">Calls</th><th>Strike</th><th colspan="3" class="up">Puts</th></tr>
    <tr><th>Change</th><th>OI</th><th></th><th></th><th></th><th>OI</th><th>Change</th></tr></thead><tbody>${s.shown.map(r => {
      const m = moved.get(r.strike), atm = r.strike === s.atm;
      return `<tr class="${atm ? 'atm' : ''}"><td class="${tone(m ? -m.call_change : 0)}">${m ? sgn(m.call_change) : ''}</td><td>${big(r.call_oi)}</td>
        <td class="bar-cell l"><i class="cb" style="width:${(r.call_oi / peak * 100).toFixed(1)}%"></i>${m ? `<i class="mv ${m.call_change >= 0 ? 'in' : 'out'}" style="width:${(Math.abs(m.call_change) / peakMove * 100).toFixed(1)}%"></i>` : ''}</td>
        <td class="k">${num(r.strike, 0)}</td>
        <td class="bar-cell r"><i class="pb" style="width:${(r.put_oi / peak * 100).toFixed(1)}%"></i>${m ? `<i class="mv ${m.put_change >= 0 ? 'in' : 'out'}" style="width:${(Math.abs(m.put_change) / peakMove * 100).toFixed(1)}%"></i>` : ''}</td>
        <td>${big(r.put_oi)}</td><td class="${tone(m ? m.put_change : 0)}">${m ? sgn(m.put_change) : ''}</td></tr>`;
    }).join('')}</tbody></table><p class="note">${note}. OI includes buyers and sellers. Large OI strikes are possible levels, not guaranteed support or resistance. Change windows end at the shown exchange time.</p>`;
}

async function loadOIHistory(symbol, seq) {
  try {
    const [oi, fut] = await Promise.all([api('/api/sensibull/series?' + new URLSearchParams({symbol, kind: 'oi'})),
      api('/api/sensibull/series?' + new URLSearchParams({symbol, kind: 'fut'})).catch(() => null)]);
    if (seq !== OI.seq) return;
    const futBy = new Map((fut?.rows || []).map(r => [r.at, r]));
    const rows = [...oi.rows].sort((a,b) => String(a.at).localeCompare(String(b.at))).slice(-15).reverse();
    $('oi-history').innerHTML = `<table class="oi-table hist"><thead><tr><th>Date</th><th>Price</th><th>Call OI</th><th>Put OI</th><th>PCR</th><th>Futures OI</th><th>Fut activity</th><th>Expiry scope</th></tr></thead><tbody>${rows.map(r => {
      const candidate = futBy.get(r.at); const f = candidate && JSON.stringify([...(candidate.expiries || [])].sort()) === JSON.stringify([...(r.expiries || [])].sort()) ? candidate : null;
      return `<tr><td>${esc(r.at)}</td><td>${num(r.ltp)} <small class="${tone(r.ltp_change_percentage)}">${pct(r.ltp_change_percentage)}</small></td><td>${big(r.call_oi)}</td><td>${big(r.put_oi)}</td>
        <td>${ok(r.pcr) ? Number(r.pcr).toFixed(2) : '—'}</td><td>${f ? big(f.oi) + ` <small class="${tone(f.oi_change_percentage)}">${pct(f.oi_change_percentage)}</small>` : '—'}</td>
        <td>${f ? esc(({lng_bld: 'Price up / OI up', sht_bld: 'Price down / OI up', sht_cov: 'Price up / OI down', lng_unv: 'Price down / OI down'})[f.activity] || f.activity || '') : '—'}</td><td>${esc((r.expiries || []).join(', '))}</td></tr>`;
    }).join('')}</tbody></table>`;
  } catch (e) {
    if (seq === OI.seq) $('oi-history').innerHTML = `<p class="empty">${esc(e.message)}</p>`;
  }
}
