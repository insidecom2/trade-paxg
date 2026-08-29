"""Typed request/response shapes for the AI Gold Trading Analyst pipeline.

The response model doubles as the OpenAI Structured Outputs JSON schema, so
the model's output is validated by construction rather than parsed as
free-form text.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# Literal (not plain str) so both pydantic validation and the Structured
# Outputs JSON schema sent to OpenAI constrain the model to these exact
# values — a bare `str` field lets the model return anything (observed in
# practice: it once returned "WAIT" as daily_bias, which is only a valid
# preferred_strategy value).
DailyBias = Literal["BULLISH", "BEARISH", "RANGE", "UNCERTAIN"]
PreferredStrategy = Literal["BUY_ON_DIP", "SELL_ON_RALLY", "BREAKOUT", "REVERSAL", "WAIT"]


class DailyOutlookResponse(BaseModel):
    daily_bias: DailyBias
    confidence: int = Field(ge=0, le=100)
    preferred_strategy: PreferredStrategy
    support_zones: List[float] = Field(default_factory=list)
    resistance_zones: List[float] = Field(default_factory=list)
    liquidity_targets: List[str] = Field(default_factory=list)
    bullish_scenario: str
    bearish_scenario: str
    invalidation: str
    avoid_chasing_notes: Optional[str] = None
    reasoning: str


TradeDecision = Literal["BUY_SETUP", "SELL_SETUP", "WAIT", "NO_TRADE"]
PreviousThesisStatus = Literal["CONFIRMED", "STILL_VALID", "WEAKENING", "INVALIDATED", "COMPLETED"]
MarketCondition = Literal[
    "LIQUIDITY_SWEEP", "BREAKOUT_RETEST", "TREND_CONTINUATION", "REVERSAL", "RANGE", "UNCLEAR"
]


class SessionAnalysisResponse(BaseModel):
    """Shared response shape for the four session-time analysis types
    (SESSION_PREPARATION, SETUP_DETECTION, SETUP_CONFIRMATION,
    FINAL_SESSION_DECISION). One schema instead of four keeps the overlap
    (decision/confidence/entry/SL/TP/reasoning) in one place; fields a
    given slot doesn't use are simply left None — see DAILY_OUTLOOK_
    INSTRUCTION-equivalent per-slot instructions in ai_prompts.py for what
    each slot is expected to populate.
    """
    decision: TradeDecision
    confidence: int = Field(ge=0, le=100)
    previous_thesis_status: Optional[PreviousThesisStatus] = None
    market_condition: Optional[MarketCondition] = None
    entry_from: Optional[float] = None
    entry_to: Optional[float] = None
    confirmation_description: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    invalidation: Optional[str] = None
    buy_scenario: Optional[str] = None
    sell_scenario: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    risk_factors: List[str] = Field(default_factory=list)
    avoid_notes: Optional[str] = None
    reasoning: str


class MacroDataPoint(BaseModel):
    indicator: str  # e.g. "CPI (headline)", "Non-farm Payrolls"
    period: str     # e.g. "2026-07-01" — the data period, not a release date
    value: float


class EconomicEvent(BaseModel):
    title: str
    scheduled_time: str  # ISO 8601, UTC
    forecast: str  # may be "" — the calendar source doesn't always have one
    previous: str  # may be ""


class MarketSnapshot(BaseModel):
    trend: Optional[str] = None
    ema_fast: Optional[float] = None
    ema_slow: Optional[float] = None
    atr: Optional[float] = None
    volume_ratio: Optional[float] = None
    bollinger_signal: Optional[str] = None
    support_levels: List[float] = Field(default_factory=list)
    resistance_levels: List[float] = Field(default_factory=list)


class GoldAIAnalysisRequest(BaseModel):
    analysis_type: str
    requested_at: str
    timezone: str
    symbol: str
    current_price: float

    h4: MarketSnapshot
    h1: MarketSnapshot

    previous_day_high: Optional[float] = None
    previous_day_low: Optional[float] = None
    asian_high: Optional[float] = None
    asian_low: Optional[float] = None

    supply_zones: List[str] = Field(default_factory=list)
    demand_zones: List[str] = Field(default_factory=list)

    # FRED (Federal Reserve) gives the most recently *released* actual/
    # previous values for a fixed set of US macro indicators — it has no
    # forecast/consensus figures and no forward-looking release calendar.
    # macro_data_note always states that scope explicitly, whether or not
    # released_macro_data is empty, so the model never assumes it also has
    # upcoming-event or forecast context it wasn't given.
    released_macro_data: List[MacroDataPoint] = Field(default_factory=list)
    macro_data_note: str = (
        "No economic news data is supplied for this analysis type."
    )

    # Today's scheduled USD high-impact ("red folder") events, from a
    # forward-looking calendar feed. This source has NO actual/reported
    # value field at all (verified against the live feed) — forecast/
    # previous and scheduled time only. news_calendar_note always states
    # that, whether or not any event was found for today.
    todays_usd_high_impact_events: List[EconomicEvent] = Field(default_factory=list)
    news_calendar_note: str = (
        "No forward-looking news calendar is supplied for this analysis type."
    )

    # Compact JSON summary of the prior stage(s) this run should build on
    # (e.g. SETUP_DETECTION includes DAILY_OUTLOOK + SESSION_PREPARATION).
    # Empty for DAILY_OUTLOOK, which has no prior stage same-day.
    previous_context: str = ""
    previous_context_note: str = (
        "No prior analysis from earlier today is supplied for this analysis type."
    )
