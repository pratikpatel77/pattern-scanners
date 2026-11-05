"""Entry board: F&O stocks where a scanner pattern and the saved Kronos forecast point the same way.

Pure functions, no network. Inputs are scanner rows with trigger/stop levels, the newest saved Kronos
forecast per stock, and live prices. A stock is Ripe only when every check below passes; anything that
fails one check is kept with the reason so nothing disappears silently.

These are price-level and forecast-agreement checks, not recommendations or measured win rates."""
from datetime import date

NEAR_PCT = 2.0          # price this close below (above, for shorts) the trigger counts as "near"
EXTENDED_PCT = 3.0      # already this far past the trigger = chasing, skip
FORECAST_MAX_AGE = 4    # calendar days since the forecast candle (covers a weekend plus a holiday)
FORECAST_DRIFT_PCT = 3.0  # price has moved this far from the forecast's last close: forecast is out of date
WAIT_PCT = 6.0          # a confirmed setup this far from its trigger is worth watching

ORDER = {'ripe': 0, 'watch': 1, 'wait': 2, 'skip': 3}


def pattern_state(direction, price, pivot, stop):
    """(state, past): past is the % beyond the trigger in the trade direction (negative = still short of it)."""
    long = direction == 'long'
    past = (price / pivot - 1) * 100 if long else (pivot / price - 1) * 100
    if (price <= stop) if long else (price >= stop):
        return 'invalidated', past
    if past >= 0:
        return ('triggered' if past <= EXTENDED_PCT else 'extended'), past
    return ('near' if past > -NEAR_PCT else 'waiting'), past


def kronos_view(direction, analysis, price, today):
    """How the saved forecast sits against the pattern direction. None when no forecast exists."""
    f = (analysis or {}).get('forecast_summary') or {}
    bias = f.get('bias')
    if not bias:
        return None
    last, median = f.get('last_close'), f.get('median_end')
    expected = (median / last - 1) * 100 if last and median else None
    up, down = bias.startswith('Up'), bias.startswith('Down')
    if (direction == 'long' and up) or (direction == 'short' and down):
        align = 'agree'
    elif (direction == 'long' and down) or (direction == 'short' and up):
        align = 'against'
    else:
        align = 'mixed'
    try:
        age = (today - date.fromisoformat(str(f.get('asof'))[:10])).days
    except ValueError:
        age = None
    drift = (price / last - 1) * 100 if price and last else None
    end=(analysis or {}).get('forecast_end')
    fresh = age is not None and 0 <= age <= FORECAST_MAX_AGE and (drift is None or abs(drift) <= FORECAST_DRIFT_PCT) and (not end or end>=str(today))
    return dict(bias=bias, align=align, expected_pct=round(expected, 2) if expected is not None else None,
                asof=f.get('asof'), age_days=age, drift_pct=round(drift, 2) if drift is not None else None,
                fresh=fresh, forecast_end=(analysis or {}).get('forecast_end'))


def event_in_trade(analysis):
    events = (analysis or {}).get('events') or {}
    inside = [e for e in events.get('events') or [] if e.get('date')]
    return (min(inside, key=lambda e: e['date']) if inside else None), bool(events.get('checked'))


