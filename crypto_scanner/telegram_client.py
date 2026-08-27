from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DivergenceSignal


class TelegramClient:
    # O Telegram limita a ~20 mensagens/minuto para o mesmo grupo.
    MIN_INTERVAL_SECONDS = 3.5
    MAX_RETRIES = 4

    _last_send_at = 0.0

    def __init__(self, token: str | None = None, chat_id: str | None = None,
                 topic_id: str | None = None, *, dry_run: bool = False):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        # Grupos-forum (is_forum: true) tem topicos. SEM message_thread_id o
        # Telegram entrega sempre no topico "General", mesmo com o chat_id
        # correto -- e sem erro nenhum, o que torna a falha silenciosa.
        self.topic_id = topic_id if topic_id is not None else os.getenv("TELEGRAM_TOPIC_ID")
        if self.topic_id is not None:
            self.topic_id = str(self.topic_id).strip() or None
        self.dry_run = dry_run
        if not self.dry_run and (not self.token or not self.chat_id):
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

    def send(self, text: str) -> bool:
        """Devolve False se nao conseguiu enviar; NUNCA levanta excecao."""
        if self.dry_run:
            print(text)
            return True

        elapsed = time.monotonic() - TelegramClient._last_send_at
        if elapsed < self.MIN_INTERVAL_SECONDS:
            time.sleep(self.MIN_INTERVAL_SECONDS - elapsed)

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # HTML permite negrito, o que e o que distingue visualmente um sinal
        # de 1W de um de 4h numa lista longa. Os valores interpolados sao
        # escapados na montagem da mensagem, nao aqui.
        fields = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        if self.topic_id:
            fields["message_thread_id"] = self.topic_id
        body = urllib.parse.urlencode(fields).encode("utf-8")

        for attempt in range(self.MAX_RETRIES):
            try:
                request = urllib.request.Request(url, data=body, method="POST")
                with urllib.request.urlopen(request, timeout=20) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                TelegramClient._last_send_at = time.monotonic()
                return bool(payload.get("ok"))
            except urllib.error.HTTPError as exc:
                TelegramClient._last_send_at = time.monotonic()
                # 400 com parse_mode = HTML mal formado. Reenviar em texto
                # simples e melhor que perder o alerta: a formatacao e
                # cosmetica, o sinal nao.
                if exc.code == 400 and fields.get("parse_mode"):
                    print("[telegram] HTTP 400 com HTML; a reenviar em texto simples")
                    plain = dict(fields)
                    plain.pop("parse_mode", None)
                    plain["text"] = re.sub(r"</?[a-zA-Z]+>", "", plain["text"])
                    try:
                        retry = urllib.request.Request(
                            url, data=urllib.parse.urlencode(plain).encode("utf-8"),
                            method="POST")
                        with urllib.request.urlopen(retry, timeout=20) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        return bool(payload.get("ok"))
                    except Exception:
                        return False
                if exc.code == 429:
                    wait = 30
                    try:
                        detail = json.loads(exc.read().decode("utf-8"))
                        wait = int(detail.get("parameters", {}).get("retry_after", wait))
                    except Exception:
                        pass
                    wait = min(wait, 90)
                    print(f"[telegram] 429; a esperar {wait}s ({attempt + 1}/{self.MAX_RETRIES})")
                    time.sleep(wait + 1)
                    continue
                print(f"[telegram] HTTP {exc.code}: envio falhou")
                return False
            except Exception as exc:
                TelegramClient._last_send_at = time.monotonic()
                print(f"[telegram] {type(exc).__name__}: envio falhou")
                return False
        print("[telegram] desisti apos varias tentativas")
        return False

    # Timeframes raros. Um sinal de 1W aparece poucas vezes por mes e nao
    # pode passar despercebido entre dezenas de sinais de 4h no mesmo topico.
    HIGH_TIMEFRAMES = ("1D", "3D", "1W")

    @classmethod
    def _is_high_tf(cls, timeframe: str) -> bool:
        return timeframe in cls.HIGH_TIMEFRAMES

    @staticmethod
    def _esc(value) -> str:
        """Escapa para HTML. Precos e simbolos sao seguros, mas escapar
        evita que um simbolo invulgar parta a mensagem inteira."""
        return html.escape(str(value), quote=False)

    @classmethod
    def _banner(cls, timeframe: str, bullish: bool) -> str:
        """Faixa de destaque para os timeframes altos."""
        if not cls._is_high_tf(timeframe):
            return ""
        mark = "\U0001F535" if bullish else "\U0001F7E0"   # circulos grandes
        band = mark * 5
        return f"{band}\n<b>\u2b50 {timeframe} \u2014 TIMEFRAME ALTO \u2b50</b>\n{band}\n\n"

    @staticmethod
    def _base_asset(symbol: str) -> str:
        """
        Extrai o ativo base de um simbolo de exchange.

        OKX usa "BTC-USDT-SWAP"; outras usam "BTCUSDT". Sem tratar os dois,
        um simbolo sem tracos produzia "BTCUSDTUSDT.P" no link do
        TradingView -- um link que abre um grafico inexistente.
        """
        base = symbol.split("-")[0].upper()
        for quote in ("USDT", "USDC", "USD"):
            if base.endswith(quote) and len(base) > len(quote):
                return base[: -len(quote)]
        return base

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
        high_tf = self._is_high_tf(signal.timeframe)

        base = self._esc(self._base_asset(signal.ticker))
        chart_symbol = urllib.parse.quote(f"OKX:{self._base_asset(signal.ticker)}USDT.P")

        def stamp(ts) -> str:
            return ts.strftime("%Y-%m-%d %H:%M") if signal.timeframe == "4h" else str(ts.date())

        head = f"<b>{icon} {title}</b>" if high_tf else f"{icon} {title}"
        pair = f"<b>{base} \u2014 {signal.timeframe}</b>" if high_tf else f"{base} \u2014 {signal.timeframe}"

        text = (
            f"{self._banner(signal.timeframe, bullish)}"
            f"{head}\n"
            f"{pair} \u2014 perp\n\n"
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

    def send_sweep(self, sweep) -> bool:
        """Alerta de varrimento de liquidez."""
        bullish = sweep.kind == "bullish_sweep"
        icon = "\U0001F7E2" if bullish else "\U0001F534"
        title = "BULLISH SWEEP" if bullish else "BEARISH SWEEP"
        high_tf = self._is_high_tf(sweep.timeframe)

        base = self._esc(self._base_asset(sweep.symbol))
        chart_symbol = urllib.parse.quote(f"OKX:{self._base_asset(sweep.symbol)}USDT.P")

        def stamp(ts) -> str:
            return ts.strftime("%Y-%m-%d %H:%M") if sweep.timeframe in ("1h", "4h") else str(ts.date())

        origem = (f"passou a origem em {sweep.origin_atr:.2f} ATR"
                  if sweep.origin_atr >= 0
                  else f"ficou a {abs(sweep.origin_atr):.2f} ATR da origem")

        head = f"<b>{icon} {title}</b>" if high_tf else f"{icon} {title}"
        pair = f"<b>{base} \u2014 {sweep.timeframe}</b>" if high_tf else f"{base} \u2014 {sweep.timeframe}"

        text = (
            f"{self._banner(sweep.timeframe, bullish)}"
            f"{head}\n"
            f"{pair} \u2014 perp\n\n"
            f"N\u00edvel varrido: ${self._fmt_price(sweep.swept_level)}\n"
            f"Extremo da vela: ${self._fmt_price(sweep.sweep_extreme)}\n"
            f"Profundidade: {sweep.depth_atr:.2f} ATR\n\n"
            f"Pavio: {sweep.wick_fraction * 100:.0f}% da vela\n"
            f"Fecho: {sweep.close_position * 100:.0f}% (topo da vela)\n\n"
            f"Tend\u00eancia: {sweep.n_pivots} pivots, {sweep.trend_atr:.2f} ATR\n"
            f"{origem}\n\n"
            f"Vela: {stamp(sweep.sweep_time)}\n"
            f"\U0001F4CA https://www.tradingview.com/chart/?symbol={chart_symbol}"
        )
        return self.send(text)
