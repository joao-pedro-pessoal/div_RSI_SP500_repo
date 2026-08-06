import unittest

import pandas as pd

from trading_scanner.tradingview_divergence import find_tradingview_regular_divergences


def frame(n=40):
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


class TradingViewExactLogicTests(unittest.TestCase):
    def test_regular_bullish_is_driven_by_rsi_pivots(self):
        df = frame()
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[10] = 45.0
        rsi.iloc[20] = 50.0  # Higher RSI low; neither needs RSI < 30.

        # The first sampled price is deliberately NOT a price pivot: neighbours
        # have lower lows. Pine still compares price at the RSI-pivot bars.
        df.iloc[10, df.columns.get_loc("Low")] = 100.0
        df.iloc[20, df.columns.get_loc("Low")] = 90.0

        signals = find_tradingview_regular_divergences(
            "TEST", "1D", df, rsi, left=5, right=5, range_lower=5, range_upper=60
        )
        bull = [s for s in signals if s.kind == "bullish_regular"]
        self.assertEqual(len(bull), 1)
        self.assertEqual(bull[0].first_pivot.position, 10)
        self.assertEqual(bull[0].second_pivot.position, 20)
        self.assertEqual(bull[0].first_rsi, 45.0)
        self.assertEqual(bull[0].second_rsi, 50.0)
        self.assertEqual(bull[0].confirmation_position, 25)
        self.assertEqual(bull[0].rsi_alignment_mode, "tradingview_rsi_pivot")

    def test_regular_bearish(self):
        df = frame()
        rsi = pd.Series(40.0, index=df.index)
        rsi.iloc[10] = 75.0
        rsi.iloc[20] = 68.0
        df.iloc[10, df.columns.get_loc("High")] = 110.0
        df.iloc[20, df.columns.get_loc("High")] = 120.0
        signals = find_tradingview_regular_divergences(
            "TEST", "1D", df, rsi, left=5, right=5, range_lower=5, range_upper=60
        )
        bear = [s for s in signals if s.kind == "bearish_regular"]
        self.assertEqual(len(bear), 1)
        self.assertEqual(bear[0].confirmation_position, 25)

    def test_pine_range_uses_shifted_previous_found_series(self):
        # In Pine `_inRange(plFound[1])`, a 5-bar distance produces barssince=4,
        # so rangeLower=5 rejects it. A 6-bar distance produces barssince=5.
        df = frame(30)
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[8] = 40.0
        rsi.iloc[13] = 45.0  # distance 5 => Pine barssince = 4
        df.iloc[8, df.columns.get_loc("Low")] = 95.0
        df.iloc[13, df.columns.get_loc("Low")] = 90.0
        signals = find_tradingview_regular_divergences(
            "TEST", "1D", df, rsi, left=2, right=2, range_lower=5, range_upper=60
        )
        self.assertFalse(any(s.kind == "bullish_regular" for s in signals))

        rsi.iloc[13] = 60.0
        rsi.iloc[14] = 45.0  # now distance 6 => Pine barssince = 5
        df.iloc[14, df.columns.get_loc("Low")] = 90.0
        signals = find_tradingview_regular_divergences(
            "TEST", "1D", df, rsi, left=2, right=2, range_lower=5, range_upper=60
        )
        self.assertTrue(any(s.kind == "bullish_regular" for s in signals))

    def test_only_previous_rsi_pivot_is_compared(self):
        df = frame(50)
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[10] = 30.0
        rsi.iloc[20] = 45.0
        rsi.iloc[30] = 40.0  # > first but < immediately previous
        df.iloc[10, df.columns.get_loc("Low")] = 100.0
        df.iloc[20, df.columns.get_loc("Low")] = 95.0
        df.iloc[30, df.columns.get_loc("Low")] = 90.0
        signals = find_tradingview_regular_divergences(
            "TEST", "1D", df, rsi, left=3, right=3, range_lower=5, range_upper=60
        )
        bull_second_positions = [s.second_pivot.position for s in signals if s.kind == "bullish_regular"]
        self.assertIn(20, bull_second_positions)
        self.assertNotIn(30, bull_second_positions)

    def test_unconfirmed_right_side_cannot_alert(self):
        df = frame(24)
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[8] = 40.0
        rsi.iloc[20] = 45.0
        df.iloc[8, df.columns.get_loc("Low")] = 95.0
        df.iloc[20, df.columns.get_loc("Low")] = 90.0
        signals = find_tradingview_regular_divergences(
            "TEST", "1D", df, rsi, left=3, right=5, range_lower=5, range_upper=60
        )
        self.assertFalse(any(s.second_pivot.position == 20 for s in signals))


if __name__ == "__main__":
    unittest.main()

