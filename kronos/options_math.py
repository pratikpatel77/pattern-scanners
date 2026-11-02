"""Bounded option structures, executable-side fills and net-only analytics."""
from datetime import datetime
import itertools
import math

import numpy as np


def charges(buy, sell, orders=0, per_order=0.):
    """NSE equity options charges, effective 2026-04-01. Brokerage is a flat amount per executed
    order (one order per leg); GST applies to brokerage, exchange and SEBI fees, as on a contract note."""
    turnover = buy + sell
    exchange, sebi, brokerage = turnover * .0003553, turnover * .000001, orders * per_order
    return dict(brokerage=brokerage, stt=sell*.0015, exchange=exchange, sebi=sebi,
                gst=(brokerage+exchange+sebi)*.18, stamp=buy*.00003, dp=0.)


def fee(buy, sell, orders=0, per_order=0.):
    return sum(charges(buy, sell, orders, per_order).values())


def book_fill(quote, side, units):
    """Average price for `units`, as a market order would get it now: buys take asks from the best
    upward, sells take bids from the best downward. Raises when the visible book is too thin."""
    levels = quote.get('ask' if side > 0 else 'bids')
    levels = [(float(l.get('price') or 0), float(l.get('volume') or l.get('qty') or 0)) for l in levels] if isinstance(levels, list) else []
    levels = [(p, v) for p, v in levels if p > 0 and v > 0]
    if not levels:   # no depth ladder supplied: the best quote is all we know
        best = quote.get('ask_qty' if side > 0 else 'bid_qty')
        if best is not None and best < units:
            raise ValueError(f"Only {int(best)} of {int(units)} units at the best price, and no deeper book.")
        return fill(quote, side), [dict(price=fill(quote, side), units=units)]
    left, cost, used = units, 0., []
    for price, volume in levels:
        take = min(left, volume)
        cost += take * price; left -= take; used.append(dict(price=price, units=take))
        if left <= 0: break
    if left > 0:
        raise ValueError(f"Only {int(units-left)} of {int(units)} units available in the top {len(levels)} price levels.")
    return round(cost / units, 4), used


def fill(option, side, slip=None):
    """Permanent paper-fill rule: a buy fills at the best ask, a sell/short at the best bid —
    the worst immediate price, with no extra slippage. Used for every entry, mark and exit.
    `slip` is accepted for old callers and ignored."""
    value = option['ask_price'] if side > 0 else option['bid']
    tick = option.get('tick') or .05
    steps = math.ceil(value/tick-1e-9) if side > 0 else math.floor(value/tick+1e-9)  # stay on the exchange tick
    return round(steps*tick, 2)


def intrinsic(kind, strike, spot):
    return max(0., spot-strike) if kind == 'CE' else max(0., strike-spot)


def bs(spot, strike, days, iv, kind):
    if days <= 0:
        return intrinsic(kind, strike, spot)
    t, sigma, rate = days/365., max(.01, iv/100), .06
    d1 = (math.log(max(.00001,spot)/strike)+(rate+sigma*sigma/2)*t)/(sigma*math.sqrt(t))
    d2 = d1-sigma*math.sqrt(t)
    cdf = lambda x: (1+math.erf(x/math.sqrt(2)))/2
    call = spot*cdf(d1)-strike*math.exp(-rate*t)*cdf(d2)
    return max(0., call if kind == 'CE' else call-spot+strike*math.exp(-rate*t))


def entry(legs, lot, slip):
    buy = sell = 0.
    for leg in legs:
        cash = fill(leg, leg['qty'], slip)*abs(leg['qty'])*lot
        if leg['qty'] > 0:
            buy += cash
        else:
            sell += cash
    return sell-buy, charges(buy,sell)


def expiry_value(legs, spot, lot):
    return sum(l['qty']*intrinsic(l['type'],l['strike'],spot)*lot for l in legs)


