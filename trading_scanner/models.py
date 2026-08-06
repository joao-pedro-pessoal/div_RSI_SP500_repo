from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


PivotKind = Literal["low", "high"]
SignalKind = Literal["bullish_regular", "bearish_regular"]


@dataclass(frozen=True)
class Pivot:
    position: int
    timestamp: pd.Timestamp
    value: float
    kind: PivotKind


@dataclass(frozen=True)
class DivergenceSignal:
    ticker: str
    timeframe: str
    kind: SignalKind
    first_pivot: Pivot
    second_pivot: Pivot
    first_rsi: float
    second_rsi: float
    first_rsi_time: pd.Timestamp
    second_rsi_time: pd.Timestamp
    rsi_alignment_mode: str
    distance_bars: int
    confirmation_position: int
    confirmation_time: pd.Timestamp

    @property
    def signal_id(self) -> str:
        return "|".join(
            [
                self.ticker,
                self.timeframe,
                self.kind,
                self.rsi_alignment_mode,
                self.first_pivot.timestamp.isoformat(),
                self.second_pivot.timestamp.isoformat(),
            ]
        )
