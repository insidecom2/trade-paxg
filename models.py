from pydantic import BaseModel, Field
from typing import List, Optional, Tuple

class Candle(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

class Zone(BaseModel):
    type: str  # 'SUPPLY' or 'DEMAND'
    top: float
    bottom: float
    strength: int = 1

class Signal(BaseModel):
    action: str                 # 'STRONG_BUY', 'SELL', 'STRONG_SELL', 'BUY', 'HOLD'
    position: str               # 'SUPPORT', 'RESISTANCE', 'NEUTRAL'
    pattern: Optional[str] = None  # 'HAMMER', 'SHOOTING_STAR', 'LONG_RED', 'LONG_GREEN', None
    price: float
    reason: str
