from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trading_scanner.config import load_config
from trading_scanner.provider import YFinanceProvider
from trading_scanner.scanner import RSIDivergenceScanner
from trading_scanner.state import SignalState, write_heartbeat
from trading_scanner.telegram_client import TelegramClient
from trading_scanner.universe import fetch_sp500_universe


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="S&P 500 RSI divergence scanner")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--scanner", default="rsi_divergence", choices=["rsi_divergence"])
    parser.add_argument("--timeframe", choices=["1D", "3D", "1W"])
    parser.add_argument("--dry-run", action="store_true", help="print Telegram messages instead of sending")
    parser.add_argument("--tickers", nargs="*", help="optional ticker subset for local testing")
    return parser.parse_args()


def run() -> int:
    args = parse_args()
    config = load_config(args.config)
    state_path = ROOT / config.state_file
    heartbeat_path = ROOT / config.heartbeat_file
    telegram = TelegramClient(dry_run=args.dry_run)
    state = SignalState(state_path)

    try:
        if args.tickers:
            display_to_data = {ticker.upper(): ticker.upper().replace(".", "-") for ticker in args.tickers}
        else:
            universe = fetch_sp500_universe()
            display_to_data = {member.ticker: member.data_ticker for member in universe}

        provider = YFinanceProvider(
            auto_adjust=config.data.auto_adjust,
            batch_size=config.data.batch_size,
            retries=config.data.retries,
        )
        calendar = provider.fetch_reference_calendar(start=config.data.three_day_anchor)
        market_data, download_failures = provider.fetch_many(
            list(display_to_data.values()),
            start=config.data.start,
        )

        scanner = RSIDivergenceScanner(config)
        new_signals = []
        skipped = 0
        scan_errors: dict[str, str] = {}
        reverse = {data: display for display, data in display_to_data.items()}
        selected_timeframes = [args.timeframe] if args.timeframe else None

        for data_ticker, daily in market_data.items():
            display_ticker = reverse.get(data_ticker, data_ticker)
            report = scanner.scan_ticker(
                display_ticker,
                daily,
                reference_calendar=calendar,
                timeframes=selected_timeframes,
            )
            skipped += len(report.skipped)
            scan_errors.update(report.errors)
            for signal in report.signals:
                if not state.contains(signal.signal_id):
                    new_signals.append(signal)

        # GRAVA A CADA N ENVIOS, nao so no fim. Se o processo morrer a meio,
        # o que ja foi enviado fica marcado e nao e reenviado na proxima
        # execucao. Sem isto, uma falha a meio faz o scan seguinte repetir
        # tudo e voltar a bater no mesmo limite.
        sent = 0
        failed_sends = 0
        for index, signal in enumerate(sorted(new_signals, key=lambda x: (x.timeframe, x.ticker, x.kind)), start=1):
            if telegram.send_signal(signal):
                state.mark_sent(signal.signal_id)
                sent += 1
            else:
                failed_sends += 1
            if not args.dry_run and index % 5 == 0:
                state.save()

        if not args.dry_run:
            state.save()

        summary = {
            "universe": len(display_to_data),
            "downloaded": len(market_data),
            "download_failures": len(download_failures),
            "data_quality_skips": skipped,
            "scan_errors": len(scan_errors),
            "new_signals": len(new_signals),
            "sent": sent,
            "send_failures": failed_sends,
        }
        write_heartbeat(heartbeat_path, status="ok", details=summary)

        if config.telegram.send_heartbeat:
            telegram.send(
                "✅ S&P 500 scanners concluídos\n"
                f"Ações: {summary['downloaded']}/{summary['universe']}\n"
                f"Novos sinais: {len(new_signals)}\n"
                f"Alertas enviados: {sent}\n"
                f"Falhas download: {summary['download_failures']}\n"
                f"Bloqueadas por dados: {skipped}\n"
                f"Erros scan: {len(scan_errors)}"
            )

        # Partial data is visible in the heartbeat; fail only if coverage is poor
        # or the detector itself errored, so one bad Yahoo ticker cannot kill 500.
        if len(market_data) < max(1, int(len(display_to_data) * 0.95)) or scan_errors:
            return 2
        return 0
    except Exception as exc:
        write_heartbeat(heartbeat_path, status="failed", details={"error": str(exc)})
        try:
            telegram.send(f"❌ S&P 500 scanner falhou\n{type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)

