"""Forecasts, uncertainty bands and transparent pattern-based research levels."""
from contextlib import nullcontext
import hashlib
import math
import numpy as np
import pandas as pd
from .core import _candles, calculate_target_price, evaluate_breakout, generate_future_timestamps
from .data import candles_json, signal_dict
from .model import get_predictor


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled('Run cancelled')


def trade_plan(history, signal, settings):
    last = float(history.close.iloc[-1])
    if signal:
        pivot, stop = float(signal.pivot), float(signal.support)
        basis = signal.scanner
    else:
        recent = history.tail(20)
        pivot, stop = float(recent.high.max()), float(recent.low.min())
        basis = '20-bar range reference; no detected pattern'
    entry = pivot * (1+settings.buffer_pct/100)
    target = calculate_target_price(pivot, stop, rules=settings.rules())
    risk = entry-stop
    budget = settings.capital*settings.risk_pct/100
    cost = (settings.fee_bps+settings.slippage_bps)/10000
    units = max(0, min(math.floor(budget/risk), math.floor(settings.capital/(entry*(1+cost))))) if risk > 0 else 0
    return {'basis': basis, 'pattern_detected': signal is not None, 'pivot': pivot,
            'entry': entry, 'stop': stop, 'target': target,
            'reward_risk': (target-entry)/risk if risk>0 else None,
            'risk_per_unit': risk, 'risk_budget': budget, 'units': units,
            'notional': units*entry, 'distance_pct': (entry/last-1)*100,
            'status': 'Wait for close confirmation' if last<entry else 'Above reference trigger',
            'note': 'Planning levels, not an order. Size is capped by cash and the risk budget; gap losses can exceed this budget.'}


def forecast(symbol, bars, settings, signal=None, predictor=None, future_dates=None,
             progress=None, cancel=None):
    check_cancel(cancel)
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
        raise ValueError('Bars must have unique, increasing timestamps.')
    context, _ = _candles(bars.tail(settings.lookback))
    if len(context)<60:
        raise ValueError('At least 60 observed candles are needed.')
    if 'volume' not in context:
        context['volume'] = 0.
    for name in ('volume','amount'):
        if name in context and (not np.isfinite(context[name]).all() or (context[name]<0).any()):
            raise ValueError(f'Invalid {name} values in market data.')
    dates = pd.DatetimeIndex(future_dates) if future_dates is not None else generate_future_timestamps(context.index[-1], settings.horizon, settings.timeframe)
    if len(dates)!=settings.horizon or dates.has_duplicates or not dates.is_monotonic_increasing or dates[0]<=context.index[-1]:
        raise ValueError('Future dates must follow history and match the forecast horizon.')
    features = context[[c for c in ('open','high','low','close','volume','amount') if c in context]].copy()
    fingerprint = hashlib.sha256(pd.util.hash_pandas_object(features, index=True).values.tobytes()).hexdigest()
    identity = f'{symbol}|{settings.timeframe}|{context.index[-1]}|{settings.seed}|{fingerprint}'
    actual_seed = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], 'little')
    predictor = predictor or get_predictor(settings.model, progress)
    if len(context)>getattr(predictor,'max_context',512):
        raise ValueError('Lookback exceeds the selected model capacity.')
    try:
        import torch
    except ImportError:
        torch = None
    rng = torch.random.fork_rng(devices=list(range(torch.cuda.device_count()))) if torch else nullcontext()
    paths, repair_count = [], 0
    with rng:
        if torch:
            torch.manual_seed(actual_seed)
        while len(paths)<settings.samples:
            check_cancel(cancel)
            count = min(settings.batch_size, settings.samples-len(paths))
            kwargs = dict(df=features.copy(), x_timestamp=pd.Series(context.index),
                          y_timestamp=pd.Series(dates), pred_len=settings.horizon,
                          T=settings.temperature, top_p=settings.top_p, verbose=False)
            if hasattr(predictor,'predict_samples'):
                _, chunk = predictor.predict_samples(**kwargs, sample_count=count)
            else:
                chunk = [predictor.predict(**kwargs, sample_count=1) for _ in range(count)]
            if len(chunk)!=count:
                raise ValueError('Model returned the wrong number of paths.')
            for path in chunk:
                if len(path)!=len(dates) or not path.index.equals(dates):
                    raise ValueError('Model returned misaligned forecast candles.')
                path, repaired = _candles(path, repair=True)
                paths.append(path)
                repair_count += repaired
            if progress:
                progress(len(paths)/settings.samples, f'{len(paths)}/{settings.samples} forecast paths')
    check_cancel(cancel)
    closes = np.array([p.close.to_numpy(float) for p in paths])
    q10, med, q90 = np.quantile(closes,[.1,.5,.9],axis=0)
    last = float(context.close.iloc[-1])
    up_rate = float(np.mean(closes[:,-1]>last)*100)
    previous = context.close.shift(1)
    atr = pd.concat([context.high-context.low,(context.high-previous).abs(),(context.low-previous).abs()],axis=1).max(axis=1).tail(14).mean()
    bias = 'Upside bias' if up_rate>=60 else 'Downside bias' if up_rate<=40 else 'Mixed outlook'
    result = {'symbol': symbol, 'timeframe': settings.timeframe,
        'asof': str(context.index[-1].date()), 'last_close': last,
        'history': candles_json(context), 'settings': settings.to_dict(),
        'forecast': [{'date':str(d.date()),'median':float(m),'q10':float(l),'q90':float(h)} for d,m,l,h in zip(dates,med,q10,q90)],
        'summary': {'bias':bias, 'median_end':float(med[-1]),
                    'median_return_pct':float((med[-1]/last-1)*100),
                    'end_q10':float(q10[-1]), 'end_q90':float(q90[-1]),
                    'upside_rate':up_rate, 'atr':float(atr), 'sample_count':len(paths)},
        'pattern':signal_dict(signal), 'plan':trade_plan(context,signal,settings),
        'breakout':evaluate_breakout(signal, paths[0], paths, rules=settings.rules()) if signal else None,
        'metadata':{**getattr(predictor,'research_metadata',{}), 'data_hash':fingerprint,
                    'seed':actual_seed, 'lookback_used':len(context), 'repaired_candles':repair_count,
                    'calendar':'Observed trading dates' if future_dates is not None else 'Estimated weekdays / weekly Fridays',
                    'partial_week':settings.timeframe=='W' and context.index[-1].weekday()!=4},
        'notes':['AI paths are sampled scenarios, not calibrated market probabilities.',
                 'The band shows the 10th–90th percentiles of predicted closes; it is not a guaranteed range.',
                 'Pretrained model data may overlap historical test dates. These tests do not establish live trading accuracy.']}
    if repair_count:
        result['notes'].append(f'{repair_count} decoded candles needed high/low bounds expanded to contain open and close.')
    if settings.samples<20:
        result['notes'].append('Fewer than 20 paths: treat the sample rate as a rough estimate. AI-filtered paper trades are disabled.')
    return result
