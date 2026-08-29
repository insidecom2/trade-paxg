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


class MacroDataPoint(BaseModel):
    indicator: str  # e.g. "CPI (headline)", "Non-farm Payrolls"
    period: str     # e.g. "2026-07-01" — the data period, not a release date
    value: float


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
