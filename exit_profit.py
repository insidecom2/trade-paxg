from dataclasses import dataclass, replace
from math import isfinite
from typing import Any, Dict, List, Optional, Tuple

from analyzer import MarketAnalyzer
from models import Candle


@dataclass(frozen=True)
class ExitProfitLevel:
    """A closing-price zone that was revisited by the market."""

    price: float
    occurrences: int
    latest_timestamp: int
    zone_tolerance: float = 0.0


@dataclass(frozen=True)
class ExitProfitCheck:
    """Comparison result for the latest closed candle and its revisited zone."""

    close_1: Optional[float] = None
    close_2: Optional[float] = None
    high_1: Optional[float] = None
    high_2: Optional[float] = None
    volume_1: Optional[float] = None
    volume_2: Optional[float] = None
    touch_count: int = 0
    resistance_zone_low: Optional[float] = None
    resistance_zone_high: Optional[float] = None
    volume_change_percent: Optional[float] = None
    rsi_previous: Optional[float] = None
    rsi_current: Optional[float] = None
    momentum: str = "N/A"
    close_1_timestamp: Optional[int] = None
    close_2_timestamp: Optional[int] = None
    zone_tolerance: float = 0.0
    same_zone: bool = False
    exit_price: Optional[float] = None
    occurrences: int = 0
    lookback: int = 16
    zone_price: Optional[float] = None
    latest_timestamp: Optional[int] = None


def calculate_exit_profit_zone_tolerance(
    candles: List[Candle],
    atr_period: int = 14,
    atr_multiplier: float = 0.75,
    minimum: float = 0.001,
    maximum: float = 0.008,
) -> float:
    """Return a narrow ATR-based percentage width for a close-price zone."""
    if not candles or candles[-1].close <= 0:
        return minimum

    current_atr = MarketAnalyzer.atr(candles, atr_period)
    if current_atr is None or current_atr <= 0:
        return minimum

    atr_tolerance = atr_multiplier * current_atr / candles[-1].close
    return min(max(minimum, atr_tolerance), maximum)


def _build_price_zones(
    candles: List[Candle], zone_tolerance: float
) -> List[List[Tuple[float, int]]]:
    prices = sorted(
        (
            (float(candle.close), int(candle.timestamp))
            for candle in candles
            if isfinite(candle.close)
        ),
        key=lambda item: item[0],
    )
    zones: List[List[Tuple[float, int]]] = []
    for price, timestamp in prices:
        if not zones:
            zones.append([(price, timestamp)])
            continue

        zone_anchor = zones[-1][0][0]
        if abs(price - zone_anchor) <= zone_tolerance * zone_anchor:
            zones[-1].append((price, timestamp))
        else:
            zones.append([(price, timestamp)])
    return zones


def calculate_rsi_pair(
    candles: List[Candle], period: int = 14
) -> Tuple[Optional[float], Optional[float]]:
    """Calculate the previous and current Wilder RSI values."""
    if period < 1:
        raise ValueError("period must be at least 1")
    if len(candles) < period + 2:
        return None, None

    closes = [float(candle.close) for candle in candles]
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    rsi_values = []
    for index in range(period, len(changes)):
        if index > period:
            average_gain = ((average_gain * (period - 1)) + gains[index]) / period
            average_loss = ((average_loss * (period - 1)) + losses[index]) / period

        if average_loss == 0:
            rsi_values.append(100.0)
        elif average_gain == 0:
            rsi_values.append(0.0)
        else:
            relative_strength = average_gain / average_loss
            rsi_values.append(100 - (100 / (1 + relative_strength)))

    return rsi_values[-2], rsi_values[-1]


def classify_momentum(
    rsi_previous: Optional[float], rsi_current: Optional[float]
) -> str:
    if rsi_previous is None or rsi_current is None:
        return "N/A"
    if rsi_current < rsi_previous:
        return "WEAK"
    if rsi_current > rsi_previous:
        return "STRONG"
    return "NEUTRAL"


