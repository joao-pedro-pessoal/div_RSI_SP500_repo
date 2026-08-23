from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import AppConfig
from .divergence import find_regular_divergences
from .indicators import wilder_rsi
from .models import DivergenceSignal
from .timeframes import build as build_timeframe
from .tradingview_divergence import find_tradingview_regular_divergences
from .validation import DataIssue, blocking_issues, validate_ohlc

# The reused detector modules expect capitalised OHLC columns; the crypto
# provider produces lowercase. Renaming happens here, at the boundary, so
# neither side has to know about the other's convention.
COLUMN_MAP = {"open": "Open", "high": "High", "low": "Low",
              "close": "Close", "volume": "Volume"}


@dataclass
class ScanReport:
    signals: list[DivergenceSignal] = field(default_factory=list)
    skipped: dict[str, list[DataIssue]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class CryptoDivergenceScanner:
    def __init__(self, config: AppConfig):
        self.config = config

    def scan_symbol(
        self,
        symbol: str,
        bars_4h: pd.DataFrame,
        timeframes: list[str] | None = None,
    ) -> ScanReport:
        report = ScanReport()
        v = self.config.validation

        issues = validate_ohlc(
            bars_4h,
            min_rows=v.min_rows,
            max_gap_bars=v.max_gap_bars,
            max_single_bar_move=v.max_single_bar_move,
        )
        if blocking_issues(issues):
            report.skipped[symbol] = issues
            return report

        s = self.config.scanner
        for timeframe in (timeframes or s.timeframes):
            try:
                bars = build_timeframe(bars_4h, timeframe)
                if len(bars) <= s.rsi_period + s.pivot_left + s.pivot_right:
                    continue

                frame = bars.rename(columns=COLUMN_MAP)
                rsi = wilder_rsi(frame["Close"], s.rsi_period)

                if s.detector_mode == "tradingview":
                    signals = find_tradingview_regular_divergences(
                        symbol, timeframe, frame, rsi,
                        left=s.pivot_left, right=s.pivot_right,
                        range_lower=s.min_distance_bars,
                        range_upper=s.max_distance_bars,
                        min_price_change_pct=s.min_price_change_pct,
                        min_rsi_delta=s.min_rsi_delta,
                    )
                else:
                    signals = find_regular_divergences(
                        symbol, timeframe, frame, rsi,
                        left=s.pivot_left, right=s.pivot_right,
                        min_distance=s.min_distance_bars,
                        max_distance=s.max_distance_bars,
                        min_price_change_pct=s.min_price_change_pct,
                        min_rsi_delta=s.min_rsi_delta,
                        comparison_mode=s.comparison_mode,
                        rsi_alignment_mode=s.rsi_alignment_mode,
                        rsi_pivot_window=s.rsi_pivot_window,
                    )

                # Per-timeframe window: a 4h bar closes six times a day, so a
                # once-daily scan needs a wider window there than on 1W.
                window = s.age_window(timeframe)
                report.signals.extend(
                    signal for signal in signals
                    if (len(bars) - 1 - signal.confirmation_position) <= window
                )
            except Exception as exc:
                report.errors[f"{symbol}:{timeframe}"] = f"{type(exc).__name__}: {exc}"
        return report
