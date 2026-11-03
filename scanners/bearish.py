"""Bearish scanners: the upside-down versions of the three bullish patterns.

Each bearish pattern is the exact mirror of a bullish one, so it reuses the
same tested detection rules. Prices are flipped with p -> K/p (K = last close
squared, so flipped prices stay on the same scale): a high becomes a low, an
up day becomes a down day, and a percentage range stays the same size. The
bullish scanner runs on the flipped bars, and its levels are flipped back.

  Stage 3 -> Stage 4      Weinstein top: after an advance, price chops around a
                          flattening 30-week MA (150-day on daily), closes BELOW
                          the MA and sits near the low of the topping range.
                          Breakdown = range low, stop = recent swing high.
  Darvas Breakdown Box    Confirmed box whose bottom is at or near the 52-week
                          low, price pressing on the bottom.
                          Breakdown = box bottom, stop = box top.
  Inverted Cup & Handle   After a decline of 20%+, a rounded dome 13-54% tall
                          with lows at matching levels, then a shallow bounce
                          (the handle) in the lower half of the dome.
                          Breakdown = handle low, stop = handle high.

Thresholds live in the bullish modules' PARAMS and apply to both directions.
"""
from __future__ import annotations

import re
from dataclasses import replace

import numpy as np
import pandas as pd

from . import cup_handle, darvas, stage
from .common import Signal

PRICE_COLS = ("open", "high", "low", "close")


def flip(bars: pd.DataFrame | None, k: float | None = None):
    """Mirror prices with p -> k/p. Returns (flipped bars, k), or (None, k) if prices are unusable."""
    if bars is None or bars.empty:
        return None, k
    px = bars[list(PRICE_COLS)].to_numpy(float)
    if not np.isfinite(px).all() or (px <= 0).any():
        return None, k
    if k is None:
        k = float(px[-1, 3]) ** 2
    out = bars.copy()
    out["open"] = k / bars["open"]
    out["high"] = k / bars["low"]
    out["low"] = k / bars["high"]
    out["close"] = k / bars["close"]
    return out, k


# --- converting a metric measured on flipped prices back to real-price terms
def _same(x, k):
    return x


def _price(x, k):
    return k / x if x else x


def _recip_pct(x, k):
    """A % change a'/b'-1 on flipped prices is b/a-1 on real ones."""
    return (100 / (100 + x) - 1) * 100


def _rise(x, k):
    """A flipped depth 1-a'/b' (%) is a real rise b/a-1 (%)."""
    return (1 / (1 - x / 100) - 1) * 100 if x < 100 else x


def _fall(x, k):
    """A flipped rise a'/b'-1 (%) is a real fall 1-b/a (%)."""
    return (1 - 1 / (1 + x / 100)) * 100


def _recip(x, k):
    return 1 / x if x else x


