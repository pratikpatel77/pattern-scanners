"""ADX/DMI power-trend engine (after C. Schaap, "ADXcellence"). Pure functions, no network, no clock.

Every rule reads only bars at or before the bar being judged. The short side reuses the long
rules on a mirrored (negated) price series, so there is exactly one copy of each rule.

Deliberate changes from the book, so the rules are objective:
- Hand-drawn trendlines become the highest high / lowest low of the low-ADX base.
- "Near the 20 EMA" is measured in ATRs, not by eye.
- Breakout entries (ADXtra) need a daily close beyond the base.
"""
from datetime import date

DMI_N, ADX_N = 13, 8      # Schaap's settings
STRONG = 25.              # ADX / DMI level that separates trend from consolidation
BASE_BARS = 30            # low-ADX consolidation needed for ADXodus / ADXtra
BASE_BARS_DUAL = 20       # ... when the weekly ADX is also low (ADXplosive)
BASE_WINDOW = 60          # the base box never looks back further than this
BREAKOUT_LOOK = 20        # a first pullback must come within this many bars of the breakout
TARGET_R = 2.0            # reward target as a multiple of the entry-to-stop risk
MAX_HOLD = 15             # time stop, in bars
STOP_BUFFER = 0.5         # stop sits this many ATRs beyond the pivot
MIN_RISK, MAX_RISK = 0.3, 3.5   # allowed entry-to-stop distance, in ATRs
WARMUP = 80               # bars needed before any rule is judged
RULES_VERSION = 5

SETUPS = ('ADXquisite', 'ADXplosive', 'ADXodus', 'ADXtender', 'ADXpress', 'ADXtra')   # priority order


_SHORT_WORDS = (       # the rules are written for the long side; a short reads them upside down
    ('Daily close above the base', 'Daily close below the base'),
    ('New high', 'New low'), ('new highs', 'new lows'), ('20-day high', '20-day low'), ('last high', 'last low'),
    ('Price pulled back to', 'Price bounced up to'), ('pulled back to', 'bounced up to'),
    ('First pullback held near', 'First bounce stalled near'), ('Pullback held near', 'Bounce stalled near'),
    ('first pullback', 'first bounce'), ('Pullback', 'Bounce'), ('pullback', 'bounce'),
    ('Breakout from', 'Breakdown from'), ('Broke out of', 'Broke down out of'), ('breakout', 'breakdown'),
)


def short_text(text):
    """Rule wording for a short setup: highs become lows and a pullback becomes a bounce."""
    for a, b in _SHORT_WORDS:
        text = text.replace(a, b)
    return text


def wilder(values, n):
    """Wilder's smoothing (alpha = 1/n), seeded with a simple average. Leading values are 0."""
    out = [0.] * len(values)
    if len(values) < n:
        return out
    avg = sum(values[:n]) / n
    out[n - 1] = avg
    for i in range(n, len(values)):
        avg += (values[i] - avg) / n
        out[i] = avg
    return out


def ema(values, n):
    out = [values[0] if values else 0.] * len(values)
    if len(values) < n:
        return out
    avg = sum(values[:n]) / n
    k = 2 / (n + 1)
    for i in range(n):
        out[i] = avg
    for i in range(n, len(values)):
        avg += (values[i] - avg) * k
        out[i] = avg
    return out


def sma(values, n):
    out, total = [], 0.
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        out.append(total / min(i + 1, n))
    return out


def dmi(h, l, c, n=DMI_N, m=ADX_N):
    """(+DMI, -DMI, ADX, ATR14). Outside days credit only the larger move; inside days credit none."""
    size = len(c)
    tr, up, down = [0.] * size, [0.] * size, [0.] * size
    for i in range(1, size):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        a, b = h[i] - h[i - 1], l[i - 1] - l[i]
        if a > b and a > 0:
            up[i] = a
        elif b > a and b > 0:
            down[i] = b
    st, su, sd = wilder(tr[1:], n), wilder(up[1:], n), wilder(down[1:], n)
    pdi, mdi, dx = [0.] * size, [0.] * size, [0.] * size
    for i in range(n, size):
        if st[i - 1] > 0:
            pdi[i], mdi[i] = 100 * su[i - 1] / st[i - 1], 100 * sd[i - 1] / st[i - 1]
        total = pdi[i] + mdi[i]
        dx[i] = 100 * abs(pdi[i] - mdi[i]) / total if total else 0.
    adx = [0.] * n + wilder(dx[n:], m)
    return pdi, mdi, adx, [0.] + wilder(tr[1:], 14)


