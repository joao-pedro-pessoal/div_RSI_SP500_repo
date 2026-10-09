"""Macro scanner (gold, silver, S&P 500, BTC): bar boundaries. Offline."""

import unittest

import pandas as pd

from crypto_scanner.macro import (
    Asset,
    closed_daily,
    closed_hourly,
    format_signal,
    source_note,
    to_session_4h,
)
from crypto_scanner.models import DivergenceSignal, Pivot

NY = "America/New_York"
DELAY = pd.Timedelta(minutes=15)


def hourly(start, periods, freq="1h"):
    idx = pd.date_range(start, periods=periods, freq=freq, tz=NY)
    n = len(idx)
    return pd.DataFrame(
        {"Open": range(n), "High": [x + 1 for x in range(n)], "Low": range(n), "Close": range(n)},
        index=idx, dtype=float,
    )


def spx_day(date):
    # Yahoo ^GSPC hourly bars: 09:30 ... 15:30 (the last one is 30 minutes)
    return hourly(f"{date} 09:30", 7)


class EquitySessionTests(unittest.TestCase):
    def test_spx_4h_bins_are_0930_and_1330(self):
        h1 = spx_day("2026-10-08")
        now = pd.Timestamp("2026-10-09 12:00", tz=NY)
        bars = to_session_4h(h1, "us_equity", now, DELAY)
        self.assertEqual(
            [t.strftime("%H:%M") for t in bars.index], ["09:30", "13:30"]
        )
        # 09:30 bin = bars 09:30,10:30,11:30,12:30 ; 13:30 bin = 13:30,14:30,15:30
        self.assertEqual(bars["Open"].tolist(), [0.0, 4.0])
        self.assertEqual(bars["Close"].tolist(), [3.0, 6.0])

    def test_spx_afternoon_bar_closes_at_1600_not_1730(self):
        h1 = spx_day("2026-10-08")
        before = to_session_4h(h1, "us_equity", pd.Timestamp("2026-10-08 16:10", tz=NY), DELAY)
        after = to_session_4h(h1, "us_equity", pd.Timestamp("2026-10-08 16:16", tz=NY), DELAY)
        self.assertEqual(len(before), 1)
        self.assertEqual(len(after), 2)

    def test_forming_hourly_bar_is_dropped(self):
        h1 = spx_day("2026-10-09")
        now = pd.Timestamp("2026-10-09 12:40", tz=NY)  # 11:30 bar ended 12:30 (+15 = 12:45)
        out = closed_hourly(h1, "us_equity", now, DELAY)
        self.assertEqual(out.index[-1].strftime("%H:%M"), "10:30")

    def test_last_spx_hour_is_30_minutes(self):
        h1 = spx_day("2026-10-09")
        out = closed_hourly(h1, "us_equity", pd.Timestamp("2026-10-09 16:16", tz=NY), DELAY)
        self.assertEqual(out.index[-1].strftime("%H:%M"), "15:30")

    def test_daily_bar_waits_for_close(self):
        daily = pd.DataFrame(
            {"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0], "Close": [1.0, 2.0]},
            index=pd.DatetimeIndex(["2026-10-08", "2026-10-09"]),
        )
        during = closed_daily(daily, "us_equity", pd.Timestamp("2026-10-09 15:00", tz=NY), DELAY)
        after = closed_daily(daily, "us_equity", pd.Timestamp("2026-10-09 16:20", tz=NY), DELAY)
        self.assertEqual(len(during), 1)
        self.assertEqual(len(after), 2)


class FuturesSessionTests(unittest.TestCase):
    def test_futures_4h_anchor_is_1800_et(self):
        # Sunday 18:00 -> Monday 17:00 (23 hourly bars), daily break 17-18
        h1 = hourly("2026-10-04 18:00", 23)
        now = pd.Timestamp("2026-10-06 00:00", tz=NY)
        bars = to_session_4h(h1, "futures", now, DELAY)
        self.assertEqual(
            [t.strftime("%a %H:%M") for t in bars.index],
            ["Sun 18:00", "Sun 22:00", "Mon 02:00", "Mon 06:00", "Mon 10:00", "Mon 14:00"],
        )
        # the last bin only has 14:00,15:00,16:00 (break at 17:00)
        self.assertEqual(bars["Close"].iloc[-1], 22.0)

    def test_futures_last_bin_closes_at_1700(self):
        h1 = hourly("2026-10-04 18:00", 23)
        bars = to_session_4h(h1, "futures", pd.Timestamp("2026-10-05 17:20", tz=NY), DELAY)
        self.assertEqual(bars.index[-1].strftime("%a %H:%M"), "Mon 14:00")


class MessageTests(unittest.TestCase):
    def test_message_is_english_and_links_chart(self):
        asset = Asset("GOLD (XAU)", "yahoo", "GC=F", "COMEX:GC1!")
        ts = pd.Timestamp("2026-10-09 15:00", tz="UTC")
        sig = DivergenceSignal(
            ticker=asset.name, timeframe="1h", kind="bullish_regular",
            first_pivot=Pivot(1, ts, 4100.0, "low"), second_pivot=Pivot(9, ts, 4090.0, "low"),
            first_rsi=30.0, second_rsi=35.0, first_rsi_time=ts, second_rsi_time=ts,
            rsi_alignment_mode="tradingview_rsi_early", distance_bars=8,
            confirmation_position=9, confirmation_time=ts, confirmed=False,
        )
        text = format_signal(sig, asset, source_note(asset))
        self.assertIn("FORMING", text)
        self.assertIn("Previous low", text)
        self.assertIn("COMEX%3AGC1%21", text)
        self.assertIn("Yahoo GC=F", text)


if __name__ == "__main__":
    unittest.main()
