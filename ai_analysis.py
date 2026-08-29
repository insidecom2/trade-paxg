"""AI Gold Trading Analyst — DAILY_OUTLOOK pipeline.

A fully isolated, feature-flagged pipeline mirroring liquidity_sweep.py's
shape: own cron entry, own lock file, own trading_state.json key namespace.
A failure anywhere in here (OpenAI down, malformed response, missing
config) must never affect the strategy, exit-profit, or liquidity-sweep
pipelines — it only logs and skips.

Flow:
    Market Data -> Technical Calculation -> OpenAI Analysis -> Telegram Alert

No automatic trade execution.
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv

from ai_client import AIAnalysisClient
from ai_models import (
    DailyOutlookResponse,
    GoldAIAnalysisRequest,
    MarketSnapshot,
    SessionAnalysisResponse,
)
from ai_prompts import (
    AI_SYSTEM_PROMPT,
    DAILY_OUTLOOK_INSTRUCTION,
    FINAL_SESSION_DECISION_INSTRUCTION,
    SESSION_PREPARATION_INSTRUCTION,
    SETUP_CONFIRMATION_INSTRUCTION,
    SETUP_DETECTION_INSTRUCTION,
)
from analyzer import MarketAnalyzer
from exchange_manager import create_market_data_manager
from fred_client import FredClient
from liquidity_sweep import ASIAN_SESSION_HOURS_UTC, BANGKOK_TZ, bangkok_now, session_high_low
from models import Candle
from news_calendar_client import NewsCalendarClient
from telegram_notifier import TelegramNotifier
from trading_state import TradingStateStore

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("AIGoldAnalyst")

load_dotenv()

H4_TIMEFRAME = "4h"
H1_TIMEFRAME = "1h"
DAILY_TIMEFRAME = "1d"

# is_uptrend/is_downtrend need 200+ candles for a valid EMA200; without
# enough history they silently report SIDEWAYS regardless of the real trend.
# Fetch enough candles to compute indicators correctly — only the resulting
# MarketSnapshot (not the raw candles) is ever sent to OpenAI, so this does
# not affect token usage.
H4_CANDLE_LIMIT = 250
H1_CANDLE_LIMIT = 250
DAILY_CANDLE_LIMIT = 3

STATE_KEY_SUFFIX = "ai_daily_outlook"
ANALYSIS_TYPE = "DAILY_OUTLOOK"

# The four session-time stages, in chronological order. Each stage's
# previous_context is built from every earlier stage's persisted AI
# response for *today only* — a stage that hasn't run yet today (disabled,
# cron didn't fire, still pending) is simply reported as missing in
# previous_context_note rather than blocking this run.
SESSION_STAGES = {
    "session_preparation": {
        "analysis_type": "SESSION_PREPARATION",
        "state_key_suffix": "ai_session_preparation",
        "instruction": SESSION_PREPARATION_INSTRUCTION,
        "requires": ["ai_daily_outlook"],
    },
    "setup_detection": {
        "analysis_type": "SETUP_DETECTION",
        "state_key_suffix": "ai_setup_detection",
        "instruction": SETUP_DETECTION_INSTRUCTION,
        "requires": ["ai_daily_outlook", "ai_session_preparation"],
    },
    "setup_confirmation": {
        "analysis_type": "SETUP_CONFIRMATION",
        "state_key_suffix": "ai_setup_confirmation",
        "instruction": SETUP_CONFIRMATION_INSTRUCTION,
        "requires": ["ai_daily_outlook", "ai_session_preparation", "ai_setup_detection"],
    },
    "final_session_decision": {
        "analysis_type": "FINAL_SESSION_DECISION",
        "state_key_suffix": "ai_final_session_decision",
        "instruction": FINAL_SESSION_DECISION_INSTRUCTION,
        "requires": [
            "ai_daily_outlook", "ai_session_preparation",
            "ai_setup_detection", "ai_setup_confirmation",
        ],
    },
}


def _analysis_enabled() -> bool:
    return os.getenv("AI_ANALYSIS_ENABLED", "false").strip().lower() == "true"


def _news_calendar_enabled() -> bool:
    # No API key needed for this source, so it defaults on; kept behind its
    # own flag anyway so it can be switched off without a redeploy if the
    # feed misbehaves (rate limiting, schema change, etc.).
    return os.getenv("AI_NEWS_CALENDAR_ENABLED", "true").strip().lower() == "true"


def _resolve_price_source() -> Optional[str]:
    """AI_PRICE_SOURCE lets this pipeline use a different market data source
    (e.g. twelvedata/XAU-USD) than the strategy/liquidity-sweep pipelines
    (PAXG/USDT via binance) without changing their shared PRICE_SOURCE."""
    return os.getenv("AI_PRICE_SOURCE") or None


def _resolve_symbol(cli_symbol: str) -> str:
    """AI_TRADING_SYMBOL overrides the CLI/TRADING_SYMBOL default so the
    symbol can match AI_PRICE_SOURCE (e.g. XAU/USD for twelvedata) without
    touching the strategy's TRADING_SYMBOL."""
    return os.getenv("AI_TRADING_SYMBOL") or cli_symbol


