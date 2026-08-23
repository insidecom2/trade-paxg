import unittest
from datetime import datetime, timezone

from analyzer import MarketAnalyzer
from models import Candle
import liquidity_sweep as ls


def make_candle(ts_ms, open_, high, low, close, volume=100.0):
    return Candle(timestamp=ts_ms, open=open_, high=high, low=low, close=close, volume=volume)


def dt_ms(year, month, day, hour, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)


class NotificationWindowTests(unittest.TestCase):
    def test_within_window_on_weekday(self):
        now = datetime(2024, 1, 8, 12, 30, tzinfo=timezone.utc)  # Mon 19:30 Bangkok
        self.assertTrue(ls.is_within_notification_window(now))

    def test_within_window_at_open(self):
        now = datetime(2024, 1, 8, 5, 0, tzinfo=timezone.utc)  # Mon 12:00 Bangkok
        self.assertTrue(ls.is_within_notification_window(now))

    def test_outside_window_before_12_bangkok(self):
        now = datetime(2024, 1, 8, 4, 30, tzinfo=timezone.utc)  # Mon 11:30 Bangkok
        self.assertFalse(ls.is_within_notification_window(now))

    def test_outside_window_after_21_bangkok(self):
        now = datetime(2024, 1, 8, 14, 0, tzinfo=timezone.utc)  # Mon 21:00 Bangkok
        self.assertFalse(ls.is_within_notification_window(now))

    def test_outside_window_on_weekend(self):
        now = datetime(2024, 1, 6, 8, 30, tzinfo=timezone.utc)  # Sat 15:30 Bangkok
        self.assertFalse(ls.is_within_notification_window(now))


class SessionHighLowTests(unittest.TestCase):
    def test_returns_session_extremes_for_today_only(self):
        reference = datetime(2024, 1, 8, 12, 0, tzinfo=timezone.utc)
        candles = [
            make_candle(dt_ms(2024, 1, 7, 3), 100, 105, 95, 100),  # yesterday, excluded
            make_candle(dt_ms(2024, 1, 8, 1), 100, 102, 99, 101),  # Asian session
            make_candle(dt_ms(2024, 1, 8, 5), 101, 106, 98, 100),  # Asian session
            make_candle(dt_ms(2024, 1, 8, 9), 100, 110, 90, 101),  # London session
        ]
        asian = ls.session_high_low(candles, ls.ASIAN_SESSION_HOURS_UTC, reference)
        london = ls.session_high_low(candles, ls.LONDON_SESSION_HOURS_UTC, reference)
        self.assertEqual(asian, (106, 98))
        self.assertEqual(london, (110, 90))

    def test_returns_none_when_no_candles_in_session(self):
        reference = datetime(2024, 1, 8, 12, 0, tzinfo=timezone.utc)
        candles = [make_candle(dt_ms(2024, 1, 8, 9), 100, 101, 99, 100)]
        self.assertIsNone(ls.session_high_low(candles, ls.ASIAN_SESSION_HOURS_UTC, reference))


def _swing_test_candles():
    # Highs/lows vary monotonically around each override so no accidental
    # local-extrema ties occur between the intended swing points.
    highs = [100 + i * 0.01 for i in range(11)]
    lows = [99 - i * 0.01 for i in range(11)]
    highs[2] = 105  # older swing high
    lows[5] = 95  # older swing low
    highs[8] = 108  # most recent swing high
    return [make_candle(i, 100, highs[i], lows[i], 100) for i in range(11)]


class RecentSwingLevelsTests(unittest.TestCase):
    def test_returns_most_recent_swing_high_and_low(self):
        analyzer = MarketAnalyzer()
        recent_high, recent_low = ls.recent_swing_levels(analyzer, _swing_test_candles(), lookback=60)
        self.assertEqual(recent_high, 108)
        self.assertEqual(recent_low, 95)

    def test_build_key_zones_includes_recent_swing_levels(self):
        analyzer = MarketAnalyzer()
        zones = ls.build_key_zones(analyzer, _swing_test_candles(), None, None, price=100)
        names = {zone.name for zone in zones}
        self.assertIn("Recent Swing High", names)
        self.assertIn("Recent Swing Low", names)


