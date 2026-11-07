"""Sensibull open-interest bridge: anonymous, read-only, polite.

Sensibull's public OI pages load without a login through an anonymous session. This module asks for the
same JSON the pages ask for, stores every snapshot locally (so intraday OI change and replay can be
computed from our own history) and never places orders. On the first 403/429 it backs off for 15 minutes.
"""
from datetime import datetime, timedelta
import math
import json
import re
import threading
import time
import uuid
import requests
from .options_market import clock, market_open

BASE = 'https://oxide.sensibull.com'
IDENTIFY = BASE + '/v1/pluto/auth/web/session/a/platform/identify'
OI_CHART = BASE + '/v1/compute/2/oi_graphs/oi_chart'
OI_VS_TIME = BASE + '/v1/compute/2/oi_graphs/oi_vs_time_chart'
FUT_OI_VS_TIME = BASE + '/v1/compute/2/oi_graphs/fut_oi_vs_time_chart'
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Origin': 'https://web.sensibull.com', 'Referer': 'https://web.sensibull.com/'}
POLL_SECONDS = 180          # the exchange refreshes stock OI about every three minutes
MIN_GAP = 1.5               # seconds between two requests, one at a time
COOLDOWN = 900              # after a 403/429
LIVE_TTL = 60               # one stock is never fetched twice within a minute
SERIES_TTL = 1800           # daily history changes once a day
DEFAULT_WATCH = ('NIFTY', 'BANKNIFTY')
SYMBOL = re.compile(r'[A-Z0-9&_-]{1,40}')
SERIES = {'oi': OI_VS_TIME, 'fut': FUT_OI_VS_TIME}


def clean_symbol(value):
    symbol = str(value or '').strip().upper()
    if not SYMBOL.fullmatch(symbol):
        raise ValueError('Choose a valid NSE symbol.')
    return symbol


def finite(value, nonnegative=False):
    if isinstance(value, bool):
        raise ValueError('Invalid OI number.')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Missing or invalid OI number.') from None
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError('Invalid OI number.')
    return result


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise ValueError('OI exchange time is missing or invalid.') from None


def parse_oi(payload, symbol):
    """Reject incomplete chains rather than manufacture zero OI or false changes."""
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise ValueError('Sensibull did not return OI data.')
    p = payload.get('payload')
    rows = p.get('per_strike_data') if isinstance(p, dict) else None
    if not isinstance(rows, dict) or not rows:
        raise ValueError('Sensibull OI format changed.')
    strikes = {}
    previous = not bool(p.get('prev_oi_unavailable'))
    for strike, row in rows.items():
        k = finite(strike, True)
        if k <= 0 or not isinstance(row, dict):
            raise ValueError('Invalid OI strike.')
        values = []
        for field in ('call_oi', 'put_oi', 'prev_call_oi', 'prev_put_oi'):
            raw = row.get(field)
            if raw is None and field.startswith('prev_'):
                previous = False
                values.append(None)
                continue
            value = finite(raw, True)
            if value != int(value):
                raise ValueError('OI must be a whole number.')
            values.append(int(value))
        key = format(k, '.12g')
        if key in strikes:
            raise ValueError('Duplicate OI strike.')
        strikes[key] = values
    inp = p.get('input')
    if not isinstance(inp, dict):
        raise ValueError('OI input scope is missing.')
    catalog = inp.get('expiries')
    if not isinstance(catalog, dict) or any(not isinstance(v, dict) for v in catalog.values()):
        raise ValueError('OI expiry catalog is invalid.')
    expiries = sorted(k for k, v in catalog.items() if isinstance(v, dict) and v.get('is_enabled'))
    if not expiries:
        raise ValueError('OI expiry scope is missing.')
    for day in [inp.get('date'), *catalog]:
        datetime.strptime(str(day), '%Y-%m-%d')
    stamp = timestamp(p.get('intraday_last_available_timestamp'))
    if stamp.astimezone(clock().tzinfo).date().isoformat() != inp['date']:
        raise ValueError('OI date and exchange time disagree.')
    calls, puts = (sum(r[i] for r in strikes.values()) for i in (0, 1))
    if calls != finite(p.get('total_call_oi'), True) or puts != finite(p.get('total_put_oi'), True):
        raise ValueError('OI totals do not match the full strike chain.')
    prev_calls = sum(r[2] for r in strikes.values()) if previous else None
    prev_puts = sum(r[3] for r in strikes.values()) if previous else None
    for field, computed in [('total_call_oi_change', calls-prev_calls if previous else None),
                            ('total_put_oi_change', puts-prev_puts if previous else None)]:
        if computed is not None and p.get(field) is not None and computed != finite(p[field]):
            raise ValueError('OI daily changes do not match the strike chain.')
    spot = finite(p.get('current_ltp'), True)
    if spot <= 0:
        raise ValueError('OI spot is unavailable.')
    prev_spot = finite(p['prev_ltp'], True) if p.get('prev_ltp') is not None else None
    pcr = puts/calls if calls else None
    prev_pcr = prev_puts/prev_calls if prev_calls else None
    return dict(symbol=symbol, date=inp['date'], data_time=stamp.isoformat(),
                spot=spot, prev_spot=prev_spot, spot_change=spot-prev_spot if prev_spot else None,
                spot_change_pct=(spot/prev_spot-1)*100 if prev_spot else None,
                atm=finite(p.get('atm_strike'), True), pcr=pcr, prev_pcr=prev_pcr,
                pcr_change=pcr-prev_pcr if pcr is not None and prev_pcr is not None else None,
                call_oi=calls, put_oi=puts, prev_call_oi=prev_calls, prev_put_oi=prev_puts,
                call_oi_change=calls-prev_calls if previous else None,
                put_oi_change=puts-prev_puts if previous else None,
                expiries=expiries, expiry_catalog=catalog, strikes=strikes)


