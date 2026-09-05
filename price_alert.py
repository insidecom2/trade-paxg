"""Notify Telegram when a closed XAU/USD 4-hour candle crosses MySQL levels."""

import argparse
import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

from exchange_manager import TwelveDataManager
from models import Candle
from telegram_notifier import TelegramNotifier
from trading_state import TradingStateStore

logger = logging.getLogger(__name__)

load_dotenv()

BANGKOK_TIMEZONE = ZoneInfo("Asia/Bangkok")
ALERT_SYMBOL = "XAUUSD"
MARKET_SYMBOL = "XAU/USD"
TIMEFRAME = "4h"
TIMEFRAME_MS = 4 * 60 * 60 * 1000
STATE_KEY = "XAUUSD|mysql_price_alert"


@dataclass(frozen=True)
class PriceAlertLevel:
    support: Optional[float]
    resistance: Optional[float]


def bangkok_day_bounds(now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Return naive Bangkok datetimes for an index-friendly MySQL range query."""
    supplied_now = now or datetime.now(BANGKOK_TIMEZONE)
    local_now = (
        supplied_now.replace(tzinfo=BANGKOK_TIMEZONE)
        if supplied_now.tzinfo is None
        else supplied_now.astimezone(BANGKOK_TIMEZONE)
    )
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.replace(tzinfo=None), (start + timedelta(days=1)).replace(tzinfo=None)


def _optional_level(value: object) -> Optional[float]:
    try:
        level = float(value)
    except (TypeError, ValueError):
        return None
    return level if math.isfinite(level) and level > 0 else None


class MySQLPriceAlertRepository:
    """Read today's configured support/resistance rows without mutating MySQL."""

    def __init__(self, host: str, port: int, user: str, password: str, database: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database

    @classmethod
    def from_env(cls) -> "MySQLPriceAlertRepository":
        required = {
            "MYSQL_HOST": os.getenv("MYSQL_HOST", "").strip(),
            "MYSQL_USER": os.getenv("MYSQL_USER", "").strip(),
            "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD", ""),
            "MYSQL_DATABASE": os.getenv("MYSQL_DATABASE", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing MySQL configuration: {', '.join(missing)}")
        try:
            port = int(os.getenv("MYSQL_PORT", "3306"))
        except ValueError as exc:
            raise ValueError("MYSQL_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MYSQL_PORT must be between 1 and 65535")
        return cls(
            host=required["MYSQL_HOST"],
            port=port,
            user=required["MYSQL_USER"],
            password=required["MYSQL_PASSWORD"],
            database=required["MYSQL_DATABASE"],
        )

    def _fetch_sync(self, start: datetime, end: datetime) -> list[PriceAlertLevel]:
        query = """
            SELECT `support`, `resistance`
            FROM `price_alert`
            WHERE `date` >= %s AND `date` < %s AND `symbol` = %s
            ORDER BY `date` ASC
        """
        connection = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
            autocommit=True,
            cursorclass=DictCursor,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, (start, end, ALERT_SYMBOL))
                rows = cursor.fetchall()
        finally:
            connection.close()

        levels = []
        for row in rows:
            support = _optional_level(row.get("support"))
            resistance = _optional_level(row.get("resistance"))
            if support is None and resistance is None:
                logger.warning("Skipping price_alert row with no valid levels")
                continue
            levels.append(PriceAlertLevel(support=support, resistance=resistance))
        return levels

    async def fetch_todays_levels(self, now: Optional[datetime] = None) -> list[PriceAlertLevel]:
        start, end = bangkok_day_bounds(now)
        return await asyncio.to_thread(self._fetch_sync, start, end)


def latest_closed_candle(candles: Iterable[Candle]) -> Candle:
    """Use the penultimate candle because providers include the live candle last."""
    ordered = list(candles)
    if len(ordered) < 2:
        raise ValueError("At least two 4-hour candles are required")
    if ordered[-1].timestamp - ordered[-2].timestamp != TIMEFRAME_MS:
        raise ValueError("Twelve Data did not return consecutive 4-hour candles")
    return ordered[-2]


def _alert_key(direction: str, level: float) -> str:
    return f"{direction}:{level:.8f}"


def _previous_alerts(state: dict, candle_timestamp: int) -> set[str]:
    if state.get("candle_timestamp") != candle_timestamp:
        return set()
    alerts = state.get("alerts", [])
    return {item for item in alerts if isinstance(item, str)}


def _crossed_alerts(
    levels: Iterable[PriceAlertLevel], candle: Candle
) -> list[tuple[str, float, str]]:
    close = _optional_level(candle.close)
    high = _optional_level(candle.high)
    low = _optional_level(candle.low)
    if close is None or high is None or low is None:
        raise ValueError("Latest closed XAU/USD candle has invalid OHLC data")

    alerts: dict[str, tuple[str, float, str]] = {}
    for item in levels:
        if item.resistance is not None and close > item.resistance:
            key = _alert_key("resistance", item.resistance)
            alerts[key] = (
                key,
                item.resistance,
                "ราคา Gold ปิดแท่ง 4H ทะลุสูงกว่าแนวต้าน "
                f"{item.resistance:.2f}\nราคาปิด XAU/USD {close:.2f}",
            )
        elif item.resistance is not None and high >= item.resistance and close <= item.resistance:
            key = _alert_key("resistance_touch", item.resistance)
            alerts[key] = (
                key,
                item.resistance,
                "ราคา Gold มีการแตะแนวต้าน "
                f"{item.resistance:.2f} แต่ปิดไม่สูงกว่าแนวต้าน\n"
                f"ราคาสูงสุด XAU/USD {high:.2f}\nราคาปิด XAU/USD {close:.2f}",
            )
        if item.support is not None and close < item.support:
            key = _alert_key("support", item.support)
            alerts[key] = (
                key,
                item.support,
                "ราคา Gold ปิดแท่ง 4H ทะลุต่ำกว่าแนวรับ "
                f"{item.support:.2f}\nราคาปิด XAU/USD {close:.2f}",
            )
        elif item.support is not None and low <= item.support and close >= item.support:
            key = _alert_key("support_touch", item.support)
            alerts[key] = (
                key,
                item.support,
                "ราคา Gold มีการแตะแนวรับ "
                f"{item.support:.2f} แต่ปิดไม่ต่ำกว่าแนวรับ\n"
                f"ราคาต่ำสุด XAU/USD {low:.2f}\nราคาปิด XAU/USD {close:.2f}",
            )
    return list(alerts.values())


async def run_price_alert(
    now: Optional[datetime] = None,
    repository: Optional[MySQLPriceAlertRepository] = None,
    market_data: Optional[TwelveDataManager] = None,
    notifier: Optional[TelegramNotifier] = None,
    state_store: Optional[TradingStateStore] = None,
) -> bool:
    """Check every configured level; preserve only successfully sent alert keys."""
    owns_market_data = market_data is None
    try:
        repository = repository or MySQLPriceAlertRepository.from_env()
        market_data = market_data or TwelveDataManager()
        notifier = notifier if notifier is not None else TelegramNotifier.from_env()
        state_store = state_store or TradingStateStore()

        levels = await repository.fetch_todays_levels(now)
        if not levels:
            logger.info("No eligible XAUUSD price_alert levels for today; no action")
            return True

        candles = await market_data.fetch_ohlcv(MARKET_SYMBOL, TIMEFRAME, limit=2)
        candle = latest_closed_candle(candles)
        sent_alerts = _previous_alerts(state_store.get(STATE_KEY), candle.timestamp)
        for key, _level, message in _crossed_alerts(levels, candle):
            if key in sent_alerts:
                logger.info("Duplicate price alert skipped: %s candle=%s", key, candle.timestamp)
                continue
            if notifier is None:
                logger.warning("Telegram is disabled; price alert was not recorded as sent")
                continue
            if await notifier.send_message(message):
                sent_alerts.add(key)
            else:
                logger.warning("Telegram rejected price alert; it remains eligible for retry: %s", key)

        state_store.save(
            STATE_KEY,
            {"candle_timestamp": candle.timestamp, "alerts": sorted(sent_alerts)},
        )
        return True
    except Exception:
        logger.exception("MySQL XAU/USD price-alert check failed")
        return False
    finally:
        if owns_market_data and market_data is not None:
            await market_data.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MySQL XAU/USD 4-hour price alert")
    return parser.parse_args()


if __name__ == "__main__":
    parse_args()
    raise SystemExit(0 if asyncio.run(run_price_alert()) else 1)
