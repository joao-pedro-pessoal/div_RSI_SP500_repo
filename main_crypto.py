#!/usr/bin/env python3
"""
Crypto RSI divergence scanner — top 100 by market cap, Bybit USDT perpetuals.

    python main.py --dry-run --symbols BTCUSDT ETHUSDT     # local test
    python main.py --dry-run                               # full universe
    python main.py                                         # send to Telegram
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crypto_scanner.config import load_config
from crypto_scanner.provider import ProviderConfig, fetch_many
from crypto_scanner.scanner import CryptoDivergenceScanner
from crypto_scanner.state import SignalState, write_heartbeat
from crypto_scanner.telegram_client import TelegramClient
from crypto_scanner.universe import build_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config_crypto_4h.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="print alerts instead of sending them")
    parser.add_argument("--symbols", nargs="*",
                        help="explicit symbols, skipping the universe lookup")
    parser.add_argument("--timeframe", action="append", dest="timeframes",
                        help="restrict to one timeframe (repeatable)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    telegram = TelegramClient(dry_run=args.dry_run)
    heartbeat_path = Path(config.heartbeat_file)

    try:
        if args.symbols:
            symbols = [s.upper() for s in args.symbols]
            print(f"[universe] {len(symbols)} symbols supplied explicitly")
        else:
            coins = build_universe(
                limit=config.universe.limit,
                exclude_stablecoins=config.universe.exclude_stablecoins,
                exclude_wrapped=config.universe.exclude_wrapped,
                snapshot_dir=Path(config.universe.snapshot_dir),
            )
            symbols = [c.exchange_symbol for c in coins]

        provider_config = ProviderConfig(
            bars=config.data.bars_4h,
            sleep_between=config.data.sleep_between,
            retries=config.data.retries,
        )
        print(f"[provider] downloading 4h bars for {len(symbols)} symbols...")
        market_data, download_failures = fetch_many(symbols, provider_config)
        print(f"[provider] {len(market_data)}/{len(symbols)} downloaded")

        if not market_data:
            raise RuntimeError("no market data downloaded")

        scanner = CryptoDivergenceScanner(config)
        # O construtor ja carrega o ficheiro; nao ha metodo load().
        state = SignalState(Path(config.state_file))

        new_signals = []
        skipped = 0
        scan_errors: dict[str, str] = {}

        for symbol, bars in market_data.items():
            report = scanner.scan_symbol(symbol, bars, timeframes=args.timeframes)
            if report.skipped:
                skipped += 1
            scan_errors.update(report.errors)
            for signal in report.signals:
                if not state.contains(signal.signal_id):
                    new_signals.append(signal)

        # State is saved during the loop, not only after it. If the process
        # dies midway, already-delivered alerts stay marked and are not
        # re-sent on the next run.
        sent = 0
        send_failures = 0
        ordered = sorted(new_signals, key=lambda x: (x.timeframe, x.ticker, x.kind))
        for index, signal in enumerate(ordered, start=1):
            if telegram.send_signal(signal):
                state.mark_sent(signal.signal_id)
                sent += 1
            else:
                send_failures += 1
            if not args.dry_run and index % 5 == 0:
                state.save()

        if not args.dry_run:
            state.save()

        summary = {
            "universe": len(symbols),
            "downloaded": len(market_data),
            "download_failures": len(download_failures),
            "data_quality_skips": skipped,
            "scan_errors": len(scan_errors),
            "new_signals": len(new_signals),
            "sent": sent,
            "send_failures": send_failures,
        }
        write_heartbeat(heartbeat_path, status="ok", details=summary)

        if config.telegram.send_heartbeat:
            telegram.send(
                "\U00002705 Crypto scanners concluídos\n"
                f"Moedas: {summary['downloaded']}/{summary['universe']}\n"
                f"Novos sinais: {summary['new_signals']}\n"
                f"Alertas enviados: {sent}\n"
                f"Falhas download: {summary['download_failures']}\n"
                f"Bloqueadas por dados: {skipped}\n"
                f"Erros scan: {len(scan_errors)}"
            )
        print(json.dumps(summary, indent=2))
        return 0

    except Exception as exc:
        write_heartbeat(heartbeat_path, status="error", details={"error": str(exc)})
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not args.dry_run:
            telegram.send(f"\U0000274C Crypto scanner falhou\n{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
