"""
timeframes.py — 4h / 1D / 3D / 1W aggregation for a 24/7 market.

This is markedly simpler than the equity equivalent. Equities need a market
calendar because "three days" means three trading sessions, and which
sessions those are depends on holidays and on where you start counting.

Crypto never closes, so a bar boundary is a pure function of the clock. Every
timeframe here is derived from 4h bars by grouping on UTC time, anchored to a
fixed epoch. No calendar, no reference asset, no per-symbol drift.

WHY EVERYTHING DERIVES FROM 4H
  One download, one source of truth. Requesting daily bars separately would
  introduce a second boundary convention that could disagree with the 4h
  aggregation — and disagreements between timeframes are close to
  undebuggable once signals are flowing.
"""

from __future__ import annotations

import pandas as pd

OHLC_AGG = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}

# Fixed anchor for multi-day grouping. A Monday, chosen so that 3D and 1W
# boundaries stay stable regardless of how much history is downloaded.
# Changing this shifts every 3D bar and therefore every 3D signal.
ANCHOR = pd.Timestamp("2017-01-02", tz="UTC")

BARS_PER_DAY_4H = 6


def _require_utc(frame: pd.DataFrame) -> pd.DataFrame:
    index = pd.DatetimeIndex(frame.index)
    if index.tz is None:
        index = index.tz_localize("UTC")
    else:
        index = index.tz_convert("UTC")
    out = frame.copy()
    out.index = index
    return out.sort_index()


def _aggregate(frame: pd.DataFrame, group_key: pd.Series, labels: pd.Series,
               expected: int) -> pd.DataFrame:
    """
    Group and drop incomplete buckets.

    A bar assembled from fewer than `expected` source bars is not the same
    bar: an exchange outage or a listing that started mid-period would
    otherwise produce a bar whose high and low are simply wrong, and pivots
    computed on it would be fiction.
    """
    work = frame.copy()
    work["_group"] = group_key.to_numpy()
    work["_label"] = labels.to_numpy()

    spec = {name: (name, how) for name, how in OHLC_AGG.items() if name in work.columns}
    spec["_count"] = ("close", "size")
    spec["_label"] = ("_label", "first")

    grouped = work.groupby("_group", sort=True).agg(**spec)
    grouped = grouped[grouped["_count"] == expected]
    grouped = grouped.drop(columns=["_count"]).set_index("_label")
    grouped.index.name = frame.index.name
    return grouped


def to_4h(frame: pd.DataFrame, drop_forming: bool = True) -> pd.DataFrame:
    """
    Native 4h bars, with the currently forming bar removed.

    O provider ja filtra pelo campo `confirm` da OKX, mas esta verificacao
    fica como segunda linha de defesa: uma barra em formacao produz pivots
    que desaparecem quatro horas depois, ou seja alertas que deixam
    retroativamente de existir.
    """
    out = _require_utc(frame)
    if drop_forming and len(out):
        now = pd.Timestamp.now(tz="UTC")
        last_open = out.index[-1]
        if last_open + pd.Timedelta(hours=4) > now:
            out = out.iloc[:-1]
    return out


def to_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """UTC days, requiring all six 4h bars to be present."""
    out = to_4h(frame)
    if out.empty:
        return out
    days = out.index.floor("D")
    return _aggregate(out, pd.Series(days, index=out.index),
                      pd.Series(days, index=out.index), BARS_PER_DAY_4H)


def to_n_days(frame: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """
    N-calendar-day bars anchored at ANCHOR.

    The anchor is what makes this deterministic across symbols: a coin listed
    last month falls on exactly the same 3D grid as one listed in 2018.
    """
    out = to_4h(frame)
    if out.empty:
        return out
    days = out.index.floor("D")
    offset = (days - ANCHOR).days // n
    labels = ANCHOR + pd.to_timedelta(offset * n, unit="D")
    return _aggregate(out, pd.Series(offset, index=out.index),
                      pd.Series(labels, index=out.index), BARS_PER_DAY_4H * n)


def to_weekly(frame: pd.DataFrame) -> pd.DataFrame:
    """Monday-open UTC weeks, requiring all 42 four-hour bars."""
    out = to_4h(frame)
    if out.empty:
        return out
    # floor to Monday without going through PeriodIndex (which drops the tz)
    weeks = out.index.floor("D") - pd.to_timedelta(out.index.dayofweek, unit="D")
    return _aggregate(out, pd.Series(weeks, index=out.index),
                      pd.Series(weeks, index=out.index), BARS_PER_DAY_4H * 7)


def build(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if timeframe == "4h":
        return to_4h(frame)
    if timeframe == "1D":
        return to_daily(frame)
    if timeframe == "3D":
        return to_n_days(frame, 3)
    if timeframe == "1W":
        return to_weekly(frame)
    raise ValueError(f"unknown timeframe: {timeframe}")
