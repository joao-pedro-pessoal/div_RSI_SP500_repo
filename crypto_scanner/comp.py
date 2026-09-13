"""
COMP — compressao estrutural e rompimentos. Porta do indicador Pine v8.

O QUE ISTO DETETA
  Uma trendline ancorada em dois pivos, validada pelo CORPO das velas (os
  pavios atravessam a vontade), e zonas horizontais formadas por clusters de
  pivos ao mesmo preco. Quando duas dessas estruturas convergem ha
  compressao. O sinal e o FECHO fora de uma delas.

A DECISAO QUE GOVERNA O FICHEIRO TODO
  No Pine a linha morre no instante em que um corpo a ultrapassa. Procurar a
  estrutura na propria barra do rompimento nao devolve nada -- ela acabou de
  ser invalidada. Por isso tudo aqui e calculado ATE A BARRA N-1, e so
  depois se testa se a barra N fechou fora. Sem isto o scanner nao produz
  sinal nenhum e o erro e silencioso.

DIFERENCAS ASSUMIDAS FACE AO PINE
  - O Pine mantem estado barra a barra (`var`) e redesenha em barstate.islast.
    Aqui calcula-se de uma vez sobre o historico. As estruturas encontradas
    sao as mesmas; o que nao se reproduz e a memoria de qual linha estava
    desenhada ha 30 barras.
  - Nao ha sweeps: ja existe um scanner de varrimentos no repositorio.
  - `upBroken`/`resBroken` nao existem: cada execucao decide de novo. A
    deduplicacao por signal_id e que evita repetir o mesmo alerta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Structure:
    """Uma trendline aceite. `b1`/`p1` sao a ancora, em posicao e preco."""
    b1: int
    p1: float
    slope: float
    touches: int
    span: int
    last_touch: int
    score: float

    def value_at(self, position: int) -> float:
        return self.p1 + self.slope * (position - self.b1)


@dataclass(frozen=True)
class Zone:
    top: float
    bottom: float
    left: int
    tests: int


@dataclass(frozen=True)
class CompSignal:
    ticker: str
    timeframe: str
    kind: str            # "breakout_up" | "breakout_down"
    source: str          # "trendline" | "zone"
    level: float
    close: float
    bar_time: pd.Timestamp
    in_compression: bool
    compression_type: str
    apex_bars: Optional[float]
    touches: int
    span_bars: int

    @property
    def signal_id(self) -> str:
        return "|".join([
            self.ticker, self.timeframe, self.kind, self.source,
            self.bar_time.isoformat(),
        ])


def wilder_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR com suavizacao de Wilder.

    Wilder e nao `ewm(adjust=False)` pela mesma razao que no RSI: a semente
    e uma media simples das primeiras `period` barras, e a diferenca
    propaga-se por centenas de barras. Toda a calibracao do COMP esta em
    multiplos de ATR, por isso um ATR diferente desloca tudo em conjunto.
    """
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    tr = tr.dropna()
    if len(tr) < period:
        return pd.Series(index=frame.index, dtype=float)
    out = [float("nan")] * len(tr)
    seed = float(tr.iloc[:period].mean())
    out[period - 1] = seed
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + float(tr.iloc[i])) / period
    return pd.Series(out, index=tr.index).reindex(frame.index)


def _pivot_positions(values: np.ndarray, left: int, right: int, kind: str) -> list[int]:
    """
    Pivos estritos: extremos unicos na janela. Empates sao rejeitados, como
    no `find_pivots` das divergencias -- um topo duplo nao e uma ancora.
    """
    out: list[int] = []
    for i in range(left, len(values) - right):
        window = values[i - left:i + right + 1]
        centre = values[i]
        if np.isnan(window).any():
            continue
        extreme = window.min() if kind == "low" else window.max()
        if centre == extreme and int((window == centre).sum()) == 1:
            out.append(i)
    return out


