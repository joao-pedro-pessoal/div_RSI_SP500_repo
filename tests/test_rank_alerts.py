import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from crypto_scanner import universe as u
from crypto_scanner.models import Pivot, DivergenceSignal
from crypto_scanner.telegram_client import TelegramClient as T


def divergence(symbol, timeframe="4h"):
    stamp = pd.Timestamp("2026-09-01", tz="UTC")
    first = Pivot(10, stamp, 100, "low")
    second = Pivot(20, stamp + pd.Timedelta(days=2), 98, "low")
    return DivergenceSignal(symbol, timeframe, "bullish_regular", first, second,
        25, 30, stamp, second.timestamp, "tradingview", 10, 25, second.timestamp)


def sweep(symbol, timeframe="4h"):
    return SimpleNamespace(symbol=symbol, timeframe=timeframe, kind="bullish_sweep",
        origin_atr=0.5, swept_level=100, sweep_extreme=98, depth_atr=1.2,
        wick_fraction=0.7, close_position=0.8, n_pivots=3, trend_atr=3.2,
        sweep_time=pd.Timestamp("2026-09-01", tz="UTC"), signal_id=f"{symbol}|{timeframe}")


class RankAlertTests(unittest.TestCase):
    def setUp(self):
        self.client = T(dry_run=True)
        self.client.set_market_cap_ranks({"BTC-USDT-SWAP":1,"ETH-USDT-SWAP":2,
            "TEN-USDT-SWAP":10,"ELEVEN-USDT-SWAP":11,"FIFTY-USDT-SWAP":50,
            "SMALL-USDT-SWAP":51})

    def test_boundaries_and_unknown(self):
        for symbol,tier in [("BTC-USDT-SWAP",0),("ETH-USDT-SWAP",1),
                            ("TEN-USDT-SWAP",1),("ELEVEN-USDT-SWAP",2),
                            ("FIFTY-USDT-SWAP",2),("SMALL-USDT-SWAP",3),
                            ("UNKNOWN-USDT-SWAP",3),("WBTC-USDT-SWAP",3)]:
            self.assertEqual(self.client._tier(symbol),tier)
        self.assertEqual(self.client._tier("BTCUSDT"),0)

    def test_invalid_rank_not_promoted(self):
        self.client.set_market_cap_ranks({"X":0,"Y":-1,"Z":None,"W":True,"A":"2"})
        self.assertEqual(self.client.market_cap_ranks,{})
        self.assertEqual(self.client._rank_label("X"),"")

    def test_individual_messages_links_labels_and_source(self):
        for factory,method in [(divergence,"send_signal"),(sweep,"send_sweep")]:
            for symbol,label in [("BTC-USDT-SWAP","₿ BITCOIN"),("ETH-USDT-SWAP","🥇 TOP 10"),
                                 ("FIFTY-USDT-SWAP","🔷 TOP 50")]:
                with patch.object(self.client,"send",return_value=True) as send:
                    getattr(self.client,method)(factory(symbol))
                text=send.call_args.args[0]
                self.assertIn(label,text)
                self.assertIn("Data: OKX · Chart: Bybit",text)
                self.assertIn("BYBIT%3A",text)
                self.assertNotIn("symbol=OKX",text)
                self.assertEqual(text.count("<b>"),text.count("</b>"))
            with patch.object(self.client,"send",return_value=True) as send:
                getattr(self.client,method)(factory("BTC-USDT-SWAP","1D"))
            self.assertIn("HIGH TIMEFRAME",send.call_args.args[0])
            self.assertIn("₿ BITCOIN",send.call_args.args[0])

    def test_chart_quote_once_and_encoded(self):
        for symbol in ("BTC", "BTCUSDT", "BTC-USDT-SWAP"):
            self.assertEqual(T._chart_symbol(symbol),"BYBIT%3ABTCUSDT.P")

    def test_order_separate_tier_batches_and_failed_prefix(self):
        for factory,method,one,digest in [(divergence,"send_signals","send_signal","_send_signal_digest"),
                                         (sweep,"send_sweeps","send_sweep","_send_sweep_digest")]:
            signals=[factory(x) for x in ("SMALL-USDT-SWAP","FIFTY-USDT-SWAP", "ETH-USDT-SWAP",
                                         "BTC-USDT-SWAP","ELEVEN-USDT-SWAP")]
            calls=[]
            def individual(s): calls.append([self.client._signal_symbol(s)]); return True
            def batch(ss): calls.append([self.client._signal_symbol(s) for s in ss]); return True
            with patch.object(self.client,one,side_effect=individual),patch.object(self.client,digest,side_effect=batch):
                self.assertEqual(getattr(self.client,method)(signals),(5,0))
            self.assertEqual(calls,[["BTC-USDT-SWAP"],["ETH-USDT-SWAP"],
                ["ELEVEN-USDT-SWAP","FIFTY-USDT-SWAP"],["SMALL-USDT-SWAP"]])
            with patch.object(self.client,one,return_value=True),patch.object(self.client,digest,return_value=False) as send:
                self.assertEqual(getattr(self.client,method)(signals),(2,3))
                self.assertEqual(send.call_count,1)

    def test_top50_digest_labels_for_both_scanners(self):
        for factory,method in [(divergence,"send_signals"),(sweep,"send_sweeps")]:
            with patch.object(self.client,"send",return_value=True) as send:
                getattr(self.client,method)([factory("FIFTY-USDT-SWAP"),factory("ELEVEN-USDT-SWAP")])
            self.assertEqual(send.call_count,1)
            text=send.call_args.args[0]
            self.assertIn("🔷 TOP 50 · #11",text)
            self.assertIn("🔷 TOP 50 · #50",text)
            self.assertIn("BYBIT%3AFIFTYUSDT.P",text)

    def test_batch_limit_and_high_tf_boundary(self):
        signals=[divergence(f"C{i}-USDT-SWAP") for i in range(12)]
        signals.append(divergence("HIGH-USDT-SWAP","1D"))
        with patch.object(self.client,"send_signal",return_value=True) as one, \
             patch.object(self.client,"_send_signal_digest",return_value=True) as batch:
            self.assertEqual(self.client.send_signals(signals),(13,0))
            self.assertEqual(one.call_count,1)
            self.assertEqual([len(c.args[0]) for c in batch.call_args_list],[10,2])


