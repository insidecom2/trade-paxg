import asyncio
import argparse
import logging
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


def parse_args():
    parser = argparse.ArgumentParser(description="PAXG Trading Signal Bot")
    parser.add_argument("--symbol", default="PAXG/USDT", help="Trading pair, e.g. PAXG/USDT")
    parser.add_argument(
        "--tf",
        "--timeframe",
        dest="timeframe",
        default="4h",
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Strategy candle timeframe (default: 15m)",
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


def build_signal_summary(
    symbol: str,
    timeframe: str,
    signal: Signal,
    support: float,
    resistance: float,
    current_price: float,
    candles_fetched=None,
    zones=None,
    key_resistance_levels=None,
    key_support_levels=None,
) -> str:
    summary = [
        "PAXG Trading Log",
        "=== ANALYSIS REPORT ===",
        f"Current Price: ${current_price:.2f}",
        f"Candles Fetched: {candles_fetched if candles_fetched is not None else 'N/A'}",
    ]
    for zone in (zones or []):
        summary.append(
            f"Zone Found: {zone.type} | Range: [{zone.bottom:.2f} - {zone.top:.2f}]"
        )
    if not zones:
        summary.append("Zone Found: NONE")
    if key_resistance_levels:
        summary.append(f"Key Resistance Levels: {key_resistance_levels}")
    else:
        summary.append("Key Resistance Levels: NONE")
    if key_support_levels:
        summary.append(f"Key Support Levels: {key_support_levels}")
    else:
        summary.append("Key Support Levels: NONE")
    summary.extend([
        "=== TRADING SIGNAL ===",
        f"Timeframe: {timeframe} | Symbol: {symbol}",
        f"Dynamic Levels: Support=${support:.2f} | Resistance=${resistance:.2f}",
        f"Action: {signal.action}",
        f"Status: {signal.status}",
        f"Position: {signal.position} | Pattern: {signal.pattern or 'NONE'}",
        format_volume_summary(signal),
        f"Last Close: ${signal.price:.2f} | Live Price: ${current_price:.2f}",
        f"Reason: {signal.reason}",
    ])
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
    """Keeps 4h levels stable until a new closed candle is available."""
    if not candles:
        raise ValueError("At least one closed candle is required")

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

    return analyzer.find_dynamic_levels(candles, price, lookback=lookback)

async def main(symbol: str = "PAXG/USDT", timeframe: str = "15m") -> bool:
    logger.info(f"🚀 Starting Analysis for {symbol}...")

    notifier = TelegramNotifier.from_env()
    exchange = BinanceManager()
    try:
        # 1. Data Acquisition
        current_price = await exchange.fetch_current_price(symbol)
        candles = await exchange.fetch_ohlcv(symbol, timeframe, limit=250)
        logger.info(f"Successfully fetched {len(candles)} candles.")

        # Binance may include the currently open candle; strategy decisions use closed candles only.
        closed_candles = candles[:-1] if len(candles) > 1 else candles

        # 2. Analysis
        analyzer = MarketAnalyzer()
        state_store = TradingStateStore()
        state_key = f"{symbol}|{timeframe}"
        previous_state = state_store.get(state_key)

        # Find SND Zones
        snd_zones = analyzer.find_snd_zones(closed_candles)
        # Find Support/Resistance
        res_levels, sup_levels = analyzer.find_support_resistance(closed_candles)

        # 3. Production Logging of Results
        logger.info("--- ANALYSIS REPORT ---")

        # Log SND
        for zone in snd_zones[-3:]: # Show last 3 zones
            logger.info(f"Zone Found: {zone.type} | Range: [{zone.bottom:.2f} - {zone.top:.2f}]")

        # Log SE
        if res_levels:
            logger.info(f"Key Resistance Levels: {sorted(res_levels, reverse=True)[:3]}")
        if sup_levels:
            logger.info(f"Key Support Levels: {sorted(sup_levels)[:3]}")

        logger.info("-----------------------")

        # 4. Trading Signal (dynamic support/resistance from live data)
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

        # 5. Telegram Notification
        if notifier is not None:
            await notifier.send_message(
                build_signal_summary(
                    symbol,
                    timeframe,
                    signal,
                    support,
                    resistance,
                    current_price,
                    candles_fetched=len(candles),
                    zones=snd_zones[-3:],
                    key_resistance_levels=sorted(res_levels, reverse=True)[:3],
                    key_support_levels=sorted(sup_levels)[:3],
                )
            )
        return True

    except Exception as e:
        logger.critical(f"System Failure: {e}", exc_info=True)
        if notifier is not None:
            await notifier.send_message(f"PAXG Trading Bot failure: {e}")
        return False
    finally:
        await exchange.close()

if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(symbol=args.symbol, timeframe=args.timeframe))
