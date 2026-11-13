"""ADX trend desk: ADX/DMI analysis of F&O stocks, the option position that fits each setup, and a
record that learns from your own ADX paper trades. Nothing here reads a Kronos forecast.

Flow: scan daily bars -> setups (Enter / Armed / Range / Watch) -> pick the expiry -> compare the
option structures -> paper trade -> exit guidance from the stock itself -> the closed trade feeds back."""
from datetime import datetime, timedelta
import json
import threading
import time

from . import adx_brain as brain
from . import adx_core as core
from .adx_options import MIN_DTE, chain_read, days_needed, plan, realised_vol
from .options_math import bs, fee
from .options_market import clock, market_open

QUOTE_TTL = 8.0
AUTO_EVERY = 600        # seconds between automatic rescans when the saved bars change
LEARN_MIN = 5           # closed trades of one type before the record changes a ranking
MAX_DAYS = 21           # calendar days before a trade is called stale
WATCH_SECONDS = 20      # how often the background watcher re-checks open trades and triggers in market hours
ALERT_LOG = 300         # alerts kept
KINDS = ('long', 'debit', 'credit', 'long_vol', 'short_vol')


AUTO_RECHECK = 300      # seconds before the desk looks again at an Enter setup it declined to trade


class Declined(ValueError):
    """The desk's own rules refuse this entry (not triggered, stale data, a Pass call, too little time).
    Unlike a failed fill it is not an error: the setup is simply not taken, and may be taken later."""


def encode(value):
    return json.dumps(value, allow_nan=False)


def row_of(symbol, direction, s, sig):
    """A signal in real prices."""
    i = sig['bar']
    passed = sum(bool(ok) for _, ok in sig['checks'])
    words = core.short_text if s.short else str
    out = dict(symbol=symbol, direction=direction, setup=sig['setup'], state=sig['state'], note=words(sig['note']),
               bar_date=str(s.dates[i]), close=s.price(s.c[i]), adx=round(s.adx[i], 1),
               pdi=round(s.mdi[i] if s.short else s.pdi[i], 1), mdi=round(s.pdi[i] if s.short else s.mdi[i], 1),
               checks=[[words(text), bool(ok)] for text, ok in sig['checks']], features=dict(sig['features']),
               quality=round(passed / len(sig['checks']) * 100) if sig['checks'] else 0)
    if sig['state'] == 'range':
        out.update(id=f'{symbol}|Base|neutral|range', direction='neutral',
                   high=s.price(sig['level']), low=s.price(sig['low']))
        return out
    if sig['state'] == 'watch':
        out.update(id=f"{symbol}|{sig['setup']}|{direction}|watch", level=s.price(sig['level']))
        return out
    risk = sig['trigger'] - sig['stop']
    out.update(id=f"{symbol}|{sig['setup']}|{direction}|{s.dates[sig['pivot']]}", trigger=s.price(sig['trigger']),
               stop=s.price(sig['stop']), target=s.price(sig['target']), risk_pct=round(risk / abs(sig['trigger']) * 100, 2))
    return out


def record(trades):
    """Closed-trade summary. Return is P&L as a share of the planned maximum loss."""
    done = [t for t in trades if t['status'] == 'closed' and t['pnl'] is not None]
    wins = [t for t in done if t['pnl'] > 0]
    rets = [t['pnl'] / t['max_loss'] for t in done if t['max_loss']]
    return dict(n=len(done), wins=len(wins), win_rate=round(len(wins) / len(done) * 100) if done else None,
                pnl=round(sum(t['pnl'] for t in done), 2), avg_return=round(sum(rets) / len(rets) * 100, 1) if rets else None)


