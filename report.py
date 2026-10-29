"""Self-contained HTML report for the pattern scanners: one tab per scanner,
Daily/Weekly filter, min-score filter, sortable columns, a sparkline of the
pattern with pivot/stop lines, and a TradingView watchlist copy box."""
from __future__ import annotations

import html
import json

from scanners import REGISTRY

STYLE = """
:root{--bg:#fafaf9;--card:#fff;--fg:#1c1917;--muted:#78716c;--border:#e7e5e4;--hover:#f5f5f4;
--accent:#1c1917;--accent-fg:#fafaf9;--green:#15803d;--green-bg:#dcfce7;--red:#b91c1c;--amber:#b45309;--amber-bg:#fef3c7;
--line:#57534e;--zone:rgba(21,128,61,.07)}
@media (prefers-color-scheme:dark){:root{--bg:#0c0a09;--card:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--border:#292524;--hover:#262220;
--accent:#f5f5f4;--accent-fg:#1c1917;--green:#4ade80;--green-bg:#14532d;--red:#f87171;--amber:#fbbf24;--amber-bg:#422006;
--line:#d6d3d1;--zone:rgba(74,222,128,.08)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,"Segoe UI",system-ui,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1400px;margin:0 auto;padding:2rem 1rem 4rem}
h1{font-size:1.6rem;margin:0 0 .25rem;letter-spacing:-.02em}
.sub{color:var(--muted);font-size:.85rem;margin:0 0 1.5rem}
.tabs{display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:1rem}
.tab{border:1px solid var(--border);background:var(--card);color:var(--fg);border-radius:.6rem;padding:.6rem .9rem;cursor:pointer;text-align:left;font:inherit}
.tab .t{font-weight:650}.tab .c{color:var(--muted);font-size:.78rem}
.tab.on{background:var(--accent);color:var(--accent-fg);border-color:var(--accent)}.tab.on .c{color:inherit;opacity:.75}
.desc{color:var(--muted);font-size:.85rem;margin:0 0 .8rem}
.bar{display:flex;gap:.75rem;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--border);border-bottom:none;border-radius:.75rem .75rem 0 0;padding:.7rem .9rem}
.seg{display:inline-flex;border:1px solid var(--border);border-radius:.5rem;overflow:hidden}
.seg button{border:0;background:transparent;color:var(--fg);padding:.3rem .7rem;font:inherit;font-size:.8rem;cursor:pointer}
.seg button.on{background:var(--accent);color:var(--accent-fg)}
.bar label{font-size:.8rem;color:var(--muted);display:flex;gap:.35rem;align-items:center}
.bar input{font:inherit;font-size:.82rem;padding:.3rem .5rem;border:1px solid var(--border);border-radius:.45rem;background:var(--bg);color:var(--fg)}
.bar input[type=number]{width:64px}.bar input.q{width:150px}
.tv{display:flex;gap:.4rem;align-items:center;flex:1 1 300px}
.tv input{flex:1;min-width:0}
.tv button{font:inherit;font-size:.8rem;font-weight:600;padding:.32rem .75rem;border-radius:.45rem;border:0;background:var(--accent);color:var(--accent-fg);cursor:pointer}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--border);border-radius:0 0 .75rem .75rem}
table{border-collapse:collapse;width:100%;font-size:.82rem;font-variant-numeric:tabular-nums}
th{position:sticky;top:0;background:var(--card);text-align:right;color:var(--muted);font-weight:600;font-size:.7rem;text-transform:uppercase;letter-spacing:.03em;padding:.55rem .7rem;border-bottom:1px solid var(--border);cursor:pointer;white-space:nowrap;user-select:none}
td{padding:.45rem .7rem;text-align:right;white-space:nowrap;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:0}tbody tr:hover{background:var(--hover)}
th.l,td.l{text-align:left}
.sym{font-weight:650}.sym a{color:inherit;text-decoration:none}.sym a:hover{text-decoration:underline}
.pill{display:inline-block;font-size:.66rem;font-weight:700;padding:.05rem .4rem;border-radius:999px;margin-left:.35rem;vertical-align:1px}
.pill.tf{background:var(--hover);color:var(--muted)}.pill.both{background:var(--green-bg);color:var(--green)}.pill.multi{background:var(--amber-bg);color:var(--amber)}
.score{display:inline-flex;align-items:center;gap:.4rem}
.score i{display:inline-block;height:6px;width:48px;border-radius:3px;background:var(--hover);overflow:hidden}
.score i b{display:block;height:100%;background:var(--green)}
.dist{color:var(--green);font-weight:600}.risk{color:var(--red)}
.notes{color:var(--muted);text-align:left}
.empty{padding:2rem;text-align:center;color:var(--muted)}
.ana{cursor:pointer;min-width:190px}
.ana:hover .v{text-decoration:underline}
.v{display:inline-block;font-size:.7rem;font-weight:700;padding:.1rem .45rem;border-radius:999px;white-space:nowrap}
.v.good{background:var(--green-bg);color:var(--green)}
.v.flat{background:var(--hover);color:var(--muted)}
.v.bad{background:var(--red-bg);color:var(--red)}
.chips{display:block;color:var(--muted);font-size:.7rem;margin-top:.15rem}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:flex-start;justify-content:center;padding:4vh 1rem;z-index:50}
.modal.on{display:flex}
.sheet{background:var(--card);border:1px solid var(--border);border-radius:.9rem;max-width:620px;width:100%;max-height:92vh;overflow-y:auto;box-shadow:0 20px 50px rgba(0,0,0,.25)}
.sheet header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--border);padding:1rem 1.2rem;display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;border-radius:.9rem .9rem 0 0}
.sheet h2{margin:0;font-size:1.05rem;letter-spacing:-.01em}
.sheet .sub2{color:var(--muted);font-size:.78rem;margin-top:.15rem}
.sheet .x{border:0;background:transparent;color:var(--muted);font-size:1.4rem;line-height:1;cursor:pointer;padding:0 .2rem}
.sheet .body{padding:1rem 1.2rem 1.4rem}
.lead{font-size:.95rem;margin:0 0 .5rem;font-weight:600}
.meter{height:7px;border-radius:4px;background:var(--hover);overflow:hidden;margin:.6rem 0 .3rem;position:relative}
.meter b{position:absolute;top:0;height:100%;border-radius:4px}
.meter b.pos{background:var(--green)}.meter b.neg{background:var(--red)}
.meter-note{display:flex;justify-content:space-between;color:var(--muted);font-size:.7rem;margin-bottom:1.1rem}
.sec{margin-bottom:1.1rem}
.sec h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:0 0 .45rem}
.sec ul{margin:0;padding:0;list-style:none}
.sec li{display:flex;gap:.55rem;padding:.3rem 0;font-size:.87rem;line-height:1.45}
.sec li .dot{flex:none;width:7px;height:7px;border-radius:50%;margin-top:.45rem}
.dot.good{background:var(--green)}.dot.flat{background:var(--muted);opacity:.5}.dot.bad{background:var(--red)}
footer{color:var(--muted);font-size:.78rem;margin-top:1.5rem;line-height:1.6}
svg.spark{display:block}
.kronos-cell{cursor:pointer;min-width:180px}
.kronos-cell:hover .v{text-decoration:underline}
"""