def judge(direction, state, past, kronos, event):
    """(verdict, reasons). ripe > watch > wait > skip."""
    reasons = []
    if state == 'invalidated':
        return 'skip', ['Price is past the stop: the pattern has failed']
    if state == 'extended':
        return 'skip', [f'Already {past:.1f}% past the trigger: entering now is chasing']
    if kronos and kronos['align'] == 'against':
        side = 'down' if direction == 'long' else 'up'
        return 'skip', [f"Kronos leans {side} ({kronos['bias'].lower()}), against this pattern"]
    ready = state in ('near', 'triggered')
    confirmed = bool(kronos and kronos['align'] == 'agree' and kronos['fresh'])
    if not kronos:
        reasons.append('No Kronos forecast saved yet: open the stock and run one')
    elif kronos['align'] == 'mixed':
        reasons.append('Kronos sees a mixed outlook, so it does not confirm the pattern')
    elif not kronos['fresh']:
        if kronos['age_days'] is not None and kronos['age_days'] > FORECAST_MAX_AGE:
            reasons.append(f"Kronos forecast is {kronos['age_days']} days old: refresh it")
        elif kronos['drift_pct'] is not None and abs(kronos['drift_pct'])>FORECAST_DRIFT_PCT:
            reasons.append(f"Price moved {kronos['drift_pct']:+.1f}% since the forecast: refresh it")
        else:
            reasons.append('Forecast dates are missing, invalid or past the horizon: refresh it')
    if event:
        reasons.append(f"{event['title']} on {event['date']} falls inside the trade period")
    if ready and confirmed and not event:
        return 'ripe', ['Pattern ' + ('has triggered' if state == 'triggered' else 'is at its trigger') +
                        f" and Kronos agrees ({kronos['bias'].lower()})"]
    if ready:
        return 'watch', reasons
    if confirmed and past > -WAIT_PCT and not event:
        return 'watch', [f'Setting up: {-past:.1f}% to the trigger and Kronos agrees']
    return 'wait', reasons or [f'{-past:.1f}% from the trigger']


def rank(row):
    """Order inside a verdict group. Pattern strength, Kronos conviction and closeness to the trigger; not a probability."""
    k, p = row['kronos'], row['pattern']
    score = (p['score'] or 0) * .5 + (5 if p['timeframe'] == 'D+W' else 0)
    if k:
        score += {'agree': 20, 'mixed': 0, 'against': -30}[k['align']] + min(abs(k['expected_pct'] or 0), 4) * 2.5
    past = row['past_pct']
    score += max(0., 10 - abs(past) * (10 / (NEAR_PCT if past < 0 else EXTENDED_PCT)))
    return round(score, 1)


def evaluate(rows, analyses, quotes, today):
    """rows: scanner rows (symbol, direction, scanner, title, timeframe, score, date, close, pivot, stop, target).
    Returns one entry per stock and direction, best pattern each, sorted ripe first."""
    best = {}
    for r in rows:
        key = (r['symbol'], r['direction'])
        group = best.setdefault(key, dict(top=None, frames=set(), count=0))
        group['count'] += 1
        group['frames'] |= set(r['timeframe'].split('+')) if r['timeframe'] != 'D+W' else {'D', 'W'}
        if group['top'] is None or (r['score'] or 0) > (group['top']['score'] or 0):
            group['top'] = r
    out = []
    for (symbol, direction), g in best.items():
        p = g['top']
        quote = quotes.get(symbol) or {}
        price = quote.get('ltp') or p['close']
        state, past = pattern_state(direction, price, p['pivot'], p['stop'])
        analysis = analyses.get(symbol)
        kronos = kronos_view(direction, analysis, price, today)
        event, checked = event_in_trade(analysis)
        verdict, reasons = judge(direction, state, past, kronos, event)
        timeframe = 'D+W' if g['frames'] >= {'D', 'W'} else p['timeframe']
        if analysis and kronos and not checked and verdict == 'ripe':
            verdict='watch'
            reasons.append('Event calendar was not checked: review it before entry')
        if verdict=='ripe' and not quote.get('fresh'):
            verdict='watch'
            reasons.append('Fresh market quote unavailable: refresh before entry')
        row = dict(symbol=symbol, direction='bullish' if direction == 'long' else 'bearish', verdict=verdict,
                   reasons=reasons, state=state, past_pct=round(past, 2), price=price,
                   change_pct=quote.get('change_pct'), live=bool(quote.get('ltp')), fresh=bool(quote.get('fresh')),
                   source=quote.get('source') if quote.get('ltp') else 'scan close',
                   pattern=dict(title=p['title'], label=p.get('label', ''), timeframe=timeframe, score=p['score'],
                                date=p['date'], pivot=p['pivot'], stop=p['stop'], target=p['target'], more=g['count'] - 1),
                   kronos=kronos, event=event, events_checked=checked,
                   best_idea=(analysis or {}).get('best'))
        row['rank'] = rank(row)
        out.append(row)
    out.sort(key=lambda r: (ORDER[r['verdict']], -r['rank'], r['symbol']))
    return out
