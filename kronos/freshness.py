"""Refresh completed scanner inputs in the background; leave original reports intact."""
from datetime import timedelta
import json
import threading
from .data import signal_dict
from .options_market import clock


class MarketRefresh:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.state = dict(status='idle', message='Prices have not been checked yet.', revision=None)

    def status(self):
        with self.lock:
            return dict(self.state)

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.state = dict(status='running', message='Checking Definedge for the latest completed session.',
                                  revision=self.state.get('revision'), done=0, total=0)
                self.thread = threading.Thread(target=self.run, name='market-refresh', daemon=True)
                self.thread.start()
            return dict(self.state)

    def run(self):
        service = self.service
        market, provider = service.market, service.prices
        try:
            current = clock()
            end = current.date() if current.hour >= 16 else current.date()-timedelta(days=1)
            # The API's benchmark candles resolve weekends and exchange holidays.
            benchmark = market.refresh_symbol('__NIFTY50__', provider, end)
            session = benchmark.index[-1].date()
            symbols = market.symbols()
            self.update(total=len(symbols), session=str(session))
            errors, signals = [], []
            from scanners.common import liquid_enough
            from analysis import analyse
            for i, symbol in enumerate(symbols):
                if self.stop.is_set():
                    return
                try:
                    cached = market.daily(symbol)
                    if cached.index[-1].date() < session:
                        market.refresh_symbol(symbol, provider, end, persist=False)
                    daily = market.daily(symbol)
                    if daily.index[-1].date() < session:
                        raise ValueError('Latest session candle is missing.')
                    if liquid_enough(daily, 2.):
                        for tf in ('D', 'W'):
                            for signal in market.detect(symbol, tf):
                                signal.analysis = analyse(signal, market.bars(symbol, tf))
                                signals.append(signal)
                except ValueError as exc:
                    # Do not keep making requests after a global login/network failure.
                    if any(word in str(exc).lower() for word in ('login','access denied','connection failed','rate limit')):
                        raise
                    errors.append(dict(symbol=symbol, error=str(exc)))
                if (i+1) % 50 == 0:
                    market.save_live()
                self.update(done=i+1, message=f'Updating candles and patterns: {i+1}/{len(symbols)} stocks.')
            market.save_live()
            from report import write_html
            path = service.directory/'scanner.html'
            temp = path.with_suffix('.tmp')
            write_html(temp, signals, ['stage','darvas','cup'], ['D','W'],
                       dict(source='API completed candles', scanned=len(symbols)-len(errors), universe='Existing scanner universe',
                            generated=current.strftime('%d %b %Y, %H:%M IST'), asof=str(session),
                            min_turnover=2., delivery='not refreshed'))
            setups = service.directory/'setups.json'
            setup_temp = setups.with_suffix('.refresh.tmp')
            setup_temp.write_text(json.dumps([signal_dict(s) for s in signals], allow_nan=False), encoding='utf-8')
            setup_temp.replace(setups)
            temp.replace(path)
            self.update(status='partial' if errors else 'complete', message=f'Patterns updated through {session}. {len(errors)} stocks unavailable. LTP is fetched separately.',
                        revision=clock().isoformat(), errors=errors)
        except Exception as exc:
            market.save_live()
            message = str(exc) if isinstance(exc, ValueError) else 'Market refresh failed. Retry Refresh prices.'
            self.update(status='failed', message=message)

    def close(self):
        self.stop.set()
