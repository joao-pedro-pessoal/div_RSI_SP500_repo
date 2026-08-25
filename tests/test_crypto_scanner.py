"""Offline tests. No network, no credentials."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crypto_scanner.config import load_config, ScannerConfig
from crypto_scanner.scanner import CryptoDivergenceScanner
from crypto_scanner.config import AppConfig
from crypto_scanner.telegram_client import TelegramClient
from crypto_scanner.timeframes import build, to_4h, to_daily, to_n_days, to_weekly
from crypto_scanner.universe import is_wrapped, EXTRA_STABLE_SYMBOLS
from crypto_scanner.validation import validate_ohlc, blocking_issues


def bars_4h(n: int, start: str = "2025-01-06", seed: int | None = None) -> pd.DataFrame:
    index = pd.date_range(start, periods=n, freq="4h", tz="UTC", name="timestamp")
    if seed is None:
        close = pd.Series(100 + np.arange(n) * 0.05, index=index)
    else:
        rng = np.random.default_rng(seed)
        close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, n))), index=index)
    op = close.shift(1).bfill()
    high = pd.concat([op, close], axis=1).max(axis=1) * 1.004
    low = pd.concat([op, close], axis=1).min(axis=1) * 0.996
    return pd.DataFrame(
        {"open": op, "high": high, "low": low, "close": close, "volume": 1.0},
        index=index,
    )


class TimeframeTests(unittest.TestCase):
    def test_daily_aggregates_six_4h_bars(self):
        df = bars_4h(60 * 6)
        daily = to_daily(df)
        self.assertEqual(len(daily), 60)
        self.assertEqual(daily["open"].iloc[0], df["open"].iloc[0])
        self.assertEqual(daily["close"].iloc[0], df["close"].iloc[5])
        self.assertEqual(daily["high"].iloc[0], df["high"].iloc[:6].max())
        self.assertAlmostEqual(daily["volume"].iloc[0], 6.0)

    def test_weekly_starts_monday(self):
        weekly = to_weekly(bars_4h(60 * 6, start="2025-01-08"))  # starts Wednesday
        self.assertTrue((weekly.index.dayofweek == 0).all())

    def test_3d_grid_is_shared_across_histories(self):
        """The whole point of a fixed anchor: two symbols with different
        histories must land on the same 3D boundaries."""
        full = bars_4h(80 * 6)
        partial = full.iloc[137:]          # offset not a multiple of 18
        a, b = to_n_days(full, 3), to_n_days(partial, 3)
        self.assertTrue(set(b.index).issubset(set(a.index)))
        common = a.index.intersection(b.index)
        self.assertGreater(len(common), 10)
        for column in ("open", "high", "low", "close"):
            np.testing.assert_allclose(a.loc[common, column], b.loc[common, column])

    def test_incomplete_bars_are_dropped(self):
        df = bars_4h(60 * 6)
        complete = len(to_daily(df))
        gapped = to_daily(df.drop(df.index[30:33]))
        self.assertEqual(len(gapped), complete - 1)

    def test_forming_bar_is_dropped(self):
        now = pd.Timestamp.now(tz="UTC").floor("4h")
        index = pd.date_range(end=now, periods=200, freq="4h", tz="UTC")
        close = pd.Series(np.linspace(100, 120, 200), index=index)
        df = pd.DataFrame({"open": close, "high": close * 1.01,
                           "low": close * 0.99, "close": close, "volume": 1.0},
                          index=index)
        self.assertEqual(len(to_4h(df)), 199)

    def test_unknown_timeframe_raises(self):
        with self.assertRaises(ValueError):
            build(bars_4h(100), "2h")


class ValidationTests(unittest.TestCase):
    def test_clean_series_passes(self):
        self.assertEqual(blocking_issues(validate_ohlc(bars_4h(300, seed=1))), [])

    def test_short_history_blocks(self):
        issues = validate_ohlc(bars_4h(50), min_rows=120)
        self.assertTrue(any(i.code == "insufficient_history" and i.blocking for i in issues))

    def test_incoherent_ohlc_blocks(self):
        df = bars_4h(300, seed=2)
        df.iloc[100, df.columns.get_loc("low")] = df["high"].iloc[100] * 2
        self.assertTrue(any(i.code == "ohlc_incoherent" for i in validate_ohlc(df)))

    def test_non_positive_blocks(self):
        df = bars_4h(300, seed=3)
        df.iloc[10, df.columns.get_loc("close")] = 0.0
        issues = validate_ohlc(df)
        self.assertTrue(any(i.code == "non_positive" and i.blocking for i in issues))

    def test_gap_is_reported_not_blocking(self):
        df = bars_4h(400, seed=4)
        gapped = df.drop(df.index[100:150])
        issues = validate_ohlc(gapped)
        self.assertTrue(any(i.code == "calendar_gaps" for i in issues))
        self.assertEqual(blocking_issues(issues), [])

    def test_large_real_move_does_not_block(self):
        """Crypto genuinely moves 40% in a day. That must not be filtered."""
        df = bars_4h(400, seed=5)
        df.loc[df.index[200]:, ["open", "high", "low", "close"]] *= 0.55
        self.assertEqual(blocking_issues(validate_ohlc(df)), [])


class UniverseTests(unittest.TestCase):
    def test_wrapped_detection(self):
        self.assertTrue(is_wrapped("WBTC", "Wrapped Bitcoin"))
        self.assertTrue(is_wrapped("STETH", "Lido Staked Ether"))
        self.assertTrue(is_wrapped("XYZ", "Wrapped Something"))
        self.assertFalse(is_wrapped("BTC", "Bitcoin"))
        self.assertFalse(is_wrapped("SOL", "Solana"))

    def test_common_stablecoins_listed(self):
        for symbol in ("USDT", "USDC", "DAI", "FDUSD"):
            self.assertIn(symbol, EXTRA_STABLE_SYMBOLS)


class AlertWindowTests(unittest.TestCase):
    def test_4h_window_covers_a_full_day(self):
        """A daily scan must not miss 4h confirmations. Six bars close per
        day, so the window has to exceed six."""
        config = ScannerConfig()
        self.assertGreaterEqual(config.age_window("4h"), 6)
        self.assertEqual(config.age_window("1W"), 1)

    def test_no_signal_is_missed_across_daily_runs(self):
        """
        Simulate consecutive daily scans over a 4h series and confirm every
        signal the detector produces is caught by at least one run.
        """
        from crypto_scanner.indicators import wilder_rsi
        from crypto_scanner.tradingview_divergence import (
            find_tradingview_regular_divergences as detect,
        )

        df = bars_4h(1400, seed=11)
        frame = df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                   "close": "Close", "volume": "Volume"})
        all_signals = detect("X", "4h", frame, wilder_rsi(frame["Close"], 14),
                             left=5, right=5)
        self.assertGreater(len(all_signals), 5)

        window = ScannerConfig().age_window("4h")
        caught: set[str] = set()
        # daily runs = every 6 bars, over the confirmable region
        for end in range(300, len(frame), 6):
            visible = frame.iloc[:end]
            found = detect("X", "4h", visible, wilder_rsi(visible["Close"], 14),
                           left=5, right=5)
            for signal in found:
                if (len(visible) - 1 - signal.confirmation_position) <= window:
                    caught.add(signal.signal_id)

        confirmable = {
            s.signal_id for s in all_signals
            if 300 <= s.confirmation_position < len(frame) - 6
        }
        missed = confirmable - caught
        self.assertEqual(missed, set(), f"{len(missed)} signals missed between runs")


class TelegramFormatTests(unittest.TestCase):
    def test_small_prices_keep_precision(self):
        """PENGU at $0.006357 must not render as $0.01."""
        self.assertNotIn("0.01", TelegramClient._fmt_price(0.006357))
        self.assertTrue(TelegramClient._fmt_price(0.000012345).startswith("0.0000123"))
        self.assertEqual(TelegramClient._fmt_price(98234.5), "98,234.50")

    def test_dry_run_needs_no_credentials(self):
        TelegramClient(dry_run=True).send("test")


class ScannerTests(unittest.TestCase):
    def test_scan_produces_signals_without_network(self):
        scanner = CryptoDivergenceScanner(AppConfig())
        report = scanner.scan_symbol("TESTUSDT", bars_4h(1200, seed=7))
        self.assertEqual(report.errors, {})

    def test_short_series_is_skipped_cleanly(self):
        scanner = CryptoDivergenceScanner(AppConfig())
        report = scanner.scan_symbol("NEWUSDT", bars_4h(40))
        self.assertIn("NEWUSDT", report.skipped)
        self.assertEqual(report.signals, [])


if __name__ == "__main__":
    unittest.main()


class MainPipelineTests(unittest.TestCase):
    """
    Exercita main() de ponta a ponta com a rede substituida.

    Esta classe existe porque um AttributeError em main() passou por 20
    testes: todos testavam modulos isoladamente e nenhum corria o
    orquestrador. Importar um modulo nao prova que o caminho principal
    funciona.
    """

    @staticmethod
    def _fake_fetch(symbols, config=None, verbose=True):
        out = {}
        for i, symbol in enumerate(symbols):
            rng = np.random.default_rng(i + 50)
            n = 4200
            index = pd.date_range(
                end=pd.Timestamp.now(tz="UTC").floor("4h"),
                periods=n, freq="4h", tz="UTC", name="timestamp",
            )
            close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.018, n))), index=index)
            op = close.shift(1).bfill()
            out[symbol] = pd.DataFrame({
                "open": op,
                "high": pd.concat([op, close], axis=1).max(axis=1) * 1.004,
                "low": pd.concat([op, close], axis=1).min(axis=1) * 0.996,
                "close": close, "volume": 1e6,
            }, index=index)
        return out, []

    def _run_main(self, symbols):
        import main_crypto as main_module
        original = main_module.fetch_many
        original_argv = sys.argv
        try:
            main_module.fetch_many = self._fake_fetch
            sys.argv = ["main_crypto.py", "--dry-run", "--symbols"] + symbols
            return main_module.main()
        finally:
            main_module.fetch_many = original
            sys.argv = original_argv

    def test_main_completes_successfully(self):
        self.assertEqual(self._run_main(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]), 0)

    def test_main_sends_signals_when_present(self):
        """Nao basta nao rebentar: o caminho de envio tem de correr."""
        import main_crypto as main_module
        self.assertEqual(self._run_main([f"C{i}-USDT-SWAP" for i in range(20)]), 0)
        state_path = Path("state/crypto_4h_heartbeat.json")
        self.assertTrue(state_path.exists())
        payload = json.loads(state_path.read_text())
        self.assertEqual(payload["status"], "ok")
        self.assertGreater(payload["details"]["new_signals"], 0)
        self.assertEqual(payload["details"]["new_signals"], payload["details"]["sent"])


class OKXProviderTests(unittest.TestCase):
    """
    Testa a paginacao contra respostas SIMULADAS da OKX.

    Nao substitui uma chamada real, mas fixa as tres coisas que a
    documentacao especifica e que sao faceis de implementar ao contrario:
    ordem newest-first, cursor `after` a andar para tras, e o campo
    `confirm` que marca a vela ainda em formacao.
    """

    @staticmethod
    def _fake_page(newest_ms: int, count: int, forming: bool = False):
        """Uma pagina OKX: newest-first, 4h de espacamento."""
        rows = []
        for i in range(count):
            ts = newest_ms - i * 4 * 3600 * 1000
            confirm = "0" if (forming and i == 0) else "1"
            price = 100 + i
            rows.append([str(ts), str(price), str(price * 1.01), str(price * 0.99),
                         str(price), "1000", "10", "10", confirm])
        return {"code": "0", "msg": "", "data": rows}

    def test_pagination_walks_backwards_and_sorts(self):
        from crypto_scanner import provider

        newest = 1_800_000_000_000
        pages = []
        cursor_seen = []

        def fake_get_retry(url, params, config):
            cursor_seen.append(params.get("after"))
            page_index = len(pages)
            if page_index >= 3:
                return {"code": "0", "msg": "", "data": []}
            start = newest - page_index * 100 * 4 * 3600 * 1000
            page = self._fake_page(start, 100, forming=(page_index == 0))
            pages.append(page)
            return page

        original = provider._get_retry
        try:
            provider._get_retry = fake_get_retry
            df = provider.fetch_klines("BTC-USDT-SWAP",
                                       provider.ProviderConfig(bars=300, sleep_between=0))
        finally:
            provider._get_retry = original

        # a primeira pagina nao leva cursor; as seguintes levam
        self.assertIsNone(cursor_seen[0])
        self.assertTrue(all(c is not None for c in cursor_seen[1:]))
        # o cursor tem de ANDAR PARA TRAS no tempo
        numeric = [int(c) for c in cursor_seen[1:]]
        self.assertEqual(numeric, sorted(numeric, reverse=True))
        # resultado ordenado do mais antigo para o mais recente
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertFalse(df.index.has_duplicates)
        # a vela em formacao (confirm="0") nao pode aparecer
        self.assertEqual(len(df), 299)

    def test_forming_candle_is_excluded(self):
        from crypto_scanner.provider import _rows_to_frame
        page = self._fake_page(1_800_000_000_000, 10, forming=True)
        df = _rows_to_frame(page["data"])
        self.assertEqual(len(df), 9)

    def test_chart_link_never_doubles_the_quote(self):
        """
        Bug apanhado no output de um teste: um simbolo sem tracos dava
        "C8USDTUSDT.P" -- USDT duplicado, link para um grafico inexistente.
        """
        from crypto_scanner.telegram_client import TelegramClient as T
        self.assertEqual(T._base_asset("BTC-USDT-SWAP"), "BTC")
        self.assertEqual(T._base_asset("PENGU-USDT-SWAP"), "PENGU")
        self.assertEqual(T._base_asset("BTCUSDT"), "BTC")
        self.assertEqual(T._base_asset("C8USDT"), "C8")
        self.assertEqual(T._base_asset("BTC"), "BTC")


class TelegramTopicRoutingTests(unittest.TestCase):
    """
    Este teste existe porque o cliente de cripto foi copiado de uma versao
    ANTIGA do scanner de accoes, anterior ao suporte a topicos. O secret
    estava correto e o codigo ignorava-o: as mensagens iam para o General
    sem erro nenhum -- uma falha silenciosa que so se ve no Telegram.
    """

    def _client(self, topic_env):
        import os
        from crypto_scanner.telegram_client import TelegramClient
        antes = os.environ.get("TELEGRAM_TOPIC_ID")
        os.environ["TELEGRAM_BOT_TOKEN"] = "x:y"
        os.environ["TELEGRAM_CHAT_ID"] = "-1003951050134"
        os.environ.pop("TELEGRAM_TOPIC_ID", None)
        if topic_env is not None:
            os.environ["TELEGRAM_TOPIC_ID"] = topic_env
        try:
            return TelegramClient()
        finally:
            os.environ.pop("TELEGRAM_TOPIC_ID", None)
            if antes is not None:
                os.environ["TELEGRAM_TOPIC_ID"] = antes

    def test_topic_id_is_read_from_environment(self):
        self.assertEqual(self._client("778").topic_id, "778")

    def test_blank_or_missing_topic_falls_back_to_general(self):
        self.assertIsNone(self._client("").topic_id)
        self.assertIsNone(self._client("   ").topic_id)
        self.assertIsNone(self._client(None).topic_id)

    def test_message_thread_id_is_sent_when_topic_set(self):
        """Nao basta ler a variavel: tem de ir no corpo do pedido."""
        import json
        import urllib.request
        from crypto_scanner.telegram_client import TelegramClient

        capturado = {}

        def fake_urlopen(request, timeout=None):
            capturado["body"] = request.data.decode()

            class R:
                def read(self): return b'{"ok":true}'
                def __enter__(self): return self
                def __exit__(self, *a): return False
            return R()

        client = self._client("778")
        original = urllib.request.urlopen
        try:
            urllib.request.urlopen = fake_urlopen
            TelegramClient._last_send_at = 0.0
            client.send("teste")
        finally:
            urllib.request.urlopen = original
        self.assertIn("message_thread_id=778", capturado["body"])


class DivergenceSplitTests(unittest.TestCase):
    """
    A separacao existe porque correr mais vezes so ajuda no 4h: nos
    timeframes altos o atraso estrutural do pivot_right=5 (120h no 1D,
    840h no 1W) torna a frequencia de execucao irrelevante.
    """

    CONFIGS = ("config_crypto_4h.yaml", "config_crypto_daily.yaml")

    def _load(self, name):
        import yaml
        path = Path(__file__).resolve().parent.parent / name
        self.assertTrue(path.exists(), f"{name} em falta")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_timeframes_are_split_without_overlap(self):
        seen = []
        for name in self.CONFIGS:
            seen.extend(self._load(name)["scanner"]["timeframes"])
        self.assertEqual(sorted(seen), ["1D", "1W", "3D", "4h"])

    def test_state_files_are_distinct(self):
        self.assertEqual(len({self._load(n)["state_file"] for n in self.CONFIGS}), 2)
        self.assertEqual(len({self._load(n)["heartbeat_file"] for n in self.CONFIGS}), 2)

    def test_intraday_config_is_light(self):
        """O 4h nao pode arrastar as 4200 barras que so o 1W precisa."""
        self.assertLessEqual(self._load("config_crypto_4h.yaml")["data"]["bars_4h"], 600)
        self.assertGreaterEqual(self._load("config_crypto_daily.yaml")["data"]["bars_4h"], 4000)

    def test_pivot_settings_stay_faithful_to_tradingview(self):
        """Fidelidade ao Pine foi decisao explicita; nao pode driftar."""
        for name in self.CONFIGS:
            scanner = self._load(name)["scanner"]
            self.assertEqual(scanner["pivot_left"], 5)
            self.assertEqual(scanner["pivot_right"], 5)
            self.assertEqual(scanner["detector_mode"], "tradingview")
