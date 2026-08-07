from typing import Literal

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel, Field

from exit_profit import run_standalone
from main import main


Timeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


class AnalysisRequest(BaseModel):
    symbol: str = Field(default="PAXG/USDT", min_length=1, max_length=32)
    timeframe: Timeframe = "15m"


class ExitProfitRequest(BaseModel):
    symbol: str = Field(default="PAXG/USDT", min_length=1, max_length=32)


app = FastAPI(title="Trade PAXG API")


@app.get("/health", status_code=200)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", status_code=202)
async def analyze(
    request: AnalysisRequest, background_tasks: BackgroundTasks
) -> dict[str, str]:
    background_tasks.add_task(main, request.symbol, request.timeframe)
    return {"status": "started"}


@app.post("/exit-profit", status_code=202)
async def exit_profit(
    request: ExitProfitRequest, background_tasks: BackgroundTasks
) -> dict[str, str]:
    background_tasks.add_task(run_standalone, request.symbol)
    return {"status": "started"}
