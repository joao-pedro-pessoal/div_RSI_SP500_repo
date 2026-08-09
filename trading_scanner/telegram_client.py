from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DivergenceSignal


class TelegramClient:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        topic_id: str | None = None,
        *,
        dry_run: bool = False,
    ):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        # Grupos-forum (is_forum: true) tem topicos. Sem message_thread_id
        # o Telegram entrega SEMPRE no topico "General", mesmo com o chat_id
        # correto. O ID do topico esta no getUpdates, campo message_thread_id.
        # Opcional: em grupos normais e canais deixa-se vazio.
        self.topic_id = topic_id if topic_id is not None else os.getenv("TELEGRAM_TOPIC_ID")
        if self.topic_id is not None:
            self.topic_id = str(self.topic_id).strip() or None
        self.dry_run = dry_run
        if not self.dry_run and (not self.token or not self.chat_id):
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    def send(self, text: str) -> bool:
        if self.dry_run:
            print(text)
            return True
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload_fields = {"chat_id": self.chat_id, "text": text}
        if self.topic_id:
            payload_fields["message_thread_id"] = self.topic_id
        body = urllib.parse.urlencode(payload_fields).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("ok"))

    def send_signal(self, signal: "DivergenceSignal") -> bool:
        bullish = signal.kind == "bullish_regular"
        icon = "🟢" if bullish else "🔴"
        title = "BULLISH RSI DIVERGENCE" if bullish else "BEARISH RSI DIVERGENCE"
        price_label = "Low" if bullish else "High"
        price_arrow = "↓" if bullish else "↑"
        rsi_arrow = "↑" if bullish else "↓"
        chart_symbol = urllib.parse.quote(signal.ticker)
        text = (
            f"{icon} {title}\n"
            f"{signal.ticker} — {signal.timeframe}\n\n"
            f"{price_label} anterior ({signal.first_pivot.timestamp.date()}): ${signal.first_pivot.value:.2f}\n"
            f"Novo {price_label.lower()} ({signal.second_pivot.timestamp.date()}): "
            f"${signal.second_pivot.value:.2f} {price_arrow}\n\n"
            f"RSI anterior: {signal.first_rsi:.2f}\n"
            f"Novo RSI: {signal.second_rsi:.2f} {rsi_arrow}\n\n"
            f"Distância: {signal.distance_bars} candles\n"
            f"RSI alignment: {signal.rsi_alignment_mode}\n"
            f"Confirmado: {signal.confirmation_time.date()}\n"
            f"📊 https://www.tradingview.com/chart/?symbol={chart_symbol}"
        )
        return self.send(text)