def _closed_candles(candles: List[Candle]) -> List[Candle]:
    return candles[:-1] if len(candles) > 1 else candles


def _build_market_snapshot(analyzer: MarketAnalyzer, candles: List[Candle]) -> MarketSnapshot:
    if not candles:
        return MarketSnapshot()

    price = candles[-1].close
    if analyzer.is_uptrend(candles):
        trend = "UP"
    elif analyzer.is_downtrend(candles):
        trend = "DOWN"
    else:
        trend = "SIDEWAYS"

    resistances, supports = analyzer.find_key_levels(candles, price, limit=3)
    bband = analyzer.bollinger_signal(candles)
    if bband.get("le"):
        bollinger_state = "UPPER_BAND_BREAKOUT"
    elif bband.get("se"):
        bollinger_state = "LOWER_BAND_BREAKOUT"
    elif bband.get("upper") is not None:
        bollinger_state = "INSIDE_BANDS"
    else:
        bollinger_state = None

    return MarketSnapshot(
        trend=trend,
        atr=analyzer.atr(candles),
        volume_ratio=analyzer.volume_ratio(candles),
        bollinger_signal=bollinger_state,
        support_levels=supports,
        resistance_levels=resistances,
    )


def _format_zones(zones) -> List[str]:
    return [f"{zone.type} {zone.bottom:.2f}-{zone.top:.2f}" for zone in zones]


async def _fetch_macro_data(fred_client: Optional[FredClient]) -> tuple[list, str]:
    if fred_client is None:
        return [], (
            "No economic data source is configured for this analysis "
            "(FRED_API_KEY not set)."
        )
    try:
        points = await fred_client.fetch_latest_released()
    except Exception as exc:  # macro data is best-effort, never fatal
        logger.warning("FRED lookup failed, continuing without macro data: %s", exc)
        return [], "Economic data lookup failed; treat macro context as unavailable."

    note = (
        "These are the most recently RELEASED actual values for a fixed set "
        "of US macro indicators (source: FRED). There is no forecast/"
        "consensus figure and no forward-looking release calendar in this "
        "data — do not imply an upcoming release date or a market-expected "
        "value beyond what is listed here."
    )
    return points, note


async def _fetch_news_calendar(
    news_calendar_client: Optional[NewsCalendarClient], reference_time: datetime
) -> tuple[list, str]:
    if news_calendar_client is None:
        return [], "No forward-looking news calendar is supplied for this analysis type."
    try:
        today_bangkok = bangkok_now(reference_time).date()
        events = await asyncio.to_thread(
            news_calendar_client.fetch_todays_usd_high_impact_events, today_bangkok
        )
    except Exception as exc:  # calendar lookup is best-effort, never fatal
        logger.warning("News calendar lookup failed, continuing without it: %s", exc)
        return [], "News calendar lookup failed; treat today's event list as unavailable."

    if not events:
        note = (
            "No scheduled USD high-impact ('red folder') event was found for "
            "today in the calendar source. This source has NO actual/reported "
            "value field at all — only forecast, previous, and scheduled time."
        )
    else:
        note = (
            "These are today's scheduled USD high-impact ('red folder') "
            "events with forecast/previous and scheduled time, from a "
            "forward-looking calendar. This source has NO actual/reported "
            "value — never claim to know the outcome of an event whose "
            "scheduled time is still ahead of requested_at."
        )
    return events, note


