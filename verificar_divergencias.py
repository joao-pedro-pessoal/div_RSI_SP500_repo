#!/usr/bin/env python3
"""
Verificacao visual do detetor de divergencias RSI -- NAO envia Telegram.

Corre o mesmo detetor que os scanners usam sobre o historico inteiro de um
ativo e gera um grafico HTML (velas + RSI + linhas de divergencia) para
comparar a olho com o TradingView.

    python verificar_divergencias.py --demo
    python verificar_divergencias.py --ticker AAPL --timeframe 1D
    python verificar_divergencias.py --crypto BTC-USDT-SWAP --timeframe 4h --confirm-bars 0
    python verificar_divergencias.py --ticker MSFT --tipos regular hidden --barras 300
    python verificar_divergencias.py --csv dados.csv          (colunas Date,Open,High,Low,Close)

--tipos:        regular | hidden | todos | ou nomes exatos (bullish_hidden ...)
--confirm-bars: velas a direita do 2o pivot antes do alerta. Omisso = o valor
                do config (normalmente = pivot_right, igual ao Pine). 0 = alerta
                logo no fecho da vela que faz o novo low/high do RSI.
                Quando e menor que pivot_right, o grafico mostra os sinais
                antecipados E os confirmados, para comparar.
"""

from __future__ import annotations

import argparse
import html
import sys
import webbrowser
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from trading_scanner.indicators import wilder_rsi
from trading_scanner.models import ALL_SIGNAL_KINDS, DivergenceSignal
from trading_scanner.tradingview_divergence import find_tradingview_divergences

TYPE_GROUPS = {
    "regular": ["bullish_regular", "bearish_regular"],
    "hidden": ["bullish_hidden", "bearish_hidden"],
    "todos": list(ALL_SIGNAL_KINDS),
    "all": list(ALL_SIGNAL_KINDS),
}
LABELS = {
    "bullish_regular": "Bull",
    "bearish_regular": "Bear",
    "bullish_hidden": "H Bull",
    "bearish_hidden": "H Bear",
}
RULE_TEXT = {
    "bullish_regular": "preco LL + RSI HL",
    "bearish_regular": "preco HH + RSI LH",
    "bullish_hidden": "preco HL + RSI LL",
    "bearish_hidden": "preco LH + RSI HH",
}


# --------------------------------------------------------------------------- data

def load_stock(ticker: str, timeframe: str, start: str, anchor: str) -> pd.DataFrame:
    from trading_scanner.provider import YFinanceProvider
    from trading_scanner.timeframes import convert_timeframe

    data, failed = YFinanceProvider().fetch_many([ticker], start=start)
    if ticker not in data:
        raise SystemExit(f"sem dados para {ticker} (falhou: {failed})")
    daily = data[ticker]
    return convert_timeframe(daily, timeframe, reference_calendar=daily.index, three_day_anchor=anchor)


def load_crypto(inst_id: str, timeframe: str, bars_4h: int) -> pd.DataFrame:
    from crypto_scanner.provider import ProviderConfig, fetch_klines
    from crypto_scanner.scanner import COLUMN_MAP
    from crypto_scanner.timeframes import build

    raw = fetch_klines(inst_id, ProviderConfig(bars=bars_4h))
    if raw.empty:
        raise SystemExit(f"sem dados para {inst_id}")
    return build(raw, timeframe).rename(columns=COLUMN_MAP)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip().title() for c in df.columns]
    date_col = next(c for c in df.columns if c in ("Date", "Time", "Timestamp", "Datetime"))
    df[date_col] = pd.to_datetime(df[date_col])
    return df.set_index(date_col).sort_index()[["Open", "High", "Low", "Close"]]


