import argparse
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from analyzer import MarketAnalyzer
from exchange_manager import create_market_data_manager
from models import Candle
from telegram_notifier import TelegramNotifier
from trading_state import TradingStateStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("LiquiditySweepBot")

load_dotenv()

STRATEGY_TIMEFRAME = "1h"
STRUCTURE_TIMEFRAME = "1h"
FETCH_CANDLE_LIMIT = 300
STRUCTURE_CANDLE_LIMIT = 200
STRUCTURE_LOOKBACK = 60

TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
CANDLE_INTERVAL_MS = TIMEFRAME_MINUTES[STRATEGY_TIMEFRAME] * 60 * 1000

BANGKOK_TZ = timezone(timedelta(hours=7))
NOTIFY_START_HOUR = 12
NOTIFY_END_HOUR = 21
NOTIFY_WEEKDAYS = range(0, 5)

ASIAN_SESSION_HOURS_UTC = (0, 8)
LONDON_SESSION_HOURS_UTC = (8, 16)

ATR_PERIOD = 14
STOP_LOSS_ATR_BUFFER = 0.75
MINIMUM_RISK_REWARD = 0.5
LONG_CANDLE_ATR_MULTIPLIER = 1.5
MINIMUM_OPPOSITE_STREAK_CANDLES = 2

CLOSE_BACK_LOOKAHEAD_CANDLES = 24
STALE_SETUP_ATR_MULTIPLIER = 3.0
CONFIRMATION_CANDLES = 1
CONFIRMATION_LOOKAHEAD_CANDLES = 6
ENTRY_COOLDOWN_CANDLES = 12

RESISTANCE = "RESISTANCE"
SUPPORT = "SUPPORT"

PHASE_IDLE = "IDLE"
PHASE_NEAR_ZONE = "NEAR_ZONE"
PHASE_SWEPT = "SWEPT"
PHASE_CONFIRMING = "CONFIRMING"
PHASE_ENTERED = "ENTERED"


class Zone:
    def __init__(self, name: str, side: str, price: float):
        self.name = name
        self.side = side
        self.price = price

    def to_dict(self) -> dict:
        return {"name": self.name, "side": self.side, "price": self.price}

    @classmethod
    def from_dict(cls, data: dict) -> "Zone":
        return cls(name=data["name"], side=data["side"], price=float(data["price"]))


def bangkok_now(now: Optional[datetime] = None) -> datetime:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(BANGKOK_TZ)


def is_within_notification_window(now: Optional[datetime] = None) -> bool:
    local = bangkok_now(now)
    return local.weekday() in NOTIFY_WEEKDAYS and NOTIFY_START_HOUR <= local.hour < NOTIFY_END_HOUR


def session_high_low(
    candles: List[Candle],
    session_hours_utc: Tuple[int, int],
    now: Optional[datetime] = None,
) -> Optional[Tuple[float, float]]:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    today = reference.astimezone(timezone.utc).date()
    start_hour, end_hour = session_hours_utc

    session_candles = []
    for candle in candles:
        candle_time = datetime.fromtimestamp(candle.timestamp / 1000, tz=timezone.utc)
        if candle_time.date() == today and start_hour <= candle_time.hour < end_hour:
            session_candles.append(candle)

    if not session_candles:
        return None
    return max(c.high for c in session_candles), min(c.low for c in session_candles)


def recent_swing_levels(
    analyzer: MarketAnalyzer, candles: List[Candle], lookback: int
) -> Tuple[Optional[float], Optional[float]]:
    recent_candles = candles[-max(5, lookback):]
    resistance_levels, support_levels = analyzer.find_support_resistance(recent_candles)
    recent_high = resistance_levels[-1] if resistance_levels else None
    recent_low = support_levels[-1] if support_levels else None
    return recent_high, recent_low


def build_key_zones(
    analyzer: MarketAnalyzer,
    structure_candles: List[Candle],
    asian_range: Optional[Tuple[float, float]],
    london_range: Optional[Tuple[float, float]],
    price: float,
) -> List[Zone]:
    zones: List[Zone] = []
    if asian_range is not None:
        high, low = asian_range
        zones.append(Zone("Asian High", RESISTANCE, high))
        zones.append(Zone("Asian Low", SUPPORT, low))
    if london_range is not None:
        high, low = london_range
        zones.append(Zone("London High", RESISTANCE, high))
        zones.append(Zone("London Low", SUPPORT, low))

    resistances, supports = analyzer.find_key_levels(
        structure_candles, price, lookback=STRUCTURE_LOOKBACK, limit=1
    )
    for level in resistances:
        zones.append(Zone("Key Resistance", RESISTANCE, level))
    for level in supports:
        zones.append(Zone("Key Support", SUPPORT, level))

    recent_high, recent_low = recent_swing_levels(analyzer, structure_candles, STRUCTURE_LOOKBACK)
    if recent_high is not None:
        zones.append(Zone("Recent Swing High", RESISTANCE, recent_high))
    if recent_low is not None:
        zones.append(Zone("Recent Swing Low", SUPPORT, recent_low))
    return zones


