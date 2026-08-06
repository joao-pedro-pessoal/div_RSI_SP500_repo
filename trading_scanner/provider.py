from __future__ import annotations

import time
from collections.abc import Iterable

import pandas as pd


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    out = frame.copy()
    out.columns = [str(c).title() for c in out.columns]
    keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in out.columns]
    out = out[keep].dropna(how="all")
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out[~out.index.duplicated(keep="last")].sort_index()


class YFinanceProvider:
    """Free initial provider. yfinance is intentionally lazy-imported."""

    def __init__(self, *, auto_adjust: bool = True, batch_size: int = 75, retries: int = 2):
        self.auto_adjust = auto_adjust
        self.batch_size = batch_size
        self.retries = retries

    @staticmethod
    def _yf():
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance is not installed; run pip install -r requirements.txt") from exc
        return yf

    def fetch_many(self, symbols: list[str], *, start: str) -> tuple[dict[str, pd.DataFrame], list[str]]:
        yf = self._yf()
        result: dict[str, pd.DataFrame] = {}
        failures: list[str] = []

        for batch in _chunks(symbols, self.batch_size):
            raw = pd.DataFrame()
            for attempt in range(self.retries + 1):
                try:
                    raw = yf.download(
                        batch,
                        start=start,
                        interval="1d",
                        group_by="ticker",
                        auto_adjust=self.auto_adjust,
                        actions=True,
                        progress=False,
                        threads=True,
                    )
                    break
                except Exception:
                    if attempt >= self.retries:
                        break
                    time.sleep(1.5 * (attempt + 1))

            for symbol in batch:
                frame = self._extract_ticker(raw, symbol)
                if frame.empty:
                    frame = self._retry_single(yf, symbol, start)
                if frame.empty:
                    failures.append(symbol)
                else:
                    result[symbol] = frame
        return result, failures

    def fetch_reference_calendar(self, *, start: str = "2000-01-03") -> pd.DatetimeIndex:
        data, failures = self.fetch_many(["SPY"], start=start)
        if failures or "SPY" not in data:
            raise RuntimeError("could not download SPY reference calendar")
        return data["SPY"].index

    def _retry_single(self, yf, symbol: str, start: str) -> pd.DataFrame:
        for attempt in range(self.retries + 1):
            try:
                raw = yf.Ticker(symbol).history(
                    start=start,
                    interval="1d",
                    auto_adjust=self.auto_adjust,
                    actions=True,
                    raise_errors=True,
                )
                if not raw.empty:
                    return _normalise_frame(raw)
            except Exception:
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        return pd.DataFrame()

    @staticmethod
    def _extract_ticker(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if raw.empty:
            return pd.DataFrame()
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                level0 = set(map(str, raw.columns.get_level_values(0)))
                level1 = set(map(str, raw.columns.get_level_values(1)))
                if symbol in level0:
                    return _normalise_frame(raw[symbol])
                if symbol in level1:
                    return _normalise_frame(raw.xs(symbol, axis=1, level=1))
                return pd.DataFrame()
            return _normalise_frame(raw)
        except Exception:
            return pd.DataFrame()

