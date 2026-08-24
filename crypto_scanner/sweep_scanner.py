"""
sweep_scanner.py — orquestra a deteccao de varrimentos por timeframe.

DUAS RESOLUCOES DESCARREGADAS
  1h e nativo. 4h, 1D e 3D sao agregados a partir de barras de 4h, usando
  o mesmo modulo timeframes.py do bot de divergencias -- para que "3D"
  signifique exatamente a mesma coisa nos dois bots.

  A alternativa era pedir cada timeframe nativo a OKX (mais barato: 12
  pedidos por moeda em vez de ~34), mas isso introduzia uma segunda
  convencao de fronteiras de barra dentro do mesmo repositorio. Duas
  definicoes de "3D" a conviver e o tipo de inconsistencia que so se
  descobre meses depois, ao comparar sinais que deviam coincidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .sweep import Sweep, SweepParams, scan_recent
from .timeframes import build as build_timeframe
from .validation import blocking_issues, validate_ohlc

# Timeframes servidos por cada descarga
FROM_1H = ("1h",)
FROM_4H = ("4h", "1D", "3D")


@dataclass
class SweepReport:
    signals: list[Sweep] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


class SweepScanner:
    def __init__(self, params: SweepParams, windows: dict[str, int],
                 min_rows: int = 120, max_gap_bars: int = 12):
        self.params = params
        self.windows = windows
        self.min_rows = min_rows
        self.max_gap_bars = max_gap_bars

    def _window(self, timeframe: str) -> int:
        return int(self.windows.get(timeframe, 1))

    def scan_symbol(
        self,
        symbol: str,
        bars_4h: pd.DataFrame | None,
        bars_1h: pd.DataFrame | None,
        timeframes: list[str],
    ) -> SweepReport:
        report = SweepReport()

        for timeframe in timeframes:
            source = bars_1h if timeframe in FROM_1H else bars_4h
            if source is None or source.empty:
                continue

            issues = validate_ohlc(source, min_rows=self.min_rows,
                                   max_gap_bars=self.max_gap_bars)
            if blocking_issues(issues):
                if symbol not in report.skipped:
                    report.skipped.append(symbol)
                continue

            try:
                bars = source if timeframe == "1h" else build_timeframe(source, timeframe)
                if len(bars) < 40:
                    continue
                report.signals.extend(
                    scan_recent(bars, symbol, timeframe, self.params,
                                window=self._window(timeframe))
                )
            except Exception as exc:
                report.errors[f"{symbol}:{timeframe}"] = f"{type(exc).__name__}: {exc}"

        return report