class CandlesEstablishedBeforeLatestTests(unittest.TestCase):
    def test_drops_the_latest_candle(self):
        candles = [make_candle(0, 1, 2, 0, 1), make_candle(1, 1, 2, 0, 1)]
        self.assertEqual(ls.candles_established_before_latest(candles), candles[:1])

    def test_single_candle_is_kept_as_context(self):
        candles = [make_candle(0, 1, 2, 0, 1)]
        self.assertEqual(ls.candles_established_before_latest(candles), candles)

    def test_zone_building_ignores_the_candle_it_will_be_tested_against(self):
        reference = datetime(2024, 1, 8, 11, 0, tzinfo=timezone.utc)
        prior_high = make_candle(dt_ms(2024, 1, 8, 9), 100, 105, 99, 104)
        new_high_candle = make_candle(dt_ms(2024, 1, 8, 10, 55), 104, 110, 103, 109)
        zone_context = ls.candles_established_before_latest([prior_high, new_high_candle])
        london_range = ls.session_high_low(zone_context, ls.LONDON_SESSION_HOURS_UTC, reference)
        self.assertEqual(london_range, (105, 99))
        self.assertTrue(ls.detect_sweep(ls.Zone("London High", ls.RESISTANCE, london_range[0]), new_high_candle))


class CandleIntervalTests(unittest.TestCase):
    def test_candle_interval_matches_strategy_timeframe(self):
        self.assertEqual(ls.CANDLE_INTERVAL_MS, ls.TIMEFRAME_MINUTES[ls.STRATEGY_TIMEFRAME] * 60 * 1000)


class ZoneDetectionTests(unittest.TestCase):
    def test_detect_sweep_resistance(self):
        zone = ls.Zone("R", ls.RESISTANCE, 100.0)
        self.assertTrue(ls.detect_sweep(zone, make_candle(0, 99, 100.5, 98, 99.5)))
        self.assertFalse(ls.detect_sweep(zone, make_candle(0, 99, 99.9, 98, 99.5)))

    def test_detect_sweep_support(self):
        zone = ls.Zone("S", ls.SUPPORT, 100.0)
        self.assertTrue(ls.detect_sweep(zone, make_candle(0, 101, 102, 99.5, 101)))
        self.assertFalse(ls.detect_sweep(zone, make_candle(0, 101, 102, 100.1, 101)))

    def test_detect_close_back(self):
        resistance = ls.Zone("R", ls.RESISTANCE, 100.0)
        support = ls.Zone("S", ls.SUPPORT, 100.0)
        self.assertTrue(ls.detect_close_back(resistance, make_candle(0, 100.5, 100.6, 99.5, 99.9)))
        self.assertFalse(ls.detect_close_back(resistance, make_candle(0, 100.5, 100.6, 99.5, 100.1)))
        self.assertTrue(ls.detect_close_back(support, make_candle(0, 99.5, 100.5, 99.4, 100.1)))
        self.assertFalse(ls.detect_close_back(support, make_candle(0, 99.5, 100.5, 99.4, 99.9)))

    def test_direction_for_zone_side(self):
        self.assertEqual(ls.direction_for_zone_side(ls.RESISTANCE), "SELL")
        self.assertEqual(ls.direction_for_zone_side(ls.SUPPORT), "BUY")


class CandleColorTests(unittest.TestCase):
    def test_candle_color(self):
        self.assertEqual(ls.candle_color(make_candle(0, 100, 101, 99, 100.5)), "GREEN")
        self.assertEqual(ls.candle_color(make_candle(0, 100, 101, 99, 99.5)), "RED")
        self.assertEqual(ls.candle_color(make_candle(0, 100, 101, 99, 100)), "NEUTRAL")

    def test_is_long_candle(self):
        big = make_candle(0, 100, 102, 99, 101.5)  # body = 1.5
        small = make_candle(0, 100, 100.3, 99.9, 100.1)  # body = 0.1
        self.assertTrue(ls.is_long_candle(big, atr_value=1.0))
        self.assertFalse(ls.is_long_candle(small, atr_value=1.0))
        self.assertFalse(ls.is_long_candle(big, atr_value=None))

    def test_opposite_streak_length(self):
        candles = [
            make_candle(0, 100, 101, 99, 100.5),  # GREEN
            make_candle(1, 100, 101, 99, 99.5),   # RED
            make_candle(2, 100, 101, 99, 99.5),   # RED
        ]
        self.assertEqual(ls.opposite_streak_length(candles, "RED"), 2)
        self.assertEqual(ls.opposite_streak_length(candles, "GREEN"), 0)


