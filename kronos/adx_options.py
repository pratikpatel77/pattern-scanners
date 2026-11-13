"""Pick the option position that fits an ADX signal, using the live option chain.

Trend setups (one DMI dominant) compare five defined-risk choices: buy an ITM, ATM or OTM option,
a debit spread (the bought option hedged by selling the target strike), and a credit spread (a short
hedged by a wing beyond it). Range setups (ADX under 25, no dominant direction) compare four:
long straddle and strangle when options are cheap, iron fly and iron condor when they are expensive.

Naked short options are never offered. A short call's loss is open-ended and a short put's is close to
it, so every structure here is hedged and its worst case is a known number.

Judging is per signal, so the figures compare like with like:
- Trend: what the position makes if the stock reaches its target against what it loses at the stop.
- Range: the stock sitting still and moving one expected move either way, weighted, against its risk.
Both are Black-Scholes estimates about a week ahead at today's implied volatility, not quotes.
"""
import math

from .options_math import bs, fee

MIN_DTE = 10              # calendar days; a nearer expiry is only used when nothing better is listed
HOLD_DAYS = 7             # calendar days ahead at which a trend trade's outcomes are estimated
RANGE_DAYS = 21           # a range trade is held far longer: its profit is the premium decaying
RICH_IV, CHEAP_IV = 1.3, 1.0   # implied / realised volatility
MAX_SPREAD = 0.12         # a leg's bid-ask spread as a share of its mid price
MIN_OI = 100              # open interest a leg needs before it may be used
MIN_STRIKES = 6           # tradable strikes near the money an expiry needs
# Range setups are scored over five outcomes, from the stock sitting still to a move of twice the
# expected size, weighted roughly as a normal distribution spreads them. Three points alone would
# over-weight the stock being exactly unchanged, which flatters sold structures and punishes bought ones.
OUTCOMES = ((0., .38), (1., .235), (-1., .235), (2., .075), (-2., .075))


def realised_vol(closes, n=20):
    """Annualised close-to-close volatility in %, or None."""
    xs = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, len(closes) - n), len(closes)) if closes[i - 1] > 0 and closes[i] > 0]
    if len(xs) < 10:
        return None
    mean = sum(xs) / len(xs)
    return round(math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1) * 252) * 100, 1)


def expected_move(spot, vol, days):
    """One standard deviation of stock movement over `days`, in rupees.

    This uses the stock's own recent volatility, not the implied volatility in the options. That is
    what makes buying and selling volatility different: when options imply more movement than the
    stock delivers, a sold structure wins the comparison, and when they imply less, a bought one does."""
    return spot * (vol or 30.) / 100 * math.sqrt(max(days, 1) / 365)


def days_needed(sig, atr):
    """Rough calendar days for the stock to travel from its trigger to its target at its own ATR.
    A trending stock nets well under one ATR a day, so this is deliberately generous."""
    if not atr or atr <= 0 or sig.get('target') is None:
        return MIN_DTE
    moves = abs(sig['target'] - sig['trigger']) / atr
    return int(min(60, max(MIN_DTE, moves / .4 * 1.4 + 3)))


def leg_problem(option):
    bid, ask = option.get('bid') or 0., option.get('ask') or 0.
    if bid <= 0 or ask < bid:
        return 'no two-sided quote', True
    if (option.get('oi') or 0) < MIN_OI:
        return f"open interest under {MIN_OI}", True
    if (ask - bid) / ((ask + bid) / 2) > MAX_SPREAD:
        return 'wide bid-ask spread', False
    return None, False