def find_nearby_zone(zones: List[Zone], price: float, tolerance_pct: float) -> Optional[Zone]:
    if price <= 0 or not zones:
        return None
    max_distance = tolerance_pct * price
    nearest: Optional[Zone] = None
    nearest_distance = None
    for zone in zones:
        distance = abs(zone.price - price)
        if distance <= max_distance and (nearest_distance is None or distance < nearest_distance):
            nearest = zone
            nearest_distance = distance
    return nearest


def detect_sweep(zone: Zone, candle: Candle) -> bool:
    if zone.side == RESISTANCE:
        return candle.high > zone.price
    return candle.low < zone.price


def detect_close_back(zone: Zone, candle: Candle) -> bool:
    if zone.side == RESISTANCE:
        return candle.close <= zone.price
    return candle.close >= zone.price


def direction_for_zone_side(side: str) -> str:
    return "SELL" if side == RESISTANCE else "BUY"


def candles_established_before_latest(candles: List[Candle]) -> List[Candle]:
    return candles[:-1] if len(candles) > 1 else candles


def compute_trade_levels(
    direction: str,
    sweep_extreme: float,
    entry_price: float,
    opposite_liquidity: Optional[float],
    atr_value: Optional[float],
) -> Tuple[Optional[float], Optional[float]]:
    buffer_value = STOP_LOSS_ATR_BUFFER * atr_value if atr_value else 0.0
    if direction == "SELL":
        stop_loss = sweep_extreme + buffer_value
        take_profit = opposite_liquidity if opposite_liquidity is not None and opposite_liquidity < entry_price else None
        return stop_loss, take_profit
    stop_loss = sweep_extreme - buffer_value
    take_profit = opposite_liquidity if opposite_liquidity is not None and opposite_liquidity > entry_price else None
    return stop_loss, take_profit


def candle_color(candle: Candle) -> str:
    if candle.close > candle.open:
        return "GREEN"
    if candle.close < candle.open:
        return "RED"
    return "NEUTRAL"


def is_long_candle(candle: Candle, atr_value: Optional[float]) -> bool:
    if atr_value is None or atr_value <= 0:
        return False
    return abs(candle.close - candle.open) >= LONG_CANDLE_ATR_MULTIPLIER * atr_value


def opposite_streak_length(candles: List[Candle], opposite_color: str) -> int:
    streak = 0
    for candle in reversed(candles):
        if candle_color(candle) != opposite_color:
            break
        streak += 1
    return streak


def detect_reversal_candle(
    direction: str, closed_candles: List[Candle], atr_value: Optional[float]
) -> bool:
    if len(closed_candles) < 2:
        return False
    current = closed_candles[-1]
    expected_color = "GREEN" if direction == "BUY" else "RED"
    opposite_color = "RED" if direction == "BUY" else "GREEN"
    if candle_color(current) != expected_color:
        return False

    leading_candles = closed_candles[:-1]
    if candle_color(leading_candles[-1]) != opposite_color:
        return False
    if is_long_candle(leading_candles[-1], atr_value):
        return True
    return opposite_streak_length(leading_candles, opposite_color) >= MINIMUM_OPPOSITE_STREAK_CANDLES


def risk_reward_ratio(
    entry_price: float, stop_loss: Optional[float], take_profit: Optional[float]
) -> Optional[float]:
    if stop_loss is None or take_profit is None:
        return None
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    return abs(take_profit - entry_price) / risk


def opposite_liquidity_level(zones: List[Zone], direction: str, price: float) -> Optional[float]:
    opposite_side = SUPPORT if direction == "SELL" else RESISTANCE
    candidates = [z.price for z in zones if z.side == opposite_side]
    if not candidates:
        return None
    if direction == "SELL":
        below = [level for level in candidates if level < price]
        return max(below) if below else min(candidates)
    above = [level for level in candidates if level > price]
    return min(above) if above else max(candidates)