def check_exit_profit_zone(
    candles: List[Candle],
    zone_tolerance: Optional[float] = None,
    rsi_period: int = 14,
) -> ExitProfitCheck:
    """Check whether the two latest closed prices are in the same ATR zone."""
    if zone_tolerance is None:
        zone_tolerance = calculate_exit_profit_zone_tolerance(candles)
    if zone_tolerance < 0:
        raise ValueError("zone_tolerance must not be negative")
    rsi_previous, rsi_current = calculate_rsi_pair(candles, period=rsi_period)
    momentum = classify_momentum(rsi_previous, rsi_current)
    if len(candles) < 2:
        return ExitProfitCheck(
            zone_tolerance=zone_tolerance,
            rsi_previous=rsi_previous,
            rsi_current=rsi_current,
            momentum=momentum,
        )

    close_1_candle, close_2_candle = candles[-2:]
    close_1 = float(close_1_candle.close)
    close_2 = float(close_2_candle.close)
    if not isfinite(close_1) or not isfinite(close_2):
        return ExitProfitCheck(
            close_1=close_1,
            close_2=close_2,
            close_1_timestamp=int(close_1_candle.timestamp),
            close_2_timestamp=int(close_2_candle.timestamp),
            zone_tolerance=zone_tolerance,
        )

    zone_anchor = min(close_1, close_2)
    same_zone = (
        zone_anchor > 0
        and abs(close_1 - close_2) <= zone_tolerance * zone_anchor
    )
    high_1 = float(close_1_candle.high)
    high_2 = float(close_2_candle.high)
    high_anchor = min(high_1, high_2)
    highs_same_zone = (
        high_anchor > 0
        and abs(high_1 - high_2) <= zone_tolerance * high_anchor
    )
    volume_1 = float(close_1_candle.volume)
    volume_2 = float(close_2_candle.volume)
    volume_change_percent = None
    if volume_1 != 0:
        volume_change_percent = ((volume_2 - volume_1) / volume_1) * 100
    return ExitProfitCheck(
        close_1=close_1,
        close_2=close_2,
        high_1=high_1,
        high_2=high_2,
        volume_1=volume_1,
        volume_2=volume_2,
        touch_count=2 if highs_same_zone else 1,
        resistance_zone_low=min(high_1, high_2) * (1 - zone_tolerance),
        resistance_zone_high=max(high_1, high_2) * (1 + zone_tolerance),
        volume_change_percent=volume_change_percent,
        rsi_previous=rsi_previous,
        rsi_current=rsi_current,
        momentum=momentum,
        close_1_timestamp=int(close_1_candle.timestamp),
        close_2_timestamp=int(close_2_candle.timestamp),
        zone_tolerance=zone_tolerance,
        same_zone=same_zone,
        exit_price=(close_1 + close_2) / 2 if same_zone else None,
    )


def check_exit_profit_at_level(
    candles: List[Candle],
    level: float,
    zone_tolerance: Optional[float] = None,
    rsi_period: int = 14,
    lookback: int = 4,
    min_occurrences: int = 2,
) -> ExitProfitCheck:
    """Check how many of the latest `lookback` closes revisit `level`."""
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if min_occurrences < 1:
        raise ValueError("min_occurrences must be at least 1")
    if not isfinite(level) or level <= 0:
        raise ValueError("level must be a positive finite number")
    if zone_tolerance is None:
        zone_tolerance = calculate_exit_profit_zone_tolerance(candles)
    if zone_tolerance < 0:
        raise ValueError("zone_tolerance must not be negative")

    rsi_previous, rsi_current = calculate_rsi_pair(candles, period=rsi_period)
    momentum = classify_momentum(rsi_previous, rsi_current)
    if len(candles) < 2:
        return ExitProfitCheck(
            zone_tolerance=zone_tolerance,
            rsi_previous=rsi_previous,
            rsi_current=rsi_current,
            momentum=momentum,
            lookback=lookback,
            zone_price=float(level),
        )

    window = candles[-lookback:]
    latest_close = float(candles[-1].close)
    latest_in_band = (
        isfinite(candles[-1].close)
        and abs(latest_close - float(level)) <= zone_tolerance * float(level)
    )
    occurrences = sum(
        1
        for candle in window
        if isfinite(candle.close)
        and abs(float(candle.close) - float(level)) <= zone_tolerance * float(level)
    )
    same_zone = latest_in_band and occurrences >= min_occurrences

    close_1_candle, close_2_candle = candles[-2:]
    close_1 = float(close_1_candle.close)
    close_2 = float(close_2_candle.close)
    volume_1 = float(close_1_candle.volume)
    volume_2 = float(close_2_candle.volume)
    volume_change_percent = None
    if volume_1 != 0:
        volume_change_percent = ((volume_2 - volume_1) / volume_1) * 100
    return ExitProfitCheck(
        close_1=close_1,
        close_2=close_2,
        high_1=float(close_1_candle.high),
        high_2=float(close_2_candle.high),
        volume_1=volume_1,
        volume_2=volume_2,
        touch_count=occurrences,
        resistance_zone_low=float(level) * (1 - zone_tolerance),
        resistance_zone_high=float(level) * (1 + zone_tolerance),
        volume_change_percent=volume_change_percent,
        rsi_previous=rsi_previous,
        rsi_current=rsi_current,
        momentum=momentum,
        close_1_timestamp=int(close_1_candle.timestamp),
        close_2_timestamp=int(close_2_candle.timestamp),
        zone_tolerance=zone_tolerance,
        same_zone=same_zone,
        exit_price=float(level) if same_zone else None,
        occurrences=occurrences,
        lookback=lookback,
        zone_price=float(level),
        latest_timestamp=int(close_2_candle.timestamp),
    )