def demo_data() -> pd.DataFrame:
    """Serie sintetica com os 4 tipos de divergencia, para testar sem internet."""
    rng = np.random.default_rng(3)
    legs = [
        # (target, bars)  -- a price path made of straight-ish legs
        (100, 1), (80, 20), (95, 12), (72, 10),   # LL with a weaker, slower drop -> bullish regular
        (100, 25), (88, 10), (112, 6),            # HH on a short thrust
        (118, 20), (104, 8),                      # slower HH -> bearish regular
        (116, 12), (98, 22), (108, 7),            # LH after a sharp rally... hidden bearish below
        (100, 6), (106, 5), (96, 25), (125, 10),  # HL after a fast dip -> hidden bullish
        (110, 12), (140, 18),
    ]
    prices = [legs[0][0]]
    for target, bars in legs[1:]:
        start = prices[-1]
        prices.extend(np.linspace(start, target, bars + 1)[1:])
    close = np.asarray(prices) * (1 + rng.normal(0, 0.004, len(prices)))
    spread = np.abs(rng.normal(0.006, 0.003, len(close))) * close
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + spread,
            "Low": np.minimum(open_, close) - spread,
            "Close": close,
        },
        index=pd.bdate_range("2025-01-01", periods=len(close)),
    )


# --------------------------------------------------------------------------- detection

def first_alert_per_leg(signals: list[DivergenceSignal]) -> list[DivergenceSignal]:
    """Early mode can produce one candidate per candle of the same leg; the
    scanner only sends the first (signal_id dedupe). Keep that one."""
    seen: set[str] = set()
    out = []
    for s in sorted(signals, key=lambda s: s.confirmation_position):
        if s.signal_id not in seen:
            seen.add(s.signal_id)
            out.append(s)
    return out


def run_detector(bars, rsi, scanner_cfg: dict, kinds, confirm_bars):
    common = dict(
        left=int(scanner_cfg.get("pivot_left", 5)),
        right=int(scanner_cfg.get("pivot_right", 5)),
        range_lower=int(scanner_cfg.get("min_distance_bars", 5)),
        range_upper=int(scanner_cfg.get("max_distance_bars", 60)),
        min_price_change_pct=float(scanner_cfg.get("min_price_change_pct", 0.0)),
        min_rsi_delta=float(scanner_cfg.get("min_rsi_delta", 0.0)),
        kinds=kinds,
    )
    confirmed = find_tradingview_divergences("X", "", bars, rsi, **common)
    early: list[DivergenceSignal] = []
    if confirm_bars is not None and confirm_bars < common["right"]:
        early = first_alert_per_leg(
            find_tradingview_divergences(
                "X", "", bars, rsi, confirm_bars=confirm_bars,
                alert_each_bar=bool(scanner_cfg.get("alert_each_bar", False)), **common,
            )
        )
    return confirmed, early


# --------------------------------------------------------------------------- report

def fmt_ts(ts: pd.Timestamp) -> str:
    ts = pd.Timestamp(ts)
    return ts.strftime("%Y-%m-%d %H:%M") if (ts.hour or ts.minute) else ts.strftime("%Y-%m-%d")


def fp(v: float) -> str:
    """Price with sensible precision: 84760.5 -> 84760.5, 0.006357 -> 0.006357."""
    av = abs(v)
    if av >= 1000:
        return f"{v:.1f}"
    if av >= 1:
        return f"{v:.3f}"
    return f"{v:.6g}"


def print_table(confirmed, early) -> None:
    lead = {}
    for s in confirmed:
        lead[(s.kind, s.first_pivot.position)] = s.confirmation_position
    rows = [("confirmado", s) for s in confirmed] + [("antecipado", s) for s in early]
    rows.sort(key=lambda r: r[1].confirmation_position)
    if not rows:
        print("Nenhuma divergencia encontrada com estes parametros.")
        return
    print(f"{'estado':<11} {'tipo':<16} {'regra':<19} {'1o pivot':<17} {'2o pivot':<17} "
          f"{'preco':>21} {'RSI':>13} {'alerta em':<17} nota")
    for state, s in rows:
        note = ""
        if state == "antecipado":
            later = lead.get((s.kind, s.first_pivot.position))
            if later is not None:
                note = f"{later - s.confirmation_position} velas antes do confirmado"
            else:
                note = "nunca chegou a confirmar"
        print(
            f"{state:<11} {s.kind:<16} {RULE_TEXT[s.kind]:<19} "
            f"{fmt_ts(s.first_pivot.timestamp):<17} {fmt_ts(s.second_pivot.timestamp):<17} "
            f"{fp(s.first_pivot.value):>10}->{fp(s.second_pivot.value):<10} "
            f"{s.first_rsi:>5.1f}->{s.second_rsi:<5.1f} "
            f"{fmt_ts(s.confirmation_time):<17} {note}"
        )


