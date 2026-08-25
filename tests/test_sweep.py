"""Testes do detetor de varrimentos. Offline."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crypto_scanner.sweep import (
    Sweep, SweepParams, detect_sweep, scan_history, scan_recent,
)
from crypto_scanner.sweep_scanner import SweepScanner


def scenario(kind: str, seed: int = 0) -> pd.DataFrame:
    """Cenarios com forma conhecida, para a resposta ser verificavel."""
    rng = np.random.default_rng(seed)
    o_, h_, l_, c_ = [], [], [], []

    def add(op, cl, hi, lo):
        o_.append(op); c_.append(cl); h_.append(hi); l_.append(lo)

    px = 100.0
    for _ in range(30):                       # ruido inicial
        nx = px + rng.normal(0, 0.4)
        add(px, nx, max(px, nx) + 0.3, min(px, nx) - 0.3)
        px = nx

    if kind.startswith("bull"):
        base = 98.0
        for lvl in (98.0, 101.0, 104.0):      # 3 minimos ascendentes
            for step in range(4):
                tgt = lvl + step * 1.2
                add(px, tgt, tgt + 0.6, lvl if step == 0 else tgt - 0.6)
                px = tgt
            for _ in range(2):
                tgt = px - 0.8
                add(px, tgt, px + 0.3, tgt - 0.4)
                px = tgt
        if kind == "bull_sweep":
            add(px, px - 0.2, px + 0.4, base - 0.3)      # varre e recupera
        elif kind == "bull_shallow":
            add(px, px - 0.2, px + 0.4, px - 1.5)        # nao chega a origem
        elif kind == "bull_no_recovery":
            add(px, base + 0.1, px + 0.3, base - 0.3)    # varre e fecha em baixo

    elif kind == "bear_sweep":
        top = 112.0
        for lvl in (112.0, 109.0, 106.0):
            for step in range(4):
                tgt = lvl - step * 1.2
                add(px, tgt, lvl if step == 0 else tgt + 0.6, tgt - 0.6)
                px = tgt
            for _ in range(2):
                tgt = px + 0.8
                add(px, tgt, tgt + 0.4, px - 0.3)
                px = tgt
        add(px, px + 0.2, top + 0.3, px - 0.4)

    elif kind == "clean_trend":
        for _ in range(30):
            tgt = px + 0.7
            add(px, tgt, tgt + 0.4, px - 0.3)
            px = tgt

    elif kind == "range":
        for _ in range(40):
            tgt = 100 + rng.normal(0, 1.2)
            add(px, tgt, max(px, tgt) + 0.5, min(px, tgt) - 0.5)
            px = tgt

    index = pd.date_range("2026-01-01", periods=len(o_), freq="4h", tz="UTC")
    return pd.DataFrame(
        {"open": o_, "high": h_, "low": l_, "close": c_, "volume": 1e6}, index=index
    )


def noise(seed: int, n: int = 1200, vol: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    op = np.r_[close[0], close[:-1]]
    return pd.DataFrame({
        "open": op,
        "high": np.maximum(op, close) * (1 + abs(rng.normal(0, vol * 0.5, n))),
        "low": np.minimum(op, close) * (1 - abs(rng.normal(0, vol * 0.5, n))),
        "close": close, "volume": 1e6,
    }, index=pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"))


class DetectionTests(unittest.TestCase):
    def test_bullish_sweep_detected(self):
        hits = sum(detect_sweep(scenario("bull_sweep", s), "X", "4h") is not None
                   for s in range(10))
        self.assertGreaterEqual(hits, 8, f"apanhou so {hits}/10")

    def test_bearish_sweep_detected(self):
        hits = sum(detect_sweep(scenario("bear_sweep", s), "X", "4h") is not None
                   for s in range(10))
        self.assertGreaterEqual(hits, 8, f"apanhou so {hits}/10")

    def test_shallow_dip_rejected(self):
        """Nao chegar a origem da tendencia nao e varrimento: os stops
        continuam la, que e o ponto todo do padrao."""
        hits = sum(detect_sweep(scenario("bull_shallow", s), "X", "4h") is not None
                   for s in range(10))
        self.assertEqual(hits, 0)

    def test_sweep_without_recovery_rejected(self):
        """Furar e fechar em baixo e rutura, nao varrimento."""
        hits = sum(detect_sweep(scenario("bull_no_recovery", s), "X", "4h") is not None
                   for s in range(10))
        self.assertEqual(hits, 0)

    def test_clean_trend_and_range_rejected(self):
        for kind in ("clean_trend", "range"):
            hits = sum(detect_sweep(scenario(kind, s), "X", "4h") is not None
                       for s in range(10))
            self.assertEqual(hits, 0, f"{kind} deu {hits} falsos positivos")

    def test_noise_rate_is_low(self):
        """
        Referencia medida: em ruido puro este padrao aparece muito menos que
        a divergencia de RSI (~0.7 vs ~15 por 1000 barras). Se este numero
        disparar, alguma alteracao afrouxou os criterios.
        """
        total = sum(len(scan_history(noise(s), "X", "4h")) for s in range(6))
        rate = total / 6 / 1170 * 1000
        self.assertLess(rate, 3.0, f"{rate:.2f} por 1000 barras — demasiado solto")


class WickFractionTests(unittest.TestCase):
    """
    O pavio grande e o criterio central deste padrao, nao um extra.

    Mede-se como fracao da amplitude TOTAL da vela, e nao pavio/corpo:
    o racio falha em velas de corpo grande com pavio enorme, e passa em
    velas de corpo minusculo com pavio irrelevante.
    """

    @staticmethod
    def _with_wick(fraction: float, seed: int = 0) -> pd.DataFrame:
        from crypto_scanner.sweep import _ascending_run, find_pivots
        df = scenario("bull_sweep", seed).copy()
        i = len(df) - 1
        lows = df["low"].to_numpy()
        run = _ascending_run(find_pivots(df["low"], 2, 2, "low"), lows, 3, 120, i)
        origin = lows[run[0]]
        low = origin - 0.3                    # desce ate a origem
        op = float(df["open"].iloc[i])
        total = (op - low) / fraction
        close = low + total * 0.95
        if close < op:
            close = op + (low + total - op) * 0.5
        for col, val in (("low", low), ("close", close),
                         ("high", max(low + total, close, op))):
            df.iloc[i, df.columns.get_loc(col)] = val
        return df

    def test_large_wicks_pass_small_wicks_rejected(self):
        p = SweepParams()
        self.assertIsNone(detect_sweep(self._with_wick(0.40), "X", "4h", p))
        self.assertIsNone(detect_sweep(self._with_wick(0.55), "X", "4h", p))
        self.assertIsNotNone(detect_sweep(self._with_wick(0.75), "X", "4h", p))
        self.assertIsNotNone(detect_sweep(self._with_wick(0.90), "X", "4h", p))

    def test_wick_fraction_is_reported(self):
        s = detect_sweep(self._with_wick(0.90), "X", "4h")
        self.assertGreater(s.wick_fraction, 0.8)


class ParameterTests(unittest.TestCase):
    def test_wick_fraction_gates_recovery(self):
        """O pavio e o criterio central: uma vela sem pavio dominante nao
        e varrimento, por muito que o fecho esteja no topo."""
        df = scenario("bull_sweep", 0)
        self.assertIsNotNone(detect_sweep(df, "X", "4h", SweepParams(min_wick_fraction=0.6)))
        self.assertIsNone(detect_sweep(df, "X", "4h", SweepParams(min_wick_fraction=0.99)))

    def test_wick_fraction_is_bounded(self):
        """Ao contrario de pavio/corpo, a fracao nunca dispara para infinito."""
        s = detect_sweep(scenario("bull_sweep", 0), "X", "4h")
        self.assertGreaterEqual(s.wick_fraction, 0.0)
        self.assertLessEqual(s.wick_fraction, 1.0)

    def test_close_position_gates_recovery(self):
        df = scenario("bull_sweep", 0)
        self.assertIsNotNone(detect_sweep(df, "X", "4h", SweepParams(min_close_position=0.5)))
        self.assertIsNone(detect_sweep(df, "X", "4h", SweepParams(min_close_position=0.99)))

    def test_wick_fraction_gates(self):
        df = scenario("bull_sweep", 0)
        self.assertIsNotNone(detect_sweep(df, "X", "4h", SweepParams(min_wick_fraction=0.6)))
        self.assertIsNone(detect_sweep(df, "X", "4h", SweepParams(min_wick_fraction=0.99)))

    def test_origin_tolerance_gates_depth(self):
        df = scenario("bull_shallow", 0)
        # Afrouxar SO a tolerancia nao chega: neste cenario o pavio nem
        # desce abaixo do ultimo pivot, logo min_sweep_depth_pct tambem
        # rejeita. E o comportamento correto -- um dip que nao varre nada
        # nao e um varrimento, por muito permissiva que seja a tolerancia.
        loose = SweepParams(origin_tolerance=5.0)
        self.assertIsNone(detect_sweep(df, "X", "4h", loose))
        # com AMBOS os criterios desligados ja passa, o que confirma que
        # sao estes dois a rejeitar e nao outra coisa qualquer
        both_off = SweepParams(origin_tolerance=5.0, min_sweep_depth_pct=-100.0)
        self.assertIsNotNone(detect_sweep(df, "X", "4h", both_off))

    def test_min_pivots_required(self):
        df = scenario("bull_sweep", 0)
        self.assertIsNone(detect_sweep(df, "X", "4h", SweepParams(min_pivots=20)))

    def test_short_series_returns_none(self):
        short = scenario("bull_sweep", 0).iloc[:10]
        self.assertIsNone(detect_sweep(short, "X", "4h"))


class SignalIdTests(unittest.TestCase):
    def test_signal_id_is_deterministic_and_specific(self):
        a = detect_sweep(scenario("bull_sweep", 0), "BTC-USDT-SWAP", "4h")
        b = detect_sweep(scenario("bull_sweep", 0), "BTC-USDT-SWAP", "4h")
        c = detect_sweep(scenario("bull_sweep", 0), "BTC-USDT-SWAP", "1D")
        self.assertEqual(a.signal_id, b.signal_id)
        self.assertNotEqual(a.signal_id, c.signal_id)
        self.assertIn("sweep", a.signal_id)


class WindowTests(unittest.TestCase):
    def test_scan_recent_covers_window(self):
        """A janela existe para uma execucao diaria nao perder velas de 1h."""
        df = scenario("bull_sweep", 0)
        self.assertEqual(len(scan_recent(df, "X", "4h", window=1)), 1)
        extended = pd.concat([df, df.iloc[-1:]])   # uma barra a mais
        self.assertGreaterEqual(len(scan_recent(extended, "X", "4h", window=3)), 1)


def long_scenario(kind: str, seed: int = 0, pad: int = 900) -> pd.DataFrame:
    """
    Cenario precedido de historico, com OHLC coerente.

    Concatenar dois DataFrames construidos a parte produz velas em que o
    open nao encaixa no range da anterior -- a validacao apanha isso e
    bloqueia o simbolo inteiro. Reconstruir os extremos a partir de
    open/close resolve.
    """
    head = noise(seed + 100, pad)
    tail = scenario(kind, seed)
    scale = float(head["close"].iloc[-1]) / float(tail["open"].iloc[0])
    tail = tail * scale
    joined = pd.concat([head, tail], ignore_index=True)
    joined["high"] = joined[["open", "close", "high"]].max(axis=1)
    joined["low"] = joined[["open", "close", "low"]].min(axis=1)
    joined.index = pd.date_range("2024-01-01", periods=len(joined), freq="4h", tz="UTC")
    return joined


class ScannerTests(unittest.TestCase):
    def test_scanner_runs_all_timeframes(self):
        scanner = SweepScanner(SweepParams(), {"4h": 1, "1D": 1, "3D": 1})
        report = scanner.scan_symbol("X-USDT-SWAP", long_scenario("bull_sweep"), None,
                                     ["4h", "1D", "3D"])
        self.assertEqual(report.errors, {})
        self.assertEqual(report.skipped, [], "validacao bloqueou dados validos")

    def test_scanner_finds_sweep_in_long_series(self):
        """O caminho completo tem de produzir sinal, nao so nao rebentar."""
        scanner = SweepScanner(SweepParams(), {"4h": 1})
        report = scanner.scan_symbol("X-USDT-SWAP", long_scenario("bull_sweep"), None, ["4h"])
        self.assertEqual(len(report.signals), 1)
        self.assertEqual(report.signals[0].kind, "bullish_sweep")

    def test_missing_data_is_skipped_cleanly(self):
        scanner = SweepScanner(SweepParams(), {"1h": 1})
        report = scanner.scan_symbol("X-USDT-SWAP", None, None, ["1h", "4h"])
        self.assertEqual(report.signals, [])
        self.assertEqual(report.errors, {})


if __name__ == "__main__":
    unittest.main()


class WorkflowSplitTests(unittest.TestCase):
    """
    A separacao por timeframe existe por uma razao medida: antes, uma
    execucao que so ia analisar 1h descarregava na mesma 3000 barras de
    4h -- 30 paginas por moeda em vez de 3. Estes testes garantem que as
    tres configuracoes continuam a pedir apenas o que precisam e a
    escrever em ficheiros de estado distintos.
    """

    CONFIGS = ("config_sweep_1h.yaml", "config_sweep_4h.yaml", "config_sweep_daily.yaml")

    def _load(self, name):
        import yaml
        path = Path(__file__).resolve().parent.parent / name
        self.assertTrue(path.exists(), f"{name} em falta")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_each_config_covers_distinct_timeframes(self):
        seen = []
        for name in self.CONFIGS:
            seen.extend(self._load(name)["scanner"]["timeframes"])
        self.assertEqual(sorted(seen), ["1D", "1h", "3D", "4h"],
                         "timeframes duplicados ou em falta entre configs")

    def test_state_files_are_distinct(self):
        files = {self._load(n)["state_file"] for n in self.CONFIGS}
        self.assertEqual(len(files), 3, "configs a partilhar ficheiro de estado")
        beats = {self._load(n)["heartbeat_file"] for n in self.CONFIGS}
        self.assertEqual(len(beats), 3)

    def test_intraday_configs_do_not_pull_deep_history(self):
        """O 1h nao pode arrastar as 3000 barras de 4h do config diario."""
        one_hour = self._load("config_sweep_1h.yaml")["data"]
        self.assertLessEqual(one_hour["bars_4h"], 400,
                             "config de 1h a pedir historico de 4h a mais")
        daily = self._load("config_sweep_daily.yaml")["data"]
        self.assertGreaterEqual(daily["bars_4h"], 2200,
                                "3D precisa de >=120 barras => >=2160 barras de 4h")

    def test_alert_windows_have_slack_but_are_small(self):
        """
        Com um workflow por timeframe, a janela so precisa de margem para
        execucoes falhadas -- nao de cobrir 24h como quando tudo corria
        uma vez por dia.
        """
        for name in self.CONFIGS:
            for tf, window in self._load(name)["scanner"]["alert_age_bars"].items():
                self.assertGreaterEqual(window, 2, f"{name}/{tf} sem margem")
                self.assertLessEqual(window, 5, f"{name}/{tf} janela grande demais")
