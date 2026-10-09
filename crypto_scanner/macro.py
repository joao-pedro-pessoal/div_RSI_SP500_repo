"""
macro.py — RSI divergences for a short watchlist: gold, silver, S&P 500, BTC.

Every timeframe from 1h to 1W. Same detector as the crypto and S&P scanners
(early alert + confirmed alert, regular + hidden), so a signal here means
exactly what it means there.

DATA SOURCES
  BTC              OKX perpetual (same feed as the crypto scanner)
  Gold / Silver    Yahoo front-month futures (GC=F / SI=F) -- compare with
                   COMEX:GC1! / COMEX:SI1! on TradingView
  S&P 500          Yahoo ^GSPC index -- compare with SP:SPX

  OKX also lists XAU/XAG/US500 perpetuals, but US500 only exists since
  2026-09 and XAG since 2026-01: not enough bars for 1D/3D/1W. They also
  trade through weekends, which the real markets do not, so RSI would not
  match the charts people actually look at.

BAR BOUNDARIES (Yahoo assets)
  Hourly bars come from Yahoo as they are. 4h bars are built from them,
  anchored at the session start in New York time, like TradingView:
    futures   18:00 ET (bins 18,22,02,06,10,14; the 14:00 bin ends at the
              17:00 daily break)
    S&P 500   09:30 ET (bins 09:30-13:30 and 13:30-16:00)
  1D comes from Yahoo daily bars; 3D and 1W are built from those.

  A bar only counts once it is CLOSED: its end time (capped at the session
  close) plus a safety delay for Yahoo's feed lag must be in the past.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from trading_scanner import timeframes as equity_tf

from .config import ScannerConfig
from .indicators import wilder_rsi
from .models import DivergenceSignal
from .scanner import COLUMN_MAP
from .tradingview_divergence import find_tradingview_divergences

NY = "America/New_York"
OHLC = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
TF_ORDER = ("1h", "4h", "1D", "3D", "1W")


@dataclass
class Asset:
    name: str                 # shown in alerts, e.g. "GOLD (XAU)"
    source: str               # "yahoo" | "okx"
    symbol: str               # "GC=F", "^GSPC", "BTC-USDT-SWAP"
    chart: str                # TradingView symbol for the link
    session: str = "futures"  # yahoo only: "futures" | "us_equity"


# session -> (4h anchor, daily close) in New York time
SESSIONS = {
    "futures": ("18:00", "17:00"),
    "us_equity": ("09:30", "16:00"),
}


@dataclass
class MacroConfig:
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    assets: list[Asset] = field(default_factory=list)
    # Yahoo futures data lags a few minutes; a bar is only used once its end
    # is at least this long in the past.
    yahoo_delay_minutes: int = 15
    hourly_days: int = 700        # Yahoo serves at most 730 days of 1h bars
    daily_start: str = "2015-01-01"
    okx_bars_1h: int = 1000
    okx_bars_4h: int = 4200


# --------------------------------------------------------------------- bars

def _parse_hhmm(value: str) -> pd.Timedelta:
    hours, minutes = value.split(":")
    return pd.Timedelta(hours=int(hours), minutes=int(minutes))


def _session_close(start_local: pd.DatetimeIndex, close: pd.Timedelta) -> pd.DatetimeIndex:
    """First session close strictly after each bar start (same or next day)."""
    same_day = start_local.normalize() + close
    return same_day.where(same_day > start_local, same_day + pd.Timedelta(days=1))


def _bar_ends(start_local: pd.DatetimeIndex, length: pd.Timedelta, close: pd.Timedelta) -> pd.DatetimeIndex:
    natural = start_local + length
    capped = _session_close(start_local, close)
    return natural.where(natural <= capped, capped)


def closed_hourly(h1: pd.DataFrame, session: str, now: pd.Timestamp, delay: pd.Timedelta) -> pd.DataFrame:
    """Yahoo 1h bars -> only bars that have fully closed (session-aware)."""
    if h1.empty:
        return h1
    _, close = SESSIONS[session]
    local = pd.DatetimeIndex(h1.index).tz_convert(NY)
    ends = _bar_ends(local, pd.Timedelta(hours=1), _parse_hhmm(close))
    out = h1.copy()
    out.index = local
    return out[(ends + delay) <= now.tz_convert(NY)]


def to_session_4h(h1: pd.DataFrame, session: str, now: pd.Timestamp, delay: pd.Timedelta) -> pd.DataFrame:
    """Aggregate CLOSED 1h bars into session-anchored 4h bars; drop the forming one."""
    if h1.empty:
        return h1
    anchor_s, close_s = SESSIONS[session]
    anchor, close = _parse_hhmm(anchor_s), _parse_hhmm(close_s)
    local = pd.DatetimeIndex(h1.index).tz_convert(NY)

    base = local.normalize() + anchor
    base = base.where(base <= local, base - pd.Timedelta(days=1))
    offset = ((local - base) // pd.Timedelta(hours=4)) * pd.Timedelta(hours=4)
    bin_start = base + offset

    frame = h1.copy()
    frame.index = local
    agg = dict(OHLC)
    if "Volume" in frame.columns:
        agg["Volume"] = "sum"
    bars = frame.groupby(bin_start).agg(agg).dropna(subset=["Close"])
    bars.index = pd.DatetimeIndex(bars.index)
    ends = _bar_ends(bars.index, pd.Timedelta(hours=4), close)
    return bars[(ends + delay) <= now.tz_convert(NY)]


def closed_daily(daily: pd.DataFrame, session: str, now: pd.Timestamp, delay: pd.Timedelta) -> pd.DataFrame:
    """Drop today's daily bar until the session close (+ delay) has passed."""
    if daily.empty:
        return daily
    _, close = SESSIONS[session]
    out = daily.copy()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    close_at = (out.index + _parse_hhmm(close)).tz_localize(NY)
    return out[(close_at + delay) <= now.tz_convert(NY)]