def dominance(pdi, mdi, start):
    """Schaap's DMI dominance. A line that crosses on top becomes dominant only after a
    crossover high (above 25 and above the other line's preceding peak) or a cross and hold
    (pulls back without crossing, then a new high above 25). Until then the old line rules.
    Returns (dom, since, kind): +1/-1/0 per bar, the bar dominance was won, and 'XH' or 'CH'."""
    size = len(pdi)
    dom, since, kind = [0] * size, [0] * size, [''] * size
    top = state = 0
    when, how = 0, ''
    run_peak = opp_peak = first_peak = 0.
    crossed, won, pulled = start, False, False
    for i in range(start, size):
        cur = 1 if pdi[i] > mdi[i] else -1 if mdi[i] > pdi[i] else top
        line = pdi if cur == 1 else mdi
        v = line[i]
        if cur != top:
            opp_peak, top, run_peak, crossed = run_peak, cur, v, i
            won, pulled, first_peak = cur == state, False, 0.
        else:
            if not won and not pulled and v < line[i - 1]:
                pulled, first_peak = True, run_peak
            run_peak = max(run_peak, v)
        if cur and not won and v > STRONG:
            if v > opp_peak:
                won, how = True, 'XH'
            elif pulled and v > first_peak and i - crossed >= 5:
                won, how = True, 'CH'
            if won:
                state, when = cur, i
        dom[i], since[i], kind[i] = state, when, how
    return dom, since, kind


class Series:
    """One stock's daily bars with every indicator the rules need. `ctx` holds per-bar weekly
    and market context that is already lagged to completed periods."""

    def __init__(self, dates, o, h, l, c, ctx=None, short=False):
        self.dates, self.o, self.h, self.l, self.c, self.short = dates, o, h, l, c, short
        self.n = len(c)
        self.pdi, self.mdi, self.adx, self.atr = dmi(h, l, c)
        self.ema20, self.sma5, self.sma10 = ema(c, 20), sma(c, 5), sma(c, 10)
        self.fast = [max(a, b) for a, b in zip(self.sma5, self.sma10)]
        self.dom, self.dom_since, self.dom_kind = dominance(self.pdi, self.mdi, DMI_N + 1)
        streak, run = [0] * self.n, 0
        for i in range(self.n):
            run = run + 1 if i >= DMI_N + ADX_N and self.adx[i] < STRONG else 0
            streak[i] = run
        self.low_streak = streak
        self.ctx = ctx or {}

    def mirror(self):
        """The same stock upside down: a downtrend here is an uptrend there."""
        neg = lambda xs: [-x for x in xs]
        ctx = dict(self.ctx)
        if ctx:
            ctx['wk_pdi'], ctx['wk_mdi'] = ctx['wk_mdi'], ctx['wk_pdi']
            ctx['wk_ema'] = [None if x is None else -x for x in ctx['wk_ema']]
            ctx['mkt'] = neg(ctx['mkt'])
            ctx['wk_peak_up'], ctx['wk_peak_down'] = ctx['wk_peak_down'], ctx['wk_peak_up']
        return Series(self.dates, neg(self.o), neg(self.l), neg(self.h), neg(self.c), ctx, not self.short)

    def price(self, x):
        return round(-x if self.short else x, 2)


