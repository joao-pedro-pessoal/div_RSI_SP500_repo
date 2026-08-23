from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DivergenceSignal


class TelegramClient:
    def __init__(self, token: str | None = None, chat_id: str | None = None, *, dry_run: bool = False):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.dry_run = dry_run
        if not self.dry_run and (not self.token or not self.chat_id):
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    def send(self, text: str) -> bool:
        if self.dry_run:
            print(text)
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok"))

    @staticmethod
    def _fmt_price(value: float) -> str:
        """
        Crypto prices span many orders of magnitude. A fixed two decimals
        turns PENGU at $0.006357 into "$0.01", which is useless for judging
        a divergence. Precision scales with magnitude instead.
        """
        av = abs(value)
        if av >= 1000:
            return f"{value:,.2f}"
        if av >= 1:
            return f"{value:.4f}".rstrip("0").rstrip(".")
        if av >= 0.01:
            return f"{value:.5f}"
        if av >= 0.0001:
            return f"{value:.7f}"
        return f"{value:.9f}"

    def send_signal(self, signal: "DivergenceSignal") -> bool:
        bullish = signal.kind == "bullish_regular"
        icon = "\U0001F7E2" if bullish else "\U0001F534"
        title = "BULLISH RSI DIVERGENCE" if bullish else "BEARISH RSI DIVERGENCE"
        price_label = "Low" if bullish else "High"
        price_arrow = "\u2193" if bullish else "\u2191"
        rsi_arrow = "\u2191" if bullish else "\u2193"

        base = signal.ticker[:-4] if signal.ticker.endswith("USDT") else signal.ticker
        chart_symbol = urllib.parse.quote(f"BYBIT:{signal.ticker}.P")

        def stamp(ts) -> str:
            # Intraday timeframes need the hour; daily and above do not.
            return ts.strftime("%Y-%m-%d %H:%M") if signal.timeframe == "4h" else str(ts.date())

        text = (
            f"{icon} {title}\n"
            f"{base} \u2014 {signal.timeframe} \u2014 perp\n\n"
            f"{price_label} anterior ({stamp(signal.first_pivot.timestamp)}): "
            f"${self._fmt_price(signal.first_pivot.value)}\n"
            f"Novo {price_label.lower()} ({stamp(signal.second_pivot.timestamp)}): "
            f"${self._fmt_price(signal.second_pivot.value)} {price_arrow}\n\n"
            f"RSI anterior: {signal.first_rsi:.2f}\n"
            f"Novo RSI: {signal.second_rsi:.2f} {rsi_arrow}\n\n"
            f"Dist\u00e2ncia: {signal.distance_bars} candles\n"
            f"Confirmado: {stamp(signal.confirmation_time)}\n"
            f"\U0001F4CA https://www.tradingview.com/chart/?symbol={chart_symbol}"
        )
        return self.send(text)
