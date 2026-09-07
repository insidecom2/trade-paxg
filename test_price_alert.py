import unittest
from datetime import datetime
from unittest.mock import patch

from models import Candle
from price_alert import (
    BANGKOK_TIMEZONE,
    PriceAlertLevel,
    STATE_KEY,
    bangkok_day_bounds,
    latest_closed_bangkok_4h_candle,
    run_price_alert,
)
from price_alert import MySQLPriceAlertRepository


class FakeRepository:
    def __init__(self, levels):
        self.levels = levels

    async def fetch_todays_levels(self, now):
        return self.levels


class FakeMarketData:
    def __init__(self, close, high=None, low=None):
        high = close if high is None else high
        low = close if low is None else low
        bucket_start = int(
            datetime(2026, 9, 4, 20, tzinfo=BANGKOK_TIMEZONE).timestamp() * 1000
        )
        self.candles = [
            Candle(timestamp=bucket_start - 3_600_000, open=1, high=1, low=1, close=1, volume=0),
            *[
                Candle(
                    timestamp=bucket_start + offset * 3_600_000,
                    open=1,
                    high=high,
                    low=low,
                    close=close,
                    volume=0,
                )
                for offset in range(4)
            ],
            Candle(timestamp=bucket_start + 14_400_000, open=1, high=1, low=1, close=1, volume=0),
        ]
        self.closed = False

    async def fetch_ohlcv(self, symbol, timeframe, limit):
        self.request = (symbol, timeframe, limit)
        return self.candles

    async def close(self):
        self.closed = True


class FakeNotifier:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.messages = []

    async def send_message(self, message):
        self.messages.append(message)
        return self.accepted


class FakeStateStore:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key, {})

    def save(self, key, state):
        self.data[key] = state


