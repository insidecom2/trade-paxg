import numpy as np
from typing import Any, Dict, List, Optional, Tuple
from models import Candle, Zone, Signal

class MarketAnalyzer:
    """
    Analyzes Supply/Demand and Support/Resistance zones.
    """
    def __init__(self, imbalance_threshold: float = 2.0):
        # imbalance_threshold: How many times larger the explosive candle is vs average
        self.imbalance_threshold = imbalance_threshold

    def find_snd_zones(self, candles: List[Candle]) -> List[Zone]:
        zones = []
        # Simple SND Logic: Look for a small candle (Base) followed by a large candle (Imbalance)
        for i in range(1, len(candles) - 1):
            body_size = abs(candles[i].close - candles[i].open)
            next_body_size = abs(candles[i+1].close - candles[i+1].open)
            
            if next_body_size > (body_size * self.imbalance_threshold) and body_size > 0:
                if candles[i+1].close > candles[i+1].open: # Bullish Imbalance -> Demand Zone
                    zones.append(Zone(type="DEMAND", top=candles[i].high, bottom=candles[i].low))
                else: # Bearish Imbalance -> Supply Zone
                    zones.append(Zone(type="SUPPLY", top=candles[i].high, bottom=candles[i].low))
        return zones

    def find_support_resistance(self, candles: List[Candle]) -> Tuple[List[float], List[float]]:
        """
        Finds SE (Support/Resistance) using local extrema.
        """
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        
        # Use a simple window to find peaks/troughs
        res_levels = []
        sup_levels = []
        
        for i in range(2, len(highs) - 2):
            if highs[i] == max(highs[i-2:i+3]):
                res_levels.append(highs[i])
            if lows[i] == min(lows[i-2:i+3]):
                sup_levels.append(lows[i])
                
        return res_levels, sup_levels

    def _cluster_levels(self, levels: List[float], tolerance: float = 0.002) -> List[float]:
        """Collapses levels within `tolerance` of each other to remove near-duplicates."""
        if not levels:
            return []
        levels = sorted(levels)
        clusters = [[levels[0]]]
        for level in levels[1:]:
            if abs(level - clusters[-1][0]) <= tolerance * clusters[-1][0]:
                clusters[-1].append(level)
            else:
                clusters.append([level])
        return [float(np.mean(c)) for c in clusters]

    def find_dynamic_levels(self, candles: List[Candle], price: float, lookback: int = 30) -> Tuple[float, float]:
        """
        Returns the nearest support below and nearest resistance above `price`,
        computed from the candles' real swing levels. Falls back to recent
        lows/highs when no swing level exists in that direction.
        """
        res_levels, sup_levels = self.find_support_resistance(candles)
        supports = self._cluster_levels(sup_levels)
        resistances = self._cluster_levels(res_levels)

        support = max((l for l in supports if l <= price), default=None)
        if support is None:
            support = min(supports, default=None)
        if support is None:
            support = min(c.low for c in candles[-lookback:])

        resistance = min((l for l in resistances if l >= price), default=None)
        if resistance is None:
            resistance = max(resistances, default=None)
        if resistance is None:
            resistance = max(c.high for c in candles[-lookback:])

        return support, resistance

    def detect_candle_pattern(self, candle: Candle, avg_body: float, long_factor: float = 2.0) -> Optional[str]:
        """
        Classifies a single candle into a pattern.
        - HAMMER: long lower wick, small body, tiny upper wick (bullish reversal at support)
        - SHOOTING_STAR: long upper wick, small body, tiny lower wick (bearish reversal at resistance)
        - LONG_RED / LONG_GREEN: body at least `long_factor` times the average body
        """
        body = abs(candle.close - candle.open)
        lower_wick = min(candle.open, candle.close) - candle.low
        upper_wick = candle.high - max(candle.open, candle.close)

        if body <= 0:
            return None

        if lower_wick >= 2.0 * body and upper_wick <= 0.5 * body:
            return "HAMMER"
        if upper_wick >= 2.0 * body and lower_wick <= 0.5 * body:
            return "SHOOTING_STAR"
        if body >= long_factor * avg_body:
            return "LONG_RED" if candle.close < candle.open else "LONG_GREEN"
        return None

    def classify_position(self, price: float, support: float, resistance: float, tolerance: float = 0.003) -> str:
        if abs(price - support) <= tolerance * support:
            return "SUPPORT"
        if abs(price - resistance) <= tolerance * resistance:
            return "RESISTANCE"
        return "NEUTRAL"

    def decide_action(self, position: str, pattern: Optional[str], price: float, support: float, resistance: float) -> Tuple[str, str]:
        if position == "SUPPORT" and pattern == "HAMMER":
            return "STRONG_BUY", "Hammer at support: buyers pushed price back up, bullish reversal confirmed"
        if pattern == "LONG_RED" and price < support:
            return "SELL", "Long red candle broke support: strong downtrend, stop loss"
        if position == "RESISTANCE" and pattern == "SHOOTING_STAR":
            return "STRONG_SELL", "Shooting star at resistance: sellers rejected price, bearish reversal confirmed"
        if pattern == "LONG_GREEN" and price > resistance:
            return "BUY", "Long green candle broke resistance: breakout, follow trend"
        return "HOLD", "No strong signal: price/pattern not aligned at key levels"

    def generate_signal(self, candles: List[Candle], support: float, resistance: float, tolerance: float = 0.003, long_factor: float = 2.0) -> Signal:
        candle = candles[-1]
        bodies = [abs(c.close - c.open) for c in candles[:-1]]
        avg_body = float(np.mean(bodies)) if bodies else abs(candle.close - candle.open)
        pattern = self.detect_candle_pattern(candle, avg_body, long_factor)
        position = self.classify_position(candle.close, support, resistance, tolerance)
        action, reason = self.decide_action(position, pattern, candle.close, support, resistance)
        return Signal(action=action, position=position, pattern=pattern, price=candle.close, reason=reason)

    @staticmethod
    def _ema(values: List[float], period: int) -> Optional[float]:
        if len(values) < period:
            return None
        multiplier = 2.0 / (period + 1)
        ema = float(np.mean(values[:period]))
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        return ema

    def is_downtrend(self, candles: List[Candle], fast_period: int = 50, slow_period: int = 200) -> bool:
        """Returns true when EMA50 is below EMA200 and price is below EMA50."""
        closes = [c.close for c in candles]
        fast_ema = self._ema(closes, fast_period)
        slow_ema = self._ema(closes, slow_period)
        if fast_ema is None or slow_ema is None:
            return False
        return fast_ema < slow_ema and closes[-1] < fast_ema

    def is_uptrend(self, candles: List[Candle], fast_period: int = 50, slow_period: int = 200) -> bool:
        """Returns true when EMA50 is above EMA200 and price is above EMA50."""
        closes = [c.close for c in candles]
        fast_ema = self._ema(closes, fast_period)
        slow_ema = self._ema(closes, slow_period)
        if fast_ema is None or slow_ema is None:
            return False
        return fast_ema > slow_ema and closes[-1] > fast_ema

    @staticmethod
    def volume_ratio(candles: List[Candle], lookback: int = 20) -> Optional[float]:
        """Compares the latest closed candle volume with the prior average volume."""
        if len(candles) <= lookback:
            return None
        average_volume = float(np.mean([c.volume for c in candles[-lookback-1:-1]]))
        if average_volume <= 0:
            return None
        return candles[-1].volume / average_volume

    @staticmethod
    def atr(candles: List[Candle], period: int = 14) -> Optional[float]:
        """Calculates simple ATR from closed candles."""
        if len(candles) < period + 1:
            return None
        true_ranges = []
        for candle, previous in zip(candles[1:], candles[:-1]):
            true_ranges.append(
                max(
                    candle.high - candle.low,
                    abs(candle.high - previous.close),
                    abs(candle.low - previous.close),
                )
            )
        return float(np.mean(true_ranges[-period:]))

    def calculate_trade_levels(
        self,
        action: str,
        entry_price: float,
        candles: List[Candle],
        stop_atr_multiplier: float = 1.5,
        target_atr_multiplier: float = 3.0,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Returns stop-loss and take-profit for a long or short signal."""
        current_atr = self.atr(candles)
        if current_atr is None or current_atr <= 0:
            return None, None
        risk_distance = stop_atr_multiplier * current_atr
        reward_distance = target_atr_multiplier * current_atr
        if action in {"BUY", "STRONG_BUY"}:
            return entry_price - risk_distance, entry_price + reward_distance
        if action in {"SELL", "STRONG_SELL"}:
            return entry_price + risk_distance, entry_price - reward_distance
        return None, None

    def generate_support_strategy_signal(
        self,
        candles: List[Candle],
        support: float,
        resistance: float,
        previous_state: Optional[Dict[str, Any]] = None,
        tolerance: float = 0.003,
        volume_multiplier: float = 1.2,
        long_factor: float = 2.0,
    ) -> Tuple[Signal, Dict[str, Any]]:
        """Evaluates support tests, breakdowns, retests, and invalidations."""
        if not candles:
            raise ValueError("At least one closed candle is required")

        candle = candles[-1]
        bodies = [abs(c.close - c.open) for c in candles[:-1]]
        avg_body = float(np.mean(bodies)) if bodies else abs(candle.close - candle.open)
        pattern = self.detect_candle_pattern(candle, avg_body, long_factor)
        position = self.classify_position(candle.close, support, resistance, tolerance)
        volume_ratio = self.volume_ratio(candles)
        volume_high = volume_ratio is not None and volume_ratio >= volume_multiplier
        downtrend = self.is_downtrend(candles)

        state = dict(previous_state or {})
        previous_status = state.get("status", "NEUTRAL")
        tracked_support = float(state.get("support", support))
        candle_timestamp = int(candle.timestamp)

        if state.get("last_candle_timestamp") == candle_timestamp:
            signal = Signal(
                action="HOLD",
                position=position,
                pattern=pattern,
                price=candle.close,
                reason="Candle already processed; waiting for a new closed candle",
                status=previous_status,
            )
            return signal, state

        action = "HOLD"
        status = "NEUTRAL"
        reason = "No support strategy signal"

        active_breakdown = previous_status in {
            "BREAKDOWN_WATCH",
            "BREAKDOWN_CONFIRMED",
            "RETEST_REJECTED",
        }
        if active_breakdown:
            if candle.close >= tracked_support:
                status = "BREAKDOWN_INVALIDATED"
                reason = "Price reclaimed support; breakdown signal invalidated"
            elif previous_status == "BREAKDOWN_CONFIRMED":
                retest_touched = (
                    candle.timestamp > int(state.get("breakdown_timestamp", 0))
                    and candle.high >= tracked_support * (1 - tolerance)
                )
                if retest_touched:
                    status = "RETEST_REJECTED"
                    action = "SELL"
                    reason = "Price retested support and closed below it; breakdown confirmed"
                else:
                    status = previous_status
                    reason = "Breakdown confirmed; waiting for a support retest"
            elif previous_status == "RETEST_REJECTED":
                status = previous_status
                reason = "Retest rejection already signaled; waiting for a new setup"
            elif pattern == "LONG_RED" and volume_high and downtrend:
                status = "BREAKDOWN_CONFIRMED"
                action = "SELL"
                reason = "Support breakdown confirmed by LONG_RED, high volume, and downtrend"
            else:
                status = previous_status
                reason = "Breakdown watch active; confirmation conditions are incomplete"
        elif candle.close < support:
            if pattern == "LONG_RED" and volume_high and downtrend:
                status = "BREAKDOWN_CONFIRMED"
                action = "SELL"
                reason = "Support breakdown confirmed by LONG_RED, high volume, and downtrend"
            else:
                status = "BREAKDOWN_WATCH"
                reason = "Price is below support; waiting for LONG_RED, high volume, and downtrend"
        elif position == "SUPPORT":
            if pattern == "HAMMER":
                status = "SUPPORT_BOUNCE_CONFIRMED"
                action = "STRONG_BUY"
                reason = "Hammer at support: buyers pushed price back up, bullish reversal confirmed"
            else:
                status = "SUPPORT_WATCH"
                reason = "Price is at support without a Hammer; no trade"
        else:
            status = "NEUTRAL"
            action, reason = self.decide_action(position, pattern, candle.close, support, resistance)

        next_state = {
            "status": status,
            "support": tracked_support if active_breakdown else float(support),
            "last_candle_timestamp": candle_timestamp,
        }
        if status == "BREAKDOWN_CONFIRMED" and action == "SELL":
            next_state["breakdown_timestamp"] = candle_timestamp
        elif active_breakdown and "breakdown_timestamp" in state:
            next_state["breakdown_timestamp"] = state["breakdown_timestamp"]
        if volume_ratio is not None:
            next_state["volume_ratio"] = round(float(volume_ratio), 4)
        next_state["downtrend"] = downtrend

        entry_price = None
        stop_loss = None
        take_profit = None
        if action in {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"}:
            entry_price = float(candle.close)
            stop_loss, take_profit = self.calculate_trade_levels(action, entry_price, candles)

        signal = Signal(
            action=action,
            position=position,
            pattern=pattern,
            price=candle.close,
            reason=reason,
            status=status,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        return signal, next_state

    def generate_resistance_strategy_signal(
        self,
        candles: List[Candle],
        support: float,
        resistance: float,
        previous_state: Optional[Dict[str, Any]] = None,
        tolerance: float = 0.003,
        volume_multiplier: float = 1.2,
        long_factor: float = 2.0,
    ) -> Tuple[Signal, Dict[str, Any]]:
        """Evaluates resistance tests, breakouts, retests, and invalidations."""
        if not candles:
            raise ValueError("At least one closed candle is required")

        candle = candles[-1]
        bodies = [abs(c.close - c.open) for c in candles[:-1]]
        avg_body = float(np.mean(bodies)) if bodies else abs(candle.close - candle.open)
        pattern = self.detect_candle_pattern(candle, avg_body, long_factor)
        position = self.classify_position(candle.close, support, resistance, tolerance)
        volume_ratio = self.volume_ratio(candles)
        volume_high = volume_ratio is not None and volume_ratio >= volume_multiplier
        uptrend = self.is_uptrend(candles)

        state = dict(previous_state or {})
        previous_status = state.get("status", "NEUTRAL")
        tracked_resistance = float(state.get("resistance", resistance))
        candle_timestamp = int(candle.timestamp)

        if state.get("last_candle_timestamp") == candle_timestamp:
            signal = Signal(
                action="HOLD",
                position=position,
                pattern=pattern,
                price=candle.close,
                reason="Candle already processed; waiting for a new closed candle",
                status=previous_status,
            )
            return signal, state

        action = "HOLD"
        status = "NEUTRAL"
        reason = "No resistance strategy signal"

        active_breakout = previous_status in {
            "BREAKOUT_WATCH",
            "BREAKOUT_CONFIRMED",
            "RETEST_HELD",
        }
        if active_breakout:
            if candle.close <= tracked_resistance:
                status = "BREAKOUT_INVALIDATED"
                reason = "Price fell back below resistance; breakout signal invalidated"
            elif previous_status == "BREAKOUT_CONFIRMED":
                retest_held = (
                    candle.timestamp > int(state.get("breakout_timestamp", 0))
                    and candle.low <= tracked_resistance * (1 + tolerance)
                )
                if retest_held:
                    status = "RETEST_HELD"
                    action = "BUY"
                    reason = "Price retested resistance and closed above it; breakout confirmed"
                else:
                    status = previous_status
                    reason = "Breakout confirmed; waiting for a resistance retest"
            elif previous_status == "RETEST_HELD":
                status = previous_status
                reason = "Resistance retest already signaled; waiting for a new setup"
            elif pattern == "LONG_GREEN" and volume_high and uptrend:
                status = "BREAKOUT_CONFIRMED"
                action = "BUY"
                reason = "Resistance breakout confirmed by LONG_GREEN, high volume, and uptrend"
            else:
                status = previous_status
                reason = "Breakout watch active; confirmation conditions are incomplete"
        elif candle.close > resistance:
            if pattern == "LONG_GREEN" and volume_high and uptrend:
                status = "BREAKOUT_CONFIRMED"
                action = "BUY"
                reason = "Resistance breakout confirmed by LONG_GREEN, high volume, and uptrend"
            else:
                status = "BREAKOUT_WATCH"
                reason = "Price is above resistance; waiting for LONG_GREEN, high volume, and uptrend"
        elif position == "RESISTANCE":
            if pattern == "SHOOTING_STAR":
                status = "RESISTANCE_REJECTION_CONFIRMED"
                action = "STRONG_SELL"
                reason = "Shooting star at resistance: sellers rejected price, bearish reversal confirmed"
            else:
                status = "RESISTANCE_WATCH"
                reason = "Price is at resistance without a Shooting Star; no trade"
        else:
            status = "NEUTRAL"
            action, reason = self.decide_action(position, pattern, candle.close, support, resistance)

        next_state = {
            "status": status,
            "resistance": tracked_resistance if active_breakout else float(resistance),
            "last_candle_timestamp": candle_timestamp,
        }
        if status == "BREAKOUT_CONFIRMED" and action == "BUY":
            next_state["breakout_timestamp"] = candle_timestamp
        elif active_breakout and "breakout_timestamp" in state:
            next_state["breakout_timestamp"] = state["breakout_timestamp"]
        if volume_ratio is not None:
            next_state["volume_ratio"] = round(float(volume_ratio), 4)
        next_state["uptrend"] = uptrend

        entry_price = None
        stop_loss = None
        take_profit = None
        if action in {"BUY", "STRONG_BUY", "SELL", "STRONG_SELL"}:
            entry_price = float(candle.close)
            stop_loss, take_profit = self.calculate_trade_levels(action, entry_price, candles)

        signal = Signal(
            action=action,
            position=position,
            pattern=pattern,
            price=candle.close,
            reason=reason,
            status=status,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        return signal, next_state

    def generate_strategy_signal(
        self,
        candles: List[Candle],
        support: float,
        resistance: float,
        previous_state: Optional[Dict[str, Any]] = None,
        tolerance: float = 0.003,
    ) -> Tuple[Signal, Dict[str, Any]]:
        """Selects support or resistance strategy based on the active level."""
        if not candles:
            raise ValueError("At least one closed candle is required")

        state = dict(previous_state or {})
        previous_level = state.get("level")
        previous_status = state.get("status", "NEUTRAL")
        support_statuses = {"BREAKDOWN_WATCH", "BREAKDOWN_CONFIRMED", "RETEST_REJECTED"}
        resistance_statuses = {"BREAKOUT_WATCH", "BREAKOUT_CONFIRMED", "RETEST_HELD"}

        if previous_level == "RESISTANCE" and previous_status in resistance_statuses:
            level = "RESISTANCE"
        elif previous_level == "SUPPORT" and previous_status in support_statuses:
            level = "SUPPORT"
        elif candles[-1].close >= resistance * (1 - tolerance):
            level = "RESISTANCE"
        else:
            level = "SUPPORT"

        if level == "RESISTANCE":
            signal, next_state = self.generate_resistance_strategy_signal(
                candles, support, resistance, previous_state=state, tolerance=tolerance
            )
        else:
            signal, next_state = self.generate_support_strategy_signal(
                candles, support, resistance, previous_state=state, tolerance=tolerance
            )
        next_state["level"] = level
        return signal, next_state
