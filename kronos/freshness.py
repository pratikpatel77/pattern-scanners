"""Refresh completed scanner inputs in the background; leave original reports intact."""
from datetime import timedelta
import json
import threading
import time
from .data import signal_dict
from .options_market import clock

CLOSE_HOUR = 16        # IST: the day's candles are final and the scan can run
WATCH_EVERY = 300      # seconds between "is a scan due?" checks
RETRY_AFTER = 1800     # seconds before an unfinished or failed scan is started again on its own


class MarketRefresh:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.state = dict(status='idle', message='Prices have not been checked yet.', revision=None)
        self.attempted = None      # monotonic time of the last scan start, from the browser or from watch()
        self.watcher = None

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
                self.attempted = time.monotonic()
                self.thread = threading.Thread(target=self.run, name='market-refresh', daemon=True)
                self.thread.start()
            return dict(self.state)

    # ---------- keep patterns current without a browser ----------
    def due(self, now=None):
        """True when no full scan has finished since the latest 16:00 IST close of a trading weekday.
        The marker is written only by a finished scan, so the early F&O publish does not count."""
        now = now or clock()
        cutoff = now.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
        if now < cutoff:
            cutoff -= timedelta(days=1)
        while cutoff.weekday() >= 5:
            cutoff -= timedelta(days=1)
        try:
            return (self.service.directory/'patterns.done').stat().st_mtime < cutoff.timestamp()
        except OSError:
            return True

    def watch(self):
        """Start a scan on its own at launch and after each close, so F&O patterns stay current while the app runs."""
        with self.lock:
            if self.watcher is not None and self.watcher.is_alive():
                return
            self.watcher = threading.Thread(target=self._watch_loop, name='market-refresh-watch', daemon=True)
            self.watcher.start()

    def _watch_loop(self):
        delay = 20.
        while not self.stop.wait(delay):
            delay = WATCH_EVERY
            try:
                idle = self.thread is None or not self.thread.is_alive()
                recent = self.attempted is not None and time.monotonic() - self.attempted < RETRY_AFTER
                if idle and not recent and self.due():
                    self.start()
            except Exception:      # tracking must never stop the app
                pass

    def _publish_partial(self, signals, covered):
        """Make the first batch of stocks (F&O) visible at once. The full scan replaces this file when it ends."""
        try:
            path = self.service.directory/'setups.json'
            kept = [s for s in self.service.setups() if str(s.get('symbol', '')).upper() not in covered]
            temp = path.with_suffix('.partial.tmp')
            temp.write_text(json.dumps(kept + [signal_dict(s) for s in signals], allow_nan=False), encoding='utf-8')
            temp.replace(path)
        except (OSError, ValueError, TypeError):
            pass

    def run(self):
        service = self.service
        market, provider = service.market, service.prices
        try:
            current = clock()
            end = current.date() if current.hour >= 16 else current.date()-timedelta(days=1)
            # The API's benchmark candles resolve weekends and exchange holidays.
            benchmark = market.refresh_symbol('__NIFTY50__', provider, end)
            session = benchmark.index[-1].date()
            try:
                fno = set(service.desk.fno_symbols())
            except Exception:      # no F&O list yet (login): scan in the usual order
                fno = set()
            symbols = sorted(market.symbols(), key=lambda name: name not in fno)   # F&O first
            first_batch = sum(name in fno for name in symbols)
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
                            for signal in market.detect(symbol, tf, direction='all'):
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
                if first_batch and i+1 == first_batch:
                    self._publish_partial(signals, set(symbols[:first_batch]))
            market.save_live()
            from report import write_html
            path = service.directory/'scanner.html'
            temp = path.with_suffix('.tmp')
            from scanners import ALL_SCANNERS
            write_html(temp, signals, list(ALL_SCANNERS), ['D','W'],
                       dict(source='API completed candles', scanned=len(symbols)-len(errors), universe='Existing scanner universe',
                            generated=current.strftime('%d %b %Y, %H:%M IST'), asof=str(session),
                            min_turnover=2., delivery='not refreshed'))
            setups = service.directory/'setups.json'
            setup_temp = setups.with_suffix('.refresh.tmp')
            setup_temp.write_text(json.dumps([signal_dict(s) for s in signals], allow_nan=False), encoding='utf-8')
            setup_temp.replace(setups)
            temp.replace(path)
            (service.directory/'patterns.done').touch()
            self.update(status='partial' if errors else 'complete', message=f'Patterns updated through {session}. {len(errors)} stocks unavailable. LTP is fetched separately.',
                        revision=clock().isoformat(), errors=errors)
        except Exception as exc:
            market.save_live()
            message = str(exc) if isinstance(exc, ValueError) else 'Market refresh failed. Retry Refresh prices.'
            self.update(status='failed', message=message)

    def close(self):
        self.stop.set()