def candidates(options, spot, lower, upper):
    """Explore multiple widths. Every short has a same-expiry risk hedge."""
    puts = sorted([o for o in options if o['type']=='PE'], key=lambda o:o['strike'])
    calls = sorted([o for o in options if o['type']=='CE'], key=lambda o:o['strike'])
    def leg(o, qty): return {**o, 'qty':qty}
    groups = []
    for kind, chain in [('PE',puts),('CE',calls)]:
        shorts = sorted(chain, key=lambda o:abs(o['strike']-(lower if kind=='PE' else upper)))[:3]
        for short in shorts:
            if (kind=='PE' and short['strike']>lower) or (kind=='CE' and short['strike']<upper):
                continue
            hedges = [o for o in chain if (o['strike']<short['strike'] if kind=='PE' else o['strike']>short['strike'])]
            for hedge in sorted(hedges,key=lambda o:abs(o['strike']-short['strike']))[:3]:
                groups.append(('Put credit spread' if kind=='PE' else 'Call credit spread',
                               'bullish' if kind=='PE' else 'bearish', [leg(short,-1),leg(hedge,1)]))
        atm = sorted(chain,key=lambda o:abs(o['strike']-spot))[:2]
        for long in atm:
            otm = [o for o in chain if (o['strike']>long['strike'] if kind=='CE' else o['strike']<long['strike'])]
            for short in sorted(otm,key=lambda o:abs(o['strike']-long['strike']))[:3]:
                groups.append(('Call debit spread' if kind=='CE' else 'Put debit spread',
                               'bullish' if kind=='CE' else 'bearish',[leg(long,1),leg(short,-1)]))
        # Butterflies, unequal wings and 1x2 backspreads: only capped loss survives scoring.
        near = sorted(sorted(chain,key=lambda o:abs(o['strike']-spot))[:9],key=lambda o:o['strike'])
        for a,b,c in itertools.combinations(near,3):
            w1,w2 = b['strike']-a['strike'],c['strike']-b['strike']
            if max(w1,w2)>3*min(w1,w2): continue
            groups.append(('Butterfly' if abs(w1-w2)<.01 else 'Broken-wing butterfly','range',
                           [leg(a,1),leg(b,-2),leg(c,1)]))
        for short in atm[:1]:
            longs = [o for o in chain if (o['strike']>short['strike'] if kind=='CE' else o['strike']<short['strike'])]
            for long in sorted(longs,key=lambda o:abs(o['strike']-short['strike']))[:2]:
                groups.append(('Call backspread' if kind=='CE' else 'Put backspread',
                               'bullish' if kind=='CE' else 'bearish',[leg(short,-1),leg(long,2)]))
    pc = [g for g in groups if g[0]=='Put credit spread'][:6]
    cc = [g for g in groups if g[0]=='Call credit spread'][:6]
    for p,c in itertools.product(pc,cc):
        if p[2][0]['strike'] < c[2][0]['strike']:
            groups.append(('Iron condor','range',p[2]+c[2]))
    for p in sorted(puts,key=lambda o:abs(o['strike']-spot))[:1]:
        c = next((o for o in calls if o['strike']==p['strike']),None)
        if not c: continue
        pl = [o for o in puts if o['strike']<p['strike']][-2:]
        cl = [o for o in calls if o['strike']>c['strike']][:2]
        for a,b in itertools.product(pl,cl):
            groups.append(('Iron butterfly','range',[leg(a,1),leg(p,-1),leg(c,-1),leg(b,1)]))
    return groups


