import asyncio
import argparse
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv
from exchange_manager import create_market_data_manager
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
BREAKOUT_MINIMUM_NEXT_RESISTANCE_ATR = 1.5
BREAKDOWN_MINIMUM_NEXT_SUPPORT_ATR = 1.5
DEFAULT_ZONE_ATR_MULTIPLIER = 0.75
ZONE_ATR_MULTIPLIERS = {
    "1h": 0.30,
}
ONE_HOUR_MAX_ZONE_POINTS = 1000
GOLD_PRICE_PER_POINT = 0.01
TIMEFRAME_DURATIONS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


def calculate_timeframe_zone_tolerance(analyzer, candles, timeframe: str) -> float:
    """Calculate the support/resistance zone width for a strategy timeframe."""
    atr_multiplier = ZONE_ATR_MULTIPLIERS.get(
        timeframe,
        DEFAULT_ZONE_ATR_MULTIPLIER,
    )
    if timeframe == "1h" and candles and candles[-1].close > 0:
        maximum = (
            ONE_HOUR_MAX_ZONE_POINTS
            * GOLD_PRICE_PER_POINT
            / candles[-1].close
        )
        return analyzer.calculate_zone_tolerance(
            candles,
            atr_multiplier=atr_multiplier,
            minimum=min(0.003, maximum),
            maximum=maximum,
        )
    return analyzer.calculate_zone_tolerance(
        candles,
        atr_multiplier=atr_multiplier,
    )


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


def _indicator(value: str, passed: bool, pending: bool = False) -> str:
    marker = "⏳" if pending else "✅" if passed else "❌"
    return f"{value} {marker}"


def _optional_indicator(value: Optional[bool]) -> str:
    if value is None:
        return "N/A"
    return "✅" if value else "❌"


def _format_bband_line(label: str, signal: Signal) -> str:
    is_le = label == "BBandLE"
    value = signal.bband_le if is_le else signal.bband_se
    return f"{label}: {_optional_indicator(value)}"


def _is_resistance_setup(signal: Signal) -> bool:
    return signal.position == "RESISTANCE" or signal.status.startswith(
        ("BREAKOUT", "RETEST_HELD")
    )


def _format_signal_reason(signal: Signal, is_resistance_setup: bool) -> str:
    if signal.status in {"BREAKOUT_WATCH", "BREAKDOWN_WATCH"}:
        level_name = "Breakout" if is_resistance_setup else "Breakdown"
        return (
            f"{level_name} occurred, but volume and candle confirmation\n"
            "are still insufficient. Waiting for confirmation/retest."
        )
    if signal.status in {"BREAKOUT_CONFIRMED", "BREAKDOWN_CONFIRMED"}:
        level_name = "Breakout" if is_resistance_setup else "Breakdown"
        return f"{level_name} confirmed. Waiting for confirmation/retest."
    if signal.status in {"RETEST_HELD", "RETEST_REJECTED"}:
        return "Retest confirmed. Signal is ready for the next move."
    return signal.reason


