"""Disk-cached, incremental, parallel bar loading on top of any DataSource.

Cache: .cache/bars.pkl -- one snapshot {SYMBOL: (fetched_at, DataFrame)}.
One file instead of 1000+ small ones: Windows (antivirus) makes per-file
opens the slowest part of a cached run.

The cache is SHARED by every broker. NSE daily bars are the same whichever
broker serves them, so after any one source has filled it, every other
source only tops it up with the last few days.

* An entry is FRESH if fetched after the most recent NSE session close
  (15:30 IST, last weekday) -- re-running the same evening makes zero API
  calls. Use --refresh intraday to pull the live bar.
* A STALE entry is topped up incrementally (last ~10 days re-requested).
  If the overlapping days disagree (split/bonus adjustment, or a broker that
  adjusts differently), the full history is refetched.
"""
from __future__ import annotations

import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

from .base import COLUMNS, DataSource
from .util import CACHE_DIR

SNAPSHOT = CACHE_DIR / "bars.pkl"
OVERLAP_DAYS = 10
ADJUSTMENT_TOLERANCE = 0.015
SAVE_EVERY = 200  # persist progress during long first-time downloads


def last_session_close(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now.weekday() < 5 and now >= close:
        return close
    d = close - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


class BarStore:
    def __init__(self, source: DataSource, history_days: int = 1000, refresh: bool = False, offline: bool = False):
        self.source = source
        self.history_days = history_days
        self.refresh = refresh
        self.offline = offline
        self.stats = {"cached": 0, "updated": 0, "fetched": 0, "failed": 0}
        self._lock = threading.Lock()
        self._dirty = 0
        self.entries: dict[str, tuple[float, pd.DataFrame]] = {}
        if SNAPSHOT.exists():
            try:
                with SNAPSHOT.open("rb") as f:
                    self.entries = pickle.load(f)
            except Exception:
                print("  cache unreadable -- starting fresh", file=sys.stderr)

    def symbols(self) -> list[str]:
        return list(self.entries)

    def _put(self, symbol: str, df: pd.DataFrame, kind: str):
        with self._lock:
            self.entries[symbol] = (time.time(), df[COLUMNS])
            self.stats[kind] += 1
            self._dirty += 1
            if self._dirty >= SAVE_EVERY:
                self._save_locked()

    def _count(self, kind: str):
        with self._lock:
            self.stats[kind] += 1

    def save(self):
        with self._lock:
            if self._dirty:
                self._save_locked()

    def _save_locked(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SNAPSHOT.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump(self.entries, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(SNAPSHOT)
        self._dirty = 0

    def get(self, symbol: str, ident: str) -> pd.DataFrame | None:
        today = date.today()
        start = today - timedelta(days=self.history_days)
        if not self.source.cacheable:
            self._count("fetched")
            return self.source.fetch_daily(ident, start, today)

        fetched_at, cached = self.entries.get(symbol, (0.0, None))
        if self.offline:
            if cached is not None:
                self._count("cached")
            return cached

        if cached is not None and not self.refresh and fetched_at >= last_session_close().timestamp():
            self._count("cached")
            return cached

        if cached is not None and len(cached):
            tail_start = (cached.index.max() - pd.Timedelta(days=OVERLAP_DAYS)).date()
            recent = self.source.fetch_daily(ident, tail_start, today)
            overlap = cached.index.intersection(recent.index)
            if len(overlap):
                drift = (recent.loc[overlap, "close"] / cached.loc[overlap, "close"] - 1).abs().max()
                if drift <= ADJUSTMENT_TOLERANCE:
                    merged = pd.concat([cached[cached.index < recent.index.min()], recent])
                    merged = merged[merged.index >= pd.Timestamp(start)]
                    self._put(symbol, merged, "updated")
                    return merged

        df = self.source.fetch_daily(ident, start, today)
        if len(df):
            self._put(symbol, df, "fetched")
        return df

    def load_many(self, instruments: dict[str, str], workers: int | None = None, progress: bool = True) -> dict[str, pd.DataFrame]:
        out: dict[str, pd.DataFrame] = {}
        total = len(instruments)
        t0 = time.time()

        def work(item):
            sym, ident = item
            try:
                return sym, self.get(sym, ident), None
            except Exception as e:  # one bad symbol must not kill the scan
                return sym, None, e

        try:
            with ThreadPoolExecutor(max_workers=workers or self.source.workers) as ex:
                futures = [ex.submit(work, it) for it in instruments.items()]
                for i, fut in enumerate(as_completed(futures), 1):
                    sym, df, err = fut.result()
                    if err is not None:
                        self._count("failed")
                        if progress:
                            print(f"  skip {sym}: {str(err)[:120]}", file=sys.stderr)
                    elif df is not None and len(df):
                        out[sym] = df
                    downloading = self.stats["fetched"] + self.stats["updated"] > 0
                    if progress and downloading and (i % 100 == 0 or i == total) and total > 50:
                        print(f"  bars {i}/{total}  ({time.time() - t0:.0f}s)", file=sys.stderr)
        finally:
            self.save()
        return out