async def _gather_common_context(
    symbol: str,
    market_data,
    analyzer: MarketAnalyzer,
    reference_time: datetime,
    fred_client: Optional[FredClient],
    news_calendar_client: Optional[NewsCalendarClient],
) -> Optional[dict]:
    """Market/indicator/macro/news fields shared by every analysis type.
    Returns None if there isn't enough candle history to proceed.
    """
    candles_4h = _closed_candles(await market_data.fetch_ohlcv(symbol, H4_TIMEFRAME, limit=H4_CANDLE_LIMIT))
    candles_1h = _closed_candles(await market_data.fetch_ohlcv(symbol, H1_TIMEFRAME, limit=H1_CANDLE_LIMIT))
    candles_1d = _closed_candles(await market_data.fetch_ohlcv(symbol, DAILY_TIMEFRAME, limit=DAILY_CANDLE_LIMIT))

    if not candles_4h or not candles_1h:
        logger.warning("Missing 4h/1h candles; cannot build analysis context.")
        return None

    current_price = candles_1h[-1].close

    previous_day_high = candles_1d[-1].high if candles_1d else None
    previous_day_low = candles_1d[-1].low if candles_1d else None

    asian_range = session_high_low(candles_1h, ASIAN_SESSION_HOURS_UTC, reference_time)
    asian_high, asian_low = asian_range if asian_range else (None, None)

    # find_snd_zones returns every unfiltered zone across the whole candle
    # history (dozens on 250 candles) — find_recent_snd_zones limits that to
    # the zones nearest the current price, which is what's relevant here and
    # keeps the OpenAI payload small.
    supply_demand = analyzer.find_recent_snd_zones(candles_1h, current_price, limit=6)
    supply_zones = [z for z in supply_demand if z.type == "SUPPLY"]
    demand_zones = [z for z in supply_demand if z.type == "DEMAND"]

    macro_data, macro_note = await _fetch_macro_data(fred_client)
    news_events, news_note = await _fetch_news_calendar(news_calendar_client, reference_time)

    return dict(
        current_price=current_price,
        h4=_build_market_snapshot(analyzer, candles_4h),
        h1=_build_market_snapshot(analyzer, candles_1h),
        previous_day_high=previous_day_high,
        previous_day_low=previous_day_low,
        asian_high=asian_high,
        asian_low=asian_low,
        supply_zones=_format_zones(supply_zones),
        demand_zones=_format_zones(demand_zones),
        released_macro_data=macro_data,
        macro_data_note=macro_note,
        todays_usd_high_impact_events=news_events,
        news_calendar_note=news_note,
    )


async def build_daily_outlook_context(
    symbol: str,
    market_data,
    analyzer: MarketAnalyzer,
    reference_time: Optional[datetime] = None,
    fred_client: Optional[FredClient] = None,
    news_calendar_client: Optional[NewsCalendarClient] = None,
) -> Optional[GoldAIAnalysisRequest]:
    reference_time = reference_time or datetime.now(timezone.utc)
    common = await _gather_common_context(
        symbol, market_data, analyzer, reference_time, fred_client, news_calendar_client
    )
    if common is None:
        return None

    return GoldAIAnalysisRequest(
        analysis_type=ANALYSIS_TYPE,
        requested_at=reference_time.isoformat(),
        timezone="Asia/Bangkok",
        symbol=symbol,
        **common,
    )


