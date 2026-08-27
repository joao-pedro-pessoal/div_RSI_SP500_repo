"""
sweep.py — deteta varrimentos de liquidez ("stop hunts").

O PADRAO
  Numa tendencia de alta, os stops dos participantes acumulam-se por baixo
  de cada minimo ascendente. Uma vela desce ate ao INICIO da tendencia,
  varre todos esses stops de uma vez, e fecha de volta perto de onde abriu.

  Espelhado para tendencia de baixa: pavio superior varre os stops acima
  dos maximos descendentes.

PORQUE NAO SE DESENHAM LINHAS
  Uma linha de tendencia desenhada a mao depende de onde a ancoras: mover
  o ponto inicial duas velas faz o padrao aparecer ou desaparecer. Aqui a
  tendencia e definida por uma sequencia de PIVOTS sucessivamente mais
  altos (ou mais baixos), o que e verificavel sem ambiguidade.

O QUE ESTE DETETOR NAO PARTILHA COM O DE DIVERGENCIA
  Sinaliza no FECHO da vela de varrimento. Nao ha as `pivot_right` barras
  de atraso: os pivots que definem a tendencia sao anteriores e ja estao
  confirmados quando o varrimento acontece.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

import numpy as np
import pandas as pd

SweepKind = Literal["bullish_sweep", "bearish_sweep"]


@dataclass
class SweepParams:
    """
    Calibrado com casos REAIS marcados pelo Jhonny no TradingView, nao com
    dados sinteticos.

    A calibracao anterior media pavios de velas inventadas com 93% de pavio
    e produziu um limiar de 0.65 que rejeitava tudo o que era real: as
    velas de rejeicao medidas tinham 0.55 a 0.74.

    Casos reais observados (minimos):
        pavio 0.55 · fecho 0.55 · pavio/corpo 3.19 · prof 0.19 ATR · tend 0.19 ATR

    Os limiares ficam ~20% abaixo desses minimos. A margem existe porque
    quatro casos sao uma amostra pequena.
    """

    # --- estrutura ---
    pivot_left: int = 2
    pivot_right: int = 2
    # 2 e nao 3: TODOS os casos validados tinham exatamente 2 pivots. Exigir
    # 3 rejeitava-os a todos.
    min_pivots: int = 2
    max_trend_bars: int = 120
    max_bars_after_pivot: int = 20

    # --- limiares, em ATR onde faz sentido ---
    #
    # Percentagens fixas nao servem: 1.5% numa tendencia de 1h no BTC e
    # enorme, numa altcoin volatil e ruido. Medido num caso real: a
    # tendencia tinha 0.63% e era rejeitada por um limiar de 1.5%.
    atr_period: int = 14
    min_trend_atr: float = 0.12       # observado minimo: 0.19
    min_depth_atr: float = 0.10       # observado minimo: 0.19
    min_wick_fraction: float = 0.45   # observado minimo: 0.55
    min_close_position: float = 0.45  # observado minimo: 0.55
    min_wick_ratio: float = 0.8       # observado minimo: 3.19


@dataclass
class Sweep:
    symbol: str
    timeframe: str
    kind: SweepKind
    sweep_time: pd.Timestamp
    trend_start_time: pd.Timestamp
    trend_start_level: float      # origem da tendencia (informativo)
    swept_level: float            # o pivot que foi varrido — o alvo real
    n_pivots: int
    trend_atr: float              # amplitude da tendencia, em ATR
    sweep_extreme: float
    sweep_close: float
    depth_atr: float              # quanto passou do pivot varrido, em ATR
    origin_atr: float             # >0 = passou tambem a origem; <0 = ficou aquem
    close_position: float
    wick_fraction: float
    wick_ratio: float
    bars_after_last_pivot: int

    @property
    def signal_id(self) -> str:
        return "|".join([
            "sweep", self.symbol, self.timeframe, self.kind,
            self.trend_start_time.isoformat(), self.sweep_time.isoformat(),
        ])

    def to_row(self) -> dict:
        row = asdict(self)
        row["sweep_time"] = self.sweep_time.isoformat()
        row["trend_start_time"] = self.trend_start_time.isoformat()
        return row


def average_true_range(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().to_numpy()


def find_pivots(series: pd.Series, left: int, right: int,
                kind: Literal["low", "high"]) -> list[int]:
    """Pivot ao estilo TradingView: extremo na janela [i-left, i+right]."""
    values = series.to_numpy(dtype=float)
    n = len(values)
    out: list[int] = []
    for i in range(left, n - right):
        centre = values[i]
        lhs, rhs = values[i - left:i], values[i + 1:i + right + 1]
        if np.isnan(centre) or np.isnan(lhs).any() or np.isnan(rhs).any():
            continue
        if kind == "low":
            if (lhs >= centre).all() and (rhs > centre).all():
                out.append(i)
        else:
            if (lhs <= centre).all() and (rhs < centre).all():
                out.append(i)
    return out


def _ascending_run(indices: list[int], values: np.ndarray,
                   min_len: int, max_span: int, end_before: int) -> list[int] | None:
    """
    A sequencia mais longa de pivots consecutivos e crescentes que termina
    antes de `end_before`.

    Percorre-se de tras para a frente porque o que interessa e a tendencia
    ATUAL: uma sequencia crescente que terminou ha 80 barras nao e a
    tendencia em que o varrimento acontece.
    """
    usable = [i for i in indices if i < end_before]
    if len(usable) < min_len:
        return None

    run = [usable[-1]]
    for prev in reversed(usable[:-1]):
        if values[prev] < values[run[-1]] and (run[0] - prev) <= max_span:
            run.append(prev)
        else:
            break
    run.reverse()
    return run if len(run) >= min_len else None


def _descending_run(indices: list[int], values: np.ndarray,
                    min_len: int, max_span: int, end_before: int) -> list[int] | None:
    usable = [i for i in indices if i < end_before]
    if len(usable) < min_len:
        return None
    run = [usable[-1]]
    for prev in reversed(usable[:-1]):
        if values[prev] > values[run[-1]] and (run[0] - prev) <= max_span:
            run.append(prev)
        else:
            break
    run.reverse()
    return run if len(run) >= min_len else None


def detect_sweep(df: pd.DataFrame, symbol: str, timeframe: str,
                 p: SweepParams = SweepParams(),
                 bar: int = -1) -> Sweep | None:
    """
    Avalia UMA vela (por omissao a ultima) como varrimento.

    O ALVO E O ULTIMO PIVOT, NAO A ORIGEM
      A versao anterior exigia que o pavio voltasse a origem da tendencia.
      Medido em casos reais: funciona em estruturas curtas (a origem ficava
      a 0.2 ATR) e e impossivel em tendencias longas (a 4.5 ATR). Quanto
      mais a tendencia corre, mais inalcancavel fica a origem -- a definicao
      nao escala.

      Varrer o ULTIMO pivot funciona nos dois casos, e e ai que estao os
      stops mais recentes. A distancia a origem continua a ser reportada
      em `origin_atr`, para se poder filtrar depois se fizer falta.
    """
    needed = p.pivot_left + p.pivot_right + p.min_pivots * 2 + 6
    if len(df) < needed:
        return None

    idx = len(df) + bar if bar < 0 else bar
    if idx < p.pivot_left + p.pivot_right + p.min_pivots or idx >= len(df):
        return None

    o = float(df["open"].iloc[idx])
    h = float(df["high"].iloc[idx])
    l = float(df["low"].iloc[idx])
    c = float(df["close"].iloc[idx])
    rng = h - l
    if rng <= 0:
        return None

    atr = average_true_range(df, p.atr_period)[idx]
    if not np.isfinite(atr) or atr <= 0:
        return None

    body = abs(c - o)
    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)

    # ---------------- bullish -------------------------------------------
    pivot_lows = find_pivots(df["low"], p.pivot_left, p.pivot_right, "low")
    run = _ascending_run(pivot_lows, lows, p.min_pivots, p.max_trend_bars, idx)
    if run is not None:
        origin_i, last_i = run[0], run[-1]
        origin, swept = lows[origin_i], lows[last_i]
        gap = idx - last_i

        # Gatilho: furou o pivot e fechou de volta acima dele.
        if gap <= p.max_bars_after_pivot and l < swept and c > swept:
            wick = min(o, c) - l
            wick_fraction = wick / rng
            close_pos = (c - l) / rng
            wick_ratio = wick / body if body > 1e-12 else 999999.0
            depth_atr = (swept - l) / atr
            trend_atr = (swept - origin) / atr
            origin_atr = (origin - l) / atr

            if (
                wick_fraction >= p.min_wick_fraction
                and close_pos >= p.min_close_position
                and wick_ratio >= p.min_wick_ratio
                and depth_atr >= p.min_depth_atr
                and trend_atr >= p.min_trend_atr
            ):
                return Sweep(
                    symbol=symbol, timeframe=timeframe, kind="bullish_sweep",
                    sweep_time=df.index[idx], trend_start_time=df.index[origin_i],
                    trend_start_level=float(origin), swept_level=float(swept),
                    n_pivots=len(run), trend_atr=float(trend_atr),
                    sweep_extreme=l, sweep_close=c,
                    depth_atr=float(depth_atr), origin_atr=float(origin_atr),
                    close_position=float(close_pos),
                    wick_fraction=float(wick_fraction),
                    wick_ratio=float(min(wick_ratio, 999)),
                    bars_after_last_pivot=int(gap),
                )

    # ---------------- bearish -------------------------------------------
    pivot_highs = find_pivots(df["high"], p.pivot_left, p.pivot_right, "high")
    run = _descending_run(pivot_highs, highs, p.min_pivots, p.max_trend_bars, idx)
    if run is not None:
        origin_i, last_i = run[0], run[-1]
        origin, swept = highs[origin_i], highs[last_i]
        gap = idx - last_i

        if gap <= p.max_bars_after_pivot and h > swept and c < swept:
            wick = h - max(o, c)
            wick_fraction = wick / rng
            close_pos = (h - c) / rng
            wick_ratio = wick / body if body > 1e-12 else 999999.0
            depth_atr = (h - swept) / atr
            trend_atr = (origin - swept) / atr
            origin_atr = (h - origin) / atr

            if (
                wick_fraction >= p.min_wick_fraction
                and close_pos >= p.min_close_position
                and wick_ratio >= p.min_wick_ratio
                and depth_atr >= p.min_depth_atr
                and trend_atr >= p.min_trend_atr
            ):
                return Sweep(
                    symbol=symbol, timeframe=timeframe, kind="bearish_sweep",
                    sweep_time=df.index[idx], trend_start_time=df.index[origin_i],
                    trend_start_level=float(origin), swept_level=float(swept),
                    n_pivots=len(run), trend_atr=float(trend_atr),
                    sweep_extreme=h, sweep_close=c,
                    depth_atr=float(depth_atr), origin_atr=float(origin_atr),
                    close_position=float(close_pos),
                    wick_fraction=float(wick_fraction),
                    wick_ratio=float(min(wick_ratio, 999)),
                    bars_after_last_pivot=int(gap),
                )

    return None


def scan_recent(df: pd.DataFrame, symbol: str, timeframe: str,
                p: SweepParams = SweepParams(), window: int = 1) -> list[Sweep]:
    """
    Avalia as ultimas `window` velas.

    A janela existe pela mesma razao que no bot de divergencias: o scan
    corre uma vez por dia, mas uma vela de 1h fecha 24 vezes nesse periodo.
    Sem janela, perder-se-iam quase todos os varrimentos intradiarios.
    """
    out: list[Sweep] = []
    for offset in range(window):
        bar = len(df) - 1 - offset
        if bar < 0:
            break
        s = detect_sweep(df.iloc[:bar + 1], symbol, timeframe, p, bar=-1)
        if s is not None:
            out.append(s)
    return out


def scan_history(df: pd.DataFrame, symbol: str, timeframe: str,
                 p: SweepParams = SweepParams()) -> list[Sweep]:
    """Percorre o historico. Serve para calibrar e medir, nao para alertar."""
    out: list[Sweep] = []
    for i in range(30, len(df)):
        s = detect_sweep(df.iloc[:i + 1], symbol, timeframe, p, bar=-1)
        if s is not None:
            out.append(s)
    return out
