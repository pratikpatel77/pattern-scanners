"""Read-only cache audit and optional real checkpoint smoke test. No orders/journal edits.

python tools/audit_calculations.py --model --live
"""
import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
from analysis import analyse
from datalayer import resample
from kronos.data import MarketData, ROOT
from kronos.engine import forecast
from kronos.settings import Settings
from scanners import ALL_SCANNERS


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',action='store_true')
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    market=MarketData()
    report=dict(symbols=0,scans=0,signals=0,dates={},errors=[],model=None,live=None)
    dates=Counter()
    for i,symbol in enumerate(market.symbols()):
        try:
            daily=market.daily(symbol)
            dates[str(daily.index[-1].date())]+=1
            report['symbols']+=1
            for tf in ('D','W'):
                bars=resample(daily,tf)
                for module in ALL_SCANNERS.values():
                    sig=module.scan(symbol,bars,tf,None)
                    report['scans']+=1
                    if sig is None:continue
                    assert np.isfinite([sig.close,sig.pivot,sig.support,sig.score]).all()
                    assert 0<=sig.score<=100 and sig.distance_pct>=-1e-8 and sig.risk_pct>=0
                    read=analyse(sig,bars)
                    json.dumps(read,allow_nan=False)
                    report['signals']+=1
        except (ValueError,AssertionError,KeyError) as exc:
            report['errors'].append(dict(symbol=symbol,error=f'{type(exc).__name__}: {exc}'))
        if (i+1)%200==0:print(f'Checked {i+1} cached symbols',flush=True)
    report['dates']=dict(sorted(dates.items()))
    bars=market.bars('SBIN','D')
    if args.live:
        try:
            from kronos.definedge_market import DefinedgeMarket
            provider=DefinedgeMarket()
            bars=provider.eod_history('SBIN')
            q=provider.quotes(['SBIN'])['quotes'].get('SBIN',{})
            report['live']=dict(symbol='SBIN',source=q.get('source'),price=q.get('ltp'),
                               candle_date=str(bars.index[-1].date()),quote_time=q.get('quote_time'),fresh=q.get('fresh'))
        except Exception as exc:
            report['live']=dict(error=f'{type(exc).__name__}: {exc}')
    if args.model:
        print('Checking the real Kronos-small checkpoint on SBIN',flush=True)
        try:
            result=forecast('SBIN',bars,replace(Settings(),horizon=5),include_paths=True,
                            progress=lambda _,msg:print(msg,flush=True))
            report['model']=dict(asof=result['asof'],summary=result['summary'],metadata=result['metadata'],
                                 valid_bands=all(r['q10']<=r['median']<=r['q90'] for r in result['forecast']),
                                 paths=len(result['paths']),horizon=len(result['forecast']))
        except Exception as exc:
            report['model']=dict(error=f'{type(exc).__name__}: {exc}')
    out=ROOT/'outputs'/'calculation-audit-2026-11-05.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(report,allow_nan=False),flush=True)


if __name__=='__main__':main()
