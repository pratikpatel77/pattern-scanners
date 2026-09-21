"""Darvas Box scanner (Nicolas Darvas).

A box forms after a stock makes a new high:
  * BOX TOP    = a high not exceeded for `confirm` bars afterwards
  * BOX BOTTOM = the lowest low after the top, not undercut for `confirm` bars
The buy trigger is a move above the box top.

This scanner finds CONFIRMED boxes where price is still inside the box
(close <= top) and trading close to the top:

  hard filters
    * top is the highest high of the recent lookback, confirmed by `confirm` bars
    * top is at/near the 52-week high (Darvas only boxed leaders at new highs)
    * bottom confirmed by `confirm` bars (price is no longer falling)
    * box height between min_height and max_height
    * close within max_distance below the top

  score (0-100): proximity to top, box tightness, uptrend alignment,
  repeated tests of the top, volume dry-up inside the box, RS vs Nifty.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .common import Signal, arrays, chart, ratio_score, relative_strength, sma, volume_ratio

NAME = "darvas"
TITLE = "Darvas Box"
DESCRIPTION = "Confirmed Darvas boxes at 52-week highs with price coiling just under the box top."


@dataclass(frozen=True)
class Params:
    lookback: int         # bars searched for the box top
    confirm: int          # bars that must not exceed the top / undercut the bottom
    high_window: int      # 52 weeks
    near_high: float      # top within this of the prior 52-week high
    min_height: float
    max_height: float
    max_distance: float
    test_band: float      # a bar "tests" the top if its high is within this band
    trend_fast: int
    trend_slow: int
    vol_recent: int
    vol_base: int


PARAMS = {
    "D": Params(lookback=60, confirm=3, high_window=250, near_high=0.05, min_height=0.03, max_height=0.20,
                max_distance=0.04, test_band=0.015, trend_fast=50, trend_slow=200, vol_recent=5, vol_base=50),
    "W": Params(lookback=26, confirm=2, high_window=52, near_high=0.05, min_height=0.04, max_height=0.30,
                max_distance=0.06, test_band=0.02, trend_fast=10, trend_slow=40, vol_recent=2, vol_base=20),
}


def scan(symbol: str, bars: pd.DataFrame, tf: str, bench: pd.DataFrame | None = None) -> Signal | None:
    p = PARAMS[tf]
    _, h, l, c, v = arrays(bars)
    n = len(c)
    if n < p.lookback + p.high_window // 2:
        return None

    ws = n - p.lookback
    top_idx = ws + int(np.argmax(h[ws:]))
    if n - 1 - top_idx < p.confirm:
        return None  # still making new highs -- top not confirmed yet
    top = float(h[top_idx])

    bottom_idx = top_idx + 1 + int(np.argmin(l[top_idx + 1 :]))
    if n - 1 - bottom_idx < p.confirm:
        return None  # still making new lows -- bottom not confirmed yet
    bottom = float(l[bottom_idx])

    close = float(c[-1])
    if not (bottom <= close <= top):
        return None
    height = top / bottom - 1
    if not (p.min_height <= height <= p.max_height):
        return None
    dist = top / close - 1
    if dist > p.max_distance:
        return None

    prior = h[max(0, top_idx - p.high_window) : top_idx]
    if len(prior) < p.high_window // 2:
        return None
    prior_high = float(prior.max())
    if top < prior_high * (1 - p.near_high):
        return None

    # ---- scoring ----
    notes = []
    s_prox = 35 * ratio_score(dist, p.max_distance, 0)
    s_tight = 20 * ratio_score(height, p.max_height, p.min_height * 2)

    tests = int(np.sum(h[top_idx + 1 :] >= top * (1 - p.test_band)))
    s_tests = 10 * ratio_score(tests, 0, 3)
    if tests >= 2:
        notes.append(f"top tested {tests}x")

    vr = volume_ratio(v, p.vol_recent, p.vol_base)
    s_vol = 10 * ratio_score(vr, 1.3, 0.7)
    if vr == vr and vr < 0.8:
        notes.append("volume drying up")

    fast, slow = sma(c, p.trend_fast), sma(c, p.trend_slow)
    s_trend = 0.0
    if fast[-1] == fast[-1]:
        s_trend += 5 * (close > fast[-1])
    if slow[-1] == slow[-1]:
        s_trend += 5 * (close > slow[-1]) + 5 * (fast[-1] > slow[-1])
        if close > fast[-1] > slow[-1]:
            notes.append("uptrend aligned")

    rs_vs_ma, _ = relative_strength(bars, bench, p.trend_fast, 1)
    s_rs = 10 * ratio_score(rs_vs_ma, -5, 10)

    if top >= prior_high:
        notes.append("top = new 52W high")

    score = s_prox + s_tight + s_tests + s_vol + s_trend + s_rs
    return Signal(
        scanner=NAME,
        symbol=symbol,
        tf=tf,
        date=bars.index[-1].date(),
        close=close,
        pivot=top,
        support=bottom,
        score=round(float(score), 1),
        label="Coiling at box top" if dist <= p.max_distance / 2 else "Inside box",
        metrics={
            "Box height %": round(height * 100, 1),
            "Box bars": n - top_idx,
            "Top tests": tests,
            "Top vs 52W high %": round((top / prior_high - 1) * 100, 1),
            "Vol ratio": None if vr != vr else round(vr, 2),
            "RS vs MA %": None if rs_vs_ma != rs_vs_ma else round(rs_vs_ma, 1),
        },
        notes=notes,
        chart=chart(c, top_idx - p.lookback // 2, {"pivot": top, "support": bottom}, zone_start=top_idx),
        zones={"start": top_idx, "lead_start": max(0, top_idx - p.lookback), "bottom_idx": bottom_idx,
               "recent": p.vol_recent * 2, "pattern_name": "box"},
    )
