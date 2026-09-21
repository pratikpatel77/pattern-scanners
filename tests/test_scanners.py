"""Synthetic-pattern tests: each scanner must find its textbook pattern while
price is below the pivot, and must NOT fire once the pattern has broken out.

    python -m unittest discover tests
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datalayer.timeframe import to_weekly  # noqa: E402
from scanners import cup_handle, darvas, stage  # noqa: E402

RNG = np.random.default_rng(7)


def frame(closes, noise=0.004, vol=None):
    closes = np.asarray(closes, float)
    wig = closes * noise
    idx = pd.bdate_range(end="2026-09-16", periods=len(closes))
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + wig,
            "low": closes - wig,
            "close": closes,
            "volume": np.full(len(closes), 1e6) if vol is None else vol,
        },
        index=idx,
    )


def seg(a, b, n):
    return list(np.linspace(a, b, n, endpoint=False))


def chop(level, amp, n, period=40):
    t = np.arange(n)
    return list(level + amp * np.sin(2 * np.pi * t / period))


class StageTest(unittest.TestCase):
    def series(self, tail_to):
        # decline, then a 1-year sideways base 90..110 with rising lows, then approach 110
        pre = seg(200, 100, 200)
        base = chop(100, 9, 220)
        lows = np.linspace(0, 5, 220)
        base = list(np.maximum(np.array(base), 91 + lows))
        return pre + base + seg(base[-1], tail_to, 20)

    def test_finds_late_stage1(self):
        sig = stage.scan("X", frame(self.series(107)), "D")
        self.assertIsNotNone(sig)
        self.assertLess(sig.distance_pct, 6)
        self.assertGreaterEqual(sig.close, 100)

    def test_ignores_breakout(self):
        s = self.series(107) + seg(107, 125, 10)
        self.assertIsNone(stage.scan("X", frame(s), "D"))

    def test_ignores_downtrend(self):
        self.assertIsNone(stage.scan("X", frame(seg(300, 100, 500)), "D"))


class DarvasTest(unittest.TestCase):
    def series(self, tail):
        # uptrend to a new high at 150, 30-bar box 138..150, then drift to `tail`
        up = seg(80, 150, 260)
        box = chop(144, 6, 30, period=15)
        return up + [150] + box + seg(box[-1], tail, 6)

    def test_finds_box_near_top(self):
        sig = darvas.scan("X", frame(self.series(148), noise=0.002), "D")
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.pivot, 150 * 1.002, delta=0.5)
        self.assertLess(sig.distance_pct, 4)

    def test_unconfirmed_top_is_ignored(self):
        s = seg(80, 160, 300)  # straight up: every bar is a new high
        self.assertIsNone(darvas.scan("X", frame(s), "D"))

    def test_far_from_top_is_ignored(self):
        self.assertIsNone(darvas.scan("X", frame(self.series(139), noise=0.002), "D"))


class CupHandleTest(unittest.TestCase):
    def series(self, handle_tail, handle_len=12):
        prior = seg(60, 100, 150)                                  # +67% advance into left rim
        t = np.linspace(0, np.pi, 120)
        cup = list(100 - 25 * np.sin(t))                           # 25% deep rounded cup back to 100
        handle = seg(100, 94, handle_len // 2) + seg(94, handle_tail, handle_len - handle_len // 2)
        return prior + cup + [100.5] + handle

    def test_finds_cup_with_handle(self):
        sig = cup_handle.scan("X", frame(self.series(97), noise=0.002), "D")
        self.assertIsNotNone(sig)
        self.assertAlmostEqual(sig.metrics["Cup depth %"], 25, delta=3)
        self.assertLess(sig.distance_pct, 5)
        self.assertLess(sig.support, sig.close)

    def test_breakout_is_ignored(self):
        s = self.series(97) + [99, 102, 104]
        self.assertIsNone(cup_handle.scan("X", frame(s, noise=0.002), "D"))

    def test_v_shape_or_too_deep_is_ignored(self):
        s = seg(60, 100, 150) + seg(100, 40, 60) + seg(40, 100, 60) + [100.5] + seg(100, 97, 12)
        self.assertIsNone(cup_handle.scan("X", frame(s, noise=0.002), "D"))

    def test_weekly(self):
        # weekly-sized shape: 30-week advance, 30-week cup, 3-week handle
        cup = list(100 - 22 * np.sin(np.linspace(0, np.pi, 30)))
        weeks = seg(65, 100, 30) + cup + [100.5, 97, 95.5, 97.5]
        sig = cup_handle.scan("X", frame(weeks, noise=0.003), "W")
        self.assertIsNotNone(sig)
        self.assertEqual(sig.metrics["Handle bars"], 3)


class WeeklyResampleTest(unittest.TestCase):
    def test_matches_pandas_grouping(self):
        df = frame(100 + RNG.normal(0, 1, 400).cumsum())
        w = to_weekly(df)
        g = df.groupby(df.index.to_period("W-FRI"))
        self.assertTrue(np.allclose(w["high"].values, g["high"].max().values))
        self.assertTrue(np.allclose(w["close"].values, g["close"].last().values))
        self.assertEqual(w.index[-1], df.index[-1])


if __name__ == "__main__":
    unittest.main()