class AdxDesk:
    # What makes this the DAILY desk. The 1-hour desk (adx_hour.py) is the same desk with these changed.
    SOURCE = 'adx'            # journal tag of this desk's paper trades: each desk sees only its own
    TITLE = 'ADX'             # first word of a trade's name
    KEY = ''                  # prefix of this desk's rows in the adx_cache table
    PERIOD = None             # the bigger timeframe a bar belongs to (None: its week)
    PER_YEAR = 252            # bars in a year
    PLAN = {}                 # holding-period arguments for adx_options.plan
    RESCAN_EVERY = AUTO_EVERY
    RETRY = False             # rescan by itself again after a failed scan
    AUTO = False              # open a paper trade by itself when a setup turns Enter
    AUTO_TRIES, AUTO_WAIT = 3, 60.   # attempts per setup, and seconds between them
    STRICT = False            # every entry, manual or automatic, needs current bars, a triggered setup and a live price
    FLOOR_DAYS = 0            # fewest calendar days an expiry may have left when a trade is opened
    MAX_OPEN = None           # most open trades at once
    MIN_EDGE = 0.             # with real fills, a trend trade must return this many times its loss at the stop (0: not checked)
    IV_STRESS = 0.            # share by which implied volatility is moved when judging a trade's outcomes

    def __init__(self, service):
        self.service = service
        self.auto_tries, self.auto_errors = {}, {}   # {signal id: (attempts, last attempt)}, {signal id: why it failed}
        self.auto_hold, self.auto_skips = {}, {}     # {signal id: do not look again before}, {signal id: why it was declined}
        self.lock = threading.Lock()
        self.job = dict(running=False, progress=0, message='', error='')
        self.quotes = {}        # {symbol: (fetched_at, quote)}; each symbol has its own freshness
        self.auto = 0.
        self.seen, self.told = None, {}       # last verdict per setup and last action per trade, for alerts
        self.stop_event, self.thread = threading.Event(), None
        self.trade_lock = threading.Lock()    # serialises the high-water read-modify-write below
        with service.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS adx_cache (key TEXT PRIMARY KEY, updated TEXT NOT NULL, payload TEXT NOT NULL)')

    # ---------- data ----------
    def _stamp(self):
        market = self.service.market
        return '|'.join([str(core.RULES_VERSION)] + [str(p.stat().st_mtime_ns if p.exists() else 0) for p in (market.path, market.live_path)])

    def _arrays(self, symbol):
        frame = self.service.market.daily(symbol)
        return ([t.date() for t in frame.index], *(frame[c].astype(float).tolist() for c in ('open', 'high', 'low', 'close')))

    def _bench(self):
        try:
            d, _, h, l, c = self._arrays('__NIFTY50__')
            return d, h, l, c
        except ValueError:
            return None

    def _views(self, symbol, bench):
        d, o, h, l, c = self._arrays(symbol)
        if len(c) < core.WARMUP + 20:
            raise ValueError('Too little price history.')
        return core.build(d, o, h, l, c, bench, self.PERIOD)

    def _day_closes(self, up):
        """Daily closes, for the stock's realised volatility."""
        return up.c

    def _days_wanted(self, sig, atr):
        """Calendar days the expiry must still have for this trade."""
        return MIN_DTE if sig['direction'] == 'neutral' else days_needed(sig, atr)

    def _expected(self):
        """The bar the saved data should reach by now."""
        today = clock().date()
        expected = today if clock().hour >= 16 and today.weekday() < 5 else today - timedelta(days=1)
        while expected.weekday() >= 5:
            expected -= timedelta(days=1)
        return str(expected)

    def _after(self, up, created):
        """(high, low) of every bar that lies wholly after the trade was opened. A daily bar spans the
        whole session, so the entry day's own bar is left out."""
        since = datetime.fromisoformat(created).date()
        return [(up.h[k], up.l[k]) for k in range(up.n) if up.dates[k] > since]

    def _timed_out(self, age, path):
        return f'Exit: held {age} days without reaching the plan.' if age >= MAX_DAYS else None

    def _behind(self, saved):
        """True when the saved scan is older than the newest bar it should hold, so it must be redone."""
        return False

    def _gap(self):
        """Seconds between automatic rescans."""
        return self.RESCAN_EVERY

    def _save(self, key, value):
        now = clock().isoformat()
        with self.service.store.connect() as db:
            db.execute('INSERT INTO adx_cache VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET updated=excluded.updated, payload=excluded.payload',
                       (self.KEY + key, now, encode(value)))

    def view(self, name, *args):
        """What the page reads. The 1-hour desk rewords the result for its own timeframe here, once."""
        return getattr(self, name)(*args)

    def universe(self):
        have = set(self.service.market.symbols())
        return sorted(s for s in self.service.desk._fno_symbols() if s in have)

    # ---------- scan ----------
    def start(self):
        with self.lock:
            if self.job['running']:
                return dict(self.job)
            self.job = dict(running=True, progress=0, message='Starting', error='')
        threading.Thread(target=self._scan, name='adx-scan', daemon=True).start()
        return dict(self.job)

    def _scan(self):
        try:
            symbols = self.universe()
            if not symbols:
                raise ValueError('No F&O stocks with saved price history. Run the scanner to load market data.')
            stamp, bench = self._stamp(), self._bench()
            rows, seen, done, last, tally = [], set(), 0, '', []
            index_move = bench[3][-1] / bench[3][-21] - 1 if bench and len(bench[3]) > 21 else None
            for n, symbol in enumerate(symbols):
                self.job.update(progress=round(n / len(symbols) * 100), message=f'{symbol} ({n + 1}/{len(symbols)})')
                try:
                    up, down = self._views(symbol, bench)
                except ValueError:
                    continue
                done += 1
                last = max(last, str(up.dates[-1]))
                tally.append((str(up.dates[-1]), up.dom[-1], up.adx[-1] >= core.STRONG, up.c[-1] > up.ema20[-1]))
                strength = round((up.c[-1] / up.c[-21] - 1 - index_move) * 100, 1) if index_move is not None and up.c[-21] > 0 else None
                for direction, s in (('bullish', up), ('bearish', down)):
                    for sig in core.detect(s, s.n - 1, watch=True):
                        row = row_of(symbol, direction, s, sig)
                        if row['id'] in seen:
                            continue          # a quiet base looks the same from both sides: keep one neutral row
                        seen.add(row['id'])
                        row['features']['rs20'] = strength
                        rows.append(row)
            rows = [r for r in rows if r['bar_date'] == last]      # a stock with old bars cannot give today's signal
            now = [t for t in tally if t[0] == last]
            share = lambda test: round(sum(1 for t in now if test(t)) / len(now) * 100) if now else 0
            breadth = dict(n=len(now), up=share(lambda t: t[1] == 1), down=share(lambda t: t[1] == -1),
                           trending=share(lambda t: t[2]), above=share(lambda t: t[3]))
            market = brain.market_state(bench, breadth, self.PER_YEAR)
            self._save('board', dict(stamp=stamp, rows=rows, data_date=last, symbols=done, time=clock().isoformat(), market=market))
            self.job.update(running=False, progress=100, message='Complete')
        except Exception as exc:
            safe = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: the ADX scan could not finish.'
            self.job.update(running=False, error=safe, message=safe)

    def _saved(self, key='board'):
        with self.service.store.connect() as db:
            row = db.execute('SELECT payload FROM adx_cache WHERE key=?', (self.KEY + key,)).fetchone()
        return json.loads(row[0]) if row else None

    # ---------- ADX paper trades ----------
    def _trades(self):
        """Every paper trade opened from this tab, newest first, flattened for the page and the record."""
        out, exits = [], {}
        for a in self._saved('alerts') or []:
            if a['kind'] == 'trade' and a['action'] == 'exit':
                exits.setdefault(a['id'], a)          # the first Exit alert raised for each trade
        for t in self.service.options.journal.paper():
            p = t['payload']
            if p.get('source') != self.SOURCE or 'adx' not in p:
                continue
            a, mark = p['adx'], p.get('mark')
            out.append(dict(id=t['id'], status=t['status'], created=t['created'], updated=t['updated'], symbol=p['symbol'],
                            signal=a.get('signal'), auto=bool(a.get('auto')), override=a.get('override'), breach=a.get('breach'),
                            setup=a['setup'], direction=a['direction'], kind=a['kind'], name=p['plan']['name'],
                            trigger=a.get('trigger'), stop=a.get('stop'), target=a.get('target'),
                            low=a.get('low'), high=a.get('high'), entry_spot=p.get('entry_spot'),
                            expiry=p['plan']['expiry'], max_loss=p['plan'].get('max_loss'), max_profit=p['plan'].get('max_profit'),
                            pnl=mark['net_pnl'] if mark else None, marked=mark['time'] if mark else None,
                            planned_loss=a.get('planned_loss'), planned_gain=a.get('planned_gain'),
                            tags=a.get('tags'), score=a.get('score'), marks=p.get('marks') or [],
                            legs=[dict(strike=l['strike'], type=l['type'], qty=l['qty'], fill=l.get('entry_fill'),
                                       symbol=l['symbol'], lot=l['lot']) for l in p['legs']]))
            if t['status'] == 'closed':
                out[-1]['review'] = brain.review(out[-1], exits.get(t['id']))
        return out

    def _lessons(self, trades):
        return brain.lessons([t for t in trades if t['status'] == 'closed'])

    def _learned(self, trades, setup):
        """{kind: score multiplier} for position types with enough of your own closed trades in this setup."""
        out = {}
        for kind in KINDS:
            rec = record([t for t in trades if t['setup'] == setup and t['kind'] == kind])
            if rec['n'] >= LEARN_MIN and rec['pnl']:
                out[kind] = 1.2 if rec['pnl'] > 0 else 0.7
        tilt = self._lessons(trades)['policy']['long_tilt']
        if tilt != 1:
            out['long'] = round(out.get('long', 1.) * tilt, 3)   # bought options that lost although the stock was right
        return out

    def _live_quotes(self, symbols):
        """Live quotes for exactly the symbols asked for. Cached per symbol, not as one blob for
        whichever request happened to run first -- board() and trades() ask for different, overlapping
        sets of symbols moments apart, and a shared single-shot cache would silently serve one request's
        symbols to the other, leaving the rest to fall back to yesterday's close."""
        now, symbols = time.monotonic(), sorted(set(symbols))
        missing = [s for s in symbols if s not in self.quotes or now - self.quotes[s][0] >= QUOTE_TTL]
        for k in range(0, len(missing), 50):
            try:
                for sym, q in self.service.prices.quotes(missing[k:k + 50])['quotes'].items():
                    self.quotes[sym] = (now, q)
            except Exception:
                pass
        stale = [s for s, (t, _) in self.quotes.items() if now - t >= 10 * QUOTE_TTL]
        for s in stale:
            del self.quotes[s]
        # A quote older than the TTL means its refresh just failed. It is still returned (the last known
        # price beats none) but carries its age and a `stale` flag, so no caller can mistake it for live.
        return {s: dict(self.quotes[s][1], age=round(now - self.quotes[s][0], 1), stale=now - self.quotes[s][0] >= QUOTE_TTL)
                for s in symbols if s in self.quotes}

    def _ratchet(self, trade_id, candidate, sign):
        """Combine `candidate` (this call's own best-price observation) with whatever is currently
        persisted for this trade, and persist the result. The lock plus a FRESH read of the trade --
        never the snapshot this call started with -- means an older, slower computation can never
        overwrite a newer one: whichever call writes last always writes max(everything seen so far),
        not just its own candidate. Returns the combined value, never worse for the trade than either."""
        if candidate is None or not sign:
            return None
        with self.trade_lock:
            try:
                fresh = self.service.options.journal.paper(key=trade_id)
            except ValueError:
                return candidate                      # the trade no longer exists; nothing to persist
            if fresh['status'] != 'open':
                return candidate
            payload = fresh['payload']
            stored = (payload.get('adx') or {}).get('high_water')
            best = candidate if stored is None else (max(stored, candidate) if sign > 0 else min(stored, candidate))
            if best != stored:
                payload['adx']['high_water'] = best
                try:
                    self.service.options.journal.update(trade_id, payload)
                except ValueError:
                    pass                               # closed between the read and this write
            return best

    @staticmethod
    def _replay(path, entry, stop, risk, sign, trail):
        """Walk the bars after entry in order. Each bar is judged against the stop that was in force when it began:
        the original stop, raised to `trail` risk-units behind the best price once the stock has moved that far
        (never beyond the entry). Returns the price that crossed the stop, or None. The order of moves inside a
        bar is unknown, so a bar's own high never raises the stop for that same bar."""
        best, level = entry, stop
        for high, low in path:
            if ((low if sign > 0 else high) - level) * sign <= 0:
                return low if sign > 0 else high
            best = max(best, high) if sign > 0 else min(best, low)
            if (best - entry) * sign >= trail * risk:
                raised = max(best - sign * risk, entry) if sign > 0 else min(best - sign * risk, entry)
                if (raised - level) * sign > 0:
                    level = raised
        return None

    def _latch(self, trade_id, info):
        """Save the first confirmed stop breach on the trade. Returns the breach on record."""
        with self.trade_lock:
            try:
                fresh = self.service.options.journal.paper(key=trade_id)
            except ValueError:
                return info
            payload = fresh['payload']
            have = (payload.get('adx') or {}).get('breach')
            if have or fresh['status'] != 'open':
                return have or info
            payload['adx']['breach'] = info
            try:
                self.service.options.journal.update(trade_id, payload)
            except ValueError:
                pass                                   # closed between the read and this write
            return info

    # ---------- reads ----------
    def board(self):
        saved = self._saved()
        stale = not saved or saved.get('stamp') != self._stamp() or self._behind(saved)
        due = time.monotonic() - self.auto > self._gap()
        if stale and not self.job['running'] and (not self.job['error'] or (self.RETRY and due)) and (not saved or due):
            self.auto = time.monotonic()
            self.start()
        if not saved:
            return dict(rows=[], counts={}, scanning=dict(self.job), data_date=None, ready=False)
        trades = self._trades()
        held = {t['symbol'] for t in trades if t['status'] == 'open'}
        rows = saved['rows']
        quotes = self._live_quotes([r['symbol'] for r in rows if r['state'] != 'watch']) if market_open() else {}
        records, market, learn = {}, saved.get('market') or {}, self._lessons(trades)
        for r in rows:
            r['held'] = r['symbol'] in held
            q = quotes.get(r['symbol']) or {}
            r['ltp'], r['ltp_age'] = q.get('ltp'), q.get('age')
            # In market hours a setup priced from an old quote, or from yesterday's close because no quote
            # arrived, must say so. (Outside market hours no quote is expected, so nothing is flagged.)
            r['price_stale'] = bool(market_open() and r['state'] != 'watch' and (q.get('stale') or not q.get('ltp')))
            key = (r['setup'], r['direction'])
            if key not in records:
                records[key] = record([t for t in trades if (t['setup'], t['direction']) == key])
            rec = r['record'] = records[key]
            losing = rec['n'] >= LEARN_MIN and rec['pnl'] < 0
            tail = f" Your trades in this setup are losing so far ({rec['wins']}/{rec['n']} won)." if losing else ''
            price = r['ltp'] or r['close']
            if r['state'] != 'watch':
                r.update(brain.conviction(r, market, learn['effects']))
            if r['state'] == 'range':
                inside = r['low'] <= price <= r['high']
                r['verdict'] = 'range' if inside else 'watch'
                r['why'] = (f"No dominant direction: the stock has held {r['low']:,.2f} to {r['high']:,.2f}. A range position is available." + tail
                            if inside else f"Price has left the {r['low']:,.2f} to {r['high']:,.2f} base: wait for a breakout setup instead.")
                r['rank'] = r['score'] - (30 if losing else 0)
                continue
            if r['state'] == 'watch':
                r['verdict'], r['why'], r['rank'] = 'watch', r['note'], 0
                continue
            bull = r['direction'] == 'bullish'
            sign = 1 if bull else -1
            risk = abs(r['trigger'] - r['stop'])
            past = (price - r['trigger']) * sign / risk if risk else 0.
            if (price - r['stop']) * sign <= 0:
                r['verdict'], r['why'] = 'skip', 'Price is beyond the stop: the setup has failed.'
            elif past > 0.5:
                r['verdict'], r['why'] = 'skip', f'Price is already {past:.1f}R past the entry: entering now is chasing.'
            elif past > 0 or (r['state'] == 'triggered' and not r['ltp']):   # a live price always decides
                r['verdict'], r['why'] = 'enter', 'Entry triggered: price is through the trigger and still close to it.' + tail
                if self.AUTO and not r['held']:
                    if r['id'] in self.auto_errors:
                        r['why'] += f" The automatic paper trade failed: {self.auto_errors[r['id']]}"
                    elif r['id'] in self.auto_skips:
                        r['why'] += f" Not traded automatically: {self.auto_skips[r['id']]}"
            else:
                r['verdict'] = 'armed'
                r['why'] = f"Enter when price trades {'above' if bull else 'below'} {r['trigger']:,.2f}." + tail
            r['rank'] = r['score'] - (30 if losing else 0)
        order = {'enter': 0, 'armed': 1, 'range': 2, 'watch': 3, 'skip': 4}
        rows.sort(key=lambda r: (order[r['verdict']], -r.get('rank', 0), r['symbol']))
        expected = self._expected()
        return dict(rows=rows, counts={v: sum(r['verdict'] == v for r in rows) for v in order}, scanning=dict(self.job),
                    data_date=saved['data_date'], ready=True, behind=saved['data_date'] < expected, expected_date=expected,
                    symbols=saved['symbols'], market_open=market_open(), live_quotes=len(quotes), auto=self.AUTO,
                    open_trades=sum(t['status'] == 'open' for t in trades), market=market,
                    alerts=[dict(id=t['id'], symbol=t['symbol'], action=t['action'], advice=t['advice'])
                            for t in (self.trades()['open'] if held else []) if t['action'] != 'hold'])

    def _row(self, symbol, signal_id):
        rows = [r for r in self.board()['rows'] if r['symbol'] == symbol]
        tradable = [r for r in rows if r['state'] != 'watch']
        return rows, next((r for r in rows if r['id'] == signal_id), None) or (tradable[0] if tradable else None)

    def _signal(self, row):
        """The stock plan a position has to fit: a trend trigger, stop and target, or the range to work inside."""
        if row['state'] == 'range':
            return dict(direction='neutral', setup=row['setup'], low=row['low'], high=row['high'])
        return dict(direction=row['direction'], setup=row['setup'], trigger=row['trigger'], stop=row['stop'], target=row['target'])

    def _events(self, symbol, end):
        try:
            events = self.service.options.events(symbol, end)
        except Exception:
            return dict(checked=False, next=None, inside=False)
        from .position_watch import next_event
        first = next_event(events)
        return dict(checked=bool(events and events.get('checked')), next=first,
                    inside=bool(first and first.get('date') and first['date'] <= end))

    def _plan(self, row, closes, atr=None, event=None):
        """Choose the expiry, then compare every structure the chain supports. A nearer expiry is used
        only when it has the time the move needs and enough tradable strikes."""
        desk, sig = self.service.desk, self._signal(row)
        hv = realised_vol(closes)
        want = self._days_wanted(sig, atr)
        chain = desk.chain(row['symbol'])
        listed = chain.get('expiries') or []
        at = next((k for k, e in enumerate(listed) if e == chain.get('expiry')), 0)
        tried, reason = [chain], ''
        while len(tried) < 3 and at + len(tried) < len(listed):
            current = tried[-1]
            read = chain_read(current, hv)
            if current.get('days', 0) >= want and read['liquid']:
                break
            reason = (f"the {current.get('expiry_date')} expiry has {current.get('days', 0):.0f} days and the move needs about {want}"
                      if current.get('days', 0) < want else
                      f"the {current.get('expiry_date')} expiry has only {read['tradable']} tradable strikes near the money")
            try:
                tried.append(desk.chain(row['symbol'], listed[at + len(tried)]['ts']))
            except ValueError:
                break
        chain = tried[-1]
        learned = self._learned(self._trades(), row['setup'])
        gap = lambda c: (f"{event['next'].get('title') or 'A scheduled event'} on {event['next']['date']}"
                         if event and event.get('next') and c.get('expiry_date') and event['next'].get('date', '9') <= c['expiry_date'] else None)
        out = plan(sig, chain, hv, learned, gap(chain), **self.PLAN)
        # Nothing in this expiry is worth trading: look at the next one before giving up.
        if not out['best'] and at + len(tried) < len(listed):
            try:
                later = desk.chain(row['symbol'], listed[at + len(tried)]['ts'])
                swap = plan(sig, later, hv, learned, gap(later), **self.PLAN)
            except ValueError:
                swap = None
            if swap and swap['best']:
                swap['notes'].insert(0, f"Nothing in the {chain.get('expiry_date')} expiry was worth trading, so this is the {later.get('expiry_date')} expiry.")
                out, chain, tried = swap, later, tried + [later]
        out['stale'] = bool(chain.get('stale')) or not chain.get('market_open')
        out['days_wanted'] = want
        if len(tried) > 1:
            out['notes'].insert(0, f"Moved to the {chain.get('expiry_date')} expiry because {reason}.")
        elif chain.get('days', 0) < want:
            out['notes'].insert(0, f"Only {chain.get('days', 0):.0f} days to this expiry and the move needs about {want}, but no later expiry is listed: time is tight.")
        if out.get('chain') and not out['chain']['liquid']:
            out['notes'].insert(0, f"Thin chain: only {out['chain']['tradable']} strikes near the money can be traded. Treat every price here with caution.")
        return out

    def detail(self, symbol, signal_id=''):
        from .desk import clean_symbol
        symbol = clean_symbol(symbol)
        up, _ = self._views(symbol, self._bench())
        i = up.n - 1
        bars = [dict(date=str(up.dates[k]), o=up.o[k], h=up.h[k], l=up.l[k], c=up.c[k], ema=round(up.ema20[k], 2),
                     pdi=round(up.pdi[k], 1), mdi=round(up.mdi[k], 1), adx=round(up.adx[k], 1)) for k in range(max(0, up.n - 130), up.n)]
        dom = up.dom[i]
        trend = 'No dominant direction'
        if dom:
            side = 'up' if dom == 1 else 'down'
            trend = (f'Power {side}trend' if up.adx[i] >= core.STRONG else f'Weak {side}trend') + (', ADX rising' if up.adx[i] > up.adx[i - 3] else ', ADX falling')
        if up.low_streak[i] >= core.BASE_BARS_DUAL:
            trend = f'Quiet base: ADX under 25 for {up.low_streak[i]} bars'
        wk = core.context(up, i)
        state = dict(trend=trend, adx=round(up.adx[i], 1), pdi=round(up.pdi[i], 1), mdi=round(up.mdi[i], 1),
                     dominant='+DMI' if dom == 1 else '−DMI' if dom == -1 else 'none',
                     dominance_since=str(up.dates[up.dom_since[i]]) if dom else None,
                     dominance_by={'XH': 'crossover high', 'CH': 'cross and hold'}.get(up.dom_kind[i], ''),
                     weekly_adx=wk['wk_adx'], weekly='+DMI on top' if wk['weekly_aligned'] else '−DMI on top' if wk['wk_adx'] is not None else 'n/a',
                     ema20=round(up.ema20[i], 2), atr=round(up.atr[i], 2))
        signals, chosen = self._row(symbol, signal_id)
        out = dict(symbol=symbol, state=state, bars=bars, signals=signals, signal=chosen, plan=None, plan_error='', event=None, decision=None)
        if not chosen or chosen['state'] == 'watch':
            return out
        horizon = str((clock() + timedelta(days=75)).date())
        out['event'] = event = self._events(symbol, horizon)
        try:
            out['plan'] = self._plan(chosen, self._day_closes(up), up.atr[i], event)
        except ValueError as exc:
            out['plan_error'] = str(exc)
        out['decision'] = self._decide(chosen, out['plan'], event, symbol, up.c[i])
        if out['plan']:
            for idea in out['plan']['ideas']:
                idea['decision'] = self._decide(chosen, dict(out['plan'], best=idea['name']),
                                                dict(event), symbol, up.c[i])
        return out

    def _decide(self, row, plan, event, symbol, fallback_spot):
        """Take / Wait / Pass for a setup, with the desk's own hard limits on top of the shared decision."""
        if plan and event.get('next'):
            event['inside'] = bool(event['next'].get('date', '9') <= (plan['expiry'] or ''))
        oi = None
        try:                                   # optional: today's open-interest lean, when the feed has it
            oi = self.service.options.oi_context(symbol, (plan or {}).get('spot') or fallback_spot)
        except Exception:
            pass
        out = brain.decide(row, plan, event, oi, unpriced=plan is None)
        if plan and plan.get('days') is not None and plan['days'] < self.FLOOR_DAYS and out['call'] != 'Pass':
            short = f"The best expiry has only {plan['days']:.0f} days left; this desk needs at least {self.FLOOR_DAYS}, because it exits 3 days before expiry."
            out = dict(out, call='Pass', why=short, cons=out['cons'] + [short])
        return out

    # ---------- trade ----------
    def _entry_problem(self, symbol, row, up):
        """Why this setup may not be entered right now, or ''. The same check guards the page and the automatic entry."""
        if row.get('price_stale') or not row.get('ltp'):
            return 'No live stock price: nothing is entered off the last bar close.'
        if row['verdict'] not in ('enter', 'range'):
            return 'Not triggered yet: only a setup on Enter can be traded.' if row['verdict'] == 'armed' else row['why']
        if self.job['error']:
            return f"The last scan failed ({self.job['error']}), so the setups may be out of date."
        expected = self._expected()
        saved = self._saved() or {}
        bench = self._bench()
        for name, have in (('The saved scan', saved.get('data_date')), (f'{symbol} bars', str(up.dates[-1])),
                           ('The Nifty bars', str(bench[0][-1]) if bench else None)):
            if have is None:
                return f'{name} are missing, so the setups cannot be trusted.'
            if str(have) < expected:
                return f'{name} end at {have}, but the last completed bar is {expected}. Entering now would use out-of-date setups.'
        return ''

    def paper(self, request, auto=False):
        """One-lot paper trade of an ADX option position at live bid/ask. No Kronos check is involved.
        With `auto`, the desk itself is entering: it takes the position its own plan recommends, and only
        when its own call on the setup is Take."""
        from .desk import clean_symbol
        symbol = clean_symbol(request.get('symbol'))
        _, row = self._row(symbol, str(request.get('signal') or ''))
        if not row or row['id'] != request.get('signal') or row['state'] == 'watch':
            raise ValueError('This ADX setup is no longer on the board. Rescan and try again.')
        if row['verdict'] in ('skip', 'watch'):
            raise ValueError(row['why'])
        up, _ = self._views(symbol, self._bench())
        if self.STRICT:
            problem = self._entry_problem(symbol, row, up)
            if problem:
                raise Declined(problem)
            if self.MAX_OPEN and sum(t['status'] == 'open' for t in self._trades()) >= self.MAX_OPEN:
                raise Declined(f'{self.MAX_OPEN} trades are already open.')
        event = self._events(symbol, str((clock() + timedelta(days=75)).date()))
        made = self._plan(row, self._day_closes(up), up.atr[-1], event)
        idea = next((x for x in made['ideas'] if x['name'] == (made['best'] if auto else request.get('idea'))), None)
        if not idea:
            raise ValueError('No option position on this chain pays enough for its risk.' if auto
                             else 'This option position is not available from the current chain.')
        if self.STRICT and made.get('days') is not None and made['days'] < self.FLOOR_DAYS:
            raise Declined(f"the best expiry has only {made['days']:.0f} days left and this desk needs at least {self.FLOOR_DAYS}.")
        override = None
        if self.STRICT:
            call = self._decide(row, dict(made, best=idea['name']), event, symbol, up.c[-1])
            if call['call'] != 'Take':
                if auto:
                    raise Declined(f"the desk's own call is {call['call']}: {call['why']}")
                if request.get('override') is not True:
                    raise Declined(f"the desk's own call is {call['call']}: {call['why']} Confirm the override to trade it anyway.")
                override = call['call']                 # a deliberate manual choice, recorded with the trade
        neutral = row['state'] == 'range'
        market = (self._saved() or {}).get('market') or {}
        adx = dict(signal=row['id'], setup=row['setup'], direction=row['direction'], kind=idea['kind'], quality=row['quality'], auto=auto,
                   score=row.get('score'), tags=brain.tags(row, market, made.get('chain')), regime=market.get('regime'),
                   planned_loss=-idea['cases'][0 if neutral else 1], planned_gain=idea['cases'][1 if neutral else 0])
        if override:
            adx['override'] = override
        if neutral:
            adx.update(low=row['low'], high=row['high'])
            # Only a short-volatility range trade is stopped out by a breakout; a long one is paid by it.
            stop = dict(lower=row['low'], upper=row['high']) if idea['kind'] == 'short_vol' else None
        else:
            bull = row['direction'] == 'bullish'
            adx.update(trigger=row['trigger'], stop=row['stop'], target=row['target'])
            stop = dict(lower=row['stop'], upper=None) if bull else dict(lower=None, upper=row['stop'])
        def check(legs, entry_charges):
            """The plan priced the position at quoted prices. The paper fill walks the real order book, so the
            target and stop outcomes are worked out again from the prices actually obtained, after the charges
            actually billed, and the trade is refused if its reward no longer clears the desk's floor."""
            left = made.get('left')
            if neutral or not self.MIN_EDGE or not left:
                return
            ivs = {l['symbol']: l['iv'] for l in idea['legs']}
            mults = (1 - self.IV_STRESS, 1., 1 + self.IV_STRESS) if self.IV_STRESS else (1.,)
            per_order = entry_charges.get('brokerage', 0.) / max(len(legs), 1)

            def net(price):
                worst = None
                for m in mults:
                    gain = buy = sell = half = 0.
                    for l in legs:
                        units = abs(l['qty']) * l['lot']
                        worth = bs(price, l['strike'], left, (ivs.get(l['symbol']) or 30.) * m, l['type'])
                        gain += l['qty'] * l['lot'] * (worth - l['entry_fill'])
                        if l['qty'] > 0:
                            sell += worth * units
                        else:
                            buy += worth * units
                        half += max((l.get('ask_price') or 0.) - (l.get('bid') or 0.), 0.) / 2 * units
                    total = gain - sum(entry_charges.values()) - fee(buy + sell, buy + sell, len(legs), per_order) - half
                    worst = total if worst is None else min(worst, total)
                return worst

            win, loss = net(row['target']), net(row['stop'])
            if win <= 0:
                raise Declined(f"at the prices it would really get, this position makes nothing at the target (₹{win:,.0f} after costs).")
            if loss < 0 and win / -loss < self.MIN_EDGE:
                raise Declined(f"at the prices it would really get, it makes ₹{win:,.0f} at the target against ₹{-loss:,.0f} lost at the stop "
                               f"({win / -loss:.2f} to 1); this desk needs at least {self.MIN_EDGE}.")

        # The ADX details are saved in the same write that opens the trade, so no reader ever sees a half-made record.
        return self.service.desk.open_paper(dict(
            check=check,
            symbol=symbol, name=f"{self.TITLE} {row['setup']} · {idea['name']}",
            legs=[dict(symbol=l['symbol'], lots=l['qty']) for l in idea['legs']],
            extra=dict(source=self.SOURCE, adx=adx), plan_extra=dict(underlying_stop=stop),
            cap=self.MAX_OPEN if self.STRICT else None,         # re-checked inside the journal's write lock, so two requests cannot both pass
            note_prefix=f'{self.TITLE} trade: managed by the stock plan and DMI dominance. '))

    def trades(self):
        """Open ADX trades with exit guidance from the stock itself, and the record of closed ones."""
        trades = self._trades()
        opened = [t for t in trades if t['status'] == 'open']
        quotes = self._live_quotes([t['symbol'] for t in opened]) if opened and market_open() else {}
        bench = self._bench() if opened else None
        today = clock().date()
        learn = self._lessons(trades)
        policy = learn['policy']
        listed = getattr(getattr(self.service.options, 'market', None), 'contracts', None) or []
        last_prices = {}
        if opened and market_open():
            legs = sorted({l['symbol'] for t in opened for l in t['legs']})
            for k in range(0, len(legs), 50):          # the price call takes at most 50 contracts; ask for every leg
                try:
                    last_prices.update(self.service.desk.ltp(legs[k:k + 50])['quotes'])
                except Exception:
                    pass                               # a trade with a missing leg price shows no P&L rather than an old one
        for t in opened:
            marks = [(last_prices.get(l['symbol']) or {}).get('ltp') for l in t['legs']]
            if t['legs'] and all(marks) and all(l['fill'] for l in t['legs']):
                # Estimate from last traded prices; a real exit fills at bid/ask and pays charges.
                t['pnl'], t['estimated'] = round(sum(l['qty'] * l['lot'] * (m - l['fill']) for l, m in zip(t['legs'], marks)), 2), True
            elif t['pnl'] is not None and opened and market_open():
                # Some option price did not arrive. The last mark may be hours or days old, so it is shown as such
                # and is not used as evidence for the loss and profit rules.
                t['last_mark'], t['pnl'], t['pnl_unknown'] = t['pnl'], None, True
            neutral = t['direction'] == 'neutral'
            sign = 0 if neutral else (1 if t['direction'] == 'bullish' else -1)
            try:
                up, _ = self._views(t['symbol'], bench)
                last, dom, adx = up.c[-1], up.dom[-1], up.adx[-1]
            except ValueError:
                up, last, dom, adx = None, None, 0, None
            q = quotes.get(t['symbol']) or {}
            price = q.get('ltp') or last
            t['spot'] = price
            t['spot_age'], t['spot_source'] = q.get('age'), 'live' if q.get('ltp') else 'close'
            t['spot_stale'] = bool(market_open() and (q.get('stale') or not q.get('ltp')))
            age = (today - datetime.fromisoformat(t['created']).date()).days
            left = (datetime.fromisoformat(t['expiry']).date() - today).days
            t['days_held'], t['days_left'], t['progress'] = age, left, None
            if not neutral:
                start = t['entry_spot'] or t['trigger']
                if price is not None and start and (t['target'] - start) * sign > 0:
                    t['progress'] = round((price - start) / (t['target'] - start) * 100)
            # The best stock price since entry drives the trailing stop. A daily bar spans the whole
            # session, so the entry day's OWN bar is never used here -- it can hold a pre-entry high or
            # low the trade was never exposed to. Full days strictly after entry are fine (the whole day
            # happened after entry) and catch up on anything missed while the app was not running; the
            # entry day itself is covered only by a recorded Mark and the live price, each pulse of the
            # watcher. The running best is persisted on the trade (`high_water`) under a lock that always
            # re-reads the latest saved value before writing, so a slower, older read can never clobber a
            # newer, higher one -- the stored value only ever moves in the trade's favour.
            path = self._after(up, t['created']) if up else []
            # A fallback close can predate entry. Only a current live observation may
            # raise the saved trail or confirm a new price-based stop breach.
            live_price = bool(market_open() and q.get('ltp') and not q.get('stale')
                              and q.get('age') is not None and q['age'] < QUOTE_TTL)
            spots = [m['underlying'] for m in t['marks'] if m.get('underlying')] + ([price] if live_price else [])
            seen = ([h for h, _ in path] + spots if sign > 0 else [l for _, l in path] + spots) if sign else []
            candidate = (max(seen) if sign > 0 else min(seen)) if seen else None
            best = self._ratchet(t['id'], candidate, sign)
            # A stop touched between two looks (the app was closed, a price call failed) must not be forgotten
            # because the stock has since recovered. Bars wholly after the entry and recorded marks are
            # checked against the ORIGINAL stop, which is the loosest it ever was, so only a certain touch counts.
            touched = None
            if sign and t['stop'] is not None:
                entry, risk = t['entry_spot'] or t['trigger'], abs((t['trigger'] or 0) - t['stop'])
                if entry and risk:      # bars in order, each against the stop (original or trailed) that was in force when it began
                    touched = self._replay(path, entry, t['stop'], risk, sign, policy['trail'])
                marked = [m['underlying'] for m in t['marks'] if m.get('underlying')]
                if touched is None and marked:      # recorded marks have no order, so only the original stop is certain
                    extreme = min(marked) if sign > 0 else max(marked)
                    if (extreme - t['stop']) * sign <= 0:
                        touched = extreme
            due = None
            if any(l['qty'] < 0 for l in t['legs']):
                first = self._events(t['symbol'], t['expiry'])['next']
                if first and first.get('date'):
                    due = (datetime.fromisoformat(first['date']).date() - today).days
            strikes = [c['strike'] for c in listed if t['kind'] == 'long' and c.get('underlying') == t['symbol']
                       and c.get('expiry') == t['expiry'] and c.get('type') == ('CE' if sign > 0 else 'PE')]
            care = brain.manage(t, price if live_price else None, best, policy, strikes, due)
            t['stop_now'], t['trailed'] = care['stop'], care['trailed']
            out = price is not None and neutral and not (t['low'] <= price <= t['high'])
            hurt = t['pnl'] is not None and t['max_loss'] and t['pnl'] <= -0.5 * t['max_loss']
            beyond = not neutral and live_price and (price - care['stop']) * sign <= 0
            if (beyond or touched is not None) and not t['breach']:
                # The first confirmed breach is saved with the trade, so a later recovery, a restart or a missed
                # price call cannot turn the trade back into a Hold.
                t['breach'] = self._latch(t['id'], dict(level=care['stop'] if beyond else t['stop'], price=price if beyond else touched,
                                                         time=clock().isoformat(), source='live price' if beyond else 'later bars'))
            if beyond:
                t['advice'], t['action'] = (f"Exit: the stock fell back to the trailed stop {care['stop']:,.2f}. The stop was raised as the stock moved your way."
                                            if care['trailed'] else f"Exit: stock is beyond the stop {t['stop']:,.2f}."), 'exit'
            elif t['breach']:
                b = t['breach']
                t['advice'], t['action'] = (f"Exit: the stock traded through the stop {b['level']:,.2f} (to {b['price']:,.2f}, seen from {b['source']} at {b['time'][:16].replace('T', ' ')}) "
                                            f"and has since come back. The setup had already failed; do not wait for it to return."), 'exit'
            elif not neutral and dom == -sign:
                t['advice'], t['action'] = f"Exit: the {'−DMI' if sign > 0 else '+DMI'} has taken dominance on the daily chart.", 'exit'
            elif out and t['kind'] == 'short_vol':
                t['advice'], t['action'] = f"Exit: the stock has broken out of its {t['low']:,.2f} to {t['high']:,.2f} range.", 'exit'
            elif left <= 3:
                t['advice'], t['action'] = f'Exit: {left} day(s) to expiry.', 'exit'
            elif hurt:
                t['advice'], t['action'] = 'Exit: half of what you put at risk is already lost.', 'exit'
            elif out and t['kind'] == 'long_vol' and (t['pnl'] or 0) > 0:
                t['advice'], t['action'] = 'Book profit: the breakout you paid for has happened.', 'profit'
            elif neutral and t['kind'] == 'long_vol' and adx is not None and adx >= core.STRONG and (t['pnl'] or 0) > 0:
                t['advice'], t['action'] = 'Book profit: ADX is above 25, so a trend has started.', 'profit'
            elif not neutral and price is not None and (price - t['target']) * sign >= 0:
                t['advice'], t['action'] = f"Take profit: stock reached the target {t['target']:,.2f}.", 'profit'
            elif t['progress'] is not None and t['progress'] >= policy['book']:
                t['advice'], t['action'] = f"Book most of it: the stock has covered {t['progress']}% of the move to the target.", 'profit'
            elif t['pnl'] is not None and t['max_profit'] and t['pnl'] >= (0.5 if neutral else 0.7) * t['max_profit']:
                t['advice'], t['action'] = f"Take profit: {'half' if neutral else '70%'} of the maximum profit is already earned.", 'profit'
            elif self._timed_out(age, path):
                t['advice'], t['action'] = self._timed_out(age, path), 'exit'
            elif care['adjust']:
                t['advice'], t['action'] = 'Adjust: ' + care['adjust'], 'adjust'
            elif care['trailed']:
                t['advice'], t['action'] = f"Hold: stop raised to {care['stop']:,.2f} to protect the move so far.", 'hold'
            else:
                t['advice'], t['action'] = 'Hold: the stock plan is intact.', 'hold'
            if t.get('pnl_unknown'):
                t['advice'] += ' Careful: not every option price arrived, so the P&L is unknown and the loss and profit limits could not be checked.'
            if t['spot_stale']:
                t['advice'] += (f" Careful: the stock price is {t['spot_age']:.0f}s old (the live refresh failed)." if t['spot_age'] is not None
                                else " Careful: no live stock price arrived, so this uses the last daily close.")
        closed = [t for t in trades if t['status'] == 'closed']
        for t in trades:
            t.pop('marks', None)
        groups = []
        for label, key in (('setup', lambda t: f"{t['setup']} · {t['direction']}"), ('structure', lambda t: t['name'].split(' · ')[-1])):
            for name in sorted({key(t) for t in closed}):
                groups.append(dict(by=label, name=name, **record([t for t in closed if key(t) == name])))
        return dict(open=opened, closed=closed[:50], total=record(closed), groups=groups, learn_min=LEARN_MIN,
                    market_open=market_open(), policy=policy,
                    lessons=dict(notes=learn['notes'], mistakes=learn['mistakes'], changes=learn['changes']))

    # ---------- real-time watch ----------
    def pulse(self):
        """One pass of the watcher: log an alert when an open trade first needs action, or when a
        graded A/B setup goes from Armed to Enter. Returns the alerts added."""
        board = self.board()
        added, now = [], clock().isoformat()
        log = self._saved('alerts') or []
        last = {a['id']: a['action'] for a in log}
        for t in (self.trades()['open'] if board.get('open_trades') else []):
            if t['action'] != 'hold' and self.told.get(t['id'], last.get(t['id'])) != t['action']:
                added.append(dict(kind='trade', id=t['id'], symbol=t['symbol'], action=t['action'], text=t['advice'], pnl=t['pnl']))
            self.told[t['id']] = t['action']
        if self.AUTO:
            added += self._auto_enter(board)
        elif self.seen is not None:
            for r in board['rows']:
                if r['verdict'] == 'enter' and self.seen.get(r['id']) == 'armed' and r.get('grade') in ('A', 'B') and not r['held']:
                    added.append(dict(kind='entry', id=r['id'], symbol=r['symbol'], action='enter', pnl=None,
                                      text=f"{r['setup']} {r['direction']} triggered through {r['trigger']:,.2f} (score {r['score']}, grade {r['grade']})."))
        self.seen = {r['id']: r['verdict'] for r in board['rows']}
        if added:
            n = log[-1]['n'] if log else 0
            for k, a in enumerate(added):
                a.update(n=n + k + 1, time=now)
            self._save('alerts', (log + added)[-ALERT_LOG:])
        return added

    def _auto_enter(self, board):
        """Open a one-lot paper trade, in the position the plan recommends, for every setup that is on
        Enter right now. Only a live stock price counts, a stock with an open trade is left alone, and a
        setup is traded once: after it is closed it is not bought again. A failed attempt (no two-sided
        option quotes, a thin chain) is retried a few times and then reported."""
        added, now = [], time.monotonic()
        if board.get('behind') or not board.get('ready'):
            return added                           # setups from out-of-date bars are never traded
        taken = {t['signal'] for t in self._trades()}
        busy = {r['symbol'] for r in board['rows'] if r['held']}
        for r in board['rows']:
            if r['verdict'] != 'enter':
                self.auto_skips.pop(r['id'], None)
            if r['verdict'] != 'enter' or not r['ltp'] or r['price_stale'] or r['id'] in taken or r['symbol'] in busy:
                continue
            if self.MAX_OPEN and len(busy) >= self.MAX_OPEN:
                break
            tries, last = self.auto_tries.get(r['id'], (0, 0.))
            if tries >= self.AUTO_TRIES or (tries and now - last < self.AUTO_WAIT) or now < self.auto_hold.get(r['id'], 0.):
                continue
            self.auto_tries[r['id']] = (tries + 1, now)
            try:
                trade = self.paper(dict(symbol=r['symbol'], signal=r['id']), auto=True)
            except Declined as exc:
                # The desk's own rules said no. That is not a failure: no attempt is used up and no alert is raised;
                # the reason shows on the row and the setup is looked at again after a while.
                self.auto_tries[r['id']] = (tries, last)
                self.auto_skips[r['id']] = str(exc)
                self.auto_hold[r['id']] = now + AUTO_RECHECK
                continue
            except Exception as exc:
                why = str(exc) if isinstance(exc, ValueError) else 'the option chain could not be read.'
                self.auto_errors[r['id']] = why
                if tries + 1 >= self.AUTO_TRIES:
                    added.append(dict(kind='entry', id=r['id'], symbol=r['symbol'], action='missed', pnl=None,
                                      text=f"{r['setup']} {r['direction']} triggered, but the automatic paper trade failed: {why}"))
                continue
            busy.add(r['symbol'])
            self.auto_errors.pop(r['id'], None)
            self.auto_skips.pop(r['id'], None)
            name = trade['payload']['plan']['name'].split(' · ')[-1]
            added.append(dict(kind='entry', id=r['id'], symbol=r['symbol'], action='enter', pnl=None,
                              text=f"Paper trade opened automatically: {name}. {r['setup']} {r['direction']} triggered through "
                                   f"{r['trigger']:,.2f} at {r['ltp']:,.2f} (score {r['score']}, grade {r['grade']})."))
        return added

    def alerts(self, after=0):
        """Alerts newer than `after`, for the page to announce. Reading this never fetches a price."""
        log = self._saved('alerts') or []
        return dict(alerts=[a for a in log if a['n'] > after][-20:], last=log[-1]['n'] if log else 0,
                    watching=bool(self.thread and self.thread.is_alive()), every=WATCH_SECONDS, market_open=market_open())

    def watch(self):
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._watch, name='adx-watch', daemon=True)
            self.thread.start()

    def _watch(self):
        while not self.stop_event.wait(WATCH_SECONDS):
            if market_open():
                try:
                    self.pulse()
                except Exception:          # watching must never stop the app
                    pass

    def close(self):
        self.stop_event.set()
