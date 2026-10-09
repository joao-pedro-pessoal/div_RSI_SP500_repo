from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


PivotKind = Literal["low", "high"]
SignalKind = Literal["bullish_regular", "bearish_regular", "bullish_hidden", "bearish_hidden"]

# Regular: price HH + RSI LH (bearish) / price LL + RSI HL (bullish).
# Hidden:  price LH + RSI HH (bearish) / price HL + RSI LL (bullish).
ALL_SIGNAL_KINDS: tuple[str, ...] = (
    "bullish_regular", "bearish_regular", "bullish_hidden", "bearish_hidden",
)


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
    # False = second pivot has fewer than pivot_right bars to its right
    # (confirm_bars < pivot_right): the divergence is shown early, while it
    # is still forming, and may still be invalidated by the next candles.
    confirmed: bool = True
    # Early signals only. True: one alert per leg. False (DCA mode): a new
    # alert on every candle that extends the divergence to a new low/high.
    per_leg: bool = True

    @property
    def bullish(self) -> bool:
        return self.kind.startswith("bullish")

    @property
    def hidden(self) -> bool:
        return self.kind.endswith("hidden")

    @property
    def title(self) -> str:
        side = "BULLISH" if self.bullish else "BEARISH"
        kind = "HIDDEN RSI DIVERGENCE" if self.hidden else "RSI DIVERGENCE"
        if self.confirmed:
            return f"CONFIRMED ✅ {side} {kind}"
        return f"FORMING ⏳ {side} {kind}"

    @property
    def signal_id(self) -> str:
        if not self.confirmed and self.per_leg:
            # While a leg keeps extending, each new candle produces a new
            # candidate second pivot. Keying on the first pivot alone sends
            # one early alert per leg instead of one per candle.
            return "|".join(
                [
                    self.ticker,
                    self.timeframe,
                    self.kind,
                    self.rsi_alignment_mode,
                    self.first_pivot.timestamp.isoformat(),
                    "early",
                ]
            )
        return "|".join(
            [
                self.ticker,
                self.timeframe,
                self.kind,
                self.rsi_alignment_mode,
                self.first_pivot.timestamp.isoformat(),
                self.second_pivot.timestamp.isoformat(),
            ]
            + ([] if self.confirmed else ["early"])
        )
