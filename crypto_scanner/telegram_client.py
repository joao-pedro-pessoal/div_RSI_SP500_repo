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
        # disable_web_page_preview: sem isto, cada alerta arrasta uma
        # pre-visualizacao enorme do TradingView que ocupa mais ecra que a
        # propria mensagem e torna uma lista de alertas ilegivel.
        fields = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
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

    # Duracao de cada timeframe, para calcular ha quanto tempo a vela fechou.
    TF_MINUTES = {"1h": 60, "4h": 240, "1D": 1440, "3D": 4320, "1W": 10080}

    @classmethod
    def _candle_age(cls, open_time, timeframe: str) -> str:
        """
        Ha quanto tempo esta vela FECHOU.

        Vai na mensagem para a frescura ser visivel sem ter de fazer contas:
        um sinal de "ha 4 min" e outro de "ha 3h" merecem atencao diferente,
        e antes nao havia forma de os distinguir de relance.
        """
        minutes = cls.TF_MINUTES.get(timeframe)
        if minutes is None:
            return ""
        import datetime as _dt
        close_time = open_time + _dt.timedelta(minutes=minutes)
        now = _dt.datetime.now(_dt.timezone.utc)
        if close_time.tzinfo is None:
            close_time = close_time.replace(tzinfo=_dt.timezone.utc)
        age = (now - close_time).total_seconds() / 60.0
        if age < 0:
            return "a fechar"
        if age < 90:
            return f"há {age:.0f} min"
        if age < 48 * 60:
            return f"há {age / 60:.1f}h"
        return f"há {age / 1440:.1f}d"

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
            f"Confirmado: {stamp(signal.confirmation_time)} "
            f"({self._candle_age(signal.confirmation_time, signal.timeframe)})\n"
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
            f"Vela: {stamp(sweep.sweep_time)} ({self._candle_age(sweep.sweep_time, sweep.timeframe)})\n"
            f"\U0001F4CA https://www.tradingview.com/chart/?symbol={chart_symbol}"
        )
        return self.send(text)

    # -----------------------------------------------------------------
    # ENVIO AGRUPADO
    # -----------------------------------------------------------------
    #
    # PORQUE EXISTE
    #   Medido em producao: 73 alertas individuais demoraram ~53 minutos a
    #   sair -- 41 segundos por mensagem, contra os 3.5s do throttle. A
    #   diferenca sao esperas de 429: o Telegram limita a ~20 mensagens por
    #   minuto para o mesmo grupo, e o job era morto pelo timeout antes de
    #   acabar, perdendo os alertas que faltavam.
    #
    #   Agrupar 10 sinais por mensagem transforma 73 envios em 8. O problema
    #   deixa de existir em vez de ser mitigado.
    #
    # O QUE NAO E AGRUPADO
    #   Os timeframes altos (1D/3D/1W). Sao raros e o objetivo e que se
    #   destaquem -- meter um sinal de 1W no meio de uma lista de dez
    #   anularia o destaque que acabamos de acrescentar.

    BATCH_SIZE = 10

    def send_sweeps(self, sweeps: list) -> tuple[int, int]:
        """Envia uma lista de varrimentos. Devolve (enviados, falhados)."""
        if not sweeps:
            return 0, 0

        individuais = [s for s in sweeps if self._is_high_tf(s.timeframe)]
        agrupaveis = [s for s in sweeps if not self._is_high_tf(s.timeframe)]

        enviados = 0
        falhados = 0

        for sweep in individuais:
            if self.send_sweep(sweep):
                enviados += 1
            else:
                falhados += 1

        for start in range(0, len(agrupaveis), self.BATCH_SIZE):
            grupo = agrupaveis[start:start + self.BATCH_SIZE]
            if self._send_sweep_digest(grupo):
                enviados += len(grupo)
            else:
                falhados += len(grupo)

        return enviados, falhados

    def _send_sweep_digest(self, sweeps: list) -> bool:
        timeframes = sorted({s.timeframe for s in sweeps})
        idade = self._candle_age(sweeps[0].sweep_time, sweeps[0].timeframe)
        header = (f"\U0001F30A <b>{len(sweeps)} varrimento"
                  f"{'s' if len(sweeps) > 1 else ''}</b> \u2014 {', '.join(timeframes)}"
                  f"{' · ' + idade if idade else ''}")

        linhas = [header, ""]
        for sweep in sweeps:
            bullish = sweep.kind == "bullish_sweep"
            icon = "\U0001F7E2" if bullish else "\U0001F534"
            base = self._esc(self._base_asset(sweep.symbol))
            quando = (sweep.sweep_time.strftime("%d/%m %H:%M")
                      if sweep.timeframe in ("1h", "4h") else str(sweep.sweep_time.date()))
            chart = urllib.parse.quote(f"OKX:{self._base_asset(sweep.symbol)}USDT.P")
            linhas.append(
                f'{icon} <a href="https://www.tradingview.com/chart/?symbol={chart}">'
                f"<b>{base}</b></a> {sweep.timeframe}  "
                f"{sweep.depth_atr:.2f} ATR · pavio {sweep.wick_fraction * 100:.0f}% · {quando}"
            )
        return self.send("\n".join(linhas))

    def send_signals(self, signals: list) -> tuple[int, int]:
        """Equivalente para divergencias de RSI."""
        if not signals:
            return 0, 0

        individuais = [s for s in signals if self._is_high_tf(s.timeframe)]
        agrupaveis = [s for s in signals if not self._is_high_tf(s.timeframe)]

        enviados = 0
        falhados = 0

        for signal in individuais:
            if self.send_signal(signal):
                enviados += 1
            else:
                falhados += 1

        for start in range(0, len(agrupaveis), self.BATCH_SIZE):
            grupo = agrupaveis[start:start + self.BATCH_SIZE]
            if self._send_signal_digest(grupo):
                enviados += len(grupo)
            else:
                falhados += len(grupo)

        return enviados, falhados

    def _send_signal_digest(self, signals: list) -> bool:
        timeframes = sorted({s.timeframe for s in signals})
        idade = self._candle_age(signals[0].confirmation_time, signals[0].timeframe)
        header = (f"\U0001F4C9 <b>{len(signals)} diverg\u00eancia"
                  f"{'s' if len(signals) > 1 else ''}</b> \u2014 {', '.join(timeframes)}"
                  f"{' · ' + idade if idade else ''}")

        linhas = [header, ""]
        for signal in signals:
            bullish = signal.kind == "bullish_regular"
            icon = "\U0001F7E2" if bullish else "\U0001F534"
            base = self._esc(self._base_asset(signal.ticker))
            quando = (signal.confirmation_time.strftime("%d/%m %H:%M")
                      if signal.timeframe == "4h" else str(signal.confirmation_time.date()))
            chart = urllib.parse.quote(f"OKX:{self._base_asset(signal.ticker)}USDT.P")
            linhas.append(
                f'{icon} <a href="https://www.tradingview.com/chart/?symbol={chart}">'
                f"<b>{base}</b></a> {signal.timeframe}  "
                f"RSI {signal.first_rsi:.0f}\u2192{signal.second_rsi:.0f} · {quando}"
            )
        return self.send("\n".join(linhas))
