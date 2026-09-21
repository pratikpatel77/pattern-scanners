"""Stage 1 -> Stage 2 scanner (Stan Weinstein stage analysis).

Stage 1 = a basing area after a decline or a sideways drift: price chops
around a flattening 30-week MA (150-day on daily) inside a bounded range.
Stage 2 starts when price breaks above the top of that base on volume with
the MA turning up.

This scanner finds stocks STILL INSIDE the base (close <= base high) that
already show the transition signs:

  hard filters (all required)
    * a base of >= base_min bars whose high/low range is <= max_base_range
    * close above the long MA, MA slope flat-to-rising (not a Stage 4 slide)
    * price has actually chopped around the MA during the base (not a stage-2 flag)
    * no big run-up right before the base (that would be a Stage 3 top)
    * close within max_distance below the base high (the breakout pivot)

  score (0-100): proximity to pivot, MA turning up, higher lows inside the
  base, volume expansion, relative strength vs Nifty 50, recent tightness.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .common import Signal, arrays, chart, ratio_score, relative_strength, sma, volume_ratio

NAME = "stage"
TITLE = "Stage 1 -> Stage 2"
DESCRIPTION = "Stage 1 bases (price chopping around a flattening 30-week MA) about to break into a Stage 2 advance."


@dataclass(frozen=True)
class Params:
    ma_len: int              # Weinstein's 30-week MA (150 days)
    slope_len: int           # bars over which the MA slope is measured
    base_min: int
    base_max: int
    max_base_range: float    # base high / base low - 1
    max_distance: float      # close must be within this below the base high
    min_ma_slope: float
    max_ma_slope: float
    above_ma_min: float      # share of base closes above the MA
    above_ma_max: float
    prior_len: int           # bars before the base checked for a run-up
    max_prior_advance: float
    vol_recent: int
    tight_len: int
    breakout_tol: float = 0.02   # recent close this far above the older base high = already broke out


PARAMS = {
    "D": Params(ma_len=150, slope_len=20, base_min=60, base_max=250, max_base_range=0.35, max_distance=0.06,
                min_ma_slope=-0.02, max_ma_slope=0.08, above_ma_min=0.25, above_ma_max=0.90,
                prior_len=120, max_prior_advance=0.40, vol_recent=10, tight_len=15),
    "W": Params(ma_len=30, slope_len=4, base_min=12, base_max=52, max_base_range=0.40, max_distance=0.08,
                min_ma_slope=-0.03, max_ma_slope=0.10, above_ma_min=0.25, above_ma_max=0.90,
                prior_len=26, max_prior_advance=0.40, vol_recent=3, tight_len=4),
}


def scan(symbol: str, bars: pd.DataFrame, tf: str, bench: pd.DataFrame | None = None) -> Signal | None:
    p = PARAMS[tf]
    _, h, l, c, v = arrays(bars)
    n = len(c)
    if n < p.ma_len + p.base_min:
        return None
    ma = sma(c, p.ma_len)

    # Longest base ending today whose range stays within limits. Range only grows
    # as we look further back, so the valid lengths form a prefix.
    hi_rev = np.maximum.accumulate(h[:-1][::-1])   # hi_rev[k] = max high of the k+1 bars before today
    lo_rev = np.minimum.accumulate(l[::-1])        # lo_rev[k] = min low of the last k+1 bars
    l_max = min(p.base_max, n - p.ma_len + 1, len(hi_rev) + 1)
    if l_max < p.base_min:
        return None
    lengths = np.arange(2, l_max + 1)
    rng = hi_rev[lengths - 2] / lo_rev[lengths - 1] - 1
    fail = np.nonzero(rng > p.max_base_range)[0]
    if len(fail) and fail[0] == 0:
        return None
    base_len = int(lengths[fail[0] - 1]) if len(fail) else int(lengths[-1])
    if base_len < p.base_min:
        return None

    s = n - base_len
    resistance = float(hi_rev[base_len - 2])
    base_low = float(lo_rev[base_len - 1])
    close = float(c[-1])

    if close > resistance:
        return None
    # Reject bases that only "contain" price because a recent breakout widened them:
    # compare the last tight_len closes with the base high as it stood before them.
    older_high = h[s : n - p.tight_len].max() if base_len > p.tight_len else resistance
    if c[-p.tight_len :].max() > older_high * (1 + p.breakout_tol):
        return None
    dist = resistance / close - 1
    if dist > p.max_distance or close <= ma[-1]:
        return None

    ma_slope = ma[-1] / ma[-1 - p.slope_len] - 1
    if not (p.min_ma_slope <= ma_slope <= p.max_ma_slope):
        return None

    above = float(np.mean(c[s:] > ma[s:]))
    if not (p.above_ma_min <= above <= p.above_ma_max):
        return None

    ps = max(0, s - p.prior_len)
    prior_advance = float("nan")
    if s - ps >= p.prior_len // 2:
        prior_advance = c[s] / c[ps:s].min() - 1
        if prior_advance > p.max_prior_advance:
            return None
        prior_change = c[s] / c[ps] - 1
    else:
        prior_change = float("nan")

    # ---- scoring ----
    notes = []
    s_prox = 25 * ratio_score(dist, p.max_distance, 0)

    ma_prev_slope = ma[-1 - p.slope_len] / ma[-1 - 2 * p.slope_len] - 1 if n > p.ma_len + 2 * p.slope_len else ma_slope
    s_ma = 8 * ratio_score(ma_slope, p.min_ma_slope, 0.01) + (7 if ma_slope > ma_prev_slope else 0)
    if ma_slope > 0 and ma_slope > ma_prev_slope:
        notes.append("MA turning up")

    third = max(1, base_len // 3)
    early_low = l[s : s + 2 * third].min()
    late_low = l[n - third :].min()
    higher_lows = late_low / early_low - 1
    s_hl = 15 * ratio_score(higher_lows, 0, 0.10)
    if higher_lows > 0.03:
        notes.append("higher lows")

    vr = volume_ratio(v, p.vol_recent, base_len)
    s_vol = 15 * ratio_score(vr, 0.9, 1.5)
    if vr == vr and vr >= 1.3:
        notes.append("volume expanding")

    rs_vs_ma, rs_slope = relative_strength(bars, bench, p.ma_len, p.slope_len)
    s_rs = 8 * ratio_score(rs_vs_ma, -5, 5) + 7 * ratio_score(rs_slope, -2, 5)
    if rs_vs_ma == rs_vs_ma and rs_vs_ma > 0 and rs_slope > 0:
        notes.append("RS vs Nifty rising")

    tight = h[-p.tight_len :].max() / l[-p.tight_len :].min() - 1
    s_tight = 10 * ratio_score(tight, p.max_base_range / 2, p.max_base_range / 6) + 5 * ratio_score(base_len, p.base_min, p.base_max * 0.6)
    if tight <= p.max_base_range / 5:
        notes.append("tight near pivot")

    score = s_prox + s_ma + s_hl + s_vol + s_rs + s_tight
    context = "after decline" if prior_change == prior_change and prior_change < -0.10 else "sideways base"
    support = float(l[-2 * p.tight_len :].min())

    return Signal(
        scanner=NAME,
        symbol=symbol,
        tf=tf,
        date=bars.index[-1].date(),
        close=close,
        pivot=resistance,
        support=support,
        score=round(float(score), 1),
        label=("Late Stage 1" if score >= 60 else "Stage 1 base") + f" ({context})",
        metrics={
            "Base bars": base_len,
            "Base range %": round(float(rng[base_len - 2]) * 100, 1),
            "Base low": round(base_low, 2),
            "MA slope %": round(float(ma_slope) * 100, 2),
            "% closes > MA": round(above * 100),
            "Higher lows %": round(float(higher_lows) * 100, 1),
            "Vol ratio": None if vr != vr else round(vr, 2),
            "RS vs MA %": None if rs_vs_ma != rs_vs_ma else round(rs_vs_ma, 1),
        },
        notes=notes,
        chart=chart(c, s - base_len // 4, {"pivot": resistance, "support": support, "ma": float(ma[-1])}, zone_start=s),
        zones={"start": s, "lead_start": ps, "recent": p.tight_len, "pattern_name": "base"},
    )
