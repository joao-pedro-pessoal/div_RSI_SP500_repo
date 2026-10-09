from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import ALL_SIGNAL_KINDS


@dataclass
class ScannerConfig:
    rsi_period: int = 14
    pivot_left: int = 5
    pivot_right: int = 5
    min_distance_bars: int = 5
    max_distance_bars: int = 60
    min_price_change_pct: float = 0.0
    min_rsi_delta: float = 0.0
    detector_mode: str = "tradingview"
    comparison_mode: str = "consecutive"
    rsi_alignment_mode: str = "price_pivot"
    rsi_pivot_window: int = 2
    alert_age_bars: int = 1
    # Which divergences to detect (detector_mode: tradingview only):
    #   bullish_regular: price Lower Low   + RSI Higher Low
    #   bearish_regular: price Higher High + RSI Lower High
    #   bullish_hidden:  price Higher Low  + RSI Lower Low
    #   bearish_hidden:  price Lower High  + RSI Higher High
    divergence_types: list[str] = field(
        default_factory=lambda: ["bullish_regular", "bearish_regular"]
    )
    # Bars the SECOND pivot needs to its right before alerting. None = same as
    # pivot_right (exact Pine behaviour). 0 = alert on the close of the bar
    # that forms the new RSI low/high: earliest possible, but not final.
    confirm_bars: int | None = None
    # Only with confirm_bars < pivot_right. True = alert on EVERY candle that
    # extends the divergence (for DCA); False = only the first one per leg.
    alert_each_bar: bool = False
    # Only with confirm_bars < pivot_right: ALSO send a second message when
    # the divergence is confirmed (pivot_right bars later).
    send_confirmed: bool = True
    timeframes: list[str] = field(default_factory=lambda: ["1D", "3D", "1W"])


@dataclass
class DataConfig:
    start: str = "2020-01-01"
    three_day_anchor: str = "2000-01-03"
    batch_size: int = 75
    retries: int = 2
    auto_adjust: bool = True


@dataclass
class ValidationConfig:
    min_rows: int = 80
    max_calendar_gap_days: int = 14
    split_ratio_tolerance: float = 0.025
    block_suspicious_splits: bool = True


@dataclass
class TelegramConfig:
    send_heartbeat: bool = True


@dataclass
class AppConfig:
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    data: DataConfig = field(default_factory=DataConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    state_file: str = "state/signals.json"
    heartbeat_file: str = "state/heartbeat.json"


def _build(cls: type, values: dict[str, Any] | None):
    return cls(**(values or {}))


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = AppConfig(
        scanner=_build(ScannerConfig, raw.get("scanner")),
        data=_build(DataConfig, raw.get("data")),
        validation=_build(ValidationConfig, raw.get("validation")),
        telegram=_build(TelegramConfig, raw.get("telegram")),
        state_file=raw.get("state_file", "state/signals.json"),
        heartbeat_file=raw.get("heartbeat_file", "state/heartbeat.json"),
    )
    _validate_config(config)
    return config


def _validate_config(config: AppConfig) -> None:
    s = config.scanner
    if s.rsi_period < 2:
        raise ValueError("rsi_period must be >= 2")
    if s.pivot_left < 1 or s.pivot_right < 1:
        raise ValueError("pivot_left and pivot_right must be >= 1")
    if s.min_distance_bars < 1 or s.max_distance_bars < s.min_distance_bars:
        raise ValueError("invalid pivot distance range")
    if s.detector_mode not in {"tradingview", "price_pivots"}:
        raise ValueError("detector_mode must be 'tradingview' or 'price_pivots'")
    if s.comparison_mode not in {"consecutive", "all"}:
        raise ValueError("comparison_mode must be 'consecutive' or 'all'")
    if s.rsi_alignment_mode not in {"price_pivot", "rsi_pivot"}:
        raise ValueError("rsi_alignment_mode must be 'price_pivot' or 'rsi_pivot'")
    if s.rsi_pivot_window < 0:
        raise ValueError("rsi_pivot_window must be >= 0")
    bad_types = set(s.divergence_types) - set(ALL_SIGNAL_KINDS)
    if bad_types or not s.divergence_types:
        raise ValueError(f"divergence_types must be a non-empty subset of {list(ALL_SIGNAL_KINDS)}")
    if s.confirm_bars is not None and not 0 <= s.confirm_bars <= s.pivot_right:
        raise ValueError("confirm_bars must be between 0 and pivot_right")
    unknown = set(s.timeframes) - {"1D", "3D", "1W"}
    if unknown:
        raise ValueError(f"unsupported timeframes: {sorted(unknown)}")
