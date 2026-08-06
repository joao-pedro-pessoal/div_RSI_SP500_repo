from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    data_ticker: str


def to_yahoo_symbol(symbol: str) -> str:
    # Yahoo uses dashes for share classes such as BRK.B and BF.B.
    return symbol.strip().upper().replace(".", "-")


def fetch_sp500_universe(url: str = SP500_URL) -> list[UniverseMember]:
    tables = pd.read_html(url)
    if not tables or "Symbol" not in tables[0].columns:
        raise RuntimeError("could not find S&P 500 Symbol table")
    symbols = tables[0]["Symbol"].astype(str).str.strip().tolist()
    if len(symbols) < 450:
        raise RuntimeError(f"S&P 500 universe unexpectedly small: {len(symbols)}")
    return [UniverseMember(s, to_yahoo_symbol(s)) for s in symbols]

