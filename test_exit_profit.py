import unittest
from unittest.mock import AsyncMock, patch

from exit_profit import (
    ExitProfitCheck,
    calculate_exit_profit_zone_tolerance,
    calculate_rsi_pair,
    check_exit_profit_at_level,
    check_exit_profit_zone,
    find_exit_profit_level,
    find_exit_profit_levels,
    resolve_exit_profit_alert,
    resolve_exit_profit_zone_alert,
    run_standalone,
)
from exit_profit_notification import (
    build_exit_profit_notification,
    should_send_exit_profit_notification,
)
from models import Candle


def make_candle(timestamp: int, close: float) -> Candle:
    return Candle(
        timestamp=timestamp,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
    )


class ExitProfitTests(unittest.TestCase):
    def test_alerts_when_two_closes_are_inside_the_same_zone(self):
        candles = [
            make_candle(1, 2500.0),
            make_candle(2, 2505.0),
            make_candle(3, 2400.0),
        ]

        level = find_exit_profit_level(candles, zone_tolerance=0.005)

        self.assertAlmostEqual(level.price, 2502.5)
        self.assertEqual(level.occurrences, 2)
        self.assertEqual(level.latest_timestamp, 2)

    def test_returns_latest_revisited_level_first(self):
        candles = [
            make_candle(1, 2500.0),
            make_candle(2, 2504.0),
            make_candle(3, 2400.0),
            make_candle(4, 2520.0),
            make_candle(5, 2524.0),
            make_candle(6, 2300.0),
        ]

        levels = find_exit_profit_levels(candles, zone_tolerance=0.005)

        self.assertEqual([level.price for level in levels], [2522.0, 2502.0])

    def test_atr_zone_uses_existing_atr_based_bounds(self):
        candles = [
            Candle(
                timestamp=index,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=100.0,
            )
            for index in range(20)
        ]

        self.assertAlmostEqual(calculate_exit_profit_zone_tolerance(candles), 0.0025)

    def test_narrow_zone_matches_prices_about_five_dollars_apart(self):
        candles = [
            make_candle(1, 4265.0),
            make_candle(2, 4270.0),
        ]

        level = find_exit_profit_level(candles, zone_tolerance=0.0015)

        self.assertAlmostEqual(level.price, 4267.5)
        self.assertEqual(level.occurrences, 2)

    def test_rejects_invalid_minimum_occurrences(self):
        with self.assertRaises(ValueError):
            find_exit_profit_levels([], min_occurrences=0)

    def test_check_compares_the_two_latest_closed_prices(self):
        candle_1 = Candle(
            timestamp=1,
            open=4260.0,
            high=4297.6,
            low=4255.0,
            close=4265.0,
            volume=100.0,
        )
        candle_2 = Candle(
            timestamp=2,
            open=4265.0,
            high=4298.3,
            low=4260.0,
            close=4270.0,
            volume=90.6,
        )
        check = check_exit_profit_zone(
            [candle_1, candle_2],
            zone_tolerance=0.0015,
        )

        self.assertTrue(check.same_zone)
        self.assertEqual(check.close_1, 4265.0)
        self.assertEqual(check.close_2, 4270.0)
        self.assertEqual(check.high_1, 4297.6)
        self.assertEqual(check.high_2, 4298.3)
        self.assertEqual(check.volume_1, 100.0)
        self.assertEqual(check.volume_2, 90.6)
        self.assertEqual(check.touch_count, 2)
        self.assertAlmostEqual(check.volume_change_percent, -9.4)
        self.assertEqual(check.exit_price, 4267.5)

    def test_notification_is_separate_and_shows_both_closes(self):
        candle_1 = Candle(
            timestamp=1,
            open=4260.0,
            high=4297.6,
            low=4255.0,
            close=4265.0,
            volume=100.0,
        )
        candle_2 = Candle(
            timestamp=2,
            open=4265.0,
            high=4298.3,
            low=4260.0,
            close=4270.0,
            volume=90.6,
        )
        check = check_exit_profit_at_level(
            [candle_1, candle_2],
            4267.5,
            zone_tolerance=0.0015,
        )

        notification = build_exit_profit_notification("PAXG/USDT", check)

        self.assertIn("PAXG 1h Exit Profit Check", notification)
        self.assertIn("Price: $4267.50", notification)
        self.assertIn("Closes at level: 2/4", notification)
        self.assertIn("Close #1: $4265.00", notification)
        self.assertIn("High #1 : 4297.6", notification)
        self.assertIn("Volume #1: 100.00", notification)
        self.assertIn("Candle #1 (1h)", notification)
        self.assertIn("Close #2: $4270.00", notification)
        self.assertIn("High #2 : 4298.3", notification)
        self.assertIn("Volume #2: 90.60", notification)
        self.assertIn("Candle #2 (1h)", notification)
        self.assertIn("Touch Count : 2", notification)
        self.assertIn("Volume Change : -9.4%", notification)
        self.assertIn("Same Zone: YES", notification)
        self.assertIn("Exit Profit Alert: $4267.50", notification)

    def test_exit_profit_notification_requires_same_zone(self):
        inside = check_exit_profit_zone(
            [make_candle(1, 4265.0), make_candle(2, 4265.5)],
            zone_tolerance=0.0005,
        )
        outside = check_exit_profit_zone(
            [make_candle(1, 4265.0), make_candle(2, 4275.0)],
            zone_tolerance=0.0005,
        )

        self.assertTrue(should_send_exit_profit_notification(inside))
        self.assertFalse(should_send_exit_profit_notification(outside))

    def test_rsi_momentum_is_weak_when_current_rsi_falls(self):
        candles = [make_candle(index, 100.0 + index) for index in range(15)]
        candles.extend([make_candle(15, 113.0), make_candle(16, 112.0)])

        check = check_exit_profit_zone(candles, zone_tolerance=0.0015)

        previous, current = calculate_rsi_pair(candles)
        self.assertAlmostEqual(check.rsi_previous, previous)
        self.assertAlmostEqual(check.rsi_current, current)
        self.assertLess(check.rsi_current, check.rsi_previous)
        self.assertEqual(check.momentum, "WEAK")

    def test_level_revisited_twice_within_lookback_alerts(self):
        candles = [
            make_candle(0, 4200.0),
            make_candle(1, 4260.0),
            make_candle(2, 4262.0),
        ]

        check = check_exit_profit_at_level(
            candles, 4261.0, zone_tolerance=0.005, lookback=4
        )

        self.assertTrue(check.same_zone)
        self.assertEqual(check.occurrences, 2)
        self.assertAlmostEqual(check.exit_price, 4261.0)
        self.assertEqual(check.latest_timestamp, 2)

    def test_latest_close_outside_band_does_not_alert(self):
        candles = [
            make_candle(0, 4260.0),
            make_candle(1, 4262.0),
            make_candle(2, 4150.0),
        ]

        check = check_exit_profit_at_level(
            candles, 4261.0, zone_tolerance=0.005, lookback=4
        )

        self.assertFalse(check.same_zone)
        self.assertEqual(check.occurrences, 2)
        self.assertIsNone(check.exit_price)

    def test_rejects_invalid_lookback_and_level(self):
        candles = [make_candle(0, 4260.0), make_candle(1, 4262.0)]

        with self.assertRaises(ValueError):
            check_exit_profit_at_level(candles, 4261.0, lookback=0)
        with self.assertRaises(ValueError):
            check_exit_profit_at_level(candles, -1.0)

    def test_resolve_alerts_on_second_close_and_again_on_third(self):
        anchor = {
            "action": "BUY",
            "entry_price": 4240.0,
            "stop_loss": 4200.0,
            "take_profit": 4270.0,
        }
        candles = [
            make_candle(0, 4200.0),
            make_candle(1, 4260.0),
            make_candle(2, 4262.0),
        ]

        check, next_anchor = resolve_exit_profit_alert(
            candles, anchor, zone_tolerance=0.005, lookback=4
        )

        self.assertIsNotNone(check)
        self.assertTrue(check.same_zone)
        self.assertEqual(check.occurrences, 2)
        self.assertEqual(next_anchor["last_alerted_timestamp"], 2)

        repeat, next_anchor = resolve_exit_profit_alert(
            candles, next_anchor, zone_tolerance=0.005, lookback=4
        )
        self.assertIsNone(repeat)

        third_candle = make_candle(3, 4265.0)
        again, next_anchor = resolve_exit_profit_alert(
            candles + [third_candle], next_anchor, zone_tolerance=0.005, lookback=4
        )
        self.assertIsNotNone(again)
        self.assertEqual(again.occurrences, 3)
        self.assertEqual(next_anchor["last_alerted_timestamp"], 3)

    def test_resolve_clears_anchor_when_take_profit_reached(self):
        anchor = {
            "action": "BUY",
            "entry_price": 4240.0,
            "stop_loss": 4200.0,
            "take_profit": 4261.0,
        }
        candles = [make_candle(0, 4240.0), make_candle(1, 4270.0)]

        check, next_anchor = resolve_exit_profit_alert(
            candles, anchor, zone_tolerance=0.005, lookback=4
        )

        self.assertIsNotNone(check)
        self.assertTrue(check.same_zone)
        self.assertAlmostEqual(check.exit_price, 4261.0)
        self.assertEqual(next_anchor, {})

    def test_resolve_clears_anchor_when_stop_loss_hit(self):
        anchor = {
            "action": "BUY",
            "entry_price": 4240.0,
            "stop_loss": 4200.0,
            "take_profit": 4261.0,
        }
        candles = [make_candle(0, 4240.0), make_candle(1, 4190.0)]

        check, next_anchor = resolve_exit_profit_alert(
            candles, anchor, zone_tolerance=0.005, lookback=4
        )

        self.assertIsNone(check)
        self.assertEqual(next_anchor, {})

    def test_resolve_returns_none_without_anchor(self):
        candles = [make_candle(0, 4240.0), make_candle(1, 4262.0)]

        check, next_anchor = resolve_exit_profit_alert(
            candles, None, zone_tolerance=0.005, lookback=4
        )

        self.assertIsNone(check)
        self.assertEqual(next_anchor, {})

    def test_zone_alerts_without_a_trade_anchor_and_deduplicates_a_candle(self):
        candles = [
            make_candle(0, 4200.0),
            make_candle(1, 4260.0),
            make_candle(2, 4262.0),
        ]

        check, state = resolve_exit_profit_zone_alert(
            candles, zone_tolerance=0.005, lookback=4
        )

        self.assertIsNotNone(check)
        self.assertTrue(check.same_zone)
        self.assertEqual(check.occurrences, 2)
        self.assertEqual(state, {"last_alerted_timestamp": 2})

        duplicate, duplicate_state = resolve_exit_profit_zone_alert(
            candles, state, zone_tolerance=0.005, lookback=4
        )

        self.assertIsNone(duplicate)
        self.assertEqual(duplicate_state, state)


