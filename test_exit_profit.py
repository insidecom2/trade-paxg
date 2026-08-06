import unittest

from exit_profit import (
    calculate_exit_profit_zone_tolerance,
    calculate_rsi_pair,
    check_exit_profit_zone,
    find_exit_profit_level,
    find_exit_profit_levels,
)
from exit_profit_notification import build_exit_profit_notification
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

        self.assertAlmostEqual(calculate_exit_profit_zone_tolerance(candles), 0.0005)

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
        check = check_exit_profit_zone(
            [candle_1, candle_2],
            zone_tolerance=0.0015,
        )

        notification = build_exit_profit_notification("PAXG/USDT", check)

        self.assertIn("Close #1: $4265.00", notification)
        self.assertIn("High #1 : 4297.6", notification)
        self.assertIn("Volume #1: 100.00", notification)
        self.assertIn("Candle #1 (15m)", notification)
        self.assertIn("Close #2: $4270.00", notification)
        self.assertIn("High #2 : 4298.3", notification)
        self.assertIn("Volume #2: 90.60", notification)
        self.assertIn("Candle #2 (15m)", notification)
        self.assertIn("Touch Count : 2", notification)
        self.assertIn("Volume Change : -9.4%", notification)
        self.assertIn("Same Zone: YES", notification)
        self.assertIn("Exit Profit Alert: $4267.50", notification)

    def test_rsi_momentum_is_weak_when_current_rsi_falls(self):
        candles = [make_candle(index, 100.0 + index) for index in range(15)]
        candles.extend([make_candle(15, 113.0), make_candle(16, 112.0)])

        check = check_exit_profit_zone(candles, zone_tolerance=0.0015)

        previous, current = calculate_rsi_pair(candles)
        self.assertAlmostEqual(check.rsi_previous, previous)
        self.assertAlmostEqual(check.rsi_current, current)
        self.assertLess(check.rsi_current, check.rsi_previous)
        self.assertEqual(check.momentum, "WEAK")


if __name__ == "__main__":
    unittest.main()
