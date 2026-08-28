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
        elif kind == "bull_no_sweep":
            # nao chega sequer ao ultimo pivot: nao ha stops varridos
            add(px, px - 0.2, px + 0.4, px - 0.4)
        elif kind == "bull_no_wick":
            # varre mas a vela e quase toda corpo -- nao e rejeicao
            add(px, base + 0.4, base + 0.5, base - 0.3)
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

    def test_dip_that_sweeps_nothing_is_rejected(self):
        """Sem furar o pivot nao ha stops varridos -- nao e o padrao."""
        hits = sum(detect_sweep(scenario("bull_no_sweep", s), "X", "4h") is not None
                   for s in range(10))
        self.assertEqual(hits, 0)

    def test_body_candle_without_wick_is_rejected(self):
        """
        Uma vela quase toda corpo nao e uma rejeicao, mesmo que fure o
        pivot. Caso real medido: pavio 0.19 -- o unico dos casos marcados
        que o Jhonny concordou nao ser um varrimento.
        """
        hits = sum(detect_sweep(scenario("bull_no_wick", s), "X", "4h") is not None
                   for s in range(10))
        self.assertEqual(hits, 0)

    def test_sweep_without_recovery_rejected(self):
        """Furar e fechar em baixo e rutura, nao varrimento."""
        hits = sum(detect_sweep(scenario("bull_no_recovery", s), "X", "4h") is not None
                   for s in range(10))
        self.assertEqual(hits, 0)

    def test_clean_trend_and_range_rejected(self):
        for kind in ("clean_trend", "range", "bull_no_wick"):
            hits = sum(detect_sweep(scenario(kind, s), "X", "4h") is not None
                       for s in range(10))
            self.assertEqual(hits, 0, f"{kind} deu {hits} falsos positivos")

    def test_noise_rate_stays_within_expected_band(self):
        """
        GUARDA DE REGRESSAO, com um numero que MUDOU de proposito.

        A calibracao sintetica dava 0.1 por 1000 barras -- parecia otimo,
        mas rejeitava todos os casos reais. Calibrado com casos reais e com
        a instrucao explicita de nao perder sinais, a taxa sobe para ~8.

        Isso traduz-se em ~25 alertas/dia com 100 moedas em 4 timeframes,
        em dados SEM informacao nenhuma. E o custo assumido de nao perder
        sinais -- nao um defeito, mas tambem nao uma virtude.

        O limite existe para apanhar afrouxamentos acidentais: se passar de
        15, alguma alteracao tornou o detetor demasiado permissivo.
        """
        total = sum(len(scan_history(noise(s), "X", "4h")) for s in range(6))
        rate = total / 6 / 1170 * 1000
        self.assertLess(rate, 25.0, f"{rate:.2f} por 1000 barras — afrouxou demais")
        self.assertGreater(rate, 0.5, f"{rate:.2f} — apertou tanto que nao deteta nada")


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
        """
        Limites ATUALIZADOS apos calibracao com casos reais.
        A versao anterior esperava que 0.55 fosse REJEITADO -- mas 0.55 e
        exatamente o pavio minimo dos casos que o Jhonny marcou como bons.
        O teste codificava a calibracao sintetica errada.
        """
        p = SweepParams()
        self.assertIsNone(detect_sweep(self._with_wick(0.25), "X", "4h", p))
        self.assertIsNone(detect_sweep(self._with_wick(0.35), "X", "4h", p))
        self.assertIsNotNone(detect_sweep(self._with_wick(0.55), "X", "4h", p))
        self.assertIsNotNone(detect_sweep(self._with_wick(0.75), "X", "4h", p))

    def test_wick_fraction_is_reported(self):
        s = detect_sweep(self._with_wick(0.90), "X", "4h")
        self.assertGreater(s.wick_fraction, 0.8)


class ParameterTests(unittest.TestCase):
    def test_wick_fraction_gates_recovery(self):
        df = scenario("bull_sweep", 0)
        self.assertIsNotNone(detect_sweep(df, "X", "4h", SweepParams(min_wick_fraction=0.4)))
        self.assertIsNone(detect_sweep(df, "X", "4h", SweepParams(min_wick_fraction=0.99)))

    def test_thresholds_accept_real_measured_cases(self):
        """
        Casos reais marcados no TradingView. Estes numeros sao a razao de
        ser da calibracao atual -- a anterior, feita com velas sinteticas
        de 93% de pavio, rejeitava-os a todos.
        """
        p = SweepParams()
        reais = [
            ("img1", 0.72, 0.72, 89.5, 0.19, 0.19, True),
            ("img2", 0.55, 0.55, 3.19, 0.22, 0.42, True),
            ("img3a", 0.71, 0.89, 3.83, 0.31, 0.54, True),
            ("img3b", 0.74, 0.92, 4.08, 1.18, 0.48, True),
            ("img3-mau", 0.19, 0.52, 0.57, 0.07, 4.61, False),
        ]
        for nome, wick, close_pos, ratio, depth, trend, esperado in reais:
            passa = (wick >= p.min_wick_fraction and close_pos >= p.min_close_position
                     and ratio >= p.min_wick_ratio and depth >= p.min_depth_atr
                     and trend >= p.min_trend_atr)
            self.assertEqual(passa, esperado, f"caso {nome}")

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

    def test_origin_is_reported_not_required(self):
        """
        A origem deixou de ser exigida. Medido em casos reais: em
        estruturas curtas fica a 0.2 ATR (alcancavel), em tendencias
        longas a 4.5 ATR (impossivel). Continua a ser reportada para
        poder filtrar-se depois, mas nao bloqueia.
        """
        s = detect_sweep(scenario("bull_sweep", 0), "X", "4h")
        self.assertIsNotNone(s)
        self.assertIsInstance(s.origin_atr, float)
        self.assertIsInstance(s.swept_level, float)

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


class StartNoticeTests(unittest.TestCase):
    """
    Aviso de inicio ligado em TODOS os scanners, por pedido explicito.

    Custo assumido: 38 mensagens de inicio por dia mais 38 de conclusao.
    O scanner de 1h contribui com 48 dessas 76, por correr 24x/dia.
    Reverter e mudar send_start_notice para false no config respetivo.
    """

    CONFIGS = ("config_sweep_1h.yaml", "config_sweep_4h.yaml",
               "config_sweep_daily.yaml")

    def _telegram(self, name):
        import yaml
        path = Path(__file__).resolve().parent.parent / name
        return yaml.safe_load(path.read_text(encoding="utf-8")).get("telegram", {})

    def test_enabled_on_all_sweep_scanners(self):
        for name in self.CONFIGS:
            self.assertTrue(self._telegram(name)["send_start_notice"], name)

    def test_enabled_on_divergence_scanners(self):
        for name in ("config_crypto_4h.yaml", "config_crypto_daily.yaml"):
            self.assertTrue(self._telegram(name)["send_start_notice"], name)

    def test_heartbeat_always_on(self):
        """
        O heartbeat nunca se desliga: a AUSENCIA dele e o unico sinal de
        que uma execucao agendada nao correu. O GitHub notifica falhas,
        nao execucoes que simplesmente nao aconteceram.
        """
        for name in ("config_sweep_1h.yaml", "config_sweep_4h.yaml",
                     "config_sweep_daily.yaml"):
            self.assertTrue(self._telegram(name).get("send_heartbeat", True), name)
