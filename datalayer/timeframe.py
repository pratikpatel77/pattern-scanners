"""Timeframe conversion. Weekly bars are built from daily bars so every
broker behaves identically (and one daily download serves both timeframes)."""
from __future__ import annotations

import numpy as np
import pandas as pd

DAILY = "D"
WEEKLY = "W"
TIMEFRAMES = {"daily": DAILY, "weekly": WEEKLY, "d": DAILY, "w": WEEKLY}


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Sat-Fri weeks, so weekend special sessions (Muhurat, Budget Saturday) join
    the following week -- same grouping as pandas' W-FRI. Each weekly bar is
    stamped with its LAST trading day, so the current, still-forming week shows
    up with today's date. numpy reduceat instead of groupby: ~50x faster."""
    if daily.empty:
        return daily
    days = daily.index.values.astype("datetime64[D]")
    week = (days.astype(np.int64) - 2) // 7          # 1970-01-03 was a Saturday -> weeks start Saturday
    starts = np.flatnonzero(np.r_[True, week[1:] != week[:-1]])
    ends = np.r_[starts[1:], len(days)] - 1
    o, h, l, c, v = (daily[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    weekly_vol = np.add.reduceat(v, starts)
    out = pd.DataFrame(
        {
            "open": o[starts],
            "high": np.maximum.reduceat(h, starts),
            "low": np.minimum.reduceat(l, starts),
            "close": c[ends],
            "volume": weekly_vol,
        },
        index=pd.DatetimeIndex(daily.index.values[ends], name="date"),
    )
    if "deliv_qty" in daily.columns:
        # delivery is published per day; a week delivers the sum of its days
        dq = np.add.reduceat(np.nan_to_num(daily["deliv_qty"].to_numpy(float)), starts)
        known = np.add.reduceat(daily["deliv_qty"].notna().to_numpy(), starts) > 0
        out["deliv_qty"] = np.where(known, dq, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            out["deliv_pct"] = np.where(known & (weekly_vol > 0), 100 * dq / weekly_vol, np.nan)
    return out


def resample(daily: pd.DataFrame, tf: str) -> pd.DataFrame:
    return to_weekly(daily) if tf == WEEKLY else daily
