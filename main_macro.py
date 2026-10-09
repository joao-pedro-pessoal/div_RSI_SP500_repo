#!/usr/bin/env python3
"""
RSI divergences for gold, silver, S&P 500 and BTC on 1h, 4h, 1D, 3D and 1W.

    python main_macro.py --dry-run                         # print, send nothing
    python main_macro.py --dry-run --timeframe 1h --asset GOLD
    python main_macro.py                                   # send to Telegram
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import yaml

from crypto_scanner.config import ScannerConfig
from crypto_scanner.macro import (
    TF_ORDER,
    Asset,
    MacroConfig,
    fetch_asset,
    format_signal,
    scan_bars,
    source_note,
)
from crypto_scanner.state import SignalState, write_heartbeat
from crypto_scanner.telegram_client import TelegramClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config_macro.yaml")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    parser.add_argument("--timeframe", action="append", dest="timeframes",
                        help="restrict to one timeframe (repeatable)")
    parser.add_argument("--asset", action="append", dest="assets",
                        help="restrict to one asset by name, e.g. GOLD (repeatable)")
    return parser.parse_args()


def load_config(path: str | Path) -> tuple[MacroConfig, dict]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = raw.get("data", {}) or {}
    cfg = MacroConfig(
        scanner=ScannerConfig(**(raw.get("scanner") or {})),
        assets=[Asset(**a) for a in raw.get("assets", [])],
        **data,
    )
    if not cfg.assets:
        raise ValueError("config has no assets")
    unknown = set(cfg.scanner.timeframes) - set(TF_ORDER)
    if unknown:
        raise ValueError(f"unsupported timeframes: {sorted(unknown)}")
    return cfg, raw


def main() -> int:
    args = parse_args()
    cfg, raw = load_config(args.config)
    telegram_cfg = raw.get("telegram", {}) or {}
    state_path = Path(raw.get("state_file", "state/macro_signals.json"))
    heartbeat_path = Path(raw.get("heartbeat_file", "state/macro_heartbeat.json"))
    telegram = TelegramClient(dry_run=args.dry_run)

    timeframes = [tf for tf in TF_ORDER if tf in (args.timeframes or cfg.scanner.timeframes)]
    wanted = {a.upper() for a in args.assets} if args.assets else None
    assets = [a for a in cfg.assets if wanted is None or a.name.upper() in wanted]

    try:
        now = pd.Timestamp.now(tz="UTC")
        state = SignalState(state_path)
        new_signals = []
        errors: dict[str, str] = {}

        for asset in assets:
            try:
                frames = fetch_asset(asset, cfg, now)
            except Exception as exc:
                errors[asset.name] = f"{type(exc).__name__}: {exc}"
                print(f"[macro] {asset.name}: download failed: {errors[asset.name]}")
                continue
            for tf in timeframes:
                bars = frames.get(tf)
                if bars is None or bars.empty:
                    errors[f"{asset.name}:{tf}"] = "no closed bars"
                    continue
                print(f"[macro] {asset.name} {tf}: {len(bars)} bars, last {bars.index[-1]}")
                try:
                    for signal in scan_bars(asset, tf, bars, cfg.scanner):
                        if not state.contains(signal.signal_id):
                            new_signals.append((asset, signal))
                except Exception as exc:
                    errors[f"{asset.name}:{tf}"] = f"{type(exc).__name__}: {exc}"

        sent = failed = 0
        for asset, signal in new_signals:
            if telegram.send(format_signal(signal, asset, source_note(asset))):
                state.mark_sent(signal.signal_id)
                sent += 1
            else:
                failed += 1
        if not args.dry_run:
            state.save()

        summary = {
            "assets": len(assets),
            "timeframes": timeframes,
            "new_signals": len(new_signals),
            "sent": sent,
            "send_failures": failed,
            "errors": errors,
        }
        if not args.dry_run:
            write_heartbeat(heartbeat_path, status="ok" if not errors else "partial", details=summary)
        if errors:
            print(f"[macro] errors: {errors}")
            # One failed download is reported, but only once the whole asset
            # is missing -- a single empty timeframe is not worth a message.
            missing = [k for k in errors if ":" not in k]
            if missing and not args.dry_run and telegram_cfg.get("send_errors", True):
                telegram.send("⚠️ Macro scanner: no data for " + ", ".join(missing))
        if telegram_cfg.get("send_heartbeat", False):
            telegram.send(
                "✅ Macro scanner finished\n"
                f"Assets: {len(assets) - len([k for k in errors if ':' not in k])}/{len(assets)}\n"
                f"Timeframes: {', '.join(timeframes)}\n"
                f"New signals: {len(new_signals)}\n"
                f"Alerts sent: {sent}"
            )
        print(summary)
        return 0
    except Exception as exc:
        if not args.dry_run:
            write_heartbeat(heartbeat_path, status="error", details={"error": str(exc)})
        print(f"fatal: {type(exc).__name__}: {exc}", file=sys.stderr)
        if not args.dry_run:
            telegram.send(f"❌ Macro scanner failed\n{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
