import argparse
import asyncio
import bisect
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from analyzer import MarketAnalyzer
from exchange_manager import BinanceManager
from models import Candle

import liquidity_sweep as ls

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("LiquiditySweepBacktest")

FETCH_LIMIT = 1000
CONTEXT_BUFFER_DAYS = 10
DEFAULT_MAX_HOLD_CANDLES = 1440  # 5 trading days at 5m


@dataclass
class Trade:
    entry_timestamp: int
    direction: str
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    outcome: str
    exit_price: Optional[float]
    holding_candles: int
    r_multiple: Optional[float]


@dataclass
class ReversalEvent:
    timestamp: int
    zone_name: str
    direction: str
    led_to_entry: bool
    skip_reason: Optional[str]


REVERSAL_DETECTED_MARKER = "ตรวจพบแท่งกลับตัว"


async def fetch_paginated_ohlcv(
    manager: BinanceManager, symbol: str, timeframe: str, since_ms: int, until_ms: int
) -> List[Candle]:
    candles: dict[int, Candle] = {}
    cursor = since_ms
    while cursor < until_ms:
        batch = await manager.fetch_ohlcv(symbol, timeframe, limit=FETCH_LIMIT, since=cursor)
        if not batch:
            break
        for candle in batch:
            if candle.timestamp < until_ms:
                candles[candle.timestamp] = candle
        last_timestamp = batch[-1].timestamp
        if last_timestamp <= cursor:
            break
        cursor = last_timestamp + 1
        if len(batch) < FETCH_LIMIT:
            break
    return [candles[ts] for ts in sorted(candles)]


def window_ending_at(sorted_candles: List[Candle], timestamps: List[int], at_index: int, limit: int) -> List[Candle]:
    start = max(0, at_index - limit + 1)
    return sorted_candles[start:at_index + 1]


def last_closed_before(sorted_candles: List[Candle], timestamps: List[int], reference_ts: int, limit: int) -> List[Candle]:
    index = bisect.bisect_right(timestamps, reference_ts)
    start = max(0, index - limit)
    return sorted_candles[start:index]


def is_backtest_tick(timestamp_ms: int) -> bool:
    candle_time = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    return ls.is_within_notification_window(candle_time)


def simulate_outcome(
    direction: str,
    entry_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    future_candles: List[Candle],
    max_hold_candles: int,
) -> Tuple[str, Optional[float], int]:
    if stop_loss is None:
        return "NO_STOP", None, 0

    for offset, candle in enumerate(future_candles[:max_hold_candles], start=1):
        if direction == "SELL":
            hit_stop = candle.high >= stop_loss
            hit_target = take_profit is not None and candle.low <= take_profit
        else:
            hit_stop = candle.low <= stop_loss
            hit_target = take_profit is not None and candle.high >= take_profit

        if hit_stop:
            return "LOSS", stop_loss, offset
        if hit_target:
            return "WIN", take_profit, offset

    return "OPEN", None, min(len(future_candles), max_hold_candles)


def r_multiple(direction: str, entry_price: float, stop_loss: float, outcome: str, exit_price: Optional[float]) -> Optional[float]:
    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return None
    if outcome == "WIN" and exit_price is not None:
        return abs(exit_price - entry_price) / risk
    if outcome == "LOSS":
        return -1.0
    return None