def yahoo_timeframes(h1: pd.DataFrame, daily: pd.DataFrame, session: str,
                     now: pd.Timestamp, delay: pd.Timedelta) -> dict[str, pd.DataFrame]:
    h1_closed = closed_hourly(h1, session, now, delay)
    d1 = closed_daily(daily, session, now, delay)
    return {
        "1h": h1_closed,
        "4h": to_session_4h(h1_closed, session, now, delay),
        "1D": d1,
        "3D": equity_tf.to_three_trading_days(d1, d1.index, anchor="2000-01-03"),
        "1W": equity_tf.to_weekly(d1),
    }


def okx_timeframes(h1: pd.DataFrame, h4: pd.DataFrame) -> dict[str, pd.DataFrame]:
    from .timeframes import build

    out = {"1h": h1.rename(columns=COLUMN_MAP)}
    for tf in ("4h", "1D", "3D", "1W"):
        out[tf] = build(h4, tf).rename(columns=COLUMN_MAP)
    return out


# --------------------------------------------------------------------- fetch

def _yahoo(symbol: str, **kwargs) -> pd.DataFrame:
    import yfinance as yf

    frame = yf.download(symbol, auto_adjust=True, progress=False,
                        multi_level_index=False, threads=False, **kwargs)
    if frame is None or frame.empty:
        raise RuntimeError(f"Yahoo returned no data for {symbol} ({kwargs.get('interval')})")
    frame = frame[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in frame.columns]]
    return frame.dropna(subset=["Open", "High", "Low", "Close"])


def fetch_asset(asset: Asset, cfg: MacroConfig, now: pd.Timestamp) -> dict[str, pd.DataFrame]:
    if asset.source == "okx":
        from .provider import BAR_1H, ProviderConfig, fetch_klines

        h1 = fetch_klines(asset.symbol, ProviderConfig(bars=cfg.okx_bars_1h, bar=BAR_1H))
        h4 = fetch_klines(asset.symbol, ProviderConfig(bars=cfg.okx_bars_4h))
        return okx_timeframes(h1, h4)
    if asset.source == "yahoo":
        h1 = _yahoo(asset.symbol, period=f"{cfg.hourly_days}d", interval="1h")
        daily = _yahoo(asset.symbol, start=cfg.daily_start, interval="1d")
        delay = pd.Timedelta(minutes=cfg.yahoo_delay_minutes)
        return yahoo_timeframes(h1, daily, asset.session, now, delay)
    raise ValueError(f"unknown source {asset.source!r} for {asset.name}")