def combine_months(parts):
    if len(parts) != 2 or any(len(p['expiries']) != 1 for p in parts):
        raise ValueError('Both monthly OI chains are required.')
    if len({p['expiries'][0][:7] for p in parts}) != 2 or len({p['date'] for p in parts}) != 1:
        raise ValueError('Monthly OI scopes do not match.')
    months = sorted(int(p['expiries'][0][:4])*12+int(p['expiries'][0][5:7]) for p in parts)
    if months[1]-months[0] != 1:
        raise ValueError('The next calendar-month expiry is missing.')
    stamps = [timestamp(p['data_time']) for p in parts]
    if (max(stamps)-min(stamps)).total_seconds() > 180:
        raise ValueError('Monthly OI timestamps are too far apart.')
    if abs(parts[0]['spot']/parts[1]['spot']-1) > .002:
        raise ValueError('Monthly OI spot prices do not match.')
    previous_spots = [p.get('prev_spot') for p in parts]
    if all(previous_spots) and abs(previous_spots[0]/previous_spots[1]-1) > .002:
        raise ValueError('Monthly OI previous spot prices do not match.')
    rows = {}
    for p in parts:
        for k, values in p['strikes'].items():
            row = rows.setdefault(k, [0, 0, 0, 0])
            for i, v in enumerate(values):
                row[i] = row[i]+v if row[i] is not None and v is not None else None
    # A strike absent in a full, reconciled expiry chain has no OI in that expiry.
    previous = all(p['prev_call_oi'] is not None and p['prev_put_oi'] is not None for p in parts)
    calls, puts = [sum(p[k] for p in parts) for k in ('call_oi', 'put_oi')]
    pc, pp = [sum(p[k] for p in parts) if previous else None for k in ('prev_call_oi', 'prev_put_oi')]
    pcr, prev_pcr = puts/calls if calls else None, pp/pc if pc else None
    return {**parts[0], 'expiries': sorted(p['expiries'][0] for p in parts), 'strikes': rows,
            'call_oi': calls, 'put_oi': puts, 'prev_call_oi': pc, 'prev_put_oi': pp,
            'call_oi_change': calls-pc if previous else None, 'put_oi_change': puts-pp if previous else None,
            'pcr': pcr, 'prev_pcr': prev_pcr, 'pcr_change': pcr-prev_pcr if pcr is not None and prev_pcr is not None else None,
            'data_time': min(stamps).isoformat(), 'scope': 'front_and_next_month', 'version': 2,
            'timestamp_skew_seconds': (max(stamps)-min(stamps)).total_seconds(),
            'by_expiry': {p['expiries'][0]: p for p in parts}}