class FakeStateStore:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key, {})

    def save(self, key, state):
        self.data[key] = state


class ExitProfitStandaloneTests(unittest.IsolatedAsyncioTestCase):
    def _exit_profit_candles(self):
        candles = [make_candle(index, 4200.0) for index in range(16)]
        candles.append(make_candle(16, 4266.0))
        candles.append(make_candle(17, 4268.0))
        candles.append(make_candle(18, 4269.0))
        return candles

    def _anchor(self, **overrides):
        anchor = {
            "action": "BUY",
            "entry_price": 4240.0,
            "stop_loss": 4200.0,
            "take_profit": 4270.0,
        }
        anchor.update(overrides)
        return anchor

    async def test_run_standalone_alerts_when_level_revisited(self):
        store = FakeStateStore()
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(return_value=self._exit_profit_candles())
        exchange.close = AsyncMock()
        notifier = AsyncMock()

        with patch("exchange_manager.BinanceManager", return_value=exchange), patch(
            "trading_state.TradingStateStore", return_value=store
        ), patch(
            "telegram_notifier.TelegramNotifier.from_env", return_value=notifier
        ), patch("builtins.print") as print_mock:
            await run_standalone()

        self.assertIn("Exit Profit Alert", print_mock.call_args_list[-1].args[0])
        notifier.send_message.assert_awaited_once_with(
            print_mock.call_args_list[-1].args[0]
        )
        self.assertEqual(
            store.data["PAXG/USDT|exit_profit"]["last_alerted_timestamp"], 17
        )

    async def test_run_standalone_does_not_alert_twice_on_same_candle(self):
        store = FakeStateStore()
        store.data["PAXG/USDT|exit_profit"] = {"last_alerted_timestamp": 17}
        exchange = AsyncMock()
        exchange.fetch_ohlcv = AsyncMock(return_value=self._exit_profit_candles())
        exchange.close = AsyncMock()
        notifier_factory = patch("telegram_notifier.TelegramNotifier.from_env")

        with patch("exchange_manager.BinanceManager", return_value=exchange), patch(
            "trading_state.TradingStateStore", return_value=store
        ), notifier_factory as notifier_from_env, patch("builtins.print") as print_mock:
            await run_standalone()

        self.assertEqual(print_mock.call_args_list[-1].args[0], "Exit Profit Alert: NONE")
        notifier_from_env.assert_not_called()


if __name__ == "__main__":
    unittest.main()
