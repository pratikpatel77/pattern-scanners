"""Active monitoring of open paper positions against the latest Kronos forecast.

Every minute in market hours: refresh live prices, re-test each open position against the newest
Kronos price paths, and re-run Kronos for a stock when its forecast is older than REFRESH_MINUTES.
Guidance is advice only (Hold / Book profit / Adjust / Exit); nothing is ever closed automatically.
"""
from datetime import datetime, timedelta, timezone
import json
import threading

import numpy as np

from .options_market import clock, market_open, number
from .options_math import bs, underlying_stop, model_exit, fee

import math

pct0 = lambda x: f'{math.floor(x + .5):.0f}'   # round half up, as the page does

REFRESH_MINUTES = 30     # re-run Kronos for an open position's stock at least this often in market hours
CHECK_SECONDS = 60
EXIT_PROFIT_SHARE = 35   # % of Kronos paths in profit below which (with a negative expectation) we say exit
DROP_ALERT = 15          # percentage-point fall in profitable paths since entry that triggers a warning


def net_delta(legs, spot, days, iv):
    h = spot * .001
    value = lambda s: sum(l['qty'] * l['lot'] * bs(s, l['strike'], days, iv, l['type']) for l in legs)
    return (value(spot + h) - value(spot - h)) / (2 * h)


def direction(delta, legs, spot):
    """bullish / bearish / range, from the expiry payoff: which way does a 7% move help?
    (Delta alone misreads far out-of-the-money spreads, which have tiny delta but a clear direction.)"""
    value = lambda s: sum(l['qty'] * l['lot'] * max(0., (s - l['strike']) if l['type'] == 'CE' else (l['strike'] - s)) for l in legs)
    up, down, flat = value(spot * 1.07), value(spot * .93), value(spot)
    scale = max(abs(up - flat), abs(down - flat), 1.)
    if up - down > .25 * scale:
        return 'bullish'
    if down - up > .25 * scale:
        return 'bearish'
    return 'range'


def kronos_stop(forecast, legs, bias):
    """Underlying stop from the latest forecast: recent support/resistance capped at one ATR."""
    try:
        return underlying_stop(bias, [dict(qty=l['qty'], type=l['type'], strike=l['strike']) for l in legs], forecast)
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def next_event(events):
    rows = sorted((events or {}).get('upcoming') or (events or {}).get('events') or [], key=lambda e: e['date'])
    return rows[0] if rows else None


