"""Local-files adapter: plug in ANY other data vendor without writing code.

Point `--data-dir` at a folder of one file per symbol:

  * `SYMBOL.csv`  with columns date,open,high,low,close[,volume]
                  (header names are case-insensitive)
  * `SYMBOL.json` as a list of [date, open, high, low, close, volume] rows
                  (the format rs-strategy-backtest already caches)

An optional `NIFTY50.csv` / `__NIFTY50__.json` is used as the benchmark.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from ..base import DataSource, rows_to_frame

BENCHMARK_NAMES = ("NIFTY50", "__NIFTY50__", "NIFTY")


class LocalFilesSource(DataSource):
    name = "local"
    workers = 8
    requests_per_second = 10_000
    cacheable = False
    needs_auth = False

    def __init__(self, data_dir: str | Path):
        super().__init__()
        self.data_dir = Path(data_dir)
        if not self.data_dir.is_dir():
            raise FileNotFoundError(f"--data-dir not found: {self.data_dir}")
        self._files = {p.stem: p for p in self.data_dir.iterdir() if p.suffix.lower() in (".csv", ".json")}

    def instruments(self) -> dict[str, str]:
        return {s: s for s in self._files if s not in BENCHMARK_NAMES}

    def benchmark_id(self) -> str | None:
        return next((n for n in BENCHMARK_NAMES if n in self._files), None)

    def _fetch_range(self, ident, start, end):  # unused: fetch_daily is overridden
        raise NotImplementedError

    def fetch_daily(self, ident: str, start: date, end: date, retries: int = 1) -> pd.DataFrame:
        path = self._files[ident]
        if path.suffix.lower() == ".json":
            df = rows_to_frame(tuple(r[:6]) if len(r) >= 6 else (*r[:5], 0.0) for r in json.loads(path.read_text()))
        else:
            raw = pd.read_csv(path)
            raw.columns = [c.strip().lower() for c in raw.columns]
            if "volume" not in raw:
                raw["volume"] = 0.0
            df = rows_to_frame(raw[["date", "open", "high", "low", "close", "volume"]].itertuples(index=False))
        return df.loc[pd.Timestamp(start) : pd.Timestamp(end)]
