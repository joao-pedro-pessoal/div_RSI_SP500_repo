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
    # --- definicao da tendencia ---
    pivot_left: int = 2
    pivot_right: int = 2
    min_pivots: int = 3          # quantos minimos ascendentes exigimos
    max_trend_bars: int = 120    # tendencia mais antiga que isto e irrelevante

    # --- o varrimento ---
    # Quao fundo o pavio tem de ir. 0.0 = tem de tocar exatamente a origem;
    # valores positivos permitem parar um pouco acima; negativos exigem
    # furar abaixo. Fracao da amplitude da tendencia.
    origin_tolerance: float = 0.15
    max_bars_after_pivot: int = 8   # o varrimento tem de vir logo a seguir

    # --- a recuperacao ("volta a onde comecou") ---
    # CRITERIO PRINCIPAL: o pavio como fracao da amplitude TOTAL da vela.
    #
    # Substitui pavio/corpo como medida central. O racio pavio/corpo tem um
    # defeito: uma vela com corpo grande falha o racio mesmo com um pavio
    # enorme, e uma vela com corpo minusculo passa com um pavio irrelevante.
    # A fracao da amplitude mede "pavio dominante" de forma estavel.
    #
    # 0.50 = metade da vela e pavio.  0.65 = claramente dominante.
    min_wick_fraction: float = 0.65
    min_close_position: float = 0.55   # fecho no topo X da amplitude da vela
                                       # (implicado pela fracao, fica como rede)
    min_wick_ratio: float = 0.8        # pavio >= X vezes o corpo (secundario)
    require_close_above_origin: bool = True

    # --- qualidade ---
    min_trend_gain_pct: float = 1.5    # tendencia demasiado plana nao conta
    min_sweep_depth_pct: float = 0.3   # varrimento tem de ser visivel


@dataclass
class Sweep:
    symbol: str
    timeframe: str
    kind: SweepKind
    sweep_time: pd.Timestamp
    trend_start_time: pd.Timestamp
    trend_start_level: float
    n_pivots: int
    trend_gain_pct: float
    sweep_extreme: float          # o low (bull) ou high (bear) da vela
    sweep_close: float
    penetration_pct: float        # quanto passou da origem, em % do preco
    close_position: float         # 0 = fecho no minimo da vela, 1 = no maximo
    wick_fraction: float          # pavio / amplitude total da vela
    wick_ratio: float             # pavio / corpo
    bars_after_last_pivot: int

    @property
    def signal_id(self) -> str:
        """
        Identificador deterministico para deduplicacao.

        Inclui a origem da tendencia, nao so a vela de varrimento: a mesma
        vela pode qualificar contra tendencias diferentes se os pivots
        mudarem, e sao sinais distintos.
        """
        return "|".join([
            "sweep", self.symbol, self.timeframe, self.kind,
            self.trend_start_time.isoformat(), self.sweep_time.isoformat(),
        ])

    def to_row(self) -> dict:
        row = asdict(self)
        row["sweep_time"] = self.sweep_time.isoformat()
        row["trend_start_time"] = self.trend_start_time.isoformat()
        return row


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
    Avalia UMA vela (por omissao a ultima) como candidata a varrimento.

    Devolve None se nao qualifica. Testar so a ultima vela e deliberado:
    o sinal e para agir no fecho, nao para encontrar o padrao no historico.
    """
    # O minimo tem de vir do que a DETECAO precisa, nao de max_trend_bars.
    # Ligar os dois rejeitava series validas: com max_trend_bars=120 exigia
    # 60 barras, quando uma tendencia de 3 pivots cabe em bem menos.
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

    body = abs(c - o)
    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)

    # ---------------- bullish: pavio inferior varre minimos ascendentes ---
    pivot_lows = find_pivots(df["low"], p.pivot_left, p.pivot_right, "low")
    run = _ascending_run(pivot_lows, lows, p.min_pivots, p.max_trend_bars, idx)
    if run is not None:
        origin_i = run[0]
        origin = lows[origin_i]
        last_pivot_i = run[-1]
        gap = idx - last_pivot_i
        span = lows[last_pivot_i] - origin
        gain_pct = span / origin * 100.0

        if gap <= p.max_bars_after_pivot and gain_pct >= p.min_trend_gain_pct:
            # O pavio tem de descer ate perto da origem da tendencia --
            # e ai que estao acumulados os stops de TODA a subida.
            threshold = origin + span * p.origin_tolerance
            close_pos = (c - l) / rng
            wick = (min(o, c) - l)
            wick_fraction = wick / rng
            wick_ratio = wick / body if body > 1e-12 else float("inf")
            depth_pct = (lows[last_pivot_i] - l) / lows[last_pivot_i] * 100.0

            if (
                l <= threshold
                and wick_fraction >= p.min_wick_fraction
                and close_pos >= p.min_close_position
                and wick_ratio >= p.min_wick_ratio
                and depth_pct >= p.min_sweep_depth_pct
                and (not p.require_close_above_origin or c > origin)
            ):
                return Sweep(
                    symbol=symbol, timeframe=timeframe, kind="bullish_sweep",
                    sweep_time=df.index[idx], trend_start_time=df.index[origin_i],
                    trend_start_level=float(origin), n_pivots=len(run),
                    trend_gain_pct=float(gain_pct), sweep_extreme=l, sweep_close=c,
                    penetration_pct=float((origin - l) / origin * 100.0),
                    close_position=float(close_pos), wick_fraction=float(wick_fraction),
                    wick_ratio=float(min(wick_ratio, 999)),
                    bars_after_last_pivot=int(gap),
                )

    # ---------------- bearish: pavio superior varre maximos descendentes --
    pivot_highs = find_pivots(df["high"], p.pivot_left, p.pivot_right, "high")
    run = _descending_run(pivot_highs, highs, p.min_pivots, p.max_trend_bars, idx)
    if run is not None:
        origin_i = run[0]
        origin = highs[origin_i]
        last_pivot_i = run[-1]
        gap = idx - last_pivot_i
        span = origin - highs[last_pivot_i]
        gain_pct = span / origin * 100.0

        if gap <= p.max_bars_after_pivot and gain_pct >= p.min_trend_gain_pct:
            threshold = origin - span * p.origin_tolerance
            close_pos = (h - c) / rng
            wick = (h - max(o, c))
            wick_fraction = wick / rng
            wick_ratio = wick / body if body > 1e-12 else float("inf")
            depth_pct = (h - highs[last_pivot_i]) / highs[last_pivot_i] * 100.0

            if (
                h >= threshold
                and wick_fraction >= p.min_wick_fraction
                and close_pos >= p.min_close_position
                and wick_ratio >= p.min_wick_ratio
                and depth_pct >= p.min_sweep_depth_pct
                and (not p.require_close_above_origin or c < origin)
            ):
                return Sweep(
                    symbol=symbol, timeframe=timeframe, kind="bearish_sweep",
                    sweep_time=df.index[idx], trend_start_time=df.index[origin_i],
                    trend_start_level=float(origin), n_pivots=len(run),
                    trend_gain_pct=float(gain_pct), sweep_extreme=h, sweep_close=c,
                    penetration_pct=float((h - origin) / origin * 100.0),
                    close_position=float(close_pos), wick_fraction=float(wick_fraction),
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
