"""Broker-agnostic data source contract.

Every broker adapter only has to answer two questions:

  * which NSE cash-equity symbols can you serve?   -> `instruments()`
  * give me daily OHLCV for one of them in a range -> `_fetch_range()`

Everything else -- chunking long ranges to the broker's per-request limit,
rate limiting, retries, caching, weekly resampling -- lives here or in
`store.py`, so the scanners never know (or care) which broker the bars
came from.

Bars are always a pandas DataFrame indexed by a tz-naive DatetimeIndex
(one row per trading day, ascending) with float columns
open, high, low, close, volume.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from datetime import date, timedelta

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]
BENCHMARK = "NIFTY50"


class RateLimiter:
    """Sliding-window limiter shared by every worker thread of one source."""

    def __init__(self, max_calls: int, period: float = 1.0):
        self.max_calls = max_calls
        self.period = period
        self.lock = threading.Lock()
        self.calls: list[float] = []

    def wait(self):
        with self.lock:
            now = time.monotonic()
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                time.sleep(max(0.0, self.period - (now - self.calls[0])))
                now = time.monotonic()
                self.calls = [t for t in self.calls if now - t < self.period]
            self.calls.append(time.monotonic())


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS, index=pd.DatetimeIndex([], name="date"), dtype=float)


def rows_to_frame(rows) -> pd.DataFrame:
    """rows: iterable of (date, open, high, low, close, volume)."""
    rows = list(rows)
    if not rows:
        return empty_bars()
    df = pd.DataFrame(rows, columns=["date", *COLUMNS])
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates("date", keep="last").set_index("date").sort_index()
    return df.astype(float)


class DataSource(ABC):
    name: str = "base"
    # Largest calendar-day span a single history request may cover.
    max_days_per_request: int = 3650
    # Parallel fetch threads and requests/second the broker tolerates.
    workers: int = 8
    requests_per_second: int = 8
    # False for sources that are already local files (no point caching).
    cacheable: bool = True
    needs_auth: bool = True

    def __init__(self):
        self._limiter = RateLimiter(self.requests_per_second, 1.0)

    def authenticate(self) -> None:  # pragma: no cover - broker specific
        pass

    @abstractmethod
    def instruments(self) -> dict[str, str]:
        """symbol (plain NSE trading symbol, e.g. 'RELIANCE') -> broker identifier."""

    @abstractmethod
    def benchmark_id(self) -> str | None:
        """Broker identifier for the Nifty 50 index, or None if unavailable."""

    @abstractmethod
    def _fetch_range(self, ident: str, start: date, end: date) -> list[tuple]:
        """Daily rows (date, o, h, l, c, v) for start..end inclusive; one request."""

    def fetch_daily(self, ident: str, start: date, end: date, retries: int = 5) -> pd.DataFrame:
        """Daily bars for start..end, split into as many requests as the broker needs."""
        rows: list[tuple] = []
        chunk_end = end
        while chunk_end >= start:
            chunk_start = max(start, chunk_end - timedelta(days=self.max_days_per_request - 1))
            for attempt in range(retries):
                self._limiter.wait()
                try:
                    rows.extend(self._fetch_range(ident, chunk_start, chunk_end))
                    break
                except NoDataError:
                    break
                except Exception:
                    if attempt == retries - 1:
                        raise
                    time.sleep(2.0 * (attempt + 1))
            chunk_end = chunk_start - timedelta(days=1)
        return rows_to_frame(rows)


class NoDataError(Exception):
    """Raised by adapters when a range legitimately has no candles (pre-listing, holidays)."""


class AuthError(Exception):
    """Raised when a broker session can't be established; message says how to fix it."""
