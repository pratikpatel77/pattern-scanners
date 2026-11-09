"""Keep a current Kronos forecast on every F&O stock that is close to entering, so Ready needs no manual step.

One stock at a time, through the same job slot and code path as a person clicking "Run Kronos forecast".
A person always wins: their request cancels the background run and the watcher stays out of the way for a while.
It only forecasts; it never places or suggests an order."""
import threading
import time

from .options_market import market_open

IDLE_OPEN = 30        # seconds between checks while the market is open
IDLE_CLOSED = 120     # and while it is closed (prices cannot change, so the board needs less polling)
STOCK_RETRY = 15 * 60  # seconds before the same stock is tried again after any attempt
FAIL_BACKOFF = 10 * 60  # seconds every stock waits after a run that failed outright (login, data, model)
YIELD_AFTER_PERSON = 90  # seconds to stay idle after a person's own request pre-empted a background run
MAX_WAIT = 15 * 60    # a single forecast that takes longer than this is abandoned (the job keeps its own record)
DONE = ('complete', 'failed', 'cancelled', 'interrupted')


class AutoForecast:
    def __init__(self, service):
        self.service = service
        self.stop = threading.Event()
        self.thread = None
        self.tried = {}            # symbol -> monotonic time of the last attempt
        self.pause_until = 0.
        self.delay = 5.
        self.state = dict(running=None, queued=0, message='Starting.', last=None)

    def status(self):
        return dict(self.state)

    # ---------- what to forecast ----------
    def candidates(self, entries, now):
        """Stocks whose pattern is at or near its trigger and whose Kronos forecast is missing or out of date,
        best-ranked first. Stocks far from their trigger wait: a forecast made now would be stale by then."""
        picked = []
        for e in entries:
            if e['state'] not in ('near', 'triggered') or e['symbol'] in picked:
                continue
            if (e['kronos'] or {}).get('fresh'):
                continue
            if now - self.tried.get(e['symbol'], -STOCK_RETRY) < STOCK_RETRY:
                continue
            picked.append(e['symbol'])
        return picked

    # ---------- one unit of work ----------
    def step(self, now=None):
        """Run at most one forecast. Returns True when it ran one (so the loop continues straight away)."""
        from .service import BusyError
        now = time.monotonic() if now is None else now
        service = self.service
        if now < self.pause_until:
            return False
        if service.active is not None:
            self.state.update(running=None, message='Waiting for the current job to finish.')
            return False
        todo = self.candidates(service.desk.entry_board()['entries'], now)
        self.state.update(running=None, queued=len(todo))
        if not todo:
            self.state['message'] = 'Every F&O stock near its trigger has a current forecast.'
            return False
        symbol = todo[0]
        self.tried[symbol] = now
        try:
            job = service.options.submit(dict(symbol=symbol), auto=True)
        except BusyError:
            self.state['message'] = 'Waiting for the current job to finish.'
            return False
        except ValueError as exc:      # e.g. the stock left the F&O list
            self.state['message'] = f'{symbol}: {exc}'
            return False
        self.state.update(running=symbol, message=f'Running Kronos for {symbol}.')
        job = self._wait(job['id'])
        self.state['running'] = None
        status = job['status']
        if status == 'cancelled':       # a person asked for something else; retry this stock later
            self.tried.pop(symbol, None)
            self.pause_until = time.monotonic() + YIELD_AFTER_PERSON
            self.state['message'] = 'Paused: you started your own forecast.'
        elif status in ('failed', 'interrupted'):
            self.pause_until = time.monotonic() + FAIL_BACKOFF
            self.state['message'] = f"Paused for {FAIL_BACKOFF // 60} min: {job.get('error') or 'the forecast could not finish'}"
        elif status == 'complete':
            row = next(iter((job.get('result') or {}).get('stocks') or []), {})
            self.state['message'] = (f'Forecast saved for {symbol}.' if row.get('has_forecast') else
                                     f"{symbol}: no forecast saved ({row.get('reason') or 'unknown reason'}). Trying again in {STOCK_RETRY // 60} min.")
        else:                           # app closing, or the run is taking too long
            self.state['message'] = f'Stopped waiting for {symbol}.'
        self.state['last'] = dict(symbol=symbol, status=status, time=time.time())
        return True

    def _wait(self, key):
        store, deadline = self.service.store, time.monotonic() + MAX_WAIT
        while True:
            job = store.get(key)
            if job['status'] in DONE or self.stop.is_set() or time.monotonic() > deadline:
                return job
            time.sleep(1)

    # ---------- background loop ----------
    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, name='auto-forecast', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.wait(self.delay):
            try:
                ran = self.step()
                self.delay = .5 if ran else (IDLE_OPEN if market_open() else IDLE_CLOSED)
            except Exception as exc:     # tracking must never stop the app
                self.state.update(running=None, message=str(exc) if isinstance(exc, ValueError) else f'Auto-forecast error: {type(exc).__name__}')
                self.delay = 60

    def close(self):
        self.stop.set()
