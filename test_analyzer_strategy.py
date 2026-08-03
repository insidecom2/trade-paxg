import tempfile
import unittest
from pathlib import Path

from analyzer import MarketAnalyzer
from main import build_signal_summary
from models import Candle, Signal
from trading_state import TradingStateStore


def make_downtrend_candles(count=220):
    candles = []
    for index in range(count):
        close = 500.0 - index * 0.5
        candles.append(
            Candle(
                timestamp=index,
                open=close + 0.5,
                high=close + 0.5,
                low=close,
                close=close,
                volume=100.0,
            )
        )
    return candles


def make_uptrend_candles(count=220):
    candles = []
    for index in range(count):
        close = 300.0 + index * 0.5
        candles.append(
            Candle(
                timestamp=index,
                open=close - 0.5,
                high=close,
                low=close - 0.5,
                close=close,
                volume=100.0,
            )
        )
    return candles


class SupportStrategyTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MarketAnalyzer()
        self.support = 395.0
        self.resistance = 410.0

    def test_support_without_hammer_is_watch_only(self):
        candles = make_downtrend_candles()
        candles.append(
            Candle(timestamp=220, open=390.5, high=391.0, low=389.8, close=390.0, volume=100.0)
        )

        signal, state = self.analyzer.generate_support_strategy_signal(
            candles, support=390.0, resistance=self.resistance
        )

        self.assertEqual(signal.status, "SUPPORT_WATCH")
        self.assertEqual(signal.action, "HOLD")
        self.assertEqual(state["last_candle_timestamp"], 220)

    def test_breakdown_requires_long_red_volume_and_downtrend(self):
        candles = make_downtrend_candles()
        candles.append(
            Candle(timestamp=220, open=400.0, high=400.0, low=390.0, close=390.0, volume=200.0)
        )

        signal, state = self.analyzer.generate_support_strategy_signal(
            candles, support=self.support, resistance=self.resistance
        )

        self.assertEqual(signal.status, "BREAKDOWN_CONFIRMED")
        self.assertEqual(signal.action, "SELL")
        self.assertEqual(state["breakdown_timestamp"], 220)
        self.assertIsNotNone(signal.entry_price)
        self.assertGreater(signal.stop_loss, signal.entry_price)
        self.assertLess(signal.take_profit, signal.entry_price)
        self.assertAlmostEqual(
            signal.stop_loss - signal.entry_price,
            (signal.entry_price - signal.take_profit) / 2,
        )

    def test_hammer_buy_gets_long_trade_levels(self):
        candles = make_downtrend_candles()
        candles.append(
            Candle(timestamp=220, open=390.5, high=390.45, low=389.0, close=390.4, volume=100.0)
        )

        signal, _ = self.analyzer.generate_support_strategy_signal(
            candles, support=390.0, resistance=self.resistance
        )

        self.assertEqual(signal.status, "SUPPORT_BOUNCE_CONFIRMED")
        self.assertEqual(signal.action, "STRONG_BUY")
        self.assertIsNotNone(signal.entry_price)
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertGreater(signal.take_profit, signal.entry_price)

    def test_retest_rejection_sells_once_then_duplicate_candle_holds(self):
        candles = make_downtrend_candles()
        candles.extend(
            [
                Candle(timestamp=220, open=400.0, high=400.0, low=390.0, close=390.0, volume=200.0),
                Candle(timestamp=221, open=394.0, high=396.0, low=389.0, close=391.0, volume=100.0),
            ]
        )
        previous_state = {
            "status": "BREAKDOWN_CONFIRMED",
            "support": self.support,
            "breakdown_timestamp": 220,
            "last_candle_timestamp": 220,
        }

        signal, state = self.analyzer.generate_support_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=previous_state
        )
        duplicate_signal, _ = self.analyzer.generate_support_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=state
        )

        self.assertEqual(signal.status, "RETEST_REJECTED")
        self.assertEqual(signal.action, "SELL")
        self.assertEqual(duplicate_signal.status, "RETEST_REJECTED")
        self.assertEqual(duplicate_signal.action, "HOLD")

    def test_retest_rejection_does_not_sell_again_on_later_candle(self):
        candles = make_downtrend_candles()
        candles.extend(
            [
                Candle(timestamp=220, open=400.0, high=400.0, low=390.0, close=390.0, volume=200.0),
                Candle(timestamp=221, open=394.0, high=396.0, low=389.0, close=391.0, volume=100.0),
                Candle(timestamp=222, open=392.0, high=393.0, low=387.0, close=388.0, volume=200.0),
            ]
        )
        previous_state = {
            "status": "RETEST_REJECTED",
            "support": self.support,
            "breakdown_timestamp": 220,
            "last_candle_timestamp": 221,
        }

        signal, _ = self.analyzer.generate_support_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=previous_state
        )

        self.assertEqual(signal.status, "RETEST_REJECTED")
        self.assertEqual(signal.action, "HOLD")

    def test_reclaiming_support_invalidates_breakdown(self):
        candles = make_downtrend_candles()
        candles.append(
            Candle(timestamp=221, open=394.0, high=397.0, low=393.0, close=396.0, volume=100.0)
        )
        previous_state = {
            "status": "BREAKDOWN_CONFIRMED",
            "support": self.support,
            "breakdown_timestamp": 220,
            "last_candle_timestamp": 220,
        }

        signal, _ = self.analyzer.generate_support_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=previous_state
        )

        self.assertEqual(signal.status, "BREAKDOWN_INVALIDATED")
        self.assertEqual(signal.action, "HOLD")


class ResistanceStrategyTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MarketAnalyzer()
        self.support = 395.0
        self.resistance = 405.0

    def test_resistance_without_shooting_star_is_watch_only(self):
        candles = make_uptrend_candles()
        candles.append(
            Candle(timestamp=220, open=409.5, high=410.0, low=409.0, close=409.8, volume=100.0)
        )

        signal, _ = self.analyzer.generate_resistance_strategy_signal(
            candles, support=self.support, resistance=410.0
        )

        self.assertEqual(signal.status, "RESISTANCE_WATCH")
        self.assertEqual(signal.action, "HOLD")

    def test_shooting_star_at_resistance_gets_short_trade_levels(self):
        candles = make_uptrend_candles()
        candles.append(
            Candle(timestamp=220, open=409.0, high=411.0, low=409.0, close=409.2, volume=100.0)
        )

        signal, _ = self.analyzer.generate_strategy_signal(
            candles, support=self.support, resistance=410.0
        )

        self.assertEqual(signal.status, "RESISTANCE_REJECTION_CONFIRMED")
        self.assertEqual(signal.action, "STRONG_SELL")
        self.assertGreater(signal.stop_loss, signal.entry_price)
        self.assertLess(signal.take_profit, signal.entry_price)

    def test_breakout_requires_long_green_volume_and_uptrend(self):
        candles = make_uptrend_candles()
        candles.append(
            Candle(timestamp=220, open=400.0, high=410.0, low=400.0, close=410.0, volume=200.0)
        )

        signal, state = self.analyzer.generate_strategy_signal(
            candles, support=self.support, resistance=self.resistance
        )

        self.assertEqual(signal.status, "BREAKOUT_CONFIRMED")
        self.assertEqual(signal.action, "BUY")
        self.assertEqual(state["level"], "RESISTANCE")
        self.assertLess(signal.stop_loss, signal.entry_price)
        self.assertGreater(signal.take_profit, signal.entry_price)

    def test_support_watch_can_switch_to_resistance_breakout(self):
        candles = make_uptrend_candles()
        candles.append(
            Candle(timestamp=220, open=400.0, high=410.0, low=400.0, close=410.0, volume=200.0)
        )
        previous_state = {
            "level": "SUPPORT",
            "status": "SUPPORT_WATCH",
            "support": self.support,
            "last_candle_timestamp": 219,
        }

        signal, state = self.analyzer.generate_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=previous_state
        )

        self.assertEqual(state["level"], "RESISTANCE")
        self.assertEqual(signal.status, "BREAKOUT_CONFIRMED")
        self.assertEqual(signal.action, "BUY")

    def test_retest_holds_then_buys_once(self):
        candles = make_uptrend_candles()
        candles.extend(
            [
                Candle(timestamp=220, open=400.0, high=410.0, low=400.0, close=410.0, volume=200.0),
                Candle(timestamp=221, open=407.0, high=409.0, low=404.0, close=408.0, volume=100.0),
            ]
        )
        previous_state = {
            "level": "RESISTANCE",
            "status": "BREAKOUT_CONFIRMED",
            "resistance": self.resistance,
            "breakout_timestamp": 220,
            "last_candle_timestamp": 220,
        }

        signal, state = self.analyzer.generate_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=previous_state
        )
        duplicate_signal, _ = self.analyzer.generate_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=state
        )

        self.assertEqual(signal.status, "RETEST_HELD")
        self.assertEqual(signal.action, "BUY")
        self.assertEqual(duplicate_signal.status, "RETEST_HELD")
        self.assertEqual(duplicate_signal.action, "HOLD")

    def test_falling_back_below_resistance_invalidates_breakout(self):
        candles = make_uptrend_candles()
        candles.append(
            Candle(timestamp=221, open=407.0, high=408.0, low=403.0, close=404.0, volume=100.0)
        )
        previous_state = {
            "level": "RESISTANCE",
            "status": "BREAKOUT_CONFIRMED",
            "resistance": self.resistance,
            "breakout_timestamp": 220,
            "last_candle_timestamp": 220,
        }

        signal, _ = self.analyzer.generate_strategy_signal(
            candles, support=self.support, resistance=self.resistance, previous_state=previous_state
        )

        self.assertEqual(signal.status, "BREAKOUT_INVALIDATED")
        self.assertEqual(signal.action, "HOLD")


class TradingStateStoreTests(unittest.TestCase):
    def test_state_is_saved_and_loaded_per_key(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "trading_state.json"
            store = TradingStateStore(path)
            state = {"status": "SUPPORT_WATCH", "last_candle_timestamp": 220}

            store.save("PAXG/USDT|4h", state)

            self.assertEqual(store.get("PAXG/USDT|4h"), state)
            self.assertEqual(store.get("BTC/USDT|1h"), {})


class SignalSummaryTests(unittest.TestCase):
    def test_trade_levels_are_only_shown_for_trade_actions(self):
        trade_signal = Signal(
            action="SELL",
            position="NEUTRAL",
            price=390.0,
            reason="breakdown",
            status="BREAKDOWN_CONFIRMED",
            entry_price=390.0,
            stop_loss=400.0,
            take_profit=370.0,
        )
        hold_signal = Signal(
            action="HOLD",
            position="SUPPORT",
            price=395.0,
            reason="waiting",
            status="SUPPORT_WATCH",
        )

        trade_summary = build_signal_summary("PAXG/USDT", "4h", trade_signal, 395.0, 410.0, 390.0)
        hold_summary = build_signal_summary("PAXG/USDT", "4h", hold_signal, 395.0, 410.0, 395.0)

        self.assertIn("Entry: $390.00", trade_summary)
        self.assertIn("Stop Loss: $400.00", trade_summary)
        self.assertIn("Take Profit: $370.00", trade_summary)
        self.assertNotIn("Entry:", hold_summary)


if __name__ == "__main__":
    unittest.main()
