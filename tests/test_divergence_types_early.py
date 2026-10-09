import unittest

import numpy as np
import pandas as pd

from trading_scanner.models import Pivot
from trading_scanner.pivots import find_pivots
from trading_scanner.tradingview_divergence import (
    find_tradingview_divergences,
    find_tradingview_regular_divergences,
)

ALL = ("bullish_regular", "bearish_regular", "bullish_hidden", "bearish_hidden")


def frame(n=40):
    index = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"Open": [100.0] * n, "High": [101.0] * n, "Low": [99.0] * n, "Close": [100.0] * n},
        index=index,
    )


def _original_regular(ohlc, rsi, left, right, range_lower, range_upper):
    """Verbatim logic of the detector before this change, for equivalence."""
    out = []
    for pivot_kind, signal_kind in (("low", "bullish_regular"), ("high", "bearish_regular")):
        pivots = find_pivots(rsi, pivot_kind, left=left, right=right)
        for prev, cur in zip(pivots, pivots[1:]):
            distance = cur.position - prev.position
            if not range_lower <= distance - 1 <= range_upper:
                continue
            col = "Low" if pivot_kind == "low" else "High"
            p0 = float(ohlc[col].iloc[prev.position])
            p1 = float(ohlc[col].iloc[cur.position])
            if pivot_kind == "low":
                ok = cur.value > prev.value and p1 < p0
            else:
                ok = cur.value < prev.value and p1 > p0
            if ok and cur.position + right < len(ohlc):
                out.append((signal_kind, prev.position, cur.position))
    return sorted(out)


class HiddenDivergenceTests(unittest.TestCase):
    def test_bullish_hidden_price_higher_low_rsi_lower_low(self):
        df = frame()
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[10] = 45.0
        rsi.iloc[20] = 40.0  # RSI Lower Low
        df.iloc[10, df.columns.get_loc("Low")] = 90.0
        df.iloc[20, df.columns.get_loc("Low")] = 95.0  # price Higher Low
        signals = find_tradingview_divergences("T", "1D", df, rsi, kinds=ALL)
        self.assertEqual([s.kind for s in signals], ["bullish_hidden"])
        self.assertTrue(signals[0].hidden and signals[0].bullish)

    def test_bearish_hidden_price_lower_high_rsi_higher_high(self):
        df = frame()
        rsi = pd.Series(40.0, index=df.index)
        rsi.iloc[10] = 65.0
        rsi.iloc[20] = 72.0  # RSI Higher High
        df.iloc[10, df.columns.get_loc("High")] = 120.0
        df.iloc[20, df.columns.get_loc("High")] = 110.0  # price Lower High
        signals = find_tradingview_divergences("T", "1D", df, rsi, kinds=ALL)
        self.assertEqual([s.kind for s in signals], ["bearish_hidden"])

    def test_hidden_is_off_unless_requested(self):
        df = frame()
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[10] = 45.0
        rsi.iloc[20] = 40.0
        df.iloc[10, df.columns.get_loc("Low")] = 90.0
        df.iloc[20, df.columns.get_loc("Low")] = 95.0
        self.assertEqual(find_tradingview_regular_divergences("T", "1D", df, rsi), [])

    def test_unknown_kind_rejected(self):
        df = frame()
        with self.assertRaises(ValueError):
            find_tradingview_divergences("T", "1D", df, pd.Series(50.0, index=df.index), kinds=["x"])