class ReversalCandleTests(unittest.TestCase):
    def test_buy_requires_green_after_a_red_streak(self):
        red = make_candle(0, 100, 100.2, 99.8, 99.9)
        candles = [red, red, make_candle(2, 99.9, 100.3, 99.85, 100.2)]
        self.assertTrue(ls.detect_reversal_candle("BUY", candles, atr_value=None))

    def test_buy_rejects_a_single_red_candle_with_no_streak_or_size(self):
        red = make_candle(0, 100, 100.2, 99.8, 99.9)
        green = make_candle(1, 99.9, 100.3, 99.85, 100.2)
        self.assertFalse(ls.detect_reversal_candle("BUY", [red, green], atr_value=None))

    def test_buy_accepts_a_single_long_red_candle(self):
        long_red = make_candle(0, 101, 101.1, 99, 99.1)  # body 1.9
        green = make_candle(1, 99.1, 99.5, 99.0, 99.4)
        self.assertTrue(ls.detect_reversal_candle("BUY", [long_red, green], atr_value=1.0))

    def test_buy_rejects_when_current_candle_is_not_green(self):
        red = make_candle(0, 100, 100.2, 99.8, 99.9)
        candles = [red, red, make_candle(2, 100, 100.1, 99.8, 99.9)]  # still red
        self.assertFalse(ls.detect_reversal_candle("BUY", candles, atr_value=None))

    def test_sell_requires_red_after_a_green_streak(self):
        green = make_candle(0, 99.9, 100.3, 99.85, 100.2)
        candles = [green, green, make_candle(2, 100.2, 100.3, 99.7, 99.8)]
        self.assertTrue(ls.detect_reversal_candle("SELL", candles, atr_value=None))

    def test_requires_at_least_two_candles(self):
        self.assertFalse(ls.detect_reversal_candle("BUY", [make_candle(0, 100, 101, 99, 100.5)], atr_value=None))


class StaleSetupTests(unittest.TestCase):
    def test_far_from_zone_with_atr_is_stale(self):
        self.assertTrue(ls.is_setup_stale(zone_price=100.0, price=110.0, atr_value=1.0))

    def test_close_to_zone_is_not_stale(self):
        self.assertFalse(ls.is_setup_stale(zone_price=100.0, price=101.0, atr_value=1.0))

    def test_no_atr_is_never_stale(self):
        self.assertFalse(ls.is_setup_stale(zone_price=100.0, price=200.0, atr_value=None))


class TradeLevelTests(unittest.TestCase):
    def test_compute_trade_levels_sell(self):
        stop_loss, take_profit = ls.compute_trade_levels("SELL", 101.2, 99.5, 98.0, atr_value=0.3)
        self.assertAlmostEqual(stop_loss, 101.2 + ls.STOP_LOSS_ATR_BUFFER * 0.3)
        self.assertEqual(take_profit, 98.0)

    def test_compute_trade_levels_sell_ignores_invalid_target(self):
        stop_loss, take_profit = ls.compute_trade_levels("SELL", 101.2, 99.5, 102.0, atr_value=0.3)
        self.assertIsNone(take_profit)

    def test_compute_trade_levels_buy(self):
        stop_loss, take_profit = ls.compute_trade_levels("BUY", 98.0, 99.5, 101.0, atr_value=0.3)
        self.assertAlmostEqual(stop_loss, 98.0 - ls.STOP_LOSS_ATR_BUFFER * 0.3)
        self.assertEqual(take_profit, 101.0)

    def test_opposite_liquidity_level(self):
        zones = [ls.Zone("R", ls.RESISTANCE, 101.0), ls.Zone("S", ls.SUPPORT, 98.0)]
        self.assertEqual(ls.opposite_liquidity_level(zones, "SELL", 99.5), 98.0)
        self.assertEqual(ls.opposite_liquidity_level(zones, "BUY", 99.5), 101.0)
        self.assertIsNone(ls.opposite_liquidity_level([], "SELL", 99.5))

    def test_risk_reward_ratio(self):
        self.assertAlmostEqual(ls.risk_reward_ratio(100.0, 101.0, 97.0), 3.0)
        self.assertIsNone(ls.risk_reward_ratio(100.0, 101.0, None))
        self.assertIsNone(ls.risk_reward_ratio(100.0, 100.0, 97.0))


class AdvanceStateTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = MarketAnalyzer()
        self.symbol = "PAXG/USDT"
        self.zones = [ls.Zone("Key Resistance", ls.RESISTANCE, 101.0), ls.Zone("Key Support", ls.SUPPORT, 98.0)]
        self.base_ts = dt_ms(2024, 1, 8, 11, 0)

    def _ts(self, index):
        return self.base_ts + index * 5 * 60 * 1000

    def test_full_sell_sequence_reaches_entry_on_close_back(self):
        candles = [make_candle(self._ts(0), 99.9, 100.0, 99.7, 99.9)]

        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, {}, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)

        candles.append(make_candle(self._ts(1), 100.7, 100.9, 100.6, 100.85))
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_NEAR_ZONE)
        self.assertIn("Key Resistance", messages[0])

        candles.append(make_candle(self._ts(2), 100.9, 101.2, 100.7, 101.0))  # sweep, GREEN
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_SWEPT)
        self.assertEqual(state["sweep_extreme"], 101.2)

        candles.append(make_candle(self._ts(3), 101.0, 101.15, 100.95, 101.1))  # still above zone, GREEN
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_SWEPT)
        self.assertEqual(messages, [])

        candles.append(make_candle(self._ts(4), 101.1, 101.15, 100.4, 100.5))  # closes back, RED
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_ENTERED)
        self.assertEqual(state["direction"], "SELL")
        self.assertEqual(state["entry_price"], 100.5)
        self.assertEqual(state["take_profit"], 98.0)
        self.assertGreaterEqual(state["stop_loss"], 101.2)
        self.assertEqual(len(messages), 2)
        self.assertIn("ตรวจพบแท่งกลับตัว", messages[0])
        self.assertIn("SELL", messages[1])
        self.assertIn("เด้งกลับ", messages[1])

    def test_buy_setup_enters_on_close_back_above_support(self):
        candles = [make_candle(self._ts(0), 98.1, 98.3, 98.0, 98.1)]
        state, _ = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, {}, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_NEAR_ZONE)

        candles.append(make_candle(self._ts(1), 98.0, 98.2, 97.6, 97.9))  # sweep, RED
        state, _ = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_SWEPT)
        self.assertEqual(state["sweep_extreme"], 97.6)

        candles.append(make_candle(self._ts(2), 97.9, 98.0, 97.7, 97.8))  # still below zone, RED
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_SWEPT)
        self.assertEqual(messages, [])

        candles.append(make_candle(self._ts(3), 97.8, 98.5, 97.7, 98.4))  # closes back above, GREEN
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_ENTERED)
        self.assertEqual(state["direction"], "BUY")
        self.assertEqual(state["entry_price"], 98.4)
        self.assertEqual(state["take_profit"], 101.0)
        self.assertLessEqual(state["stop_loss"], 97.6)
        self.assertEqual(len(messages), 2)
        self.assertIn("ตรวจพบแท่งกลับตัว", messages[0])
        self.assertIn("BUY", messages[1])

    def test_confirmation_phase_is_used_when_more_than_one_candle_is_required(self):
        original_confirmation_candles = ls.CONFIRMATION_CANDLES
        ls.CONFIRMATION_CANDLES = 2
        try:
            zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
            state = {
                **zone.to_dict(),
                "phase": ls.PHASE_SWEPT,
                "sweep_extreme": 101.2,
                "sweep_timestamp": self._ts(0),
                "expires_at": self._ts(10),
                "last_candle_timestamp": self._ts(0),
            }
            candles = [
                make_candle(self._ts(0), 100.1, 100.5, 100.0, 100.4),  # GREEN, history
                make_candle(self._ts(1), 100.3, 100.95, 100.2, 100.5),  # GREEN, close-back
            ]
            state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
            self.assertEqual(state["phase"], ls.PHASE_CONFIRMING)
            self.assertIn("Key Resistance", messages[0])

            candles.append(make_candle(self._ts(2), 100.5, 100.6, 100.2, 100.3))  # RED, still holding
            state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
            self.assertEqual(state["phase"], ls.PHASE_ENTERED)
            self.assertEqual(state["entry_price"], 100.3)
        finally:
            ls.CONFIRMATION_CANDLES = original_confirmation_candles

    def test_confirmation_fails_when_price_falls_back_through_zone(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_CONFIRMING,
            "sweep_extreme": 101.2,
            "confirmation_count": 1,
            "expires_at": self._ts(5),
            "last_candle_timestamp": self._ts(0),
        }
        candles = [make_candle(self._ts(1), 100.6, 101.05, 100.5, 101.05)]  # closes back above the zone again
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertIn("อาจเป็น Breakout จริง", messages[0])

    def test_confirmation_expires_back_to_idle(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_CONFIRMING,
            "sweep_extreme": 101.2,
            "confirmation_count": 1,
            "expires_at": self._ts(1),
            "last_candle_timestamp": self._ts(0),
        }
        candles = [make_candle(self._ts(1), 100.5, 100.6, 100.3, 100.4)]  # still holding, but past expiry
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertTrue(messages)

    def test_skips_entry_when_risk_reward_is_too_low(self):
        zones = [ls.Zone("Key Resistance", ls.RESISTANCE, 101.0), ls.Zone("Key Support", ls.SUPPORT, 100.77)]
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_CONFIRMING,
            "sweep_extreme": 101.2,
            "confirmation_count": 1,
            "expires_at": self._ts(5),
            "last_candle_timestamp": self._ts(0),
        }
        candles = [
            make_candle(self._ts(-2), 100.7, 101.1, 100.6, 100.9),  # GREEN
            make_candle(self._ts(-1), 100.9, 101.1, 100.8, 101.0),  # GREEN, 2-candle streak
            make_candle(self._ts(1), 100.9, 100.95, 100.7, 100.8),  # RED entry candle
        ]
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertEqual(len(messages), 2)
        self.assertIn("ตรวจพบแท่งกลับตัว", messages[0])
        self.assertIn("โซนเป้าหมายฝั่งตรงข้ามใกล้เกินไป", messages[1])

    def test_skips_entry_when_candle_has_not_flipped_color(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_CONFIRMING,
            "sweep_extreme": 101.2,
            "confirmation_count": 1,
            "expires_at": self._ts(5),
            "last_candle_timestamp": self._ts(0),
        }
        candles = [
            make_candle(self._ts(0), 100.6, 100.95, 100.5, 100.4),  # RED, not the required flip for SELL
            make_candle(self._ts(1), 100.5, 100.6, 100.3, 100.4),  # RED too, no color flip
        ]
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertEqual(len(messages), 1)
        self.assertIn("แท่งเทียนยังไม่กลับสี", messages[0])

    def test_idle_stays_idle_when_no_zone_nearby(self):
        candles = [make_candle(self._ts(0), 50, 50.1, 49.9, 50)]
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, {}, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertEqual(messages, [])

    def test_near_zone_resets_when_price_drifts_away_without_sweeping(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_NEAR_ZONE,
            "last_candle_timestamp": self._ts(0),
        }
        candles = [make_candle(self._ts(1), 95, 95.1, 94.9, 95)]
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertEqual(messages, [])

    def test_swept_is_abandoned_when_price_runs_far_from_the_zone(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_SWEPT,
            "sweep_extreme": 101.2,
            "sweep_timestamp": self._ts(0),
            "expires_at": self._ts(30),
            "last_candle_timestamp": self._ts(-1),
        }
        history = [make_candle(self._ts(i), 100, 100.3, 99.7, 100) for i in range(15)]
        far_away_candle = make_candle(self._ts(15), 109, 110.5, 108.5, 110)
        candles = history + [far_away_candle]
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertIn("ยกเลิก", messages[0])
        self.assertIn("ราคาวิ่งหนีไปไกล", messages[0])

    def test_swept_expires_back_to_idle(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_SWEPT,
            "sweep_extreme": 101.2,
            "sweep_timestamp": self._ts(0),
            "expires_at": self._ts(1),
            "last_candle_timestamp": self._ts(0),
        }
        candles = [make_candle(self._ts(1), 101.05, 101.1, 101.0, 101.05)]  # still above zone, no close-back
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)
        self.assertIn("ยังไม่เข้าไม้", messages[0])

    def test_entered_state_persists_until_cooldown_expires(self):
        zone = ls.Zone("Key Resistance", ls.RESISTANCE, 101.0)
        state = {
            **zone.to_dict(),
            "phase": ls.PHASE_ENTERED,
            "direction": "SELL",
            "entry_price": 100.5,
            "stop_loss": 101.3,
            "take_profit": 98.0,
            "expires_at": self._ts(2),
            "last_candle_timestamp": self._ts(0),
        }
        candles = [make_candle(self._ts(1), 100, 100.1, 99.9, 100)]
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_ENTERED)
        self.assertEqual(messages, [])

        candles.append(make_candle(self._ts(2), 100, 100.1, 99.9, 100))
        state, messages = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, state, self.symbol)
        self.assertEqual(state["phase"], ls.PHASE_IDLE)

    def test_same_candle_is_not_reprocessed(self):
        candles = [make_candle(self._ts(0), 50, 50.1, 49.9, 50)]
        state, _ = ls.advance_liquidity_sweep_state(self.analyzer, candles, self.zones, {}, self.symbol)
        state_again, messages_again = ls.advance_liquidity_sweep_state(
            self.analyzer, candles, self.zones, state, self.symbol
        )
        self.assertEqual(state, state_again)
        self.assertEqual(messages_again, [])


if __name__ == "__main__":
    unittest.main()
