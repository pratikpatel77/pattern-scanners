"""Auditable OI context. Heuristic ranking only; never a probability or a payoff input."""
import math
from datetime import date
from .sensibull import timestamp
from .options_market import clock

VERSION = 1
UNAVAILABLE = 'OI context unavailable or stale'
CONFLICT = 'OI and price context conflicts with this direction'
MIXED = 'OI evidence is mixed across expiries or time windows'
LIMIT = .08


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sign(value, threshold):
    return 1 if value > threshold else -1 if value < -threshold else 0


def positioning(price_change, pcr_change):
    # PCR is not a measure of option writing. Require price confirmation; abstain on disagreement.
    if not numeric(price_change) or not numeric(pcr_change):
        return 0
    price, oi = sign(price_change, .15), sign(pcr_change, .02)
    return price if price == oi else 0


def levels(snap, spot):
    def peak(index, lo, hi):
        rows = [(float(k), v[index]) for k, v in snap['strikes'].items() if lo <= float(k) <= hi and v[index] > 0]
        if not rows:
            return None
        k, oi = max(rows, key=lambda r: (r[1], -abs(r[0]-spot)))
        row=snap['strikes'][format(k, '.12g')]
        return dict(strike=k, oi=oi, day_change=oi-row[index+2] if row[index+2] is not None else None)
    return dict(support=peak(1, spot*.95, spot), resistance=peak(0, spot, spot*1.05))


def history_context(series, snap, kind):
    """Use a contiguous tail with the same explicit expiry basket; never cross a roll boundary."""
    valid = []
    for r in (series or {}).get('rows', []):
        try:
            day = date.fromisoformat(r['at'])
            if day.isoformat() > snap['date']:
                continue
            fields = ('oi', 'ltp') if kind == 'fut' else ('call_oi', 'put_oi', 'ltp')
            if any(not numeric(r.get(k)) or r[k] < 0 for k in fields) or r['ltp'] <= 0:
                continue
            valid.append(r)
        except (KeyError, TypeError, ValueError):
            continue
    dates = [r['at'] for r in valid]
    if len(dates) != len(set(dates)):
        return dict(ready=False, note='Duplicate daily history dates.')
    valid.sort(key=lambda r: r['at'])
    tail = []
    for row in reversed(valid):
        if sorted(row.get('expiries') or []) != snap['expiries']:
            break
        tail.append(row)
    tail.reverse()
    if len(tail) < 2:
        return dict(ready=False, note='Not enough daily history with the same two expiries.')
    last, prev = tail[-1], tail[-2]
    if (date.fromisoformat(last['at'])-date.fromisoformat(prev['at'])).days > 4:
        return dict(ready=False, note='Daily history has a large gap.')
    if (date.fromisoformat(snap['date'])-date.fromisoformat(last['at'])).days > 4:
        return dict(ready=False, note='Daily history is too old.')
    pc = (last['ltp']/prev['ltp']-1)*100
    out = dict(ready=True, asof=last['at'], observations=len(tail), price_change_pct=pc,
               expiries=snap['expiries'], source='Sensibull daily history', direction=0)
    if kind == 'fut':
        change = (last['oi']/prev['oi']-1)*100 if prev['oi'] else None
        ps, os = sign(pc, .15), sign(change, .5) if change is not None else 0
        activity = {(1, 1): 'Price up / futures OI up', (1, -1): 'Price up / futures OI down',
                    (-1, 1): 'Price down / futures OI up', (-1, -1): 'Price down / futures OI down'}
        out.update(oi_change_pct=change, activity=activity.get((ps, os), 'Small or mixed changes'), direction=ps if os else 0)
    else:
        first = tail[max(0, len(tail)-4)]
        old = first['put_oi']/first['call_oi'] if first['call_oi'] else None
        current = last['put_oi']/last['call_oi'] if last['call_oi'] else None
        out.update(pcr=current, pcr_change=current-old if current is not None and old is not None else None,
                   from_date=first['at'], trend_price_change_pct=(last['ltp']/first['ltp']-1)*100,
                   trend_direction=positioning((last['ltp']/first['ltp']-1)*100, current-old) if current is not None and old is not None else 0)
    return out