def weekly_context(dates, h, l, c, bench=None, period=None):
    """Weekly ADX/DMI and 20-week EMA for each daily bar, taken from the last COMPLETED week
    (so a daily bar never sees its own week's close). `bench` is (dates, h, l, c) of the index.
    `period` names the bigger timeframe a bar belongs to; the default is its ISO week. The 1-hour
    desk passes the bar's date, which makes the bigger timeframe the last completed DAY."""
    weeks, index = [], []
    for i, d in enumerate(dates):
        key = period(d) if period else d.isocalendar()[:2]
        if not weeks or weeks[-1][0] != key:
            weeks.append([key, h[i], l[i], c[i]])
        else:
            w = weeks[-1]
            w[1], w[2], w[3] = max(w[1], h[i]), min(w[2], l[i]), c[i]
        index.append(len(weeks) - 1)
    wh, wl, wc = [w[1] for w in weeks], [w[2] for w in weeks], [w[3] for w in weeks]
    pdi, mdi, adx, _ = dmi(wh, wl, wc)
    avg = ema(wc, 20)
    ready = DMI_N + ADX_N
    peak = [0.] * len(weeks)      # highest weekly ADX of the last 12 weeks made with +DMI on top (negative: -DMI on top)
    for w in range(len(weeks)):
        ups = [adx[k] for k in range(max(ready, w - 11), w + 1) if pdi[k] > mdi[k]]
        downs = [adx[k] for k in range(max(ready, w - 11), w + 1) if mdi[k] > pdi[k]]
        peak[w] = (max(ups, default=0.), max(downs, default=0.))
    def lag(xs, empty):
        return [xs[w - 1] if w >= 1 and w - 1 >= ready else empty for w in index]
    mkt = [0] * len(dates)
    if bench:
        bd, bh, bl, bc = bench
        bp, bm, _, _ = dmi(bh, bl, bc)
        state, _, _ = dominance(bp, bm, DMI_N + 1)
        by_date = dict(zip(bd, state))
        last = 0
        for i, d in enumerate(dates):
            last = by_date.get(d, last)
            mkt[i] = last
    return dict(wk_adx=lag(adx, None), wk_pdi=lag(pdi, 0.), wk_mdi=lag(mdi, 0.), wk_ema=lag(avg, None),
                wk_peak_up=lag([p[0] for p in peak], 0.), wk_peak_down=lag([p[1] for p in peak], 0.), mkt=mkt)


def build(dates, o, h, l, c, bench=None, period=None):
    """(long view, short view) of one stock."""
    ctx = weekly_context(dates, h, l, c, bench, period)
    up = Series(dates, o, h, l, c, ctx)
    return up, up.mirror()


# ---------- rule helpers (long side; shorts run on the mirrored series) ----------
def pullback(s, i, since, ma=None, near=0.5, hold=1.0, look=6):
    """Pivot-low entry after a retracement to a moving average. The pivot is the lowest low of
    the retracement; the entry trigger is that bar's high, the stop sits just under its low."""
    a = max(since + 1, i - look + 1)
    if a > i:
        return None
    j = min(range(a, i + 1), key=lambda k: (s.l[k], -k))
    ma = ma or s.ema20
    atr = s.atr[j]
    if atr <= 0 or s.l[j] > ma[j] + near * atr or s.c[j] < ma[j] - hold * atr:
        return None
    high = max(range(since, j + 1), key=lambda k: s.h[k])
    if j - high < 2:                       # a real retracement, not the breakout bar itself
        return None
    trigger, stop = s.h[j], s.l[j] - STOP_BUFFER * atr
    if any(s.h[k] > trigger for k in range(j + 1, i)):
        return None                        # already triggered on an earlier bar
    if not MIN_RISK * atr <= trigger - stop <= MAX_RISK * atr:
        return None
    return dict(pivot=j, trigger=trigger, stop=stop, state='triggered' if i > j and s.h[i] > trigger else 'armed',
                swing=s.h[high])


def base_breakout(s, i, look=BREAKOUT_LOOK):
    """Most recent first close above a low-ADX base within `look` bars: (bar, resistance, base length)."""
    for b in range(i, max(WARMUP, i - look), -1):
        length = max(s.low_streak[b - 3:b])
        if length < BASE_BARS_DUAL:
            continue
        res = max(s.h[b - min(length, BASE_WINDOW):b])
        if s.c[b] > res and s.c[b - 1] <= res:
            return b, res, length
    return None