async def build_session_context(
    stage_key: str,
    symbol: str,
    market_data,
    analyzer: MarketAnalyzer,
    reference_time: Optional[datetime] = None,
    fred_client: Optional[FredClient] = None,
    news_calendar_client: Optional[NewsCalendarClient] = None,
    previous_context: str = "",
    previous_context_note: str = "",
) -> Optional[GoldAIAnalysisRequest]:
    reference_time = reference_time or datetime.now(timezone.utc)
    common = await _gather_common_context(
        symbol, market_data, analyzer, reference_time, fred_client, news_calendar_client
    )
    if common is None:
        return None

    return GoldAIAnalysisRequest(
        analysis_type=SESSION_STAGES[stage_key]["analysis_type"],
        requested_at=reference_time.isoformat(),
        timezone="Asia/Bangkok",
        symbol=symbol,
        previous_context=previous_context,
        previous_context_note=previous_context_note or (
            "No prior analysis from earlier today is supplied for this analysis type."
        ),
        **common,
    )


def _load_previous_stage_context(
    state_store: TradingStateStore, symbol: str, local_date: str, required_suffixes: List[str]
) -> tuple:
    """Reads each required earlier stage's persisted AI response for
    *today only* and serializes it for the prompt. A stage that hasn't
    run yet today (disabled, not yet fired, or failed) is reported as
    missing in the note rather than blocking this run — the model is
    instructed not to treat a missing stage as evidence of anything.
    """
    context = {}
    missing = []
    for suffix in required_suffixes:
        state = state_store.get(f"{symbol}|{suffix}")
        if state.get("date") == local_date and state.get("status") == "sent":
            context[suffix] = state.get("ai_response", {})
        else:
            missing.append(suffix)

    if not context:
        note = "No prior analysis from earlier today is supplied for this analysis type."
    elif missing:
        note = (
            f"Prior analysis is included for: {sorted(context.keys())}. "
            f"Missing/not yet run today: {missing}. Treat a missing stage as "
            "simply unavailable, not as evidence of anything."
        )
    else:
        note = (
            "Includes every expected prior analysis from earlier today, taken "
            "from each stage's persisted AI response only."
        )
    return json.dumps(context, ensure_ascii=False), note


def _format_todays_events_block(todays_events: Optional[List]) -> List[str]:
    """Rendered directly from the calendar source, not the AI response, so
    today's schedule is always shown accurately regardless of whether the
    model chose to mention it in its narrative fields."""
    if not todays_events:
        return []
    lines = ["", "ข่าววันนี้ (USD กล่องแดง):"]
    for event in todays_events:
        local_time = (
            datetime.fromisoformat(event.scheduled_time).astimezone(BANGKOK_TZ).strftime("%H:%M")
        )
        detail = f"{local_time} {event.title}"
        if event.forecast:
            detail += f" (คาดการณ์ {event.forecast}, ครั้งก่อน {event.previous or '-'})"
        lines.append(f"- {detail}")
    return lines


def format_daily_outlook_message(
    symbol: str,
    response: DailyOutlookResponse,
    todays_events: Optional[List] = None,
) -> str:
    lines = [
        "GOLD AI ANALYSIS — ภาพรวมประจำวัน",
        "",
        f"สัญลักษณ์: {symbol}",
        f"ทิศทาง: {response.daily_bias}",
        f"ความมั่นใจ: {response.confidence}%",
        f"กลยุทธ์ที่แนะนำ: {response.preferred_strategy}",
    ]
    if response.support_zones:
        lines.append(f"แนวรับ: {', '.join(f'{v:.2f}' for v in response.support_zones)}")
    if response.resistance_zones:
        lines.append(f"แนวต้าน: {', '.join(f'{v:.2f}' for v in response.resistance_zones)}")
    if response.liquidity_targets:
        lines.append(f"เป้าหมาย Liquidity: {', '.join(response.liquidity_targets)}")

    lines += _format_todays_events_block(todays_events)

    lines += [
        "",
        f"สถานการณ์ขาขึ้น: {response.bullish_scenario}",
        f"สถานการณ์ขาลง: {response.bearish_scenario}",
        f"จุดยกเลิกมุมมอง: {response.invalidation}",
    ]
    if response.avoid_chasing_notes:
        lines.append(f"ข้อควรระวัง: {response.avoid_chasing_notes}")
    lines += ["", f"เหตุผล: {response.reasoning}"]
    return "\n".join(lines)


