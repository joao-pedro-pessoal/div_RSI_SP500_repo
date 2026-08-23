"""
provider.py — OHLCV for Bybit USDT perpetuals.

DATA PROVENANCE
  Source : https://api.bybit.com/v5/market/kline
  Method : public HTTPS GET, no API key, no authentication
  Sent   : symbol, interval, time range. Nothing else.
  Stored : in memory for the duration of the scan.

WHY PERPETUALS AND NOT SPOT
  This is what the charts being replicated show. It is worth being explicit
  about the consequence: perpetual prices are not spot prices. Funding
  payments and basis mean a perpetual can drift from the underlying asset,
  and any future return measurement inherits that drift.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import pandas as pd

KLINE_URL = "https://api.bybit.com/v5/market/kline"
USER_AGENT = "CryptoDivergenceScanner/1.0"

MAX_LIMIT = 1000          # Bybit's per-request maximum

# Bybit interval codes. Only 4h is fetched natively; 1D, 3D and 1W are
# aggregated locally so that every timeframe shares one source of truth
# and one bar-boundary convention.
INTERVAL_4H = "240"


@dataclass
class ProviderConfig:
    bars: int = 1500              # 4h bars to fetch (~250 days)
    sleep_between: float = 0.15   # public endpoint is generous; stay polite
    retries: int = 3
    timeout: int = 30


def _request(params: dict, timeout: int) -> dict:
    url = f"{KLINE_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_klines(symbol: str, config: ProviderConfig = ProviderConfig()) -> pd.DataFrame:
    """
    4h OHLCV for one perpetual, oldest first.

    Bybit returns at most 1000 candles per call and orders them NEWEST FIRST.
    Both details matter: the ordering must be reversed, and more than 1000
    bars requires paging backwards with the `end` parameter.
    """
    collected: list[list] = []
    end_ms: int | None = None

    while len(collected) < config.bars:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": INTERVAL_4H,
            "limit": min(MAX_LIMIT, config.bars - len(collected)),
        }
        if end_ms is not None:
            params["end"] = end_ms

        payload = None
        for attempt in range(config.retries):
            try:
                payload = _request(params, config.timeout)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 502, 503, 504) and attempt < config.retries - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise
            except Exception:
                if attempt < config.retries - 1:
                    time.sleep(1.0)
                    continue
                raise

        if payload is None or payload.get("retCode") != 0:
            message = (payload or {}).get("retMsg", "no response")
            raise RuntimeError(f"{symbol}: Bybit error: {message}")

        rows = payload.get("result", {}).get("list", [])
        if not rows:
            break

        collected.extend(rows)
        # page backwards: next request ends just before the oldest bar so far
        oldest_ms = int(rows[-1][0])
        end_ms = oldest_ms - 1
        if len(rows) < params["limit"]:
            break
        time.sleep(config.sleep_between)

    if not collected:
        return pd.DataFrame()

    frame = pd.DataFrame(
        collected,
        columns=["start", "open", "high", "low", "close", "volume", "turnover"],
    )
    frame["timestamp"] = pd.to_datetime(frame["start"].astype("int64"), unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    out = (
        frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
        .sort_index()                                  # Bybit returns newest first
        .loc[lambda df: ~df.index.duplicated(keep="last")]
        .dropna()
    )
    out.index.name = "timestamp"
    return out


def fetch_many(
    symbols: list[str],
    config: ProviderConfig = ProviderConfig(),
    verbose: bool = True,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Returns (data, failed_symbols). One failure never stops the scan."""
    data: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    for index, symbol in enumerate(symbols, start=1):
        try:
            frame = fetch_klines(symbol, config)
            if frame.empty:
                failed.append(symbol)
            else:
                data[symbol] = frame
        except Exception as exc:
            failed.append(symbol)
            if verbose:
                print(f"[provider] {symbol}: {type(exc).__name__}: {exc}")
        if verbose and index % 25 == 0:
            print(f"[provider] {index}/{len(symbols)}")
        time.sleep(config.sleep_between)
    return data, failed