def underlying_stop(bias, legs, forecast):
    """Early invalidation reference, independent of the expiry maximum loss."""
    spot=forecast['last_close']
    history=forecast.get('history',[])
    atr=forecast.get('summary',{}).get('atr') or 0
    if atr<=0 and history:
        ranges=[max(r['high']-r['low'],abs(r['high']-history[i-1]['close']),abs(r['low']-history[i-1]['close']))
                for i,r in enumerate(history) if i>0]
        atr=float(np.mean(ranges[-14:])) if ranges else 0
    fallback=atr<=0
    if fallback: atr=spot*.01
    recent=history[-5:]
    low=min((r['low'] for r in recent),default=spot-atr)
    high=max((r['high'] for r in recent),default=spot+atr)
    lower=max(spot-atr,min(spot-atr*.25,low-atr*.1))
    upper=min(spot+atr,max(spot+atr*.25,high+atr*.1))
    if bias=='range':
        puts=[l['strike'] for l in legs if l['qty']<0 and l['type']=='PE' and l['strike']<spot]
        calls=[l['strike'] for l in legs if l['qty']<0 and l['type']=='CE' and l['strike']>spot]
        if puts: lower=max(lower,max(puts))
        if calls: upper=min(upper,min(calls))
    return dict(lower=round(max(.01,lower),2) if bias!='bearish' else None,
                upper=round(upper,2) if bias!='bullish' else None,
                basis='Recent five-bar support/resistance with 0.1 ATR buffer, capped at one ATR from forecast spot; range trades also exit at a nearer short strike.' if not fallback else 'Fallback: 1% from forecast spot; ATR unavailable.',
                rule='Exit on the first fresh underlying quote at or beyond a stop. Do not wait for expiry.',
                automatic=False)


REWARD_REASONS = ('Stressed net profit below minimum', 'Stressed net return on maximum loss below minimum')


def reward_reasons(plan, prefs):
    """Apply configurable net reward floors to new and saved analyses."""
    profit = plan.get('stressed_mean_net')
    loss = plan.get('max_loss')
    if profit is None or loss is None or not math.isfinite(profit) or not math.isfinite(loss) or loss <= 0:
        return list(REWARD_REASONS)
    reasons = []
    if profit < prefs.get('min_net_profit', 100.): reasons.append(REWARD_REASONS[0])
    if profit / loss * 100 < prefs.get('min_return_on_risk_pct', 10.): reasons.append(REWARD_REASONS[1])
    return reasons


