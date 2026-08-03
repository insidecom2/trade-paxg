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
    def __init__(
        self,
        token: str,
        chat_id: str,
        timeout: float = 10.0,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_delay = max(0.0, retry_delay)

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
        total_attempts = self.max_retries + 1
        for attempt in range(total_attempts):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ) as resp:
                        if resp.status == 200:
                            return True

                        detail = (await resp.text()).strip()
                        if len(detail) > 500:
                            detail = detail[:500] + "..."
                        retryable = resp.status == 429 or resp.status >= 500
                        logger.warning(
                            "Telegram send failed (attempt %d/%d): HTTP %s%s",
                            attempt + 1,
                            total_attempts,
                            resp.status,
                            f": {detail}" if detail else "",
                        )
                        if not retryable:
                            return False
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                error_detail = str(e) or repr(e)
                logger.warning(
                    "Telegram send failed (attempt %d/%d): %s (%s)",
                    attempt + 1,
                    total_attempts,
                    error_detail,
                    type(e).__name__,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(self.retry_delay * (2**attempt))

        return False
