from __future__ import annotations

import pandas as pd

from .models import Pivot


def find_pivots(
    series: pd.Series,
    kind: str,
    left: int = 3,
    right: int = 3,
) -> list[Pivot]:
    """Return strict, fully confirmed price pivots.

    A pivot needs `left` older and `right` newer bars. Equal highs/lows inside
    the comparison window are rejected to avoid ambiguous flat pivots.
    """
    if kind not in {"low", "high"}:
        raise ValueError("kind must be 'low' or 'high'")
    if left < 1 or right < 1:
        raise ValueError("left/right must be >= 1")

    values = pd.to_numeric(series, errors="coerce")
    pivots: list[Pivot] = []
    for i in range(left, len(values) - right):
        center = values.iloc[i]
        window = values.iloc[i - left : i + right + 1]
        if pd.isna(center) or window.isna().any():
            continue
        if kind == "low":
            valid = center == window.min() and int((window == center).sum()) == 1
        else:
            valid = center == window.max() and int((window == center).sum()) == 1
        if valid:
            pivots.append(Pivot(i, values.index[i], float(center), kind))
    return pivots

