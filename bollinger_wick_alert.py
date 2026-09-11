"""Notify Telegram when a closed XAU/USD 5-minute candle wicks outside Bollinger Bands."""

import argparse
import asyncio
import logging
import math
from datetime import datetime
from statistics import fmean, pstdev
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from exchange_manager import TwelveDataManager
from models import Candle
from telegram_notifier import TelegramNotifier
from trading_state import TradingStateStore

logger = logging.getLogger(__name__)

load_dotenv()

BANGKOK_TIMEZONE = ZoneInfo("Asia/Bangkok")
MARKET_SYMBOL = "XAU/USD"
TIMEFRAME = "5m"
CANDLE_INTERVAL_MS = 5 * 60 * 1000
BOLLINGER_PERIOD = 20
BOLLINGER_DEVIATION = 2.0
CANDLE_LIMIT = BOLLINGER_PERIOD + 1
STATE_KEY = "XAUUSD|bollinger_wick_alert|5m"
BUY_MESSAGE = "BUY: แท่ง 5 นาทีที่ปิดแล้วมี low ทะลุ Lower Band แต่ราคาปิดกลับเข้ากรอบ"
SELL_MESSAGE = "SELL: แท่ง 5 นาทีที่ปิดแล้วมี high ทะลุ Upper Band แต่ราคาปิดกลับเข้ากรอบ"


def _timestamp_ms(now: datetime) -> int:
    if now.tzinfo is None:
        now = now.replace(tzinfo=BANGKOK_TIMEZONE)
    return int(now.timestamp() * 1000)


def latest_closed_candles(candles: Iterable[Candle], now: datetime) -> list[Candle]:
    """Return all candles whose full five-minute interval ended by ``now``."""
    now_ms = _timestamp_ms(now)
    return sorted(
        (candle for candle in candles if candle.timestamp + CANDLE_INTERVAL_MS <= now_ms),
        key=lambda candle: candle.timestamp,
    )


def bollinger_bands(candles: Iterable[Candle]) -> tuple[float, float]:
    """Calculate upper/lower population-standard-deviation bands for 20 closes."""
    window = list(candles)[-BOLLINGER_PERIOD:]
    if len(window) < BOLLINGER_PERIOD:
        raise ValueError("At least 20 completed candles are required")

    closes = [float(candle.close) for candle in window]
    if not all(math.isfinite(close) for close in closes):
        raise ValueError("Closed candle has an invalid close")
    middle = fmean(closes)
    deviation = pstdev(closes)
    return (
        middle + BOLLINGER_DEVIATION * deviation,
        middle - BOLLINGER_DEVIATION * deviation,
    )


def wick_alerts(candles: Iterable[Candle]) -> list[str]:
    """Return every direction satisfied by the most recent closed candle."""
    closed = list(candles)
    upper, lower = bollinger_bands(closed)
    candle = closed[-1]
    open_price = float(candle.open)
    high, low, close = float(candle.high), float(candle.low), float(candle.close)
    if not all(math.isfinite(value) for value in (open_price, high, low, close)):
        raise ValueError("Closed candle has invalid OHLC data")
    if low > high or not low <= open_price <= high or not low <= close <= high:
        raise ValueError("Closed candle has inconsistent OHLC data")

    if not lower <= close <= upper:
        return []

    alerts = []
    if low < lower:
        alerts.append("BUY")
    if high > upper:
        alerts.append("SELL")
    return alerts


def _previous_alerts(state: dict, candle_timestamp: int) -> set[str]:
    if state.get("candle_timestamp") != candle_timestamp:
        return set()
    alerts = state.get("alerts", [])
    return {item for item in alerts if item in {"BUY", "SELL"}}


async def run_bollinger_wick_alert(
    now: Optional[datetime] = None,
    market_data: Optional[TwelveDataManager] = None,
    notifier: Optional[TelegramNotifier] = None,
    state_store: Optional[TradingStateStore] = None,
) -> bool:
    """Evaluate a completed XAU/USD 5m candle and notify each matching direction."""
    owns_market_data = market_data is None
    try:
        now = now or datetime.now(BANGKOK_TIMEZONE)
        market_data = market_data or TwelveDataManager(output_timezone="Asia/Bangkok")
        notifier = notifier if notifier is not None else TelegramNotifier.from_env()
        state_store = state_store or TradingStateStore()

        candles = await market_data.fetch_ohlcv(MARKET_SYMBOL, TIMEFRAME, limit=CANDLE_LIMIT)
        closed = latest_closed_candles(candles, now)
        if len(closed) < BOLLINGER_PERIOD:
            logger.warning("Bollinger wick alert skipped: fewer than 20 completed 5m candles")
            return False

        candle = closed[-1]
        sent_alerts = _previous_alerts(state_store.get(STATE_KEY), candle.timestamp)
        for direction in wick_alerts(closed):
            if direction in sent_alerts:
                logger.info("Duplicate Bollinger wick alert skipped: %s candle=%s", direction, candle.timestamp)
                continue
            if notifier is None:
                logger.warning("Telegram is disabled; Bollinger wick alert was not recorded as sent")
                continue
            message = BUY_MESSAGE if direction == "BUY" else SELL_MESSAGE
            if await notifier.send_message(message):
                sent_alerts.add(direction)
            else:
                logger.warning("Telegram rejected Bollinger wick alert; it remains eligible for retry: %s", direction)

        state_store.save(
            STATE_KEY,
            {"candle_timestamp": candle.timestamp, "alerts": sorted(sent_alerts)},
        )
        return True
    except Exception:
        logger.exception("XAU/USD Bollinger wick alert check failed")
        return False
    finally:
        if owns_market_data and market_data is not None:
            await market_data.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="XAU/USD Bollinger wick alert")
    return parser.parse_args()


if __name__ == "__main__":
    parse_args()
    raise SystemExit(0 if asyncio.run(run_bollinger_wick_alert()) else 1)
