"""Public Bybit V5 OHLCV for linear USDT perpetuals (no API key).

Only closed candles are used, based on Bybit's response time, never the
VM clock. There is no automatic fallback to another exchange.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import pandas as pd

API_ROOT = "https://api.bybit.com/v5"
KLINES_URL = f"{API_ROOT}/market/kline"
INSTRUMENTS_URL = f"{API_ROOT}/market/instruments-info"
USER_AGENT = "CryptoDivergenceScanner/2.0"
BAR_1H = "60"
BAR_4H = "240"
PAGE_LIMIT = 1000
DEFAULT_SLEEP = 0.12


@dataclass
class ProviderConfig:
    bars: int = 4200
    bar: str = BAR_4H
    sleep_between: float = DEFAULT_SLEEP
    retries: int = 3
    timeout: int = 30


def _get(url: str, params: dict, timeout: int) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        hint = ""
        if exc.code == 403:
            hint = "; verificar restricoes do IP/regiao da VM (Bybit bloqueia IPs dos EUA)"
        raise urllib.error.HTTPError(
            full, exc.code, f"{exc.reason} (host: api.bybit.com){hint}", exc.headers, None
        ) from None


def _get_retry(url: str, params: dict, config: ProviderConfig) -> dict:
    for attempt in range(max(1, config.retries)):
        try:
            payload = _get(url, params, config.timeout)
            if payload.get("retCode") == 0:
                return payload
            if payload.get("retCode") in (10006, 10000, 10016) and attempt < config.retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"Bybit code {payload.get('retCode')}: {payload.get('retMsg')}")
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt >= config.retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt >= config.retries - 1:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("Bybit request failed")


def fetch_perpetual_symbols(timeout: int = 30) -> set[str]:
    """All Trading linear USDT perpetuals, including paginated instruments."""
    config = ProviderConfig(timeout=timeout)
    symbols: set[str] = set()
    seen_cursors: set[str] = set()
    params = {"category": "linear", "status": "Trading", "limit": 1000}
    while True:
        result = _get_retry(INSTRUMENTS_URL, params, config)["result"]
        symbols.update(
            item["symbol"] for item in result["list"]
            if item.get("status") == "Trading"
            and item.get("contractType") == "LinearPerpetual"
            and item.get("settleCoin") == "USDT"
            and item.get("quoteCoin") == "USDT"
            and not item.get("isPreListing", False)
        )
        cursor = result.get("nextPageCursor")
        if not cursor:
            return symbols
        if cursor in seen_cursors:
            raise RuntimeError("Bybit instruments pagination did not advance")
        seen_cursors.add(cursor)
        params["cursor"] = cursor
        time.sleep(config.sleep_between)


def _interval(bar: str) -> str:
    interval = {"1H": BAR_1H, "4H": BAR_4H}.get(str(bar).upper(), str(bar))
    if interval not in (BAR_1H, BAR_4H):
        raise ValueError(f"Unsupported base interval: {bar}; use 60 or 240")
    return interval


def _rows_to_frame(rows: list[list], *, interval: str, server_time_ms: int) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "turnover"])
    starts = pd.to_numeric(frame["ts"], errors="raise").astype("int64")
    frame["timestamp"] = pd.to_datetime(starts, unit="ms", utc=True)
    frame = frame.loc[starts + int(interval) * 60_000 <= server_time_ms].copy()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    out = frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    return out[~out.index.duplicated(keep="last")].sort_index().dropna()


def fetch_klines(inst_id: str, config: ProviderConfig | None = None) -> pd.DataFrame:
    config = config or ProviderConfig()
    if config.bars <= 0:
        raise ValueError("bars must be positive")
    interval = _interval(config.bar)
    # Accept old explicit CLI symbols during migration; universe uses BTCUSDT.
    symbol = inst_id.upper().replace("-USDT-SWAP", "USDT")
    collected: dict[int, list] = {}
    cursor: int | None = None
    server_time_ms: int | None = None
    # One extra row allows for the current, unclosed candle on the first page.
    while len(collected) < config.bars + 1:
        limit = min(PAGE_LIMIT, config.bars + 1 - len(collected))
        params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
        if cursor is not None:
            params["end"] = cursor
        payload = _get_retry(KLINES_URL, params, config)
        if server_time_ms is None:
            server_time_ms = int(payload["time"])
            if server_time_ms <= 0:
                raise RuntimeError("Invalid Bybit server time")
        rows = payload["result"]["list"]
        if not rows:
            break
        oldest = min(int(row[0]) for row in rows)
        if cursor is not None and oldest > cursor:
            raise RuntimeError("Bybit kline pagination did not advance")
        collected.update((int(row[0]), row) for row in rows)
        cursor = oldest - 1  # end is inclusive; exclude the oldest fetched row
        if len(rows) < limit:
            break
        time.sleep(config.sleep_between)
    if not collected:
        return pd.DataFrame()
    return _rows_to_frame(list(collected.values()), interval=interval,
                          server_time_ms=server_time_ms).tail(config.bars)


def fetch_many(symbols: list[str], config: ProviderConfig | None = None,
               verbose: bool = True) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Returns (data, failed). One symbol failure never stops the scan."""
    config = config or ProviderConfig()
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
        if verbose and index % 10 == 0:
            print(f"[provider] Bybit {index}/{len(symbols)}", flush=True)
        time.sleep(config.sleep_between)
    return data, failed
