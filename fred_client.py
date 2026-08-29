"""Thin wrapper around the FRED (Federal Reserve Economic Data) API.

FRED gives the most recently *released* actual value for a fixed set of US
macro series — it has no forecast/consensus figures and its releases/dates
endpoint only reports dates already in the past (verified against the live
API), not a forward-looking calendar. This client is scoped to that: latest
released values only. It never raises; a failed lookup returns an empty
list so the AI pipeline can proceed without macro context rather than fail.
"""

import asyncio
import logging
import os
from typing import List, Optional

import requests

from ai_models import MacroDataPoint

logger = logging.getLogger(__name__)

FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"

# Series most relevant to gold: inflation, employment, and policy rate.
TRACKED_SERIES = {
    "CPIAUCSL": "CPI (headline)",
    "CPILFESL": "Core CPI",
    "PCEPI": "PCE Price Index",
    "PAYEMS": "Non-farm Payrolls",
    "UNRATE": "Unemployment Rate",
    "FEDFUNDS": "Fed Funds Rate (effective)",
}


class FredClient:
    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> Optional["FredClient"]:
        api_key = os.getenv("FRED_API_KEY", "").strip()
        if not api_key:
            logger.info("FRED_API_KEY not set; macro data omitted from AI context")
            return None
        return cls(api_key)

    def _fetch_latest_sync(self, series_id: str) -> Optional[MacroDataPoint]:
        try:
            response = requests.get(
                FRED_API_URL,
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            observations = response.json().get("observations", [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("FRED request failed for %s: %s", series_id, exc)
            return None

        if not observations:
            return None
        obs = observations[0]
        try:
            value = float(obs["value"])
        except (KeyError, ValueError, TypeError):
            logger.warning("FRED returned a non-numeric value for %s: %s", series_id, obs)
            return None

        return MacroDataPoint(
            indicator=TRACKED_SERIES.get(series_id, series_id),
            period=obs.get("date", ""),
            value=value,
        )

    async def fetch_latest_released(self) -> List[MacroDataPoint]:
        """Fetches the latest released value for every tracked series.
        Best-effort: a failure on one series is logged and skipped, it
        never aborts the others or raises to the caller.
        """
        results = await asyncio.gather(
            *(
                asyncio.to_thread(self._fetch_latest_sync, series_id)
                for series_id in TRACKED_SERIES
            )
        )
        return [point for point in results if point is not None]