async def run_backtest(
    symbol: str,
    days: int,
    max_hold_candles: int,
) -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    fetch_since = start - timedelta(days=CONTEXT_BUFFER_DAYS)

    manager = BinanceManager()
    try:
        logger.info("Fetching %s candles from %s to %s...", ls.STRATEGY_TIMEFRAME, fetch_since.date(), end.date())
        candles_5m = await fetch_paginated_ohlcv(
            manager, symbol, ls.STRATEGY_TIMEFRAME, int(fetch_since.timestamp() * 1000), int(end.timestamp() * 1000)
        )
        logger.info("Fetching %s candles from %s to %s...", ls.STRUCTURE_TIMEFRAME, fetch_since.date(), end.date())
        candles_1h = await fetch_paginated_ohlcv(
            manager, symbol, ls.STRUCTURE_TIMEFRAME, int(fetch_since.timestamp() * 1000), int(end.timestamp() * 1000)
        )
    finally:
        await manager.close()

    if not candles_5m or not candles_1h:
        logger.error("No historical data returned; aborting.")
        return

    logger.info(
        "Fetched %d %s candles and %d %s candles.",
        len(candles_5m), ls.STRATEGY_TIMEFRAME, len(candles_1h), ls.STRUCTURE_TIMEFRAME,
    )

    timestamps_5m = [c.timestamp for c in candles_5m]
    timestamps_1h = [c.timestamp for c in candles_1h]
    analyzer = MarketAnalyzer()

    state: dict = {}
    trades: List[Trade] = []
    reversal_events: List[ReversalEvent] = []
    funnel: Counter = Counter()
    invalidated_from: Counter = Counter()
    ticks_evaluated = 0

    backtest_start_ts = int(start.timestamp() * 1000)

    for index, candle in enumerate(candles_5m):
        if candle.timestamp < backtest_start_ts:
            continue
        if not is_backtest_tick(candle.timestamp):
            continue

        ticks_evaluated += 1
        closed_5m_window = window_ending_at(candles_5m, timestamps_5m, index, ls.FETCH_CANDLE_LIMIT)
        closed_1h_window = last_closed_before(candles_1h, timestamps_1h, candle.timestamp, ls.STRUCTURE_CANDLE_LIMIT)
        if len(closed_1h_window) < 5:
            continue

        reference_time = datetime.fromtimestamp(candle.timestamp / 1000, tz=timezone.utc)
        zone_context = ls.candles_established_before_latest(closed_5m_window)
        asian_range = ls.session_high_low(zone_context, ls.ASIAN_SESSION_HOURS_UTC, reference_time)
        london_range = ls.session_high_low(zone_context, ls.LONDON_SESSION_HOURS_UTC, reference_time)
        zones = ls.build_key_zones(analyzer, closed_1h_window, asian_range, london_range, candle.close)

        phase_before = state.get("phase", ls.PHASE_IDLE)
        zone_name_before = state.get("name")
        zone_side_before = state.get("side")
        new_state, messages = ls.advance_liquidity_sweep_state(analyzer, closed_5m_window, zones, state, symbol)
        phase_after = new_state.get("phase", ls.PHASE_IDLE)
        state = new_state

        if not messages:
            continue

        if phase_after != ls.PHASE_IDLE:
            funnel[phase_after] += 1

        if phase_after == ls.PHASE_IDLE and phase_before != ls.PHASE_IDLE:
            invalidated_from[phase_before] += 1

        if len(messages) == 2 and REVERSAL_DETECTED_MARKER in messages[0]:
            led_to_entry = phase_after == ls.PHASE_ENTERED
            reversal_events.append(
                ReversalEvent(
                    timestamp=candle.timestamp,
                    zone_name=zone_name_before or "?",
                    direction=ls.direction_for_zone_side(zone_side_before) if zone_side_before else "?",
                    led_to_entry=led_to_entry,
                    skip_reason=None if led_to_entry else messages[1],
                )
            )

        if phase_after == ls.PHASE_ENTERED:
            future_candles = candles_5m[index + 1:]
            outcome, exit_price, holding_candles = simulate_outcome(
                new_state["direction"],
                new_state["entry_price"],
                new_state.get("stop_loss"),
                new_state.get("take_profit"),
                future_candles,
                max_hold_candles,
            )
            stop_loss = new_state.get("stop_loss")
            r_value = r_multiple(
                new_state["direction"], new_state["entry_price"], stop_loss, outcome, exit_price
            ) if stop_loss is not None else None
            trades.append(
                Trade(
                    entry_timestamp=candle.timestamp,
                    direction=new_state["direction"],
                    entry_price=new_state["entry_price"],
                    stop_loss=stop_loss,
                    take_profit=new_state.get("take_profit"),
                    outcome=outcome,
                    exit_price=exit_price,
                    holding_candles=holding_candles,
                    r_multiple=r_value,
                )
            )

    print_report(symbol, days, ticks_evaluated, funnel, invalidated_from, trades, reversal_events)


