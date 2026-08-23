#!/usr/bin/env python3
"""
check_apis.py — testa as ligacoes a Bybit e CoinGecko, uma de cada vez.

Corre isto ANTES do primeiro scan completo. Se algo estiver mal, este
script diz-te exatamente qual das quatro chamadas falhou, em vez de teres
de ler o log de um scan de 100 moedas.

    python check_apis.py
"""

from __future__ import annotations

import sys
import time

EXPECTED_BARS = 4200


def secao(titulo: str) -> None:
    print(f"\n{'=' * 60}\n{titulo}\n{'=' * 60}")


def main() -> int:
    falhas = 0
    df = None

    # ---------------------------------------------------------------
    secao("1/4  OKX: lista de swaps perpetuos")
    try:
        from crypto_scanner.provider import fetch_perpetual_symbols
        t0 = time.time()
        perps = fetch_perpetual_symbols()
        print(f"  OK  {len(perps)} swaps perpetuos USDT em {time.time() - t0:.1f}s")
        for s in ("BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"):
            marca = "OK " if s in perps else "??? "
            print(f"  {marca} {s}")
        if len(perps) < 100:
            print("  AVISO: poucos simbolos. O filtro ctType/settleCcy pode estar errado.")
            falhas += 1
    except Exception as exc:
        print(f"  FALHOU: {type(exc).__name__}: {exc}")
        print("  -> problema na OKX ou no filtro de instrumentos")
        falhas += 1

    # ---------------------------------------------------------------
    secao("2/4  OKX: velas de 4h (o teste mais importante)")
    try:
        from crypto_scanner.provider import fetch_klines, ProviderConfig
        t0 = time.time()
        df = fetch_klines("BTC-USDT-SWAP", ProviderConfig(bars=EXPECTED_BARS))
        dt = time.time() - t0
        print(f"  {len(df)} barras em {dt:.1f}s")
        if df.empty:
            print("  FALHOU: nenhuma vela devolvida")
            falhas += 1
        else:
            print(f"  primeira : {df.index[0]}")
            print(f"  ultima   : {df.index[-1]}")
            print(f"  fecho    : ${df['close'].iloc[-1]:,.2f}")

            ordenado = df.index.is_monotonic_increasing
            print(f"  ordem cronologica crescente: {ordenado}")
            if not ordenado:
                print("  FALHOU: a OKX devolve newest-first; a inversao nao funcionou")
                falhas += 1

            # espacamento de 4h sem buracos: valida a paginacao
            deltas = df.index.to_series().diff().dropna()
            regular = (deltas == deltas.median()).mean()
            print(f"  espacamento regular: {regular * 100:.1f}% das barras")
            if regular < 0.98:
                print("  AVISO: buracos no espacamento -> paginacao suspeita")
                falhas += 1

            if len(df) < EXPECTED_BARS * 0.9:
                print(f"  AVISO: esperadas ~{EXPECTED_BARS}, vieram {len(df)}")
                print("  -> a paginacao com 'after' pode estar a parar cedo demais")
                falhas += 1

            if df.index.has_duplicates:
                print("  FALHOU: timestamps duplicados -> paginacao repete velas")
                falhas += 1
    except Exception as exc:
        print(f"  FALHOU: {type(exc).__name__}: {exc}")
        falhas += 1

    # ---------------------------------------------------------------
    secao("3/4  Agregacao de timeframes")
    if df is None or df.empty:
        print("  SALTADO: o passo 2 nao devolveu velas")
    else:
      try:
        from crypto_scanner.timeframes import build
        for tf in ("4h", "1D", "3D", "1W"):
            b = build(df, tf)
            minimo = 34
            estado = "OK" if len(b) >= minimo + 30 else "JUSTO" if len(b) >= minimo else "POUCO"
            print(f"  {tf:3}: {len(b):5} barras  {estado}")
            if len(b) < minimo:
                falhas += 1
      except Exception as exc:
        print(f"  FALHOU: {type(exc).__name__}: {exc}")
        falhas += 1

    # ---------------------------------------------------------------
    secao("4/4  CoinGecko: ranking e exclusoes")
    try:
        from crypto_scanner.universe import build_universe
        t0 = time.time()
        coins = build_universe(limit=100, snapshot_dir=None)
        print(f"  {len(coins)} moedas em {time.time() - t0:.1f}s")
        if len(coins) < 80:
            print("  AVISO: menos de 80 moedas. O mapeamento simbolo->perpetuo")
            print("  pode estar a falhar mais do que devia.")
            falhas += 1
        print("\n  primeiras 12:")
        for c in coins[:12]:
            print(f"    {c.rank:3}. {c.symbol:<8} {c.exchange_symbol:<14} {c.name}")

        simbolos = {c.symbol for c in coins}
        maus = simbolos & {"USDT", "USDC", "DAI", "WBTC", "STETH", "WETH"}
        if maus:
            print(f"\n  FALHOU: stablecoins/wrapped nao excluidos: {maus}")
            falhas += 1
        else:
            print("\n  OK  sem stablecoins nem tokens wrapped")
    except Exception as exc:
        print(f"  FALHOU: {type(exc).__name__}: {exc}")
        falhas += 1

    # ---------------------------------------------------------------
    secao("RESULTADO")
    if falhas == 0:
        print("  Tudo OK. Podes correr:  python main.py --dry-run")
        return 0
    print(f"  {falhas} problema(s). Ve acima qual das chamadas falhou.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
