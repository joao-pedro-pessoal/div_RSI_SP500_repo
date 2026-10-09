from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from .models import ALL_SIGNAL_KINDS, DivergenceSignal, Pivot
from .pivots import find_pivots

REGULAR_KINDS: tuple[str, ...] = ("bullish_regular", "bearish_regular")

# signal kind -> (RSI pivot kind, price must go "up"/"down", RSI must go "up"/"down")
#
#   bullish_regular: price Lower Low   + RSI Higher Low
#   bearish_regular: price Higher High + RSI Lower High
#   bullish_hidden:  price Higher Low  + RSI Lower Low
#   bearish_hidden:  price Lower High  + RSI Higher High
_RULES: dict[str, tuple[str, str, str]] = {
    "bullish_regular": ("low", "down", "up"),
    "bearish_regular": ("high", "up", "down"),
    "bullish_hidden": ("low", "up", "down"),
    "bearish_hidden": ("high", "down", "up"),
}


def _candidate_pivots(series: pd.Series, kind: str, left: int, right: int) -> list[Pivot]:
    """Like `find_pivots`, but `right` may be 0.

    With right=0 the candidate is simply a bar that is the strict extreme of
    itself plus the `left` bars before it: the moment RSI turns into a new
    local low/high, before any bar to the right exists.
    """
    if right >= 1:
        return find_pivots(series, kind, left=left, right=right)
    values = pd.to_numeric(series, errors="coerce")
    pivots: list[Pivot] = []
    for i in range(left, len(values)):
        center = values.iloc[i]
        window = values.iloc[i - left : i + 1]
        if pd.isna(center) or window.isna().any():
            continue
        extreme = window.min() if kind == "low" else window.max()
        if center == extreme and int((window == center).sum()) == 1:
            pivots.append(Pivot(i, values.index[i], float(center), kind))  # type: ignore[arg-type]
    return pivots


def _moved(direction: str, previous: float, current: float, tolerance: float, relative: bool) -> bool:
    if relative:
        if direction == "up":
            return current > previous * (1.0 + tolerance)
        return current < previous * (1.0 - tolerance)
    if direction == "up":
        return current > previous + tolerance
    return current < previous - tolerance


