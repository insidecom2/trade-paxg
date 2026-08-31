import os
import unittest
from unittest.mock import Mock, patch

import requests

from exchange_manager import (
    TwelveDataManager,
    create_market_data_manager,
)


class ExchangeManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_source_factory_defaults_to_binance(self):
        with patch.dict(os.environ, {}, clear=True):
            manager = create_market_data_manager()

        self.assertEqual(manager.__class__.__name__, "BinanceManager")

    def test_source_factory_rejects_unknown_source(self):
        with patch.dict(os.environ, {"PRICE_SOURCE": "other"}, clear=True):
            with self.assertRaisesRegex(ValueError, "PRICE_SOURCE"):
                create_market_data_manager()

    def test_source_factory_accepts_explicit_source_override(self):
        # AI_PRICE_SOURCE should be able to pick twelvedata regardless of
        # what PRICE_SOURCE is set to for the strategy/liquidity-sweep
        # pipelines.
        with patch.dict(
            os.environ, {"PRICE_SOURCE": "binance", "TWELVEDATA_API_KEY": "key"}, clear=True
        ):
            manager = create_market_data_manager("twelvedata")

        self.assertEqual(manager.__class__.__name__, "TwelveDataManager")

    def test_twelvedata_manager_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TWELVEDATA_API_KEY"):
                TwelveDataManager()

    def test_twelvedata_manager_retries_timeout_then_returns_payload(self):
        payload = {"status": "ok", "values": []}
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload

        with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "key"}, clear=True):
            manager = TwelveDataManager()
            with (
                patch("exchange_manager.requests.get", side_effect=[
                    requests.ReadTimeout("slow response"), response,
                ]) as request_get,
                patch("exchange_manager.time.sleep") as sleep,
            ):
                actual = manager._fetch_sync("XAU/USD", "1h", 250)

        self.assertEqual(actual, payload)
        self.assertEqual(request_get.call_count, 2)
        sleep.assert_called_once_with(1.0)

    def test_twelvedata_manager_does_not_retry_client_error(self):
        response = Mock()
        response.status_code = 401
        error = requests.HTTPError("unauthorized", response=response)

        with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "key"}, clear=True):
            manager = TwelveDataManager()
            with (
                patch("exchange_manager.requests.get", side_effect=error) as request_get,
                patch("exchange_manager.time.sleep") as sleep,
            ):
                with self.assertRaisesRegex(requests.HTTPError, "unauthorized"):
                    manager._fetch_sync("XAU/USD", "1h", 250)

        self.assertEqual(request_get.call_count, 1)
        sleep.assert_not_called()

    def test_twelvedata_manager_retries_rate_limit(self):
        rate_limited_response = Mock(status_code=429)
        rate_limited = requests.HTTPError("rate limited", response=rate_limited_response)
        payload = {"status": "ok", "values": []}
        success_response = Mock()
        success_response.raise_for_status.return_value = None
        success_response.json.return_value = payload

        with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "key"}, clear=True):
            manager = TwelveDataManager()
            with (
                patch("exchange_manager.requests.get", side_effect=[
                    rate_limited, success_response,
                ]) as request_get,
                patch("exchange_manager.time.sleep") as sleep,
            ):
                actual = manager._fetch_sync("XAU/USD", "4h", 250)

        self.assertEqual(actual, payload)
        self.assertEqual(request_get.call_count, 2)
        sleep.assert_called_once_with(1.0)

    async def test_twelvedata_manager_parses_candles_oldest_first(self):
        payload = {
            "status": "ok",
            "values": [
                {"datetime": "2026-08-29 10:00:00", "open": "2", "high": "3", "low": "1", "close": "2.5"},
                {"datetime": "2026-08-29 09:00:00", "open": "1", "high": "2", "low": "0.5", "close": "1.8"},
            ],
        }
        with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "key"}, clear=True):
            manager = TwelveDataManager()
            with patch.object(manager, "_fetch_sync", return_value=payload):
                candles = await manager.fetch_ohlcv("XAU/USD", "1h", limit=2)

        self.assertEqual(len(candles), 2)
        self.assertLess(candles[0].timestamp, candles[1].timestamp)
        self.assertEqual(candles[1].close, 2.5)
        self.assertEqual(candles[0].volume, 0.0)

    async def test_twelvedata_manager_raises_on_error_status(self):
        with patch.dict(os.environ, {"TWELVEDATA_API_KEY": "key"}, clear=True):
            manager = TwelveDataManager()
            with patch.object(manager, "_fetch_sync", return_value={"status": "error", "message": "bad symbol"}):
                with self.assertRaisesRegex(RuntimeError, "Twelve Data error"):
                    await manager.fetch_ohlcv("BAD/SYM", "1h")


if __name__ == "__main__":
    unittest.main()
