"""The decision layer of the ADX desk. Pure functions: no network, no clock, no storage.

market_state   what the index and the whole F&O list are doing (direction, breadth, volatility)
conviction     one 0-100 score for a setup, built from named pieces of evidence
decide         Take / Wait / Pass, with the reason
manage         trailing stop and adjustment for an open trade
review         what happened in a closed trade, and the mistake if there was one
lessons        what the closed trades teach: score changes and rule changes

Learning is deliberately slow. Every average is mixed with PRIOR imaginary break-even trades, so
three results barely move a score and thirty move it properly. Nothing here is a measured edge.
"""
import math

from . import adx_core as core

PRIOR = 6                 # imaginary break-even trades mixed into every learned average
LEAN = 0.35               # market tilt beyond which a setup is with or against the market
MIN_SEEN = 3              # closed trades before a learned effect is shown or used
POLICY = dict(book=80, trail=1.0, stall_days=7, long_tilt=1.0)   # the rules before any lesson
MISTAKES = dict(
    late_exit='Closed after the exit signal',
    gave_back='Gave back a profit',
    structure='Right on the stock, wrong position',
    stalled='Stock went nowhere: time decay',
    overshoot='Lost more than planned')
TAG_TEXT = {'market:with': 'taken with the market', 'market:against': 'taken against the market',
            'market:flat': 'taken in a market with no direction', 'market:trending': 'range trades in a trending market',
            'weekly:with': 'with the weekly trend', 'weekly:against': 'against the weekly trend',
            'grade:A': 'graded A', 'grade:B': 'graded B', 'grade:C': 'graded C'}


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def vol_rank(closes, n=20, look=250, per_year=252):
    """(realised volatility now in %, its percentile within the last `look` bars).
    `per_year` is how many bars make a year: 252 daily bars, or 7 times that for 1-hour bars."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < n + 40:
        return None, None
    vols = []
    for k in range(max(n, len(rets) - look), len(rets) + 1):
        w = rets[k - n:k]
        mean = sum(w) / n
        vols.append(math.sqrt(sum((x - mean) ** 2 for x in w) / (n - 1) * per_year) * 100)
    return round(vols[-1], 1), round(sum(v <= vols[-1] for v in vols) / len(vols) * 100)


def market_state(bench, breadth=None, per_year=252):
    """The background every setup trades against. `bench` is (dates, h, l, c) of the index;
    `breadth` is {n, up, down, trending, above} in % across the scanned stocks.
    `tilt` runs from -1 (everything falling) to +1 (everything rising)."""
    out = dict(regime='unknown', tilt=0., index=None, breadth=breadth, vol=None, vol_rank=None, date=None, lines=[], stance='')
    leans = []
    if bench and len(bench[3]) > core.WARMUP:
        d, h, l, c = bench
        pdi, mdi, adx, _ = core.dmi(h, l, c)
        dom = core.dominance(pdi, mdi, core.DMI_N + 1)[0][-1]
        avg = core.ema(c, 20)[-1]
        strong = adx[-1] >= core.STRONG
        side = 1 if dom == 1 and c[-1] > avg else -1 if dom == -1 and c[-1] < avg else 0
        out['regime'] = 'bull' if side > 0 else 'bear' if side < 0 else 'mixed' if strong else 'range'
        out['index'] = dict(adx=round(adx[-1], 1), pdi=round(pdi[-1], 1), mdi=round(mdi[-1], 1), dominant=dom,
                            above_ema=c[-1] > avg, rising=adx[-1] > adx[-4], close=round(c[-1], 2))
        out['date'] = str(d[-1])
        leans.append((.6, side * (1. if strong else .6)))
        name = {'bull': 'uptrend', 'bear': 'downtrend', 'mixed': 'trend and price disagree', 'range': 'no trend'}[out['regime']]
        out['lines'].append(f"Nifty: {'strong ' if strong and side else ''}{name} (ADX {adx[-1]:.0f}, "
                            f"{'+DMI' if pdi[-1] > mdi[-1] else '−DMI'} on top, price {'above' if c[-1] > avg else 'below'} its 20 EMA).")
        out['vol'], out['vol_rank'] = vol_rank(c, per_year=per_year)
    if breadth and breadth.get('n'):
        # Share of stocks above their 20 EMA: 50% is neutral, 80% or 20% is a full lean. DMI dominance
        # changes hands slowly, so it is shown but not used for the lean.
        leans.append((.4, clamp((breadth['above'] - 50) / 30, -1, 1)))
        out['lines'].append(f"Breadth: {breadth['above']}% of {breadth['n']} F&O stocks are above their 20 EMA; {breadth['up']}% have +DMI dominant, "
                            f"{breadth['down']}% have −DMI dominant, {breadth['trending']}% have ADX above 25.")
    if leans:
        out['tilt'] = round(sum(w * x for w, x in leans) / sum(w for w, _ in leans), 2)
    if out['vol_rank'] is not None:
        mood = 'high' if out['vol_rank'] >= 80 else 'low' if out['vol_rank'] <= 20 else 'normal'
        out['lines'].append(f"Volatility: Nifty is moving {out['vol']:.0f}% a year, {mood} against its last year ({out['vol_rank']}th percentile)"
                            + ('. Stops are hit more easily and options cost more.' if mood == 'high' else '.'))
    t = out['tilt']
    out['stance'] = ('The market is helping bullish setups. Bearish setups are fighting it.' if t >= LEAN else
                     'The market is helping bearish setups. Bullish setups are fighting it.' if t <= -LEAN else
                     'The market is not helping either side. Judge each stock on its own; range positions fit.') if leans else ''
    return out


def tags(row, market, read=None):
    """The conditions a trade was taken in. Stored with the trade so results can be grouped later."""
    tilt = (market or {}).get('tilt') or 0.
    f = row.get('features') or {}
    if row.get('state') == 'range':
        out = dict(market='flat' if abs(tilt) < LEAN else 'trending')
    else:
        with_ = tilt * (1 if row['direction'] == 'bullish' else -1)
        out = dict(market='with' if with_ >= LEAN else 'against' if with_ <= -LEAN else 'flat')
        if f.get('wk_adx') is not None:
            out['weekly'] = 'with' if f.get('weekly_aligned') else 'against'
    if row.get('grade'):
        out['grade'] = row['grade']
    if read and read.get('iv_hv') is not None:
        out['vol'] = 'rich' if read['iv_hv'] >= 1.3 else 'cheap' if read['iv_hv'] <= 1.0 else 'fair'
    return out


def conviction(row, market=None, effects=None):
    """Score a setup from 0 to 100, starting at 50. Each factor adds or removes points and says why,
    so the score can always be read back. It orders setups; it is not a win probability."""
    f, market = row.get('features') or {}, market or {}
    tilt, known = market.get('tilt') or 0., market.get('regime', 'unknown') != 'unknown' or bool(market.get('breadth'))
    parts = []

    def add(name, points, note):
        points = int(round(points))
        if points:
            parts.append(dict(name=name, points=points, note=note))

    quality = row.get('quality') or 0
    if row.get('state') == 'range':
        length = f.get('base_bars') or 0
        add('Length of the range', clamp((length - 30) / 30 * 10, 0, 10), f'ADX under 25 for {length} bars')
        add('Rules passed', clamp((quality - 70) / 30 * 8, -8, 8), f"{quality}% of the range checklist")
        if known:
            calm = abs(tilt) < LEAN
            add('Market', 6 if calm else -6, 'The market has no strong direction either, which suits a range' if calm
                else 'The market is trending, which tends to pull quiet stocks out of their range')
        if (market.get('vol_rank') or 0) >= 80:
            add('Volatility', -5, 'Index volatility is high: ranges break more often')
    else:
        sign = 1 if row['direction'] == 'bullish' else -1
        add('Rules passed', clamp((quality - 70) / 30 * 12, -12, 12), f"{quality}% of the setup's checklist")
        if f.get('wk_adx') is not None:
            add('Weekly trend', 8 if f.get('weekly_aligned') else -8, 'Weekly DMI points the same way' if f.get('weekly_aligned') else 'Weekly DMI points the other way')
        if known:
            push = sign * tilt
            add('Market', push * 12, 'Index and breadth are moving the same way' if push >= LEAN else
                'Index and breadth are moving the other way' if push <= -LEAN else 'The market is leaning slightly ' + ('with' if push > 0 else 'against') + ' this trade')
        if f.get('rs20') is not None:
            ahead = sign * f['rs20']
            add('Relative strength', clamp(ahead / 5 * 6, -6, 6), f"{f['rs20']:+.1f}% against Nifty over 20 days")
        if 'adx_rising' in f:
            add('ADX direction', 4 if f['adx_rising'] else -2, 'ADX is rising: the trend is gaining strength' if f['adx_rising'] else 'ADX is falling')
        if (row.get('risk_pct') or 0) > 6:
            add('Stop distance', -4, f"The stop is {row['risk_pct']:.1f}% away: an option needs a large move to pay for that risk")
    score = 50 + sum(p['points'] for p in parts)
    grade = lambda s: 'A' if s >= 70 else 'B' if s >= 55 else 'C'
    # Your own results, grouped by the same conditions. The grade used is the one before this step.
    for key, value in tags(dict(row, grade=grade(score)), market).items():
        learned = (effects or {}).get(f'{key}:{value}')
        if learned:
            add('Your record', learned['points'], f"Your trades {TAG_TEXT.get(f'{key}:{value}', value)}: {learned['wins']}/{learned['n']} won, "
                                                  f"average {learned['avg']:+.0f}% of risk")
    score = int(clamp(50 + sum(p['points'] for p in parts), 0, 100))
    parts.sort(key=lambda p: -abs(p['points']))
    return dict(score=score, grade=grade(score), factors=parts)


def decide(row, plan=None, event=None, oi=None, unpriced=False):
    """One call for the detail panel: Take, Wait or Pass, the reason, and what speaks for and against."""
    best = next((x for x in (plan or {}).get('ideas') or [] if x['name'] == plan.get('best')), None) if plan else None
    read = (plan or {}).get('chain') or {}
    pros = [f"{p['name']}: {p['note']} ({p['points']:+d})" for p in row.get('factors') or [] if p['points'] > 0]
    cons = [f"{p['name']}: {p['note']} ({p['points']:+d})" for p in row.get('factors') or [] if p['points'] < 0]
    if read.get('iv_hv') is not None and best:
        buying = best['kind'] in ('long', 'debit', 'long_vol')
        if read['iv_hv'] >= 1.3:
            (cons if buying else pros).append(f"Options are expensive ({read['iv_hv']}x the stock's recent movement), and this position {'buys' if buying else 'sells'} them")
        elif read['iv_hv'] <= 1.0:
            (pros if buying else cons).append(f"Options are cheap ({read['iv_hv']}x the stock's recent movement), and this position {'buys' if buying else 'sells'} them")
    if oi and oi.get('available') and oi.get('bias') in ('bullish', 'bearish') and row.get('direction') in ('bullish', 'bearish'):
        (pros if oi['bias'] == row['direction'] else cons).append(f"Open-interest and price changes today lean {oi['bias']}")
    if event and event.get('inside') and event.get('next'):
        cons.append(f"{event['next'].get('title') or 'A scheduled event'} on {event['next']['date']} falls inside the trade: the stock can gap")
    elif event and not event.get('checked'):
        cons.append('The event calendar could not be checked')
    if plan and not read.get('liquid', True):
        cons.append('The option chain is thin: fills will be worse than shown')
    stale = bool(plan and plan.get('stale'))
    if unpriced:
        cons.append('No option position could be priced: the chain failed to load or is too thin')
    if stale:
        cons.append('Option prices are the last reported quotes, not live: the market is closed or the chain feed is down')
    if row.get('price_stale'):
        age = row.get('ltp_age')
        cons.append(f"The stock price is {age:.0f}s old: its live refresh failed" if age is not None
                    else "No live stock price arrived: the row is priced from the last daily close")
    grade, verdict = row.get('grade'), row.get('verdict')
    if verdict in ('skip', 'watch'):
        call, why = 'Pass', row.get('why', '')
    elif plan is not None and not best:
        call, why = 'Pass', 'The stock setup is valid, but no option position on this chain pays enough for its risk.'
    elif grade == 'C':
        call, why = 'Pass', f"Score {row.get('score')}: too much speaks against this setup. Better ones are higher on the list."
    elif event and event.get('inside') and best and best['kind'] in ('credit', 'short_vol'):
        call, why = 'Pass', 'A scheduled event falls inside the trade and this position has sold options: a gap can jump the short strike.'
    elif verdict == 'armed':
        call, why = 'Wait', f"Score {row.get('score')} (grade {grade}). Not triggered yet: act only when the stock trades through {row.get('trigger'):,.2f}."
    elif unpriced:
        call, why = 'Wait', f"Score {row.get('score')} (grade {grade}). Setup valid — option prices unavailable."
    elif row.get('price_stale'):
        call, why = 'Wait', f"Score {row.get('score')} (grade {grade}). Setup valid — waiting for a live stock price."
    elif stale:
        # The setup itself is ready, but a stale or closed-market chain cannot be traded, so the
        # headline call must not read as "go ahead" -- that is what the separate chain warning below
        # it would otherwise contradict.
        call, why = 'Wait', f"Score {row.get('score')} (grade {grade}). Setup valid — waiting for live option prices."
    else:
        call = 'Take'
        against = any(p['name'] == 'Market' and p['points'] <= -8 for p in row.get('factors') or [])
        why = f"Score {row.get('score')} (grade {grade})." + (
            ' The setup is valid, but it trades against a strong market: that is its main risk.' if against else
            ' An average setup: nothing is wrong, nothing stands out.' if grade == 'B' else ' The evidence lines up.')
    return dict(call=call, why=why, pros=pros, cons=cons)


def manage(t, price, best, policy=None, strikes=(), event_days=None):
    """Trailing stop and adjustment for an open trade. `best` is the best stock price since entry.
    Returns {stop, trailed, adjust}: `adjust` is a sentence, or '' when nothing needs changing."""
    policy = policy or POLICY
    out = dict(stop=t.get('stop'), trailed=False, adjust='')
    shorts = [l for l in t.get('legs') or [] if l['qty'] < 0]
    if event_days is not None and 0 <= event_days <= 2 and shorts:
        out['adjust'] = f"A scheduled event is {event_days} day(s) away and this position has sold options. Close it before the event: a gap can jump the short strike."
    if t['direction'] == 'neutral':
        entry = t.get('entry_spot')
        for l in shorts if price is not None and entry and not out['adjust'] else []:
            room = abs(l['strike'] - entry)
            left = (l['strike'] - price) if l['type'] == 'CE' else (price - l['strike'])
            if room > .01 * entry and left < .3 * room:
                out['adjust'] = (f"The stock ({price:,.2f}) is close to your sold {l['strike']:g} {l['type']}. Close that side now and keep the other, "
                                 f"or close the whole position. Do not wait for the range to break.")
                break
        return out
    sign = 1 if t['direction'] == 'bullish' else -1
    entry = t.get('entry_spot') or t.get('trigger')
    risk = abs((t.get('trigger') or 0) - (t.get('stop') or 0))
    if not entry or not risk or price is None:
        return out
    best = max(best or price, price) if sign > 0 else min(best or price, price)
    if (best - entry) * sign >= policy['trail'] * risk:
        # Follow the stock one risk-unit behind its best price, never below the entry price.
        trail = best - sign * risk
        trail = max(trail, entry) if sign > 0 else min(trail, entry)
        if (trail - t['stop']) * sign > 0:
            out.update(stop=round(trail, 2), trailed=True)
    if out['adjust']:
        return out
    pnl, progress = t.get('pnl'), t.get('progress')
    if t['kind'] == 'long' and progress is not None and progress >= 50 and (pnl or 0) > 0:
        typ = 'CE' if sign > 0 else 'PE'
        near = min(strikes, key=lambda k: abs(k - t['target'])) if strikes else None
        held = next((l['strike'] for l in t.get('legs') or [] if l['qty'] > 0), None)
        if near is None or held is None or (near - held) * sign > 0:
            out['adjust'] = (f"The stock is {progress}% of the way to the target. Sell the {f'{near:g} {typ}' if near else f'{typ} nearest the target'} against your bought option: "
                             f"it becomes a debit spread, takes money off the table and still pays up to the target.")
    elif t['kind'] in ('long', 'debit') and (t.get('days_held') or 0) >= policy['stall_days'] and progress is not None \
            and abs(progress) < 25 and (pnl or 0) < 0:
        out['adjust'] = (f"The stock has gone nowhere for {t['days_held']} days and time decay is costing you. Close now rather than waiting for the stop; "
                         f"re-enter if the setup triggers again.")
    return out


def review(t, first_exit=None):
    """What happened in one closed trade. `mistake` is a key of MISTAKES, or '' for a trade that was
    simply right or simply wrong. `first_exit` is the first Exit alert the desk raised for it."""
    pnl, risk_money = t.get('pnl'), t.get('max_loss')
    if pnl is None or not risk_money:
        return dict(mistake='', text='', peak=None, move_r=None)
    marks = t.get('marks') or []
    seen = [m['net_pnl'] for m in marks if m.get('net_pnl') is not None]
    peak = max(seen) if seen else None
    spots = [m['underlying'] for m in marks if m.get('underlying')]
    move = None
    sign = 0 if t['direction'] == 'neutral' else 1 if t['direction'] == 'bullish' else -1
    unit = abs((t.get('trigger') or 0) - (t.get('stop') or 0))
    if sign and unit and spots and t.get('entry_spot'):
        move = round((spots[-1] - t['entry_spot']) * sign / unit, 2)
    out = dict(mistake='', peak=peak, move_r=move, cost=0.)
    late = first_exit and first_exit.get('pnl') is not None and t['updated'][:10] > first_exit['time'][:10] \
        and pnl < first_exit['pnl'] - .1 * risk_money
    if late:
        out.update(mistake='late_exit', cost=round(first_exit['pnl'] - pnl, 2),
                   text=f"The desk said exit on {first_exit['time'][:10]} at about ₹{first_exit['pnl']:,.0f}. Closing later cost ₹{first_exit['pnl'] - pnl:,.0f} more.")
    elif peak is not None and peak >= .25 * risk_money and pnl < .5 * peak:
        out.update(mistake='gave_back', cost=round(peak - pnl, 2),
                   text=f"Was up ₹{peak:,.0f} and closed at ₹{pnl:,.0f}. The profit was not protected.")
    elif pnl > 0:
        out['text'] = 'Worked as planned.'
    elif move is not None and move >= .5:
        out.update(mistake='structure', cost=round(-pnl, 2),
                   text=f"The stock moved {move:+.1f}R your way but the position lost: time decay or the price paid ate the move.")
    elif move is not None and abs(move) < .5:
        out.update(mistake='stalled', cost=round(-pnl, 2), text='The stock went nowhere and the position lost value while waiting.')
    elif t.get('planned_loss') and -pnl > 1.3 * t['planned_loss']:
        out.update(mistake='overshoot', cost=round(-pnl - t['planned_loss'], 2),
                   text=f"Lost ₹{-pnl:,.0f} against a planned ₹{t['planned_loss']:,.0f}: the exit was late or the stock gapped.")
    else:
        out['text'] = 'The stock went against the setup and the loss stayed within the plan. Nothing to fix.' if move is not None else 'Closed at a loss.'
    return out


def lessons(closed):
    """Turn closed trades into (a) score changes per condition, (b) a count of each mistake and
    (c) rule changes. Trades need `tags` and `review` (older trades without them are skipped)."""
    done = [t for t in closed if t.get('pnl') is not None and t.get('max_loss')]
    effects, notes = {}, []
    for dim in ('market', 'weekly', 'grade'):
        for value in sorted({(t.get('tags') or {}).get(dim) for t in done} - {None}):
            group = [t for t in done if (t.get('tags') or {}).get(dim) == value]
            rets = [t['pnl'] / t['max_loss'] for t in group]
            points = int(round(clamp(sum(rets) / (len(rets) + PRIOR) * 30, -8, 8)))
            if len(rets) < MIN_SEEN or not points:
                continue
            wins, avg = sum(t['pnl'] > 0 for t in group), sum(rets) / len(rets) * 100
            effects[f'{dim}:{value}'] = dict(points=points, n=len(rets), wins=wins, avg=round(avg, 1))
            notes.append(f"Trades {TAG_TEXT.get(f'{dim}:{value}', value)}: {wins}/{len(rets)} won, average {avg:+.0f}% of risk. "
                         f"Setups like this now score {abs(points)} point{'s' if abs(points) > 1 else ''} {'higher' if points > 0 else 'lower'}.")
    found = {}
    for t in done:
        key = (t.get('review') or {}).get('mistake')
        if key:
            row = found.setdefault(key, dict(key=key, name=MISTAKES[key], n=0, cost=0., long=0))
            row['n'] += 1
            row['cost'] = round(row['cost'] + (t['review'].get('cost') or 0.), 2)
            row['long'] += t.get('kind') == 'long'
    policy, changes = dict(POLICY), []
    if found.get('gave_back', {}).get('n', 0) >= 2:
        policy.update(book=65, trail=.75)
        changes.append(f"You gave back a profit {found['gave_back']['n']} times. The desk now says Book at 65% of the way to the target (was 80%) "
                       f"and starts trailing the stop after 0.75R (was 1R).")
    if found.get('stalled', {}).get('n', 0) >= 2:
        policy['stall_days'] = 5
        changes.append(f"{found['stalled']['n']} trades lost to time decay while the stock stood still. Bought positions are now flagged after 5 flat days (was 7).")
    if found.get('structure', {}).get('long', 0) >= 2:
        policy['long_tilt'] = .85
        changes.append(f"{found['structure']['long']} bought options lost although the stock moved your way. Bought options are now ranked 15% lower against spreads.")
    if found.get('late_exit', {}).get('n', 0) >= 1:
        changes.append(f"Closing after the Exit signal has cost ₹{found['late_exit']['cost']:,.0f} so far. Close on the day the desk says Exit.")
    return dict(effects=effects, notes=notes, mistakes=sorted(found.values(), key=lambda r: -r['cost']), policy=policy, changes=changes)
