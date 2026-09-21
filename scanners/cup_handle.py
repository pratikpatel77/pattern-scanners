"""Cup & Handle scanner (William O'Neil).

    left rim            right rim  pivot = handle high
       |\                 /\__
       | \               /    \_/  <- handle: shallow drift in the upper half
       |  \_           _/
       |    \___   ___/            <- cup: 12-35% deep, U-shaped, 7-65 weeks
       |        \_/
  prior uptrend >= 20%

The buy trigger is a move above the pivot (the right rim / handle high).

This scanner finds a complete cup plus a handle that is still forming
(close <= pivot) with price close to the pivot:

  hard filters
    * prior advance of >= prior_advance into the left rim
    * left rim and right rim are local highs and the highest points of the cup
    * right rim within 90%-105% of the left rim
    * cup depth and length within limits, bottom not at either edge (no V / slide)
    * handle length and depth within limits, handle low in the upper half of the cup
    * close within max_distance below the pivot

  score (0-100): proximity to pivot, U-shape roundness, depth quality, rim
  symmetry, prior uptrend strength, handle tightness and volume dry-up,
  trend and RS vs Nifty.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .common import Signal, arrays, chart, mean, ratio_score, relative_strength, sma

NAME = "cup"
TITLE = "Cup & Handle"
DESCRIPTION = "Completed cups with a handle forming in the upper half, price just under the pivot."


@dataclass(frozen=True)
class Params:
    cup_min: int
    cup_max: int
    depth_min: float
    depth_max: float
    handle_min: int
    handle_max: int
    handle_depth_min: float
    handle_depth_max: float
    rim_ratio_min: float
    rim_ratio_max: float
    rim_window: int        # rims must be the highest high within this many bars on each side
    prior_len: int
    prior_advance: float
    bottom_pos_min: float
    bottom_pos_max: float
    max_distance: float
    trend_ma: int


PARAMS = {
    "D": Params(cup_min=35, cup_max=300, depth_min=0.12, depth_max=0.35, handle_min=5, handle_max=25,
                handle_depth_min=0.02, handle_depth_max=0.12, rim_ratio_min=0.90, rim_ratio_max=1.05, rim_window=10,
                prior_len=130, prior_advance=0.20, bottom_pos_min=0.2, bottom_pos_max=0.8, max_distance=0.05, trend_ma=50),
    "W": Params(cup_min=7, cup_max=65, depth_min=0.12, depth_max=0.40, handle_min=1, handle_max=6,
                handle_depth_min=0.02, handle_depth_max=0.15, rim_ratio_min=0.90, rim_ratio_max=1.05, rim_window=2,
                prior_len=26, prior_advance=0.20, bottom_pos_min=0.2, bottom_pos_max=0.8, max_distance=0.08, trend_ma=10),
}


def _band_score(x: float, lo: float, ideal_lo: float, ideal_hi: float, hi: float) -> float:
    if x < ideal_lo:
        return ratio_score(x, lo, ideal_lo)
    if x > ideal_hi:
        return ratio_score(x, hi, ideal_hi)
    return 1.0


def scan(symbol: str, bars: pd.DataFrame, tf: str, bench: pd.DataFrame | None = None) -> Signal | None:
    p = PARAMS[tf]
    _, h, l, c, v = arrays(bars)
    n = len(c)
    if n < p.cup_min + p.handle_min + p.prior_len // 2:
        return None

    hs, ls = pd.Series(h), pd.Series(l)
    prev_max = hs.rolling(p.rim_window, min_periods=1).max().shift(1).to_numpy()
    next_max = hs[::-1].rolling(p.rim_window, min_periods=1).max().shift(1)[::-1].to_numpy()
    prior_min = ls.rolling(p.prior_len, min_periods=p.prior_len // 2).min().shift(1).to_numpy()
    close = float(c[-1])

    best = None
    for handle_len in range(p.handle_min, p.handle_max + 1):
        iR = n - 1 - handle_len
        lo = max(p.rim_window, iR - p.cup_max)
        hi_left = iR - p.cup_min
        if hi_left < lo:
            break
        pivot = float(h[iR])
        if h[iR + 1 :].max() > pivot or not (pivot >= prev_max[iR]):
            continue
        handle_low = float(l[iR + 1 :].min())
        handle_depth = 1 - handle_low / pivot
        if not (p.handle_depth_min <= handle_depth <= p.handle_depth_max):
            continue
        dist = pivot / close - 1
        if dist > p.max_distance:
            continue

        # suffix max/min over the cup interior for every possible left rim at once
        H, L = h[lo:iR], l[lo:iR]
        m = len(H)
        sufmax = np.maximum.accumulate(H[::-1])[::-1]
        rev = L[::-1]
        acc = np.minimum.accumulate(rev)
        pos = np.maximum.accumulate(np.where(rev <= acc, np.arange(m), 0))
        sufmin = acc[::-1]
        sufargmin = lo + (m - 1 - pos[::-1])

        iL = np.arange(lo, hi_left + 1)
        j = iL - lo + 1
        left = h[iL]
        interior_max = sufmax[j]
        bottom = sufmin[j]
        iB = sufargmin[j]
        cup_len = iR - iL
        rim_ratio = pivot / left
        depth = 1 - bottom / left
        bpos = (iB - iL) / cup_len
        with np.errstate(invalid="ignore", divide="ignore"):
            prior = left / prior_min[iL] - 1

        mask = (
            (left >= prev_max[iL]) & (left >= next_max[iL])
            & (interior_max <= np.maximum(left, pivot))
            & (rim_ratio >= p.rim_ratio_min) & (rim_ratio <= p.rim_ratio_max)
            & (depth >= p.depth_min) & (depth <= p.depth_max)
            & (bpos >= p.bottom_pos_min) & (bpos <= p.bottom_pos_max)
            & (handle_low >= bottom + 0.5 * (pivot - bottom))
            & (prior >= p.prior_advance)
        )
        for k in np.nonzero(mask)[0]:
            il, ib, b = int(iL[k]), int(iB[k]), float(bottom[k])
            cup_closes = c[il : iR + 1]
            roundness = float(np.mean(cup_closes <= b + 0.3 * (left[k] - b)))
            cand = {
                "iL": il, "iR": iR, "iB": ib, "left": float(left[k]), "bottom": b, "pivot": pivot,
                "handle_len": handle_len, "handle_low": handle_low, "handle_depth": handle_depth,
                "depth": float(depth[k]), "rim_ratio": float(rim_ratio[k]), "bpos": float(bpos[k]),
                "prior": float(prior[k]), "roundness": roundness, "dist": dist,
            }
            cand["shape"] = (
                25 * ratio_score(dist, p.max_distance, 0)
                + 15 * ratio_score(roundness, 0.15, 0.40)
                + 10 * _band_score(cand["depth"], p.depth_min, 0.15, 0.30, p.depth_max)
                + 10 * ratio_score(abs(cand["rim_ratio"] - 1), 0.10, 0.0)
                + 5 * ratio_score(abs(cand["bpos"] - 0.5), 0.3, 0.0)
                + 10 * ratio_score(cand["prior"], p.prior_advance, p.prior_advance * 2.5)
                + 8 * ratio_score(handle_depth, p.handle_depth_max, p.handle_depth_max * 0.4)
            )
            if best is None or cand["shape"] > best["shape"]:
                best = cand

    if best is None:
        return None

    notes = []
    iL, iR = best["iL"], best["iR"]
    handle_vol, cup_vol = mean(v[iR + 1 :]), mean(v[iL : iR + 1])
    vr = handle_vol / cup_vol if cup_vol > 0 else float("nan")
    s_vol = 7 * ratio_score(vr, 1.2, 0.7)
    if vr == vr and vr < 0.85:
        notes.append("handle volume drying up")

    ma = sma(c, p.trend_ma)
    s_trend = 5 * (ma[-1] == ma[-1] and close > ma[-1])
    rs_vs_ma, _ = relative_strength(bars, bench, p.trend_ma, 1)
    s_rs = 5 * ratio_score(rs_vs_ma, -5, 5)

    if best["roundness"] >= 0.35:
        notes.append("rounded U bottom")
    if abs(best["rim_ratio"] - 1) <= 0.03:
        notes.append("symmetric rims")
    if best["handle_depth"] <= p.handle_depth_max / 2:
        notes.append("tight handle")

    score = best["shape"] + s_vol + s_trend + s_rs
    cup_len = iR - iL
    return Signal(
        scanner=NAME,
        symbol=symbol,
        tf=tf,
        date=bars.index[-1].date(),
        close=close,
        pivot=best["pivot"],
        support=best["handle_low"],
        score=round(float(score), 1),
        label="Handle near pivot" if best["dist"] <= p.max_distance / 2 else "Handle forming",
        metrics={
            "Cup bars": cup_len,
            "Cup depth %": round(best["depth"] * 100, 1),
            "Handle bars": best["handle_len"],
            "Handle depth %": round(best["handle_depth"] * 100, 1),
            "Rim ratio": round(best["rim_ratio"], 3),
            "Prior advance %": round(best["prior"] * 100),
            "Handle vol ratio": None if vr != vr else round(vr, 2),
        },
        notes=notes,
        chart=chart(c, iL - cup_len // 5, {"pivot": best["pivot"], "support": best["handle_low"], "bottom": best["bottom"]}, zone_start=iL),
        zones={"start": iL, "lead_start": max(0, iL - p.prior_len), "bottom_idx": best["iB"],
               "handle_start": iR + 1, "recent": best["handle_len"], "pattern_name": "cup"},
    )