def strike_changes(new, old):
    if new['symbol'] != old['symbol'] or new['date'] != old['date'] or new['expiries'] != old['expiries']:
        raise ValueError('OI comparison needs the same symbol, session and expiries.')
    if set(new['strikes']) != set(old['strikes']):
        raise ValueError('Strike coverage changed; wait for matching snapshots.')
    rows = [dict(strike=float(k), call_oi=a[0], put_oi=a[1],
                 call_change=a[0]-old['strikes'][k][0], put_change=a[1]-old['strikes'][k][1])
            for k, a in sorted(new['strikes'].items(), key=lambda item: float(item[0]))]
    return dict(rows=rows, call_change=sum(r['call_change'] for r in rows), put_change=sum(r['put_change'] for r in rows))


class SensibullFeed:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()          # guards session and cooldown
        self.session = None
        self.device = str(uuid.uuid4())
        self.last_request = 0.
        self.blocked_until = 0.
        self.last_error = ''
        self.live_cache = {}
        self.stop = threading.Event()
        self.thread = None
        self.last_poll = None
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS sb_snapshot (symbol TEXT NOT NULL, taken TEXT NOT NULL, data_time TEXT, date TEXT,
                    spot REAL, pcr REAL, call_oi INTEGER, put_oi INTEGER, call_chg INTEGER, put_chg INTEGER, expiries TEXT, strikes TEXT NOT NULL,
                    PRIMARY KEY (symbol, taken));
                CREATE INDEX IF NOT EXISTS sb_snapshot_day ON sb_snapshot (symbol, date, taken);
                CREATE TABLE IF NOT EXISTS sb_cache (name TEXT PRIMARY KEY, fetched TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sb_watch (symbol TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS sb_snapshot_v2 (symbol TEXT NOT NULL, data_time TEXT NOT NULL,
                    scope TEXT NOT NULL, date TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (symbol, data_time, scope));
            ''')
            if db.execute('SELECT COUNT(*) FROM sb_watch').fetchone()[0] == 0:
                db.executemany('INSERT INTO sb_watch VALUES (?)', [(s,) for s in DEFAULT_WATCH])

    # ---------- HTTP ----------
    def _post(self, url, body):
        with self.lock:
            if time.time() < self.blocked_until:
                raise ValueError('Sensibull is pausing after a block or rate limit. Try again later.')
            for attempt in range(2):
                wait = MIN_GAP - (time.monotonic() - self.last_request)
                if wait > 0:
                    time.sleep(wait)
                self.last_request = time.monotonic()
                try:
                    if self.session is None:
                        self.session = requests.Session()
                        identity = self.session.get(IDENTIFY, headers={**HEADERS, 'x-device-id': self.device}, timeout=(5, 15))
                        if identity.status_code in (403, 429):
                            self.blocked_until = time.time() + COOLDOWN
                            self.last_error = f'Sensibull returned {identity.status_code}; paused for 15 minutes.'
                            raise ValueError(self.last_error)
                        identity.raise_for_status()
                    r = self.session.post(url, json=body, headers={**HEADERS, 'x-device-id': self.device}, timeout=(5, 25))
                except requests.RequestException:
                    self.session = None
                    self.last_error = 'Sensibull connection failed.'
                    raise ValueError(self.last_error) from None
                if r.status_code == 401 and attempt == 0:
                    self.session = None            # anonymous token expired: identify again and retry once
                    continue
                if r.status_code in (403, 429):
                    self.blocked_until = time.time() + COOLDOWN
                    self.last_error = f'Sensibull returned {r.status_code}; paused for 15 minutes.'
                    raise ValueError(self.last_error)
                if r.status_code != 200:
                    self.last_error = f'Sensibull returned {r.status_code}.'
                    raise ValueError(self.last_error)
                try:
                    data = r.json()
                except ValueError:
                    raise ValueError('Sensibull sent an unreadable response.') from None
                self.last_error = ''
                return data
            raise ValueError('Sensibull login could not be refreshed.')

    # ---------- snapshots ----------
    def live(self, symbol, force=False):
        symbol = clean_symbol(symbol)
        # Single-flight across UI and poller; the HTTP lock is reentrant.
        with self.lock:
            cached = self.live_cache.get(symbol)
            if cached and not force and time.time() - cached[0] < LIVE_TTL:
                return cached[1]
            body = {'underlying': symbol, 'expiries': None, 'atm_strike_selection': 'twenty',
                    'input_min_strike': None, 'input_max_strike': None, 'auto_update': 'full_day', 'show_prev_oi': True}
            first = parse_oi(self._post(OI_CHART, body), symbol)
            catalog = first['expiry_catalog']
            monthly = sorted(k for k, v in catalog.items() if isinstance(v, dict)
                             and v.get('is_weekly') is False and k >= first['date'])
            selected = []
            for k in monthly:
                if not selected or k[:7] != selected[-1][:7]:
                    selected.append(k)
                if len(selected) == 2:
                    break
            if len(selected) != 2:
                raise ValueError('Current and next monthly OI expiries are not both available.')
            parts = []
            for expiry in selected:
                if first['expiries'] == [expiry]:
                    part = first
                else:
                    choice = {k: {**v, 'is_enabled': k == expiry} for k, v in catalog.items()}
                    part = parse_oi(self._post(OI_CHART, {**body, 'expiries': choice}), symbol)
                if part['expiries'] != [expiry]:
                    raise ValueError('Sensibull did not honour the requested expiry.')
                parts.append(part)
            snap = combine_months(parts)
            snap['taken'] = clock().isoformat(timespec='seconds')
            with self.store.connect() as db:
                if db.execute('SELECT COUNT(*) FROM sb_watch').fetchone()[0] < 60:
                    db.execute('INSERT OR IGNORE INTO sb_watch VALUES (?)', (symbol,))
                snap['recording_enabled'] = db.execute('SELECT 1 FROM sb_watch WHERE symbol=?', (symbol,)).fetchone() is not None
            self._save(snap)
            self.live_cache[symbol] = (time.time(), snap)
            return snap

    def _save(self, snap):
        if snap.get('version') != 2:
            raise ValueError('Only verified two-month snapshots can be recorded.')
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO sb_snapshot_v2 VALUES (?,?,?,?,?)',
                       (snap['symbol'], snap['data_time'], ','.join(snap['expiries']), snap['date'],
                        json.dumps(snap, allow_nan=False)))

    def snapshots(self, symbol, date=None):
        symbol = clean_symbol(symbol)
        with self.store.connect() as db:
            if date is None:
                last = db.execute('SELECT date FROM sb_snapshot_v2 WHERE symbol=? ORDER BY data_time DESC LIMIT 1', (symbol,)).fetchone()
                date = last['date'] if last else None
            rows = db.execute('SELECT payload FROM sb_snapshot_v2 WHERE symbol=? AND date=? ORDER BY data_time',
                              (symbol, date)).fetchall() if date else []
        return sorted([json.loads(r['payload']) for r in rows], key=lambda s: timestamp(s['data_time']))

    def changes(self, symbol, minutes, latest=None):
        minutes = int(minutes)
        if not 1 <= minutes <= 480:
            raise ValueError('Choose 1 to 480 minutes.')
        snaps = self.snapshots(symbol, latest['date'] if latest else None)
        new = latest or (snaps[-1] if snaps else None)
        unavailable = dict(symbol=clean_symbol(symbol), ready=False, minutes=minutes, snapshots=len(snaps),
                           note='A full matching window of recorded exchange data is not available.')
        if new is None:
            return unavailable
        end = timestamp(new['data_time'])
        snaps = [s for s in snaps if s['expiries'] == new['expiries'] and timestamp(s['data_time']) <= end]
        cutoff = end - timedelta(minutes=minutes)
        choices = [s for s in snaps if 0 <= (cutoff-timestamp(s['data_time'])).total_seconds() <= 180]
        if not choices:
            return unavailable
        base = max(choices, key=lambda s: timestamp(s['data_time']))
        times = sorted({timestamp(s['data_time']) for s in snaps if timestamp(s['data_time']) >= timestamp(base['data_time'])} | {end})
        if any((b-a).total_seconds() > 360 for a, b in zip(times, times[1:])):
            return {**unavailable, 'note': 'Recording gap exceeds six minutes; this window is incomplete.'}
        try:
            result = strike_changes(new, base)
        except ValueError as exc:
            return {**unavailable, 'note': str(exc)}
        return dict(symbol=new['symbol'], ready=True, minutes=minutes,
                    covered_minutes=round((end-timestamp(base['data_time'])).total_seconds()/60, 1),
                    from_time=base['data_time'], to_time=new['data_time'], snapshots=len(snaps),
                    spot_change_pct=(new['spot']/base['spot']-1)*100,
                    pcr_change=new['pcr']-base['pcr'] if new['pcr'] is not None and base['pcr'] is not None else None,
                    baseline_oi=base['call_oi']+base['put_oi'], **result)

    # ---------- history series ----------
    def series(self, symbol, kind):
        symbol = clean_symbol(symbol)
        if kind not in SERIES:
            raise ValueError('Unknown series.')
        name = f'{kind}:{symbol}'
        with self.store.connect() as db:
            row = db.execute('SELECT fetched,payload FROM sb_cache WHERE name=?', (name,)).fetchone()
        if row and time.time() - clock().fromisoformat(row['fetched']).timestamp() < SERIES_TTL:
            return json.loads(row['payload'])
        data = self._post(SERIES[kind], {'underlying': symbol})
        p = data.get('payload') if isinstance(data, dict) else None
        if data.get('success') is not True or not isinstance(p, dict) or not isinstance(p.get('data'), list):
            raise ValueError('Sensibull history format changed.')
        value = dict(symbol=symbol, kind=kind, rows=p['data'], fetched=clock().isoformat(timespec='seconds'))
        with self.store.connect() as db:
            db.execute('INSERT OR REPLACE INTO sb_cache VALUES (?,?,?)', (name, value['fetched'], json.dumps(value, allow_nan=False)))
        return value

    # ---------- watchlist and poller ----------
    def watch(self):
        with self.store.connect() as db:
            return [r['symbol'] for r in db.execute('SELECT symbol FROM sb_watch ORDER BY symbol')]

    def set_watch(self, symbols):
        symbols = list(dict.fromkeys(clean_symbol(s) for s in symbols))
        if not 1 <= len(symbols) <= 60:
            raise ValueError('Watch 1 to 60 symbols.')
        with self.store.connect() as db:
            db.execute('DELETE FROM sb_watch')
            db.executemany('INSERT INTO sb_watch VALUES (?)', [(s,) for s in symbols])
        return symbols

    def status(self):
        with self.store.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM sb_snapshot_v2').fetchone()[0]
        paused = max(0, int(self.blocked_until - time.time()))
        return dict(watch=self.watch(), polling=bool(self.thread and self.thread.is_alive()), poll_seconds=POLL_SECONDS, paused_seconds=paused,
                    last_poll=self.last_poll, last_error=self.last_error, snapshots_stored=total, market_open=market_open())

    def poll_once(self):
        """Fetch every watched symbol one after another; stop at the first block."""
        done = 0
        for symbol in self.watch():
            if self.stop.is_set():
                break
            try:
                self.live(symbol, force=True); done += 1
            except ValueError:
                if time.time() < self.blocked_until:
                    break
        self.last_poll = clock().isoformat(timespec='seconds')
        return done

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()
        def loop():
            while not self.stop.wait(POLL_SECONDS):
                if market_open() and time.time() >= self.blocked_until:
                    try:
                        self.poll_once()
                    except Exception:
                        self.last_error = 'Sensibull poll failed.'
        self.thread = threading.Thread(target=loop, name='sensibull-poll', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