def context(s, i):
    c = s.ctx
    if not c or c['wk_adx'][i] is None:
        return dict(weekly_aligned=False, market_aligned=False, wk_adx=None)
    return dict(weekly_aligned=c['wk_pdi'][i] > c['wk_mdi'][i], market_aligned=c['mkt'][i] > 0,
                wk_adx=round(c['wk_adx'][i], 1))


def signal(s, i, setup, entry, checks, features, note, kind='pullback'):
    risk = entry['trigger'] - entry['stop']
    target = entry['trigger'] + TARGET_R * risk
    return dict(setup=setup, state=entry['state'], kind=kind, bar=i, pivot=entry['pivot'],
                trigger=entry['trigger'], stop=entry['stop'], target=target, swing=entry.get('swing'),
                checks=checks, features=features, note=note)


def detect(s, i, watch=False):
    """Every setup present at bar i, best first. With `watch`, also early-stage rows."""
    if i < WARMUP or i >= s.n or s.atr[i] <= 0:
        return []
    out = []
    ctx = context(s, i)
    dom = s.dom[i] == 1
    won, how = s.dom_since[i], s.dom_kind[i]
    brk = base_breakout(s, i)

    # --- ADXodus / ADXplosive: breakout from a low-ADX base, enter the first pullback to the 20 EMA
    if brk and dom:
        b, res, length = brk
        wk = s.ctx.get('wk_adx', [None] * s.n)[b] if s.ctx else None
        dual = wk is not None and wk < STRONG and ctx['weekly_aligned']
        name = 'ADXplosive' if dual else 'ADXodus' if length >= BASE_BARS else None
        pb = pullback(s, i, b) if name and i > b else None
        base = range(b - min(length, BASE_WINDOW), b)
        if name and pb and s.l[pb['pivot']] >= res - 1.5 * s.atr[b] and max(s.pdi[b:i + 1]) > STRONG:
            j = pb['pivot']
            f = dict(ctx, deep_base=min(s.adx[k] for k in base) < 15,
                     dmi_above_adx=s.pdi[b - 1] > s.adx[b - 1] and s.mdi[b - 1] > s.adx[b - 1],
                     dmi_hold=min(s.pdi[max(b, j - 2):j + 1]) >= 20, wide_separation=s.pdi[j] - s.mdi[j] >= 5,
                     adx_rising=s.adx[i] > s.adx[b], base_bars=length, adx=round(s.adx[i], 1))
            checks = [(f'Low-ADX base of {length} bars before the breakout', True),
                      ('Dominant DMI won by a crossover high or cross and hold', True),
                      ('First pullback held near the 20 EMA', True),
                      ('Both DMI lines were above ADX before the breakout', f['dmi_above_adx']),
                      ('Dominant DMI held near 25 on the pullback', f['dmi_hold']),
                      ('Weekly DMI points the same way', ctx['weekly_aligned'])]
            if dual:
                checks.insert(1, (f'Weekly ADX was also low ({wk:.0f}) at the breakout', True))
            out.append(signal(s, i, name, pb, checks, f, 'Breakout from a long quiet base; entering the first pullback.'))
        elif watch and name and i - b <= 12 and not pb and s.c[i] > res:
            out.append(dict(setup=name, state='watch', bar=i, note='Broke out of its base; wait for the first pullback to the 20 EMA.',
                            level=res, features=dict(ctx, base_bars=length, adx=round(s.adx[i], 1)), checks=[]))

    # --- ADXquisite: weekly power trend, daily correction that looked like a reversal, then dominance returns
    c = s.ctx
    if dom and c and c['wk_adx'][i] is not None and not out and 1 <= i - won <= 15:
        wk_trend = c['wk_peak_up'][i] >= STRONG and c['wk_pdi'][i] > c['wk_mdi'][i]
        corr = [k for k in range(max(WARMUP, won - 30), won) if s.mdi[k] > s.pdi[k]]
        near_wk = min(s.l[max(0, i - 30):i + 1]) <= c['wk_ema'][i] + 2 * s.atr[i]
        pb = pullback(s, i, won) if wk_trend and len(corr) >= 5 and near_wk else None
        if pb:
            j = pb['pivot']
            before = max(s.adx[max(WARMUP, corr[0] - 30):corr[0] + 1])
            f = dict(ctx, deep_base=False, dmi_above_adx=False, dmi_hold=min(s.pdi[max(won, j - 2):j + 1]) >= 20,
                     wide_separation=s.pdi[j] - s.mdi[j] >= 5, adx_rising=s.adx[i] > s.adx[won],
                     countertrend_weaker=max(s.adx[k] for k in corr) < before, adx=round(s.adx[i], 1))
            checks = [(f"Weekly trend is strong (weekly ADX peak {c['wk_peak_up'][i]:.0f})", True),
                      ('Price pulled back to the weekly 20 EMA zone', True),
                      (f'Daily correction lasted {len(corr)} bars with the other DMI on top', True),
                      ('Daily DMI dominance returned to the weekly direction', True),
                      ('Pullback held near the daily 20 EMA', True),
                      ('The correction\'s ADX peak was weaker than the trend\'s', f['countertrend_weaker'])]
            out.append(signal(s, i, 'ADXquisite', pb, checks, f, 'Weekly trend intact; the daily correction has ended.'))

    recent_base = brk is not None
    # --- ADXtender: established ADX > 25 trend, pullback to the 20 EMA with no DMI cross
    if dom and not out and not recent_base and i - won >= 5:
        hi = max(range(i - 25, i + 1), key=lambda k: s.h[k])
        new_high = s.h[hi] >= max(s.h[hi - 20:hi])
        strong = max(s.adx[max(hi - 3, 0):min(hi + 4, i + 1)]) >= STRONG and s.adx[hi] > s.adx[hi - 5]
        pb = pullback(s, i, hi) if new_high and strong and i - hi >= 2 else None
        if pb:
            j = pb['pivot']
            gap = min(s.pdi[k] - s.mdi[k] for k in range(hi, i + 1))
            peak = max(s.adx[hi:i + 1])
            prev = max(range(hi - 40, hi - 4), key=lambda k: s.h[k])
            diverging = s.h[hi] > s.h[prev] and max(s.pdi[hi - 3:hi + 2]) < max(s.pdi[prev - 3:prev + 2]) - 2
            if gap > 0 and s.adx[i] < peak and not diverging:
                f = dict(ctx, deep_base=False, dmi_above_adx=False, dmi_hold=min(s.pdi[j - 2:j + 1]) >= 20,
                         wide_separation=gap >= 5, adx_rising=s.adx[i] > s.adx[i - 1], adx=round(s.adx[i], 1))
                checks = [(f'New high with ADX above 25 (peak {peak:.0f})', True),
                          ('Price pulled back to the 20 EMA', True),
                          ('ADX turned down during the pullback', True),
                          ('The other DMI never crossed on top', True),
                          ('No momentum divergence at the last high', True),
                          ('DMI lines stayed 5+ points apart', gap >= 5),
                          ('Weekly DMI points the same way', ctx['weekly_aligned'])]
                out.append(signal(s, i, 'ADXtender', pb, checks, f, 'Strong trend resting at the 20 EMA.'))
        elif watch and new_high and strong and s.adx[i] >= STRONG and i - hi <= 3:
            out.append(dict(setup='ADXtender', state='watch', bar=i, note='Power trend at new highs; wait for a pullback to the 20 EMA.',
                            level=s.ema20[i], features=dict(ctx, adx=round(s.adx[i], 1)), checks=[]))

    # --- ADXpress: fast breakout, 2-5 bar pause near the 5/10 SMA
    if dom and not out and how == 'XH' and i - won <= 15 and s.adx[i] > 15 and s.adx[i] > s.adx[i - 3]:
        hi = max(range(i - 5, i + 1), key=lambda k: s.h[k])
        pause = range(hi + 1, i + 1)
        if 2 <= len(pause) <= 5 and s.h[hi] >= max(s.h[hi - 20:hi]) and min(s.pdi[k] for k in pause) >= STRONG \
                and max(s.h[k] for k in pause) - min(s.l[k] for k in pause) <= 2 * s.atr[i]:
            pb = pullback(s, i, hi - 1, ma=s.fast, near=0.4, hold=0.6, look=len(pause))
            if pb and pb['pivot'] > hi:
                f = dict(ctx, deep_base=False, dmi_above_adx=False, dmi_hold=True, wide_separation=s.pdi[i] - s.mdi[i] >= 5,
                         adx_rising=True, adx=round(s.adx[i], 1))
                checks = [('20-day high made within the last 5 bars', True), ('Dominant DMI made a crossover high', True),
                          ('Dominant DMI stayed above 25 through the pause', True), ('ADX above 15 and rising', True),
                          (f'{len(pause)}-bar tight pause near the 5/10 SMA', True),
                          ('Weekly DMI points the same way', ctx['weekly_aligned'])]
                out.append(signal(s, i, 'ADXpress', pb, checks, f, 'Fast breakout pausing briefly; no deep pullback expected.'))

    # --- ADXtra: dominance changed inside the base first, then price closes out of the base
    length = max(s.low_streak[i - 3:i])
    if dom and not out and length >= BASE_BARS and i - won >= 1 and i - won <= length:
        res = max(s.h[i - min(length, BASE_WINDOW):i])
        low = min(range(i - 8, i + 1), key=lambda k: s.l[k])
        stop = s.l[low] - STOP_BUFFER * s.atr[i]
        f = dict(ctx, deep_base=min(s.adx[i - min(length, BASE_WINDOW):i]) < 15,
                 dmi_above_adx=s.pdi[i - 1] > s.adx[i - 1] and s.mdi[i - 1] > s.adx[i - 1], dmi_hold=s.pdi[i] >= 20,
                 wide_separation=s.pdi[i] - s.mdi[i] >= 5, adx_rising=s.adx[i] > s.adx[i - 3], base_bars=length, adx=round(s.adx[i], 1))
        near_ema = min(s.l[i - 3:i + 1]) <= s.ema20[i] + s.atr[i]
        if s.c[i] > res and s.c[i - 1] <= res and s.pdi[i] >= max(s.pdi[i - 10:i]) and near_ema \
                and MIN_RISK * s.atr[i] <= s.c[i] - stop <= MAX_RISK * s.atr[i]:
            entry = dict(pivot=low, trigger=s.c[i], stop=stop, state='triggered', swing=res)
            checks = [(f'Low-ADX base of {length} bars', True), ('DMI dominance changed inside the base (cross and hold)', True),
                      ('Price was at the 20 EMA before breaking out', True), ('Daily close above the base', True),
                      ('Dominant DMI made a new high on the breakout', True),
                      ('Both DMI lines above ADX before the breakout', f['dmi_above_adx']),
                      ('Weekly DMI points the same way', ctx['weekly_aligned'])]
            out.append(signal(s, i, 'ADXtra', entry, checks, f, 'Closed out of its base with dominance already established.', kind='close'))
        elif watch and s.c[i] <= res and s.c[i] >= res - 1.5 * s.atr[i] and s.c[i] >= s.ema20[i] - 0.5 * s.atr[i]:
            out.append(dict(setup='ADXtra', state='watch', bar=i, note='Dominance already changed inside the base; needs a daily close beyond the base.',
                            level=res, features=f, checks=[]))
    # No dominant direction: a range, which option structures can trade even though price cannot be followed.
    if watch and not out and not s.short and s.low_streak[i] >= BASE_BARS:
        length = s.low_streak[i]
        window = range(i - min(length, BASE_WINDOW) + 1, i + 1)
        out.append(dict(setup='Base', state='range', bar=i, level=max(s.h[k] for k in window), low=min(s.l[k] for k in window),
                        note=f'Quiet for {length} bars (ADX {s.adx[i]:.0f}): the bulls and bears are evenly matched.',
                        features=dict(ctx, base_bars=length, adx=round(s.adx[i], 1)),
                        checks=[(f'ADX under 25 for {length} bars', True),
                                ('Both DMI lines below 25 (no dominance)', max(s.pdi[i], s.mdi[i]) < STRONG),
                                ('Price inside the base', s.l[i] >= min(s.l[k] for k in window) and s.h[i] <= max(s.h[k] for k in window))]))
    return out
