"""Price forecast metrics and conservative, next-open paper trade execution."""
import math
import numpy as np
from .core import classify_path


def compare(result, actual):
    count = min(len(actual),len(result['forecast']))
    if not count:
        return {'available':0,'required':len(result['forecast']),'complete':False,'rows':[],'metrics':{}}
    actual = actual.iloc[:count]
    expected = np.array([r['median'] for r in result['forecast'][:count]])
    observed = actual.close.to_numpy(float)
    low = np.array([r['q10'] for r in result['forecast'][:count]])
    high = np.array([r['q90'] for r in result['forecast'][:count]])
    last = result['last_close']
    errors = expected-observed
    baseline = float(np.mean(np.abs(observed-last)))
    mae = float(np.mean(np.abs(errors)))
    metrics = {'mae':mae, 'rmse':float(np.sqrt(np.mean(errors**2))),
        'smape_pct':float(np.mean(2*np.abs(errors)/(np.abs(expected)+np.abs(observed)))*100),
        'baseline_mae':baseline, 'mae_skill_pct':100*(1-mae/baseline) if baseline>1e-12 else None,
        'coverage_pct':float(np.mean((observed>=low)&(observed<=high))*100),
        'direction_correct':bool(np.sign(expected[-1]-last)==np.sign(observed[-1]-last)),
        'actual_return_pct':float((observed[-1]/last-1)*100)}
    rows = [{'date':str(t.date()),'forecast_date':result['forecast'][i]['date'],
             'actual':float(observed[i]),'median':float(expected[i]), 'q10':float(low[i]),
             'q90':float(high[i]),'absolute_error':float(abs(errors[i]))} for i,t in enumerate(actual.index)]
    return {'available':count,'required':len(result['forecast']),
            'complete':count==len(result['forecast']),'rows':rows,'metrics':metrics,
            'alignment':'Trading bar order; forecast calendar dates can differ on holidays.'}


def simulate_trade(signal, actual, settings, capital=None):
    """Long-only research rule. Confirm at close, enter next open, stop wins ties.

    Gap fills use actual open. Broken-pivot close exits at next open. Both sides
    pay fees and adverse slippage. No leverage, no intrabar ordering assumption.
    """
    cash = settings.capital if capital is None else capital
    base = {'status':'no_trade','pnl':0.,'return_pct':0.,'units':0,'entry_date':None,
            'exit_date':None,'entry':None,'exit':None,'net_r':None,'reason':'No confirmed entry'}
    if signal is None:
        return {**base,'reason':'No pattern at this historical origin'}
    from .core import calculate_target_price
    pivot, stop = signal.pivot, signal.support
    trigger = pivot*(1+settings.buffer_pct/100)
    target = calculate_target_price(pivot,stop,rules=settings.rules())
    streak, pending, entry, units, exit_next = 0, False, None, 0, False
    fee, slip = settings.fee_bps/10000, settings.slippage_bps/10000
    curve = []
    for i,(timestamp,row) in enumerate(actual.iterrows()):
        day = str(timestamp.date())
        if entry is None and pending:
            raw_entry = float(row.open)
            entry = raw_entry*(1+slip)
            if raw_entry<=stop or entry>=target:
                return {**base,'reason':'Entry gap is outside stop/target levels'}
            units = min(math.floor(cash*settings.risk_pct/100/(entry-stop)), math.floor(cash/(entry*(1+fee))))
            if units<1:
                return {**base,'reason':'Risk budget is too small for one unit'}
            entry_date = day
            debit = units*entry*(1+fee)
        if entry is not None:
            reason, exit_price = None, None
            if row.open<=stop:
                reason,exit_price = 'stop',float(row.open)
            elif exit_next:
                reason,exit_price = 'failed_breakout',float(row.open)
            elif row.open>=target:
                reason,exit_price = 'target',float(row.open)
            elif row.low<=stop:
                reason,exit_price = 'stop',stop
            elif row.high>=target:
                reason,exit_price = 'target',target
            elif i==len(actual)-1:
                reason,exit_price = 'time_exit',float(row.close)
            elif row.close<pivot:
                exit_next=True
            if reason:
                fill = exit_price*(1-slip)
                credit = units*fill*(1-fee)
                pnl = credit-debit
                curve.append({'date':day,'equity':cash+pnl})
                return {**base,'status':reason,'reason':reason.replace('_',' '),
                    'pnl':pnl,'return_pct':pnl/cash*100,'units':units,
                    'entry_date':entry_date,'exit_date':day,'entry':entry,'exit':fill,
                    'net_r':pnl/(units*(entry-stop)), 'curve':curve,
                    'fees':fee*units*(entry+fill)}
            curve.append({'date':day,'equity':cash-debit+units*float(row.close)})
        else:
            if row.low<=stop:
                return {**base,'reason':'Pattern invalidated before entry'}
            streak = streak+1 if row.close>=trigger else 0
            pending = streak>=settings.confirmation_bars
    return base


def summarize(windows, initial_capital):
    if not windows:
        return {'windows':0,'trades':0}
    comparisons=[w['comparison'] for w in windows]
    rows=[r for c in comparisons for r in c['rows']]
    absolute=[r['absolute_error'] for r in rows]
    baseline=[abs(r['actual']-w['last_close']) for w in windows for r in w['comparison']['rows']]
    mae,naive=float(np.mean(absolute)),float(np.mean(baseline))
    trades=[w['trade'] for w in windows if w['trade']['status']!='no_trade']
    curve=[{'date':windows[0]['asof'],'equity':initial_capital}]
    cash=initial_capital
    for w in windows:
        trade=w['trade']
        if trade.get('curve'):
            curve.extend(trade['curve'])
        cash+=trade['pnl']
        curve.append({'date':w['comparison']['rows'][-1]['date'],'equity':cash})
    eq=np.array([r['equity'] for r in curve])
    peak=np.maximum.accumulate(eq)
    brier=[w['brier'] for w in windows if w.get('brier') is not None]
    return {'windows':len(windows),'observations':len(rows),'mae':mae,
        'rmse':float(np.sqrt(np.mean([(r['median']-r['actual'])**2 for r in rows]))),
        'smape_pct':float(np.mean([2*abs(r['median']-r['actual'])/(abs(r['median'])+abs(r['actual'])) for r in rows])*100),
        'baseline_mae':naive,'mae_skill_pct':100*(1-mae/naive) if naive>1e-12 else None,
        'direction_accuracy_pct':float(np.mean([c['metrics']['direction_correct'] for c in comparisons])*100),
        'coverage_pct':float(np.mean([r['q10']<=r['actual']<=r['q90'] for r in rows])*100),
        'brier_score':float(np.mean(brier)) if brier else None,'pattern_windows':len(brier),
        'trades':len(trades),'win_rate_pct':sum(t['pnl']>0 for t in trades)/len(trades)*100 if trades else None,
        'net_return_pct':(cash/initial_capital-1)*100,
        'max_drawdown_pct':float(np.max((peak-eq)/peak)*100),'ending_equity':cash,'equity':curve}
