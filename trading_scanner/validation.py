from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DataIssue:
    code: str
    message: str
    blocking: bool


def validate_ohlc(
    df: pd.DataFrame,
    *,
    min_rows: int = 80,
    max_calendar_gap_days: int = 14,
    split_ratio_tolerance: float = 0.08,
    block_suspicious_splits: bool = False,
) -> list[DataIssue]:
    """Eight groups of defensive checks for silently bad market data."""
    issues: list[DataIssue] = []
    required = ["Open", "High", "Low", "Close"]

    # 1. Schema.
    missing = [c for c in required if c not in df.columns]
    if missing:
        return [DataIssue("missing_columns", f"missing OHLC columns: {missing}", True)]

    # 2. Enough observations.
    if len(df) < min_rows:
        issues.append(DataIssue("too_short", f"only {len(df)} rows; need {min_rows}", True))
    if df.empty:
        return issues

    # 3. Index integrity.
    if not isinstance(df.index, pd.DatetimeIndex):
        issues.append(DataIssue("bad_index", "index is not DatetimeIndex", True))
    else:
        if not df.index.is_monotonic_increasing:
            issues.append(DataIssue("unsorted_index", "timestamps are not sorted", True))
        if df.index.has_duplicates:
            issues.append(DataIssue("duplicate_dates", "duplicate timestamps found", True))

    numeric = df[required].apply(pd.to_numeric, errors="coerce")

    # 4. Finite numeric OHLC.
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy()).all():
        issues.append(DataIssue("non_finite", "NaN/inf OHLC values found", True))

    # 5. Positive prices.
    if (numeric <= 0).any().any():
        issues.append(DataIssue("non_positive", "non-positive OHLC value found", True))

    # 6. Candle invariants.
    candle_bad = (
        (numeric["High"] < numeric[["Open", "Close", "Low"]].max(axis=1))
        | (numeric["Low"] > numeric[["Open", "Close", "High"]].min(axis=1))
    )
    if bool(candle_bad.any()):
        issues.append(DataIssue("invalid_candle", "OHLC high/low invariant violated", True))

    # 7. Implausibly long missing-calendar gaps (warning, since holidays exist).
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
        gaps = df.index.to_series().diff().dt.days.dropna()
        if len(gaps) and int(gaps.max()) > max_calendar_gap_days:
            issues.append(DataIssue("long_gap", f"calendar gap of {int(gaps.max())} days", False))

    # 8. Split-like discontinuity.
    #
    # CORRIGIDO — dois bugs medidos na versao anterior:
    #
    #   (a) A lista de racios nao tinha 10 nem 20. Splits de 10:1 e 20:1
    #       passavam 0/20 vezes. Nao e hipotetico: a NVDA fez 10:1 em
    #       junho de 2024 e esta no S&P 500.
    #
    #   (b) Tolerancia de 0.025 era apertada demais: 19/20 nos racios
    #       cobertos. O racio observado NAO e o racio do split -- e o
    #       racio vezes o movimento real da acao nesse dia. Um split 2:1
    #       num dia de +2.1% da 2.043, nao 2.000.
    #
    #   (c) Racios pequenos (<2) sao ambiguos: uma queda real de -49%
    #       disparava falso positivo 11/20 vezes, e com blocking ativo o
    #       ticker era removido do scan em silencio. Um alerta falso ve-se
    #       e ignora-se; um ticker bloqueado e um sinal perdido de que
    #       nunca ficas a saber. Por isso racios grandes bloqueiam e
    #       pequenos apenas avisam.
    closes = numeric["Close"]
    ratios = (closes.shift(1) / closes).to_numpy()
    returns = closes.pct_change().to_numpy()
    # .shift(1): a janela NAO pode incluir a propria barra do salto, ou o
    # salto infla a volatilidade e suprime a sua propria detecao.
    vol = closes.pct_change().rolling(60, min_periods=20).std().shift(1).to_numpy()

    major_ratios = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0, 30.0]
    minor_ratios = [1.2, 1.25, 4 / 3, 1.4, 1.5, 5 / 3]

    major_hits: list[int] = []
    minor_hits: list[int] = []
    for i in range(1, len(ratios)):
        r = ratios[i]
        if not np.isfinite(r) or r <= 0 or not np.isfinite(returns[i]):
            continue
        # Um tick mau reverte no dia seguinte; um split nao.
        nxt = returns[i + 1] if i + 1 < len(returns) else 0.0
        if np.isfinite(nxt) and np.sign(nxt) != np.sign(returns[i]) and abs(nxt) > abs(returns[i]) * 0.5:
            continue
        # O salto tem de ser enorme face a volatilidade DESTA acao.
        v = vol[i]
        if np.isfinite(v) and v > 0 and abs(returns[i]) < 6.0 * v:
            continue
        if any(min(abs(r - s) / s, abs(r - 1 / s) * s) < split_ratio_tolerance for s in major_ratios):
            major_hits.append(i)
        elif any(min(abs(r - s) / s, abs(r - 1 / s) * s) < 0.03 for s in minor_ratios):
            minor_hits.append(i)

    if major_hits:
        dates = [df.index[i].date().isoformat() for i in major_hits[:3]]
        issues.append(
            DataIssue("split_like_jump", f"split-like close discontinuity near {dates}", block_suspicious_splits)
        )
    if minor_hits:
        dates = [df.index[i].date().isoformat() for i in minor_hits[:3]]
        issues.append(
            DataIssue("possible_small_split", f"ambiguous split-sized move near {dates}; verify manually", False)
        )
    return issues


def blocking_issues(issues: list[DataIssue]) -> list[DataIssue]:
    return [issue for issue in issues if issue.blocking]