def analyse_structure(name, bias, legs, forecast, prefs, ivp):
    lot = legs[0]['lot']
    if any(l['lot'] != lot or l['expiry'] != legs[0]['expiry'] for l in legs):
        raise ValueError('Mixed lots or expiries are not supported.')
    tail = sum(l['qty'] for l in legs if l['type']=='CE')
    if tail < 0:
        raise ValueError('Unbounded loss is not allowed.')
    cash, costs = entry(legs,lot,prefs['slippage'])
    # Exit reserve is a disclosed estimate; actual paper exits use fresh bid/ask and actual turnover.
    turnover = sum(fill(l,l['qty'],prefs['slippage'])*abs(l['qty'])*lot for l in legs)
    reserve = fee(turnover,turnover) + turnover*prefs['slippage']
    total_cost = sum(costs.values())+reserve
    net = lambda s: cash + expiry_value(legs,s,lot)-total_cost
    knots = sorted({0.,*[l['strike'] for l in legs]})
    vals = [net(s) for s in knots]
    max_loss = max(0.,-min(vals))
    max_profit = None if tail>0 else max(vals)
    if max_loss <= 0 or (max_profit is not None and max_profit<=0):
        raise ValueError('No useful bounded payoff after costs.')
    breakevens=[]
    extended = knots+[knots[-1]+max(100.,knots[-1])]
    for a,b in zip(extended,extended[1:]):
        x,y=net(a),net(b)
        if x*y<0: breakevens.append(a+(b-a)*(-x)/(y-x))
        elif abs(x)<1e-7: breakevens.append(a)
    end = forecast['forecast'][-1]['date']
    remaining = (datetime.fromisoformat(legs[0]['expiry'])-datetime.fromisoformat(end)).days
    stress=[]
    for iv_mult in (.75,1.,1.25):
        pnl=[]
        for path in forecast['paths']:
            price=path[-1]['close']
            buy=sell=0.
            for l in legs:
                value=bs(price,l['strike'],remaining,l['iv']*iv_mult,l['type'])
                # Modelled future spread plus slippage; never presented as executable prices.
                half=(l['ask_price']-l['bid'])/2
                close=max(0.,value-half)*(1-prefs['slippage']) if l['qty']>0 else (value+half)*(1+prefs['slippage'])
                amount=close*abs(l['qty'])*lot
                if l['qty']>0: sell+=amount
                else: buy+=amount
            pnl.append(cash+sell-buy-sum(costs.values())-fee(buy,sell))
        stress.append(pnl)
    baseline=np.array(stress[1]); worst_mean=min(float(np.mean(x)) for x in stress)
    floors=np.array([min(r['low'] for r in p) for p in forecast['paths']])
    ceilings=np.array([max(r['high'] for r in p) for p in forecast['paths']])
    shorts=[l for l in legs if l['qty']<0]
    touched=np.array([any((lo<=l['strike'] if l['type']=='PE' else hi>=l['strike']) for l in shorts)
                      for lo,hi in zip(floors,ceilings)])
    good=float(np.mean(baseline>0)*100)
    downside=float(np.quantile(baseline,.1))
    score=worst_mean/max_loss + downside/max_loss*.15
    if cash>0: score+=.04  # small credit preference; risk and pricing still dominate
    reasons=reward_reasons(dict(stressed_mean_net=round(worst_mean,2),max_loss=round(max_loss,2)),prefs)
    if worst_mean<=0: reasons.append('Negative average outcome under IV stress')
    if good<prefs['min_scenario_pct']: reasons.append('Too few profitable model scenarios')
    if cash>0 and cash/lot < max(.5, sum(l['ask_price']-l['bid'] for l in legs)*2): reasons.append('Credit too small for the spreads')
    if ivp is None: reasons.append('IV percentile needs a dated source')
    if ivp is not None:
        if cash>0 and ivp<30: score-=.08
        if cash<0 and ivp>70: score-=.08
    spot=forecast['last_close']
    days=max(1,(datetime.fromisoformat(legs[0]['expiry'])-datetime.fromisoformat(forecast['asof'])).days) if forecast.get('asof') else remaining+7
    theta=delta=vega=0.
    for l in legs:
        value=bs(spot,l['strike'],days,l['iv'],l['type'])
        scale=l['qty']*lot
        theta+=(bs(spot,l['strike'],days-1,l['iv'],l['type'])-value)*scale
        delta+=(bs(spot+.5,l['strike'],days,l['iv'],l['type'])-bs(spot-.5,l['strike'],days,l['iv'],l['type']))*scale
        vega+=(bs(spot,l['strike'],days,l['iv']+1,l['type'])-value)*scale
    chart_lo=max(.01,min(spot*.9,knots[1]*.97))
    chart_hi=max(spot*1.1,knots[-1]*1.03)
    points=sorted({*np.linspace(chart_lo,chart_hi,161),*knots[1:],*breakevens})
    holding=[]
    for price in points:
        buy=sell=0.
        for l in legs:
            value=bs(price,l['strike'],remaining,l['iv'],l['type'])
            half=(l['ask_price']-l['bid'])/2
            close=max(0.,value-half)*(1-prefs['slippage']) if l['qty']>0 else (value+half)*(1+prefs['slippage'])
            amount=close*abs(l['qty'])*lot
            if l['qty']>0: sell+=amount
            else: buy+=amount
        holding.append(dict(price=round(float(price),2),net=round(cash+sell-buy-sum(costs.values())-fee(buy,sell),2)))
    return dict(name=name,bias=bias,legs=legs,lot=lot,entry_credit=round(cash,2),
        max_loss=round(max_loss,2),max_profit=round(max_profit,2) if max_profit is not None else None,
        breakevens=[round(x,2) for x in sorted(set(breakevens))],
        entry_charges=costs,exit_cost_reserve=round(reserve,2),cost_estimate=round(total_cost,2),
        scenario_profit_pct=round(good,1),scenario_mean_net=round(float(np.mean(baseline)),2),
        stressed_mean_net=round(worst_mean,2),scenario_tail_net=round(downside,2),
        short_touch_pct=round(float(np.mean(touched)*100),1),score=round(score,5),
        payoff=[dict(price=round(float(s),2),net=round(net(s),2)) for s in points],
        holding_payoff=holding,underlying_stop=underlying_stop(bias,legs,forecast),
        status='Skip' if reasons else 'Paper candidate',reasons=reasons,
        exit_by=end,expiry=legs[0]['expiry'],slippage=prefs['slippage'],
        greeks=dict(theta_day=round(theta,2),delta=round(delta,2),vega_point=round(vega,2)))