def resolve_exit_profit_alert(
    candles: List[Candle],
    anchor: Optional[Dict[str, Any]] = None,
    zone_tolerance: Optional[float] = None,
    lookback: int = 4,
    min_occurrences: int = 2,
) -> Tuple[Optional[ExitProfitCheck], Dict[str, Any]]:
    """Decide whether an exit-profit alert fires from the active TP anchor.

    Returns the check to notify on, plus the next persisted anchor state.
    The anchor is cleared once stop-loss is hit or take-profit is reached,
    so an alert is never repeated against a closed trade.
    """
    state = dict(anchor or {})
    if not candles:
        return None, state
    take_profit = state.get("take_profit")
    if take_profit is None:
        return None, state

    action = state.get("action", "")
    stop_loss = state.get("stop_loss")
    is_long = action in {"BUY", "STRONG_BUY"}
    is_short = action in {"SELL", "STRONG_SELL"}
    if not is_long and not is_short:
        return None, state

    latest_close = float(candles[-1].close)
    if stop_loss is not None and (
        latest_close <= stop_loss if is_long else latest_close >= stop_loss
    ):
        return None, {}

    if zone_tolerance is None:
        zone_tolerance = calculate_exit_profit_zone_tolerance(candles)
    check = check_exit_profit_at_level(
        candles,
        float(take_profit),
        zone_tolerance=zone_tolerance,
        lookback=lookback,
        min_occurrences=min_occurrences,
    )
    tp_reached = (latest_close > take_profit) if is_long else (latest_close < take_profit)
    latest_timestamp = int(candles[-1].timestamp)
    previous_timestamp = state.get("last_alerted_timestamp")
    should_alert = (check.same_zone or tp_reached) and latest_timestamp != previous_timestamp
    if not should_alert:
        return None, state
    if tp_reached:
        return replace(check, same_zone=True, exit_price=float(take_profit)), {}
    return check, {**state, "last_alerted_timestamp": latest_timestamp}


def find_exit_profit_levels(
    candles: List[Candle],
    min_occurrences: int = 2,
    zone_tolerance: Optional[float] = None,
) -> List[ExitProfitLevel]:
    """Find price zones with at least two closes inside the ATR zone."""
    if min_occurrences < 1:
        raise ValueError("min_occurrences must be at least 1")
    if zone_tolerance is None:
        zone_tolerance = calculate_exit_profit_zone_tolerance(candles)
    if zone_tolerance < 0:
        raise ValueError("zone_tolerance must not be negative")

    levels = [
        ExitProfitLevel(
            price=sum(price for price, _ in zone) / len(zone),
            occurrences=len(zone),
            latest_timestamp=max(timestamp for _, timestamp in zone),
            zone_tolerance=zone_tolerance,
        )
        for zone in _build_price_zones(candles, zone_tolerance)
        if len(zone) >= min_occurrences
    ]
    return sorted(levels, key=lambda level: level.latest_timestamp, reverse=True)


def find_exit_profit_level(
    candles: List[Candle],
    min_occurrences: int = 2,
    zone_tolerance: Optional[float] = None,
) -> Optional[ExitProfitLevel]:
    """Return the most recently revisited exit-profit zone, if any."""
    levels = find_exit_profit_levels(
        candles,
        min_occurrences=min_occurrences,
        zone_tolerance=zone_tolerance,
    )
    return levels[0] if levels else None


async def run_standalone(symbol: str = "PAXG/USDT", lookback: int = 4) -> None:
    """Run the 1h exit-profit check against the take-profit anchor in state."""
    import logging

    from dotenv import load_dotenv

    from exchange_manager import BinanceManager
    from exit_profit_notification import build_exit_profit_notification
    from trading_state import TradingStateStore

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    exchange = BinanceManager()
    try:
        candles = await exchange.fetch_ohlcv(symbol, "1h", limit=250)
        closed_candles = candles[:-1] if len(candles) > 1 else candles
        store = TradingStateStore()
        key = f"{symbol}|exit_profit"
        anchor = store.get(key)
        check, next_anchor = resolve_exit_profit_alert(
            closed_candles, anchor, lookback=lookback
        )
        store.save(key, next_anchor)
        if check is not None:
            print(build_exit_profit_notification(symbol, check))
        else:
            print("Exit Profit Alert: NONE")
    finally:
        await exchange.close()


if __name__ == "__main__":
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Standalone 1h Exit Profit Check")
    parser.add_argument("--symbol", default="PAXG/USDT")
    parser.add_argument("--lookback", type=int, default=4)
    args = parser.parse_args()
    asyncio.run(run_standalone(args.symbol, lookback=args.lookback))
