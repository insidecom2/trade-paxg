import os
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from exchange_manager import (
    MySQLManager,
    TwelveDataManager,
    _price_points_to_candles,
    create_market_data_manager,
)


class ExchangeManagerTests(unittest.IsolatedAsyncioTestCase):
    def test_mysql_hourly_prices_are_aggregated_into_four_hour_candles(self):
        start = int(datetime(2026, 8, 17, tzinfo=timezone.utc).timestamp() * 1000)
        points = [
            (start + index * 60 * 60 * 1000, price)
            for index, price in enumerate([100.0, 102.0, 99.0, 101.0])
        ]

        candles = _price_points_to_candles(points, "4h")

        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0].open, 100.0)
        self.assertEqual(candles[0].high, 102.0)
        self.assertEqual(candles[0].low, 99.0)
        self.assertEqual(candles[0].close, 101.0)
        self.assertEqual(candles[0].volume, 0.0)

    async def test_mysql_manager_reads_latest_price_from_existing_table(self):
        class FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchall(self):
                return [{"f_price": Decimal("2450.25"), "f_datetime": datetime(2026, 8, 17)}]

        class FakeConnection:
            def __init__(self):
                self.cursor_instance = FakeCursor()

            def cursor(self):
                return self.cursor_instance

            def close(self):
                pass

        class FakePyMySQL:
            class cursors:
                DictCursor = object()

            def __init__(self):
                self.connection = FakeConnection()

            def connect(self, **kwargs):
                return self.connection

        env = {
            "MYSQL_HOST": "localhost",
            "MYSQL_PORT": "3306",
            "MYSQL_DATABASE": "prices",
            "MYSQL_USER": "reader",
            "MYSQL_PASSWORD": "secret",
            "MYSQL_PRICE_TABLE": "xauusd_prices",
        }
        fake_pymysql = FakePyMySQL()
        with patch.dict(os.environ, env, clear=False), patch(
            "exchange_manager.pymysql", fake_pymysql
        ):
            manager = MySQLManager()
            price = await manager.fetch_current_price("PAXG/USDT")

        self.assertEqual(price, 2450.25)
        self.assertEqual(fake_pymysql.connection.cursor_instance.params, ("xauusd", 1))
        self.assertIn("FROM `xauusd_prices`", fake_pymysql.connection.cursor_instance.query)

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

    def test_mysql_table_name_rejects_sql_fragments(self):
        env = {
            "MYSQL_HOST": "localhost",
            "MYSQL_DATABASE": "prices",
            "MYSQL_USER": "reader",
            "MYSQL_PRICE_TABLE": "prices;DROP TABLE users",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "MYSQL_PRICE_TABLE"):
                MySQLManager()


if __name__ == "__main__":
    unittest.main()
