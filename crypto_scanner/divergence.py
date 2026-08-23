from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from .models import DivergenceSignal, Pivot
from .pivots import find_pivots


def _candidate_pairs(pivots: list[Pivot], mode: str) -> Iterable[tuple[Pivot, Pivot]]:
    if mode == "consecutive":
        yield from zip(pivots, pivots[1:])
        return
    if mode == "all":
        for current_i in range(1, len(pivots)):
            for previous_i in range(current_i):
                yield pivots[previous_i], pivots[current_i]
        return
    raise ValueError("comparison_mode must be 'consecutive' or 'all'")


def _align_rsi(
    rsi: pd.Series,
    price_pivot: Pivot,
    *,
    kind: str,
    alignment_mode: str,
    rsi_pivots: list[Pivot],
    rsi_pivot_window: int,
) -> tuple[float, int, pd.Timestamp] | None:
    if alignment_mode == "price_pivot":
        value = rsi.iloc[price_pivot.position]
        if pd.isna(value):
            return None
        return float(value), price_pivot.position, rsi.index[price_pivot.position]
    if alignment_mode != "rsi_pivot":
        raise ValueError("unknown RSI alignment mode")

    candidates = [
        pivot
        for pivot in rsi_pivots
        if abs(pivot.position - price_pivot.position) <= rsi_pivot_window
    ]
    if not candidates:
        return None
    if kind == "low":
        chosen = min(candidates, key=lambda p: (abs(p.position - price_pivot.position), p.value))
    else:
        chosen = min(candidates, key=lambda p: (abs(p.position - price_pivot.position), -p.value))
    return chosen.value, chosen.position, chosen.timestamp


def find_regular_divergences(
    ticker: str,
    timeframe: str,
    ohlc: pd.DataFrame,
    rsi: pd.Series,
    *,
    left: int,
    right: int,
    min_distance: int,
    max_distance: int,
    min_price_change_pct: float = 0.0,
    min_rsi_delta: float = 0.0,
    comparison_mode: str = "consecutive",
    rsi_alignment_mode: str = "price_pivot",
    rsi_pivot_window: int = 2,
) -> list[DivergenceSignal]:
    """Detect regular divergence with explicit/configurable RSI alignment."""
    signals: list[DivergenceSignal] = []
    for kind, signal_kind in (("low", "bullish_regular"), ("high", "bearish_regular")):
        price_series = ohlc["Low"] if kind == "low" else ohlc["High"]
        pivots = find_pivots(price_series, kind, left=left, right=right)
        rsi_pivots = (
            find_pivots(rsi, kind, left=left, right=right)
            if rsi_alignment_mode == "rsi_pivot"
            else []
        )
        for first, second in _candidate_pairs(pivots, comparison_mode):
            distance = second.position - first.position
            if not min_distance <= distance <= max_distance:
                continue
            first_aligned = _align_rsi(
                rsi,
                first,
                kind=kind,
                alignment_mode=rsi_alignment_mode,
                rsi_pivots=rsi_pivots,
                rsi_pivot_window=rsi_pivot_window,
            )
            second_aligned = _align_rsi(
                rsi,
                second,
                kind=kind,
                alignment_mode=rsi_alignment_mode,
                rsi_pivots=rsi_pivots,
                rsi_pivot_window=rsi_pivot_window,
            )
            if first_aligned is None or second_aligned is None:
                continue
            first_rsi, _first_rsi_pos, first_rsi_time = first_aligned
            second_rsi, second_rsi_pos, second_rsi_time = second_aligned

            if kind == "low":
                price_ok = second.value < first.value * (1.0 - min_price_change_pct)
                rsi_ok = second_rsi > first_rsi + min_rsi_delta
            else:
                price_ok = second.value > first.value * (1.0 + min_price_change_pct)
                rsi_ok = second_rsi < first_rsi - min_rsi_delta
            if not (price_ok and rsi_ok):
                continue

            confirmation_position = max(second.position + right, second_rsi_pos + right)
            if confirmation_position >= len(ohlc):
                continue
            signals.append(
                DivergenceSignal(
                    ticker=ticker,
                    timeframe=timeframe,
                    kind=signal_kind,  # type: ignore[arg-type]
                    first_pivot=first,
                    second_pivot=second,
                    first_rsi=first_rsi,
                    second_rsi=second_rsi,
                    first_rsi_time=first_rsi_time,
                    second_rsi_time=second_rsi_time,
                    rsi_alignment_mode=rsi_alignment_mode,
                    distance_bars=distance,
                    confirmation_position=confirmation_position,
                    confirmation_time=ohlc.index[confirmation_position],
                )
            )
    return sorted(signals, key=lambda s: (s.confirmation_time, s.ticker, s.kind))