PROGRESS_MESSAGES = {
    PHASE_NEAR_ZONE: "ราคาเข้าใกล้โซนสำคัญ: {zone_name} ${zone_price:.2f}\nกำลังจับตาการกวาด Liquidity",
    PHASE_SWEPT: "ราคาวิ่งไปชน/ทะลุ {zone_name} ($ {zone_price:.2f})\nรอดูว่าจะเด้งกลับมาปิดเลยแนวหรือไม่",
    "SWEEP_INVALID": "ยังไม่เข้าไม้ — ราคาไม่เด้งกลับมาปิดเลย {zone_name}\nอาจเป็น Breakout จริง รอดูต่อ",
    "STALE_SETUP": (
        "ยกเลิก setup เดิมที่ {zone_name}\n"
        "ราคาวิ่งหนีไปไกลเกินไปแล้ว กลับไปเฝ้าดูโซนใหม่"
    ),
    PHASE_CONFIRMING: "เด้งกลับมาปิดเลย {zone_name} แล้ว\nรอดูว่าจะยืนราคาได้ไหม",
    "CONFIRMATION_FAILED": "ยังไม่เข้าไม้ — ราคากลับไปหลุด {zone_name} อีกครั้งระหว่างรอยืนราคา\nอาจเป็น Breakout จริง รอดูต่อ",
    "RR_TOO_LOW": (
        "ข้ามการเข้าไม้ที่ {zone_name}\n"
        "โซนเป้าหมายฝั่งตรงข้ามใกล้เกินไป (R:R {risk_reward_text} ต่ำกว่าเกณฑ์ {minimum_rr}) กลับไปเฝ้าดูโซนใหม่"
    ),
    "REVERSAL_CANDLE_MISSING": (
        "ข้ามการเข้าไม้ที่ {zone_name}\n"
        "แท่งเทียนยังไม่กลับสี ({expected_color}) กลับไปเฝ้าดูโซนใหม่"
    ),
    "REVERSAL_DETECTED": (
        "ตรวจพบแท่งกลับตัว ({expected_color}) ที่ {zone_name}\n"
        "แท่ง{current_color}แรกหลังแท่ง{opposite_color}ยาวหรือหลายแท่งติด กำลังเช็คเงื่อนไขก่อนเข้าไม้"
    ),
    PHASE_ENTERED: (
        "จุดที่ราคามักวิ่งจริง 🎯\n"
        "เด้งกลับมาปิดเลยแนวแล้ว เข้า {direction} ที่ ${entry_price:.2f}\n"
        "Stop Loss: {stop_loss_text}\n"
        "Take Profit: {take_profit_text}"
    ),
}


def format_price(value: Optional[float]) -> str:
    return f"${value:.2f}" if value is not None else "N/A"


def build_progress_message(symbol: str, key: str, **fields) -> str:
    symbol_name = symbol.split("/", 1)[0].upper()
    template = PROGRESS_MESSAGES[key]
    risk_reward = fields.get("risk_reward")
    body = template.format(
        stop_loss_text=format_price(fields.get("stop_loss")),
        take_profit_text=format_price(fields.get("take_profit")),
        risk_reward_text=f"{risk_reward:.2f}" if risk_reward is not None else "N/A",
        minimum_rr=MINIMUM_RISK_REWARD,
        **fields,
    )
    return f"{symbol_name} {STRATEGY_TIMEFRAME.upper()} | Liquidity Sweep\n\n{body}"


def _expires_at(candle_timestamp: int, candles_ahead: int) -> int:
    return candle_timestamp + candles_ahead * CANDLE_INTERVAL_MS


def _reset_state(candle: Candle) -> dict:
    return {"phase": PHASE_IDLE, "last_candle_timestamp": candle.timestamp}


def is_setup_stale(zone_price: float, price: float, atr_value: Optional[float]) -> bool:
    if atr_value is None or atr_value <= 0:
        return False
    return abs(price - zone_price) >= STALE_SETUP_ATR_MULTIPLIER * atr_value