def build_html(title: str, bars: pd.DataFrame, rsi: pd.Series, confirmed, early, show: int) -> str:
    offset = max(0, len(bars) - show)
    view = bars.iloc[offset:]
    rsi_view = rsi.iloc[offset:]
    n = len(view)
    W, left_pad, right_pad = 1200, 10, 60
    price_h, rsi_h, gap, top = 420, 150, 30, 20
    step = (W - left_pad - right_pad) / max(n, 1)
    hi, lo = float(view["High"].max()), float(view["Low"].min())
    pad = (hi - lo) * 0.06 or 1.0
    hi, lo = hi + pad, lo - pad

    def x(pos: int) -> float:
        return left_pad + (pos - offset + 0.5) * step

    def yp(v: float) -> float:
        return top + (hi - v) / (hi - lo) * price_h

    rsi_top = top + price_h + gap

    def yr(v: float) -> float:
        return rsi_top + (100 - v) / 100 * rsi_h

    parts: list[str] = []
    # grid / labels
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        parts.append(f'<line class="grid" x1="{left_pad}" x2="{W - right_pad}" y1="{yp(v):.1f}" y2="{yp(v):.1f}"/>'
                     f'<text class="axis" x="{W - right_pad + 4}" y="{yp(v) + 4:.1f}">{fp(v)}</text>')
    for v in (30, 50, 70):
        parts.append(f'<line class="grid{" mid" if v == 50 else ""}" x1="{left_pad}" x2="{W - right_pad}" '
                     f'y1="{yr(v):.1f}" y2="{yr(v):.1f}"/><text class="axis" x="{W - right_pad + 4}" y="{yr(v) + 4:.1f}">{v}</text>')
    # candles
    body_w = max(1.0, step * 0.6)
    for i, (ts, row) in enumerate(view.iterrows()):
        pos = offset + i
        up = row["Close"] >= row["Open"]
        cls = "up" if up else "dn"
        cx = x(pos)
        y1, y2 = yp(max(row["Open"], row["Close"])), yp(min(row["Open"], row["Close"]))
        parts.append(
            f'<g class="{cls}"><title>{fmt_ts(ts)}  O {fp(row["Open"])} H {fp(row["High"])} '
            f'L {fp(row["Low"])} C {fp(row["Close"])}  RSI {rsi.iloc[pos]:.1f}</title>'
            f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{yp(row["High"]):.1f}" y2="{yp(row["Low"]):.1f}"/>'
            f'<rect x="{cx - body_w / 2:.1f}" y="{y1:.1f}" width="{body_w:.1f}" height="{max(1.0, y2 - y1):.1f}"/></g>'
        )
    # rsi line
    pts = " ".join(f"{x(offset + i):.1f},{yr(v):.1f}" for i, v in enumerate(rsi_view) if pd.notna(v))
    parts.append(f'<polyline class="rsi" points="{pts}"/>')

    def draw(s: DivergenceSignal, is_early: bool) -> None:
        if s.first_pivot.position < offset:
            return
        cls = ("bull" if s.bullish else "bear") + (" hidden" if s.hidden else "") + (" early" if is_early else "")
        x1, x2 = x(s.first_pivot.position), x(s.second_pivot.position)
        label = LABELS[s.kind] + (" ⏳" if is_early else "")
        tip = html.escape(
            f"{s.kind} ({RULE_TEXT[s.kind]}) {'ANTECIPADO' if is_early else 'confirmado'}\n"
            f"{fmt_ts(s.first_pivot.timestamp)} -> {fmt_ts(s.second_pivot.timestamp)}\n"
            f"preco {fp(s.first_pivot.value)} -> {fp(s.second_pivot.value)}\n"
            f"RSI {s.first_rsi:.1f} -> {s.second_rsi:.1f}\n"
            f"alerta: {fmt_ts(s.confirmation_time)}"
        )
        below = s.bullish
        extra = 13 if is_early else 0  # keep early and confirmed labels apart
        ly = yp(s.second_pivot.value) + ((16 + extra) if below else (-8 - extra))
        cx = x(s.confirmation_position)
        parts.append(
            f'<g class="div {cls}"><title>{tip}</title>'
            f'<line x1="{x1:.1f}" x2="{x2:.1f}" y1="{yp(s.first_pivot.value):.1f}" y2="{yp(s.second_pivot.value):.1f}"/>'
            f'<line x1="{x1:.1f}" x2="{x2:.1f}" y1="{yr(s.first_rsi):.1f}" y2="{yr(s.second_rsi):.1f}"/>'
            f'<text x="{x2:.1f}" y="{ly:.1f}" text-anchor="middle">{label}</text>'
            f'<line class="conf" x1="{cx:.1f}" x2="{cx:.1f}" y1="{rsi_top + rsi_h:.1f}" y2="{rsi_top + rsi_h + 8:.1f}"/>'
            f"</g>"
        )

    for s in confirmed:
        draw(s, False)
    for s in early:
        draw(s, True)

    height = rsi_top + rsi_h + 20
    counts = {k: sum(1 for s in confirmed if s.kind == k) for k in ALL_SIGNAL_KINDS}
    early_counts = {k: sum(1 for s in early if s.kind == k) for k in ALL_SIGNAL_KINDS}
    summary = " · ".join(
        f"{LABELS[k]}: {counts[k]}" + (f" (+{early_counts[k]} antecipados)" if early else "")
        for k in ALL_SIGNAL_KINDS if counts[k] or early_counts[k]
    ) or "nenhuma divergencia"
    summary = f"Historico completo ({len(bars)} velas): {summary}"
    return f"""<!doctype html>
<html lang="pt"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--grid:#e5e5ea;--up:#26a69a;--dn:#ef5350;--bull:#1b9e5a;--bear:#d6336c;--rsi:#7e57c2}}
@media (prefers-color-scheme:dark){{:root{{--bg:#131722;--fg:#e6e6e6;--muted:#9aa0a6;--grid:#2a2e39;--up:#26a69a;--dn:#ef5350;--bull:#4cd18a;--bear:#ff6b9a;--rsi:#b39ddb}}}}
body{{background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;margin:0;padding:16px}}
h1{{font-size:18px;margin:0 0 4px}} p{{margin:4px 0;color:var(--muted)}}
.wrap{{overflow-x:auto}} svg{{width:100%;min-width:700px;height:auto}}
.grid{{stroke:var(--grid)}} .grid.mid{{stroke-dasharray:3 3}} .axis{{fill:var(--muted);font-size:11px}}
.up line,.up rect{{stroke:var(--up);fill:var(--up)}} .dn line,.dn rect{{stroke:var(--dn);fill:var(--dn)}}
.rsi{{fill:none;stroke:var(--rsi);stroke-width:1.3}}
.div line{{stroke-width:2.2}} .div text{{font-size:11px;font-weight:600}}
.bull line{{stroke:var(--bull)}} .bull text{{fill:var(--bull)}} .bear line{{stroke:var(--bear)}} .bear text{{fill:var(--bear)}}
.hidden line{{stroke-dasharray:2 3}} .early line{{stroke-dasharray:7 4;opacity:.8}} .early text{{font-style:italic}}
.conf{{stroke-width:3!important}}
.legend span{{margin-right:14px}}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p>{html.escape(summary)}</p>
<p class="legend"><span>— linha cheia: regular</span><span>··· pontilhado: hidden</span><span>- - tracejado + ⏳: antecipado (confirm_bars)</span><span>traco sob o RSI: vela em que o alerta seria enviado</span></p>
<p>Passa o rato por cima das linhas/velas para ver os valores.</p>
<div class="wrap"><svg viewBox="0 0 {W} {height:.0f}" xmlns="http://www.w3.org/2000/svg">{''.join(parts)}</svg></div>
</body></html>"""


