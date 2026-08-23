from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI using Wilder's original smoothing with an SMA seed."""
    if period < 2:
        raise ValueError("period must be >= 2")
    close = pd.to_numeric(close, errors="coerce").astype(float)
    out = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= period:
        return out

    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta.clip(upper=0.0))

    seed_gains = gains.iloc[1 : period + 1]
    seed_losses = losses.iloc[1 : period + 1]
    if seed_gains.isna().any() or seed_losses.isna().any():
        return out

    avg_gain = float(seed_gains.mean())
    avg_loss = float(seed_losses.mean())

    def _value(gain: float, loss: float) -> float:
        if loss == 0.0 and gain == 0.0:
            return 50.0
        if loss == 0.0:
            return 100.0
        if gain == 0.0:
            return 0.0
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    out.iloc[period] = _value(avg_gain, avg_loss)
    for i in range(period + 1, len(close)):
        gain = gains.iloc[i]
        loss = losses.iloc[i]
        if pd.isna(gain) or pd.isna(loss):
            avg_gain = np.nan
            avg_loss = np.nan
            continue
        if np.isnan(avg_gain) or np.isnan(avg_loss):
            return out
        avg_gain = ((avg_gain * (period - 1)) + float(gain)) / period
        avg_loss = ((avg_loss * (period - 1)) + float(loss)) / period
        out.iloc[i] = _value(avg_gain, avg_loss)
    return out