def build_context(snap, windows, histories, spot, mode, asof):
    now = clock()
    stamp = timestamp(snap['data_time'])
    age = (now-stamp).total_seconds()
    notes = ['OI includes buyers and sellers; these are context clues, not proof of option writing.']
    issues = []
    if snap.get('version') != 2 or len(snap.get('by_expiry', {})) != 2:
        issues.append('Two verified monthly chains are required.')
    if age < -60:
        issues.append('OI timestamp is in the future.')
    if mode == 'Live':
        if age > 600 or snap['date'] != now.date().isoformat():
            issues.append('OI is older than ten minutes or belongs to another session.')
        if abs(snap['spot']/spot-1) > .005:
            issues.append('OI and current spot differ by more than 0.5%.')
    elif snap['date'] != asof:
        issues.append('OI date does not match the forecast closing date.')
    if snap.get('timestamp_skew_seconds', 0):
        issues.append('Monthly OI timestamps do not match exactly; wait for aligned data.')
    by_expiry = snap.get('by_expiry', {})
    each = {e: dict(call_oi=p['call_oi'], put_oi=p['put_oi'], pcr=p['pcr'], pcr_change=p['pcr_change'],
                   call_change=p['call_oi_change'], put_change=p['put_oi_change'],
                   direction=positioning(p.get('spot_change_pct'), p.get('pcr_change')), **levels(p, spot))
            for e, p in by_expiry.items()}
    daily = positioning(snap.get('spot_change_pct'), snap.get('pcr_change'))
    votes = [daily] if daily else []
    evidence = []
    if daily:
        evidence.append('Daily price and combined PCR change agree.')
    # Overlapping windows form ONE confirmation, not five independent votes.
    intraday = [positioning(w.get('spot_change_pct'), w.get('pcr_change')) for m, w in windows.items()
                if int(m) in (30, 60) and w.get('ready')]
    intraday = [v for v in intraday if v]
    votes += intraday
    if intraday:
        evidence.append('Recorded 30/60-minute price and PCR context is available.')
    oi_history = history_context(histories.get('oi'), snap, 'oi')
    fut = history_context(histories.get('fut'), snap, 'fut')
    # Undated intraday availability must never turn daily futures data into a live signal.
    futures_vote = fut.get('direction', 0) if mode == 'EOD' and fut.get('asof') == snap['date'] else 0
    if futures_vote:
        votes.append(futures_vote)
        evidence.append(fut['activity'] + ' (daily context).')
    expiry_votes = {v['direction'] for v in each.values() if v['direction']}
    mixed = len(set(votes)) > 1 or len(expiry_votes) > 1
    # No directional bias from futures or absolute PCR alone.
    direction = daily if daily and not mixed else 0
    strength = .5 if direction else 0.
    if direction and (direction in intraday or futures_vote == direction):
        strength = 1.
    if direction and oi_history.get('trend_direction'):
        if oi_history['trend_direction'] != direction:
            strength *= .5
            notes.append('Recent daily options history points the other way; OI influence is halved.')
        else:
            evidence.append('Recent same-expiry daily options history agrees; it is not counted as another independent signal.')
    if mixed:
        notes.append('Monthly or time-window evidence conflicts; no OI ranking boost.')
    rollover = dict(note='Next-month share is positioning, not a measured trader rollover rate.')
    if len(by_expiry) == 2:
        front, nxt = [by_expiry[e] for e in sorted(by_expiry)]
        total = snap['call_oi']+snap['put_oi']
        next_total = nxt['call_oi']+nxt['put_oi']
        rollover['next_month_share_pct'] = next_total/total*100 if total else None
        if all(p.get('prev_call_oi') is not None and p.get('prev_put_oi') is not None for p in (front, nxt)):
            before = snap['prev_call_oi']+snap['prev_put_oi']
            next_before = nxt['prev_call_oi']+nxt['prev_put_oi']
            fchange = front['call_oi_change']+front['put_oi_change']
            nchange = nxt['call_oi_change']+nxt['put_oi_change']
            rollover.update(front_change=fchange, next_change=nchange, combined_change=fchange+nchange,
                            share_change_pp=next_total/total*100-next_before/before*100 if total and before else None,
                            possible_roll=fchange < 0 < nchange)
            if rollover['possible_roll']:
                notes.append('Front-month OI fell while next-month OI rose: possible rollover, not proof of new direction.')
    if not fut.get('ready'):
        notes.append('Comparable daily futures context is unavailable.')
    if not oi_history.get('ready'):
        notes.append('Comparable daily options history is unavailable.')
    if snap.get('recording_enabled') is False:
        notes.append('Automatic recording watchlist is full; this stock records only when requested.')
    if not any(w.get('ready') for w in windows.values()):
        notes.append('Full intraday recording windows are not yet available.')
    return dict(version=VERSION, available=not issues, source='Sensibull', mode=mode, asof=snap['date'],
                data_time=snap['data_time'], observed_at=now.isoformat(), expiries=snap['expiries'],
                bias=('bullish' if direction > 0 else 'bearish' if direction < 0 else 'mixed' if mixed else 'neutral') if not issues else 'unavailable',
                strength=strength if not issues else 0., mixed=mixed, evidence=evidence, notes=notes, issues=issues,
                spot=snap['spot'], pcr=snap['pcr'], pcr_change=snap['pcr_change'], by_expiry=each,
                levels=levels(snap, spot), rollover=rollover, windows=windows, history=oi_history, futures=fut,
                rule='Heuristic; OI changes score by at most 0.08. No change to model probabilities or payoffs.')


