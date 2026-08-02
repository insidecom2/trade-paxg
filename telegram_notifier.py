import asyncio
import logging
import os
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """
    Sends messages through the Telegram Bot API.
    Config is read from TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
    Fails soft: any send error is logged as a warning, never raised.
    """
    def __init__(self, token: str, chat_id: str, timeout: float = 10.0):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["TelegramNotifier"]:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            logger.warning(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; "
                "Telegram notifications disabled"
            )
            return None
        return cls(token, chat_id)

    async def send_message(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            return False
        url = TELEGRAM_API.format(token=self.token)
        payload = {"chat_id": self.chat_id, "text": text}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Telegram send failed: HTTP {resp.status}")
                        return False
                    return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"Telegram send failed: {e}")
            return False