def print_report(
    symbol: str,
    days: int,
    ticks_evaluated: int,
    funnel: Counter,
    invalidated_from: Counter,
    trades: List[Trade],
    reversal_events: List[ReversalEvent],
) -> None:
    window_text = f"{ls.NOTIFY_START_HOUR:02d}:00-{ls.NOTIFY_END_HOUR:02d}:00 Bangkok Mon-Fri"
    print(f"\n=== Liquidity Sweep Backtest: {symbol} | {ls.STRATEGY_TIMEFRAME} | last {days} days | {window_text} ===\n")
    print(f"Ticks evaluated inside the notification window: {ticks_evaluated}\n")

    print("Funnel (transitions reached):")
    for phase in (ls.PHASE_NEAR_ZONE, ls.PHASE_SWEPT, ls.PHASE_ENTERED):
        print(f"  {phase:<20} {funnel.get(phase, 0)}")

    print("\nInvalidated / timed out, by the phase they were in:")
    if invalidated_from:
        for phase, count in invalidated_from.most_common():
            print(f"  {phase:<20} {count}")
    else:
        print("  none")

    print(f"\nReversal candles detected: {len(reversal_events)}")
    if reversal_events:
        for event in reversal_events:
            when = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if event.led_to_entry:
                outcome_text = "-> entered"
            else:
                outcome_text = f"-> skipped ({event.skip_reason.splitlines()[-1].strip()})"
            print(f"  {when} UTC | {event.direction:<4} | {event.zone_name:<16} {outcome_text}")
    else:
        print("  none")

    print(f"\nTotal entries: {len(trades)}")
    if not trades:
        print("\nNo trades were triggered in this period.")
        return

    outcomes = Counter(trade.outcome for trade in trades)
    wins = outcomes.get("WIN", 0)
    losses = outcomes.get("LOSS", 0)
    resolved = wins + losses
    win_rate = (wins / resolved * 100) if resolved else 0.0
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
    avg_r = sum(r_values) / len(r_values) if r_values else 0.0

    print(f"  WIN: {wins}  LOSS: {losses}  OPEN: {outcomes.get('OPEN', 0)}  NO_STOP: {outcomes.get('NO_STOP', 0)}")
    print(f"  Win rate (resolved trades only): {win_rate:.1f}%")
    print(f"  Average R multiple (resolved trades only): {avg_r:.2f}")
    print(f"  Total R: {sum(r_values):.2f}")

    print("\nTrade log:")
    for trade in trades:
        entry_time = datetime.fromtimestamp(trade.entry_timestamp / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        r_text = f"{trade.r_multiple:.2f}R" if trade.r_multiple is not None else "N/A"
        print(
            f"  {entry_time} UTC | {trade.direction:<4} | entry {trade.entry_price:.2f} | "
            f"SL {trade.stop_loss} | TP {trade.take_profit} | {trade.outcome} ({r_text}, {trade.holding_candles} candles)"
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest the liquidity sweep strategy against Binance history")
    parser.add_argument("--symbol", default="PAXG/USDT", help="Trading pair, e.g. PAXG/USDT")
    parser.add_argument("--days", type=int, default=30, help="How many days of history to backtest (default: 30)")
    parser.add_argument(
        "--max-hold-candles",
        type=int,
        default=DEFAULT_MAX_HOLD_CANDLES,
        help="Max 5m candles to hold a trade before marking it OPEN (default: 1440 = 5 days)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run_backtest(args.symbol, args.days, args.max_hold_candles))
