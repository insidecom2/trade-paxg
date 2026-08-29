import unittest
from datetime import date
from unittest.mock import patch

import requests

from news_calendar_client import NewsCalendarClient


def make_response(payload, status=200):
    class FakeResponse:
        def raise_for_status(self):
            if status >= 400:
                raise requests.HTTPError(f"HTTP {status}")

        def json(self):
            return payload

    return FakeResponse()


class NewsCalendarClientTests(unittest.TestCase):
    def test_filters_to_usd_high_impact_events_today_in_bangkok(self):
        payload = [
            # 2026-08-30T21:00:00-04:00 == 2026-08-31 08:00 Bangkok -> not today
            {"title": "Late one", "country": "USD", "impact": "High",
             "date": "2026-08-30T21:00:00-04:00", "forecast": "1.0%", "previous": "0.9%"},
            # 2026-08-30T08:30:00-04:00 == 2026-08-30 19:30 Bangkok -> today
            {"title": "Core PCE Price Index m/m", "country": "USD", "impact": "High",
             "date": "2026-08-30T08:30:00-04:00", "forecast": "0.2%", "previous": "0.1%"},
            {"title": "Low impact USD", "country": "USD", "impact": "Low",
             "date": "2026-08-30T08:30:00-04:00", "forecast": "", "previous": ""},
            {"title": "High impact EUR", "country": "EUR", "impact": "High",
             "date": "2026-08-30T08:30:00-04:00", "forecast": "", "previous": ""},
        ]
        client = NewsCalendarClient()
        with patch.object(client, "_fetch_sync", return_value=payload):
            events = client.fetch_todays_usd_high_impact_events(today_bangkok=date(2026, 8, 30))

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Core PCE Price Index m/m")
        self.assertEqual(events[0].forecast, "0.2%")

    def test_returns_empty_list_on_request_failure(self):
        client = NewsCalendarClient()
        with patch.object(client, "_fetch_sync", side_effect=requests.ConnectionError("boom")):
            events = client.fetch_todays_usd_high_impact_events(today_bangkok=date(2026, 8, 30))
        self.assertEqual(events, [])

    def test_returns_empty_list_on_rate_limit(self):
        client = NewsCalendarClient()

        def raise_429():
            raise requests.HTTPError("429 rate limited")

        with patch.object(client, "_fetch_sync", side_effect=raise_429):
            events = client.fetch_todays_usd_high_impact_events(today_bangkok=date(2026, 8, 30))
        self.assertEqual(events, [])

    def test_skips_events_with_unparseable_dates(self):
        payload = [
            {"title": "Bad date", "country": "USD", "impact": "High",
             "date": "not-a-date", "forecast": "", "previous": ""},
        ]
        client = NewsCalendarClient()
        with patch.object(client, "_fetch_sync", return_value=payload):
            events = client.fetch_todays_usd_high_impact_events(today_bangkok=date(2026, 8, 30))
        self.assertEqual(events, [])

    def test_returns_empty_list_when_no_today_bangkok_given_and_no_match(self):
        # Sanity check the default-today path doesn't raise even with an
        # empty feed.
        client = NewsCalendarClient()
        with patch.object(client, "_fetch_sync", return_value=[]):
            events = client.fetch_todays_usd_high_impact_events()
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
