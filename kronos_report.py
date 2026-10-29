"""Reports and historical labels owned solely by the Kronos feature."""
from __future__ import annotations
import csv
from datetime import datetime
import html
import json
from pathlib import Path
import numpy as np
import pandas as pd
from kronos_breakout import _candles, classify_path


def validate_forecasts(signals, bars_dict, rules):
    """Later bars label outcomes only after model inference. Require full horizon."""
    from datalayer import resample
    labelled, pending = [], 0
    for sig in signals:
        k = sig.kronos
        if k.get("status") != "ok":
            continue
        daily = bars_dict[sig.symbol]
        future = daily.loc[daily.index.date > pd.Timestamp(sig.date).date()]
        if sig.tf == "W":
            end_week = pd.Timestamp(sig.date) + pd.Timedelta(days=(4-pd.Timestamp(sig.date).weekday()) % 7)
            future = resample(future.loc[future.index.date > end_week.date()], "W")
            if len(future):
                completed = [t.normalize() + pd.Timedelta(days=(4-t.weekday()) % 7) <= daily.index[-1].normalize() for t in future.index]
                future = future.loc[completed]
        if len(future) < k["horizon"]:
            k["validation"] = {"status": "pending", "available_bars": len(future), "required_bars": k["horizon"]}
            pending += 1
            continue
        try:
            actual, _ = _candles(future.iloc[:k["horizon"]])
            outcome, entry, target_bar = classify_path(sig, actual, k.get("evaluation_target", k["target"]), rules)
            label, p = int(outcome == "success"), k["success_rate"] / 100
            k["validation"] = {"status": "complete", "outcome": outcome,
                                "breakout_bar": entry, "target_bar": target_bar,
                                "brier_score": round((p-label)**2, 5)}
            labelled.append((p, label))
        except ValueError as exc:
            k["validation"] = {"status": "error", "error": str(exc)}
    return {"evaluated": len(labelled), "pending": pending,
            "observed_success_rate": round(100*float(np.mean([y for _,y in labelled])), 2) if labelled else None,
            "brier_score": round(float(np.mean([(p-y)**2 for p,y in labelled])), 5) if labelled else None,
            "note": "Descriptive historical checks only. Overlapping setups are correlated; pretrained model training may overlap the evaluation period. No calibrated accuracy claim."}


def write_reports(signals, metadata, csv_path=None, json_path=None, html_path=None):
    ordered = sorted(signals, key=lambda s: s.kronos.get("success_rate", -1), reverse=True)
    records = [{"symbol": s.symbol, "scanner": s.scanner, "timeframe": s.tf,
                "date": str(s.date), "close": s.close, "pivot": s.pivot,
                "support": s.support, "pattern_score": s.score, "kronos": s.kronos} for s in ordered]
    payload = {"generated": datetime.now().astimezone().isoformat(), **metadata, "signals": records}
    if json_path:
        Path(json_path).write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    if csv_path:
        fields = ["symbol", "scanner", "timeframe", "date", "close", "pivot", "support", "pattern_score",
                  "status", "verdict", "success_rate", "sample_count", "success_interval",
                  "breakout_prob", "fakeout_prob", "breakdown_prob", "target", "trigger", "rr",
                  "bars_to_breakout", "bars_to_target", "horizon", "seed", "model", "repaired_candles", "error", "validation"]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in records:
                flat = {**r, **r["kronos"]}
                writer.writerow({key: json.dumps(flat[key]) if isinstance(flat.get(key), (list, dict)) else flat.get(key, "") for key in fields})
    if not html_path:
        return
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    for r in records:
        k = r["kronos"]
        body = f'<p>{esc(k.get("headline", ""))}</p>'
        if k.get("status") == "ok":
            for section in k["sections"]:
                body += '<h3>' + esc(section['title']) + '</h3><ul>' + ''.join('<li>' + esc(b['text']) + '</li>' for b in section['bullets']) + '</ul>'
            body += '<h3>Forecast closes · model scenarios</h3><div class="scroll"><table><tr><th>Date*</th><th>10th percentile</th><th>Median</th><th>90th percentile</th></tr>'
            for d, low, mid, high in zip(k['forecast_dates'], k['pred_q10'], k['pred_closes'], k['pred_q90']):
                body += f'<tr><td>{esc(d)}</td><td>{low:.2f}</td><td>{mid:.2f}</td><td>{high:.2f}</td></tr>'
            body += '</table></div><p class="muted">*Approximate dates; exchange holidays are not modelled.</p>'
        if k.get('validation'):
            body += '<h3>Observed outcome</h3><pre>' + esc(json.dumps(k['validation'], indent=2)) + '</pre>'
        cards.append(f'<details data-symbol="{esc(r["symbol"].lower())}" data-tone="{esc(k.get("tone", "flat"))}"><summary><span><b>{esc(r["symbol"])}</b><small>{esc(r["scanner"])} · {esc(r["timeframe"])} · as of {esc(r["date"])}</small></span><span class="{esc(k.get("tone", "flat"))}">{esc(k.get("verdict", "Unavailable"))}<small>{esc(" · ".join(k.get("chips", [])))}</small></span></summary><div class="body"><p>Close {r["close"]:.2f} · Pattern pivot {r["pivot"]:.2f} · Invalidation {r["support"]:.2f}</p>{body}</div></details>')
    validation = '<h2>Historical outcome check</h2><pre>' + esc(json.dumps(metadata['validation'], indent=2)) + '</pre>' if metadata.get('validation') else ''
    document = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kronos · Breakout success</title>