def _attempt_entry(
    analyzer: MarketAnalyzer,
    closed_5m: List[Candle],
    zones: List[Zone],
    state: dict,
    zone: Zone,
    direction: str,
    symbol: str,
    latest: Candle,
) -> Tuple[dict, List[str]]:
    atr_value = analyzer.atr(closed_5m, ATR_PERIOD)
    if not detect_reversal_candle(direction, closed_5m, atr_value):
        expected_color = "แดง→เขียว" if direction == "BUY" else "เขียว→แดง"
        return _reset_state(latest), [
            build_progress_message(
                symbol, "REVERSAL_CANDLE_MISSING", zone_name=zone.name, expected_color=expected_color
            )
        ]

    expected_color = "แดง→เขียว" if direction == "BUY" else "เขียว→แดง"
    reversal_message = build_progress_message(
        symbol,
        "REVERSAL_DETECTED",
        zone_name=zone.name,
        expected_color=expected_color,
        current_color="เขียว" if direction == "BUY" else "แดง",
        opposite_color="แดง" if direction == "BUY" else "เขียว",
    )

    entry_price = latest.close
    opposite_level = opposite_liquidity_level(zones, direction, entry_price)
    stop_loss, take_profit = compute_trade_levels(
        direction, state["sweep_extreme"], entry_price, opposite_level, atr_value
    )
    risk_reward = risk_reward_ratio(entry_price, stop_loss, take_profit)
    if risk_reward is None or risk_reward < MINIMUM_RISK_REWARD:
        skip_message = build_progress_message(
            symbol, "RR_TOO_LOW", zone_name=zone.name, risk_reward=risk_reward
        )
        return _reset_state(latest), [reversal_message, skip_message]

    new_state = {
        **state,
        "phase": PHASE_ENTERED,
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "expires_at": _expires_at(latest.timestamp, ENTRY_COOLDOWN_CANDLES),
        "last_candle_timestamp": latest.timestamp,
    }
    entry_message = build_progress_message(
        symbol,
        PHASE_ENTERED,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
    )
    return new_state, [reversal_message, entry_message]


def advance_liquidity_sweep_state(
    analyzer: MarketAnalyzer,
    closed_5m: List[Candle],
    zones: List[Zone],
    state: dict,
    symbol: str,
) -> Tuple[dict, List[str]]:
    if not closed_5m:
        return state, []

    latest = closed_5m[-1]
    if state.get("last_candle_timestamp") == latest.timestamp:
        return state, []

    phase = state.get("phase", PHASE_IDLE)
    tolerance = analyzer.calculate_zone_tolerance(closed_5m)

    if phase == PHASE_IDLE:
        zone = find_nearby_zone(zones, latest.close, tolerance)
        if zone is None:
            return _reset_state(latest), []
        new_state = {
            **zone.to_dict(),
            "phase": PHASE_NEAR_ZONE,
            "last_candle_timestamp": latest.timestamp,
        }
        return new_state, [
            build_progress_message(symbol, PHASE_NEAR_ZONE, zone_name=zone.name, zone_price=zone.price)
        ]

    zone = Zone.from_dict(state)
    direction = direction_for_zone_side(zone.side)

    if phase == PHASE_NEAR_ZONE:
        if detect_sweep(zone, latest):
            new_state = {
                **state,
                "phase": PHASE_SWEPT,
                "sweep_extreme": latest.high if zone.side == RESISTANCE else latest.low,
                "sweep_timestamp": latest.timestamp,
                "expires_at": _expires_at(latest.timestamp, CLOSE_BACK_LOOKAHEAD_CANDLES),
                "last_candle_timestamp": latest.timestamp,
            }
            return new_state, [
                build_progress_message(symbol, PHASE_SWEPT, zone_name=zone.name, zone_price=zone.price)
            ]
        if not find_nearby_zone([zone], latest.close, tolerance * 3):
            return _reset_state(latest), []
        return {**state, "last_candle_timestamp": latest.timestamp}, []

    if phase == PHASE_SWEPT:
        atr_value = analyzer.atr(closed_5m, ATR_PERIOD)
        if is_setup_stale(zone.price, latest.close, atr_value):
            return _reset_state(latest), [build_progress_message(symbol, "STALE_SETUP", zone_name=zone.name)]
        if detect_close_back(zone, latest):
            if CONFIRMATION_CANDLES <= 1:
                return _attempt_entry(analyzer, closed_5m, zones, state, zone, direction, symbol, latest)
            new_state = {
                **state,
                "phase": PHASE_CONFIRMING,
                "confirmation_count": 1,
                "expires_at": _expires_at(latest.timestamp, CONFIRMATION_LOOKAHEAD_CANDLES),
                "last_candle_timestamp": latest.timestamp,
            }
            return new_state, [build_progress_message(symbol, PHASE_CONFIRMING, zone_name=zone.name)]
        if latest.timestamp >= state.get("expires_at", latest.timestamp):
            return _reset_state(latest), [
                build_progress_message(symbol, "SWEEP_INVALID", zone_name=zone.name)
            ]
        return {**state, "last_candle_timestamp": latest.timestamp}, []

    if phase == PHASE_CONFIRMING:
        atr_value_confirming = analyzer.atr(closed_5m, ATR_PERIOD)
        if is_setup_stale(zone.price, latest.close, atr_value_confirming):
            return _reset_state(latest), [build_progress_message(symbol, "STALE_SETUP", zone_name=zone.name)]
        still_holding = detect_close_back(zone, latest)
        expired = latest.timestamp >= state.get("expires_at", latest.timestamp)
        if still_holding and not expired:
            confirmation_count = state.get("confirmation_count", 1) + 1
            if confirmation_count >= CONFIRMATION_CANDLES:
                return _attempt_entry(analyzer, closed_5m, zones, state, zone, direction, symbol, latest)
            return {
                **state,
                "confirmation_count": confirmation_count,
                "last_candle_timestamp": latest.timestamp,
            }, []
        return _reset_state(latest), [
            build_progress_message(symbol, "CONFIRMATION_FAILED", zone_name=zone.name)
        ]

    if phase == PHASE_ENTERED:
        if latest.timestamp >= state.get("expires_at", latest.timestamp):
            return _reset_state(latest), []
        return {**state, "last_candle_timestamp": latest.timestamp}, []

    return _reset_state(latest), []


