"""Read-only Definedge cash prices and completed candles, with automatic login."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import io
import threading
import zipfile
import requests
from datalayer.sources.definedge import DefinedgeSource
from datalayer.util import cached_download
from .options_market import IST, LiveMarket, clock, market_open, number

QUOTE_URL = 'https://integrate.definedgesecurities.com/dart/v1/quotes/{exchange}/{token}'
MASTER_URL = 'https://app.definedgesecurities.com/public/allmaster.zip'


def depth_item(quote, now):
    """Definedge quote -> the same shape as a FYERS depth item. A missing book stays empty; nothing is invented."""
    def levels(side):
        out = []
        for i in range(1, 6):
            price, qty = number(quote.get(f'best_{side}_price{i}')), number(quote.get(f'best_{side}_qty{i}'))
            if price > 0:
                out.append(dict(price=price, volume=qty))
        return out
    stamp = 0.
    text = str(quote.get('last_trade_time') or '')
    if text and text != '00:00:00':
        try:
            clock_time = datetime.strptime(text, '%H:%M:%S').time()
            stamp = datetime.combine(now.date(), clock_time, IST).timestamp()
        except ValueError:
            pass
    price = number(quote.get('ltp'))
    return dict(ltp=price, ltt=stamp, o=number(quote.get('day_open')), h=number(quote.get('day_high')),
                l=number(quote.get('day_low')), v=number(quote.get('volume')),
                bids=levels('bid'), ask=levels('ask'))


class DefinedgeMarket(LiveMarket):
    name = 'Definedge'

    def __init__(self):
        super().__init__()
        self.source = DefinedgeSource()
        self.login_date = None
        self.instruments = {}
        self.auth_lock = threading.RLock()
        self.fallback = self  # this object is its own Definedge fallback

    def ready(self):
        with self.auth_lock:
            if self.login_date != clock().date():
                try:
                    self.source.authenticate()
                    self.instruments = self.source.instruments()
                except Exception:
                    raise ValueError('Definedge login or connection failed. Check the API login and retry Refresh prices.') from None
                self.login_date = clock().date()

    def daily_bars(self, symbol, start, end):
        try:
            self.ready()
        except ValueError:
            return super().daily_bars(symbol, start, end)
        ident = self.source.benchmark_id() if symbol == '__NIFTY50__' else self.instruments.get(symbol)
        if not ident:
            return super().daily_bars(symbol, start, end)
        try:
            return self.source.fetch_daily(ident, start, end, retries=1)
        except Exception:
            return super().daily_bars(symbol, start, end)

    def option_tokens(self):
        """(stock, expiry, strike, CE/PE) -> Definedge token, from today's instrument master."""
        today = clock().date()
        if getattr(self, 'option_index_date', None) != today:
            path = cached_download(MASTER_URL, 'definedge_allmaster.zip')
            index = {}
            with zipfile.ZipFile(path) as z, z.open('allmaster.csv') as f:
                for raw in io.TextIOWrapper(f, encoding='utf-8', errors='ignore'):
                    p = raw.strip().split(',')
                    if len(p) > 9 and p[0] == 'NFO' and p[4] == 'OPTSTK' and p[8] in ('CE', 'PE'):
                        try:
                            expiry = datetime.strptime(p[5], '%d%m%Y').date()
                            index[(p[2], str(expiry), float(p[9]) / 100, p[8])] = p[1]
                        except ValueError:
                            continue
            self.option_index, self.option_index_date = index, today
        return self.option_index

    def definedge_item(self, symbol, meta=None):
        """Live price and order book from Definedge for a FYERS-style symbol; used when FYERS cannot answer."""
        self.ready()
        if symbol.startswith('NSE:') and symbol.endswith('-EQ'):
            exchange, token = 'NSE', self.instruments.get(symbol[4:-3])
        elif meta:
            exchange = 'NFO'
            token = self.option_tokens().get((meta['underlying'], meta['expiry'], float(meta['strike']), meta['type']))
        else:
            exchange, token = 'NFO', None
        if not token:
            raise ValueError('Definedge has no matching instrument for this price.')
        for attempt in (1, 2):
            self.source._limiter.wait()
            try:
                response = requests.get(QUOTE_URL.format(exchange=exchange, token=token),
                                        headers={'Authorization': self.source.session_key}, timeout=(8, 25))
            except requests.RequestException:
                raise ValueError('Live data connection failed. No saved price will be substituted.') from None
            if response.status_code in (401, 403) and attempt == 1:
                self.login_date = None
                self.ready()
                continue
            break
        if response.status_code in (401, 403):
            raise ValueError('Definedge login expired or access denied. Renew the Definedge login.')
        if response.status_code == 429:
            raise ValueError('Definedge rate limit reached. Wait before refreshing.')
        try:
            data = response.json()
        except ValueError:
            raise ValueError('Definedge returned an unreadable price.') from None
        if data.get('status') != 'SUCCESS' or number(data.get('ltp')) <= 0:
            raise ValueError('Definedge could not supply a live price for this contract.')
        return depth_item(data, clock())

    def definedge_daily(self, symbol, start, end):
        """Completed daily candles from Definedge only (no FYERS retry)."""
        self.ready()
        ident = self.instruments.get(symbol)
        if not ident:
            raise ValueError('Definedge has no daily candles for this stock.')
        return self.source.fetch_daily(ident, start, end, retries=1)

    def cash_quote(self, symbol):
        self.ready()
        ident = self.instruments.get(symbol)
        if not ident:
            return None
        self.source._limiter.wait()
        try:
            response = requests.get(f'https://integrate.definedgesecurities.com/dart/v1/quotes/NSE/{ident}',
                headers={'Authorization': self.source.session_key}, timeout=(8,25))
            if response.status_code in (401,403):
                self.login_date = None
                raise ValueError('Definedge login expired. Retry Refresh prices to log in again.')
            response.raise_for_status()
            data = response.json()
        except requests.RequestException:
            raise ValueError('Definedge quote connection failed. Retry Refresh prices.') from None
        if data.get('status') != 'SUCCESS' or number(data.get('ltp')) <= 0:
            return None
        # REST returns only HH:MM:SS. Do not invent a trade date or allow a fill.
        return dict(symbol=symbol, ltp=number(data['ltp']), quote_time=None,
                    reported_trade_time=data.get('last_trade_time'), fresh=False,
                    fetched_at=clock().isoformat(), trade_age_seconds=None, source=self.name)

    def definedge_quotes(self, symbols):
        if not 1 <= len(symbols) <= 50:
            raise ValueError('Request between 1 and 50 symbols.')
        self.ready()
        with ThreadPoolExecutor(max_workers=6) as pool:
            values = list(pool.map(self.cash_quote, symbols))
        quotes = {q['symbol']:q for q in values if q}
        return dict(quotes=quotes, missing=[s for s in symbols if s not in quotes],
                    fetched_at=clock().isoformat(), market_open=market_open(), source=self.name)

    def quotes(self, symbols):
        # Prefer dated FYERS quotes when its login works. Definedge remains an
        # automatic-login fallback with the missing trade date clearly exposed.
        try:
            if len(symbols) == 1:
                q = self.preview_depth(f'NSE:{symbols[0]}-EQ')
                if number(q.get('ltp')) <= 0:
                    raise ValueError('FYERS LTP unavailable.')
                q.update(symbol=symbols[0], source=q.get('source','FYERS'), timestamp_kind='trade')
                return dict(quotes={symbols[0]:q}, missing=[], fetched_at=q['fetched_at'],
                            market_open=market_open(), source='FYERS')
            return super().quotes(symbols)
        except ValueError:
            return self.definedge_quotes(symbols)

    def eod_history(self, symbol):
        today = clock().date()
        end = today if clock().hour >= 16 else today-timedelta(days=1)
        return self.daily_bars(symbol, today-timedelta(days=300), end)
