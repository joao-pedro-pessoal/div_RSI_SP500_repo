import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

from crypto_scanner import provider as p
from crypto_scanner.telegram_client import TelegramClient as T

STEP = 240 * 60_000
NOW = 1_800_000_000_000


def row(ts):
    return [str(ts), "100", "102", "98", "101", "12", "1212"]


def response(rows):
    return {"retCode": 0, "time": NOW, "result": {"list": rows}}


class BybitProviderTests(unittest.TestCase):
    def test_pages_backwards_exact_count_closed_sorted(self):
        calls = []
        def fake(url, params, config):
            calls.append(dict(params))
            start = NOW if "end" not in params else params["end"] + 1 - STEP
            return response([row(start - i * STEP) for i in range(params["limit"])])
        with patch.object(p, "_get_retry", side_effect=fake):
            frame = p.fetch_klines("BTCUSDT", p.ProviderConfig(bars=1200, sleep_between=0))
        self.assertEqual(len(frame), 1200)
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertFalse(frame.index.has_duplicates)
        self.assertEqual(int(frame.index[-1].timestamp()*1000), NOW-STEP)
        self.assertEqual(calls[1]["end"], NOW - 999*STEP - 1)
        self.assertEqual([c["limit"] for c in calls], [1000, 201])
        self.assertEqual(calls[0]["category"], "linear")
        self.assertEqual(calls[0]["interval"], "240")

    def test_closed_boundary_uses_server_clock(self):
        frame = p._rows_to_frame([row(NOW), row(NOW-STEP), row(NOW-2*STEP)],
                                 interval="240", server_time_ms=NOW)
        self.assertEqual(len(frame), 2)
        frame = p._rows_to_frame([row(NOW-STEP)], interval="240", server_time_ms=NOW-1)
        self.assertTrue(frame.empty)

    def test_short_history_and_duplicate_cleanup(self):
        with patch.object(p, "_get_retry", return_value=response([row(NOW-STEP)]*2)):
            frame = p.fetch_klines("BTCUSDT", p.ProviderConfig(bars=10))
        self.assertEqual(len(frame), 1)

    def test_repeated_page_raises(self):
        with patch.object(p, "PAGE_LIMIT", 2), patch.object(p, "_get_retry", return_value=response([row(NOW),row(NOW-STEP)])):
            with self.assertRaisesRegex(RuntimeError, "pagination"):
                p.fetch_klines("BTCUSDT", p.ProviderConfig(bars=3, sleep_between=0))

    def test_instruments_all_pages_and_filters(self):
        valid = {"symbol":"BTCUSDT", "status":"Trading", "contractType":"LinearPerpetual",
                 "settleCoin":"USDT", "quoteCoin":"USDT"}
        variants = [dict(valid, symbol="BAD1", status="PreLaunch"),
                    dict(valid, symbol="BAD2", contractType="LinearFutures"),
                    dict(valid, symbol="BAD3", settleCoin="USDC"),
                    dict(valid, symbol="BAD4", isPreListing=True)]
        pages = [{"result":{"list":[valid]+variants,"nextPageCursor":"next"}},
                 {"result":{"list":[dict(valid,symbol="ETHUSDT")],"nextPageCursor":""}}]
        calls=[]
        def fake(url, params, config):
            calls.append(dict(params)); return pages[len(calls)-1]
        with patch.object(p, "_get_retry", side_effect=fake), patch.object(p.time,"sleep"):
            self.assertEqual(p.fetch_perpetual_symbols(), {"BTCUSDT","ETHUSDT"})
        self.assertEqual(calls[1]["cursor"],"next")

    def test_rate_limit_retry_and_no_retry_on_403(self):
        with patch.object(p,"_get",side_effect=[{"retCode":10006},response([])]) as get, patch.object(p.time,"sleep"):
            self.assertEqual(p._get_retry(p.KLINES_URL,{},p.ProviderConfig())["retCode"],0)
            self.assertEqual(get.call_count,2)
        error=urllib.error.HTTPError(p.KLINES_URL,403,"Forbidden",{},None)
        with patch.object(p,"_get",side_effect=error) as get:
            with self.assertRaises(urllib.error.HTTPError):
                p._get_retry(p.KLINES_URL,{},p.ProviderConfig())
            self.assertEqual(get.call_count,1)

    def test_missing_server_time_fails_closed(self):
        with patch.object(p,"_get_retry",return_value={"result":{"list":[row(NOW)]}}):
            with self.assertRaises(KeyError):
                p.fetch_klines("BTCUSDT")

    def test_universe_maps_only_available_bybit_pairs(self):
        from crypto_scanner import universe as u
        ranking=[{"symbol":"btc","id":"bitcoin","name":"Bitcoin","market_cap":10},
                 {"symbol":"eth","id":"ethereum","name":"Ethereum","market_cap":5}]
        with patch.object(u,"fetch_ranking",return_value=ranking), patch.object(u,"fetch_perpetual_symbols",return_value={"BTCUSDT"}):
            coins=u.build_universe(exclude_stablecoins=False)
        self.assertEqual([c.exchange_symbol for c in coins],["BTCUSDT"])


class BTCAlertsTests(unittest.TestCase):
    def test_btc_individual_first_and_partial_failure_prefix(self):
        for symbol_attr, method, single, digest in (("ticker","send_signals","send_signal","_send_signal_digest"),
                                                   ("symbol","send_sweeps","send_sweep","_send_sweep_digest")):
            signals=[SimpleNamespace(**{symbol_attr:s},timeframe=tf,kind="bullish")
                     for s,tf in [("ETHUSDT","4h"),("SOLUSDT","1D"),("BTCUSDT","4h")]]
            client=T(dry_run=True)
            with patch.object(client,single,side_effect=[True,False]) as one, patch.object(client,digest) as batch:
                self.assertEqual(getattr(client,method)(signals),(1,2))
                self.assertIs(one.call_args_list[0].args[0],signals[2])
                batch.assert_not_called()
            with patch.object(client,single,return_value=True) as one, patch.object(client,digest,return_value=True) as batch:
                self.assertEqual(getattr(client,method)(signals),(3,0))
                self.assertEqual(one.call_count,2)
                self.assertEqual(batch.call_args.args[0],[signals[0]])

    def test_bitcoin_sweep_banner_and_bybit_chart(self):
        from tests.test_sweep import scenario
        from crypto_scanner.sweep import detect_sweep
        signal=detect_sweep(scenario("bull_sweep",0),"BTCUSDT","4h")
        client=T(dry_run=True)
        with patch.object(client,"send",return_value=True) as send:
            client.send_sweep(signal)
        text=send.call_args.args[0]
        self.assertIn("₿ BITCOIN",text)
        self.assertIn("BYBIT%3ABTCUSDT.P",text)
        self.assertNotIn("BTCUSDTUSDT",text)

    def test_bitcoin_divergence_banner_and_link(self):
        import pandas as pd
        pivot=SimpleNamespace(timestamp=pd.Timestamp("2026-09-01",tz="UTC"),value=100)
        signal=SimpleNamespace(ticker="BTCUSDT",timeframe="4h",kind="bullish_regular",
            first_pivot=pivot,second_pivot=pivot,first_rsi=25,second_rsi=30,distance_bars=10,
            confirmation_time=pivot.timestamp)
        client=T(dry_run=True)
        with patch.object(client,"send",return_value=True) as send:
            client.send_signal(signal)
        self.assertIn("₿ BITCOIN",send.call_args.args[0])
        self.assertIn("BYBIT%3ABTCUSDT.P",send.call_args.args[0])
        self.assertFalse(T._is_btc("WBTCUSDT"))