def find_tradingview_divergences(
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
    kinds: Iterable[str] = REGULAR_KINDS,
    confirm_bars: int | None = None,
    alert_each_bar: bool = False,
    also_confirmed: bool = False,
) -> list[DivergenceSignal]:
    """Replicate the divergence logic from TradingView's Pine script.

    Crucially, pivots are detected on RSI, not price. Price is sampled on the
    bars of two consecutive RSI pivots. Pine's `_inRange(plFound[1])` measures
    `barssince` on a one-bar-shifted pivot-found series, hence the exact range
    check is `(distance_between_pivots - 1)`.

    `kinds` selects regular and/or hidden divergences (see `_RULES`).

    `confirm_bars` is how many bars the SECOND pivot needs to its right before
    the signal is emitted. None means `right`, which is exactly the Pine
    behaviour. A smaller value (0 = on the close of the pivot bar itself)
    shows the divergence early, while it is still forming; those signals are
    marked `confirmed=False` because later candles may still extend the leg.
    The FIRST pivot is always a fully confirmed pivot (`right` bars), and it
    must already be confirmed at the moment the early signal is emitted, so
    nothing here uses information from the future.

    `alert_each_bar` (early mode only) gives every candle that extends the
    divergence its own signal_id, so each one alerts (DCA). Otherwise one
    alert per leg.

    `also_confirmed` (early mode only) additionally returns the normal,
    fully confirmed signals, so the same divergence alerts twice: once
    as soon as it forms and again when the pivot is confirmed.
    """
    if range_lower < 0 or range_upper < range_lower:
        raise ValueError("invalid lookback range")
    confirm = right if confirm_bars is None else int(confirm_bars)
    if not 0 <= confirm <= right:
        raise ValueError("confirm_bars must be between 0 and pivot_right")
    kinds = tuple(kinds)
    unknown = set(kinds) - set(ALL_SIGNAL_KINDS)
    if unknown:
        raise ValueError(f"unknown divergence kinds: {sorted(unknown)}")

    confirmed = confirm == right
    alignment = "tradingview_rsi_pivot" if confirmed else "tradingview_rsi_early"
    signals: list[DivergenceSignal] = []

    for pivot_kind in ("low", "high"):
        wanted = [k for k in kinds if _RULES[k][0] == pivot_kind]
        if not wanted:
            continue
        full_pivots = find_pivots(rsi, pivot_kind, left=left, right=right)
        candidates = full_pivots if confirmed else _candidate_pivots(rsi, pivot_kind, left, confirm)
        price_column = "Low" if pivot_kind == "low" else "High"

        previous_index = -1
        for current_rsi_pivot in candidates:
            current_pos = current_rsi_pivot.position
            confirmation_position = current_pos + confirm
            if confirmation_position >= len(ohlc):
                continue

            # Previous = latest fully confirmed pivot that was already known
            # when this candidate is emitted. With confirm == right this is
            # simply the previous pivot, like ta.valuewhen(..., 1) in Pine.
            while (
                previous_index + 1 < len(full_pivots)
                and full_pivots[previous_index + 1].position < current_pos
                and full_pivots[previous_index + 1].position + right <= confirmation_position
            ):
                previous_index += 1
            if previous_index < 0:
                continue
            previous_rsi_pivot = full_pivots[previous_index]
            previous_pos = previous_rsi_pivot.position

            distance = current_pos - previous_pos
            pine_bars_since = distance - 1
            if not range_lower <= pine_bars_since <= range_upper:
                continue

            previous_price = float(ohlc[price_column].iloc[previous_pos])
            current_price = float(ohlc[price_column].iloc[current_pos])

            for signal_kind in wanted:
                _, price_dir, rsi_dir = _RULES[signal_kind]
                price_ok = _moved(price_dir, previous_price, current_price, min_price_change_pct, True)
                osc_ok = _moved(rsi_dir, previous_rsi_pivot.value, current_rsi_pivot.value, min_rsi_delta, False)
                if not (price_ok and osc_ok):
                    continue
                signals.append(
                    DivergenceSignal(
                        ticker=ticker,
                        timeframe=timeframe,
                        kind=signal_kind,  # type: ignore[arg-type]
                        first_pivot=Pivot(previous_pos, ohlc.index[previous_pos], previous_price, pivot_kind),  # type: ignore[arg-type]
                        second_pivot=Pivot(current_pos, ohlc.index[current_pos], current_price, pivot_kind),  # type: ignore[arg-type]
                        first_rsi=previous_rsi_pivot.value,
                        second_rsi=current_rsi_pivot.value,
                        first_rsi_time=previous_rsi_pivot.timestamp,
                        second_rsi_time=current_rsi_pivot.timestamp,
                        rsi_alignment_mode=alignment,
                        distance_bars=distance,
                        confirmation_position=confirmation_position,
                        confirmation_time=ohlc.index[confirmation_position],
                        confirmed=confirmed,
                        per_leg=not alert_each_bar,
                    )
                )

    if not confirmed and also_confirmed:
        signals.extend(
            find_tradingview_divergences(
                ticker, timeframe, ohlc, rsi,
                left=left, right=right,
                range_lower=range_lower, range_upper=range_upper,
                min_price_change_pct=min_price_change_pct,
                min_rsi_delta=min_rsi_delta,
                kinds=kinds,
            )
        )

    return sorted(signals, key=lambda s: (s.confirmation_time, s.ticker, s.kind))


def find_tradingview_regular_divergences(
    ticker: str,
    timeframe: str,
    ohlc: pd.DataFrame,
    rsi: pd.Series,
    **kwargs,
) -> list[DivergenceSignal]:
    """Backwards-compatible entry point: regular divergences only."""
    kwargs.setdefault("kinds", REGULAR_KINDS)
    return find_tradingview_divergences(ticker, timeframe, ohlc, rsi, **kwargs)
