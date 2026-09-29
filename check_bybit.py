#!/usr/bin/env python3
"""Read-only preflight: real Bybit instruments and BTC closed 1h/4h history.

No Telegram messages, credentials, ranking requests or state writes.
"""
from crypto_scanner.provider import ProviderConfig, fetch_klines, fetch_perpetual_symbols


def main():
    try:
        symbols = fetch_perpetual_symbols()
        if "BTCUSDT" not in symbols:
            raise RuntimeError("BTCUSDT missing from Trading USDT perpetuals")
        print(f"Bybit OK: {len(symbols)} USDT perpetuals; BTCUSDT available")
        for interval, count in (("60", 3), ("240", 1002)):
            frame = fetch_klines("BTCUSDT", ProviderConfig(bar=interval, bars=count))
            if len(frame) != count or not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
                raise RuntimeError(f"Invalid {interval}m history: {len(frame)}/{count} candles")
            print(f"BTCUSDT {interval}m OK: {len(frame)} closed candles; last open {frame.index[-1]}")
        print("BYBIT PREFLIGHT OK")
        return 0
    except Exception as exc:
        print(f"BYBIT PREFLIGHT FAILED: {type(exc).__name__}: {exc}")
        print("Nao ativar a migracao nesta VM enquanto este teste falhar. HTTP 403 pode indicar bloqueio de IP/regiao.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
