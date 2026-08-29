import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

import ccxt.async_support as ccxt
import requests
from models import Candle

logger = logging.getLogger(__name__)


class BinanceManager:
    """
    Fetches PAXG market data from Binance.
    api.binance.com is geo-blocked in some regions, so requests automatically
    retry through the public mirrors listed in HOSTS.
    """
    HOSTS = [
        'https://api.binance.com',
        'https://data-api.binance.vision',
    ]

    def __init__(self, hosts: Optional[List[str]] = None):
        self.hosts = hosts or self.HOSTS
        self._current_host = None
        self.exchange = None

    def _init_exchange(self, host: str) -> None:
        api_urls = {
            'public': f'{host}/api/v3',
            'private': f'{host}/api/v3',
            'v1': f'{host}/api/v1',
        }
        self.exchange = ccxt.binance({'enableRateLimit': True, 'urls': {'api': api_urls}})

    async def _call(self, method_name: str, *args, **kwargs):
        last_error = None
        for host in self.hosts:
            try:
                if self._current_host != host:
                    await self.close()
                    self._init_exchange(host)
                    self._current_host = host
                return await getattr(self.exchange, method_name)(*args, **kwargs)
            except (ccxt.ExchangeNotAvailable, ccxt.RequestTimeout, ccxt.NetworkError) as e:
                last_error = e
                logger.warning(f"Binance host {host} unreachable ({e}); trying mirror...")
        raise last_error

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100, since: Optional[int] = None
    ) -> List[Candle]:
        try:
            ohlcv = await self._call('fetch_ohlcv', symbol, timeframe, since=since, limit=limit)
            return [
                Candle(
                    timestamp=x[0], open=x[1], high=x[2], low=x[3], close=x[4], volume=x[5]
                ) for x in ohlcv
            ]
        except Exception as e:
            logger.error(f"Error fetching data from Binance: {e}")
            raise

    async def fetch_current_price(self, symbol: str) -> float:
        try:
            ticker = await self._call('fetch_ticker', symbol)
            price = ticker['last']
            logger.info(f"Current {symbol} Price: {price}")
            return float(price)
        except Exception as e:
            logger.error(f"Error fetching current price from Binance: {e}")
            raise

    async def close(self) -> None:
        if self.exchange is not None:
            try:
                await self.exchange.close()
            except Exception as e:
                logger.warning(f"Error closing Binance session: {e}")
            self.exchange = None


TWELVEDATA_API_URL = "https://api.twelvedata.com/time_series"
TWELVEDATA_INTERVALS = {"1h": "1h", "4h": "4h", "1d": "1day"}


class TwelveDataManager:
    """Fetches XAU/USD spot candles from the Twelve Data REST API.

    Twelve Data has no volume for spot gold, so every candle is returned
    with volume=0.0. Requests are synchronous (the `requests` library) and
    run off the event loop via asyncio.to_thread.
    """

    def __init__(self):
        self.api_key = os.getenv("TWELVEDATA_API_KEY", "").strip()
        if not self.api_key:
            raise ValueError("TWELVEDATA_API_KEY is required when PRICE_SOURCE=twelvedata")

    def _fetch_sync(self, symbol: str, timeframe: str, limit: int) -> dict:
        interval = TWELVEDATA_INTERVALS.get(timeframe)
        if interval is None:
            raise ValueError(
                "Twelve Data source supports only 1h, 4h, and 1d timeframes"
            )
        response = requests.get(
            TWELVEDATA_API_URL,
            params={
                "symbol": symbol,
                "interval": interval,
                "outputsize": limit,
                "timezone": "UTC",
                "apikey": self.api_key,
            },
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100, since: Optional[int] = None
    ) -> List[Candle]:
        del since  # Twelve Data is paged by outputsize, not a since cursor.
        payload = await asyncio.to_thread(self._fetch_sync, symbol, timeframe, limit)
        if payload.get("status") != "ok":
            raise RuntimeError(f"Twelve Data error for {symbol}: {payload}")

        candles = [
            Candle(
                timestamp=int(
                    datetime.strptime(value["datetime"], "%Y-%m-%d %H:%M:%S")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                    * 1000
                ) if len(value["datetime"]) > 10 else int(
                    datetime.strptime(value["datetime"], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                    * 1000
                ),
                open=float(value["open"]),
                high=float(value["high"]),
                low=float(value["low"]),
                close=float(value["close"]),
                volume=0.0,
            )
            for value in payload.get("values", [])
        ]
        candles.sort(key=lambda c: c.timestamp)  # Twelve Data returns newest-first
        return candles

    async def fetch_current_price(self, symbol: str) -> float:
        payload = await asyncio.to_thread(self._fetch_sync, symbol, "1h", 1)
        if payload.get("status") != "ok" or not payload.get("values"):
            raise LookupError(f"No Twelve Data price found for {symbol}")
        return float(payload["values"][0]["close"])

    async def close(self) -> None:
        """Requests are short-lived per call, so there is nothing to close."""


def create_market_data_manager(source: Optional[str] = None):
    resolved_source = (source or os.getenv("PRICE_SOURCE", "binance")).strip().lower()
    if resolved_source == "binance":
        return BinanceManager()
    if resolved_source == "twelvedata":
        return TwelveDataManager()
    raise ValueError("PRICE_SOURCE must be either 'binance' or 'twelvedata'")