<style>body{margin:0;background:#101720;color:#e7edf5;font:16px/1.6 system-ui}main{max-width:1060px;margin:45px auto;padding:0 22px}h1{font-size:34px;line-height:1.2}.muted,small{color:#aab8ca}small{display:block;font-size:13px}input,select{background:#182331;border:1px solid #435369;color:#fff;padding:12px;border-radius:8px;margin:8px 12px 24px 0}details{background:#182331;border:1px solid #344357;border-radius:10px;margin:12px 0}summary{cursor:pointer;padding:20px;display:flex;justify-content:space-between;gap:20px}.body{padding:0 22px 24px;border-top:1px solid #344357}.good{color:#67ddb0}.bad{color:#ffac9f}h3{font-size:17px}li{margin:8px 0}table{border-collapse:collapse;width:100%}td,th{text-align:right;padding:8px;border-bottom:1px solid #344357}td:first-child,th:first-child{text-align:left}pre{white-space:pre-wrap}.scroll{overflow:auto}@media(max-width:650px){summary{display:block}summary>span{display:block;margin-bottom:8px}}</style>
<main><p class="muted">PATTERN SCANNER / KRONOS</p><h1>Which patterns follow through?</h1><p>Ranked by the share of sampled paths that confirm above the pivot and reach the target before invalidation.</p><p class="muted">Scenario frequencies are uncalibrated model estimates. Open a setup for the success rule, failure outcomes and forecast range.</p>'''
    document += '<p class="muted">' + esc(f"Model {metadata.get('model')} · {len(records)} setups · generated {payload['generated']}") + '</p>'
    document += '<input id="search" aria-label="Find symbol" placeholder="Find symbol"><select id="filter" aria-label="Filter outcomes"><option value="all">All setups</option><option value="good">Favourable</option><option value="bad">Risk flagged</option><option value="flat">Unconfirmed / unavailable</option></select>'
    document += ''.join(cards) + validation + '''<p class="muted">The original scanner's scores and pattern rules are unchanged. Success percentages refer to the configured horizon, target and invalidation rules.</p></main><script>
function filter(){const q=document.querySelector('#search').value.toLowerCase(),t=document.querySelector('#filter').value;document.querySelectorAll('details').forEach(d=>d.hidden=!d.dataset.symbol.includes(q)||(t!=='all'&&d.dataset.tone!==t))}document.querySelector('#search').oninput=filter;document.querySelector('#filter').onchange=filter;
</script></html>'''
    Path(html_path).write_text(document, encoding="utf-8")
