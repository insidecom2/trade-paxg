import unittest
from datetime import datetime, timedelta

from bollinger_wick_alert import (
    BANGKOK_TIMEZONE,
    BUY_MESSAGE,
    SELL_MESSAGE,
    STATE_KEY,
    bollinger_bands,
    latest_closed_candles,
    run_bollinger_wick_alert,
)
from models import Candle


class FakeMarketData:
    def __init__(self, candles):
        self.candles = candles
        self.request = None

    async def fetch_ohlcv(self, symbol, timeframe, limit):
        self.request = (symbol, timeframe, limit)
        return self.candles

    async def close(self):
        return None


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


class BollingerWickAlertTests(unittest.IsolatedAsyncioTestCase):
    now = datetime(2026, 9, 4, 10, 0, tzinfo=BANGKOK_TIMEZONE)

    def candles(self, high=100.0, low=100.0, close=100.0, include_live=True):
        first = self.now - timedelta(minutes=100)
        values = [
            Candle(
                timestamp=int((first + timedelta(minutes=5 * index)).timestamp() * 1000),
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=0.0,
            )
            for index in range(19)
        ]
        values.append(
            Candle(
                timestamp=int((self.now - timedelta(minutes=5)).timestamp() * 1000),
                open=100.0,
                high=high,
                low=low,
                close=close,
                volume=0.0,
            )
        )
        if include_live:
            values.append(
                Candle(
                    timestamp=int(self.now.timestamp() * 1000),
                    open=100.0,
                    high=999.0,
                    low=1.0,
                    close=500.0,
                    volume=0.0,
                )
            )
        return values

    async def test_lower_band_wick_sends_the_exact_buy_message(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_bollinger_wick_alert(
                now=self.now,
                market_data=FakeMarketData(self.candles(low=99.0)),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [BUY_MESSAGE])

    async def test_upper_band_wick_sends_the_exact_sell_message(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_bollinger_wick_alert(
                now=self.now,
                market_data=FakeMarketData(self.candles(high=101.0)),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [SELL_MESSAGE])

    async def test_both_wicks_send_both_messages(self):
        notifier = FakeNotifier()
        self.assertTrue(
            await run_bollinger_wick_alert(
                now=self.now,
                market_data=FakeMarketData(self.candles(high=101.0, low=99.0)),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [BUY_MESSAGE, SELL_MESSAGE])

    async def test_boundary_and_outside_close_do_not_send(self):
        for high, low, close in ((100.0, 100.0, 100.0), (101.0, 99.0, 101.0)):
            notifier = FakeNotifier()
            await run_bollinger_wick_alert(
                now=self.now,
                market_data=FakeMarketData(self.candles(high=high, low=low, close=close)),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
            self.assertEqual(notifier.messages, [])

    async def test_live_candle_is_excluded_before_calculation(self):
        candles = self.candles()
        closed = latest_closed_candles(candles, self.now)
        upper, lower = bollinger_bands(closed)

        self.assertEqual(len(closed), 20)
        self.assertEqual(closed[-1].close, 100.0)
        self.assertEqual((upper, lower), (100.0, 100.0))

    async def test_deduplicates_successful_delivery_per_direction_and_candle(self):
        notifier = FakeNotifier()
        market_data = FakeMarketData(self.candles(low=99.0))
        state = FakeStateStore()
        kwargs = {
            "now": self.now,
            "market_data": market_data,
            "notifier": notifier,
            "state_store": state,
        }

        self.assertTrue(await run_bollinger_wick_alert(**kwargs))
        self.assertTrue(await run_bollinger_wick_alert(**kwargs))

        self.assertEqual(notifier.messages, [BUY_MESSAGE])
        self.assertEqual(state.get(STATE_KEY)["alerts"], ["BUY"])

    async def test_rejected_delivery_is_eligible_on_the_next_cron_run(self):
        notifier = FakeNotifier(accepted=False)
        state = FakeStateStore()
        kwargs = {
            "now": self.now,
            "market_data": FakeMarketData(self.candles(low=99.0)),
            "notifier": notifier,
            "state_store": state,
        }

        self.assertTrue(await run_bollinger_wick_alert(**kwargs))
        self.assertTrue(await run_bollinger_wick_alert(**kwargs))

        self.assertEqual(notifier.messages, [BUY_MESSAGE, BUY_MESSAGE])
        self.assertEqual(state.get(STATE_KEY)["alerts"], [])

    async def test_too_few_completed_candles_fails_safely(self):
        notifier = FakeNotifier()
        self.assertFalse(
            await run_bollinger_wick_alert(
                now=self.now,
                market_data=FakeMarketData(self.candles()[:19]),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [])

    async def test_inconsistent_latest_ohlc_fails_safely(self):
        candles = self.candles(high=99.0, low=101.0)
        notifier = FakeNotifier()
        self.assertFalse(
            await run_bollinger_wick_alert(
                now=self.now,
                market_data=FakeMarketData(candles),
                notifier=notifier,
                state_store=FakeStateStore(),
            )
        )
        self.assertEqual(notifier.messages, [])


if __name__ == "__main__":
    unittest.main()
