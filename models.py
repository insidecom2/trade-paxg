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
    status: str = "NEUTRAL"
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    volume_ratio: Optional[float] = None
    volume_status: Optional[str] = None  # 'THICK', 'NORMAL', 'THIN', 'UNKNOWN'
    bband_le: Optional[bool] = None
    bband_se: Optional[bool] = None
    bband_upper: Optional[float] = None
    bband_middle: Optional[float] = None
    bband_lower: Optional[float] = None