SCRIPT = r"""
const DATA = __DATA__;
const META = __META__;
let active = DATA.scanners[0].name, tf = 'all', sortKey = 'score', sortDir = -1;

function fmt(v, d=2){ return v==null ? '' : Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d}); }
function spark(ch){
  const w=150,h=40,p=3, xs=ch.closes; if(!xs||xs.length<2) return '';
  const preds=ch.pred_closes||[];
  const totalPoints = xs.length + preds.length;
  const vals=xs.concat(preds, Object.values(ch.lines)); const lo=Math.min(...vals), hi=Math.max(...vals), span=(hi-lo)||1;
  const X=i=>p+i*(w-2*p)/(Math.max(1, totalPoints-1)), Y=v=>h-p-(v-lo)*(h-2*p)/span;
  let s=`<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`;
  if(ch.zone_start!=null) s+=`<rect x="${X(ch.zone_start)}" y="0" width="${X(xs.length-1)-X(ch.zone_start)}" height="${h}" fill="var(--zone)"/>`;
  const col={pivot:'var(--green)',support:'var(--red)',ma:'var(--amber)',bottom:'var(--muted)'};
  for(const [k,v] of Object.entries(ch.lines)) s+=`<line x1="${p}" x2="${w-p}" y1="${Y(v)}" y2="${Y(v)}" stroke="${col[k]||'var(--muted)'}" stroke-width="1" stroke-dasharray="3 2"><title>${k} ${v}</title></line>`;
  if(ch.pred_target) s+=`<line x1="${X(xs.length-1)}" x2="${w-p}" y1="${Y(ch.pred_target)}" y2="${Y(ch.pred_target)}" stroke="#8b5cf6" stroke-width="1" stroke-dasharray="2 2"><title>target ${ch.pred_target}</title></line>`;
  s+=`<polyline fill="none" stroke="var(--line)" stroke-width="1.3" points="${xs.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}"/>`;
  if(preds.length > 0){
    const predPts = [[xs.length-1, xs[xs.length-1]]].concat(preds.map((v,i)=>[xs.length+i, v]));
    s+=`<polyline fill="none" stroke="#8b5cf6" stroke-width="1.5" stroke-dasharray="3 2" points="${predPts.map(([i,v])=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(' ')}"/>`;
  }
  s+=`</svg>`;
  return s;
}
function tvLink(sym){ return 'https://www.tradingview.com/chart/?symbol=NSE:'+encodeURIComponent(sym.replace(/-/g,'_')); }
function vTone(v){ return (v==='Strong accumulation'||v==='Accumulation') ? 'good' : (v==='Mixed' ? 'flat' : 'bad'); }
function esc(t){ const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }

function openAnalysis(i){
  const r = DATA.signals[i], a = r.analysis;
  if(!a || !a.sections) return;
  const m = document.getElementById('modal');
  const tfName = r.tf==='W' ? 'Weekly' : 'Daily';
  const p = a.pressure, w = Math.min(50, Math.abs(p)/2);
  m.querySelector('h2').textContent = r.symbol + '  \u00b7  ' + a.verdict;
  m.querySelector('.sub2').textContent = DATA.scanners.find(s=>s.name===r.scanner).title + ' \u00b7 ' + tfName
      + ' \u00b7 close ' + fmt(r.close) + ' \u00b7 pivot ' + fmt(r.pivot) + ' (' + fmt(r.distance) + '% away)';
  let h = '<p class="lead">' + esc(a.headline) + '</p>'
    + '<div class="meter"><b class="' + (p>=0?'pos':'neg') + '" style="left:' + (p>=0?50:50-w) + '%;width:' + w + '%"></b></div>'
    + '<div class="meter-note"><span>selling pressure</span><span>buying pressure ' + (p>=0?'+':'') + p + '</span></div>';
  for(const sec of a.sections){
    h += '<div class="sec"><h3>' + esc(sec.title) + '</h3><ul>'
      + sec.bullets.map(b=>'<li><span class="dot ' + b.tone + '"></span><span>' + esc(b.text) + '</span></li>').join('')
      + '</ul></div>';
  }
  m.querySelector('.body').innerHTML = h;
  m.classList.add('on');
}
function openKronos(i){
  const r = DATA.signals[i], k = r.kronos;
  if(!k || !k.sections) return;
  const m = document.getElementById('modal');
  const tfName = r.tf==='W' ? 'Weekly' : 'Daily';
  m.querySelector('h2').textContent = r.symbol + '  \u00b7  ' + k.verdict;
  m.querySelector('.sub2').textContent = 'Kronos Foundation Model \u00b7 ' + tfName
      + ' \u00b7 Close ' + fmt(r.close) + ' \u00b7 Pivot ' + fmt(r.pivot) + ' \u00b7 Target ' + fmt(k.target);
  const prob = k.probability, w = Math.min(100, Math.max(0, prob));
  let h = '<p class="lead">' + esc(k.headline) + '</p>'
    + '<div class="meter"><b class="pos" style="left:0;width:' + w + '%"></b></div>'
    + '<div class="meter-note"><span>Breakout Probability</span><span>' + prob + '% (R:R ' + k.rr + 'x)</span></div>';
  for(const sec of k.sections){
    h += '<div class="sec"><h3>' + esc(sec.title) + '</h3><ul>'
      + sec.bullets.map(b=>'<li><span class="dot ' + b.tone + '"></span><span>' + esc(b.text) + '</span></li>').join('')
      + '</ul></div>';
  }
  m.querySelector('.body').innerHTML = h;
  m.classList.add('on');
}

function closeAnalysis(){ document.getElementById('modal').classList.remove('on'); }

function render(){
  const sc = DATA.scanners.find(s=>s.name===active);
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on', b.dataset.name===active));
  document.querySelectorAll('.seg button').forEach(b=>b.classList.toggle('on', b.dataset.tf===tf));
  document.getElementById('desc').textContent = sc.description;
  const q = document.getElementById('q').value.trim().toUpperCase();
  const minScore = parseFloat(document.getElementById('minscore').value)||0;
  const maxDist = parseFloat(document.getElementById('maxdist').value);
  let rows = DATA.signals.filter(s=>s.scanner===active && (tf==='all'||s.tf===tf) && s.symbol.includes(q) && s.score>=minScore && (isNaN(maxDist)||s.distance<=maxDist));
  const val = r => {
    if(sortKey==='analysis') return r.analysis ? r.analysis.pressure : -999;
    if(sortKey==='kronos') return r.kronos ? r.kronos.score : -999;
    const v = sortKey in r ? r[sortKey] : r.metrics[sortKey];
    return Array.isArray(v) ? v.length : v;
  };
  rows.sort((a,b)=>{ const x=val(a), y=val(b); if(x==null) return 1; if(y==null) return -1;
    return (typeof x==='string' ? x.localeCompare(y) : x-y) * sortDir; });

  const hasKronos = DATA.signals.some(s=>s.kronos!=null);
  const mkeys = sc.metric_keys;
  const baseCols = [['symbol','Symbol','l'],['score','Score',''],['chart','Pattern','l'],['close','Close',''],['pivot','Pivot',''],
    ['distance','To pivot %',''],['support','Stop',''],['risk','Risk %',''],['label','Phase','l']];
  if(hasKronos){
    baseCols.push(['kronos','Kronos Breakout','l']);
  }
  baseCols.push(['analysis','Analysis','l'],['notes','Signals','l']);
  const cols = [...baseCols, ...mkeys.map(k=>[k,k,''])];
  const head = cols.map(([k,l,c])=>`<th class="${c}" data-k="${k}">${l}${sortKey===k?(sortDir>0?' ▲':' ▼'):''}</th>`).join('');
  const body = rows.map(r=>{
    const both = DATA.signals.some(o=>o.scanner===r.scanner && o.symbol===r.symbol && o.tf!==r.tf);
    const others = DATA.signals.filter(o=>o.scanner!==r.scanner && o.symbol===r.symbol).map(o=>o.scanner);
    const multi = [...new Set(others)].length;
    return `<tr><td class="l sym"><a href="${tvLink(r.symbol)}" target="_blank" rel="noopener">${r.symbol}</a><span class="pill tf">${r.tf}</span>`
      + (both?'<span class="pill both" title="Same setup on Daily and Weekly">D+W</span>':'')
      + (multi?`<span class="pill multi" title="Also in: ${[...new Set(others)].join(', ')}">+${multi}</span>`:'')
      + `</td><td><span class="score"><i><b style="width:${r.score}%"></b></i>${Math.round(r.score)}</span></td>`
      + `<td class="l">${spark(r.chart)}</td><td>${fmt(r.close)}</td><td>${fmt(r.pivot)}</td>`
      + `<td class="dist">${fmt(r.distance)}</td><td>${fmt(r.support)}</td><td class="risk">${fmt(r.risk,1)}</td><td class="l">${r.label}</td>`
      + (hasKronos ? (r.kronos ? `<td class="l kronos-cell" data-i="${r.i}" title="Click for the full Kronos AI forecast"><span class="v ${r.kronos.tone || 'good'}">${r.kronos.verdict || ''}</span><span class="chips">${(r.kronos.chips||[]).join(' \u00b7 ')}</span></td>` : '<td class="l"></td>') : '')
      + (r.analysis
          ? `<td class="l ana" data-i="${r.i}" title="Click for the full volume + delivery read"><span class="v ${vTone(r.analysis.verdict)}">${r.analysis.verdict || ''}</span><span class="chips">${(r.analysis.chips||[]).join(' \u00b7 ')}</span></td>`
          : '<td class="l"></td>')
      + `<td class="l notes">${(r.notes||[]).join(' · ')}</td>`
      + mkeys.map(k=>`<td>${(r.metrics && r.metrics[k]!=null) ? r.metrics[k] : ''}</td>`).join('')
      + '</tr>';
  }).join('');
  document.getElementById('table').innerHTML = rows.length
    ? `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
    : '<div class="empty">No setups match these filters.</div>';
  document.querySelectorAll('#table td.ana').forEach(td=>td.onclick=()=>openAnalysis(+td.dataset.i));
  if(hasKronos){
    document.querySelectorAll('#table td.kronos-cell').forEach(td=>td.onclick=()=>openKronos(+td.dataset.i));
  }
  document.querySelectorAll('#table th').forEach(th=>th.onclick=()=>{
    const k=th.dataset.k; if(k==='chart') return;
    if(sortKey===k) sortDir*=-1; else { sortKey=k; sortDir=['symbol','label','distance','risk'].includes(k)?1:-1; } render(); });
  document.getElementById('tvlist').value = [...new Set(rows.map(r=>'NSE:'+r.symbol.replace(/-/g,'_')))].join(',');
  DATA.scanners.forEach(s=>{
    const n = DATA.signals.filter(x=>x.scanner===s.name && (tf==='all'||x.tf===tf)).length;
    document.querySelector(`.tab[data-name="${s.name}"] .c`).textContent = n + ' setups';
  });
}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{ active=b.dataset.name; sortKey='score'; sortDir=-1; render(); });
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{ tf=b.dataset.tf; render(); });
['q','minscore','maxdist'].forEach(id=>document.getElementById(id).oninput=render);
document.getElementById('copy').onclick=e=>{
  const t=document.getElementById('tvlist'); t.select();
  (navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).catch(()=>document.execCommand('copy'));
  e.target.textContent='Copied'; setTimeout(()=>e.target.textContent='Copy',1200);
};
render();

const modalEl = document.getElementById('modal');
modalEl.addEventListener('click', e => { if(e.target===modalEl || e.target.classList.contains('x')) closeAnalysis(); });
document.addEventListener('keydown', e => { if(e.key==='Escape') closeAnalysis(); });
"""


