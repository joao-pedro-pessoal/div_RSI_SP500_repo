from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .comp import CompSignal, detect
from .timeframes import build as build_timeframe
from .validation import blocking_issues, validate_ohlc

# O provider da OKX produz colunas minusculas; os detetores esperam-nas
# capitalizadas. A traducao fica na fronteira, como no scanner.py, para
# nenhum dos lados ter de conhecer a convencao do outro.
COLUMN_MAP = {"open": "Open", "high": "High", "low": "Low",
              "close": "Close", "volume": "Volume"}


@dataclass
class CompReport:
    signals: list[CompSignal] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    skipped: bool = False


class CompScanner:
    """
    Corre o COMP em cada timeframe configurado, a partir de barras 4h nativas.

    Mesma forma que o CryptoDivergenceScanner de proposito: quem souber ler
    um sabe ler o outro, e o main_comp.py e quase identico ao main_crypto.py.
    """

    def __init__(self, config):
        self.config = config

    def scan_symbol(self, symbol: str, bars_4h: pd.DataFrame,
                    timeframes: list[str] | None = None) -> CompReport:
        report = CompReport()
        v = self.config.validation

        issues = validate_ohlc(
            bars_4h,
            min_rows=v.min_rows,
            max_gap_bars=v.max_gap_bars,
            max_single_bar_move=v.max_single_bar_move,
        )
        if blocking_issues(issues):
            report.skipped = True
            report.errors[f"{symbol}:data"] = "dados bloqueados pela validacao"
            return report

        cfg = self.config.comp
        for timeframe in (timeframes or cfg.timeframes):
            try:
                bars = build_timeframe(bars_4h, timeframe)
                if bars.empty:
                    continue
                frame = bars.rename(columns=COLUMN_MAP)
                report.signals.extend(detect(symbol, timeframe, frame, cfg))
            except Exception as exc:            # nunca derrubar o scan todo
                report.errors[f"{symbol}:{timeframe}"] = f"{type(exc).__name__}: {exc}"
        return report
