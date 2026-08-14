import asyncio
import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from exchange_manager import BinanceManager
from analyzer import MarketAnalyzer
from telegram_notifier import TelegramNotifier
from trading_state import TradingStateStore
from models import Signal

# Production Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("TradingBot")

# Load secrets from .env before reading config
load_dotenv()

FETCH_CANDLE_LIMIT = 400
ANALYSIS_CANDLE_LIMIT = 250
ANALYSIS_REPORT_ITEM_LIMIT = 3
ANALYSIS_REPORT_LOOKBACK = 60
TIMEFRAME_DURATIONS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def validate_candle_timeframe(candles, timeframe: str) -> int:
    """Verify that fetched candles use the requested strategy timeframe.

    Missing candles (for example around a market/session gap) are tolerated,
    but the most common positive interval must match the requested timeframe.
    Returning the observed interval also makes the check easy to log and test.
    """
    if timeframe not in TIMEFRAME_DURATIONS:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if len(candles) < 2:
        raise ValueError(
            f"Cannot verify {timeframe} candle interval with fewer than two candles"
        )

    intervals = [
        current.timestamp - previous.timestamp
        for previous, current in zip(candles, candles[1:])
        if current.timestamp > previous.timestamp
    ]
    if not intervals:
        raise ValueError(f"Cannot verify {timeframe} candle interval: invalid timestamps")

    observed_interval = Counter(intervals).most_common(1)[0][0]
    expected_interval = int(TIMEFRAME_DURATIONS[timeframe].total_seconds() * 1000)
    if observed_interval != expected_interval:
        observed_minutes = observed_interval / 60_000
        expected_minutes = expected_interval / 60_000
        raise ValueError(
            "Candle timeframe mismatch: "
            f"requested={timeframe} ({expected_minutes:g}m), "
            f"received={observed_minutes:g}m"
        )
    return observed_interval


def parse_args():
    parser = argparse.ArgumentParser(description="PAXG Trading Signal Bot")
    parser.add_argument("--symbol", default="PAXG/USDT", help="Trading pair, e.g. PAXG/USDT")
    parser.add_argument(
        "--tf",
        "--timeframe",
        dest="timeframe",
        default="4h",
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Strategy candle timeframe (default: 4h)",
    )
    return parser.parse_args()

def format_volume_summary(signal: Signal) -> str:
    volume_labels = {
        "THICK": "หนาแน่น",
        "NORMAL": "ปกติ",
        "THIN": "บาง",
        "UNKNOWN": "ไม่มีข้อมูล",
    }
    volume_status = volume_labels.get(signal.volume_status or "UNKNOWN", "ไม่มีข้อมูล")
    volume_summary = f"Volume: {volume_status}"
    if signal.volume_ratio is not None:
        volume_summary += f" ({signal.volume_ratio:.2f}x เทียบค่าเฉลี่ย 20 แท่ง)"
    return volume_summary


def should_send_signal_notification(signal: Signal) -> bool:
    """Send every generated trading signal, including HOLD."""
    return True


def prepare_analysis_candles(
    candles,
    timeframe: str = "4h",
    limit: int = ANALYSIS_CANDLE_LIMIT,
):
    """Return the latest closed candles inside the approximate XAUUSD session."""
    closed_candles = candles[:-1] if len(candles) > 1 else candles
    candle_duration = TIMEFRAME_DURATIONS[timeframe]
    session_candles = []
    for candle in closed_candles:
        candle_start = datetime.fromtimestamp(candle.timestamp / 1000, tz=timezone.utc)
        sunday = candle_start - timedelta(days=(candle_start.weekday() + 1) % 7)
        session_open = sunday.replace(hour=22, minute=0, second=0, microsecond=0)
        session_close = session_open + timedelta(days=5)
        candle_end = candle_start + candle_duration
        if candle_start >= session_open and candle_end <= session_close:
            session_candles.append(candle)

    return session_candles[-limit:]