class BearScanner:
    """Wraps a bullish scanner module and runs it on mirrored prices."""
    DIRECTION = "short"

    def __init__(self, base, name, title, description, labels, notes, metrics, pattern_name, lines=None):
        self.base = base
        self.NAME, self.TITLE, self.DESCRIPTION = name, title, description
        self.PARAMS = base.PARAMS
        self.labels = labels            # [(old substring, new)] applied in order
        self.notes = notes              # {old note: new note} plus regex pairs
        self.metrics = metrics          # {old key: (new key, transform, decimals)}
        self.pattern_name = pattern_name
        self.lines = lines or {}        # chart line renames

    def scan(self, symbol: str, bars: pd.DataFrame, tf: str, bench: pd.DataFrame | None = None) -> Signal | None:
        fb, k = flip(bars)
        if fb is None:
            return None
        fbench, _ = flip(bench)
        sig = self.base.scan(symbol, fb, tf, fbench)
        if sig is None:
            return None

        c = bars["close"].to_numpy(float)
        n_ch = len(sig.chart.get("closes", []))
        chart = {
            "closes": [round(float(v), 2) for v in c[len(c) - n_ch:]] if n_ch else [],
            "lines": {self.lines.get(name, name): round(k / v, 2) for name, v in sig.chart.get("lines", {}).items() if v},
            "zone_start": sig.chart.get("zone_start"),
        }
        label = sig.label
        for old, new in self.labels:
            label = label.replace(old, new)
        notes = []
        for note in sig.notes:
            new = self.notes.get(note)
            if new is None:
                new = note
                for pattern, repl in self.notes.get("__regex__", []):
                    new = re.sub(pattern, repl, new)
            notes.append(new)
        metrics = {}
        for key, value in sig.metrics.items():
            new_key, fn, nd = self.metrics.get(key, (key, _same, None))
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
                value = fn(float(value), k) if fn is not _same else value
                if nd is not None:
                    value = round(value) if nd == 0 else round(value, nd)
            metrics[new_key] = value

        return replace(
            sig,
            scanner=self.NAME,
            close=float(c[-1]),
            pivot=float(k / sig.pivot),
            support=float(k / sig.support),
            label=label,
            metrics=metrics,
            notes=notes,
            chart=chart,
            zones={**sig.zones, "pattern_name": self.pattern_name},
            direction="short",
        )


STAGE4 = BearScanner(
    stage, "stage_bear", "Stage 3 -> Stage 4",
    "Stage 3 tops (price chopping around a flattening 30-week MA after an advance) about to break down into a Stage 4 decline.",
    labels=[("Late Stage 1", "Late Stage 3"), ("Stage 1 base", "Stage 3 top"),
            ("after decline", "after advance"), ("sideways base", "sideways top")],
    notes={"MA turning up": "MA turning down", "higher lows": "lower highs",
           "RS vs Nifty rising": "RS vs Nifty falling", "tight near pivot": "tight near breakdown"},
    metrics={"Base range %": ("Top range %", _same, None), "Base low": ("Top high", _price, 2),
             "Base bars": ("Top bars", _same, None),
             "MA slope %": ("MA slope %", _recip_pct, 2), "% closes > MA": ("% closes < MA", _same, None),
             "Higher lows %": ("Lower highs %", _fall, 1), "RS vs MA %": ("RS vs MA %", _recip_pct, 1)},
    pattern_name="top",
)

DARVAS_BEAR = BearScanner(
    darvas, "darvas_bear", "Darvas Breakdown Box",
    "Confirmed Darvas boxes at 52-week lows with price pressing on the box bottom.",
    labels=[("Coiling at box top", "Pressing on box bottom")],
    notes={"uptrend aligned": "downtrend aligned", "top = new 52W high": "bottom = new 52W low",
           "__regex__": [(r"^top tested", "bottom tested")]},
    metrics={"Top tests": ("Bottom tests", _same, None),
             "Top vs 52W high %": ("Bottom vs 52W low %", _recip_pct, 1),
             "RS vs MA %": ("RS vs MA %", _recip_pct, 1)},
    pattern_name="box",
)

CUP_BEAR = BearScanner(
    cup_handle, "cup_bear", "Inverted Cup & Handle",
    "Rounded tops after a decline, with a shallow bounce (handle) in the lower half, price just above the breakdown level.",
    labels=[("Handle near pivot", "Handle near breakdown")],
    notes={"rounded U bottom": "rounded dome top", "handle volume drying up": "handle volume drying up"},
    metrics={"Cup bars": ("Dome bars", _same, None), "Cup depth %": ("Dome height %", _rise, 1),
             "Handle depth %": ("Handle bounce %", _rise, 1), "Rim ratio": ("Rim ratio", _recip, 3),
             "Prior advance %": ("Prior decline %", _fall, 0)},
    pattern_name="dome",
    lines={"bottom": "top"},
)

BEARISH = (STAGE4, DARVAS_BEAR, CUP_BEAR)
