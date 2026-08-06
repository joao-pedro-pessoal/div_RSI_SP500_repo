import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from trading_scanner.state import SignalState, write_heartbeat
from trading_scanner.timeframes import to_three_trading_days, to_weekly
from trading_scanner.validation import blocking_issues, validate_ohlc


def clean_daily(index):
    n = len(index)
    close = [100.0 + i * 0.1 for i in range(n)]
    return pd.DataFrame(
        {
            "Open": close,
            "High": [x + 1 for x in close],
            "Low": [x - 1 for x in close],
            "Close": close,
            "Volume": [1000] * n,
        },
        index=index,
    )


class TimeframeTests(unittest.TestCase):
    def test_three_day_uses_fixed_reference_groups(self):
        calendar = pd.bdate_range("2026-01-05", periods=8)
        df = clean_daily(calendar)
        bars = to_three_trading_days(df, calendar, anchor="2026-01-05")
        self.assertEqual(len(bars), 2)  # final two reference sessions are incomplete
        self.assertEqual(bars.index[0], calendar[2])
        self.assertEqual(bars.iloc[0]["Open"], df.iloc[0]["Open"])
        self.assertEqual(bars.iloc[0]["Close"], df.iloc[2]["Close"])

    def test_three_day_rejects_group_with_missing_symbol_session(self):
        calendar = pd.bdate_range("2026-01-05", periods=6)
        df = clean_daily(calendar).drop(calendar[1])
        bars = to_three_trading_days(df, calendar, anchor="2026-01-05")
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars.index[0], calendar[5])

    def test_weekly_drops_partial_week(self):
        # Monday through Thursday: W-FRI bucket must not be treated as closed.
        index = pd.date_range("2026-01-05", periods=4, freq="D")
        bars = to_weekly(clean_daily(index))
        self.assertEqual(len(bars), 0)

    def test_weekly_keeps_friday_close(self):
        index = pd.date_range("2026-01-05", periods=5, freq="D")
        bars = to_weekly(clean_daily(index))
        self.assertEqual(len(bars), 1)


class ValidationTests(unittest.TestCase):
    def test_clean_data_passes(self):
        df = clean_daily(pd.bdate_range("2026-01-01", periods=100))
        self.assertEqual(blocking_issues(validate_ohlc(df)), [])

    def test_split_like_unadjusted_jump_is_reported_not_blocked(self):
        # Mudanca deliberada: NAO bloqueia. Uma queda real de -49% da racio 1.96
        # e um split 2:1 da 2.00 -- indistinguiveis pelo preco. Bloquear remove o
        # ticker do scan em silencio, e um sinal perdido de que nunca ficas a
        # saber e pior que um alerta falso. Reporta-se; decide o humano.
        index = pd.bdate_range("2025-01-01", periods=140)
        df = clean_daily(index)
        for column in ["Open", "High", "Low", "Close"]:
            df.loc[df.index[60]:, column] *= 0.5
        issues = validate_ohlc(df)
        self.assertTrue(any(x.code == "split_like_jump" for x in issues))
        self.assertFalse(any(x.code == "split_like_jump" and x.blocking for x in issues))
        # ... mas continua a poder bloquear se pedires explicitamente
        strict = validate_ohlc(df, block_suspicious_splits=True)
        self.assertTrue(any(x.code == "split_like_jump" and x.blocking for x in strict))

    def test_invalid_ohlc_is_blocked(self):
        df = clean_daily(pd.bdate_range("2026-01-01", periods=100))
        df.iloc[10, df.columns.get_loc("High")] = 1.0
        issues = validate_ohlc(df)
        self.assertTrue(any(x.code == "invalid_candle" and x.blocking for x in issues))

    def test_90_synthetic_split_discontinuities_are_flagged(self):
        ratios = [0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0]
        index = pd.bdate_range("2025-01-01", periods=140)
        for case in range(90):
            df = clean_daily(index)
            cut = 20 + (case % 90)
            ratio = ratios[case % len(ratios)]
            for column in ["Open", "High", "Low", "Close"]:
                df.loc[df.index[cut]:, column] *= ratio
            issues = validate_ohlc(df)
            self.assertTrue(any(x.code == "split_like_jump" for x in issues), msg=f"case {case}")

    def test_105_clean_series_do_not_trigger_split_guard(self):
        index = pd.bdate_range("2025-01-01", periods=100)
        for case in range(105):
            df = clean_daily(index)
            scale = 0.5 + (case / 100.0)
            for column in ["Open", "High", "Low", "Close"]:
                df[column] *= scale
            issues = validate_ohlc(df)
            self.assertFalse(any(x.code == "split_like_jump" for x in issues), msg=f"case {case}")


class StateTests(unittest.TestCase):
    def test_signal_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "signals.json"
            state = SignalState(path)
            state.mark_sent("abc")
            state.save()
            loaded = SignalState(path)
            self.assertTrue(loaded.contains("abc"))

    def test_heartbeat_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "heartbeat.json"
            write_heartbeat(path, status="ok", details={"signals": 2})
            payload = json.loads(path.read_text())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["details"]["signals"], 2)


if __name__ == "__main__":
    unittest.main()
