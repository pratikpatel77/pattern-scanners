"""Delivery-quantity data from NSE's daily "full bhavcopy".

    https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv

That file is the only free source of DELIVERY quantity -- shares actually taken
into demat, as opposed to intraday churn. No broker API carries it. One file per
trading day, published each evening, no login needed.

Cache: .cache/delivery.pkl -- {SYMBOL: DataFrame(index=date, deliv_qty, deliv_pct, ttl_qty)}
plus the set of days already handled (holidays included, so they are never
re-requested). A first backfill pulls ~2 years (~500 files, a few minutes);
after that it is one small file per evening.
"""
from __future__ import annotations

import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from io import StringIO

import pandas as pd
import requests

from .base import RateLimiter
from .util import CACHE_DIR

SNAPSHOT = CACHE_DIR / "delivery.pkl"
URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/csv,application/csv,*/*",
    "Referer": "https://www.nseindia.com/all-reports",
}
SERIES_KEEP = {"EQ", "BE"}
COLUMNS = ["deliv_qty", "deliv_pct", "ttl_qty"]
PUBLISH_HOUR = 18  # NSE posts the file for a session in the evening


class DeliveryStore:
    def __init__(self, history_days: int = 730, workers: int = 6):
        self.history_days = history_days
        self.workers = workers
        self.data: dict[str, pd.DataFrame] = {}
        self.done: set = set()      # days resolved: file loaded OR confirmed absent (holiday)
        self.missing: set = set()   # days with no file at all
        self._limiter = RateLimiter(8, 1.0)
        self._lock = threading.Lock()
        self._load()

    # ---------- persistence ----------
    def _load(self):
        if not SNAPSHOT.exists():
            return
        try:
            with SNAPSHOT.open("rb") as f:
                blob = pickle.load(f)
            self.data, self.done, self.missing = blob["data"], blob["done"], blob["missing"]
        except Exception:
            print("  delivery cache unreadable -- starting fresh", file=sys.stderr)

    def save(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SNAPSHOT.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump({"data": self.data, "done": self.done, "missing": self.missing}, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(SNAPSHOT)

    # ---------- download ----------
    def _fetch_day(self, d: date):
        url = URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
        for attempt in range(3):
            self._limiter.wait()
            try:
                r = requests.get(url, headers=HEADERS, timeout=45)
            except requests.RequestException:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None                      # market holiday / weekend
            if r.status_code != 200 or not r.text.lstrip().startswith("SYMBOL"):
                time.sleep(1.5 * (attempt + 1))
                continue
            df = pd.read_csv(StringIO(r.text), skipinitialspace=True)
            df.columns = [c.strip().upper() for c in df.columns]
            df = df[df["SERIES"].astype(str).str.strip().isin(SERIES_KEEP)]
            out = pd.DataFrame(
                {
                    "symbol": df["SYMBOL"].astype(str).str.strip(),
                    "date": pd.Timestamp(d),
                    "deliv_qty": pd.to_numeric(df["DELIV_QTY"], errors="coerce"),
                    "deliv_pct": pd.to_numeric(df["DELIV_PER"], errors="coerce"),
                    "ttl_qty": pd.to_numeric(df["TTL_TRD_QNTY"], errors="coerce"),
                }
            )
            return out.dropna(subset=["deliv_qty"])
        raise RuntimeError("delivery file failed for " + str(d))

    def _wanted_days(self, upto: date = None):
        upto = upto or date.today()
        if time.localtime().tm_hour < PUBLISH_HOUR and upto == date.today():
            upto -= timedelta(days=1)          # the file for today is not out yet
        start = date.today() - timedelta(days=self.history_days)
        days, d = [], start
        while d <= upto:
            if d.weekday() < 5 and d not in self.done:
                days.append(d)
            d += timedelta(days=1)
        return days

    def ensure(self, progress: bool = True) -> int:
        """Download whatever is missing (first run: ~2 years). Returns trading days added."""
        days = self._wanted_days()
        if not days:
            return 0
        if progress:
            print(f"Delivery data: fetching {len(days)} day(s) from NSE...", file=sys.stderr, flush=True)
        t0, frames, failed = time.time(), [], 0

        def work(d):
            try:
                return d, self._fetch_day(d), None
            except Exception as e:
                return d, None, e

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = [ex.submit(work, d) for d in days]
            for i, fut in enumerate(as_completed(futures), 1):
                d, df, err = fut.result()
                if err is not None:
                    failed += 1
                    continue
                with self._lock:
                    self.done.add(d)
                    if df is None or df.empty:
                        self.missing.add(d)
                    else:
                        frames.append(df)
                if progress and (i % 50 == 0 or i == len(days)) and len(days) > 20:
                    print(f"  delivery {i}/{len(days)}  ({time.time() - t0:.0f}s)", file=sys.stderr, flush=True)

        if frames:
            self._merge(frames)
        self.save()
        if progress:
            note = f", {failed} failed" if failed else ""
            print(f"  delivery: +{len(frames)} trading days{note} ({time.time() - t0:.0f}s)", file=sys.stderr)
        return len(frames)

    def _merge(self, frames):
        fresh = pd.concat(frames, ignore_index=True)
        cutoff = pd.Timestamp(date.today() - timedelta(days=self.history_days + 30))
        for symbol, g in fresh.groupby("symbol", sort=False):
            add = g.set_index("date")[COLUMNS].sort_index()
            old = self.data.get(symbol)
            merged = add if old is None else pd.concat([old[~old.index.isin(add.index)], add]).sort_index()
            self.data[symbol] = merged[merged.index >= cutoff].astype("float32")

    # ---------- reading ----------
    def get(self, symbol: str):
        return self.data.get(symbol)

    def attach(self, bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """`bars` plus deliv_qty / deliv_pct columns joined on date (NaN where unknown)."""
        d = self.get(symbol)
        if d is None or d.empty:
            return bars
        return bars.join(d[["deliv_qty", "deliv_pct"]], how="left")

    def coverage(self) -> str:
        days = sorted(self.done - self.missing)
        return f"{len(days)} trading days, {days[0]} to {days[-1]}" if days else "none"
