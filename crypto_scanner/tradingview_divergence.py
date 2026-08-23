from __future__ import annotations

import pandas as pd

from .models import DivergenceSignal, Pivot
from .pivots import find_pivots


def find_tradingview_regular_divergences(
    ticker: str,
    timeframe: str,
    ohlc: pd.DataFrame,
    rsi: pd.Series,
    *,
    left: int = 5,
    right: int = 5,
    range_lower: int = 5,
    range_upper: int = 60,
    min_price_change_pct: float = 0.0,
    min_rsi_delta: float = 0.0,
) -> list[DivergenceSignal]:
    """Replicate the regular-divergence logic from TradingView's Pine script.

    Crucially, pivots are detected on RSI, not price. Price is sampled on the
    bars of two consecutive RSI pivots. Pine's `_inRange(plFound[1])` measures
    `barssince` on a one-bar-shifted pivot-found series, hence the exact range
    check is `(distance_between_pivots - 1)`.

    Hidden divergences are deliberately excluded because the supplied Pine
    indicator has both hidden plots disabled by default.
    """
    if range_lower < 0 or range_upper < range_lower:
        raise ValueError("invalid lookback range")
    signals: list[DivergenceSignal] = []

    for pivot_kind, signal_kind in (("low", "bullish_regular"), ("high", "bearish_regular")):
        rsi_pivots = find_pivots(rsi, pivot_kind, left=left, right=right)
        for previous_rsi_pivot, current_rsi_pivot in zip(rsi_pivots, rsi_pivots[1:]):
            distance = current_rsi_pivot.position - previous_rsi_pivot.position
            pine_bars_since = distance - 1
            if not range_lower <= pine_bars_since <= range_upper:
                continue

            previous_pos = previous_rsi_pivot.position
            current_pos = current_rsi_pivot.position
            if pivot_kind == "low":
                previous_price = float(ohlc["Low"].iloc[previous_pos])
                current_price = float(ohlc["Low"].iloc[current_pos])
                osc_ok = current_rsi_pivot.value > previous_rsi_pivot.value + min_rsi_delta
                price_ok = current_price < previous_price * (1.0 - min_price_change_pct)
            else:
                previous_price = float(ohlc["High"].iloc[previous_pos])
                current_price = float(ohlc["High"].iloc[current_pos])
                osc_ok = current_rsi_pivot.value < previous_rsi_pivot.value - min_rsi_delta
                price_ok = current_price > previous_price * (1.0 + min_price_change_pct)
            if not (osc_ok and price_ok):
                continue

            confirmation_position = current_pos + right
            if confirmation_position >= len(ohlc):
                continue

            previous_price_point = Pivot(
                position=previous_pos,
                timestamp=ohlc.index[previous_pos],
                value=previous_price,
                kind=pivot_kind,  # type: ignore[arg-type]
            )
            current_price_point = Pivot(
                position=current_pos,
                timestamp=ohlc.index[current_pos],
                value=current_price,
                kind=pivot_kind,  # type: ignore[arg-type]
            )
            signals.append(
                DivergenceSignal(
                    ticker=ticker,
                    timeframe=timeframe,
                    kind=signal_kind,  # type: ignore[arg-type]
                    first_pivot=previous_price_point,
                    second_pivot=current_price_point,
                    first_rsi=previous_rsi_pivot.value,
                    second_rsi=current_rsi_pivot.value,
                    first_rsi_time=previous_rsi_pivot.timestamp,
                    second_rsi_time=current_rsi_pivot.timestamp,
                    rsi_alignment_mode="tradingview_rsi_pivot",
                    distance_bars=distance,
                    confirmation_position=confirmation_position,
                    confirmation_time=ohlc.index[confirmation_position],
                )
            )

    return sorted(signals, key=lambda s: (s.confirmation_time, s.ticker, s.kind))

