import asyncio
import logging
import math
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional

import ccxt.async_support as ccxt
from models import Candle

try:
    import pymysql
except ImportError:  # pragma: no cover - exercised when the optional dependency is absent
    pymysql = None

logger = logging.getLogger(__name__)

MYSQL_SYMBOL = "xauusd"
MYSQL_TIMEFRAME_HOURS = {
    "1h": 1,
    "4h": 4,
    "1d": 24,
}
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str, setting_name: str) -> str:
    if not SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(
            f"{setting_name} must contain only letters, numbers, and underscores"
        )
    return value


def _datetime_to_timestamp_ms(value: Any) -> int:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("f_datetime must be a datetime or ISO-8601 string")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _price_points_to_candles(
    points: list[tuple[int, float]], timeframe: str
) -> list[Candle]:
    """Aggregate hourly MySQL prices into the requested strategy timeframe."""
    hours = MYSQL_TIMEFRAME_HOURS.get(timeframe)
    if hours is None:
        raise ValueError(
            "MySQL price data is hourly and supports only 1h, 4h, and 1d timeframes"
        )

    bucket_size_ms = hours * 60 * 60 * 1000
    buckets: dict[int, list[float]] = {}
    for timestamp, price in points:
        bucket_start = timestamp - (timestamp % bucket_size_ms)
        buckets.setdefault(bucket_start, []).append(price)

    return [
        Candle(
            timestamp=timestamp,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=0.0,
        )
        for timestamp, prices in sorted(buckets.items())
    ]

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

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
        try:
            ohlcv = await self._call('fetch_ohlcv', symbol, timeframe, limit=limit)
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


class MySQLManager:
    """Reads hourly XAUUSD prices from an existing MySQL table."""

    def __init__(self):
        self.host = self._required_setting("MYSQL_HOST")
        self.port = self._port_setting()
        self.database = self._required_setting("MYSQL_DATABASE")
        self.user = self._required_setting("MYSQL_USER")
        self.password = os.getenv("MYSQL_PASSWORD", "")
        self.table = _validate_identifier(
            self._required_setting("MYSQL_PRICE_TABLE"), "MYSQL_PRICE_TABLE"
        )

    @staticmethod
    def _required_setting(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            raise ValueError(f"{name} is required when PRICE_SOURCE=mysql")
        return value

    @staticmethod
    def _port_setting() -> int:
        value = os.getenv("MYSQL_PORT", "3306").strip()
        try:
            port = int(value)
        except ValueError as exc:
            raise ValueError("MYSQL_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MYSQL_PORT must be between 1 and 65535")
        return port

    def _fetch_rows_sync(self, limit: int) -> list[dict[str, Any]]:
        if pymysql is None:
            raise RuntimeError(
                "PyMySQL is required for PRICE_SOURCE=mysql; install project dependencies"
            )

        query = (
            "SELECT f_price, f_datetime "
            f"FROM `{self.table}` "
            "WHERE LOWER(f_symbol) = %s "
            "ORDER BY f_datetime DESC LIMIT %s"
        )
        connection = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=10,
            read_timeout=10,
            write_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, (MYSQL_SYMBOL, limit))
                return list(cursor.fetchall())
        finally:
            connection.close()

    @staticmethod
    def _row_to_point(row: dict[str, Any]) -> tuple[int, float]:
        if row.get("f_price") is None or row.get("f_datetime") is None:
            raise ValueError("MySQL price rows must contain f_price and f_datetime")

        price = float(Decimal(str(row["f_price"])))
        if not math.isfinite(price) or price <= 0:
            raise ValueError("MySQL f_price must be a finite positive number")
        return _datetime_to_timestamp_ms(row["f_datetime"]), price

    async def _fetch_points(self, limit: int) -> list[tuple[int, float]]:
        rows = await asyncio.to_thread(self._fetch_rows_sync, limit)
        points = [self._row_to_point(row) for row in rows]
        return sorted(points)

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> List[Candle]:
        del symbol  # The existing source table is explicitly for f_symbol=xauusd.
        hours = MYSQL_TIMEFRAME_HOURS.get(timeframe)
        if hours is None:
            raise ValueError(
                "MySQL price data is hourly and supports only 1h, 4h, and 1d timeframes"
            )
        source_limit = max(limit * hours + hours, hours + 1)
        points = await self._fetch_points(source_limit)
        candles = _price_points_to_candles(points, timeframe)
        if not candles:
            raise LookupError(f"No MySQL prices found for f_symbol={MYSQL_SYMBOL}")
        return candles[-limit:]

    async def fetch_current_price(self, symbol: str) -> float:
        del symbol
        points = await self._fetch_points(1)
        if not points:
            raise LookupError(f"No MySQL prices found for f_symbol={MYSQL_SYMBOL}")
        return points[-1][1]

    async def close(self) -> None:
        """Connections are short-lived per query, so there is nothing to close."""


def create_market_data_manager():
    source = os.getenv("PRICE_SOURCE", "binance").strip().lower()
    if source == "binance":
        return BinanceManager()
    if source == "mysql":
        return MySQLManager()
    raise ValueError("PRICE_SOURCE must be either 'binance' or 'mysql'")
