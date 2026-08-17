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

    def find_recent_snd_zones(
        self,
        candles: List[Candle],
        price: float,
        lookback: int = 60,
        limit: int = 3,
    ) -> List[Zone]:
        """Returns the latest zones that are near or contain the current price."""
        if not candles or limit <= 0:
            return []

        recent_candles = candles[-max(5, lookback):]
        zones = self.find_snd_zones(recent_candles)
        tolerance = self.calculate_zone_tolerance(recent_candles)
        max_distance = tolerance * price
        nearby_zones = []

        for zone in reversed(zones):
            distance = max(zone.bottom - price, price - zone.top, 0.0)
            if distance <= max_distance:
                nearby_zones.append(zone)
                if len(nearby_zones) == limit:
                    break

        return nearby_zones

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

    def find_key_levels(
        self,
        candles: List[Candle],
        price: float,
        lookback: int = 60,
        limit: int = 3,
    ) -> Tuple[List[float], List[float]]:
        """Returns recent resistance above and support below the current price."""
        if not candles or limit <= 0:
            return [], []

        recent_candles = candles[-max(5, lookback):]
        res_levels, sup_levels = self.find_support_resistance(recent_candles)
        resistances = self._cluster_levels(res_levels)
        supports = self._cluster_levels(sup_levels)

        recent_resistances = sorted(
            (level for level in resistances if level >= price),
        )[:limit]
        recent_supports = sorted(
            (level for level in supports if level <= price),
            reverse=True,
        )[:limit]
        return recent_resistances, recent_supports

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

    def find_dynamic_levels(self, candles: List[Candle], price: float, lookback: int = 60) -> Tuple[float, float]:
        """
        Returns the nearest support below and nearest resistance above `price`,
        computed from recent real swing levels. Falls back to recent lows/highs
        when no swing level exists in that direction.
        """
        if not candles:
            raise ValueError("At least one closed candle is required")

        recent_candles = candles[-max(5, lookback):]
        res_levels, sup_levels = self.find_support_resistance(recent_candles)
        supports = self._cluster_levels(sup_levels)
        resistances = self._cluster_levels(res_levels)

        support = max((l for l in supports if l <= price), default=None)
        if support is None:
            support = min(supports, default=None)
        if support is None:
            support = min(c.low for c in recent_candles)

        resistance = min((l for l in resistances if l >= price), default=None)
        if resistance is None:
            resistance = max(resistances, default=None)
        if resistance is None:
            resistance = max(c.high for c in recent_candles)

        return support, resistance

    def find_next_resistance(
        self,
        candles: List[Candle],
        resistance: float,
        lookback: int = 60,
    ) -> Optional[float]:
        """Returns the nearest clustered swing resistance strictly above `resistance`."""
        if not candles:
            return None

        recent_candles = candles[-max(5, lookback):]
        resistance_levels, _ = self.find_support_resistance(recent_candles)
        clustered_levels = self._cluster_levels(resistance_levels)
        return min(
            (level for level in clustered_levels if level > resistance),
            default=None,
        )

    def find_next_support(
        self,
        candles: List[Candle],
        support: float,
        lookback: int = 60,
    ) -> Optional[float]:
        """Returns the nearest clustered swing support strictly below `support`."""
        if not candles:
            return None

        recent_candles = candles[-max(5, lookback):]
        _, support_levels = self.find_support_resistance(recent_candles)
        clustered_levels = self._cluster_levels(support_levels)
        return max(
            (level for level in clustered_levels if level < support),
            default=None,
        )

    def calculate_zone_tolerance(
        self,
        candles: List[Candle],
        atr_period: int = 14,
        atr_multiplier: float = 0.75,
        minimum: float = 0.003,
        maximum: float = 0.015,
    ) -> float:
        """Returns an ATR-based percentage width for support/resistance zones."""
        if not candles or candles[-1].close <= 0:
            return minimum

        current_atr = self.atr(candles, atr_period)
        if current_atr is None or current_atr <= 0:
            return minimum

        atr_tolerance = atr_multiplier * current_atr / candles[-1].close
        return min(max(minimum, atr_tolerance), maximum)

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
        volume_ratio = self.volume_ratio(candles)
        return Signal(
            action=action,
            position=position,
            pattern=pattern,
            price=candle.close,
            reason=reason,
            volume_ratio=volume_ratio,
            volume_status=self.classify_volume(volume_ratio),
        )

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
    def bollinger_signal(
        candles: List[Candle], period: int = 20, standard_deviations: float = 2.0
    ) -> Dict[str, Any]:
        """Detects a closed-candle cross through a Bollinger outer band.

        BBandLE is a cross from at-or-below the previous upper band to above
        the current upper band. BBandSE is the inverse cross through the lower
        band. The returned band values are for the latest closed candle.
        """
        if period < 2:
            raise ValueError("Bollinger period must be at least 2")
        if standard_deviations <= 0:
            raise ValueError("Bollinger standard deviations must be positive")

        empty = {
            "le": None,
            "se": None,
            "upper": None,
            "middle": None,
            "lower": None,
        }
        if len(candles) < period + 1:
            return empty

        closes = np.asarray([float(candle.close) for candle in candles], dtype=float)

        def bands_at(end_index: int) -> Tuple[float, float, float]:
            window = closes[end_index - period + 1 : end_index + 1]
            middle = float(np.mean(window))
            deviation = float(np.std(window))
            upper = middle + standard_deviations * deviation
            lower = middle - standard_deviations * deviation
            return upper, middle, lower

        previous_upper, _, previous_lower = bands_at(len(closes) - 2)
        upper, middle, lower = bands_at(len(closes) - 1)
        previous_close = float(closes[-2])
        current_close = float(closes[-1])
        return {
            "le": previous_close <= previous_upper and current_close > upper,
            "se": previous_close >= previous_lower and current_close < lower,
            "upper": upper,
            "middle": middle,
            "lower": lower,
        }

    @staticmethod
    def classify_volume(
        volume_ratio: Optional[float],
        high_threshold: float = 1.2,
        low_threshold: float = 0.8,
    ) -> str:
        """Classifies the latest volume against its recent average."""
        if volume_ratio is None:
            return "UNKNOWN"
        if volume_ratio >= high_threshold:
            return "THICK"
        if volume_ratio < low_threshold:
            return "THIN"
        return "NORMAL"

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
        market_price: Optional[float] = None,
        next_support: Optional[float] = None,
        minimum_next_support_atr: float = 3.0,
        bband_enabled: bool = False,
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
        volume_status = self.classify_volume(volume_ratio, high_threshold=volume_multiplier)
        downtrend = self.is_downtrend(candles)
        bband = self.bollinger_signal(candles) if bband_enabled else {}
        bband_se = bband.get("se") is True
        bband_requirement = ", and BBandSE" if bband_enabled else ""

        state = dict(previous_state or {})
        previous_status = state.get("status", "NEUTRAL")
        tracked_support = float(state.get("support", support))
        active_breakdown_statuses = {
            "BREAKDOWN_WATCH",
            "BREAKDOWN_CONFIRMED",
            "RETEST_REJECTED",
        }
        tracked_next_support = next_support
        if (
            previous_status in active_breakdown_statuses
            and state.get("next_support") is not None
        ):
            tracked_next_support = float(state["next_support"])
        candle_timestamp = int(candle.timestamp)
        observed_price = float(market_price) if market_price is not None else candle.close
        is_new_candle = state.get("last_candle_timestamp") != candle_timestamp
        current_atr = self.atr(candles)
        next_support_distance = (
            candle.close - tracked_next_support
            if tracked_next_support is not None
            else None
        )
        next_support_far_enough = (
            next_support_distance is not None
            and current_atr is not None
            and current_atr > 0
            and next_support_distance >= minimum_next_support_atr * current_atr
        )

        action = "HOLD"
        status = "NEUTRAL"
        reason = "No support strategy signal"

        active_breakdown = previous_status in active_breakdown_statuses
        if active_breakdown:
            if previous_status == "BREAKDOWN_WATCH":
                watch_invalidated = (
                    observed_price >= tracked_support
                    if market_price is not None
                    else is_new_candle and candle.close >= tracked_support
                )
            else:
                watch_invalidated = is_new_candle and candle.close >= tracked_support
            if watch_invalidated:
                status = "BREAKDOWN_INVALIDATED"
                reason = "Price reclaimed support; breakdown signal invalidated"
            elif previous_status == "BREAKDOWN_CONFIRMED":
                retest_touched = is_new_candle and (
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
            elif (
                is_new_candle
                and candle.close < tracked_support
                and pattern == "LONG_RED"
                and volume_high
                and downtrend
                and next_support_far_enough
                and (not bband_enabled or bband_se)
            ):
                status = "BREAKDOWN_CONFIRMED"
                action = "SELL"
                reason = (
                    "Support breakdown confirmed by LONG_RED, high volume, downtrend, "
                    f"sufficient distance to next support{bband_requirement}"
                )
            else:
                status = previous_status
                reason = (
                    "Breakdown watch active; waiting for LONG_RED, high volume, downtrend, "
                    f"sufficient distance to next support{bband_requirement}"
                )
        elif observed_price < support:
            if (
                is_new_candle
                and candle.close < support
                and pattern == "LONG_RED"
                and volume_high
                and downtrend
                and next_support_far_enough
                and (not bband_enabled or bband_se)
            ):
                status = "BREAKDOWN_CONFIRMED"
                action = "SELL"
                reason = (
                    "Support breakdown confirmed by LONG_RED, high volume, downtrend, "
                    f"sufficient distance to next support{bband_requirement}"
                )
            else:
                status = "BREAKDOWN_WATCH"
                reason = (
                    "Price is below support; waiting for LONG_RED, high volume, downtrend, "
                    f"sufficient distance to next support{bband_requirement}"
                )
        elif is_new_candle and position == "SUPPORT":
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
            "next_support_far_enough": next_support_far_enough,
        }
        if tracked_next_support is not None:
            next_state["next_support"] = float(tracked_next_support)
        if current_atr is not None:
            next_state["breakdown_atr"] = round(float(current_atr), 8)
        if status == "BREAKDOWN_CONFIRMED" and action == "SELL":
            next_state["breakdown_timestamp"] = candle_timestamp
        elif active_breakdown and "breakdown_timestamp" in state:
            next_state["breakdown_timestamp"] = state["breakdown_timestamp"]
        if volume_ratio is not None:
            next_state["volume_ratio"] = round(float(volume_ratio), 4)
        next_state["volume_status"] = volume_status
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
            volume_ratio=volume_ratio,
            volume_status=volume_status,
            bband_le=bband.get("le"),
            bband_se=bband.get("se"),
            bband_upper=bband.get("upper"),
            bband_middle=bband.get("middle"),
            bband_lower=bband.get("lower"),
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
        market_price: Optional[float] = None,
        next_resistance: Optional[float] = None,
        minimum_next_resistance_atr: float = 3.0,
        bband_enabled: bool = False,
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
        volume_status = self.classify_volume(volume_ratio, high_threshold=volume_multiplier)
        uptrend = self.is_uptrend(candles)
        bband = self.bollinger_signal(candles) if bband_enabled else {}
        bband_le = bband.get("le") is True
        bband_requirement = ", and BBandLE" if bband_enabled else ""

        state = dict(previous_state or {})
        previous_status = state.get("status", "NEUTRAL")
        tracked_resistance = float(state.get("resistance", resistance))
        active_breakout_statuses = {
            "BREAKOUT_WATCH",
            "BREAKOUT_CONFIRMED",
            "RETEST_HELD",
        }
        tracked_next_resistance = next_resistance
        if (
            previous_status in active_breakout_statuses
            and state.get("next_resistance") is not None
        ):
            tracked_next_resistance = float(state["next_resistance"])
        candle_timestamp = int(candle.timestamp)
        observed_price = float(market_price) if market_price is not None else candle.close
        is_new_candle = state.get("last_candle_timestamp") != candle_timestamp
        current_atr = self.atr(candles)
        next_resistance_distance = (
            tracked_next_resistance - candle.close
            if tracked_next_resistance is not None
            else None
        )
        next_resistance_far_enough = (
            next_resistance_distance is not None
            and current_atr is not None
            and current_atr > 0
            and next_resistance_distance >= minimum_next_resistance_atr * current_atr
        )

        action = "HOLD"
        status = "NEUTRAL"
        reason = "No resistance strategy signal"

        active_breakout = previous_status in active_breakout_statuses
        if active_breakout:
            if previous_status == "BREAKOUT_WATCH":
                watch_invalidated = (
                    observed_price <= tracked_resistance
                    if market_price is not None
                    else is_new_candle and candle.close <= tracked_resistance
                )
            else:
                watch_invalidated = is_new_candle and candle.close <= tracked_resistance
            if watch_invalidated:
                status = "BREAKOUT_INVALIDATED"
                reason = "Price fell back below resistance; breakout signal invalidated"
            elif previous_status == "BREAKOUT_CONFIRMED":
                retest_held = is_new_candle and (
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
            elif (
                is_new_candle
                and candle.close > tracked_resistance
                and pattern == "LONG_GREEN"
                and volume_high
                and uptrend
                and next_resistance_far_enough
                and (not bband_enabled or bband_le)
            ):
                status = "BREAKOUT_CONFIRMED"
                action = "BUY"
                reason = (
                    "Resistance breakout confirmed by LONG_GREEN, high volume, uptrend, "
                    f"sufficient distance to next resistance{bband_requirement}"
                )
            else:
                status = previous_status
                reason = (
                    "Breakout watch active; waiting for LONG_GREEN, high volume, uptrend, "
                    f"sufficient distance to next resistance{bband_requirement}"
                )
        elif observed_price > resistance:
            if (
                is_new_candle
                and candle.close > resistance
                and pattern == "LONG_GREEN"
                and volume_high
                and uptrend
                and next_resistance_far_enough
                and (not bband_enabled or bband_le)
            ):
                status = "BREAKOUT_CONFIRMED"
                action = "BUY"
                reason = (
                    "Resistance breakout confirmed by LONG_GREEN, high volume, uptrend, "
                    f"sufficient distance to next resistance{bband_requirement}"
                )
            else:
                status = "BREAKOUT_WATCH"
                reason = (
                    "Price is above resistance; waiting for LONG_GREEN, high volume, uptrend, "
                    f"sufficient distance to next resistance{bband_requirement}"
                )
        elif is_new_candle and position == "RESISTANCE":
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
            "next_resistance_far_enough": next_resistance_far_enough,
        }
        if tracked_next_resistance is not None:
            next_state["next_resistance"] = float(tracked_next_resistance)
        if current_atr is not None:
            next_state["breakout_atr"] = round(float(current_atr), 8)
        if status == "BREAKOUT_CONFIRMED" and action == "BUY":
            next_state["breakout_timestamp"] = candle_timestamp
        elif active_breakout and "breakout_timestamp" in state:
            next_state["breakout_timestamp"] = state["breakout_timestamp"]
        if volume_ratio is not None:
            next_state["volume_ratio"] = round(float(volume_ratio), 4)
        next_state["volume_status"] = volume_status
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
            volume_ratio=volume_ratio,
            volume_status=volume_status,
            bband_le=bband.get("le"),
            bband_se=bband.get("se"),
            bband_upper=bband.get("upper"),
            bband_middle=bband.get("middle"),
            bband_lower=bband.get("lower"),
        )
        return signal, next_state

    def generate_strategy_signal(
        self,
        candles: List[Candle],
        support: float,
        resistance: float,
        previous_state: Optional[Dict[str, Any]] = None,
        tolerance: float = 0.003,
        market_price: Optional[float] = None,
        next_resistance: Optional[float] = None,
        minimum_next_resistance_atr: float = 3.0,
        next_support: Optional[float] = None,
        minimum_next_support_atr: float = 3.0,
        bband_enabled: bool = False,
    ) -> Tuple[Signal, Dict[str, Any]]:
        """Selects support or resistance strategy based on the active level."""
        if not candles:
            raise ValueError("At least one closed candle is required")

        state = dict(previous_state or {})
        previous_level = state.get("level")
        previous_status = state.get("status", "NEUTRAL")
        observed_price = float(market_price) if market_price is not None else candles[-1].close
        support_statuses = {"BREAKDOWN_WATCH", "BREAKDOWN_CONFIRMED", "RETEST_REJECTED"}
        resistance_statuses = {"BREAKOUT_WATCH", "BREAKOUT_CONFIRMED", "RETEST_HELD"}

        if previous_level == "RESISTANCE" and previous_status in resistance_statuses:
            level = "RESISTANCE"
        elif previous_level == "SUPPORT" and previous_status in support_statuses:
            level = "SUPPORT"
        elif observed_price >= resistance * (1 - tolerance):
            level = "RESISTANCE"
        else:
            level = "SUPPORT"

        if level == "RESISTANCE":
            signal, next_state = self.generate_resistance_strategy_signal(
                candles,
                support,
                resistance,
                previous_state=state,
                tolerance=tolerance,
                market_price=market_price,
                next_resistance=next_resistance,
                minimum_next_resistance_atr=minimum_next_resistance_atr,
                bband_enabled=bband_enabled,
            )
        else:
            signal, next_state = self.generate_support_strategy_signal(
                candles,
                support,
                resistance,
                previous_state=state,
                tolerance=tolerance,
                market_price=market_price,
                next_support=next_support,
                minimum_next_support_atr=minimum_next_support_atr,
                bband_enabled=bband_enabled,
            )
        next_state["level"] = level
        return signal, next_state
