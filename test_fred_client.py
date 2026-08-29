import os
import unittest
from unittest.mock import patch

from fred_client import FredClient, TRACKED_SERIES


class FredClientTests(unittest.IsolatedAsyncioTestCase):
    def test_from_env_returns_none_without_api_key(self):
        with patch.dict(os.environ, {"FRED_API_KEY": ""}, clear=True):
            self.assertIsNone(FredClient.from_env())

    def test_from_env_returns_client_with_api_key(self):
        with patch.dict(os.environ, {"FRED_API_KEY": "key"}, clear=True):
            client = FredClient.from_env()
        self.assertIsNotNone(client)
        self.assertEqual(client.api_key, "key")

    async def test_fetch_latest_released_skips_failed_series_and_keeps_others(self):
        client = FredClient(api_key="key")

        def fake_fetch(series_id):
            if series_id == "UNRATE":
                return None  # simulates a failed/empty lookup for one series
            return object()

        with patch.object(client, "_fetch_latest_sync", side_effect=fake_fetch):
            results = await client.fetch_latest_released()

        # one series (UNRATE) returned None and must be dropped, not raise
        self.assertEqual(len(results), len(TRACKED_SERIES) - 1)

    def test_fetch_latest_sync_parses_observation(self):
        client = FredClient(api_key="key")

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"observations": [{"date": "2026-07-01", "value": "332.813"}]}

        with patch("fred_client.requests.get", return_value=FakeResponse()):
            point = client._fetch_latest_sync("CPIAUCSL")

        self.assertIsNotNone(point)
        self.assertEqual(point.indicator, TRACKED_SERIES["CPIAUCSL"])
        self.assertEqual(point.period, "2026-07-01")
        self.assertEqual(point.value, 332.813)

    def test_fetch_latest_sync_returns_none_on_non_numeric_value(self):
        client = FredClient(api_key="key")

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"observations": [{"date": "2026-07-01", "value": "."}]}  # FRED's "no data" marker

        with patch("fred_client.requests.get", return_value=FakeResponse()):
            point = client._fetch_latest_sync("CPIAUCSL")

        self.assertIsNone(point)

    def test_fetch_latest_sync_returns_none_on_request_error(self):
        import requests

        client = FredClient(api_key="key")
        with patch("fred_client.requests.get", side_effect=requests.ConnectionError("boom")):
            point = client._fetch_latest_sync("CPIAUCSL")

        self.assertIsNone(point)


if __name__ == "__main__":
    unittest.main()