def collect_context(feed, symbol, spot, mode='Live', asof=None):
    if feed is None:
        return dict(version=VERSION, available=False, bias='unavailable', issues=['OI feed is not connected.'])
    try:
        snap = feed.live(symbol)
        windows = {str(m): feed.changes(symbol, m, latest=snap) for m in (5, 15, 30, 60, 180)}
        histories = {}
        for kind in ('oi', 'fut'):
            try:
                histories[kind] = feed.series(symbol, kind)
            except ValueError as exc:
                histories[kind] = {'rows': [], 'error': str(exc)}
        result = build_context(snap, windows, histories, spot, mode, asof)
        for kind, h in histories.items():
            if h.get('error'):
                result['notes'].append(kind+' history: '+h['error'])
        return result
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return dict(version=VERSION, available=False, bias='unavailable', issues=[str(exc)], source='Sensibull')


def apply_context(plan, context):
    """Idempotent contribution, shared by scan, strike edits and paper-entry rechecks."""
    base = plan.get('score_before_oi', plan['score'])
    plan['score_before_oi'] = base
    plan['reasons'] = [r for r in plan['reasons'] if r not in (UNAVAILABLE, CONFLICT, MIXED)]
    adjustment = 0.
    notes = []
    if not context.get('available'):
        plan['reasons'].append(UNAVAILABLE)
        notes.extend(context.get('issues', []))
    else:
        if context.get('mixed'):
            plan['reasons'].append(MIXED)
        bias = context['bias']
        if bias in ('bullish', 'bearish'):
            aligned = plan['bias'] == bias
            adjustment = (.04 if aligned else -.06)*context['strength']
            notes.append('Price-confirmed OI context '+('supports' if aligned else 'does not support')+' this direction.')
            if plan['bias'] in ('bullish', 'bearish') and not aligned:
                plan['reasons'].append(CONFLICT)
        # Only the TRADED expiry can inform short-strike placement. Combined walls are descriptive.
        own = context.get('by_expiry', {}).get(plan['expiry'])
        if own:
            for leg in plan['legs']:
                if leg['qty'] >= 0 or plan['name'] not in ('Put credit spread', 'Call credit spread', 'Iron condor'):
                    continue
                wall = own['support' if leg['type'] == 'PE' else 'resistance']
                if wall:
                    inside = leg['strike'] > wall['strike'] if leg['type'] == 'PE' else leg['strike'] < wall['strike']
                    adjustment += -.02 if inside else (.01 if (wall.get('day_change') or 0) > 0 else 0)
                    notes.append(f"{leg['type']} short {leg['strike']}: {'inside' if inside else 'beyond'} the traded-expiry OI level {wall['strike']}.")
        else:
            notes.append('Traded expiry is outside the two-month OI view; no strike-level adjustment.')
    adjustment = max(-LIMIT, min(LIMIT, adjustment))
    plan['score'] = round(base+adjustment, 5)
    plan['oi'] = dict(version=VERSION, bias=context.get('bias', 'unavailable'), available=context.get('available', False),
                      score_adjustment=round(adjustment, 5), data_time=context.get('data_time'), notes=notes,
                      expiries=context.get('expiries', []))
    return plan