class PriceAlertTests(unittest.IsolatedAsyncioTestCase):
    def test_latest_closed_candle_is_aggregated_on_bangkok_4h_boundary(self):
        bucket_start = int(
            datetime(2026, 9, 4, 20, tzinfo=BANGKOK_TIMEZONE).timestamp() * 1000
        )
        candles = [
            Candle(timestamp=bucket_start - 3_600_000, open=90, high=91, low=89, close=90, volume=1),
            Candle(timestamp=bucket_start, open=100, high=105, low=99, close=102, volume=2),
            Candle(timestamp=bucket_start + 3_600_000, open=102, high=110, low=101, close=108, volume=3),
            Candle(timestamp=bucket_start + 7_200_000, open=108, high=109, low=95, close=98, volume=4),
            Candle(timestamp=bucket_start + 10_800_000, open=98, high=103, low=97, close=101, volume=5),
            Candle(timestamp=bucket_start + 14_400_000, open=101, high=102, low=100, close=101, volume=6),
        ]

        candle = latest_closed_bangkok_4h_candle(candles)

        self.assertEqual(candle.timestamp, bucket_start)
        self.assertEqual((candle.open, candle.high, candle.low, candle.close, candle.volume), (100, 110, 95, 101, 14))

    async def test_empty_lookup_does_nothing(self):
        repository = FakeRepository([])
        notifier = FakeNotifier()
        market_data = FakeMarketData(close=2000.5)
        state = FakeStateStore()

        self.assertTrue(
            await run_price_alert(
                now=datetime(2026, 9, 4, 19, 30, tzinfo=BANGKOK_TIMEZONE),
                repository=repository,
                market_data=market_data,
                notifier=notifier,
                state_store=state,
            )
        )
        self.assertEqual(notifier.messages, [])
        self.assertEqual(state.get(STATE_KEY), {})

    async def test_resistance_cross_sends_once_per_closed_candle(self):
        repository = FakeRepository([PriceAlertLevel(support=None, resistance=2000)])
        market_data = FakeMarketData(close=2001)
        notifier = FakeNotifier()
        state = FakeStateStore()

        self.assertTrue(
            await run_price_alert(
                repository=repository,
                market_data=market_data,
                notifier=notifier,
                state_store=state,
            )
        )
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("ทะลุสูงกว่าแนวต้าน 2000.00", notifier.messages[0])
        self.assertTrue(
            await run_price_alert(
                repository=repository,
                market_data=market_data,
                notifier=notifier,
                state_store=state,
            )
        )
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("resistance:2000.00000000", state.get(STATE_KEY)["alerts"])

    async def test_support_cross_sends_message(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_price_alert(
                repository=FakeRepository([PriceAlertLevel(support=2000, resistance=None)]),
                market_data=FakeMarketData(close=1999),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("ทะลุต่ำกว่าแนวรับ 2000.00", notifier.messages[0])

    async def test_equal_close_does_not_send(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_price_alert(
                repository=FakeRepository([PriceAlertLevel(support=1900, resistance=2100)]),
                market_data=FakeMarketData(close=2000),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [])

    async def test_resistance_touch_that_closes_at_or_below_level_sends_message(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_price_alert(
                repository=FakeRepository([PriceAlertLevel(support=None, resistance=2000)]),
                market_data=FakeMarketData(close=1999, high=2001, low=1990),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("แตะแนวต้าน 2000.00 แต่ปิดไม่สูงกว่าแนวต้าน", notifier.messages[0])

    async def test_support_touch_that_closes_at_or_above_level_sends_message(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_price_alert(
                repository=FakeRepository([PriceAlertLevel(support=2000, resistance=None)]),
                market_data=FakeMarketData(close=2001, high=2010, low=1999),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(len(notifier.messages), 1)
        self.assertIn("แตะแนวรับ 2000.00 แต่ปิดไม่ต่ำกว่าแนวรับ", notifier.messages[0])

    async def test_failed_telegram_delivery_is_not_deduplicated(self):
        notifier = FakeNotifier(accepted=False)
        state = FakeStateStore()
        kwargs = {
            "repository": FakeRepository([PriceAlertLevel(support=None, resistance=2000)]),
            "market_data": FakeMarketData(close=2001),
            "notifier": notifier,
            "state_store": state,
        }
        self.assertTrue(await run_price_alert(**kwargs))
        self.assertTrue(await run_price_alert(**kwargs))
        self.assertEqual(len(notifier.messages), 2)
        self.assertEqual(state.get(STATE_KEY)["alerts"], [])

    async def test_repository_failure_returns_false_without_sending(self):
        class BrokenRepository:
            async def fetch_todays_levels(self, now):
                raise RuntimeError("mysql unavailable")

        notifier = FakeNotifier()
        self.assertFalse(
            await run_price_alert(
                repository=BrokenRepository(),
                market_data=FakeMarketData(close=2001),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [])

    def test_bangkok_date_range_is_naive_and_half_open(self):
        start, end = bangkok_day_bounds(datetime(2026, 9, 4, 19, tzinfo=BANGKOK_TIMEZONE))
        self.assertEqual(start, datetime(2026, 9, 4))
        self.assertEqual(end, datetime(2026, 9, 5))

    def test_mysql_query_filters_symbol_and_bangkok_range(self):
        captured = {}

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return None

            def execute(self, query, params):
                captured["query"] = query
                captured["params"] = params

            def fetchall(self):
                return [
                    {"support": "1990.5", "resistance": "2010.5"},
                    {"support": "not-a-number", "resistance": None},
                ]

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                captured["closed"] = True

        repository = MySQLPriceAlertRepository("host", 3306, "user", "password", "database")
        with patch("price_alert.pymysql.connect", return_value=Connection()):
            levels = repository._fetch_sync(datetime(2026, 9, 4), datetime(2026, 9, 5))

        self.assertIn("`date` >= %s AND `date` < %s AND `symbol` = %s", captured["query"])
        self.assertEqual(captured["params"], (datetime(2026, 9, 4), datetime(2026, 9, 5), "XAUUSD"))
        self.assertEqual(levels, [PriceAlertLevel(support=1990.5, resistance=2010.5)])
        self.assertTrue(captured["closed"])


if __name__ == "__main__":
    unittest.main()
