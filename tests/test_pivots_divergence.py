import unittest

import pandas as pd

from trading_scanner.divergence import find_regular_divergences
from trading_scanner.pivots import find_pivots


def frame_with_dates(n=25):
    index = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame(
        {
            "Open": [100.0] * n,
            "High": [101.0] * n,
            "Low": [99.0] * n,
            "Close": [100.0] * n,
        },
        index=index,
    )


class PivotTests(unittest.TestCase):
    def test_strict_low_pivot(self):
        df = frame_with_dates(12)
        df.iloc[5, df.columns.get_loc("Low")] = 90.0
        pivots = find_pivots(df["Low"], "low", left=2, right=2)
        self.assertEqual([p.position for p in pivots], [5])

    def test_equal_lows_are_rejected(self):
        df = frame_with_dates(12)
        df.iloc[5, df.columns.get_loc("Low")] = 90.0
        df.iloc[6, df.columns.get_loc("Low")] = 90.0
        pivots = find_pivots(df["Low"], "low", left=2, right=2)
        self.assertEqual(pivots, [])


class DivergenceTests(unittest.TestCase):
    def test_bullish_regular(self):
        df = frame_with_dates()
        df.iloc[5, df.columns.get_loc("Low")] = 90.0
        df.iloc[15, df.columns.get_loc("Low")] = 85.0
        rsi = pd.Series(50.0, index=df.index)
        rsi.iloc[5] = 30.0
        rsi.iloc[15] = 40.0
        signals = find_regular_divergences(
            "TEST", "1D", df, rsi,
            left=2, right=2, min_distance=3, max_distance=20,
        )
        bull = [s for s in signals if s.kind == "bullish_regular"]
        self.assertEqual(len(bull), 1)
        self.assertEqual(bull[0].confirmation_position, 17)
        self.assertEqual(bull[0].distance_bars, 10)

    def test_bearish_regular(self):
        df = frame_with_dates()
        df.iloc[5, df.columns.get_loc("High")] = 110.0
        df.iloc[15, df.columns.get_loc("High")] = 115.0
        rsi = pd.Series(50.0, index=df.index)
        rsi.iloc[5] = 70.0
        rsi.iloc[15] = 60.0
        signals = find_regular_divergences(
            "TEST", "1D", df, rsi,
            left=2, right=2, min_distance=3, max_distance=20,
        )
        bear = [s for s in signals if s.kind == "bearish_regular"]
        self.assertEqual(len(bear), 1)

    def test_equal_price_is_not_divergence(self):
        df = frame_with_dates()
        df.iloc[5, df.columns.get_loc("Low")] = 90.0
        df.iloc[15, df.columns.get_loc("Low")] = 90.0
        rsi = pd.Series(50.0, index=df.index)
        rsi.iloc[5] = 30.0
        rsi.iloc[15] = 40.0
        signals = find_regular_divergences(
            "TEST", "1D", df, rsi,
            left=2, right=2, min_distance=3, max_distance=20,
        )
        self.assertFalse(any(s.kind == "bullish_regular" for s in signals))

    def test_independent_rsi_pivot_alignment(self):
        df = frame_with_dates()
        df.iloc[5, df.columns.get_loc("Low")] = 90.0
        df.iloc[15, df.columns.get_loc("Low")] = 85.0
        rsi = pd.Series(50.0, index=df.index)
        # RSI pivots are deliberately one bar after the price pivots.
        rsi.iloc[6] = 30.0
        rsi.iloc[16] = 40.0
        signals = find_regular_divergences(
            "TEST", "1D", df, rsi,
            left=2, right=2, min_distance=3, max_distance=20,
            rsi_alignment_mode="rsi_pivot", rsi_pivot_window=2,
        )
        bull = [s for s in signals if s.kind == "bullish_regular"]
        self.assertEqual(len(bull), 1)
        self.assertEqual(bull[0].first_rsi_time, df.index[6])
        self.assertEqual(bull[0].second_rsi_time, df.index[16])
        self.assertEqual(bull[0].confirmation_position, 18)


if __name__ == "__main__":
    unittest.main()
