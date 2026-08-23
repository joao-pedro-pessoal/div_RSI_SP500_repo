"""
universe.py — top N coins by market cap, mapped to Bybit USDT perpetuals.

DATA PROVENANCE
  Ranking : https://api.coingecko.com/api/v3/coins/markets
            Public endpoint, no API key. Sent: nothing but query parameters.
  Symbols : https://api.bybit.com/v5/market/instruments-info
            Public endpoint, no key, no authentication.
  Stored  : universe/ranking_<date>.json on disk, committed to the repo.
            Nothing is sent anywhere else.

WHY THE RANKING IS SAVED
  The top 100 by market cap is a moving target, and crypto assets do not
  merely leave an index — they die outright. A backtest run later against
  today's top 100 would silently delete every coin that collapsed, which
  is survivorship bias in its most severe form.

  Saving each run's ranking costs nothing now and builds a point-in-time
  record that cannot be bought or reconstructed afterwards.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
BYBIT_INSTRUMENTS = "https://api.bybit.com/v5/market/instruments-info"

USER_AGENT = "CryptoDivergenceScanner/1.0"

# Wrapped and liquid-staking derivatives track an underlying asset almost
# exactly. Their divergences duplicate signals already produced by the
# underlying, so they add alert volume without adding information.
WRAPPED_PATTERNS = (
    "wrapped", "staked", "liquid staking", "restaked", "bridged", "peg",
)
WRAPPED_SYMBOLS = {
    "WBTC", "WETH", "WBETH", "STETH", "WSTETH", "WEETH", "EETH", "RETH",
    "CBBTC", "CBETH", "SOLVBTC", "LBTC", "METH", "SWETH", "OSETH", "ANKRETH",
    "RSETH", "EZETH", "PUFETH", "MSOL", "JITOSOL", "BNSOL", "JUPSOL", "BSOL",
    "STBTC", "TBTC", "BTCB", "RENBTC", "HBTC", "WBNB", "WAVAX", "WMATIC",
    "WSOL", "WHYPE", "WSTHYPE", "SUPEROETH", "OETH", "FRXETH", "SFRXETH",
}

# Some stablecoins are missed by CoinGecko's category filter, so this list
# is a safety net rather than the primary mechanism.
EXTRA_STABLE_SYMBOLS = {
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "TUSD", "USDD", "USDP",
    "USDS", "USD1", "BUIDL", "USDY", "GUSD", "LUSD", "CRVUSD", "FRAX", "SUSD",
    "USDX", "RLUSD", "USDF", "USR", "DEUSD", "USDG", "EURC", "EURS", "STEUR",
    "SUSDE", "SUSDS", "SDAI", "USTC",
}


@dataclass(frozen=True)
class Coin:
    rank: int
    coin_id: str          # CoinGecko id, e.g. "bitcoin"
    symbol: str           # e.g. "BTC"
    name: str
    market_cap: float
    exchange_symbol: str  # e.g. "BTCUSDT"


def _get_json(url: str, params: dict | None = None, timeout: int = 30) -> dict | list:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json_retry(url: str, params: dict | None = None, attempts: int = 4) -> dict | list:
    """
    CoinGecko's free tier rate-limits aggressively and answers 429.

    Backoff is exponential because retrying immediately after a 429 tends to
    extend the block rather than resolve it.
    """
    delay = 5.0
    for attempt in range(attempts):
        try:
            return _get_json(url, params)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503, 504) and attempt < attempts - 1:
                print(f"[universe] HTTP {exc.code}; waiting {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_stablecoin_ids() -> set[str]:
    """CoinGecko's own stablecoin category. Empty set on failure — the
    hardcoded symbol list still applies."""
    try:
        rows = _get_json_retry(COINGECKO_MARKETS, {
            "vs_currency": "usd", "category": "stablecoins",
            "order": "market_cap_desc", "per_page": 250, "page": 1,
        })
        return {str(r["id"]) for r in rows}
    except Exception as exc:
        print(f"[universe] stablecoin category unavailable ({type(exc).__name__}); using fallback list")
        return set()


def fetch_ranking(pages: int = 2, per_page: int = 250) -> list[dict]:
    """
    Market-cap ranking, newest first.

    More than 100 are fetched deliberately: after removing stablecoins,
    wrapped tokens, and coins without a perpetual contract, a top-100 request
    would leave well under 100 tradeable names.
    """
    rows: list[dict] = []
    for page in range(1, pages + 1):
        batch = _get_json_retry(COINGECKO_MARKETS, {
            "vs_currency": "usd", "order": "market_cap_desc",
            "per_page": per_page, "page": page,
        })
        if not batch:
            break
        rows.extend(batch)
        if page < pages:
            time.sleep(3.0)   # free tier is roughly 5-15 calls/minute
    return rows


def fetch_perpetual_symbols() -> set[str]:
    """
    USDT-margined perpetuals currently trading on Bybit.

    `category=linear` covers USDT and USDC linear contracts. Only entries
    with status Trading and settleCoin USDT are kept, so delisted or
    pre-launch contracts do not enter the universe.
    """
    symbols: set[str] = set()
    cursor = ""
    for _ in range(20):                      # generous page guard
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = _get_json(BYBIT_INSTRUMENTS, params)
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        for item in result.get("list", []):
            if (
                item.get("status") == "Trading"
                and item.get("settleCoin") == "USDT"
                and item.get("contractType") == "LinearPerpetual"
            ):
                symbols.add(item["symbol"])
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            break
        time.sleep(0.2)
    return symbols


def is_wrapped(symbol: str, name: str) -> bool:
    if symbol.upper() in WRAPPED_SYMBOLS:
        return True
    lowered = name.lower()
    return any(pattern in lowered for pattern in WRAPPED_PATTERNS)


def build_universe(
    limit: int = 100,
    exclude_stablecoins: bool = True,
    exclude_wrapped: bool = True,
    snapshot_dir: Path | None = None,
) -> list[Coin]:
    """Ranked coins that actually have a Bybit USDT perpetual."""
    ranking = fetch_ranking()
    if not ranking:
        raise RuntimeError("empty ranking from CoinGecko")

    stable_ids = fetch_stablecoin_ids() if exclude_stablecoins else set()
    perps = fetch_perpetual_symbols()
    if not perps:
        raise RuntimeError("no perpetual symbols returned by Bybit")

    coins: list[Coin] = []
    dropped = {"stablecoin": 0, "wrapped": 0, "no_perp": 0}

    for row in ranking:
        if len(coins) >= limit:
            break
        symbol = str(row.get("symbol", "")).upper()
        name = str(row.get("name", ""))
        coin_id = str(row.get("id", ""))
        if not symbol:
            continue

        if exclude_stablecoins and (coin_id in stable_ids or symbol in EXTRA_STABLE_SYMBOLS):
            dropped["stablecoin"] += 1
            continue
        if exclude_wrapped and is_wrapped(symbol, name):
            dropped["wrapped"] += 1
            continue

        # The mapping CoinGecko symbol -> exchange symbol is the fragile
        # step: tickers collide and are not standardised across venues.
        # Verifying against the live instrument list is what keeps this
        # honest — an unverified guess would silently scan the wrong asset.
        exchange_symbol = f"{symbol}USDT"
        if exchange_symbol not in perps:
            dropped["no_perp"] += 1
            continue

        coins.append(Coin(
            rank=len(coins) + 1,
            coin_id=coin_id,
            symbol=symbol,
            name=name,
            market_cap=float(row.get("market_cap") or 0.0),
            exchange_symbol=exchange_symbol,
        ))

    print(f"[universe] {len(coins)} coins | dropped: "
          f"{dropped['stablecoin']} stablecoins, {dropped['wrapped']} wrapped, "
          f"{dropped['no_perp']} without perpetual")

    if snapshot_dir is not None:
        save_snapshot(coins, snapshot_dir)
    return coins


def save_snapshot(coins: list[Coin], directory: Path) -> Path:
    """Point-in-time record. See the note at the top of this module."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = directory / f"ranking_{stamp}.json"
    path.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "coins": [asdict(c) for c in coins],
    }, indent=2))
    return path