async def run_liquidity_sweep_check(symbol: str = "PAXG/USDT", now: Optional[datetime] = None) -> bool:
    if not is_within_notification_window(now):
        logger.info("Outside the 12:00-20:00 Bangkok Mon-Fri window; skipping.")
        return True

    logger.info("🎯 Starting Liquidity Sweep check for %s...", symbol)
    notifier = TelegramNotifier.from_env()
    market_data = None
    try:
        market_data = create_market_data_manager()
        candles_5m = await market_data.fetch_ohlcv(symbol, STRATEGY_TIMEFRAME, limit=FETCH_CANDLE_LIMIT)
        candles_1h = await market_data.fetch_ohlcv(symbol, STRUCTURE_TIMEFRAME, limit=STRUCTURE_CANDLE_LIMIT)
        closed_5m = candles_5m[:-1] if len(candles_5m) > 1 else candles_5m
        closed_1h = candles_1h[:-1] if len(candles_1h) > 1 else candles_1h
        if not closed_5m:
            logger.warning("No closed candles available; skipping.")
            return True

        analyzer = MarketAnalyzer()
        reference_time = now or datetime.now(timezone.utc)
        zone_context = candles_established_before_latest(closed_5m)
        asian_range = session_high_low(zone_context, ASIAN_SESSION_HOURS_UTC, reference_time)
        london_range = session_high_low(zone_context, LONDON_SESSION_HOURS_UTC, reference_time)
        zones = build_key_zones(analyzer, closed_1h, asian_range, london_range, closed_5m[-1].close)

        state_store = TradingStateStore()
        state_key = f"{symbol}|liquidity_sweep"
        state = state_store.get(state_key)

        new_state, messages = advance_liquidity_sweep_state(analyzer, closed_5m, zones, state, symbol)
        state_store.save(state_key, new_state)

        if new_state.get("phase") == PHASE_ENTERED and new_state.get("entry_price") is not None:
            state_store.save(
                f"{symbol}|exit_profit",
                {
                    "action": new_state["direction"],
                    "entry_price": float(new_state["entry_price"]),
                    "stop_loss": (
                        float(new_state["stop_loss"]) if new_state.get("stop_loss") is not None else None
                    ),
                    "take_profit": (
                        float(new_state["take_profit"]) if new_state.get("take_profit") is not None else None
                    ),
                    "entry_timestamp": int(closed_5m[-1].timestamp),
                },
            )

        if messages:
            for message in messages:
                logger.info("\n%s", message)
                if notifier is not None:
                    await notifier.send_message(message)
        else:
            logger.info("No phase change (phase=%s)", new_state.get("phase"))
        return True
    except Exception as e:
        logger.critical("System Failure: %s", e, exc_info=True)
        return False
    finally:
        if market_data is not None:
            await market_data.close()


def parse_args():
    parser = argparse.ArgumentParser(description="XAUUSD Liquidity Sweep Notification Bot")
    parser.add_argument("--symbol", default="PAXG/USDT", help="Trading pair, e.g. PAXG/USDT")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_liquidity_sweep_check(symbol=args.symbol))