def chain_read(chain, hv=None, horizon=None):
    """What the option chain itself is saying: volatility, open-interest walls, skew, liquidity."""
    rows = [r for r in chain.get('rows') or [] if r.get('CE') and r.get('PE')]
    spot, iv = chain['spot']['ltp'], chain.get('atm_iv')
    oi = lambda r, t: r[t].get('oi') or 0
    # A call wall is resistance above the stock and a put wall is support below it.
    call_wall = max([r for r in rows if r['strike'] >= spot], key=lambda r: oi(r, 'CE'), default=None)
    put_wall = max([r for r in rows if r['strike'] <= spot], key=lambda r: oi(r, 'PE'), default=None)
    at = min(range(len(rows)), key=lambda k: abs(rows[k]['strike'] - spot)) if rows else 0
    near = rows[max(0, at - 6):at + 7]
    tradable = [r for r in near if not leg_problem(r['CE'])[1] or not leg_problem(r['PE'])[1]]
    ratio = round(iv / hv, 2) if iv and hv else None
    horizon = horizon or min(chain.get('days') or HOLD_DAYS, HOLD_DAYS)
    em = expected_move(spot, hv or iv, horizon)        # what the stock does
    im = expected_move(spot, iv, horizon)              # what the options are priced for
    skew = None
    up, down = rows[min(at + 3, len(rows) - 1)] if rows else None, rows[max(at - 3, 0)] if rows else None
    if up and down and up['CE'].get('iv') and down['PE'].get('iv'):
        skew = round(down['PE']['iv'] - up['CE']['iv'], 1)
    facts = []
    if ratio is not None:
        facts.append(('Implied vs recent volatility', f"{iv:.0f}% vs {hv:.0f}% ({ratio}x) — " +
                      ('options are expensive' if ratio >= RICH_IV else 'options are cheap' if ratio <= CHEAP_IV else 'fairly priced')))
    if call_wall and put_wall:
        facts.append(('Open-interest walls', f"{put_wall['strike']:,.0f} below, {call_wall['strike']:,.0f} above — the range the chain is positioned for"))
    if chain.get('pcr'):
        facts.append(('Put-call ratio', f"{chain['pcr']:.2f} — more " + ('puts' if chain['pcr'] > 1 else 'calls') + ' open'))
    if chain.get('max_pain'):
        facts.append(('Max pain', f"{chain['max_pain']:,.0f}"))
    if skew is not None:
        facts.append(('Skew', f"{skew:+.1f} — downside options are " + ('dearer' if skew > 1 else 'cheaper' if skew < -1 else 'level with upside')))
    facts.append(('Expected move', f"±{em:,.0f} ({em / spot * 100:.1f}%) over {horizon:.0f} days, from the stock's own recent movement"))
    facts.append(('Tradable strikes near the money', f"{len(tradable)} of {len(near)}"))
    return dict(iv=iv, hv=hv, iv_hv=ratio, em=round(em, 2), im=round(im, 2), skew=skew, pcr=chain.get('pcr'), max_pain=chain.get('max_pain'),
                call_wall=call_wall['strike'] if call_wall else None, put_wall=put_wall['strike'] if put_wall else None,
                liquid=len(tradable) >= MIN_STRIKES, tradable=len(tradable), facts=facts)


def breakevens(legs, paid, hi):
    """Exact break-even stock prices at expiry. The payoff is linear between strikes."""
    value = lambda s: sum(l['qty'] * max(0., s - l['strike'] if l['type'] == 'CE' else l['strike'] - s) for l in legs) - paid
    points = sorted({0., hi} | {l['strike'] for l in legs})
    out = set()
    for a, b in zip(points, points[1:]):
        fa, fb = value(a), value(b)
        if fa == 0:
            out.add(a)
        elif fa * fb < 0:
            out.add(a + (b - a) * abs(fa) / (abs(fa) + abs(fb)))
    return sorted(round(x, 2) for x in out)


def position(name, kind, parts, chain, left):
    """Price one structure. parts: [(chain row, 'CE'|'PE', +1 buy / -1 sell)]. None if it cannot be used."""
    lot, atm_iv = chain['lot'], chain.get('atm_iv') or 30.
    legs, warnings = [], []
    for row, typ, qty in parts:
        o = row[typ]
        side = o.get('ask') if qty > 0 else o.get('bid')
        problem, fatal = leg_problem(o)
        if fatal or not side or side <= 0:
            return None                                   # never build on a leg that cannot be traded
        if problem:
            warnings.append(f"{int(row['strike'])} {typ}: {problem}")
        legs.append(dict(strike=row['strike'], type=typ, qty=qty, price=float(side), symbol=o.get('symbol'), iv=o.get('iv') or atm_iv,
                         spread=max((o.get('ask') or 0.) - (o.get('bid') or 0.), 0.)))
    if len({(l['strike'], l['type']) for l in legs}) != len(legs):
        return None
    paid = sum(l['qty'] * l['price'] for l in legs)        # positive = debit paid, negative = credit received
    strikes = [l['strike'] for l in legs]
    hi = max(strikes) * 2
    ends = [sum(l['qty'] * max(0., k - l['strike'] if l['type'] == 'CE' else l['strike'] - k) for l in legs) - paid
            for k in strikes + [0., hi]]
    open_ended = any(l['qty'] > 0 and l['type'] == 'CE' for l in legs) and sum(l['qty'] for l in legs if l['type'] == 'CE') > 0
    max_loss = round(-min(ends) * lot, 2)
    max_profit = None if open_ended else round(max(ends) * lot, 2)
    if max_loss <= 0 or (max_profit is not None and max_profit <= 0):
        return None
    return dict(name=name, kind=kind, expiry=chain.get('expiry_date'), lot=lot, legs=legs, net=round(-paid, 2),
                cost=round(abs(paid) * lot, 2), max_loss=max_loss, max_profit=max_profit,
                breakevens=breakevens(legs, paid, hi), warnings=warnings,
                value=lambda spot, iv=1.: round((sum(l['qty'] * bs(spot, l['strike'], left, l['iv'] * iv, l['type']) for l in legs) - paid) * lot, 2))


