from __future__ import annotations

import io
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# A Wikipedia BLOQUEIA (HTTP 403) pedidos com o User-Agent por defeito do
# Python. A politica deles exige que clientes automaticos se identifiquem
# com um contacto. Da tua maquina pode passar; dos IPs partilhados do
# GitHub Actions o bloqueio e garantido.
#
# Poe aqui um contacto teu real -- e o que a politica da Wikipedia pede.
USER_AGENT = (
    "SP500DivergenceScanner/1.0 "
    "(https://github.com/joao-pedro-pessoal/div_RSI_SP500_repo)"
)


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    data_ticker: str


def to_yahoo_symbol(symbol: str) -> str:
    # Yahoo uses dashes for share classes such as BRK.B and BF.B.
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500_html(url: str = SP500_URL, timeout: int = 30) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


FALLBACK_FILE = Path(__file__).resolve().parent.parent / "data_sp500_tickers.csv"


def load_fallback_universe() -> list[UniverseMember]:
    """Lista guardada no repositorio, usada quando a Wikipedia falha."""
    if not FALLBACK_FILE.exists():
        raise RuntimeError(f"ficheiro de reserva em falta: {FALLBACK_FILE}")
    symbols = pd.read_csv(FALLBACK_FILE)["symbol"].astype(str).str.strip().tolist()
    if len(symbols) < 450:
        raise RuntimeError(f"lista de reserva pequena demais: {len(symbols)}")
    return [UniverseMember(s, to_yahoo_symbol(s)) for s in symbols]


def fetch_sp500_universe(url: str = SP500_URL, *, allow_fallback: bool = True) -> list[UniverseMember]:
    """
    Universo atual do S&P 500.

    Tenta a Wikipedia; se falhar, usa a lista guardada no repositorio.

    PORQUE A RESERVA EXISTE: a Wikipedia bloqueia IPs partilhados como os
    do GitHub Actions, e sem reserva um 403 derruba o scan inteiro. A lista
    guardada fica desatualizada devagar (o indice muda ~20 nomes por ano),
    o que e muito melhor que nao correr de todo.
    """
    try:
        tables = pd.read_html(io.StringIO(fetch_sp500_html(url)))
        if not tables or "Symbol" not in tables[0].columns:
            raise RuntimeError("could not find S&P 500 Symbol table")
        symbols = tables[0]["Symbol"].astype(str).str.strip().tolist()
        if len(symbols) < 450:
            raise RuntimeError(f"S&P 500 universe unexpectedly small: {len(symbols)}")
        return [UniverseMember(s, to_yahoo_symbol(s)) for s in symbols]
    except Exception as exc:
        if not allow_fallback:
            raise
        print(f"[universo] Wikipedia falhou ({type(exc).__name__}: {exc}); a usar lista de reserva")
        return load_fallback_universe()