def _scan_line(o, c, ext, b1, p1, slope, direction, end, tol_touch, tol_body):
    """
    Percorre a linha de `b1` ate `end`. Devolve (rompida, toques, ultimo toque,
    gap medio).

    Vectorizado porque e o unico sitio quente: com 12 pivos por lado sao ~260
    varrimentos por simbolo e por timeframe.
    """
    idx = np.arange(b1, end + 1)
    level = p1 + slope * (idx - b1)
    oo = o[b1:end + 1]
    cc = c[b1:end + 1]
    ee = ext[b1:end + 1]

    body = np.maximum(oo, cc) if direction > 0 else np.minimum(oo, cc)
    violation = (body > level + tol_body) if direction > 0 else (body < level - tol_body)
    if violation.any():
        return True, 0, -1, 999.0

    gap = float(np.mean(np.abs(ee - level)))
    dist = np.minimum(np.abs(ee - level), np.minimum(np.abs(oo - level), np.abs(cc - level)))
    return False, dist, idx, gap


def _count_touches(dist: np.ndarray, idx: np.ndarray, tol: float, min_sep: int):
    """Toques separados no tempo: tres velas seguidas nao sao tres toques."""
    hits = idx[dist <= tol]
    touches = 0
    last = -(10 ** 9)
    for bar in hits:
        if bar - last >= min_sep:
            touches += 1
            last = int(bar)
    return touches, last


def _consolidation_start(frame: pd.DataFrame, atr: float, end: int, cfg) -> Optional[int]:
    """
    Onde comeca a consolidacao: a ultima vela de impulso com espaco suficiente
    atras dela. As linhas preferem ancorar depois do impulso, senao ancoram no
    extremo da propria vela que o causou.
    """
    high = frame["High"].to_numpy(dtype=float)[:end + 1]
    low = frame["Low"].to_numpy(dtype=float)[:end + 1]
    impulses = np.where((high - low) >= cfg.impulse_atr * atr)[0]
    if impulses.size == 0:
        return None
    usable = impulses[end - impulses >= cfg.min_span]
    if usable.size:
        return int(usable[-1])
    return int(impulses[-2]) if impulses.size >= 2 else None


def _best_line(frame, pivot_positions, direction, end, atr, cfg) -> Optional[Structure]:
    """
    A melhor trendline de um lado. Pontuacao = toques x comprimento^peso,
    penalizada pela distancia media a estrutura e por ancorar fora da janela
    da consolidacao.
    """
    if len(pivot_positions) < 2 or atr <= 0:
        return None

    o = frame["Open"].to_numpy(dtype=float)
    c = frame["Close"].to_numpy(dtype=float)
    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    ext = high if direction > 0 else low

    # Ancoras possiveis: ponta do pavio e extremo do corpo.
    wick = {p: (high[p] if direction > 0 else low[p]) for p in pivot_positions}
    body = {p: (max(o[p], c[p]) if direction > 0 else min(o[p], c[p])) for p in pivot_positions}
    variants = []
    if cfg.anchor in ("wick", "both"):
        variants.append(wick)
    if cfg.anchor in ("body", "both"):
        variants.append(body)

    consol = _consolidation_start(frame, atr, end, cfg)
    min_delta = atr * 0.10
    best: Optional[Structure] = None
    best_score = 0.0

    for i, b1 in enumerate(pivot_positions[:-1]):
        if end - b1 > cfg.max_scan:
            continue
        in_window = (
            not cfg.anchor_after_impulse
            or consol is None
            or (consol < b1 <= consol + cfg.anchor_window)
        )
        anchor_mul = 1.0 if in_window else cfg.anchor_penalty

        for src1 in variants:
            p1 = src1[b1]
            if src1 is body and abs(body[b1] - wick[b1]) < min_delta:
                continue
            for b2 in pivot_positions[i + 1:]:
                if b2 > end or b2 - b1 < cfg.min_span:
                    continue
                for src2 in variants:
                    p2 = src2[b2]
                    if src2 is body and abs(body[b2] - wick[b2]) < min_delta:
                        continue
                    slope = (p2 - p1) / (b2 - b1)
                    if abs(slope) > cfg.max_slope_atr * atr:
                        continue

                    span = end - b1
                    if span < cfg.min_span or span > cfg.max_scan:
                        continue
                    grow = 1.0 + cfg.tol_span_k * span / 100.0
                    tol_touch = cfg.tol_atr * atr * grow
                    tol_body = cfg.body_tol_atr * atr * grow

                    broken, dist, idx, gap = _scan_line(
                        o, c, ext, b1, p1, slope, direction, end, tol_touch, tol_body)
                    if broken:
                        continue
                    touches, last = _count_touches(dist, idx, tol_touch, cfg.min_touch_sep)
                    if touches < cfg.min_touches:
                        continue
                    if end - last > cfg.relevance_bars:
                        continue

                    gap_atr = gap / atr if atr else 999.0
                    score = (touches * (max(span, 1) ** cfg.span_exp)
                             / (1.0 + cfg.fit_k * gap_atr) * anchor_mul)
                    if score > best_score:
                        best_score = score
                        best = Structure(b1, p1, slope, touches, span, last, score)
    return best


