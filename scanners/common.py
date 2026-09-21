"""Shared types and indicator helpers for the pattern scanners.

Every scanner exposes:

    NAME, TITLE, DESCRIPTION
    PARAMS = {"D": Params(...), "W": Params(...)}
    def scan(symbol, bars, tf, bench=None) -> Signal | None

`bars` is the DataFrame from the data layer (already resampled to `tf`),
`bench` the Nifty 50 bars on the same timeframe (may be None).
Scanners only look at the data -- no I/O -- so they are trivially testable
and can be re-run on a cache with `--asof` to check past setups.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd


@dataclass
class Signal:
    scanner: str
    symbol: str
    tf: str                      # "D" or "W"
    date: date                   # last bar
    close: float
    pivot: float                 # breakout level
    support: float               # invalidation level (base/box/handle low)
    score: float                 # 0-100, higher = better-formed and closer to trigger
    label: str                   # short phase description
    metrics: dict = field(default_factory=dict)   # scanner-specific columns (ordered)
    notes: list[str] = field(default_factory=list)
    chart: dict = field(default_factory=dict)     # closes + reference lines for the report sparkline
    zones: dict = field(default_factory=dict)     # bar indices of the pattern, for volume/delivery analysis
    analysis: dict = field(default_factory=dict)  # filled in by analysis.py after the scan

    @property
    def distance_pct(self) -> float:
        return (self.pivot / self.close - 1) * 100

    @property
    def risk_pct(self) -> float:
        return (1 - self.support / self.close) * 100


def arrays(bars: pd.DataFrame):
    return (
        bars["open"].to_numpy(float),
        bars["high"].to_numpy(float),
        bars["low"].to_numpy(float),
        bars["close"].to_numpy(float),
        bars["volume"].to_numpy(float),
    )


def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1 :] = (c[n:] - c[:-n]) / n
    return out


def mean(x: np.ndarray) -> float:
    return float(np.mean(x)) if len(x) else float("nan")


def clip01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return float(min(1.0, max(0.0, x)))


def ratio_score(value: float, bad: float, good: float) -> float:
    """0 at `bad`, 1 at `good`, linear in between (works for either direction)."""
    if good == bad:
        return 1.0
    return clip01((value - bad) / (good - bad))


def volume_ratio(vol: np.ndarray, recent: int, base: int) -> float:
    """Recent average volume / longer average volume; NaN when there is no volume data."""
    if len(vol) < base or vol[-base:].sum() <= 0:
        return float("nan")
    b = vol[-base:].mean()
    return float(vol[-recent:].mean() / b) if b > 0 else float("nan")


def relative_strength(bars: pd.DataFrame, bench: pd.DataFrame | None, ma_len: int, slope_len: int):
    """Mansfield-style RS: (stock/index) vs its own moving average.
    Returns (rs_vs_ma_pct, rs_slope_pct) or (nan, nan) when no benchmark."""
    if bench is None or bench.empty:
        return float("nan"), float("nan")
    joined = bars["close"].to_frame("s").join(bench["close"].rename("b"), how="inner").dropna()
    if len(joined) < ma_len + slope_len:
        return float("nan"), float("nan")
    rs = (joined["s"] / joined["b"]).to_numpy()
    rs_ma = sma(rs, ma_len)
    return float((rs[-1] / rs_ma[-1] - 1) * 100), float((rs[-1] / rs[-1 - slope_len] - 1) * 100)


def chart(close: np.ndarray, start: int, lines: dict[str, float], zone_start: int | None = None) -> dict:
    start = max(0, start)
    return {
        "closes": [round(float(v), 2) for v in close[start:]],
        "lines": {k: round(float(v), 2) for k, v in lines.items()},
        "zone_start": None if zone_start is None else max(0, zone_start - start),
    }


def liquid_enough(bars_daily: pd.DataFrame, min_turnover_cr: float, days: int = 20) -> bool:
    """Average daily traded value (close x volume) over `days`, in crore rupees.
    Sources without volume pass automatically."""
    tail = bars_daily.tail(days)
    if tail.empty or tail["volume"].sum() <= 0:
        return True
    return float((tail["close"] * tail["volume"]).mean()) / 1e7 >= min_turnover_cr