def build_signal_summary(
    symbol: str,
    timeframe: str,
    signal: Signal,
    support: float,
    resistance: float,
    current_price: float,
) -> str:
    summary = [
        "=== TRADING SIGNAL ===",
        f"Timeframe: {timeframe} | Symbol: {symbol}",
        f"Action: {signal.action}",
        f"Status: {signal.status}",
        f"Position: {signal.position} | Pattern: {signal.pattern or 'NONE'}",
        f"Dynamic Levels: Support=${support:.2f} | Resistance=${resistance:.2f}",
        format_volume_summary(signal),
        f"Last Close: ${signal.price:.2f} | Live Price: ${current_price:.2f}",
        f"Reason: {signal.reason}",
    ]
    if signal.entry_price is not None:
        summary.extend([
            f"Entry: ${signal.entry_price:.2f}",
            f"Stop Loss: ${signal.stop_loss:.2f}",
            f"Take Profit: ${signal.take_profit:.2f}",
        ])
    return "\n".join(summary)


def resolve_dynamic_levels(
    analyzer: MarketAnalyzer,
    candles,
    price: float,
    previous_state,
    lookback: int = 60,
):
    """Keeps levels based on closed-candle data until a new candle is available."""
    if not candles:
        raise ValueError("At least one closed candle is required")

    previous_support = previous_state.get("support")
    previous_resistance = previous_state.get("resistance")
    active_level_statuses = {
        "BREAKDOWN_WATCH",
        "BREAKDOWN_CONFIRMED",
        "RETEST_REJECTED",
        "BREAKOUT_WATCH",
        "BREAKOUT_CONFIRMED",
        "RETEST_HELD",
    }
    if (
        previous_state.get("status") in active_level_statuses
        and previous_support is not None
        and previous_resistance is not None
    ):
        # Keep the level that was actually broken. Recomputing here could move
        # the reference past the breakout and lose the pending confirmation.
        return float(previous_support), float(previous_resistance)

    if previous_support is not None and previous_resistance is not None:
        # Check the live price against the previous levels before allowing a
        # fresh calculation to replace them. This catches the first breakout
        # even when the closed candle has already formed a new level.
        observed_price = float(price)
        if observed_price < float(previous_support) or observed_price > float(previous_resistance):
            return float(previous_support), float(previous_resistance)

    candle_timestamp = int(candles[-1].timestamp)
    try:
        if (
            int(previous_state.get("levels_timestamp")) == candle_timestamp
            and previous_state.get("support") is not None
            and previous_state.get("resistance") is not None
        ):
            return float(previous_state["support"]), float(previous_state["resistance"])
    except (TypeError, ValueError):
        pass

    # Live price is only for breakout monitoring. It must not change which
    # previous support/resistance levels are selected.
    reference_price = float(candles[-1].close)
    return analyzer.find_dynamic_levels(candles, reference_price, lookback=lookback)

