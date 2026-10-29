"""Daily cache watcher. Reforecast changed inputs; never train or place orders."""
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import threading
import pandas as pd
from .data import IST
from .storage import now


class DailyMonitor:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        with service.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS daily_watch (symbol TEXT PRIMARY KEY, state TEXT NOT NULL)')

    def rows(self):
        with self.service.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT state FROM daily_watch ORDER BY symbol')]

    def save(self, row):
        with self.service.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO daily_watch VALUES (?,?)', (row['symbol'], json.dumps(row)))

    def configure(self, request):
        symbol = str(request.get('symbol', '')).strip().upper()
        if symbol not in self.service.market.symbols():
            raise ValueError('Choose a cached symbol first.')
        if type(request.get('enabled')) is not bool:
            raise ValueError('Choose whether daily watching is enabled.')
        with self.lock:
            rows = self.rows()
            row = next((r for r in rows if r['symbol'] == symbol), None)
            if row is None:
                if len(rows) >= 30:
                    raise ValueError('The daily watchlist supports up to 30 stocks.')
                row = dict(symbol=symbol, run=None, last_success=None, fingerprint=None,
                           message='Waiting for the first daily check.', alert='', checked=None)
            row['enabled'] = request['enabled']
            self.save(row)
        return self.rows()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.loop, name='kronos-daily-watch', daemon=True)
            self.thread.start()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                # Cache may be in the middle of a scanner write. Retry next minute.
                pass
            self.stop.wait(60)

    def tick(self):
        with self.lock:
            for row in self.rows():
                if not row['enabled']:
                    continue
                row['checked'] = now()
                try:
                    self.check(row)
                except Exception:
                    row['message'] = 'Could not read current data. Will check again next minute.'
                self.save(row)

    def check(self, row):
        service = self.service
        if row.get('run'):
            run = service.store.get(row['run'])
            if run['status'] in ('queued', 'running', 'cancelling'):
                row['message'] = run['message']
                return
            if run['status'] == 'complete' and row['run'] != row.get('last_success'):
                result = run['result']
                previous = service.store.get(row['last_success'])['result'] if row.get('last_success') else None
                row['alert'] = describe_change(previous, result)
                row['last_success'] = row['run']
            elif run['status'] != 'complete':
                row['message'] = 'Forecast ' + run['status'] + '. Retry with Analyze prediction; automatic retry waits for changed inputs.'
        bars = service.market.bars(row['symbol'], 'D')
        row['data_date'] = str(bars.index[-1].date())
        if row.get('last_success'):
            comparison = service.run(row['last_success'])['result']['comparison']
            row['quality'] = comparison
            row['quality_origin'] = service.store.get(row['last_success'])['result']['asof']
            # Keep scoring earlier snapshots even after a fresh forecast replaces them.
            scored = []
            for saved in service.store.list(limit=1000):
                if (saved['kind'] == 'forecast' and saved['status'] == 'complete'
                        and saved['request'].get('monitor')
                        and saved['request']['symbol'] == row['symbol']):
                    check = service.run(saved['id'])['result']['comparison']
                    if check['available']:
                        scored.append((check, service.store.get(saved['id'])['result']['asof']))
            if scored:
                row['quality'], row['quality_origin'] = scored[0]
            complete = [c for c, origin in scored if c['complete']]
            row['completed_forecasts'] = len(complete)
            row['baseline_wins'] = sum(c['metrics']['mae'] < c['metrics']['baseline_mae'] for c in complete)
        age = (datetime.now(IST).date() - bars.index[-1].date()).days
        if age > 4:
            row['message'] = 'Prices are old. Run the original scanner to fetch fresh bars.'
            return
        settings = replace(service.store.settings(), timeframe='D')
        payload = pd.util.hash_pandas_object(bars.tail(settings.lookback), index=True).values.tobytes()
        fingerprint = hashlib.sha256(payload + json.dumps(settings.to_dict(), sort_keys=True).encode()).hexdigest()
        if fingerprint == row.get('fingerprint'):
            if not row.get('run') or service.store.get(row['run'])['status'] == 'complete':
                row['message'] = 'Watching for a new completed daily candle.'
            return
        if service.active is not None:
            row['message'] = 'New inputs ready. Waiting for the current job.'
            return
        run = service.submit({'kind':'forecast', 'symbol':row['symbol'], 'scanner':'all',
                              'settings':settings.to_dict(), 'asof':row['data_date'],
                              'monitor':True})
        row.update(run=run['id'], fingerprint=fingerprint, message='New daily forecast queued.')

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


def describe_change(previous, current):
    if previous is None:
        return 'First daily forecast saved. Quality needs later actual prices.'
    if previous['settings'] != current['settings']:
        return 'Forecast settings changed. New result starts a fresh comparison.'
    old, new = previous['summary'], current['summary']
    changes = []
    if old['bias'] != new['bias']:
        changes.append(old['bias'] + ' → ' + new['bias'])
    delta = new['upside_rate'] - old['upside_rate']
    if abs(delta) >= 15:
        changes.append(f'Upside sample share changed {delta:+.1f} percentage points')
    if bool(previous.get('pattern')) != bool(current.get('pattern')):
        changes.append('Pattern detected' if current.get('pattern') else 'Previous pattern no longer detected')
    return '. '.join(changes) if changes else 'Forecast updated; no large outlook change. Sample shares are uncalibrated.'