def _zones(frame, pivots, end, atr, cfg) -> tuple[Optional[Zone], Optional[Zone]]:
    """
    Clusters horizontais em duas passagens: a primeira acha o centro de massa
    a volta da ancora, a segunda conta os testes a volta desse centro. Tira a
    dependencia de qual pivo calhou ser testado primeiro.

    Topos e fundos entram no mesmo conjunto: um nivel e um nivel. E o que
    torna visivel o efeito de flip -- um fundo antigo a reforcar uma
    resistencia actual.
    """
    if atr <= 0 or len(pivots) < cfg.zone_min_touches:
        return None, None

    price_now = float(frame["Close"].to_numpy(dtype=float)[end])
    radius = cfg.zone_tol_atr * atr / 2.0
    levels = np.array([p[1] for p in pivots], dtype=float)
    bars = np.array([p[0] for p in pivots], dtype=int)

    def best_side(side: int) -> Optional[Zone]:
        chosen: Optional[Zone] = None
        best_count, best_right = 0, -(10 ** 9)
        for anchor in levels:
            near = np.abs(levels - anchor) <= radius
            if not near.any():
                continue
            centre = float(levels[near].mean())
            group = np.abs(levels - centre) <= radius
            if not group.any():
                continue
            order = np.argsort(bars[group])
            gb, gl = bars[group][order], levels[group][order]
            count, last = 0, -(10 ** 9)
            kept = []
            for bar, lvl in zip(gb, gl):
                if bar - last >= cfg.zone_min_sep:
                    count += 1
                    last = int(bar)
                    kept.append(lvl)
            if count < cfg.zone_min_touches:
                continue
            side_ok = centre > price_now if side > 0 else centre < price_now
            if not side_ok:
                continue
            right = int(gb[-1])
            if count > best_count or (count == best_count and right > best_right):
                mid = (max(kept) + min(kept)) / 2.0
                half = max((max(kept) - min(kept)) / 2.0, cfg.zone_tol_atr * atr * 0.15)
                best_count, best_right = count, right
                chosen = Zone(mid + half, mid - half, max(int(gb[0]), end - cfg.max_scan), count)
        return chosen

    return best_side(1), best_side(-1)


def _compression(up: Optional[Structure], dn: Optional[Structure],
                 res: Optional[Zone], sup: Optional[Zone], end: int, cfg):
    """A, B e C do indicador. Devolve (tipo, barras ate ao apex)."""
    if up and dn:
        gap = up.value_at(end) - dn.value_at(end)
        rate = dn.slope - up.slope
        if gap > 0 and rate > 0:
            apex = gap / rate
            if apex <= cfg.max_apex:
                return "TL x TL", apex
    if dn and res and dn.slope > 0 and dn.value_at(end) < res.bottom:
        apex = (res.bottom - dn.value_at(end)) / dn.slope
        if apex <= cfg.max_apex:
            return "TL sobe x Res", apex
    if up and sup and up.slope < 0 and up.value_at(end) > sup.top:
        apex = (up.value_at(end) - sup.top) / (-up.slope)
        if apex <= cfg.max_apex:
            return "TL desce x Sup", apex
    return "", None