def evaluate(trade, analysis, spot, ltp_pnl, contracts=(), events=None, baseline=None):
    """Test an open position against every Kronos path and turn the result into one clear action."""
    p = trade['payload']
    legs = [dict(qty=l['qty'], lot=l['lot'], strike=float(l['strike']), type=l['type'], entry=l['entry_fill'], symbol=l['symbol'],
                 iv=l.get('iv'),bid=l.get('bid',0.),ask_price=l.get('ask_price',0.))
            for l in p['legs']]
    f = analysis['forecast']
    paths = f.get('paths') or []
    iv = number(analysis.get('iv')) or 25.
    costs = sum((p.get('entry_charges') or {}).values())
    expiry = datetime.fromisoformat(p['plan']['expiry']).date()
    exit_by = p.get('exit_by') or p['plan'].get('exit_by') or str(expiry)
    today = clock().date()
    days_now = max((expiry - today).days, 1 / 24)
    delta = net_delta(legs, spot, days_now, iv)
    bias = direction(delta, legs, spot)
    stop = kronos_stop(f, legs, bias)
    # Evaluate at the planned exit or the forecast horizon, whichever comes first.
    dates = [r['date'] for r in (paths[0] if paths else [])]
    eligible = [i for i,d in enumerate(dates) if str(today)<=d<=exit_by]
    idx = max(eligible) if eligible else None
    prefs = p.get('prefs') or {}
    slip, brokerage = prefs.get('slippage',.01), prefs.get('brokerage',20.)
    credit = sum(-l['qty']*l['lot']*l['entry'] for l in legs)
    pnl, touched, breach = [], 0, 0
    shorts = [l for l in legs if l['qty'] < 0]
    for path in paths:
        if idx is None:
            continue
        row = path[idx]
        s = row['close']
        left = max((expiry - datetime.fromisoformat(row['date']).date()).days, 0)
        pnl.append(credit-costs+model_exit(legs,s,left,slip,brokerage,fallback_iv=iv))
        lows, highs = [path[i]['low'] for i in eligible], [path[i]['high'] for i in eligible]
        if stop and ((stop.get('lower') is not None and min(lows) <= stop['lower']) or
                     (stop.get('upper') is not None and max(highs) >= stop['upper'])):
            touched += 1
        if any((l['type'] == 'PE' and s < l['strike']) or (l['type'] == 'CE' and s > l['strike']) for l in shorts):
            breach += 1
    n = len(pnl) or 1
    stats = dict(profit_pct=round(sum(x > 0 for x in pnl) / n * 100, 1) if pnl else None,
                 expected=round(float(np.mean(pnl)), 2) if pnl else None,
                 tail=round(float(np.quantile(pnl, .1)), 2) if pnl else None,
                 stop_touch_pct=round(touched / n * 100, 1), short_breach_pct=round(breach / n * 100, 1),
                 eval_date=dates[idx] if idx is not None else None, bias=bias, delta=round(delta, 1))
    summary = f.get('summary') or {}
    view = summary.get('bias', '')
    max_profit, max_loss = p['plan'].get('max_profit'), p['plan'].get('max_loss') or 1
    reasons, action, suggestion = [], 'Hold', ''

    def roll_target(leg):
        """Nearest listed strike just beyond the Kronos 10% path-low (puts) / 90% path-high (calls) zone."""
        zone = analysis.get('lower') if leg['type'] == 'PE' else analysis.get('upper')
        strikes = sorted({c['strike'] for c in contracts if c['type'] == leg['type']})
        if zone is None or not strikes:
            return None
        beyond = [k for k in strikes if (k <= zone if leg['type'] == 'PE' else k >= zone)]
        return (max(beyond) if leg['type'] == 'PE' else min(beyond)) if beyond else None

    event = next_event(events)
    event_before_exit = bool(event and event['date'] <= exit_by)
    if str(today)>=exit_by:
        action='Exit'
        reasons.append('The planned exit date has been reached. Refresh exit quotes; settlement is not modelled.')
    elif stop and ((stop.get('lower') is not None and spot <= stop['lower']) or (stop.get('upper') is not None and spot >= stop['upper'])):
        action = 'Exit'
        level = stop['lower'] if stop.get('lower') is not None and spot <= stop['lower'] else stop['upper']
        reasons.append(f'Stock ₹{spot:,.2f} has crossed the Kronos stop ₹{level:,.2f}; the view behind this trade no longer holds.')
    elif idx is None:
        action='Watch'
        reasons.append('No future forecast bars before the planned exit. Refresh the forecast; no hold signal is available.')
    elif stats['profit_pct'] is not None and stats['profit_pct'] < EXIT_PROFIT_SHARE and (stats['expected'] or 0) < 0:
        action = 'Exit'
        reasons.append(f"Only {pct0(stats['profit_pct'])}% of the latest Kronos paths end in profit by {stats['eval_date']}, "
                       f"with an expected result of ₹{stats['expected']:,.0f}.")
    elif ltp_pnl is not None and max_profit and ltp_pnl >= .5 * max_profit:
        action = 'Book profit'
        reasons.append(f'You have captured {pct0(ltp_pnl / max_profit * 100)}% of the maximum profit; the rest is small against the remaining risk.')
    else:
        threatened = []
        for l in shorts:
            beyond = [i for i, path in enumerate(paths) if (l['type'] == 'PE' and path[idx]['close'] < l['strike']) or
                      (l['type'] == 'CE' and path[idx]['close'] > l['strike'])]
            share = len(beyond) / max(len(paths), 1) * 100
            median = summary.get('median_end')
            # Finishing beyond a short strike only hurts when the position loses there. A backspread holds extra
            # long options past the short strike, so for it that is the profit case, not a warning.
            loses_there = bool(beyond) and float(np.mean([pnl[i] for i in beyond])) < 0
            if loses_there and (share >= 40 or (median and ((l['type'] == 'PE' and median < l['strike']) or (l['type'] == 'CE' and median > l['strike'])))):
                threatened.append((l, share))
        against = (bias == 'bullish' and view.startswith('Down')) or (bias == 'bearish' and view.startswith('Up'))
        if threatened:
            action = 'Adjust'
            l, share = max(threatened, key=lambda x: x[1])
            target = roll_target(l)
            reasons.append(f"{pct0(share)}% of Kronos paths finish beyond your short {l['strike']:g} {l['type']}.")
            suggestion = (f"Roll the short {l['strike']:g} {l['type']} (with its hedge) to about {target:g} {l['type']}, beyond the Kronos "
                          f"{'low' if l['type'] == 'PE' else 'high'} zone, or close this side." if target else
                          f"Close or roll the short {l['strike']:g} {l['type']} away from the stock.")
        elif against:
            action = 'Adjust'
            reasons.append(f"Kronos has turned to '{view}', against this {bias} position.")
            suggestion = 'Reduce the position: close it, or add an opposite spread to flatten delta.'
        if baseline and baseline.get('profit_pct') is not None and stats['profit_pct'] is not None:
            drop = baseline['profit_pct'] - stats['profit_pct']
            if drop >= DROP_ALERT:
                reasons.append(f"Profitable Kronos paths fell from {pct0(baseline['profit_pct'])}% at entry to {pct0(stats['profit_pct'])}%.")
                if action == 'Hold':
                    action = 'Watch'
    if event_before_exit:
        reasons.append(f"{event['title']} on {event['date']} falls before your exit ({exit_by}); prices can gap.")
        if action in ('Hold', 'Watch') and (event.get('days_away') if event.get('days_away') is not None else 99) <= 2:
            action = 'Adjust'
            suggestion = suggestion or 'Exit or hedge before the event; gaps can jump past stops.'
    if action == 'Hold' and not reasons:
        reasons.append(f"Kronos still favours this trade: {pct0(stats['profit_pct'])}% of paths in profit by {stats['eval_date']}, "
                       f"expected ₹{stats['expected']:,.0f}." if stats['profit_pct'] is not None else 'No forecast paths to test yet.')
    return dict(trade=trade['id'], symbol=p['symbol'], action=action, reasons=reasons, suggestion=suggestion, stats=stats,
                pricing_note='Model exit includes spread, slippage and brokerage; hold-to-date estimate, stops counted separately. Missing leg IV uses ATM IV, or 25% if unavailable.',
                stop=stop, event=event, event_before_exit=event_before_exit, view=view, spot=spot, ltp_pnl=ltp_pnl,
                forecast_id=analysis.get('id'), forecast_created=analysis.get('forecast_created') or analysis.get('created'),
                forecast_mode=analysis.get('data_mode'), checked=clock().isoformat())


