#!/usr/bin/env python3
"""
Liquidity sweep scanner — top 100 by market cap, OKX USDT perpetuals.

Deteta varrimentos de stops: uma tendencia definida por pivots sucessivos,
seguida de uma vela que vai ate a ORIGEM dessa tendencia e fecha de volta
perto de onde abriu.

    python main_sweep.py --dry-run --symbols BTC-USDT-SWAP ETH-USDT-SWAP
    python main_sweep.py --dry-run
    python main_sweep.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from crypto_scanner.provider import BAR_1H, BAR_4H, ProviderConfig, fetch_many
from crypto_scanner.state import SignalState, write_heartbeat
from crypto_scanner.sweep import SweepParams
from crypto_scanner.sweep_scanner import SweepScanner
from crypto_scanner.telegram_client import TelegramClient
from crypto_scanner.universe import build_universe

DEFAULT_CONFIG = "config_sweep.yaml"


def load(path: str) -> dict:
    p = Path(path)
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--timeframe", action="append", dest="timeframes")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    cfg = load(args.config)
    scanner_cfg = cfg.get("scanner", {})
    data_cfg = cfg.get("data", {})
    universe_cfg = cfg.get("universe", {})

    timeframes = args.timeframes or scanner_cfg.get("timeframes", ["1h", "4h", "1D", "3D"])
    windows = scanner_cfg.get("alert_age_bars", {"1h": 25, "4h": 7, "1D": 2, "3D": 1})

    params = SweepParams(**{
        k: v for k, v in scanner_cfg.items()
        if k in SweepParams.__dataclass_fields__
    })

    state_file = cfg.get("state_file", "state/sweep_signals.json")
    heartbeat_file = cfg.get("heartbeat_file", "state/sweep_heartbeat.json")
    telegram = TelegramClient(dry_run=args.dry_run)

    try:
        if args.symbols:
            symbols = [s.upper() for s in args.symbols]
            print(f"[universe] {len(symbols)} symbols supplied explicitly")
        else:
            coins = build_universe(
                limit=universe_cfg.get("limit", 100),
                exclude_stablecoins=universe_cfg.get("exclude_stablecoins", True),
                exclude_wrapped=universe_cfg.get("exclude_wrapped", True),
                snapshot_dir=None,     # o bot de divergencias ja guarda o ranking
            )
            symbols = [c.exchange_symbol for c in coins]

        need_4h = any(t in ("4h", "1D", "3D") for t in timeframes)
        need_1h = "1h" in timeframes

        data_4h: dict = {}
        data_1h: dict = {}
        failures: list[str] = []

        if need_4h:
            print(f"[provider] 4h bars for {len(symbols)} symbols...")
            data_4h, f4 = fetch_many(symbols, ProviderConfig(
                bars=data_cfg.get("bars_4h", 3000), bar=BAR_4H,
                sleep_between=data_cfg.get("sleep_between", 0.12),
            ))
            failures.extend(f4)
            print(f"[provider] 4h: {len(data_4h)}/{len(symbols)}")

        if need_1h:
            print(f"[provider] 1h bars for {len(symbols)} symbols...")
            data_1h, f1 = fetch_many(symbols, ProviderConfig(
                bars=data_cfg.get("bars_1h", 400), bar=BAR_1H,
                sleep_between=data_cfg.get("sleep_between", 0.12),
            ))
            failures.extend(f1)
            print(f"[provider] 1h: {len(data_1h)}/{len(symbols)}")

        if not data_4h and not data_1h:
            raise RuntimeError("no market data downloaded")

        scanner = SweepScanner(params, windows)
        state = SignalState(Path(state_file))

        new_signals = []
        skipped: set[str] = set()
        errors: dict[str, str] = {}

        for symbol in symbols:
            report = scanner.scan_symbol(
                symbol, data_4h.get(symbol), data_1h.get(symbol), timeframes
            )
            skipped.update(report.skipped)
            errors.update(report.errors)
            for signal in report.signals:
                if not state.contains(signal.signal_id):
                    new_signals.append(signal)

        sent = 0
        send_failures = 0
        ordered = sorted(new_signals, key=lambda s: (s.timeframe, s.symbol, s.kind))
        for index, signal in enumerate(ordered, start=1):
            if telegram.send_sweep(signal):
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
            "downloaded_4h": len(data_4h),
            "downloaded_1h": len(data_1h),
            "download_failures": len(set(failures)),
            "data_quality_skips": len(skipped),
            "scan_errors": len(errors),
            "new_signals": len(new_signals),
            "sent": sent,
            "send_failures": send_failures,
        }
        write_heartbeat(Path(heartbeat_file), status="ok", details=summary)

        if cfg.get("telegram", {}).get("send_heartbeat", True):
            telegram.send(
                "\U00002705 Sweep scanner concluído\n"
                # max() e nao downloaded_4h: no workflow de 1h nao se
                # descarregam barras de 4h, e o heartbeat dizia "0/100".
                f"Moedas: {max(summary['downloaded_4h'], summary['downloaded_1h'])}"
                f"/{summary['universe']}\n"
                f"Timeframes: {', '.join(timeframes)}\n"
                f"Novos sinais: {summary['new_signals']}\n"
                f"Alertas enviados: {sent}\n"
                f"Falhas download: {summary['download_failures']}\n"
                f"Bloqueadas por dados: {len(skipped)}\n"
                f"Erros scan: {len(errors)}"
            )
        print(json.dumps(summary, indent=2))
        return 0

    except Exception as exc:
        write_heartbeat(Path(heartbeat_file), status="error", details={"error": str(exc)})
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not args.dry_run:
            telegram.send(f"\U0000274C Sweep scanner falhou\n{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