# --------------------------------------------------------------------- scan

def scan_bars(asset: Asset, timeframe: str, bars: pd.DataFrame, s: ScannerConfig) -> list[DivergenceSignal]:
    if len(bars) <= s.rsi_period + s.pivot_left + s.pivot_right:
        return []
    rsi = wilder_rsi(bars["Close"], s.rsi_period)
    signals = find_tradingview_divergences(
        asset.name, timeframe, bars, rsi,
        left=s.pivot_left, right=s.pivot_right,
        range_lower=s.min_distance_bars, range_upper=s.max_distance_bars,
        min_price_change_pct=s.min_price_change_pct, min_rsi_delta=s.min_rsi_delta,
        kinds=s.divergence_types, confirm_bars=s.confirm_bars,
        alert_each_bar=s.alert_each_bar, also_confirmed=s.send_confirmed,
    )
    window = s.age_window(timeframe)
    return [sig for sig in signals if (len(bars) - 1 - sig.confirmation_position) <= window]


# --------------------------------------------------------------------- message

def _fmt_price(value: float) -> str:
    return f"{value:,.2f}" if abs(value) >= 100 else f"{value:,.4f}".rstrip("0").rstrip(".")


def _stamp(ts: pd.Timestamp, timeframe: str) -> str:
    ts = pd.Timestamp(ts)
    if timeframe in ("1h", "4h"):
        if ts.tzinfo is not None:
            ts = ts.tz_convert("UTC")
        return ts.strftime("%Y-%m-%d %H:%M UTC")
    return str(ts.date())


def format_signal(signal: DivergenceSignal, asset: Asset, source_note: str) -> str:
    import html
    import urllib.parse

    bullish = signal.bullish
    icon = "\U0001F7E2" if bullish else "\U0001F534"
    price_label = "low" if bullish else "high"
    p_arrow = "↑" if signal.second_pivot.value > signal.first_pivot.value else "↓"
    r_arrow = "↑" if signal.second_rsi > signal.first_rsi else "↓"
    status = "Confirmed" if signal.confirmed else "Detected (still forming, may change)"
    high_tf = signal.timeframe in ("1D", "3D", "1W")
    head = f"{icon} {signal.title}"
    pair = f"{html.escape(asset.name)} — {signal.timeframe}"
    if high_tf:
        head, pair = f"<b>{head}</b>", f"<b>{pair}</b>"
    chart = urllib.parse.quote(asset.chart)
    tf = signal.timeframe
    return (
        f"{head}\n"
        f"{pair}\n\n"
        f"Previous {price_label} ({_stamp(signal.first_pivot.timestamp, tf)}): "
        f"{_fmt_price(signal.first_pivot.value)}\n"
        f"New {price_label} ({_stamp(signal.second_pivot.timestamp, tf)}): "
        f"{_fmt_price(signal.second_pivot.value)} {p_arrow}\n\n"
        f"Previous RSI: {signal.first_rsi:.2f}\n"
        f"New RSI: {signal.second_rsi:.2f} {r_arrow}\n\n"
        f"Distance: {signal.distance_bars} candles\n"
        f"{status}: {_stamp(signal.confirmation_time, tf)}\n"
        f"{source_note}\n"
        f"\U0001F4CA https://www.tradingview.com/chart/?symbol={chart}"
    )


def source_note(asset: Asset) -> str:
    feed = "OKX" if asset.source == "okx" else f"Yahoo {asset.symbol}"
    return f"Data: {feed} · Chart: {asset.chart}"