class PositionWatch:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.state = dict(running=False, last_check=None, message='Not started', refreshing=None)
        with service.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS desk_guidance (trade TEXT PRIMARY KEY, payload TEXT NOT NULL, baseline TEXT)')

    # ---------- storage ----------
    def saved(self):
        with self.service.store.connect() as db:
            rows = db.execute('SELECT trade,payload,baseline FROM desk_guidance').fetchall()
        return {r[0]: dict(json.loads(r[1]), baseline=json.loads(r[2]) if r[2] else None) for r in rows}

    def save(self, value, baseline):
        with self.service.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO desk_guidance VALUES (?,?,?)',
                       (value['trade'], json.dumps(value, allow_nan=False), json.dumps(baseline, allow_nan=False) if baseline else None))

    # ---------- one pass ----------
    def check(self, only=None, refresh=True, force=False):
        options = self.service.options
        trades = [t for t in options.journal.paper() if t['status'] == 'open' and (only is None or t['id'] == only)]
        saved = self.saved()
        results = {}
        if not trades:
            return results
        symbols = sorted({t['payload']['symbol'] for t in trades})
        quotes = {}
        try:
            wanted = [f'NSE:{s}-EQ' for s in symbols] + sorted({l['symbol'] for t in trades for l in t['payload']['legs']})
            for i in range(0, len(wanted), 50):
                quotes.update(self.service.desk.ltp(wanted[i:i + 50])['quotes'])
        except ValueError as exc:
            self.state['message'] = f'Live prices unavailable: {exc}'
        for t in trades:
            p, sym = t['payload'], t['payload']['symbol']
            key = options.journal.latest_forecast_id(sym)
            analysis = options.journal.analysis(key) if key else None
            age = None
            if analysis:
                made = datetime.fromisoformat(analysis.get('forecast_created') or analysis['created'])
                age = (datetime.now(timezone.utc) - made).total_seconds() / 60
            # Re-run Kronos when the forecast is missing or older than the refresh interval (one stock at a time).
            if refresh and market_open() and (force or analysis is None or age > REFRESH_MINUTES) and self.service.active is None:
                try:
                    options.submit(dict(symbol=sym))
                    self.state['refreshing'] = sym
                except ValueError:
                    pass
            if not analysis or not (analysis.get('forecast') or {}).get('paths'):
                continue
            spot = number((quotes.get(f'NSE:{sym}-EQ') or {}).get('ltp')) or number(analysis.get('spot'))
            buy=sell=0.
            legs_ok=True
            for leg in p['legs']:
                quote=quotes.get(leg['symbol']) or {}
                price=number(quote.get('bid' if leg['qty']>0 else 'ask'))
                if price<=0:
                    legs_ok=False; break
                amount=price*abs(leg['qty'])*leg['lot']
                if leg['qty']>0:sell+=amount
                else:buy+=amount
            credit=sum(-l['qty']*l['lot']*l['entry_fill'] for l in p['legs'])
            ltp_pnl=(credit+sell-buy-sum((p.get('entry_charges') or {}).values())-
                     fee(buy,sell,len(p['legs']),(p.get('prefs') or {}).get('brokerage',20.))) if legs_ok else None
            try:
                events = options.events(sym, p['plan']['expiry'])
            except Exception:
                events = None
            contracts = [c for c in getattr(options.market, 'contracts', []) if c.get('underlying') == sym and c.get('expiry') == p['plan']['expiry']]
            old = saved.get(t['id'])
            baseline = old.get('baseline') if old else None
            value = evaluate(t, analysis, spot, ltp_pnl, contracts, events, baseline)
            if not baseline:
                baseline = dict(value['stats'], forecast_id=value['forecast_id'], time=value['checked'])
            prev_action = old.get('action') if old else None
            value['changed'] = value['action'] != prev_action
            value['since'] = value['checked'] if value['changed'] or not old else old.get('since', value['checked'])
            value['forecast_age_min'] = round(age, 1) if age is not None else None
            self.save(value, baseline)
            results[t['id']] = {**value, 'baseline': baseline}
        self.state.update(last_check=clock().isoformat(), message=f'Checked {len(results)} open position(s).')
        return results

    def snapshot(self):
        open_ids = {t['id'] for t in self.service.options.journal.paper() if t['status'] == 'open'}
        rows = {k: v for k, v in self.saved().items() if k in open_ids}
        job = self.service.active
        return dict(positions=rows, state={**self.state, 'busy': bool(job)}, refresh_minutes=REFRESH_MINUTES,
                    check_seconds=CHECK_SECONDS, market_open=market_open())

    # ---------- background loop ----------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run, name='position-watch', daemon=True)
        self.thread.start()

    def run(self):
        self.state['running'] = True
        first = True
        while not self.stop_event.wait(5 if first else CHECK_SECONDS):
            if market_open() or first:
                try:
                    self.check(refresh=market_open())
                except Exception as exc:   # monitoring must never stop the app
                    self.state['message'] = f'Monitor error: {type(exc).__name__}'
            first = False

    def close(self):
        self.stop_event.set()
