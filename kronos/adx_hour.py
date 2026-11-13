"""The ADX desk on 1-hour bars: the same rules, option plans and learning as the daily desk
(adx_desk.py), kept as a separate desk so the two can be compared.

What differs from the daily desk:
- Bars are completed 1-hour candles fetched from the broker API (FYERS; Definedge minute candles when
  FYERS cannot answer). A new fetch and scan run by themselves after every completed hour.
- The bigger timeframe is the last completed DAY (the daily desk uses the last completed week).
- A setup that turns Enter on a live price is paper traded automatically, one lot, in the position the
  plan recommends.
- Its trades, alerts, record and lessons are stored apart from the daily desk's.
- A trade is timed out after core.MAX_HOLD hourly bars, and option outcomes are estimated two days ahead.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
import pickle
import re
import threading

import pandas as pd
import requests

from . import adx_core as core
from .adx_desk import AdxDesk
from .options_market import IST, clock

NIFTY = '__NIFTY50__'
FYERS_URL = 'https://api-t1.fyers.in/data/history'
MINUTE_URL = 'https://data.definedgesecurities.com/sds/history/NSE/{token}/minute/{start}0000/{end}2359'
OPEN, LAST = 555, 915     # minute of the day the first and the last 1-hour bar start (09:15 and 15:15)
SETTLE = 45               # seconds after an hour ends before its candle is asked for
HISTORY_DAYS = 198        # calendar days loaded for a new stock (about 950 hourly bars, 135 daily ones)
CHUNK_DAYS = 99           # FYERS serves at most 100 days of intraday candles per request
KEEP_BARS = 1200
MIN_MINUTES = .75         # share of an hour's minutes that must have traded before a minute-built candle is accepted
FLOOR_DAYS = 5            # fewest calendar days an expiry may have left when a trade is opened (it exits at 3)
MAX_OPEN = 10             # most 1-hour trades open at once: one lot each is not equal money across stocks, so the count is capped
RETRY_MAX = 900           # longest wait, in seconds, between rescans while the newest hour is still missing
HOUR_DTE = 6              # calendar days an expiry must still have: two sessions held plus the 3-day exit rule
WORDS = {'Weekly': 'Daily', 'weekly': 'daily', 'Daily': 'Hourly', 'daily': 'hourly', '20-day': '20-bar',
         'over 20 days': 'over 20 hourly bars', 'against its last year': 'against its last 250 hourly bars'}
WORD = re.compile('|'.join(sorted(WORDS, key=len, reverse=True)))


def now():
    return clock().replace(tzinfo=None)


def last_bar(at=None):
    """Start of the newest completed 1-hour bar. Bars start at 09:15, 10:15 ... 15:15; the last one
    ends with the session at 15:30. Weekends are skipped; exchange holidays are not known here."""
    at = (at or now()) - timedelta(seconds=SETTLE)
    day, minute = at.date(), at.hour * 60 + at.minute
    if day.weekday() < 5 and minute >= OPEN + 60:
        done = (LAST - OPEN) // 60 if minute >= 930 else (minute - OPEN) // 60 - 1
        return datetime.combine(day, clock_time(9, 15)) + timedelta(hours=done)
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, clock_time(15, 15))


def reword(value):
    """The rule texts are written for daily bars with a weekly background. Here the bars are hourly
    and the background is daily. One pass over the text, so a word is never translated twice."""
    if isinstance(value, str):
        return WORD.sub(lambda m: WORDS[m.group()], value)
    if isinstance(value, list):
        return [reword(v) for v in value]
    if isinstance(value, dict):
        return {k: v if k in ('id', 'signal', 'symbol') else reword(v) for k, v in value.items()}
    return value


def session_bars(frame, due):
    """Keep only completed bars that start inside the session."""
    minute = frame.index.hour * 60 + frame.index.minute
    frame = frame.loc[(minute >= OPEN) & (minute <= LAST) & (frame.index <= pd.Timestamp(due))]
    return frame.loc[~frame.index.duplicated(keep='last')].sort_index()


class HourBars:
    """Completed 1-hour candles per stock, from the broker API. Kept on disk only so a restart does
    not download months of history again; every scan asks the API for the newest candles."""

    def __init__(self, api, path):
        self.api, self.path = api, Path(path)
        self.frames, self.lock = {}, threading.Lock()
        self.fyers_fails = 0
        try:
            with self.path.open('rb') as f:        # our own local cache, written below
                self.frames = pickle.load(f)
        except (OSError, pickle.PickleError, EOFError):
            pass

    def get(self, symbol):
        frame = self.frames.get(symbol)
        if frame is None or frame.empty:
            raise ValueError('No 1-hour candles for this stock yet. Press Rescan.')
        return frame

    def _fyers(self, symbol, start, end):
        ident = 'NSE:NIFTY50-INDEX' if symbol == NIFTY else f'NSE:{symbol}-EQ'
        rows = []
        while start <= end:
            until = min(end, start + timedelta(days=CHUNK_DAYS - 1))
            data = self.api._get(FYERS_URL, dict(symbol=ident, resolution='60', date_format='1', range_from=str(start),
                                                 range_to=str(until), cont_flag='1'), True)
            rows.extend(r[:5] for r in data.get('candles', []))
            start = until + timedelta(days=1)
        if not rows:
            raise ValueError('FYERS returned no 1-hour candles.')
        frame = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close'])
        frame.index = pd.to_datetime(frame.pop('date'), unit='s', utc=True).dt.tz_convert(IST).dt.tz_localize(None)
        return frame.astype(float)

    def _definedge(self, symbol, start, end):
        """1-minute candles from Definedge, joined into the same 09:15-anchored hours FYERS serves."""
        api = self.api
        api.ready()
        token = api.source.benchmark_id() if symbol == NIFTY else api.instruments.get(symbol)
        if not token:
            raise ValueError('Definedge has no candles for this stock.')
        api.source._limiter.wait()
        try:
            r = requests.get(MINUTE_URL.format(token=token, start=start.strftime('%d%m%Y'), end=end.strftime('%d%m%Y')),
                             headers={'Authorization': api.source.session_key}, timeout=(8, 60))
            if r.status_code in (401, 403):
                api.login_date = None                  # log in again on the next call
            r.raise_for_status()
        except requests.RequestException:
            raise ValueError('Definedge candle request failed.') from None
        rows = []
        for line in r.text.strip().splitlines():
            f = line.split(',')
            try:
                rows.append((datetime.strptime(f[0][:12], '%d%m%Y%H%M'), float(f[1]), float(f[2]), float(f[3]), float(f[4])))
            except (ValueError, IndexError):
                continue
        if not rows:
            raise ValueError('Definedge returned no candles.')
        minutes = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close']).set_index('date').sort_index()
        minutes = minutes.loc[~minutes.index.duplicated(keep='last')]      # a repeated minute is one minute, not two
        minutes['minutes'] = 1
        hours = minutes.resample('60min', offset='15min').agg(dict(open='first', high='max', low='min', close='last', minutes='sum'))
        # An hour built from only a few minutes is a feed gap or a truncated download, not a candle: a liquid
        # F&O stock trades in nearly every minute. The 15:15 bar has 15 minutes, every other bar 60.
        room = [15 if t.hour * 60 + t.minute >= LAST else 60 for t in hours.index]
        hours = hours.loc[[n >= MIN_MINUTES * r for n, r in zip(hours.minutes, room)]]
        return hours.drop(columns='minutes').dropna()

    def _range(self, symbol, start, end):
        if self.fyers_fails < 3:                       # after three failures in a row FYERS is left alone for this refresh
            try:
                frame = self._fyers(symbol, start, end)
                self.fyers_fails = 0
                return frame
            except ValueError:
                self.fyers_fails += 1
        return self._definedge(symbol, start, end)

    def _fetch(self, symbol, due):
        old, end = self.frames.get(symbol), due.date()
        full = end - timedelta(days=HISTORY_DAYS)
        fresh = session_bars(self._range(symbol, max(full, old.index[-1].date()) if old is not None and len(old) else full, end), due)
        if fresh.empty:
            raise ValueError('No completed 1-hour candles.')
        if old is not None and len(old):
            both = old.index.intersection(fresh.index)
            if len(both) and (fresh.loc[both, 'close'] / old.loc[both, 'close'] - 1).abs().max() > .015:
                # The saved bars no longer match the API (a split or a bonus): load the history again.
                fresh, old = session_bars(self._range(symbol, full, end), due), None
        if old is not None and len(old):
            fresh = pd.concat([old.loc[old.index < fresh.index[0]], fresh])
        return fresh.iloc[-KEEP_BARS:]

    def refresh(self, symbols, progress=None):
        """Bring every stock up to the newest completed hour. Raises only when nothing could be fetched."""
        due = last_bar()
        todo = [s for s in symbols if s not in self.frames or self.frames[s].index[-1] < pd.Timestamp(due)]
        self.fyers_fails = 0

        self.failed = set()

        def one(symbol):
            try:
                self.frames[symbol] = self._fetch(symbol, due)
                return True
            except Exception:                         # one stock failing must not stop the others
                self.failed.add(symbol)
                return False

        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            for n, ok in enumerate(pool.map(one, todo)):
                done += ok
                if progress and (n % 5 == 0 or n + 1 == len(todo)):
                    progress(n + 1, len(todo))
        if todo and not done:
            raise ValueError('1-hour candles could not be fetched from FYERS or Definedge. Check the logins and the connection, then press Rescan.')
        if done:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.path.with_suffix('.tmp')
                with temp.open('wb') as f:
                    pickle.dump(dict(self.frames), f, protocol=pickle.HIGHEST_PROTOCOL)
                temp.replace(self.path)
        return due

    def coverage(self, symbols, due=None):
        """Which stocks lack the newest completed hour, and which could not be fetched at all."""
        due = pd.Timestamp(due or last_bar())
        behind = sorted(s for s in symbols if s not in self.frames or self.frames[s].index[-1] < due)
        failed = sorted(s for s in getattr(self, 'failed', set()) if s in symbols)
        return dict(total=len(symbols), behind_n=len(behind), behind=behind[:12], failed_n=len(failed), failed=failed[:12])


class AdxHourDesk(AdxDesk):
    SOURCE, TITLE, KEY = 'adx1h', 'ADX 1H', '1h:'
    PERIOD = staticmethod(lambda d: d.date())          # the bigger timeframe of an hourly bar is its day
    PER_YEAR = 252 * 7
    IV_STRESS, MIN_EDGE = .15, 1.
    PLAN = dict(hold_days=2, range_days=3, time_exit=f'after {core.MAX_HOLD} hourly bars', costs=True, iv_stress=IV_STRESS, min_edge=MIN_EDGE)
    RESCAN_EVERY, RETRY = 120, True
    AUTO, STRICT, FLOOR_DAYS, MAX_OPEN = True, True, FLOOR_DAYS, MAX_OPEN

    def __init__(self, service):
        super().__init__(service)
        self.bars = HourBars(service.prices, Path(service.directory) / 'hour_bars.pkl')
        self.late, self.late_for, self.cover = 0, None, None   # rescans in a row that ended with the newest hour still missing (for which hour); the last coverage report

    def _behind(self, saved):
        """The scan must be redone when the newest bar is missing for the saved scan, for the Nifty, or for any
        stock. The saved board's latest timestamp alone would hide a Nifty or a single stock that is still behind."""
        if str(saved.get('data_date') or '') < self._expected():
            return True
        cover = self.bars.coverage(list(self.bars.frames) + ([] if NIFTY in self.bars.frames else [NIFTY]))
        return bool(cover['behind_n'] or (self.cover or {}).get('behind_n'))      # the scan's own report also counts stocks never fetched

    def _gap(self):
        """While the newest hour is missing the scan is repeated, waiting twice as long each time, so a
        holiday or a long outage does not hammer the broker API. A new hour starts the count again."""
        if self.late_for != self._expected():
            self.late, self.late_for = 0, self._expected()
        return min(self.RESCAN_EVERY * 2 ** self.late, RETRY_MAX)

    def board(self):
        out = super().board()
        out['coverage'] = self.cover
        return out

    # ---------- data ----------
    def _stamp(self):
        return f'{core.RULES_VERSION}|1h|{last_bar()}'      # changes when an hour completes, which starts a fetch and a scan

    def _arrays(self, symbol):
        frame = self.bars.get(symbol)
        return ([t.to_pydatetime() for t in frame.index], *(frame[c].tolist() for c in ('open', 'high', 'low', 'close')))

    def universe(self):
        return sorted(self.service.desk._fno_symbols())

    def _scan(self):
        symbols = self.universe() + [NIFTY]
        try:
            self.bars.refresh(symbols, lambda n, total: self.job.update(
                progress=round(n / total * 100), message=f'Fetching 1-hour candles ({n}/{total})'))
        except Exception as exc:
            safe = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__}: 1-hour candles could not be fetched.'
            self.job.update(running=False, error=safe, message=safe)
            self.late += 1
            self.cover = self.bars.coverage(symbols)
            return
        self.cover = self.bars.coverage(symbols)
        super()._scan()
        saved = self._saved()
        self.late = self.late + 1 if saved and self._behind(saved) else 0

    def _day_closes(self, up):
        """The last hourly close of each COMPLETED day, for the stock's volatility. A day still in progress is left out."""
        days = {}
        for d, c in zip(up.dates, up.c):
            days[d.date()] = (d, c)
        out = [v for _, v in days.values()]
        if days and max(d for d, _ in days.values()).replace(tzinfo=None).time() < clock_time(15, 15) and len(out) > 1:
            out.pop()
        return out

    def _days_wanted(self, sig, atr):
        return HOUR_DTE

    def _expected(self):
        return str(last_bar())

    def _after(self, up, created):
        since = datetime.fromisoformat(created)
        if since.tzinfo:
            since = since.astimezone(IST).replace(tzinfo=None)
        return [(up.h[k], up.l[k]) for k in range(up.n) if up.dates[k] >= since]   # bars that began after the entry

    def _timed_out(self, age, path):
        return f'Exit: held {len(path)} hourly bars without reaching the plan.' if len(path) >= core.MAX_HOLD else None

    def view(self, name, *args):
        return reword(getattr(self, name)(*args))