SESSION_STAGE_LABELS = {
    "SESSION_PREPARATION": "เตรียมเซสชัน 18:00",
    "SETUP_DETECTION": "หา Setup 19:00",
    "SETUP_CONFIRMATION": "ยืนยัน Setup 20:00",
    "FINAL_SESSION_DECISION": "สรุปเซสชัน 21:00",
}


def format_session_analysis_message(
    symbol: str,
    analysis_type: str,
    response: SessionAnalysisResponse,
    todays_events: Optional[List] = None,
) -> str:
    label = SESSION_STAGE_LABELS.get(analysis_type, analysis_type)
    lines = [
        f"GOLD AI ANALYSIS — {label}",
        "",
        f"สัญลักษณ์: {symbol}",
        f"การตัดสินใจ: {response.decision}",
        f"ความมั่นใจ: {response.confidence}%",
    ]
    if response.previous_thesis_status:
        lines.append(f"สถานะ thesis ก่อนหน้า: {response.previous_thesis_status}")
    if response.market_condition:
        lines.append(f"สภาวะตลาด: {response.market_condition}")
    if response.entry_from is not None and response.entry_to is not None:
        lines.append(f"โซนเข้า: {response.entry_from:.2f} - {response.entry_to:.2f}")
    if response.confirmation_description:
        lines.append(f"เงื่อนไขยืนยัน: {response.confirmation_description}")
    if response.stop_loss is not None:
        lines.append(f"Stop Loss: {response.stop_loss:.2f}")
    if response.take_profit_1 is not None:
        lines.append(f"TP1: {response.take_profit_1:.2f}")
    if response.take_profit_2 is not None:
        lines.append(f"TP2: {response.take_profit_2:.2f}")

    lines += _format_todays_events_block(todays_events)

    lines.append("")
    if response.buy_scenario:
        lines.append(f"สถานการณ์ Buy: {response.buy_scenario}")
    if response.sell_scenario:
        lines.append(f"สถานการณ์ Sell: {response.sell_scenario}")
    if response.invalidation:
        lines.append(f"จุดยกเลิกมุมมอง: {response.invalidation}")
    if response.reasons:
        lines.append("เหตุผลสนับสนุน: " + "; ".join(response.reasons))
    if response.risk_factors:
        lines.append("ปัจจัยเสี่ยง: " + "; ".join(response.risk_factors))
    if response.avoid_notes:
        lines.append(f"ข้อควรระวัง: {response.avoid_notes}")
    lines += ["", f"เหตุผล: {response.reasoning}"]
    return "\n".join(lines)