def detect(ticker: str, timeframe: str, frame: pd.DataFrame, cfg) -> list[CompSignal]:
    """
    Sinais na ULTIMA barra fechada do `frame`.

    O `frame` ja vem sem a barra em formacao (ver timeframes.build), portanto
    a ultima linha e uma vela fechada.
    """
    needed = cfg.pivot_left + cfg.pivot_right + cfg.min_span + 30
    if len(frame) < needed:
        return []

    atr_series = wilder_atr(frame, cfg.atr_period)
    atr = float(atr_series.iloc[-1]) if not atr_series.empty else float("nan")
    if not np.isfinite(atr) or atr <= 0:
        return []

    n = len(frame) - 1          # barra do rompimento
    end = n - 1                 # estruturas validas ATE aqui -- ver cabecalho
    if end < needed:
        return []

    high = frame["High"].to_numpy(dtype=float)
    low = frame["Low"].to_numpy(dtype=float)
    close = frame["Close"].to_numpy(dtype=float)

    # Pivos confirmados a data de `end`: exigem pivot_right barras a direita,
    # por isso o ultimo pivo possivel esta em end - pivot_right.
    highs = [p for p in _pivot_positions(high, cfg.pivot_left, cfg.pivot_right, "high")
             if p <= end - cfg.pivot_right and end - p <= cfg.max_scan]
    lows = [p for p in _pivot_positions(low, cfg.pivot_left, cfg.pivot_right, "low")
            if p <= end - cfg.pivot_right and end - p <= cfg.max_scan]
    highs = highs[-cfg.max_pivots:]
    lows = lows[-cfg.max_pivots:]

    up = _best_line(frame, highs, 1, end, atr, cfg)
    dn = _best_line(frame, lows, -1, end, atr, cfg)

    # Zonas: topos e fundos juntos, so os que tiveram REACAO depois do teste.
    reacted = []
    for pos in highs:
        after = low[pos + 1:pos + 1 + cfg.pivot_right]
        if after.size and (high[pos] - after.min()) >= cfg.zone_react_atr * atr:
            reacted.append((pos, high[pos]))
    for pos in lows:
        after = high[pos + 1:pos + 1 + cfg.pivot_right]
        if after.size and (after.max() - low[pos]) >= cfg.zone_react_atr * atr:
            reacted.append((pos, low[pos]))
    reacted = sorted(reacted)[-cfg.zone_max_pivots:]
    res, sup = _zones(frame, reacted, end, atr, cfg)

    comp_type, apex = _compression(up, dn, res, sup, end, cfg)
    in_comp = bool(comp_type)

    buf = cfg.break_buffer_atr * atr
    allow_tl = "trendline" in cfg.signal_sources
    allow_zone = "zone" in cfg.signal_sources
    signals: list[CompSignal] = []

    def emit(kind, source, level, touches, span):
        # A compressao deixou de ser porteiro e passou a qualificador: um
        # rompimento de trendline limpo conta mesmo sem ela, se a config
        # deixar. E a mesma escolha feita no Pine v8.
        if not in_comp:
            if source == "trendline" and not allow_tl:
                return
            if source == "zone" and not allow_zone:
                return
        signals.append(CompSignal(
            ticker=ticker, timeframe=timeframe, kind=kind, source=source,
            level=float(level), close=float(close[n]),
            bar_time=frame.index[n], in_compression=in_comp,
            compression_type=comp_type, apex_bars=apex,
            touches=touches, span_bars=span,
        ))

    if up is not None:
        lvl_now, lvl_prev = up.value_at(n), up.value_at(end)
        if close[n] > lvl_now + buf and close[end] <= lvl_prev + buf:
            emit("breakout_up", "trendline", lvl_now, up.touches, up.span)
    if dn is not None:
        lvl_now, lvl_prev = dn.value_at(n), dn.value_at(end)
        if close[n] < lvl_now - buf and close[end] >= lvl_prev - buf:
            emit("breakout_down", "trendline", lvl_now, dn.touches, dn.span)
    if res is not None and close[n] > res.top + buf and close[end] <= res.top + buf:
        emit("breakout_up", "zone", res.top, res.tests, end - res.left)
    if sup is not None and close[n] < sup.bottom - buf and close[end] >= sup.bottom - buf:
        emit("breakout_down", "zone", sup.bottom, sup.tests, end - sup.left)

    return signals