def write_html(path, signals, scanner_names, tfs, meta):
    data = {
        "scanners": [
            {
                "name": n,
                "title": REGISTRY[n].TITLE,
                "description": REGISTRY[n].DESCRIPTION,
                "metric_keys": list(dict.fromkeys(k for s in signals if s.scanner == n for k in s.metrics)),
            }
            for n in scanner_names
        ],
        "signals": [
            {
                "i": i,
                "analysis": s.analysis or None,
                "kronos": getattr(s, "kronos", None) or None,
                "scanner": s.scanner, "symbol": s.symbol, "tf": s.tf, "date": str(s.date),
                "score": s.score, "close": round(s.close, 2), "pivot": round(s.pivot, 2),
                "distance": round(s.distance_pct, 2), "support": round(s.support, 2), "risk": round(s.risk_pct, 2),
                "label": html.escape(s.label), "metrics": s.metrics, "notes": [html.escape(x) for x in s.notes], "chart": s.chart,
            }
            for i, s in enumerate(signals)
        ],
    }
    tabs = "".join(
        f'<button class="tab" data-name="{n}"><div class="t">{html.escape(REGISTRY[n].TITLE)}</div><div class="c"></div></button>'
        for n in scanner_names
    )
    tf_buttons = '<button data-tf="all">Both</button>' if len(tfs) > 1 else ""
    tf_buttons += "".join(f'<button data-tf="{t}">{"Daily" if t == "D" else "Weekly"}</button>' for t in tfs)
    last_bar = max((s.date for s in signals), default=None)
    asof = f" &middot; as of {html.escape(meta['asof'])}" if meta.get("asof") else ""

    script = SCRIPT.replace("__DATA__", json.dumps(data).replace("</", "<\\/")).replace("__META__", json.dumps(meta, default=str))
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pattern Scanners</title><style>{STYLE}</style></head>
<body><div class="wrap">
<h1>Pre-breakout pattern scan</h1>
<p class="sub">{html.escape(meta['source'])} &middot; {meta['scanned']:,} symbols &middot; {html.escape(meta['universe'])} &middot; last bar {last_bar or '-'}{asof} &middot; generated {html.escape(meta['generated'])}</p>
<div class="tabs">{tabs}</div>
<p class="desc" id="desc"></p>
<div class="bar">
  <div class="seg">{tf_buttons}</div>
  <label>Min score <input type="number" id="minscore" value="0" min="0" max="100" step="5"></label>
  <label>Max to pivot % <input type="number" id="maxdist" step="0.5" placeholder="any"></label>
  <input class="q" id="q" placeholder="Filter symbol">
  <div class="tv"><label>TradingView</label><input id="tvlist" readonly><button id="copy" type="button">Copy</button></div>
</div>
<div class="scroll" id="table"></div>
<div class="modal" id="modal"><div class="sheet"><header><div><h2></h2><div class="sub2"></div></div><button class="x" type="button" aria-label="Close">&times;</button></header><div class="body"></div></div></div>
<footer>
<b>Pivot</b> = breakout level (base high / box top / handle high). <b>To pivot %</b> = how far price must rise to trigger. <b>Stop</b> = pattern invalidation
(recent swing low / box bottom / handle low); <b>Risk %</b> = distance from close to stop. <b>D+W</b> = same setup on both timeframes;
<b>+N</b> = also flagged by N other scanners. Sparkline: shaded part is the pattern, green dashed = pivot, red dashed = stop.
<b>Analysis</b> reads volume + NSE delivery + price across the whole pattern &mdash; click any cell for the full breakdown. Delivery data: {html.escape(str(meta.get('delivery', 'off')))}.<br>Stocks below Rs {meta['min_turnover']:g} Cr average daily turnover are excluded. Not investment advice &mdash; confirm on the chart.
</footer>
</div><script>{script}</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