async def run_daily_outlook(symbol: str = "PAXG/USDT", now: Optional[datetime] = None) -> bool:
    symbol = _resolve_symbol(symbol)
    if not _analysis_enabled():
        logger.info("ai.analysis.skipped reason=disabled analysisType=%s symbol=%s", ANALYSIS_TYPE, symbol)
        return True

    ai_client = AIAnalysisClient.from_env()
    if ai_client is None:
        logger.info("ai.analysis.skipped reason=missing_api_key analysisType=%s symbol=%s", ANALYSIS_TYPE, symbol)
        return True

    reference_time = now or datetime.now(timezone.utc)
    local_date = bangkok_now(reference_time).strftime("%Y-%m-%d")
    state_store = TradingStateStore()
    state_key = f"{symbol}|{STATE_KEY_SUFFIX}"
    state = state_store.get(state_key)
    if state.get("date") == local_date and state.get("status") == "sent":
        logger.info(
            "ai.analysis.skipped reason=duplicate analysisType=%s symbol=%s date=%s",
            ANALYSIS_TYPE, symbol, local_date,
        )
        return True

    logger.info("ai.analysis.started analysisType=%s symbol=%s", ANALYSIS_TYPE, symbol)
    market_data = None
    try:
        market_data = create_market_data_manager(_resolve_price_source())
        analyzer = MarketAnalyzer()
        fred_client = FredClient.from_env()
        news_calendar_client = NewsCalendarClient() if _news_calendar_enabled() else None
        request = await build_daily_outlook_context(
            symbol, market_data, analyzer, reference_time, fred_client, news_calendar_client
        )
        if request is None:
            logger.warning("ai.analysis.skipped reason=missing_market_data analysisType=%s symbol=%s", ANALYSIS_TYPE, symbol)
            return True

        user_payload = (
            f"{DAILY_OUTLOOK_INSTRUCTION}\n\nMARKET_DATA (JSON):\n"
            f"{request.model_dump_json(indent=2)}"
        )
        response = await asyncio.to_thread(
            ai_client.analyze, AI_SYSTEM_PROMPT, user_payload, DailyOutlookResponse
        )

        if response is None:
            logger.warning("ai.analysis.failed analysisType=%s symbol=%s reason=invalid_or_no_response", ANALYSIS_TYPE, symbol)
            state_store.save(state_key, {
                "date": local_date,
                "analyzed_at": reference_time.isoformat(),
                "status": "failed",
                "error_message": "OpenAI response missing or failed schema validation",
            })
            return True

        message = format_daily_outlook_message(symbol, response, request.todays_usd_high_impact_events)
        notifier = TelegramNotifier.from_env()
        sent = False
        if notifier is not None:
            sent = await notifier.send_message(message)
        else:
            logger.warning("telegram.ai_alert.skipped reason=notifier_unavailable analysisType=%s", ANALYSIS_TYPE)
        if sent:
            logger.info("telegram.ai_alert.sent analysisType=%s symbol=%s", ANALYSIS_TYPE, symbol)

        state_store.save(state_key, {
            "date": local_date,
            "analyzed_at": reference_time.isoformat(),
            "status": "sent",
            "current_price": request.current_price,
            "daily_bias": response.daily_bias,
            "confidence": response.confidence,
            "preferred_strategy": response.preferred_strategy,
            "support_zones": response.support_zones,
            "resistance_zones": response.resistance_zones,
            "invalidation": response.invalidation,
            "input_snapshot": request.model_dump(),
            "ai_response": response.model_dump(),
        })
        logger.info("ai.analysis.completed analysisType=%s symbol=%s decision=%s confidence=%d", ANALYSIS_TYPE, symbol, response.daily_bias, response.confidence)
        return True
    except Exception as e:
        logger.critical("ai.analysis.failed analysisType=%s symbol=%s error=%s", ANALYSIS_TYPE, symbol, e, exc_info=True)
        return False
    finally:
        if market_data is not None:
            await market_data.close()


