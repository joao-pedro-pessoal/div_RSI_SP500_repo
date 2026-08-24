"""
provider.py — OHLCV for OKX USDT perpetual swaps.

PORQUE OKX E NAO BYBIT
  Medido a partir de um runner do GitHub Actions (IP Azure, EUA):

    api.bybit.com    403  "CloudFront is configured to block access
                           from your country"
    api.bytick.com   403  mesmo bloqueio (o espelho nao ajuda)
    fapi.binance.com 451  restricao legal
    www.okx.com      200  funciona

  A Bybit funciona a partir de Portugal, o que torna isto especialmente
  traicoeiro: o codigo passava em todos os testes locais e falhava em
  producao.

  Consequencia a assumir: os precos passam a ser da OKX. Perpetuos das
  duas exchanges seguem-se de perto mas nao sao identicos, portanto um
  sinal aqui pode nao coincidir exatamente com o grafico da Bybit.

DATA PROVENANCE
  Source : https://www.okx.com/api/v5/market/{candles,history-candles}
  Method : public HTTPS GET, no key, no authentication
  Sent   : instId, bar, pagination cursor. Nothing else.
  Stored : in memory for the duration of the scan.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import pandas as pd

API_ROOT = "https://www.okx.com/api/v5"
HISTORY_URL = f"{API_ROOT}/market/history-candles"
RECENT_URL = f"{API_ROOT}/market/candles"
INSTRUMENTS_URL = f"{API_ROOT}/public/instruments"

USER_AGENT = "CryptoDivergenceScanner/1.0"

BAR_1H = "1H"
BAR_4H = "4H"
PAGE_LIMIT = 100          # maximo da OKX no endpoint historico

# Rate limit publicado: 20 pedidos por 2 segundos por IP no history-candles.
# 0.12s entre pedidos da ~8/s, com margem.
DEFAULT_SLEEP = 0.12


@dataclass
class ProviderConfig:
    bars: int = 4200              # ~700 dias de barras 4h
    bar: str = BAR_4H             # "1H", "4H", ... (codigo de bar da OKX)
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
        host = urllib.parse.urlparse(full).netloc
        raise urllib.error.HTTPError(
            full, exc.code, f"{exc.reason} (host: {host})", exc.headers, None
        ) from None


def _get_retry(url: str, params: dict, config: ProviderConfig) -> dict:
    for attempt in range(config.retries):
        try:
            payload = _get(url, params, config.timeout)
            if payload.get("code") == "0":
                return payload
            if payload.get("code") == "50011" and attempt < config.retries - 1:
                time.sleep(1.5 * (attempt + 1))     # rate limited
                continue
            raise RuntimeError(f"OKX code {payload.get('code')}: {payload.get('msg')}")
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503, 504) and attempt < config.retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_perpetual_symbols(timeout: int = 30) -> set[str]:
    """
    USDT-margined perpetual swaps currently live on OKX.

    instId format is "BTC-USDT-SWAP". Only linear contracts settled in
    USDT with state "live" are kept, so expired or pre-launch instruments
    never enter the universe.
    """
    payload = _get(INSTRUMENTS_URL, {"instType": "SWAP"}, timeout)
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX instruments: {payload.get('msg')}")
    return {
        item["instId"]
        for item in payload.get("data", [])
        if item.get("state") == "live"
        and item.get("settleCcy") == "USDT"
        and item.get("ctType") == "linear"
    }


def _rows_to_frame(rows: list[list]) -> pd.DataFrame:
    """
    OKX candle layout: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]

    `confirm` is "1" for a closed candle and "0" for the one still forming.
    Filtering on it is more reliable than comparing timestamps to the clock,
    which is what the Bybit version had to do.
    """
    frame = pd.DataFrame(rows, columns=[
        "ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm",
    ])
    frame = frame[frame["confirm"].astype(str) == "1"]
    if frame.empty:
        return pd.DataFrame()

    frame = frame.copy()
    frame["timestamp"] = pd.to_datetime(frame["ts"].astype("int64"), unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "vol"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    out = frame.set_index("timestamp")[["open", "high", "low", "close", "vol"]]
    return out.rename(columns={"vol": "volume"})


def fetch_klines(inst_id: str, config: ProviderConfig = ProviderConfig()) -> pd.DataFrame:
    """
    4h OHLCV for one perpetual swap, oldest first.

    OKX returns newest-first and caps each page at 100 candles, so history
    is walked backwards with the `after` cursor: each request asks for
    candles strictly older than the oldest one collected so far.
    """
    collected: list[list] = []
    cursor: str | None = None

    while len(collected) < config.bars:
        params = {"instId": inst_id, "bar": config.bar, "limit": PAGE_LIMIT}
        if cursor is not None:
            params["after"] = cursor

        # First page from the recent endpoint so the newest closed candle is
        # never missed; history-candles can lag slightly behind it.
        url = RECENT_URL if cursor is None else HISTORY_URL
        payload = _get_retry(url, params, config)
        rows = payload.get("data", [])
        if not rows:
            break

        collected.extend(rows)
        cursor = rows[-1][0]              # oldest ts on this page
        if len(rows) < PAGE_LIMIT:
            break
        time.sleep(config.sleep_between)

    if not collected:
        return pd.DataFrame()

    frame = _rows_to_frame(collected)
    if frame.empty:
        return frame

    out = frame[~frame.index.duplicated(keep="last")].sort_index().dropna()
    out.index.name = "timestamp"
    return out


def fetch_many(
    symbols: list[str],
    config: ProviderConfig = ProviderConfig(),
    verbose: bool = True,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Returns (data, failed). One failure never stops the scan."""
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
            print(f"[provider] {index}/{len(symbols)}", flush=True)
        time.sleep(config.sleep_between)
    return data, failed
