"""Tests for the volume + delivery + price-action rules.

Synthetic patterns with a known character: if buying days carry the volume and
the delivery, the read must say accumulation; if selling days do, it must say
the opposite; and it must still work when NSE delivery data is missing.

    python -m unittest discover tests
"""
import sys
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import analyse, measure, points, qty  # noqa: E402
from scanners.common import Signal  # noqa: E402

RNG = np.random.default_rng(3)


def make_bars(n=260, accumulation=True, delivery=True, base_level=100.0):
    """A run-up then a flat base. In the accumulation case the up days carry
    the volume and the delivery; in the distribution case the down days do."""
    lead = np.linspace(base_level * 0.7, base_level, 120)
    base = base_level + np.sin(np.linspace(0, 6 * np.pi, n - 120)) * 4
    close = np.concatenate([lead, base])
    up = np.r_[True, np.diff(close) > 0]

    vol = np.where(up, 2.0e5, 1.0e5) if accumulation else np.where(up, 1.0e5, 2.0e5)
    vol = vol * RNG.uniform(0.9, 1.1, len(close))
    vol[:120] *= 1.4  # the lead-in trades heavier than the base

    idx = pd.bdate_range(end=date(2026, 9, 18), periods=len(close))
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": vol},
        index=idx,
    )
    if delivery:
        pct = np.where(up, 70.0, 40.0) if accumulation else np.where(up, 40.0, 70.0)
        df["deliv_pct"] = pct
        df["deliv_qty"] = vol * pct / 100
    return df


def make_signal(df, tf="D"):
    n = len(df)
    return Signal(
        scanner="stage", symbol="TEST", tf=tf, date=df.index[-1].date(),
        close=float(df["close"].iloc[-1]), pivot=float(df["high"].max()),
        support=float(df["low"].iloc[-30:].min()), score=70.0, label="test base",
        zones={"start": 120, "lead_start": 0, "recent": 15, "pattern_name": "base"},
    )


class VerdictTest(unittest.TestCase):
    def test_accumulation_is_recognised(self):
        df = make_bars(accumulation=True)
        a = analyse(make_signal(df), df)
        self.assertGreater(a["pressure"], 20)
        self.assertIn(a["verdict"], ("Accumulation", "Strong accumulation"))
        self.assertTrue(a["has_delivery"])

    def test_distribution_is_recognised(self):
        df = make_bars(accumulation=False)
        a = analyse(make_signal(df), df)
        self.assertLess(a["pressure"], -20)
        self.assertIn(a["verdict"], ("Caution", "Distribution"))

    def test_works_without_delivery_data(self):
        df = make_bars(accumulation=True, delivery=False)
        a = analyse(make_signal(df), df)
        self.assertFalse(a["has_delivery"])
        self.assertGreater(a["pressure"], 0)
        text = " ".join(b["text"] for s in a["sections"] for b in s["bullets"])
        self.assertIn("No NSE delivery data", text)

    def test_delivery_changes_the_read(self):
        """Same price and volume, only the delivery split flipped."""
        df = make_bars(accumulation=True)
        good = analyse(make_signal(df), df)["pressure"]
        flipped = df.copy()
        flipped["deliv_pct"] = 110 - flipped["deliv_pct"]
        flipped["deliv_qty"] = flipped["volume"] * flipped["deliv_pct"] / 100
        worse = analyse(make_signal(flipped), flipped)["pressure"]
        self.assertGreater(good, worse)


class OutputShapeTest(unittest.TestCase):
    def setUp(self):
        self.df = make_bars()
        self.a = analyse(make_signal(self.df), self.df)

    def test_sections_and_bullets_are_readable(self):
        titles = [s["title"] for s in self.a["sections"]]
        for expected in ("Volume", "Delivery", "Price action", "What to watch"):
            self.assertIn(expected, titles)
        for s in self.a["sections"]:
            self.assertTrue(s["bullets"])
            for b in s["bullets"]:
                self.assertGreater(len(b["text"]), 15)
                self.assertIn(b["tone"], ("good", "bad", "flat"))
                self.assertNotIn("nan", b["text"].lower())

    def test_table_cell_is_short(self):
        self.assertLessEqual(len(self.a["chips"]), 2)
        self.assertLess(len(self.a["headline"]), 130)

    def test_weekly_pattern(self):
        from datalayer.timeframe import to_weekly

        w = to_weekly(self.df)
        self.assertIn("deliv_pct", w.columns)
        a = analyse(make_signal(w, tf="W"), w)
        text = " ".join(b["text"] for s in a["sections"] for b in s["bullets"])
        self.assertIn("week", text)


class MetricTest(unittest.TestCase):
    def test_up_down_volume_ratio(self):
        df = make_bars(accumulation=True)
        m = measure(make_signal(df), df)
        self.assertGreater(m["ud_vol"], 1.4)
        self.assertLess(m["vol_vs_lead"], 1.0)      # base is quieter than the run-up
        self.assertGreater(m["deliv_ud_gap"], 10)   # delivery much higher on up days

    def test_formatting_helpers(self):
        self.assertEqual(qty(13_000_000), "1.3 Cr")
        self.assertEqual(qty(250_000), "2.5 L")
        self.assertEqual(qty(8200), "8,200")
        self.assertEqual(points(1), "+1 point")
        self.assertEqual(points(-4), "-4 points")


if __name__ == "__main__":
    unittest.main()