async def run_session_analysis(
    stage_key: str, symbol: str = "PAXG/USDT", now: Optional[datetime] = None
) -> bool:
    """Runs one of the four session-time stages (see SESSION_STAGES).
    Same shape as run_daily_outlook: feature-flagged, fails soft on any
    external dependency, dedups per Bangkok-local day, never raises out
    to the caller except for a caught-and-logged critical failure.
    """
    if stage_key not in SESSION_STAGES:
        raise ValueError(f"Unknown session stage: {stage_key}")
    stage = SESSION_STAGES[stage_key]
    analysis_type = stage["analysis_type"]
    state_key_suffix = stage["state_key_suffix"]

    symbol = _resolve_symbol(symbol)
    if not _analysis_enabled():
        logger.info("ai.analysis.skipped reason=disabled analysisType=%s symbol=%s", analysis_type, symbol)
        return True

    ai_client = AIAnalysisClient.from_env()
    if ai_client is None:
        logger.info("ai.analysis.skipped reason=missing_api_key analysisType=%s symbol=%s", analysis_type, symbol)
        return True

    reference_time = now or datetime.now(timezone.utc)
    local_date = bangkok_now(reference_time).strftime("%Y-%m-%d")
    state_store = TradingStateStore()
    state_key = f"{symbol}|{state_key_suffix}"
    state = state_store.get(state_key)
    if state.get("date") == local_date and state.get("status") == "sent":
        logger.info(
            "ai.analysis.skipped reason=duplicate analysisType=%s symbol=%s date=%s",
            analysis_type, symbol, local_date,
        )
        return True

    logger.info("ai.analysis.started analysisType=%s symbol=%s", analysis_type, symbol)
    market_data = None
    try:
        previous_context, previous_context_note = _load_previous_stage_context(
            state_store, symbol, local_date, stage["requires"]
        )

        market_data = create_market_data_manager(_resolve_price_source())
        analyzer = MarketAnalyzer()
        fred_client = FredClient.from_env()
        news_calendar_client = NewsCalendarClient() if _news_calendar_enabled() else None
        request = await build_session_context(
            stage_key, symbol, market_data, analyzer, reference_time,
            fred_client, news_calendar_client, previous_context, previous_context_note,
        )
        if request is None:
            logger.warning("ai.analysis.skipped reason=missing_market_data analysisType=%s symbol=%s", analysis_type, symbol)
            return True

        user_payload = (
            f"{stage['instruction']}\n\nMARKET_DATA (JSON):\n"
            f"{request.model_dump_json(indent=2)}"
        )
        response = await asyncio.to_thread(
            ai_client.analyze, AI_SYSTEM_PROMPT, user_payload, SessionAnalysisResponse
        )

        if response is None:
            logger.warning("ai.analysis.failed analysisType=%s symbol=%s reason=invalid_or_no_response", analysis_type, symbol)
            state_store.save(state_key, {
                "date": local_date,
                "analyzed_at": reference_time.isoformat(),
                "status": "failed",
                "error_message": "OpenAI response missing or failed schema validation",
            })
            return True

        message = format_session_analysis_message(
            symbol, analysis_type, response, request.todays_usd_high_impact_events
        )
        notifier = TelegramNotifier.from_env()
        sent = False
        if notifier is not None:
            sent = await notifier.send_message(message)
        else:
            logger.warning("telegram.ai_alert.skipped reason=notifier_unavailable analysisType=%s", analysis_type)
        if sent:
            logger.info("telegram.ai_alert.sent analysisType=%s symbol=%s", analysis_type, symbol)

        state_store.save(state_key, {
            "date": local_date,
            "analyzed_at": reference_time.isoformat(),
            "status": "sent",
            "current_price": request.current_price,
            "decision": response.decision,
            "confidence": response.confidence,
            "previous_thesis_status": response.previous_thesis_status,
            "input_snapshot": request.model_dump(),
            "ai_response": response.model_dump(),
        })
        logger.info(
            "ai.analysis.completed analysisType=%s symbol=%s decision=%s confidence=%d",
            analysis_type, symbol, response.decision, response.confidence,
        )
        return True
    except Exception as e:
        logger.critical("ai.analysis.failed analysisType=%s symbol=%s error=%s", analysis_type, symbol, e, exc_info=True)
        return False
    finally:
        if market_data is not None:
            await market_data.close()


def parse_args():
    parser = argparse.ArgumentParser(description="AI Gold Trading Analyst")
    parser.add_argument("--symbol", default="PAXG/USDT", help="Trading pair, e.g. PAXG/USDT")
    parser.add_argument(
        "--stage",
        default="daily_outlook",
        choices=["daily_outlook", *SESSION_STAGES.keys()],
        help="Which analysis stage to run (default: daily_outlook)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.stage == "daily_outlook":
        asyncio.run(run_daily_outlook(symbol=args.symbol))
    else:
        asyncio.run(run_session_analysis(args.stage, symbol=args.symbol))
