from __future__ import annotations

import pandas as pd


OHLC_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily OHLC into Friday-labelled weeks and drop partial week."""
    daily = _prepare(daily)
    agg = dict(OHLC_AGG)
    if "Volume" in daily.columns:
        agg["Volume"] = "sum"
    weekly = daily.resample("W-FRI", label="right", closed="right").agg(agg).dropna(subset=["Close"])
    # If the latest observed session is before the Friday label, that week may
    # still be in progress. A Friday holiday is therefore confirmed next run.
    if len(weekly) and weekly.index[-1] > daily.index[-1]:
        weekly = weekly.iloc[:-1]
    return weekly


def to_three_trading_days(
    daily: pd.DataFrame,
    reference_calendar: pd.DatetimeIndex,
    anchor: str = "2000-01-03",
) -> pd.DataFrame:
    """Aggregate exactly three reference-market sessions per bar.

    Grouping is fixed by a reference calendar (normally SPY) and a fixed anchor,
    so IPO dates or missing rows for one ticker never shift all future 3D bars.
    Only complete 3-session groups with all three ticker observations survive.
    """
    daily = _prepare(daily)
    calendar = pd.DatetimeIndex(reference_calendar).tz_localize(None).normalize()
    calendar = calendar[~calendar.duplicated()].sort_values()
    anchor_ts = pd.Timestamp(anchor).normalize()
    calendar = calendar[calendar >= anchor_ts]
    if len(calendar) == 0:
        return daily.iloc[0:0].copy()

    map_df = pd.DataFrame({"session": calendar, "group": range(len(calendar))})
    map_df["group"] = map_df["group"] // 3
    expected = map_df.groupby("group").agg(expected=("session", "size"), label=("session", "max"))
    expected = expected[expected["expected"] == 3]

    working = daily.reset_index(names="session")
    working = working.merge(map_df, how="inner", on="session")
    if working.empty:
        return daily.iloc[0:0].copy()

    agg: dict[str, tuple[str, str]] = {
        "Open": ("Open", "first"),
        "High": ("High", "max"),
        "Low": ("Low", "min"),
        "Close": ("Close", "last"),
        "observed": ("session", "size"),
    }
    if "Volume" in working.columns:
        agg["Volume"] = ("Volume", "sum")
    grouped = working.groupby("group").agg(**agg)
    grouped = grouped.join(expected, how="inner")
    grouped = grouped[grouped["observed"] == 3].drop(columns=["observed", "expected"])
    grouped = grouped.set_index("label")
    grouped.index.name = None
    return grouped


def convert_timeframe(
    daily: pd.DataFrame,
    timeframe: str,
    *,
    reference_calendar: pd.DatetimeIndex,
    three_day_anchor: str,
) -> pd.DataFrame:
    if timeframe == "1D":
        return _prepare(daily)
    if timeframe == "3D":
        return to_three_trading_days(daily, reference_calendar, anchor=three_day_anchor)
    if timeframe == "1W":
        return to_weekly(daily)
    raise ValueError(f"unsupported timeframe: {timeframe}")

