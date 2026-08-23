#!/usr/bin/env python3
"""
check_apis_ci.py — descobre QUAL endpoint bloqueia os IPs do GitHub Actions.

Corre isto como workflow manual. Testa cada origem isoladamente e continua
mesmo quando uma falha, para que uma unica execucao responda a tudo.

Nao levanta excecoes e sai sempre com 0: o objetivo e o relatorio, nao o
estado de saida.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

UA = "CryptoDivergenceScanner/1.0"
TIMEOUT = 25

# Cada entrada: (nome, url, o que confirma se funcionar)
ENDPOINTS = [
    (
        "CoinGecko  ranking",
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc&per_page=5&page=1",
        "universo por market cap",
    ),
    (
        "Bybit      instrumentos (api.bybit.com)",
        "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=5",
        "lista de perpetuos",
    ),
    (
        "Bybit      klines (api.bybit.com)",
        "https://api.bybit.com/v5/market/kline"
        "?category=linear&symbol=BTCUSDT&interval=240&limit=5",
        "PRECOS — o mais critico",
    ),
    (
        "Bybit      klines (api.bytick.com, espelho)",
        "https://api.bytick.com/v5/market/kline"
        "?category=linear&symbol=BTCUSDT&interval=240&limit=5",
        "alternativa se api.bybit.com bloquear",
    ),
    (
        "OKX        candles (alternativa)",
        "https://www.okx.com/api/v5/market/candles"
        "?instId=BTC-USDT-SWAP&bar=4H&limit=5",
        "outra exchange, se a Bybit bloquear",
    ),
    (
        "Binance    klines (alternativa)",
        "https://fapi.binance.com/fapi/v1/klines"
        "?symbol=BTCUSDT&interval=4h&limit=5",
        "outra exchange",
    ),
]


def probe(name: str, url: str, purpose: str) -> dict:
    resultado = {"name": name, "purpose": purpose, "ok": False}
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(400).decode("utf-8", errors="replace")
            resultado.update(ok=True, status=response.status, sample=body[:120])
    except urllib.error.HTTPError as exc:
        # O corpo de um 403 costuma dizer o motivo (regiao, bot, rate limit).
        try:
            detalhe = exc.read(400).decode("utf-8", errors="replace")
        except Exception:
            detalhe = ""
        resultado.update(status=exc.code, error=f"HTTP {exc.code}", sample=detalhe[:200])
    except (urllib.error.URLError, socket.timeout) as exc:
        resultado.update(status=None, error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:
        resultado.update(status=None, error=f"{type(exc).__name__}: {exc}")
    return resultado


def main() -> int:
    print("=" * 68)
    print("DIAGNOSTICO DE REDE — que origens respondem a partir deste runner")
    print("=" * 68)

    try:
        with urllib.request.urlopen(
            urllib.request.Request("https://api.ipify.org?format=json",
                                   headers={"User-Agent": UA}), timeout=15
        ) as r:
            print(f"IP de saida: {json.loads(r.read()).get('ip')}")
    except Exception as exc:
        print(f"IP de saida: desconhecido ({type(exc).__name__})")
    print()

    resultados = []
    for name, url, purpose in ENDPOINTS:
        r = probe(name, url, purpose)
        resultados.append(r)
        marca = "OK    " if r["ok"] else "FALHOU"
        status = r.get("status")
        print(f"{marca}  {name}")
        print(f"          {purpose}")
        if r["ok"]:
            print(f"          HTTP {status} · {r['sample'][:90]}")
        else:
            print(f"          {r.get('error')}")
            if r.get("sample"):
                print(f"          resposta: {r['sample'][:160]}")
        print()

    print("=" * 68)
    print("LEITURA")
    print("=" * 68)

    def estado(fragmento: str) -> bool:
        return any(r["ok"] for r in resultados if fragmento in r["name"])

    cg = estado("CoinGecko")
    bybit_k = any(r["ok"] for r in resultados if "klines (api.bybit.com)" in r["name"])
    bytick = any(r["ok"] for r in resultados if "bytick" in r["name"])
    okx = estado("OKX")
    binance = estado("Binance")

    if not cg:
        print("- CoinGecko bloqueada -> universo precisa de lista de reserva")
        print("  (mesma solucao que se usou para a Wikipedia)")
    else:
        print("- CoinGecko acessivel")

    if bybit_k:
        print("- Bybit acessivel -> precos OK, nao e preciso mudar de exchange")
    elif bytick:
        print("- Bybit bloqueada MAS o espelho api.bytick.com funciona")
        print("  -> muda-se so o dominio, o resto do codigo fica igual")
    else:
        print("- Bybit bloqueada e sem espelho.")
        alternativas = [n for n, ok in (("OKX", okx), ("Binance", binance)) if ok]
        if alternativas:
            print(f"  -> alternativas disponiveis: {', '.join(alternativas)}")
            print("  -> implica reescrever provider.py para essa exchange")
        else:
            print("  -> NENHUMA exchange acessivel a partir deste runner.")
            print("  -> o GitHub Actions nao serve; seria preciso outro sitio")
            print("     para correr (VPS, Raspberry Pi, PC ligado)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
