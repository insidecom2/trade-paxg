import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from telegram_notifier import TelegramNotifier


class FakeResponse:
    def __init__(self, status: int, body: str = ""):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def text(self):
        return self.body


class FakeRequest:
    def __init__(self, outcome):
        self.outcome = outcome

    async def __aenter__(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def __aexit__(self, exc_type, exc, tb):
        return None


class FakeSession:
    outcomes = []

    def __init__(self):
        self.outcome = self.outcomes.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def post(self, *args, **kwargs):
        return FakeRequest(self.outcome)


class TelegramNotifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        FakeSession.outcomes = []

    async def test_retries_transient_http_error(self):
        FakeSession.outcomes = [FakeResponse(503, '{"ok":false}'), FakeResponse(200)]
        notifier = TelegramNotifier("token", "chat", max_retries=2, retry_delay=0.25)

        with patch("telegram_notifier.aiohttp.ClientSession", FakeSession), patch(
            "telegram_notifier.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            self.assertTrue(await notifier.send_message("hello"))

        sleep.assert_awaited_once_with(0.25)

    async def test_does_not_retry_non_retryable_http_error(self):
        FakeSession.outcomes = [FakeResponse(401, '{"description":"Unauthorized"}')]
        notifier = TelegramNotifier("token", "chat", max_retries=2, retry_delay=0)

        with patch("telegram_notifier.aiohttp.ClientSession", FakeSession), patch(
            "telegram_notifier.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            self.assertFalse(await notifier.send_message("hello"))

        sleep.assert_not_awaited()
        self.assertEqual(FakeSession.outcomes, [])

    async def test_retries_exception_with_empty_message(self):
        FakeSession.outcomes = [aiohttp.ServerDisconnectedError(), FakeResponse(200)]
        notifier = TelegramNotifier("token", "chat", max_retries=1, retry_delay=0)

        with patch("telegram_notifier.aiohttp.ClientSession", FakeSession), patch(
            "telegram_notifier.asyncio.sleep", new_callable=AsyncMock
        ):
            with self.assertLogs("telegram_notifier", level="WARNING") as logs:
                self.assertTrue(await notifier.send_message("hello"))

        self.assertIn("ServerDisconnectedError", logs.output[0])


if __name__ == "__main__":
    unittest.main()