def round_trip_cost(idea, per_order=20.):
    """Estimated rupees to open and close one lot of a position: brokerage and statutory charges on both
    trades, plus half of each leg's bid-ask spread (the exit is priced at the middle, but filled at the bid or ask)."""
    buy = sum(l['price'] * idea['lot'] * l['qty'] for l in idea['legs'] if l['qty'] > 0)
    sell = sum(l['price'] * idea['lot'] * -l['qty'] for l in idea['legs'] if l['qty'] < 0)
    # Closing reverses every leg, so what was bought is sold and what was sold is bought back.
    charges = fee(buy + sell, sell + buy, 2 * len(idea['legs']), per_order)
    return charges + sum(l['spread'] / 2 * idea['lot'] * abs(l['qty']) for l in idea['legs'])


def trend_ideas(rows, at, sig, chain, read):
    """Five choices for a stock with a dominant DMI."""
    bull = sig['direction'] == 'bullish'
    step, typ, other = (1, 'CE', 'PE') if bull else (-1, 'PE', 'CE')
    word, strikes = ('call' if bull else 'put'), [r['strike'] for r in rows]
    out = []

    def add(name, kind, parts):
        if all(0 <= k < len(rows) for k, _, _ in parts):
            out.append((name, kind, [(rows[k], t, q) for k, t, q in parts]))

    add(f'Buy ITM {word}', 'long', [(at - 2 * step, typ, 1)])
    add(f'Buy ATM {word}', 'long', [(at, typ, 1)])
    add(f'Buy OTM {word}', 'long', [(at + 2 * step, typ, 1)])
    far = min(range(len(rows)), key=lambda k: abs(strikes[k] - sig['target']))
    if (far - at) * step < 1:
        far = at + step
    add(f'{word.capitalize()} debit spread', 'debit', [(at, typ, 1), (far, typ, -1)])
    beyond = [k for k in range(len(rows)) if (strikes[k] <= sig['stop'] if bull else strikes[k] >= sig['stop'])]
    if beyond:
        near = max(beyond) if bull else min(beyond)
        wing = near - step
        if 0 <= wing < len(rows) and abs(strikes[wing] - strikes[near]) < 0.01 * chain['spot']['ltp'] and 0 <= wing - step < len(rows):
            wing -= step                                  # one strike is too narrow to be worth the charges
        add(f"{'Put' if bull else 'Call'} credit spread", 'credit', [(wing, other, 1), (near, other, -1)])
    return out


def range_ideas(rows, at, chain, read):
    """Four choices for a stock with no dominant direction: two that buy movement, two that sell it."""
    spot, em, strikes = chain['spot']['ltp'], read['em'], [r['strike'] for r in rows]
    near = lambda price: min(range(len(rows)), key=lambda k: abs(strikes[k] - price))
    ok = lambda k: 0 <= k < len(rows)
    out = []

    def add(name, kind, parts):
        if all(ok(k) for k, _, _ in parts):
            out.append((name, kind, [(rows[k], t, q) for k, t, q in parts]))

    up, down = max(near(spot + em), at + 1), min(near(spot - em), at - 1)
    add('Long straddle', 'long_vol', [(at, 'CE', 1), (at, 'PE', 1)])
    add('Long strangle', 'long_vol', [(up, 'CE', 1), (down, 'PE', 1)])
    add('Iron fly', 'short_vol', [(at, 'CE', -1), (at, 'PE', -1), (up, 'CE', 1), (down, 'PE', 1)])

    def wall(level, default):
        """An open-interest wall becomes a short strike only when it sits one to 2.5 expected moves
        away: nearer and the stock reaches it in normal trade, further and there is nothing to collect."""
        return level if level and em and 1 <= abs(level - spot) / em <= 2.5 else default

    # Clamped so the protective wings two strikes further out are always listed.
    short_call = min(max(near(wall(read['call_wall'], spot + em)), at + 1), len(rows) - 3)
    short_put = max(min(near(wall(read['put_wall'], spot - em)), at - 1), 2)
    add('Iron condor', 'short_vol', [(short_call, 'CE', -1), (short_put, 'PE', -1),
                                     (short_call + 2, 'CE', 1), (short_put - 2, 'PE', 1)])
    return out


