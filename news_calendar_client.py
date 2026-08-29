"""Thin wrapper around a public forward-looking economic calendar feed.

There is no official free API for a forex economic calendar with
actual/forecast/previous + timing (verified during evaluation: Finnhub and
Financial Modeling Prep both gate that behind a paid tier). This uses the
JSON feed that powers Forex Factory's own embeddable calendar widget
(https://nfs.faireconomy.media/ff_calendar_thisweek.json) — publicly
reachable, not the forexfactory.com HTML page, and not scraped.

Confirmed against the live feed (do not assume otherwise if this ever
changes): it has NO "actual" field at all — forecast, previous, and
scheduled time only. It is also rate-limited (429 with Retry-After was
observed during evaluation) and appeared to only refresh its "this week"
window around weekday market hours, not exactly at each date change — since
weekends have no economic releases this hasn't mattered in practice, but a
request landing right after a stale cache can legitimately return no
event for "today" even inside a real trading week. Both cases are handled
the same way: an empty list, never an error surfaced to the caller.
"""

import logging
from datetime import date, datetime, timezone
from typing import List, Optional

import requests

from ai_models import EconomicEvent
from liquidity_sweep import BANGKOK_TZ

logger = logging.getLogger(__name__)

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HIGH_IMPACT = "High"
USD = "USD"


class NewsCalendarClient:
    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def _fetch_sync(self) -> List[dict]:
        response = requests.get(CALENDAR_URL, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Unexpected calendar payload shape")
        return payload

    def fetch_todays_usd_high_impact_events(
        self, today_bangkok: Optional[date] = None
    ) -> List[EconomicEvent]:
        """Best-effort: any failure (network, rate limit, malformed
        response) is logged and returns an empty list rather than raising,
        so a calendar outage never breaks the rest of the analysis.

        "today" is the Bangkok-local calendar date, matching how every
        other "today"/session concept in this codebase is defined
        (bangkok_now, is_within_notification_window, etc.) — the calendar
        source itself reports event times in US Eastern.
        """
        try:
            raw_events = self._fetch_sync()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("News calendar request failed: %s", exc)
            return []

        target_date = today_bangkok or datetime.now(timezone.utc).astimezone(BANGKOK_TZ).date()
        events = []
        for item in raw_events:
            if item.get("country") != USD or item.get("impact") != HIGH_IMPACT:
                continue
            raw_date = item.get("date")
            if not raw_date:
                continue
            try:
                scheduled = datetime.fromisoformat(raw_date)
            except ValueError:
                logger.warning("Unparseable calendar event date: %s", raw_date)
                continue
            scheduled_utc = scheduled.astimezone(timezone.utc)
            if scheduled_utc.astimezone(BANGKOK_TZ).date() != target_date:
                continue
            events.append(
                EconomicEvent(
                    title=item.get("title", ""),
                    scheduled_time=scheduled_utc.isoformat(),
                    forecast=item.get("forecast") or "",
                    previous=item.get("previous") or "",
                )
            )
        return events
