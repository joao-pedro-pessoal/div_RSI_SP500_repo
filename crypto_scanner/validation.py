"""
validation.py — data quality checks for crypto perpetual OHLCV.

DIFFERENT FROM THE EQUITY VERSION
  Perpetual contracts have no splits and no dividends, so the split-ratio
  detection that dominates equity validation is not applicable here.

  Crypto's characteristic data problems are different:
    - exchange outages leaving gaps in an otherwise 24/7 series
    - thin books producing wick spikes that revert immediately
    - newly listed contracts with too little history for RSI(14) + pivots

  Nothing is silently dropped for a suspicious price move. Crypto genuinely
  moves 40% in a day, and a filter aggressive enough to catch bad data would
  discard exactly the events most worth seeing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class DataIssue:
    code: str
    message: str
    blocking: bool


def validate_ohlc(
    frame: pd.DataFrame,
    *,
    min_rows: int = 120,
    max_gap_bars: int = 12,
    max_single_bar_move: float = 0.60,
) -> list[DataIssue]:
    issues: list[DataIssue] = []

    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        return [DataIssue("missing_columns", f"missing: {missing}", True)]

    if len(frame) < min_rows:
        issues.append(DataIssue(
            "insufficient_history",
            f"{len(frame)} bars, need {min_rows} (recently listed contract?)",
            True,
        ))
        return issues

    index = pd.DatetimeIndex(frame.index)
    if not index.is_monotonic_increasing:
        issues.append(DataIssue("unsorted_index", "timestamps not ordered", True))
    if index.has_duplicates:
        issues.append(DataIssue("duplicate_timestamps", "repeated timestamps", True))

    numeric = frame[list(REQUIRED_COLUMNS)]
    if not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        issues.append(DataIssue("non_finite", "NaN or infinity present", True))
        return issues
    if (numeric <= 0).any().any():
        issues.append(DataIssue("non_positive", "price <= 0", True))
        return issues

    high_ok = frame["high"] >= frame[["open", "close"]].max(axis=1) - 1e-12
    low_ok = frame["low"] <= frame[["open", "close"]].min(axis=1) + 1e-12
    if not bool((high_ok & low_ok & (frame["low"] <= frame["high"])).all()):
        issues.append(DataIssue("ohlc_incoherent", "impossible OHLC relationship", True))

    # 24/7 market: bar spacing should be perfectly regular. Gaps mean the
    # exchange was down or the contract was halted, and a gap shifts every
    # aggregated bar that follows it.
    if len(index) > 1:
        deltas = index.to_series().diff().dropna()
        step = deltas.median()
        if pd.notna(step) and step > pd.Timedelta(0):
            gaps = deltas[deltas > step * max_gap_bars]
            if len(gaps):
                issues.append(DataIssue(
                    "calendar_gaps",
                    f"{len(gaps)} gaps longer than {max_gap_bars} bars",
                    False,
                ))

    # Wick spike that reverts: bad tick, not a real move. Reported only.
    returns = frame["close"].pct_change().to_numpy()
    spikes = 0
    for i in range(1, len(returns) - 1):
        a, b = returns[i], returns[i + 1]
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        if abs(a) > max_single_bar_move and np.sign(a) != np.sign(b) and abs(b) > max_single_bar_move * 0.7:
            spikes += 1
    if spikes:
        issues.append(DataIssue(
            "reverting_spikes",
            f"{spikes} large moves that immediately reverse (suspect ticks)",
            False,
        ))

    return issues


def blocking_issues(issues: list[DataIssue]) -> list[DataIssue]:
    return [issue for issue in issues if issue.blocking]