def plan(sig, chain, hv=None, learned=None, event=None, hold_days=HOLD_DAYS, range_days=RANGE_DAYS, time_exit='after 21 days',
         costs=False, iv_stress=0., min_edge=0.):
    """Every usable position for one signal, best first, each with the reason it was or was not picked.
    `hold_days`, `range_days` and `time_exit` describe how long the desk holds a trade: the 1-hour desk
    holds for a couple of sessions, so its outcomes are estimated nearer in time.
    With `costs`, every outcome is after the estimated round-trip charges and exit half-spread. With
    `iv_stress`, each outcome is the worst of implied volatility that much lower, unchanged and higher.
    A trend position must also return at least `min_edge` times its loss at the stop to be picked."""
    rows = [r for r in chain.get('rows') or [] if r.get('CE') and r.get('PE')]
    if len(rows) < 6 or not chain.get('lot') or not chain.get('days'):
        return dict(ideas=[], best=None, chain=None, notes=['The option chain is too thin to build a position.'], exits=[], case_labels=[])
    neutral = sig['direction'] == 'neutral'
    hold = min(range_days if neutral else hold_days, max(1., chain['days'] - 3))
    left = max(chain['days'] - hold, 0.5)
    read = chain_read(chain, hv, hold)
    spot, em = chain['spot']['ltp'], read['em']
    at = min(range(len(rows)), key=lambda k: abs(rows[k]['strike'] - spot))
    specs = range_ideas(rows, at, chain, read) if neutral else trend_ideas(rows, at, sig, chain, read)
    if neutral:
        cases = [('Flat', spot), ('Up one move', spot + em), ('Down one move', spot - em)]
    else:
        cases = [('At target', sig['target']), ('At stop', sig['stop']), ('Flat', spot)]

    # A scheduled event inside the trade is a gap waiting to happen: it rewards owning options and
    # punishes having sold them, whatever the volatility reading says.
    event_tilt = dict(short_vol=.5, credit=.7, long_vol=1.3) if event else {}
    # Range setups need no volatility tilt: their cases are already measured against what the stock
    # actually moves, so an over-priced option shows up as a loss in the figures themselves.
    tilt = {}
    if not neutral and read['iv_hv'] is not None and read['iv_hv'] >= RICH_IV:
        tilt = dict(long=.7, credit=1.25)
    elif not neutral and read['iv_hv'] is not None and read['iv_hv'] <= CHEAP_IV:
        tilt = dict(long=1.15, debit=1.1, credit=.8)

    made = []
    for name, kind, parts in specs:
        idea = position(name, kind, parts, chain, left)
        if not idea:
            continue
        raw = idea.pop('value')
        cost = round(round_trip_cost(idea), 2) if costs else 0.
        idea['round_trip_cost'] = cost
        mults = (1. - iv_stress, 1., 1. + iv_stress) if iv_stress else (1.,)
        value = lambda price: min(raw(price, m) for m in mults) - cost
        idea['cases'] = [round(value(price), 2) for _, price in cases]
        if neutral:
            gain = sum(w * value(spot + k * em) for k, w in OUTCOMES)
            idea['edge'] = round(gain / idea['max_loss'] * 100, 1)
            idea['headline'] = f"{idea['edge']:+.0f}% of risk"
            base = max(idea['edge'] / 20, 0.)
        else:
            target, stop, _ = idea['cases']
            loss = -stop
            idea['edge'] = round(target / loss, 2) if loss > 0 and target > 0 else 0.
            idea['headline'] = f"{idea['edge']} to 1" if idea['edge'] else 'no gain at target'
            base = idea['edge']
        idea['score'] = round(base * tilt.get(idea['kind'], 1.) * event_tilt.get(idea['kind'], 1.)
                              * (learned or {}).get(idea['kind'], 1.) * (.6 if idea['warnings'] else 1.), 3)
        made.append(idea)
    made.sort(key=lambda x: -x['score'])
    eligible = [x for x in made if x['score'] > 0 and (neutral or x['edge'] >= min_edge)]
    best = eligible[0] if eligible else None

    notes = []
    if costs:
        notes.append(f"Every figure is after estimated round-trip charges and half the bid-ask spread on exit"
                     + (f", and is the worst of implied volatility {iv_stress * 100:.0f}% lower, unchanged or {iv_stress * 100:.0f}% higher." if iv_stress else '.'))
    if read['iv_hv'] is not None and read['iv_hv'] >= RICH_IV:
        notes.append(f"Options are expensive ({read['iv']:.0f}% implied against {read['hv']:.0f}% recent), so selling premium is marked up and buying it is marked down.")
    elif read['iv_hv'] is not None and read['iv_hv'] <= CHEAP_IV:
        notes.append(f"Options are cheap ({read['iv']:.0f}% implied against {read['hv']:.0f}% recent), so buying premium is marked up and selling it is marked down.")
    if not neutral and best and read['call_wall'] and read['put_wall']:
        wall = read['call_wall'] if sig['direction'] == 'bullish' else read['put_wall']
        if (sig['target'] - wall) * (1 if sig['direction'] == 'bullish' else -1) > 0:
            notes.append(f"The biggest open interest on the way sits at {wall:,.0f}, before the {sig['target']:,.2f} target: the move may stall there.")
    if event:
        notes.append(f"{event} falls inside this trade: sold options are marked down and bought options up, because a result can gap the stock straight through a short strike.")
    if learned:
        for kind, factor in learned.items():
            notes.append(f"Your own closed ADX trades of this type {'made' if factor > 1 else 'lost'} money, so {kind.replace('_', ' ')} positions are marked {'up' if factor > 1 else 'down'}.")
    for x in made:
        if x is best:
            if neutral:
                x['why'] = (f"Best use of this range: about {x['edge']:+.0f}% of the ₹{x['max_loss']:,.0f} at risk, weighing the stock "
                            f"staying put against a move either way.")
            else:
                x['why'] = (f"Best balance for this plan: about ₹{x['cases'][0]:,.0f} if the stock reaches the target against "
                            f"about ₹{-x['cases'][1]:,.0f} if it hits the stop ({x['edge']} to 1).")
        elif not best:
            x['why'] = 'Nothing here is worth trading on this chain.'
        elif x['score'] <= 0:
            x['why'] = 'Does not pay off even in its good case.'
        elif x not in eligible:
            x['why'] = f"After costs it returns {x['edge']} times its loss at the stop; this desk needs at least {min_edge}."
        else:
            bits = [f"{x['headline']} against {best['headline']} for the pick"]
            if not neutral and x['cases'][2] < best['cases'][2] - 1:
                bits.append(f"loses ₹{-x['cases'][2]:,.0f} if the stock goes nowhere")
            if x['max_loss'] > best['max_loss'] * 1.3:
                bits.append(f"risks more (₹{x['max_loss']:,.0f})")
            if x['warnings']:
                bits.append('liquidity concern')
            x['why'] = 'Weaker: ' + ', '.join(bits) + '.'

    if neutral:
        exits = [f"Breakout: a close outside {sig['low']:,.2f} – {sig['high']:,.2f} ends the range. " +
                 ('Book the profit, the move has happened.' if (best or {}).get('kind') == 'long_vol' else 'Exit, the range trade has failed.'),
                 'Loss stop: exit if the position is down half of what you put at risk.',
                 'Take profit at 50% of the maximum, or once ADX rises above 25 and a trend starts.',
                 'Time: exit 3 days before expiry; options lose value fastest in the last week.']
    else:
        side = 'below' if sig['direction'] == 'bullish' else 'above'
        exits = [f"Stop loss: exit if the stock trades {side} {sig['stop']:,.2f}." +
                 (f" The position should then be down about ₹{-best['cases'][1]:,.0f}." if best else ''),
                 f"Target: book profit at {sig['target']:,.2f}, or most of it once 80% of the way there.",
                 f"Exit if the {'−DMI' if sig['direction'] == 'bullish' else '+DMI'} takes dominance on the daily chart.",
                 f'Time: exit {time_exit}, or 3 days before expiry, whichever comes first.']
    return dict(ideas=made, best=best['name'] if best else None, chain=read, case_labels=[c[0] for c in cases],
                days=round(chain['days'], 1), hold_days=round(hold), expiry=chain.get('expiry_date'), spot=spot,
                neutral=neutral, notes=notes, exits=exits, left=round(left, 2), case_prices=[c[1] for c in cases])