class UniverseRankTests(unittest.TestCase):
    def test_global_rank_survives_filtering_and_snapshot(self):
        rows=[{"id":"tether","symbol":"usdt","name":"Tether","market_cap_rank":3},
              {"id":"missing","symbol":"missing","name":"Missing","market_cap_rank":4},
              {"id":"eleven","symbol":"eleven","name":"Eleven","market_cap_rank":11},
              {"id":"unknown","symbol":"unknown","name":"Unknown","market_cap_rank":None}]
        with tempfile.TemporaryDirectory() as d, patch.object(u,"fetch_ranking",return_value=rows), \
             patch.object(u,"fetch_stablecoin_ids",return_value={"tether"}), \
             patch.object(u,"fetch_perpetual_symbols",return_value={"ELEVEN-USDT-SWAP","UNKNOWN-USDT-SWAP"}):
            coins=u.build_universe(snapshot_dir=Path(d))
            saved=json.loads(next(Path(d).glob("*.json")).read_text())
        self.assertEqual(coins[0].rank,1)
        self.assertEqual(coins[0].market_cap_rank,11)
        self.assertIsNone(coins[1].market_cap_rank)
        self.assertEqual(saved["coins"][0]["market_cap_rank"],11)


class PipelineRankTests(unittest.TestCase):
    def test_both_mains_pass_rank_and_persist_only_delivered_btc(self):
        import main_crypto
        import main_sweep
        for module, factory, scanner_class in [(main_crypto,divergence,"CryptoDivergenceScanner"),
                                                (main_sweep,sweep,"SweepScanner")]:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as d:
                import yaml
                config={"scanner":{"timeframes":["4h"]},"telegram":{"send_start_notice":False,"send_heartbeat":False},
                        "state_file":f"{d}/signals.json","heartbeat_file":f"{d}/heartbeat.json"}
                path=Path(d)/"config.yaml";path.write_text(yaml.safe_dump(config))
                coins=[u.Coin(1,"ethereum","ETH","Ethereum",1,"ETH-USDT-SWAP",2),
                       u.Coin(2,"bitcoin","BTC","Bitcoin",2,"BTC-USDT-SWAP",1)]
                def report(symbol,*args,**kwargs):
                    return SimpleNamespace(signals=[factory(symbol)],errors={},skipped=[])
                messages=[]
                def send(client,text):
                    messages.append(text)
                    return len(messages)==1
                with patch("sys.argv",[module.__name__,"--config",str(path)]), \
                     patch.dict("os.environ",{"TELEGRAM_BOT_TOKEN":"test","TELEGRAM_CHAT_ID":"test"}), \
                     patch.object(module,"build_universe",return_value=coins), \
                     patch.object(module,"fetch_many",return_value=({c.exchange_symbol:object() for c in coins},[])), \
                     patch.object(getattr(module,scanner_class),"scan_symbol",side_effect=report), \
                     patch.object(T,"send",new=send),redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(),0)
                self.assertIn("₿ BITCOIN",messages[0])
                self.assertIn("🥇 TOP 10",messages[1])
                state=json.loads(Path(config["state_file"]).read_text())
                self.assertIn("BTC-USDT-SWAP",json.dumps(state))
                self.assertNotIn("ETH-USDT-SWAP",json.dumps(state))