# --------------------------------------------------------------------------- main

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--ticker", help="acao (yfinance), ex. AAPL")
    src.add_argument("--crypto", help="perp OKX, ex. BTC-USDT-SWAP")
    src.add_argument("--csv", help="ficheiro CSV com Date,Open,High,Low,Close")
    src.add_argument("--demo", action="store_true", help="dados sinteticos, sem internet")
    p.add_argument("--timeframe", default=None, help="1D/3D/1W (acoes) ou 4h/1D/3D/1W (cripto)")
    p.add_argument("--config", default=None, help="yaml de onde ler os parametros do scanner")
    p.add_argument("--tipos", nargs="+", default=None, help="regular | hidden | todos | nomes exatos")
    p.add_argument("--confirm-bars", type=int, default=None)
    p.add_argument("--barras", type=int, default=250, help="quantas velas mostrar no grafico")
    p.add_argument("--out", default=None, help="ficheiro HTML de saida")
    p.add_argument("--no-open", action="store_true", help="nao abrir o browser")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config or ("config_crypto_4h.yaml" if args.crypto else "config.yaml")
    raw_cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) if Path(config_path).exists() else {}
    scanner_cfg = (raw_cfg or {}).get("scanner", {}) or {}

    if args.tipos:
        kinds: list[str] = []
        for t in args.tipos:
            kinds.extend(TYPE_GROUPS.get(t, [t]))
    else:
        kinds = scanner_cfg.get("divergence_types") or TYPE_GROUPS["regular"]
    kinds = list(dict.fromkeys(kinds))
    bad = set(kinds) - set(ALL_SIGNAL_KINDS)
    if bad:
        raise SystemExit(f"tipos desconhecidos: {sorted(bad)}")
    confirm_bars = args.confirm_bars if args.confirm_bars is not None else scanner_cfg.get("confirm_bars")

    if args.demo:
        name, tf = "DEMO", args.timeframe or "1D"
        bars = demo_data()
        if args.tipos is None:
            kinds = list(ALL_SIGNAL_KINDS)
        if confirm_bars is None:
            confirm_bars = 0
    elif args.ticker:
        name, tf = args.ticker.upper(), args.timeframe or "1D"
        data_cfg = (raw_cfg or {}).get("data", {}) or {}
        bars = load_stock(name, tf, data_cfg.get("start", "2020-01-01"), data_cfg.get("three_day_anchor", "2000-01-03"))
    elif args.crypto:
        name, tf = args.crypto.upper(), args.timeframe or "4h"
        need = {"4h": 600, "1D": 2400, "3D": 4200, "1W": 4200}.get(tf, 4200)
        bars = load_crypto(name, tf, need)
    else:
        name, tf = Path(args.csv).stem, args.timeframe or "?"
        bars = load_csv(args.csv)

    rsi = wilder_rsi(bars["Close"], int(scanner_cfg.get("rsi_period", 14)))
    confirmed, early = run_detector(bars, rsi, scanner_cfg, kinds, confirm_bars)

    right = int(scanner_cfg.get("pivot_right", 5))
    print(f"{name} {tf}: {len(bars)} velas, {bars.index[0]} -> {bars.index[-1]}")
    print(f"tipos: {', '.join(kinds)}")
    print(f"pivot_left={scanner_cfg.get('pivot_left', 5)} pivot_right={right} "
          f"confirm_bars={confirm_bars if confirm_bars is not None else right}")
    print()
    print_table(confirmed, early)

    out = Path(args.out or f"verificacao_{name.replace('/', '_')}_{tf}.html")
    title = f"{name} {tf} — divergencias RSI"
    out.write_text(build_html(title, bars, rsi, confirmed, early, args.barras), encoding="utf-8")
    print(f"\ngrafico: {out.resolve()}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
