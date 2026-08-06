from __future__ import annotations

import os

from .telegram_client import TelegramClient


def main() -> None:
    if not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_CHAT_ID"):
        return
    client = TelegramClient()
    client.send("❌ O workflow dos scanners falhou antes de concluir. Consulta os logs do GitHub Actions.")


if __name__ == "__main__":
    main()

