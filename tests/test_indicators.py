import unittest

import pandas as pd

from trading_scanner.indicators import wilder_rsi


class WilderRSITests(unittest.TestCase):
    def test_known_wilder_example_first_rsi(self):
        # Classic 14-period Wilder worksheet series; first RSI is ~70.46.
        closes = pd.Series(
            [
                44.34, 44.09, 44.15, 43.61, 44.33,
                44.83, 45.10, 45.42, 45.84, 46.08,
                45.89, 46.03, 45.61, 46.28, 46.28,
            ]
        )
        rsi = wilder_rsi(closes, 14)
        self.assertAlmostEqual(rsi.iloc[14], 70.46, places=1)

    def test_constant_series_is_neutral_after_seed(self):
        rsi = wilder_rsi(pd.Series([100.0] * 30), 14)
        self.assertEqual(rsi.iloc[14], 50.0)
        self.assertTrue((rsi.iloc[14:] == 50.0).all())

    def test_insufficient_data_returns_nan(self):
        rsi = wilder_rsi(pd.Series([1.0, 2.0, 3.0]), 14)
        self.assertTrue(rsi.isna().all())


if __name__ == "__main__":
    unittest.main()

