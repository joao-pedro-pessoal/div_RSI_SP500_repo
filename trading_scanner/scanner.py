from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import AppConfig
from .divergence import find_regular_divergences
from .indicators import wilder_rsi
from .models import DivergenceSignal
from .timeframes import convert_timeframe
from .tradingview_divergence import find_tradingview_regular_divergences
from .validation import DataIssue, blocking_issues, validate_ohlc


@dataclass
class ScanReport:
    signals: list[DivergenceSignal] = field(default_factory=list)
    skipped: dict[str, list[DataIssue]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class RSIDivergenceScanner:
    def __init__(self, config: AppConfig):
        self.config = config

    def scan_ticker(
        self,
        ticker: str,
        daily: pd.DataFrame,
        *,
        reference_calendar: pd.DatetimeIndex,
        timeframes: list[str] | None = None,
    ) -> ScanReport:
        report = ScanReport()
        v = self.config.validation
        issues = validate_ohlc(
            daily,
            min_rows=v.min_rows,
            max_calendar_gap_days=v.max_calendar_gap_days,
            split_ratio_tolerance=v.split_ratio_tolerance,
            block_suspicious_splits=v.block_suspicious_splits,
        )
        if blocking_issues(issues):
            report.skipped[ticker] = issues
            return report

        s = self.config.scanner
        chosen_timeframes = timeframes or s.timeframes
        for timeframe in chosen_timeframes:
            try:
                bars = convert_timeframe(
                    daily,
                    timeframe,
                    reference_calendar=reference_calendar,
                    three_day_anchor=self.config.data.three_day_anchor,
                )
                if len(bars) <= s.rsi_period + s.pivot_left + s.pivot_right:
                    continue
                rsi = wilder_rsi(bars["Close"], s.rsi_period)
                if s.detector_mode == "tradingview":
                    signals = find_tradingview_regular_divergences(
                        ticker,
                        timeframe,
                        bars,
                        rsi,
                        left=s.pivot_left,
                        right=s.pivot_right,
                        range_lower=s.min_distance_bars,
                        range_upper=s.max_distance_bars,
                        min_price_change_pct=s.min_price_change_pct,
                        min_rsi_delta=s.min_rsi_delta,
                    )
                else:
                    signals = find_regular_divergences(
                        ticker,
                        timeframe,
                        bars,
                        rsi,
                        left=s.pivot_left,
                        right=s.pivot_right,
                        min_distance=s.min_distance_bars,
                        max_distance=s.max_distance_bars,
                        min_price_change_pct=s.min_price_change_pct,
                        min_rsi_delta=s.min_rsi_delta,
                        comparison_mode=s.comparison_mode,
                        rsi_alignment_mode=s.rsi_alignment_mode,
                        rsi_pivot_window=s.rsi_pivot_window,
                    )
                report.signals.extend(
                    sig
                    for sig in signals
                    if (len(bars) - 1 - sig.confirmation_position) <= s.alert_age_bars
                )
            except Exception as exc:
                report.errors[f"{ticker}:{timeframe}"] = str(exc)
        return report
