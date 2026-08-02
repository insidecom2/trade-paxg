import asyncio
import argparse
import logging
from dotenv import load_dotenv
from exchange_manager import BinanceManager
from analyzer import MarketAnalyzer
from telegram_notifier import TelegramNotifier
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
    parser.add_argument("--tf", "--timeframe", dest="timeframe", default="1h",
                        help="Candle timeframe: 1m, 5m, 15m, 1h, 4h, 1d (default: 1h)")
    return parser.parse_args()

def build_signal_summary(symbol: str, timeframe: str, signal: Signal, support: float, resistance: float, current_price: float) -> str:
    return "\n".join([
        "PAXG Trading Signal",
        f"Symbol: {symbol} | Timeframe: {timeframe}",
        f"Action: {signal.action}",
        f"Position: {signal.position} | Pattern: {signal.pattern or 'NONE'}",
        f"Support: ${support:.2f} | Resistance: ${resistance:.2f}",
        f"Last Close: ${signal.price:.2f} | Live Price: ${current_price:.2f}",
        f"Reason: {signal.reason}",
    ])

async def main():
    args = parse_args()
    symbol = args.symbol
    timeframe = args.timeframe
    
    logger.info(f"🚀 Starting Analysis for {symbol}...")

    notifier = TelegramNotifier.from_env()
    exchange = BinanceManager()
    try:
        # 1. Data Acquisition
        current_price = await exchange.fetch_current_price(symbol)
        candles = await exchange.fetch_ohlcv(symbol, timeframe)
        logger.info(f"Successfully fetched {len(candles)} candles.")

        # 2. Analysis
        analyzer = MarketAnalyzer()
        
        # Find SND Zones
        snd_zones = analyzer.find_snd_zones(candles)
        # Find Support/Resistance
        res_levels, sup_levels = analyzer.find_support_resistance(candles)

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
        support, resistance = analyzer.find_dynamic_levels(candles, current_price)
        signal = analyzer.generate_signal(candles, support, resistance)
        logger.info("--- TRADING SIGNAL ---")
        logger.info(f"Timeframe: {timeframe} | Symbol: {symbol}")
        logger.info(f"Dynamic Levels: Support=${support:.2f} | Resistance=${resistance:.2f}")
        logger.info(f"Position: {signal.position} | Pattern: {signal.pattern or 'NONE'}")
        logger.info(f"Action: {signal.action} | Last Close: {signal.price:.2f} | Live Price: {current_price:.2f}")
        logger.info(f"Reason: {signal.reason}")
        logger.info("-----------------------")

        # 5. Telegram Notification
        if notifier is not None:
            await notifier.send_message(
                build_signal_summary(symbol, timeframe, signal, support, resistance, current_price)
            )

    except Exception as e:
        logger.critical(f"System Failure: {e}", exc_info=True)
        if notifier is not None:
            await notifier.send_message(f"PAXG Trading Bot failure: {e}")
    finally:
        await exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
