"""Fast options desk: one live option-chain call per refresh, custom strategy legs and
one-click paper trades. Read-only market data; no broker orders."""
from datetime import datetime, timedelta
import math
import threading
import time
import uuid

from .options_market import FYERS_COOLDOWN, IST, clock, market_open, number
from .options_math import bs, book_fill, charges, intrinsic, payoff_limits

CHAIN_URL = 'https://api-t1.fyers.in/data/options-chain-v3'
QUOTES_URL = 'https://api-t1.fyers.in/data/quotes'
STRIKES = 25          # strikes each side of ATM in one chain call
CHAIN_TTL = 1.0       # seconds; concurrent browser polls share one FYERS call
SYMBOL_OK = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789&-_')


def clean_symbol(value):
    symbol = str(value or '').strip().upper()
    if not 1 <= len(symbol) <= 40 or set(symbol) - SYMBOL_OK:
        raise ValueError('Choose a valid F&O stock.')
    return symbol


def years_left(expiry_ts, now=None):
    """Calendar days to the 15:30 IST expiry, never below ~1 hour so greeks stay finite."""
    now = now or clock().timestamp()
    return max((expiry_ts - now) / 86400, 1 / 24)


def implied_vol(price, spot, strike, days, kind):
    """Black-Scholes IV in %, by bisection. None when the price carries no time value."""
    if any(not math.isfinite(v) for v in (price,spot,strike,days)) or price <= 0 or spot <= 0 or strike <= 0 or days <= 0 or kind not in ('CE','PE'):
        return None
    # European puts can trade below immediate intrinsic due to discounting.
    # Check the model's no-arbitrage lower bound, not immediate exercise value.
    lo, hi = .5, 400.
    if not bs(spot,strike,days,lo,kind) < price < bs(spot,strike,days,hi,kind):
        return None
    for _ in range(45):
        mid = (lo + hi) / 2
        if bs(spot, strike, days, mid, kind) > price:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 2)


def delta(spot, strike, days, iv, kind):
    if not iv or days <= 0 or spot <= 0:
        return None
    t, sigma = days / 365., iv / 100
    d1 = (math.log(spot / strike) + (.06 + sigma * sigma / 2) * t) / (sigma * math.sqrt(t))
    n = (1 + math.erf(d1 / math.sqrt(2))) / 2
    return round(n if kind == 'CE' else n - 1, 3)


def max_pain(rows):
    strikes = [r['strike'] for r in rows]
    if not strikes:
        return None
    def pain(k):
        total = 0.
        for r in rows:
            total += (r['CE'] or {}).get('oi', 0) * max(0., k - r['strike'])
            total += (r['PE'] or {}).get('oi', 0) * max(0., r['strike'] - k)
        return total
    return min(strikes, key=pain)


def payoff_summary(legs, spot, entry_credit, costs):
    """Exact expiry bounds including a stock price of zero and the call tail."""
    return payoff_limits(legs,entry_credit,costs)


