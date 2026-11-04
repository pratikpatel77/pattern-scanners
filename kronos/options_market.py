"""Read-only, fresh FYERS data. No order endpoints and no cached option prices."""
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import math
import threading
import time

import pandas as pd
import requests

IST = timezone(timedelta(hours=5, minutes=30))
FYERS_COOLDOWN = 20.


def clock():
    return datetime.now(IST)


def number(value, default=0.):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


_live_seen = 0.


def note_live(age):
    """Record that a broker quote traded within the last 30s: the exchange is trading right now."""
    global _live_seen
    if age is not None and 0 <= age <= 30:
        _live_seen = time.time()


def market_open(stamp=None):
    stamp = stamp or clock()
    if stamp.weekday() >= 5:
        return False
    minute = stamp.hour * 60 + stamp.minute
    if 555 <= minute < 930:
        return True
    # Outside the scheduled hours, a broker quote that traded seconds ago (late open, extended session) still proves it is live.
    return 540 <= minute < 960 and time.time() - _live_seen <= 60


class LiveMarket:
    name = 'FYERS'
    def __init__(self):
        self.session = requests.Session()
        self.lock = threading.RLock()
        self.last_request = 0.
        self.contracts = []
        self.master_date = None
        self.chain_cache = {}
        self.expiry_cache = {}
        self.by_symbol = {}
        # Permanent rule: when FYERS cannot supply a price (rate limit, expired login, outage), the
        # Definedge API supplies it. After a FYERS failure, Definedge serves for FYERS_COOLDOWN seconds
        # before FYERS is tried again, so a rate-limited FYERS is not hammered.
        self.fallback = None
        self.fyers_down_until = 0.

    def _get(self, url, params=None, auth=False):
        headers = {'User-Agent': 'Kronos-Options/1.0'}
        if auth:
            from datalayer import config
            app, token = config.get('FYERS_APP_ID'), config.get('FYERS_ACCESS_TOKEN')
            if not app or not token:
                raise ValueError('FYERS login is missing. Refresh the existing FYERS login first.')
            headers['Authorization'] = f'{app}:{token}'
        # Only the start of each request is spaced out (about 8 per second, inside FYERS limits);
        # the requests themselves run in parallel.
        with self.lock:
            delay = .12 - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
        try:
            response = self.session.get(url, params=params, headers=headers, timeout=(8, 25))
            if response.status_code in (401, 403):
                raise ValueError('FYERS login expired or access denied. Renew the FYERS login, then click Refresh prices.')
            if response.status_code == 429:
                raise ValueError('FYERS rate limit reached. Wait before refreshing.')
            if response.status_code in (400, 404, 422):
                raise ValueError('FYERS has no market data for this symbol or date range.')
            response.raise_for_status()
            if not auth:
                return response.text
            result = response.json()
            if result.get('s') != 'ok':
                if result.get('code') in (-8, -15, -16, -17):
                    raise ValueError('FYERS login expired or invalid. Renew the FYERS login, then click Refresh prices.')
                raise ValueError('FYERS could not supply live data. Check login, permissions and subscription.')
            return result.get('data', result.get('d', result))
        except (requests.RequestException, requests.exceptions.JSONDecodeError):
            raise ValueError('Live data connection failed. No saved price will be substituted.') from None

    def universe(self):
        with self.lock:
            if self.master_date != clock().date():
                cash = self._get('https://public.fyers.in/sym_details/NSE_CM.csv')
                equities = {r[9][4:-3] for r in csv.reader(io.StringIO(cash))
                            if len(r) > 9 and r[9].startswith('NSE:') and r[9].endswith('-EQ')}
                text = self._get('https://public.fyers.in/sym_details/NSE_FO.csv')
                contracts = []
                for row in csv.reader(io.StringIO(text)):
                    if len(row) < 17 or row[16] not in ('CE', 'PE') or row[13] not in equities:
                        continue
                    try:
                        expiry = datetime.fromtimestamp(float(row[8]), IST).date()
                        lot = int(row[3])
                        strike = float(row[15])
                    except (ValueError, OSError):
                        continue
                    if expiry >= clock().date() and lot > 0 and strike > 0:
                        contracts.append(dict(symbol=row[9], underlying=row[13], expiry=str(expiry),
                                              lot=lot, strike=strike, type=row[16], tick=number(row[4], .05)))
                if not contracts:
                    raise ValueError('No valid stock options in the current FYERS instrument list.')
                self.contracts, self.master_date = contracts, clock().date()
                self.by_symbol = {c['symbol']: c for c in contracts}
        return sorted({c['underlying'] for c in self.contracts})

    def depth(self, symbol, require_fresh=True):
        source = 'FYERS'
        item = None
        if self.fallback is not None and time.monotonic() < self.fyers_down_until:
            item, source = self.fallback.definedge_item(symbol, self.by_symbol.get(symbol)), 'Definedge'
        if item is None:
            try:
                data = self._get('https://api-t1.fyers.in/data/depth', {'symbol': symbol, 'ohlcv_flag': 1}, True)
                item = data.get(symbol, {})
            except ValueError:
                if self.fallback is None:
                    raise
                self.fyers_down_until = time.monotonic() + FYERS_COOLDOWN
                item, source = self.fallback.definedge_item(symbol, self.by_symbol.get(symbol)), 'Definedge'
        stamp = number(item.get('ltt'))
        if stamp > 1e12:
            stamp /= 1000
        age = clock().timestamp() - stamp
        note_live(age if stamp else None)
        bids, asks = item.get('bids') or [], item.get('ask') or []
        bid = number(bids[0].get('price')) if bids else 0
        ask = number(asks[0].get('price')) if asks else 0
        # The order book is fetched live, so a rarely traded strike is still tradable. Fresh means:
        # market hours and a real two-sided book. The last-trade age is shown, not required.
        fresh = market_open() and 0 < bid <= ask
        if require_fresh and not fresh:
            raise ValueError('Market closed or no two-sided order book for this contract. Fresh prices required.')
        return {**item, 'symbol': symbol, 'quote_time': stamp, 'fetched_at': clock().isoformat(),
                'source': source, 'fresh': fresh, 'trade_age_seconds': round(age, 1) if stamp else None,
                'bid': bid,
                'ask_price': ask,
                'bid_qty': number(bids[0].get('volume')) if bids else 0,
                'ask_qty': number(asks[0].get('volume')) if asks else 0}

    def depth_many(self, symbols, require_fresh=True):
        """Live books for several contracts at once, in the order given. The first failure is raised."""
        symbols = list(symbols)
        if len(symbols) < 2:
            return [self.depth(s, require_fresh) for s in symbols]
        with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as pool:
            return list(pool.map(lambda s: self.depth(s, require_fresh), symbols))

    def preview_depth(self, symbol):
        """Show the last reported trade with its age; never authorize a stale fill."""
        return self.depth(symbol, require_fresh=False)

    def quotes(self, symbols):
        """Uncached snapshots. Missing prices and trade times stay missing."""
        symbols = list(dict.fromkeys(symbols))
        if not 1 <= len(symbols) <= 50:
            raise ValueError('Request between 1 and 50 symbols.')
        rows = self._get('https://api-t1.fyers.in/data/quotes',
                         {'symbols': ','.join(f'NSE:{s}-EQ' for s in symbols)}, True)
        fetched = clock()
        result = {}
        for row in rows:
            symbol = str(row.get('n', ''))
            if not symbol.startswith('NSE:') or not symbol.endswith('-EQ'):
                continue
            symbol = symbol[4:-3]
            if symbol not in symbols or row.get('s') != 'ok':
                continue
            value = row.get('v') or {}
            price = number(value.get('lp'))
            if price <= 0:
                continue
            stamp = number(value.get('tt'))
            if stamp > 1e12:
                stamp /= 1000
            age = fetched.timestamp() - stamp if stamp else None
            note_live(age)
            result[symbol] = dict(symbol=symbol, ltp=price, change_pct=number(value.get('chp')),
                quote_time=stamp or None, fetched_at=fetched.isoformat(),
                source='FYERS', timestamp_kind='quote',
                fresh=market_open(fetched) and age is not None and 0 <= age <= 30,
                trade_age_seconds=round(age, 1) if age is not None else None)
        return dict(quotes=result, missing=[s for s in symbols if s not in result],
                    fetched_at=fetched.isoformat(), market_open=market_open(fetched), source='FYERS')

    def daily_bars(self, symbol, start, end):
        """Completed history for scanner inputs, chunked at the daily API limit."""
        ident = 'NSE:NIFTY50-INDEX' if symbol == '__NIFTY50__' else f'NSE:{symbol}-EQ'
        rows = []
        while start <= end:
            until = min(end, start + timedelta(days=364))
            data = self._get('https://api-t1.fyers.in/data/history',
                dict(symbol=ident, resolution='D', date_format='1', range_from=str(start),
                     range_to=str(until), cont_flag='1'), True)
            rows.extend(r[:6] for r in data.get('candles', []))
            start = until + timedelta(days=1)
        if not rows:
            raise ValueError('FYERS returned no completed daily candles.')
        frame = pd.DataFrame(rows, columns=['date','open','high','low','close','volume'])
        frame.index = pd.to_datetime(frame.pop('date'), unit='s', utc=True).dt.tz_convert(IST).dt.tz_localize(None).dt.normalize()
        return frame.loc[frame.index.date <= end].sort_index().loc[lambda f: ~f.index.duplicated(keep='last')]

    def eod_history(self, symbol):
        return self.history(symbol,None,completed=True)

    def history(self, symbol, spot, completed=False):
        today = clock().date()
        # Allow time after the normal close for the provider's daily candle to settle.
        end=today if completed and clock().hour>=16 else today-timedelta(days=1)
        try:
            data = self._get('https://api-t1.fyers.in/data/history',
                             dict(symbol=f'NSE:{symbol}-EQ', resolution='D', date_format='1',
                                  range_from=str(today-timedelta(days=300)), range_to=str(end),
                                  cont_flag='1'), True)
            rows = data.get('candles', [])
            if not rows:
                raise ValueError('Fresh forecast inputs unavailable from FYERS.')
            frame = pd.DataFrame([r[:6] for r in rows], columns=['date','open','high','low','close','volume'])
            frame.index = pd.to_datetime(frame.pop('date'), unit='s', utc=True).dt.tz_convert(IST).dt.tz_localize(None).dt.normalize()
        except ValueError:
            if self.fallback is None:
                raise
            frame = self.fallback.definedge_daily(symbol, today-timedelta(days=300), end)
        frame = frame.loc[frame.index.date <= end].sort_index()
        if len(frame) < 60 or (today-frame.index[-1].date()).days > 5:
            raise ValueError('Daily candles are missing or too old for a live forecast.')
        if completed:
            return frame
        price = number(spot.get('ltp'))
        if price <= 0 or number(spot.get('o')) <= 0:
            raise ValueError('Underlying live OHLC data is missing.')
        # Today's forming candle is explicit; the forecast starts after today.
        frame.loc[pd.Timestamp(today)] = [number(spot['o']), max(price, number(spot.get('h'))),
            min(price, number(spot.get('l', spot.get('i')), price)), price, number(spot.get('v'))]
        return frame

    def chain(self, symbol, forecast_end):
        # Contract identity barely changes; share one chain between repeated edits for 30 seconds.
        key = (symbol, str(forecast_end))
        hit = self.chain_cache.get(key)
        if hit and time.monotonic() - hit[0] < 30:
            return [dict(o) for o in hit[1]]
        options = self._chain(symbol, forecast_end)
        self.chain_cache[key] = (time.monotonic(), options)
        return [dict(o) for o in options]

    def _chain(self, symbol, forecast_end):
        self.universe()
        end = pd.Timestamp(forecast_end).date()
        choices = sorted({c['expiry'] for c in self.contracts if c['underlying'] == symbol
                          and end + timedelta(days=3) <= pd.Timestamp(c['expiry']).date() <= clock().date()+timedelta(days=60)})
        if not choices:
            raise ValueError('No expiry safely beyond the forecast period within 60 days.')
        expiry = choices[0]
        # Resolve the API's exact expiry timestamp, rather than guessing it.
        day_key = (symbol, clock().date())
        dates = self.expiry_cache.get(day_key)
        if dates is None:
            initial = self._get('https://api-t1.fyers.in/data/options-chain-v3',
                                dict(symbol=f'NSE:{symbol}-EQ', strikecount=1, timestamp='', greeks='1'), True)
            dates = self.expiry_cache[day_key] = initial.get('expiryData', [])
        match = next((e for e in dates if str(datetime.fromtimestamp(int(e['expiry']), IST).date()) == expiry), None)
        if not match:
            raise ValueError('Instrument and option-chain expiries do not match.')
        stamp = match['expiry']
        data = self._get('https://api-t1.fyers.in/data/options-chain-v3',
                         dict(symbol=f'NSE:{symbol}-EQ', strikecount=30, timestamp=stamp, greeks='1'), True)
        master = {c['symbol']: c for c in self.contracts if c['underlying'] == symbol and c['expiry'] == expiry}
        options = []
        for row in data.get('optionsChain', []):
            contract = master.get(row.get('symbol'))
            if contract:
                options.append({**contract, 'iv': number((row.get('greeks') or {}).get('iv', row.get('iv'))),
                    'ltp':number(row.get('ltp')),
                    'bid': number(row.get('bid')), 'ask_price': number(row.get('ask')),
                    'oi': number(row.get('oi')), 'v': number(row.get('volume'))})
        if len(options) < 6:
            raise ValueError('Too few option quotes for this stock and expiry.')
        return options
