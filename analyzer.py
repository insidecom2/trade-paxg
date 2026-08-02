import numpy as np
from typing import List, Tuple, Optional
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