class FastDesk:
    def __init__(self, service):
        self.service = service
        self.cache = {}
        self.last_good = {}
        self.locks = {}
        self.guard = threading.Lock()
        self.board = None
        self.board_lock = threading.Lock()
        self.board_state = {}      # (symbol, direction) -> dict(verdict, since)
        self.board_events = []     # newest last: ready / lost transitions seen while the app was running
        self.board_seq = 0
        self.board_thread = None
        self.board_stop = threading.Event()
        self.board_started = None

    @property
    def market(self):
        return self.service.options.market

    def warm(self):
        """Load the instrument master once in the background so the first chain is instant."""
        def run():
            try:
                self.market.universe()
            except Exception:
                pass
        threading.Thread(target=run, name='desk-warmup', daemon=True).start()

    # ---------- reads ----------
    def bootstrap(self):
        options = self.service.options
        try:
            universe = options.market.universe()
        except ValueError:
            universe = []
        fno = set(universe)
        ideas = [dict(symbol=r['symbol'], patterns=r['patterns'][:3], bias=r.get('bias', 'bullish'))
                 for r in options.stock_ideas() if not fno or r['symbol'] in fno]
        ai = {}
        for a in options.journal.analyses():
            f = a.get('forecast_summary')
            if a['symbol'] in ai or not f or not f.get('bias'):
                continue
            ai[a['symbol']] = dict(bias=f['bias'], lower=a.get('lower'), upper=a.get('upper'),
                                   median_end=f.get('median_end'), last_close=f.get('last_close'),
                                   created=a.get('created'), asof=f.get('asof'))
        return dict(universe=universe, ideas=ideas, ai=ai, paper=options.journal.paper(), settings=options.journal.settings(),
                    market_open=market_open(), time=clock().isoformat())

    def chain(self, symbol, expiry=None):
        symbol = clean_symbol(symbol)
        expiry = str(expiry or '')
        if expiry and not expiry.isdigit():
            raise ValueError('Unknown expiry.')
        key = (symbol, expiry)
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            hit = self.cache.get(key)
            if hit and time.monotonic() - hit[0] < CHAIN_TTL:
                return hit[1]
            try:
                data = self.market._get(CHAIN_URL, dict(symbol=f'NSE:{symbol}-EQ', strikecount=STRIKES,
                                                        timestamp=expiry), True)
                value = self._shape(symbol, data)
            except ValueError as exc:
                old = self.last_good.get(key)
                if not old:
                    raise
                value = {**old, 'stale': True, 'error': str(exc)}
                try:   # keep the stock price moving through the Definedge fallback
                    q = self.service.prices.cash_quote(symbol)
                    if q:
                        value['spot'] = {**old['spot'], 'ltp': q['ltp'], 'source': 'Definedge'}
                except ValueError:
                    pass
                self.cache[key] = (time.monotonic(), value)
                return value
            self.cache[key] = (time.monotonic(), value)
            self.last_good[key] = value
            return value

    def _shape(self, symbol, data):
        rows = data.get('optionsChain') or []
        under = next((r for r in rows if r.get('option_type') == ''), None)
        if not under or number(under.get('ltp')) <= 0:
            raise ValueError('FYERS returned no stock price for this chain.')
        spot = number(under['ltp'])
        expiries = [dict(ts=str(e.get('expiry')), date=str(e.get('date'))) for e in data.get('expiryData') or []]
        options = [r for r in rows if r.get('option_type') in ('CE', 'PE')]
        if not options:
            raise ValueError('No option quotes for this stock.')
        # The chain's own contracts tell us the expiry; the master gives lot and tick.
        master = getattr(self.market, 'by_symbol', {}) or {}
        if not master:
            try:
                self.market.universe(); master = self.market.by_symbol
            except ValueError:
                master = {}
        first = master.get(options[0].get('symbol'), {})
        expiry_date = first.get('expiry')
        selected = next((e for e in expiries if expiry_date and
                         datetime.strptime(e['date'], '%d-%m-%Y').date().isoformat() == expiry_date), None)
        if selected is None and expiries:
            selected = expiries[0]
        # Chain expiry identifiers may be midnight epochs, not settlement time.
        expiry_day = expiry_date or (datetime.strptime(selected['date'],'%d-%m-%Y').date().isoformat() if selected else None)
        if not expiry_day:
            raise ValueError('Option expiry is unavailable; cannot calculate IV.')
        expiry_ts = datetime.fromisoformat(expiry_day).replace(hour=15,minute=30,tzinfo=IST).timestamp()
        days = years_left(expiry_ts)
        by_strike = {}
        for r in options:
            strike = number(r.get('strike_price'))
            kind = r['option_type']
            ltp, bid, ask = number(r.get('ltp')), number(r.get('bid')), number(r.get('ask'))
            mid = (bid + ask) / 2 if 0 < bid <= ask else ltp
            iv = implied_vol(mid, spot, strike, days, kind)
            info = master.get(r.get('symbol'), {})
            by_strike.setdefault(strike, dict(strike=strike, CE=None, PE=None))[kind] = dict(
                symbol=r.get('symbol'), ltp=ltp, ch=number(r.get('ltpch')), chp=number(r.get('ltpchp')),
                bid=bid, ask=ask, oi=number(r.get('oi')), oich=number(r.get('oich')), vol=number(r.get('volume')),
                iv=iv, delta=delta(spot, strike, days, iv, kind), lot=info.get('lot'), tick=info.get('tick', .05))
        table = [by_strike[k] for k in sorted(by_strike)]
        atm = min(table, key=lambda r: abs(r['strike'] - spot))
        atm_ivs = [x['iv'] for x in (atm['CE'], atm['PE']) if x and x.get('iv')]
        lot = next((x['lot'] for r in table for x in (r['CE'], r['PE']) if x and x.get('lot')), None)
        call_oi, put_oi = number(data.get('callOi')), number(data.get('putOi'))
        vix = data.get('indiavixData') or {}
        return dict(symbol=symbol, time=clock().isoformat(), market_open=market_open(), stale=False, source='FYERS',
                    spot=dict(ltp=spot, ch=number(under.get('ltpch')), chp=number(under.get('ltpchp')),
                              bid=number(under.get('bid')), ask=number(under.get('ask')),
                              fut=number(under.get('fp')) or None, source='FYERS'),
                    expiries=expiries, expiry=selected, expiry_date=expiry_date, days=round(days, 3),
                    lot=lot, atm=atm['strike'], atm_iv=round(sum(atm_ivs) / len(atm_ivs), 2) if atm_ivs else None,
                    pcr=round(put_oi / call_oi, 2) if call_oi else None, max_pain=max_pain(table),
                    vix=number(vix.get('ltp')) or None, rows=table)

    def ai(self, symbol):
        """Latest saved Kronos forecast and its two best strategies. Instant: never runs the model."""
        symbol = clean_symbol(symbol)
        options = self.service.options
        job = None
        if self.service.active:
            try:
                run = self.service.store.get(self.service.active)
                job = {k: run.get(k) for k in ('id', 'kind', 'status', 'progress', 'message', 'created')}
                job['symbol'] = (run.get('request') or {}).get('symbol')
            except Exception:
                job = None
        key = options.journal.latest_forecast_id(symbol)
        if not key:
            return dict(symbol=symbol, analysis=None, job=job)
        a = options.journal.analysis(key)
        f = a.get('forecast') or {}
        from .options_math import underlying_stop
        ideas = []
        for c in options.top_ideas(a.get('candidates', [])):
            if c.get('calculation_version')!=2:
                c={**c,'status':'Skip','reasons':list(c.get('reasons',[]))+['Saved calculation is outdated. Analyse this stock again']}
            try:
                stop = c.get('underlying_stop') or underlying_stop(c['bias'], c['legs'], f)
            except (KeyError, TypeError, ValueError):
                stop = None
            ideas.append(dict(id=c.get('id'), name=c['name'], bias=c.get('bias'), status=c.get('status'),
                              expiry=c.get('expiry'), entry_credit=c.get('entry_credit'), max_loss=c.get('max_loss'),
                              max_profit=c.get('max_profit'), breakevens=c.get('breakevens', []),
                              scenario_profit_pct=c.get('scenario_profit_pct'), stressed_mean_net=c.get('stressed_mean_net'),
                              short_touch_pct=c.get('short_touch_pct'), underlying_stop=stop, exit_by=c.get('exit_by'),
                              reasons=[r for r in c.get('reasons', []) if not r.startswith('Chain preview')],
                              legs=[dict(strike=l['strike'], type=l['type'], qty=l['qty'], ltp=l.get('ltp')) for l in c['legs']]))
        return dict(symbol=symbol, job=job, analysis=dict(
            id=a['id'], created=a.get('created'), forecast_created=a.get('forecast_created'), data_mode=a.get('data_mode'),
            data_asof=a.get('data_asof'), spot=a.get('spot'), lower=a.get('lower'), upper=a.get('upper'),
            forecast_end=a.get('forecast_end'), expiry=a.get('expiry'), status=a.get('status'), reason=a.get('reason'),
            patterns=a.get('patterns', []), ideas=ideas,
            forecast=dict(asof=f.get('asof'), last_close=f.get('last_close'), summary=f.get('summary'),
                          history=[dict(date=r['date'], close=r['close']) for r in (f.get('history') or [])[-60:]],
                          rows=[dict(date=r['date'], median=r['median'], q10=r['q10'], q90=r['q90'],
                                     samples=[p[i]['close'] for p in f.get('paths',[]) if len(p)>i and p[i]['date']==r['date']])
                                for i,r in enumerate(f.get('forecast', []))])))

    def context(self, symbol):
        """Next scheduled event and the Kronos stop levels for one stock (both sides; the page picks by strategy)."""
        symbol = clean_symbol(symbol)
        options = self.service.options
        from .position_watch import next_event, kronos_stop
        end = str((clock() + timedelta(days=60)).date())
        try:
            events = options.events(symbol, end)
        except Exception:
            events = None
        key = options.journal.latest_forecast_id(symbol)
        stop = None
        if key:
            f = options.journal.analysis(key).get('forecast') or {}
            stop = kronos_stop(f, [], 'range') if f.get('history') else None
        return dict(symbol=symbol, event=next_event(events), events_checked=bool(events and events.get('checked')),
                    stop=stop, upcoming=(events or {}).get('upcoming', [])[:5], setups=self.pattern_levels(symbol))

    def pattern_levels(self, symbol):
        """Saved scanner setups for one stock with a measured-move target, best first.
        Bullish: trigger above the pivot, stop below. Bearish: trigger below the breakdown level, stop above."""
        return self.pattern_rows(symbol)[:4]

    def pattern_rows(self, only=None):
        """Every saved scanner setup (or one stock's) with trigger, stop and a measured-move target, best score first."""
        from scanners import ALL_SCANNERS
        try:
            rules = self.service.store.settings()
            r, floor = float(rules.target_r), float(rules.min_target_pct)
        except Exception:
            r, floor = 1.0, 5.0
        rows, seen = [], {}
        for s in self.service.setups():
            symbol = str(s.get('symbol', '')).upper()
            if only and symbol != only:
                continue
            try:
                pivot, stop, close = float(s['pivot']), float(s['support']), float(s['close'])
            except (KeyError, TypeError, ValueError):
                continue
            short = s.get('direction') == 'short'
            if short:
                if not stop > pivot > 0:
                    continue
                target = pivot - max((stop - pivot) * r, pivot * floor / 100)
            else:
                if not pivot > stop > 0:
                    continue
                target = pivot + max((pivot - stop) * r, pivot * floor / 100)
            key = (symbol, s.get('scanner'), round(pivot, 2), round(stop, 2))
            if key in seen:   # the same pattern on Daily and Weekly: one row
                row = seen[key]
                if s.get('timeframe') not in row['timeframe']:
                    row['timeframe'] = 'D+W'
                row['score'] = max(row['score'] or 0, s.get('score') or 0)
                continue
            module = ALL_SCANNERS.get(s.get('scanner'))
            rows.append(dict(symbol=symbol, scanner=s.get('scanner'), title=module.TITLE if module else str(s.get('scanner')),
                             direction='short' if short else 'long', timeframe=s.get('timeframe', 'D'),
                             date=s.get('date'), label=s.get('label', ''), score=s.get('score'),
                             close=close, pivot=round(pivot, 2), stop=round(stop, 2), target=round(max(target, .05), 2)))
            seen[key] = rows[-1]
        rows.sort(key=lambda x: -(x['score'] or 0))
        return rows

    # ---------- entry board: pattern + Kronos agreement across all F&O stocks ----------
    BOARD_TTL = 8.0       # seconds; browser polls share one pass
    BOARD_QUOTE_BAND = 10  # only quote stocks whose last scan close is within this % of the trigger

    def entry_board(self):
        """F&O stocks ranked by how ready they are to enter, from saved scanner setups, the newest saved Kronos
        forecast per stock and live prices. Never runs the model; stocks without a forecast say so."""
        with self.board_lock:
            hit = self.board
            if hit and time.monotonic() - hit[0] < self.BOARD_TTL:
                return hit[1]
            value = self._entry_board()
            self.board = (time.monotonic(), value)
            return value

    BOARD_EVERY = 60       # seconds between background checks while the market is open

    def start_board_watch(self):
        """Re-check the board in the background for as long as the app runs. Nothing is tracked while it is closed."""
        if self.board_thread and self.board_thread.is_alive():
            return
        self.board_stop.clear()
        self.board_thread = threading.Thread(target=self._board_loop, name='entry-board', daemon=True)
        self.board_thread.start()

    def close_board_watch(self):
        self.board_stop.set()

    def _board_loop(self):
        first = True
        while not self.board_stop.wait(5 if first else self.BOARD_EVERY):
            if first or market_open():     # a closed market cannot change prices; the page still refreshes on demand
                try:
                    self.board = None          # skip the 8 s page cache: background checks always re-price
                    self.entry_board()
                except Exception:          # tracking must never stop the app
                    pass
            first = False

    def _track(self, entries, now):
        """Record when a stock becomes Ready and when it stops being Ready. The first pass is the baseline."""
        baseline = not self.board_state
        seen = set()
        for e in entries:
            key = (e['symbol'], e['direction'])
            seen.add(key)
            old = self.board_state.get(key)
            if old is None or old['verdict'] != e['verdict']:
                if not baseline and (e['verdict'] == 'ripe' or (old and old['verdict'] == 'ripe')):
                    self.board_seq += 1
                    kind = 'ready' if e['verdict'] == 'ripe' else 'lost'
                    self.board_events.append(dict(id=self.board_seq, kind=kind, symbol=e['symbol'], direction=e['direction'],
                                                  price=e['price'], time=now, reason=(e['reasons'] or [''])[0],
                                                  verdict=e['verdict'], pattern=e['pattern']['title']))
                    del self.board_events[:-60]
                old = self.board_state[key] = dict(verdict=e['verdict'], since=now)
            e['since'] = old['since']
        for key in set(self.board_state) - seen:   # the setup left the scanner list
            del self.board_state[key]

    def _fno_symbols(self):
        try:
            universe = set(self.market.universe())
            if universe:
                return universe
        except ValueError:
            pass
        try:
            return {key[0] for key in self.service.prices.option_tokens()}
        except Exception:
            raise ValueError('F&O stock list unavailable. Check the FYERS login.') from None

    def _entry_board(self):
        from .entry_board import evaluate
        fno = self._fno_symbols()
        rows = [r for r in self.pattern_rows() if r['symbol'] in fno]
        options = self.service.options
        analyses = {}
        for a in options.journal.analyses():    # newest first
            f = a.get('forecast_summary')
            if a['symbol'] not in analyses and f and f.get('bias') and a['symbol'] in {r['symbol'] for r in rows}:
                try:
                    best = options.compact(a).get('best')
                except Exception:
                    best = None
                analyses[a['symbol']] = {**a, 'best': dict(name=best['name'], status=best['status']) if best else None}
        near = sorted({r['symbol'] for r in rows
                       if abs(r['close'] / r['pivot'] - 1) * 100 <= self.BOARD_QUOTE_BAND})
        quotes, failed = {}, 0
        for i in range(0, len(near), 50):
            try:
                quotes.update(self.service.prices.quotes(near[i:i + 50])['quotes'])
            except ValueError:
                failed += 1
        today = clock().date()
        entries = evaluate(rows, analyses, quotes, today)
        now = clock().isoformat()
        self._track(entries, now)
        if self.board_started is None:
            self.board_started = now
        counts = {v: sum(e['verdict'] == v for e in entries) for v in ('ripe', 'watch', 'wait', 'skip')}
        return dict(entries=entries, counts=counts, time=now, market_open=market_open(),
                    events=list(self.board_events), tracking_since=self.board_started,
                    tracking=bool(self.board_thread and self.board_thread.is_alive()),
                    stocks=len({r['symbol'] for r in rows}), with_forecast=len(analyses),
                    quotes_ok=len(quotes), quotes_wanted=len(near), quotes_failed=failed)

    def ltp(self, symbols):
        """Last prices for option and stock contracts (open-position MTM). Up to 50 per call."""
        symbols = list(dict.fromkeys(s for s in symbols if s))
        if not 1 <= len(symbols) <= 50 or any(not s.startswith('NSE:') or len(s) > 60 for s in symbols):
            raise ValueError('Request 1 to 50 NSE contracts.')
        market = self.market
        if market.fallback is not None and time.monotonic() < market.fyers_down_until:
            return self._ltp_definedge(symbols)
        try:
            rows = market._get(QUOTES_URL, {'symbols': ','.join(symbols)}, True)
        except ValueError:
            if market.fallback is None:
                raise
            market.fyers_down_until = time.monotonic() + FYERS_COOLDOWN
            return self._ltp_definedge(symbols)
        out = {}
        for row in rows if isinstance(rows, list) else []:
            v = row.get('v') or {}
            if row.get('s') == 'ok' and number(v.get('lp')) > 0:
                out[row.get('n')] = dict(ltp=number(v['lp']), bid=number(v.get('bid')), ask=number(v.get('ask')),
                                         chp=number(v.get('chp')))
        return dict(quotes=out, time=clock().isoformat(), market_open=market_open())

    def _ltp_definedge(self, symbols):
        """Same shape as ltp(), priced by Definedge when FYERS cannot answer. Contracts it cannot price are omitted."""
        market = self.market
        try:
            market.universe()   # public instrument list, no login needed
        except ValueError:
            pass
        out, error = {}, None
        for symbol in symbols:
            try:
                item = market.fallback.definedge_item(symbol, market.by_symbol.get(symbol))
            except ValueError as exc:
                error = exc
                continue
            bids, asks = item.get('bids') or [], item.get('ask') or []
            out[symbol] = dict(ltp=item['ltp'], bid=bids[0]['price'] if bids else 0., ask=asks[0]['price'] if asks else 0.,
                               chp=0., source='Definedge')
        if not out and error:
            raise error
        return dict(quotes=out, time=clock().isoformat(), market_open=market_open(), source='Definedge')

    # ---------- pre-entry check: the same test the position monitor applies after entry ----------
    def preflight(self, request):
        """Test proposed legs against the saved Kronos paths exactly as the monitor would the minute after entry,
        so a warning appears before the trade rather than after it. Advice only; nothing is placed."""
        from .position_watch import evaluate, REFRESH_MINUTES
        options = self.service.options
        symbol = clean_symbol(request.get('symbol'))
        raw = request.get('legs')
        if not isinstance(raw, list) or not 1 <= len(raw) <= 6:
            raise ValueError('Add 1 to 6 legs.')
        self.market.universe()
        contracts, legs = [], []
        for item in raw:
            contract = self.market.by_symbol.get(str((item or {}).get('symbol', '')))
            lots = (item or {}).get('lots')
            if not contract or contract['underlying'] != symbol or type(lots) is not int or lots == 0 or abs(lots) > 50:
                raise ValueError('A leg is not valid for this stock. Reload the chain.')
            contracts.append({**contract, 'qty': lots})
        expiry = contracts[0]['expiry']
        if len({c['expiry'] for c in contracts}) > 1:
            raise ValueError('All legs must share one expiry.')
        key = options.journal.latest_forecast_id(symbol)
        analysis = options.journal.analysis(key) if key else None
        if not analysis or not (analysis.get('forecast') or {}).get('paths'):
            return dict(symbol=symbol, action=None, reasons=[], stale=False, forecast_age_min=None,
                        message='No saved Kronos forecast for this stock. Run one so this trade can be checked before you enter.')
        quotes = self.ltp([f'NSE:{symbol}-EQ'] + [c['symbol'] for c in contracts])['quotes']
        spot = number((quotes.get(f'NSE:{symbol}-EQ') or {}).get('ltp')) or number(analysis.get('spot'))
        for c in contracts:
            q = quotes.get(c['symbol']) or {}
            side = number(q.get('ask')) if c['qty'] > 0 else number(q.get('bid'))
            c['entry_fill'] = side
            c.update(bid=number(q.get('bid')),ask_price=number(q.get('ask')))
            if c['entry_fill'] <= 0:
                raise ValueError(f"No entry-side quote for {c['symbol']}.")
        credit = sum(-c['qty'] * c['lot'] * c['entry_fill'] for c in contracts)
        summary = payoff_summary([dict(type=c['type'], strike=c['strike'], qty=c['qty'], lot=c['lot']) for c in contracts], spot, credit, 0.)
        exit_by = str(datetime.fromisoformat(expiry).date() - timedelta(days=1))
        from .options import preferences
        prefs = preferences(options.journal.settings())
        buy = sum(c['entry_fill']*c['qty']*c['lot'] for c in contracts if c['qty']>0)
        sell = sum(-c['entry_fill']*c['qty']*c['lot'] for c in contracts if c['qty']<0)
        trade = dict(id='preflight', payload=dict(symbol=symbol, legs=contracts, prefs=prefs,
                                                  entry_charges=charges(buy,sell,len(contracts),prefs['brokerage']), exit_by=exit_by,
                                                  plan=dict(expiry=expiry, exit_by=exit_by, max_profit=summary['max_profit'],
                                                            max_loss=summary['max_loss'])))
        try:
            events = options.events(symbol, expiry)
        except Exception:
            events = None
        pool = [c for c in getattr(self.market, 'contracts', []) if c.get('underlying') == symbol and c.get('expiry') == expiry]
        v = evaluate(trade, analysis, spot, None, pool, events, None)
        made = datetime.fromisoformat(analysis.get('forecast_created') or analysis['created'])
        age = (datetime.now(made.tzinfo) - made).total_seconds() / 60
        stale = market_open() and age > REFRESH_MINUTES
        return dict(symbol=symbol, action=v['action'], reasons=v['reasons'], suggestion=v['suggestion'], stats=v['stats'],
                    stop=v['stop'], event=v['event'], event_before_exit=v['event_before_exit'], view=v['view'], spot=spot,
                    forecast_age_min=round(age, 1), stale=stale, refresh_minutes=REFRESH_MINUTES,
                    message='The forecast is older than the monitor refresh time: it will be re-run soon after entry and may change this advice. Refresh it first.' if stale else '')

    # ---------- paper trade from custom legs ----------
    def open_paper(self, request):
        options = self.service.options
        symbol = clean_symbol(request.get('symbol'))
        raw = request.get('legs')
        if not isinstance(raw, list) or not 1 <= len(raw) <= 6:
            raise ValueError('Add 1 to 6 legs.')
        self.market.universe()
        legs = []
        for item in raw:
            contract = self.market.by_symbol.get(str((item or {}).get('symbol', '')))
            lots = (item or {}).get('lots')
            if not contract or contract['underlying'] != symbol:
                raise ValueError('A leg does not belong to this stock. Reload the chain.')
            if type(lots) is not int or lots == 0 or abs(lots) > 50:
                raise ValueError('Lots must be a whole number from 1 to 50, buy or sell.')
            legs.append({**contract, 'qty': lots})
        if len({l['expiry'] for l in legs}) > 1:
            raise ValueError('All legs must share one expiry.')
        if len({l['symbol'] for l in legs}) != len(legs):
            raise ValueError('Combine repeated contracts into one net leg.')
        if clock().date() >= datetime.fromisoformat(legs[0]['expiry']).date():
            raise ValueError('Choose an expiry after today; physical settlement is not modelled.')
        from .options import preferences
        prefs = preferences(options.journal.settings())
        with options.action_lock:
            quotes = options._depths([l['symbol'] for l in legs])  # fresh two-sided books, market hours only
            spot = options.market.depth(f'NSE:{symbol}-EQ', require_fresh=False)
            buy = sell = 0.
            for leg, q in zip(legs, quotes):
                leg.update({k: q[k] for k in ('bid', 'ask_price', 'bid_qty', 'ask_qty', 'quote_time', 'source') if k in q},
                           ltp=number(q.get('ltp')))
                units = abs(leg['qty']) * leg['lot']
                try:   # market order: buys lift the asks, sells hit the bids, deeper levels if needed
                    leg['entry_fill'], leg['entry_levels'] = book_fill(q, leg['qty'], units)
                except ValueError as exc:
                    raise ValueError(f"{leg['symbol']}: {exc}") from None
                if leg['qty'] > 0:
                    buy += leg['entry_fill'] * units
                else:
                    sell += leg['entry_fill'] * units
            # One executed order per leg, as the broker would bill it.
            entry_charges = charges(buy, sell, len(legs), prefs['brokerage'])
            credit = round(sell - buy, 2)
            summary = payoff_summary(legs, number(spot.get('ltp')), credit, sum(entry_charges.values()))
            expiry = legs[0]['expiry']
            name = str(request.get('name') or 'Custom strategy')[:60]
            exit_by = str(datetime.fromisoformat(expiry).date() - timedelta(days=1))
            plan = dict(name=name, expiry=expiry, lot=legs[0]['lot'], legs=legs, entry_credit=credit,
                        exit_by=exit_by, underlying_stop=None, **summary)
            payload = dict(symbol=symbol, plan=plan, legs=legs, prefs=prefs, entry_spot=number(spot.get('ltp')),
                           entry_time=clock().isoformat(), exit_by=exit_by, entry_credit=credit,
                           entry_charges=entry_charges, research=False, accepted_warnings=[], source='desk', fill_model='market',
                           note='Market-order paper fill: buys lift the live asks, sells hit the live bids, walking deeper levels if needed. '
                                'Brokerage per executed order plus exchange, STT, SEBI, GST and stamp charges.')
            return options.journal.open('desk:' + uuid.uuid4().hex, 'custom', payload)