class EarlyDivergenceTests(unittest.TestCase):
    def _bull_setup(self, n=40):
        df = frame(n)
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[10] = 45.0
        rsi.iloc[20] = 50.0
        df.iloc[10, df.columns.get_loc("Low")] = 100.0
        df.iloc[20, df.columns.get_loc("Low")] = 90.0
        return df, rsi

    def test_confirm_bars_zero_fires_on_the_pivot_bar(self):
        df, rsi = self._bull_setup()
        signals = find_tradingview_divergences("T", "1D", df, rsi, confirm_bars=0)
        bull = [s for s in signals if s.kind == "bullish_regular" and s.second_pivot.position == 20]
        self.assertEqual(len(bull), 1)
        self.assertEqual(bull[0].confirmation_position, 20)  # vs 25 with the Pine default
        self.assertFalse(bull[0].confirmed)
        self.assertIn("FORMING", bull[0].title)

    def test_early_signal_visible_before_right_side_exists(self):
        # Only 2 bars after the second pivot: the Pine default cannot see it yet.
        df, rsi = self._bull_setup(n=23)
        self.assertEqual(find_tradingview_regular_divergences("T", "1D", df, rsi), [])
        early = find_tradingview_divergences("T", "1D", df, rsi, confirm_bars=2)
        self.assertEqual([(s.kind, s.confirmation_position) for s in early], [("bullish_regular", 22)])

    def test_first_pivot_must_already_be_confirmed(self):
        # First RSI low at 10 is confirmed at 15; a candidate at 14 cannot use it.
        df = frame()
        rsi = pd.Series(60.0, index=df.index)
        rsi.iloc[10] = 45.0
        rsi.iloc[14] = 50.0
        for i in range(15, 40):
            rsi.iloc[i] = 60.0
        df.iloc[10, df.columns.get_loc("Low")] = 100.0
        df.iloc[14, df.columns.get_loc("Low")] = 90.0
        signals = find_tradingview_divergences(
            "T", "1D", df, rsi, confirm_bars=0, range_lower=0, left=3
        )
        self.assertFalse(any(s.second_pivot.position == 14 for s in signals))

    def test_early_id_is_stable_while_leg_extends(self):
        df, rsi = self._bull_setup()
        rsi.iloc[21] = 49.0  # leg extends one bar: new candidate at 21
        df.iloc[21, df.columns.get_loc("Low")] = 89.0
        signals = [
            s for s in find_tradingview_divergences("T", "1D", df, rsi, confirm_bars=0)
            if s.kind == "bullish_regular"
        ]
        self.assertGreaterEqual(len(signals), 2)
        self.assertEqual(len({s.signal_id for s in signals}), 1)

    def test_dca_mode_alerts_every_extension(self):
        df, rsi = self._bull_setup()
        rsi.iloc[21] = 49.0
        df.iloc[21, df.columns.get_loc("Low")] = 89.0
        signals = [
            s for s in find_tradingview_divergences("T", "1D", df, rsi, confirm_bars=0, alert_each_bar=True)
            if s.kind == "bullish_regular"
        ]
        self.assertGreaterEqual(len(signals), 2)
        self.assertEqual(len({s.signal_id for s in signals}), len(signals))

    def test_also_confirmed_sends_early_then_confirmed(self):
        df, rsi = self._bull_setup()
        signals = [
            s for s in find_tradingview_divergences(
                "T", "1D", df, rsi, confirm_bars=0, alert_each_bar=True, also_confirmed=True
            )
            if s.kind == "bullish_regular" and s.second_pivot.position == 20
        ]
        early = [s for s in signals if not s.confirmed]
        confirmed = [s for s in signals if s.confirmed]
        self.assertEqual([s.confirmation_position for s in early], [20])
        self.assertEqual([s.confirmation_position for s in confirmed], [25])
        self.assertNotEqual(early[0].signal_id, confirmed[0].signal_id)
        self.assertIn("FORMING", early[0].title)
        self.assertIn("CONFIRMED", confirmed[0].title)

    def test_confirm_bars_above_right_rejected(self):
        df, rsi = self._bull_setup()
        with self.assertRaises(ValueError):
            find_tradingview_divergences("T", "1D", df, rsi, confirm_bars=6)


class EquivalenceTests(unittest.TestCase):
    def test_default_matches_original_detector_on_random_walks(self):
        from trading_scanner.indicators import wilder_rsi

        rng = np.random.default_rng(7)
        for seed in range(25):
            n = 400
            close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
            spread = np.abs(rng.normal(0, 0.01, n)) * close
            df = pd.DataFrame(
                {"Open": close, "High": close + spread, "Low": close - spread, "Close": close},
                index=pd.bdate_range("2020-01-01", periods=n),
            )
            rsi = wilder_rsi(df["Close"], 14)
            new = sorted(
                (s.kind, s.first_pivot.position, s.second_pivot.position)
                for s in find_tradingview_regular_divergences("T", "1D", df, rsi)
            )
            self.assertEqual(new, _original_regular(df, rsi, 5, 5, 5, 60), f"seed {seed}")
            # and every signal keeps the exact old signal_id format
            for s in find_tradingview_regular_divergences("T", "1D", df, rsi):
                self.assertTrue(s.confirmed)
                self.assertEqual(s.rsi_alignment_mode, "tradingview_rsi_pivot")
                self.assertFalse(s.signal_id.endswith("early"))


if __name__ == "__main__":
    unittest.main()