def build_signal_summary(
    symbol: str,
    timeframe: str,
    signal: Signal,
    support: float,
    resistance: float,
    current_price: float,
    next_resistance: Optional[float] = None,
    next_support: Optional[float] = None,
    strategy_state: Optional[dict] = None,
) -> str:
    state = strategy_state or {}
    is_resistance_setup = _is_resistance_setup(signal)
    level = resistance if is_resistance_setup else support

    if is_resistance_setup:
        trend_is_confirmed = state.get("uptrend") is True
        trend = "UP" if trend_is_confirmed else "DOWN" if state.get("uptrend") is False else "UNKNOWN"
        expected_pattern = "LONG_GREEN"
    else:
        trend_is_confirmed = state.get("downtrend") is True
        trend = "DOWN" if trend_is_confirmed else "UP" if state.get("downtrend") is False else "UNKNOWN"
        expected_pattern = "LONG_RED"

    breakout_is_confirmed = (
        current_price > level if is_resistance_setup else current_price < level
    )
    next_level_value = next_resistance if is_resistance_setup else next_support
    headroom = None
    if next_level_value is not None and current_price > 0:
        headroom = (
            (next_level_value - current_price) / current_price * 100
            if is_resistance_setup
            else (current_price - next_level_value) / current_price * 100
        )
    headroom_is_available = headroom is not None and headroom > 0

    volume_is_confirmed = signal.volume_status == "THICK"
    pattern = signal.pattern or "NONE"
    pattern_is_confirmed = pattern == expected_pattern
    retest_is_confirmed = signal.status in {"RETEST_HELD", "RETEST_REJECTED"}
    retest_is_pending = signal.status in {
        "BREAKOUT_WATCH",
        "BREAKOUT_CONFIRMED",
        "BREAKDOWN_WATCH",
        "BREAKDOWN_CONFIRMED",
    }
    bband_enabled = signal.bband_le is not None or signal.bband_se is not None
    score = sum(
        (
            breakout_is_confirmed,
            headroom_is_available,
            trend_is_confirmed,
            volume_is_confirmed,
            pattern_is_confirmed,
            retest_is_confirmed,
        )
    )

    symbol_name = symbol.split("/", 1)[0].upper()
    if is_resistance_setup:
        resistance_line = f"Resistance: {_indicator(f'${resistance:.2f}', breakout_is_confirmed)}"
        support_line = f"Support: ${support:.2f}"
    else:
        resistance_line = f"Resistance: ${resistance:.2f}"
        support_line = f"Support: {_indicator(f'${support:.2f}', breakout_is_confirmed)}"
    next_resistance_text = (
        f"${next_resistance:.2f}" if next_resistance is not None else "N/A"
    )
    next_support_text = f"${next_support:.2f}" if next_support is not None else "N/A"
    headroom_value = f"{headroom:.2f}%" if headroom is not None else "N/A"
    if retest_is_confirmed:
        retest = "HELD" if is_resistance_setup else "REJECTED"
    elif signal.status.endswith("INVALIDATED"):
        retest = "INVALID"
    else:
        retest = "WAIT"

    summary = [
        f"{symbol_name} {timeframe.upper()} | {signal.action} | {signal.status}",
        "",
        f"Price: ${current_price:.2f}",
        f"Close: ${signal.price:.2f}",
        "",
        resistance_line,
        f"Next Resistance: {next_resistance_text}",
        support_line,
        f"Next Support: {next_support_text}",
        f"Headroom: {_indicator(headroom_value, headroom_is_available)}",
        "",
        f"Trend: {_indicator(trend, trend_is_confirmed)}",
        f"Volume: {_indicator(f'{signal.volume_ratio:.2f}x' if signal.volume_ratio is not None else 'N/A', volume_is_confirmed)}",
        f"Pattern: {_indicator(pattern, pattern_is_confirmed)}",
        f"Retest: {_indicator(retest, retest_is_confirmed, retest_is_pending)}",
        "",
        f"Score: {score}/6",
        "",
        "Reason:",
        _format_signal_reason(signal, is_resistance_setup),
    ]
    if bband_enabled:
        bband_lines = [
            _format_bband_line("BBandLE", signal),
            _format_bband_line("BBandSE", signal),
        ]
        if signal.bband_upper is not None:
            bband_lines.append(
                "BBands: "
                f"Upper ${signal.bband_upper:.2f} | "
                f"Middle ${signal.bband_middle:.2f} | "
                f"Lower ${signal.bband_lower:.2f}"
            )
        retest_line = _indicator(retest, retest_is_confirmed, retest_is_pending)
        insert_at = summary.index(f"Retest: {retest_line}")
        summary[insert_at:insert_at] = bband_lines
    if signal.entry_price is not None:
        summary.extend(
            [
                "",
                f"Entry: ${signal.entry_price:.2f}",
                f"Stop Loss: ${signal.stop_loss:.2f}" if signal.stop_loss is not None else "Stop Loss: N/A",
                f"Take Profit: ${signal.take_profit:.2f}" if signal.take_profit is not None else "Take Profit: N/A",
            ]
        )
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
    market_data = None
    try:
        market_data = create_market_data_manager()
        # 1. Data Acquisition
        current_price = await market_data.fetch_current_price(symbol)
        candles = await market_data.fetch_ohlcv(
            symbol, timeframe, limit=FETCH_CANDLE_LIMIT
        )
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
        zone_tolerance = calculate_timeframe_zone_tolerance(
            analyzer,
            closed_candles,
            timeframe,
        )

        # Find recent zones and levels relative to the latest closed price.
        analysis_reference_price = closed_candles[-1].close
        latest_zones = analyzer.find_recent_snd_zones(
            closed_candles,
            analysis_reference_price,
            lookback=ANALYSIS_REPORT_LOOKBACK,
            limit=ANALYSIS_REPORT_ITEM_LIMIT,
            max_distance=zone_tolerance * analysis_reference_price,
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
        active_breakout_statuses = {
            "BREAKOUT_WATCH",
            "BREAKOUT_CONFIRMED",
            "RETEST_HELD",
        }
        active_breakdown_statuses = {
            "BREAKDOWN_WATCH",
            "BREAKDOWN_CONFIRMED",
            "RETEST_REJECTED",
        }
        next_resistance = previous_state.get("next_resistance")
        if (
            previous_state.get("status") not in active_breakout_statuses
            or next_resistance is None
        ):
            next_resistance = analyzer.find_next_resistance(
                closed_candles,
                resistance,
                lookback=ANALYSIS_REPORT_LOOKBACK,
            )
        next_support = previous_state.get("next_support")
        if (
            previous_state.get("status") not in active_breakdown_statuses
            or next_support is None
        ):
            next_support = analyzer.find_next_support(
                closed_candles,
                support,
                lookback=ANALYSIS_REPORT_LOOKBACK,
            )
        signal, next_state = analyzer.generate_strategy_signal(
            closed_candles,
            support,
            resistance,
            previous_state=previous_state,
            tolerance=zone_tolerance,
            market_price=current_price,
            next_resistance=next_resistance,
            minimum_next_resistance_atr=BREAKOUT_MINIMUM_NEXT_RESISTANCE_ATR,
            next_support=next_support,
            minimum_next_support_atr=BREAKDOWN_MINIMUM_NEXT_SUPPORT_ATR,
            bband_enabled=timeframe == "4h",
        )
        next_state["support"] = float(support)
        next_state["resistance"] = float(resistance)
        next_state["levels_timestamp"] = int(closed_candles[-1].timestamp)
        next_state["zone_tolerance"] = round(zone_tolerance, 6)
        state_store.save(state_key, next_state)
        if signal.entry_price is not None:
            state_store.save(
                f"{symbol}|exit_profit",
                {
                    "action": signal.action,
                    "entry_price": float(signal.entry_price),
                    "stop_loss": (
                        float(signal.stop_loss) if signal.stop_loss is not None else None
                    ),
                    "take_profit": (
                        float(signal.take_profit) if signal.take_profit is not None else None
                    ),
                    "entry_timestamp": int(closed_candles[-1].timestamp),
                },
            )
        signal_summary = build_signal_summary(
            symbol,
            timeframe,
            signal,
            support,
            resistance,
            current_price,
            next_resistance=next_resistance,
            next_support=next_support,
            strategy_state=next_state,
        )
        logger.info("\n%s", signal_summary)

        # 5. Telegram Notification: every generated trading signal, including HOLD.
        if notifier is not None and should_send_signal_notification(signal):
            await notifier.send_message(signal_summary)
        return True

    except Exception as e:
        logger.critical(f"System Failure: {e}", exc_info=True)
        return False
    finally:
        if market_data is not None:
            await market_data.close()

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(symbol=args.symbol, timeframe=args.timeframe))