async def main(symbol: str = "PAXG/USDT", timeframe: str = "4h") -> bool:
    logger.info(f"🚀 Starting Analysis for {symbol}...")

    notifier = TelegramNotifier.from_env()
    exchange = BinanceManager()
    try:
        # 1. Data Acquisition
        current_price = await exchange.fetch_current_price(symbol)
        candles = await exchange.fetch_ohlcv(symbol, timeframe, limit=FETCH_CANDLE_LIMIT)
        observed_interval = validate_candle_timeframe(candles, timeframe)
        logger.info(f"Successfully fetched {len(candles)} candles.")
        logger.info(
            "Timeframe check passed: requested=%s, candle interval=%dm; "
            "all strategy calculations use this timeframe",
            timeframe,
            observed_interval // 60_000,
        )

        # Approximate XAUUSD history while retaining 250 candles for analysis.
        closed_candles = prepare_analysis_candles(candles, timeframe=timeframe)
        logger.info(
            "Using %d closed XAUUSD-session candles for analysis (weekend/session boundaries excluded).",
            len(closed_candles),
        )

        # 2. Analysis
        analyzer = MarketAnalyzer()
        state_store = TradingStateStore()
        state_key = f"{symbol}|{timeframe}"
        previous_state = state_store.get(state_key)

        # Find recent zones and levels relative to the latest closed price.
        analysis_reference_price = closed_candles[-1].close
        latest_zones = analyzer.find_recent_snd_zones(
            closed_candles,
            analysis_reference_price,
            lookback=ANALYSIS_REPORT_LOOKBACK,
            limit=ANALYSIS_REPORT_ITEM_LIMIT,
        )
        latest_resistance_levels, latest_support_levels = analyzer.find_key_levels(
            closed_candles,
            analysis_reference_price,
            lookback=ANALYSIS_REPORT_LOOKBACK,
            limit=ANALYSIS_REPORT_ITEM_LIMIT,
        )

        # 3. Production Logging of Results
        logger.info("--- ANALYSIS REPORT ---")

        # Log SND
        if latest_zones:
            for zone in latest_zones:
                logger.info(f"Zone Found: {zone.type} | Range: [{zone.bottom:.2f} - {zone.top:.2f}]")
        else:
            logger.info("Zone Found: NONE")

        # Log SE
        if latest_resistance_levels:
            logger.info(f"Key Resistance Levels: {latest_resistance_levels}")
        else:
            logger.info("Key Resistance Levels: NONE")
        if latest_support_levels:
            logger.info(f"Key Support Levels: {latest_support_levels}")
        else:
            logger.info("Key Support Levels: NONE")

        logger.info("-----------------------")

        # 4. Trading Signal (levels from closed data; live price only monitors breaks)
        support, resistance = resolve_dynamic_levels(
            analyzer,
            closed_candles,
            current_price,
            previous_state,
        )
        zone_tolerance = analyzer.calculate_zone_tolerance(closed_candles)
        signal, next_state = analyzer.generate_strategy_signal(
            closed_candles,
            support,
            resistance,
            previous_state=previous_state,
            tolerance=zone_tolerance,
            market_price=current_price,
        )
        next_state["support"] = float(support)
        next_state["resistance"] = float(resistance)
        next_state["levels_timestamp"] = int(closed_candles[-1].timestamp)
        next_state["zone_tolerance"] = round(zone_tolerance, 6)
        state_store.save(state_key, next_state)
        logger.info("--- TRADING SIGNAL ---")
        logger.info(f"Timeframe: {timeframe} | Symbol: {symbol}")
        logger.info(f"Dynamic Levels: Support=${support:.2f} | Resistance=${resistance:.2f}")
        logger.info(f"Status: {signal.status}")
        logger.info(f"Position: {signal.position} | Pattern: {signal.pattern or 'NONE'}")
        logger.info(format_volume_summary(signal))
        logger.info(f"Action: {signal.action} | Last Close: {signal.price:.2f} | Live Price: {current_price:.2f}")
        if signal.entry_price is not None:
            logger.info(
                f"Trade Levels: Entry=${signal.entry_price:.2f} | "
                f"SL=${signal.stop_loss:.2f} | TP=${signal.take_profit:.2f}"
            )
        logger.info(f"Reason: {signal.reason}")
        logger.info("-----------------------")

        # 5. Telegram Notification: every generated trading signal, including HOLD.
        if notifier is not None and should_send_signal_notification(signal):
            await notifier.send_message(
                build_signal_summary(
                    symbol,
                    timeframe,
                    signal,
                    support,
                    resistance,
                    current_price,
                )
            )
        return True

    except Exception as e:
        logger.critical(f"System Failure: {e}", exc_info=True)
        return False
    finally:
        await exchange.close()

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(symbol=args.symbol, timeframe=args.timeframe))
