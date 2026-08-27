from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


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
    timeframes: list[str] = field(default_factory=lambda: ["4h", "1D", "3D", "1W"])

    # Per-timeframe alert window, in bars of that timeframe.
    #
    # The scan runs once a day, but a 4h bar closes six times a day. With a
    # window of 1 bar, five of every six 4h confirmations would be missed
    # entirely. The 4h window is therefore 7 rather than 6: the extra bar
    # gives overlap, so a late or skipped run leaves no gap. Deduplication
    # by signal id means the overlap costs nothing.
    alert_age_bars: dict[str, int] = field(
        default_factory=lambda: {"4h": 7, "1D": 2, "3D": 1, "1W": 1}
    )

    def age_window(self, timeframe: str) -> int:
        return int(self.alert_age_bars.get(timeframe, 1))


@dataclass
class UniverseConfig:
    limit: int = 100
    exclude_stablecoins: bool = True
    exclude_wrapped: bool = True
    snapshot_dir: str = "universe"


@dataclass
class DataConfig:
    # 4200 bars = ~700 days. Chosen by the WEEKLY timeframe, not the 4h one:
    # 1500 bars would give only 35 weekly candles, barely above the
    # RSI(14) + 5+5 pivot minimum, so 1W would produce almost no signals.
    bars_4h: int = 4200
    sleep_between: float = 0.15
    retries: int = 3


@dataclass
class ValidationConfig:
    min_rows: int = 120
    max_gap_bars: int = 12       # 4h bars; 12 = two days of missing data
    # No split checks here: perpetual contracts do not have splits or
    # dividends. Crypto's equivalent failure is a token redenomination,
    # which is rare and would show up as a gap plus a price discontinuity.
    max_single_bar_move: float = 0.60


@dataclass
class TelegramConfig:
    send_heartbeat: bool = True
    # Aviso de "a comecar". Util para saber que o workflow disparou, mas
    # duplica o numero de mensagens -- desliga onde a frequencia e alta.
    send_start_notice: bool = True


@dataclass
class AppConfig:
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    data: DataConfig = field(default_factory=DataConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    state_file: str = "state/signals.json"
    heartbeat_file: str = "state/heartbeat.json"


def _build(cls: type, values: dict[str, Any] | None):
    return cls(**(values or {}))


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    path = Path(path)
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig(
        scanner=_build(ScannerConfig, raw.get("scanner")),
        universe=_build(UniverseConfig, raw.get("universe")),
        data=_build(DataConfig, raw.get("data")),
        validation=_build(ValidationConfig, raw.get("validation")),
        telegram=_build(TelegramConfig, raw.get("telegram")),
        state_file=raw.get("state_file", "state/signals.json"),
        heartbeat_file=raw.get("heartbeat_file", "state/heartbeat.json"),
    )
